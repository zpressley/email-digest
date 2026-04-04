"""Daily digest entrypoint."""
import json
import os
from datetime import date
from src.analysis.roster_analyzer import get_todays_roster_impact
from src.analysis.pitcher_analyzer import get_my_upcoming_starts
from src.analysis.matchup_finder import get_streaming_opportunities
from src.analysis.free_agent_tracker import get_hot_free_agents
from src.analysis.hitter_analyzer import get_statcast_trends
from src.analysis.prospect_tracker import get_prospect_callouts
from src.analysis.category_standings import get_matchup_status
from src.analysis.discord_reader import get_posts_as_text
from src.data.ai_client import generate_farm_report, generate_baseball_pulse
from src.data.snapshot_store import save_snapshot
from src.data.yahoo_client import YahooClient
from src.data.mlb_client import MLBClient
from src.data import team_offense_ranker
from src.data.weekly_matchup_engine import get_weekly_matchup_section
from src.config import COMBINED_PLAYERS_PATH
from src.mailer.renderer import render_daily
from src.mailer.sender import send_email


def run():
    today = date.today()
    print(f"Running daily digest for {today}")

    print("📊 Fetching matchup status...")
    matchup_status = get_matchup_status()

    print("📋 Fetching roster impact...")
    roster_impact = get_todays_roster_impact()

    print("🔄 Fetching upcoming starts...")
    upcoming_starts = get_my_upcoming_starts(days_ahead=5)

    print("⚡ Finding streaming opportunities...")
    streaming_opportunities = get_streaming_opportunities()

    print("🔥 Fetching free agent heat...")
    hot_free_agents = get_hot_free_agents()

    print("📈 Running Statcast analysis...")
    statcast_trends = get_statcast_trends()

    print("🌱 Fetching prospect callouts...")
    prospect_callouts = get_prospect_callouts()

    print("🌾 Generating AI farm report...")
    farm_report = generate_farm_report(prospect_callouts)

    print("📡 Reading Discord Twitter feed...")
    feed_text = get_posts_as_text()
    print("📡 Generating baseball pulse...")
    baseball_pulse = generate_baseball_pulse(feed_text)

    print("📊 Building weekly matchup projection...")
    try:
        with open(COMBINED_PLAYERS_PATH) as f:
            combined_players = json.load(f)
    except Exception:
        combined_players = []
    yahoo  = YahooClient()
    mlb    = MLBClient()
    weekly_matchup_section = get_weekly_matchup_section(
        yahoo, mlb, team_offense_ranker, combined_players
    )

    context = {
        "date":                    today.strftime("%A, %B %-d"),
        "matchup_status":          matchup_status,
        "roster_impact":           roster_impact,
        "upcoming_starts":         upcoming_starts,
        "streaming_opportunities": streaming_opportunities,
        "hot_free_agents":         hot_free_agents,
        "statcast_trends":         statcast_trends,
        "prospect_callouts":       prospect_callouts,
        "farm_report":             farm_report,
        "baseball_pulse":          baseball_pulse,
        "weekly_matchup_section":  weekly_matchup_section,
    }

    save_snapshot({"daily": context}, today)
    html = render_daily(context)
    send_email(f"⚾ Fantasy Digest — {context['date']}", html)
    print("✅ Daily digest complete.")


if __name__ == "__main__":
    run()