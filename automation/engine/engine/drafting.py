"""Draft a page from a research brief and render it into the site's page template.

Model drafting is off unless CONTENT_LLM_ENABLED=true (it is a paid API). The
model returns structured JSON; this module, not the model, controls the page
template, scripts and structured data, and it strips any HTML outside a small
allowlist. Every draft then goes through quality.check before it can be proposed.
"""
import html
import json
import os
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlsplit

MODEL = "claude-opus-5-5"
CHECKOUT = "https://tlink.peakbot.com/checkout/price_1UDhh0BZsQfGLBPdlnScmYrH"
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "meta_description": {"type": "string"},
        "h1": {"type": "string"},
        "intro_html": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {"heading": {"type": "string"}, "html": {"type": "string"}},
            "required": ["heading", "html"], "additionalProperties": False}},
        "faq": {"type": "array", "items": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
            "required": ["question", "answer"], "additionalProperties": False}},
        "sources": {"type": "array", "items": {
            "type": "object",
            "properties": {"url": {"type": "string"}, "label": {"type": "string"}},
            "required": ["url", "label"], "additionalProperties": False}},
        "unverifiable_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "meta_description", "h1", "intro_html", "sections", "faq", "sources",
                 "unverifiable_points"],
    "additionalProperties": False,
}
SYSTEM = """You write pages for usetradelink.com, the site of TradeLink, a server-side futures trade copier.
Write for a futures trader deciding how to copy trades across accounts. Be specific, plain and brief.
Rules you must follow:
- Use only the TradeLink facts listed in the brief under allowed_facts. Never use blocked_facts.
- Never invent tests, results, quotes, user stories, author credentials, partnerships or compatibility.
- Any figure, plan detail or rule about a competitor, broker or prop firm must come from a source URL you list,
  and the paragraph stating it must link to that URL. If you cannot source a point, leave it out and list it
  in unverifiable_points.
- Do not reuse sentences from TradeLink's existing pages. Answer the main question in the first paragraph.
- Use the head keyword naturally; never repeat it for its own sake.
- HTML in intro_html, sections[].html and faq answers may only use p, ul, ol, li, a, strong, em, table, thead,
  tbody, tr, th, td. No scripts, styles, images, forms or attributes other than href.
- Only include FAQ entries for questions listed in the brief. Otherwise return an empty faq list."""

ALLOWED_TAGS = {"p", "ul", "ol", "li", "a", "strong", "em", "table", "thead", "tbody", "tr", "th", "td", "code", "br"}


