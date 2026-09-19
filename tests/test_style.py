"""House-style rules that code enforces after Claude replies."""

import pytest

from ma_deal_finder import extract
from helpers import deal_json, make_article


@pytest.mark.parametrize("raw, expected", [
    ("Kyklos acquired Akritas S.A. for €5m", "Kyklos acquired Akritas for €5m"),
    ("Foo, Inc. bought Bar Ltd", "Foo bought Bar"),
    ("Alpha A.E.D.A.K.E.S. holds 5%", "Alpha holds 5%"),
    ("It bought Foo Inc. The deal closes.", "It bought Foo. The deal closes."),
    ("Sold to a USA buyer", "Sold to a USA buyer"),
    ("Revenue - 2024: €38.3m", "Revenue – 2024: €38.3m"),
    ("Revenue — 2024", "Revenue – 2024"),
    ("2023—24", "2023–24"),
    ("2023-24", "2023–24"),
    ("plus FY-24 net debt", "plus FY-24 net debt"),
    ("approximately €5m", "c. €5m"),
    ("€2.5 m and $3 bn", "€2.5m and $3bn"),
    ("€2.5 million", "€2.5m"),
    ("€ 2.5m", "€2.5m"),
    ("EUR 45m", "€45m"),
    ("EV/EBITDA – LTM: 10.7x", "EV/EBITDA – LTM: 10.70x"),
    ("10.75 x", "10.75x"),
    ("Net Income – 2024: -€20m", "Net Income – 2024: (€20m)"),
    ("Net Income – 2024: –€20m", "Net Income – 2024: (€20m)"),
    ("Leverage: -2.5x", "Leverage: (2.50x)"),
    ("(€20m)", "(€20m)"),
    ("between €2m–€3m", "between €2m–€3m"),
    ("Revenue – €38m", "Revenue – €38m"),
], ids=[
    "strip S.A.", "strip Inc. with comma", "strip A.E.D.A.K.E.S.", "keep sentence-ending period",
    "leave USA alone", "spaced hyphen to en dash", "spaced em dash", "tight em dash",
    "digit range", "FY-24 hyphen kept", "approximately", "unit spacing", "million word",
    "currency spacing", "EUR code", "multiple two decimals", "multiple spaced x",
    "negative hyphen", "negative en dash", "negative multiple", "already in parentheses",
    "range is not negative", "label dash is not negative",
])
def test_polish_text(raw, expected):
    assert extract.polish_text(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("kyklos participations reaches 95.96% stake in akritas",
     "Kyklos Participations Reaches 95.96% Stake in Akritas"),
    ("PPC Sells 51% to Aktor for €120m", "PPC Sells 51% to Aktor for €120m"),
    ("acquires post-merger entity", "Acquires Post-Merger Entity"),
    ("a fund buys stake", "A Fund Buys Stake"),
])
def test_title_case(raw, expected):
    assert extract.title_case(raw) == expected


def raw_deal():
    return deal_json(
        "Kyklos Participations S.A.", "Akritas S.A.",
        "kyklos participations reaches 95.96% stake in akritas S.A.", "2026-09-16",
        industry_header="wood processing & building materials",
        description="Kyklos acquired 95.96% of Akritas S.A. for approximately €12 million. Two. Three.",
        deal_financials=[{"label": "Implied EV", "value": "EUR 45.2 m"},
                         {"label": "EV/EBITDA", "value": "n/a"},
                         {"label": "EV/Sales", "value": "1.1 x"}],
        deal_footnotes=["Implied equity value plus FY-24 net debt"],
        target={"description": "Akritas makes wood panels.",
                "financials": [{"label": "Revenue - 2024", "value": "€38.3 m"},
                               {"label": "Net Debt – 2024", "value": "not disclosed"}]},
    )


def test_polish_deal_applies_house_style():
    deal = extract.polish_deal(raw_deal(), make_article("a"), extensive=False)
    assert deal["industry_header"] == "WOOD PROCESSING & BUILDING MATERIALS"
    assert deal["headline"] == "Kyklos Participations Reaches 95.96% Stake in Akritas"
    assert deal["description"].startswith("Kyklos acquired 95.96% of Akritas for c. €12m.")
    assert deal["buyer_name"] == "Kyklos Participations"


def test_undisclosed_lines_are_dropped_rather_than_shown():
    deal = extract.polish_deal(raw_deal(), make_article("a"), extensive=False)
    assert deal["deal_financials"] == [{"label": "Implied EV", "value": "€45.2m"},
                                       {"label": "EV/Sales", "value": "1.10x"}]
    assert deal["target"]["financials"] == [{"label": "Revenue – 2024", "value": "€38.3m"}]


def test_footnotes_get_a_leading_star():
    deal = extract.polish_deal(raw_deal(), make_article("a"), extensive=False)
    assert deal["deal_footnotes"] == ["*Implied equity value plus FY-24 net debt"]


def test_rumours_are_dropped_in_only_ma_mode():
    rumour = dict(raw_deal(), item_type="rumour")
    assert extract.polish_deal(rumour, make_article("a"), extensive=False) is None


def test_rumours_have_no_financials_block_in_extensive_mode():
    rumour = dict(raw_deal(), item_type="rumour")
    item = extract.polish_deal(rumour, make_article("a"), extensive=True)
    assert (item["deal_financials"], item["target"], item["buyer"]) == ([], None, None)


def test_item_without_headline_or_description_is_rejected():
    assert extract.polish_deal(dict(raw_deal(), headline=""), make_article("a"), False) is None
    assert extract.polish_deal(dict(raw_deal(), description=""), make_article("a"), False) is None


@pytest.mark.parametrize("text, expected", [
    ('```json\n{"relevant": false}\n```', {"relevant": False}),
    ('Here you go: {"a": 1} done', {"a": 1}),
    ("no json at all", None),
    ('{"broken": ', None),
])
def test_parse_json_tolerates_fences_and_prose(text, expected):
    assert extract.parse_json(text) == expected
