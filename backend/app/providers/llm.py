from __future__ import annotations

import json
import math
import re
import urllib.request
from dataclasses import dataclass
from typing import Any

from .contract import TRANSITIONS


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


# 中文配音实测约 4.2~4.5 字/秒（edge-tts 晓晓、rate=1.0、不含句间停顿）。
# 预算按 4.2 算：带内部空格/顿号的旁白实际会慢到约 4.0 字/秒，超出的部分由
# 配音阶段提速兜住（MAX_RATE_SCALE），比反过来留一大段没人说话的空档好。
# 注意：这个值必须按"裁掉引擎静音之后"的测量来定。旧值 3.8 是按含 0.2~0.6 秒
# 引擎内边距的时长标的，裁剪上线后沿用旧值会让成片尾部空出 2~8 秒。
NARRATION_CHARS_PER_SECOND = 4.2

# 字数兜底的改写轮数。一轮实测不总能落进区间，两轮封顶。
NARRATION_REWRITE_ATTEMPTS = 2

# 模型偶尔会把 JSON 包在 Markdown 代码块里，或者返回截断内容。先剥壳再解析，
# 解析失败时把错误回灌给模型重试一次；仍然失败才让任务失败。
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_CAPTION_NOISE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _strip_json_fence(content: str) -> str:
    return _JSON_FENCE.sub("", str(content or "").strip())


def _parse_json_object(content: str) -> dict[str, Any]:
    text = _strip_json_fence(content)
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("LLM returned a non-object JSON response")
    return result


def _caption_key(value: Any) -> str:
    """Compare narration and subtitle without whitespace or punctuation."""
    return _CAPTION_NOISE.sub("", str(value or "")).casefold()


