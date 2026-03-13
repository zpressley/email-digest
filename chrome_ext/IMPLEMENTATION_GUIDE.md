# FBP Hub Chrome Extension — Implementation Guide
### FBP+ Table Hacker + Baseline Calculator

---

## Overview

This system has two parts that work together:

1. **Chrome Extension** — injected onto Yahoo Fantasy Baseball. Reads `league_baselines.json` from GitHub and replaces Yahoo's raw stat cells with FBP+ heatmap values when toggled on (100 = league average for your 12-team rostered pool)

2. **`calculate_baselines.py`** — runs daily in your existing pipeline. Fetches season stats for all rostered players from Yahoo API, stores cumulative snapshots in the email-digest repo, diffs them to derive windowed baselines (Last 7/14/30), and overwrites `league_baselines.json` in fbp-hub so the extension always has fresh numbers

---

## File Inventory

| File | Repo | Purpose |
|------|------|---------|
| `content.js` | Extension (local) | Badge injection + FBP+ toggle + heatmap logic |
| `style.css` | Extension (local) | Badge styles + FBP+ button + heatmap colors |
| `manifest.json` | Extension (local) | Chrome extension config (unchanged) |
| `calculate_baselines.py` | `fbp-trade-bot` | Daily baseline calculator |
| `data/baselines/YYYY-MM-DD.json` | `email-digest` | Daily cumulative snapshots (one per day) |
| `data/league_baselines.json` | `fbp-hub` | Rolled-up baselines — what the extension reads |

---

## Part 1: Chrome Extension Setup

### Step 1 — Replace extension files

Drop the new `content.js` and `style.css` into your extension folder, replacing the existing versions. `manifest.json` is unchanged.

```
your-extension-folder/
  manifest.json      ← unchanged
  content.js         ← replace with new version
  style.css          ← replace with new version
```

### Step 2 — Reload the extension

1. Go to `chrome://extensions`
2. Find "Dynasty Prospect Overlay"
3. Click the refresh icon

### Step 3 — Verify badges still work

Open `baseball.fantasysports.yahoo.com` and confirm prospect/contract badges still appear on player names. If they do, Phase 1 is intact.

### Step 4 — FBP+ toggle

The `⚡ FBP+ [OFF]` button will appear near Yahoo's stat toolbar **only after `league_baselines.json` exists in fbp-hub**. It will be invisible until then — this is intentional so it doesn't show up broken.

Once baselines are live, clicking the button:
- Replaces every stat cell in the visible table with its FBP+ value
- Colors cells on a green → charcoal → red heatmap (100 = league avg)
- Hover any cell to see the raw value + FBP+ value in a tooltip
- Click again to revert to raw stats
- Automatically re-applies when Yahoo loads new data (tab changes, sorting, etc.)

### How FBP+ is computed

```
FBP+ = (player_stat / league_baseline) × 100

For inverted stats (lower = better):
FBP+ = (league_baseline / player_stat) × 100
```

**Inverted pitcher stats:** `ER`, `ERA`, `HR`, `H/9`, `BB/9`, `TB`
**Inverted batter stats:** `K` (strikeouts only)

**Heatmap tiers:**

| Range | Color | Label |
|-------|-------|-------|
| 160+ | Deep green | Elite |
| 130–159 | Medium green | Great |
| 115–129 | Light green | Above avg |
| 85–114 | Charcoal | Average |
| 70–84 | Orange | Below avg |
| <70 | FBP Red | Poor |

### Yahoo timeframe detection

The extension reads whichever Yahoo timeframe tab is active and pulls the matching baseline:

| Yahoo tab | Baseline key |
|-----------|-------------|
| Today (live) | `today` |
| Last 7 Days (total) | `last7` |
| Last 14 Days (total) | `last14` |
| Last 30 Days (total) | `last30` |
| Season (total) | `season` |

> **Note:** Only "total" tabs are supported. Avg, projection, and std dev tabs will fall back to `season` baseline since those views don't match how baselines are computed.

---

## Part 2: Baseline Calculator Setup

### Step 1 — Place the script

Copy `calculate_baselines.py` into the root of your `fbp-trade-bot` repo alongside `update_yahoo_players.py` and `token_manager.py`.

```
fbp-trade-bot/
  calculate_baselines.py    ← add this
  update_yahoo_players.py
  token_manager.py
  data/
    yahoo_players.json      ← read by this script
```

### Step 2 — Wire into `update_all.py`

Add one line after `update_yahoo_players.py`:

```python
def run_all():
    run_script("build_mlb_id_cache.py")
    run_script("track_roster_status.py")
    run_script("log_roster_events.py")
    run_script("update_yahoo_players.py")
    run_script("calculate_baselines.py")    # ← add this line
    run_script("update_hub_players.py")
    run_script("update_wizbucks.py")
    run_script("merge_players.py")
    run_script("save_standings.py")
```

It must run after `update_yahoo_players.py` because it reads `data/yahoo_players.json`.

### Step 3 — Set up repo paths

The script writes to two repos. Set these environment variables in your GitHub Actions workflow:

```yaml
env:
  EMAIL_DIGEST_DATA_DIR: ../email-digest/data
  FBP_HUB_DATA_DIR: ../fbp-hub/data
```

And check out both repos in your workflow before running the pipeline:

```yaml
steps:
  - name: Checkout fbp-trade-bot
    uses: actions/checkout@v3
    with:
      path: fbp-trade-bot

  - name: Checkout email-digest
    uses: actions/checkout@v3
    with:
      repository: zpressley/email-digest   # adjust to your actual repo name
      path: email-digest
      token: ${{ secrets.GITHUB_TOKEN }}

  - name: Checkout fbp-hub
    uses: actions/checkout@v3
    with:
      repository: zpressley/fbp-hub
      path: fbp-hub
      token: ${{ secrets.GITHUB_TOKEN }}
```

