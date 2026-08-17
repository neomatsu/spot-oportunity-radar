from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from sqlalchemy import select

from core.config import load_yaml_config  # noqa: E402
from data.database import (  # noqa: E402
    AssetDataStatusORM,
    SignalORM,
    TechnicalSnapshotORM,
    session_scope,
)
from data.repositories.assets_repo import AssetsRepository  # noqa: E402
from data.repositories.planned_entries_repo import PlannedEntriesRepository  # noqa: E402
from data.repositories.prices_repo import PricesRepository  # noqa: E402
from services.historical_score_service import HistoricalScoreService  # noqa: E402
from services.planned_entry_service import PlannedEntryService  # noqa: E402
from services.support_detection_service import SupportDetectionService  # noqa: E402
from services.technical_service import TechnicalService  # noqa: E402

RANGE_OPTIONS = {
    "Todo": None,
    "5 años": 365 * 5,
    "2 años": 365 * 2,
    "1 año": 365,
    "6 meses": 182,
    "3 meses": 91,
}
PLANNED_ENTRY_DEFAULTS = load_yaml_config("planned_entries.yaml").get(
    "planned_entries", {}
)

st.title("Asset Detail")

with session_scope() as session:
    assets = AssetsRepository(session).list_enabled()
    asset_options = {f"{asset.symbol} — {asset.name}": asset for asset in assets}
    selected_label = st.selectbox("Seleccionar activo", list(asset_options) if asset_options else [])

    if not selected_label:
        st.info("No hay activos disponibles.")
    else:
        asset = asset_options[selected_label]
        price_frame = PricesRepository(session).get_asset_prices(asset.id)
        technical = session.scalar(
            select(TechnicalSnapshotORM)
            .where(TechnicalSnapshotORM.asset_id == asset.id)
            .order_by(TechnicalSnapshotORM.date.desc())
            .limit(1)
        )
        signal = session.scalar(
            select(SignalORM)
            .where(SignalORM.asset_id == asset.id)
            .order_by(SignalORM.date.desc())
            .limit(1)
        )
        data_status = session.scalar(
            select(AssetDataStatusORM).where(AssetDataStatusORM.asset_id == asset.id).limit(1)
        )

        last_price = float(price_frame["close"].iloc[-1]) if not price_frame.empty else None
        last_price_date = price_frame["date"].iloc[-1] if not price_frame.empty else None
        planned_entries_repo = PlannedEntriesRepository(session)
        planned_levels = planned_entries_repo.list_for_asset(asset.id)

        # --- Cabecera del activo ---
        st.subheader(f"{asset.name} ({asset.symbol})")

        info_col1, info_col2, info_col3, info_col4 = st.columns(4)
        info_col1.metric("Tipo", asset.asset_type.upper())
        info_col2.metric("Sector", asset.sector)
        info_col3.metric(
            "Precio actual",
            f"{last_price:,.2f}" if last_price is not None else "N/A",
        )
        info_col4.metric(
            "Fecha precio",
            str(last_price_date)[:10] if last_price_date is not None else "N/A",
        )

        # --- Scores ---
        st.markdown("**Scores actuales**")
        score_col1, score_col2, score_col3, score_col4 = st.columns(4)
        score_col1.metric(
            "Technical score",
            f"{technical.technical_score:.1f}" if technical and technical.technical_score else "N/A",
        )
        score_col2.metric("Risk score", f"{signal.risk_score:.1f}" if signal else "N/A")
        score_col3.metric("Final score", f"{signal.final_score:.1f}" if signal else "N/A")
        score_col4.metric(
            "Recomendación",
            signal.recommendation if signal else "N/A",
        )

        # --- Estado de datos ---
        with st.expander("Estado de datos", expanded=False):
            ds_col1, ds_col2, ds_col3, ds_col4 = st.columns(4)
            ds_col1.metric("Frescura", data_status.freshness_status if data_status else "N/A")
            ds_col2.metric(
                "Última barra",
                str(data_status.last_available_bar_date)[:10]
                if data_status and data_status.last_available_bar_date else "N/A",
            )
            ds_col3.metric(
                "Histórico desde",
                str(data_status.historical_coverage_start)[:10]
                if data_status and data_status.historical_coverage_start else "N/A",
            )
            ds_col4.metric(
                "Modo / Fuente",
                f"{data_status.data_mode} / {data_status.last_refresh_source or '—'}"
                if data_status else "N/A",
            )

        # --- Plan de entradas manuales ---
        with st.expander("Plan de compras parciales", expanded=bool(planned_levels)):
            st.caption(
                "Añade varios niveles independientes. El job diario avisa una vez al entrar "
                "en la tolerancia y rearma el nivel cuando el precio vuelve a alejarse."
            )
            with st.form(f"planned_entry_form_{asset.id}", clear_on_submit=False):
                plan_col1, plan_col2, plan_col3 = st.columns(3)
                target_price = plan_col1.number_input(
                    "Precio objetivo",
                    min_value=0.000001,
                    value=float(last_price or 1.0),
                    format="%.6f",
                )
                suggested_weight_pct = plan_col2.number_input(
                    "% de capital sugerido",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(
                        PLANNED_ENTRY_DEFAULTS.get("default_suggested_weight_pct", 5.0)
                    ),
                    step=1.0,
                )
                suggested_capital = plan_col3.number_input(
                    "Capital sugerido (opcional)",
                    min_value=0.0,
                    value=0.0,
                    step=100.0,
                )
                rule_col1, rule_col2, rule_col3 = st.columns(3)
                tolerance_pct = rule_col1.number_input(
                    "Avisar a distancia (%)",
                    min_value=0.0,
                    value=float(
                        PLANNED_ENTRY_DEFAULTS.get("default_tolerance_pct", 1.0)
                    ),
                    step=0.25,
                )
                rearm_distance_pct = rule_col2.number_input(
                    "Rearmar al alejarse (%)",
                    min_value=0.1,
                    value=float(
                        PLANNED_ENTRY_DEFAULTS.get("default_rearm_distance_pct", 3.0)
                    ),
                    step=0.5,
                )
                use_expiry = rule_col3.checkbox("Usar fecha de expiración", value=False)
                expires_at = st.date_input(
                    "Expira el",
                    value=date.today() + timedelta(days=90),
                    disabled=not use_expiry,
                )
                notes = st.text_input("Notas / motivo del nivel", value="")
                create_level = st.form_submit_button(
                    "Añadir nivel de compra", type="primary", use_container_width=True
                )
            if create_level:
                try:
                    PlannedEntryService(session).create_level(
                        asset=asset,
                        target_price=target_price,
                        suggested_weight_pct=suggested_weight_pct or None,
                        suggested_capital=suggested_capital or None,
                        tolerance_pct=tolerance_pct,
                        rearm_distance_pct=rearm_distance_pct,
                        notes=notes,
                        expires_at=expires_at if use_expiry else None,
                    )
                    session.commit()
                    st.success("Nivel de compra añadido.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

            if planned_levels:
                plan_rows = []
                for level in planned_levels:
                    distance = (
                        PlannedEntryService.distance_pct(last_price, level.target_price)
                        if last_price is not None
                        else None
                    )
                    plan_rows.append(
                        {
                            "ID": level.id,
                            "Estado": level.status,
                            "Precio objetivo": level.target_price,
                            "Divisa": level.price_currency or asset.quote_currency or "N/A",
                            "Distancia %": round(distance, 2) if distance is not None else None,
                            "% sugerido": level.suggested_weight_pct,
                            "Capital sugerido": level.suggested_capital,
                            "Tolerancia %": level.tolerance_pct,
                            "Rearme %": level.rearm_distance_pct,
                            "Expira": level.expires_at,
                            "Notas": level.notes,
                        }
                    )
                st.dataframe(pd.DataFrame(plan_rows), use_container_width=True, hide_index=True)
                manage_col1, manage_col2, manage_col3 = st.columns([2, 2, 1])
                selected_level_id = manage_col1.selectbox(
                    "Gestionar nivel",
                    [level.id for level in planned_levels],
                    format_func=lambda level_id: next(
                        f"#{level.id} · {level.target_price:,.2f} · {level.status}"
                        for level in planned_levels
                        if level.id == level_id
                    ),
                    key=f"planned_entry_manage_{asset.id}",
                )
                selected_status = manage_col2.selectbox(
                    "Nuevo estado",
                    ["active", "paused", "executed_manually", "expired"],
                    key=f"planned_entry_status_{asset.id}",
                )
                if manage_col3.button(
                    "Aplicar", key=f"planned_entry_apply_{asset.id}", use_container_width=True
                ):
                    PlannedEntryService(session).set_status(selected_level_id, selected_status)
                    session.commit()
                    st.rerun()
                if st.button(
                    "Eliminar nivel seleccionado",
                    key=f"planned_entry_delete_{asset.id}",
                ):
                    planned_entries_repo.delete(selected_level_id)
                    session.commit()
                    st.rerun()

        if price_frame.empty:
            st.warning("No hay histórico de precios para este activo.")
        else:
            price_frame["date"] = pd.to_datetime(price_frame["date"])
            enriched = TechnicalService.compute_indicators(price_frame)
            support_service = SupportDetectionService()
            support_zones = support_service.detect_support_zone(enriched)

            # Selector de rango (junto al gráfico)
            range_label = st.selectbox(
                "Rango visible",
                list(RANGE_OPTIONS),
                index=2,
            )
            lookback_days = RANGE_OPTIONS[range_label]
            if lookback_days is None:
                display_frame = enriched.copy()
            else:
                cutoff = enriched["date"].max() - pd.Timedelta(days=lookback_days)
                display_frame = enriched[enriched["date"] >= cutoff].copy()

            # --- Gráfico de precio ---
            price_fig = go.Figure()
            price_fig.add_trace(
                go.Candlestick(
                    x=display_frame["date"],
                    open=display_frame["open"],
                    high=display_frame["high"],
                    low=display_frame["low"],
                    close=display_frame["close"],
                    name="OHLC",
                )
            )
            for label, color in [("ema20", "#f39c12"), ("sma50", "#3498db"), ("sma200", "#9b59b6")]:
                price_fig.add_trace(
                    go.Scatter(
                        x=display_frame["date"],
                        y=display_frame[label],
                        mode="lines",
                        name=label.upper(),
                        line=dict(color=color, width=1.5),
                    )
                )
            for band in support_service.build_zone_bands(support_zones):
                price_fig.add_hrect(**band)
            for level in planned_levels:
                if level.status not in {"active", "triggered"}:
                    continue
                band_color = (
                    "rgba(245, 158, 11, 0.20)"
                    if level.status == "active"
                    else "rgba(37, 99, 235, 0.18)"
                )
                price_fig.add_hrect(
                    y0=level.target_price,
                    y1=level.target_price * (1.0 + level.tolerance_pct / 100.0),
                    fillcolor=band_color,
                    line_width=1,
                    line_color="#d97706" if level.status == "active" else "#2563eb",
                    annotation_text=(
                        f"Plan compra {level.suggested_weight_pct:g}%"
                        if level.suggested_weight_pct
                        else "Plan compra"
                    ),
                    annotation_position="top left",
                )
            visible_low = float(display_frame["low"].min())
            visible_high = float(display_frame["high"].max())
            visible_range = max(visible_high - visible_low, visible_high * 0.02, 1.0)
            y_padding = visible_range * 0.08
            price_fig.update_layout(height=520, xaxis_rangeslider_visible=False)
            price_fig.update_yaxes(
                range=[max(0.0, visible_low - y_padding), visible_high + y_padding]
            )
            st.plotly_chart(price_fig, use_container_width=True)

            # --- RSI ---
            rsi_fig = go.Figure()
            rsi_fig.add_trace(
                go.Scatter(
                    x=display_frame["date"],
                    y=display_frame["rsi14"],
                    mode="lines",
                    name="RSI 14",
                    line=dict(color="#1f77b4"),
                )
            )
            rsi_fig.add_hline(y=30, line_dash="dash", line_color="green", annotation_text="30")
            rsi_fig.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="70")
            rsi_fig.update_layout(height=220, yaxis_title="RSI 14", margin=dict(t=30, b=20))
            st.plotly_chart(rsi_fig, use_container_width=True)

            # --- Soportes ---
            zone_rows = support_service.build_zone_table_rows(support_zones, current_price=last_price)
            if zone_rows:
                st.subheader("Soportes y resistencias")
                zone_frame = pd.DataFrame(zone_rows).rename(
                    columns={
                        "type": "Tipo",
                        "zone": "Zona",
                        "distance_pct": "Distancia %",
                        "score": "Score",
                        "touches": "Toques",
                        "method": "Método",
                    }
                )
                st.dataframe(zone_frame, use_container_width=True, hide_index=True)

            # --- Score histórico ---
            history_service = HistoricalScoreService(session)
            history_start = display_frame["date"].min().date()
            history_end = display_frame["date"].max().date()
            point_state_key = f"historical_score_point_{asset.id}"
            history_state_key = f"historical_score_history_{asset.id}_{range_label}"

            st.subheader("Score histórico")
            st.caption(
                "Cálculo bajo demanda con caché persistente. El portfolio fit histórico usa "
                "contexto neutral para no depender de la cartera actual."
            )

            point_col1, point_col2 = st.columns([2, 1])
            selected_history_date = point_col1.date_input(
                "Fecha para consultar score",
                value=history_end,
                min_value=price_frame["date"].min(),
                max_value=price_frame["date"].max(),
                key=f"historical_score_date_input_{asset.id}",
            )
            if point_col2.button(
                "Consultar score",
                key=f"historical_score_button_{asset.id}",
                use_container_width=True,
            ):
                with st.spinner("Calculando score histórico..."):
                    st.session_state[point_state_key] = history_service.get_score_as_of(
                        asset, as_of_date=selected_history_date,
                    )

            history_controls = st.columns([2, 1])
            history_controls[0].caption(f"Rango visible: {history_start} → {history_end}")
            if history_controls[1].button(
                "Ver evolución del score",
                key=f"historical_score_history_button_{asset.id}_{range_label}",
                use_container_width=True,
            ):
                with st.spinner("Calculando scores del período..."):
                    history_frame = history_service.get_score_history(
                        asset, start_date=history_start, end_date=history_end,
                    )
                    st.session_state[history_state_key] = history_frame.to_dict("records")

            point_snapshot = st.session_state.get(point_state_key)
            if point_snapshot:
                st.markdown("**Score en fecha seleccionada**")
                pm = st.columns(4)
                pm[0].metric("Fecha", str(point_snapshot["date"]))
                pm[1].metric("Technical", f'{float(point_snapshot["technical_score"]):.1f}')
                pm[2].metric("Risk", f'{float(point_snapshot["risk_score"]):.1f}')
                pm[3].metric("Final", f'{float(point_snapshot["final_score"]):.1f}')

                breakdown = point_snapshot.get("technical_payload_json", {}).get("breakdown", {})
                if breakdown:
                    st.markdown("**Desglose técnico**")
                    bd_cols = st.columns(4)
                    items = [(k, v) for k, v in breakdown.items() if isinstance(v, (int, float))]
                    for i, (k, v) in enumerate(items):
                        bd_cols[i % 4].metric(k.replace("_", " ").title(), f"{v:.1f}")

                signal_json = point_snapshot.get("signal_payload_json", {})
                if signal_json:
                    reasons = signal_json.get("reasons", [])
                    if reasons:
                        st.markdown("**Razones históricas**")
                        for r in reasons:
                            st.write(f"- {r}")

            cached_history_rows = st.session_state.get(history_state_key)
            if cached_history_rows:
                history_frame = pd.DataFrame(cached_history_rows)
                history_frame["date"] = pd.to_datetime(history_frame["date"])
                score_fig = make_subplots(specs=[[{"secondary_y": True}]])
                score_fig.add_trace(
                    go.Scatter(x=history_frame["date"], y=history_frame["final_score"],
                               mode="lines", name="Final score",
                               line=dict(color="#1f77b4", width=2)), secondary_y=False,
                )
                score_fig.add_trace(
                    go.Scatter(x=history_frame["date"], y=history_frame["technical_score"],
                               mode="lines", name="Technical score",
                               line=dict(color="#2ca02c", width=1.8)), secondary_y=False,
                )
                score_fig.add_trace(
                    go.Scatter(x=history_frame["date"], y=history_frame["risk_score"],
                               mode="lines", name="Risk score",
                               line=dict(color="#d62728", width=1.8)), secondary_y=False,
                )
                score_price_frame = display_frame[
                    display_frame["date"].dt.date.between(history_start, history_end)
                ][["date", "close"]].copy()
                score_fig.add_trace(
                    go.Scatter(x=score_price_frame["date"], y=score_price_frame["close"],
                               mode="lines", name="Precio",
                               line=dict(color="#7f7f7f", width=1.4, dash="dot")),
                    secondary_y=True,
                )
                score_fig.update_layout(height=360, margin=dict(t=40, b=20))
                score_fig.update_yaxes(title_text="Score", range=[0, 100], secondary_y=False)
                score_fig.update_yaxes(title_text="Precio", secondary_y=True)
                st.plotly_chart(score_fig, use_container_width=True)

        # --- Por qué es interesante ---
        if signal:
            reasons = signal.rationale_json.get("reasons", [])
            if reasons:
                st.subheader("Por qué es interesante")
                for reason in reasons:
                    st.write(f"— {reason}")

            # Score breakdown como métricas estructuradas
            st.subheader("Desglose del score")
            breakdown = signal.rationale_json.get("score_breakdown", {})
            if breakdown:
                bd_items = [(k, v) for k, v in breakdown.items() if isinstance(v, (int, float))]
                bd_cols = st.columns(min(len(bd_items), 4))
                for i, (k, v) in enumerate(bd_items):
                    bd_cols[i % 4].metric(k.replace("_", " ").title(), f"{v:.1f}")
            else:
                breakdown_alt = signal.rationale_json.get("breakdown", {})
                if breakdown_alt:
                    bd_items = [(k, v) for k, v in breakdown_alt.items() if isinstance(v, (int, float))]
                    bd_cols = st.columns(min(len(bd_items), 4))
                    for i, (k, v) in enumerate(bd_items):
                        bd_cols[i % 4].metric(k.replace("_", " ").title(), f"{v:.1f}")

            inv_col, _ = st.columns([1, 1])
            invalidation = signal.rationale_json.get("invalidation")
            if invalidation:
                with inv_col:
                    st.warning(f"**Invalidación:** {invalidation}")

        # --- Snapshot técnico y recomendación en columnas colapsadas ---
        if technical or signal:
            detail_col1, detail_col2 = st.columns(2)
            if technical:
                with detail_col1:
                    with st.expander("Indicadores técnicos", expanded=False):
                        ind_data = {
                            "RSI 14": f"{technical.rsi14:.1f}" if technical.rsi14 else "N/A",
                            "SMA 50": f"{technical.sma50:.2f}" if technical.sma50 else "N/A",
                            "SMA 200": f"{technical.sma200:.2f}" if technical.sma200 else "N/A",
                            "EMA 20": f"{technical.ema20:.2f}" if technical.ema20 else "N/A",
                            "ATR 14": f"{technical.atr14:.2f}" if technical.atr14 else "N/A",
                            "Soporte bajo": f"{technical.support_low:.2f}" if technical.support_low else "N/A",
                            "Soporte alto": f"{technical.support_high:.2f}" if technical.support_high else "N/A",
                            "Dist. soporte %": f"{technical.distance_to_support_pct:.1f}%" if technical.distance_to_support_pct else "N/A",
                        }
                        for label, value in ind_data.items():
                            st.markdown(f"**{label}:** {value}")
            if signal:
                with detail_col2:
                    with st.expander("Detalle de la recomendación", expanded=False):
                        rec_data = {
                            "Recomendación": signal.recommendation,
                            "Compra mín.": f"{signal.suggested_buy_low:.2f}" if signal.suggested_buy_low else "N/A",
                            "Compra máx.": f"{signal.suggested_buy_high:.2f}" if signal.suggested_buy_high else "N/A",
                            "Peso sugerido": f"{signal.suggested_weight_add:.1%}" if signal.suggested_weight_add else "N/A",
                            "Risk score": f"{signal.risk_score:.1f}" if signal.risk_score else "N/A",
                            "Nivel de riesgo": signal.rationale_json.get("risk_level", "N/A"),
                            "Portfolio fit": f"{signal.rationale_json.get('portfolio_fit_score', 'N/A')}",
                        }
                        for label, value in rec_data.items():
                            st.markdown(f"**{label}:** {value}")
