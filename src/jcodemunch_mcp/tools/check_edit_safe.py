"""Preflight check: is it safe to edit this symbol?

Combines reference analysis, signature impact (external callers),
cyclomatic complexity regression warnings, missing test coverage signals,
and runtime execution history.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import index_status_to_tool_error, resolve_repo

logger = logging.getLogger(__name__)

_TEST_FILE_RE = re.compile(r"(^|[/\\])(test_|tests?[/\\]|_test\.|conftest\.py)", re.IGNORECASE)


def _is_test_file(file_path: str) -> bool:
    return bool(_TEST_FILE_RE.search(file_path or ""))


def _resolve_target(index, symbol: str) -> Optional[dict]:
    """Resolve a symbol id or name to one symbol dict."""
    for sym in index.symbols:
        if sym.get("id") == symbol:
            return sym
    candidates = [s for s in index.symbols if s.get("name") == symbol]
    if not candidates:
        return None
    # Prefer non-import kinds with the largest body
    candidates.sort(key=lambda s: (
        s.get("kind") == "import",
        -int(s.get("byte_length", 0) or 0),
    ))
    return candidates[0]


def _runtime_hits(store: IndexStore, owner: str, name: str, symbol_id: str) -> Optional[int]:
    """Best-effort runtime hit count over the indexed trace window."""
    try:
        import sqlite3  # noqa: PLC0415
        db_path = store._sqlite._db_path(owner, name)
        if not db_path.exists():
            return None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        try:
            cur = conn.execute(
                "SELECT COALESCE(SUM(hit_count), 0) FROM runtime_calls WHERE symbol_id = ?",
                (symbol_id,),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] else None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_edit_safe: runtime hits skipped: %s", exc, exc_info=True)
        return None


def check_edit_safe(
    repo: str,
    symbol: str,
    cross_repo: bool = True,
    include_runtime: bool = True,
    storage_path: Optional[str] = None,
) -> dict:
    """Composite preflight: can this symbol be edited safely?

    Checks signature impact, complexity, missing tests, and runtime usage.
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    target = _resolve_target(index, symbol)
    if target is None:
        return {"error": f"Symbol not found: {symbol}"}

    target_id = target["id"]
    target_name = target.get("name", "")
    target_file = target.get("file", "")

    blockers: list[dict] = []

    # ── Signal 1: External callers (Signature Impact) ──
    external_import_count = 0
    test_import_count = 0
    cross_repo_count = 0
    try:
        from .find_importers import find_importers  # noqa: PLC0415
        importers_out = find_importers(
            repo=f"{owner}/{name}", file_path=target_file,
            cross_repo=cross_repo, storage_path=storage_path,
        )
        for entry in importers_out.get("importers", []) or []:
            if entry.get("cross_repo"):
                cross_repo_count += 1
                blockers.append({
                    "kind": "cross_repo_import",
                    "repo": entry.get("source_repo", ""),
                    "file": entry.get("file", ""),
                    "severity": 4,
                })
            else:
                imp_file = entry.get("file", "")
                if imp_file and imp_file != target_file:
                    if _is_test_file(imp_file):
                        test_import_count += 1
                    else:
                        external_import_count += 1
                        blockers.append({
                            "kind": "external_import",
                            "file": imp_file,
                            "severity": 3,
                        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_edit_safe: find_importers skipped: %s", exc, exc_info=True)

    internal_ref_count = 0
    test_ref_count = 0
    try:
        from .check_references import check_references  # noqa: PLC0415
        ref_out = check_references(
            repo=f"{owner}/{name}", identifier=target_name,
            search_content=True, max_content_results=20,
            storage_path=storage_path,
        )
        for entry in ref_out.get("results", []) or []:
            for ref in entry.get("content_references", []) or []:
                ref_file = ref.get("file", "")
                if not ref_file or ref_file == target_file:
                    continue
                if _is_test_file(ref_file):
                    test_ref_count += 1
                else:
                    internal_ref_count += 1
                    if internal_ref_count <= 3:
                        blockers.append({
                            "kind": "internal_reference",
                            "file": ref_file,
                            "line": ref.get("line", 0),
                            "severity": 2,
                        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_edit_safe: check_references skipped: %s", exc, exc_info=True)

    # ── Signal 2: Complexity Check ──
    cyclomatic = target.get("cyclomatic") or 0
    if cyclomatic > 10:
        blockers.append({
            "kind": "high_complexity",
            "cyclomatic": cyclomatic,
            "severity": 3,
            "detail": f"Complexity is high ({cyclomatic}), prone to regressions."
        })

    # ── Signal 3: Missing test coverage ──
    total_test_callers = test_ref_count + test_import_count
    total_external_callers = external_import_count + internal_ref_count
    has_test_coverage = total_test_callers > 0
    if total_external_callers > 0 and not has_test_coverage:
        blockers.append({
            "kind": "no_test_coverage",
            "severity": 3,
            "detail": "Symbol is actively referenced but has zero unit tests covering it."
        })

    # ── Signal 4: Runtime observed usage ──
    runtime_hits = _runtime_hits(store, owner, name, target_id) if include_runtime else None
    if runtime_hits and runtime_hits > 0:
        blockers.append({
            "kind": "runtime_observed",
            "hit_count": runtime_hits,
            "severity": 4,
        })

    # ── Verdict ──
    if runtime_hits and runtime_hits > 0:
        verdict = "runtime_observed_critical"
    elif cross_repo_count > 0 or external_import_count > 0:
        verdict = "signature_impact_risky"
    elif cyclomatic > 10:
        verdict = "complexity_risky"
    elif total_external_callers > 0 and not has_test_coverage:
        verdict = "no_test_coverage_risky"
    else:
        verdict = "safe_to_edit"

    confidence = 0.95
    if verdict == "signature_impact_risky":
        confidence = 0.60
    elif verdict == "complexity_risky":
        confidence = 0.70
    elif verdict == "no_test_coverage_risky":
        confidence = 0.75
    elif verdict == "runtime_observed_critical":
        confidence = 0.30

    actions = {
        "safe_to_edit": "Code is isolated or simple. Safe to proceed with local edits.",
        "signature_impact_risky": "External files depend on this symbol. Maintain signature/interface compatibility.",
        "complexity_risky": "Symbol is highly complex. Ensure strict regression testing or refactor helper methods.",
        "no_test_coverage": "Write unit tests covering this symbol before modifying its logic.",
        "runtime_observed_critical": "Observed active in production traffic. Change with caution."
    }

    # Rank blockers by severity
    blockers.sort(key=lambda b: -b.get("severity", 0))

    raw_bytes = int(target.get("byte_length", 0) or 0) + 1000
    response_bytes = 800
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="check_edit_safe")

    elapsed = (time.perf_counter() - start) * 1000

    result = {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "target": {
            "symbol_id": target_id,
            "name": target_name,
            "kind": target.get("kind", ""),
            "file": target_file,
            "line": target.get("line", 0),
        },
        "cyclomatic": cyclomatic,
        "has_test_coverage": has_test_coverage,
        "blockers": blockers[:5],
        "recommended_action": actions.get(verdict, "Proceed with edits carefully."),
        "signals": {
            "external_import_count": external_import_count,
            "test_import_count": test_import_count,
            "cross_repo_count": cross_repo_count,
            "internal_ref_count": internal_ref_count,
            "test_ref_count": test_ref_count,
            "cyclomatic": cyclomatic,
        },
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    if runtime_hits is not None:
        result["signals"]["runtime_hits"] = runtime_hits

    return result
