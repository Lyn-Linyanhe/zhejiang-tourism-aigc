"""续跑一个已失败的全 AI 任务。

不新建 preview：失败任务里 shot_1.mp4 和 shot_2.png 已经落盘，编排器的
existing_assets / first_frame_path 复用逻辑会直接捡起来，省掉 3 次付费调用。
从 storyboard 续跑（音频有缓存，秒级完成），产物快照写回 full-ai-task.json，
好让 accept_full_ai.py 直接验收。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parent


def call(path: str, method: str = "POST", data=None):
    request = urllib.request.Request(
        BASE + path,
        data=(json.dumps(data, ensure_ascii=False).encode() if data is not None else None),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code, exc.read().decode()[:400], flush=True)
        raise


def main() -> int:
    task_id = sys.argv[1] if len(sys.argv) > 1 else (
        (ROOT / "full-ai-task.json").read_text(encoding="utf-8")
        and json.loads((ROOT / "full-ai-task.json").read_text(encoding="utf-8"))["id"]
    )
    print(json.dumps({"resuming": task_id}, ensure_ascii=False), flush=True)
    print(json.dumps(call(f"/api/tasks/{task_id}/generate", data={}), ensure_ascii=False), flush=True)

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
                        "video": render.get("video_local_path"),
                        "duration": render.get("total_duration"),
                        "label_burned": render.get("has_burned_aigc_label"),
                        "music": (task.get("audio") or {}).get("music_provider"),
                        "voice": (task.get("audio") or {}).get("voice_provider"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0 if task.get("status") == "completed" else 1
    print("TIMEOUT", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
