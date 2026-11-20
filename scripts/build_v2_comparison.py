"""Build and validate v2 exclusively from the frozen comparison inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_TASK_ID = "task_9f7631a202ca"
COMPARISON_CONFIG = {
    "voice": "女声",
    "voice_rate": 1.02,
    "target_duration": 30.0,
    "include_ai_label": True,
    "resolution": "720x1280",
    "fps": 25,
    "voice_volume": 1.0,
    "music_volume": 0.18,
}
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import settings  # noqa: E402
from backend.app.models import Shot  # noqa: E402
from backend.app.providers.audio_runtime import AudioRuntimeClient  # noqa: E402
from backend.app.providers.contract import CONTRACT_VERSION  # noqa: E402
from backend.app.services.audio_sync import PerShotAudioBuilder, probe_audio  # noqa: E402
from backend.app.services.renderer import VideoRenderer  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_media(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path.name
        ],
        cwd=path.parent,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffprobe failed")
    return json.loads(completed.stdout)


def timestamp_ms(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def validate_srt(cues: list[dict[str, Any]], srt_path: Path) -> None:
    text = srt_path.read_text(encoding="utf-8")
    timings = re.findall(
        r"(?m)^(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})$",
        text,
    )
    expected = [(timestamp_ms(float(cue["start"])), timestamp_ms(float(cue["end"]))) for cue in cues]
    if timings != expected:
        raise RuntimeError("v2 SRT timestamps do not match cues exactly")
    for cue in cues:
        if str(cue["text"]) not in text:
            raise RuntimeError(f"SRT is missing cue text for {cue['shot_id']}")


def validate_cues(cues: list[dict[str, Any]], traces: list[dict[str, Any]]) -> None:
    previous_end = 0.0
    trace_by_shot = {str(item["shot_id"]): item for item in traces}
    for cue in cues:
        start = float(cue["start"])
        end = float(cue["end"])
        if start < previous_end - 0.0005 or end <= start:
            raise RuntimeError("Cues are not monotonic and non-overlapping")
        trace = trace_by_shot[str(cue["shot_id"])]
        measured = probe_audio(Path(trace["segment_path"]))["duration"]
        if abs((end - start) - measured) > 0.05:
            raise RuntimeError(f"Cue boundary error exceeds 50 ms: {cue['shot_id']}")
        previous_end = end


def publish(source: Path, destination: Path) -> None:
    if source.parent.drive.lower() != destination.parent.drive.lower():
        raise RuntimeError("Atomic publication requires source and destination on the same volume")
    os.replace(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    new_dir = project_root / "new"
    frozen = new_dir / "comparison-inputs"
    required = [
        new_dir / "v1.mp4",
        new_dir / "v1.srt",
        frozen / "shots.json",
        frozen / "voice-original.wav",
        frozen / "ambient_demo_synth.aac",
    ] + [frozen / "assets" / f"shot_{index}.jpg" for index in range(1, 7)]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Frozen comparison baseline is incomplete: " + ", ".join(missing))

    shot_payloads = json.loads((frozen / "shots.json").read_text(encoding="utf-8"))
    shots: list[Shot] = []
    for raw in shot_payloads:
        values = dict(raw)
        values["asset_local_path"] = str((new_dir / values["asset_local_path"]).resolve())
        shots.append(Shot(**values))

    build_root = new_dir / f".v2-build-{uuid.uuid4().hex[:8]}"
    media_root = build_root / "media"
    output_dir = media_root / "v2"
    build_root.mkdir(parents=True, exist_ok=False)
    try:
        runtime = AudioRuntimeClient.from_settings(settings)
        audio_result = PerShotAudioBuilder(settings, runtime).build(
            "v2",
            shots,
            voice=COMPARISON_CONFIG["voice"],
            rate=COMPARISON_CONFIG["voice_rate"],
            target_duration=COMPARISON_CONFIG["target_duration"],
            output_dir=output_dir,
            force_provider="windows-system-speech",
        )
        # Comparison artifacts outlive the temporary render directory. Point
        # their trace at the persistent cache source instead of temp segments.
        for trace in audio_result.provider_trace:
            trace["segment_path"] = str(trace.get("source_path", trace["segment_path"]))
        music = frozen / "ambient_demo_synth.aac"
        audio_result.music_path = str(music.resolve())
        audio_result.music_match = {
            "id": "ambient-demo-synth-frozen-baseline",
            "path": str(music.resolve()),
            "moods": ["舒缓"],
            "styles": ["诗意纪实"],
            "license": "Project-owned technical comparison fixture",
            "author": "Zhejiang Tourism AIGC project",
            "usage_scope": "v1-v2 competition technical comparison",
            "approved": True,
            "provider": "frozen-comparison-input",
            "model": "metadata-exact-match-v1",
        }
        audio_result.validate()
        audio = audio_result.to_dict()
        audio.update(
            {
                "voice_local_path": audio_result.voice_path,
                "music_local_path": audio_result.music_path,
                "duration_sec": audio_result.total_duration,
                "voice_provider": "windows-system-speech",
                "voice_model": "installed-system-voice",
                "music_provider": "frozen-comparison-input",
                "contract_version": CONTRACT_VERSION,
            }
        )

        rendered = VideoRenderer(media_root).render(
            "v2",
            [shot.__dict__.copy() for shot in shots],
            audio,
            COMPARISON_CONFIG["include_ai_label"],
            True,
            COMPARISON_CONFIG["resolution"],
            COMPARISON_CONFIG["fps"],
            COMPARISON_CONFIG["voice_volume"],
            COMPARISON_CONFIG["music_volume"],
        )
        final_video = output_dir / "final.mp4"
        srt = output_dir / "subtitles.srt"
        cues = [dict(item) for item in audio["cues"]]
        validate_cues(cues, audio_result.provider_trace)
        validate_srt(cues, srt)

        ffprobe = probe_media(final_video)
        streams = ffprobe.get("streams", [])
        video_stream = next(item for item in streams if item.get("codec_type") == "video")
        audio_stream = next(item for item in streams if item.get("codec_type") == "audio")
        final_duration = float(ffprobe["format"]["duration"])
        if abs(final_duration - audio_result.total_duration) > 0.05:
            raise RuntimeError("v2 final duration differs from AudioResult.total_duration")
        expected_resolution = COMPARISON_CONFIG["resolution"]
        width, height = (int(value) for value in expected_resolution.split("x"))
        validations = {
            "cues_monotonic_non_overlapping": True,
            "cue_segment_error_lte_50ms": True,
            "srt_matches_cues_to_millisecond": True,
            "duration_error_lte_50ms": True,
            "same_frozen_assets": all(Path(shot.asset_local_path).is_relative_to(frozen.resolve()) for shot in shots),
            "same_frozen_bgm": Path(audio_result.music_path).resolve() == music.resolve(),
            "video_codec_h264": video_stream.get("codec_name") == "h264",
            "audio_codec_aac": audio_stream.get("codec_name") == "aac",
            "audio_48khz": int(audio_stream.get("sample_rate", 0)) == 48000,
            "resolution_matches": int(video_stream.get("width", 0)) == width and int(video_stream.get("height", 0)) == height,
            "fps_matches": video_stream.get("avg_frame_rate") == f"{COMPARISON_CONFIG['fps']}/1",
            "voice_provider_is_windows_system_speech": all(
                item.get("provider") == "windows-system-speech"
                for item in audio_result.provider_trace
            ),
        }
        if not all(validations.values()):
            failed = [name for name, ok in validations.items() if not ok]
            raise RuntimeError("v2 validation failed: " + ", ".join(failed))

        input_hashes = {
            "bgm": sha256(music),
            "assets": {path.name: sha256(path) for path in sorted((frozen / "assets").glob("*"))},
            "shots_json": sha256(frozen / "shots.json"),
            "v1_original_voice": sha256(frozen / "voice-original.wav"),
        }
        manifest = {
            **rendered,
            "schema": "zj-v2-comparison-manifest-v1",
            "contract_version": CONTRACT_VERSION,
            "source_task_id": SOURCE_TASK_ID,
            "comparison_version": "v2",
            "target_duration": COMPARISON_CONFIG["target_duration"],
            "actual_duration": audio_result.total_duration,
            "timing_strategy": "measured-per-shot-audio-cues",
            "mix_strategy": "sidechaincompress-asplit-2",
            "silence_between_shots_ms": 150,
            "input_hashes": input_hashes,
            "audio_result": audio,
            "validations": validations,
        }
        cues_payload = {
            "schema": "zj-cues-v1",
            "timing_source": audio_result.timing_source,
            "target_duration": audio_result.target_duration,
            "total_duration": audio_result.total_duration,
            "silence_between_shots_ms": 150,
            "cues": cues,
            "segments": audio_result.provider_trace,
        }
        report = f"""# v1 / v2 音画同步对照

