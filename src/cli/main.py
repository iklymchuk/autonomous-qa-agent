"""
Typer CLI entrypoint for the AutonomousQA Agent.
All commands print Rich-formatted output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import print as rprint

load_dotenv()

app = typer.Typer(
    name="qa-agent",
    help="🤖 AutonomousQA Agent — zero-config AI-powered web testing",
    rich_markup_mode="rich",
)
console = Console()


def _setup_logging(level: str) -> None:
    """Configure logging level from string."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _make_run_id() -> str:
    """Generate a timestamped run ID."""
    return f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def _get_reports_dir() -> Path:
    """Get reports directory from env or default."""
    return Path(os.getenv("QA_REPORTS_DIR", "reports"))


async def _full_run(
    url: str,
    depth: int,
    browsers: list[str],
    headless: bool,
    a11y: bool,
    visual_diff: bool,
    interactive: bool,
    log_level: str,
) -> None:
    """Full orchestrated agent run."""
    from src.agent.crawler import SiteCrawler
    from src.agent.executor import TestExecutor
    from src.agent.flow_inferencer import FlowInferencer
    from src.agent.test_generator import TestGenerator
    from src.analysis.accessibility import AccessibilityAuditor
    from src.analysis.severity_scorer import SeverityScorer
    from src.analysis.visual_diff import VisualDiffer
    from src.cli_bridge import PlaywrightCLI
    from src.models import AgentConfig, RunData
    from src.reporting.html_reporter import HTMLReporter
    from src.reporting.json_reporter import JSONReporter

    model = os.getenv("QA_MODEL", "gpt-4o-mini")
    config = AgentConfig(
        url=url,
        max_depth=depth,
        browsers=browsers,
        headless=headless,
        a11y=a11y,
        visual_diff=visual_diff,
        interactive=interactive,
        run_id=_make_run_id(),
        reports_dir=_get_reports_dir(),
        model=model,
        log_level=log_level,
    )

    run_dir = config.reports_dir / config.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    run_data = RunData(
        run_id=config.run_id,
        config=config,
        started_at=datetime.now(timezone.utc),
        run_dir=run_dir,
    )

    cli = PlaywrightCLI()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        # STEP 1: Get playwright version
        task = progress.add_task("[1/13] Getting Playwright version...", total=None)
        try:
            run_data.playwright_version = await cli.get_version()
            progress.update(task, description=f"[1/13] Playwright {run_data.playwright_version}")
        except Exception as exc:
            console.print(f"[yellow]⚠ Could not get playwright version: {exc}[/yellow]")
        progress.remove_task(task)

        # STEP 2: Install browsers
        task = progress.add_task("[2/13] Verifying browsers...", total=None)
        try:
            await cli.install_browsers(browsers)
            progress.update(task, description=f"[2/13] Browsers ready: {', '.join(browsers)}")
        except Exception as exc:
            console.print(f"[yellow]⚠ Browser install warning: {exc}[/yellow]")
        progress.remove_task(task)

        # STEP 3: Codegen scaffold
        task = progress.add_task("[3/13] Running codegen scaffold...", total=None)
        codegen_script = ""
        try:
            codegen_result = await cli.codegen(url, run_dir / "codegen_script.py")
            run_data.codegen_result = codegen_result
            if codegen_result.script_path.exists():
                codegen_script = codegen_result.script_path.read_text()
            progress.update(task, description=f"[3/13] Codegen: {codegen_result.actions_recorded} actions")
        except Exception as exc:
            console.print(f"[yellow]⚠ Codegen skipped: {exc}[/yellow]")
        progress.remove_task(task)

        # STEP 4: HAR capture
        task = progress.add_task("[4/13] Capturing HAR traffic...", total=None)
        try:
            har_result = await cli.save_har(url, run_dir / "traffic.har")
            run_data.har_result = har_result
            progress.update(task, description=f"[4/13] HAR: {har_result.request_count} requests")
        except Exception as exc:
            console.print(f"[yellow]⚠ HAR capture skipped: {exc}[/yellow]")
        progress.remove_task(task)

        # STEP 5: BFS Crawl
        task = progress.add_task("[5/13] Crawling web app...", total=None)
        try:
            crawler = SiteCrawler(cli=cli)
            crawl_result = await crawler.crawl(
                url=url,
                max_depth=depth,
                browser_type=browsers[0],
                run_dir=run_dir,
                headless=headless,
            )
            run_data.crawl_result = crawl_result
            progress.update(
                task,
                description=f"[5/13] Crawled {crawl_result.total_pages} pages"
                + (f" ({len(crawl_result.errors)} errors)" if crawl_result.errors else ""),
            )
        except Exception as exc:
            console.print(f"[red]✗ Crawl failed: {exc}[/red]")
            raise typer.Exit(1) from exc
        progress.remove_task(task)

        # STEP 6: Flow inference
        task = progress.add_task("[6/13] Inferring user flows with AI...", total=None)
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            inferencer = FlowInferencer(client=client, model=model)
            flows = await inferencer.infer(crawl_result, codegen_script)
            run_data.flows = flows
            progress.update(task, description=f"[6/13] {len(flows)} user flows inferred")
        except Exception as exc:
            console.print(f"[yellow]⚠ Flow inference failed: {exc}[/yellow]")
            flows = []
        progress.remove_task(task)

        # STEP 7: Test generation
        task = progress.add_task("[7/13] Generating test code...", total=None)
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            generator = TestGenerator(client=client, model=model)
            test_suite = await generator.generate(flows, url, run_dir)
            run_data.test_suite = test_suite
            progress.update(
                task,
                description=f"[7/13] Generated {test_suite.test_count} tests"
                + (" ⚠ syntax errors" if not test_suite.syntax_valid else ""),
            )
        except Exception as exc:
            console.print(f"[yellow]⚠ Test generation failed: {exc}[/yellow]")
            test_suite = None
        progress.remove_task(task)

        # STEP 8: Execute tests
        task = progress.add_task("[8/13] Running tests...", total=None)
        execution_result = None
        if test_suite:
            try:
                executor = TestExecutor(cli=cli)
                execution_result = await executor.run(test_suite, config, run_dir)
                run_data.execution_result = execution_result
                progress.update(
                    task,
                    description=f"[8/13] Tests: {execution_result.passed}/{execution_result.total} passed",
                )
            except Exception as exc:
                console.print(f"[yellow]⚠ Test execution error: {exc}[/yellow]")
        else:
            progress.update(task, description="[8/13] Tests: skipped (no suite)")
        progress.remove_task(task)

        # STEP 9: Accessibility audit
        if a11y and run_data.crawl_result:
            task = progress.add_task("[9/13] Auditing accessibility...", total=None)
            try:
                auditor = AccessibilityAuditor()
                a11y_report = await auditor.audit(
                    run_data.crawl_result.pages, browser_type=browsers[0]
                )
                run_data.a11y_report = a11y_report
                progress.update(
                    task,
                    description=f"[9/13] WCAG score: {a11y_report.wcag_score:.0f}/100 ({a11y_report.total_violations} violations)",
                )
            except Exception as exc:
                console.print(f"[yellow]⚠ Accessibility audit failed: {exc}[/yellow]")
            progress.remove_task(task)

        # STEP 10: Visual diff
        if visual_diff and run_data.crawl_result:
            task = progress.add_task("[10/13] Computing visual diffs...", total=None)
            try:
                differ = VisualDiffer(cli=cli)
                before_map = await differ.capture_baseline(run_data.crawl_result.pages, run_dir)
                after_map = await differ.capture_after(run_data.crawl_result.pages, run_dir)
                vdiff = await differ.diff(before_map, after_map, run_dir)
                run_data.visual_diff_result = vdiff
                progress.update(
                    task,
                    description=f"[10/13] Visual diff: {vdiff.pages_changed}/{vdiff.total_pages} pages changed",
                )
            except Exception as exc:
                console.print(f"[yellow]⚠ Visual diff failed: {exc}[/yellow]")
            progress.remove_task(task)

        # STEP 11: Severity scoring
        if execution_result and execution_result.failed > 0:
            task = progress.add_task("[11/13] Scoring failure severity...", total=None)
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                scorer = SeverityScorer(client=client, model=model)
                scored = await scorer.score(
                    execution_result,
                    target_url=url,
                    generated_tests_path=run_dir / "generated_tests.py",
                )
                run_data.scored_failures = scored
                # Build severity breakdown
                for sf in scored:
                    sev = sf.severity.upper()
                    if sev in run_data.severity_breakdown:
                        run_data.severity_breakdown[sev] += 1
                progress.update(task, description=f"[11/13] Severity scored: {len(scored)} failures")
            except Exception as exc:
                console.print(f"[yellow]⚠ Severity scoring failed: {exc}[/yellow]")
            progress.remove_task(task)

        # STEP 12: Generate reports
        task = progress.add_task("[12/13] Generating reports...", total=None)
        run_data.finished_at = datetime.now(timezone.utc)
        try:
            html_path = HTMLReporter().generate(run_data)
            json_path = JSONReporter().generate(run_data)
            progress.update(task, description="[12/13] Reports saved")
        except Exception as exc:
            console.print(f"[red]✗ Report generation failed: {exc}[/red]")
            raise typer.Exit(1) from exc
        progress.remove_task(task)

        # STEP 13: Trace viewer
        if interactive and execution_result and execution_result.failed > 0:
            task = progress.add_task("[13/13] Opening trace viewer...", total=None)
            failed_with_trace = [
                t for t in execution_result.tests if t.status == "failed" and t.trace_path
            ]
            if failed_with_trace:
                await cli.show_trace(failed_with_trace[0].trace_path)  # type: ignore[arg-type]
            progress.remove_task(task)

    # Final summary table
    console.print()
    _print_summary(run_data, html_path, json_path)


