# Scoring Tuning Study - Shock Windows

Runner para iteraciones de ajuste del scoring con comparativa sobre universos de referencia.

## Resumen por iteracion

### Shock regime tuning

| Variante | Aggregate score |
| --- | --- |
| Control | -28.79 |
| Balanced shock | -28.79 |
| Early shock recognition | -28.79 |
| Strict shock | -28.79 |

Ganadora de la iteracion: `shock_regime_control` con `aggregate_score=-28.79`.

### Shock trend tuning

| Variante | Aggregate score |
| --- | --- |
| Control | -28.79 |
| Relaxed slow trend in shocks | -28.79 |
| Balanced shock trend | -28.79 |

Ganadora de la iteracion: `shock_trend_control` con `aggregate_score=-28.79`.

### Shock support handling

| Variante | Aggregate score |
| --- | --- |
| Control | -28.79 |
| ATR-aware overshoot | -28.79 |
| Balanced ATR overshoot | -28.79 |

Ganadora de la iteracion: `shock_support_control` con `aggregate_score=-28.79`.

## Detalle por universo

| Iteracion | Variante | Universo | Config | Train exp | Test exp | Train PF | Test PF | Test DD | Train trades | Test trades | Universe score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| shock_regime | Control | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_regime | Balanced shock | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_regime | Early shock recognition | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_regime | Strict shock | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_regime | Control | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_regime | Balanced shock | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_regime | Early shock recognition | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_regime | Strict shock | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_regime | Control | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_regime | Balanced shock | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_regime | Early shock recognition | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_regime | Strict shock | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_regime | Control | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_regime | Balanced shock | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_regime | Early shock recognition | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_regime | Strict shock | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_support | Control | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_support | ATR-aware overshoot | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_support | Balanced ATR overshoot | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_support | Control | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_support | ATR-aware overshoot | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_support | Balanced ATR overshoot | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_support | Control | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_support | ATR-aware overshoot | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_support | Balanced ATR overshoot | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_support | Control | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_support | ATR-aware overshoot | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_support | Balanced ATR overshoot | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_trend | Control | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_trend | Relaxed slow trend in shocks | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_trend | Balanced shock trend | qqq_shock_reference | Balanced fixed 20d | -0.4 | 0.0 | 0.84 | 0.0 | 0.0 | 3 | 0 | -21.84 |
| shock_trend | Control | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_trend | Relaxed slow trend in shocks | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_trend | Balanced shock trend | soxx_shock_reference | Balanced fixed 20d | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0 | -22.0 |
| shock_trend | Control | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_trend | Relaxed slow trend in shocks | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_trend | Balanced shock trend | spy_shock_reference | Balanced fixed 20d | -2.65 | 0.0 | 0.21 | 0.0 | 0.0 | 3 | 0 | -31.65 |
| shock_trend | Control | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_trend | Relaxed slow trend in shocks | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |
| shock_trend | Balanced shock trend | sxr8_shock_reference | Balanced fixed 20d | -4.65 | 0.0 | 0.0 | 0.0 | 0.0 | 3 | 0 | -39.67 |

Interpretacion: cada iteracion parte del campeon anterior y compara variantes sobre universos de referencia fijos para aislar el efecto del cambio.