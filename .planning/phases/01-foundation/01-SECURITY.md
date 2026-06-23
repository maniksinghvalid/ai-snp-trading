---
phase: 01-foundation
status: secured
threats_total: 16
threats_closed: 16
threats_open: 0
asvs_level: default
block_on: high
register_authored_at_plan_time: true
audit_date: 2026-06-23
test_evidence: "python3 -m pytest tests/ -q → 217 passed"
---

# SECURITY.md — Phase 1 (Foundation) Threat Verification

**Phase:** 01 — Foundation (broker access, paper guard, state store, strategy layer, safety primitives)
**Audit date:** 2026-06-23
**ASVS level:** default (config did not specify 1/2/3)
**block_on:** high
**Register provenance:** authored at plan time (verification-only audit — no new-threat scanning)
**Test evidence:** `python3 -m pytest tests/ -q` → 217 passed

This document records the disposition and evidence for every threat in the Phase 1
STRIDE register (PLAN files 01-01 through 01-04). Implementation files were read-only during
the audit; the single open item (T-01-SC) was subsequently remediated by pinning the two
unpinned dependencies in `requirements.txt`.

---

## Threat Verification Summary

- **threats_total:** 16
- **closed:** 16
- **open:** 0

---

## Closed Threats

| Threat ID | Category | Disposition | Evidence (file:line) |
|-----------|----------|-------------|----------------------|
| T-01-01 | Elevation of Privilege | mitigate | `bot/safety/paper_guard.py:64-131` — triple fail-closed: Guard 1 `cfg.paper_trading` (L79), Guard 2 `cfg.trd_env != "SIMULATE"` (L83), Guard 3 broker `get_acc_list()` trd_env for explicit acc_id (L91-123); each calls `_fail()` → raises `PaperGuardError` before contexts usable. Gate invoked at `bot/gateway/gateway.py:269` in `connect()` after context creation. Tests: `tests/safety/test_paper_guard.py` (four fail cases + pass case, all green). |
| T-01-02 | Tampering | mitigate | `bot/safety/audit_log.py:47` — `open(AUDIT_LOG_PATH, "a", ...)` append mode, never truncates. Refusal events recorded via `_fail()` in `paper_guard.py:52-56`. Test `tests/safety/test_audit_log.py:49,66` proves two calls → two lines and first line byte-for-byte preserved. |
| T-01-03 | Spoofing | mitigate | `bot/safety/paper_guard.py:108-131` — no auto-selection; iterates `get_acc_list()` rows matching exactly `safe_int(cfg.acc_id)` (L114-115); reads broker-reported `trd_env` for that row only (L118); `acc_id` not found → `_fail()` (L127-131). `acc_id` default 0 + `paper_trading=False` default in `gateway.py:65-66` forces explicit operator config. |
| T-01-04 | Information Disclosure | mitigate | `bot/gateway/gateway.py:51-66` — `GatewayConfig` stores no `login_account`/`login_pwd` (docstring L57 documents deliberate omission); `get_gateway_config()` reads no password env var. Grep confirms zero credential reads/stores in `bot/`. Gateway never logs `self.cfg`. |
| T-01-05 | Denial of Service | **ACCEPT** | `bot/gateway/gateway.py:119-143` — `_check_opend_alive` raises a clear `ConnectionError` (not an unhandled crash) on refused/unreachable OpenD; called first in `connect()` (L265). Genuinely handled & explicitly accepted: bot non-functional without OpenD by design. See Accepted Risks Log. |
| T-01-SC | Tampering (supply chain) | mitigate | `requirements.txt` — ALL runtime deps now version-constrained: `moomoo-api>=10.4.6408,<11.0`, `pandas>=2.0,<4.0`, `numpy==2.5.0`, `jsonschema>=4.0,<5.0`, `structlog==26.1.0`. The previously-unpinned `pandas` and `jsonschema` are now bounded (covering installed pandas 3.0.3 / jsonschema 4.26.0). All packages are legitimate, well-known PyPI projects (no typosquat). "No unpinned packages" guarantee now met. **Remediated 2026-06-23.** |
| T-01-06 | Tampering | mitigate | `bot/state/store.py:47-120` — `atomic_write_json`: same-dir `tempfile.mkstemp` (L78), `json.dump`+flush+`os.fsync` (L82-85), re-read `json.load` parse-validate (L89-90), `os.replace` atomic swap (L93), temp unlinked on any pre-swap exception (L113-119). Crash-injection test `tests/state/test_atomic_write.py:113-152` monkeypatches `os.replace` to raise, asserts original byte-for-byte unchanged. |
| T-01-07 | Information Disclosure | mitigate | `bot/state/store.py:109` — `os.chmod(path, S_IRUSR\|S_IWUSR)` (0600) after swap. `DEFAULT_DB_PATH = data/bot_state.db` (L28), NOT `/tmp`; `data/` gitignored (`.gitignore:3`). Test `tests/state/test_atomic_write.py:94-100` asserts 0600. |
| T-01-08 | Denial of Service | **ACCEPT** | `bot/state/migrations.py:41-90` — schema uses `CREATE TABLE IF NOT EXISTS` (resilient idempotent open); full reconstruct-from-broker on corrupt DB is explicitly Phase 4 scope. See Accepted Risks Log. |
| T-01-09 | Tampering | mitigate | `bot/config/loader.py:106-141` — `FileNotFoundError`/`json.JSONDecodeError`/`jsonschema.ValidationError` each → `ConfigError` with field path; `bot/config/schema.py` enforces required groups + typed fields. Fails fast before any strategy decision. Tests: `tests/config/test_loader.py`. |
| T-01-10 | Repudiation | mitigate | `bot/strategy/trend_join_long.py` reads every threshold from `self._cfg.*` (config-driven; no Python-literal override). Behavioral proof: `tests/strategy/test_config_driven.py` swaps baseline vs modified `StrategyConfig` and asserts filter output changes (D-12). `loader.py:41-56` `parse_initial_stop_rule` refuses silent fallback (CR-01). |
| T-01-11 | Information Disclosure | **ACCEPT** | `bot/strategy/{core,indicators,trend_join_long}.py` import only `abc`, `typing`, `math`, `pandas`, pure `bot.*` — grep: no network, no `open(`, no `sqlite`, no `moomoo`, no `os.getenv`. Pure-logic layer. See Accepted Risks Log. |
| T-01-12 | Denial of Service | mitigate | `bot/safety/kill_switch.py:149-188` — `_trigger` sets `threading.Event` (L163), runs flush callbacks (L176-180), guarded by `RLock` + `_triggered_once` (L157-160) for idempotency. Tests: `tests/safety/test_kill_switch.py:187` (trigger twice → flush once) and SIGINT-path equivalence. |
| T-01-13 | Tampering | mitigate | `bot/safety/kill_switch.py:183-187` — shutdown writes append-only `append_audit({"event": "kill_switch", ...})`. Test `tests/safety/test_kill_switch.py:104,163` asserts `event == "kill_switch"`; append-only preservation covered by `test_audit_log.py`. |
| T-01-14 | Repudiation / Correctness | mitigate | `bot/safety/et_helpers.py:21` — `ET = ZoneInfo("America/New_York")`; aware datetimes; grep confirms no `pytz`, no fixed `timedelta(hours=` offset. DST test `tests/safety/test_et_helpers.py:61-78` asserts July=-4h (EDT), January=-5h (EST). |
| T-01-15 | Information Disclosure | mitigate | `bot/safety/logger.py:118-156` — processor chain logs timestamp/level/logger-name/event only; no `FutuConfig`/password/token/credential reference (grep confirms NONE). |

