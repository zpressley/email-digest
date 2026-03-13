// ============================================================
// FBP Hub Chrome Extension — content.js
// Phase 1: Prospect/Contract Badges
// Phase 2: FBP+ Table Hacker
// ============================================================

// --- Data URLs ---
const COMBINED_PLAYERS_URL   = "https://raw.githubusercontent.com/zpressley/FBPTradeBot/refs/heads/main/data/combined_players.json";
const UPID_DATABASE_URL      = "https://raw.githubusercontent.com/zpressley/FBPTradeBot/refs/heads/main/data/upid_database.json";
const TEAM_COLORS_URL        = "https://raw.githubusercontent.com/zpressley/fbp-hub/refs/heads/main/data/team_colors.json";
const LEAGUE_BASELINES_URL   = "https://raw.githubusercontent.com/zpressley/fbp-hub/refs/heads/main/data/league_baselines.json";

// --- State ---
let playerData     = {};
let teamColors     = {};
let leagueBaselines = {};         // { batters: { season: {R,HR,...}, last30: {...}, ... }, pitchers: {...} }
let fbpPlusActive  = false;       // Is FBP+ mode currently on?
let rawCellCache   = new Map();   // td element → original raw text, so we can toggle back

// ============================================================
// UTILITIES
// ============================================================

function getContrastYIQ(hexcolor) {
    if (!hexcolor) return '#FFFFFF';
    hexcolor = hexcolor.replace("#", "");
    const r = parseInt(hexcolor.substr(0,2),16);
    const g = parseInt(hexcolor.substr(2,2),16);
    const b = parseInt(hexcolor.substr(4,2),16);
    const yiq = ((r*299)+(g*587)+(b*114))/1000;
    return (yiq >= 128) ? '#111111' : '#FFFFFF';
}

function normalizeName(name) {
    if (!name) return "";
    return name
        .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-zA-Z\s]/g, "")
        .replace(/\b(Jr|Sr|II|III)\b/ig, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
}

// ============================================================
// DATA LOADING
// ============================================================

async function loadData() {
    try {
        console.log("FBP Extension: Fetching databases...");

        const fetches = [
            fetch(COMBINED_PLAYERS_URL),
            fetch(UPID_DATABASE_URL),
            fetch(TEAM_COLORS_URL),
            fetch(LEAGUE_BASELINES_URL).catch(() => null)  // graceful — baselines optional
        ];

        const [combinedRes, upidRes, colorsRes, baselinesRes] = await Promise.all(fetches);

        const combinedPlayers  = await combinedRes.json();
        const upidDatabase     = await upidRes.json();
        teamColors             = await colorsRes.json();

        if (baselinesRes && baselinesRes.ok) {
            leagueBaselines = await baselinesRes.json();
            console.log("FBP Extension: League baselines loaded ✓");
        } else {
            console.warn("FBP Extension: league_baselines.json not found — FBP+ will be unavailable.");
        }

        // --- Build playerData map ---
        const ownedUpids = {};

        combinedPlayers.forEach(p => {
            if (p.FBP_Team && p.FBP_Team.trim() !== "") {
                const profile = {
                    team:     p.FBP_Team.trim(),
                    type:     p.player_type     ? p.player_type.trim().toUpperCase()     : "",
                    years:    p.years_simple    ? p.years_simple.trim().toUpperCase()    : "",
                    status:   p.status          ? p.status.trim().toUpperCase()          : "",
                    contract: p.contract_type   ? p.contract_type.trim().toUpperCase()  : ""
                };
                if (p.name)  playerData[normalizeName(p.name)] = profile;
                if (p.upid)  ownedUpids[p.upid] = profile;
            }
        });

        const byUpidData = upidDatabase.by_upid || {};
        for (const [upid, data] of Object.entries(byUpidData)) {
            if (ownedUpids[upid] && data.alt_names) {
                data.alt_names.forEach(alt => {
                    playerData[normalizeName(alt)] = ownedUpids[upid];
                });
            }
        }

        console.log(`FBP Extension: ${Object.keys(playerData).length} name variants mapped.`);

        scanPageForPlayers();
        injectFBPPlusToggle();

    } catch (err) {
        console.error("FBP Extension: Failed to load data.", err);
    }
}

// ============================================================
// PHASE 1 — BADGE INJECTION
// ============================================================

