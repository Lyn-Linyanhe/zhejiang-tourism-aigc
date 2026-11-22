"""Render a local publication-mode pilot from the inspected Commons collection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import settings
from backend.app.models import Shot, Task, TaskInput, TraceRecord
from backend.app.services import knowledge
from backend.app.services.orchestrator import Orchestrator
from backend.app.storage import Storage


VISUAL_REVIEW = {
    "hz-xihu": ("西湖群山与湖面全景", "wide", 0.50, 0.57),
    "hz-duanqiao": ("断桥雨中行人与桥体全景", "wide", 0.57, 0.43),
    "hz-leifengta": ("雷峰塔与湖面", "wide", 0.64, 0.42),
    "hz-lingyinsi": ("灵隐寺大雄宝殿", "wide", 0.49, 0.48),
    "hz-longjing": ("梅家坞龙井茶汤细节", "detail", 0.49, 0.57),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    args = parser.parse_args()
    collection = json.loads(args.index.read_text(encoding="utf-8"))
    rows = {row["entity_id"]: row for row in collection["items"] if row.get("library_asset_id")}
    storage = Storage(settings.db_path)
    orchestrator = Orchestrator(settings, storage)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    task = Task(
        id=f"entity_pilot_{stamp}",
        input=TaskInput.from_dict({
            "city": "杭州", "theme": "杭州五处景物与文化", "duration": 30,
            "shot_count": 0, "visual_usage": "publication", "asset_source": "local-library",
            "voice": "Windows 系统语音", "voice_engine": "windows", "voice_rate": 1.05,
            "music_mood": "无音乐", "output_resolution": "720x1280", "fps": 25,
            "include_subtitles": True, "include_ai_label": True,
        }),
        status="draft", stage="preview", progress=35,
        title="湖山之间，读懂杭州", summary="五个实体的开放照片、短事实、授权与逐镜来源验收。",
        cta="沿着湖山，认识杭州。", trace=TraceRecord("offline-editorial", "reviewed-pilot", "entity-pilot-v1", False),
    )
    credits = ["# 杭州实体素材试点署名", "", "本片为静态实拍照片裁剪、调色与缓慢运镜的本地验收片，含系统合成语音。", "随片交付此署名文件；对外发布时应保持作者、来源和许可链接可见。", ""]
    fact_records = []
    for order, (entity_id, (title, tag, focus_x, focus_y)) in enumerate(VISUAL_REVIEW.items(), 1):
        row = rows[entity_id]
        asset = storage.get_library_asset(row["library_asset_id"])
        if not asset or asset["sha256"] != row["sha256"]:
            raise ValueError(f"Missing or changed pilot file: {entity_id}")
        entity = knowledge.get_entity(entity_id, "杭州")
        record = entity["fact_records"][0]
        fact_records.append({"entity_id": entity_id, **record})
        storage.update_library_asset(asset["id"], {
            "review_status": "approved", "license_status": "approved", "allowed_usages": ["preview", "publication"],
            "license_evidence": row["license_evidence"] + f"; local receipt: {row['receipt']}; visually inspected 2026-11-20",
        })
        task.shots.append(Shot(
            id=f"shot_{order}", order=order, duration_sec=6.0,
            title=title, visual_prompt=title, narration=record["text"], subtitle=record["text"],
            entity_id=entity_id, shot_tag=tag, library_asset_id=asset["id"],
            focus_x=focus_x, focus_y=focus_y, motion="gentle", transition="dissolve",
        ))
        credits.extend([
            f"- 镜头 {order}：{row['source_title']} — {row['author']} — {row['license']}。",
            f"  原始页面：{row['source_url']}", f"  许可：{row['license_url']}",
            "  改动：竖屏裁剪、尺寸调整、调色、缓慢推拉、转场、字幕及合成配音。", "",
        ])
    task.narration = "".join(shot.narration for shot in task.shots)
    task.audio["fact_records"] = fact_records
    task.trace.events.append({"type": "pilot-review", "checked_at": "2026-11-20", "facts": fact_records})
    storage.save_task(task)
    draft = task.to_dict()
    draft["id"] = f"{task.id}_review"
    storage.save_task(orchestrator._task_from_dict(draft))
    orchestrator._run(task.id, "audio")
    saved = storage.get_task(task.id)
    output = settings.media_dir / task.id
    output.mkdir(parents=True, exist_ok=True)
    (output / "ATTRIBUTION.md").write_text("\n".join(credits), encoding="utf-8")
    (output / "fact-records.json").write_text(json.dumps(fact_records, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "task.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    if saved["status"] != "completed":
        raise RuntimeError(saved["error"])
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for shot in manifest["shots"]:
        expected = rows[shot["entity_id"]]
        assert shot["provenance"]["sha256"] == expected["sha256"]
        assert shot["provenance"]["review_status"] == "approved"
        assert shot["provenance"]["license_status"] == "approved"
        assert shot["shot_tag"] == VISUAL_REVIEW[shot["entity_id"]][1]
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json", str(output / "final.mp4"),
    ], capture_output=True, text=True, check=True)
    probe = json.loads(result.stdout)
    (output / "ffprobe.json").write_text(json.dumps(probe, indent=2), encoding="utf-8")
    print(json.dumps({"task_id": task.id, "review_task_id": draft["id"], "output": str(output), "status": saved["status"], "probe": probe}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
