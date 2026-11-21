"""Generate a West Lake sample via preview, edit, generate; --online verifies real stock footage."""

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.config import settings
from backend.app.models import TaskInput
from backend.app.services.orchestrator import Orchestrator
from backend.app.storage import Storage

SHOTS = [
    ("湖边出发", "杭州西湖，沿着湖岸慢慢走。"),
    ("湖面山影", "远处的山，映在西湖的水面上。"),
    ("湖上慢游", "一阵微风吹过，水面泛起波纹。"),
    ("沿岸望湖", "沿着岸边走一段，再回头望望。"),
    ("湖畔停留", "找个地方坐下，听一听湖边的声音。"),
    ("西湖再见", "离开杭州以前，把这一眼西湖带走。"),
]


def run(online):
    cfg = replace(settings, default_provider="offline",
                  subtitle_alignment="auto" if online else "sentence")
    app = Orchestrator(cfg, Storage(cfg.db_path))
    task = app.create_preview(
        TaskInput.from_dict(
            dict(
                city="杭州",
                landmark="西湖",
                theme="杭州西湖漫游",
                shot_count=6,
                duration=30,
                style="诗意纪实",
                voice="Windows 系统语音",
                voice_engine="windows",
                music_mood="舒缓",
                asset_source="stock" if online else "offline",
            )
        )
    )
    if len(task.shots) != 6:
        raise RuntimeError(task.error or "脚本没有生成六镜")
    for shot, (title, narration) in zip(task.shots, SHOTS):
        app.update_shot(
            task.id,
            shot.id,
            dict(
                title=title,
                visual_prompt="杭州西湖，" + title,
                narration=narration,
                subtitle=narration,
            ),
        )
    app.update_draft(
        task.id,
        dict(title="杭州西湖 · 沿湖慢行", narration="".join(s for _, s in SHOTS)),
    )
    app.generate(task.id)
    print(task.id, flush=True)
    while True:
        state = app.storage.get_task(task.id)
        if state["status"] in {"completed", "failed"}:
            break
        time.sleep(1)
    if state["status"] == "failed":
        raise RuntimeError(state["error"])
    folder = cfg.media_dir / task.id
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(folder / "final.mp4"),
            ]
        )
    )
    durations = {s["codec_type"]: float(s["duration"]) for s in info["streams"]}
    durations["container"] = float(info["format"]["duration"])
    durations_ok = all(
        abs(durations[k] - 30) <= 0.08 for k in ("video", "audio", "container")
    )
    footage = [
        a
        for a in state["assets"]
        if a["media_type"] == "video"
        and a["source"] in {"pixabay", "pexels"}
        and a.get("page_url")
    ]
    stream_ids = set()
    for a in footage:
        streams = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                a["local_path"],
            ]
        )
        if streams.strip():
            stream_ids.add(a["external_id"])
    subtitle = (folder / "subtitles.srt").read_text(encoding="utf-8")

    def seconds(stamp):
        h, m, s, ms = map(int, re.split("[:,]", stamp))
        return h * 3600 + m * 60 + s + ms / 1000

    bounds = [
        tuple(map(seconds, line.split(" --> ")))
        for line in subtitle.splitlines()
        if " --> " in line
    ]
    subtitles_ok = bool(bounds) and all(
        0 <= a < b <= 30.001 and (i == 0 or a >= bounds[i - 1][1])
        for i, (a, b) in enumerate(bounds)
    )
    report = dict(
        task_id=task.id,
        online=online,
        streams=durations,
        subtitle_count=len(bounds),
        subtitles_ok=subtitles_ok,
        unique_stock_videos=len(stream_ids),
        assets=state["assets"],
        alignment=state["audio"].get("subtitle_alignment"),
        voice=state["audio"]["voice_provider"],
        music=state["audio"]["music_provider"],
        music_notice=state["audio"].get("music_notice"),
        passed=durations_ok and subtitles_ok and (not online or len(stream_ids) >= 4),
    )
    (folder / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(folder, report["passed"], flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--online",
        action="store_true",
        help="Require at least four distinct stock videos",
    )
    sys.exit(run(parser.parse_args().online))
