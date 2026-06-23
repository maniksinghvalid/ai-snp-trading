#!/usr/bin/env python3
"""
bot.config — Strategy configuration package.

Exports: load_strategy_config, StrategyConfig, ConfigError
"""

from bot.config.loader import load_strategy_config, StrategyConfig, ConfigError

__all__ = ["load_strategy_config", "StrategyConfig", "ConfigError"]
