# =============================================================================
# soccer_bot.py
# Bot de análisis estadístico para fútbol internacional
# Mundial de Norteamérica 2026 + Ligas Europeas y Locales
# Compatible con Google Colab
# =============================================================================

# ── INSTALACIÓN (Colab) ──────────────────────────────────────────────────────
# !pip install requests pandas numpy scipy

# =============================================================================
# SECCIÓN CONFIG — Completar antes de ejecutar
# =============================================================================

CONFIG = {
    # Telegram Bot Token obtenido desde @BotFather
    "TELEGRAM_BOT_TOKEN": "8697845783:AAHE_CfzGJY144FBlBrnbbInNJ1B1-frz64",

    # Chat IDs o Channel IDs de destino (pueden ser negativos para canales)
    "CHANNELS": {
        "mundial_2026":     "-1003948622323",   # Canal Mundial 2026
        "premier_league":   "-100XXXXXXXXXX",   # Canal Premier League
        "la_liga":          "-100XXXXXXXXXX",   # Canal La Liga
        "serie_a":          "-100XXXXXXXXXX",   # Canal Serie A
        "bundesliga":       "-100XXXXXXXXXX",   # Canal Bundesliga
        "ligue_1":          "-100XXXXXXXXXX",   # Canal Ligue 1
        "liga_mx":          "-100XXXXXXXXXX",   # Canal Liga MX
        "general":          "-100XXXXXXXXXX",   # Canal general (fallback)
    },

    # Edge mínimo en puntos porcentuales para marcar valor (1X2)
    "EDGE_THRESHOLD_PP": 12,

    # Ventana de partidos recientes para análisis
    "RECENT_MATCHES_WINDOW": 7,

    # Línea Over/Under por defecto
    "OU_LINE": 2.5,

    # Umbral de confianza mínima para publicar una apuesta (0.0 a 1.0)
    "MIN_CONFIDENCE": 0.60,

    # Mostrar picks sin edge como "informativo" en lugar de ignorarlos
    "SHOW_INFORMATIVE": True,
}

# =============================================================================
# IMPORTS
# =============================================================================

import requests
import json
import math
import numpy as np
from datetime import datetime, date
from typing import Optional
from scipy.stats import poisson

# =============================================================================
# MOTOR DE POISSON
# =============================================================================

class PoissonEngine:
    """
    Calcula probabilidades de resultados usando distribución de Poisson.
    Entradas: promedios de goles anotados/recibidos de cada equipo.
    """

    @staticmethod
    def goal_expectancy(
        home_attack: float,
        home_defense: float,
        away_attack: float,
        away_defense: float,
        home_advantage: float = 1.10,
    ) -> tuple[float, float]:
        """
        Retorna (lambda_home, lambda_away): goles esperados para cada equipo.
        home_advantage ajusta el factor cancha propia (default +10%).
        """
        lambda_home = home_attack * away_defense * home_advantage
        lambda_away = away_attack * home_defense
        return round(lambda_home, 4), round(lambda_away, 4)

    @staticmethod
    def result_matrix(
        lambda_home: float,
        lambda_away: float,
        max_goals: int = 8,
    ) -> np.ndarray:
        """
        Genera matriz de probabilidades [goles_local x goles_visitante].
        """
        home_probs = [poisson.pmf(i, lambda_home) for i in range(max_goals + 1)]
        away_probs = [poisson.pmf(j, lambda_away) for j in range(max_goals + 1)]
        matrix = np.outer(home_probs, away_probs)
        return matrix

    @staticmethod
    def market_1x2(matrix: np.ndarray) -> dict:
        """
        Calcula probabilidades 1X2 a partir de la matriz de Poisson.
        Retorna dict con claves: home, draw, away (valores entre 0 y 1).
        """
        n = matrix.shape[0]
        home_win = sum(matrix[i][j] for i in range(n) for j in range(n) if i > j)
        draw     = sum(matrix[i][j] for i in range(n) for j in range(n) if i == j)
        away_win = sum(matrix[i][j] for i in range(n) for j in range(n) if i < j)
        total = home_win + draw + away_win
        return {
            "home": round(home_win / total, 4),
            "draw": round(draw     / total, 4),
            "away": round(away_win / total, 4),
        }

    @staticmethod
    def over_under(
        lambda_home: float,
        lambda_away: float,
        line: float = 2.5,
        max_goals: int = 15,
    ) -> dict:
        """
        Calcula P(Over line) y P(Under line) con Poisson convolucionado.
        """
        total_goals_probs = {}
        for g_home in range(max_goals + 1):
            for g_away in range(max_goals + 1):
                total = g_home + g_away
                prob  = poisson.pmf(g_home, lambda_home) * poisson.pmf(g_away, lambda_away)
                total_goals_probs[total] = total_goals_probs.get(total, 0) + prob

        over  = sum(p for g, p in total_goals_probs.items() if g > line)
        under = sum(p for g, p in total_goals_probs.items() if g <= line)
        total = over + under
        return {
            "over":  round(over  / total, 4),
            "under": round(under / total, 4),
            "line":  line,
        }


