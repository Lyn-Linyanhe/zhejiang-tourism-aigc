from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any


def _resolve_knowledge_context(context: dict[str, Any]) -> str:
    """取本次创作的浙江文旅事实参考块，用于注入生成 prompt。

    知识库是增强而非依赖：语料缺失、路径异常或服务不可用时应静默降级为
    空串（prompt 退回原有的"不得编造"约束），绝不能让它阻断脚本生成。
    """
    try:
        from ..services import knowledge

        # None 不能直接 str()——那会得到字面量 "None" 并被当成城市名参与匹配
        def _field(name: str) -> str:
            value = context.get(name)
            return "" if value is None else str(value)

        return knowledge.build_generation_context(
            city=_field("city"),
            landmark=_field("landmark"),
            theme=_field("theme"),
            culture=_field("culture"),
            festival=_field("festival"),
        )
    except Exception:  # noqa: BLE001 - 知识库失败必须降级，不能阻断主流程
        return ""


@dataclass
class LLMResult:
    data: dict[str, Any]
    provider: str
    model: str
    fallback_used: bool
    event: dict[str, Any]


class LLMProvider:
    name = "base"

    def generate(self, prompt: str, context: dict[str, Any]) -> LLMResult:
        raise NotImplementedError


class OpenAICompatibleLLMProvider(LLMProvider):
    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 45) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, context: dict[str, Any]) -> LLMResult:
        if not self.base_url or not self.api_key or not self.model:
            raise RuntimeError("LLM requires base_url, api_key, and model")
        duration = int(context.get("duration", 30))
        shot_count = int(context.get("shot_count") or (4 if duration <= 15 else 6 if duration <= 30 else 8))
        custom_brief = str(context.get("custom_brief", "")).strip()
        knowledge_context = _resolve_knowledge_context(context)
        schema = (
            '{"title":"string","summary":"string","cta":"string",'
            '"tags":["string"],"narration":"string",'
            '"shots":[{"id":"shot_1","order":1,"duration_sec":5,'
            '"title":"string","visual_prompt":"string","narration":"string",'
            '"subtitle":"string","transition":"fade"}],'
            '"knowledge":{"fact":"string","color":"#24526a","accent":"#d6ad60","aliases":[]}}'
        )
        fallback_rule = (
            "不得编造实时价格、活动日期、交通班次、荣誉或其他无法核验的事实。"
            if not knowledge_context
            else "实时价格、活动日期、交通班次、荣誉等资料未覆盖的信息不得编造。"
        )
        instruction = (
            "你是浙江文旅竖屏短视频总编导。请根据用户输入生成可直接用于视频制作的 JSON。"
            f"必须生成 {shot_count} 个镜头，镜头时长总和必须等于 {duration} 秒。"
            "每个镜头必须有具体可视化画面、旁白和字幕，不能使用空字符串、占位符或泛泛的“风景”。"
            f"{fallback_rule}"
            f"{knowledge_context}"
            "只输出 JSON，不要 Markdown，不要解释。JSON 结构必须符合："
            f"{schema}"
            f"用户补充要求：{custom_brief or '无'}。"
            f"用户输入：{prompt}"
        )
        # 参考块拼在 user prompt 中创作要求之后。位置与写法一律以
        # scripts/kb_injection_eval.py 的实测数据为准，不要凭单次观感调整。
        body = {
            "model": self.model,
            "temperature": 0.7,
            "messages": [
                {"role": "system", "content": "你是严谨的浙江文旅视频编导，只输出合法 JSON。"},
                {"role": "user", "content": instruction},
            ],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        result = json.loads(content)
        if not isinstance(result, dict):
            raise RuntimeError("LLM returned a non-object JSON response")
        shots = result.get("shots")
        if not isinstance(shots, list) or len(shots) != shot_count:
            count = len(shots) if isinstance(shots, list) else 0
            raise RuntimeError(f"LLM returned {count} shots; expected {shot_count}")
        required = ("title", "summary", "cta", "narration")
        if any(not str(result.get(key, "")).strip() for key in required):
            raise RuntimeError("LLM returned an empty title, summary, CTA, or narration")
        for index, shot in enumerate(shots, start=1):
            if not isinstance(shot, dict):
                raise RuntimeError("LLM returned an invalid shot")
            for key in ("visual_prompt", "narration", "subtitle"):
                if not str(shot.get(key, "")).strip():
                    raise RuntimeError(f"LLM returned an empty shot field: {key}")
            shot.setdefault("id", f"shot_{index}")
            shot.setdefault("order", index)
            shot.setdefault("transition", "fade" if index == 1 else "dissolve")
        return LLMResult(
            data=result,
            provider=self.name,
            model=self.model,
            fallback_used=False,
            event={"type": "llm", "provider": self.name, "model": self.model, "ok": True},
        )
