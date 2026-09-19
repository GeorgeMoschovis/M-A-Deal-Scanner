"""Deals announced or completed before the look-back window are left out."""

from datetime import date

import pytest

import extract
from helpers import FakeClient, deal_json, make_article

WINDOW = date(2026, 9, 12)


def run(payload, key="article"):
    client = FakeClient({key: payload})
    result = extract.extract_deal(client, make_article(key), False, set(), extract.Usage(), WINDOW)
    return result, client


def test_is_stale_only_for_known_dates_before_the_window():
    assert extract.is_stale({"event_date": "2026-09-11"}, WINDOW) is True
    assert extract.is_stale({"event_date": "2026-09-12"}, WINDOW) is False
    assert extract.is_stale({"event_date": "2026-09-18"}, WINDOW) is False
    assert extract.is_stale({"event_date": ""}, WINDOW) is False


def test_model_can_flag_an_old_deal_as_stale():
    (deal, stale), _ = run({"relevant": False, "stale": True})
    assert (deal, stale) == (None, True)


def test_old_event_date_is_caught_by_code_even_if_the_model_writes_it_up():
    payload = deal_json("Yfantis", "Nikas", "Yfantis Acquires Nikas from Bespoke SGA", "2025-12-02")
    (deal, stale), _ = run(payload)
    assert (deal, stale) == (None, True)


def test_completion_inside_the_window_is_kept():
    payload = deal_json("PPC", "ABO Energy", "PPC Completes ABO Energy Acquisition", "2026-09-18")
    (deal, stale), _ = run(payload)
    assert stale is False
    assert deal["event_date"] == "2026-09-18"


@pytest.mark.parametrize("event_date", [None, "2025-12", "yesterday"])
def test_unknown_or_unclear_dates_are_kept(event_date):
    payload = deal_json("A", "B", "A Buys B", event_date)
    (deal, stale), _ = run(payload)
    assert deal is not None and stale is False
    assert deal["event_date"] == ""


def test_window_start_is_sent_to_the_model():
    payload = deal_json("A", "B", "A Buys B", None)
    _, client = run(payload)
    assert "WINDOW_START: 2026-09-12" in client.extraction_prompts[0]


def test_irrelevant_article_is_neither_a_deal_nor_stale():
    (deal, stale), _ = run({"relevant": False})
    assert (deal, stale) == (None, False)
