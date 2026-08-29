# Guia funcional de Spot Opportunity Radar

## 1. Objetivo y alcance

Spot Opportunity Radar es una aplicacion local de apoyo a decisiones de inversion. Su
objetivo es reunir datos de mercado, calcular indicadores tecnicos y de riesgo, ordenar
oportunidades, mantener una cartera, generar alertas y validar reglas mediante backtesting.

La aplicacion no envia ordenes a un broker. Las compras, ventas, trade intents, niveles de
entrada y porcentajes sugeridos son propuestas que requieren una decision humana.

Esta guia describe todas las funcionalidades operativas actuales, excepto el modulo Crypto
Pump Radar. Esta escrita para una persona sin conocimiento previo del proyecto.

## 2. Flujo general de la aplicacion

El flujo principal es el siguiente:

1. Se carga el inventario de activos desde `config/assets.yaml` y SQLite.
2. El sistema comprueba si existen precios recientes en cache.
3. Si hace falta, consulta el proveedor configurado para cada tipo de activo.
4. Los precios diarios se normalizan y se guardan en SQLite con su proveedor y divisa.
5. Se calculan RSI, medias, ATR, rango anual y zonas de soporte/resistencia.
6. Se calculan `technical_score`, `risk_score` y `portfolio_fit_score`.
7. Los tres bloques se combinan en `final_opportunity_score`.
8. Se asigna una recomendacion: `BUY_CANDIDATE`, `WATCH` o `AVOID`.
9. El motor de alertas compara el estado actual con reglas, historial y cartera.
10. Las alertas permitidas se persisten y, si corresponde, se envian por Telegram.
11. Todos los resultados quedan disponibles en Streamlit, jobs CLI y backtesting.

La UI lee principalmente desde SQLite. Abrir una pagina no debe provocar descargas masivas.
Los botones de actualizar y los jobs son los responsables de refrescar datos externos.

## 3. Arquitectura funcional

La aplicacion separa las responsabilidades en capas:

| Capa | Responsabilidad |
| --- | --- |
| `app/pages` | Paginas Streamlit y presentacion de resultados |
| `services` | Calculos, reglas, orquestacion y casos de uso |
| `data/providers` | Acceso a APIs y proveedores externos |
| `data/repositories` | Lectura y escritura de SQLite |
| `jobs` | Ejecuciones CLI manuales o programadas |
| `backtesting` | Simulacion historica y metricas |
| `market_regime` | Detector independiente de regimen de mercado |
| `config` | Activos, umbrales, pesos, providers y reglas operativas |

Esta separacion permite que Streamlit, el job diario y los tests reutilicen las mismas reglas.

## 4. Activos y universo de seguimiento

El inventario se define en `config/assets.yaml`. Cada activo incluye, segun disponibilidad:

- simbolo interno;
- nombre;
- tipo: `stock`, `etf` o `crypto`;
- sector y region;
- si esta habilitado;
- si soporta fundamentales;
- divisa de cotizacion;
- alias por proveedor e identificadores externos como ISIN.

El simbolo interno es la clave usada por la aplicacion. Un mismo instrumento puede necesitar
simbolos distintos en FMP, Yahoo Finance, Alpha Vantage o un broker. Los alias evitan tratar
esas representaciones como activos diferentes.

Eliminar o deshabilitar un activo del inventario evita que participe en nuevos refreshes y
rankings. Las operaciones y snapshots historicos ya persistidos no se borran automaticamente.

## 5. Fuentes de datos

### 5.1 Precios diarios

La prioridad actual por tipo de activo se configura en `config/data_sources.yaml`:

| Tipo | Prioridad |
| --- | --- |
| Acciones | FMP, yfinance, Alpha Vantage |
| ETFs | FMP, yfinance, Alpha Vantage |
| Crypto | Binance, Bybit |

Comportamiento relevante:

- FMP es el proveedor principal para muchas acciones y ETFs cuando la suscripcion lo permite.
- yfinance actua como fallback y como fuente de backfill largo de hasta cinco anos.
- Alpha Vantage cubre determinados simbolos, sujeto a su limite diario y plan.
- Binance y Bybit suministran velas diarias de crypto.
- El provider demo solo se usa si `APP_DEMO_MODE` y la configuracion lo permiten.
- Un fallo de red no reemplaza datos reales validos por demo.
- Cada barra puede guardar `provider`, `quote_currency`, `is_adjusted` e instante de insercion.

### 5.2 Politica cache-first

Antes de llamar a un proveedor, el sistema revisa SQLite y el estado del activo:

- acciones y ETFs tienen por defecto un intervalo de refresh de 24 horas;
- crypto tiene un intervalo de 180 minutos;
- el cambio de sesion bursatil se considera alrededor de las 21:30 hora local;
- hasta cinco dias sin una barra nueva puede seguir clasificandose segun calendario y frescura;
- `--force` ignora la ventana de cache y fuerza un intento de refresh.

