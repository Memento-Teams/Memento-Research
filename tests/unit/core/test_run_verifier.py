"""Unit tests for the deterministic Stage 6 run verifier. Fully offline —
the infra querier is injected; no network and no real session key.

run_ids + claimed statuses are supplied as parsed (run_id, status) pairs
(the pipeline reuses its tested _parse_runner_report_runs), so this module
re-implements no run_id regex."""
from __future__ import annotations

from onemancompany.core import run_verifier as rv

_CREDS = ("http://infra.test:9999", "vk_test")


def _q(status_map):
    """status_map: run_id -> payload dict (or None for unreachable)."""
    def q(server_url, session_key, run_id, timeout):
        return status_map.get(run_id, {"_http": 404})
    return q


# --- verdict semantics ---------------------------------------------------

def test_no_runs_is_unverifiable():
    v = rv.verify([], creds=_CREDS)
    assert v.verdict == rv.UNVERIFIABLE and not v.failed


def test_claimed_success_and_infra_success_passes():
    v = rv.verify([("r1", "succeeded")], creds=_CREDS, querier=_q({"r1": {"status": "succeeded"}}))
    assert v.passed


def test_claimed_success_but_infra_failed_gates():
    v = rv.verify([("r1", "succeeded")], creds=_CREDS, querier=_q({"r1": {"status": "failed"}}))
    assert v.failed and "r1" in v.reason


def test_claimed_success_but_infra_not_found_gates():
    v = rv.verify([("ghost", "succeeded")], creds=_CREDS, querier=_q({}))
    assert v.failed


def test_claimed_success_but_infra_still_running_gates():
    v = rv.verify([("r1", "succeeded")], creds=_CREDS, querier=_q({"r1": {"status": "still_running"}}))
    assert v.failed


def test_honest_failure_is_not_gated():
    # The report honestly says the run failed → real negative result, not
    # fabrication → must NOT be rejected by the authenticity gate.
    v = rv.verify([("r1", "failed")], creds=_CREDS, querier=_q({"r1": {"status": "failed"}}))
    assert not v.failed


def test_infra_unreachable_is_unverifiable_not_fail():
    v = rv.verify([("r1", "succeeded")], creds=_CREDS, querier=lambda *a: None)
    assert v.verdict == rv.UNVERIFIABLE and not v.failed


def test_mixed_one_bad_gates():
    v = rv.verify([("ok", "succeeded"), ("bad", "succeeded")], creds=_CREDS,
                  querier=_q({"ok": {"status": "succeeded"}, "bad": {"status": "failed"}}))
    assert v.failed


def test_nested_data_status_parsed():
    v = rv.verify([("r1", "succeeded")], creds=_CREDS,
                  querier=_q({"r1": {"data": {"status": "succeeded"}}}))
    assert v.passed


def test_no_creds_is_unverifiable():
    v = rv.verify([("r1", "succeeded")], creds=None,
                  querier=_q({"r1": {"status": "succeeded"}}))
    # load_infra_creds may find the bundled file; force the no-creds path:
    import onemancompany.core.run_verifier as m
    orig = m.load_infra_creds
    m.load_infra_creds = lambda *a, **k: None
    try:
        v = rv.verify([("r1", "succeeded")], creds=None, querier=_q({"r1": {"status": "succeeded"}}))
        assert v.verdict == rv.UNVERIFIABLE
    finally:
        m.load_infra_creds = orig


# --- metric fidelity (the "are the numbers real?" check) -----------------

def test_metric_match_passes():
    report = 'results: {"accuracy": 0.90, "loss": 0.10}'
    v = rv.verify([("r1", "succeeded")], report, creds=_CREDS,
                  querier=_q({"r1": {"status": "succeeded", "metrics": {"accuracy": 0.91, "loss": 0.099}}}))
    assert v.passed  # within 5% tolerance


def test_metric_mismatch_gates():
    report = 'results: {"accuracy": 0.95}'
    v = rv.verify([("r1", "succeeded")], report, creds=_CREDS,
                  querier=_q({"r1": {"status": "succeeded", "metrics": {"accuracy": 0.50}}}))
    assert v.failed and "accuracy" in v.reason


def test_empty_infra_metrics_skips_metric_check():
    # Real infra often exposes no metrics → cannot compare → must NOT fail a
    # genuinely-succeeded run just because the report has numbers.
    report = 'results: {"accuracy": 0.95}'
    v = rv.verify([("r1", "succeeded")], report, creds=_CREDS,
                  querier=_q({"r1": {"status": "succeeded", "metrics": {}}}))
    assert v.passed


def test_extract_claimed_metrics():
    m = rv.extract_claimed_metrics('foo {"acc": 0.9, "n": 100, "name": "x", "ok": true} bar')
    assert m == {"acc": 0.9, "n": 100.0}  # numbers only, bool excluded


def test_runs_cap(monkeypatch):
    monkeypatch.setattr(rv, "MAX_RUNS", 2)
    seen = []
    def q(s, k, rid, t):
        seen.append(rid)
        return {"status": "succeeded"}
    rv.verify([("a", "succeeded"), ("b", "succeeded"), ("c", "succeeded")], creds=_CREDS, querier=q)
    assert seen == ["a", "b"]  # capped at MAX_RUNS
