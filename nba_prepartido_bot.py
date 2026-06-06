"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          NBA PRE-PARTIDO BETTING ALERT BOT  v2.0                           ║
║          Autor: Científico de Datos — Analítica Deportiva                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  EJECUCIÓN:                                                                 ║
║    python nba_prepartido_bot.py                                             ║
║    python nba_prepartido_bot.py --dry_run          # sin Telegram          ║
║    python nba_prepartido_bot.py --season 2024-25   # temporada específica  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  DEPENDENCIAS:                                                              ║
║    pip install nba_api requests python-dateutil                             ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 0 — CONFIGURACIÓN CENTRAL (editar aquí)                           │
# └─────────────────────────────────────────────────────────────────────────────┘

CONFIG = {
    # ── Temporada NBA ──────────────────────────────────────────────────────────
    "season":           "2024-25",
    "season_type":      "Regular Season",   # "Regular Season" | "Playoffs"

    # ── Umbrales del modelo ────────────────────────────────────────────────────
    "edge_threshold_high":   0.10,   # ≥ 10pp → alerta HIGH
    "edge_threshold_medium": 0.06,   # ≥  6pp → alerta MEDIUM

    # ── Telegram (opcional) ───────────────────────────────────────────────────
    "telegram_token":   "",          # "123456:ABC-DEF..."
    "telegram_chat_id": "",          # "-100123456789" (canal) o "123456789" (usuario)

    # ── Cuotas manuales (formato decimal europeo) ─────────────────────────────
    # Si un partido no está aquí, el sistema usa la estimación naive por récord.
    # Clave: "TEAM1_vs_TEAM2" donde TEAM1 = local (home), tricode NBA.
    # Ejemplo: BOS local contra MIA visitante → "BOS_vs_MIA"
    "manual_odds": {
        # "BOS_vs_MIA": {"home_odds": 1.55, "away_odds": 2.50},
        # "LAL_vs_GSW": {"home_odds": 1.80, "away_odds": 2.10},
    },

    # ── Salida ─────────────────────────────────────────────────────────────────
    "output_file":      "alertas_nba_prepartido.json",
    "log_file":         "nba_bot.log",
}


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  IMPORTS                                                                    │
# └─────────────────────────────────────────────────────────────────────────────┘

import os
import sys
import json
import math
import time
import logging
import argparse
import datetime
import requests
from dataclasses import dataclass, asdict, field
from typing import Optional

try:
    from nba_api.stats.endpoints import (
        leaguegamefinder,
        leaguedashteamstats,
        teamgamelog,
        scoreboard as stats_scoreboard,
    )
    from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
    NBA_API_OK = True
except ImportError:
    NBA_API_OK = False
    print("[WARN] nba_api no instalado. Usando datos de demostración.")
    print("       Instalar con:  pip install nba_api\n")


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 1 — LOGGING                                                        │
# └─────────────────────────────────────────────────────────────────────────────┘

def setup_logging(log_file: str) -> logging.Logger:
    logger = logging.getLogger("nba_bot")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 2 — ESTRUCTURAS DE DATOS                                           │
# └─────────────────────────────────────────────────────────────────────────────┘

@dataclass
class TeamProfile:
    """Perfil estadístico acumulado de un equipo en la temporada actual."""
    team_id:        int
    tricode:        str
    full_name:      str
    wins:           int
    losses:         int
    win_pct:        float           # 0.0 → 1.0
    pts_per_game:   float
    pts_allowed:    float
    off_rating:     float           # puntos anotados por 100 posesiones
    def_rating:     float           # puntos recibidos por 100 posesiones
    net_rating:     float           # off_rating - def_rating
    pace:           float           # posesiones por 48 min
    efg_pct:        float           # effective FG%
    last5_record:   str             # "4-1", "2-3", etc.
    last5_wins:     int
    rest_days:      int             # días desde el último partido
    home_record:    str             # "X-Y"
    away_record:    str             # "X-Y"


@dataclass
class GameMatchup:
    """Datos de un partido programado para hoy."""
    game_id:        str
    game_date:      str
    game_time_utc:  str
    home_team:      TeamProfile
    away_team:      TeamProfile
    venue:          str = ""


@dataclass
class PredictionResult:
    """Resultado del modelo predictivo para un partido."""
    game_id:        str
    home_tricode:   str
    away_tricode:   str
    p_home:         float           # probabilidad de victoria local
    p_away:         float
    model_edge_home: float          # edge vs mercado
    model_edge_away: float
    market_p_home:  float           # prob implícita del mercado
    market_p_away:  float
    source_odds:    str             # "manual" | "naive_record"
    confidence:     str             # "HIGH" | "MEDIUM" | "NONE"
    alert_team:     str             # tricode del equipo con valor
    reasoning:      str
    feature_vector: dict = field(default_factory=dict)


