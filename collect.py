#!/usr/bin/env python3
"""Ship-it Stats collector. Read-only over /home/box/agent-data and /workspace/tokenmax.

Writes ONLY data/stats.json inside this folder. Metadata only: counts, timestamps, tool/app names.
No prompts, message contents, commands or secrets are exported.
Reuses Bot Island's collector (bot-island/collect.py) and the Hub's project scanner (hub/scan.py),
imported without writing bytecode into those folders.
"""
import collections, datetime as dt, glob, json, os, sys, time
sys.dont_write_bytecode = True
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/workspace/tokenmax"
AGENTS = "/home/box/agent-data/agents"
TRANSCRIPTS = "/home/box/agent-data/agent-transcripts"
ET = ZoneInfo("America/New_York")
BUILD_DAY = dt.date(2026, 10, 7)
# Grok Bot weekly pool reset ~7:42 PM ET Thu Oct 8, 2026
WEEK_RESET = dt.datetime(2026, 10, 8, 19, 42, tzinfo=ET)
WEEK_END = dt.date(2026, 10, 15)  # next reset day (display label Oct 8–15)

sys.path.insert(0, os.path.join(ROOT, "bot-island"))
import collect as island  # noqa: E402
sys.path.pop(0)
sys.path.insert(0, os.path.join(ROOT, "hub"))
import scan as hub  # noqa: E402
sys.path.pop(0)

TOOL_TYPES = {"mcp_tool_call": "Connector (MCP) call", "shell_command": "Shell command",
              "browser_navigation": "Browser visit", "computer_use_session": "Computer-use session"}

SCRATCH = {"build", "work", "tmp", "pieces", "frames", "render", "cache", "clips"}

# Projects that shipped on Build Night Oct 7 (from the Oct 7 6:04 PM ET snapshot archived in TokenMax-Oct7-build.zip).
# File mtimes were bulk-rewritten by the Oct 8 ~8:11 PM ET unpack, so live mtimes can no longer recover Oct 7.
OCT7_ARCHIVE_NAMES = [
    "agent-playbook", "ai-ops-atlas", "bookmark-atlas", "bot-island", "bot-island-3d", "boyd-wall",
    "chelsea-matchday", "chelsea-time-machine", "chopsticks", "docket-pilot", "explainer-studio",
    "family-week", "fleet-doctor", "fleet-funnies", "fleet-yearbook", "fridge-board", "hspt-quest",
    "hub", "llm-box-advisor", "math-arcade", "mission-control", "orioles-2026", "pods-radio",
    "ravens-film-room", "recap", "season-book", "ship-it-stats", "slack-pulse", "sports-desk",
    "terps-preview", "times-archive", "trailer", "trip-kit", "trivia-night", "writing-studio",
]
OCT7_ARCHIVE_TOTALS = {
    "files": 3017, "loc": 42259, "screenshots": 306, "tests": 72, "codeFiles": 384,
    "bytes": 2294192203, "videos": 12, "projects": 35, "ready": 29, "newOct7": 35,
}


def et_day(ts):
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET)
    return t.date().isoformat(), t.hour


