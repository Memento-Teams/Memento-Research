"""Tests for the parameterised Stage-6 deterministic submit (#156).

Covers receipt parsing (entrypoint + code/remote paths, nothing hardcoded),
run_id extraction, and the submit wrapper's graceful degradation when creds /
scripts are missing (the engine must HOLD, not crash).
"""
from __future__ import annotations

import pytest

from onemancompany.agents import stage6_infra as s6

RECEIPT = """# Stage 6 Implementation Receipt

## 4. Runnable Entrypoint

### Smoke (runner runs this FIRST, <=5 min)
cd omc/408898030d34/iter_001 && python experiment.py --smoke --seed 42 --output_dir outputs

What `--smoke` shrinks: 5 test problems + 10 validation problems.

### Full (runner runs this only if smoke succeeded)
cd omc/408898030d34/iter_001 && python experiment.py --seed 42 --output_dir outputs

- **Local file**: `/tmp/stage6_impl/408898030d34/experiment.py`
- **Remote path**: `omc/408898030d34/iter_001/experiment.py`
"""


def test_parse_receipt_extracts_entrypoints_and_paths():
    r = s6.parse_receipt(RECEIPT, project_id="408898030d34")
    assert r.ok
    assert r.smoke_cmd == "cd omc/408898030d34/iter_001 && python experiment.py --smoke --seed 42 --output_dir outputs"
    assert r.full_cmd == "cd omc/408898030d34/iter_001 && python experiment.py --seed 42 --output_dir outputs"
    assert r.code_dir == "/tmp/stage6_impl/408898030d34"
    assert r.remote_dest == "omc/408898030d34/iter_001"


def test_parse_receipt_remote_dest_falls_back_to_project_id():
    r = s6.parse_receipt("### Smoke\npython run.py --smoke\n", project_id="abc123")
    assert r.smoke_cmd == "python run.py --smoke"
    assert r.remote_dest == "omc/abc123/iter_001"


def test_parse_receipt_no_headers_uses_first_cmd():
    r = s6.parse_receipt("To run the experiment:\npython main.py --foo bar\n")
    assert r.smoke_cmd == "python main.py --foo bar"


def test_parse_receipt_empty_is_not_ok():
    assert s6.parse_receipt("nothing runnable here").ok is False


def test_extract_run_id_json_and_text():
    assert s6._extract_run_id('{"success": true, "run_id": "run_abc12345"}') == "run_abc12345"
    assert s6._extract_run_id("...submitted run_deadbeef99 to infra...") == "run_deadbeef99"
    assert s6._extract_run_id("no id here") == ""


def test_submit_holds_without_creds(monkeypatch):
    monkeypatch.delenv("INFRA_SERVER_URL", raising=False)
    monkeypatch.delenv("INFRA_SESSION_KEY", raising=False)
    r = s6.submit(s6.parse_receipt(RECEIPT, project_id="p"), {"x": "y"}, "/tmp/base.conf.json")
    assert r.ok is False and "INFRA_SERVER_URL" in r.error


def test_submit_holds_without_scripts(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://infra")
    monkeypatch.setenv("INFRA_SESSION_KEY", "k")
    r = s6.submit(s6.parse_receipt(RECEIPT, project_id="p"), {}, "/tmp/base.conf.json")
    assert r.ok is False and "scripts" in r.error


def test_submit_success_pushes_then_submits(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://infra")
    monkeypatch.setenv("INFRA_SESSION_KEY", "k")
    calls = []

    def fake_run(args, env, timeout=320.0):
        calls.append(args)
        if args[1].endswith("fast_push_code.sh"):
            return 0, '{"success": true}'
        return 0, '{"success": true, "run_id": "run_11223344"}'

    monkeypatch.setattr(s6, "_run", fake_run)
    scripts = {"fast_push_code.sh": "/s/fast_push_code.sh", "fast_submit.sh": "/s/fast_submit.sh",
               "fast_query_exp_status.sh": "/s/fast_query_exp_status.sh"}
    r = s6.submit(s6.parse_receipt(RECEIPT, project_id="p"), scripts, "/tmp/base.conf.json", kind="smoke")
    assert r.ok and r.run_id == "run_11223344" and r.kind == "smoke"
    # pushed code first, then submitted
    assert any("fast_push_code.sh" in a[1] for a in calls)
    assert any("fast_submit.sh" in a[1] for a in calls)
    # the submitted command is the receipt's smoke cmd (parameterised, not hardcoded)
    submit_call = [a for a in calls if "fast_submit.sh" in a[1]][0]
    assert "--smoke" in " ".join(submit_call)


def test_submit_full_uses_full_cmd(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://infra")
    monkeypatch.setenv("INFRA_SESSION_KEY", "k")
    monkeypatch.setenv("STAGE6_SKIP_PUSH", "push")  # skip push for this check

    def fake_run(args, env, timeout=320.0):
        return 0, '{"run_id": "run_full9999"}'

    monkeypatch.setattr(s6, "_run", fake_run)
    scripts = {"fast_push_code.sh": "a", "fast_submit.sh": "b", "fast_query_exp_status.sh": "c"}
    r = s6.submit(s6.parse_receipt(RECEIPT, project_id="p"), scripts, "/tmp/base.conf.json", kind="full")
    assert r.ok and r.run_id == "run_full9999" and r.kind == "full"
