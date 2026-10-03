export default {
  "apps/web/**/*.{ts,tsx}": () => "pnpm --filter web typecheck",
  "apps/portfolio-service/**/*.{ts,tsx}": () => "pnpm --filter portfolio-service typecheck",
  "apps/analytics-service/**/*.{ts,tsx}": () => "pnpm --filter analytics-service typecheck",
  "packages/shared-types/**/*.{ts,tsx}": () => "pnpm --filter @portfolio-analyzer/shared-types typecheck",
  "packages/auth-utils/**/*.{ts,tsx}": () => "pnpm --filter @portfolio-analyzer/auth-utils typecheck",
  "packages/analytics-core/**/*.{ts,tsx}": () => "pnpm --filter @portfolio-analyzer/analytics-core typecheck",
  "packages/broker-sdk/**/*.{ts,tsx}": () => "pnpm --filter @portfolio-analyzer/broker-sdk typecheck",
  "packages/tax-core/**/*.{ts,tsx}": () => "pnpm --filter @portfolio-analyzer/tax-core typecheck",
  // backtest-dashboard conventions: registry format + the agent instruction files (stdlib python, no venv)
  "{research/backtests/dashboard_runs.json,AGENTS.md,CLAUDE.md,GEMINI.md,.clinerules,.github/copilot-instructions.md,.cursor/rules/*.mdc}":
    () => "python3 research/backtests/check_dashboard_registry.py",
};
