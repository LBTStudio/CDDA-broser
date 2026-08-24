# はじめての方へ：ブラウザ版 Cataclysm DDA 導入ガイド

このガイドは **専門知識ゼロ・コピペだけで完了** することを目指して書かれています。
あなたが実際に操作するのは 15 分程度。残りの 40〜60 分は GitHub が自動でゲームを組み立てる待ち時間です。

## 用意するもの（2つだけ）
1. ネットにつながったパソコン（Chromebook / Windows / Mac どれでもOK）
2. メールアドレス（GitHub 登録用）

ソフトのインストール、Chromebook の Linux 機能などは **一切不要** です。

## 全体像
1. GitHub アカウントを作る（3分）
2. この zip の中身を GitHub に置く（5分）
3. GitHub のスイッチを2つ押す（2分）
4. 40〜60分待つ（放置でOK）
5. できた URL を Chrome で開く → ゲーム開始

## ① GitHub アカウントを作る
1. https://github.com/signup を開く
2. メールアドレス → パスワード → ユーザー名（半角英数字。後でゲームの URL になります。例: tanaka-cdda）
3. 確認メールのコードを入力して完了

## ② 配布ファイルを GitHub に置く
1. この zip（cdda-web-0I-lite.zip）をダウンロードして **解凍**
   - Windows: 右クリック →「すべて展開」
   - Chromebook: ファイルアプリでダブルクリック → 中身をコピー
   - 解凍すると cdda-web-0I フォルダが出ます
2. GitHub にログイン → 右上「＋」→「New repository」
3. Repository name に半角英字で名前を入力（例: my-cdda）
   - 「Public」のままでOK（無料で無制限に組み立てが使えます）
   - **「Add a README file」にはチェックしない**
4. 「Create repository」
5. 開いたページの「uploading an existing file」リンクを押す
6. 解凍した **cdda-web-0I フォルダの中身全部** をドラッグ＆ドロップ
   - ⚠️ Chromebook / Mac では `.github` などピリオド始まりの隠しファイルが最初見えないことがあります。ファイル選択画面で **Ctrl + .** を押すと見えます。`.github` フォルダは自動組み立ての設計図なので **必須** です
   - 合計 1MB 程度・数十秒で終わります
7. 画面下の緑の「Commit changes」を押す

## ③ スイッチを2つ押す
1. リポジトリ上部「Settings」→ 左メニュー「Pages」
2. 「Source」を **「GitHub Actions」** に変更
3. 上部「Actions」タブ → 黄色い注意文が出たら「I understand my workflows, go ahead and enable them」
4. 左の「Build CDDA 0.I WebAssembly」→ 右の「Run workflow」→ 緑の「Run workflow」

## ④ 40〜60分待つ
画面を閉じてOK。GitHub が自動でソース取得 → WebAssembly 組み立て → データ圧縮 → 公開まで行います。
Actions 画面が緑の ✓ になったら完成です。**赤い ✗ になったら**: 失敗画面の URL をそのまま相談窓口に貼ってください。

## ⑤ 遊ぶ
ブラウザで次を開くだけ：
  https://あなたのユーザー名.github.io/リポジトリ名/
例: https://tanaka-cdda.github.io/my-cdda/

- 初回だけ約 70MB を読み込みます（Wi-Fi 推奨。2回目以降は速い）
- Chromebook は Chrome で開くだけ。Linux 機能は不要です

## プレイヤーごとのセーブ分離
- ゲームを開くと最初に **プレイヤー名入力画面** が出ます
- 名前ごとに専用のセーブ領域がブラウザ内に作られ、**家族で1台を共用してもセーブは完全に別々** です
- 使った名前はボタンとして残り、2回目以降はクリックで続きから
- 左上の歯車 →「Switch Player」で切替（切替前にゲーム内でセーブを）
- 歯車 →「Export Saves」で自分のセーブを zip でバックアップできます

## セーブの注意点
- セーブは「その端末の、そのブラウザの中」に保存。別端末には自動引き継ぎされません
- ブラウザの「サイトデータ削除」で消えます。大切な冒険は Export Saves で定期バックアップを

## 困ったとき
| 症状 | 対処 |
|---|---|
| 読み込みが止まる | 再読み込み。2回目はキャッシュで進むことが多い |
| 真っ黒 | 再読み込みして最初から |
| セーブが消えた | サイトデータ削除が原因。Export でのバックアップを習慣に |
| 動作が重い | 他のタブを閉じる／タイルセットを ASCIITileset に変更 |
| Actions が失敗 | 失敗ログ画面の URL を相談窓口へ |
