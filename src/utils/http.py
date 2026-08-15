"""HTTP helper dengan timeout, retry, dan exponential backoff."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 2
USER_AGENT = "btc-market-brief/1.0 (+https://github.com/tigorworks/crypto-news)"

_session: Optional[requests.Session] = None


def session() -> requests.Session:
    """Session global supaya koneksi dipakai ulang."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT})
    return _session


class HttpError(Exception):
    """Kegagalan HTTP setelah semua retry habis."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def request(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    retry_on_status: tuple = (429, 500, 502, 503, 504),
) -> requests.Response:
    """Kirim request dengan retry.

    Status yang jelas-jelas permanen (401, 403, 451, 404) tidak di-retry —
    percuma, dan kita butuh status code-nya untuk memutuskan fallback.
    """
    last_error: Optional[Exception] = None
    attempt = 0

    while attempt <= retries:
        try:
            resp = session().request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code in retry_on_status and attempt < retries:
                wait = 2 ** (attempt + 1)
                log.warning(
                    "HTTP %s dari %s, retry dalam %ss", resp.status_code, url, wait
                )
                time.sleep(wait)
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise HttpError(
                    f"HTTP {resp.status_code} dari {url}: {resp.text[:200]}",
                    status_code=resp.status_code,
                )
            return resp
        except HttpError:
            raise
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait = 2 ** (attempt + 1)
            log.warning("Gagal request %s (%s), retry dalam %ss", url, exc, wait)
            time.sleep(wait)
            attempt += 1

    raise HttpError(f"Gagal request {url} setelah {retries + 1} percobaan: {last_error}")


def get_json(url: str, **kwargs) -> Any:
    return request("GET", url, **kwargs).json()


def post_json(url: str, json_body: Any, **kwargs) -> Any:
    return request("POST", url, json_body=json_body, **kwargs).json()


def get_text(url: str, **kwargs) -> str:
    return request("GET", url, **kwargs).text
