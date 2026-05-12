# Spot Opportunity Radar

Spot Opportunity Radar es una herramienta personal de decision support para detectar oportunidades de compra en spot sobre una watchlist cerrada de acciones, ETFs y crypto. Combina estructura tecnica, modelado de riesgo, ajuste a cartera y trazabilidad de decisiones en una app Streamlit local.

## Stack

- Python 3.13.12
- Streamlit
- SQLite + SQLAlchemy
- pandas + numpy
- scipy + scikit-learn
- pydantic + pydantic-settings
- httpx
- yfinance
- plotly
- pytest
- ruff

## Estructura

```text
app/           UI Streamlit multipagina
core/          configuracion, enums, modelos y logging
data/          base de datos, repositorios y proveedores de mercado
services/      logica de negocio: tecnicos, riesgo, scoring, portfolio, recomendaciones
jobs/          comandos de actualizacion y generacion de senales
backtesting/   base de evaluacion historica ligera
config/        YAML externos de activos, scoring, riesgo y reglas de cartera
tests/         tests unitarios
```

## Lo que hace ahora

- crea SQLite automaticamente y hace seed idempotente de activos desde `config/assets.yaml`
- ingiere precios diarios reales cuando hay proveedor disponible
- usa modo demo reproducible cuando faltan APIs o un proveedor falla
- calcula RSI14, SMA50, SMA200, EMA20, ATR14 y posicion relativa en rango de 52 semanas
- detecta una zona de soporte simple a partir de pivots y minimos recientes
- detecta zonas de soporte y resistencia con metodos `simple`, `clustering`,
  `price_time` y `combined`
- calcula:
  - `technical_score`
  - `risk_score`
  - `portfolio_fit_score`
  - `final_opportunity_score`
- genera recomendaciones `BUY_CANDIDATE`, `WATCH` y `AVOID`
- guarda snapshots tecnicos y senales con breakdown y rationale
- detecta eventos operativos y genera alertas persistidas con deduplicacion
- crea trade intents revisables cuando una senal supera umbrales y los datos son frescos/reales
- permite envio por UI, consola y Telegram cuando esta configurado
- muestra dashboard, watchlist, detalle de activo, cartera y backtesting ligero

## Instalacion

```bash
cd C:\Personal\Workspace\spot-oportunity-radar
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
copy .env.example .env
```

## Configuracion de `.env`

Variables principales:

- `APP_DB_URL`: ruta SQLite local
- `APP_DEMO_MODE=true|false`: activa el fallback demo
- `APP_TELEGRAM_ENABLED=true|false`: activa el canal Telegram
- `APP_TELEGRAM_BOT_TOKEN`: token del bot de Telegram
- `APP_TELEGRAM_CHAT_ID`: chat id de destino para alertas
- `ALPHAVANTAGE_API_KEY`: datos reales de acciones y ETFs si usas Alpha Vantage
- `FMP_API_KEY`: datos reales de acciones y ETFs si usas FMP

Si no configuras APIs, la app sigue funcionando en modo demo o hibrido:

- crypto: intenta usar Binance real
- acciones y ETFs: usa fallback sintetico reproducible si no hay proveedor real

## Politica de cache y refresh

SQLite es la fuente principal de lectura de la app.

La politica operativa actual es:

1. leer primero desde SQLite
2. si los datos siguen frescos, servir cache y no llamar a la API
3. si los datos estan stale o faltan, intentar refresh incremental
4. si el provider falla, preservar el ultimo dato valido ya guardado
5. usar fallback demo solo si no hay datos reales utilizables y `APP_DEMO_MODE=true`

Metadatos por activo:

- `last_available_bar_date`
- `last_refresh_attempt_at`
- `last_successful_refresh_at`
- `last_refresh_status`
- `last_refresh_source`
- `data_mode`: `real`, `demo`, `mixed`, `unknown`
- `freshness_status`: `fresh`, `stale`, `missing`
- `last_error_message`

El refresh incremental evita reemplazar todo el historico:

- descarga datos del provider
- inserta solo fechas nuevas
- actualiza filas coincidentes si reaparecen
- no genera duplicados por `asset_id + date`

## Configuracion de data sources

Las reglas de cache y refresh viven en `config/data_sources.yaml`.

Parametros clave:

- `prefer_cached_data`
- `refresh_on_app_start`
- `equities_refresh_interval_hours`
- `crypto_refresh_interval_minutes`
- `max_staleness_days`
- `allow_demo_fallback`
- `preserve_real_data_on_provider_failure`
- `providers_priority`
- `yfinance_enabled`
- `yfinance_as_fallback`
- `yfinance_long_history_enabled`
- `yfinance_long_history_period`
- `yfinance_long_history_min_rows`
- `allow_provider_mixing`

## Deteccion de soportes y resistencias

La deteccion de zonas tecnicas ya no depende solo de minimos recientes.

El proyecto soporta cuatro metodos configurables:

