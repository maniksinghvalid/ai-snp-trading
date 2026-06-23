#!/usr/bin/env python3
"""
bot.gateway — Broker access layer for the Moomoo/Futu OpenAPI.

Exports MoomooGateway (persistent OpenD contexts, run_in_executor wrappers,
pre-flight connectivity check, reconciliation skeletons), GatewayConfig,
GatewayError, and get_gateway_config.
"""
from bot.gateway.gateway import (
    MoomooGateway,
    GatewayConfig,
    GatewayError,
    get_gateway_config,
)

__all__ = ["MoomooGateway", "GatewayConfig", "GatewayError", "get_gateway_config"]
