# Cataclysm: DDA 0.I — ブラウザ版ビルドキット

👉 はじめての方は `README-はじめにお読みください.md` を開いてください。
GitHub Actions が最新安定版 0.I "Ito" を自動でブラウザ版に組み立て、GitHub Pages に公開します。
Chromebook は Linux 不要・Chrome で URL を開くだけです。

## 特徴
- 最新安定版 0.I "Ito" (2026-06-06) を WebAssembly でブラウザ実行
- プレイヤー名ごとにセーブデータを完全分離（初回起動時に名前入力）
- セーブのエクスポート（zip バックアップ）機能つき
- リポジトリは約 1MB で GitHub の 25MB 制限に引っかからない
- 重い成果物（wasm 約45MB＋データ約68MB）は CI が毎回生成して Pages へ直接公開

## 技術詳細
docs/ARCHITECTURE.md を参照。ローカルビルド: `bash scripts/build-local.sh`（8GB RAM 以上の Linux/mac）。
