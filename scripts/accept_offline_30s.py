"""30 秒离线西湖成片验收。走产品路径，不另写一套 ffmpeg 演示。

从任意工作目录运行：

    python path/to/scripts/accept_offline_30s.py

配音用 tts.build_audio 的真实 cues。镜头时长由
Orchestrator.apply_audio_durations 反推并锁到 30 秒。成片由
VideoRenderer.render 写出。配音长于 30 秒时按现有规则裁到 30，不把成片拉长。
字幕用 cues 的绝对时间，最后一条不超过 30。

edge-tts 不可用时退出码非 0，不用静音假装对齐。
标准输出一行 JSON。container / video / audio 都在 29.95 到 30.05 才退出码 0。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.models import Shot, Task, TaskInput  # noqa: E402
from backend.app.providers.media import OfflineAssetProvider  # noqa: E402
from backend.app.providers.tts import build_audio  # noqa: E402
from backend.app.services.orchestrator import Orchestrator  # noqa: E402
from backend.app.services.renderer import VideoRenderer  # noqa: E402


WIDTH = 720
HEIGHT = 1280
FPS = 25
DURATION = 30
MOOD = "雅致"

SHOTS = (
    ("shot_1", "苏堤春晓", "苏堤新柳拂过薄雾里的西湖，游船还没出港。", "苏堤春晓，柳色初齐。"),
    ("shot_2", "断桥残雪", "断桥石栏还留着一层薄霜，远处保俶塔隐在晨雾中。", "断桥残雪，塔影依稀。"),
    ("shot_3", "白堤闲步", "白堤两侧桃花才露一点粉，行人走得很慢。", "白堤闲步，花事未浓。"),
    ("shot_4", "三潭印月", "三座石塔立在湖心，倒影被细浪轻轻拉开。", "三潭印月，塔影入波。"),
    ("shot_5", "雷峰夕照", "雷峰塔在暖色里慢慢沉下去，湖面接住最后一点光。", "雷峰夕照，余光停在湖上。"),
    ("shot_6", "平湖秋月", "平湖秋月的石栏外，一轮淡月已经悬在对岸山影上。", "平湖秋月，月色刚刚落下。"),
)


def _probe(path: Path, select: str) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            select,
            "-show_entries",
            "format=duration" if select == "a" else "stream=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    values = []
    for line in (result.stdout or "").splitlines():
        try:
            values.append(float(line.strip()))
        except ValueError:
            continue
    return max(values) if values else 0.0


def _probe_container(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        return float((result.stdout or "").strip())
    except ValueError:
        return 0.0


def _srt_last_end(path: Path) -> float:
    text = path.read_text(encoding="utf-8")
    last = 0.0
    for line in text.splitlines():
        if "-->" not in line:
            continue
        stamp = line.split("-->", 1)[1].strip().split()[0]
        hours, minutes, rest = stamp.split(":")
        seconds, millis = rest.split(",")
        value = int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
        last = max(last, value)
    return round(last, 3)


def _fail(reason: str) -> int:
    print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
    return 1


def main() -> int:
    settings = Settings()
    out_dir = settings.media_dir / "accept_30s"
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    task_input = TaskInput.from_dict(
        {
            "city": "杭州",
            "landmark": "西湖",
            "theme": "西湖六景",
            "culture": "宋韵",
            "duration": DURATION,
            "style": "诗意纪实",
            "voice": "女声",
            "music_mood": MOOD,
            "shot_count": 6,
            "asset_source": "offline",
            "output_resolution": f"{WIDTH}x{HEIGHT}",
            "fps": FPS,
            "include_subtitles": True,
            "include_ai_label": True,
        }
    )
    shots = []
    for index, (shot_id, title, visual, narration) in enumerate(SHOTS, start=1):
        shots.append(
            Shot(
                id=shot_id,
                order=index,
                duration_sec=round(DURATION / len(SHOTS), 3),
                title=title,
                visual_prompt=visual,
                narration=narration,
                subtitle=narration,
                transition=("fade", "dissolve", "slideleft", "slideright", "zoom", "fade")[index - 1],
            )
        )
    task = Task(
        id="accept_30s",
        input=task_input,
        title="西湖六景",
        narration="".join(item[3] for item in SHOTS),
        shots=shots,
    )

    try:
        audio = build_audio(
            task.narration,
            task.input.voice,
            task.input.voice_rate,
            task.input.music_mood,
            [shot.__dict__.copy() for shot in task.shots],
            out_dir,
            settings.music_dir,
        )
    except Exception as exc:
        return _fail(f"edge-tts 不可用，不用静音代替：{exc}")

    if str(audio.event.get("provider") or "") != "edge-tts":
        return _fail(
            "配音没有走 edge-tts（"
            f"{audio.event.get('provider')}），拒绝用其他声音假装对齐"
        )
    if not audio.cues:
        return _fail("edge-tts 没有返回 cues，不能假装字幕和声音对齐")

    cues = [
        {"shot_id": cue.shot_id, "start": cue.start, "end": cue.end, "text": cue.text}
        for cue in audio.cues
    ]
    shot_sum = Orchestrator.apply_audio_durations(task, cues, float(audio.total_duration))
    if abs(shot_sum - DURATION) > 0.05:
        return _fail(f"镜头时长没有锁到 {DURATION} 秒，实际 {shot_sum}")

    provider = OfflineAssetProvider()
    render_shots = []
    for shot in task.shots:
        if float(shot.duration_sec) <= 0:
            continue
        asset = provider.create_or_find(
            task.id,
            {
                "id": shot.id,
                "order": shot.order,
                "title": shot.title,
                "visual_prompt": shot.visual_prompt,
            },
            assets_dir,
            "#24526a",
            "#d6ad60",
        )
        shot.asset_local_path = asset["local_path"]
        shot.asset_media_type = "image"
        render_shots.append(shot.__dict__.copy())

    task.audio = {
        "voice_local_path": audio.voice_path,
        "music_local_path": audio.music_path,
        "music_file": Path(audio.music_path).name if audio.music_path else "",
        "voice_provider": audio.event.get("provider", ""),
        "cues": cues,
        "duck": audio.event.get("mix") if isinstance(audio.event.get("mix"), dict) else {},
        "requested_duration": float(DURATION),
    }
    try:
        VideoRenderer(settings.media_dir).render(
            task.id,
            render_shots,
            task.audio,
            task.input.include_ai_label,
            task.input.include_subtitles,
            task.input.output_resolution,
            task.input.fps,
            task.input.voice_volume,
            task.input.music_volume,
        )
    except Exception as exc:
        return _fail(f"VideoRenderer.render 失败：{exc}")

    final_path = out_dir / "final.mp4"
    srt_path = out_dir / "subtitles.srt"
    if not final_path.is_file() or not srt_path.is_file():
        return _fail("成片或字幕没有写到 runtime/media/accept_30s")

    container = _probe_container(final_path)
    video = _probe(final_path, "v:0")
    audio_sec = _probe(final_path, "a:0")
    srt_last = _srt_last_end(srt_path)
    report = {
        "path": str(final_path),
        "container_sec": round(container, 3),
        "video_sec": round(video, 3),
        "audio_sec": round(audio_sec, 3),
        "srt_last_end": srt_last,
        "shot_sum": round(shot_sum, 3),
        "music_file": task.audio["music_file"],
        "voice_provider": task.audio["voice_provider"],
    }
    print(json.dumps(report, ensure_ascii=False))
    if srt_last > DURATION + 0.001:
        return 1
    if not all(29.95 <= value <= 30.05 for value in (container, video, audio_sec)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
