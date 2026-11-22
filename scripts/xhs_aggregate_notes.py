"""Aggregate the multi-city XHS collect shards into one provincial CSV.

Inputs:
  - runtime/_xhs_collect_multi/*.json  (Hangzhou / Ningbo / 8 light-coverage cities)
  - docs/research/xhs-shaoxing-mcp-notes.csv  (the earlier Shaoxing pilot)

Dedupes by note id, keeps every row, and adds a `city` column. Flags stay
conservative: video / empty title / spot not mentioned / older than 180 days
are marked, never silently dropped.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "runtime/_xhs_collect_multi"
SHAOXING = ROOT / "docs/research/xhs-shaoxing-mcp-notes.csv"
OUT = ROOT / "docs/research/xhs-zhejiang-mcp-notes.csv"
RUNTIME_FULL = SRC / "notes_with_tokens.csv"

SPOT_CITY = {
    "西湖": "杭州",
    "灵隐寺·飞来峰": "杭州",
    "西溪国家湿地公园": "杭州",
    "京杭大运河·拱宸桥": "杭州",
    "良渚古城遗址": "杭州",
    "天一阁·月湖": "宁波",
    "老外滩": "宁波",
    "东钱湖": "宁波",
    "兰亭": "绍兴",
    "东湖": "绍兴",
    "安昌古镇": "绍兴",
    "仓桥直街": "绍兴",
    "鲁迅故里·沈园": "绍兴",
    "大禹陵": "绍兴",
    "江心屿": "温州",
    "雁荡山": "温州",
    "楠溪江": "温州",
    "南湖": "嘉兴",
    "乌镇": "嘉兴",
    "西塘": "嘉兴",
    "南浔古镇": "湖州",
    "莫干山": "湖州",
    "太湖旅游度假区": "湖州",
    "横店影视城": "金华",
    "双龙洞": "金华",
    "诸葛八卦村": "金华",
    "江郎山": "衢州",
    "廿八都古镇": "衢州",
    "龙游石窟": "衢州",
    "普陀山": "舟山",
    "朱家尖": "舟山",
    "东极岛": "舟山",
    "台州府城墙": "台州",
    "神仙居": "台州",
    "石塘": "台州",
    "仙都": "丽水",
    "云和梯田": "丽水",
    "松阳古村落": "丽水",
}
CITY_ORDER = ["杭州", "宁波", "绍兴", "温州", "嘉兴", "湖州", "金华", "衢州", "舟山", "台州", "丽水"]

ALIASES = {
    "西湖": ["西湖"],
    "灵隐寺·飞来峰": ["灵隐", "飞来峰"],
    "西溪国家湿地公园": ["西溪"],
    "京杭大运河·拱宸桥": ["拱宸桥", "运河"],
    "良渚古城遗址": ["良渚"],
    "天一阁·月湖": ["天一阁", "月湖"],
    "老外滩": ["老外滩"],
    "东钱湖": ["东钱湖"],
    "兰亭": ["兰亭"],
    "东湖": ["东湖"],
    "安昌古镇": ["安昌"],
    "仓桥直街": ["仓桥"],
    "鲁迅故里·沈园": ["鲁迅故里", "鲁迅故居", "沈园", "百草园", "三味书屋"],
    "大禹陵": ["大禹陵", "禹陵", "禹庙"],
    "江心屿": ["江心屿"],
    "雁荡山": ["雁荡"],
    "楠溪江": ["楠溪江"],
    "南湖": ["南湖"],
    "乌镇": ["乌镇"],
    "西塘": ["西塘"],
    "南浔古镇": ["南浔"],
    "莫干山": ["莫干山"],
    "太湖旅游度假区": ["太湖", "月亮湾"],
    "横店影视城": ["横店"],
    "双龙洞": ["双龙洞"],
    "诸葛八卦村": ["诸葛", "八卦村"],
    "江郎山": ["江郎山"],
    "廿八都古镇": ["廿八都"],
    "龙游石窟": ["龙游石窟", "石窟"],
    "普陀山": ["普陀山"],
    "朱家尖": ["朱家尖"],
    "东极岛": ["东极"],
    "台州府城墙": ["台州府城墙", "府城墙", "江南长城", "揽胜门"],
    "神仙居": ["神仙居"],
    "石塘": ["石塘", "小箬"],
    "仙都": ["仙都", "鼎湖峰"],
    "云和梯田": ["云和梯田", "云和"],
    "松阳古村落": ["松阳", "杨家堂"],
}

COLUMNS = [
    "city", "matched_spots", "source_keywords", "note_id", "note_url", "xsec_token",
    "title", "author", "author_id", "published_at", "note_type",
    "likes", "collects", "comments", "shares", "image_count",
    "image_aspects", "image_sizes", "has_video", "is_video", "spot_mentioned",
    "empty_title", "freshness", "usable_for_visual_reference",
    "ip_location", "desc_excerpt", "desc_chars", "detail_error",
    "review_status", "license_status",
]


def _freshness(published_at: str, today: dt.date) -> str:
    try:
        date = dt.date.fromisoformat(published_at)
    except ValueError:
        return "unknown"
    return "current-180d" if (today - date).days <= 180 else "older-180d"


def _mentioned(title: str, desc: str, spots: list[str]) -> bool:
    text = title + " " + desc
    return any(alias in text for spot in spots for alias in ALIASES.get(spot, [spot]))


def _flag(value: object) -> bool:
    return str(value).strip().lower() == "true"


def _city_of(spots: list[str]) -> str:
    for spot in spots:
        if spot in SPOT_CITY:
            return SPOT_CITY[spot]
    return ""


def main() -> int:
    merged: dict[str, dict] = {}
    errors = []

    # 1) Earlier Shaoxing pilot CSV (already curated and flagged).
    with SHAOXING.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            note_id = row["note_id"]
            spots = [s for s in re.split(r"[;|]", row["matched_spots"]) if s]
            row["_spots"] = spots
            row["_keywords"] = [k for k in re.split(r"[;|]", row["source_keywords"]) if k]
            merged[note_id] = row

    # 2) New multi-city shards.
    shards = sorted(path for path in SRC.glob("*.json") if path.name != "aggregate.json")
    for shard in shards:
        payload = json.loads(shard.read_text(encoding="utf-8"))
        spot = str(payload.get("spot") or "")
        if spot not in SPOT_CITY:
            errors.append(f"{shard.name}: unknown spot {spot!r}")
            continue
        keyword = str(payload.get("keyword") or "")
        for item in payload.get("items") or []:
            note_id = str(item.get("note_id") or "")
            if not note_id:
                continue
            if note_id in merged:
                row = merged[note_id]
                if spot and spot not in row["_spots"]:
                    row["_spots"].append(spot)
                if keyword and keyword not in row["_keywords"]:
                    row["_keywords"].append(keyword)
                continue
            row = dict(item)
            row["_spots"] = [spot]
            row["_keywords"] = [keyword] if keyword else []
            merged[note_id] = row

    today = dt.date.today()
    rows = []
    for row in merged.values():
        title = str(row.get("title") or "").strip()
        desc = str(row.get("desc_excerpt") or "")
        spots = row["_spots"]
        city = _city_of(spots)
        if not city:
            errors.append(f"{row.get('note_id')}: no city for spots {spots}")
            continue
        image_count = int(row.get("image_count") or 0)
        note_type = str(row.get("note_type") or "")
        is_video = (_flag(row.get("is_video"))
                    or _flag(row.get("has_video"))
                    or note_type == "video"
                    or image_count <= 1)
        sizes = row.get("image_sizes") or []
        sizes_text = ";".join(sizes) if isinstance(sizes, list) else str(sizes)
        mentioned = _mentioned(title, desc, spots)
        rows.append({
            "city": city,
            "matched_spots": ";".join(spots),
            "source_keywords": ";".join(row["_keywords"]),
            "note_id": row.get("note_id", ""),
            "note_url": row.get("note_url") or f"https://www.xiaohongshu.com/explore/{row.get('note_id', '')}",
            "xsec_token": row.get("xsec_token", ""),
            "title": title,
            "author": row.get("author", ""),
            "author_id": row.get("author_id", ""),
            "published_at": row.get("published_at", ""),
            "note_type": row.get("note_type", ""),
            "likes": row.get("likes", 0),
            "collects": row.get("collects", 0),
            "comments": row.get("comments", 0),
            "shares": row.get("shares", 0),
            "image_count": image_count,
            "image_aspects": row.get("image_aspects", ""),
            "image_sizes": sizes_text,
            "has_video": str(bool(row.get("has_video"))),
            "is_video": str(is_video),
            "spot_mentioned": str(mentioned),
            "empty_title": str(not title),
            "freshness": _freshness(str(row.get("published_at") or ""), today),
            "usable_for_visual_reference": str(not is_video and bool(title) and mentioned),
            "ip_location": row.get("ip_location", ""),
            "desc_excerpt": desc,
            "desc_chars": row.get("desc_chars", 0),
            "detail_error": row.get("detail_error", ""),
            "review_status": "metadata-only",
            "license_status": "reference-only",
        })

    if errors:
        print("WARNINGS")
        for error in errors:
            print(" -", error)

    rows.sort(key=lambda item: (CITY_ORDER.index(item["city"]), -int(item["likes"] or 0), item["note_id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with RUNTIME_FULL.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    # xsec_token is session-sensitive: keep it only in runtime, never in docs/.
    for row in rows:
        row["xsec_token"] = ""
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    per_city = {}
    for row in rows:
        bucket = per_city.setdefault(row["city"], {"notes": 0, "usable": 0, "spots": set()})
        bucket["notes"] += 1
        bucket["usable"] += row["usable_for_visual_reference"] == "True"
        bucket["spots"].update(row["matched_spots"].split(";"))
    # Per-spot detail coverage: a spot whose details mostly failed yields
    # metadata-only rows with no date/images, so it must be visible in the
    # summary instead of silently inflating the note count.
    per_spot = {}
    for shard in shards:
        payload = json.loads(shard.read_text(encoding="utf-8"))
        spot = str(payload.get("spot") or "")
        if not spot:
            continue
        items = payload.get("items") or []
        failed = sum(1 for item in items if item.get("detail_error"))
        bucket = per_spot.setdefault(spot, {"total": 0, "failed": 0})
        bucket["total"] += len(items)
        bucket["failed"] += failed
    incomplete = {
        spot: info for spot, info in per_spot.items()
        if info["total"] and info["failed"] / info["total"] > 0.3
    }

    summary = {
        "shards": len(shards),
        "unique": len(rows),
        "usable": sum(1 for row in rows if row["usable_for_visual_reference"] == "True"),
        "videos": sum(1 for row in rows if row["is_video"] == "True"),
        "empty_title": sum(1 for row in rows if row["empty_title"] == "True"),
        "not_mentioned": sum(1 for row in rows if row["spot_mentioned"] == "False"),
        "older": sum(1 for row in rows if row["freshness"] == "older-180d"),
        "detail_failed": sum(1 for row in rows if row["detail_error"]),
        "per_city": {
            city: {"notes": info["notes"], "usable": info["usable"], "spots": len(info["spots"])}
            for city, info in per_city.items()
        },
        "incomplete_spots": incomplete,
        "out": str(OUT),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
