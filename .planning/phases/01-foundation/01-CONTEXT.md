# Phase 1: Foundation - Context

**Gathered:** 2026-06-23
**Status:** Ready for planning

<domain>
## Phase Boundary

The non-trading bedrock of the bot, built as independently-testable layers with **no orders, no scanning, and no live market data** in this phase:

- **MoomooGateway** — broker access layer (persistent quote/trade contexts, connectivity pre-flight, `run_in_executor` wrappers).
- **StateStore** — durable SQLite persistence with atomic writes and a versioned migration runner.
- **StrategyConfig + StrategyCore + TrendJoinLong + indicators** — pure strategy logic (SMA200, RVOL, swing_low_2_2, D1/D2/D3 filters) reading every parameter from `rules.json`; zero I/O; unit-tested against synthetic DataFrames.
- **Safety primitives** — hard paper-trading guard (SAFE-01), startup-reconciliation skeleton (SAFE-02) and 60–90s reconciliation loop skeleton (SAFE-03), kill switch (SAFE-04), JSONL audit log (SAFE-05), structured rotating logging (SVC-03), and zoneinfo ET helpers (SVC-04).

Requirements in scope: **CFG-01, STATE-01, SAFE-01, SAFE-02, SAFE-03, SAFE-04, SAFE-05, SVC-03, SVC-04**.

Out of scope this phase: the premarket scanner (Phase 2), live 5m subscriptions / signal engine (Phase 3), order placement & position FSM (Phase 4), service scheduler & Telegram alerts (Phase 5), backtester (Phase 6). The reconciliation pieces here are **skeletons/wiring only** — full position reconciliation logic lands in Phase 4.
</domain>

<decisions>
## Implementation Decisions

### Package Layout & Imports
- **D-01:** New bot code lives in a **top-level `bot/` package** at repo root, with subpackages `bot/gateway/`, `bot/state/`, `bot/strategy/`, `bot/config/`, `bot/safety/`. `tests/` is a sibling tree. (Not `src/` layout — this is an operator-run app, not a distributed library.)
- **D-02:** **`MoomooGateway` wraps the moomoo SDK directly** (uses `OpenQuoteContext`/`OpenSecTradeContext` with its own thin typed connection logic), treating `skills/moomooapi/scripts/common.py` as a *reference pattern* only. The existing `skills/moomooapi` scripts stay an **untouched CLI skill** — no path-shim imports, no package-ifying the existing scripts. (Honors CLAUDE.md "reuse, don't rewrite" without coupling the bot to a non-package script dir.)

### Paper-Safety Guard (SAFE-01)
- **D-03:** **Account selection is explicit** — operator must set `FUTU_ACC_ID` (or a bot config field) AND `PAPER_TRADING=true`. No auto-guessing of the account.
- **D-04:** **Triple, independent assertion (fail-closed):** ALL must hold or the bot hard-exits — (1) `PAPER_TRADING=true` flag, (2) `FUTU_TRD_ENV==SIMULATE`, (3) the selected account's broker-reported `trd_env==SIMULATE` (verified via `get_acc_list`). Any single misconfiguration fails closed.
- **D-05:** Implemented as **one reusable `assert_paper_account()` function**, invoked as a hard startup gate now AND designed so Phase 4's order-submission path re-invokes it before every `place_order`. Goal: structurally impossible to place a REAL-money order even if the startup gate were bypassed.
- **D-06:** Guard failure = **hard non-zero exit** before any order path is reachable, with a structured log line + an audit-log entry recording the refusal.

### State Schema & Persistence (STATE-01)
- **D-07:** **Full v1 schema defined now as migration 0001** — `positions`, `trades`, `daily_scan`, `bar_cache`, plus a `meta` table. (The strategy is fully specified, so the data model is knowable today; gives every later phase a stable target and makes the crash-injection/atomic-write test meaningful against real tables.)
- **D-08:** **Migration mechanism = `PRAGMA user_version` + ordered migration steps** applied on startup (pure stdlib, no deps). Startup reads `user_version` and applies steps up to current.
- **D-09:** **DB file at `./data/bot_state.db`** (the `data/` dir is gitignored), with the path **overridable via a config/env field** so tests can point at a tmp path.
- **D-10:** **Atomic writes** via temp-file + `os.replace()`; validate before replacing. (Standard SQLite is ACID for its own file; the atomic temp-file pattern applies to any non-DB state snapshots / state-flush artifacts and is what the crash-injection test exercises.)

### Testing Setup
- **D-11:** **pytest**, with a top-level `tests/` tree **mirroring `bot/`** (`tests/strategy/`, `tests/state/`, …) and shared fixtures in `conftest.py`.
- **D-12:** The **"no hardcoded strategy literal" criterion is proven behaviorally** — a parametrized test loads a baseline `rules.json` and a modified one, and asserts indicator/filter outputs change accordingly (proves params actually flow from config). Refactor-proof; no AST/magic-number scan required.
- **D-13:** **Dependencies declared in `requirements.txt` (runtime) + `requirements-dev.txt` (test/dev tooling).** No `pyproject.toml` / build-system ceremony.

