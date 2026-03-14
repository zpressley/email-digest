"""Jinja2 email renderer."""
from jinja2 import Environment, FileSystemLoader
import os

TEMPLATE_DIR = os.path.dirname(__file__)
env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))


def render_daily(context: dict) -> str:
    return env.get_template("daily_template.html").render(**context)


def render_weekly(context: dict) -> str:
    return env.get_template("weekly_template.html").render(**context)
