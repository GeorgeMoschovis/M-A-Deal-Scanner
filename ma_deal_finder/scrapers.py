"""Source definitions and scrapers. Each scraper returns (articles, note)."""

import html
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from itertools import zip_longest
from urllib.parse import unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import warnings

import feedparser
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

USER_AGENT = "Mozilla/5.0 (compatible; ma-deal-finder/0.1; local research tool)"
ROBOTS_TOKEN = "ma-deal-finder"
TIMEOUT = 20
FETCH_WORKERS = 4
SOURCE_WORKERS = 4  # sources are separate sites, so each site still sees at most FETCH_WORKERS requests
POLITE_DELAY = 0.3


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    kind: str  # "html", "rss" or "edgar"
    urls: tuple = ()
    article_re: str = ""


@dataclass
class Article:
    url: str
    title: str
    text: str
    source: str
    published: datetime | None = None


GREEK_SOURCES = [
    Source("euro2day", "Euro2day", "html",
           ("https://www.euro2day.gr/taggroups/3/exagores--syghoneyseis.html",), r"/article/\d+/"),
    Source("capital", "Capital.gr", "html",
           ("https://www.capital.gr/epixeiriseis",), r"/\d{6,}/"),
    Source("moneyreview", "MoneyReview", "html",
           ("https://www.moneyreview.gr/business-and-finance/",), r"/\d{5,}/[^/]+/?$"),
    Source("ot", "OT.gr", "html",
           ("https://www.ot.gr/category/epixeiriseis/", "https://www.ot.gr/category/agores/"),
           r"/20\d\d/\d\d/\d\d/"),
    Source("newmoney", "Newmoney", "html",
           ("https://www.newmoney.gr/category/epixeiriseis/",), r"/roh/[^/]+/[^/]+/[^/]{20,}"),
    Source("powergame", "PowerGame", "html",
           ("https://www.powergame.gr/category/epichirisis/",), r"/epichirisis/\d+/"),
    Source("energypress", "Energypress", "html",
           ("https://www.energypress.gr/",), r"/news/[^/]{20,}$"),
    Source("naftemporiki", "Naftemporiki", "html",
           ("https://www.naftemporiki.gr/business/",), r"/(?:business|finance/[a-z-]+)/\d+/"),
    Source("businessdaily", "Businessdaily", "html",
           ("https://www.businessdaily.gr/epiheiriseis",), r"/\d{5,}_"),
]

GREEK_SECONDARY = [
    Source("mononews", "Mononews", "html",
           ("https://www.mononews.gr/category/business",), r"/business/(?:[^/]+/)?[^/]*(?:-[^/]*){5,}$"),
    Source("insider", "Insider.gr", "html",
           ("https://www.insider.gr/oikonomia/ellada",), r"/(?:oikonomia|epiheiriseis)/\d+/"),
    Source("liberal", "Liberal.gr", "html",
           ("https://www.liberal.gr/katigories/epiheiriseis",), r"/epiheiriseis/[^/]+$"),
    Source("bankingnews", "Bankingnews", "html", ("https://www.bankingnews.gr/",)),
]

EDGAR = Source("edgar", "SEC EDGAR 8-K", "edgar", ("https://efts.sec.gov/LATEST/search-index",))
GLOBENEWSWIRE = Source(
    "globenewswire", "GlobeNewswire M&A", "rss",
    ("https://www.globenewswire.com/RssFeed/subjectcode/27-Mergers%20And%20Acquisitions"
     "/feedTitle/GlobeNewswire%20-%20Mergers%20And%20Acquisitions",))
PRNEWSWIRE_US = Source(
    "prn_us", "PR Newswire M&A (US)", "rss",
    ("https://www.prnewswire.com/rss/financial-services-latest-news/"
     "acquisitions-mergers-and-takeovers-list.rss",))
PRNEWSWIRE_UK = Source(
    "prn_uk", "PR Newswire M&A (UK/Europe)", "rss",
    ("https://www.prnewswire.co.uk/rss/financial-services-latest-news/"
     "acquisitions-mergers-and-takeovers-list.rss",))

GEO_SOURCES = {
    "Greece": GREEK_SOURCES + GREEK_SECONDARY,
    "United States": [EDGAR, GLOBENEWSWIRE, PRNEWSWIRE_US],
    "United Kingdom": [GLOBENEWSWIRE, PRNEWSWIRE_UK],
    "Rest of Europe": [GLOBENEWSWIRE, PRNEWSWIRE_UK],
}
GEOGRAPHIES = list(GEO_SOURCES)


def sources_for(geographies):
    seen, out = set(), []
    for geo in geographies:
        for src in GEO_SOURCES[geo]:
            if src.key not in seen:
                seen.add(src.key)
                out.append(src)
    return out


def default_sources(geographies):
    secondary = {s.key for s in GREEK_SECONDARY}
    return [s for s in sources_for(geographies) if s.key not in secondary]


_robots = {}


