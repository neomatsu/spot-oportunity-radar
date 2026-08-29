from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from services.estimated_volume_profile_service import EstimatedVolumeProfile


def build_estimated_volume_profile_figure(
    price_frame: pd.DataFrame,
    result: EstimatedVolumeProfile,
    *,
    symbol: str,
) -> go.Figure:
    data = price_frame.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values("date")

    profile = result.profile.copy()
    hvn_bins = {
        bin_index
        for node in result.hvns
        for bin_index in range(node.start_bin, node.end_bin + 1)
    }
    colors = []
    for bin_index in profile["bin_index"]:
        if int(bin_index) == result.poc.start_bin:
            colors.append("#e76f51")
        elif int(bin_index) in hvn_bins:
            colors.append("#2a9d8f")
        else:
            colors.append("rgba(100, 116, 139, 0.52)")

    figure = make_subplots(
        rows=1,
        cols=2,
        shared_yaxes=True,
        column_widths=[0.79, 0.21],
        horizontal_spacing=0.015,
    )
    figure.add_trace(
        go.Candlestick(
            x=data["date"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            name=symbol,
            increasing_line_color="#14b8a6",
            decreasing_line_color="#ef4444",
            increasing_fillcolor="#14b8a6",
            decreasing_fillcolor="#ef4444",
            hoverlabel={"namelength": 0},
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=-profile["estimated_volume"],
            y=profile["price_center"],
            width=(profile["price_high"] - profile["price_low"]) * 0.9,
            orientation="h",
            marker={"color": colors, "line": {"width": 0}},
            customdata=profile[["estimated_volume", "normalized_intensity"]],
            hovertemplate=(
                "Precio %{y:,.4f}<br>Volumen estimado %{customdata[0]:,.0f}"
                "<br>Intensidad %{customdata[1]:.1%}<extra></extra>"
            ),
            name="Estimated Volume Profile",
        ),
        row=1,
        col=2,
    )

    for node in result.hvns:
        figure.add_shape(
            type="rect",
            xref="paper",
            yref="y",
            x0=0,
            x1=1,
            y0=node.low,
            y1=node.high,
            fillcolor="#2a9d8f",
            opacity=0.08,
            line={"width": 0},
            layer="below",
        )
        figure.add_annotation(
            xref="paper",
            yref="y",
            x=0.985,
            y=node.center,
            text=f"HVN {node.center:,.2f}",
            showarrow=False,
            xanchor="right",
            font={"size": 10, "color": "#0f766e"},
            bgcolor="rgba(255,255,255,0.70)",
        )

    figure.add_shape(
        type="line",
        xref="paper",
        yref="y",
        x0=0,
        x1=1,
        y0=result.poc.center,
        y1=result.poc.center,
        line={"color": "#e76f51", "width": 2.5},
    )
    figure.add_annotation(
        xref="paper",
        yref="y",
        x=0.995,
        y=result.poc.center,
        text=f"POC {result.poc.center:,.2f}",
        showarrow=False,
        xanchor="right",
        yshift=12,
        font={"size": 11, "color": "#c2410c"},
        bgcolor="rgba(255,255,255,0.86)",
    )

    price_span = result.price_max - result.price_min
    y_padding = max(price_span * 0.025, result.current_price * 0.002)
    figure.update_yaxes(
        range=[result.price_min - y_padding, result.price_max + y_padding],
        title_text="Precio",
        row=1,
        col=1,
    )
    figure.update_xaxes(rangeslider_visible=False, row=1, col=1)
    figure.update_xaxes(
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        title_text="Volumen estimado",
        row=1,
        col=2,
    )
    figure.update_layout(
        height=680,
        margin={"l": 10, "r": 10, "t": 40, "b": 20},
        hovermode="closest",
        showlegend=False,
        bargap=0,
        title={"text": f"{symbol} · Estimated Volume Profile", "x": 0.01},
    )
    return figure
