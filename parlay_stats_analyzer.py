# ==============================================================================
# parlay_stats_analyzer.py  v3.0
# ==============================================================================
# Analizador cuantitativo de estadísticas deportivas para Parlays.
#
# MODULO 1 — SpreadAnalyzer refactorizado para SportAPI (sportapi7.p.rapidapi.com)
#   Wrapper oficial de Sofascore en RapidAPI. Barrido multi-deporte en una sola
#   ejecución: Fútbol (ligas configuradas), NBA y MLB.
#   Endpoint: /api/{sport}/scheduled-events/{day}/{month}/{year}
#   Odds:     /api/{sport}/match/{eventId}/odds
#
# MODULO 2 — PlayerPropsAnalyzer (NBA / MLB) intacto.
# MODULO 3 — TelegramNotifier nativo en el pipeline.
# MODULO 4 — ReportBuilder: Markdown limpio listo para canal Telegram premium.
#
# INSTALACION:
#   pip install requests colorama
#
# EJECUCION:
#   python parlay_stats_analyzer.py
#   En Colab: exec(open("parlay_stats_analyzer.py").read())
# ==============================================================================

import hashlib
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("[WARN] Instala requests: pip install requests")

try:
    from colorama import Fore, Style, init as _cinit
    _cinit(autoreset=True)
    C = {
        "G": Fore.GREEN,
        "Y": Fore.YELLOW,
        "R": Fore.RED,
        "C": Fore.CYAN,
        "M": Fore.MAGENTA,
        "W": Fore.WHITE,
        "B": Style.BRIGHT,
        "0": Style.RESET_ALL,
    }
except ImportError:
    C = {k: "" for k in ["G", "Y", "R", "C", "M", "W", "B", "0"]}


# ==============================================================================
# SECCION 0 — CONFIG CENTRAL  <— EDITA AQUI Y SOLO AQUI
# ==============================================================================

CONFIG = {

    # ── Fecha de análisis ─────────────────────────────────────────────────
    # "auto" = fecha del sistema. Cambia a "2026-06-10" para fecha fija.
    "analysis_date": "auto",

    # ── Directorio de salida ──────────────────────────────────────────────
    # "." = carpeta actual.  En Colab usa "/content/"
    "output_dir": ".",

    # ── SportAPI (sportapi7.p.rapidapi.com) ───────────────────────────────
    # Wrapper multi-deporte de Sofascore en RapidAPI. Plan PRO requerido.
    # Genera tu key en: https://rapidapi.com/fluis.lacasse/api/sportapi7
    "SPORTAPI_KEY": "ffc05d5442msh0d6cdc5e550926dp12de2cjsnb5e113e904ed",

    # ── BallDontLie (NBA player stats, gratuita con key) ──────────────────
    # https://app.balldontlie.io  — deja "" para modo demo en Player Props
    "balldontlie_key": "",

    # ── Telegram ──────────────────────────────────────────────────────────
    # Si ambos están configurados el reporte se envía automáticamente.
    # Deja en "" para solo generar archivos locales.
    "telegram_bot_token": "",
    "telegram_chat_id": "",

    # ── Parámetros de análisis ────────────────────────────────────────────
    "n_games": 10,
    "spread_value_buffer": 3.0,
    "high_consistency_pct": 0.80,
    "consistency_pct": 0.70,

    # ── Deportes para el barrido de Spreads vía SportAPI ─────────────────
    # Cada entrada define un deporte a barrer.
    # "sport_path": segmento que va en la URL de SportAPI.
    # "label":      nombre legible para el reporte.
    # "league_ids": lista de IDs de torneo a filtrar ([] = todos los eventos).
    #
    # IDs de referencia frecuentes en SportAPI / Sofascore:
    #   Fútbol  — WC 2026: 1239  |  Premier League: 17  |  La Liga: 8
    #             Serie A: 23    |  Bundesliga: 35       |  Ligue 1: 34
    #             MX Liga MX: 352
    #   NBA       tournament_id: 132
    #   MLB       tournament_id: 150
    #
    # Deja "league_ids" vacío para capturar TODOS los eventos del deporte ese día.
    "spread_sports": [
        {
            "sport_path": "football",
            "label": "Fútbol",
            "league_ids": [1239, 17, 8, 23, 35, 34, 352],
        },
        {
            "sport_path": "basketball",
            "label": "NBA",
            "league_ids": [132],
        },
        {
            "sport_path": "baseball",
            "label": "MLB",
            "league_ids": [150],
        },
    ],

    # ── Mercado de handicap a extraer de SportAPI ─────────────────────────
    # SportAPI (vía Sofascore/bet365) etiqueta el handicap con estos nombres.
    # Se busca en orden; el primero que exista en la respuesta se usa.
    "handicap_market_names": [
        "Asian Handicap",
        "Handicap",
        "Point Spread",
        "Run Line",         # MLB
        "Puck Line",        # NHL (por si se agrega)
    ],

    # ── Roster de jugadores (Player Props) ───────────────────────────────
    "roster": [
        {
            "name": "Stephen Curry",
            "sport": "NBA",
            "lines": {
                "points": 27.5,
                "rebounds": 4.5,
                "assists": 5.5,
                "three_pointers_made": 4.5,
            },
        },
        {
            "name": "Nikola Jokic",
            "sport": "NBA",
            "lines": {
                "points": 24.5,
                "rebounds": 11.5,
                "assists": 8.5,
                "three_pointers_made": 0.5,
            },
        },
        {
            "name": "Shai Gilgeous-Alexander",
            "sport": "NBA",
            "lines": {
                "points": 30.5,
                "rebounds": 4.5,
                "assists": 5.5,
                "three_pointers_made": 1.5,
            },
        },
        {
            "name": "Jayson Tatum",
            "sport": "NBA",
            "lines": {
                "points": 26.5,
                "rebounds": 7.5,
                "assists": 4.5,
                "three_pointers_made": 2.5,
            },
        },
        {
            "name": "Anthony Edwards",
            "sport": "NBA",
            "lines": {
                "points": 25.5,
                "rebounds": 5.5,
                "assists": 5.5,
                "three_pointers_made": 3.5,
            },
        },
        {
            "name": "LeBron James",
            "sport": "NBA",
            "lines": {
                "points": 23.5,
                "rebounds": 7.5,
                "assists": 7.5,
                "three_pointers_made": 1.5,
            },
        },
        {
            "name": "Gerrit Cole",
            "sport": "MLB",
            "role": "pitcher",
            "lines": {"strikeouts": 7.5},
        },
        {
            "name": "Spencer Strider",
            "sport": "MLB",
            "role": "pitcher",
            "lines": {"strikeouts": 8.5},
        },
        {
            "name": "Zack Wheeler",
            "sport": "MLB",
            "role": "pitcher",
            "lines": {"strikeouts": 7.5},
        },
        {
            "name": "Freddie Freeman",
            "sport": "MLB",
            "role": "batter",
            "lines": {"hits": 1.5},
        },
        {
            "name": "Mookie Betts",
            "sport": "MLB",
            "role": "batter",
            "lines": {"hits": 1.5},
        },
    ],
}

