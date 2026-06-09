"""Repair raw metadata for specific notices without touching content assets."""

from __future__ import annotations

import json
from pathlib import Path

RAW_DIR = Path("/data/raw")

PATCHES = {
    "SW_175057": {
        "author": "소프트웨어학과",
        "date": "2026.05.13",
        "views": "19",
        "category": "행사 및 대회",
    },
    "SW_175082": {
        "author": "소프트웨어학과",
        "date": "2026.05.14",
        "views": "29",
        "category": "행사 및 대회",
    },
}


def main() -> None:
    for article_id, patch in PATCHES.items():
        path = RAW_DIR / f"{article_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(patch)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{article_id}: {patch}")


if __name__ == "__main__":
    main()
