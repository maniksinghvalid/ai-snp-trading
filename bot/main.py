#!/usr/bin/env python3
"""
bot.main — Entry point for the AI S&P Trading Bot (Trend Join Long).

Composes all Phase 1-4 components, wires the KillSwitch, and runs TradingBot
under asyncio.run. This module is importable without side effects — asyncio.run
is only reached when main() is called (from bot/__main__.py).

Note: KillSwitch.register_flush(position_manager.flush_all) is wired INSIDE
TradingBot._readiness_gate() and TradingBot._register_jobs() — not here (R-04-01).
D-07 graceful shutdown (flush + audit + gateway.close + bot-stopped alert) runs
in TradingBot.run()'s finally block. OpenDWatchdog (SVC-02) is constructed here and
injected into the bot after construction (watchdog needs bot ref for _entries_enabled).

Exports: main
"""
import asyncio
import os
import sys

from bot.config.loader import ConfigError, load_strategy_config
from bot.execution.engine import ExecutionEngine
from bot.gateway.gateway import MoomooGateway, get_gateway_config
from bot.position.manager import PositionManager
from bot.risk.risk_engine import RiskEngine
from bot.safety.kill_switch import KillSwitch
from bot.safety.logger import configure_logging, get_logger
from bot.service.alerter import TelegramAlerter
from bot.service.bot import TradingBot
from bot.service.watchdog import OpenDWatchdog
from bot.signal.signal_engine import SignalEngine
from bot.state.store import StateStore
from bot.strategy.trend_join_long import TrendJoinLong

import bot.scanner.scanner as _scanner_module


# ============================================================
# Main entry point
# ============================================================

def main() -> None:
    """Compose all bot components and run TradingBot under asyncio.run.

    Construction order:
      1. configure_logging()  — must be first (before any structlog calls)
      2. load_strategy_config("rules.json")  — raises ConfigError → stderr + sys.exit(1)
      3. Read TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from env (never log these — Pitfall 4)
      4. Construct MoomooGateway, StateStore, TrendJoinLong, ExecutionEngine, TelegramAlerter
      5. Construct PositionManager with on_entry_alert/on_exit_alert lambdas that
         dispatch via asyncio.create_task (fire-and-forget, ALERT-04)
      5d. Construct SignalEngine (loop-independent leaf; cfg/gateway/store only — D-04)
      5e. Construct RiskEngine (loop-independent leaf; receives signal_engine ref for
          the D-09 daily-cap burst guard — RISK-05 / Pitfall 7)
      6. Construct KillSwitch
      7. Construct TradingBot then OpenDWatchdog (watchdog needs bot ref) — inject via bot._watchdog
      8. asyncio.run(bot.run())
    """
    # Step 1: Configure structured logging before any component is constructed
    configure_logging()
    _logger = get_logger(__name__)

    # Step 2: Load strategy config — ConfigError → stderr + sys.exit(1)
    try:
        cfg = load_strategy_config("rules.json")
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    # Step 3: Read Telegram secrets from environment (never log/interpolate — Pitfall 4)
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    # Step 4: Construct broker and state components
    gateway = MoomooGateway(get_gateway_config())
    store = StateStore().open()

    # Step 5a: Construct strategy and execution engine
    strategy = TrendJoinLong(cfg)
    engine = ExecutionEngine(gateway=gateway, store=store, cfg=cfg)

    # Step 5d: Construct SignalEngine — loop-independent leaf (D-04)
    # Takes only cfg/gateway/store; the loop-dependent aggregator stays in TradingBot.run()
    signal_engine = SignalEngine(cfg=cfg, gateway=gateway, store=store)

    # Step 5e: Construct RiskEngine — loop-independent leaf (D-04)
    # Receives signal_engine reference so note_intent_emitted() drives the D-09 daily-cap
    # burst guard (RISK-05 / Pitfall 7). signal_engine MUST be constructed first.
    risk_engine = RiskEngine(cfg=cfg, gateway=gateway, store=store, signal_engine=signal_engine)

    # Step 5b: Construct alerter
    alerter = TelegramAlerter(
        token=telegram_token,
        chat_id=telegram_chat_id,
        logger=_logger,
    )

    # Step 5c: Construct PositionManager with fire-and-forget alert lambdas (ALERT-04)
    # asyncio.create_task dispatches send() without blocking the trade loop (D-14)
    position_manager = PositionManager(
        store=store,
        engine=engine,
        cfg=cfg,
        strategy=strategy,
        on_entry_alert=lambda code, qty, price, stop: asyncio.create_task(
            alerter.send_entry_alert(code, qty, price, stop)
        ),
        on_exit_alert=lambda code, reason, r: asyncio.create_task(
            alerter.send_exit_alert(code, reason, r)
        ),
        gateway=gateway,
    )

    # Step 6: Construct KillSwitch (flush registration happens inside TradingBot)
    kill_switch = KillSwitch()

    # Step 7a: Construct TradingBot (watchdog injected after construction — needs bot ref)
    bot = TradingBot(
        cfg=cfg,
        gateway=gateway,
        store=store,
        scanner=_scanner_module,
        position_manager=position_manager,
        execution_engine=engine,
        kill_switch=kill_switch,
        alerter=alerter,
        watchdog=None,  # set below after watchdog construction
        signal_engine=signal_engine,
        risk_engine=risk_engine,
    )

    # Step 7b: Construct OpenDWatchdog with bot reference (SVC-02)
    watchdog = OpenDWatchdog(
        gateway=gateway,
        bot=bot,
        alerter=alerter,
        cfg=cfg,
    )
    bot._watchdog = watchdog

    # Step 8: Run the bot's async lifecycle (blocks until kill-switch triggered)
    asyncio.run(bot.run())
