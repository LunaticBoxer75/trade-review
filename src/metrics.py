"""All trading-stat computations. Pure functions, take a dataframe, return numbers/dicts."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _safe(x, default=0.0):
    return default if (x is None or pd.isna(x)) else x


def closed_only(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["is_closed"]].copy()


def winloss_only(df: pd.DataFrame) -> pd.DataFrame:
    """Exclude scratches from win-rate math."""
    return df[df["is_win"] | df["is_loss"]].copy()


def headline(df: pd.DataFrame) -> dict:
    closed = closed_only(df)
    wl = winloss_only(df)
    if len(closed) == 0:
        return {
            "trades": 0, "wins": 0, "losses": 0, "scratches": 0, "open": int(df["is_open"].sum()),
            "win_rate": 0.0, "net_pnl": 0.0, "gross_wins": 0.0, "gross_losses": 0.0,
            "profit_factor": 0.0, "expectancy_dollar": 0.0, "expectancy_R": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "avg_win_loss_ratio": 0.0, "net_R": 0.0,
            "largest_win": 0.0, "largest_loss": 0.0,
            "max_drawdown_dollar": 0.0, "max_drawdown_R": 0.0,
            "avg_holding_days": 0.0, "total_fees": 0.0,
        }
    wins = wl[wl["is_win"]]
    losses = wl[wl["is_loss"]]
    gross_wins = float(wins["Net P&L"].sum()) if len(wins) else 0.0
    gross_losses = abs(float(losses["Net P&L"].sum())) if len(losses) else 0.0
    avg_win = float(wins["Net P&L"].mean()) if len(wins) else 0.0
    avg_loss = float(losses["Net P&L"].mean()) if len(losses) else 0.0
    win_rate = (len(wins) / len(wl)) if len(wl) else 0.0
    expectancy_dollar = (win_rate * avg_win) + ((1 - win_rate) * avg_loss) if len(wl) else 0.0
    R_series = wl["R"].dropna()
    expectancy_R = float(R_series.mean()) if len(R_series) else 0.0

    cum = closed["Net P&L"].cumsum()
    drawdown_dollar = (cum - cum.cummax()).min()
    cum_R = closed["R"].fillna(0).cumsum()
    drawdown_R = (cum_R - cum_R.cummax()).min()

    return {
        "trades": int(len(closed)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "scratches": int(closed["is_scratch"].sum()),
        "open": int(df["is_open"].sum()),
        "win_rate": float(win_rate),
        "net_pnl": float(closed["Net P&L"].sum()),
        "gross_wins": gross_wins,
        "gross_losses": gross_losses,
        "profit_factor": (gross_wins / gross_losses) if gross_losses > 0 else float("inf") if gross_wins > 0 else 0.0,
        "expectancy_dollar": float(expectancy_dollar),
        "expectancy_R": float(expectancy_R),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_win_loss_ratio": (abs(avg_win / avg_loss)) if avg_loss else 0.0,
        "net_R": float(closed["R"].fillna(0).sum()),
        "largest_win": float(wins["Net P&L"].max()) if len(wins) else 0.0,
        "largest_loss": float(losses["Net P&L"].min()) if len(losses) else 0.0,
        "max_drawdown_dollar": float(drawdown_dollar) if not pd.isna(drawdown_dollar) else 0.0,
        "max_drawdown_R": float(drawdown_R) if not pd.isna(drawdown_R) else 0.0,
        "avg_holding_days": float(closed["holding_days"].mean()) if len(closed) else 0.0,
        "total_fees": float(closed["Fee"].fillna(0).sum()),
    }


def headline_unrealized(df: pd.DataFrame) -> dict:
    """Stats for OPEN positions only. Their P&L is unrealized; R is undefined
    (Tradezella's Trade Risk on open rows is running MAE, not stated risk)."""
    opens = df[df["is_open"]].copy()
    if len(opens) == 0:
        return {
            "open_count": 0, "unrealized_pnl": 0.0,
            "unrealized_winners": 0, "unrealized_losers": 0,
            "best_open_pnl": 0.0, "worst_open_pnl": 0.0,
            "avg_holding_days": 0.0,
            "total_capital_at_risk": 0.0,
        }
    pnl = opens["Net P&L"].fillna(0)
    cost = opens["Adjusted Cost"].fillna(0).abs()
    return {
        "open_count": int(len(opens)),
        "unrealized_pnl": float(pnl.sum()),
        "unrealized_winners": int((pnl > 0).sum()),
        "unrealized_losers": int((pnl < 0).sum()),
        "best_open_pnl": float(pnl.max()),
        "worst_open_pnl": float(pnl.min()),
        "avg_holding_days": float(opens["holding_days"].mean()),
        "total_capital_at_risk": float(cost.sum()),
    }


def combined_headline(df: pd.DataFrame) -> dict:
    """Realized P&L + unrealized P&L = mark-to-market 'if I closed everything today'.
    Used for the third KPI strip when there are open positions."""
    realized = headline(df)
    unrealized = headline_unrealized(df)
    return {
        "realized_pnl": realized["net_pnl"],
        "unrealized_pnl": unrealized["unrealized_pnl"],
        "mtm_pnl": realized["net_pnl"] + unrealized["unrealized_pnl"],
        "realized_R": realized["net_R"],
    }


def open_positions_detail(df: pd.DataFrame) -> pd.DataFrame:
    """Per-row table for the Open Positions panel. Includes 'giving back gains?' flag.

    Computes side-aware Entry/Mark prices: for LONG the entry is Avg Buy Price
    and the mark is Avg Sell Price; for SHORT it's reversed. This avoids the
    misleading 'Avg buy / Avg mark' labels for shorts, where the meanings would
    swap and lead a quick read astray.
    """
    opens = df[df["is_open"]].copy()
    if len(opens) == 0:
        return pd.DataFrame()

    pnl = opens["Net P&L"].fillna(0)
    mfe_raw = opens["Position MFE"]
    cost = opens["Adjusted Cost"].fillna(0).abs()

    is_short = opens["Side"].astype(str).str.lower() == "short"
    opens["entry_price"] = np.where(is_short, opens["Avg Sell Price"], opens["Avg Buy Price"])
    opens["mark_price"] = np.where(is_short, opens["Avg Buy Price"], opens["Avg Sell Price"])

    # giveback = how much of peak unrealized gain has been surrendered.
    # Use NaN (not 0) when MFE is missing so missing-peak rows are visually
    # distinguishable from actual zero-giveback rows.
    mfe = mfe_raw.where(mfe_raw.notna() & (mfe_raw > 0))
    opens["giveback_dollars"] = (mfe - pnl).where(mfe.notna())
    opens["giveback_pct"] = (opens["giveback_dollars"] / mfe * 100)
    opens["pct_return"] = (pnl / cost.replace(0, np.nan) * 100)
    opens["status_chip"] = pnl.apply(lambda v: "🟢 winning" if v > 0 else ("🔴 losing" if v < 0 else "⚪ flat"))

    cols = [
        "Symbol", "Side", "instrument_class", "Quantity",
        "entry_price", "mark_price",
        "holding_days", "Position MFE", "Position MAE",
        "Net P&L", "pct_return", "giveback_dollars", "giveback_pct",
        "Adjusted Cost", "status_chip"
    ]
    cols = [c for c in cols if c in opens.columns]
    return opens[cols].sort_values("Net P&L", ascending=False).reset_index(drop=True)


def position_lifecycle(df: pd.DataFrame) -> pd.DataFrame:
    """Group every trade row by originating identity (Symbol, Open Date, Open Time).

    A single XYZ position opened in April might appear here as ONE row that
    rolls up: 3 closed slices (one win, two losses) + 1 still-open row with 100
    shares. This is the lens that lets you see whether April-opened conviction
    trades worked out across multiple months.

    Returns one row per origination with:
      origination_date, symbol, side, total_slices, closed_slices, open_qty,
      total_realized_pnl, unrealized_pnl, total_pnl, status, latest_activity
    """
    if len(df) == 0:
        return pd.DataFrame()
    work = df.copy()
    # Use 'unknown' as a fallback for missing Open Time so the groupby doesn't
    # silently drop rows.
    work["_open_time"] = work["Open Time"].fillna("unknown")
    work["_open_date"] = work["Open Date"].fillna("unknown")
    grouped = work.groupby(["Symbol", "_open_date", "_open_time"], dropna=False)

    rows = []
    for (sym, open_date, open_time), g in grouped:
        closed = g[g["is_closed"]]
        opens = g[g["is_open"]]
        realized = float(closed["Net P&L"].sum()) if len(closed) else 0.0
        unrealized = float(opens["Net P&L"].sum()) if len(opens) else 0.0
        # Original size = sum of closed slice quantities + remaining open quantity
        closed_qty = float(closed["Quantity"].sum()) if len(closed) else 0.0
        open_qty = float(opens["Quantity"].sum()) if len(opens) else 0.0
        original_qty = closed_qty + open_qty

        if len(opens) and not len(closed):
            status = "fully open"
        elif len(opens) and len(closed):
            status = "partially closed"
        elif not len(opens) and len(closed):
            status = "fully closed"
        else:
            status = "unknown"

        # Wins/losses among closed slices
        wins = int((closed["is_win"]).sum()) if len(closed) else 0
        losses = int((closed["is_loss"]).sum()) if len(closed) else 0
        scratches = int((closed["is_scratch"]).sum()) if len(closed) else 0
        latest_activity = max(
            [d for d in [closed["close_dt"].max() if len(closed) else None,
                          opens["snapshot_date"].max() if len(opens) and "snapshot_date" in opens else None]
             if d is not None and pd.notna(d)],
            default=pd.NaT)

        rows.append({
            "origination_date": open_date,
            "symbol": sym,
            "side": g["Side"].iloc[0],
            "instrument_class": g["instrument_class"].iloc[0],
            "originating_month": g["originating_month"].iloc[0]
                if "originating_month" in g.columns else open_date[:7],
            "original_qty": original_qty,
            "closed_qty": closed_qty,
            "open_qty": open_qty,
            "slices": len(g),
            "closed_slices": len(closed),
            "wins": wins,
            "losses": losses,
            "scratches": scratches,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": realized + unrealized,
            "status": status,
            "latest_activity": latest_activity,
        })

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["status", "total_pnl"], ascending=[True, False]).reset_index(drop=True)
    return out


