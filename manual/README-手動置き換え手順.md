# Workflow ファイルの手動置き換え手順

自動 push ではワークフローファイル（`.github/workflows/` 以下）を変更する
権限がないため、ワークフロー変更があった場合はこの
`manual/build-and-release.yml` を **あなたの手で**
リポジトリの `.github/workflows/build-and-release.yml` に上書きしてください。

## 【今回 PR #5】置き換えが【必須】です

今回の更新（読み込み高速化・日本語入力バグ修正・マップ移動の軽量化）には
ワークフロー本体の変更が含まれています。**マージ後に必ず下記の
「置き換え手順」を実行してください**。置き換えないと:

- 新パッチ `cdda-0I-emscripten-json-cache.patch`（JSON 解析キャッシュの
  永続化 = 2 回目以降のロード短縮）が**ビルドに取り込まれず**、
  検証 grep も走りません
- コンパイル最適化の `-Os` → **`-O3`** 変更（マップ移動の軽量化の柱）が
  反映されません

今回ワークフローに入った変更は次の 3 点です:

1. **JSON キャッシュ永続化パッチの適用ステップ追加**
   （`git apply ../patches/cdda-0I-emscripten-json-cache.patch`）
2. **コンパイル最適化を `-O3` に変更**（emscripten ブランチの
   `OPTLEVEL = -Os` だけを書き換える限定 sed + 確認用 awk）。
   ビルド時間は多少伸びる可能性がありますが、リンク段の `-Os` は
   維持しているため wasm サイズの増加は限定的です
3. **新パッチの検証 grep を 5 行追加**（IME ゲーティング / 描画 yield
   間引き / input_context スタック / JSON キャッシュ / immutable root）

## （前回 PR #3 の回）置き換えが【必須】でした

今回の更新には、ワークフロー本体の設定変更が含まれています。
置き換えないと以下の改善が反映されません:

1. **SIGILL クラッシュ対策**（ワールド作成直後・キャラ作成直後のロード中に
   まれに発生していたもの）:
   - Asyncify 退避バッファを 4MiB → **16MiB** に拡大
     （深い呼び出し中の巻き戻しでバッファが尽きると SIGILL 型のトラップに
     なるため、観測された最深チェーンより十分上に上限を設定）
   - wasm メインスタックを 1MiB → **4MiB** に拡大
     （マップ生成の再帰でのスタック枯渇も SIGILL 型トラップの原因）
2. **動作の高速化**: リンク最適化を `-O0` → 本家標準の **`-Os`** に変更。
   これまで `-O0` でビルド時間を短縮していましたが、Asyncify 計装が
   未最適化のまま残るため、プレイ中の動作が全体的に重くなっていました。
   `-Os` は本家が Web 版の公式ビルドに使っている実績ある設定です。
   （代わりにビルド時間は 1〜2 時間 → 2〜3 時間程度に伸びます）
3. **Stats Through Kills MOD の同梱**: リポジトリの `mods/` フォルダを
   ゲームデータに同梱するステップを追加。初回はキル経験値 MOD
   「Stats Through Kills」が入ります（ワールド作成時の MOD 一覧に表示）。
4. **日本語入力（IME）対応の検証行**: パッチが正しく当たったかを CI が
   確認する grep を 2 行追加。

## 置き換え手順（ブラウザだけで完結、5分）

1. ブラウザで以下を開く:
   https://github.com/LBTStudio/CDDA-broser/blob/main/manual/build-and-release.yml
   （プルリクエストをマージした**あと**に開いてください）
2. 右上の「Raw」ボタンを押し、表示された全文を Ctrl+A → Ctrl+C でコピー
3. 次に以下を開く:
   https://github.com/LBTStudio/CDDA-broser/blob/main/.github/workflows/build-and-release.yml
4. 右上の鉛筆アイコン（Edit this file）を押す
5. エディタ内を Ctrl+A で全選択 → Ctrl+V で貼り付け（全文置き換え）
6. 右上の緑ボタン「Commit changes...」を押す
7. 出てきたダイアログで:
   - Commit message はそのままで OK
   - 「Commit directly to the `main` branch」が選ばれていることを確認
   - 緑の「Commit changes」を押す

これで完了です。そのあと Actions からビルドを実行してください
（手順は PR の説明文にあります）。
