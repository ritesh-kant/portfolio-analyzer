"""Unit tests for news article deduplication hashing.

Tests the _make_hash function from rss.py — no network, no DB.
"""

from src.scrapers.rss import _make_hash


class TestMakeHash:
    def test_deterministic_same_inputs(self) -> None:
        h1 = _make_hash("Reliance Q1 profit surges 25%", "Economic Times")
        h2 = _make_hash("Reliance Q1 profit surges 25%", "Economic Times")
        assert h1 == h2

    def test_different_headline_produces_different_hash(self) -> None:
        h1 = _make_hash("Reliance Q1 profit surges 25%", "Economic Times")
        h2 = _make_hash("Infosys Q2 revenue misses estimates", "Economic Times")
        assert h1 != h2

    def test_different_source_produces_different_hash(self) -> None:
        h1 = _make_hash("Same headline", "Economic Times")
        h2 = _make_hash("Same headline", "Moneycontrol")
        assert h1 != h2

    def test_hash_length_is_24_chars(self) -> None:
        h = _make_hash("Test headline", "Source")
        assert len(h) == 24

    def test_hash_is_hex_string(self) -> None:
        h = _make_hash("Test headline", "Source")
        assert all(c in "0123456789abcdef" for c in h)

    def test_leading_trailing_whitespace_stripped(self) -> None:
        h1 = _make_hash("  Nifty closes above 25000  ", "ET")
        h2 = _make_hash("Nifty closes above 25000", "ET")
        assert h1 == h2

    def test_case_insensitive(self) -> None:
        h1 = _make_hash("NIFTY CLOSES ABOVE 25000", "ET")
        h2 = _make_hash("nifty closes above 25000", "ET")
        assert h1 == h2

    def test_mixed_case_normalised(self) -> None:
        h1 = _make_hash("Reliance AGM: record dividend", "ET")
        h2 = _make_hash("RELIANCE AGM: RECORD DIVIDEND", "ET")
        assert h1 == h2

    def test_empty_headline_does_not_raise(self) -> None:
        h = _make_hash("", "ET")
        assert len(h) == 24

    def test_unique_hashes_for_similar_headlines(self) -> None:
        h1 = _make_hash("Nifty rises 1%", "ET")
        h2 = _make_hash("Nifty rises 2%", "ET")
        assert h1 != h2