def streaks(df: pd.DataFrame) -> dict:
    wl = winloss_only(df).sort_values("close_dt")
    if len(wl) == 0:
        return {"max_win_streak": 0, "max_loss_streak": 0, "current_streak": 0, "current_streak_type": "none"}
    seq = wl["is_win"].astype(int).values
    max_w = max_l = 0
    cur_w = cur_l = 0
    for s in seq:
        if s == 1:
            cur_w += 1; cur_l = 0
            max_w = max(max_w, cur_w)
        else:
            cur_l += 1; cur_w = 0
            max_l = max(max_l, cur_l)
    last = seq[-1]
    return {
        "max_win_streak": int(max_w),
        "max_loss_streak": int(max_l),
        "current_streak": int(cur_w if last == 1 else cur_l),
        "current_streak_type": "win" if last == 1 else "loss",
    }


def by_instrument_class(df: pd.DataFrame) -> pd.DataFrame:
    closed = closed_only(df)
    if len(closed) == 0:
        return pd.DataFrame()
    g = closed.groupby("instrument_class").agg(
        trades=("Net P&L", "size"),
        net_pnl=("Net P&L", "sum"),
        net_R=("R", lambda s: s.fillna(0).sum()),
        win_rate=("is_win", lambda s: s.sum() / max((s | closed.loc[s.index, "is_loss"]).sum(), 1)),
        avg_R=("R", "mean"),
    ).reset_index()
    return g


