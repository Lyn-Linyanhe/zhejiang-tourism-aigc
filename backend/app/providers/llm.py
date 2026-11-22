from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any


from ..services.cost import provider_call, record_response

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


# 中文配音实测约 3.9 字/秒（edge-tts 晓晓，rate=1.0）。预算按 3.8 算，留一点余量：
# 宁可少写一个字，也不要把最后一句挤到容器外被裁掉。
NARRATION_CHARS_PER_SECOND = 3.8

# 字数兜底的改写轮数。一轮实测不总能落进区间，两轮封顶。
NARRATION_REWRITE_ATTEMPTS = 2


def plan_narration_budget(duration: int, shot_count: int) -> dict[str, float]:
    """把成片时长拆成每镜的字数预算。

    逐镜配音在镜头之间留一段静音（services.audio_sync.SILENCE_BETWEEN_SHOTS），
    那段时间说不了字。算预算前必须先扣掉，否则旁白必然超长，成片锁回用户
    秒数时把最后一句的结尾裁掉——这正是"结尾不完整"的成因。
    """
    try:
        from ..services.audio_sync import SILENCE_BETWEEN_SHOTS
    except Exception:  # noqa: BLE001 - 取不到就退回同一份默认值
        SILENCE_BETWEEN_SHOTS = 0.150
    silence_total = SILENCE_BETWEEN_SHOTS * max(0, shot_count - 1)
    usable = max(1.0, float(duration) - silence_total)
    total_chars = max(shot_count * 6, int(usable * NARRATION_CHARS_PER_SECOND))
    # 向下取整，不用 round：每镜上限回乘镜头数必须仍不超过总预算，
    # 否则 8 镜时 round 出来的上限会反超总预算，旁白又超长被裁。
    per_shot_chars = max(6, total_chars // shot_count)
    return {
        "silence_total": silence_total,
        "per_shot_seconds": usable / shot_count,
        "total_chars": total_chars,
        "per_shot_chars": per_shot_chars,
        # 下限留 2 字余量。旁白偏短和偏长一样是废片：短了成片会剩下没有解说、
        # 只有静止画面的空档（画面锁在用户选的秒数，配音补不满就只能冻最后一帧）。
        "per_shot_floor": max(4, per_shot_chars - 2),
    }


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
        # 每镜旁白要和它的配音时长对得上：中文配音约每秒 4 字。旁白超出镜头时长时，
        # 渲染器会把成片锁回用户选择的秒数，多出来的部分只能被裁掉。
        budget = plan_narration_budget(duration, shot_count)
        per_shot_seconds = budget["per_shot_seconds"]
        per_shot_chars = budget["per_shot_chars"]
        per_shot_floor = budget["per_shot_floor"]
        total_chars = budget["total_chars"]
        custom_brief = str(context.get("custom_brief", "")).strip()
        subject_lock = str(context.get("subject_lock", "")).strip()
        if not subject_lock:
            place = f"{context.get('city', '')}{context.get('landmark', '')}".strip()
            subject_lock = f"{place or '核心景点'}的可辨识主体、轮廓、材质和标志性细节"
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
            f"每镜约 {per_shot_seconds:.1f} 秒，中文配音约每秒 3.8 字，"
            f"该镜的 narration 应写满 {per_shot_floor}~{per_shot_chars} 个汉字——"
            f"这是目标区间，不是上限。"
            f"全片旁白合计不得超过 {total_chars} 个汉字，这是硬约束："
            f"配音时长 = 总字数 ÷ 3.8 + 镜头间的停顿，超过用户选的 {duration} 秒，"
            "最后一句的结尾就会被裁掉。镜头多或少都要先把总字数守住。"
            "旁白太短会在成片里留下没有解说、只有静止画面的空档，太长则结尾被裁掉。"
            "按目标字数写满，不要靠重复句或空话凑数。"
            "subtitle 是烧进画面给观众看的字幕，必须与同一镜 narration 逐字对应"
            f"（可用空格代替逗号、去掉句末标点），长度不超过 {per_shot_chars} 字；"
            "不要另起一个短标题，观众读到的字必须和听到的话一致。"
            "每个镜头必须有具体可视化画面、旁白和字幕，不能使用空字符串、占位符或泛泛的“风景”。"
            f"全片必须锁定同一主体：{subject_lock}。"
            "每个 visual_prompt 都要明确写出这个主体如何出现，并只改变机位、景别、动作、时间和光线；"
            "不得在不同镜头重新设计主体，也不要用风格形容词代替主体描述。"
            "visual_prompt 的推荐结构是：主体及其固定外观 + 本镜场景/动作 + 景别与机位 + 光线；"
            "不要写抽象空话，不要指定文字、水印或画面内字幕。"
            f"{fallback_rule}"
            f"{knowledge_context}"
            "只输出 JSON，不要 Markdown，不要解释。JSON 结构必须符合："
            f"{schema}"
            f"用户补充要求：{custom_brief or '无'}。"
            f"用户输入：{prompt}"
        )
        # 参考块拼在 user prompt 中创作要求之后。位置与写法一律以
        # scripts/kb_injection_eval.py 的实测数据为准，不要凭单次观感调整。
        messages = [
            {"role": "system", "content": "你是严谨的浙江文旅视频编导，只输出合法 JSON。"},
            {"role": "user", "content": instruction},
        ]
        result = self._chat(messages)
        shots = self._validated_shots(result, shot_count)

        # 字数靠 prompt 措辞约束不可靠：同一份约束实测出过"每镜 17 字"（结尾被裁）
        # 和"每镜 7 字"（成片后半段没有解说、只剩静止画面）两种偏差，方向还相反。
        # 旁白总字数直接决定成片有没有长时间空档，所以按实际字数兜一次改写。
        # 只多打一次文本模型，比让整条视频作废便宜得多。
        min_total = max(shot_count * per_shot_floor, int(total_chars * 0.85))
        total = self._narration_total(shots)
        attempts = 0
        # 实测一轮改写不一定能落进区间（29 字改成 42 字，仍偏低），所以给两轮。
        # 文本模型一次调用远比让整条视频带着空档或断句交付便宜。
        while attempts < NARRATION_REWRITE_ATTEMPTS and not min_total <= total <= total_chars:
            messages.append(
                {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)}
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"你上一版每镜旁白分别是 "
                        f"{'、'.join(str(len(str(s.get('narration', '')))) for s in shots)} 字，"
                        f"全片 {total} 字。"
                        f"请把每镜 narration 改写成正好 {per_shot_chars} 字左右"
                        f"（全片约 {total_chars} 字，不得少于 {min_total} 字，不得多于 {total_chars} 字）。"
                        "保留同样的分镜结构、主体锁定、画面描述和事实依据；"
                        "每镜多写一个具体的景物、动作或可核实的细节来补足字数，"
                        "不要用重复句、空话或感叹词凑数。"
                        "subtitle 与改写后的 narration 逐字对应。重新输出完整 JSON。"
                    ),
                }
            )
            result = self._chat(messages)
            shots = self._validated_shots(result, shot_count)
            total = self._narration_total(shots)
            attempts += 1

        return LLMResult(
            data=result,
            provider=self.name,
            model=self.model,
            fallback_used=False,
            event={
                "type": "llm",
                "provider": self.name,
                "model": self.model,
                "ok": True,
                "narration_chars": total,
                "narration_target": total_chars,
                "narration_rewrite_attempts": attempts,
                "billing_instrumented": True,
                "prompt_version": "zj-tourism-prompt-v2",
                "subject_lock": subject_lock,
            },
        )

    def _chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "temperature": 0.7,
            "messages": messages,
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
        with provider_call("llm", self.name, self.model) as billing:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                record_response(billing, response)
                payload = json.loads(response.read().decode("utf-8"))
                record_response(billing, response, payload)
        result = json.loads(payload["choices"][0]["message"]["content"])
        if not isinstance(result, dict):
            raise RuntimeError("LLM returned a non-object JSON response")
        return result

    @staticmethod
    def _narration_total(shots: list[dict[str, Any]]) -> int:
        return sum(len(str(shot.get("narration", ""))) for shot in shots)

    @staticmethod
    def _validated_shots(result: dict[str, Any], shot_count: int) -> list[dict[str, Any]]:
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
        return shots
