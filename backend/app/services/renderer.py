from __future__ import annotations

import json
import subprocess
from math import gcd, isfinite
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
    XFADE_PRESETS,
)
from ..providers.focus import detect_focus
from ..providers.subtitles import create_ass


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

# 成片响度目标。短视频平台普遍把响度拉齐到 -14~-16 LUFS，取 -16 是为了留出
# AAC 转码与平台二次归一化的余量；真峰值留 1.5dB 避免转码后削顶。
TARGET_LUFS = -16.0
MAX_TRUE_PEAK = -1.5
# 补增益的上下限。超出说明源素材本身有问题（全程静音或几乎满刻度），
# 与其硬拉到目标，不如留原样并在 manifest 里记下实测值。
MIN_GAIN_DB = -12.0
MAX_GAIN_DB = 18.0


def measure_loudness(path: Path) -> dict[str, float | None]:
    """用 loudnorm 的分析模式量一遍整轨响度，只读不写。

    run_ffmpeg 会把日志压到 error 级，而 loudnorm 的 JSON 是 info 级输出，
    所以这里直接调 subprocess，保留 stderr 再抓最后一段 JSON。
    """
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af",
        f"loudnorm=I={TARGET_LUFS}:TP={MAX_TRUE_PEAK}:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    text = result.stderr or ""
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}

    def number(key: str) -> float | None:
        try:
            return float(data.get(key))
        except (TypeError, ValueError):
            return None

    return {"input_i": number("input_i"), "input_tp": number("input_tp")}


