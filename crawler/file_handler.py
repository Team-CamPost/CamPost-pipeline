"""
CamPost Crawler — 첨부파일 다운로드 및 텍스트 추출
PDF  : pdfplumber
HWP  : pyhwp (BodyText 전체, 표 구조 포함) + olefile 폴백 (PrvText)
HWPX : zipfile + XML 파싱
기타 : 다운로드만, 텍스트 추출 생략
"""

import base64
import hashlib
import logging
import mimetypes
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from .config import EXTRACTABLE_EXTS, FILES_DIR, USER_AGENT

mimetypes.add_type("application/x-hwp", ".hwp")
mimetypes.add_type("application/x-hwpx", ".hwpx")
mimetypes.add_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")

log = logging.getLogger("campost.file_handler")
logging.getLogger("hwp5").setLevel(logging.WARNING)

_MAX_INLINE_IMAGES = 10
_MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


def _safe_filename(article_id: str, name: str) -> str:
    safe = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return f"{article_id}_{safe}"


def _compute_checksum(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _reusable_download_size(path: Path) -> int | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file() or stat.st_size <= 0:
        return None
    return stat.st_size


def _get_mime_type(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


async def download_file(url: str, save_path: Path) -> bool:
    headers = {"User-Agent": USER_AGENT}
    tmp_path = save_path.with_name(f".{save_path.name}.tmp")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            tmp_path.write_bytes(resp.content)
            tmp_path.replace(save_path)
            log.debug(f"다운로드 완료: {save_path.name} ({len(resp.content):,} bytes)")
            return True
    except Exception as exc:
        log.warning(f"다운로드 실패 ({save_path.name}): {exc}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError as cleanup_exc:
            log.warning(f"다운로드 실패 임시 파일 정리 실패 ({tmp_path.name}): {cleanup_exc}")
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


def _get_para_text_from_chunks(chunks: list) -> str:
    """pyhwp chunks에서 텍스트 직접 추출 (control code 건너뜀)."""
    parts = []
    for _rng, d in chunks:
        if isinstance(d, str):
            parts.append(d)
        elif isinstance(d, dict) and d.get("code") == 13:
            parts.append("\n")
    return "".join(parts).strip("\n")


def _extract_hwp_pyhwp(path: Path) -> str:
    """
    Extract all HWP BodyText paragraph text with pyhwp.

    This is a plain paragraph-text pass over every body section. It does not
    reconstruct table layout or styling. Returns an empty string when pyhwp is
    unavailable or the file cannot be parsed.
    """
    try:
        from hwp5.xmlmodel import Hwp5File
    except ImportError:
        return ""

    try:
        hw = Hwp5File(str(path))
        sections = list(hw.bodytext.sections)
        if not sections:
            return ""
        models: list[dict] = []
        for sec in sections:
            models.extend(list(sec.models()))
    except Exception as exc:
        log.debug(f"HWP pyhwp 파일 열기/섹션 수집 실패 ({path.name}): {exc}")
        return ""

    # Collect all body paragraph text. On the CAMPOST HWP sample set this was
    # more complete than reconstructing table state, which dropped text in
    # table-heavy notices.
    result_parts: list[str] = []
    try:
        for m in models:
            if m.get("tagname") != "HWPTAG_PARA_TEXT":
                continue
            text = _get_para_text_from_chunks(m.get("content", {}).get("chunks", [])).strip()
            if text:
                result_parts.append(text)
    except Exception as exc:
        log.warning(f"HWP pyhwp BodyText 순회 실패 ({path.name}): {exc}")
        return ""

    return "\n".join(result_parts)


def _extract_hwp(path: Path) -> tuple[str, str]:
    """
    Extract HWP text.

    Prefer pyhwp BodyText paragraph extraction. PrvText is used only as a
    fallback when BodyText extraction returns no text. Returns ("", "none")
    when neither source has usable text.
    """
    pyhwp_text = _extract_hwp_pyhwp(path)
    if pyhwp_text:
        log.debug(f"HWP pyhwp BodyText 사용: {len(pyhwp_text)}자")
        return pyhwp_text, "pyhwp_bodytext"

    prvtext = ""
    try:
        import olefile

        with olefile.OleFileIO(str(path)) as ole:
            if ole.exists("PrvText"):
                raw = ole.openstream("PrvText").read()
                prvtext = raw.decode("utf-16-le", errors="ignore").strip()
    except Exception as exc:
        log.warning(f"HWP PrvText 읽기 실패 ({path.name}): {exc}")

    if not pyhwp_text and not prvtext:
        log.debug(f"HWP 텍스트 추출 결과 없음 ({path.name})")
        return "", "none"

    log.debug(f"HWP PrvText fallback 사용: {len(prvtext)}자")
    return prvtext, "olefile_prvtext"


def _extract_hwpx(path: Path) -> tuple[str, str]:
    """HWPX(ZIP+XML) 에서 텍스트 추출."""
    try:
        texts = []
        with zipfile.ZipFile(path) as z:
            section_files = sorted(
                [n for n in z.namelist() if n.startswith("Contents/section") and n.endswith(".xml")]
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


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _extract_docx_xml_text(raw_xml: bytes) -> str:
    root = ET.fromstring(raw_xml)
    paragraphs: list[str] = []

    for paragraph in root.iter():
        if _xml_local_name(paragraph.tag) != "p":
            continue

        parts: list[str] = []
        for el in paragraph.iter():
            name = _xml_local_name(el.tag)
            if name == "t" and el.text:
                parts.append(el.text)
            elif name == "tab":
                parts.append("\t")
            elif name in {"br", "cr"}:
                parts.append("\n")

        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)

    return "\n".join(paragraphs)


def _extract_docx(path: Path) -> tuple[str, str]:
    """DOCX(ZIP+WordprocessingML) text extraction."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            text_parts: list[str] = []

            xml_files = ["word/document.xml"]
            xml_files.extend(
                sorted(
                    name
                    for name in names
                    if re.fullmatch(r"word/(header|footer)\d+\.xml", name)
                    or name in {"word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}
                )
            )

            for xml_file in xml_files:
                if xml_file not in names:
                    continue
                text = _extract_docx_xml_text(z.read(xml_file)).strip()
                if text:
                    text_parts.append(text)

        return "\n".join(text_parts).strip(), "docx_xml"
    except Exception as exc:
        log.warning(f"DOCX parsing failed ({path.name}): {exc}")
        return "", "docx_xml"


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
    if ext == "docx":
        return _extract_docx(path)
    return "", "none"


async def extract_external_images(body_html: str, article_id: str) -> list[dict]:
    """
    본문 HTML에서 외부 URL <img src="https://..."> 이미지를 다운로드해 저장.
    base64 인라인 이미지는 제외 (extract_inline_images가 처리).
    최대 10개 이미지만 처리한다.
    """
    pattern = r'<img[^>]+src=["\']?(https?://[^"\'>\s]+)["\']?'
    results = []
    urls_seen = set()
    count = 0

    for url in re.findall(pattern, body_html):
        if count >= _MAX_INLINE_IMAGES:
            log.warning(f"  외부 이미지 개수 제한({_MAX_INLINE_IMAGES}개) 초과, 나머지 건너뜀")
            break
        if url in urls_seen:
            continue
        urls_seen.add(url)

        parsed_path = Path(urlparse(url).path)
        raw_ext = parsed_path.suffix[1:].lower() if parsed_path.suffix else "jpg"
        if raw_ext not in ("jpg", "jpeg", "png", "gif", "webp", "svg"):
            raw_ext = "jpg"

        mime_type = "image/svg+xml" if raw_ext == "svg" else "image/" + ("jpeg" if raw_ext == "jpg" else raw_ext)

        filename = _safe_filename(article_id, f"ext_img_{count}.{raw_ext}")
        save_path = FILES_DIR / filename

        ok = await download_file(url, save_path)
        if not ok:
            continue

        file_size = save_path.stat().st_size
        checksum = _compute_checksum(save_path)
        log.info(f"  외부 이미지 저장: {filename} ({file_size:,} bytes)")
        results.append({
            "name": filename,
            "url": url,
            "ext": raw_ext,
            "file_key": filename,
            "local_path": f"files/{filename}",
            "mime_type": mime_type,
            "file_size": file_size,
            "checksum": checksum,
            "extracted_text": "",
            "download_ok": True,
            "parser": "none",
            "parse_ok": False,
        })
        count += 1

    return results


def extract_inline_images(body_html: str, article_id: str) -> list[dict]:
    """
    본문 HTML에서 base64 인라인 이미지를 추출해 /data/files/ 에 저장.
    이미지 전용 공지(텍스트 없이 포스터 이미지만 있는 경우)를 처리하기 위해 사용.
    최대 10개, 개당 10MB 초과 시 건너뜀.
    """
    pattern = r'data:image/(jpeg|png|gif|webp);base64,([A-Za-z0-9+/=]+)'
    matches = re.findall(pattern, body_html)

    if len(matches) > _MAX_INLINE_IMAGES:
        log.warning(
            f"  인라인 이미지 개수 제한 초과: {len(matches)}개 중 {_MAX_INLINE_IMAGES}개만 처리"
        )
    matches = matches[:_MAX_INLINE_IMAGES]

    results = []
    for i, (img_type, b64_data) in enumerate(matches):
        # base64 디코드 전 크기 추정 (패딩 고려)
        padding = b64_data.count("=")
        estimated_size = (len(b64_data) * 3) // 4 - padding
        if estimated_size > _MAX_INLINE_IMAGE_BYTES:
            log.warning(
                f"  인라인 이미지 크기 제한 초과로 건너뜀 "
                f"(inline_img_{i}): 추정 {estimated_size:,} bytes"
            )
            continue

        ext = "jpg" if img_type == "jpeg" else img_type
        filename = _safe_filename(article_id, f"inline_img_{i}.{ext}")
        save_path = FILES_DIR / filename
        try:
            save_path.write_bytes(base64.b64decode(b64_data, validate=True))
            file_size = save_path.stat().st_size
            checksum = _compute_checksum(save_path)
            log.info(f"  인라인 이미지 저장: {filename} ({file_size:,} bytes)")
            results.append({
                "name": filename,
                "url": "",
                "ext": ext,
                "file_key": filename,
                "local_path": f"files/{filename}",
                "mime_type": f"image/{img_type}",
                "file_size": file_size,
                "checksum": checksum,
                "extracted_text": "",
                "download_ok": True,
                "parser": "none",
                "parse_ok": False,
            })
        except Exception as exc:
            log.warning(f"  인라인 이미지 저장 실패 ({filename}): {exc}")
    return results


async def process_attachments(attachments: list[dict], article_id: str) -> list[dict]:
    """
    각 첨부파일을 다운로드하고 텍스트를 추출하여 반환.

    추가되는 필드:
        file_key       : "{article_id}_{safe_filename}"
        local_path     : data/files/ 기준 상대 경로
        mime_type, file_size, checksum
        download_cached: 기존 non-empty 파일 재사용 여부
        extracted_text : 추출된 텍스트
        download_ok    : 다운로드 성공 여부
        parser         : 사용한 파서 이름 (parse_logs 기록용)
        parse_ok       : 텍스트 추출 성공 여부
    """
    results = []
    for att in attachments:
        filename = _safe_filename(article_id, att["name"])
        save_path = FILES_DIR / filename

        download_cached = False
        cached_file_size = _reusable_download_size(save_path)
        if cached_file_size is not None:
            download_ok = True
            download_cached = True
            log.debug(f"다운로드 캐시 사용: {save_path.name} ({cached_file_size:,} bytes)")
        else:
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

        parse_quality = (
            "full"
            if parse_ok and parser in {"pyhwp_bodytext", "pdfplumber", "hwpx_xml", "docx_xml"}
            else "preview"
            if parse_ok and parser == "olefile_prvtext"
            else "none"
        )
        file_key = filename
        checksum = _compute_checksum(save_path) if download_ok else None
        file_size = cached_file_size if download_cached else save_path.stat().st_size if download_ok else None
        mime_type = _get_mime_type(att["name"])

        results.append(
            {
                **att,
                "file_key": file_key,
                "local_path": f"files/{filename}",
                "mime_type": mime_type,
                "file_size": file_size,
                "checksum": checksum,
                "extracted_text": extracted_text,
                "extracted_chars": len(extracted_text),
                "download_ok": download_ok,
                "download_cached": download_cached,
                "parser": parser,
                "parse_quality": parse_quality,
                "parse_ok": parse_ok,
            }
        )

    return results
