from __future__ import annotations

import json
import subprocess
from math import gcd
from pathlib import Path
from typing import Any

from ..providers.media_ffmpeg import (
    AIGC_LABEL_TEXT,
    aigc_label_filter,
    create_srt,
    find_cjk_font,
    is_video_media,
    run_ffmpeg,
    shot_filter,
    transition_filter,
)


# 成片编码质量。成片要经过逐镜渲染 → 拼接 → 烧字幕三道重编码，
# 每道都用同一组参数，否则最后一道会把前面的画质抹掉。
# 想更高画质就调低 VIDEO_CRF；现场渲染太慢就把 VIDEO_PRESET 调回 veryfast。
VIDEO_CRF = "18"
VIDEO_PRESET = "medium"


# 旁白出现时 BGM 的处理方式。默认"不压低"：两条轨道各自已经调好音量
# （默认 music_volume=0.18 比人声低约 15dB，人声本来就不会被盖住），
# 再叠 sidechaincompress 会让音乐忽大忽小。需要时可在任务里选"轻微/标准"。
MUSIC_DUCKING_MODES = ("不压低", "轻微", "标准")
DEFAULT_MUSIC_DUCKING = "不压低"


def music_ducking(duck: dict[str, Any] | None) -> tuple[str, int]:
    """从调用方给的 duck 配置取出压低模式与释放时间（毫秒）。缺省不压低。"""
    mode = DEFAULT_MUSIC_DUCKING
    release = 0.35
    if isinstance(duck, dict):
        candidate = str(duck.get("mode") or "").strip()
        if candidate in MUSIC_DUCKING_MODES:
            mode = candidate
        try:
            release = float(duck.get("release_sec", release))
        except (TypeError, ValueError):
            release = 0.35
    return mode, max(50, int(round(release * 1000)))


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
                VIDEO_PRESET,
                "-crf",
                VIDEO_CRF,
                str(output),
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Shot render failed: {source.name}")

    def _burn_overlays(
        self, output_dir: Path, source: Path, output: Path, filters: list[str]
    ) -> None:
        """把字幕和 AIGC 标识烧进画面。一次重编码搞定，避免多烧一道掉一次画质。

        Run from the task directory so the subtitles filter can use an ASCII
        relative path on Windows without escaping drive letters.
        """
        result = run_ffmpeg(
            [
                "-i",
                source.name,
                "-vf",
                ",".join(filters),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                VIDEO_PRESET,
                "-crf",
                VIDEO_CRF,
                "-an",
                "-fps_mode",
                "cfr",
                output.name,
            ],
            cwd=output_dir,
        )
        if result.returncode != 0 or not output.exists():
            raise RuntimeError(result.stderr.strip() or "Subtitle/label burn-in failed")

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
        for index, (audio_path, _volume) in enumerate(audio_tracks):
            if index == 1:
                # 音乐输入循环。曲子比成片短时 apad 补的是静音（后半段没音乐），
                # -stream_loop -1 让它无缝续上，再由下面的 atrim 精确截到成片秒数。
                args.extend(["-stream_loop", "-1"])
            args.extend(["-i", str(audio_path)])

        if len(audio_tracks) == 1:
            filter_complex = (
                f"[1:a]volume={audio_tracks[0][1]:.3f},"
                f"apad=pad_dur={duration:.3f},atrim=duration={duration:.3f}[a]"
            )
        else:
            mode, release_ms = music_ducking(duck)
            music_volume = float(audio_tracks[1][1])
            # 淡入/淡出按成片时长收缩，短片不出现两段淡变重叠。
            fade_in = min(0.8, duration / 5)
            fade_out = min(2.0, duration / 3)
            fade_out_start = max(0.0, duration - fade_out)
            voice_chain = (
                f"[1:a]volume={audio_tracks[0][1]:.3f},"
                f"apad=pad_dur={duration:.3f},atrim=duration={duration:.3f}"
            )
            music_chain = (
                f"[2:a]volume={music_volume:.3f},"
                f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={fade_in:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}[music]"
            )
            if mode == "不压低":
                # 旁白与 BGM 的音量已经在各自轨道上调好（默认 BGM 比人声低约 15dB），
                # 再叠 sidechain 属于过度处理，会让音乐忽大忽小。
                filter_complex = (
                    f"{voice_chain}[voice];"
                    f"{music_chain};"
                    "[voice][music]amix=inputs=2:duration=longest:dropout_transition=0[a]"
                )
            else:
                params = {
                    "轻微": "threshold=0.05:ratio=2.5:attack=20",
                    "标准": "threshold=0.02:ratio=8:attack=20",
                }[mode]
                filter_complex = (
                    f"{voice_chain},asplit=2[voice][sc];"
                    f"{music_chain};"
                    f"[music][sc]sidechaincompress={params}:release={release_ms}[ducked];"
                    "[voice][ducked]amix=inputs=2:duration=longest:dropout_transition=0[a]"
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
        width, height = (int(value) for value in resolution.split("x", 1))
        divisor = gcd(width, height) or 1
        aspect_ratio = f"{width // divisor}:{height // divisor}"
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
        duck_mode, _ = music_ducking(duck)

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
        # tpad 的 stop_duration 必须按实际缺口算，不能写死 1 秒。镜头合计比请求秒数
        # 短超过 1 秒时，写死 1 秒补不满，成片就会短于用户选的秒数
        # （实测请求 15.0 秒、镜头合计 13.46 秒时出片只有 14.48 秒）。
        # 多给 1 秒冗余，最终仍由 trim / -frames:v 精确截到 locked_frames。
        pad_seconds = round(max(0.0, total_duration - shot_total) + 1.0, 3)
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
                    f"tpad=stop_mode=clone:stop_duration={pad_seconds:.3f},"
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
                "-preset",
                VIDEO_PRESET,
                "-crf",
                VIDEO_CRF,
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
        # 字幕和 AIGC 标识都在这一道烧进去。标识是合规要求，不能因为用户关掉字幕
        # 就不烧；缺中文字体时跳过并在 manifest 里记 label_burned=false。
        overlay_filters: list[str] = []
        if include_subtitles:
            overlay_filters.append("subtitles=subtitles.srt")
        label_text = AIGC_LABEL_TEXT if include_ai_label else ""
        label_font = find_cjk_font() if label_text else None
        label_burned = bool(label_text and label_font)
        if label_burned:
            overlay_filters.append(
                aigc_label_filter(label_text, width, height, label_font)
            )
        visual_video = silent_video
        if overlay_filters:
            overlaid = output_dir / "overlaid.mp4"
            self._burn_overlays(output_dir, silent_video, overlaid, overlay_filters)
            visual_video = overlaid

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
                    "-preset",
                    VIDEO_PRESET,
                    "-crf",
                    VIDEO_CRF,
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
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "has_audio": "audio" in streams,
            "has_burned_subtitles": include_subtitles,
            "has_burned_aigc_label": label_burned,
            "aigc_label_font": str(label_font) if label_burned else "",
            "total_duration": total_duration,
            # 混音策略与时间轴口径，随 manifest 一并留档。名字要反映实际做法：
            # 不压低时用 music-constant，不能一律写成 sidechain。
            "mix_strategy": (
                ("music-constant" if duck_mode == "不压低" else "music-ducked-sidechain")
                if len(audio_tracks) >= 2
                else "voice-only" if len(audio_tracks) == 1
                else "none"
            ),
            "music_ducking": duck_mode if len(audio_tracks) >= 2 else "",
            "timing_source": audio.get("timing_source", ""),
            "target_duration": audio.get("target_duration", ""),
            "cues": raw_cues if isinstance(raw_cues, list) else [],
            "music_match": audio.get("music_match", None),
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