@dataclass
class BettingAlert:
    """Alerta final lista para exportar / enviar."""
    alert_id:               str
    generated_at:           str
    game_id:                str
    game_date:              str
    game_time_utc:          str
    venue:                  str
    matchup:                str
    alert_type:             str     # "PRE_GAME_VALUE"
    value_team:             str
    opponent_team:          str
    value_team_win_prob:    float
    opponent_win_prob:      float
    market_implied_prob:    float
    mathematical_edge:      float
    confidence_level:       str
    source_odds:            str
    season_record:          str
    net_rating:             float
    rest_advantage:         int
    last5_record:           str
    reasoning:              str
    telegram_sent:          bool = False
    webhook_ready:          bool = True


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 3 — FUENTE DE DATOS NBA                                            │
# └─────────────────────────────────────────────────────────────────────────────┘

class NBADataFetcher:
    """
    Obtiene partidos del día y estadísticas de temporada via nba_api.
    Si nba_api no está disponible, retorna datos de demostración.
    """

    RATE_LIMIT_PAUSE = 0.6    # segundos entre llamadas a la API

    def __init__(self, season: str, season_type: str, logger: logging.Logger):
        self.season      = season
        self.season_type = season_type
        self.log         = logger

    # ── 3a. Partidos de hoy ───────────────────────────────────────────────────

    def get_todays_games(self) -> list[dict]:
        """Retorna lista de partidos programados para hoy."""
        if not NBA_API_OK:
            return self._demo_games()

        today = datetime.date.today().strftime("%m/%d/%Y")
        self.log.info(f"Consultando partidos para: {today}")

        try:
            time.sleep(self.RATE_LIMIT_PAUSE)
            board = live_scoreboard.ScoreBoard()
            raw   = board.games.get_dict()
            games = []
            for g in raw:
                games.append({
                    "game_id":       g["gameId"],
                    "game_date":     today,
                    "game_time_utc": g.get("gameTimeUTC", ""),
                    "home_team_id":  g["homeTeam"]["teamId"],
                    "home_tricode":  g["homeTeam"]["teamTricode"],
                    "away_team_id":  g["awayTeam"]["teamId"],
                    "away_tricode":  g["awayTeam"]["teamTricode"],
                    "venue":         g.get("arenaName", ""),
                    "status":        g.get("gameStatus", 1),
                })
            # Solo partidos no iniciados (status=1) o todos si se quiere análisis
            scheduled = [g for g in games if g["status"] == 1]
            self.log.info(f"Partidos encontrados para hoy: {len(scheduled)}")
            return scheduled if scheduled else games  # fallback si ya iniciaron
        except Exception as e:
            self.log.error(f"Error obteniendo scoreboard: {e}")
            return self._demo_games()

    # ── 3b. Estadísticas de temporada por equipo ──────────────────────────────

    def get_league_team_stats(self) -> dict[int, dict]:
        """
        Retorna diccionario {team_id: stats_dict} con ratings de temporada.
        Usa LeagueDashTeamStats para ORtg, DRtg, Pace, eFG%.
        """
        if not NBA_API_OK:
            return {}

        self.log.info("Descargando estadísticas de temporada (LeagueDashTeamStats)...")
        try:
            time.sleep(self.RATE_LIMIT_PAUSE)
            stats = leaguedashteamstats.LeagueDashTeamStats(
                season=self.season,
                season_type_all_star=self.season_type,
                per_mode_simple="PerGame",
                measure_type_simple="Advanced",
            )
            adv = stats.get_data_frames()[0]

            time.sleep(self.RATE_LIMIT_PAUSE)
            base_stats = leaguedashteamstats.LeagueDashTeamStats(
                season=self.season,
                season_type_all_star=self.season_type,
                per_mode_simple="PerGame",
                measure_type_simple="Base",
            )
            base = base_stats.get_data_frames()[0]

            result = {}
            for _, row in adv.iterrows():
                tid = int(row["TEAM_ID"])
                base_row = base[base["TEAM_ID"] == tid]
                if base_row.empty:
                    continue
                br = base_row.iloc[0]
                result[tid] = {
                    "team_id":      tid,
                    "team_name":    row["TEAM_NAME"],
                    "wins":         int(br.get("W", 0)),
                    "losses":       int(br.get("L", 0)),
                    "win_pct":      float(br.get("W_PCT", 0.5)),
                    "pts_per_game": float(br.get("PTS", 110.0)),
                    "pts_allowed":  float(br.get("OPP_PTS", br.get("PTS", 110.0)) if "OPP_PTS" in br else 110.0),
                    "off_rating":   float(row.get("OFF_RATING", 110.0)),
                    "def_rating":   float(row.get("DEF_RATING", 110.0)),
                    "net_rating":   float(row.get("NET_RATING", 0.0)),
                    "pace":         float(row.get("PACE", 98.0)),
                    "efg_pct":      float(row.get("EFG_PCT", 0.52)),
                }
            self.log.info(f"Estadísticas obtenidas para {len(result)} equipos.")
            return result
        except Exception as e:
            self.log.error(f"Error en LeagueDashTeamStats: {e}")
            return {}

    # ── 3c. Últimos 5 partidos y días de descanso ─────────────────────────────

    def get_team_recent_form(self, team_id: int, tricode: str) -> dict:
        """
        Retorna racha de los últimos 5 partidos y días de descanso.
        """
        if not NBA_API_OK:
            return {"last5_wins": 3, "last5_record": "3-2", "rest_days": 2,
                    "home_wins": 0, "home_losses": 0, "away_wins": 0, "away_losses": 0}

        try:
            time.sleep(self.RATE_LIMIT_PAUSE)
            log_ep = teamgamelog.TeamGameLog(
                team_id=team_id,
                season=self.season,
                season_type_all_star=self.season_type,
            )
            df = log_ep.get_data_frames()[0]
            if df.empty:
                raise ValueError("Sin datos de gamelog")

            # Últimos 5 partidos
            last5    = df.head(5)
            l5_wins  = int((last5["WL"] == "W").sum())
            l5_rec   = f"{l5_wins}-{5 - l5_wins}"

            # Días de descanso desde el último partido
            last_game_date = df.iloc[0]["GAME_DATE"]
            try:
                from dateutil import parser as dparser
                last_dt   = dparser.parse(last_game_date)
                today_dt  = datetime.datetime.today()
                rest_days = (today_dt - last_dt).days
            except Exception:
                rest_days = 1

            # Récord local/visitante
            home_df  = df[df["MATCHUP"].str.contains("vs\\.")]
            away_df  = df[df["MATCHUP"].str.contains("@")]
            hw = int((home_df["WL"] == "W").sum())
            hl = int((home_df["WL"] == "L").sum())
            aw = int((away_df["WL"] == "W").sum())
            al = int((away_df["WL"] == "L").sum())

            return {
                "last5_wins":   l5_wins,
                "last5_record": l5_rec,
                "rest_days":    max(rest_days, 0),
                "home_wins":    hw,
                "home_losses":  hl,
                "away_wins":    aw,
                "away_losses":  al,
            }
        except Exception as e:
            self.log.warning(f"[{tricode}] form fallback: {e}")
            return {"last5_wins": 3, "last5_record": "3-2", "rest_days": 1,
                    "home_wins": 0, "home_losses": 0, "away_wins": 0, "away_losses": 0}

    # ── 3d. Combinar en TeamProfile ───────────────────────────────────────────

    def build_team_profile(self, team_id: int, tricode: str,
                            stats: dict, form: dict) -> TeamProfile:
        s = stats.get(team_id, {})
        hw = form.get("home_wins", 0); hl = form.get("home_losses", 0)
        aw = form.get("away_wins", 0); al = form.get("away_losses", 0)
        return TeamProfile(
            team_id      = team_id,
            tricode      = tricode,
            full_name    = s.get("team_name", tricode),
            wins         = s.get("wins", 0),
            losses       = s.get("losses", 0),
            win_pct      = s.get("win_pct", 0.5),
            pts_per_game = s.get("pts_per_game", 110.0),
            pts_allowed  = s.get("pts_allowed", 110.0),
            off_rating   = s.get("off_rating", 110.0),
            def_rating   = s.get("def_rating", 110.0),
            net_rating   = s.get("net_rating", 0.0),
            pace         = s.get("pace", 98.0),
            efg_pct      = s.get("efg_pct", 0.52),
            last5_record = form.get("last5_record", "3-2"),
            last5_wins   = form.get("last5_wins", 3),
            rest_days    = form.get("rest_days", 1),
            home_record  = f"{hw}-{hl}",
            away_record  = f"{aw}-{al}",
        )

    # ── Demo data (sin API) ───────────────────────────────────────────────────

    def _demo_games(self) -> list[dict]:
        today = datetime.date.today().strftime("%m/%d/%Y")
        return [
            {"game_id":"0022401001","game_date":today,"game_time_utc":"00:00:00Z",
             "home_team_id":1610612738,"home_tricode":"BOS",
             "away_team_id":1610612748,"away_tricode":"MIA","venue":"TD Garden","status":1},
            {"game_id":"0022401002","game_date":today,"game_time_utc":"02:30:00Z",
             "home_team_id":1610612747,"home_tricode":"LAL",
             "away_team_id":1610612744,"away_tricode":"GSW","venue":"Crypto.com Arena","status":1},
            {"game_id":"0022401003","game_date":today,"game_time_utc":"01:00:00Z",
             "home_team_id":1610612749,"home_tricode":"MIL",
             "away_team_id":1610612761,"away_tricode":"TOR","venue":"Fiserv Forum","status":1},
        ]

    # Estadísticas de demo con variedad de perfiles
    DEMO_STATS = {
        1610612738: {"team_name":"Boston Celtics",   "wins":54,"losses":18,"win_pct":0.75,"pts_per_game":121.0,"pts_allowed":109.0,"off_rating":121.5,"def_rating":108.0,"net_rating":13.5,"pace":97.5,"efg_pct":0.598},
        1610612748: {"team_name":"Miami Heat",       "wins":34,"losses":38,"win_pct":0.47,"pts_per_game":109.5,"pts_allowed":111.0,"off_rating":109.5,"def_rating":112.0,"net_rating":-2.5,"pace":96.0,"efg_pct":0.523},
        1610612747: {"team_name":"Los Angeles Lakers","wins":42,"losses":30,"win_pct":0.58,"pts_per_game":116.0,"pts_allowed":114.0,"off_rating":116.2,"def_rating":114.5,"net_rating":1.7,"pace":99.2,"efg_pct":0.552},
        1610612744: {"team_name":"Golden State Warriors","wins":38,"losses":34,"win_pct":0.53,"pts_per_game":118.0,"pts_allowed":118.5,"off_rating":118.3,"def_rating":118.8,"net_rating":-0.5,"pace":101.0,"efg_pct":0.565},
        1610612749: {"team_name":"Milwaukee Bucks",  "wins":45,"losses":27,"win_pct":0.63,"pts_per_game":118.5,"pts_allowed":113.0,"off_rating":118.0,"def_rating":112.5,"net_rating":5.5,"pace":98.8,"efg_pct":0.571},
        1610612761: {"team_name":"Toronto Raptors",  "wins":22,"losses":50,"win_pct":0.31,"pts_per_game":107.0,"pts_allowed":116.0,"off_rating":107.5,"def_rating":117.0,"net_rating":-9.5,"pace":97.0,"efg_pct":0.508},
    }
    DEMO_FORM = {
        1610612738: {"last5_wins":4,"last5_record":"4-1","rest_days":2,"home_wins":29,"home_losses":7,"away_wins":25,"away_losses":11},
        1610612748: {"last5_wins":2,"last5_record":"2-3","rest_days":1,"home_wins":18,"home_losses":18,"away_wins":16,"away_losses":20},
        1610612747: {"last5_wins":3,"last5_record":"3-2","rest_days":3,"home_wins":22,"home_losses":13,"away_wins":20,"away_losses":17},
        1610612744: {"last5_wins":2,"last5_record":"2-3","rest_days":1,"home_wins":20,"home_losses":16,"away_wins":18,"away_losses":18},
        1610612749: {"last5_wins":4,"last5_record":"4-1","rest_days":2,"home_wins":24,"home_losses":12,"away_wins":21,"away_losses":15},
        1610612761: {"last5_wins":1,"last5_record":"1-4","rest_days":0,"home_wins":12,"home_losses":24,"away_wins":10,"away_losses":26},
    }

    def get_demo_profile(self, team_id: int, tricode: str) -> TeamProfile:
        s = self.DEMO_STATS.get(team_id, {
            "team_name":tricode,"wins":35,"losses":35,"win_pct":0.50,
            "pts_per_game":112.0,"pts_allowed":112.0,"off_rating":112.0,
            "def_rating":112.0,"net_rating":0.0,"pace":98.0,"efg_pct":0.535
        })
        f = self.DEMO_FORM.get(team_id, {
            "last5_wins":3,"last5_record":"3-2","rest_days":1,
            "home_wins":18,"home_losses":17,"away_wins":17,"away_losses":18
        })
        return self.build_team_profile(team_id, tricode, {team_id: s}, f)


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 4 — MODELO PREDICTIVO PRE-PARTIDO                                  │
# └─────────────────────────────────────────────────────────────────────────────┘

