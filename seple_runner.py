#!/usr/bin/env python3
"""SEPLE v5 — Self-Evolving Prompt Loop Engineer (autonomous runner)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from seple_llm import LLMClient, LLMResponse, extract_json

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
BEST_PROMPT_FILE = ROOT / "best_prompt.md"
EVOLUTION_FILE = ROOT / "EVOLUTION.md"
LATEST_SUMMARY_FILE = ROOT / "latest_summary.md"
CHECKPOINT_FILE = ROOT / "checkpoint_report.md"
CONTINUE_PROMPT_FILE = ROOT / "continue_prompt.json"
INITIAL_PROMPT_FILE = ROOT / ".initial_prompt_backup.md"

FITNESS_WEIGHTS = {
    "clarity": 0.18,
    "specificity": 0.18,
    "robustness": 0.17,
    "iterability": 0.17,
    "self_awareness": 0.15,
    "error_recovery": 0.15,
}

FITNESS_DIMS = list(FITNESS_WEIGHTS.keys())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        _ensure_fresh_state()
    with STATE_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict[str, Any]) -> None:
    state["last_updated"] = utc_now()
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def compute_weighted_score(fitness: dict[str, float]) -> float:
    return round(
        sum(float(fitness.get(k, 0)) * w for k, w in FITNESS_WEIGHTS.items()), 1
    )


def _get_default_provider_model() -> tuple[str, str]:
    """Prefer Ollama llama3.1 locally when no API keys are present (per requirements)."""
    has_openrouter = bool(os.getenv("OPENROUTER_API_KEY"))
    has_grok = bool(os.getenv("XAI_API_KEY"))
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))

    env_provider = os.getenv("SEPLE_LLM_PROVIDER")
    env_model = os.getenv("SEPLE_MODEL")

    if env_provider and env_model:
        return env_provider.lower(), env_model

    if env_provider:
        prov = env_provider.lower()
        if prov == "ollama":
            return "ollama", env_model or "llama3.1"
        if prov == "grok":
            return "grok", env_model or "grok-3"
        if prov == "openrouter":
            return "openrouter", env_model or "google/gemini-2.5-flash"
        if prov == "openai":
            return "openai", env_model or "gpt-4o"
        if prov == "anthropic":
            return "anthropic", env_model or "claude-3-5-sonnet-20241022"

    # No explicit provider: choose based on available keys, default to ollama for truly local
    if has_openrouter:
        return "openrouter", "google/gemini-2.5-flash"
    if has_grok:
        return "grok", "grok-3"
    if has_openai:
        return "openai", "gpt-4o"
    if has_anthropic:
        return "anthropic", "claude-3-5-sonnet-20241022"
    # No keys at all -> real local default
    return "ollama", "llama3.1"


def _ensure_fresh_state() -> None:
    """Create initial state.json if it does not exist. Never simulates data."""
    prov, model = _get_default_provider_model()
    template = {
        "generation": 0,
        "version": "v1.0.0",
        "best_score": 0.0,
        "previous_score": 0.0,
        "consecutive_high_scores": 0,
        "status": "idle",
        "fitness_scores": dict.fromkeys(FITNESS_DIMS, 0),
        "tokens_used": 0,
        "tokens_this_gen": 0,
        "token_history": [],
        "score_history": [],
        "self_fix_count": 0,
        "self_fix_events": [],
        "reflections": [],
        "started_at": None,
        "last_updated": None,
        "last_generation_duration_s": 0,
        "llm_provider": prov,
        "llm_model": model,
        "stop_reason": None,
        "max_generation": 50,
        "checkpoint_generation": 50,
        "checkpoints_completed": 0,
        "last_summary": {"score_delta": 0, "successes": [], "problems": [], "improvements": []},
        "evolution_insights": [],
        "awaiting_continue": False,
    }
    save_state(template)
    if not LATEST_SUMMARY_FILE.exists():
        write_text(LATEST_SUMMARY_FILE, "# SEPLE Iteration Summary\n\n**Status:** Fresh start. Awaiting first generation.\n")
    if not CHECKPOINT_FILE.exists():
        write_text(CHECKPOINT_FILE, "# SEPLE Checkpoint Report\n\nNo checkpoint yet.\n")


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def ensure_git_repo() -> None:
    if not (ROOT / ".git").exists():
        run_git("init")
        run_git("config", "user.email", "tokenwaster76@users.noreply.github.com")
        run_git("config", "user.name", "tokenwaster76")


def git_commit(message: str) -> str | None:
    ensure_git_repo()
    run_git("add", "-A")
    status = run_git("status", "--porcelain", check=False)
    if not status.stdout.strip():
        return None
    run_git("commit", "-m", message)
    rev = run_git("rev-parse", "--short", "HEAD", check=False)
    return rev.stdout.strip() or None


def build_evolution_system_prompt(state: dict[str, Any], meta_improvements: str = "") -> str:
    reflections = state.get("reflections", [])[-5:]
    reflection_text = "\n".join(f"- Gen {r['gen']}: {r['text']}" for r in reflections) or "(none yet)"

    return f"""You are SEPLE v5 (Self-Evolving Prompt Loop Engineer), an autonomous meta-prompt optimizer.

