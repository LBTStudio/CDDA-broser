# Workflow ファイルの手動置き換え手順

自動 push ではワークフローファイル（`.github/workflows/` 以下）を変更する
権限がないため、この `manual/build-and-release.yml` を **あなたの手で**
リポジトリの `.github/workflows/build-and-release.yml` に上書きしてください。

## なぜ置き換えるのか

中身の実行内容（パッチ適用・メモリ設定・ビルド手順）は現行と同一です。
違いは「パッチが正しく当たったかを CI が確認する検証行」の追加のみ:

- `mount_idbfs( idbfs_dir.c_str() )` — セーブ消失バグ修正の確認
- `fsPersistenceReady` — キャラ作成直後の Exception 停止修正の確認
- `CDDA_ON_IDBFS_MOUNTED` / `pagehide` — 設定移行フック・タブ破棄対策の確認
- `cdda_last_pump_yield` — ワールド作成後の過負荷ダイアログ修正の確認

**置き換えなくてもビルドは成功します**（パッチ自体は既に反映済み）。
置き換えると、将来パッチが壊れたときに CI が早期検知できるようになります。

## 置き換え手順（ブラウザだけで完結、5分）

1. ブラウザで以下を開く:
   https://github.com/LBTStudio/CDDA-broser/blob/genspark_ai_developer/manual/build-and-release.yml
2. 右上の「Raw」ボタンを押し、表示された全文を Ctrl+A → Ctrl+C でコピー
3. 次に以下を開く:
   https://github.com/LBTStudio/CDDA-broser/blob/genspark_ai_developer/.github/workflows/build-and-release.yml
4. 右上の鉛筆アイコン（Edit this file）を押す
5. エディタ内を Ctrl+A で全選択 → Ctrl+V で貼り付け（全文置き換え）
6. 右上の緑ボタン「Commit changes...」を押す
7. 出てきたダイアログで:
   - Commit message はそのままで OK
   - 「Commit directly to the `genspark_ai_developer` branch」が
     選ばれていることを確認（重要）
   - 緑の「Commit changes」を押す

これで完了です。以降のビルドで新しい検証が有効になります。
