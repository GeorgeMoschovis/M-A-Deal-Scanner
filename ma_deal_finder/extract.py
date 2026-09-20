"""Claude extraction, house-style enforcement, de-duplication and grouping."""

import json
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from urllib.parse import urlparse

import anthropic

from .providers import CLAUDE, Provider
from .scrapers import normalise

MAX_ARTICLE_CHARS = 14_000
MAX_OUTPUT_TOKENS = 3_000

SECTORS = ["Industrials", "Financials", "Energy", "Real Estate", "Technology",
           "Consumer/Retail", "Healthcare", "Shipping", "Telecom"]
OTHER_ITEM_TYPES = {"rumour", "ipo", "capital_raise", "strategic_alternatives"}

SYSTEM_PROMPT = """You are an M&A analyst on a deal-coverage desk. You read one news article, press release or regulatory filing and, if it reports an event that matches the requested MODE, write it up in the desk's house style. Reply with one JSON object and nothing else: no prose, no code fences.

MODE (first line of the user message)
- ONLY_MA: relevant only if the article reports a completed or announced acquisition, merger, stake purchase, tender/public offer or squeeze-out. Nothing else is relevant: not IPOs, capital raises, rumours, strategic reviews, results or general news.
- EXTENSIVE: everything in ONLY_MA, plus IPOs, capital raises, rumours and developments, and strategic-alternatives reports.
If the article is not relevant, reply {"relevant": false}.

DATES (the user message gives PUBLISHED and WINDOW_START)
Set latest_event_date (YYYY-MM-DD) to the date of the most recent milestone of the transaction that the article reports as news: announcement, signing, approval or completion. If the article only mentions a transaction that happened earlier as background (for example inside a results report), use the date of that earlier transaction. Resolve relative dates ("yesterday", "last month") from PUBLISHED. Use null if no date is stated or you are not sure; never guess. If latest_event_date is before WINDOW_START, reply {"relevant": false, "stale": true} instead of writing the item up.

Use only facts stated in the article. Never use outside knowledge and never invent a figure. Write in English and give Greek companies their usual English names in Latin script.

PER-DEAL OUTPUT (item_type "deal")
1. industry_header: all capitals, e.g. "WOOD PROCESSING & BUILDING MATERIALS". If the user message lists headers already used in this digest and this deal is in the same industry, reuse that header exactly.
2. headline: one line. Who acquired who, stake (if not 100%), consideration if available. Title case (every word capitalized except small words like "to", "for", "a"). No speculation, no opinion. Example: "Kyklos Participations Reaches 95.96% Stake in Akritas".
3. description: exactly 3 sentences, no more.
   - Sentence 1: who acquired who, the stake, the consideration, and the industries involved.
   - Sentence 2: deal structure, consideration breakdown, or other financial detail.
   - Sentence 3: brief, factual reason for the deal / the buyer's stated goal.
   No speculation, no personal opinion, no promotional tone.
4. deal_financials: only what is calculable from disclosed numbers. Never invent a figure. Lines, in this order:
   - "Implied EV": Implied Enterprise Value. State it as EV unless the source explicitly reports equity value only; EV is the market-standard default.
   - "Implied Equity Value"
   - "EV/EBITDA"
   - "EV/Sales"
   If a figure has to be derived (EV from disclosed price x stake, equity value from EV minus net debt, etc.), compute it. If the underlying numbers are not disclosed, omit the line entirely rather than guessing. Add a footnote to deal_footnotes for every derived figure, each starting with "*", e.g. "*Implied transaction equity value plus FY-24 net debt".
5. target: "description" is one short paragraph on the target, with promotional language stripped. "financials" are lines labelled with the fiscal year or period, in this order when disclosed: Revenue, EBITDA, Net Income, Net Debt, Leverage. Example: label "Revenue – 2024", value "€38.3m". Leverage = Net Debt / EBITDA. Only for operating companies, not funds: for a fund use an empty financials list.
6. buyer: same structure as target. Never cover the seller.

LTM (listed companies that report more often than year-end): if the deal is announced before FY financials are out, do not use stale prior-year numbers. Compute
   LTM EBITDA (as of Q3) = 9M current-year EBITDA + prior FY EBITDA − 9M prior-year EBITDA
and label the figure LTM. Net Debt = Long-Term Debt + Short-Term Debt + ST Leasing Liabilities + LT Leasing Liabilities − Cash & Cash Equivalents.

OTHER ITEMS (EXTENSIVE mode only): item_type "rumour", "ipo", "capital_raise" or "strategic_alternatives" form the "Rumours and Developments" section. Their description is exactly 1 paragraph. deal_financials is empty, target and buyer are null. The headline rules above still apply.

GLOBAL FORMATTING (applies to every string you write)
- Numbers: k / m / bn / tn suffix directly after the number, no space (€2.5m, not €2.5 m). Greek sources use "εκατ." for million, "δισ." for billion and a decimal comma: convert.
- Currency symbol directly before the number, no space (€2.5, $2.5).
- Multiples: two decimal places, then "x", no space (10.75x).
- Negative numbers in parentheses: (€20m).
- Use "c." not "approximately".
- Dashes: always the en dash (–), never a hyphen (-) or em dash (—), for financials and in body text.
- Strip legal entity suffixes everywhere (S.A., A.E., A.E.D.A.K.E.S., Inc., Ltd, plc, ...): "Akritas", not "Akritas S.A.".
- No verbosity: shortest accurate phrasing, only the essential facts.
- Never write out multi-level ownership chains ("a wholly owned subsidiary of X, which is a subsidiary of Y"); state only the relevant entity.
- If key financials are not disclosed anywhere in the article, leave that line out rather than estimating. Set financials_note to "financials not disclosed" only if the deal is otherwise substantial enough to be worth flagging; otherwise null.

OUTPUT SCHEMA
{
  "relevant": true,
  "item_type": "deal" | "rumour" | "ipo" | "capital_raise" | "strategic_alternatives",
  "sector": "Industrials" | "Financials" | "Energy" | "Real Estate" | "Technology" | "Consumer/Retail" | "Healthcare" | "Shipping" | "Telecom" | "Other",
  "region": where the target or issuer is based: "Greece" | "United States" | "United Kingdom" | "Rest of Europe" | "Other",
  "latest_event_date": "YYYY-MM-DD" or null,
  "industry_header": "ALL CAPS INDUSTRY",
  "buyer_name": "short buyer name, empty string if none",
  "target_name": "short target or issuer name",
  "headline": "...",
  "description": "...",
  "deal_financials": [{"label": "Implied EV", "value": "€45.2m"}],
  "deal_footnotes": ["*..."],
  "target": {"description": "...", "financials": [{"label": "Revenue – 2024", "value": "€38.3m"}]} or null,
  "buyer": {"description": "...", "financials": [...]} or null,
  "financials_note": "financials not disclosed" or null
}"""

