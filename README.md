# Trade Review Dashboard

A Streamlit dashboard + monthly review system for a US equity swing trader using **Robinhood + Tradezella**. It does three things:

1. **Auto-merges** monthly Tradezella exports into a single multi-month view, handling open positions correctly across snapshots.
2. **Computes everything** — P&L, R-multiples, profit factor, drawdown, MAE/MFE-based execution leak detection, post-loss/post-win behavior, streaks, instrument/duration/setup breakdowns.
3. **Generates AI commentary** per month and month-over-month via Bedrock (Claude Sonnet 4), so each review has a written verdict, what-worked / what-didn't, biggest leak, behavioral observations, and a 30-day action plan.

---

## Quick start

```bash
cd ~/workspaces/trade-review
pip install -r requirements.txt
streamlit run app.py
```

Then drop your Tradezella exports into `csv-exports/` (one per month, named `YYYY-MM.csv`). The dashboard auto-loads everything in that directory on each page refresh.

---

## How it works

### Folder layout

```
trade-review/
├── app.py                      ← Streamlit dashboard entry point
├── requirements.txt
├── CLAUDE.md                   ← System prompt for the monthly-review agent
├── src/
│   ├── data.py                 ← Tradezella CSV loader + snapshot-aware merge
│   ├── metrics.py              ← All stats computations
│   └── ai_insights.py          ← Bedrock-backed analysis with disk caching
├── csv-exports/                ← drop YYYY-MM.csv files here
├── reports/                    ← markdown monthly reviews land here
├── ledgers/                    ← compounding ledger per month
└── insights/                   ← Bedrock JSON cache (auto-managed)
```

### Multi-month upload behavior

Each Tradezella CSV is treated as a **point-in-time snapshot** of your journal. The system reconciles snapshots without double-counting:

| Scenario | What happens |
|---|---|
| Closed slice in April CSV | Counted in April. Future snapshots don't change it. |
| Open position in April CSV, still open in May CSV | May's row wins (updated quantity, P&L). April's stale OPEN is dropped. |
| Open position in April CSV, fully closed in May CSV | May's closed slice(s) are counted in May. April's OPEN is dropped. |
| Position opened *and* trimmed within a month | Each closed slice is its own row; remaining open shares are one row. |

**Concrete example.** April CSV shows XYZ as OPEN with 100 shares, +$1,500 unrealized. In May, you trim 40 shares for +$600, leaving 60 still open at +$2,000. After uploading both:
- 40-share May slice → counted in May's realized P&L
- April closed slices → unchanged
- Remaining 60 open shares → unrealized strip, attributed to April's origination

See `CLAUDE.md` for the full design rationale.

### Month attribution

Each row carries three month columns:
- `originating_month` — when the position was entered (always from Open Date)
- `closing_month` — when P&L was booked (Close Date for closed; null for open)
- `month` — the canonical view: closing_month for closed trades, originating_month for open trades

The default Monthly review tab uses `month`. The Positions tab uses `originating_month` so April-opened conviction trades stay grouped under April even after they close in June.

---

## Dashboard tabs

### 1. Overview
- All-time KPIs (realized P&L, R, win rate, expectancy, profit factor, trade count)
- Amber unrealized strip (open positions, capital deployed, winners/losers, best/worst)
- Mark-to-market strip (realized + unrealized = "if I closed everything today")
- Equity curve in $ with dashed unrealized tail to today's date
- Cumulative R curve
- Open positions panel with giveback alerts
- Month-over-month stats table

### 2. Monthly review
- Same KPIs scoped to one month with deltas vs. prior month
- AI verdict (calls Bedrock; cached to `insights/YYYY-MM.json`)
- What worked / what didn't (specific tickers cited)
- Biggest execution leak with estimated R cost
- Behavioral observation (tilt patterns, post-loss behavior)
- Edge quality assessment
- Open positions commentary (sized appropriately? giving back gains?)
- 30-day action plan: 3 items, each with rationale, change, success metric, and tripwire
- Month-over-month trajectory analysis (growing/stable/eroding + drivers)

### 3. Charts
- Monthly P&L bar chart
- R-multiple distribution histogram
- Breakdowns by holding duration, instrument class, day-of-week, session, symbol

### 4. Execution
- MAE/MFE-based leak proxies:
  - **Held too long** — Loss closed but MFE was ≥ risk earlier (gave back a winner)
  - **Left on table** — Win with <30% exit efficiency and MFE ≥ 3R
  - **No stop set** — Trade Risk = 0 in journal
  - **Blew through stop** — MAE > planned risk
