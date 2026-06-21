# -*- coding: utf-8 -*-
"""
tennis_analyzer_bot.py
======================
Analizador de cartelera diaria de Tenis (ATP / WTA) con entrega a Telegram.

Fuente de datos : SportAPI7 (sportapi7.p.rapidapi.com) - wrapper de Sofascore vía RapidAPI.
Salida          : Reporte en TEXTO PLANO (sin Markdown) + diccionario para Jupyter/Colab.

Lógica del modelo
-----------------
1. Descarga los scheduled-events del día en tenis.
2. Filtra torneos cuya categoría contenga "ATP" o "WTA".
3. Por cada partido calcula un Índice de Consistencia = tasa de sets ganados
   en los últimos encuentros de cada jugador.
4. Genera ALERTA DE ALTO VALOR cuando:
       favorito  -> set win rate > HIGH_SET_WIN_RATE (default 75%)
       rival     -> set win rate < LOW_SET_WIN_RATE  (default 40%)
   La lectura del modelo es: el favorito gana en sets corridos.

Autor: Quant Software Engineer — Sush Flow Studio
Compatible con Google Colab y ejecución local.
"""

import time
import datetime
import requests

# =====================================================================
# 1. CONFIGURACIÓN CENTRALIZADA
# =====================================================================
CONFIG = {
    # ---- Credenciales RapidAPI (SportAPI7 / Sofascore) -------------
    "RAPIDAPI_KEY":   "TU_RAPIDAPI_KEY_AQUI",
    "RAPIDAPI_HOST":  "sportapi7.p.rapidapi.com",
    "BASE_URL":       "https://sportapi7.p.rapidapi.com/api/v1",

    # ---- Telegram --------------------------------------------------
    "TELEGRAM_BOT_TOKEN": "TU_TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID":   "TU_CHAT_ID",

    # ---- Parámetros del deporte ------------------------------------
    "SPORT_PATH":          "tennis",
    "ALLOWED_CATEGORIES":  ["ATP", "WTA"],   # filtro por nombre de categoría

    # ---- Umbrales del modelo de consistencia -----------------------
    "HIGH_SET_WIN_RATE":   0.75,   # favorito: > 75% de sets ganados
    "LOW_SET_WIN_RATE":    0.40,   # rival:    < 40% de sets ganados
    "RECENT_MATCHES":      10,     # nº de encuentros recientes a evaluar
    "MIN_SETS_SAMPLE":     6,      # mínimo de sets jugados para confiar en la tasa

    # ---- Control de ejecución --------------------------------------
    "REQUEST_TIMEOUT":     20,     # segundos por request
    "REQUEST_DELAY":       0.6,    # pausa entre llamadas para no saturar la API
    "DEMO_MODE":           True,   # True = usa datos simulados si no hay API real
    "SEND_TO_TELEGRAM":    True,   # False = solo construye el reporte sin enviar
    "STUDIO_NAME":         "SUSH FLOW STUDIO",
}


# =====================================================================
# 2. UTILIDADES HTTP
# =====================================================================
def _headers():
    """Headers requeridos por RapidAPI."""
    return {
        "x-rapidapi-key":  CONFIG["RAPIDAPI_KEY"],
        "x-rapidapi-host": CONFIG["RAPIDAPI_HOST"],
        "Accept":          "application/json",
    }


def _get(endpoint):
    """
    GET genérico contra SportAPI7. Devuelve dict (JSON) o None ante cualquier fallo.
    Nunca lanza excepción hacia arriba: el flujo continúa con datos parciales/demo.
    """
    url = f"{CONFIG['BASE_URL']}{endpoint}"
    try:
        resp = requests.get(url, headers=_headers(),
                            timeout=CONFIG["REQUEST_TIMEOUT"])
        time.sleep(CONFIG["REQUEST_DELAY"])
        if resp.status_code == 200:
            return resp.json()
        print(f"[WARN] {endpoint} -> HTTP {resp.status_code}")
        return None
    except requests.RequestException as e:
        print(f"[WARN] Error de red en {endpoint}: {e}")
        return None


# =====================================================================
# 3. DESCARGA Y FILTRADO DE LA CARTELERA
# =====================================================================
def fetch_scheduled_events(date_str=None):
    """
    Descarga los scheduled-events de tenis para la fecha indicada (YYYY-MM-DD).
    Si DEMO_MODE está activo y la API no responde, regresa cartelera simulada.
    """
    if date_str is None:
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    endpoint = f"/sport/{CONFIG['SPORT_PATH']}/scheduled-events/{date_str}"
    data = _get(endpoint)

    if data and isinstance(data.get("events"), list) and data["events"]:
        return data["events"], date_str

    if CONFIG["DEMO_MODE"]:
        print("[INFO] Usando cartelera DEMO (sin datos reales de la API).")
        return _demo_events(), date_str

    return [], date_str


