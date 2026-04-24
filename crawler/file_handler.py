"""
CamPost Crawler — 첨부파일 다운로드 및 텍스트 추출
PDF  : pdfplumber
HWP  : olefile (PrvText 스트림)
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
from xml.etree import ElementTree as ET

import httpx

from .config import EXTRACTABLE_EXTS, FILES_DIR, USER_AGENT

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
    pyhwp(hwp5)로 HWP BodyText 전체 추출 — 표 구조 포함.

    표는 LIST_HEADER(row/col)로 셀을 특정하고 '헤더 | 셀1 | 셀2' 형식으로 변환.
    pyhwp 미설치 또는 파싱 실패 시 빈 문자열 반환.
    """
    try:
        from hwp5.xmlmodel import Hwp5File
    except ImportError:
        return ""

    try:
        hw = Hwp5File(str(path))
        sec = list(hw.bodytext.sections)[0]
        models = list(sec.models())
    except Exception:
        return ""

    result_parts: list[str] = []
    in_table = False
    current_cell: tuple[int, int] | None = None
    cell_data: dict[tuple[int, int], list[str]] = {}
    max_row = max_col = 0

    def flush_table() -> str:
        if not cell_data:
            return ""
        # pyhwp는 colspan/rowspan으로 가려진 ghost 셀을 저장하지 않는다.
        # 실제 LIST_HEADER 엔트리가 있는 셀만 row별로 모아 출력하면
        # 빈 "|  |" 가 생기지 않는다.
        from collections import defaultdict
        rows_dict: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for (r, c), lines in cell_data.items():
            text = " / ".join(l for l in lines if l.strip())
            rows_dict[r].append((c, text))

        out_lines = []
        for i, r in enumerate(sorted(rows_dict)):
            cells = [text for _, text in sorted(rows_dict[r])]
            row_text = " | ".join(cells)
            out_lines.append(row_text)
            if i == 0:
                out_lines.append("-" * max(40, len(row_text)))
        return "\n".join(out_lines)

    for m in models:
        tagname = m.get("tagname", "")
        level   = m.get("level", 0)
        content = m.get("content", {})
        chunks  = content.get("chunks", [])

        if tagname == "HWPTAG_CTRL_HEADER" and content.get("chid") == "tbl ":
            in_table = True
            current_cell = None
            cell_data = {}
            max_row = max_col = 0
            continue

        if in_table and tagname == "HWPTAG_TABLE":
            continue

        if in_table:
            if level <= 1 and tagname in ("HWPTAG_PARA_HEADER", "HWPTAG_CTRL_HEADER"):
                table_text = flush_table()
                if table_text:
                    result_parts.append("[표]\n" + table_text)
                in_table = False
                current_cell = None
                cell_data = {}
            elif tagname == "HWPTAG_LIST_HEADER" and level == 2:
                row, col = content.get("row", 0), content.get("col", 0)
                current_cell = (row, col)
                max_row = max(max_row, row)
                max_col = max(max_col, col)
                cell_data.setdefault(current_cell, [])
                continue
            elif tagname == "HWPTAG_PARA_TEXT" and level == 3 and current_cell is not None:
                text = _get_para_text_from_chunks(chunks).strip()
                if text:
                    cell_data[current_cell].append(text)
                continue
            else:
                continue

        if tagname == "HWPTAG_PARA_TEXT" and level == 1:
            text = _get_para_text_from_chunks(chunks).strip()
            if text:
                result_parts.append(text)

    if in_table:
        table_text = flush_table()
        if table_text:
            result_parts.append("[표]\n" + table_text)

    return "\n".join(result_parts)


def _extract_hwp(path: Path) -> tuple[str, str]:
    """
    HWP 텍스트 추출.

    1차: pyhwp로 BodyText 전체 파싱 (표 구조 포함).
    2차: olefile PrvText (pyhwp 실패 또는 결과가 PrvText보다 짧을 때 보완).
    두 결과 중 긴 쪽을 반환.
    """
    pyhwp_text = _extract_hwp_pyhwp(path)

    prvtext = ""
    try:
        import olefile

        ole = olefile.OleFileIO(str(path))
        if ole.exists("PrvText"):
            raw = ole.openstream("PrvText").read()
            prvtext = raw.decode("utf-16-le", errors="ignore").strip()
    except Exception as exc:
        log.warning(f"HWP PrvText 읽기 실패 ({path.name}): {exc}")

    if len(pyhwp_text) >= len(prvtext):
        log.debug(f"HWP pyhwp 사용: {len(pyhwp_text)}자 (PrvText={len(prvtext)}자)")
        return pyhwp_text, "pyhwp"
    else:
        log.debug(f"HWP PrvText 사용: {len(prvtext)}자 (pyhwp={len(pyhwp_text)}자)")
        return prvtext, "olefile"


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


async def extract_external_images(body_html: str, article_id: str) -> list[dict]:
    """
    본문 HTML에서 외부 URL <img src="https://..."> 이미지를 다운로드해 저장.
    base64 인라인 이미지는 제외 (extract_inline_images가 처리).
    """
    pattern = r'<img[^>]+src=["\']?(https?://[^"\'>\s]+)["\']?'
    results = []
    urls_seen = set()
    for i, url in enumerate(re.findall(pattern, body_html)):
        if url in urls_seen:
            continue
        urls_seen.add(url)

        # URL에서 확장자 추출, 없으면 jpg 기본값
        url_path = url.split("?")[0].split("#")[0]
        ext = url_path.rsplit(".", 1)[-1].lower() if "." in url_path.rsplit("/", 1)[-1] else "jpg"
        if ext not in ("jpg", "jpeg", "png", "gif", "webp", "svg"):
            ext = "jpg"
        mime_ext = "jpeg" if ext == "jpg" else ext

        filename = _safe_filename(article_id, f"ext_img_{i}.{ext}")
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
            "ext": ext,
            "file_key": filename,
            "local_path": f"files/{filename}",
            "mime_type": f"image/{mime_ext}",
            "file_size": file_size,
            "checksum": checksum,
            "extracted_text": "",
            "download_ok": True,
            "parser": "none",
            "parse_ok": False,
        })
    return results


def extract_inline_images(body_html: str, article_id: str) -> list[dict]:
    """
    본문 HTML에서 base64 인라인 이미지를 추출해 /data/files/ 에 저장.
    이미지 전용 공지(텍스트 없이 포스터 이미지만 있는 경우)를 처리하기 위해 사용.
    """
    pattern = r'data:image/(jpeg|png|gif|webp);base64,([A-Za-z0-9+/=]+)'
    results = []
    for i, (img_type, b64_data) in enumerate(re.findall(pattern, body_html)):
        ext = "jpg" if img_type == "jpeg" else img_type
        filename = _safe_filename(article_id, f"inline_img_{i}.{ext}")
        save_path = FILES_DIR / filename
        try:
            save_path.write_bytes(base64.b64decode(b64_data))
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

        results.append(
            {
                **att,
                "file_key": file_key,
                "local_path": f"files/{filename}",
                "mime_type": mime_type,
                "file_size": file_size,
                "checksum": checksum,
                "extracted_text": extracted_text,
                "download_ok": download_ok,
                "parser": parser,
                "parse_ok": parse_ok,
            }
        )

    return results
