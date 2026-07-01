"""
archivist — turn the Wayback Machine into a structured, provenanced corpus with a local LLM.

  archivist grind   <recipe.yaml> [--minutes N] [--no-think]
  archivist compile <recipe.yaml>
  archivist show    <recipe.yaml> [--n 30]
"""
from __future__ import annotations

import argparse
import json
import os
import time


def _log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    return line


def cmd_grind(args):
    from .compile import compile_catalog
    from .engine import Engine
    from .extract import MLXExtractor, check_gpu_wired
    from .recipe import load_recipe

    recipe = load_recipe(args.recipe)
    os.makedirs(recipe.out_dir, exist_ok=True)
    logf = os.path.join(recipe.out_dir, "grind.log")

    def log(m):
        line = _log(m)
        try:
            open(logf, "a").write(line + "\n")
        except Exception:
            pass

    check_gpu_wired(need_mb=args.need_wired_mb, log=log)
    extractor = MLXExtractor(recipe, think=not args.no_think, log=log)
    Engine(recipe, extractor, log=log).run(minutes=args.minutes)
    if args.compile:
        compile_catalog(recipe, log=log)


def cmd_compile(args):
    from .compile import compile_catalog
    from .recipe import load_recipe

    compile_catalog(load_recipe(args.recipe), log=_log)


def cmd_show(args):
    from .recipe import load_recipe

    recipe = load_recipe(args.recipe)
    path = os.path.join(recipe.out_dir, "catalog.json")
    if not os.path.exists(path):
        _log(f"no catalog yet — run: archivist compile {args.recipe}")
        return
    rows = json.load(open(path, encoding="utf-8"))
    for r in rows[: args.n]:
        flag = " ⚠inflated" if r.get("concept_inflated") else ""
        lbl = f" [{r['label']}]" if r.get("label") else ""
        print(f"  real:{r['real_attestations']:3} (of {r['total_mentions']:3})  {r['name'][:40]:40}{lbl}{flag}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="archivist", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grind", help="discover + extract from the Wayback Machine (resumable)")
    g.add_argument("recipe")
    g.add_argument("--minutes", type=float, default=60.0, help="time budget for this run")
    g.add_argument("--no-think", action="store_true", help="disable model thinking (faster, lower quality)")
    g.add_argument("--need-wired-mb", type=int, default=20000, help="min GPU wired memory to require")
    g.add_argument("--no-compile", dest="compile", action="store_false", help="skip compile after the run")
    g.set_defaults(func=cmd_grind, compile=True)

    c = sub.add_parser("compile", help="dedup + rank items.jsonl into catalog.json")
    c.add_argument("recipe")
    c.set_defaults(func=cmd_compile)

    s = sub.add_parser("show", help="print the top of the compiled catalog")
    s.add_argument("recipe")
    s.add_argument("--n", type=int, default=30)
    s.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