def plan_narration_budget(duration: int, shot_count: int) -> dict[str, float]:
    """把成片时长拆成每镜的字数预算。

    逐镜配音在镜头之间留一段静音（services.audio_sync.SHOT_GAP_TTS_SEC，
    裁掉引擎内边距后由构建器显式补上），那段时间说不了字。算预算前必须先扣掉，
    否则旁白必然超长，成片锁回用户秒数时把最后一句的结尾裁掉——这正是
    "结尾不完整"的成因。
    """
    try:
        from ..services.audio_sync import SHOT_GAP_TTS_SEC
    except Exception:  # noqa: BLE001 - 取不到就退回同一份默认值
        SHOT_GAP_TTS_SEC = 0.300
    silence_total = SHOT_GAP_TTS_SEC * max(0, shot_count - 1)
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
        # 自动镜头数：按每镜约 5 秒切，夹在 2–12 之间。
        # 图生视频单镜 4–15 秒，5 秒是既够运动、又不至于让单镜成本翻倍的中间值。
        # 短时长要额外保护：5 秒切成 2 镜只有 2.5 秒/镜，低于模型下限，
        # 所以镜头数不超过 floor(duration / 4)。
        max_by_floor = max(1, int(duration // 4))
        auto_shots = min(12, max_by_floor, max(2, round(duration / 5)))
        auto_shots = max(1, auto_shots)
        shot_count = int(context.get("shot_count") or auto_shots)
        # 每镜旁白要和它的配音时长对得上：中文配音约每秒 4 字。旁白超出镜头时长时，
        # 渲染器会把成片锁回用户选择的秒数，多出来的部分只能被裁掉。
        budget = plan_narration_budget(duration, shot_count)
        per_shot_seconds = budget["per_shot_seconds"]
        per_shot_chars = budget["per_shot_chars"]
        per_shot_floor = budget["per_shot_floor"]
        total_chars = budget["total_chars"]
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
            f"每镜约 {per_shot_seconds:.1f} 秒，中文配音约每秒 {NARRATION_CHARS_PER_SECOND} 字，"
            f"该镜的 narration 应写满 {per_shot_floor}~{per_shot_chars} 个汉字——"
            f"这是目标区间，不是上限。"
            f"全片旁白合计不得超过 {total_chars} 个汉字，这是硬约束："
            f"配音时长 = 总字数 ÷ {NARRATION_CHARS_PER_SECOND} + 镜头间的停顿，超过用户选的 {duration} 秒，"
            "最后一句的结尾就会被裁掉。镜头多或少都要先把总字数守住。"
            "旁白太短会在成片里留下没有解说、只有静止画面的空档，太长则结尾被裁掉。"
            "按目标字数写满，不要靠重复句或空话凑数。"
            "subtitle 是烧进画面给观众看的字幕，必须与同一镜 narration 逐字对应"
            f"（可用空格代替逗号、去掉句末标点），长度不超过 {per_shot_chars} 字；"
            "不要另起一个短标题，观众读到的字必须和听到的话一致。"
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
        messages = [
            {"role": "system", "content": "你是严谨的浙江文旅视频编导，只输出合法 JSON。"},
            {"role": "user", "content": instruction},
        ]
        result, shots = self._generate_validated(messages, shot_count)

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
                        "保留同样的分镜结构、画面描述和事实依据；"
                        "每镜多写一个具体的景物、动作或可核实的细节来补足字数，"
                        "不要用重复句、空话或感叹词凑数。"
                        "subtitle 与改写后的 narration 逐字对应。重新输出完整 JSON。"
                    ),
                }
            )
            result, shots = self._generate_validated(messages, shot_count)
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
            },
        )

    def _generate_validated(
        self, messages: list[dict[str, str]], shot_count: int
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Parse JSON, then validate the script; repair once on either failure."""
        result = self._chat(messages)
        try:
            return result, self._validated_shots(result, shot_count)
        except Exception as exc:  # noqa: BLE001 - retry once with the exact validation error
            repair_messages = list(messages) + [
                {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)[:6000]},
                {
                    "role": "user",
                    "content": (
                        f"上一版不符合要求：{exc}。请修正后重新输出完整 JSON；"
                        "镜头数、必填字段、字幕与旁白逐字对应、转场名都必须正确。"
                        "只输出 JSON，不要 Markdown 或解释。"
                    ),
                },
            ]
            result = self._chat(repair_messages)
            return result, self._validated_shots(result, shot_count)

    def _chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        content = self._complete(messages)
        try:
            return _parse_json_object(content)
        except (json.JSONDecodeError, ValueError) as exc:
            repair_messages = list(messages) + [
                {"role": "assistant", "content": str(content or "")[:6000]},
                {
                    "role": "user",
                    "content": (
                        f"上一条回复不是合法 JSON：{exc}。"
                        "请只输出修正后的完整 JSON，不要 Markdown 代码块或解释。"
                    ),
                },
            ]
            content = self._complete(repair_messages)
            try:
                return _parse_json_object(content)
            except (json.JSONDecodeError, ValueError) as second:
                raise RuntimeError(f"LLM returned invalid JSON after repair: {second}") from second

    def _complete(self, messages: list[dict[str, str]]) -> str:
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
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM response is missing choices[0].message.content") from exc
        return str(content or "")

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
            for key in ("title", "visual_prompt", "narration", "subtitle"):
                if not str(shot.get(key, "")).strip():
                    raise RuntimeError(f"Shot {index} returned an empty {key}")
            if _caption_key(shot.get("subtitle")) != _caption_key(shot.get("narration")):
                raise RuntimeError(f"Shot {index} subtitle does not match its narration")
            if "duration_sec" in shot:
                try:
                    duration = float(shot["duration_sec"])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"Shot {index} has a non-numeric duration_sec") from exc
                if not math.isfinite(duration) or duration <= 0:
                    raise RuntimeError(f"Shot {index} has an invalid duration_sec")
            transition = str(shot.get("transition") or "").strip().lower()
            if transition and transition not in TRANSITIONS:
                raise RuntimeError(f"Shot {index} has an unsupported transition: {transition}")
            shot.setdefault("id", f"shot_{index}")
            shot.setdefault("order", index)
            shot["transition"] = transition or ("fade" if index == 1 else "dissolve")
        return shots
