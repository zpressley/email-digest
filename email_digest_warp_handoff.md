# Email Digest — WARP Implementation Handoff
## April 14, 2026

Seven targeted changes. Work in order — later changes depend on earlier ones.

---

## Change 1 — IL Filter in yahoo_client.py

**File:** `src/data/yahoo_client.py`

**What:** Every method that returns pitchers (rostered starters, FA pitchers, bullpen) must skip players whose Yahoo selected_position is an IL slot, or whose status field indicates injury. Jackson Jobe was surfacing with full start projections despite being on IL. Max Scherzer was surfacing as a streaming add.

**Find the method `get_pitchers_with_remaining_starts()`** (and also `get_fa_pitchers_with_starts()`). Inside the loop that iterates players, add this block **before** appending the player to the result list:

```python
# ── IL filter — must be before any yield/append ──────────────────────────
NS = "{http://fantasysports.yahooapis.com/fantasy/v2/base.rng}"

selected_pos_el = player.find(f".//{NS}selected_position/{NS}position")
status_el       = player.find(f"{NS}status")

IL_SLOTS    = {"IL", "IL10", "IL60", "DL", "DL15", "DL60", "NA"}
IL_STATUSES = {"IL", "DL", "IR", "DTD"}   # DTD = day-to-day, skip conservatively

selected_pos = selected_pos_el.text.strip() if selected_pos_el is not None else ""
status_text  = status_el.text.strip()       if status_el       is not None else ""

if selected_pos in IL_SLOTS or status_text in IL_STATUSES:
    continue   # skip this player entirely
```

**Apply the same block in these three methods:**
1. `get_pitchers_with_remaining_starts(is_opponent=False)` — rostered SP/RP
2. `get_fa_pitchers_with_starts()` — free agent streamers
3. Any method that builds the bullpen list (usually named something like `get_bullpen_pitchers()` or done inline in `pitcher_analyzer.py`)

**Note:** `NS` is already defined at the top of your yahoo_client file as a module constant. Don't redefine it — just use the existing one.

---

## Change 2 — SP Cards 2-up Grid Layout

**File:** `mailer/matchup_engine.css`

**What:** SP start decision cards currently stack full-width one per row. Switch to a 2-column grid so two cards sit side by side, cutting vertical scroll roughly in half.

**Find `.start-decisions` and replace:**

```css
/* BEFORE */
.start-decisions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 20px;
}

/* AFTER */
.start-decisions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 20px;
}
```

**Add the mobile override** at the bottom of the `@media (max-width: 480px)` block that already exists in this file:

```css
@media (max-width: 480px) {
  /* ... existing rules ... */
  .start-decisions {
    grid-template-columns: 1fr;
  }
}
```

**Also in `.start-card`**, add `word-break: break-word;` so long pitcher names don't overflow the narrower card:

```css
.start-card {
  background: #ffffff;
  border: 1px solid #e8e8e4;
  border-radius: 8px;
  padding: 12px 14px;
  word-break: break-word;   /* ADD THIS LINE */
}
```

---

## Change 3 — Merge "My Pitcher Starts" into SP Cards

**What:** The "My Pitcher Starts" section and the "SP Start Decisions" section contain overlapping information and sometimes contradict each other. Kill "My Pitcher Starts" as a standalone section. Move its two unique data points — start date and opponent K% — into each SP card.

### Step A — weekly_matchup_engine.py: add fields to StartDecision

Find the `StartDecision` dataclass (or namedtuple). Add two fields:

```python
@dataclass
class StartDecision:
    name: str
    recommendation: str          # MUST_START / START / CONDITIONAL / SIT
    confidence: str              # HIGH / MEDIUM / LOW
    data_quality: str            # "62 apps (STRONG)" etc.
    ip_floor_flag: bool
    projection: PitcherProjection
    start_date_label: str = ""   # ADD: "Today", "Thu Apr 17", "Fri Apr 18"
    opp_k_pct: float = 0.0       # ADD: opposing team K% from team_offense_ranker
```

### Step B — weekly_matchup_engine.py: populate those fields

In the loop where you build `start_decisions`, after calling `make_start_decision()`, set the new fields:

```python
dec = make_start_decision(proj, cat_outcomes, banked_ip, other_ip)

# Populate start date label
game_date = proj.game_date  # already on PitcherProjection
today = date.today()
if game_date == today:
    dec.start_date_label = "Today"
elif game_date == today + timedelta(days=1):
    dec.start_date_label = "Tomorrow"
else:
    dec.start_date_label = game_date.strftime("%a %b %-d")  # "Thu Apr 17"

# Populate opponent K%
opp_team = proj.opp_team_abbr   # e.g. "LAA" — already on projection
opp_stats = team_offense_ranker.get_team_stats(opp_team)
dec.opp_k_pct = opp_stats.get("k_pct", 0.0) if opp_stats else 0.0

start_decisions.append(dec)
```

### Step C — weekly_matchup_engine.py: update render_scorecard() SP card HTML

Find the block inside `render_scorecard()` that renders each start card. Add the date badge and K% to the `opp-line`:

```python
# Date badge — colored by urgency
if dec.start_date_label == "Today":
    date_badge = '<span class="rec-badge" style="background:#fce8e6;color:#c5221f">Today</span>'
elif dec.start_date_label == "Tomorrow":
    date_badge = '<span class="rec-badge" style="background:#fff3cd;color:#856404">Tomorrow</span>'
else:
    date_badge = f'<span class="rec-badge" style="background:#f1f0ec;color:#5a5a54">{dec.start_date_label}</span>'

# Build the opp line with K%
k_pct_str = f" · K% {dec.opp_k_pct:.1f}%" if dec.opp_k_pct else ""
opp_line_html = (
    f'<div class="opp-line">'
    f'vs {dec.projection.opp_team_abbr} (offense rank #{dec.projection.opp_offense_rank})'
    f'{k_pct_str}'
    f'</div>'
)

# In the start-header div, add date_badge after the rec_badge:
# <span class="pitcher-name">...</span>
# <span class="rec-badge pill-...">MUST_START</span>
# {date_badge}                              <-- INSERT HERE
# <span class="conf-badge">HIGH</span>
# ...
```

Full card HTML block (replace the existing one in render_scorecard):

```python
html.append(f'''
<div class="start-card">
  <div class="start-header">
    <span class="pitcher-name">{dec.name}</span>
    <span class="rec-badge pill-{dec.recommendation.lower().replace("_","-")}">{dec.recommendation}</span>
    {date_badge}
    <span class="conf-badge">{dec.confidence}</span>
    <span class="dq-badge" style="color:{dq_color}">{dec.data_quality}</span>
    {"<span class='ip-flag'>⚠️ IP</span>" if dec.ip_floor_flag else ""}
  </div>
  {opp_line_html}
  <div class="scenarios">
    <div class="scenario bad">🔴 Bad: &nbsp;{bad_line}</div>
    <div class="scenario avg">🟡 Avg: &nbsp;{avg_line}</div>
    <div class="scenario good">🟢 Good: {good_line}</div>
  </div>
  <div class="reasoning">{reasoning}</div>
</div>
''')
```

### Step D — Remove "My Pitcher Starts" from daily_digest.py and template

In `src/daily_digest.py`, find where `my_pitcher_starts` is built and passed to the template. **Delete that block entirely.** It's typically something like:

```python
# DELETE THIS WHOLE BLOCK:
pitcher_starts = pitcher_analyzer.get_my_pitcher_starts(
    yahoo_client=yahoo_client,
    mlb_client=mlb_client,
    ...
)
```

In `mailer/digest_template.html` (or wherever your Jinja2 template lives), find the `<!-- MY PITCHER STARTS -->` comment block and **delete it along with its surrounding `<div class="section">...</div>`**.

---

## Change 4 — Bullpen Section: Collapse to Single Summary Line

**What:** The full RP table (pitcher, expected apps, avg line, data strength) is noise. Replace with one actionable sentence about APP category status.

### Step A — pitcher_analyzer.py: add build_bullpen_summary()

Add this function to `src/analysis/pitcher_analyzer.py`:

