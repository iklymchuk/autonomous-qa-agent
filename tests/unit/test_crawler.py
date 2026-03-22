"""Unit tests for SiteCrawler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.crawler import SiteCrawler
from src.models import DOMSnapshot


@pytest.fixture
def crawler() -> SiteCrawler:
    cli = MagicMock()
    cli.screenshot = AsyncMock(return_value=MagicMock(path=Path("/tmp/screen.png")))
    return SiteCrawler(cli=cli)


def test_is_crawlable_same_origin(crawler: SiteCrawler) -> None:
    """Same-origin URLs should be crawlable."""
    assert crawler._is_crawlable(
        "http://example.com/page",
        "http://example.com",
        set(),
        set(),
    )


def test_is_crawlable_different_origin(crawler: SiteCrawler) -> None:
    """Different-origin URLs must not be crawled."""
    assert not crawler._is_crawlable(
        "http://other.com/page",
        "http://example.com",
        set(),
        set(),
    )


def test_is_crawlable_already_visited(crawler: SiteCrawler) -> None:
    """Already-visited URLs must not be re-crawled."""
    url = "http://example.com/page"
    assert not crawler._is_crawlable(url, "http://example.com", set(), {url})


def test_is_crawlable_javascript_link(crawler: SiteCrawler) -> None:
    """javascript: links must be skipped."""
    assert not crawler._is_crawlable(
        "javascript:void(0)", "http://example.com", set(), set()
    )


def test_is_crawlable_static_file(crawler: SiteCrawler) -> None:
    """Static file URLs (.png, .pdf etc.) must be skipped."""
    assert not crawler._is_crawlable(
        "http://example.com/image.png", "http://example.com", set(), set()
    )
    assert not crawler._is_crawlable(
        "http://example.com/doc.pdf", "http://example.com", set(), set()
    )


def test_is_crawlable_robots_txt_disallowed(crawler: SiteCrawler) -> None:
    """Paths disallowed in robots.txt must be skipped."""
    assert not crawler._is_crawlable(
        "http://example.com/admin/secret",
        "http://example.com",
        {"/admin"},
        set(),
    )


def test_normalize_url_relative(crawler: SiteCrawler) -> None:
    """Relative URLs must be resolved against base."""
    result = crawler._normalize_url("/about", "http://example.com")
    assert result == "http://example.com/about"


def test_normalize_url_absolute(crawler: SiteCrawler) -> None:
    """Absolute URLs must be returned unchanged (fragment stripped)."""
    result = crawler._normalize_url("http://example.com/page#section", "http://example.com")
    assert "#section" not in result
    assert "http://example.com/page" in result


def test_normalize_url_protocol_relative(crawler: SiteCrawler) -> None:
    """Protocol-relative URLs (//...) must get the base protocol."""
    result = crawler._normalize_url("//example.com/page", "https://example.com")
    assert result.startswith("https://")


@pytest.mark.asyncio
async def test_crawl_continues_on_page_failure(crawler: SiteCrawler) -> None:
    """Crawl must continue even if a page fails to load."""
    with patch("src.agent.crawler.async_playwright") as mock_pw:
        # Simulate playwright context that fails on first page
        mock_page = MagicMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Connection refused"))
        mock_page.close = AsyncMock()

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_chromium = MagicMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)

        mock_playwright_instance = MagicMock()
        mock_playwright_instance.chromium = mock_chromium
        mock_playwright_instance.__aenter__ = AsyncMock(return_value=mock_playwright_instance)
        mock_playwright_instance.__aexit__ = AsyncMock(return_value=None)

        mock_pw.return_value = mock_playwright_instance

        result = await crawler.crawl("http://example.com", max_depth=1)

    # Should not raise, should record the error
    assert result.errors or result.pages  # something was recorded
    assert isinstance(result.total_pages, int)


def test_url_to_slug_formats() -> None:
    """URL slug generation must produce filesystem-safe strings."""
    from src.analysis.visual_diff import _url_to_slug

    assert "/" not in _url_to_slug("http://example.com/login")
    slug = _url_to_slug("http://example.com/some/deep/path")
    assert len(slug) <= 40
