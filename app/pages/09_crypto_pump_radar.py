from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from core.config import load_yaml_config  # noqa: E402
from data.database import init_db, session_scope  # noqa: E402
from data.repositories.crypto_pump_repo import CryptoPumpRepository  # noqa: E402
from services.crypto_pump_history_service import (  # noqa: E402
    CryptoPumpHistoryService,
    HistoryReport,
)
from services.crypto_pump_radar_service import (  # noqa: E402
    CandidateRow,
    CryptoPumpRadarService,
)

CLASSIFICATION_COLORS = {
    "IGNORE": "#64748b",
    "WATCH": "#0ea5e9",
    "EARLY_MOMENTUM": "#16a34a",
    "HIGH_RISK_PUMP": "#f97316",
    "EXTREME_SPECULATION": "#dc2626",
}


def _format_score_color(value: float | None, *, danger_above: float = 70) -> str:
    if value is None or pd.isna(value):
        return ""
    if value >= danger_above:
        return "color: #dc2626; font-weight: 700"
    if value >= danger_above - 15:
        return "color: #d97706; font-weight: 600"
    return ""


def _format_classification_color(value: str | None) -> str:
    if not value:
        return ""
    color = CLASSIFICATION_COLORS.get(str(value), "#475569")
    return f"background-color: {color}; color: white; font-weight: 700"


def _candidates_to_frame(rows: list[CandidateRow]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    records = []
    for idx, row in enumerate(rows, start=1):
        summary = row.to_summary()
        summary["rank"] = idx
        records.append(summary)
    frame = pd.DataFrame(records)
    column_order = [
        "rank",
        "symbol",
        "chain",
        "dex",
        "price_usd",
        "liquidity_usd",
        "volume_1h",
        "volume_24h",
        "buys_1h",
        "sells_1h",
        "price_change_5m",
        "price_change_1h",
        "price_change_6h",
        "price_change_24h",
        "pair_age_hours",
        "pump_momentum_score",
        "rug_risk_score",
        "final_speculative_score",
        "classification",
        "url",
    ]
    for col in column_order:
        if col not in frame.columns:
            frame[col] = None
    return frame[column_order]


def _draw_top_table(frame: pd.DataFrame) -> None:
    display = frame.rename(
        columns={
            "rank": "Rank",
            "symbol": "Par",
            "chain": "Chain",
            "dex": "DEX",
            "price_usd": "Price USD",
            "liquidity_usd": "Liquidez",
            "volume_1h": "Vol 1h",
            "volume_24h": "Vol 24h",
            "buys_1h": "Buys 1h",
            "sells_1h": "Sells 1h",
            "price_change_5m": "Δ 5m",
            "price_change_1h": "Δ 1h",
            "price_change_6h": "Δ 6h",
            "price_change_24h": "Δ 24h",
            "pair_age_hours": "Edad (h)",
            "pump_momentum_score": "Momentum",
            "rug_risk_score": "Rug Risk",
            "final_speculative_score": "Final Score",
            "classification": "Clase",
            "url": "DexScreener",
        }
    )
    styler = display.style
    styler = styler.map(
        lambda v: _format_score_color(v, danger_above=70),
        subset=["Final Score", "Momentum"],
    )
    styler = styler.map(_format_classification_color, subset=["Clase"])
    styler = styler.map(
        lambda v: _format_score_color(v, danger_above=60), subset=["Rug Risk"]
    )

    st.dataframe(
        styler,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price USD": st.column_config.NumberColumn("Price USD", format="$%.6f"),
            "Liquidez": st.column_config.NumberColumn("Liquidez", format="$%.0f"),
            "Vol 1h": st.column_config.NumberColumn("Vol 1h", format="$%.0f"),
            "Vol 24h": st.column_config.NumberColumn("Vol 24h", format="$%.0f"),
            "Δ 5m": st.column_config.NumberColumn("Δ 5m", format="%+.1f%%"),
            "Δ 1h": st.column_config.NumberColumn("Δ 1h", format="%+.1f%%"),
            "Δ 6h": st.column_config.NumberColumn("Δ 6h", format="%+.1f%%"),
            "Δ 24h": st.column_config.NumberColumn("Δ 24h", format="%+.1f%%"),
            "Edad (h)": st.column_config.NumberColumn("Edad (h)", format="%.1f"),
            "Final Score": st.column_config.NumberColumn("Final Score", format="%.1f"),
            "Momentum": st.column_config.NumberColumn("Momentum", format="%.1f"),
            "Rug Risk": st.column_config.NumberColumn("Rug Risk", format="%.1f"),
            "DexScreener": st.column_config.LinkColumn("DexScreener", display_text="abrir"),
        },
    )


