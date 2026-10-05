# Phase 12: IBS ETF mean-reversion bot - Pattern Map

**Mapped:** 2026-10-04
**Files analyzed:** 30 (new/modified)
**Analogs found:** 28 / 30 (the two gaps are partial: `trading_days_between` and the parity test have no direct analog, the shapes are given in 12-RESEARCH.md Code Examples)

Primary template: `bot/options/` (Phase 8). `TradingBot` is a template only, not a base class.
Line numbers refer to the worktree as of 2026-10-04.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|---|---|---|---|---|
| `bot/ibs/__init__.py` | package marker | n/a | `bot/options/__init__.py` (17 lines, docstring only, no re-exports) | exact |
| `bot/ibs/schema.py` | config (jsonschema) | transform | `bot/options/schema.py` (OPTIONS_SCHEMA, `_HHMM_PATTERN`) | exact |
| `bot/ibs/config.py` | config loader | transform | `bot/options/config.py` (`_read_json`, `_validate`, `_check_strategy`, `_flatten`, `load_options_config`) | exact |
| `bot/ibs/strategy.py` | pure core | transform | `bot/options/strategy.py` (pure fns, no I/O); logic source `.scratch/strategy_search.py:51-94` | role-match |
| `bot/ibs/store.py` | model/store | CRUD | `bot/options/store.py` (`OptionsStore(StateStore)`, `get_meta`/`set_meta` at 224-240) | exact |
| `bot/ibs/execution.py` | service adapter | request-response + polling | `bot/options/execution.py` (`LegExecutor.fill_leg`, reused not copied) | exact (reuse) |
| `bot/ibs/service.py` | service/orchestrator | event-driven (scheduler) | `bot/options/service.py` (`OptionsBot`, `main`) | exact |
| `rules_ibs.json` | config | n/a | `rules_options.json` | exact |
| `bot/main.py` (modify) | dispatch | request-response | its own options branch, lines ~80-86 | exact |
| `bot/scanner/calendar.py` (modify) | utility | transform | `is_trading_day` / `get_prior_n_trading_days` in same file | exact |
| `bot/safety/logger.py` (modify) | utility | file-I/O | `configure_logging` in same file (lines 61-125) | exact |
| `bot/state/migrations.py` (modify, if Open Q1 = migration) | migration | CRUD | `_migration_0006` / `_migration_0007` (308-401), `MIGRATIONS` + `CURRENT_VERSION = 7` (404-415) | exact |
| `tests/conftest.py` (modify) | test fixture | n/a | `_isolate_bot_log` (lines 44-62) | exact |
| `tests/ibs/conftest.py` | test fixture | n/a | `tests/options/conftest.py` (literal rules dict + loaded cfg) | exact |
| `tests/ibs/test_schema_config.py` | test | transform | `tests/options/test_config.py` | exact |
| `tests/ibs/test_strategy.py`, `test_parity.py` | test | transform | `tests/options/test_strategy.py`; parity has none (see No Analog) | role-match |
| `tests/ibs/test_store.py` | test | CRUD | `tests/options/test_store.py` | exact |
| `tests/ibs/test_execution.py` | test | request-response | `tests/options/test_execution.py` | exact |
| `tests/ibs/test_service.py` | test | event-driven | `tests/options/test_service.py` | exact |
| `tests/ibs/test_dispatch.py` | test | request-response | `tests/options/test_dispatch.py` | exact |
| `tests/ibs/test_hygiene.py` | test (static grep/plistlib) | n/a | none direct; see Pattern notes | partial |
| `scripts/uat_ibs_probe.py` | script | request-response | `scripts/uat_options_probe.py` | exact |
| `deploy/com.bot.ibs.plist` | config | n/a | `deploy/com.bot.trading.plist` | exact (with 3 fixes) |
| `deploy/README.md` (runbook section) | doc | n/a | existing `deploy/README.md` | role-match |
| `docs/research/2026-10-04-ibs-etf-strategy-search.md` | doc | n/a | `docs/research/2026-08-18-external-strategies-results.md` | exact |
| `backtester/experimental/ibs_search/*.py` (6 files) | research scripts | batch | `.scratch/*.py` themselves (moved) | exact |

## Pattern Assignments

### `bot/ibs/schema.py` and `bot/ibs/config.py` (config, transform)

**Analog:** `bot/options/schema.py`, `bot/options/config.py`

