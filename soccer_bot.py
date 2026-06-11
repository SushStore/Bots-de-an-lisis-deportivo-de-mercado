# =============================================================================
# soccer_bot.py
# Bot de análisis estadístico para fútbol internacional
# Mundial de Norteamérica 2026 + Ligas Europeas y Locales
# Compatible con Google Colab
# Fuente de datos: API-Football (RapidAPI)
# =============================================================================

# ── INSTALACIÓN (Colab) ──────────────────────────────────────────────────────
# !pip install requests numpy scipy

# =============================================================================
# SECCIÓN CONFIG — Completar antes de ejecutar
# =============================================================================

CONFIG = {
    # ── API-Football (RapidAPI) ──────────────────────────────────────────────
    # Obtén tu key en: https://rapidapi.com/api-sports/api/api-football
    "API_FOOTBALL_KEY": "ffc05d5442msh0d6cdc5e550926dp12de2cjsnb5e113e904ed",

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
    # ID de API-Football : (nombre legible, canal Telegram, avg_goles_liga)
    "LEAGUES": {
        1:   ("Copa del Mundo 2026",  "mundial_2026",   1.25),
        39:  ("Premier League",       "premier_league", 1.42),
        140: ("La Liga",              "la_liga",        1.38),
        135: ("Serie A",              "serie_a",        1.36),
        78:  ("Bundesliga",           "bundesliga",     1.55),
        61:  ("Ligue 1",              "ligue_1",        1.30),
        262: ("Liga MX",              "liga_mx",        1.30),
    },

    # Temporada activa (API-Football usa el año de inicio)
    "SEASON": 2025,
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
# CLIENTE API-FOOTBALL
# =============================================================================

class APIFootballClient:
    """
    Wrapper sobre la API de api-football.com (RapidAPI).
    Maneja headers, rate-limit básico y fallback a demo data.
    """

    BASE_URL = "https://rapidapi.com"
    # Endpoint alternativo si tienes suscripción directa (sin RapidAPI):
    # BASE_URL = "https://v3.football.api-sports.io"

    def __init__(self, api_key: str):
        self.api_key  = api_key
        self.headers  = {
            "X-RapidAPI-Key":  ffc05d5442msh0d6cdc5e550926dp12de2cjsnb5e113e904ed,
            "X-RapidAPI-Host": "sportapi7.p.rapidapi.com",
        }
        self._cache: dict = {}          # cache simple en memoria por sesión
        self._last_call: float = 0.0    # para respetar rate-limit

    # ── Método interno ───────────────────────────────────────────────────────

    def _get(self, endpoint: str, params: dict) -> Optional[dict]:
        """
        GET genérico con cache, retry x2 y throttle de 1 req/seg.
        Retorna el dict JSON completo o None si falla.
        """
        cache_key = endpoint + str(sorted(params.items()))
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Throttle suave: mínimo 1 segundo entre llamadas
        elapsed = time.time() - self._last_call
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        url = f"{self.BASE_URL}/{endpoint}"
        for attempt in range(1, 3):
            try:
                resp = requests.get(url, headers=self.headers, params=params, timeout=15)
                self._last_call = time.time()
                if resp.status_code == 200:
                    data = resp.json()
                    self._cache[cache_key] = data
                    return data
                elif resp.status_code == 429:
                    print(f"  [API] Rate-limit hit, esperando 10s...")
                    time.sleep(10)
                else:
                    print(f"  [API] Error {resp.status_code} en {endpoint} (intento {attempt})")
                    time.sleep(2)
            except requests.exceptions.RequestException as exc:
                print(f"  [API] Excepcion en {endpoint}: {exc} (intento {attempt})")
                time.sleep(3)
        return None

    # ── Endpoints públicos ───────────────────────────────────────────────────

    def get_fixtures_by_date(self, league_id: int, season: int, target_date: str) -> list[dict]:
        """
        Retorna lista de fixtures para (liga, temporada, fecha YYYY-MM-DD).
        Cada elemento es el objeto `fixture` tal como lo devuelve la API.
        """
        data = self._get("fixtures", {
            "league": league_id,
            "season": season,
            "date":   target_date,
        })
        if not data or "response" not in data:
            return []
        return data["response"]

    def get_team_recent_fixtures(self, team_id: int, last: int = 7) -> list[dict]:
        """
        Retorna los últimos `last` fixtures de un equipo (todos los estados).
        """
        data = self._get("fixtures", {
            "team": team_id,
            "last": last,
        })
        if not data or "response" not in data:
            return []
        return data["response"]

    def get_h2h(self, team1_id: int, team2_id: int, last: int = 5) -> list[dict]:
        """
        Retorna los últimos enfrentamientos directos entre dos equipos.
        """
        data = self._get("fixtures/headtohead", {
            "h2h":  f"{team1_id}-{team2_id}",
            "last": last,
        })
        if not data or "response" not in data:
            return []
        return data["response"]

    def get_odds(self, fixture_id: int, bookmaker_id: int = 8) -> Optional[dict]:
        """
        Obtiene cuotas para un fixture dado.
        bookmaker_id=8 → Bet365 (disponible en plan Basic+).
        Retorna dict con claves 'home', 'draw', 'away' en cuota decimal,
        o None si no hay cuotas disponibles.
        """
        data = self._get("odds", {
            "fixture":    fixture_id,
            "bookmaker":  bookmaker_id,
        })
        if not data or not data.get("response"):
            return None

        try:
            bets = data["response"][0]["bookmakers"][0]["bets"]
            for bet in bets:
                if bet["name"] == "Match Winner":
                    values = {v["value"]: float(v["odd"]) for v in bet["values"]}
                    return {
                        "home": values.get("Home"),
                        "draw": values.get("Draw"),
                        "away": values.get("Away"),
                    }
        except (IndexError, KeyError, TypeError):
            pass
        return None


# =============================================================================
# TRANSFORMADORES API → Clases del modelo
# =============================================================================

def parse_team_recent_matches(raw_fixtures: list[dict], team_id: int) -> list[dict]:
    """
    Convierte los fixtures crudos de API-Football al formato que espera TeamStats:
      [{"scored": int, "conceded": int, "home": bool}, ...]

    Solo incluye partidos ya finalizados (status: FT, AET, PEN).
    """
    FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD"}
    result = []

    for fixture in raw_fixtures:
        status = fixture.get("fixture", {}).get("status", {}).get("short", "")
        if status not in FINISHED_STATUSES:
            continue

        teams  = fixture.get("teams", {})
        goals  = fixture.get("goals", {})

        home_id   = teams.get("home", {}).get("id")
        away_id   = teams.get("away", {}).get("id")
        home_goals = goals.get("home")
        away_goals = goals.get("away")

        if home_goals is None or away_goals is None:
            continue

        if team_id == home_id:
            result.append({
                "scored":   int(home_goals),
                "conceded": int(away_goals),
                "home":     True,
            })
        elif team_id == away_id:
            result.append({
                "scored":   int(away_goals),
                "conceded": int(home_goals),
                "home":     False,
            })

    return result


def parse_h2h_matches(raw_h2h: list[dict], home_team_id: int) -> list[dict]:
    """
    Convierte H2H crudo al formato que espera MatchAnalyzer:
      [{"home_goals": int, "away_goals": int}, ...]

    Normaliza siempre desde la perspectiva del home_team_id como local.
    """
    FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD"}
    result = []

    for fixture in raw_h2h:
        status = fixture.get("fixture", {}).get("status", {}).get("short", "")
        if status not in FINISHED_STATUSES:
            continue

        teams = fixture.get("teams", {})
        goals = fixture.get("goals", {})

        api_home_id = teams.get("home", {}).get("id")
        home_goals  = goals.get("home")
        away_goals  = goals.get("away")

        if home_goals is None or away_goals is None:
            continue

        # Normalizar: siempre home_team_id como "home" en la perspectiva
        if api_home_id == home_team_id:
            result.append({"home_goals": int(home_goals), "away_goals": int(away_goals)})
        else:
            result.append({"home_goals": int(away_goals), "away_goals": int(home_goals)})

    return result


def parse_odds_safe(raw_odds: Optional[dict]) -> Optional[dict]:
    """
    Valida que las cuotas tengan los tres valores y sean > 1.0.
    Retorna None si algún valor falta o parece inválido.
    """
    if not raw_odds:
        return None
    required = ("home", "draw", "away")
    if not all(raw_odds.get(k) and raw_odds[k] > 1.0 for k in required):
        return None
    return raw_odds


# =============================================================================
# FETCHER PRINCIPAL
# =============================================================================

class APIFootballFetcher:
    """
    Orquesta todas las llamadas a API-Football para construir el diccionario
    de datos en el mismo formato que antes usaba SAMPLE_DATA.
    Resultado: dict compatible con run_analysis().
    """

    FALLBACK_AVG_GOALS = 1.35   # usado si el equipo no tiene historial

    def __init__(self, client: APIFootballClient):
        self.client = client

    def fetch_day(self, target_date: str) -> dict:
        """
        Punto de entrada principal. Retorna el dict de ligas/grupos/partidos
        listo para pasar a run_analysis().

        target_date: string "YYYY-MM-DD"
        """
        result = {}
        leagues = CONFIG["LEAGUES"]
        season  = CONFIG["SEASON"]

        for league_id, (league_name, channel_key, avg_goals) in leagues.items():
            print(f"\n[Fetcher] Cargando fixtures: {league_name} ({target_date})")

            fixtures = self.client.get_fixtures_by_date(league_id, season, target_date)

            if not fixtures:
                print(f"  Sin partidos para {league_name} el {target_date}")
                continue

            print(f"  Encontrados {len(fixtures)} fixture(s)")

            matches_by_group: dict[str, list] = {}

            for fix in fixtures:
                # Solo procesar partidos programados/en juego del día
                status = fix.get("fixture", {}).get("status", {}).get("short", "")
                if status in ("CANC", "PST", "ABD", "INT", "AWD", "WO"):
                    print(f"  Saltando fixture con status '{status}'")
                    continue

                fixture_id = fix["fixture"]["id"]
                home_team  = fix["teams"]["home"]
                away_team  = fix["teams"]["away"]
                group_name = fix.get("league", {}).get("round", league_name)

                home_id    = home_team["id"]
                away_id    = away_team["id"]
                home_name  = home_team["name"]
                away_name  = away_team["name"]

                print(f"  Procesando: {home_name} vs {away_name} (ID:{fixture_id})")

                # ── Forma reciente local ────────────────────────────────────
                raw_home_fixtures = self.client.get_team_recent_fixtures(home_id, last=10)
                home_matches = parse_team_recent_matches(raw_home_fixtures, home_id)
                home_matches = home_matches[-CONFIG["RECENT_MATCHES_WINDOW"]:]

                # ── Forma reciente visitante ────────────────────────────────
                raw_away_fixtures = self.client.get_team_recent_fixtures(away_id, last=10)
                away_matches = parse_team_recent_matches(raw_away_fixtures, away_id)
                away_matches = away_matches[-CONFIG["RECENT_MATCHES_WINDOW"]:]

                # ── H2H ────────────────────────────────────────────────────
                raw_h2h   = self.client.get_h2h(home_id, away_id, last=6)
                h2h_data  = parse_h2h_matches(raw_h2h, home_id)

                # ── Cuotas ─────────────────────────────────────────────────
                raw_odds  = self.client.get_odds(fixture_id)
                odds_data = parse_odds_safe(raw_odds)

                if odds_data:
                    print(f"    Cuotas: 1={odds_data['home']}  X={odds_data['draw']}  2={odds_data['away']}")
                else:
                    print(f"    Cuotas: no disponibles para este fixture")

                # ── Ensamblar el partido ────────────────────────────────────
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
                    "fixture_id": fixture_id,
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
        Igual que fetch_day() pero si la API no responde o la key está vacía,
        usa FALLBACK_DATA de demostración para no romper el pipeline.
        """
        key = CONFIG.get("API_FOOTBALL_KEY", "").strip()
        if not key:
            print("[Fetcher] API_FOOTBALL_KEY vacía → usando FALLBACK_DATA de demostración")
            return FALLBACK_DATA

        try:
            data = self.fetch_day(target_date)
        except Exception as exc:
            print(f"[Fetcher] Error inesperado: {exc} → usando FALLBACK_DATA")
            return FALLBACK_DATA

        if not data:
            print("[Fetcher] No se obtuvieron datos reales → usando FALLBACK_DATA")
            return FALLBACK_DATA

        return data


# =============================================================================
# MOTOR DE POISSON
# =============================================================================

class PoissonEngine:
    """
    Calcula probabilidades de resultados usando distribución de Poisson.
    """

    @staticmethod
    def goal_expectancy(
        home_attack:    float,
        home_defense:   float,
        away_attack:    float,
        away_defense:   float,
        home_advantage: float = 1.10,
    ) -> tuple[float, float]:
        lambda_home = home_attack * away_defense * home_advantage
        lambda_away = away_attack * home_defense
        return round(lambda_home, 4), round(lambda_away, 4)

    @staticmethod
    def result_matrix(lambda_home: float, lambda_away: float, max_goals: int = 8) -> np.ndarray:
        home_probs = [poisson.pmf(i, lambda_home) for i in range(max_goals + 1)]
        away_probs = [poisson.pmf(j, lambda_away) for j in range(max_goals + 1)]
        return np.outer(home_probs, away_probs)

    @staticmethod
    def market_1x2(matrix: np.ndarray) -> dict:
        n        = matrix.shape[0]
        home_win = sum(matrix[i][j] for i in range(n) for j in range(n) if i > j)
        draw     = sum(matrix[i][j] for i in range(n) for j in range(n) if i == j)
        away_win = sum(matrix[i][j] for i in range(n) for j in range(n) if i < j)
        total    = home_win + draw + away_win
        return {
            "home": round(home_win / total, 4),
            "draw": round(draw     / total, 4),
            "away": round(away_win / total, 4),
        }

    @staticmethod
    def over_under(lambda_home: float, lambda_away: float, line: float = 2.5, max_goals: int = 15) -> dict:
        totals = {}
        for gh in range(max_goals + 1):
            for ga in range(max_goals + 1):
                t = gh + ga
                p = poisson.pmf(gh, lambda_home) * poisson.pmf(ga, lambda_away)
                totals[t] = totals.get(t, 0) + p
        over  = sum(p for g, p in totals.items() if g > line)
        under = sum(p for g, p in totals.items() if g <= line)
        total = over + under
        return {"over": round(over/total, 4), "under": round(under/total, 4), "line": line}


# =============================================================================
# MODELO DE EQUIPO
# =============================================================================

class TeamStats:
    """
    Contenedor de estadísticas de un equipo basadas en partidos recientes.
    """

    def __init__(self, name: str, recent_matches: list[dict], league_avg_goals: float = 1.35):
        self.name       = name
        self.matches    = recent_matches[-CONFIG["RECENT_MATCHES_WINDOW"]:]
        self.league_avg = league_avg_goals
        self._compute()

    def _compute(self):
        if not self.matches:
            self.attack       = 1.0
            self.defense      = 1.0
            self.avg_scored   = self.league_avg
            self.avg_conceded = self.league_avg
            return
        scored_list   = [m["scored"]   for m in self.matches]
        conceded_list = [m["conceded"] for m in self.matches]
        self.avg_scored   = sum(scored_list)   / len(scored_list)
        self.avg_conceded = sum(conceded_list) / len(conceded_list)
        self.attack  = self.avg_scored   / self.league_avg if self.league_avg else 1.0
        self.defense = self.avg_conceded / self.league_avg if self.league_avg else 1.0

    def form_string(self) -> str:
        form = []
        for m in self.matches[-5:]:
            if m["scored"] > m["conceded"]:
                form.append("G")
            elif m["scored"] < m["conceded"]:
                form.append("P")
            else:
                form.append("E")
        return "".join(form) if form else "N/D"


# =============================================================================
# ANALIZADOR DE PARTIDO
# =============================================================================

class MatchAnalyzer:
    """
    Analiza un partido completo: 1X2, Over/Under, edge vs mercado, H2H.
    """

    def __init__(
        self,
        home:             TeamStats,
        away:             TeamStats,
        market_odds:      Optional[dict] = None,
        h2h_matches:      Optional[list[dict]] = None,
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
        avg_home = sum(m["home_goals"] for m in self.h2h) / len(self.h2h)
        avg_away = sum(m["away_goals"] for m in self.h2h) / len(self.h2h)
        adj_home = avg_home / self.league_avg if self.league_avg else 1.0
        adj_away = avg_away / self.league_avg if self.league_avg else 1.0
        return round(0.80 + 0.20 * adj_home, 4), round(0.80 + 0.20 * adj_away, 4)

    def analyze(self) -> dict:
        h2h_adj_h, h2h_adj_a = self._h2h_adjustment()

        lambda_home, lambda_away = self.engine.goal_expectancy(
            home_attack  = self.home.attack  * h2h_adj_h,
            home_defense = self.home.defense,
            away_attack  = self.away.attack  * h2h_adj_a,
            away_defense = self.away.defense,
        )

        matrix    = self.engine.result_matrix(lambda_home, lambda_away)
        probs_1x2 = self.engine.market_1x2(matrix)
        probs_ou  = self.engine.over_under(lambda_home, lambda_away, line=CONFIG["OU_LINE"])

        market_probs, edge_1x2 = {}, {}
        if self.odds:
            rh = 1 / self.odds.get("home", 99)
            rd = 1 / self.odds.get("draw", 99)
            ra = 1 / self.odds.get("away", 99)
            margin = rh + rd + ra
            if margin > 0:
                market_probs = {
                    "home": round(rh / margin, 4),
                    "draw": round(rd / margin, 4),
                    "away": round(ra / margin, 4),
                }
                edge_1x2 = {
                    "home": round((probs_1x2["home"] - market_probs["home"]) * 100, 2),
                    "draw": round((probs_1x2["draw"] - market_probs["draw"]) * 100, 2),
                    "away": round((probs_1x2["away"] - market_probs["away"]) * 100, 2),
                }

        value_picks = []
        threshold   = CONFIG["EDGE_THRESHOLD_PP"]

        if edge_1x2:
            labels = {
                "home": f"1 ({self.home.name})",
                "draw": "X (Empate)",
                "away": f"2 ({self.away.name})",
            }
            for result, edge in edge_1x2.items():
                if edge >= threshold:
                    value_picks.append({
                        "market":      "1X2",
                        "pick":        labels[result],
                        "model_prob":  probs_1x2[result],
                        "market_prob": market_probs[result],
                        "edge_pp":     edge,
                        "odds":        self.odds.get(result) if self.odds else None,
                    })

        if probs_ou["over"] >= CONFIG["MIN_CONFIDENCE"]:
            value_picks.append({
                "market":     "Over/Under",
                "pick":       f"Over {CONFIG['OU_LINE']}",
                "model_prob": probs_ou["over"],
                "edge_pp":    None,
            })
        elif probs_ou["under"] >= CONFIG["MIN_CONFIDENCE"]:
            value_picks.append({
                "market":     "Over/Under",
                "pick":       f"Under {CONFIG['OU_LINE']}",
                "model_prob": probs_ou["under"],
                "edge_pp":    None,
            })

        return {
            "home_team":    self.home.name,
            "away_team":    self.away.name,
            "lambda_home":  lambda_home,
            "lambda_away":  lambda_away,
            "probs_1x2":    probs_1x2,
            "probs_ou":     probs_ou,
            "market_probs": market_probs,
            "edge_1x2":     edge_1x2,
            "value_picks":  value_picks,
            "home_form":    self.home.form_string(),
            "away_form":    self.away.form_string(),
        }


# =============================================================================
# CONSTRUCTOR DE REPORTE
# =============================================================================

def clean_text(text: str) -> str:
    """
    Elimina todo rastro de Markdown antes del envío por Telegram.
    Garantiza texto plano 100% limpio.
    """
    for token in ["***", "**", "*", "___", "__", "`", "~~~", "~~", "###", "##", "#", ">", "[", "]", "(", ")"]:
        text = text.replace(token, "")
    # underscore suelto → espacio
    text = text.replace("_", " ")
    # colapsar espacios y saltos repetidos
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_match_block(result: dict, idx: int) -> str:
    lines = []
    sep   = "=" * 40

    lines.append(sep)
    lines.append(f"  PARTIDO {idx}  |  {result['home_team']} vs {result['away_team']}")
    lines.append(sep)

    lines.append(f"Forma reciente:")
    lines.append(f"  {result['home_team']}: {result['home_form']}")
    lines.append(f"  {result['away_team']}: {result['away_form']}")

    lh = result["lambda_home"]
    la = result["lambda_away"]
    lines.append(f"Goles esperados:  {result['home_team']} {lh:.2f}  |  {result['away_team']} {la:.2f}")

    p = result["probs_1x2"]
    lines.append(f"Prob. modelo 1X2: 1={p['home']*100:.1f}%  X={p['draw']*100:.1f}%  2={p['away']*100:.1f}%")

    if result.get("market_probs"):
        mp = result["market_probs"]
        lines.append(f"Prob. mercado:    1={mp['home']*100:.1f}%  X={mp['draw']*100:.1f}%  2={mp['away']*100:.1f}%")
        e  = result["edge_1x2"]
        lines.append(f"Edge (pp):        1={e['home']:+.1f}  X={e['draw']:+.1f}  2={e['away']:+.1f}")
    else:
        lines.append(f"Cuotas: no disponibles")

    ou = result["probs_ou"]
    lines.append(f"Over/Under {ou['line']}: Over={ou['over']*100:.1f}%  Under={ou['under']*100:.1f}%")

    picks = result["value_picks"]
    if picks:
        lines.append("")
        lines.append("  PICKS DE VALOR")
        for pk in picks:
            prob_str = f"{pk['model_prob']*100:.1f}%"
            edge_str = f"  Edge: +{pk['edge_pp']:.1f}pp" if pk.get("edge_pp") else ""
            odds_str = f"  Cuota: {pk['odds']:.2f}" if pk.get("odds") else ""
            lines.append(f"  {pk['market']} - {pk['pick']}  ({prob_str}){edge_str}{odds_str}")
    else:
        if CONFIG["SHOW_INFORMATIVE"]:
            lines.append("  (Sin edge suficiente - informativo)")

    lines.append("")
    return "\n".join(lines)


def build_report(league_name: str, matches_results: list[dict]) -> str:
    today   = date.today().strftime("%d/%m/%Y")
    header  = "\n".join([
        "",
        "=" * 40,
        "  SOCCER BOT - ANALISIS ESTADISTICO",
        f"  Liga: {league_name.upper()}",
        f"  Fecha: {today}",
        "=" * 40,
        "",
    ])
    body   = "\n".join(build_match_block(r, i) for i, r in enumerate(matches_results, 1))
    footer = "\n".join([
        "=" * 40,
        f"  Modelo: Poisson + H2H  |  Edge min: {CONFIG['EDGE_THRESHOLD_PP']}pp",
        "  Bot generado automaticamente",
        "=" * 40,
        "",
    ])
    return clean_text(header + body + footer)


# =============================================================================
# ENVÍO TELEGRAM
# =============================================================================

def send_telegram(channel_key: str, text: str) -> dict:
    """
    Envía texto plano (sin parse_mode) al canal de Telegram correspondiente.
    Divide automáticamente si supera 4096 caracteres.
    """
    token   = CONFIG["TELEGRAM_BOT_TOKEN"]
    chat_id = CONFIG["CHANNELS"].get(channel_key) or CONFIG["CHANNELS"]["general"]
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks  = [text[i:i+4096] for i in range(0, len(text), 4096)]
    results = []

    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk}   # sin parse_mode intencional
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
    """
    Itera sobre el dict de ligas/grupos producido por el fetcher (o fallback),
    instancia TeamStats + MatchAnalyzer para cada partido, construye el reporte
    y opcionalmente lo envía a Telegram.
    """
    all_results = []

    for league_key, league_data in data.items():
        league_avg  = league_data.get("league_avg_goals", 1.35)
        channel_key = league_data.get("channel_key", "general")
        groups      = league_data.get("groups", {})
        league_results = []

        for group_name, matches in groups.items():
            print(f"\n[Analizando] {league_key.upper()} / {group_name}")

            for match in matches:
                home_stats = TeamStats(
                    name             = match["home"]["name"],
                    recent_matches   = match["home"]["recent_matches"],
                    league_avg_goals = league_avg,
                )
                away_stats = TeamStats(
                    name             = match["away"]["name"],
                    recent_matches   = match["away"]["recent_matches"],
                    league_avg_goals = league_avg,
                )

                analyzer = MatchAnalyzer(
                    home             = home_stats,
                    away             = away_stats,
                    market_odds      = match.get("odds"),
                    h2h_matches      = match.get("h2h"),
                    league_avg_goals = league_avg,
                )

                result = analyzer.analyze()
                result["group"] = group_name
                league_results.append(result)

                picks_str = " | ".join(
                    f"{pk['pick']} ({pk['model_prob']*100:.0f}%)"
                    for pk in result["value_picks"]
                ) or "Sin edge"

                print(
                    f"  {result['home_team']} vs {result['away_team']}"
                    f"  Lambdas: {result['lambda_home']:.2f}/{result['lambda_away']:.2f}"
                    f"  Picks: {picks_str}"
                )

        report_text = build_report(
            league_name     = league_key.replace("_", " ").title(),
            matches_results = league_results,
        )

        print(f"\n{'='*52}")
        print(f"REPORTE  {league_key.upper()}")
        print(f"{'='*52}")
        print(report_text)

        if send:
            send_telegram(channel_key, report_text)

        all_results.append({
            "league":  league_key,
            "channel": channel_key,
            "count":   len(league_results),
            "results": league_results,
        })

    return all_results


# =============================================================================
# FALLBACK DATA — Se usa solo cuando API_FOOTBALL_KEY está vacía o falla la API
# =============================================================================

FALLBACK_DATA = {
    "premier_league_demo": {
        "league_avg_goals": 1.42,
        "channel_key":      "premier_league",
        "groups": {
            "Premier League - DEMO": [
                {
                    "home": {
                        "name": "Arsenal (DEMO)",
                        "recent_matches": [
                            {"scored": 2, "conceded": 0, "home": True},
                            {"scored": 3, "conceded": 1, "home": False},
                            {"scored": 1, "conceded": 0, "home": True},
                            {"scored": 2, "conceded": 2, "home": False},
                            {"scored": 3, "conceded": 0, "home": True},
                            {"scored": 0, "conceded": 1, "home": False},
                            {"scored": 2, "conceded": 1, "home": True},
                        ],
                    },
                    "away": {
                        "name": "Chelsea (DEMO)",
                        "recent_matches": [
                            {"scored": 1, "conceded": 1, "home": True},
                            {"scored": 2, "conceded": 2, "home": False},
                            {"scored": 0, "conceded": 1, "home": True},
                            {"scored": 1, "conceded": 0, "home": False},
                            {"scored": 2, "conceded": 1, "home": True},
                            {"scored": 1, "conceded": 2, "home": False},
                            {"scored": 0, "conceded": 0, "home": True},
                        ],
                    },
                    "odds": {"home": 2.00, "draw": 3.60, "away": 3.75},
                    "h2h": [
                        {"home_goals": 2, "away_goals": 2},
                        {"home_goals": 1, "away_goals": 0},
                        {"home_goals": 3, "away_goals": 1},
                    ],
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
    Obtiene datos reales (o fallback) y ejecuta análisis SIN enviar a Telegram.
    """
    if target_date is None:
        target_date = date.today().strftime("%Y-%m-%d")

    print("=" * 52)
    print("  MODO DRY RUN — Sin envio a Telegram")
    print(f"  Fecha analizada: {target_date}")
    print("=" * 52)

    client  = APIFootballClient(api_key=CONFIG.get("API_FOOTBALL_KEY", ""))
    fetcher = APIFootballFetcher(client)
    data    = fetcher.fetch_day_with_fallback(target_date)

    results = run_analysis(data, send=False)

    print(f"\nResumen:")
    total_picks = 0
    for r in results:
        picks = sum(len(m["value_picks"]) for m in r["results"])
        total_picks += picks
        print(f"  {r['league']}: {r['count']} partidos  |  {picks} picks de valor")
    print(f"  Total picks de valor: {total_picks}")
    return results


def production_run(target_date: Optional[str] = None):
    """
    Obtiene datos reales y envía los reportes a Telegram.
    Requiere API_FOOTBALL_KEY y TELEGRAM_BOT_TOKEN configurados en CONFIG.
    """
    if target_date is None:
        target_date = date.today().strftime("%Y-%m-%d")

    print("=" * 52)
    print("  MODO PRODUCCION — Enviando a Telegram")
    print(f"  Fecha analizada: {target_date}")
    print("=" * 52)

    client  = APIFootballClient(api_key=CONFIG["API_FOOTBALL_KEY"])
    fetcher = APIFootballFetcher(client)
    data    = fetcher.fetch_day_with_fallback(target_date)

    return run_analysis(data, send=True)


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    # ──────────────────────────────────────────────────────────
    # Con API_FOOTBALL_KEY vacía  → usa FALLBACK_DATA (demo)
    # Con API_FOOTBALL_KEY llena  → descarga fixtures reales
    #
    # Para enviar a Telegram cambia dry_run() por production_run()
    # ──────────────────────────────────────────────────────────
    dry_run()
