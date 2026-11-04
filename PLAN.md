# Agent Smith: Project Plan

Goal now: Amin can run the project on his own machine and explain every part of it in a Google internship interview.
Goal later: pass the 42 evaluation (SWE-bench, benchmark report, 42 README). We switch only when Amin says so.

Status legend: [ ] todo, [~] in progress, [x] done
Subject references: "V.2(3)" = item 3 of section V.2 in `agent smith subject.pdf` (v1.1). Page numbers are the printed ones.

---

## 0. Overview

### 0.1 Milestones at a glance

| # | Milestone | Owner | Est. | Status |
|---|-----------|-------|------|--------|
| **Phase 1: Interview-ready MBPP agent** |||||
| 1 | Repo, uv, Python 3.10, Pydantic models, JSON config loading | Shared | 3-4 h | [x] done 2026-11-04, tag `m1-done` |
| 2 | Sandbox policy layer (in-process): exec, restricted builtins, import allowlist, path guard, AST checks | A | 5-7 h | [x] done 2026-11-04, tag `m2-done` |
| 3 | Sandbox isolation: worker process, IPC, timeout (soft + hard), partial output | A | 6-8 h | [ ] |
| 3.5 | Sandbox hardening: memory limit, network block, audit hook, KeyboardInterrupt/SystemExit, security suite | A | 5-7 h | [ ] |
| 4 | Sandbox REPL CLI (`uv run sandbox`) and `final_answer` | A | 3-4 h | [ ] |
| 5 | MCP fundamentals + MBPP MCP server (`run_tests`) + standalone client | A | 5-6 h | [ ] |
| 5.5 | MCP inside the sandbox: bridge, tool wrappers, HTTP transport, auto-generated manual | A | 6-8 h | [ ] |
| 6 | LLM provider layer: abstraction, multi-key rotation, retries, usage tracking | B | 5-6 h | [ ] |
| 7 | Code extraction layer (python blocks, XML, Hermes, ReAct) | B | 4-5 h | [ ] |
| 7.5 | Agent loop, system prompt, budgets, feedback messages, SolutionOutput | B | 6-8 h | [ ] |
| 8 | MBPP CLI, moulinette dump/run/validate, batch runs, tuning | B | 4-8 h | [ ] |
| 8.5 | Interview prep: notes, pitches, demo, personal README, live-modification drills | Shared | 6-8 h | [ ] |
| **Phase 2: SWE-bench and 42 evaluation (after the interview)** |||||
| 9 | SWE-bench tools (9 mandatory) + Docker bridge | Split | 10-14 h | [ ] |
| 10 | SWE-bench agent, limits, container cleanup, signal handling | B | 10-15 h | [ ] |
| 11 | BENCHMARK_REPORT.md (5 models, ≥2 providers, 3 tasks, ablation) | Shared | 8-12 h + quota waits | [ ] |
| 12 | 42-format README, requirement re-read, exam dry runs, live-mod practice | Shared | 4-6 h | [ ] |

Phase 1 total: roughly 60-80 hours of focused work. Phase 2: roughly 35-50 hours.

### 0.2 Minimum viable cut (if the interview is close)
If time runs short, this order gives a working, explainable demo earliest:
M1 → M2 → M3 (+ memory limit from 3.5) → M4 → M5 (stdio only) → M6 → M7 (python blocks only) → M7.5 → M8.
Then return to fill in: rest of M3.5, M5.5 HTTP + resources/prompts, M7 XML/Hermes/ReAct.
M6 has no dependency on M2-M5.5 and can be pulled forward at any time.

### 0.3 What changed compared to the previous PLAN.md, and why
1. **M3 split into M3 and M3.5.** Process isolation (worker, IPC, timeout) and hardening (memory, network, audit hook, exception propagation) are each a full session of new concepts. M3.5 ends with a security test suite that mirrors `exam_sandbox.sh`, so it deserves its own checkpoint.
2. **M2 redefined as "policy layer, in-process" and M3 as "move it into a worker process".** This answers the subject's in-process-vs-subprocess question (V.2 green box) explicitly, and nothing written in M2 is thrown away: the same policy code runs inside the worker.
3. **M5 split into M5 and M5.5.** Learn MCP on its own (protocol, server, a plain client) before wiring it through the worker pipe and an async bridge. The sync/async bridge is the hardest concept of Phase 1.
4. **M7 split into M7 (extraction) and M7.5 (loop).** Extraction is a pure function (text in, code + diagnostics out) and is the best place for table-driven tests. The loop then builds on a tested component.
5. **M1 gets a step 1.0 (repo root fix) plus `.gitignore` / `.env.example`.** The subject's commands (`cd moulinette` ... `cd ../student`) show that `student/` is the repository and `moulinette/` a sibling. Today the git repo is the parent folder.
6. **`SWEBenchTaskInput` moved from M1 to M9.** Phase 1 stays focused.
7. **MBPP `run_tests` executes candidate code inside a sandbox worker**, not with a raw `exec` in the MCP server (M5). A tool that runs model-written code without restrictions would be a sandbox bypass (VI.4.1).
8. **M8.5 has a measurable "done when"** (timed pitch, timed live-mod drills, fresh-clone run).
9. **M9 starts with a Docker spike and an architecture decision** before any tool is written.
10. **Revised from my earlier M1 explanation:** `extra="forbid"` only for *our own* config models, not for the subject-defined models (see D12).
11. Added time estimates, design decisions with trade-offs, traceability, a risk register and definitions of done.
12. **Updated after reading the moulinette source (2026-11-03).** We copy `models_public.py`. The MBPP first test is hidden. MBPP validation needs Docker. CLI exit codes and default flags are fixed. `display` consistency rules were added to M7.5. The SIGTERM → 10 s → SIGKILL kill is per process group. The SWE-bench exam pool is fixed (6 tasks). The exam scripts are still missing.

---

## A. Big picture

### A.1 System architecture

```
┌──────────────────────────── agent process (host) ───────────────────────────────┐
│                                                                                  │
│  CLI (agent_mbpp / agent_swebench)                                               │
│    │ builds everything from config + CLI args                                    │
│    ▼                                                                             │
│  Agent loop (orchestrator) ───► LLM client ──HTTPS──► LLM provider (free tier)   │
│    │   ▲                        (KeyPool, retries, usage, stop sequences)        │
│    │   │ observation text                                                        │
│    ▼   │                                                                         │
│  Code extraction (pure functions: python / XML / Hermes / ReAct → Python code)   │
│    │ code                                                                        │
│    ▼                                                                             │
│  ┌──────────────────────── Sandbox ─────────────────────────────┐                │
│  │ Controller (parent side)                                     │                │
│  │   - spawns + supervises the worker, enforces hard timeout    │                │
│  │   - relays tool calls ───► MCPBridge (asyncio thread) ───────┼─stdio / HTTP──►│ MCP server process
│  │          ▲ JSON messages over a pipe                         │                │ (mcp_tools_mbpp.py,
│  └──────────┼───────────────────────────────────────────────────┘                │  unknown server, ...)
└─────────────┼────────────────────────────────────────────────────────────────────┘
              ▼
   Sandbox worker process (untrusted code runs here)
     rlimits (memory, CPU, file size) · audit hook (network, processes, paths)
     restricted builtins · import allowlist · path guard · AST check
     persistent namespace · MCP tool stubs · final_answer
```

The "Sandbox" box in the subject's figure (page 8) = our controller + worker + MCP client. The sandbox wraps the MCP client, not the other way round (page 16 info box).

### A.2 Data flow of one MBPP run

```
moulinette_eval dump mbpp ──► ../cache/mbpp_task.json
uv run python -m agent_mbpp --task-file ... --output ... --model-name M --provider-url U
```
1. **CLI**: parse args, load `.env` (without overriding real env vars), load `configs/agent_mbpp.json`, `sandbox_template.json`, `configs/providers.json` into Pydantic models. Any error → readable message + a valid `solution.json` with `error` set.
2. **Task**: `MBPPTaskInput.model_validate_json(...)`.
3. **Sandbox start**: controller spawns the worker (limits + policy + audit hook installed).
4. **MCP**: controller's MCPBridge launches `python mcp_tools_mbpp.py` over stdio → `initialize` → `tools/list`, `resources/list`, `prompts/list`.
5. **Wrappers + manual**: tool specs sent to the worker, which builds Python stubs (`run_tests(code, test_list)`). The manual is generated from the same specs.
6. **Prompt**: system prompt = framework rules + response format + manual. The user message has the task definition, the required function signature and the tests. `test_list` is also injected into the namespace as a variable, so the model doesn't have to copy it (saves tokens).
7. **Loop**, for step = 1..max_iterations:
   1. budget check (cumulative input/output tokens, wall-clock deadline)
   2. LLM call with `stop=["<end_code>", "Observation:"]` and `max_tokens = min(per_call_cap, output_budget_left)`. Returns text, usage, latency and retries.
   3. extraction → code + notes. No code → feedback message, record step, next iteration.
   4. `sandbox.execute(code)`: worker runs it; `run_tests(...)` goes worker → controller → MCP server → back; stdout is streamed to the controller.
   5. `final_answer(code_string)` called → stop the loop.
   6. observation = stdout + error + notes, truncated with an explicit marker → appended as the next user message
   7. `StepMetrics` recorded; `solution.json` rewritten atomically (so a kill still leaves a valid file)
8. **Finish**: `SolutionOutput` totals, `system_prompt`, `success`, `error` → final write.
9. **Shutdown** (also on SIGTERM / Ctrl+C): stop worker, stop MCP server, (Phase 2: remove container).
```
moulinette_eval validate mbpp task.json solution.json ──► PASS / FAIL (tests + limits)
```

### A.3 Directory and module layout

```
agent-smith/                    parent folder, NOT a git repository
├── moulinette/                 42 grader, read-only, never committed
├── cache/                      dumped tasks + solutions (the subject uses ../cache)
└── student/                    ← THE git repository
    ├── pyproject.toml          project metadata, deps, console script `sandbox`
    ├── uv.lock                 exact dependency versions (committed)
    ├── .python-version         3.10
    ├── .gitignore              .venv, .env, __pycache__, cache/, evaluations/, *.log
    ├── .env.example            key variable NAMES with empty values (committed)
    ├── README.md               personal README now, 42 format in M12
    ├── CLAUDE.md  PLAN.md  WORK_DIVISION.md  INTERVIEW_NOTES.md  AI_USAGE_LOG.md
    ├── notes/                  M1.md, M2.md, ... learning notes for the teammate
    ├── sandbox_template.json   default SandboxConfig (root: the subject calls it by bare name)
    ├── configs/
    │   ├── providers.json      provider name → base_url, API-key env var names
    │   ├── agent_mbpp.json     limits with safety margins (VI.1.1)
    │   └── agent_swebench.json (Phase 2)
    ├── mcp_tools_mbpp.py       MCP server, must be at root (V.2(4))
    ├── mcp_tools_swebench.py   MCP server, must be at root (Phase 2)
    ├── agent_smith/            the framework (reusable library)
    │   ├── __init__.py
    │   ├── models.py           StepMetrics, SolutionOutput, SandboxConfig, MBPPTaskInput (+ SWEBenchTaskInput in M9)
    │   ├── config.py           load_json_model(), .env loading, ConfigError
    │   ├── sandbox/
    │   │   ├── policy.py       import guard, safe builtins, path guard, AST validator   (M2)
    │   │   ├── runtime.py      exec in a namespace, output capture, last-expression echo (M2)
    │   │   ├── errors.py       SandboxSecurityError, SandboxTimeout, FinalAnswerSignal  (M2-M4)
    │   │   ├── protocol.py     message types controller ⇄ worker                        (M3)
    │   │   ├── worker.py       child process main: limits, audit hook, message loop     (M3, M3.5)
    │   │   ├── controller.py   Sandbox class: start / execute / close, timeouts         (M3)
    │   │   ├── manual.py       sandbox manual generation                                (M5.5)
    │   │   └── cli.py          `uv run sandbox` REPL                                     (M4)
    │   ├── mcp_client/         (NOT named `mcp`: that would shadow the SDK package)
    │   │   ├── bridge.py       MCPBridge: asyncio loop in a thread, stdio + HTTP        (M5, M5.5)
    │   │   └── schema.py       JSON Schema → Python signature                           (M5.5)
    │   ├── llm/
    │   │   ├── base.py         LLMClient Protocol, ChatMessage, LLMResponse             (M6)
    │   │   ├── openai_compat.py  httpx client for OpenAI-compatible APIs                (M6)
    │   │   ├── keys.py         KeyPool rotation + cooldowns                             (M6)
    │   │   └── fake.py         scripted FakeLLM for tests                               (M6)
    │   ├── agent/
    │   │   ├── extraction.py   four parsers → ExtractionResult                           (M7)
    │   │   ├── prompts.py      system prompt templates                                   (M7.5)
    │   │   ├── budget.py       iterations / tokens / deadline accounting                 (M7.5)
    │   │   ├── loop.py         Agent: Thought → Code → Observation                       (M7.5)
    │   │   └── output.py       SolutionOutput builder, atomic write                      (M7.5)
    │   └── swebench/           Phase 2: tool implementations, executors, docker          (M9, M10)
    ├── agent_mbpp/__main__.py      `uv run python -m agent_mbpp ...`                     (M8)
    ├── agent_swebench/__main__.py  `uv run python -m agent_swebench ...`                 (M10)
    ├── scripts/                mcp_probe.py, llm_ping.py, run_mbpp_batch.sh, make_report.py
    ├── benchmarks/             Phase 2: committed solution.json files backing the report
    ├── BENCHMARK_REPORT.md     Phase 2
    └── tests/                  pytest; fixtures/ (sample tasks, broken configs, dummy MCP server, mini repo)
```

