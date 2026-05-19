"""Normalize derived attachment metadata in raw notice JSON files."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import OUTPUT_DIR
from crawler.quality import load_raw_notices, normalize_attachment_metadata
from crawler.reprocess import stamp_reprocessed_at


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize attachment metadata in raw JSON.")
    parser.add_argument("--root", type=Path, default=OUTPUT_DIR, help="pipeline data root")
    parser.add_argument("--dry-run", action="store_true", help="print changes without writing files")
    parser.add_argument(
        "--refresh-file-metadata",
        action="store_true",
        help="also refresh file_size/checksum from files on disk",
    )
    parser.add_argument("--json", action="store_true", help="print full JSON change report")
    parser.add_argument("--limit", type=int, default=30, help="max changes to print in text mode")
    args = parser.parse_args()

    raw_dir = args.root / "raw"
    files_dir = args.root / "files"
    notices, load_errors = load_raw_notices(raw_dir)
    changes = normalize_attachment_metadata(
        notices,
        files_dir,
        refresh_file_metadata=args.refresh_file_metadata,
    )
    report = {
        "summary": {
            "notices": len(notices),
            "changed_notices": len(changes),
            "changed_attachments": sum(len(item["changes"]) for item in changes),
            "dry_run": args.dry_run,
            "load_errors": len(load_errors),
        },
        "changes": changes,
        "load_errors": load_errors,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print("[normalize-attachment-metadata]")
        print(f"raw notices        : {summary['notices']}")
        print(f"changed notices    : {summary['changed_notices']}")
        print(f"changed attachments: {summary['changed_attachments']}")
        print(f"dry-run            : {summary['dry_run']}")
        if load_errors:
            print(f"load errors        : {len(load_errors)}")
        printed = 0
        for notice_change in changes:
            for attachment_change in notice_change["changes"]:
                if printed >= args.limit:
                    break
                print(
                    f"- {notice_change['article_id']}[{attachment_change['index']}] "
                    f"{attachment_change.get('name')}: {attachment_change['updates']}"
                )
                printed += 1
            if printed >= args.limit:
                break

    if args.dry_run:
        return

    by_path = {item["path"]: item for item in changes}
    for notice in notices:
        change = by_path.get(str(notice.path))
        if not change:
            continue
        attachments = notice.data.get("attachments")
        if not isinstance(attachments, list):
            continue
        for attachment_change in change["changes"]:
            attachment = attachments[attachment_change["index"]]
            if isinstance(attachment, dict):
                attachment.update(attachment_change["updates"])
        stamp_reprocessed_at(notice.data)
        notice.path.write_text(json.dumps(notice.data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