Then commit both repos at the end of the workflow:

```yaml
  - name: Commit email-digest snapshots
    working-directory: email-digest
    run: |
      git config user.name "github-actions"
      git config user.email "actions@github.com"
      git add data/baselines/
      git diff --staged --quiet || git commit -m "Daily baseline snapshot $(date +%F)"
      git push

  - name: Commit fbp-hub baselines
    working-directory: fbp-hub
    run: |
      git config user.name "github-actions"
      git config user.email "actions@github.com"
      git add data/league_baselines.json
      git diff --staged --quiet || git commit -m "Update league baselines $(date +%F)"
      git push
```

### Step 4 — First run

On the first run, no historical snapshots exist yet so Last 7/14/30 baselines will fall back to season totals (the script warns but doesn't fail). After 7, 14, and 30 days of snapshots accumulating, the windowed baselines will be accurate.

---

## How the baseline math works

### What gets stored each day

`email-digest/data/baselines/2026-03-13.json`:

```json
{
  "date": "2026-03-13",
  "n_batters": 156,
  "n_pitchers": 108,
  "batter_totals": {
    "R": 4820, "H": 9100, "HR": 1240, ...
  },
  "pitcher_totals": {
    "APP": 890, "IP": 3240.2, "ER": 1180, "K": 3890,
    "H_allowed": 3020, "BB_allowed": 980, ...
  }
}
```

These are **raw league-wide cumulative season totals** — the sum of every rostered player's season stats at that moment in time.

### How windowed baselines are derived

```
Last 7 counting stats:
  R_last7 = batter_totals[today].R - batter_totals[today-7].R
  baseline_R = R_last7 / n_batters

Last 7 rate stats (IP-weighted):
  K_last7  = pitcher_totals[today].K  - pitcher_totals[today-7].K
  IP_last7 = pitcher_totals[today].IP - pitcher_totals[today-7].IP
  K/9_last7 = (K_last7 / IP_last7) * 9
  baseline_K/9 = K/9_last7
```

This is correct because:
- Cumulative totals diffed over a window give the **actual production in that window**
- IP-weighted rate derivation means a 35-IP starter influences K/9 more than a 5-IP bullpen arm
- The baseline naturally reflects the actual talent level of your current 12-team rostered pool, which shifts as teams make adds/drops

### What gets written to fbp-hub

`fbp-hub/data/league_baselines.json`:

```json
{
  "_generated": "2026-03-13",
  "_n_batters": 156,
  "_n_pitchers": 108,
  "batters": {
    "season": { "R": 30.9, "H": 58.3, "HR": 7.9, "RBI": 28.1, ... },
    "last30": { "R": 8.2,  "H": 16.1, ... },
    "last14": { "R": 3.9,  "H": 7.6,  ... },
    "last7":  { "R": 2.1,  "H": 3.8,  ... },
    "today":  { "R": 30.9, ... }
  },
  "pitchers": {
    "season": { "APP": 8.2, "ERA": 3.71, "K/9": 9.4, ... },
    "last30": { ... },
    "last14": { ... },
    "last7":  { ... },
    "today":  { ... }
  }
}
```

---

## Known stubs / future work

### TB (Total Bases)
Yahoo doesn't expose Total Bases as a clean stat ID via the players endpoint. It's stubbed at `0.0` in both batter and pitcher baselines, which means TB cells will not get FBP+ treatment (they'll be skipped since baseline is 0). To fix this once you identify the correct Yahoo stat ID for your league:

1. Add it to `BATTER_STAT_IDS` in `calculate_baselines.py`
2. Add the accumulation line in `aggregate_batter_totals()`
3. Add the per-player division in `batter_baseline_from_totals()`

### Stat ID verification
The Yahoo stat IDs in the script are standard MLB IDs but should be verified against your specific league's stat configuration. To check: use the Yahoo API's league stat categories endpoint:

```
GET https://fantasysports.yahooapis.com/fantasy/v2/league/469.l.15505/stat_categories
```

Compare the returned IDs against the constants in `BATTER_STAT_IDS` and `PITCHER_STAT_IDS`.

### "Today (live)" baseline
The `today` timeframe uses season cumulative stats, which is identical to `season`. Truly live "today only" stats would require a separate API call with `type=date&date=YYYY-MM-DD`, which Yahoo exposes but rate-limits aggressively. The current approach is the right tradeoff for a daily pipeline.

---

## Troubleshooting

**FBP+ button doesn't appear**
- `league_baselines.json` doesn't exist yet in fbp-hub, or the GitHub raw URL isn't resolving
- Check browser console for `FBP Extension: league_baselines.json not found`
- Manually push a `league_baselines.json` to fbp-hub to unblock while the pipeline catches up

**FBP+ button appears but cells don't change**
- Yahoo updated their table class names — check `table.Table` selector in `applyFBPPlus()`
- Open console, toggle FBP+ on, look for errors

**Windowed baselines all same as season**
- Normal for first 30 days — no historical snapshots exist yet to diff against
- Check `email-digest/data/baselines/` to confirm snapshots are accumulating

**Yahoo API stat fetch returns empty**
- Token may be expired — check `token.json` and re-run `get_token.py`
- Game key `469` may need updating for 2027 season
- Verify with: `GET https://fantasysports.yahooapis.com/fantasy/v2/game/469`
