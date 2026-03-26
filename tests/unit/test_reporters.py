"""Unit tests for HTMLReporter and JSONReporter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.models import AgentConfig, RunData
from src.reporting.html_reporter import HTMLReporter, _encode_image
from src.reporting.json_reporter import JSONReporter


@pytest.fixture
def run_data(tmp_path: Path) -> RunData:
    config = AgentConfig(url="http://example.com", run_id="run_test123")
    return RunData(
        run_id="run_test123",
        config=config,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        run_dir=tmp_path,
    )


# ── JSONReporter ──────────────────────────────────────────────────────────────

def test_json_reporter_creates_file(run_data: RunData, tmp_path: Path) -> None:
    path = JSONReporter().generate(run_data)
    assert path.exists()
    assert path.name == "report.json"


def test_json_reporter_structure(run_data: RunData) -> None:
    path = JSONReporter().generate(run_data)
    data = json.loads(path.read_text())
    assert data["run_id"] == "run_test123"
    assert data["url"] == "http://example.com"
    assert "summary" in data
    assert "config" in data
    assert "test_results" in data


def test_json_reporter_summary_defaults(run_data: RunData) -> None:
    path = JSONReporter().generate(run_data)
    data = json.loads(path.read_text())
    summary = data["summary"]
    assert summary["test_count"] == 0
    assert summary["passed"] == 0
    assert summary["failed"] == 0
    assert summary["pass_rate"] == 0.0
    assert summary["pages_crawled"] == 0
    assert summary["flows_inferred"] == 0


def test_json_reporter_no_a11y_when_disabled(run_data: RunData) -> None:
    run_data.config.a11y = False
    path = JSONReporter().generate(run_data)
    data = json.loads(path.read_text())
    assert data["accessibility"] is None


def test_json_reporter_no_visual_diff_when_disabled(run_data: RunData) -> None:
    run_data.config.visual_diff = False
    path = JSONReporter().generate(run_data)
    data = json.loads(path.read_text())
    assert data["visual_diff"] is None


# ── HTMLReporter ──────────────────────────────────────────────────────────────

def test_html_reporter_creates_file(run_data: RunData) -> None:
    path = HTMLReporter().generate(run_data)
    assert path.exists()
    assert path.name == "report.html"


def test_html_reporter_contains_run_id(run_data: RunData) -> None:
    path = HTMLReporter().generate(run_data)
    assert "run_test123" in path.read_text()


def test_html_reporter_contains_url(run_data: RunData) -> None:
    path = HTMLReporter().generate(run_data)
    assert "http://example.com" in path.read_text()


def test_html_reporter_fallback_on_template_error(run_data: RunData, monkeypatch: pytest.MonkeyPatch) -> None:
    reporter = HTMLReporter()
    monkeypatch.setattr(reporter._env, "get_template", lambda _: (_ for _ in ()).throw(Exception("template missing")))
    path = reporter.generate(run_data)
    content = path.read_text()
    assert "template missing" in content
    assert "run_test123" in content


# ── _encode_image ─────────────────────────────────────────────────────────────

def test_encode_image_returns_empty_for_missing(tmp_path: Path) -> None:
    assert _encode_image(tmp_path / "nonexistent.png") == ""


def test_encode_image_returns_empty_for_none() -> None:
    assert _encode_image(None) == ""


def test_encode_image_encodes_file(tmp_path: Path) -> None:
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n")
    result = _encode_image(img)
    assert len(result) > 0
    import base64
    assert base64.b64decode(result) == b"\x89PNG\r\n"