def _draw_history_charts(report: HistoryReport) -> None:
    frame = report.snapshots
    if frame.empty:
        st.info(
            "Todavia no hay snapshots persistidos para este par. "
            "Ejecuta el scanner o lanza el job CLI para empezar a registrarlos."
        )
        return

    event_lookup = {ev.timestamp: ev for ev in report.events}

    def _add_event_markers(fig: go.Figure, y_max_hint: float | None) -> None:
        for ev in report.events:
            color = CLASSIFICATION_COLORS.get(ev.classification, "#475569")
            fig.add_vline(
                x=ev.timestamp,
                line=dict(color=color, dash="dot", width=1.5),
                annotation_text=ev.classification,
                annotation_position="top",
                annotation_font=dict(color=color, size=11),
            )

    # Precio + scores
    score_fig = go.Figure()
    score_fig.add_trace(
        go.Scatter(
            x=frame["detected_at"],
            y=frame["final_speculative_score"],
            mode="lines+markers",
            name="Final Score",
            line=dict(color="#dc2626", width=2),
        )
    )
    score_fig.add_trace(
        go.Scatter(
            x=frame["detected_at"],
            y=frame["pump_momentum_score"],
            mode="lines",
            name="Pump Momentum",
            line=dict(color="#16a34a", width=1.5, dash="dot"),
        )
    )
    score_fig.add_trace(
        go.Scatter(
            x=frame["detected_at"],
            y=frame["rug_risk_score"],
            mode="lines",
            name="Rug Risk",
            line=dict(color="#f97316", width=1.5, dash="dash"),
        )
    )
    score_fig.update_layout(
        title="Scores especulativos",
        height=360,
        margin=dict(t=40, b=30),
        yaxis=dict(range=[0, 100]),
        legend=dict(orientation="h", y=1.1),
    )
    _add_event_markers(score_fig, 100)
    st.plotly_chart(score_fig, use_container_width=True)

    price_fig = go.Figure()
    price_fig.add_trace(
        go.Scatter(
            x=frame["detected_at"],
            y=frame["price_usd"],
            mode="lines+markers",
            name="Price USD",
            line=dict(color="#0ea5e9", width=2),
        )
    )
    price_fig.update_layout(
        title="Precio",
        height=300,
        margin=dict(t=40, b=20),
        yaxis_title="USD",
    )
    _add_event_markers(price_fig, None)
    st.plotly_chart(price_fig, use_container_width=True)

    liq_fig = go.Figure()
    liq_fig.add_trace(
        go.Scatter(
            x=frame["detected_at"],
            y=frame["liquidity_usd"],
            mode="lines+markers",
            name="Liquidez USD",
            line=dict(color="#64748b", width=2),
        )
    )
    liq_fig.add_trace(
        go.Scatter(
            x=frame["detected_at"],
            y=frame["volume_1h"],
            mode="lines",
            name="Volumen 1h",
            line=dict(color="#16a34a", width=1.4, dash="dot"),
            yaxis="y2",
        )
    )
    liq_fig.add_trace(
        go.Scatter(
            x=frame["detected_at"],
            y=frame["volume_24h"],
            mode="lines",
            name="Volumen 24h",
            line=dict(color="#7c3aed", width=1.4, dash="dash"),
            yaxis="y2",
        )
    )
    liq_fig.update_layout(
        title="Liquidez y volumen",
        height=320,
        margin=dict(t=40, b=20),
        yaxis=dict(title="Liquidez USD"),
        yaxis2=dict(title="Volumen", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.12),
    )
    st.plotly_chart(liq_fig, use_container_width=True)

    tx_fig = go.Figure()
    tx_fig.add_trace(
        go.Bar(
            x=frame["detected_at"],
            y=frame["buys_1h"],
            name="Buys 1h",
            marker_color="#16a34a",
        )
    )
    tx_fig.add_trace(
        go.Bar(
            x=frame["detected_at"],
            y=frame["sells_1h"],
            name="Sells 1h",
            marker_color="#dc2626",
        )
    )
    tx_fig.update_layout(
        title="Transacciones 1h",
        barmode="group",
        height=280,
        margin=dict(t=40, b=20),
        legend=dict(orientation="h", y=1.15),
    )
    st.plotly_chart(tx_fig, use_container_width=True)

    if event_lookup:
        st.subheader("Eventos detectados")
        events_frame = pd.DataFrame(
            [
                {
                    "timestamp": ev.timestamp,
                    "clasificacion": ev.classification,
                    "score": ev.final_speculative_score,
                    "rug_risk": ev.rug_risk_score,
                    "prior_pump_penalty": ev.prior_pump_penalty,
                    "price_usd": ev.price_usd,
                }
                for ev in report.events
            ]
        )
        st.dataframe(events_frame, use_container_width=True, hide_index=True)

    st.subheader("Tabla historica de snapshots")
    st.dataframe(
        frame.sort_values("detected_at", ascending=False),
        use_container_width=True,
        hide_index=True,
    )

    replay = report.replay
    cols = st.columns(4)
    cols[0].metric("Snapshots", replay.snapshots_total)
    cols[1].metric(
        "Max up tras señal",
        f"{replay.max_up_pct:+.1f}%" if replay.max_up_pct is not None else "N/A",
    )
    cols[2].metric(
        "Drawdown tras max",
        f"{replay.drawdown_after_max_pct:+.1f}%"
        if replay.drawdown_after_max_pct is not None
        else "N/A",
    )
    cols[3].metric("Timing", replay.timing_label)
    for note in replay.notes:
        st.warning(note)