def by_setup(df: pd.DataFrame) -> pd.DataFrame:
    closed = closed_only(df)
    if len(closed) == 0:
        return pd.DataFrame()
    closed = closed.copy()
    closed["Playbook"] = closed["Playbook"].fillna("").replace("", "(untagged)")
    g = closed.groupby("Playbook").agg(
        trades=("Net P&L", "size"),
        net_pnl=("Net P&L", "sum"),
        net_R=("R", lambda s: s.fillna(0).sum()),
        avg_R=("R", "mean"),
        win_rate=("is_win", "mean"),
    ).reset_index().sort_values("net_pnl", ascending=False)
    return g


def by_holding(df: pd.DataFrame) -> pd.DataFrame:
    closed = closed_only(df)
    if len(closed) == 0:
        return pd.DataFrame()
    g = closed.groupby("holding_bucket", observed=True).agg(
        trades=("Net P&L", "size"),
        net_pnl=("Net P&L", "sum"),
        net_R=("R", lambda s: s.fillna(0).sum()),
        avg_R=("R", "mean"),
        win_rate=("is_win", "mean"),
    ).reset_index()
    return g


def by_dow(df: pd.DataFrame) -> pd.DataFrame:
    closed = closed_only(df)
    if len(closed) == 0:
        return pd.DataFrame()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    g = closed.groupby("dow").agg(
        trades=("Net P&L", "size"),
        net_pnl=("Net P&L", "sum"),
        net_R=("R", lambda s: s.fillna(0).sum()),
        win_rate=("is_win", "mean"),
    ).reset_index()
    g["dow"] = pd.Categorical(g["dow"], categories=order, ordered=True)
    return g.sort_values("dow")


