"""Weekly review entrypoint."""
from datetime import date
from src.analysis.category_standings import get_category_dashboard, get_target_categories
from src.analysis.pitcher_analyzer import get_league_pitcher_usage
from src.data.snapshot_store import save_snapshot
from src.email.renderer import render_weekly
from src.email.sender import send_email


def run():
    today = date.today()
    print(f"Running weekly review for {today}")

    dashboard = get_category_dashboard()
    target_cats = get_target_categories(dashboard)

    context = {
        "date": today.strftime("%A, %B %-d"),
        "category_dashboard": dashboard,
        "target_categories": target_cats,
        "league_pitcher_usage": get_league_pitcher_usage(),
    }

    save_snapshot({"weekly": context}, today)
    html = render_weekly(context)
    send_email(f"⚾ Weekly Review — {context['date']}", html)
    print("Weekly review complete.")


if __name__ == "__main__":
    run()
