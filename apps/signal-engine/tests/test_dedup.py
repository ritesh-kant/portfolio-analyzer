"""Unit tests for news article deduplication hashing.

Tests the _make_hash and _make_story_hash functions from rss.py
— no network, no DB.
"""

from src.scrapers.rss import _make_hash, _make_story_hash


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


class TestMakeStoryHash:
    """story_hash collapses the SAME story across multiple sources/feeds.

    Used at the signal layer to ensure ET + Moneycontrol + LiveMint all
    reporting the same RBI announcement collapse into one nt_signals row.
    """

    def test_deterministic_same_input(self) -> None:
        assert _make_story_hash("RBI cuts repo rate") == _make_story_hash("RBI cuts repo rate")

    def test_same_headline_different_source_same_story_hash(self) -> None:
        # The whole point of this hash — source must NOT affect it.
        h_et = _make_story_hash("RBI cuts repo rate by 25bp")
        h_mc = _make_story_hash("RBI cuts repo rate by 25bp")
        assert h_et == h_mc

    def test_topic_hash_vs_story_hash_diverge_on_source(self) -> None:
        # Same headline, two sources → same story_hash but different topic_hash.
        # This is what allows the audit log (nt_news_raw) to keep one row per
        # source while the signal layer dedupes to a single event.
        headline = "RBI keeps rates unchanged"
        t1 = _make_hash(headline, "Economic Times")
        t2 = _make_hash(headline, "Moneycontrol")
        s1 = _make_story_hash(headline)
        s2 = _make_story_hash(headline)
        assert t1 != t2
        assert s1 == s2

    def test_case_insensitive(self) -> None:
        assert _make_story_hash("RBI Cuts Rates") == _make_story_hash("rbi cuts rates")

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert _make_story_hash("  Nifty closes above 25000  ") == _make_story_hash("Nifty closes above 25000")

    def test_internal_whitespace_collapsed(self) -> None:
        # Reuters/PTI wire copy often double-spaces around punctuation.
        assert _make_story_hash("Nifty   closes  above 25000") == _make_story_hash("Nifty closes above 25000")

    def test_different_headlines_different_hash(self) -> None:
        assert _make_story_hash("Reliance Q1 profit up 25%") != _make_story_hash("Reliance Q1 profit down 25%")

    def test_hash_length_is_24_chars(self) -> None:
        assert len(_make_story_hash("Anything")) == 24

    def test_hash_is_hex_string(self) -> None:
        h = _make_story_hash("Some headline")
        assert all(c in "0123456789abcdef" for c in h)
