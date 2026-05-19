"""Tests for quant.feature_store.pit_join.asof_left_join.

Plan completion criterion (Month 2):
    pytest tests/test_pit_join.py passes
    — deliberate future-data query must return NaN, not a value.

The tests verify the core guarantee:
    Any feature row timestamped AFTER the event's inference_date
    CANNOT appear in the join result.

This is the single structural safeguard against lookahead bias.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.feature_store.pit_join import asof_left_join


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_features(rows: list[dict]) -> pd.DataFrame:
    """Build a features DataFrame from a list of dicts."""
    return pd.DataFrame(rows)


def _make_events(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ── Core PIT correctness ──────────────────────────────────────────────────────

class TestPITCorrectness:
    """The most important tests: future data must never appear in results."""

    def test_future_feature_returns_nan(self):
        """Feature available AFTER inference_date must produce NaN — never a value."""
        features = _make_features([
            {
                "symbol": "RELIANCE",
                "data_available_at": "2023-06-20",  # future relative to event
                "alpha_signal": 0.42,
            }
        ])
        events = _make_events([
            {
                "symbol": "RELIANCE",
                "inference_date": "2023-06-15",  # event is BEFORE feature availability
                "event_col": "earnings",
            }
        ])

        result = asof_left_join(features, events)

        assert len(result) == 1
        assert np.isnan(result.loc[0, "alpha_signal"]), (
            "Feature timestamped after inference_date must be NaN in the join. "
            "This is the primary lookahead-bias guard."
        )

    def test_same_day_feature_is_included(self):
        """Feature available on the SAME day as inference_date must be included."""
        features = _make_features([
            {"symbol": "TCS", "data_available_at": "2023-06-15", "alpha_signal": 0.7}
        ])
        events = _make_events([
            {"symbol": "TCS", "inference_date": "2023-06-15", "event_col": "e"}
        ])

        result = asof_left_join(features, events)

        assert len(result) == 1
        assert not np.isnan(result.loc[0, "alpha_signal"])
        assert abs(result.loc[0, "alpha_signal"] - 0.7) < 1e-9

    def test_most_recent_prior_feature_is_used(self):
        """When multiple prior feature rows exist, the most recent one wins."""
        features = _make_features([
            {"symbol": "INFY", "data_available_at": "2023-06-01", "alpha_signal": 0.1},
            {"symbol": "INFY", "data_available_at": "2023-06-10", "alpha_signal": 0.5},
            {"symbol": "INFY", "data_available_at": "2023-06-20", "alpha_signal": 0.9},
        ])
        events = _make_events([
            {"symbol": "INFY", "inference_date": "2023-06-15", "event_col": "e"}
        ])

        result = asof_left_join(features, events)

        # Row dated 2023-06-10 is the most recent valid one (< 2023-06-15)
        assert abs(result.loc[0, "alpha_signal"] - 0.5) < 1e-9

    def test_no_valid_prior_feature_returns_nan(self):
        """If ALL feature rows are after inference_date, every feature col is NaN."""
        features = _make_features([
            {"symbol": "HDFC", "data_available_at": "2023-07-01", "alpha_signal": 1.0},
            {"symbol": "HDFC", "data_available_at": "2023-08-01", "alpha_signal": 2.0},
        ])
        events = _make_events([
            {"symbol": "HDFC", "inference_date": "2023-06-01", "event_col": "e"}
        ])

        result = asof_left_join(features, events)

        assert len(result) == 1
        assert np.isnan(result.loc[0, "alpha_signal"])


# ── Multiple symbols ───────────────────────────────────────────────────────────

class TestMultipleSymbols:
    def test_each_symbol_gets_its_own_feature(self):
        """Symbols must not contaminate each other."""
        features = _make_features([
            {"symbol": "RELIANCE", "data_available_at": "2023-06-10", "f": 1.0},
            {"symbol": "TCS",      "data_available_at": "2023-06-10", "f": 2.0},
        ])
        events = _make_events([
            {"symbol": "RELIANCE", "inference_date": "2023-06-15", "ev": "a"},
            {"symbol": "TCS",      "inference_date": "2023-06-15", "ev": "b"},
        ])

        result = asof_left_join(features, events)

        reliance_row = result[result["symbol"] == "RELIANCE"].iloc[0]
        tcs_row = result[result["symbol"] == "TCS"].iloc[0]
        assert abs(reliance_row["f"] - 1.0) < 1e-9
        assert abs(tcs_row["f"] - 2.0) < 1e-9

    def test_symbol_with_no_features_gets_nan(self):
        """A symbol that has no features should get NaN, not an error."""
        features = _make_features([
            {"symbol": "RELIANCE", "data_available_at": "2023-06-10", "f": 1.0},
        ])
        events = _make_events([
            {"symbol": "RELIANCE", "inference_date": "2023-06-15", "ev": "a"},
            {"symbol": "WIPRO",    "inference_date": "2023-06-15", "ev": "b"},
        ])

        result = asof_left_join(features, events)

        wipro_row = result[result["symbol"] == "WIPRO"].iloc[0]
        assert np.isnan(wipro_row["f"])

    def test_mixed_pit_validity_across_symbols(self):
        """Valid feature for one symbol, future feature for another → correct NaN split."""
        features = _make_features([
            {"symbol": "A", "data_available_at": "2023-06-10", "f": 99.0},  # valid
            {"symbol": "B", "data_available_at": "2023-06-20", "f": 88.0},  # future
        ])
        events = _make_events([
            {"symbol": "A", "inference_date": "2023-06-15", "ev": "x"},
            {"symbol": "B", "inference_date": "2023-06-15", "ev": "y"},
        ])

        result = asof_left_join(features, events)

        a_row = result[result["symbol"] == "A"].iloc[0]
        b_row = result[result["symbol"] == "B"].iloc[0]
        assert abs(a_row["f"] - 99.0) < 1e-9, "valid PIT feature should be present"
        assert np.isnan(b_row["f"]),            "future feature must be NaN"


# ── Multiple feature columns ──────────────────────────────────────────────────

class TestMultipleFeatureColumns:
    def test_all_feature_cols_joined_or_nan(self):
        """All feature columns must appear in result; NaN if no valid row."""
        features = _make_features([
            {
                "symbol": "ICICI",
                "data_available_at": "2023-06-10",
                "feat_a": 1.0,
                "feat_b": 2.0,
                "feat_c": 3.0,
            }
        ])
        events = _make_events([
            {"symbol": "ICICI", "inference_date": "2023-06-15", "ev": "e"},
            {"symbol": "ICICI", "inference_date": "2023-06-05", "ev": "f"},  # before feature
        ])

        result = asof_left_join(features, events)

        valid = result[result["inference_date"] == pd.Timestamp("2023-06-15")].iloc[0]
        assert abs(valid["feat_a"] - 1.0) < 1e-9
        assert abs(valid["feat_b"] - 2.0) < 1e-9
        assert abs(valid["feat_c"] - 3.0) < 1e-9

        invalid = result[result["inference_date"] == pd.Timestamp("2023-06-05")].iloc[0]
        assert np.isnan(invalid["feat_a"])
        assert np.isnan(invalid["feat_b"])
        assert np.isnan(invalid["feat_c"])


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_features_all_nan(self):
        """Empty features DataFrame → all feature columns are NaN in result."""
        features = pd.DataFrame(
            columns=["symbol", "data_available_at", "alpha_signal"]
        )
        events = _make_events([
            {"symbol": "X", "inference_date": "2023-06-15", "ev": "e"}
        ])
        result = asof_left_join(features, events)
        assert len(result) == 1
        assert np.isnan(result.loc[0, "alpha_signal"])

    def test_empty_events_returns_empty(self):
        """Empty events DataFrame → empty result (no error)."""
        features = _make_features([
            {"symbol": "X", "data_available_at": "2023-06-10", "alpha_signal": 1.0}
        ])
        events = pd.DataFrame(columns=["symbol", "inference_date", "ev"])
        result = asof_left_join(features, events)
        assert len(result) == 0

    def test_row_order_preserved(self):
        """Result rows are in the same order as the input events."""
        features = _make_features([
            {"symbol": "A", "data_available_at": "2023-01-01", "f": 1.0},
            {"symbol": "B", "data_available_at": "2023-01-01", "f": 2.0},
            {"symbol": "C", "data_available_at": "2023-01-01", "f": 3.0},
        ])
        # Events in Z → A → B order intentionally
        events = _make_events([
            {"symbol": "C", "inference_date": "2023-06-01", "ev": "c"},
            {"symbol": "A", "inference_date": "2023-06-01", "ev": "a"},
            {"symbol": "B", "inference_date": "2023-06-01", "ev": "b"},
        ])

        result = asof_left_join(features, events)

        assert list(result["symbol"]) == ["C", "A", "B"]
        assert list(result["ev"]) == ["c", "a", "b"]

    def test_feature_time_col_not_in_result(self):
        """``data_available_at`` should NOT appear in the result if it was not
        an original event column (it's a join artefact, not user data)."""
        features = _make_features([
            {"symbol": "X", "data_available_at": "2023-06-01", "score": 5.0}
        ])
        events = _make_events([
            {"symbol": "X", "inference_date": "2023-06-15", "ev": "e"}
        ])
        result = asof_left_join(features, events)
        assert "data_available_at" not in result.columns