Schema header style (schema.py 1-30): module docstring naming CFG-01, `_HHMM_PATTERN = r"^\d{2}:\d{2}$"`, a top-level `*_SCHEMA = {"type": "object", "required": [...], "additionalProperties": True, "properties": {...}}`. Structural only; business rules are plain Python in config.py.

Config imports (config.py 24-40):
```python
import copy, json, os
from dataclasses import dataclass
from typing import Optional
import jsonschema
from bot.config.loader import ConfigError          # ONE exception type for the whole bot
from bot.options.schema import OPTIONS_SCHEMA, STRATEGIES_SCHEMA
```
Read/validate helpers to copy (config.py 209-233), changing only the "rules_options.json" wording to "rules_ibs.json":
```python
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f: raw = f.read()
    except FileNotFoundError:
        raise ConfigError(f"rules_options.json not found: {path}")
    try: return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"rules_options.json is not valid JSON: {exc}") from exc

def _validate(data, schema):
    try: jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        field_path = " -> ".join(str(p) for p in exc.absolute_path) if exc.absolute_path else exc.validator_value
        raise ConfigError(f"... schema validation failed: {exc.message} (path: {field_path if field_path else exc.json_path})") from exc
```
Dataclass style: `OptionsConfig` (config.py 100+) is a flat class, JSON leaf names verbatim, universe stored as `tuple` (immutable). `IbsConfig` should do the same (use `@dataclass(frozen=True)`).

State-DB guard to copy (config.py 442-474; mirror constant at 52 asserted equal to `bot.state.store.DEFAULT_DB_PATH` by a test, not imported):
```python
_DEFAULT_EQUITY_STATE_DB = "data/bot_state.db"
... raise ConfigError("service.equity_state_db must not be the options bot's own state_db")
```
IBS version: abspath(`service.state_db`) must not equal `data/bot_state.db` or `data/options_state.db`. Cross-field checks are listed in 12-RESEARCH.md "Config shape". No Python defaults for strategy numbers (all required keys).

---

### `bot/ibs/strategy.py` (pure core, transform)

**Analog:** `bot/options/strategy.py` (pure functions, no I/O, no calendar import). Logic source (verbatim decision rules) `.scratch/strategy_search.py:71-92` (will live at `backtester/experimental/ibs_search/strategy_search.py`):
```python
exits = [j for j, (_, _, d0) in pos.items() if X[t, j] or t - d0 >= max_hold]
...
free = slots - (len(pos) - (len(exits) if next_open else 0))
cand = [j for j in np.where(E[t])[0] if j not in pos and np.isfinite(Cv[t, j])]
cand.sort(key=lambda j: R[t, j])          # stable: IBS asc, then column order
for j in cand[:free]: ...
```
Function shapes (parse_snapshot, compute_ibs, decide_exits, decide_entries, size_position, trading_days_held) are given in 12-RESEARCH.md "Pure strategy core". Notes: exits are popped before candidates are built, so same-day re-entry after a time exit is the research behaviour (Pitfall 4); the parity test must cover it.

---

### `bot/ibs/store.py` (store, CRUD)

**Analog:** `bot/options/store.py`

Header and idioms (lines 1-60): subclass `StateStore` (inherits `open()/close()/_lock/_conn`/migrations), writes `with self._lock:` then `commit()`, reads flip `row_factory = sqlite3.Row` inside the lock and reset before returning plain dicts, `?` placeholders only, column tuple constants (`_POSITION_COLUMNS`) in migration order. Insert idiom (store.py 55-70): `cols = [c for c in _POSITION_COLUMNS if pos.get(c) is not None]` then `INSERT INTO t (cols) VALUES (?...)`.

Meta pattern to copy verbatim (store.py 224-240):
```python
def get_meta(self, key):
    with self._lock:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None
def set_meta(self, key, value):
    with self._lock:
        self._conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._conn.commit()
```
Table DDL: see 12-RESEARCH.md "Store design". Table names must be `ibs_positions/ibs_trades/ibs_orders` (never `positions`, `trades`, `pending_intents`, because `OpenDWatchdog` runs equity `startup_reconcile` against this store).

If migration route (Open Q1 recommendation): follow `_migration_0006` (migrations.py 308+): `conn.execute("CREATE TABLE IF NOT EXISTS ...")` per statement (not executescript, WR-03), idempotent, never edit shipped migrations; append `_migration_0008` to `MIGRATIONS` (404-412), bump `CURRENT_VERSION = 8` (415), and update `tests/state/test_migrations.py` (imports `CURRENT_VERSION`, `MIGRATIONS`, `_migration_0007`). Alternative: `IbsStore.open()` override running the same `CREATE TABLE IF NOT EXISTS` after `super().open()`.

