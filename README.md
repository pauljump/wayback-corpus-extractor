# wayback-corpus-extractor

**Turn the Wayback Machine into a structured, fully-provenanced dataset — with a local LLM.**

Point it at a topic and a recipe. It discovers the dense archived pages, reads each one with a
model running *on your own machine*, keeps only what matches your definition, tracks every
claim back to a source + quote + a re-retrievable snapshot, and compiles a clean, honestly-ranked
catalog. One YAML file retargets it at a new subject. The engine never changes.

- **Local.** A model on your laptop (MLX / Apple Silicon). Your data never leaves the machine.
- **$0 per page.** No API bill. Run it overnight, run it for a week — it costs electricity.
- **Provenance on every row.** Source URL + archived timestamp + the exact quote + a link that
  re-fetches the original capture. Nothing is unfalsifiable.
- **Resumable.** Append-only log + a done-set. Reboot, sleep, crash — it picks up where it left off.
- **One file to retarget.** A topic is a YAML *recipe*. The engine never changes.

> The premise: a huge amount of real, hard-won human knowledge is sitting in old forum
> threads and dead blogs, captured by the Internet Archive and readable by no one. A local
> model is cheap enough to actually read all of it.

---

## The thing this replaces

Some datasets don't exist as a download — they only exist scattered across thousands of pages, in
human sentences. "Every tool this community actually swears by." "Every X that people describe
doing Y." Building that used to require a person to **read the whole internet** for one type of
thing and hand-compile it. Then it required a cloud LLM — fast, but metered, and your corpus
lives on someone else's box.

This is the third option: the reading is done by an **open model running locally**. Slow, but
free, private, and yours.

---

## Quickstart

Requires a Mac with Apple Silicon (M-series) and **24 GB+** unified memory for the 30B model.

```bash
pip install archivist-wayback          # or: git clone … && pip install -e .
pip install mlx-lm                     # the local model backend (Apple Silicon)

# 30B+ models need a raised GPU memory ceiling on macOS (resets on reboot — see note below)
sudo sysctl iogpu.wired_limit_mb=21504

# build a catalog of what the mechanical-keyboard community is actually into:
archivist grind   recipes/mechanical-keyboards.yaml --minutes 60
archivist compile recipes/mechanical-keyboards.yaml
archivist show    recipes/mechanical-keyboards.yaml
```

Output (`~/.archivist/mechanical-keyboards/catalog.json`), ranked by **real attestations** —
distinct pages where someone genuinely describes building/owning/loving the thing, not just
pages where the word appears:

```
  real: 41 (of  58)  Gateron Ink Black            [switch]
  real: 33 (of  39)  GMK Olivia                   [keycaps]
  real: 27 (of  31)  Tofu65                       [board]
  real: 19 (of  22)  Holee Mod                    [mod]
  real:  6 (of  44)  Cherry MX                    [switch] ⚠inflated
```

That last line is the point: "Cherry MX" is *named* everywhere, but mostly as a generic
reference — so it's flagged, not crowned. Honest ranking over loud ranking.

> **Note on the sample above:** the output shape is illustrative; the numbers above match the
> recipe's community, not a fixed fixture. A real grind output will be committed to
> `examples/` after the first overnight run on the mechanical-keyboards recipe.

---

## How it works

```
recipe.yaml ──► discover ──► fetch snapshot ──► extract (local LLM) ──► provenance ──► compile
               (CDX +         (Wayback,           (your definition       (source +      (dedup +
                trail          polite + backoff)    + schema)              quote +        honest
                follow)                                                    re-fetch)      rank)
```

1. **Discover.** Enumerate archived URLs via the Internet Archive's CDX API, server-side-filtered
   to the pages most likely to be dense ("endgame", "collection", "my build"…). Reddit, blogs,
   and phpBB-style forums are first-class sources.
2. **Follow the trail.** Every page's outbound links and subreddit mentions are harvested back
   into the frontier — so you reach pages that *demonstrate* an interest without ever naming it
   (the keyword-blindness fix). Discovery snowballs from the seed set.
3. **Extract.** A local model reads the page text and returns only records matching your recipe's
   definition + schema. Two-step reasoning (list candidates → keep only clear matches), then
   strict JSON output.
4. **Provenance.** Each record is stamped with its source URL, the snapshot timestamp, a quoted
   excerpt, and a `web.archive.org/web/<ts>id_/<url>` link that re-fetches the exact capture.
5. **Compile.** Dedup, merge variants, and rank by genuine attestation — with concept-inflation
   flagged.

It's built to survive the real world: a polite gateway with exponential backoff (the Archive
throttles a hot IP and will silently break your crawl otherwise), a sentinel-snapshot fetch for
discovered links, a per-page crash guard, and a GPU-wired-memory pre-check so a 30B model fails
*clearly* instead of OOM-aborting six hours into an overnight run.

---

## Writing a recipe

A recipe is the entire configuration for a new topic. See
[`recipes/mechanical-keyboards.yaml`](recipes/mechanical-keyboards.yaml) and
[`recipes/README.md`](recipes/README.md) for the full field reference. The minimal shape:

