#!/usr/bin/env python3
"""Fantasy Football HQ - local server.

Serves the web app and proxies the public data sources it combines:
  * ESPN Fantasy (your league, rosters, ESPN projections, ESPN analyst ranks)
  * Sleeper (independent weekly projections, trending adds)
  * CBS Sports (weekly projected stat lines)
  * FantasyPros expert consensus rankings + projections, via the open
    DynastyProcess data set (github.com/dynastyprocess/data, refreshed daily)
  * Draft Sharks (weekly and rest-of-season rankings, top 25 per position)
  * FantasyCalc (trade values built from real fantasy trades)

Run:  python3 server.py   then open http://localhost:8765
No third-party packages needed.
"""
import csv
import io
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8765"))
# hosting services (Render, Railway, ...) set PORT and need the server reachable from outside
HOST = os.environ.get("HOST", "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
HERE = os.path.dirname(os.path.abspath(__file__))
ESPN = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0"
SLEEPER = "https://api.sleeper.app"
CACHE_SECONDS = 600
_cache = {}

# python.org builds of Python on macOS ship without CA certificates; fall back to the system bundle.
_SSL = ssl.create_default_context()
if not _SSL.get_ca_certs() and os.path.exists("/etc/ssl/cert.pem"):
    _SSL = ssl.create_default_context(cafile="/etc/ssl/cert.pem")


def fetch(url, headers=None, ttl=CACHE_SECONDS):
    key = url + json.dumps(headers or {}, sort_keys=True)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "FantasyHQ/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=45, context=_SSL) as r:
        body = r.read()
    _cache[key] = (time.time(), body)
    return body


DP_DATA = "https://github.com/dynastyprocess/data/raw/master/files"
CBS = "https://www.cbssports.com/fantasy/football/stats/{pos}/{season}/{week}/projections/ppr/"


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def fantasypros():
    """Weekly + rest-of-season FantasyPros expert consensus (ECR)."""
    weekly = []
    for r in csv.DictReader(io.StringIO(fetch(f"{DP_DATA}/fp_latest_weekly.csv").decode("utf-8", "replace"))):
        pos = {"dst": "D/ST", "k": "K", "qb": "QB"}.get(r["page"], r["page"].replace("ppr-", "").upper())
        if pos not in ("QB", "RB", "WR", "TE", "K", "D/ST"):
            continue
        weekly.append({"name": r["player_name"], "pos": pos, "team": r["team"], "ecr": num(r["ecr"]),
                       "posRank": r["pos_rank"], "pts": num(r["r2p_pts"]), "grade": r["start_sit_grade"],
                       "sd": num(r["sd"]), "date": r["scrape_date"]})
    ros = []
    for r in csv.DictReader(io.StringIO(fetch(f"{DP_DATA}/db_fpecr_latest.csv").decode("utf-8", "replace"))):
        if r["ecr_type"] != "rp":
            continue
        pos = {"DST": "D/ST"}.get(r["pos"], r["pos"])
        if pos in ("QB", "RB", "WR", "TE", "K", "D/ST"):
            ros.append({"name": r["player"], "pos": pos, "team": r["team"], "ecr": num(r["ecr"])})
    return {"weekly": weekly, "ros": ros}


def cbs(season, week):
    """CBS weekly projected stat lines (points are recomputed in the app with league scoring)."""
    out = []
    for pos in ("QB", "RB", "WR", "TE", "K"):
        try:
            h = fetch(CBS.format(pos=pos, season=season, week=week)).decode("utf-8", "replace")
        except Exception:
            continue
        cols = re.findall(r"sortcol=([a-z_]+)", h)
        body = h[h.find("<tbody"):h.find("</tbody>")]
        for row in re.findall(r"<tr.*?</tr>", body, re.S):
            m = re.search(r'CellPlayerName--long.*?<a[^>]*>([^<]+)</a>.*?CellPlayerName-team">\s*([A-Z]+)', row, re.S)
            if not m:
                continue
            cells = [num(c.strip()) for c in re.findall(r'TableBase-bodyTd--number[^>]*>([^<]*)<', row)]
            stats = {}
            for k, v in zip(cols[1:], cells):
                stats.setdefault(k, v)
            out.append({"name": m.group(1).strip(), "team": m.group(2), "pos": pos, "stats": stats})
    return out


NFL_TEAMS = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WSH",
}
DS_POS = {"qb": "QB", "rb": "RB", "wr": "WR", "te": "TE", "k": "K", "def": "D/ST"}