El backfill largo con yfinance se usa al inicializar una serie corta. Despues se realizan
refreshes incrementales para no descargar cinco anos en cada ejecucion.

### 5.3 Mezcla de proveedores

La mezcla esta permitida, pero no es silenciosa. El estado del activo registra:

- proveedor principal;
- fuente del ultimo refresh;
- provider historico base;
- cobertura inicial y final;
- ultima barra disponible;
- mezcla reciente de proveedores.

Antes de sustituir una serie se comparan solapamientos, escalas y transiciones demo-real. Esto
reduce errores por splits, series ajustadas, tickers incorrectos o cotizaciones en peniques.

### 5.4 Fundamentales

El servicio de fundamentales puede solicitar datos a FMP y guardar snapshots. Actualmente
calcula un bloque fundamental basico a partir de crecimiento de ingresos y otros campos que
devuelva el proveedor. Este bloque esta preparado para consulta y evolucion futura, pero no
forma parte del `final_opportunity_score` operativo actual.

### 5.5 Divisas

La moneda base del portfolio es EUR. Las cotizaciones se mantienen en su divisa nativa y se
convierten para valoracion mediante cambios diarios obtenidos de Yahoo Finance y cacheados en
`fx_rates_daily`.

Casos especiales:

- `GBX` o `GBp` se divide entre 100 y despues se convierte de GBP a EUR.
- USDC y USDT importados desde Kraken se tratan como USD con una hipotesis explicita 1:1.
- Si no existe cambio historico, no se inventa un valor: la fila queda invalida o la posicion
  se marca para revision.

### 5.6 Fuentes especializadas

Los detectores independientes usan fuentes adicionales:

| Dato | Fuente principal |
| --- | --- |
| Bitcoin Fear & Greed | Alternative.me |
| DXY | Yahoo Finance, `DX-Y.NYB` |
| Interes publico por Bitcoin | Wikimedia Pageviews |
| S&P 500 | Yahoo Finance, `^GSPC` |
| CAPE | Yale/Shiller, extendido con Multpl |
| Comprobacion CAPE | GuruFocus, solo referencia |
| VIX | CBOE |
| Fed Funds y Treasury real 10Y | FRED |
| Constituyentes S&P 500 | dataset publico de constituents |
| Precios de constituyentes S&P 500 | yfinance |

## 6. Indicadores tecnicos

Para cada serie diaria se calculan:

- RSI14 con medias exponenciales tipo Wilder (`alpha = 1/14`);
- EMA20;
- SMA50 y SMA200;
- ATR14 y media de ATR14 a 63 sesiones;
- maximo y minimo de 52 semanas sobre hasta 252 sesiones;
- distancia a maximo y minimo anual;
- ROC a 10 y 20 sesiones;
- media de volumen a 20 sesiones y ratios de volumen.

Las medias largas requieren suficiente historico. Cuando faltan datos, el score utiliza reglas
neutrales o reducidas y lo deja indicado en el rationale.

## 7. Soportes y resistencias

El metodo recomendado es `combined`, configurado en `config/support_detection.yaml`. Existen
cuatro metodos intercambiables:

### 7.1 Simple

Usa minimos recientes de una ventana, actualmente 90 barras. Se conserva como fallback.

### 7.2 Clustering

1. Detecta pivots locales con `scipy.signal.argrelextrema`.
2. Usa pivots low y, opcionalmente, pivots high.
3. Agrupa precios cercanos con DBSCAN.
4. Descarta clusters con menos contactos que `min_cluster_samples`.
5. Puntua contactos, recencia, dispersion y posicion respecto al precio.

Parametros actuales: `pivot_order=5`, `cluster_eps_pct=0.006` y dos contactos minimos.

### 7.3 Price-time

Construye un histograma de aceptacion de precio con 120 bins. Los rangos donde el mercado ha
pasado mas tiempo actuan como proxy de HVN. Se conservan nodos con intensidad relativa igual o
superior al 75% del maximo.

### 7.4 Combined

Fusiona zonas de pivots y price-time. Una coincidencia dentro del 0.8% aumenta la relevancia.
Devuelve como maximo seis zonas y usa hasta cinco anos de historico si estan disponibles.

### 7.5 Roles y compatibilidad

Las zonas se clasifican como:

- nearest, major y structural support;
- nearest, major y structural resistance.

Cada zona contiene centro, limites, contactos y scores parciales. Para mantener compatibilidad,
`support_low`, `support_high` y `distance_to_support_pct` corresponden al soporte mas cercano.
Asset Detail dibuja bandas, no lineas exactas, porque un soporte es un rango de aceptacion.

