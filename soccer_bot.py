# =============================================================================
# soccer_bot.py
# Bot de análisis estadístico para fútbol internacional
# Mundial de Norteamérica 2026 + Ligas Europeas y Locales
# Compatible con Google Colab
# Fuente de datos: SportAPI (sportapi7.p.rapidapi.com) — wrapper de Sofascore
# =============================================================================

# ── INSTALACIÓN (Colab) ──────────────────────────────────────────────────────
# !pip install requests numpy scipy

# =============================================================================
# SECCIÓN CONFIG — Completar antes de ejecutar
# =============================================================================

CONFIG = {
    # ── SportAPI (RapidAPI) ──────────────────────────────────────────────────
    # Obtén tu key en: https://rapidapi.com/rapidsportapi/api/sportapi7
    "SPORTAPI_KEY": "",

    # ── Telegram ─────────────────────────────────────────────────────────────
    "TELEGRAM_BOT_TOKEN": "TU_BOT_TOKEN_AQUI",

    "CHANNELS": {
        "mundial_2026":   "-100XXXXXXXXXX",
        "premier_league": "-100XXXXXXXXXX",
        "la_liga":        "-100XXXXXXXXXX",
        "serie_a":        "-100XXXXXXXXXX",
        "bundesliga":     "-100XXXXXXXXXX",
        "ligue_1":        "-100XXXXXXXXXX",
        "liga_mx":        "-100XXXXXXXXXX",
        "general":        "-100XXXXXXXXXX",
    },

    # ── Modelo ───────────────────────────────────────────────────────────────
    "EDGE_THRESHOLD_PP":      12,     # pp de ventaja mínima para marcar valor
    "RECENT_MATCHES_WINDOW":   7,     # últimos N partidos para calcular forma
    "OU_LINE":               2.5,     # línea Over/Under
    "MIN_CONFIDENCE":        0.60,    # prob. mínima para publicar pick O/U
    "SHOW_INFORMATIVE":      True,    # mostrar partidos sin edge como info

    # ── Ligas a monitorear ───────────────────────────────────────────────────
    # unique_tournament_id de Sofascore/SportAPI :
    #   (nombre legible, canal Telegram, avg_goles_liga)
    #
    # IDs confirmados de Sofascore (mismos que usa SportAPI):
    #   1    → UEFA Champions League
    #   7    → La Liga
    #   8    → Ligue 1
    #   17   → Premier League
    #   23   → Serie A
    #   35   → Bundesliga
    #   238  → Liga MX Apertura / Clausura
    #   16   → Copa del Mundo  (ID del torneo FIFA WC)
    #
    # TODO: verifica estos IDs en tu panel de RapidAPI:
    #       GET /api/v1/sport/football/unique-tournament/{id}
    #       y compara con lo que ves en la URL de Sofascore.com
    "LEAGUES": {
        17:  ("Premier League",      "premier_league", 1.42),
        7:   ("La Liga",             "la_liga",        1.38),
        23:  ("Serie A",             "serie_a",        1.36),
        35:  ("Bundesliga",          "bundesliga",     1.55),
        8:   ("Ligue 1",             "ligue_1",        1.30),
        238: ("Liga MX",             "liga_mx",        1.30),
        16:  ("Copa del Mundo 2026", "mundial_2026",   1.25),
    },

    # Temporada activa (Sofascore/SportAPI usa el ID numérico de temporada,
    # NO el año). Ver TODO en SportAPIClient.get_season_id_for_league().
    # Este valor se usa como fallback si no se puede obtener dinámicamente.
    "SEASON_FALLBACK_IDS": {
        17:  61643,   # Premier League 2024-25 — TODO: actualizar cada año
        7:   61643,   # La Liga 2024-25          — TODO: actualizar cada año
        23:  61643,   # Serie A 2024-25           — TODO: actualizar cada año
        35:  61643,   # Bundesliga 2024-25        — TODO: actualizar cada año
        8:   61643,   # Ligue 1 2024-25           — TODO: actualizar cada año
        238: 61643,   # Liga MX 2025              — TODO: actualizar cada año
        16:  57478,   # Copa del Mundo 2026       — TODO: verificar en SportAPI
    },
}

# =============================================================================
# IMPORTS
# =============================================================================

import requests
import time
import re
import numpy as np
from datetime import date
from typing import Optional
from scipy.stats import poisson

