"""Daily digest entrypoint — pybaseball + MLB Stats API + Discord feed.

No fantasy-host API involved: rosters come from combined_players.json
(trade bot), stats from the MLB Stats API and Baseball Savant, and news
from the TweetShift Twitter dump channels on Discord.
"""
import os
from datetime import date

from src.analysis.roster_analyzer import get_todays_roster_impact
from src.analysis.free_agent_tracker import get_hot_free_agents
from src.analysis.hitter_analyzer import get_statcast_trends
from src.analysis.prospect_tracker import get_prospect_callouts
from src.analysis.category_standings import get_matchup_status
from src.analysis.discord_reader import get_posts_as_text
from src.analysis.pitching_planner import get_pitching_planner
from src.data.ai_client import generate_farm_report, generate_baseball_pulse
from src.data.snapshot_store import save_snapshot
from src.mailer.renderer import render_daily
from src.mailer.sender import send_email


def run():
    today = date.today()
    print(f"Running daily digest for {today}")

    print("📊 Fetching matchup status...")
    matchup_status = get_matchup_status()

    print("📋 Fetching roster impact...")
    roster_impact = get_todays_roster_impact()

    print("⚾ Building pitching planner...")
    pitching_planner = get_pitching_planner()

    print("🔥 Fetching free agent heat...")
    hot_free_agents = get_hot_free_agents()

    print("📈 Running Statcast analysis...")
    statcast_trends = get_statcast_trends()

    print("🌱 Fetching prospect callouts...")
    prospect_callouts = get_prospect_callouts()

    print("🌾 Generating AI farm report...")
    farm_report = generate_farm_report(prospect_callouts)
    print(f"🌾 Farm report length: {len(farm_report)} chars")

    print("📡 Reading Discord Twitter feed...")
    feed_text = get_posts_as_text()
    print(f"📡 Discord feed length: {len(feed_text)} chars")
    print("📡 Generating baseball pulse...")
    baseball_pulse = generate_baseball_pulse(feed_text)
    print(f"📡 Baseball pulse length: {len(baseball_pulse)} chars")

    context = {
        "date":              today.strftime("%A, %B %-d"),
        "matchup_status":    matchup_status,
        "roster_impact":     roster_impact,
        "pitching_planner":  pitching_planner,
        "hot_free_agents":   hot_free_agents,
        "statcast_trends":   statcast_trends,
        "prospect_callouts": prospect_callouts,
        "farm_report":       farm_report,
        "baseball_pulse":    baseball_pulse,
    }

    save_snapshot({"daily": context}, today)
    html = render_daily(context)

    if os.getenv("DIGEST_DRY_RUN"):
        preview_path = os.getenv("DIGEST_PREVIEW_PATH", "digest_preview.html")
        with open(preview_path, "w") as f:
            f.write(html)
        print(f"🧪 DIGEST_DRY_RUN set — wrote {preview_path}, no email sent.")
        return

    send_email(f"⚾ Baseball Digest — {context['date']}", html)
    print("✅ Daily digest complete.")


if __name__ == "__main__":
    run()
