"""Tests for check_edit_safe — edit preflight composite tool."""

from pathlib import Path
import pytest

from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from .conftest_helpers import create_custom_index


_SAFE_REPO = {
    "used.py": (
        "def used_func():\n"
        "    return 1\n"
    ),
    "lonely.py": (
        "def orphan_func():\n"
        "    return 'isolated'\n"
    ),
    "consumer.py": (
        "from used import used_func\n\n"
        "def consume():\n"
        "    return used_func() + 1\n"
    ),
}

_COMPLEX_REPO = {
    "complex_file.py": (
        "def complex_func(x):\n"
        "    if x > 1:\n"
        "        if x > 2:\n"
        "            if x > 3:\n"
        "                if x > 4:\n"
        "                    if x > 5:\n"
        "                        if x > 6:\n"
        "                            if x > 7:\n"
        "                                if x > 8:\n"
        "                                    if x > 9:\n"
        "                                        if x > 10:\n"
        "                                            return 1\n"
        "    return 0\n"
    )
}


class TestCheckEditSafe:
    def test_isolated_function_returns_safe_to_edit(self, tmp_path):
        repo, storage = create_custom_index(tmp_path, _SAFE_REPO)
        result = check_edit_safe(repo, symbol="orphan_func", storage_path=storage)
        assert "error" not in result, result
        assert result["verdict"] == "safe_to_edit"
        assert result["confidence"] >= 0.9

    def test_used_function_returns_signature_impact(self, tmp_path):
        repo, storage = create_custom_index(tmp_path, _SAFE_REPO)
        result = check_edit_safe(repo, symbol="used_func", storage_path=storage)
        assert "error" not in result
        assert result["verdict"] == "signature_impact_risky"
        assert result["confidence"] <= 0.65

    def test_complexity_risky(self, tmp_path):
        repo, storage = create_custom_index(tmp_path, _COMPLEX_REPO)
        result = check_edit_safe(repo, symbol="complex_func", storage_path=storage)
        assert "error" not in result
        # Should be classified as complexity_risky since cyclomatic > 10
        assert result["verdict"] == "complexity_risky"
        assert result["cyclomatic"] >= 11
        assert "highly complex" in result["recommended_action"].lower()

    def test_unknown_symbol(self, tmp_path):
        repo, storage = create_custom_index(tmp_path, _SAFE_REPO)
        result = check_edit_safe(repo, symbol="DoesNotExist", storage_path=storage)
        assert "error" in result
