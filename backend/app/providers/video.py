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
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from ..services.cost import provider_call
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
# 实测该网关最稳定的最短片段是 5 秒。镜头时长由逐镜配音反推，短旁白（如 8 个字
# ≈2 秒）会把请求压到 2 秒，那种长度会被网关拒掉，整条任务跟着失败。
# 请求 5 秒再由渲染器按镜头时长裁掉多余部分，比让 provider 端报错安全。
MIN_DURATION = 5
MAX_DURATION = 12
SUPPORTED_RESOLUTIONS = frozenset({"480p", "720p", "768p", "1080p"})
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
    api_style = (_env("VIDEO_API_STYLE") or _settings_value(settings, "video_api_style") or "auto").lower()
    if api_style == "auto":
        api_style = "tokendance" if "tokendance.space" in base_url.lower() else "volcengine"
    poll_interval = int(_env("VIDEO_POLL_INTERVAL") or _settings_value(settings, "video_poll_interval") or "5")
    timeout = int(_env("VIDEO_TIMEOUT") or _settings_value(settings, "video_timeout") or "1800")
    return {
        "base_url": base_url.rstrip("/"), "api_key": api_key, "model": model,
        "resolution": resolution, "ratio": ratio, "api_style": api_style,
        "poll_interval": poll_interval, "timeout": timeout,
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


def _is_error_envelope(payload: dict[str, Any]) -> bool:
    """网关的错误信封：带 error 字段、且没有任务状态。

    正常任务对象也有可能有 status；错误信封没有 status，只有 error / request_id / type。
    两个条件都满足才算，避免把"失败任务"（有 status=failed 且有 error）误判成查询失败
    ——那种要按任务失败处理，不能重试。
    """
    if not isinstance(payload, dict) or not payload.get("error"):
        return False
    return not str(payload.get("status") or "").strip()


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
        require_first_frame: bool = True,
    ) -> None:
        resolved = resolve_config(settings)
        # 同一个模型既能图生视频也能文生视频，差别只在有没有首帧。
        # require_first_frame=False 走纯文生视频：不带图，但**必须**显式给 ratio，
        # 网关对此有硬校验（"ratio is required for text-to-video"）。
        self.require_first_frame = require_first_frame
        self.base_url = (base_url or resolved["base_url"]).rstrip("/")
        self.api_key = api_key if api_key else resolved["api_key"]
        self.model = model or resolved["model"]
        self.resolution = normalize_resolution(str(resolved["resolution"]))
        self.ratio = normalize_ratio(str(resolved["ratio"]))
        self.api_style = (
            "tokendance" if "tokendance.space" in self.base_url.lower() else "volcengine"
        ) if base_url else resolved["api_style"]
        self.poll_interval = max(1, int(resolved["poll_interval"]))
        self.timeout = max(30, int(resolved["timeout"]))
        if not self.base_url:
            raise VideoConfigError("未配置 VIDEO_BASE_URL。")
        if not self.api_key:
            raise VideoConfigError(
                "未配置 VIDEO_API_KEY。视频生成不会在缺少密钥时假装生成成功。"
            )
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
        if self.require_first_frame:
            if frame_path is None or not frame_path.is_file():
                raise RuntimeError("图生视频缺少首帧图片 first_frame_path")
        else:
            # 文生视频：没有首帧是正常的，给了也不该用——用了就退回成图生视频，
            # 画面运动只剩推镜，正是要避免的那种。
            frame_path = None
        visual = str(shot.get("visual_prompt") or shot.get("prompt") or "").strip()
        if not visual:
            raise RuntimeError("视频生成缺少 visual_prompt")
        prompt = compose_prompt(visual, style_ctx)
        task_id = str(shot.get("task_id") or style_ctx.get("task_id") or "")
        seed = seed_for_shot(task_id, shot, style_ctx)
        duration = clamp_duration(
            style_ctx.get("duration_sec", shot.get("duration_sec", DEFAULT_DURATION))
        )
        resolution = normalize_resolution(str(style_ctx.get("resolution") or self.resolution))
        ratio = normalize_ratio(str(style_ctx.get("ratio") or self.ratio))
        image_url = image_to_data_url(frame_path) if frame_path else ""
        if self.api_style == "tokendance":
            return self._generate_tokendance(
                shot, frame_path, output_dir, prompt, seed, duration, resolution, ratio
            )
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
        with provider_call("asset", self.name, self.model) as billing:
            billing["usage"] = {"requested_video_seconds": duration}
            remote_id = submit_task(tasks_url, payload, self.api_key)
            billing["remote_task_id"] = remote_id
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
                "usage": task.get("usage"),
                "billing_instrumented": True,
                "duration": duration,
                "resolution": resolution,
                "ratio": ratio,
                "mode": "i2v" if frame_path else "t2v",
            },
        )
        result.validate()
        return result


    def _generate_tokendance(self, shot, frame_path, output_dir, prompt, seed, duration, resolution, ratio=""):
        # ratio 缺省回落到配置值：图生视频用不到它（比例跟首帧走），文生视频必须给。
        ratio = str(ratio or self.ratio)
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        shot_id = str(shot.get("id") or f"shot_{shot.get('order', 1)}")
        if Path(shot_id).name != shot_id or shot_id in {".", ".."}:
            raise ValueError("Invalid shot id")
        receipt = target / f"{shot_id}.remote-task.json"
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        body: dict[str, Any] = {
            "model": self.model,
            "resolution": str(resolution).upper(),
            "duration": duration,
            "content": content,
        }
        if frame_path:
            content.append(
                {"type": "image_url", "image_url": {"url": image_to_data_url(frame_path)}, "role": "first_frame"}
            )
        else:
            # 文生视频**必须**显式给 ratio，且不能是 adaptive。不带这一段网关会报
            # "invalid params, ratio is required for text-to-video and must not be adaptive (2013)"。
            body["ratio"] = str(ratio)
        fingerprint = hashlib.sha256(json.dumps({"endpoint": self.base_url, "body": body}, sort_keys=True).encode()).hexdigest()
        saved = {}
        if receipt.is_file():
            try:
                saved = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise VideoSubmitUnconfirmed("Remote task receipt is unreadable; submission was not repeated") from exc
            if not isinstance(saved, dict) or not saved.get("remote_task_id") or saved.get("fingerprint") != fingerprint:
                raise VideoSubmitUnconfirmed("Existing remote task receipt does not match this input; submission was not repeated")
        else:
            with provider_call("asset", self.name, self.model) as billing:
                billing["usage"] = {"requested_video_seconds": duration}
                submitted = self._tokendance_request(f"{self.base_url}/video_generation", "POST", body)
                task_id = str(submitted.get("task_id") or submitted.get("id") or "")
                if not task_id:
                    raise VideoSubmitUnconfirmed("TokenDance 图生视频提交未返回 task_id")
                billing["remote_task_id"] = task_id
            saved = {"remote_task_id": task_id, "provider": self.name, "model": self.model, "shot_id": shot_id,
                     "fingerprint": fingerprint, "status": "submitted", "usage": billing["usage"]}
            receipt.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        task_id = str(saved["remote_task_id"])
        deadline = time.monotonic() + self.timeout
        video_url = ""
        query_failures = 0
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            payload = self._tokendance_request(f"{self.base_url}/query/video_generation/{urllib.parse.quote(task_id, safe='')}", "GET", None)
            # 网关出错时返回的是 {"error":…,"request_id":…,"type":…} 这种信封，
            # 不是任务对象。不先认出来就会去读 status，报出 'unrecognized status ""'
            # 这种把真实原因（限流/额度/参数）整个吞掉的错误。查询失败只重试查询，
            # 绝不重新提交——远端任务可能已经计费。
            if _is_error_envelope(payload):
                query_failures += 1
                detail = json.dumps(payload.get("error"), ensure_ascii=False)[:300]
                saved.update(status="query_error", last_polled_at=time.time(), query_error=detail)
                receipt.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
                if query_failures > MAX_POLL_RETRIES:
                    raise RuntimeError(
                        f"TokenDance 查询连续失败 {query_failures} 次，远端任务 {task_id} 状态未知：{detail}"
                    )
                continue
            query_failures = 0
            task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
            status = str(task.get("status") or "").lower()
            saved.update(status=status, last_polled_at=time.time(), response_fields=sorted(task))
            if isinstance(task.get("usage"), dict):
                saved["usage"] = task["usage"]
            receipt.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            if status == "succeeded":
                content = task.get("content")
                video_url = str((content.get("url") or content.get("video_url")) if isinstance(content, dict) else "") or str(task.get("url") or "")
                if not video_url:
                    raise VideoDownloadError(f"TokenDance task {task_id} succeeded but returned no video URL")
                break
            if status in TERMINAL_FAILURE:
                raise RuntimeError(f"TokenDance 图生视频失败: {status} {task.get('error') or ''}".strip())
            if status not in ACTIVE_STATUS:
                raise VideoSubmitUnconfirmed(
                    f"TokenDance task {task_id} returned unrecognized status {status!r}; "
                    f"response fields: {sorted(task)}; raw: {json.dumps(payload, ensure_ascii=False)[:300]}"
                )
        if not video_url:
            raise VideoSubmitUnconfirmed(f"TokenDance 图生视频超时，远端编号已保存，可查询同一任务: {task_id}")
        dest = target / f"{shot_id}.mp4"
        download_video(video_url, dest)
        result = AssetResult(path=str(dest), media_type=MEDIA_TYPE_VIDEO, source=self.name, model=self.model, seed=seed, prompt=prompt, first_frame_path=str(frame_path or ""), cost=float(self.cost_per_call), event={"type":"video", "provider":self.name, "api_style":"tokendance", "remote_task_id":task_id, "duration":duration, "resolution":resolution, "mode": "i2v" if frame_path else "t2v"})
        result.validate()
        saved.update(status="downloaded", local_path=str(dest))
        receipt.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def _tokendance_request(self, url, method, body):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response: payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"TokenDance 图生视频 HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"TokenDance 图生视频请求未完成：{exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("TokenDance 图生视频请求超时；未自动重试，避免重复提交") from exc
        if not isinstance(payload, dict): raise RuntimeError("TokenDance 图生视频返回格式不是 JSON 对象")
        return payload


def build_provider(settings: Any = None) -> CloudVideoProvider:
    """registry._instantiate 找的就是这个名字。走图生视频。"""
    return CloudVideoProvider(settings)


def build_t2v_provider(settings: Any = None) -> CloudVideoProvider:
    """纯文生视频。同一个模型，只是不带首帧。"""
    return CloudVideoProvider(settings, require_first_frame=False)


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
