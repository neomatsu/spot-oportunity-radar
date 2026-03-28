from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from data.database import (  # noqa: E402
    AssetDataStatusORM,
    SignalORM,
    TechnicalSnapshotORM,
    session_scope,
)
from data.repositories.assets_repo import AssetsRepository  # noqa: E402
from data.repositories.prices_repo import PricesRepository  # noqa: E402
from services.technical_service import TechnicalService  # noqa: E402

st.title("Asset Detail")

with session_scope() as session:
    assets = AssetsRepository(session).list_enabled()
    asset_options = {f"{asset.symbol} - {asset.name}": asset for asset in assets}
    selected_label = st.selectbox("Activo", list(asset_options) if asset_options else [])

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

        st.subheader(f"{asset.name} ({asset.symbol})")

        metric_cols = st.columns(8)
        metric_cols[0].metric(
            "Precio actual",
            f"{last_price:,.2f}" if last_price is not None else "N/A",
        )
        metric_cols[1].metric(
            "Fecha precio",
            str(last_price_date) if last_price_date is not None else "N/A",
        )
        metric_cols[2].metric("Tipo", asset.asset_type.upper())
        metric_cols[3].metric("Sector", asset.sector)
        metric_cols[4].metric(
            "Technical score",
            (
                f"{technical.technical_score:.1f}"
                if technical and technical.technical_score
                else "N/A"
            ),
        )
        metric_cols[5].metric("Risk score", f"{signal.risk_score:.1f}" if signal else "N/A")
        metric_cols[6].metric("Final score", f"{signal.final_score:.1f}" if signal else "N/A")
        metric_cols[7].metric(
            "Origen datos",
            (
                f"{data_status.data_mode} / {data_status.last_refresh_source}"
                if data_status and data_status.last_refresh_source
                else (data_status.data_mode if data_status else "unknown")
            ),
        )

        status_cols = st.columns(2)
        status_cols[0].metric(
            "Freshness", data_status.freshness_status if data_status else "missing"
        )
        status_cols[1].metric(
            "Ultima barra",
            (
                str(data_status.last_available_bar_date)
                if data_status and data_status.last_available_bar_date
                else "N/A"
            ),
        )

        if price_frame.empty:
            st.warning("No hay historico de precios para este activo.")
        else:
            price_frame["date"] = pd.to_datetime(price_frame["date"])
            enriched = TechnicalService.compute_indicators(price_frame)

            price_fig = go.Figure()
            price_fig.add_trace(
                go.Candlestick(
                    x=enriched["date"],
                    open=enriched["open"],
                    high=enriched["high"],
                    low=enriched["low"],
                    close=enriched["close"],
                    name="OHLC",
                )
            )
            for label in ["ema20", "sma50", "sma200"]:
                price_fig.add_trace(
                    go.Scatter(
                        x=enriched["date"],
                        y=enriched[label],
                        mode="lines",
                        name=label.upper(),
                    )
                )
            if technical and technical.support_low and technical.support_high:
                price_fig.add_hrect(
                    y0=technical.support_low,
                    y1=technical.support_high,
                    fillcolor="LightGreen",
                    opacity=0.18,
                    line_width=0,
                )
            price_fig.update_layout(height=520, xaxis_rangeslider_visible=False)
            st.plotly_chart(price_fig, use_container_width=True)

            rsi_fig = go.Figure()
            rsi_fig.add_trace(
                go.Scatter(x=enriched["date"], y=enriched["rsi14"], mode="lines", name="RSI 14")
            )
            rsi_fig.add_hline(y=30, line_dash="dash", line_color="green")
            rsi_fig.add_hline(y=70, line_dash="dash", line_color="red")
            rsi_fig.update_layout(height=220, yaxis_title="RSI", margin=dict(t=30, b=20))
            st.plotly_chart(rsi_fig, use_container_width=True)

        if signal:
            st.subheader("Why this is interesting")
            for reason in signal.rationale_json.get("reasons", []):
                st.write(f"- {reason}")

            detail_col1, detail_col2 = st.columns(2)
            with detail_col1:
                st.subheader("Score breakdown")
                st.json(signal.rationale_json.get("score_breakdown", {}))
            with detail_col2:
                st.subheader("Invalidation")
                st.warning(signal.rationale_json.get("invalidation", "No definida"))

        info_col1, info_col2 = st.columns(2)
        if technical:
            with info_col1:
                st.subheader("Technical snapshot")
                st.json(
                    {
                        "rsi14": technical.rsi14,
                        "sma50": technical.sma50,
                        "sma200": technical.sma200,
                        "ema20": technical.ema20,
                        "atr14": technical.atr14,
                        "support_low": technical.support_low,
                        "support_high": technical.support_high,
                        "distance_to_support_pct": technical.distance_to_support_pct,
                        "breakdown": technical.rationale_json.get("breakdown", {}),
                        "reasons": technical.rationale_json.get("reasons", []),
                    }
                )
        if signal:
            with info_col2:
                st.subheader("Recommendation")
                st.json(
                    {
                        "recommendation": signal.recommendation,
                        "suggested_buy_low": signal.suggested_buy_low,
                        "suggested_buy_high": signal.suggested_buy_high,
                        "suggested_weight_add": signal.suggested_weight_add,
                        "risk_score": signal.risk_score,
                        "risk_level": signal.rationale_json.get("risk_level"),
                        "portfolio_fit_score": signal.rationale_json.get("portfolio_fit_score"),
                        "rationale": signal.rationale_json,
                    }
                )