---

### `bot/ibs/execution.py` (adapter, request-response/polling)

**Analog:** `bot/options/execution.py` (`LegExecutor`, lines 36-132 read). Import to reuse, not copy:
```python
from bot.options.execution import LegExecutor
```
Signature and contract:
```python
async def fill_leg(self, code, side, qty, bid, ask, aggressive=False, on_placed=None)
# returns (order_id, avg_price, filled_qty) or None; raises RuntimeError on unconfirmed cancel (CR-04)
# price = mid +/- cfg.limit_buffer_usd; max(round(price,2), 0.01); orders placed via
#   self._gw.place_order(code, int(qty), price, trd_side)
```
cfg attributes it reads: `limit_buffer_usd, poll_interval_s, ttl_s, escalation_step_usd, max_retries`. Adapter (two `SimpleNamespace` configs, `bid = ask = last`, `asyncio.wait_for` deadline) is in 12-RESEARCH.md Pattern 4. Add one test that fails if those attribute names change. Helpers already shared: `bot.execution.engine._get_trd_side_buy/_get_trd_side_sell`.

---

### `bot/ibs/service.py` (service, event-driven)

**Analog:** `bot/options/service.py`

Imports (service.py 32-70), swap options for ibs:
```python
import asyncio, html, os, sys
from datetime import date, datetime, time as _time, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger     # IBS also needs DateTrigger (apscheduler.triggers.date)
from bot.config.loader import ConfigError
from bot.gateway.gateway import MoomooGateway, get_gateway_config
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.kill_switch import KillSwitch
from bot.safety.logger import configure_logging, get_logger
from bot.scanner.calendar import get_market_close_et, is_trading_day
from bot.service.alerter import TelegramAlerter
from bot.service.report import write_reports
from bot.service.watchdog import OpenDWatchdog
```
Helpers to copy (223-245): `_parse_hhmm`, `_esc = html.escape(str(v))`, `_signed`. `_manage_cutoff` (229-234) shows the close-time construction: `datetime.combine(day, _time(hour, minute), tzinfo=_ET)` from `get_market_close_et(day)`.

Constructor and watchdog duck-typing (378-420): store `_cfg/_gateway/_store/_kill_switch/_alerter/_watchdog/_watchdog_task`, `AsyncIOScheduler(timezone=_ET)`, `self._entries_enabled = False`, `self._lock = asyncio.Lock()`, `self._position_manager = None`, `self._bar_agg = None`.

Readiness gate (427-445), copy shape:
```python
result = self._gateway.connect()
if asyncio.iscoroutine(result): await result
await self.reconcile(startup=True)
self._kill_switch.install()
self._entries_enabled = True
```
Reconcile (451-582): `broker = await self._gateway.get_option_positions()` becomes `await self._gateway.get_positions()` (gateway.py:494, `refresh_cache=True` default, returns a tuple); `statuses = ("OPEN","OPENING","CLOSING") if startup else ("OPEN",)`; mismatch sets `NEEDS_ATTENTION` + `await alerter.send(f"<b>IBS NEEDS ATTENTION</b> {_esc(...)} ...")` + `append_audit({"event": ..., ...})`. Codes with no IBS row: counted/logged only (SAFE-OG-01).

Job registration shape (600-640), kwargs reused by IBS `arm_today`:
```python
common = dict(coalesce=True, max_instances=1, misfire_grace_time=_MISFIRE_GRACE_S)
self._scheduler.add_job(self._job_eod, CronTrigger(hour=hour, minute=minute, timezone=_ET), id="options_eod", **common)
```
IBS differs: a daily CronTrigger `ibs_arm` arms three one-shot `DateTrigger` jobs (`ibs_decide`, `ibs_hard_cancel`, `ibs_eod`) with `replace_existing=True` (full code in 12-RESEARCH.md Pattern 3; lesson from `bot/service/bot.py:630-665`). No `force_close` job.

EOD job (1392-1430), copy structure: guard `is_trading_day`, send summary via `await self._alerter.send(...)`, `os.makedirs(self._cfg.report_dir, exist_ok=True)` BEFORE `write_reports(html, today.isoformat(), report_dir=self._cfg.report_dir)` (non-recursive mkdir), `except asyncio.CancelledError: raise` then `except Exception: _logger.error("..._error", exc_info=True)`.

