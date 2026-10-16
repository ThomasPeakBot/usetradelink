"""Engine entry point.

    python -m engine.run                       # full scheduled cycle
    python -m engine.run --site-dir ../..      # inventory a local checkout instead of the live site
    python -m engine.run import-snapshot FILE  # load saved Semrush responses as evidence
    python -m engine.run report                # print current opportunities as Markdown
    python -m engine.run audit-content --site-dir ../..   # run the quality gate over existing pages

Paid or outward-facing steps are off by default and gated by env flags:
ENGINE_SEMRUSH_ENABLED, ENGINE_COMPETITOR_CRAWL, CONTENT_LLM_ENABLED, ENGINE_PROPOSALS_ENABLED.
"""
import argparse
import json
import os
import sys
import traceback
import uuid
from datetime import date

import requests

from . import briefs, competitors, drafting, inventory, opportunities, proposals, quality, research, tracking
from .db import connect, migrate
from .signals import build_signals, recent_changes

USER_AGENT = "TradeLinkSEOEngine/1.0 (site-owner research)"


class Run:
    def __init__(self, conn):
        self.conn = conn
        self.id = uuid.uuid4()
        self.steps = {}
        self.errors = []
        with conn.transaction():
            conn.execute("INSERT INTO engine_runs(id) VALUES(%s)", (self.id,))

    def step(self, name, fn, *args):
        try:
            result = fn(*args)
            self.steps[name] = result if isinstance(result, (dict, list, int, str, type(None))) else str(result)
            return result
        except Exception as exc:
            if self.conn.info.transaction_status != 0:
                self.conn.rollback()
            # Class name and location only: no messages that could contain URLs with keys.
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            self.errors.append({"step": name, "error": exc.__class__.__name__,
                                "at": f"{os.path.basename(frame.filename)}:{frame.lineno}"})
            self.steps[name] = "error"
            return None

    def finish(self):
        status = "complete" if not self.errors else "failed"
        with self.conn.transaction():
            self.conn.execute("UPDATE engine_runs SET finished_at=now(),status=%s,steps=%s,errors=%s WHERE id=%s",
                              (status, json.dumps(self.steps, default=str), json.dumps(self.errors), self.id))
        return status


def save_opportunities(conn, run_id, items):
    """Upsert by fingerprint; anything open that this run no longer supports is superseded."""
    seen = []
    with conn.transaction():
        for o in items:
            if o["action"] == "no_action":
                continue
            seen.append(o["fingerprint"])
            score = dict(o["score"], metrics=o.get("metrics", {}))
            row = conn.execute("SELECT id FROM seo_opportunities WHERE fingerprint=%s AND status IN "
                               "('open','briefed','drafted','proposed')", (o["fingerprint"],)).fetchone()
            if row:
                conn.execute("UPDATE seo_opportunities SET run_id=%s,priority=%s,score=%s,evidence=%s,rationale=%s,"
                             "keywords=%s,last_seen=now() WHERE id=%s",
                             (run_id, o["priority"], json.dumps(score, default=str), json.dumps(o["evidence"], default=str),
                              o["rationale"], o["keywords"], row["id"]))
            else:
                conn.execute("INSERT INTO seo_opportunities(id,fingerprint,run_id,action,content_type,target_url,"
                             "head_keyword,keywords,priority,score,evidence,rationale) "
                             "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             (uuid.uuid4(), o["fingerprint"], run_id, o["action"], o["content_type"], o["target_url"],
                              o["head_keyword"], o["keywords"], o["priority"], json.dumps(score, default=str),
                              json.dumps(o["evidence"], default=str), o["rationale"]))
        conn.execute("UPDATE seo_opportunities SET status='superseded' WHERE status='open' AND NOT (fingerprint = ANY(%s))",
                     (seen,))
    counts = {}
    for o in items:
        counts[o["action"]] = counts.get(o["action"], 0) + 1
    return counts


def write_briefs(conn, pages, brands, limit):
    rows = conn.execute(
        "SELECT o.* FROM seo_opportunities o WHERE o.status='open' AND o.action IN "
        "('new_page','improve_page','rewrite_title_meta','refresh_outdated') "
        "AND NOT EXISTS (SELECT 1 FROM seo_content_briefs b WHERE b.opportunity_id=o.id) "
        "ORDER BY priority DESC LIMIT %s", (limit,)).fetchall()
    made = []
    for o in rows:
        o = dict(o)
        brief, md = briefs.build(conn, o, pages, brands)
        with conn.transaction():
            conn.execute("INSERT INTO seo_content_briefs(id,opportunity_id,brief,markdown) VALUES(%s,%s,%s,%s)",
                         (uuid.uuid4(), o["id"], json.dumps(brief, default=str), md))
            conn.execute("UPDATE seo_opportunities SET status='briefed' WHERE id=%s", (o["id"],))
        made.append(o["head_keyword"])
    return made


