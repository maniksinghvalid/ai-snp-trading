#!/usr/bin/env python3
"""
tests/safety/test_audit_log.py — Tests for bot.safety.audit_log.

Verifies:
  - append_audit writes a JSONL line to the audit log
  - Two calls yield exactly two lines and the first line is preserved (append-only)
  - An unwritable path does NOT raise (silent-on-failure per SAFE-05)
  - Each entry contains 'timestamp' and 'bot_version' injected fields
"""
import json
import os
import pytest

from bot.safety.audit_log import append_audit, AUDIT_LOG_PATH


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_audit_path(tmp_path, monkeypatch):
    """Redirect AUDIT_LOG_PATH to a temp file for test isolation."""
    audit_file = str(tmp_path / "test_audit.jsonl")
    # Monkeypatch the module-level constant used by append_audit
    import bot.safety.audit_log as audit_mod
    monkeypatch.setattr(audit_mod, "AUDIT_LOG_PATH", audit_file)
    return audit_file


# ============================================================
# Tests
# ============================================================

class TestAppendAudit:
    """Tests for append_audit()."""

    def test_writes_single_entry(self, tmp_audit_path):
        """A single call should write exactly one JSONL line."""
        append_audit({"event": "test_event", "key": "value"})
        with open(tmp_audit_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "test_event"
        assert entry["key"] == "value"

    def test_appends_not_overwrites(self, tmp_audit_path):
        """Two calls must yield exactly two lines; the first line must be unchanged."""
        append_audit({"event": "first_event", "order_id": "A001"})
        append_audit({"event": "second_event", "order_id": "A002"})

        with open(tmp_audit_path, encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 2, "Expected exactly 2 JSONL lines after 2 calls"

        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["event"] == "first_event"
        assert first["order_id"] == "A001"
        assert second["event"] == "second_event"
        assert second["order_id"] == "A002"

    def test_first_line_preserved_after_second_write(self, tmp_audit_path):
        """After two writes, the first line must be byte-for-byte unchanged."""
        append_audit({"event": "preserved_event"})

        with open(tmp_audit_path, encoding="utf-8") as f:
            original_first_line = f.readline()

        append_audit({"event": "second_event"})

        with open(tmp_audit_path, encoding="utf-8") as f:
            after_second_first_line = f.readline()

        assert original_first_line == after_second_first_line, (
            "First line changed after a second append — audit log is not append-only"
        )

    def test_injects_timestamp(self, tmp_audit_path):
        """append_audit must inject a 'timestamp' field in UTC ISO-8601 format."""
        append_audit({"event": "check_timestamp"})
        with open(tmp_audit_path, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        assert "timestamp" in entry
        ts = entry["timestamp"]
        assert ts.endswith("Z"), f"Expected UTC timestamp ending in Z, got: {ts}"

    def test_injects_bot_version(self, tmp_audit_path):
        """append_audit must inject a 'bot_version' field."""
        append_audit({"event": "check_version"})
        with open(tmp_audit_path, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        assert "bot_version" in entry
        assert entry["bot_version"], "bot_version must be a non-empty string"

    def test_does_not_raise_on_unwritable_path(self, monkeypatch):
        """An unwritable path must never raise — SAFE-05 silent-on-failure."""
        import bot.safety.audit_log as audit_mod
        monkeypatch.setattr(audit_mod, "AUDIT_LOG_PATH", "/nonexistent_dir/audit.jsonl")
        # Must not raise any exception
        append_audit({"event": "should_silently_fail"})

    def test_unicode_content_preserved(self, tmp_audit_path):
        """ensure_ascii=False means Unicode content is preserved verbatim."""
        append_audit({"event": "unicode_test", "symbol": "US.AAPL", "note": "日本語"})
        with open(tmp_audit_path, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        assert entry["note"] == "日本語"

    def test_does_not_mutate_caller_dict(self, tmp_audit_path):
        """append_audit must not modify the caller's dict (it should work on a copy)."""
        original = {"event": "immutability_test"}
        original_keys_before = set(original.keys())
        append_audit(original)
        assert set(original.keys()) == original_keys_before, (
            "append_audit mutated the caller's dict"
        )
