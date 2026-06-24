#!/usr/bin/env python3
"""
bot.signal — Intraday signal pipeline for the AI S&P Trading Bot.

Provides BarAggregator (bar-close detection via timestamp-advance with
session-level dedup, SIG-02), dataclasses BarEvent and SignalEvent, and
SignalEngine (I1/I2/I3 intraday-filter + gate evaluation).

Exports: BarEvent, SignalEvent (dataclasses in bot.signal.events)
"""
