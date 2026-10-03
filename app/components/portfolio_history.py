from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services.portfolio_history_service import PortfolioHistory


def portfolio_history_figure(frame: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["market_value"],
            name="Valor de mercado",
            mode="lines+markers" if len(frame) == 1 else "lines",
            line={"color": "#2563EB", "width": 3},
            fill="tozeroy",
            fillcolor="rgba(37, 99, 235, 0.07)",
            connectgaps=False,
            hovertemplate="%{y:,.2f} €<extra>Valor de mercado</extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["invested_cost"],
            name="Capital invertido",
            mode="lines+markers" if len(frame) == 1 else "lines",
            line={"color": "#D97706", "width": 2, "dash": "dash", "shape": "hv"},
            connectgaps=False,
            hovertemplate="%{y:,.2f} €<extra>Capital invertido</extra>",
        )
    )
    figure.update_layout(
        height=390,
        margin={"l": 12, "r": 20, "t": 20, "b": 12},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Arial, sans-serif", "color": "#475569", "size": 12},
        hovermode="x unified",
        separators=",.",
        hoverlabel={"bgcolor": "#FFFFFF", "font_size": 13},
        legend={"orientation": "h", "y": 1.12, "x": 0, "font_size": 12},
        xaxis={
            "showgrid": False,
            "showline": False,
            "tickformat": "%d %b %Y",
            "hoverformat": "%d/%m/%Y",
            "zeroline": False,
        },
        yaxis={
            "gridcolor": "rgba(148,163,184,0.18)",
            "zeroline": False,
            "tickformat": ",.0f",
            "ticksuffix": " €",
            "rangemode": "tozero",
        },
    )
    return figure


def _eur(value: float) -> str:
    if pd.isna(value):
        return "Sin datos"
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def render_portfolio_history(history: PortfolioHistory) -> None:
    with st.container(border=True):
        st.subheader("Evolución de la cartera")
        st.caption("Tu inversión y su valor de mercado, día a día · EUR")
        if history.excluded_symbols:
            st.info(
                "Histórico parcial: se excluyen las posiciones manuales sin movimientos ("
                + ", ".join(history.excluded_symbols)
                + "). Registra sus compras para incluirlas."
            )
        if history.frame.empty:
            st.info("Registra o importa tu primera compra para ver la evolución de la cartera.")
            return

        period = st.radio(
            "Periodo",
            ["1M", "3M", "6M", "1A", "Todo"],
            index=4,
            horizontal=True,
            key="portfolio_history_period",
            label_visibility="collapsed",
        )
        frame = history.frame
        end = frame.iloc[-1]["date"]
        months = {"1M": 1, "3M": 3, "6M": 6, "1A": 12}
        if period in months:
            frame = frame[frame["date"] >= end - pd.DateOffset(months=months[period])]

        latest = frame.iloc[-1]
        cost, value = latest["invested_cost"], latest["market_value"]
        pnl = value - cost if pd.notna(value) and pd.notna(cost) else float("nan")
        cols = st.columns(3)
        cols[0].metric("Capital invertido", _eur(cost))
        cols[1].metric("Valor de mercado", _eur(value))
        cols[2].metric(
            "Resultado no realizado",
            _eur(pnl),
            delta=(
                f"{pnl / cost * 100:+.2f}% sobre coste"
                if pd.notna(cost) and cost > 0 and pd.notna(pnl)
                else None
            ),
        )
        st.plotly_chart(
            portfolio_history_figure(frame),
            use_container_width=True,
            config={"displayModeBar": False, "scrollZoom": False},
        )
        st.caption(
            f"{frame.iloc[0]['date']:%d/%m/%Y} — {end:%d/%m/%Y} · "
            "Capital invertido = coste de las posiciones abiertas, con gastos de compra. "
            "Las ventas reducen ese coste. No incluye efectivo ni ganancias realizadas."
        )
        st.caption(
            "Valoración con el último cierre disponible y el cambio a EUR guardado a esa fecha. "
            "Se mantiene el último cierre en días sin cotización; no es una cotización en vivo."
        )
        oldest = latest["oldest_price_date"]
        if pd.notna(oldest):
            st.caption(f"Cierre más antiguo utilizado en el último punto: {oldest:%d/%m/%Y}.")
        incomplete = frame[["market_value", "invested_cost"]].isna().any(axis=1)
        if incomplete.any():
            st.warning(
                f"Hay {int(incomplete.sum())} días con precios o cambios de divisa incompletos. "
                "Los valores desconocidos se muestran como huecos en la gráfica."
            )
        if "review_symbols" in frame:
            review = frame[frame["review_symbols"] != ""]
            if not review.empty:
                st.warning(
                    "Hay precios históricos muy alejados del coste de compra (×4 o ×0,25). "
                    "Se conservan en la gráfica; conviene revisar su divisa, escala o mapeo."
                )
                with st.expander("Valoraciones históricas para revisar"):
                    st.dataframe(
                        review[["date", "review_symbols", "market_value"]].rename(
                            columns={
                                "date": "Fecha",
                                "review_symbols": "Activos",
                                "market_value": "Valor de la cartera EUR",
                            }
                        ),
                        hide_index=True,
                        use_container_width=True,
                    )
