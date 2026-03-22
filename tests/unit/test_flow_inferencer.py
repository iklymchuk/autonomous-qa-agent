"""Unit tests for FlowInferencer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.flow_inferencer import FlowInferencer
from src.models import CrawlResult, DOMSnapshot, UserFlow


def make_mock_client(response_json: str) -> MagicMock:
    """Create a mock OpenAI client returning the given JSON string."""
    client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = response_json
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=mock_response)
    return client


def make_crawl_result() -> CrawlResult:
    """Minimal crawl result for testing."""
    return CrawlResult(
        base_url="http://example.com",
        pages=[
            DOMSnapshot(
                url="http://example.com",
                title="Home",
                depth=0,
            ),
            DOMSnapshot(
                url="http://example.com/login",
                title="Login",
                depth=1,
            ),
        ],
        total_pages=2,
    )


VALID_FLOWS_JSON = json.dumps(
    [
        {
            "name": "User Login Flow",
            "priority": "HIGH",
            "description": "Login with valid credentials",
            "preconditions": [],
            "steps": [
                {
                    "action": "navigate",
                    "selector": "",
                    "value": "http://example.com/login",
                    "description": "Navigate to login",
                    "expected_result": "Login page visible",
                },
                {
                    "action": "fill",
                    "selector": "#email",
                    "value": "admin@test.com",
                    "description": "Enter email",
                    "expected_result": "Email entered",
                },
                {
                    "action": "assert",
                    "selector": "h1",
                    "value": "Login",
                    "description": "Assert page title",
                    "expected_result": "Title is Login",
                },
            ],
            "expected_outcome": "Dashboard visible",
            "test_data": {"email": "admin@test.com"},
        },
        {
            "name": "Navigation to Dashboard",
            "priority": "MEDIUM",
            "description": "Navigate from home to dashboard",
            "preconditions": [],
            "steps": [
                {
                    "action": "navigate",
                    "selector": "",
                    "value": "http://example.com",
                    "description": "Go home",
                    "expected_result": "Home visible",
                },
                {
                    "action": "click",
                    "selector": "#cta-button",
                    "value": None,
                    "description": "Click CTA",
                    "expected_result": "Dashboard loads",
                },
            ],
            "expected_outcome": "Dashboard shown",
            "test_data": {},
        },
    ]
)


@pytest.mark.asyncio
async def test_infer_returns_flows() -> None:
    """infer must return UserFlow list from valid OpenAI response."""
    client = make_mock_client(VALID_FLOWS_JSON)
    inferencer = FlowInferencer(client=client)

    flows = await inferencer.infer(make_crawl_result(), codegen_script="# empty")

    assert len(flows) == 2
    assert all(isinstance(f, UserFlow) for f in flows)
    assert flows[0].priority == "HIGH"  # sorted HIGH first
    assert flows[0].name == "User Login Flow"


@pytest.mark.asyncio
async def test_infer_sorts_by_priority() -> None:
    """Flows must be sorted HIGH → MEDIUM → LOW."""
    flows_json = json.dumps(
        [
            {
                "name": "Low Priority",
                "priority": "LOW",
                "steps": [{"action": "navigate", "selector": "", "value": "/", "description": "go home", "expected_result": "ok"}],
            },
            {
                "name": "High Priority",
                "priority": "HIGH",
                "steps": [{"action": "click", "selector": "#btn", "value": None, "description": "click", "expected_result": "ok"}],
            },
            {
                "name": "Medium Priority",
                "priority": "MEDIUM",
                "steps": [],
            },
        ]
    )
    client = make_mock_client(flows_json)
    inferencer = FlowInferencer(client=client)
    flows = await inferencer.infer(make_crawl_result())

    priorities = [f.priority for f in flows]
    assert priorities == ["HIGH", "MEDIUM", "LOW"]


@pytest.mark.asyncio
async def test_infer_deduplicates_flows() -> None:
    """Flows with identical action+selector sequences must be deduplicated."""
    step = {
        "action": "click",
        "selector": "#btn",
        "value": None,
        "description": "click button",
        "expected_result": "action done",
    }
    flows_json = json.dumps(
        [
            {"name": "Flow A", "priority": "MEDIUM", "steps": [step]},
            {"name": "Flow B", "priority": "MEDIUM", "steps": [step]},  # duplicate
        ]
    )
    client = make_mock_client(flows_json)
    inferencer = FlowInferencer(client=client)
    flows = await inferencer.infer(make_crawl_result())

    assert len(flows) == 1
    assert flows[0].name == "Flow A"


@pytest.mark.asyncio
async def test_infer_retries_on_invalid_json() -> None:
    """infer must retry once when OpenAI returns invalid JSON."""
    call_count = 0

    async def mock_create(**kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_message = MagicMock()
        if call_count == 1:
            mock_message.content = "NOT VALID JSON {{{"
        else:
            mock_message.content = VALID_FLOWS_JSON
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        return mock_response

    client = MagicMock()
    client.chat.completions.create = mock_create
    inferencer = FlowInferencer(client=client)

    flows = await inferencer.infer(make_crawl_result())
    assert call_count == 2
    assert len(flows) == 2


@pytest.mark.asyncio
async def test_infer_returns_empty_on_openai_failure() -> None:
    """infer must return [] gracefully if OpenAI call raises."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
    inferencer = FlowInferencer(client=client)

    flows = await inferencer.infer(make_crawl_result())
    assert flows == []