# =============================================================================
# CLIENTE SPORTAPI  (sportapi7.p.rapidapi.com — wrapper de Sofascore)
# =============================================================================
#
#  ARQUITECTURA DE SPORTAPI / SOFASCORE
#  ─────────────────────────────────────────────────────────────────────────────
#  SportAPI es un wrapper no oficial de Sofascore expuesto en RapidAPI.
#  Sus rutas siguen la misma estructura que la API interna de Sofascore:
#
#  Host:    sportapi7.p.rapidapi.com
#  Prefijo: /api/v1
#
#  ENDPOINTS IMPLEMENTADOS (y cómo encontrar sus nombres exactos):
#  ─────────────────────────────────────────────────────────────────────────────
#  1. FIXTURES DEL DÍA
#     Ruta construida:
#       GET /api/v1/sport/football/scheduled-events/{DATE}
#     Parámetros path: DATE = "YYYY-MM-DD"
#     En el panel RapidAPI busca el endpoint que diga algo como:
#       "Scheduled Events", "Events by date", "Sport Events"
#     Respuesta esperada (campo raíz): "events" → lista de objetos evento
#     Cada evento tiene: id, homeTeam{id,name}, awayTeam{id,name},
#       tournament{name, uniqueTournament{id}}, status{type}
#
#     TODO: Si el endpoint exacto es distinto en tu plan, reemplaza la
#           constante ENDPOINT_SCHEDULED_EVENTS más abajo.
#
#  2. HISTORIAL DE EQUIPO (forma reciente)
#     Ruta construida:
#       GET /api/v1/team/{TEAM_ID}/events/last/{PAGE}
#     Parámetros path: TEAM_ID = int, PAGE = 0 (página más reciente)
#     En el panel RapidAPI busca: "Team Last Events", "Team Events"
#     Respuesta esperada (campo raíz): "events" → lista de eventos pasados
#
#     TODO: Si el endpoint usa query param en lugar de path param, ajusta
#           ENDPOINT_TEAM_LAST_EVENTS y el método _build_url.
#
#  3. HEAD TO HEAD
#     Ruta construida:
#       GET /api/v1/event/{EVENT_ID}/h2h/events
#     Parámetros path: EVENT_ID = int (ID del fixture del día)
#     En el panel RapidAPI busca: "H2H Events", "Head to Head"
#     Respuesta esperada: "previousEvent" (lista) O "events" con H2H
#
#     TODO: Sofascore puede devolver el H2H anidado como
#           data["homeTeamEvents"] / data["awayTeamEvents"] en algunos
#           endpoints. Ajusta parse_sportapi_h2h() si es el caso.
#
#  4. ODDS (cuotas pre-partido)
#     Ruta construida:
#       GET /api/v1/odds/{ODDS_PROVIDER_ID}/recommended-prematch-top-voted
#             /sport/football
#     O bien por evento:
#       GET /api/v1/event/{EVENT_ID}/odds/{ODDS_PROVIDER_ID}
#
#     En el panel RapidAPI busca: "Odds", "Pre-match Odds", "Event Odds"
#     Respuesta esperada: lista de mercados; el mercado "Full time"
#       tiene outcomes: home/draw/away con "fractionalValue" o "decimalOdds"
#
#     TODO: El oddsProviderId varía según el bookmaker. Busca en la
#           documentación de SportAPI el id de Bet365 (suele ser 1 o 2).
#           Asigna el valor en ODDS_PROVIDER_ID más abajo.
#
#  CÓMO OBTENER LOS NOMBRES EXACTOS DESDE TU PANEL RAPIDAPI:
#  ─────────────────────────────────────────────────────────────────────────────
#  1. Ve a https://rapidapi.com/rapidsportapi/api/sportapi7
#  2. Abre el panel "Endpoints" (columna izquierda)
#  3. Para cada grupo, busca los que contengan:
#       - "Scheduled"  →  fixtures del día
#       - "Team" + "Events" o "Last"  →  historial
#       - "H2H" o "Head"  →  H2H
#       - "Odds" o "Prematch"  →  cuotas
#  4. Haz clic en el endpoint y en la sección "Code Snippets" (Python)
#     verás la URL completa con los parámetros de path/query.
#  5. Copia la ruta base (sin el host) y pégala en las constantes abajo.
# =============================================================================