def by_session(df: pd.DataFrame) -> pd.DataFrame:
    closed = closed_only(df)
    if len(closed) == 0:
        return pd.DataFrame()
    g = closed.groupby("session", observed=True).agg(
        trades=("Net P&L", "size"),
        net_pnl=("Net P&L", "sum"),
        net_R=("R", lambda s: s.fillna(0).sum()),
        win_rate=("is_win", "mean"),
    ).reset_index()
    return g


def by_symbol(df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    closed = closed_only(df)
    if len(closed) == 0:
        return pd.DataFrame()
    g = closed.groupby("Symbol").agg(
        trades=("Net P&L", "size"),
        net_pnl=("Net P&L", "sum"),
        net_R=("R", lambda s: s.fillna(0).sum()),
        win_rate=("is_win", "mean"),
    ).reset_index().sort_values("net_pnl", ascending=False)
    if top_n:
        return pd.concat([g.head(top_n), g.tail(top_n)]).drop_duplicates()
    return g


def by_month(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) == 0:
        return pd.DataFrame()
    # Union of months from closed and open rows so brand-new months with only
    # open positions still appear in the MoM table.
    closed_months = set(df.loc[df["is_closed"], "month"].dropna())
    open_months = set(df.loc[df["is_open"], "month"].dropna())
    months = sorted(closed_months | open_months)
    if not months:
        return pd.DataFrame()
    rows = []
    for month in months:
        h = headline(df[df["month"] == month])
        h["month"] = month
        rows.append(h)
    return pd.DataFrame(rows).sort_values("month")


def post_loss_behavior(df: pd.DataFrame, window: int = 3) -> dict:
    wl = winloss_only(df).sort_values("close_dt").reset_index(drop=True)
    if len(wl) < window + 1:
        return {"trades_after_loss": 0, "win_rate_after_loss": 0.0, "avg_R_after_loss": 0.0,
                "trades_after_win": 0, "win_rate_after_win": 0.0, "avg_R_after_win": 0.0}
    after_loss_idx, after_win_idx = [], []
    for i, row in wl.iterrows():
        if row["is_loss"]:
            after_loss_idx.extend(range(i + 1, min(i + 1 + window, len(wl))))
        elif row["is_win"]:
            after_win_idx.extend(range(i + 1, min(i + 1 + window, len(wl))))
    after_loss_idx = sorted(set(after_loss_idx))
    after_win_idx = sorted(set(after_win_idx))
    al = wl.iloc[after_loss_idx] if after_loss_idx else wl.iloc[0:0]
    aw = wl.iloc[after_win_idx] if after_win_idx else wl.iloc[0:0]
    return {
        "trades_after_loss": len(al),
        "win_rate_after_loss": float(al["is_win"].mean()) if len(al) else 0.0,
        "avg_R_after_loss": float(al["R"].mean()) if len(al) else 0.0,
        "trades_after_win": len(aw),
        "win_rate_after_win": float(aw["is_win"].mean()) if len(aw) else 0.0,
        "avg_R_after_win": float(aw["R"].mean()) if len(aw) else 0.0,
    }


def execution_leaks(df: pd.DataFrame) -> dict:
    closed = closed_only(df)
    if len(closed) == 0:
        return {"held_too_long": 0, "left_on_table": 0, "no_stop": 0, "blew_stop": 0,
                "leak_dollars_held_too_long": 0.0}
    return {
        "held_too_long": int(closed["leak_held_too_long"].sum()),
        "left_on_table": int(closed["leak_left_on_table"].sum()),
        "no_stop": int(closed["leak_no_stop"].sum()),
        "blew_stop": int(closed["leak_blew_stop"].sum()),
        "leak_dollars_held_too_long": float(closed.loc[closed["leak_held_too_long"], "Net P&L"].sum()),
    }


def risk_distribution(df: pd.DataFrame) -> dict:
    closed = closed_only(df)
    risks = closed["risk_abs"].dropna()
    if len(risks) == 0:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0, "std": 0.0,
                "outlier_count": 0, "outlier_threshold": 0.0}
    median = float(risks.median())
    threshold = 2 * median
    return {
        "min": float(risks.min()), "max": float(risks.max()),
        "mean": float(risks.mean()), "median": median, "std": float(risks.std() or 0),
        "outlier_count": int((risks > threshold).sum()),
        "outlier_threshold": threshold,
    }


