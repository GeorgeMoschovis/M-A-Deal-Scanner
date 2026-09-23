"""A source that breaks is skipped and reported; it never aborts the run. Sources are scraped in parallel."""

import threading
import time
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


def test_results_follow_source_order_even_when_sources_finish_out_of_order(monkeypatch):
    sources = scrapers.GREEK_SOURCES[:4]
    delays = {src.key: 0.05 * (len(sources) - i) for i, src in enumerate(sources)}  # first finishes last

    def fake_collect(src, since, n, contact=""):
        time.sleep(delays[src.key])
        return [], src.key
    monkeypatch.setattr(scrapers, "collect", fake_collect)
    finished = []
    results = scrapers.collect_all(sources, SINCE, 5, on_done=lambda src, a, note: finished.append(src.key))
    assert [note for _, note in results] == [src.key for src in sources]
    assert finished == [src.key for src in reversed(sources)]


def test_several_sources_are_scraped_at_the_same_time(monkeypatch):
    lock, running, peak = threading.Lock(), [0], [0]

    def fake_collect(src, since, n, contact=""):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return [], ""
    monkeypatch.setattr(scrapers, "collect", fake_collect)
    scrapers.collect_all(scrapers.GREEK_SOURCES, SINCE, 5)
    assert peak[0] == scrapers.SOURCE_WORKERS


def test_a_failing_source_does_not_stop_the_others(monkeypatch):
    article = scrapers.Article("https://example.com/a", "T", "body", "Capital.gr")

    def scrape_html(src, since, n):
        if src.key == "euro2day":
            raise RuntimeError("broken markup")
        return [article], ""
    monkeypatch.setattr(scrapers, "scrape_html", scrape_html)
    results = scrapers.collect_all(scrapers.GREEK_SOURCES[:2], SINCE, 5)
    assert results == [([], "failed (RuntimeError)"), ([article], "")]
