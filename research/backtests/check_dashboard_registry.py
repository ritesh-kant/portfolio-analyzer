"""Validate the backtest-dashboard conventions. Standard library only, so it runs
anywhere (git hook, CI, a fresh clone with no venv):

    python3 research/backtests/check_dashboard_registry.py

Checks
  * dashboard_runs.json is valid JSON and every entry has a non-empty `bt` and
    `title`; a `hypothesis` path, when given, exists in the repo
  * every coding-agent instruction file exists and still names the dashboard
    workflow, so Claude Code, Copilot, Cline, Cursor, Gemini CLI, Codex … all get
    the same rule (the full text lives in AGENTS.md; the others are short pointers)

Whether every run on disk is LABELLED needs the CSVs, which are not in git —
that check is `pnpm bt:check`, run it after a backtest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "research" / "backtests" / "dashboard_runs.json"

# every file a coding agent may read its project rules from, and what it must still say
AGENT_FILES = [
    "AGENTS.md",                              # Codex, Copilot agent, Cline, Cursor, Windsurf, Aider, …
    "CLAUDE.md",                              # Claude Code
    "GEMINI.md",                              # Gemini CLI
    ".github/copilot-instructions.md",        # GitHub Copilot (chat / code review)
    ".clinerules",                            # Cline
    ".cursor/rules/backtest-reports.mdc",     # Cursor
]
MUST_MENTION = ("dashboard_runs.json", "bt:dashboard", "bt:check", "--tag")


def main() -> int:
    errors: list[str] = []

    if not REGISTRY.exists():
        errors.append(f"missing {REGISTRY.relative_to(ROOT)}")
    else:
        try:
            reg = json.loads(REGISTRY.read_text())
        except json.JSONDecodeError as e:
            errors.append(f"{REGISTRY.name} is not valid JSON: {e}")
            reg = {}
        for key, val in reg.items():
            if key.startswith("_"):
                continue
            if not isinstance(val, dict):
                errors.append(f"{key}: value must be an object with bt + title")
                continue
            for field in ("bt", "title"):
                if not str(val.get(field) or "").strip():
                    errors.append(f"{key}: missing '{field}'")
            hyp = val.get("hypothesis")
            if hyp and not (ROOT / hyp).exists():
                errors.append(f"{key}: hypothesis file not found: {hyp}")
        for lst in ("_legacy_unlabelled", "_legacy_not_chartable"):
            v = reg.get(lst, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                errors.append(f"{lst} must be a list of CSV stems")

    for rel in AGENT_FILES:
        f = ROOT / rel
        if not f.exists():
            errors.append(f"missing agent instruction file: {rel}")
            continue
        text = f.read_text()
        for needle in MUST_MENTION:
            if needle not in text and "@AGENTS.md" not in text:
                errors.append(f"{rel} no longer mentions '{needle}' (or imports AGENTS.md)")

    for e in errors:
        print(f"✗ {e}")
    if errors:
        return 1
    print("✓ dashboard registry and agent instruction files are consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
