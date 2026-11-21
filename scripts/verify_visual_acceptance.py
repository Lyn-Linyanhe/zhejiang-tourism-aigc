"""离线视觉验收：从任务存档读取素材，直接检查文件而不是相信 provider 元数据。

示例：

    py -3.12 scripts/verify_visual_acceptance.py --task-id task_xxx --data-dir runtime
    py -3.12 scripts/verify_visual_acceptance.py --task-file task.json --min-video-shots 0

默认要求至少 4 个真实视频镜头，并要求每个素材可解码、通过基础画质门槛。
仅做纯静图离线 smoke 时显式传 `--min-video-shots 0`；不要为了通过验收而把
照片元数据伪造成视频。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.visual_qa import verify_task_visuals, write_report  # noqa: E402


def _load_task(args: argparse.Namespace) -> dict:
    if args.task_file:
        return json.loads(Path(args.task_file).read_text(encoding="utf-8"))
    if not args.task_id:
        raise ValueError("--task-id or --task-file is required")
    from backend.app.storage import Storage

    data_dir = Path(args.data_dir)
    data_dir = data_dir if data_dir.is_absolute() else ROOT / data_dir
    storage = Storage(data_dir / "db" / "app.sqlite3")
    task = storage.get_task(args.task_id)
    if not task:
        raise KeyError(f"task not found: {args.task_id}")
    return task


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline objective visual acceptance")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task-file", help="task JSON exported from the API or Storage")
    source.add_argument("--task-id", help="task id in the runtime database")
    parser.add_argument("--data-dir", default="runtime", help="runtime data directory for --task-id")
    parser.add_argument("--min-video-shots", type=int, default=4)
    parser.add_argument("--strict-reference", action="store_true", help="enforce the subject reference color-signature gate")
    parser.add_argument("--report", default="", help="output path; defaults to the task media directory")
    args = parser.parse_args()

    task = _load_task(args)
    report = verify_task_visuals(
        task,
        min_video_shots=max(0, args.min_video_shots),
        # 客观验收不接受“无法解码”作为通过；缺 FFmpeg 时报告会明确失败。
        strict_media=True,
        strict_reference=args.strict_reference,
    )
    if args.report:
        target = Path(args.report)
    elif args.task_file:
        target = Path(args.task_file).resolve().parent / "visual_acceptance.json"
    else:
        data_dir = Path(args.data_dir)
        data_dir = data_dir if data_dir.is_absolute() else ROOT / data_dir
        target = data_dir / "media" / str(task.get("id") or "task") / "visual_acceptance.json"
    write_report(report, target)
    print(json.dumps({
        "task_id": report.get("task_id"),
        "passed": report.get("passed"),
        "video_shot_count": report.get("requirements", {}).get("video_shot_count"),
        "generation_contract_passed": report.get("generation_contract", {}).get("passed"),
        "failures": report.get("failures", []),
        "report": str(target),
    }, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