def _print_summary(run_data: "RunData", html_path: Path, json_path: Path) -> None:  # type: ignore[name-defined]
    """Print the final run summary table."""
    from src.models import RunData

    exec_result = run_data.execution_result
    total = exec_result.total if exec_result else 0
    passed = exec_result.passed if exec_result else 0
    failed = exec_result.failed if exec_result else 0

    # Summary panel
    pass_rate = run_data.pass_rate
    color = "green" if pass_rate == 100 else "yellow" if pass_rate >= 70 else "red"

    table = Table(title=f"QA Run Summary — {run_data.run_id}", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="dim", width=24)
    table.add_column("Value", width=40)

    table.add_row("URL", run_data.config.url)
    table.add_row("Duration", f"{run_data.duration_seconds:.1f}s")
    table.add_row("Pages Crawled", str(run_data.crawl_result.total_pages if run_data.crawl_result else 0))
    table.add_row("Flows Inferred", str(len(run_data.flows)))
    table.add_row("Tests Run", str(total))
    table.add_row("Passed", f"[green]{passed}[/green]")
    table.add_row("Failed", f"[red]{failed}[/red]")
    table.add_row("Pass Rate", f"[{color}]{pass_rate:.1f}%[/{color}]")

    sev = run_data.severity_breakdown
    table.add_row(
        "Severity",
        f"[red]CRITICAL:{sev.get('CRITICAL',0)}[/red] "
        f"[yellow]HIGH:{sev.get('HIGH',0)}[/yellow] "
        f"MEDIUM:{sev.get('MEDIUM',0)} LOW:{sev.get('LOW',0)}",
    )

    if run_data.a11y_report:
        table.add_row("WCAG Score", f"{run_data.a11y_report.wcag_score:.0f}/100")

    console.print(table)
    console.print()
    console.print(f"[bold green]✓ Report saved to:[/bold green] {html_path}")
    console.print(f"[bold green]✓ JSON saved to:[/bold green]   {json_path}")


