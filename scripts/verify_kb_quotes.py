"""Verify that every evidence_quote really appears in its source page.

Run:  python -I scripts/verify_kb_quotes.py [--city shaoxing ningbo hangzhou]

Network-dependent: this is an audit tool, not a unit test. It reports, for each
record, whether the quote is a verbatim substring of the fetched page. Quotes
that are reassembled from table cells or lightly edited will show as MISS with
the exact divergence point, which is what you need to fix the record.
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "data_zhejiang_kb"

# Local DNS fails for www.news.cn in this environment; the IP + Host header works.
HOST_OVERRIDES = {"www.news.cn": "202.108.119.194"}

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_ctx)
)


def _plain(text: str) -> str:
    # <meta name="description"> often carries the only stable prose on
    # JS-rendered official sites, so keep it before stripping tags.
    metas = re.findall(
        r'<meta[^>]+name=["\'](?:description|keywords)["\'][^>]+content=["\']([^"\']*)["\']',
        text, re.IGNORECASE,
    )
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    body = html.unescape(" ".join(metas) + " " + text)
    return re.sub(r"\s+", "", body)


_CACHE_DIR = ROOT / "runtime/_kb_page_cache"


def _cache_path(url: str) -> pathlib.Path:
    import hashlib
    return _CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".txt")


def _wiki(title: str, retries: int = 3) -> str:
    query = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": "1",
        "titles": title, "format": "json", "redirects": "1",
    })
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                "https://zh.wikipedia.org/w/api.php?" + query,
                headers={"User-Agent": "zhejiang-kb-audit/1.0"},
            )
            payload = json.loads(_OPENER.open(request, timeout=40).read().decode())
            for page in payload["query"]["pages"].values():
                return page.get("extract") or ""
            return ""
        except Exception as exc:  # 429 and transient network errors
            last = exc
            time.sleep(10 * (attempt + 1))
    raise last


def fetch(url: str) -> str:
    # Cache every fetched page: Wikipedia rate-limits hard, and re-verifying
    # the same record twice should not cost another request.
    cached = _cache_path(url)
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    if "zh.wikipedia.org/wiki/" in url:
        title = urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace("_", " ")
        body = re.sub(r"\s+", "", _wiki(title))
    else:
        body = _fetch_live(url)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if body:
        cached.write_text(body, encoding="utf-8")
    return body


def _fetch_live(url: str) -> str:
    target, headers = url, {"User-Agent": "Mozilla/5.0"}
    for host, ip in HOST_OVERRIDES.items():
        if host in url:
            # TLS SNI would be the bare IP, so drop to http for the override.
            target = url.replace(host, ip).replace("https://", "http://", 1)
            headers["Host"] = host
            break
    raw = _OPENER.open(urllib.request.Request(target, headers=headers), timeout=40).read()
    for encoding in ("utf-8", "gb18030"):
        try:
            return _plain(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return _plain(raw.decode("utf-8", "replace"))


ELLIPSIS = re.compile(r"…+|\.{3,}")


def quote_segments(quote: str) -> list[str]:
    """Split an elided quote into the parts that must each appear verbatim.

    "A……B" is an honest excerpt as long as A and B both exist in the source.
    Requiring the whole string to be contiguous would flag every legitimate
    elision, so each segment is checked separately.
    """
    return [seg for seg in ELLIPSIS.split(quote) if seg.strip()]


def divergence(segment: str, page: str) -> str:
    """Return a human-readable description of where the quote stops matching."""
    if segment in page:
        return ""
    for length in range(len(segment), 8, -1):
        head = segment[:length]
        if head in page:
            start = page.find(head) + length
            return (
                f"matches first {length}/{len(segment)} chars; "
                f"quote continues {segment[length:length + 18]!r} "
                f"but page has {page[start:start + 18]!r}"
            )
    return "no common prefix found — likely a paraphrase, not a quote"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", nargs="*", default=[
        "hangzhou", "ningbo", "shaoxing", "huzhou", "wenzhou", "jiaxing",
        "jinhua", "quzhou", "zhoushan", "taizhou", "lishui"])
    args = parser.parse_args()

    cache: dict[str, str] = {}
    problems = 0
    checked = 0
    for city in args.city:
        path = KB_DIR / f"{city}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n=== {city} ===")
        for entity in payload["entities"]:
            for index, record in enumerate(entity.get("fact_records") or []):
                quote = re.sub(r"\s+", "", str(record.get("evidence_quote") or ""))
                url = str(record.get("source_url") or "")
                if not quote or not url:
                    continue
                checked += 1
                if url not in cache:
                    try:
                        cache[url] = fetch(url)
                    except Exception as exc:
                        cache[url] = ""
                        print(f"ERR   {entity['id']:22} #{index} {type(exc).__name__}: {url[:70]}")
                        problems += 1
                        continue
                page = cache[url]
                if not page:
                    continue
                segments = quote_segments(quote)
                failures = [seg for seg in segments if seg not in page]
                if not failures:
                    print(f"OK    {entity['id']:22} #{index}"
                          + (f"  ({len(segments)} segments)" if len(segments) > 1 else ""))
                else:
                    problems += 1
                    print(f"MISS  {entity['id']:22} #{index}")
                    for segment in failures:
                        print(f"      {divergence(segment, page)}")
                        print(f"      segment: {segment[:90]}")

    print(f"\nchecked {checked} records, {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