Your job: analyze the current best system prompt, evaluate it against the fitness rubric, and produce an improved version.

## Fitness Rubric (score each 0-100)
- clarity: unambiguous, well-structured instructions
- specificity: concrete constraints, examples, schemas
- robustness: edge cases, partial failures, ambiguity handling
- iterability: supports incremental self-improvement across generations
- self_awareness: reflection, limitations, meta-cognition
- error_recovery: graceful degradation, self-diagnosis, fallbacks

best_score = weighted average using weights: {json.dumps(FITNESS_WEIGHTS)}

## Current Generation: {state['generation'] + 1}
## Current Version: {state['version']}
## Current Best Score: {state['best_score']}
## Current Fitness: {json.dumps(state['fitness_scores'], indent=2)}

## Previous Reflections
{reflection_text}

## Score History (last 10)
{json.dumps(state.get('score_history', [])[-10:])}

{meta_improvements}

## CRITICAL: Response Format
Return ONLY valid JSON (no markdown fences) with this exact schema:
{{
  "reflection": "string — honest assessment of current prompt weaknesses",
  "improvements": ["list of specific changes made"],
  "new_prompt": "string — the FULL updated prompt text (markdown)",
  "fitness_scores": {{"clarity": 0, "specificity": 0, "robustness": 0, "iterability": 0, "self_awareness": 0, "error_recovery": 0}},
  "best_score": 0.0,
  "version_notes": "brief version changelog",
  "successes": ["what improved this generation"],
  "problems": ["remaining risks or weaknesses"],
  "next_focus": ["priority for next generation"],
  "code_fixes": [{{"file": "relative/path.py", "search": "exact old text", "replace": "exact new text"}}]
}}

Rules:
- new_prompt must be the complete prompt, not a diff
- fitness_scores must reflect the NEW prompt quality
- best_score must match weighted average within 5 points
- Strengthen the weakest dimension without regressing others
- code_fixes only if runner code has bugs (usually empty array)"""


def build_evolution_user_prompt() -> str:
    prompt = read_text(BEST_PROMPT_FILE)
    return f"""## Current Best Prompt (best_prompt.md)

{prompt}

