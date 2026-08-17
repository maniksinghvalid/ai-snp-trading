#!/usr/bin/env python3
"""
tests/options/test_dispatch.py — strategy_name dispatch + `--rules` argv (D5).

bot.main.main() peeks the rules file's top-level strategy_name and hands the
process to the options bot for "tasty_credit_spreads"; anything else keeps the
existing equity construction sequence, now honouring the given path instead of
the old hard-coded "rules.json".
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.main
import bot.options.service
from bot.__main__ import _parse_args


def _write_rules(tmp_path, payload) -> str:
    path = tmp_path / "rules_under_test.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8")
    return str(path)


def _patch_equity_seams(monkeypatch):
    """Patch the equity path's three I/O seams (mirrors tests/test_main_wiring)."""
    mock_gateway = AsyncMock()
    monkeypatch.setattr(bot.main, "MoomooGateway", MagicMock(return_value=mock_gateway))
    monkeypatch.setattr(bot.main, "StateStore", MagicMock())
    monkeypatch.setattr(bot.main.asyncio, "run", lambda coro: coro.close())
    return bot.main.MoomooGateway


# ============================================================
# Dispatch
# ============================================================

def test_options_strategy_name_dispatches_to_options_main(tmp_path, monkeypatch):
    rules = _write_rules(tmp_path, {"strategy_name": "tasty_credit_spreads"})
    mock_gateway_cls = _patch_equity_seams(monkeypatch)

    seen = []
    monkeypatch.setattr(bot.options.service, "main", lambda path: seen.append(path))

    bot.main.main(rules_path=rules)

    assert seen == [rules]
    mock_gateway_cls.assert_not_called()


def test_equity_path_loads_the_given_rules_path(tmp_path, monkeypatch):
    # The shipped rules.json, relocated: the equity bot must load the path it is
    # given, not the hard-coded "rules.json" it used before D5.
    rules = _write_rules(tmp_path, Path("rules.json").read_text(encoding="utf-8"))
    _patch_equity_seams(monkeypatch)

    real_loader = bot.main.load_strategy_config
    seen = []

    def _recording_loader(path):
        seen.append(path)
        return real_loader(path)

    monkeypatch.setattr(bot.main, "load_strategy_config", _recording_loader)

    bot.main.main(rules_path=rules)

    assert seen == [rules]


def test_missing_rules_file_exits_1_with_error(tmp_path, monkeypatch, capsys):
    _patch_equity_seams(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        bot.main.main(rules_path=str(tmp_path / "nope.json"))

    assert exc.value.code == 1
    assert "[ERROR]" in capsys.readouterr().err


def test_malformed_rules_file_exits_1_with_error(tmp_path, monkeypatch, capsys):
    rules = _write_rules(tmp_path, "{not json")
    _patch_equity_seams(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        bot.main.main(rules_path=rules)

    assert exc.value.code == 1
    assert "[ERROR]" in capsys.readouterr().err


# ============================================================
# argv
# ============================================================

def test_parse_args_defaults_to_rules_json():
    assert _parse_args([]).rules == "rules.json"


def test_parse_args_honours_rules_flag():
    assert _parse_args(["--rules", "rules_options.json"]).rules == "rules_options.json"
