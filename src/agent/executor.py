"""
Test executor: runs generated pytest files via subprocess and parses results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from src.cli_bridge import PlaywrightCLI
from src.models import AgentConfig, ExecutionResult, GeneratedTestSuite, TestResult

logger = logging.getLogger(__name__)


class TestExecutor:
    """
    Runs generated test suites via pytest subprocess.
    Uses asyncio.create_subprocess_exec (never shell=True).
    """

    def __init__(self, cli: PlaywrightCLI | None = None) -> None:
        self._cli = cli or PlaywrightCLI()

    def _parse_pytest_json(self, json_path: Path) -> list[TestResult]:
        """Parse pytest-json-report output into TestResult objects."""
        if not json_path.exists():
            logger.warning("pytest JSON report not found: %s", json_path)
            return []

        try:
            data = json.loads(json_path.read_text())
            results: list[TestResult] = []

            for test in data.get("tests", []):
                # Extract test name (remove file path prefix)
                name = test.get("nodeid", "unknown").split("::")[-1]

                # Extract error message from call outcome
                error_message = ""
                outcome = test.get("outcome", "unknown")
                call = test.get("call", {})
                if call and call.get("longrepr"):
                    error_message = str(call["longrepr"])[:2000]

                # Build trace path
                trace_path: Path | None = None
                for key in ("setup", "call", "teardown"):
                    phase = test.get(key, {})
                    if phase:
                        for extra in phase.get("extra", []):
                            if extra.get("name") == "trace" and extra.get("url"):
                                trace_path = Path(extra["url"])
                                break

                results.append(
                    TestResult(
                        name=name,
                        status=outcome,
                        duration=test.get("duration", 0.0),
                        error_message=error_message,
                        trace_path=trace_path,
                    )
                )

            return results

        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Failed to parse pytest JSON report: %s", exc)
            return []

    async def run(
        self,
        suite: GeneratedTestSuite,
        config: AgentConfig,
        run_dir: Path | None = None,
    ) -> ExecutionResult:
        """
        Execute the generated test suite via pytest subprocess.

        Args:
            suite: Generated test suite to run
            config: Agent configuration (browsers, headless, etc.)
            run_dir: Directory to save pytest output and traces

        Returns:
            ExecutionResult with per-test outcomes
        """
        if run_dir is None:
            run_dir = config.reports_dir / config.run_id

        traces_dir = run_dir / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        json_report_path = (run_dir / "pytest_raw.json").resolve()

        # Build pytest command
        cmd: list[str] = [
            sys.executable,
            "-m",
            "pytest",
            str(suite.file_path.resolve()),
            "-v",
            "--tb=short",
            "--json-report",
            f"--json-report-file={json_report_path}",
            "--timeout=30",
            "-p", "no:base_url",  # disable pytest-base-url: conflicts with generated base_url fixture
        ]

        if not config.headless:
            cmd.append("--headed")

        logger.info("Running pytest: %s", " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(suite.file_path.parent),
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=300.0  # 5 min max
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            returncode = proc.returncode or 0

            logger.info("pytest exit code: %d", returncode)
            logger.debug("pytest stdout:\n%s", stdout[:3000])

        except TimeoutError:
            logger.error("pytest timed out after 300s")
            stdout = ""
            stderr = "Timeout after 300s"
            returncode = -1

        except Exception as exc:
            logger.error("Failed to run pytest: %s", exc)
            stdout = ""
            stderr = str(exc)
            returncode = -1

        # Parse results
        tests = self._parse_pytest_json(json_report_path)

        # If no JSON report, try to infer from stdout
        if not tests and stdout:
            tests = self._parse_stdout_fallback(stdout)

        total = len(tests)
        passed = sum(1 for t in tests if t.status == "passed")
        failed = sum(1 for t in tests if t.status == "failed")
        skipped = sum(1 for t in tests if t.status == "skipped")
        duration = sum(t.duration for t in tests)

        result = ExecutionResult(
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_seconds=duration,
            tests=tests,
            stdout=stdout,
            stderr=stderr,
            pytest_raw_path=json_report_path,
        )

        logger.info(
            "Test run complete: %d/%d passed, %d failed, %d skipped",
            passed,
            total,
            failed,
            skipped,
        )

        # Open trace viewer if interactive and there are failures
        if config.interactive and result.failed > 0:
            failed_tests = [t for t in tests if t.status == "failed" and t.trace_path]
            if failed_tests and failed_tests[0].trace_path:
                await self._cli.show_trace(failed_tests[0].trace_path)

        return result

    def _parse_stdout_fallback(self, stdout: str) -> list[TestResult]:
        """Fallback parser when pytest JSON report is unavailable."""
        results: list[TestResult] = []
        for line in stdout.split("\n"):
            line = line.strip()
            if " PASSED" in line:
                name = line.split(" PASSED")[0].split("::")[-1].strip()
                results.append(TestResult(name=name, status="passed"))
            elif " FAILED" in line:
                name = line.split(" FAILED")[0].split("::")[-1].strip()
                results.append(TestResult(name=name, status="failed"))
            elif " SKIPPED" in line:
                name = line.split(" SKIPPED")[0].split("::")[-1].strip()
                results.append(TestResult(name=name, status="skipped"))
        return results
