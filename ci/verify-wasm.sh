#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: 生成した wasm がブラウザで実行できるか検証する
#
# ==================================================================
# なぜこの検証が必要なのか（実際に配信事故を起こした）
# ==================================================================
# run 33985475507（#75）は【全ジョブ成功・緑】で配信まで通ったのに、
# ブラウザで開くと起動せず、次のエラーになった:
#
#   WebAssembly instantiation failed
#   CompileError: WebAssembly.instantiateStreaming():
#   size 7657177 > maximum function size 7654321 @+249007
#
# 生成物を解析すると原因はこうだった:
#
#   code section  : 65,053,447 バイト / 関数 59,341 個
#   最大の関数     : index 0 が 7,657,177 バイト  ← V8 上限を超過
#   2 番目の関数   :          439,552 バイト（17 倍も小さい）
#   V8 の 1 関数上限:        7,654,321 バイト
#   超過幅         :            2,856 バイト = 【0.037%】
#
# 【この事故の本質】
# (1) em++ / wasm-opt の終了コードは【0】である。CI は緑になる。
#     壊れるのはブラウザが wasm を instantiate する瞬間だけ。
# (2) 超過幅が 0.037% しかない。つまり以前の 116 + -Os の構成でも
#     【上限の直下ぎりぎり】で、たまたま動いていただけである。
#     CDDA のコードが少し増えれば、いつか必ず再発する時限爆弾だった。
#
# よって「今回の設定を直す」だけでは不十分で、
# 【上限に近づいたら CI で止める】仕組みが必要である。
# それがこのスクリプトである。
#
# ==================================================================
# V8 の 1 関数あたり最大サイズについて
# ==================================================================
# V8 の kV8MaxWasmFunctionSize = 7,654,321 バイト（約 7.3MiB）。
# これは仕様上の制限ではなく V8 の実装上の上限だが、
# Chrome / Edge / Chromebook はすべて V8 なので、
# 我々の配信対象では実質的な絶対上限である。
#
# 【なぜ 1 つの関数がそんなに大きくなるのか】
# Binaryen の「呼び出し元が 1 つだけの関数」のインライン上限
# --one-caller-inline-max-function-size (-ocimfs) は
# 【既定が -1 = 無制限】である。
# したがって「1 回だけ呼ばれる巨大関数」が呼び出し元に丸ごと
# 取り込まれ、それが連鎖すると 1 関数が数 MB まで膨らむ。
# ImGui の ShowDemoWindow のように
# 「巨大なヘルパーを 1 回ずつ呼ぶ」構造が典型である。
#
# ==================================================================
# 使い方
# ==================================================================
#   bash ci/verify-wasm.sh cataclysm-tiles.wasm
#
# 終了コード:
#   0 = 検証通過（ブラウザで instantiate できる見込み）
#   1 = 検証失敗（配信してはいけない）
set -uo pipefail

WASM="${1:-}"

if [ -z "$WASM" ]; then
    echo "使い方: verify-wasm.sh <wasm ファイル>" >&2
    exit 1
fi

if [ ! -f "$WASM" ]; then
    echo "ERROR: wasm が見つかりません: $WASM" >&2
    exit 1
fi

echo "=================================================================="
echo "[VERIFY] wasm の実行可能性を検証します: $WASM"
echo "=================================================================="

# ------------------------------------------------------------------
# 検証本体
# ------------------------------------------------------------------
# 【なぜ python で自前パースするのか】
# ・node で instantiate するのが最も確実だが、メモリ設定や
#   JS グルーの依存があり、リンクジョブで安定して動かない。
# ・wasm-opt --metrics は「合計」しか出さず、
#   【1 関数の最大値】を教えてくれない。今回必要なのはそれである。
# ・wasm のバイナリ形式のうち、我々が見たいのは
#   code section の「各関数本体の長さ」だけであり、
#   これは LEB128 を読むだけで取れる。依存を増やす必要がない。
python3 - "$WASM" <<'PYEOF'
import sys, struct

# V8 の実装上限（kV8MaxWasmFunctionSize）。
# ここを緩めることはできないので定数として扱う。
V8_MAX_FUNCTION_SIZE = 7654321

# 警告を出す閾値。95% を超えたら「次の変更で壊れる」と見なす。
# 今回の事故は 100.037% だった。余裕が無いことを事前に知りたい。
WARN_RATIO = 0.95

path = sys.argv[1]
data = open(path, 'rb').read()

if data[:4] != b'\x00asm':
    print(f"[VERIFY] ERROR: wasm のマジックが不正です: {data[:4]!r}")
    sys.exit(1)

