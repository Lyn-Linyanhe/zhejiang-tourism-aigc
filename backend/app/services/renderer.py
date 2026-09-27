from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..providers.media_ffmpeg import (
    create_srt,
    is_video_media,
    run_ffmpeg,
    shot_filter,
    transition_filter,
)


def _probe_stream_types(path: Path) -> set[str]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _is_video_asset(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


class VideoRenderer:
    def __init__(self, media_root: Path) -> None:
        self.media_root = media_root

    def _render_shot(
        self,
        source: Path,
        output: Path,
        duration: float,
        resolution: str,
        fps: int,
        *,
        media_type: str = "",
        transition: str = "fade",
        grade: dict[str, Any] | None = None,
        clip_start: float = 0.0,
    ) -> None:
        width, height = (int(value) for value in resolution.split("x", 1))
        video = is_video_media(media_type, source) if media_type else _is_video_asset(source)
        if video:
            # 实拍从素材中段起用，避开片头标题和片尾黑场。
            try:
                start = max(0.0, float(clip_start or 0))
            except (TypeError, ValueError):
                start = 0.0
            # -an 放在输出侧。这里不保留素材原声，避免游船环境声盖过旁白。
            input_args = ["-ss", f"{start:.3f}", "-an", "-i", str(source)]
        else:
            # 先铺成一段静帧，再做运镜。-loop 1 会让按帧计数的滤镜每帧重置。
            input_args = ["-loop", "1", "-framerate", str(fps), "-i", str(source)]
        visual = shot_filter(width, height, duration, fps, video=video, grade=grade)
        motion = transition_filter(transition, duration, width, height, fps)
        if motion:
            visual = f"{visual},{motion}"
        result = run_ffmpeg(
            [
                *input_args,
                "-t",
                f"{duration:.3f}",
                "-vf",
                visual,
                "-r",
                str(fps),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "veryfast",
                "-crf",
                "24",
                str(output),
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Shot render failed: {source.name}")

    def _burn_subtitles(self, output_dir: Path, silent_video: Path, output: Path) -> None:
        # Run from the task directory so the subtitles filter can use an ASCII
        # relative path on Windows without escaping drive letters.
        result = run_ffmpeg(
            [
                "-i",
                "silent.mp4",
                "-vf",
                "subtitles=subtitles.srt",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "veryfast",
                "-crf",
                "24",
                "-an",
                "-vsync",
                "cfr",
                "subtitled.mp4",
            ],
            cwd=output_dir,
        )
        if result.returncode != 0 or not output.exists():
            raise RuntimeError(result.stderr.strip() or "Subtitle burn-in failed")

    def _mux_audio(
        self,
        visual_video: Path,
        audio_tracks: list[tuple[Path, float]],
        final_path: Path,
        duration: float,
        duck: dict[str, Any] | None = None,
        fps: int = 25,
    ) -> None:
        args = ["-i", str(visual_video)]
        for audio_path, _volume in audio_tracks:
            args.extend(["-i", str(audio_path)])

        if len(audio_tracks) == 1:
            filter_complex = (
                f"[1:a]volume={audio_tracks[0][1]:.3f},"
                f"apad=pad_dur={duration:.3f},atrim=duration={duration:.3f}[a]"
            )
        else:
            # 旁白说话时压低 BGM。音量和释放时间优先用调用方传入的 duck，
            # 没有就用 P3 约定的 0.18 / 0.35。sidechaincompress 不改变时长。
            ducked = 0.18
            release = 0.35
            if duck:
                try:
                    ducked = float(duck.get("music_volume", ducked))
                except (TypeError, ValueError):
                    ducked = 0.18
                try:
                    release = float(duck.get("release_sec", release))
                except (TypeError, ValueError):
                    release = 0.35
            # 没有 duck 时沿用调用方传入的音量，避免把已经调好的 music_volume 再压一次。
            music_volume = float(audio_tracks[1][1])
            if duck and duck.get("music_volume") not in (None, ""):
                music_volume = ducked
            release_ms = max(50, int(round(release * 1000)))
            filter_complex = (
                f"[1:a]volume={audio_tracks[0][1]:.3f},apad=pad_dur={duration:.3f},asplit=2[voice][sc];"
                f"[2:a]volume={music_volume:.3f},apad=pad_dur={duration:.3f}[music];"
                f"[music][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release={release_ms}[ducked];"
                "[voice][ducked]amix=inputs=2:duration=longest:dropout_transition=0,"
                f"atrim=duration={duration:.3f}[a]"
            )
        # -t 在非整数帧边界会向上取整（25fps 下 15.000 仍可能变成 15.040）。
        # 用帧数锁住画面，音频仍按请求秒数 apad/atrim，不把成片拉长。
        locked_frames = max(1, int(round(duration * fps)))
        result = run_ffmpeg(
            [
                *args,
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-frames:v",
                str(locked_frames),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(fps),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(final_path),
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Audio mux failed")

    def render(
        self,
        task_id: str,
        shots: list[dict[str, Any]],
        audio: dict[str, Any],
        include_ai_label: bool,
        include_subtitles: bool = True,
        resolution: str = "720x1280",
        fps: int = 25,
        voice_volume: float = 1.0,
        music_volume: float = 0.18,
        grade: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        output_dir = self.media_root / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        segment_paths: list[Path] = []
        # 成片时长以用户选择的秒数为准。镜头之和若被配音拉长，这里裁回请求秒数；
        # 镜头之和更短时，尾部由下面的 tpad 补到请求秒数，不把停顿裁掉。
        shot_total = sum(max(0.0, float(shot["duration_sec"])) for shot in shots)
        requested = audio.get("requested_duration") if isinstance(audio, dict) else None
        try:
            requested_duration = float(requested) if requested not in (None, "") else 0.0
        except (TypeError, ValueError):
            requested_duration = 0.0
        if requested_duration > 0:
            total_duration = requested_duration
        else:
            total_duration = shot_total
        # 调用方暂时不传 grade 时，用 audio 里透传的，再没有就交给滤镜用默认值。
        active_grade = grade if grade is not None else audio.get("grade")
        if not isinstance(active_grade, dict):
            active_grade = None
        duck = audio.get("duck") if isinstance(audio.get("duck"), dict) else None
        if duck is None:
            event = audio.get("event")
            if isinstance(event, dict) and isinstance(event.get("mix"), dict):
                duck = event["mix"]

        for shot in shots:
            duration = float(shot["duration_sec"])
            if duration <= 0:
                # 没有配音时间轴的镜头不进成片，避免把名义时长再加一遍。
                continue
            source = Path(shot["asset_local_path"])
            if not source.exists():
                raise RuntimeError(f"Asset does not exist: {source}")
            segment = output_dir / f"{shot['id']}.mp4"
            self._render_shot(
                source,
                segment,
                duration,
                resolution,
                fps,
                media_type=str(shot.get("asset_media_type") or ""),
                transition=str(shot.get("transition") or "cut"),
                grade=active_grade,
                clip_start=float(shot.get("clip_start") or 0),
            )
            segment_paths.append(segment)
        if not segment_paths:
            raise RuntimeError("No shot has a positive duration")

        concat_file = output_dir / "concat.txt"
        concat_file.write_text(
            "\n".join(
                f"file '{path.as_posix().replace(chr(39), chr(39) + chr(92) + chr(39))}'"
                for path in segment_paths
            ),
            encoding="utf-8",
        )
        silent_video = output_dir / "silent.mp4"
        # concat demuxer + 重编码，不用 xfade。每段 -t 会按帧向上取整，
        # 四段加起来会比请求秒数长一到两帧。这里按帧数把成片锁回各镜头时长之和，
        # 不足的尾部用最后一帧补上，不跨段重叠。
        locked_frames = max(1, int(round(total_duration * fps)))
        result = run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-vf",
                (
                    f"tpad=stop_mode=clone:stop_duration=1,"
                    f"fps={fps},"
                    f"trim=end_frame={locked_frames},"
                    f"setpts=PTS-STARTPTS"
                ),
                "-frames:v",
                str(locked_frames),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(fps),
                "-an",
                str(silent_video),
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Shot concat failed")

        srt_path = output_dir / "subtitles.srt"
        raw_cues = audio.get("cues") if isinstance(audio, dict) else None
        # 字幕用 cue 的绝对时间。画面被 -t 裁到 total_duration 时，
        # 超出画面的那一句必须收进画面内，不能写到画面结束之后。
        create_srt(
            shots,
            srt_path,
            raw_cues if isinstance(raw_cues, list) else None,
            picture_end=total_duration,
        )
        subtitled = output_dir / "subtitled.mp4"
        visual_video = silent_video
        if include_subtitles:
            self._burn_subtitles(output_dir, silent_video, subtitled)
            visual_video = subtitled

        audio_tracks: list[tuple[Path, float]] = []
        voice_path = Path(str(audio.get("voice_local_path", "")).strip())
        music_path = Path(str(audio.get("music_local_path", "")).strip())
        if audio.get("voice_local_path") and voice_path.exists():
            audio_tracks.append((voice_path, float(voice_volume)))
        if audio.get("music_local_path") and music_path.exists():
            audio_tracks.append((music_path, float(music_volume)))
        final_path = output_dir / "final.mp4"
        if audio_tracks:
            self._mux_audio(
                visual_video,
                audio_tracks,
                final_path,
                total_duration,
                duck=duck,
                fps=fps,
            )
        else:
            locked_frames = max(1, int(round(total_duration * fps)))
            result = run_ffmpeg(
                [
                    "-i",
                    str(visual_video),
                    "-frames:v",
                    str(locked_frames),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    str(fps),
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(final_path),
                ]
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Final video copy failed")

        streams = _probe_stream_types(final_path)
        if "video" not in streams:
            raise RuntimeError("Final video has no video stream")
        if audio_tracks and "audio" not in streams:
            raise RuntimeError("An audio track was requested but final video has no audio stream")

        cover = output_dir / "cover.jpg"
        cover_result = run_ffmpeg(
            ["-i", str(final_path), "-frames:v", "1", "-q:v", "3", str(cover)]
        )
        metadata = {
            "video_url": f"/media/{task_id}/final.mp4",
            "subtitle_url": f"/media/{task_id}/subtitles.srt",
            "cover_url": f"/media/{task_id}/cover.jpg" if cover_result.returncode == 0 else "",
            "video_local_path": str(final_path),
            "format": "MP4/H.264/AAC" if "audio" in streams else "MP4/H.264",
            "aspect_ratio": "9:16",
            "resolution": resolution,
            "has_audio": "audio" in streams,
            "has_burned_subtitles": include_subtitles,
            "render_provider": "ffmpeg",
            "rendered_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "aigc_label": "本作品包含 AI 生成或合成内容" if include_ai_label else "",
        }
        (output_dir / "manifest.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return metadata
