#!/usr/bin/env python3
"""
backtester — Phase 6 offline strategy replay harness (Trend Join Long).

Reuses bot/strategy, bot/signal, bot/risk, bot/position unchanged; swaps only
the broker-facing leaves (feed/execution/gateway) for simulated equivalents so
historical yfinance 5m data can be replayed through the exact live pipeline.
"""
