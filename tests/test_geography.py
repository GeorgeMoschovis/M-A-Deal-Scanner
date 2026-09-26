"""Items with no link to any selected geography are left out; even a slight link keeps them."""

from datetime import date

import pytest

from ma_deal_finder import extract
from helpers import FakeClient, deal_json, make_article

WINDOW = date(2026, 9, 12)
GREECE = {"Greece"}


def run(payload, geographies=GREECE, key="article"):
    client = FakeClient({key: payload})
    result = extract.extract_deal(client, make_article(key), False, set(), extract.Usage(), WINDOW,
                                  geographies)
    return result, client


def deal(geographies):
    return deal_json("BP", "Devon Eagle Ford", "BP Weighs Devon Eagle Ford Assets", "2026-09-20",
                     geographies=geographies)


def test_model_can_flag_an_item_outside_the_selected_geographies():
    (item, skipped), _ = run({"relevant": False, "outside_geographies": True})
    assert (item, skipped) == (None, extract.OUTSIDE_GEOGRAPHIES)


def test_unlinked_deal_is_caught_by_code_even_if_the_model_writes_it_up():
    (item, skipped), _ = run(deal(["United States"]))
    assert (item, skipped) == (None, extract.OUTSIDE_GEOGRAPHIES)


def test_a_slight_link_to_a_selected_geography_is_enough():
    (item, skipped), _ = run(deal(["United States", "Greece"]))
    assert item is not None and skipped == ""


def test_any_of_several_selected_geographies_counts():
    (item, skipped), _ = run(deal(["United Kingdom"]), geographies={"Greece", "United Kingdom"})
    assert item is not None and skipped == ""


@pytest.mark.parametrize("geographies", [None, [], "Greece", ["Cyprus"]])
def test_missing_or_unreadable_geographies_are_kept(geographies):
    (item, skipped), _ = run(deal(geographies))
    assert item is not None and skipped == ""
    assert item["geographies"] == []


def test_unknown_labels_are_dropped_and_known_ones_kept():
    item = extract.polish_deal(deal(["Cyprus", "Rest of Europe", "Other"]), make_article("a"), False)
    assert item["geographies"] == ["Rest of Europe", "Other"]


def test_selected_geographies_are_sent_to_the_model():
    _, client = run(deal(["Greece"]), geographies={"United Kingdom", "Greece"})
    assert 'GEOGRAPHIES: ["Greece", "United Kingdom"]' in client.extraction_prompts[0]


def test_without_a_selection_nothing_is_filtered_by_region():
    (item, skipped), client = run(deal(["United States"]), geographies=())
    assert item is not None and skipped == ""
    assert "GEOGRAPHIES" not in client.extraction_prompts[0]
