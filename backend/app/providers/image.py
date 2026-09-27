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
REQUEST_TIMEOUT = 300


class ImageConfigError(RuntimeError):
    """地址或模型没有配好。注册表应据此回退，而不是当成已生成。"""


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


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
    return {
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "size": size,
    }


def build_image_payload(
    prompt: str,
    *,
    model: str,
    size: str,
    seed: int,
    negative: str,
) -> dict[str, Any]:
    """文生图请求体。seed 必带；负向词用两个常见键名，避免网关对不上。"""
    return {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "seed": int(seed),
        "response_format": "b64_json",
        "negative_prompt": negative,
        "negative": negative,
        "watermark": False,
    }


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
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
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
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
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
        if not self.base_url:
            raise ImageConfigError(
                "未配置 IMAGE_BASE_URL。可以填 OpenAI 兼容云端，也可以填本地 ComfyUI 网关。"
            )
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
