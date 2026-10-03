from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..providers.media_ffmpeg import create_srt, run_ffmpeg


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
    ) -> None:
        width, height = (int(value) for value in resolution.split("x", 1))
        if _is_video_asset(source):
            input_args = ["-stream_loop", "-1", "-i", str(source)]
        else:
            input_args = ["-loop", "1", "-i", str(source)]
        result = run_ffmpeg(
            [
                *input_args,
                "-t",
                f"{duration:.2f}",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},format=yuv420p",
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
    ) -> None:
        args = ["-i", str(visual_video)]
        for audio_path, _volume in audio_tracks:
            args.extend(["-i", str(audio_path)])

        if len(audio_tracks) == 1:
            filter_complex = (
                f"[1:a]volume={audio_tracks[0][1]:.3f},"
                f"apad=pad_dur={duration:.2f},atrim=duration={duration:.2f}[a]"
            )
        else:
            filter_complex = (
                f"[1:a]volume={audio_tracks[0][1]:.3f},apad=pad_dur={duration:.2f}[voice];"
                f"[2:a]volume={audio_tracks[1][1]:.3f},apad=pad_dur={duration:.2f}[music];"
                "[voice][music]amix=inputs=2:duration=longest:dropout_transition=2,"
                f"atrim=duration={duration:.2f}[a]"
            )
        result = run_ffmpeg(
            [
                *args,
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-t",
                f"{duration:.2f}",
                "-c:v",
                "copy",
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
    ) -> dict[str, Any]:
        output_dir = self.media_root / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        segment_paths: list[Path] = []
        total_duration = sum(float(shot["duration_sec"]) for shot in shots)

        for shot in shots:
            source = Path(shot["asset_local_path"])
            if not source.exists():
                raise RuntimeError(f"Asset does not exist: {source}")
            segment = output_dir / f"{shot['id']}.mp4"
            self._render_shot(
                source,
                segment,
                float(shot["duration_sec"]),
                resolution,
                fps,
            )
            segment_paths.append(segment)

        concat_file = output_dir / "concat.txt"
        concat_file.write_text(
            "\n".join(
                f"file '{path.as_posix().replace(chr(39), chr(39) + chr(92) + chr(39))}'"
                for path in segment_paths
            ),
            encoding="utf-8",
        )
        silent_video = output_dir / "silent.mp4"
        result = run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
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
        create_srt(shots, srt_path)
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
            )
        else:
            result = run_ffmpeg(
                [
                    "-i",
                    str(visual_video),
                    "-t",
                    f"{total_duration:.2f}",
                    "-c",
                    "copy",
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
