"""
Layer 1: All Playwright CLI interactions live here exclusively.
No other module may shell out to playwright CLI directly.

Uses asyncio.create_subprocess_exec (never shell=True) for all subprocess calls.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.models import CodegenResult, HARResult, InstallResult, ScreenshotResult

logger = logging.getLogger(__name__)


class PlaywrightCLIError(Exception):
    """Raised when a Playwright CLI command exits with non-zero status."""

    def __init__(self, command: str, returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"playwright CLI failed (exit {returncode}): {command}\n{stderr}")


class PlaywrightCLI:
    """
    Wraps all Playwright CLI commands as async Python functions.

    LAYER 1 of the two-layer Playwright architecture. All CLI calls go through
    this class. Python API calls live in crawler.py and executor.py.
    """

    async def _run(
        self, args: list[str], timeout: float = 60.0, input_data: bytes | None = None
    ) -> tuple[int, str, str]:
        """
        Execute a playwright CLI command via asyncio.create_subprocess_exec.

        Args:
            args: CLI arguments (playwright is prepended automatically)
            timeout: Maximum seconds to wait for the process
            input_data: Optional stdin bytes

        Returns:
            (returncode, stdout, stderr) tuple

        Raises:
            PlaywrightCLIError: on non-zero exit code
        """
        full_cmd = ["playwright"] + args
        cmd_str = " ".join(full_cmd)
        logger.debug("Executing: %s", cmd_str)

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if input_data else None,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=input_data),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise PlaywrightCLIError(cmd_str, -1, f"Command timed out after {timeout}s")

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            returncode = proc.returncode or 0

            if returncode != 0:
                logger.error("Command failed (exit %d): %s\nstderr: %s", returncode, cmd_str, stderr)
                raise PlaywrightCLIError(cmd_str, returncode, stderr)

            logger.debug("Command succeeded: %s\nstdout: %s", cmd_str, stdout[:500])
            return returncode, stdout, stderr

        except FileNotFoundError as exc:
            raise PlaywrightCLIError(
                cmd_str, -1, "playwright CLI not found. Run: pip install playwright"
            ) from exc

    async def get_version(self) -> str:
        """
        Capture playwright version string.

        Returns:
            Version string e.g. "Version 1.49.0"
        """
        _, stdout, _ = await self._run(["--version"])
        version = stdout.strip()
        logger.info("Playwright version: %s", version)
        return version

    async def install_browsers(self, browsers: list[str]) -> InstallResult:
        """
        Install specified browser engines.

        Runs `playwright install <browser>` for each browser in the list.

        Args:
            browsers: List of browser names: chromium, firefox, webkit

        Returns:
            InstallResult with installed browser list and version
        """
        installed: list[str] = []
        all_stdout = ""
        all_stderr = ""

        for browser in browsers:
            logger.info("Installing browser: %s", browser)
            _, stdout, stderr = await self._run(
                ["install", browser], timeout=300.0  # browser download can take time
            )
            installed.append(browser)
            all_stdout += stdout
            all_stderr += stderr

        version = await self.get_version()
        return InstallResult(
            browsers_installed=installed,
            version=version,
            stdout=all_stdout,
            stderr=all_stderr,
        )

    async def codegen(self, url: str, output_path: Path, timeout_ms: int = 15000) -> CodegenResult:
        """
        Generate a headless interaction scaffold by crawling the page with the Python API.

        Replaces the interactive `playwright codegen` CLI (which requires a human).
        Visits the URL headlessly, extracts interactive elements, and writes a minimal
        Playwright scaffold script for use as AI flow inference context.

        Args:
            url: Target URL to open
            output_path: Path to write the generated Python test file
            timeout_ms: Page load timeout in ms

        Returns:
            CodegenResult with script path and approximate action count
        """
        from playwright.async_api import async_playwright

        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Running codegen scaffold (headless) for %s → %s", url, output_path)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

                elements = await page.evaluate("""() => {
                    const items = [];
                    document.querySelectorAll('input[type=text], input[type=email], input[type=password], textarea, select').forEach(el => {
                        const id = el.id ? '#' + el.id : null;
                        items.push({type: 'fill', selector: id || el.tagName.toLowerCase(), label: el.placeholder || el.name || ''});
                    });
                    document.querySelectorAll('button, input[type=submit], input[type=button]').forEach(el => {
                        const id = el.id ? '#' + el.id : null;
                        items.push({type: 'click', selector: id || el.tagName.toLowerCase(), label: (el.textContent || el.value || '').trim()});
                    });
                    document.querySelectorAll('a[href]').forEach(el => {
                        const href = el.getAttribute('href');
                        if (href && !href.startsWith('#') && !href.startsWith('http')) {
                            items.push({type: 'goto', selector: href, label: (el.textContent || '').trim()});
                        }
                    });
                    return items.slice(0, 30);
                }""")

                await browser.close()

            lines = [
                "# Auto-generated scaffold (headless crawl)",
                "from playwright.sync_api import Page, expect",
                "",
                f'def test_scaffold(page: Page) -> None:',
                f'    page.goto("{url}")',
            ]
            for el in elements:
                if el["type"] == "fill":
                    lines.append(f'    page.fill("{el["selector"]}", "test")  # {el["label"]}')
                elif el["type"] == "click":
                    lines.append(f'    # page.click("{el["selector"]}")  # {el["label"]}')
                else:
                    lines.append(f'    # page.goto("{url}{el["selector"]}")  # {el["label"]}')

            script = "\n".join(lines) + "\n"
            output_path.write_text(script)

            return CodegenResult(
                script_path=output_path,
                actions_recorded=len(elements),
                stdout="",
                stderr="",
            )

        except Exception as exc:
            logger.warning("Codegen scaffold failed (non-fatal): %s", exc)
            output_path.write_text("# codegen failed — no scaffold available\n")
            return CodegenResult(
                script_path=output_path,
                actions_recorded=0,
                stdout="",
                stderr=str(exc),
            )

    async def save_har(
        self,
        url: str,
        har_path: Path,
        glob: str = "**/api/**",
        timeout: float = 30.0,
    ) -> HARResult:
        """
        Capture HAR traffic file for the target URL using the Playwright Python API.

        Uses headless Chromium with context.record_har_path — no headed browser needed.

        Args:
            url: Target URL
            har_path: Output path for the .har file
            glob: URL glob pattern to filter HAR capture
            timeout: Page load timeout in seconds

        Returns:
            HARResult with HAR path and request counts
        """
        import json

        from playwright.async_api import async_playwright

        har_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Capturing HAR for %s → %s", url, har_path)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    record_har_path=str(har_path),
                    record_har_url_filter=glob,
                )
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
                await context.close()  # flushes HAR to disk
                await browser.close()

            request_count = 0
            api_request_count = 0

            if har_path.exists():
                try:
                    har_data = json.loads(har_path.read_text())
                    entries = har_data.get("log", {}).get("entries", [])
                    request_count = len(entries)
                    api_request_count = sum(
                        1 for e in entries if "/api/" in e.get("request", {}).get("url", "")
                    )
                except (json.JSONDecodeError, KeyError):
                    pass

            return HARResult(
                har_path=har_path,
                request_count=request_count,
                api_request_count=api_request_count,
                stdout="",
                stderr="",
            )

        except Exception as exc:
            logger.warning("HAR capture failed (non-fatal): %s", exc)
            return HARResult(
                har_path=har_path,
                stdout="",
                stderr=str(exc),
            )

    async def screenshot(
        self, url: str, output_path: Path, full_page: bool = True
    ) -> ScreenshotResult:
        """
        Capture a full-page screenshot using playwright CLI.

        Runs `playwright screenshot --full-page <url> <output_path>`

        Args:
            url: Target URL
            output_path: Output PNG path
            full_page: Whether to capture the full scrollable page

        Returns:
            ScreenshotResult with path and file size
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Taking screenshot: %s → %s", url, output_path)

        args = ["screenshot"]
        if full_page:
            args.append("--full-page")
        args.extend([url, str(output_path)])

        try:
            _, stdout, stderr = await self._run(args, timeout=30.0)

            file_size_kb = 0.0
            if output_path.exists():
                file_size_kb = output_path.stat().st_size / 1024

            return ScreenshotResult(
                path=output_path,
                file_size_kb=file_size_kb,
                stdout=stdout,
                stderr=stderr,
            )

        except PlaywrightCLIError as exc:
            logger.warning("Screenshot failed for %s: %s", url, exc)
            return ScreenshotResult(
                path=output_path,
                stdout="",
                stderr=str(exc),
            )

    async def show_trace(self, trace_path: Path) -> None:
        """
        Open the Playwright trace viewer for a trace zip file.

        Only called when config.interactive = True and a trace file exists.
        Runs non-blocking (fire and forget) since it opens a browser window.

        Args:
            trace_path: Path to the trace.zip file
        """
        if not trace_path.exists():
            logger.warning("Trace file not found: %s", trace_path)
            return

        logger.info("Opening trace viewer: %s", trace_path)
        # Fire and forget — trace viewer runs until user closes it
        proc = await asyncio.create_subprocess_exec(
            "playwright",
            "show-trace",
            str(trace_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("Trace viewer opened (PID %d)", proc.pid)
