"""
CamPost Pipeline — re-extract 배치 스크립트

/data/raw/*.json을 순회해 현재 extractor로 deadline/target/apply_method를 재추출하고
변경된 파일만 덮어써서 Importer가 DB를 자동 갱신하게 합니다.

사용법:
  python scripts/re_extract.py                          # 전체 실행 (AI 포함)
  python scripts/re_extract.py --no-ai                  # regex만 (빠름, API 키 불필요)
  python scripts/re_extract.py --only-null              # null인 필드만 채움 (기존 값 보존)
  python scripts/re_extract.py --dry-run                # 변경 예정 내용만 출력 (저장 안 함)
  python scripts/re_extract.py --source SW              # 특정 소스만
  python scripts/re_extract.py --no-ai --only-null      # 안전 모드: regex로 null만 채움
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import GEMINI_API_KEY, GEMINI_MODEL, OUTPUT_DIR
from crawler.extractor import extract_key_info_with_ai


def re_extract(
    dry_run: bool,
    no_ai: bool,
    only_null: bool,
    source_filter: str | None,
    fields: set[str],
) -> None:
    raw_dir = Path(OUTPUT_DIR) / "raw"

    if not raw_dir.exists():
        print(f"[오류] raw 디렉토리가 없습니다: {raw_dir}")
        sys.exit(1)

    files = sorted(raw_dir.glob("*.json"))

    if source_filter:
        prefix = source_filter.upper() + "_"
        files = [f for f in files if f.stem.startswith(prefix)]

    api_key = "" if no_ai else GEMINI_API_KEY
    model = GEMINI_MODEL
    total = len(files)

    print(
        f"[re-extract] 대상: {total}개 | AI: {'OFF' if no_ai else f'ON ({model})'} | "
        f"only-null: {only_null} | dry-run: {dry_run} | fields: {','.join(sorted(fields))}"
    )
    if source_filter:
        print(f"             소스 필터: {source_filter.upper()}")
    print("-" * 60)

    updated = 0
    skipped = 0
    errors = 0

    for i, path in enumerate(files, 1):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[{i}/{total}] {path.name} 읽기 실패: {e}")
            errors += 1
            continue

        body_text = data.get("body_text") or ""
        attachments = data.get("attachments") or []

        # 본문도 첨부파일 텍스트도 없으면 스킵
        has_att_text = any(a.get("extracted_text") for a in attachments)
        if not body_text and not has_att_text:
            skipped += 1
            continue

        try:
            result = extract_key_info_with_ai(
                body_text,
                attachments,
                api_key,
                model,
                title=data.get("title") or "",
                notice_date=data.get("date") or "",
            )
        except Exception as e:
            print(f"[{i}/{total}] {path.stem} 추출 실패: {e}")
            errors += 1
            continue

        old = {
            "deadline":     data.get("deadline"),
            "target":       data.get("target"),
            "apply_method": data.get("apply_method"),
        }

        result = {
            k: result[k] if k in fields else old[k]
            for k in old
        }

        # --only-null: 기존 값이 있는 필드는 건드리지 않음
        if only_null:
            result = {
                k: result[k] if old[k] is None else old[k]
                for k in old
            }

        changed = {k for k in old if old[k] != result[k]}

        if not changed:
            skipped += 1
            continue

        diff_str = " | ".join(
            f"{k}: {str(old[k])!r} → {str(result[k])!r}" for k in sorted(changed)
        )
        print(f"[{i}/{total}] {path.stem}  {diff_str}")

        if not dry_run:
            data["deadline"]     = result["deadline"]
            data["target"]       = result["target"]
            data["apply_method"] = result["apply_method"]
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        updated += 1

    print("-" * 60)
    action = "변경 예정" if dry_run else "업데이트 완료"
    print(f"[re-extract] {action}: {updated}개 | 변경 없음: {skipped}개 | 오류: {errors}개")
    if not dry_run and updated > 0:
        print("  → Importer가 30초 내 DB 자동 반영합니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="extractor 재실행 배치 스크립트")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="실제 저장 없이 변경 내용만 출력",
    )
    parser.add_argument(
        "--no-ai", action="store_true",
        help="AI 호출 없이 regex만 사용 (빠름, API 쿼터 절약)",
    )
    parser.add_argument(
        "--only-null", action="store_true",
        help="null인 필드만 채움 (기존 값이 있으면 건드리지 않음, 안전 모드)",
    )
    parser.add_argument(
        "--source",
        help="특정 소스 코드만 처리 (예: SW, ACE, MOBILE)",
    )
    parser.add_argument(
        "--fields",
        default="deadline,target,apply_method",
        help="갱신할 필드 목록(쉼표 구분). 예: deadline",
    )
    args = parser.parse_args()

    allowed_fields = {"deadline", "target", "apply_method"}
    selected_fields = {field.strip() for field in args.fields.split(",") if field.strip()}
    unknown_fields = selected_fields - allowed_fields
    if unknown_fields:
        print(f"[오류] 알 수 없는 fields 값: {', '.join(sorted(unknown_fields))}")
        sys.exit(1)
    if not selected_fields:
        print("[오류] fields 값이 비어 있습니다.")
        sys.exit(1)

    re_extract(
        dry_run=args.dry_run,
        no_ai=args.no_ai,
        only_null=args.only_null,
        source_filter=args.source,
        fields=selected_fields,
    )
