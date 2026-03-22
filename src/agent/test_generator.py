"""
Test generation: sends user flows to OpenAI to generate executable pytest + Playwright code.
Validates syntax via ast.parse() before saving.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from src.models import GeneratedTestSuite, UserFlow

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / ".claude" / "skills" / "ui-tester" / "prompts"


def _load_system_prompt(filename: str) -> str:
    """Load and extract system prompt from a .md prompt file."""
    path = _PROMPTS_DIR / filename
    if not path.exists():
        return (
            "You are an expert test automation engineer. Generate a complete, valid Python "
            "pytest file using Playwright async API. Return ONLY the Python code."
        )

    content = path.read_text()
    lines = content.split("\n")
    system_lines: list[str] = []
    in_system = False

    for line in lines:
        if line.strip() == "## System Prompt":
            in_system = True
            continue
        if in_system and line.startswith("## "):
            break
        if in_system:
            system_lines.append(line)

    return "\n".join(system_lines).strip()


def _extract_python_code(raw: str) -> str:
    """Strip markdown code fences from AI output if present."""
    raw = raw.strip()
    if raw.startswith("```python"):
        lines = raw.split("\n")
        # Remove first line (```python) and last line (```)
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3]
    elif raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3]
    return raw.strip()


def _count_test_functions(code: str) -> int:
    """Count the number of test_ functions in the generated code."""
    try:
        tree = ast.parse(code)
        return sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
        )
    except SyntaxError:
        return 0


def _extract_page_objects(code: str) -> list[str]:
    """Extract Page Object class names from generated code."""
    try:
        tree = ast.parse(code)
        return [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith("Page")
        ]
    except SyntaxError:
        return []


class TestGenerator:
    """
    Generates executable pytest + Playwright test code from user flows via OpenAI.
    Validates syntax with ast.parse() and retries once on syntax error.
    """

    def __init__(self, client: AsyncOpenAI | None = None, model: str = "gpt-4o-mini") -> None:
        self._client = client or AsyncOpenAI()
        self._model = model

    async def _call_openai(self, flows: list[UserFlow], base_url: str, retry_hint: str = "") -> str:
        """Make a single OpenAI call to generate test code."""
        system_prompt = _load_system_prompt("generate_tests.md")

        flows_json = json.dumps([f.model_dump(mode="json") for f in flows], indent=2)

        user_content = (
            f"Base URL: {base_url}\n"
            f"Number of flows: {len(flows)}\n\n"
            f"User Flows:\n```json\n{flows_json}\n```\n\n"
            "Generate a single complete Python pytest file. "
            "Return ONLY the Python code, no markdown fences, no explanation."
        )

        if retry_hint:
            user_content += f"\n\nPREVIOUS ATTEMPT FAILED: {retry_hint}\nFix the syntax error and try again."

        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )

        return response.choices[0].message.content or ""

    async def generate(
        self,
        flows: list[UserFlow],
        base_url: str,
        run_dir: Path = Path("reports"),
    ) -> GeneratedTestSuite:
        """
        Generate an executable pytest file from user flows.

        Args:
            flows: List of inferred user flows
            base_url: Target application URL
            run_dir: Directory to save generated_tests.py

        Returns:
            GeneratedTestSuite with file path, test count, and page objects
        """
        if not flows:
            logger.warning("No flows provided to test generator")
            empty_path = run_dir / "generated_tests.py"
            empty_path.parent.mkdir(parents=True, exist_ok=True)
            empty_path.write_text(
                "# No flows were inferred — no tests generated\n"
                "import pytest\n\n"
                "def test_no_flows():\n"
                "    pytest.skip('No flows were inferred by the AI agent')\n"
            )
            return GeneratedTestSuite(
                file_path=empty_path,
                test_count=0,
                syntax_valid=True,
            )

        logger.info("Generating tests for %d flows...", len(flows))

        raw_code = ""
        generation_errors: list[str] = []
        syntax_valid = False

        for attempt in range(2):
            try:
                raw = await self._call_openai(
                    flows,
                    base_url,
                    retry_hint=generation_errors[-1] if generation_errors else "",
                )
                raw_code = _extract_python_code(raw)

                # Validate syntax
                ast.parse(raw_code)
                syntax_valid = True
                logger.info("Generated test code passes syntax check (attempt %d)", attempt + 1)
                break

            except SyntaxError as exc:
                error_msg = f"SyntaxError at line {exc.lineno}: {exc.msg}"
                logger.warning("Syntax error in generated code (attempt %d): %s", attempt + 1, error_msg)
                generation_errors.append(error_msg)
                if attempt == 1:
                    logger.error("Test generation failed syntax check after 2 attempts")

            except Exception as exc:
                error_msg = str(exc)
                logger.error("OpenAI call failed during test generation: %s", error_msg)
                generation_errors.append(error_msg)
                break

        # Save generated file
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_dir / "generated_tests.py"

        if syntax_valid:
            output_path.write_text(raw_code)
        else:
            # Save broken file with .broken extension for debugging
            broken_path = run_dir / "generated_tests.py.broken"
            broken_path.write_text(raw_code)
            logger.error("Saved broken test file to %s", broken_path)

            # Write a minimal valid file so execution can proceed
            output_path.write_text(
                "# Test generation produced invalid syntax — see generated_tests.py.broken\n"
                "import pytest\n\n"
                "def test_generation_failed():\n"
                f'    pytest.skip("Test generation failed: {generation_errors[-1] if generation_errors else "unknown"}")\n'
            )
            raw_code = output_path.read_text()

        test_count = _count_test_functions(raw_code)
        page_objects = _extract_page_objects(raw_code)

        logger.info(
            "Test suite saved: %s (%d tests, %d page objects)",
            output_path,
            test_count,
            len(page_objects),
        )

        return GeneratedTestSuite(
            file_path=output_path,
            test_count=test_count,
            page_objects=page_objects,
            syntax_valid=syntax_valid,
            generation_errors=generation_errors,
        )