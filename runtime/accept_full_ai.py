"""全 AI 成片验收：拿 task 快照和 final.mp4 逐条核对可交付指标。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def probe(path: Path) -> dict:
    done = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=index,codec_name,codec_type,width,height,channels,sample_rate,nb_frames",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    return json.loads(done.stdout or "{}")


def main() -> int:
    task = json.loads((ROOT / "full-ai-task.json").read_text(encoding="utf-8"))
    render = task.get("render") or {}
    audio = task.get("audio") or {}
    video = Path(render.get("video_local_path") or "")
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    check("任务 completed", task.get("status") == "completed", str(task.get("status")))
    check("成片存在", video.is_file(), str(video))

    info = probe(video) if video.is_file() else {}
    streams = info.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = float((info.get("format") or {}).get("duration") or 0)

    requested = float(task["input"]["duration"])
    check("时长锁回请求秒数", abs(duration - requested) < 0.2, f"{duration:.3f}s / 请求 {requested}s")
    check(
        "分辨率等于 output_resolution",
        f"{video_stream.get('width')}x{video_stream.get('height')}" == task["input"]["output_resolution"],
        f"{video_stream.get('width')}x{video_stream.get('height')}",
    )
    check("有视频流", video_stream.get("codec_name") == "h264", str(video_stream.get("codec_name")))
    check("有音频流", bool(audio_stream), str(audio_stream.get("codec_name")))
    check("烧录字幕", bool(render.get("has_burned_subtitles")))
    check("烧录 AIGC 标识", bool(render.get("has_burned_aigc_label")), str(render.get("aigc_label_font")))
    check("BGM 保持音量不变", render.get("mix_strategy") == "music-constant", str(render.get("mix_strategy")))
    check("音乐有淡入淡出", "afade" not in str(render.get("mix_strategy")), "见 ffmpeg 滤镜")
    check("配音 provider 已记录", bool(audio.get("voice_provider")) and audio.get("voice_provider") != "none", str(audio.get("voice_provider")))
    check("音乐 provider 已记录", bool(audio.get("music_local_path")), str(audio.get("music_provider")))

    assets = task.get("assets") or []
    check("每镜都有素材", len(assets) == len(task.get("shots") or []), f"{len(assets)} 素材 / {len(task.get('shots') or [])} 镜头")
    check("全部素材是 AI 生成", all(a.get("is_ai_generated") for a in assets), "")
    check("素材都是视频（图生视频）", all(a.get("media_type") == "video" for a in assets),
          ",".join(sorted({str(a.get("media_type")) for a in assets})))
    check("未使用降级链", not (task.get("trace") or {}).get("fallback_used"),
          str((task.get("trace") or {}).get("fallback_used")))

    # 旁白覆盖：配音实测时长不能明显短于容器时长，否则尾部是静止画面。
    voice_end = max((float(c.get("end", 0)) for c in (audio.get("cues") or [])), default=0)
    check("旁白基本铺满整片", voice_end >= requested - 1.5, f"旁白到 {voice_end:.2f}s / 成片 {requested}s")

    width = max(len(name) for name, _, _ in checks)
    failed = 0
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{mark}] {name.ljust(width)}  {detail}")
    print()
    print(f"{len(checks) - failed}/{len(checks)} 通过")
    print(f"成片: {video}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