Evolve this prompt for generation {{next_gen}}. Return JSON only."""


def parse_evolution_response(
    llm: LLMClient, system: str, user: str, state: dict[str, Any]
) -> tuple[dict[str, Any], LLMResponse]:
    response = llm.chat(system, user)
    try:
        data = extract_json(response.content)
    except (ValueError, json.JSONDecodeError):
        retry_user = user + "\n\nYour previous response was not valid JSON. Return ONLY valid JSON."
        response = llm.chat(system, retry_user, temperature=0.3)
        data = extract_json(response.content)

    computed = compute_weighted_score(data.get("fitness_scores", {}))
    llm_score = float(data.get("best_score", computed))
    if abs(llm_score - computed) > 5:
        data["best_score"] = computed
    else:
        data["best_score"] = llm_score

    return data, response


def apply_code_fixes(fixes: list[dict[str, str]]) -> list[str]:
    applied = []
    for fix in fixes:
        rel = fix.get("file", "")
        search = fix.get("search", "")
        replace = fix.get("replace", "")
        if not rel or not search:
            continue
        path = ROOT / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        if search not in content:
            continue
        path.write_text(content.replace(search, replace, 1), encoding="utf-8")
        applied.append(rel)
    return applied


def bump_version(version: str) -> str:
    if version.startswith("v"):
        parts = version[1:].split(".")
        while len(parts) < 3:
            parts.append("0")
        parts[-1] = str(int(parts[-1]) + 1)
        return "v" + ".".join(parts)
    return f"v{version}.1"


def format_fitness_table(
    fitness: dict[str, float], prev: dict[str, float] | None = None
) -> str:
    lines = ["| Dimension | Score | Δ |", "|-----------|-------|---|"]
    for dim in FITNESS_DIMS:
        score = fitness.get(dim, 0)
        delta = ""
        if prev and dim in prev:
            d = round(score - prev[dim], 1)
            delta = f"+{d}" if d > 0 else str(d)
        lines.append(f"| {dim} | {score} | {delta} |")
    return "\n".join(lines)


def write_latest_summary(
    state: dict[str, Any],
    data: dict[str, Any],
    prev_score: float,
    tokens_this_gen: int,
) -> str:
    gen = state["generation"]
    new_score = state["best_score"]
    delta = round(new_score - prev_score, 1)
    delta_str = f"+{delta}" if delta > 0 else str(delta)

    content = f"""# SEPLE Iteration Summary — Generation {gen}

**Score:** {prev_score} → {new_score} ({delta_str}) | **Version:** {state['version']} | **Tokens this gen:** {tokens_this_gen:,}

## What Changed
{chr(10).join(f'- {i}' for i in data.get('improvements', [])) or '- (initial evaluation)'}

## Reflection
> {data.get('reflection', 'No reflection provided.')}

## Fitness Snapshot
{format_fitness_table(state['fitness_scores'], data.get('_prev_fitness'))}

## Successes This Gen
{chr(10).join(f'- {s}' for s in data.get('successes', [])) or '- —'}

## Problems / Risks
{chr(10).join(f'- {p}' for p in data.get('problems', [])) or '- —'}

## Next Focus
{chr(10).join(f'- {n}' for n in data.get('next_focus', [])) or '- Continue iterative refinement'}
"""
    write_text(LATEST_SUMMARY_FILE, content)
    return content


def append_evolution_log(
    state: dict[str, Any],
    data: dict[str, Any],
    prev_score: float,
    tokens_this_gen: int,
    commit_hash: str | None,
) -> None:
    gen = state["generation"]
    delta = round(state["best_score"] - prev_score, 1)
    entry = f"""
## Generation {gen}

**Timestamp:** {utc_now()}  
**Score:** {prev_score} → {state['best_score']} ({'+' if delta > 0 else ''}{delta})  
**Version:** {state['version']}  
**Tokens:** {tokens_this_gen:,} (cumulative: {state['tokens_used']:,})  
**Commit:** `{commit_hash or 'none'}`

### Improvements
{chr(10).join(f'- {i}' for i in data.get('improvements', []))}

### Reflection
{data.get('reflection', '')}

### Fitness
{format_fitness_table(state['fitness_scores'])}

### Successes
{chr(10).join(f'- {s}' for s in data.get('successes', []))}

### Problems
{chr(10).join(f'- {p}' for p in data.get('problems', []))}

