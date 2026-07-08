"""Skills Hub — built-in catalog of curated skill YAML files.

Skills are stored in ``bauer/data/skills/<category>/<slug>.yaml`` and can
be installed to the user's ``~/.bauer/skills/`` directory via the CLI or
the :meth:`SkillsHub.install` method.

CLI::

    bauer skills hub list [--category devops]
    bauer skills hub search <query>
    bauer skills hub install <slug>
    bauer skills hub show <slug>

"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# Directory inside the bauer package where built-in skills live.
_SKILLS_DATA_DIR = Path(__file__).parent / "data" / "skills"


@dataclass
class HubSkillEntry:
    slug: str
    name: str
    category: str
    description: str
    path: Path

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "category": self.category,
            "description": self.description,
        }


class SkillsHub:
    """Catalog of built-in skills shipped with the Bauer package."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._dir = Path(data_dir) if data_dir else _SKILLS_DATA_DIR

    def _load_entry(self, path: Path) -> HubSkillEntry | None:
        try:
            text = path.read_text(encoding="utf-8")
            if _HAS_YAML:
                data = _yaml.safe_load(text) or {}
            else:
                data = _simple_parse(text)
            name = data.get("name") or path.stem
            desc = data.get("description") or ""
            category = path.parent.name
            slug = path.stem
            return HubSkillEntry(slug=slug, name=name, category=category,
                                 description=desc, path=path)
        except Exception:
            return None

    def list_skills(self, category: str | None = None) -> list[HubSkillEntry]:
        """Return all hub skills, optionally filtered by category."""
        if not self._dir.exists():
            return []
        entries: list[HubSkillEntry] = []
        pattern = f"{category}/*.yaml" if category else "**/*.yaml"
        for p in sorted(self._dir.glob(pattern)):
            if not p.is_file():
                continue
            e = self._load_entry(p)
            if e:
                entries.append(e)
        return entries

    def categories(self) -> list[str]:
        if not self._dir.exists():
            return []
        return sorted(p.name for p in self._dir.iterdir() if p.is_dir())

    def get(self, slug: str) -> HubSkillEntry | None:
        for e in self.list_skills():
            if e.slug == slug:
                return e
        return None

    def search(self, query: str, top_k: int = 10) -> list[HubSkillEntry]:
        """TF-IDF ranking over slug + name + description."""
        skills = self.list_skills()
        if not query.strip() or not skills:
            return skills

        def _tokens(text: str) -> list[str]:
            return re.findall(r"[a-zA-Z0-9]+", text.lower())

        q_tokens = _tokens(query)
        if not q_tokens:
            return skills[:top_k]

        # Build IDF on the corpus of all skill texts
        corpus: list[list[str]] = []
        for s in skills:
            corpus.append(_tokens(f"{s.slug} {s.name} {s.description}"))

        N = len(corpus)
        df: Counter[str] = Counter()
        for doc in corpus:
            for tok in set(doc):
                df[tok] += 1

        def _idf(tok: str) -> float:
            return math.log((1 + N) / (1 + df.get(tok, 0))) + 1.0

        def _score(doc_tokens: list[str]) -> float:
            freq = Counter(doc_tokens)
            total = len(doc_tokens) or 1
            s = 0.0
            for tok in q_tokens:
                tf = freq.get(tok, 0) / total
                s += tf * _idf(tok)
            return s

        scored = [(s, _score(c)) for s, c in zip(skills, corpus)]
        scored.sort(key=lambda x: -x[1])
        return [s for s, sc in scored if sc > 0][:top_k]

    def install(self, slug: str, dest_dir: str | Path | None = None) -> bool:
        """Copy a hub skill YAML to *dest_dir* (default: ``~/.bauer/skills/``)."""
        entry = self.get(slug)
        if entry is None:
            return False
        if dest_dir is None:
            dest = Path.home() / ".bauer" / "skills"
        else:
            dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"{slug}.yaml"
        target.write_bytes(entry.path.read_bytes())
        return True

    def read_content(self, slug: str) -> str | None:
        """Return raw YAML content for a hub skill."""
        entry = self.get(slug)
        if entry is None:
            return None
        return entry.path.read_text(encoding="utf-8")


def get_default_hub() -> SkillsHub:
    return SkillsHub()


# ---------------------------------------------------------------------------
# Minimal YAML key=value parser (no pyyaml fallback)
# ---------------------------------------------------------------------------

def _simple_parse(text: str) -> dict:
    result: dict = {}
    for line in text.splitlines():
        if ":" in line and not line.strip().startswith("-"):
            key, _, val = line.partition(":")
            k = key.strip()
            v = val.strip().strip('"').strip("'")
            if k and v:
                result[k] = v
    return result
