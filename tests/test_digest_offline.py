"""
Offline smoke-test for the daily digest, both modes.
Mocks all HTTP and Claude API calls with canned responses — no network,
no credentials needed.
Run from repo root: PYTHONPATH=. python tests/test_digest_offline.py
"""
import json
import os
import re
import sys
from datetime import date, timedelta

import tempfile

_TMP = tempfile.mkdtemp(prefix="digest-test-")
os.environ["DIGEST_DRY_RUN"] = "1"
os.environ.setdefault("MY_TEAM_ABBR", "WAR")
os.environ.setdefault("COMBINED_PLAYERS_PATH", "data/combined_players.json")
# Keep test artifacts out of the real git-tracked snapshot dirs
os.environ["SNAPSHOT_DIR"] = os.path.join(_TMP, "snapshots")
os.environ["STATCAST_SNAPSHOT_DIR"] = os.path.join(_TMP, "statcast_snapshots")
os.environ["DIGEST_PREVIEW_PATH"] = os.path.join(_TMP, "digest_preview.html")
PREVIEW_PATH = os.environ["DIGEST_PREVIEW_PATH"]
os.environ.pop("DISCORD_BOT_TOKEN", None)
os.environ.pop("ANTHROPIC_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

import unittest.mock as mock
import requests

TODAY    = date.today()
TOMORROW = TODAY + timedelta(days=1)

# Real WAR pitchers/hitters from combined_players.json used in fixtures
DUSTIN_MAY_ID   = 669160   # WAR SP, STL
TAYLOR_WARD_ID  = 621493   # WAR OF, BAL
STREAMER_ID     = 999001   # unrostered SP
HOT_FA_ID       = 999002   # unrostered hitter


def _game(home_abbr, home_id, away_abbr, away_id,
          home_pitcher=None, away_pitcher=None, iso=None):
    def side(abbr, tid, pitcher):
        d = {"team": {"id": tid, "abbreviation": abbr, "name": abbr}}
        if pitcher:
            d["probablePitcher"] = {"id": pitcher[0], "fullName": pitcher[1]}
        return d
    return {
        "gameDate": f"{iso or TODAY.isoformat()}T17:10:00Z",
        "teams": {
            "home": side(home_abbr, home_id, home_pitcher),
            "away": side(away_abbr, away_id, away_pitcher),
        },
    }


def _schedule_response(url):
    m = re.search(r"date=(\d{4}-\d{2}-\d{2})", url)
    day = m.group(1) if m else TODAY.isoformat()
    games = []
    if day == TODAY.isoformat():
        # Taylor Ward's BAL plays today; opposing probable has a soft ERA
        games.append(_game("BAL", 110, "CWS", 145,
                           home_pitcher=(555001, "Some Oriole"),
                           away_pitcher=(555002, "Soft Tosser"), iso=day))
    if day == TOMORROW.isoformat():
        # Dustin May starts tomorrow vs CWS; a streamer faces PIT
        games.append(_game("STL", 138, "CWS", 145,
                           home_pitcher=(DUSTIN_MAY_ID, "Dustin May"),
                           away_pitcher=(555003, "Sox Starter"), iso=day))
        games.append(_game("MIA", 146, "PIT", 134,
                           home_pitcher=(STREAMER_ID, "Streamy McStream"),
                           away_pitcher=(555004, "Bucs Arm"), iso=day))
    return {"dates": [{"date": day, "games": games}]}


def _team_stats_response():
    splits = []
    for i, (tid, abbr) in enumerate(sorted(
        {108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
         113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
         118: "KC",  119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
         134: "PIT", 135: "SD",  136: "SEA", 137: "SF",  138: "STL",
         139: "TB",  140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
         144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL"}.items()
    )):
        splits.append({
            "team": {"id": tid, "abbreviation": abbr, "name": abbr},
            "stat": {
                "gamesPlayed": 14,
                "runs": 40 + i * 2,          # spread so ranks differ
                "strikeOuts": 140 - i * 2,
                "plateAppearances": 560,
                "ops": f"{0.620 + i * 0.005:.3f}",
            },
        })
    return {"stats": [{"splits": splits}]}


def _pitching_season_response(era, whip="1.10", k9="9.1"):
    return {"stats": [{"splits": [{"stat": {
        "era": era, "whip": whip, "strikeoutsPer9Inn": k9,
    }}]}]}


def _leaders_response():
    splits = [{
        "player":   {"id": HOT_FA_ID, "fullName": "Hot Waiver Bat"},
        "team":     {"abbreviation": "COL"},
        "position": {"abbreviation": "OF"},
        "stat": {"plateAppearances": 28, "avg": ".393", "ops": "1.214",
                 "homeRuns": 4, "rbi": 11, "stolenBases": 2},
    }, {
        # Rostered player (Taylor Ward) — must be filtered out
        "player":   {"id": TAYLOR_WARD_ID, "fullName": "Taylor Ward"},
        "team":     {"abbreviation": "BAL"},
        "position": {"abbreviation": "OF"},
        "stat": {"plateAppearances": 30, "avg": ".350", "ops": "1.100",
                 "homeRuns": 3, "rbi": 9, "stolenBases": 0},
    }]
    return {"stats": [{"splits": splits}]}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)[:200]

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def fake_get(url, *args, **kwargs):
    if "/schedule" in url:
        return FakeResponse(_schedule_response(url))
    if "/teams/stats" in url:
        return FakeResponse(_team_stats_response())
    if "/stats?stats=byDateRange&group=hitting&sportIds=1" in url:
        return FakeResponse(_leaders_response())
    if f"/people/{STREAMER_ID}/stats" in url:
        return FakeResponse(_pitching_season_response("3.10"))
    if "/stats?stats=season&group=pitching" in url or \
       re.search(r"/people/\d+/stats\?stats=season&group=pitching", url):
        return FakeResponse(_pitching_season_response("4.80"))
    if re.search(r"/people/\d+\?hydrate=rosterEntries", url):
        return FakeResponse({"people": [{"active": False}]})
    if re.search(r"/people/\d+/stats", url):
        return FakeResponse({"stats": []})
    if "discord.com" in url:
        return FakeResponse({}, status=403)
    return FakeResponse({}, status=404)


SAMPLE_BRIEF = """MY ROSTER

Dustin May finished the season healthy and the feed notes his cutter usage jumped in September, a keep signal for 2027.

MY PROSPECTS

No prospect news today.

TRANSACTIONS

Example Closer signed with Colorado, which moves his 2027 value down sharply.

PROSPECT PIPELINE

Top Pipeline Bat homered twice in the Arizona Fall League opener.

2027 OUTLOOK

@enosarris flagged Breakout Arm as a 2027 sleeper after a velocity gain."""


def run_inseason_checks(failures: list):
    os.environ["DIGEST_MODE"] = "inseason"
    with mock.patch.object(requests, "get", side_effect=fake_get), \
         mock.patch.object(requests, "post",
                           side_effect=RuntimeError("network disabled in test")):
        from src.daily_digest import run
        run()

    if not os.path.exists(PREVIEW_PATH):
        failures.append("inseason: digest_preview.html was not written")
        return
    html = open(PREVIEW_PATH).read()
    checks = {
        "roster impact rendered": "Soft Tosser" in html,
        "my start rendered":      "Dustin May" in html,
        "streamer rendered":      "Streamy McStream" in html,
        "hot FA rendered":        "Hot Waiver Bat" in html,
        # Taylor Ward is rostered — he must not appear in the FA section
        "rostered FA filtered":   "Taylor Ward" not in
                                  html.split("Free Agent Watch")[-1]
                                      .split("Statcast")[0],
    }
    for label, ok in checks.items():
        if not ok:
            failures.append(f"inseason: {label}")
    os.remove(PREVIEW_PATH)


def run_offseason_checks(failures: list):
    os.environ["DIGEST_MODE"] = "offseason"
    from src.data import ai_client
    from src.analysis import discord_reader
    from src.daily_digest import run

    # Section parser on canned model output
    sections = ai_client.parse_offseason_sections(SAMPLE_BRIEF)
    if [s["label"] for s in sections] != [
        "My Roster", "My Prospects", "Transactions",
        "Prospect Pipeline", "2027 Outlook",
    ]:
        failures.append(f"offseason: parser produced {[s['label'] for s in sections]}")

    # Empty feed → no email, no crash, no preview written
    with mock.patch.object(discord_reader, "get_twitter_feed_posts",
                           return_value=[]):
        run()
    if os.path.exists(PREVIEW_PATH):
        failures.append("offseason: empty feed still produced a preview")

    # Feed present → brief rendered through the offseason template
    fake_posts = [{"channel": "twitter-dump", "handle": "mlbtraderumors",
                   "category": "TRANSACTIONS", "content": "Example Closer signs."}]
    with mock.patch.object(discord_reader, "get_twitter_feed_posts",
                           return_value=fake_posts), \
         mock.patch.object(ai_client, "generate_offseason_brief",
                           return_value=ai_client.parse_offseason_sections(SAMPLE_BRIEF)), \
         mock.patch.object(requests, "post",
                           side_effect=RuntimeError("network disabled in test")):
        # daily_digest imports these inside run_offseason — patch at source
        run()

    if not os.path.exists(PREVIEW_PATH):
        failures.append("offseason: preview was not written")
        return
    html = open(PREVIEW_PATH).read()
    checks = {
        "brief section rendered":  "Dustin May finished the season" in html,
        "outlook section rendered": "Breakout Arm" in html,
        "post count in footer":    "1 posts analyzed" in html,
        "offseason header":        "Hot Stove Digest" in html,
    }
    for label, ok in checks.items():
        if not ok:
            failures.append(f"offseason: {label}")


def main():
    failures = []
    run_inseason_checks(failures)
    run_offseason_checks(failures)

    if failures:
        print("\n❌ FAILURES:")
        for f in failures:
            print(f"   - {f}")
        sys.exit(1)
    print("\n✅ Offline digest smoke test passed (both modes).")


if __name__ == "__main__":
    main()
