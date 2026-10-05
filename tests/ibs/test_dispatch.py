#!/usr/bin/env python3
"""
tests/ibs/test_dispatch.py — `--rules rules_ibs.json` routes to bot.ibs.service.main (IBS-02).

bot.main.main() peeks the rules file's top-level strategy_name; the equity and
options routes must be unchanged (regression tests below).
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import bot.ibs.service
import bot.main
import bot.options.service

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_rules(tmp_path, payload) -> str:
    path = tmp_path / "rules_under_test.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _patch_equity_seams(monkeypatch):
    """Patch the equity path's I/O seams (mirrors tests/options/test_dispatch.py)."""
    mock_gateway = AsyncMock()
    monkeypatch.setattr(bot.main, "MoomooGateway", MagicMock(return_value=mock_gateway))
    monkeypatch.setattr(bot.main, "StateStore", MagicMock())
    monkeypatch.setattr(bot.main.asyncio, "run", lambda coro: coro.close())
    return bot.main.MoomooGateway


def test_ibs_strategy_name_dispatches_to_ibs_main(tmp_path, monkeypatch):
    rules = _write_rules(tmp_path, {"strategy_name": "ibs_etf_mean_reversion"})
    gateway_cls = _patch_equity_seams(monkeypatch)
    seen = []
    monkeypatch.setattr(bot.ibs.service, "main", lambda path: seen.append(path))
    monkeypatch.setattr(bot.options.service, "main", lambda path: seen.append("OPTIONS"))

    bot.main.main(rules_path=rules)

    assert seen == [rules]
    gateway_cls.assert_not_called()


def test_shipped_rules_ibs_dispatches_to_ibs_main(monkeypatch):
    rules = str(Path(__file__).resolve().parents[2] / "rules_ibs.json")
    gateway_cls = _patch_equity_seams(monkeypatch)
    seen = []
    monkeypatch.setattr(bot.ibs.service, "main", lambda path: seen.append(path))

    bot.main.main(rules_path=rules)

    assert seen == [rules]
    gateway_cls.assert_not_called()


def test_options_route_unchanged(tmp_path, monkeypatch):
    rules = _write_rules(tmp_path, {"strategy_name": "tasty_credit_spreads"})
    _patch_equity_seams(monkeypatch)
    seen = []
    monkeypatch.setattr(bot.options.service, "main", lambda path: seen.append(path))
    monkeypatch.setattr(bot.ibs.service, "main", lambda path: seen.append("IBS"))

    bot.main.main(rules_path=rules)

    assert seen == [rules]


def test_equity_route_never_imports_ibs_main(tmp_path, monkeypatch):
    rules = _write_rules(tmp_path, json.loads((REPO_ROOT / "rules.json").read_text(encoding="utf-8")))
    _patch_equity_seams(monkeypatch)
    seen = []
    monkeypatch.setattr(bot.ibs.service, "main", lambda path: seen.append("IBS"))

    bot.main.main(rules_path=rules)

    assert seen == []
