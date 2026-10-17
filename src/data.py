"""Tradezella CSV loader. Single source of truth for all dashboard data."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = ROOT / "csv-exports"


def _parse_open_dt(row) -> pd.Timestamp | None:
    d = row.get("Open Date")
    t = row.get("Open Time")
    if pd.isna(d):
        return None
    base = pd.to_datetime(d, errors="coerce")
    if pd.isna(base):
        return None
    if isinstance(t, str) and t.strip():
        m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})", t)
        if m:
            h, mi, s = map(int, m.groups())
            return base.replace(hour=h, minute=mi, second=s)
    return base


def _parse_close_dt(row) -> pd.Timestamp | None:
    d = row.get("Close Date")
    t = row.get("Close Time")
    if pd.isna(d):
        return None
    base = pd.to_datetime(d, errors="coerce")
    if pd.isna(base):
        return None
    if isinstance(t, str) and t.strip():
        m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})", t)
        if m:
            h, mi, s = map(int, m.groups())
            return base.replace(hour=h, minute=mi, second=s)
    return base


def _classify_instrument(row) -> str:
    spread = str(row.get("Spread Type", "")).strip().lower()
    instr = str(row.get("Instrument", "")).strip()
    if spread == "stock":
        return "stock"
    if spread in {"single", "vertical", "iron condor", "strangle", "straddle"}:
        return "option"
    if "PUT" in instr.upper() or "CALL" in instr.upper():
        return "option"
    return spread or "stock"


_OPTION_RE = re.compile(r"^(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(\d+(?:\.\d+)?)\s+(PUT|CALL)$", re.IGNORECASE)


def _parse_option_details(instrument: str) -> dict:
    if not isinstance(instrument, str):
        return {}
    m = _OPTION_RE.match(instrument.strip())
    if not m:
        return {}
    underlying, expiry, strike, kind = m.groups()
    return {
        "option_underlying": underlying,
        "option_expiry": pd.to_datetime(expiry, errors="coerce"),
        "option_strike": float(strike),
        "option_kind": kind.upper(),
    }


def _snapshot_date_from_path(path: Path, df: pd.DataFrame) -> pd.Timestamp:
    """Determine the snapshot date for this CSV — the as-of date when the
    Tradezella export was generated. Used to order snapshots so the latest
    state of an open position wins when the same position appears across
    multiple monthly exports.

    Priority:
    1. Filename starts with YYYY-MM-DD → that exact date
    2. Filename starts with YYYY-MM → last day of that month
    3. Fallback: max close_dt observed in the file
    """
    stem = path.stem
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", stem)
    if m:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{4})-(\d{2})", stem)
    if m:
        return (pd.Timestamp(int(m.group(1)), int(m.group(2)), 1) + pd.offsets.MonthEnd(0))
    if "close_dt" in df.columns and df["close_dt"].notna().any():
        return df["close_dt"].max()
    return pd.Timestamp.now().normalize()


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df["open_dt"] = df.apply(_parse_open_dt, axis=1)
    df["close_dt"] = df.apply(_parse_close_dt, axis=1)
    df["snapshot_date"] = _snapshot_date_from_path(path, df)
    df["source_file"] = path.name
    df["instrument_class"] = df.apply(_classify_instrument, axis=1)
    opt_details = df.apply(lambda r: _parse_option_details(r.get("Instrument", "")), axis=1, result_type="expand")
    if not opt_details.empty:
        for col in ["option_underlying", "option_expiry", "option_strike", "option_kind"]:
            if col not in opt_details.columns:
                opt_details[col] = None
        df = pd.concat([df, opt_details], axis=1)

    df["holding_days"] = (df["close_dt"] - df["open_dt"]).dt.total_seconds() / 86400.0
    df["holding_bucket"] = pd.cut(
        df["holding_days"],
        bins=[-1, 1, 3, 10, 10**6],
        labels=["intraday", "short (1-3d)", "medium (4-10d)", "extended (11+d)"],
    )
    df["dow"] = df["open_dt"].dt.day_name()
    df["hour_of_day"] = df["open_dt"].dt.hour
    df["session"] = pd.cut(
        df["hour_of_day"],
        bins=[-1, 9, 11, 14, 23],
        labels=["pre-market", "open (9-11)", "mid (11-14)", "close (14+)"],
    )

    df["status_norm"] = df["Status"].astype(str).str.strip().str.upper()
    is_open_pre = df["status_norm"] == "OPEN"
    # originating_month = month the position was entered (always from open_dt)
    # closing_month     = month P&L was booked (close_dt for closed; NaN for open)
    # month             = the canonical attribution: closing_month for closed
    #                     trades, originating_month for open trades. This is
    #                     the field used for the default monthly view, where
    #                     "April's review" means trades closed in April plus
    #                     positions originated in April that are still open.
    df["originating_month"] = df["open_dt"].dt.to_period("M").astype(str)
    df["closing_month"] = df["close_dt"].dt.to_period("M").astype(str).where(~is_open_pre, None)
    df["month"] = df["closing_month"].where(~is_open_pre, df["originating_month"])

    df["is_closed"] = df["status_norm"].isin({"WIN", "LOSS", "BE"})
    df["is_win"] = df["status_norm"] == "WIN"
    df["is_loss"] = df["status_norm"] == "LOSS"
    df["is_scratch"] = df["status_norm"] == "BE"
    df["is_open"] = df["status_norm"] == "OPEN"

    # R-multiple is only meaningful for closed trades — Tradezella's `Trade Risk`
    # column on OPEN rows is the running MAE-since-open in dollars, not the user's
    # stated risk. So we null out R/risk_abs for open positions.
    risk = df["Trade Risk"].abs().replace(0, np.nan)
    risk_for_R = risk.where(df["is_closed"], other=np.nan)
    df["R"] = df["Net P&L"] / risk_for_R
    df["risk_abs"] = risk.where(df["is_closed"], other=np.nan)

    # For OPEN rows, separate columns. Net P&L on an OPEN row is the running
    # unrealized P&L from Tradezella; Close Date is the export-as-of date.
    df["unrealized_pnl"] = df["Net P&L"].where(df["is_open"], other=0.0)
    df["realized_pnl"] = df["Net P&L"].where(df["is_closed"], other=0.0)

    mfe = df["Position MFE"].fillna(0)
    mae = df["Position MAE"].fillna(0)

    # Only score leaks on closed trades — open trades are still in-flight.
    df["leak_held_too_long"] = df["is_loss"] & (mfe >= risk.fillna(0))
    df["leak_left_on_table"] = df["is_win"] & (df["Exit Efficiency"].fillna(100) < 30) & (mfe >= 3 * risk.fillna(np.inf))
    df["leak_no_stop"] = df["is_closed"] & (df["Trade Risk"].fillna(0) == 0)
    df["leak_blew_stop"] = df["is_closed"] & (mae.abs() > risk.fillna(np.inf))

    df = df.sort_values("close_dt").reset_index(drop=True)
    closed = df[df["is_closed"]].copy()
    closed["cum_pnl"] = closed["Net P&L"].cumsum()
    closed["cum_R"] = closed["R"].fillna(0).cumsum()
    df = df.merge(closed[["cum_pnl", "cum_R"]], left_index=True, right_index=True, how="left")

    return df


def list_available_months() -> list[str]:
    if not CSV_DIR.exists():
        return []
    months = set()
    for f in CSV_DIR.glob("*.csv"):
        m = re.match(r"(\d{4}-\d{2})", f.stem)
        if m:
            months.add(m.group(1))
    return sorted(months)


def load_all_months() -> pd.DataFrame:
    """Load every CSV in csv-exports/ into one frame, applying snapshot-aware
    merging across monthly exports.

    The merging model treats each monthly CSV as a snapshot of the trading
    journal as-of `snapshot_date` (parsed from filename, e.g. 2026-04 → end
    of April). Rules:

    1. Closed trades are immutable — once a slice closes, it carries the same
       (Symbol, Open Date, Open Time, Close Date, Close Time, Quantity) tuple
       across every subsequent snapshot. We dedupe by that tuple, keeping any
       single copy.

    2. Open positions are mutable — across snapshots their quantity, P&L, MFE,
       MAE evolve. Identity is (Symbol, Open Date, Open Time). For each open
       identity, we keep the row from the LATEST snapshot only. If that
       identity is missing from the latest snapshot, it means the position
       fully closed by then — the closed slices appear in that snapshot
       instead, and we drop any prior OPEN view.

    3. There is no double-counting: closed slices are unique by execution
       tuple; open identities collapse to one row per origination.

    This means uploading May's CSV after April's will:
    - Preserve every April closed slice
    - Add every May closed slice (including any that close April-opened
      positions)
    - Replace April's OPEN row with May's updated state for any position
      still open at end of May
    - Drop April's OPEN row entirely for positions fully closed in May
    """
    frames = []
    if not CSV_DIR.exists():
        return pd.DataFrame()
    for f in sorted(CSV_DIR.glob("*.csv")):
        try:
            frames.append(load_csv(f))
        except Exception as e:
            print(f"failed loading {f}: {e}")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)

    # Closed trades: dedupe by execution identity. Any snapshot will do.
    closed_part = df[df["is_closed"]].drop_duplicates(
        subset=["Symbol", "Open Date", "Open Time", "Close Date", "Close Time", "Quantity"])

    # Open positions: identify by origination tuple (Symbol, Open Date, Open Time).
    # Per origination, keep only the row from the latest snapshot — UNLESS that
    # origination has any closed slice equalling/exceeding the original size in
    # a later snapshot (handled implicitly: if the latest snapshot doesn't
    # include the OPEN row, it's been fully closed; we drop it from the open
    # set entirely).
    opens_all = df[df["is_open"]].copy()
    if len(opens_all) > 0:
        latest_snapshot = opens_all["snapshot_date"].max()
        # Only keep open rows from the LATEST snapshot. Earlier-snapshot opens
        # for the same origination are stale; later-snapshot opens override them.
        # If an origination is open in an earlier snapshot but missing from the
        # latest, that means it fully closed — we drop the stale open view.
        latest_opens = opens_all[opens_all["snapshot_date"] == latest_snapshot]
        # Within the latest snapshot, dedupe by origination just in case
        # Tradezella ever emits two open rows for the same origination.
        open_part = latest_opens.drop_duplicates(
            subset=["Symbol", "Open Date", "Open Time"], keep="last")
    else:
        open_part = opens_all

    df = pd.concat([closed_part, open_part], ignore_index=True)
    df = df.sort_values("close_dt").reset_index(drop=True)
    closed = df[df["is_closed"]].copy()
    closed["cum_pnl"] = closed["Net P&L"].cumsum()
    closed["cum_R"] = closed["R"].fillna(0).cumsum()
    df = df.drop(columns=[c for c in ["cum_pnl", "cum_R"] if c in df.columns])
    df = df.merge(closed[["cum_pnl", "cum_R"]], left_index=True, right_index=True, how="left")
    return df