def draftsharks(fmt):
    """Draft Sharks weekly + rest-of-season rankings: the top 25 at each position that they publish openly."""
    fmt = "ppr" if fmt == "ppr" else "half-ppr"  # Draft Sharks has no standard-scoring pages

    def page(kind, slug):
        out = []
        try:
            h = fetch(f"https://www.draftsharks.com/{kind}/{fmt}/{slug}").decode("utf-8", "replace")
        except Exception:
            return out
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
            for node in nodes:
                if not isinstance(node, dict) or node.get("@type") != "ItemList":
                    continue
                for el in node.get("itemListElement", []):
                    item = el.get("item") or {}
                    name = item.get("name", "")
                    team = name if slug == "def" else (item.get("affiliation") or {}).get("name", "")
                    out.append({"name": name, "pos": DS_POS[slug], "team": NFL_TEAMS.get(team, ""), "rank": el.get("position")})
        return out

    jobs = [(kind, slug) for kind in ("weekly-rankings", "ros-rankings") for slug in DS_POS]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda j: page(*j), jobs))
    out = {"weekly": [], "ros": []}
    for (kind, _), rows in zip(jobs, results):
        out["weekly" if kind == "weekly-rankings" else "ros"].extend(rows)
    return out


FANTASY_POS = {"QB", "RB", "WR", "TE", "K", "PK", "FB"}


def injuries():
    """Injury reports from ESPN, CBS Sports and Sleeper, each kept separate so the app can cross-check."""
    out = {"espn": [], "cbs": [], "sleeper": []}
    try:
        data = json.loads(fetch("https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"))
        for team in data.get("injuries", []):
            for i in team.get("injuries", []):
                a = i.get("athlete", {})
                pos = (a.get("position") or {}).get("abbreviation", "")
                if pos not in FANTASY_POS:
                    continue
                det = i.get("details") or {}
                out["espn"].append({"name": a.get("displayName"), "pos": "K" if pos == "PK" else pos,
                                    "team": (a.get("team") or {}).get("abbreviation", ""),
                                    "status": i.get("status"), "date": i.get("date"), "note": i.get("shortComment"),
                                    "injury": det.get("type"), "returnDate": det.get("returnDate")})
    except Exception:
        pass
    try:
        h = fetch("https://www.cbssports.com/nfl/injuries/").decode("utf-8", "replace")
        for block in re.split(r'TableBase-title', h)[1:]:
            tm = re.search(r'/nfl/teams/([A-Z]+)/', block)
            for row in re.findall(r'<tr class="TableBase-bodyTr">.*?</tr>', block, re.S):
                name = re.search(r'CellPlayerName--long.*?<a[^>]*>([^<]+)</a>', row, re.S)
                cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
                if not name or len(cells) < 5 or cells[1] not in FANTASY_POS:
                    continue
                ret = re.search(r"Expected Return - Week (\d+)", cells[4])
                out["cbs"].append({"name": name.group(1).strip(), "pos": cells[1], "team": tm.group(1) if tm else "",
                                   "updated": cells[2], "injury": cells[3], "status": cells[4],
                                   "returnWeek": int(ret.group(1)) if ret else None})
    except Exception:
        pass
    try:
        # Sleeper asks that the full player dump be pulled at most daily-ish; cache it for 6 hours
        players = json.loads(fetch(f"{SLEEPER}/v1/players/nfl", ttl=6 * 3600))
        for pid, p in players.items():
            if p.get("injury_status") and p.get("position") in FANTASY_POS and p.get("team"):
                out["sleeper"].append({"name": p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}",
                                       "pos": p.get("position"), "team": p.get("team"), "status": p.get("injury_status"),
                                       "injury": p.get("injury_body_part"), "practice": p.get("practice_participation"),
                                       "notes": p.get("injury_notes")})
    except Exception:
        pass
    return out


