#!/usr/bin/env python3
"""
wayback-corpus-extractor — turn the Wayback Machine into a structured, fully-provenanced
corpus of *any one kind of thing*, using a local LLM (MLX on Apple Silicon). $0/page, private.

Point it at a topic (config.yaml): it discovers the dense archived pages, reads each one with a
local model, extracts the entities you defined, and appends them durably — with a re-retrievable
source URL + supporting quote for every single item. Resumable: kill it, rerun it, it continues.

    python extract.py config.yaml

Then compile the raw extractions into a ranked, deduped catalog:

    python compile.py config.yaml
"""
import os, sys, json, time, re, subprocess, urllib.request, urllib.parse
import yaml
from mlx_lm import load, generate

# ---------------------------------------------------------------- config
if len(sys.argv) < 2:
    print("usage: python extract.py <config.yaml>"); sys.exit(1)
CFG = yaml.safe_load(open(sys.argv[1]))

MODEL   = os.environ.get("MLX_MODEL", CFG.get("model", "mlx-community/Qwen3-30B-A3B-4bit"))
THINK   = os.environ.get("THINK", "1" if CFG.get("think", True) else "0") == "1"
MINUTES = float(os.environ.get("MINUTES", CFG.get("minutes", 60)))
OUT     = os.path.expanduser(os.environ.get("OUT", CFG.get("output_dir", "~/wce-out")))
os.makedirs(OUT, exist_ok=True)
DONE_F  = os.path.join(OUT, "done_urls.txt")
ITEMS_F = os.path.join(OUT, "items.jsonl")
LOG_F   = os.path.join(OUT, "extract.log")

DISC    = CFG.get("discovery", {})
DOMAINS = DISC.get("domains", [])
SUBS    = DISC.get("reddit_subs", [])
# server-side URL filter: only pull archived pages whose URL hints at a dense "listing" page
LISTING = DISC.get("listing_keywords", "")
# cheap text pre-filter so the slow model only reads pages that actually contain the signal
TRIAGE  = re.compile(CFG.get("triage_keywords", ".") , re.I)
# never fetch obvious non-content URLs (assets, logins, pagination, feeds, ...)
JUNK    = re.compile(r"\.(jpg|jpeg|png|gif|css|js|ico|pdf|zip)|/download/|file\.php|avatar|posting\.php|"
                     r"memberlist|/wp-(login|admin|json)|/feed|/login|/register|/tags?/|/page/\d|"
                     r"replytocom|[?&]m=\d|[?&]share=|#comment|/categories?/|/forums/$", re.I)

# ---------------------------------------------------------------- extraction prompt (from config)
EX   = CFG["extraction"]
ENT  = EX["entity"]
DEFN = EX["definition"].strip()
# config supplies one example JSON item; we ask for an array of that exact shape.
SCHEMA = json.dumps(EX["schema"], ensure_ascii=False)
PROMPT_HEAD = (
    f"You extract instances of: {ENT}.\n"
    f"DEFINITION — {DEFN}\n"
    "Work in two steps INTERNALLY: (1) list every candidate mention on the page, "
    "(2) keep only those that clearly match the definition, and merge duplicates into one canonical name. "
    f"Then output ONLY the final deduplicated JSON array. Each item has exactly this shape:\n{SCHEMA}\n"
    "Every item MUST include an \"evidence_excerpt\": one short, de-identified sentence copied from the page "
    "that proves it. If the page contains none, output []."
)

def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    open(LOG_F, "a").write(line + "\n")

# ---------------------------------------------------------------- archive.org client (throttled)
# Every archive.org call goes through here: a polite inter-request gap + exponential backoff on
# refusal/429. Wayback throttles a hot IP ("Connection refused" errno 61) — one wrapper makes the
# whole run back off instead of silently losing the trail.
_last_wb = [0.0]
WB_GAP = float(os.environ.get("WB_GAP", CFG.get("wayback_gap_seconds", 1.0)))
def wb_open(u, timeout=30, tries=4):
    for i in range(tries):
        gap = time.time() - _last_wb[0]
        if gap < WB_GAP: time.sleep(WB_GAP - gap)
        try:
            with urllib.request.urlopen(u, timeout=timeout) as r:
                data = r.read()
            _last_wb[0] = time.time()
            return data
        except Exception:
            _last_wb[0] = time.time()
            if i == tries - 1: raise
            time.sleep(2.0 * (i + 1))   # 2s, 4s, 6s
    return None

