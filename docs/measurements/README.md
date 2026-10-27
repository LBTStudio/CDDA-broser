# 計測・技術事実データベース

このディレクトリは **一度確認した技術的事実を再確認せずに済ませる** ための
恒久記録です。新しい最適化を検討するときは、まずここを読んでください。

| ファイル | 内容 |
|---|---|
| `FACTS.md` | 確定した技術的事実（結論と根拠）の一覧。**最初に読む** |
| `2026-09-03-yield-primitives.md` | ブラウザ yield プリミティブの実測（4ms クランプの発見） |
| `2026-09-03-asyncify-overhead.md` | Asyncify 計装そのものの CPU コスト実測 |
| `2026-09-03-build-pipeline.md` | ビルド時間の内訳と CI 高速化の根拠 |
| `raw/` | ベンチマークの生ログ（コンソール出力そのまま） |

再現手順は `bench/README.md`（リポジトリ直下 `bench/`）にあります。
ベンチマークのソースも同梱してあるので、環境が変わったときは
`bash bench/run-all.sh` で再計測できます。

## 計測環境（2026-09-03）

- サンドボックス: Linux x86_64, 2 vCPU, RAM 985MiB
- ブラウザ: Playwright Chromium 1234 (headless, `--no-sandbox`)
- Emscripten: 3.1.51（本番 CI と同一）
- 比較用のリンク設定は本番と同じ `-sASYNCIFY` 有効

RAM 4GB Chromebook の実機ではないため、**絶対時間や高速化率をそのまま実機へ転用できません**。
ブラウザAPI由来の待機時間を比較する参考にはなりますが、ゲーム内処理、描画、
メモリ圧力、CPU制限、ブラウザ・OSの違いによって相対比も変わり得ます。
実機に適用する判断には、同じシナリオの変更前後を同じ端末で測定する必要があります。
実バンドル測定の限界は [REVIEW-2026-10-27.md](REVIEW-2026-10-27.md) を参照してください。

### Browser crash and severe-stall guard (2026-10-27)

The real-bundle Playwright harness persists Chromium page-crash events and fails even when a crash occurs after the startup promise has completed. During the controlled active-movement window, a missing RAF sample or a maximum RAF interval of 5,000 ms or longer also fails the run. These are severe-failure guards, not a Chromebook stability guarantee. See [the guard review](BROWSER-HEALTH-GUARDS-2026-10-27.md) and `node bench/browser_health_test.mjs`.