## 8. Sistema de scoring principal

### 8.1 Technical score

El score tecnico se limita a 0-100 y suma cinco componentes:

```text
technical_score = clamp(
    rsi_contextual
  + support_distance
  + trend
  + momentum_52w
  + volume_confirmation,
  0,
  100
)
```

El volumen esta desactivado actualmente (`max_points=0`) porque los estudios no demostraron
mejora. Se mantiene la infraestructura para una futura medida direccional.

#### RSI contextual

El RSI no se interpreta igual en todos los regimenes:

- sobreventa es RSI menor de 35;
- zona neutral llega hasta 60;
- por encima de 60 recibe cero puntos.

Un RSI bajo aporta 28 puntos en uptrend, 24 en shock, 18 en estructura neutral y 8 en
downtrend. Esto evita premiar de igual forma una correccion dentro de una tendencia sana y una
caida estructural.

El regimen `shock` se detecta cuando coinciden:

- RSI menor de 35;
- EMA20 al menos 3% por debajo de SMA50;
- ATR14 superior a 1.5 veces su media.

#### Distancia a soporte

La puntuacion es continua:

```text
support_score = 28 * exp(-0.35 * distance_pct)
```

Si el precio esta por debajo del soporte, normalmente recibe cero. Si la perforacion queda
dentro de un ATR, se considera posible overshoot y recibe el 40% del maximo. Estar dentro de
un ATR por encima del soporte aplica un bonus moderado, limitado a 28 puntos.

#### Tendencia

Se suman tres relaciones:

| Condicion | Puntos |
| --- | ---: |
| SMA50 > SMA200 | +8 |
| SMA50 < SMA200 | -6 |
| Precio > SMA200 | +6 |
| Precio <= SMA200 | -4 |
| EMA20 > SMA50 | +12 |
| EMA20 < SMA50 | -2 |

EMA20 permite detectar recuperaciones antes que un cruce de medias lento.

#### Momentum de 52 semanas

La posicion se normaliza entre minimo y maximo anual. La zona optima es 15%-40% del rango y
recibe hasta 16 puntos. Cerca de maximos, por encima del 75%, recibe cero. Demasiado cerca del
minimo recibe solo el 30% para no confundir caida libre con oportunidad confirmada.

El componente ROC existe, pero su peso operativo actual es cero tras los estudios realizados.

### 8.2 Risk score

El risk score tambien va de 0 a 100, pero un valor alto significa mas riesgo:

```text
risk_score = volatility_component
           + drawdown_component
           + concentration_component
           + asset_type_component
```

- Volatilidad anualizada: hasta 28 puntos entre 18% y 60%.
- Max drawdown: hasta 24 puntos entre 12% y 32%.
- Concentracion: hasta 20 puntos por peso del activo, sector y clase.
- Riesgo estructural por tipo: hasta 28 puntos, usando bases stock=45, ETF=24, crypto=72.

Los niveles son low hasta 33, medium hasta 66 y high por encima. Con menos de 30 barras se usa
el riesgo base del tipo de activo y se declara historial insuficiente.

### 8.3 Portfolio fit score

Mide si una nueva entrada mejora o empeora la composicion actual. Parte de 55 y ajusta:

- activo infraponderado: +14;
- activo cerca del limite: -20;
- sector infraponderado: +10;
- sector saturado: -18;
- clase de activo infraponderada: +14;
- clase de activo saturada: -16;
- diversificacion suficiente: +8;
- cash inferior al 10%: -14.

En carteras con tres posiciones o menos, crypto recibe una penalizacion cold-start de 15. El
resultado se limita a 0-100.

### 8.4 Final opportunity score

El riesgo se invierte porque menor riesgo debe aportar mas valor:

```text
final = w_technical * technical_score
      + w_risk      * (100 - risk_score)
      + w_portfolio * portfolio_fit_score
```

Los pesos cambian con el nivel de riesgo:

| Riesgo | Technical | Riesgo invertido | Portfolio fit |
| --- | ---: | ---: | ---: |
| Menor de 45 | 55% | 20% | 25% |
| 45 a menor de 70 | 50% | 30% | 20% |
| 70 o mas | 40% | 40% | 20% |

El resultado y los pesos usados se guardan en el breakdown. El Market Regime Detector no
modifica este score actualmente.

## 9. Recomendaciones y sizing

### 9.1 Clasificacion

`BUY_CANDIDATE` requiere simultaneamente:

- final score al menos 72;
- risk score como maximo 68;
- distancia absoluta al soporte como maximo 5%;
- portfolio fit al menos 45.

`WATCH` requiere final score al menos 45 y risk score como maximo 85. Los demas casos son
`AVOID`.

### 9.2 Zona de compra e invalidacion

