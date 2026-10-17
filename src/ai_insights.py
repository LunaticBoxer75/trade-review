"""Bedrock-backed AI commentary. Caches to disk so it runs once per month, not per page load."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
INSIGHTS_DIR = ROOT / "insights"
INSIGHTS_DIR.mkdir(exist_ok=True)

MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
REGION = "us-west-2"


def _client():
    from anthropic import AnthropicBedrock
    return AnthropicBedrock(aws_region=REGION)


def _build_month_prompt(month: str, headline: dict, leaks: dict, post_loss: dict,
                        risk_dist: dict, by_setup_df: pd.DataFrame,
                        by_holding_df: pd.DataFrame, by_dow_df: pd.DataFrame,
                        by_instr_df: pd.DataFrame, top_winners: list[dict],
                        top_losers: list[dict],
                        unrealized: dict | None = None,
                        open_positions: list[dict] | None = None) -> str:
    open_block = ""
    if unrealized and unrealized.get("open_count", 0) > 0:
        open_block = f"""

OPEN POSITIONS AT MONTH-END (unrealized — NOT counted in realized P&L above):
Summary: {json.dumps(unrealized, indent=2, default=str)}

Per-position detail (sorted by current unrealized P&L):
{json.dumps(open_positions or [], indent=2, default=str)}

Important: For each open position, look at giveback_pct — if >40%, the trade gave
back most of its peak gains and is at risk of becoming a winner-turned-loser. Comment
on whether the trader is letting winners turn into losers, and whether any open
position is dangerously oversized vs. typical risk."""
    return f"""You are a swing-trading performance coach. Analyze this trader's {month} on Robinhood.
Be direct, specific, and reference exact tickers/numbers from the data. No generic advice.

HEADLINE METRICS (REALIZED — closed trades only):
{json.dumps(headline, indent=2, default=str)}

EXECUTION LEAK COUNTS (proxies inferred from MAE/MFE since user doesn't tag mistakes):
{json.dumps(leaks, indent=2, default=str)}

POST-LOSS / POST-WIN BEHAVIOR (next 3 trades after each loss/win):
{json.dumps(post_loss, indent=2, default=str)}

RISK DISTRIBUTION (per-trade $ risk from Tradezella):
{json.dumps(risk_dist, indent=2, default=str)}

BY SETUP (Playbook tag, '(untagged)' = user didn't tag):
{by_setup_df.to_string(index=False) if not by_setup_df.empty else 'no data'}

BY HOLDING DURATION:
{by_holding_df.to_string(index=False) if not by_holding_df.empty else 'no data'}

BY DAY-OF-WEEK:
{by_dow_df.to_string(index=False) if not by_dow_df.empty else 'no data'}

BY INSTRUMENT CLASS (stock vs option):
{by_instr_df.to_string(index=False) if not by_instr_df.empty else 'no data'}

TOP 5 WINNERS:
{json.dumps(top_winners, indent=2, default=str)}

TOP 5 LOSERS:
{json.dumps(top_losers, indent=2, default=str)}{open_block}

Return STRICT JSON with this shape:
{{
  "headline_verdict": "2-3 sentence thesis on the month. Direct voice.",
  "what_worked": ["3-5 bullet points, each citing specific tickers/setups/numbers"],
  "what_didnt": ["3-5 bullet points, each citing specific tickers/setups/numbers"],
  "biggest_leak": {{"name": "...", "evidence": "...", "estimated_cost_R": 0.0}},
  "behavioral_observation": "One paragraph on tilt/streaks/post-loss patterns visible in the data",
  "edge_quality": "One paragraph on which instrument class / duration / setup is the actual edge",
  "open_positions_commentary": "One paragraph specifically on the open positions — sized appropriately? giving back gains? thesis still intact? (or null if no opens)",
  "top_3_actions": [
    {{"title": "Verb-first, max 10 words", "why": "specific finding cited", "change": "exact behavior change", "metric": "what to track", "tripwire": "early warning"}},
    {{...}}, {{...}}
  ]
}}

Output ONLY the JSON, no preamble."""


def _build_mom_prompt(curr_month: str, prev_month: str, curr: dict, prev: dict, mom: dict) -> str:
    return f"""You are comparing this trader's {curr_month} vs {prev_month}.
Be specific. Reference exact numbers.

CURRENT MONTH ({curr_month}):
{json.dumps(curr, indent=2, default=str)}

PRIOR MONTH ({prev_month}):
{json.dumps(prev, indent=2, default=str)}

MOM DELTAS (>=20% moves flagged):
{json.dumps(mom, indent=2, default=str)}

Return STRICT JSON:
{{
  "trajectory": "growing | stable | eroding",
  "primary_driver": "1 sentence on the dominant cause of the delta",
  "what_improved": ["specific bullets with numbers"],
  "what_degraded": ["specific bullets with numbers"],
  "concerning_trend": "the single most worrying movement (or null if none)",
  "encouraging_trend": "the single most encouraging movement (or null if none)"
}}

Output ONLY the JSON."""


def _call_bedrock(prompt: str, max_tokens: int = 2000) -> dict:
    try:
        client = _client()
        msg = client.messages.create(
            model=MODEL_ID,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)
    except Exception as e:
        return {"error": str(e), "raw": prompt[:200]}


def get_month_insights(month: str, df: pd.DataFrame, force: bool = False) -> dict:
    cache = INSIGHTS_DIR / f"{month}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text())

    from . import metrics
    sub = df[df["month"] == month]
    if len(sub) == 0:
        return {"error": f"no data for {month}"}

    closed = sub[sub["is_closed"]]
    top_w = closed.nlargest(5, "Net P&L")[["Symbol", "Net P&L", "R", "holding_days", "Status"]].to_dict("records")
    top_l = closed.nsmallest(5, "Net P&L")[["Symbol", "Net P&L", "R", "holding_days", "Status"]].to_dict("records")

    open_detail_df = metrics.open_positions_detail(sub)
    open_positions = open_detail_df.to_dict("records") if len(open_detail_df) else []

    prompt = _build_month_prompt(
        month=month,
        headline=metrics.headline(sub),
        leaks=metrics.execution_leaks(sub),
        post_loss=metrics.post_loss_behavior(sub),
        risk_dist=metrics.risk_distribution(sub),
        by_setup_df=metrics.by_setup(sub),
        by_holding_df=metrics.by_holding(sub),
        by_dow_df=metrics.by_dow(sub),
        by_instr_df=metrics.by_instrument_class(sub),
        top_winners=top_w,
        top_losers=top_l,
        unrealized=metrics.headline_unrealized(sub),
        open_positions=open_positions,
    )
    result = _call_bedrock(prompt, max_tokens=3000)
    cache.write_text(json.dumps(result, indent=2, default=str))
    return result


def get_mom_insights(curr_month: str, prev_month: str, df: pd.DataFrame, force: bool = False) -> dict:
    cache = INSIGHTS_DIR / f"mom_{prev_month}_to_{curr_month}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text())

    from . import metrics
    curr = metrics.headline(df[df["month"] == curr_month])
    prev = metrics.headline(df[df["month"] == prev_month])
    mom = metrics.mom_diff(curr, prev)
    prompt = _build_mom_prompt(curr_month, prev_month, curr, prev, mom)
    result = _call_bedrock(prompt, max_tokens=1500)
    cache.write_text(json.dumps(result, indent=2, default=str))
    return result
