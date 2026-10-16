import os

ENGINE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO = os.path.abspath(os.path.join(ENGINE, "..", ".."))
DB = os.getenv("TEST_DATABASE_URL", "")


def page(url, title, h1=None, headings=(), links=(), modified=None, text=""):
    return {"url": url, "title": title, "h1": h1 or title, "headings": list(headings),
            "internal_links": list(links), "date_modified": modified, "text": text}


def sig(volume=None, kd=None, impressions=0, clicks=0, position=None, pages=None, competitors=(), intents=()):
    s = {"volume": volume, "kd": kd, "intents": list(intents), "competitors": list(competitors),
         "evidence": [{"source": "test", "metric": "volume", "value": volume}]}
    if impressions:
        s["gsc"] = {"impressions": impressions, "clicks": clicks, "position": position, "pages": pages or {}}
    return s


def good_article(extra=""):
    """A varied, sourced, ~900-word article body used to prove the gate can pass."""
    topics = ["leader account", "follower sizing", "order rejection", "daily loss rule", "partial fill",
              "Tradovate login", "evaluation account", "funded account", "contract choice", "session timing",
              "network outage", "manual flatten", "copy group", "fixed quantity", "multiplier setting"]
    paras = []
    for i, t in enumerate(topics):
        paras.append(
            f"<p>Point {i + 1} concerns the {t}. Traders who manage several accounts often check the {t} "
            f"before the open on day {i + 3}, because a mismatch in group {chr(65 + i)} can leave follower "
            f"{i + 2} with a different position than the leader. Write down what the {t} should be for each "
            f"account, compare it with the broker screen after the first fill, and keep note {i * 7 + 1} in "
            f"a journal so the next review takes minutes instead of an hour of guesswork for setup {i + 11}.</p>")
    return ("<!doctype html><html lang='en'><head><title>How to Copy Trades on Tradovate Step by Step</title>"
            "<meta name='description' content='A practical walkthrough of copying futures trades across Tradovate "
            "accounts, with the checks that prevent leader and follower drift.'>"
            "<link rel='canonical' href='https://usetradelink.com/guides/how-to-copy-trade-on-tradovate/'>"
            "</head><body><h1>How to copy trades on Tradovate</h1>" + "".join(paras) + extra + "</body></html>")