def loudness_gain(loudness: dict[str, float | None]) -> float:
    """按实测响度算出固定增益，同时保证补完不超真峰值上限。"""
    measured = loudness.get("input_i")
    if measured is None or not isfinite(measured):
        return 0.0
    gain = TARGET_LUFS - measured
    peak = loudness.get("input_tp")
    if peak is not None and isfinite(peak):
        gain = min(gain, MAX_TRUE_PEAK - peak)
    return max(MIN_GAIN_DB, min(MAX_GAIN_DB, gain))


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
        focus: tuple[float, float] = (0.5, 0.5),
        motion: str = "push",
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
        visual = shot_filter(
            width, height, duration, fps, video=video, grade=grade,
            focus=focus, motion=motion,
        )
        if video:
            visual += f",tpad=stop_mode=clone:stop_duration={duration:.3f}"
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
    ) -> dict[str, Any]:
        """先混出人声+BGM 的 premix，再按响度目标补增益，最后封进 MP4。

        分两步是因为 amix 默认 normalize=1，会把每路都除以输入数：带 BGM 时
        人声会被平白压低 6dB，而纯人声时又不会，成片响度全看有没有音乐。
        这里改成 normalize=0，混完再统一按目标响度补一个固定增益（不是动态
        压限，避免 15 秒短片里的抽气感），并留 alimiter 兜住峰值。
        """
        mix_path = final_path.parent / "audio-mix.wav"
        premix_args: list[str] = []
        chains: list[str] = []
        for index, (audio_path, volume) in enumerate(audio_tracks):
            if index >= 1:
                # 音乐输入循环。曲子比成片短时 -stream_loop -1 让它无缝续上，
                # 再由 atrim 精确截到成片秒数。统一转 48k 立体声，避免 amix
                # 因为声道布局不同而拒绝混音。
                premix_args.extend(["-stream_loop", "-1"])
            premix_args.extend(["-i", str(audio_path)])
            chains.append(
                f"[{index}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
                f"channel_layouts=stereo,volume={float(volume):.3f}"
            )

        if len(audio_tracks) == 1:
            premix_filter = (
                f"{chains[0]},apad=pad_dur={duration:.3f},"
                f"atrim=duration={duration:.3f}[a]"
            )
        else:
            mode, release_ms = music_ducking(duck)
            # 淡入/淡出按成片时长收缩，短片不出现两段淡变重叠。
            fade_in = min(0.8, duration / 5)
            fade_out = min(2.0, duration / 3)
            fade_out_start = max(0.0, duration - fade_out)
            voice_chain = (
                f"{chains[0]},apad=pad_dur={duration:.3f},atrim=duration={duration:.3f}"
            )
            music_chain = (
                f"{chains[1]},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={fade_in:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}[music]"
            )
            if mode == "不压低":
                # 旁白与 BGM 的音量已经在各自轨道上调好（默认 BGM 比人声低约 15dB），
                # 再叠 sidechain 属于过度处理，会让音乐忽大忽小。
                premix_filter = (
                    f"{voice_chain}[voice];"
                    f"{music_chain};"
                    "[voice][music]amix=inputs=2:duration=longest:"
                    "dropout_transition=0:normalize=0[a]"
                )
            else:
                params = {
                    "轻微": "threshold=0.05:ratio=2.5:attack=20",
                    "标准": "threshold=0.02:ratio=8:attack=20",
                }[mode]
                premix_filter = (
                    f"{voice_chain},asplit=2[voice][sc];"
                    f"{music_chain};"
                    f"[music][sc]sidechaincompress={params}:release={release_ms}[ducked];"
                    "[voice][ducked]amix=inputs=2:duration=longest:"
                    "dropout_transition=0:normalize=0[a]"
                )

        premix = run_ffmpeg(
            [
                *premix_args,
                "-filter_complex",
                premix_filter,
                "-map",
                "[a]",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(mix_path),
            ]
        )
        if premix.returncode != 0:
            raise RuntimeError(premix.stderr.strip() or "Audio premix failed")

        # premix 存在才量；缺文件说明 ffmpeg 行为异常，交给后面的 mux 报错，
        # 不在这里猜测（也让只验证参数拼装的测试不必真跑 ffmpeg）。
        loudness = measure_loudness(mix_path) if mix_path.is_file() else {}
        gain_db = loudness_gain(loudness)

        # -t 在非整数帧边界会向上取整（25fps 下 15.000 仍可能变成 15.040）。
        # 用帧数锁住画面，音频仍按请求秒数 apad/atrim，不把成片拉长。
        locked_frames = max(1, int(round(duration * fps)))
        result = run_ffmpeg(
            [
                "-i",
                str(visual_video),
                "-i",
                str(mix_path),
                "-filter_complex",
                f"[1:a]volume={gain_db:.2f}dB:precision=float,"
                "alimiter=limit=0.891:level=disabled[a]",
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
        mix_path.unlink(missing_ok=True)
        return {
            "target_lufs": TARGET_LUFS,
            "input_lufs": loudness.get("input_i"),
            "input_true_peak": loudness.get("input_tp"),
            "gain_db": round(gain_db, 2),
            "amix_normalize": 0,
        }

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

        active_shots = [shot for shot in shots if float(shot["duration_sec"]) > 0]
        if not active_shots:
            raise RuntimeError("No shot has a positive duration")
        manifest_shots = []
        focus_records: list[dict[str, Any]] = []
        for index, shot in enumerate(active_shots):
            manifest_shots.append({
                "id": shot.get("id", ""), "order": shot.get("order", index + 1),
                "title": shot.get("title", ""), "duration_sec": float(shot["duration_sec"]),
                "entity_id": shot.get("entity_id", ""), "library_asset_id": shot.get("library_asset_id", ""),
                "shot_tag": shot.get("shot_tag", ""), "provenance": shot.get("provenance", {}),
            })
        base_frames = [max(1, round(float(shot["duration_sec"]) * fps)) for shot in active_shots]
        locked_frames = max(1, round(total_duration * fps))
        transition_frames: list[int] = []
        for left, right in zip(base_frames, base_frames[1:]):
            # Even counts split equally across the nominal boundary. Keep
            # short shots legible and preserve the voice/subtitle timeline.
            count = min(round(0.56 * fps), left // 3, right // 3)
            transition_frames.append(max(2, count // 2 * 2))
        boundaries: list[int] = []
        elapsed_frames = 0
        for count in base_frames[:-1]:
            elapsed_frames += count
            boundaries.append(elapsed_frames)
        focus_records: list[dict[str, Any]] = []
        for index, shot in enumerate(active_shots):
            head = transition_frames[index - 1] // 2 if index else 0
            tail = transition_frames[index] // 2 if index < len(transition_frames) else 0
            extra = max(0, locked_frames - sum(base_frames)) if index == len(active_shots) - 1 else 0
            clip_frames = base_frames[index] + head + tail + extra
            duration = clip_frames / fps
            source = Path(shot["asset_local_path"])
            if not source.exists():
                raise RuntimeError(f"Asset does not exist: {source}")
            video = is_video_media(str(shot.get("asset_media_type") or ""), source)
            focus: tuple[float, float] = (0.5, 0.5)
            method = "video"
            if not video:
                x, y = shot.get("focus_x"), shot.get("focus_y")
                if x is not None and y is not None:
                    focus = (float(x), float(y))
                    method = "manual"
                else:
                    focus = detect_focus(source)
                    method = "saliency"
            mode = str(shot.get("motion") or "auto").lower()
            if mode == "auto":
                mode = ("push", "pull", "gentle")[index % 3]
            focus_records.append({
                "shot_id": shot["id"], "x": focus[0], "y": focus[1],
                "method": method, "motion": mode if not video else "native",
            })
            segment = output_dir / f"{shot['id']}.mp4"
            self._render_shot(
                source,
                segment,
                duration,
                resolution,
                fps,
                media_type=str(shot.get("asset_media_type") or ""),
                focus=focus,
                motion=mode,
                grade=active_grade,
                clip_start=float(shot.get("clip_start") or 0),
            )
            segment_paths.append(segment)
        silent_video = output_dir / "silent.mp4"
        graph: list[str] = []
        current = "0:v"
        transition_records: list[dict[str, Any]] = []
        for index, boundary in enumerate(boundaries):
            name = str(active_shots[index + 1].get("transition") or "dissolve").lower()
            preset = XFADE_PRESETS.get(name, "dissolve")
            count = transition_frames[index]
            offset = (boundary - count // 2) / fps
            output = f"x{index + 1}"
            graph.append(
                f"[{current}][{index + 1}:v]xfade=transition={preset}:"
                f"duration={count / fps:.6f}:offset={offset:.6f}[{output}]"
            )
            current = output
            transition_records.append({
                "from": active_shots[index]["id"],
                "to": active_shots[index + 1]["id"],
                "style": preset, "duration_sec": count / fps,
            })
        graph.append(
            f"[{current}]tpad=stop_mode=clone:stop_duration={total_duration + 1:.3f},"
            f"fps={fps},trim=end_frame={locked_frames},setpts=PTS-STARTPTS[v]"
        )
        inputs = [part for path in segment_paths for part in ("-i", str(path))]
        result = run_ffmpeg(
            [
                *inputs,
                "-filter_complex", ";".join(graph),
                "-map", "[v]",
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
            create_ass(srt_path, output_dir / "subtitles.ass", width, height)
            overlay_filters.append("subtitles=subtitles.ass")
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
        mix_info: dict[str, Any] = {}
        if audio_tracks:
            mix_info = self._mux_audio(
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
            "loudness": mix_info,
            "timing_source": audio.get("timing_source", ""),
            "target_duration": audio.get("target_duration", ""),
            "cues": raw_cues if isinstance(raw_cues, list) else [],
            "music_match": audio.get("music_match", None),
            "render_provider": "ffmpeg",
            "shots": manifest_shots,
            "focus_points": focus_records,
            "transitions": transition_records,
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