version = struct.unpack('<I', data[4:8])[0]
print(f"[VERIFY] 形式: wasm version {version} / 全体 {len(data):,} バイト")


def read_uleb(buf, i):
    """LEB128（符号なし可変長整数）を 1 個読む。"""
    result = 0
    shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, i


# code section（id=10）を探す
code_off = code_len = None
i = 8
while i < len(data):
    section_id = data[i]
    i += 1
    section_len, i = read_uleb(data, i)
    if section_id == 10:
        code_off, code_len = i, section_len
    i += section_len

if code_off is None:
    print("[VERIFY] ERROR: code section が見つかりません（wasm が壊れています）")
    sys.exit(1)

# 各関数本体の長さを列挙する
count, p = read_uleb(data, code_off)
sizes = []
for idx in range(count):
    body_len, after_len = read_uleb(data, p)
    sizes.append((body_len, idx, p))
    p = after_len + body_len

total = sum(s[0] for s in sizes)
print(f"[VERIFY] code section: {code_len:,} バイト / 関数 {count:,} 個")
print(f"[VERIFY] 関数本体合計: {total:,} バイト（平均 {total // max(count, 1):,}）")

sizes.sort(reverse=True)

print("[VERIFY] 大きい関数 上位 5 個:")
for body_len, idx, off in sizes[:5]:
    ratio = 100.0 * body_len / V8_MAX_FUNCTION_SIZE
    print(f"[VERIFY]   func #{idx:<6d} {body_len:>12,} バイト "
          f"(V8 上限の {ratio:6.2f}%)")

biggest, biggest_idx, biggest_off = sizes[0]

print("")
print(f"[VERIFY] V8 の 1 関数上限: {V8_MAX_FUNCTION_SIZE:,} バイト")

over = [s for s in sizes if s[0] > V8_MAX_FUNCTION_SIZE]

if over:
    print("")
    print("[VERIFY] ============================================================")
    print("[VERIFY] 検証失敗: V8 の 1 関数上限を超える関数があります")
    print("[VERIFY] ============================================================")
    for body_len, idx, off in over:
        print(f"[VERIFY]   func #{idx} = {body_len:,} バイト "
              f"(超過 {body_len - V8_MAX_FUNCTION_SIZE:,} バイト / "
              f"ファイル位置 +{off})")
    print("[VERIFY]")
    print("[VERIFY] この成果物はブラウザで次のエラーになり【起動しません】:")
    print("[VERIFY]   CompileError: WebAssembly.instantiateStreaming():")
    print(f"[VERIFY]   size {biggest} > maximum function size "
          f"{V8_MAX_FUNCTION_SIZE} @+{biggest_off}")
    print("[VERIFY]")
    print("[VERIFY] 対処（ci/tune-makefile.sh / ci/link.sh を参照）:")
    print("[VERIFY]   1 回だけ呼ばれる関数のインライン展開を制限する。")
    print("[VERIFY]   Binaryen の -ocimfs（既定 -1 = 無制限）が原因なので、")
    print("[VERIFY]   有限値を与えると巨大関数の生成を防げる。")
    print("[VERIFY]   詳細: docs/measurements/FACTS.md F-27")
    sys.exit(1)

ratio = biggest / V8_MAX_FUNCTION_SIZE
if ratio > WARN_RATIO:
    print("")
    print(f"[VERIFY] WARNING: 最大の関数が上限の {100 * ratio:.2f}% に達しています。")
    print(f"[VERIFY]          func #{biggest_idx} = {biggest:,} バイト")
    print(f"[VERIFY]          残り余裕 {V8_MAX_FUNCTION_SIZE - biggest:,} バイトしかありません。")
    print("[VERIFY]          次にコードが増えたときに配信事故になります。")
    print("[VERIFY]          インライン展開の制限を強めることを検討してください。")
else:
    print(f"[VERIFY] 最大の関数は上限の {100 * ratio:.2f}%（余裕 "
          f"{V8_MAX_FUNCTION_SIZE - biggest:,} バイト）")

print("[VERIFY] OK: V8 の 1 関数上限に関する検証を通過しました")
PYEOF

rc=$?

if [ "$rc" -ne 0 ]; then
    echo ""
    echo "ERROR: wasm の検証に失敗しました。配信してはいけません。" >&2
    echo "       【ここで止めるのが目的です】" >&2
    echo "       この種の破損は em++ の終了コードが 0 のまま起きるため、" >&2
    echo "       CI が緑になり、ユーザーのブラウザで初めて発覚します。" >&2
    exit "$rc"
fi

echo "[VERIFY] 検証完了"
exit 0
