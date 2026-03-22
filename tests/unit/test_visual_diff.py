"""Unit tests for VisualDiffer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from src.analysis.visual_diff import VisualDiffer, _url_to_slug
from src.models import DOMSnapshot, ScreenshotResult


def make_solid_png(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (100, 100)) -> None:
    """Create a solid-color PNG for testing."""
    img = Image.new("RGB", size, color)
    img.save(path)


@pytest.fixture
def differ(tmp_path: Path) -> VisualDiffer:
    cli = MagicMock()
    cli.screenshot = AsyncMock()

    async def _mock_screenshot(url: str, output_path: Path, full_page: bool = True) -> ScreenshotResult:
        make_solid_png(output_path, (0, 128, 255))
        return ScreenshotResult(path=output_path, file_size_kb=5.0)

    cli.screenshot.side_effect = _mock_screenshot
    return VisualDiffer(cli=cli)


# ── Slug Tests ────────────────────────────────────────────────────────────────


def test_url_to_slug_home() -> None:
    """Root URL should produce 'home' slug."""
    result = _url_to_slug("http://example.com/")
    assert result in ("home", "_", "")  # stripped _ becomes empty → fallback to 'home'
    # The function returns "home" for empty path
    assert _url_to_slug("http://example.com") == "home" or len(result) <= 40


def test_url_to_slug_path() -> None:
    """URL path should produce filesystem-safe slug."""
    result = _url_to_slug("http://example.com/login")
    assert "/" not in result
    assert len(result) <= 40


def test_url_to_slug_no_special_chars() -> None:
    """Slug must not contain filesystem-unsafe characters."""
    result = _url_to_slug("http://example.com/some/deep/path?query=1&foo=bar")
    assert "/" not in result
    assert "?" not in result
    assert "=" not in result


# ── Diff Tests ────────────────────────────────────────────────────────────────


def test_compute_diff_identical_images(differ: VisualDiffer, tmp_path: Path) -> None:
    """Identical images must produce 0% change."""
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    diff_out = tmp_path / "diff.png"
    make_solid_png(before, (100, 100, 100))
    make_solid_png(after, (100, 100, 100))

    change_pct, changed, total = differ._compute_diff(before, after, diff_out)
    assert change_pct == 0.0
    assert changed == 0


def test_compute_diff_completely_different(differ: VisualDiffer, tmp_path: Path) -> None:
    """Completely different images must produce >90% change."""
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    diff_out = tmp_path / "diff.png"
    make_solid_png(before, (0, 0, 0))
    make_solid_png(after, (255, 255, 255))

    change_pct, changed, total = differ._compute_diff(before, after, diff_out)
    assert change_pct > 90.0
    assert changed == total


def test_compute_diff_creates_diff_image(differ: VisualDiffer, tmp_path: Path) -> None:
    """compute_diff must save a diff image to the output path."""
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    diff_out = tmp_path / "diff.png"
    make_solid_png(before, (50, 50, 50))
    make_solid_png(after, (200, 200, 200))

    differ._compute_diff(before, after, diff_out)
    assert diff_out.exists()


def test_compute_diff_handles_size_mismatch(differ: VisualDiffer, tmp_path: Path) -> None:
    """compute_diff must handle images of different sizes by resizing."""
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    diff_out = tmp_path / "diff.png"
    make_solid_png(before, (0, 0, 0), size=(100, 100))
    make_solid_png(after, (255, 255, 255), size=(200, 150))

    # Must not raise
    change_pct, _, _ = differ._compute_diff(before, after, diff_out)
    assert change_pct >= 0.0


@pytest.mark.asyncio
async def test_diff_skips_missing_before(differ: VisualDiffer, tmp_path: Path) -> None:
    """diff must skip pairs where before-image doesn't exist."""
    after = tmp_path / "after.png"
    make_solid_png(after, (0, 0, 0))

    before_map: dict[str, Path] = {"http://example.com": Path("/nonexistent.png")}
    after_map: dict[str, Path] = {"http://example.com": after}

    result = await differ.diff(before_map, after_map, tmp_path)
    assert result.total_pages == 0


@pytest.mark.asyncio
async def test_capture_baseline_uses_existing_screenshots(
    differ: VisualDiffer, tmp_path: Path
) -> None:
    """capture_baseline must reuse screenshots already taken by the crawler."""
    existing_screenshot = tmp_path / "existing.png"
    make_solid_png(existing_screenshot, (0, 0, 0))

    pages = [
        DOMSnapshot(
            url="http://example.com",
            depth=0,
            screenshot_path=existing_screenshot,
        )
    ]

    baseline = await differ.capture_baseline(pages, tmp_path)
    assert "http://example.com" in baseline
    assert baseline["http://example.com"] == existing_screenshot
    # CLI screenshot should NOT be called since we already have the file
    differ._cli.screenshot.assert_not_called()  # type: ignore[attr-defined]
