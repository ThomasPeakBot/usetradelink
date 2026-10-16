"""Quality and accuracy gate for generated or edited pages.

A draft passes only if it is substantively different from existing pages, uses
only published TradeLink facts, cites a source for every third-party figure,
and avoids keyword stuffing and stock filler. The gate judges substance, not
whether a person or a model wrote the text.
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

FACTS = Path(__file__).resolve().parent.parent / "facts" / "tradelink_facts.json"
MIN_WORDS = {"landing": 600, "article": 700, "comparison": 900, "update": 0}
# Claims that are never acceptable in TradeLink copy.
FORBIDDEN = [
    r"\bguarantee[sd]?\b", r"\brisk[- ]free\b", r"\bundetectable\b", r"\bnever lose\b",
    r"\b100% (accurate|safe|reliable|uptime)\b", r"\bzero (latency|slippage)\b",
    r"\b(we|our team) (tested|have tested|personally tested)\b", r"\bour (users|customers|traders) (say|report|love)\b",
    r"\btestimonial", r"\b(approved|endorsed|certified) by\b", r"\bbypass(es|ing)? (prop firm )?rules\b",
]
# Stock phrasing that signals generic, low-effort copy.
FILLER = [
    r"in today'?s fast[- ]paced", r"\bdelve\b", r"\bgame[- ]changer\b", r"\bunlock (the|your)\b",
    r"\bseamless(ly)?\b", r"look no further", r"ever[- ]evolving", r"navigating the (world|landscape)",
    r"it'?s (important|worth) (to note|noting)", r"\bin conclusion\b", r"\belevate your\b",
    r"\brevolutioni[sz]e", r"\bharness the power\b", r"\btake your trading to the next level\b",
]
COMPAT = re.compile(r"\b(?:supports?|works with|compatible with|connects? (?:to|with|through)|integrates? with)\s+"
                    r"((?:[A-Z]\w*)(?:\s*(?:,|and|or)\s*[A-Z]\w*)*)")
NEGATION = re.compile(r"\b(not|no|never|cannot|neither|nor|without|coming soon|not yet)\b|n't\b", re.I)
MONEY_OR_PCT = re.compile(r"\$\s?\d[\d,.]*|\b\d+(?:\.\d+)?\s?%")
VERIFIED_DATE = re.compile(r"\b(last (verified|checked|updated)|checked on|verified on|as of)\b", re.I)


class Blocks(HTMLParser):
    """Text blocks (p, li, td, th, h*) with the links inside each."""
    BLOCK = {"p", "li", "td", "th", "h1", "h2", "h3", "h4", "figcaption", "blockquote", "dd", "dt"}
    SKIP = {"script", "style", "noscript", "nav", "footer", "header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.title = ""
        self.meta = ""
        self.canonical = ""
        self.h1 = 0
        self._cur = None
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and a.get("name", "").lower() == "description":
            self.meta = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").split():
            self.canonical = a.get("href", "")
        if tag == "h1":
            self.h1 += 1
        if tag in self.BLOCK and not self._skip:
            self._cur = {"tag": tag, "text": [], "links": [], "class": a.get("class", "")}
        elif tag == "a" and self._cur is not None and a.get("href"):
            self._cur["links"].append(a["href"])

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK and self._cur is not None and self._cur["tag"] == tag:
            text = re.sub(r"\s+", " ", "".join(self._cur["text"])).strip()
            if text:
                self.blocks.append({"tag": tag, "text": text, "links": self._cur["links"],
                                    "class": self._cur["class"]})
            self._cur = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._cur is not None and not self._skip:
            self._cur["text"].append(data)


def load_facts(path=FACTS):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def shingles(text, n=5):
    words = re.findall(r"[a-z0-9$%']+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 12]


def external(href, site_host="usetradelink.com"):
    host = urlsplit(href).hostname or ""
    return bool(host) and host != site_host and not host.endswith(".peakbot.com")


def check(html, path, existing_texts, content_type="article", head_keyword="", facts=None,
          competitor_domains=None):
    """Return {"passed", "errors", "warnings", "metrics"} for a draft page.

    existing_texts: {url: visible text} of current pages (the page being edited excluded).
    competitor_domains: {"brand name": "domain"} used to require first-party sources.
    """
    facts = facts or load_facts()
    competitor_domains = competitor_domains or {}
    doc = Blocks()
    doc.feed(html)
    doc.close()
    text = " ".join(b["text"] for b in doc.blocks)
    words = len(text.split())
    errors, warnings = [], []

    # Structure
    title = re.sub(r"\s+", " ", doc.title).strip()
    if doc.h1 != 1:
        errors.append(f"needs exactly one <h1> (found {doc.h1})")
    if not title or len(title) > 65:
        errors.append(f"title must be 1-65 characters (found {len(title)})")
    if not 70 <= len(doc.meta.strip()) <= 160:
        errors.append(f"meta description must be 70-160 characters (found {len(doc.meta.strip())})")
    expected = "https://usetradelink.com/" + path[:-len("index.html")] if path.endswith("index.html") else None
    if expected and doc.canonical != expected:
        errors.append(f"canonical must be {expected}")
    minimum = MIN_WORDS.get(content_type, 600)
    if words < minimum:
        errors.append(f"thin content: {words} words, need at least {minimum} for a {content_type}")

    # Originality against our own pages
    mine = shingles(text)
    overlap = {}
    for url, other in existing_texts.items():
        theirs = shingles(other)
        if mine and theirs:
            j = len(mine & theirs) / len(mine | theirs)
            overlap[url] = round(j, 3)
            if j > 0.2:
                errors.append(f"too similar to {url} (5-word shingle overlap {j:.0%})")
    # Disclosure notes ("Published by PeakBot, the maker of TradeLink…") are meant to repeat.
    body = " ".join(b["text"] for b in doc.blocks if "note" not in b["class"].split())
    reused = [s for s in sentences(body) if any(s in other for other in existing_texts.values())]
    if len(reused) > 2:
        errors.append(f"{len(reused)} sentences copied verbatim from existing pages, e.g. {reused[0][:90]!r}")

    # Keyword stuffing
    if head_keyword and words:
        hits = len(re.findall(re.escape(head_keyword.lower()), text.lower()))
        density = hits * 100 / words
        if density > 2.5:
            errors.append(f"keyword stuffing: '{head_keyword}' used {hits} times ({density:.1f} per 100 words)")
        if title.lower().count(head_keyword.lower()) > 1:
            warnings.append("head keyword repeated in title")

    # Forbidden claims and filler
    low = text.lower()
    all_sentences = [x for b in doc.blocks for x in re.split(r"(?<=[.!?])\s+", b["text"])]
    for pattern in FORBIDDEN:
        for sentence in all_sentences:
            m = re.search(pattern, sentence.lower())
            # "Copying does not guarantee fills" is a disclaimer, not a claim.
            if m and not sentence.strip().endswith("?") and \
                    not NEGATION.search(sentence.lower()[:m.start()][-40:]):
                errors.append(f"forbidden claim: {m.group(0)!r} in {sentence[:90]!r}")
                break
    filler = [m.group(0) for p in FILLER for m in [re.search(p, low)] if m]
    if len(filler) >= 2:
        errors.append(f"generic filler phrasing: {', '.join(repr(f) for f in filler)}")
    elif filler:
        warnings.append(f"generic filler phrasing: {filler[0]!r}")
    if re.search(r"\btradelink (advertises|publicly lists|claims)\b", low):
        warnings.append("describes TradeLink in the third person ('TradeLink advertises'); state first-party facts plainly")

    # TradeLink facts: disputed or unconfirmed facts may not be used at all.
    # Judged per sentence: only statements about TradeLink itself, and not negated or questions.
    clauses = [c for x in all_sentences if not x.strip().endswith("?")
               for c in re.split(r";|,| while | whereas | but ", x)]
    about_us = [c for c in clauses if "tradelink" in c.lower()]
    for fact in facts["facts"]:
        if fact["status"] == "published":
            continue
        for pattern in fact.get("patterns", []):
            hit = next((x for x in about_us if re.search(pattern, x, re.I)), None)
            if hit:
                errors.append(f"uses {fact['status']} fact '{fact['id']}' in {hit[:90]!r}: "
                              f"{fact.get('note', fact['statement'])}")
                break
    allowed_platforms = {"tradovate"}
    for sentence in about_us:
        if NEGATION.search(sentence):
            continue
        for m in COMPAT.finditer(sentence):
            subject = sentence[:m.start()].lower()
            if "tradelink" not in subject[-60:]:
                continue  # the sentence is about another product
            names = re.split(r"\s*(?:,|and|or)\s*", m.group(1))
            bad = [n for n in names if n and n.lower() not in allowed_platforms and n.lower() != "tradelink"]
            if bad:
                errors.append(f"unverified compatibility claim: {m.group(0)!r}")

    # Figures and competitor statements need a cited source in the same block.
    published_money = [p for f in facts["facts"] if f["status"] == "published" for p in f.get("patterns", [])]
    needs_date = False
    page_hosts = {urlsplit(h).hostname or "" for b in doc.blocks for h in b["links"] if external(h)}
    for block in doc.blocks:
        t = block["text"]
        # Table cells and list items can rely on the page's cited sources; paragraphs must cite inline.
        inline = block["tag"] not in ("td", "th", "li")
        links = block["links"] if inline else [f"https://{h}/" for h in page_hosts]
        has_source = any(external(h) for h in links)
        figures = [m.group(0).rstrip(",.;") for m in MONEY_OR_PCT.finditer(t)]
        figures = [f for f in figures if not any(re.fullmatch(p, f.replace(" ", "")) for p in published_money)]
        if figures and not has_source:
            errors.append(f"unsourced figure {figures[0]!r} in: {t[:90]!r}")
        for brand, domain in competitor_domains.items():
            if re.search(rf"\b{re.escape(brand)}\b", t, re.I) and (figures or re.search(
                    r"\b(trial|plans?|supports?|limits?|lists?)\b", t, re.I)):
                needs_date = True
                if not any((urlsplit(h).hostname or "").endswith(domain) for h in links):
                    errors.append(f"claim about {brand} needs a link to {domain} in the same paragraph: {t[:90]!r}")
    if needs_date and not VERIFIED_DATE.search(text):
        errors.append("third-party claims present but no 'last verified' date on the page")

    return {"passed": not errors, "errors": errors, "warnings": warnings,
            "metrics": {"words": words, "title_length": len(title), "max_overlap": max(overlap.values(), default=0),
                        "overlap": overlap}}
