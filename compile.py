#!/usr/bin/env python3
"""
compile.py — turn raw extractions (items.jsonl) into a ranked, deduped catalog (catalog.json).

The honest ranking: by **distinct source pages** that independently attest a thing — not raw
mention count. Ten people on ten pages saying "X" is a real signal; one page listing X as an
*example* is not. This is the line that separates a corpus from noise.

    python compile.py <config.yaml>
"""
import json, re, os, sys, collections
import yaml

if len(sys.argv) < 2:
    print("usage: python compile.py <config.yaml>"); sys.exit(1)
CFG = yaml.safe_load(open(sys.argv[1]))
OUT = os.path.expanduser(os.environ.get("OUT", CFG.get("output_dir", "~/wce-out")))
ITEMS   = os.path.join(OUT, "items.jsonl")
CATALOG = os.path.join(OUT, "catalog.json")

# optional: words that are never a real entity (the schema's meta-terms, the topic itself, ...)
BLOCK = set(w.lower() for w in CFG.get("compile", {}).get("blocklist", []))

# evidence that is NOT a genuine attestation: a list of examples, page chrome, or a model non-answer
CONCEPT_RE = re.compile(r"for example|such as|e\.g\.|examples? (of|include|are)|things? like|a common", re.I)
CHROME_RE  = re.compile(r"jump to content|my subreddits|edit subscriptions|log ?in|sign ?up|submit a|view entire", re.I)
NONANS_RE  = re.compile(r"not applicable|not (mentioned|provided|specified|stated|present|found)|^n/?a\b|no (specific|genuine|clear|explicit)", re.I)

def norm(name):
    n = re.sub(r"[^\w\s]", "", name.lower()).strip()
    return re.sub(r"\s+", " ", n)

def valid_evidence(ev):
    ev = (ev or "").strip()
    if len(ev) < 14: return False
    if CONCEPT_RE.search(ev) or CHROME_RE.search(ev) or NONANS_RE.search(ev): return False
    return True

def main():
    cat, n_raw = {}, 0
    for line in open(ITEMS, encoding="utf-8"):
        try: x = json.loads(line)
        except Exception: continue
        name = (x.get("name") or "").strip()
        if not name: continue
        n_raw += 1
        key = norm(name)
        if not key or len(key) < 2 or key in BLOCK: continue
        e = cat.setdefault(key, {"names": collections.Counter(), "sources": [],
                                 "real_pages": set(), "all_pages": set()})
        e["names"][name] += 1
        url = x.get("source_url")
        e["all_pages"].add(url)
        ev = x.get("evidence_excerpt") or ""
        if valid_evidence(ev):
            e["real_pages"].add(url)
            e["sources"].append({"url": url, "ts": x.get("source_ts"),
                                 "wayback_url": x.get("wayback_url"), "evidence": ev[:240],
                                 "model": x.get("model")})

    # plural merge: "widgets" -> "widget" when both exist
    for k in list(cat.keys()):
        if k.endswith("s") and k[:-1] in cat and k in cat:
            a, b = cat[k[:-1]], cat[k]
            a["names"].update(b["names"]); a["sources"] += b["sources"]
            a["real_pages"] |= b["real_pages"]; a["all_pages"] |= b["all_pages"]
            del cat[k]

    out = []
    for key, e in cat.items():
        real, total = len(e["real_pages"]), len(e["all_pages"])
        out.append({
            "name": e["names"].most_common(1)[0][0],
            "real_attestations": real,   # distinct pages where someone genuinely attests it
            "total_mentions": total,     # all pages that named it (incl. example-lists)
            "concept_inflated": total >= 5 and real / total < 0.34,  # mostly examples/chrome
            "sources": e["sources"][:25],
        })
    out.sort(key=lambda r: (-r["real_attestations"], -r["total_mentions"]))
    json.dump(out, open(CATALOG, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    flagged = sum(1 for r in out if r["concept_inflated"])
    print(f"compiled {n_raw} raw -> {len(out)} unique  |  {flagged} flagged concept-inflated")
    print(f"wrote {CATALOG}")
    print("=== top 20 by real attestations (distinct pages that genuinely attest it) ===")
    for r in out[:20]:
        flag = " ⚠inflated" if r["concept_inflated"] else ""
        print(f"  real:{r['real_attestations']:3} (of {r['total_mentions']:3})  {r['name'][:40]:40}{flag}")

if __name__ == "__main__":
    main()
