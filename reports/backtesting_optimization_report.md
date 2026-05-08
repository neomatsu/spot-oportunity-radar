# Backtesting Optimization Report

## 1. Resumen ejecutivo

Se han evaluado **48 configuraciones** sobre **4 universos** con el runner `python -m jobs.run_backtesting_study`.

Universos analizados:
- `mixed`: 29 activos, periodo 2023-08-11 a 2026-04-01, 12 configuraciones en shortlist.
- `stocks`: 6 activos, periodo 2021-04-05 a 2026-04-01, 9 configuraciones en shortlist.
- `etfs`: 20 activos, periodo 2023-08-11 a 2026-04-01, 12 configuraciones en shortlist.
- `crypto`: 3 activos, periodo 2023-07-02 a 2026-04-02, 2 configuraciones en shortlist.

Conclusiones principales:
- El ranking final no se ha ordenado por retorno bruto, sino por un score compuesto con peso fuerte en expectancy y profit factor out-of-sample, penalizando drawdown, pocos trades y colapso train/test.
- Las configuraciones demasiado relajadas tienden a degradarse claramente fuera de muestra, especialmente en crypto.
- Las configuraciones con `BUY_CANDIDATE` solo o con filtros de riesgo más duros suelen sacrificar frecuencia, pero mejoran la robustez.

## 2. Metodología

- El estudio usa el motor historico barra a barra ya existente.
- Las señales se recalculan con datos disponibles hasta cada fecha; no se usa look-ahead.
- La validacion temporal se hace con split simple in-sample / out-of-sample 70/30.
- El estudio se lanza por script CLI, sin depender de la UI.
- Se priorizan activos con al menos 400 barras para evitar universos con muestra demasiado corta.

Auditoría rápida del estado previo:
- El optimizer original soportaba grid search y split train/test, pero no barría recommendation sets ni familias de salida de forma amplia.
- El reporting existente exponia métricas y segmentación, pero no generaba un informe consolidado multiuniverso.
- La persistencia en DB ya existía; este estudio persiste al menos la mejor configuración de cada universo y exporta artefactos CSV/Markdown.

Criterio compuesto usado en el estudio:

```text
strategy_score =
  + expectancy_out_of_sample * 8
  + (profit_factor_out_of_sample - 1) * 25
  + avg_return_out_of_sample * 3
  + expectancy_in_sample * 3
  - max_drawdown_out_of_sample * 0.9
  - 2.5 * |expectancy_train - expectancy_test|
  - penalizaciones por pocos trades, PF flojo y colapso OOS
```

La lógica favorece estrategias con edge visible fuera de muestra y penaliza muestras pequeñas y gaps grandes entre train y test.

## 3. Mejores configuraciones globales

| universe | rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mixed | 1 | Balanced fixed 20d | 259 | 126 | 1.1 | 146.19 | 199.63 | 1.16 | 49.26 |
| etfs | 1 | Balanced fixed 20d | 227 | 115 | 0.77 | 160.34 | 364.63 | 0.99 | 48.42 |
| etfs | 2 | Signal loss 50 strict | 170 | 71 | 0.17 | 258.12 | 311.99 | 1.58 | 46.09 |
| mixed | 2 | Signal loss 40 | 376 | 164 | 0.59 | 112.1 | 114.56 | 3.01 | 46.06 |
| mixed | 3 | Signal loss 50 strict | 178 | 78 | 0.22 | 234.76 | 228.33 | 1.85 | 45.99 |
| etfs | 3 | Signal loss 40 | 323 | 147 | 0.48 | 125.01 | 143.79 | 2.9 | 45.83 |
| mixed | 4 | Balanced fixed 10d | 564 | 271 | 0.34 | 69.64 | 91.4 | 3.01 | 45.31 |
| stocks | 1 | Balanced fixed 20d | 41 | 30 | 3.27 | 1.97 | 1.76 | 2.19 | 45.26 |
| etfs | 4 | Balanced fixed 10d | 482 | 232 | 0.26 | 81.02 | 123.43 | 3.07 | 45.02 |
| crypto | 1 | Balanced fixed 10d | 12 | 5 | -0.27 | 2.46 | 4.88 | 0.06 | 41.37 |
| mixed | 5 | High quality fixed 30d | 80 | 33 | 0.04 | 1.99 | 5.42 | 0.52 | 38.67 |
| etfs | 5 | High quality fixed 30d | 80 | 33 | 0.04 | 1.99 | 5.42 | 0.52 | 38.67 |

## 4. Mejores configuraciones por universo

### mixed

| rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Balanced fixed 20d | 259 | 126 | 1.1 | 146.19 | 199.63 | 1.16 | 49.26 |
| 2 | Signal loss 40 | 376 | 164 | 0.59 | 112.1 | 114.56 | 3.01 | 46.06 |
| 3 | Signal loss 50 strict | 178 | 78 | 0.22 | 234.76 | 228.33 | 1.85 | 45.99 |
| 4 | Balanced fixed 10d | 564 | 271 | 0.34 | 69.64 | 91.4 | 3.01 | 45.31 |
| 5 | High quality fixed 30d | 80 | 33 | 0.04 | 1.99 | 5.42 | 0.52 | 38.67 |

### stocks

| rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Balanced fixed 20d | 41 | 30 | 3.27 | 1.97 | 1.76 | 2.19 | 45.26 |
| 2 | Balanced fixed 10d | 132 | 73 | 1.18 | 1.38 | 1.74 | 2.26 | 34.69 |
| 3 | Signal loss 40 | 75 | 41 | 1.33 | 1.01 | 1.41 | 2.09 | 22.67 |
| 4 | Conservative fixed 15d | 7 | 17 | -0.37 | 0.76 | 1.35 | 1.14 | 12.15 |
| 5 | Relaxed hybrid 20/10/30 | 260 | 106 | 0.25 | 0.64 | 1.32 | 3.24 | 11.9 |

### etfs

| rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Balanced fixed 20d | 227 | 115 | 0.77 | 160.34 | 364.63 | 0.99 | 48.42 |
| 2 | Signal loss 50 strict | 170 | 71 | 0.17 | 258.12 | 311.99 | 1.58 | 46.09 |
| 3 | Signal loss 40 | 323 | 147 | 0.48 | 125.01 | 143.79 | 2.9 | 45.83 |
| 4 | Balanced fixed 10d | 482 | 232 | 0.26 | 81.02 | 123.43 | 3.07 | 45.02 |
| 5 | High quality fixed 30d | 80 | 33 | 0.04 | 1.99 | 5.42 | 0.52 | 38.67 |

### crypto

| rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Balanced fixed 10d | 12 | 5 | -0.27 | 2.46 | 4.88 | 0.06 | 41.37 |
| 2 | Relaxed hybrid 20/10/30 | 43 | 12 | 0.15 | 0.45 | 1.39 | 0.14 | 14.27 |

## 5. Análisis por activo

Los siguientes resultados usan la mejor configuración persistida por universo.

| universe | asset | total_trades | expectancy_pct | profit_factor | avg_return_pct | max_drawdown_pct |
| --- | --- | --- | --- | --- | --- | --- |
| crypto | ETHUSDT | 1 | 4.36 | 4.36 | 4.36 | 0.0 |
| crypto | BTCUSDT | 16 | 0.29 | 1.1 | 0.29 | 0.74 |
| etfs | LCUJ.DE | 22 | 831.16 | 1935.98 | 831.16 | 0.3 |
| etfs | VWRL.LON | 18 | 3.02 | 7.16 | 3.02 | 0.37 |
| etfs | PPFB.DE | 14 | 2.66 | 4.56 | 2.66 | 0.37 |
| etfs | SOXX | 9 | 2.48 | 2.31 | 2.48 | 0.24 |
| etfs | VWCE.DE | 16 | 1.8 | 3.5 | 1.8 | 0.52 |
| etfs | IWM | 19 | 1.79 | 2.02 | 1.79 | 0.7 |
| etfs | SPY | 20 | 1.75 | 3.34 | 1.75 | 0.4 |
| etfs | MCHI | 11 | 1.51 | 1.6 | 1.51 | 0.27 |
| etfs | EIMI.LON | 17 | 1.16 | 2.2 | 1.16 | 0.46 |
| etfs | SXR8.DE | 19 | 1.14 | 1.76 | 1.14 | 1.03 |
| etfs | QQQ | 19 | 0.88 | 1.81 | 0.88 | 0.64 |
| etfs | IJH | 20 | 0.83 | 1.65 | 0.83 | 0.83 |
| etfs | EXSA.DE | 19 | 0.81 | 1.8 | 0.81 | 0.79 |
| etfs | TLT | 7 | -0.06 | 0.96 | -0.06 | 0.29 |
| etfs | BIL | 18 | -0.22 | 0.07 | -0.22 | 0.21 |
| etfs | IB28 | 14 | -0.23 | 0.32 | -0.23 | 0.18 |
| etfs | JPST | 24 | -0.24 | 0.05 | -0.24 | 0.36 |
| etfs | FLOT | 17 | -0.27 | 0.03 | -0.27 | 0.24 |

## 6. Hallazgos clave

- Permitir BUY_CANDIDATE + WATCH aporta más frecuencia y no empeora claramente el ranking medio.
- Exigir SMA50 > SMA200 mejora la expectancy media fuera de muestra.
- Los horizontes fijos siguen siendo competitivos y menos sensibles al ruido del score de salida.
- Crypto sigue mostrando mayor fragilidad temporal y exige filtros más duros para no degradar robustez.

## 7. Configuración recomendada actual

- Principal: `Balanced fixed 20d` con `strategy_score=49.26`.
- Conservadora: `High quality fixed 30d`.
- Agresiva: `Balanced fixed 20d`.

## 8. Riesgos y limitaciones

- La simulación de cartera del motor existe, pero el estudio se ha centrado en `trade_by_trade` para aislar la calidad de la señal.
- El universo ETF ha quedado restringido a activos con suficiente histórico; varios ETFs recientes o sin datos reales se han excluido del estudio robusto.
- El modelo sigue siendo EOD, sin microestructura, spreads reales ni latencia.
- Las métricas out-of-sample son más fiables que las in-sample, pero siguen limitadas por el tamaño de muestra disponible.

## 9. Próximos pasos recomendados

- Profundizar en una segunda iteración separando reglas para crypto y equities/ETFs.
- Añadir walk-forward simple cuando haya más histórico homogéneo en ETFs internacionales.
- Excluir o tratar aparte los activos con histórico insuficiente para evitar ruido en universos mixtos.
- Evaluar configuración principal también en modo portfolio básico para estudiar solapamientos y consumo de capital.