_MA_TERMS = [
    "εξαγορ", "συγχων", "αποκτ", "δημοσια προταση", "δημοσια προσφορα", "εθελοντικ", "εξωθηση",
    "ιδιωτικοποι", "πουλα", "πωλει", "πωληση εταιρ", "πωληση μετοχ", "πωληση ποσοστ",
    "πωληση θυγατρ", "αγορασε", "αγοραζ", "μεταβιβασ", "ντιλ", "deal",
    "acqui", "merger", "merge", "takeover", "take-over", "tender offer", "buyout", "buy-out",
    "stake", "to buy", "buys", "bought", "sells", "sold", "divest", "definitive agreement",
    "purchase agreement", "business combination", "take private", "public offer", "agreed to",
]
_EXTENSIVE_TERMS = [
    "ipo", "εισαγωγη", "δημοσια εγγραφη", "αυξηση κεφαλαι", "capital increase", "rights issue",
    "offering", "placement", "strategic alternative", "στρατηγικ", "φημες", "rumour", "rumor",
    "in talks", "exploring", "considering", "ενδιαφερον", "ομολογ", "listing",
]


def _term_regex(terms):
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + ")")


MA_PATTERN = _term_regex(_MA_TERMS)
EXTENSIVE_PATTERN = _term_regex(_MA_TERMS + _EXTENSIVE_TERMS)