def streaks(days):
    """Longest and current run of consecutive ET days with activity."""
    if not days:
        return {"longest": 0, "longestEnd": None, "current": 0}
    ds = sorted(dt.date.fromisoformat(d) for d in days)
    best, run, best_end = 1, 1, ds[0]
    for a, b in zip(ds, ds[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        if run > best:
            best, best_end = run, b
    today = dt.datetime.now(ET).date()
    cur, d = 0, today
    s = set(ds)
    if d not in s:
        d = d - dt.timedelta(days=1)  # streak still alive if yesterday counted
    while d in s:
        cur += 1
        d -= dt.timedelta(days=1)
    return {"longest": best, "longestEnd": best_end.isoformat(), "current": cur}


def routine_runs():
    """Full run history from each bot's automations/*/runs.json (metadata only)."""
    out = []
    for runs_path in glob.glob(os.path.join(AGENTS, "*", "automations", "*", "runs.json")):
        aid = runs_path.split(os.sep)[-4]
        a = island.read_json(os.path.join(os.path.dirname(runs_path), "automation.json"), {}) or {}
        for r in island.read_json(runs_path, []) or []:
            s = r.get("startedAt")
            if not s:
                continue
            f = r.get("finishedAt")
            out.append({"bot": aid, "ts": island.iso(s), "routine": a.get("name") or "routine",
                        "status": r.get("status"), "trigger": r.get("trigger"),
                        "minutes": round((f - s) / 60000, 1) if f and f > s else None})
    return out


def subagent_transcripts():
    """Executor/sub-agent transcripts: tool-call counts per file, dated by mtime.
    Only tool *names* are read; no content is exported."""
    rows = []
    for p in glob.glob(os.path.join(TRANSCRIPTS, "sand-subagent-*", "*.jsonl")):
        calls, names = 0, collections.Counter()
        try:
            with open(p, errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    for c in ((d.get("message") or {}).get("content") or []):
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            calls += 1
                            names[c.get("name") or "?"] += 1
        except OSError:
            continue
        m = os.path.getmtime(p)
        t = dt.datetime.fromtimestamp(m, ET)
        rows.append({"day": t.date().isoformat(), "hour": t.hour, "toolCalls": calls, "tools": dict(names)})
    return rows


def has_dist_snapshot(proj_dir):
    dist = os.path.join(proj_dir, "dist")
    if not os.path.isdir(dist):
        return False
    for f in os.listdir(dist):
        if "snapshot" in f.lower() and f.endswith(".html"):
            return True
        if f.endswith(".html") and ("snapshot" in f.lower() or f.endswith("-offline.html") or "dist" in f.lower()):
            return True
    # also accept any self-contained offline html in dist/
    return any(f.endswith(".html") for f in os.listdir(dist))


def scan_projects():
    """Scan every /workspace/tokenmax project via hub.project + this-week mtime flags."""
    reset_ts = WEEK_RESET.timestamp()
    # "Hot" remasters: activity after the Oct 8 unpack window (~8:30 PM ET)
    hot_after = dt.datetime(2026, 10, 8, 20, 30, tzinfo=ET).timestamp()
    projs = []
    for pname in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, pname)
        if not os.path.isdir(d) or pname.startswith("."):
            continue
        # skip zip/archives at root level that aren't projects
        if pname.endswith(".zip") or pname.endswith(".md"):
            continue
        p = hub.project(pname, do_ping=False)
        if not p["counts"]["files"]:
            continue
        p["videos"] = [v for v in p["videos"] if not any(seg.lower() in SCRATCH for seg in v.split(os.sep)[:-1])]
        first = None
        last = None
        if p["firstMtime"]:
            first = dt.datetime.fromisoformat(p["firstMtime"])
        if p["lastMtime"]:
            last = dt.datetime.fromisoformat(p["lastMtime"])
        last_ts = last.timestamp() if last else 0
        touched_week = bool(last and last_ts >= reset_ts)
        hot = bool(last and last_ts >= hot_after and last.date() >= dt.date(2026, 10, 9))
        # also hot if brand-new this week (first file after unpack)
        if first and first.timestamp() >= hot_after:
            hot = True
            touched_week = True
        dist_ok = has_dist_snapshot(d) or bool(p.get("ready"))
        projs.append({
            "name": pname, "title": p["title"], "ready": p["ready"], "port": p["port"],
            "counts": p["counts"], "videos": len(p["videos"]), "cover": p["cover"],
            "firstMtime": p["firstMtime"], "lastMtime": p["lastMtime"],
            "touchedThisWeek": touched_week, "hot": hot, "hasDist": dist_ok,
            "inOct7Archive": pname in OCT7_ARCHIVE_NAMES,
        })
    return projs


def week_sparkline(day_tool_calls, day_actions, reset_date, end_date):
    """Daily series from reset day through today.
    Prefer scored fleet actions (tool+routine+files+msgs) because audit tool-call
    streams can go quiet while other event types still land in Bot Island's collector.
    """
    today = min(dt.datetime.now(ET).date(), end_date)
    start = reset_date
    out = []
    d = start
    while d <= today:
        key = d.isoformat()
        out.append({
            "day": key,
            "toolCalls": int(day_tool_calls.get(key, 0)),
            "actions": int(day_actions.get(key, 0)),
        })
        d += dt.timedelta(days=1)
    return out


def collect():
    t0 = time.time()
    fleet = island.collect()
    bots = {b["id"]: {"id": b["id"], "name": b["name"], "title": b["title"], "color": b["color"]} for b in fleet["bots"]}
    name = lambda aid: bots.get(aid, {}).get("name") or aid[:8]
    per = collections.defaultdict(lambda: collections.Counter())  # (bot, day) -> counter
    hours = collections.defaultdict(collections.Counter)  # bot -> hour histogram
    app_counts, tool_counts, pair_counts = collections.Counter(), collections.Counter(), collections.Counter()
    host_counts = collections.Counter()
    boyd_msgs = collections.Counter()
    day_tool_calls = collections.Counter()
    for e in fleet["events"]:
        ts, t = e.get("ts"), e.get("type")
        if not ts:
            continue
        day, hr = et_day(ts)
        b = e.get("bot")
        if t in TOOL_TYPES:
            per[(b, day)]["toolCalls"] += 1
            per[(b, day)][t] += 1
            hours[b][hr] += 1
            day_tool_calls[day] += 1
            app_counts[TOOL_TYPES[t] if t != "mcp_tool_call" else (e.get("app") or "Connector")] += 1
            if t == "mcp_tool_call":
                tool_counts[f'{e.get("app")} · {e.get("tool")}'] += 1
            if t == "browser_navigation" and e.get("host"):
                host_counts[e["host"]] += 1
        elif t == "file_write":
            per[(b, day)]["filesWritten"] += 1
        elif t == "agent_msg":
            src = e.get("from")
            per[(src, day)]["msgsSent"] += 1
            per[(b, day)]["msgsReceived"] += 1
            pair_counts[tuple(sorted([name(src), name(b)]))] += 1
        elif t == "boyd_msg":
            per[(b, day)]["boydMsgs"] += 1
            boyd_msgs[b] += 1
    runs = routine_runs()
    run_status = collections.Counter()
    for r in runs:
        day, hr = et_day(r["ts"])
        per[(r["bot"], day)]["routineRuns"] += 1
        run_status[r.get("status") or "unknown"] += 1
    METRICS = ["toolCalls", "routineRuns", "filesWritten", "msgsSent", "msgsReceived", "boydMsgs"]
    days = sorted({d for (_, d) in per})
    grid = []
    for (b, d), c in sorted(per.items(), key=lambda kv: (kv[0][1], name(kv[0][0]))):
        if b not in bots:
            continue
        grid.append({"bot": b, "day": d, **{m: c.get(m, 0) for m in METRICS},
                     "score": c.get("toolCalls", 0) + c.get("routineRuns", 0) + c.get("filesWritten", 0) + c.get("msgsSent", 0)})
    by_bot = {}
    for b in bots:
        rows = [g for g in grid if g["bot"] == b]
        active = [g["day"] for g in rows if g["score"] > 0]
        tot = {m: sum(g[m] for g in rows) for m in METRICS}
        best = max(rows, key=lambda g: g["score"], default=None)
        h = hours.get(b) or {}
        by_bot[b] = {**bots[b], "totals": tot, "activeDays": len(active), "streak": streaks(active),
                     "bestDay": {"day": best["day"], "score": best["score"]} if best and best["score"] else None,
                     "peakHourET": max(h, key=h.get) if h else None}
    day_tot = collections.Counter()
    for g in grid:
        day_tot[g["day"]] += g["score"]
    subs = subagent_transcripts()
    sub_by_day = collections.defaultdict(lambda: {"transcripts": 0, "toolCalls": 0})
    sub_tools = collections.Counter()
    for s in subs:
        sub_by_day[s["day"]]["transcripts"] += 1
        sub_by_day[s["day"]]["toolCalls"] += s["toolCalls"]
        sub_tools.update(s["tools"])

    projs = scan_projects()
    week = [p for p in projs if p["touchedThisWeek"]]
    hot = [p for p in week if p["hot"]]
    week_tot = {k: sum(p["counts"][k] for p in week) for k in ("files", "loc", "screenshots", "tests", "codeFiles", "bytes")}
    week_tot["videos"] = sum(p["videos"] for p in week)
    week_tot["projects"] = len(week)
    week_tot["ready"] = sum(1 for p in week if p["ready"])
    week_tot["hasDist"] = sum(1 for p in week if p["hasDist"])
    week_tot["hot"] = len(hot)
    hot_tot = {k: sum(p["counts"][k] for p in hot) for k in ("files", "loc", "screenshots", "tests", "codeFiles", "bytes")}
    hot_tot["videos"] = sum(p["videos"] for p in hot)
    hot_tot["projects"] = len(hot)
    hot_tot["ready"] = sum(1 for p in hot if p["ready"])
    hot_tot["hasDist"] = sum(1 for p in hot if p["hasDist"])

    # Oct 7 archive strip: live disk stats for the 35 projects that shipped that night
    archive_live = [p for p in projs if p["inOct7Archive"]]
    archive_live_tot = {k: sum(p["counts"][k] for p in archive_live) for k in ("files", "loc", "screenshots", "tests", "codeFiles", "bytes")}
    archive_live_tot["videos"] = sum(p["videos"] for p in archive_live)
    archive_live_tot["projects"] = len(archive_live)
    archive_live_tot["ready"] = sum(1 for p in archive_live if p["ready"])
    archive_live_tot["hasDist"] = sum(1 for p in archive_live if p["hasDist"])

    usage = island.read_json(os.path.join(ROOT, "usage.json"), None)
    best_day = max(day_tot, key=day_tot.get) if day_tot else None
    best_botday = max(grid, key=lambda g: g["score"], default=None)
    longest = max(by_bot.values(), key=lambda b: b["streak"]["longest"])
    current_streak_bot = max(by_bot.values(), key=lambda b: (b["streak"]["current"], b["streak"]["longest"]))
    pair = pair_counts.most_common(1)
    totals = {m: sum(g[m] for g in grid) for m in METRICS}
    totals["subagentToolCalls"] = sum(s["toolCalls"] for s in subs)
    totals["subagentTranscripts"] = len(subs)

    spark = week_sparkline(day_tool_calls, day_tot, WEEK_RESET.date(), WEEK_END)
    week_tool_calls = sum(x["toolCalls"] for x in spark)
    week_actions = sum(x["actions"] for x in spark)

    highlights = []
    if best_day:
        highlights.append({"icon": "🔥", "title": "Busiest fleet day", "value": f"{day_tot[best_day]:,} actions",
                           "detail": best_day})
    if best_botday:
        highlights.append({"icon": "🏆", "title": "Biggest single bot-day", "value": f"{best_botday['score']:,} actions",
                           "detail": f"{name(best_botday['bot'])} on {best_botday['day']}"})
    highlights.append({"icon": "📆", "title": "Longest daily streak", "value": f"{longest['streak']['longest']} days",
                       "detail": f"{longest['name']} (ended {longest['streak']['longestEnd']})"})
    if current_streak_bot["streak"]["current"]:
        highlights.append({"icon": "⚡", "title": "Current streak leader",
                           "value": f"{current_streak_bot['streak']['current']} days",
                           "detail": current_streak_bot["name"]})
    if pair:
        highlights.append({"icon": "✉️", "title": "Chattiest bot pair", "value": f"{pair[0][1]} messages",
                           "detail": " ↔ ".join(pair[0][0])})
    if tool_counts:
        tc = tool_counts.most_common(1)[0]
        highlights.append({"icon": "🧰", "title": "Most-called connector tool", "value": f"{tc[1]:,} calls", "detail": tc[0]})
    if host_counts:
        hc = host_counts.most_common(1)[0]
        highlights.append({"icon": "🌐", "title": "Most-visited site", "value": f"{hc[1]:,} visits", "detail": hc[0]})
    highlights.append({"icon": "🚀", "title": "This Week (since Thu reset)",
                       "value": f"{week_tot['projects']} projects on disk",
                       "detail": f"{week_tot['hot']} remastered/built since Fri · {week_tot['loc']:,} LOC · {week_tot['hasDist']} with dist"})
    highlights.append({"icon": "🛠️", "title": "Build Night Oct 7 (archive)",
                       "value": f"{OCT7_ARCHIVE_TOTALS['projects']} projects",
                       "detail": f"{OCT7_ARCHIVE_TOTALS['loc']:,} LOC · {OCT7_ARCHIVE_TOTALS['screenshots']:,} shots · snapshot 6:04 PM ET"})
    highlights.append({"icon": "🤖", "title": "Executor sub-agent tool calls (saved transcripts)",
                       "value": f"{totals['subagentToolCalls']:,}",
                       "detail": f"across {totals['subagentTranscripts']} transcripts; per-day split not reliable (see notes)"})

    no_audit = []
    for bid, bmeta in bots.items():
        if not os.path.exists(os.path.join(AGENTS, bid, "audit.jsonl")):
            no_audit.append(bmeta["name"])

    return {
        "generatedAt": dt.datetime.now(ET).isoformat(timespec="seconds"),
        "range": {"first": days[0] if days else None, "last": days[-1] if days else None, "days": len(days)},
        "week": {
            "label": "This Week (Oct 8–15)",
            "resetAt": WEEK_RESET.isoformat(timespec="minutes"),
            "resetNote": "Grok Bot weekly pool reset ~7:42 PM ET Thu Oct 8, 2026",
            "endDate": WEEK_END.isoformat(),
            "totals": week_tot,
            "hotTotals": hot_tot,
            "projects": week,
            "sparkline": spark,
            "toolCallsThisWeek": week_tool_calls,
            "actionsThisWeek": week_actions,
            "usage": usage,
        },
        "buildNightArchive": {
            "date": BUILD_DAY.isoformat(),
            "label": "Build Night · Wed Oct 7 (archive)",
            "snapshotAt": "2026-10-07T18:04:22-04:00",
            "source": "TokenMax-Oct7-build.zip ship-it-stats snapshot (mtimes later rewritten by Oct 8 unpack)",
            "totals": OCT7_ARCHIVE_TOTALS,
            "liveTotals": archive_live_tot,
            "names": OCT7_ARCHIVE_NAMES,
            "projects": archive_live,
        },
        # keep buildNight key for backward compat with older UI / tests that expect it
        "buildNight": {
            "date": BUILD_DAY.isoformat(),
            "totals": OCT7_ARCHIVE_TOTALS,
            "projects": projs,
            "usage": usage,
            "note": "Superseded by week + buildNightArchive; totals are the frozen Oct 7 snapshot.",
        },
        "metrics": METRICS, "bots": by_bot, "grid": grid,
        "dayTotals": dict(sorted(day_tot.items())),
        "dayToolCalls": dict(sorted(day_tool_calls.items())),
        "totals": totals, "routineStatus": dict(run_status),
        "topApps": app_counts.most_common(12), "topTools": tool_counts.most_common(12),
        "topHosts": host_counts.most_common(10),
        "topPairs": [[" ↔ ".join(k), v] for k, v in pair_counts.most_common(8)],
        "subagents": {"byDay": dict(sorted(sub_by_day.items())), "topTools": sub_tools.most_common(12)},
        "highlights": highlights,
        "streakLeaders": {
            "longest": {"name": longest["name"], "days": longest["streak"]["longest"],
                        "ended": longest["streak"]["longestEnd"], "color": longest["color"]},
            "current": {"name": current_streak_bot["name"], "days": current_streak_bot["streak"]["current"],
                        "color": current_streak_bot["color"]},
        },
        "notes": {
            "toolCalls": "Connector (MCP) calls + shell commands + browser visits + computer-use sessions from each bot's audit.jsonl.",
            "filesWritten": "Output-file writes Bot Island attributes to a bot by folder pattern, de-duplicated to one per minute, last 30 days only. Bots without mapped folders show 0, which means not measured, not zero.",
            "routineRuns": "Every entry in automations/*/runs.json.",
            "messages": "Bot-to-bot message metadata (who and when) from each bot's transcript store. No contents.",
            "subagents": "Executor sub-agent transcripts carry no per-line timestamps and no parent-bot field, so they are not credited to a bot. Shared restore mtimes make per-day splits unreliable.",
            "noAudit": ("No audit.jsonl on disk for: " + (", ".join(no_audit) if no_audit else "none") +
                        ". A 0 for those bots means not measured, not zero activity."),
            "thisWeek": ("Projects whose newest file mtime is on/after the Thu Oct 8 ~7:42 PM ET weekly reset. "
                         "'Hot' = remastered or newly built on/after Fri Oct 9 (after the Oct 8 ~8:11 PM unpack). "
                         "Many folders share an Oct 8 8:11 PM ET mtime from that unpack — they count as This Week on-disk "
                         "but are not marked hot. Counts use hub/scan.py rules."),
            "buildNightArchive": ("Frozen Oct 7 6:04 PM ET totals from the TokenMax-Oct7-build.zip snapshot. "
                                  "Live mtimes cannot recover Oct 7 after the Oct 8 unpack rewrote them. "
                                  "liveTotals = current disk stats for those same 35 project names."),
            "auditGap": "The latest mcp/shell/browser/computer-use event in any audit.jsonl is 2026-09-15. This week's sparkline therefore uses scored fleet actions (tool calls + routine runs + files written + bot msgs) from Bot Island events, which still include Oct 8–10 activity even when the raw tool-call stream is quiet.",
            "days": "Days are Eastern Time.",
        },
        "collectSeconds": round(time.time() - t0, 2),
    }


def main():
    data = collect()
    out = os.path.join(HERE, "data", "stats.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".tmp", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(out + ".tmp", out)
    t = data["totals"]
    w = data["week"]["totals"]
    print(f"wrote {out}: {len(data['bots'])} bots, {data['range']['days']} days, "
          f"tool calls {t['toolCalls']}, routine runs {t['routineRuns']}, msgs {t['msgsSent']}, "
          f"this week {w['projects']} projects ({w['hot']} hot) · {w['loc']} loc in {data['collectSeconds']}s")


if __name__ == "__main__":
    main()
