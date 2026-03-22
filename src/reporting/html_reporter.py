"""
HTML reporter: renders a self-contained HTML report with base64-embedded images.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.models import RunData

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _encode_image(path: Path | None) -> str:
    """Encode an image file as base64 string. Returns empty string if not found."""
    if path and path.exists():
        try:
            return base64.b64encode(path.read_bytes()).decode("utf-8")
        except Exception as exc:
            logger.debug("Failed to encode image %s: %s", path, exc)
    return ""


class HTMLReporter:
    """
    Generates a self-contained HTML report from RunData.
    All images are base64-encoded inline — no external file references needed.
    """

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )

    def _build_image_maps(
        self, run_data: RunData
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        """
        Build base64-encoded image maps for before/after/diff screenshots.

        Returns:
            (before_images, after_images, diff_images) — URL → base64 string
        """
        before_images: dict[str, str] = {}
        after_images: dict[str, str] = {}
        diff_images: dict[str, str] = {}

        if not run_data.visual_diff_result:
            # Use crawl screenshots as before-images if no visual diff run
            if run_data.crawl_result:
                for page in run_data.crawl_result.pages:
                    if page.screenshot_path:
                        encoded = _encode_image(page.screenshot_path)
                        if encoded:
                            before_images[page.url] = encoded
            return before_images, after_images, diff_images

        for diff in run_data.visual_diff_result.diffs:
            if diff.before_path:
                before_images[diff.url] = _encode_image(diff.before_path)
            if diff.after_path:
                after_images[diff.url] = _encode_image(diff.after_path)
            if diff.diff_path:
                diff_images[diff.url] = _encode_image(diff.diff_path)

        return before_images, after_images, diff_images

    def generate(self, run_data: RunData) -> Path:
        """
        Render the HTML report and save it to run_data.run_dir/report.html.

        Args:
            run_data: Complete run data aggregated from all agent steps

        Returns:
            Path to the saved report.html
        """
        output_path = run_data.run_dir / "report.html"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Load generated test code for display
        test_code = ""
        if run_data.test_suite and run_data.test_suite.file_path.exists():
            test_code = run_data.test_suite.file_path.read_text()

        before_images, after_images, diff_images = self._build_image_maps(run_data)

        try:
            template = self._env.get_template("report.html.j2")
            html_content = template.render(
                run_data=run_data,
                test_code=test_code,
                before_images=before_images,
                after_images=after_images,
                diff_images=diff_images,
            )
        except Exception as exc:
            logger.error("Template rendering failed: %s", exc)
            # Fallback: minimal HTML report
            html_content = self._fallback_html(run_data, str(exc))

        output_path.write_text(html_content, encoding="utf-8")
        size_kb = output_path.stat().st_size / 1024
        logger.info("HTML report saved: %s (%.1f KB)", output_path, size_kb)
        return output_path

    def _fallback_html(self, run_data: RunData, error: str) -> str:
        """Minimal fallback HTML when Jinja2 rendering fails."""
        exec_result = run_data.execution_result
        total = exec_result.total if exec_result else 0
        passed = exec_result.passed if exec_result else 0
        failed = exec_result.failed if exec_result else 0

        return f"""<!DOCTYPE html>
<html><head><title>QA Report {run_data.run_id}</title></head>
<body style="font-family: monospace; background: #111; color: #eee; padding: 24px;">
<h1>QA Report — {run_data.config.url}</h1>
<p>Run ID: {run_data.run_id}</p>
<p>Template rendering error: {error}</p>
<h2>Results</h2>
<p>Total: {total} | Passed: {passed} | Failed: {failed}</p>
</body></html>"""