def is_candidate(article, extensive):
    lead = normalise(f"{article.title} {article.text[:2500]}")
    return bool((EXTENSIVE_PATTERN if extensive else MA_PATTERN).search(lead))


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    provider: Provider = CLAUDE

    @property
    def cost(self):
        return (self.input_tokens * self.provider.price_in
                + self.output_tokens * self.provider.price_out)


SMALL_WORDS = {"a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet", "at", "by", "in",
               "of", "on", "to", "up", "as", "off", "per", "via", "with", "from", "into", "over",
               "than", "vs"}


def title_case(text):
    out = []
    for i, word in enumerate(text.split(" ")):
        if i > 0 and word.lower() in SMALL_WORDS:
            out.append(word.lower())
        elif word == word.lower():
            out.append("-".join(part[:1].upper() + part[1:] for part in word.split("-")))
        else:
            out.append(word)
    return " ".join(out)


_SUFFIXES = [r"S\.A\.S\.?", r"S\.A\.", r"SA", r"A\.E\.E\.X\.?", r"A\.E\.D\.A\.K\.E\.S\.?", r"A\.E\.",
             r"AEDAKES", r"AEEX", r"AE", r"S\.p\.A\.?", r"SpA", r"N\.V\.", r"NV", r"B\.V\.", r"BV",
             r"GmbH", r"AG", r"Inc\.?", r"Corp\.?", r"Ltd\.?", r"Limited", r"LLC", r"L\.L\.C\.",
             r"LLP", r"L\.P\.", r"PLC", r"plc", r"S\.r\.l\.?"]
LEGAL_SUFFIX = re.compile(r"(?:,\s*|\s+)(?:" + "|".join(_SUFFIXES) + r")(?!\w)")
UNIT_WORDS = {"trillion": "tn", "billion": "bn", "million": "m", "thousand": "k",
              "bln": "bn", "mln": "m", "mn": "m"}


def _strip_suffix(match):
    ends_sentence = re.match(r'\s+[A-Z€$£"“]|\s*$', match.string[match.end():])
    return "." if match[0].endswith(".") and ends_sentence else ""


def polish_text(text):
    if not isinstance(text, str):
        return ""
    text = LEGAL_SUFFIX.sub(_strip_suffix, text)
    text = re.sub(r"\s+[-—]{1,2}\s+|—", lambda m: "–" if m[0] == "—" else " – ", text)
    text = re.sub(r"(?<=\d)-(?=\d)", "–", text)
    text = re.sub(r"\bapproximately\b", "c.", text)
    text = re.sub(r"\bApproximately\b", "C.", text)
    text = re.sub(r"\bapprox\.", "c.", text)
    text = re.sub(r"(?<=\d)\s*\b(trillion|billion|million|thousand|bln|mln|mn)\b",
                  lambda m: UNIT_WORDS[m[1].lower()], text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s+(k|m|bn|tn)\b", r"\1", text)
    text = re.sub(r"\bEUR\s?(?=\d)", "€", text)
    text = re.sub(r"\bUSD\s?(?=\d)", "$", text)
    text = re.sub(r"\bGBP\s?(?=\d)", "£", text)
    text = re.sub(r"([€$£])\s+(?=\d)", r"\1", text)
    text = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)\s?x\b", lambda m: f"{float(m[1]):.2f}x", text)
    text = re.sub(r"(?:(?<=\s)|^)[-–−]([€$£]\d[\d.,]*(?:k|m|bn|tn)?)", r"(\1)", text)
    text = re.sub(r"(?:(?<=\s)|^)[-–−](\d[\d.,]*x)", r"(\1)", text)
    return re.sub(r"\s{2,}", " ", text).strip()


_UNDISCLOSED = re.compile(r"^(?:n/?a|none|unknown|not (?:disclosed|available)|undisclosed|–|-)?$", re.I)


