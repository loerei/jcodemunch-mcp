"""Highly robust, AST-bounded file editor (smart_patcher)."""

import difflib
import os
import time
from pathlib import Path
from typing import Optional, Union

from ..storage import IndexStore
from ._utils import resolve_repo


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


def smart_patcher(
    targetFile: str,
    searchContent: str,
    replaceContent: str,
    folderFilter: Optional[str] = None,
    fileFilter: Optional[str] = None,
    startLine: Optional[int] = None,
    endLine: Optional[int] = None,
    symbolName: Optional[str] = None,
    allowMultiple: bool = False,
    lineFilter: Optional[Union[str, int]] = None,
    dryRun: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Perform a robust search-and-replace, optionally constrained to an AST symbol or line range."""
    cwd = Path.cwd().resolve()
    target_path = Path(targetFile).resolve()

    # --- Context Mismatch Guard ---
    try:
        target_path.relative_to(cwd)
    except ValueError:
        return {
            "error": "fatal_context_mismatch",
            "detail": (
                f"[FATAL CONTEXT MISMATCH]\n"
                f"Target file '{targetFile}' is outside the active MCP workspace '{cwd}'.\n\n"
                "To prevent destructive out-of-sync executions:\n"
                "1. Make sure your active workspace matches the target repository.\n"
                "2. Ensure the terminal shell is CD'ed to the target repository.\n"
            )
        }

    # --- Filter Checks ---
    if folderFilter:
        resolved_folder = (cwd / folderFilter).resolve()
        try:
            target_path.relative_to(resolved_folder)
        except ValueError:
            return {"error": f"Target file '{targetFile}' does not reside inside folderFilter '{folderFilter}'"}

    if fileFilter:
        file_name = target_path.name
        if fileFilter not in file_name:
            return {"error": f"Target file name '{file_name}' does not match fileFilter '{fileFilter}'"}

    if not target_path.exists():
        return {"error": f"Target file not found at {target_path}"}

    # --- Read original content ---
    try:
        file_content = target_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}

    is_crlf = "\r\n" in file_content
    norm_file = file_content.replace("\r\n", "\n")
    norm_search = searchContent.replace("\r\n", "\n")
    norm_replace = replaceContent.replace("\r\n", "\n")

    file_lines = norm_file.split("\n")

    # --- Boundary Resolution ---
    resolved_start_line = startLine
    resolved_end_line = endLine

    if symbolName:
        try:
            from .resolve_repo import resolve_repo as resolve_repo_fn
            repo_res = resolve_repo_fn(str(cwd), storage_path)
            if not repo_res.get("found"):
                return {"error": f"Workspace at '{cwd}' is not indexed. Call index_folder first to resolve symbols."}
            
            repo_id = repo_res["repo"]
            owner, name = repo_id.split("/", 1)
            
            store = IndexStore(base_path=storage_path)
            index = store.load_index(owner, name)
            if not index:
                return {"error": f"Index for '{repo_id}' could not be loaded."}
                
            # Get relative path of target_file within the source_root
            source_root = Path(repo_res.get("source_root") or index.source_root or cwd).resolve()
            try:
                rel_file_path = str(target_path.relative_to(source_root)).replace("\\", "/")
            except ValueError:
                rel_file_path = str(target_path.relative_to(cwd)).replace("\\", "/")
                
            # Find symbol in index
            matched_symbols = []
            for sym in index.symbols:
                if sym.get("name") == symbolName and sym.get("file") == rel_file_path:
                    matched_symbols.append(sym)
                    
            if not matched_symbols:
                return {"error": f"Symbol '{symbolName}' not found in file '{rel_file_path}'."}
                
            symbol = matched_symbols[0]
            resolved_start_line = symbol["line"]
            resolved_end_line = symbol["end_line"]
        except Exception as e:
            return {"error": f"Error resolving symbolName '{symbolName}': {e}"}

    # Determine line boundaries (1-indexed inclusive to 0-indexed slice)
    start_idx = (resolved_start_line - 1) if resolved_start_line is not None else 0
    end_idx = (resolved_end_line - 1) if resolved_end_line is not None else len(file_lines) - 1

    start_idx = max(0, min(start_idx, len(file_lines) - 1))
    end_idx = max(start_idx, min(end_idx, len(file_lines) - 1))

    # Slice target content inside scope boundary
    target_slice = "\n".join(file_lines[start_idx:end_idx + 1])

    # Occurrences check
    occurrences = target_slice.count(norm_search)
    if occurrences == 0:
        first_lines = "\n".join(norm_search.split("\n")[:3])
        err_msg = f"Error: Search content not found inside the specified scope (lines {start_idx + 1} to {end_idx + 1})!\nFirst 3 lines of search block:\n{first_lines}"
        if symbolName:
            err_msg += f"\nAST Scope: Symbol '{symbolName}' at lines {start_idx + 1}-{end_idx + 1}"
        return {"error": err_msg}

    if not allowMultiple and occurrences > 1:
        return {
            "error": (
                f"Error: Search content occurs {occurrences} times within the specified scope (lines {start_idx + 1} to {end_idx + 1}). "
                "To replace all, set 'allowMultiple: true'."
            )
        }

    # --- Line Filter Assertion ---
    if lineFilter is not None:
        is_numeric = False
        try:
            assert_line_num = int(lineFilter)
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
            if str(lineFilter) not in target_slice:
                return {
                    "error": f"Error: lineFilter assertion failed! The target scope does not contain the substring '{lineFilter}'."
                }

    # Apply replacement
    patched_slice = target_slice.replace(norm_search, norm_replace)

    before_part = "\n".join(file_lines[:start_idx]) + "\n" if start_idx > 0 else ""
    after_part = "\n" + "\n".join(file_lines[end_idx + 1:]) if end_idx < len(file_lines) - 1 else ""
    patched_file = before_part + patched_slice + after_part

    if is_crlf:
        patched_file = patched_file.replace("\n", "\r\n")

    if dryRun:
        diff_text = generate_diff(file_content, patched_file, targetFile)
        output = f"🔍 **[DRY RUN] Diff for patch proposal:**\n\n```diff\n{diff_text}```\n"
        output += f"- Target file: `{targetFile}`\n"
        output += f"- Match occurrences inside scope: **{occurrences}**\n"
        if symbolName:
            output += f"- Scope: AST symbol `{symbolName}` (lines {start_idx + 1}-{end_idx + 1})\n"
        elif startLine or endLine:
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

    output = f"✅ **File patched successfully!**\n"
    output += f"- Target file: `{targetFile}`\n"
    output += f"- Replaced occurrences: **{occurrences}**\n"
    if symbolName:
        output += f"- Scope: AST symbol `{symbolName}` (lines {start_idx + 1}-{end_idx + 1})\n"
    elif startLine or endLine:
        output += f"- Scope: Line range {start_idx + 1}-{end_idx + 1}\n"
    if occurrences > 1:
        output += f"⚠️ *Warning:* Replaced {occurrences} identical occurrences.\n"

    return {
        "success": True,
        "dryRun": False,
        "message": output,
        "occurrences": occurrences
    }
