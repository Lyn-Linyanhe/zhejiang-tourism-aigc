"""OpenAI 兼容文生图。

请求走 POST {IMAGE_BASE_URL}/images/generations。地址可以指向本地
ComfyUI / SD 网关。密钥允许为空：本地网关经常不鉴权，为空时不带
Authorization。地址或模型缺失则抛错，由注册表降级，不假装已经出图。

请求体带 seed 和负向词。官方 OpenAI Images 不收 negative_prompt，
方舟 Seedream 的 SDK 也没有这个字段；本地 ComfyUI 网关需要它。
因此 negative_prompt 与 negative 两个键一起写，网关认其中一个即可。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .contract import MEDIA_TYPE_IMAGE, AssetResult
from ..services.consistency import compose_prompt, negative_prompt, seed_for_shot


PROVIDER_NAME = "cloud-t2i"
DEFAULT_SIZE = "1024x1536"
DEFAULT_MODEL = "doubao-seedream-4-0-250828"
DEFAULT_COST = 0.0
# 读超时（秒）。实测同一网关同一模型在 37 秒到 300 秒以上之间波动，卡在多镜任务
# 的中间一镜上会把前面已出的图和视频全部作废，所以默认给得宽一点。
# 按本项目的既定原则，付费调用不做自动重试（超时可能已经出图并计费），只把等待窗口
# 放长，由 IMAGE_TIMEOUT_SECONDS 按现场网络调。
DEFAULT_REQUEST_TIMEOUT = 600


class ImageConfigError(RuntimeError):
    """地址或模型没有配好。注册表应据此回退，而不是当成已生成。"""


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def request_timeout() -> int:
    """单次文生图的读超时。惰性读取，确保 .env 已经加载。"""
    try:
        return max(30, int(_env("IMAGE_TIMEOUT_SECONDS") or DEFAULT_REQUEST_TIMEOUT))
    except ValueError:
        return DEFAULT_REQUEST_TIMEOUT


def _settings_value(settings: Any, name: str) -> str:
    if settings is None:
        return ""
    if isinstance(settings, dict):
        return str(settings.get(name, "") or "").strip()
    return str(getattr(settings, name, "") or "").strip()


def resolve_config(settings: Any = None) -> dict[str, str]:
    """环境变量优先，settings 次之。本地网关允许空密钥。"""
    base_url = _env("IMAGE_BASE_URL") or _settings_value(settings, "image_base_url")
    api_key = _env("IMAGE_API_KEY") or _settings_value(settings, "image_api_key")
    model = _env("IMAGE_MODEL") or _settings_value(settings, "image_model") or DEFAULT_MODEL
    size = _env("IMAGE_SIZE") or _settings_value(settings, "image_size") or DEFAULT_SIZE
    api_style = (_env("IMAGE_API_STYLE") or _settings_value(settings, "image_api_style") or "auto").lower()
    if api_style == "auto":
        api_style = "tokendance" if "tokendance.space" in base_url.lower() else "openai-compatible"
    return {
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "size": size,
        "api_style": api_style,
    }


def build_image_payload(
    prompt: str,
    *,
    model: str,
    size: str,
    seed: int,
    negative: str,
    api_style: str = "openai-compatible",
) -> dict[str, Any]:
    """文生图请求体。seed 必带；负向词用两个常见键名，避免网关对不上。

    TokenDance 的 Seedream 协议不收 negative_prompt 字段（也不收 seed），
    负向词只能折进 prompt。不折的话默认负向词里的"文字，水印，logo"整条失效，
    出图会带上模型自己画的伪中文标语。
    """
    if api_style == "tokendance":
        text = prompt
        if negative.strip():
            text = f"{prompt}\n\n不要出现以下内容：{negative}"
        return {"model": model, "prompt": text, "size": size, "response_format": "url", "output_format": "png", "watermark": False}
    return {
        "model": model, "prompt": prompt, "n": 1, "size": size, "seed": int(seed),
        "response_format": "b64_json", "negative_prompt": negative, "negative": negative, "watermark": False,
    }


def build_edit_payload(
    prompt: str,
    *,
    model: str,
    image_data_url: str,
    quality: str = "high",
) -> dict[str, Any]:
    """图生图（images/edits）请求体。

    网关只接受 JSON：`image` 必须是 base64 或 data URI。multipart 上传会返回
    “不支持 multipart 文件上传”，http URL 会返回“暂不支持 URL 形态”。所以调用方
    必须先用 image_file_to_data_url() 把参考图读成 data URI。
    """
    if not image_data_url.startswith("data:image/"):
        raise ValueError("图生图的 image 必须是 data URI（data:image/...;base64,...）")
    return {"model": model, "prompt": prompt, "image": image_data_url, "quality": quality}


def image_file_to_data_url(path: Any) -> str:
    """把本地图片读成 data URI，供 images/edits 使用。"""
    file_path = Path(path)
    raw = file_path.read_bytes()
    if not raw:
        raise RuntimeError(f"参考图为空，无法图生图：{file_path}")
    suffix = file_path.suffix.lower().lstrip(".") or "png"
    if suffix == "jpg":
        suffix = "jpeg"
    return f"data:image/{suffix};base64,{base64.b64encode(raw).decode('ascii')}"


def _decode_image(payload: dict[str, Any]) -> bytes:
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise RuntimeError("文生图响应没有 data[0]")
    entry = data[0]
    encoded = entry.get("b64_json")
    if encoded:
        try:
            raw = base64.b64decode(encoded)
        except Exception as exc:
            raise RuntimeError("文生图 b64_json 无法解码") from exc
        if not raw:
            raise RuntimeError("文生图返回了空图片")
        return raw
    image_url = entry.get("url")
    if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
        request = urllib.request.Request(image_url, method="GET")
        with urllib.request.urlopen(request, timeout=request_timeout()) as response:
            raw = response.read()
        if not raw:
            raise RuntimeError("文生图下载结果是空文件")
        return raw
    raise RuntimeError("文生图响应既没有 b64_json 也没有 url")


def request_image(endpoint: str, payload: dict[str, Any], api_key: str) -> bytes:
    """发一次生成请求。读超时不自动重试，避免同一张图扣两次费。"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=request_timeout()) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"文生图被拒绝：HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"文生图请求没有完成，未自动重试：{exc.reason}") from exc
    if not isinstance(body, dict):
        raise RuntimeError("文生图响应不是 JSON 对象")
    return _decode_image(body)


