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
            targetFile=str(external_file),
            searchContent="hello",
            replaceContent="world",
            dryRun=True
        )

        assert "error" in res
        assert res["error"] == "fatal_context_mismatch"
        assert "fatal_context_mismatch" in res.get("error", "") or "fatal_context_mismatch" == res.get("error")

    def test_dry_run_generates_diff(self, tmp_path, monkeypatch):
        """smart_patcher must return a diff on dryRun and not modify the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        res = smart_patcher(
            targetFile="app.py",
            searchContent="line 2",
            replaceContent="modified line 2",
            dryRun=True
        )

        assert "success" in res
        assert res["success"] is True
        assert res["dryRun"] is True
        assert "modified line 2" in res["message"]
        # Verify file is not changed
        assert app_file.read_text() == "line 1\nline 2\nline 3\n"

    def test_successful_patch(self, tmp_path, monkeypatch):
        """smart_patcher must successfully modify the file."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        res = smart_patcher(
            targetFile="app.py",
            searchContent="line 2",
            replaceContent="modified line 2",
            dryRun=False
        )

        assert "success" in res
        assert res["success"] is True
        assert res["dryRun"] is False
        assert app_file.read_text() == "line 1\nmodified line 2\nline 3\n"

    def test_folder_and_file_filters(self, tmp_path, monkeypatch):
        """smart_patcher must respect folderFilter and fileFilter."""
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        app_file = src_dir / "app.py"
        app_file.write_text("test content\n")

        # Wrong folder filter
        res = smart_patcher(
            targetFile="src/app.py",
            searchContent="test",
            replaceContent="best",
            folderFilter="tests",
            dryRun=True
        )
        assert "error" in res

        # Correct folder filter
        res = smart_patcher(
            targetFile="src/app.py",
            searchContent="test",
            replaceContent="best",
            folderFilter="src",
            dryRun=True
        )
        assert "success" in res

        # Wrong file filter
        res = smart_patcher(
            targetFile="src/app.py",
            searchContent="test",
            replaceContent="best",
            fileFilter="test",
            dryRun=True
        )
        assert "error" in res

        # Correct file filter
        res = smart_patcher(
            targetFile="src/app.py",
            searchContent="test",
            replaceContent="best",
            fileFilter="app",
            dryRun=True
        )
        assert "success" in res

    def test_line_filter_assertions(self, tmp_path, monkeypatch):
        """smart_patcher must respect numeric and string lineFilter assertions."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("line 1\nline 2\nline 3\n")

        # Numeric lineFilter matches actual line
        res = smart_patcher(
            targetFile="app.py",
            searchContent="line 2",
            replaceContent="modified",
            lineFilter=2,
            dryRun=True
        )
        assert "success" in res

        # Numeric lineFilter mismatch
        res = smart_patcher(
            targetFile="app.py",
            searchContent="line 2",
            replaceContent="modified",
            lineFilter=3,
            dryRun=True
        )
        assert "error" in res

        # String lineFilter match
        res = smart_patcher(
            targetFile="app.py",
            searchContent="line 2",
            replaceContent="modified",
            lineFilter="line 2",
            dryRun=True
        )
        assert "success" in res

        # String lineFilter mismatch
        res = smart_patcher(
            targetFile="app.py",
            searchContent="line 2",
            replaceContent="modified",
            lineFilter="unrelated",
            dryRun=True
        )
        assert "error" in res

    def test_allow_multiple_check(self, tmp_path, monkeypatch):
        """smart_patcher must refuse to modify multiple matches by default unless allowMultiple is True."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "app.py"
        app_file.write_text("dup\ndup\n")

        # Default is False
        res = smart_patcher(
            targetFile="app.py",
            searchContent="dup",
            replaceContent="new",
            allowMultiple=False,
            dryRun=True
        )
        assert "error" in res

        # Explicit True
        res = smart_patcher(
            targetFile="app.py",
            searchContent="dup",
            replaceContent="new",
            allowMultiple=True,
            dryRun=True
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
            targetFile="app.py",
            searchContent="dup",
            replaceContent="new",
            startLine=1,
            endLine=2,
            allowMultiple=True,
            dryRun=False
        )
        assert "success" in res
        # Only lines 1 and 2 replaced
        assert app_file.read_text() == "new\nnew\ndup\n"

    def test_ast_symbol_boundary(self, tmp_path, monkeypatch):
        """smart_patcher must restrict search and replace to AST symbolName boundaries by loading the index."""
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
        
        # Call smart_patcher with symbolName boundary (restricts to target_func, lines 1-3)
        res = smart_patcher(
            targetFile="app.py",
            searchContent="dup",
            replaceContent="new",
            symbolName="target_func",
            dryRun=False,
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
