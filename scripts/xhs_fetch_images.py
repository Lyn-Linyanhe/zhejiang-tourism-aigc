"""Download a bounded set of Xiaohongshu note images for internal reference.

Reads the provincial note table, picks the top notes per spot, re-fetches each
note detail to get fresh CDN URLs, and stores a few images under runtime/.
Nothing is committed; every row is reference-only and must not be published.

Usage:
    python -I runtime/_xhs_collect_multi/fetch_images.py
"""

from __future__ import annotations

import concurrent.futures
import csv
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
# docs/ copies have xsec_token stripped; the runtime copy keeps it for re-fetching.
NOTES = ROOT / "runtime/_xhs_collect_multi/notes_with_tokens.csv"
if not NOTES.exists():
    NOTES = ROOT / "docs/research/xhs-zhejiang-mcp-notes.csv"
OUT = ROOT / "runtime/_xhs_images"
MANIFEST = OUT / "manifest.csv"
INDEX = ROOT / "docs/research/xhs-zhejiang-image-index.csv"
PORTS = (18060, 18061, 18062)
DEEP_CITIES = {"杭州", "宁波", "绍兴"}
DEEP_NOTES_PER_SPOT = 2
DEEP_IMAGES_PER_NOTE = 3
LIGHT_NOTES_PER_SPOT = 3
LIGHT_IMAGES_PER_NOTE = 3
PROMO_WORDS = ("应援", "联动", "抽奖", "明星", "探店", "烤肉", "福利", "送", "开票", "演出时间")
GUIDE_WORDS = ("攻略", "路线", "地图", "合集", "保姆", "避坑", "一日游", "两日游",
               "citywalk", "Citywalk", "游记", "打卡路线", "怎么玩", "这样逛")

sys.path.insert(0, str(ROOT / "scripts"))
from xhs_mcp_client import McpClient  # noqa: E402

SLUGS = {
    "西湖": "xihu",
    "灵隐寺·飞来峰": "lingyinsi",
    "西溪国家湿地公园": "xixi",
    "京杭大运河·拱宸桥": "gongchenqiao",
    "良渚古城遗址": "liangzhu",
    "天一阁·月湖": "tianyige",
    "老外滩": "laowaitan",
    "东钱湖": "dongqianhu",
    "兰亭": "lanting",
    "东湖": "donghu",
    "安昌古镇": "anchang",
    "仓桥直街": "cangqiao",
    "鲁迅故里·沈园": "luxun",
    "大禹陵": "dayuling",
    "江心屿": "jiangxinyu",
    "南湖": "nanhu",
    "南浔古镇": "nanxun",
    "横店影视城": "hengdian",
    "江郎山": "jianglangshan",
    "普陀山": "putuoshan",
    "台州府城墙": "taizhou-fucheng",
    "仙都": "xiandu",
    "雁荡山": "yandangshan",
    "楠溪江": "nanxijiang",
    "乌镇": "wuzhen",
    "西塘": "xitang",
    "莫干山": "moganshan",
    "太湖旅游度假区": "taihu",
    "双龙洞": "shuanglongdong",
    "诸葛八卦村": "zhugecun",
    "廿八都古镇": "nianbadu",
    "龙游石窟": "longyoushiku",
    "朱家尖": "zhujiajian",
    "东极岛": "dongjidao",
    "神仙居": "shenxianju",
    "石塘": "shitang",
    "云和梯田": "yunhetitian",
    "松阳古村落": "songyang",
}
CITY_ORDER = ["杭州", "宁波", "绍兴", "温州", "嘉兴", "湖州", "金华", "衢州", "舟山", "台州", "丽水"]

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.xiaohongshu.com/",
    })
    with OPENER.open(request, timeout=45) as response:
        return response.read()


def _pick_images(image_list: list[dict], count: int, start: int = 0) -> list[dict]:
    picked = []
    for image in image_list[start:]:
        if image.get("urlDefault") or image.get("urlPre"):
            picked.append(image)
        if len(picked) >= count:
            break
    return picked


def _is_guide(title: str) -> bool:
    return any(word in title for word in GUIDE_WORDS)


def _is_promo(title: str) -> bool:
    return any(word in title for word in PROMO_WORDS)


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


