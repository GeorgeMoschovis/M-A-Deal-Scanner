import html
import os
from datetime import date
from itertools import zip_longest
from pathlib import Path

import anthropic
import streamlit as st
from dotenv import dotenv_values

from ma_deal_finder import scrapers
from ma_deal_finder.docx_export import build_docx
from ma_deal_finder.providers import PROVIDERS, make_client
from ma_deal_finder.extract import (SECTORS, Usage, extract_deal, group_deals,
                     is_candidate, merge_duplicates, merge_with_claude, source_links)

ALL_SECTORS = "All sectors"
ONLY_MA, EXTENSIVE = "Only M&As", "Extensive"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Streamlit has no default-width option: drag the sidebar's own handle once, only if it is still at 300px
WIDEN_SIDEBAR_JS = """<script>
if (!window.__sidebarWidened) {
  const started = Date.now();
  let seenAt = 0;
  const timer = setInterval(() => {
    const sidebar = document.querySelector('[data-testid="stSidebar"]');
    const handle = sidebar && [...sidebar.querySelectorAll('div')]
      .find(d => getComputedStyle(d).cursor === 'col-resize');
    const width = sidebar ? sidebar.getBoundingClientRect().width : 0;
    if (handle && sidebar.getAttribute('aria-expanded') === 'true' && width > 0) {
      seenAt = seenAt || Date.now();
      if (Date.now() - seenAt < 1000) return;
      window.__sidebarWidened = true;
      clearInterval(timer);
      if (Math.abs(width - 300) > 10) return;
      const shift = Math.min(600, window.innerWidth * 0.45) - width;
      const box = handle.getBoundingClientRect();
      const x = box.x + box.width / 2, y = box.y + 200;
      const fire = (el, type, cx) => el.dispatchEvent(new MouseEvent(type,
        {bubbles: true, cancelable: true, view: window, clientX: cx, clientY: y, button: 0}));
      fire(handle, 'mousedown', x);
      setTimeout(() => fire(window, 'mousemove', x + shift), 80);
      setTimeout(() => fire(window, 'mouseup', x + shift), 200);
    } else if (Date.now() - started > 10000) {
      clearInterval(timer);
    }
  }, 100);
}
</script>"""

st.set_page_config(page_title="ma-deal-finder", layout="wide")
st.markdown(
    """<style>
.deal{border:1px solid rgba(128,128,128,.35);border-radius:6px;padding:14px 18px;margin:0 0 14px 0}
.deal p{text-align:justify;margin:0 0 8px 0}
.deal .headline{font-weight:700;margin-bottom:6px}
.deal .foot{font-style:italic}
.industry{font-weight:700;letter-spacing:.02em;margin:22px 0 8px 0}
</style>""",
    unsafe_allow_html=True,
)


def esc(text):
    # "$" is escaped too, otherwise Streamlit reads $...$ pairs as LaTeX
    return html.escape(text or "").replace("$", "&#36;")


def financial_lines(lines):
    body = "<br>".join(f"{esc(line['label'])}: {esc(line['value'])}" for line in lines)
    return f"<p>{body}</p>"


def card_html(deal):
    parts = [f'<div class="deal"><p class="headline">{esc(deal["headline"])}</p>',
             f'<p>{esc(deal["description"])}</p>']
    if deal["deal_financials"]:
        parts.append(financial_lines(deal["deal_financials"]))
    for note in deal["deal_footnotes"]:
        parts.append(f'<p class="foot">{esc(note)}</p>')
    if deal["financials_note"]:
        parts.append(f'<p class="foot">{esc(deal["financials_note"])}</p>')
    for title, key in (("The Target", "target"), ("The Buyer", "buyer")):
        party = deal[key]
        if party:
            parts.append(f"<p><b>{title}</b></p><p>{esc(party['description'])}</p>")
            if party["financials"]:
                parts.append(financial_lines(party["financials"]))
    links = ", ".join(f'<a href="{esc(url)}" target="_blank">{esc(label)}</a>'
                      for label, url in source_links(deal) if url.startswith(("http://", "https://")))
    parts.append(f"<p>Source: {links}</p></div>")
    return "".join(parts)


def render_digest(deals):
    groups, developments = group_deals(deals)
    for header, items in groups:
        st.markdown(f'<div class="industry">{esc(header)}</div>', unsafe_allow_html=True)
        for deal in items:
            st.markdown(card_html(deal), unsafe_allow_html=True)
    if developments:
        st.markdown('<div class="industry">RUMOURS AND DEVELOPMENTS</div>', unsafe_allow_html=True)
        for item in developments:
            st.markdown(card_html(item), unsafe_allow_html=True)