def _lines(items):
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        label, value = polish_text(item.get("label")), polish_text(str(item.get("value", "")))
        if label and not _UNDISCLOSED.match(value):
            out.append({"label": label, "value": value})
    return out


def _party(data):
    if not isinstance(data, dict) or not data.get("description"):
        return None
    return {"description": polish_text(data["description"]), "financials": _lines(data.get("financials"))}


def _iso_date(value):
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) else ""
    except ValueError:
        return ""


def is_stale(deal, window_start):
    """True only when the deal's latest milestone is a known date before the look-back window."""
    return bool(deal["event_date"]) and date.fromisoformat(deal["event_date"]) < window_start


def polish_deal(data, article, extensive):
    item_type = data.get("item_type") if data.get("item_type") in OTHER_ITEM_TYPES else "deal"
    if item_type != "deal" and not extensive:
        return None
    headline = title_case(polish_text(data.get("headline")).rstrip("."))
    description = polish_text(data.get("description"))
    if not headline or not description:
        return None
    is_deal = item_type == "deal"
    footnotes = [polish_text(f) for f in data.get("deal_footnotes") or [] if isinstance(f, str)]
    note = polish_text(data.get("financials_note") or "")
    return {
        "item_type": item_type,
        "event_date": _iso_date(data.get("latest_event_date")),
        "sector": data.get("sector") if data.get("sector") in SECTORS else "Other",
        "region": data.get("region") or "Other",
        "industry_header": polish_text(data.get("industry_header") or "OTHER").rstrip(".").upper(),
        "buyer_name": polish_text(data.get("buyer_name")).rstrip("."),
        "target_name": polish_text(data.get("target_name")).rstrip("."),
        "headline": headline,
        "description": description,
        "deal_financials": _lines(data.get("deal_financials")) if is_deal else [],
        "deal_footnotes": [("*" + f.lstrip("*")) for f in footnotes if f] if is_deal else [],
        "target": _party(data.get("target")) if is_deal else None,
        "buyer": _party(data.get("buyer")) if is_deal else None,
        "financials_note": note if is_deal else "",
        "published": article.published.date().isoformat() if article.published else "",
        "sources": [{"title": article.title, "url": article.url, "name": article.source}],
    }


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _ask(client, system, user, max_tokens, usage):
    """One model call; returns (parsed JSON or None, finished normally)."""
    message = client.messages.create(model=usage.provider.model, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": user}],
                                     **usage.provider.request_extras)
    usage.calls += 1
    usage.input_tokens += message.usage.input_tokens
    usage.output_tokens += message.usage.output_tokens
    text = next((b.text for b in message.content if b.type == "text"), "")
    return parse_json(text), message.stop_reason != "max_tokens"


def extract_deal(client, article, extensive, used_headers, usage, window_start):
    """Returns (deal or None, stale). stale is True when the deal predates the look-back window."""
    lines = [f"MODE: {'EXTENSIVE' if extensive else 'ONLY_MA'}"]
    if used_headers:
        lines.append(f"HEADERS ALREADY USED: {json.dumps(sorted(used_headers), ensure_ascii=False)}")
    published = article.published.date().isoformat() if article.published else "unknown"
    lines += [f"WINDOW_START: {window_start.isoformat()}", f"SOURCE: {article.source}",
              f"PUBLISHED: {published}", f"URL: {article.url}", f"TITLE: {article.title}",
              "", "ARTICLE:", article.text[:MAX_ARTICLE_CHARS]]
    data, finished = _ask(client, SYSTEM_PROMPT, "\n".join(lines), MAX_OUTPUT_TOKENS, usage)
    if not finished or not data:
        return None, False
    if data.get("stale"):
        return None, True
    if not data.get("relevant"):
        return None, False
    deal = polish_deal(data, article, extensive)
    if deal and is_stale(deal, window_start):
        return None, True
    return deal, False


def _key(name):
    return re.sub(r"[^a-z0-9]", "", normalise(name))


def _similar(a, b):
    a, b = _key(a), _key(b)
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.8