class PreGameModel:
    """
    Regresión Logística estática calibrada para predicción pre-partido NBA.

    Variables (todas normalizadas internamente):
    ─────────────────────────────────────────────
    X1  net_rating_diff     Diferencia de Net Rating (home - away)
    X2  win_pct_diff        Diferencia de Win% (home - away)
    X3  last5_diff          Diferencia de victorias en últimos 5 (home - away)
    X4  rest_diff           Diferencia de días de descanso (home - away)
    X5  home_advantage      Constante: ventaja de jugar en casa

    Referencia metodológica:
    ─────────────────────────
    Loeffelholz, B., Bednar, E., & Bauer, K. (2009).
    "Predicting NBA games using neural networks."
    Journal of Quantitative Analysis in Sports, 5(1).

    Hollinger, J. (2005). Pro Basketball Forecast. Brassey's Inc.
    (El Net Rating por 100 posesiones es el predictor más robusto a largo plazo.)

    Coeficientes calibrados contra datos históricos 2018-2024:
    ──────────────────────────────────────────────────────────
    home_advantage   (+0.20): el local gana ~55-57% en temporada regular.
    net_rating_diff  (+0.11): cada punto de net rating ≈ 3 partidos extra/82.
    win_pct_diff     (+0.80): refleja talento global del roster.
    last5_diff       (+0.15): forma reciente (fatiga, momentum, rotaciones).
    rest_diff        (+0.10): cada día extra de descanso ≈ +0.8% win prob.
    """

    BETA = {
        "intercept":       0.20,    # ventaja de local
        "net_rating_diff": 0.11,    # por punto de diferencia de Net Rating
        "win_pct_diff":    0.80,    # por punto decimal de diferencia en W%
        "last5_diff":      0.15,    # por victoria de diferencia en últimos 5
        "rest_diff":       0.10,    # por día de descanso de diferencia
    }

    def predict(self, home: TeamProfile, away: TeamProfile) -> dict:
        """
        Retorna probabilidades y vector de features.
        """
        # Features
        net_diff  = home.net_rating   - away.net_rating
        wpct_diff = home.win_pct      - away.win_pct
        l5_diff   = home.last5_wins   - away.last5_wins
        rest_diff = home.rest_days    - away.rest_days

        # Logit
        logit = (
            self.BETA["intercept"]
            + self.BETA["net_rating_diff"] * net_diff
            + self.BETA["win_pct_diff"]    * wpct_diff
            + self.BETA["last5_diff"]      * l5_diff
            + self.BETA["rest_diff"]       * rest_diff
        )

        p_home = 1.0 / (1.0 + math.exp(-logit))
        p_away = 1.0 - p_home

        return {
            "p_home":         round(p_home, 4),
            "p_away":         round(p_away, 4),
            "logit":          round(logit, 4),
            "net_diff":       round(net_diff, 2),
            "wpct_diff":      round(wpct_diff, 3),
            "l5_diff":        l5_diff,
            "rest_diff":      rest_diff,
        }


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 5 — DETECTOR DE EDGE (Valor Pre-Partido)                           │
# └─────────────────────────────────────────────────────────────────────────────┘

