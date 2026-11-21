"""Collect two Commons follow-up photos and register inspected files as candidates.

Run with python -I. Download into a new directory, inspect the photos and
receipts, then register them in the explicitly selected data directory.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


CANDIDATES = {
    56576742: ("hz-duanqiao", "duanqiao-snow-2008.jpg", "wide", "zhiyin586@163.com",
               "Snowy West Lake with the Broken Bridge in the background, 2008-01-29."),
    49832125: ("hz-leifengta", "leifengta-inside-golden-ceiling-2008.jpg", "detail", "HALUK COMERTEL",
               "Leifeng Pagoda interior ceiling detail, 2008-09-07."),
}
USER_AGENT = "ZhejiangTourismAIGC/2.0 (small public image collection)"
MEDIA_HOSTS = {"thumb.wikimedia.org", "upload.wikimedia.org"}
LICENSE_URL = "https://creativecommons.org/licenses/by/3.0/"


def _plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip()


def _commons_media(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname in MEDIA_HOSTS


def _fetch_image(opener, info: dict) -> tuple[bytes, str]:
    errors = []
    for url in dict.fromkeys(info.get(key) for key in ("thumburl", "url")):
        if not url or not _commons_media(url):
            continue
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(request, timeout=20) as response:
                if not _commons_media(response.geturl()) or response.headers.get_content_type() != "image/jpeg":
                    raise ValueError("Unexpected media response")
                content = response.read(20 * 1024 * 1024 + 1)
            if not content.startswith(b"\xff\xd8\xff") or len(content) > 20 * 1024 * 1024:
                raise ValueError("Expected a JPEG no larger than 20 MiB")
            return content, url
        except (OSError, ValueError) as exc:
            errors.append(f"{urllib.parse.urlsplit(url).hostname}: {exc}")
    raise ValueError("; ".join(errors) or "No eligible Commons media URL")


def download(output: Path, direct: bool) -> bool:
    output.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    rows = []
    for page_id, (entity_id, name, tag, author, note) in CANDIDATES.items():
        row = {"page_id": page_id, "entity_id": entity_id, "name": name, "shot_tags": [tag], "note": note}
        try:
            params = urllib.parse.urlencode({
                "action": "query", "format": "json", "formatversion": 2, "pageids": page_id,
                "prop": "imageinfo|revisions", "iiprop": "url|size|extmetadata", "iiurlwidth": 1280,
                "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|ImageDescription", "rvprop": "ids|timestamp",
            })
            request = urllib.request.Request(f"https://commons.wikimedia.org/w/api.php?{params}", headers={"User-Agent": USER_AGENT})
            with opener.open(request, timeout=20) as response:
                receipt = json.loads(response.read(2 * 1024 * 1024))
            page = receipt["query"]["pages"][0]
            source_dir = output / entity_id / "source"
            source_dir.mkdir(parents=True)
            proof = source_dir / "commons-response.json"
            proof.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            info = page["imageinfo"][0]
            meta = info["extmetadata"]
            observed = lambda key: _plain(meta[key]["value"])
            license_name = observed("LicenseShortName")
            license_url = observed("LicenseUrl").replace("http://", "https://", 1).rstrip("/") + "/"
            if page["pageid"] != page_id or license_name != "CC BY 3.0" or license_url != LICENSE_URL or observed("Artist").casefold() != author.casefold():
                raise ValueError("Commons identity or license changed; review the saved source receipt")
            row.update(
                source_url=info["descriptionurl"], source_title=page["title"], author=observed("Artist"),
                license=license_name, license_url=license_url, revision=page["revisions"][0]["revid"],
                receipt=proof.relative_to(output).as_posix(), original_url=info["url"],
            )
            content, media_url = _fetch_image(opener, info)
            image = output / entity_id / "image" / name
            image.parent.mkdir()
            image.write_bytes(content)
            row.update(file=image.relative_to(output).as_posix(), media_url=media_url, sha256=hashlib.sha256(content).hexdigest(), status="downloaded")
        except (OSError, ValueError, KeyError, IndexError) as exc:
            row.update(status="unavailable", error=str(exc))
        rows.append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    index = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "items": rows}
    (output / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return all(row["status"] == "downloaded" for row in rows)


def register(index_path: Path, data_dir: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.app.services.library import save_library_upload
    from backend.app.storage import Storage

    index = json.loads(index_path.read_text(encoding="utf-8"))
    storage = Storage(data_dir / "db" / "app.sqlite3")
    for row in index["items"]:
        if row["status"] != "downloaded":
            continue
        entity_id, name, tag, author, note = CANDIDATES[row["page_id"]]
        image = (index_path.parent / entity_id / "image" / name).resolve()
        if not image.is_relative_to(index_path.parent.resolve()):
            raise ValueError("Collection file is outside the collection directory")
        content = image.read_bytes()
        if row["entity_id"] != entity_id or row["author"].casefold() != author.casefold() or row["license_url"] != LICENSE_URL or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ValueError(f"Inspection index no longer matches the downloaded file: {name}")
        asset = save_library_upload(storage, data_dir / "entity-assets", name, content, {
            "entity_id": entity_id, "city": "杭州", "shot_tags": [tag], "author": author,
            "source_url": row["source_url"], "source_title": row["source_title"],
            "source_publisher": "Wikimedia Commons", "license": "CC BY 3.0", "license_url": LICENSE_URL,
            "license_evidence": f"Commons pageid={row['page_id']}; revision={row['revision']}; receipt={row['receipt']}",
            "usage_scope": f"Credit author, original file page, license and edits. {note}",
            "retrieved_at": index["retrieved_at"][:10],
        })
        print(json.dumps({k: asset[k] for k in ("id", "entity_id", "shot_tags", "review_status", "license_status", "allowed_usages", "sha256")}, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("download")
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--direct", action="store_true", help="Use a direct connection for these public requests only")
    intake = commands.add_parser("register", help="Run only after inspecting downloaded images and receipts")
    intake.add_argument("--index", type=Path, required=True)
    intake.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "download":
        raise SystemExit(0 if download(args.output.resolve(), args.direct) else 1)
    register(args.index.resolve(), args.data_dir.resolve())
