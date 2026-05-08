# Scoring Tuning Study

## Executive Summary

Se ha ejecutado una bateria iterativa de tuning del scoring sobre cuatro universos de referencia fijos:

- `mixed_reference`
- `stocks_reference`
- `etfs_reference`
- `crypto_reference`

La metrica agregada del estudio mejora desde `-5.37` en el baseline inicial hasta `-1.40` en la mejor combinacion encontrada.

La mejor combinacion provisional es:

- Iteracion `trend`: `trend_fast_recovery`
- Iteracion `shock`: sin mejora incremental frente al control
- Iteracion `support`: `support_lenient`
- Iteracion `recommendation`: sin mejora incremental frente al control

## Methodology

Cada iteracion modifica solo un bloque de la logica mientras mantiene el campeon de la iteracion anterior como baseline. La comparativa se hace siempre sobre las mismas ventanas y configuraciones historicas ganadoras de referencia.

El score agregado pondera el resultado de cada universo segun el peso definido en `config/scoring_tuning.yaml`.

## Iteration Results

| Iteration | Baseline | Best Variant | Best Score | Delta |
| --- | ---: | --- | ---: | ---: |
| trend | -5.37 | `trend_fast_recovery` | -3.53 | +1.84 |
| shock | -3.53 | `shock_control` (empate) | -3.53 | +0.00 |
| support | -3.53 | `support_lenient` | -1.40 | +2.13 |
| recommendation | -1.40 | `recommendation_control` (empate) | -1.40 | +0.00 |

## Winning Configuration

### Trend winner: `trend_fast_recovery`

```yaml
technical:
  trend:
    golden_cross_bonus: 12
    death_cross_penalty: -6
    price_above_sma200: 6
    price_below_sma200: -4
    ema20_above_sma50: 12
    ema20_below_sma50: -2
```

### Support winner: `support_lenient`

```yaml
technical:
  support_distance:
    overshoot_near_support_score_ratio: 0.45
    atr_proximity_bonus_multiplier: 1.12
```

No se ha observado mejora incremental medible al ajustar `shock` o el gating de `recommendation` dentro de este benchmark.

## Universe Impact

### Trend: baseline vs winner

| Universe | Baseline Score | Winner Score | Comment |
| --- | ---: | ---: | --- |
| mixed_reference | -10.54 | 0.27 | Mejora fuerte; deja de penalizar en exceso el universo mixto. |
| stocks_reference | 4.78 | 4.69 | Practicamente plano. |
| etfs_reference | -9.74 | -9.72 | Sin impacto material. |
| crypto_reference | -6.67 | -26.00 | Empeora mucho por perdida de muestra util. |

### Support: baseline vs winner

| Universe | Baseline Score | Winner Score | Comment |
| --- | ---: | ---: | --- |
| mixed_reference | 0.27 | 2.94 | Mejora adicional clara. |
| stocks_reference | 4.69 | 8.67 | Mejora clara en test expectancy y PF. |
| etfs_reference | -9.72 | -9.72 | Sin impacto material. |
| crypto_reference | -26.00 | -26.00 | Sin impacto material. |

## Interpretation

Hallazgos principales:

- El mayor problema del scoring previo estaba en la penalizacion de tendencia lenta y en la gestion demasiado dura del soporte roto.
- El mejor ajuste no viene de abrir el modelo con thresholds mas laxos, sino de hacer la recuperacion mas rapida y el manejo de overshoot mas tolerante.
- El regimen `shock` no mueve resultados en este benchmark, lo que sugiere que o bien no se activa en estos casos o su impacto actual es demasiado pequeno frente al resto del scoring.
- La parte `etfs_reference` sigue siendo el punto debil del estudio; no mejora con estos cambios.
- `crypto_reference` queda con poca muestra util en esta bateria, por lo que no conviene usarlo para calibracion fina todavia.

## Recommendation

Configuracion provisional recomendada para seguir iterando:

- aplicar `trend_fast_recovery`
- aplicar `support_lenient`
- mantener `shock` como esta hasta tener un benchmark mas sensible a capitulaciones reales
- mantener `recommendation_thresholds` actuales por ahora

## Next Steps

1. Repetir la comparativa sobre un benchmark ETF mas amplio para confirmar si el problema de ETFs es del scoring o del universo elegido.
2. Preparar una segunda bateria centrada solo en `shock` con casos de dips violentos conocidos.
3. Si la mejora se confirma visualmente y en backtesting, promover la configuracion ganadora a `config/scoring.yaml`.
