"""
Accessibility auditor: injects axe-core and runs WCAG 2.1 AA checks.
Uses Playwright Python async API (Layer 2).
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import async_playwright

from src.models import A11yNode, A11yReport, A11yViolation, DOMSnapshot

logger = logging.getLogger(__name__)

# axe-core CDN URL — pinned to stable version
AXE_CORE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"

# WCAG score weights by impact level
IMPACT_WEIGHTS = {
    "critical": 20,
    "serious": 10,
    "moderate": 5,
    "minor": 1,
}


def _calculate_wcag_score(violations: list[A11yViolation]) -> float:
    """
    Calculate a WCAG score from 0–100.

    Formula: 100 - (critical*20 + serious*10 + moderate*5 + minor*1)
    Clamped to [0, 100].
    """
    deductions = sum(IMPACT_WEIGHTS.get(v.impact, 1) for v in violations)
    score = max(0.0, min(100.0, 100.0 - deductions))
    return round(score, 1)


def _parse_violations(raw_violations: list[dict[str, Any]], page_url: str) -> list[A11yViolation]:
    """Parse raw axe-core violations into A11yViolation models."""
    violations: list[A11yViolation] = []

    for item in raw_violations:
        nodes = [
            A11yNode(
                selector=node.get("target", [""])[0] if node.get("target") else "",
                html=node.get("html", "")[:500],
                failure_summary=node.get("failureSummary", "")[:500],
            )
            for node in item.get("nodes", [])[:10]  # cap at 10 nodes per violation
        ]

        violations.append(
            A11yViolation(
                rule_id=item.get("id", "unknown"),
                impact=item.get("impact", "minor"),
                description=item.get("description", ""),
                help_url=item.get("helpUrl", ""),
                nodes=nodes,
                page_url=page_url,
            )
        )

    return violations


class AccessibilityAuditor:
    """
    Runs axe-core accessibility audits via Playwright Python async API.
    Reports WCAG 2.1 AA compliance score per page.
    """

    async def _audit_page(
        self,
        url: str,
        browser_type: str = "chromium",
    ) -> list[A11yViolation]:
        """
        Audit a single page URL using axe-core.

        Args:
            url: Page URL to audit
            browser_type: Browser engine to use

        Returns:
            List of A11yViolation objects found on the page
        """
        async with async_playwright() as pw:
            browser_launcher = getattr(pw, browser_type, pw.chromium)
            browser = await browser_launcher.launch(headless=True)
            try:
                page = await browser.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)

                    # Inject axe-core from CDN
                    await page.add_script_tag(url=AXE_CORE_CDN)

                    # Wait for axe to be available
                    await page.wait_for_function("typeof axe !== 'undefined'", timeout=10000)

                    # Run axe audit
                    raw_results: dict[str, Any] = await page.evaluate(
                        "axe.run().then(r => ({violations: r.violations, passes: r.passes.length}))"
                    )

                    violations = _parse_violations(raw_results.get("violations", []), url)
                    logger.info(
                        "axe-core audit on %s: %d violations, %d passes",
                        url,
                        len(violations),
                        raw_results.get("passes", 0),
                    )
                    return violations

                except Exception as exc:
                    logger.warning("axe-core audit failed for %s: %s", url, exc)
                    return []
                finally:
                    await page.close()
            finally:
                await browser.close()

    async def audit(
        self,
        pages: list[DOMSnapshot],
        browser_type: str = "chromium",
    ) -> A11yReport:
        """
        Run accessibility audits on all crawled pages.

        Args:
            pages: List of DOMSnapshots from the crawler
            browser_type: Browser engine to use

        Returns:
            A11yReport with WCAG score and all violations
        """
        all_violations: list[A11yViolation] = []
        pages_audited = 0

        for snapshot in pages:
            if snapshot.error:
                logger.debug("Skipping accessibility audit for failed page: %s", snapshot.url)
                continue

            logger.info("Auditing accessibility: %s", snapshot.url)
            try:
                violations = await self._audit_page(snapshot.url, browser_type)
                all_violations.extend(violations)
                pages_audited += 1
            except Exception as exc:
                logger.warning("Skipping accessibility audit for %s: %s", snapshot.url, exc)

        # Calculate impact breakdown
        by_impact: dict[str, int] = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
        for v in all_violations:
            impact = v.impact.lower()
            by_impact[impact] = by_impact.get(impact, 0) + 1

        wcag_score = _calculate_wcag_score(all_violations)

        logger.info(
            "Accessibility audit complete: score=%.1f, %d violations across %d pages",
            wcag_score,
            len(all_violations),
            pages_audited,
        )

        return A11yReport(
            wcag_score=wcag_score,
            violations=all_violations,
            pages_audited=pages_audited,
            total_violations=len(all_violations),
            by_impact=by_impact,
        )
