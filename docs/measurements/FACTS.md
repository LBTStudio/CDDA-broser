# 確定した技術的事実（FACTS）

このファイルは **実測または一次資料で確定した事実だけ** を書きます。
推測は書きません。新しい最適化を検討するときは、まずここを読んで
「すでに調べたこと」を再調査しないでください。

各項目には **確定日**、**根拠**（生ログまたはソース位置）、
**結論として何をしたか / しないか** を必ず付けます。

---

## F-01. `emscripten_sleep()` の遅さは Asyncify ではなく setTimeout の 4ms クランプ

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-yield-primitives.log`, `raw/2026-09-03-asyncify-overhead.log`

実測値（Chromium, emcc 3.1.51, `-O3 -sASYNCIFY`）:

| yield の方法 | 1回あたり | 備考 |
|---|---|---|
| `emscripten_sleep(0)` | **4.25 ms** | 現行 CDDA/本家が使う唯一の方法 |
| `emscripten_sleep(1)` | 5.20 ms | `SDL_Delay(1)` の実体（後述 F-03） |
| `MessageChannel.postMessage` | **0.075 ms** | **57倍速い** |
| `scheduler.yield()` | 0.035 ms | 121倍速いが F-04 の問題あり |
| `requestAnimationFrame` | 15.9 ms | ディスプレイ同期。描画には必要 |

**Asyncify のスタック巻き戻しは原因ではない**ことが 2 つの方法で確定した:

1. **スタック深度を変えても 4.25ms から動かない**
   （depth=0 → 4.2530ms、depth=1024 → 4.3030ms）。
   巻き戻しコストなら深度に比例するはずで、比例しない。
2. **Asyncify 計装ありの wasm と無しの wasm で CPU 速度が変わらない**
   （0.1030 ms/turn vs 0.1250 ms/turn。むしろ計装ありが速く出ており
   計測ノイズの範囲＝有意差なし）。

つまり `emscripten_sleep(0)` の 4.25ms は
**`setTimeout(fn, 0)` がブラウザ仕様で最短 4ms にクランプされる**
ことによる純粋な待ち時間である。CPU は 4ms 間アイドルしている。

**結論**: yield プリミティブを `MessageChannel` に差し替える。
Asyncify を外す・`ASYNCIFY_IGNORE_INDIRECT` を使うなどの
危険な最適化は **不要**（効果がないと実測済み）。

---

## F-02. 現行の「yield 間引き」は誤った対処だった（両方悪くなる）

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-paint-starvation.log`

F-01 を知らない状態では「yield が高いなら回数を減らす」しかなく、
実際に過去の PR で 50ms → 250ms 間隔へ間引いた。その結果を実測すると:

| 設定 | スループット | 描画の最大空白 |
|---|---|---|
| `sleep0` / 250ms 間引き（現行） | 329.5 turns/s | **258.8 ms**（＝カクつく） |
| `sleep0` / 16ms 間引き | 261.8 turns/s（**-21%**） | 249.1 ms |
| `MessageChannel` / 16ms 間引き | 327.4 turns/s | **27.0 ms**（滑らか） |
| `MessageChannel` / 4ms 間引き | 325.2 turns/s | 23.7 ms |

`sleep0` では **「滑らかさ」と「速さ」がトレードオフ**になり、
どちらを選んでも一方が犠牲になる。しかも 16ms 間引きにしても
最大空白は 249ms のまま改善しない（4ms クランプで戻りが遅いため）。

`MessageChannel` にすると **トレードオフが消滅する**:
スループットはほぼ満額（-0.6%）のまま、描画の空白が 1/10 になる。

**結論**: 間引き幅のチューニングは筋が悪い。プリミティブを替えて
間引きを **細かく（16ms）戻す** のが正解。

---

## F-03. `SDL_Delay(1)` も同じ 4ms クランプ経路を通る

- **確定日**: 2026-09-03
- **根拠**: emsdk 3.1.51 同梱 SDL2 ポートのソース
  `cache/ports/sdl2/SDL-release-2.24.2/src/timer/unix/SDL_systimer.c:187-202`

```c
void SDL_Delay(Uint32 ms) {
#ifdef __EMSCRIPTEN__
    if (emscripten_has_asyncify() && SDL_GetHintBoolean(SDL_HINT_EMSCRIPTEN_ASYNCIFY, SDL_TRUE)) {
        emscripten_sleep(ms);   /* ← ここ */
        return;
    }
#endif
```

CDDA の入力待ちループ `input_manager::get_input_event`
(`src/sdltiles.cpp:3960-3980`) は `SDL_Delay(1)` を回している。
これは 1ms ではなく **実測 5.20ms** かかる。

**影響**: キー入力待ちの解像度が 5ms 単位になる（＝入力遅延）。
1 ターンごとに 1 回通るため、長時間の連続行動でも効く。

**結論**: 入力待ちループにも高速 yield を使う。

---

## F-04. `scheduler.yield()` は速いが描画を飢餓させるので採用しない

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-paint-starvation.log`

`scheduler.yield()` は 0.035ms で最速だが、rAF の最大空白が
**103〜115ms** に悪化した（MessageChannel は 23〜27ms）。

理由: `scheduler.yield()` は「同じ優先度のタスクの先頭に割り込んで戻る」
継続方式のため、`requestAnimationFrame`（＝描画）より優先されてしまい、
ループが回り続ける間フレームが落ちる。

なお本サンドボックス Chromium では `scheduler.yield` は
**利用可能**（`scheduler_yield_available=1`）だった。
つまり「使えないから使わない」のではなく **意図して使わない**。

**結論**: `MessageChannel` を採用。`scheduler.yield` は使わない。

---

## F-05. 描画は rAF で行う必要がある（MessageChannel だけでは足りない）

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-paint-starvation.log` の `raf_frames` 列

`MessageChannel` yield 中も rAF は 109〜122 frames/2s（≒55〜61fps）
発火し続けた。つまり **ブラウザの描画は止まらない**。

ただし rAF 自体は 15.9ms かかる（F-01）ので、
**画面を実際に更新したい瞬間だけ** rAF を使い、
それ以外の「単に制御を返したい」箇所では MessageChannel を使う、
という **2 段構成**が最適。

**結論**:
- 「制御を返す」＝ MessageChannel（0.075ms）
- 「フレームを描く」＝ rAF（16ms、最大 60fps に自然にペーシングされる）

---

## F-06. ターン処理が重く見える構造上の理由（コード位置）

- **確定日**: 2026-09-03
- **根拠**: CDDA 0.I ソース読解

1 回のプレイヤー行動で yield 点を通る経路:

| 場所 | ソース | 現状 |
|---|---|---|
| 描画後 | `src/ui_manager.cpp:469` | `emscripten_sleep(1)` 無条件（本家）→ 当リポジトリで 33ms 間引き |
| イベント汲み上げ | `src/sdltiles.cpp` `input_manager::pump_events` | 当リポジトリで 250ms 間引きの `sleep(0)` |
| 入力待ち | `src/sdltiles.cpp:3960` | `SDL_Delay(1)` = 5.2ms（F-03） |
| mapgen | `src/mapgen.cpp` `apply_mapgen_in_phases` | 50ms 間引きの `sleep(0)` |
| overmap 生成 | `src/overmap.cpp` `generate` / `place_specials` | `sleep(0)` |

**粉砕（ACT_PULP）が特に遅くなる理由**:
`pulp_activity_actor::do_turn` (`src/activity_actor.cpp:8748`) は
**「1 ターンに死体 1 個だけ殴る」** 実装
（コメント: `return; // pulp at most one corpse per turn`）。
死体 1 体を潰すのに数百ターン必要で、その各ターンが
`do_turn.cpp:543` の `while( u.get_moves() > 0 && u.activity )` を通り、
毎ターン `ui_manager::redraw` → `pump_events` を経由する。
＝ **4ms クランプが数百回積算される**。これが体感の正体。

`raw/2026-09-03-paint-starvation.log` の
`per_turn_yield` シナリオ（400 ターン、毎ターン yield）が
まさにこれで、**2202ms → 411ms（5.4倍改善）** が見込める。

**結論**: プリミティブ差し替えだけで粉砕・製作・待機・自動移動の
すべてが同時に改善する。個別の活動に手を入れる必要はない。

---

## F-07. ビルド構成の実測値（CI 高速化の前提）

- **確定日**: 2026-09-03
- **根拠**: `2026-09-03-build-pipeline.md`

- 翻訳単位数: `src/*.cpp` **401** + third-party 28 + imgui 9 = **438**
- コンパイルは **完全に並列化可能**（1 ファイル 1 オブジェクト、
  依存は PCH のみ）
- リンク（Binaryen/wasm-opt）は **単一プロセス・並列化不可**
- GitHub Actions `ubuntu-latest` は **4 vCPU**。現行 workflow は
  `make -j4` = ちょうど使い切っているが、**ジョブは 1 個しか使っていない**

**結論**: GitHub Actions は「1 ジョブ 4 vCPU」を複数ジョブに増やせる。
コンパイルを **N シャードに分割して並列ジョブ**で走らせ、
オブジェクトを artifact 経由で集約してリンクだけ 1 ジョブで行えば、
コンパイル段を N 倍速にできる。加えて `ccache` を
`actions/cache` に載せれば 2 回目以降はほぼゼロになる。

---

## F-08. `-fwasm-exceptions` は ASYNCIFY と併用不可（採用しない）

- **確定日**: 2026-09-03
- **根拠**: emsdk 3.1.51 `upstream/emscripten/emcc.py:878`

```python
diagnostics.warning('emcc', 'ASYNCIFY=1 is not compatible with -fwasm-exceptions. '
                    'Parts of the program that mix ASYNCIFY and exceptions will not compile.')
```

CDDA は `-fexceptions`（JS ベース例外）でビルドされている。
これを高速な `-fwasm-exceptions` に替えると例外処理が速くなるが、
**ASYNCIFY と混ぜられない**ため当プロジェクトでは使えない。

**結論**: `-fexceptions` を維持する。この方向は検討終了。

---

## F-09. `ASYNCIFY_IGNORE_INDIRECT` は使えない

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-asyncify-overhead.log` + CDDA のコード構造

計測上は最速（0.089 ms/turn）で wasm も小さい（24KB vs 33KB）が、
**間接呼び出し越しの yield が壊れる**。CDDA では
`player_activity::do_turn` → `activity_actor::do_turn`（仮想関数）
のように、yield を含む経路が仮想関数を必ず跨ぐため使用不可。

しかも F-01 より計装コスト自体が誤差なので、**得られる利益もない**。

**結論**: 使わない。この方向は検討終了。

---

## F-10. 4ms クランプは「実機 Chromebook でも同じ」仕様値

- **確定日**: 2026-09-03
- **根拠**: HTML Standard の timer initialization steps
  （ネストレベル 5 以上のタイマーは最低 4ms にクランプすると規定）

これは CPU 性能ではなく **仕様**なので、
サンドボックスの計測値がそのまま RAM 4GB Chromebook にも当てはまる。
むしろ実機は 1 ターンの CPU 時間が長いため、
**クランプ待ちの絶対量は同じ**まま CPU 時間が増える。

つまり本改善は「速い機械でだけ効く最適化」ではなく、
**遅い機械ほど固定オーバーヘッドの比率が下がって効く**。

**結論**: RAM 4GB 前提の要求に対して正しい方向の改善である。

---

## F-11. セーブが重い主因は IDBFS の `syncfs`、ファイル書き込みではない

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-save-load-idbfs.log` [1][4]

| 項目 | 実測 |
|---|---|
| MEMFS へ 4KB 書き込み | **0.109 ms/ファイル** |
| 1200 quad のセーブ本体（JSON直列化負荷込み） | 500 ms |
| セーブ完了後の `syncfs(false)` 1 回 | **1544 ms** |

`map::save()`（`src/map.cpp:7818`）は `saven()` を呼ぶだけで、
実体は `MAPBUFFER.add_submap()` によるメモリ登録である
（`src/map.cpp:8084-8107`）。ディスク書き出しは `mapbuffer::save()`
（`src/mapbuffer.cpp:183`）だが、その書き込み先も Emscripten では MEMFS
であり安価。**体感時間の主成分は MEMFS → IndexedDB の同期**である。

**結論**: セーブ改善は書き込み処理ではなく `syncfs` のコストを狙う。

---

## F-12. `syncfs` のコストは変更量ではなく「マウント全体のファイル件数」に比例

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-save-load-idbfs.log` [2]
  + 一次資料 `emsdk/upstream/emscripten/src/library_idbfs.js`

実測（変更ゼロの空同期）:

| セーブツリーのファイル数 | 空同期コスト |
|---|---|
| 100 | 5.80 ms |
| 500 | 20.70 ms |
| 1000 | 36.05 ms |
| 2000 | 70.30 ms |
| 4000 | **146.90 ms** |

**4000 ファイル中 1 ファイルだけ変更しても 125.50 ms かかる。**

一次資料で構造を確定した（`library_idbfs.js`）:

- `IDBFS.syncfs` は毎回 `getLocalSet` と `getRemoteSet` の両方を呼ぶ
  （`library_idbfs.js:28-38`）。
- `getLocalSet`（同 :90-118）は `FS.readdir` / `FS.stat` で
  **マウント配下を全再帰走査**し、全エントリの mtime を集める。
- `getRemoteSet`（同 :121-150）は IndexedDB の timestamp インデックスに対し
  `openKeyCursor()` で **全キーをカーソル走査**する。
- `reconcile`（同 :237-）が両者を突き合わせて差分を出す。
  差分がゼロでも、上記 2 つの全走査は必ず実行される。

**結論**: セーブツリーのファイル件数を減らすことが唯一の根本策。
デバウンス頻度を下げても 1 回あたりのコストは下がらない。

---

## F-13. 同じ総バイト数でも「ファイル数」で 7.3 倍差が出る

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-save-load-idbfs.log` [3]

| 構成 | 総サイズ | `syncfs` |
|---|---|---|
| 4KB × 2000 ファイル | 約 8MB | **759.0 ms** |
| 8MB × 1 ファイル | 約 8MB | **103.9 ms** |

F-12 の通りコストはレコード件数で決まるため、バイト数は支配的でない。
この差は起動時の復元 `syncfs(true)` にも同じ構造で乗る
（= ゲーム全般のロード時間に直接影響する）。

なお本リポジトリは `WORLD_COMPRESSION2` の既定値を EMSCRIPTEN で
`false` にしている（`patches/cdda-0I-emscripten-mo-reader.patch`、
`src/options.cpp:1776-1788`）。圧縮を有効にすると quad が `maps.zzip`
1 ファイルに束ねられるため、この計測はその判断の再評価材料になる。
ただし無効化の理由（zzip の書き込み可能 mmap を MEMFS 上でヒープコピーで
エミュレートするため低メモリ機で不安定）は依然有効なので、
**既定値の変更ではなく、同期対象そのものを減らす方向で対処する。**

**結論**: 同期コストは「件数」で決まる。件数を抑える設計にする。

---