def gather(sources, cfg, status):
    since = scrapers.since_cutoff(cfg["days"])
    status.write(f"Scraping {len(sources)} source(s)")
    results = scrapers.collect_all(sources, since, cfg["per_source"], cfg["sec_contact"],
                                   on_done=lambda src, articles, note: status.write(
                                       f"Scraped {src.name}: {len(articles)} article(s)"))
    by_source, notes = {}, {}
    for src, (articles, note) in zip(sources, results):
        by_source[src.name] = articles
        notes[src.name] = (bool(articles), f"{len(articles)} article(s)" + (f" – {note}" if note else ""))
    return by_source, notes


def pick_candidates(by_source, cfg):
    filtered = [[a for a in articles if is_candidate(a, cfg["extensive"])]
                for articles in by_source.values()]
    interleaved = [a for group in zip_longest(*filtered) for a in group if a]
    return interleaved[:cfg["max_calls"]], len(interleaved)


def analyse(client, candidates, cfg, wire_names, progress):
    usage, deals, headers, failed, stale = Usage(provider=cfg["provider"]), [], set(), [], 0
    for i, article in enumerate(candidates, 1):
        progress.progress(i / len(candidates), text=f"Reading article {i} of {len(candidates)} ({article.source})")
        try:
            deal, is_old = extract_deal(client, article, cfg["extensive"], headers, usage,
                                        cfg["window_start"])
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise
        except anthropic.APIError as exc:
            failed.append(f"{type(exc).__name__}: {exc}")
            continue
        stale += is_old
        if not deal:
            continue
        if not cfg["all_sectors"] and deal["sector"] not in cfg["sectors"]:
            continue
        if article.source in wire_names and deal["region"] not in cfg["geographies"]:
            continue
        deals.append(deal)
        headers.add(deal["industry_header"])
    progress.progress(1.0, text="Checking for duplicate deals")
    deals = merge_with_claude(client, merge_duplicates(deals), usage)
    return deals, usage, failed, stale


def run(cfg, sources):
    provider = cfg["provider"]
    client = make_client(provider, st.session_state[provider.key_state].strip())
    wire_names = {s.name for s in sources if s.kind == "rss"}
    with st.status("Generating digest", expanded=True) as status:
        by_source, notes = gather(sources, cfg, status)
        candidates, total = pick_candidates(by_source, cfg)
        status.write(f"{total} candidate article(s) after the keyword filter; "
                     f"sending {len(candidates)} to {provider.label}")
        if not candidates:
            deals, usage, failed, stale = [], Usage(provider=provider), [], 0
        else:
            progress = st.progress(0.0)
            try:
                deals, usage, failed, stale = analyse(client, candidates, cfg, wire_names, progress)
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
                status.update(label="API key rejected", state="error")
                st.error(f"{provider.company} rejected the API key. Check it in the sidebar and try again.")
                st.stop()
            progress.empty()
        status.update(label="Digest ready", state="complete", expanded=False)
    st.session_state["result"] = {
        "deals": deals, "usage": usage, "failed": failed, "stale": stale, "notes": notes,
        "capped": total - len(candidates), "docx": build_docx(deals),
    }


def on_geography():
    state = st.session_state
    state["geography"] = state["geography"] or state["geography_prev"]
    state["geography_prev"] = state["geography"]


def on_sector():
    state = st.session_state
    now, prev = state["sector"], state["sector_prev"]
    if ALL_SECTORS in now and len(now) > 1:
        # "All sectors" is exclusive: picking a sector drops it, picking it clears the sectors
        now = [s for s in now if s != ALL_SECTORS] if ALL_SECTORS in prev else [ALL_SECTORS]
    state["sector"] = now or [ALL_SECTORS]
    state["sector_prev"] = state["sector"]


env_file = dotenv_values(Path(__file__).with_name(".env"))
for option in PROVIDERS.values():
    # re-assigned every run so a key survives while its field is hidden; typed key > .env > environment
    st.session_state[option.key_state] = (st.session_state.get(option.key_state)
                                          or env_file.get(option.env_var) or os.getenv(option.env_var, ""))
st.session_state.setdefault("geography", ["Greece"])
st.session_state.setdefault("geography_prev", ["Greece"])
st.session_state.setdefault("sector", [ALL_SECTORS])
st.session_state.setdefault("sector_prev", [ALL_SECTORS])

