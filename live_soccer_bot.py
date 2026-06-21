# -*- coding: utf-8 -*-
"""
live_soccer_bot.py
==================
Bot de alertas de fútbol EN VIVO para la suite de automatización (Google Colab).

Fuente de datos : SportAPI (sportapi7.p.rapidapi.com) — wrapper de Sofascore vía RapidAPI.
Salida          : Telegram (texto plano, SIN parse_mode para evitar errores de Markdown).
Filosofía       : CONFIG-first, manejo de excepciones en cada llamada de red, sin librerías
                  externas complejas (solo `requests` + utilidades nativas), modo DEMO de respaldo.

LÓGICA DE LA SEÑAL ("equipo que domina pero no es premiado")
------------------------------------------------------------
Para cada partido activo cuyo minuto esté entre 60 y 80:
  1) Un equipo se considera DOMINANTE si:
        - su posesión es MAYOR al 60%, O
        - su diferencia de tiros a puerta es >= 3 a su favor.
  2) Se dispara ALERTA si ese mismo equipo dominante va:
        - empatado, O
        - perdiendo por exactamente 1 gol.
"""

import time
import html
import requests

# =============================================================================
# 1. CONFIG CENTRALIZADO
# =============================================================================
CONFIG = {
    # --- Credenciales ---
    "SPORTAPI_KEY":        "TU_RAPIDAPI_KEY_AQUI",          # x-rapidapi-key de SportAPI
    "TELEGRAM_BOT_TOKEN":  "TU_TELEGRAM_BOT_TOKEN_AQUI",     # token de @BotFather
    "TELEGRAM_CHAT_ID":    "TU_CHAT_ID_AQUI",                # CHAT_ID centralizado

    # --- Endpoints SportAPI ---
    "API_HOST":            "sportapi7.p.rapidapi.com",
    "LIVE_EVENTS_URL":     "https://sportapi7.p.rapidapi.com/api/v1/sport/football/events/live",
    "STATISTICS_URL":      "https://sportapi7.p.rapidapi.com/api/v1/event/{event_id}/statistics",

    # --- Parámetros de la señal ---
    "MIN_MINUTE":          60,    # ventana de tiempo (inclusive)
    "MAX_MINUTE":          80,
    "POSSESSION_THRESHOLD": 60.0, # posesión MAYOR a este valor (> 60%)
    "SOT_DIFF_THRESHOLD":  3,     # diferencia de tiros a puerta >= 3

    # --- Operación ---
    "REQUEST_TIMEOUT":     15,    # segundos por llamada de red
    "RUN_CONTINUOUS":      False, # True = loop infinito; False = un solo escaneo
    "SCAN_INTERVAL_SEC":   90,    # pausa entre escaneos cuando RUN_CONTINUOUS=True
    "DEMO_MODE":           False, # True = simula un partido para probar el pipeline de Telegram
}

# Memoria de la sesión para no repetir la misma alerta por el mismo partido.
_ALERTED_EVENTS = set()


# =============================================================================
# 2. UTILIDADES DE RED (todas blindadas con try/except)
# =============================================================================
def _api_headers():
    """Headers requeridos por RapidAPI."""
    return {
        "x-rapidapi-key":  CONFIG["SPORTAPI_KEY"],
        "x-rapidapi-host": CONFIG["API_HOST"],
    }