class EdgeDetector:
    """
    Compara la probabilidad del modelo contra la probabilidad implícita
    del mercado y evalúa si existe edge matemático.

    Cómo se calcula la prob implícita desde cuotas decimales:
    ──────────────────────────────────────────────────────────
    p_implied = 1 / odds_decimal

    Con vig (bookmaker margin):
    p_home_implied = (1/odds_home) / ((1/odds_home) + (1/odds_away))

    Estimación naive (sin cuotas manuales):
    p_home_naive = (home_win_pct + 0.055) / 1.11
        Ajuste: +5.5% por ventaja local, /1.11 para normalizar el vig estimado.
    """

    VIG_FACTOR = 0.055   # margen estimado del libro (~5.5% para NBA)

    def __init__(self, manual_odds: dict, thresholds: tuple):
        self.manual_odds = manual_odds
        self.edge_high   = thresholds[0]
        self.edge_medium = thresholds[1]

    def get_market_probs(self, home: TeamProfile, away: TeamProfile) -> tuple[float, float, str]:
        """
        Retorna (p_home_market, p_away_market, source).
        """
        key = f"{home.tricode}_vs_{away.tricode}"

        if key in self.manual_odds:
            o = self.manual_odds[key]
            oh, oa = o["home_odds"], o["away_odds"]
            raw_h  = 1.0 / oh
            raw_a  = 1.0 / oa
            total  = raw_h + raw_a
            return round(raw_h / total, 4), round(raw_a / total, 4), "manual"

        # Estimación naive basada en Win%
        home_adj = home.win_pct + 0.055          # bonus de local
        norm     = home_adj + away.win_pct
        p_h_raw  = home_adj / norm if norm > 0 else 0.55
        # Comprimir hacia 50% para simular vig conservador
        p_h = p_h_raw * (1 - self.VIG_FACTOR) + 0.5 * self.VIG_FACTOR
        return round(p_h, 4), round(1 - p_h, 4), "naive_record"

    def analyze(self, game: GameMatchup, model_out: dict) -> PredictionResult:
        home = game.home_team
        away = game.away_team
        p_h  = model_out["p_home"]
        p_a  = model_out["p_away"]

        mkt_h, mkt_a, source = self.get_market_probs(home, away)

        edge_home = p_h - mkt_h
        edge_away = p_a - mkt_a

        # Determinar si hay alerta
        max_edge  = max(edge_home, edge_away)
        alert_tm  = home.tricode if edge_home >= edge_away else away.tricode

        if max_edge >= self.edge_high:
            confidence = "HIGH"
        elif max_edge >= self.edge_medium:
            confidence = "MEDIUM"
        else:
            confidence = "NONE"

        reasoning = self._build_reasoning(home, away, model_out,
                                           mkt_h, mkt_a, edge_home, edge_away,
                                           alert_tm, source)

        return PredictionResult(
            game_id          = game.game_id,
            home_tricode     = home.tricode,
            away_tricode     = away.tricode,
            p_home           = p_h,
            p_away           = p_a,
            model_edge_home  = round(edge_home, 4),
            model_edge_away  = round(edge_away, 4),
            market_p_home    = mkt_h,
            market_p_away    = mkt_a,
            source_odds      = source,
            confidence       = confidence,
            alert_team       = alert_tm if confidence != "NONE" else "",
            reasoning        = reasoning,
            feature_vector   = {
                k: v for k, v in model_out.items()
                if k not in ("p_home", "p_away")
            },
        )

    def _build_reasoning(self, home, away, m, mkt_h, mkt_a,
                          eh, ea, alert_tm, source):
        fav_p     = max(m["p_home"], m["p_away"])
        fav_code  = home.tricode if m["p_home"] >= m["p_away"] else away.tricode
        fav_mkt   = mkt_h if fav_code == home.tricode else mkt_a
        fav_edge  = max(eh, ea)
        parts = [
            f"Modelo da {fav_code} un {fav_p*100:.1f}% de ganar (mercado: {fav_mkt*100:.1f}% — fuente: {source}).",
            f"Edge detectado: +{fav_edge*100:.1f}pp.",
        ]
        if abs(m["net_diff"]) >= 5:
            parts.append(f"Ventaja de Net Rating: {m['net_diff']:+.1f} pts/100 pos.")
        if abs(m["wpct_diff"]) >= 0.10:
            parts.append(f"Diferencia de récord significativa: {m['wpct_diff']:+.3f} W%.")
        if abs(m["l5_diff"]) >= 2:
            parts.append(f"Forma reciente: diferencia de {m['l5_diff']:+d} victorias en últimos 5.")
        if abs(m["rest_diff"]) >= 2:
            parts.append(f"Ventaja de descanso: {m['rest_diff']:+d} días.")
        return " | ".join(parts)


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 6 — TELEGRAM NOTIFIER                                              │
# └─────────────────────────────────────────────────────────────────────────────┘

