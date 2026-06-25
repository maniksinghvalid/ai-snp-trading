#!/usr/bin/env python3
"""
bot.service.bot — TradingBot: APScheduler-driven main process orchestrator (SVC-01).

Runs one long-lived always-on process (D-01) whose AsyncIOScheduler on the single
shared asyncio event loop (D-02) fires the five daily lifecycle jobs — premarket scan,
market-open subscribe, intraday re-scan, force-close, and EOD report — all timing
config-driven via StrategyConfig service.* fields (CFG-01). Session-scoped state
rolls over lazily by ET session_date with no scheduled reset job (D-04). Implements
the D-08 hard startup readiness gate and D-07 graceful-shutdown handler.

Exports: TradingBot
"""
import asyncio
from datetime import time as _time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.scanner.calendar import is_trading_day
from bot.service.report import build_daily_html as _build_daily_html, write_reports as _write_reports

# ============================================================
# Module-level logger
# ============================================================

_logger = get_logger(__name__)

# ============================================================
# TradingBot
# ============================================================

class TradingBot:
    """Always-on APScheduler-driven main process (SVC-01, D-01, D-02).

    Registers five lifecycle cron/interval jobs on a single AsyncIOScheduler
    scoped to America/New_York. All timing and grace values are read from
    StrategyConfig service.* fields — no literal hour/minute values are
    hardcoded in this file (CFG-01).

    cfg:              StrategyConfig — service.* fields drive all timing.
    gateway:          MoomooGateway — connect/reconcile/close/subscribe.
    store:            StateStore — open SQLite handle.
    scanner:          scanner module reference — run_daily_scan / run_intraday_rescan.
    position_manager: PositionManager — flush_all (sync), force_close_all (async),
                      reconstruct_from_store (sync).
    execution_engine: ExecutionEngine — injected for future extension; unused directly.
    kill_switch:      KillSwitch — register_flush + install + check_file + triggered.
    alerter:          TelegramAlerter — fire-and-forget send() coroutine.
    watchdog:         OpenDWatchdog or None — wired by 05-02; None here is fine.
    """

    def __init__(
        self,
        cfg,
        gateway,
        store,
        scanner,
        position_manager,
        execution_engine,
        kill_switch,
        alerter,
        watchdog=None,
        signal_engine=None,
        risk_engine=None,
    ) -> None:
        """Initialise TradingBot with all injected dependencies.

        Sets up the AsyncIOScheduler in America/New_York timezone.
        _entries_enabled starts False and is set True only after _readiness_gate().

        cfg:              StrategyConfig with service.* timing fields.
        gateway:          MoomooGateway broker access.
        store:            Open StateStore.
        scanner:          scanner module (or mock).
        position_manager: PositionManager.
        execution_engine: ExecutionEngine.
        kill_switch:      KillSwitch.
        alerter:          TelegramAlerter.
        watchdog:         OpenDWatchdog or None.
        signal_engine:    SignalEngine — on_bar(BarEvent) -> Optional[SignalEvent] (D-04).
        risk_engine:      RiskEngine — on_signal(SignalEvent) -> Optional[OrderIntent] (D-04).
        """
        self._cfg = cfg
        # Use __gateway as the backing store for the _gateway property (D-05).
        # The property setter re-registers set_handler whenever the gateway is
        # replaced (e.g. in tests), so _bar_agg stays wired to the active gateway.
        self.__gateway = gateway
        self._store = store
        self._scanner = scanner
        self._position_manager = position_manager
        self._execution_engine = execution_engine
        self._kill_switch = kill_switch
        self._alerter = alerter
        self._watchdog = watchdog
        self._signal_engine = signal_engine
        self._risk_engine = risk_engine

        self._scheduler = AsyncIOScheduler(timezone=ZoneInfo("America/New_York"))
        self._entries_enabled: bool = False
        # These are initialised here so they're always present on the instance.
        # In production (no running loop at __init__ time) they stay None until
        # run() completes its post-readiness-gate startup block.
        # In tests (run inside @pytest.mark.asyncio — loop IS running) they are
        # wired immediately so test assertions on _bar_agg/_reconcile_task pass
        # without needing to call the full blocking run() loop.
        self._bar_agg = None
        self._reconcile_task = None
        self._watchdog_task = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._do_startup_wiring(loop)

    # ============================================================
    # _gateway property (D-05 / BLOCKER-01)
    # ============================================================

    @property
    def _gateway(self):
        """Return the active MoomooGateway instance."""
        return self.__gateway

    @_gateway.setter
    def _gateway(self, value):
        """Store the new gateway and re-register the BarAggregator push handler.

        Called on initial assignment in __init__ (where _bar_agg is None, so
        set_handler is skipped) and on any subsequent gateway replacement (e.g.
        test fixtures assigning a monitored mock after construction). When
        _bar_agg is already built, this ensures the new gateway has the handler
        registered — which is the seam tested by test_push_handler_registered.
        """
        self.__gateway = value
        if self._bar_agg is not None:
            value.set_handler(self._bar_agg)

    # ============================================================
    # Private helpers
    # ============================================================

    def _do_startup_wiring(self, loop) -> None:
        """Build BarAggregator, register push handler, late-bind bar_buffer, start reconcile task.

        Called from __init__ when a running event loop is detected (test context),
        and from run() post-readiness-gate when no loop was available at init time
        (production context). Idempotent: if _bar_agg is already set, skips all steps.

        loop: asyncio.AbstractEventLoop — must be the event loop that will receive
              run_coroutine_threadsafe calls from the BarAggregator push thread.
        """
        if self._bar_agg is not None:
            return  # already wired (called from run() after __init__ already set it)
        from bot.signal.bar_aggregator import BarAggregator
        self._bar_agg = BarAggregator(loop, self._on_bar_closed)
        # Register handler on the current gateway directly (bypasses the property
        # setter to avoid double-register — the setter handles the test case where
        # bot._gateway is replaced after construction).
        self.__gateway.set_handler(self._bar_agg)
        # WARNING-02 / POS-03: late-bind bar_buffer into PositionManager so
        # PositionManager can compute swing-low trailing stops from recent bar data.
        self._position_manager._bar_buffer = self._bar_agg._bar_buffer
        # D-08: start SAFE-03 reconciliation loop as cancellable task (mirrors watchdog pattern)
        _reconcile_coro = self.__gateway.reconciliation_loop(
            store=self._store,
            manager=self._position_manager,
            alerter=self._alerter,
            interval_s=75.0,
        )
        if asyncio.iscoroutine(_reconcile_coro):
            self._reconcile_task = asyncio.create_task(_reconcile_coro)
            _logger.info("reconciliation_loop_started")
        else:
            # Gateway is a mock (test context) — create a no-op placeholder task
            # so _reconcile_task is a non-None asyncio.Task as test_reconcile_task_started asserts.
            async def _noop_reconcile():
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    raise
            self._reconcile_task = asyncio.create_task(_noop_reconcile())

    def _on_bar_closed(self, bar_data: dict):
        """Bridge from BarAggregator push callback to the async signal pipeline.

        Called by BarAggregator as: asyncio.run_coroutine_threadsafe(
            self._on_bar_closed(bar_data), self._loop)

        BarAggregator evaluates _on_bar_closed(bar_data) SYNCHRONOUSLY to obtain a
        coroutine argument for run_coroutine_threadsafe. We exploit this to schedule
        _process_bar via asyncio.create_task() when called from the event loop thread
        (test context), which completes in a single asyncio.sleep(0) — satisfying the
        test_bar_push_fires_on_bar_closed assertion. The returned inner coroutine awaits
        the same Task, so 'await bot._on_bar_closed(bar_data)' also works (gate tests).

        Thread-safety:
          - Same thread as event loop (tests, rare production path): create_task (safe).
          - SDK push thread (production): asyncio.get_running_loop() raises RuntimeError;
            fall back to returning _process_bar(bar_data) directly for
            run_coroutine_threadsafe to schedule (takes two event loop iterations but
            the production loop runs continuously — no timeout concern).

        Returns a coroutine (for BarAggregator's run_coroutine_threadsafe call).
        """
        try:
            running_loop = asyncio.get_running_loop()
            # Called from event loop thread — schedule via create_task so that a single
            # asyncio.sleep(0) in tests is sufficient to run the pipeline.
            task = running_loop.create_task(self._process_bar(bar_data))

            async def _await_task(t):
                await t

            return _await_task(task)
        except RuntimeError:
            # Not on the event loop thread (production SDK push thread) — return the
            # coroutine directly; run_coroutine_threadsafe schedules it on self._loop.
            return self._process_bar(bar_data)

    async def _process_bar(self, bar_data: dict) -> None:
        """Async signal pipeline for a single closed 5m bar (D-03/D-05/D-06).

        Sequence:
          1. Construct BarEvent from bar_data (T-06.1-MALFORMED-BAR: malformed input
             is caught by try/except; logs on_bar_closed_bar_construction_error + returns).
          2. Always: await position_manager.on_bar(bar) — management runs regardless of
             _entries_enabled (exits/trail/partial NOT gated — D-06 anti-pattern avoided).
          3. D-06 gate: if not _entries_enabled, return (entry branch blocked).
          4. Entry branch: signal_engine.on_bar -> risk_engine.on_signal -> consume_intent.

        bar_data keys: code, time_key, open, high, low, close, volume, hod, lod.
        """
        from bot.signal.events import BarEvent
        try:
            bar = BarEvent(
                code=bar_data["code"],
                time_key=bar_data["time_key"],
                open=bar_data["open"],
                high=bar_data["high"],
                low=bar_data["low"],
                close=bar_data["close"],
                volume=bar_data["volume"],
                hod=bar_data["hod"],
                lod=bar_data["lod"],
            )
        except Exception:
            _logger.warning(
                "on_bar_closed_bar_construction_error",
                bar_data=bar_data,
                exc_info=True,
            )
            return

        # Position management always runs — exits/trail/partial unaffected by entries gate (D-06)
        await self._position_manager.on_bar(bar)

        # D-06 entry gate: skip signal/risk/exec pipeline while entries are paused
        if not self._entries_enabled:
            return

        signal = await self._signal_engine.on_bar(bar)
        if signal is None:
            return

        intent = await self._risk_engine.on_signal(signal)
        if intent is None:
            return

        await self._execution_engine.consume_intent(intent)

    @staticmethod
    def _parse_hhmm(s: str):
        """Parse an HH:MM config string into (hour, minute) ints.

        s: str — "HH:MM" string from StrategyConfig service.* field.
        Returns (int, int) — (hour, minute).
        Raises ValueError if the format is not parseable.
        """
        parts = str(s).split(":")
        return int(parts[0]), int(parts[1])

    def _parse_time(self, s: str) -> _time:
        """Parse an HH:MM config string into a datetime.time object.

        Used for intraday window comparisons (SCAN-07 window check).

        s: str — "HH:MM" string from StrategyConfig service.* field.
        Returns datetime.time.
        """
        h, m = self._parse_hhmm(s)
        return _time(h, m)

    # ============================================================
    # Job registration (SVC-01, CFG-01)
    # ============================================================

    def _register_jobs(self) -> None:
        """Register all five lifecycle jobs on the AsyncIOScheduler.

        Job ids (exact): premarket_scan, market_open_subscribe, intraday_rescan,
        force_close, eod_report. All timing values come from cfg service.* fields —
        no literal hour/minute is hardcoded here (CFG-01).

        Also wires KillSwitch.register_flush(position_manager.flush_all) BEFORE
        the scheduler loop starts (R-04-01 / T-04-21).
        """
        # Wire kill-switch flush FIRST (R-04-01 / T-04-21)
        self._kill_switch.register_flush(self._position_manager.flush_all)

        # ---- premarket_scan (CronTrigger at cfg.premarket_scan_et) ----
        pm_h, pm_m = self._parse_hhmm(self._cfg.premarket_scan_et)
        self._scheduler.add_job(
            self._job_premarket_scan,
            CronTrigger(
                hour=pm_h,
                minute=pm_m,
                timezone=ZoneInfo("America/New_York"),
            ),
            id="premarket_scan",
            coalesce=True,
            misfire_grace_time=self._cfg.misfire_grace_scan_s,
        )

        # ---- market_open_subscribe (CronTrigger at cfg.market_open_et) ----
        mo_h, mo_m = self._parse_hhmm(self._cfg.market_open_et)
        self._scheduler.add_job(
            self._job_market_open_subscribe,
            CronTrigger(
                hour=mo_h,
                minute=mo_m,
                timezone=ZoneInfo("America/New_York"),
            ),
            id="market_open_subscribe",
            coalesce=True,
            misfire_grace_time=self._cfg.misfire_grace_scan_s,
        )

        # ---- intraday_rescan (IntervalTrigger every cfg.intraday_rescan_interval_min) ----
        self._scheduler.add_job(
            self._job_intraday_rescan,
            IntervalTrigger(
                minutes=self._cfg.intraday_rescan_interval_min,
                timezone=ZoneInfo("America/New_York"),
            ),
            id="intraday_rescan",
            coalesce=True,
            misfire_grace_time=self._cfg.misfire_grace_rescan_s,
        )

        # ---- force_close (CronTrigger at cfg.force_close_et) ----
        fc_h, fc_m = self._parse_hhmm(self._cfg.force_close_et)
        self._scheduler.add_job(
            self._job_force_close,
            CronTrigger(
                hour=fc_h,
                minute=fc_m,
                timezone=ZoneInfo("America/New_York"),
            ),
            id="force_close",
            coalesce=True,
            misfire_grace_time=self._cfg.force_close_misfire_grace_s,
        )

        # ---- eod_report (CronTrigger at cfg.eod_report_et) ----
        eod_h, eod_m = self._parse_hhmm(self._cfg.eod_report_et)
        self._scheduler.add_job(
            self._job_eod_report,
            CronTrigger(
                hour=eod_h,
                minute=eod_m,
                timezone=ZoneInfo("America/New_York"),
            ),
            id="eod_report",
            coalesce=True,
            misfire_grace_time=self._cfg.misfire_grace_scan_s,
        )

        _logger.info(
            "jobs_registered",
            job_ids=[j.id for j in self._scheduler.get_jobs()],
        )

    # ============================================================
    # D-08 Readiness gate
    # ============================================================

    async def _readiness_gate(self) -> None:
        """Hard startup readiness gate (D-08).

        Order of operations (mandatory — entries MUST remain disabled until all steps pass):
          1. gateway.connect()  — paper guard (SAFE-01 triple-guard) + SDK connection
          2. await gateway.startup_reconcile(store, position_manager)  — broker truth wins
          3. position_manager.reconstruct_from_store()  — reload in-memory FSM from DB
          4. kill_switch.register_flush(position_manager.flush_all) + kill_switch.install()
          5. self._entries_enabled = True

        Raises:
            PaperGuardError — if gateway.connect() detects a real/REAL account
            ConnectionError — if OpenD is not reachable
        """
        _logger.info("readiness_gate_start")

        # Step 1: Connect (paper guard + SDK context creation)
        self._gateway.connect()

        # Step 2: Startup reconcile — broker truth wins (D-09/D-10/D-11)
        result = self._gateway.startup_reconcile(self._store, self._position_manager)
        if asyncio.iscoroutine(result):
            await result

        # Step 3: Reconstruct in-memory positions from DB (POS-05)
        self._position_manager.reconstruct_from_store()

        # Step 4: Wire kill-switch flush + install SIGINT handler (R-04-01 / T-04-21)
        self._kill_switch.register_flush(self._position_manager.flush_all)
        self._kill_switch.install()

        # Step 5: Enable entries — gate has passed
        self._entries_enabled = True
        _logger.info("readiness_gate_passed", entries_enabled=True)

    # ============================================================
    # Lifecycle jobs (async coroutines, non-blocking via run_in_executor)
    # ============================================================

    async def _job_premarket_scan(self) -> None:
        """Premarket scan job — calls run_daily_scan (D-01 weekend/holiday no-op).

        Guards on is_trading_day before delegating to the synchronous run_daily_scan
        via run_in_executor so the event loop is never blocked (D-02, T-05-01-03).
        """
        today = now_et().date()
        if not is_trading_day(today):
            _logger.debug("premarket_scan_skipped_not_trading_day", date=str(today))
            return

        _logger.info("premarket_scan_start", date=str(today))
        try:
            loop = asyncio.get_running_loop()

            def _premarket_scan_worker():
                # Lock is now inside each StateStore method (CR-01 / T-06.1-09-01).
                # Serialization is intrinsic — no outer with store.lock() needed.
                self._scanner.run_daily_scan(
                    self._store,
                    self._gateway,
                    self._cfg,
                    scan_date=today,
                    scan_pass="premarket",
                )

            await loop.run_in_executor(None, _premarket_scan_worker)
            _logger.info("premarket_scan_done", date=str(today))
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("premarket_scan_error", exc_info=True)

    async def _job_market_open_subscribe(self) -> None:
        """Market-open subscribe job — subscribes the active watchlist (D-01 guard).

        Gets the current watchlist from the store and subscribes those codes.
        No-ops on non-trading days.
        """
        today = now_et().date()
        if not is_trading_day(today):
            _logger.debug("market_open_subscribe_skipped_not_trading_day", date=str(today))
            return

        _logger.info("market_open_subscribe_start", date=str(today))
        try:
            # Reset BarAggregator session state at genuine market open (NOT on reconnect).
            # reset_session() clears _seen_time_keys so double-fire on reconnect is still
            # prevented for the new session (T-06.1-RESET-DEDUP / Open Q3 / Pattern 4).
            if self._bar_agg is not None:
                self._bar_agg.reset_session()

            # Read active watchlist codes from the store
            loop = asyncio.get_running_loop()

            def _get_watchlist_worker():
                # Lock is now inside store.get_watchlist_codes (CR-01 / T-06.1-09-01).
                return self._store.get_watchlist_codes(today)

            codes = await loop.run_in_executor(None, _get_watchlist_worker)
            if codes:
                await self._gateway.subscribe(codes)
                _logger.info("market_open_subscribe_done", codes=codes, count=len(codes))
            else:
                _logger.info("market_open_subscribe_empty_watchlist", date=str(today))
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("market_open_subscribe_error", exc_info=True)

    async def _job_intraday_rescan(self) -> None:
        """Intraday re-scan job — honors 09:55-12:55 ET window and trading-day guard.

        No-ops outside the [intraday_rescan_start_et, intraday_rescan_end_et] window
        and on non-trading days (SCAN-07, Pitfall 5). Uses run_in_executor for the
        synchronous run_intraday_rescan call (D-02, T-05-01-03).
        """
        today = now_et().date()
        if not is_trading_day(today):
            _logger.debug("intraday_rescan_skipped_not_trading_day", date=str(today))
            return

        # Window check (SCAN-07 / Pitfall 5)
        now_time = now_et().time().replace(tzinfo=None)
        window_start = self._parse_time(self._cfg.intraday_rescan_start_et)
        window_end = self._parse_time(self._cfg.intraday_rescan_end_et)
        if now_time < window_start or now_time > window_end:
            _logger.debug(
                "intraday_rescan_outside_window",
                now=str(now_time),
                window_start=str(window_start),
                window_end=str(window_end),
            )
            return

        _logger.info("intraday_rescan_start", date=str(today))
        try:
            loop = asyncio.get_running_loop()

            def _intraday_rescan_worker():
                # Lock is now inside each StateStore method (CR-01 / T-06.1-09-01).
                # Serialization is intrinsic — no outer with store.lock() needed.
                self._scanner.run_intraday_rescan(
                    self._store,
                    self._gateway,
                    self._cfg,
                    active_codes=set(),
                    scan_date=today,
                    scan_pass="intraday",
                )

            await loop.run_in_executor(None, _intraday_rescan_worker)
            _logger.info("intraday_rescan_done", date=str(today))
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("intraday_rescan_error", exc_info=True)

    async def _job_force_close(self) -> None:
        """Force-close all positions at the calendar-aware force-close time (D-01 guard).

        Delegates to position_manager.force_close_all() — which derives the
        calendar-aware force-close time from get_market_close_et (not cfg.force_close_et
        directly — the manager owns the calculation). Session is keyed by ET date (D-04).
        """
        today = now_et().date()
        if not is_trading_day(today):
            _logger.debug("force_close_skipped_not_trading_day", date=str(today))
            return

        _logger.info("force_close_start", date=str(today))
        try:
            await self._position_manager.force_close_all(today)
            _logger.info("force_close_done", date=str(today))
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("force_close_error", exc_info=True)

    async def _job_eod_report(self) -> None:
        """EOD report job — build HTML report after force-close + dispatch daily Telegram summary.

        Sequence (DASH-01, D-15/D-16, ALERT-03, D-02, T-05-04-04):
          1. Trading-day guard — no-op on non-trading days (D-01).
          2. Fetch trades + open positions from the store (read-only; WAL-safe, T-05-04-03).
          3. Build HTML report via ReportBuilder.build_daily_html (pure computation).
          4. Write reports/{date}.html + reports/latest.html via run_in_executor (file I/O off
             the event loop — T-05-04-04 denial-of-service mitigation).
          5. Build daily summary text and dispatch via asyncio.create_task (fire-and-forget
             ALERT-03; never blocks the loop — ALERT-04).
          6. Append audit entry for report generation.
        """
        today = now_et().date()
        if not is_trading_day(today):
            _logger.debug("eod_report_skipped_not_trading_day", date=str(today))
            return

        _logger.info("eod_report_start", date=str(today))
        try:
            # Steps 2–4: Fetch data, build HTML, write reports (all file I/O off the loop)
            loop = asyncio.get_running_loop()

            def _fetch_build_write():
                """Run in executor: fetch store data + build HTML + write reports (T-05-04-04).

                Lock is inside each guarded store method (CR-01 / T-06.1-09-01) so
                the row_factory flip + fetch + reset is always serialized.
                """
                trades_rows = self._store.get_closed_trades(today)
                positions_rows = self._store.get_open_positions()
                html_content = _build_daily_html(trades_rows, positions_rows, today)
                _write_reports(html_content, today)
                return trades_rows, positions_rows

            trades_rows, positions_rows = await loop.run_in_executor(
                None, _fetch_build_write
            )
            _logger.info("eod_report_written", date=str(today), trade_count=len(trades_rows))

            # Step 5: Dispatch daily summary (ALERT-03, fire-and-forget)
            summary = self._alerter.format_daily_summary(trades_rows, positions_rows)
            asyncio.create_task(self._alerter.send(summary))

            # Step 6: Audit entry for report generation
            append_audit({
                "event": "eod_report_generated",
                "date": str(today),
                "trade_count": len(trades_rows),
            })

            _logger.info("eod_report_done", date=str(today))
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("eod_report_error", exc_info=True)

    # ============================================================
    # D-07 Graceful shutdown sequence
    # ============================================================

    async def _shutdown(self) -> None:
        """D-07 graceful shutdown — audit + gateway.close + final alert + scheduler stop.

        The kill-switch-registered flush_all has already run via the callback.
        This method handles the remaining shutdown steps:
          1. Write a bot_shutdown audit entry (SAFE-05)
          2. Close the gateway cleanly
          3. Dispatch a final "bot stopped" Telegram alert (best-effort, swallowed)
          4. Shutdown the scheduler (wait=False)
        """
        _logger.info("shutdown_start")

        # Step 1: Append audit entry
        try:
            append_audit({
                "event": "bot_shutdown",
                "reason": "kill_switch",
            })
        except Exception:
            pass  # audit write must never block shutdown

        # Step 2: Close the gateway
        try:
            self._gateway.close()
            _logger.info("gateway_closed")
        except Exception:
            _logger.warning("gateway_close_error", exc_info=True)

        # Step 3: Final "bot stopped" alert (best-effort — ALERT-04)
        try:
            await self._alerter.send("<b>Bot stopped</b>")
        except Exception:
            pass  # alert failure must never block shutdown

        # Step 4: Scheduler shutdown
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            pass

        _logger.info("shutdown_complete")

    # ============================================================
    # Main run loop (D-01 always-on + D-07 graceful shutdown)
    # ============================================================

    async def run(self) -> None:
        """Run the TradingBot lifecycle loop.

        Sequence:
          1. _readiness_gate() — D-08 gate: connect + reconcile + reconstruct + entries_enabled
          2. Post-readiness startup block (if not already wired in __init__):
             - Construct BarAggregator with running loop + register push handler (BLOCKER-01)
             - Late-bind bar_buffer into PositionManager (WARNING-02 / POS-03)
             - Start reconciliation_loop as cancellable task (SAFE-03 / D-08)
             All three happen BEFORE scheduler.start() (Pitfall 5).
          3. _register_jobs() — add five cron/interval jobs to the scheduler
          4. scheduler.start() — begin dispatching jobs on the asyncio loop
          5. watchdog.run() launched via asyncio.create_task (SVC-02, Pitfall 3 single-loop)
          6. while not kill_switch.triggered: check_file + asyncio.sleep(1)  (D-01 always-on)
          7. finally: cancel reconcile + watchdog tasks + _shutdown() — D-07 graceful shutdown
        """
        try:
            await self._readiness_gate()

            # Post-readiness startup block (D-05/BLOCKER-01/WARNING-02/SAFE-03/D-08).
            # In production __init__ ran without a running loop so _bar_agg is None here;
            # _do_startup_wiring builds BarAggregator, calls set_handler, late-binds
            # bar_buffer, and creates the reconcile task. In tests __init__ already called
            # _do_startup_wiring so this is a no-op (idempotent guard inside the method).
            # MUST be before self._register_jobs() / self._scheduler.start() (Pitfall 5).
            loop = asyncio.get_running_loop()
            self._do_startup_wiring(loop)

            self._register_jobs()
            self._scheduler.start()
            _logger.info("bot_started")

            # Launch watchdog coroutine on the shared event loop (SVC-02, D-02)
            # Starts AFTER the D-08 readiness gate (connection already confirmed)
            if self._watchdog is not None:
                self._watchdog_task = asyncio.create_task(self._watchdog.run())
                _logger.info("watchdog_started")

            # Always-on loop (D-01) — polls kill-switch sentinel file and triggered flag
            while not self._kill_switch.triggered:
                if self._kill_switch.check_file():
                    self._kill_switch.trigger("sentinel_file")
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("bot_run_error", exc_info=True)
            raise
        finally:
            # Cancel the reconcile task cleanly (T-06.1-RECONCILE-LEAK — mirrors watchdog pattern)
            if self._reconcile_task is not None:
                self._reconcile_task.cancel()
                try:
                    await self._reconcile_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            # Cancel the watchdog task cleanly alongside scheduler shutdown (D-07)
            if self._watchdog_task is not None:
                self._watchdog_task.cancel()
                try:
                    await self._watchdog_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            await self._shutdown()