# =============================================================================
# MODELO DE EQUIPO
# =============================================================================

class TeamStats:
    """
    Contenedor de estadísticas de un equipo basadas en partidos recientes.
    Calcula automáticamente promedios de ataque y defensa normalizados.
    """

    def __init__(self, name: str, recent_matches: list[dict], league_avg_goals: float = 1.35):
        """
        recent_matches: lista de dicts con claves:
            - scored   (int): goles anotados en ese partido
            - conceded (int): goles recibidos en ese partido
            - home     (bool): True si jugó de local
        league_avg_goals: promedio de goles por equipo en la liga (default UEFA ~1.35)
        """
        self.name = name
        self.matches = recent_matches[-CONFIG["RECENT_MATCHES_WINDOW"]:]
        self.league_avg = league_avg_goals
        self._compute()

    def _compute(self):
        if not self.matches:
            self.attack  = 1.0
            self.defense = 1.0
            self.avg_scored    = self.league_avg
            self.avg_conceded  = self.league_avg
            return

        scored_list   = [m["scored"]   for m in self.matches]
        conceded_list = [m["conceded"] for m in self.matches]

        self.avg_scored   = sum(scored_list)   / len(scored_list)
        self.avg_conceded = sum(conceded_list) / len(conceded_list)

        # Índices Dixon-Coles simplificados
        self.attack  = self.avg_scored   / self.league_avg if self.league_avg else 1.0
        self.defense = self.avg_conceded / self.league_avg if self.league_avg else 1.0

    def form_string(self) -> str:
        """Retorna cadena de forma reciente, ej: GPPGE"""
        result_map = {True: "G", False: "P", None: "E"}
        form = []
        for m in self.matches[-5:]:
            scored, conceded = m["scored"], m["conceded"]
            if scored > conceded:
                form.append("G")
            elif scored < conceded:
                form.append("P")
            else:
                form.append("E")
        return "".join(form) if form else "N/D"


# =============================================================================
# ANALIZADOR DE PARTIDO
# =============================================================================

