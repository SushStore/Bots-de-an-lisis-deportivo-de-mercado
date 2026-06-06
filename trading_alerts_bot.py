"""
=============================================================================
  MOTOR DE BOT DE ALERTAS DE TRADING PARA TELEGRAM
  Estrategia: Reversión a la Media (RSI < 35 + Precio ≤ SMA50)
  Compatible con Google Colab (100% gratuito)
=============================================================================

INSTALACIÓN (ejecutar en la primera celda de Colab):
    !pip install yfinance pandas requests

CÓMO USAR:
    1. Ajusta la lista TICKERS con los símbolos que quieras monitorear.
    2. Configura tu TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID (opcional).
    3. Ejecuta el script. Las alertas se imprimen en consola y/o se envían por Telegram.

ESTRATEGIA:
    - RSI < 35  →  Zona de sobreventa (presión vendedora extrema)
    - Precio ≤ SMA(50) * 1.01  →  El precio está cerca o por debajo de la media
    Ambas condiciones juntas sugieren una posible reversión alcista de corto plazo.
=============================================================================
"""

import json
import time
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

# =============================================================================
# ⚙️  CONFIGURACIÓN  —  EDITAR AQUÍ
# =============================================================================

# Lista de tickers a monitorear
TICKERS = ["AAPL", "MSFT", "TSLA", "NVDA", "AMD", "AMZN", "GOOGL"]

# Parámetros de indicadores
RSI_PERIOD    = 14    # Períodos para calcular el RSI
SMA_PERIOD    = 50    # Períodos para la Media Móvil Simple
RSI_UMBRAL    = 35    # RSI por debajo de este valor = sobreventa
SMA_MARGEN    = 1.01  # El precio puede estar hasta un 1% SOBRE la SMA (zona de contacto)

# Período de datos históricos descargado por yfinance
# "6mo" da suficientes velas para calcular SMA(50) con datos diarios
PERIODO_DATA  = "6mo"
INTERVALO     = "1d"  # Velas diarias

# Telegram (dejar en None si no quieres enviar mensajes)
TELEGRAM_BOT_TOKEN = 8697480527:AAG8Chyrt3glCUT4ai4d9WWEqS_RrxVzmlk   # Ejemplo: "123456:ABC-DEF..."
TELEGRAM_CHAT_ID   = -1004297177989   # Ejemplo: "-1001234567890"

# =============================================================================
# 📊  CÁLCULO DE INDICADORES
# =============================================================================

def calcular_rsi(serie: pd.Series, periodos: int = 14) -> pd.Series:
    """
    Calcula el RSI (Relative Strength Index) usando el método de Wilder
    (EWM con com = periodos - 1), que es el estándar de la industria.

    Args:
        serie:    Serie de precios de cierre.
        periodos: Ventana del RSI (default 14).

    Returns:
        Serie de RSI, misma longitud que la entrada.
    """
    delta = serie.diff()

    # Separar ganancias y pérdidas
    ganancias = delta.clip(lower=0)
    perdidas  = (-delta).clip(lower=0)

    # Media exponencial ponderada con suavizado de Wilder
    avg_ganancia = ganancias.ewm(com=periodos - 1, min_periods=periodos).mean()
    avg_perdida  = perdidas.ewm(com=periodos - 1, min_periods=periodos).mean()

    # Evitar división por cero
    rs = avg_ganancia / avg_perdida.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calcular_sma(serie: pd.Series, periodos: int = 50) -> pd.Series:
    """
    Calcula la Media Móvil Simple (SMA).

    Args:
        serie:    Serie de precios de cierre.
        periodos: Ventana de la SMA (default 50).

    Returns:
        Serie de SMA, con NaN en las primeras (periodos - 1) filas.
    """
    return serie.rolling(window=periodos).mean()


# =============================================================================
# 📥  DESCARGA Y ANÁLISIS DE DATOS
# =============================================================================

def obtener_datos(ticker: str) -> dict | None:
    """
    Descarga datos históricos de un ticker con yfinance y calcula RSI + SMA.

    Args:
        ticker: Símbolo bursátil (e.g. "AAPL").

    Returns:
        Diccionario con métricas del último día, o None si falla la descarga.
    """
    try:
        df = yf.download(
            ticker,
            period=PERIODO_DATA,
            interval=INTERVALO,
            progress=False,     # Silencia la barra de progreso en Colab
            auto_adjust=True,   # Ajusta splits y dividendos automáticamente
        )

        # Validar que tenemos datos suficientes
        if df.empty or len(df) < SMA_PERIOD:
            print(f"  ⚠️  {ticker}: datos insuficientes ({len(df)} velas). Saltando.")
            return None

        # Extraer serie de cierre (yfinance devuelve MultiIndex si es un solo ticker)
        cierre = df["Close"].squeeze()

        # Calcular indicadores
        rsi = calcular_rsi(cierre, RSI_PERIOD)
        sma = calcular_sma(cierre, SMA_PERIOD)

        # Tomar valores del ÚLTIMO período disponible
        precio_actual = float(cierre.iloc[-1])
        rsi_actual    = float(rsi.iloc[-1])
        sma_actual    = float(sma.iloc[-1])
        fecha         = cierre.index[-1].strftime("%Y-%m-%d")

        return {
            "ticker":  ticker,
            "fecha":   fecha,
            "precio":  round(precio_actual, 2),
            "rsi":     round(rsi_actual, 2),
            "sma50":   round(sma_actual, 2),
        }

    except Exception as e:
        print(f"  ❌  Error al procesar {ticker}: {e}")
        return None


