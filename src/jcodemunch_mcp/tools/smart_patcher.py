"""Highly robust, AST-bounded file editor (smart_patcher)."""

import difflib
import os
import time
from pathlib import Path
from typing import Optional, Union

from ..storage import IndexStore
from ..security import validate_path


def generate_diff(original: str, modified: str, filename: str) -> str:
    """Generate a unified diff representation of changes."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3
    )
    return "".join(diff)


def _resolve_ast_boundaries(
    cwd: Path,
    target_path: Path,
    symbol_name: Optional[str],
    storage_path: Optional[str],
    start_line: Optional[int],
    end_line: Optional[int],
) -> tuple[Optional[int], Optional[int], Optional[dict]]:
    """Resolve start and end lines for AST symbolName boundary."""
    if not symbol_name:
        return start_line, end_line, None

    try:
        from .resolve_repo import resolve_repo as resolve_repo_fn
        repo_res = resolve_repo_fn(str(cwd), storage_path)
        if not repo_res.get("found"):
            return None, None, {"error": f"Workspace at '{cwd}' is not indexed. Call index_folder first to resolve symbols."}
        
        repo_id = repo_res["repo"]
        owner, name = repo_id.split("/", 1)
        
        store = IndexStore(base_path=storage_path)
        index = store.load_index(owner, name)
        if not index:
            return None, None, {"error": f"Index for '{repo_id}' could not be loaded."}
            
        source_root = Path(repo_res.get("source_root") or index.source_root or cwd).resolve()
        try:
            rel_file_path = str(target_path.relative_to(source_root)).replace("\\", "/")
        except ValueError:
            rel_file_path = str(target_path.relative_to(cwd)).replace("\\", "/")
            
        matched_symbols = []
        for sym in index.symbols:
            if sym.get("name") == symbol_name and sym.get("file") == rel_file_path:
                matched_symbols.append(sym)
                
        if not matched_symbols:
            return None, None, {"error": f"Symbol '{symbol_name}' not found in file '{rel_file_path}'."}
            
        symbol = matched_symbols[0]
        return symbol["line"], symbol["end_line"], None
    except Exception as e:
        return None, None, {"error": f"Error resolving symbolName '{symbol_name}': {e}"}


def _apply_line_filters(
    target_slice: str,
    norm_search: str,
    start_idx: int,
    line_filter: Optional[Union[str, int]],
) -> Optional[dict]:
    """Assert lineFilter substring or numeric line index checks."""
    if line_filter is None:
        return None

    is_numeric = False
    try:
        assert_line_num = int(line_filter)
        is_numeric = True
    except (ValueError, TypeError):
        pass

    if is_numeric:
        match_index = target_slice.find(norm_search)
        before_match = target_slice[:match_index]
        lines_before_match = before_match.count("\n")
        actual_start_line = start_idx + 1 + lines_before_match
        if actual_start_line != assert_line_num:
            return {
                "error": f"Error: lineFilter assertion failed! The search content starts at line {actual_start_line}, but lineFilter asserted line {assert_line_num}."
            }
    else:
        if str(line_filter) not in target_slice:
            return {
                "error": f"Error: lineFilter assertion failed! The target scope does not contain the substring '{line_filter}'."
            }
    return None


def _read_file_and_check_filters(
    target_path: Path,
    cwd: Path,
    folder_filter: Optional[str],
    file_filter: Optional[str],
) -> tuple[Optional[str], Optional[dict]]:
    """Verify filters and read the original file content."""
    if folder_filter:
        resolved_folder = (cwd / folder_filter).resolve()
        try:
            target_path.relative_to(resolved_folder)
        except ValueError:
            return None, {"error": f"Target file does not reside inside folder_filter '{folder_filter}'"}

    if file_filter:
        file_name = target_path.name
        if file_filter not in file_name:
            return None, {"error": f"Target file name '{file_name}' does not match file_filter '{file_filter}'"}

    if not target_path.exists():
        return None, {"error": f"Target file not found at {target_path}"}

    try:
        return target_path.read_text(encoding="utf-8", errors="replace"), None
    except Exception as e:
        return None, {"error": f"Failed to read file: {e}"}


def _slice_and_check_occurrences(
    file_lines: list[str],
    start_idx: int,
    end_idx: int,
    norm_search: str,
    allow_multiple: bool,
    symbol_name: Optional[str],
) -> tuple[Optional[str], Optional[dict]]:
    """Slice target content and verify occurrence counts within scope."""
    target_slice = "\n".join(file_lines[start_idx:end_idx + 1])
    occurrences = target_slice.count(norm_search)

    if occurrences == 0:
        first_lines = "\n".join(norm_search.split("\n")[:3])
        err_msg = f"Error: Search content not found inside the specified scope (lines {start_idx + 1} to {end_idx + 1})!\nFirst 3 lines of search block:\n{first_lines}"
        if symbol_name:
            err_msg += f"\nAST Scope: Symbol '{symbol_name}' at lines {start_idx + 1}-{end_idx + 1}"
        return None, {"error": err_msg}

    if not allow_multiple and occurrences > 1:
        return None, {
            "error": (
                f"Error: Search content occurs {occurrences} times within the specified scope (lines {start_idx + 1} to {end_idx + 1}). "
                "To replace all, set 'allow_multiple: true'."
            )
        }

    return target_slice, None


def _smart_patcher_impl(
    target_file: str,
    search_content: str,
    replace_content: str,
    folder_filter: Optional[str] = None,
    file_filter: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    symbol_name: Optional[str] = None,
    allow_multiple: bool = False,
    line_filter: Optional[Union[str, int]] = None,
    dry_run: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Internal implementation of smart_patcher."""
    cwd = Path.cwd().resolve()
    base_dir = os.path.abspath(cwd)

    # --- Context Mismatch Guard & Blocker Path Traversal Protection ---
    resolved_path = os.path.abspath(os.path.join(base_dir, target_file))
    if not resolved_path.startswith(base_dir):
        raise ValueError("fatal_context_mismatch")
    if not resolved_path.startswith(base_dir + os.sep) and resolved_path != base_dir:
        raise ValueError("fatal_context_mismatch")

    target_path = Path(resolved_path)

    # --- Filter Checks & File Read ---
    file_content, err = _read_file_and_check_filters(target_path, cwd, folder_filter, file_filter)
    if err:
        return err

    is_crlf = "\r\n" in file_content
    norm_file = file_content.replace("\r\n", "\n")
    norm_search = search_content.replace("\r\n", "\n")
    norm_replace = replace_content.replace("\r\n", "\n")

    file_lines = norm_file.split("\n")

    # --- Boundary Resolution ---
    resolved_start_line, resolved_end_line, err = _resolve_ast_boundaries(
        cwd, target_path, symbol_name, storage_path, start_line, end_line
    )
    if err:
        return err

    # Determine line boundaries (1-indexed inclusive to 0-indexed slice)
    start_idx = (resolved_start_line - 1) if resolved_start_line is not None else 0
    end_idx = (resolved_end_line - 1) if resolved_end_line is not None else len(file_lines) - 1

    start_idx = max(0, min(start_idx, len(file_lines) - 1))
    end_idx = max(start_idx, min(end_idx, len(file_lines) - 1))

    # Slice target content inside scope boundary and verify occurrences
    target_slice, err = _slice_and_check_occurrences(
        file_lines, start_idx, end_idx, norm_search, allow_multiple, symbol_name
    )
    if err:
        return err

    occurrences = target_slice.count(norm_search)

    # --- Line Filter Assertion ---
    err = _apply_line_filters(target_slice, norm_search, start_idx, line_filter)
    if err:
        return err

    # Apply replacement
    patched_slice = target_slice.replace(norm_search, norm_replace)

    before_part = "\n".join(file_lines[:start_idx]) + "\n" if start_idx > 0 else ""
    after_part = "\n" + "\n".join(file_lines[end_idx + 1:]) if end_idx < len(file_lines) - 1 else ""
    patched_file = before_part + patched_slice + after_part

    if is_crlf:
        patched_file = patched_file.replace("\n", "\r\n")

    if dry_run:
        diff_text = generate_diff(file_content, patched_file, target_file)
        output = f"🔍 **[DRY RUN] Diff for patch proposal:**\n\n```diff\n{diff_text}```\n"
        output += f"- Target file: `{target_file}`\n"
        output += f"- Match occurrences inside scope: **{occurrences}**\n"
        if symbol_name:
            output += f"- Scope: AST symbol `{symbol_name}` (lines {start_idx + 1}-{end_idx + 1})\n"
        elif start_line or end_line:
            output += f"- Scope: Line range {start_idx + 1}-{end_idx + 1}\n"

        return {
            "success": True,
            "dryRun": True,
            "message": output,
            "occurrences": occurrences
        }

    # Write patched file
    try:
        target_path.write_text(patched_file, encoding="utf-8")
    except Exception as e:
        return {"error": f"Failed to write patched file: {e}"}

    output = "✅ **File patched successfully!**\n"
    output += f"- Target file: `{target_file}`\n"
    output += f"- Replaced occurrences: **{occurrences}**\n"
    if symbol_name:
        output += f"- Scope: AST symbol `{symbol_name}` (lines {start_idx + 1}-{end_idx + 1})\n"
    elif start_line or end_line:
        output += f"- Scope: Line range {start_idx + 1}-{end_idx + 1}\n"
    if occurrences > 1:
        output += f"⚠️ *Warning:* Replaced {occurrences} identical occurrences.\n"

    return {
        "success": True,
        "dryRun": False,
        "message": output,
        "occurrences": occurrences
    }


