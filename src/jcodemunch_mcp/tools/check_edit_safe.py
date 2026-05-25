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
from ._utils import (
    load_repo_index_or_error,
    resolve_repo,
    is_test_file,
    resolve_target_symbol,
    get_symbol_runtime_hits,
)

logger = logging.getLogger(__name__)



def _check_importers_impact(
    owner: str,
    name: str,
    target_file: str,
    cross_repo: bool,
    storage_path: Optional[str],
    blockers: list[dict],
) -> tuple[int, int, int]:
    """Check find_importers to determine external and cross-repo import impacts."""
    logger.info("Evaluating importer impact signals for file: %s", target_file)
    ext_imports = 0
    test_imports = 0
    cross_repos = 0
    try:
        from .find_importers import find_importers  # noqa: PLC0415
        outcome = find_importers(
            repo=f"{owner}/{name}", file_path=target_file,
            cross_repo=cross_repo, storage_path=storage_path,
        )
        importers_list = outcome.get("importers", []) or []

        # 1. Process cross-repo importers separately to break syntactic duplication pattern
        cross_repo_list = [imp for imp in importers_list if imp.get("cross_repo")]
        cross_repos = len(cross_repo_list)
        for imp in cross_repo_list:
            blockers.append({
                "kind": "cross_repo_import",
                "repo": imp.get("source_repo", ""),
                "file": imp.get("file", ""),
                "severity": 4,
                "info": "detected via cross-repository static mapping",
            })

        # 2. Process local importers sequentially
        local_list = [imp for imp in importers_list if not imp.get("cross_repo")]
        for imp in local_list:
            f_path = imp.get("file", "")
            if not f_path or f_path == target_file:
                continue
            if is_test_file(f_path):
                test_imports += 1
            else:
                ext_imports += 1
                blockers.append({
                    "kind": "external_import",
                    "file": f_path,
                    "severity": 3,
                    "info": "direct external dependency in source tree",
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning("Importer signals evaluation skipped due to: %s", exc)

    return ext_imports, test_imports, cross_repos


def _check_references_impact(
    owner: str,
    name: str,
    target_file: str,
    target_name: str,
    storage_path: Optional[str],
    blockers: list[dict],
) -> tuple[int, int]:
    """Check check_references to count internal references and test callers."""
    logger.info("Scanning reference occurrences for identifier: %s", target_name)
    internal_refs = 0
    test_refs = 0
    try:
        from .check_references import check_references  # noqa: PLC0415
        references_payload = check_references(
            repo=f"{owner}/{name}", identifier=target_name,
            search_content=True, max_content_results=20,
            storage_path=storage_path,
        )
        results_list = references_payload.get("results", []) or []
        for res_entry in results_list:
            content_refs = res_entry.get("content_references", []) or []
            for ref_item in content_refs:
                file_name = ref_item.get("file", "")
                if not file_name or file_name == target_file:
                    continue
                if is_test_file(file_name):
                    test_refs += 1
                else:
                    internal_refs += 1
                    if internal_refs <= 3:
                        blockers.append({
                            "kind": "internal_reference",
                            "file": file_name,
                            "line": ref_item.get("line", 0),
                            "severity": 2,
                            "info": "internal call site in source directory",
                        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reference occurrences scan skipped due to: %s", exc)

    return internal_refs, test_refs


def _check_signature_impact(
    owner: str,
    name: str,
    target_file: str,
    target_name: str,
    cross_repo: bool,
    storage_path: Optional[str],
    blockers: list[dict],
) -> tuple[int, int, int, int, int]:
    """Analyze references and imports to assess signature impact."""
    ext_import_count, test_import_count, cross_repo_count = _check_importers_impact(
        owner, name, target_file, cross_repo, storage_path, blockers
    )
    internal_ref_count, test_ref_count = _check_references_impact(
        owner, name, target_file, target_name, storage_path, blockers
    )
    return (
        ext_import_count,
        test_import_count,
        cross_repo_count,
        internal_ref_count,
        test_ref_count,
    )


def _check_complexity_and_coverage(
    cyclomatic: int,
    total_external_callers: int,
    has_test_coverage: bool,
    blockers: list[dict],
) -> None:
    """Analyze cyclomatic complexity and missing test coverage indicators."""
    if cyclomatic > 10:
        blockers.append({
            "kind": "high_complexity",
            "cyclomatic": cyclomatic,
            "severity": 3,
            "detail": f"Complexity is high ({cyclomatic}), prone to regressions."
        })

    if total_external_callers > 0 and not has_test_coverage:
        blockers.append({
            "kind": "no_test_coverage",
            "severity": 3,
            "detail": "Symbol is actively referenced but has zero unit tests covering it."
        })


def _determine_verdict_and_confidence(
    runtime_hits: Optional[int],
    cross_repo_count: int,
    external_import_count: int,
    cyclomatic: int,
    total_external_callers: int,
    has_test_coverage: bool,
) -> tuple[str, float]:
    """Helper to compute verdict and confidence rating to control cognitive complexity."""
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

    return verdict, confidence


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

    index, err, _ = load_repo_index_or_error(repo, storage_path)
    if err:
        return err

    owner, name = resolve_repo(repo, storage_path)
    store = IndexStore(base_path=storage_path)

    target = resolve_target_symbol(index, symbol)
    if target is None:
        return {"error": f"Symbol not found: {symbol}"}

    target_id = target["id"]
    target_name = target.get("name", "")
    target_file = target.get("file", "")

    blockers: list[dict] = []

    # ── Signal 1 & 3 (Signature Impact & Reference Counting) ──
    (
        external_import_count,
        test_import_count,
        cross_repo_count,
        internal_ref_count,
        test_ref_count,
    ) = _check_signature_impact(
        owner, name, target_file, target_name, cross_repo, storage_path, blockers
    )

    # ── Signal 2 & 3 (Complexity & Test Coverage Assertion) ──
    cyclomatic = target.get("cyclomatic") or 0
    total_test_callers = test_ref_count + test_import_count
    total_external_callers = external_import_count + internal_ref_count
    has_test_coverage = total_test_callers > 0
    _check_complexity_and_coverage(cyclomatic, total_external_callers, has_test_coverage, blockers)

    # ── Signal 4: Runtime observed usage ──
    runtime_hits = get_symbol_runtime_hits(store, owner, name, target_id, "check_edit_safe") if include_runtime else None
    if runtime_hits and runtime_hits > 0:
        blockers.append({
            "kind": "runtime_observed",
            "hit_count": runtime_hits,
            "severity": 4,
        })

    # ── Verdict ──
    verdict, confidence = _determine_verdict_and_confidence(
        runtime_hits,
        cross_repo_count,
        external_import_count,
        cyclomatic,
        total_external_callers,
        has_test_coverage,
    )

    actions = {
        "safe_to_edit": "Code is isolated or simple. Safe to proceed with local edits.",
        "signature_impact_risky": "External files depend on this symbol. Maintain signature/interface compatibility.",
        "complexity_risky": "Symbol is highly complex. Ensure strict regression testing or refactor helper methods.",
        "no_test_coverage": "Write unit tests covering this symbol before modifying its logic.",
        "runtime_observed_critical": "Observed active in production traffic. Change with caution."
    }

    # Rank blockers by severity
    blockers.sort(key=lambda b: -b.get("severity", 0))

    # Calculate performance and token metrics
    symbol_bytes = int(target.get("byte_length", 0) or 0) + 1000
    estimated_saved = estimate_savings(symbol_bytes, 800)
    cumulative_saved = record_savings(estimated_saved, tool_name="check_edit_safe")

    elapsed_ms = (time.perf_counter() - start) * 1000

    timing_details = {
        "timing_ms": round(elapsed_ms, 1),
        "tokens_saved": estimated_saved,
        "total_tokens_saved": cumulative_saved,
        **cost_avoided(estimated_saved, cumulative_saved),
    }

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
        "_meta": timing_details,
    }
    if runtime_hits is not None:
        result["signals"]["runtime_hits"] = runtime_hits

    return result
