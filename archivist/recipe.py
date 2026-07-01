"""
archivist.recipe — a Recipe is everything topic-specific, in one YAML file.

It declares WHAT you're cataloguing (the entity + its definition), the SHAPE of each
extracted record (schema), WHERE to look (sources), and the cheap text filters that keep
the slow model focused. The engine is generic; the recipe is the whole personality.

Write a new recipe, point archivist at it — that's the entire "configure for a new topic"
step. No code changes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml


@dataclass
class Recipe:
    topic: str
    entity: str  # singular, e.g. "a mechanical-keyboard enthusiasm"
    definition: str  # the discriminating definition the model reasons against
    schema: list  # [{name, desc}] — fields on each extracted record
    sources: dict  # {blogs: [...], reddit: [...], phpbb: [{host, base}]}
    filters: dict  # {listing, triage}  (regex strings; junk has a built-in default)
    model: str = "mlx-community/Qwen3-30B-A3B-4bit"
    enums: dict = field(default_factory=dict)  # optional {field: [allowed values]}
    stopwords: list = field(default_factory=list)  # meta-terms to drop at compile time
    trail: dict = field(default_factory=dict)  # {relevant_sub, allow_ext, follow_ext}
    wayback_gap: float = 1.0
    out_dir: str = ""
    slug: str = ""

    def __post_init__(self):
        if not self.slug:
            self.slug = re.sub(r"[^a-z0-9]+", "-", self.topic.lower()).strip("-")
        if not self.out_dir:
            self.out_dir = os.path.expanduser(f"~/.archivist/{self.slug}")

    def prompt_head(self) -> str:
        """Build the extraction instruction from the recipe — the only 'prompt engineering'
        a user ever touches is the recipe's definition + schema."""
        fields = []
        for f in self.schema:
            allowed = self.enums.get(f["name"])
            if allowed:
                spec = "|".join(f'"{v}"' for v in allowed)
                fields.append(f'"{f["name"]}":[{spec}]')
            else:
                fields.append(f'"{f["name"]}":"<{f["desc"]}>"')
        item = "{ " + ", ".join(fields) + " }"
        return (
            f"You extract instances of {self.entity} from a web page.\n"
            f"DEFINITION — {self.definition}\n"
            "Work in two steps INTERNALLY: (1) list every candidate mention, (2) keep only "
            "those that clearly match the definition, and merge duplicates into one canonical "
            "name. Then output ONLY the final deduplicated JSON array. Each item: "
            f"{item}. If none, []."
        )


def load_recipe(path: str) -> Recipe:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Recipe(**data)
