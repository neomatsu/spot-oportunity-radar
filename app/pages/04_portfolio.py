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
from data.repositories.prices_repo import PricesRepository  # noqa: E402
from services.portfolio_service import PortfolioService  # noqa: E402

st.title("Portfolio")

with session_scope() as session:
    assets = AssetsRepository(session).list_enabled()
    repo = PortfolioRepository(session)
    portfolio_service = PortfolioService(session)
    symbol_to_asset = {asset.symbol: asset for asset in assets}
    asset_symbols = list(symbol_to_asset)

    selected_symbol = st.selectbox("Activo", asset_symbols if asset_symbols else [])
    selected_asset = symbol_to_asset.get(selected_symbol) if selected_symbol else None
    existing_position = (
        repo.get_by_asset_id(selected_asset.id) if selected_asset is not None else None
    )

    with st.form("add_position"):
        st.markdown("**Editar posición**")
        form_col1, form_col2 = st.columns(2)
        quantity = form_col1.number_input(
            "Cantidad",
            min_value=0.0,
            step=1.0,
            value=float(existing_position.quantity) if existing_position else 0.0,
        )
        avg_cost = form_col2.number_input(
            "Precio medio de compra",
            min_value=0.0,
            step=1.0,
            value=float(existing_position.avg_cost) if existing_position else 0.0,
        )
        # Pesos como porcentaje (0-100%) para el usuario; almacenados internamente como 0-1
        current_weight_pct = form_col1.number_input(
            "Peso actual (%)",
            min_value=0.0,
            max_value=100.0,
            step=0.5,
            value=float(existing_position.current_weight) * 100 if existing_position else 0.0,
            help="Porcentaje del total de la cartera (0–100).",
        )
        target_weight_pct = form_col2.number_input(
            "Peso objetivo (%)",
            min_value=0.0,
            max_value=100.0,
            step=0.5,
            value=float(existing_position.target_weight) * 100 if existing_position else 0.0,
            help="Porcentaje objetivo del total de la cartera (0–100).",
        )
        action_col1, action_col2 = st.columns(2)
        submitted = action_col1.form_submit_button("Guardar posición", use_container_width=True, type="primary")
        delete_clicked = action_col2.form_submit_button(
            "Eliminar posición",
            use_container_width=True,
            disabled=existing_position is None,
        )

        if submitted and selected_asset:
            repo.upsert_position(
                asset_id=selected_asset.id,
                quantity=quantity,
                avg_cost=avg_cost,
                current_weight=current_weight_pct / 100.0,
                target_weight=target_weight_pct / 100.0,
            )
            st.success(f"Posición guardada para {selected_symbol}.")

        if delete_clicked and selected_asset:
            repo.delete_position(selected_asset.id)
            st.success(f"Posición eliminada para {selected_symbol}.")

    rows = session.execute(
        select(PortfolioPositionORM, AssetORM)
        .join(AssetORM, AssetORM.id == PortfolioPositionORM.asset_id)
        .order_by(AssetORM.symbol)
    ).all()
    exposure = portfolio_service.get_exposures()

    # Obtener precios actuales para calcular P&L
    prices_repo = PricesRepository(session)
    current_prices: dict[str, float] = {}
    for _, asset in rows:
        price_frame = prices_repo.get_asset_prices(asset.id)
        if not price_frame.empty:
            current_prices[asset.symbol] = float(price_frame["close"].iloc[-1])

positions_df = pd.DataFrame(
    [
        {
            "Símbolo": asset.symbol,
            "Tipo": asset.asset_type,
            "Sector": asset.sector,
            "Cantidad": position.quantity,
            "Coste medio": position.avg_cost,
            "Precio actual": current_prices.get(asset.symbol),
            "Peso actual %": round(float(position.current_weight) * 100, 1),
            "Peso objetivo %": round(float(position.target_weight) * 100, 1),
        }
        for position, asset in rows
    ]
)

# P&L calculado fuera del df construction para facilitar lectura
if not positions_df.empty and "Precio actual" in positions_df.columns:
    positions_df["P&L"] = (
        (positions_df["Precio actual"] - positions_df["Coste medio"])
        * positions_df["Cantidad"]
    ).round(2)
    positions_df["P&L %"] = (
        (positions_df["Precio actual"] / positions_df["Coste medio"] - 1) * 100
    ).round(2)