def _same_deal(a, b):
    if not _similar(a["target_name"], b["target_name"]):
        return False
    return _similar(a["buyer_name"], b["buyer_name"]) or not (a["buyer_name"] and b["buyer_name"])


def _richness(deal):
    parties = sum(len(deal[k]["financials"]) for k in ("target", "buyer") if deal[k])
    return len(deal["deal_financials"]) + parties + len(deal["description"]) / 200


def _combine(a, b):
    """Keep the richer write-up and carry over every source link."""
    base, other = (a, b) if _richness(a) >= _richness(b) else (b, a)
    known = {s["url"] for s in base["sources"]}
    base["sources"] = base["sources"] + [s for s in other["sources"] if s["url"] not in known]
    return base


def merge_duplicates(deals):
    """Cheap pass: collapse deals whose buyer and target names match."""
    merged = []
    for deal in deals:
        match = next((m for m in merged if _same_deal(m, deal)), None)
        if match is None:
            merged.append(deal)
        else:
            merged[merged.index(match)] = _combine(match, deal)
    return merged


DEDUPE_PROMPT = """You compare two write-ups of M&A news and decide whether they describe the same transaction: the same parties and the same asset, stake, joint venture or offer. The same deal reported by different outlets, with different names, spellings or wording, is the same transaction. Separate deals by the same buyer, or separate tranches, are different. If you are not sure, answer false. Reply with JSON only: {"same_deal": true} or {"same_deal": false}."""
MAX_DEDUPE_CALLS = 30
_NAME_STOP = {"group", "holdings", "holding", "company", "bank", "capital", "partners"}


def _name_tokens(deal):
    words = re.findall(r"[a-z0-9]+", normalise(f"{deal['buyer_name']} {deal['target_name']}"))
    return {w for w in words if len(w) >= 4 and w not in _NAME_STOP}


def _might_match(a, b):
    if (a["item_type"] == "deal") != (b["item_type"] == "deal"):
        return False
    return (_similar(a["buyer_name"], b["buyer_name"]) or _similar(a["target_name"], b["target_name"])
            or bool(_name_tokens(a) & _name_tokens(b)))


def _brief(deal):
    return (f"Headline: {deal['headline']}\nBuyer: {deal['buyer_name']}\nTarget: {deal['target_name']}\n"
            f"Published: {deal['published']}\nSummary: {deal['description']}")


def merge_with_claude(client, deals, usage):
    """Second pass: ask Claude about pairs that share a name but were not caught by name matching."""
    deals, calls, i = list(deals), 0, 0
    while i < len(deals):
        j = i + 1
        while j < len(deals):
            same = False
            if calls < MAX_DEDUPE_CALLS and _might_match(deals[i], deals[j]):
                calls += 1
                try:
                    data, _ = _ask(client, DEDUPE_PROMPT,
                                   f"A:\n{_brief(deals[i])}\n\nB:\n{_brief(deals[j])}", 30, usage)
                except anthropic.APIError:
                    data = None
                same = bool(data and data.get("same_deal") is True)
            if same:
                deals[i] = _combine(deals[i], deals[j])
                del deals[j]
            else:
                j += 1
        i += 1
    return deals


def source_links(deal):
    """(label, url) pairs for a deal's sources; repeated domains get a number."""
    counts, links = {}, []
    for source in deal["sources"]:
        domain = urlparse(source["url"]).netloc.removeprefix("www.")
        counts[domain] = counts.get(domain, 0) + 1
        label = domain if counts[domain] == 1 else f"{domain} ({counts[domain]})"
        links.append((label, source["url"]))
    return links


def group_deals(deals):
    """Returns ([(industry header, [deals])], [rumours and developments])."""
    ma = sorted((d for d in deals if d["item_type"] == "deal"),
                key=lambda d: d["published"], reverse=True)
    groups = {}
    for deal in ma:
        groups.setdefault(deal["industry_header"], []).append(deal)
    others = sorted((d for d in deals if d["item_type"] != "deal"),
                    key=lambda d: d["published"], reverse=True)
    return sorted(groups.items()), others
