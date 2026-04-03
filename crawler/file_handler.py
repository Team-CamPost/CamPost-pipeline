"""
CamPost Crawler — 첨부파일 다운로드 및 텍스트 추출
PDF  : pdfplumber
HWP  : olefile (PrvText 스트림)
HWPX : zipfile + XML 파싱
기타 : 다운로드만, 텍스트 추출 생략
"""

import hashlib
import logging
import mimetypes
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from .config import FILES_DIR, EXTRACTABLE_EXTS, USER_AGENT

mimetypes.add_type("application/x-hwp", ".hwp")
mimetypes.add_type("application/x-hwpx", ".hwpx")

log = logging.getLogger("campost.file_handler")


def _safe_filename(article_id: str, name: str) -> str:
    safe = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return f"{article_id}_{safe}"


def _compute_checksum(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _get_mime_type(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


async def download_file(url: str, save_path: Path) -> bool:
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)
            log.debug(f"다운로드 완료: {save_path.name} ({len(resp.content):,} bytes)")
            return True
    except Exception as exc:
        log.warning(f"다운로드 실패 ({save_path.name}): {exc}")
        return False


def _extract_pdf(path: Path) -> tuple[str, str]:
    """Returns (extracted_text, parser_name)"""
    try:
        import pdfplumber
        texts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texts.append(t)
        return "\n".join(texts).strip(), "pdfplumber"
    except Exception as exc:
        log.warning(f"PDF 파싱 실패 ({path.name}): {exc}")
        return "", "pdfplumber"


def _extract_hwp(path: Path) -> tuple[str, str]:
    """olefile로 HWP PrvText 스트림(UTF-16LE 평문) 추출."""
    try:
        import olefile
        ole = olefile.OleFileIO(str(path))
        if ole.exists("PrvText"):
            raw = ole.openstream("PrvText").read()
            return raw.decode("utf-16-le", errors="ignore").strip(), "olefile"
        log.warning(f"HWP PrvText 스트림 없음: {path.name}")
        return "", "olefile"
    except Exception as exc:
        log.warning(f"HWP 파싱 실패 ({path.name}): {exc}")
        return "", "olefile"


def _extract_hwpx(path: Path) -> tuple[str, str]:
    """HWPX(ZIP+XML) 에서 텍스트 추출."""
    try:
        texts = []
        with zipfile.ZipFile(path) as z:
            section_files = sorted(
                [n for n in z.namelist()
                 if n.startswith("Contents/section") and n.endswith(".xml")]
            )
            for section in section_files:
                with z.open(section) as f:
                    root = ET.parse(f).getroot()
                    for el in root.iter():
                        if el.tag.endswith("}t") or el.tag == "t":
                            if el.text:
                                texts.append(el.text)
        return "\n".join(texts).strip(), "hwpx_xml"
    except Exception as exc:
        log.warning(f"HWPX 파싱 실패 ({path.name}): {exc}")
        return "", "hwpx_xml"


def extract_text(path: Path, ext: str) -> tuple[str, str]:
    """
    파일 텍스트 추출.
    Returns (extracted_text, parser_name)
    """
    if ext == "pdf":
        return _extract_pdf(path)
    if ext == "hwp":
        return _extract_hwp(path)
    if ext == "hwpx":
        return _extract_hwpx(path)
    return "", "none"


async def process_attachments(attachments: list[dict], article_id: str) -> list[dict]:
    """
    각 첨부파일을 다운로드하고 텍스트를 추출하여 반환.

    추가되는 필드:
        file_key       : "{article_id}_{safe_filename}"
        local_path     : data/files/ 기준 상대 경로
        mime_type, file_size, checksum
        extracted_text : 추출된 텍스트
        download_ok    : 다운로드 성공 여부
        parser         : 사용한 파서 이름 (parse_logs 기록용)
        parse_ok       : 텍스트 추출 성공 여부
    """
    results = []
    for att in attachments:
        filename = _safe_filename(article_id, att["name"])
        save_path = FILES_DIR / filename

        download_ok = await download_file(att["url"], save_path)

        extracted_text = ""
        parser = "none"
        parse_ok = False

        if download_ok and att["ext"] in EXTRACTABLE_EXTS:
            extracted_text, parser = extract_text(save_path, att["ext"])
            parse_ok = bool(extracted_text)
            log.info(
                f"  텍스트 추출 [{att['ext'].upper()}] {att['name'][:30]} "
                f"→ {len(extracted_text)}자 (parser={parser})"
            )

        file_key = filename
        checksum = _compute_checksum(save_path) if download_ok else None
        file_size = save_path.stat().st_size if download_ok else None
        mime_type = _get_mime_type(att["name"])

        results.append({
            **att,
            "file_key":       file_key,
            "local_path":     f"files/{filename}",
            "mime_type":      mime_type,
            "file_size":      file_size,
            "checksum":       checksum,
            "extracted_text": extracted_text,
            "download_ok":    download_ok,
            "parser":         parser,
            "parse_ok":       parse_ok,
        })

    return results