def cdx(prefix, limit=5000, listing=False):
    """Wayback CDX: enumerate distinct archived URLs under a prefix. With listing=True, apply a
    server-side regex filter so we only get the dense 'listing/recommendation' pages."""
    u = (f"http://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(prefix)}"
         f"&matchType=prefix&output=json&collapse=urlkey&fl=timestamp,original&limit={limit}")
    if listing and LISTING:
        u += "&filter=original:(?i).*(" + LISTING + ").*"
    try:
        rows = json.loads(wb_open(u, timeout=120))
        return [(ts, url) for ts, url in rows[1:]]
    except Exception as e:
        log(f"  cdx fail {prefix}: {e}"); return []

def canon(url):
    """Collapse variants of the same page to one canonical key — so 'done' dedup is reliable.
    Reddit/forum thread identity is the thread id (in the query), not the path — preserve it."""
    rm = re.search(r"reddit\.com/r/(\w+)/comments/(\w+)", url, re.I)
    if rm:
        return f"reddit.com/r/{rm.group(1)}/comments/{rm.group(2)}".lower()
    tm = re.search(r"viewtopic\.php.*?\bt=(\d+)", url, re.I)
    if tm:
        host = re.sub(r"^https?://", "", url).split("/")[0].replace(":80", "")
        return f"{host}/viewtopic.php?t={tm.group(1)}".lower()
    url = re.sub(r"[?#].*$", "", url)
    url = re.sub(r":80(/|$)", r"\1", url)
    url = re.sub(r"/(amp|feed|comment-page-\d+)/?$", "/", url, flags=re.I)
    return url.rstrip("/").lower()

def snap(ts, url):
    """Fetch the archived HTML. Exact 14-digit ts -> raw 'id_' capture first (fast, exact), then
    nearest-snapshot fallback. A partial/sentinel ts goes straight to the nearest-snapshot form."""
    mods = ("id_", "") if (ts and len(ts) >= 12 and ts.isdigit()) else ("",)
    for mod in mods:
        try:
            h = (wb_open(f"http://web.archive.org/web/{ts}{mod}/{url}", timeout=30) or b"").decode("utf-8", "ignore")
            if len(h) > 500:
                return h
        except Exception:
            pass
    return ""

