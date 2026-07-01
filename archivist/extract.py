"""
archivist.extract — the extraction backend(s).

An extractor is any callable: text -> list[dict] | None. The engine doesn't care how the
list is produced. Today there's one backend: a local MLX model on Apple Silicon ($0/page,
fully offline, your data never leaves the machine). Keeping this the *only* MLX-dependent
module means the engine imports and runs without it — and means new backends (a local
Ollama server, your own Claude subscription) slot in without touching the engine.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys


def parse_arr(s):
    """Pull the final JSON array out of a model response (tolerant of <think> blocks and
    ```json fences)."""
    if not s:
        return None
    s = re.sub(r"<think>.*?</think>", " ", s, flags=re.S | re.I).replace("```json", " ").replace("```", " ")
    a, b = s.find("["), s.rfind("]")
    if a < 0 or b < 0 or b <= a:
        return None
    try:
        j = json.loads(s[a : b + 1])
        return j if isinstance(j, list) else None
    except Exception:
        return None


def check_gpu_wired(need_mb=20000, log=print):
    """A 30B/32B-class model needs ~18-21GB of GPU-wired memory; macOS caps that at a
    default ~16.8GB regardless of free RAM, and a reboot/sleep RESETS any raised limit.
    Without this check the model OOMs mid-inference — an uncatchable Metal abort that kills
    the whole process. Refuse cleanly with the fix instead of crashing hours in."""
    try:
        w = int(
            subprocess.run(
                ["sysctl", "-n", "iogpu.wired_limit_mb"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
        )
    except Exception:
        return  # not macOS / can't tell — let it run
    if w == 0 or (0 < w < need_mb):
        eff = "default (~16.8GB)" if w == 0 else f"{w}MB"
        log(f"!! GPU wired limit is {eff} — too low (needs ~{need_mb}MB). A reboot/sleep resets it.")
        log(f"!! Raise it, then relaunch:   sudo sysctl iogpu.wired_limit_mb={need_mb + 1504}")
        sys.exit(2)


class MLXExtractor:
    def __init__(self, recipe, think=True, max_tokens=4000, log=print):
        try:
            from mlx_lm import generate, load
        except ImportError:
            log("!! mlx-lm not installed. On Apple Silicon:  pip install mlx-lm")
            sys.exit(2)
        self._generate = generate
        self.recipe = recipe
        self.think = think
        self.max_tokens = max_tokens
        self.prompt_head = recipe.prompt_head()
        t0 = __import__("time").time()
        self.model, self.tok = load(recipe.model)
        log(f"model loaded ({recipe.model.split('/')[-1]}) in {__import__('time').time() - t0:.0f}s")

    def __call__(self, text):
        full = self.prompt_head + "\n\nPAGE TEXT:\n" + text
        msgs = [{"role": "user", "content": full}]
        try:
            prompt = self.tok.apply_chat_template(msgs, add_generation_prompt=True, enable_thinking=self.think)
        except TypeError:
            prompt = self.tok.apply_chat_template(msgs, add_generation_prompt=True)
        resp = self._generate(self.model, self.tok, prompt=prompt, max_tokens=self.max_tokens, verbose=False)
        return parse_arr(resp)
