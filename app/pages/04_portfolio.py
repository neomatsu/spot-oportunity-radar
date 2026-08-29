from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from data.database import session_scope  # noqa: E402
from data.repositories.assets_repo import AssetsRepository  # noqa: E402
from data.repositories.portfolio_repo import PortfolioRepository  # noqa: E402
from services.broker_import_service import BrokerImportService  # noqa: E402
from services.portfolio_service import PortfolioService  # noqa: E402

st.title("Portfolio")
st.caption(
    "Gestiona la cartera por movimientos de compra/venta. Las posiciones agregadas se "
    "recalculan automaticamente a partir de las transacciones."
)


def _money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def _render_new_transaction(asset_symbols: list[str], symbol_to_asset: dict) -> None:
    st.caption(
        "Entrada manual de respaldo. Para el uso habitual se recomienda importar las "
        "operaciones desde Trade Republic o Kraken."
    )
    tx_col1, tx_col2 = st.columns(2)
    selected_symbol = tx_col1.selectbox("Activo", asset_symbols)
    transaction_type = tx_col2.selectbox("Tipo de movimiento", ["BUY", "SELL"])
    selected_asset = symbol_to_asset[selected_symbol]

    date_col, mode_col = st.columns(2)
    transaction_date = date_col.date_input("Fecha", value=pd.Timestamp.today().date())
    input_mode = mode_col.radio(
        "Modo de entrada",
        ["Importe", "Unidades"],
        horizontal=True,
    )

    with session_scope() as session:
        transaction_service = PortfolioService(session)
        estimated_price, price_source, price_date = transaction_service.price_for_date(
            selected_asset.id,
            transaction_date,
        )
        available_quantity = transaction_service.available_quantity(selected_asset.id)

    if estimated_price is None:
        st.warning("No hay precio historico para esa fecha. Introduce el precio manualmente.")
    else:
        st.info(
            f"Precio sugerido: {_money(estimated_price)} "
            f"({price_source}, fecha usada: {price_date})"
        )

    with st.form("portfolio_transaction_form"):
        sell_all = False
        if transaction_type == "SELL":
            sell_all = st.checkbox(
                "Vender toda la posicion disponible",
                value=False,
                help=f"Unidades disponibles: {available_quantity:.6f}",
            )
        form_col1, form_col2, form_col3 = st.columns(3)
        price = form_col1.number_input(
            "Precio aplicado",
            min_value=0.0,
            value=float(estimated_price or 0.0),
            step=0.01,
            help="Puedes editarlo para reflejar el precio exacto de la operacion.",
        )
        gross_amount = None
        quantity = None
        if input_mode == "Importe":
            gross_amount = form_col2.number_input(
                "Importe bruto",
                min_value=0.0,
                value=(available_quantity * price if sell_all and price > 0 else 0.0),
                step=100.0,
                disabled=sell_all,
            )
            calculated_quantity = (
                available_quantity if sell_all else gross_amount / price if price > 0 else 0.0
            )
            form_col3.metric("Unidades calculadas", f"{calculated_quantity:.6f}")
        else:
            quantity = form_col2.number_input(
                "Unidades",
                min_value=0.0,
                value=available_quantity if sell_all else 0.0,
                step=0.01,
                disabled=sell_all,
            )
            calculated_amount = quantity * price
            form_col3.metric("Importe calculado", _money(calculated_amount))

        fees = st.number_input("Comisiones", min_value=0.0, value=0.0, step=1.0)
        notes = st.text_input("Notas", value="")
        submitted = st.form_submit_button("Guardar movimiento", type="primary")

        if submitted:
            try:
                with session_scope() as session:
                    service = PortfolioService(session)
                    service.add_transaction_and_recalculate(
                        asset_id=selected_asset.id,
                        transaction_type=transaction_type,
                        transaction_date=transaction_date,
                        quantity=(
                            available_quantity
                            if sell_all
                            else quantity if input_mode == "Unidades" else None
                        ),
                        gross_amount=(
                            None
                            if sell_all
                            else gross_amount if input_mode == "Importe" else None
                        ),
                        price=price,
                        fees=fees,
                        price_source=price_source if estimated_price is not None else "manual",
                        notes=notes or None,
                    )
                st.success("Movimiento guardado y cartera recalculada.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


with session_scope() as session:
    assets = AssetsRepository(session).list_enabled()
    portfolio_service = PortfolioService(session)
    repo = PortfolioRepository(session)
    symbol_to_asset = {asset.symbol: asset for asset in assets}
    asset_symbols = list(symbol_to_asset)
    total_capital = portfolio_service.get_total_capital(default=0.0)

capital_col, recalc_col = st.columns([3, 1])
with capital_col.form("portfolio_capital_form"):
    new_total_capital = st.number_input(
        "Capital total disponible para invertir",
        min_value=0.0,
        value=float(total_capital),
        step=1000.0,
        help="Se usa para calcular automaticamente el peso actual de cada posicion.",
    )
    save_capital = st.form_submit_button("Guardar capital total", type="primary")
    if save_capital:
        with session_scope() as session:
            PortfolioService(session).set_total_capital(new_total_capital)
        st.success("Capital total actualizado y pesos recalculados.")
        st.rerun()

if recalc_col.button("Recalcular cartera", use_container_width=True):
    with session_scope() as session:
        PortfolioService(session).recalculate_positions()
    st.success("Cartera recalculada desde movimientos.")
    st.rerun()

with st.expander("Importar operaciones de Trade Republic", expanded=False):
    st.caption(
        "Se importan exclusivamente filas TRADING de tipo BUY o SELL. El ISIN se "
        "asocia a un activo de seguimiento y transaction_id evita duplicados."
    )
    uploaded_file = st.file_uploader(
        "CSV exportado por Trade Republic",
        type=["csv"],
        key="trade_republic_csv",
    )
    if uploaded_file is not None:
        try:
            csv_content = uploaded_file.getvalue()
            with session_scope() as session:
                import_service = BrokerImportService(session)
                broker_transactions = import_service.parse_trade_republic(csv_content)
                resolved_asset_ids = import_service.resolve_asset_ids(broker_transactions)
                duplicate_ids = import_service.duplicate_transaction_ids(broker_transactions)

            if not broker_transactions:
                st.warning("El fichero no contiene operaciones TRADING BUY/SELL.")
            else:
                asset_id_to_symbol = {asset.id: asset.symbol for asset in assets}
                external_assets = {
                    item.external_asset_id: item for item in broker_transactions
                }
                selected_mappings: dict[str, int] = {}
                st.markdown("**Mapeo de activos**")
                for external_id, item in external_assets.items():
                    mapped_asset_id = resolved_asset_ids.get(external_id)
                    mapped_symbol = asset_id_to_symbol.get(mapped_asset_id or -1)
                    if mapped_symbol:
                        st.success(f"{external_id} · {item.external_name} → {mapped_symbol}")
                        selected_mappings[external_id] = mapped_asset_id  # type: ignore[assignment]
                        continue
                    selected_symbol = st.selectbox(
                        f"{external_id} · {item.external_name}",
                        options=[""] + asset_symbols,
                        format_func=lambda value: value or "Sin mapear (omitir operaciones)",
                        key=f"trade_republic_mapping_{external_id}",
                    )
                    if selected_symbol:
                        selected_mappings[external_id] = symbol_to_asset[selected_symbol].id

                preview_rows = []
                for item in broker_transactions:
                    mapped_id = selected_mappings.get(item.external_asset_id)
                    if item.external_transaction_id in duplicate_ids:
                        status = "Duplicada"
                    elif mapped_id is None:
                        status = "Sin mapear"
                    elif item.currency != "EUR":
                        status = f"Moneda no soportada ({item.currency})"
                    else:
                        status = "Lista para importar"
                    preview_rows.append(
                        {
                            "Fecha": item.transaction_date,
                            "Tipo": item.transaction_type,
                            "ISIN": item.external_asset_id,
                            "Activo": asset_id_to_symbol.get(mapped_id or -1, "N/A"),
                            "Unidades": item.quantity,
                            "Precio": item.price,
                            "Importe": item.gross_amount,
                            "Comisión": item.fees,
                            "Impuestos": item.taxes,
                            "Estado": status,
                            "Transaction ID": item.external_transaction_id,
                        }
                    )
                st.dataframe(pd.DataFrame(preview_rows), hide_index=True, use_container_width=True)

                if st.button("Confirmar importación", type="primary"):
                    with session_scope() as session:
                        summary = BrokerImportService(session).import_trade_republic(
                            broker_transactions,
                            manual_mappings=selected_mappings,
                        )
                    st.success(
                        f"Importadas: {summary.imported} · Duplicadas: {summary.duplicates} · "
                        f"Sin mapear: {summary.unmapped} · Inválidas: {summary.invalid}"
                    )
                    for error in summary.errors:
                        st.warning(error)
        except ValueError as exc:
            st.error(str(exc))

with st.expander("Importar operaciones spot de Kraken", expanded=False):
    st.caption(
        "Se importan ejecuciones BUY/SELL del historial de trades spot. `txid` evita "
        "duplicados y los importes USDC/USDT se convierten a EUR usando el cambio "
        "USD/EUR de la fecha, asumiendo paridad 1:1 con USD."
    )
    kraken_file = st.file_uploader(
        "CSV de historial de operaciones de Kraken",
        type=["csv"],
        key="kraken_spot_csv",
    )
    if kraken_file is not None:
        try:
            csv_content = kraken_file.getvalue()
            with session_scope() as session:
                import_service = BrokerImportService(session)
                broker_transactions = import_service.parse_kraken(csv_content)
                resolved_asset_ids = import_service.resolve_asset_ids(broker_transactions)
                duplicate_ids = import_service.duplicate_transaction_ids(
                    broker_transactions
                )

            if not broker_transactions:
                st.warning("El fichero no contiene operaciones spot BUY/SELL.")
            else:
                asset_id_to_symbol = {asset.id: asset.symbol for asset in assets}
                external_assets = {
                    item.external_asset_id: item for item in broker_transactions
                }
                selected_mappings: dict[str, int] = {}
                st.markdown("**Mapeo de criptoactivos**")
                for external_id, item in external_assets.items():
                    mapped_asset_id = resolved_asset_ids.get(external_id)
                    mapped_symbol = asset_id_to_symbol.get(mapped_asset_id or -1)
                    if mapped_symbol:
                        st.success(f"{external_id} · {item.external_name} → {mapped_symbol}")
                        selected_mappings[external_id] = mapped_asset_id  # type: ignore[assignment]
                        continue
                    selected_symbol = st.selectbox(
                        f"{external_id} · {item.external_name}",
                        options=[""] + asset_symbols,
                        format_func=lambda value: value
                        or "Sin mapear (omitir operaciones)",
                        key=f"kraken_mapping_{external_id}",
                    )
                    if selected_symbol:
                        selected_mappings[external_id] = symbol_to_asset[
                            selected_symbol
                        ].id

                preview_rows = []
                for item in broker_transactions:
                    mapped_id = selected_mappings.get(item.external_asset_id)
                    if item.external_transaction_id in duplicate_ids:
                        status = "Duplicada"
                    elif mapped_id is None:
                        status = "Sin mapear"
                    else:
                        status = "Lista para importar y convertir a EUR"
                    preview_rows.append(
                        {
                            "Fecha UTC": item.occurred_at,
                            "Tipo": item.transaction_type,
                            "Par": item.external_name,
                            "Activo": asset_id_to_symbol.get(mapped_id or -1, "N/A"),
                            "Unidades": item.quantity,
                            "Precio original": item.price,
                            "Importe original": item.gross_amount,
                            "Comisión original": item.fees,
                            "Divisa": item.currency,
                            "Estado": status,
                            "TxID": item.external_transaction_id,
                        }
                    )
                st.dataframe(
                    pd.DataFrame(preview_rows),
                    hide_index=True,
                    use_container_width=True,
                )

                if st.button("Confirmar importación Kraken", type="primary"):
                    with session_scope() as session:
                        summary = BrokerImportService(session).import_kraken(
                            broker_transactions,
                            manual_mappings=selected_mappings,
                        )
                    st.success(
                        f"Importadas: {summary.imported} · Duplicadas: "
                        f"{summary.duplicates} · Sin mapear: {summary.unmapped} · "
                        f"Inválidas: {summary.invalid}"
                    )
                    for error in summary.errors:
                        st.warning(error)
                    if summary.imported:
                        st.rerun()
        except ValueError as exc:
            st.error(str(exc))

if not asset_symbols:
    st.info("No hay activos habilitados.")
    st.stop()

with session_scope() as session:
    service = PortfolioService(session)
    repo = PortfolioRepository(session)
    portfolio_rows = service.portfolio_rows()
    transaction_rows = service.transaction_rows()
    exposure = service.get_exposures()
    total_capital = service.get_total_capital(default=0.0)

positions_df = pd.DataFrame(portfolio_rows)
transactions_df = pd.DataFrame(transaction_rows)

st.subheader("Resumen")
if positions_df.empty:
    invested_cost = 0.0
    market_value = 0.0
    largest_position = 0.0
else:
    invested_cost = float(positions_df["cost_basis"].sum())
    market_value = float(positions_df["current_value"].sum())
    largest_position = float(positions_df["current_weight"].max())
cash_value = total_capital - invested_cost if total_capital > 0 else 0.0

with session_scope() as session:
    liquidity_plan = PortfolioService(session).planned_cash_reserve(
        total_capital=total_capital,
        estimated_cash=cash_value,
    )

summary_cols = st.columns(6)
summary_cols[0].metric("Capital total", _money(total_capital))
summary_cols[1].metric("Coste invertido", _money(invested_cost))
summary_cols[2].metric("Valor actual", _money(market_value))
summary_cols[3].metric("Cash estimado", _money(cash_value))
summary_cols[4].metric("Peso actual", f"{exposure.total_invested_weight * 100:.1f}%")
summary_cols[5].metric("Mayor posicion", f"{largest_position * 100:.1f}%")

st.markdown("#### Liquidez planificada")
liquidity_cols = st.columns(4)
liquidity_cols[0].metric(
    "Compromiso solicitado",
    _money(liquidity_plan.requested_commitment),
    help="Suma del capital nominal o porcentaje sugerido de los planes activos.",
)
liquidity_cols[1].metric(
    "Cash reservado",
    _money(liquidity_plan.effective_reserved),
    help="Parte del cash estimado que puede cubrir los planes de compra vigentes.",
)
liquidity_cols[2].metric(
    "Cash completamente libre",
    _money(liquidity_plan.free_cash),
    help="Cash estimado que queda después de reservar los planes vigentes.",
)
liquidity_cols[3].metric(
    "Déficit de reserva",
    _money(liquidity_plan.reserve_deficit),
    help="Capital comprometido por los planes que no está cubierto por el cash estimado.",
)
st.caption(
    "La reserva es una capa de planificación: no bloquea dinero en el broker. "
    "Incluye niveles activos y disparados aún no ejecutados; excluye pausados, "
    "expirados y ejecutados."
)
if liquidity_plan.reserve_deficit > 0:
    st.warning(
        "Los planes de compra superan el cash estimado en "
        f"{_money(liquidity_plan.reserve_deficit)}. Revisa importes, porcentajes o prioridades."
    )

with st.expander(
    f"Reserva para compras planificadas ({len(liquidity_plan.reservations)} niveles)",
    expanded=False,
):
    if not liquidity_plan.reservations:
        st.info("No hay niveles de compra activos con reserva asociada.")
    else:
        reservation_rows = [
            {
                "Símbolo": item.symbol,
                "Estado": item.status,
                "Precio objetivo": item.target_price,
                "Divisa": item.price_currency or "N/A",
                "Precio actual": item.current_price,
                "Distancia": item.distance_pct,
                "% capital": item.suggested_weight_pct,
                "Capital nominal": item.suggested_capital,
                "Reserva solicitada": item.requested_capital,
                "Cálculo": {
                    "capital_nominal": "Capital nominal",
                    "porcentaje_capital": "% del capital total",
                    "sin_asignacion": "Sin asignación",
                }[item.calculation_basis],
            }
            for item in liquidity_plan.reservations
        ]
        st.dataframe(
            pd.DataFrame(reservation_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Precio objetivo": st.column_config.NumberColumn(format="%.4f"),
                "Precio actual": st.column_config.NumberColumn(format="%.4f"),
                "Distancia": st.column_config.NumberColumn(format="%.2f%%"),
                "% capital": st.column_config.NumberColumn(format="%.2f%%"),
                "Capital nominal": st.column_config.NumberColumn(format="%.2f €"),
                "Reserva solicitada": st.column_config.NumberColumn(format="%.2f €"),
            },
        )

if positions_df.empty:
    st.info("No hay posiciones agregadas. Anade compras para construir la cartera.")
else:
    valuation_warnings = positions_df.dropna(subset=["valuation_warning"])
    if not valuation_warnings.empty:
        st.warning(
            "Hay posiciones que requieren revision: "
            + "; ".join(
                f"{row.symbol}: {row.valuation_warning}"
                for row in valuation_warnings.itertuples()
            )
        )
    display_positions = positions_df[
        [
            "symbol",
            "asset_type",
            "sector",
            "quantity",
            "avg_cost",
            "current_price_native",
            "quote_currency",
            "fx_rate_to_eur",
            "current_price_eur",
            "cost_basis",
            "current_value",
            "current_weight",
            "target_weight",
            "pnl",
            "pnl_pct",
            "valuation_warning",
        ]
    ].rename(
        columns={
            "symbol": "Simbolo",
            "asset_type": "Tipo",
            "sector": "Sector",
            "quantity": "Cantidad",
            "avg_cost": "Coste medio EUR",
            "current_price_native": "Precio nativo",
            "quote_currency": "Divisa",
            "fx_rate_to_eur": "Cambio a EUR",
            "current_price_eur": "Precio actual EUR",
            "cost_basis": "Coste invertido EUR",
            "current_value": "Valor actual EUR",
            "current_weight": "Peso actual",
            "target_weight": "Peso objetivo",
            "pnl": "P&L",
            "pnl_pct": "P&L %",
            "valuation_warning": "Revision",
        }
    )
    display_positions["Peso actual"] = display_positions["Peso actual"] * 100
    display_positions["Peso objetivo"] = display_positions["Peso objetivo"] * 100
    st.dataframe(
        display_positions,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Cantidad": st.column_config.NumberColumn("Cantidad", format="%.6f"),
            "Coste medio EUR": st.column_config.NumberColumn(
                "Coste medio EUR", format="%.2f"
            ),
            "Precio nativo": st.column_config.NumberColumn("Precio nativo", format="%.4f"),
            "Cambio a EUR": st.column_config.NumberColumn("Cambio a EUR", format="%.6f"),
            "Precio actual EUR": st.column_config.NumberColumn(
                "Precio actual EUR", format="%.2f"
            ),
            "Coste invertido EUR": st.column_config.NumberColumn(
                "Coste invertido EUR", format="%.2f"
            ),
            "Valor actual EUR": st.column_config.NumberColumn(
                "Valor actual EUR", format="%.2f"
            ),
            "Peso actual": st.column_config.NumberColumn("Peso actual", format="%.2f%%"),
            "Peso objetivo": st.column_config.NumberColumn("Peso objetivo", format="%.2f%%"),
            "P&L": st.column_config.NumberColumn("P&L", format="%.2f"),
            "P&L %": st.column_config.NumberColumn("P&L %", format="%.2f%%"),
        },
    )

    asset_df = pd.DataFrame([{"symbol": s, "weight": w} for s, w in exposure.by_asset.items()])
    sector_df = pd.DataFrame(
        [{"sector": s, "weight": w} for s, w in exposure.by_sector.items()]
    )
    type_df = pd.DataFrame(
        [{"asset_type": t, "weight": w} for t, w in exposure.by_asset_type.items()]
    )

    chart_col1, chart_col2, chart_col3 = st.columns(3)
    if not asset_df.empty:
        chart_col1.plotly_chart(
            px.pie(asset_df, names="symbol", values="weight", title="Por activo"),
            use_container_width=True,
        )
    if not sector_df.empty:
        chart_col2.plotly_chart(
            px.pie(sector_df, names="sector", values="weight", title="Por sector"),
            use_container_width=True,
        )
    if not type_df.empty:
        chart_col3.plotly_chart(
            px.bar(type_df, x="asset_type", y="weight", color="asset_type", title="Por clase"),
            use_container_width=True,
        )

