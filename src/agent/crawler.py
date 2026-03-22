"""
Layer 2: BFS web crawler using Playwright Python async API.
Extracts structured DOM snapshots from each discovered page.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Page, async_playwright

from src.cli_bridge import PlaywrightCLI
from src.models import (
    ButtonElement,
    CrawlResult,
    DOMSnapshot,
    FormElement,
    FormField,
    HeadingElement,
    InputElement,
    LinkElement,
    SelectElement,
)

logger = logging.getLogger(__name__)

# JavaScript to extract a structured DOM snapshot from the current page
_DOM_EXTRACT_JS = """
() => {
    const safeText = (el) => (el ? el.textContent.trim().substring(0, 200) : '');
    const safeAttr = (el, attr) => (el ? (el.getAttribute(attr) || '') : '');

    // Inputs
    const inputs = Array.from(document.querySelectorAll('input, textarea')).map(el => {
        const labelEl = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
        return {
            selector: el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : el.tagName.toLowerCase()),
            input_type: el.type || 'text',
            placeholder: el.placeholder || '',
            label: safeText(labelEl),
            required: el.required || false,
            name: el.name || ''
        };
    });

    // Buttons
    const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"]')).map(el => ({
        selector: el.id ? '#' + el.id : (el.type ? '[type="' + el.type + '"]' : 'button'),
        text: safeText(el) || el.value || '',
        button_type: el.type || 'button'
    }));

    // Links
    const links = Array.from(document.querySelectorAll('a[href]')).map(el => {
        const href = el.href || '';
        const currentOrigin = window.location.origin;
        return {
            selector: el.id ? '#' + el.id : 'a[href="' + el.getAttribute('href') + '"]',
            href: href,
            text: safeText(el),
            is_same_origin: href.startsWith(currentOrigin) || href.startsWith('/')
        };
    });

    // Forms
    const forms = Array.from(document.querySelectorAll('form')).map(form => {
        const fields = Array.from(form.querySelectorAll('input, textarea, select')).map(el => {
            const labelEl = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
            return {
                selector: el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : el.tagName.toLowerCase()),
                field_type: el.type || el.tagName.toLowerCase(),
                name: el.name || '',
                label: safeText(labelEl),
                required: el.required || false
            };
        });
        return {
            selector: form.id ? '#' + form.id : 'form',
            action: form.action || '',
            method: form.method || 'get',
            fields: fields
        };
    });

    // Selects
    const selects = Array.from(document.querySelectorAll('select')).map(el => ({
        selector: el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : 'select'),
        options: Array.from(el.options).map(o => o.text),
        name: el.name || ''
    }));

    // Headings
    const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6')).map(el => ({
        level: parseInt(el.tagName[1]),
        text: safeText(el)
    }));

    return {
        title: document.title,
        inputs: inputs,
        buttons: buttons,
        links: links,
        forms: forms,
        selects: selects,
        headings: headings
    };
}
"""


class SiteCrawler:
    """
    BFS crawler that uses Playwright Python async API (Layer 2).
    For each page, calls cli_bridge.screenshot() to capture before-images (Layer 1).
    """

    def __init__(self, cli: PlaywrightCLI | None = None) -> None:
        self._cli = cli or PlaywrightCLI()

    async def _check_robots_txt(self, base_url: str) -> set[str]:
        """
        Parse robots.txt and return set of disallowed paths.
        Returns empty set if robots.txt is not available.
        """
        disallowed: set[str] = set()
        robots_url = urljoin(base_url, "/robots.txt")

        try:
            import urllib.request

            with urllib.request.urlopen(robots_url, timeout=5) as resp:
                content = resp.read().decode("utf-8", errors="replace")

            user_agent_applies = False
            for line in content.splitlines():
                line = line.strip()
                if line.lower().startswith("user-agent:"):
                    agent = line.split(":", 1)[1].strip()
                    user_agent_applies = agent in ("*", "playwright")
                elif user_agent_applies and line.lower().startswith("disallow:"):
                    path = line.split(":", 1)[1].strip()
                    if path:
                        disallowed.add(path)

        except Exception as exc:
            logger.debug("Could not fetch robots.txt: %s", exc)

        return disallowed

    def _is_crawlable(
        self,
        url: str,
        base_url: str,
        disallowed_paths: set[str],
        visited: set[str],
    ) -> bool:
        """Check if a URL should be crawled."""
        if url in visited:
            return False

        parsed = urlparse(url)
        base_parsed = urlparse(base_url)

        # Same origin check
        if parsed.netloc and parsed.netloc != base_parsed.netloc:
            return False

        # Skip non-http schemes
        if parsed.scheme and parsed.scheme not in ("http", "https", ""):
            return False

        # Skip anchors and javascript
        if url.startswith("#") or url.startswith("javascript:"):
            return False

        # Skip file extensions that aren't pages
        skip_extensions = {".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico", ".css", ".js"}
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in skip_extensions):
            return False

        # Check robots.txt
        for disallowed in disallowed_paths:
            if parsed.path.startswith(disallowed):
                logger.debug("Skipping %s (robots.txt)", url)
                return False

        return True

    def _normalize_url(self, url: str, base_url: str) -> str:
        """Normalize a potentially relative URL to absolute."""
        if url.startswith("//"):
            parsed_base = urlparse(base_url)
            return f"{parsed_base.scheme}:{url}"
        if not url.startswith("http"):
            return urljoin(base_url, url)
        # Remove fragment
        parsed = urlparse(url)
        return parsed._replace(fragment="").geturl()

    async def _extract_snapshot(
        self, page: Page, url: str, depth: int, run_dir: Path | None = None
    ) -> DOMSnapshot:
        """Extract a structured DOM snapshot from the current page."""
        try:
            raw = await page.evaluate(_DOM_EXTRACT_JS)

            # Convert raw dicts to model objects
            inputs = [InputElement(**i) for i in raw.get("inputs", [])]
            buttons = [ButtonElement(**b) for b in raw.get("buttons", [])]
            links = [LinkElement(**lnk) for lnk in raw.get("links", [])]
            forms = [
                FormElement(
                    selector=f["selector"],
                    action=f.get("action", ""),
                    method=f.get("method", "get"),
                    fields=[FormField(**fld) for fld in f.get("fields", [])],
                )
                for f in raw.get("forms", [])
            ]
            selects = [SelectElement(**s) for s in raw.get("selects", [])]
            headings = [HeadingElement(**h) for h in raw.get("headings", [])]

            snapshot = DOMSnapshot(
                url=url,
                title=raw.get("title", ""),
                depth=depth,
                inputs=inputs,
                buttons=buttons,
                links=links,
                forms=forms,
                selects=selects,
                headings=headings,
            )

            # Capture screenshot via CLI (Layer 1)
            if run_dir:
                slug = re.sub(r"[^\w]", "_", urlparse(url).path or "home")[:40]
                screenshot_path = run_dir / "visual" / f"before_{slug}.png"
                result = await self._cli.screenshot(url, screenshot_path, full_page=True)
                if result.path.exists():
                    snapshot.screenshot_path = result.path

            return snapshot

        except Exception as exc:
            logger.error("Failed to extract snapshot from %s: %s", url, exc)
            return DOMSnapshot(url=url, depth=depth, error=str(exc))

    async def crawl(
        self,
        url: str,
        max_depth: int = 3,
        browser_type: str = "chromium",
        run_dir: Path | None = None,
        headless: bool = True,
    ) -> CrawlResult:
        """
        BFS crawl from root URL, extracting DOMSnapshot for each page.

        Uses Playwright Python async API (Layer 2).
        Calls cli_bridge.screenshot() for each page (Layer 1).

        Args:
            url: Root URL to start crawl from
            max_depth: Maximum BFS depth
            browser_type: Browser engine: chromium|firefox|webkit
            run_dir: Run directory for screenshots
            headless: Whether to run browser in headless mode

        Returns:
            CrawlResult with all discovered page snapshots
        """
        start_time = time.time()
        pages: list[DOMSnapshot] = []
        errors: list[str] = []
        visited: set[str] = set()

        disallowed_paths = await self._check_robots_txt(url)
        logger.info("Starting BFS crawl: %s (max_depth=%d, browser=%s)", url, max_depth, browser_type)

        async with async_playwright() as pw:
            browser_launcher = getattr(pw, browser_type, pw.chromium)
            browser: Browser = await browser_launcher.launch(headless=headless)

            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    ignore_https_errors=True,
                )
                # BFS queue: (url, depth)
                queue: deque[tuple[str, int]] = deque([(url, 0)])
                visited.add(url)

                while queue:
                    current_url, depth = queue.popleft()
                    logger.info("Crawling [depth=%d]: %s", depth, current_url)

                    page = await context.new_page()
                    try:
                        await page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(0.5)  # brief settle time

                        snapshot = await self._extract_snapshot(page, current_url, depth, run_dir)
                        pages.append(snapshot)

                        # Enqueue same-origin links for next depth level
                        if depth < max_depth:
                            for link in snapshot.links:
                                if not link.href:
                                    continue
                                normalized = self._normalize_url(link.href, url)
                                if self._is_crawlable(normalized, url, disallowed_paths, visited):
                                    visited.add(normalized)
                                    queue.append((normalized, depth + 1))

                    except Exception as exc:
                        error_msg = f"Failed to crawl {current_url}: {exc}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        pages.append(DOMSnapshot(url=current_url, depth=depth, error=str(exc)))
                    finally:
                        await page.close()

                await context.close()
            finally:
                await browser.close()

        duration = time.time() - start_time
        logger.info(
            "Crawl complete: %d pages in %.1fs (%d errors)", len(pages), duration, len(errors)
        )

        return CrawlResult(
            base_url=url,
            pages=pages,
            total_pages=len(pages),
            errors=errors,
            duration_seconds=duration,
        )
