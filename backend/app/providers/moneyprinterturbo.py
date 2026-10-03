from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .llm import LLMProvider, LLMResult


_BRIDGE_CODE = r"""
import json
import sys

from app.services import llm

request = json.load(sys.stdin)
script = llm.generate_script(
    video_subject=request["video_subject"],
    language="Chinese",
    paragraph_number=request["paragraph_number"],
    video_script_prompt=request["video_script_prompt"],
)
if not script:
    raise RuntimeError("MoneyPrinterTurbo returned an empty script")

terms = []
try:
    terms = llm.generate_terms(
        video_subject=request["video_subject"],
        video_script=script,
        amount=request["paragraph_number"],
        match_script_order=True,
    )
except Exception as exc:
    print("MPT_TERMS_ERROR:" + str(exc), file=sys.stderr)

print("MPT_RESULT:" + json.dumps({"script": script, "terms": terms}, ensure_ascii=False))
"""


def _split_script(text: str, count: int) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []

    sentences = [
        item.strip(" ，,。.!！？；;")
        for item in re.split(r"(?<=[。.!！？；;])\s*", normalized)
        if item.strip(" ，,。.!！？；;")
    ]
    if len(sentences) >= count:
        return sentences[:count]

    # Upstream scripts are often one or two long paragraphs. Character-based
    # slicing keeps every generated shot populated without changing the source
    # script stored in the trace.
    result: list[str] = []
    width = max(1, math.ceil(len(normalized) / count))
    for index in range(count):
        chunk = normalized[index * width : (index + 1) * width].strip(" ，,。.!！？；;")
        if chunk:
            result.append(chunk)
    while len(result) < count:
        result.append(result[-1] if result else normalized)
    return result[:count]


def build_storyboard(
    *,
    script: str,
    terms: list[str],
    context: dict[str, Any],
) -> dict[str, Any]:
    city = str(context.get("city", "")).strip()
    landmark = str(context.get("landmark", "")).strip()
    theme = str(context.get("theme", "")).strip()
    duration = int(context.get("duration", 30))
    style = str(context.get("style", "诗意纪实")).strip()
    shot_count = int(context.get("shot_count") or (4 if duration <= 15 else 6 if duration <= 30 else 8))
    segments = _split_script(script, shot_count)
    if not segments:
        raise ValueError("MoneyPrinterTurbo script cannot be converted to shots")

    normalized_terms = [str(term).strip() for term in terms if str(term).strip()]
    shots: list[dict[str, Any]] = []
    unit = round(duration / shot_count, 2)
    for index, segment in enumerate(segments):
        keyword = normalized_terms[index] if index < len(normalized_terms) else ""
        visual = ", ".join(
            part
            for part in (
                keyword,
                city,
                landmark,
                theme,
                f"{style}竖屏文旅画面",
            )
            if part
        )
        shots.append(
            {
                "id": f"shot_{index + 1}",
                "order": index + 1,
                "duration_sec": unit,
                "title": f"上游脚本镜头 {index + 1}",
                "visual_prompt": visual,
                "narration": segment + "。",
                "subtitle": segment + "。",
                "transition": "fade" if index == 0 else "dissolve",
            }
        )

    return {
        "title": f"{city}：{theme}" if city and theme else theme or city or "浙江文旅短片",
        "summary": script[:180],
        "cta": f"来{city}走一走，发现浙江文旅新风景。" if city else "来浙江，发现更多风景。",
        "tags": [value for value in (city, landmark, theme, *normalized_terms[:3]) if value],
        "narration": script,
        "shots": shots,
        "knowledge": {
            "fact": "脚本由 MoneyPrinterTurbo 生成，事实与活动信息仍需人工审核。",
            "color": "#24526a",
            "accent": "#d6ad60",
            "aliases": [],
        },
    }


class MoneyPrinterTurboLLMProvider(LLMProvider):
    """Call the sibling MoneyPrinterTurbo checkout without importing its app package.

    Both projects use a top-level Python package named ``app``. A subprocess keeps
    those namespaces isolated and also makes the upstream dependency optional.
    """

    name = "moneyprinterturbo"
    model = "configured-in-upstream-config.toml"

    def __init__(
        self,
        root: Path,
        timeout_seconds: int = 180,
        python_executable: str = "",
    ) -> None:
        self.root = root.expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self.python_executable = python_executable.strip()

    def _python_command(self) -> str:
        if self.python_executable:
            return self.python_executable
        candidates = (
            self.root / ".venv" / "Scripts" / "python.exe",
            self.root / ".venv" / "bin" / "python",
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return sys.executable

    def generate(self, prompt: str, context: dict[str, Any]) -> LLMResult:
        if not self.root.is_dir():
            raise RuntimeError(f"MoneyPrinterTurbo root does not exist: {self.root}")
        if not (self.root / "app" / "services" / "llm.py").is_file():
            raise RuntimeError(f"MoneyPrinterTurbo source is incomplete: {self.root}")

        duration = int(context.get("duration", 30))
        shot_count = int(context.get("shot_count") or (4 if duration <= 15 else 6 if duration <= 30 else 8))
        subject = "、".join(
            value
            for value in (
                context.get("city"),
                context.get("landmark"),
                context.get("theme"),
                context.get("culture"),
                context.get("festival"),
            )
            if value
        )
        request_body = {
            "video_subject": subject,
            "paragraph_number": shot_count,
            "video_script_prompt": (
                "面向中国年轻游客，围绕浙江文旅城市短视频创作。"
                f"城市：{context.get('city', '')}；受众：{context.get('audience', '')}；"
                f"风格：{context.get('style', '')}。要求内容具体、可核验，"
                "不要编造实时活动、价格、交通和荣誉信息。"
                + f"补充要求：{context.get('custom_brief', '') or '无'}。"
            ),
        }
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.root)
        try:
            completed = subprocess.run(
                [self._python_command(), "-c", _BRIDGE_CODE],
                cwd=self.root,
                env=environment,
                input=json.dumps(request_body, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"MoneyPrinterTurbo timed out after {self.timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"cannot start MoneyPrinterTurbo: {exc}") from exc

        marker = "MPT_RESULT:"
        result_line = next(
            (line[len(marker) :] for line in completed.stdout.splitlines() if line.startswith(marker)),
            "",
        )
        if completed.returncode != 0 or not result_line:
            detail = (completed.stderr or completed.stdout or "").strip()
            detail = detail[-1200:] if detail else "no bridge output"
            raise RuntimeError(
                f"MoneyPrinterTurbo failed with exit code {completed.returncode}: {detail}"
            )
        try:
            upstream = json.loads(result_line)
            data = build_storyboard(
                script=str(upstream["script"]),
                terms=list(upstream.get("terms") or []),
                context=context,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid MoneyPrinterTurbo result: {exc}") from exc

        return LLMResult(
            data=data,
            provider=self.name,
            model=self.model,
            fallback_used=False,
            event={
                "type": "llm",
                "provider": self.name,
                "model": self.model,
                "prompt_version": "mpt-zj-tourism-bridge-v1",
                "ok": True,
                "root": str(self.root),
                "shot_count": len(data["shots"]),
            },
        )
