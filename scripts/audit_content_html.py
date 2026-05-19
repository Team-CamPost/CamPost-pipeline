"""Audit render-ready content_html fields in raw notice JSON files."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import OUTPUT_DIR
from crawler.quality import audit_content_html, load_raw_notices


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit content_html/content_assets quality.")
    parser.add_argument("--root", type=Path, default=OUTPUT_DIR, help="pipeline data root")
    parser.add_argument("--json", action="store_true", help="print full JSON report")
    parser.add_argument("--limit", type=int, default=30, help="max issues to print in text mode")
    args = parser.parse_args()

    raw_dir = args.root / "raw"
    files_dir = args.root / "files"
    notices, load_errors = load_raw_notices(raw_dir)
    report = audit_content_html(notices, files_dir)
    report["load_errors"] = load_errors

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    summary = report["summary"]
    print("[content-html audit]")
    print(f"raw notices       : {summary['notices']}")
    print(f"with content_html : {summary['with_content_html']}")
    print(f"empty content_html: {summary['empty_content_html']}")
    print(f"with tables/images: {summary['with_tables']} / {summary['with_images']}")
    print(f"issues            : {summary['issue_count']} {summary['issue_counts']}")
    if load_errors:
        print(f"load errors       : {len(load_errors)}")
    for issue in report["issues"][: args.limit]:
        print(f"- [{issue['severity']}] {issue['article_id']} {issue['code']}: {issue['message']}")


if __name__ == "__main__":
    main()
