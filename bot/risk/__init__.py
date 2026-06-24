#!/usr/bin/env python3
"""
bot.risk — Risk sizing and OrderIntent emission for the AI S&P Trading Bot.

Provides RiskEngine (1%-risk sizing, 10%-notional cap, <1-share guard,
live equity read via gateway, OrderIntent persistence, structlog audit)
and the OrderIntent dataclass.

Exports: OrderIntent (dataclass in bot.risk.events)
"""