with st.expander("Nuevo movimiento", expanded=False):
    _render_new_transaction(asset_symbols, symbol_to_asset)

st.subheader("Movimientos")
if transactions_df.empty:
    st.caption("No hay movimientos registrados.")
else:
    st.dataframe(
        transactions_df.rename(
            columns={
                "id": "ID",
                "symbol": "Simbolo",
                "type": "Tipo",
                "date": "Fecha",
                "quantity": "Cantidad",
                "price": "Precio",
                "gross_amount": "Importe bruto",
                "fees": "Comisiones",
                "taxes": "Impuestos",
                "currency": "Divisa",
                "price_source": "Fuente precio",
                "notes": "Notas",
                "external_source": "Origen externo",
                "external_transaction_id": "ID externo",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
    delete_id = st.number_input(
        "ID de movimiento a eliminar",
        min_value=0,
        value=0,
        step=1,
        help="Eliminar un movimiento recalcula la cartera completa.",
    )
    if st.button("Eliminar movimiento", disabled=delete_id <= 0):
        with session_scope() as session:
            deleted = PortfolioService(session).delete_transaction_and_recalculate(int(delete_id))
        if deleted:
            st.success("Movimiento eliminado y cartera recalculada.")
            st.rerun()
        else:
            st.warning("No se encontro ese movimiento.")

with st.expander("Edicion manual avanzada", expanded=False):
    st.caption(
        "Usala solo como fallback. El flujo recomendado es registrar movimientos y dejar "
        "que el sistema recalcule la posicion agregada."
    )
    selected_manual_symbol = st.selectbox("Activo manual", asset_symbols)
    manual_asset = symbol_to_asset[selected_manual_symbol]
    with session_scope() as session:
        manual_repo = PortfolioRepository(session)
        existing_position = manual_repo.get_by_asset_id(manual_asset.id)
    with st.form("manual_position_form"):
        col1, col2 = st.columns(2)
        quantity = col1.number_input(
            "Cantidad manual",
            min_value=0.0,
            value=float(existing_position.quantity) if existing_position else 0.0,
            step=1.0,
        )
        avg_cost = col2.number_input(
            "Precio medio manual",
            min_value=0.0,
            value=float(existing_position.avg_cost) if existing_position else 0.0,
            step=1.0,
        )
        current_weight_pct = col1.number_input(
            "Peso actual manual (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(existing_position.current_weight) * 100 if existing_position else 0.0,
            step=0.5,
        )
        target_weight_pct = col2.number_input(
            "Peso objetivo (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(existing_position.target_weight) * 100 if existing_position else 0.0,
            step=0.5,
        )
        save_manual = st.form_submit_button("Guardar posicion manual")
        if save_manual:
            with session_scope() as session:
                PortfolioRepository(session).upsert_position(
                    asset_id=manual_asset.id,
                    quantity=quantity,
                    avg_cost=avg_cost,
                    current_weight=current_weight_pct / 100,
                    target_weight=target_weight_pct / 100,
                )
            st.success("Posicion manual guardada.")
            st.rerun()

st.subheader("Alertas de concentracion")
warnings: list[str] = []
for symbol, weight in exposure.by_asset.items():
    if weight >= 0.10:
        warnings.append(f"{symbol}: peso elevado ({weight * 100:.1f}%).")
for sector, weight in exposure.by_sector.items():
    if weight >= 0.25:
        warnings.append(f"Sector {sector}: concentracion elevada ({weight * 100:.1f}%).")
if len(exposure.by_sector) < 3 and exposure.by_sector:
    warnings.append("La cartera tiene poca diversificacion sectorial.")

if warnings:
    for item in warnings:
        st.warning(item)
else:
    st.success("Sin concentraciones preocupantes con las reglas actuales.")