def quality_filtered_pnl(df: pd.DataFrame) -> dict:
    """What if you'd skipped trades flagged with leaks?"""
    closed = closed_only(df)
    if len(closed) == 0:
        return {"actual_pnl": 0.0, "filtered_pnl": 0.0, "delta": 0.0,
                "actual_R": 0.0, "filtered_R": 0.0}
    leak_mask = (
        closed["leak_held_too_long"] | closed["leak_left_on_table"]
        | closed["leak_no_stop"] | closed["leak_blew_stop"]
    )
    clean = closed[~leak_mask]
    actual_pnl = float(closed["Net P&L"].sum())
    filtered_pnl = float(clean["Net P&L"].sum())
    actual_R = float(closed["R"].fillna(0).sum())
    filtered_R = float(clean["R"].fillna(0).sum())
    return {
        "actual_pnl": actual_pnl, "filtered_pnl": filtered_pnl, "delta": filtered_pnl - actual_pnl,
        "actual_R": actual_R, "filtered_R": filtered_R,
    }


def mom_diff(curr: dict, prev: dict | None) -> dict:
    """Compare current month headline to previous. Returns deltas + flags."""
    if not prev:
        return {}
    out = {}
    for k in curr:
        if isinstance(curr[k], (int, float)) and isinstance(prev.get(k), (int, float)):
            delta = curr[k] - prev[k]
            pct = (delta / prev[k] * 100) if prev[k] not in (0, None) else None
            out[k] = {"delta": delta, "pct": pct,
                      "moved_20pct": pct is not None and abs(pct) >= 20}
    return out