def filter_important_tournaments(events):
    """
    Conserva solo eventos cuya categoría (category.name) contenga ATP o WTA.
    Lectura defensiva de la estructura típica de Sofascore.
    """
    keep = []
    for ev in events:
        tournament = ev.get("tournament", {}) or {}
        category   = tournament.get("category", {}) or {}
        cat_name   = (category.get("name") or "").upper()
        if any(tag in cat_name for tag in CONFIG["ALLOWED_CATEGORIES"]):
            keep.append(ev)
    return keep


# =====================================================================
# 4. ÍNDICE DE CONSISTENCIA (TASA DE SETS GANADOS RECIENTES)
# =====================================================================
def fetch_player_set_rate(player_id, player_name=""):
    """
    Calcula la tasa de sets ganados de un jugador en sus últimos encuentros.

    En Sofascore los jugadores de tenis se modelan como 'team'; el histórico se
    consulta vía /team/{id}/events/last/{page}. Para cada partido finalizado se
    suman los sets ganados/perdidos del jugador y se obtiene la proporción.

    Devuelve: (set_win_rate float[0..1], sets_jugados int)
    """
    if not player_id:
        return _demo_set_rate(player_name)

    data = _get(f"/team/{player_id}/events/last/0")
    if not data or not isinstance(data.get("events"), list):
        if CONFIG["DEMO_MODE"]:
            return _demo_set_rate(player_name)
        return None, 0

    sets_won = 0
    sets_lost = 0
    matches = data["events"][: CONFIG["RECENT_MATCHES"]]

    for m in matches:
        status = (m.get("status", {}) or {}).get("type")
        if status != "finished":
            continue

        home = m.get("homeTeam", {}) or {}
        is_home = home.get("id") == player_id

        # period scores -> sets; en Sofascore vienen como period1..period5
        home_score = m.get("homeScore", {}) or {}
        away_score = m.get("awayScore", {}) or {}

        for p in ("period1", "period2", "period3", "period4", "period5"):
            hp = home_score.get(p)
            ap = away_score.get(p)
            if hp is None or ap is None:
                continue
            # quien ganó el set (en tenis el juego se decide por games del set)
            player_games   = hp if is_home else ap
            opponent_games = ap if is_home else hp
            if player_games > opponent_games:
                sets_won += 1
            elif opponent_games > player_games:
                sets_lost += 1

    total = sets_won + sets_lost
    if total == 0:
        if CONFIG["DEMO_MODE"]:
            return _demo_set_rate(player_name)
        return None, 0

    return sets_won / total, total


def _extract_players(event):
    """Devuelve (id1, nombre1, id2, nombre2) de forma defensiva."""
    home = event.get("homeTeam", {}) or {}
    away = event.get("awayTeam", {}) or {}
    return (
        home.get("id"), home.get("name", "Jugador 1"),
        away.get("id"), away.get("name", "Jugador 2"),
    )


def analyze_match(event):
    """
    Evalúa un partido y devuelve un dict con métricas + flag de alerta.
    Estructura lista para visualizar en un DataFrame de pandas si se desea.
    """
    id1, name1, id2, name2 = _extract_players(event)
    tournament = (event.get("tournament", {}) or {}).get("name", "Torneo")

    rate1, n1 = fetch_player_set_rate(id1, name1)
    rate2, n2 = fetch_player_set_rate(id2, name2)

    result = {
        "partido":        f"{name1} vs {name2}",
        "torneo":         tournament,
        "jugador1":       name1,
        "jugador2":       name2,
        "set_rate_j1":    round(rate1, 3) if rate1 is not None else None,
        "set_rate_j2":    round(rate2, 3) if rate2 is not None else None,
        "sets_muestra_j1": n1,
        "sets_muestra_j2": n2,
        "alerta_alto_valor": False,
        "favorito":       None,
    }

    if rate1 is None or rate2 is None:
        return result

    hi  = CONFIG["HIGH_SET_WIN_RATE"]
    lo  = CONFIG["LOW_SET_WIN_RATE"]
    mn  = CONFIG["MIN_SETS_SAMPLE"]

    # Muestra mínima para evitar señales con poca data
    if n1 < mn or n2 < mn:
        return result

    if rate1 > hi and rate2 < lo:
        result["alerta_alto_valor"] = True
        result["favorito"] = name1
    elif rate2 > hi and rate1 < lo:
        result["alerta_alto_valor"] = True
        result["favorito"] = name2

    return result


# =====================================================================
# 5. CONSTRUCCIÓN Y ENVÍO DEL REPORTE (TEXTO PLANO)
# =====================================================================
def clean_plain_text(text):
    """
    Elimina caracteres que disparan parseo Markdown en Telegram.
    Se envía SIN parse_mode, pero se sanea por seguridad para evitar Bad Request 400.
    """
    for ch in ("*", "_", "`", "[", "]"):
        text = text.replace(ch, "")
    return text


def build_match_block(match):
    """Bloque de reporte para un partido con alerta de alto valor."""
    lines = [
        "🎾 TENNIS ANALYTICS VIP — " + CONFIG["STUDIO_NAME"],
        "🏟️ Partido: " + match["partido"],
        "🏆 Torneo: " + match["torneo"],
        "📈 Probabilidad Modelo: " + str(match["favorito"]) + " gana en Sets Corridos.",
        "📊 Consistencia (sets ganados): "
        + f"{match['jugador1']} {int((match['set_rate_j1'] or 0)*100)}% | "
        + f"{match['jugador2']} {int((match['set_rate_j2'] or 0)*100)}%",
        "⚠️ Apuesta con responsabilidad.",
        "————————————————————————",
    ]
    return "\n".join(lines)


