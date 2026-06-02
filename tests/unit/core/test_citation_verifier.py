"""Unit tests for the deterministic citation verifier. Fully offline —
the HTTP getter is injected, no network."""
from __future__ import annotations

from onemancompany.core import citation_verifier as cv

_VERIFIED_ATOM = (
    '<?xml version="1.0"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    '<entry><id>http://arxiv.org/abs/2301.12345v1</id>'
    '<title>A Real Paper</title></entry></feed>'
)
_ERROR_ATOM = (
    '<?xml version="1.0"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    '<entry><id>http://arxiv.org/api/errors#incorrect_id</id>'
    '<title>Error</title></entry></feed>'
)


def test_extract_identifiers_arxiv_and_doi():
    text = "See 2301.12345v2 and 10.1145/3292500.3330701 for details."
    ids = cv.extract_identifiers(text)
    assert ("arxiv", "2301.12345") in ids
    assert ("doi", "10.1145/3292500.3330701") in ids


def test_extract_dedupes_and_skips_doi_internal_numbers():
    # The numeric run inside the DOI must not be double-counted as an arXiv id.
    text = "10.1234/2301.12345 cited twice 10.1234/2301.12345"
    ids = cv.extract_identifiers(text)
    arxivs = [i for i in ids if i[0] == "arxiv"]
    dois = [i for i in ids if i[0] == "doi"]
    assert len(dois) == 1
    assert arxivs == []


def _getter(arxiv_map=None, doi_code=200):
    arxiv_map = arxiv_map or {}

    def g(url, timeout):
        if "id_list=" in url:
            aid = url.split("id_list=")[1]
            body = arxiv_map.get(aid)
            return (200, body) if body is not None else (200, _ERROR_ATOM)
        if "crossref" in url:
            return (doi_code, "")
        return None

    return g


def test_verified_arxiv():
    rep = cv.verify_text("paper 2301.12345", getter=_getter({"2301.12345": _VERIFIED_ATOM}))
    assert rep.counts[cv.VERIFIED] == 1
    assert rep.checks[0].status == cv.VERIFIED


def test_fabricated_arxiv_when_api_reports_error():
    rep = cv.verify_text("fake 2399.99999", getter=_getter({"2399.99999": _ERROR_ATOM}))
    assert rep.counts[cv.FABRICATED] == 1
    assert rep.fabricated[0].identifier == "2399.99999"


def test_fabricated_doi_on_404():
    rep = cv.verify_text("ref 10.9999/nope.xyz", getter=_getter(doi_code=404))
    assert rep.counts[cv.FABRICATED] == 1


def test_verified_doi_on_200():
    rep = cv.verify_text("ref 10.1145/3292500.3330701", getter=_getter(doi_code=200))
    assert rep.counts[cv.VERIFIED] == 1


def test_unverifiable_on_network_error():
    rep = cv.verify_text("paper 2301.12345", getter=lambda url, t: None)
    assert rep.counts[cv.UNVERIFIABLE] == 1
    # Fail-safe: a network outage must never brand a citation as fabricated.
    assert rep.counts[cv.FABRICATED] == 0


def test_render_report_lists_fabricated():
    rep = cv.verify_text("fake 2399.99999", getter=_getter({"2399.99999": _ERROR_ATOM}))
    md = cv.render_report(rep)
    assert "Fabricated" in md and "2399.99999" in md
