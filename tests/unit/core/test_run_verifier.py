"""Unit tests for the deterministic Stage 6 run verifier. Fully offline —
the infra querier is injected, no network and no real session key."""
from __future__ import annotations

from onemancompany.core import run_verifier as rv

_CREDS = ("http://infra.test:9999", "vk_test")


def _querier(status_map):
    """status_map: run_id -> payload (dict) or None (unreachable)."""
    def q(server_url, session_key, run_id, timeout):
        return status_map.get(run_id, {"_http": 404})
    return q


def test_extract_run_ids_dedupes_and_skips_placeholders():
    text = "- run_id: abc123\nstatus: succeeded\n- run_id: abc123\n- run_id: <RID>\n- run_id: none"
    assert rv.extract_run_ids(text) == ["abc123"]


def test_no_run_id_is_unverifiable_not_fail():
    v = rv.verify_text("the experiment ran locally, no remote run", creds=_CREDS)
    assert v.verdict == rv.UNVERIFIABLE
    assert not v.failed


def test_succeeded_run_passes():
    v = rv.verify_text("- run_id: r1", creds=_CREDS,
                       querier=_querier({"r1": {"status": "succeeded"}}))
    assert v.passed


def test_failed_run_gates():
    v = rv.verify_text("- run_id: r1", creds=_CREDS,
                       querier=_querier({"r1": {"status": "failed"}}))
    assert v.failed
    assert "r1" in v.reason


def test_missing_run_id_on_infra_gates():
    # 404 from infra = the claimed run_id does not exist = fabrication.
    v = rv.verify_text("- run_id: ghost", creds=_CREDS, querier=_querier({}))
    assert v.failed


def test_still_running_gates():
    v = rv.verify_text("- run_id: r1", creds=_CREDS,
                       querier=_querier({"r1": {"status": "still_running"}}))
    assert v.failed


def test_infra_unreachable_is_unverifiable_not_fail():
    # querier returns None for transport error → fail-safe, do not block.
    v = rv.verify_text("- run_id: r1", creds=_CREDS,
                       querier=lambda *a: None)
    assert v.verdict == rv.UNVERIFIABLE
    assert not v.failed


def test_mixed_one_failed_gates():
    v = rv.verify_text("- run_id: ok1\n- run_id: bad2", creds=_CREDS,
                       querier=_querier({"ok1": {"status": "succeeded"},
                                         "bad2": {"status": "failed"}}))
    assert v.failed


def test_nested_data_status_parsed():
    v = rv.verify_text("- run_id: r1", creds=_CREDS,
                       querier=_querier({"r1": {"data": {"status": "succeeded"}}}))
    assert v.passed