```python
def build_bullpen_summary(
    bullpen: list[dict],
    cat_outcomes: list,          # same cat_outcomes from weekly_matchup_engine
) -> str:
    """
    Returns a single sentence summarizing bullpen APP contribution.
    bullpen: list of dicts with keys: name, expected_apps
    cat_outcomes: list of CatOutcome objects from weekly_matchup_engine
    """
    total_apps = sum(p.get("expected_apps", 0) for p in bullpen)

    # Find APP cat outcome
    app_outcome = next((c for c in cat_outcomes if c.cat == "APP"), None)

    if app_outcome is None:
        return f"Bullpen projects {total_apps:.1f} appearances this week."

    action = app_outcome.action  # SAFE / HEDGE / NEED_HELP
    opp_avg = app_outcome.opp_avg_val
    your_avg = app_outcome.your_avg_val

    if action == "SAFE":
        return (
            f"Bullpen projects {total_apps:.1f} apps — "
            f"sufficient to win APP (you {your_avg:.0f} vs opp avg {opp_avg:.0f})."
        )
    elif action == "HEDGE":
        gap = opp_avg - your_avg
        return (
            f"Bullpen projects {total_apps:.1f} apps. "
            f"APP is close — opponent averages {opp_avg:.0f} vs your {your_avg:.0f}. "
            f"High-leverage RP usage this week matters."
        )
    else:  # NEED_HELP
        deficit = opp_avg - your_avg
        return (
            f"⚠️ Bullpen projects {total_apps:.1f} apps but APP is a losing category "
            f"(you avg {your_avg:.0f}, opp avg {opp_avg:.0f}, deficit {deficit:.0f}). "
            f"Consider streaming a high-appearance RP from FA."
        )
```

### Step B — weekly_matchup_engine.py: call it and pass to renderer

In `get_weekly_matchup_section()`, after building `bullpen` and `cat_outcomes`:

```python
from src.analysis.pitcher_analyzer import build_bullpen_summary

bullpen_summary = build_bullpen_summary(bullpen, cat_outcomes)

# Pass to WeekPlan (add bullpen_summary field to WeekPlan dataclass)
plan = WeekPlan(
    ...
    bullpen=bullpen,               # keep for internal use if needed
    bullpen_summary=bullpen_summary,  # ADD THIS
    ...
)
```

Add `bullpen_summary: str = ""` to the `WeekPlan` dataclass.

### Step C — render_scorecard(): replace RP table with summary line

Find the block that renders the RP table. Replace it entirely:

```python
# BEFORE (delete this whole block):
# html.append('<h3>🔥 Bullpen Projection</h3>')
# html.append('<p class="rp-note">Expected appearances based on workload rate...</p>')
# html.append('<table class="rp-table">...')
# ... table rows ...
# html.append('</table>')

# AFTER:
if plan.bullpen_summary:
    html.append(f'''
    <div class="alert-bar" style="margin-top:12px;margin-bottom:4px;">
      🔥 <b>Bullpen:</b> {plan.bullpen_summary}
    </div>
    ''')
```

The `alert-bar` class already exists in the digest CSS — reuse it. No new CSS needed.

---

## Change 5 — Statcast Signals: Add Deltas

**What:** Show the directional change that drove the trend score, not just the current value. "Montgomery trending down" with Whiff% 35.5 is meaningless without knowing it was 28.0 two weeks ago.

**File:** `src/data/statcast_client.py`

### Step A — Pull two time windows instead of one

Find where you fetch Statcast data (likely calling Baseball Savant or the MLB Stats API). You need two rolling windows per player:

```python
def get_player_statcast_splits(mlb_id: int) -> dict:
    """
    Returns current 14-day and prior 14-day Statcast splits.
    Uses Baseball Savant player page API.
    """
    from datetime import date, timedelta
    today = date.today()
    
    # Current window: last 14 days
    current_end   = today
    current_start = today - timedelta(days=14)
    
    # Prior window: days 15-28
    prior_end   = today - timedelta(days=15)
    prior_start = today - timedelta(days=29)
    
    def fetch_window(start, end):
        url = (
            f"https://baseballsavant.mlb.com/statcast_search/csv"
            f"?player_id={mlb_id}"
            f"&game_date_gt={start.strftime('%Y-%m-%d')}"
            f"&game_date_lt={end.strftime('%Y-%m-%d')}"
            f"&type=batter"
            f"&min_pitches=0"
        )
        try:
            import pandas as pd
            df = pd.read_csv(url)
            if df.empty:
                return {}
            return {
                "whiff_pct":   _calc_whiff(df),
                "chase_pct":   _calc_chase(df),
                "barrel_pct":  _calc_barrel(df),
                "hard_hit_pct": _calc_hard_hit(df),
                "pa":          len(df["at_bat_number"].unique()) if "at_bat_number" in df.columns else 0,
            }
        except Exception:
            return {}
    
    current = fetch_window(current_start, current_end)
    prior   = fetch_window(prior_start, prior_end)
    
    return {"current": current, "prior": prior}
```

