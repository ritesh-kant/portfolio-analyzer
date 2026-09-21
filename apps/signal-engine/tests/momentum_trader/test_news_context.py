"""Pure filtering logic for the descriptive news annotation — no network calls.

Covers `select_matches` only: symbol matching (NSE's exact `nse_symbol` field
vs. word-boundary text search for RSS/BSE), the lookback window, and result
ordering/capping. `recent_news`'s scraper fan-out is exercised end-to-end by
the scanner, not unit-tested here (same convention as ledger.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.momentum_trader.news_context import MAX_ITEMS, select_matches

AT = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


def _article(**kw):
    base = {
        "headline": "Some headline",
        "raw_text": "Some headline. Some body.",
        "source": "Test Source",
        "publisher": "Test Publisher",
        "tier": "tier1",
        "url": "https://example.com",
        "published_at": AT - timedelta(hours=1),
    }
    base.update(kw)
    return base


def test_nse_symbol_field_matches_exactly_even_without_text_mention():
    articles = [_article(headline="Board meeting outcome", raw_text="unrelated text",
                          nse_symbol="RELIANCE")]
    matches = select_matches(articles, "RELIANCE", AT)
    assert len(matches) == 1


def test_rss_text_match_is_word_boundary_not_substring():
    # "TCS" must not match inside an unrelated longer token.
    articles = [_article(raw_text="MYTCSCORP reported results today")]
    assert select_matches(articles, "TCS", AT) == []

    articles = [_article(raw_text="TCS reported results today")]
    assert len(select_matches(articles, "TCS", AT)) == 1


def test_symbol_match_is_case_insensitive():
    articles = [_article(raw_text="reliance shares rallied")]
    assert len(select_matches(articles, "RELIANCE", AT)) == 1


def test_no_match_for_unrelated_symbol():
    articles = [_article(raw_text="Infosys wins a large deal")]
    assert select_matches(articles, "TCS", AT) == []


def test_article_outside_lookback_window_is_excluded():
    articles = [_article(raw_text="RELIANCE news", published_at=AT - timedelta(hours=25))]
    assert select_matches(articles, "RELIANCE", AT, lookback=timedelta(hours=24)) == []


def test_article_published_after_at_is_excluded():
    articles = [_article(raw_text="RELIANCE news", published_at=AT + timedelta(minutes=5))]
    assert select_matches(articles, "RELIANCE", AT) == []


def test_article_with_missing_timestamp_is_dropped():
    articles = [_article(raw_text="RELIANCE news", published_at=None)]
    assert select_matches(articles, "RELIANCE", AT) == []


def test_naive_published_at_is_treated_as_utc():
    articles = [_article(raw_text="RELIANCE news", published_at=AT.replace(tzinfo=None) - timedelta(hours=1))]
    assert len(select_matches(articles, "RELIANCE", AT)) == 1


def test_results_sorted_newest_first_and_capped():
    articles = [
        _article(raw_text="RELIANCE news", published_at=AT - timedelta(hours=h))
        for h in range(1, MAX_ITEMS + 5)
    ]
    matches = select_matches(articles, "RELIANCE", AT)
    assert len(matches) == MAX_ITEMS
    timestamps = [m["published_at"] for m in matches]
    assert timestamps == sorted(timestamps, reverse=True)


def test_output_shape_is_display_only_fields():
    articles = [_article(raw_text="RELIANCE news")]
    [match] = select_matches(articles, "RELIANCE", AT)
    assert set(match) == {"headline", "source", "publisher", "tier", "url", "published_at"}
