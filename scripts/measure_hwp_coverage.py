from __future__ import annotations

import json
import shutil
import statistics
import subprocess
from collections import Counter
from pathlib import Path

try:
    from hwp5.xmlmodel import Hwp5File
except Exception:  # pragma: no cover
    Hwp5File = None


def para_text_from_chunks(chunks: list) -> str:
    parts: list[str] = []
    for _rng, data in chunks:
        if isinstance(data, str):
            parts.append(data)
        elif isinstance(data, dict) and data.get("code") == 13:
            parts.append("\n")
    return "".join(parts).strip("\n")


def bodytext_text(path: Path) -> str:
    if Hwp5File is None:
        return ""
    hw = Hwp5File(str(path))
    try:
        parts: list[str] = []
        for section in hw.bodytext.sections:
            for model in section.models():
                if model.get("tagname") != "HWPTAG_PARA_TEXT":
                    continue
                text = para_text_from_chunks(model.get("content", {}).get("chunks", [])).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)
    finally:
        close = getattr(hw, "close", None)
        if callable(close):
            close()


def prvtext_text(path: Path) -> str:
    try:
        import olefile

        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("PrvText"):
                return ""
            raw = ole.openstream("PrvText").read()
            return raw.decode("utf-16-le", errors="ignore").strip()
    except Exception:
        return ""


def hwp5txt_text(path: Path) -> str:
    executable = shutil.which("hwp5txt")
    if not executable:
        return ""
    try:
        proc = subprocess.run(
            [executable, str(path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:
        return ""
    return (proc.stdout or "").strip()


def pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 2)


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "avg": None, "median": None, "p95": None, "max": None}
    sorted_values = sorted(values)
    p95_index = min(len(sorted_values) - 1, int(len(sorted_values) * 0.95))
    return {
        "min": round(min(values), 2),
        "avg": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "p95": round(sorted_values[p95_index], 2),
        "max": round(max(values), 2),
    }


def resolve_local_path(root: Path, local_path: str) -> Path:
    rel = local_path.replace("\\", "/")
    if rel.startswith("files/"):
        return root / rel
    path = Path(rel)
    if path.is_absolute():
        return path
    return root / "files" / path.name


def main() -> None:
    root = Path("/data") if Path("/data/raw").exists() else Path("data")
    raw_dir = root / "raw"

    rows: list[dict] = []
    errors: list[dict] = []

    for raw_path in sorted(raw_dir.glob("*.json")):
        try:
            data = json.loads(raw_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(
                {"notice": raw_path.stem, "name": raw_path.name, "error": f"json_error: {exc}"}
            )
            continue
        notice_id = data.get("id") or raw_path.stem
        for attachment in data.get("attachments") or []:
            if (attachment.get("ext") or "").lower() != "hwp":
                continue
            local_path = attachment.get("local_path") or ""
            path = resolve_local_path(root, local_path)
            if not path.exists():
                errors.append(
                    {"notice": notice_id, "name": attachment.get("name"), "error": "missing_file"}
                )
                continue
            try:
                body = bodytext_text(path)
                prv = prvtext_text(path)
                cli = hwp5txt_text(path)
            except Exception as exc:
                errors.append(
                    {"notice": notice_id, "name": attachment.get("name"), "error": repr(exc)}
                )
                continue
            stored = attachment.get("extracted_text") or ""
            body_chars = len(body)
            stored_chars = len(stored)
            prv_chars = len(prv)
            cli_chars = len(cli)
            rows.append(
                {
                    "notice": notice_id,
                    "name": attachment.get("name") or path.name,
                    "parser": attachment.get("parser"),
                    "stored_chars": stored_chars,
                    "bodytext_chars": body_chars,
                    "prvtext_chars": prv_chars,
                    "hwp5txt_chars": cli_chars,
                    "stored_vs_body_pct": pct(stored_chars, body_chars),
                    "prv_vs_body_pct": pct(prv_chars, body_chars),
                    "stored_vs_hwp5txt_pct": pct(stored_chars, cli_chars),
                    "stored_minus_body": stored_chars - body_chars,
                    "stored_minus_hwp5txt": stored_chars - cli_chars,
                    "body_minus_prv": body_chars - prv_chars,
                    "exact_body_match": stored == body,
                }
            )

    ratios = [row["stored_vs_body_pct"] for row in rows if row["stored_vs_body_pct"] is not None]
    prv_ratios = [row["prv_vs_body_pct"] for row in rows if row["prv_vs_body_pct"] is not None]
    cli_ratios = [
        row["stored_vs_hwp5txt_pct"] for row in rows if row["stored_vs_hwp5txt_pct"] is not None
    ]
    shortfalls = [row["bodytext_chars"] - row["stored_chars"] for row in rows]
    summary = {
        "root": str(root),
        "hwp_count": len(rows),
        "errors": errors,
        "parser_counts": dict(Counter(row["parser"] for row in rows)),
        "exact_body_matches": sum(1 for row in rows if row["exact_body_match"]),
        "stored_vs_body_pct": quantiles(ratios),
        "prv_vs_body_pct": quantiles(prv_ratios),
        "stored_vs_hwp5txt_pct": quantiles(cli_ratios),
        "total_stored_chars": sum(row["stored_chars"] for row in rows),
        "total_bodytext_chars": sum(row["bodytext_chars"] for row in rows),
        "total_prvtext_chars": sum(row["prvtext_chars"] for row in rows),
        "total_hwp5txt_chars": sum(row["hwp5txt_chars"] for row in rows),
        "total_stored_vs_body_pct": pct(
            sum(row["stored_chars"] for row in rows),
            sum(row["bodytext_chars"] for row in rows),
        ),
        "total_prv_vs_body_pct": pct(
            sum(row["prvtext_chars"] for row in rows),
            sum(row["bodytext_chars"] for row in rows),
        ),
        "total_stored_vs_hwp5txt_pct": pct(
            sum(row["stored_chars"] for row in rows),
            sum(row["hwp5txt_chars"] for row in rows),
        ),
        "shortfall_chars": {
            "min": min(shortfalls) if shortfalls else None,
            "max": max(shortfalls) if shortfalls else None,
            "total": sum(shortfalls),
        },
    }
    worst_by_stored = sorted(
        rows,
        key=lambda row: (
            row["stored_vs_body_pct"] if row["stored_vs_body_pct"] is not None else -1,
            -row["bodytext_chars"],
        ),
    )[:10]
    biggest_prv_gap = sorted(rows, key=lambda row: row["body_minus_prv"], reverse=True)[:10]

    print(
        json.dumps(
            {
                "summary": summary,
                "worst_by_stored_vs_body": worst_by_stored,
                "biggest_preview_gaps": biggest_prv_gap,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
