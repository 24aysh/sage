"""Public URL, domain, and untrusted-content normalization."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from html.parser import HTMLParser
from urllib.parse import urlsplit

_SAFE_DOMAIN = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PROMPT_LIKE_LINE = re.compile(
    r"(?i)^\s*(?:system|assistant|developer)\s*(?:message|instruction)?\s*:"
)
_SECRETISH = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password)"
    r"\s*[:=]\s*[^\s,;]+"
)


def validate_public_result_url(value: str) -> str:
    """Validate a public search-result URL without a model-chosen fetch."""

    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Research result must use public HTTPS.")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Research result URL has an invalid port.") from None
    if parsed.username or parsed.password or port not in {None, 443}:
        raise ValueError("Research result URL contains forbidden authority data.")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local research result URLs are forbidden.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if not _SAFE_DOMAIN.fullmatch(hostname):
            raise ValueError("Research result hostname is invalid.") from None
    else:
        if not address.is_global:
            raise ValueError("Non-public research result addresses are forbidden.")
    return parsed._replace(query="", fragment="").geturl()


def normalize_external_text(value: str, max_chars: int) -> str:
    """Convert external HTML or text to bounded inert model context."""

    if not value or max_chars < 1:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        candidate = parser.text if parser.saw_markup else value
    except Exception:
        candidate = value
    candidate = _CONTROL_CHARACTERS.sub("", candidate).replace("\r", "")
    candidate = _SECRETISH.sub(r"\1=[redacted]", candidate)
    lines: list[str] = []
    blank = False
    for raw_line in candidate.splitlines():
        line = " ".join(raw_line.split())
        if _PROMPT_LIKE_LINE.match(line):
            line = f"[external text] {line}"
        if not line:
            if not blank:
                lines.append("")
            blank = True
            continue
        blank = False
        if len(lines) >= 2 and line == lines[-1] == lines[-2]:
            continue
        lines.append(line)
    normalized = "\n".join(lines).strip()
    if len(normalized) <= max_chars:
        return normalized
    marker = "\n... [external content truncated]"
    return normalized[: max(0, max_chars - len(marker))] + marker


def normalize_domains(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    normalized: list[str] = []
    for value in values:
        domain = value.strip().casefold().rstrip(".")
        if not domain or not _SAFE_DOMAIN.fullmatch(domain):
            raise ValueError("Research domains must be valid hostnames.")
        try:
            address = ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError("Research domains must be public hostnames.")
        if domain not in normalized:
            normalized.append(domain)
    return tuple(normalized)


def domain_allowed(hostname: str, allowed: Sequence[str]) -> bool:
    normalized = hostname.casefold().rstrip(".")
    return any(
        normalized == domain or normalized.endswith(f".{domain}")
        for domain in allowed
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0
        self.saw_markup = False

    @property
    def text(self) -> str:
        return " ".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        self.saw_markup = True
        if tag in {"script", "style", "form", "svg", "noscript"}:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and tag in {
            "p", "div", "br", "li", "h1", "h2", "h3", "pre", "code"
        }:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "form", "svg", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and tag in {
            "p", "div", "li", "h1", "h2", "h3", "pre", "code"
        }:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(data)
