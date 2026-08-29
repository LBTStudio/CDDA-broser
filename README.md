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

## RAM 4GB Chromebook 向けの安定化（このリポジトリ独自）
- **世界生成中のフリーズ対策**: JSON ロード・MOD 相互作用・ファイナライズ・
  オーバーマップ生成・マップ生成の各段階で 50ms ごとにブラウザへ制御を返す
  パッチを適用（`patches/` 参照。すべて Emscripten ビルド限定）
- **メモリ方針**: 起動時 256MB・上限 2GB（成長方式）。世界生成のピークで
  1GB 上限だと確保に失敗して固まるため 2GB に設定
- **Asyncify スタック 4MB**: 深い呼び出し中の yield でも状態が壊れないように
- **セーブ書き込みの間引き**: 世界生成中の大量ファイル書き込みを 250ms 単位で
  まとめて IndexedDB へ永続化（1 ファイルごとの同期をやめて負荷を削減）
- **エラーの見える化**: 黒画面のまま止まらず、日本語の対処ヒントつき
  エラー画面を表示（メモリ不足時は「他のタブを閉じて再読み込み」を案内）

## ビルドの動かし方
Actions タブ →「Build CDDA 0.I WebAssembly」→ Run workflow。
`deploy_pages` のチェックを外すと Pages を更新せずビルド成果物（artifact）
だけ作れるので、公開前の動作確認に使えます。

## 技術詳細
docs/ARCHITECTURE.md を参照。ローカルビルド: `bash scripts/build-local.sh`（8GB RAM 以上の Linux/mac）。
