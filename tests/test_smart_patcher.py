"""Tests for smart_patcher tool."""

from pathlib import Path
import pytest

from jcodemunch_mcp.tools.smart_patcher import smart_patcher
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.resolve_repo import _compute_repo_id


class TestSmartPatcher:
    def test_context_mismatch_guard(self, tmp_path):
        """smart_patcher must refuse to edit a file outside the active workspace."""
        external_dir = tmp_path / "external"
        external_dir.mkdir()
        external_file = external_dir / "app.py"
        external_file.write_text("print('hello')\n")

        # Call smart_patcher pointing outside the current working directory
        res = smart_patcher(
            target_file=str(external_file),
            search_content="hello",
            replace_content="world",
            dry_run=True
        )

        assert "error" in res
        assert res["error"] == "fatal_context_mismatch"

    def test_dry_run_generates_diff(self, tmp_path, monkeypatch):
        """smart_patcher must return a diff on dry_run and not modify the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        res = smart_patcher(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=True
        )

        assert "success" in res
        assert res["success"] is True
        assert res["dryRun"] is True
        assert "modified line 2" in res["message"]
        # Verify file is not changed
        assert app_file.read_text() == "line 1\nline 2\nline 3\n"

    def test_patch_success(self, tmp_path, monkeypatch):
        """smart_patcher must successfully modify the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        res = smart_patcher(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified line 2",
            dry_run=False
        )

        assert "success" in res
        assert res["success"] is True
        assert res["dryRun"] is False
        assert app_file.read_text() == "line 1\nmodified line 2\nline 3\n"

    def test_folder_and_file_filters(self, tmp_path, monkeypatch):
        """smart_patcher must respect folder_filter and file_filter."""
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        app_file = src_dir / "app.py"
        app_file.write_text("test content\n")

        # Wrong folder filter
        res = smart_patcher(
            target_file="src/app.py",
            search_content="test",
            replace_content="best",
            folder_filter="tests",
            dry_run=True
        )
        assert "error" in res

        # Correct folder filter
        res = smart_patcher(
            target_file="src/app.py",
            search_content="test",
            replace_content="best",
            folder_filter="src",
            dry_run=True
        )
        assert "success" in res

        # Wrong file filter
        res = smart_patcher(
            target_file="src/app.py",
            search_content="test",
            replace_content="best",
            file_filter="test",
            dry_run=True
        )
        assert "error" in res

        # Correct file filter
        res = smart_patcher(
            target_file="src/app.py",
            search_content="test",
            replace_content="best",
            file_filter="app",
            dry_run=True
        )
        assert "success" in res

    def test_line_filter_assertions(self, tmp_path, monkeypatch):
        """smart_patcher must respect numeric and string line_filter assertions."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        # Numeric line_filter matches actual line
        res = smart_patcher(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified",
            line_filter=2,
            dry_run=True
        )
        assert "success" in res

        # Numeric line_filter mismatch
        res = smart_patcher(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified",
            line_filter=3,
            dry_run=True
        )
        assert "error" in res

        # String line_filter match
        res = smart_patcher(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified",
            line_filter="line 2",
            dry_run=True
        )
        assert "success" in res

        # String line_filter mismatch
        res = smart_patcher(
            target_file="app.py",
            search_content="line 2",
            replace_content="modified",
            line_filter="unrelated",
            dry_run=True
        )
        assert "error" in res

    def test_allow_multiple_check(self, tmp_path, monkeypatch):
        """smart_patcher must refuse to modify multiple matches by default unless allow_multiple is True."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("dup\ndup\n")

        # Default is False
        res = smart_patcher(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            allow_multiple=False,
            dry_run=True
        )
        assert "error" in res

        # Explicit True
        res = smart_patcher(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            allow_multiple=True,
            dry_run=True
        )
        assert "success" in res
        assert res["occurrences"] == 2

    def test_line_range_boundary(self, tmp_path, monkeypatch):
        """smart_patcher must restrict search and replace to the specified line boundaries."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("dup\ndup\ndup\n")

        # Scope lines 1-2 only (contains 2 occurrences)
        res = smart_patcher(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            start_line=1,
            end_line=2,
            allow_multiple=True,
            dry_run=False
        )
        assert "success" in res
        # Only lines 1 and 2 replaced
        assert app_file.read_text() == "new\nnew\ndup\n"

    def test_ast_symbol_boundary(self, tmp_path, monkeypatch):
        """smart_patcher must restrict search and replace to AST symbol_name boundaries by loading the index."""
        monkeypatch.chdir(tmp_path)
        
        # Write a file containing a python function
        app_file = tmp_path / "app.py"
        app_file.write_text(
            "def target_func():\n"
            "    val = 'dup'\n"
            "    return val\n"
            "\n"
            "other_val = 'dup'\n"
        )
        
        # Index the directory
        store_path = str(tmp_path / "store")
        index_folder(str(tmp_path), use_ai_summaries=False, storage_path=store_path, identity_mode="local")
        
        # Call smart_patcher with symbol_name boundary (restricts to target_func, lines 1-3)
        res = smart_patcher(
            target_file="app.py",
            search_content="dup",
            replace_content="new",
            symbol_name="target_func",
            dry_run=False,
            storage_path=store_path
        )
        
        assert "success" in res
        assert res["success"] is True
        
        # Verify only the 'dup' inside target_func was replaced
        expected_content = (
            "def target_func():\n"
            "    val = 'new'\n"
            "    return val\n"
            "\n"
            "other_val = 'dup'\n"
        )
        assert app_file.read_text() == expected_content
