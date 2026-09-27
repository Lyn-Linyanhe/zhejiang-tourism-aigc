"""火山方舟 Seedance 图生视频。

创建任务：POST {VIDEO_BASE_URL}/contents/generations/tasks
查询任务：GET  同一个地址 /{task_id}

图片项字段对照官方 SDK（volcengine-python-sdk，2025-07）：

    {"type": "image_url", "image_url": {"url": ...}, "role": "first_frame"}

role 取值来自官方示例 content_generation_tasks.py 里注释掉的
"role": "first_frame"。创建任务的顶层字段还有 ratio、duration、
resolution、seed、watermark。

提交接口不自动重试。超时或 5xx 时付费任务可能已经创建，重试会重复扣费。
轮询只重试同一次查询。下载完成后检查文件非空；空文件算下载失败，
不把远端成功说成本地成功。

密钥缺失直接抛错。不要在没有密钥时写出一个假 mp4。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .contract import MEDIA_TYPE_VIDEO, AssetResult
from ..services.consistency import compose_prompt, seed_for_shot


PROVIDER_NAME = "cloud-i2v"
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-1-0-pro-250528"
DEFAULT_RESOLUTION = "720p"
DEFAULT_RATIO = "9:16"
DEFAULT_DURATION = 5
MIN_DURATION = 2
MAX_DURATION = 12
SUPPORTED_RESOLUTIONS = frozenset({"480p", "720p", "1080p"})
SUPPORTED_RATIOS = frozenset({"9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "9:21"})
POLL_INTERVAL_SECONDS = 5.0
RUN_TIMEOUT_SECONDS = 1800.0
MAX_POLL_RETRIES = 5
DEFAULT_COST = 0.0
TERMINAL_FAILURE = frozenset({"failed", "cancelled", "canceled", "expired"})
ACTIVE_STATUS = frozenset({"queued", "running"})


class VideoConfigError(RuntimeError):
    """密钥或地址没有配好。调用方应回退，不能当成生成成功。"""


class VideoSubmitUnconfirmed(RuntimeError):
    """提交没有拿到明确结果。远端可能已经创建付费任务，禁止自动再提交。"""


class VideoDownloadError(RuntimeError):
    """远端任务成功了，但本地文件没下下来或是空的。"""


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _settings_value(settings: Any, name: str) -> str:
    if settings is None:
        return ""
    if isinstance(settings, dict):
        return str(settings.get(name, "") or "").strip()
    return str(getattr(settings, name, "") or "").strip()


def resolve_config(settings: Any = None) -> dict[str, Any]:
    """密钥只从 VIDEO_API_KEY（或 settings.video_api_key）读。没有就空着，由构造函数抛错。"""
    base_url = (
        _env("VIDEO_BASE_URL")
        or _settings_value(settings, "video_base_url")
        or DEFAULT_BASE_URL
    )
    api_key = _env("VIDEO_API_KEY") or _settings_value(settings, "video_api_key")
    model = _env("VIDEO_MODEL") or _settings_value(settings, "video_model") or DEFAULT_MODEL
    resolution = (
        _env("VIDEO_RESOLUTION")
        or _settings_value(settings, "video_resolution")
        or DEFAULT_RESOLUTION
    ).lower()
    ratio = _env("VIDEO_RATIO") or _settings_value(settings, "video_ratio") or DEFAULT_RATIO
    return {
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "resolution": resolution,
        "ratio": ratio,
    }


def clamp_duration(value: Any) -> int:
    try:
        duration = int(float(value))
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION
    return min(max(duration, MIN_DURATION), MAX_DURATION)


def normalize_resolution(value: str) -> str:
    text = str(value or "").strip().lower()
    if text not in SUPPORTED_RESOLUTIONS:
        allowed = ", ".join(sorted(SUPPORTED_RESOLUTIONS))
        raise VideoConfigError(f"不支持的分辨率 {value!r}，只能是 {allowed}")
    return text


def normalize_ratio(value: str) -> str:
    text = str(value or "").strip()
    if text not in SUPPORTED_RATIOS:
        allowed = ", ".join(sorted(SUPPORTED_RATIOS))
        raise VideoConfigError(f"不支持的比例 {value!r}，只能是 {allowed}")
    return text


def image_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    if not raw:
        raise VideoConfigError(f"首帧图片是空文件：{path}")
    suffix = path.suffix.lower()
    mime = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(
        suffix, "image/jpeg"
    )
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_video_payload(
    *,
    model: str,
    prompt: str,
    image_url: str,
    duration: int,
    resolution: str,
    ratio: str,
    seed: int,
) -> dict[str, Any]:
    """图生视频请求体。时长、分辨率、比例都是参数，缺省竖屏 720p、5 秒。

    有首帧时 content 带 image_url 项；没有首帧时不放空图片项。
    """
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if str(image_url or "").strip():
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url},
                "role": "first_frame",
            }
        )
    return {
        "model": model,
        "content": content,
        "ratio": ratio,
        "duration": duration,
        "resolution": resolution,
        "seed": int(seed),
        "watermark": False,
    }


def _request_json(
    url: str,
    api_key: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> tuple[int, dict[str, Any] | None, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                return response.status, None, raw[:300]
            return response.status, body, ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return exc.code, None, detail
    except urllib.error.URLError as exc:
        raise VideoSubmitUnconfirmed(
            f"图生视频{method}没有拿到响应，未自动重试：{exc.reason}"
        ) from exc


def submit_task(tasks_url: str, payload: dict[str, Any], api_key: str) -> str:
    """只提交一次。5xx、超时、没有 task id 都视为可能已扣费，不重试。"""
    try:
        status, body, detail = _request_json(
            tasks_url, api_key, method="POST", payload=payload, timeout=60
        )
    except VideoSubmitUnconfirmed:
        raise
    if status >= 500 or status == 0:
        raise VideoSubmitUnconfirmed(
            f"图生视频提交返回 HTTP {status}，付费任务可能已经创建，不自动重试。{detail}"
        )
    if not 200 <= status < 300 or body is None:
        raise RuntimeError(f"图生视频提交被拒绝：HTTP {status} {detail}")
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        raise VideoSubmitUnconfirmed("图生视频提交成功但没有返回 task id，不自动重试")
    return task_id


def poll_task(tasks_url: str, task_id: str, api_key: str, *, sleeper=time.sleep, clock=time.monotonic) -> dict[str, Any]:
    """轮询同一次任务。临时失败最多重试查询，不会重新创建任务。"""
    deadline = clock() + RUN_TIMEOUT_SECONDS
    failures = 0
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise VideoSubmitUnconfirmed(f"图生视频仍未结束，已超过 {int(RUN_TIMEOUT_SECONDS)} 秒：{task_id}")
        try:
            status, body, detail = _request_json(
                f"{tasks_url}/{task_id}",
                api_key,
                method="GET",
                timeout=min(30.0, max(remaining, 0.1)),
            )
        except VideoSubmitUnconfirmed as exc:
            failures += 1
            if failures > MAX_POLL_RETRIES or deadline - clock() <= 0:
                raise VideoSubmitUnconfirmed(
                    f"轮询失败，远端任务状态未知：{task_id} {exc}"
                ) from exc
            sleeper(min(POLL_INTERVAL_SECONDS, max(deadline - clock(), 0)))
            continue
        if status in {429, 500, 502, 503, 504}:
            failures += 1
            if failures > MAX_POLL_RETRIES:
                raise VideoSubmitUnconfirmed(f"轮询连续失败，任务可能仍在远端：{task_id}")
            sleeper(min(POLL_INTERVAL_SECONDS, max(deadline - clock(), 0)))
            continue
        if not 200 <= status < 300 or body is None:
            raise VideoSubmitUnconfirmed(f"轮询得到无法确认的状态 HTTP {status}：{task_id} {detail}")
        failures = 0
        state = str(body.get("status") or "").strip().lower()
        if state == "succeeded":
            return body
        if state in TERMINAL_FAILURE:
            error = body.get("error")
            raise RuntimeError(f"图生视频失败：{task_id} status={state} detail={error}")
        if state not in ACTIVE_STATUS:
            raise VideoSubmitUnconfirmed(f"图生视频返回未知状态 {state!r}：{task_id}")
        sleeper(min(POLL_INTERVAL_SECONDS, max(deadline - clock(), 0)))


def download_video(url: str, dest: Path) -> None:
    """下载后检查文件存在且非空。失败与「远端没生成」分开报。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except urllib.error.URLError as exc:
        raise VideoDownloadError(f"远端视频已生成，但下载失败：{exc.reason}") from exc
    dest.write_bytes(data)
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise VideoDownloadError(f"远端视频已生成，但本地文件为空：{dest}")


