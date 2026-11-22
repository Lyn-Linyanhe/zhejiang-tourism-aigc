"""探针：网关收不收"纯文生视频"（content 只有 text、没有 first_frame）。

只提交一次，不重试（付费任务重试会重复计费）。回执写在 runtime/probe_t2v/ 下。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app.config  # noqa: E402,F401
from app.providers.video import resolve_config  # noqa: E402

ROOT = Path(__file__).resolve().parent / "probe_t2v"
ROOT.mkdir(parents=True, exist_ok=True)

PROMPT = (
    "西湖断桥清晨，薄雾中一名撑油纸伞的行人缓缓走过石桥，"
    "柳枝随风摆动，水面泛起涟漪，远处雷峰塔若隐若现。"
    "镜头从桥下缓慢升起并向右横移，展现湖面全景。竖屏构图，写实电影感。"
)


def request(url: str, method: str, key: str, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    cfg = resolve_config()
    base, key, model = cfg["base_url"].rstrip("/"), cfg["api_key"], cfg["model"]
    resolution = str(cfg["resolution"]).upper()
    duration = 5

    body = {
        "model": model,
        "resolution": resolution,
        "duration": duration,
        # 文生视频**必须**显式给 ratio，且不能是 adaptive——网关对此有硬校验：
        # "invalid params, ratio is required for text-to-video and must not be adaptive (2013)"
        "ratio": cfg.get("ratio") or "9:16",
        # 关键差异：content 里**没有** image_url 项
        "content": [{"type": "text", "text": PROMPT}],
    }
    (ROOT / "submit-body.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"提交 纯文本 t2v：model={model} resolution={resolution} duration={duration}s")
    started = time.monotonic()
    status, payload = request(f"{base}/video_generation", "POST", key, body)
    print(f"提交返回 HTTP {status}  用时 {time.monotonic() - started:.1f}s")
    print(f"响应: {json.dumps(payload, ensure_ascii=False)[:500] if isinstance(payload, dict) else payload}")
    if not isinstance(payload, dict):
        return 1
    task_id = str(payload.get("task_id") or payload.get("id") or "")
    if not task_id:
        print("没有拿到 task_id —— 网关不接受纯文生视频")
        return 1
    print(f"远端任务: {task_id}，开始轮询")

    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        time.sleep(5)
        status, payload = request(
            f"{base}/query/video_generation/{urllib.parse.quote(task_id, safe='')}",
            "GET", key, None, timeout=30,
        )
        if not isinstance(payload, dict):
            print(f"  查询 HTTP {status}: {payload}")
            continue
        task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
        state = str(task.get("status") or "").lower()
        print(f"  [{time.monotonic() - started:6.1f}s] status={state}")
        if state == "succeeded":
            content = task.get("content")
            url = (content.get("url") or content.get("video_url")) if isinstance(content, dict) else task.get("url")
            elapsed = time.monotonic() - started
            print(f"\n纯文生视频成功，总耗时 {elapsed:.1f}s")
            print(f"视频地址: {str(url)[:160]}")
            (ROOT / "result.json").write_text(
                json.dumps({"task_id": task_id, "elapsed_sec": round(elapsed, 1), "url": url},
                           ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return 0
        if state in {"failed", "cancelled", "canceled", "expired"}:
            print(f"远端失败: {json.dumps(task, ensure_ascii=False)[:400]}")
            return 1
    print("超时")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
