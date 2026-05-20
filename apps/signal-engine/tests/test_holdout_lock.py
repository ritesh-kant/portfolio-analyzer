"""Tests for quant.research.holdout_lock.

Plan completion criterion (Month 2):
    Attempting to read hold-out parquet without lock-release env var raises.

The hold-out lock is the most important infrastructure safeguard in the
rebuild.  These tests verify default-deny: any code that touches hold-out
dates without an explicit unlock raises immediately.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from quant.research.holdout_lock import (
    HOLDOUT_START,
    DEV_END,
    assert_no_holdout_access,
    read_holdout,
)


# ── assert_no_holdout_access ──────────────────────────────────────────────────

class TestAssertNoHoldoutAccess:
    """assert_no_holdout_access must raise for hold-out dates, pass otherwise."""

    def test_holdout_start_date_raises(self):
        """The first hold-out date (2024-07-01) must raise."""
        with pytest.raises(ValueError, match="HOLD-OUT VIOLATION"):
            assert_no_holdout_access(HOLDOUT_START)

    def test_date_after_holdout_start_raises(self):
        """Any date after 2024-07-01 must raise."""
        with pytest.raises(ValueError, match="HOLD-OUT VIOLATION"):
            assert_no_holdout_access("2025-01-15")

    def test_dev_end_does_not_raise(self):
        """The last dev date (2024-06-30) must NOT raise."""
        assert_no_holdout_access(DEV_END)  # should not raise

    def test_train_date_does_not_raise(self):
        """A training set date must not raise."""
        assert_no_holdout_access("2020-06-15")

    def test_boundary_day_before_holdout_ok(self):
        """2024-06-30 (one day before hold-out) must not raise."""
        assert_no_holdout_access("2024-06-30")

    def test_accepts_pandas_timestamp(self):
        """Should accept pd.Timestamp as well as str."""
        # Should not raise
        assert_no_holdout_access(pd.Timestamp("2024-06-30"))
        # Should raise
        with pytest.raises(ValueError, match="HOLD-OUT VIOLATION"):
            assert_no_holdout_access(pd.Timestamp("2024-07-01"))

    def test_error_message_contains_holdout_start(self):
        """Error message should mention the hold-out start date for diagnosis."""
        with pytest.raises(ValueError, match=HOLDOUT_START):
            assert_no_holdout_access("2024-07-01")


# ── read_holdout ──────────────────────────────────────────────────────────────

class TestReadHoldout:
    """read_holdout must enforce the three-precondition gate."""

    @pytest.fixture
    def valid_hypothesis(self, tmp_path: Path) -> Path:
        """A well-formed hypothesis file with ``final: true``."""
        f = tmp_path / "2024-01-15-pead-midcap.md"
        f.write_text(
            textwrap.dedent("""\
                # Hypothesis: PEAD on Nifty Midcap 150

                final: true

                ## Hypothesis
                After a positive earnings surprise > 1σ on midcap stocks,
                price drifts +80–120 bps over 5 trading days.

                ## Falsification
                If mean 5-day drift < 40 bps or DSR < 0.5, strategy is killed.
            """),
            encoding="utf-8",
        )
        return f

    @pytest.fixture
    def draft_hypothesis(self, tmp_path: Path) -> Path:
        """A hypothesis file WITHOUT ``final: true`` — still a draft."""
        f = tmp_path / "2024-01-15-pead-draft.md"
        f.write_text(
            textwrap.dedent("""\
                # Hypothesis: PEAD draft

                ## Hypothesis
                TBD

                ## Falsification
                TBD
            """),
            encoding="utf-8",
        )
        return f

    def test_raises_if_env_var_not_set(self, valid_hypothesis: Path):
        """Without QUANT_HOLDOUT_UNLOCK env var, read_holdout must refuse."""
        # Ensure env var is not set
        os.environ.pop("QUANT_HOLDOUT_UNLOCK", None)

        with pytest.raises(PermissionError, match="QUANT_HOLDOUT_UNLOCK"):
            read_holdout("pead_midcap", valid_hypothesis)

    def test_raises_if_env_var_wrong_strategy(self, valid_hypothesis: Path, monkeypatch):
        """Env var set to a different strategy name must refuse."""
        monkeypatch.setenv("QUANT_HOLDOUT_UNLOCK", "wrong_strategy")

        with pytest.raises(PermissionError, match="pead_midcap"):
            read_holdout("pead_midcap", valid_hypothesis)

    def test_raises_if_hypothesis_not_final(self, draft_hypothesis: Path, monkeypatch):
        """Hypothesis without ``final: true`` must refuse unlock."""
        monkeypatch.setenv("QUANT_HOLDOUT_UNLOCK", "pead_midcap")

        with pytest.raises(ValueError, match="final: true"):
            read_holdout("pead_midcap", draft_hypothesis)

    def test_raises_if_hypothesis_file_missing(self, tmp_path: Path, monkeypatch):
        """Non-existent hypothesis file must raise FileNotFoundError."""
        monkeypatch.setenv("QUANT_HOLDOUT_UNLOCK", "pead_midcap")

        with pytest.raises(FileNotFoundError):
            read_holdout("pead_midcap", tmp_path / "nonexistent.md")

    def test_succeeds_with_correct_env_and_final_hypothesis(
        self, valid_hypothesis: Path, tmp_path: Path, monkeypatch
    ):
        """With correct env var and final hypothesis, read_holdout must succeed."""
        monkeypatch.setenv("QUANT_HOLDOUT_UNLOCK", "pead_midcap")
        # Isolate from the real MLflow store so no prior run is visible
        monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path}/mlflow.db")

        # Should not raise
        read_holdout("pead_midcap", valid_hypothesis)

    def test_final_flag_is_case_insensitive(self, tmp_path: Path, monkeypatch):
        """``Final: True`` (capitalised) should also be accepted."""
        f = tmp_path / "hyp.md"
        f.write_text("Final: True\n\nSome hypothesis content.", encoding="utf-8")
        monkeypatch.setenv("QUANT_HOLDOUT_UNLOCK", "strategy_x")
        # Isolate from the real MLflow store
        monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path}/mlflow2.db")

        # Should not raise
        read_holdout("strategy_x", f)


# ── Integration: builder refuses hold-out dates ───────────────────────────────

class TestBuilderGuard:
    """Feature builders must call assert_no_holdout_access; these tests verify
    that the guard propagates correctly to user-facing code."""

    def test_momentum_builder_refuses_holdout_date(self):
        """momentum_features with a hold-out date must raise."""
        import pandas as pd
        from quant.features.builder import momentum_features

        # Construct a minimal OHLCV with multi-index
        dates = pd.bdate_range("2024-01-01", periods=30)
        prices = pd.Series([100.0 + i for i in range(30)], index=dates)
        df = pd.DataFrame({"close": prices, "symbol": "TEST"})
        df = df.set_index([df.index, df["symbol"]]).drop(columns=["symbol"])
        df.index.names = ["business_date", "symbol"]
        df["turnover_lacs"] = 1000.0

        with pytest.raises(ValueError, match="HOLD-OUT VIOLATION"):
            momentum_features(df, "TEST", as_of_date="2024-08-01")

    def test_turnover_builder_refuses_holdout_date(self):
        """turnover_features with a hold-out date must raise."""
        import pandas as pd
        from quant.features.builder import turnover_features

        dates = pd.bdate_range("2024-01-01", periods=30)
        df = pd.DataFrame({
            "symbol": "TEST",
            "turnover_lacs": [1000.0 + i for i in range(30)],
        }, index=dates)
        df.index.name = "business_date"

        with pytest.raises(ValueError, match="HOLD-OUT VIOLATION"):
            turnover_features(df, "TEST", as_of_date="2024-09-01")
