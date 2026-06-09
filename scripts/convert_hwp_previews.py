"""Generate PDF preview files for downloaded HWP/HWPX attachments.

Usage:
  python scripts/convert_hwp_previews.py
  python scripts/convert_hwp_previews.py --source SW
  python scripts/convert_hwp_previews.py --dry-run
  python scripts/convert_hwp_previews.py --force
"""

import argparse
import json
import mimetypes
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import OUTPUT_DIR, PDF_PREVIEW_EXTS
from crawler.file_handler import convert_to_pdf_preview
from crawler.r2_storage import upload_file_to_r2
from crawler.reprocess import stamp_reprocessed_at


def _resolve_local_path(files_dir: Path, local_path: str | None) -> Path | None:
    if not local_path:
        return None
    normalized = local_path.replace("\\", "/")
    if normalized.startswith("files/"):
        return files_dir / normalized.removeprefix("files/")
    return files_dir / Path(normalized).name


def _content_type(filename: str) -> str:
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HWP/HWPX PDF previews.")
    parser.add_argument("--root", type=Path, default=OUTPUT_DIR, help="pipeline data root")
    parser.add_argument("--source", help="article id prefix, e.g. SW")
    parser.add_argument(
        "--dry-run", action="store_true", help="print candidates without converting"
    )
    parser.add_argument(
        "--force", action="store_true", help="reconvert attachments with success status"
    )
    parser.add_argument(
        "--skip-r2", action="store_true", help="do not upload generated files to R2"
    )
    parser.add_argument("--limit", type=int, help="maximum attachments to process")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be greater than or equal to 1")

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
        except (OSError, json.JSONDecodeError) as exc:
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

            metadata = convert_to_pdf_preview(local_path, ext, force=args.force)
            attachment.update(metadata)

            if not args.skip_r2:
                local_key = str(attachment.get("local_path") or "").replace("\\", "/")
                if local_key:
                    uploaded_url = upload_file_to_r2(
                        local_path, local_key, _content_type(local_path.name)
                    )
                    if uploaded_url:
                        attachment["r2_url"] = uploaded_url

                preview_pdf_path = str(metadata.get("preview_pdf_path") or "").replace("\\", "/")
                preview_local_path = _resolve_local_path(files_dir, preview_pdf_path)
                if (
                    metadata["conversion_status"] == "success"
                    and preview_pdf_path
                    and preview_local_path
                ):
                    uploaded_preview_url = upload_file_to_r2(
                        preview_local_path,
                        preview_pdf_path,
                        "application/pdf",
                    )
                    if uploaded_preview_url:
                        attachment["preview_pdf_r2_url"] = uploaded_preview_url

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
