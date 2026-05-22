"""Generate PDF preview files for downloaded HWP/HWPX attachments.

Usage:
  python scripts/convert_hwp_previews.py
  python scripts/convert_hwp_previews.py --source SW
  python scripts/convert_hwp_previews.py --dry-run
  python scripts/convert_hwp_previews.py --force
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import OUTPUT_DIR, PDF_PREVIEW_EXTS
from crawler.file_handler import convert_to_pdf_preview
from crawler.reprocess import stamp_reprocessed_at


def _resolve_local_path(files_dir: Path, local_path: str | None) -> Path | None:
    if not local_path:
        return None
    normalized = local_path.replace("\\", "/")
    if normalized.startswith("files/"):
        return files_dir / normalized.removeprefix("files/")
    return files_dir / Path(normalized).name


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HWP/HWPX PDF previews.")
    parser.add_argument("--root", type=Path, default=OUTPUT_DIR, help="pipeline data root")
    parser.add_argument("--source", help="article id prefix, e.g. SW")
    parser.add_argument("--dry-run", action="store_true", help="print candidates without converting")
    parser.add_argument("--force", action="store_true", help="reconvert attachments with success status")
    parser.add_argument("--limit", type=int, help="maximum attachments to process")
    args = parser.parse_args()

    raw_dir = args.root / "raw"
    files_dir = args.root / "files"
    source_prefix = f"{args.source.upper()}_" if args.source else None
    status_counts: Counter[str] = Counter()
    candidates = 0
    converted = 0
    errors = 0

    for path in sorted(raw_dir.glob("*.json")):
        if source_prefix and not path.stem.startswith(source_prefix):
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors += 1
            print(f"[error] read failed {path.name}: {exc}")
            continue

        attachments = data.get("attachments")
        if not isinstance(attachments, list):
            continue

        changed = False
        for attachment in attachments:
            if args.limit is not None and candidates >= args.limit:
                break
            if not isinstance(attachment, dict):
                continue

            ext = str(attachment.get("ext") or "").lower()
            if ext not in PDF_PREVIEW_EXTS:
                continue
            if attachment.get("conversion_status") == "success" and not args.force:
                continue

            local_path = _resolve_local_path(files_dir, attachment.get("local_path"))
            if not local_path or not local_path.exists():
                status_counts["missing_source"] += 1
                continue

            candidates += 1
            name = attachment.get("name") or local_path.name
            if args.dry_run:
                print(f"[candidate] {path.stem}: {name}")
                continue

            metadata = convert_to_pdf_preview(local_path, ext)
            attachment.update(metadata)
            status_counts[str(metadata["conversion_status"])] += 1
            changed = True
            if metadata["conversion_status"] == "success":
                converted += 1
            else:
                errors += 1
            print(f"[{metadata['conversion_status']}] {path.stem}: {name}")

        if changed:
            stamp_reprocessed_at(data)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.limit is not None and candidates >= args.limit:
            break

    print("[hwp-preview conversion]")
    print(f"candidates: {candidates}")
    print(f"converted : {converted}")
    print(f"errors    : {errors}")
    print(f"statuses  : {dict(status_counts)}")


if __name__ == "__main__":
    main()