- Quality-filtered P&L (skip leak-flagged trades, see what's left)
- Streaks (max win, max loss, current)
- Post-loss / post-win behavior (next 3 trades after each)
- Risk distribution (min, median, mean, max, outliers)

### 5. Positions
- Position lifecycle view — each row is one originating trade rolled up across all slices
- Status: fully open (amber), partially closed (darker amber), fully closed
- Columns: original qty, closed qty, open qty, wins/losses/scratches, realized $, unrealized $, total $
- Filter by month → defaults to *originated in* that month

### 6. Trades
- Per-trade table (closed + OPEN with amber row highlight)
- Download filtered CSV
- Sortable columns including Zella score, MAE/MFE, exit efficiency

### Sidebar filters
- Month
- Instrument class (stock / option)
- Side (long / short)
- Closed-trade status (WIN / LOSS / BE) — does not affect open positions, by design

---

## Stat definitions

| Metric | Definition |
|---|---|
| **R-multiple** | `Net P&L / abs(Trade Risk)` per trade. `Trade Risk` comes from your Tradezella journal (per-trade dollar risk). |
| **Win rate** | Wins / (Wins + Losses). Scratches (BE) excluded from numerator and denominator. |
| **Expectancy ($)** | (win_rate × avg_win) + (loss_rate × avg_loss) |
| **Expectancy (R)** | Mean R across all closed trades |
| **Profit factor** | gross_wins / gross_losses |
| **Max drawdown** | Peak-to-trough on cumulative realized P&L curve |
| **Quality-filtered P&L** | What total P&L would have been if you skipped trades flagged with any leak |

### Leak proxies (MAE/MFE-based)

The system infers execution mistakes from Tradezella's MAE/MFE columns since the `Mistakes` tag is usually empty:

| Flag | Trigger |
|---|---|
| Held too long | `is_loss & MFE ≥ Trade Risk` — trade was a winner at some point but closed for a loss |
| Left on table | `is_win & Exit Efficiency < 30% & MFE ≥ 3 × Trade Risk` |
| No stop set | `Trade Risk == 0` |
| Blew through stop | `abs(MAE) > Trade Risk` — actual adverse excursion exceeded planned risk |

These are heuristics, not ground truth. They give you a tractable "this is probably a leak" signal without needing manual mistake tagging.

---

## Generating monthly reviews

When you ask Claude (in this directory) for a monthly review, it:

1. Reads the latest CSV in `csv-exports/`
2. Reads the prior ledger in `ledgers/`
3. Reads `baseline.json` (or asks you for it on month 1)
4. Runs the six-pillar framework defined in `CLAUDE.md`:
   - Performance snapshot
   - Edge quality analysis
   - Execution quality audit
   - Risk and position sizing review
   - Behavioural and psychological patterns
   - Strategic improvement ledger
5. Writes `reports/YYYY-MM.md` (the full report)
6. Writes `ledgers/YYYY-MM.md` (compounding ledger for next month)

The four outputs in each review:
- **Stats dashboard** (one-screen card with overall grade A–F)
- **Full diagnostic report** (1,200–1,800 word prose deep-dive)
- **30-day action plan** (3 items with tripwires)
- **Compounding ledger** (edge inventory, behavioral pattern log, rule book, quarterly trajectory)

---

## AI insights

Powered by Claude Sonnet 4 via AWS Bedrock (`us.anthropic.claude-sonnet-4-20250514-v1:0` in `us-west-2`). Uses your default AWS credential chain.

- Cached to `insights/YYYY-MM.json` and `insights/mom_PREV_to_CURR.json`
- "Regenerate" button per month if you want fresh analysis
- Prompts include: headline metrics, leak counts, post-loss behavior, risk distribution, setup breakdown, holding/dow/instrument breakdowns, top 5 winners/losers, and (when present) per-position open-position detail with giveback %

---

## Conventions

### File naming

```
csv-exports/2026-04.csv          ← end-of-April snapshot
csv-exports/2026-05.csv          ← end-of-May snapshot
csv-exports/2026-05-15.csv       ← mid-May snapshot (full date wins)
```

The filename's date is parsed as the snapshot's as-of date. Whichever filename has the latest date wins for open-position state.

### Tradezella column dependencies

Required: `Symbol`, `Open Date`, `Open Time`, `Close Date`, `Close Time`, `Quantity`, `Net P&L`, `Trade Risk`, `Status`, `Side`, `Spread Type`.

Optional but recommended: `Position MAE`, `Position MFE`, `Exit Efficiency`, `Realized RR`, `Zella Score`, `Playbook`, `Mistakes`, `Reviewed`.

When `Playbook` is empty (the common case), Pillar 2 (edge by setup) becomes dialogue-based — Claude will ask you to tag winners and losers in the conversation.

---

## Troubleshooting

**"No CSVs in csv-exports/"** → drop a Tradezella export there.

**Open positions don't appear** → the status filter only affects closed trades; opens always pass through. If you still don't see them, check that the `Status` column in your CSV says `OPEN` (case-insensitive).

**Stale AI insights** → click "Regenerate AI insights for this month" on the Monthly review tab. Cache is at `insights/YYYY-MM.json`.

**Bedrock auth fails** → refresh your AWS credentials (e.g. `aws sso login`) and reload the page.

**Dashboard loads slowly** → loading is cached via `@st.cache_data`. First load reads every CSV; subsequent navigation is instant. Drop a new CSV → restart streamlit (or click hamburger → Rerun).

---

## Tech stack

- **Streamlit** for UI
- **Pandas** + **NumPy** for compute
- **Plotly** for charts
- **Anthropic SDK (Bedrock)** for AI commentary
- **Python 3.12** (uses `match`/`|` types, won't work on 3.9)