@app.command()
def run(
    url: str = typer.Option("", "--url", "-u", help="Target web app URL"),
    depth: int = typer.Option(int(os.getenv("QA_MAX_DEPTH", "3")), "--depth", "-d", help="BFS crawl depth"),
    browsers: str = typer.Option(os.getenv("QA_BROWSERS", "chromium"), "--browsers", "-b", help="Comma-separated browser list"),
    headed: bool = typer.Option(False, "--headed", help="Run in headed (visible) mode", is_flag=True),
    no_a11y: bool = typer.Option(False, "--no-a11y", help="Disable accessibility audit", is_flag=True),
    visual_diff: bool = typer.Option(False, "--visual-diff", help="Capture visual diffs", is_flag=True),
    interactive: bool = typer.Option(False, "--interactive", help="Open trace viewer on failure", is_flag=True),
    log_level: str = typer.Option(os.getenv("QA_LOG_LEVEL", "INFO"), "--log-level", help="Logging level"),
) -> None:
    """
    Run the full autonomous QA agent against a URL.

    Example: qa-agent run --url https://example.com --depth 3 --a11y
    """
    if not url:
        console.print("[red]Error: --url is required[/red]")
        raise typer.Exit(1)

    _setup_logging(log_level)

    headless = not headed
    a11y = not no_a11y
    browser_list = [b.strip() for b in browsers.split(",") if b.strip()]

    console.print(
        Panel(
            f"[bold]🤖 AutonomousQA Agent[/bold]\n"
            f"[dim]URL:[/dim] {url}\n"
            f"[dim]Depth:[/dim] {depth} | "
            f"[dim]Browsers:[/dim] {', '.join(browser_list)} | "
            f"[dim]Headless:[/dim] {headless} | "
            f"[dim]A11y:[/dim] {a11y}",
            border_style="cyan",
        )
    )

    asyncio.run(
        _full_run(
            url=url,
            depth=depth,
            browsers=browser_list,
            headless=headless,
            a11y=a11y,
            visual_diff=visual_diff,
            interactive=interactive,
            log_level=log_level,
        )
    )