# ==============================================================================
# FIN CONFIG
# ==============================================================================


# ------------------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------------------

NBA_CATS = ["points", "rebounds", "assists", "three_pointers_made"]
MLB_CATS = ["strikeouts", "hits"]
CAT_ES = {
    "points": "Puntos",
    "rebounds": "Rebotes",
    "assists": "Asistencias",
    "three_pointers_made": "Triples",
    "strikeouts": "Ponches",
    "hits": "Hits",
}

SPORTAPI_HOST = "sportapi7.p.rapidapi.com"
SPORTAPI_BASE = "https://sportapi7.p.rapidapi.com/api"

# Timeout por llamada API en segundos
TIMEOUT = 12


# ------------------------------------------------------------------------------
# Utilidades de consola
# ------------------------------------------------------------------------------

def _log(msg: str, color: str = "W", bold: bool = False) -> None:
    prefix = (C["B"] if bold else "") + C.get(color, "")
    print(f"{prefix}{msg}{C['0']}")


def _section(title: str) -> None:
    width = 66
    _log("\n" + "=" * width, "C", bold=True)
    _log(f"  {title}", "C", bold=True)
    _log("=" * width, "C", bold=True)


def _bar(rate: float, width: int = 10) -> str:
    filled = round(rate * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _resolve_date(cfg_value: str) -> date:
    if cfg_value.lower() == "auto":
        return date.today()
    try:
        return date.fromisoformat(cfg_value)
    except ValueError:
        _log(f"[WARN] Fecha invalida '{cfg_value}'. Usando hoy.", "Y")
        return date.today()


def _sportapi_headers(api_key: str) -> dict:
    return {
        "x-rapidapi-host": SPORTAPI_HOST,
        "x-rapidapi-key": api_key,
    }


# ==============================================================================
# MODULO 1 — SPREAD ANALYZER  (SportAPI multideporte)
# ==============================================================================

class SpreadAnalyzer:
    """
    Consume SportAPI (sportapi7.p.rapidapi.com) para obtener eventos programados
    y sus handicaps (spreads) en Fútbol, NBA y MLB en una sola ejecucion.

    Flujo por deporte:
      1. GET /api/{sport}/scheduled-events/{day}/{month}/{year}
         → lista de eventos del dia, filtrada por league_ids si se configuran.
      2. Por cada evento, GET /api/{sport}/match/{eventId}/odds
         → extrae el mercado de handicap (Asian Handicap / Point Spread / Run Line).
      3. Calcula margen histórico promedio del equipo local (stub determinístico
         ligado al nombre; reemplazable con endpoint de form guide en producción).
      4. Detecta lineas de valor: si el spread del casino supera el margen
         habitual en mas de spread_value_buffer puntos.

    Notas de produccion:
      - La API devuelve odds de bet365 en el plan PRO.
      - El endpoint de odds puede no tener datos para todos los eventos
        (partidos muy pequeños); en ese caso el evento se incluye sin spread.
      - Se respeta un delay entre llamadas para no saturar el rate-limit del plan.
    """

    ODDS_DELAY_SECONDS = 0.35    # pausa entre llamadas de odds para cuidar rate-limit

    def __init__(self, cfg: dict) -> None:
        self.api_key = cfg["SPORTAPI_KEY"]
        self.sports = cfg["spread_sports"]
        self.buffer = cfg["spread_value_buffer"]
        self.n_games = cfg["n_games"]
        self.market_names = cfg["handicap_market_names"]

    # ── Entrada pública ────────────────────────────────────────────────────
    def analyze(self, target_date: date) -> list:
        if not self.api_key:
            _log("  [SpreadAnalyzer] SPORTAPI_KEY vacio -> sin datos de spreads.", "R")
            return []

        if not HAS_REQUESTS:
            _log("  [SpreadAnalyzer] requests no disponible.", "R")
            return []

        all_results = []
        for sport_cfg in self.sports:
            _log(
                f"\n  Barriendo {sport_cfg['label']} "
                f"({sport_cfg['sport_path']})...",
                "C",
            )
            events = self._fetch_scheduled(sport_cfg, target_date)
            _log(f"  {len(events)} eventos encontrados para hoy.", "W")

            for event in events:
                result = self._process_event(event, sport_cfg, target_date)
                if result:
                    all_results.append(result)

        return all_results

    # ── Paso 1: Eventos programados del día ────────────────────────────────
   sport = sport_cfg["sport_path"]
        # Usa el formato YYYY-MM-DD y la ruta v1 oficial de SportAPI
        url = f"https://sportapi7.p.rapidapi.com/api/v1/sport/{sport}/scheduled-events/{target_date.strftime('%Y-%m-%d')}"
        try:
            resp = requests.get(
                url,
                headers=_sportapi_headers(self.api_key),
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            events = data.get("events", [])
            if not events:
                _log(f"  [SportAPI] Sin eventos para {sport} en {target_date}.", "Y")
                return []

            # Filtrar por league_ids si se configuraron
            league_ids = sport_cfg.get("league_ids", [])
            if league_ids:
                filtered = []
                for ev in events:
                    tid = (
                        ev.get("tournament", {})
                        .get("uniqueTournament", {})
                        .get("id")
                    )
                    if tid in league_ids:
                        filtered.append(ev)
                events = filtered
                _log(
                    f"  [SportAPI] Filtro league_ids={league_ids}: "
                    f"{len(events)} eventos.",
                    "W",
                )

            return events

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "?"
            _log(
                f"  [SportAPI] HTTP {status} en scheduled-events/{sport}: {exc}",
                "R",
            )
            return []
        except Exception as exc:
            _log(
                f"  [SportAPI] Error en scheduled-events/{sport}: "
                f"{exc.__class__.__name__}: {exc}",
                "R",
            )
            return []

    # ── Paso 2: Odds del evento ────────────────────────────────────────────
    def _fetch_odds(self, event_id: int, sport: str) -> dict:
        """
        Retorna dict con home_spread, away_spread y bookmaker_name.
        Retorna {} si no hay datos de handicap disponibles.
        """
        import time
        time.sleep(self.ODDS_DELAY_SECONDS)

        url = f"{SPORTAPI_BASE}/{sport}/match/{event_id}/odds"
        try:
            resp = requests.get(
                url,
                headers=_sportapi_headers(self.api_key),
                timeout=TIMEOUT,
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            data = resp.json()
            return self._parse_handicap(data)

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "?"
            if status not in (404, 422):
                _log(f"    [Odds] HTTP {status} para eventId={event_id}", "Y")
            return {}
        except Exception as exc:
            _log(
                f"    [Odds] Error para eventId={event_id}: "
                f"{exc.__class__.__name__}: {exc}",
                "Y",
            )
            return {}

    def _parse_handicap(self, data: dict) -> dict:
        """
        Estructura de SportAPI (Sofascore/bet365):
        {
          "markets": [
            {
              "marketName": "Asian Handicap",
              "choices": [
                {"name": "1",  "handicapValue": -1.5, "fractionalValue": "..."},
                {"name": "2",  "handicapValue": 1.5,  "fractionalValue": "..."}
              ]
            },
            ...
          ]
        }
        name "1" = home, "2" = away, "X" = draw (no aplica para NBA/MLB)
        """
        markets = data.get("markets", [])
        if not markets:
            # Algunos endpoints devuelven estructura plana diferente
            markets = data.get("odds", {}).get("markets", [])

        for market in markets:
            mname = market.get("marketName", "")
            if not any(h.lower() in mname.lower() for h in self.market_names):
                continue

            choices = market.get("choices", [])
            home_hcap = None
            away_hcap = None
            bookmaker = market.get("sourceId", "bet365")

            for choice in choices:
                name = choice.get("name", "")
                # handicapValue puede venir como float o como string
                raw_hcap = choice.get("handicapValue", choice.get("handicap"))
                try:
                    hcap = float(raw_hcap) if raw_hcap is not None else None
                except (TypeError, ValueError):
                    hcap = None

                if name == "1":
                    home_hcap = hcap
                elif name == "2":
                    away_hcap = hcap

            if home_hcap is not None and away_hcap is not None:
                return {
                    "home_spread": home_hcap,
                    "away_spread": away_hcap,
                    "bookmaker": str(bookmaker),
                    "market": mname,
                }

        return {}

    # ── Paso 3+4: Procesar evento completo ────────────────────────────────
    def _process_event(
        self, event: dict, sport_cfg: dict, target_date: date
    ) -> dict:
        event_id = event.get("id")
        home_team = event.get("homeTeam", {}).get("name", "TBD")
        away_team = event.get("awayTeam", {}).get("name", "TBD")
        tournament = (
            event.get("tournament", {})
            .get("uniqueTournament", {})
            .get("name", sport_cfg["label"])
        )
        start_ts = event.get("startTimestamp", 0)
        start_dt = (
            datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%H:%M UTC")
            if start_ts
            else "--:--"
        )

        _log(f"    {home_team} vs {away_team}  [{tournament}]", "W")

        # Obtener odds
        odds = self._fetch_odds(event_id, sport_cfg["sport_path"])
        home_spread = odds.get("home_spread")
        away_spread = odds.get("away_spread")
        bookmaker = odds.get("bookmaker", "N/A")
        market = odds.get("market", "N/A")

        if home_spread is None:
            _log(f"      Sin datos de handicap para este evento.", "Y")

        # Margen histórico del equipo local
        h_avg, h_hist = self._estimate_margin(home_team, home=True)
        a_avg, a_hist = self._estimate_margin(away_team, home=False)

        # Evaluación de valor
        h_val = self._is_value(h_avg, home_spread) if home_spread is not None else False
        a_val = self._is_value(a_avg, away_spread) if away_spread is not None else False
        value_team = self._value_label(home_team, away_team, h_val, a_val)

        return {
            "sport": sport_cfg["label"],
            "sport_path": sport_cfg["sport_path"],
            "tournament": tournament,
            "event_id": event_id,
            "home_team": home_team,
            "away_team": away_team,
            "start_time": start_dt,
            "home_spread": home_spread,
            "away_spread": away_spread,
            "bookmaker": bookmaker,
            "market": market,
            "home_avg_margin": h_avg,
            "away_avg_margin": a_avg,
            "home_history": h_hist,
            "away_history": a_hist,
            "home_spread_value": h_val,
            "away_spread_value": a_val,
            "value_team": value_team,
        }

    # ── Helpers ────────────────────────────────────────────────────────────
    def _estimate_margin(self, team: str, home: bool) -> tuple:
        """
        Margen promedio de los últimos N juegos (stub determinístico).
        En producción: reemplazar con llamada a endpoint de form/statistics
        de SportAPI: /api/{sport}/team/{teamId}/events/last/{page}
        """
        seed = int(hashlib.md5(f"{team}{home}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        base = 3.5 if home else -2.0
        hist = [round(rng.gauss(base, 8), 1) for _ in range(self.n_games)]
        avg = round(sum(hist) / len(hist), 2)
        return avg, hist

    def _is_value(self, avg_margin: float, casino_spread: float) -> bool:
        """
        Hay valor cuando el equipo recibe puntos (spread > 0) y pierde
        habitualmente por menos de (spread - buffer) puntos.
        """
        return casino_spread > 0 and avg_margin > -(casino_spread - self.buffer)

    def _value_label(
        self, home: str, away: str, hv: bool, av: bool
    ) -> str:
        parts = []
        if hv:
            parts.append(home)
        if av:
            parts.append(away)
        return " & ".join(parts) if parts else "-"


# ==============================================================================
# MODULO 2 — PLAYER PROPS + CONSISTENCIA
# ==============================================================================

class PlayerPropsAnalyzer:
    """
    Calcula promedios y Over-rates para jugadores NBA/MLB en sus últimos N juegos.
    NBA: BallDontLie API (gratis con key).
    MLB: stub determinístico listo para conectar SportRadar o statsapi.mlb.com.
    Nunca lanza excepción; siempre retorna datos reales o demo.
    """

    _NBA_BASES = {
        "Stephen Curry": (29, 5.0, 6.0, 4.5),
        "LeBron James": (26, 7.0, 7.0, 1.5),
        "Nikola Jokic": (25, 12.0, 9.0, 0.5),
        "Shai Gilgeous-Alexander": (31, 5.0, 6.0, 1.5),
        "Anthony Edwards": (27, 5.0, 5.0, 3.5),
        "Jayson Tatum": (27, 8.0, 5.0, 2.5),
        "Luka Doncic": (32, 9.0, 8.0, 3.0),
        "Giannis Antetokounmpo": (30, 11.0, 6.0, 0.5),
    }

    _MLB_K = {
        "Gerrit Cole": 8.5,
        "Spencer Strider": 9.5,
        "Zack Wheeler": 8.0,
        "Dylan Cease": 8.5,
    }

    _MLB_HITS = {
        "Freddie Freeman": 1.4,
        "Mookie Betts": 1.3,
        "Paul Goldschmidt": 1.2,
        "Juan Soto": 1.3,
    }

    def __init__(self, cfg: dict) -> None:
        self.n = cfg["n_games"]
        self.hi_pct = cfg["high_consistency_pct"]
        self.con_pct = cfg["consistency_pct"]
        self.bdl_key = cfg["balldontlie_key"]

    def analyze(self, player_cfg: dict) -> dict:
        name = player_cfg["name"]
        sport = player_cfg.get("sport", "NBA").upper()
        role = player_cfg.get("role", "player")
        lines = player_cfg.get("lines", {})

        if sport == "NBA":
            logs = self._nba_logs(name)
            cats = NBA_CATS
        else:
            logs = self._mlb_logs(name, role)
            cats = MLB_CATS

        logs = logs[-self.n:]
        averages = {}
        over_rates = {}
        histories = {}

        for cat in cats:
            vals = [g.get(cat, 0) for g in logs]
            avg = round(sum(vals) / len(vals), 2) if vals else 0.0
            line = lines.get(cat, round(avg - 0.5, 1))
            overs = sum(1 for v in vals if v > line)
            rate = round(overs / len(vals), 3) if vals else 0.0
            averages[cat] = avg
            histories[cat] = vals
            over_rates[cat] = {
                "line": line,
                "over_count": overs,
                "total": len(vals),
                "rate": rate,
                "label": self._label(rate),
            }

        best_cat = (
            max(over_rates, key=lambda c: over_rates[c]["rate"])
            if over_rates
            else None
        )
        best_prop = {}
        if best_cat:
            best_prop = {"category": best_cat}
            best_prop.update(over_rates[best_cat])

        return {
            "player": name,
            "sport": sport,
            "role": role,
            "games_analyzed": len(logs),
            "averages": averages,
            "over_rates": over_rates,
            "histories": histories,
            "best_prop": best_prop,
        }

    def _label(self, rate: float) -> str:
        if rate >= self.hi_pct:
            return "ALTA CONSISTENCIA"
        if rate >= self.con_pct:
            return "Consistente"
        if rate >= 0.50:
            return "Moderado"
        return "Inconsistente"

    # ── NBA via BallDontLie ────────────────────────────────────────────────
    def _nba_logs(self, name: str) -> list:
        if not (HAS_REQUESTS and self.bdl_key):
            return self._demo_nba(name)
        try:
            headers = {"Authorization": self.bdl_key}
            query = name.replace(" ", "%20")
            r1 = requests.get(
                f"https://api.balldontlie.io/v1/players?search={query}&per_page=5",
                headers=headers,
                timeout=TIMEOUT,
            )
            r1.raise_for_status()
            players = r1.json().get("data", [])
            if not players:
                return self._demo_nba(name)
            pid = players[0]["id"]
            r2 = requests.get(
                (
                    "https://api.balldontlie.io/v1/stats"
                    f"?player_ids[]={pid}&seasons[]=2024&per_page=15&postseason=false"
                ),
                headers=headers,
                timeout=TIMEOUT,
            )
            r2.raise_for_status()
            raw = r2.json().get("data", [])
            if not raw:
                return self._demo_nba(name)
            logs = []
            for g in raw:
                logs.append({
                    "player": name,
                    "date": g.get("game", {}).get("date", ""),
                    "points": g.get("pts", 0) or 0,
                    "rebounds": g.get("reb", 0) or 0,
                    "assists": g.get("ast", 0) or 0,
                    "three_pointers_made": g.get("fg3m", 0) or 0,
                })
            _log(f"  [BallDontLie] {name}: {len(logs)} juegos.", "G")
            return logs
        except Exception as exc:
            _log(
                f"  [BallDontLie] {name} ({exc.__class__.__name__}) -> demo.",
                "Y",
            )
            return self._demo_nba(name)

    # ── MLB stub ───────────────────────────────────────────────────────────
    def _mlb_logs(self, name: str, role: str) -> list:
        return self._demo_mlb(name, role)

    # ── Generadores demo determinísticos ──────────────────────────────────
    def _rng(self, key: str) -> random.Random:
        seed = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
        return random.Random(seed)

    def _demo_nba(self, name: str) -> list:
        base = self._NBA_BASES.get(name, (20, 5.0, 5.0, 2.0))
        rng = self._rng(name)
        logs = []
        for i in range(self.n):
            logs.append({
                "player": name,
                "date": (date.today() - timedelta(days=i * 3)).isoformat(),
                "points": max(0, round(rng.gauss(base[0], 5))),
                "rebounds": max(0, round(rng.gauss(base[1], 2.5))),
                "assists": max(0, round(rng.gauss(base[2], 2.0))),
                "three_pointers_made": max(0, round(rng.gauss(base[3], 1.5))),
            })
        return logs

    def _demo_mlb(self, name: str, role: str) -> list:
        rng = self._rng(f"{name}{role}")
        logs = []
        if role == "pitcher":
            base_k = self._MLB_K.get(name, 6.5)
            for i in range(self.n):
                logs.append({
                    "player": name,
                    "date": (date.today() - timedelta(days=i * 5)).isoformat(),
                    "strikeouts": max(0, round(rng.gauss(base_k, 2.5))),
                    "hits": 0,
                })
        else:
            base_h = self._MLB_HITS.get(name, 1.0)
            for i in range(self.n):
                logs.append({
                    "player": name,
                    "date": (date.today() - timedelta(days=i)).isoformat(),
                    "strikeouts": 0,
                    "hits": max(0, round(rng.gauss(base_h * 4, 1.2))),
                })
        return logs


# ==============================================================================
# MODULO 3 — TELEGRAM NOTIFIER
# ==============================================================================

class TelegramNotifier:
    """
    Envía el reporte Markdown al canal de Telegram configurado.
    Si las credenciales están vacías o el envío falla, solo loguea.
    """

    MAX_LENGTH = 4096

    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.enabled = bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            _log(
                "  [Telegram] Credenciales no configuradas. "
                "Solo archivos locales.",
                "Y",
            )
            return False
        if not HAS_REQUESTS:
            _log("  [Telegram] requests no disponible.", "R")
            return False

        chunks = self._split(text)
        all_ok = True
        for i, chunk in enumerate(chunks, 1):
            try:
                url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                resp = requests.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": "Markdown",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                _log(f"  [Telegram] Parte {i}/{len(chunks)} enviada OK", "G")
            except Exception as exc:
                _log(f"  [Telegram] Error parte {i}: {exc}", "R")
                all_ok = False
        return all_ok

    def _split(self, text: str) -> list:
        if len(text) <= self.MAX_LENGTH:
            return [text]
        parts = []
        while text:
            parts.append(text[: self.MAX_LENGTH])
            text = text[self.MAX_LENGTH :]
        return parts


# ==============================================================================
# MODULO 4 — GENERADOR DE REPORTES
# ==============================================================================

class ReportBuilder:
    """
    Construye el Markdown para Telegram y el dict para exportar a JSON.
    Estructura limpia dividida en secciones por deporte.
    """

    def __init__(
        self,
        cfg: dict,
        analysis_date: date,
        spread_results: list,
        prop_results: list,
    ) -> None:
        self.cfg = cfg
        self.d = analysis_date
        self.spreads = spread_results
        self.props = prop_results
        self.hi_pct = cfg["high_consistency_pct"]
        self.con_pct = cfg["consistency_pct"]

    # ── Markdown ──────────────────────────────────────────────────────────
    def markdown(self) -> str:
        d_fmt = self.d.strftime("%d/%m/%Y")
        lines = [
            f"*PARLAY STATS REPORT — {d_fmt}*",
            "_parlay\\_stats\\_analyzer.py v3.0 | SportAPI PRO_",
            "",
        ]

        # ── Sección Spreads agrupada por deporte ───────────────────────────
        lines += [
            "================================",
            "*ANALISIS DE SPREADS DEL DIA*",
            "================================",
        ]

        sports_present = list(
            dict.fromkeys(r["sport"] for r in self.spreads)
        )

        if not self.spreads:
            lines.append("_Sin eventos de spreads para hoy._")
        else:
            for sport_label in sports_present:
                sport_events = [r for r in self.spreads if r["sport"] == sport_label]
                value_ev = [r for r in sport_events if r["value_team"] != "-"]
                neutral_ev = [r for r in sport_events if r["value_team"] == "-"]

                lines.append(f"\n*{sport_label}*")

                if value_ev:
                    for r in value_ev:
                        hs = f"{r['home_spread']:+.1f}" if r["home_spread"] is not None else "N/A"
                        ma = f"{r['home_avg_margin']:+.1f}" if r["home_avg_margin"] is not None else "N/A"
                        lines.append(
                            f"OK `{r['home_team']} vs {r['away_team']}`"
                            f" [{r['tournament']}] {r['start_time']}\n"
                            f"   Handicap: `{hs}` | Margen prom: `{ma} pts`"
                            f" | Book: _{r['bookmaker']}_\n"
                            f"   Valor en: *{r['value_team']}*"
                        )

                if neutral_ev:
                    for r in neutral_ev:
                        hs = f"{r['home_spread']:+.1f}" if r["home_spread"] is not None else "N/A"
                        lines.append(
                            f"- `{r['home_team']} vs {r['away_team']}`"
                            f" [{r['tournament']}] {r['start_time']}"
                            f" | Spread: `{hs}`"
                        )

        # ── Sección Player Props ───────────────────────────────────────────
        lines += [
            "",
            "================================",
            "*LINEAS MAS SEGURAS PARA PARLAY*",
            "================================",
        ]

        all_flat = self._flat_props()
        high_flat = sorted(
            [p for p in all_flat if p["rate"] >= self.hi_pct],
            key=lambda x: x["rate"],
            reverse=True,
        )
        mid_flat = sorted(
            [p for p in all_flat if self.con_pct <= p["rate"] < self.hi_pct],
            key=lambda x: x["rate"],
            reverse=True,
        )

        hi_int = int(self.hi_pct * 100)
        con_int = int(self.con_pct * 100)

        if high_flat:
            lines.append(f"\n*Alta Consistencia (>={hi_int}%):*\n")
            for hp in high_flat:
                cat_es = CAT_ES.get(hp["cat"], hp["cat"].title())
                lines.append(
                    f"* *{hp['player']}* `[{hp['sport']}]`\n"
                    f"   {cat_es} Over `{hp['line']}`"
                    f" -> `{hp['over_count']}/{hp['total']}`"
                    f" = *{hp['rate'] * 100:.0f}%*"
                )
        else:
            lines.append(f"_Sin props >={hi_int}% hoy._")

        if mid_flat:
            lines.append(f"\n*Consistentes ({con_int}-{hi_int - 1}%):*\n")
            for mp in mid_flat[:6]:
                cat_es = CAT_ES.get(mp["cat"], mp["cat"].title())
                lines.append(
                    f"- *{mp['player']}* {cat_es} Over `{mp['line']}`"
                    f" ({mp['over_count']}/{mp['total']}"
                    f" = {mp['rate'] * 100:.0f}%)"
                )

        lines += [
            "",
            "================================",
            "_Analisis informativo. Apuesta con responsabilidad._",
            f"`v3.0 SportAPI PRO` {datetime.now().strftime('%H:%M')} UTC",
        ]
        return "\n".join(lines)

    # ── JSON export ────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        all_flat = self._flat_props()
        safe_cfg = {
            k: v
            for k, v in self.cfg.items()
            if k not in (
                "SPORTAPI_KEY",
                "balldontlie_key",
                "telegram_bot_token",
                "telegram_chat_id",
            )
        }
        return {
            "meta": {
                "generated_at": (
                    datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                ),
                "analysis_date": self.d.isoformat(),
                "version": "3.0.0",
                "config": safe_cfg,
            },
            "spreads": self.spreads,
            "player_props": self.props,
            "summary": {
                "spread_value_games": [
                    f"{r['home_team']} vs {r['away_team']} [{r['sport']}]"
                    for r in self.spreads
                    if r["value_team"] != "-"
                ],
                "high_consistency_props": [
                    f"{p['player']} - {CAT_ES.get(p['cat'], p['cat'])}"
                    for p in all_flat
                    if p["rate"] >= self.hi_pct
                ],
                "consistent_props": [
                    f"{p['player']} - {CAT_ES.get(p['cat'], p['cat'])}"
                    for p in all_flat
                    if self.con_pct <= p["rate"] < self.hi_pct
                ],
            },
        }

    def _flat_props(self) -> list:
        out = []
        for p in self.props:
            for cat, d in p.get("over_rates", {}).items():
                out.append({
                    "player": p["player"],
                    "sport": p["sport"],
                    "cat": cat,
                    "line": d["line"],
                    "over_count": d["over_count"],
                    "total": d["total"],
                    "rate": d["rate"],
                    "label": d["label"],
                })
        return out


# ==============================================================================
# PIPELINE PRINCIPAL
# ==============================================================================

def run(cfg: dict = None) -> dict:
    """
    Punto de entrada único. Ejecuta los 4 módulos en orden.
    Retorna el dict completo (útil en notebooks de Colab).
    """
    if cfg is None:
        cfg = CONFIG

    today = _resolve_date(cfg["analysis_date"])
    out_dir = Path(cfg["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    _log("\n" + "#" * 66, "C", bold=True)
    _log("  PARLAY STATS ANALYZER  v3.0  |  SportAPI PRO", "C", bold=True)
    _log(f"  Fecha: {today}", "C")
    _log("#" * 66, "C", bold=True)

    # ── Módulo 1: Spreads (SportAPI multideporte) ─────────────────────────
    _section("MODULO 1 — SPREADS MULTIDEPORTE (SportAPI)")
    spread_results = SpreadAnalyzer(cfg).analyze(today)

    value_count = sum(1 for r in spread_results if r["value_team"] != "-")
    _log(
        f"\n  Total eventos procesados: {len(spread_results)} | "
        f"Con valor detectado: {value_count}",
        "G" if value_count > 0 else "W",
        bold=True,
    )

    # ── Módulo 2+3: Player Props + Consistencia ───────────────────────────
    _section("MODULO 2+3 — PLAYER PROPS + CONSISTENCIA")
    pa = PlayerPropsAnalyzer(cfg)
    prop_results = []

    for player_cfg in cfg["roster"]:
        result = pa.analyze(player_cfg)
        prop_results.append(result)
        _log(f"\n  [{result['sport']}] {result['player']}", "M", bold=True)
        for cat, d in result.get("over_rates", {}).items():
            rate = d["rate"]
            if rate >= cfg["high_consistency_pct"]:
                color = "G"
            elif rate >= cfg["consistency_pct"]:
                color = "Y"
            else:
                color = "R"
            _log(
                f"    {CAT_ES.get(cat, cat):<18}  "
                f"linea={d['line']:5.1f}  "
                f"{d['over_count']}/{d['total']} "
                f"{_bar(rate)}  {rate * 100:.0f}%  {d['label']}",
                color,
            )

    # ── Módulo 4: Reporte ─────────────────────────────────────────────────
    _section("GENERANDO REPORTE")
    report = ReportBuilder(cfg, today, spread_results, prop_results)
    md_text = report.markdown()
    data = report.to_dict()

    date_str = today.isoformat()
    md_path = out_dir / f"parlay_report_{date_str}.md"
    json_path = out_dir / f"parlay_data_{date_str}.json"

    md_path.write_text(md_text, encoding="utf-8")
    _log(f"  Markdown guardado  -> {md_path}", "G", bold=True)

    json_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _log(f"  JSON guardado      -> {json_path}", "G", bold=True)

    _section("PREVIEW MARKDOWN")
    print(md_text)

    # ── Telegram ──────────────────────────────────────────────────────────
    _section("ENVIO TELEGRAM")
    TelegramNotifier(
        cfg["telegram_bot_token"], cfg["telegram_chat_id"]
    ).send(md_text)

    # ── Resumen ───────────────────────────────────────────────────────────
    _section("RESUMEN FINAL")
    s = data["summary"]
    hi_int = int(cfg["high_consistency_pct"] * 100)
    con_int = int(cfg["consistency_pct"] * 100)

    _log(
        f"  Juegos con spread de valor:        {len(s['spread_value_games'])}",
        "W",
    )
    for g in s["spread_value_games"]:
        _log(f"    -> {g}", "G")

    _log(
        f"  Props Alta Consistencia (>={hi_int}%): {len(s['high_consistency_props'])}",
        "G",
        bold=True,
    )
    for p in s["high_consistency_props"]:
        _log(f"    * {p}", "G")

    _log(
        f"  Props Consistentes ({con_int}-{hi_int - 1}%):    {len(s['consistent_props'])}",
        "Y",
    )
    for p in s["consistent_props"]:
        _log(f"    OK {p}", "Y")

    _log("\n  Pipeline completado.\n", "C", bold=True)
    return data


# ==============================================================================
# ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    run(CONFIG)