def build_full_report(alerts, date_str, total_scanned):
    """Reporte completo en texto plano."""
    if not alerts:
        return (
            f"🎾 TENNIS ANALYTICS VIP — {CONFIG['STUDIO_NAME']}\n"
            f"📅 Fecha: {date_str}\n"
            f"🔎 Partidos ATP/WTA escaneados: {total_scanned}\n"
            f"Sin alertas de alto valor para hoy.\n"
            f"⚠️ Apuesta con responsabilidad."
        )

    header = (
        f"🎾 TENNIS ANALYTICS VIP — {CONFIG['STUDIO_NAME']}\n"
        f"📅 Fecha: {date_str} | Alertas: {len(alerts)} / {total_scanned} partidos\n"
        f"————————————————————————"
    )
    body = "\n".join(build_match_block(a) for a in alerts)
    return clean_plain_text(header + "\n" + body)


def send_telegram_message(text):
    """Envía el reporte a Telegram en texto plano (sin parse_mode)."""
    token   = CONFIG["TELEGRAM_BOT_TOKEN"]
    chat_id = CONFIG["TELEGRAM_CHAT_ID"]
    url     = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text":    text,
        "disable_web_page_preview": True,
        # Nota: NO se incluye parse_mode -> evita Bad Request 400 por Markdown.
    }
    try:
        r = requests.post(url, data=payload, timeout=CONFIG["REQUEST_TIMEOUT"])
        if r.status_code == 200:
            print("[OK] Reporte enviado a Telegram.")
            return True
        print(f"[WARN] Telegram HTTP {r.status_code}: {r.text[:160]}")
        return False
    except requests.RequestException as e:
        print(f"[WARN] Error enviando a Telegram: {e}")
        return False


# =====================================================================
# 6. ORQUESTADOR PRINCIPAL
# =====================================================================
def run_tennis_analyzer(date_str=None):
    """
    Ejecuta todo el pipeline y devuelve un diccionario con los resultados,
    pensado para inspección en Jupyter/Colab.

    return = {
        "fecha": str,
        "total_escaneados": int,
        "alertas": [ {match...}, ... ],
        "todos_los_partidos": [ {match...}, ... ],
        "reporte_texto": str,
        "enviado_telegram": bool,
    }
    """
    events, date_str = fetch_scheduled_events(date_str)
    important = filter_important_tournaments(events)
    print(f"[INFO] {len(important)} partidos ATP/WTA encontrados para {date_str}.")

    analyzed = [analyze_match(ev) for ev in important]
    alerts   = [m for m in analyzed if m["alerta_alto_valor"]]

    report = build_full_report(alerts, date_str, len(important))
    print("\n" + report + "\n")

    sent = False
    if CONFIG["SEND_TO_TELEGRAM"]:
        sent = send_telegram_message(report)

    return {
        "fecha":              date_str,
        "total_escaneados":   len(important),
        "alertas":            alerts,
        "todos_los_partidos": analyzed,
        "reporte_texto":      report,
        "enviado_telegram":   sent,
    }


# =====================================================================
# 7. DATOS DEMO (fallback offline / sin API real)
# =====================================================================
def _demo_events():
    """Cartelera simulada con estructura compatible con Sofascore."""
    return [
        {
            "homeTeam": {"id": 101, "name": "C. Alcaraz"},
            "awayTeam": {"id": 102, "name": "J. Smith"},
            "tournament": {"name": "ATP Masters 1000",
                          "category": {"name": "ATP"}},
        },
        {
            "homeTeam": {"id": 201, "name": "I. Swiatek"},
            "awayTeam": {"id": 202, "name": "M. Lopez"},
            "tournament": {"name": "WTA 1000",
                          "category": {"name": "WTA"}},
        },
        {
            "homeTeam": {"id": 301, "name": "Player Local"},
            "awayTeam": {"id": 302, "name": "Player Visitante"},
            "tournament": {"name": "ITF Future",
                          "category": {"name": "ITF"}},  # se filtra fuera
        },
    ]


def _demo_set_rate(player_name):
    """Tasas de sets simuladas determinísticas por nombre (para reproducibilidad)."""
    demo_table = {
        "C. Alcaraz":       (0.86, 14),
        "J. Smith":         (0.32, 11),
        "I. Swiatek":       (0.81, 16),
        "M. Lopez":         (0.38, 10),
        "Player Local":     (0.55, 8),
        "Player Visitante": (0.50, 8),
    }
    return demo_table.get(player_name, (0.50, 8))


# =====================================================================
# 8. ENTRADA
# =====================================================================
if __name__ == "__main__":
    resultados = run_tennis_analyzer()
    # En Colab/Jupyter puedes hacer:
    #   import pandas as pd
    #   pd.DataFrame(resultados["todos_los_partidos"])
