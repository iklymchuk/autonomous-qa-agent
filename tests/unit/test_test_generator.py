"""Unit tests for TestGenerator."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.test_generator import (
    TestGenerator,
    _count_test_functions,
    _extract_page_objects,
    _extract_python_code,
)
from src.models import FlowStep, UserFlow

VALID_PYTHON_CODE = '''
import pytest
from playwright.async_api import async_playwright, Page, expect


class LoginPage:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.email_input = "#email"
        self.password_input = "#password"
        self.submit_btn = "#login-submit"

    async def login(self, email: str, password: str) -> None:
        await self.page.fill(self.email_input, email)
        await self.page.fill(self.password_input, password)
        await self.page.click(self.submit_btn)


@pytest.mark.asyncio
async def test_user_login_flow(page: Page) -> None:
    """User Login with Valid Credentials."""
    login = LoginPage(page)
    await page.goto("http://localhost:5000/login")
    await login.login("admin@test.com", "password123")
    await expect(page.locator("h1")).to_be_visible()


@pytest.mark.asyncio
async def test_navigate_to_dashboard(page: Page) -> None:
    """Navigate from home to dashboard."""
    await page.goto("http://localhost:5000")
    await page.click("#cta-button")
    await expect(page.locator("#dashboard-title")).to_be_visible()
'''


def make_client(code: str) -> MagicMock:
    """Mock OpenAI client that returns given code."""
    client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = code
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    client.chat.completions.create = AsyncMock(return_value=mock_response)
    return client


def make_flows() -> list[UserFlow]:
    return [
        UserFlow(
            name="User Login Flow",
            priority="HIGH",
            steps=[
                FlowStep(action="navigate", selector="", value="/login", description="go to login", expected_result="login page"),
                FlowStep(action="fill", selector="#email", value="admin@test.com", description="enter email", expected_result="email filled"),
                FlowStep(action="assert", selector="h1", value="Login", description="check title", expected_result="title visible"),
            ],
        )
    ]


def test_extract_python_code_strips_fences() -> None:
    """_extract_python_code must strip ```python ... ``` fences."""
    fenced = "```python\nprint('hello')\n```"
    result = _extract_python_code(fenced)
    assert result == "print('hello')"


def test_extract_python_code_no_fences() -> None:
    """_extract_python_code must pass through plain code unchanged."""
    code = "import pytest\n"
    result = _extract_python_code(code)
    assert result == "import pytest"


def test_count_test_functions() -> None:
    """_count_test_functions must count async test_ functions."""
    assert _count_test_functions(VALID_PYTHON_CODE) == 2


def test_extract_page_objects() -> None:
    """_extract_page_objects must find classes ending in Page."""
    objects = _extract_page_objects(VALID_PYTHON_CODE)
    assert "LoginPage" in objects


@pytest.mark.asyncio
async def test_generate_creates_valid_python(tmp_path: Path) -> None:
    """generate must produce a syntactically valid Python file."""
    client = make_client(VALID_PYTHON_CODE)
    generator = TestGenerator(client=client)

    suite = await generator.generate(make_flows(), "http://localhost:5000", tmp_path)

    assert suite.file_path.exists()
    assert suite.syntax_valid
    assert suite.test_count == 2
    # Verify the file actually parses
    ast.parse(suite.file_path.read_text())


@pytest.mark.asyncio
async def test_generate_retries_on_syntax_error(tmp_path: Path) -> None:
    """generate must retry once when code has a syntax error."""
    call_count = 0

    async def mock_create(**kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_message = MagicMock()
        if call_count == 1:
            mock_message.content = "def broken(:\n    pass  # syntax error"
        else:
            mock_message.content = VALID_PYTHON_CODE
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        return mock_response

    client = MagicMock()
    client.chat.completions.create = mock_create

    generator = TestGenerator(client=client)
    suite = await generator.generate(make_flows(), "http://localhost:5000", tmp_path)

    assert call_count == 2
    assert suite.syntax_valid


@pytest.mark.asyncio
async def test_generate_empty_flows_produces_skip_test(tmp_path: Path) -> None:
    """generate with no flows must create a placeholder pytest.skip file."""
    client = make_client(VALID_PYTHON_CODE)
    generator = TestGenerator(client=client)

    suite = await generator.generate([], "http://localhost:5000", tmp_path)

    assert suite.file_path.exists()
    content = suite.file_path.read_text()
    assert "pytest.skip" in content
    assert suite.test_count == 0


@pytest.mark.asyncio
async def test_generate_invalid_twice_saves_broken_file(tmp_path: Path) -> None:
    """When both attempts produce invalid syntax, broken file is saved."""
    client = make_client("def broken(:\n")  # always bad
    generator = TestGenerator(client=client)

    suite = await generator.generate(make_flows(), "http://localhost:5000", tmp_path)

    # Should save .broken file
    broken_path = tmp_path / "generated_tests.py.broken"
    assert broken_path.exists()
    assert not suite.syntax_valid
    # But the main .py file should still be valid (fallback skip test)
    ast.parse(suite.file_path.read_text())