---

## Open Threats

**None.** All 16 threats are CLOSED (13 mitigated with code+test evidence, 3 accepted risks).
T-01-SC was OPEN at audit time (unpinned `pandas`/`jsonschema`) and remediated the same day by
adding version bounds in `requirements.txt`.

---

## Accepted Risks Log

| Threat ID | Category | Accepted Because | Re-evaluate In |
|-----------|----------|------------------|----------------|
| T-01-05 | Denial of Service (OpenD connectivity) | Pre-flight `_check_opend_alive` raises a clear `ConnectionError` (`bot/gateway/gateway.py:119-143`); bot is non-functional without OpenD by design. No unhandled crash, no real-money exposure. | Phase 5 (watchdog / auto-reconnect) |
| T-01-08 | Denial of Service (corrupt DB on open) | `CREATE TABLE IF NOT EXISTS` makes open idempotent/resilient (`bot/state/migrations.py`); full reconstruct-from-broker recovery deferred. | Phase 4 (reconciliation / recovery) |
| T-01-11 | Information Disclosure (strategy layer) | Pure-logic layer: no credentials read, no I/O (grep-verified). Nothing sensitive to disclose. | If strategy layer ever gains I/O |

---

## Unregistered Flags (new attack surface w/o threat mapping)

Every plan's SUMMARY `## Threat Flags` section maps to an existing registered threat ID —
**no unregistered flags found**:

| SUMMARY source | Flagged surface | Maps to |
|----------------|-----------------|---------|
| 01-01 | `gateway.py` socket TCP check | T-01-05 (accept) |
| 01-01 | `audit_log.py` write to `~/.futu_trade_audit.jsonl` | T-01-02 (mitigate) |
| 01-02 | `atomic_write_json` | T-01-06 (mitigate) |
| 01-02 | snapshot file permissions | T-01-07 (mitigate) |
| 01-02 | corrupt DB on open | T-01-08 (accept) |
| 01-03 | `loader.py` reads local `rules.json` | T-01-09 (mitigate) |
| 01-03 | `bot/strategy/` pure logic | T-01-11 (accept) |
| 01-04 | `logger.py` write to `logs/bot.log` | T-01-15 (mitigate) |
| 01-04 | `kill_switch.py` sentinel read + audit write | T-01-12 / T-01-13 (mitigate) |

---

## Security Audit 2026-06-23

| Metric | Count |
|--------|-------|
| Threats found | 16 |
| Closed | 16 |
| Open | 0 |

Initial audit (gsd-security-auditor): 15/16 closed, T-01-SC open (unpinned `pandas`/`jsonschema`).
Remediation: pinned `pandas>=2.0,<4.0` and `jsonschema>=4.0,<5.0` in `requirements.txt` → T-01-SC CLOSED.
Final: threats_open = 0.

---

## Verdict

**SECURED** — 16/16 threats closed (13 mitigated with code+test evidence, 3 documented accepted
risks). All safety-critical money-path threats (paper guard, audit immutability, atomic state,
config validation, kill switch, timezone correctness) are CLOSED. The supply-chain pinning gap
(T-01-SC) was remediated by version-bounding all runtime dependencies.
