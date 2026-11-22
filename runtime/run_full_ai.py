"""全 AI 端到端生成驱动。

asset_source=ai → 文生图首帧 → 图生视频 → 逐镜 edge-tts 配音 → 授权音乐
→ FFmpeg 合成（烧字幕 + AIGC 标识）。轮询到终态为止，产物路径写进日志。

单镜实测约 112 秒（t2i 67s + i2v 44s），四镜串行约 8 分钟，所以预算给到 45 分钟。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parent

BODY = {
    "city": "杭州",
    "landmark": "西湖",
    "theme": "西湖春日宋韵游",
    "culture": "宋韵文化与江南园林",
    "festival": "春日",
    "audience": "年轻游客",
    "duration": 15,
    "style": "诗意纪实",
    "voice": "晓伊·活泼女声",
    "music_mood": "舒缓",
    "music_ducking": "不压低",
    "shot_count": 4,
    "asset_source": "ai",
    "output_resolution": "720x1280",
    "fps": 25,
    "include_subtitles": True,
    "include_ai_label": True,
    "custom_brief": "用经核查的西湖事实，画面要有镜头运动，不要静态图。",
}


def call(path: str, method: str = "POST", data=None, timeout: int = 180):
    request = urllib.request.Request(
        BASE + path,
        data=(json.dumps(data, ensure_ascii=False).encode() if data is not None else None),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code, exc.read().decode()[:500], flush=True)
        raise


def main() -> int:
    # 可选第一个参数覆盖素材来源，方便对比 ai-image / ai-video / ai-t2v
    if len(sys.argv) > 1:
        BODY["asset_source"] = sys.argv[1]
        print(f"素材来源覆盖为 {BODY['asset_source']}", flush=True)
    preview = call("/api/previews", data=BODY)
    task_id = preview["id"]
    (ROOT / "full-ai-preview.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "preview_id": task_id,
                "status": preview.get("status"),
                "shots": len(preview.get("shots") or []),
                "error": preview.get("error"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if preview.get("status") == "failed":
        return 1

    started = call(f"/api/tasks/{task_id}/generate", data={})
    print(json.dumps({"generate": started}, ensure_ascii=False), flush=True)

    deadline = time.monotonic() + 45 * 60
    previous = ""
    while time.monotonic() < deadline:
        time.sleep(10)
        task = call(f"/api/tasks/{task_id}", method="GET")
        (ROOT / "full-ai-task.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        line = json.dumps(
            {
                "elapsed": int(time.monotonic() - (deadline - 45 * 60)),
                "status": task.get("status"),
                "stage": task.get("stage"),
                "progress": task.get("progress"),
                "assets": len(task.get("assets") or []),
                "error": (task.get("error") or "")[:200],
            },
            ensure_ascii=False,
        )
        if line != previous:
            print(line, flush=True)
            previous = line
        if task.get("status") in {"completed", "failed", "cancelled"}:
            render = task.get("render") or {}
            print(
                json.dumps(
                    {
                        "FINAL": task.get("status"),
                        "video": render.get("video_url"),
                        "local": render.get("video_local_path"),
                        "duration": render.get("total_duration"),
                        "resolution": render.get("resolution"),
                        "aspect": render.get("aspect_ratio"),
                        "has_audio": render.get("has_audio"),
                        "label_burned": render.get("has_burned_aigc_label"),
                        "subs": render.get("has_burned_subtitles"),
                        "mix": render.get("mix_strategy"),
                        "audio_provider": (task.get("audio") or {}).get("voice_provider"),
                        "music_provider": (task.get("audio") or {}).get("music_provider"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0 if task.get("status") == "completed" else 1
    print("TIMEOUT after 45 minutes", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
