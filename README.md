# M&A Deal Finder

A local Streamlit app that drafts a weekly M&A deal digest. **Problem:** writing the sheet means reading dozens of articles across many sites (for Greece, a dozen Greek-language sites with no single feed), picking the real transactions and rewriting each in one fixed house style. **Method:** the app scrapes Greek business sites, SEC EDGAR 8-Ks and newswire feeds, drops unrelated articles with a keyword filter, has Claude Haiku 4.5 judge relevance and write each deal in house style, then enforces the number, dash and naming rules in code. **Result:** a digest of deal cards grouped by industry, each ending in source links, plus a Word (.docx) export in Calibri 10. A default Greece run fetches c. 80 articles, sends c. 40 to Claude and costs an estimated \$0.20–0.35. Figures come only from the article text, so derived multiples still need checking against the source.

![M&A Deal Finder: sidebar filters and a generated digest for the United Kingdom](docs/digest.png)

> **Disclaimer:** built on public sources only; the output is not investment advice.

## Quick start

1. Install the dependencies (Python 3.10+):

   ```
   python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` (the file is git-ignored) and add your key, or paste the key into the sidebar instead:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

3. Run the app. Using `python -m` keeps install and run on the same Python:

   ```
   python -m streamlit run app.py --server.address localhost
   ```

4. In the sidebar pick geography, sector and search type, then click **Generate Digest**. Sources, look-back and cost limits are under **Advanced**.
5. Review the cards, then click **Download as Word (.docx)**.

To run the tests (no API key or network needed): `python -m pip install -r requirements-dev.txt`, then `python -m pytest`.

## The Problem

A weekly deal sheet means reading dozens of articles across many sites, picking the real transactions and rewriting each in one house format. For Greece the material is spread over a dozen Greek-language sites with no single feed, and the numbers use Greek conventions (decimal commas, "εκατ.", "δισ."). This tool automates the first pass. It does not replace review: figures come from the article text only, so derived multiples should be checked against the source.

## Method

### Sources

Per-site scrapers read a listing page, keep links from the look-back window (default 7 days), fetch each article and read its date from page metadata or the URL.

| Geography | Sources |
|---|---|
| Greece | Euro2day (M&A tag page), Capital.gr, MoneyReview, OT.gr, Newmoney, PowerGame, Energypress, Naftemporiki, Businessdaily; secondary: Mononews, Insider.gr, Liberal.gr, Bankingnews |
| United States | SEC EDGAR 8-Ks (Items 1.01 and 2.01), GlobeNewswire and PR Newswire M&A feeds |
| United Kingdom, Rest of Europe | GlobeNewswire and PR Newswire UK M&A feeds |

`robots.txt` is checked first. Blocked, unreachable or empty sites are skipped, with the reason shown under "Source status". EDGAR requests use a contact email you enter in the sidebar.

### Filtering

1. A keyword filter (accent-insensitive Greek stems plus English terms) drops unrelated articles before any API call.
2. Claude (`claude-haiku-4-5-20251001`) judges relevance. "Only M&As" accepts acquisitions, mergers, stake purchases, tender offers and squeeze-outs; "Extensive" adds IPOs, capital raises, rumours and strategic reviews.
3. Claude also labels sector and region for the filters, and returns the date of the deal's latest milestone. Deals whose latest milestone predates the look-back window are left out; unclear dates are kept.
4. Duplicates across articles merge into one entry listing every source: matching names directly, other pairs via a short Claude check. If unsure, both stay.

### Formatting and Word export

The system prompt in `ma_deal_finder/extract.py` encodes the house style. Claude returns JSON, and code enforces en dashes, `k/m/bn/tn` suffixes, two-decimal multiples, negatives in parentheses, legal-suffix stripping and title-case headlines. `ma_deal_finder/docx_export.py` writes Calibri 10, justified, with bold headlines, italic footnotes and clickable sources.

### Cost and API key

Each article sent to Claude is one API call; the sidebar caps the number and the app reports actual usage. A United Kingdom run of 24 articles used 73,120 input and 7,407 output tokens, about \$0.11. The key comes from `.env` or the sidebar and is never logged or written by the app.

### Known limits

- Scrapers depend on site markup; a broken source returns nothing and says so.
- Bankingnews pages have no dates, wire feeds show only ~20 items, and EDGAR uses one document per filing.
- "Exactly three sentences" and "never invent a figure" are enforced by the prompt, not by code.
- Article text is capped at 14,000 characters per call.

## Result

One card per deal under an all-caps industry header, plus a "Rumours and Developments" group in Extensive mode. The Word file mirrors it (see the screenshot above).

```text
INDUSTRY HEADER
Buyer Reaches 95.96% Stake in Target    (bold headline)
Three sentences: deal, structure, reason
Implied EV: €…m
EV/EBITDA: …x
*Footnote for derived figures           (italic)
The Target / The Buyer: short paragraph, financials
Source: euro2day.gr, capital.gr         (links)
```
