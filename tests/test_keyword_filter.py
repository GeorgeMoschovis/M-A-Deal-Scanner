"""The keyword filter that runs before any article is sent to Claude."""

import pytest

import extract
import scrapers


def article(title, text=""):
    return scrapers.Article("https://example.com/a", title, text, "test")


@pytest.mark.parametrize("title", [
    "ΔΕΗ: Ολοκληρώθηκε η απόκτηση των εταιρειών της ABO Energy",
    "Εξαγορά της Vernicos Yachts",
    "ΕΞΑΓΟΡΑ ΤΗΣ ΑΚΡΙΤΑΣ",
    "Pearson Acquires Assessment Technology Provider",
    "Bank7 to buy Century Financial in merger",
])
def test_deal_articles_pass_in_greek_and_english_regardless_of_accents_or_case(title):
    assert extract.is_candidate(article(title), extensive=False)


def test_unrelated_articles_are_dropped():
    unrelated = article("Ρεύμα: Φόβοι για υψηλές τιμές", "Οι τιμές ανεβαίνουν τον Οκτώβριο")
    assert not extract.is_candidate(unrelated, extensive=False)
    assert not extract.is_candidate(unrelated, extensive=True)


def test_ipo_news_only_passes_in_extensive_mode():
    ipo = article("Westinghouse IPO valuation")
    assert not extract.is_candidate(ipo, extensive=False)
    assert extract.is_candidate(ipo, extensive=True)


def test_only_the_lead_of_the_article_is_checked():
    late_mention = article("Market wrap", "filler " * 1000 + "acquisition")
    assert not extract.is_candidate(late_mention, extensive=False)