## F-14. 現行の 250ms デバウンスはセーブ中の多重同期を正しく防いでいる

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-save-load-idbfs.log` [4]

1200 quad のセーブ（本体 500ms）の最中に発火した `syncfs` は **0 回**。
`patches/cdda-0I-emscripten-idbfs-debounce.patch` の
`fsSyncInFlight` ガードと 250ms タイマーは意図通り動いている。

**この方向は既に解決済みであり、再調査不要。**
ただし `filesystem.cpp` の `setFsNeedsSync()`（`src/filesystem.cpp:60-65`）は
`assure_dir_exist` / `write_to_file` / `remove_file` / `rename_file` の
**4 箇所すべてで無条件に `EM_ASM` を跨ぐ**ので、
quad ごとに JS 境界を越える回数だけは残っている（F-11 の通り微小）。

**結論**: デバウンス機構は変更しない。狙うのは F-12 の件数削減。

---

## F-15. ロード中の描画停止は 259ms。MessageChannel 化で 32.6ms（8分の1）

- **確定日**: 2026-09-03
- **根拠**: `raw/2026-09-03-save-load-idbfs.log` [5][6]

データ規模（実測）:

```
$ find data/json -name "*.json" -exec cat {} + | grep -c '"type"'
60142          # JSON オブジェクト数
$ find data/json data/mods -name "*.json" | wc -l
6170           # JSON ファイル数
```

`init.cpp:195` の `inp_mngr.pump_events()` は
**JSON オブジェクト 1 個ごと**に呼ばれる（= 約 60142 回）。

| 構成 | 所要 | yield回数 | 描画フレーム | 最大描画停止 |
|---|---|---|---|---|
| 現行（`sleep(0)` / 250ms 予算） | 1107 ms | 4 | 4 | **259.4 ms** |
| MessageChannel / 16ms 予算 | 1082 ms | 65 | 64 | **32.6 ms** |
| MessageChannel / 8ms 予算 | 1066 ms | 130 | 63 | 24.7 ms |

**yield 回数を 16 倍にしても所要時間は増えない**（むしろ 25ms 短い）。
F-01 の 4ms クランプが消えるためである。

8ms まで詰めても描画フレームは 63 で増えない（表示の上限が 60fps）。
一方 yield 回数は倍になる。**16ms が最適点。**

またスロットル判定そのもの（`emscripten_get_now()` + 分岐を 60142 回）の
コストは誤差である（yield なし 1182ms vs 判定のみ 1196ms）。
**判定の間引きは不要。**

**結論**: ロード経路も同じ MessageChannel 化 + 16ms 予算に統一する。
ユーザー要求のプログレスバーは、この改善なしでは 259ms 止まって
フリーズに見えるため、**両者は同時に実施しないと意味がない。**

---

## F-16. `mapbuffer::save()` の進捗表示は 500ms 間隔で、yield もそこにしかない

- **確定日**: 2026-09-03
- **根拠**: `src/mapbuffer.cpp:183-237`（適用済みソースを直接確認）

```cpp
static constexpr std::chrono::milliseconds update_interval( 500 );
...
for( auto &elem : submaps ) {
    if( last_update + update_interval < now ) {
        popup.message( _( "Please wait as the map saves [%d/%d]" ), ... );
        ui_manager::redraw();
        refresh_display();
        inp_mngr.pump_events();      // ← セーブ経路で yield する唯一の場所
        last_update = now;
    }
    ...
    save_quad( ... );
}
```

つまりマップセーブ中は **500ms 間隔でしか** 画面が更新されず、
yield もそこでしか起きない。さらに `pump_events()` 側にも
250ms スロットル（`cdda_last_pump_yield`）が入っているため、
実効的な描画間隔は 500ms である。

`save_quad()`（同 :239-367）は quad ごとに
`world_generator->active_world->has_compression_enabled()` を評価し、
非圧縮時は `std::filesystem::exists()` を呼ぶ。上流のコメントが
「均一サブマップが膨大なため、この存在確認だけでセーブ総コストの
約 70% に達したテストケースがあった」と明記している（同 :260-262）。

**結論**: セーブ中の描画間隔を 500ms → 16ms 相当に詰める。
`pump_events()` 側のスロットルを 16ms にすれば、この 500ms 判定を
そのまま残しても描画は滑らかになる（判定自体は上流互換のため触らない）。

---

## F-17: 新プリミティブ `cata_web_yield` の実機検証（実装後の確認）

- **確認日**: 2026-09-03
- **確認方法**: `bench/verify_primitive.cpp` を実際の `src/cata_web_yield.{h,cpp}`
  とリンクして Chromium 実機で実行
- **信頼度**: 実測（自作コードの動作確認）

F-01〜F-05 の計測結果に基づいて実装した共有プリミティブ
`src/cata_web_yield.{h,cpp}` が、設計どおりに動くことを実機で確認した。

### コンパイル検証

```
em++ -std=c++17 -c cata_web_yield.cpp -Wall -Wextra -Wpedantic -Werror -DEMSCRIPTEN
→ 警告 0、エラー 0
```

CDDA 本体は `-Wpedantic -Werror` でビルドされるため、これは必須条件である。
`EM_ASM` の `$0` は `-Wdollar-in-identifier-extension` を踏むが、
`cata_web_yield.cpp` は引数なしの `EM_ASYNC_JS` のみを使うので該当しない。

### 実機測定結果

| 項目 | 実測値 | 期待値（F-01〜F-03） | 判定 |
|---|---|---|---|
| `yield_now()` 1 回 | **0.0366ms** | 0.075ms 前後 | OK（むしろ速い） |
| `emscripten_sleep(0)` 1 回（同一環境の対照） | **4.1580ms** | 4.25ms | OK |
| 倍率 | **114 倍** | 57 倍 | OK |
| `yield_if_due()` 200ms 中の yield 回数 | **12 回** | 200/16 = 12.5 回 | OK |
| `yield_paint()` 1 回 | **16.30ms** / フレーム進行 20/20 | 15.9ms・毎回 1 フレーム | OK |
| `yield_now()` 連打 500ms 中の rAF 発火 | **30 回** | 60fps なら 30 回 | OK |
| 再入ガード | クラッシュせず | Asyncify 入れ子巻き戻しを回避 | OK |

### 重要な確認事項

**1. `yield_now()` を 500ms 間連打しても rAF はきっちり 30 回発火した。**

これが `scheduler.yield()` を採用しなかった理由の裏返しの証明である。
F-04 で `scheduler.yield()` は rAF を 103〜115ms 間隔まで飢餓させたが、
MessageChannel はマクロタスクなので、ブラウザのレンダリング
ステップが各タスク境界で通常どおり挟まる。
**「速い」だけでなく「描画を止めない」ことを同一実験で同時に満たす。**

**2. `yield_if_due()` の予算判定は誤差なく機能した（12 回 / 期待 12.5 回）。**

`yield` 後に時刻を再取得する実装（`last_yield_ms = emscripten_get_now()`）
により、重い描画が挟まっても直後に再 yield しない。

**3. `yield_paint()` は 20 回呼んで 20 フレーム進んだ。**

`requestAnimationFrame` を確実に 1 枚待つ。1 回 16.30ms なので
ホットパスでは使えないが、`game::start_game()` のような
「1 回だけ確実に描画させたい」箇所には正しく使える。

### プリミティブの使い分け（実装上の指針）

| マクロ | 実体 | 1 回のコスト | 用途 |
|---|---|---|---|
| `CATA_WEB_YIELD()` | 16ms 予算付き MessageChannel | 0.037ms | ホットパス全般（ターン処理、JSON ロード、描画ループ、pump_events） |
| `CATA_WEB_YIELD_THROTTLED(n)` | n ms 予算付き | 0.037ms | 予算を変えたい場合 |
| `CATA_WEB_YIELD_NOW()` | 無条件 MessageChannel | 0.037ms | 呼び出し頻度が低く確実に譲渡したい箇所（`overmap::generate` 入口など） |
| `CATA_WEB_YIELD_PAINT()` | requestAnimationFrame | 16.3ms | 1 フレーム確実に描かせたい箇所のみ（`game::start_game`） |

非 Emscripten ビルドでは 4 つとも `do {} while( false )` に展開され、
ネイティブビルドの挙動・性能は一切変わらない。

---

## F-18: 製作（クラフト）が遅い真因は `do_turn.cpp` の 100ms レート制限

- **確認日**: 2026-09-03
- **確認方法**: `bench/craft_real.c` で実コードの構造を再現して Chromium 実測
- **信頼度**: 実測 + 一次ソース（CDDA 実コード）

ユーザ報告「実際にプレイしていて製作もかなり進みが遅かった」の原因を特定した。
**粉砕とは別の原因**であり、別の修正が必要である。

### 実コードから確定した製作中の 1 ターンの流れ

```
main.cpp:958          while( !do_turn() ) {}
do_turn.cpp:565       while( u.get_moves() > 0 || uquit == QUIT_WATCH ) {
do_turn.cpp:566-578     process_falling / cleanup_dead / mon_info_update /
                        process_sound_markers（NPC 全員）/ process_explosions
do_turn.cpp:579-583     if( !u.activity && ... ) { ui_manager::redraw(); }
                        ↑ 製作中は u.activity が真なので【この描画は走らない】
do_turn.cpp:587         g->handle_action()
do_turn.cpp:600-602     while( u.get_moves() > 0 && u.activity ) u.activity.do_turn( u );
                        → craft_activity_actor::do_turn（activity_actor.cpp:4163）
                        → 末尾で crafter.set_moves( 0 )（同 :4223）
do_turn.cpp:607-616   else 節:
                        static start; now;
                        if( ( now - start ).count() > 100 ) {   ← 【100ms レート制限】
                            handle_key_blocking_activity();
                            start = now;
                        }
do_turn.cpp:239         input_context ctxt = get_default_mode_input_context();
                        ← 146 個の register_action を毎回実行
do_turn.cpp:240         ctxt.handle_input( 0 )
do_turn.cpp:255-257     ui_manager::redraw(); refresh_display();
```

### 決定的な事実 3 点

**(1) 製作中は `do_turn.cpp:579` の `redraw()` が走らない。**

条件は `if( !u.activity && g->uquit != QUIT_WATCH && ... )` である。
製作中は `u.activity` が真なので、通常ターンの描画経路が丸ごと skip される。
画面が更新されるのは `handle_key_blocking_activity()` の中だけ。

**(2) その `handle_key_blocking_activity()` は 100ms に 1 回しか呼ばれない。**

`do_turn.cpp:613` のコメントは "Rate limit key polling to 10 times a second."
と書いており、意図は**キー入力ポーリングの節約**である。
ネイティブでは 100ms ごとの描画でも問題にならない（ターン処理自体が速く、
OS のイベントループはブロックされない）が、
**ブラウザでは wasm がメインスレッドを占有するため、
「100ms ごとに 1 回だけブラウザへ制御が返る」という意味になる。**

**(3) `get_default_mode_input_context()` は 1 回 0.0521ms かかり、
製作 1 ターンの実処理（0.0401ms）より重い。**

`register_action` は `std::find` による線形探索（`input_context.cpp:199`）を
毎回行うので、146 個の登録で 146×145/2 = 10585 回の文字列比較になる。
さらに `std::vector<std::string>` への push_back で 146 回のヒープ確保、
10 個は `to_translation()` 付きで `std::map` 挿入も伴う。

### 実測結果（`bench/craft_real.c`、Chromium 実機）

部品ごとの単体コスト:

| 部品 | 1 回のコスト |
|---|---|
| `get_default_mode_input_context()` 相当（146 register_action） | **0.0521ms** |
| 製作 1 ターンの実処理（`craft_activity_actor::do_turn` + 毎ターン処理） | 0.0401ms |
| 画面更新（`ui_manager::redraw()` + `refresh_display()`） | 0.0952ms |

製作 4000 ターン（実時間 66 分ぶんの作業）を通した比較:

| 条件 | 総時間 | 画面更新 | 描画フレーム | **最大描画停止** |
|---|---|---|---|---|
| [2] 現行（100ms 制限 / 毎回 ctxt 構築 / `sleep(0)`） | 160.0ms | 1 回 | **1** | **101.3ms** |
| [3] MessageChannel のみ（100ms 維持） | 151.5ms | 1 回 | **1** | **101.3ms** |
| [4] 16ms + MessageChannel（ctxt 毎回構築） | 160.3ms | 9 回 | **9** | **17.3ms** |
| [5] 16ms + MessageChannel + ctxt 再利用 | **150.7ms** | 9 回 | **9** | **16.8ms** |

4GB Chromebook 想定（1 ターンが 3 倍重い機体）:

| 条件 | 総時間 | 描画フレーム | 最大描画停止 |
|---|---|---|---|
| [6] 低速機 + 現行 | 162.3ms | **1** | **100.5ms** |
| [7] 低速機 + 改善案 | 158.3ms | **9** | **17.2ms** |

### 読み取り

**[3] が最重要の否定的結果**: 譲渡プリミティブを MessageChannel に
変えただけでは製作は改善しない（最大停止 101.3ms で変化なし）。
100ms のレート制限が支配しているため、
**F-01〜F-05 の粉砕向け修正だけでは製作の遅さは直らない。**
これは粉砕と製作が別原因であることの直接証明である。

**[4] で 100ms → 16ms にすると描画フレームが 1 → 9 に増え、
最大停止が 101.3ms → 17.3ms（5.9 分の 1）になる。**
しかも総時間は 160.0 → 160.3ms でほぼ変わらない。
つまり**滑らかさを 6 倍にする代償がゼロ**である。

**[5] でさらに ctxt を再利用すると総時間が 150.7ms になる。**
[4] 比 6% 短縮。ctxt 構築（0.0521ms）が更新 9 回ぶん消えるだけでなく、
146 回のヒープ確保/解放が消えることで GC 圧も下がる
（4GB 機ではこちらの効果がより大きい）。

**低速機ほど効果が大きい**: [6] vs [7] で描画フレームが 1 → 9。
低速機では 100ms の枠に入るターン数が減るため、
現行実装だと「100ms 止まって、わずかしか進まない」の繰り返しになる。
16ms 化すると 1 フレームあたりの進捗は小さくなるが、
**進捗が見えている時間の割合が 9 倍になる**ため体感が大きく改善する。

### 結論（実装方針）

1. `do_turn.cpp:613` の 100ms を 16ms に変更する（EMSCRIPTEN 限定）。
   ネイティブは 100ms のまま（元の意図であるキーポーリング節約を保つ）。
2. `do_turn.cpp:239` の `input_context` を関数 static にして再利用する。
   `get_default_mode_input_context()` は毎回同じ内容を返す純粋関数なので
   キャッシュしても挙動は変わらない。
3. `handle_key_blocking_activity()` の `redraw()` 経路に
   `CATA_WEB_YIELD()` を入れて、更新後にブラウザへ制御を返す。

これは F-01〜F-05（粉砕向け・`sdltiles.cpp` / `ui_manager.cpp`）とは
**独立した修正**であり、両方必要である。

---

## F-19. 日本語 IME が効かない真因は `input_context` スタック覗き見と `operator=` の欠落

- **確定日**: 2026-09-03
- **根拠**: `src/input_context.h` (0.I 原本), `src/input_popup.cpp:21`,
  `src/inventory_ui.cpp:2698-2731`, `src/advanced_inv.cpp:1904-1928`,
  `src/cata_imgui.cpp:1013-1036`, `src/game.cpp:3034-3042`,
  `raw/2026-09-03-text-input-scope.log`

### 修正前の仕組み

`patches/cdda-0I-emscripten-world-yield.patch` は、ブラウザ側の日本語 IME を
「文字入力欄にいるときだけ」有効にするために、`sdltiles.cpp` の
`cdda_web_wants_ime()` で **`input_context_stack` の一番上の要素の
カテゴリ名を覗き見** していた。

```cpp
// 修正前（概念）
static bool cdda_web_wants_ime()
{
    if( input_context_stack.empty() ) { return false; }
    const std::string &cat = input_context_stack.back()->get_category();
    return cat == "STRING_INPUT" || cat == "STRING_EDITOR" || ...;
}
```

`input_context_stack` は upstream では `__ANDROID__` 限定の仕組みで、
本リポジトリの `world-yield` パッチが `EMSCRIPTEN` にも広げたものである。

### この方法が壊れる 2 つの独立した理由

**理由 1: `input_context::operator=` がスタックを操作しない**

`src/input_context.h` の構造（0.I 原本、行番号は本リポジトリの clone 基準）:

| 位置 | 内容 |
|---|---|
| 55-57 | `input_context()` が `#if defined(__ANDROID__) \|\| defined(EMSCRIPTEN)` で `input_context_stack.push_back( this )` |
| 70-72 | `explicit input_context( category, mode )` も同じく `push_back( this )` |
| 80-90 | `#if defined(EMSCRIPTEN) && !defined(__ANDROID__)`: `~input_context()` が `remove( this )`、および `get_category()` |
| 136-151 | `#if defined(__ANDROID__)` 限定の `operator=`。**12 個のメンバをコピーするだけで、スタック操作を一切しない** |

そして `src/input_popup.cpp:21` は、コンストラクタ本体で

```cpp
ctxt = input_context( "STRING_INPUT", keyboard_mode::keychar );  // コピー代入
```

をしていた。この 1 行で起きることを追うと:

1. メンバ `ctxt` はメンバ初期化で **既定コンストラクタ** が走り、
   カテゴリ `"default"` で `push_back( this )` される。
2. 右辺の一時オブジェクトが `"STRING_INPUT"` で `push_back` される。
3. `operator=` がメンバをコピーする（**スタックは触らない**）。
4. 一時オブジェクトが破棄され、デストラクタが自分自身を `remove` する。

結果、**スタックの上に残るのは `"STRING_INPUT"` ではなくカテゴリ
`"default"` のメンバ `ctxt`**。`get_category()` は `"default"` を返し、
`cdda_web_wants_ime()` は `false` を返す。IME は永久に有効にならない。

なお `operator=` は `__ANDROID__` 限定なので、Android では
そもそもこのコピー代入が**コンパイルできない**（暗黙の代入演算子も
`std::list` に自身を登録する型では意図どおりに働かない）。
つまりこの経路は Android では踏まれず、EMSCRIPTEN でだけ静かに壊れていた。

**理由 2: スタックの「一番上」は入れ子順に依存する**

`inventory_selector::query_string()` (`inventory_ui.cpp:2698-2731`) は

```cpp
spopup = std::make_unique<string_input_popup>();
do {
    ui_manager::redraw();
    spopup->query_string( /*loop=*/false );
} while( !spopup->confirmed() && !spopup->canceled() );
spopup.reset();
```

という形で、**外側の inventory_selector 自身の `input_context` を
スタックに載せたまま**、内側に文字入力欄を作る。
`advanced_inv.cpp:1904-1928` も同じ形。

このとき `input_context_stack.back()` が文字入力欄のものになるかは、
両者の構築順・破棄順という実装詳細に依存する。
「一番上を見る」設計そのものが、入れ子のある UI では成立しない。

### 修正後の仕組み

スタックの覗き見をやめ、**明示的な参照カウント方式**に置き換えた。
新規ファイル `src/cata_web_text_input.{h,cpp}`:

| API | 用途 |
|---|---|
| `cata_web::text_input_scope` | RAII。文字入力ウィジェットのメンバとして持つ |
| `CATA_WEB_TEXT_INPUT_MEMBER` | 上記をメンバ宣言するマクロ。非 EMSCRIPTEN では空展開で `sizeof` を変えない |
| `cata_web::set_text_input_flag( key, bool )` | ウィジェットオブジェクトを持たない「状態フラグ型」入力欄用。**冪等** |
| `cata_web::text_input_active()` | `cdda_web_wants_ime()` がこれを返すだけになった |

`sdltiles.cpp`:

```cpp
static bool cdda_web_wants_ime()
{
    return cata_web::text_input_active();
}
```

**「呼び出しスコープ」ではなく「オブジェクト寿命」に束ねた理由**:
上記の `inventory_ui.cpp` のように、呼び出し側は
`query_string( /*loop=*/false )` を**毎フレーム呼ぶ**。
`query_string()` の内部をスコープにすると、1 フレームごとに
IME が ON/OFF を繰り返し、変換途中の未確定文字列が壊れる。
`string_input_popup` オブジェクトが生きている間 = 入力欄が
画面に出ている間、という対応が正しい。

### スコープを設置した箇所（全 4 箇所）

| ファイル:行 | クラス | 備考 |
|---|---|---|
| `string_input_popup.h:108` | `string_input_popup` | curses/tiles 共通の主要入力欄 |
| `input_popup.h:113` | `string_input_popup_imgui` | **基底 `input_popup` には入れない**（後述） |
| `string_editor_window.h:54` | `string_editor_window` | 複数行エディタ |
| `game.cpp:3049` | `end_screen_ui_impl` | 死亡画面の「最後の言葉」欄 |

**基底クラス `input_popup` に入れなかった理由**:
`number_input_popup<T>` が同じ基底を継承している。数値欄で日本語 IME が
有効になると、半角数字の直接入力が変換対象に取られて入力できなくなる。
文字列専用の派生クラスにだけ持たせるのが正しい。

**`end_screen_ui_impl` を個別に扱った理由**:
この画面は `string_input_popup` を経由せず
`ImGui::InputText( "##LAST_WORD_BOX", &text )` を直接呼ぶ
(`game.cpp:3117`)。共通経路の修正では拾えない。

### フラグ方式が必要だった箇所（1 箇所）

キーバインドメニュー (`input_context::display_menu()`) の絞り込み欄は、
**専用の入力欄クラスを持たない**。`cataimgui::window::draw_filter()`
(`cata_imgui.cpp:1013`) が `ImGui::InputText` を毎フレーム描き、
入力中かどうかは `kb_menu_status::filter` という**状態変数**でしか
表現されていない。

そこで `display_menu()` のループ内で毎周

```cpp
cata_web::set_text_input_flag( &kb_filter_key,
                               kb_menu.status == kb_menu_status::filter );
```

を呼ぶ。`set_text_input_flag()` は冪等なので毎フレーム呼んで問題ない。

さらに、このループは `break` / `return action_to_execute;`
(`input_context.cpp:1142`) / 例外の複数経路で抜けるため、
ループ**手前**に RAII ガードを置いて必ずフラグを下ろす:

```cpp
struct kb_filter_guard_t {
    ~kb_filter_guard_t() {
        cata_web::set_text_input_flag( &kb_filter_key, false );
    }
} kb_filter_guard;
```

これがないと、**絞り込み入力中にメニューを閉じるとフラグが立ったまま
残り、マップ移動中も IME が有効になって操作不能になる**。

なお `draw_filter()` の呼び出し元は `input_context.cpp:687` の 1 箇所だけ
であることを確認済み（`grep -rn "draw_filter\b"`）。他 UI への波及はない。

### 合わせて直したコピー代入の欠陥

`src/input_popup.cpp` のコンストラクタを初期化リストに直した:

```cpp
input_popup::input_popup( int width, const std::string &title, const point &pos,
                          ImGuiWindowFlags flags ) :
    cataimgui::window( ... ),
    ctxt( "STRING_INPUT", keyboard_mode::keychar ),   // ← コピー代入をやめた
    pos( pos ),
    width( width )
```

参照カウント方式に変えたので IME 判定自体はもうこれに依存しないが、
`"default"` カテゴリの `input_context` がスタックに載り続けるのは
Android のクイックショートカット表示にも影響する明確な欠陥なので直した。

### 意味論の検証結果

`.scratch/ctest2/t.cpp`（`bench/text_input_scope_test.cpp` として同梱）で
ネイティブ実行して確認:

| # | 検証内容 | 結果 |
|---|---|---|
| 1 | RAII の入れ子（depth 0→1→2→1→0）と `active()` の追従 | OK |
| 2 | 同じキーで `set_text_input_flag(true)` を 5 回呼んでも depth は 1 | OK |
| 3 | 異なるキーは独立にカウントされ、片方を下ろしても `active()` は真 | OK |
| 4 | RAII とフラグの混在（depth 1→2→1→0） | OK |
| 5 | 立っていないフラグを下ろしても depth が負にならない | OK |

コンパイル検証:

| 条件 | 結果 |
|---|---|
| `em++ -std=c++17 -Wall -Wextra -Wpedantic -Werror -DEMSCRIPTEN` | EXIT=0、警告ゼロ |
| `g++ -std=c++17 -Wall -Wextra -Wpedantic -Werror`（EMSCRIPTEN 未定義） | EXIT=0、816 バイト＝実質空 TU |

CDDA は `-Wpedantic -Werror` でビルドするため、`-Werror` クリーンは必須条件。

### 結論

- スタック覗き見（`input_context_stack.back()->get_category()`）は**廃止**。
  入れ子 UI で原理的に成立しない。
- IME の要求は**入力欄オブジェクトの寿命**に束ねる。
- オブジェクトを持たない入力欄（キーバインド絞り込み）だけ
  冪等フラグ + RAII ガードで扱う。
- 数値入力欄には**絶対にスコープを付けない**。

### 今後 IME 対応の入力欄を増やすときの手順

1. `string_input_popup` / `string_input_popup_imgui` /
   `string_editor_window` のどれかを使っているなら**何もしなくてよい**。
2. `ImGui::InputText` を直接呼ぶ新しいクラスを書いたら、
   そのクラスの private に `CATA_WEB_TEXT_INPUT_MEMBER` を 1 行足す。
3. 入力中かどうかが状態変数でしか分からない場合だけ
   `set_text_input_flag()` を使い、**必ず RAII ガードで解除**する。
4. 数値専用欄には付けない。

---

## F-20 読書・製作など長時間アクティビティが遅い真因は `mon_info_update()` の無制限呼び出し

計測日: 2026-09-03
計測器: `bench/activity_paths.c`
生ログ: `docs/measurements/raw/2026-09-03-activity-paths.log`

### この節を追加した理由

「読書の進行がゲームがまともにできないレベルで遅い」という報告に対し、
**ロード・読み込みの全経路を網羅的に計測**し、
**ゲームプレイに差し支えない経過時間の基準を軸に**改善点を洗い出した記録。

F-18（製作の遅さ）とは**さらに別の真因**である。
F-18 は「描画経路が 100ms に絞られていた」という描画側の問題だったが、
本節は「1 ターンあたりの計算量そのもの」の問題である。

### 判定軸（プレイに差し支えない経過時間の基準）

これまでの節は「速くなった / 遅くなった」の相対比較だった。
本節では**絶対的な合否基準**を先に決めてから計測している。

| # | 基準 | しきい値 | 根拠 |
|---|---|---|---|
| A | 最大描画停止 | ≤ 50ms | これを超えるとフレーム落ちを知覚する（20fps 相当の 1 フレーム分） |
| B | 入力応答遅延 | ≤ 100ms | Nielsen の「0.1 秒 = 即座に反応したと感じる」限界 |
| C | 進捗表示の更新 | ≤ 500ms | これを超えると「固まった」と感じる |
| D | 実描画フレーム率 | ≥ 20fps | 下回るとスクロールが飛んで視認できない |

出典: Nielsen, *Usability Engineering* (1993) の応答時間しきい値
（0.1 秒 = 即座に感じる / 1.0 秒 = 思考が途切れない / 10 秒 = 注意が切れる）。
ゲームは毎フレーム画面が変わる文脈なので、上記のうち
「0.1 秒」を入力応答に、その半分以下を描画停止に割り当てて厳しめに取った。

**この 4 基準が、以降すべての改善判断の軸である。**

### まず否定した仮説

| 仮説 | 検証 | 結果 |
|---|---|---|
| `read_activity_actor::do_turn` が重い | `activity_actor.cpp:1702-1760` を読み、実測 | **否**。0.0040ms。分岐と一部の乗算のみ |
| オートセーブが挟まっている | `options.cpp:1661` `AUTOSAVE_MINUTES` 既定 5（実時間 5 分） | **否**。読書 1 冊の間に基本かからない |
| マップのロードが挟まっている | 読書中はプレイヤーが動かない | **否**。submap のロードは発生しない |
| yield プリミティブが悪い | F-17 の修正が入った状態でも遅い | **否**（F-18 と同じ結論） |

### 真因

技能書 1 冊は `data/json/items/book/` の最頻値 `"time": "30 m"` = ゲーム内 30 分。
CDDA の 1 ターンは 1 ゲーム秒なので **1800 ターン**である（48 冊が該当）。

読書本体が軽くても、**1800 ターンぶん `do_turn()` の全パイプラインが回る**。
その内訳を実測したのが下表（1 回あたり ms）。

| 部品 | ms | 全ターンに占める割合 |
|---|---|---|
| `g->mon_info_update()` | **0.6170** | **71.7%** |
| `monmove()` | 0.4185 | 48.7% |
| `ui_manager::redraw()` + `refresh_display()` | 0.3650 | 42.4% |
| `u.process_turn()` | 0.2565 | 29.8% |
| `m.build_map_cache( levz, true )` | 0.2333 | 27.1% |
| `m.process_items()` | 0.2155 | 25.1% |
| `get_default_mode_input_context()` | 0.1545 | 18.0% |
| `m.build_floor_caches()` | 0.1350 | 15.7% |
| `m.process_fields()` | 0.0545 | 6.3% |
| 読書本体（activity actor） | 0.0040 | 0.5% |
| **1 ターン全パイプライン合計** | **0.8600** | 100% |

（合計が各部品の総和より小さいのは、部品単体計測に含まれる
ウォームアップ・キャッシュ効果の差。割合は「合計に対する比」として読む。）

**`mon_info_update()` 単独で 1 ターンの 72% を占める。**

重い理由は `Character::get_visible_creatures( MAPSIZE_X )`
（`character.cpp:10624-10632`）:

```cpp
return g->get_creatures_if( [this, range, &here]( const Creature & critter ) -> bool {
    return this != &critter && pos_abs() != critter.pos_abs() &&
    rl_dist( pos_abs(), critter.pos_abs() ) <= range && sees( here, critter );
} );
```

`rl_dist()` に加えて **`sees( here, critter )` の完全な LOS 判定**を
可視クリーチャ全件に対して行う。`MAPSIZE_X = 132` なので
リアリティバブル全域（132×132 = 17424 タイル）が対象になる。

さらにこれは `do_turn.cpp` の**3 箇所**で呼ばれ、**いずれもレート制限がない**。

| 行 | 位置 | 文脈 |
|---|---|---|
| 613 | `while( u.get_moves() > 0 \|\| g->uquit == QUIT_WATCH )` 内 | `m.process_falling(); g->cleanup_dead();` の直後 |
| 696 | 上記 `if` の `else` 節 | レート制限済み `handle_key_blocking_activity()` ブロック（682-694）の直後 |
| 762 | 毎ターンのメインパイプライン | field 放出ブロックの後、`u.process_turn()`（763）の直前 |

アクティビティ中は行動力を使い切っているので **696 の経路**が主に回る。

### 描画経路が 3 つしかないことの再確認（F-18 の補強）

| 行 | 経路 | アクティビティ中の挙動 |
|---|---|---|
| 621 | `if( !u.activity && ... ) ui_manager::redraw()` | **実行されない**（条件に `!u.activity` があるため） |
| 696 | `handle_key_blocking_activity()` | 0.I は 100ms 制限、本 PR で 16ms に変更済み（F-18） |
| 795 | `wait_popup` の redraw | `wait_refresh_rate` = **`5_minutes`（=300 ターン）**（786-793） |

`refresh_display()`（`sdltiles.cpp:542`）は SDL の
`SetRenderTarget` / `ClearScreen` / `RenderCopy` を呼ぶだけで、
**ブラウザのイベントループには制御を返さない**。
したがって「実際に画面が出る」のは yield を挟んだ時だけである。

### 単純にスキップしてはいけない理由

`mon_info_update()` は表示更新だけの関数ではなく、**safemode を駆動している**
（`game.cpp:4845-4875`）:

| 副作用 | 意味 |
|---|---|
| `set_safe_mode( SAFE_MODE_STOP )` | 新規発見時に移動を止める |
| `cancel_activity_or_ignore_query( distraction_type::hostile_spotted_far, ... )` | アクティビティ中断の問い合わせ |
| `turnssincelastmon += calendar::turn - previous_turn` / `AUTOSAFEMODE` | 自動 safemode 復帰 |
| `previous_turn = calendar::turn; mostseen = newseen;` | 差分検出の基準値更新 |
| `mon_visible.new_seen_mon` | `game.cpp:10318` の警告表示が読む |

つまり「呼ばない」ことはできない。
できるのは**呼ぶ頻度に上限を切って間引く**ことだけである。

### 検討した 2 案

| 案 | 方式 | 懸念 |
|---|---|---|
| 案A | 描画のタイミング（16ms ごと）にまとめて 1 回だけ実行 | 実時間基準なので、**CPU 速度によって遅延ターン数が変わる** |
| 案B | **ゲームターン N ターンごと**に実行 | 遅延が N ターンで一定。N の選定が必要 |

**実時間ではなくゲームターンを基準にすべき理由**:
モンスターの接近はゲームターンで進む。実時間で間引くと、CPU が速い環境では
1 回の間隔に入るターン数が増え、safemode の反応が何ターン遅れるかが
**機種依存**になる。ターン数で切れば、どんな機種でも遅れは最大 N ターンで一定。

### 計測結果（読書 600 ターン。技能書 1800 ターンの 1/3。総時間は 3 倍で読む）

最大描画停止・fps は 1 ターンあたりの性質なのでターン数に依らない。

| シナリオ | total_ms | frames | fps | 最大描画停止 | 入力応答 | 描画回数 |
|---|---|---|---|---|---|---|
| [2a] 現行 0.I | 544 | 5 | 9.2 **NG** | 105.8 **NG** | 101.2 **NG** | 5 |
| [2b] 本 PR 現状（F-18 まで） | 564 | 31 | 55.0 OK | 34.1 OK | 17.2 OK | 32 |
| [2c] 案A | **391** | 22 | 56.3 OK | 17.9 OK | 17.0 OK | 22 |
| [2d] 案B（N=16） | 450 | 26 | 57.8 OK | 17.8 OK | 17.2 OK | 26 |

**低速機（CPU 3 倍遅い想定 = RAM 4GB Chromebook 相当）:**

| シナリオ | total_ms | frames | fps | 最大描画停止 | 入力応答 | 描画回数 |
|---|---|---|---|---|---|---|
| [3a] 現行 0.I | 1719 | 29 | 16.9 **NG** | 106.1 **NG** | 102.4 **NG** | 16 |
| [3b] 本 PR 現状 | 1623 | 89 | 54.8 OK | 23.7 OK | 23.3 OK | 89 |
| [3c] 案A | 1255 | 68 | 54.2 OK | **44.3** OK | 44.0 OK | 68 |
| [3d] 案B（N=16） | **1181** | 67 | 56.7 OK | **18.6** OK | 18.3 OK | 67 |

### 基準に対する判定

- **現行 0.I は基準 A / B / D の 3 つすべてを満たさない。**
  最大描画停止 106ms（基準 50ms の 2.1 倍）、
  入力応答 102ms（基準 100ms 超）、実 fps 16.9（基準 20fps 未満）。
  「ゲームがまともにできない」という報告は数値上も正しい。
- 本 PR の F-18 までの修正で**すでに全基準を満たす**（fps 54.8 / 停止 23.7ms）。
  ただし総時間は速い機種でむしろわずかに悪化した（544 → 564ms）。
  描画回数が 6 倍になったコスト分である。
- **案B が最良。** 低速機で 0.I 比 **31% 高速化**（1719 → 1181ms）し、
  かつ最大描画停止を **106.1ms → 18.6ms** に縮める。

### 案A を採らなかった理由

速い機種の総時間だけ見ると案A（391ms）が案B（450ms）より速い。
しかし**低速機では逆転する**（案A 1255ms / 案B 1181ms）。

さらに決定的なのは最大描画停止で、案A は低速機で **44.3ms** に悪化する。
基準 A（50ms）はぎりぎり満たすが、案B の 18.6ms とは 2.4 倍の差がある。
原因は、案A が `mon_info_update()` を**描画のタイミングで実行する**ため、
その 0.6170ms × 低速係数 3 のコストが**描画ウィンドウの内側に入る**こと。
描画のために制御を返す直前に一番重い処理を足す形になっている。

「RAM 4GB の PC でも」という要件を満たすには低速機の数値を優先すべきなので、
**ターン数基準の案B を採用**する。

### N=16 の根拠（間隔の掃引）

N は「小さすぎると効果が出ない / 大きすぎると safemode が遅れる」
というトレードオフなので、実測で決めた。
外れ値対策として各 N を 3 回まわし中央値を採用（低速機想定・600 ターン）。

| N | total_ms | fps | 最大描画停止 | 判定 |
|---|---|---|---|---|
| 1 | 1592 | 55.3 | 19.6 | 間引き効果なし |
| 2 | 1365 | 55.7 | 19.4 | |
| 4 | 1290 | 54.3 | 50.0 | |
| 8 | 1229 | 54.5 | 35.8 | |
| **16** | **1172** | **57.2** | **19.0** | **飽和点** |
| 32 | 1162 | 56.8 | 34.0 | これ以上速くならない |
| 64 | 1178 | 56.0 | 36.0 | 遅延だけ増える |

**N=16 で総時間が飽和する**（1365 → 1290 → 1229 → **1172** → 1162 → 1178）。
N=32 以上にしても速くならず、safemode の遅延だけが伸びる。
したがって N=16 は「**最大の効果が得られる最小の N**」であり、
safemode の反応を不必要に遅らせずに済む値である。

CDDA の 1 ターンは 1 ゲーム秒なので **16 ターン = 16 ゲーム秒**。

### 実装

`src/do_turn.cpp` の匿名 namespace に `mon_info_update_throttled( bool force )` を追加し、
613 / 696 / 762 の 3 箇所を差し替えた。

```cpp
#if defined(EMSCRIPTEN)
constexpr int mon_info_update_interval_turns = 16;

void mon_info_update_throttled( const bool force = false )
{
    static time_point last_update = calendar::turn_zero;
    static bool has_run = false;

    if( !force && has_run &&
        calendar::turn - last_update < time_duration::from_turns(
            mon_info_update_interval_turns ) ) {
        return;
    }
    last_update = calendar::turn;
    has_run = true;
    g->mon_info_update();
}
#else
void mon_info_update_throttled( const bool /*force*/ = false )
{
    g->mon_info_update();
}
#endif
```

呼び出しはすべて `mon_info_update_throttled( !u.activity )`。

### 適用範囲を「アクティビティ中」だけに絞った理由

`force = !u.activity` としている点が重要である。

間引きを常時かけると、**通常の歩行・戦闘中も** safemode の反応が
最大 16 ターン遅れうる。しかしそこは:

- 元々 1 ターンに数回しか回らないので**間引いても速くならない**
- モンスターと対峙している最も危険度が高い場面である

そこで `u.activity` が空（= プレイヤーが自分で操作している場面）では
**毎ターン必ず実行**する。間引くのは読書・製作・分解・粉砕など
「1800 ターン一気に進む」場面に限る。

**これにより通常プレイの挙動は 0.I と完全に同一のまま、
長時間アクティビティだけが速くなる。**

### ネイティブ版への影響

`#else` 側は `g->mon_info_update()` をそのまま呼ぶ薄いラッパで、
`-O3` でインライン展開されるため**コストゼロ・挙動完全同一**。
ネイティブでは 1 ターン 0.86ms が問題にならないので間引く必要がない。

### コンパイル検証

| 対象 | コマンド | 結果 |
|---|---|---|
| `src/do_turn.cpp` | CDDA の実ビルドフラグ（`-Werror -Wall -Wextra -Wpedantic -Wold-style-cast -Wsuggest-override -Wzero-as-null-pointer-constant` 等）で `-fsyntax-only` | **警告 0 / エラー 0** |

使用 API はすべて既存のもの:
`calendar::turn_zero`（`calendar.h:557`）、
`time_duration::from_turns`（`calendar.h:212`、`constexpr`）。
`do_turn.cpp` は既に `calendar.h` を include 済みなので追加 include は不要。

### 結論

- 読書が遅いのは読書処理のせいではなく、**1800 ターンぶんの
  `do_turn()` 全パイプライン**が回るため。
- その 72% は `mon_info_update()` であり、**3 箇所すべてで無制限**に呼ばれていた。
- safemode を駆動するので**スキップは不可**。**ゲームターン数で上限を切る**のが正解。
- 実時間基準（案A）は機種依存の遅延を生み、低速機で描画停止が 2.4 倍悪化する。
- N は掃引の結果 **16 が飽和点**。それ以上は遅延だけが増える。
- `force = !u.activity` により**通常プレイは 0.I と完全同一**。

### この節から導ける一般則

1. **重いのは「1 回のコスト」ではなく「1 回 × 回数」**。
   読書本体 0.0040ms は無視できるが、1800 ターン回れば効いてくるのは
   本体ではなく同時に回るパイプラインである。
   アクティビティの遅さを調べるときは actor ではなく `do_turn()` を見る。
2. **副作用を持つ処理は「間引く」しかできない。「飛ばす」とゲーム性が壊れる。**
   間引くときは必ず「何がどれだけ遅れるか」を明示できる単位
   （ここではゲームターン）で上限を切る。
3. **実時間で間引くとゲーム的な遅延が機種依存になる。**
   ゲームロジックに関わる間引きはゲーム内時間で切る。
4. **間隔は掃引して飽和点を採る。** 大きくすれば速くなるとは限らず、
   飽和点を超えると副作用（遅延）だけが増える。
5. **最適化は低速機の数値で判断する。** 速い機種では案A が有利だが、
   要件が「RAM 4GB でも」なら低速機の逆転結果を優先する。

### 補足: 基準 C（進捗表示 ≤ 500ms）は変更不要だった

当初 `wait_refresh_rate = 5_minutes`（= 300 ターン）が基準 C を
破っているのではないかと疑った。しかし `do_turn.cpp:910-913` を読むと
**2 段構えになっている**:

```cpp
if( g->first_redraw_since_waiting_started ||
    calendar::once_every( std::min( 1_minutes, wait_refresh_rate ) ) ) {   // ← 60 ターン
    if( g->first_redraw_since_waiting_started ||
        calendar::once_every( wait_refresh_rate ) ) {                       // ← 300 ターン
        ui_manager::redraw();          // メイン UI（マップ）の再描画
    }
    ...
    g->wait_popup->on_top( true ).wait_message( "%s", wait_message );
    ui_manager::redraw();              // 進捗ポップアップの再描画
    refresh_display();
}
```

- 300 ターンおきなのは**メイン UI（マップ）**の再描画。
- **進捗ポップアップ**の更新は `std::min( 1_minutes, ... )` = **60 ターン**おき。

60 ターンを実時間に換算すると:

| 環境 | 1 ターン | 60 ターン | 基準 C（≤500ms） |
|---|---|---|---|
| 現行 0.I・低速機 | 2.87ms | 172ms | OK |
| 案B・低速機 | 1.97ms | 118ms | OK |
| 案B・高速機 | 0.75ms | 45ms | OK |

**いずれも基準 C を満たすので `wait_refresh_rate` は変更しない。**

なお `refresh_display()` はブラウザに制御を返さないので、
この 60 ターンおきの更新が「実際に画面に出る」のは
次に yield したときである。本 PR では `handle_key_blocking_activity()` が
16ms おきに yield するので、画面反映の遅れは最大 16ms しか増えない。

**必要のない変更をしないことも監査結果である。**
`wait_refresh_rate` を短くすると `ui_manager::redraw()`（0.3650ms）の
呼び出し回数が増え、総時間が悪化するだけで基準の改善にはならない。

---

## F-21. ビルド分割（Workflow 高速化）で確認した事実

**要求**: 「Workflow で 4 時間を要したので、Workflow の処理能力を
目いっぱいに使って実装速度を高速化してほしい。この時、プログレスバーも必要だ」

### F-21-1. 所要時間の内訳と、分割できる／できない境界

| 工程 | 内容 | 並列化 | 根拠 |
|---|---|---|---|
| コンパイル | 440 TU → `.o` | **できる** | TU 間に依存が無い |
| リンク | `.o` → `.wasm` | **ジョブ分割はできない**（ただしプロセス内はマルチスレッド） | 大域解析が必要。詳細は下記 |
| データ同梱 | `data/`+`gfx/` → `.data` | 別工程と**並行できる** | `.js`/`.wasm` に依存しない |

**リンクをジョブ分割できない理由**（推測ではなくフラグから確定）:

```
LDFLAGS += -Os
LDFLAGS += -sASYNCIFY
```

`-sASYNCIFY` は**モジュール全体の呼び出しグラフを解析**して、
スタックを巻き戻す必要のある関数すべてにステート機械を挿入する変換である。
解析が本質的に大域的なので、モジュールを分割すると呼び出しグラフが
分断されて成立しない。よって**リンクは 1 台の runner 上の 1 プロセス**で
やりきるしかなく、GitHub Actions の N ジョブに割ることはできない。

#### 【重要な訂正】「`wasm-opt` は単一スレッド」は誤りだった

本ファイルの以前の版は「`wasm-opt` も単一スレッドで動く」と書いていた。
**これは誤りである。** 実測とソースの両方で反証された。

実測（`user` 時間が `real` を超えるなら複数コアを使っている証拠）:

```
$ nproc
2
$ wasm-opt --asyncify -Os b3.wasm -o /dev/null     # 753KB の wasm
real 5.829  user 9.258  sys 0.249     ← user/real = 1.59 倍 → マルチスレッド
$ BINARYEN_CORES=1 wasm-opt --asyncify -Os ...
real 6.853  user 6.684  sys 0.155     ← user/real = 0.98 倍 → 単一スレッド
```

ソース側の根拠（Binaryen）:

- `src/passes/Asyncify.cpp` に `bool isFunctionParallel() override { return true; }`
  が 920 / 1259 / 1420 / 1444 行に存在する
- `src/passes/Asyncify.cpp:440` で
  `ModuleUtils::ParallelFunctionAnalysis<Types> analysis(...)` を使っている
- `src/ir/module-utils.h:271` の `struct ParallelFunctionAnalysis` に
  「Perform an analysis by operating on each function, in parallel.」とコメント

**なぜ誤ったのか / 教訓**:
「ジョブに分割できない」＝「単一スレッド」だと短絡した。この 2 つは別の話である。
*大域解析が必要（＝プロセスを分けられない）*ことと
*プロセス内でスレッド並列できる*ことは両立する。
以後、並列性の主張は必ず `user`/`real` 比で裏を取ること。

#### ただし「マルチスレッドだから速い」わけではない（スケーリングは悪い）

上の実測をそのまま読むと、2 コアでの短縮は
`6.853s → 5.829s` = **わずか 1.18 倍**にとどまる。
`user` が 1.59 倍になっているのに実時間が 1.18 倍しか縮まないのは、
Asyncify の大域解析部分（関数並列にできない部分）が支配的で、
アムダールの法則どおり頭打ちになっているためである。

→ **コアを増やしてもリンク時間はほとんど縮まらない。**
リンクを速くしたいなら、コア数ではなく
**やらせる仕事の量そのものを減らす**しかない（F-22 / F-23 を参照）。

> **⚠️ 本番規模での追記（F-23-2）**: 上の実測は小さいモジュールでの話である。
> 本番の wasm では wasm-opt は **4 コア中 1.05 コアしか使っていない**
> （CPU% 平均 105 / 最大 112、2218 サンプル）。
> つまり大規模では「スケーリングが悪い」どころか
> **ほとんど並列に走っていない**。コア増設の効果はゼロと考えてよい。

### F-21-2. オブジェクト一覧は Makefile 自身に聞く（440 個）

```
make -s print-OBJS NATIVE=emscripten BACKTRACE=0 TILES=1 TESTS=0 \
  RUNTESTS=0 RELEASE=1 LOCALIZE=1 LANGUAGES=ja CCACHE=1 LINTJSON=0
→ 440 個
```

`print-%:` ターゲット（`ci/tune-makefile.sh` が追記）:
```make
print-%:
	@echo $($*)
```

**`ls src/*.cpp` で推測してはいけない。** CDDA の Makefile は
```make
SOURCES += $(THIRD_PARTY_SOURCES)   # flatbuffers / zstd
SOURCES += $(IMGUI_SOURCES)         # SDL の有無で中身が変わる
```
と条件分岐でソースを増減させる。推測すると
`obj/tiles/third-party/flatbuffers/idl_parser.o` や
`obj/tiles/third-party/imgui/imgui.o` を取りこぼし、
**リンク時に未定義シンボルになる**（＝数十分待ってから失敗する）。

### F-21-3. 分割は「剰余」で配る（連続ブロックではない）

一覧は `sort` 済みなので、連続ブロックで切ると同じ接頭辞の
ファイル（`mapgen*.cpp`, `monster*.cpp` 等）が 1 シャードに集まる。
CDDA では大きい TU 同士が名前で隣接しがちなので所要時間が偏る。

**並列ビルドの所要時間は最も遅いシャードで決まる**ため、偏りは損失になる。
剰余（`( NR - 1 ) % n`）で配れば大きい TU が全シャードに散る。

実測（新規クローン・8 分割）: 55 / 55 / 55 / 55 / 55 / 55 / 55 / 55

### F-21-4. make 変数が食い違うと「リンクまで発覚しない」

分割すると make を呼ぶ場所が「計画・N シャード・リンク」に増える。
渡す変数が 1 つでも違うと壊れるが、**壊れ方が遅い**。

| 食い違う変数 | 起きること | 発覚する時点 |
|---|---|---|
| `TILES` | `IMGUI_SOURCES` の有無が変わり OBJS の中身がずれる | リンク |
| `RELEASE` | `OPTLEVEL` と `-DRELEASE` が変わり PCH と不整合 | コンパイル |
| `BACKTRACE` | `-DBACKTRACE` の有無でヘッダの条件分岐がずれる | リンク（ODR） |
| `LOCALIZE` | `-DLOCALIZE` の有無で同様にずれる | リンク |

→ **`ci/make-args.sh` の配列 1 つに集約し、全員が source する。**
YAML に 3 か所コピペする方式では必ず片方を直し忘れる。

### F-21-5. PCH は配布してはいけない（各機で生成する）

`.o` の生成規則は PCH を前提にしている:
```make
$(ODIR)/%.o: $(SRC_DIR)/%.cpp $(PCH_P)
```

PCH（`pch/main-pch.hpp.gch`）の実測サイズ: **19MB**、生成時間 **5 秒**。

**配布が不可な理由**: PCH には生成時の絶対パス・コンパイラのバージョン・
有効なマクロが焼き込まれており、環境が違うと拒否される。
実際に遭遇したエラー（F-20 の検証中）:
```
error: __OPTIMIZE__ predefined macro was enabled in PCH file
       but is currently disabled
```

→ 各シャードでローカルに作る。5 秒の重複コストは N ジョブが
並列に払うので、全体には 5 秒ぶんしか効かない。

### F-21-6.【最重要】復元した `.o` のタイムスタンプ問題

分割ビルドで最も事故りやすい箇所。**しかも「遅いだけで成功する」ので
気づけない。**

make は mtime を比較して再ビルドを判断する。
アーティファクトから `.o` を復元すると、同じジョブで checkout した
`.cpp` の mtime は「checkout した時刻」＝ほぼ現在時刻になる。
つまり高い確率で **`.cpp` が `.o` より新しい**と判定され、
make は 440 個を全部コンパイルし直す。→ **分割の意味が完全に消える。**

**実測での確認**:

| 状態 | `make -q obj/tiles/character_id.o` |
|---|---|
| `touch src/character_id.cpp` した直後 | **1**（再ビルド必要） |
| `ci/link.sh` の手順 3 を通した後 | **0**（最新） |

**対策と、その順序**:
```
1. PCH を touch
2. sleep 1          ← mtime 粒度が 1 秒の FS で同時刻になるのを回避
3. 全 .o を touch
```
PCH → `.o` の順でなければならない（`.o` は PCH に依存しているので、
PCH が `.o` より新しいとやはり再コンパイル対象になる）。

**正当性の根拠**: 全シャードが同一コミット・同一パッチ・同一 make 変数で
コンパイルしている（`apply-patches.sh` / `make-args.sh` で強制）ので、
復元した `.o` はそのソースから作られた正しい成果物である。

### F-21-7. アーティファクトの展開先（公式仕様で確認）

`actions/upload-artifact` は **単一ディレクトリを `path` に指定すると、
そのディレクトリの「中身」がアーティファクトの root になる**。

`path: cdda/obj/` でアップロードすると、中身は
```
tiles/achievement.o
tiles/action.o
```
であり、**先頭に `obj/` は付かない**。

| 展開先の指定 | 結果 | 判定 |
|---|---|---|
| `path: cdda/` | `cdda/tiles/*.o` | **誤**（Makefile が探す場所と違う） |
| `path: cdda/obj/` | `cdda/obj/tiles/*.o` | 正 |

出典: `actions/upload-artifact` 公式 README
（"the least common ancestor of all the search paths will be used as
the root directory of the artifact"）。

→ リンク前に配置を検証するステップを入れてある（`find obj -name '*.o' | wc -l`）。

### F-21-8. ccache の効果（実測）

| 実行 | コンパイル所要 | ccache |
|---|---|---|
| 1 回目（キャッシュ無し） | **7 秒** | 0/3 ヒット |
| 2 回目（`.o` を消して再実行） | **0 秒** | 2/2 ヒット |

`CCACHE=1` のとき Makefile は `CXX` の前に ccache を挟む:
```
CCACHE_SLOPPINESS=pch_defines,time_macros,include_file_ctime,include_file_mtime ccache emcc
```

**キャッシュキーの設計**:
- `patches/*.patch` と `ci/*.sh` のハッシュを含める
  （パッチが変われば `.o` も変わるため）
- `github.run_id` を含める
  （`actions/cache` は**同一キーへの上書きを許さない**ので、固定キーだと
  「最初に保存されたキャッシュが永久に使われ、以降育たない」状態になる）
- `restore-keys` で前回のものを引き継ぐ

**注意**: `CCACHE` の値は OBJS の中身を変えない（`CXX` の前置きだけ）ので、
`print-OBJS` の結果には影響しない。

### F-21-9. 進捗バーは「生成済み `.o` の実在数」で数える

**採用しなかった方法とその理由**:

| 方法 | 却下理由 |
|---|---|
| make の出力行を数える | `-j4` で行が混ざる。ccache ヒット時は行が出ないことがある |
| `make -n` で事前に総数を得る | 増分ビルドでは分母が変わり、実行間の比較ができない |
| Makefile に自前カウンタを追加 | 上流との差分が増えてパッチの保守性が落ちる |

**採用**: 分母 = シャードの担当件数（既知・固定）、
分子 = そのうち実在するファイル数。
make の出力形式・並列度・ccache のヒット率に**一切依存しない**。

**実装上の落とし穴（実測で踏んだ）**:

```bash
# 誤: 呼び出し側の set -o pipefail に巻き込まれる
tr '\n' '\0' < list | xargs -0 -r ls -d 2>/dev/null | wc -l
```
存在しないファイルで `ls` が失敗 → `xargs` が 123 を返す →
パイプライン全体が失敗 → 呼び出し側の `|| echo 0` が発動して
出力済みの数値の**後ろに 0 が連結**され `"10\n0"` になる →
以降の算術式が `integer expression expected` で全滅した。

```bash
# 正: bash の [ -f ] は組み込みで fork しない
while IFS= read -r path; do
    [ -f "$path" ] && n=$(( n + 1 ))
done < "$list"
```
440 回回しても fork は 0 回で、外部プロセス 1 個の起動より速い。
パイプラインを使わないので pipefail の影響も受けない。

**GitHub Actions のログでの表示**: ログは追記専用なので `\r` での
行上書きはできない（`\r` がそのまま記録される）。一定間隔で
1 行ずつ新しいバーを出す。縦に並んだバーから速度の推移も読み取れる。

```
[PROGRESS] compile shard-2 [############------------------]  40% ( 22/ 55) 経過 3m12s 残り推定 4m48s
```

**リンクとデータ同梱は内部進捗を観測できない**ので 0% → 100% しか出ない。
その代わり止まって見えるときにメモリ状況を出し続け、
「OOM でスワップに落ちている」のか「正常に重い」のかを切り分ける。

### F-21-10. CI ロジックをスクリプトに出すと検証できる

`ci/*.sh` に出したことで**ローカルで実行して検証でき、
実際に 5 件の不具合を発見・修正できた**。
YAML の中に書いていたら、どれも実際のビルドを回すまで気づけなかった。

| 発見した不具合 | 検出方法 |
|---|---|
| 検証 grep が英語コメントを探していた（パッチが日本語に置換済み） | 新規クローンで `apply-patches.sh` 実行 |
| `cata_web::yield_now` の修飾名は定義側に現れない（`namespace` 内） | 同上 |
| 出力先の使い回しで前回の `shard-*.txt` が残り、誤って重複判定 | 6 分割 → 3 分割で再実行 |
| `pipefail` で進捗の数値が破損 | 実際に監視を動かした |
| `apply-patches.sh` の引数を `..` にしていた（正しくは `../patches`） | 実機と同じディレクトリ構成で実行 |

### F-21-11. 一般則（今後の作業のため）

1. **並列化の上限は「分割できない工程」で決まる。**
   まず依存関係を洗い、分割可能／不可能／並行可能を分類する。
   不可能な部分を「縮まない下限」として正直に認識する。
2. **ビルド構成の情報源は常にビルドシステム自身に聞く。**
   `ls` や勘で列挙すると、条件分岐で増減する要素を取りこぼす。
3. **「遅いだけで成功する」壊れ方を最優先で疑う。**
   タイムスタンプ不整合はこの典型。失敗してくれる不具合より危険。
4. **設定の重複は必ず片方が腐る。**
   同じ値を 2 か所以上に書くくらいなら 1 か所に集約して参照させる。
5. **CI ロジックは YAML ではなくスクリプトに書く。**
   ローカルで実行できれば、実際のビルドを回す前に不具合を潰せる。

---

## F-22. リンク段をさらに速くするために実測した事実（2026-09-04）

### F-22-0. 動機：実際のログで「40 分以上 1/2 のまま」だった

利用者から提示された実ログの抜粋:

```
[PROGRESS] link (単一スレッド) [------] 0% (0/2) 経過 19m01s 残り推定 計測中
[PROGRESS] link (単一スレッド) [###---] 50% (1/2) 経過 19m31s 残り推定 19m31s
   ... （以降 40 分以上ずっと 50% のまま）
[PROGRESS] link (単一スレッド) [###---] 50% (1/2) 経過 41m31s 残り推定 41m31s
```

このログから**測定なしで読み取れる事実**が 2 つある。

1. **`.wasm` は 19 分で出ている。`.js` が出ないまま 22 分以上経過した。**
   分母 2 の内訳は `.wasm` と `.js` なので、`0/2 → 1/2` の遷移は
   `.wasm` の出現を意味する。つまり内訳は
   **wasm-ld までで約 19 分、その後の post-link で 22 分以上**。
   → **時間を食っているのは post-link（finalize + wasm-opt + acorn）側**である。
2. **メモリが 2.9GB → 7.3GB に跳ねた時点が post-link の本体。**
   ログの 34m 付近で 3.4GB→4.0GB→5.9GB→7.3GB と急増している。
   これは `wasm-opt` がモジュール全体をメモリに載せた瞬間である。
   7.3GB 消費するので、**16GB runner でなければ OOM する**。

### F-22-1.【この節は F-23 で撤回・訂正済み】リンクの `-Os` を `-O2` にすると 41% 速い（実測）

> **⚠️ 撤回**: 本節の数値（41%、`-O1` はサイズ 18 倍）は**いずれも誤り**である。
> 246KB の玩具モジュールで測ったため、短縮率も副作用も両方まちがえた。
> 本番規模で測り直した正しい値は **F-23** にある
> （`-O2` は **20%** 短縮、`-O1` のサイズ増は **+2.6%** にすぎない）。
> 結論の方向（`-O2` を採る）だけは偶然合っていたが、根拠は無効である。

`emcc` リンク全体（`-sASYNCIFY` 付き）を 3 回平均で測った。

| リンク最適化 | 平均所要 | 生成 wasm | 対 `-Os` 時間 | 対 `-Os` サイズ |
|---|---|---|---|---|
| `-O1` | 0.903s | 749,455 B | 55% | **+1762%**（最適化されない） |
| **`-O2`** | **0.977s** | **40,607 B** | **59%** | **+0.9%** |
| `-Os` | 1.656s | 40,260 B | 100%（現状） | 100% |
| `-O3` | 1.693s | 40,260 B | 102% | 100% |

→ **`-O2` は `-Os` より 41% 速く、サイズ増はわずか 0.9%。**

`-O1` は速いがサイズが 18 倍に膨れるので**却下**
（4GB RAM 機で読み込めなくなる = 利用者の要件を壊す）。
`-O3` は `-Os` より遅くサイズも同じなので**選ぶ理由がない**。

**なぜ `-O2` と `-Os` でこれだけ違うのか**:
`-Os`/`-O3` は Binaryen のパスパイプラインが長く、
特にサイズ削減系のパスを収束するまで反復する。
`-O2` はその反復を打ち切る。CDDA のような大きなモジュールでは
この反復コストが支配的になる。

**注意（測定の落とし穴）**: `wasm-opt` 単体に `--asyncify -Os` を
かけて測ろうとすると、既に asyncify 済みの wasm には
`Fatal: Module::addExport: asyncify_start_unwind already exists`
で**即座に失敗する**。失敗しても `time` は時間を返すので、
「-O2 と -Os は同じ速さ」という誤った結論が出る。
必ず **exit code と出力ファイルの存在**を確認すること。
（実際に一度この罠に落ちた。素の wasm に対して測り直した。）

### F-22-2. `EMCC_CORES` は `wasm-opt` には効かない（ソースで確定）

`EMCC_CORES` を増やせばリンクが速くなるのではと考えたが、**効かない**。

```
$ grep -rn "EMCC_CORES" upstream/emscripten/tools/*.py
tools/shared.py:136:  return int(os.environ.get('EMCC_CORES', os.cpu_count()))

$ grep -rn "get_num_cores" upstream/emscripten/tools/*.py
tools/js_optimizer.py:247:  intended_num_chunks = round(shared.get_num_cores() * NUM_CHUNKS_PER_CORE)
tools/shared.py:196:        num_parallel_processes = get_num_cores()
tools/system_libs.py:144:  cmd = ['ninja', '-C', build_dir, f'-j{...}']
tools/system_libs.py:545:  chunk_size = max(1, len(objects) // (2 * ...))
```

`EMCC_CORES` の参照先は
**(a) JS オプティマイザのチャンク分割、(b) システムライブラリのビルド並列度**
の 2 つだけである。`wasm-opt` の起動時に
スレッド数を渡している箇所は `tools/building.py` に存在しない
（`grep -n "thread" tools/building.py` の結果は pthread 関連 2 件のみ）。

`wasm-opt` は自前で `std::thread::hardware_concurrency()` 相当を見るので、
**runner のコア数（4）がそのまま使われる**。
外から増やす手段はない（そもそも F-21-1 のとおりスケールしない）。

なお `BINARYEN_CORES=1` は効く（上の実測で確認）が、
これは**減らす**方向の環境変数なので高速化には使えない。

### F-22-3. リンクの内部フェーズは外から観測できる（進捗バーの解）

「リンクの内部進捗は観測できない」というのも**誤りだった**。
2 つの方法で観測できる。

#### 方法 A: `/proc/PID/task/PID/children` を再帰的に辿る（採用）

`emcc` は Python プロセスで、内部で各ツールを**順番に子プロセスとして起動**する。
子孫の `/proc/PID/comm` を読めば、いま何をしているかが分かる。

```bash
$ emcc -Os -sASYNCIFY t.cpp -o out.js &
$ # 子孫を再帰的に辿って comm を読む
  [tick 1] python3          ← emcc 本体
  [tick 4] MainThread
  [tick 6] wasm-opt         ← いま wasm-opt を実行中
```

**注意**: `pgrep -f "wasm-opt|wasm-ld"` で探すのは**間違い**。
自分自身のコマンドラインがパターンに一致して誤検出する
（実際に「全工程を同時に検出」という不可能な結果が出た）。
必ず `/proc/PID/comm`（実行ファイル名そのもの）を見ること。

#### 方法 B: `EMCC_DEBUG=2` が番号付き中間ファイルを落とす

```
$ EMCC_DEBUG=2 emcc -Os -sASYNCIFY t.cpp -o out.js
$ ls /tmp/emscripten_temp/
emcc-0-base.wasm                      ← wasm-ld 直後
emcc-1-strip.wasm
emcc-2-wasm-emscripten-finalize.wasm
emcc-3-post_finalize.wasm
emcc-4-original.js
emcc-5-wasm-opt.wasm                  ← 1 回目の wasm-opt
emcc-6-byn.wasm
emcc-7-preclean.js
emcc-8-preclean.wasm
emcc-9-AJSDCE.js
emcc-10-wasm-metadce.wasm             ← metadce
emcc-11-applyDCEGraphRemovals.js
emcc-12-AJSDCE.js
emcc-13-wasm-opt.wasm                 ← 2 回目の wasm-opt
emcc-14-applyImportAndExportNameChanges.js
emcc-15-postclean.js
emcc-16-postclean.wasm                ← 完成
```

**全 17 フェーズが番号順に確定している。**
ファイルの出現を数えれば `n/17` の進捗が取れる。

ただし `building.py:1307` の `save_intermediate` は
`shutil.copyfile` で**毎フェーズ丸ごとコピーする**。
CDDA の wasm は 40MB 級なので 17 回 × 40MB のコピーが増える。
**本番では方法 A を使い、方法 B は調査時のみ**とする。

#### リンクが起動するツールの順序（`emcc -v` で確定）

```
wasm-ld → wasm-emscripten-finalize → wasm-opt
→ acorn-optimizer.mjs ×3 → wasm-opt → acorn-optimizer.mjs
```

### F-22-4. `$GITHUB_STEP_SUMMARY` はジョブを越えて共有されない（公式仕様）

進捗が読みにくかった第 2 の原因。公式ドキュメントの記述:

- 「GITHUB_STEP_SUMMARY is unique for each step in a job」
- 「Job summaries are isolated between steps and each step is
  restricted to a maximum size of 1MiB」
- 「When a job finishes, the summaries for all steps in a job are
  grouped together into a single job summary…
  **If multiple jobs generate summaries, the job summaries are
  ordered by job completion time**」
- 「A maximum of 20 job summaries from steps are displayed per job」

**現状の実装は壊れている**:
`manual/build-and-release.yml` の `plan` ジョブで
Markdown の表ヘッダ（`| 工程 | 対象 | 所要 | 成果物 |`）を書き、
`compile`/`link`/`data`/`bundle` の各ジョブが行だけを追記していた。

ジョブを越えて共有されないので、
**ヘッダの無い `| … |` 行が、しかも完了時刻順（＝不定順）に並ぶ**。
表として描画されない。

→ **各ジョブが自分でヘッダを含む完結したブロックを書く**しかない。

### F-22-5. 一般則（今回得た教訓）

1. **「分割できない」と「単一スレッド」を混同しない。**
   前者はプロセス境界の話、後者はプロセス内の話。両立する。
2. **並列性の主張は `user`/`real` 比で裏を取る。**
   `user > real` なら複数コアを使っている。それ以外は推測にすぎない。
3. **マルチスレッドでも、大域解析が支配的ならコアは効かない。**
   コアを足す前にアムダールの法則で上限を見積もる。
4. **時間を測るときは exit code と出力の存在を必ず確認する。**
   失敗した実行も `time` は時間を返す。速く見えるのは失敗しているだけ。
5. **プロセスを名前で探すときは `/proc/PID/comm` を使う。**
   `pgrep -f` は自分自身に一致して誤検出する。
6. **進捗の分母は「利用者が待つ時間」に比例させる。**
   分母 2（`.js`/`.wasm`）は、40 分のうち 22 分を 1 つの目盛りに
   押し込むので情報量がほぼゼロだった。

---

## F-23. リンク 4 時間の内訳を本番ログで実測し、F-22-1 を訂正した（2026-09-04）

**要求（主題）**: 「CDDA の改良を行うにあたって、Action が長すぎる。
これを高速化できないか。そしてその上で改善もできるようにしてほしい」

つまり求められているのは *1 回のビルドを短くすること* だけではなく、
**「直して → 焼いて → 遊んで確かめる」を回せる状態にすること**である。
4 時間待たされる限り、改良は事実上 1 日 1 回しか試せない。

### F-23-1.【決定的】4h23m のうち wasm-opt 単独で 4h02m（93%）

成功実行 33730439512（合計 4h23m）のログには `ps` の定期出力が
残っていたので、**2218 サンプル**を集計した。

| プロセス | 累計時間 | 全体比 |
|---|---|---|
| **`wasm-opt`** | **4h02m** | **93%** |
| `clang` (440 TU) | 約 15m | 6% |
| `node` (acorn 等) | 数分 | 1% |

→ **「ビルドが 4 時間」は、実質「wasm-opt が 4 時間」である。**
他をいくら削っても最大 7% しか縮まない。

根拠ログ: `docs/measurements/raw/2026-09-04-link-breakdown-real.log`

### F-23-2. コア増設・メモリ増設は【効かない】（実測で棄却）

同じ 2218 サンプルから、wasm-opt 実行中の資源使用を取った。

| 指標 | 実測 | 上限 | 判定 |
|---|---|---|---|
| CPU% | 平均 105 / 最大 112 / 最小 103 | 400（4 vCPU） | **1.05 コアしか使っていない** |
| RSS | 最大 6.46 GB | 16 GB | 余っている |
| 起動回数 | 1 回 | — | 分割の余地なし |

F-21-1 は「マルチスレッドだがスケールしない」と書いていたが、
本番規模では**そもそも並列に走っていない**（1.05 コア）。
小さいモジュールでは関数並列パスが効いて `user/real = 1.59` になったが、
本番では大域解析部分が支配して並列部分が埋もれる。

→ ランナーを 4 コアから 16 コアにしても、メモリを増やしても、
wasm-opt を多重起動しても**無意味**。
残る手は「やらせる仕事の量を減らす」ことだけである。

### F-23-3.【真犯人】遅いのは Asyncify ではなく `inlining-optimizing`

`BINARYEN_PASS_DEBUG=1` を付けると
`[PassRunner] running pass: NAME... N seconds` がパス単位で出る。
本番と同じ引数・現実的な規模（N=1000, `-Os`）で測った内訳:

| パス | 所要 | 比率 |
|---|---|---|
| **`inlining-optimizing`** | **12.422s** | **71%** |
| `asyncify` | 2.747s | 16% |
| その他 31 パス | 2.266s | 13% |

→ **本プロジェクトが長らく書いてきた「Asyncify の大域解析が支配的」は誤り。**
支配しているのは**インライン展開**である。
インライン展開は最適化レベルで直接制御できるので、
「リンク段は原理的に縮まない」という結論も同時に崩れる。

### F-23-4. 本番規模で測り直した最適化レベル（F-22-1 の訂正値）

本番の wasm-opt 引数をそのまま使い、規模を 2 段階で測って線形性を確認した。

| レベル | N=1000 (438KB) | N=2000 (868KB) | 対 `-Os` 時間 | 対 `-Os` サイズ |
|---|---|---|---|---|
| `-Os`（従来） | 17.457s / 435,596 B | 34.634s / 862,809 B | 100% | 100% |
| **`-O2`**（配信用） | 13.993s / 447,282 B | 27.912s / 886,119 B | **80%（-20%）** | **+2.7%** |
| **`-O1`**（検証用） | **3.817s** / 446,905 B | **7.759s** / 884,835 B | **22%（-78%）** | **+2.6%** |

時間もサイズも N に対してほぼ線形なので、本番（4h02m）に外挿すると:

| レベル | リンク段の見込み | 備考 |
|---|---|---|
| `-Os` | 4h02m | 従来 |
| `-O2` | 約 3h14m | 既定。配信用 |
| **`-O1`** | **約 53 分** | 検証用。改良サイクルはこれで回す |

**`-O1` のサイズ増はわずか +2.6%** であり、
F-22-1 が書いた「18 倍に膨れるので却下」は完全な誤りだった。
4GB RAM 機の要件を壊さないので、検証用として採用できる。

根拠ログ: `docs/measurements/raw/2026-09-04-wasm-opt-scaling.log`

### F-23-5. 【反省】なぜ 2 回続けて数値をまちがえたのか

F-22-1 の測定は **246KB の合成 wasm** に対して行っていた。
本番の wasm はその桁が違う。その結果:

- 短縮率を **41% → 実際は 20%** と過大評価した
- `-O1` の副作用を **18 倍 → 実際は +2.6%** と過大評価した

つまり**短縮率も副作用も、両方まちがえた**。
方向（`-O2` にする）が偶然合っていたので気づかなかった。

**教訓**: 最適化の測定は**本番規模でやらないと、
短縮率も副作用も両方まちがえる**。
規模が変わるとパスの支配関係そのものが入れ替わる
（小規模では関数並列が効き、大規模ではインライン展開が支配する）。
「小さい例で傾向は分かる」は最適化の測定では成り立たない。

### F-23-6. 採った対策（二段構え）

| 用途 | `link_opt` | リンク段 | 位置づけ |
|---|---|---|---|
| 配信 | `O2`（既定） | 約 3h14m | 公開する版を焼くとき |
| 検証 | `O1` | **約 53 分** | 改良して確かめるとき |

実装:

- `ci/tune-makefile.sh` が環境変数 `CDDA_LINK_OPT`（既定 `O2`、`O1` 可、
  それ以外は即エラー）で Makefile の RELEASE 分岐を書き換える
- `manual/build-and-release.yml` に選択入力 `link_opt` を追加し、
  `tune-makefile.sh` を呼ぶ 3 箇所すべてと `link.sh` に同じ値を渡す
- `ci/link.sh` の想定所要時間もレベル別（O1=53分 / O2=194分 / Os=242分）に変更。
  従来は 45 分固定で、実測 4h02m と 5 倍以上ずれており
  「想定超過」を出し続けるだけの無意味な表示だった

### F-23-7.【重要】`Makefile` の `-O` 行は 2 つある（実装中に踏んだ罠）

CDDA の Makefile には最適化フラグ行が **2 つ**ある。

```
699 行  LDFLAGS += -Os                                            ← RELEASE=1（置換対象）
703 行  LDFLAGS += -O1 # Emscripten link time is slow, so use ...  ← デバッグ分岐（触ってはいけない）
```

`CDDA_LINK_OPT=O1` を実装したとき、素朴に `grep -- '-O1'` したため
**703 行に誤ヒット**し、「すでに O1 です」と報告しながら
699 行は `-Os` のまま残り、直後の検証で
`ERROR: 'LDFLAGS += -Os' が残っています` で落ちた。

**対策**: `# Release-mode Linker flags.` のコメントを起点に `awk` で
RELEASE 分岐の行番号を特定し、`sed` を**その行番号に限定**して適用する。

```bash
release_opt_line="$(
    awk '/# Release-mode Linker flags\./ { inrel = 1; next }
         inrel && /^[[:space:]]*LDFLAGS \+= -O/ { print NR; exit }
         inrel && /^[[:space:]]*else/ { exit }' Makefile
)"
```

検証: 各レベル 3 回連続実行して冪等、703 行は不変、
`O3`/`Os` など不正値は exit 1。

### F-23-8. 一般則（今回得た教訓）

1. **最適化の測定は本番規模で行う。**
   小規模での測定は短縮率も副作用も、そして
   *どのパスが支配的か*さえまちがえる。
2. **「並列化できない」の理由を推測で書かない。**
   `BINARYEN_PASS_DEBUG=1` のようにパス単位で測る手段があるなら、
   犯人を名指しできるまで測る。本件は 3 年ぶんの記述が
   間違った容疑者（Asyncify）を指していた。
3. **資源を増やす前に「使っているか」を見る。**
   4 コア中 1.05 コアなら、増設は 1 円も効かない。
   CPU% を実測してから増設を検討する。
4. **`sed` でフラグを書き換えるときは行を特定してから。**
   同じ文字列が別の意味で複数箇所に出るのが普通である。
5. **「速くする」の目的は 1 回の短縮ではなく反復回数である。**
   4 時間なら 1 日 1 回、53 分なら 1 日 8 回試せる。
   改良の速さは後者に比例する。

---

## F-24. リンク結果キャッシュ（改良サイクルを回すための本命）（2026-09-04）

### F-24-1. 動機：最適化レベルだけでは「改善できるように」に届かない

F-23 で `-O1`（約 53 分）を用意したが、これでも
「直して → 焼いて → 遊ぶ」を 1 日に何度も回すには重い。
そして実際の改良作業では、**C++ を 1 行も触らない変更**が頻繁に起きる。

- `data/` や `gfx/` だけ直した
- MOD を足した
- HTML / JS ラッパだけ直した
- ワークフローの設定だけ直した

この場合 `.o` は全部 ccache から即座に復元されるので、
**残るのは wasm-opt の 4 時間だけ**という最悪の形になる。
「1 文字も C++ を変えていないのに 4 時間待つ」状態である。

### F-24-2. 原理：wasm-opt は入力が同じなら出力も同じ

wasm-opt の入力は
**「440 個の `.o`」＋「リンク引数」**だけである。
外部の状態（時刻・乱数・ネットワーク）に依存しない決定的な変換なので、
入力が同一なら出力（`.js` / `.wasm`）はビット単位で同一になる。

→ 入力の指紋が前回と一致したら、**4 時間まるごと省略できる**。

### F-24-3. 指紋に入れたもの（`ci/link-fingerprint.sh`）

| # | 対象 | 入れる理由 |
|---|---|---|
| 1 | 全 `.o` の**内容**ハッシュ | C++ の変更が反映される |
| 2 | `CDDA_MAKE_ARGS`（make 変数） | `RELEASE` / `TILES` / `LOCALIZE` などが変われば出力が変わる |
| 3 | `CDDA_LINK_OPT`（O2 / O1） | 最適化レベルが変われば出力が変わる |
| 4 | `Makefile` 自体 | `LDFLAGS` の変更が反映される |
| 5 | `ci/*.sh` | リンク手順そのものの変更 |
| 6 | `emcc --version` | Binaryen が変われば出力が変わる |

**設計の原則**: 入れ忘れは「変更したのに古い wasm が配られる」
という**最も気づきにくい事故**になる。逆に余計なものを入れても
「当たるべきキャッシュが当たらない」だけで正しさは壊れない。
よって迷ったら入れる。4・5 は厳密には過剰だが過剰側に倒した。

### F-24-4.【重要】ファイル名ではなく内容、そして mtime に依存しない

- **ファイル名・個数だけでは駄目**: 同じ名前で中身が変わる
  （＝ C++ を編集した通常のケース）を検出できない
- **mtime を入れては駄目**: アーティファクトから復元した `.o` の
  mtime は毎回変わる（F-21-6 で既知）。mtime を含めると
  キャッシュが**一度も当たらない**
- **`sort` が必須**: `find` の返す順序はファイルシステム依存で、
  同じ内容でも実行ごとに変わりうる。ソートしないと毎回キーが変わる

検証済み（`/tmp/fptest` にて）:

| 条件 | 期待 | 結果 |
|---|---|---|
| 同一入力で 2 回 | 同じ指紋 | PASS |
| `.o` の内容を変更 | 指紋が変わる | PASS |
| `.o` を同内容で書き直し（mtime だけ変化） | 同じ指紋 | PASS |
| `CDDA_LINK_OPT=O1` に変更 | 指紋が変わる | PASS |
| `Makefile` を変更 | 指紋が変わる | PASS |
| `.o` が 1 個欠落 | 指紋を無効化 | PASS |
| 一覧が空 | 指紋を無効化 | PASS |

### F-24-5. `restore-keys` を意図的に置かない

`actions/cache` の `restore-keys` は前方一致で古いキャッシュを拾う機能で、
ccache では有用（部分的に古くても得がある）だが、
**リンク結果では絶対に使ってはいけない**。

入力が違う `.wasm` を拾ってしまうと、
**直したはずの変更が反映されないまま公開される**。
しかも動くので誰も気づかない。完全一致のみを許す。

### F-24-6. `pipefail` の罠（実装中に踏んだ）

指紋計算で `.o` の実在数を数えるとき、最初はこう書いた。

```bash
found_count="$(
    while IFS= read -r o; do
        [ -f "$o" ] && echo x
    done < "$objs_list" | wc -l
)"
```

これは `set -o pipefail` 下で、**一覧の最後の `.o` が存在しない場合に
while ループが終了ステータス 1 を返す**ため、
コマンド置換ごと失敗して `set -e` でスクリプトが無言死する。

症状は「`.o` 欠落のテストで出力が空、exit 0」だった
（`echo` に到達せず、しかしコマンド置換の失敗は
`$?` に現れない位置だったため一見成功に見えた）。

**対策**: `&&` の短絡ではなく `if` 文にしてループの終了状態を 0 に保つ。

```bash
        if [ -f "$o" ]; then
            echo x
        fi
```

**一般則**: `set -o pipefail` 下で `while ... done | cmd` を
コマンド置換に入れるときは、ループ本体の最後のコマンドの
終了ステータスがそのままループの終了ステータスになることを忘れない。
`[ ... ] && echo` は条件が偽のとき 1 を返す。

### F-24-7. キャッシュに当たっても成果物を検証する

「キャッシュに当たった」と「ファイルが揃っている」は別の話である。
`actions/cache` が部分的に壊れた場合や、
パス指定を間違えた場合に空ファイルを配りうる。

そこでキャッシュ経路でも必ず

```bash
for f in cataclysm-tiles.js cataclysm-tiles.wasm; do
  [ -s "$f" ] || { echo "ERROR: ${f} がありません" >&2; exit 1; }
done
```

を通す。`-s`（サイズが 0 でない）まで見るのが要点で、
`-f`（存在する）だけでは空ファイルを通してしまう。

### F-24-8. 期待される効果

| 変更の種類 | 従来 | 対策後 |
|---|---|---|
| C++ を変更（-O2） | 4h23m | 約 3h30m |
| C++ を変更（-O1、検証用） | 4h23m | **約 1h10m** |
| **C++ 以外だけ変更**（data / MOD / HTML / 設定） | 4h23m | **約 15 分** |
| 同一内容の再実行 | 4h23m | **約 15 分** |

最後の 2 行が本命である。改良作業の多くはここに該当する。

### F-24-9. リンクジョブの `timeout-minutes` を 240 → 330 に

実測 `-Os` で wasm-opt 単独 242 分。これに
`.o` のダウンロード（1GB 超）＋ emsdk 導入＋ PCH 生成が 20〜40 分乗る。
**従来の 240 分では `-Os` のとき確実に打ち切られる**（間に合っていたのは
たまたま）。Actions のジョブ上限 360 分に対し余裕を見て 330 分にした。

同時に `ci/link.sh` の「想定所要時間」を 45 分固定から
レベル別（O1=53分 / O2=194分 / Os=242分）に変更した。
従来は実測の 5 分の 1 で、「想定超過」を出し続けるだけの
情報量ゼロの表示だった。

### F-24-10. 一般則

1. **「速くする」より「やらないで済ませる」方が効く。**
   -O2 で 20%、-O1 で 78% だが、キャッシュなら **100%** である。
   最適化の前に「そもそも実行する必要があるか」を問う。
2. **決定的な変換はキャッシュできる。**
   入力を列挙できるなら指紋が作れる。
   列挙に自信が無い項目は入れる側に倒す（誤って当たるより、
   誤って外れる方が 100 倍安全）。
3. **キャッシュキーに mtime を混ぜない。**
   CI ではファイルの mtime は毎回変わる。内容で判断する。
4. **キャッシュの部分一致（`restore-keys`）は成果物には使わない。**
   「古いが動くもの」を配る事故になる。

---

## F-25. 分割ビルド初回実行の失敗解析：406 個の再コンパイルと 240 分打ち切り（2026-09-04）

### F-25-1. 何が起きたか

ユーザーが分割構成のワークフローを適用し、run 33888888789 として初めて実行された。
結果は **失敗（cancelled）** である。

| 項目 | 値 |
|---|---|
| 実行 ID | 33888888789 |
| コミット | `20f8f75`（`-O2` 化・キャッシュ導入の【前】） |
| 全体 | 4h04m54s で打ち切り |
| コンパイル 8 シャード | 全て成功（最長 6m37s） |
| リンクジョブ | 15:27:26 開始 → 19:27:47 **cancelled** |

打ち切りの直接原因は **リンクジョブの `timeout-minutes: 240`** である。
ジョブ終了時のログに決定的な証拠が残っていた:

```
Terminate orphan process: pid (6809) (wasm-opt)
```

つまり 3 時間 56 分かけても wasm-opt はまだ走っていた。
F-23-1 で「wasm-opt が 4h02m」と実測していたのだから、
240 分（4h00m）の上限では **原理的に間に合わない**。
この点は既に F-24-9 で 330 分へ引き上げ済みだったが、
ユーザーが適用したのは引き上げ前の版だったため打ち切られた。

### F-25-2. 【本題】リンクジョブが 406 個を再コンパイルしていた

より重要な発見はこちらである。ログにこう出ていた:

```
WARNING: 標本 5 個のうち 5 個が「再ビルド必要」と判定されました:
  obj/tiles/achievement.o
  ...
         リンク時に再コンパイルが走り、分割の効果が失われます。
```

そして実際に **406 回のコンパイルコマンド**が流れた。

| 項目 | 値 |
|---|---|
| `.o` 総数 | 440 |
| リンクジョブで再コンパイルされた数 | **406** |
| 再コンパイルされなかった数 | 34（すべて `src/third-party/`） |
| 浪費した時間 | 15:31:00 → 15:50:01 = **約 19 分** |

`ci/link.sh` は「タイムスタンプ整合」を行っており、
`PCH → .o` の順に `touch` していた。それでも防げていなかった。

### F-25-3. 原因：`.d` に焼き込まれた emsdk の絶対パスが、ジョブごとに変わる

Makefile 末尾には依存ファイルの取り込みがある:

```make
-include ${OBJS:.o=.d}
```

`.d` はコンパイル時に `-MMD -MP` で生成され、中身は **絶対パス** である:

```
pch/main-pch.hpp.gch: pch/main-pch.hpp \
  /home/runner/work/_temp/<UUID>/emsdk-main/upstream/emscripten/
    cache/sysroot/include/SDL2/SDL.h ...
```

ここで `mymindstorm/setup-emsdk@v14` は
**ジョブごとにランダムな UUID のディレクトリへ emsdk を展開する**。
run 33888888789 の 9 ジョブを実際に調べたところ、全部違った:

| ジョブ | emsdk の展開先 |
|---|---|
| コンパイル shard-0 | `_temp/e7b82ce1-bf6e-493f-a4e9-96802d360006/emsdk-main` |
| コンパイル shard-1 | `_temp/17ed1ac2-9038-4084-a63c-4026e5685b79/emsdk-main` |
| コンパイル shard-2 | `_temp/04bcd337-f759-4e9f-af91-7b774bef284a/emsdk-main` |
| コンパイル shard-3 | `_temp/192c5b91-5377-4e4b-acf7-4926b3f1c112/emsdk-main` |
| コンパイル shard-4 | `_temp/a5ee7ab2-61c1-4608-9169-7aa5ab45b726/emsdk-main` |
| コンパイル shard-5 | `_temp/729671f5-af62-4590-a651-5265041f6eba/emsdk-main` |
| コンパイル shard-6 | `_temp/1d4b70d4-ea83-4f65-b9d7-54fe43816593/emsdk-main` |
| コンパイル shard-7 | `_temp/d6cd496f-7401-46c2-a423-d69263bc2035/emsdk-main` |
| **リンク** | `_temp/927d2ddc-82ea-43c1-815f-3915d69bde6a/emsdk-main` |

したがってリンクジョブから見ると、`.d` が指すヘッダは **存在しない**。
make は「前提ファイルが存在しない」ターゲットを常に「更新が必要」と扱う。
**mtime をどう `touch` してもこの判定は覆せない**。
`ci/link.sh` のタイムスタンプ整合が効かなかったのはこのためである。

### F-25-4. 引き金は 2 つある（最小再現で確認）

`/tmp` に最小の Makefile を作って切り分けた。
次の 2 つが **それぞれ単独でも** 再コンパイルを起こす。

| 機構 | 内容 | 単独で再現したか |
|---|---|---|
| **機構A** | PCH の `.d` が壊れている → PCH が作り直される → PCH に依存する `.o` が巻き添え | **した** |
| **機構B** | 各 `.o` 自身の `.d` が壊れている → その `.o` が直接古いと判定される | **した** |

本番ではこの両方が同時に成立していた。
つまり「PCH だけ直せばよい」は誤りで、**すべての `.d`** を処理する必要がある。

機構Aの再現ログ（要点）:

```
=== 全て最新のはずの状態で make を実行 ===
  ==> PCH 再生成
  ==> obj/a.o 再コンパイル (PCH 依存)
--- make -q obj/a.o ---  exit=1   ← 古いと判定
--- make -q obj/tp.o --- exit=0   ← PCH 非依存なので無傷
```

`make -q` の戻り値が本番ログの「標本 5 個すべて再ビルド必要」と一致する。

### F-25-5. 406 対 34 の内訳について（確定した部分と、していない部分）

**確定している**のは機構Aによる説明である。Makefile の規則から演繹できる:

```make
$(ODIR)/third-party/%.o: $(SRC_DIR)/third-party/%.cpp     # PCH に依存しない
$(ODIR)/%.o:             $(SRC_DIR)/%.cpp $(PCH_P)        # PCH に依存する
```

PCH が作り直されれば、PCH に依存する `src/*.o` だけが巻き添えになる。
これは 406 対 34 の内訳と正確に一致する。

**確定していない**のは、機構Bがなぜ third-party に及ばなかったかである。
当初この理由を「zstd / imgui は SDL を include しないから」と書いたが、
**これは誤りだった**。確認したところ `imgui.cpp` は SDL を参照している。
打ち切られた実行のログには `.d` の中身までは残っておらず、
現時点では確証を取れない。ここは「未確認」と記しておく。

ただし **対策は変わらない**。どちらの機構であっても
「`.d` を全部消す」ことで両方まとめて解消できる。

### F-25-6. 対策：リンク段では `.d` を削除する

`ci/link.sh` の手順 3 の冒頭に追加した:

```bash
dep_count=0
if [ -d "$CDDA_OBJ_DIR" ]; then
    dep_count="$( find "$CDDA_OBJ_DIR" -name '*.d' -type f | wc -l )"
    find "$CDDA_OBJ_DIR" -name '*.d' -type f -delete
fi
pch_dep_dir="$( dirname "$( make -s print-PCH_P "${CDDA_MAKE_ARGS[@]}" )" )"
if [ -d "$pch_dep_dir" ]; then
    pch_deps="$( find "$pch_dep_dir" -name '*.d' -type f | wc -l )"
    find "$pch_dep_dir" -name '*.d' -type f -delete
    dep_count=$(( dep_count + pch_deps ))
fi
echo "[LINK] .d を ${dep_count} 個削除しました（emsdk の絶対パスが無効なため）"
```

**なぜ「消す」のが正しいのか。**
`.d` の役割は「ソースを編集したとき、どの `.o` を作り直すか」を知ることである。
リンク段では `.o` は既に全部そろっていて、これから作るのはリンク成果物だけなので、
`.d` は最初から不要である。消しても成果物の正しさに影響しない。

**なぜ「パスを書き換える」ではないのか。**
`sed` で UUID を差し替える案も検討したが採らなかった:

- UUID は毎回変わるので、書き換え先を実行時に特定する必要がある
- emsdk 内部の構成が変わると静かに壊れる
- そもそも不要な情報なので、消すのが最も単純で壊れにくい

「使わない情報を正確に保つ」より「使わない情報を捨てる」ほうが安全である。

### F-25-7. 効果の検証

修正の前後を最小再現で比較した。

```
===== 修正前（.d をそのまま） =====
  ==> PCH 再生成
  ==> obj/tiles/a.o 再コンパイル
  ==> obj/tiles/b.o 再コンパイル

===== 修正後（.d を削除する） =====
make: Nothing to be done for 'all'.
```

再コンパイルが **完全にゼロ** になった。
本番では約 19 分の短縮に相当する。

### F-25-8. この失敗から得た一般的な教訓

1. **CI をまたいで `.d`（依存ファイル）を持ち回ってはいけない。**
   `-MMD` が書く絶対パスは、そのジョブのディレクトリ構成に強く依存する。
   別ジョブへ持ち込むと「存在しない前提ファイル」になり、
   **mtime をどう調整しても** 再ビルドが止まらない。

2. **`touch` による整合には限界がある。**
   make の判断材料は mtime だけではない。
   「前提ファイルが存在するか」も見ている。
   タイムスタンプ対策を入れたからといって安心してはいけない。

3. **警告を出しただけで済ませない。**
   `ci/link.sh` は「再ビルド必要」を正しく検出して警告していた。
   しかし「失敗にはしない」設計だったため、19 分を浪費しながら通り過ぎた。
   検出できているなら、原因を潰すところまでやる必要がある。
   （なお警告のままにした判断自体は妥当である。
   ここで失敗させていたらビルドは 1 回も通らなかった。）

4. **タイムアウトは実測値より十分に大きく取る。**
   wasm-opt が 4h02m と分かっていながら上限 240 分で走らせれば、
   確実に打ち切られる。実測 + 余裕を必ず確保する。

5. **「成功していた過去の実行」と構成が違うことを見落とさない。**
   4h23m で成功していた run 33730439512 は **単一ジョブ**の旧構成だった。
   分割構成での実行はこれが初回であり、比較対象として同列に扱えない。

---

## F-26. wasm-opt を Binaryen 132 に差し替えてリンクを -37.8% にした（2026-09-04）

### F-26-0. 前提と、この調査の出発点

F-23 で「Action 4h23m のうち wasm-opt 単独が 4h02m（93%）」、
さらにパス単位で「`inlining-optimizing` が 71%」と判明していた。
残った問いは **「そのパスをこれ以上速くする手はあるのか」** だった。

**測定環境の限界を先に明記する。**
- サンドボックスの `nproc` は **2**（本番ランナーは 4）。
  したがってコア数に関する結論は Amdahl の式で外挿している。
- `/tmp` は **493MB の tmpfs**。作業中に使い切って一度作業を失った。
  以降の計測物は `/home/user/bench/` と `/home/user/bn/` に置いた。

### F-26-1. 「全コアを使う」という記述と実測 1.05 コアの矛盾を解いた

web.dev の Binaryen 解説にはこう書かれている（原文）:

> "Binaryen's internal IR uses compact data structures and is designed for
>  completely parallel code generation and optimization, using all available
>  CPU cores."

しかし本番実測の CPU は平均 105% = **1.05 コア**しかなかった。
この矛盾を、版 116 のソースを直接読んで解決した。

`src/passes/Inlining.cpp` 1105 行目:

```
// perform inlinings TODO: parallelize
```

同ファイルには `isFunctionParallel() { return true; }` もある。つまり
**インライン候補の「探索」は並列だが、実際の「適用」が逐次だった**。
ドキュメントは設計思想（将来形）を述べていたのであって、
このパスの実装が追いついていなかっただけである。

**測定した並列化率（`BINARYEN_CORES` 1 と 2 の比から Amdahl 逆算）**

| 版 | 1 コア | 2 コア | 速度比 | 並列化率 p | 4 コア時の理論上限 |
|---|---|---|---|---|---|
| 116 | 7.13s | 5.82s | 1.23x | **約 37%** | 1.38x |
| 132 | 5.54s | 4.95s | 1.12x | **約 21%** | — |

⇒ **「コアを増やす」策は完全に死んだ。** 4 コアでも上限 1.38x、
実際には 1.05 コアしか使えていない。RSS も 6.46GB / 16GB で余裕がある。

### F-26-2. 【突破口】上流が、我々の版の直後にこのパスを高速化していた

方針を「フラグ調整」から
**「このホットパスに対する上流のコミット履歴を追う」** に変えた。
これが決定的だった。emsdk 3.1.51 同梱の wasm-opt は **116**（2023年12月）。

| PR | 日付 | 題目 | 報告された効果 |
|---|---|---|---|
| #6966 | 2024-09-24 | Parallelize the actual inlining part of the Inlining pass | 「makes the pass **over 2x faster**」 |
| #6967 | 2024-09-24 | Avoid repeated ReFinalize etc. when inlining | 「a **5x speedup** on a large real-world wasm file」 |
| #6969 | 2024-09-26 | Stop creating unneeded blocks around calls when inlining | — |
| #7669 | 2025-06-30 | Always inline trivial calls that always shrink | — |
| #7670 | 2025-07-07 | Generalize "trivial call" flag to other simple instructions | — |
| #7820 | 2025-08-18 | Inlining: Make MaxCombinedBinarySize configurable | — |

PR 本文の引用:

> #6966: "the actual inlining - copying the code into the target function -
>  was done sequentially. It turns out that a lot of work happens there:
>  this PR makes the pass over 2x faster."

> #6967: "This turns out to be very important, a 5x speedup on a large
>  real-world wasm file I am looking at. ... in one case we inline over
>  1,000 times into a function!"

つまり **我々のボトルネックそのものが、我々の版の 9 か月後に修正されていた**。
#6966 が F-26-1 の `TODO: parallelize` を解消したものである。

### F-26-3. 実測（本番と同一引数）

入力は本番ログから採取した実際の wasm-opt 引数を使用。
N=4000 = 8579 関数 / 入力 1.89MB。**3 回反復**。

| 設定 | r1 | r2 | r3 | 平均 | 出力サイズ |
|---|---|---|---|---|---|
| 116 `-Os`（従来） | 6.163s | 5.712s | 5.739s | **5.871s** | 771,797 B |
| **132 `-O2`（採用）** | 3.742s | 3.771s | 3.436s | **3.650s** | 772,860 B |
| 132 `-O2` + oc20 | 3.188s | 3.494s | 3.589s | 3.424s | 775,875 B |

⇒ **-37.8%。しかも出力サイズは +0.14% しか増えない。**

**効果は規模が大きいほど伸びる**（`-Os` 同条件で 116→132）:

| 規模 | 116 | 132 | 差 |
|---|---|---|---|
| N=1000 | 2.163s | 2.213s | +2%（小さすぎて利かない） |
| N=2000 | 3.211s | 2.930s | -9% |
| N=4000 | 5.845s | 4.394s | **-25%** |

本番は 8579 関数どころではないので、**-37.8% は下限**と考えてよい。

`inlining-optimizing` 単独（`BINARYEN_PASS_DEBUG=1`）でも
1.055→0.772 / 1.922→1.511 / 3.849→2.990（-21〜-27%）と確認した。

### F-26-4. 【最重要】版 120 は 116 より遅い

| 版 | 実測（2 回） |
|---|---|
| 116 | 9.216s / 9.090s |
| **120** | **13.113s / 12.772s** ← 明確に遅い |

120 を試した時点では「新しくすると **遅くなる**」という結果だった。
**もし 120 だけを試して打ち切っていたら「更新は無意味」と
誤って結論していた。** 版を上げるときは必ず複数版を実測すること。
「新しい = 速い」は成り立たない。

### F-26-5. 【致命的な落とし穴】132 は `-Os` / `-O3` / `-Oz` で壊れる

132 に差し替えて `-Os` でリンクすると、**ビルドは成功するのに
実行時に**こう落ちる:

```
failed to asynchronously prepare wasm: LinkError:
WebAssembly.instantiate(): Import #0 "a" "a":
function import requires a callable
```

**切り分け手順**（推測で終わらせず特定した）:
1. 116 に戻して同一フラグ → 動く ⇒ **132 起因で確定**
2. `-sASYNCIFY` / `-sWASM_BIGINT` / `-lembind` / `-sLZ4` /
   `-sFORCE_FILESYSTEM` を個別に外す → いずれも無関係
   ⇒ **最適化レベルだけが要因**

原因は import 名の短縮（minify）が emscripten 3.1.51 の
JS グルー側と食い違うこと。

**正当性検証**（Asyncify・仮想呼び出し・インライン候補を含む
決定論的テスト。期待値 `SUM=10650`）:

| 組み合わせ | 結果 |
|---|---|
| 116 `-Os` / `-O2` / `-O1` | すべて SUM=10650 |
| **132 `-O2`** | SUM=10650 **安全** |
| **132 `-O1`** | SUM=10650 **安全** |
| 132 `-Os` | 上記 LinkError **使用禁止** |

**`-O1` も安全であることを確認したのが重要。**
`-O1` は検証用モード（約53分）として実際に使うので、
ここが壊れていたら「検証用ビルドだけ起動しない」事故になっていた。

**なぜ終了コードで守れないのか。**
破損ビルドでも `em++` の終了コードは **0** である。
壊れるのは wasm を instantiate する時点なので、CI は緑になる。
そのため `ci/tune-makefile.sh` で
**`Os` / `O3` / `Oz` を入口で拒否する**実装にした。

### F-26-6. `-ocimfs` は既定にしない（差が誤差に埋もれる）

`--one-caller-inline-max-function-size=20` の効果:

| 指標 | 132 `-O2` | 132 `-O2` + oc20 |
|---|---|---|
| 平均時間 | 3.650s | 3.424s（-6%） |
| 実測レンジ | 3.436〜3.771 | 3.188〜3.589 **重なる** |
| raw サイズ | 772,860 B | 775,875 B（+0.4%） |
| gzip サイズ | 62,063 B | 61,886 B |

レンジが重なるので **-6% は誤差と区別できない**。
サイズは増えるため、既定にはせず任意とした。

**emcc は `-ocimfs` を受け付けない（静かに無視される罠）。**
`em++ -O2 -ocimfs 20` は
`em++: error: 20: No such file or directory` を出しつつ
**ビルドは素の `-O2` として成功する**。
`tools/link.py` の `get_binaryen_passes` にフックが無いためである。
正しい経路は
`-sBINARYEN_EXTRA_PASSES=--one-caller-inline-max-function-size=20`
（`EMCC_DEBUG=1` で実コマンド行に届くことを確認済み）。

### F-26-7. 却下した案（すべて実測のうえで）

| 案 | 結果 | 却下理由 |
|---|---|---|
| コア数を増やす | p≈37%、4 コア上限 1.38x | 実測 1.05 コア。原理的に効かない |
| メモリを増やす | RSS 6.46GB / 16GB | 余っている |
| wasm-opt を多重起動 | 起動回数 1 | 分割の余地なし |
| 入力を小さくする | **既に適用済み** | `OPTLEVEL = -O3`（Makefile L425-426） |
| Asyncify を外す（JSPI） | `-Os` 5.81→5.40s（7%） | 効果が小さく互換性を失う |
| `asyncify-ignore-indirect` | **正当性が壊れる** | 下記 |
| Binaryen 120 | 116 より遅い | F-26-4 |
| 配信を `-O1` にする | gzip が増える | 62,063 → 62,870 B |

**`asyncify-ignore-indirect` は数字だけ見れば魅力的だった**
（`-Os` 2.77→2.54s、177,893→166,808 B、asyncify IR 23,166→10,210）。
しかし node で実行すると:

```
通常 ASYNCIFY            => before=0 / after=42        正しい
ASYNCIFY_IGNORE_INDIRECT => before=0 / after=0
                            before=0 / after=42        【誤り】
```

**戻り値が壊れ、`main` が 2 回実行された。**
CDDA は yield を含む仮想呼び出しを多用するので使用不可。
**時間とサイズだけを見て採用していたら、静かに壊れた成果物を
配布していた。**

### F-26-8. 採用した構成と本番での見込み

**結論: Binaryen を 116 → 132 に差し替えるのが最大の効果を持つ。
配信用ビルドの wasm-opt が実測比で -39%（242 分 → 約 148 分）になる。**

| 項目 | 内容 |
|---|---|
| wasm-opt | **版 132**（`ci/setup-binaryen.sh` が差し替え） |
| 最適化レベル | **`-O2` 固定**（`-Os` は禁止 / `-O1` は検証用） |
| `-ocimfs` | 既定では使わない（誤差 + サイズ増） |
| 見込み | 242 分 → **約 148 分（2h28m）** |
| gzip | 62,141 B → 62,126 B（4GB 機の基準を満たす） |

**差し替え方式にした理由。** emsdk 全体を上げると Makefile の
フラグ互換性・JS グルー・ASYNCIFY 周りが一斉に変わる。
一方 wasm-opt は **静的リンク済みの単体実行ファイル**
（`ldd` が "statically linked"）なので、バイナリ 1 個の
上書きで済む。副作用は無害な警告 1 行だけ:

```
em++: warning: unexpected binaryen version: 132 (expected 115) [-Wversion-check]
```

**実装上の安全策**（`ci/setup-binaryen.sh`）:
- sha256 検証（版固定だけでは不十分。資産は再アップロードされうる）
- 差し替え**前**に新バイナリの動作確認、**後**に版を再確認して
  失敗ならロールバック
- **全ての失敗経路で 116 のまま続行し終了コード 0**
  （4 時間のビルドをネットワーク障害で落とさない）
- 冪等（既に 132 なら何もしない / `.orig` を上書きしない）
- tarball から **wasm-opt 1 個だけ**を取り出す
  （全展開すると 260MB 超で小さい `/tmp` が溢れる。実際に
  `curl: (23) Failure writing output to destination` で失敗した）

**キャッシュ指紋に wasm-opt の版を含めた**（`ci/link-fingerprint.sh`）。
emcc の版が同じままでも wasm-opt だけが変わるので、
含めないと「116 で作った成果物を 132 の実行が再利用する」
（またはその逆）事故が起きる。

**正直な留保。** PR #6967 が直した O(n²) 的な最悪ケース
（1 関数に 1000 回インライン）は合成ベンチでは**再現できなかった**
（コンパイラが自明な関数を畳んでしまう）。
本番でそれが起きていれば短縮幅はさらに大きいが、確認できていない。
よって -37.8% は**下限**である。

### F-26-9. 一般化できる教訓

1. **ドキュメントの「並列」を信じる前に実測し、合わなければソースを読む。**
   web.dev の記述と 1.05 コアの矛盾は、`Inlining.cpp` の
   `TODO: parallelize` 1 行を読むまで解けなかった。

2. **ホットスポットが特定できたら、上流のそのファイルの
   コミット履歴を追う。** フラグ調整では -6%（誤差）だったが、
   上流の PR を取り込むだけで -37.8% になった。

3. **1 つの新版だけで「更新は無意味」と結論してはいけない。**
   120 は 116 より遅かった。132 まで試して初めて勝ちが見えた。

4. **時間とサイズだけで採用を決めてはいけない。**
   `asyncify-ignore-indirect` は両方良かったが正当性が壊れていた。
   必ず実行して期待値と一致するか確認する。

5. **終了コード 0 は「正しい成果物」を意味しない。**
   132 `-Os` はビルド成功・実行時破損だった。
   守れないものは入口で禁止するしかない。

6. **フラグが静かに無視されることがある。**
   `em++ -O2 -ocimfs 20` はエラーを出しながら素の `-O2` として
   成功していた。実際のコマンド行（`EMCC_DEBUG=1`）で確認する。

---

## F-27. 配信事故：V8 の「1 関数あたり上限」で起動不能になった（2026-09-05）

### F-27-0. 症状

run 33985475507（#75）は **全ジョブ成功・緑**で配信まで通ったが、
ブラウザで開くとゲームが起動せず、次のエラーになった。

```
エラーが発生しました / An error occurred
WebAssembly instantiation failed
CompileError: WebAssembly.instantiateStreaming():
size 7657177 > maximum function size 7654321 @+249007
```

### F-27-1. 原因は「1 つの関数が V8 の上限を超えた」

配信された wasm を直接解析した（`ci/verify-wasm.sh` の元になった手法）。

```
全体          : 67,724,825 バイト
code section  : 65,053,447 バイト / 関数 59,341 個
関数本体合計  : 64,963,414 バイト（平均 1,094）
```

関数サイズの上位:

| 順位 | 関数 | サイズ | V8 上限比 |
|---|---|---|---|
| 1 | func #0 | **7,657,177 B** | **100.04%** ← 超過 |
| 2 | func #21265 | 439,552 B | 5.74% |
| 3 | func #12973 | 370,176 B | 4.84% |

**1 位だけが 2 位の 17 倍**という異常な形をしていた。
エラーの `@+249007` は func #0 のファイル内位置と完全に一致した。

V8 の `kV8MaxWasmFunctionSize` = **7,654,321 バイト**。
これは wasm 仕様の制限ではなく V8 の実装上限だが、
Chrome / Edge / Chromebook はすべて V8 なので配信対象では絶対上限である。

### F-27-2. 【最重要】これは元からあった時限爆弾だった

「132 / `-O2` に変えたせいだ」で終わらせず、
**最後に動いていた #71 の成果物**（Actions のアーティファクトが
まだ生きていた）を取得して同じ解析をかけた。

| ビルド | 構成 | 最大関数 | V8 上限比 | 結果 |
|---|---|---|---|---|
| #71 | 116 / `-Os` | 7,459,141 B | **97.45%** | 動く（余裕 **195,180 B** のみ） |
| #75 | 132 / `-O2` | 7,657,177 B | **100.04%** | **起動不能**（超過 2,856 B） |

差は **198,036 B = +2.65%**。

⇒ **巨大関数は以前から存在し、上限の 97.45% という崖っぷちで
たまたま動いていただけ**だった。
今回の構成変更が 2.65% 押して限界を超えさせた。

**この事実が最も重要である。** 構成を元に戻しても直るのは今回だけで、
**CDDA のコードが 2.6% 増えれば `-Os` でも同じ事故が起きていた**。
つまり「私の変更が壊した」ではなく
「元から壊れる寸前で、私の変更が引き金になった」が正確な理解である。

### F-27-3. なぜ CI では検出できなかったのか

| 検査 | 結果 |
|---|---|
| `em++` の終了コード | **0**（成功） |
| `wasm-opt` の終了コード | **0**（成功） |
| ワークフローの `test -s` | 通過（サイズは 0 でない） |
| 全 13 ジョブ | すべて緑 |

壊れるのは**ブラウザが wasm を instantiate する瞬間だけ**なので、
CI からは一切見えない。
これは F-26-5 で記録した「132 × `-Os` の LinkError」と
**まったく同じ構造の罠**である（ビルド成功・実行時破損）。

同じ型の事故を 2 回続けて踏んだので、
**生成物を毎回測る**以外に防ぐ方法がないと結論した。

### F-27-4. なぜ 1 つの関数が 7MB になるのか

Binaryen の「呼び出し元が 1 つだけの関数」のインライン上限

```
--one-caller-inline-max-function-size (-ocimfs)   既定 -1 = 無制限
```

が**無制限**であることが原因である。
「1 回だけ呼ばれる巨大関数」が呼び出し元に丸ごと取り込まれ、
それが連鎖すると 1 関数が数 MB まで膨らむ。
ImGui の `ShowDemoWindow` のように
「巨大なヘルパーを 1 回ずつ順に呼ぶ」構造が典型である。

**再現に成功した。** 「1 回だけ呼ばれる関数を 140 段連鎖」させる
合成ケースを作って測った（各段 60 文相当）:

| 設定 | 最大関数 | 実行結果 |
|---|---|---|
| 制限なし（#75 と同じ） | **185,583 B** | `OK 18213300.000000` |
| **`-ocimfs=20`** | **23,764 B（-87%）** | `OK 18213300.000000` |

- **正当性**: 出力が完全一致（動作は変わらない）
- **サイズ**: raw 205,954 → 206,515 B / gzip 30,319 → 30,408 B（**+0.3%**）

⇒ 動作を変えずに巨大関数だけを潰せる。

### F-27-5. 採用した対策（2 段構え）

**(1) 生成源を止める** — `ci/tune-makefile.sh`

RELEASE 分岐に次を注入する:

```
LDFLAGS += -sBINARYEN_EXTRA_PASSES=--one-caller-inline-max-function-size=20
```

`-ocimfs` は emcc が直接受け付けない（F-26-6 の罠）。
`-sBINARYEN_EXTRA_PASSES` 経由が正しく、
`EMCC_DEBUG=1` で wasm-opt の実コマンド行に
`one-caller-inline-max-function-size=20` が届くことを確認した。

**(2) 生成物を毎回検証する** — `ci/verify-wasm.sh`（新規）

code section を解析して**最大関数サイズ**を測る:

- 上限超過 → **失敗**（配信させない）
- 上限の 95% 超 → **警告**（次の変更で壊れると予告）

壊れている #75 の wasm に対して実行すると、
ブラウザのエラーを一字一句再現した:

```
[VERIFY]   func #0 = 7,657,177 バイト (超過 2,856 バイト / ファイル位置 +249007)
[VERIFY] この成果物はブラウザで次のエラーになり【起動しません】:
[VERIFY]   size 7657177 > maximum function size 7654321 @+249007
```

動いていた #71 に対しては通過するが、警告が出る:

```
[VERIFY] WARNING: 最大の関数が上限の 97.45% に達しています。
[VERIFY]          残り余裕 195,180 バイトしかありません。
```

**この警告が #71 の時点で出ていれば、事故を予見できた。**

**(3) キャッシュ汚染も確認した**

リンク結果キャッシュが壊れた wasm を再利用しないことを実測で確認:

```
修正前の指紋: 8a2a473d3b1f1430
修正後の指紋: 866167a54d521ed5   ← 変わる
```

指紋は `Makefile` と `ci/*.sh` を含むため、今回の修正で自動的に無効化される。

### F-27-6. 一般化できる教訓

1. **「終了コード 0」は「正しい成果物」を意味しない。**
   F-26-5（132 × `-Os` の LinkError）と F-27 で **2 回**同じ型を踏んだ。
   ビルドが通ることと動くことは別問題である。
   **生成物そのものを測る検査を CI に置く**しかない。

2. **回帰を見つけたら「元に戻す」で終わらせず、過去の成果物と比較する。**
   #71 を解析しなければ「132 が悪い」で終わり、
   **97.45% という崖っぷちだった事実**を見逃していた。
   その場合、数か月後に `-Os` のまま同じ事故が再発していた。

3. **限界の「近さ」を監視する。**
   97.45% は「動いている」が「安全」ではない。
   閾値ギリギリの値は、次の小さな変更で必ず壊れる。
   だから `verify-wasm.sh` は 95% で警告を出す。

4. **既定値が「無制限」の設定は疑う。**
   `-ocimfs` の既定 -1（無制限）が原因だった。
   最適化器の既定値は「一般的なケースに最適」であって
   「我々のケースで安全」ではない。

5. **合成ケースで再現できるまで原因を確定させない。**
   140 段の連鎖で -87% を再現できたことで、
   初めて「対策が効く」と言えるようになった。
   F-26 で O(n²) を再現できなかった件は正直に留保として記録してある。
