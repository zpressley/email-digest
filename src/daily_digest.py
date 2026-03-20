"""Daily digest entrypoint."""
from datetime import date
from src.analysis.roster_analyzer import get_todays_roster_impact
from src.analysis.pitcher_analyzer import get_my_upcoming_starts
from src.analysis.matchup_finder import get_streaming_opportunities
from src.analysis.free_agent_tracker import get_hot_free_agents
from src.analysis.hitter_analyzer import get_breakout_watch, get_bench_candidates
from src.analysis.prospect_tracker import get_prospect_callouts
from src.analysis.discord_reader import get_posts_as_text
from src.data.ai_client import generate_farm_report, generate_baseball_pulse
from src.data.snapshot_store import save_snapshot
from src.mailer.renderer import render_daily
from src.mailer.sender import send_email


def run():
    today = date.today()
    print(f"Running daily digest for {today}")

    # Core analysis
    print("📋 Fetching roster impact...")
    roster_impact = get_todays_roster_impact()

    print("🔄 Fetching upcoming starts...")
    upcoming_starts = get_my_upcoming_starts(days_ahead=5)

    print("🎯 Finding streaming opportunities...")
    streaming_opportunities = get_streaming_opportunities()

    print("🔥 Fetching free agent heat...")
    hot_free_agents = get_hot_free_agents()

    print("📈 Running Statcast analysis...")
    breakout_watch = get_breakout_watch()
    bench_candidates = get_bench_candidates()

    print("🌱 Fetching prospect callouts...")
    prospect_callouts = get_prospect_callouts()

    # AI features
    print("🌾 Generating AI farm report...")
    farm_report = generate_farm_report(prospect_callouts)

    print("📡 Reading Discord Twitter feed...")
    feed_text = get_posts_as_text()
    print("📡 Generating baseball pulse...")
    baseball_pulse = generate_baseball_pulse(feed_text)

    context = {
        "date":                   today.strftime("%A, %B %-d"),
        "roster_impact":          roster_impact,
        "upcoming_starts":        upcoming_starts,
        "streaming_opportunities": streaming_opportunities,
        "hot_free_agents":        hot_free_agents,
        "breakout_watch":         breakout_watch,
        "bench_candidates":       bench_candidates,
        "prospect_callouts":      prospect_callouts,
        "farm_report":            farm_report,
        "baseball_pulse":         baseball_pulse,
    }

    save_snapshot({"daily": context}, today)

    html = render_daily(context)
    send_email(f"⚾ Fantasy Digest — {context['date']}", html)
    print("✅ Daily digest complete.")


if __name__ == "__main__":
    run()
