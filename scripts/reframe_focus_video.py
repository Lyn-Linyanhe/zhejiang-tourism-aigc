"""Reframe the six stills in the West Lake draft and render real scene transitions.

Run from the repository root: python scripts/reframe_focus_video.py
The source final.mp4 supplies its existing AAC mix; subtitles and the AI label
are burned once on the new picture. The source deliverable is kept intact.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.providers.media_ffmpeg import (
    AIGC_LABEL_TEXT,
    aigc_label_filter,
    find_cjk_font,
    grade_filter,
)


DRAFT = ROOT / "runtime" / "media" / "draft_02fb57435335"
WORK = DRAFT / "focus_work"
WIDTH, HEIGHT, FPS = 1920, 1080, 30
TOTAL = 30.0
TRANSITION = 0.56
HALF = TRANSITION / 2
# Durations of the source shot files. Their sum is 26.5667 s; the final tea
# image continues moving through the original 30 s tail.
SHOT_DURATIONS = (5.166667, 4.0, 4.7, 4.966667, 3.7, 4.033333)
# Coordinates refer to the 16:9 center crop, after source aspect correction.
# Subjects: Leifeng Pagoda, pagoda/boat, lake boat, sunset reflection,
# pagoda framed by willow, and the central tea-leaf texture.
MOTIONS = (
    {"focus": (0.77, 0.32), "start": 1.00, "end": 1.20},
    {"focus": (0.62, 0.39), "start": 1.16, "end": 1.01},
    {"focus": (0.45, 0.55), "start": 1.04, "end": 1.16},
    {"focus": (0.64, 0.62), "start": 1.00, "end": 1.17},
    {"focus": (0.53, 0.48), "start": 1.02, "end": 1.21},
    {"focus": (0.51, 0.52), "start": 1.13, "end": 1.02},
)
# Transition names are ffmpeg xfade presets. Each is centered on the original
# shot boundary so the voice/subtitle timing stays aligned.
TRANSITIONS = ("smoothleft", "fade", "dissolve", "circleopen", "wipeleft")


def run(args: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed")


def motion_filter(index: int, duration: float) -> str:
    motion = MOTIONS[index]
    fx, fy = motion["focus"]
    # Smoothstep eases the zoom at both ends. Scale evaluates t per frame;
    # crop x/y evaluate against the changing scaled dimensions. Cropping at
    # the focus point keeps the chosen subject stationary while it grows.
    u = f"min(1,max(0,t/{duration:.6f}))"
    eased = f"({u})*({u})*(3-2*({u}))"
    zoom = f"({motion['start']:.4f}+({motion['end']-motion['start']:.4f})*({eased}))"
    return (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={WIDTH}:{HEIGHT},"
        f"scale=w='trunc({WIDTH}*({zoom})/2)*2':"
        f"h='trunc({HEIGHT}*({zoom})/2)*2':eval=frame:flags=bicubic,"
        f"crop={WIDTH}:{HEIGHT}:"
        f"x='(in_w-{WIDTH})*{fx:.4f}':y='(in_h-{HEIGHT})*{fy:.4f}',"
        f"{grade_filter()},fps={FPS},format=yuv420p"
    )


def main() -> None:
    WORK.mkdir(exist_ok=True)
    source_final = DRAFT / "final.mp4"
    if not source_final.exists():
        raise FileNotFoundError(source_final)
    boundaries: list[float] = []
    elapsed = 0.0
    for duration in SHOT_DURATIONS[:-1]:
        elapsed += duration
        boundaries.append(elapsed)
    starts = [0.0, *(point - HALF for point in boundaries)]
    ends = [*(point + HALF for point in boundaries), TOTAL]
    clips: list[Path] = []
    for index, (start, end) in enumerate(zip(starts, ends)):
        clip = WORK / f"focus_{index + 1}.mp4"
        duration = end - start
        print(f"Rendering shot {index + 1}/6: {duration:.2f} s", flush=True)
        run([
            "-loop", "1", "-framerate", str(FPS),
            "-i", str(DRAFT / "assets" / f"shot_{index + 1}.jpg"),
            "-t", f"{duration:.6f}", "-vf", motion_filter(index, duration),
            "-frames:v", str(round(duration * FPS)), "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", str(clip),
        ])
        clips.append(clip)

    # Xfade is an actual two-picture transition. Offsets are absolute in the
    # accumulated chain, centered at the scene boundaries.
    graph: list[str] = []
    current = "0:v"
    for index, (boundary, style) in enumerate(zip(boundaries, TRANSITIONS)):
        output = f"x{index + 1}"
        graph.append(
            f"[{current}][{index + 1}:v]xfade=transition={style}:"
            f"duration={TRANSITION:.3f}:offset={boundary - HALF:.6f}[{output}]"
        )
        current = output
    filters = ["subtitles=subtitles.srt"]
    font = find_cjk_font()
    if font:
        filters.append(aigc_label_filter(AIGC_LABEL_TEXT, WIDTH, HEIGHT, font))
    graph.append(f"[{current}]{','.join(filters)},trim=duration={TOTAL:.3f},setpts=PTS-STARTPTS[v]")
    output = DRAFT / "final_focus.mp4"
    print("Compositing transitions, subtitles, and audio", flush=True)
    inputs: list[str] = []
    for clip in clips:
        inputs.extend(["-i", str(clip)])
    inputs.extend(["-i", str(source_final)])
    run([
        *inputs, "-filter_complex", ";".join(graph),
        "-map", "[v]", "-map", "6:a:0", "-t", f"{TOTAL:.3f}",
        "-frames:v", str(round(TOTAL * FPS)),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(output),
    ], cwd=DRAFT)
    details = {
        "source_video": str(source_final),
        "output_video": str(output),
        "duration_sec": TOTAL,
        "fps": FPS,
        "focal_motion": MOTIONS,
        "transitions": TRANSITIONS,
        "transition_duration_sec": TRANSITION,
        "audio_source": "original final.mp4 AAC track",
        "subtitles_source": "subtitles.srt",
    }
    (DRAFT / "focus_edit.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(output, flush=True)


if __name__ == "__main__":
    main()
