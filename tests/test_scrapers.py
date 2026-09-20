"""A source that breaks is skipped and reported; it never aborts the run."""

from datetime import datetime, timezone

import pytest

from ma_deal_finder import scrapers

SINCE = datetime(2026, 9, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("source, scraper_name", [
    (scrapers.GREEK_SOURCES[0], "scrape_html"),
    (scrapers.GLOBENEWSWIRE, "scrape_feed"),
    (scrapers.EDGAR, "scrape_edgar"),
])
@pytest.mark.parametrize("error", [IndexError("empty ciks list"), TypeError("odd shape"),
                                   AttributeError("missing tag"), RuntimeError("anything")])
def test_any_scraper_error_becomes_a_note_instead_of_a_crash(monkeypatch, source, scraper_name, error):
    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr(scrapers, scraper_name, broken)
    articles, note = scrapers.collect(source, SINCE, 5, "me@example.com")
    assert articles == []
    assert note == f"failed ({type(error).__name__})"


def test_a_working_scraper_result_is_passed_through(monkeypatch):
    article = scrapers.Article("https://example.com/a", "T", "body", "Euro2day")
    monkeypatch.setattr(scrapers, "scrape_html", lambda src, since, n: ([article], ""))
    assert scrapers.collect(scrapers.GREEK_SOURCES[0], SINCE, 5) == ([article], "")