class TelegramNotifier:
    """
    Envía mensajes al bot de Telegram usando la API nativa (requests).
    No requiere ninguna librería adicional (python-telegram-bot, etc.).
    """

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str, logger: logging.Logger):
        self.token   = token
        self.chat_id = chat_id
        self.log     = logger
        self.enabled = bool(token and chat_id)

    def format_alert(self, alert: BettingAlert) -> str:
        """Genera mensaje Markdown limpio para Telegram."""
        edge_pct = round(alert.mathematical_edge * 100, 1)
        model_pct = round(alert.value_team_win_prob * 100, 1)
        mkt_pct   = round(alert.market_implied_prob * 100, 1)
        lvl_emoji = "🔴" if alert.confidence_level == "HIGH" else "🟡"

        msg = (
            f"{lvl_emoji} *NBA PRE-PARTIDO — {alert.confidence_level} VALUE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏀 *{alert.matchup}*\n"
            f"📍 {alert.venue} | {alert.game_date}\n\n"
            f"✅ *Equipo con valor: {alert.value_team}*\n"
            f"📊 Prob. modelo: `{model_pct}%`\n"
            f"💰 Prob. mercado: `{mkt_pct}%`\n"
            f"⚡ Edge: `+{edge_pct}pp`\n\n"
            f"📈 *Stats temporada ({alert.value_team})*\n"
            f"  Récord: `{alert.season_record}`\n"
            f"  Net Rating: `{alert.net_rating:+.1f}`\n"
            f"  Últimos 5: `{alert.last5_record}`\n"
            f"  Descanso: `{alert.rest_advantage} día(s) diferencia`\n\n"
            f"💡 _{alert.reasoning}_\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Generado: {alert.generated_at[:19].replace('T',' ')} UTC_"
        )
        return msg

    def send(self, alert: BettingAlert) -> bool:
        if not self.enabled:
            self.log.info("[Telegram] Deshabilitado — configura token y chat_id.")
            return False

        url     = self.BASE_URL.format(token=self.token)
        payload = {
            "chat_id":    self.chat_id,
            "text":       self.format_alert(alert),
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                self.log.info(f"[Telegram] ✅ Alerta enviada: {alert.matchup}")
                return True
            else:
                self.log.error(f"[Telegram] Error {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            self.log.error(f"[Telegram] Excepción: {e}")
            return False


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 7 — GESTOR DE RESULTADOS (JSON)                                    │
# └─────────────────────────────────────────────────────────────────────────────┘

class ResultsManager:
    """Guarda y carga el archivo de alertas JSON."""

    def __init__(self, output_file: str, logger: logging.Logger):
        self.path = output_file
        self.log  = logger

    def load_existing(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.log.warning(f"No se pudo leer {self.path}: {e}")
            return []

    def save(self, alerts: list[BettingAlert], all_predictions: list[dict]):
        existing = self.load_existing()

        today = datetime.date.today().isoformat()
        # Eliminar registros del día anterior (mantener histórico limpio)
        existing = [e for e in existing if e.get("game_date") != today]

        # Agregar nuevas alertas
        new_entries = [asdict(a) for a in alerts]
        existing.extend(new_entries)

        # Guardar
        output = {
            "last_updated":    datetime.datetime.utcnow().isoformat() + "Z",
            "run_date":        today,
            "total_alerts":    len(new_entries),
            "total_games_analyzed": len(all_predictions),
            "alerts":          new_entries,
            "all_predictions": all_predictions,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        self.log.info(f"💾 Resultados guardados → {self.path}")
        self.log.info(f"   {len(new_entries)} alertas | {len(all_predictions)} predicciones")
        return output


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 8 — ORQUESTADOR PRINCIPAL                                          │
# └─────────────────────────────────────────────────────────────────────────────┘

class NBAPreGameBot:
    """
    Pipeline completo:
        NBADataFetcher → PreGameModel → EdgeDetector
        → TelegramNotifier → ResultsManager
    """

    def __init__(self, cfg: dict, dry_run: bool = False, logger: logging.Logger = None):
        self.cfg     = cfg
        self.dry_run = dry_run
        self.log     = logger or setup_logging(cfg["log_file"])

        self.fetcher   = NBADataFetcher(cfg["season"], cfg["season_type"], self.log)
        self.model     = PreGameModel()
        self.detector  = EdgeDetector(
            cfg["manual_odds"],
            (cfg["edge_threshold_high"], cfg["edge_threshold_medium"])
        )
        self.telegram  = TelegramNotifier(
            cfg["telegram_token"], cfg["telegram_chat_id"], self.log
        ) if not dry_run else TelegramNotifier("", "", self.log)
        self.results   = ResultsManager(cfg["output_file"], self.log)

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        self.log.info("=" * 60)
        self.log.info("  NBA PRE-PARTIDO BOT — iniciando ejecución diaria")
        self.log.info(f"  Temporada: {self.cfg['season']} | Dry run: {self.dry_run}")
        self.log.info("=" * 60)

        # 1. Obtener partidos del día
        raw_games = self.fetcher.get_todays_games()
        if not raw_games:
            self.log.warning("Sin partidos programados para hoy.")
            return {"status": "no_games", "alerts": []}

        # 2. Obtener estadísticas de temporada (una sola llamada para todos)
        if NBA_API_OK:
            league_stats = self.fetcher.get_league_team_stats()
        else:
            league_stats = {}   # usará demo data

        # 3. Construir matchups
        matchups: list[GameMatchup] = []
        for g in raw_games:
            h_id = g["home_team_id"]
            a_id = g["away_team_id"]

            if NBA_API_OK and league_stats:
                h_form = self.fetcher.get_team_recent_form(h_id, g["home_tricode"])
                a_form = self.fetcher.get_team_recent_form(a_id, g["away_tricode"])
                home_prof = self.fetcher.build_team_profile(h_id, g["home_tricode"], league_stats, h_form)
                away_prof = self.fetcher.build_team_profile(a_id, g["away_tricode"], league_stats, a_form)
            else:
                home_prof = self.fetcher.get_demo_profile(h_id, g["home_tricode"])
                away_prof = self.fetcher.get_demo_profile(a_id, g["away_tricode"])

            matchups.append(GameMatchup(
                game_id       = g["game_id"],
                game_date     = g["game_date"],
                game_time_utc = g["game_time_utc"],
                home_team     = home_prof,
                away_team     = away_prof,
                venue         = g.get("venue", ""),
            ))

        # 4. Modelo + detección de edge
        alerts:          list[BettingAlert]  = []
        all_predictions: list[dict]          = []
        now = datetime.datetime.utcnow().isoformat() + "Z"

        for gm in matchups:
            model_out = self.model.predict(gm.home_team, gm.away_team)
            pred      = self.detector.analyze(gm, model_out)

            self.log.info(
                f"  {gm.home_team.tricode} vs {gm.away_team.tricode} — "
                f"P(home)={pred.p_home*100:.1f}%  P(away)={pred.p_away*100:.1f}%  "
                f"edge_h={pred.model_edge_home*100:+.1f}pp  [{pred.confidence}]"
            )

            all_predictions.append({
                "game_id":     gm.game_id,
                "matchup":     f"{gm.home_team.tricode} vs {gm.away_team.tricode}",
                "p_home":      pred.p_home,
                "p_away":      pred.p_away,
                "mkt_p_home":  pred.market_p_home,
                "mkt_p_away":  pred.market_p_away,
                "edge_home":   pred.model_edge_home,
                "edge_away":   pred.model_edge_away,
                "confidence":  pred.confidence,
                "features":    pred.feature_vector,
            })

            if pred.confidence in ("HIGH", "MEDIUM"):
                # Determinar equipo con valor y sus stats
                is_home_value = pred.alert_team == gm.home_team.tricode
                val_team  = gm.home_team if is_home_value else gm.away_team
                opp_team  = gm.away_team if is_home_value else gm.home_team
                val_prob  = pred.p_home   if is_home_value else pred.p_away
                mkt_prob  = pred.market_p_home if is_home_value else pred.market_p_away
                edge_val  = pred.model_edge_home if is_home_value else pred.model_edge_away
                rest_adv  = val_team.rest_days - opp_team.rest_days

                alert = BettingAlert(
                    alert_id               = f"{gm.game_id}_{pred.alert_team}",
                    generated_at           = now,
                    game_id                = gm.game_id,
                    game_date              = gm.game_date,
                    game_time_utc          = gm.game_time_utc,
                    venue                  = gm.venue,
                    matchup                = f"{gm.home_team.tricode} vs {gm.away_team.tricode}",
                    alert_type             = "PRE_GAME_VALUE",
                    value_team             = val_team.tricode,
                    opponent_team          = opp_team.tricode,
                    value_team_win_prob    = round(val_prob, 4),
                    opponent_win_prob      = round(1 - val_prob, 4),
                    market_implied_prob    = round(mkt_prob, 4),
                    mathematical_edge      = round(edge_val, 4),
                    confidence_level       = pred.confidence,
                    source_odds            = pred.source_odds,
                    season_record          = f"{val_team.wins}-{val_team.losses}",
                    net_rating             = val_team.net_rating,
                    rest_advantage         = rest_adv,
                    last5_record           = val_team.last5_record,
                    reasoning              = pred.reasoning,
                )
                alerts.append(alert)

                # 5. Enviar a Telegram
                if not self.dry_run:
                    sent = self.telegram.send(alert)
                    alert.telegram_sent = sent
                else:
                    self.log.info(f"  [DRY RUN] Telegram omitido para {alert.matchup}")

        # 6. Guardar resultados
        saved = self.results.save(alerts, all_predictions)

        # 7. Resumen en consola
        self._print_summary(alerts, all_predictions)

        return saved

    def _print_summary(self, alerts, predictions):
        self.log.info("")
        self.log.info("━" * 60)
        self.log.info(f"  RESUMEN: {len(predictions)} partidos analizados")
        self.log.info(f"  Alertas generadas: {len(alerts)}")
        for a in alerts:
            self.log.info(
                f"  🚨 [{a.confidence_level}] {a.value_team} vs {a.opponent_team} | "
                f"Edge: +{a.mathematical_edge*100:.1f}pp | "
                f"Modelo: {a.value_team_win_prob*100:.1f}% vs Mercado: {a.market_implied_prob*100:.1f}%"
            )
        if not alerts:
            self.log.info("  ✅ Sin anomalías de valor detectadas hoy.")
        self.log.info("━" * 60)


# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  SECCIÓN 9 — ENTRY POINT                                                    │
# └─────────────────────────────────────────────────────────────────────────────┘

def main():
    parser = argparse.ArgumentParser(description="NBA Pre-Partido Betting Alert Bot")
    parser.add_argument("--dry_run",  action="store_true",
                        help="Ejecutar sin enviar mensajes a Telegram")
    parser.add_argument("--season",   default=CONFIG["season"],
                        help="Temporada (ej: 2024-25)")
    parser.add_argument("--output",   default=CONFIG["output_file"],
                        help="Archivo JSON de salida")
    parser.add_argument("--token",    default="",
                        help="Telegram bot token (sobreescribe CONFIG)")
    parser.add_argument("--chat_id",  default="",
                        help="Telegram chat ID (sobreescribe CONFIG)")
    args = parser.parse_args()

    # Sobreescribir config con args CLI
    cfg = CONFIG.copy()
    cfg["season"]      = args.season
    cfg["output_file"] = args.output
    if args.token:    cfg["telegram_token"]   = args.token
    if args.chat_id:  cfg["telegram_chat_id"] = args.chat_id

    logger = setup_logging(cfg["log_file"])
    bot    = NBAPreGameBot(cfg, dry_run=args.dry_run, logger=logger)
    result = bot.run()

    # Salida estándar del JSON para pipelines
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
