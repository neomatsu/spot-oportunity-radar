# Scoring Tuning Study - ETFs

Runner para iteraciones de ajuste del scoring con comparativa sobre universos de referencia.

## Resumen por iteracion

### ETF trend tuning

| Variante | Aggregate score |
| --- | --- |
| Control | 9.79 |
| Balanced ETF trend | 9.59 |
| Faster ETF recovery | 9.47 |
| More defensive slow trend | 9.05 |

Ganadora de la iteracion: `etf_trend_control` con `aggregate_score=9.79`.

### ETF support tuning

| Variante | Aggregate score |
| --- | --- |
| Balanced support handling | 11.14 |
| Lenient ETF support handling | 9.91 |
| Control | 9.79 |
| Tight support discipline | 9.08 |

Ganadora de la iteracion: `etf_support_balanced` con `aggregate_score=11.14`.

### ETF recommendation gating

| Variante | Aggregate score |
| --- | --- |
| Control | 11.14 |
| Buy threshold 70 | 11.14 |
| Buy threshold 69 | 11.14 |
| Buy 70 and risk 70 | 11.14 |

Ganadora de la iteracion: `etf_recommendation_control` con `aggregate_score=11.14`.

## Detalle por universo

| Iteracion | Variante | Universo | Config | Train exp | Test exp | Train PF | Test PF | Test DD | Train trades | Test trades | Universe score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| etf_recommendation | Control | etfs_equity_broad | Balanced fixed 20d | 1.11 | 1.0 | 1.82 | 1.93 | 1.86 | 74 | 44 | 23.81 |
| etf_recommendation | Buy threshold 70 | etfs_equity_broad | Balanced fixed 20d | 1.11 | 1.0 | 1.82 | 1.93 | 1.86 | 74 | 44 | 23.81 |
| etf_recommendation | Buy threshold 69 | etfs_equity_broad | Balanced fixed 20d | 1.11 | 1.0 | 1.82 | 1.93 | 1.86 | 74 | 44 | 23.81 |
| etf_recommendation | Buy 70 and risk 70 | etfs_equity_broad | Balanced fixed 20d | 1.11 | 1.0 | 1.82 | 1.93 | 1.86 | 74 | 44 | 23.81 |
| etf_recommendation | Control | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.16 | 118 | 47 | -18.43 |
| etf_recommendation | Buy threshold 70 | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.16 | 118 | 47 | -18.43 |
| etf_recommendation | Buy threshold 69 | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.16 | 118 | 47 | -18.43 |
| etf_recommendation | Buy 70 and risk 70 | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.16 | 118 | 47 | -18.43 |
| etf_support | Balanced support handling | etfs_equity_broad | Balanced fixed 20d | 1.11 | 1.0 | 1.82 | 1.93 | 1.86 | 74 | 44 | 23.81 |
| etf_support | Lenient ETF support handling | etfs_equity_broad | Balanced fixed 20d | 1.0 | 0.95 | 1.7 | 1.87 | 1.87 | 75 | 44 | 22.06 |
| etf_support | Control | etfs_equity_broad | Balanced fixed 20d | 0.96 | 0.95 | 1.66 | 1.87 | 1.87 | 74 | 44 | 21.89 |
| etf_support | Tight support discipline | etfs_equity_broad | Balanced fixed 20d | 1.27 | 0.88 | 2.01 | 1.75 | 2.04 | 72 | 44 | 20.87 |
| etf_support | Control | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.16 | 118 | 47 | -18.43 |
| etf_support | Tight support discipline | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.15 | 118 | 47 | -18.42 |
| etf_support | Balanced support handling | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.16 | 118 | 47 | -18.43 |
| etf_support | Lenient ETF support handling | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.17 | 118 | 47 | -18.44 |
| etf_trend | Control | etfs_equity_broad | Balanced fixed 20d | 0.96 | 0.95 | 1.66 | 1.87 | 1.87 | 74 | 44 | 21.89 |
| etf_trend | Balanced ETF trend | etfs_equity_broad | Balanced fixed 20d | 1.25 | 0.89 | 1.99 | 1.8 | 1.92 | 72 | 43 | 21.64 |
| etf_trend | More defensive slow trend | etfs_equity_broad | Balanced fixed 20d | 1.11 | 0.89 | 1.78 | 1.79 | 1.87 | 72 | 43 | 20.85 |
| etf_trend | Faster ETF recovery | etfs_equity_broad | Balanced fixed 20d | 1.01 | 0.89 | 1.71 | 1.8 | 1.92 | 73 | 43 | 20.55 |
| etf_trend | Faster ETF recovery | etfs_defensive_bonds | Balanced fixed 10d | -0.35 | -0.35 | 0.23 | 0.25 | 1.06 | 122 | 48 | -16.36 |
| etf_trend | Control | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.4 | 0.16 | 0.15 | 1.16 | 118 | 47 | -18.43 |
| etf_trend | More defensive slow trend | etfs_defensive_bonds | Balanced fixed 10d | -0.34 | -0.42 | 0.17 | 0.15 | 1.13 | 107 | 44 | -18.5 |
| etf_trend | Balanced ETF trend | etfs_defensive_bonds | Balanced fixed 10d | -0.38 | -0.41 | 0.16 | 0.15 | 1.15 | 118 | 46 | -18.52 |

Interpretacion: cada iteracion parte del campeon anterior y compara variantes sobre universos de referencia fijos para aislar el efecto del cambio.