# ---------------- Page entrypoint ---------------------------------------------

init_db()

st.set_page_config(page_title="Crypto Pump Radar", layout="wide")
st.title("Crypto Pump Radar")
config = load_yaml_config("crypto_pump_radar.yaml")
disclaimer = (
    config.get("ui", {}).get("risk_disclaimer")
    or "Modulo exploratorio de alta especulacion. Riesgo de perdida total."
)

st.error(
    "Modulo especulativo independiente. NO modifica scoring principal, alertas, "
    "Telegram ni cartera. Uso exploratorio."
)
st.warning(disclaimer)
st.caption(
    "Fuente: DexScreener. Los scores son heuristicos y no garantizan ningun movimiento."
)

tabs = st.tabs(["Scanner automatico", "Analisis manual", "Histórico persistido"])

# ---------------- Scanner automatico -----------------------------------------

with tabs[0]:
    st.subheader("Top candidatos especulativos")
    scanner_cfg = config.get("scanner", {})
    available_chains = list(scanner_cfg.get("chains", []))
    col1, col2, col3 = st.columns([3, 1, 1])
    selected_chains = col1.multiselect(
        "Chains a escanear",
        options=available_chains,
        default=available_chains,
    )
    top_n = col2.number_input(
        "Top N",
        min_value=3,
        max_value=50,
        value=int(scanner_cfg.get("top_n", 10)),
    )
    persist = col3.checkbox("Persistir snapshots", value=True)

    if st.button("Ejecutar scan", type="primary", use_container_width=True):
        with st.spinner("Llamando a DexScreener y puntuando candidatos..."):
            try:
                with session_scope() as session:
                    repo = CryptoPumpRepository(session)
                    service = CryptoPumpRadarService(repository=repo, config=config)
                    result = service.scan(
                        chains=selected_chains or None,
                        persist=persist,
                        dry_run=not persist,
                    )
                    top_rows = result.top_n[: int(top_n)]
                    st.session_state["crypto_pump_last_scan"] = {
                        "frame": _candidates_to_frame(top_rows),
                        "candidates_total": len(result.candidates),
                        "rejected": result.rejected_count,
                        "chains_scanned": result.chains_scanned,
                        "errors": list(result.error_messages),
                    }
            except Exception as exc:  # defensive guard for the UI
                st.exception(exc)

    state = st.session_state.get("crypto_pump_last_scan")
    if state:
        info_cols = st.columns(3)
        info_cols[0].metric("Candidatos", state["candidates_total"])
        info_cols[1].metric("Descartados", state["rejected"])
        info_cols[2].metric("Chains", ", ".join(state["chains_scanned"]) or "-")
        frame = state["frame"]
        if frame.empty:
            st.info("Sin candidatos con los filtros actuales.")
        else:
            _draw_top_table(frame)
        if state.get("errors"):
            with st.expander("Errores del scan", expanded=False):
                for err in state["errors"]:
                    st.write(f"- {err}")
    else:
        st.info("Pulsa 'Ejecutar scan' para generar candidatos.")

