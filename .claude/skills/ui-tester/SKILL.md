# ui-tester Skill

name: ui-tester
description: |
  Zero-config autonomous UI QA agent. Given a URL, crawls the app,
  infers user flows, generates and executes Playwright tests, audits
  accessibility, captures visual diffs, classifies failures by severity,
  and saves a full report to reports/.

input_schema:
  url:
    type: string
    required: true
    description: Target web application URL to test
  max_depth:
    type: integer
    default: 3
    description: BFS crawl depth limit
  browsers:
    type: array
    items: string
    enum: [chromium, firefox, webkit]
    default: [chromium]
    description: Browser engines to run tests against
  headless:
    type: boolean
    default: true
    description: Run browsers in headless mode
  a11y:
    type: boolean
    default: true
    description: Run axe-core WCAG 2.1 AA accessibility audit
  visual_diff:
    type: boolean
    default: false
    description: Capture before/after screenshots and compute pixel diff
  interactive:
    type: boolean
    default: false
    description: Open Playwright trace viewer after test run

output_schema:
  report_path:
    type: string
    description: Absolute path to generated HTML report
  json_path:
    type: string
    description: Absolute path to machine-readable JSON report
  test_count:
    type: integer
    description: Total number of generated and executed tests
  passed:
    type: integer
    description: Number of passing tests
  failed:
    type: integer
    description: Number of failing tests
  severity_breakdown:
    type: object
    description: Failure counts by severity { CRITICAL, HIGH, MEDIUM, LOW }
  wcag_score:
    type: number
    description: WCAG 2.1 AA score 0.0–100.0 (present if a11y=true)
  playwright_version:
    type: string
    description: Playwright version captured from CLI at run start

tools_allowed: [browser, bash]

