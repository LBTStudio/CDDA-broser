#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: Makefile のビルド設定調整
#
# apply-patches.sh と同じ理由（全シャードでフラグを完全一致させる）で
# スクリプトに切り出してある。
#
# 使い方: cdda のチェックアウト先で
#     ../ci/tune-makefile.sh
set -euo pipefail

if [ ! -f Makefile ]; then
    echo "ERROR: CDDA のソースツリー内で実行してください" >&2
    exit 1
fi

# ------------------------------------------------------------------
# リンク最適化は上流既定の -Os を維持する
# ------------------------------------------------------------------
# 以前 -O0 リンクで Binaryen の長い段を飛ばしていたが、
# wasm のダウンロードサイズが約 2 倍になるうえ、決定的なのは
# Asyncify の計測コードが未最適化のまま残ることで、
# 4GB 機での実プレイが体感で明確に重くなった。
# 上流の CI も -Os で web ビルドを配っているので実証済みの構成。
grep -q 'LDFLAGS += -Os' Makefile

# ------------------------------------------------------------------
# コンパイル最適化は -Os -> -O3（速度優先）
# ------------------------------------------------------------------
# マップ移動・アクティビティは mapgen / イベント / AI のホットループを
# 通る。-O3 のベクトル化とインライン展開で低性能機の 1 ターンあたり
# CPU 時間が実測で下がる。
# サイズ増は上の -Os リンク段（Binaryen のサイズ最適化）が
# モジュール全体に効くので抑えられる。
grep -q 'OPTLEVEL = -Os' Makefile
# 範囲限定 sed: emscripten の分岐だけを書き換える
# （osx / mingw の分岐はそれぞれの値を保つ）。
sed -i \
  '/else ifeq ($(NATIVE), emscripten)/,/else/ s/OPTLEVEL = -Os/OPTLEVEL = -O3/' \
  Makefile

# ------------------------------------------------------------------
# メモリ設定（4GB 機向け）
# ------------------------------------------------------------------
# 上流は即座に 512MiB を確保し 4GiB ヒープを許すが、
# ブラウザ実測で日本語メニューは 256MiB + 成長許可で起動する。
sed -i 's/-sINITIAL_MEMORY=512MB/-sINITIAL_MEMORY=256MB/' Makefile

# ワールド生成が最大メモリの瞬間（core+mod JSON ロード、overmap 生成、
# mapgen）。ALLOW_MEMORY_GROWTH では最大値は予約上限にすぎず
# 実際に確保されるわけではないが、1GiB 上限では 4GB 機の
# ワールド生成中にちょうど成長に失敗して abort した。
# 2GiB なら平常時の使用量は同じまま生成のピークを吸収できる。
sed -i 's/-sMAXIMUM_MEMORY=4GB/-sMAXIMUM_MEMORY=2GB/' Makefile

# yield パッチは overmap / mapgen の深い呼び出し連鎖の中で
# ブラウザに制御を返す。Asyncify は生きている全フレームを
# 固定バッファに巻き戻すので、上流の 16KiB では溢れて
# 静かにメモリを壊す（真っ白な画面、メニューが出ない）。
# 16MiB は 1 回だけの確保（2GiB 上限の 0.8%）で、
# 観測された最深の連鎖よりはるかに上に上限を置ける。
sed -i 's/-sASYNCIFY_STACK_SIZE=16384/-sASYNCIFY_STACK_SIZE=16777216/' Makefile

# 深く入れ子になった overmap special は 256KiB のメインスタックも圧迫する。
# wasm のスタック枯渇は mapgen の再帰中に SIGILL 相当のトラップになる。
# 4MiB の 1 回確保で余裕をもって解消する。
sed -i 's/-sSTACK_SIZE=262144/-sSTACK_SIZE=4194304/' Makefile

# ------------------------------------------------------------------
# シャーディング用の print- ターゲットを追加
# ------------------------------------------------------------------
# ビルドを並列ジョブに分割するには、Makefile が考えるオブジェクト
# 一覧を【Makefile 自身に聞く】必要がある。
# ここで一覧を推測（ls src/*.cpp など）してはいけない:
# third-party（flatbuffers / zstd / imgui）や条件分岐で増減する
# ソースを取りこぼし、リンク時に未定義シンボルになる。
#
# 冪等にするため、既に追加済みなら何もしない。
if ! grep -q '^print-%:' Makefile; then
    printf '\nprint-%%:\n\t@echo $($*)\n' >> Makefile
fi

# ------------------------------------------------------------------
# 検証
# ------------------------------------------------------------------
grep -n -E 'INITIAL_MEMORY|MAXIMUM_MEMORY|ASYNCIFY_STACK_SIZE|-sSTACK_SIZE|LDFLAGS \+= -Os' Makefile
grep -q -- '-sINITIAL_MEMORY=256MB'          Makefile
grep -q -- '-sMAXIMUM_MEMORY=2GB'            Makefile
grep -q -- '-sASYNCIFY_STACK_SIZE=16777216'  Makefile
grep -q -- '-sSTACK_SIZE=4194304'            Makefile
grep -q -- 'LDFLAGS += -Os'                  Makefile
grep -q '^print-%:'                          Makefile
# emscripten のコンパイル分岐が -O3 になっていること。
awk '/else ifeq \(\$\(NATIVE\), emscripten\)/{found=1} found && /OPTLEVEL = -O3/{ok=1} END{exit ok?0:1}' Makefile

echo "[TUNE] OK: Makefile の設定を確認した"
