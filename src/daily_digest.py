"""Daily digest entrypoint.

Two modes, selected by DIGEST_MODE (default: offseason):

    offseason — the digest IS the Twitter analysis: the Discord TweetShift
                feed goes to Claude for an offseason briefing focused on
                prospect development and 2027 fantasy outlooks,
                personalized to the WAR roster and farm system.

    inseason  — the full daily digest: roster impact, pitching planner,
                free agent heat, Statcast trends, prospects, and the
                baseball pulse (pybaseball + MLB Stats API + Discord).
"""
import os
from datetime import date

from src.data.snapshot_store import save_snapshot
from src.mailer.sender import send_email


def _deliver(subject: str, html: str) -> None:
    if os.getenv("DIGEST_DRY_RUN"):
        preview_path = os.getenv("DIGEST_PREVIEW_PATH", "digest_preview.html")
        with open(preview_path, "w") as f:
            f.write(html)
        print(f"🧪 DIGEST_DRY_RUN set — wrote {preview_path}, no email sent.")
        return
    send_email(subject, html)
    print("✅ Digest sent.")


def run_offseason(today: date) -> None:
    from src.analysis.discord_reader import get_twitter_feed_posts, get_posts_as_text
    from src.data.ai_client import generate_offseason_brief
    from src.mailer.renderer import render_offseason

    print("📡 Reading Discord Twitter feed...")
    posts     = get_twitter_feed_posts()
    feed_text = get_posts_as_text(posts)
    print(f"📡 Discord feed: {len(posts)} posts, {len(feed_text)} chars")

    if not feed_text:
        print("🥶 No feed activity — skipping today's offseason brief.")
        return

    print("🥶 Generating offseason brief...")
    brief = generate_offseason_brief(feed_text)
    if not brief:
        print("⚠️  Offseason brief came back empty — not sending.")
        return

    context = {
        "date":            today.strftime("%A, %B %-d"),
        "offseason_brief": brief,
        "post_count":      len(posts),
    }
    save_snapshot({"offseason": context}, today)
    html = render_offseason(context)
    _deliver(f"🥶 Hot Stove Digest — {context['date']}", html)


def run_inseason(today: date) -> None:
    from src.analysis.roster_analyzer import get_todays_roster_impact
    from src.analysis.free_agent_tracker import get_hot_free_agents
    from src.analysis.hitter_analyzer import get_statcast_trends
    from src.analysis.prospect_tracker import get_prospect_callouts
    from src.analysis.category_standings import get_matchup_status
    from src.analysis.discord_reader import get_posts_as_text
    from src.analysis.pitching_planner import get_pitching_planner
    from src.data.ai_client import generate_farm_report, generate_baseball_pulse
    from src.mailer.renderer import render_daily

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
    _deliver(f"⚾ Baseball Digest — {context['date']}", html)


def run():
    today = date.today()
    mode = os.getenv("DIGEST_MODE", "offseason").lower()
    print(f"Running daily digest for {today} (mode: {mode})")

    if mode == "inseason":
        run_inseason(today)
    else:
        run_offseason(today)


if __name__ == "__main__":
    run()
