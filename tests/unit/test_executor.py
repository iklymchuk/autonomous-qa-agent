"""Unit tests for TestExecutor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.executor import TestExecutor
from src.models import AgentConfig, GeneratedTestSuite


@pytest.fixture
def executor() -> TestExecutor:
    cli = MagicMock()
    cli.show_trace = AsyncMock()
    return TestExecutor(cli=cli)


@pytest.fixture
def config() -> AgentConfig:
    return AgentConfig(
        url="http://localhost:5000",
        browsers=["chromium"],
        headless=True,
        run_id="run_test",
        reports_dir=Path("/tmp/qa_test_reports"),
    )


@pytest.fixture
def suite(tmp_path: Path) -> GeneratedTestSuite:
    test_file = tmp_path / "generated_tests.py"
    test_file.write_text("import pytest\ndef test_example(): pass\n")
    return GeneratedTestSuite(file_path=test_file, test_count=1, syntax_valid=True)


def make_pytest_json(passed: int = 1, failed: int = 0) -> str:
    tests = []
    for i in range(passed):
        tests.append({"nodeid": f"test_file.py::test_pass_{i}", "outcome": "passed", "duration": 0.1})
    for i in range(failed):
        tests.append({
            "nodeid": f"test_file.py::test_fail_{i}",
            "outcome": "failed",
            "duration": 0.2,
            "call": {"longrepr": f"AssertionError: expected True but got False (test {i})"},
        })
    return json.dumps({"tests": tests})


@pytest.mark.asyncio
async def test_run_parses_passed_tests(
    executor: TestExecutor, config: AgentConfig, suite: GeneratedTestSuite, tmp_path: Path
) -> None:
    """run must correctly count passed tests from pytest JSON report."""
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    json_report = run_dir / "pytest_raw.json"
    json_report.write_text(make_pytest_json(passed=2, failed=0))

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"2 passed", b""))
        proc.returncode = 0
        mock_exec.return_value = proc

        result = await executor.run(suite, config, run_dir)

    assert result.passed == 2
    assert result.failed == 0
    assert result.total == 2


@pytest.mark.asyncio
async def test_run_parses_failed_tests(
    executor: TestExecutor, config: AgentConfig, suite: GeneratedTestSuite, tmp_path: Path
) -> None:
    """run must correctly count failed tests and capture error messages."""
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    json_report = run_dir / "pytest_raw.json"
    json_report.write_text(make_pytest_json(passed=1, failed=2))

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"1 passed, 2 failed", b""))
        proc.returncode = 1
        mock_exec.return_value = proc

        result = await executor.run(suite, config, run_dir)

    assert result.passed == 1
    assert result.failed == 2
    assert result.total == 3
    failed = [t for t in result.tests if t.status == "failed"]
    assert len(failed) == 2
    assert "AssertionError" in failed[0].error_message


@pytest.mark.asyncio
async def test_run_handles_timeout(
    executor: TestExecutor, config: AgentConfig, suite: GeneratedTestSuite, tmp_path: Path
) -> None:
    """run must not crash on asyncio.TimeoutError — returns empty ExecutionResult."""
    import asyncio
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        mock_exec.return_value = proc

        result = await executor.run(suite, config, run_dir)

    assert result.total == 0
    assert "Timeout" in result.stderr


@pytest.mark.asyncio
async def test_run_uses_correct_pytest_flags(
    executor: TestExecutor, config: AgentConfig, suite: GeneratedTestSuite, tmp_path: Path
) -> None:
    """run must include -v --tb=short --json-report flags."""
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()

    captured_args: list[str] = []

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        mock_exec.return_value = proc

        def capture(*args: object, **kwargs: object) -> MagicMock:
            captured_args.extend(str(a) for a in args)
            return proc

        mock_exec.side_effect = capture
        await executor.run(suite, config, run_dir)

    assert "-v" in captured_args
    assert "--tb=short" in captured_args
    assert "--json-report" in captured_args


@pytest.mark.asyncio
async def test_run_does_not_open_trace_without_interactive_flag(
    executor: TestExecutor, suite: GeneratedTestSuite, tmp_path: Path
) -> None:
    """show_trace must NOT be called when interactive=False."""
    config = AgentConfig(
        url="http://localhost:5000",
        browsers=["chromium"],
        interactive=False,
        run_id="run_test",
    )
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    (run_dir / "pytest_raw.json").write_text(make_pytest_json(passed=0, failed=1))

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 1
        mock_exec.return_value = proc
        await executor.run(suite, config, run_dir)

    executor._cli.show_trace.assert_not_called()  # type: ignore[attr-defined]
