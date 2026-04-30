"""
Rebuild derived raw JSON fields.

Examples:
  python scripts/re_extract.py --no-ai --source SW --fields deadline
  python scripts/re_extract.py --source SW --fields content_html,content_assets,content_stats
  python scripts/re_extract.py --dry-run --source SW --fields content_html,content_assets,content_stats
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import GEMINI_API_KEY, GEMINI_MODEL, OUTPUT_DIR
from crawler.content import build_content_payload
from crawler.extractor import extract_key_info_with_ai

KEY_INFO_FIELDS = {"deadline", "target", "apply_method"}
CONTENT_FIELDS = {"content_html", "content_assets", "content_stats"}
ALLOWED_FIELDS = KEY_INFO_FIELDS | CONTENT_FIELDS


def _preview(value: object, limit: int = 80) -> str:
    text = str(value)[:limit]
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="backslashreplace").decode(encoding)


def re_extract(
    dry_run: bool,
    no_ai: bool,
    only_null: bool,
    source_filter: str | None,
    fields: set[str],
) -> None:
    raw_dir = Path(OUTPUT_DIR) / "raw"

    if not raw_dir.exists():
        print(f"[error] raw directory does not exist: {raw_dir}")
        sys.exit(1)

    files = sorted(raw_dir.glob("*.json"))
    if source_filter:
        prefix = source_filter.upper() + "_"
        files = [f for f in files if f.stem.startswith(prefix)]

    api_key = "" if no_ai else GEMINI_API_KEY
    model = GEMINI_MODEL
    wants_key_info = bool(fields & KEY_INFO_FIELDS)
    wants_content = bool(fields & CONTENT_FIELDS)

    print(
        f"[re-extract] targets: {len(files)} | AI: {'OFF' if no_ai else f'ON ({model})'} | "
        f"only-null: {only_null} | dry-run: {dry_run} | fields: {','.join(sorted(fields))}"
    )
    if source_filter:
        print(f"             source: {source_filter.upper()}")
    print("-" * 60)

    updated = 0
    skipped = 0
    errors = 0

    for i, path in enumerate(files, 1):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[{i}/{len(files)}] {path.name} read failed: {exc}")
            errors += 1
            continue

        body_text = data.get("body_text") or ""
        body_html = data.get("body_html") or ""
        attachments = data.get("attachments") or []
        has_att_text = any(a.get("extracted_text") for a in attachments)

        if wants_key_info and not body_text and not has_att_text:
            skipped += 1
            continue

        old: dict = {}
        result: dict = {}

        if wants_key_info:
            try:
                extracted = extract_key_info_with_ai(
                    body_text,
                    attachments,
                    api_key,
                    model,
                    title=data.get("title") or "",
                    notice_date=data.get("date") or "",
                )
            except Exception as exc:
                print(f"[{i}/{len(files)}] {path.stem} extraction failed: {exc}")
                errors += 1
                continue

            for key in KEY_INFO_FIELDS:
                old[key] = data.get(key)
                result[key] = extracted[key] if key in fields else old[key]

        if wants_content:
            content_payload = build_content_payload(body_html, attachments)
            for key in CONTENT_FIELDS:
                old[key] = data.get(key)
                result[key] = content_payload[key] if key in fields else old[key]

        if only_null:
            result = {key: value if old[key] is None else old[key] for key, value in result.items()}

        changed = {key for key in old if old[key] != result[key]}
        if not changed:
            skipped += 1
            continue

        diff_str = " | ".join(
            f"{key}: {_preview(old[key])!r} -> {_preview(result[key])!r}"
            for key in sorted(changed)
        )
        print(f"[{i}/{len(files)}] {path.stem}  {diff_str}")

        if not dry_run:
            for key, value in result.items():
                data[key] = value
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        updated += 1

    print("-" * 60)
    action = "would update" if dry_run else "updated"
    print(f"[re-extract] {action}: {updated} | unchanged: {skipped} | errors: {errors}")
    if not dry_run and updated > 0:
        print("  Importer should pick up the changed raw JSON files on its next cycle.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild derived raw JSON fields.")
    parser.add_argument("--dry-run", action="store_true", help="print changes without writing files")
    parser.add_argument("--no-ai", action="store_true", help="disable AI extraction for key info fields")
    parser.add_argument(
        "--only-null",
        action="store_true",
        help="only fill fields whose current value is null",
    )
    parser.add_argument("--source", help="process one source code only, e.g. SW")
    parser.add_argument(
        "--fields",
        default="deadline,target,apply_method",
        help=(
            "comma-separated fields to update. "
            "Allowed: deadline,target,apply_method,content_html,content_assets,content_stats"
        ),
    )
    args = parser.parse_args()

    selected_fields = {field.strip() for field in args.fields.split(",") if field.strip()}
    unknown_fields = selected_fields - ALLOWED_FIELDS
    if unknown_fields:
        print(f"[error] unknown fields: {', '.join(sorted(unknown_fields))}")
        sys.exit(1)
    if not selected_fields:
        print("[error] fields cannot be empty")
        sys.exit(1)

    re_extract(
        dry_run=args.dry_run,
        no_ai=args.no_ai,
        only_null=args.only_null,
        source_filter=args.source,
        fields=selected_fields,
    )
