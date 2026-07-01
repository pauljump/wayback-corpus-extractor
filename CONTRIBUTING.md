# Contributing

Thanks for looking. archivist is small on purpose — a generic engine plus per-topic recipes.

## Good first contributions

- **A recipe.** The highest-leverage contribution: a `recipes/<topic>.yaml` for a domain with
  rich archived communities (hobbies, tools, music gear, regional knowledge…). Include a short
  note on the sources and a sample of the output.
- **A backend.** `archivist/extract.py` defines the contract: an extractor is any callable
  `text -> list[dict] | None`. A local-Ollama backend or a "bring your own model" backend would
  open archivist up beyond Apple Silicon.
- **Engine robustness.** Better discovery (sitemaps, more forum engines), smarter dedup, faster
  triage.

## Layout

```
archivist/
  engine.py    # generic Wayback discovery + trail-following + resumable loop
  recipe.py    # the Recipe dataclass + YAML loader + prompt builder
  extract.py   # extraction backends (MLX today)
  compile.py   # dedup + honest ranking
  cli.py       # grind / compile / show
recipes/       # one YAML per topic
```

## Principles

- **Provenance is non-negotiable.** Every extracted row keeps its source, timestamp, quote, and
  a re-retrievable snapshot link. Don't add features that produce unfalsifiable data.
- **The engine stays topic-agnostic.** If something is specific to one subject, it belongs in a
  recipe, not in `engine.py`.
- **Be a good guest of the Archive.** All archive.org traffic goes through the backoff gateway.
  Don't bypass it.

## Dev setup

```bash
git clone https://github.com/pauljump/wayback-corpus-extractor && cd wayback-corpus-extractor
pip install -e .
pip install mlx-lm    # Apple Silicon, for the local backend
```

Open an issue before a large change so we can talk shape. PRs welcome.
