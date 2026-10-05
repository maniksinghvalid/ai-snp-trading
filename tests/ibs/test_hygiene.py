#!/usr/bin/env python3
"""
tests/ibs/test_hygiene.py — static source checks on bot/ibs/*.py.

Enforces D-06 (no force-close / flattening), D-08 (LIMIT only, no manage_exit),
D-10 (no unlock_trade, no yfinance, no live subscriptions), D-13 (separate process:
no equity trade-loop imports), D-19 and IBS-01 (no strategy literal in Python —
every number lives in rules_ibs.json).
"""
import ast
import io
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IBS_FILES = sorted((REPO_ROOT / "bot" / "ibs").glob("*.py"))

FORBIDDEN_TOKENS = (
    "OrderType.MARKET", "force_close", "unlock_trade", "manage_exit",
    "yfinance", ".subscribe(",
)
STRATEGY_NUMBERS = {0.2, 0.8, 10000.0, 100000.0, 900.0}
FORBIDDEN_IMPORT_PREFIXES = (
    "bot.service.bot", "bot.position", "bot.signal", "bot.scanner.scanner",
    "bot.execution.engine",
)


def _tokens(path):
    return tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)


def test_ibs_files_found():
    assert IBS_FILES


def test_no_forbidden_tokens_in_ibs_package():
    hits = [(f.name, tok) for f in IBS_FILES
            for tok in FORBIDDEN_TOKENS if tok in f.read_text(encoding="utf-8")]
    assert hits == []


def test_no_strategy_literals_in_ibs_package():
    hits = []
    for f in IBS_FILES:
        for tok in _tokens(f):
            if tok.type == tokenize.NUMBER:
                try:
                    if float(tok.string) in STRATEGY_NUMBERS:
                        hits.append(f"{f.name}:{tok.start[0]} {tok.string}")
                except ValueError:
                    pass
            elif tok.type == tokenize.STRING:
                try:
                    val = ast.literal_eval(tok.string)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(val, str) and val.startswith("US."):
                    hits.append(f"{f.name}:{tok.start[0]} {tok.string}")
    assert hits == []


def test_ibs_never_imports_equity_trade_loop():
    hits = []
    for f in IBS_FILES:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    hits.append(f"{f.name}:{node.lineno} {name}")
    assert hits == []