with st.sidebar:
    st.title("ma-deal-finder")
    provider = PROVIDERS[st.radio("AI model", list(PROVIDERS), help=(
        "Both read the same articles with the same instructions. DeepSeek is cheaper; its servers "
        "receive the article text."))]
    st.text_input(f"{provider.company} API key", type="password", key=provider.key_state,
                  help=f"Prefilled from {provider.env_var} in .env if set. You can also paste it here; "
                       "it is then held in this session's memory only.")
    geographies = st.pills("Geography", scrapers.GEOGRAPHIES, selection_mode="multi",
                           key="geography", on_change=on_geography) or []
    sector_choice = st.pills("Sector", [ALL_SECTORS] + SECTORS, selection_mode="multi",
                             key="sector", on_change=on_sector) or []
    search_type = st.radio("Search type", [ONLY_MA, EXTENSIVE], help=(
        "Only M&As: acquisitions, mergers, stake purchases, tender/public offers, squeeze-outs. "
        "Extensive: also IPOs, capital raises, rumours and developments, strategic-alternatives reports."))
    available = {s.key: s for s in scrapers.sources_for(geographies)}
    with st.expander("Advanced"):
        chosen_keys = st.pills("Sources", list(available), selection_mode="multi",
                               default=[s.key for s in scrapers.default_sources(geographies)],
                               format_func=lambda k: available[k].name) or []
        sources = [available[k] for k in available if k in chosen_keys]
        days = st.slider("Look back (days)", 1, 14, 7)
        per_source = st.slider("Articles fetched per source", 3, 25, 10)
        max_calls = st.slider("Max articles sent to the model", 10, 150, 40,
                              help="Cost guard: each article sent to the model is one API call.")
        sec_contact = ""
        if any(s.kind == "edgar" for s in sources):
            sec_contact = st.text_input("SEC contact email", help=(
                "SEC asks automated clients to identify themselves in the User-Agent. "
                "Used only for requests to sec.gov."))
    per_article = provider.estimated_cost_per_article
    st.caption(f"Estimated cost ≈ \\${max_calls * per_article:.2f} if all {max_calls} articles are sent "
               f"(about \\${per_article:.3f} each; the actual figure is shown after each run).")
    generate = st.button("Generate Digest", type="primary", use_container_width=True)
    st.html(WIDEN_SIDEBAR_JS, unsafe_allow_javascript=True)

if generate:
    if not st.session_state.get(provider.key_state, "").strip():
        st.error(f"Enter your {provider.company} API key in the sidebar.")
    elif not sources:
        st.error("Select at least one source.")
    else:
        cfg = {"days": days, "per_source": per_source, "max_calls": max_calls,
               "sec_contact": sec_contact, "extensive": search_type == EXTENSIVE,
               "all_sectors": ALL_SECTORS in sector_choice or not sector_choice,
               "sectors": set(sector_choice), "geographies": set(geographies),
               "window_start": scrapers.since_cutoff(days).date(), "provider": provider}
        run(cfg, sources)

result = st.session_state.get("result")
if result:
    usage = result["usage"]
    st.download_button("Download as Word (.docx)", result["docx"],
                       file_name=f"ma-digest-{date.today().isoformat()}.docx", mime=DOCX_MIME)
    st.caption(f"{len(result['deals'])} item(s) · {usage.provider.company} usage: {usage.calls} calls, "
               f"{usage.input_tokens:,} input / {usage.output_tokens:,} output tokens ≈ \\${usage.cost:.3f}")
    if result["stale"]:
        st.caption(f"{result['stale']} older deal(s) left out because they were announced or "
                   "completed before the look-back window.")
    if result["failed"]:
        first_error = result["failed"][0][:200].replace("`", "'")
        st.warning(f"{len(result['failed'])} article(s) could not be processed because of API errors. "
                   f"First error: `{first_error}`")
    if result["capped"]:
        st.info(f"{result['capped']} further candidate article(s) were skipped by the "
                "'Max articles sent to the model' limit.")
    if result["deals"]:
        render_digest(result["deals"])
    else:
        st.info("No matching deals found for these settings.")
    with st.sidebar.expander("Source status", expanded=True):
        for name, (ok, message) in result["notes"].items():
            marker = ":green[OK]" if ok else ":orange[Check]"
            st.markdown(f"{marker} **{name}** – {message}")
else:
    st.info("Choose geography, sector and search type in the sidebar, enter your API key, "
            "and click Generate Digest.")
