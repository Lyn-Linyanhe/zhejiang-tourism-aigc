"""Minimal local client for the Xiaohongshu MCP server.

The MCP server is a local, already-authenticated browser bridge. This client
only reads search results and note metadata; it never downloads media and
never copies full note bodies into its output.

Usage:
    python scripts/xhs_mcp_client.py --port 18060 search --keyword 兰亭
    python scripts/xhs_mcp_client.py --port 18060 detail --feed-id ID --token TOKEN
    python scripts/xhs_mcp_client.py --port 18060 collect --keyword 兰亭 --spot 兰亭 --out runtime/_xhs_collect/lanting.json
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import msvcrt
import pathlib
import re
import tempfile
import time
import urllib.request


def _post(port: int, payload: dict, timeout: int = 180) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    if not body.strip():
        return {}
    data = json.loads(body)
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result") or {}


@contextlib.contextmanager
def _port_lock(port: int):
    path = pathlib.Path(tempfile.gettempdir()) / f"xhs_mcp_{port}.lock"
    with path.open("a+") as handle:
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class McpClient:
    def __init__(self, port: int) -> None:
        self.port = port
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        _post(self.port, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "zhejiang-xhs-pilot", "version": "1.0"},
            },
        })
        _post(self.port, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    def call(self, name: str, arguments: dict) -> dict:
        with _port_lock(self.port):
            self._ensure_initialized()
            result = _post(self.port, {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            })
        items = result.get("content") or []
        text = next((item.get("text", "") for item in items if item.get("type") == "text"), "")
        if result.get("isError"):
            raise RuntimeError(text or "MCP tool failed")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}


def _likes(value: object) -> int:
    text = str(value or "0").strip().replace(",", "")
    match = re.match(r"([0-9.]+)\s*([万wW]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    return int(number * 10000) if match.group(2) else int(number)


def _date(ms: object) -> str:
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _aspects(sizes: list[tuple[int, int]]) -> str:
    counts: dict[str, int] = {}
    for width, height in sizes:
        if not width or not height:
            continue
        if width / height > 1.15:
            label = "横图"
        elif height / width > 1.15:
            label = "竖图"
        else:
            label = "方图"
        counts[label] = counts.get(label, 0) + 1
    return "/".join(f"{key}{value}" for key, value in sorted(counts.items())) or "未知"


def _feed_row(feed: dict, spot: str, keyword: str) -> dict:
    card = feed.get("noteCard") or {}
    user = card.get("user") or {}
    interact = card.get("interactInfo") or {}
    cover = card.get("cover") or {}
    return {
        "spot": spot,
        "source_keyword": keyword,
        "note_id": str(feed.get("id") or ""),
        "xsec_token": str(feed.get("xsecToken") or ""),
        "title": str(card.get("displayTitle") or ""),
        "author": str(user.get("nickname") or user.get("nickName") or ""),
        "author_id": str(user.get("userId") or ""),
        "likes": _likes(interact.get("likedCount")),
        "collects": _likes(interact.get("collectedCount")),
        "comments": _likes(interact.get("commentCount")),
        "shares": _likes(interact.get("sharedCount")),
        "cover_width": int(cover.get("width") or 0),
        "cover_height": int(cover.get("height") or 0),
    }


def _detail_row(detail: dict, base: dict) -> dict:
    note = (detail.get("data") or {}).get("note") or {}
    images = note.get("imageList") or []
    sizes = [(int(item.get("width") or 0), int(item.get("height") or 0)) for item in images]
    interact = note.get("interactInfo") or {}
    user = note.get("user") or {}
    desc = str(note.get("desc") or "")
    row = dict(base)
    row.update({
        "note_id": str(note.get("noteId") or base.get("note_id") or ""),
        "title": str(note.get("title") or base.get("title") or ""),
        "author": str(user.get("nickname") or user.get("nickName") or base.get("author") or ""),
        "author_id": str(user.get("userId") or base.get("author_id") or ""),
        "published_at": _date(note.get("time")),
        "note_type": str(note.get("type") or ""),
        "ip_location": str(note.get("ipLocation") or ""),
        "likes": _likes(interact.get("likedCount", base.get("likes"))),
        "collects": _likes(interact.get("collectedCount", base.get("collects"))),
        "comments": _likes(interact.get("commentCount", base.get("comments"))),
        "shares": _likes(interact.get("sharedCount", base.get("shares"))),
        "image_count": len(images),
        "image_aspects": _aspects(sizes),
        "image_sizes": [f"{w}x{h}" for w, h in sizes[:12]],
        "desc_excerpt": desc.replace("\n", " ")[:300],
        "desc_chars": len(desc),
        "has_video": bool(note.get("video")),
    })
    return row


def collect(client: McpClient, spot: str, keyword: str, details: int, limit: int,
            sort_by: str, note_type: str, publish_time: str) -> dict:
    search = client.call("search_feeds", {
        "keyword": keyword,
        "filters": {"sort_by": sort_by, "note_type": note_type, "publish_time": publish_time},
    })
    feeds = [feed for feed in (search.get("feeds") or []) if feed.get("modelType") == "note"]
    feeds.sort(key=lambda feed: _likes((feed.get("noteCard") or {}).get("interactInfo", {}).get("likedCount")), reverse=True)
    feeds = feeds[:max(1, limit)]
    rows = []
    failures = []
    for feed in feeds[:max(0, details)]:
        base = _feed_row(feed, spot, keyword)
        try:
            detail = client.call("get_feed_detail", {
                "feed_id": base["note_id"], "xsec_token": base["xsec_token"],
                "load_all_comments": False,
            })
            rows.append(_detail_row(detail, base))
        except Exception as exc:  # keep the search row even if detail fails
            base["detail_error"] = str(exc)
            rows.append(base)
            failures.append(base["note_id"])
        time.sleep(0.3)
    return {
        "spot": spot, "keyword": keyword, "sort_by": sort_by,
        "note_type": note_type, "publish_time": publish_time,
        "search_count": len(search.get("feeds") or []),
        "collected_count": len(rows), "detail_failures": failures,
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Xiaohongshu MCP client")
    parser.add_argument("--port", type=int, default=18060)
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search")
    search.add_argument("--keyword", required=True)
    search.add_argument("--limit", type=int, default=20)

    detail = sub.add_parser("detail")
    detail.add_argument("--feed-id", required=True)
    detail.add_argument("--token", required=True)

    gather = sub.add_parser("collect")
    gather.add_argument("--spot", required=True)
    gather.add_argument("--keyword", required=True)
    gather.add_argument("--out", type=pathlib.Path, required=True)
    gather.add_argument("--limit", type=int, default=20)
    gather.add_argument("--details", type=int, default=10)
    gather.add_argument("--sort", default="最多点赞")
    gather.add_argument("--note-type", default="图文")
    gather.add_argument("--publish-time", default="半年内")

    args = parser.parse_args()
    client = McpClient(args.port)
    if args.command == "search":
        result = client.call("search_feeds", {"keyword": args.keyword})
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    if args.command == "detail":
        result = client.call("get_feed_detail", {"feed_id": args.feed_id, "xsec_token": args.token})
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0

    result = collect(client, args.spot, args.keyword, args.details, args.limit,
                     args.sort, args.note_type, args.publish_time)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "spot": args.spot, "collected": result["collected_count"],
        "failures": len(result["detail_failures"]), "out": str(args.out),
    }, ensure_ascii=True))
    return 0 if result["collected_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
