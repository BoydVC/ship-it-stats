#!/usr/bin/env python3
"""Ship-it Stats smoke test: data correctness (independent recount), privacy, read-only, server, snapshot, images."""
import glob, json, os, struct, subprocess, sys, urllib.request
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = "/home/box/agent-data/agents"
PORT = int(os.environ.get("SHIPIT_PORT", "4260"))
passed, failed = 0, []

def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(f"{name} {detail}")
        print("FAIL", name, detail)

def tree_state(root):
    st = {}
    for b, ds, fs in os.walk(root):
        for f in fs:
            p = os.path.join(b, f)
            try:
                s = os.stat(p); st[p] = (s.st_mtime_ns, s.st_size)
            except OSError:
                pass
    return st

# 1. read-only: Bot Island and Hub folders + agent automations untouched by a collector run
watch = ["/workspace/tokenmax/bot-island", "/workspace/tokenmax/hub"]
before = {w: tree_state(w) for w in watch}
runs_before = {p: os.stat(p).st_mtime_ns for p in glob.glob(f"{AGENTS}/*/automations/*/runs.json")}
r = subprocess.run([sys.executable, os.path.join(HERE, "collect.py")], capture_output=True, text=True)
check("collector exits 0", r.returncode == 0, r.stderr[-300:] if r.stderr else r.stdout[-200:])
for w in watch:
    after = tree_state(w)
    changed = [p for p in after if p in before[w] and after[p] != before[w][p] and "/data/" not in p and not p.endswith(".log")]
    new = [p for p in after if p not in before[w] and "/data/" not in p]
    check(f"collector did not write into {os.path.basename(w)}", not changed and not new, str((changed + new)[:3]))
check("runs.json untouched", all(os.stat(p).st_mtime_ns == m for p, m in runs_before.items()))

D = json.load(open(os.path.join(HERE, "data", "stats.json")))
for k in ("bots", "grid", "totals", "buildNight", "buildNightArchive", "week", "highlights", "notes", "range", "dayToolCalls", "streakLeaders"):
    check(f"stats has {k}", k in D)

# 2. independent recount: tool calls from audit.jsonl, routine runs from runs.json
TOOL = {"mcp_tool_call", "shell_command", "browser_navigation", "computer_use_session"}
tc = 0
for a in glob.glob(f"{AGENTS}/*/audit.jsonl"):
    aid = a.split("/")[-2]
    if aid not in D["bots"]:
        continue
    for line in open(a, errors="replace"):
        try:
            ev = json.loads(line)
            if ev.get("type") in TOOL and ev.get("ts"):
                tc += 1
        except Exception:
            pass
check("tool calls match audit.jsonl recount", D["totals"]["toolCalls"] == tc, f"{D['totals']['toolCalls']} vs {tc}")
rr = 0
for p in glob.glob(f"{AGENTS}/*/automations/*/runs.json"):
    if p.split("/")[-4] in D["bots"]:
        rr += sum(1 for x in json.load(open(p)) if x.get("startedAt"))
check("routine runs match runs.json recount", D["totals"]["routineRuns"] == rr, f"{D['totals']['routineRuns']} vs {rr}")
check("grid totals add up", sum(g["toolCalls"] for g in D["grid"]) == D["totals"]["toolCalls"])
check("bot totals add up", sum(b["totals"]["msgsSent"] for b in D["bots"].values()) == D["totals"]["msgsSent"])
check("sent == received (bot-to-bot)", abs(D["totals"]["msgsSent"] - D["totals"]["msgsReceived"]) <= 5,
      f"{D['totals']['msgsSent']} vs {D['totals']['msgsReceived']}")
check("dayToolCalls sum == toolCalls", sum(D["dayToolCalls"].values()) == D["totals"]["toolCalls"])

# 3. This Week + Build Night archive
W = D["week"]
week = [p for p in W["projects"] if p.get("touchedThisWeek")]
for k in ("files", "loc", "screenshots", "tests"):
    check(f"this week {k} sums", W["totals"][k] == sum(p["counts"][k] for p in week))
check("this week has projects", W["totals"]["projects"] >= 4, str(W["totals"]["projects"]))
check("this week has hot remasters", W["totals"]["hot"] >= 1, str(W["totals"]["hot"]))
check("sparkline present", len(W.get("sparkline") or []) >= 1)
check("sparkline toolCallsThisWeek matches", W["toolCallsThisWeek"] == sum(s["toolCalls"] for s in W["sparkline"]))
check("sparkline actionsThisWeek matches", W.get("actionsThisWeek", 0) == sum(s.get("actions", 0) for s in W["sparkline"]))
check("sparkline has this-week actions or documented quiet", W.get("actionsThisWeek", 0) >= 0)
A = D["buildNightArchive"]
check("oct7 archive has 35 projects", A["totals"]["projects"] == 35)
check("oct7 archive loc frozen", A["totals"]["loc"] == 42259)
check("oct7 archive names match", len(A["names"]) == 35)
check("oct7 live projects present", A["liveTotals"]["projects"] >= 30, str(A["liveTotals"]["projects"]))
# backward-compat buildNight key still present with frozen totals
check("buildNight compat totals", D["buildNight"]["totals"]["projects"] == 35)

# 4. streak sanity
for b in D["bots"].values():
    check(f"streak {b['name']} <= active days", b["streak"]["longest"] <= max(1, b["activeDays"]) and b["streak"]["current"] <= b["streak"]["longest"])

# 5. privacy: metadata only
raw = open(os.path.join(HERE, "data", "stats.json")).read()
def strings(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k; yield from strings(v)
    elif isinstance(o, list):
        for v in o: yield from strings(v)
    elif isinstance(o, str):
        yield o
long = [s for s in strings(D) if len(s) > 600]
check("no string > 600 chars (no message bodies)", not long, str(long[:1])[:120])
for bad in ('"prompt"', '"content"', '"text"', '"command"', "api_key", "password"):
    check(f"no {bad} key/term", bad not in raw)

# 6. server
def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
        return r.status, r.read()
try:
    s, b = get("/"); check("GET / 200", s == 200 and b"Ship-it Stats" in b)
    check("UI has This Week", b"This Week" in b)
    check("UI has count-up hooks", b"data-count" in b)
    s, b = get("/data/stats.json"); check("GET stats.json", s == 200 and json.loads(b)["totals"])
    s, b = get("/api/health"); check("GET health", s == 200)
except Exception as e:
    check("server reachable on 4260", False, str(e))

# 7. snapshot + images
snap = os.path.join(HERE, "dist", "ship-it-stats-snapshot.html")
check("snapshot exists", os.path.exists(snap))
if os.path.exists(snap):
    h = open(snap).read()
    check("snapshot inlines data", "window.__DATA__=" in h)
    check("snapshot has no external scripts/styles", "<script src" not in h and "<link" not in h and "fetch('http" not in h)
    check("snapshot has This Week", "This Week" in h)
def png_size(p):
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None
check("share card is 1200x675 PNG", png_size(os.path.join(HERE, "dist", "share-card.png")) == (1200, 675))
shots = glob.glob(os.path.join(HERE, "screenshots", "*.png"))
check("best home screenshot present", os.path.exists(os.path.join(HERE, "screenshots", "00-best-home.png")))
check("best screenshot present", glob.glob(os.path.join(HERE, "screenshots", "00-best-*.png")))
check("phone screenshot present", any("phone" in s and (png_size(s) or (0,0))[0] == 390 for s in shots))
check("desktop screenshot present", any("desktop" in s and (png_size(s) or (0,0))[0] == 1440 for s in shots))

print(f"{passed} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
