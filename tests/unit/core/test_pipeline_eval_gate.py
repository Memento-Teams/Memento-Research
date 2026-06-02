"""Tests for the deterministic eval wiring in the pipeline engine:
the Stage 6 run-authenticity GATE and the Stage 2 advisory citation audit.
These exercise the real engine methods (not a wholesale mock of dispatch)."""
from __future__ import annotations

import pytest

from onemancompany.core import pipeline_engine as pe
from onemancompany.core import run_verifier as rv
from onemancompany.core import citation_verifier as cv


@pytest.fixture(autouse=True)
def clear_registry():
    pe._active_pipelines.clear()
    yield
    pe._active_pipelines.clear()


STAGE6 = {"id": 6, "skill": "experimentalist", "name": "Auto Experiment"}
STAGE2 = {"id": 2, "skill": "literature_surveyor", "name": "Literature Survey"}


def _engine(tmp_path):
    return pe.PipelineEngine("p1", str(tmp_path), "topic")


# --- Stage 6 deterministic run-authenticity gate ------------------------

def test_run_gate_rejects_on_failed_verdict(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    monkeypatch.setattr(rv, "verify",lambda *a, **k: rv.RunVerdict(rv.FAIL, reason="ghost not found"))
    calls = []
    monkeypatch.setattr(eng, "_reject_or_hold", lambda *a, **k: calls.append(k))
    handled = eng._deterministic_run_gate(STAGE6, "- run_id: ghost")
    assert handled is True, "a failed infra verdict must REJECT (caller stops)"
    assert calls and calls[0]["outcome_prefix"] == "run_verify_reject"


def test_run_gate_passes_through_on_unverifiable(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    monkeypatch.setattr(rv, "verify",lambda *a, **k: rv.RunVerdict(rv.UNVERIFIABLE, reason="no infra"))
    monkeypatch.setattr(eng, "_reject_or_hold", lambda *a, **k: pytest.fail("must not reject when unverifiable"))
    assert eng._deterministic_run_gate(STAGE6, "ran locally") is False


def test_run_gate_passes_through_on_pass(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    monkeypatch.setattr(rv, "verify",lambda *a, **k: rv.RunVerdict(rv.PASS, reason="ok"))
    monkeypatch.setattr(eng, "_reject_or_hold", lambda *a, **k: pytest.fail("must not reject on pass"))
    assert eng._deterministic_run_gate(STAGE6, "- run_id: r1") is False


def test_run_gate_never_crashes_pipeline(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    def boom(*a, **k):
        raise RuntimeError("infra client exploded")
    monkeypatch.setattr(rv, "verify",boom)
    # An error in the gate must degrade to "proceed", never raise.
    assert eng._deterministic_run_gate(STAGE6, "x") is False


# --- _reject_or_hold shared path ----------------------------------------

def test_reject_or_hold_retries_then_dispatches_producer(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    eng.state["retries"] = 0
    monkeypatch.setattr(eng, "_record_stage_memory", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_emit_stage_event", lambda *a, **k: None)
    dispatched = {}
    monkeypatch.setattr(eng, "_dispatch_producer", lambda feedback="": dispatched.update(feedback=feedback))
    eng._reject_or_hold(STAGE6, feedback="bad run", confidence=None,
                        producer_result="r", outcome_prefix="run_verify_reject")
    assert eng.state["retries"] == 1
    assert dispatched["feedback"] == "bad run"


def test_reject_or_hold_holds_gate_when_exhausted(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    eng.state["retries"] = pe.MAX_RETRIES
    monkeypatch.setattr(eng, "_record_stage_memory", lambda *a, **k: None)
    held = {}
    monkeypatch.setattr(eng, "_emit_gate_event", lambda sid, conf, exhausted=False: held.update(exhausted=exhausted))
    monkeypatch.setattr(eng, "_dispatch_producer", lambda *a, **k: pytest.fail("must not retry past MAX_RETRIES"))
    eng._reject_or_hold(STAGE6, feedback="bad", confidence=None,
                        producer_result="r", outcome_prefix="run_verify_reject")
    assert eng.state["phase"] == "gate"
    assert held["exhausted"] is True


# --- Stage 2 advisory citation audit ------------------------------------

def test_advisory_citation_writes_report(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    rep = cv.CitationReport(checks=[
        cv.CitationCheck("2399.99999", "arxiv", cv.FABRICATED, "no such id"),
        cv.CitationCheck("2301.12345", "arxiv", cv.VERIFIED, "ok"),
    ])
    monkeypatch.setattr(cv, "verify_text", lambda *a, **k: rep)
    t = eng._advisory_citation_check(STAGE2, "survey citing 2399.99999 and 2301.12345")
    t.join(timeout=5)
    out = tmp_path / "stage2_citation_report.md"
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "2399.99999" in body and "Fabricated" in body


def test_advisory_citation_never_raises(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    monkeypatch.setattr(cv, "verify_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    t = eng._advisory_citation_check(STAGE2, "text")
    t.join(timeout=5)  # thread swallows the error; no report, no crash
    assert not (tmp_path / "stage2_citation_report.md").exists()
