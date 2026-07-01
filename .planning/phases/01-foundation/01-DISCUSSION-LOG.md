# Phase 1: Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-23
**Phase:** 01-foundation
**Areas discussed:** Package layout & imports, Paper-safety guard (SAFE-01), State schema scope, Testing setup

---

## Package Layout & Imports

### Q1 — Where should the new bot code live?

| Option | Description | Selected |
|--------|-------------|----------|
| Top-level `bot/` package | bot/ at repo root with gateway/state/strategy/config/safety subpackages; tests/ sibling | ✓ |
| src/ layout (src/bot/) | Package under src/bot/ with pyproject.toml; packaging ceremony | |
| Under skills/ | Inside skills/ alongside moomooapi | |

**User's choice:** Top-level `bot/` package.

### Q2 — How should the bot reuse the existing skills/moomooapi client?

| Option | Description | Selected |
|--------|-------------|----------|
| MoomooGateway wraps the SDK directly | Gateway uses moomoo SDK classes directly; common.py is reference only; existing scripts untouched | ✓ |
| Import common.py via sys.path shim | Add scripts dir to sys.path, import common verbatim; brittle coupling | |
| Package-ify moomooapi first | Add __init__.py to existing scripts; modifies working skill | |

**User's choice:** MoomooGateway wraps the SDK directly.
**Notes:** Honors CLAUDE.md "reuse, don't rewrite" without coupling the bot to a non-package script dir.

---

## Paper-Safety Guard (SAFE-01)

### Q1 — How should the bot choose which paper account to trade?

| Option | Description | Selected |
|--------|-------------|----------|
| Require explicit acc_id + verify SIMULATE | Operator sets FUTU_ACC_ID + PAPER_TRADING=true; bot verifies that account's trd_env==SIMULATE | ✓ |
| Auto-select the single SIMULATE account | Bot auto-picks the sim account from get_acc_list | |

**User's choice:** Require explicit acc_id + verify SIMULATE.

### Q2 — How many independent layers should the guard enforce?

| Option | Description | Selected |
|--------|-------------|----------|
| Multiple independent assertions | PAPER_TRADING flag + FUTU_TRD_ENV==SIMULATE + broker account trd_env==SIMULATE; all must hold | ✓ |
| Single broker-truth assertion | Only verify broker account trd_env==SIMULATE | |

**User's choice:** Multiple independent assertions (fail-closed).

### Q3 — Where should the SIMULATE assertion live?

| Option | Description | Selected |
|--------|-------------|----------|
| Reusable guard: startup + wraps order path | One assert_paper_account() at startup AND re-invoked before every future order | ✓ |
| Startup gate only | Assert once at startup, hard-exit on failure | |

**User's choice:** Reusable guard: startup + wraps order path.
**Notes:** Operator prioritized making a real-money order structurally impossible over convenience.

---

## State Schema Scope

### Q1 — How much of the SQLite schema should Phase 1 define?

| Option | Description | Selected |
|--------|-------------|----------|
| Full v1 schema as migration 0001 | Define positions/trades/daily_scan/bar_cache + meta now | ✓ |
| Minimal now + migration framework | Only Phase-1 tables now; later phases add theirs | |

**User's choice:** Full v1 schema as migration 0001.
**Notes:** Strategy is fully specified, so the data model is knowable; coherence chosen over YAGNI for the schema.

### Q2 — What migration/versioning mechanism?

| Option | Description | Selected |
|--------|-------------|----------|
| PRAGMA user_version + ordered steps | Startup runner applies ordered steps; pure stdlib | ✓ |
| schema_migrations tracking table | Table recording applied migrations | |

**User's choice:** PRAGMA user_version + ordered steps.

### Q3 — Where does the SQLite DB file live, and is the path configurable?

| Option | Description | Selected |
|--------|-------------|----------|
| data/ dir, path from config | ./data/bot_state.db (gitignored), overridable for tests | ✓ |
| Fixed repo-root path | Hardcode ./bot_state.db | |

**User's choice:** data/ dir, path from config.

---

## Testing Setup

### Q1 — Test framework and layout?

| Option | Description | Selected |
|--------|-------------|----------|
| pytest, tests/ mirrors bot/ | pytest + tests/ tree mirroring bot/, conftest fixtures | ✓ |
| stdlib unittest | No third-party dep, more boilerplate | |

**User's choice:** pytest, tests/ mirrors bot/.

### Q2 — How should the 'no hardcoded strategy literal' criterion be enforced?

| Option | Description | Selected |
|--------|-------------|----------|
| Behavioral test w/ swapped rules.json | Load baseline vs modified rules.json, assert outputs change | ✓ |
| Behavioral + AST/source literal scan | Add AST magic-number scan on top; needs allowlist | |

**User's choice:** Behavioral test w/ swapped rules.json.

### Q3 — How should new Python dependencies be declared?

| Option | Description | Selected |
|--------|-------------|----------|
| requirements.txt + requirements-dev.txt | Runtime + dev split, no packaging ceremony | ✓ |
| pyproject.toml | Single unified file with build-system config | |

**User's choice:** requirements.txt + requirements-dev.txt.

---

## Claude's Discretion

- `rules.json` at repo root, validated at startup with `jsonschema`; fail-fast on malformed/missing.
- structlog rotating-file config (JSON-ready, contextvars-aware).
- zoneinfo ET helpers (America/New_York, DST-correct).
- JSONL audit log extending `~/.futu_trade_audit.jsonl`.
- Kill switch via file-touch + SIGINT with graceful shutdown + state flush.
- asyncio scaffolding + typed dataclass events; broker I/O via run_in_executor.
- Internal class/function signatures and module decomposition within the agreed package boundaries.

## Deferred Ideas

None — discussion stayed within phase scope.