Why `agent_mbpp` and `agent_swebench` are top-level packages: the subject runs `uv run python -m agent_mbpp` from `student/` (V.3(1)). They stay thin (CLI only). All logic lives in `agent_smith`, so the CLIs, the REPL and the tests share one implementation.

### A.4 Config files, dependencies, what lives where

| What | Where | Committed? |
|------|-------|-----------|
| Sandbox limits / allowlists | `sandbox_template.json` → `SandboxConfig` | yes |
| Agent limits (iterations, token budgets, deadline, truncation sizes, temperature) | `configs/agent_*.json` → `AgentConfig` (M7.5) | yes |
| Providers (base URL, env var names of keys) | `configs/providers.json` → `ProviderConfig` (M6) | yes |
| API key values | environment / `.env` | **never** (`.env.example` lists names only) |
| Dumped tasks, scratch solutions | `../cache/` | no (outside repo) |
| Local evaluation-style runs | `evaluations/` | no (gitignored) |
| Benchmark evidence | `benchmarks/` | yes (V.7 requires the solution.json files) |

**Runtime dependencies (uv):**
- `pydantic` v2: models and config validation
- `mcp`, the official MCP Python SDK (client sessions, stdio + streamable HTTP transports, FastMCP for our servers). The moulinette itself depends on `mcp[cli]>=1.22.0`; we use the same lower bound and pin what uv resolves.
- `httpx`: HTTP client for LLM providers (already a dependency of `mcp`; declare it explicitly because we import it)
- `python-dotenv`: load `.env` (optional; a 15-line parser is an acceptable alternative)

**Dev dependencies:** `pytest`, `ruff` (lint + format), `mypy` (optional type checking).

**Forbidden:** smolagents, langchain/langgraph, llama-index, crewai, autogen, RestrictedPython, or any library that implements agent orchestration or sandboxing for us (IV.2, V.2 red box).

### A.5 Key design decisions (options, trade-offs, recommendation)

**D1. Where untrusted code runs (V.2 green box)**
| Option | Kill runaway code? | Memory limit? | Persistent variables? | Complexity |
|---|---|---|---|---|
| a. `exec` in the agent process | only cooperatively (signals between bytecodes); C-level loops can't be stopped | an rlimit would limit the whole agent | yes | low |
| b. New subprocess per execution | yes (kill) | yes (rlimit in child) | no: variables are lost every step, which breaks "persistent variables between steps" (III.1) | medium |
| c. **Persistent worker subprocess** | yes (kill + respawn) | yes | yes, until a hard kill | medium-high |
| d. Container / gVisor / seccomp | yes | yes | yes | high, not stdlib-only, slow |

**Recommendation: (c).** A long-lived worker process holds the namespace; the parent supervises it. A hard kill loses the namespace, and the LLM is told explicitly. This is the standard supervisor pattern and an easy story to tell.

**D2. Layers of restriction (defense in depth)**, from cheapest to strongest:
1. AST check before execution: reject known escape attributes (`__subclasses__`, `__globals__`, `__builtins__`, `__class__`, `__bases__`, `__mro__`, `__code__`, `f_globals`, `tb_frame`, ...).
2. Restricted builtins: remove `eval`, `exec`, `compile`, `input`, `breakpoint`, `globals`, `locals`, `vars`, `exit`, `quit`, `help`; replace `open` and `__import__` with guarded versions. Keep `__build_class__`, or `class Foo:` breaks.
3. Import allowlist with `.*` submodule semantics.
4. Path guard on `open()` with `os.path.realpath` (defeats `..` and symlinks).
5. Worker-level: `sys.addaudithook` (PEP 578, stdlib) blocks sockets, process creation and file access outside `allowed_directories`, even if layers 1-4 are bypassed (for example `random._os.system(...)`). `resource.setrlimit` caps memory, CPU and file size.

Honest framing for the interview: layers 1-4 are *policy*, layer 5 plus process separation is *containment*. Python's docs say audit hooks are not a full security boundary; in production I would add OS isolation (namespaces, seccomp, containers).