if positions_df.empty:
    st.info("No hay posiciones cargadas todavía.")
else:
    top_cols = st.columns(4)
    top_cols[0].metric("Posiciones", len(positions_df))
    top_cols[1].metric("Peso invertido", f"{exposure.total_invested_weight * 100:.1f}%")
    top_cols[2].metric("Cash estimado", f"{(1 - exposure.total_invested_weight) * 100:.1f}%")
    top_cols[3].metric(
        "Mayor posición",
        f"{max(exposure.by_asset.values()) * 100:.1f}%" if exposure.by_asset else "N/A",
    )

    st.dataframe(
        positions_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "P&L": st.column_config.NumberColumn("P&L", format="%.2f"),
            "P&L %": st.column_config.NumberColumn("P&L %", format="%.2f%%"),
            "Coste medio": st.column_config.NumberColumn("Coste medio", format="%.2f"),
            "Precio actual": st.column_config.NumberColumn("Precio actual", format="%.2f"),
            "Peso actual %": st.column_config.NumberColumn("Peso actual %", format="%.1f%%"),
            "Peso objetivo %": st.column_config.NumberColumn("Peso objetivo %", format="%.1f%%"),
        },
    )

    asset_df = pd.DataFrame(
        [{"symbol": s, "weight": w} for s, w in exposure.by_asset.items()]
    )
    sector_df = pd.DataFrame(
        [{"sector": s, "weight": w} for s, w in exposure.by_sector.items()]
    )
    type_df = pd.DataFrame(
        [{"asset_type": t, "weight": w} for t, w in exposure.by_asset_type.items()]
    )

    chart_col1, chart_col2, chart_col3 = st.columns(3)
    if not asset_df.empty:
        chart_col1.plotly_chart(
            px.pie(
                asset_df,
                names="symbol",
                values="weight",
                title="Distribución por activo",
                color_discrete_sequence=px.colors.qualitative.Set2,
            ),
            use_container_width=True,
        )
    if not sector_df.empty:
        chart_col2.plotly_chart(
            px.pie(
                sector_df,
                names="sector",
                values="weight",
                title="Distribución por sector",
                color_discrete_sequence=px.colors.qualitative.Pastel,
            ),
            use_container_width=True,
        )
    if not type_df.empty:
        chart_col3.plotly_chart(
            px.bar(
                type_df,
                x="asset_type",
                y="weight",
                title="Distribución por clase",
                labels={"asset_type": "Clase", "weight": "Peso"},
                color="asset_type",
                color_discrete_sequence=px.colors.qualitative.Set2,
            ),
            use_container_width=True,
        )

    st.subheader("Alertas de concentración")
    warnings: list[str] = []
    for symbol, weight in exposure.by_asset.items():
        if weight >= 0.10:
            warnings.append(f"{symbol}: peso elevado ({weight * 100:.1f}%) — considera no añadir más.")
    for sector, weight in exposure.by_sector.items():
        if weight >= 0.25:
            warnings.append(f"Sector {sector}: concentración elevada ({weight * 100:.1f}%).")
    if len(exposure.by_sector) < 3:
        warnings.append("La cartera tiene poca diversificación sectorial (menos de 3 sectores).")

    if warnings:
        for item in warnings:
            st.warning(item)
    else:
        st.success("Sin concentraciones preocupantes con las reglas actuales.")

# --- Portfolio demo (colapsado para evitar clicks accidentales) ---
with st.expander("Datos de prueba", expanded=False):
    st.caption("Carga un portfolio de ejemplo para explorar la interfaz.")
    if st.button("Cargar portfolio demo", use_container_width=True):
        with session_scope() as session:
            assets_inner = AssetsRepository(session).list_enabled()
            repo_inner = PortfolioRepository(session)
            symbol_map = {a.symbol: a for a in assets_inner}
            demo_weights = {
                "MSFT": (12, 390, 0.10, 0.08),
                "SPY": (20, 500, 0.18, 0.20),
                "BTCUSDT": (0.15, 58000, 0.06, 0.05),
            }
            for sym, (qty, cost, cw, tw) in demo_weights.items():
                if sym in symbol_map:
                    repo_inner.upsert_position(
                        asset_id=symbol_map[sym].id,
                        quantity=qty,
                        avg_cost=cost,
                        current_weight=cw,
                        target_weight=tw,
                    )
        st.success("Portfolio demo cargado. Recarga la página para ver los cambios.")
