"""
JSON reporter: produces a machine-readable CI-consumable report.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.models import RunData

logger = logging.getLogger(__name__)


class JSONReporter:
    """Generates a structured JSON report for CI integration."""

    def generate(self, run_data: RunData) -> Path:
        """
        Generate report.json and save to run_data.run_dir.

        Args:
            run_data: Complete run data

        Returns:
            Path to saved report.json
        """
        output_path = run_data.run_dir / "report.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        exec_result = run_data.execution_result

        report: dict[object, object] = {
            "run_id": run_data.run_id,
            "url": run_data.config.url,
            "started_at": run_data.started_at.isoformat(),
            "finished_at": run_data.finished_at.isoformat() if run_data.finished_at else None,
            "duration_seconds": run_data.duration_seconds,
            "playwright_version": run_data.playwright_version,
            "config": {
                "browsers": run_data.config.browsers,
                "max_depth": run_data.config.max_depth,
                "headless": run_data.config.headless,
                "a11y": run_data.config.a11y,
                "visual_diff": run_data.config.visual_diff,
            },
            "summary": {
                "test_count": exec_result.total if exec_result else 0,
                "passed": exec_result.passed if exec_result else 0,
                "failed": exec_result.failed if exec_result else 0,
                "skipped": exec_result.skipped if exec_result else 0,
                "pass_rate": round(run_data.pass_rate, 2),
                "severity_breakdown": run_data.severity_breakdown,
                "pages_crawled": run_data.crawl_result.total_pages if run_data.crawl_result else 0,
                "flows_inferred": len(run_data.flows),
            },
            "accessibility": {
                "wcag_score": run_data.a11y_report.wcag_score if run_data.a11y_report else None,
                "total_violations": run_data.a11y_report.total_violations if run_data.a11y_report else 0,
                "by_impact": run_data.a11y_report.by_impact if run_data.a11y_report else {},
            }
            if run_data.config.a11y
            else None,
            "visual_diff": {
                "pages_compared": run_data.visual_diff_result.total_pages if run_data.visual_diff_result else 0,
                "pages_changed": run_data.visual_diff_result.pages_changed if run_data.visual_diff_result else 0,
            }
            if run_data.config.visual_diff
            else None,
            "test_results": [
                {
                    "name": t.name,
                    "status": t.status,
                    "duration": round(t.duration, 3),
                    "error_message": t.error_message[:500] if t.error_message else None,
                }
                for t in (exec_result.tests if exec_result else [])
            ],
            "failures": [
                {
                    "test_name": f.test_name,
                    "severity": f.severity,
                    "reason": f.reason,
                    "is_likely_flaky": f.is_likely_flaky,
                    "reproduction_steps": f.reproduction_steps,
                    "recommended_fix": f.recommended_fix,
                }
                for f in run_data.scored_failures
            ],
            "report_files": {
                "html": str(run_data.run_dir / "report.html"),
                "json": str(output_path),
                "generated_tests": str(run_data.run_dir / "generated_tests.py"),
                "har": str(run_data.run_dir / "traffic.har"),
                "codegen_script": str(run_data.run_dir / "codegen_script.py"),
            },
        }

        output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        logger.info("JSON report saved: %s", output_path)
        return output_path
