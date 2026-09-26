"""The Streamlit app run end to end with a fake model client and a fake article. No network or keys."""

from datetime import datetime, timezone
from pathlib import Path

import anthropic
import dotenv
import pytest
from streamlit.testing.v1 import AppTest

from ma_deal_finder import scrapers
from helpers import FakeClient, deal_json, make_article

APP = str(Path(__file__).resolve().parent.parent / "app.py")
DEEPSEEK = "DeepSeek V4.1 Flash"
TODAY = datetime.now(timezone.utc).date().isoformat()  # the app measures its window from the real clock


@pytest.fixture
def fake_run(monkeypatch):
    """Returns run(client, env_file=None): starts the app, clicks Generate Digest, returns the AppTest."""
    article = make_article("ion-1", source="Capital.gr")
    article.text = "εξαγορά stake acquisition " * 40
    monkeypatch.setattr(scrapers, "collect",
                        lambda src, since, n, contact="": ([article] if src.name == "Capital.gr" else [], ""))
    for name in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def run(client, env_file=None):
        monkeypatch.setattr(dotenv, "dotenv_values", lambda *args, **kwargs: env_file or {})
        monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        at.sidebar.text_input(key="api_key").set_value("fake-key")
        at.run()
        at.sidebar.button[0].click()
        at.run()
        return at
    return run


def cards(at):
    return [m.value for m in at.markdown if 'class="deal"' in m.value]


def test_a_successful_run_shows_the_deal_card(fake_run):
    payload = deal_json("Ion", "Lambda", "ion completes lambda acquisition", TODAY)
    at = fake_run(FakeClient({"ion-1": payload}))
    assert not at.exception
    assert len(cards(at)) == 1
    assert "Ion Completes Lambda Acquisition" in cards(at)[0]


def test_api_errors_are_counted_and_the_first_one_is_shown(fake_run):
    at = fake_run(FakeClient(fail_extraction=True))
    assert not at.exception
    warning = at.warning[0].value
    assert "1 article(s) could not be processed" in warning
    assert "APIError: Error code: 400 - model not found" in warning


def test_a_key_from_the_env_file_prefills_and_survives_switching_models(fake_run):
    at = fake_run(FakeClient(), env_file={"ANTHROPIC_API_KEY": "from-env-claude",
                                          "DEEPSEEK_API_KEY": "from-env-deepseek"})
    at.sidebar.radio[0].set_value(DEEPSEEK).run()
    assert at.sidebar.text_input[0].label == "DeepSeek API key"
    assert at.sidebar.text_input[0].value == "from-env-deepseek"
    at.sidebar.text_input(key="deepseek_api_key").set_value("typed-key").run()
    at.sidebar.radio[0].set_value("Claude Haiku 4.5 (Anthropic)").run()
    at.sidebar.radio[0].set_value(DEEPSEEK).run()
    assert at.sidebar.text_input[0].value == "typed-key"


def test_deepseek_run_uses_its_own_key_and_endpoint(fake_run, monkeypatch):
    created = []
    payload = deal_json("Ion", "Lambda", "ion completes lambda acquisition", TODAY)

    client = FakeClient({"ion-1": payload})

    def factory(**kwargs):
        created.append(kwargs)
        return client

    at = fake_run(FakeClient(), env_file={"DEEPSEEK_API_KEY": "from-env-deepseek"})
    monkeypatch.setattr(anthropic, "Anthropic", factory)
    at.sidebar.radio[0].set_value(DEEPSEEK).run()
    at.sidebar.button[0].click().run()
    assert created[-1]["api_key"] == "from-env-deepseek"
    assert created[-1]["base_url"] == "https://api.deepseek.com/anthropic"
    assert client.last_extras == {"thinking": {"type": "disabled"}}
    assert len(cards(at)) == 1


def test_a_deal_with_no_link_to_the_selected_geography_is_left_out_and_counted(fake_run):
    payload = deal_json("BP", "Devon", "bp weighs devon assets", TODAY,
                        geographies=["United States"])
    at = fake_run(FakeClient({"ion-1": payload}))
    assert not at.exception
    assert cards(at) == []
    assert any("1 item(s) left out because they have no link to the selected geographies" in c.value
               for c in at.caption)
