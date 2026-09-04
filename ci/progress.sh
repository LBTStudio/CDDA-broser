#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: ビルド進捗バーの共通ライブラリ
#
# ==================================================================
# なぜ専用のライブラリが必要なのか
# ==================================================================
# 従来のワークフローは 60 秒ごとに
#     [HEARTBEAT] WebAssembly build active: elapsed=1234s
# と出すだけだった。これは「ハングしていない」ことしか分からない。
# 4 時間のビルド中、あと何分で終わるのかは全く読めなかった。
#
# 進捗バーを出すには「終わった数 / 全体の数」が必要である。
# ここで素直に思いつく方法はどれも使えない:
#
#   ・make の出力行を数える
#       → -j4 で並列に走るので行が混ざる。ccache がヒットすると
#         そもそも行が出ないことがある。数として信頼できない。
#
#   ・make --debug や -n で事前に総数を得る
#       → 依存解析に時間がかかるうえ、増分ビルドでは
#         「もう存在する .o」を除いた数になり、
#         再実行のたびに分母が変わって比較できない。
#
#   ・GNU make の $(words) で自前カウンタを作る
#       → Makefile を改造することになる。CDDA の Makefile に
#         手を入れる箇所を増やすと、上流との差分が増えて
#         パッチの保守性が落ちる。
#
# 【採用した方法】
# 生成物である .o ファイルの【実在数を外から数える】。
#
#   分母 = シャードに割り当てられたオブジェクトの件数（既知・固定）
#   分子 = そのうち実際にファイルとして存在する件数
#
# この方法の利点:
#   ・make の並列度・出力形式・ccache のヒット率に一切依存しない
#   ・make 本体を改造しない
#   ・分母が固定なので、実行ごとの進捗を横に並べて比較できる
#   ・バックグラウンドで数えるだけなのでビルドを一切妨げない
#
# 唯一の注意点は「.o が書かれた瞬間 = そのTUが完了」であることに
# 依存する点。emcc は -o で直接書くため、書き込み途中のファイルが
# 一瞬カウントされうる。数秒間隔のサンプリングでは実害がなく、
# 最終的な完了判定は make の終了コードで行うので問題にならない。
#
# ==================================================================
# GitHub Actions のログでの見え方について
# ==================================================================
# Actions のログは【追記専用】である。端末のように \r で行を
# 上書きして「動くバー」にすることはできない（\r がそのまま
# 記録され、かえって読みにくくなる）。
#
# そこで一定間隔で【1 行ずつ新しいバーを出す】。
# こうすると縦に並んだバーが伸びていく形になり、
# 過去の進み具合との比較（＝速度の推移）まで読み取れる。
# 端末で直接使う場合は PROGRESS_INPLACE=1 で \r 更新にできる。

# ------------------------------------------------------------------
# progress_bar <完了数> <全体数> <幅>
#   [####################----------]  67% (295/440)
# の左側部分を組み立てて標準出力に返す。
# ------------------------------------------------------------------
progress_bar() {
    local done_n="$1" total_n="$2" width="${3:-30}"
    local filled pct

    if [ "$total_n" -le 0 ]; then
        # 0 除算回避。分母が取れない状況では空のバーを返す。
        printf '[%*s]' "$width" ''
        return 0
    fi

    pct=$(( done_n * 100 / total_n ))
    filled=$(( done_n * width / total_n ))
    # 完了数が全体数を超える異常時（想定外の .o が混ざった等）に
    # バーが崩れないよう上限で止める。
    [ "$filled" -gt "$width" ] && filled="$width"
    [ "$pct" -gt 100 ] && pct=100

    local bar=''
    local i=0
    while [ "$i" -lt "$filled" ]; do bar="${bar}#"; i=$(( i + 1 )); done
    while [ "$i" -lt "$width" ];  do bar="${bar}-"; i=$(( i + 1 )); done

    printf '[%s] %3d%%' "$bar" "$pct"
}

# ------------------------------------------------------------------
# format_hms <秒>
#   1234 → 20m34s
# 経過時間・残り時間の表示用。
# ------------------------------------------------------------------
format_hms() {
    local s="$1"
    [ "$s" -lt 0 ] && s=0
    local h=$(( s / 3600 ))
    local m=$(( ( s % 3600 ) / 60 ))
    local sec=$(( s % 60 ))
    if [ "$h" -gt 0 ]; then
        printf '%dh%02dm%02ds' "$h" "$m" "$sec"
    elif [ "$m" -gt 0 ]; then
        printf '%dm%02ds' "$m" "$sec"
    else
        printf '%ds' "$sec"
    fi
}

# ------------------------------------------------------------------
# count_existing_objects <一覧ファイル>
# 一覧に書かれたパスのうち、実在するものの数を返す。
#
# ------------------------------------------------------------------
# 【実装の経緯】最初は 1 プロセスで済ませようと
#     tr '\n' '\0' < list | xargs -0 -r ls -d 2>/dev/null | wc -l
# と書いたが、テストで壊れた。原因は 2 つ:
#
#   (1) 呼び出し側が set -o pipefail を有効にしているため、
#       存在しないファイルで ls が失敗 → xargs が 123 を返し、
#       パイプライン全体が失敗と判定される。
#       すると呼び出し側の `|| echo 0` が発動して、
#       すでに出力済みの数値の【後ろに 0 が連結】され
#       "10\n0" のような値になり、以降の算術式が
#       「integer expression expected」で全滅した。
#
#   (2) 一覧の件数が多いと xargs が ls を複数回に分けて起動しうる。
#       その場合でも wc -l なら合計は合うが、
#       ls の出力形式に依存する点は本質的に脆い。
#
# 【現在の実装】bash の [ -f ] は【組み込みコマンドで fork しない】。
# したがって 440 回回しても fork は 0 回であり、
# 外部プロセスを 1 個起動するより速い。
# 当初「440 回の fork になるから重い」と考えたのは誤りだった。
#
# 副作用として、パイプラインを使わないので pipefail の影響を
# 一切受けず、常に単一の整数だけを返すことが保証される。
# ------------------------------------------------------------------
count_existing_objects() {
    local list="$1"
    local n=0
    local path

    if [ ! -f "$list" ]; then
        echo 0
        return 0
    fi

    while IFS= read -r path; do
        [ -n "$path" ] || continue
        [ -f "$path" ] && n=$(( n + 1 ))
    done < "$list"

    echo "$n"
    return 0
}

