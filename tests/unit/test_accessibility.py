"""Unit tests for AccessibilityAuditor."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.analysis.accessibility import AccessibilityAuditor, _calculate_wcag_score
from src.models import A11yViolation, DOMSnapshot


def make_violation(impact: str) -> A11yViolation:
    return A11yViolation(rule_id=f"rule-{impact}", impact=impact, description=f"{impact} issue")


# ── WCAG Score Formula Tests ──────────────────────────────────────────────────


def test_wcag_score_no_violations() -> None:
    """No violations → perfect score of 100."""
    assert _calculate_wcag_score([]) == 100.0


def test_wcag_score_one_critical() -> None:
    """One critical violation → 100 - 20 = 80."""
    assert _calculate_wcag_score([make_violation("critical")]) == 80.0


def test_wcag_score_mixed_violations() -> None:
    """Mixed violations → correct weighted deduction."""
    violations = [
        make_violation("critical"),   # -20
        make_violation("serious"),    # -10
        make_violation("moderate"),   # -5
        make_violation("minor"),      # -1
    ]
    # 100 - (20 + 10 + 5 + 1) = 64
    assert _calculate_wcag_score(violations) == 64.0


def test_wcag_score_clamped_to_zero() -> None:
    """Score must never go below 0."""
    violations = [make_violation("critical")] * 10  # -200 total
    assert _calculate_wcag_score(violations) == 0.0


def test_wcag_score_clamped_to_hundred() -> None:
    """Score must never exceed 100."""
    assert _calculate_wcag_score([]) == 100.0


# ── Auditor Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_skips_pages_with_errors() -> None:
    """Auditor must skip pages that had crawl errors."""
    auditor = AccessibilityAuditor()
    pages = [DOMSnapshot(url="http://example.com/broken", depth=0, error="timeout")]

    with patch.object(auditor, "_audit_page", new_callable=AsyncMock) as mock_audit:
        result = await auditor.audit(pages)

    mock_audit.assert_not_called()
    assert result.pages_audited == 0


@pytest.mark.asyncio
async def test_audit_aggregates_violations() -> None:
    """Auditor must aggregate violations from all pages."""
    auditor = AccessibilityAuditor()
    pages = [
        DOMSnapshot(url="http://example.com/", depth=0),
        DOMSnapshot(url="http://example.com/login", depth=1),
    ]

    async def mock_audit_page(url: str, browser_type: str = "chromium") -> list[A11yViolation]:
        return [make_violation("critical"), make_violation("minor")]

    with patch.object(auditor, "_audit_page", side_effect=mock_audit_page):
        result = await auditor.audit(pages)

    assert result.pages_audited == 2
    assert result.total_violations == 4
    # 100 - (critical*20 + minor*1) * 2 pages
    # = 100 - (20+1)*2 = 100 - 42 = 58
    assert result.wcag_score == 58.0


@pytest.mark.asyncio
async def test_audit_continues_on_page_failure() -> None:
    """Auditor must not crash if a single page audit fails."""
    auditor = AccessibilityAuditor()
    pages = [
        DOMSnapshot(url="http://example.com/good", depth=0),
        DOMSnapshot(url="http://example.com/bad", depth=0),
    ]

    call_count = 0

    async def mock_audit_page(url: str, browser_type: str = "chromium") -> list[A11yViolation]:
        nonlocal call_count
        call_count += 1
        if "bad" in url:
            raise Exception("axe injection failed")
        return []

    with patch.object(auditor, "_audit_page", side_effect=mock_audit_page):
        result = await auditor.audit(pages)

    # Auditor should still report on the good page
    assert result.pages_audited >= 1


@pytest.mark.asyncio
async def test_audit_by_impact_breakdown() -> None:
    """Auditor must correctly populate by_impact dict."""
    auditor = AccessibilityAuditor()
    pages = [DOMSnapshot(url="http://example.com/", depth=0)]

    async def mock_audit_page(url: str, browser_type: str = "chromium") -> list[A11yViolation]:
        return [
            make_violation("critical"),
            make_violation("critical"),
            make_violation("serious"),
        ]

    with patch.object(auditor, "_audit_page", side_effect=mock_audit_page):
        result = await auditor.audit(pages)

    assert result.by_impact["critical"] == 2
    assert result.by_impact["serious"] == 1
    assert result.by_impact["moderate"] == 0