---
"""
    with EVOLUTION_FILE.open("a", encoding="utf-8") as f:
        f.write(entry)


def print_iteration_summary(
    state: dict[str, Any],
    data: dict[str, Any],
    prev_score: float,
    tokens_this_gen: int,
    commit_hash: str | None,
) -> None:
    gen = state["generation"]
    delta = round(state["best_score"] - prev_score, 1)
    sign = "+" if delta > 0 else ""
    bar = "═" * 60
    print(f"\n{bar}")
    print(f"  🧬 SEPLE Generation {gen} Complete")
    print(f"{bar}")
    print(f"  Score:  {prev_score} → {state['best_score']} ({sign}{delta})")
    print(f"  Version: {state['version']}")
    print(f"  Tokens: {tokens_this_gen:,} this gen | {state['tokens_used']:,} total")
    print(f"  Commit: {commit_hash or '—'}")
    print()
    for imp in data.get("improvements", [])[:3]:
        print(f"  ✦ {imp}")
    if data.get("problems"):
        print(f"  ⚠ {data['problems'][0]}")
    print(f"{bar}\n")


def run_self_fix(llm: LLMClient, error: Exception, state: dict[str, Any]) -> bool:
    tb = traceback.format_exc()
    source_files = ["seple_runner.py", "seple_llm.py"]
    file_contents = ""
    for name in source_files:
        path = ROOT / name
        if path.exists():
            file_contents += f"\n### {name}\n```python\n{path.read_text(encoding='utf-8')[:8000]}\n```\n"

    system = """You are a Python debugging expert. Diagnose the error and provide exact search/replace patches.
Return ONLY valid JSON:
{"diagnosis": "string", "patches": [{"file": "seple_runner.py", "search": "exact text", "replace": "fixed text"}]}"""
    user = f"""Error in SEPLE runner:
```
{tb}
```
{file_contents}
Provide minimal patches to fix the error."""

    try:
        response = llm.chat(system, user, temperature=0.2)
        fix_data = extract_json(response.content)
        applied = apply_code_fixes(fix_data.get("patches", []))
        state["tokens_used"] += response.total_tokens
        state["self_fix_count"] = state.get("self_fix_count", 0) + 1
        state["self_fix_events"].append({
            "timestamp": utc_now(),
            "error": str(error),
            "diagnosis": fix_data.get("diagnosis", ""),
            "patches_applied": applied,
            "tokens": response.total_tokens,
        })
        save_state(state)
        if applied:
            git_commit(f"self-fix: {fix_data.get('diagnosis', 'auto patch')[:72]}")
            print(f"  🔧 Self-fix applied: {fix_data.get('diagnosis', '')[:80]}")
            return True
    except Exception as fix_err:
        print(f"  ❌ Self-fix failed: {fix_err}")
    return False


def run_generation(llm: LLMClient, state: dict[str, Any]) -> None:
    gen_start = time.time()
    state["generation"] += 1
    state["status"] = "running"
    if not state.get("started_at"):
        state["started_at"] = utc_now()

    prev_score = state["best_score"]
    prev_fitness = dict(state["fitness_scores"])
    meta = state.get("_meta_improvements", "")
    if meta:
        del state["_meta_improvements"]

    system = build_evolution_system_prompt(state, meta)
    user = build_evolution_user_prompt().replace("{next_gen}", str(state["generation"]))

    data, response = parse_evolution_response(llm, system, user, state)
    data["_prev_fitness"] = prev_fitness

    tokens_this_gen = response.total_tokens
    state["tokens_this_gen"] = tokens_this_gen
    state["tokens_used"] += tokens_this_gen
    state["token_history"].append({
        "gen": state["generation"],
        "input": response.input_tokens,
        "output": response.output_tokens,
        "total": response.total_tokens,
        "timestamp": utc_now(),
    })

    new_score = float(data["best_score"])
    state["previous_score"] = prev_score
    state["fitness_scores"] = {k: float(data["fitness_scores"].get(k, 0)) for k in FITNESS_DIMS}
    state["score_history"].append({"gen": state["generation"], "score": new_score})
    state["reflections"].append({
        "gen": state["generation"],
        "text": data.get("reflection", ""),
    })
    state["reflections"] = state["reflections"][-20:]

    improved = new_score > prev_score or state["generation"] == 1
    if improved and data.get("new_prompt"):
        write_text(BEST_PROMPT_FILE, data["new_prompt"])
        state["version"] = bump_version(state["version"])
        state["best_score"] = new_score
    elif new_score > state["best_score"]:
        state["best_score"] = new_score

    if state["best_score"] >= 98:
        state["consecutive_high_scores"] = state.get("consecutive_high_scores", 0) + 1
    else:
        state["consecutive_high_scores"] = 0

    applied_fixes = apply_code_fixes(data.get("code_fixes", []))
    state["last_summary"] = {
        "score_delta": round(state["best_score"] - prev_score, 1),
        "successes": data.get("successes", []),
        "problems": data.get("problems", []),
        "improvements": data.get("improvements", []),
    }
    if data.get("next_focus"):
        state["evolution_insights"] = (state.get("evolution_insights", []) + data["next_focus"])[-10:]

    state["last_generation_duration_s"] = round(time.time() - gen_start, 2)
    state["llm_provider"] = llm.provider
    state["llm_model"] = llm.model
    save_state(state)

    write_latest_summary(state, data, prev_score, tokens_this_gen)
    delta = round(state["best_score"] - prev_score, 1)
    commit_msg = (
        f"gen-{state['generation']:03d}: score {prev_score}→{state['best_score']} "
        f"({'+' if delta > 0 else ''}{delta}) — {data.get('version_notes', 'evolution')[:50]}"
    )
    commit_hash = git_commit(commit_msg)
    append_evolution_log(state, data, prev_score, tokens_this_gen, commit_hash)
    print_iteration_summary(state, data, prev_score, tokens_this_gen, commit_hash)

    if applied_fixes:
        git_commit(f"gen-{state['generation']:03d}: code fixes — {', '.join(applied_fixes)}")


def build_checkpoint_prompt(state: dict[str, Any]) -> tuple[str, str]:
    system = """You are SEPLE v5 evolution analyst. Produce a comprehensive checkpoint report.
Return ONLY valid JSON:
{
  "evolution_narrative": "narrative of how evolution progressed",
  "successes": ["list"],
  "problems": ["list"],
  "insights": ["list"],
  "plateaus_detected": ["list"],
  "recommendations_for_next_phase": ["list"],
  "suggested_meta_improvements": "tweaks to evolution strategy for next 50 gens",
  "overall_grade": "letter grade",
  "continue_worth_it": true
}"""
    initial = read_text(INITIAL_PROMPT_FILE) or read_text(BEST_PROMPT_FILE)
    current = read_text(BEST_PROMPT_FILE)
    user = f"""Checkpoint at generation {state['generation']}.

## Stats
- Best score: {state['best_score']}
- Score history: {json.dumps(state.get('score_history', []))}
- Fitness: {json.dumps(state['fitness_scores'])}
- Tokens used: {state['tokens_used']:,}
- Self-fixes: {state.get('self_fix_count', 0)}
- Reflections: {json.dumps(state.get('reflections', [])[-10:])}

## Initial prompt length: {len(initial)} chars
## Current prompt length: {len(current)} chars

## Current best prompt (excerpt)
{current[:3000]}

Analyze the full evolution journey and return JSON."""
    return system, user


def write_checkpoint_report(state: dict[str, Any], data: dict[str, Any], tokens: int) -> None:
    content = f"""# SEPLE Checkpoint Report — Generation {state['generation']}

**Generated:** {utc_now()}  
**Overall Grade:** {data.get('overall_grade', 'N/A')}  
**Best Score:** {state['best_score']}  
**Tokens Used:** {state['tokens_used']:,} (+{tokens:,} for this report)  
**Continue Worth It:** {data.get('continue_worth_it', True)}