def draft_and_propose(conn, pages, brands, config):
    """Only for new pages: model draft -> render -> quality gate -> optional PR."""
    summary = {"drafted": 0, "rejected": 0, "proposed": 0, "skipped": None}
    if os.getenv("CONTENT_LLM_ENABLED", "false").lower() != "true":
        summary["skipped"] = "content_llm_disabled"
        return summary
    rows = conn.execute(
        "SELECT b.id AS brief_id, b.brief, o.id AS opp_id FROM seo_content_briefs b JOIN seo_opportunities o "
        "ON o.id=b.opportunity_id WHERE o.status='briefed' AND o.action='new_page' "
        "AND NOT EXISTS (SELECT 1 FROM seo_content_drafts d WHERE d.brief_id=b.id) "
        "ORDER BY o.priority DESC LIMIT %s", (int(os.getenv("ENGINE_DRAFTS_PER_RUN", "1")),)).fetchall()
    texts = {p["url"]: p.get("text", "") for p in pages}
    domains = {c["name"]: c["domain"] for c in config["competitors"]}
    for r in rows:
        brief = r["brief"]
        draft, info = drafting.draft_with_claude(brief, [p["title"] for p in pages])
        if draft is None:
            summary["rejected"] += 1
            continue
        if isinstance(info, dict):
            with conn.transaction():
                conn.execute("INSERT INTO engine_api_usage(provider,report,target,units_reserved,units_reported,success) "
                             "VALUES('anthropic','draft',%s,0,%s,true)",
                             (brief["head_keyword"], info["input_tokens"] + info["output_tokens"]))
        page = drafting.render_page(draft, brief)
        report = quality.check(page, brief["path"], texts, brief["content_type"], brief["head_keyword"],
                               competitor_domains=domains)
        status = "passed" if report["passed"] else "failed_quality"
        draft_id = uuid.uuid4()
        with conn.transaction():
            conn.execute("INSERT INTO seo_content_drafts(id,brief_id,path,html,quality,status) VALUES(%s,%s,%s,%s,%s,%s)",
                         (draft_id, r["brief_id"], brief["path"], page, json.dumps(report), status))
            if report["passed"]:
                conn.execute("UPDATE seo_opportunities SET status='drafted' WHERE id=%s", (r["opp_id"],))
        summary["drafted"] += 1
        if report["passed"] and proposals.enabled():
            files = {brief["path"]: page}
            body = (f"Automated SEO proposal. **Review the diff before approving in Slack.**\n\n"
                    f"Why: {brief['why']}\n\nUnverifiable points the drafter left out: "
                    f"{', '.join(draft.get('unverifiable_points') or []) or 'none'}\n\n"
                    f"Quality gate: passed ({report['metrics']['words']} words, max overlap "
                    f"{report['metrics']['max_overlap']:.0%}). Warnings: {report['warnings'] or 'none'}")
            number = proposals.open_proposal(files, f"SEO: {draft['title']}", body, brief["head_keyword"])
            with conn.transaction():
                conn.execute("UPDATE seo_content_drafts SET status='proposed',pr_number=%s WHERE id=%s", (number, draft_id))
                conn.execute("UPDATE seo_opportunities SET status='proposed' WHERE id=%s", (r["opp_id"],))
            summary["proposed"] += 1
    return summary


def inventory_step(conn, site_dir):
    if site_dir:
        pages = inventory.from_directory(site_dir)
    else:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        pages = inventory.from_site(session)
    inventory.save(conn, pages)
    return pages


def cycle(conn, site_dir=None, today=None):
    today = today or date.today()
    run = Run(conn)
    config = research.load_config()
    brands = [c["brand"] for c in config["competitors"]]
    run.step("competitors_registered", research.ensure_competitors, conn, config)
    pages = run.step("inventory", inventory_step, conn, site_dir) or []
    run.steps["inventory"] = len(pages)
    run.step("semrush", research.run_semrush, conn, config)
    if os.getenv("ENGINE_COMPETITOR_CRAWL", "false").lower() == "true":
        run.step("competitor_pages", competitors.run, conn, config)
    run.step("changes_synced", tracking.sync_changes, conn)
    run.step("changes_evaluated", tracking.evaluate, conn, today)
    signals = run.step("signals", build_signals, conn) or {}
    run.steps["signals"] = len(signals)
    if signals and pages:
        items = opportunities.build(signals, pages, brands, recent_changes(conn), today)
        run.step("opportunities", save_opportunities, conn, run.id, items)
        run.step("briefs", write_briefs, conn, pages, brands, int(os.getenv("ENGINE_BRIEFS_PER_RUN", "5")))
        run.step("drafts", draft_and_propose, conn, pages, brands, config)
    status = run.finish()
    return run, status