**If you're not hitting Baseball Savant CSV directly** and are using a different approach (e.g., pre-cached data), apply the same two-window logic to whatever data source you have. The key is producing a `current` dict and a `prior` dict with the same keys.

### Step B — Compute deltas in get_statcast_signals()

Find `get_statcast_signals()` or equivalent in `statcast_client.py`. After fetching both windows, compute deltas:

```python
def _compute_deltas(current: dict, prior: dict) -> dict:
    """Returns dict of metric -> (current_val, prior_val, delta)."""
    deltas = {}
    for key in current:
        if key == "pa":
            continue
        c = current.get(key)
        p = prior.get(key)
        if c is not None and p is not None:
            deltas[key] = {
                "current": round(c, 1),
                "prior":   round(p, 1),
                "delta":   round(c - p, 1),
            }
    return deltas
```

### Step C — Add deltas to the signal dict returned per player

Each player signal dict should now include a `"deltas"` key:

```python
signal = {
    "name":   player_name,
    "trend":  "up" or "down",    # existing
    "score":  float,              # existing signal score
    "pa":     int,                # existing
    "metrics": current_metrics,  # existing current values
    "deltas":  _compute_deltas(current_metrics, prior_metrics),  # ADD
    "metric_source": "discipline metrics only" or "contact metrics",  # existing
}
```

### Step D — Render deltas in the Statcast section of the template

**File:** `mailer/digest_template.html` (the Jinja2 template that renders the Statcast section)

Find the stat-box loop. Replace it with a version that shows delta:

```html
{% for key, label in [("whiff_pct","Whiff%"), ("chase_pct","Chase%"), ("barrel_pct","Barrel%"), ("hard_hit_pct","Hard Hit%")] %}
  {% if player.metrics.get(key) is not none %}
  {% set current_val = player.metrics[key] %}
  {% set delta_info  = player.deltas.get(key) %}
  {% set delta_val   = delta_info.delta if delta_info else none %}

  {# Color logic: for whiff/chase, up=bad; for barrel/hard_hit, up=good #}
  {% if key in ["whiff_pct", "chase_pct"] %}
    {% set is_good = (current_val < 25) %}
  {% else %}
    {% set is_good = (current_val > 10) %}
  {% endif %}
  {% set val_class = "good" if is_good else ("bad" if not is_good) else "" %}

  <div class="stat-box">
    <div class="stat-label">{{ label }}</div>
    <div class="stat-val {{ val_class }}">{{ "%.1f"|format(current_val) }}</div>
    {% if delta_val is not none and delta_val != 0 %}
      {% set delta_positive = delta_val > 0 %}
      {# For whiff/chase: increase is bad (red). For barrel/hard_hit: increase is good (green) #}
      {% if key in ["whiff_pct", "chase_pct"] %}
        {% set delta_class = "bad" if delta_positive else "good" %}
      {% else %}
        {% set delta_class = "good" if delta_positive else "bad" %}
      {% endif %}
      <div class="stat-delta {{ delta_class }}">
        {{ "▲" if delta_positive else "▼" }}{{ "%.1f"|format(delta_val|abs) }}
      </div>
    {% endif %}
  </div>
  {% endif %}
{% endfor %}
```

Add the `.stat-delta` CSS to `mailer/styles.css`:

```css
.stat-delta {
  font-size: 9px;
  font-weight: 700;
  margin-top: 1px;
  letter-spacing: 0.03em;
}
.stat-delta.good { color: #137333; }
.stat-delta.bad  { color: #c5221f; }
```

---

## Change 6 — Matchup Section: Opponent Name + Current Score