---

## Evolution Narrative

{data.get('evolution_narrative', 'No narrative provided.')}

## Successes

{chr(10).join(f'- ✅ {s}' for s in data.get('successes', [])) or '- —'}

## Problems

{chr(10).join(f'- ⚠️ {p}' for p in data.get('problems', [])) or '- —'}

## Insights

{chr(10).join(f'- 💡 {i}' for i in data.get('insights', [])) or '- —'}

## Plateaus Detected

{chr(10).join(f'- 📉 {p}' for p in data.get('plateaus_detected', [])) or '- None detected'}

## Recommendations for Next Phase

{chr(10).join(f'- 🎯 {r}' for r in data.get('recommendations_for_next_phase', [])) or '- —'}

## Suggested Meta-Improvements

{data.get('suggested_meta_improvements', 'None')}
"""
    write_text(CHECKPOINT_FILE, content)

    summary_header = f"""# SEPLE CHECKPOINT — Generation {state['generation']}

> {data.get('evolution_narrative', '')[:300]}...

See [checkpoint_report.md](checkpoint_report.md) for full analysis.

**Grade:** {data.get('overall_grade', 'N/A')} | **Score:** {state['best_score']} | **Tokens:** {state['tokens_used']:,}

"""
    existing = read_text(LATEST_SUMMARY_FILE)
    write_text(LATEST_SUMMARY_FILE, summary_header + existing)


def run_checkpoint(llm: LLMClient, state: dict[str, Any]) -> dict[str, Any]:
    system, user = build_checkpoint_prompt(state)
    response = llm.chat(system, user, temperature=0.5)
    data = extract_json(response.content)

    state["tokens_used"] += response.total_tokens
    state["tokens_this_gen"] += response.total_tokens
    state["token_history"].append({
        "gen": state["generation"],
        "type": "checkpoint",
        "total": response.total_tokens,
        "timestamp": utc_now(),
    })
    state["checkpoints_completed"] = state.get("checkpoints_completed", 0) + 1
    state["evolution_insights"].extend(data.get("insights", []))
    save_state(state)

    write_checkpoint_report(state, data, response.total_tokens)

    bar = "═" * 60
    print(f"\n{bar}")
    print(f"  📊 CHECKPOINT: Generation {state['generation']} Complete")
    print(f"{bar}")
    print(f"  Grade: {data.get('overall_grade', 'N/A')} | Score: {state['best_score']}")
    print(f"  Tokens: {state['tokens_used']:,}")
    print()
    print(f"  {data.get('evolution_narrative', '')[:500]}")
    print()
    for s in data.get("successes", [])[:3]:
        print(f"  ✅ {s}")
    for p in data.get("problems", [])[:3]:
        print(f"  ⚠️  {p}")
    print(f"\n  Full report: checkpoint_report.md")
    print(f"{bar}\n")

    return data


def handle_checkpoint(llm: LLMClient, state: dict[str, Any]) -> dict[str, Any] | None:
    """Run checkpoint report and prompt user to continue or stop."""
    checkpoint_data = run_checkpoint(llm, state)
    state = load_state()
    meta = checkpoint_data.get("suggested_meta_improvements", "")
    if meta:
        state["_meta_improvements"] = f"## Phase Meta-Improvements\n{meta}"
        save_state(state)

    if not prompt_continue(state):
        if load_state().get("status") == "awaiting_continue":
            return None
        create_github_issue(load_state())
        return None
    return checkpoint_data


def prompt_continue(state: dict[str, Any]) -> bool:
    state["awaiting_continue"] = True
    state["status"] = "awaiting_continue"
    save_state(state)

    next_cap = state["max_generation"] + 50
    prompt_text = (
        f"\nContinue evolving for 50 more generations (up to gen {next_cap})? [y/N]: "
    )

    if sys.stdin.isatty():
        answer = input(prompt_text).strip().lower()
        cont = answer in ("y", "yes")
    else:
        write_text(
            CONTINUE_PROMPT_FILE,
            json.dumps({
                "awaiting_user": True,
                "generation": state["generation"],
                "max_generation": state["max_generation"],
                "prompt": f"Continue to gen {next_cap}?",
                "timestamp": utc_now(),
            }, indent=2),
        )
        print(
            "\n  ⏸  Non-interactive mode: awaiting decision.\n"
            "  Run: python seple_runner.py --resume y   (or --resume n)\n"
        )
        return False  # caller should exit

    state["awaiting_continue"] = False
    if cont:
        state["max_generation"] += 50
        state["status"] = "running"
        save_state(state)
        git_commit(f"checkpoint-{state['generation']}: continue to gen {state['max_generation']}")
        print(f"  ▶ Continuing to generation {state['max_generation']}...")
    else:
        state["status"] = "stopped"
        state["stop_reason"] = "user_stop_at_checkpoint"
        save_state(state)
        print("  ⏹ Stopping evolution per user request.")
    return cont


def handle_resume(choice: str) -> bool:
    if not CONTINUE_PROMPT_FILE.exists() and not load_state().get("awaiting_continue"):
        print("No pending continue prompt.")
        return False

    state = load_state()
    cont = choice.lower() in ("y", "yes")
    state["awaiting_continue"] = False

    if cont:
        state["max_generation"] += 50
        state["status"] = "running"
        save_state(state)
        if CONTINUE_PROMPT_FILE.exists():
            CONTINUE_PROMPT_FILE.unlink()
        git_commit(f"checkpoint-{state['generation']}: continue to gen {state['max_generation']}")
        print(f"  ▶ Resumed — target gen {state['max_generation']}")
        return True

    state["status"] = "stopped"
    state["stop_reason"] = "user_stop_at_checkpoint"
    save_state(state)
    if CONTINUE_PROMPT_FILE.exists():
        CONTINUE_PROMPT_FILE.unlink()
    return False


def create_github_issue(state: dict[str, Any]) -> None:
    token = os.getenv("GITHUB_TOKEN")
    owner = os.getenv("GITHUB_OWNER", "tokenwaster76")
    repo = os.getenv("GITHUB_REPO", "seple-best-loop-evolver")

    best_prompt = read_text(BEST_PROMPT_FILE)
    checkpoint = read_text(CHECKPOINT_FILE)
    score_table = "\n".join(
        f"| {e['gen']} | {e['score']} |" for e in state.get("score_history", [])[-20:]
    )

    body = f"""## SEPLE v5 Evolution Complete

