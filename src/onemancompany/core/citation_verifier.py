"""Deterministic citation-authenticity verifier for Stage 2 (Literature Survey).

ZERO LLM. Extracts arXiv IDs and DOIs from the literature deliverable with
regex, then verifies each against the **real** arXiv / Crossref APIs (free, no
auth). An LLM-written survey that invents a plausible-looking arXiv ID is
caught here deterministically — without asking another (hallucination-prone)
LLM to judge it.

Classification per reference:
  - ``verified``      — the identifier resolves to a real record.
  - ``fabricated``    — the API positively reports no such record.
  - ``unverifiable``  — could not check (network error / non-arXiv-non-DOI ref).

Fail-safe: a lookup that cannot be performed is ``unverifiable``, never
``fabricated`` — a network outage must not brand real citations as fake.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable

from loguru import logger

# arXiv IDs: new-style 2301.12345 / 2301.12345v2 (4-digit YYMM . 4-5 digits).
_ARXIV_RE = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
# DOIs: 10.<registrant>/<suffix>. Trim trailing punctuation that hugs prose.
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\")\]<>]+)", re.IGNORECASE)

_ARXIV_API = "http://export.arxiv.org/api/query?id_list={id}"
_CROSSREF_API = "https://api.crossref.org/works/{doi}"

VERIFIED = "verified"
FABRICATED = "fabricated"
UNVERIFIABLE = "unverifiable"


@dataclass
class CitationCheck:
    identifier: str
    kind: str  # "arxiv" | "doi"
    status: str  # VERIFIED | FABRICATED | UNVERIFIABLE
    evidence: str = ""


@dataclass
class CitationReport:
    checks: list[CitationCheck] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        c = {VERIFIED: 0, FABRICATED: 0, UNVERIFIABLE: 0}
        for chk in self.checks:
            c[chk.status] = c.get(chk.status, 0) + 1
        return c

    @property
    def fabricated(self) -> list[CitationCheck]:
        return [c for c in self.checks if c.status == FABRICATED]

    @property
    def total(self) -> int:
        return len(self.checks)


def extract_identifiers(text: str) -> list[tuple[str, str]]:
    """Return a de-duplicated list of ``(kind, identifier)`` found in ``text``.

    DOIs are matched first so an arXiv-style numeric run inside a DOI suffix is
    not double-counted as an arXiv id."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    doi_spans: list[tuple[int, int]] = []
    for m in _DOI_RE.finditer(text):
        doi = m.group(1).rstrip(".,;)")
        doi_spans.append((m.start(), m.end()))
        key = ("doi", doi.lower())
        if key not in seen:
            seen.add(key)
            out.append(("doi", doi))
    for m in _ARXIV_RE.finditer(text):
        if any(s <= m.start() < e for s, e in doi_spans):
            continue  # numeric chunk lives inside a DOI
        aid = m.group(1)
        key = ("arxiv", aid)
        if key not in seen:
            seen.add(key)
            out.append(("arxiv", aid))
    return out


def _http_get(url: str, timeout: float) -> tuple[int, str] | None:
    """GET ``url`` → (status_code, body), or None on transport error."""
    try:
        import httpx

        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "autoresearch-citation-verifier/1.0"})
        return r.status_code, r.text
    except Exception as exc:  # noqa: BLE001 — network is best-effort
        logger.debug("[citation-verify] GET {} failed: {}", url, exc)
        return None


def _check_arxiv(arxiv_id: str, timeout: float, getter: Callable) -> CitationCheck:
    res = getter(_ARXIV_API.format(id=arxiv_id), timeout)
    if res is None:
        return CitationCheck(arxiv_id, "arxiv", UNVERIFIABLE, "arxiv API unreachable")
    code, body = res
    if code != 200 or not body:
        return CitationCheck(arxiv_id, "arxiv", UNVERIFIABLE, f"arxiv HTTP {code}")
    try:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(body)
        entries = root.findall("a:entry", ns)
        # A bad id_list yields a single entry whose <id> points at the API
        # error doc (arxiv.org/api/errors) — treat that as "no such paper".
        for e in entries:
            eid = (e.findtext("a:id", default="", namespaces=ns) or "")
            if "api/errors" in eid:
                return CitationCheck(arxiv_id, "arxiv", FABRICATED, "arxiv: no such id")
            title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
            if title:
                return CitationCheck(arxiv_id, "arxiv", VERIFIED, f"arxiv: {title[:80]}")
        return CitationCheck(arxiv_id, "arxiv", FABRICATED, "arxiv: no entry")
    except ET.ParseError as exc:
        return CitationCheck(arxiv_id, "arxiv", UNVERIFIABLE, f"arxiv parse error: {exc}")


def _check_doi(doi: str, timeout: float, getter: Callable) -> CitationCheck:
    res = getter(_CROSSREF_API.format(doi=doi), timeout)
    if res is None:
        return CitationCheck(doi, "doi", UNVERIFIABLE, "crossref unreachable")
    code, _ = res
    if code == 404:
        return CitationCheck(doi, "doi", FABRICATED, "crossref: DOI not found")
    if code == 200:
        return CitationCheck(doi, "doi", VERIFIED, "crossref: resolved")
    return CitationCheck(doi, "doi", UNVERIFIABLE, f"crossref HTTP {code}")


def verify_text(text: str, *, timeout: float = 8.0, getter: Callable = _http_get) -> CitationReport:
    """Extract identifiers from ``text`` and verify each. ``getter`` is the
    HTTP fetcher — injected so tests run fully offline."""
    report = CitationReport()
    for kind, ident in extract_identifiers(text):
        if kind == "arxiv":
            report.checks.append(_check_arxiv(ident, timeout, getter))
        else:
            report.checks.append(_check_doi(ident, timeout, getter))
    return report


def render_report(report: CitationReport) -> str:
    """Render the citation report as advisory markdown."""
    c = report.counts
    lines = [
        "# Stage 2 Citation Authenticity (deterministic)",
        "",
        f"Checked {report.total} identifier(s): "
        f"{c[VERIFIED]} verified / {c[FABRICATED]} fabricated / {c[UNVERIFIABLE]} unverifiable.",
        "",
        "_Deterministic: arXiv IDs and DOIs are resolved against the real "
        "arXiv / Crossref APIs — no LLM judgment. Advisory only._",
        "",
    ]
    if report.fabricated:
        lines.append("## 🚨 Fabricated (API reports no such record)")
        for chk in report.fabricated:
            lines.append(f"- `{chk.identifier}` ({chk.kind}) — {chk.evidence}")
        lines.append("")
    lines.append("## All checks")
    for chk in report.checks:
        mark = {VERIFIED: "✅", FABRICATED: "🚨", UNVERIFIABLE: "⚠️"}.get(chk.status, "?")
        lines.append(f"- {mark} `{chk.identifier}` ({chk.kind}) — {chk.status}: {chk.evidence}")
    return "\n".join(lines) + "\n"