**What:** The matchup table shows no opponent name and no current live score. By Tuesday you've already played Monday's games — you need to know where you actually stand.

### Step A — yahoo_client.py: expose opponent name from get_current_matchup_full()

Find `get_current_matchup_full()`. It already returns both teams' data. Make sure it also returns:

```python
def get_current_matchup_full(self) -> dict:
    # ... existing fetch logic ...
    
    # ADD these to the return dict:
    return {
        # ... existing keys ...
        "opponent_team_name": opponent_abbr,     # e.g. "HAM"
        "current_score_you":  cats_you_winning,  # int — count of cats you're winning NOW
        "current_score_opp":  cats_opp_winning,  # int — count of cats they're winning NOW
        "score_as_of":        "Mon Apr 13",       # human-readable last-updated day
    }
```

To compute `cats_you_winning` from banked stats:

```python
# Both teams' banked stats are already in your matchup fetch.
# For each of the 20 categories, compare banked totals.
# Lower-is-better cats: ER, HR(pit), ERA, H/9, BB/9, K(bat)
LOWER_BETTER = {"ER", "HR_pit", "ERA", "H/9", "BB/9", "K_bat"}

cats_you_winning = 0
cats_opp_winning = 0

for cat, your_val, opp_val in banked_category_pairs:
    if cat in LOWER_BETTER:
        if your_val < opp_val:
            cats_you_winning += 1
        elif opp_val < your_val:
            cats_opp_winning += 1
    else:
        if your_val > opp_val:
            cats_you_winning += 1
        elif opp_val > your_val:
            cats_opp_winning += 1
```

### Step B — weekly_matchup_engine.py: pass opponent name and score to WeekPlan

Add to `WeekPlan` dataclass:

```python
@dataclass
class WeekPlan:
    # ... existing fields ...
    opponent_name:    str = ""
    current_score_you: int = 0
    current_score_opp: int = 0
    score_as_of:       str = ""
```

In `get_weekly_matchup_section()`, after calling `get_current_matchup_full()`:

```python
matchup = yahoo_client.get_current_matchup_full()

# Extract new fields
opponent_name     = matchup.get("opponent_team_name", "OPP")
current_score_you = matchup.get("current_score_you", 0)
current_score_opp = matchup.get("current_score_opp", 0)
score_as_of       = matchup.get("score_as_of", "")
```

### Step C — render_scorecard(): add header with opponent + score

In `render_scorecard()`, replace the plain `<h2>` header:

```python
# BEFORE:
html.append('<h2>📊 Weekly Matchup Projection</h2>')

# AFTER:
score_display = (
    f'<span style="color:#137333;font-weight:700">WAR {plan.current_score_you}</span>'
    f' — '
    f'<span style="color:#c5221f;font-weight:700">{plan.opponent_name} {plan.current_score_opp}</span>'
)
score_note = f' <span style="font-size:0.72rem;color:#9a9a94;font-weight:400">through {plan.score_as_of}</span>' if plan.score_as_of else ""

html.append(f'<h2>📊 WAR vs {plan.opponent_name}{score_note}<br>'
            f'<span style="font-size:1rem;font-weight:600">{score_display}</span></h2>')
```

---

## Change 7 — Week Plan: Daily Refresh with Current Score Context

**What:** Week Plan is generated Monday and never updated. By Tuesday it's stale. Regenerate it every day using actual banked stats.

**File:** `src/analysis/weekly_matchup_engine.py` — `build_summary()` function

### Step A — Pass current banked stats and actual score into build_summary()

Change the signature:

```python
# BEFORE:
def build_summary(cat_outcomes, ip_plan, sp_decisions):

# AFTER:
def build_summary(cat_outcomes, ip_plan, sp_decisions,
                  current_score_you=0, current_score_opp=0,
                  opponent_name="OPP", score_as_of=""):
```

### Step B — Replace the prose output with structured daily plan

Replace the body of `build_summary()`:

```python
def build_summary(cat_outcomes, ip_plan, sp_decisions,
                  current_score_you=0, current_score_opp=0,
                  opponent_name="OPP", score_as_of=""):

    safe      = [c for c in cat_outcomes if c.action == "SAFE"]
    hedge     = [c for c in cat_outcomes if c.action == "HEDGE"]
    need_help = [c for c in cat_outcomes if c.action in ("NEED_HELP","STREAM_K","STREAM_QS")]

    must_starts = [d for d in sp_decisions if d.recommendation == "MUST_START"]
    must_names  = ", ".join(d.name for d in must_starts) if must_starts else "None"

    # IP status
    ip_banked  = ip_plan.banked
    ip_needed  = max(0, IP_MINIMUM - ip_banked)
    starts_est = math.ceil(ip_needed / 5.0) if ip_needed > 0 else 0
    ip_line = (
        f"✅ IP minimum covered ({ip_banked:.1f}/{IP_MINIMUM:.0f} IP banked)"
        if ip_needed == 0
        else f"⚠️ {ip_needed:.1f} IP short of {IP_MINIMUM:.0f} min — need ~{starts_est} more SP start(s)"
    )

    # Current score
    score_line = ""
    if score_as_of:
        leader = "WAR leads" if current_score_you > current_score_opp else (
                 f"{opponent_name} leads" if current_score_opp > current_score_you else "Tied")
        score_line = (
            f"Current score: {leader} {max(current_score_you,current_score_opp)}"
            f"–{min(current_score_you,current_score_opp)} "
            f"(through {score_as_of}). "
        )

    parts = [
        score_line,
        f"Projected {len(safe)+len(hedge)}/20 categories in your favor (avg vs avg). ",
        f"Safe at worst-case: {', '.join(c.cat for c in safe)}. " if safe else "",
        (f"Vulnerable: {', '.join(c.cat for c in hedge)} — one bad pitching stretch flips these. "
         if hedge else ""),
        (f"Need help: {', '.join(c.cat for c in need_help)}. "
         if need_help else ""),
        ip_line + ". ",
        f"Must-start remaining: {must_names}.",
    ]

    return "".join(p for p in parts if p)
```

### Step C — Pass the new args when calling build_summary()

In `get_weekly_matchup_section()`, update the call:

```python
summary = build_summary(
    cat_outcomes,
    ip_plan,
    sp_decisions_only,
    current_score_you=current_score_you,    # from matchup fetch
    current_score_opp=current_score_opp,
    opponent_name=opponent_name,
    score_as_of=score_as_of,
)
```

---

## Dependency / Order Summary

```
Change 1 (IL filter)          — standalone, do first, unblocks everything
Change 2 (SP grid CSS)        — standalone CSS, no deps
Change 3 (merge SP sections)  — depends on Change 1 (clean pitcher list)
Change 4 (bullpen summary)    — depends on Change 3 (cat_outcomes already flowing)
Change 5 (Statcast deltas)    — standalone, statcast_client.py only
Change 6 (opponent + score)   — depends on yahoo_client changes in Change 1
Change 7 (daily week plan)    — depends on Change 6 (needs opponent_name, score)
```

## Files Modified Summary

| File | Changes |
|------|---------|
| `src/data/yahoo_client.py` | IL filter in 3 methods; expose opponent name + current score from `get_current_matchup_full()` |
| `src/data/statcast_client.py` | Two-window fetch; delta computation; deltas in signal dict |
| `src/analysis/pitcher_analyzer.py` | Add `build_bullpen_summary()` |
| `src/analysis/weekly_matchup_engine.py` | `StartDecision` new fields; populate date/K% in loop; `WeekPlan` new fields; updated `build_summary()`; `render_scorecard()` — SP card HTML, replace RP table, updated header |
| `mailer/matchup_engine.css` | `.start-decisions` grid; `.start-card` word-break; mobile override |
| `mailer/styles.css` | `.stat-delta` with `.good`/`.bad` variants |
| `mailer/digest_template.html` | Statcast stat-box loop with delta display; remove My Pitcher Starts section |
| `src/daily_digest.py` | Remove `pitcher_starts` build block; pass new `current_score_*` / `opponent_name` / `score_as_of` to template context |

## Do NOT Touch
- `token_manager.py`, `bot.py`, anything in `fbp-trade-bot` repo — digest is standalone
- `combined_players.json` — read-only, source of mlb_id_map
- `ANTHROPIC_KEY` env var name — already correct, don't rename
- `PYTHONPATH=.` in GitHub Actions — required, don't remove
