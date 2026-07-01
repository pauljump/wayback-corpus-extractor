"""
archivist.engine — the generic Wayback Machine harvesting engine.

Turns archived web pages into a structured, fully-provenanced corpus. The engine is
topic-agnostic: it takes a Recipe (what to hunt, where, and the extraction schema) plus
an `extract` callable (text -> list[dict]) and runs a resumable discovery + extraction
loop against the Internet Archive.

Everything topic-specific lives in the Recipe. Everything here is reusable.

The careful bits — and why they exist — are kept verbatim from the original grind:
  * wb_open: one polite gateway for every archive.org call (inter-request gap +
    exponential backoff). Wayback throttles a hot IP and silently breaks the trail.
  * snap: exact `id_` capture for real timestamps, but go STRAIGHT to the nearest-
    snapshot redirect for discovered links (a sentinel ts) — chasing an exact capture
    that doesn't exist burns the backoff and stalls the harvest.
  * the frontier: a seed queue PLUS everything the community link-graph turns up, so we
    reach pages that *demonstrate* an interest without naming it (keyword-blindness fix).
  * resume: append-only JSONL + a done-set, so a reboot/sleep never loses or repeats work.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Callable

from .recipe import Recipe

JUNK = re.compile(
    r"\.(jpg|jpeg|png|gif|css|js|ico|pdf|zip)|/download/|file\.php|avatar|posting\.php|"
    r"memberlist|/wp-(login|admin|json)|/feed|/login|/register|/tags?/|/page/\d|replytocom|"
    r"[?&]m=\d|[?&]share=|#comment|/categories?/|/forums/$",
    re.I,
)

RD_THREAD_RE = re.compile(r"/r/([A-Za-z0-9_]{2,30})/comments/([a-z0-9]{4,12})", re.I)
SUBMENT_RE = re.compile(r"\br/([A-Za-z0-9_]{3,30})\b")
EXT_RE = re.compile(r"https?://[A-Za-z0-9.\-]+/[^\s\"'<>]*", re.I)

SNAP_SENTINEL = "2020"  # any ts -> Wayback redirects to the temporally-nearest capture
OUTLINKS_PER_PAGE = 15
FRONTIER_MAX = 60000
NEW_SUB_MAX = 15


class Engine:
    def __init__(self, recipe: Recipe, extract: Callable[[str], list | None], log=print):
        self.r = recipe
        self.extract = extract
        self.log = log
        self.wb_gap = recipe.wayback_gap
        self._last_wb = 0.0

        os.makedirs(recipe.out_dir, exist_ok=True)
        self.done_f = os.path.join(recipe.out_dir, "done_urls.txt")
        self.items_f = os.path.join(recipe.out_dir, "items.jsonl")
        self.subs_f = os.path.join(recipe.out_dir, "subs_harvested.txt")

        self.triage = re.compile(recipe.filters["triage"], re.I) if recipe.filters.get("triage") else None
        self.relevant_sub = (
            re.compile(recipe.trail["relevant_sub"], re.I) if recipe.trail.get("relevant_sub") else None
        )
        self.allow_ext = set(recipe.sources.get("blogs", [])) | set(recipe.trail.get("allow_ext", []))
        self.follow_ext = bool(recipe.trail.get("follow_ext", False))

    # ---- archive.org gateway: polite gap + backoff (a hot IP gets throttled) ----
    def wb_open(self, u, timeout=30, tries=4):
        for i in range(tries):
            gap = time.time() - self._last_wb
            if gap < self.wb_gap:
                time.sleep(self.wb_gap - gap)
            try:
                with urllib.request.urlopen(u, timeout=timeout) as resp:
                    data = resp.read()
                self._last_wb = time.time()
                return data
            except Exception:
                self._last_wb = time.time()
                if i == tries - 1:
                    raise
                time.sleep(2.0 * (i + 1))  # 2s, 4s, 6s
        return None

    def cdx(self, prefix, limit=5000, listing=False):
        u = (
            "http://web.archive.org/cdx/search/cdx?url="
            + urllib.parse.quote(prefix)
            + f"&matchType=prefix&output=json&collapse=urlkey&fl=timestamp,original&limit={limit}"
        )
        if listing and self.r.filters.get("listing"):
            u += "&filter=original:(?i).*(" + self.r.filters["listing"] + ").*"
        try:
            rows = json.loads(self.wb_open(u, timeout=120))
            return [(ts, url) for ts, url in rows[1:]]
        except Exception as e:
            self.log(f"  cdx fail {prefix}: {e}")
            return []

    @staticmethod
    def canon(url):
        """Collapse page variants to one identity. Forum threads are identified by the
        thread id in the query (?t= / /comments/<id>), NOT the path — preserve it or all
        threads merge into one."""
        tm = re.search(r"viewtopic\.php.*?\bt=(\d+)", url, re.I)
        if tm:
            host = re.sub(r"^https?://", "", url).split("/")[0].replace(":80", "")
            return f"{host}/forums/viewtopic.php?t={tm.group(1)}".lower()
        rm = re.search(r"reddit\.com/r/(\w+)/comments/(\w+)", url, re.I)
        if rm:
            return f"reddit.com/r/{rm.group(1)}/comments/{rm.group(2)}".lower()
        url = re.sub(r"[?#].*$", "", url)
        url = re.sub(r":80(/|$)", r"\1", url)
        url = re.sub(r"/(amp|feed|comment-page-\d+)/?$", "/", url, flags=re.I)
        return url.rstrip("/").lower()

    def snap(self, ts, url):
        # Exact 14-digit ts (from CDX): raw `id_` first (fast, exact), then nearest-snapshot
        # fallback. A sentinel/partial ts (a discovered link) goes STRAIGHT to the redirect —
        # the `id_` form has no exact capture, so it just errors and burns the backoff.
        mods = ("id_", "") if (ts and len(ts) >= 12 and ts.isdigit()) else ("",)
        for mod in mods:
            try:
                h = (self.wb_open(f"http://web.archive.org/web/{ts}{mod}/{url}", timeout=30) or b"").decode(
                    "utf-8", "ignore"
                )
                if len(h) > 500:
                    return h
            except Exception:
                pass
        return ""

    @staticmethod
    def html_to_text(h):
        h = re.sub(r"<script[\s\S]*?</script>", " ", h, flags=re.I)
        h = re.sub(r"<style[\s\S]*?</style>", " ", h, flags=re.I)
        h = re.sub(r"<[^>]+>", " ", h)
        h = re.sub(r"&[a-z]+;", " ", h, flags=re.I)
        return re.sub(r"\s+", " ", h).strip()

    # ---- trail-following: let the community link-graph lead discovery ----
    def harvest(self, h):
        urls, subs = [], set()
        for sub, tid in RD_THREAD_RE.findall(h):
            urls.append(f"https://old.reddit.com/r/{sub}/comments/{tid}/")
        for host, base in [(s["host"], s.get("base", "")) for s in self.r.sources.get("phpbb", [])]:
            wp_re = re.compile(rf"({re.escape(host)}/[^\s\"'<>]*viewtopic\.php\?[^\s\"'<>]*\bt=\d+)", re.I)
            for t in wp_re.findall(h):
                urls.append("https://" + t)
        if self.follow_ext:
            for m in EXT_RE.findall(h):
                dom = re.sub(r"^https?://(www\.)?", "", m).split("/")[0].lower()
                if any(dom == a or dom.endswith("." + a) for a in self.allow_ext):
                    urls.append(m)
        if self.relevant_sub:
            for s in SUBMENT_RE.findall(h):
                if self.relevant_sub.search(s):
                    subs.add(s)
        return urls, subs

    def enum_sub(self, sub, seen, done_canon, frontier):
        added = 0
        for ts, url in self.cdx(f"old.reddit.com/r/{sub}/comments", limit=2000, listing=True):
            if JUNK.search(url) or "/comments/" not in url:
                continue
            c = self.canon(url)
            if not c or c in seen or c in done_canon:
                continue
            seen.add(c)
            frontier.append((ts, url, f"sub:r/{sub}"))
            added += 1
        return added

    # ---- discovery: build the seed queue from the recipe's sources ----
    def _seed(self, seen, done_canon):
        r = self.r
        queue = []

        for dom in r.sources.get("blogs", []):
            kept = 0
            for ts, url in self.cdx(f"{dom}/", limit=5000, listing=True):
                if JUNK.search(url):
                    continue
                c = self.canon(url)
                if not c or c in seen or c in done_canon:
                    continue
                seen.add(c)
                queue.append((ts, url))
                kept += 1
            self.log(f"  {dom}: {kept} listing pages")

        # phpBB-style forums: no URL slug, so crawl viewforum index pages, parse thread
        # titles, and keep the ones whose title matches the listing filter (densest source).
        title_re = re.compile(r"" + (r.filters.get("listing") or r".") , re.I)
        thread_re = re.compile(r"""viewtopic\.php\?[^"'<>]*\bt=(\d+)[^"'<>]*["'][^>]*>\s*([^<]{4,90})<""")
        for s in r.sources.get("phpbb", []):
            dom, base = s["host"], s.get("base", s["host"] + "/forums/")
            idx_seen, gold, t0 = set(), 0, time.time()
            for ts, idx_url in self.cdx(base + "viewforum.php", limit=8000):
                fm = re.search(r"[?&]f=(\d+)", idx_url)
                if not fm:
                    continue
                sm = re.search(r"[?&]start=(\d+)", idx_url)
                k = fm.group(1) + ":" + (sm.group(1) if sm else "0")
                if k in idx_seen:
                    continue
                idx_seen.add(k)
                if len(idx_seen) > 160 or time.time() - t0 > 220:  # time-box; resumable across runs
                    break
                tseen = set()
                for m in thread_re.finditer(self.snap(ts, idx_url)):
                    tid, title = m.group(1), m.group(2).strip()
                    if tid in tseen or title.lower().startswith(("re:", "»")):
                        continue
                    tseen.add(tid)
                    if title_re.search(title):
                        turl = f"https://{dom}/forums/viewtopic.php?t={tid}"
                        c = self.canon(turl)
                        if c in seen or c in done_canon:
                            continue
                        seen.add(c)
                        queue.append((ts, turl))
                        gold += 1
            self.log(f"  {dom} (phpBB): {gold} title-matched threads from {len(idx_seen)} index pages")

        for sub in r.sources.get("reddit", []):
            kept = 0
            for ts, url in self.cdx(f"old.reddit.com/r/{sub}/comments", limit=8000, listing=True):
                if JUNK.search(url) or "/comments/" not in url:
                    continue
                c = self.canon(url)
                if not c or c in seen or c in done_canon:
                    continue
                seen.add(c)
                queue.append((ts, url))
                kept += 1
            self.log(f"  r/{sub}: {kept} threads")

        self.log(f"work queue: {len(queue)} seed pages")
        return queue

    def run(self, minutes: float):
        r = self.r
        self.log(f"=== archivist grind: {r.topic}  model={r.model}  budget={minutes}min ===")

        done = set(open(self.done_f).read().split("\n")) if os.path.exists(self.done_f) else set()
        done_canon = set(self.canon(u) for u in done if u)
        self.log(f"resume: {len(done)} URLs done")

        seen: set = set()
        queue = self._seed(seen, done_canon)
        frontier = deque((ts, url, None) for ts, url in queue)
        subs_seen = set(s.lower() for s in r.sources.get("reddit", []))
        if os.path.exists(self.subs_f):
            subs_seen |= set(l.strip().lower() for l in open(self.subs_f) if l.strip())
        new_subs_used = fr_links = 0

        pages = items = 0
        deadline = time.time() + minutes * 60
        while frontier:
            if time.time() > deadline:
                self.log("time budget reached — stopping cleanly")
                break
            ts, url, via = frontier.popleft()
            try:  # crash guard: one bad page must never kill an overnight run
                h = self.snap(ts, url)
                text = self.html_to_text(h)
                if len(text) < 200:
                    open(self.done_f, "a").write(url + "\n")
                    done.add(url)
                    continue
                # cheap triage gates only the slow model — we STILL follow the trail of a
                # no-match page (that's the keyword-blindness fix).
                if (self.triage is None or self.triage.search(text)):
                    arr = self.extract(text[:20000])
                    pages += 1
                    if arr:
                        for it in arr:
                            it["source_url"] = url
                            it["source_ts"] = ts
                            it["wayback_url"] = f"http://web.archive.org/web/{ts}id_/{url}"
                            it["model"] = r.model.split("/")[-1]
                            it["extracted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                            if via:
                                it["discovered_via"] = via
                            open(self.items_f, "a").write(json.dumps(it, ensure_ascii=False) + "\n")
                        items += len(arr)
                        self.log(f"#{pages} +{len(arr)} ({items} total) <- {url[:70]}")
                    else:
                        self.log(f"#{pages} none <- {url[:70]}")
                # follow the trail: enqueue linked/mentioned threads, enumerate new subs
                if h and len(frontier) < FRONTIER_MAX:
                    links, subs = self.harvest(h)
                    taken = 0
                    for lk in links:
                        if taken >= OUTLINKS_PER_PAGE:
                            break
                        if JUNK.search(lk):
                            continue
                        c = self.canon(lk)
                        if not c or c in seen or c in done_canon:
                            continue
                        # no per-link availability lookup (it was the bottleneck): enqueue with a
                        # sentinel ts; snap()'s nearest-snapshot redirect resolves it on fetch.
                        seen.add(c)
                        frontier.appendleft((SNAP_SENTINEL, lk, f"link:{url[:50]}"))
                        fr_links += 1
                        taken += 1
                    for s in subs:
                        if s.lower() in subs_seen:
                            continue
                        subs_seen.add(s.lower())
                        open(self.subs_f, "a").write(s + "\n")
                        if new_subs_used < NEW_SUB_MAX:
                            n = self.enum_sub(s, seen, done_canon, frontier)
                            new_subs_used += 1
                            self.log(f"  TRAIL new sub r/{s}: +{n} threads")
                open(self.done_f, "a").write(url + "\n")
                done.add(url)
            except Exception as e:
                self.log(f"  ERR {str(e)[:90]} <- {url[:55]}")
                try:
                    open(self.done_f, "a").write(url + "\n")
                    done.add(url)
                except Exception:
                    pass
        self.log(
            f"=== paused: {pages} pages, {items} items, +{fr_links} trail-links, "
            f"+{new_subs_used} new subs, {len(done)} URLs done ==="
        )
        return {"pages": pages, "items": items, "done": len(done)}