**D3. Timeout strategy**
- **Soft:** in the worker, `signal.setitimer` raises `SandboxTimeout` (a `BaseException` subclass) between bytecodes. The namespace survives.
- **Hard:** the controller waits `timeout + grace`; if the worker is still busy (a C-level loop, catastrophic regex), it kills and respawns the worker. The namespace is lost and the LLM is told.
- The timer is paused while an MCP tool call is in flight (tools are outside the sandbox's timeout, page 16).

**D4. Controller ⇄ worker messages: JSON, not pickle.** The worker runs untrusted code; unpickling data it sends could execute code in the parent. Each message is a JSON object sent with `Connection.send_bytes`.

**D5. Who owns the MCP connection, and sync vs async**
- The **controller** owns it, not the worker. The worker has no network and can't spawn processes, which is exactly what stdio and HTTP transports need.
- The `mcp` SDK is asyncio-based; the agent loop is synchronous. **Recommendation:** an `MCPBridge` runs its own event loop in a background thread and exposes sync methods via `asyncio.run_coroutine_threadsafe`. The alternative, making the whole agent async, spreads `async` everywhere and is harder to explain and test.

**D6. MCP transport order: stdio first, then streamable HTTP.** Stdio has no ports or URLs, its lifetime is tied to the client, and it's the subject's main example. HTTP adds a server you start separately, and the URL path (`/mcp`) has to match. Both are mandatory (V.2(4)).

**D7. MCP library: the official SDK, plus one hand-written handshake.** The SDK is allowed: it implements the protocol, not agent orchestration. In M5 we first send a raw `initialize` JSON-RPC message by hand, so you can explain what the SDK does on the wire.

**D8. Provider abstraction**
- `LLMClient` Protocol: `complete(messages, stop, max_tokens) -> LLMResponse`.
- One implementation, `OpenAICompatibleClient` (httpx). Most free providers expose an OpenAI-compatible `/chat/completions` endpoint, so switching provider means changing config, not code.
- Adapters for native APIs can be added behind the same Protocol if needed.
- Keys come from env var names listed in `providers.json` (e.g. `OPENROUTER_API_KEY`, `OPENROUTER_API_KEY_2`).
- `KeyPool` handles rotation: round-robin; on 429, cool the key down for `Retry-After`; on 401/403, disable the key; if all keys are cooling, wait, bounded by the deadline.
- Retries: exponential backoff with jitter for 5xx and timeouts. Every attempt is counted (`retries`, `total_requests`).
- Provider fallback is optional and off by default. If it's used, it must switch to another provider serving the **same model**: `moulinette_eval display` warns when steps have different `model_name`s.

**D9. Code extraction and stop sequences**
- The model is told to answer `Thought: ...` then a ```` ```python ... ``` ```` block followed by `<end_code>`.
- The API's `stop` parameter makes the provider stop generating at `<end_code>`. Without it, models often keep going and write an invented `Observation:`, a hallucinated tool output (V.6 question box). Stop strings are not included in the returned text.
- Extraction runs the parsers in order: python fences → XML `<invoke>` → Hermes `<tool_call>` → ReAct `Action:`.
- Non-Python formats are converted to `result = tool(arg=<repr(value)>)\nprint(result)`. `repr()` produces safe Python literals, so a string argument can never inject code.
- Defensive cuts: anything after a stray `Observation:` is dropped; a missing closing fence → interpret the rest as code and say so in the feedback.
- Some providers cap the number of stop sequences (often 4) or ignore them for some models; extraction must not rely on them.

**D10. MBPP budget strategy.** Input tokens are cumulative: every call resends the whole conversation, and the limit is 6,000 for the whole task (VI.1.1).
- Example: a 500-token system prompt plus a 250-token task makes call 1 ≈ 750 tokens. Call 2 ≈ 750 + 300 (answer) + 200 (observation) ≈ 1,250. Call 3 ≈ 1,750; call 4 ≈ 2,250. Four calls ≈ 6,000.
- So realistically ≤ 4 iterations, even though `max_iterations` is 10.
- Design for a **one-step solve**: in one code block the model writes the solution string, calls `run_tests(solution, test_list)`, and calls `final_answer(solution)` if the tests pass.
- Keep the system prompt compact, use a non-reasoning model (thinking tokens count against the limit), and pre-check the budget before every call.

**D11. Package layout.** Flat multi-package layout (`agent_smith/`, `agent_mbpp/`, `agent_swebench/` at the root) with the `hatchling` build backend and an explicit `packages` list. A build backend is required for the `sandbox` console script. `src/` layout was considered and rejected: `python -m agent_mbpp` from `student/` must find the package and the subject expects root-level files. Alternative: `uv_build` with a module list; verify in M1 if you prefer it.

**D12. Pydantic strictness policy**
- Subject-defined models (`StepMetrics`, `SolutionOutput`, `SandboxConfig`, `MBPPTaskInput`, `SWEBenchTaskInput`): taken verbatim from `../moulinette/models_public.py` (copied, never edited), with default extra-field handling (ignore). Its `SandboxConfig` has empty default lists; we subclass it with the subject's defaults, as the moulinette itself does.
- Our own models (`AgentConfig`, `ProviderConfig`): `extra="forbid"` so a typo like `max_iteratons` fails loudly. Add range checks like `Field(gt=0)`.

**D13. SWE-bench architecture (tentative, decided in M9 after a spike)**
- Recommended: approach (b) from V.4. The sandbox and the MCP server run on the host; tools go through an `Executor` abstraction.
  - `LocalExecutor` runs commands in `TESTBED_PATH`; the moulinette's isolated tool tests use this path (V.4 red box).
  - `DockerExecutor` runs the same commands via `docker exec` in the task container.
  - Benefit: one code path, so the isolated tests exercise the same logic the agent uses.
- Alternative: run the MCP server inside the container (`--mcp-stdio "docker exec -i <id> python mcp_tools_swebench.py"`). Cleaner paths, but it needs Python ≥ 3.10 and the `mcp` package inside the container. Check that in the spike.

**D14. Output writing.** `solution.json` is written atomically (write a temp file, then `os.replace`) after **every** step. Even if the grader's SIGTERM/SIGKILL (VI.1) hits mid-run, a valid JSON with real data exists.

---

## B. Milestones

Each milestone follows the routine from CLAUDE.md:
1. explain the concept and design
2. show the full code; Amin types it
3. run the "done when" check
4. prediction check and explain-back
5. `notes/M<n>.md`
6. `AI_USAGE_LOG.md` line
7. Amin commits (tag `m<n>-done`, so live modifications can be reverted with `git checkout`)

---

### Milestone 1: Repo, uv, Python 3.10, Pydantic models, config loading
**Owner** Shared · **Time** 3-4 h · **Subject** IV.1, V.1.1, V.2 (SandboxConfig, "Pydantic models and JSON configuration files"), V.3(3), VI.3 (no hardcoded keys), VIII

**Goal and why now.** Everything else exchanges these data structures, and `SolutionOutput` *is* the grader's contract. Getting a reproducible environment and typed contracts first means later bugs show up at the boundary instead of deep inside the loop.

**Concepts first**
- uv: `pyproject.toml`, `uv.lock`, `.venv`, `uv sync`, `uv run`, dependency groups, console scripts, build backend
- `.python-version` and `requires-python`
- Python packages, `__init__.py`, what `python -m pkg` does (runs `pkg/__main__.py`, puts the cwd on `sys.path`)
- Pydantic v2: `BaseModel`, `Field`, `default_factory` (why not `= []`), lax coercion (`"256"` → `256`), `model_validate_json`, `model_dump_json`, `ValidationError`
- Secrets hygiene: env vars, `.env`, `.gitignore`

**Steps**
1.0 Repo root fix and setup (Amin runs it):
- the git repo (cloned from `git@github.com:aminoqr/agent-smith.git`) was in the parent folder. DONE 2026-11-03: `mv .git student/.git`, so `student/` is now the repo root with the same remote. Nothing was deleted.
- `.gitignore` starts with `agent smith subject.pdf` (42 course material, not for a public repo); the rest is added in 1.9
- create `../cache/`
- the moulinette is already at `../moulinette/`: run `uv sync` there once (its README's install step)
- enable Docker Desktop's WSL integration for this distro, then check `docker run --rm python:3.11-slim python -c 'print(1)'`. `validate mbpp` runs solutions in that image, so Docker is needed from M8 on, even for MBPP.

1.1 `uv init` inside `student/`, pin 3.10 (`.python-version`, `requires-python = ">=3.10,<3.11"`). Check: `uv run python --version`.

1.2 Edit `pyproject.toml`: hatchling build-system, `packages = ["agent_smith"]` (others added later), `uv add pydantic`, `uv add --dev pytest ruff`.

1.3 `agent_smith/__init__.py`. Check: `uv run python -c "import agent_smith"`.

1.4 Models:
- `cp ../moulinette/models_public.py agent_smith/models_public.py` (Amin runs it). The moulinette README says to copy this file: it is the exact schema `solution.json` is validated against, so it is never edited.
- `agent_smith/models.py` re-exports the five models. Its `SandboxConfig` subclass sets the subject's default allowlist and directories, because the public file's defaults are empty lists (the moulinette's own `models.py` does the same).
- we read every field together; you must be able to explain each one

1.5 `agent_smith/config.py`: `load_json_model(path, model_cls)`. It raises `ConfigError` with a one-line, human-readable message for: file missing, invalid JSON (with line/column), validation errors (field path + reason).

1.6 `sandbox_template.json` with the default values; `tests/fixtures/bad_sandbox.json` with a wrong type.

1.7 `tests/test_models.py`:
- SolutionOutput JSON round-trip
- defaults are not shared between instances
- `task_id` is a str

1.8 `tests/test_config.py`: good file, missing file, bad JSON, bad type.

1.9 `.gitignore`, `.env.example`; first commit.

**Files:** `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.env.example`, `agent_smith/__init__.py`, `agent_smith/models_public.py` (copied), `agent_smith/models.py`, `agent_smith/config.py`, `sandbox_template.json`, `tests/test_models.py`, `tests/test_config.py`, `tests/fixtures/*.json`

**Done when**
- `uv run python --version` → `Python 3.10.x`
- `uv run python -c "from agent_smith.config import load_json_model; from agent_smith.models import SandboxConfig; print(load_json_model('sandbox_template.json', SandboxConfig))"` → a SandboxConfig with `max_execution_time_seconds=30 max_memory_mb=512` and the 20 default imports
- the same command with `tests/fixtures/bad_sandbox.json` → one line like `Invalid config tests/fixtures/bad_sandbox.json: max_memory_mb: Input should be a valid integer` and no traceback
- `uv run pytest -q` → all green

**Understanding checks**
1. Predict: `SandboxConfig.model_validate_json('{"max_memory_mb": "256"}')` and then with `"abc"`. Which one fails, and why does the first not fail?
2. Why `Field(default_factory=list)`? Pydantic copies a plain `= []` default per instance, so when does `default_factory` matter, and what does a plain `@dataclass` do with `= []`?
3. `SolutionOutput.timestamp` uses `default_factory=datetime.now().isoformat`. If you build the object at the *start* of the run, what timestamp ends up in the file?
4. Why is `task_id` an `int` in `MBPPTaskInput` but a `str` in `SolutionOutput`?
5. A sandbox JSON contains `"timeout": 5` (a typo). What happens with the default config, and what would `extra="forbid"` change?

**Likely live modifications**
- Add a `temperature: float = 0.0` field to `StepMetrics` and see it appear in `solution.json`.
- Change the default memory limit to 256 MB. Is it the JSON or the code default that wins?
- Make `SandboxConfig` reject unknown keys.

**Interview talking points**
- *Say:* "I started from the output contract the grader validates, and I validate every input at the boundary with Pydantic, so bad data fails at load time with a precise message, not ten calls deep in the agent loop."
- *Hardest question:* "Why Pydantic instead of dataclasses, and how would you evolve the schema without breaking old solution files?"

**Risks and common mistakes**
- Renaming or "improving" subject field names. The grader then rejects the file.
- Mutable defaults.
- Forgetting to pin 3.10.
- Committing `.env`.
- Working in the wrong git root.

---

### Milestone 2: Sandbox policy layer (in-process)
**Owner** A · **Time** 5-7 h · **Subject** V.2(3) imports / filesystem / builtins, V.2 ".*" rule (page 16), V.2 red box (stdlib only), IV.2 configurable sandbox, VI exam_sandbox (import, builtin, path)

**Goal and why now.** Implement *what code may do* as small pure functions that can be unit-tested in-process. M3 moves the same code into a worker process unchanged. This is the core safety idea of the project and has no dependency except `SandboxConfig`.

**Concepts first**
- `compile` / `exec` / `eval`; the globals dict and its `__builtins__` key; why the namespace persists if you reuse the dict
- How `import` works: `__import__(name, globals, locals, fromlist, level)`, what `fromlist` and `level` mean, `sys.modules`
- Allowlist matching: exact `"re"` vs `"collections.*"`; why `"math.*"` must not match `"mathx"`
- Why Python-level sandboxes leak: the object graph (`().__class__.__base__.__subclasses__()`), and allowed modules exposing dangerous ones (`random._os`)
- The `ast` module: parse, walk, `ast.Attribute`, line numbers
- `os.path.realpath` vs `abspath`, symlinks, `..`, `os.path.commonpath`
- Output capture: `contextlib.redirect_stdout`, `io.StringIO`
- Tracebacks: trimming to the user's code (`filename="<sandbox>"`)
- REPL-style echo: run all statements, then `eval` the last expression and print its `repr`

**Steps**
2.1 `errors.py`: `SandboxSecurityError(Exception)` with a clear message.

2.2 `policy.is_import_allowed(name, allowlist)` + a table-driven test (`math` ✓, `math.foo` with `math.*` ✓, `collections.abc` with `collections.*` ✓, `os` ✗, `mathx` ✗).

2.3 `policy.make_guarded_import(allowlist)`. It handles `fromlist` entries that are submodules (`from collections import abc`) and rejects relative imports (`level > 0`).

2.4 `policy.make_safe_builtins(config)`: copy `builtins.__dict__`, replace the denylisted names with stubs that raise `SandboxSecurityError("builtin 'eval' is disabled in the sandbox")`, keep `__build_class__`.

2.5 `policy.make_guarded_open(allowed_directories)`: realpath, then check the path is inside an allowed directory, otherwise raise a security error naming the allowed dirs.

2.6 `policy.validate_ast(code)`: reject denylisted attribute names; report the line.

2.7 `runtime.execute(code, namespace) -> ExecResult(stdout, error, value_repr)`: AST check → compile → exec statements → echo the last expression; errors formatted without sandbox-internal frames.

2.8 `tests/test_sandbox_policy.py`: blocked and allowed matrices, persistence across two calls, echo.

**Files:** `agent_smith/sandbox/{__init__,errors,policy,runtime}.py`, `tests/test_sandbox_policy.py`

**Done when**
- `uv run pytest tests/test_sandbox_policy.py -q` green
- small demo (run with `uv run python -c ...` or a scratch script) using the template config:
  - `execute("import os", ns)` → `SandboxSecurityError: import of 'os' is not allowed`
  - `execute("open('/etc/passwd')", ns)` → `access to '/etc/passwd' denied (allowed: /testbed, /tmp/agent)`
  - `execute("eval('1+1')", ns)` → `builtin 'eval' is disabled in the sandbox`
  - `execute("x = 21", ns)` then `execute("x * 2", ns)` → value `42`
  - `execute("class A: pass\nA()", ns)` works

**Understanding checks**
1. Predict `from collections import abc` with allowlist `["collections"]` and with `["collections.*"]`.
2. `import random` works although `random` itself imports `os`. Why? And can the user reach `os` through it? (Yes: `random._os`. Which layer will stop that, and in which milestone?)
3. Why `realpath` and not `abspath`? Construct the symlink attack.
4. What breaks if you delete `__build_class__` from builtins?
5. Predict the output of `execute("1 + 1\n2 + 2", ns)`.

**Likely live modifications**
- Allow `numpy` (config only, no code change: this proves the sandbox is configurable).
- Block `sorted` or `print`.
- Add a second allowed directory.
- Change the wording of the import error.

**Interview talking points**
- *Say:* "Restrictions are layered: AST check, restricted builtins, import allowlist and path guard. They're policy, and they're unit-tested as pure functions before they go anywhere near a process."
- *Hardest question:* "Can you escape your sandbox?" Be honest: name the known escape classes, and say which layer catches each (the audit hook and process isolation come in M3/M3.5).

**Risks and common mistakes**
- Over-blocking (`__build_class__`, `__name__` missing from the namespace).
- `startswith` path checks (`/tmp/agent2` passes a naive `startswith("/tmp/agent")`).
- Forgetting `fromlist`.
- Swallowing errors instead of reporting them.

---

### Milestone 3: Sandbox isolation (worker process, IPC, timeout)
**Owner** A · **Time** 6-8 h · **Subject** V.2(3) execution timeout, V.2 green box (isolation approach), V.1 feedback "timeout and partial output", III.1 persistent variables, VI exam_sandbox (timeout)

**Goal and why now.** Untrusted code must be killable without killing the agent. Moving the M2 policy into a supervised child process makes timeouts and resource limits possible.

**Concepts first (new)**
- Process vs thread; why Python threads can't be killed
- `multiprocessing` start methods: fork vs spawn, and why fork is dangerous with threads and asyncio (we use spawn)
- `Pipe`, message framing, JSON vs pickle (D4)
- Signals: SIGALRM / `setitimer`, SIGTERM, SIGKILL, SIGINT going to the whole terminal process group
- `Process.join`, exit codes, zombie processes, orphans (the worker must exit when its pipe closes)
- Streaming output so partial output survives a kill

**Steps**
3.1 `protocol.py`: message shapes.
- controller → worker: `exec {code}`, `tool_result` (M5.5), `shutdown`
- worker → controller: `stdout {data}`, `done {ok, error, value, final_answer, control}`, `tool_call` (M5.5)

3.2 `worker.py`:
- ignore SIGINT (the parent decides)
- build namespace + policy from the config it receives
- loop: receive → `runtime.execute` with a stdout writer that sends chunks → send `done`
- exit on pipe EOF

3.3 `controller.py`: `Sandbox(config)` with `start()`, `execute(code) -> ExecutionResult`, `close()`, context-manager support.

3.4 Soft timeout in the worker: `setitimer` → `SandboxTimeout(BaseException)`; result marked `timed_out`, namespace kept.

3.5 Hard timeout in the controller: if there's no `done` within `timeout + grace`, kill → respawn → result marked `timed_out` and `namespace_reset`.

3.6 Partial output: the controller accumulates `stdout` chunks; on timeout the result contains them and an explicit note. Cap the total output size.

3.7 Worker death (EOF, non-zero exit) → respawn + explicit note.

3.8 `tests/test_sandbox_timeout.py`.

**Files:** `agent_smith/sandbox/{protocol,worker,controller}.py`, `tests/test_sandbox_timeout.py`

**Done when** `uv run pytest tests/test_sandbox_timeout.py -q` is green. The tests use a config with `max_execution_time_seconds=2`:
- `print("start")\nwhile True: pass` → returns in about 2 s, stdout `start`, note `Execution timed out after 2 s; output above is partial`
- `x = 1`, then a timeout, then `print(x)` → `1` (soft timeout kept the namespace)
- a C-level hang that ignores signals (try `re.match(r'(a+)+$', 'a'*40 + 'b')`; we verify together which example really ignores SIGALRM) → hard kill within `timeout + grace`; the next `print(x)` → `NameError`, with a note `sandbox restarted, variables were lost`
- after any of the above, `sandbox.execute("1+1")` → `2` (the sandbox stays usable)
- no leftover worker processes after `close()` (`pgrep -f agent_smith.sandbox.worker` is empty)

**Understanding checks**
1. Why can't `threading.Timer` stop runaway code?
2. Predict exactly what the LLM sees for `print("a"); while True: pass` with a 2 s timeout.
3. Why must the parent never unpickle data from the worker?
4. You press Ctrl+C in the terminal. Which processes receive SIGINT, and why does the worker ignore it?
5. Where is the namespace stored, and what happens to it on a soft vs a hard timeout?

**Likely live modifications**
- Set the timeout to 5 s (JSON only).
- Include the elapsed time in the timeout message.
- Change the grace period.
- Print a line on stderr every time the worker is respawned.

**Interview talking points**
- *Say:* "LLM code runs in a persistent child process. The parent is a supervisor with a two-stage kill switch: a soft timeout keeps the state, a hard kill-and-respawn handles code stuck in C, and the model is always told which one happened."
- *Hardest question:* "Why not just use Docker or seccomp for MBPP?" (stdlib-only constraint, latency per step; in production I'd layer OS isolation underneath)

**Risks and common mistakes**
- Spawn re-imports the main module (guard entry points).
- Deadlocks when a pipe fills up with huge output (stream it and cap it).
- Orphaned workers after a parent crash.
- Unclear timeout semantics when tool calls are involved (handled in M5.5).
- If spawn misbehaves with the console script, fall back to `subprocess.Popen([sys.executable, "-m", "agent_smith.sandbox.worker"])`.

---

### Milestone 3.5: Sandbox hardening
**Owner** A · **Time** 5-7 h · **Subject** V.2(3) no network / memory limits / restricted builtins, V.2(2) exception propagation, V.2 red box, VI exam_sandbox (network, memory, path, builtin), VI.4.1 (no bypass)

**Goal and why now.** Close the gaps that a Python-level policy can't: memory bombs, network access, process creation and object-graph escapes. Also make control-flow exceptions behave correctly. Ends with a suite that mirrors `exam_sandbox.sh`.

**Concepts first (new)**
- `resource.setrlimit`: `RLIMIT_AS` (virtual memory), `RLIMIT_CPU`, `RLIMIT_FSIZE`; `MemoryError` vs the process being killed
- PEP 578 audit hooks: `sys.addaudithook`, event names (`open`, `socket.connect`, `subprocess.Popen`, `os.system`, `os.exec`, `os.fork`, `os.remove`, ...); hooks can't be removed once added
- The exception hierarchy: `BaseException` → `Exception` vs `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`; why `except BaseException:` / bare `except:` are bugs here

**Steps**
3.5.1 Worker sets rlimits at startup from `max_memory_mb`, plus a CPU ceiling and a file-size ceiling.

3.5.2 `MemoryError` → clear message `memory limit of 512 MB exceeded`. Worker killed → respawn + note `worker died (likely memory limit)`.

3.5.3 Audit hook:
- pre-import the allowlisted modules first, then install the hook
- the hook is active only while user code runs (a flag), so interpreter internals aren't blocked
- it denies network, process creation, `ctypes`, and file events outside `allowed_directories`

3.5.4 Exception propagation:
- user code raising `SystemExit` / `KeyboardInterrupt` → the worker reports `control: "SystemExit"` → `Sandbox.execute` re-raises the same type in the parent
- Ctrl+C while the parent waits → kill the worker, re-raise
- never swallowed anywhere

3.5.5 `tests/test_sandbox_security.py`:
- import block
- builtin block
- network block: with `socket` added to the allowlist on purpose, `socket.create_connection(("1.1.1.1", 80))` is still blocked by the hook
- path restrict, including `..` and symlinks
- timeout
- memory (`bytearray(2 * 1024**3)`)
- escapes (`random._os.system("echo pwned")`, `__import__('os')`, the `().__class__` chain)
- `raise SystemExit(3)` propagates

**Files:** `agent_smith/sandbox/worker.py`, `agent_smith/sandbox/policy.py`, `agent_smith/sandbox/controller.py`, `tests/test_sandbox_security.py`

**Done when**
- `uv run pytest tests/test_sandbox_security.py -v` → every test green
- `random._os.system("echo pwned")` prints a security error and **not** `pwned`
- `[0] * (10**9)` → memory-limit message, then `1+1` → `2`
- `pytest.raises(SystemExit)` around `sandbox.execute("raise SystemExit(3)")` passes

**Understanding checks**
1. Why must the hook be installed *after* the pre-imports? What would happen otherwise?
2. Predict what the agent sees for `try:\n    raise KeyboardInterrupt\nexcept Exception:\n    print("caught")`.
3. Why `RLIMIT_AS` and not "check RSS every 100 ms"? What does each miss?
4. Name the layer that stops each of: `import socket`, `random._os.system("id")`, `open("/etc/passwd")`, `while True: pass`.

**Likely live modifications**
- Lower the memory limit to 128 MB and show the bomb failing earlier.
- Add `os.remove` to the denied events.
- Allow writes only inside `/tmp/agent`, read-only in `/testbed`.

**Interview talking points**
- *Say:* "Five layers. The audit hook is the backstop for escapes through allowed modules, and rlimits make memory bombs a clean error instead of a crashed machine."
- *Hardest question:* "The Python docs say audit hooks aren't a sandbox. Why rely on one?" (It's one layer behind process isolation; I know its limits, for example a C extension with raw syscalls, and I'd add seccomp/namespaces in production.)

**Risks and common mistakes**
- The hook blocks the interpreter's own imports or encodings, causing strange failures.
- `RLIMIT_AS` set below the interpreter's baseline.
- `RLIMIT_AS` isn't enforced on macOS (fine on WSL2/Linux; check the campus machines).

---

### Milestone 4: Sandbox REPL CLI and final_answer
**Owner** A · **Time** 3-4 h · **Subject** V.2(1) CLI and REPL behavior, V.2(2) final_answer, III figure point 4

**Goal and why now.** A human-facing entry point to the exact same `Sandbox` the agent will use, so everything can be tested by hand. Adds `final_answer`, which the loop needs.

**Concepts first**
- `[project.scripts]` console entry points
- `argparse` (optional positional, mutually exclusive flags)
- REPL mechanics: `codeop.compile_command` tells complete from incomplete input (the `...` continuation prompt)
- EOF (Ctrl+D) vs Ctrl+C; `sys.stdin.isatty()` for piped input
- `final_answer` as a control signal: `FinalAnswerSignal(BaseException)`, so `except Exception` in model code can't swallow it
- Exit codes

**Steps**
4.1 `cli.py`: `sandbox [config.json] [--mcp-stdio CMD | --mcp-server URL]` (the MCP flags are accepted but print "not yet supported" until M5.5); register `sandbox = "agent_smith.sandbox.cli:main"`.

4.2 Loop:
- `>>> ` / `... ` prompts
- `exit` command and EOF end the session cleanly (exit 0)
- after each entry, print the value, stdout or error

4.3 `final_answer(answer)` injected by the worker: stores the answer, raises `FinalAnswerSignal`; `ExecutionResult.final_answer` set. Non-string arguments → clear error, or conversion with a note (decide together).

4.4 REPL: on a final answer, print `Final answer:\n<answer>` and exit 0.

4.5 Ctrl+C: propagate (clean message, non-zero exit), consistent with V.2(2).

4.6 `tests/test_cli.py` drives the CLI with `subprocess.run(..., input=...)`.

**Files:** `agent_smith/sandbox/cli.py`, `agent_smith/sandbox/errors.py`, `agent_smith/sandbox/worker.py`, `pyproject.toml`, `tests/test_cli.py`

**Done when**
- `uv run sandbox` shows a prompt; typing `def f(x):`, `    return x * 2`, an empty line, then `f(21)` prints `42`
- `printf 'x = 2\nx * 21\nexit\n' | uv run sandbox` → prints `42`, exit code 0
- `printf 'import os\n' | uv run sandbox sandbox_template.json` → import error, then exit 0 at EOF
- `printf 'final_answer("def f(): return 1")\nprint("never")\n' | uv run sandbox` → prints the final answer, never prints `never`

**Understanding checks**
1. Why is `final_answer` not an MCP tool? What would break if it were?
2. Predict: `try:\n    final_answer("x")\nexcept Exception:\n    print("caught")`.
3. How does the REPL know `for i in range(3):` is incomplete?
4. What's the difference between the `exit` command and the (removed) `exit()` builtin?

**Likely live modifications**
- Change the prompt to `sandbox> `.
- Make `final_answer` print the answer's length.
- Add a `:manual` command that prints the sandbox manual (after M5.5).

**Interview talking points**
- *Say:* "The REPL and the agent share one `Sandbox` class, so what I test by hand is exactly what the model gets."
- *Hardest question:* "Why make `final_answer` raise instead of just setting a flag?" (It stops the rest of the block; `BaseException` beats `except Exception` in model code.)

**Risks and common mistakes**
- Prompts printed to stdout make piped output messy (acceptable, or print prompts only on a TTY).
- Forgetting `flush`.
- Treating the `exit` text inside a multi-line block as a command.

---

### Milestone 5: MCP fundamentals + MBPP MCP server
**Owner** A · **Time** 5-6 h · **Subject** V.2(4) MCP integration and root-level tool files, V.3(2) `run_tests`, III figure (MCP servers are separate processes, only your own), IV.2 tools independent of the loop, VI.4.1

**Goal and why now.** Understand the protocol on its own before wiring it through the worker. Ship the one MBPP tool the agent needs.

**Concepts first (new)**
- MCP: JSON-RPC 2.0 messages (request/response/notification, `id`), the `initialize` handshake and capabilities, `tools/list`, `tools/call`, `resources/list` / `resources/read`, `prompts/list` / `prompts/get`
- Transports: stdio (newline-delimited JSON over the child's stdin/stdout) vs streamable HTTP
- Why MCP: tools are decoupled from the agent, so the grader can plug in an unknown server
- asyncio primer: coroutine, event loop, `await`, `async with`, `asyncio.run`
- FastMCP: type hints + docstring → tool JSON Schema
- **In a stdio server, stdout belongs to the protocol**: debug with stderr/logging only
- A tool that runs model code must itself be sandboxed

**Steps**
5.1 `uv add mcp`. A 10-line hello server (scratch file, not committed); pipe a hand-written `initialize` line into it and read the reply, so you see the wire format.

5.2 `mcp_tools_mbpp.py`: FastMCP server.
- `run_tests(code: str, test_list: list[str]) -> str` returns `json.dumps({"success": bool, "output": str})`
- it executes `code` + each assertion in a **fresh sandbox worker** (M3 `Sandbox`) with a timeout
- output reports per-assertion pass/fail, compactly

5.3 Expose at least one resource (e.g. `mbpp://guidelines`) and one prompt (e.g. `solve_mbpp`), to cover "tools, resources, and prompts must be exposed".

5.4 `--transport` option: stdio (default) or streamable HTTP with host/port (used in M5.5).

5.5 `scripts/mcp_probe.py`: async stdio client. It lists tools, resources and prompts, calls `run_tests` with a correct and a wrong solution, prints the results.

5.6 `tests/test_mcp_mbpp.py`: call the tool function directly (independent of the loop) and through a stdio session.

**Files:** `mcp_tools_mbpp.py`, `scripts/mcp_probe.py`, `tests/test_mcp_mbpp.py`, `pyproject.toml`

**Done when**
- `uv run python scripts/mcp_probe.py "python mcp_tools_mbpp.py"` prints:
  - the tool list with `run_tests` and its input schema (`code: string`, `test_list: array`)
  - for `def add(a, b): return a + b` with `["assert add(1, 2) == 3", "assert add(0, 0) == 0"]` → `{"success": true, "output": "2/2 assertions passed"}`
  - for `def add(a, b): return a - b` → `success: false` and the failing assertion text
  - for `while True: pass` → `success: false` and a timeout message within the configured seconds
- `uv run pytest tests/test_mcp_mbpp.py -q` green

**Understanding checks**
1. What's in the `initialize` request and response? Why is there a handshake at all?
2. Predict what happens if you add `print("debug")` inside `run_tests` in a stdio server.
3. How does FastMCP know `test_list` is an array of strings?
4. Why does `run_tests` use a sandbox worker instead of `exec`?

**Likely live modifications**
- Add a `check_syntax(code)` tool.
- Make `run_tests` stop at the first failing assertion.
- Change the `run_tests` timeout.

**Interview talking points**
- *Say:* "Tools live in a separate MCP server process. The agent only knows the protocol, so swapping or adding tool servers needs no agent changes, and the grader tests that with a server I've never seen."
- *Hardest question:* "`run_tests` executes model-written code outside your sandbox. Isn't that a bypass?" (It isn't: the tool runs it in its own restricted worker.)

**Risks and common mistakes**
- `print` in a stdio server.
- MCP SDK API differences between versions (pin it; read the docs of the pinned version).
- `test_imports` from the task (prepend them to the code being tested).
- Assertions that hang.

---

### Milestone 5.5: MCP inside the sandbox
**Owner** A · **Time** 6-8 h · **Subject** V.2(4) callable as Python functions / unknown server / stdio + streamable HTTP, V.2(5) manual, V.2 page 16 (dynamic discovery, independent security domains), V.2(1) `--mcp-stdio` / `--mcp-server`, V.1 feedback "tool output truncated", VI exam_sandbox "MCP protocol"

**Goal and why now.** Code-as-action: the model calls `run_tests(...)` like a normal Python function inside the sandbox. This completes the sandbox component described in the subject.

**Concepts first (new)**
- Sync/async bridge: event loop in a background thread, `asyncio.run_coroutine_threadsafe`, `concurrent.futures.Future.result(timeout)`, shutdown order, deadlock patterns
- RPC through the worker pipe: worker-side stubs block until the controller answers
- JSON Schema → `inspect.Signature` / `inspect.Parameter` (names, defaults, required); mapping positional args to schema order
- Tool names that aren't valid identifiers (`get-weather` → `get_weather`); protecting `final_answer` from being shadowed
- Tool results: content list, text content, `structuredContent`, `isError`
- Truncation with an explicit marker; streamable HTTP endpoint (`/mcp`)

**Steps**
5.5.1 `mcp_client/bridge.py`: `MCPBridge.connect_stdio(command)` / `connect_http(url)`, `list_tools()`, `call_tool(name, args)`, `list_resources()`, `read_resource(uri)`, `list_prompts()`, `get_prompt(name, args)`, `close()`. All are sync, backed by the background loop.

5.5.2 Controller: on `start()`, fetch the specs and send them to the worker. On `tool_call`, pause the timeout clock, call the bridge, send `tool_result`, resume the clock.

5.5.3 Worker: build stubs with real signatures and docstrings; inject `list_resources`, `read_resource`, `list_prompts`, `get_prompt` helpers; refuse to overwrite `final_answer`.

5.5.4 Truncation: tool output and stdout longer than N chars → head + tail + `[output truncated: 2,000 of 15,342 chars shown]`.

5.5.5 `sandbox/manual.py`: generate a compact manual from the specs (signature line + one-line description per tool, `final_answer`, allowed imports, limits). Token count matters for MBPP.

5.5.6 CLI: wire `--mcp-stdio "python mcp_tools_mbpp.py"` (`shlex.split`) and `--mcp-server URL`.

5.5.7 `tests/fixtures/dummy_server.py`: an "unknown" server with odd tool names and types. Test that the wrappers and the manual adapt and that `run_tests` is absent.

5.5.8 Tests for: a tool call during execution, the timeout pause, an error result, truncation, HTTP.

**Files:** `agent_smith/mcp_client/{__init__,bridge,schema}.py`, `agent_smith/sandbox/{controller,worker,manual,cli}.py`, `tests/fixtures/dummy_server.py`, `tests/test_mcp_sandbox.py`

**Done when**
- `uv run sandbox --mcp-stdio "python mcp_tools_mbpp.py" sandbox_template.json`, then `print(run_tests("def add(a,b): return a+b", ["assert add(1,2)==3"]))` → `{"success": true, ...}`
- in terminal 1, `uv run python mcp_tools_mbpp.py --transport http --port 8765`; in terminal 2, `uv run sandbox --mcp-server http://127.0.0.1:8765/mcp`, then the same call → same result
- with `--mcp-stdio "python tests/fixtures/dummy_server.py"`, the manual lists the dummy tools, not `run_tests`, and `final_answer` is still present
- a tool returning 100k chars → truncation marker in the output
- `uv run pytest tests/test_mcp_sandbox.py -q` green; no leftover server processes after exit

**Understanding checks**
1. Why does the controller own the MCP connection and not the worker?
2. A tool call takes 40 s and the sandbox timeout is 30 s. What happens, and why is that correct per the subject?
3. Predict the manual line for a tool `search(query: str, limit: int = 5)`.
4. An unknown server exposes a tool named `final_answer`. What does your sandbox do?
5. Draw the full path of one `run_tests` call, from the model's text to the MCP server and back.

**Likely live modifications**
- Change the truncation limit.
- Add parameter types and defaults to the manual.
- Prefix every MCP tool with `mcp_`.
- Log each tool call with its duration to stderr.

**Interview talking points**
- *Say:* "Tools discovered over MCP become real Python functions in the sandbox, so the model can loop and branch over tool results inside one step. That's the main advantage of code actions over JSON tool calls."
- *Hardest question:* "How do you call an async library from synchronous code without deadlocks?"

**Risks and common mistakes**
- Calling the bridge from inside its own loop thread (deadlock).
- A zombie stdio server on exit.
- HTTP path mismatch.
- A manual that eats the MBPP token budget.

---

### Milestone 6: LLM provider layer
**Owner** B · **Time** 5-6 h · **Subject** IV.2 multiple providers / usage tracking, V.6 free tiers / multiple keys / rotation / abstraction / stop sequences / fallback, VI (env vars, `.env`), VI.2 internal retries in `StepMetrics.retries`, VI.3 no hardcoded keys

**Goal and why now.** The loop needs a reliable "text in, text + usage out" call. It's independent of the sandbox, so it can be built and tested with fakes.

**Concepts first**
- OpenAI-compatible chat completions: `messages` with roles, `max_tokens`, `temperature`, `stop`, `usage.prompt_tokens` / `completion_tokens`, `finish_reason`
- HTTP status codes: 429 (rate limit, `Retry-After`), 401/403 (bad key), 5xx; connect and read timeouts
- Exponential backoff with jitter; why jitter matters
- Key rotation strategies and cooldowns
- `typing.Protocol` for abstraction; dependency injection; fake clients
- `httpx.MockTransport` for tests
- `time.perf_counter` for latency
- Free-tier realities (requests/minute, tokens/day, model churn); reasoning tokens count toward limits

**Steps**
6.1 `llm/base.py`: `ChatMessage`, `LLMResponse(text, input_tokens, output_tokens, latency_ms, retries, requests, api_url, model, finish_reason)`, `LLMClient` Protocol, error types (`LLMError`, `AllKeysExhausted`).

6.2 `configs/providers.json` + `ProviderConfig` (`extra="forbid"`): `name`, `base_url`, `key_env_vars` (list). Resolve the provider from `--provider-url`.

6.3 `llm/keys.py` KeyPool:
- `next_key()`
- `report_rate_limited(key, retry_after)`
- `report_invalid(key)`
- wait-or-raise when all keys are cooling, bounded by a deadline
- keys masked in every log line

6.4 `llm/openai_compat.py`: build the payload, send with timeout, parse usage (if usage is missing, estimate it and mark the estimate), retry loop with a per-call retry count.

6.5 `llm/fake.py`: `FakeLLM(scripted_responses)` for loop tests.

6.6 `scripts/llm_ping.py --provider-url U --model-name M`: one real call printing text, tokens, latency, retries.

6.7 `tests/test_llm.py`: MockTransport scenarios: success; 429 on key 1 → key 2 succeeds (`retries == 1`); 401 disables a key; all keys exhausted → `AllKeysExhausted`; 500 → backoff → success.

**Files:** `agent_smith/llm/{__init__,base,keys,openai_compat,fake}.py`, `configs/providers.json`, `.env.example`, `scripts/llm_ping.py`, `tests/test_llm.py`

**Done when**
- `uv run python scripts/llm_ping.py` works against **two different providers** (prints text, input/output tokens, latency in ms)
- `uv run pytest tests/test_llm.py -q` green, including rotate-on-429 and all-keys-exhausted
- `git grep -nE "(sk-|gsk_|AIza)[A-Za-z0-9_-]{10,}"` → no output

**Understanding checks**
1. Predict: two keys, both return 429 with `Retry-After: 5`, and the deadline is 3 s away. What does the client do?
2. What exactly does `StepMetrics.retries` count, and how does `total_requests` relate to it?
3. Why add jitter to the backoff?
4. Where would you add a third provider? (Answer should be: config only.)

**Likely live modifications**
- Change the max retries.
- Add a provider via `providers.json` only.
- Print which (masked) key served each request.
- Change the request timeout.

**Interview talking points**
- *Say:* "One OpenAI-compatible client covers most free providers. Switching provider is a config change. Rotation and retries are isolated in a KeyPool that's fully tested with a mocked transport, so no real 429 is needed."
- *Hardest question:* "Is a 429 per key, per model or per account, and how does your rotation behave in each case?"

**Risks and common mistakes**
- Free models disappear or change limits (keep ≥ 3 providers ready).
- Providers that reject or cap `stop` sequences.
- Missing `usage` in responses.
- Reasoning models silently eating the token budget.
- Logging a key.

---

### Milestone 7: Code extraction layer
**Owner** B · **Time** 4-5 h · **Subject** V.1(2) and the formats box (a-d, conversion to Python), V.1 feedback "no valid code block" / "malformed but interpreted"

**Goal and why now.** A pure, heavily tested function between the LLM and the sandbox. Needed by the loop, independent of everything else.

**Concepts first**
- Regex: non-greedy matching, `re.DOTALL`, anchors
- Why parser order matters
- `xml.etree.ElementTree` on an extracted fragment; `json.loads` and its failure modes
- `repr()` for safe code generation (no injection)
- `ast.parse` as a syntax pre-check
- How stop sequences shape the text you receive (the stop string is removed)

**Steps**
7.1 `ExtractionResult(code: str | None, fmt: str, notes: list[str])`.

7.2 Python fences: ```` ```python ````, ```` ```py ````, bare ```` ``` ````; strip `<end_code>`.
- several blocks → join them in order, with a note
- missing closing fence → take the rest, with the note `missing closing fence, interpreted everything after the opening fence as code`
- cut anything after a hallucinated `Observation:`

7.3 XML `<invoke name="..."><parameter name="...">...</parameter></invoke>` → call.

7.4 Hermes `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` (arguments may be a dict or a JSON string) → call.

7.5 ReAct `Action: tool` / `Action Input: {...}` → call.

7.6 Converter: `result = name(k=repr(v), ...)\nprint(result)`; `final_answer` is special-cased (no print).

7.7 Syntax pre-check → a note with the line number (the sandbox still reports the real error).

7.8 `tests/test_extraction.py`: table-driven, ≥ 20 realistic outputs (several models' styles, edge cases).

**Files:** `agent_smith/agent/{__init__,extraction}.py`, `tests/test_extraction.py`

**Done when** `uv run pytest tests/test_extraction.py -v` → ≥ 20 cases green, including:
- plain prose → `code=None`, with a reason
- an unclosed fence → code plus the "missing closing fence" note
- `<tool_call>{"name":"run_tests","arguments":{"code":"x = '\"'","test_list":[]}}</tool_call>` → `result = run_tests(code='x = \'"\'', test_list=[])` (valid Python, injection-safe)
- a ReAct block → the equivalent call
- text with a fake `Observation:` after the code → the observation is dropped

**Understanding checks**
1. Why `repr()` and not an f-string with quotes? Build the input that breaks the f-string version.
2. Predict the extraction for a reply containing a code block, then `Observation: 42`, then a second code block.
3. The model puts ```` ```python ```` inside a string in its code. What does your regex do?
4. Which format wins if a reply contains both a python fence and a `<tool_call>`? Why that order?

**Likely live modifications**
- Accept ```` ```Python ```` (case-insensitive).
- Add a `<code>...</code>` format.
- Switch the multi-block policy to "first block only".

**Interview talking points**
- *Say:* "Extraction is a pure function, text in and code plus diagnostics out, so it's the most heavily tested part. Every non-Python format is normalized to Python so the sandbox stays format-agnostic."
- *Hardest question:* "How do you avoid executing code the model only meant as an example?"

**Risks and common mistakes**
- Greedy regexes.
- Nested backticks.
- JSON with single quotes or trailing commas.
- Overfitting to one model's style.

---

### Milestone 7.5: Agent loop, system prompt, budgets, SolutionOutput
**Owner** B · **Time** 6-8 h · **Subject** V.1(1,3,4,6), V.1 feedback box, V.1.1 (all fields + info box), V.3(4) limits and configurable `max_iterations`, VI.1 (cumulative tokens, reasoning tokens, hard kill), IV.1 graceful errors, IV.2 own loop, VI.4.1 traceability

**Goal and why now.** Everything comes together: Thought → Code → Observation with honest metrics, within limits, never crashing.

**Concepts first**
- The ReAct / CodeAct idea; conversation state as a list of messages; roles
- Prompt design: role, response format with slots, tool docs (the manual), rules, one tiny example loop
- Budget accounting: cumulative input/output tokens; estimating the next call *before* sending it; `max_tokens` = remaining output budget; wall-clock deadline below the grader's kill
- Observation formatting and truncation
- Atomic writes (`os.replace`)
- Failure taxonomy: no code, syntax error, runtime error, timeout, tool error, budget exhausted, provider down

**Steps**
7.5.1 `AgentConfig` (`extra="forbid"`) + `configs/agent_mbpp.json` with margins: `max_iterations 10`, `max_input_tokens 5500` (limit 6000), `max_output_tokens 1400` (limit 1500), `deadline_seconds 100` (limit 120), `observation_max_chars`, `temperature 0`, `stop`.

7.5.2 `agent/budget.py`: `Budget.can_afford(estimated_input)`, `record(response)`, `remaining_output()`, `time_left()`.

7.5.3 `agent/prompts.py`:
- compact system prompt: format = `Thought:` + one python block + `<end_code>`
- rules: test with `run_tests(solution, test_list)`; submit with `final_answer(solution_code_string)`; keep the exact function name from `function_definition`; no internet; only the listed imports
- inject the manual
- the one-step solve-test-submit example (D10)
- measure its token count

7.5.4 `agent/loop.py` `Agent(llm, sandbox, config).run(system_prompt, task_message) -> SolutionOutput`:
- each step: budget check → call → extract → (feedback | execute) → observation
- build a `StepMetrics` with **all** fields (`llm_output` raw, `sandbox_input` = the executed code or `""`, `sandbox_output`, `retries`, `api_url`, `model_name`, timing)
- pass the consistency checks in `moulinette_eval display`, which reviewers use:
  - `llm_output` is never empty
  - `sandbox_output` is empty whenever `sandbox_input` is empty, so "no code block" feedback goes into the conversation, not into `sandbox_output`
  - timestamps are in order
  - every step has the same `model_name`
  - no two consecutive steps have identical `sandbox_input`: detect a repeat and tell the model instead of re-running it

7.5.5 The five feedback messages: no code block; malformed but interpreted (say how); timeout with partial output; truncated output; tool/edit errors passed through as-is.

7.5.6 Termination and semantics:
- `final_answer` → `success=True`, `solution=answer`
- max iterations / budget / deadline → `success=False`, best candidate if any, `error` explains why

7.5.7 `agent/output.py`: totals (`iterations`, `total_requests` = Σ(1 + retries), token sums, `total_time_seconds`), `system_prompt`, atomic write after every step.

7.5.8 Top-level guard: any exception → valid `solution.json` with `error`; `KeyboardInterrupt`/`SystemExit` → write the file, then re-raise.

7.5.9 `tests/test_agent_loop.py` with `FakeLLM`: solve in one step; no-code then code; budget stop; provider failure → error in the output.

**Files:** `agent_smith/agent/{prompts,budget,loop,output}.py`, `agent_smith/models.py` (AgentConfig), `configs/agent_mbpp.json`, `tests/test_agent_loop.py`, `tests/fixtures/mbpp_sample.json`

**Done when**
- `uv run pytest tests/test_agent_loop.py -q` green
- a small script (`scripts/run_one.py`) solves `tests/fixtures/mbpp_sample.json` with a real model, and `../cache/sample_solution.json` validates: `uv run python -c "from agent_smith.models import SolutionOutput as S; s=S.model_validate_json(open('../cache/sample_solution.json').read()); print(s.success, s.iterations, s.total_input_tokens, s.total_output_tokens)"` → `True`, small iteration count, ≤ 6000, ≤ 1500
- every step in the file has non-empty `llm_output`, `api_url`, `model_name`
- `cd ../moulinette && uv run moulinette_eval display ../cache/sample_solution.json` → `All checks passed`

**Understanding checks**
1. Predict `total_input_tokens` for 3 iterations if the system + task = 700 tokens and each answer + observation adds 400.
2. Why write `solution.json` after every step, and why atomically?
3. When exactly is `success` true? Is "tests passed but no `final_answer`" a success?
4. What goes into `sandbox_input` when no code was found?
5. Where would you change the stop sequences, and what breaks if you remove them?

**Likely live modifications** (the most likely place for the 42 review's MBPP modifications)
- Set `max_iterations` to 3 and re-run.
- Print the token usage per step to stderr.
- Add a sentence to the system prompt and show it appear in `system_prompt`.
- Change the observation truncation.
- Make the agent refuse to submit when `run_tests` didn't pass.

**Interview talking points**
- *Say:* "The loop itself is about a hundred lines. The engineering is in the budget accounting (tokens are cumulative, so every step resends history), explicit feedback for every failure mode, and writing a truthful trace after every step."
- *Hardest question:* "How do you stop the model from hallucinating observations, and how would this scale to 30 iterations with large observations?"

**Risks and common mistakes**
- A system prompt that eats the budget.
- Double-counting retries.
- Counting failed requests' tokens wrongly.
- Building `SolutionOutput` at the start (wrong timestamp).
- Accepting a `final_answer` that isn't a string of code.

---

### Milestone 8: MBPP CLI, moulinette integration, tuning
**Owner** B · **Time** 4-8 h · **Subject** V.3(1) CLI and task loading, V.1.2 development approach, VI exam_mbpp, VI.1.1 limits, VI.2 pass criteria, VI.5 logging structure, VI.1 SIGTERM handling

**Goal and why now.** The real pipeline the grader runs: dump → run → validate. Then measure and fix failures.

**Concepts first**
- Exact CLI contract (`--task-file`, `--output`, `--model-name`, `--provider-url`)
- Reproducibility (`temperature 0`)
- Measuring a pass rate honestly; reading `solution.json` traces to diagnose failures
- Choosing a model under tight limits (non-reasoning, fast, follows the format)
- Signal handlers (`signal.signal(SIGTERM, ...)`) and process-group cleanup
- Batch runs and log capture

**Steps**
8.1 `agent_mbpp/__main__.py`:
- argparse: `--task-file` and `--output` are required. `--model-name` and `--provider-url` take defaults from config, because `quickstart.sh` leaves them out when you don't pass them. Optional extras: `--config`, `--max-iterations`, `--env-file`.
- load `.env`
- wire SandboxConfig + `Sandbox(--mcp-stdio "python mcp_tools_mbpp.py")` + LLM + Agent
- write the output. Exit code 0 whenever `solution.json` was written, even if the task wasn't solved: `run-agent` passes a non-zero exit on as a failure, and `quickstart.sh` stops there (`set -e`).

8.2 Facts confirmed from the moulinette source:
- the dump is exactly `MBPPTaskInput` JSON; the reference solution is not included
- **the first test is hidden.** `test_list` holds tests 2..n only, and validation runs all of them, so the solution must be general, not fitted to the visible asserts
- the dump drops `test_imports` when a task has exactly one. Tell the model to put any needed imports inside its solution
- validation runs `solution + test_imports + tests` with `python -c` in Docker (`python:3.11-slim`, 128 MB, no network, 30 s). The solution must not read input or run heavy code at import time

8.3 SIGTERM handler. `run-agent` sends SIGTERM to the whole process group (agent, worker and MCP server at once), waits 10 s, then sends SIGKILL. The handler writes the current `solution.json` and exits quickly; it doesn't wait for children that are already dying.

8.4 Run one task end-to-end; validate.

8.5 `scripts/run_mbpp_batch.sh N`: dump / `moulinette_eval run-agent 120 "..."` / validate N tasks into `evaluations/mbpp/<timestamp>/<task_id>/{task.json,solution.json,stdout.log,stderr.log}` (the VI.5 layout; gitignored).

8.6 Run ≥ 10 tasks with 2 models from 2 providers. Record the pass rate, average iterations and tokens in INTERVIEW_NOTES.md.

8.7 Failure analysis loop: read the traces, then fix the prompt, extraction or feedback (not overfitting to one task).

**Files:** `agent_mbpp/__init__.py`, `agent_mbpp/__main__.py`, `pyproject.toml` (packages), `scripts/run_mbpp_batch.sh`

**Done when**
- this sequence prints PASS:
  ```
  cd ../moulinette && uv run moulinette_eval dump mbpp --output ../cache/mbpp_task.json
  cd ../student && uv run python -m agent_mbpp --task-file ../cache/mbpp_task.json --output ../cache/mbpp_solution.json --model-name "<model>" --provider-url "<url>"
  cd ../moulinette && uv run moulinette_eval validate mbpp ../cache/mbpp_task.json ../cache/mbpp_solution.json
  ```
- `cd ../moulinette && ./quickstart.sh mbpp --student-path ../student --model-name "<model>" --provider-url "<url>"` → `Overall: PASSED` (it wraps dump, `run-agent 120` and validate)
- `scripts/run_mbpp_batch.sh 10` → ≥ 8/10 PASS (margin above the 4/5 requirement), every solution within 10 iterations / 6000 input / 1500 output / 120 s
- `cd ../moulinette && uv run moulinette_eval run-agent 5 "uv run python -m agent_mbpp --task-file ../cache/mbpp_task.json --output ../cache/mbpp_solution.json"` (forced kill) still leaves a valid `solution.json` and no leftover processes

**Understanding checks**
1. Predict what the validator says if the agent finishes without `final_answer`.
2. Your pass rate is 6/10. Walk through how you'd find the cause using only `solution.json`.
3. Why is temperature 0 not a guarantee of identical runs?
4. What happens to the worker and the MCP server when the grader sends SIGKILL?

**Likely live modifications** (the 42 review: "small live modifications to your agent and re-run it on an MBPP task", VI.4)
- Stop after 2 iterations.
- Print the model's Thought to stderr.
- Switch model via the CLI.
- Add a field to `StepMetrics`.
- Make every observation start with `Observation:`.
- Revert everything with `git checkout .`.

**Interview talking points**
- *Say:* concrete numbers, e.g. "X/10 MBPP tasks with model M at an average of Y iterations and Z input tokens, under a 6k cumulative budget."
- *Hardest question:* "How do you know the agent didn't just memorize MBPP?" (The trace shows real execution; tests run in the sandbox; honest limits.)

**Risks and common mistakes**
- The moulinette is still missing.
- Rate limits mid-batch.
- A flaky model.
- Tuning the prompt on the same 10 tasks you report.

---

### Milestone 8.5: Interview preparation
**Owner** Shared · **Time** 6-8 h · **Subject** II (AI transparency), VI.4 (live modifications), VII (README in English; the 42 format comes later)

**Goal and why now.** Turn the working system into a clear story you can tell and defend without notes.

**Concepts first**
- The STAR structure for project stories
- Explaining trade-offs (option, cost, why we chose it)
- System-design vocabulary: boundaries, failure modes, observability
- Being honest about AI use and solo work

**Steps**
8.5.1 Claude consolidates INTERVIEW_NOTES.md (decisions D1-D14, numbers, talking points per milestone); Amin reviews and rewrites it in his own words.

8.5.2 Draw the architecture (A.1) and the data flow (A.2) from memory, three times.

8.5.3 A 2-minute pitch and a 10-minute deep dive, rehearsed out loud.

8.5.4 Demo script:
- REPL security demo: import block, path block, timeout, memory, `final_answer`
- one live MBPP run with the trace

8.5.5 Personal README (Amin writes it; Claude reviews): what it is, architecture, how to run, design decisions, results, honest credits ("built solo; teammate unavailable"; AI usage).

8.5.6 Mock interview: Claude asks 15 questions (including the "hardest questions" above) and rates the answers.

8.5.7 Live-modification drill: 10 exercises from the lists above, 5 minutes each, `git checkout .` after each.

8.5.8 "What I'd do next": SWE-bench, OS-level isolation, context management, evals at scale.

**Files:** `INTERVIEW_NOTES.md`, `README.md` (Amin), `notes/M8.5.md`

**Done when**
- the architecture is drawn and one run is explained end-to-end without notes
- the 2-minute pitch is ≤ 2:15
- ≥ 8/10 drills are finished within 5 minutes
- from a fresh clone, `git clone <repo> /tmp/as && cd /tmp/as && uv sync && uv run pytest -q && uv run sandbox` works

**Understanding checks:** the mock interview is the check. Each "hardest question" from M1-M8 must get a confident answer.

**Likely live modifications:** the drill list above.

**Interview talking points**
- *Say:* "I built a code agent from scratch: my own loop, a process-isolated sandbox, MCP tool servers, and multi-provider LLM access with key rotation, solving MBPP under strict token budgets."
- *Hardest question:* "What was the hardest bug, and what would you redesign?"

**Risks and common mistakes**
- Memorized answers without understanding (the explain-back checks guard against this).
- Overclaiming in the README.

---

### Milestone 9: SWE-bench tools + Docker bridge (Phase 2)
**Owner** Split (A: file/search tools, B: execution tools + Docker) · **Time** 10-14 h · **Subject** V.4 (approach a/b, TESTBED_PATH, git diff, container dependencies, SWEBenchTaskInput), V.5.1-V.5.3 (9 tools with formats), V.1 feedback "edit introduced a syntax/lint error", IV.2 tools independent, VI.3 tools pass independent tests

**Goal and why now.** Phase 2 starts with tools, because the grader tests each one in isolation, and the agent is only as good as its tools.

**Concepts first (new)**
- Docker: image vs container, `docker run -d`, `exec`, `cp`, `rm -f`, labels, volumes
- Docker Desktop on WSL2
- SWE-bench task anatomy: `/testbed`, conda env `testbed`, `eval_script` (FAIL_TO_PASS / PASS_TO_PASS)
- The executor abstraction (D13)
- grep / ripgrep semantics
- `ast` for finding definitions; reference finding (grep `-w` vs jedi)
- `git -c core.fileMode=false diff`
- Lint after edit (`py_compile`, ruff)
- Path normalization and escape prevention

**Steps**
9.0 Spike (decision gate):
- `docker pull` the image for `sympy__sympy-14711`
- inspect `/testbed`, the Python versions (`python`, `/opt/miniconda3/bin/python`) and `docker exec` latency
- read the moulinette's isolated tool tests (how TESTBED_PATH and run_tests are used)
- write the decision D13 into INTERVIEW_NOTES.md

9.1 `SWEBenchTaskInput` in `models.py` (page 19-20, exact fields).

9.2 `swebench/executor.py`: `Executor.run(cmd, workdir, timeout, stdin) -> (stdout, stderr, exit_code)`; `LocalExecutor(TESTBED_PATH)`, `DockerExecutor(container)`.

9.3 Path normalization: accept `/testbed/...`, `TESTBED_PATH/...` and relative paths; reject escapes outside the repo.

9.4 The six file and search tools:
- `read_file(filepath, start_line, end_line)` → `N: line`
- `edit_file(filepath, old_str, new_str)`: exactly one match, else a clear error with a hint; after editing `.py` files, run a syntax/lint check and append any problem
- `list_files(directory, pattern)`
- `search_code(pattern, file_pattern)` → `/abs/path:line content`, capped with a truncation note
- `search_function_or_class_definition_in_code(name)`
- `find_references(name, filepath, line)`

9.5 The three execution tools:
- `run_tests()`: the eval script; summarized tail
- `get_patch()`: `git -c core.fileMode=false diff`
- `run_command(command, workdir)`: stdout, stderr, exit_code, timeout

9.6 `mcp_tools_swebench.py` at the root exposing all nine (+ a resource with the task statement, + a prompt).

9.7 `tests/fixtures/mini_repo/` + `tests/test_swebench_tools.py`, with one test per tool through `TESTBED_PATH`.

**Files:** `agent_smith/models.py`, `agent_smith/swebench/{executor,paths,tools}.py`, `mcp_tools_swebench.py`, `tests/fixtures/mini_repo/`, `tests/test_swebench_tools.py`

**Done when**
- `TESTBED_PATH=$PWD/tests/fixtures/mini_repo uv run pytest tests/test_swebench_tools.py -v` → 9 tool groups green
- `TESTBED_PATH=$PWD/tests/fixtures/mini_repo uv run sandbox --mcp-stdio "python mcp_tools_swebench.py" sandbox_template.json`, then `print(search_code("def ", "*.py"))` → lines in the `/abs/path:line content` format
- the same tools run against a live container through `DockerExecutor`

**Understanding checks**
1. Why must `edit_file` fail when `old_str` occurs twice?
2. Predict the `read_file` output format for lines 3-4 of a 2-line file.
3. Why `core.fileMode=false`?
4. How does `find_references` avoid matching `name` inside `rename`?

**Likely live modifications**
- Change the `search_code` result cap.
- Add context lines to `read_file`.
- Make `run_command` refuse `rm -rf`.

**Interview talking points**
- *Say:* "Tools are plain functions over an executor interface, so the same code is unit-tested locally and runs inside Docker for the real task."
- *Hardest question:* "Why not give the agent just `run_command` and let it grep itself?" (Tokens, output formats, safety, reliability.)

**Risks and common mistakes**
- Image size and disk usage on WSL2.
- Container Python too old for in-container tooling.
- Path confusion between host and container.
- Unbounded grep output.

---

### Milestone 10: SWE-bench agent, limits, cleanup (Phase 2)
**Owner** B · **Time** 10-15 h · **Subject** V.4 (CLI, cleanup, constraints enforced, hard limits, final_answer(get_patch())), VI.1.2 limits, VI.1 SIGTERM/SIGKILL + signal handler, VI exam_swebench, V.6 (SWE-bench Verified research), VI.4.1 AI safety

**Goal and why now.** Reuse the whole framework on real bugs, with bigger budgets and long conversations.

**Concepts first**
- Container lifecycle and idempotent cleanup (`finally`, `atexit`, SIGTERM handler; labels for stale containers after SIGKILL)
- Context management (eliding old observations, keeping recent ones in full)
- An exploration methodology (reproduce → locate → read → edit → test → patch)
- Researching SWE-bench Verified agent designs and leaderboards (V.6)
- AI-safety rules: no fetching PRs or issues, no memorized patches without exploration

**Steps**
10.1 `agent_swebench/__main__.py` (same flags as MBPP).

10.2 Container manager:
- start with `--label agent-smith=1`
- clean stale labeled containers on startup
- cleanup on every exit path, fast enough to finish inside `run-agent`'s 10 s grace after SIGTERM

10.3 MCP server wired to the container (D13).

10.4 `configs/agent_swebench.json`: 30 iterations, 300k input, 10k output, a deadline around 840 s of the 900 s limit.

10.5 SWE-bench system prompt; `final_answer(get_patch())`.

10.6 Context trimming policy.

10.7 The exam pool is fixed in the moulinette (`EXAM_POOL`, 6 tasks): django__django-11066, pydata__xarray-4629, scikit-learn__scikit-learn-13439, sympy__sympy-13480, sympy__sympy-18189, sympy__sympy-14711.
- start with the subject's three suggestions, then cover all six
- pre-pull the images with `uv run moulinette_eval warmup`
- validation applies the patch in a fresh container (`git apply`, falling back to `patch --fuzz=5`) and requires FULL resolution: every FAIL_TO_PASS and PASS_TO_PASS test must pass
- the solution must contain diff markers (`diff --git`, `@@`)

10.8 Measure the "remove unused tools" question from the subject.

**Files:** `agent_swebench/__main__.py`, `agent_smith/swebench/docker.py`, `agent_smith/agent/prompts.py`, `configs/agent_swebench.json`, `tests/test_container_cleanup.py`

**Done when**
- dump → run → validate on the 3 tasks gives ≥ 2/3 PASS within limits
- `docker ps -a --filter label=agent-smith` is empty after each run, also after `timeout -s TERM 30 uv run python -m agent_swebench ...`

**Understanding checks**
1. SIGTERM arrives while `docker exec` runs a test. Trace what your code does.
2. Why can't SIGKILL be handled, and how do labels help anyway?
3. Predict the token cost of keeping all observations vs keeping the last 5.

**Likely live modifications**
- Change the trimming window.
- Remove a tool and compare.
- Print container IDs on start and stop.

**Interview talking points**
- *Say:* "Same loop, sandbox and MCP layer; only tools, prompt and budgets change. That's the payoff of the architecture."
- *Hardest question:* "How do you prove the agent didn't recall the upstream fix from training data?" (Trace review, exploration steps, `system_prompt` provenance.)

**Risks and common mistakes**
- Container leaks.
- Quota exhaustion on long runs.
- Very slow test scripts.
- Memorized patches flagged by reviewers.

---

### Milestone 11: Benchmark report (Phase 2)
**Owner** Shared · **Time** 8-12 h + quota waits · **Subject** V.7 (all six report sections, 5 models, ≥ 2 providers, ≥ 3 tasks, backing solution.json in the repo), V.6.1 (free tiers, more than 2 providers recommended), III (benchmark multiple models)

**Goal.** A data-backed comparison that justifies the final model choice.

**Concepts first**
- Experimental design: same tasks, same budget, one variable changed at a time
- Ablation studies
- Small-sample caveats
- The intermediary metrics:
  - the step at which the agent first touches the file that ends up in the patch
  - the iterations between "tests first pass" and `final_answer`

**Steps**
11.1 Pick 5 models across ≥ 3 providers and a fixed set of 3 tasks from the exam pool (say why).

11.2 Run the matrix into `benchmarks/<model>/<task>/solution.json`.

11.3 `scripts/make_report.py`: aggregates the tables from the files (optional but makes the numbers traceable).

11.4 Provider reliability: average `request_time_ms`, total retries, availability.

11.5 ≥ 2 intermediary metrics.

11.6 One ablation (e.g. prompt change or tool removal), same model and tasks.

11.7 Conclusions.

**Files:** `BENCHMARK_REPORT.md`, `benchmarks/**/solution.json`, `scripts/make_report.py`

**Done when**
- the report has Setup, Results table, Provider reliability, Intermediary metrics, Ablation and Conclusions
- every number traces to a committed `solution.json`
- re-running `uv run python scripts/make_report.py` reproduces the tables

**Understanding checks**
1. Why can't you conclude much from 3 tasks?
2. What's a confounder in your ablation?
3. Explain "availability" from your data.

**Likely live modifications:** add a column (e.g. average tokens per iteration) to the report script.

**Interview talking points**
- *Say:* "I chose model X because of success rate *and* iteration efficiency and reliability, not raw solve rate."
- *Hardest question:* "Is your ablation result statistically meaningful?"

**Risks and common mistakes**
- Daily quotas (spread runs over days, keep > 2 providers ready).
- Models deprecated mid-benchmark.
- Committing huge logs.

---

### Milestone 12: 42 README and evaluation readiness (Phase 2)
**Owner** Shared · **Time** 4-6 h · **Subject** VII (README format and sections, English), VIII (submission contents, no images/weights/generated outputs), VI (exam scripts, `.env`), VI.3, VI.4

**Goal.** Pass the evaluation on campus machines.

**Steps**
12.1 Convert the personal README to the 42 format:
- first line, italic: *This project has been created as part of the 42 curriculum by <logins>*
- Description, Instructions, Resources (including how AI was used)
- System architecture, Agent loop, Sandbox design, Tool implementation details, Benchmark results and analysis
- all in English

12.2 Re-read the subject against table C; tick every row.

12.3 Fresh clone + `.env` → run `exam_mbpp.sh`, `exam_swebench.sh`, `exam_sandbox.sh`.

12.4 Repo hygiene: no keys, no images, no generated outputs except `benchmarks/`.

12.5 Live-modification practice, round 2.

**Done when**
- exam_mbpp ≥ 4/5
- exam_swebench ≥ 2/3, with containers cleaned
- exam_sandbox ALL
- the README's first line is exact
- the key grep is clean

**Understanding checks / live mods:** repeat the M8.5 drill with Phase 2 code.

**Interview talking points:** n/a (evaluation-focused).

**Risks and common mistakes**
- Campus machine differences (Docker permissions, Python availability, RLIMIT behavior).
- Free tiers under load during evaluation.

---

## C. Traceability: subject requirement → milestone

| ID | Requirement | Subject | Milestone(s) | Verified by |
|----|-------------|---------|--------------|-------------|
| R01 | Python 3.10 | IV.1 | M1 | `uv run python --version` |
| R02 | uv as package manager | IV.1 | M1 | `uv.lock`, `uv run` everywhere |
| R03 | Clean architecture, readable, structured, documented | IV.1 | all (layout M1), M12 review | review, docstrings |
| R04 | Errors handled gracefully, no crashes | IV.1 | M1, M7.5, M8, M10 | error-path tests, always-valid output |
| R05 | All execution sandboxed | IV.1 | M3, M5 (`run_tests` uses a worker) | M3.5 suite |
| R06 | Multiple LLM providers and models | IV.2, V.6 | M6, M11 | `llm_ping` on 2 providers |
| R07 | Usage tracking: tokens, retries, latency, requests | IV.2 | M6, M7.5 | StepMetrics/SolutionOutput fields |
| R08 | Configurable sandbox (imports, filesystem) | IV.2, V.2 | M1, M2 | JSON-only live mod |
| R09 | Tools work independently of the agent loop | IV.2, V.5 | M5, M9 | direct-call tests |
| R10 | No agent-orchestration libraries | IV.2 | M1 (deps), every review | `pyproject.toml` |
| R11 | Own agent loop | IV.2 | M7.5 | `agent/loop.py` |
| R12 | Thought → Code → Observation loop | III, V.1(1) | M7.5 | trace in solution.json |
| R13 | Extract code; formats a-d; convert to Python calls | V.1(2) | M7 | `test_extraction.py` |
| R14 | Execute in sandbox; feed results back | V.1(3,4) | M7.5 | loop tests |
| R15 | Solve benchmark tasks autonomously | V.1(5) | M8, M10 | dump/run/validate |
| R16 | System prompt: tool docs, response slots, loop examples | V.1(6) | M7.5, M10 | `prompts.py` |
| R17 | Feedback: no valid code block | V.1 box | M7, M7.5 | loop test |
| R18 | Feedback: malformed block interpreted (explain how) | V.1 box | M7 | extraction test |
| R19 | Feedback: timeout with partial output | V.1 box | M3, M7.5 | timeout test |
| R20 | Feedback: tool output truncated | V.1 box | M5.5 | truncation test |
| R21 | Feedback: edit introduced syntax/lint error | V.1 box | M9 | `edit_file` test |
| R22 | StepMetrics fields exactly as specified | V.1.1 | M1 (model), M7.5 (filled) | model tests |
| R23 | SolutionOutput fields exactly incl. `system_prompt` | V.1.1, V.4 | M1, M7.5 | validator |
| R24 | Each step has api_url, model_name, llm_output, sandbox_input, sandbox_output, retries | V.1.1 info, V.4 | M7.5 | trace check |
| R25 | Sandbox CLI: plain, config, `--mcp-stdio`, `--mcp-server`, SWE tools | V.2(1) | M4, M5.5, M9 | CLI commands |
| R26 | REPL: prompt, loop, same restrictions, wrappers + final_answer, prints result/error, exit + EOF | V.2(1) | M4 (+M5.5) | `test_cli.py` |
| R27 | final_answer injected, captures arg, signals completion; not MCP; always present | V.2(2), III fig. | M4, M5.5 | dummy-server test |
| R28 | MBPP `final_answer(code)`; SWE `final_answer(get_patch())` | V.2(2) | M7.5, M10 | prompts + traces |
| R29 | KeyboardInterrupt/SystemExit propagate, never silently caught | V.2(2) | M3.5 | security suite |
| R30 | Import allowlist with `.*` submodules | V.2(3), p.16 | M2 | policy tests |
| R31 | Filesystem allowlist (`allowed_directories`, evaluated inside sandbox) | V.2(3) | M2, M3.5 | path tests incl. symlink |
| R32 | No network access | V.2(3) | M3.5 | network test |
| R33 | Execution timeout (sandboxed code only) | V.2(3), p.16 | M3, M5.5 (pause during tools) | timeout tests |
| R34 | Memory limit | V.2(3) | M3.5 | memory test |
| R35 | Restricted builtins | V.2(3) | M2 | builtin tests |
| R36 | Security with stdlib only, no RestrictedPython | V.2 red box | M2, M3.5 | deps review |
| R37 | Isolation approach chosen and justified | V.2 green box | M3 (D1) | INTERVIEW_NOTES |
| R38 | Mandatory tools integrated into the agent | V.2(4) | M5, M9, M10 | runs |
| R39 | MCP tools, resources and prompts exposed | V.2(4) | M5 (server), M5.5 (client helpers) | probe + manual |
| R40 | MCP tools callable as Python functions in the sandbox | V.2(4) | M5.5 | REPL demo |
| R41 | Works with an unknown MCP server; dynamic discovery | V.2(4), p.16 | M5.5 | dummy server test |
| R42 | `mcp_tools_mbpp.py`, `mcp_tools_swebench.py` at repo root | V.2(4) | M5, M9 | layout |
| R43 | stdio and streamable HTTP transports | V.2(4) | M5.5 | both demos |
| R44 | MCP servers are separate processes; only own servers | III fig. | M5 | architecture |
| R45 | Sandbox manual generated from the connected server's schemas | V.2(5) | M5.5 | dummy server test |
| R46 | Sandbox limits/behavior via Pydantic + JSON config | V.2 | M1 | config tests |
| R47 | MCP tools are a separate security domain, not under sandbox timeout | V.2 p.16 | M5.5 | timeout-pause test |
| R48 | MBPP CLI with exact flags | V.3(1) | M8 | dump/run/validate |
| R49 | `run_tests(code, test_list)` → JSON with `success`, `output` | V.3(2) | M5 | `test_mcp_mbpp.py` |
| R50 | `MBPPTaskInput` model | V.3(3) | M1 | model test |
| R51 | MBPP output: `benchmark="mbpp"`, solution = function code | V.3(3) | M7.5, M8 | validator |
| R52 | Operate within limits; `max_iterations` configurable | V.3(4) | M7.5 | config + CLI flag |
| R53 | SWE-bench in Docker, approach (a) or (b), sandbox constraints still enforced | V.4 | M9 (D13), M10 | spike notes |
| R54 | Clean containers after execution | V.4, VI table | M10 | `docker ps` check |
| R55 | Patch via `git -c core.fileMode=false diff` | V.4 | M9 | `get_patch` test |
| R56 | Tools read `TESTBED_PATH` | V.4 red box | M9 | isolated tests |
| R57 | SWE-bench CLI with exact flags | V.4(1) | M10 | dump/run/validate |
| R58 | `SWEBenchTaskInput` model | V.4(3) | M9 | model test |
| R59 | SWE-bench hard limits (30 / 300k / 10k / 900 s) | V.4(4), VI.1.2 | M10 | config + traces |
| R60 | `read_file(filepath, start_line, end_line)`, `N: line` format | V.5.1 | M9 | tool test |
| R61 | `edit_file(filepath, old_str, new_str)` exact replace | V.5.1 | M9 | tool test |
| R62 | `list_files(directory, pattern)` | V.5.1 | M9 | tool test |
| R63 | `search_code(pattern, file_pattern)`, `/abs/path:line content` | V.5.2 | M9 | tool test |
| R64 | `search_function_or_class_definition_in_code(name)` | V.5.2 | M9 | tool test |
| R65 | `find_references(name, filepath, line)` | V.5.2 | M9 | tool test |
| R66 | `run_tests()` runs the eval script | V.5.3 | M9 | tool test |
| R67 | `get_patch()` unified git diff | V.5.3 | M9 | tool test |
| R68 | `run_command(command, workdir)` → stdout, stderr, exit code | V.5.3 | M9 | tool test |
| R69 | Free tiers only (no paid plans or credits) | V.6.1 | M6, M11 | provider list |
| R70 | Multiple API keys per provider + rotation (mandatory) | V.6.1 | M6 | rotation test |
| R71 | Provider fallback (recommended) | V.6.1 | M6 (optional) | config |
| R72 | Abstraction: switch providers without refactoring | V.6.1 | M6 | config-only live mod |
| R73 | Stop sequences against hallucinated tool output | V.6 box | M6, M7, M7.5 | traces |
| R74 | Benchmark report: setup, results, reliability, ≥ 2 intermediary metrics, ablation, conclusions; 5 models, ≥ 2 providers, ≥ 3 tasks; solution.json in repo | V.7 | M11 | report review |
| R75 | API keys from environment variables (standard names); `.env` | VI | M6, M8 | fresh-clone run |
| R76 | exam_sandbox: import, builtin, network, path, timeout, memory, MCP protocol | VI table | M2, M3, M3.5, M5.5 | security suite |
| R77 | MBPP limits: 10 iterations, 6,000 in, 1,500 out, 120 s | VI.1.1 | M7.5, M8 | batch check |
| R78 | SWE-bench limits: 30, 300,000, 10,000, 900 s | VI.1.2 | M10 | runs |
| R79 | Token limits cumulative; reasoning tokens count | VI.1 | M6 (model choice), M7.5 | budget tests |
| R80 | Timeout enforced by SIGTERM → SIGKILL incl. children; cleanup must still run | VI.1 | M3 (worker exits on EOF), M8, M10 | `timeout -s TERM` tests |
| R81 | Pass: MBPP 4/5, SWE-bench 2/3; no re-runs | VI.2 | M8, M10, M12 | exam scripts |
| R82 | No hardcoded API keys | VI.3 | M1, M6, M12 | `git grep` check |
| R83 | Live modifications in 2-5 min; revert with git | VI.4 | every milestone, M8.5, M12 | drills |
| R84 | AI safety: no fetching solutions, no memorized patches, no bypass, no outside resources | VI.4.1 | M5, M7.5, M10 | prompt rules, no network, traces |
| R85 | Evaluation logging structure `evaluations/TYPE/date/task_id/...` | VI.5 | M8 (mirrored locally), M12 | batch output |
| R86 | README first line italic 42 sentence; Description, Instructions, Resources (+ AI use) | VII | M12 (personal README in M8.5) | review |
| R87 | README extras: architecture, agent loop, sandbox design, tools, benchmark results | VII | M12 | review |
| R88 | README in English | VII | M8.5, M12 | review |
| R89 | Config files for sandbox and models in the repo | VIII | M1, M6, M7.5 | layout |
| R90 | No Docker images, model weights, generated outputs | VIII | M1 (.gitignore), M12 | `git ls-files` review |
| R91 | Transparency about AI use | II | AI_USAGE_LOG every milestone, README | log |
| R92 | Development approach: MBPP first, capable model, limits relaxed first | V.1.2 | M8 | process |

---

## D. Risk register and definitions of done

### D.1 Risk register

| ID | Risk | Likelihood | Impact | Mitigation | Where |
|----|------|-----------|--------|------------|-------|
| K1 | The exam scripts (`exam_mbpp.sh`, `exam_swebench.sh`, `exam_sandbox.sh`) aren't in the moulinette folder, so we don't know how the sandbox security tests, the "MCP protocol" test and the isolated SWE-bench tool tests drive our code | High | High | Follow the subject's CLI contracts exactly; make the REPL work with piped stdin; ask staff or peers for the exam scripts before Phase 2 | M4, M5.5, M9 |
| K2 | MBPP 6,000 cumulative input tokens too tight | High | High | Compact prompt + manual, one-step solve/test/submit pattern, pre-call budget check, safety margins | M5.5, M7.5 |
| K3 | Free-tier quotas, rate limits, model deprecations | High | Medium | ≥ 3 providers, several keys each, KeyPool cooldowns, runs spread over days | M6, M11 |
| K4 | Reasoning models exhaust token limits | Medium | High | Prefer non-reasoning models; record output tokens per step | M6, M8 |
| K5 | Provider ignores or limits stop sequences | Medium | Medium | Extraction cuts at `Observation:`; ≤ 4 stop strings | M6, M7 |
| K6 | Sandbox escape via object graph or allowed-module internals | Medium | High | Layered defense (D2), audit hook backstop, escape tests | M2, M3.5 |
| K7 | Audit hook blocks interpreter internals (imports, encodings) | Medium | Medium | Pre-import, active-only-during-user-code flag, tests | M3.5 |
| K8 | `RLIMIT_AS` behaves differently on other OSes / campus machines | Low-Medium | Medium | Test on Linux; check campus machines in M12 | M3.5, M12 |
| K9 | `multiprocessing` spawn problems from the console script | Low | Medium | Fallback to `subprocess.Popen` worker | M3 |
| K10 | Async bridge deadlocks or zombie MCP servers | Medium | Medium | Strict shutdown order, process checks in tests | M5.5 |
| K11 | Docker Desktop on WSL2: image size, disk, slow pulls | Medium | Medium | Spike early in Phase 2, prune images | M9 |
| K12 | SWE-bench container Python too old for in-container tools | Medium | Medium | Host-side tools via the executor (D13) | M9 |
| K13 | Containers leak after SIGKILL | Medium | Medium | Labels + stale cleanup at startup; deadline margin | M10 |
| K14 | API key leaked into git | Low | High | `.gitignore`, `.env.example`, grep check before every commit, masked logs | M1, M6 |
| K15 | Agent violates AI safety (fetches solutions) → grade 0 | Low | Critical | No network in sandbox, no web tools, prompt rule, trace review | M7.5, M10 |
| K16 | Time pressure before the interview | Medium | High | Minimum viable cut (0.2); M6 can move earlier | all |
| K17 | Understanding gap (code typed but not internalized) | Medium | High | Per-milestone predict/explain-back, drills, mock interview | all, M8.5 |
| K18 | Repo root mismatch: the git repo is the parent folder, so the moulinette sits inside it | Certain (today) | Medium | M1 step 1.0 | M1 |
| K19 | `mcp` SDK API changes between versions | Medium | Low | Pin the version; read docs for that version | M5 |
| K20 | Our KeyboardInterrupt/REPL behaviour differs from what the exam expects | Medium | Medium | Confirm with the exam scripts if we can get them | M4, M3.5 |
| K21 | Docker not reachable from WSL (Docker Desktop's WSL integration is off). `validate mbpp` runs solutions in Docker, so it fails | Certain (today) | High | Turn on WSL integration for this distro before M8; check with `docker run --rm python:3.11-slim python -c 'print(1)'` | M1 (setup), M8 |

### D.2 Definition of done: Phase 1 (interview-ready)
- [ ] Fresh clone → `uv sync` → `.env` filled → everything below works without manual tweaks
- [ ] `uv run pytest -q` green; security suite covers import, builtin, network, path, timeout, memory, escapes, exception propagation
- [ ] `uv run sandbox` works with `--mcp-stdio` and `--mcp-server`, and with an unknown dummy server
- [ ] MBPP dump → run → validate passes on ≥ 8/10 tasks with at least 2 models from 2 providers, all within 10 iterations / 6,000 in / 1,500 out / 120 s
- [ ] Every `solution.json` has complete, real step data (`llm_output`, `sandbox_input`, `sandbox_output`, `retries`, `api_url`, `model_name`) and the full `system_prompt`
- [ ] SIGTERM during a run leaves a valid `solution.json` and no stray processes
- [ ] No API keys in the repo (`git grep` check clean)
- [ ] INTERVIEW_NOTES.md, notes/M1-M8.5, AI_USAGE_LOG.md complete; personal README honest and in English
- [ ] Amin can draw the architecture, walk through one run, answer every "hardest question", and finish ≥ 8/10 live-modification drills within 5 minutes

### D.3 Definition of done: Phase 2 (evaluation-ready)
- [ ] All 9 SWE-bench tools pass isolated tests with `TESTBED_PATH`
- [ ] exam_mbpp ≥ 4/5, exam_swebench ≥ 2/3 (containers cleaned), exam_sandbox ALL, on a fresh clone with the evaluation `.env`
- [ ] BENCHMARK_REPORT.md complete (5 models, ≥ 2 providers, ≥ 3 tasks, reliability, ≥ 2 intermediary metrics, ablation, conclusions) and backed by committed solution.json files
- [ ] 42-format README (exact italic first line, all required sections, English)
- [ ] Repo contains config files for sandbox and models; no images, weights or generated outputs except `benchmarks/`
- [ ] Every row of table C ticked

---

## Open questions (to resolve with Amin)
1. When is the interview? That decides whether we use the minimum viable cut (0.2).
2. Do you have the exam scripts (`exam_*.sh`)? They aren't in the moulinette folder, and they define how the sandbox security tests, the MCP protocol test and the isolated tool tests are run.
3. REPL Ctrl+C behavior: exit the REPL (current plan, matches V.2(2)) or cancel only the current input?
4. Which LLM providers and how many keys per provider do you already have?

## Per-milestone routine
Explain → show full code → Amin types it → run the "done when" check → prediction check + explain-back + "where would you change X?" → `notes/M<n>.md` → `AI_USAGE_LOG.md` line → Amin commits and tags `m<n>-done`.