### Claude's Discretion
Taken at standard research defaults (see `.planning/research/SUMMARY.md` and PITFALLS.md):
- `rules.json` at **repo root**, validated at startup with **`jsonschema`**; malformed/missing config fails fast with a clear error.
- **structlog** rotating-file config (JSON-ready, contextvars-aware across the asyncio boundary).
- **zoneinfo**-based ET helpers (`America/New_York`, DST-correct); no naive datetimes or fixed UTC offsets.
- **JSONL audit log** extending the existing `~/.futu_trade_audit.jsonl` pattern (append-only, never overwrites prior entries).
- **Kill switch** via both file-touch (sentinel path) and SIGINT, triggering a graceful shutdown + state flush.
- **asyncio** single-process scaffolding and the typed dataclass event types are introduced as needed; broker I/O always via `run_in_executor`.
- Exact internal class/function signatures and module decomposition within the above package boundaries.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy & Config (source of truth)
- `.planning/PROJECT.md` §"The Strategy — Trend Join Long" — the canonical `rules.json` content (CFG-01): all universe/daily/intraday filters, time gates, exit rules, and risk params. No strategy constant may be hardcoded in Python.
- `.planning/REQUIREMENTS.md` — CFG-01, STATE-01, SAFE-01…05, SVC-03, SVC-04 acceptance text (this phase's requirement IDs).

### Architecture & Research (HIGH confidence)
- `.planning/research/SUMMARY.md` §"Phase 1: Foundation" and §"Architecture Approach" — component boundaries (MoomooGateway, StateStore, StrategyCore ABC), build-order rationale, stack choices.
- `.planning/research/ARCHITECTURE.md` — event-driven bus, `StrategyCore` ABC + `TrendJoinLong`, FSM, `run_in_executor`/`loop.call_soon_threadsafe` patterns.
- `.planning/research/PITFALLS.md` — non-atomic state writes (#5), RVOL look-ahead (#4), reconciliation/ghost positions (#3), paper-trading guard rationale.
- `.planning/research/STACK.md` — pinned versions (zoneinfo stdlib, SQLite stdlib, structlog 26.x); note: heavier deps (APScheduler, python-telegram-bot, backtrader2) belong to later phases.

### Existing Codebase (reuse-by-reference)
- `skills/moomooapi/scripts/common.py` — reference for `FutuConfig`, `create_quote_context()`/`create_trade_context()`, `check_ret()`, `safe_*` helpers, `get_acc_list`/account-type access, OpenD connectivity socket check. **Wrapped, not imported** (D-02).
- `.planning/codebase/STRUCTURE.md` — repo layout, naming conventions (snake_case files, PascalCase classes, UPPER_SNAKE constants).
- `.planning/codebase/CONVENTIONS.md` — code-style conventions to match.
- `.planning/codebase/INTEGRATIONS.md` — OpenD on `127.0.0.1:11111`, `TrdEnv.SIMULATE` default, `get_acc_list`/`get_accounts` account-type fields, audit-log location.
- `skills/moomooapi/docs/API_LIMITS.md` — rate/quota limits (relevant to gateway connectivity, not exercised heavily this phase).
- `CLAUDE.md` — project constraints (Python 3.6+, reuse moomoo SDK, paper-only `FUTU_TRD_ENV=SIMULATE`, ET timezone, market-hours awareness, $100k sizing basis).
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `skills/moomooapi/scripts/common.py`: `FutuConfig` dataclass + `get_config()` (env-var mapping), `create_quote_context()`/`create_trade_context()` (OpenD context factories), `check_ret()` (SDK return-code handling), `safe_get/safe_float/safe_int`, socket-based OpenD connectivity check. The gateway re-implements equivalents directly against the SDK (D-02) but should mirror these signatures/patterns for consistency.
- Existing trade-audit pattern at `~/.futu_trade_audit.jsonl` (append-only JSONL) — extend, don't replace (SAFE-05).

### Established Patterns
- Market inferred from code prefix (`US.`); paper trading (`TrdEnv.SIMULATE`) is the default; `unlock_trade()` is forbidden via SDK (manual GUI only) — the bot must never attempt it.
- snake_case files, PascalCase classes (`FutuConfig` → `MoomooGateway`, `StateStore`), UPPER_SNAKE constants, leading-underscore private helpers.

### Integration Points
- OpenD daemon on `127.0.0.1:11111` (configurable via `FUTU_OPEND_HOST`/`FUTU_OPEND_PORT`) — gateway pre-flight checks connectivity before use.
- Account/environment truth via `get_acc_list` (per-account `trd_env`) — the authoritative SIMULATE assertion source for SAFE-01.
</code_context>

<specifics>
## Specific Ideas

- The paper guard must be **belt-and-suspenders and fail-closed** — the operator explicitly prioritized making a real-money order structurally impossible over convenience (no account auto-selection).
- Because the strategy is fully specified, the operator chose to **commit to the full data model now** rather than grow it piecemeal — coherence over YAGNI for the schema specifically.
- Tests should prove config-drivenness by **behavior** (swap `rules.json`, observe output change), not by static inspection.
</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope. (Telegram alerts, scheduler/APScheduler, full reconciliation logic, scanner, signal/order/position engines, and the backtester remain mapped to their later phases per ROADMAP.md.)
</deferred>

---

*Phase: 01-foundation*
*Context gathered: 2026-06-23*