# =============================================================================
# 🚨  DETECCIÓN DE ALERTAS
# =============================================================================

def evaluar_condicion(datos: dict) -> dict | None:
    """
    Aplica la lógica de la estrategia de reversión a la media.

    Condición de alerta ACTIVADA si:
        - RSI < RSI_UMBRAL  (zona de sobreventa)
        - Precio ≤ SMA50 * SMA_MARGEN  (cerca o por debajo de la media)

    Args:
        datos: Resultado de obtener_datos().

    Returns:
        Diccionario de alerta (con recomendación) si se activa, None si no.
    """
    if datos is None:
        return None

    rsi    = datos["rsi"]
    precio = datos["precio"]
    sma50  = datos["sma50"]

    condicion_rsi   = rsi < RSI_UMBRAL
    condicion_sma   = precio <= sma50 * SMA_MARGEN

    if condicion_rsi and condicion_sma:
        # Construir frase de recomendación según intensidad del RSI
        if rsi < 25:
            fuerza      = "EXTREMA sobreventa"
            recomendacion = f"RSI={rsi} en zona crítica — posible rebote fuerte. Considerar entrada con stop ajustado."
        elif rsi < 30:
            fuerza      = "Sobreventa fuerte"
            recomendacion = f"RSI={rsi} bajo SMA50 — señal de reversión activa. Esperar confirmación en vela siguiente."
        else:
            fuerza      = "Sobreventa moderada"
            recomendacion = f"RSI={rsi} tocando SMA50 — zona de soporte. Vigilar volumen y vela de confirmación."

        return {
            "ticker":          datos["ticker"],
            "fecha":           datos["fecha"],
            "precio_actual":   datos["precio"],
            "rsi_14":          rsi,
            "sma_50":          sma50,
            "condicion":       fuerza,
            "recomendacion":   recomendacion,
        }

    return None


# =============================================================================
# 📬  ENVÍO A TELEGRAM (opcional)
# =============================================================================

def enviar_telegram(mensaje: str) -> bool:
    """
    Envía un mensaje de texto al canal o chat de Telegram configurado.

    Args:
        mensaje: Texto del mensaje (acepta Markdown).

    Returns:
        True si el envío fue exitoso, False si falló.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False  # No configurado — omitir silenciosamente

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       mensaje,
        "parse_mode": "Markdown",
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            print(f"  ⚠️  Telegram error {resp.status_code}: {resp.text}")
            return False
    except requests.RequestException as e:
        print(f"  ❌  No se pudo conectar a Telegram: {e}")
        return False


def formatear_mensaje_telegram(alerta: dict) -> str:
    """
    Formatea una alerta como mensaje Markdown para Telegram.

    Args:
        alerta: Diccionario generado por evaluar_condicion().

    Returns:
        String con formato Telegram Markdown.
    """
    return (
        f"🚨 *ALERTA DE TRADING* — `{alerta['ticker']}`\n"
        f"📅 Fecha: {alerta['fecha']}\n"
        f"💰 Precio: `${alerta['precio_actual']}`\n"
        f"📉 RSI(14): `{alerta['rsi_14']}` ← _{alerta['condicion']}_\n"
        f"📊 SMA(50): `${alerta['sma_50']}`\n"
        f"💡 {alerta['recomendacion']}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )


# =============================================================================
# 🔄  MOTOR PRINCIPAL
# =============================================================================

def ejecutar_analisis() -> list[dict]:
    """
    Orquesta el análisis completo:
      1. Descarga datos de todos los tickers.
      2. Evalúa condiciones de alerta.
      3. Imprime resultados y envía alertas a Telegram.

    Returns:
        Lista de diccionarios de alertas detectadas.
    """
    print("=" * 60)
    print("  🤖  BOT DE ALERTAS DE TRADING  |  Estrategia: Reversión")
    print(f"  🕒  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print(f"  Analizando {len(TICKERS)} tickers: {', '.join(TICKERS)}\n")

    alertas = []

    for ticker in TICKERS:
        print(f"  📡  Procesando {ticker}...", end=" ")
        datos  = obtener_datos(ticker)
        alerta = evaluar_condicion(datos)

        if alerta:
            alertas.append(alerta)
            print(f"🚨 ALERTA ACTIVADA  (RSI={alerta['rsi_14']})")

            # Enviar a Telegram si está configurado
            mensaje = formatear_mensaje_telegram(alerta)
            if enviar_telegram(mensaje):
                print(f"       ✅  Enviado a Telegram.")
        else:
            if datos:
                print(f"✅ Sin señal  (RSI={datos['rsi']}, Precio=${datos['precio']}, SMA50=${datos['sma50']})")

        time.sleep(0.5)  # Pequeña pausa para no saturar la API de yfinance

    # ── Resumen final ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  📋  RESUMEN: {len(alertas)} alerta(s) detectada(s) de {len(TICKERS)} tickers")
    print("=" * 60)

    if alertas:
        print("\n📊  ALERTAS EN JSON:\n")
        print(json.dumps(alertas, indent=2, ensure_ascii=False))
    else:
        print("\n  ✅  Sin alertas activas. El mercado está en zona neutral.")

    return alertas


# =============================================================================
# ▶️  PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    alertas = ejecutar_analisis()

    # Guardar resultado en archivo JSON (útil en Colab para descargar)
    with open("alertas_trading.json", "w", encoding="utf-8") as f:
        json.dump(alertas, f, indent=2, ensure_ascii=False)
    print("\n  💾  Resultados guardados en: alertas_trading.json")