class SportAPIClient:
    """
    Wrapper sobre SportAPI (sportapi7.p.rapidapi.com).
    Implementa los 4 endpoints necesarios para el motor Poisson:
      - scheduled_events  : fixtures del día por deporte
      - team_last_events  : historial reciente de un equipo
      - event_h2h         : enfrentamientos directos de un fixture
      - event_odds        : cuotas pre-partido de un fixture
    """

    BASE_URL = "https://sportapi7.p.rapidapi.com"

    # ── Rutas de endpoint ────────────────────────────────────────────────────
    # TODO: Si SportAPI usa rutas distintas en tu plan, actualiza estas
    #       constantes con las rutas exactas de tu panel RapidAPI.
    #       Usa {placeholders} para los parámetros de path.

    # Fixtures del día (DATE = "YYYY-MM-DD")
    ENDPOINT_SCHEDULED_EVENTS  = "/api/v1/sport/football/scheduled-events/{date}"

    # Últimos partidos de un equipo (PAGE = 0 más reciente)
    ENDPOINT_TEAM_LAST_EVENTS  = "/api/v1/team/{team_id}/events/last/{page}"

    # H2H de un evento específico
    ENDPOINT_EVENT_H2H         = "/api/v1/event/{event_id}/h2h/events"

    # Cuotas pre-partido de un evento
    # TODO: reemplaza {odds_provider_id} según tu plan. Prueba con 1 (Bet365)
    ENDPOINT_EVENT_ODDS        = "/api/v1/event/{event_id}/odds/{odds_provider_id}"

    # ID del proveedor de cuotas (Bet365 suele ser 1 en Sofascore/SportAPI)
    # TODO: verifica en tu panel cuál está disponible en tu tier
    ODDS_PROVIDER_ID = 1

    # Códigos de status de Sofascore que indican partido terminado
    FINISHED_STATUSES = {"finished", "after extra time", "after penalties", "awarded"}

    def __init__(self, api_key: str):
        self.api_key    = api_key
        self.headers    = {
            "X-RapidAPI-Key":  api_key,
            "X-RapidAPI-Host": "sportapi7.p.rapidapi.com",
        }
        self._cache:     dict  = {}
        self._last_call: float = 0.0

    # ── HTTP genérico ────────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        """
        GET genérico con cache por sesión, throttle 1 req/seg y retry x2.
        path: ruta relativa ya construida (sin host).
        """
        cache_key = path + str(sorted((params or {}).items()))
        if cache_key in self._cache:
            return self._cache[cache_key]

        elapsed = time.time() - self._last_call
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        url = f"{self.BASE_URL}{path}"
        for attempt in range(1, 3):
            try:
                resp = requests.get(
                    url,
                    headers = self.headers,
                    params  = params or {},
                    timeout = 15,
                )
                self._last_call = time.time()

                if resp.status_code == 200:
                    data = resp.json()
                    self._cache[cache_key] = data
                    return data
                elif resp.status_code == 429:
                    print(f"  [SportAPI] Rate-limit, esperando 10s...")
                    time.sleep(10)
                elif resp.status_code == 404:
                    print(f"  [SportAPI] 404 en {path} — endpoint no disponible en tu plan")
                    return None
                else:
                    print(f"  [SportAPI] HTTP {resp.status_code} en {path} (intento {attempt})")
                    time.sleep(2)
            except requests.exceptions.RequestException as exc:
                print(f"  [SportAPI] Excepcion en {path}: {exc} (intento {attempt})")
                time.sleep(3)
        return None

    # ── Endpoints públicos ───────────────────────────────────────────────────

    def get_scheduled_events(self, target_date: str) -> list[dict]:
        """
        Retorna todos los eventos de fútbol programados para target_date.
        target_date: "YYYY-MM-DD"

        Ruta: GET /api/v1/sport/football/scheduled-events/{date}
        Respuesta: {"events": [...], "hasNextPage": bool}

        TODO: Si tu plan devuelve la lista en una clave distinta a "events",
              actualiza el acceso data.get("events", []) abajo.
        """
        path = self.ENDPOINT_SCHEDULED_EVENTS.format(date=target_date)
        data = self._get(path)

        if not data:
            return []

        # Sofascore usa "events" como clave raíz de la lista de partidos
        # TODO: si SportAPI usa otra clave (p.ej. "data", "results"), cámbiala
        return data.get("events", [])

    def get_team_last_events(self, team_id: int, page: int = 0) -> list[dict]:
        """
        Retorna los últimos partidos de un equipo (paginado).
        page=0 devuelve la página más reciente (10-20 eventos según el plan).

        Ruta: GET /api/v1/team/{team_id}/events/last/{page}
        Respuesta: {"events": [...], "hasNextPage": bool}

        TODO: Si necesitas más de una página para obtener 7 partidos,
              llama con page=1, page=2... hasta tener suficientes resultados.
              Sofascore pagina en grupos de ~10 eventos.
        """
        path = self.ENDPOINT_TEAM_LAST_EVENTS.format(
            team_id = team_id,
            page    = page,
        )
        data = self._get(path)
        if not data:
            return []
        return data.get("events", [])

    def get_event_h2h(self, event_id: int) -> list[dict]:
        """
        Retorna los enfrentamientos directos históricos para un evento dado.

        Ruta: GET /api/v1/event/{event_id}/h2h/events
        Respuesta posible A: {"events": [...]}
        Respuesta posible B: {"homeTeamEvents": [...], "awayTeamEvents": [...]}

        TODO: Sofascore a veces devuelve el H2H como listas separadas por
              equipo. Si ves datos vacíos, prueba acceder a:
              data.get("homeTeamEvents", []) + data.get("awayTeamEvents", [])
              y ajusta parse_sportapi_h2h() acordemente.
        """
        path = self.ENDPOINT_EVENT_H2H.format(event_id=event_id)
        data = self._get(path)
        if not data:
            return []

        # Intenta primero la clave "events", luego concatena las dos listas
        if "events" in data:
            return data["events"]
        h2h_home = data.get("homeTeamEvents", [])
        h2h_away = data.get("awayTeamEvents", [])
        return h2h_home + h2h_away

    def get_event_odds(self, event_id: int) -> Optional[dict]:
        """
        Retorna las cuotas pre-partido para un evento.

        Ruta: GET /api/v1/event/{event_id}/odds/{odds_provider_id}
        Respuesta esperada:
        {
          "markets": [
            {
              "marketName": "Full time",
              "choices": [
                {"name": "1", "fractionalValue": "...", "decimalValue": 1.85},
                {"name": "X", "fractionalValue": "...", "decimalValue": 3.40},
                {"name": "2", "fractionalValue": "...", "decimalValue": 4.20}
              ]
            },
            ...
          ]
        }

        TODO: Si SportAPI usa otra estructura para los mercados/cuotas, ajusta
              parse_sportapi_odds() para mapear los campos correctos.
              Nombres alternativos del mercado 1X2: "Match Result",
              "1X2", "FT Result", "Full Time Result".
        TODO: Si el endpoint de odds no está en tu plan, este método
              retornará None y el bot seguirá sin cuotas (modo informativo).
        """
        path = self.ENDPOINT_EVENT_ODDS.format(
            event_id         = event_id,
            odds_provider_id = self.ODDS_PROVIDER_ID,
        )
        data = self._get(path)
        return data  # el parseo ocurre en parse_sportapi_odds()


# =============================================================================
# PARSEADORES  SportAPI JSON → formato interno del motor Poisson
# =============================================================================

def _sofascore_is_finished(event: dict) -> bool:
    """
    Determina si un evento de Sofascore/SportAPI ya finalizó.
    Sofascore usa event["status"]["type"] con valores en inglés.
    """
    status_type = (
        event.get("status", {})
             .get("type", "")
             .lower()
    )
    return status_type in SportAPIClient.FINISHED_STATUSES


def _extract_score(event: dict, perspective: str) -> tuple[Optional[int], Optional[int]]:
    """
    Extrae (goles_anotados, goles_recibidos) desde un evento de Sofascore,
    desde la perspectiva del equipo indicado ("home" o "away").

    Sofascore almacena los goles en:
      event["homeScore"]["current"]  (o "normalTime", o "display")
      event["awayScore"]["current"]

    TODO: Si el marcador no aparece en "current", prueba "normalTime" o
          "display" como clave alternativa.
    """
    home_score_obj = event.get("homeScore", {})
    away_score_obj = event.get("awayScore", {})

    # Intentar "current" primero, luego "normalTime", luego "display"
    def _pick(obj: dict) -> Optional[int]:
        for key in ("current", "normalTime", "display", "period1"):
            if obj.get(key) is not None:
                try:
                    return int(obj[key])
                except (ValueError, TypeError):
                    continue
        return None

    hg = _pick(home_score_obj)
    ag = _pick(away_score_obj)

    if hg is None or ag is None:
        return None, None

    if perspective == "home":
        return hg, ag          # scored, conceded
    else:
        return ag, hg          # scored (fue visitante), conceded


def parse_sportapi_team_events(
    raw_events: list[dict],
    team_id: int,
) -> list[dict]:
    """
    Convierte la lista de eventos de SportAPI al formato de TeamStats:
      [{"scored": int, "conceded": int, "home": bool}, ...]

    Solo incluye partidos ya finalizados.
    Los eventos de Sofascore tienen:
      event["homeTeam"]["id"]  / event["awayTeam"]["id"]

    TODO: Algunos endpoints de SportAPI usan "teams": {"home": {...}}
          en lugar de "homeTeam"/"awayTeam" directamente.
          Si los ids no se extraen correctamente, prueba:
          event.get("teams", {}).get("home", {}).get("id")
    """
    result = []

    for event in raw_events:
        if not _sofascore_is_finished(event):
            continue

        # Extraer IDs de equipo — estructura principal de Sofascore
        home_id = (
            event.get("homeTeam", {}).get("id")
            or event.get("teams", {}).get("home", {}).get("id")
        )
        away_id = (
            event.get("awayTeam", {}).get("id")
            or event.get("teams", {}).get("away", {}).get("id")
        )

        if team_id == home_id:
            scored, conceded = _extract_score(event, "home")
            is_home = True
        elif team_id == away_id:
            scored, conceded = _extract_score(event, "away")
            is_home = False
        else:
            continue  # evento no corresponde a este equipo

        if scored is None or conceded is None:
            continue

        result.append({
            "scored":   scored,
            "conceded": conceded,
            "home":     is_home,
        })

    return result


def parse_sportapi_h2h(
    raw_events: list[dict],
    home_team_id: int,
) -> list[dict]:
    """
    Convierte el H2H de SportAPI al formato de MatchAnalyzer:
      [{"home_goals": int, "away_goals": int}, ...]

    La perspectiva se normaliza siempre con home_team_id como "local".

    TODO: Si el endpoint H2H devuelve los eventos ya filtrados por equipo
          (p.ej. "homeTeamEvents" contiene solo los del equipo local),
          en ese caso todos los eventos de esa lista cuentan como "home".
          Ajusta esta función si ves resultados invertidos.
    """
    result = []

    for event in raw_events:
        if not _sofascore_is_finished(event):
            continue

        api_home_id = (
            event.get("homeTeam", {}).get("id")
            or event.get("teams", {}).get("home", {}).get("id")
        )

        hg_raw, ag_raw = _extract_score(event, "home")
        if hg_raw is None or ag_raw is None:
            continue

        # Normalizar perspectiva
        if api_home_id == home_team_id:
            result.append({"home_goals": hg_raw, "away_goals": ag_raw})
        else:
            result.append({"home_goals": ag_raw, "away_goals": hg_raw})

    return result


def parse_sportapi_odds(raw_odds_data: Optional[dict]) -> Optional[dict]:
    """
    Extrae cuotas 1X2 (decimal) desde la respuesta de SportAPI/Sofascore.

    Estructura esperada de SportAPI:
    {
      "markets": [
        {
          "marketName": "Full time",
          "choices": [
            {"name": "1", "decimalValue": 1.85},
            {"name": "X", "decimalValue": 3.40},
            {"name": "2", "decimalValue": 4.20}
          ]
        }
      ]
    }

    Nombres alternativos conocidos de Sofascore para el mercado 1X2:
      "Full time", "Match Result", "1X2", "FT 1X2"

    TODO: Si decimalValue aparece como string en lugar de float,
          castea con float(choice["decimalValue"]).
    TODO: Si la estructura es distinta en tu plan, imprime raw_odds_data
          en un dry_run y ajusta las claves abajo.
    """
    if not raw_odds_data:
        return None

    markets = raw_odds_data.get("markets", [])

    # Nombres de mercado 1X2 conocidos (insensible a mayúsculas)
    TARGET_MARKET_NAMES = {"full time", "match result", "1x2", "ft 1x2", "ft result"}

    for market in markets:
        market_name = market.get("marketName", "").lower()
        if market_name not in TARGET_MARKET_NAMES:
            continue

        choices = market.get("choices", [])
        odds_map = {}
        for choice in choices:
            name = choice.get("name", "").strip()
            # decimalValue puede venir como float o como string
            raw_val = choice.get("decimalValue") or choice.get("odd") or choice.get("value")
            if raw_val is None:
                continue
            try:
                odds_map[name] = float(raw_val)
            except (ValueError, TypeError):
                continue

        # Sofascore usa "1", "X", "2" como nombres de outcome
        home_odd = odds_map.get("1") or odds_map.get("Home")
        draw_odd = odds_map.get("X") or odds_map.get("Draw")
        away_odd = odds_map.get("2") or odds_map.get("Away")

        if home_odd and draw_odd and away_odd and all(v > 1.0 for v in [home_odd, draw_odd, away_odd]):
            return {"home": home_odd, "draw": draw_odd, "away": away_odd}

    return None


# =============================================================================
# FETCHER PRINCIPAL  (SportAPIFetcher)
# =============================================================================

class SportAPIFetcher:
    """
    Orquesta las llamadas a SportAPI para construir el dict de ligas/partidos
    compatible con run_analysis().

    Flujo por liga:
      1. get_scheduled_events(date)   → lista de todos los eventos del día
      2. Filtrar por unique_tournament_id de la liga
      3. Por cada partido:
         a. get_team_last_events(home_id)  → forma reciente local
         b. get_team_last_events(away_id)  → forma reciente visitante
         c. get_event_h2h(event_id)        → historial directo
         d. get_event_odds(event_id)       → cuotas pre-partido
      4. Parsear y ensamblar → TeamStats + MatchAnalyzer
    """

    def __init__(self, client: SportAPIClient):
        self.client = client

    def _get_unique_tournament_id(self, event: dict) -> Optional[int]:
        """
        Extrae el ID del torneo único desde un evento de Sofascore.
        Sofascore almacena esto en:
          event["tournament"]["uniqueTournament"]["id"]
        o en:
          event["uniqueTournament"]["id"]

        TODO: Si el campo no existe, imprime event.keys() en dry_run
              para localizar la ruta correcta.
        """
        uid = (
            event.get("tournament", {})
                 .get("uniqueTournament", {})
                 .get("id")
            or event.get("uniqueTournament", {}).get("id")
        )
        return uid

    def _is_valid_event(self, event: dict) -> bool:
        """
        Descarta eventos cancelados, aplazados o sin datos suficientes.
        Sofascore status.type para eventos no jugados: "notstarted", "inprogress"
        Sofascore status.type a descartar: "cancelled", "postponed", "interrupted"
        """
        status_type = event.get("status", {}).get("type", "").lower()
        SKIP = {"cancelled", "postponed", "interrupted", "abandoned", "delayed"}
        return status_type not in SKIP

    def fetch_day(self, target_date: str) -> dict:
        """
        Punto de entrada principal. Retorna el dict de ligas/grupos/partidos
        listo para pasar a run_analysis().
        target_date: "YYYY-MM-DD"
        """
        leagues = CONFIG["LEAGUES"]
        result  = {}

        print(f"\n[SportAPI] Descargando eventos del {target_date}...")
        all_events = self.client.get_scheduled_events(target_date)

        if not all_events:
            print("  [SportAPI] Sin eventos retornados para esa fecha")
            return result

        print(f"  [SportAPI] {len(all_events)} eventos totales de fútbol recibidos")

        # Agrupar eventos por unique_tournament_id
        events_by_league: dict[int, list] = {}
        for event in all_events:
            uid = self._get_unique_tournament_id(event)
            if uid is not None:
                events_by_league.setdefault(uid, []).append(event)

        # Procesar solo las ligas configuradas
        for league_id, (league_name, channel_key, avg_goals) in leagues.items():
            league_events = events_by_league.get(league_id, [])

            if not league_events:
                print(f"  Sin partidos: {league_name} (ID {league_id})")
                continue

            print(f"\n[Fetcher] {league_name}: {len(league_events)} partido(s)")
            matches_by_group: dict[str, list] = {}

            for event in league_events:
                if not self._is_valid_event(event):
                    continue

                event_id   = event.get("id")
                home_team  = event.get("homeTeam", {})
                away_team  = event.get("awayTeam", {})

                # TODO: si SportAPI usa "teams.home" en lugar de "homeTeam",
                #       reemplaza estas líneas:
                # home_team = event.get("teams", {}).get("home", {})
                # away_team = event.get("teams", {}).get("away", {})

                home_id   = home_team.get("id")
                away_id   = away_team.get("id")
                home_name = home_team.get("name", f"Equipo {home_id}")
                away_name = away_team.get("name", f"Equipo {away_id}")

                group_name = (
                    event.get("roundInfo", {}).get("name")
                    or event.get("tournament", {}).get("name")
                    or league_name
                )

                if not home_id or not away_id or not event_id:
                    print(f"  Saltando evento sin IDs completos: {event.get('id')}")
                    continue

                print(f"  Procesando: {home_name} vs {away_name} (ID:{event_id})")

                # ── Forma reciente local ─────────────────────────────────────
                raw_home = self.client.get_team_last_events(home_id, page=0)
                home_matches = parse_sportapi_team_events(raw_home, home_id)
                # Si no hay suficientes en la pág 0, pide pág 1
                if len(home_matches) < CONFIG["RECENT_MATCHES_WINDOW"]:
                    raw_home_p1 = self.client.get_team_last_events(home_id, page=1)
                    home_matches += parse_sportapi_team_events(raw_home_p1, home_id)
                home_matches = home_matches[-CONFIG["RECENT_MATCHES_WINDOW"]:]

                # ── Forma reciente visitante ─────────────────────────────────
                raw_away = self.client.get_team_last_events(away_id, page=0)
                away_matches = parse_sportapi_team_events(raw_away, away_id)
                if len(away_matches) < CONFIG["RECENT_MATCHES_WINDOW"]:
                    raw_away_p1 = self.client.get_team_last_events(away_id, page=1)
                    away_matches += parse_sportapi_team_events(raw_away_p1, away_id)
                away_matches = away_matches[-CONFIG["RECENT_MATCHES_WINDOW"]:]

                # ── H2H ─────────────────────────────────────────────────────
                raw_h2h  = self.client.get_event_h2h(event_id)
                h2h_data = parse_sportapi_h2h(raw_h2h, home_id)

                # ── Cuotas ──────────────────────────────────────────────────
                raw_odds  = self.client.get_event_odds(event_id)
                odds_data = parse_sportapi_odds(raw_odds)

                if odds_data:
                    print(f"    Cuotas: 1={odds_data['home']}  X={odds_data['draw']}  2={odds_data['away']}")
                else:
                    print(f"    Cuotas: no disponibles")

                match_entry = {
                    "home": {
                        "name":           home_name,
                        "recent_matches": home_matches,
                        "team_id":        home_id,
                    },
                    "away": {
                        "name":           away_name,
                        "recent_matches": away_matches,
                        "team_id":        away_id,
                    },
                    "odds":       odds_data,
                    "h2h":        h2h_data,
                    "event_id":   event_id,
                }

                matches_by_group.setdefault(group_name, []).append(match_entry)

            if matches_by_group:
                result[league_name.lower().replace(" ", "_")] = {
                    "league_avg_goals": avg_goals,
                    "channel_key":      channel_key,
                    "groups":           matches_by_group,
                }

        return result

    def fetch_day_with_fallback(self, target_date: str) -> dict:
        """
        Igual que fetch_day() pero con tres capas de protección:
          1. Key vacía     → FALLBACK_DATA inmediato
          2. API error     → FALLBACK_DATA
          3. Sin datos     → FALLBACK_DATA
        """
        key = CONFIG.get("SPORTAPI_KEY", "").strip()
        if not key:
            print("[Fetcher] SPORTAPI_KEY vacía → usando FALLBACK_DATA (demo)")
            return FALLBACK_DATA

        try:
            data = self.fetch_day(target_date)
        except Exception as exc:
            print(f"[Fetcher] Error inesperado: {exc} → usando FALLBACK_DATA")
            return FALLBACK_DATA

        if not data:
            print("[Fetcher] Sin datos reales → usando FALLBACK_DATA (demo)")
            return FALLBACK_DATA

        return data


# =============================================================================
# MOTOR DE POISSON  —  NO MODIFICAR
# =============================================================================

class PoissonEngine:
    @staticmethod
    def goal_expectancy(
        home_attack: float, home_defense: float,
        away_attack: float, away_defense: float,
        home_advantage: float = 1.10,
    ) -> tuple[float, float]:
        return (
            round(home_attack * away_defense * home_advantage, 4),
            round(away_attack * home_defense,                  4),
        )

    @staticmethod
    def result_matrix(lh: float, la: float, max_goals: int = 8) -> np.ndarray:
        hp = [poisson.pmf(i, lh) for i in range(max_goals + 1)]
        ap = [poisson.pmf(j, la) for j in range(max_goals + 1)]
        return np.outer(hp, ap)

    @staticmethod
    def market_1x2(matrix: np.ndarray) -> dict:
        n  = matrix.shape[0]
        hw = sum(matrix[i][j] for i in range(n) for j in range(n) if i > j)
        d  = sum(matrix[i][j] for i in range(n) for j in range(n) if i == j)
        aw = sum(matrix[i][j] for i in range(n) for j in range(n) if i < j)
        t  = hw + d + aw
        return {"home": round(hw/t, 4), "draw": round(d/t, 4), "away": round(aw/t, 4)}

    @staticmethod
    def over_under(lh: float, la: float, line: float = 2.5, max_goals: int = 15) -> dict:
        totals: dict[int, float] = {}
        for gh in range(max_goals + 1):
            for ga in range(max_goals + 1):
                t = gh + ga
                p = poisson.pmf(gh, lh) * poisson.pmf(ga, la)
                totals[t] = totals.get(t, 0) + p
        ov = sum(p for g, p in totals.items() if g > line)
        un = sum(p for g, p in totals.items() if g <= line)
        t  = ov + un
        return {"over": round(ov/t, 4), "under": round(un/t, 4), "line": line}


# =============================================================================
# TEAM STATS  —  NO MODIFICAR
# =============================================================================

class TeamStats:
    def __init__(self, name: str, recent_matches: list[dict], league_avg_goals: float = 1.35):
        self.name       = name
        self.matches    = recent_matches[-CONFIG["RECENT_MATCHES_WINDOW"]:]
        self.league_avg = league_avg_goals
        self._compute()

    def _compute(self):
        if not self.matches:
            self.attack = self.defense = 1.0
            self.avg_scored = self.avg_conceded = self.league_avg
            return
        sl = [m["scored"]   for m in self.matches]
        cl = [m["conceded"] for m in self.matches]
        self.avg_scored   = sum(sl) / len(sl)
        self.avg_conceded = sum(cl) / len(cl)
        self.attack  = self.avg_scored   / self.league_avg if self.league_avg else 1.0
        self.defense = self.avg_conceded / self.league_avg if self.league_avg else 1.0

    def form_string(self) -> str:
        form = []
        for m in self.matches[-5:]:
            if   m["scored"] > m["conceded"]: form.append("G")
            elif m["scored"] < m["conceded"]: form.append("P")
            else:                             form.append("E")
        return "".join(form) or "N/D"


# =============================================================================
# MATCH ANALYZER  —  NO MODIFICAR
# =============================================================================

class MatchAnalyzer:
    def __init__(
        self,
        home: TeamStats, away: TeamStats,
        market_odds: Optional[dict] = None,
        h2h_matches: Optional[list[dict]] = None,
        league_avg_goals: float = 1.35,
    ):
        self.home       = home
        self.away       = away
        self.odds       = market_odds
        self.h2h        = h2h_matches or []
        self.engine     = PoissonEngine()
        self.league_avg = league_avg_goals

    def _h2h_adjustment(self) -> tuple[float, float]:
        if not self.h2h:
            return 1.0, 1.0
        ah = sum(m["home_goals"] for m in self.h2h) / len(self.h2h)
        aa = sum(m["away_goals"] for m in self.h2h) / len(self.h2h)
        adj_h = ah / self.league_avg if self.league_avg else 1.0
        adj_a = aa / self.league_avg if self.league_avg else 1.0
        return round(0.80 + 0.20 * adj_h, 4), round(0.80 + 0.20 * adj_a, 4)

    def analyze(self) -> dict:
        ah, aa = self._h2h_adjustment()
        lh, la = self.engine.goal_expectancy(
            self.home.attack  * ah, self.home.defense,
            self.away.attack  * aa, self.away.defense,
        )
        matrix    = self.engine.result_matrix(lh, la)
        probs_1x2 = self.engine.market_1x2(matrix)
        probs_ou  = self.engine.over_under(lh, la, line=CONFIG["OU_LINE"])

        market_probs, edge_1x2 = {}, {}
        if self.odds:
            rh = 1 / self.odds.get("home", 99)
            rd = 1 / self.odds.get("draw", 99)
            ra = 1 / self.odds.get("away", 99)
            mg = rh + rd + ra
            if mg > 0:
                market_probs = {
                    "home": round(rh/mg, 4),
                    "draw": round(rd/mg, 4),
                    "away": round(ra/mg, 4),
                }
                edge_1x2 = {
                    "home": round((probs_1x2["home"] - market_probs["home"]) * 100, 2),
                    "draw": round((probs_1x2["draw"] - market_probs["draw"]) * 100, 2),
                    "away": round((probs_1x2["away"] - market_probs["away"]) * 100, 2),
                }

        value_picks = []
        thr = CONFIG["EDGE_THRESHOLD_PP"]
        if edge_1x2:
            labels = {"home": f"1 ({self.home.name})", "draw": "X (Empate)", "away": f"2 ({self.away.name})"}
            for res, edge in edge_1x2.items():
                if edge >= thr:
                    value_picks.append({
                        "market":      "1X2",
                        "pick":        labels[res],
                        "model_prob":  probs_1x2[res],
                        "market_prob": market_probs[res],
                        "edge_pp":     edge,
                        "odds":        self.odds.get(res) if self.odds else None,
                    })

        if probs_ou["over"] >= CONFIG["MIN_CONFIDENCE"]:
            value_picks.append({"market": "Over/Under", "pick": f"Over {CONFIG['OU_LINE']}", "model_prob": probs_ou["over"], "edge_pp": None})
        elif probs_ou["under"] >= CONFIG["MIN_CONFIDENCE"]:
            value_picks.append({"market": "Over/Under", "pick": f"Under {CONFIG['OU_LINE']}", "model_prob": probs_ou["under"], "edge_pp": None})

        return {
            "home_team":    self.home.name,  "away_team":    self.away.name,
            "lambda_home":  lh,              "lambda_away":  la,
            "probs_1x2":    probs_1x2,       "probs_ou":     probs_ou,
            "market_probs": market_probs,    "edge_1x2":     edge_1x2,
            "value_picks":  value_picks,
            "home_form":    self.home.form_string(),
            "away_form":    self.away.form_string(),
        }


# =============================================================================
# REPORTE Y TELEGRAM
# =============================================================================

def clean_text(text: str) -> str:
    for token in ["***","**","*","___","__","`","~~~","~~","###","##","#",">","[","]","(",")"]:
        text = text.replace(token, "")
    text = text.replace("_", " ")
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_match_block(result: dict, idx: int) -> str:
    sep   = "=" * 40
    lines = [
        sep,
        f"  PARTIDO {idx}  |  {result['home_team']} vs {result['away_team']}",
        sep,
        f"Forma reciente:",
        f"  {result['home_team']}: {result['home_form']}",
        f"  {result['away_team']}: {result['away_form']}",
        f"Goles esperados:  {result['home_team']} {result['lambda_home']:.2f}  |  {result['away_team']} {result['lambda_away']:.2f}",
    ]
    p = result["probs_1x2"]
    lines.append(f"Prob. modelo 1X2: 1={p['home']*100:.1f}%  X={p['draw']*100:.1f}%  2={p['away']*100:.1f}%")
    if result.get("market_probs"):
        mp = result["market_probs"]
        e  = result["edge_1x2"]
        lines.append(f"Prob. mercado:    1={mp['home']*100:.1f}%  X={mp['draw']*100:.1f}%  2={mp['away']*100:.1f}%")
        lines.append(f"Edge (pp):        1={e['home']:+.1f}  X={e['draw']:+.1f}  2={e['away']:+.1f}")
    else:
        lines.append("Cuotas: no disponibles")
    ou = result["probs_ou"]
    lines.append(f"Over/Under {ou['line']}: Over={ou['over']*100:.1f}%  Under={ou['under']*100:.1f}%")
    if result["value_picks"]:
        lines += ["", "  PICKS DE VALOR"]
        for pk in result["value_picks"]:
            e_str = f"  Edge: +{pk['edge_pp']:.1f}pp" if pk.get("edge_pp") else ""
            o_str = f"  Cuota: {pk['odds']:.2f}"      if pk.get("odds")    else ""
            lines.append(f"  {pk['market']} - {pk['pick']}  ({pk['model_prob']*100:.1f}%){e_str}{o_str}")
    elif CONFIG["SHOW_INFORMATIVE"]:
        lines.append("  (Sin edge suficiente - informativo)")
    lines.append("")
    return "\n".join(lines)


def build_report(league_name: str, matches_results: list[dict]) -> str:
    today  = date.today().strftime("%d/%m/%Y")
    header = "\n".join(["", "="*40, "  SOCCER BOT - ANALISIS ESTADISTICO",
                        f"  Liga: {league_name.upper()}", f"  Fecha: {today}", "="*40, ""])
    body   = "\n".join(build_match_block(r, i) for i, r in enumerate(matches_results, 1))
    footer = "\n".join(["="*40, f"  Modelo: Poisson + H2H  |  Edge min: {CONFIG['EDGE_THRESHOLD_PP']}pp",
                        "  Bot generado automaticamente", "="*40, ""])
    return clean_text(header + body + footer)


def send_telegram(channel_key: str, text: str) -> dict:
    token   = CONFIG["TELEGRAM_BOT_TOKEN"]
    chat_id = CONFIG["CHANNELS"].get(channel_key) or CONFIG["CHANNELS"]["general"]
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks  = [text[i:i+4096] for i in range(0, len(text), 4096)]
    results = []
    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk}  # sin parse_mode intencional
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            results.append({"status": "ok", "chars": len(chunk)})
            print(f"  [Telegram] OK → '{channel_key}' ({len(chunk)} chars)")
        except requests.exceptions.RequestException as exc:
            results.append({"status": "error", "error": str(exc)})
            print(f"  [Telegram] ERROR → '{channel_key}': {exc}")
    return {"channel": channel_key, "chunks": len(chunks), "detail": results}


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def run_analysis(data: dict, send: bool = False) -> list[dict]:
    all_results = []
    for league_key, league_data in data.items():
        league_avg     = league_data.get("league_avg_goals", 1.35)
        channel_key    = league_data.get("channel_key", "general")
        groups         = league_data.get("groups", {})
        league_results = []

        for group_name, matches in groups.items():
            print(f"\n[Analizando] {league_key.upper()} / {group_name}")
            for match in matches:
                home_stats = TeamStats(match["home"]["name"], match["home"]["recent_matches"], league_avg)
                away_stats = TeamStats(match["away"]["name"], match["away"]["recent_matches"], league_avg)
                analyzer   = MatchAnalyzer(home_stats, away_stats, match.get("odds"), match.get("h2h"), league_avg)
                result     = analyzer.analyze()
                result["group"] = group_name
                league_results.append(result)

                picks_str = " | ".join(f"{pk['pick']} ({pk['model_prob']*100:.0f}%)" for pk in result["value_picks"]) or "Sin edge"
                print(f"  {result['home_team']} vs {result['away_team']}  L: {result['lambda_home']:.2f}/{result['lambda_away']:.2f}  {picks_str}")

        report_text = build_report(league_key.replace("_", " ").title(), league_results)
        print(f"\n{'='*52}\nREPORTE  {league_key.upper()}\n{'='*52}")
        print(report_text)
        if send:
            send_telegram(channel_key, report_text)

        all_results.append({"league": league_key, "channel": channel_key, "count": len(league_results), "results": league_results})

    return all_results


# =============================================================================
# FALLBACK DATA — Usado cuando SPORTAPI_KEY está vacía o la API falla
# =============================================================================

FALLBACK_DATA = {
    "premier_league_demo": {
        "league_avg_goals": 1.42,
        "channel_key":      "premier_league",
        "groups": {
            "Premier League - DEMO": [
                {
                    "home": {"name": "Arsenal (DEMO)", "recent_matches": [
                        {"scored": 2, "conceded": 0, "home": True},
                        {"scored": 3, "conceded": 1, "home": False},
                        {"scored": 1, "conceded": 0, "home": True},
                        {"scored": 2, "conceded": 2, "home": False},
                        {"scored": 3, "conceded": 0, "home": True},
                        {"scored": 0, "conceded": 1, "home": False},
                        {"scored": 2, "conceded": 1, "home": True},
                    ]},
                    "away": {"name": "Chelsea (DEMO)", "recent_matches": [
                        {"scored": 1, "conceded": 1, "home": True},
                        {"scored": 2, "conceded": 2, "home": False},
                        {"scored": 0, "conceded": 1, "home": True},
                        {"scored": 1, "conceded": 0, "home": False},
                        {"scored": 2, "conceded": 1, "home": True},
                        {"scored": 1, "conceded": 2, "home": False},
                        {"scored": 0, "conceded": 0, "home": True},
                    ]},
                    "odds": {"home": 2.00, "draw": 3.60, "away": 3.75},
                    "h2h":  [{"home_goals": 2, "away_goals": 2},
                             {"home_goals": 1, "away_goals": 0},
                             {"home_goals": 3, "away_goals": 1}],
                },
            ],
        },
    },
}


# =============================================================================
# PUNTOS DE ENTRADA
# =============================================================================

def dry_run(target_date: Optional[str] = None):
    """
    Descarga datos reales (o fallback) y analiza SIN enviar a Telegram.
    Ideal para depurar la integración con SportAPI en Colab.
    """
    if target_date is None:
        target_date = date.today().strftime("%Y-%m-%d")
    print("=" * 52)
    print("  MODO DRY RUN — Sin envio a Telegram")
    print(f"  Fecha analizada: {target_date}")
    print("=" * 52)

    client  = SportAPIClient(api_key=CONFIG.get("SPORTAPI_KEY", ""))
    fetcher = SportAPIFetcher(client)
    data    = fetcher.fetch_day_with_fallback(target_date)

    results     = run_analysis(data, send=False)
    total_picks = 0
    for r in results:
        picks = sum(len(m["value_picks"]) for m in r["results"])
        total_picks += picks
        print(f"  {r['league']}: {r['count']} partidos  |  {picks} picks de valor")
    print(f"  Total picks de valor: {total_picks}")
    return results


def production_run(target_date: Optional[str] = None):
    """
    Descarga datos reales y envía los reportes a Telegram.
    Requiere SPORTAPI_KEY y TELEGRAM_BOT_TOKEN configurados en CONFIG.
    """
    if target_date is None:
        target_date = date.today().strftime("%Y-%m-%d")
    print("=" * 52)
    print("  MODO PRODUCCION — Enviando a Telegram")
    print(f"  Fecha analizada: {target_date}")
    print("=" * 52)

    client  = SportAPIClient(api_key=CONFIG["SPORTAPI_KEY"])
    fetcher = SportAPIFetcher(client)
    data    = fetcher.fetch_day_with_fallback(target_date)
    return run_analysis(data, send=True)


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    # Con SPORTAPI_KEY vacía  → FALLBACK_DATA (demo seguro)
    # Con SPORTAPI_KEY llena  → datos reales de SportAPI
    # Para producción, cambia dry_run() por production_run()
    dry_run()