class MatchAnalyzer:
    """
    Analiza un partido completo evaluando 1X2 y Over/Under.
    Integra H2H para ajustar ligeramente las expectativas.
    """

    def __init__(
        self,
        home: TeamStats,
        away: TeamStats,
        market_odds: Optional[dict] = None,
        h2h_matches: Optional[list[dict]] = None,
        league_avg_goals: float = 1.35,
    ):
        """
        market_odds: dict con claves 'home', 'draw', 'away' en formato cuota decimal.
                     Ej: {"home": 1.80, "draw": 3.40, "away": 4.20}
        h2h_matches: lista de dicts con 'home_goals', 'away_goals'
        """
        self.home    = home
        self.away    = away
        self.odds    = market_odds
        self.h2h     = h2h_matches or []
        self.engine  = PoissonEngine()
        self.league_avg = league_avg_goals

    def _h2h_adjustment(self) -> tuple[float, float]:
        """
        Calcula ajuste de goles esperados basado en H2H.
        Retorna (adj_home, adj_away) — valores cercanos a 1.0 = sin ajuste.
        """
        if not self.h2h:
            return 1.0, 1.0

        h2h_home_goals = [m["home_goals"] for m in self.h2h]
        h2h_away_goals = [m["away_goals"] for m in self.h2h]

        avg_h2h_home = sum(h2h_home_goals) / len(h2h_home_goals)
        avg_h2h_away = sum(h2h_away_goals) / len(h2h_away_goals)

        adj_home = (avg_h2h_home / self.league_avg) if self.league_avg else 1.0
        adj_away = (avg_h2h_away / self.league_avg) if self.league_avg else 1.0

        # Ponderación suave: 20% H2H, 80% forma reciente
        blended_home = 0.80 * 1.0 + 0.20 * adj_home
        blended_away = 0.80 * 1.0 + 0.20 * adj_away
        return round(blended_home, 4), round(blended_away, 4)

    def analyze(self) -> dict:
        """
        Ejecuta el análisis completo y retorna un dict con todos los mercados.
        """
        h2h_adj_home, h2h_adj_away = self._h2h_adjustment()

        lambda_home, lambda_away = self.engine.goal_expectancy(
            home_attack  = self.home.attack  * h2h_adj_home,
            home_defense = self.home.defense,
            away_attack  = self.away.attack  * h2h_adj_away,
            away_defense = self.away.defense,
        )

        matrix    = self.engine.result_matrix(lambda_home, lambda_away)
        probs_1x2 = self.engine.market_1x2(matrix)
        probs_ou  = self.engine.over_under(lambda_home, lambda_away, line=CONFIG["OU_LINE"])

        # Convertir cuotas de mercado a probabilidades implícitas
        market_probs = {}
        edge_1x2     = {}
        if self.odds:
            raw_home = 1 / self.odds.get("home", 99)
            raw_draw = 1 / self.odds.get("draw", 99)
            raw_away = 1 / self.odds.get("away", 99)
            margin   = raw_home + raw_draw + raw_away
            if margin > 0:
                market_probs = {
                    "home": round(raw_home / margin, 4),
                    "draw": round(raw_draw / margin, 4),
                    "away": round(raw_away / margin, 4),
                }
                edge_1x2 = {
                    "home": round((probs_1x2["home"] - market_probs["home"]) * 100, 2),
                    "draw": round((probs_1x2["draw"] - market_probs["draw"]) * 100, 2),
                    "away": round((probs_1x2["away"] - market_probs["away"]) * 100, 2),
                }

        # Determinar picks de valor
        value_picks = []
        threshold   = CONFIG["EDGE_THRESHOLD_PP"]

        if edge_1x2:
            for result, edge in edge_1x2.items():
                if edge >= threshold:
                    label = {"home": f"1 ({self.home.name})",
                             "draw": "X (Empate)",
                             "away": f"2 ({self.away.name})"}[result]
                    value_picks.append({
                        "market":      "1X2",
                        "pick":        label,
                        "model_prob":  probs_1x2[result],
                        "market_prob": market_probs[result],
                        "edge_pp":     edge,
                        "odds":        self.odds.get(result) if self.odds else None,
                    })

        # Over/Under: comparar con mercado si hay cuotas OU
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
            "home_team":     self.home.name,
            "away_team":     self.away.name,
            "lambda_home":   lambda_home,
            "lambda_away":   lambda_away,
            "probs_1x2":     probs_1x2,
            "probs_ou":      probs_ou,
            "market_probs":  market_probs,
            "edge_1x2":      edge_1x2,
            "value_picks":   value_picks,
            "home_form":     self.home.form_string(),
            "away_form":     self.away.form_string(),
        }


