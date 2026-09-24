# Phase 11: Multi-strategy options bot (bull_call_spread) - Pattern Map

**Mapped:** 2026-09-24
**Files analyzed:** 15 (12 modified, 3 new: `bot/options/universe.py`, `tests/options/test_universe.py`, `docs/research/2026-09-24-super-bull-call-spread.md`)
**Analogs found:** 15 / 15 (this phase is 100% in-tree refactor — the analog for almost every file is itself, at its pre-phase revision)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `rules_options.json` | config | request-response (loaded once at process start) | itself (current flat shape) | exact (structural rewrite) |
| `bot/options/schema.py` | config/schema | CRUD (validation only) | itself + `bot/config/schema.py` (`_IMPLEMENTED_EXIT_MODELS` sibling) | exact |
| `bot/options/config.py` | config | CRUD (load/flatten) | itself + `bot/config/loader.py` (fail-closed pattern) | exact |
| `bot/options/strategy.py` | service (pure core) | transform | itself (`pick_strikes`/`manage_decision` extend by branch) | exact |
| `bot/options/store.py` | model/store | CRUD | itself + `bot/state/migrations.py` (guard pattern) | exact |
| `bot/options/universe.py` (NEW) | service (read-only reader) | request-response / file-I/O | `bot.state.store.StateStore.get_watchlist_codes` (query shape only, no import) | role-match |
| `bot/options/service.py` | service (orchestrator) | event-driven (APScheduler jobs) | itself | exact |
| `bot/main.py` | route/dispatch | request-response | itself | exact |
| `backtester/options_run.py` | controller (CLI) | batch | itself | exact |
| `docs/research/2026-09-24-super-bull-call-spread.md` (NEW) | docs | — | `docs/research/2026-08-17-tastylive-options-research.md` | exact |
| `bot/state/migrations.py` (`_migration_0007`, NEW) | migration | batch | `_migration_0004`/`_migration_0005` (guarded ALTER pattern) | exact |
| `tests/options/conftest.py` | test | — | itself | exact |
| `tests/options/test_config.py`, `test_strategy.py`, `test_store.py`, `test_service.py`, `test_dispatch.py` | test | — | themselves (extend) | exact |
| `tests/options/test_universe.py` (NEW) | test | — | `tests/options/test_store.py` (sqlite fixture style) | role-match |
| `tests/backtester/options/test_options_run.py` | test | — | itself | exact |

## Pattern Assignments

### `bot/options/schema.py`

**Analog:** itself, current `OPTIONS_SCHEMA` (`bot/options/schema.py:24-211`)

**Current structural shape to preserve for the legacy branch** (lines 24-38):
```python
OPTIONS_SCHEMA = {
    "type": "object",
    "required": ["strategy_name", "universe", "entry", "structure", "sizing", "manage", "execution", "service"],
    "additionalProperties": True,
    "properties": {
        "strategy_name": {"type": "string"},
        "universe": {"type": "array", "items": {"type": "string"}, "minItems": 1},
```

**Enum to extend for `structure.type`** (line 106-109):
```python
"type": {
    "type": "string",
    "enum": ["iron_condor", "put_credit_spread"]
},
```
→ add `"bull_call_spread"` to this enum, and mirror it in a NEW schema object for the `strategies`-shape file (do not attempt `oneOf`/conditional jsonschema — see Shared Patterns "No-conditional-schema").

Keep `OPTIONS_SCHEMA` frozen (D-06 legacy path uses it byte for byte); add a second top-level schema dict (e.g. `STRATEGIES_SCHEMA`) for the new shape, structural only (each strategy entry validated as `type: object` with `required: ["name", "entry", "structure", "sizing", "manage"]`; `universe`/`universe_source` are NOT both put in `required` — that XOR is a loader-side check per Pattern 1 below, not a schema conditional).

---

### `bot/options/config.py`

**Analog:** itself (`bot/options/config.py:24-229`) + `bot/config/loader.py:38,276` for the fail-closed idiom

