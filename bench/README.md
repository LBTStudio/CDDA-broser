# bench/ — 性能検証ベンチマーク一式

`docs/measurements/FACTS.md` に書かれている数値は、すべてここにある
プログラムを実際にブラウザで走らせて出したものです。
**数値を疑ったら、まずここを再実行してください。**

再実行して結果が変わったら `FACTS.md` を更新し、
生ログを `docs/measurements/raw/` に置いてください。

---

## なぜ CDDA 本体ではなく単体ベンチなのか

CDDA 本体のフルビルドは GitHub Actions で 4 時間、
ローカル（2 vCPU / 1GB RAM 級のサンドボックス）では
1 ファイルのコンパイルさえツールのタイムアウトを超えます。

一方、ここで測りたいのは

- ブラウザに制御を返す（yield）1 回あたりのコスト
- yield の方法によって描画フレームが何枚入るか
- IndexedDB への同期（`syncfs`）のコストが何に比例するか
- ターン処理ループの構造が最大描画停止時間にどう効くか

といった、**ブラウザとランタイムの性質**です。
これらは CDDA のゲームロジックとは独立しているので、
同じ構造を持つ最小プログラムで正確に測れます。

ベンチは「本物の呼び出し回数・本物のデータ量」を再現しています
（例: `craft_real.c` は実際の DEFAULTMODE の 146 個の
アクション名をそのまま持っています）。

---

## 前提環境

| 必要なもの | 入手方法 |
|---|---|
| Emscripten SDK 3.1.51 | 本番ビルドと同じバージョンを使う。バージョンを変えると数値が変わる |
| Chromium | Playwright 同梱のものを使った（`~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome`） |
| `playwright-core` (npm) | ベンチランナーが使う |

Emscripten の有効化:

```bash
source /path/to/emsdk/emsdk_env.sh
```

---

## 実行方法

### 一括実行

```bash
cd bench
export CHROME_PATH=~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome
./run-all.sh
```

結果は `bench/out/<名前>.log` に保存されます。

### 個別実行

```bash
# 1. ビルド
emcc yield_kinds.c -o out/yield_kinds.html -O3 -sASYNCIFY \
  -sINITIAL_MEMORY=64MB -sALLOW_MEMORY_GROWTH \
  -sASYNCIFY_STACK_SIZE=1048576 -sSTACK_SIZE=1048576

# 2. HTTP で配信（file:// では wasm が読めない）
python3 -m http.server 8099 --directory out &

# 3. ブラウザで走らせてログを取る
CHROME_PATH=... node run-bench-all.js http://localhost:8099/yield_kinds.html 120000
```

---

## ⚠ ビルドフラグの注意（重要）

`-sASYNCIFY_STACK_SIZE=1048576 -sSTACK_SIZE=1048576` を**必ず**付けてください。

本番 CI は `ASYNCIFY_STACK_SIZE=16777216` / `STACK_SIZE=4194304` を使いますが、
検証用サンドボックス（実測 985MiB）ではこの値だと
**OOM でブラウザが落ちます**。
ベンチはスタックを深く使わないので 1MB で十分です。

`-sASYNCIFY` は必須です。CDDA のウェブビルドは Asyncify 前提で、
yield コストの測定には計装後のコードで測る必要があります。

---

## ⚠ 計測ツールの注意（重要）

**`PlaywrightConsoleCapture` を使ってはいけません。**
wasm のベンチが終わる前にページを閉じてしまい、結果が取れません。

代わりに:

| ランナー | 挙動 | 使い分け |
|---|---|---|
| `run-bench.js` | `RESULT` / `DONE` / `ERR` で始まる行だけ出す | 出力が英語の定型行だけのベンチ |
| `run-bench-all.js` | **console 出力を全行そのまま出す** | 日本語の計測行を出すベンチ（`craft_real.c` など）。取りこぼしがない |

どちらも `DONE` を含む行が出たら即終了、出なければ第 2 引数のミリ秒でタイムアウトします。

`Failed to load resource: ... 404 (File not found)` は
Emscripten の HTML シェルが favicon を探しているだけなので**無害**です。

---

## ベンチ一覧

### yield プリミティブ系（F-01〜F-05, F-17）

| ファイル | 何を測るか | 対応する事実 |
|---|---|---|
| `yield_cost.c` | `emscripten_sleep(0)` の 1 回あたりコストと、**スタック深度を変えても変わらない**こと | F-01 |
| `yield_kinds.c` | `emscripten_sleep(0/1)` / `MessageChannel` / `scheduler.yield()` / `requestAnimationFrame` のコスト比較 | F-01 |
| `asyncify_overhead.cpp` | Asyncify 計装あり／なしで **CPU 速度が変わらない**こと（＝Asyncify は無罪） | F-02 |
| `paint_starve.c` | yield の種類ごとに、実際に描画フレームが何枚入るか。`scheduler.yield()` が rAF を飢餓させることの証拠 | F-04 |
| `per_turn_yield.c` | ターンごとに yield する構造でのスループットと最大描画停止 | F-05 |
| `verify_primitive.cpp` | **実装した `cata_web_yield` そのもの**の動作検証（6 項目）。再入ガード、`yield_if_due` の予算判定、`yield_paint` が確実に 1 フレーム入れること | F-17 |

`verify_primitive.cpp` は `src/cata_web_yield.{h,cpp}`（パッチで追加される
実物のソース）と一緒にビルドします:

```bash
cp /path/to/cdda/src/cata_web_yield.{h,cpp} .
em++ -std=c++17 verify_primitive.cpp cata_web_yield.cpp -o out/verify.html \
  -O2 -sASYNCIFY -sINITIAL_MEMORY=64MB -sALLOW_MEMORY_GROWTH \
  -sASYNCIFY_STACK_SIZE=1048576 -sSTACK_SIZE=1048576
```

