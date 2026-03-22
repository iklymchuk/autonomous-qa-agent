"""
Severity scorer: batches all test failures into a single OpenAI call for classification.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from src.models import ExecutionResult, ScoredFailure, TestResult

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / ".claude" / "skills" / "ui-tester" / "prompts"


def _load_system_prompt() -> str:
    """Load severity analysis system prompt."""
    path = _PROMPTS_DIR / "analyze_results.md"
    if not path.exists():
        return (
            "You are a senior QA lead. Classify test failures by business severity "
            "(CRITICAL/HIGH/MEDIUM/LOW). Return ONLY a valid JSON array."
        )

    content = path.read_text()
    lines = content.split("\n")
    system_lines: list[str] = []
    in_system = False

    for line in lines:
        if line.strip() == "## System Prompt":
            in_system = True
            continue
        if in_system and line.startswith("## "):
            break
        if in_system:
            system_lines.append(line)

    return "\n".join(system_lines).strip()


class SeverityScorer:
    """
    Classifies test failures by business severity using OpenAI.
    Batches ALL failures into a single API call for efficiency.
    """

    def __init__(
        self,
        client: AsyncOpenAI | None = None,
        model: str = "gpt-4o-mini",
    ) -> None:
        self._client = client or AsyncOpenAI()
        self._model = model

    def _build_failure_payload(
        self,
        failed_tests: list[TestResult],
        generated_tests_path: Path | None = None,
    ) -> list[dict[str, str]]:
        """Build the JSON payload for OpenAI from failed test results."""
        # Try to load generated test source for context
        test_source = ""
        if generated_tests_path and generated_tests_path.exists():
            test_source = generated_tests_path.read_text()

        payload = []
        for test in failed_tests:
            # Extract relevant code snippet
            snippet = ""
            if test_source:
                lines = test_source.split("\n")
                # Find the test function
                start_idx = None
                for i, line in enumerate(lines):
                    if f"def {test.name}" in line or f"async def {test.name}" in line:
                        start_idx = i
                        break
                if start_idx is not None:
                    end_idx = min(start_idx + 30, len(lines))
                    snippet = "\n".join(lines[start_idx:end_idx])

            payload.append(
                {
                    "test_name": test.name,
                    "error_message": test.error_message[:1000],
                    "test_code_snippet": snippet[:800],
                    "page_url": test.page_url,
                    "duration_seconds": str(round(test.duration, 2)),
                }
            )

        return payload

    async def score(
        self,
        execution_result: ExecutionResult,
        target_url: str = "",
        generated_tests_path: Path | None = None,
    ) -> list[ScoredFailure]:
        """
        Classify all test failures by severity in a single batched OpenAI call.

        Args:
            execution_result: Full test execution result
            target_url: Target URL for context in the prompt
            generated_tests_path: Path to generated_tests.py for code context

        Returns:
            List of ScoredFailure with severity + reason + reproduction steps
        """
        failed_tests = [t for t in execution_result.tests if t.status == "failed"]

        if not failed_tests:
            logger.info("No failures to score")
            return []

        logger.info("Scoring %d test failures via OpenAI...", len(failed_tests))

        system_prompt = _load_system_prompt()
        failure_payload = self._build_failure_payload(failed_tests, generated_tests_path)

        user_content = (
            f"Target URL: {target_url or 'unknown'}\n\n"
            f"Failed Tests:\n```json\n{json.dumps(failure_payload, indent=2)}\n```\n\n"
            "Return ONLY a valid JSON array with one entry per failed test in the same order. "
            "No markdown, no explanation."
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )

            raw_output = response.choices[0].message.content or "[]"

            # Strip markdown fences
            raw_output = raw_output.strip()
            if raw_output.startswith("```"):
                lines = raw_output.split("\n")
                raw_output = "\n".join(lines[1:])
                if raw_output.endswith("```"):
                    raw_output = raw_output[:-3]

            scored_data = json.loads(raw_output)
            if not isinstance(scored_data, list):
                raise ValueError("Expected JSON array from severity scorer")

            # Map scored data back to ScoredFailure models
            scored_failures: list[ScoredFailure] = []
            for i, item in enumerate(scored_data):
                # Get original error for reference
                original_error = failed_tests[i].error_message if i < len(failed_tests) else ""

                scored_failures.append(
                    ScoredFailure(
                        test_name=item.get("test_name", f"test_{i}"),
                        severity=item.get("severity", "MEDIUM"),
                        reason=item.get("reason", ""),
                        is_likely_flaky=item.get("is_likely_flaky", False),
                        reproduction_steps=item.get("reproduction_steps", []),
                        recommended_fix=item.get("recommended_fix", ""),
                        original_error=original_error,
                    )
                )

            logger.info(
                "Severity scoring complete: %s",
                {s: sum(1 for f in scored_failures if f.severity == s) for s in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
            )
            return scored_failures

        except (json.JSONDecodeError, ValueError, Exception) as exc:
            logger.warning("Severity scoring failed, assigning MEDIUM to all: %s", exc)
            # Fallback: assign MEDIUM to all failures
            return [
                ScoredFailure(
                    test_name=t.name,
                    severity="MEDIUM",
                    reason="Severity scoring failed — manual triage required.",
                    original_error=t.error_message,
                )
                for t in failed_tests
            ]
