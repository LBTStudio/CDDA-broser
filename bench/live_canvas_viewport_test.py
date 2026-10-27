#!/usr/bin/env python3
"""Source guard for the live low-resolution canvas capture.

This protects the diagnostic measurement only. It does not claim the CSS
setting that looks better in GitHub-hosted Chromium will look better on
physical ChromeOS hardware.
"""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
runner = (root / "bench" / "playwright_game_measurement.mjs").read_text(encoding="utf-8")
workflow = (root / ".github" / "workflows" / "browser-measurement.yml").read_text(encoding="utf-8")
docs = (root / "docs" / "measurements" / "PLAYWRIGHT.md").read_text(encoding="utf-8")

steps = [
    "await page.setViewportSize({ width: 850, height: 645 });",
    "live-canvas-850x645-pixelated.png",
    "el.style.setProperty('image-rendering', 'auto', 'important')",
    "live-canvas-850x645-auto.png",
    "await page.setViewportSize(originalViewport);",
    "const liveViewportComparison = {",
    "visualScaleComparison, liveViewportComparison, fixture:",
]
positions = [runner.find(step) for step in steps]
assert all(position >= 0 for position in positions), (
    "live viewport capture steps or result serialization are missing"
)
assert positions == sorted(positions), "capture must run in viewport->A/B->restore order"
assert "scaleX: canvas.width ? rect.width / canvas.width : null" in runner
assert "scaleY: canvas.height ? rect.height / canvas.height : null" in runner
assert "imageRendering: style.imageRendering" in runner
assert "viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio }" in runner
assert "timeout-minutes: 60" in workflow, (
    "the real-game renderer/population matrix must have enough time to upload viewport captures"
)
assert "live-canvas-850x645-pixelated.png" in docs
assert "live-canvas-850x645-auto.png" in docs
print("PASS live canvas viewport diagnostic: actual 850x645 resize, intrinsic/CSS scale metrics, A/B capture, viewport restoration, and timeout budget")