def smart_patcher(
    target_file: str,
    search_content: str,
    replace_content: str,
    folder_filter: Optional[str] = None,
    file_filter: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    symbol_name: Optional[str] = None,
    allow_multiple: bool = False,
    line_filter: Optional[Union[str, int]] = None,
    dry_run: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Perform a robust search-and-replace, optionally constrained to an AST symbol or line range."""
    try:
        # Path validation at the entry point to satisfy static taint-analysis engines
        cwd = Path.cwd().resolve()
        base_dir = os.path.abspath(cwd)
        resolved_path = os.path.abspath(os.path.join(base_dir, target_file))
        if not resolved_path.startswith(base_dir):
            raise ValueError("fatal_context_mismatch")
        if not resolved_path.startswith(base_dir + os.sep) and resolved_path != base_dir:
            raise ValueError("fatal_context_mismatch")

        return _smart_patcher_impl(
            target_file=resolved_path,
            search_content=search_content,
            replace_content=replace_content,
            folder_filter=folder_filter,
            file_filter=file_filter,
            start_line=start_line,
            end_line=end_line,
            symbol_name=symbol_name,
            allow_multiple=allow_multiple,
            line_filter=line_filter,
            dry_run=dry_run,
            storage_path=storage_path,
        )
    except ValueError as e:
        if str(e) == "fatal_context_mismatch":
            cwd = Path.cwd().resolve()
            return {
                "error": "fatal_context_mismatch",
                "detail": (
                    f"[FATAL CONTEXT MISMATCH]\n"
                    f"Target file '{target_file}' is outside the active MCP workspace '{cwd}'.\n\n"
                    "To prevent destructive out-of-sync executions:\n"
                    "1. Make sure your active workspace matches the target repository.\n"
                    "2. Ensure the terminal shell is CD'ed to the target repository.\n"
                )
            }
        return {"error": str(e)}