- `simple`: fallback compatible con la logica original
- `clustering`: pivots locales + `argrelextrema` + `DBSCAN`
- `price_time`: proxy de HVN usando distribucion de tiempo/precio
- `combined`: fusiona clustering y price-time y es el metodo recomendado

La configuracion vive en:

- `config/support_detection.yaml`

Parametros principales:

- `method`
- `historical_window_years`
- `pivot_order`
- `use_pivot_highs`
- `min_cluster_samples`
- `cluster_eps_pct`
- `price_time_bins`
- `hvn_threshold_pct`
- `combine_proximity_pct`
- `max_returned_zones`
- `use_recency_weighting`

Compatibilidad:

- se siguen poblando `support_low`, `support_high` y `distance_to_support_pct`
- recommendation, invalidation, scoring y backtesting siguen usando esos campos legacy
- cuando hay varias zonas, los campos legacy se alimentan desde `nearest_support_zone`

Payload ampliado en snapshots tecnicos:

- `support_zones`
- `resistance_zones`
- `nearest_support_zone`
- `major_support_zone`
- `structural_support_zone`
- `nearest_resistance_zone`
- `major_resistance_zone`
- `structural_resistance_zone`

En `Asset Detail` las zonas se muestran:

- como bandas horizontales translúcidas en el gráfico
- y como tabla resumida con tipo, rango, distancia, score y touches

## Integracion de yfinance

`yfinance` actua como proveedor secundario/fallback para acciones y ETFs. No sustituye al
provider principal por defecto.

Uso previsto:

- fallback cuando el provider principal no cubre un simbolo o falla
- backfill de historico largo para ampliar la cobertura hasta ~5 anos
- apoyo para analisis estructural, SMA200 y contexto multianual

Politica operativa:

1. SQLite sigue siendo la fuente principal de lectura
2. primero se intenta servir desde cache si el activo esta `fresh`
3. si el provider principal falla, se puede probar `yfinance` segun prioridad configurada
4. si el historico es corto, `yfinance` puede hacer un backfill largo controlado
5. el sistema inserta solo barras nuevas o mas antiguas faltantes y evita duplicados
6. no se sobrescribe a ciegas historico bueno existente

Trazabilidad por proveedor:

- cada barra diaria guarda:
  - `provider`
  - `is_adjusted`
  - `inserted_at`
- el estado del activo guarda:
  - `primary_provider`
  - `historical_provider_baseline`
  - `historical_coverage_start`
  - `historical_coverage_end`
  - `recent_provider_mix`

Esto permite detectar si una serie ha recibido backfill o mezcla reciente de fuentes.

Historico largo:

- `yfinance_long_history_period: 5y` permite un backfill largo inicial
- despues, el refresh diario sigue siendo incremental y cache-first
- no se redescargan 5 anos cada dia salvo que falte cobertura y la config lo permita

Limitaciones:

- `yfinance` no es una fuente oficial de mercado
- puede haber diferencias pequenas de OHLC frente a otros providers
- por eso el proyecto registra el proveedor por barra y deja visible la mezcla reciente
- la arquitectura intenta mantener un `primary_provider` por activo y usar `yfinance`
  de forma prudente como fallback o backfill

## Alpha Vantage gratuito

Para acciones y ETFs, el proyecto usa Alpha Vantage gratuito con:

- `TIME_SERIES_DAILY`
- `outputsize=compact`

Esto implica:

- no se usa `TIME_SERIES_DAILY_ADJUSTED`
- no hay `adjusted close` en este modo
- el modo gratuito puede devolver un historico mas corto que el ideal para SMA200 o rango completo de 52 semanas
- para el analisis tecnico del MVP esto es aceptable, porque el sistema trabaja con OHLCV diario estandar

Comportamiento operativo:

- si Alpha Vantage devuelve datos validos, se guardan en SQLite como precios reales
- si devuelve `Note`, `Information`, error de simbolo, payload vacio o fallo de red, la app no se rompe
- si ya habia datos reales validos en SQLite, se conservan y se siguen usando
- solo se cae a demo cuando no hay datos reales utilizables y `APP_DEMO_MODE=true`

Limitaciones del plan gratuito:

- el rate limiting es estricto
- por eso el proyecto evita llamar a la API si ya hay datos recientes en SQLite
- ademas aplica un throttling sencillo entre peticiones reales para no abusar del servicio

## Inicializacion

Inicializacion manual opcional:

```bash
python -m data.database --init
```

Actualizacion de precios:

```bash
python -m jobs.refresh_prices
```

Generacion de senales:

```bash
python -m jobs.generate_signals
```

Escaneo de eventos y generacion de alertas:

```bash
python -m jobs.check_market_events
```

Envio de alertas pendientes:

```bash
python -m jobs.send_alerts
```

Pipeline diario sencillo de senales + alertas:

```bash
python -m jobs.daily_signal_scan
```

Runner diario completo para ejecucion automatica sin Streamlit:

```bash
python -m jobs.daily_market_run
```

Arranque de Streamlit:

