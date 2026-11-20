"""Collect the reviewed Commons pilot candidates without granting publication approval."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import settings
from backend.app.services.library import save_library_upload
from backend.app.storage import Storage


CANDIDATES = (
    (142796203, "hz-xihu", "西湖航拍", ["wide"], "Modern aerial photograph; do not describe it as live video."),
    (54990963, "hz-leifengta", "雷峰塔", ["wide"], "Verify the pagoda and framing before approving."),
    (164814351, "hz-duanqiao", "断桥", ["wide"], "Photographed in May; do not describe it as a snow scene."),
    (16607645, "hz-longjing", "梅家坞龙井茶", ["detail"], "Tea detail only; not evidence of the hand-frying process."),
    (3217320, "hz-lingyinsi", "灵隐寺大雄宝殿", ["wide"], "Temple hall photograph; not Feilai Peak carvings."),
    (135470809, "hz-grandcanal", "1919 年拱宸桥", ["wide"], "Historical photograph from 1919; label the period explicitly."),
)


def plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", value or "")).strip()


def collect(args: argparse.Namespace) -> Path:
    storage = Storage(settings.db_path)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if args.direct else urllib.request.build_opener()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for page_id, entity_id, title, tags, note in CANDIDATES:
        folder = output / f"{entity_id}-{uuid.uuid4().hex[:8]}"
        folder.mkdir()
        params = urllib.parse.urlencode({
            "action": "query", "format": "json", "pageids": str(page_id),
            "prop": "imageinfo|revisions", "iiprop": "url|size|extmetadata",
            "iiurlwidth": "1600", "rvprop": "ids|timestamp", "rvslots": "main",
        })
        query_url = f"https://commons.wikimedia.org/w/api.php?{params}"
        request = urllib.request.Request(query_url, headers={"User-Agent": "ZhejiangTourismAIGC/2.0 (local entity media pilot)"})
        try:
            with opener.open(request, timeout=30) as response:
                receipt = json.loads(response.read().decode("utf-8"))
            page = receipt["query"]["pages"][str(page_id)]
            info = page["imageinfo"][0]
            fields = info.get("extmetadata") or {}
            license_name = plain(fields.get("LicenseShortName", {}).get("value", ""))
            if license_name not in {"CC BY 2.0", "CC BY 3.0", "CC BY 4.0", "CC0"}:
                raise ValueError(f"Unexpected license requires a separate review: {license_name}")
            download_url = info.get("thumburl") or info["url"]
            media_request = urllib.request.Request(download_url, headers={"User-Agent": "ZhejiangTourismAIGC/2.0 (local entity media pilot)"})
            with opener.open(media_request, timeout=45) as response:
                content = response.read(20 * 1024 * 1024 + 1)
                content_type = response.headers.get("Content-Type", "")
            if len(content) > 20 * 1024 * 1024 or not content_type.startswith("image/"):
                raise ValueError(f"Invalid image response: {content_type}, {len(content)} bytes")
            suffix = Path(urllib.parse.unquote(urllib.parse.urlsplit(download_url).path)).suffix.lower()
            filename = f"{entity_id}{suffix}"
            (folder / filename).write_bytes(content)
            (folder / "commons-response.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            retrieved = datetime.now(timezone.utc).date().isoformat()
            revision = (page.get("revisions") or [{}])[0].get("revid")
            metadata = {
                "entity_id": entity_id, "city": "杭州", "place_name": title,
                "shot_tags": tags, "source_url": info["descriptionurl"], "source_title": page["title"],
                "source_publisher": "Wikimedia Commons", "author": plain(fields.get("Artist", {}).get("value", "")),
                "license": license_name, "license_url": fields.get("LicenseUrl", {}).get("value", ""),
                "license_evidence": f"Commons pageid={page_id}; revision={revision}; retrieved={retrieved}; commons-response.json",
                "usage_scope": "Credit author, original file page, license, and cropping/motion changes. " + note,
                "retrieved_at": retrieved,
            }
            asset = save_library_upload(storage, settings.library_dir, filename, content, metadata)
            row = {
                **metadata, "library_asset_id": asset["id"], "sha256": hashlib.sha256(content).hexdigest(),
                "file": str((folder / filename).relative_to(output)),
                "receipt": str((folder / "commons-response.json").relative_to(output)),
                "download_url": download_url, "original_url": info["url"],
                "width": info.get("thumbwidth", info.get("width")), "height": info.get("thumbheight", info.get("height")),
                "description": plain(fields.get("ImageDescription", {}).get("value", "")),
                "review_status": asset["review_status"], "note": note,
            }
            rows.append(row)
            print(json.dumps({"entity_id": entity_id, "asset_id": asset["id"], "file": str(folder / filename), "license": license_name}, ensure_ascii=True), flush=True)
        except Exception as exc:
            rows.append({"entity_id": entity_id, "page_id": page_id, "error": str(exc), "review_status": "unavailable"})
            print(json.dumps(rows[-1], ensure_ascii=True), flush=True)
    index = output / "index.json"
    index.write_text(json.dumps({"retrieved_at": datetime.now(timezone.utc).isoformat(), "items": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="A new, empty collection directory")
    parser.add_argument("--direct", action="store_true", help="Bypass proxies only for this public Commons request")
    arguments = parser.parse_args()
    print(collect(arguments), flush=True)