| 项目 | v1 | v2 |
|---|---|---|
| 时间轴 | 名义镜头时间轴，每镜 5 秒 | 逐镜配音实测 cues，镜间静音 150 ms |
| 成片时长 | 30.000 秒 | {audio_result.total_duration:.3f} 秒 |
| 字幕 | 按镜头名义时长生成 | 与 cue 起止时间逐毫秒一致 |
| 画面 | 固定 6 镜、共 30 秒 | 镜头时长由对应配音和间隔反向更新 |
| BGM | 固定音量 0.18，无 ducking | 同一 BGM、同一音量上限，旁白期间 sidechain ducking |
| 配音 | 整轨 Windows 系统配音 | 同一 Windows 系统语音、同一逐镜文本，逐镜生成 |
| 输入画面/BGM | 冻结基线 | 与 v1 使用同一冻结文件及 SHA-256 |

## 自动验收

{chr(10).join(f'- {"通过" if ok else "失败"}：`{name}`' for name, ok in validations.items())}

## 工程验收

主项目的编译、前端语法、测试、HTTP smoke、音频运行时和迁移幂等性由
`implementation-validation.json` 单独记录；重新构建 v2 后应重新执行这些门禁。

## 人工试听检查

- 对比 v1 是否存在字幕提前或滞后。
- 检查 v2 字幕只覆盖实际发声区间，150 ms 镜间静音不显示字幕。
- 对比 v1 固定混音与 v2 旁白期间 BGM 自动降低、间隙恢复是否自然。
"""

        artifacts = {
            "v2.mp4": final_video,
            "v2.srt": srt,
            "v2-cues.json": build_root / "v2-cues.json",
            "v2-manifest.json": build_root / "v2-manifest.json",
            "v2-ffprobe.json": build_root / "v2-ffprobe.json",
            "v1-v2-comparison.md": build_root / "v1-v2-comparison.md",
        }
        artifacts["v2-cues.json"].write_text(json.dumps(cues_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts["v2-manifest.json"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts["v2-ffprobe.json"].write_text(json.dumps(ffprobe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts["v1-v2-comparison.md"].write_text(report, encoding="utf-8")
        for name, source in artifacts.items():
            publish(source, new_dir / name)

        receipt = {
            "ok": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "v2_duration": audio_result.total_duration,
            "v2_sha256": sha256(new_dir / "v2.mp4"),
            "artifacts": {name: str(new_dir / name) for name in artifacts},
            "validations": validations,
        }
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    finally:
        if build_root.parent.resolve() == new_dir.resolve() and build_root.name.startswith(".v2-build-"):
            shutil.rmtree(build_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
