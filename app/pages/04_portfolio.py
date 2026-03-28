from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import select

from data.database import AssetORM, PortfolioPositionORM, session_scope  # noqa: E402
from data.repositories.assets_repo import AssetsRepository  # noqa: E402
from data.repositories.portfolio_repo import PortfolioRepository  # noqa: E402
from services.portfolio_service import PortfolioService  # noqa: E402

st.title("Portfolio")

with session_scope() as session:
    assets = AssetsRepository(session).list_enabled()
    repo = PortfolioRepository(session)
    portfolio_service = PortfolioService(session)
    symbol_to_asset = {asset.symbol: asset for asset in assets}
    asset_symbols = list(symbol_to_asset)

    if st.button("Cargar portfolio demo", use_container_width=True):
        demo_weights = {
            "MSFT": (12, 390, 0.10, 0.08),
            "SPY": (20, 500, 0.18, 0.20),
            "BTCUSDT": (0.15, 58000, 0.06, 0.05),
        }
        for asset in assets:
            if asset.symbol in demo_weights:
                quantity, avg_cost, current_weight, target_weight = demo_weights[asset.symbol]
                repo.upsert_position(
                    asset_id=asset.id,
                    quantity=quantity,
                    avg_cost=avg_cost,
                    current_weight=current_weight,
                    target_weight=target_weight,
                )
        st.success("Portfolio demo cargado.")

    selected_symbol = st.selectbox("Activo", asset_symbols if asset_symbols else [])
    selected_asset = symbol_to_asset.get(selected_symbol) if selected_symbol else None
    existing_position = (
        repo.get_by_asset_id(selected_asset.id) if selected_asset is not None else None
    )

    with st.form("add_position"):
        quantity = st.number_input(
            "Cantidad",
            min_value=0.0,
            step=1.0,
            value=float(existing_position.quantity) if existing_position else 0.0,
        )
        avg_cost = st.number_input(
            "Precio medio",
            min_value=0.0,
            step=1.0,
            value=float(existing_position.avg_cost) if existing_position else 0.0,
        )
        current_weight = st.number_input(
            "Peso actual",
            min_value=0.0,
            max_value=1.0,
            step=0.01,
            value=float(existing_position.current_weight) if existing_position else 0.0,
        )
        target_weight = st.number_input(
            "Peso objetivo",
            min_value=0.0,
            max_value=1.0,
            step=0.01,
            value=float(existing_position.target_weight) if existing_position else 0.0,
        )
        action_col1, action_col2 = st.columns(2)
        submitted = action_col1.form_submit_button(
            "Guardar posicion",
            use_container_width=True,
        )
        delete_clicked = action_col2.form_submit_button(
            "Eliminar posicion",
            use_container_width=True,
            disabled=existing_position is None,
        )

        if submitted and selected_asset:
            repo.upsert_position(
                asset_id=selected_asset.id,
                quantity=quantity,
                avg_cost=avg_cost,
                current_weight=current_weight,
                target_weight=target_weight,
            )
            st.success(f"Posicion guardada para {selected_symbol}.")

        if delete_clicked and selected_asset:
            repo.delete_position(selected_asset.id)
            st.success(f"Posicion eliminada para {selected_symbol}.")

    rows = session.execute(
        select(PortfolioPositionORM, AssetORM)
        .join(AssetORM, AssetORM.id == PortfolioPositionORM.asset_id)
        .order_by(AssetORM.symbol)
    ).all()
    exposure = portfolio_service.get_exposures()

positions_df = pd.DataFrame(
    [
        {
            "symbol": asset.symbol,
            "asset_type": asset.asset_type,
            "sector": asset.sector,
            "quantity": position.quantity,
            "avg_cost": position.avg_cost,
            "current_weight": position.current_weight,
            "target_weight": position.target_weight,
        }
        for position, asset in rows
    ]
)

if positions_df.empty:
    st.info("No hay posiciones cargadas todavia.")
else:
    top_cols = st.columns(4)
    top_cols[0].metric("Posiciones", len(positions_df))
    top_cols[1].metric("Peso invertido", f"{exposure.total_invested_weight * 100:.1f}%")
    top_cols[2].metric("Cash estimado", f"{(1 - exposure.total_invested_weight) * 100:.1f}%")
    top_cols[3].metric(
        "Mayor posicion",
        f"{max(exposure.by_asset.values()) * 100:.1f}%" if exposure.by_asset else "N/A",
    )

    st.dataframe(positions_df, use_container_width=True, hide_index=True)

    asset_df = pd.DataFrame(
        [{"symbol": symbol, "weight": weight} for symbol, weight in exposure.by_asset.items()]
    )
    sector_df = pd.DataFrame(
        [{"sector": sector, "weight": weight} for sector, weight in exposure.by_sector.items()]
    )
    type_df = pd.DataFrame(
        [
            {"asset_type": asset_type, "weight": weight}
            for asset_type, weight in exposure.by_asset_type.items()
        ]
    )

    chart_col1, chart_col2, chart_col3 = st.columns(3)
    if not asset_df.empty:
        chart_col1.plotly_chart(
            px.pie(asset_df, names="symbol", values="weight", title="Distribucion por activo"),
            use_container_width=True,
        )
    if not sector_df.empty:
        chart_col2.plotly_chart(
            px.pie(sector_df, names="sector", values="weight", title="Distribucion por sector"),
            use_container_width=True,
        )
    if not type_df.empty:
        chart_col3.plotly_chart(
            px.bar(type_df, x="asset_type", y="weight", title="Distribucion por clase"),
            use_container_width=True,
        )

    st.subheader("Concentration checks")
    warnings: list[str] = []
    for symbol, weight in exposure.by_asset.items():
        if weight >= 0.10:
            warnings.append(
                f"Reducir o no anadir mas a {symbol}: "
                f"peso ya elevado ({weight * 100:.1f}%)."
            )
    for sector, weight in exposure.by_sector.items():
        if weight >= 0.25:
            warnings.append(f"Sector {sector} bastante cargado ({weight * 100:.1f}%).")
    if len(exposure.by_sector) < 3:
        warnings.append("La cartera tiene poca diversificacion sectorial.")

    if warnings:
        for item in warnings:
            st.write(f"- {item}")
    else:
        st.success("La cartera no muestra concentraciones preocupantes con las reglas actuales.")