Shutdown/run loop (1433-1500): `_shutdown` = audit, gateway.close, alert, `scheduler.shutdown(wait=False)` each in its own try/except. IBS must reorder: cancel in-flight decision task, sweep non-terminal `ibs_orders`, then `gateway.close()` (see Pattern 5). Run loop:
```python
while not self._kill_switch.triggered:
    if self._kill_switch.check_file(): self._kill_switch.trigger("sentinel_file")
    await asyncio.sleep(1)
```
with `finally:` cancel and await `_watchdog_task`, then `await self._shutdown()`.

`main(rules_path)` (1506-1562), copy order: `configure_logging()` (IBS: `configure_logging(log_name="ibs.log", force=True)`), load config with `except ConfigError as exc: print(f"[ERROR] {exc}", file=sys.stderr); sys.exit(1)`, `gateway = MoomooGateway(get_gateway_config())`, `os.makedirs(os.path.dirname(cfg.state_db) or ".", exist_ok=True)`, `store = IbsStore(cfg.state_db).open()`, `TelegramAlerter(token=os.environ.get("TELEGRAM_BOT_TOKEN",""), chat_id=os.environ.get("TELEGRAM_CHAT_ID",""), logger=_log)`, `KillSwitch(sentinel_path=cfg.kill_file)`, build bot with `watchdog=None`, then `bot._watchdog = OpenDWatchdog(gateway=gateway, bot=bot, alerter=alerter, cfg=cfg)`, `asyncio.run(bot.run())`. `OpenDWatchdog.__init__(gateway, bot, alerter, cfg)` is at `bot/service/watchdog.py:52`; cfg must carry `watchdog_poll_interval_s`, `watchdog_reconnect_initial_s`, `watchdog_reconnect_cap_s`.

---

### `bot/main.py` (modify, dispatch)

**Analog:** its own options branch. Current text (`bot/main.py` ~lines 80-86):
```python
if data.get("strategy_name", "") == "tasty_credit_spreads" or "strategies" in data:
    from bot.options.service import main as _options_main
    _options_main(rules_path)
    return
```
Add a sibling above the equity loader (note `configure_logging()` is called at line 67 BEFORE the peek, hence the logger change):
```python
if data.get("strategy_name", "") == "ibs_etf_mean_reversion":
    from bot.ibs.service import main as _ibs_main
    _ibs_main(rules_path)
    return
```
Keep the options condition untouched.

---

### `bot/safety/logger.py` (modify)

**Analog:** itself. Current signature (line 61): `def configure_logging(log_dir: str = _DEFAULT_LOG_DIR, level: str = _DEFAULT_LEVEL) -> None:` with `global _configured; if _configured: return`, `log_file = os.path.join(log_dir, "bot.log")`, and `root_logger.handlers.clear()` already present. Change: add keyword-only `*, log_name: str = "bot.log", force: bool = False`; guard becomes `if _configured and not force: return`; `log_file = os.path.join(log_dir, log_name)`. Keyword-only defaults live in `__kwdefaults__`, so the `__defaults__` patch in `tests/conftest.py:44-62` keeps working. Extend `tests/safety/test_logger.py`.

### `bot/scanner/calendar.py` (modify)

**Analog:** `is_trading_day` (lines 32-43) uses `_nyse.valid_days(start_date=dt, end_date=dt)`. Add (`timedelta` import needed; file currently imports only `date`):
```python
def trading_days_between(start_exclusive: date, end_inclusive: date) -> list:
    valid = _nyse.valid_days(start_date=start_exclusive + timedelta(days=1), end_date=end_inclusive)
    return [d.date() for d in valid]
```
Update the `Exports:` line in the docstring.

---

### `tests/conftest.py` (modify, audit-log isolation)

**Analog:** `_isolate_bot_log` (44-62):
```python
@pytest.fixture(scope="session", autouse=True)
def _isolate_bot_log(tmp_path_factory):
    from bot.safety import logger
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(logger.configure_logging, "__defaults__",
                   (str(tmp_path_factory.mktemp("logs")), logger._DEFAULT_LEVEL))
        yield
```
Add a sibling `_isolate_audit_log` with the same shape: `mp.setattr(audit_log, "AUDIT_LOG_PATH", str(tmp_path_factory.mktemp("audit") / "audit.jsonl"))` (module global read at call time, `bot/safety/audit_log.py:~47`). Existing safety tests that set the attribute themselves keep working.

---

### `tests/ibs/conftest.py` and test modules

