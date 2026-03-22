"""Unit tests for PlaywrightCLI — verifies exact CLI command strings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli_bridge import PlaywrightCLI, PlaywrightCLIError


@pytest.fixture
def cli() -> PlaywrightCLI:
    return PlaywrightCLI()


def make_mock_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    """Create a mock asyncio subprocess."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.returncode = returncode
    proc.pid = 12345
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_get_version_calls_correct_command(cli: PlaywrightCLI) -> None:
    """get_version must call `playwright --version`."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = make_mock_proc(stdout="Version 1.49.0")
        version = await cli.get_version()

    mock_exec.assert_called_once()
    call_args = mock_exec.call_args[0]
    assert call_args[0] == "playwright"
    assert "--version" in call_args
    assert version == "Version 1.49.0"


@pytest.mark.asyncio
async def test_install_browsers_calls_each_browser(cli: PlaywrightCLI) -> None:
    """install_browsers must call `playwright install <browser>` for each browser."""
    call_args_list: list[tuple] = []

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        async def capture_call(*args: object, **kwargs: object) -> MagicMock:
            call_args_list.append(args)
            return make_mock_proc(stdout="Version 1.49.0")

        mock_exec.side_effect = capture_call

        result = await cli.install_browsers(["chromium", "firefox"])

    # Should have called: install chromium, install firefox, --version
    install_calls = [args for args in call_args_list if "install" in args]
    assert len(install_calls) == 2
    all_args = [str(a) for args in install_calls for a in args]
    assert "chromium" in all_args
    assert "firefox" in all_args
    assert "chromium" in result.browsers_installed
    assert "firefox" in result.browsers_installed


@pytest.mark.asyncio
async def test_codegen_calls_correct_command(cli: PlaywrightCLI, tmp_path: Path) -> None:
    """codegen must call `playwright codegen <url> --output <path> --timeout 15000`."""
    output = tmp_path / "codegen.py"

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = make_mock_proc()
        await cli.codegen("http://example.com", output)

    call_args = mock_exec.call_args[0]
    assert call_args[0] == "playwright"
    assert "codegen" in call_args
    assert "http://example.com" in call_args
    assert "--output" in call_args
    assert "--timeout" in call_args
    assert "15000" in call_args


@pytest.mark.asyncio
async def test_codegen_handles_failure_gracefully(cli: PlaywrightCLI, tmp_path: Path) -> None:
    """codegen must not raise on failure — returns CodegenResult with empty script."""
    output = tmp_path / "codegen.py"

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = make_mock_proc(stderr="timeout", returncode=1)
        result = await cli.codegen("http://example.com", output)

    assert result.script_path == output
    assert result.actions_recorded == 0
    assert output.exists()


@pytest.mark.asyncio
async def test_save_har_calls_correct_command(cli: PlaywrightCLI, tmp_path: Path) -> None:
    """save_har must call playwright open with --save-har flag."""
    har_path = tmp_path / "traffic.har"

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = make_mock_proc()
        await cli.save_har("http://example.com", har_path)

    call_args = mock_exec.call_args[0]
    assert "open" in call_args
    assert any("--save-har=" in str(a) for a in call_args)
    assert "http://example.com" in call_args


@pytest.mark.asyncio
async def test_screenshot_calls_correct_command(cli: PlaywrightCLI, tmp_path: Path) -> None:
    """screenshot must call `playwright screenshot --full-page <url> <output>`."""
    output = tmp_path / "screen.png"

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = make_mock_proc()
        output.write_bytes(b"fake png data")  # simulate created file
        await cli.screenshot("http://example.com", output)

    call_args = mock_exec.call_args[0]
    assert "screenshot" in call_args
    assert "--full-page" in call_args
    assert "http://example.com" in call_args
    assert str(output) in call_args


@pytest.mark.asyncio
async def test_screenshot_without_full_page(cli: PlaywrightCLI, tmp_path: Path) -> None:
    """screenshot with full_page=False must not include --full-page flag."""
    output = tmp_path / "screen.png"

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = make_mock_proc()
        await cli.screenshot("http://example.com", output, full_page=False)

    call_args = mock_exec.call_args[0]
    assert "--full-page" not in call_args


@pytest.mark.asyncio
async def test_nonzero_exit_raises_error(cli: PlaywrightCLI) -> None:
    """Non-zero exit code must raise PlaywrightCLIError with stderr."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = make_mock_proc(
            stderr="Browser not found", returncode=1
        )
        with pytest.raises(PlaywrightCLIError) as exc_info:
            await cli.get_version()

    assert "Browser not found" in str(exc_info.value)
    assert exc_info.value.returncode == 1


@pytest.mark.asyncio
async def test_playwright_not_found_raises_error(cli: PlaywrightCLI) -> None:
    """FileNotFoundError when playwright is not installed must raise PlaywrightCLIError."""
    with patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        side_effect=FileNotFoundError("playwright not found"),
    ):
        with pytest.raises(PlaywrightCLIError) as exc_info:
            await cli.get_version()

    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_show_trace_fires_and_forgets(cli: PlaywrightCLI, tmp_path: Path) -> None:
    """show_trace must call playwright show-trace and not raise."""
    trace_path = tmp_path / "trace.zip"
    trace_path.write_bytes(b"fake trace")

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = MagicMock()
        mock_proc.pid = 99
        mock_exec.return_value = mock_proc
        await cli.show_trace(trace_path)

    call_args = mock_exec.call_args[0]
    assert "show-trace" in call_args
    assert str(trace_path) in call_args


@pytest.mark.asyncio
async def test_show_trace_skips_missing_file(cli: PlaywrightCLI, tmp_path: Path) -> None:
    """show_trace must not raise if trace file doesn't exist."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        await cli.show_trace(tmp_path / "nonexistent.zip")

    mock_exec.assert_not_called()