# =============================================================================
# CONSTRUCTOR DE REPORTE
# =============================================================================

def clean_text(text: str) -> str:
    """
    Elimina Markdown y caracteres especiales.
    Reemplaza *, _, `, ~, #, > por espacios o texto limpio.
    Garantiza que el string sea texto plano puro antes del envío.
    """
    replacements = [
        ("***", ""),
        ("**",  ""),
        ("*",   ""),
        ("___", ""),
        ("__",  ""),
        ("_",   " "),
        ("`",   ""),
        ("~~~", ""),
        ("~~",  ""),
        ("###", ""),
        ("##",  ""),
        ("#",   ""),
        (">",   ""),
        ("[",   ""),
        ("]",   ""),
        ("(",   ""),
        (")",   ""),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    # Colapsar espacios múltiples
    import re
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_match_block(result: dict, idx: int) -> str:
    """
    Construye el bloque de texto plano para un partido.
    """
    lines = []
    sep = "=" * 38

    lines.append(sep)
    lines.append(f"  PARTIDO {idx}  |  {result['home_team']} vs {result['away_team']}")
    lines.append(sep)

    lines.append(f"Forma reciente:")
    lines.append(f"  {result['home_team']}: {result['home_form']}")
    lines.append(f"  {result['away_team']}: {result['away_form']}")

    lh = result["lambda_home"]
    la = result["lambda_away"]
    lines.append(f"Goles esperados: {result['home_team']} {lh:.2f}  |  {result['away_team']} {la:.2f}")

    p = result["probs_1x2"]
    lines.append(
        f"Probabilidades 1X2:"
        f" 1={p['home']*100:.1f}%  X={p['draw']*100:.1f}%  2={p['away']*100:.1f}%"
    )

    if result["market_probs"]:
        mp = result["market_probs"]
        lines.append(
            f"Casa de apuestas:  "
            f"1={mp['home']*100:.1f}%  X={mp['draw']*100:.1f}%  2={mp['away']*100:.1f}%"
        )
        e = result["edge_1x2"]
        lines.append(
            f"Edge (pp):         "
            f"1={e['home']:+.1f}  X={e['draw']:+.1f}  2={e['away']:+.1f}"
        )

    ou = result["probs_ou"]
    lines.append(
        f"Over/Under {ou['line']}: Over={ou['over']*100:.1f}%  Under={ou['under']*100:.1f}%"
    )

    picks = result["value_picks"]
    if picks:
        lines.append("")
        lines.append("  >> PICKS DE VALOR <<")
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


def build_report(league_name: str, match_group: str, matches_results: list[dict]) -> str:
    """
    Construye el reporte completo de texto plano para una liga/grupo.
    """
    today = date.today().strftime("%d/%m/%Y")
    header_lines = [
        "",
        "=" * 38,
        f"  SOCCER BOT - ANALISIS ESTADISTICO",
        f"  Liga / Grupo: {league_name.upper()}",
        f"  Partidos del dia: {today}",
        "=" * 38,
        "",
    ]
    header = "\n".join(header_lines)

    body_parts = []
    for idx, result in enumerate(matches_results, start=1):
        body_parts.append(build_match_block(result, idx))

    footer_lines = [
        "=" * 38,
        "  Modelo: Poisson + H2H | Edge min: "
        f"{CONFIG['EDGE_THRESHOLD_PP']}pp",
        "  Bot generado automaticamente",
        "=" * 38,
        "",
    ]
    footer = "\n".join(footer_lines)

    full_report = header + "\n".join(body_parts) + footer
    return clean_text(full_report)


# =============================================================================
# ENVÍO TELEGRAM
# =============================================================================

def send_telegram(channel_key: str, text: str) -> dict:
    """
    Envía texto plano a un canal de Telegram.
    Usa parse_mode=None para garantizar que no se procese Markdown.
    Divide el mensaje si supera el límite de 4096 caracteres.
    """
    token      = CONFIG["TELEGRAM_BOT_TOKEN"]
    chat_id    = CONFIG["CHANNELS"].get(channel_key) or CONFIG["CHANNELS"]["general"]
    base_url   = f"https://api.telegram.org/bot{token}/sendMessage"
    max_length = 4096
    responses  = []

    # Dividir en fragmentos si es necesario
    chunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]

    for chunk in chunks:
        payload = {
            "chat_id":    chat_id,
            "text":       chunk,
            # Sin parse_mode → Telegram no interpreta Markdown ni HTML
        }
        try:
            resp = requests.post(base_url, json=payload, timeout=15)
            resp.raise_for_status()
            responses.append({"status": "ok", "chunk_len": len(chunk)})
            print(f"[Telegram] Enviado a '{channel_key}' ({len(chunk)} chars)")
        except requests.exceptions.RequestException as e:
            responses.append({"status": "error", "error": str(e)})
            print(f"[Telegram] ERROR al enviar a '{channel_key}': {e}")

    return {"channel": channel_key, "chunks_sent": len(responses), "detail": responses}