function scanPageForPlayers() {
    document.querySelectorAll('a.name').forEach(el => {
        if (el.parentNode.querySelector('.fbp-badge-container')) return;

        const cleanName = normalizeName(el.textContent);
        if (!playerData[cleanName]) return;

        const profile = playerData[cleanName];
        const colors  = teamColors[profile.team];
        const container = document.createElement('span');
        container.className = 'fbp-badge-container';

        // Normalize years_simple display
        let displayYears = profile.years;
        if (displayYears === "TCR")  displayYears = "TC R";
        if (displayYears === "TC1")  displayYears = "TC 1";
        if (!displayYears && profile.status) {
            if (profile.status.includes("TCR") || profile.status.includes("TC R")) displayYears = "TC R";
            if (profile.status.includes("TC1") || profile.status.includes("TC 1")) displayYears = "TC 1";
        }

        if (profile.type === "FARM") {
            // Team badge
            const teamBadge = document.createElement('span');
            teamBadge.className = 'fbp-badge';
            teamBadge.textContent = profile.team;
            if (colors?.primary) {
                teamBadge.style.backgroundColor = colors.primary;
                teamBadge.style.color = getContrastYIQ(colors.primary);
                if (colors.secondary) {
                    teamBadge.style.border = `1px solid ${colors.secondary}`;
                    teamBadge.style.padding = '1px 5px';
                }
            }
            container.appendChild(teamBadge);

            // Contract badge
            let contractText = "", contractClass = "";
            if (profile.contract.includes("PURCHASED"))    { contractText = "PC"; contractClass = "fbp-contract-pc"; }
            else if (profile.contract.includes("DEVELOPMENT")) { contractText = "DC"; contractClass = "fbp-contract-dc"; }
            else if (profile.contract.includes("BLUE CHIP"))   { contractText = "BC"; contractClass = "fbp-contract-bc"; }
            if (!contractText && displayYears && displayYears !== "TC 1") {
                contractText = displayYears; contractClass = "fbp-contract-mlb";
            }
            if (contractText) {
                const cb = document.createElement('span');
                cb.className = `fbp-badge ${contractClass}`;
                cb.textContent = contractText;
                container.appendChild(cb);
            }

        } else if (profile.type === "MLB") {
            if (displayYears && displayYears !== "TC 1") {
                const mlbBadge = document.createElement('span');
                mlbBadge.className = 'fbp-badge fbp-contract-mlb';
                mlbBadge.textContent = displayYears;
                container.appendChild(mlbBadge);
            }
        }

        if (container.hasChildNodes()) {
            el.parentNode.insertBefore(container, el.nextSibling);
        }
    });
}

// ============================================================
// PHASE 2 — FBP+ TABLE HACKER
// ============================================================

// --- FBP+ Toggle Button ---

function injectFBPPlusToggle() {
    // Don't double-inject
    if (document.getElementById('fbp-plus-toggle')) return;
    // Don't inject if no baselines loaded
    if (!leagueBaselines || Object.keys(leagueBaselines).length === 0) return;

    // Find a good anchor — Yahoo's stat view controls area
    // We try multiple selectors to handle different Yahoo page layouts
    const anchors = [
        document.querySelector('.stat-categories'),
        document.querySelector('#statmodetabs'),
        document.querySelector('.Pos-selector'),
        document.querySelector('.Table2-header-row th:first-child'),
        document.querySelector('.Ptable-header'),
        document.querySelector('#players-table'),
    ].filter(Boolean);

    const anchor = anchors[0];
    if (!anchor) {
        // Retry after next mutation picks it up
        return;
    }

    const btn = document.createElement('button');
    btn.id = 'fbp-plus-toggle';
    btn.className = 'fbp-plus-btn';
    btn.innerHTML = `
        <span class="fbp-plus-icon">⚡</span>
        <span class="fbp-plus-label">FBP+</span>
        <span class="fbp-plus-state">OFF</span>
    `;
    btn.title = "Toggle FBP+ stat heatmaps (100 = league average for your 12-team format)";

    btn.addEventListener('click', () => {
        fbpPlusActive = !fbpPlusActive;
        btn.classList.toggle('fbp-plus-btn--active', fbpPlusActive);
        btn.querySelector('.fbp-plus-state').textContent = fbpPlusActive ? 'ON' : 'OFF';

        if (fbpPlusActive) {
            applyFBPPlus();
        } else {
            revertFBPPlus();
        }
    });

    // Insert before the anchor element
    anchor.parentNode.insertBefore(btn, anchor);
    console.log("FBP Extension: FBP+ toggle injected.");
}

// --- Detect active timeframe from Yahoo's tab UI ---

function detectActiveTimeframe() {
    // Yahoo timeframe tabs: "Today (live)", "Last 7 Days", "Last 14 Days",
    // "Last 30 Days", "Season (total)"
    const activeTab = document.querySelector('[class*="statmodetab"][class*="selected"], [class*="stat-tab"][class*="active"], .Fz-sm.Fw-b');
    if (!activeTab) return 'season';
    const text = activeTab.textContent.trim().toLowerCase();
    if (text.includes('today') || text.includes('live')) return 'today';
    if (text.includes('30'))  return 'last30';
    if (text.includes('14'))  return 'last14';
    if (text.includes('7'))   return 'last7';
    return 'season';
}

