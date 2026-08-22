from __future__ import annotations

from pathlib import Path


def load_skills(directory: str) -> str:
    root = Path(directory)
    if not root.is_dir():
        return ""
    loaded: list[str] = []
    for path in sorted(root.glob("*/SKILL.md")):
        content = path.read_text(encoding="utf-8").strip()
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) == 3:
                content = parts[2].strip()
        loaded.append(f"## Skill: {path.parent.name}\n\n{content}")
    return "\n\n".join(loaded)
