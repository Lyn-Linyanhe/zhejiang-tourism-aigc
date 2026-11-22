"""Real cloud acceptance run for the script-generation pipeline.

Runs N fixed briefs against the configured LLM, validates the output, and
writes both the evaluation sample table and the per-shot table described in
docs/research/evaluation-protocol.md.

Usage:
    python -I scripts/eval_script_cloud.py            # 10 briefs
    python -I scripts/eval_script_cloud.py --limit 3  # smoke
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.providers.llm import OpenAICompatibleLLMProvider  # noqa: E402

OUT_DIR = ROOT / "docs/research"
SAMPLE_CSV = OUT_DIR / "evaluation-sample-cloud.csv"
SHOT_CSV = OUT_DIR / "evaluation-shot-cloud.csv"
RAW_DIR = ROOT / "runtime/_eval_cloud"

SAMPLE_COLUMNS = [
    "sample_id", "task_id", "run_id", "input_snapshot_sha256",
    "provider", "model", "fallback", "started_at", "finished_at",
    "duration_seconds", "status", "shot_count", "expected_shots",
    "total_narration_chars", "target_narration_chars",
    "subtitle_match_count", "transition_valid", "empty_field_count",
    "fact_claim_count", "supported_claim_count", "unsupported_claim_count",
    "reviewer", "reviewed_at", "notes",
]
SHOT_COLUMNS = [
    "sample_id", "shot_order", "duration_sec", "narration_chars",
    "visual_prompt_chars", "subtitle_matches_narration", "transition",
    "transition_allowed", "has_empty_field", "notes",
]

# 10 fixed briefs: 3 cities with knowledge base + 2 without, varied duration.
BRIEFS = [
    ("hz-xihu-spring", "杭州", "西湖", "春日赏花", "文艺", 15, 4),
    ("hz-longjing-tea", "杭州", "西湖龙井茶制作技艺", "明前采茶与炒制", "纪实", 20, 5),
    ("hz-liangzhu", "杭州", "良渚古城遗址", "五千年文明", "人文", 30, 6),
    ("sx-lanting", "绍兴", "兰亭", "书法与曲水流觞", "国风", 15, 4),
    ("sx-anchang", "绍兴", "安昌古镇", "水乡烟火气", "生活", 20, 5),
    ("nb-tianyige", "宁波", "天一阁·月湖", "藏书楼与月湖", "人文", 30, 6),
    ("huz-nanxun", "湖州", "南浔古镇", "蚕丝商帮与水乡", "纪实", 20, 5),
    ("huz-moganshan", "湖州", "莫干山", "竹海与民国别墅", "文艺", 15, 4),
    ("wz-yandangshan", "温州", "雁荡山", "山海奇峰", "壮阔", 20, 5),
    ("ls-yunhetitian", "丽水", "云和梯田", "稻浪与云海", "治愈", 30, 6),
]

TRANSITIONS = {"fade", "dissolve", "slideleft", "slideright", "zoom"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _narration_budget(duration: int, shot_count: int) -> int:
    """Mirror llm.plan_narration_budget's total target without importing internals."""
    from backend.app.providers.llm import plan_narration_budget
    return int(plan_narration_budget(duration, shot_count)["total_chars"])


