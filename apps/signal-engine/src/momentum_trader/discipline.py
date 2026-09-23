"""Account-level guardrails — the transcript's "Risk Management & Position
Sizing" rules, which act on the DAY rather than on any one symbol.

Everything else in this package decides one symbol at a time: is this chart a
bull flag, is the volume heavy enough, where is the stop. These four rules
cannot be expressed there, because each of them is a statement about the
trader's running results across every symbol:

  * **3 strikes.** "Walk away for the day after 3 consecutive losses."
  * **Profit protection.** "Walk away if you give back 50% of your peak daily
    profit."
  * **Starter size.** "Start the day with a small/starter position. Increase to
    full size only after building a green profit cushion."
  * **Size down after a loss.** "Always decrease share size following a loss" —
    the transcript's counter to revenge trading.

So they live here, own a single mutable `DayDiscipline` per session, and the
scanner consults it in two places: before arming an entry (may the day trade at
all?) and when sizing one (how many rupees of risk is this trade allowed?).

Size is expressed as a FRACTION of the configured full risk, never as a share
count, because the rest of the system sizes by rupees-at-risk ÷ stop distance
(`risk.plan_trade`). Halving the fraction halves the rupees at risk, which is
what "smaller position" means when stops are of different widths.

The ladder, stated once so it cannot drift:

    start of day          → starter_frac        (half size)
    a winner, day green   → double, capped at 1 (full size, cushion earned)
    a winner, day red     → unchanged           (a cushion is a POSITIVE balance)
    a loser               → halve, floored      (never increase after a loss)

Nothing here is a threshold fitted to results: every number is quoted verbatim
from the transcript (3 strikes, 50% give-back) or is the plain reading of
"small/starter" and "decrease" (half, floored at a quarter so size cannot decay
to zero and silently stop the day without saying so).

This module is deliberately free of I/O and of pandas: it is a small state
machine over closed-trade P&L, so the scanner can drive it live and a test can
drive it with a list of numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── frozen parameters (quoted from the transcript) ───────────────────────────
MAX_CONSECUTIVE_LOSSES = 3   # "3-Strikes Rule: walk away after 3 consecutive losses"
GIVEBACK_FRAC = 0.50         # "walk away if you give back 50% of your peak daily profit"
STARTER_FRAC = 0.50          # "start the day with a small/starter position"
LOSS_SIZE_FRAC = 0.50        # "always decrease share size following a loss"
MIN_SIZE_FRAC = 0.25         # floor, so repeated losses shrink size without reaching zero
FULL_SIZE_FRAC = 1.0

HALT_THREE_STRIKES = "three_strikes"
HALT_GIVEBACK = "profit_giveback"


@dataclass(frozen=True)
class DisciplineConfig:
    """Off by default: a backtest that replays a symbol in isolation has no
    concept of a trading day's running P&L, so enabling this by default would
    make pool backtests depend on the order symbols happen to be processed in."""

    enabled: bool = False
    # The 50% give-back halt can be switched off on its own while the strikes
    # rule and size ladder stay on (operator decision 2026-09-23: one winner
    # then one loser was enough to end the day).
    giveback_halt: bool = True
    max_consecutive_losses: int = MAX_CONSECUTIVE_LOSSES
    giveback_frac: float = GIVEBACK_FRAC
    starter_frac: float = STARTER_FRAC
    loss_size_frac: float = LOSS_SIZE_FRAC
    min_size_frac: float = MIN_SIZE_FRAC

    def __post_init__(self) -> None:
        if self.max_consecutive_losses < 1:
            raise ValueError("max_consecutive_losses must be >= 1")
        for name in ("giveback_frac", "starter_frac", "loss_size_frac", "min_size_frac"):
            v = float(getattr(self, name))
            if not 0.0 < v <= 1.0:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.min_size_frac > self.starter_frac:
            raise ValueError("min_size_frac cannot exceed starter_frac")


@dataclass
class DayDiscipline:
    """Running state for one trading day. One instance per scanner process."""

    cfg: DisciplineConfig = field(default_factory=DisciplineConfig)
    net_inr: float = 0.0
    peak_inr: float = 0.0
    consecutive_losses: int = 0
    trades: int = 0
    wins: int = 0
    halted_reason: str = ""
    size_frac: float = field(init=False)

    def __post_init__(self) -> None:
        self.size_frac = self.cfg.starter_frac if self.cfg.enabled else FULL_SIZE_FRAC

    # ── queries ──────────────────────────────────────────────────────────────

    @property
    def halted(self) -> bool:
        return bool(self.halted_reason)

    def can_trade(self) -> tuple[bool, str]:
        """(allowed, reason). Reason is "ok" while trading is permitted."""
        if not self.cfg.enabled or not self.halted_reason:
            return True, "ok"
        return False, self.halted_reason

    def risk_inr(self, full_risk_inr: float) -> float:
        """Rupees of risk this trade may take, after the size ladder."""
        if not self.cfg.enabled:
            return full_risk_inr
        return full_risk_inr * self.size_frac

    # ── mutation ─────────────────────────────────────────────────────────────

    def record(self, net_inr: float) -> None:
        """Fold one CLOSED trade's net P&L into the day, then re-check the halts.

        A trade that nets exactly zero counts as a loss: it did not build the
        cushion the size ladder is waiting for, and after real costs an exact
        scratch is a stop-out that happened to round.
        """
        self.trades += 1
        self.net_inr += float(net_inr)
        self.peak_inr = max(self.peak_inr, self.net_inr)

        if net_inr > 0.0:
            self.wins += 1
            self.consecutive_losses = 0
            if self.cfg.enabled and self.net_inr > 0.0:
                # A green cushion exists → step up, never past full size.
                self.size_frac = min(FULL_SIZE_FRAC, self.size_frac * 2.0)
        else:
            self.consecutive_losses += 1
            if self.cfg.enabled:
                self.size_frac = max(
                    self.cfg.min_size_frac, self.size_frac * self.cfg.loss_size_frac
                )

        if not self.cfg.enabled or self.halted_reason:
            return
        if self.consecutive_losses >= self.cfg.max_consecutive_losses:
            self.halted_reason = HALT_THREE_STRIKES
        elif (self.cfg.giveback_halt and self.peak_inr > 0.0
              and self.net_inr <= self.peak_inr * self.cfg.giveback_frac):
            # Only meaningful once the day HAS been profitable: you cannot give
            # back profit you never made, so a day that opens red never trips it.
            self.halted_reason = HALT_GIVEBACK

    # ── reporting ────────────────────────────────────────────────────────────

    def summary(self) -> str:
        state = self.halted_reason or "active"
        return (f"{self.trades} trades, {self.wins}W/{self.trades - self.wins}L, "
                f"net ₹{self.net_inr:,.0f}, peak ₹{self.peak_inr:,.0f}, "
                f"size {self.size_frac:.0%}, streak {self.consecutive_losses}L, {state}")