def espn_headers(handler):
    s2 = handler.headers.get("X-ESPN-S2", "").strip()
    swid = handler.headers.get("X-ESPN-SWID", "").strip()
    if s2 and swid:
        if not swid.startswith("{"):
            swid = "{" + swid.strip("{}") + "}"
        return {"Cookie": f"espn_s2={s2}; SWID={swid}"}
    return {}


def player_filter(week, limit=700):
    weeks = [str(w) for w in range(week, 18)]
    return json.dumps({"players": {
        "limit": limit,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        "filterSlotIds": {"value": [0, 2, 4, 6, 17, 16]},
        "filterStatsForTopScoringPeriodIds": {
            "value": 5,
            "additionalValue": ["002026", "102026"] + [f"112026{w}" for w in weeks],
        },
        "filterRanksForScoringPeriodIds": {"value": [week]},
        "filterRanksForRankTypes": {"value": ["PPR", "STANDARD"]},
    }})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, body):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/state":
                self.send_json(200, fetch(f"{SLEEPER}/v1/state/nfl"))
            elif u.path == "/api/league":
                season = int(q.get("season", 2026))
                lid = urllib.parse.quote(q["leagueId"])
                views = "&".join(f"view={v}" for v in
                                 ["mTeam", "mRoster", "mSettings", "mMatchup", "mStatus"])
                url = f"{ESPN.format(season=season)}/leagues/{lid}?{views}"
                self.send_json(200, fetch(url, espn_headers(self)))
            elif u.path == "/api/players":
                season = int(q.get("season", 2026))
                week = int(q.get("week", 1))
                lid = q.get("leagueId")
                base = ESPN.format(season=season)
                path = f"leagues/{urllib.parse.quote(lid)}" if lid else "leaguedefaults/3"
                url = f"{base}/{path}?view=kona_player_info&scoringPeriodId={week}"
                hdrs = {"X-Fantasy-Filter": player_filter(week), **(espn_headers(self) if lid else {})}
                self.send_json(200, fetch(url, hdrs))
            elif u.path == "/api/sleeper":
                season = int(q.get("season", 2026))
                week = int(q.get("week", 1))
                pos = "&".join(f"position[]={p}" for p in ["QB", "RB", "WR", "TE", "K", "DEF"])
                url = f"{SLEEPER}/projections/nfl/{season}/{week}?season_type=regular&{pos}"
                self.send_json(200, fetch(url))
            elif u.path == "/api/trending":
                self.send_json(200, fetch(f"{SLEEPER}/v1/players/nfl/trending/add?lookback_hours=48&limit=200"))
            elif u.path == "/api/injuries":
                self.send_json(200, injuries())
            elif u.path == "/api/draftsharks":
                self.send_json(200, draftsharks(q.get("fmt", "ppr")))
            elif u.path == "/api/fantasypros":
                self.send_json(200, fantasypros())
            elif u.path == "/api/cbs":
                self.send_json(200, cbs(int(q.get("season", 2026)), int(q.get("week", 1))))
            elif u.path == "/api/tradevalues":
                params = urllib.parse.urlencode({"isDynasty": "false", "numQbs": q.get("qbs", "1"),
                                                 "numTeams": q.get("teams", "10"), "ppr": q.get("ppr", "1")})
                self.send_json(200, fetch(f"https://api.fantasycalc.com/values/current?{params}"))
            else:
                self.send_json(404, {"error": "not found"})
        except urllib.error.HTTPError as e:
            msg = {401: "ESPN says this league is private. Add your espn_s2 and SWID cookies in Settings.",
                   404: "League not found. Double-check the league ID and season."}.get(e.code, f"Upstream error {e.code}")
            self.send_json(e.code, {"error": msg})
        except Exception as e:  # network trouble, bad params
            self.send_json(500, {"error": str(e)})


if __name__ == "__main__":
    print(f"Fantasy HQ running at http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
