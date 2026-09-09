# bench/ — 性能検証ベンチマーク一式

## 2026-09-09: JSPI候補の自動検証（利用者の実機フィードバック不要）

実Chromium 151 + Emscripten 4.0.15で、こちらの環境に閉じた検証を実施しました。
通常条件/CPU 4倍制限の10実行、同じ計算結果、一時停止後のC++例外/RAII、
JSPI+JS例外の非適合を自動で確認します。**ゲーム全体・4GB端末の再現ではありません。**
`asyncify_overhead.cpp` の旧版には一時停止可能な呼び出しがなく、計装が除去され得ました。
旧版から「AsyncifyのCPU負荷はない」と結論するのは不適切です。

### 再現用コマンド（検証担当側で実行）

専用のEmscripten **4.0.15** を使用します。公開用3.1.51の環境に上書きせず、
付属Binaryenも差し替えません。`playwright-core` と対応するChromiumを用意します。
実ソースの試験は未改変0.IをHEADに持ち、全10パッチを適用したツリーを指定します。

```sh
source /path/to/isolated-emsdk/emsdk_env.sh
export CDDA_EMXX=/path/to/isolated-emsdk/upstream/emscripten/em++
export CDDA_EMCC=/path/to/isolated-emsdk/upstream/emscripten/emcc
export CDDA_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright-core

# 4構成 + 不適合の負例、通常/CPU制限で計10実行。コンパイルも自動。
node bench/jspi_probe.js

# 実Makefileの既定維持・例外ABI・メモリ設定・再実行・不正値の検査
python3 bench/jspi_build_test.py /path/to/patched/cdda

# 実スケジューラのキー/日本語起床、順序、アイドル空回り抑止
CDDA_RUNTIME_BACKEND=asyncify node bench/scheduler_browser_test.js /path/to/patched/cdda
CDDA_RUNTIME_BACKEND=jspi node bench/scheduler_browser_test.js /path/to/patched/cdda

# 実main.cppのmount_idbfsを使う保存/復元/失敗復旧/分離
CDDA_RUNTIME_BACKEND=jspi node bench/idbfs_browser_test.js /path/to/patched/cdda

# HTML IMEブリッジ + 実Chromium CDP変換試験
node bench/ime_bridge_test.js
```

- `jspi_probe.js` は同一ソース・同一コンパイラ・O2・64MiB初期メモリで比較。
  各条件をウォームアップ後7回測り、中央値/最小/最大と全条件のチェックサム一致を記録。
  結果は `bench/out/jspi-probe/results.json`。基準ログは `docs/measurements/raw/2026-09-09-jspi-mechanism.json`。
- 比較は **Asyncify+JS例外 → JSPI+Wasm例外** であり、JSPI単独の寄与ではありません。
  同期対照2種も実行して例外処理方式の影響を切り分けます。同期版は待機しないため製品候補ではありません。
- JSPI+JS例外は `trying to suspend JS frames` が期待される負例です。
  4.0.15でAsyncify+Wasm例外を試したところコンパイラ側で失敗したため、公開候補にはしません。
- スケジューラ試験は実wasm/ブラウザを使いますが、既存SDL受信者はテスト用リスナーです。
  日本語イベントは型付きDOMイベント。表示される往復時間にはPlaywright通信を含み、ゲームの入力遅延ではありません。
- 保存試験は実IndexedDBですが最小wasmです。ゲームの全セーブ内容・タブ破棄・物理ディスク容量不足を保証しません。
- ブラウザは毎回専用コンテキストを使い、利用者のセーブは読み書きしません。外部HTTPサーバーも不要です。
- 実 `cata_web_yield.cpp` / `input_popup.cpp` / `main.cpp` / `sdltiles.cpp` は新コンパイラの
  `-fwasm-exceptions -fsyntax-only -Wall -Wextra -Werror` で合格。フルリンク/実ゲームの動作確認とは別です。

### JSPI版ゲームのビルド条件と未完了項目

未改変0.Iにパッチを適用し、`CDDA_RUNTIME_BACKEND=jspi bash ci/tune-makefile.sh` 相当の
設定で全オブジェクト/PCHを再生成します。**既定はAsyncifyのままです。**
実ソースツリー内から呼ぶ際はスクリプトを絶対パスで指定してください。
SDL依存の初回キャッシュロック問題は、先に
`embuilder build freetype harfbuzz sdl2_ttf sdl2_image` を行って解消しました。
JSPI版のフルビルドと実ゲームの移動・ロード・長時間RAM計測は未完了です。
約1GBの手元環境では巨大な `game.cpp` の検査がメモリ上限に近づき、安全のため停止しました。
GitHub連携にはActions実行・workflow編集の権限がないため、検証用workflow差分はPR本文の添付で別提供します。
公開版を無検証で切り替えず、利用者の検証待ちにもせず、未達をそのまま記録します。

