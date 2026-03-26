"""
Visual differ: captures before/after screenshots via CLI and computes pixel diffs with Pillow.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageChops

from src.cli_bridge import PlaywrightCLI
from src.models import DOMSnapshot, VisualDiff, VisualDiffResult

logger = logging.getLogger(__name__)


def _url_to_slug(url: str) -> str:
    """Convert a URL to a filesystem-safe slug."""
    parsed = urlparse(url)
    path = parsed.path or "home"
    slug = re.sub(r"[^\w]", "_", path)[:40].strip("_")
    return slug or "home"


class VisualDiffer:
    """
    Captures before/after page screenshots and computes pixel-level diffs.
    Screenshot capture uses CLI (Layer 1). Diffing uses Pillow.
    """

    def __init__(self, cli: PlaywrightCLI | None = None) -> None:
        self._cli = cli or PlaywrightCLI()

    async def capture_baseline(
        self, pages: list[DOMSnapshot], run_dir: Path
    ) -> dict[str, Path]:
        """
        Capture before-screenshots for all pages.
        Called after initial crawl, before any tests run.

        Args:
            pages: List of crawled pages
            run_dir: Run output directory

        Returns:
            Mapping of URL → screenshot path
        """
        visual_dir = run_dir / "visual"
        visual_dir.mkdir(parents=True, exist_ok=True)

        baseline: dict[str, Path] = {}

        for snapshot in pages:
            if snapshot.error:
                continue
            slug = _url_to_slug(snapshot.url)
            output_path = visual_dir / f"before_{slug}.png"

            # If crawler already captured a screenshot, reuse it
            if snapshot.screenshot_path and snapshot.screenshot_path.exists():
                baseline[snapshot.url] = snapshot.screenshot_path
                logger.debug("Reusing existing screenshot for %s", snapshot.url)
                continue

            result = await self._cli.screenshot(snapshot.url, output_path, full_page=True)
            if result.path.exists():
                baseline[snapshot.url] = result.path
                logger.debug("Captured baseline: %s → %s", snapshot.url, result.path)

        logger.info("Captured %d baseline screenshots", len(baseline))
        return baseline

    async def capture_after(
        self, pages: list[DOMSnapshot], run_dir: Path
    ) -> dict[str, Path]:
        """
        Capture after-screenshots for all pages (post-test run).

        Args:
            pages: List of crawled pages
            run_dir: Run output directory

        Returns:
            Mapping of URL → screenshot path
        """
        visual_dir = run_dir / "visual"
        visual_dir.mkdir(parents=True, exist_ok=True)

        after: dict[str, Path] = {}

        for snapshot in pages:
            if snapshot.error:
                continue
            slug = _url_to_slug(snapshot.url)
            output_path = visual_dir / f"after_{slug}.png"
            result = await self._cli.screenshot(snapshot.url, output_path, full_page=True)
            if result.path.exists():
                after[snapshot.url] = result.path

        return after

    def _compute_diff(
        self,
        before_path: Path,
        after_path: Path,
        diff_path: Path,
    ) -> tuple[float, int, int]:
        """
        Compute pixel diff between two images.

        Args:
            before_path: Before screenshot
            after_path: After screenshot
            diff_path: Output path for diff image

        Returns:
            (change_percentage, changed_pixels, total_pixels)
        """
        try:
            before_img = Image.open(before_path).convert("RGB")
            after_img = Image.open(after_path).convert("RGB")

            # Resize to same dimensions if needed
            if before_img.size != after_img.size:
                after_img = after_img.resize(before_img.size, Image.LANCZOS)

            # Compute diff
            diff_img = ImageChops.difference(before_img, after_img)

            # Count changed pixels (non-black in diff)
            total_pixels = before_img.width * before_img.height
            changed_pixels = sum(
                1 for pixel in diff_img.getdata() if any(c > 10 for c in pixel)
            )

            change_pct = (changed_pixels / total_pixels) * 100 if total_pixels > 0 else 0.0

            # Create highlighted diff image: changed pixels → red

            # Tint changed areas red by compositing
            red_overlay = Image.new("RGB", before_img.size, (255, 0, 0))
            mask = diff_img.convert("L")
            composite = Image.composite(red_overlay, before_img, mask)
            composite.save(diff_path)

            logger.debug(
                "Diff %s: %.2f%% changed (%d/%d pixels)",
                diff_path.name,
                change_pct,
                changed_pixels,
                total_pixels,
            )
            return change_pct, changed_pixels, total_pixels

        except Exception as exc:
            logger.warning("Failed to compute diff %s vs %s: %s", before_path, after_path, exc)
            return 0.0, 0, 0

    async def diff(
        self,
        before: dict[str, Path],
        after: dict[str, Path],
        run_dir: Path,
    ) -> VisualDiffResult:
        """
        Compute pixel diffs between before and after screenshots.

        Args:
            before: URL → before screenshot path mapping
            after: URL → after screenshot path mapping
            run_dir: Run directory to save diff images

        Returns:
            VisualDiffResult with per-page diff details
        """
        visual_dir = run_dir / "visual"
        visual_dir.mkdir(parents=True, exist_ok=True)

        diffs: list[VisualDiff] = []
        pages_changed = 0

        for url, before_path in before.items():
            if url not in after:
                logger.debug("No after-screenshot for %s, skipping diff", url)
                continue

            after_path = after[url]
            if not before_path.exists() or not after_path.exists():
                continue

            slug = _url_to_slug(url)
            diff_path = visual_dir / f"diff_{slug}.png"

            change_pct, changed_pixels, total_pixels = self._compute_diff(
                before_path, after_path, diff_path
            )

            visual_diff = VisualDiff(
                url=url,
                change_pct=round(change_pct, 2),
                diff_path=diff_path if diff_path.exists() else None,
                before_path=before_path,
                after_path=after_path,
                changed_pixels=changed_pixels,
                total_pixels=total_pixels,
            )
            diffs.append(visual_diff)

            if change_pct > 0.5:  # threshold: 0.5% change counts as "changed"
                pages_changed += 1

        logger.info(
            "Visual diff complete: %d pages compared, %d changed", len(diffs), pages_changed
        )

        return VisualDiffResult(
            diffs=diffs,
            total_pages=len(diffs),
            pages_changed=pages_changed,
        )