@app.command()
def codegen(
    url: str = typer.Argument(help="Target URL for codegen session"),
) -> None:
    """
    Run Playwright codegen against a URL and save the scaffold.

    Example: qa-agent codegen http://localhost:5000
    """
    from src.cli_bridge import PlaywrightCLI

    _setup_logging("INFO")

    async def _run() -> None:
        cli = PlaywrightCLI()
        reports_dir = _get_reports_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = reports_dir / f"codegen_{timestamp}.py"

        console.print(f"[cyan]Starting codegen for {url}...[/cyan]")
        result = await cli.codegen(url, output_path)
        console.print(f"[green]✓ Codegen saved to: {result.script_path}[/green]")
        console.print(f"  Actions recorded: {result.actions_recorded}")

    asyncio.run(_run())


@app.command()
def screenshot(
    url: str = typer.Argument(help="URL to screenshot"),
    output: str = typer.Option("", "--output", "-o", help="Output PNG path"),
) -> None:
    """
    Capture a full-page screenshot of a URL.

    Example: qa-agent screenshot https://example.com --output screen.png
    """
    from src.cli_bridge import PlaywrightCLI

    _setup_logging("INFO")

    async def _run() -> None:
        cli = PlaywrightCLI()
        output_path = Path(output) if output else _get_reports_dir() / "screenshot.png"
        result = await cli.screenshot(url, output_path, full_page=True)
        console.print(f"[green]✓ Screenshot saved: {result.path} ({result.file_size_kb:.1f} KB)[/green]")

    asyncio.run(_run())


@app.command()
def report(
    last: bool = typer.Option(False, "--last", help="Open the most recent report"),
    list_all: bool = typer.Option(False, "--list", help="List all saved reports"),
) -> None:
    """
    Manage saved QA reports.

    qa-agent report --last     → opens most recent report in browser
    qa-agent report --list     → lists all reports with summaries
    """
    reports_dir = _get_reports_dir()

    if last:
        run_dirs = sorted(
            [d for d in reports_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not run_dirs:
            console.print("[yellow]No reports found[/yellow]")
            raise typer.Exit(1)

        html_path = run_dirs[0] / "report.html"
        if html_path.exists():
            console.print(f"[green]Opening: {html_path}[/green]")
            webbrowser.open(str(html_path.absolute()))
        else:
            console.print(f"[red]Report not found: {html_path}[/red]")

    elif list_all:
        run_dirs = sorted(
            [d for d in reports_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )

        table = Table(title="QA Reports", header_style="bold cyan")
        table.add_column("Run ID", width=24)
        table.add_column("URL", width=40)
        table.add_column("Pass Rate", width=12)
        table.add_column("Severity", width=24)

        for run_dir in run_dirs:
            json_path = run_dir / "report.json"
            if json_path.exists():
                try:
                    data = json.loads(json_path.read_text())
                    summary = data.get("summary", {})
                    sev = summary.get("severity_breakdown", {})
                    table.add_row(
                        data.get("run_id", run_dir.name),
                        data.get("url", "—")[:38],
                        f"{summary.get('pass_rate', 0):.0f}%",
                        f"C:{sev.get('CRITICAL',0)} H:{sev.get('HIGH',0)} M:{sev.get('MEDIUM',0)} L:{sev.get('LOW',0)}",
                    )
                except Exception:
                    table.add_row(run_dir.name, "—", "—", "—")

        console.print(table)


@app.command()
def clean() -> None:
    """Delete all contents of the reports directory (with confirmation)."""
    reports_dir = _get_reports_dir()
    run_dirs = [d for d in reports_dir.iterdir() if d.is_dir() and d.name.startswith("run_")]

    if not run_dirs:
        console.print("[yellow]No run directories to clean[/yellow]")
        return

    confirmed = typer.confirm(f"Delete {len(run_dirs)} run director{'ies' if len(run_dirs) > 1 else 'y'}?")
    if confirmed:
        import shutil
        for d in run_dirs:
            shutil.rmtree(d)
        console.print(f"[green]✓ Deleted {len(run_dirs)} run directories[/green]")
    else:
        console.print("[dim]Cancelled[/dim]")


@app.command()
def install_browsers() -> None:
    """Install all Playwright browsers (chromium, firefox, webkit)."""
    from src.cli_bridge import PlaywrightCLI

    _setup_logging("INFO")

    async def _run() -> None:
        cli = PlaywrightCLI()
        console.print("[cyan]Installing browsers...[/cyan]")
        result = await cli.install_browsers(["chromium", "firefox", "webkit"])
        console.print(f"[green]✓ Installed: {', '.join(result.browsers_installed)}[/green]")
        console.print(f"  Version: {result.version}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()