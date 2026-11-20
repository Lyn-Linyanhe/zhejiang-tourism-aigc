"""Find a useful zoom anchor in an image without a cloud vision dependency.

This is a contrast-based saliency estimate, not semantic object recognition.
An editor can override it with normalized focus_x/focus_y coordinates.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


SAMPLE_WIDTH = 160
SAMPLE_HEIGHT = 90


def detect_focus(path: Path) -> tuple[float, float]:
    """Return the strongest interior detail cluster, or the image center."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-frames:v", "1", "-vf",
            f"scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode or len(result.stdout) != SAMPLE_WIDTH * SAMPLE_HEIGHT * 3:
        return 0.5, 0.5
    pixels = result.stdout
    luminance = [
        (pixels[i] * 54 + pixels[i + 1] * 183 + pixels[i + 2] * 19) >> 8
        for i in range(0, len(pixels), 3)
    ]
    scores = [[0.0] * 16 for _ in range(9)]
    for y in range(1, SAMPLE_HEIGHT - 1):
        row = y * SAMPLE_WIDTH
        gy = y // 10
        for x in range(1, SAMPLE_WIDTH - 1):
            gx = x // 10
            at = row + x
            scores[gy][gx] += (
                abs(luminance[at + 1] - luminance[at - 1])
                + abs(luminance[at + SAMPLE_WIDTH] - luminance[at - SAMPLE_WIDTH])
            )
    ranked: list[tuple[float, float, float]] = []
    for gy in range(1, 8):
        for gx in range(1, 15):
            x, y = (gx + 0.5) / 16, (gy + 0.5) / 9
            # Avoid a border branch or a corner texture becoming the anchor.
            spatial = 1 - 0.30 * abs(x - 0.5) * 2 - 0.16 * abs(y - 0.5) * 2
            ranked.append((scores[gy][gx] * spatial, x, y))
    best = max(ranked, default=(0, 0.5, 0.5))
    # A flat or nearly blank frame has no reliable subject.
    if best[0] < 150:
        return 0.5, 0.5
    return round(best[1], 4), round(best[2], 4)