def _caption_key(text: str) -> str:
    from backend.app.providers.llm import _caption_key as key
    return key(text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(BRIEFS))
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    if not (base_url and api_key and model):
        print("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL must be set (see .env)")
        return 1

    provider = OpenAICompatibleLLMProvider(base_url, api_key, model, timeout=args.timeout)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    samples: list[dict] = []
    shots_rows: list[dict] = []
    for sample_id, city, landmark, theme, style, duration, shot_count in BRIEFS[: args.limit]:
        prompt = f"城市：{city}；地标：{landmark}；主题：{theme}；风格：{style}"
        snapshot = json.dumps(
            {"city": city, "landmark": landmark, "theme": theme,
             "style": style, "duration": duration, "shot_count": shot_count},
            ensure_ascii=False, sort_keys=True,
        )
        context = {"city": city, "landmark": landmark, "theme": theme,
                   "style": style, "duration": duration, "shot_count": shot_count}
        started = dt.datetime.now()
        t0 = time.time()
        status = "ok"
        note = ""
        data: dict = {}
        try:
            result = provider.generate(prompt, context)
            data = result.data
            provider_name = result.provider
            model_name = result.model
            fallback = result.fallback_used
        except Exception as exc:
            status = "error"
            note = f"{type(exc).__name__}: {exc}"[:200]
            provider_name = model_name = ""
            fallback = True
            traceback.print_exc()
        elapsed = round(time.time() - t0, 2)
        finished = dt.datetime.now()

        shots = data.get("shots") or []
        (RAW_DIR / f"{sample_id}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        subtitle_ok = 0
        empty_fields = 0
        transitions_valid = True
        total_chars = 0
        for index, shot in enumerate(shots, start=1):
            narration = str(shot.get("narration") or "")
            subtitle = str(shot.get("subtitle") or "")
            total_chars += len(narration)
            match = _caption_key(subtitle) == _caption_key(narration) and bool(narration.strip())
            subtitle_ok += 1 if match else 0
            transition = str(shot.get("transition") or "").lower()
            allowed = transition in TRANSITIONS
            transitions_valid = transitions_valid and allowed
            missing = [k for k in ("title", "visual_prompt", "narration", "subtitle")
                       if not str(shot.get(k) or "").strip()]
            empty_fields += len(missing)
            shots_rows.append({
                "sample_id": sample_id, "shot_order": shot.get("order") or index,
                "duration_sec": shot.get("duration_sec", ""),
                "narration_chars": len(narration),
                "visual_prompt_chars": len(str(shot.get("visual_prompt") or "")),
                "subtitle_matches_narration": match,
                "transition": transition, "transition_allowed": allowed,
                "has_empty_field": bool(missing),
                "notes": "缺失:" + ",".join(missing) if missing else "",
            })

        samples.append({
            "sample_id": sample_id, "task_id": "", "run_id": f"run-{started:%Y%m%d%H%M%S}",
            "input_snapshot_sha256": _sha(snapshot),
            "provider": provider_name, "model": model_name, "fallback": fallback,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "duration_seconds": elapsed, "status": status,
            "shot_count": len(shots), "expected_shots": shot_count,
            "total_narration_chars": total_chars,
            "target_narration_chars": _narration_budget(duration, shot_count),
            "subtitle_match_count": subtitle_ok, "transition_valid": transitions_valid,
            "empty_field_count": empty_fields,
            "fact_claim_count": "", "supported_claim_count": "",
            "unsupported_claim_count": "",
            "reviewer": "pending-manual", "reviewed_at": "", "notes": note,
        })
        print(f"{sample_id:20} {status:5} {elapsed:6.1f}s shots={len(shots)}/{shot_count} "
              f"chars={total_chars} subtitle_ok={subtitle_ok} empty={empty_fields}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with SAMPLE_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SAMPLE_COLUMNS)
        writer.writeheader()
        writer.writerows(samples)
    with SHOT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHOT_COLUMNS)
        writer.writeheader()
        writer.writerows(shots_rows)

    ok = sum(1 for s in samples if s["status"] == "ok")
    print(json.dumps({
        "samples": len(samples), "ok": ok, "failed": len(samples) - ok,
        "success_rate": round(ok / max(1, len(samples)), 3),
        "avg_seconds": round(sum(s["duration_seconds"] for s in samples) / max(1, len(samples)), 1),
        "sample_csv": str(SAMPLE_CSV), "shot_csv": str(SHOT_CSV),
    }, ensure_ascii=False))
    return 0 if ok == len(samples) else 1


if __name__ == "__main__":
    sys.exit(main())
