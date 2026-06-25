#!/usr/bin/env python3
"""
bot.service — Service orchestration layer for the AI S&P Trading Bot.

Submodules:
  bot.service.bot      — TradingBot: APScheduler-driven main loop (SVC-01)
  bot.service.watchdog — OpenDWatchdog: disconnect/reconnect polling (SVC-02)
  bot.service.alerter  — TelegramAlerter: push notifications (ALERT-01..04)
  bot.service.report   — ReportBuilder: EOD HTML dashboard (DASH-01)
"""
