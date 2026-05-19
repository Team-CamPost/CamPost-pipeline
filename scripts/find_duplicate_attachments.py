"""Find duplicate attachment candidates without deleting or rewriting files."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import OUTPUT_DIR
from crawler.quality import find_duplicate_attachments, load_raw_notices


def main() -> None:
    parser = argparse.ArgumentParser(description="Find duplicate attachment candidates.")
    parser.add_argument("--root", type=Path, default=OUTPUT_DIR, help="pipeline data root")
    parser.add_argument("--json", action="store_true", help="print full JSON report")
    parser.add_argument("--limit", type=int, default=20, help="max duplicate groups to print in text mode")
    args = parser.parse_args()

    raw_dir = args.root / "raw"
    notices, load_errors = load_raw_notices(raw_dir)
    report = find_duplicate_attachments(notices)
    report["load_errors"] = load_errors

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    summary = report["summary"]
    print("[duplicate-attachments audit]")
    print(f"raw notices              : {summary['notices']}")
    print(f"attachments              : {summary['attachments']}")
    print(f"duplicate checksum groups: {summary['duplicate_checksum_groups']}")
    print(f"duplicate url groups     : {summary['duplicate_url_groups']}")
    print(f"same-notice duplicates   : {summary['same_notice_duplicates']}")
    if load_errors:
        print(f"load errors              : {len(load_errors)}")

    for group in report["duplicate_checksum_groups"][: args.limit]:
        checksum = group[0].get("checksum")
        print(f"- checksum {checksum}: {len(group)} attachments")
        for item in group[:5]:
            print(f"  {item['article_id']}[{item['index']}] {item.get('file_key')}")


if __name__ == "__main__":
    main()
