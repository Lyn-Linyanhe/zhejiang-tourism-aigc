"""Fetch Xiaohongshu note images through the logged-in in-app browser.

Two phases, because the browser tool and the shell cannot share memory:

1. `EXTRACT_JS` runs in the page and returns, for one spot, the notes worth
   sampling plus their full image CDN URLs.
2. `download_images()` (run from the shell) reads that JSON, downloads a bounded
   number of images per note, and rebuilds the contact sheet.

Nothing is committed: images land in `runtime/_xhs_images/` which is gitignored,
and every row is `reference-only`.

Usage:
    python -I scripts/xhs_fetch_images_browser.py --manifest runtime/_xhs_images/_extracted.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import pathlib
import sys
import time
import urllib.request

from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime/_xhs_images"
MANIFEST = OUT / "manifest.csv"
INDEX = ROOT / "docs/research/xhs-zhejiang-image-index.csv"

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Per-spot budget: how many notes to sample and how many images per note.
DEEP_CITIES = {"杭州", "宁波", "绍兴"}
DEEP_NOTES, DEEP_IMAGES = 2, 3
LIGHT_NOTES, LIGHT_IMAGES = 3, 3

PROMO_WORDS = ("应援", "联动", "抽奖", "明星", "探店", "烤肉", "福利", "开票", "演出时间")
GUIDE_WORDS = ("攻略", "路线", "地图", "合集", "保姆", "避坑", "一日游", "两日游",
               "citywalk", "Citywalk", "游记", "打卡路线", "怎么玩", "这样逛")

COLUMNS = ["city", "spot", "note_id", "author", "published_at", "note_title",
           "image_index", "file", "width", "height", "bytes", "sha256",
           "source_url", "fetched_at", "license_status", "review_status", "error"]

# Evaluated in the page: returns { spot, notes: [{note_id, title, author,
# published_at, images: [{url, width, height}]}] }
EXTRACT_JS = r"""
(args) => {
  const st = window.__INITIAL_STATE__ || {};
  const map = (st.note && st.note.noteDetailMap) || {};
  const keys = Object.keys(map).filter(k => k && k !== 'undefined');
  if (!keys.length) return { ok: false, reason: 'no detail' };
  const note = map[keys[0]].note;
  if (!note) return { ok: false, reason: 'no note' };
  const imgs = (note.imageList || []).map(i => ({
    url: i.urlDefault || i.urlPre || '',
    width: i.width || 0,
    height: i.height || 0,
  })).filter(i => i.url);
  return {
    ok: true,
    note_id: String(note.noteId || ''),
    title: String(note.title || ''),
    author: String((note.user || {}).nickname || ''),
    published_at: note.time ? new Date(note.time).toISOString().slice(0, 10) : '',
    images: imgs,
  };
}
"""


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.xiaohongshu.com/",
    })
    with OPENER.open(request, timeout=45) as response:
        return response.read()


def _contact_sheet(paths: list[pathlib.Path], out_path: pathlib.Path) -> None:
    if not paths:
        return
    columns = 3 if len(paths) > 2 else len(paths)
    rows = (len(paths) + columns - 1) // columns
    thumb_w, thumb_h, gap = 420, 560, 12
    sheet = Image.new("RGB", (columns * thumb_w + (columns + 1) * gap,
                             rows * thumb_h + (rows + 1) * gap), "white")
    for index, path in enumerate(paths):
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
        image.thumbnail((thumb_w, thumb_h))
        x = gap + (index % columns) * (thumb_w + gap)
        y = gap + (index // columns) * (thumb_h + gap)
        sheet.paste(image, (x + (thumb_w - image.width) // 2, y + (thumb_h - image.height) // 2))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "JPEG", quality=85)


def _is_promo(title: str) -> bool:
    return any(word in title for word in PROMO_WORDS)


def _is_guide(title: str) -> bool:
    return any(word in title for word in GUIDE_WORDS)


def _slug_map() -> dict[str, str]:
    """Reuse the slug table from the MCP fetch script to stay consistent."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from xhs_fetch_images import SLUGS  # noqa: E402
    return SLUGS


def _load_existing() -> dict[tuple[str, str, str], dict]:
    if not MANIFEST.exists():
        return {}
    import csv
    with MANIFEST.open(encoding="utf-8-sig") as handle:
        return {(r["spot"], r["note_id"], r["image_index"]): r for r in csv.DictReader(handle)}


def download_images(extracted: dict, slugs: dict[str, str]) -> list[dict]:
    """Download images for one spot from an EXTRACT_JS payload set."""
    import csv

    existing = _load_existing()
    rows: list[dict] = []
    for spot, payload in extracted.items():
        city = payload["city"]
        deep = city in DEEP_CITIES
        note_limit = DEEP_NOTES if deep else LIGHT_NOTES
        image_limit = DEEP_IMAGES if deep else LIGHT_IMAGES
        folder = OUT / city / slugs[spot]
        folder.mkdir(parents=True, exist_ok=True)
        saved: list[pathlib.Path] = []
        notes = [n for n in payload["notes"] if n.get("ok") and not _is_promo(n.get("title", ""))]
        for note in notes[:note_limit]:
            start = 1 if _is_guide(note.get("title", "")) else 0
            for offset, image in enumerate(note["images"][start:start + image_limit]):
                position = start + offset + 1
                key = (spot, note["note_id"], str(position))
                if key in existing and existing[key].get("sha256"):
                    rows.append(existing[key])
                    saved.append(ROOT / existing[key]["file"])
                    continue
                try:
                    data = _download(image["url"])
                    path = folder / f"{note['note_id'][:8]}_{position}.webp"
                    path.write_bytes(data)
                    with Image.open(io.BytesIO(data)) as opened:
                        width, height = opened.size
                    saved.append(path)
                    rows.append({
                        "city": city, "spot": spot, "note_id": note["note_id"],
                        "author": note.get("author", ""),
                        "published_at": note.get("published_at", ""),
                        "note_title": note.get("title", ""), "image_index": position,
                        "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                        "width": width, "height": height, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "source_url": f"https://www.xiaohongshu.com/explore/{note['note_id']}",
                        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
                        "license_status": "reference-only", "review_status": "unreviewed",
                        "error": "",
                    })
                except Exception as exc:
                    rows.append({"city": city, "spot": spot, "note_id": note["note_id"],
                                 "image_index": position, "error": f"download: {exc}"})
                time.sleep(0.15)
        _contact_sheet(saved, OUT / "contact_sheets" / f"{city}-{slugs[spot]}.jpg")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True,
                        help="JSON written by the browser tool: {spot: {city, notes: [...]}}")
    args = parser.parse_args()

    import csv

    extracted = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
    slugs = _slug_map()
    unknown = [spot for spot in extracted if spot not in slugs]
    if unknown:
        print("unknown spots (add to SLUGS first):", unknown)
        return 1

    new_rows = download_images(extracted, slugs)
    existing = _load_existing()
    for row in new_rows:
        if not row.get("error"):
            existing[(row["spot"], row["note_id"], str(row["image_index"]))] = row

    merged = sorted(existing.values(),
                    key=lambda r: (r["city"], r["spot"], r["note_id"], int(r["image_index"])))
    for target in (MANIFEST, INDEX):
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(merged)

    ok = [r for r in new_rows if not r.get("error")]
    print(json.dumps({
        "spots": len(extracted),
        "new_images": len(ok),
        "failed": len(new_rows) - len(ok),
        "total_images": len(merged),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