**Analog:** `tests/options/conftest.py` (module docstring listing fixtures; `options_rules` is a LITERAL dict, not derived from loader code, so the drift-guard test comparing it to the shipped file is not circular; `options_cfg` writes it to `tmp_path` and loads it). IBS: `ibs_rules` literal (shape in 12-RESEARCH.md "Config shape"), `ibs_cfg` via `load_ibs_config(str(path))`.

`tests/options/test_service.py` fixtures to copy (lines 1-100):
```python
def _run(coro): return asyncio.run(coro)          # no pytest-asyncio; plain asyncio.run
@pytest.fixture
def store(tmp_path):
    st = OptionsStore(str(tmp_path / "options.db")).open(); yield st; st.close()   # REAL store on tmp DB
@pytest.fixture
def alerter(): return MagicMock(send=AsyncMock(), _enabled=True)
@pytest.fixture
def gateway():
    gw = MagicMock(); gw.connect = MagicMock(); gw.close = MagicMock()
    gw.get_market_snapshot = AsyncMock(return_value=(0, [])); ...; return gw
@pytest.fixture
def make_bot(...):  # OptionsBot(cfg=..., gateway=..., store=..., kill_switch=MagicMock(triggered=True, check_file=MagicMock(return_value=False)), alerter=...)
```
IBS gateway mock needs `get_positions`, `get_market_snapshot`, `place_order`, `cancel_order`, `get_order_status` as `AsyncMock`. Snapshot row fixture shape is in 12-RESEARCH.md "Live facts" (note the gateway returns `(ret, DataFrame)`; options tests iterate `snap.iterrows()`).

`tests/options/test_dispatch.py` (lines 1-56) to copy for `tests/ibs/test_dispatch.py`: `_write_rules(tmp_path, payload)`, `_patch_equity_seams(monkeypatch)` (patches `bot.main.MoomooGateway`, `bot.main.StateStore`, `bot.main.asyncio.run`), then:
```python
seen = []
monkeypatch.setattr(bot.options.service, "main", lambda path: seen.append(path))   # IBS: bot.ibs.service
bot.main.main(rules_path=rules)
assert seen == [rules]; mock_gateway_cls.assert_not_called()
```
Also a test that the shipped repo-root `rules_ibs.json` dispatches (mirrors `test_shipped_rules_options_dispatches_to_options_main`). Leave the options dispatch tests untouched as the regression set.

`tests/ibs/test_hygiene.py`: no analog; implement with `pathlib` grep over `bot/ibs/*.py` for `OrderType.MARKET`, `force_close`, `unlock_trade`, `manage_exit`; `plistlib.load` for the plist; `py_compile` + no `/Users/acdc` for `backtester/experimental/ibs_search/*.py`.

---

### `scripts/uat_ibs_probe.py` (script)

**Analog:** `scripts/uat_options_probe.py`. Copy:
- shebang + docstring with Usage block (`PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 scripts/... [--rules ...]` and the `--live-1lot ... --confirm` line).
- `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` then imports with `# noqa: E402` (gateway, config loader, strategy fns, `now_et`).
- Helpers `_p(*a): print(*a, flush=True)` and `_hr(title)` (78-char `=` banner).
- `main()` (lines 252-278): argparse with `--rules` (default `rules_ibs.json`), `--live-1lot` store_true, `--symbol`, `--confirm`; `if a.live_1lot and not a.confirm: _p("--live-1lot places PAPER orders; add --confirm to proceed."); sys.exit(2)`; `gw = MoomooGateway(get_gateway_config()); gw.connect()` (paper guard); `try: asyncio.run(_run()) finally: gw.close()`.
- `live_1lot` uses a scratch DB under `tempfile` and prints realized friction; IBS version: BUY 1 share (e.g. US.XLU) via the same `IbsExecutor`, observe fill, SELL it, report friction.
Read-only mode must import the SAME `parse_snapshot/compute_ibs/decide_*/size_position` as the service so the probe cannot drift, print per-code age and `(ask - bid)` (to calibrate `max_snapshot_age_s`, buffers), and print broker share holdings.

---

### `deploy/com.bot.ibs.plist`

