"""Keyword normalisation, topical relevance and clustering.

Relevance is rule-based and conservative so the engine never chases traffic
TradeLink cannot serve (forex, crypto, MT4/5, stock signals).
"""
import re

STOPWORDS = {"a", "an", "the", "for", "to", "of", "on", "in", "and", "or", "with", "is",
             "are", "can", "you", "i", "how", "what", "does", "do", "best", "my", "your",
             "vs", "versus", "between", "across", "multiple"}
CORE = re.compile(r"\b(trade copier|copy ?trad(e|er|ing)|copier|tradovate|group trade)\b", re.I)
FUTURES = re.compile(r"\b(futures?|prop( firm)?s?|funded|apex|topstep|tradeify|"
                     r"tradovate|ninjatrader|rithmic|mnq|nq|es|eval(uation)?)\b", re.I)
EXCLUDE = re.compile(r"\b(forex|fx|mt4|mt5|metatrader|crypto|bitcoin|binance|bybit|stocks?|"
                     r"options|website copier|httrack|track website|ctrader|zulutrade|etoro)\b", re.I)
QUESTION = re.compile(r"^(how|what|can|does|do|is|are|why|which|when|should|will)\b", re.I)
COMPARISON = re.compile(r"\b(vs|versus|alternative|alternatives|compare|comparison|review|reviews)\b", re.I)
TRANSACTIONAL = re.compile(r"\b(price|pricing|cost|cheap|best|buy|subscription|plan|discount|free)\b", re.I)


def normalize(keyword):
    return re.sub(r"\s+", " ", (keyword or "").strip().lower())


def tokens(text):
    words = re.findall(r"[a-z0-9]+", normalize(text))
    out = set()
    for w in words:
        if w in STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w in ("copying", "copy", "copier", "copie"):
            w = "copy"
        if w in ("trading", "trade", "trader"):
            w = "trade"
        out.add(w)
    return out


def relevance(keyword, competitor_brands=()):
    """0..1 fit with TradeLink's product; 0 means out of scope."""
    k = normalize(keyword)
    if not k or EXCLUDE.search(k):
        return 0.0
    brand = any(b and b in k.replace(" ", "") for b in competitor_brands)
    core = bool(CORE.search(k))
    futures = bool(FUTURES.search(k))
    if core and futures:
        return 1.0
    if brand:
        # Competitor brand terms matter mainly with comparison modifiers.
        return 0.8 if COMPARISON.search(k) else 0.4
    if re.search(r"\btrade copier\b|\bcopier\b", k):
        return 0.6  # the product category, without a market qualifier
    if core:
        # Unqualified "copy trading" is mostly stocks/forex/crypto social trading.
        return 0.3
    if futures and re.search(r"\b(multiple accounts|accounts at once|scale|copy)\b", k):
        return 0.5
    return 0.0


def intent(keyword, provider_intents=()):
    """Coarse intent label; provider intents (Semrush codes or names) win when present."""
    names = {"0": "commercial", "1": "informational", "2": "navigational", "3": "transactional"}
    labels = []
    for value in provider_intents or ():
        for part in str(value).replace(";", ",").split(","):
            part = part.strip().lower()
            if part:
                labels.append(names.get(part, part))
    if labels:
        for preferred in ("transactional", "commercial", "informational", "navigational"):
            if preferred in labels:
                return preferred
    k = normalize(keyword)
    if COMPARISON.search(k):
        return "commercial"
    if TRANSACTIONAL.search(k):
        return "transactional"
    if QUESTION.search(k):
        return "informational"
    return "commercial"


def content_type_for(keyword, competitor_brands=()):
    k = normalize(keyword)
    if COMPARISON.search(k) or any(b and b in k.replace(" ", "") for b in competitor_brands):
        return "comparison"
    if QUESTION.search(k):
        return "article"
    return "landing"


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def cluster(keywords, weight, threshold=0.6):
    """Greedy clustering: heaviest keyword first becomes a cluster head.

    `weight` maps keyword -> demand used to order heads. Returns a list of
    {"head": str, "keywords": [str, ...]}.
    """
    ordered = sorted(set(keywords), key=lambda k: (-weight.get(k, 0), k))
    clusters = []
    for kw in ordered:
        t = tokens(kw)
        for c in clusters:
            if jaccard(t, c["tokens"]) >= threshold:
                c["keywords"].append(kw)
                break
        else:
            clusters.append({"head": kw, "tokens": t, "keywords": [kw]})
    return [{"head": c["head"], "keywords": c["keywords"]} for c in clusters]