def report(conn, limit=40):
    rows = conn.execute("SELECT action,content_type,target_url,head_keyword,priority,rationale,score,status "
                        "FROM seo_opportunities WHERE status IN ('open','briefed','drafted','proposed') "
                        "ORDER BY priority DESC LIMIT %s", (limit,)).fetchall()
    lines = ["| Priority | Action | Keyword | Target | Volume | KD | Why |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        m = (r["score"] or {}).get("metrics", {})
        lines.append(f"| {r['priority']} | {r['action']}{' (' + r['content_type'] + ')' if r['content_type'] else ''} | "
                     f"{r['head_keyword']} | {r['target_url'] or 'new page'} | {m.get('volume') or '—'} | "
                     f"{m.get('kd') if m.get('kd') is not None else '—'} | {r['rationale'].replace('|', '/')} |")
    return "\n".join(lines)


def spend(conn, pause_reason=None, resume=False):
    """Semrush spending this month across the collector and the engine; optionally pause or resume."""
    from .providers.semrush import Semrush
    with conn.transaction():
        if pause_reason:
            conn.execute("INSERT INTO seo_spend_controls(provider,paused,reason,updated_at) VALUES('semrush',true,%s,now()) "
                         "ON CONFLICT(provider) DO UPDATE SET paused=true,reason=EXCLUDED.reason,updated_at=now()",
                         (pause_reason,))
        if resume:
            conn.execute("UPDATE seo_spend_controls SET paused=false,reason='resumed by operator',updated_at=now() "
                         "WHERE provider='semrush'")
    client = Semrush(conn, key="")
    control = conn.execute("SELECT paused, reason, updated_at FROM seo_spend_controls WHERE provider='semrush'").fetchone()
    return {"month_units_used": client.month_used(), "engine_caps": {"run": client.run_cap, "month": client.month_cap,
            "call": client.call_cap}, "paused": bool(control and control["paused"]),
            "reason": control["reason"] if control else "", "kill_switch": os.getenv("SEMRUSH_KILL_SWITCH", "") == "true"}


def audit_content(site_dir):
    """Quality-gate every existing page against the others (no database needed)."""
    pages = inventory.from_directory(site_dir)
    domains = {c["name"]: c["domain"] for c in research.load_config()["competitors"]}
    out = {}
    for p in pages:
        rel = p["url"].replace("https://usetradelink.com/", "") + "index.html"
        with open(os.path.join(site_dir, rel), encoding="utf-8") as fh:
            report = quality.check(fh.read(), rel, {q["url"]: q["text"] for q in pages if q["url"] != p["url"]},
                                   "update", competitor_domains=domains)
        out[p["url"]] = {"passed": report["passed"], "errors": report["errors"], "warnings": report["warnings"]}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="engine.run")
    parser.add_argument("command", nargs="?", default="cycle", choices=["cycle", "migrate", "import-snapshot", "report", "audit-content", "spend"])
    parser.add_argument("path", nargs="?")
    parser.add_argument("--site-dir")
    parser.add_argument("--pause", metavar="REASON", help="spend: pause all Semrush spending")
    parser.add_argument("--resume", action="store_true", help="spend: resume Semrush spending")
    args = parser.parse_args(argv)
    if args.command == "audit-content":
        print(json.dumps(audit_content(args.site_dir or "."), indent=1))
        return 0
    with connect() as conn:
        applied = migrate(conn)
        if args.command == "migrate":
            print(json.dumps({"applied": applied}))
            return 0
        if args.command == "import-snapshot":
            print(json.dumps(research.import_snapshot(conn, args.path)))
            return 0
        if args.command == "report":
            print(report(conn))
            return 0
        if args.command == "spend":
            print(json.dumps(spend(conn, pause_reason=args.pause, resume=args.resume), default=str))
            return 0
        run, status = cycle(conn, args.site_dir)
        print(json.dumps({"run_id": str(run.id), "status": status, "steps": run.steps, "errors": run.errors},
                         default=str))
        return 0 if status == "complete" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": exc.__class__.__name__}))
        sys.exit(1)
