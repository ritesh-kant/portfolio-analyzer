"""Keyword-based institutional entity classifier for NSE bulk deal client names.

Pre-registered in hypothesis: research/hypotheses/2026-05-23-bdm-institutional.md
The keyword list (§6) is immutable once any MLflow experiment begins.

Usage
-----
  from quant.data.entity_classifier import is_institutional, classify_events

  # Single entity
  is_institutional("HDFC MUTUAL FUND - HDFC BALANCED ADVANTAGE FUND")  # True
  is_institutional("ANIL KUMAR SHARMA")                                  # False

  # Classify a bulk deal events DataFrame (from bdm.build_events)
  events = classify_events(events_df)
  institutional = events[events["is_institutional"]]
"""

from __future__ import annotations

import pandas as pd

# ── Pre-registered keyword list (immutable after first MLflow run) ─────────────
# Hypothesis §6: entity is INSTITUTIONAL if client_name (uppercased) contains
# any of the following strings.  Case-insensitive match.
_INSTITUTIONAL_KEYWORDS: tuple[str, ...] = (
    "MUTUAL FUND",
    " MF ",
    "MF-",
    "FII",
    "FPI",
    "INSURANCE",
    "INSUR",
    "PENSION",
    "PROVIDENT FUND",
    "PROVIDENT",
    " FUND",
    "LIFE INSURANCE",
    "GENERAL INSURANCE",
    "ASSET MANAGEMENT",
    "AMC",
    "INVESTMENT TRUST",
    "NATIONAL PENSION",
    "EMPLOYEES' STATE INSURANCE",
    "LIC",
    "NEW INDIA ASSURANCE",
    "UNITED INDIA INSURANCE",
    "SBI LIFE",
    "HDFC LIFE",
    "ICICI PRUDENTIAL LIFE",
    "KOTAK MAHINDRA LIFE",
)


def is_institutional(client_name: str) -> bool:
    """Return True if the client name matches any pre-registered institutional keyword.

    Parameters
    ----------
    client_name : str
        Buyer/seller entity name from NSE bulk deal disclosure (free text).

    Returns
    -------
    bool
        True = classified as institutional (mutual fund / FII / insurance / pension).
        False = not institutional (retail HNI, promoter, corporate treasury, etc.).

    Notes
    -----
    This is a conservative classifier: it may miss some institutional entities
    whose names do not contain the registered keywords (false negatives are
    acceptable).  False positives (classifying retail as institutional) would
    hurt Sharpe and are avoided.

    Examples
    --------
    >>> is_institutional("HDFC MUTUAL FUND - HDFC BALANCED ADVANTAGE FUND")
    True
    >>> is_institutional("MOTILAL OSWAL FII")
    True
    >>> is_institutional("LIC OF INDIA")
    True
    >>> is_institutional("ANIL KUMAR SHARMA")
    False
    >>> is_institutional("RELIANCE INDUSTRIES LTD")
    False
    """
    name_upper = str(client_name).strip().upper()
    return any(kw.upper() in name_upper for kw in _INSTITUTIONAL_KEYWORDS)


def classify_events(events: pd.DataFrame) -> pd.DataFrame:
    """Add `is_institutional` boolean column to a bulk deal events DataFrame.

    The events DataFrame must have a `client_names` column (pipe-separated
    entity names, as produced by bdm.build_events()).

    An event is classified as institutional if ANY of the buyer entity names
    matches the keyword list.

    Parameters
    ----------
    events : pd.DataFrame
        Output of quant.strategies.bdm.build_events().
        Expected columns: symbol, event_date, total_value_cr, client_names.

    Returns
    -------
    DataFrame with an additional `is_institutional` column.
    """
    if events.empty:
        events = events.copy()
        events["is_institutional"] = pd.Series(dtype=bool)
        return events

    def _event_is_institutional(client_names_str: str) -> bool:
        # client_names is pipe-separated: "HDFC MF | ICICI PRUDENTIAL LIFE | ..."
        names = str(client_names_str).split("|")
        return any(is_institutional(name.strip()) for name in names)

    events = events.copy()
    events["is_institutional"] = events["client_names"].apply(_event_is_institutional)
    return events