**Analog:** `deploy/com.bot.trading.plist` (whole file read). Copy the comment header, `Label`, `ProgramArguments` (venv python, `-m`, `bot`, add `--rules`, `rules_ibs.json`), `WorkingDirectory`, `RunAtLoad`, `ThrottleInterval 30`, `EnvironmentVariables` (`PAPER_TRADING=true`, `FUTU_TRD_ENV=SIMULATE`, `FUTU_OPEND_HOST/PORT`, `TELEGRAM_*` placeholders, explicit `PATH`), `StandardOutPath`/`StandardErrorPath` (`logs/ibs.stdout.log`, `logs/ibs.stderr.log`). Three required deviations (12-RESEARCH.md Pitfall 8):
1. `KeepAlive` as dict `{SuccessfulExit: false}` instead of `<true/>` (kill-file exit 0 must not restart).
2. Add `FUTU_ACC_ID` = `1727266` (missing in the template; paper guard Guard 3 fails otherwise) and `PYTHONUNBUFFERED=1`.
3. `Label` `com.bot.ibs`; keep `chmod 600` / do-not-commit instructions.

### `docs/research/2026-10-04-ibs-etf-strategy-search.md`

**Analog:** `docs/research/2026-08-18-external-strategies-results.md`: title with phase and requirement ids, "Dated" line citing the pre-registration commit, asset locations, numbered sections starting with `## 1. Executive summary` (bold verdict, per-hypothesis outcome, explicit "Recommendation"), later sections for tables/limitations. Pair with hypotheses file style `2026-08-18-external-strategies-hypotheses.md` for pre-registered pass criteria.

### `backtester/experimental/ibs_search/*.py`

Source files in `.scratch/`: `strategy_search.py`, `strategy_search_r2.py`, `ibs_robust.py`, `r5_robust.py`, `ibs_sizing.py`, `screen_study.py`. Only change path constants (hard-coded `ROOT = "/Users/acdc/..."` and `.scratch/` reads; lines listed in 12-RESEARCH.md Pitfall 11). `strategy_search.py` downloads data at import, so tests must AST-extract `simulate` (lines 51-94), never import it.

## Shared Patterns

### Fail-closed config
**Source:** `bot/options/config.py` (`ConfigError` re-used from `bot.config.loader`; the process entry catches it, prints `[ERROR] ...` to stderr, `sys.exit(1)`). Loader never calls `sys.exit`. **Apply to:** `bot/ibs/config.py`, `bot/ibs/service.py:main`.

### Alert formatting
**Source:** `bot/options/service.py:237-310`. Prefix each body with `<b>IBS ...</b>`, wrap every interpolated value in `_esc()`, never include broker error strings or the token. `TelegramAlerter(token, chat_id, logger)` (`bot/service/alerter.py:62`), `await alerter.send(text)` never raises. **Apply to:** all IBS alerts and the EOD summary.

### Audit
**Source:** `append_audit({"event": "...", ...})` from `bot.safety.audit_log` wrapped so a failure never blocks (`try/except Exception: pass` in `_shutdown`). Event names `ibs_*`. Gateway `place_order` already audits every order. **Apply to:** reconcile mismatches, shutdown, hard-cancel sweep.

### Job error handling
**Source:** `_job_eod` tail: `except asyncio.CancelledError: raise` / `except Exception: _logger.error("..._error", exc_info=True)`. **Apply to:** every scheduled IBS job.

### Naming (D6)
DB `data/ibs_state.db`, kill file `.bot_kill_ibs`, report dir `reports/ibs` (with `os.makedirs`), log `logs/ibs.log` (new, needs the logger change; options has none), job ids `ibs_arm/ibs_decide/ibs_hard_cancel/ibs_eod`, audit events `ibs_*`.

## No Analog Found

| File | Role | Reason |
|---|---|---|
| `trading_days_between` (calendar) | utility | No range-count helper exists; use `valid_days` as in `is_trading_day` |
| `tests/ibs/test_parity.py` | test | No research-vs-production parity test exists; build per 12-RESEARCH.md (AST-extract `simulate`, synthetic ~25-session scenario incl. same-day re-entry and tie cases) |
| `tests/ibs/test_hygiene.py` | test | No static-source/plist test precedent in `tests/options/` |
| Per-bot log file | utility | Both existing bots share `logs/bot.log`; logger change required |
| Audit-log isolation | test fixture | None exists today (Pitfall 2); modeled on `_isolate_bot_log` |

## Metadata

**Analog search scope:** `bot/options/`, `bot/main.py`, `bot/safety/logger.py`, `bot/scanner/calendar.py`, `bot/state/migrations.py`, `bot/service/{watchdog,alerter,report}.py`, `bot/gateway/gateway.py` (signatures only), `tests/conftest.py`, `tests/options/`, `scripts/uat_options_probe.py`, `deploy/`, `docs/research/`, `.scratch/strategy_search.py`
**Pattern extraction date:** 2026-10-04