La buy zone usa los limites del soporte mas cercano. La invalidacion indica perdida clara del
limite inferior con volatilidad creciente. Es una explicacion tecnica, no una orden de stop.

### 9.3 Suggested weight add

El porcentaje sugerido parte de 5%, 3% o 1.5% segun riesgo low, medium o high. Se ajusta por:

- final score;
- portfolio fit;
- penalizacion progresiva del risk score.

Los multiplicadores estan acotados para evitar posiciones desproporcionadas.

## 10. Paginas de la aplicacion

### 10.1 Dashboard

Resume la situacion operativa en una sola pantalla:

- pulso Bitcoin Opportunity y S&P 500 Opportunity;
- estado del ultimo job diario;
- capital, coste invertido, valor actual, P&L, cash, exposicion y posiciones;
- oportunidades prioritarias de la watchlist;
- composicion de cartera;
- alertas recientes que requieren atencion.

El Dashboard no recalcula todo el sistema. Agrega snapshots ya persistidos.

### 10.2 Watchlist

Muestra todos los activos habilitados con:

- precio y fecha de la ultima barra;
- tipo, sector y region;
- RSI y distancia a soporte;
- technical, risk, portfolio fit y final score;
- nivel de riesgo y recomendacion;
- modo de datos, frescura y proveedor.

Permite filtrar y ordenar para localizar candidatos, datos stale o activos en demo.

### 10.3 Asset Detail

Es la ficha tecnica individual. Incluye:

- precio, fecha, scores, recomendacion y estado de datos;
- grafico OHLC con EMA20, SMA50 y SMA200;
- selector de periodo visible sin perder el historico almacenado;
- bandas de soportes y resistencias;
- tabla de zonas con distancia, score y contactos;
- RSI;
- desglose y rationale de scoring;
- motivos de interes, buy zone e invalidacion;
- proveedor base, cobertura y mezcla de fuentes.

#### Historico del score bajo demanda

El usuario puede solicitar un intervalo historico. Para cada fecha se usan solamente barras
disponibles hasta ese dia y se reconstruyen technical, risk y final score. Los resultados se
cachean en SQLite para no repetir calculos. La grafica superpone score y precio para revisar
como reaccionaba el modelo en caidas y recuperaciones.

#### Planes de compra parcial

Se pueden registrar varios precios objetivo por activo con:

- porcentaje o capital sugerido;
- tolerancia de proximidad;
- distancia de rearme;
- expiracion y notas.

Los niveles se dibujan en el grafico y participan en el job de alertas, pero no crean una
orden ni una posicion.

### 10.4 Portfolio

La cartera se construye a partir de movimientos, no de pesos escritos manualmente.

#### Capital y posiciones

El usuario introduce el capital total disponible. Cada compra guarda fecha, unidades, precio,
importe, comisiones e impuestos. Varias compras del mismo activo acumulan unidades y recalculan
el coste medio ponderado.

Una venta reduce unidades y elimina del coste pendiente el coste medio de las unidades
vendidas. Vender toda la posicion calcula automaticamente la cantidad disponible.

#### Metricas

```text
coste invertido = suma del coste pendiente de posiciones abiertas
valor actual    = unidades * precio actual convertido a EUR
P&L             = valor actual - coste invertido
peso actual     = valor actual / capital total
cash estimado   = capital total - coste invertido
```

`cash estimado` es una aproximacion de planificacion basada en coste pendiente; no es una
conciliacion bancaria completa de todos los flujos, dividendos, intereses e impuestos.

#### Importacion Trade Republic

Solo procesa filas `category=TRADING` con `BUY` o `SELL`. Usa `transaction_id` para impedir
duplicados y el ISIN para mapear activos. Si no existe mapeo automatico, la UI solicita uno.

#### Importacion Kraken

Procesa ejecuciones spot `BUY` y `SELL`. Cada fill mantiene su `txid`, par, precio, coste,
comision y volumen. El activo base se mapea automaticamente, por ejemplo BTC a BTCUSDT. Los
importes USDC/USDT se convierten historicamente a EUR y se conserva el payload original. No
se permiten ventas superiores a las unidades disponibles ni operaciones con margen.

#### Reserva para caidas

Los planes de compra activos o triggered generan un compromiso de capital. El capital nominal
tiene prioridad; si falta, se usa el porcentaje sobre capital total.

La pagina diferencia:

- compromiso solicitado;
- cash reservado, limitado por cash estimado;
- cash completamente libre;
- deficit de reserva.

La reserva es informativa y no bloquea fondos en un broker.

### 10.5 Alerts

Es el centro operativo de eventos, alertas, historial, trade intents y planes de compra.

Permite:

- escanear eventos manualmente;
- enviar alertas pendientes;
- filtrar por severidad, grupo, tipo, activo y estado;
- consultar logs de notificacion;
- revisar y cambiar estados de trade intents;
- crear, pausar, rearmar o eliminar niveles de compra;
- importar niveles masivamente desde Excel.

### 10.6 Settings

Muestra el modo demo/real, disponibilidad de credenciales, providers configurados y tabla de
estado de datos. Es una vista de diagnostico; los secretos se configuran en `.env`.

### 10.7 Backtesting

Permite seleccionar activos, fechas, reglas y modo de simulacion. Los resultados incluyen
trades, win rate, expectancy, profit factor, drawdown, retorno/DD, curvas y segmentacion.

Los modos se detallan en la seccion 15.

### 10.8 Market Regime Detector

Calcula probabilidades Bull, Bear y Bubble para cada activo y una clasificacion dominante.
Puede actualizar todo el universo, mostrar una tabla coloreada y reconstruir historico por
activo. Es observacional y no cambia el scoring principal.

### 10.9 Bitcoin Opportunity Detector

Indicador contrarian independiente 0-100 para acumulacion de Bitcoin. Incluye score actual,
componentes, historico desde 2018, grafica frente a precio y backtesting por umbrales.

### 10.10 S&P 500 Opportunity Detector

Indicador contrarian independiente para acumulacion del indice. Incluye fuentes macro,
valoracion, breadth, historico, forward returns, backtesting y alertas por cruce.

### 10.11 S&P 500 Opportunities

Lee el ultimo ranking calculado sobre los constituyentes actuales del S&P 500. Permite filtrar
por score, riesgo, recomendacion, sector y disponibilidad de datos, y abrir una ficha rapida
con grafico. Es un screener independiente: sus activos no se anaden automaticamente a la
watchlist ni modifican la cartera.

### 10.12 Volume Profile Lab

Laboratorio visual que construye un `Estimated Volume Profile` usando exclusivamente barras
OHLCV diarias ya almacenadas en SQLite. El usuario elige activo, periodo, numero de bins y
numero maximo de HVN. El volumen de cada vela se reparte uniformemente entre los intervalos de
precio atravesados entre Low y High.

La pagina muestra candles y un histograma horizontal alineado con el precio, identifica el POC
y agrupa maximos locales relevantes como High Volume Nodes. Incluye tabla con centro, rango,
volumen estimado, intensidad relativa y distancia al precio actual.

No representa volumen intradia real por precio. No descarga datos, no persiste resultados y no
modifica soportes, scoring, recomendaciones, alertas ni backtesting.

## 11. Alertas

### 11.1 Deteccion

El scan usa los ultimos scores, recomendaciones, datos y posiciones. Puede generar:

- `entry_signal`: nueva oportunidad de entrada;
- `watch_signal`: setup cercano;
- `risk_deterioration`: empeoramiento de riesgo o recomendacion;
- `data_quality`: datos stale, demo o insuficientes;
- `portfolio_constraint`: conflicto con limites de cartera;
- alertas de gestion de posiciones;
- alertas RSI cycle;
- cruces Bitcoin/S&P 500 Opportunity;
- proximidad o cruce de planes manuales.

La generacion y el envio son fases distintas. Una alerta puede existir en SQLite y no estar
permitida para Telegram.

### 11.2 Alertas de posiciones abiertas

Solo se calculan para activos con posicion:

| Tipo | Criterio principal actual |
| --- | --- |
| `overbought_warning` | RSI >= 75 y extension sobre SMA50 >= 12% |
| `take_profit` | beneficio >= 15% y RSI >= 68 o recomendacion WATCH/AVOID |
| `trim_position` | peso >= 12% o exceso sobre objetivo >= 3 puntos |
| `reduce_risk` | risk >= 70 o precio bajo SMA50 |
| `stop_loss_warning` | precio dentro de 1.5% del soporte o por debajo |
| `exit_candidate` | AVOID, final <= 35, risk >= 80 o soporte roto |
| `rebalance_sell` | peso al menos 1 punto por encima del maximo |

Estas son sugerencias de revision, no ventas automaticas.

### 11.3 Alertas RSI cycle

Se detectan como maximo una vez por ciclo:

- `buy_rsi_25`, `buy_rsi_20`, `buy_bullish_divergence`;
- `sell_rsi_75`, `sell_rsi_80`, `sell_bearish_divergence`.

Si coinciden dos niveles el mismo dia, gana el mas extremo. Las divergencias se confirman al
reingresar por encima de 30 o por debajo de 70 y no usan informacion futura.

### 11.4 Deduplicacion

La clave combina activo y tipo. Una alerta se suprime dentro de su cooldown si no existe un
cambio material. Puede reaparecer si cambia severidad, score, distancia o estado por encima
del umbral configurado. La mayoria de alertas de posicion y RSI usan siete dias; los planes
manuales usan un dia.