## 2026-09-08: 証拠の扱いと現行の回帰テスト

**以下の古い説明には、合成ループを実ゲームの実測と誤認した記述が残っています。**
F-18/F-20の0.6170ms、72%、31%高速化、Chromebook相当の合格判定を
CDDAの実プレイ性能として使用しないでください。CPU負荷倍率は4GB端末の再現ではありません。
危険検知の16ターン間引きはゲーム体験を変えるため撤回済みです。

現在の回帰テスト（Python 3 / C++17 g++ / Node.js 22）:

```sh
python3 bench/runtime_efficiency_test.py /path/to/patched/cdda
node bench/asset_loader_test.js
node bench/ime_bridge_test.js
node bench/idbfs_sync_test.js /path/to/patched/cdda
python3 bench/input_utf8_test.py /path/to/patched/cdda
```

最初のテストは未改変0.IをHEADに持ち、全パッチ適用済みのソースツリーを指定します。
実ソースの待機表示コードを抽出して224条件で上流と比較し、
AIがyield以外で同じコードであることを確認します。
読書1800ターンの1800→31は**進捗文字列の生成回数**であって所要時間ではありません。
2番目は実シェルのローダーをNodeの実ストリーム/wasmと模擬ストレージで動かし、
失敗時の復旧と参照解放を検証します。ブラウザ実機のRAM測定ではありません。
3番目は実HTMLのIMEブリッジを抽出し、変換・確定・取消・貼り付け・入力終了後の
文字漏れ・外部DOMへの非干渉を検証します。
4番目はパッチ適用後の `main.cpp` にある実JavaScriptを抽出し、同期/復旧・描画待ち非依存・
保留変更・タブイベント・マウント失敗・実シェルの警告など12シナリオを検証します。
タイマーとFSは制御用アダプターで、実IndexedDBの試験ではありません。
5番目は実 `input_popup.cpp` のコールバックを抽出し、CDDA同梱の実ImGuiとリンクします。
UTF-8境界・バイト上限・カーソル・選択・履歴など895条件と、実InputTextへの
300バイトの日本語入力を検証します。旧コールバックのバッファ長不一致も負例で確認。
ソースツリーのHEADは未改変0.Iとし、同梱 `src/third-party/imgui` が必要です。
このテストはネイティブのImGui試験で、実ブラウザやゲーム全体の試験ではありません。
5つとも `ci/apply-patches.sh` に組み込まれています。追加のnpmパッケージは不要です。

### 任意の実ブラウザ統合テスト（PR #12）

こちらは自動ビルド前の軽量テストには含まれません。Emscripten 3.1.51、
`playwright-core`、対応するChromium実行ファイルが別途必要です。
既存のPlaywrightブラウザキャッシュを使います。`CDDA_PLAYWRIGHT_MODULE` は
インストール済みのモジュールへの絶対パスです。

```sh
CDDA_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright-core \
  node bench/ime_bridge_test.js

# 必要なら事前に emsdk_env.sh を読み込み、Emscriptenを有効にします。
CDDA_EMCC=/path/to/emsdk/upstream/emscripten/emcc \
CDDA_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright-core \
  node bench/idbfs_browser_test.js /path/to/patched/cdda
```

- IME: 実ChromiumのCDPで「にほん→日本」の確定、欄終了後の漏れ遮断、再入力を確認。
  OS固有の候補ウィンドウや、実ゲーム内の各フィルタは未検証です。
- IDBFS: 実ソースの `mount_idbfs` を最小wasmへ組み込み、実Emscripten IDBFS/IndexedDBを
  使用。1000回のdirty通知→1回の同期、日本語ファイルの再ロード後の一致、
  1回の同期失敗を注入しての復旧、同期中変更の直列保存、プレイヤー分離を確認します。
  rAFが呼ばれると失敗する条件でも保存が成功します。実ChromeOSのタブ破棄とは異なります。
- テスト成果物と一時ファイルは `bench/out/` に生成します。ブラウザはテスト専用の
  新規コンテキストを使用し、利用者のセーブにはアクセスしません。
- 仮想テストURLへの要求はPlaywrightで応答するためHTTPサーバーの起動は不要です。
  これらはゲーム全体のビルド・性能試験ではありません。

これらの合格は実ゲームの速度・4GB RAMでの安定性・セーブ完全性の保証ではありません。
それらには4GB実機と比較対象のネイティブ版による別の実プレイ計測が必要です。

---

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
| `asyncify_overhead.cpp` | 実際の一時停止を含むJSPI/Asyncify対照試験。旧版の「CPU負荷なし」という結論は撤回（上記2026-09-09節） | F-02は採用しない |
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
