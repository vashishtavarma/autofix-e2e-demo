"""Language know-how lives in markdown files so the pipeline stays generic."""
from __future__ import annotations

from .config import Config


def load_skills(cfg: Config) -> str:
    parts = []
    for name in cfg.skills:
        path = cfg.skills_dir / f"{name}.md"
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n---\n\n".join(parts)