# ------------------------------------------------------------------
# progress_monitor_start <一覧ファイル> <全体数> <ラベル> [間隔秒]
#
# バックグラウンドで進捗を出し続ける。PID を PROGRESS_MONITOR_PID に入れる。
# 必ず progress_monitor_stop で止めること（trap 推奨）。
#
# 出力例:
#   [PROGRESS] compile shard-2 [############------------------]  40% ( 29/ 73) 経過 3m12s 残り推定 4m48s
# ------------------------------------------------------------------
progress_monitor_start() {
    local list="$1" total="$2" label="$3" interval="${4:-10}"
    local started_at
    started_at="$( date +%s )"

    (
        # サブシェル内で回す。
        # 【重要】親の set -e / set -o pipefail を明示的に解除する。
        # 解除しないと、監視処理内の些細な失敗（date や free の
        # 一時的な失敗など）でモニタが黙って死に、
        # 進捗が出なくなったのかビルドが止まったのか
        # 区別できなくなる。監視はビルドを妨げてはならないので、
        # 何が起きても回り続けることを優先する。
        set +e
        set +o pipefail

        local last_done=-1
        while true; do
            local now elapsed done_n eta_str line
            now="$( date +%s )"
            elapsed=$(( now - started_at ))
            done_n="$( count_existing_objects "$list" )"
            # count_existing_objects は必ず単一の整数を返すが、
            # 万一空になっても算術式が壊れないよう保険をかける。
            [ -n "$done_n" ] || done_n=0

            # 残り時間の推定。
            # 【前提】1 個あたりの平均時間が今後も同じ、という単純な線形外挿。
            # ccache がヒットする前半は極端に速く進むため序盤の推定は
            # 短く出がちだが、進むにつれて実測平均に収束する。
            # 「あと何分か」の目安としてはこれで十分である。
            if [ "$done_n" -gt 0 ] && [ "$elapsed" -gt 0 ]; then
                local remain=$(( total - done_n ))
                [ "$remain" -lt 0 ] && remain=0
                eta_str="残り推定 $( format_hms $(( elapsed * remain / done_n )) )"
            else
                eta_str="残り推定 計測中"
            fi

            line="$( printf '[PROGRESS] %s %s (%3d/%3d) 経過 %s %s' \
                "$label" \
                "$( progress_bar "$done_n" "$total" 30 )" \
                "$done_n" "$total" \
                "$( format_hms "$elapsed" )" \
                "$eta_str" )"

            if [ "${PROGRESS_INPLACE:-0}" = "1" ]; then
                printf '\r%s' "$line"
            else
                # 進捗が変わっていないときも一定間隔で出す。
                # 【理由】GitHub Actions は【出力が 6 時間無い】と
                # ジョブを打ち切るが、それ以上に重要なのは
                # 「リンク段のように分子が動かない工程」でも
                # 生きていることを示し続ける必要がある点である。
                # 従来の HEARTBEAT が果たしていた役割をここで兼ねる。
                echo "$line"
                if [ "$done_n" = "$last_done" ] && [ "$done_n" -lt "$total" ]; then
                    # 止まって見えるときだけ、資源状況を添える。
                    # OOM でスワップに落ちている等の切り分けに使う。
                    free -m 2>/dev/null | sed -n '2p' | awk '{ printf "[PROGRESS]   メモリ 使用%sMiB / 全体%sMiB 空き%sMiB\n", $3, $2, $7 }' || true
                fi
            fi
            last_done="$done_n"

            sleep "$interval"
        done
    ) &
    PROGRESS_MONITOR_PID="$!"
}

# ------------------------------------------------------------------
# progress_monitor_stop
# ------------------------------------------------------------------
progress_monitor_stop() {
    if [ -n "${PROGRESS_MONITOR_PID:-}" ]; then
        kill "$PROGRESS_MONITOR_PID" 2>/dev/null || true
        wait "$PROGRESS_MONITOR_PID" 2>/dev/null || true
        PROGRESS_MONITOR_PID=''
        [ "${PROGRESS_INPLACE:-0}" = "1" ] && printf '\n'
    fi
    return 0
}

# ------------------------------------------------------------------
# step_summary <行...>
# GitHub Actions の実行結果ページ（Summary）に Markdown を追記する。
#
# ログは長大で読みにくいので、進捗の要約はここに出す。
# $GITHUB_STEP_SUMMARY が無い環境（ローカル）では標準出力に出す。
# ------------------------------------------------------------------
step_summary() {
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
        printf '%s\n' "$@" >> "$GITHUB_STEP_SUMMARY"
    else
        printf '%s\n' "$@"
    fi
}
