"""Rebuild extracted_text for downloaded DOCX attachments in raw JSON files.

Usage:
  python scripts/reparse_docx_attachments.py
  python scripts/reparse_docx_attachments.py --source SW
  python scripts/reparse_docx_attachments.py --dry-run
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import OUTPUT_DIR
from crawler.file_handler import extract_text


def _resolve_local_path(local_path: str) -> Path:
    rel = Path(local_path)
    if rel.is_absolute():
        return rel
    if rel.parts and rel.parts[0] == "files":
        return Path(OUTPUT_DIR) / rel
    return Path(OUTPUT_DIR) / "files" / rel.name


def reparse_docx_attachments(dry_run: bool, source_filter: str | None) -> None:
    raw_dir = Path(OUTPUT_DIR) / "raw"
    files = sorted(raw_dir.glob("*.json"))
    if source_filter:
        prefix = source_filter.upper() + "_"
        files = [path for path in files if path.stem.startswith(prefix)]

    print(f"[docx-reparse] targets: {len(files)} | dry-run: {dry_run}")

    updated_files = 0
    updated_attachments = 0
    errors = 0
    parser_counts: Counter[str] = Counter()

    for raw_path in files:
        try:
            data = json.loads(raw_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[error] {raw_path.name} read failed: {exc}")
            errors += 1
            continue

        attachments = data.get("attachments")
        if not isinstance(attachments, list):
            continue

        changed = False
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if (attachment.get("ext") or "").lower() != "docx":
                continue
            if not attachment.get("download_ok") or not attachment.get("local_path"):
                continue

            local_path = _resolve_local_path(attachment.get("local_path") or "")
            if not local_path.exists():
                print(f"[error] {raw_path.stem} missing file: {attachment.get('local_path')}")
                errors += 1
                continue

            try:
                text, parser = extract_text(local_path, "docx")
            except Exception as exc:
                print(f"[error] {raw_path.stem} {attachment.get('name')}: {exc}")
                errors += 1
                continue

            parser_counts[parser] += 1
            parse_ok = bool(text)
            new_values = {
                "extracted_text": text,
                "extracted_chars": len(text),
                "parser": parser,
                "parse_quality": "full" if parser == "docx_xml" and parse_ok else "none",
                "parse_ok": parse_ok,
            }

            if any(attachment.get(key) != value for key, value in new_values.items()):
                print(
                    f"[update] {raw_path.stem} | {attachment.get('name')} | "
                    f"{attachment.get('parser')} -> {parser} | {len(text)} chars"
                )
                attachment.update(new_values)
                changed = True
                updated_attachments += 1

        if changed:
            updated_files += 1
            if not dry_run:
                raw_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    action = "would update" if dry_run else "updated"
    print(
        f"[docx-reparse] {action}: files={updated_files}, "
        f"attachments={updated_attachments}, errors={errors}"
    )
    print(f"[docx-reparse] parsers: {dict(parser_counts)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reparse downloaded DOCX attachments.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source", help="process one source code only, e.g. SW")
    args = parser.parse_args()
    reparse_docx_attachments(dry_run=args.dry_run, source_filter=args.source)