# ---------------- Analisis manual --------------------------------------------

with tabs[1]:
    st.subheader("Buscar token / par concreto")
    query = st.text_input(
        "Simbolo, address, pair address o URL de DexScreener",
        placeholder="ej. PEPE, 0xabc..., dexscreener.com/ethereum/0x...",
    )
    persist_manual = st.checkbox("Persistir snapshot al consultar", value=True)
    if st.button("Buscar", use_container_width=True):
        if not query.strip():
            st.warning("Introduce un termino antes de buscar.")
        else:
            with st.spinner("Resolviendo en DexScreener..."):
                try:
                    with session_scope() as session:
                        repo = CryptoPumpRepository(session)
                        service = CryptoPumpRadarService(
                            repository=repo if persist_manual else None,
                            config=config,
                        )
                        rows = service.manual_lookup_persisted(query)
                        st.session_state["crypto_pump_manual_result"] = {
                            "query": query,
                            "rows": rows,
                        }
                except Exception as exc:
                    st.exception(exc)

    manual = st.session_state.get("crypto_pump_manual_result")
    if manual and manual.get("rows"):
        rows = manual["rows"]
        st.write(f"Resultados para `{manual['query']}` — {len(rows)} par(es)")
        frame = _candidates_to_frame(rows)
        _draw_top_table(frame)

        st.markdown("---")
        st.subheader("Detalle por par")
        labels = [
            f"{r.pair.symbol} ({r.pair.chain}) {r.pair.pair_address[:8]}"
            for r in rows
        ]
        selected = st.selectbox(
            "Selecciona par",
            options=range(len(rows)),
            format_func=lambda i: labels[i],
        )
        if selected is not None:
            chosen = rows[int(selected)]
            score_cols = st.columns(4)
            score_cols[0].metric("Final", chosen.score.final_speculative_score)
            score_cols[1].metric("Momentum", chosen.score.pump_momentum_score)
            score_cols[2].metric("Rug Risk", chosen.score.rug_risk_score)
            score_cols[3].metric("Clase", chosen.score.classification)
            with st.expander("Breakdown de scoring", expanded=False):
                st.json(chosen.score.breakdown)

            with session_scope() as session:
                repo = CryptoPumpRepository(session)
                history_service = CryptoPumpHistoryService(repo)
                report = history_service.build_report(
                    chain=chosen.pair.chain,
                    pair_address=chosen.pair.pair_address,
                    limit=int(
                        config.get("manual_lookup", {}).get("max_history_snapshots", 1000)
                    ),
                )
            st.markdown("### Historico persistido")
            _draw_history_charts(report)
    elif manual:
        st.info("Sin resultados para esa busqueda.")

# ---------------- Historico persistido (sin nueva busqueda) -------------------

with tabs[2]:
    st.subheader("Revisar par ya persistido en DB")
    with session_scope() as session:
        repo = CryptoPumpRepository(session)
        recent_runs = repo.list_recent_runs(limit=20)

    if not recent_runs:
        st.info(
            "Aun no hay scan_runs persistidos. Ejecuta el scanner o el job CLI: "
            "`python -m jobs.run_crypto_pump_scan`."
        )
    else:
        st.write("Scan runs recientes:")
        runs_frame = pd.DataFrame(
            [
                {
                    "id": r.id,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "status": r.status,
                    "chains": ", ".join(r.chains_scanned or []),
                    "candidates": r.candidates_found,
                }
                for r in recent_runs
            ]
        )
        st.dataframe(runs_frame, use_container_width=True, hide_index=True)

    col_chain, col_addr = st.columns(2)
    chain_input = col_chain.text_input("Chain", value="solana", key="hist_chain")
    pair_input = col_addr.text_input("Pair address", value="", key="hist_pair")
    if st.button("Reconstruir historico"):
        if not chain_input.strip() or not pair_input.strip():
            st.warning("Necesitas chain y pair address.")
        else:
            with session_scope() as session:
                repo = CryptoPumpRepository(session)
                history_service = CryptoPumpHistoryService(repo)
                report = history_service.build_report(
                    chain=chain_input.strip(),
                    pair_address=pair_input.strip(),
                )
            _draw_history_charts(report)