def _load_robots(root):
    try:
        r = requests.get(f"{root}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=10)
    except requests.RequestException:
        return None
    parser = RobotFileParser()
    if r.status_code == 200:
        parser.parse(r.text.splitlines())
        return parser
    if r.status_code >= 500:
        parser.disallow_all = True
        return parser
    return None


def allowed(url):
    parts = urlparse(url)
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _robots:
        _robots[root] = _load_robots(root)
    parser = _robots[root]
    return parser is None or parser.can_fetch(ROBOTS_TOKEN, url)


def http_get(url, headers=None, timeout=TIMEOUT):
    merged = {"User-Agent": USER_AGENT, "Accept-Language": "el,en;q=0.8", **(headers or {})}
    r = requests.get(url, headers=merged, timeout=timeout)
    r.raise_for_status()
    return r


def _bare_host(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def _as_utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        return _as_utc(parsedate_to_datetime(value))
    except (TypeError, ValueError):
        return None


def url_date(url):
    m = re.search(r"/(20\d\d)/(\d\d)/(\d\d)/", url)
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
    except ValueError:
        return None


def since_cutoff(days):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days)


def _looks_like_article(path):
    segments = [s for s in path.split("/") if s]
    if not segments or segments[0] in {"tag", "tags", "category", "author", "page", "search"}:
        return False
    return segments[-1].count("-") >= 4


def extract_links(soup, page_url, src):
    host = _bare_host(page_url)
    seen, links = set(), []
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"]).split("#")[0].split("?")[0]
        if not url.startswith(("http://", "https://")) or _bare_host(url) != host:
            continue
        path = unquote(urlparse(url).path)
        matches = re.search(src.article_re, path) if src.article_re else _looks_like_article(path)
        if matches and url not in seen:
            seen.add(url)
            links.append(url)
    return links


DATE_META = [
    ("property", "article:published_time"), ("property", "og:article:published_time"),
    ("itemprop", "datePublished"), ("name", "date"), ("name", "pubdate"),
    ("name", "publish-date"), ("name", "publication_date"), ("name", "DC.date.issued"),
]
NOISE_TAGS = ["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe"]
BODY_SELECTORS = (
    "article, [itemprop=articleBody], [class*=article-body], [class*=article__body], "
    "[class*=entry-content], [class*=post-content], [class*=story-body], [class*=blog-body]"
)
NOISE_SELECTORS = "[class*=comment], [id*=comment], [class*=related], [class*=share]"


def _meta(soup, attr, value):
    tag = soup.find("meta", attrs={attr: value})
    return tag["content"].strip() if tag and tag.get("content") else ""


def _find_date(soup):
    for attr, value in DATE_META:
        dt = parse_date(_meta(soup, attr, value))
        if dt:
            return dt
    for script in soup.find_all("script", type="application/ld+json"):
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', script.string or "")
        if m and (dt := parse_date(m[1])):
            return dt
    time_tag = soup.find("time", attrs={"datetime": True})
    return parse_date(time_tag["datetime"]) if time_tag else None


def _paragraphs(container):
    parts = [p.get_text(" ", strip=True) for p in container.find_all(["p", "h2", "h3", "li"])]
    parts = [p for p in parts if len(p) >= 25]
    if sum(map(len, parts)) < 300:
        # some sites put the body straight into the container, separated by <br>
        parts = [line for line in container.get_text("\n", strip=True).split("\n") if len(line) >= 25]
    return parts


def parse_article_page(content, url):
    soup = BeautifulSoup(content, "lxml")
    h1 = soup.find("h1")
    title = _meta(soup, "property", "og:title") or (h1.get_text(" ", strip=True) if h1 else "")
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    published = _find_date(soup) or url_date(url)
    best = []
    for container in soup.select(BODY_SELECTORS) or [soup.body or soup]:
        if container.decomposed:
            continue
        for tag in container(NOISE_TAGS) + container.select(NOISE_SELECTORS):
            if not tag.decomposed:
                tag.decompose()
        paragraphs = _paragraphs(container)
        if sum(map(len, paragraphs)) > sum(map(len, best)):
            best = paragraphs
    return title, published, "\n".join(best)


def fetch_article(url, source_name):
    time.sleep(POLITE_DELAY)
    try:
        r = http_get(url)
    except requests.RequestException:
        return None
    title, published, text = parse_article_page(r.content, r.url)
    if len(text) < 200:
        return None
    return Article(r.url, title, text, source_name, published)


def _interleave(lists):
    merged = [x for group in zip_longest(*lists) for x in group if x]
    return list(dict.fromkeys(merged))


def scrape_html(src, since, max_articles):
    per_listing, errors = [], []
    for listing in src.urls:
        if not allowed(listing):
            errors.append("robots.txt disallows the listing page")
            continue
        try:
            r = http_get(listing)
        except requests.RequestException as exc:
            errors.append(f"listing unreachable ({type(exc).__name__})")
            continue
        per_listing.append(extract_links(BeautifulSoup(r.content, "lxml"), r.url, src))
    links = _interleave(per_listing)
    if not links:
        return [], "; ".join(errors) or "no article links found on the listing page"
    links = [u for u in links if (d := url_date(u)) is None or d >= since]
    links = [u for u in links if allowed(u)][:max_articles]
    with ThreadPoolExecutor(FETCH_WORKERS) as pool:
        fetched = list(pool.map(lambda u: fetch_article(u, src.name), links))
    articles = [a for a in fetched if a and (a.published is None or a.published >= since)]
    undated = sum(a.published is None for a in articles)
    if undated:
        errors.append(f"{undated} article(s) show no publication date; kept because they come from the latest-news listing")
    if not articles and not errors:
        errors.append("no articles within the look-back window")
    return articles, "; ".join(errors)


def _entry_date(entry):
    for field in ("published", "updated"):
        dt = parse_date(entry.get(field))
        if dt:
            return dt
    return None


def scrape_feed(src, since, max_articles):
    r = http_get(src.urls[0], timeout=30)
    feed = feedparser.parse(r.content)
    articles = []
    for entry in feed.entries:
        published = _entry_date(entry)
        if published and published < since:
            continue
        summary = BeautifulSoup(entry.get("summary", ""), "lxml").get_text(" ", strip=True)
        articles.append(Article(entry.link, html.unescape(entry.get("title", "")), summary,
                                src.name, published))
        if len(articles) >= max_articles:
            break
    for article in articles:
        if allowed(article.url):
            full = fetch_article(article.url, src.name)
            if full and len(full.text) > len(article.text):
                article.text = full.text
    note = "" if articles else "no items in the feed within the look-back window"
    return articles, note


EDGAR_QUERY = ('"merger agreement" OR "purchase agreement" OR "definitive agreement" '
               'OR "tender offer" OR "acquisition"')
EDGAR_ITEMS = {"1.01", "2.01"}
SPAC_SIC = "6770"


def _edgar_doc_url(hit):
    adsh, filename = hit["_id"].split(":", 1)
    cik = int(hit["_source"]["ciks"][0])
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{filename}"


def _edgar_text(content):
    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "ix:header"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    m = re.search(r"Item\s+(?:1\.01|2\.01)", text)
    return text[m.start():] if m else text


def scrape_edgar(since, max_articles, contact):
    if not contact.strip():
        return [], "skipped: enter a contact email in the sidebar (SEC requires one in the User-Agent)"
    headers = {"User-Agent": f"ma-deal-finder/0.1 personal research tool ({contact.strip()})",
               "Accept-Encoding": "gzip, deflate"}
    params = {"q": EDGAR_QUERY, "forms": "8-K", "dateRange": "custom",
              "startdt": since.date().isoformat(), "enddt": datetime.now(timezone.utc).date().isoformat()}
    r = requests.get(EDGAR.urls[0], params=params, headers=headers, timeout=30)
    r.raise_for_status()
    hits, filings = [], set()
    for hit in r.json()["hits"]["hits"]:
        source = hit["_source"]
        if not EDGAR_ITEMS & set(source.get("items") or []) or SPAC_SIC in (source.get("sics") or []):
            continue
        # the index lists a filing's exhibits as separate hits; keep the top-ranked one
        if source["adsh"] not in filings:
            filings.add(source["adsh"])
            hits.append(hit)
    articles = []
    for hit in hits[:max_articles]:
        time.sleep(0.2)
        url = _edgar_doc_url(hit)
        try:
            doc = requests.get(url, headers=headers, timeout=30)
            doc.raise_for_status()
        except requests.RequestException:
            continue
        source = hit["_source"]
        name = re.sub(r"\s*\((?:CIK|[A-Z.\-, ]+\)).*", "", source["display_names"][0]).strip()
        items = ", ".join(sorted(EDGAR_ITEMS & set(source["items"])))
        articles.append(Article(url, f"{name} – 8-K Item {items}", _edgar_text(doc.content),
                                EDGAR.name, parse_date(source.get("file_date"))))
    return articles, "" if articles else "no matching 8-K filings found"


def collect(src, since, max_articles, sec_contact=""):
    try:
        if src.kind == "html":
            return scrape_html(src, since, max_articles)
        if src.kind == "rss":
            return scrape_feed(src, since, max_articles)
        return scrape_edgar(since, max_articles, sec_contact)
    except Exception as exc:  # one broken source must not abort the whole run
        return [], f"failed ({type(exc).__name__})"


def collect_all(sources, since, max_articles, sec_contact="", on_done=None):
    """Scrapes SOURCE_WORKERS sources at a time; returns [(articles, note)] in the order of `sources`.

    on_done(src, articles, note) runs in the calling thread as each source finishes.
    """
    results = {}
    with ThreadPoolExecutor(SOURCE_WORKERS) as pool:
        futures = {pool.submit(collect, src, since, max_articles, sec_contact): src for src in sources}
        for future in as_completed(futures):
            src = futures[future]
            results[src.key] = future.result()
            if on_done:
                on_done(src, *results[src.key])
    return [results[src.key] for src in sources]


def normalise(text):
    stripped = unicodedata.normalize("NFD", text)
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()
