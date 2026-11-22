"""Batch-collect Xiaohongshu notes through the logged-in in-app browser.

The MCP server has its own cookie jar and gets rate-limited; the app's browser
is logged in and its `__INITIAL_STATE__` already holds exactly the metadata we
need. This script is the Node-side driver; it is meant to be run from the Node
REPL browser tool, not from a plain shell.

Protocol
--------
Call `collectSpot(tab, { spot, keyword, details })` for each spot. It:
  1. navigates to the site's own search page,
  2. reads candidate rows from `window.__INITIAL_STATE__.search.feeds`,
  3. sorts by likes and opens the top N note pages one at a time,
  4. reads each note from `window.__INITIAL_STATE__.note.noteDetailMap`.

Navigation happens on the Node side because navigating from inside the page
destroys the execution context. Keep the tab bound across the whole batch.

Usage from the browser tool
---------------------------
    const out = await collectSpot(tab, { spot: "云和梯田", keyword: "云和梯田", details: 10 });
    await saveShard(out, "yunhetitian");

`saveShard` writes `runtime/_xhs_collect_multi/<name>.json` in the same shape as
`scripts/xhs_mcp_client.py collect`, so `scripts/xhs_aggregate_notes.py` picks it
up unchanged.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runtime/_xhs_collect_multi"

# --- page-side snippets -------------------------------------------------
# Keep these as plain function sources so they can be pasted into
# `tab.playwright.evaluate(...)` from the browser tool.

SEARCH_JS = r"""
() => {
  const state = window.__INITIAL_STATE__ || {};
  const src = (state.search && state.search.feeds) || (state.feed && state.feed.feeds);
  const raw = src ? (src._rawValue || src) : [];
  const arr = Array.isArray(raw) ? raw : [];
  const likeNum = (v) => {
    if (v === null || v === undefined) return 0;
    const s = String(v).replace(/,/g, '').trim();
    const m = s.match(/([0-9.]+)\s*([万wW]?)/);
    if (!m) return 0;
    return m[2] ? Math.round(parseFloat(m[1]) * 10000) : parseInt(m[1], 10);
  };
  return arr.map((feed) => {
    const card = feed.noteCard || {};
    const info = card.interactInfo || {};
    return {
      note_id: String(feed.id || ''),
      xsec_token: String(feed.xsecToken || ''),
      title: String(card.displayTitle || ''),
      author: String((card.user || {}).nickname || (card.user || {}).nickName || ''),
      author_id: String((card.user || {}).userId || ''),
      likes: likeNum(info.likedCount),
      collects: likeNum(info.collectedCount),
      comments: likeNum(info.commentCount),
      shares: likeNum(info.sharedCount),
      note_type: String(card.type || ''),
    };
  }).filter((row) => row.note_id && row.xsec_token);
}
"""

DETAIL_JS = r"""
() => {
  const state = window.__INITIAL_STATE__ || {};
  const map = (state.note && state.note.noteDetailMap) || {};
  const keys = Object.keys(map).filter((k) => k && k !== 'undefined');
  if (!keys.length) return { ok: false, reason: 'detail not loaded', is404: location.href.includes('/404') };
  const note = map[keys[0]].note;
  if (!note) return { ok: false, reason: 'no note object', is404: location.href.includes('/404') };
  const imgs = note.imageList || [];
  const desc = String(note.desc || '');
  const counts = {};
  for (const im of imgs) {
    const w = im.width || 0, h = im.height || 0;
    if (!w || !h) continue;
    const k = w / h > 1.15 ? '横图' : (h / w > 1.15 ? '竖图' : '方图');
    counts[k] = (counts[k] || 0) + 1;
  }
  const likeNum = (v) => {
    if (v === null || v === undefined) return 0;
    const s = String(v).replace(/,/g, '').trim();
    const m = s.match(/([0-9.]+)\s*([万wW]?)/);
    if (!m) return 0;
    return m[2] ? Math.round(parseFloat(m[1]) * 10000) : parseInt(m[1], 10);
  };
  const info = note.interactInfo || {};
  return {
    ok: true,
    note_id: String(note.noteId || ''),
    title: String(note.title || ''),
    author: String((note.user || {}).nickname || (note.user || {}).nickName || ''),
    author_id: String((note.user || {}).userId || ''),
    published_at: note.time ? new Date(note.time).toISOString().slice(0, 10) : '',
    note_type: String(note.type || ''),
    ip_location: String(note.ipLocation || ''),
    likes: likeNum(info.likedCount),
    collects: likeNum(info.collectedCount),
    comments: likeNum(info.commentCount),
    shares: likeNum(info.sharedCount),
    image_count: imgs.length,
    image_aspects: Object.keys(counts).sort().map((k) => k + counts[k]).join('/') || '未知',
    image_sizes: imgs.slice(0, 12).map((i) => `${i.width || 0}x${i.height || 0}`),
    desc_excerpt: desc.replace(/\n/g, ' ').slice(0, 300),
    desc_chars: desc.length,
    has_video: !!note.video,
  };
}
"""

# Spots still missing complete details, with the keyword used for search.
PENDING = {
    "西塘": "西塘古镇",
    "莫干山": "莫干山",
    "太湖旅游度假区": "湖州太湖 月亮湾",
    "廿八都古镇": "衢州廿八都古镇",
    "龙游石窟": "衢州龙游石窟",
    "朱家尖": "舟山朱家尖",
    "石塘": "温岭石塘 七彩小箬村",
    "云和梯田": "云和梯田",
    "松阳古村落": "丽水松阳 杨家堂 古村落",
}

SLUGS = {
    "西塘": "xitang",
    "莫干山": "moganshan",
    "太湖旅游度假区": "taihu",
    "廿八都古镇": "nianbadu",
    "龙游石窟": "longyoushiku",
    "朱家尖": "zhujiajian",
    "石塘": "shitang",
    "云和梯田": "yunhetitian",
    "松阳古村落": "songyang",
}


def shard_path(spot: str) -> pathlib.Path:
    return OUT_DIR / f"{SLUGS[spot]}.json"


def save_shard(payload: dict, name: str) -> pathlib.Path:
    """Write a shard in the same shape as the MCP collector."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    items = payload.get("items", [])
    body = {
        "spot": payload.get("spot", ""),
        "keyword": payload.get("keyword", ""),
        "sort_by": "最多点赞",
        "note_type": "不限",
        "publish_time": "不限",
        "search_count": payload.get("search_count", len(items)),
        "collected_count": len(items),
        "detail_failures": [i["note_id"] for i in items if i.get("detail_error")],
        "items": items,
        "collected_via": "iab-initial-state",
        "collected_at": payload.get("collected_at", ""),
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def report() -> int:
    """Print which pending spots still have detail failures."""
    missing = []
    for spot in PENDING:
        path = shard_path(spot)
        if not path.exists():
            missing.append((spot, "no shard"))
            continue
        body = json.loads(path.read_text(encoding="utf-8"))
        items = body.get("items", [])
        failed = sum(1 for i in items if i.get("detail_error"))
        via = body.get("collected_via", "mcp")
        if failed:
            missing.append((spot, f"{failed}/{len(items)} failed ({via})"))
        else:
            print(f"OK   {spot}: {len(items)} 条完整 ({via})")
    for spot, reason in missing:
        print(f"TODO {spot}: {reason}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(report())
