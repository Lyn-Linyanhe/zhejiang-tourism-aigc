"""Search stock footage, keeping attribution and the evidence for location matches."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

CITIES = dict(
    zip(
        "杭州 宁波 温州 嘉兴 湖州 绍兴 金华 衢州 舟山 台州 丽水".split(),
        "Hangzhou Ningbo Wenzhou Jiaxing Huzhou Shaoxing Jinhua Quzhou Zhoushan Taizhou Lishui".split(),
    )
)
LICENSE = {
    "pexels": "https://www.pexels.com/license/",
    "pixabay": "https://pixabay.com/service/license-summary/",
}


def _get(url, headers):
    return urllib.request.urlopen(
        urllib.request.Request(
            url, headers={"User-Agent": "ZhejiangTourism/2.0", **headers}
        ),
        timeout=30,
    )


@lru_cache(maxsize=256)
def search(platform, key, base, query, kind, day):
    video = kind == "video"
    headers = {}
    if platform == "pixabay":
        endpoint = "https://pixabay.com/api/" + ("videos/" if video else "")
        params = {"key": key, "q": query[:100], "safesearch": "true", "per_page": 30}
        if not video:
            params["image_type"] = "photo"
    else:
        endpoint = base.removesuffix("/v1") + (
            "/videos/search" if video else "/v1/search"
        )
        params = {"query": query, "per_page": 30}
        headers["Authorization"] = key
    try:
        with _get(endpoint + "?" + urllib.parse.urlencode(params), headers) as response:
            data = json.load(response)
    except Exception as exc:
        raise RuntimeError(
            f"{platform} 检索失败 ({getattr(exc, 'code', type(exc).__name__)})"
        ) from None
    return data.get(
        "hits" if platform == "pixabay" else "videos" if video else "photos", []
    )


SHOT_TERMS = {
    "船": ("boat", "cruise"),
    "舟": ("boat",),
    "桥": ("bridge",),
    "塔": ("pagoda", "tower"),
    "亭": ("pavilion",),
    "柳": ("willow",),
    "堤": ("causeway",),
    "花": ("flower", "blossom"),
    "山": ("mountain", "hill"),
    "日落": ("sunset",),
    "夜": ("night",),
    "街": ("street",),
    "航拍": ("aerial", "drone"),
    "荷": ("lotus",),
    "倒影": ("reflection",),
}


def _candidate(platform, raw, kind, size, duration, anchors, city, query, terms=()):
    video = kind == "video"
    if platform == "pexels":
        page = raw.get("url", "")
        author = (
            (raw.get("user") or {}).get("name") if video else raw.get("photographer")
        )
        variants = (
            [
                (f.get("width"), f.get("height"), f.get("link"))
                for f in raw.get("video_files", [])
                if f.get("file_type") == "video/mp4"
            ]
            if video
            else [
                (raw.get("width"), raw.get("height"), raw.get("src", {}).get("large2x"))
            ]
        )
    else:
        page, author = raw.get("pageURL", ""), raw.get("user")
        variants = (
            [
                (f.get("width"), f.get("height"), f.get("url"))
                for f in raw.get("videos", {}).values()
            ]
            if video
            else [
                (
                    raw.get("imageWidth"),
                    raw.get("imageHeight"),
                    raw.get("largeImageURL"),
                )
            ]
        )
    variants = [
        (w, h, url)
        for w, h, url in variants
        if w and h and url and url.startswith("https://")
    ]
    if not variants or not page or not raw.get("id"):
        return None
    width, height = size

    def quality(v):
        w, h, _ = v
        return (
            abs(math.log(w / h / (width / height)))
            + abs(math.log(max(w / width, h / height))) * 0.16
        )

    w, h, url = min(variants, key=quality)
    tags = raw.get("tags") or ""
    if isinstance(tags, list):
        tags = " ".join(str(t) for t in tags)
    evidence = re.sub(
        r"[-_]+",
        " ",
        urllib.parse.unquote(" ".join([page, tags, raw.get("alt") or ""])),
    ).casefold()
    matches = [word for word in anchors if word and word.casefold() in evidence]
    city_match = bool(city and city.casefold() in evidence)
    if (
        raw.get("is_ai_generated") is True
        or raw.get("ai_generated") is True
        or "ai generated" in evidence
    ):
        return None
    tier = 0 if matches and city_match else 1 if matches else 2 if city_match else 3
    label = (
        ("地标线索：" + matches[0])
        if matches
        else ("城市氛围：" + city if city_match else "氛围素材，地点待核实")
    )
    hits = sum(word in evidence for word in terms)
    score = (
        tier,
        -hits,
        max(0, duration - raw.get("duration", 0)) if video else 0,
        quality((w, h, url)),
    )
    return score, dict(
        id=f"{platform}_{kind}_{raw['id']}",
        source=platform,
        page_url=page,
        author=author or platform,
        license=LICENSE[platform],
        name=label,
        usage_scope="按素材页和平台许可使用",
        is_ai_generated=False,
        media_type=kind,
        location_evidence=label,
        download=url,
        search_query=query,
    )


class StockProvider:
    def __init__(self, services):
        self.services = [service for service in services if service[1]]

    def create_or_find(
        self, task_id, shot, output_dir, theme_color, accent, style_ctx=None
    ):
        ctx = style_ctx or {}
        w, h = map(int, ctx.get("output_resolution", "720x1280").split("x"))
        city = CITIES.get(shot.get("city"), shot.get("city", ""))
        landmark = shot.get("landmark", "")
        aliases = [
            s.strip() for s in shot.get("search_query", "").split(";") if s.strip()
        ]
        anchors = [landmark, *shot.get("landmark_aliases", [])]
        anchors = [a.strip() for a in anchors if a.strip()]
        if city:
            anchors += [
                a.casefold().removesuffix(" " + city.casefold()).strip()
                for a in anchors
                if a.casefold().endswith(" " + city.casefold())
            ]
        detail = next((s for s in aliases if s.isascii()), "")
        queries = list(
            dict.fromkeys(
                filter(
                    None,
                    [
                        (" ".join(filter(None, [detail, city]))).strip()
                        if detail
                        else "",
                        " ".join(
                            filter(
                                None,
                                [
                                    next(
                                        (a for a in anchors if a and a.isascii()),
                                        landmark,
                                    ),
                                    city,
                                ],
                            )
                        ),
                        city,
                        detail or shot.get("title"),
                    ],
                )
            )
        )
        prompt = (shot.get("title") or "") + " " + (shot.get("visual_prompt") or "")
        terms = {
            word
            for name, words in SHOT_TERMS.items()
            if name in prompt
            for word in words
        }
        used = ctx.get("stock_ids", set())
        lock = ctx.get("stock_lock") or threading.Lock()
        errors = []
        output_dir.mkdir(exist_ok=True, parents=True)
        for kind in ("video", "image"):
            ranked = {}
            for query in queries:
                for platform, key, base in self.services:
                    try:
                        rows = search(
                            platform, key, base, query, kind, int(time.time() // 86400)
                        )
                    except RuntimeError as exc:
                        errors.append(str(exc))
                        continue
                    for raw in rows:
                        candidate = _candidate(
                            platform,
                            raw,
                            kind,
                            (w, h),
                            float(shot.get("duration_sec", 0)),
                            anchors,
                            city,
                            query,
                            terms,
                        )
                        if candidate:
                            ranked.setdefault(candidate[1]["id"], candidate)
                if any(
                    score[0] == 0 and a["id"] not in used
                    for score, a in ranked.values()
                ):
                    break
            for _, asset in sorted(ranked.values(), key=lambda entry: entry[0]):
                uid = asset["id"]
                with lock:
                    if uid in used:
                        continue
                    used.add(uid)
                path = output_dir / (
                    shot["id"] + "-" + uid + (".mp4" if kind == "video" else ".jpg")
                )
                tmp = path.with_suffix(".part")
                try:
                    with (
                        _get(asset.pop("download"), {}) as response,
                        tmp.open("wb") as target,
                    ):
                        shutil.copyfileobj(response, target)
                    probe = subprocess.run(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-select_streams",
                            "v:0",
                            "-show_entries",
                            "stream=width,height",
                            "-of",
                            "csv=p=0",
                            str(tmp),
                        ],
                        capture_output=True,
                        timeout=20,
                    )
                    if probe.returncode or not probe.stdout.strip():
                        raise ValueError("无法解码素材")
                    tmp.replace(path)
                except Exception as exc:
                    tmp.unlink(missing_ok=True)
                    with lock:
                        used.discard(uid)
                    errors.append(f"{uid}: {type(exc).__name__}")
                    continue
                return dict(
                    asset,
                    external_id=uid,
                    id=f"{task_id}_{shot['id']}_{uid}",
                    url=f"/media/{task_id}/assets/{path.name}",
                    local_path=str(path),
                )
        raise RuntimeError(
            "素材库没有可用的独立镜头。" + " / ".join(dict.fromkeys(errors))[:400]
        )