```bash
python -m streamlit run app/main.py
```

Tambien puedes usar [run_spot_opportunity_radar.bat](C:/Personal/Workspace/spot-oportunity-radar/run_spot_opportunity_radar.bat).

Estudio amplio de configuraciones y generacion de informe:

```bash
python -m jobs.run_backtesting_study
```

Esto genera artefactos en `reports/`, incluyendo:

- `backtesting_optimization_report.md`
- `backtesting_top_configs.csv`
- `backtesting_by_asset.csv`
- `backtesting_segment_analysis.csv`
- `backtesting_discarded_configs.csv`

## Como interpretar las senales

### Technical score

Se descompone en:

- RSI contextualizado por tendencia
- distancia a soporte con decaimiento continuo
- estructura de tendencia mas simetrica
- momentum relativo dentro del rango de 52 semanas

Regla general:

- score alto: setup tecnico mas limpio o atractivo
- score bajo: estructura debil, sobrecompra o poco edge en la entrada

Detalles de la version actual:

- RSI bajo en uptrend puntua claramente mejor que RSI bajo en downtrend
- si el precio esta por debajo del soporte, el componente de soporte cae a cero
- `death cross` y `golden cross` ahora penalizan/bonifican de forma mas simetrica
- el rango anual ya no se usa como proxy simple de "cerca de minimos", sino como
  momentum relativo contextual

### Risk score

Se descompone en:

- `volatility_component`
- `drawdown_component`
- `concentration_component`
- `asset_type_component`

Regla general:

- `low`: riesgo contenido
- `medium`: aceptable con prudencia
- `high`: exige tamano pequeno o evitar la entrada

### Portfolio fit score

Considera:

- peso actual del activo
- saturacion del sector
- sobreponderacion por clase de activo
- diversificacion existente
- cash disponible frente al objetivo

Ademas, en `cold start`:

- si la cartera tiene muy pocas posiciones, el sistema puede aplicar una penalizacion
  prudente por tipo de activo
- actualmente `crypto` recibe una penalizacion extra en cartera casi vacia

### Final opportunity score

Se calcula desde `config/scoring.yaml`:

```text
final_opportunity_score =
  weight(technical_score) * technical_score +
  weight(inverted_risk_score) * (100 - risk_score) +
  weight(portfolio_fit_score) * portfolio_fit_score
```

Ahora puede usar pesos adaptativos segun el riesgo:

- riesgo bajo -> mas peso al bloque tecnico
- riesgo medio -> equilibrio entre tecnico y riesgo
- riesgo alto -> mas peso al control de riesgo

Si `adaptive_weights.enabled` esta desactivado, el sistema sigue usando la formula
clasica `50/25/25`.

## Recomendaciones

- `BUY_CANDIDATE`: score alto, riesgo aceptable, soporte cercano y buen encaje en cartera
- `WATCH`: idea interesante pero sin confirmacion suficiente o con peor timing
- `AVOID`: score flojo, riesgo alto o cartera saturada

El tamano sugerido de posicion depende de:

- `risk_level`
- `final_score`
- `portfolio_fit_score`
- penalizacion por riesgo total

## UI util

- `Dashboard`: ranking de oportunidades, heatmap de riesgo, top scores y senales recientes
- `Watchlist`: filtros por tipo de activo, riesgo y recomendacion con color por fila, mas `data_mode`, `freshness_status` y `last_refresh_source`
- `Asset Detail`: grafico con medias, soporte, RSI, breakdown del score, rationale,
  invalidation, provider base y cobertura historica
- `Portfolio`: exposicion por activo, sector y clase; alertas simples de concentracion
- `Alerts`: alertas activas, historial, estado de envios y trade intents revisables
- `Market Regime Detector`: contexto bull/bear/bubble por activo, historico persistido
  y grafica de probabilidades frente al precio
- `Backtesting`: motor historico configurable con modos trade-by-trade, portfolio basico,
  portfolio realista y un modo RSI-only, con reglas de salida, segmentacion,
  persistencia y grid search de parametros

## Market Regime Detector

La app incluye un modulo observacional de regimen de mercado. No modifica el scoring,
las recomendaciones, las alertas ni el backtesting operativo.

Calcula para cada activo:

- `bull_probability`
- `bear_probability`
- `bubble_probability`
- `dominant_regime`: `BULL`, `BEAR` o `TRANSITION`

`bubble_probability` se trata como overlay de riesgo, no como regimen dominante. Un
activo puede estar en contexto bull y tener al mismo tiempo riesgo de sobreextension.

La logica es interpretable y rule-based:

- bull: precio y medias por encima de `SMA200`, estructura de maximos/minimos,
  RSI constructivo y fuerza relativa frente al benchmark
- bear: perdida de `SMA200`, `SMA50 < SMA200`, drawdown, estructura bajista y
  debilidad relativa
- bubble: extension frente a `SMA200`, aceleracion, expansion de volatilidad y
  proximidad repetida a maximos

Configuracion:

