from __future__ import annotations

import html

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from services.dashboard_service import DashboardDetectorSnapshot

CLASSIFICATION_LABELS = {
    "CAPITULACION_EXTREMA": "Capitulación extrema",
    "OPORTUNIDAD_EXCEPCIONAL": "Oportunidad excepcional",
    "BUENA_OPORTUNIDAD": "Buena oportunidad",
    "OPORTUNIDAD": "Oportunidad",
    "NEUTRAL": "Neutral",
    "POCO_ATRACTIVO": "Poco atractivo",
    "MUY_DESFAVORABLE": "Muy desfavorable",
    "DATOS_INSUFICIENTES": "Datos insuficientes",
}


def dashboard_styles() -> str:
    return """
    <style>
      .dashboard-kicker {
        color: #64748b; font-size: .78rem; font-weight: 700;
        letter-spacing: .12em; text-transform: uppercase; margin-bottom: .2rem;
      }
      .dashboard-subtitle { color: #64748b; margin: -.25rem 0 1.2rem; }
      .market-card-head { display:flex; align-items:flex-start; justify-content:space-between; }
      .market-card-title { color:#172033; font-size:1.12rem; font-weight:750; }
      .market-card-score { color:#172033; font-size:2.65rem; font-weight:760; line-height:1; }
      .market-card-score span { color:#64748b; font-size:1rem; font-weight:600; }
      .market-card-label { font-size:1rem; font-weight:700; margin-top:.35rem; }
      .market-card-meta { color:#64748b; font-size:.84rem; margin-top:.45rem; }
      .market-card-delta { border-radius:999px; font-size:.78rem; font-weight:700;
        padding:.28rem .55rem; background:#f1f5f9; color:#475569; white-space:nowrap; }
      .status-line { display:flex; gap:.55rem; align-items:center; justify-content:flex-end;
        color:#64748b; font-size:.86rem; margin-top:.35rem; }
      .status-dot { width:.55rem; height:.55rem; border-radius:50%; display:inline-block; }
      .portfolio-metrics { display:grid; grid-template-columns:repeat(6, minmax(0, 1fr));
        gap:1rem; margin:.35rem 0 1.7rem; }
      .portfolio-metric { min-width:0; min-height:120px; box-sizing:border-box;
        background:#f8fafc; border:1px solid #e2e8f0; border-radius:14px;
        padding:1rem 1.05rem; display:flex; flex-direction:column; justify-content:flex-start; }
      .portfolio-metric-label { color:#334155; font-size:.86rem; margin-bottom:.55rem; }
      .portfolio-metric-value { color:#172033; font-size:clamp(1.45rem, 1.85vw, 2.15rem);
        font-weight:450; line-height:1.12; letter-spacing:-.025em; white-space:nowrap; }
      .portfolio-metric-delta { align-self:flex-start; min-height:1.45rem; margin-top:.55rem;
        border-radius:999px; padding:.18rem .45rem; background:#dcfce7; color:#15803d;
        font-size:.76rem; font-weight:650; line-height:1.1; }
      .portfolio-metric-delta.is-negative { background:#fee2e2; color:#dc2626; }
      .portfolio-metric-delta.is-empty { visibility:hidden; }
      @media (max-width: 1100px) {
        .portfolio-metrics { grid-template-columns:repeat(3, minmax(0, 1fr)); }
      }
      @media (max-width: 650px) {
        .portfolio-metrics { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:.7rem; }
        .portfolio-metric { min-height:108px; padding:.85rem; }
      }
    </style>
    """


def portfolio_metrics_html(metrics: list[tuple[str, str, str | None]]) -> str:
    cards: list[str] = []
    for label, value, delta in metrics:
        delta_class = "is-empty"
        delta_text = "Sin variación"
        if delta is not None:
            delta_class = "is-negative" if delta.startswith("-") else ""
            delta_text = delta
        cards.append(
            '<div class="portfolio-metric">'
            f'<div class="portfolio-metric-label">{html.escape(label)}</div>'
            f'<div class="portfolio-metric-value">{html.escape(value)}</div>'
            f'<div class="portfolio-metric-delta {delta_class}">'
            f'{html.escape(delta_text)}</div>'
            "</div>"
        )
    return '<div class="portfolio-metrics">' + "".join(cards) + "</div>"


def detector_card_html(detector: DashboardDetectorSnapshot) -> str:
    score = "N/A" if detector.score is None else f"{detector.score:.1f}"
    label = CLASSIFICATION_LABELS.get(detector.classification, detector.classification)
    color = score_color(detector.score)
    price = "N/A" if detector.price is None else f"{detector.price:,.2f}"
    price_date = detector.price_date.isoformat() if detector.price_date else "N/A"
    delta = "Sin histórico comparable"
    if detector.score_change is not None:
        sign = "+" if detector.score_change >= 0 else ""
        delta = f"{sign}{detector.score_change:.1f} · 5 sesiones"
    return f"""
    <div class="market-card-head">
      <div>
        <div class="market-card-title">{html.escape(detector.label)}</div>
        <div class="market-card-score" style="color:{color}">{score}<span>/100</span></div>
        <div class="market-card-label" style="color:{color}">{html.escape(label)}</div>
        <div class="market-card-meta">{html.escape(detector.price_label)} {price} · {price_date}
          · {detector.available_components}/6 componentes</div>
      </div>
      <div class="market-card-delta">{html.escape(delta)}</div>
    </div>
    """


def detector_sparkline(detector: DashboardDetectorSnapshot) -> go.Figure:
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    frame = pd.DataFrame(detector.history)
    if frame.empty:
        figure.add_annotation(
            text="Sin histórico persistido", x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font={"color": "#64748b"},
        )
    else:
        figure.add_trace(
            go.Scatter(
                x=frame["date"], y=frame["score"], mode="lines", name="Score",
                line={"color": "#2563eb", "width": 2.4},
                hovertemplate="%{x|%d/%m/%Y}<br>Score %{y:.1f}<extra></extra>",
            ),
            secondary_y=False,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["date"], y=frame["price"], mode="lines", name="Precio",
                line={"color": "#94a3b8", "width": 1.4, "dash": "dot"},
                hovertemplate="%{x|%d/%m/%Y}<br>Precio %{y:,.2f}<extra></extra>",
            ),
            secondary_y=True,
        )
    figure.add_hline(y=65, line={"color": "#22c55e", "dash": "dash", "width": 1})
    figure.add_hline(y=80, line={"color": "#15803d", "dash": "dot", "width": 1})
    figure.update_layout(
        height=180,
        margin={"l": 5, "r": 5, "t": 18, "b": 5},
        showlegend=False,
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    figure.update_xaxes(showgrid=False, title=None)
    figure.update_yaxes(range=[0, 100], showgrid=True, gridcolor="#e2e8f0", secondary_y=False)
    figure.update_yaxes(showgrid=False, showticklabels=False, secondary_y=True)
    return figure


def score_color(score: float | None) -> str:
    if score is None:
        return "#64748b"
    if score >= 80:
        return "#15803d"
    if score >= 65:
        return "#16a34a"
    if score >= 45:
        return "#d97706"
    return "#dc2626"