### セーブ / IndexedDB 系（F-11〜F-14, F-16）

| ファイル | 何を測るか | 対応する事実 |
|---|---|---|
| `idbfs_cost.c` | `syncfs` 1 回のコスト | F-11 |
| `idbfs_scale.c` | **`syncfs` のコストが変更量ではなくマウント内の総ファイル数に比例する**こと。4000 ファイルなら変更ゼロでも 146.9ms | F-12 |
| `idbfs_mount.c` | マウント構成を変えた場合の比較 | F-12 |
| `save_real.c` | 実際のセーブ手順（`mapbuffer::save()` の 500ms 進捗間隔 + 250ms デバウンス）の再現。デバウンスは正しく効いており途中同期はゼロ | F-13, F-14, F-16 |

### ロード系（F-15）

| ファイル | 何を測るか | 対応する事実 |
|---|---|---|
| `load_yield.c` | JSON 60142 オブジェクトのロード中の yield 挙動 | F-15 |
| `load_paint.c` | ロード中に描画フレームが何枚入るか。現行 4 枚 / 最大停止 259.4ms → 改善後 64 枚 / 32.6ms | F-15 |

### 製作（クラフト）系（F-18）

| ファイル | 何を測るか | 対応する事実 |
|---|---|---|
| `craft_real.c` | 製作ターンループの忠実な再現。実際の 146 個の DEFAULTMODE アクション名、`std::find` 線形走査、`std::string` コピーを含む。7 シナリオ | F-18 |

`craft_real.c` の最重要の出力は **シナリオ [3]** です。
「yield を MessageChannel に変えるだけでは最大描画停止が
101.3ms から 1 ミリ秒も改善しない」という**否定的結果**を示します。
これが「粉砕の修正とは別に製作の修正が必要」という結論の根拠です。

### アクティビティ全経路（読書・製作・分解・粉砕）系（F-20）

| ファイル | 何を測るか | 対応する事実 |
|---|---|---|
| `activity_paths.c` | 1 ターンの全パイプラインを実寸法（132×132 タイル配列を実際に走査、dirty 3 層）で再現し、部品ごとのコストと 4 シナリオ（現行 0.I / 本 PR / 案A / 案B）を高速機・低速機の両方で比較。さらに案B の間隔 N を掃引 | F-20 |

このベンチだけは**明示的な合否基準を内蔵**しています。
出力の `[OK]` / `[NG]` は下記のしきい値に対する判定です:

| # | 基準 | しきい値 |
|---|---|---|
| A | 最大描画停止 | ≤ 50ms |
| B | 入力応答遅延 | ≤ 100ms |
| C | 進捗表示の更新 | ≤ 500ms |
| D | 実描画フレーム率 | ≥ 20fps |

出典は Nielsen, *Usability Engineering* (1993) の応答時間しきい値。
**「速くなったか」ではなく「プレイに差し支えないか」で判定する**のが
このベンチの主眼です。

最重要の出力は 3 つ:

1. **`[1g] mon_info_update() = 0.6170`** — 1 ターン合計 0.8600ms の **72%**。
   一方 `[1a]` の読書本体は 0.0040ms。
   「読書が遅いのは読書処理のせいではない」ことの直接の証拠。
2. **`[3a]` 行の 3 連 NG** — 現行 0.I は低速機で
   fps 16.9 / 停止 106.1ms / 入力 102.4ms と基準 A/B/D すべてを外す。
3. **`[4-xx]` の掃引** — 総時間が N=16 で飽和する。
   N を増やしても速くならず safemode の遅延だけ伸びるので、
   N=16 が「最大の効果が得られる最小の N」。

⚠ このベンチは全シナリオで **10 分近くかかります**（掃引が 7 点 × 3 回）。
`run-all.sh` のタイムアウトも 900000ms に設定してあります。
ツールのタイムアウトを避けるため、必ず
`nohup node run-bench-all.js ... > /tmp/out.log 2>&1 &` の形で
バックグラウンド実行してから結果を読んでください。

⚠ 掃引のような多点計測は、ブラウザの rAF 停止（タブスロットリング / GC）で
単発だと外れ値が混じります（実際に N=4 で max_gap 5145ms を観測）。
このベンチは **3 回まわして中央値**を採るようにしてあります。

### 日本語 IME 系（F-19）

| ファイル | 何を測るか | 対応する事実 |
|---|---|---|
| `text_input_scope_test.cpp` | `cata_web::text_input_scope` / `set_text_input_flag()` の参照カウント意味論（入れ子・冪等性・キー独立性・アンダーフロー耐性） | F-19 |

これだけは**ブラウザではなくネイティブで**走ります（純粋な C++ ロジックなので）:

```bash
cp /path/to/cdda/src/cata_web_text_input.{h,cpp} .
g++ -std=c++17 -DEMSCRIPTEN -Wall -Wextra -Wpedantic \
  text_input_scope_test.cpp cata_web_text_input.cpp -o out/text_input_test
./out/text_input_test
```

---

## 新しいベンチを足すときの約束

1. 最後に必ず `DONE` を含む行を `printf` する（ランナーの終了条件）。
2. 結果は 1 行ずつ `printf` する。改行しないと Emscripten の
   stdout バッファに溜まって出てこない。
3. 「現行」と「改善案」の**両方**を同じプログラム内で測る。
   別プログラムだと環境差が混ざる。
4. **否定的結果も必ず残す**。`craft_real.c` の `[3]` のように
   「効かなかった」ことの記録が、後から同じ道を辿るのを防ぐ。
5. 測ったら `docs/measurements/FACTS.md` に F-番号で追記し、
   生ログを `docs/measurements/raw/YYYY-MM-DD-名前.log` に置く。
