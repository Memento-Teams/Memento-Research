# Memento Memory Tool

Long-term cross-session memory for OMC employees. Two LangChain tools
(`store`, `recall`) backed by the vendored `memento_v4` package
(causal graph + hybrid vector/BM25/BFS retrieval). Each employee has
a private memory store; the tool resolves `employee_id` server-side
from the active `Vessel` ContextVar, so an LLM cannot read or write
another employee's memory.

## When to use this

You are building a feature where an employee needs to remember facts,
decisions, customer configs, or verbatim values across tasks (i.e.
beyond a single LangGraph run). Examples:

- "What did we decide about cache layer X last quarter?"
- Customer onboarding facts (URLs, ports, SSO settings)
- Coding standards / framework choices the team agreed on
- Bug fixes that should not be re-derived next time

You do **not** need this for in-task scratchpad notes (use
`SESSION-STATE.md` or the agent's working messages) or for static
configuration (use yaml under `company/`).

## Quick start — let an employee use it

1. Add `memento` to the employee's tools in their `profile.yaml`:

   ```yaml
   tools:
     - memento
   ```

   The asset tool registry picks the manifest up from
   `company/assets/tools/memento/tool.yaml` and registers two tool
   names: `store` and `recall`.

2. Set the LLM env vars (any OpenAI-compatible endpoint works):

   ```bash
   export OPENROUTER_API_KEY=sk-...
   export OPENROUTER_BASE_URL=https://app.ppapi.ai/v1
   export MEMENTO_MODEL=gemini-3-flash-preview     # finalize model
   ```

3. Nudge the agent in its `system_prompt_template`. The tool itself
   does not auto-run on task start/end — the LLM decides. A minimal
   prompt fragment that works:

   ```text
   You have a long-term memory. Two tools:
   - recall(query, top_k=5): search prior sessions before answering
     factual questions.
   - store(turns): persist a finished session at end-of-task when
     facts, decisions, or customer-specific values were captured.

   Rules:
   - For factual recall ("what did we decide", "what is X for
     customer Y"), call recall FIRST. Do not answer from prior
     knowledge.
   - When a task captures a fact, call store BEFORE the final
     answer. Pass full turns including verbatim values (URLs, port
     numbers, names).
   ```

That's the whole integration on the agent side. The tool is
registered globally but only resolves to a real memory dir when
called from inside a `Vessel` run, so you don't need any other glue.

## Quick start — call from Python (tests, scripts, batch jobs)

Same two tools, no LLM involved. You set the `Vessel` ContextVar
manually and call `.invoke(...)`:

```python
from types import SimpleNamespace
from onemancompany.core.vessel import _current_vessel
from company.assets.tools.memento.memento import store, recall

vessel = SimpleNamespace(employee_id="E00006")
token = _current_vessel.set(vessel)
try:
    store.invoke({
        "turns": [
            {"role": "user", "content": "Acme uses SAML SSO. IdP at sso.acme.example."},
            {"role": "assistant", "content": "Acme onboarding documented: SAML 2.0, IdP sso.acme.example, 4hr session timeout."},
        ]
    })
    result = recall.invoke({"query": "How does Acme authenticate?", "top_k": 3})
    print(result["context"])
finally:
    _current_vessel.reset(token)
```

If you need a fresh memory root for a test (instead of
`EMPLOYEES_DIR/{employee_id}/memory/`), monkeypatch
`onemancompany.core.config.EMPLOYEES_DIR` and the module-level
`EMPLOYEES_DIR` in `company.assets.tools.memento.memento`.

## Tool schemas

### `store(turns: list[dict]) -> dict`

Persists a finished session into the active employee's memory and
runs the memento_v4 finalizer (1 LLM call) to extract a
`SessionNode` (title, goal, outcome, key quotes, files touched) and
update the causal graph.

**Input:**
```python
turns = [
    {"role": "user" | "assistant", "content": "..."},
    ...
]
```
Roles other than `user`/`assistant` are rejected. Empty / non-string
content is rejected. The transcript is written to
`EMPLOYEES_DIR/{employee_id}/memory/sessions/NNN.json` **before**
finalize runs, so a finalize crash never loses the raw turns.

**Output:**
```python
{
    "status": "ok",
    "session_id": "convE00006_sess1",
    "session_num": 1,
    "title": "Acme onboarding — SAML SSO",
    "outcome": "complete",
    "edges_added": 0,
    "supersede_added": 0,
}
```
On finalize failure, returns `{"status": "error", "message": "...", "session_num": N, "note": "transcript persisted; will retry on next store/recall"}`.

### `recall(query: str, top_k: int = 5) -> dict`

Hybrid retrieval over the active employee's prior sessions: vector
similarity (Chroma) + BM25 lexical match + causal-chain BFS
expansion (forward up to 5 hops, backward up to 2). `top_k` is
clamped to `[1, 20]`. Returns at most `top_k` sessions.

**Output:**
```python
{
    "status": "ok",
    "query": "How does Acme authenticate?",
    "context": "## Acme onboarding (...) [SUPERSEDED if any]\n- ...\n- ...",
    "session_ids": ["convE00006_sess1", "convE00006_sess7", ...],
}
```
If memory is empty, returns
`{"status": "ok", "context": "(no prior sessions)", "session_ids": []}`.

## On-disk layout

```
EMPLOYEES_DIR/{employee_id}/memory/
├── sessions/
│   ├── 001.json        # raw turns (always written)
│   └── ...
└── conv_{employee_id}/
    ├── _v4_meta.json   # supersede sidecar
    └── causal/
        └── _global/
            └── MEMORY.md
```

Each session JSON is the source of truth for raw turns. Everything
under `conv_{employee_id}/` is rebuildable from those JSONs by
re-running ingest.

## Isolation guarantee

`employee_id` is **never** a tool parameter. It is read from
`onemancompany.core.vessel._current_vessel` inside `_resolve_employee_id()`.
Files live under `EMPLOYEES_DIR/{employee_id}/memory/`. The LLM has
no way to address a different employee's store: there is no field to
pass, and the path is computed server-side. To run as a different
employee from your own code, set the ContextVar yourself (the
`Vessel` runtime does this automatically for in-task agent runs).

## Cost notes

- `store` runs **1 LLM call** per invocation (the memento_v4
  finalize). Defaults use `AblationFlags(reflect_synthesis=False)`
  to skip the synthesis pass — keeps cost predictable.
- `recall` runs **0 LLM calls**. Hybrid retrieval is local
  (Chroma + BM25 + BFS over the on-disk causal graph).
- Cold-start cost on a fresh memory dir: 1 store finalize.
- Re-ingest cost: the adapter rebuilds its in-memory index per
  process, so each `recall` call re-ingests the existing sessions
  from disk before searching. For batch scripts that issue many
  recalls, build the adapter once and reuse — patch
  `MemoryV4Adapter` if you need that path; the asset-tool wrapper
  re-instantiates per call by design (process-shared state would
  break isolation).

## Phase-1 known limitations

- The upstream finalize prompt does not always emit `causal_edges`
  or `superseded` flags on short transcripts (3-5 turns). Vector +
  BM25 still find the right session, but ranking does not promote
  the latest decision over a superseded one. Tracked for an
  upstream fix.
- No automatic on-task hook. The LLM must decide to call `store` /
  `recall`. If you want unconditional recall at task start (the
  pattern OMC's "default memory" uses), wrap the agent with a
  pre-run step that calls `recall.invoke(...)` and prepends the
  context to the LLM's input.

## Tests

- Unit: `tests/tools/test_memento.py` — 14 tests, no LLM, fully
  patched adapter.
- Integration: `tests/integration/test_memento_e2e.py` — 3 tests,
  real LLM, auto-skipped without `OPENROUTER_API_KEY`.
- Stress: `scripts/test_memento_tool.py` — 22-session corpus,
  10 retrieval queries, runs `store`/`recall` directly.
- Agentic: `scripts/test_memento_agent.py` — 2-task LLM-driven
  scenario; `scripts/test_memento_agent_corpus.py` — 22-session
  corpus + 6 LLM-driven recall queries with verbatim assertions.