step_by_step_instructions: |
  1. ENVIRONMENT SETUP [cli_bridge.py — Layer 1 CLI]
     - Call PlaywrightCLI.get_version() to capture playwright --version
     - Store version string in run metadata for the final report
     - Call PlaywrightCLI.install_browsers(config.browsers) to ensure all
       required browser engines are installed before any automation starts
     - Error handling: if browser install fails, raise immediately with a
       clear message asking the user to run `make install-browsers`

  2. CODEGEN SCAFFOLD [cli_bridge.py — Layer 1 CLI]
     - Call PlaywrightCLI.codegen(url, output_path=run_dir/codegen_script.py)
     - This runs `playwright codegen <url> --output <path> --timeout 15000`
       in headed mode, recording a realistic starting interaction scaffold
     - The resulting codegen_script.py is passed to FlowInferencer as context
       so that Claude starts from human-realistic interactions, not just DOM
     - Error handling: if codegen times out or produces an empty script,
       log a WARNING and continue — codegen is a quality hint, not required

  3. HAR CAPTURE [cli_bridge.py — Layer 1 CLI]
     - Call PlaywrightCLI.save_har(url, har_path=run_dir/traffic.har)
     - This runs `playwright open --save-har=<path> --save-har-glob=**/api/**`
       to capture all network traffic including API calls
     - HAR is stored in the run directory for human inspection
     - Error handling: non-zero exit → log WARNING, skip HAR, continue

  4. BFS CRAWL [crawler.py — Layer 2 Python API]
     - Call SiteCrawler.crawl(url, max_depth, browser_type)
     - Opens a Playwright browser context, performs breadth-first traversal
     - Respects same-origin policy — never follows external links
     - Checks robots.txt before crawling
     - For each page: extracts DOMSnapshot (inputs, buttons, forms, links,
       selects, headings) via page.evaluate()
     - For each page: calls cli_bridge.screenshot() to capture before-image
     - Continues BFS even if individual pages fail (never crashes)
     - Returns CrawlResult with all DOMSnapshots + error list

  5. FLOW INFERENCE [flow_inferencer.py — AI Layer via OpenAI]
     - Call FlowInferencer.infer(crawl_result, codegen_script_content)
     - Sends full CrawlResult JSON + codegen script to GPT-4o
     - Uses system prompt from prompts/infer_flows.md
     - Model returns structured JSON: list of UserFlow objects with steps
     - Deduplicates semantically equivalent flows
     - Sorts by priority (HIGH → MEDIUM → LOW)
     - Error handling: if OpenAI returns invalid JSON, retry once with
       explicit JSON-only instruction; if still invalid, raise

  6. TEST GENERATION [test_generator.py — AI Layer via OpenAI]
     - Call TestGenerator.generate(flows, base_url)
     - Sends UserFlow list JSON to GPT-4o
     - Uses system prompt from prompts/generate_tests.md
     - Model returns a complete Python pytest file with:
       * pytest fixtures (browser, page, base_url)
       * One test function per UserFlow
       * Page Object Model classes
       * Playwright trace capture enabled
     - Validates syntax via ast.parse() — retries once on SyntaxError
     - Saves to run_dir/generated_tests.py
     - Error handling: if second attempt also fails syntax check, save
       the broken file with a .broken extension and log ERROR, continue
       with zero tests (report will reflect 0 tests run)

  7. TEST EXECUTION [executor.py — Layer 1 subprocess + Layer 2 pytest-playwright]
     - Call TestExecutor.run(suite, config)
     - Runs pytest on generated_tests.py via asyncio.create_subprocess_exec
     - Flags: -v --tb=short --json-report --json-report-file=pytest_raw.json
     - Playwright traces saved per-test to run_dir/traces/
     - Captures full stdout/stderr
     - Returns ExecutionResult with per-test status, duration, error_message
     - Error handling: pytest process failure (exit code 2+) → log ERROR
       but still parse whatever pytest_raw.json was written

  8. ACCESSIBILITY AUDIT [accessibility.py — Layer 2 Python API] (if a11y=true)
     - Call AccessibilityAuditor.audit(crawl_result.pages)
     - For each page: open fresh browser page, inject axe-core from CDN
     - Run axe.run() via page.evaluate(), collect violations
     - Calculate WCAG score: 100 - (critical*20 + serious*10 + moderate*5 + minor*1)
     - Clamp score to [0, 100]
     - Error handling: if axe injection fails on a page, skip that page,
       log WARNING, continue

  9. VISUAL DIFF [visual_diff.py — Layer 1 CLI + Pillow] (if visual_diff=true)
     - Crawl is now complete; re-capture all pages as after-images
     - Call cli_bridge.screenshot() for each page → run_dir/visual/after_*.png
     - Call VisualDiffer.diff(before_map, after_map)
     - Pillow pixel diff: mark changed pixels red on composite image
     - Save diff images to run_dir/visual/diff_*.png
     - Error handling: if a before-image is missing for a URL, skip that pair

  10. SEVERITY SCORING [severity_scorer.py — AI Layer via OpenAI] (if failures > 0)
      - Call SeverityScorer.score(execution_result)
      - Batch ALL failed tests into a single OpenAI call
      - Uses system prompt from prompts/analyze_results.md
      - Returns list[ScoredFailure] with severity + reason + reproduction_steps
      - Error handling: if OpenAI call fails, assign severity=MEDIUM to all
        failures and log WARNING

  11. REPORT GENERATION [html_reporter.py + json_reporter.py]
      - Assemble RunData from all previous step outputs
      - Call HTMLReporter.generate(run_data) → run_dir/report.html
        * Self-contained: all images base64-encoded inline
        * Sections: summary, test results, test code, a11y, visual diffs,
          crawl map, debug logs
      - Call JSONReporter.generate(run_data) → run_dir/report.json
        * Machine-readable, CI-consumable summary
      - Print final summary table to terminal using Rich

  12. TRACE VIEWER [cli_bridge.py — Layer 1 CLI] (if interactive=true)
      - Find the trace zip for the first failed test
      - Call PlaywrightCLI.show_trace(trace_path)
      - This opens the Playwright trace viewer in a browser window
      - Only runs if at least one test failed and a trace file exists