def _video_url(task: dict[str, Any]) -> str:
    content = task.get("content")
    if isinstance(content, dict):
        url = content.get("video_url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    raise VideoDownloadError("远端任务成功，但响应里没有可下载的 video_url")


class CloudVideoProvider:
    """注册名 cloud-i2v。没有密钥时构造就失败，注册表会回退到下一档。"""

    name = PROVIDER_NAME
    cost_per_call = DEFAULT_COST

    def __init__(
        self,
        settings: Any = None,
        *,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
    ) -> None:
        resolved = resolve_config(settings)
        self.base_url = (base_url or resolved["base_url"]).rstrip("/")
        self.api_key = api_key if api_key else resolved["api_key"]
        self.model = model or resolved["model"]
        self.resolution = normalize_resolution(str(resolved["resolution"]))
        self.ratio = normalize_ratio(str(resolved["ratio"]))
        if not self.api_key:
            raise VideoConfigError(
                "未配置 VIDEO_API_KEY。图生视频不会在缺少密钥时假装生成成功。"
            )
        if not self.base_url:
            raise VideoConfigError("未配置 VIDEO_BASE_URL。")
        if not self.model:
            raise VideoConfigError("未配置 VIDEO_MODEL。")

    def generate(
        self,
        shot: dict[str, Any],
        output_dir: Any,
        style_ctx: dict[str, Any] | None = None,
    ) -> AssetResult:
        style_ctx = style_ctx or {}
        frame = str(
            shot.get("first_frame_path")
            or shot.get("image_path")
            or style_ctx.get("first_frame_path")
            or ""
        ).strip()
        frame_path = Path(frame) if frame else None
        if frame_path is None or not frame_path.is_file():
            raise RuntimeError("图生视频缺少首帧图片 first_frame_path")
        visual = str(shot.get("visual_prompt") or shot.get("prompt") or "").strip()
        if not visual:
            raise RuntimeError("图生视频缺少 visual_prompt")
        prompt = compose_prompt(visual, style_ctx)
        task_id = str(shot.get("task_id") or style_ctx.get("task_id") or "")
        seed = seed_for_shot(task_id, shot, style_ctx)
        duration = clamp_duration(
            style_ctx.get("duration_sec", shot.get("duration_sec", DEFAULT_DURATION))
        )
        resolution = normalize_resolution(str(style_ctx.get("resolution") or self.resolution))
        ratio = normalize_ratio(str(style_ctx.get("ratio") or self.ratio))
        image_url = image_to_data_url(frame_path)
        payload = build_video_payload(
            model=self.model,
            prompt=prompt,
            image_url=image_url,
            duration=duration,
            resolution=resolution,
            ratio=ratio,
            seed=seed,
        )
        tasks_url = f"{self.base_url}/contents/generations/tasks"
        remote_id = submit_task(tasks_url, payload, self.api_key)
        task = poll_task(tasks_url, remote_id, self.api_key)
        target = Path(output_dir)
        shot_id = str(shot.get("id") or f"shot_{shot.get('order', 1)}")
        dest = target / f"{shot_id}.mp4"
        download_video(_video_url(task), dest)
        result = AssetResult(
            path=str(dest),
            media_type=MEDIA_TYPE_VIDEO,
            source=self.name,
            model=self.model,
            seed=seed,
            prompt=prompt,
            first_frame_path=str(frame_path),
            cost=float(self.cost_per_call),
            event={
                "type": "video",
                "provider": self.name,
                "remote_task_id": remote_id,
                "duration": duration,
                "resolution": resolution,
                "ratio": ratio,
            },
        )
        result.validate()
        return result


def build_provider(settings: Any = None) -> CloudVideoProvider:
    """registry._instantiate 找的就是这个名字。"""
    return CloudVideoProvider(settings)


def main() -> int:
    parser = argparse.ArgumentParser(description="方舟图生视频")
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--prompt", default="镜头缓慢前推，保持同一光色")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION)
    parser.add_argument("--resolution", default=DEFAULT_RESOLUTION)
    parser.add_argument("--ratio", default=DEFAULT_RATIO)
    args = parser.parse_args()
    out = Path(args.out)
    provider = build_provider()
    result = provider.generate(
        {
            "id": out.stem,
            "order": 1,
            "visual_prompt": args.prompt,
            "first_frame_path": args.image,
            "duration_sec": args.duration,
        },
        out.parent,
        {"resolution": args.resolution, "ratio": args.ratio},
    )
    print(result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
