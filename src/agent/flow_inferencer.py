"""
Flow inference: sends DOM snapshots to OpenAI GPT-4o to infer realistic user flows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from src.models import CrawlResult, FlowStep, UserFlow

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / ".claude" / "skills" / "ui-tester" / "prompts"


def _load_prompt(name: str) -> str:
    """Load a prompt file from the prompts directory."""
    path = _PROMPTS_DIR / name
    if path.exists():
        return path.read_text()
    logger.warning("Prompt file not found: %s", path)
    return ""


def _extract_system_prompt(prompt_content: str) -> str:
    """Extract the system prompt section from a prompt .md file."""
    lines = prompt_content.split("\n")
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


class FlowInferencer:
    """
    Sends crawl results to OpenAI to infer structured user flows.
    Uses gpt-4o-mini at temperature=0 for deterministic, reproducible outputs.
    """

    def __init__(self, client: AsyncOpenAI | None = None, model: str = "gpt-4o-mini") -> None:
        self._client = client or AsyncOpenAI()
        self._model = model

    def _deduplicate_flows(self, flows: list[UserFlow]) -> list[UserFlow]:
        """
        Remove semantically equivalent flows (same selectors + actions).
        Keeps the first occurrence when duplicates are found.
        """
        seen_signatures: set[str] = set()
        unique: list[UserFlow] = []

        for flow in flows:
            # Build a signature from the sequence of action+selector pairs
            sig_parts = [f"{s.action}:{s.selector}" for s in flow.steps]
            signature = "|".join(sig_parts)

            if signature not in seen_signatures:
                seen_signatures.add(signature)
                unique.append(flow)
            else:
                logger.debug("Deduplicating flow '%s' (duplicate signature)", flow.name)

        logger.info("Deduplication: %d → %d unique flows", len(flows), len(unique))
        return unique

    def _sort_by_priority(self, flows: list[UserFlow]) -> list[UserFlow]:
        """Sort flows HIGH → MEDIUM → LOW."""
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        return sorted(flows, key=lambda f: priority_order.get(f.priority, 1))

    def _parse_flows(self, raw_json: str) -> list[UserFlow]:
        """Parse raw JSON string into list of UserFlow objects."""
        # Strip markdown code fences if present
        raw_json = raw_json.strip()
        if raw_json.startswith("```"):
            lines = raw_json.split("\n")
            raw_json = "\n".join(lines[1:-1]) if len(lines) > 2 else raw_json

        data = json.loads(raw_json)

        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array of flows, got: {type(data)}")

        flows: list[UserFlow] = []
        for item in data:
            steps = [FlowStep(**s) for s in item.get("steps", [])]
            flow = UserFlow(
                name=item.get("name", "Unnamed Flow"),
                priority=item.get("priority", "MEDIUM"),
                description=item.get("description", ""),
                preconditions=item.get("preconditions", []),
                steps=steps,
                expected_outcome=item.get("expected_outcome", ""),
                test_data=item.get("test_data", {}),
            )
            flows.append(flow)

        return flows

    async def infer(
        self,
        crawl_result: CrawlResult,
        codegen_script: str = "",
    ) -> list[UserFlow]:
        """
        Infer realistic user flows from crawl results using OpenAI.

        Args:
            crawl_result: BFS crawl result with DOM snapshots
            codegen_script: Raw content of playwright codegen output (Layer 1 context)

        Returns:
            Deduplicated list of UserFlow objects sorted by priority
        """
        prompt_content = _load_prompt("infer_flows.md")
        system_prompt = _extract_system_prompt(prompt_content)

        if not system_prompt:
            system_prompt = (
                "You are an expert SDET. Analyze DOM snapshots and infer user flows. "
                "Return ONLY a valid JSON array of flow objects."
            )

        # Serialize crawl result, limiting size to avoid token limits
        crawl_data = crawl_result.model_dump(mode="json")
        # Truncate very large DOM snapshots
        for page in crawl_data.get("pages", []):
            for link in page.get("links", []):
                link.pop("selector", None)  # reduce noise

        user_content = (
            f"Base URL: {crawl_result.base_url}\n\n"
            f"Codegen Scaffold (recorded human interactions):\n```python\n{codegen_script[:3000]}\n```\n\n"
            f"Crawl Result (DOM snapshots):\n```json\n{json.dumps(crawl_data, indent=2)[:8000]}\n```\n\n"
            "Return ONLY a valid JSON array of user flows. No markdown, no explanation."
        )

        logger.info("Inferring user flows from %d pages...", len(crawl_result.pages))

        for attempt in range(2):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                )

                raw_output = response.choices[0].message.content or "[]"
                flows = self._parse_flows(raw_output)
                flows = self._deduplicate_flows(flows)
                flows = self._sort_by_priority(flows)

                logger.info("Inferred %d user flows", len(flows))
                return flows

            except json.JSONDecodeError as exc:
                if attempt == 0:
                    logger.warning("JSON parse failed, retrying with explicit instruction: %s", exc)
                    user_content += "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY the raw JSON array, no other text."
                else:
                    logger.error("Flow inference failed after retry: %s", exc)
                    return []
            except Exception as exc:
                logger.error("OpenAI call failed during flow inference: %s", exc)
                if attempt == 1:
                    return []

        return []
