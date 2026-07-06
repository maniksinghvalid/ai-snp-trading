# SECURITY.md — Phase 05: Service Orchestration and Reliability

**Phase:** 05 — service-orchestration-and-reliability
**Audit date:** 2026-06-24
**ASVS Level:** 2
**Auditor:** gsd-security-auditor (claude-sonnet-4-6)
**Verdict:** SECURED — all 30 registered threats CLOSED (28 mitigated, 2 accepted)

---

## Threat Verification

| Threat ID | Category | Disposition | Status | Evidence |
|-----------|----------|-------------|--------|----------|
| T-05-SC | Tampering | mitigate | CLOSED | requirements.txt:15 `apscheduler==3.11.2` exact pin; plan records human gate approval before install |
| T-05-00-01 | Information Disclosure | mitigate | CLOSED | rules.json contains no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID; .env.example:38-46 documents both as blank; loader.py:179 `svc_cfg = data.get("service", {})` maps only non-secret toggles |
| T-05-00-02 | Tampering | mitigate | CLOSED | rules.json:71 `"force_close_misfire_grace_s": 300`; bot/service/bot.py:192 `misfire_grace_time=self._cfg.force_close_misfire_grace_s` — config-driven, not None |
| T-05-00-03 | Spoofing | accept | CLOSED | Accepted risk: callbacks at this layer receive only position fields (code, qty, price, stop); no network sink or HTML rendering occurs here; alerter escaping is applied in 05-03 (T-05-03-03) |
| T-05-01-01 | Elevation of Privilege | mitigate | CLOSED | bot/service/bot.py:235-250 `_readiness_gate`: gateway.connect() (paper guard) -> startup_reconcile -> reconstruct_from_store -> register_flush -> _entries_enabled=True in that exact order; entries_enabled=False at construction (line 96) |
| T-05-01-02 | Tampering | mitigate | CLOSED | bot/service/bot.py:192 force_close job uses `misfire_grace_time=self._cfg.force_close_misfire_grace_s` (300 from rules.json); force_close_all() in Phase 4 retains its own internal time check |
| T-05-01-03 | Denial of Service | mitigate | CLOSED | bot/service/bot.py:271,302,344,413 all synchronous scanner/I-O calls wrapped in `loop.run_in_executor(None, ...)` — event loop never blocked |
| T-05-01-04 | Information Disclosure | mitigate | CLOSED | bot/main.py:66-67 reads secrets into local vars; bot/main.py log calls (lines 55,62) never interpolate telegram_token/chat_id; only ConfigError text (no secrets) is printed to stderr |
| T-05-01-05 | Tampering | mitigate | CLOSED | bot/service/bot.py:139 `register_flush(flush_all)` in _register_jobs; bot/service/bot.py:246 again in _readiness_gate; finally block (lines 521-530) cancels watchdog + calls _shutdown() which writes audit (line 453), closes gateway (line 462), dispatches final alert (line 469) |
| T-05-01-SAFE | Elevation of Privilege | mitigate | CLOSED | bot/gateway/gateway.py:291 `assert_paper_account(self.cfg, self._trade_ctx)` called inside connect(), which is the first step of _readiness_gate() at bot/service/bot.py:235 — paper guard runs before _entries_enabled=True |
| T-05-02-01 | Elevation of Privilege | mitigate | CLOSED | bot/service/watchdog.py:129 `self._bot._entries_enabled = False` in `_on_disconnect()` — set before any reconnect attempt (reconnect_loop started via create_task at line 145) |
| T-05-02-02 | Tampering | mitigate | CLOSED | bot/service/watchdog.py:203-223 `_on_reconnect`: `startup_reconcile` awaited (line 203), re-subscribe (lines 209-220), then `_entries_enabled=True` (line 223) — ordering enforced in code |
| T-05-02-03 | Denial of Service | mitigate | CLOSED | bot/service/watchdog.py:161-176: `delay = cfg.watchdog_reconnect_initial_s`; `delay = min(delay * 2, cap)` with `cap = cfg.watchdog_reconnect_cap_s` (both from rules.json) |
| T-05-02-04 | Information Disclosure | mitigate | CLOSED | bot/service/watchdog.py:132-135 disconnect alert is the literal string `"<b>OpenD DISCONNECTED</b> — new entries paused. Reconnecting..."` — no token, exception text, or credentials interpolated; reconnect text similarly fixed at line 227 |
| T-05-02-05 | Tampering | mitigate | CLOSED | bot/service/watchdog.py only sets `_bot._entries_enabled`; no force_close, exit order, or stop-out path is referenced in the watchdog — exits remain armed |
| T-05-03-01 | Information Disclosure | mitigate | CLOSED | bot/service/alerter.py:81 token used only in `_API_URL.format(token=self._token)` inside `_post_blocking`; warning log at line 118 logs only `chat_id`; no other log line references `self._token` |
| T-05-03-02 | Denial of Service | mitigate | CLOSED | bot/service/alerter.py:114 `await loop.run_in_executor(None, self._post_blocking, text)`; `_TIMEOUT_S = 10` at line 60 bounds the call; callers dispatch via asyncio.create_task |
| T-05-03-03 | Spoofing | mitigate | CLOSED | bot/service/alerter.py:145 `html.escape(str(code))` in format_entry_alert; lines 170-171 `html.escape(str(code))` and `html.escape(str(exit_reason))` in format_exit_alert; parse_mode HTML |
| T-05-03-04 | Tampering | mitigate | CLOSED | bot/service/alerter.py:112-121 `send()` wraps run_in_executor in `try/except Exception` that warning-logs and returns normally — never re-raises (ALERT-04) |
| T-05-03-05 | Information Disclosure | accept | CLOSED | Accepted risk: `_API_URL = "https://api.telegram.org/bot{token}/sendMessage"` (alerter.py:59) uses HTTPS; urllib.request.urlopen defaults to TLS certificate verification on https:// URLs; fixed host (api.telegram.org); no user-controlled URL component — no SSRF surface |
| T-05-04-01 | Spoofing | mitigate | CLOSED | bot/service/report.py:260-261 `html.escape(str(t.get('code')))` and `html.escape(str(t.get('exit_reason')))` in closed-trades rows; lines 270-271 same for open-positions code/phase; line 284 session_date_str escaped in title |
| T-05-04-02 | Information Disclosure | mitigate | CLOSED | deploy/com.bot.trading.plist:89,92 contains only placeholder strings `YOUR_TOKEN_HERE`/`YOUR_CHAT_ID_HERE`; deploy/README.md:26,35 mandates `chmod 600`; README.md:29 explicitly states "DO NOT commit a filled-in copy" |
| T-05-04-03 | Tampering | mitigate | CLOSED | bot/state/store.py:196-197 WAL mode enabled (`PRAGMA journal_mode=WAL`); _job_eod_report uses read-only accessors (get_closed_trades, get_open_positions); no schema mutation in Phase 5 |
| T-05-04-04 | Denial of Service | mitigate | CLOSED | bot/service/bot.py:403-415 `_fetch_build_write` closure runs store fetch + build_daily_html + write_reports entirely inside `loop.run_in_executor(None, _fetch_build_write)` — event loop not blocked |
| T-05-04-05 | Denial of Service | mitigate | CLOSED | deploy/com.bot.trading.plist:61 `<integer>30</integer>` ThrottleInterval matches `service.launchd_throttle_interval_s` default (rules.json:73); deploy/run_forever.sh:38 `THROTTLE_INTERVAL=30` |
| T-05-04-SAFE | Elevation of Privilege | mitigate | CLOSED | deploy/com.bot.trading.plist:74-78 hardcodes `PAPER_TRADING=true` and `FUTU_TRD_ENV=SIMULATE` in EnvironmentVariables; paper guard (assert_paper_account) still runs in _readiness_gate — defence in depth |
| T-05-05-01 | Tampering | mitigate | CLOSED | bot/position/state.py:122 `pending_exit_reason: Optional[str] = None` is a pure annotation field; bot/position/manager.py:209-214 `prev_phase = pos.phase` captured BEFORE `evaluate_close()` mutates the FSM — evaluate_close logic is untouched |
| T-05-05-02 | Denial of Service | mitigate | CLOSED | bot/position/manager.py:524-541 full-close alert wrapped in `try/except Exception -> _logger.warning`; lines 585-598 partial alert wrapped in same pattern — both paths swallow callback exceptions (ALERT-04) |
| T-05-05-03 | Information Disclosure | mitigate | CLOSED | bot/service/alerter.py:170-171 `html.escape(str(exit_reason))` applied before interpolation; manager-emitted reason strings are fixed internal identifiers (stop_out/trail_stop/breakeven/partial/force_close) — no external input reaches them |
| T-05-05-04 | Repudiation | mitigate | CLOSED | bot/position/manager.py:580-598 partial alert fires once at `_trigger_partial_profit`; bot/position/manager.py:524 full-close alert gated on `pos.remaining_quantity == 0` — partial then full close fires two distinct alerts, never a double-fire of the same event |