- defaults externos en `config/regime_config.yaml`
- defaults de paquete en `market_regime/regime_config.yaml`
- benchmark relativo por defecto: `SPY`

Persistencia:

- tabla `market_regime_history`
- unique por `asset_id + date`
- guarda probabilidades, regimen dominante y breakdown JSON

Uso en UI:

- abrir `Market Regime Detector`
- seleccionar activo
- calcular regimen actual o historico del rango
- revisar badge, barras de probabilidades y grafico historico frente al precio

Esta primera fase deja el modulo listo para validar visualmente el contexto antes de
decidir si en el futuro entra como filtro de backtesting o como ajuste opcional del
scoring.

## Score historico bajo demanda

`Asset Detail` puede calcular scores historicos para una fecha concreta o para el rango
visible del grafico sin tocar el pipeline diario.

Diseno:

- calculo bajo demanda desde UI
- cache persistente en `historical_score_snapshots`
- reutiliza la misma logica de indicadores, soportes, riesgo, scoring y recommendation
- calcula cada fecha en modo `as of`, sin usar datos futuros
- por defecto usa `portfolio_context = neutral` para que el score historico no dependa de
  la cartera actual del usuario

Esto permite:

- consultar `technical_score`, `risk_score` y `final_score` en una fecha concreta
- ver la evolucion historica del score junto al precio
- recalcular solo las fechas que faltan, manteniendo el resto en cache

## Tuning iterativo del scoring

Existe un runner especifico para iterar ajustes del scoring sin tocar a mano
`config/scoring.yaml` en cada prueba:

```bash
python -m jobs.run_scoring_tuning_study
```

El estudio usa:

- universos de referencia fijos en `config/scoring_tuning.yaml`
- configuraciones historicas ganadoras por universo
- iteraciones separadas por bloque de logica:
  - tendencia lenta
  - shock regime
  - soporte con ATR
  - recommendation gating

Artefactos generados:

- `reports/scoring_tuning_iteration_summary.csv`
- `reports/scoring_tuning_iteration_details.csv`
- `reports/scoring_tuning_study.md`

La idea es que cada iteracion compare variantes sobre el mismo conjunto de
backtests de referencia para aislar el impacto del cambio y seleccionar un
campeon antes de pasar al siguiente bloque.

## Alertas y trade intents

La fase actual no ejecuta brokers. Su objetivo es:

- detectar eventos relevantes
- filtrar ruido y deduplicar
- notificar por canales configurables
- preparar una propuesta operativa revisable

### Tipos de alerta

- `entry_signal`: nuevo setup accionable o entrada real en buy zone
- `watch_signal`: setup interesante pero todavia incompleto
- `risk_deterioration`: empeora recommendation o sube mucho el riesgo
- `data_quality`: datos stale, demo o no ideales
- `portfolio_constraint`: la cartera limita o bloquea la accion

Ademas, para posiciones ya abiertas en cartera, el sistema soporta alertas de gestion:

- `overbought_warning`
- `take_profit`
- `trim_position`
- `reduce_risk`
- `exit_candidate`
- `stop_loss_warning`
- `rebalance_sell`

El sistema tambien soporta alertas especificas de la estrategia `RSI Cycle`:

- `buy_rsi_25`
- `buy_rsi_20`
- `buy_bullish_divergence`
- `sell_rsi_75`
- `sell_rsi_80`
- `sell_bearish_divergence`

### Severidades

- `info`
- `warning`
- `high`
- `critical`

### Deduplicacion y anti-spam

Las alertas usan:

- `dedupe_key` por activo + tipo
- cooldown configurable por tipo de alerta
- reenvio solo si cambia materialmente el score o la severidad

Esto evita repetir mensajes como "sigue en BUY_CANDIDATE" si no ha cambiado nada relevante.

### Trade intents

Un `trade intent` es una propuesta operativa, no una orden real.

Incluye:

- activo y alerta origen
- recommendation y scores
- buy zone
- peso sugerido y capital estimado
- invalidacion
- rationale y blockers de cartera/datos

Estados soportados:

- `new`
- `reviewed`
- `approved`
- `rejected`
- `expired`
- `executed_manually`

Se crea solo cuando, como minimo:

- la senal es suficientemente fuerte
- el riesgo esta dentro de limites
- los datos estan `real` y `fresh`
- el universo esta permitido
- no hay bloqueos graves de cartera

### Alertas de gestion de posiciones

Estas alertas se evalúan solo para activos con posicion abierta en la cartera manual.

Su objetivo no es ejecutar ventas automaticas, sino ayudarte a gestionar:

- posibles tomas de beneficios
- reducciones parciales
- rebalanceos por exceso de peso
- deterioro tecnico relevante
- cercania o ruptura de invalidacion

Grupos logicos:

- `entry`: oportunidades de entrada
- `position_management`: gestion de posiciones abiertas
- `risk`: deterioro o restricciones de cartera
- `data`: problemas de calidad/frescura de datos

Ejemplos de disparo:

