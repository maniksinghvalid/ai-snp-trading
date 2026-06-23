#!/usr/bin/env python3
"""
tests/state/test_atomic_write.py — Crash-injection tests for atomic_write_json (D-10).

Verifies that atomic_write_json(path, data):
  (a) Writes successfully, result parses as JSON.
  (b) When os.replace is monkeypatched to raise mid-operation (crash after
      temp-write but before the atomic swap), the ORIGINAL file is unchanged
      and still valid JSON.
  (c) When the temp-write produces corrupt/truncated content, no partial
      content lands at the target path and the original is still valid.
  (d) A fresh target (no prior file) works correctly.
  (e) Permissions on the written file are restrictive (0600).
"""

import json
import os
import stat

import pytest

from bot.state.store import atomic_write_json


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def baseline_file(tmp_path):
    """Write a baseline JSON file at tmp_path/state.json and return its path."""
    path = str(tmp_path / "state.json")
    original = {"version": 1, "positions": [], "status": "baseline"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(original, f)
    return path


@pytest.fixture
def new_data():
    """Fresh data dict to write atomically."""
    return {"version": 2, "positions": [{"id": "POS-001", "code": "US.AAPL"}]}


# ============================================================
# Happy-path tests
# ============================================================

class TestAtomicWriteSuccess:
    """Successful writes."""

    def test_successful_write_produces_valid_json(self, tmp_path, new_data):
        """A successful atomic_write_json call writes valid JSON to the path."""
        path = str(tmp_path / "state.json")
        atomic_write_json(path, new_data)
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
        assert result == new_data

    def test_successful_write_replaces_prior_content(self, baseline_file, new_data):
        """Atomic write replaces prior file content with new data."""
        atomic_write_json(baseline_file, new_data)
        with open(baseline_file, "r", encoding="utf-8") as f:
            result = json.load(f)
        assert result["version"] == 2
        assert result["positions"][0]["id"] == "POS-001"

    def test_no_temp_files_left_after_success(self, tmp_path, new_data):
        """No .tmp files must remain in the directory after a successful write."""
        path = str(tmp_path / "state.json")
        atomic_write_json(path, new_data)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Unexpected .tmp files: {tmp_files}"

    def test_write_to_fresh_path_works(self, tmp_path, new_data):
        """Writing to a path with no prior file must succeed and produce valid JSON."""
        path = str(tmp_path / "fresh_state.json")
        assert not os.path.exists(path)
        atomic_write_json(path, new_data)
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
        assert result == new_data

    def test_unicode_data_preserved(self, tmp_path):
        """Unicode strings in data must be preserved round-trip (ensure_ascii=False)."""
        path = str(tmp_path / "unicode.json")
        data = {"name": "テスト", "emoji": "📈", "code": "US.AAPL"}
        atomic_write_json(path, data)
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
        assert result == data

    def test_written_file_has_restrictive_permissions(self, tmp_path, new_data):
        """Written file must have 0600 permissions (owner r/w only, T-01-07)."""
        path = str(tmp_path / "state.json")
        atomic_write_json(path, new_data)
        mode = os.stat(path).st_mode
        # Only owner read and write bits should be set
        assert mode & stat.S_IRUSR, "Owner read bit must be set"
        assert mode & stat.S_IWUSR, "Owner write bit must be set"
        assert not (mode & stat.S_IRGRP), "Group read bit must NOT be set"
        assert not (mode & stat.S_IROTH), "Other read bit must NOT be set"


# ============================================================
# Crash-injection: os.replace raises mid-operation
# ============================================================

class TestCrashInjectionReplaceRaises:
    """Simulate a crash after temp-write but before the atomic os.replace swap."""

    def test_original_file_unchanged_when_replace_raises(
        self, baseline_file, new_data, monkeypatch
    ):
        """When os.replace raises, the original file must be byte-for-byte unchanged."""
        # Record the exact original bytes before the crash
        with open(baseline_file, "rb") as f:
            original_bytes = f.read()

        # Monkeypatch os.replace to raise — simulates a crash mid-swap
        def crash_replace(src, dst):
            raise OSError("Simulated crash during os.replace")

        monkeypatch.setattr(os, "replace", crash_replace)

        with pytest.raises(OSError, match="Simulated crash"):
            atomic_write_json(baseline_file, new_data)

        # Original file must be byte-for-byte unchanged
        with open(baseline_file, "rb") as f:
            after_bytes = f.read()
        assert after_bytes == original_bytes, (
            "Original file was corrupted after os.replace crash injection"
        )

    def test_original_still_valid_json_when_replace_raises(
        self, baseline_file, new_data, monkeypatch
    ):
        """When os.replace raises, the original file must still parse as valid JSON."""
        monkeypatch.setattr(os, "replace", lambda src, dst: (_ for _ in ()).throw(
            OSError("crash")
        ))

        try:
            atomic_write_json(baseline_file, new_data)
        except OSError:
            pass  # expected — swallow the injected exception

        with open(baseline_file, "r", encoding="utf-8") as f:
            result = json.load(f)  # must not raise
        assert result["status"] == "baseline"  # original content intact

    def test_no_temp_files_left_after_replace_crash(
        self, tmp_path, new_data, monkeypatch
    ):
        """Temp file must be cleaned up even when os.replace raises."""
        path = str(tmp_path / "state.json")
        # Create a baseline
        with open(path, "w") as f:
            json.dump({"v": 0}, f)

        crash_count = {"n": 0}

        original_replace = os.replace

        def crash_replace(src, dst):
            crash_count["n"] += 1
            raise OSError("crash")

        monkeypatch.setattr(os, "replace", crash_replace)

        try:
            atomic_write_json(path, new_data)
        except OSError:
            pass

        # No .tmp files must remain
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Orphan .tmp files after crash: {tmp_files}"


# ============================================================
# Crash-injection: corrupt temp write
# ============================================================

class TestCrashInjectionCorruptTempWrite:
    """Simulate a truncated or corrupt write to the temp file."""

    def test_corrupt_json_not_written_to_target(self, tmp_path, monkeypatch):
        """If json.dump produces corrupt output, the target path must not be touched."""
        path = str(tmp_path / "state.json")
        original = {"status": "safe"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(original, f)
        with open(path, "rb") as f:
            original_bytes = f.read()

        # Monkeypatch json.dump to write truncated/invalid JSON
        original_dump = json.dump

        def corrupt_dump(obj, fp, **kwargs):
            # Write a truncated/invalid JSON fragment instead of the real data
            fp.write('{"truncated": true')  # missing closing }

        monkeypatch.setattr(json, "dump", corrupt_dump)

        # The validate step (json.load on temp file) must raise JSONDecodeError
        # and the original file must remain unchanged
        with pytest.raises(json.JSONDecodeError):
            atomic_write_json(path, {"new": "data"})

        # Original file must be unchanged
        with open(path, "rb") as f:
            after_bytes = f.read()
        assert after_bytes == original_bytes, (
            "Original file was overwritten despite corrupt temp write"
        )

    def test_no_partial_content_at_target_after_corrupt_write(
        self, tmp_path, monkeypatch
    ):
        """No partial content must land at the target path after a corrupt temp write."""
        path = str(tmp_path / "clean.json")
        # No prior file — fresh path

        original_dump = json.dump

        def corrupt_dump(obj, fp, **kwargs):
            fp.write("{bad json")  # invalid

        monkeypatch.setattr(json, "dump", corrupt_dump)

        try:
            atomic_write_json(path, {"will": "fail"})
        except (json.JSONDecodeError, Exception):
            pass  # expected

        # The target file must NOT exist (no prior file, swap never happened)
        assert not os.path.exists(path), (
            "Target path must not exist after failed write to a fresh path"
        )

    def test_no_temp_files_left_after_corrupt_write(self, tmp_path, monkeypatch):
        """Temp file must be cleaned up when the validate step raises."""
        path = str(tmp_path / "state.json")

        original_dump = json.dump

        def corrupt_dump(obj, fp, **kwargs):
            fp.write("{invalid")

        monkeypatch.setattr(json, "dump", corrupt_dump)

        try:
            atomic_write_json(path, {"k": "v"})
        except Exception:
            pass

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Orphan .tmp after corrupt write: {tmp_files}"


# ============================================================
# Nested data structure tests
# ============================================================

class TestAtomicWriteDataTypes:
    """Various data types that must round-trip through atomic_write_json."""

    def test_nested_dict_round_trips(self, tmp_path):
        data = {"a": {"b": {"c": [1, 2, 3]}}}
        path = str(tmp_path / "nested.json")
        atomic_write_json(path, data)
        with open(path, "r") as f:
            assert json.load(f) == data

    def test_empty_dict_round_trips(self, tmp_path):
        data = {}
        path = str(tmp_path / "empty.json")
        atomic_write_json(path, data)
        with open(path, "r") as f:
            assert json.load(f) == data

    def test_large_data_round_trips(self, tmp_path):
        data = {"positions": [{"id": f"P{i}", "val": i * 1.5} for i in range(1000)]}
        path = str(tmp_path / "large.json")
        atomic_write_json(path, data)
        with open(path, "r") as f:
            result = json.load(f)
        assert len(result["positions"]) == 1000