def html_to_text(h):
    h = re.sub(r"<script[\s\S]*?</script>", " ", h, flags=re.I)
    h = re.sub(r"<style[\s\S]*?</style>", " ", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = re.sub(r"&[a-z]+;", " ", h, flags=re.I)
    return re.sub(r"\s+", " ", h).strip()

def parse_arr(s):
    if not s: return None
    s = re.sub(r"<think>.*?</think>", " ", s, flags=re.S|re.I).replace("```json", " ").replace("```", " ")
    a, b = s.find("["), s.rfind("]")
    if a < 0 or b < 0 or b <= a: return None
    try:
        j = json.loads(s[a:b+1]); return j if isinstance(j, list) else None
    except Exception:
        return None

# ---------------------------------------------------------------- GPU wired-memory guard
# A 30B/32B-class 4-bit model needs ~18-21 GB of GPU-wired memory; macOS caps that at a default
# ~16.8 GB regardless of free RAM, and a reboot/sleep RESETS any raised limit. Without this guard
# the model OOMs mid-inference — an uncatchable Metal abort that kills the whole process. Check
# first, refuse cleanly with the fix.
NEED_WIRED_MB = int(os.environ.get("NEED_WIRED_MB", CFG.get("need_wired_mb", 20000)))
def gpu_wired_mb():
    try:
        return int(subprocess.run(["sysctl", "-n", "iogpu.wired_limit_mb"],
                                  capture_output=True, text=True, timeout=5).stdout.strip())
    except Exception:
        return -1

log(f"=== EXTRACT start  model={MODEL}  think={THINK}  budget={MINUTES}min  out={OUT} ===")
_w = gpu_wired_mb()
if _w == 0 or (0 < _w < NEED_WIRED_MB):
    eff = "default (~16.8GB)" if _w == 0 else f"{_w}MB"
    log(f"!! ABORT: GPU wired limit is {eff} — too low for {MODEL.split('/')[-1]} (needs ~{NEED_WIRED_MB}MB).")
    log(f"!! A reboot or sleep resets it. Raise it, then relaunch:")
    log(f"!!   sudo sysctl iogpu.wired_limit_mb=21504")
    sys.exit(2)
log(f"GPU wired limit OK: {_w}MB (need {NEED_WIRED_MB})")

t0 = time.time(); model, tok = load(MODEL); log(f"model loaded {time.time()-t0:.0f}s")

done = set(open(DONE_F).read().split("\n")) if os.path.exists(DONE_F) else set()
done_canon = set(canon(u) for u in done if u)
log(f"resume: {len(done)} URLs done")

# ---------------------------------------------------------------- discovery (build the work queue)
queue, seen = [], set()
for dom in DOMAINS:
    kept = 0
    for ts, url in cdx(f"{dom}/", limit=5000, listing=True):
        if JUNK.search(url): continue
        c = canon(url)
        if not c or c in seen or c in done_canon: continue
        seen.add(c); queue.append((ts, url)); kept += 1
    log(f"  {dom}: {kept} listing pages")

for sub in SUBS:
    kept = 0
    for ts, url in cdx(f"old.reddit.com/r/{sub}/comments", limit=8000, listing=True):
        if JUNK.search(url) or "/comments/" not in url: continue
        c = canon(url)
        if not c or c in seen or c in done_canon: continue
        seen.add(c); queue.append((ts, url)); kept += 1
    log(f"  r/{sub}: {kept} listing threads")

log(f"work queue: {len(queue)} candidate pages")

# ---------------------------------------------------------------- the grind
pages = items = 0
deadline = time.time() + MINUTES * 60
for ts, url in queue:
    if time.time() > deadline:
        log("time budget reached — stopping cleanly"); break
    try:  # CRASH GUARD: one bad page (fetch/model/parse) must never kill the run
        text = html_to_text(snap(ts, url))
        if len(text) < 200:
            open(DONE_F, "a").write(url + "\n"); done.add(url); continue
        if not TRIAGE.search(text):
            open(DONE_F, "a").write(url + "\n"); done.add(url); continue
        prompt_text = PROMPT_HEAD + "\n\nPAGE TEXT:\n" + text[:20000]
        msgs = [{"role": "user", "content": prompt_text}]
        try: prompt = tok.apply_chat_template(msgs, add_generation_prompt=True, enable_thinking=THINK)
        except TypeError: prompt = tok.apply_chat_template(msgs, add_generation_prompt=True)
        t = time.time(); resp = generate(model, tok, prompt=prompt, max_tokens=4000, verbose=False); dt = time.time()-t
        arr = parse_arr(resp)
        pages += 1
        if arr:
            for it in arr:
                it["source_url"]   = url
                it["source_ts"]    = ts
                it["wayback_url"]   = f"http://web.archive.org/web/{ts}id_/{url}"  # re-retrievable receipt
                it["model"]         = MODEL.split("/")[-1]
                it["extracted_at"]  = time.strftime("%Y-%m-%dT%H:%M:%S")
                open(ITEMS_F, "a").write(json.dumps(it, ensure_ascii=False) + "\n")
            items += len(arr)
            log(f"#{pages} {dt:.0f}s +{len(arr)} ({items} total) <- {url[:70]}")
        else:
            log(f"#{pages} {dt:.0f}s NOJSON <- {url[:70]}")
        open(DONE_F, "a").write(url + "\n"); done.add(url)
    except Exception as e:
        log(f"  ERR {str(e)[:90]} <- {url[:55]}")
        try: open(DONE_F, "a").write(url + "\n"); done.add(url)
        except Exception: pass

log(f"=== EXTRACT paused: {pages} pages read, {items} items, {len(done)} URLs done ===")
