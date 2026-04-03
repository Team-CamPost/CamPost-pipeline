"""
CamPost Crawler — 루트 실행 스크립트 (V3)

  python main.py          # 1회 실행 후 종료
  python main.py --loop   # APScheduler 반복 실행

V3 저장 구조:
  data/raw/{article_id}.json   ← Python Crawler가 쓰는 RawStore
  data/files/                  ← 첨부파일 다운로드
  data/seen_hashes.json        ← 중복 방지 해시

DB 적재는 Spring Boot Importer의 책임이다.
"""

from crawler import main

if __name__ == "__main__":
    main()
