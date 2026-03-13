"""Daily digest entrypoint."""
from datetime import date
from src.analysis.roster_analyzer import get_todays_roster_impact
from src.analysis.pitcher_analyzer import get_my_upcoming_starts
from src.analysis.matchup_finder import get_streaming_opportunities
from src.analysis.free_agent_tracker import get_hot_free_agents
from src.analysis.hitter_analyzer import get_breakout_watch, get_bench_candidates
from src.analysis.prospect_tracker import get_prospect_callouts
from src.data.snapshot_store import save_snapshot
from src.email.renderer import render_daily
from src.email.sender import send_email


def run():
    today = date.today()
    print(f"Running daily digest for {today}")

    context = {
        "date": today.strftime("%A, %B %-d"),
        "roster_impact": get_todays_roster_impact(),
        "upcoming_starts": get_my_upcoming_starts(days_ahead=5),
        "streaming_opportunities": get_streaming_opportunities(),
        "hot_free_agents": get_hot_free_agents(),
        "breakout_watch": get_breakout_watch(),
        "bench_candidates": get_bench_candidates(),
        "prospect_callouts": get_prospect_callouts(),
    }

    save_snapshot({"daily": context}, today)
    html = render_daily(context)
    send_email(f"⚾ Fantasy Digest — {context['date']}", html)
    print("Daily digest complete.")


if __name__ == "__main__":
    run()
