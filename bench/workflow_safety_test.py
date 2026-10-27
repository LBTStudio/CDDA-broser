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
expected_simd_early_guard = "if: ${{ inputs.wasm_simd == 'on' && inputs.deploy_pages != false }}"
expected_deploy_gate = "if: ${{ inputs.deploy_pages != false && inputs.runtime_backend != 'jspi' }}"
assert "Reject experimental JSPI deployment" in workflow
assert "Reject experimental SIMD deployment" in workflow
assert expected_simd_early_guard in workflow
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
measure_workflow = (root / ".github/workflows/browser-measurement.yml").read_text(encoding="utf-8")
architecture = (root / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
facts = (root / "docs/measurements/FACTS.md").read_text(encoding="utf-8")
assert "cdda-assets-bundle-v3-*" in architecture
assert "immutable bundle snapshots" in architecture
assert "CI rejects" in architecture and "mon_info_update_throttled" in architecture
assert "exclusive Web Lock" in architecture
assert "F-20 [旧案・撤回済み]" in facts
assert "this throttling proposal was retracted" in facts

# Architecture A/B experiments default to current scalar behavior and can measure
# an exact build artifact without publishing experimental binaries.
assert "wasm_simd:" in workflow and 'default: "off"' in workflow
assert "CDDA_WASM_SIMD" in workflow
assert 'echo "| WebAssembly SIMD | $CDDA_WASM_SIMD |"' in workflow
assert "CDDA_WASM_SIMD" in (root / "ci/tune-makefile.sh").read_text(encoding="utf-8")
assert "artifact_run_id:" in measure_workflow
assert "github.event_name == 'pull_request' && 'compare'" in measure_workflow
assert 'GITHUB_EVENT_NAME" == "pull_request' in measure_workflow
assert "for population in 32 64 128 200; do" in measure_workflow
assert "Download exact bundle from the selected full build" in measure_workflow
assert "github.event.inputs.artifact_run_id" in measure_workflow
assert "measurement-source.json" in measure_workflow
assert "SOURCE_COMMIT_SHA" in measure_workflow
assert "cdda-build-provenance" in workflow
assert "build-provenance.json" in workflow
assert "cdda-build-provenance" in measure_workflow
assert "measurementRunAttempt" in measure_workflow
assert "name: cdda-playwright-measurement-${{ github.run_id }}-attempt-${{ github.run_attempt }}" in measure_workflow
assert "export SOURCE_COMMIT_SHA SOURCE_RUN_ATTEMPT" in measure_workflow

# Python heredocs embedded in YAML run blocks must remain inside the block
# scalar; a column-0 "import" silently turns the workflow into an invalid file.
for workflow_path in [workflow_path, root / ".github/workflows/browser-measurement.yml"]:
    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "python3 - <<'PY'":
            continue
        command_indent = len(line) - len(line.lstrip())
        for body_index in range(index + 1, len(lines)):
            body_line = lines[body_index]
            if not body_line.strip():
                continue
            body_indent = len(body_line) - len(body_line.lstrip())
            assert body_indent >= command_indent, (
                f"{workflow_path}:{body_index + 1}: heredoc line escaped YAML run block"
            )
            if body_line.strip() == "PY":
                break
        else:
            raise AssertionError(f"{workflow_path}:{index + 1}: unterminated Python heredoc")

# The generated binary is checked regardless of deployment mode. Experimental
# artifacts use deploy_pages=false, but must not bypass the V8 function-size guard.
verify_step = workflow.split("      - name: Verify linked binary", 1)[1].split("      - name: Upload linked binary", 1)[0]
assert "if:" not in verify_step, "binary verification must run for unpublished experimental builds"

runner = (root / "bench/playwright_game_measurement.mjs").read_text(encoding="utf-8")
contract = (root / "bench/measurement_contract.mjs").read_text(encoding="utf-8")
assert "validateFixtureOutcome" in runner and "classifyRequestFailures" in runner
assert "validateActiveRenderer" in runner
assert "activeRendererValidation.ok" in runner
assert "extractWasmHeapSamples" in runner and "wasmHeapSampleValidation.ok" in runner
assert "webglFallback" in runner
assert "population-monster-count" in (root / "bench/measurement_contract.mjs").read_text(encoding="utf-8")
assert "fixtureValidation.ok" in runner and "requestFailureValidation.ok" in runner
assert "reloadRestoreValidation" in runner
assert "persistentProfile = 'perf_' + profile" in runner
assert "fill(persistentProfile)" in runner
assert "moves-preserved" in runner
assert "population === 0 ? scenario + '-' + renderer : scenario + '-' + renderer + '-z' + population" in runner
assert "Save reload/restore verification failed" in runner
assert "reading-reached-level-five" in contract
assert "sleep-wake-message" in contract
assert "known Emscripten data-package request abort" in contract
assert "measurement_contract_test.mjs" in (root / "ci/apply-patches.sh").read_text(encoding="utf-8")
fixture_source = (root / "bench/real_activity_fixture.py").read_text(encoding="utf-8")
assert "sampleCount = population > 0 ? 30 : 15" in fixture_source
assert "bytes.length < 2000000" in fixture_source
assert "extractWasmHeapSamples" in contract
print("PASS docs consistency: synchronized workflows, build provenance, artifact identity, binary safety, and strict game-measurement acceptance")