**Fail-closed structure guard to mirror for every new D-11 rule** (lines 33-36, 162-171):
```python
_IMPLEMENTED_STRUCTURES = ("iron_condor", "put_credit_spread")
...
structure_type = str(st["type"])
if structure_type not in _IMPLEMENTED_STRUCTURES:
    raise ConfigError(
        f"structure.type '{structure_type}' is not implemented; "
        f"expected one of: {', '.join(_IMPLEMENTED_STRUCTURES)}"
    )
```
→ extend `_IMPLEMENTED_STRUCTURES` to include `"bull_call_spread"`; add sibling `if`/`raise ConfigError` checks for: duplicate strategy names, both/neither of `universe`/`universe_source`, unknown `universe_source` value, missing IV-gate keys when `structure_type` is a credit type, missing `profit_target_pct_of_max` when `structure_type == "bull_call_spread"` (Architecture Pattern 4 in RESEARCH.md — do not forget the `manage`-block conditional, only IV-gate is called out in CONTEXT D-05).

**Legacy flattening call site to keep completely unchanged (D-06 branch)** (lines 154-160, 199-229):
```python
en = data["entry"]; st = data["structure"]; sz = data["sizing"]
mg = data["manage"]; ex = data["execution"]; svc = data["service"]
...
sizing_equity_usd=float(sz["sizing_equity_usd"]),
...
manage_interval_min=int(mg["manage_interval_min"]),
```
**Critical relocation for the NEW strategies-shape branch** (RESEARCH.md Pitfall 2): read `sizing_equity_usd`/`max_bp_usage_pct`/`daily_loss_limit_pct` from `data["risk"]` and `manage_interval_min`/`equity_state_db` from `data["service"]` — NOT from the per-strategy `sizing`/`manage` blocks. Write two separate, non-shared mapping code paths; do not parametrize a single mapping function over "risk source dict" in a way that could silently read the wrong dict — an explicit `if "strategies" in data: ... else: ...` branch (mirroring the existing single-branch shape) is the smaller, safer diff (Architecture Pattern 1).

**Dataclass field-ordering pattern to follow (D-09) — 100%-explicit-kwargs, no field defaults** (lines 43-110 declare every field with no `=` default; lines 173-229 pass every field explicitly in one `return OptionsConfig(...)`):
```python
@dataclass
class OptionsConfig:
    # ---- top level ----
    strategy_name: str
    universe: tuple
    ...
```
→ append new fields (`name`, `universe_source`, `long_delta`, `max_debit_to_width`, `profit_target_pct_of_max`, `equity_state_db`) at the end with **no** dataclass-level default (Pattern 3 in RESEARCH.md); both loader branches must pass an explicit value (`None` where the design says None) in their `OptionsConfig(...)` calls.

**New functions to add, same module, same style:**
- `load_options_book(path) -> OptionsBook` — reads raw dict once, if `"strategies" in data` builds a tuple of `OptionsConfig` (one per entry) else wraps the single legacy `load_options_config` result in a 1-tuple.
- `legacy_view(raw: dict, name: str) -> dict` — inverse of the strategies-shape flattening; for a legacy-shape `raw`, return it unchanged (identity, per D-10).

---

### `bot/options/strategy.py`

**Analog:** itself — extend `pick_strikes` and add `manage_decision_debit` beside `manage_decision`.

**Existing structure-dispatch guard to extend** (lines 141-166):
```python
def pick_strikes(rows, underlying_px, structure, cfg) -> Optional[dict]:
    ...
    if structure not in ("iron_condor", "put_credit_spread"):
        raise ValueError(f"unsupported structure: {structure}")

    width_target = max(
        underlying_px * cfg.wing_width_pct_of_underlying / 100,
        getattr(cfg, "min_wing_width_usd", 0.0),
    )
```
→ add `"bull_call_spread"` to the allowed tuple; add a new branch after the existing `if structure == "iron_condor":` block (lines 190-201) that selects calls only, long leg via `_closest_delta(calls, cfg.long_delta)`, short leg via `_pick_wing(calls, long_strike, +width_target)`, `debit = _mid(long) - _mid(short)` (note the credit path computes `_mid(short) - _mid(long)` — the sign is flipped for a debit structure, D-13), legs ordered `[_leg(long, "BUY"), _leg(short, "SELL")]` (BUY first, matching the existing longs-before-shorts leg ordering convention at lines 211-214), and the 1/4-rule gate `0 < debit <= cfg.max_debit_to_width * width` replacing the credit-path's `credit < cfg.min_credit_to_width * width` gate (line 208).

