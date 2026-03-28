# Backtesting Optimization Report

## 1. Resumen ejecutivo

Se han evaluado **48 configuraciones** sobre **4 universos** con el runner `python -m jobs.run_backtesting_study`.

Universos analizados:
- `mixed`: 12 activos, periodo 2024-08-19 a 2026-03-27, 4 configuraciones en shortlist.
- `stocks`: 6 activos, periodo 2024-08-19 a 2026-03-27, 3 configuraciones en shortlist.
- `etfs`: 3 activos, periodo 2024-08-19 a 2026-03-27, 3 configuraciones en shortlist.
- `crypto`: 3 activos, periodo 2023-07-02 a 2026-03-27, 4 configuraciones en shortlist.

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
| etfs | 1 | Balanced fixed 10d | 36 | 11 | 0.69 | 2.51 | 3.19 | 0.45 | 46.73 |
| stocks | 1 | Hybrid 10/5/20 | 29 | 5 | 1.64 | 1.54 | 1.74 | 0.12 | 40.0 |
| stocks | 2 | TP8 SL5 | 33 | 5 | 0.91 | 1.14 | 1.55 | 0.12 | 28.34 |
| mixed | 1 | Balanced hybrid 12/7/15 | 22 | 6 | 1.45 | 0.9 | 1.4 | 0.52 | 22.41 |
| stocks | 3 | Relaxed hybrid 20/10/30 | 63 | 37 | 2.41 | 0.86 | 1.24 | 1.12 | 17.81 |
| mixed | 2 | Conservative fixed 15d | 17 | 5 | 1.35 | 0.34 | 1.29 | 0.21 | 12.33 |
| mixed | 3 | Balanced fixed 10d | 90 | 21 | 0.36 | 0.42 | 1.16 | 1.07 | 8.59 |
| crypto | 1 | Signal loss 40 | 13 | 7 | -1.58 | 0.77 | 1.35 | 0.24 | 6.39 |
| etfs | 2 | Hybrid 10/5/20 | 15 | 9 | 0.45 | 0.19 | 1.08 | 0.65 | 4.21 |
| crypto | 2 | Balanced fixed 20d | 12 | 6 | -0.28 | -0.0 | 1.0 | 0.18 | -1.7 |
| etfs | 3 | Signal loss 40 | 15 | 7 | -2.79 | 0.3 | 1.13 | 0.54 | -10.03 |
| crypto | 3 | Balanced fixed 10d | 25 | 12 | -1.05 | -0.08 | 0.97 | 0.4 | -15.57 |

## 4. Mejores configuraciones por universo

### mixed

| rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Balanced hybrid 12/7/15 | 22 | 6 | 1.45 | 0.9 | 1.4 | 0.52 | 22.41 |
| 2 | Conservative fixed 15d | 17 | 5 | 1.35 | 0.34 | 1.29 | 0.21 | 12.33 |
| 3 | Balanced fixed 10d | 90 | 21 | 0.36 | 0.42 | 1.16 | 1.07 | 8.59 |
| 4 | Hybrid 10/5/20 | 54 | 16 | 1.04 | -0.06 | 0.98 | 0.66 | -19.38 |

### stocks

| rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Hybrid 10/5/20 | 29 | 5 | 1.64 | 1.54 | 1.74 | 0.12 | 40.0 |
| 2 | TP8 SL5 | 33 | 5 | 0.91 | 1.14 | 1.55 | 0.12 | 28.34 |
| 3 | Relaxed hybrid 20/10/30 | 63 | 37 | 2.41 | 0.86 | 1.24 | 1.12 | 17.81 |

### etfs

| rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Balanced fixed 10d | 36 | 11 | 0.69 | 2.51 | 3.19 | 0.45 | 46.73 |
| 2 | Hybrid 10/5/20 | 15 | 9 | 0.45 | 0.19 | 1.08 | 0.65 | 4.21 |
| 3 | Signal loss 40 | 15 | 7 | -2.79 | 0.3 | 1.13 | 0.54 | -10.03 |

### crypto

| rank | label | train_total_trades | test_total_trades | train_expectancy_pct | test_expectancy_pct | test_profit_factor | test_max_drawdown_pct | strategy_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Signal loss 40 | 13 | 7 | -1.58 | 0.77 | 1.35 | 0.24 | 6.39 |
| 2 | Balanced fixed 20d | 12 | 6 | -0.28 | -0.0 | 1.0 | 0.18 | -1.7 |
| 3 | Balanced fixed 10d | 25 | 12 | -1.05 | -0.08 | 0.97 | 0.4 | -15.57 |
| 4 | TP8 SL5 | 24 | 10 | 0.21 | -0.01 | 1.0 | 0.23 | -18.24 |

## 5. Análisis por activo

Los siguientes resultados usan la mejor configuración persistida por universo.

| universe | asset | total_trades | expectancy_pct | profit_factor | avg_return_pct | max_drawdown_pct |
| --- | --- | --- | --- | --- | --- | --- |
| crypto | ETHUSDT | 3 | 3.89 | 3.02 | 3.89 | 0.13 |
| crypto | BTCUSDT | 17 | -1.57 | 0.61 | -1.57 | 1.17 |
| etfs | QQQ | 13 | 1.93 | 1.88 | 1.93 | 1.08 |
| etfs | IB28 | 28 | 1.32 | 2.6 | 1.32 | 0.37 |
| etfs | SPY | 6 | 0.85 | 2.28 | 0.85 | 0.01 |
| mixed | GOOGL | 1 | 18.25 | 18.25 | 18.25 | 0.0 |
| mixed | IB28 | 3 | 4.11 | 12.33 | 4.11 | 0.0 |
| mixed | QQQ | 1 | -10.68 | 0.0 | -10.68 | 0.0 |
| stocks | NVDA | 1 | 9.79 | 9.79 | 9.79 | 0.0 |
| stocks | GOOGL | 7 | 3.36 | 2.51 | 3.36 | 0.15 |
| stocks | TSM | 7 | 3.36 | 2.51 | 3.36 | 0.13 |
| stocks | AMZN | 15 | -0.03 | 0.99 | -0.03 | 0.49 |
| stocks | MSFT | 4 | -3.85 | 0.01 | -3.86 | 0.39 |

## 6. Hallazgos clave

- Permitir BUY_CANDIDATE + WATCH aporta más frecuencia y no empeora claramente el ranking medio.
- Exigir SMA50 > SMA200 mejora la expectancy media fuera de muestra.
- Los horizontes fijos siguen siendo competitivos y menos sensibles al ruido del score de salida.
- Crypto sigue mostrando mayor fragilidad temporal y exige filtros más duros para no degradar robustez.
- RSI máximos más exigentes tienden a mejorar la calidad media de entrada frente a filtros muy laxos.

## 7. Configuración recomendada actual

- Principal: `Balanced hybrid 12/7/15` con `strategy_score=22.41`.
- Conservadora: `Conservative fixed 15d`.
- Agresiva: `Balanced hybrid 12/7/15`.

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