### 11.5 Telegram

Telegram requiere `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` y canal habilitado. Actualmente se
envian exclusivamente los tipos de `config/notifications.yaml`:

- entrada principal;
- seis alertas RSI cycle;
- compra/venta Bitcoin Opportunity;
- compra/venta S&P 500 Opportunity;
- proximidad y cruce de niveles manuales.

Los activos en portfolio respetan la misma lista; ya no existe una excepcion para enviar todos
sus tipos. UI y consola pueden conservar mas alertas que Telegram.

### 11.6 Trade intents

Una alerta accionable puede crear una propuesta operativa si cumple, entre otros:

- final score >= 72;
- risk <= 55;
- portfolio fit >= 45;
- recomendacion BUY_CANDIDATE;
- activo stock o ETF;
- datos reales y frescos;
- ausencia de violaciones graves de cartera.

El intent calcula capital, peso, restricciones e invalidacion. Sus estados son new, reviewed,
approved, rejected, expired y executed_manually. Aprobar no envia una orden.

## 12. Market Regime Detector

### 12.1 Bull

```text
bull = 35% trend + 25% structure + 20% momentum + 20% relative strength
```

Premia precio y SMA50 sobre SMA200, maximos/minimos crecientes, RSI 45-70 y rendimiento a 63
sesiones superior a SPY.

### 12.2 Bear

```text
bear = 35% trend break + 25% drawdown + 20% structure break + 20% relative weakness
```

Premia precio y SMA50 bajo SMA200, drawdown, maximos/minimos decrecientes y debilidad relativa.

### 12.3 Bubble overlay

```text
bubble = 30% extension + 25% acceleration + 20% volatility
       + 15% proximity to ATH + 10% valuation optional
```

Bubble no es regimen dominante; es una capa de riesgo. Los scores brutos se normalizan para
sumar 100. Se clasifica BULL si bull > 60 y bear < 30, BEAR si bear > 60 y TRANSITION en el
resto. Se requieren aproximadamente 220 barras para historico fiable.

## 13. Bitcoin Opportunity Detector

### 13.1 Componentes y pesos

El score global es una media ponderada de componentes disponibles:

| Componente | Peso | Interpretacion contrarian |
| --- | ---: | --- |
| Fear & Greed | 20% | mas miedo, mayor oportunidad |
| RSI diario | 20% | RSI bajo, mayor puntuacion |
| Distancia a EMA200 | 20% | precio bajo EMA200, mayor oportunidad |
| Liquidez | 15% | volumen 7d frente a base 30d; es un proxy local, no M2 |
| DXY | 15% | dolar debilitandose, mayor oportunidad para riesgo |
| Interes publico | 10% | bajo percentil de visitas, mayor oportunidad contrarian |

Se necesitan al menos cuatro componentes. Los pesos faltantes se redistribuyen; un dato ausente
no se sustituye silenciosamente por neutral.

### 13.2 Clasificacion

- 80+: excepcional;
- 65-79.99: buena oportunidad;
- 45-64.99: neutral;
- 30-44.99: cautela;
- menos de 30: desfavorable.

### 13.3 Historico

El historico diario puede reconstruirse desde 2018. Se cachea en SQLite y combina precio BTC,
Fear & Greed historico, DXY, pageviews y componentes derivados. La grafica usa score en el eje
izquierdo y precio en el derecho.

### 13.4 Backtesting Bitcoin

Usa tres umbrales de compra y tres de venta, porcentajes configurables, rearme, comision y
slippage. La configuracion por defecto actual compra en 70/75/80 y vende en 15/20/25. El sizing
de compra usa `available_cash`: una venta repone cash y la siguiente compra toma su porcentaje
del cash disponible, no del capital inicial fijo.

Las senales del cierre se ejecutan en la siguiente barra para evitar look-ahead. Se comparan
capital final, retorno, drawdown, compras, ventas y buy-and-hold.

## 14. S&P 500 Opportunity Detector

### 14.1 Componentes

| Componente | Peso | Fuente/calculo |
| --- | ---: | --- |
| Valuation | 15% | CAPE historico, CAPE movil 20y y excess CAPE yield |
| Sentiment | 20% | percentil VIX |
| Momentum | 15% | RSI diario/semanal y distancia SMA200 |
| Drawdown | 25% | caida desde maximo conocido |
| Breadth | 15% | porcentaje de componentes sobre SMA200 |
| Macro | 10% | nivel y cambio de Fed Funds |

La valoracion combina 50% percentil historico completo inverso, 25% percentil movil de 20 anos
inverso y 25% valoracion relativa a tipos reales. Aplica shrinkage parcial hacia 5/10 para no
convertir una valoracion extrema en certeza absoluta.

