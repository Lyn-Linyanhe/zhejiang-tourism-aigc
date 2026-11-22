"""同一段提示词跑两条链路，对比耗时与调用次数。

链路 A（当前）：文生图出首帧 → 图生视频
链路 B（文生视频）：纯文本直接出视频

产出 runtime/compare/ 下的两个 mp4 与一份 timings.json。
提交都不重试（付费任务重试会重复计费）。
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
from app.providers.image import build_provider as build_image_provider  # noqa: E402
from app.providers.video import image_to_data_url, resolve_config  # noqa: E402

ROOT = Path(__file__).resolve().parent / "compare"
ROOT.mkdir(parents=True, exist_ok=True)

PROMPT = (
    "西湖断桥清晨，薄雾中一名撑油纸伞的行人缓缓走过石桥，柳枝随风摆动，"
    "水面泛起涟漪，远处雷峰塔若隐若现。镜头从桥下缓慢升起并向右横移，"
    "展现湖面全景。竖屏构图，写实电影感。"
)
SHOT = {"id": "cmp", "order": 1, "title": "断桥", "visual_prompt": PROMPT, "duration_sec": 5}
STYLE = {"task_id": "compare", "style": "诗意纪实", "color": "#1f6f68", "accent": "#d6ad60", "size": "1440x2560"}


def request(url: str, method: str, key: str, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read().decode("utf-8", "replace")[:300]
    except Exception as exc:  # noqa: BLE001
        return 0, None, f"{type(exc).__name__}: {exc}"


def poll(base: str, key: str, task_id: str, timeout: int = 900) -> tuple[dict | None, float, str]:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        time.sleep(5)
        _, payload, err = request(
            f"{base}/query/video_generation/{urllib.parse.quote(task_id, safe='')}", "GET", key, None, 30
        )
        if not isinstance(payload, dict):
            continue
        if payload.get("error") and not payload.get("status"):
            return None, time.monotonic() - started, json.dumps(payload.get("error"), ensure_ascii=False)[:300]
        task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
        state = str(task.get("status") or "").lower()
        if state == "succeeded":
            return task, time.monotonic() - started, ""
        if state in {"failed", "cancelled", "canceled", "expired"}:
            return None, time.monotonic() - started, json.dumps(task, ensure_ascii=False)[:300]
    return None, time.monotonic() - started, "timeout"


def download(url: str, dest: Path) -> int:
    with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=180) as r:
        dest.write_bytes(r.read())
    return dest.stat().st_size


def video_url(task: dict) -> str:
    content = task.get("content")
    if isinstance(content, dict):
        return str(content.get("url") or content.get("video_url") or "")
    return str(task.get("url") or "")


def main() -> int:
    cfg = resolve_config()
    base, key, model = cfg["base_url"].rstrip("/"), cfg["api_key"], cfg["model"]
    resolution = str(cfg["resolution"]).upper()
    ratio = cfg.get("ratio") or "9:16"
    timings: dict[str, object] = {"model": model, "resolution": resolution, "ratio": ratio}

    # --- 链路 A：文生图 → 图生视频 ---
    print("[A] 文生图...", flush=True)
    t0 = time.monotonic()
    image = build_image_provider().generate(SHOT, ROOT, STYLE)
    t2i_sec = time.monotonic() - t0
    print(f"    首帧 {Path(image.path).name}  {t2i_sec:.1f}s", flush=True)

    print("[A] 图生视频...", flush=True)
    t0 = time.monotonic()
    _, submitted, err = request(f"{base}/video_generation", "POST", key, {
        "model": model, "resolution": resolution, "duration": 5, "ratio": ratio,
        "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": image_to_data_url(Path(image.path))}, "role": "first_frame"},
        ],
    })
    if not isinstance(submitted, dict) or not submitted.get("task_id"):
        print(f"    提交失败: {err or submitted}")
        return 1
    task, poll_sec, err = poll(base, key, submitted["task_id"])
    i2v_sec = time.monotonic() - t0
    if task is None:
        print(f"    i2v 失败: {err}")
        return 1
    size_a = download(video_url(task), ROOT / "A_t2i_i2v.mp4")
    print(f"    完成 {i2v_sec:.1f}s, {size_a} bytes", flush=True)

    # --- 链路 B：纯文生视频 ---
    print("[B] 文生视频...", flush=True)
    t0 = time.monotonic()
    _, submitted, err = request(f"{base}/video_generation", "POST", key, {
        "model": model, "resolution": resolution, "duration": 5, "ratio": ratio,
        "content": [{"type": "text", "text": PROMPT}],
    })
    if not isinstance(submitted, dict) or not submitted.get("task_id"):
        print(f"    提交失败: {err or submitted}")
        return 1
    task, poll_sec, err = poll(base, key, submitted["task_id"])
    t2v_sec = time.monotonic() - t0
    if task is None:
        print(f"    t2v 失败: {err}")
        return 1
    size_b = download(video_url(task), ROOT / "B_t2v.mp4")
    print(f"    完成 {t2v_sec:.1f}s, {size_b} bytes", flush=True)

    timings.update({
        "A_文生图_秒": round(t2i_sec, 1),
        "A_图生视频_秒": round(i2v_sec, 1),
        "A_合计_秒": round(t2i_sec + i2v_sec, 1),
        "A_付费调用次数": 2,
        "A_成片字节": size_a,
        "B_文生视频_秒": round(t2v_sec, 1),
        "B_付费调用次数": 1,
        "B_成片字节": size_b,
    })
    (ROOT / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 对比 ===")
    print(json.dumps(timings, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
