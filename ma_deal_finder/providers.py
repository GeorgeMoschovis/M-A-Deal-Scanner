"""The selectable models. DeepSeek is reached through its Anthropic-compatible endpoint, so both use the anthropic SDK."""

from dataclasses import dataclass, field

import anthropic

TYPICAL_INPUT_TOKENS = 3_400
TYPICAL_OUTPUT_TOKENS = 900


@dataclass(frozen=True)
class Provider:
    label: str
    company: str
    model: str
    price_in: float  # USD per input token
    price_out: float  # USD per output token
    key_state: str  # st.session_state key of the API key field
    env_var: str
    base_url: str | None = None
    request_extras: dict = field(default_factory=dict)

    @property
    def estimated_cost_per_article(self):
        return TYPICAL_INPUT_TOKENS * self.price_in + TYPICAL_OUTPUT_TOKENS * self.price_out


CLAUDE = Provider("Claude Haiku 4.5 (Anthropic)", "Anthropic", "claude-haiku-4-5-20251001",
                  1.00 / 1_000_000, 5.00 / 1_000_000, "api_key", "ANTHROPIC_API_KEY")

# peak-hour prices; off-peak is half. Thinking is switched off: extraction needs no reasoning.
DEEPSEEK = Provider("DeepSeek V4.1 Flash", "DeepSeek", "deepseek-flash",
                    0.30 / 1_000_000, 1.20 / 1_000_000, "deepseek_api_key", "DEEPSEEK_API_KEY",
                    base_url="https://api.deepseek.com/anthropic",
                    request_extras={"thinking": {"type": "disabled"}})

PROVIDERS = {p.label: p for p in (CLAUDE, DEEPSEEK)}


def make_client(provider, api_key):
    return anthropic.Anthropic(api_key=api_key, base_url=provider.base_url, timeout=90.0)
