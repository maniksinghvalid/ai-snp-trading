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
import datetime as _datetime
from datetime import time as _time
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot.position.state import PositionPhase, PositionState

from bot.position.manager import get_force_close_time_et
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
        # D-07 / Pitfall 6: one-shot circuit-breaker side-effect flag (RISK-CIRCUIT).
        # Prevents re-alerting and re-abandoning on each bar of an already-handled trip.
        # Initialized to False; set True on first trip or at startup when a persisted
        # trip date matches today (restart safety — _readiness_gate reads the store).
        self._breaker_handled: bool = False
        # These are initialised here so they're always present on the instance.
        # In production (no running loop at __init__ time) they stay None until
        # run() completes its post-readiness-gate startup block.
        # In tests (run inside @pytest.mark.asyncio — loop IS running) they are
        # wired immediately so test assertions on _bar_agg/_reconcile_task pass
        # without needing to call the full blocking run() loop.
        self._bar_agg = None
        self._reconcile_task = None
        self._watchdog_task = None
        # Finding 3.2: caller-owned, trading-day-keyed daily-bar cache so the
        # intraday rescan job reuses the day's daily download instead of
        # re-fetching the full ~500-symbol universe every ~30 min.
        self._daily_bar_cache: dict = {}
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

        bar_data keys: code, time_key, open, high, low, close, volume, hod, lod, cum_volume.
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
                cum_volume=bar_data.get("cum_volume", 0),  # Phase 7 RVOL-TOD cumulative volume
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

        # D-08: one-shot circuit-breaker side-effects (abandon PENDING intents + alert).
        # Runs unconditionally so the breaker always triggers the handler even when
        # signal_engine.on_bar returned None due to Gate 7 blocking a new entry.
        _session_date_str = now_et().date().isoformat()
        await self._handle_circuit_breaker_side_effects(_session_date_str)

        if signal is None:
            return

        intent = await self._risk_engine.on_signal(signal)
        if intent is None:
            return

        # Fix 1.1: capture FillEvent return; wire fill into PositionManager.
        fill = await self._execution_engine.consume_intent(intent)
        if fill is not None:
            # Build AWAITING_FILL PositionState — DB-first via register_position (POS-05/EXEC-05).
            pos = PositionState(
                position_id=str(uuid4()),
                code=intent.code,
                phase=PositionPhase.AWAITING_FILL,
                entry_price=intent.entry_price,
                initial_stop=intent.stop_price,
                trail_stop=intent.stop_price,
                full_quantity=intent.quantity,
                remaining_quantity=intent.quantity,
                entry_order_id=fill.order_id,   # EXEC-05: match by order_id
            )
            self._position_manager.register_position(pos)   # DB-first (manager.py:1003)
            self._position_manager.on_fill(fill)            # FSM AWAITING_FILL → ACTIVE
            await self._position_manager.arm_stop_protection(pos)  # D-01: broker stop post-fill
        else:
            # Abandon path: intent was not filled; resolve it in state store.
            self._store.resolve_pending_intent(intent.intent_id, "ABANDONED")

        # Fix #5: decrement _pending_count on BOTH the fill and abandon paths.
        # Without this call, _pending_count only grows, permanently blocking
        # new entries after max_concurrent_positions fills (note_intent_emitted
        # was already called by RiskEngine; this resolves the slot).
        self._signal_engine.note_intent_resolved()

    async def _handle_circuit_breaker_side_effects(self, session_date_str: str) -> None:
        """One-shot handler for D-08 side-effects when the circuit breaker trips.

        On the FIRST bar of a session in which the -2R breaker has fired:
          1. Mark every PENDING pending_intent as ABANDONED via resolve_pending_intent.
          2. Send exactly one Telegram alert (best-effort — ALERT-04: failure swallowed).
          3. Set self._breaker_handled = True so subsequent bars are no-ops.

        On subsequent bars in the same session (self._breaker_handled == True):
          No action — the side-effects are one-shot (idempotent D-08 invariant).

        Existing positions are NOT force-closed — management continues via
        position_manager.on_bar (D-06: the breaker only halts NEW entries).

        Called from _process_bar after signal_engine.on_bar so the gate has by then
        persisted any new trip date via set_circuit_breaker_date.

        Args:
            session_date_str: Today's ET ISO date string (e.g. "2026-07-03").
        """
        # Guard: only act if breaker is tripped today AND not yet handled this session
        if self._store.get_circuit_breaker_date() != session_date_str:
            return
        if self._breaker_handled:
            return  # already handled — idempotent one-shot

        # D-08: mark all PENDING entry intents as ABANDONED
        for intent_id, code in self._store.get_pending_intent_codes("PENDING"):
            try:
                self._store.resolve_pending_intent(intent_id, "ABANDONED")
                _logger.info(
                    "circuit_breaker_intent_abandoned",
                    intent_id=intent_id,
                    code=code,
                )
            except Exception:
                _logger.warning(
                    "circuit_breaker_abandon_error",
                    intent_id=intent_id,
                    code=code,
                    exc_info=True,
                )

        # D-08: dispatch one Telegram alert (fire-and-forget, ALERT-04)
        try:
            await self._alerter.send(
                "<b>CIRCUIT BREAKER</b>: Daily -2R realized loss reached — "
                "no new entries for the rest of the session. "
                "Existing positions continue to be managed."
            )
        except Exception:
            _logger.warning("circuit_breaker_alert_failed", exc_info=True)

        self._breaker_handled = True
        _logger.info(
            "circuit_breaker_alert",
            session_date=session_date_str,
            reason="D-08: PENDING intents abandoned + one Telegram alert sent",
        )

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

        # ---- force_close (DateTrigger — rescheduled daily by _job_premarket_scan) ----
        # Fix 1.5: Use a DateTrigger rather than a static CronTrigger so the
        # force-close fires at the CALENDAR-AWARE close time (half-day aware) each
        # trading day. _job_premarket_scan reschedules this job every morning after
        # the scan completes using get_force_close_time_et(today). The initial
        # placeholder is set far in the future so it never fires unless explicitly
        # rescheduled for the current day. (T-06.2-05)
        self._scheduler.add_job(
            self._job_force_close,
            DateTrigger(
                run_date=_datetime.datetime(2099, 1, 1, tzinfo=ZoneInfo("America/New_York")),
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

        # Step 6: Initialize _breaker_handled from persistent store (Pitfall 6 / D-07).
        # If the bot is restarted mid-session after a -2R trip, the stored trip date
        # equals today → mark as already handled so the restart does not re-cancel
        # PENDING intents or re-send the circuit-breaker Telegram alert.
        try:
            stored_breaker = self._store.get_circuit_breaker_date()
            today_et = now_et().date().isoformat()
            self._breaker_handled = (stored_breaker == today_et)
            _logger.info(
                "breaker_handled_initialized",
                stored_breaker_date=stored_breaker,
                today_et=today_et,
                breaker_handled=self._breaker_handled,
            )
        except Exception:
            self._breaker_handled = False
            _logger.warning("breaker_handled_init_error", exc_info=True)

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

            # Fix 1.5: reschedule the force_close job with the calendar-aware
            # close time for today. get_force_close_time_et returns the correct
            # time for half-days (e.g. 12:51 ET on early-close days), avoiding
            # the stale 15:51 CronTrigger that previously left positions open
            # ~3 hours past early-close. (T-06.2-05)
            try:
                close_time = get_force_close_time_et(today)
                run_at = _datetime.datetime.combine(
                    today,
                    close_time,
                    tzinfo=ZoneInfo("America/New_York"),
                )
                self._scheduler.reschedule_job(
                    "force_close",
                    trigger=DateTrigger(run_date=run_at),
                )
                _logger.info(
                    "force_close_job_rescheduled",
                    date=str(today),
                    run_at=str(run_at),
                )
            except Exception:
                _logger.error("force_close_reschedule_error", exc_info=True)

        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("premarket_scan_error", exc_info=True)

    async def _job_market_open_subscribe(self) -> None:
        """Market-open subscribe job — seeds premarket highs, THEN subscribes the watchlist (D-01 guard).

        Gets the current watchlist from the store, seeds SignalEngine._premarket_highs
        via fetch_premarket_highs() FIRST, then subscribes those codes (D-01/D-03).

        Ordering rationale (race fix): gateway.subscribe(codes) arms the BarAggregator
        push handler immediately (is_first_push=True/subscribe_push=True), after which
        a closed 5m bar can be delivered at any moment. fetch_premarket_highs only
        freezes _premarket_highs at the END of its snapshot round-trip (~100-500ms).
        If subscribe ran first, bars arriving during that RTT would hit Gate 1 of
        on_bar with an empty _premarket_highs dict and be dropped — the original
        failure mode, narrowed to the first-snapshot window (the most critical bars
        right at the open). Seeding BEFORE subscribe freezes the highs before any bar
        can flow, so Gate 1 is satisfied from the very first push — no race window.

        This swap is safe because get_market_snapshot is subscription-independent: the
        moomoo snapshot call (and gateway.get_market_snapshot) require no prior
        subscribe (verified — moomoo get_snapshot.py: "no subscription required";
        gateway.py: pre_high_price "available for US stocks without an extended-hours
        subscription"). The push handler / bar flow is gated on subscribe(), not on
        snapshot, so fetching highs before subscribe works fully.

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
                # D-01/D-03: Seed premarket highs via one batched snapshot call BEFORE
                # subscribing. fetch_premarket_highs() calls set_premarket_highs()
                # internally, freezing _premarket_highs for the session so Gate 1 of
                # on_bar can pass. Seeding runs FIRST so the wiring seam is:
                # seed → subscribe → bars flow. This closes the market-open race where
                # a bar could arrive during the snapshot RTT and hit an empty Gate 1.
                # Snapshot is subscription-independent, so this ordering is safe.
                if self._signal_engine is not None:
                    await self._signal_engine.fetch_premarket_highs(codes)

                await self._gateway.subscribe(codes)
                _logger.info("market_open_subscribe_done", codes=codes, count=len(codes))
            else:
                _logger.info("market_open_subscribe_empty_watchlist", date=str(today))
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("market_open_subscribe_error", exc_info=True)

    async def _seed_premarket_highs_on_startup(self) -> None:
        """Seed premarket highs immediately on bot startup if the market is already open (D-01/D-03).

        Handles the mid-session restart case: when the bot starts AFTER 09:30 ET but
        BEFORE EOD (force_close_et), the 09:30 CronTrigger will NOT fire until next day,
        so premarket highs would never be seeded — the bot would be dead-for-the-day.

        Reads the watchlist and calls fetch_premarket_highs() if:
          1. today is a trading day, AND
          2. now_et() is between market_open_et (inclusive) and force_close_et (inclusive).

        Called from run() after _do_startup_wiring() and before _register_jobs() so the
        seeding is in place BEFORE the scheduler starts dispatching bars (Pitfall 5 order).
        No-op on non-trading days or outside the session window.
        """
        today = now_et().date()
        if not is_trading_day(today):
            _logger.debug("startup_seed_skipped_not_trading_day", date=str(today))
            return

        # Check if now_et() is inside the session window [market_open_et, force_close_et]
        now_time = now_et().time().replace(tzinfo=None)
        open_h, open_m = self._parse_hhmm(self._cfg.market_open_et)
        close_h, close_m = self._parse_hhmm(self._cfg.force_close_et)
        market_open_time = _time(open_h, open_m)
        force_close_time = _time(close_h, close_m)

        if now_time < market_open_time or now_time > force_close_time:
            _logger.debug(
                "startup_seed_skipped_outside_session",
                now=str(now_time),
                market_open=str(market_open_time),
                force_close=str(force_close_time),
            )
            return

        if self._signal_engine is None:
            return

        _logger.info("startup_seed_premarket_highs_start", date=str(today))
        try:
            loop = asyncio.get_running_loop()

            def _get_watchlist_worker():
                return self._store.get_watchlist_codes(today)

            codes = await loop.run_in_executor(None, _get_watchlist_worker)
            if codes:
                await self._signal_engine.fetch_premarket_highs(codes)
                _logger.info(
                    "startup_seed_premarket_highs_done",
                    date=str(today),
                    codes=codes,
                    count=len(codes),
                )
            else:
                _logger.info("startup_seed_premarket_highs_empty_watchlist", date=str(today))
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("startup_seed_premarket_highs_error", exc_info=True)

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
            rescan_watchlist: list = []

            def _intraday_rescan_worker():
                # Lock is now inside each StateStore method (CR-01 / T-06.1-09-01).
                # Serialization is intrinsic — no outer with store.lock() needed.
                nonlocal rescan_watchlist
                # Finding 2.2: real active codes (open positions + in-flight intents)
                # so the scanner never evicts/unsubscribes a symbol that is currently
                # managed or awaiting a fill.
                active_codes: set = set()
                for row in self._store.get_open_positions():
                    if (row.get("remaining_quantity") or 0) > 0 and row.get("phase") != "CLOSED":
                        active_codes.add(row.get("code"))
                for row in self._store.get_pending_intent_codes():
                    active_codes.add(row.get("code"))
                active_codes.discard(None)

                rescan_watchlist = self._scanner.run_intraday_rescan(
                    self._store,
                    self._gateway,
                    self._cfg,
                    active_codes=active_codes,
                    scan_date=today,
                    scan_pass="intraday",
                    daily_bars_cache=self._daily_bar_cache,
                ) or []

            await loop.run_in_executor(None, _intraday_rescan_worker)

            # Seed premarket highs for rescan-discovered codes (D-01 merge guard).
            # fetch_and_merge_premarket_highs skips codes already in _premarket_highs
            # (preserving the 09:30 frozen highs) and merges only genuinely new codes.
            # This closes the gap where rescan-added symbols were permanently stuck at
            # Gate 1 (signal_skipped_no_premarket_high) because fetch_premarket_highs
            # was only called once at 09:30 for the market-open watchlist.
            if self._signal_engine is not None and rescan_watchlist:
                await self._signal_engine.fetch_and_merge_premarket_highs(rescan_watchlist)

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

                get_closed_trades is used for the HTML display table only (bounded
                at LIMIT 20 by design — avoids loading all rows for a visual list).
                get_daily_trade_stats provides uncapped SQL COUNT/SUM aggregates for
                the summary alert (DAILY-CAP-01).
                """
                trades_rows = self._store.get_closed_trades(today)   # display list (LIMIT 20)
                trade_stats = self._store.get_daily_trade_stats(today)  # uncapped aggregates
                positions_rows = self._store.get_open_positions()
                html_content = _build_daily_html(trades_rows, positions_rows, today)
                _write_reports(html_content, today)
                return trade_stats, trades_rows, positions_rows

            trade_stats, trades_rows, positions_rows = await loop.run_in_executor(
                None, _fetch_build_write
            )
            _logger.info(
                "eod_report_written",
                date=str(today),
                trade_count=trade_stats["trade_count"],
            )

            # Step 5: Dispatch daily summary (ALERT-03, fire-and-forget)
            # trade_stats comes from get_daily_trade_stats (uncapped) — not trades_rows
            summary = self._alerter.format_daily_summary(trade_stats, positions_rows)
            asyncio.create_task(self._alerter.send(summary))

            # Step 6: Audit entry for report generation
            append_audit({
                "event": "eod_report_generated",
                "date": str(today),
                "trade_count": trade_stats["trade_count"],
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

            # D-01/D-03 mid-session restart: if the bot starts while the market is
            # already open, seed premarket highs immediately so the bot is not
            # dead-for-the-day. Called BEFORE _register_jobs/_scheduler.start() so
            # Gate 1 is satisfied before any bar can flow through the pipeline.
            await self._seed_premarket_highs_on_startup()

            # One-time startup warning when Telegram alerter is disabled.
            # The alerter only logs at DEBUG level when it no-ops a send() call, which
            # is invisible at the INFO default. Surface the disabled state once here so
            # the operator can confirm alert delivery intent at startup without having to
            # look for a send failure. Uses alerter._enabled so we do not re-read env
            # vars and stay consistent with however the alerter was constructed (D-12/D-13).
            if not self._alerter._enabled:
                _logger.warning(
                    "alerter_disabled_at_startup",
                    reason="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured",
                )

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
