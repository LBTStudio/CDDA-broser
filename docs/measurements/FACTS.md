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
| リンク | `.o` → `.wasm` | **できない** | 単一スレッドの `wasm-opt` |
| データ同梱 | `data/`+`gfx/` → `.data` | 別工程と**並行できる** | `.js`/`.wasm` に依存しない |

**リンクが分割できない理由**（推測ではなくフラグから確定）:

```
LDFLAGS += -Os
LDFLAGS += -sASYNCIFY
```

`-sASYNCIFY` は**モジュール全体の呼び出しグラフを解析**して、
スタックを巻き戻す必要のある関数すべてにステート機械を挿入する変換である。
解析が本質的に大域的なので、モジュールを分割すると呼び出しグラフが
分断されて成立しない。`wasm-opt` も単一スレッドで動く。

→ **全体の所要時間の下限は「リンク段の時間」である。**
これは技術的な限界であり、それ以上は縮められない。

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
