"""Submit raw notice payloads to the deployed backend importer."""

import json
import logging
from pathlib import Path

import httpx

from .config import BACKEND_IMPORT_URL, IMPORTER_API_TOKEN

log = logging.getLogger("campost.backend_importer")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if IMPORTER_API_TOKEN:
        headers["X-Importer-Token"] = IMPORTER_API_TOKEN
    return headers


async def submit_raw_file(path: Path) -> bool:
    """POST a saved raw JSON file to the backend importer when configured."""
    if not BACKEND_IMPORT_URL:
        return False

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Backend import skipped; failed to read raw JSON %s: %s", path.name, exc)
        return False

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(BACKEND_IMPORT_URL, json=payload, headers=_headers())
            response.raise_for_status()
    except Exception as exc:
        log.warning("Backend import failed (%s): %s", path.name, exc)
        return False

    log.info("Backend import submitted: %s", path.name)
    return True
