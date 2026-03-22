"""Unit tests for SeverityScorer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analysis.severity_scorer import SeverityScorer
from src.models import ExecutionResult, TestResult


def make_client(response_json: str) -> MagicMock:
    """Mock OpenAI client returning the given JSON."""
    client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = response_json
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    client.chat.completions.create = AsyncMock(return_value=mock_response)
    return client


def make_execution_result(failed_names: list[str], passed_count: int = 1) -> ExecutionResult:
    """Create an ExecutionResult with specified failed tests."""
    tests = [
        TestResult(
            name=name,
            status="failed",
            duration=0.5,
            error_message=f"AssertionError: {name} failed",
        )
        for name in failed_names
    ]
    tests += [
        TestResult(name=f"test_pass_{i}", status="passed", duration=0.1)
        for i in range(passed_count)
    ]
    return ExecutionResult(
        total=len(tests),
        passed=passed_count,
        failed=len(failed_names),
        tests=tests,
    )


SCORED_RESPONSE = json.dumps(
    [
        {
            "test_name": "test_login_flow",
            "severity": "HIGH",
            "reason": "Login is broken — users cannot authenticate",
            "is_likely_flaky": False,
            "reproduction_steps": [
                "Navigate to /login",
                "Enter credentials",
                "Click Submit",
                "Observe error",
            ],
            "recommended_fix": "Check form action URL",
        },
        {
            "test_name": "test_broken_page",
            "severity": "CRITICAL",
            "reason": "JS error crashes the page immediately",
            "is_likely_flaky": False,
            "reproduction_steps": ["Navigate to /broken"],
            "recommended_fix": "Fix the JavaScript error",
        },
    ]
)


@pytest.mark.asyncio
async def test_score_returns_one_result_per_failure() -> None:
    """score must return exactly one ScoredFailure per failed test."""
    client = make_client(SCORED_RESPONSE)
    scorer = SeverityScorer(client=client)

    result = make_execution_result(["test_login_flow", "test_broken_page"])
    scored = await scorer.score(result, target_url="http://localhost:5000")

    assert len(scored) == 2


@pytest.mark.asyncio
async def test_score_batches_into_single_call() -> None:
    """score must make exactly ONE OpenAI call regardless of failure count."""
    client = make_client(SCORED_RESPONSE)
    scorer = SeverityScorer(client=client)

    result = make_execution_result(["test_login_flow", "test_broken_page"])
    await scorer.score(result)

    # Only one API call should have been made
    assert client.chat.completions.create.call_count == 1


@pytest.mark.asyncio
async def test_score_assigns_correct_severity() -> None:
    """score must correctly parse severity from OpenAI response."""
    client = make_client(SCORED_RESPONSE)
    scorer = SeverityScorer(client=client)

    result = make_execution_result(["test_login_flow", "test_broken_page"])
    scored = await scorer.score(result)

    high_failures = [s for s in scored if s.severity == "HIGH"]
    critical_failures = [s for s in scored if s.severity == "CRITICAL"]
    assert len(high_failures) == 1
    assert len(critical_failures) == 1


@pytest.mark.asyncio
async def test_score_returns_empty_for_no_failures() -> None:
    """score must return [] when there are no failed tests."""
    client = make_client("[]")
    scorer = SeverityScorer(client=client)

    result = make_execution_result([])
    scored = await scorer.score(result)

    assert scored == []
    client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_score_fallback_on_openai_failure() -> None:
    """score must assign MEDIUM to all failures when OpenAI call fails."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
    scorer = SeverityScorer(client=client)

    result = make_execution_result(["test_fail_1", "test_fail_2"])
    scored = await scorer.score(result)

    assert len(scored) == 2
    assert all(s.severity == "MEDIUM" for s in scored)


@pytest.mark.asyncio
async def test_score_includes_reproduction_steps() -> None:
    """scored failures must include reproduction steps."""
    client = make_client(SCORED_RESPONSE)
    scorer = SeverityScorer(client=client)

    result = make_execution_result(["test_login_flow", "test_broken_page"])
    scored = await scorer.score(result)

    for sf in scored:
        if sf.severity in ("HIGH", "CRITICAL"):
            assert len(sf.reproduction_steps) > 0


@pytest.mark.asyncio
async def test_score_preserves_original_error() -> None:
    """ScoredFailure must retain the original error message."""
    client = make_client(SCORED_RESPONSE)
    scorer = SeverityScorer(client=client)

    result = make_execution_result(["test_login_flow", "test_broken_page"])
    scored = await scorer.score(result)

    assert all(sf.original_error for sf in scored)
    assert "AssertionError" in scored[0].original_error