**`size_position` per Claude's Discretion (D-14)** — smallest diff per RESEARCH.md is a `risk_usd` parameter, not two functions:
```python
# current (lines 222-238):
def size_position(width, credit, cfg, open_max_loss_total) -> int:
    risk = (width - credit) * _CONTRACT_MULTIPLIER
    ...
```
→ generalize by accepting the already-computed per-spread `risk` (or add a debit-aware wrapper that computes `debit * _CONTRACT_MULTIPLIER` and calls a shared core) — either is acceptable per CONTEXT's discretion note, but the credit-path call site and its numeric outputs must not change (regression-tested by existing `tests/options/test_strategy.py`).

**`manage_decision` to mirror exactly for `manage_decision_debit` (D-15)** (lines 264-285):
```python
def manage_decision(mark, credit, dte, cfg) -> Optional[str]:
    if dte <= cfg.assignment_guard_dte:
        return "assignment_guard"
    if credit - mark >= cfg.profit_target_pct_of_credit / 100 * credit:
        return "profit_target"
    if (cfg.stop_loss_credit_multiple is not None
            and mark - credit >= cfg.stop_loss_credit_multiple * credit):
        return "stop_loss"
    if dte <= cfg.manage_dte:
        return "dte_exit"
    return None
```
New sibling function, same signature shape, same first-hit-wins `if` chain, no stop-loss branch, `mark_spread` UNCHANGED (verified sign math in RESEARCH.md Code Examples):
```python
def manage_decision_debit(mark, debit, width, dte, cfg) -> Optional[str]:
    if dte <= cfg.assignment_guard_dte:
        return "assignment_guard"
    if (-mark - debit) >= cfg.profit_target_pct_of_max / 100 * (width - debit):
        return "profit_target"
    if cfg.manage_dte is not None and dte <= cfg.manage_dte:
        return "dte_exit"
    return None
```

---

### `bot/options/universe.py` (NEW)

**Analog:** `bot/state/store.py:692-714` (`get_watchlist_codes` — query shape ONLY, D-17 forbids importing `StateStore`)