---

## Accepted Risks

| Threat ID | Rationale |
|-----------|-----------|
| T-05-00-03 | Spoofing via alert callback input: PositionManager callbacks receive only structured position fields (code, qty, price, stop). There is no HTML sink or network call at the PositionManager layer. HTML escaping is applied at the TelegramAlerter boundary (T-05-03-03), which is the correct rendering boundary. Accepted as a deliberate layer-boundary design; escaping at the sink is standard practice. |
| T-05-03-05 | MITM on Telegram transport: urllib.request.urlopen with an https:// URL uses Python's default TLS verification (certifi/system trust store). The destination is the fixed hostname api.telegram.org with no user-supplied URL component, eliminating SSRF risk. Accepted because: (1) TLS is on by default with no override (no ssl=False, no unverified context), (2) the fixed-host constraint eliminates SSRF, (3) the transport layer (api.telegram.org) is operated by Telegram with standard commercial TLS. |

---

## Unregistered Flags

None. All SUMMARY `## Threat Flags` sections across plans 05-00 through 05-05 report no new attack surface beyond the registered threat register.

---

## Notes

- **T-05-SC supply-chain gate:** The plan records a blocking human-gate task (05-00-PLAN.md Task 0) requiring operator confirmation before apscheduler was pinned. The SUMMARY confirms the gate was passed as pre-approved. The auditor verifies the downstream artifact (exact pin `apscheduler==3.11.2` in requirements.txt:15) is present as required.
- **WAL mode (T-05-04-03):** bot/state/store.py:197 confirms `PRAGMA journal_mode=WAL` is executed at `open()` time, enabling concurrent reads during EOD report generation.
- **Partial + full-close alert ordering (T-05-05-04):** The two-alert-per-lifecycle behaviour (one partial alert, one full-close alert) is the INTENDED contract per ALERT-02. The `remaining_quantity == 0` gate in `_on_exit_fill` (line 524) prevents the full-close alert from double-firing on the same partial event.
- **secrets not logged (T-05-01-04, T-05-03-01):** bot/main.py stores telegram_token in a local variable (lines 66-67) but no `_logger.*` call in that file references the variable. The TelegramAlerter warning log (alerter.py:118) logs only `chat_id` (non-secret channel identifier) — the token is never logged anywhere in the Phase 5 codebase.