class Sanitizer(HTMLParser):
    """Keeps allowlisted tags; drops everything else (and the text inside script/style)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.drop = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object", "svg", "template"):
            self.drop += 1
            return
        if self.drop or tag not in ALLOWED_TAGS:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            parts = urlsplit(href)
            if parts.scheme not in ("http", "https") and not href.startswith("/"):
                return self.out.append("<a>")
            rel = ' rel="noopener"' if parts.scheme and parts.hostname != "usetradelink.com" else ""
            return self.out.append(f'<a href="{html.escape(href, quote=True)}"{rel}>')
        self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object", "svg", "template"):
            self.drop = max(0, self.drop - 1)
            return
        if not self.drop and tag in ALLOWED_TAGS and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.drop:
            self.out.append(html.escape(data, quote=False))


def sanitize(fragment):
    s = Sanitizer()
    s.feed(fragment or "")
    s.close()
    return "".join(s.out)


def strip_tags(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sanitize(fragment))).strip()


def render_page(draft, brief, today=None):
    """Fill the site's guide template. Only sanitized model HTML reaches the page body."""
    today = today or date.today()
    url = brief["target_url"]
    e = lambda s: html.escape(s or "", quote=True)
    crumbs_name = draft["h1"][:60]
    graph = [
        {"@type": "Article", "headline": draft["h1"], "description": draft["meta_description"],
         "datePublished": today.isoformat(), "dateModified": today.isoformat(),
         "author": {"@type": "Organization", "name": "PeakBot", "url": "https://peakbot.com/"},
         "publisher": {"@type": "Organization", "name": "PeakBot", "url": "https://peakbot.com/"},
         "mainEntityOfPage": url, "image": "https://usetradelink.com/assets/TradeLink_Social_Preview_1200x630.png"},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://usetradelink.com/"},
            {"@type": "ListItem", "position": 2, "name": "Resources", "item": "https://usetradelink.com/resources/"},
            {"@type": "ListItem", "position": 3, "name": crumbs_name, "item": url}]},
    ]
    faq = [f for f in draft.get("faq", []) if f["question"].strip() and f["answer"].strip()]
    if faq:
        graph.append({"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["question"].strip(),
             "acceptedAnswer": {"@type": "Answer", "text": strip_tags(f["answer"])}} for f in faq]})
    jsonld = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False).replace("</", "<\\/")
    sections = "\n".join(f'<section class="section"><h2>{e(s["heading"])}</h2>{sanitize(s["html"])}</section>'
                         for s in draft["sections"])
    faq_html = ""
    if faq:
        items = "".join(f'<div class="faq-item"><h3>{e(f["question"].strip())}</h3><p>{e(strip_tags(f["answer"]))}</p></div>'
                        for f in faq)
        faq_html = f'<section class="section" id="faq"><h2>Frequently asked questions</h2>{items}</section>'
    sources = "".join(f'<li><a href="{e(s["url"])}" rel="noopener">{e(s["label"])}</a></li>'
                      for s in draft.get("sources", []) if urlsplit(s["url"]).scheme == "https")
    links = " · ".join(f'<a href="{e(l["url"].replace("https://usetradelink.com", ""))}">{e(l["title"])}</a>'
                       for l in brief.get("internal_links", []))
    return f"""<!doctype html>
<html lang="en" data-page-type="guide">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(draft["title"])}</title>
<meta name="description" content="{e(draft["meta_description"])}">
<meta name="robots" content="index,follow,max-image-preview:large">
<link rel="canonical" href="{e(url)}">
<link rel="icon" href="/assets/favicon-32.jpg" sizes="32x32"><link rel="apple-touch-icon" href="/assets/apple-touch-icon.jpg"><link rel="stylesheet" href="/seo.css">
<script src="/tracking.js" async></script>
<meta property="og:type" content="article"><meta property="og:site_name" content="TradeLink by PeakBot">
<meta property="og:title" content="{e(draft["title"])}"><meta property="og:description" content="{e(draft["meta_description"])}">
<meta property="og:url" content="{e(url)}"><meta property="og:image" content="https://usetradelink.com/assets/TradeLink_Social_Preview_1200x630.png">
<script type="application/ld+json">{jsonld}</script>
</head>
<body>
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-NP74LT9B" height="0" width="0" style="display:none;visibility:hidden" title="Google Tag Manager"></iframe></noscript>
<a class="skip" href="#content">Skip to content</a>
<header class="top"><div class="wrap top-inner"><a class="brand" href="https://usetradelink.com/" aria-label="TradeLink home"><img src="/assets/logo.png" alt="TradeLink by PeakBot" width="154" height="41"></a><nav class="nav" aria-label="Main navigation"><a href="https://usetradelink.com/">Home</a><a href="/resources/">Resources</a><a href="/#pricing">Pricing</a><a class="button" data-cta="header" href="{CHECKOUT}">Get TradeLink</a></nav></div></header>
<main id="content"><div class="wrap">
<nav class="crumbs" aria-label="Breadcrumb"><a href="https://usetradelink.com/">Home</a> / <a href="/resources/">Resources</a> / {e(crumbs_name)}</nav>
<section class="hero"><div class="eyebrow">Last verified {today.strftime("%B %-d, %Y")}</div>
<h1>{e(draft["h1"])}</h1>
{sanitize(draft["intro_html"])}
</section>
{sections}
{faq_html}
<section class="section"><h2>Sources</h2><ul class="source-list">{sources}</ul></section>
<section class="section"><div class="callout"><h2>Copy futures trades with TradeLink</h2><p>One $40/month plan for up to 150 accounts, connected through Tradovate.</p><a class="button" data-cta="footer-callout" href="{CHECKOUT}">Start TradeLink</a></div></section>
</div>
<section class="section" aria-label="Related guides"><div class="wrap"><h2>Related guides</h2><p>{links or '<a href="/resources/">All futures copier guides</a>'}</p></div></section>
</main>
<footer class="footer"><div class="wrap"><p>© {today.year} TradeLink by PeakBot. Educational product information, not financial advice.</p><p>Futures trading involves substantial risk of loss. Copying does not guarantee fills, consistency, compliance or profit.</p></div></footer>
</body>
</html>
"""


def user_prompt(brief, existing_titles):
    payload = {k: brief[k] for k in ("action", "content_type", "head_keyword", "keywords", "why",
                                     "questions_people_search", "current_queries", "competitor_pages",
                                     "allowed_facts", "blocked_facts", "forbidden_claims", "requirements")}
    return ("Research brief (JSON):\n" + json.dumps(payload, default=str, indent=1) +
            "\n\nExisting TradeLink pages (do not duplicate their angle):\n" +
            "\n".join(f"- {t}" for t in existing_titles) +
            "\n\nWrite the page. Return only the JSON object described by the schema.")


def draft_with_claude(brief, existing_titles, client=None):
    """Return (draft_dict, usage) or (None, reason). Requires CONTENT_LLM_ENABLED=true."""
    if os.getenv("CONTENT_LLM_ENABLED", "false").lower() != "true":
        return None, "content_llm_disabled"
    import anthropic
    client = client or anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=os.getenv("CONTENT_LLM_MODEL", MODEL),
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": user_prompt(brief, existing_titles)}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.RateLimitError:
        return None, "rate_limited"
    except anthropic.APIStatusError as exc:
        return None, f"api_error_{exc.status_code}"
    except anthropic.APIConnectionError:
        return None, "network_error"
    if message.stop_reason == "refusal":
        return None, "refused"
    if message.stop_reason == "max_tokens":
        return None, "truncated"
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        draft = json.loads(text)
    except ValueError:
        return None, "invalid_json"
    usage = {"input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens,
             "model": message.model}
    return draft, usage
