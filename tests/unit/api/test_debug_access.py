"""Tests for the debug-access layer (full per-run artifact + telemetry exposure)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import onemancompany.api.debug_access as da

# A real, complete run captured under paperloop_artifacts/ (Stage 1..9 success).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = _REPO_ROOT / "paperloop_artifacts" / "965257e794dd-SUCCESS" / "iterations" / "iter_001"
_FIXTURE_YAML = _FIXTURE.parent / "iter_001.yaml"

requires_fixture = pytest.mark.skipif(
    not _FIXTURE.is_dir(), reason="paperloop_artifacts fixture not present"
)


# ── pure helpers ─────────────────────────────────────────────────────────────
@requires_fixture
def test_extract_run_ids_finds_stage6_sandbox_runs():
    state = da._read_pipeline_state(_FIXTURE)
    rids = da.extract_run_ids(state)
    assert "run_76f2d8f840b2" in rids["all"]
    assert "run_3a0ee2e10075" in rids["all"]
    # Both were submitted in Stage 6.
    assert set(rids["by_stage"].get("6", [])) >= {"run_76f2d8f840b2", "run_3a0ee2e10075"}


@requires_fixture
def test_aggregate_tokens_rolls_up_total_and_breakdowns():
    idoc = yaml.safe_load(_FIXTURE_YAML.read_text(encoding="utf-8"))
    tok = da.aggregate_tokens(idoc)
    assert tok["total"]["total"] > 0
    assert tok["total"]["cost_usd"] > 0
    assert tok["by_employee"] and tok["by_employee"][0]["total"] > 0
    # by_employee is sorted descending by total tokens
    totals = [e["total"] for e in tok["by_employee"]]
    assert totals == sorted(totals, reverse=True)
    assert "Kimi-K2.6" in tok["models"]


def test_aggregate_tokens_handles_empty():
    tok = da.aggregate_tokens({})
    assert tok["total"]["total"] == 0
    assert tok["by_employee"] == []


@requires_fixture
def test_categorize_and_file_tree():
    cats = da.categorize_artifacts(_FIXTURE)
    assert cats.get("stage_outputs"), "stage{N}_*.md should be classified"
    assert cats.get("gate_reviews"), "gate_review_stage*.md should be classified"
    # nodes/ internals get their own bucket, not 'other'
    assert "engine_internals" in cats

    tree = da.build_file_tree(_FIXTURE, include_internal=True)
    paths = {f["path"] for f in tree}
    assert "pipeline_state.yaml" in paths
    assert "stage1_topic_refiner.md" in paths
    assert ".DS_Store" not in paths  # OS junk skipped
    # include_internal=False drops the engine nodes/ tree
    ext = da.build_file_tree(_FIXTURE, include_internal=False)
    assert not any(p["path"].startswith("nodes/") for p in ext)


@requires_fixture
def test_build_run_info_shape():
    idoc = yaml.safe_load(_FIXTURE_YAML.read_text(encoding="utf-8"))
    info = da.build_run_info("965257e794dd", "iter_001", _FIXTURE,
                             {"name": "e2e", "status": "active"}, idoc, redact=True)
    for key in ("meta", "config", "pipeline", "tokens", "sandbox", "artifacts", "logs", "download"):
        assert key in info
    assert info["meta"]["project_id"] == "965257e794dd"
    assert info["config"]["topic"]
    assert "run_76f2d8f840b2" in info["sandbox"]["all"]
    assert info["pipeline"]["stages_completed"]
    assert info["logs"]["debug_trace"]["present"] in (True, False)


def test_redact_text_scrubs_keys_and_ips(monkeypatch):
    monkeypatch.setenv("INFRA_SESSION_KEY", "TOPSECRETSESSIONVALUE")
    s = "use sk-" "ABCD1234efgh, session_key=hunter2abc, infra TOPSECRETSESSIONVALUE at 8.208.118.99"
    out = da.redact_text(s, enabled=True)
    assert "sk-" "ABCD1234efgh" not in out
    assert "hunter2abc" not in out
    assert "TOPSECRETSESSIONVALUE" not in out
    assert "8.208.118.99" not in out
    # disabled → unchanged
    assert da.redact_text(s, enabled=False) == s


# ── route smoke tests (resilient to local data availability) ─────────────────
@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from onemancompany.main import app
    return TestClient(app, raise_server_exceptions=False)


def test_runs_route_returns_list(client):
    resp = client.get("/api/debug/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert "runs" in data and isinstance(data["runs"], list)


def test_run_not_found_is_404(client):
    resp = client.get("/api/debug/run/__definitely_not_a_real_project__")
    assert resp.status_code == 404


def test_file_path_traversal_blocked(client):
    runs = client.get("/api/debug/runs").json()["runs"]
    if not runs:
        pytest.skip("no local runs to exercise the file route")
    pid = runs[0]["project_id"]
    resp = client.get(f"/api/debug/run/{pid}/file", params={"path": "../../../../etc/passwd"})
    assert resp.status_code in (403, 404)


# ── hardening regressions ────────────────────────────────────────────────────
def test_is_denied_credential_files():
    for n in (".env", ".env.production", "id_rsa", "server.pem", "site.key",
              "store.p12", ".netrc", "credentials.json", "aws-credentials"):
        assert da._is_denied(n), n
    for n in ("stage1_topic_refiner.md", "pipeline_state.yaml", "main.pdf",
              "plot.png", "paper.tex"):
        assert not da._is_denied(n), n


def test_safe_target_blocks_traversal_and_nullbyte(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "ok.md").write_text("hi", encoding="utf-8")
    assert da._safe_target(root, "ok.md") == (root / "ok.md").resolve()
    assert da._safe_target(root, "../../etc/passwd") is None
    assert da._safe_target(root, "/etc/passwd") is None
    assert da._safe_target(root, "a\x00b") is None
    assert da._safe_target(root, "") is None


def test_safe_target_blocks_symlink_escape(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET", encoding="utf-8")
    try:
        (root / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported on this platform")
    assert da._safe_target(root, "escape.txt") is None


def test_redact_text_covers_tokens_hosts_ipv6(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://10.11.12.13:8080")
    s = ("Authorization: Bearer abcdef1234567890ABCDEFxyz "
         "jwt eyJhbGciOi.eyJzdWIi.sigPART9 "
         "host worker-3.internal "
         "ipv6 2001:db8:abcd:0012::1 "
         "ipv4 8.208.118.99 "
         "infra http://10.11.12.13:8080/api/status")
    out = da.redact_text(s, True)
    assert "abcdef1234567890ABCDEFxyz" not in out
    assert "eyJhbGciOi.eyJzdWIi.sigPART9" not in out
    assert "worker-3.internal" not in out
    assert "2001:db8:abcd:0012::1" not in out
    assert "8.208.118.99" not in out
    assert "10.11.12.13" not in out


def test_aggregate_tokens_survives_malformed_cost():
    doc = {"cost": {
        "actual_cost_usd": "nope",
        "token_usage": {"total": "oops", "input": 7},
        "breakdown": ["bad", 123,
                      {"employee_id": "e1", "input_tokens": "5",
                       "output_tokens": 3, "cost_usd": "x", "model": "m"}],
    }}
    tok = da.aggregate_tokens(doc)
    assert tok["total"]["total"] == 0      # "oops" coerced
    assert tok["total"]["input"] == 7
    assert tok["total"]["cost_usd"] == 0.0  # "nope" coerced
    assert len(tok["by_employee"]) == 1     # non-dict entries skipped
    e = tok["by_employee"][0]
    assert e["input"] == 5 and e["output"] == 3  # "5" coerced


def test_aggregate_tokens_breakdown_not_a_list():
    tok = da.aggregate_tokens({"cost": {"breakdown": "not-a-list"}})
    assert tok["by_employee"] == [] and tok["total"]["calls"] == 0


def test_categorize_figures_before_pdf_and_stage_sort():
    assert da._categorize("figures/plot.pdf") == "figures"
    assert da._categorize("stage8_pdf/main.pdf") == "paper_pdf"
    assert da._categorize("figures/loss.png") == "figures"
    assert sorted(["7", "6", "6_impl", "10"], key=da._stage_sort_key) == \
        ["6", "6_impl", "7", "10"]


def test_effective_redact_gating(monkeypatch):
    class _Req:
        def __init__(self, host):
            self.client = type("C", (), {"host": host})()

    # Raw output is NEVER inferred from client IP — every real caller reaches this
    # box over an ssh -L tunnel and appears as 127.0.0.1, so loopback must NOT be
    # trusted. Raw requires the server-side OMC_DEBUG_ALLOW_RAW=1 opt-in only.
    monkeypatch.delenv("OMC_DEBUG_ALLOW_RAW", raising=False)
    assert da._effective_redact(1, None) is True            # asked to redact
    assert da._effective_redact(0, None) is True            # default → redact
    assert da._effective_redact(0, _Req("203.0.113.9")) is True   # public → redact
    assert da._effective_redact(0, _Req("127.0.0.1")) is True     # loopback → STILL redact
    monkeypatch.setenv("OMC_DEBUG_ALLOW_RAW", "1")
    assert da._effective_redact(0, _Req("127.0.0.1")) is False    # explicit opt-in → raw
    assert da._effective_redact(1, _Req("127.0.0.1")) is True     # redact=1 always wins


# ── route-level hardening (synthetic run dir, no real project needed) ─────────
@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    d = tmp_path / "run"
    d.mkdir()
    (d / "stage1_topic.md").write_text("topic ok; key sk-" "ABCDEF123456 here",
                                       encoding="utf-8")
    (d / ".env").write_text("INFRA_SESSION_KEY=supersecretvalue", encoding="utf-8")
    (d / "evil.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    figs = d / "figures"
    figs.mkdir()
    (figs / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET OUTSIDE", encoding="utf-8")
    linked = True
    try:
        (d / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        linked = False
    monkeypatch.setattr(da, "_project_dir",
                        lambda pid, iteration="": d if pid == "fakerun" else None)
    monkeypatch.delenv("OMC_DEBUG_ALLOW_RAW", raising=False)
    return {"dir": d, "pid": "fakerun", "linked": linked}


def test_route_html_served_as_inert_text(client, fake_run):
    r = client.get(f"/api/debug/run/{fake_run['pid']}/file",
                   params={"path": "evil.html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "default-src 'none'" in r.headers.get("content-security-policy", "")


def test_route_credential_file_denied(client, fake_run):
    r = client.get(f"/api/debug/run/{fake_run['pid']}/file", params={"path": ".env"})
    assert r.status_code == 403


def test_route_redaction_forced_for_public_caller(client, fake_run):
    # redact=0 must be IGNORED for a non-loopback (testclient) caller.
    r = client.get(f"/api/debug/run/{fake_run['pid']}/file",
                   params={"path": "stage1_topic.md", "redact": 0})
    assert r.status_code == 200
    assert "sk-" "ABCDEF123456" not in r.text
    assert "[redacted-key]" in r.text


def test_route_files_tree_excludes_denied_and_symlink(client, fake_run):
    j = client.get(f"/api/debug/run/{fake_run['pid']}/files").json()
    names = {f["name"] for f in j["files"]}
    assert "stage1_topic.md" in names
    assert ".env" not in names
    if fake_run["linked"]:
        assert "escape.txt" not in names
    cat = {f["path"]: f["category"] for f in j["files"]}
    assert cat.get("figures/plot.png") == "figures"


def test_route_symlink_escape_blocked(client, fake_run):
    if not fake_run["linked"]:
        pytest.skip("symlinks unsupported")
    r = client.get(f"/api/debug/run/{fake_run['pid']}/file",
                   params={"path": "escape.txt"})
    assert r.status_code == 403


# ── round-2 regressions: leaks confirmed by the adversarial verify pass ───────
_PEM = ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEF\n"
        "AASCBKcwggSjAgEAAoIBAQ\n-----END PRIVATE KEY-----")


def test_redact_pem_private_key_block():
    out = da.redact_text(f"key:\n{_PEM}\nend", True)
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEF" not in out
    assert "[redacted-pem]" in out


def test_redact_gcp_service_account_private_key():
    sa = '{"type":"service_account","private_key":"' + _PEM.replace("\n", "\\n") + '","client_email":"x@y.iam"}'
    out = da.redact_text(sa, True)
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEF" not in out


def test_redact_provider_tokens():
    assert "AKIA" "IOSFODNN7EXAMPLE" not in da.redact_text("AKIA" "IOSFODNN7EXAMPLE", True)
    assert "AKIA" "IOSFODNN7EXAMPLE" not in da.redact_text("aws_access_key_id: AKIA" "IOSFODNN7EXAMPLE", True)
    assert "xoxb-" "2345678901-2345678901234-AbCdEfGh" not in da.redact_text("xoxb-" "2345678901-2345678901234-AbCdEfGh", True)
    assert "ghp_" "16C7e42F292c6912E7710c838347Ae178B4a" not in da.redact_text("ghp_" "16C7e42F292c6912E7710c838347Ae178B4a", True)
    assert "github_pat_" "11ABCDEFG0123456789_abcdefghij" not in da.redact_text("github_pat_" "11ABCDEFG0123456789_abcdefghij", True)


def test_redact_connection_strings_and_pgpass():
    assert "s3cr3tP4ss" not in da.redact_text("postgres://dbuser:s3cr3tP4ss@db.prod.example.com:5432/appdb", True)
    assert "pass123" not in da.redact_text("mongodb+srv://user:pass123@cluster0.mongodb.net/test", True)
    assert "Tr0ub4dor" not in da.redact_text("Server=db;Uid=sa;Pwd=Tr0ub4dor;", True)
    assert "S3cr3tP@ss" not in da.redact_text("db.host:5432:appdb:appuser:S3cr3tP@ss", True)


def test_redact_high_entropy_gated_by_hint():
    hexv = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"  # 64
    sha1 = "da39a3ee5e6b4b0d3255bfef95601890afd80709"                          # 40
    # high-entropy blobs are scrubbed ONLY on a secret-context line…
    assert hexv not in da.redact_text(f"api_key = {hexv}", True)
    assert sha1 not in da.redact_text(f"secret {sha1}", True)
    # …and PRESERVED in prose (a citation hash / git sha / checksum stays readable)
    assert hexv in da.redact_text(f"checksum {hexv}", True)
    assert sha1 in da.redact_text(f"commit {sha1} merged", True)
    assert "paper_files/abc123def4567890abcdef1234567890ab-Paper" in da.redact_text(
        "see https://x/paper_files/abc123def4567890abcdef1234567890ab-Paper", True)
    # AWS secret next to its AKIA id IS scrubbed (AKIA flags the line)
    aws = "AKIA" "IOSFODNN7EXAMPLE wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in da.redact_text(aws, True)
    # run_<hex> sandbox ids must survive everywhere
    assert "run_76f2d8f840b2" in da.redact_text("submitted run_76f2d8f840b2 ok", True)
    # prose like "basic arithmetic" must not be eaten by the bearer/basic rule
    assert "arithmetic" in da.redact_text("basic arithmetic (+, -)", True)


def test_redact_ipv6_does_not_eat_macs_or_times():
    assert "00:1b:44:11:3a:b7" in da.redact_text("MAC 00:1b:44:11:3a:b7 here", True)
    assert "12:34:56" in da.redact_text("elapsed 12:34:56 done", True)
    # a real IPv6 IS still scrubbed
    assert "2001:db8:abcd:0012::1" not in da.redact_text("addr 2001:db8:abcd:0012::1", True)


def test_denylist_round2_shapes():
    for n in ("kubeconfig", "admin.conf", "config", "config.json",
              "service-account.json", "gcp-service-account.json", "sa-key.json",
              "client_secret.json", "pgpass", ".my.cnf", ".boto", ".s3cfg",
              "rclone.conf", "vault-token", "keystore.jceks", "signing.p8",
              "client.ovpn", "database.yml", "authorized_keys", "known_hosts",
              "identity"):
        assert da._is_denied(n), n


def test_path_is_denied_credential_dirs():
    for rel in (".ssh/server_rsa", ".aws/credentials", ".kube/config",
                "sub/.gnupg/secring.gpg", ".docker/config.json"):
        assert da._path_is_denied(rel), rel
    assert not da._path_is_denied("figures/plot.png")
    assert not da._path_is_denied("stage1_topic.md")


def test_content_private_key_detection(tmp_path):
    keyf = tmp_path / "deploy_key"          # innocuous name, key content
    keyf.write_text(_PEM + "\n", encoding="utf-8")
    assert da._content_has_private_key(keyf)
    plain = tmp_path / "stage1.md"
    plain.write_text("# topic\njust text", encoding="utf-8")
    assert not da._content_has_private_key(plain)


def test_safe_float_rejects_non_finite():
    assert da._safe_float(float("inf")) == 0.0
    assert da._safe_float(float("nan")) == 0.0
    assert da._safe_float("inf") == 0.0
    assert da._safe_float("3.5") == 3.5


def test_tail_lines_bounded(tmp_path):
    f = tmp_path / "big.log"
    f.write_text("\n".join(f"line{i}" for i in range(10_000)), encoding="utf-8")
    tail = da._tail_lines(f, 5)
    assert tail[-1] == "line9999"
    assert len(tail) == 5


@pytest.fixture
def poison_run(tmp_path, monkeypatch):
    """A run whose iteration cost holds inf/nan and which contains an odd-named
    private-key file — exercises the round-2 route fixes."""
    d = tmp_path / "run"
    d.mkdir()
    (d / "stage1.md").write_text("ok", encoding="utf-8")
    (d / "pipeline_state.yaml").write_text(
        "topic: t\ncurrent_stage: 9\nstage_results:\n  '1': done\n", encoding="utf-8")
    (d / "deploy_key").write_text(_PEM + "\n", encoding="utf-8")
    import onemancompany.core.project_archive as pa
    monkeypatch.setattr(da, "_project_dir", lambda pid, it="": d if pid == "poison" else None)
    idoc = {"iteration_id": "iter_001", "status": "completed",
            "cost": {"actual_cost_usd": float("inf"), "token_usage": {"total": 5},
                     "breakdown": [{"employee_id": "e", "cost_usd": float("nan"),
                                    "model": "m", "input_tokens": 1, "output_tokens": 1}]}}
    monkeypatch.setattr(pa, "load_named_project",
                        lambda pid: {"name": "poison", "iterations": ["iter_001"], "status": "active"})
    monkeypatch.setattr(pa, "load_iteration", lambda pid, it: idoc)
    monkeypatch.setattr(pa, "load_project", lambda pid: idoc)
    monkeypatch.delenv("OMC_DEBUG_ALLOW_RAW", raising=False)
    return {"pid": "poison"}


def test_route_non_finite_cost_does_not_500(client, poison_run):
    r = client.get("/api/debug/run/poison")
    assert r.status_code == 200
    assert r.json()["tokens"]["total"]["cost_usd"] == 0.0


def test_route_private_key_file_blocked_and_unlisted(client, poison_run):
    rf = client.get("/api/debug/run/poison/file", params={"path": "deploy_key"})
    assert rf.status_code == 403
    files = client.get("/api/debug/run/poison/files").json()["files"]
    assert not any(f["name"] == "deploy_key" for f in files)


def test_route_bundle_excludes_private_key_and_valid_manifest(client, poison_run):
    import io as _io
    import json as _json
    import zipfile as _zip
    b = client.get("/api/debug/run/poison/bundle")
    assert b.status_code == 200
    zf = _zip.ZipFile(_io.BytesIO(b.content))
    assert "debug_manifest.json" in zf.namelist()
    _json.loads(zf.read("debug_manifest.json"))   # must be strict-valid JSON
    assert not any("deploy_key" in n for n in zf.namelist())


# ── round-3 regressions: plaintext-credential & provider-token coverage ───────
def test_denylist_round3_plaintext_credential_stems():
    for n in ("vault_pass.txt", "passwords.txt", "password.txt", "secret.txt",
              "token.txt", "tokens.json", "connection_string.txt",
              "database-url.txt", ".pgpass.bak", "_netrc", "dockerconfig.json",
              "config.env.json", "backup.env", "auth_token.log"):
        assert da._is_denied(n), n
    # ML artifacts that merely contain 'token'/'key' must STILL be served
    for n in ("tokenizer.json", "token_usage.md", "tokens_used.json",
              "keypoints.md", "monkey.png"):
        assert not da._is_denied(n), n


def test_redact_provider_tokens_round3():
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in da.redact_text(
        "AKIA" "IOSFODNN7EXAMPLE wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", True)
    assert "sk_live_" "4eC39HqLyjWDarjtT1zdp7dc" not in da.redact_text("sk_live_" "4eC39HqLyjWDarjtT1zdp7dc", True)
    assert "glpat-" "ABCDEFGHIJ1234567890" not in da.redact_text("glpat-" "ABCDEFGHIJ1234567890", True)
    assert "abcdEFGH1234567890abcdEFGH1234567890abcd==" not in da.redact_text(
        "AccountKey=abcdEFGH1234567890abcdEFGH1234567890abcd==", True)


def test_redact_uri_redacts_host_too():
    out = da.redact_text("postgres://u:p@db.prod.example.com:5432/x", True)
    assert "db.prod.example.com" not in out and "p@" not in out


def test_project_dir_rejects_non_list_iterations(monkeypatch):
    import onemancompany.core.project_archive as pa
    # truthy-but-not-a-list iterations must NOT pass the 'has a real run' gate
    monkeypatch.setattr(pa, "load_named_project",
                        lambda pid: {"iterations": "oops", "status": "active"})
    assert da._project_dir("whatever") is None


# ── round-4 regressions: tunnel-trust, IaC files, generic value redaction ─────
def test_denylist_round4_iac_and_dotless():
    for n in ("terraform.tfvars", "prod.tfvars", "serverless.yml", "wrangler.toml",
              "npmrc", "pip.conf", "pypirc", "boto", "s3cfg", "dockercfg",
              "env", "environment", "conn.ini", "db_connection.txt"):
        assert da._is_denied(n), n


def test_redact_generic_secretish_keys():
    # weird key names that end in a secret-ish token must still scrub the value
    assert "PlaintextDBpassword123Long" not in da.redact_text(
        'db_admin_pw = "PlaintextDBpassword123Long"', True)
    assert "plainkeyvalue9876543210abcdef" not in da.redact_text(
        'signing_key = "plainkeyvalue9876543210abcdef"', True)
    # …but token-count fields are NOT a secret and must stay readable
    assert "512" in da.redact_text("max_tokens: 512", True)
    assert "5666590" in da.redact_text("token_usage: 5666590", True)


def test_redact_uri_password_only_authority():
    assert "authpassword" not in da.redact_text("redis://:authpassword@redis-host:6379/0", True)


def test_content_secret_dump_detection(tmp_path):
    dump = tmp_path / "env"     # innocuous, extension-less name
    dump.write_text("export AWS_SECRET_ACCESS_KEY=abc\nDB_PASSWORD=hunter2\n", encoding="utf-8")
    assert da._content_is_secret_dump(dump)
    normal = tmp_path / "stage1.md"
    normal.write_text("# Topic\nWe set learning_rate = 0.01 and batch = 32.", encoding="utf-8")
    assert not da._content_is_secret_dump(normal)


def test_json_safe_scrubs_non_finite():
    out = da._json_safe({"a": float("inf"), "b": [float("nan"), 1.5], "c": "ok"})
    assert out["a"] is None and out["b"][0] is None and out["b"][1] == 1.5 and out["c"] == "ok"


def test_aggregate_tokens_non_dict_iter_doc():
    # docstring promises defensiveness — a non-dict must not raise
    assert da.aggregate_tokens("not-a-dict")["total"]["total"] == 0
    assert da.aggregate_tokens(None)["by_employee"] == []


# ── round-5 regressions: non-finite counts, scalar state, more credential files ─
def test_safe_int_handles_non_finite():
    # int(inf) raises OverflowError, int(nan) raises ValueError — both → default
    assert da._safe_int(float("inf")) == 0
    assert da._safe_int(float("-inf")) == 0
    assert da._safe_int(float("nan")) == 0
    assert da._safe_int("12") == 12


def test_aggregate_tokens_non_finite_counts_no_overflow():
    doc = {"cost": {"token_usage": {"total": float("inf"), "input": float("nan")},
                    "breakdown": [{"employee_id": "e", "input_tokens": float("inf"),
                                   "output_tokens": 3, "model": "m"}]}}
    tok = da.aggregate_tokens(doc)          # must not raise OverflowError
    assert tok["total"]["total"] == 0 and tok["total"]["input"] == 0


def test_extract_run_ids_non_dict_state():
    assert da.extract_run_ids("scalar") == {"all": [], "by_stage": {}}
    assert da.extract_run_ids(None) == {"all": [], "by_stage": {}}


def test_denylist_round5_state_and_appconfig():
    for n in ("terraform.tfstate", "prod.tfstate", "terraform.tfstate.backup",
              "application.yml", "application.yaml", "application.properties",
              "db.properties", "kubeconfig.yaml", "cluster.conf", "auth.json",
              "config.toml"):
        assert da._is_denied(n), n


def test_secret_dump_sniff_no_overdeny_ml(tmp_path):
    # genuine secrets dump → denied
    dump = tmp_path / "weird"
    dump.write_text("DB_PASSWORD=hunter2\nAWS_SECRET_ACCESS_KEY=abc123\n", encoding="utf-8")
    assert da._content_is_secret_dump(dump)
    # ML/research content with token/key/author words → NOT denied
    ml = tmp_path / "experiment.py"
    ml.write_text("max_tokens = 512\nsort_key = 1\nnum_key_value_heads = 8\n", encoding="utf-8")
    assert not da._content_is_secret_dump(ml)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("author: Jane\ntoken_count: 512\nmax_tokens: 1024\n", encoding="utf-8")
    assert not da._content_is_secret_dump(cfg)


def test_redact_slack_app_token():
    assert "xapp-" "1-ABCDEFGH-123456789" not in da.redact_text("xapp-" "1-ABCDEFGH-123456789", True)
