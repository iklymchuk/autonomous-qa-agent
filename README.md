# AutonomousQA Agent

> Zero-config AI-powered web testing: give it a URL, get a full QA report.

[![CI](https://github.com/iklymchuk/autonomous-qa-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/iklymchuk/autonomous-qa-agent/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/codecov/c/github/iklymchuk/autonomous-qa-agent)](https://codecov.io/gh/iklymchuk/autonomous-qa-agent)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Playwright](https://img.shields.io/badge/playwright-1.49-green.svg)](https://playwright.dev/)

---

## What it does

AutonomousQA Agent is a production-grade autonomous QA platform that requires zero human-written
test scripts. Given only a URL, it crawls the web application, infers realistic user flows using
GPT-4o, generates executable Playwright pytest tests dynamically, runs them, audits accessibility
with axe-core, captures visual diffs, and produces a self-contained HTML + JSON report — all
without a single line of test code written by a human.

The platform uses a deliberate two-layer Playwright architecture: the Playwright CLI handles all
artifact capture (HAR files, screenshots, traces, codegen scaffolds) while the Playwright Python
async API handles intelligent automation (BFS crawling, DOM extraction, axe-core injection).
GPT-4o powers the three AI steps: flow inference from DOM snapshots, pytest code generation with
Page Object Model, and batched failure severity classification.

---

## Demo

```
[GIF placeholder — record with vhs after first run]
```

Example report summary (printed to terminal after a run):

```
┌─────────────────────────────────────────────────────────┐
│ QA Run Summary — run_20240115_143022                     │
├──────────────────────────┬──────────────────────────────┤
│ URL                      │ http://localhost:5000         │
│ Duration                 │ 47.2s                         │
│ Pages Crawled            │ 6                             │
│ Flows Inferred           │ 5                             │
│ Tests Run                │ 5                             │
│ Passed                   │ 4                             │
│ Failed                   │ 1                             │
│ Pass Rate                │ 80.0%                         │
│ Severity                 │ CRITICAL:0 HIGH:1 MEDIUM:0   │
│ WCAG Score               │ 85/100                        │
└──────────────────────────┴──────────────────────────────┘

✓ Report saved to: reports/run_20240115_143022/report.html
✓ JSON saved to:   reports/run_20240115_143022/report.json
```

---

## Quick Start

```bash
git clone https://github.com/iklymchuk/autonomous-qa-agent.git
cd autonomous-qa-agent

make install
make install-browsers

cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

make demo
```

---

## CLI Usage

### Full agent run

```bash
qa-agent run --url https://example.com
qa-agent run --url https://example.com --depth 5 --browsers chromium,firefox
qa-agent run --url https://example.com --a11y --visual-diff
qa-agent run --url https://example.com --headed --interactive  # opens trace viewer on failure
```

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | required | Target web app URL |
| `--depth` | 3 | BFS crawl depth |
| `--browsers` | chromium | Comma-separated: chromium,firefox,webkit |
| `--headless/--headed` | headless | Browser visibility |
| `--a11y/--no-a11y` | a11y | Run axe-core WCAG audit |
| `--visual-diff` | off | Capture before/after screenshots + pixel diff |
| `--interactive` | off | Open Playwright trace viewer after failures |
| `--log-level` | INFO | DEBUG / INFO / WARNING / ERROR |

### Other commands

```bash
# Record a codegen session (explore a site manually)
qa-agent codegen http://localhost:5000

# Capture a full-page screenshot
qa-agent screenshot https://example.com --output screen.png

# Open the most recent report in browser
qa-agent report --last

# List all saved reports
qa-agent report --list

# Delete all reports (with confirmation)
qa-agent clean

# Install all Playwright browsers
qa-agent install-browsers
```

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              qa-agent run --url <url>           │
└──────────────────────┬──────────────────────────┘
                       │
       ┌───────────────▼───────────────┐
       │         cli_bridge.py         │  ← LAYER 1: Playwright CLI
       │  codegen │ har │ screenshot   │
       │  install │ version │ trace    │
       └───────────────┬───────────────┘
                       │
       ┌───────────────▼───────────────┐
       │    crawler / executor         │  ← LAYER 2: Playwright Python API
       │    accessibility              │
       └───────────────┬───────────────┘
                       │
       ┌───────────────▼───────────────┐
       │         OpenAI GPT-4o         │  ← AI Layer
       │  infer flows │ gen tests      │
       │  score severity               │
       └───────────────┬───────────────┘
                       │
       ┌───────────────▼───────────────┐
       │          reporters            │
       │     report.html │ .json       │
       └───────────────────────────────┘
```

### Agent Steps (in order)

| Step | Layer | Module | Output |
|------|-------|--------|--------|
| 1 | CLI | `cli_bridge.get_version()` | Playwright version string |
| 2 | CLI | `cli_bridge.install_browsers()` | Verified browser installations |
| 3 | CLI | `cli_bridge.codegen()` | `codegen_script.py` |
| 4 | CLI | `cli_bridge.save_har()` | `traffic.har` |
| 5 | Python API | `crawler.crawl()` | `CrawlResult` with DOM snapshots |
| 6 | AI | `flow_inferencer.infer()` | `list[UserFlow]` sorted by priority |
| 7 | AI | `test_generator.generate()` | `generated_tests.py` |
| 8 | CLI + API | `executor.run()` | `ExecutionResult` |
| 9 | Python API | `accessibility.audit()` | `A11yReport` + WCAG score |
| 10 | CLI | `visual_diff.diff()` | Before/after/diff PNGs |
| 11 | AI | `severity_scorer.score()` | `list[ScoredFailure]` |
| 12 | Python | `html_reporter + json_reporter` | `report.html` + `report.json` |
| 13 | CLI | `cli_bridge.show_trace()` | Opens trace viewer (if `--interactive`) |

---

## Report Structure

```
reports/
└── run_20240115_143022/
    ├── report.html          # Self-contained HTML (base64 images, no external deps)
    ├── report.json          # CI-consumable JSON summary
    ├── generated_tests.py   # AI-generated test code (auditable)
    ├── codegen_script.py    # Raw playwright codegen output
    ├── traffic.har          # Full HAR from CLI capture
    ├── pytest_raw.json      # Raw pytest-json-report output
    ├── visual/
    │   ├── before_*.png     # Screenshots before tests
    │   ├── after_*.png      # Screenshots after tests
    │   └── diff_*.png       # Pixel diff (changed pixels in red)
    └── traces/
        └── test_*.zip       # Playwright traces per test
```

The `report.html` is fully self-contained: all images are base64-encoded inline. You can
email it, archive it, or open it offline without needing any other files.

---

## SDET Design Decisions

### 1. Why two-layer Playwright (CLI + Python API)

Playwright's CLI and Python API serve different purposes, and conflating them leads to
fragile, opaque automation. The CLI is designed for artifact generation: HAR files, codegen
scaffolds, screenshots, and trace viewing are all "output artifacts" best captured via composable
shell commands that work identically in CI and locally. The Python async API is designed for
programmatic control: BFS traversal, dynamic DOM evaluation, route interception, and axe-core
injection all require in-process logic that CLI cannot express. By separating these concerns into
`cli_bridge.py` (Layer 1) and the agent/analysis modules (Layer 2), we get CI-portability from the
CLI and full control from the Python API without either compromising the other.

### 2. Why BFS crawling with same-origin enforcement

Depth-first crawling risks getting stuck in deep link trees or following external redirects.
BFS guarantees that shallow, high-value pages (home, login, dashboard) are always discovered
before deep or less-important pages, regardless of how the navigation graph is structured.
Same-origin enforcement prevents the agent from crawling third-party sites (CDNs, OAuth providers,
analytics) that are irrelevant to the application under test and would pollute the flow inference
with noise. Robots.txt compliance is included as a professional courtesy and to avoid unintentional
crawling of protected paths.

### 3. Why codegen scaffold is passed to flow inferencer as context

Playwright's codegen records actual human interactions in a browser session. When this script is
provided alongside the DOM snapshots to GPT-4o, the model has evidence of realistic interaction
patterns — which elements a human would actually click, what data they would enter, which pages
matter. Without this context, the model must infer flows purely from static DOM structure, which
tends to produce generic, low-value test flows. The codegen output acts as a "ground truth" signal
that significantly improves the quality and specificity of inferred flows.

### 4. Why Page Object Model in generated tests

POM is the industry standard for maintainable Playwright test suites for good reason: it separates
selector maintenance from test logic. When the AI generates POM classes (LoginPage, DashboardPage,
etc.), the result is test code that a human engineer can actually read, understand, and maintain.
Raw selector-based tests with hardcoded CSS strings scattered through test functions are fragile
and unreadable. By instructing the model to produce POM classes, the generated tests serve as
documentation as well as verification.

### 5. Why ast.parse() validation before test execution

Large language models occasionally produce syntactically invalid Python despite instructions.
Running `ast.parse()` before test execution catches these failures instantly (< 1ms) without
the confusion of a cryptic pytest import error. The retry mechanism gives the model a second
attempt with the specific syntax error message, which dramatically increases the success rate.
If both attempts fail, the broken file is preserved for debugging while a valid skip-test placeholder
ensures the pipeline continues cleanly rather than crashing.

### 6. Why batched Claude calls for severity scoring (not per-failure)

A separate API call per failure would multiply latency and cost linearly with the number of
failures. A single batched call processes all failures in one inference pass, giving the model
comparative context: it can assess whether multiple failures are related (e.g., all failing on
the same page suggests an infrastructure issue vs. isolated test failures). Batch scoring also
produces more consistent severity assessments since the model evaluates all failures relative to
each other rather than in isolation.

### 7. Why base64-inline images in HTML report

A report that depends on file paths is fragile: moving the report directory, archiving the run,
or emailing the report will break image references. By encoding all screenshots as base64 data
URIs directly in the HTML, the `report.html` becomes a single portable file. This is the same
approach used by major test reporting tools (Allure, Playwright's built-in HTML reporter) and is
standard practice for SDET portfolio artifacts that need to be shared across teams.

### 8. Why temperature=0 for all AI calls

Temperature=0 makes language model outputs deterministic given the same inputs. In a CI/CD
context, deterministic behavior is critical: the same codebase must produce the same test files
across runs, making it possible to detect genuine regressions vs. random variation in test generation.
Temperature=0 also minimizes "creative" hallucinations — the model produces the most likely,
most conventional output, which for code generation means syntactically correct, idiomatic Python
rather than experimental approaches that may not work.

---

## Running Tests

### Unit tests (no API key required)

```bash
make test
# or
poetry run pytest tests/unit/ -v
```

### Unit tests with coverage

```bash
make coverage
# Opens htmlcov/index.html
```

### Integration tests (requires OPENAI_API_KEY)

```bash
export OPENAI_API_KEY=sk-...
make test-integration
```

Integration tests are automatically skipped if `OPENAI_API_KEY` is not set:

```
SKIP tests/integration/test_full_agent_run.py::test_full_agent_run_creates_reports
  OPENAI_API_KEY not set — skipping integration tests
```

### Lint + type check

```bash
make lint       # ruff
make typecheck  # mypy
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make changes and add tests
4. Run the full check suite: `make lint && make typecheck && make test`
5. Submit a pull request with a clear description

Coverage target: 80%+ (enforced in CI). All new modules require unit tests mocking external calls.
