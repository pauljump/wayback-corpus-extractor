"""
archivist.compile — turn the raw append-only items.jsonl into a clean, ranked catalog.

Rule-based (no model), so it's safe to run while a grind is still going. The one idea
worth understanding: we rank by REAL ATTESTATIONS — distinct pages where someone genuinely
*describes having / doing* the thing — not raw mentions. That demotes "concept inflation":
a term that's everywhere only because it keeps appearing in "common examples include …"
lists or in page chrome. Honest ranking over loud ranking.
"""
from __future__ import annotations

import collections
import json
import os
import re

from .recipe import Recipe

# Evidence that is NOT a person describing genuinely having/doing the thing:
CONCEPT_RE = re.compile(
    r"for example|such as|e\.g\.|examples? (of|include|are)|things? like|common (examples?|interests?)",
    re.I,
)
CHROME_RE = re.compile(
    r"jump to content|my subreddits|edit subscriptions|popular\s*-\s*all|submit a|log ?in|sign ?up|view entire",
    re.I,
)
NONANS_RE = re.compile(
    r"not applicable|not (mentioned|provided|specified|stated|present|found)|^n/?a\b|no (specific|genuine|clear|explicit)",
    re.I,
)


def _norm(name):
    n = re.sub(r"[^\w\s]", "", name.lower()).strip()
    return re.sub(r"\s+", " ", n)


def _valid_evidence(ev):
    ev = (ev or "").strip()
    if len(ev) < 14:
        return False
    return not (CONCEPT_RE.search(ev) or CHROME_RE.search(ev) or NONANS_RE.search(ev))


def _name_field(recipe: Recipe):
    return recipe.schema[0]["name"] if recipe.schema else "name"


def _evidence_field(recipe: Recipe):
    for f in recipe.schema:
        if re.search(r"evidence|excerpt|quote", f["name"], re.I):
            return f["name"]
    return None


def _label_field(recipe: Recipe):
    # the first enum'd field makes a nice secondary label (category / driver / type)
    for f in recipe.schema:
        if f["name"] in recipe.enums:
            return f["name"]
    return None


def compile_catalog(recipe: Recipe, log=print):
    items_f = os.path.join(recipe.out_dir, "items.jsonl")
    catalog_f = os.path.join(recipe.out_dir, "catalog.json")
    if not os.path.exists(items_f):
        log(f"no items yet at {items_f} — run a grind first")
        return

    name_field = _name_field(recipe)
    ev_field = _evidence_field(recipe)
    label_field = _label_field(recipe)
    block = set(w.lower() for w in recipe.stopwords)

    cat: dict = {}
    n_raw = 0
    for line in open(items_f, encoding="utf-8"):
        try:
            x = json.loads(line)
        except Exception:
            continue
        name = (x.get(name_field) or "").strip()
        if not name:
            continue
        n_raw += 1
        if name.lower() in block:
            continue
        key = _norm(name)
        if not key or len(key) < 2 or key in block:
            continue
        e = cat.setdefault(
            key,
            {
                "names": collections.Counter(),
                "labels": collections.Counter(),
                "sources": [],
                "real_pages": set(),
                "all_pages": set(),
            },
        )
        e["names"][name] += 1
        if label_field and x.get(label_field):
            e["labels"][x[label_field]] += 1
        url = x.get("source_url")
        e["all_pages"].add(url)
        ev = x.get(ev_field) if ev_field else None
        if ev_field is None or _valid_evidence(ev):
            e["real_pages"].add(url)
            e["sources"].append(
                {
                    "url": url,
                    "ts": x.get("source_ts"),
                    "wayback_url": x.get("wayback_url"),
                    "evidence": (ev or "")[:240],
                    "model": x.get("model"),
                }
            )

    # plural merge: "legos" -> "lego" when both exist
    for k in list(cat.keys()):
        if k.endswith("s") and k[:-1] in cat and k in cat:
            a, b = cat[k[:-1]], cat[k]
            a["names"].update(b["names"])
            a["labels"].update(b["labels"])
            a["sources"] += b["sources"]
            a["real_pages"] |= b["real_pages"]
            a["all_pages"] |= b["all_pages"]
            del cat[k]

    out = []
    for e in cat.values():
        real, total = len(e["real_pages"]), len(e["all_pages"])
        out.append(
            {
                "name": e["names"].most_common(1)[0][0],
                "label": (e["labels"].most_common(1)[0][0] if e["labels"] else ""),
                "real_attestations": real,
                "total_mentions": total,
                "concept_inflated": total >= 5 and real / total < 0.34,
                "sources": e["sources"][:25],
            }
        )
    out.sort(key=lambda r: (-r["real_attestations"], -r["total_mentions"]))
    json.dump(out, open(catalog_f, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    flagged = sum(1 for r in out if r["concept_inflated"])
    log(f"compiled {n_raw} raw -> {len(out)} unique  |  {flagged} flagged concept-inflated")
    log(f"catalog: {catalog_f}")
    log("=== top 20 by real attestations (someone actually describes having/doing it) ===")
    for r in out[:20]:
        flag = " ⚠inflated" if r["concept_inflated"] else ""
        lbl = f" [{r['label']}]" if r["label"] else ""
        log(f"  real:{r['real_attestations']:3} (of {r['total_mentions']:3})  {r['name'][:34]:34}{lbl}{flag}")
    return out
