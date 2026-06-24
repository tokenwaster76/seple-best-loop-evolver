#!/usr/bin/env python3
"""SEPLE v5 — Real-time terminal dashboard (Rich TUI)."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
from rich import box
from rich.bar import Bar
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

DARK_THEME = Theme({
    "info": "cyan",
    "warning": "yellow",
    "danger": "red",
    "success": "green",
    "accent": "magenta",
    "dim": "dim white",
    "header": "bold bright_white",
})

FITNESS_DIMS = [
    "clarity", "specificity", "robustness",
    "iterability", "self_awareness", "error_recovery",
]


def load_json(path: Path) -> dict | None:
    try:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def read_text(path: Path, max_lines: int | None = None) -> str:
    try:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        if max_lines:
            lines = text.split("\n")[:max_lines]
            return "\n".join(lines)
        return text
    except OSError:
        return ""


def file_age_seconds(path: Path) -> float | None:
    try:
        if path.exists():
            return time.time() - path.stat().st_mtime
    except OSError:
        pass
    return None


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def calc_rate(count: float, started_at: str | None) -> float:
    start = parse_ts(started_at)
    if not start:
        return 0.0
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    if elapsed < 1:
        return 0.0
    return count / (elapsed / 60)


def ascii_sparkline(values: list[float], width: int = 40) -> str:
    """Render a simple unicode sparkline from score values."""
    if not values:
        return "—"
    blocks = "▁▂▃▄▅▆▇█"
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values
    lo, hi = min(sampled), max(sampled)
    span = hi - lo or 1.0
    return "".join(
        blocks[min(int((v - lo) / span * (len(blocks) - 1)), len(blocks) - 1)]
        for v in sampled
    )


def score_color(score: float) -> str:
    if score >= 80:
        return "green"
    if score >= 60:
        return "yellow"
    return "red"


def build_header(state: dict | None, stale: bool) -> Panel:
    if not state:
        title = Text("SEPLE v5 — Self-Evolving Prompt Loop Engineer", style="header")
        sub = Text("⏳ Waiting for runner...", style="warning")
        return Panel(Group(title, sub), border_style="yellow", box=box.DOUBLE)

    status = state.get("status", "idle")
    status_style = {
        "running": "success",
        "awaiting_continue": "warning",
        "stopped": "danger",
        "completed": "info",
        "idle": "dim",
    }.get(status, "white")

    gen = state.get("generation", 0)
    max_gen = state.get("max_generation", 50)
    score = state.get("best_score", 0)
    version = state.get("version", "v1.0.0")

    title = Text("🧬 SEPLE v5 — Self-Evolving Prompt Loop Engineer", style="header")
    line1 = Text.assemble(
        ("Gen ", "dim"), (f"{gen}", "bold cyan"), (f"/{max_gen}", "dim"),
        "  │  ", ("Score ", "dim"), (f"{score}", f"bold {score_color(score)}"),
        "  │  ", ("Version ", "dim"), (version, "bold magenta"),
    )
    line2 = Text.assemble(
        ("Status ", "dim"), (status.upper(), status_style),
        "  │  ", ("Provider ", "dim"), (state.get("llm_provider", "?"), "cyan"),
        "/", (state.get("llm_model", "?"), "cyan"),
    )
    if stale:
        line2.append("  │  ", style="dim")
        line2.append("⚠ Stale data", style="warning")

    if state.get("awaiting_continue"):
        line2.append("\n")
        line2.append("⏸ AWAITING CONTINUE — run: python seple_runner.py --resume y|n", style="bold yellow")

    return Panel(Group(title, line1, line2), border_style="bright_blue", box=box.DOUBLE)


def build_fitness_table(state: dict | None) -> Panel:
    table = Table(show_header=True, header_style="bold", expand=True, box=box.SIMPLE)
    table.add_column("Dimension", style="cyan", width=16)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Bar", min_width=20)

    if not state:
        table.add_row("—", "—", "—")
        return Panel(table, title="📊 Fitness Scores", border_style="blue")

    fitness = state.get("fitness_scores", {})
    for dim in FITNESS_DIMS:
        score = float(fitness.get(dim, 0))
        color = score_color(score)
        bar = Bar(size=score, begin=0, end=100, width=20, color=color)
        table.add_row(dim, f"{score:.0f}", bar)

    return Panel(table, title="📊 Fitness Scores", border_style="blue")


def build_metrics_panel(state: dict | None) -> Panel:
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent

    table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
    table.add_column("Key", style="dim")
    table.add_column("Val", style="bold")

    if state:
        tokens = state.get("tokens_used", 0)
        tokens_gen = state.get("tokens_this_gen", 0)
        gens_rate = calc_rate(state.get("generation", 0), state.get("started_at"))
        tok_rate = calc_rate(tokens, state.get("started_at"))
        fixes = state.get("self_fix_count", 0)
        gen = max(state.get("generation", 1), 1)
        fix_rate = round(fixes / gen * 100, 1)

        table.add_row("🖥 CPU", f"{cpu:.1f}%")
        table.add_row("💾 Memory", f"{mem:.1f}%")
        table.add_row("⚡ Gens/min", f"{gens_rate:.2f}")
        table.add_row("🪙 Tokens Used", f"{tokens:,}")
        table.add_row("🪙 This Gen", f"{tokens_gen:,}")
        table.add_row("🪙 Tokens/min", f"{tok_rate:,.0f}")
        table.add_row("🔧 Self-fixes", f"{fixes} ({fix_rate}%)")
        table.add_row("⏱ Last Gen", f"{state.get('last_generation_duration_s', 0):.1f}s")
    else:
        table.add_row("🖥 CPU", f"{cpu:.1f}%")
        table.add_row("💾 Memory", f"{mem:.1f}%")
        table.add_row("—", "Waiting for state...")

    return Panel(table, title="📈 Real-time Metrics", border_style="green")


def build_sparkline_panel(state: dict | None) -> Panel:
    if not state or not state.get("score_history"):
        content = Text("No score data yet", style="dim")
    else:
        scores = [float(e["score"]) for e in state["score_history"]]
        spark = Text(ascii_sparkline(scores), style="bold cyan")
        latest = scores[-1]
        delta = scores[-1] - scores[-2] if len(scores) > 1 else 0
        sign = "+" if delta > 0 else ""
        label = Text.assemble(
            ("Score History ", "dim"),
            (f"({len(scores)} gens)", "dim"),
            "  latest: ",
            (f"{latest}", f"bold {score_color(latest)}"),
            f" ({sign}{delta:.1f})",
        )
        content = Group(label, spark)

    return Panel(content, title="📉 Score Trend", border_style="cyan")


def build_progress_panel(state: dict | None) -> Panel:
    if not state:
        return Panel(Text("—", style="dim"), title="🎯 Progress", border_style="magenta")

    gen = state.get("generation", 0)
    max_gen = state.get("max_generation", 50)
    score = state.get("best_score", 0)

    gen_progress = Progress(
        TextColumn("[bold]Generations"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
    )
    gen_task = gen_progress.add_task("gen", total=max_gen, completed=min(gen, max_gen))

    score_progress = Progress(
        TextColumn("[bold]Score→98  "),
        BarColumn(bar_width=30, style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed:.0f}/98"),
    )
    score_progress.add_task("score", total=98, completed=min(score, 98))

    high = state.get("consecutive_high_scores", 0)
    note = Text(f"Consecutive ≥98: {high}/2", style="dim")

    return Panel(
        Group(gen_progress, score_progress, note),
        title="🎯 Progress",
        border_style="magenta",
    )


def build_latest_summary_panel(root: Path) -> Panel:
    text = read_text(root / "latest_summary.md", max_lines=22)
    if not text.strip():
        text = "*Waiting for first generation...*"
    return Panel(
        Markdown(text),
        title="📝 Latest Iteration",
        border_style="yellow",
        height=14,
    )


def build_checkpoint_panel(root: Path, state: dict | None) -> Panel:
    text = read_text(root / "checkpoint_report.md", max_lines=18)
    if state and state.get("checkpoints_completed", 0) == 0:
        text = "*No checkpoint yet (at gen 50, 100...)*\n\n" + text[:200]
    return Panel(
        Markdown(text),
        title="📊 Checkpoint Report",
        border_style="bright_magenta",
        height=12,
    )


def build_prompt_panel(root: Path) -> Panel:
    text = read_text(root / "best_prompt.md", max_lines=16)
    if not text:
        text = "(no prompt yet)"
    return Panel(
        Text(text, style="white"),
        title="✨ Best Prompt (snippet)",
        border_style="bright_cyan",
        height=12,
    )


def build_evolution_panel(root: Path) -> Panel:
    full = read_text(root / "EVOLUTION.md")
    lines = full.split("\n")
    # Grab last ~15 lines of actual content
    tail = "\n".join(lines[-15:]) if lines else ""
    if not tail.strip() or tail.strip() == "---":
        tail = "*Evolution log empty — waiting for runner*"
    return Panel(
        Markdown(tail),
        title="📜 Evolution Log (tail)",
        border_style="dim blue",
        height=10,
    )


def build_layout(root: Path) -> Layout:
    state = load_json(root / "state.json")
    age = file_age_seconds(root / "state.json")
    stale = age is not None and age > 30 and state and state.get("status") == "running"

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1),
    )
    layout["left"].split_column(
        Layout(name="fitness", size=12),
        Layout(name="metrics_row", size=8),
        Layout(name="progress", size=6),
        Layout(name="sparkline", size=5),
    )
    layout["right"].split_column(
        Layout(name="latest", size=14),
        Layout(name="checkpoint", size=12),
        Layout(name="prompt", size=12),
        Layout(name="evolution", size=10),
    )
    layout["metrics_row"].split_row(
        Layout(name="metrics", ratio=1),
    )

    layout["header"].update(build_header(state, stale))
    layout["fitness"].update(build_fitness_table(state))
    layout["metrics"].update(build_metrics_panel(state))
    layout["sparkline"].update(build_sparkline_panel(state))
    layout["progress"].update(build_progress_panel(state))
    layout["latest"].update(build_latest_summary_panel(root))
    layout["checkpoint"].update(build_checkpoint_panel(root, state))
    layout["prompt"].update(build_prompt_panel(root))
    layout["evolution"].update(build_evolution_panel(root))

    refreshed = datetime.now().strftime("%H:%M:%S")
    footer_text = Text.assemble(
        ("Last refresh: ", "dim"), (refreshed, "cyan"),
        "  │  ", ("Root: ", "dim"), (str(root), "dim"),
    )
    if stale:
        footer_text.append("  │  ⚠ Runner may be idle", style="warning")
    layout["footer"].update(Panel(footer_text, border_style="dim"))

    return layout


def main() -> None:
    parser = argparse.ArgumentParser(description="SEPLE v5 TUI Dashboard")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="SEPLE repo directory",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=float(os.getenv("SEPLE_DASH_REFRESH", "1.5")),
        help="Refresh interval in seconds",
    )
    args = parser.parse_args()
    root = args.dir.resolve()

    console = Console(theme=DARK_THEME)

    with Live(
        build_layout(root),
        console=console,
        refresh_per_second=4,
        screen=True,
    ) as live:
        try:
            while True:
                time.sleep(args.refresh)
                live.update(build_layout(root))
        except KeyboardInterrupt:
            console.print("\n[dim]Dashboard exited.[/dim]")


if __name__ == "__main__":
    main()