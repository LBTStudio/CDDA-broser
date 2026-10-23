#!/usr/bin/env python3
"""Regression checks for the manual-release workflow's experimental backend guard."""
from pathlib import Path

workflow = (Path(__file__).resolve().parent.parent / ".github/workflows/build-and-release.yml").read_text(encoding="utf-8")
expected_early_guard = "if: ${{ inputs.runtime_backend == 'jspi' && inputs.deploy_pages != false }}"
expected_deploy_gate = "if: ${{ inputs.deploy_pages != false && inputs.runtime_backend != 'jspi' }}"
assert "Reject experimental JSPI deployment" in workflow
assert expected_early_guard in workflow
assert 'JSPI is experimental and must not be deployed to GitHub Pages' in workflow
assert expected_deploy_gate in workflow
assert "runtime_backend:" in workflow and "- \"jspi\"" in workflow
print("PASS workflow safety: JSPI cannot pass the plan guard or reach Pages deployment")