# =============================================================================
# DATOS DE EJEMPLO — Mundial 2026 + Ligas
# =============================================================================
#
#  INSTRUCCIONES:
#  Sustituye estos datos con los resultados reales de tu fuente de datos
#  (API-Football, football-data.org, Sofascore, etc.)
#  La estructura debe coincidir con la que usan TeamStats y MatchAnalyzer.
#
# =============================================================================

SAMPLE_DATA = {
    "mundial_2026": {
        "league_avg_goals": 1.25,
        "channel_key": "mundial_2026",
        "groups": {
            "Grupo A": [
                {
                    "home": {
                        "name": "Mexico",
                        "recent_matches": [
                            {"scored": 2, "conceded": 0, "home": True},
                            {"scored": 1, "conceded": 1, "home": False},
                            {"scored": 3, "conceded": 1, "home": True},
                            {"scored": 0, "conceded": 2, "home": False},
                            {"scored": 2, "conceded": 1, "home": True},
                            {"scored": 1, "conceded": 0, "home": False},
                            {"scored": 2, "conceded": 2, "home": True},
                        ],
                    },
                    "away": {
                        "name": "Polonia",
                        "recent_matches": [
                            {"scored": 1, "conceded": 1, "home": True},
                            {"scored": 2, "conceded": 0, "home": False},
                            {"scored": 0, "conceded": 1, "home": True},
                            {"scored": 1, "conceded": 2, "home": False},
                            {"scored": 2, "conceded": 1, "home": True},
                            {"scored": 1, "conceded": 1, "home": False},
                            {"scored": 0, "conceded": 0, "home": True},
                        ],
                    },
                    "odds":    {"home": 2.45, "draw": 3.20, "away": 2.90},
                    "h2h": [
                        {"home_goals": 1, "away_goals": 1},
                        {"home_goals": 0, "away_goals": 1},
                        {"home_goals": 2, "away_goals": 0},
                    ],
                },
            ],
            "Grupo B": [
                {
                    "home": {
                        "name": "USA",
                        "recent_matches": [
                            {"scored": 3, "conceded": 1, "home": True},
                            {"scored": 1, "conceded": 1, "home": False},
                            {"scored": 2, "conceded": 0, "home": True},
                            {"scored": 1, "conceded": 3, "home": False},
                            {"scored": 2, "conceded": 2, "home": True},
                            {"scored": 0, "conceded": 1, "home": False},
                            {"scored": 1, "conceded": 0, "home": True},
                        ],
                    },
                    "away": {
                        "name": "Inglaterra",
                        "recent_matches": [
                            {"scored": 2, "conceded": 0, "home": True},
                            {"scored": 3, "conceded": 1, "home": False},
                            {"scored": 1, "conceded": 0, "home": True},
                            {"scored": 2, "conceded": 1, "home": False},
                            {"scored": 1, "conceded": 1, "home": True},
                            {"scored": 3, "conceded": 0, "home": False},
                            {"scored": 2, "conceded": 1, "home": True},
                        ],
                    },
                    "odds":    {"home": 4.10, "draw": 3.50, "away": 1.75},
                    "h2h": [
                        {"home_goals": 0, "away_goals": 2},
                        {"home_goals": 0, "away_goals": 1},
                    ],
                },
            ],
        },
    },

    "premier_league": {
        "league_avg_goals": 1.42,
        "channel_key": "premier_league",
        "groups": {
            "Premier League": [
                {
                    "home": {
                        "name": "Arsenal",
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
                        "name": "Chelsea",
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
                    "odds":    {"home": 2.00, "draw": 3.60, "away": 3.75},
                    "h2h": [
                        {"home_goals": 2, "away_goals": 2},
                        {"home_goals": 1, "away_goals": 0},
                        {"home_goals": 3, "away_goals": 1},
                    ],
                },
            ],
        },
    },

    "la_liga": {
        "league_avg_goals": 1.38,
        "channel_key": "la_liga",
        "groups": {
            "La Liga": [
                {
                    "home": {
                        "name": "Real Madrid",
                        "recent_matches": [
                            {"scored": 3, "conceded": 1, "home": True},
                            {"scored": 2, "conceded": 0, "home": False},
                            {"scored": 4, "conceded": 1, "home": True},
                            {"scored": 1, "conceded": 1, "home": False},
                            {"scored": 2, "conceded": 0, "home": True},
                            {"scored": 3, "conceded": 2, "home": False},
                            {"scored": 2, "conceded": 1, "home": True},
                        ],
                    },
                    "away": {
                        "name": "Barcelona",
                        "recent_matches": [
                            {"scored": 2, "conceded": 1, "home": True},
                            {"scored": 3, "conceded": 0, "home": False},
                            {"scored": 1, "conceded": 1, "home": True},
                            {"scored": 4, "conceded": 2, "home": False},
                            {"scored": 2, "conceded": 1, "home": True},
                            {"scored": 1, "conceded": 0, "home": False},
                            {"scored": 3, "conceded": 1, "home": True},
                        ],
                    },
                    "odds":    {"home": 2.10, "draw": 3.40, "away": 3.20},
                    "h2h": [
                        {"home_goals": 3, "away_goals": 1},
                        {"home_goals": 0, "away_goals": 4},
                        {"home_goals": 2, "away_goals": 2},
                        {"home_goals": 1, "away_goals": 2},
                    ],
                },
            ],
        },
    },

    "liga_mx": {
        "league_avg_goals": 1.30,
        "channel_key": "liga_mx",
        "groups": {
            "Liga MX": [
                {
                    "home": {
                        "name": "Chivas",
                        "recent_matches": [
                            {"scored": 2, "conceded": 1, "home": True},
                            {"scored": 0, "conceded": 0, "home": False},
                            {"scored": 1, "conceded": 1, "home": True},
                            {"scored": 2, "conceded": 2, "home": False},
                            {"scored": 1, "conceded": 0, "home": True},
                            {"scored": 0, "conceded": 1, "home": False},
                            {"scored": 3, "conceded": 1, "home": True},
                        ],
                    },
                    "away": {
                        "name": "America",
                        "recent_matches": [
                            {"scored": 2, "conceded": 0, "home": True},
                            {"scored": 1, "conceded": 1, "home": False},
                            {"scored": 3, "conceded": 1, "home": True},
                            {"scored": 0, "conceded": 2, "home": False},
                            {"scored": 2, "conceded": 0, "home": True},
                            {"scored": 1, "conceded": 1, "home": False},
                            {"scored": 2, "conceded": 2, "home": True},
                        ],
                    },
                    "odds":    {"home": 2.80, "draw": 3.10, "away": 2.50},
                    "h2h": [
                        {"home_goals": 1, "away_goals": 2},
                        {"home_goals": 2, "away_goals": 1},
                        {"home_goals": 0, "away_goals": 1},
                    ],
                },
            ],
        },
    },
}


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def run_analysis(data: dict = SAMPLE_DATA, send: bool = True) -> list[dict]:
    """
    Itera sobre todas las ligas y grupos, analiza cada partido,
    construye el reporte y (opcionalmente) lo envía por Telegram.
    """
    all_results = []

    for league_key, league_data in data.items():
        league_avg  = league_data.get("league_avg_goals", 1.35)
        channel_key = league_data.get("channel_key", "general")
        groups      = league_data.get("groups", {})

        league_matches_results = []

        for group_name, matches in groups.items():
            print(f"\n[Analizando] {league_key.upper()} / {group_name}")

            for match in matches:
                home_stats = TeamStats(
                    name            = match["home"]["name"],
                    recent_matches  = match["home"]["recent_matches"],
                    league_avg_goals= league_avg,
                )
                away_stats = TeamStats(
                    name            = match["away"]["name"],
                    recent_matches  = match["away"]["recent_matches"],
                    league_avg_goals= league_avg,
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
                league_matches_results.append(result)

                # Imprimir resumen en consola
                picks_str = " | ".join(
                    f"{pk['pick']} ({pk['model_prob']*100:.0f}%)"
                    for pk in result["value_picks"]
                ) or "Sin edge"

                print(
                    f"  {result['home_team']} vs {result['away_team']}"
                    f" — Lambdas: {result['lambda_home']:.2f}/{result['lambda_away']:.2f}"
                    f" — Picks: {picks_str}"
                )

        # Construir reporte unificado para la liga
        report_text = build_report(
            league_name    = league_key.replace("_", " ").title(),
            match_group    = "Todos los grupos",
            matches_results= league_matches_results,
        )

        print(f"\n{'='*50}")
        print(f"REPORTE FINAL — {league_key.upper()}")
        print(f"{'='*50}")
        print(report_text)

        if send:
            send_result = send_telegram(channel_key, report_text)
            print(f"[Envio] {send_result}")

        all_results.append({
            "league":        league_key,
            "channel":       channel_key,
            "matches_count": len(league_matches_results),
            "results":       league_matches_results,
        })

    return all_results


# =============================================================================
# FUNCIÓN DE PRUEBA SIN ENVÍO
# =============================================================================

def dry_run():
    """
    Ejecuta el análisis completo sin enviar nada a Telegram.
    Útil para verificar el modelo y el formato del reporte en Colab.
    """
    print("=" * 50)
    print("  MODO DRY RUN — Sin envio a Telegram")
    print("=" * 50)
    results = run_analysis(data=SAMPLE_DATA, send=False)
    print(f"\nTotal de ligas analizadas: {len(results)}")
    for r in results:
        picks_with_value = sum(
            len(m["value_picks"]) for m in r["results"]
        )
        print(f"  {r['league']}: {r['matches_count']} partidos, {picks_with_value} picks de valor")
    return results


# =============================================================================
# FUNCIÓN DE PRODUCCIÓN CON ENVÍO REAL
# =============================================================================

def production_run():
    """
    Ejecuta el análisis completo Y envía los reportes a Telegram.
    Asegurarse de que CONFIG tenga las credenciales correctas.
    """
    print("=" * 50)
    print("  MODO PRODUCCION — Enviando a Telegram")
    print("=" * 50)
    return run_analysis(data=SAMPLE_DATA, send=True)


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    # ──────────────────────────────────────────────
    # CAMBIA dry_run() por production_run()
    # cuando tengas las credenciales configuradas.
    # ──────────────────────────────────────────────
    dry_run()