**Stop reason:** {state.get('stop_reason', 'completed')}  
**Generations:** {state['generation']}  
**Best score:** {state['best_score']}  
**Version:** {state['version']}  
**Tokens used:** {state['tokens_used']:,}  
**Self-fixes:** {state.get('self_fix_count', 0)}  
**Checkpoints:** {state.get('checkpoints_completed', 0)}

### Score History (last 20)
| Gen | Score |
|-----|-------|
{score_table}

### Checkpoint Report
{checkpoint[:4000]}

### Best Prompt
<details>
<summary>best_prompt.md</summary>

{best_prompt}
</details>
"""

    local_summary = ROOT / "COMPLETION_SUMMARY.md"
    write_text(local_summary, body)

    if not token:
        print("  ⚠ GITHUB_TOKEN not set — wrote COMPLETION_SUMMARY.md locally.")
        return

    import requests

    resp = requests.post(
        f"https://api.github.com/repos/{owner}/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "title": f"SEPLE v5 Complete — Gen {state['generation']}, Score {state['best_score']}",
            "body": body,
            "labels": ["seple", "evolution-complete"],
        },
        timeout=30,
    )
    if resp.status_code == 201:
        issue = resp.json()
        print(f"  📝 GitHub issue created: {issue.get('html_url')}")
    else:
        print(f"  ⚠ GitHub issue failed ({resp.status_code}): {resp.text[:200]}")


def should_stop(state: dict[str, Any]) -> bool:
    if state.get("consecutive_high_scores", 0) >= 2:
        state["stop_reason"] = "score_threshold_98_x2"
        return True
    if state["generation"] >= state["max_generation"]:
        return False  # checkpoint handles this
    return False


def reset_state() -> None:
    _ensure_fresh_state()
    # Overwrite logs for a true reset
    write_text(LATEST_SUMMARY_FILE, "# SEPLE Iteration Summary\n\n**Status:** Reset complete.\n")
    write_text(CHECKPOINT_FILE, "# SEPLE Checkpoint Report\n\nNo checkpoint yet.\n")
    # Also reset best prompt to the seed if the user wants a full clean start
    # (we keep the shipped best_prompt.md as the canonical seed)
    print("State reset to fresh initial (gen 0).")


def main() -> None:
    parser = argparse.ArgumentParser(description="SEPLE v5 — Self-Evolving Prompt Loop Engineer")
    parser.add_argument("--once", action="store_true", help="Run a single generation")
    parser.add_argument("--reset", action="store_true", help="Reset state to initial template")
    parser.add_argument("--resume", type=str, metavar="y|n", help="Resume after checkpoint prompt")
    args = parser.parse_args()

    if args.reset:
        reset_state()
        return

    if args.resume:
        if handle_resume(args.resume):
            pass
        else:
            state = load_state()
            create_github_issue(state)
            return

    ensure_git_repo()
    if not BEST_PROMPT_FILE.exists():
        # Ship a minimal viable seed so first evolution has something real to improve
        seed = """# SEPLE Meta-System Prompt v1.0.0