- `take_profit`: beneficio latente alto y extension tecnica
- `trim_position`: peso actual muy por encima del objetivo o del maximo por activo
- `reduce_risk`: riesgo elevado o perdida de SMA50
- `exit_candidate`: recommendation `AVOID`, score muy bajo, riesgo alto o ruptura de soporte
- `stop_loss_warning`: precio muy cerca o por debajo de invalidacion
- `rebalance_sell`: activo sobreponderado aunque no este tecnicamente roto

### Configuracion externa

Las reglas viven en:

- `config/alerts.yaml`
- `config/notifications.yaml`
- `config/execution_rules.yaml`

Desde ahi puedes ajustar:

- thresholds de score y riesgo
- cooldowns y deduplicacion
- severidad
- universos prioritarios
- canales activos
- reglas de creacion de intents
- sizing sugerido y expiracion
- thresholds de profit, RSI, rebalanceo y deterioro para alertas de venta/reduccion

### Telegram

Si quieres activar Telegram:

1. crea un bot y consigue el token
2. identifica el `chat_id`
3. configura en `.env`:

```env
APP_TELEGRAM_ENABLED=true
APP_TELEGRAM_BOT_TOKEN=tu_token
APP_TELEGRAM_CHAT_ID=tu_chat_id
```

Si faltan credenciales, el canal se desactiva de forma elegante y la app sigue funcionando.

La seleccion de tipos enviados por Telegram se controla desde:

- `config/notifications.yaml`

Puedes mantener solo compras:

- `enabled_alert_types: [entry_signal]`

o ampliar con tipos de gestion como:

- `take_profit`
- `trim_position`
- `exit_candidate`
- `rebalance_sell`

o con tipos especificos del modo RSI:

- `buy_rsi_25`
- `buy_rsi_20`
- `buy_bullish_divergence`
- `sell_rsi_75`
- `sell_rsi_80`
- `sell_bearish_divergence`

### Simplificaciones actuales

- no hay ejecucion real en broker
- no hay email en produccion, aunque la arquitectura deja preparado el servicio multicanal
- los trade intents usan sizing estimado a partir de score, cash y reglas de cartera
- el sistema prioriza calidad y trazabilidad sobre frecuencia de alertas

## Ejecucion automatica diaria

El proyecto ya puede ejecutarse automaticamente desde CLI sin tener la app abierta.

### Que hace el runner

`python -m jobs.daily_market_run` coordina:

1. comprobacion basica de entorno y configuracion
2. refresh de datos usando la capa cache-first ya existente
3. recalculo de senales y recomendaciones
4. deteccion de eventos y generacion de alertas
5. envio de alertas pendientes por Telegram
6. persistencia de un resumen de ejecucion

La orquestacion reutiliza la logica actual. No depende de Streamlit.

### Flags utiles

```bash
python -m jobs.daily_market_run --dry-run
python -m jobs.daily_market_run --no-telegram
python -m jobs.daily_market_run --only-refresh
python -m jobs.daily_market_run --only-alerts
python -m jobs.daily_market_run --force
```

Uso recomendado:

- `--dry-run`: recorre el flujo pero no envia Telegram
- `--no-telegram`: ejecuta todo salvo el envio final
- `--only-refresh`: refresca datos y recalcula senales
- `--only-alerts`: reutiliza los datos ya guardados y solo escanea/envia alertas
- `--force`: fuerza refresh aunque haya cache reciente

### Trazabilidad

Cada ejecucion se registra en:

- logs de consola / archivo si rediriges la salida
- tabla `scheduled_job_runs` en SQLite

El resumen guarda:

- hora de inicio y fin
- duracion
- activos refrescados / cacheados / preservados
- errores de refresh
- senales generadas
- alertas detectadas y deduplicadas
- alerts sent / failed / skipped
- estado final `success`, `partial_success` o `failed`

### Configuracion

La configuracion del runner vive en:

- `config/scheduler.yaml`

Parametros actuales:

- hora recomendada de ejecucion
- si se permite modo demo
- si el refresh debe forzarse por defecto

### Windows Task Scheduler

Se incluye un script listo para Windows:

- [run_daily_market_job.bat](C:/Personal/Workspace/spot-oportunity-radar/scripts/run_daily_market_job.bat)

Ese script:

- se mueve al root del proyecto
- activa `.venv` si existe
- ejecuta `python -m jobs.daily_market_run`
- guarda la salida en `logs/`
- devuelve el exit code correcto

Comando recomendado en el Programador de tareas:

- Programa/script:
  `C:\Personal\Workspace\spot-oportunity-radar\scripts\run_daily_market_job.bat`

Directorio de inicio recomendado:

- `C:\Personal\Workspace\spot-oportunity-radar`

### Hora recomendada

Como el sistema trabaja con `daily bars`, una hora razonable para Espana es:

- por la manana, por ejemplo `08:15 Europe/Madrid`, para revisar el estado general
- o despues del cierre de USA si quieres priorizar el cierre diario norteamericano

