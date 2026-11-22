"""OpenAI 兼容视频网关（/v1/videos）适配层。

与 `video.py` 里的火山方舟 Seedance 协议不同，这个网关用一套更简单的异步任务：

    创建：POST {VIDEO_BASE_URL}/videos        -> {"id": "vid_...", "status": "queued"}
    查询：GET  {VIDEO_BASE_URL}/videos/{id}   -> status: queued|in_progress|completed|failed
    下载：GET  返回体里的 url

两条硬约束来自实测：

1. **不要重复提交**。超时或 5xx 时远端可能已经建好任务并计费，重试等于重复扣费。
   所以创建只发一次，失败就抛错，由调用方决定是否人工重试。
2. **图生视频的 image 必须是 data URI**。网关不接受 multipart 上传，也不接受
   http URL（会报“暂不支持 URL 形态”）。

轮询只重试同一次查询，不新建任务。下载后校验文件非空，空文件算失败，
不把远端成功说成本地成功。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_POLL_INTERVAL = 5
DEFAULT_MAX_WAIT = 600
TERMINAL_OK = {"completed", "succeeded", "success"}
TERMINAL_FAIL = {"failed", "error", "cancelled", "canceled"}


class VideoGatewayError(RuntimeError):
    """网关侧失败。message 里保留远端原文，便于定位是参数问题还是配额问题。"""


def _request(
    url: str,
    *,
    payload: dict[str, Any] | None,
    api_key: str,
    method: str = "POST",
    timeout: int = 120,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise VideoGatewayError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise VideoGatewayError(f"请求未完成（未自动重试，避免重复计费）：{exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise VideoGatewayError(f"响应不是 JSON：{body[:200]}") from exc
    if not isinstance(parsed, dict):
        raise VideoGatewayError("响应不是 JSON 对象")
    if "error" in parsed:
        raise VideoGatewayError(str(parsed["error"])[:400])
    return parsed


def submit(
    base_url: str,
    api_key: str,
    *,
    prompt: str,
    seconds: int = 5,
    model: str = "",
    image_data_url: str = "",
) -> str:
    """提交一个视频任务，返回任务 id。只发一次，不重试。"""
    if not prompt.strip():
        raise VideoGatewayError("视频提示词为空")
    if not 4 <= int(seconds) <= 15:
        raise VideoGatewayError("seconds 必须在 4–15 之间")
    if len(prompt) > 2000:
        raise VideoGatewayError("prompt 超过 2000 字符上限")
    payload: dict[str, Any] = {"prompt": prompt, "seconds": int(seconds)}
    if model:
        payload["model"] = model
    if image_data_url:
        if not image_data_url.startswith("data:image/"):
            raise VideoGatewayError("图生视频的 image 必须是 data URI")
        payload["image"] = image_data_url
    body = _request(f"{base_url.rstrip('/')}/videos", payload=payload, api_key=api_key)
    task_id = str(body.get("id") or "")
    if not task_id:
        raise VideoGatewayError(f"创建响应缺少 id：{body}")
    return task_id


def poll(
    base_url: str,
    api_key: str,
    task_id: str,
    *,
    interval: int = DEFAULT_POLL_INTERVAL,
    max_wait: int = DEFAULT_MAX_WAIT,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """轮询同一个任务直到终态。只重试查询，不新建任务。"""
    deadline = time.monotonic() + max_wait
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _request(
            f"{base_url.rstrip('/')}/videos/{task_id}",
            payload=None,
            api_key=api_key,
            method="GET",
        )
        status = str(last.get("status") or "").lower()
        if status in TERMINAL_OK:
            return last
        if status in TERMINAL_FAIL:
            raise VideoGatewayError(f"任务 {task_id} 失败：{last.get('error') or status}")
        sleep(interval)
    raise VideoGatewayError(
        f"任务 {task_id} 在 {max_wait}s 内未完成（最后状态 {last.get('status')}）；"
        "不要重复提交，先查询该任务或人工确认"
    )


def download(url: str, api_key: str, target: Any, *, timeout: int = 300) -> Path:
    """下载成片并校验非空。空文件算失败。"""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    if not raw:
        raise VideoGatewayError("下载到的视频为空文件")
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    if not path.is_file() or path.stat().st_size <= 0:
        raise VideoGatewayError(f"视频落盘失败：{path}")
    return path


def generate_video(
    base_url: str,
    api_key: str,
    *,
    prompt: str,
    target: Any,
    seconds: int = 5,
    model: str = "",
    image_data_url: str = "",
    interval: int = DEFAULT_POLL_INTERVAL,
    max_wait: int = DEFAULT_MAX_WAIT,
) -> dict[str, Any]:
    """提交 → 轮询 → 下载，返回远端最终状态与本地路径。"""
    task_id = submit(
        base_url, api_key, prompt=prompt, seconds=seconds,
        model=model, image_data_url=image_data_url,
    )
    final = poll(base_url, api_key, task_id, interval=interval, max_wait=max_wait)
    url = str(final.get("url") or "")
    if not url:
        raise VideoGatewayError(f"任务 {task_id} 完成但没有 url：{final}")
    path = download(url, api_key, target)
    return {"task_id": task_id, "status": final.get("status"), "path": str(path),
            "credit": final.get("credit"), "seconds": final.get("seconds")}
