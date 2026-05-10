#!/usr/bin/env bash
# Set up the literature-conflict-graph talent's external dependencies.
#
# Usage:
#   bash scripts/setup-lcg.sh              — clone (or update) aigraph repo,
#                                            build its venv, verify, and hire
#                                            employee 00015 if backend is up
#   bash scripts/setup-lcg.sh --no-hire    — same, but skip the hire step
#   bash scripts/setup-lcg.sh --skip-clone — just rebuild the venv (assumes
#                                            $LCG_REPO already cloned)
#
# Env overrides:
#   LCG_REPO=<path>      — where to clone (default ~/projects/literature-conflict-graph)
#   LCG_BRANCH=<branch>  — which branch to checkout (default stable/v0.7-runner-local)
#   LCG_PYTHON_VERSION=<x.y> — Python version for the venv (default 3.12)
#   OMC_PORT=<port>      — backend port for the optional hire step (default 8001)
#
# Idempotent: running twice is safe; clone becomes a fast-forward, venv
# install is incremental.

set -euo pipefail

LCG_REPO="${LCG_REPO:-$HOME/projects/literature-conflict-graph}"
LCG_BRANCH="${LCG_BRANCH:-stable/v0.7-runner-local}"
LCG_PYTHON_VERSION="${LCG_PYTHON_VERSION:-3.12}"
LCG_REPO_URL="https://github.com/iamlilAJ/literature-conflict-graph.git"
OMC_PORT="${OMC_PORT:-8001}"

DO_CLONE=1
DO_HIRE=1
for arg in "$@"; do
  case "$arg" in
    --skip-clone) DO_CLONE=0 ;;
    --no-hire)    DO_HIRE=0 ;;
    --help|-h)
      sed -n '2,20p' "$0"
      exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

# ── helpers ──
info()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
error() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. prereqs ──
command -v git >/dev/null || error "git not found"
command -v uv  >/dev/null || error "uv not found — install from https://astral.sh/uv"

# ── 2. clone / update ──
if [ "$DO_CLONE" = 1 ]; then
  if [ -d "$LCG_REPO/.git" ]; then
    info "lcg repo exists at $LCG_REPO — fetching"
    git -C "$LCG_REPO" fetch --quiet origin "$LCG_BRANCH"
    git -C "$LCG_REPO" checkout --quiet "$LCG_BRANCH"
    git -C "$LCG_REPO" pull --quiet --ff-only || warn "non-fast-forward; keeping local"
  else
    info "cloning lcg → $LCG_REPO (branch $LCG_BRANCH)"
    mkdir -p "$(dirname "$LCG_REPO")"
    git clone --quiet --branch "$LCG_BRANCH" "$LCG_REPO_URL" "$LCG_REPO"
  fi
  ok "repo @ $(git -C "$LCG_REPO" rev-parse --short HEAD) ($LCG_BRANCH)"
fi

[ -d "$LCG_REPO" ] || error "$LCG_REPO does not exist (re-run without --skip-clone)"

# ── 3. venv + install ──
if [ ! -d "$LCG_REPO/.venv" ]; then
  info "creating venv at $LCG_REPO/.venv (Python $LCG_PYTHON_VERSION)"
  uv venv --quiet "$LCG_REPO/.venv" --python "$LCG_PYTHON_VERSION"
fi
info "installing aigraph[real] into venv"
uv pip install --quiet --python "$LCG_REPO/.venv/bin/python" -e "$LCG_REPO[real]"
ok "venv ready"

# ── 4. verify aigraph_query.py runs ──
RUN_DIR="$LCG_REPO/artifacts/runs/arxiv-reasoning-v0.7-540p"
[ -d "$RUN_DIR" ] || error "expected pre-computed run dir not found: $RUN_DIR (lcg repo missing artifacts/?)"

OUT=$(mktemp -t lcg-verify.XXXXXX)
trap 'rm -f "$OUT"' EXIT
"$LCG_REPO/.venv/bin/python" "$LCG_REPO/scripts/aigraph_query.py" \
  --run-dir "$RUN_DIR" \
  --topic "agentic reasoning" \
  --k 3 \
  --output "$OUT" >/dev/null
HEAD=$(head -c 60 "$OUT")
[ "${HEAD#\# Selected Hypotheses}" != "$HEAD" ] || error "aigraph_query.py output unexpected: '$HEAD'"
ok "aigraph_query.py smoke OK ($(wc -c <"$OUT") bytes)"

# ── 5. optional hire ──
if [ "$DO_HIRE" = 0 ]; then
  ok "setup complete (hire skipped)"
  echo
  echo "Next: in OMC UI, Range Selector → Stage 3 ▾ → Literature Conflict Researcher"
  exit 0
fi

if ! curl -s -o /dev/null -m 2 "http://127.0.0.1:$OMC_PORT/" 2>/dev/null; then
  warn "OMC backend not reachable on :$OMC_PORT — skipping hire."
  echo "Start the backend with 'bash start.sh' then re-run: bash $0 --skip-clone"
  exit 0
fi

# Check if employee 00015 already exists
if curl -s -m 3 "http://127.0.0.1:$OMC_PORT/api/bootstrap" \
   | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if any(e.get('employee_number')=='00015' for e in d.get('employees',[])) else 1)" 2>/dev/null; then
  ok "employee 00015 already hired — nothing to do"
  exit 0
fi

info "hiring talent via /api/candidates/hire-from-cv"
curl -s -m 30 -X POST "http://127.0.0.1:$OMC_PORT/api/candidates/hire-from-cv" \
  -H 'Content-Type: application/json' \
  -d '{"cv":{"name":"Literature Conflict Researcher","role":"Researcher","talent_id":"literature-conflict-graph","hosting":"openclaw","auth_method":"api_key","api_provider":"custom","llm_model":"","temperature":0.3,"skills":["literature_conflict_graph"]}}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(' →', d.get('status','?'), '—', d.get('message',''))"

# Wait for onboarding
for _ in $(seq 1 30); do
  if curl -s -m 3 "http://127.0.0.1:$OMC_PORT/api/bootstrap" \
     | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if any(e.get('employee_number')=='00015' for e in d.get('employees',[])) else 1)" 2>/dev/null; then
    ok "employee 00015 registered"
    echo
    echo "Done. In OMC UI, Range Selector → Stage 3 ▾ → Literature Conflict Researcher"
    exit 0
  fi
  sleep 1
done
warn "onboarding timed out after 30s — check backend logs"