La configuracion por defecto documentada usa `08:15 Europe/Madrid`.

### Manejo de errores

- fallos parciales de provider no tumbaran necesariamente el runner
- si falla Telegram, queda reflejado y el job puede acabar como `partial_success`
- solo errores criticos de configuracion o ejecucion llevan a estado `failed`
- el exit code es `0` para `success` y `partial_success`, y `1` para `failed`

## Backtesting

El modulo `backtesting/` ahora esta separado en:

- `engine.py`: simulacion historica barra a barra sin look-ahead
- `scenarios.py`: escenarios y splits train/test
- `metrics.py`: metricas por trade, estrategia y segmentos
- `optimizer.py`: grid search simple y explicable
- `reporting.py`: adaptacion de resultados a tablas y graficos
- `models.py`: dominio de escenarios, trades, runs y resultados

### Como funciona

1. Para cada activo y fecha se recalculan indicadores, soporte, riesgo, portfolio fit,
   final score y recomendacion usando solo datos disponibles hasta ese momento.
2. Si la configuracion de entrada lo permite, se abre una operacion simulada.
3. La salida puede ser por:
   - horizonte fijo
   - take profit / stop loss
   - perdida de señal
   - alertas historicas de gestion de posicion reutilizando las reglas live
   - invalidacion
   - o una regla hibrida con primer evento
4. Cada trade guarda:
   - fecha de entrada y salida
   - precio de entrada y salida
   - retorno bruto y neto
   - drawdown maximo de la operacion
   - MFE / MAE
   - scores y rationale de entrada
   - parametros usados

### Modos disponibles

- `trade_by_trade`: evalua cada señal de forma independiente
- `portfolio`: version basica con capital inicial, cash, maximo de posiciones abiertas
  y sizing sencillo
- `portfolio_realistic`: simulacion mas cercana al uso real de cartera
  - parte de un capital inicial configurable
  - compra con `suggested_weight_add` o con un override fijo
  - mantiene cash disponible y cash minimo objetivo
  - respeta limites por activo, sector y tipo cuando se activa
  - permite ampliar posiciones existentes
  - vende parcial o totalmente segun alertas reales de gestion (`take_profit`,
    `reduce_risk`, `trim_position`, `exit_candidate`, `stop_loss_warning`,
    `rebalance_sell`, `overbought_warning`)
  - registra eventos `BUY`, `SELL_PARTIAL` y `SELL_FULL`
- `rsi_cycle_strategy`: modo especifico RSI-only para activos tendenciales
  - trabaja solo con RSI diario, ciclos y divergencias confirmadas
  - abre un ciclo de compra cuando el RSI entra en sobreventa
  - permite, como maximo, una compra por `BUY_RSI_25`, una por `BUY_RSI_20`
    y una por `BUY_BULLISH_DIVERGENCE` dentro del mismo ciclo
  - abre un ciclo de venta cuando el RSI entra en sobrecompra
  - permite, como maximo, una venta por `SELL_RSI_75`, una por `SELL_RSI_80`
    y una por `SELL_BEARISH_DIVERGENCE` dentro del mismo ciclo
  - resetea cada ciclo solo cuando el RSI reingresa por encima de 30 o por
    debajo de 70
  - no usa stop loss ni take profit clasico
  - no usa las alertas generales del sistema como salida
  - registra motivos explicitos como `BUY_RSI_25`, `BUY_RSI_20`,
    `BUY_BULLISH_DIVERGENCE`, `SELL_RSI_75`, `SELL_RSI_80`,
    `SELL_BEARISH_DIVERGENCE`

### RSI Cycle Strategy

La deteccion de divergencias es auditable y sin look-ahead:

- un pivot bajo solo se confirma cuando el RSI ya ha girado al alza
- un pivot alto solo se confirma cuando el RSI ya ha girado a la baja
- la divergencia alcista exige:
  - segundo valle con precio mas bajo
  - segundo valle con RSI mas alto
  - separacion minima y maxima configurable entre pivots
  - confirmacion por cruce de vuelta sobre 30, por defecto activada
- la divergencia bajista aplica la logica espejo con el cruce de vuelta bajo 70

Parametros especificos del modo:

- thresholds de sobreventa y sobrecompra
- porcentajes de compra por evento RSI
- porcentajes de venta por evento RSI
- distancia minima y maxima entre pivots
- comparacion por `close` o por extremos `high/low`
- una sola divergencia por ciclo o multiples, segun config

Limitaciones:

- es un modelo EOD, no intradia
- no esta pensado para scalping ni activos extremadamente laterales
- el objetivo es capturar acumulacion/distribucion por ciclos, no timing perfecto

### Configuracion

Las reglas viven en:

- `config/backtesting.yaml`
- `config/optimization.yaml`

Parametros configurables:

