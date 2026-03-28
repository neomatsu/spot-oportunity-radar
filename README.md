# Spot Opportunity Radar

Spot Opportunity Radar es una herramienta personal de decision support para detectar oportunidades de compra en spot sobre una watchlist cerrada de acciones, ETFs y crypto. Combina estructura tecnica, modelado de riesgo, ajuste a cartera y trazabilidad de decisiones en una app Streamlit local.

## Stack

- Python 3.13.12
- Streamlit
- SQLite + SQLAlchemy
- pandas + numpy
- pydantic + pydantic-settings
- httpx
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
- calcula:
  - `technical_score`
  - `risk_score`
  - `portfolio_fit_score`
  - `final_opportunity_score`
- genera recomendaciones `BUY_CANDIDATE`, `WATCH` y `AVOID`
- guarda snapshots tecnicos y senales con breakdown y rationale
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

Arranque de Streamlit:

```bash
python -m streamlit run app/main.py
```

Tambien puedes usar [run_spot_opportunity_radar.bat](C:/Personal/Workspace/spot-oportunity-radar/run_spot_opportunity_radar.bat).

## Como interpretar las senales

### Technical score

Se descompone en:

- RSI
- distancia a soporte
- estructura de tendencia
- posicion relativa dentro del rango de 52 semanas

Regla general:

- score alto: setup tecnico mas limpio o atractivo
- score bajo: estructura debil, sobrecompra o poco edge en la entrada

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

### Final opportunity score

Se calcula desde `config/scoring.yaml`:

```text
final_opportunity_score =
  weight(technical_score) * technical_score +
  weight(inverted_risk_score) * (100 - risk_score) +
  weight(portfolio_fit_score) * portfolio_fit_score
```

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
- `Asset Detail`: grafico con medias, soporte, RSI, breakdown del score, rationale, invalidation y estado de datos
- `Portfolio`: exposicion por activo, sector y clase; alertas simples de concentracion
- `Backtesting`: revision ligera de senales historicas con retornos a 5, 10 y 20 sesiones

## Backtesting actual

La version incluida es deliberadamente simple:

- usa senales ya persistidas
- compara precio en fecha de senal vs 5, 10 y 20 sesiones despues
- calcula hit rate y retorno medio

No sustituye un backtester completo con reglas de ejecucion, slippage o capital management.

## Limitaciones actuales

- la zona de soporte es una aproximacion simple, no un modelo avanzado de market structure
- los fundamentales siguen preparados, pero no dominan el score final
- el modo demo usa series sinteticas razonables, utiles para probar la app pero no para validar edge real
- Alpha Vantage gratuito devuelve historico compacto; algunos activos no llegaran de inicio a SMA200 completa
- el sizing es prudente y configurable, pero no es un motor de optimizacion de cartera

## Ajustar parametros

Puedes afinar el comportamiento sin tocar codigo:

- `config/scoring.yaml`: pesos, thresholds y bandas tecnicas
- `config/risk_rules.yaml`: componentes de riesgo y umbrales
- `config/portfolio_rules.yaml`: limites, targets y sizing base
- `config/data_sources.yaml`: politica de cache, refresh y prioridad de providers
- `config/assets.yaml`: watchlist

## Tests y lint

```bash
python -m pytest
python -m ruff check .
```

## Siguientes pasos recomendados

- anadir fundamentales reales para acciones y ETFs
- enriquecer la deteccion de soporte con clustering de pivots
- anadir snapshots mas densos para backtesting historico serio
- incorporar metricas de acierto por recomendacion y por clase de activo