```yaml
topic: Mechanical Keyboards
slug: mechanical-keyboards

entity: >
  a mechanical-keyboard enthusiasm — a specific board, switch, keycap set, or mod someone is
  genuinely into

definition: >
  An ENTHUSIASM is a specific, named thing the person describes building, owning, daily-driving,
  lusting after, or going deep on. NOT a generic word, NOT a one-time question, NOT page chrome.

schema:
  - { name: name,             desc: the specific thing, title case }
  - { name: category,         desc: which kind of thing }
  - { name: evidence_excerpt, desc: one sentence showing genuine interest }

enums:
  category: [board, switch, keycaps, layout, mod, other]

sources:
  reddit: [MechanicalKeyboards, olkb, ErgoMechKeyboards]
  # phpbb:                         # phpBB forums: crawls the index pages, filters by thread title
  #   - host: some-keyboard-forum.net
  #     base: some-keyboard-forum.net/forums/

filters:
  listing: "build|endgame|grail|collection|favou?rite"
  triage:  "switch|keycap|build log|i love|really into|obsessed"

trail:
  relevant_sub: "keyboard|keeb|mech|switch|keycap|ergo|olkb|qmk|via"
  follow_ext: false
```

The `schema` first field is the catalog key. A field whose name contains `evidence`/`excerpt`/`quote`
drives attestation scoring. The `enums` block adds a secondary label column to the catalog.

---

## Source types

**Reddit** — thread URLs are slug-based; the CDX listing filter is applied directly.

**Blogs** — bare domain (e.g. `some-keyboard-blog.com`); CDX-enumerated listing pages only.

**phpBB forums** — threads are `viewtopic.php?t=<id>`, no meaningful slug. The engine
index-crawls `viewforum.php` pages, parses thread titles, and keeps only threads whose title
matches the listing filter. Specify as:

```yaml
sources:
  phpbb:
    - host: some-forum.net
      base: some-forum.net/forums/
```

---

## "More powerful" was the wrong target

During development, a benchmark-topping 32B **reasoner** scored *worse* — and ran ~9x slower —
than an efficient 30B mixture-of-experts model on the exact same pages. It over-thought a simple
judgment call. The 30B MoE (`Qwen3-30B-A3B-4bit`) matched cloud Haiku quality at ~36 tok/s in
24 GB.

**Fit beats power.** The default recipe uses the model that won, not the one with the best
leaderboard score.

---

## Honest limits

- **Apple Silicon only** for the bundled backend (MLX). The engine is backend-agnostic; other
  backends are welcome (see Contributing and Roadmap).
- **Not a magic box.** Quality needs per-topic tuning. A sharp `definition` — especially the
  *what-doesn't-count* half — is the difference between a corpus and noise.
- **Discovery is filter-seeded, then trail-driven.** It finds the dense veins well; it is not a
  complete crawl of the web.
- **Slow.** Seconds to a couple of minutes per page. It's a marathon, not a sprint. That's why
  it's resumable and runs overnight.
- **Respect sources.** archivist reads the *Internet Archive's* captures, politely and with
  backoff. Check the terms and consent norms of any community before you publish a dataset built
  from it — just because something was archived doesn't mean it's yours to redistribute.

### macOS GPU memory note

A 30B 4-bit model needs ~18-21 GB of GPU-*wired* memory; macOS caps that at ~16.8 GB by default
regardless of free RAM, and a reboot or sleep **resets** any raised limit. The CLI checks this on
startup and refuses cleanly with the fix rather than OOM-crashing mid-run:

```bash
sudo sysctl iogpu.wired_limit_mb=21504
```

---

## Illustrative sample output

The JSON below shows the catalog shape — field names, provenance structure, and concept-inflation
flag. **It is illustrative only**: the source URLs and Reddit thread IDs are fabricated
placeholders; a real grind output will replace this once the first overnight mechanical-keyboards
run completes.

```json
[
  {
    "name": "Gateron Ink Black",
    "label": "switch",
    "real_attestations": 41,
    "total_mentions": 58,
    "concept_inflated": false,
    "sources": [
      {
        "url": "https://old.reddit.com/r/MechanicalKeyboards/comments/<thread-id>/",
        "ts": "20211004142200",
        "wayback_url": "http://web.archive.org/web/20211004142200id_/https://old.reddit.com/r/MechanicalKeyboards/comments/<thread-id>/",
        "evidence": "Been daily driving Ink Blacks for a year now, lubed with 205g0 — nothing else feels as smooth.",
        "model": "Qwen3-30B-A3B-4bit"
      }
    ]
  },
  {
    "name": "Cherry MX",
    "label": "switch",
    "real_attestations": 6,
    "total_mentions": 44,
    "concept_inflated": true,
    "sources": [...]
  }
]
```

Full illustrative sample: [`examples/catalog.sample.json`](examples/catalog.sample.json)

---

## Roadmap

- [ ] **More backends** — local Ollama server; "bring your own model"; open archivist beyond
      Apple Silicon (most-wanted contribution — see CONTRIBUTING.md).
- [ ] **Recipe library** — a community collection of recipes for different domains.
- [ ] **Swarm mode** *(the big one).* Corpus-building is embarrassingly parallel: a topic's URL
      space splits cleanly into shards, each owned by exactly one worker. The plan is a coordinator
      that lets many people point their idle compute at a *shared, public* dataset — claim a shard,
      extract it, return provenanced rows, and the work re-homes the instant a contributor drops.
      Folding@home, but it builds open knowledge. Watch the repo if you want this.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The highest-leverage contribution is a recipe for a new
domain. A new extraction backend (Ollama, llama.cpp) is the most-wanted engineering contribution.

---

## Credits

Built on the shoulders of the [Internet Archive](https://archive.org/) — please
[donate to them](https://archive.org/donate); none of this works without their Wayback Machine.
Also built on Apple's [MLX](https://github.com/ml-explore/mlx).

MIT licensed. Built by [@paulljump](https://x.com/paulljump).
