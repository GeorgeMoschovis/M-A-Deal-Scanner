"""The same deal reported by several articles becomes one entry that lists every source."""

import extract
from helpers import FakeClient, make_deal

JV_1 = "https://www.capital.gr/ion-jv"
JV_2 = "https://www.mononews.gr/ion-jv"


def ion_joint_ventures():
    """Two write-ups of one Ion joint venture (different target names), plus a separate Ion deal."""
    first = make_deal("Ion", "Mexico Wafer Products JV",
                      "Ion Establishes 35% Stake in Mexico Wafer Products Joint Venture", JV_1,
                      buyer={"description": "Ion makes chocolate.",
                             "financials": [{"label": "Revenue – 2025", "value": "€209.1m"}]})
    second = make_deal("Ion", "Mexican Wafer Trading and Distribution JV",
                       "Ion Establishes 35% Stake in Mexican Wafer Trading and Distribution Joint Venture",
                       JV_2)
    other = make_deal("Ion", "Lambda", "Ion Completes Lambda Acquisition", "https://example.com/lambda")
    return [first, other, second]


def both_mention_wafer(a, b):
    return "Wafer" in a and "Wafer" in b


def test_matching_names_merge_without_any_api_call():
    a = make_deal("PPC", "ABO Energy Polska", "PPC Buys ABO Energy Polska", "https://a.example/1")
    b = make_deal("PPC S.A.", "ABO Energy Polska S.A.", "PPC Completes ABO Energy Polska Deal",
                  "https://b.example/2")
    merged = extract.merge_duplicates([a, b])
    assert len(merged) == 1
    assert sorted(s["url"] for s in merged[0]["sources"]) == ["https://a.example/1", "https://b.example/2"]


def test_the_richer_write_up_is_kept():
    thin = make_deal("PPC", "ABO Energy", "PPC Buys ABO Energy", "https://a.example/1")
    rich = make_deal("PPC", "ABO Energy", "PPC Buys ABO Energy", "https://b.example/2",
                     deal_financials=[{"label": "Implied EV", "value": "€45.2m"}])
    merged = extract.merge_duplicates([thin, rich])
    assert merged[0]["deal_financials"] == [{"label": "Implied EV", "value": "€45.2m"}]


def test_different_buyers_of_the_same_target_stay_separate():
    a = make_deal("PPC", "ABO Energy", "PPC Buys ABO Energy", "https://a.example/1")
    b = make_deal("Aktor", "ABO Energy", "Aktor Buys ABO Energy", "https://b.example/2")
    assert len(extract.merge_duplicates([a, b])) == 2


def test_name_matching_alone_misses_differently_named_targets():
    assert len(extract.merge_duplicates(ion_joint_ventures())) == 3


def test_claude_check_merges_the_two_joint_venture_write_ups():
    client = FakeClient(same_deal=both_mention_wafer)
    merged = extract.merge_with_claude(client, ion_joint_ventures(), extract.Usage())
    assert len(merged) == 2
    jv = next(d for d in merged if "Wafer" in d["headline"])
    assert sorted(s["url"] for s in jv["sources"]) == [JV_1, JV_2]
    assert jv["buyer"] is not None
    assert any("Lambda" in d["headline"] for d in merged)


def test_claude_check_costs_are_counted_in_usage():
    usage = extract.Usage()
    extract.merge_with_claude(FakeClient(same_deal=both_mention_wafer), ion_joint_ventures(), usage)
    assert usage.calls == 2
    assert usage.input_tokens == 200 and usage.output_tokens == 100


def test_unsure_answer_keeps_both_entries():
    merged = extract.merge_with_claude(FakeClient(same_deal=lambda a, b: False),
                                       ion_joint_ventures(), extract.Usage())
    assert len(merged) == 3


def test_api_error_during_the_check_keeps_both_entries():
    merged = extract.merge_with_claude(FakeClient(fail_dedupe=True), ion_joint_ventures(),
                                       extract.Usage())
    assert len(merged) == 3


def test_a_single_deal_needs_no_api_call():
    client = FakeClient()
    extract.merge_with_claude(client, [ion_joint_ventures()[1]], extract.Usage())
    assert client.dedupe_calls == 0


def test_deals_and_rumours_are_never_compared():
    deal = make_deal("Ion", "Lambda", "Ion Completes Lambda Acquisition", "https://a.example/1")
    rumour = make_deal("Ion", "Lambda", "Ion Weighs Lambda Deal", "https://b.example/2",
                       item_type="rumour")
    client = FakeClient(same_deal=lambda a, b: True)
    extract.merge_with_claude(client, [deal, rumour], extract.Usage())
    assert client.dedupe_calls == 0


def test_number_of_claude_checks_is_capped(monkeypatch):
    monkeypatch.setattr(extract, "MAX_DEDUPE_CALLS", 1)
    deals = [make_deal("Ion", f"Target {i}", f"Ion Buys Target {i}", f"https://a.example/{i}")
             for i in range(4)]
    client = FakeClient()
    extract.merge_with_claude(client, deals, extract.Usage())
    assert client.dedupe_calls == 1
