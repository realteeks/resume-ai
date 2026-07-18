"""Fetch a job posting from a URL and extract its visible text.

SSRF hardening:
  * scheme restricted to http/https; URLs with embedded credentials rejected;
  * the host must resolve ONLY to public IPs (blocks loopback, private,
    link-local/metadata, reserved, multicast);
  * redirects are NOT auto-followed — each hop is re-validated before we fetch
    it (closes redirect-to-internal bypasses);
  * the response body is streamed and capped to avoid memory blowups.

Residual: a determined attacker controlling low-TTL authoritative DNS could in
theory rebind the hostname to a private IP in the window between our validation
and httpx's connection (classic DNS-rebinding TOCTOU). Fully closing this needs
connecting to a pinned, pre-validated IP while preserving TLS SNI; given this
app's threat model (Google-authenticated users fetching public job posts) we
accept that residual and document it here rather than ship a fragile TLS hack.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

MAX_BYTES = 3_000_000
TIMEOUT = 12.0
MAX_REDIRECTS = 4
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class JobFetchError(Exception):
    pass


def _assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise JobFetchError(f"Could not resolve host: {host}") from e
    if not infos:
        raise JobFetchError(f"Could not resolve host: {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            raise JobFetchError("Refusing to fetch a non-public address.")


def _validate(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise JobFetchError("URL must start with http:// or https://")
    if parsed.username or parsed.password:
        raise JobFetchError("URLs with embedded credentials are not allowed.")
    if not parsed.hostname:
        raise JobFetchError("Invalid URL.")
    _assert_public_host(parsed.hostname)


def fetch_job_text(url: str) -> str:
    """Return cleaned visible text from a job posting URL."""
    url = (url or "").strip()
    _validate(url)
    current = url
    try:
        with httpx.Client(
            follow_redirects=False, timeout=TIMEOUT,
            headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"},
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            break
                        current = str(httpx.URL(resp.url).join(loc))
                        _validate(current)  # re-validate before following
                        continue
                    resp.raise_for_status()
                    ctype = resp.headers.get("content-type", "")
                    if "html" not in ctype and "text" not in ctype:
                        raise JobFetchError(f"Unsupported content type: {ctype or 'unknown'}")
                    chunks, total = [], 0
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= MAX_BYTES:
                            break
                    return html_to_text(b"".join(chunks)[:MAX_BYTES])
            raise JobFetchError("Too many redirects.")
    except JobFetchError:
        raise
    except httpx.HTTPStatusError as e:
        raise JobFetchError(f"The site returned {e.response.status_code}.")
    except httpx.HTTPError as e:
        raise JobFetchError(f"Could not fetch the page ({type(e).__name__}).")


def html_to_text(raw: bytes) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
