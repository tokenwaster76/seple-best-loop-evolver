# SEPLE v5 — Self-Evolving Prompt Loop Engineer

Autonomous, **100% real** self-improving prompt engineering loop for [tokenwaster76](https://github.com/tokenwaster76).

Every generation makes an actual LLM API call. Token counts come from real API `usage` fields. Errors trigger real LLM-generated patches with git commits.

## Features

- **Real LLM evolution** every generation (OpenRouter default, also Grok/Ollama/OpenAI/Claude)
- **Live file-backed state** — `state.json`, `best_prompt.md`, `EVOLUTION.md`, `latest_summary.md`
- **Rich TUI dashboard** — fitness bars, sparklines, token rates, CPU/memory
- **Per-iteration summaries** — file + terminal + TUI + evolution log
- **Gen-50 checkpoints** — LLM evolution report (insights, problems, successes) + continue/stop
- **Self-fix** — LLM diagnoses errors, applies patches, commits
- **Git auto-commit** every generation
- **GitHub issue** on completion with full summary

## Quick Start

```bash
cd seple-best-loop-evolver
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Required for default OpenRouter provider
export OPENROUTER_API_KEY="your-openrouter-api-key"

# Optional — for GitHub issue on completion
export GITHUB_TOKEN="your-github-token"

# Terminal 1 — run the evolution loop
python seple_runner.py

# Terminal 2 — watch live (or tmux split)
python seple_tui_dashboard.py
```

### Single generation (test)

```bash
python seple_runner.py --once
```

### Reset state

```bash
python seple_runner.py --reset
```

## LLM Configuration

**Strong default**: Ollama + `llama3.1` when no cloud API keys are present (local-first, matches spec).

| Variable | Default (auto) | Description |
|----------|----------------|-------------|
| `SEPLE_LLM_PROVIDER` | `ollama` (if no keys) | `openrouter`, `grok`, `openai`, `anthropic`, `ollama` |
| `SEPLE_MODEL` | `llama3.1` (or provider default) | Model slug |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API base URL |
| `XAI_API_KEY` | — | Grok/xAI API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `SEPLE_GEN_SLEEP` | `3` | Seconds between generations |
| `SEPLE_DASH_REFRESH` | `1.5` | Dashboard refresh interval |

### OpenRouter (recommended)

```bash
export OPENROUTER_API_KEY="sk-or-..."
export SEPLE_LLM_PROVIDER=openrouter
export SEPLE_MODEL="google/gemini-2.5-flash"   # or any OpenRouter model slug
python seple_runner.py
```

Browse models at [openrouter.ai/models](https://openrouter.ai/models).

### Ollama (local)

```bash
# Install Ollama, then:
ollama pull llama3.1
export SEPLE_LLM_PROVIDER=ollama
export SEPLE_MODEL=llama3.1
python seple_runner.py
```

## Checkpoint at Generation 50

At gen 50, 100, 150… the runner:

1. Makes an extra LLM call for a full **evolution checkpoint report**
2. Writes `checkpoint_report.md` with insights, successes, problems
3. Pauses and asks: **Continue 50 more generations? [y/N]**

Non-interactive (SSH detach):

```bash
# Runner writes continue_prompt.json and exits
python seple_runner.py --resume y   # continue
python seple_runner.py --resume n   # stop + GitHub issue
```

## Stop Conditions

- **User chooses `n`** at a checkpoint
- **`best_score >= 98`** for 2 consecutive generations → auto-stop + GitHub issue
- Dynamic cap starts at 50, extends by 50 each time you continue

## Files

| File | Purpose |
|------|---------|
| `seple_runner.py` | Core autonomous loop |
| `seple_llm.py` | LLM providers + token tracking |
| `seple_tui_dashboard.py` | Live Rich TUI |
| `state.json` | Persisted evolution state |
| `best_prompt.md` | Current best prompt (evolves) |
| `EVOLUTION.md` | Full generation log |
| `latest_summary.md` | Current iteration summary |
| `checkpoint_report.md` | Gen-50+ evolution analysis |

## tmux Example

```bash
tmux new-session -d -s seple
tmux split-window -h
tmux send-keys -t 0 'cd seple-best-loop-evolver && source .venv/bin/activate && python seple_runner.py' Enter
tmux send-keys -t 1 'cd seple-best-loop-evolver && source .venv/bin/activate && python seple_tui_dashboard.py' Enter
tmux attach -t seple
```

## License

MIT — built for autonomous prompt engineering research.