class CloudImageProvider:
    """注册名 cloud-t2i。generate(shot, output_dir, style_ctx) -> AssetResult。"""

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
        self.size = resolved["size"]
        self.api_style = (
            "tokendance" if "tokendance.space" in self.base_url.lower() else "openai-compatible"
        ) if base_url else resolved["api_style"]
        if not self.base_url:
            raise ImageConfigError(
                "未配置 IMAGE_BASE_URL。可以填 OpenAI 兼容云端，也可以填本地 ComfyUI 网关。"
            )
        if self.api_style == "tokendance" and not self.api_key:
            raise ImageConfigError("未配置 IMAGE_API_KEY。TokenDance 文生图需要 API key。")
        if not self.model:
            raise ImageConfigError("未配置 IMAGE_MODEL。")

    def generate(
        self,
        shot: dict[str, Any],
        output_dir: Any,
        style_ctx: dict[str, Any] | None = None,
    ) -> AssetResult:
        style_ctx = style_ctx or {}
        visual = str(shot.get("visual_prompt") or shot.get("prompt") or "").strip()
        if not visual:
            raise RuntimeError("文生图缺少 visual_prompt，拒绝发送空提示词")
        prompt = compose_prompt(visual, style_ctx)
        negative = negative_prompt(style_ctx)
        task_id = str(shot.get("task_id") or style_ctx.get("task_id") or "")
        seed = seed_for_shot(task_id, shot, style_ctx)
        payload = build_image_payload(
            prompt,
            model=self.model,
            size=str(style_ctx.get("size") or self.size),
            seed=seed,
            negative=negative,
            api_style=self.api_style,
        )
        raw = request_image(f"{self.base_url}/images/generations", payload, self.api_key)
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        shot_id = str(shot.get("id") or f"shot_{shot.get('order', 1)}")
        path = target / f"{shot_id}.png"
        path.write_bytes(raw)
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"文生图落盘失败或文件为空：{path}")
        result = AssetResult(
            path=str(path),
            media_type=MEDIA_TYPE_IMAGE,
            source=self.name,
            model=self.model,
            seed=seed,
            prompt=prompt,
            cost=float(self.cost_per_call),
            event={
                "type": "image",
                "provider": self.name,
                "negative_prompt": negative,
                "size": payload["size"],
                "cost_reported": self.cost_per_call > 0,
            },
        )
        result.validate()
        return result

    def edit(
        self,
        shot: dict[str, Any],
        reference_path: Any,
        output_dir: Any,
        style_ctx: dict[str, Any] | None = None,
    ) -> AssetResult:
        """参考图生图：用同一镜头的参考图 + 画面提示词生成首帧。

        走 images/edits，不是 images/generations。参考图先转 data URI，
        因为网关不接受 multipart 也不接受 http URL。
        """
        style_ctx = style_ctx or {}
        visual = str(shot.get("visual_prompt") or shot.get("prompt") or "").strip()
        if not visual:
            raise RuntimeError("参考图生图缺少 visual_prompt，拒绝发送空提示词")
        prompt = compose_prompt(visual, style_ctx)
        data_url = image_file_to_data_url(reference_path)
        payload = build_edit_payload(
            prompt,
            model=self.model,
            image_data_url=data_url,
            quality=str(style_ctx.get("quality") or "high"),
        )
        raw = request_image(f"{self.base_url}/images/edits", payload, self.api_key)
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        shot_id = str(shot.get("id") or f"shot_{shot.get('order', 1)}")
        path = target / f"{shot_id}_edit.png"
        path.write_bytes(raw)
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"参考图生图落盘失败或文件为空：{path}")
        result = AssetResult(
            path=str(path),
            media_type=MEDIA_TYPE_IMAGE,
            source=f"{self.name}-edit",
            model=self.model,
            seed=0,
            prompt=prompt,
            cost=float(self.cost_per_call),
            event={
                "type": "image",
                "provider": f"{self.name}-edit",
                "reference": str(reference_path),
                "quality": payload["quality"],
                "cost_reported": self.cost_per_call > 0,
            },
        )
        result.validate()
        return result


def build_provider(settings: Any = None) -> CloudImageProvider:
    """registry._instantiate 找的就是这个名字。"""
    return CloudImageProvider(settings)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI 兼容文生图")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    provider = build_provider()
    result = provider.generate(
        {"id": out.stem, "order": 1, "visual_prompt": args.prompt},
        out.parent,
    )
    if Path(result.path) != out:
        out.write_bytes(Path(result.path).read_bytes())
    print(result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
