# Bitcoin Opportunity Threshold Study

- Periodo completo: 2018-01-01 a 2026-08-14
- Train: 2018-01-01 a 2023-12-31
- Test: 2024-01-01 a 2026-08-14
- Porcentajes fijos: compras 10/20/40%, ventas 10/10/10%.
- Robust score: 25% CAGR train + 40% CAGR test + 20% exceso CAGR test - 10% drawdown test - 5% gap train/test.

## Resumen ejecutivo

- Mejor ranking robusto: compras 70/75/80 y ventas 15/20/25; retorno completo 877.74% y test 11.57%.
- Mayor retorno completo: 1201.99%, pero obtiene -11.38% en test; no debe considerarse automaticamente la mejor configuracion.
- Mayor retorno test: 15.59% con drawdown test -44.27%.
- Ninguna configuracion supera el buy-and-hold del tramo test; los umbrales mejoran la gestion tactica historica, pero no demuestran una ventaja estable en el regimen reciente.

## Top 20 robusto

| buy_threshold_1 | buy_threshold_2 | buy_threshold_3 | sell_threshold_1 | sell_threshold_2 | sell_threshold_3 | full_total_return_pct | full_max_drawdown_pct | train_cagr_pct | test_cagr_pct | test_max_drawdown_pct | robust_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 70.00 | 75.00 | 80.00 | 15.00 | 20.00 | 25.00 | 877.74 | -65.91 | 37.15 | 4.27 | -47.58 | 2.56 |
| 70.00 | 75.00 | 80.00 | 10.00 | 15.00 | 25.00 | 808.45 | -67.32 | 34.59 | 5.08 | -48.82 | 2.45 |
| 70.00 | 75.00 | 80.00 | 15.00 | 25.00 | 30.00 | 610.57 | -64.50 | 30.59 | 5.35 | -43.67 | 2.34 |
| 70.00 | 75.00 | 80.00 | 15.00 | 20.00 | 30.00 | 693.19 | -65.83 | 33.00 | 4.51 | -45.06 | 2.14 |
| 70.00 | 75.00 | 80.00 | 10.00 | 15.00 | 30.00 | 639.73 | -67.81 | 30.56 | 5.28 | -46.25 | 2.03 |
| 70.00 | 75.00 | 80.00 | 10.00 | 15.00 | 20.00 | 870.92 | -68.76 | 36.43 | 4.08 | -50.33 | 2.01 |
| 70.00 | 75.00 | 80.00 | 20.00 | 25.00 | 30.00 | 612.96 | -64.50 | 31.67 | 4.38 | -43.71 | 1.92 |
| 70.00 | 75.00 | 80.00 | 10.00 | 20.00 | 25.00 | 780.94 | -65.91 | 35.26 | 3.97 | -48.83 | 1.86 |
| 65.00 | 75.00 | 80.00 | 10.00 | 15.00 | 25.00 | 693.10 | -71.57 | 31.49 | 5.19 | -49.63 | 1.82 |
| 70.00 | 75.00 | 80.00 | 10.00 | 25.00 | 30.00 | 554.30 | -64.50 | 28.98 | 5.17 | -44.77 | 1.79 |
| 65.00 | 70.00 | 90.00 | 10.00 | 15.00 | 25.00 | 665.10 | -68.01 | 30.77 | 5.28 | -49.82 | 1.71 |
| 65.00 | 75.00 | 80.00 | 15.00 | 20.00 | 25.00 | 717.80 | -66.86 | 33.04 | 4.34 | -48.45 | 1.70 |
| 65.00 | 70.00 | 90.00 | 10.00 | 15.00 | 20.00 | 759.63 | -68.70 | 33.91 | 4.36 | -51.21 | 1.61 |
| 65.00 | 70.00 | 90.00 | 15.00 | 20.00 | 25.00 | 682.49 | -63.35 | 32.11 | 4.49 | -48.62 | 1.59 |
| 65.00 | 70.00 | 75.00 | 10.00 | 15.00 | 25.00 | 602.21 | -72.52 | 29.22 | 5.55 | -49.93 | 1.57 |
| 65.00 | 70.00 | 90.00 | 15.00 | 25.00 | 30.00 | 475.57 | -62.87 | 25.74 | 5.69 | -44.27 | 1.53 |
| 65.00 | 75.00 | 80.00 | 10.00 | 15.00 | 20.00 | 757.96 | -72.05 | 33.78 | 4.24 | -51.05 | 1.52 |
| 65.00 | 70.00 | 75.00 | 10.00 | 15.00 | 20.00 | 678.77 | -72.77 | 32.22 | 4.72 | -51.20 | 1.50 |
| 65.00 | 70.00 | 85.00 | 10.00 | 15.00 | 25.00 | 625.72 | -69.76 | 29.63 | 5.28 | -49.82 | 1.48 |
| 70.00 | 75.00 | 80.00 | 10.00 | 20.00 | 30.00 | 620.42 | -65.83 | 31.14 | 4.24 | -46.29 | 1.46 |

## Configuracion de referencia 70/75/80 - 20/25/30

| buy_threshold_1 | buy_threshold_2 | buy_threshold_3 | sell_threshold_1 | sell_threshold_2 | sell_threshold_3 | full_total_return_pct | full_max_drawdown_pct | train_cagr_pct | test_cagr_pct | test_max_drawdown_pct | robust_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 70.00 | 75.00 | 80.00 | 20.00 | 25.00 | 30.00 | 612.96 | -64.50 | 31.67 | 4.38 | -43.71 | 1.92 |

## Limitaciones

La seleccion sigue expuesta a sobreajuste. El tramo test no se utilizo para generar senales, pero si forma parte del ranking comparativo final.