def _collect_spot(client: McpClient, city: str, spot: str, notes: list[dict]) -> list[dict]:
    deep = city in DEEP_CITIES
    note_limit = DEEP_NOTES_PER_SPOT if deep else LIGHT_NOTES_PER_SPOT
    image_limit = DEEP_IMAGES_PER_NOTE if deep else LIGHT_IMAGES_PER_NOTE
    slug = SLUGS[spot]
    folder = OUT / city / slug
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    saved: list[pathlib.Path] = []
    candidates = [note for note in notes if not _is_promo(note.get("title", ""))]
    for note in candidates[:note_limit]:
        try:
            detail = client.call("get_feed_detail", {
                "feed_id": note["note_id"], "xsec_token": note["xsec_token"],
                "load_all_comments": False,
            })
        except Exception as exc:
            rows.append({"city": city, "spot": spot, "note_id": note["note_id"],
                         "error": f"detail: {exc}"})
            continue
        payload = (detail.get("data") or {}).get("note") or {}
        start = 1 if _is_guide(note.get("title", "")) else 0
        images = _pick_images(payload.get("imageList") or [], image_limit, start)
        for index, image in enumerate(images):
            position = start + index + 1
            url = image.get("urlDefault") or image.get("urlPre")
            try:
                data = _download(url)
                digest = hashlib.sha256(data).hexdigest()
                path = folder / f"{note['note_id'][:8]}_{position}.webp"
                path.write_bytes(data)
                with Image.open(io.BytesIO(data)) as opened:
                    width, height = opened.size
                saved.append(path)
                rows.append({
                    "city": city, "spot": spot, "note_id": note["note_id"],
                    "author": note.get("author", ""), "published_at": note.get("published_at", ""),
                    "note_title": note.get("title", ""), "image_index": position,
                    "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "width": width, "height": height, "bytes": len(data),
                    "sha256": digest, "source_url": note.get("note_url", ""),
                    "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "license_status": "reference-only", "review_status": "unreviewed",
                    "error": "",
                })
            except Exception as exc:
                rows.append({"city": city, "spot": spot, "note_id": note["note_id"],
                             "image_index": position, "error": f"download: {exc}"})
            time.sleep(0.2)
        time.sleep(0.3)
    _contact_sheet(saved, OUT / "contact_sheets" / f"{city}-{slug}.jpg")
    return rows


def main() -> int:
    with NOTES.open(encoding="utf-8-sig") as handle:
        notes = list(csv.DictReader(handle))

    by_spot: dict[tuple[str, str], list[dict]] = {}
    for row in notes:
        if row["usable_for_visual_reference"] != "True" or row["detail_error"]:
            continue
        for spot in row["matched_spots"].split(";"):
            if spot:
                by_spot.setdefault((row["city"], spot), []).append(row)

    tasks = []
    for (city, spot), items in by_spot.items():
        items.sort(key=lambda item: -int(item["likes"] or 0))
        tasks.append((city, spot, items))
    tasks.sort(key=lambda task: (CITY_ORDER.index(task[0]), task[1]))

    # One worker per MCP instance; each worker keeps its own port to itself.
    buckets = [tasks[index::len(PORTS)] for index in range(len(PORTS))]
    all_rows: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PORTS)) as pool:
        futures = []
        for port, bucket in zip(PORTS, buckets):
            futures.append(pool.submit(_run_bucket, port, bucket))
        for future in concurrent.futures.as_completed(futures):
            all_rows.extend(future.result())

    all_rows.sort(key=lambda row: (CITY_ORDER.index(row["city"]), row["spot"],
                                   row["note_id"], row.get("image_index") or 0))
    OUT.mkdir(parents=True, exist_ok=True)
    columns = ["city", "spot", "note_id", "author", "published_at", "note_title",
               "image_index", "file", "width", "height", "bytes", "sha256",
               "source_url", "fetched_at", "license_status", "review_status", "error"]
    with MANIFEST.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    with INDEX.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    ok = [row for row in all_rows if not row.get("error")]
    summary = {
        "spots": len(tasks),
        "images": len(ok),
        "failed": len(all_rows) - len(ok),
        "per_city": {
            city: sum(1 for row in ok if row["city"] == city)
            for city in CITY_ORDER if any(row["city"] == city for row in all_rows)
        },
        "manifest": str(MANIFEST),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _run_bucket(port: int, tasks: list[tuple[str, str, list[dict]]]) -> list[dict]:
    client = McpClient(port)
    rows = []
    for city, spot, items in tasks:
        try:
            rows.extend(_collect_spot(client, city, spot, items))
        except Exception as exc:
            rows.append({"city": city, "spot": spot, "note_id": "",
                         "error": f"spot: {exc}"})
    return rows


if __name__ == "__main__":
    sys.exit(main())