**Query pattern to re-implement standalone with plain `sqlite3`:**
```python
# Source: bot/state/store.py:709-714 (query shape to copy, not the class/import)
rows = self._conn.execute(
    "SELECT code FROM daily_scan WHERE scan_date=? ORDER BY rank ASC",
    (scan_date_str,),
).fetchall()
return [r[0] for r in rows]
```
**Fail-closed read-only reader (from RESEARCH.md's already-verified probe, use verbatim shape):**
```python
import sqlite3

def read_equity_watchlist(db_path: str, scan_date_iso: str, cap: int = 20) -> list:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(
            "SELECT code FROM daily_scan WHERE scan_date=? ORDER BY rank ASC",
            (scan_date_iso,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows][:cap]
    except sqlite3.OperationalError:
        return []
```
Do NOT precheck with `os.path.exists()` (TOCTOU / doesn't catch locked-DB — RESEARCH.md Anti-Patterns). Do NOT `from bot.state.store import StateStore`.

---

### `bot/options/store.py`

**Analog:** itself — extend `_POSITION_COLUMNS` and pass `strategy_name` through insert/read (no new methods required if D-22's per-strategy counters are done as a Python-side filter — see Open Question 1 recommendation, adopted here as the smaller diff).

**Column tuple to extend** (lines 26-30):
```python
_POSITION_COLUMNS = (
    "position_id", "underlying", "structure", "expiry", "dte_at_entry",
    "ivr_at_entry", "credit_per_spread", "width", "qty", "max_loss_usd",
    "status", "opened_at", "closed_at", "close_reason", "realized_pnl_usd",
)
```
→ append `"strategy_name"` at the end (matches migration 0007's `ADD COLUMN` order — insert/read already use `pos.get(c)` for every column in `_POSITION_COLUMNS`, lines 56-61, so this is the only change needed for insert; `get_option_positions` (lines 143-171) already `SELECT *`s the row so `strategy_name` comes through for free).

**Per-strategy filter (D-22) — Python-side, in `service.py`, NOT a new store method** (per RESEARCH.md Open Question 1 recommendation): `[p for p in active if p["strategy_name"] == cfg.name]` over the already-fetched `get_option_positions(_ACTIVE_STATUSES)` result.

---

### `bot/state/migrations.py` (`_migration_0007`, NEW)

**Analog:** `_migration_0004` (lines 203-217) — guarded single-column ALTER, exact pattern to copy:
```python
_POSITIONS_0004_COLUMNS = (
    ("entry_order_id", "TEXT"),
    ...
)

def _migration_0004(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, decl in _POSITIONS_0004_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")
```
→ new `_migration_0007(conn)`:
```python
def _migration_0007(conn: sqlite3.Connection) -> None:
    """D-18: add strategy_name to option_positions (never edit _migration_0006)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(option_positions)")}
    if "strategy_name" not in existing:
        conn.execute(
            "ALTER TABLE option_positions ADD COLUMN strategy_name TEXT "
            "NOT NULL DEFAULT 'tasty_credit_spreads'"
        )
```
Append to `MIGRATIONS` list (lines 362-369) as the new last entry — **never edit `_migration_0006`** (RESEARCH.md Anti-Patterns, D-08 rule stated in the module docstring at line 323).

---

### `bot/options/service.py`

**Analog:** itself throughout — this file gets the largest diff but every new piece has a direct existing sibling.

**`_register_jobs` per-strategy loop, from the current single-strategy version** (lines 404-440):
```python
hour, minute = _parse_hhmm(cfg.entry_scan_et)
self._scheduler.add_job(
    self._job_entry_scan,
    CronTrigger(hour=hour, minute=minute, timezone=_ET),
    id="options_entry_scan", **common,
)
if cfg.second_entry_scan_et is not None:
    ...
    id="options_entry_scan_2", **common,
```
→ loop over `self._book.strategies` (or equivalent), building `id=f"options_entry_scan_{cfg.name}"` and `f"options_entry_scan_{cfg.name}_2"`; ONE `options_manage` and ONE `options_eod` job unchanged (D-20 — these stay singular).

**`_scan_and_open` global busy/BP-headroom computation to keep GLOBAL (D-22)** (lines 508-513):
```python
active = self._store.get_option_positions(_ACTIVE_STATUSES)
busy = {p["underlying"] for p in active}
open_max_loss_total = sum(
    float(p["max_loss_usd"] or 0) for p in active if p["status"] in _OPEN_STATUSES
)
open_count = sum(1 for p in active if p["status"] in _OPEN_STATUSES)
```
→ keep this computation exactly as-is (global across strategies); add a strategy-scoped `opened_today`/`open_count` filter alongside it for the two per-strategy caps (`max_new_positions_per_day`, `max_concurrent_positions`), e.g. `strategy_active = [p for p in active if p["strategy_name"] == cfg.name]`.

**`_manage_position` dispatch point (D-21)** (lines 720-741 — `manage_decision(mark, credit, dte, cfg)` call): branch here on `pos["strategy_name"]`:
```python
dec = (
    manage_decision_debit(mark, -credit, pos_width, dte, strat_cfg)
    if pos["structure"] == "bull_call_spread"
    else manage_decision(mark, credit, dte, strat_cfg)
)
```
(recall D-19: `debit = -credit_per_spread` for a debit position; `strat_cfg` = the `OptionsConfig` matching `pos["strategy_name"]`, looked up from `self._book`).

**`main()` composition root** (lines 944-993) — change `load_options_config(rules_path)` to `load_options_book(rules_path)` and build one `OptionsBot` holding the whole book; keep `OptionsStore(...)`, `TelegramAlerter`, `KillSwitch`, `OpenDWatchdog` construction otherwise unchanged (D-25 — own DB/kill-file/report-dir stay singular per D6, not per strategy).

**D-29 reconcile pattern to mirror** — same shape as the existing `NEEDS_ATTENTION` sets at lines 332 and 361/759-767 (`self._store.set_position_status(pid, "NEEDS_ATTENTION")` + `self._alerter.send(...)` + `append_audit({...})`); add the same triple at startup reconcile for any active position whose `strategy_name` is not in the loaded book's strategy names.

---

### `bot/main.py`

**Analog:** itself, the exact dispatch check to widen (lines 67-80):
```python
try:
    with open(rules_path, "r", encoding="utf-8") as f:
        strategy_name = json.load(f).get("strategy_name", "")
except (FileNotFoundError, json.JSONDecodeError) as exc:
    print(f"[ERROR] cannot read {rules_path}: {exc}", file=sys.stderr)
    sys.exit(1)

if strategy_name == "tasty_credit_spreads":
    from bot.options.service import main as _options_main
    _options_main(rules_path)
    return
```
→ per RESEARCH.md Open Question 2 recommendation (smallest diff, keeps existing `tests/options/test_dispatch.py` shape): widen the same peeked dict to also check for the `strategies` key:
```python
try:
    with open(rules_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as exc:
    print(f"[ERROR] cannot read {rules_path}: {exc}", file=sys.stderr)
    sys.exit(1)

if data.get("strategy_name", "") == "tasty_credit_spreads" or "strategies" in data:
    from bot.options.service import main as _options_main
    _options_main(rules_path)
    return
```
Add a regression test case in `tests/options/test_dispatch.py` (existing file) asserting a `{"strategies": [...]}` payload also routes to `bot.options.service.main`.

---

### `backtester/options_run.py`

**Analog:** itself — `build_arg_parser` (lines 45-76) and `main()` (lines 176-230).

**Arg-parser pattern to add `--strategy` to** (line 51):
```python
parser.add_argument("--rules", default="rules_options.json", help="Path to rules_options.json")
```
→ add `parser.add_argument("--strategy", default=None, help="Strategy name to run (default: first in file)")` right after.

**`main()` insertion point — `legacy_view` must run BEFORE `apply_overrides`** (lines 203-213, D-10):
```python
with open(args.rules, "r", encoding="utf-8") as f:
    raw = json.load(f)
except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
    print(f"[ERROR] {exc}", file=sys.stderr)
    return 1

try:
    effective = apply_overrides(raw, args.set_args)
```
→ insert `raw = legacy_view(raw, args.strategy)` between the file read and the `apply_overrides(raw, args.set_args)` call; reject a `bull_call_spread` selection with a `[ERROR]` message + `return 1` at that same point (same `print(f"[ERROR] ...", file=sys.stderr); return 1` idiom used throughout this function, e.g. lines 205-213, 226-228).

---

### `docs/research/2026-09-24-super-bull-call-spread.md` (NEW)

**Analog:** `docs/research/2026-08-17-tastylive-options-research.md` — read that file's section headings (source URL, channel, publish date, retrieval method, distilled rules, deviations-from-source) and mirror them 1:1; do not reproduce the transcript (D-27 explicit constraint).

## Shared Patterns

### Fail-closed loader checks ("no conditional jsonschema")
**Source:** `bot/options/config.py:33-36,162-171`; sibling in `bot/config/loader.py:38,276` (`_IMPLEMENTED_EXIT_MODELS`)
**Apply to:** every new D-11 validation rule in `bot/options/config.py` — plain `if ...: raise ConfigError(...)` in Python, never a jsonschema `oneOf`/`anyOf`/`if-then-else` (zero precedent in this codebase, per RESEARCH.md).

### Guarded idempotent migration
**Source:** `bot/state/migrations.py:214-217` (`_migration_0004`)
**Apply to:** `_migration_0007` — `PRAGMA table_info(...)` existing-columns set, guard each `ALTER TABLE ADD COLUMN`, never edit a shipped migration (0001-0006 frozen).

### `NEEDS_ATTENTION` alert + audit triple
**Source:** `bot/options/service.py:759-767` (`_manage_position`'s close-incomplete branch)
```python
self._store.set_position_status(pid, "NEEDS_ATTENTION")
await self._alerter.send(f"<b>Options NEEDS ATTENTION</b> {_esc(...)} — ...")
append_audit({"event": "...", "position_id": pid, "underlying": ..., "reason": ...})
```
**Apply to:** D-29's startup-reconcile orphan-strategy guard (same three calls, new trigger condition: `pos["strategy_name"] not in loaded_strategy_names`).

### Raise, don't exit (loader boundary)
**Source:** `bot/options/config.py:127` docstring — "raises ConfigError; caller handles exit"; caller pattern at `bot/main.py:83-87` and `bot/options/service.py:960-964`:
```python
try:
    cfg = load_options_config(rules_path)
except ConfigError as exc:
    print(f"[ERROR] {exc}", file=sys.stderr)
    sys.exit(1)
```
**Apply to:** every new loader function (`load_options_book`, `legacy_view`) — never call `sys.exit` inside `bot/options/config.py` itself.

## No Analog Found

None — every file in this phase has a direct, verified in-tree analog (itself, pre-phase). This is a pure internal refactor with zero new external integration points.

## Metadata

**Analog search scope:** `bot/options/`, `bot/state/`, `bot/main.py`, `backtester/options_run.py`, `bot/config/loader.py` (sibling pattern), `docs/research/`
**Files scanned:** `bot/options/config.py`, `schema.py`, `strategy.py`, `store.py`, `service.py`, `bot/state/migrations.py`, `bot/state/store.py`, `bot/main.py`, `backtester/options_run.py`
**Pattern extraction date:** 2026-09-24
