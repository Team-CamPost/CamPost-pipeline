"""
Rebuild extracted_text for downloaded HWP attachments in raw JSON files.

Examples:
  python scripts/reparse_hwp_attachments.py
  python scripts/reparse_hwp_attachments.py --source SW
  python scripts/reparse_hwp_attachments.py --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import OUTPUT_DIR
from crawler.file_handler import extract_text


def _resolve_local_path(local_path: str) -> Path:
    rel = local_path.replace("\\", "/")
    if rel.startswith("files/"):
        return Path(OUTPUT_DIR) / rel
    return Path(rel)


def reparse_hwp_attachments(dry_run: bool, source_filter: str | None) -> None:
    raw_dir = Path(OUTPUT_DIR) / "raw"
    files = sorted(raw_dir.glob("*.json"))
    if source_filter:
        prefix = source_filter.upper() + "_"
        files = [path for path in files if path.stem.startswith(prefix)]

    updated_files = 0
    updated_attachments = 0
    unchanged = 0
    errors = 0
    parser_counts: dict[str, int] = {}

    print(
        f"[hwp-reparse] targets: {len(files)} | dry-run: {dry_run}"
        + (f" | source: {source_filter.upper()}" if source_filter else "")
    )
    print("-" * 60)

    for raw_path in files:
        try:
            data = json.loads(raw_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[error] {raw_path.name} read failed: {exc}")
            errors += 1
            continue

        attachments = data.get("attachments") or []
        if not isinstance(attachments, list):
            continue

        changed = False
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if (attachment.get("ext") or "").lower() != "hwp":
                continue
            if not attachment.get("download_ok") or not attachment.get("local_path"):
                continue

            local_path = _resolve_local_path(str(attachment["local_path"]))
            if not local_path.exists():
                print(f"[error] {raw_path.stem} missing file: {attachment.get('local_path')}")
                errors += 1
                continue

            try:
                text, parser = extract_text(local_path, "hwp")
            except Exception as exc:
                print(f"[error] {raw_path.stem} {attachment.get('name')}: {exc}")
                errors += 1
                continue

            extracted = text.strip()
            parse_ok = bool(extracted)
            quality = (
                "full"
                if parse_ok and parser == "pyhwp_bodytext"
                else "preview"
                if parse_ok and parser == "olefile_prvtext"
                else "none"
            )
            parser_counts[parser] = parser_counts.get(parser, 0) + 1

            old = (
                attachment.get("extracted_text") or "",
                attachment.get("parser"),
                attachment.get("parse_quality"),
                attachment.get("extracted_chars"),
            )
            new = (extracted, parser, quality, len(extracted))
            if old == new:
                unchanged += 1
                continue

            print(
                f"[update] {raw_path.stem} | {attachment.get('name')} | "
                f"{attachment.get('parser')}->{parser} | "
                f"{len(attachment.get('extracted_text') or '')}->{len(extracted)} chars"
            )
            attachment["extracted_text"] = extracted
            attachment["parser"] = parser
            attachment["parse_ok"] = parse_ok
            attachment["parse_quality"] = quality
            attachment["extracted_chars"] = len(extracted)
            changed = True
            updated_attachments += 1

        if changed:
            updated_files += 1
            if not dry_run:
                raw_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    print("-" * 60)
    action = "would update" if dry_run else "updated"
    print(
        f"[hwp-reparse] {action}: files={updated_files}, "
        f"attachments={updated_attachments}, unchanged={unchanged}, errors={errors}"
    )
    print(f"[hwp-reparse] parsers: {parser_counts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild HWP attachment extracted_text fields.")
    parser.add_argument("--dry-run", action="store_true", help="print changes without writing files")
    parser.add_argument("--source", help="process one source code only, e.g. SW")
    args = parser.parse_args()
    reparse_hwp_attachments(dry_run=args.dry_run, source_filter=args.source)