// --- Detect batter vs pitcher table ---

function detectTableType(headerCells) {
    const headers = Array.from(headerCells).map(th => th.textContent.trim().toUpperCase());
    // ERA, APP, K/9, H/9, BB/9, QS = pitcher table
    if (headers.some(h => h === 'ERA' || h === 'APP' || h === 'K/9' || h === 'H/9' || h === 'BB/9' || h === 'QS')) return 'pitchers';
    return 'batters';
}

// --- Core FBP+ math ---
// Returns null if we can't compute (missing baseline or non-numeric cell)

// Stats where LOWER is better — split by table type because HR and BB
// mean opposite things for batters vs pitchers.
// Pitcher inverted: ER, ERA, HR (allowed), H/9, BB/9, TB (allowed)
// Batter inverted: K (strikeouts are bad for batters)
const INVERTED_PITCHER = new Set(['ER', 'ERA', 'HR', 'H/9', 'BB/9', 'TB']);

function computeFBPPlus(statKey, rawValue, tableType, timeframe) {
    const pool = leagueBaselines[tableType]?.[timeframe];
    if (!pool) return null;
    const baseline = pool[statKey];
    if (baseline == null || baseline === 0) return null;
    const val = parseFloat(rawValue.replace(/[^0-9.\-]/g, ''));
    if (isNaN(val)) return null;

    const isInverted = tableType === 'pitchers'
        ? INVERTED_PITCHER.has(statKey)
        : statKey === 'K';  // only strikeouts are bad for batters

    if (isInverted) {
        return Math.round((baseline / val) * 100);
    }
    return Math.round((val / baseline) * 100);
}

// --- Heatmap CSS class ---

function fbpPlusClass(plusVal) {
    if (plusVal === null) return '';
    if (plusVal >= 160) return 'fbp-plus-elite';
    if (plusVal >= 130) return 'fbp-plus-great';
    if (plusVal >= 115) return 'fbp-plus-above';
    if (plusVal >= 85)  return 'fbp-plus-avg';
    if (plusVal >= 70)  return 'fbp-plus-below';
    return 'fbp-plus-poor';
}

// --- Apply FBP+ to all visible stat tables ---

function applyFBPPlus() {
    const tables = document.querySelectorAll('table.Table');
    const timeframe = detectActiveTimeframe();

    tables.forEach(table => {
        const headerRow = table.querySelector('thead tr, tr.Table2-header-row');
        if (!headerRow) return;

        const headerCells = headerRow.querySelectorAll('th');
        const tableType   = detectTableType(headerCells);
        const statKeys    = Array.from(headerCells).map(th => th.textContent.trim().toUpperCase());

        // Walk every data row
        const dataRows = table.querySelectorAll('tbody tr');
        dataRows.forEach(row => {
            const cells = row.querySelectorAll('td');
            cells.forEach((td, i) => {
                const statKey = statKeys[i];
                if (!statKey) return;

                // Skip non-stat columns (name, team, pos, ownership)
                const skip = new Set(['PLAYER', 'NAME', 'TEAM', 'POS', '%OWN', 'GP', '']);
                if (skip.has(statKey)) return;

                const rawText = td.textContent.trim();
                if (!rawText || rawText === '-' || rawText === '*') return;

                const plusVal = computeFBPPlus(statKey, rawText, tableType, timeframe);
                if (plusVal === null) return;

                // Cache raw value before first replacement
                if (!rawCellCache.has(td)) {
                    rawCellCache.set(td, { text: rawText, className: td.className });
                }

                td.textContent = plusVal;
                td.classList.add('fbp-plus-cell', fbpPlusClass(plusVal));
                td.title = `Raw: ${rawText} | FBP+: ${plusVal} (100 = league avg)`;
            });
        });
    });
}

// --- Revert all cells back to raw stats ---

function revertFBPPlus() {
    rawCellCache.forEach((cached, td) => {
        td.textContent = cached.text;
        td.className   = cached.className;
        td.title       = '';
    });
    rawCellCache.clear();
}

// ============================================================
// MUTATION OBSERVER — watch for Yahoo dynamic loads
// ============================================================

const observer = new MutationObserver(() => {
    clearTimeout(window.fbpScanTimeout);
    window.fbpScanTimeout = setTimeout(() => {
        scanPageForPlayers();
        // Re-inject toggle if Yahoo re-rendered the toolbar
        if (!document.getElementById('fbp-plus-toggle')) {
            injectFBPPlusToggle();
        }
        // If FBP+ is active and Yahoo loaded new table data, re-apply
        if (fbpPlusActive) {
            revertFBPPlus();
            applyFBPPlus();
        }
    }, 400);
});

observer.observe(document.body, { childList: true, subtree: true });

// ============================================================
// BOOT
// ============================================================
loadData();
