"""Shared test builders and a fake Claude client. No network or API key is needed."""

import json
import types
from datetime import datetime, timezone

import anthropic

from ma_deal_finder import extract
from ma_deal_finder import scrapers

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def make_article(key, source="Capital.gr"):
    return scrapers.Article(f"https://example.com/{key}", key, "text " * 80, source, NOW)


def deal_json(buyer_name, target_name, headline, event_date, **overrides):
    """A relevant deal as Claude would return it, before house-style polishing."""
    data = {
        "relevant": True, "item_type": "deal", "sector": "Consumer/Retail", "region": "Greece",
        "industry_header": "FOOD & BEVERAGE", "buyer_name": buyer_name, "target_name": target_name,
        "headline": headline, "description": f"{buyer_name} did something with {target_name}. Two. Three.",
        "deal_financials": [], "deal_footnotes": [], "target": None, "buyer": None,
        "financials_note": None, "latest_event_date": event_date,
    }
    data.update(overrides)
    return data


def make_deal(buyer_name, target_name, headline, url, event_date="2026-09-16", **overrides):
    """A polished deal dict with a given source URL."""
    article = make_article("unused")
    deal = extract.polish_deal(deal_json(buyer_name, target_name, headline, event_date, **overrides),
                               article, extensive=True)
    deal["sources"] = [{"title": headline, "url": url, "name": "test"}]
    return deal


class FakeClient:
    """Stands in for anthropic.Anthropic.

    Extraction replies come from `payloads` (keyed by article key); duplicate checks are answered
    by `same_deal(text_a, text_b)`.
    """

    def __init__(self, payloads=None, same_deal=None, fail_dedupe=False):
        self.messages = self
        self.payloads = payloads or {}
        self.same_deal = same_deal or (lambda a, b: False)
        self.fail_dedupe = fail_dedupe
        self.extraction_prompts = []
        self.dedupe_calls = 0

    def create(self, *, model, max_tokens, system, messages):
        user = messages[0]["content"]
        if system == extract.DEDUPE_PROMPT:
            self.dedupe_calls += 1
            if self.fail_dedupe:
                raise anthropic.APIError("simulated failure", request=None, body=None)
            first, second = user.split("\n\nB:\n")
            payload = {"same_deal": self.same_deal(first, second)}
        else:
            self.extraction_prompts.append(user)
            payload = next(p for key, p in self.payloads.items()
                           if f"URL: https://example.com/{key}\n" in user)
        block = types.SimpleNamespace(type="text", text=json.dumps(payload))
        usage = types.SimpleNamespace(input_tokens=100, output_tokens=50)
        return types.SimpleNamespace(content=[block], stop_reason="end_turn", usage=usage)
