#!/usr/bin/env python3
"""Regression checks for the manual release workflow, linker cache and deploy safety."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
workflow_path = root / ".github/workflows/build-and-release.yml"
manual_path = root / "manual/build-and-release.yml"
workflow = workflow_path.read_text(encoding="utf-8")
manual = manual_path.read_text(encoding="utf-8")

# The manual replacement file must never roll back deployment safety or the
# build topology. It is intentionally kept byte-for-byte equal to the live one.
assert workflow == manual, "manual/build-and-release.yml is stale; sync it with the live workflow"

expected_early_guard = "if: ${{ inputs.runtime_backend == 'jspi' && inputs.deploy_pages != false }}"
expected_deploy_gate = "if: ${{ inputs.deploy_pages != false && inputs.runtime_backend != 'jspi' }}"
assert "Reject experimental JSPI deployment" in workflow
assert expected_early_guard in workflow
assert "JSPI is experimental and must not be deployed to GitHub Pages" in workflow
assert expected_deploy_gate in workflow
assert "runtime_backend:" in workflow and '- "jspi"' in workflow

# The linker fingerprint is only trustworthy after all planned object files
# have been downloaded and checked in the link job. Computing it in plan
# inevitably returned an "incomplete-<timestamp>" key and defeated reuse.
assert "link_cache_key: ${{ steps.plan.outputs.link_cache_key }}" not in workflow
assert 'link_fingerprint="$( bash ../ci/link-fingerprint.sh ../shard-plan/objs-all.txt )"' not in workflow.split("      - name: Build shard plan", 1)[1].split("      - name: Upload shard plan", 1)[0]
assert "Fingerprint complete link inputs" in workflow
assert 'CDDA_LINK_OPT="${{ inputs.link_opt }}" bash ../ci/link-fingerprint.sh ../shard-plan/objs-all.txt' in workflow
assert "key: wasm-link-${{ steps.link_fingerprint.outputs.key }}" in workflow

link_job = workflow.split("  link:\n", 1)[1].split("  # ==================================================================\n  # ジョブ 5", 1)[0]
ordered_steps = [
    "      - name: Download all shard objects",
    "      - name: Verify object layout",
    "      - name: Setup emsdk",
    "      - name: Fingerprint complete link inputs",
    "      - name: Restore wasm link cache",
    "      - name: Link WebAssembly (if not cached)",
]
positions = [link_job.index(marker) for marker in ordered_steps]
assert positions == sorted(positions), positions
assert 'if [ "$expected" -eq 0 ] || [ "$found" -ne "$expected" ]; then' in link_job

# No empty cross-job table: step/job summaries are not shared across jobs.
assert 'echo "## 各工程の実測"' not in workflow
assert 'echo "| 工程 | 対象 | 所要 | 成果物 |"' not in workflow
assert "## 各工程の実行ログ" in workflow

# Architecture and measurement notes must describe the implementation that is
# actually applied by CI, not the retracted first version of the activity patch.
architecture = (root / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
facts = (root / "docs/measurements/FACTS.md").read_text(encoding="utf-8")
assert "cdda-assets-bundle-v3-*" in architecture
assert "immutable bundle snapshots" in architecture
assert "CI rejects" in architecture and "mon_info_update_throttled" in architecture
assert "exclusive Web Lock" in architecture
assert "F-20 [旧案・撤回済み]" in facts
assert "this throttling proposal was retracted" in facts
# Current guidance must not present historical draft PRs as outstanding work or
# promise a fixed cold-build time / obsolete download size to first-time users.
readme = (root / "README.md").read_text(encoding="utf-8")
quickstart = (root / "README-はじめにお読みください.md").read_text(encoding="utf-8")
for stale in ("[Draft PR #22]", "[Draft PR #21]", "[Draft PR #20]", "[Draft PR #19]"):
    assert stale not in readme, f"README still presents merged PR as a draft: {stale}"
assert "PR #22 は2026年9月14日にmainへマージ済み" in readme
assert "JSPIは公開用バックエンドではなく" in readme
assert "runtime_backend=asyncify" in readme and "deploy_pages=true" in readme
assert "40〜60分" not in quickstart
assert "約 120MB 前後" in quickstart
assert "同じIDを複数タブで同時に起動できません" in quickstart
assert "最初から" not in quickstart
print("PASS docs consistency: immutable cache, current danger-check cadence, profile tab locks, release flow, user setup guidance, and retired optimization clearly labelled")