def _request_json(url, headers=None, params=None):
    """
    GET genérico que SIEMPRE devuelve dict o None. Nunca lanza excepción hacia afuera:
    así el pipeline jamás muere por un error de red.
    """
    try:
        resp = requests.get(
            url,
            headers=headers or {},
            params=params,
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        print(f"[RED] Timeout consultando: {url}")
    except requests.exceptions.HTTPError as e:
        print(f"[RED] HTTP {getattr(e.response, 'status_code', '??')} en: {url}")
    except requests.exceptions.RequestException as e:
        print(f"[RED] Error de conexión en {url}: {e}")
    except ValueError:
        print(f"[RED] Respuesta no es JSON válido: {url}")
    except Exception as e:
        print(f"[RED] Error inesperado en {url}: {e}")
    return None


def get_live_events():
    """Devuelve la lista de eventos de fútbol en vivo (o lista vacía)."""
    data = _request_json(CONFIG["LIVE_EVENTS_URL"], headers=_api_headers())
    if not data:
        return []
    events = data.get("events", [])
    print(f"[INFO] Partidos en vivo recibidos: {len(events)}")
    return events


def get_event_statistics(event_id):
    """Devuelve el JSON de estadísticas de un partido (o None)."""
    url = CONFIG["STATISTICS_URL"].format(event_id=event_id)
    return _request_json(url, headers=_api_headers())


def send_telegram(text):
    """Envía un mensaje de TEXTO PLANO a Telegram. Sin parse_mode a propósito."""
    token = CONFIG["TELEGRAM_BOT_TOKEN"]
    chat_id = CONFIG["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}  # <- nada de parse_mode
    try:
        resp = requests.post(url, data=payload, timeout=CONFIG["REQUEST_TIMEOUT"])
        resp.raise_for_status()
        print("[TELEGRAM] Alerta enviada correctamente.")
        return True
    except Exception as e:
        print(f"[TELEGRAM] No se pudo enviar el mensaje: {e}")
        return False


# =============================================================================
# 3. PARSEO DE DATOS DEL PARTIDO
# =============================================================================
def compute_minute(event):
    """
    Calcula el minuto aproximado del partido a partir del timestamp de inicio del
    periodo actual. Devuelve int o None si no se puede determinar.

    Códigos de estado relevantes de Sofascore:
        6  = 1er tiempo, 7 = 2do tiempo, 31 = entretiempo.
    """
    try:
        status = event.get("status", {}) or {}
        code = status.get("code")
        desc = (status.get("description") or "").lower()

        # Base según el periodo
        if code == 7 or "2nd" in desc or "second" in desc:
            base = 45
        elif code == 6 or "1st" in desc or "first" in desc:
            base = 0
        elif code == 31 or "halftime" in desc:
            return 45  # entretiempo: fuera de la ventana de interés
        else:
            base = 0  # fallback prudente

        time_obj = event.get("time", {}) or {}
        period_start = time_obj.get("currentPeriodStartTimestamp")
        if not period_start:
            return None

        elapsed = int((time.time() - period_start) / 60)
        if elapsed < 0:
            elapsed = 0
        minute = base + elapsed + 1  # +1 para alinear con el reloj visible
        return minute
    except Exception as e:
        print(f"[PARSE] No se pudo calcular el minuto: {e}")
        return None


def _to_number(raw):
    """Convierte '60%' -> 60.0, '5' -> 5.0, devuelve None si no se puede."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def extract_stats(stats_json):
    """
    Recorre las estadísticas del periodo 'ALL' y extrae posesión y tiros a puerta.
    Devuelve dict: {pos_home, pos_away, sot_home, sot_away} o None si falta info clave.
    Prioriza los campos numéricos homeValue/awayValue; si no existen, parsea los strings.
    """
    if not stats_json:
        return None
    try:
        result = {"pos_home": None, "pos_away": None, "sot_home": None, "sot_away": None}

        for period_block in stats_json.get("statistics", []):
            if period_block.get("period", "ALL") != "ALL":
                continue
            for group in period_block.get("groups", []):
                for item in group.get("statisticsItems", []):
                    name = (item.get("name") or "").lower()
                    home_val = item.get("homeValue", item.get("home"))
                    away_val = item.get("awayValue", item.get("away"))

                    if "ball possession" in name or name == "possession":
                        result["pos_home"] = _to_number(home_val)
                        result["pos_away"] = _to_number(away_val)
                    elif "shots on target" in name or "on target" in name:
                        result["sot_home"] = _to_number(home_val)
                        result["sot_away"] = _to_number(away_val)

        # Solo es útil si tenemos al menos un par de métricas completas
        has_pos = result["pos_home"] is not None and result["pos_away"] is not None
        has_sot = result["sot_home"] is not None and result["sot_away"] is not None
        if not (has_pos or has_sot):
            return None
        return result
    except Exception as e:
        print(f"[PARSE] Error extrayendo estadísticas: {e}")
        return None


# =============================================================================
# 4. MOTOR DE LA SEÑAL
# =============================================================================
def evaluate_alert(scores, stats):
    """
    Evalúa la condición de alerta para ambos equipos.
    Devuelve el nombre del equipo dominante ('home'/'away') que activa la señal, o None.
    """
    pos = {"home": stats.get("pos_home"), "away": stats.get("pos_away")}
    sot = {"home": stats.get("sot_home"), "away": stats.get("sot_away")}

    for team, rival in (("home", "away"), ("away", "home")):
        # --- Condición de DOMINIO ---
        possession_dom = pos[team] is not None and pos[team] > CONFIG["POSSESSION_THRESHOLD"]

        sot_dom = False
        if sot[team] is not None and sot[rival] is not None:
            sot_dom = (sot[team] - sot[rival]) >= CONFIG["SOT_DIFF_THRESHOLD"]

        if not (possession_dom or sot_dom):
            continue

        # --- Condición de MARCADOR (empatado o perdiendo por 1) ---
        diff = scores[team] - scores[rival]
        if diff == 0 or diff == -1:
            return team  # equipo dominante que aún no es premiado

    return None


def build_message(event, minute, scores, stats, dominant):
    """Construye el mensaje de texto plano exactamente con el formato solicitado."""
    home = event.get("homeTeam", {}).get("name", "Local")
    away = event.get("awayTeam", {}).get("name", "Visitante")
    tournament = event.get("tournament", {}).get("name", "Torneo")

    pos_home = int(stats["pos_home"]) if stats.get("pos_home") is not None else "?"
    pos_away = int(stats["pos_away"]) if stats.get("pos_away") is not None else "?"
    k_home = int(stats["sot_home"]) if stats.get("sot_home") is not None else "?"
    k_away = int(stats["sot_away"]) if stats.get("sot_away") is not None else "?"

    dom_name = home if dominant == "home" else away

    msg = (
        "🚨 ALERTA EN VIVO — SUSH FLOW STUDIO\n"
        f"🏟️ Partido: {home} vs {away} [{tournament}]\n"
        f"⏱️ Minuto: {minute} | Marcador: {scores['home']}-{scores['away']}\n"
        f"📊 Estadística Clave: Posesión {pos_home}%-{pos_away}% | Tiros a Puerta: {k_home}-{k_away}\n"
        "🎯 Pick Recomendado: Over Goles Línea Asiática o Empate No Acción en Vivo.\n"
        f"🔎 Dominio sin premio: {dom_name}"
    )
    return msg


# =============================================================================
# 5. ESCANEO PRINCIPAL
# =============================================================================
def run_scan():
    """Un ciclo completo: trae partidos en vivo, filtra, evalúa y alerta."""
    if CONFIG["DEMO_MODE"]:
        return _run_demo()

    events = get_live_events()
    alerts_sent = 0

    for event in events:
        try:
            event_id = event.get("id")
            if event_id is None:
                continue

            # --- Filtro 1 (barato): ventana de minuto 60-80 ---
            minute = compute_minute(event)
            if minute is None or not (CONFIG["MIN_MINUTE"] <= minute <= CONFIG["MAX_MINUTE"]):
                continue

            # Evita re-alertar el mismo partido durante la sesión
            if event_id in _ALERTED_EVENTS:
                continue

            scores = {
                "home": (event.get("homeScore", {}) or {}).get("current", 0),
                "away": (event.get("awayScore", {}) or {}).get("current", 0),
            }

            # --- Filtro 2 (caro): estadísticas solo de los partidos en ventana ---
            stats = extract_stats(get_event_statistics(event_id))
            if not stats:
                continue

            dominant = evaluate_alert(scores, stats)
            if dominant:
                message = build_message(event, minute, scores, stats, dominant)
                if send_telegram(message):
                    _ALERTED_EVENTS.add(event_id)
                    alerts_sent += 1

        except Exception as e:
            # Cualquier fallo con un partido NO debe detener el escaneo de los demás
            print(f"[SCAN] Error procesando un partido: {e}")
            continue

    print(f"[SCAN] Escaneo finalizado. Alertas enviadas: {alerts_sent}")
    return alerts_sent


def _run_demo():
    """Simula un partido que cumple la señal para validar el pipeline de Telegram."""
    print("[DEMO] Modo demostración activo (no se consulta SportAPI).")
    event = {
        "id": "DEMO-001",
        "homeTeam": {"name": "Cruz Azul"},
        "awayTeam": {"name": "América"},
        "tournament": {"name": "Liga MX"},
    }
    scores = {"home": 0, "away": 1}                       # local perdiendo por 1
    stats = {"pos_home": 67.0, "pos_away": 33.0,          # local domina posesión
             "sot_home": 7.0, "sot_away": 2.0}            # y tiros a puerta
    minute = 72
    dominant = evaluate_alert(scores, stats)
    if dominant:
        send_telegram(build_message(event, minute, scores, stats, dominant))
    return 1


def main():
    """Punto de entrada: un solo escaneo o loop continuo según CONFIG."""
    print("=" * 60)
    print("  LIVE SOCCER BOT — SUSH FLOW STUDIO")
    print("=" * 60)

    if not CONFIG["RUN_CONTINUOUS"]:
        run_scan()
        return

    print(f"[LOOP] Modo continuo activo. Intervalo: {CONFIG['SCAN_INTERVAL_SEC']}s")
    while True:
        try:
            run_scan()
            time.sleep(CONFIG["SCAN_INTERVAL_SEC"])
        except KeyboardInterrupt:
            print("\n[LOOP] Detenido por el usuario.")
            break
        except Exception as e:
            print(f"[LOOP] Error en el ciclo, continuando: {e}")
            time.sleep(CONFIG["SCAN_INTERVAL_SEC"])


if __name__ == "__main__":
    main()
