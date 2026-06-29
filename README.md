# wayback-corpus-extractor

**Turn the Wayback Machine into a structured, fully-sourced dataset of *any one kind of thing* — read by a local AI, for free, on your own machine.**

Point it at a topic and a schema. It finds the dense archived pages, reads each one with a local LLM, and compiles a ranked, deduped catalog — with a quote and a re-retrievable source link behind **every single entry**.

```
topic + schema  ──►  discover archived pages  ──►  local LLM reads each  ──►  ranked catalog
                     (Wayback CDX)                 (MLX, $0/page, private)    (every fact sourced)
```

No API keys. No per-page cost. Your data never leaves the machine.

---

## The thing this replaces

Some datasets don't exist as a download — they only exist scattered across thousands of pages, in human sentences. "Every tool this community actually swears by." "Every X that people describe doing Y." Building that used to require a person to **read the whole internet** for one type of thing and hand-compile it. Then it required a cloud LLM — fast, but metered, and your corpus lives on someone else's box.

This is the third option: the reading is done by an **open model running locally**. Slow, but free, private, and yours. It's information extraction → knowledge-base construction, for the price of electricity.

---

## A 30-second look at the output

Every item is ranked by **how many independent pages attest it** (the honest signal — not raw mention count), and carries its receipts ([`examples/catalog.sample.json`](examples/catalog.sample.json)):

```json
{
  "name": "Aeropress",
  "real_attestations": 14,
  "total_mentions": 17,
  "sources": [
    {
      "wayback_url": "http://web.archive.org/web/20190114093210id_/https://.../my-everyday-setup",
      "evidence": "I've owned a dozen brewers and the Aeropress is the one I reach for every morning.",
      "ts": "20190114093210"
    }
  ]
}
```

You can click straight back to the archived page each claim came from. Nothing is asserted without a source.

---

## Quickstart

Requires a Mac with Apple Silicon (M-series) and **24 GB+** unified memory for the 30B model.

```bash
git clone https://github.com/pauljump/wayback-corpus-extractor
cd wayback-corpus-extractor
pip install -r requirements.txt

# 30B+ models need a raised GPU memory ceiling on macOS (resets on reboot — see note below)
sudo sysctl iogpu.wired_limit_mb=21504

cp config.example.yaml config.yaml      # then edit config.yaml for your topic
python extract.py config.yaml           # discover + read + extract (resumable; Ctrl-C any time)
python compile.py config.yaml           # dedup + rank into catalog.json
```

The first run downloads the model (~17 GB) once. `extract.py` is **resumable** — it tracks finished URLs, so kill it and rerun and it picks up where it stopped.

---

## How it works

1. **Discover** — uses the [Wayback CDX API](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md) to enumerate archived pages under your target sites, server-side-filtered to the *dense* listing pages (the "what I use" / "best of" pages), not random ones.
2. **Triage (free)** — a cheap text-level keyword filter. Only pages that actually contain your signal reach the model. This is what makes a local grind viable — it skips pure chatter for free.
3. **Extract** — a local MLX model reads each surviving page and returns your schema as JSON, with a supporting quote per item. A two-step internal prompt (list candidates → keep only real matches) does the judgment.
4. **Provenance** — every item is stamped with its source URL, snapshot timestamp, a re-retrievable `id_` Wayback link, the model that read it, and when.
5. **Compile** — dedup (case/plural/punctuation), then rank by **distinct source pages with genuine evidence**. Ten pages independently vouching for a thing is signal; one page listing it as an example is not. Items that are mostly examples get flagged `concept_inflated`.

---

## Point it at your own topic

Edit three blocks in `config.yaml`:

- **`discovery`** — the sites/subreddits to mine, and a `listing_keywords` regex for which archived URLs to keep.
- **`triage_keywords`** — the cheap text pre-filter that decides which pages the model bothers to read.
- **`extraction`** — `entity` (what you're collecting), `definition` (what counts and, crucially, what *doesn't*), and `schema` (the JSON shape of each item).

The shipped [`config.example.yaml`](config.example.yaml) builds a catalog of *gear a hobby community recommends*. Swap those four fields and you're mining something else.

---

## "More powerful" was the wrong target

During development, a benchmark-topping 32B **reasoner** scored *worse* — and ran ~9× slower — than an efficient 30B mixture-of-experts model on the exact same pages. It over-thought a simple judgment call. The 30B MoE (`Qwen3-30B-A3B-4bit`) matched cloud Haiku quality at ~36 tok/s in 24 GB.

**Fit beats power.** The default config uses the model that won, not the one with the best leaderboard score.

---

## Honest limits

- **Apple Silicon only.** MLX is Apple's framework. The good models want **24–32 GB+** unified memory. This narrows the audience — an Ollama/llama.cpp fallback for portability is the most-wanted contribution (see roadmap).
- **Not a magic box.** Quality needs per-topic prompt tuning. A sharp `definition` (especially the *what-doesn't-count* half) is the difference between a corpus and noise.
- **Slug-based discovery.** Great for blogs and Reddit. Slug-less forums (phpBB `viewtopic.php?t=12345`) need an index-crawl pass — not in this release.
- **It's a grinder, not a firehose.** ~20–100 s/page on the dense model. Built to run for hours/overnight, resumably, not to return in real time.

### macOS GPU memory note

A 30B 4-bit model needs ~18–21 GB of GPU-*wired* memory; macOS caps that at ~16.8 GB by default regardless of free RAM, and a reboot or sleep **resets** any raised limit. `extract.py` checks this on startup and refuses cleanly with the fix rather than OOM-crashing mid-run:

```bash
sudo sysctl iogpu.wired_limit_mb=21504
```

---

## Roadmap

- [ ] **Ollama / llama.cpp backend** — lift the Apple-Silicon-only limit (most-wanted).
- [ ] **phpBB / slug-less forum discovery** — index-crawl thread titles → IDs.
- [ ] **Link-following discovery** — reach pages that *demonstrate* a thing without naming it (the keyword-blindness fix).
- [ ] **Web admin** — start/stop/compile/review the loop from a phone.

PRs and issues welcome — especially the Ollama backend.

---

## Credits

Built on the shoulders of the [Internet Archive](https://archive.org/) (please [donate](https://archive.org/donate) — this tool leans entirely on their Wayback Machine) and Apple's [MLX](https://github.com/ml-explore/mlx).

MIT licensed. Built by [@paulljump](https://x.com/paulljump).
