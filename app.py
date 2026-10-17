"""Trade Review Dashboard. Run: streamlit run app.py"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import ai_insights, data, metrics

st.set_page_config(page_title="Trade Review", layout="wide", initial_sidebar_state="expanded")


@st.cache_data(show_spinner="Loading trades...")
def _load() -> pd.DataFrame:
    return data.load_all_months()


def _fmt_dollar(x: float) -> str:
    if x is None or pd.isna(x):
        return "-"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _fmt_pct(x: float) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{x*100:.1f}%"


def _fmt_R(x: float) -> str:
    if x is None or pd.isna(x):
        return "-"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.2f}R"


def _kpi_row(h: dict, prev: dict | None = None) -> None:
    cols = st.columns(6)
    inf_ = float("inf")
    pf_curr = h["profit_factor"]
    pf_prev = prev["profit_factor"] if prev else None
    pf_delta = ""
    if prev and pf_curr != inf_ and pf_prev != inf_:
        pf_delta = f"{pf_curr - pf_prev:+.2f}"
    items = [
        ("Net P&L (realized)", _fmt_dollar(h["net_pnl"]), prev and _fmt_dollar(h["net_pnl"] - prev["net_pnl"])),
        ("Net R (realized)", _fmt_R(h["net_R"]), prev and _fmt_R(h["net_R"] - prev["net_R"])),
        ("Win rate (realized)", _fmt_pct(h["win_rate"]), prev and _fmt_pct(h["win_rate"] - prev["win_rate"])),
        ("Expectancy (realized)", _fmt_R(h["expectancy_R"]), prev and _fmt_R(h["expectancy_R"] - prev["expectancy_R"])),
        ("Profit factor", f"{pf_curr:.2f}" if pf_curr != inf_ else "∞", pf_delta),
        ("Closed trades", str(h["trades"]), prev and f"{h['trades'] - prev['trades']:+d}"),
    ]
    for col, (label, value, delta) in zip(cols, items):
        col.metric(label, value, delta=delta if delta else None)


def _unrealized_strip(u: dict) -> None:
    """Amber-styled KPI strip for open positions. Skips render if no opens.

    Header text already states the open count, so the 5-column metric grid
    uses the slot for capital deployed instead of duplicating the count."""
    if u["open_count"] == 0:
        return
    flat_count = u["open_count"] - u["unrealized_winners"] - u["unrealized_losers"]
    wl_label = (f"{u['unrealized_winners']} / {u['unrealized_losers']}"
                + (f" / {flat_count} flat" if flat_count > 0 else ""))
    st.markdown(
        '<div style="background:#fef3c7;border-left:4px solid #f59e0b;padding:8px 14px;'
        'border-radius:4px;margin:8px 0;font-size:0.85em;color:#78350f;">'
        f'<b>📌 Unrealized — {u["open_count"]} open position{"s" if u["open_count"] != 1 else ""}.</b> '
        'These are not booked. R-multiples not shown (Tradezella\'s Trade Risk on open '
        'rows is running MAE, not your stated risk).</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(5)
    cols[0].metric("Unrealized P&L", _fmt_dollar(u["unrealized_pnl"]))
    cols[1].metric("Capital deployed", _fmt_dollar(u["total_capital_at_risk"]),
                   help="Sum of cost basis across all open positions")
    cols[2].metric("Winners / Losers", wl_label)
    cols[3].metric("Best open", _fmt_dollar(u["best_open_pnl"]))
    cols[4].metric("Worst open", _fmt_dollar(u["worst_open_pnl"]))


def _mtm_strip(c: dict, open_count: int = 0) -> None:
    """Combined realized + unrealized = mark-to-market. Renders whenever opens exist,
    even if unrealized P&L rounds to zero (so the visibility predicate matches the
    unrealized strip's predicate and the sequence is consistent)."""
    if open_count == 0:
        return
    cols = st.columns(3)
    cols[0].metric("Realized", _fmt_dollar(c["realized_pnl"]))
    cols[1].metric("+ Unrealized", _fmt_dollar(c["unrealized_pnl"]))
    cols[2].metric("= Mark-to-market", _fmt_dollar(c["mtm_pnl"]),
                   help="If you closed every open position right now")


def _render_open_positions_panel(df: pd.DataFrame) -> None:
    detail = metrics.open_positions_detail(df)
    if len(detail) == 0:
        return
    st.markdown("### 📌 Open positions")

    def _opt_dollar(v):
        return "—" if v is None or pd.isna(v) else _fmt_dollar(v)

    def _opt_pct(v, sign=False):
        if v is None or pd.isna(v):
            return "—"
        fmt = f"{v:+.2f}%" if sign else f"{v:.0f}%"
        return fmt

    show = detail.copy()
    show["Net P&L"] = show["Net P&L"].apply(_fmt_dollar)
    show["pct_return"] = show["pct_return"].apply(lambda v: _opt_pct(v, sign=True))
    show["giveback_dollars"] = show["giveback_dollars"].apply(_opt_dollar)
    show["giveback_pct"] = show["giveback_pct"].apply(_opt_pct)
    show["holding_days"] = show["holding_days"].apply(lambda v: f"{v:.0f}d" if pd.notna(v) else "—")
    show["Adjusted Cost"] = show["Adjusted Cost"].apply(_fmt_dollar)
    show["Position MAE"] = show["Position MAE"].apply(_opt_dollar)
    show["Position MFE"] = show["Position MFE"].apply(_opt_dollar)
    show["entry_price"] = show["entry_price"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
    show["mark_price"] = show["mark_price"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
    show.columns = [
        "Symbol", "Side", "Class", "Qty", "Entry", "Mark",
        "Days held", "Peak gain", "Peak drawdown",
        "Unrealized P&L", "% return", "Giveback $", "Giveback %",
        "Cost basis", "Status"
    ]
    st.dataframe(show, use_container_width=True, hide_index=True)

    # Giveback alert: only fires on rows that have a real (non-NaN) giveback_pct
    flagged = detail[detail["giveback_pct"] >= 40]
    if len(flagged):
        names = ", ".join(flagged["Symbol"].tolist())
        st.warning(
            f"⚠️ Giveback alert: **{names}** {'have' if len(flagged) > 1 else 'has'} "
            f"surrendered ≥40% of peak unrealized gains. Watch for winner-turning-loser pattern."
        )

    # Surface the missing-MFE caveat for any row, not only when all rows lack it.
    no_mfe = detail[detail["Position MFE"].isna() | (detail["Position MFE"] == 0)]
    if len(no_mfe) > 0:
        names = ", ".join(no_mfe["Symbol"].tolist())
        st.caption(
            f"ℹ️ Peak gain / drawdown / giveback shown as '—' for: **{names}** — "
            "Tradezella doesn't populate MFE/MAE on OPEN rows. These will appear once positions close."
        )


def render_overview(df: pd.DataFrame) -> None:
    st.subheader("All-time overview")
    h = metrics.headline(df)
    u = metrics.headline_unrealized(df)
    c = metrics.combined_headline(df)
    _kpi_row(h)
    _unrealized_strip(u)
    if u["open_count"] > 0:
        _mtm_strip(c, open_count=u["open_count"])
        _render_open_positions_panel(df)

    cols = st.columns(4)
    cols[0].metric("Wins / Losses / BE", f"{h['wins']} / {h['losses']} / {h['scratches']}")
    cols[1].metric("Avg win / loss ratio", f"{h['avg_win_loss_ratio']:.2f}")
    cols[2].metric("Largest win", _fmt_dollar(h["largest_win"]))
    cols[3].metric("Largest loss", _fmt_dollar(h["largest_loss"]))

    cols = st.columns(3)
    cols[0].metric("Max drawdown ($)", _fmt_dollar(h["max_drawdown_dollar"]))
    cols[1].metric("Max drawdown (R)", _fmt_R(h["max_drawdown_R"]))
    cols[2].metric("Avg holding (days)", f"{h['avg_holding_days']:.1f}")

    st.markdown("### Equity curve")
    closed = df[df["is_closed"]].sort_values("close_dt").copy()
    # Recompute cumulative P&L on the filtered closed set so the curve stays
    # consistent with the headline KPIs (which compute net_pnl from the same
    # filtered frame).
    if len(closed):
        closed["cum_pnl"] = closed["Net P&L"].cumsum()
        closed["cum_R"] = closed["R"].fillna(0).cumsum()

    has_closed = len(closed) > 0
    has_opens = u["open_count"] > 0

    if has_closed or has_opens:
        fig = go.Figure()
        if has_closed:
            fig.add_trace(go.Scatter(
                x=closed["close_dt"], y=closed["cum_pnl"], mode="lines",
                name="Realized P&L", line=dict(color="#2563eb", width=2)))
        if has_opens:
            last_dt = closed["close_dt"].max() if has_closed else df.loc[df["is_open"], "open_dt"].min()
            last_realized = float(closed["cum_pnl"].iloc[-1]) if has_closed else 0.0
            today = pd.Timestamp.now().normalize()
            mtm_value = last_realized + u["unrealized_pnl"]
            fig.add_trace(go.Scatter(
                x=[last_dt, today],
                y=[last_realized, mtm_value], mode="lines+markers",
                name=f"+ Unrealized ({_fmt_dollar(u['unrealized_pnl'])})",
                line=dict(color="#f59e0b", width=2, dash="dash"),
                marker=dict(size=8)))
        fig.update_layout(height=380, margin=dict(t=20, l=0, r=0, b=0),
                          yaxis_title="P&L ($)", hovermode="x unified",
                          legend=dict(orientation="h", y=1.05))
        st.plotly_chart(fig, use_container_width=True)

        if has_closed:
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=closed["close_dt"], y=closed["cum_R"], mode="lines",
                                       name="Cum R (realized)", line=dict(color="#10b981")))
            fig2.update_layout(height=300, margin=dict(t=20, l=0, r=0, b=0), yaxis_title="Cum R",
                               hovermode="x unified")
            st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No realized or open trades match this filter.")


def render_month(df: pd.DataFrame, month: str, prev_month: str | None) -> None:
    sub = df[df["month"] == month]
    h = metrics.headline(sub)
    u = metrics.headline_unrealized(sub)
    c = metrics.combined_headline(sub)
    prev = metrics.headline(df[df["month"] == prev_month]) if prev_month else None

    st.subheader(f"{month} — monthly review")
    # Banner for months with only opens — KPI row would be all zeros otherwise.
    if h["trades"] == 0 and u["open_count"] > 0:
        st.info(
            f"📭 No closed trades booked in {month}. "
            f"{u['open_count']} position{'s' if u['open_count'] != 1 else ''} still open — see unrealized strip below."
        )
    _kpi_row(h, prev)
    _unrealized_strip(u)
    if u["open_count"] > 0:
        _mtm_strip(c, open_count=u["open_count"])
        _render_open_positions_panel(sub)

    st.markdown("### AI verdict")
    if st.button("Regenerate AI insights for this month", key=f"regen_{month}"):
        with st.spinner("Calling Bedrock..."):
            ai_insights.get_month_insights(month, df, force=True)
        st.rerun()
    with st.spinner("Loading AI insights..."):
        insights = ai_insights.get_month_insights(month, df)
    if "error" in insights:
        st.warning(f"AI insights unavailable: {insights['error']}")
    else:
        st.info(insights.get("headline_verdict", ""))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**What worked**")
            for b in insights.get("what_worked", []):
                st.markdown(f"- {b}")
        with c2:
            st.markdown("**What didn't**")
            for b in insights.get("what_didnt", []):
                st.markdown(f"- {b}")

        leak = insights.get("biggest_leak", {})
        if leak:
            st.error(f"**Biggest leak: {leak.get('name', '')}** — {leak.get('evidence', '')} "
                     f"(estimated cost: {leak.get('estimated_cost_R', 0):.2f}R)")
        st.markdown(f"**Behavioral observation:** {insights.get('behavioral_observation', '')}")
        st.markdown(f"**Edge quality:** {insights.get('edge_quality', '')}")
        opc = insights.get("open_positions_commentary")
        if opc and opc != "null" and opc.strip():
            st.markdown(f"**Open positions commentary:** {opc}")

        st.markdown("### 30-day action plan")
        for i, action in enumerate(insights.get("top_3_actions", []), 1):
            with st.expander(f"{i}. {action.get('title', '')}", expanded=True):
                st.markdown(f"**Why:** {action.get('why', '')}")
                st.markdown(f"**Change:** {action.get('change', '')}")
                st.markdown(f"**Success metric:** {action.get('metric', '')}")
                st.markdown(f"**Tripwire:** {action.get('tripwire', '')}")

    if prev_month:
        st.markdown("### Month-over-month")
        with st.spinner("Loading MoM analysis..."):
            mom = ai_insights.get_mom_insights(month, prev_month, df)
        if "error" not in mom:
            traj = mom.get("trajectory", "")
            color = {"growing": "🟢", "stable": "🟡", "eroding": "🔴"}.get(traj, "")
            st.markdown(f"**Trajectory: {color} {traj}** — {mom.get('primary_driver', '')}")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Improved**")
                for b in mom.get("what_improved", []):
                    st.markdown(f"- {b}")
            with c2:
                st.markdown("**Degraded**")
                for b in mom.get("what_degraded", []):
                    st.markdown(f"- {b}")
            ct = mom.get("concerning_trend")
            if ct: st.warning(f"Concerning: {ct}")
            et = mom.get("encouraging_trend")
            if et: st.success(f"Encouraging: {et}")


def render_charts(df: pd.DataFrame, month_filter: str | None) -> None:
    sub = df if month_filter == "All time" else df[df["month"] == month_filter]
    closed = sub[sub["is_closed"]]
    if len(closed) == 0:
        if len(sub) > 0 and sub["is_open"].any():
            st.info(
                f"This filter has only open positions ({int(sub['is_open'].sum())} of them) — "
                "close trades to see realized analytics. The Open positions panel on Overview "
                "and Monthly review tabs has the per-position detail."
            )
        else:
            st.info("No trades match this filter.")
        return

    st.markdown("### Monthly P&L")
    monthly = metrics.by_month(df)
    if len(monthly):
        fig = go.Figure()
        colors = ["#10b981" if v >= 0 else "#ef4444" for v in monthly["net_pnl"]]
        fig.add_trace(go.Bar(x=monthly["month"], y=monthly["net_pnl"], marker_color=colors, name="Net P&L"))
        fig.update_layout(height=320, margin=dict(t=20, l=0, r=0, b=0), yaxis_title="Net P&L ($)")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Distribution of trade R-multiples")
    R_vals = closed["R"].dropna()
    if len(R_vals):
        fig = px.histogram(R_vals, nbins=40, color_discrete_sequence=["#2563eb"])
        fig.update_layout(height=300, margin=dict(t=20, l=0, r=0, b=0),
                          xaxis_title="R", yaxis_title="trades", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### By holding duration")
        bh = metrics.by_holding(sub)
        if len(bh):
            fig = px.bar(bh, x="holding_bucket", y="net_pnl", color="net_pnl",
                         color_continuous_scale=["#ef4444", "#10b981"],
                         labels={"net_pnl": "Net P&L ($)"})
            fig.update_layout(height=300, margin=dict(t=20, l=0, r=0, b=0))
            st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("### By instrument class")
        bi = metrics.by_instrument_class(sub)
        if len(bi):
            fig = px.bar(bi, x="instrument_class", y="net_pnl", color="net_pnl",
                         color_continuous_scale=["#ef4444", "#10b981"])
            fig.update_layout(height=300, margin=dict(t=20, l=0, r=0, b=0))
            st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### By day-of-week")
        bd = metrics.by_dow(sub)
        if len(bd):
            fig = px.bar(bd, x="dow", y="net_pnl", color="net_pnl",
                         color_continuous_scale=["#ef4444", "#10b981"])
            fig.update_layout(height=300, margin=dict(t=20, l=0, r=0, b=0))
            st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.markdown("### By session")
        bs = metrics.by_session(sub)
        if len(bs):
            fig = px.bar(bs, x="session", y="net_pnl", color="net_pnl",
                         color_continuous_scale=["#ef4444", "#10b981"])
            fig.update_layout(height=300, margin=dict(t=20, l=0, r=0, b=0))
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("### By symbol — top winners and losers")
    bs = metrics.by_symbol(sub, top_n=15)
    if len(bs):
        bs_sorted = bs.sort_values("net_pnl")
        fig = px.bar(bs_sorted, x="net_pnl", y="Symbol", orientation="h", color="net_pnl",
                     color_continuous_scale=["#ef4444", "#10b981"])
        fig.update_layout(height=600, margin=dict(t=20, l=0, r=0, b=0))
        st.plotly_chart(fig, use_container_width=True)


def render_execution(df: pd.DataFrame, month_filter: str | None) -> None:
    sub = df if month_filter == "All time" else df[df["month"] == month_filter]
    leaks = metrics.execution_leaks(sub)
    qf = metrics.quality_filtered_pnl(sub)
    pl = metrics.post_loss_behavior(sub)
    rd = metrics.risk_distribution(sub)
    sk = metrics.streaks(sub)

    st.markdown("### Execution leak proxies (inferred from MAE/MFE)")
    cols = st.columns(4)
    cols[0].metric("Held too long", leaks["held_too_long"], help="Loss closed but MFE was ≥ risk earlier — gave back a winner")
    cols[1].metric("Left on table", leaks["left_on_table"], help="Win with <30% exit efficiency and MFE ≥ 3R")
    cols[2].metric("No stop set", leaks["no_stop"], help="Trade Risk = 0 in journal")
    cols[3].metric("Blew through stop", leaks["blew_stop"], help="MAE exceeded planned risk")

    st.markdown("### Quality-filtered P&L (skip leak-flagged trades)")
    cols = st.columns(3)
    cols[0].metric("Actual P&L", _fmt_dollar(qf["actual_pnl"]))
    cols[1].metric("If clean trades only", _fmt_dollar(qf["filtered_pnl"]),
                   delta=_fmt_dollar(qf["delta"]))
    cols[2].metric("R uplift", _fmt_R(qf["filtered_R"] - qf["actual_R"]))

    st.markdown("### Streaks")
    cols = st.columns(3)
    cols[0].metric("Max win streak", sk["max_win_streak"])
    cols[1].metric("Max loss streak", sk["max_loss_streak"])
    cols[2].metric("Current streak", f"{sk['current_streak']} {sk['current_streak_type']}")

    st.markdown("### Post-loss / post-win behavior (next 3 trades)")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**After a loss** ({pl['trades_after_loss']} trades sampled)")
        st.markdown(f"- Win rate: **{_fmt_pct(pl['win_rate_after_loss'])}**")
        st.markdown(f"- Avg R: **{_fmt_R(pl['avg_R_after_loss'])}**")
    with c2:
        st.markdown(f"**After a win** ({pl['trades_after_win']} trades sampled)")
        st.markdown(f"- Win rate: **{_fmt_pct(pl['win_rate_after_win'])}**")
        st.markdown(f"- Avg R: **{_fmt_R(pl['avg_R_after_win'])}**")

    st.markdown("### Risk-per-trade distribution")
    cols = st.columns(5)
    cols[0].metric("Min", _fmt_dollar(rd["min"]))
    cols[1].metric("Median", _fmt_dollar(rd["median"]))
    cols[2].metric("Mean", _fmt_dollar(rd["mean"]))
    cols[3].metric("Max", _fmt_dollar(rd["max"]))
    cols[4].metric("Outliers (>2× median)", rd["outlier_count"])

    risks = sub[sub["is_closed"]]["risk_abs"].dropna()
    if len(risks):
        fig = px.histogram(risks, nbins=30, color_discrete_sequence=["#f59e0b"])
        fig.update_layout(height=280, margin=dict(t=20, l=0, r=0, b=0),
                          xaxis_title="$ risk per trade", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)


def render_positions(df: pd.DataFrame, month_filter: str | None) -> None:
    """Position-lifecycle view. Each row is one originating trade rolled up
    across every slice (closed + still-open). Lets you see whether April-opened
    conviction trades worked out across multiple months."""
    st.markdown("### Position lifecycle (grouped by origination)")
    st.caption(
        "Each row = one originating trade. A position opened in April and "
        "trimmed across April–June rolls up to ONE row showing total realized + "
        "unrealized + remaining open quantity. Use the 'Originated in' filter "
        "below to scope by entry month."
    )

    if month_filter == "All time":
        scoped = df
        scope_label = "all time"
    else:
        # Default scope on this tab: positions ORIGINATED in the selected month
        # (rather than closed in it). That's the lens this tab is for.
        scoped = df[df["originating_month"] == month_filter]
        scope_label = f"originated in {month_filter}"

    lc = metrics.position_lifecycle(scoped)
    if len(lc) == 0:
        st.info(f"No positions {scope_label}.")
        return

    summary_cols = st.columns(5)
    summary_cols[0].metric("Originations", len(lc))
    summary_cols[1].metric("Fully closed", int((lc["status"] == "fully closed").sum()))
    summary_cols[2].metric("Partially closed", int((lc["status"] == "partially closed").sum()))
    summary_cols[3].metric("Fully open", int((lc["status"] == "fully open").sum()))
    summary_cols[4].metric("Total P&L (real + unreal)", _fmt_dollar(lc["total_pnl"].sum()))

    show = lc.copy()
    show["realized_pnl"] = show["realized_pnl"].apply(_fmt_dollar)
    show["unrealized_pnl"] = show["unrealized_pnl"].apply(_fmt_dollar)
    show["total_pnl"] = show["total_pnl"].apply(_fmt_dollar)
    show["original_qty"] = show["original_qty"].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
    show["closed_qty"] = show["closed_qty"].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
    show["open_qty"] = show["open_qty"].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
    show["latest_activity"] = show["latest_activity"].apply(
        lambda v: v.strftime("%Y-%m-%d") if pd.notna(v) else "—")
    show = show[[
        "symbol", "side", "instrument_class", "originating_month", "origination_date",
        "original_qty", "closed_qty", "open_qty",
        "wins", "losses", "scratches",
        "realized_pnl", "unrealized_pnl", "total_pnl",
        "status", "latest_activity",
    ]].rename(columns={
        "symbol": "Symbol", "side": "Side", "instrument_class": "Class",
        "originating_month": "Originated", "origination_date": "Open date",
        "original_qty": "Orig qty", "closed_qty": "Closed qty", "open_qty": "Open qty",
        "wins": "W", "losses": "L", "scratches": "BE",
        "realized_pnl": "Realized $", "unrealized_pnl": "Unrealized $", "total_pnl": "Total $",
        "status": "Status", "latest_activity": "Last activity",
    })

    def _color_status(row):
        bg = {
            "fully open": "#fef3c7",     # amber
            "partially closed": "#fde68a", # darker amber
            "fully closed": "",
        }.get(row["Status"], "")
        if bg:
            return [f"background-color: {bg}"] * len(row)
        return [""] * len(row)

    styled = show.style.apply(_color_status, axis=1)
    st.dataframe(styled, use_container_width=True, height=600, hide_index=True)
    st.download_button(
        "Download position lifecycle CSV",
        show.to_csv(index=False),
        file_name=f"positions_{month_filter or 'all'}.csv",
    )


def render_trades(df: pd.DataFrame, month_filter: str | None) -> None:
    sub = df if month_filter == "All time" else df[df["month"] == month_filter]
    show = sub[[
        "Symbol", "instrument_class", "Side", "Status", "Open Date", "Close Date",
        "holding_days", "Quantity", "Entry Price", "Exit Price",
        "Trade Risk", "Net P&L", "R", "Realized RR", "Position MAE", "Position MFE",
        "Exit Efficiency", "Zella Score", "Playbook"
    ]].copy()
    show.columns = ["Symbol", "Class", "Side", "Status", "Open", "Close", "Days held",
                    "Qty", "Entry", "Exit", "Risk $", "Net P&L", "R", "Realized RR",
                    "MAE", "MFE", "Exit eff %", "Zella", "Setup"]

    def _highlight_open(row):
        if row["Status"] == "OPEN":
            return ["background-color: #fef3c7"] * len(row)
        return [""] * len(row)

    styled = show.style.apply(_highlight_open, axis=1)
    st.markdown(
        '<span style="display:inline-block;width:14px;height:14px;background:#fef3c7;'
        'border:1px solid #f59e0b;border-radius:2px;vertical-align:middle;"></span> '
        '= OPEN position (unrealized P&L; R, MAE/MFE, exit-eff, Realized RR, Zella '
        'all blank by design — these populate once the trade closes)',
        unsafe_allow_html=True,
    )
    st.dataframe(styled, use_container_width=True, height=600)
    st.download_button("Download filtered CSV", show.to_csv(index=False),
                       file_name=f"trades_{month_filter or 'all'}.csv")


def render_mom_table(df: pd.DataFrame) -> None:
    monthly = metrics.by_month(df)
    if len(monthly) == 0:
        st.info("No closed-month data yet.")
        return
    show = monthly[[
        "month", "trades", "wins", "losses", "scratches", "win_rate",
        "net_pnl", "net_R", "expectancy_R", "profit_factor",
        "avg_win", "avg_loss", "max_drawdown_dollar", "max_drawdown_R",
        "avg_holding_days", "total_fees"
    ]].copy()
    for c in ["win_rate"]:
        show[c] = show[c].apply(_fmt_pct)
    for c in ["net_pnl", "avg_win", "avg_loss", "max_drawdown_dollar", "total_fees"]:
        show[c] = show[c].apply(_fmt_dollar)
    for c in ["net_R", "expectancy_R", "max_drawdown_R"]:
        show[c] = show[c].apply(_fmt_R)
    for c in ["profit_factor"]:
        show[c] = show[c].apply(lambda v: f"{v:.2f}" if v != float("inf") else "∞")
    st.dataframe(show, use_container_width=True)


# ---------------- App body ----------------
st.title("Trade Review Dashboard")

df = _load()
if len(df) == 0:
    st.warning("No CSVs in csv-exports/. Drop a Tradezella export there and refresh.")
    st.stop()

months = sorted(df["month"].dropna().unique().tolist())
sidebar = st.sidebar
sidebar.header("Filters")
month_choice = sidebar.selectbox("Month", ["All time"] + months, index=len(months))
instr_choices = sidebar.multiselect("Instrument class", df["instrument_class"].dropna().unique().tolist(),
                                     default=df["instrument_class"].dropna().unique().tolist())
side_choices = sidebar.multiselect("Side", ["long", "short"], default=["long", "short"])
status_choices = sidebar.multiselect(
    "Closed-trade status (does not affect open positions)",
    ["WIN", "LOSS", "BE"], default=["WIN", "LOSS", "BE"])

# Architectural: instrument/side filters apply to everything; status filter
# applies ONLY to closed trades. Open positions always pass through —
# unrealized P&L is a separate concern from win/loss/scratch slicing.
base = df[df["instrument_class"].isin(instr_choices) & df["Side"].isin(side_choices)]
filt = base[base["is_open"] | base["status_norm"].isin(status_choices)]

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["Overview", "Monthly review", "Charts", "Execution", "Positions", "Trades"])

with tab1:
    render_overview(filt)
    st.markdown("### Month-over-month table")
    render_mom_table(filt)

with tab2:
    if month_choice == "All time":
        st.info("Select a specific month in the sidebar to see the monthly review.")
    else:
        idx = months.index(month_choice)
        prev = months[idx - 1] if idx > 0 else None
        render_month(filt, month_choice, prev)

with tab3:
    render_charts(filt, month_choice)

with tab4:
    render_execution(filt, month_choice)

with tab5:
    render_positions(filt, month_choice)

with tab6:
    render_trades(filt, month_choice)
