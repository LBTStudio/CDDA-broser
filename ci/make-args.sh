#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: make に渡す変数の【唯一の定義元】
#
# ==================================================================
# なぜ独立したファイルにするのか
# ==================================================================
# ビルドを複数ジョブに分割すると、make を呼ぶ箇所が一気に増える:
#
#   1. ci/shard-plan.sh   → make print-OBJS       （何を作るべきか列挙）
#   2. ci/build-shard.sh  → make obj/tiles/xxx.o  （シャードが .o を作る）
#      （これが N ジョブぶん、それぞれ別マシンで走る）
#   3. ci/link.sh         → make cataclysm-tiles.js（最後にリンク）
#
# これらに渡す変数が【1 つでも食い違うと】ビルドは壊れる。
# しかも壊れ方が非常に厄介で、次のような形で出る:
#
#   ・TILES の値が違う  → IMGUI_SOURCES の有無が変わり、
#                          OBJS の中身自体がずれる。
#                          リンクが要求する .o がどのシャードにも
#                          存在せず「No rule to make target」。
#   ・RELEASE の値が違う → OPTLEVEL と -DRELEASE が変わる。
#                          コンパイル済み .o と PCH の整合が崩れ、
#                          「__OPTIMIZE__ predefined macro was enabled
#                           in PCH file but is currently disabled」。
#   ・BACKTRACE の値が違う → -DBACKTRACE の有無が変わる。
#                          ヘッダの条件コンパイルがシャード間でずれ、
#                          ODR 違反（-Wodr）またはリンク時の未定義シンボル。
#   ・LOCALIZE の値が違う → -DLOCALIZE の有無が変わり、同様にずれる。
#
# 【最悪なのは発覚のタイミング】である。
# ずれはコンパイル段では検出されない。全シャードが緑になった後、
# 最後のリンクジョブで初めて落ちる。分割ビルドではコンパイル段に
# 数十分かかるので、「数十分待ってから失敗」を繰り返すことになる。
#
# YAML に同じ変数リストを 3 か所コピペする方式では、
# 片方だけ直して片方を忘れる事故が必ず起きる。
# そこで【配列 1 つに集約して全員が source する】。
# こうすれば食い違いは構造的に発生しえない。
#
# ==================================================================
# 各変数の意味と、その値でなければならない理由
# ==================================================================
# NATIVE=emscripten
#     ビルドターゲットを Emscripten にする。これが wasm 化の起点。
#     Makefile 内の emscripten 分岐（-sUSE_SDL=2 等、OPTLEVEL、
#     INITIAL_MEMORY/MAXIMUM_MEMORY/ASYNCIFY 各種）が有効になる。
#
# BACKTRACE=0
#     ネイティブのスタックトレース採取を無効化する。
#     wasm には execinfo.h / backtrace() が無いのでリンクできない。
#     0 にしないとビルドが通らない（選択の余地はない）。
#
# TILES=1
#     SDL2 タイル版。ブラウザには端末（curses）が無いので必須。
#     この値は IMGUI_SOURCES の有無を左右する = OBJS の件数が変わる。
#     シャードとリンクで食い違うと即座に破綻するので特に重要。
#
# TESTS=0 / RUNTESTS=0
#     Catch2 のテストバイナリを作らない・走らせない。
#     テスト側 TU は 100 個超あり、そのぶん丸ごと時間の無駄になる。
#     配布物にも含まれない。
#
# RELEASE=1
#     リリースビルド。-DRELEASE が付き、OPTLEVEL が最適化寄りになる。
#     デバッグビルドは wasm が肥大して 4GB RAM 機で読み込めない。
#
# LOCALIZE=1 / LANGUAGES=ja
#     gettext による翻訳を有効化し、日本語だけを生成する。
#     全言語を生成すると .mo が増えて配布サイズと時間が膨らむ。
#     日本語ゲームとして必要な最小構成。
#
# CCACHE=1
#     コンパイラキャッシュを有効化する。
#     【ここが従来の 4 時間ビルドとの最大の差】。
#     Makefile は CCACHE=1 のとき CXX の前に ccache を挟む。
#     GitHub Actions の actions/cache で ~/.ccache を持ち回れば、
#     パッチが変わっていない TU は【コンパイルを完全に飛ばせる】。
#
#     ただしキャッシュの有効性はパッチ内容に依存するので、
#     キャッシュキーは patches/ のハッシュを含めなければならない
#     （そうしないと古い .o を掴んで不整合になる）。
#     キー設計はワークフロー側の責務。
#
#     注意: CCACHE の値は OBJS の中身を変えない（CXX の前置きだけ）。
#     したがって print-OBJS の結果には影響しない。
#     それでも全員同じ値にしておくのは、
#     「PCH のハッシュ計算に使うコンパイラ文字列」を揃えるためである。
#
# LINTJSON=0
#     JSON の lint を走らせない。data/json の検証は
#     このリポジトリの改変対象外（C++ 側だけを触っている）なので、
#     毎回走らせても得るものがない。
#
# ==================================================================
# 使い方
# ==================================================================
#   . "$( dirname "$0" )/make-args.sh"
#   make -j4 "${CDDA_MAKE_ARGS[@]}" cataclysm-tiles.js
#
# ==================================================================
# 変更するときの手順
# ==================================================================
# ここを変えたら【必ず ccache のキャッシュキーも変える】こと。
# make 変数が変わればコンパイル結果も変わるが、ccache は
# コンパイラ引数をハッシュに含むので基本的には自動で別エントリに
# なる。ただし PCH 経由の間接的な差分は取りこぼしうるため、
# 明示的にキーを変えるのが安全側である。

# 配列で持つ理由: 文字列で持って $CDDA_MAKE_ARGS と展開すると
# 単語分割の挙動が shell の設定に依存する。配列 + "${arr[@]}" なら
# 各要素が確実に 1 引数として渡る。
CDDA_MAKE_ARGS=(
    NATIVE=emscripten
    BACKTRACE=0
    TILES=1
    TESTS=0
    RUNTESTS=0
    RELEASE=1
    LOCALIZE=1
    LANGUAGES=ja
    CCACHE=1
    LINTJSON=0
)

# 並列度。ubuntu-latest は 4 vCPU なので 4。
# これより大きくしてもコア数を超えた分はコンテキストスイッチの
# 損になるだけで、メモリ使用量（emcc 1 プロセスあたり数百 MB）が
# 増えて OOM のリスクが上がる。
CDDA_MAKE_JOBS="${CDDA_MAKE_JOBS:-4}"

# リンク生成物の名前。Makefile の TILES_TARGET_NAME に対応する。
# 複数箇所で使うのでここに置く。
CDDA_TARGET_JS="cataclysm-tiles.js"
CDDA_TARGET_WASM="cataclysm-tiles.wasm"

# オブジェクトの出力先。Makefile の ODIR = $(BUILD_PREFIX)obj、
# TILES=1 のとき ODIRTILES = obj/tiles となる。
# シャードのアーティファクトをここに戻す必要があるので定義しておく。
CDDA_OBJ_DIR="obj/tiles"

export CDDA_MAKE_JOBS CDDA_TARGET_JS CDDA_TARGET_WASM CDDA_OBJ_DIR
