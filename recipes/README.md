# Recipes

A recipe is one YAML file that retargets archivist at a new subject. The engine never changes —
the recipe is the whole personality. Copy [`mechanical-keyboards.yaml`](mechanical-keyboards.yaml)
and edit.

## Fields

| Field | Required | What it does |
|------|----------|--------------|
| `topic` | ✓ | Human name. Also derives the slug + output dir (`~/.archivist/<slug>/`). |
| `entity` | ✓ | One line: the *thing* you're cataloguing, singular. |
| `definition` | ✓ | The discriminating definition the model reasons against. Spend your effort here — say clearly what counts AND what doesn't. |
| `schema` | ✓ | List of `{name, desc}` — the fields on each extracted record. The **first** field is the catalog key. A field whose name contains `evidence`/`excerpt`/`quote` is used for attestation scoring. |
| `sources` | ✓ | Where to look: `reddit` (subreddit names), `blogs` (bare domains), `phpbb` (`{host, base}` for forums whose threads are `viewtopic.php?t=<id>`). |
| `filters.listing` | ✓ | Server-side CDX regex — only enumerate archived URLs whose path hints at a dense "here's what I'm into" page. Also filters forum threads by title. |
| `filters.triage` | ✓ | Cheap text regex — only pages whose text matches get the (slow) model. This is your yield multiplier. |
| `model` | | MLX model id. Default `mlx-community/Qwen3-30B-A3B-4bit`. |
| `enums` | | `{field: [allowed values]}` — constrains a schema field to a fixed set. |
| `stopwords` | | Meta-terms to drop at compile (the topic itself, e.g. "keyboard"). |
| `trail.relevant_sub` | | Regex gating which mentioned subreddits the trail-follower is allowed to chase. Keeps the crawl from drifting. |
| `trail.follow_ext` | | Follow external blog links found on pages (default false). |
| `wayback_gap` | | Seconds between archive.org calls. Default 1.0 — raise it if you get throttled. |

## Tips

- **The definition is the product.** A vague definition gives a noisy catalog. Name the
  near-misses explicitly ("NOT: a generic word, a one-time question, page chrome").
- **Pick sources with dense, opinionated communities.** Forums and subreddits where people
  enumerate what they're into are gold; news sites are not.
- **Tune `triage` first.** Too loose and the model wastes time on chatter; too tight and you miss
  pages. Watch the grind log's hit rate and adjust.
- **Start with `--minutes 20`** to sanity-check a new recipe before an overnight run.