You are SEPLE v5, an autonomous prompt improver.

## Task
Analyze the provided current best prompt, score it on the rubric, and return an improved full prompt + JSON.

## Rubric
clarity, specificity, robustness, iterability, self_awareness, error_recovery (0-100).

Return ONLY the required JSON with full "new_prompt".
"""
        write_text(BEST_PROMPT_FILE, seed)

    if not INITIAL_PROMPT_FILE.exists():
        write_text(INITIAL_PROMPT_FILE, read_text(BEST_PROMPT_FILE))

    llm = LLMClient()
    state = load_state()

    if state.get("awaiting_continue"):
        print("Awaiting continue decision. Use --resume y or --resume n")
        return

    sleep_s = float(os.getenv("SEPLE_GEN_SLEEP", "3"))
    start_gen = state["generation"]

    print(f"\n🚀 SEPLE v5 starting — provider={llm.provider} model={llm.model}")
    print(f"   Target: gen {state['max_generation']} | current: gen {state['generation']}\n")

    while True:
        state = load_state()

        if args.once and state["generation"] > start_gen:
            break

        if not args.once and state["generation"] >= state["max_generation"]:
            if state["generation"] > 0 and state["generation"] % 50 == 0:
                if handle_checkpoint(llm, state) is None:
                    return
                continue
            break

        try:
            run_generation(llm, state)
            state = load_state()
        except Exception as exc:
            print(f"\n❌ Error in generation: {exc}")
            if run_self_fix(llm, exc, load_state()):
                continue
            state = load_state()
            state["status"] = "error"
            state["stop_reason"] = str(exc)
            save_state(state)
            raise

        if should_stop(state):
            state["status"] = "stopped"
            save_state(state)
            create_github_issue(state)
            print(f"\n🏁 Stopped: {state['stop_reason']}")
            return

        if not args.once:
            time.sleep(sleep_s)

    state = load_state()
    if state["status"] == "running":
        state["status"] = "completed"
        state["stop_reason"] = state.get("stop_reason") or "generation_batch_complete"
        save_state(state)
    print(f"\n✅ SEPLE run finished — gen {state['generation']}, score {state['best_score']}")


if __name__ == "__main__":
    main()