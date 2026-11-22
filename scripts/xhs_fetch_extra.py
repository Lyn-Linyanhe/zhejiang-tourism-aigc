"""Targeted second pass: pull additional images for spots whose first pass
was dominated by route maps, promo posters or mixed-subject covers.

Appends to the existing manifest instead of replacing it, then rebuilds the
contact sheets from everything on disk. Reference-only, never committed.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from xhs_fetch_images import MANIFEST, OUT, SLUGS, _contact_sheet, _download  # noqa: E402
from xhs_mcp_client import McpClient  # noqa: E402

# (city, spot, note_id, start_position, count) -- positions are 1-based
EXTRA = [
    ("舟山", "普陀山", "69df3e28000000001e00c263", 5, 6),
    ("舟山", "普陀山", "68b53fe0000000001b03cfaf", 4, 4),
    ("金华", "横店影视城", "6a361f00000000000f006491", 4, 6),
    ("金华", "横店影视城", "6a6d95d7000000002500dff6", 1, 6),
    ("嘉兴", "南湖", "6a75f2f9000000002500c817", 4, 4),
    ("嘉兴", "南湖", "6a801aa0000000003400fb89", 2, 4),
    ("温州", "江心屿", "69633cf1000000000e00da95", 4, 6),
    ("湖州", "南浔古镇", "6a7b0f52000000002202ee17", 4, 4),
    ("绍兴", "鲁迅故里·沈园", "6a3bcdc7000000000702e3c1", 2, 4),
    ("绍兴", "鲁迅故里·沈园", "6abe3fc80000000015016545", 2, 3),
]
COLUMNS = ["city", "spot", "note_id", "author", "published_at", "note_title",
           "image_index", "file", "width", "height", "bytes", "sha256",
           "source_url", "fetched_at", "license_status", "review_status", "error"]


def main() -> int:
    source = ROOT / "runtime/_xhs_collect_multi/notes_with_tokens.csv"
    if not source.exists():
        source = ROOT / "docs/research/xhs-zhejiang-mcp-notes.csv"
    with source.open(encoding="utf-8-sig") as handle:
        notes = {row["note_id"]: row for row in csv.DictReader(handle)}
    existing = {}
    if MANIFEST.exists():
        with MANIFEST.open(encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                existing[(row["spot"], row["note_id"], row["image_index"])] = row

    rows = []
    for city, spot, note_id, start, count in EXTRA:
        note = notes.get(note_id)
        if not note:
            rows.append({"city": city, "spot": spot, "note_id": note_id, "error": "note not in table"})
            continue
        try:
            detail = McpClient(18060).call("get_feed_detail", {
                "feed_id": note_id, "xsec_token": note["xsec_token"], "load_all_comments": False,
            })
        except Exception as exc:
            rows.append({"city": city, "spot": spot, "note_id": note_id, "error": f"detail: {exc}"})
            continue
        images = ((detail.get("data") or {}).get("note") or {}).get("imageList") or []
        folder = OUT / city / SLUGS[spot]
        folder.mkdir(parents=True, exist_ok=True)
        for offset in range(start - 1, min(start - 1 + count, len(images))):
            image = images[offset]
            position = offset + 1
            url = image.get("urlDefault") or image.get("urlPre")
            if not url:
                continue
            try:
                data = _download(url)
                path = folder / f"{note_id[:8]}_{position}.webp"
                path.write_bytes(data)
                with Image.open(io.BytesIO(data)) as opened:
                    width, height = opened.size
                rows.append({
                    "city": city, "spot": spot, "note_id": note_id,
                    "author": note.get("author", ""), "published_at": note.get("published_at", ""),
                    "note_title": note.get("title", ""), "image_index": position,
                    "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "width": width, "height": height, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "source_url": note.get("note_url", ""),
                    "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "license_status": "reference-only", "review_status": "unreviewed",
                    "error": "",
                })
            except Exception as exc:
                rows.append({"city": city, "spot": spot, "note_id": note_id,
                             "image_index": position, "error": f"download: {exc}"})

    for row in rows:
        if not row.get("error"):
            existing[(row["spot"], row["note_id"], str(row["image_index"]))] = row
    merged = sorted(existing.values(), key=lambda row: (row["city"], row["spot"], row["note_id"], int(row["image_index"])))
    with MANIFEST.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)
    index = ROOT / "docs/research/xhs-zhejiang-image-index.csv"
    with index.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    for (city, spot), _ in {(row["city"], row["spot"]): None for row in merged if not row.get("error")}.items():
        folder = OUT / city / SLUGS[spot]
        _contact_sheet(sorted(folder.glob("*.webp")), OUT / "contact_sheets" / f"{city}-{SLUGS[spot]}.jpg")

    print(json.dumps({
        "added": len([row for row in rows if not row.get("error")]),
        "failed": len([row for row in rows if row.get("error")]),
        "total": len(merged),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