- modo de entrada: cierre de señal o apertura siguiente
- comision y slippage
- tamaño fijo o basado en `suggested_weight_add`
- umbral minimo de `final_opportunity_score`
- maximo `risk_score`
- distancia maxima a soporte
- RSI maximo
- exigir o no tendencia alcista de fondo
- take profit, stop loss y horizonte
- alertas de salida basadas en las mismas reglas operativas que el sistema live:
  `take_profit`, `reduce_risk`, `exit_candidate`, `stop_loss_warning`,
  `trim_position`, `rebalance_sell`, `overbought_warning`
- capital inicial, cash minimo y limites de cartera para simulacion realista
- porcentaje de venta por tipo de alerta para recortes parciales o salidas completas
- criterio compuesto de evaluacion en optimizacion

### Simulacion de cartera realista

Este modo intenta parecerse mas al uso real de la app:

1. parte de un capital inicial configurable
2. compra solo cuando aparece una señal de entrada valida
3. si `use_suggested_weight_add` esta activo, usa el peso sugerido por la señal
4. si no, usa un override fijo de compra
5. mantiene cash disponible y puede reservar un minimo de cash objetivo
6. cuando salta una alerta de gestion sobre una posicion abierta, aplica el
   porcentaje de venta configurado para ese tipo de alerta
7. si una alerta fuerte como `exit_candidate` o `stop_loss_warning` coincide con
   otras menores, se aplica la prioridad configurada

Limitaciones:

- sigue siendo una simulacion EOD, no intradiaria
- la venta parcial usa reglas simples y una prioridad unica por dia/activo
- el sizing y las restricciones de cartera son razonables, pero no institucionales

### Metricas

Por estrategia:

- numero total de trades
- win rate
- retorno medio y mediano
- profit factor
- expectancy
- max drawdown
- mejor y peor trade
- duracion media
- ratio retorno / drawdown
- sharpe simplificado
- trades por mes

Por segmento:

- activo
- sector
- asset type
- recomendacion
- score band
- risk band

### Optimizacion y robustez

La optimizacion usa `grid search` simple y guarda:

- parametros de cada combinacion
- metricas in-sample
- metricas out-of-sample
- evaluation score compuesto

Medidas anti-overfitting incluidas:

- split temporal in-sample / out-of-sample
- penalizacion a combinaciones con muy pocos trades
- evaluacion compuesta que no depende solo del retorno bruto

### Persistencia

El sistema persiste resultados en SQLite mediante:

- `backtest_runs`
- `backtest_parameter_sets`
- `backtest_metrics`
- `backtest_trades`

SQLite sigue siendo la fuente principal tambien para el analisis historico.

## Limitaciones actuales

- la zona de soporte es una aproximacion simple, no un modelo avanzado de market structure
- los fundamentales siguen preparados, pero no dominan el score final
- el modo demo usa series sinteticas razonables, utiles para probar la app pero no para validar edge real
- Alpha Vantage gratuito devuelve historico compacto; algunos activos no llegaran de inicio a SMA200 completa
- el sizing es prudente y configurable, pero no es un motor de optimizacion de cartera
- la simulacion de cartera es intencionalmente basica: no modela correlaciones, mark-to-market
  intradiario ni prioridades complejas entre señales concurrentes
- el backtesting evita look-ahead, pero sigue siendo una aproximacion EOD y no una simulacion
  de microestructura o ejecucion institucional

## Ajustar parametros

Puedes afinar el comportamiento sin tocar codigo:

- `config/scoring.yaml`: pesos, thresholds y bandas tecnicas
- `config/risk_rules.yaml`: componentes de riesgo y umbrales
- `config/portfolio_rules.yaml`: limites, targets y sizing base
- `config/data_sources.yaml`: politica de cache, refresh y prioridad de providers
- `config/backtesting.yaml`: reglas de entrada, salida y ejecucion
- `config/optimization.yaml`: grids de parametros y score compuesto de evaluacion
- `config/assets.yaml`: watchlist

## Crypto Pump Radar

Modulo INDEPENDIENTE de alta especulacion para detectar criptomonedas con
senales tempranas de posible movimiento fuerte (pump). NO afecta al scoring
principal, alertas, Telegram, cartera ni al backtesting actuales. Vive en su
propia pagina, su propio job y sus propias tablas.

> Advertencia: alta especulacion. Riesgo de perdida total por rug-pull,
> honeypots y manipulacion. Los scores son heuristicos: NO predicen pumps con
> certeza y los datos pueden estar incompletos.

### Que hace

- Escanea DexScreener para detectar pares DEX con momentum reciente.
- Aplica filtros minimos de salud (liquidez, volumen, edad del par, txns).
- Calcula varios sub-scores y un `final_speculative_score` de 0 a 100.
- Genera un Top N (10 por defecto) y persiste snapshots con timestamp.
- Permite buscar tokens / pares concretos y reconstruir su historico.
- Marca en la grafica cuando habria saltado cada clasificacion.
- Hace un replay ligero (max-up posterior y drawdown) si hay snapshots suficientes.

### Fuentes de datos

Provider principal:

- `DexScreenerProvider` (`data/providers/dexscreener_provider.py`) → endpoints
  publicos de DexScreener (`/latest/dex/search`, `/latest/dex/pairs/...`,
  `/latest/dex/tokens/...`).

Previstos para fases futuras (no implementados todavia): Birdeye, CoinGecko,
exploradores on-chain, honeypot scanners, APIs de holders.

### Como ejecutar un scan

UI Streamlit:

```bash
streamlit run app/main.py
# luego abre la pagina "09 Crypto Pump Radar"
```

CLI:

```bash
python -m jobs.run_crypto_pump_scan
python -m jobs.run_crypto_pump_scan --dry-run
python -m jobs.run_crypto_pump_scan --chains solana ethereum --top 5
python -m jobs.run_crypto_pump_scan --json-output
```

Toda la configuracion vive en `config/crypto_pump_radar.yaml`: chains,
umbrales de filtro, pesos del scoring, bandas de clasificacion y advertencias.

### Como interpretar los scores

| Score | Clasificacion | Lectura |
| --- | --- | --- |
| 0-35 | IGNORE | No hay senal aprovechable. |
| 35-55 | WATCH | Algun ingrediente positivo, pero no es un setup claro. |
| 55-70 | EARLY_MOMENTUM | Movimiento empieza a despertarse, edad y liquidez razonables. |
| 70-85 | HIGH_RISK_PUMP | Estructura tipica de pump en marcha. Riesgo alto. |
| 85-100 | EXTREME_SPECULATION | Maximo riesgo: posible blow-off o manipulacion. |

Sub-scores que componen el final:

- `pump_momentum_score`: aceleracion 5m/1h/6h, ratio buys/sells, vol acelerando.
- `liquidity_quality_score`: liquidez suficiente y ratio vol/liq saludable.
- `transaction_quality_score`: numero de txns y dominio de buys.
- `early_trend_score`: 24h aun no parabolico, 1h despertando, vol creciendo.
- `prior_pump_penalty`: penaliza si 24h/6h ya son extremos o si hay historial.
- `rug_risk_score`: penaliza pares muy nuevos, liquidez ridicula, FDV inflado.

Formula (configurable en YAML, valores por defecto):

```
final = 0.30*momentum + 0.20*liquidity + 0.20*transactions + 0.20*early_trend
      − 0.25*rug_risk − 0.15*prior_pump_penalty
```

Reescalada y recortada a 0-100.

### Como revisar el historico de un token

1. Ejecuta el scanner periodicamente (UI o CLI) para acumular snapshots.
2. En la pagina, pestana "Analisis manual" introduce simbolo, address, pair
   address o URL de DexScreener.
3. La pestana "Histórico persistido" permite reconstruir un par usando solo
   `chain` + `pair_address`, sin volver a llamar a DexScreener.
4. Si hay snapshots suficientes veras graficas de precio, liquidez, volumen,
   buys/sells y scores, con lineas verticales marcando cuando habria saltado
   cada clasificacion. La tarjeta de replay indica `max_up_pct` posterior,
   drawdown desde el maximo y un timing heuristico (`EARLY` / `MID` / `LATE`).

### Limitaciones

- La calidad del replay depende COMPLETAMENTE de la frecuencia con la que se
  haya ejecutado el scanner; un solo snapshot no permite valorar nada.
- DexScreener tiene rate limits: el provider degrada silenciosamente y registra
  warning, no rompe la app.
- No hay deteccion de honeypots ni analisis on-chain todavia.
- Los pesos del scoring son heuristicas iniciales; convendra calibrarlos con
  datos reales antes de tomar decisiones serias.
- Tokens / pares duplicados se deduplican por `chain:pair_address`, pero el
  mismo token puede aparecer en varios pares.
- Las clasificaciones se llaman `WATCH`, `EARLY_MOMENTUM`, etc.; estos labels
  son INTERNOS del modulo radar y no se mezclan con `RecommendationStatus` del
  pipeline principal (viven en otra tabla, otra service y otra pagina).

### Tablas creadas

- `crypto_pump_snapshots`: snapshot de cada par puntuado (con scores, metricas
  y `payload_json` con el breakdown completo).
- `crypto_pump_scan_runs`: metadatos de cada scan (chains, top, errores).

Ambas son independientes del resto del esquema y se inicializan automaticamente
al arrancar la app via `init_db()`.

### Telegram, alertas y cartera

Este modulo NO envia mensajes a Telegram, NO crea alertas en la tabla `alerts`
ni interfiere con el job diario. Esto es intencional en esta fase: el modulo es
exploratorio y conviene verlo en la UI antes de integrarlo en flujos
automatizados.

## Tests y lint

```bash
python -m pytest
python -m ruff check .
```

## Siguientes pasos recomendados

- anadir fundamentales reales para acciones y ETFs
- enriquecer la deteccion de soporte con clustering de pivots
- enriquecer el modo portfolio con mark-to-market diario y restricciones mas finas
- incorporar metricas de acierto por recomendacion y por clase de activo
