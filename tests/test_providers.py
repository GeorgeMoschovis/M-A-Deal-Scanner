"""The model choice: which model, endpoint, request options and prices each provider uses."""

import json
import types
from datetime import date

import pytest

from ma_deal_finder import extract
from ma_deal_finder.providers import CLAUDE, DEEPSEEK, PROVIDERS, make_client
from helpers import deal_json, make_article, make_deal

WINDOW = date(2026, 9, 12)


class RecordingClient:
    """Records the keyword arguments of every messages.create call."""

    def __init__(self, payload, leading_blocks=()):
        self.messages = self
        self.payload = payload
        self.leading_blocks = list(leading_blocks)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = types.SimpleNamespace(type="text", text=json.dumps(self.payload))
        usage = types.SimpleNamespace(input_tokens=10, output_tokens=5)
        return types.SimpleNamespace(content=self.leading_blocks + [text], stop_reason="end_turn",
                                     usage=usage)


def extract_with(provider, client):
    usage = extract.Usage(provider=provider)
    deal, _ = extract.extract_deal(client, make_article("a"), False, set(), usage, WINDOW)
    return deal, usage


def test_both_models_are_offered_with_claude_first():
    assert list(PROVIDERS.values()) == [CLAUDE, DEEPSEEK]


def test_deepseek_uses_its_flash_model_through_the_anthropic_compatible_endpoint():
    assert DEEPSEEK.model == "deepseek-flash"
    assert DEEPSEEK.base_url == "https://api.deepseek.com/anthropic"
    assert DEEPSEEK.request_extras == {"thinking": {"type": "disabled"}}


def test_claude_uses_haiku_on_the_default_endpoint_with_no_extra_options():
    assert CLAUDE.model == "claude-haiku-4-5-20251001"
    assert CLAUDE.base_url is None
    assert CLAUDE.request_extras == {}


def test_each_model_has_its_own_key_field_and_env_variable():
    assert (CLAUDE.key_state, CLAUDE.env_var) == ("api_key", "ANTHROPIC_API_KEY")
    assert (DEEPSEEK.key_state, DEEPSEEK.env_var) == ("deepseek_api_key", "DEEPSEEK_API_KEY")


def test_make_client_points_each_key_at_its_own_endpoint():
    claude = make_client(CLAUDE, "claude-key")
    deepseek = make_client(DEEPSEEK, "deepseek-key")
    assert str(claude.base_url).startswith("https://api.anthropic.com")
    assert str(deepseek.base_url).startswith("https://api.deepseek.com/anthropic")
    assert (claude.api_key, deepseek.api_key) == ("claude-key", "deepseek-key")


def test_default_usage_sends_the_claude_model_and_no_thinking_option():
    client = RecordingClient(deal_json("A", "B", "A Buys B", None))
    extract_with(CLAUDE, client)
    call = client.calls[0]
    assert call["model"] == "claude-haiku-4-5-20251001"
    assert "thinking" not in call


def test_deepseek_usage_sends_its_model_with_thinking_disabled():
    client = RecordingClient(deal_json("A", "B", "A Buys B", None))
    deal, _ = extract_with(DEEPSEEK, client)
    call = client.calls[0]
    assert call["model"] == "deepseek-flash"
    assert call["thinking"] == {"type": "disabled"}
    assert deal["headline"] == "A Buys B"


def test_reasoning_blocks_before_the_answer_are_ignored():
    thinking = types.SimpleNamespace(type="thinking", thinking="let me think")
    client = RecordingClient(deal_json("A", "B", "A Buys B", None), leading_blocks=[thinking])
    deal, _ = extract_with(DEEPSEEK, client)
    assert deal["headline"] == "A Buys B"


def test_the_duplicate_check_uses_the_selected_model_too():
    deals = [make_deal("Ion", "Lambda", "Ion Buys Lambda", "https://a.example/1"),
             make_deal("Ion", "Interion", "Ion Buys Interion", "https://b.example/2")]
    client = RecordingClient({"same_deal": False})
    usage = extract.Usage(provider=DEEPSEEK)
    extract.merge_with_claude(client, deals, usage)
    assert client.calls and all(c["model"] == "deepseek-flash" for c in client.calls)
    assert all(c["thinking"] == {"type": "disabled"} for c in client.calls)


def test_cost_uses_the_selected_models_prices():
    tokens = dict(input_tokens=1_000_000, output_tokens=1_000_000)
    assert extract.Usage(provider=CLAUDE, **tokens).cost == pytest.approx(6.00)
    assert extract.Usage(provider=DEEPSEEK, **tokens).cost == pytest.approx(1.50)


def test_usage_defaults_to_claude_pricing():
    assert extract.Usage(input_tokens=1_000_000).cost == pytest.approx(1.00)


def test_estimated_cost_per_article():
    assert round(CLAUDE.estimated_cost_per_article, 3) == 0.008
    assert DEEPSEEK.estimated_cost_per_article < CLAUDE.estimated_cost_per_article / 3