El score bruto se calibra con una ventana movil de cinco anos, objetivo medio 50 y escala de
dispersion 0.80. La calibracion pretende que 50 represente una condicion habitual reciente sin
eliminar la informacion de ciclos largos.

### 14.2 Fuentes y fechas conocidas

- CAPE tiene un lag de publicacion de 45 dias.
- Fed Funds y Treasury real aplican un dia de lag.
- Los percentiles historicos son expansivos: no usan observaciones futuras.
- Breadth usa los constituyentes actuales y por tanto tiene survivorship bias.
- FRED no es un vintage ALFRED; revisiones historicas pueden introducir sesgo.

### 14.3 Forward returns

Para cada sesion se calculan retornos futuros a 3, 6, 12, 24, 36 y 60 meses. Se agrupan por
bandas de score y se muestran media, mediana, percentiles, probabilidad positiva y adverse
excursion. Este analisis comprueba si scores altos ordenan mejores retornos sin depender de una
estrategia concreta.

### 14.4 Backtesting y alertas

El perfil robusto actual compra al cruzar 60, 62.5 y 77.5 con 50% del cash disponible en cada
escalon, y vende 50%, 20% y 20% al cruzar 25, 40 y 45. El sistema permite porcentajes cero,
sensibilidad, train/test y estudios reanudables.

Las alertas live usan los tres umbrales de compra y solo el umbral de venta 25 con una reduccion
sugerida del 50%. Se envian al cruzar, no por permanecer cada dia en la misma zona.

## 15. Backtesting general

### 15.1 Principios

- Las senales se calculan con datos conocidos en la fecha.
- La entrada por defecto se ejecuta en el siguiente open.
- Se incluyen comisiones de 8 bps y slippage de 5 bps por defecto.
- Los resultados se persisten con parametros, trades, metricas y eventos.
- Un buen retorno con pocos trades o gran gap train/test no se considera robusto.

### 15.2 Trade by trade

Evalua cada entrada de forma aislada. Es util para expectancy, profit factor, win rate y
comparacion de reglas sin competencia por cash. Puede permitir BUY_CANDIDATE y WATCH, aplicar
thresholds opcionales y usar fixed horizon, TP/SL, perdida de senal o hybrid.

### 15.3 Portfolio basico

Simula capital, posiciones simultaneas y restricciones sencillas. Es mas realista que trades
aislados, pero no modela toda la gestion parcial avanzada.

### 15.4 Portfolio realistic

Mantiene cash y equity diariamente. Las compras usan `suggested_weight_add` o un override y
respetan cash minimo, maximo por activo, sector, clase y numero de posiciones. Puede ampliar
posiciones existentes.

Las ventas son parciales por tipo:

- take profit 25%;
- reduce risk 30%;
- trim 20%;
- exit y stop 100%;
- rebalance 20%;
- overbought 10%.

Si coinciden alertas el mismo dia se aplica una sola, con prioridad stop, exit, reduce, trim,
take profit, rebalance y overbought.

### 15.5 RSI Cycle Strategy

Es independiente del scoring general. Abre un ciclo bajo RSI 30 y permite una compra por RSI
25, RSI 20 y divergencia alcista confirmada. El ciclo se rearma al volver sobre 30.

La logica espejo vende en RSI 75, RSI 80 y divergencia bajista, con rearme bajo 70. No usa stop
loss ni alertas generales. Las divergencias comparan pivots separados entre 3 y 20 barras y se
confirman por reentrada. Solo puede ejecutarse una compra o venta por activo y dia.

### 15.6 Filtro Market Regime

Opcionalmente puede exigir bull minimo, bloquear bear alto y reducir sizing si bubble supera
un umbral. Esta integracion solo afecta al escenario de backtest cuando se activa.

### 15.7 Metricas

Se calculan, segun modo:

- total trades, win rate, retorno medio y mediano;
- expectancy, profit factor y payoff;
- max drawdown y return/DD;
- holding medio;
- capital final, cash y exposicion;
- P&L realizado/no realizado;
- compras, ventas parciales y salidas completas;
- concentracion maxima y composicion final;
- segmentacion por simbolo, sector, tipo, score y riesgo.

### 15.8 Optimizacion

El grid search genera combinaciones, persiste resultados y permite split 70/30. Los estudios
CLI anaden ranking compuesto, penalizacion por pocos trades, gaps train/test, drawdown y runs
descartados. Para evitar sobreajuste deben buscarse mesetas de parametros, no solo el ganador.

## 16. Automatizacion diaria

El comando principal es:

```powershell
python -m jobs.daily_market_run
```

Orden de ejecucion:

1. valida configuracion y modo demo;
2. refresca precios de forma incremental;
3. recalcula snapshots, scores y recomendaciones;
4. actualiza historico reciente Bitcoin y S&P 500 Opportunity;
5. detecta eventos y crea alertas/trade intents;
6. envia alertas pendientes permitidas;
7. guarda resumen y codigo de salida.

Flags:

- `--dry-run`: no envia Telegram;
- `--no-telegram`: omite notificaciones;
- `--only-refresh`: solo datos y senales;
- `--only-alerts`: no refresca precios;
- `--force`: fuerza proveedores aunque el cache sea reciente.

Cada run guarda inicio, fin, duracion, status, activos refrescados/cacheados, errores, senales,
alertas y envios. Un fallo parcial de un activo no detiene necesariamente todo el job.

Jobs adicionales permiten refrescar precios, fundamentales, breadth, scoring S&P 500, generar
historicos y ejecutar estudios de optimizacion.

## 17. Persistencia y trazabilidad

SQLite contiene, entre otras, estas entidades funcionales:

- activos y barras diarias;
- estado y logs de refresh;
- snapshots tecnicos, fundamentales y senales;
- score historico y market regime;
- historicos Bitcoin/S&P 500 Opportunity;
- posiciones, movimientos, mapeos externos y cambios FX;
- runs, parametros, metricas, trades y eventos de backtest;
- eventos, alertas, notification logs y trade intents;
- niveles manuales de compra;
- runs del job diario.

Los payloads JSON conservan breakdowns, rationale, provider, conversiones y contexto. Esto
permite explicar por que se calculo un score o se genero una alerta.

## 18. Configuracion principal

| Archivo | Responsabilidad |
| --- | --- |
| `assets.yaml` | Inventario y alias |
| `data_sources.yaml` | Cache, providers y fallback |
| `support_detection.yaml` | Zonas tecnicas |
| `scoring.yaml` | Technical/final score y recomendaciones |
| `risk_rules.yaml` | Riesgo |
| `portfolio_rules.yaml` | Limites, fit y sizing |
| `alerts.yaml` | Eventos, thresholds, cooldowns |
| `notifications.yaml` | Canales y tipos Telegram |
| `execution_rules.yaml` | Trade intents |
| `planned_entries.yaml` | Niveles manuales |
| `backtesting.yaml` | Modos y defaults de simulacion |
| `regime_config.yaml` | Market Regime Detector |
| `bitcoin_opportunity.yaml` | Detector y backtest Bitcoin |
| `sp500_opportunity.yaml` | Detector, backtest y alertas S&P 500 |
| `sp500_scoring.yaml` | Ranking de constituyentes |
| `scheduler.yaml` | Runner diario |

Los secretos se guardan en `.env`, nunca en YAML versionado.

## 19. Limitaciones conocidas

- No existe ejecucion real en broker.
- El portfolio no sustituye una contabilidad fiscal o extracto bancario.
- Los precios diarios no modelan microestructura intradia.
- TP/SL por barra pueden tener ambiguedad si ambos niveles se tocan el mismo dia.
- Dividendos, retenciones y corporate actions dependen del provider y del tipo de serie.
- yfinance no ofrece SLA y puede cambiar simbolos o disponibilidad.
- Alpha Vantage gratuito tiene limite de peticiones.
- FMP restringe endpoints y simbolos segun suscripcion.
- La mezcla de providers puede introducir pequenas diferencias OHLC pese a la trazabilidad.
- El breadth del S&P 500 usa universo actual y contiene survivorship bias.
- CAPE, FRED y macro historico no son completamente point-in-time safe.
- Los backtests no garantizan resultados futuros y pueden sobreajustarse.
- Scores altos indican condiciones relativas favorables segun reglas, no certeza de subida.

## 20. Glosario rapido

| Termino | Significado |
| --- | --- |
| Technical score | Calidad tecnica contrarian y de recuperacion, 0-100 |
| Risk score | Riesgo estimado; mas alto es peor |
| Portfolio fit | Encaje con la cartera actual |
| Final score | Combinacion adaptativa de los tres bloques |
| BUY_CANDIDATE | Cumple score, riesgo, soporte y fit |
| WATCH | Interesante pero incompleto |
| AVOID | Relacion actual poco atractiva |
| Fresh | Datos dentro de la ventana esperada |
| Stale | Datos mas antiguos de lo permitido |
| Demo | Datos sinteticos, no aptos para trade intents |
| Trade intent | Propuesta operativa pendiente de decision humana |
| Dedupe | Supresion de alertas equivalentes |
| Rearme | Condicion necesaria para permitir un nuevo cruce |
| Expectancy | Retorno medio esperado por trade |
| Profit factor | Beneficios brutos / perdidas brutas |
| Drawdown | Caida desde un maximo previo |
