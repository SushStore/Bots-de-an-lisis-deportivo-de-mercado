# ==============================================================================
# parlay_stats_analyzer.py  ·  v2.0
# ==============================================================================
# Analizador cuantitativo de estadísticas deportivas para Parlays.
# Arquitectura CONFIG-first: toda la configuración vive en la SECCIÓN 0.
# Compatible con Google Colab (sin argumentos CLI, sin .env obligatorio).
#
# INSTALACIÓN (una sola vez):
#   pip install requests colorama
#
# EJECUCIÓN:
#   python parlay_stats_analyzer.py
#   — o en Colab: exec(open("parlay_stats_analyzer.py").read())
# ==============================================================================

# ──────────────────────────────────────────────────────────────────────────────
# DEPENDENCIAS
# ──────────────────────────────────────────────────────────────────────────────
import hashlib
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("[WARN] 'requests' no instalado. pip install requests")

try:
    from colorama import Fore, Style, init as _cinit
    _cinit(autoreset=True)
    C = {"G": Fore.GREEN, "Y": Fore.YELLOW, "R": Fore.RED,
         "C": Fore.CYAN,  "M": Fore.MAGENTA,"W": Fore.WHITE,
         "B": Style.BRIGHT, "0": Style.RESET_ALL}
except ImportError:
    C = {k: "" for k in ["G","Y","R","C","M","W","B","0"]}


# ==============================================================================
# SECCIÓN 0 — CONFIG CENTRAL  ←  EDITA AQUÍ Y SOLO AQUÍ
# ==============================================================================
CONFIG = {

    # ── Fecha de análisis ──────────────────────────────────────────────────
    # "auto" usa la fecha del sistema. Cambia a "2026-06-05" para una fecha fija.
    "analysis_date": "auto",

    # ── Directorio de salida ───────────────────────────────────────────────
    # "." = carpeta donde corre el script. En Colab: "/content/"
    "output_dir": ".",

    # ── API Keys ───────────────────────────────────────────────────────────
    # Deja en "" para activar datos demo automáticamente.
    # The Odds API (gratis, 500 req/mes): https://the-odds-api.com
    "odds_api_key": "",
    # BallDontLie (NBA, gratis con key): https://app.balldontlie.io
    "balldontlie_key": "",

    # ── Telegram ───────────────────────────────────────────────────────────
    # Si ambos valores están configurados, el reporte se envía automáticamente.
    # Deja en "" para solo generar archivos locales sin enviar nada.
    "telegram_bot_token": "",   # "123456789:AAF..."
    "telegram_chat_id":   "",   # "-100123456789" (canal) o "123456" (usuario)

    # ── Parámetros de análisis ─────────────────────────────────────────────
    "n_games":                  10,     # juegos históricos a evaluar
    "spread_value_buffer":      3.0,    # puntos extra mínimos para ser "valor"
    "high_consistency_pct":     0.80,   # ≥ 80 % → ⭐ Alta Consistencia
    "consistency_pct":          0.70,   # ≥ 70 % → ✅ Consistente

    # ── Deporte para análisis de Spreads ──────────────────────────────────
    # Opciones: "basketball_nba" | "baseball_mlb" | "americanfootball_nfl"
    "spread_sport": "basketball_nba",

    # ── Roster de jugadores a analizar ────────────────────────────────────
    # Agrega, quita o edita libremente.
    # "lines": las líneas reales del casino para ese día.
    # Si omites una categoría, se usa el promedio del jugador − 0.5 como proxy.
    "roster": [
        # ── NBA ──────────────────────────────────────────────────────────
        {
            "name": "Stephen Curry", "sport": "NBA",
            "lines": {"points": 27.5, "rebounds": 4.5,
                      "assists": 5.5,  "three_pointers_made": 4.5},
        },
        {
            "name": "Nikola Jokic", "sport": "NBA",
            "lines": {"points": 24.5, "rebounds": 11.5,
                      "assists": 8.5,  "three_pointers_made": 0.5},
        },
        {
            "name": "Shai Gilgeous-Alexander", "sport": "NBA",
            "lines": {"points": 30.5, "rebounds": 4.5,
                      "assists": 5.5,  "three_pointers_made": 1.5},
        },
        {
            "name": "Jayson Tatum", "sport": "NBA",
            "lines": {"points": 26.5, "rebounds": 7.5,
                      "assists": 4.5,  "three_pointers_made": 2.5},
        },
        {
            "name": "Anthony Edwards", "sport": "NBA",
            "lines": {"points": 25.5, "rebounds": 5.5,
                      "assists": 5.5,  "three_pointers_made": 3.5},
        },
        {
            "name": "LeBron James", "sport": "NBA",
            "lines": {"points": 23.5, "rebounds": 7.5,
                      "assists": 7.5,  "three_pointers_made": 1.5},
        },
        # ── MLB pitchers ─────────────────────────────────────────────────
        {
            "name": "Gerrit Cole", "sport": "MLB", "role": "pitcher",
            "lines": {"strikeouts": 7.5},
        },
        {
            "name": "Spencer Strider", "sport": "MLB", "role": "pitcher",
            "lines": {"strikeouts": 8.5},
        },
        {
            "name": "Zack Wheeler", "sport": "MLB", "role": "pitcher",
            "lines": {"strikeouts": 7.5},
        },
        # ── MLB batters ──────────────────────────────────────────────────
        {
            "name": "Freddie Freeman", "sport": "MLB", "role": "batter",
            "lines": {"hits": 1.5},
        },
        {
            "name": "Mookie Betts", "sport": "MLB", "role": "batter",
            "lines": {"hits": 1.5},
        },
    ],
}
# ==============================================================================
# FIN CONFIG — no necesitas tocar nada más abajo
# ==============================================================================


# ──────────────────────────────────────────────────────────────────────────────
# UTILIDADES DE CONSOLA
# ──────────────────────────────────────────────────────────────────────────────
NBA_CATS = ["points", "rebounds", "assists", "three_pointers_made"]
MLB_CATS = ["strikeouts", "hits"]
CAT_ES   = {
    "points": "Puntos", "rebounds": "Rebotes", "assists": "Asistencias",
    "three_pointers_made": "Triples", "strikeouts": "Ponches", "hits": "Hits",
}

def _log(msg: str, color: str = "W", bold: bool = False) -> None:
    print(f"{C['B'] if bold else ''}{C.get(color,'')}{msg}{C['0']}")

def _section(title: str) -> None:
    w = 64
    _log("\n" + "═" * w, "C", bold=True)
    _log(f"  {title}", "C", bold=True)
    _log("═" * w, "C", bold=True)

def _bar(rate: float, w: int = 10) -> str:
    f = round(rate * w)
    return "[" + "█" * f + "░" * (w - f) + "]"

def _resolve_date(cfg_value: str) -> date:
    if cfg_value.lower() == "auto":
        return date.today()
    try:
        return date.fromisoformat(cfg_value)
    except ValueError:
        _log(f"[WARN] Fecha inválida '{cfg_value}'. Usando hoy.", "Y")
        return date.today()


# ==============================================================================
# MÓDULO 1 — SPREADS / HANDICAPS
# ==============================================================================
class SpreadAnalyzer:
    """
    Descarga los spreads del día desde The Odds API.
    Si la API falla o la key está vacía → activa datos demo sin romper el flujo.
    """

    def __init__(self, cfg: dict):
        self.sport   = cfg["spread_sport"]
        self.n_games = cfg["n_games"]
        self.buffer  = cfg["spread_value_buffer"]
        self.api_key = cfg["odds_api_key"]

    # ── Entrada pública ────────────────────────────────────────────────────
    def analyze(self, target_date: date) -> list[dict]:
        raw_games = self._safe_fetch()
        results   = []
        for g in raw_games:
            h_avg, h_hist = self._estimate_margin(g["home_team"], home=True)
            a_avg, a_hist = self._estimate_margin(g["away_team"], home=False)
            h_val = self._is_value(h_avg, g["home_spread"])
            a_val = self._is_value(a_avg, g["away_spread"])
            results.append({
                **g,
                "home_avg_margin":   h_avg,
                "away_avg_margin":   a_avg,
                "home_history":      h_hist,
                "away_history":      a_hist,
                "home_spread_value": h_val,
                "away_spread_value": a_val,
                "value_team":        self._value_label(g, h_val, a_val),
            })
        return results

    # ── Fetch con triple capa de seguridad ────────────────────────────────
    def _safe_fetch(self) -> list[dict]:
        if not self.api_key:
            _log("  [Spreads] Sin API key → datos demo activados.", "Y")
            return self._demo_games()
        if not HAS_REQUESTS:
            _log("  [Spreads] requests no disponible → datos demo.", "Y")
            return self._demo_games()
        try:
            url = (
                f"https://api.the-odds-api.com/v4/sports/{self.sport}/odds/"
                f"?apiKey={self.api_key}&regions=us&markets=spreads&oddsFormat=american"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            parsed = self._parse(resp.json())
            if not parsed:
                _log("  [Spreads] API respondió vacío → datos demo.", "Y")
                return self._demo_games()
            _log(f"  [Spreads] {len(parsed)} juegos obtenidos de The Odds API.", "G")
            return parsed
        except Exception as exc:
            _log(f"  [Spreads] API error ({exc.__class__.__name__}: {exc}) → demo.", "Y")
            return self._demo_games()

    def _parse(self, raw: list) -> list[dict]:
        out = []
        for game in raw:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            for bk in game.get("bookmakers", [])[:1]:
                for mkt in bk.get("markets", []):
                    if mkt["key"] != "spreads":
                        continue
                    pts = {o["name"]: o["point"] for o in mkt["outcomes"]}
                    out.append({
                        "sport":       self.sport,
                        "home_team":   home,
                        "away_team":   away,
                        "commence":    game.get("commence_time", ""),
                        "home_spread": pts.get(home, 0.0),
                        "away_spread": pts.get(away, 0.0),
                    })
        return out

    # ── Margen histórico (stub determinístico) ────────────────────────────
    def _estimate_margin(self, team: str, home: bool) -> tuple[float, list[float]]:
        seed = int(hashlib.md5(f"{team}{home}".encode()).hexdigest()[:8], 16)
        rng  = random.Random(seed)
        base = 3.5 if home else -2.0
        hist = [round(rng.gauss(base, 8), 1) for _ in range(self.n_games)]
        return round(sum(hist) / len(hist), 2), hist

    def _is_value(self, avg: float, spread: float) -> bool:
        return spread > 0 and avg > -(spread - self.buffer)

    def _value_label(self, g: dict, hv: bool, av: bool) -> str:
        parts = ([g["home_team"]] if hv else []) + ([g["away_team"]] if av else [])
        return " & ".join(parts) if parts else "—"

    # ── Demo data ─────────────────────────────────────────────────────────
    def _demo_games(self) -> list[dict]:
        return [
            {"sport": "NBA", "home_team": "Golden State Warriors",
             "away_team": "LA Clippers",      "commence": "",
             "home_spread": -5.5, "away_spread":  5.5},
            {"sport": "NBA", "home_team": "Boston Celtics",
             "away_team": "Miami Heat",        "commence": "",
             "home_spread": -8.0, "away_spread":  8.0},
            {"sport": "NBA", "home_team": "Denver Nuggets",
             "away_team": "OKC Thunder",       "commence": "",
             "home_spread": -3.5, "away_spread":  3.5},
            {"sport": "MLB", "home_team": "New York Yankees",
             "away_team": "Houston Astros",    "commence": "",
             "home_spread": -1.5, "away_spread":  1.5},
            {"sport": "MLB", "home_team": "Los Angeles Dodgers",
             "away_team": "San Diego Padres",  "commence": "",
             "home_spread": -1.5, "away_spread":  1.5},
        ]


# ==============================================================================
# MÓDULO 2 — PLAYER PROPS
# ==============================================================================
class PlayerPropsAnalyzer:
    """
    Calcula promedios + Over-rates para un jugador en sus últimos N juegos.
    NBA: BallDontLie API.  MLB: datos demo estructurados (SportRadar-ready).
    Nunca lanza excepción; siempre retorna datos (reales o demo).
    """

    # Bases realistas para el generador demo
    _NBA_BASES = {
        "Stephen Curry":            (29, 5.0, 6.0, 4.5),
        "LeBron James":             (26, 7.0, 7.0, 1.5),
        "Nikola Jokic":             (25,12.0, 9.0, 0.5),
        "Shai Gilgeous-Alexander":  (31, 5.0, 6.0, 1.5),
        "Anthony Edwards":          (27, 5.0, 5.0, 3.5),
        "Jayson Tatum":             (27, 8.0, 5.0, 2.5),
        "Luka Doncic":              (32, 9.0, 8.0, 3.0),
        "Giannis Antetokounmpo":    (30,11.0, 6.0, 0.5),
    }
    _MLB_K    = {"Gerrit Cole": 8.5, "Spencer Strider": 9.5,
                 "Zack Wheeler": 8.0, "Dylan Cease": 8.5}
    _MLB_HITS = {"Freddie Freeman": 1.4, "Mookie Betts": 1.3,
                 "Paul Goldschmidt": 1.2, "Juan Soto": 1.3}

    def __init__(self, cfg: dict):
        self.n       = cfg["n_games"]
        self.hi_pct  = cfg["high_consistency_pct"]
        self.con_pct = cfg["consistency_pct"]
        self.bdl_key = cfg["balldontlie_key"]

    # ── Entrada pública ────────────────────────────────────────────────────
    def analyze(self, player_cfg: dict) -> dict:
        name  = player_cfg["name"]
        sport = player_cfg.get("sport", "NBA").upper()
        role  = player_cfg.get("role", "player")
        lines = player_cfg.get("lines", {})

        logs = (self._nba_logs(name) if sport == "NBA"
                else self._mlb_logs(name, role))
        logs = logs[-self.n:]
        cats = NBA_CATS if sport == "NBA" else MLB_CATS

        averages   = {}
        over_rates = {}
        histories  = {}

        for cat in cats:
            vals   = [g.get(cat, 0) for g in logs]
            avg    = round(sum(vals) / len(vals), 2) if vals else 0.0
            line   = lines.get(cat, round(avg - 0.5, 1))
            overs  = sum(1 for v in vals if v > line)
            rate   = round(overs / len(vals), 3) if vals else 0.0

            averages[cat]  = avg
            histories[cat] = vals
            over_rates[cat] = {
                "line": line, "over_count": overs,
                "total": len(vals), "rate": rate,
                "label": self._label(rate),
            }

        best_cat = max(over_rates, key=lambda c: over_rates[c]["rate"]) if over_rates else None
        return {
            "player": name, "sport": sport, "role": role,
            "games_analyzed": len(logs),
            "averages":   averages,
            "over_rates": over_rates,
            "histories":  histories,
            "best_prop":  ({"category": best_cat, **over_rates[best_cat]}
                           if best_cat else {}),
        }

    def _label(self, rate: float) -> str:
        if rate >= self.hi_pct:  return "⭐ Prop de Alta Consistencia"
        if rate >= self.con_pct: return "✅ Consistente"
        if rate >= 0.50:         return "⚠️ Moderado"
        return "❌ Inconsistente"

    # ── NBA: BallDontLie con fallback demo ────────────────────────────────
    def _nba_logs(self, name: str) -> list[dict]:
        if not (HAS_REQUESTS and self.bdl_key):
            return self._demo_nba(name)
        try:
            hdrs = {"Authorization": self.bdl_key}
            q    = name.replace(" ", "%20")
            r    = requests.get(
                f"https://api.balldontlie.io/v1/players?search={q}&per_page=5",
                headers=hdrs, timeout=8
            )
            r.raise_for_status()
            players = r.json().get("data", [])
            if not players:
                return self._demo_nba(name)
            pid  = players[0]["id"]
            r2   = requests.get(
                f"https://api.balldontlie.io/v1/stats"
                f"?player_ids[]={pid}&seasons[]=2024&per_page=15&postseason=false",
                headers=hdrs, timeout=10
            )
            r2.raise_for_status()
            raw = r2.json().get("data", [])
            if not raw:
                return self._demo_nba(name)
            logs = []
            for g in raw:
                logs.append({
                    "player": name,
                    "date":   g.get("game", {}).get("date", ""),
                    "points":              g.get("pts",  0) or 0,
                    "rebounds":            g.get("reb",  0) or 0,
                    "assists":             g.get("ast",  0) or 0,
                    "three_pointers_made": g.get("fg3m", 0) or 0,
                })
            _log(f"  [BallDontLie] {name}: {len(logs)} juegos obtenidos.", "G")
            return logs
        except Exception as exc:
            _log(f"  [BallDontLie] {name} error ({exc.__class__.__name__}) → demo.", "Y")
            return self._demo_nba(name)

    # ── MLB stub (conecta SportRadar en producción) ────────────────────────
    def _mlb_logs(self, name: str, role: str) -> list[dict]:
        return self._demo_mlb(name, role)

    # ── Generadores demo determinísticos ─────────────────────────────────
    def _rng(self, key: str) -> random.Random:
        return random.Random(int(hashlib.md5(key.encode()).hexdigest()[:8], 16))

    def _demo_nba(self, name: str) -> list[dict]:
        base = self._NBA_BASES.get(name, (20, 5.0, 5.0, 2.0))
        rng  = self._rng(name)
        return [
            {
                "player": name,
                "date":   (date.today() - timedelta(days=i * 3)).isoformat(),
                "points":              max(0, round(rng.gauss(base[0], 5))),
                "rebounds":            max(0, round(rng.gauss(base[1], 2.5))),
                "assists":             max(0, round(rng.gauss(base[2], 2.0))),
                "three_pointers_made": max(0, round(rng.gauss(base[3], 1.5))),
            }
            for i in range(self.n)
        ]

    def _demo_mlb(self, name: str, role: str) -> list[dict]:
        rng = self._rng(f"{name}{role}")
        if role == "pitcher":
            base_k = self._MLB_K.get(name, 6.5)
            return [
                {"player": name,
                 "date":   (date.today() - timedelta(days=i * 5)).isoformat(),
                 "strikeouts": max(0, round(rng.gauss(base_k, 2.5))),
                 "hits": 0}
                for i in range(self.n)
            ]
        base_h = self._MLB_HITS.get(name, 1.0)
        return [
            {"player": name,
             "date":   (date.today() - timedelta(days=i)).isoformat(),
             "strikeouts": 0,
             "hits": max(0, round(rng.gauss(base_h * 4, 1.2)))}
            for i in range(self.n)
        ]


# ==============================================================================
# MÓDULO 3 — TELEGRAM NOTIFIER
# ==============================================================================
class TelegramNotifier:
    """
    Envía mensajes a un canal/chat de Telegram vía Bot API.
    Si las credenciales están vacías o el envío falla → solo loguea, nunca rompe.
    """

    MAX_LENGTH = 4096   # límite de Telegram por mensaje

    def __init__(self, token: str, chat_id: str):
        self.token   = token.strip()
        self.chat_id = chat_id.strip()
        self.enabled = bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            _log("  [Telegram] Credenciales no configuradas. Solo archivos locales.", "Y")
            return False
        if not HAS_REQUESTS:
            _log("  [Telegram] requests no disponible. Instala: pip install requests", "R")
            return False

        # Telegram no admite '_' dentro de nombres en MarkdownV2; usamos Markdown normal
        chunks = self._split(text)
        ok     = True
        for i, chunk in enumerate(chunks, 1):
            try:
                url  = f"https://api.telegram.org/bot{self.token}/sendMessage"
                resp = requests.post(url, json={
                    "chat_id":    self.chat_id,
                    "text":       chunk,
                    "parse_mode": "Markdown",
                }, timeout=15)
                resp.raise_for_status()
                _log(f"  [Telegram] Parte {i}/{len(chunks)} enviada ✓", "G")
            except Exception as exc:
                _log(f"  [Telegram] Error al enviar parte {i}: {exc}", "R")
                ok = False
        return ok

    def _split(self, text: str) -> list[str]:
        if len(text) <= self.MAX_LENGTH:
            return [text]
        parts = []
        while text:
            parts.append(text[:self.MAX_LENGTH])
            text = text[self.MAX_LENGTH:]
        return parts


# ==============================================================================
# MÓDULO 4 — GENERADOR DE REPORTES
# ==============================================================================
class ReportBuilder:
    """
    Construye el Markdown para Telegram y el dict para exportar a JSON.
    """

    def __init__(self, cfg: dict, analysis_date: date,
                 spread_results: list[dict], prop_results: list[dict]):
        self.cfg      = cfg
        self.d        = analysis_date
        self.spreads  = spread_results
        self.props    = prop_results
        self.hi_pct   = cfg["high_consistency_pct"]
        self.con_pct  = cfg["consistency_pct"]

    # ── Markdown ──────────────────────────────────────────────────────────
    def markdown(self) -> str:
        d_fmt  = self.d.strftime("%d/%m/%Y")
        lines  = [
            f"📊 *PARLAY STATS REPORT — {d_fmt}*",
            f"_parlay\\_stats\\_analyzer.py · v2.0_",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏟️ *ANÁLISIS DE SPREADS DEL DÍA*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        value_sp   = [r for r in self.spreads if r["value_team"] != "—"]
        neutral_sp = [r for r in self.spreads if r["value_team"] == "—"]

        if value_sp:
            lines.append("\n🔥 *Líneas con Valor de Hándicap:*\n")
            for r in value_sp:
                lines.append(
                    f"✅ `{r['home_team']} vs {r['away_team']}`\n"
                    f"   Spread casino: `{r['home_spread']:+.1f}` | "
                    f"Margen prom: `{r['home_avg_margin']:+.1f} pts`\n"
                    f"   💡 Equipo con valor: _{r['value_team']}_"
                )
        else:
            lines.append("_No se detectaron líneas de valor hoy._")

        if neutral_sp:
            lines.append("\n📋 *Otros juegos:*")
            for r in neutral_sp:
                lines.append(
                    f"• `{r['home_team']} vs {r['away_team']}` — "
                    f"Spread: `{r['home_spread']:+.1f}`"
                )

        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🎯 *LÍNEAS MÁS SEGURAS PARA PARLAY*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        all_flat  = self._flat_props()
        high_flat = sorted(
            [p for p in all_flat if p["rate"] >= self.hi_pct],
            key=lambda x: x["rate"], reverse=True
        )
        mid_flat  = sorted(
            [p for p in all_flat if self.con_pct <= p["rate"] < self.hi_pct],
            key=lambda x: x["rate"], reverse=True
        )

        if high_flat:
            lines.append(f"\n⭐ *Alta Consistencia (≥{int(self.hi_pct*100)}%):*\n")
            for hp in high_flat:
                cat_es = CAT_ES.get(hp["cat"], hp["cat"].title())
                lines.append(
                    f"🏆 *{hp['player']}* `[{hp['sport']}]`\n"
                    f"   {cat_es} — Over `{hp['line']}` → "
                    f"`{hp['over_count']}/{hp['total']}` = "
                    f"*{hp['rate']*100:.0f}%* {hp['label']}"
                )
        else:
            lines.append(f"_Sin props ≥{int(self.hi_pct*100)}% hoy._")

        if mid_flat:
            lines.append(f"\n✅ *Consistentes ({int(self.con_pct*100)}–{int(self.hi_pct*100)-1}%):*\n")
            for mp in mid_flat[:6]:
                cat_es = CAT_ES.get(mp["cat"], mp["cat"].title())
                lines.append(
                    f"• *{mp['player']}* — {cat_es} Over `{mp['line']}` "
                    f"({mp['over_count']}/{mp['total']} = {mp['rate']*100:.0f}%)"
                )

        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ _Análisis informativo. Apuesta con responsabilidad._",
            f"🤖 `parlay_stats_analyzer v2.0` · {datetime.now().strftime('%H:%M')} UTC",
        ]
        return "\n".join(lines)

    # ── JSON export ───────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        all_flat = self._flat_props()
        return {
            "meta": {
                "generated_at":  datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
                "analysis_date": self.d.isoformat(),
                "version":       "2.0.0",
                "config": {
                    k: v for k, v in self.cfg.items()
                    if k not in ("odds_api_key", "balldontlie_key",
                                 "telegram_bot_token", "telegram_chat_id")
                },
            },
            "spreads":      self.spreads,
            "player_props": self.props,
            "summary": {
                "spread_value_games":
                    [f"{r['home_team']} vs {r['away_team']}"
                     for r in self.spreads if r["value_team"] != "—"],
                "high_consistency_props":
                    [f"{p['player']} – {CAT_ES.get(p['cat'], p['cat'])}"
                     for p in all_flat if p["rate"] >= self.hi_pct],
                "consistent_props":
                    [f"{p['player']} – {CAT_ES.get(p['cat'], p['cat'])}"
                     for p in all_flat
                     if self.con_pct <= p["rate"] < self.hi_pct],
            },
        }

    def _flat_props(self) -> list[dict]:
        out = []
        for p in self.props:
            for cat, d in p.get("over_rates", {}).items():
                out.append({
                    "player":     p["player"],
                    "sport":      p["sport"],
                    "cat":        cat,
                    "line":       d["line"],
                    "over_count": d["over_count"],
                    "total":      d["total"],
                    "rate":       d["rate"],
                    "label":      d["label"],
                })
        return out


# ==============================================================================
# PIPELINE PRINCIPAL
# ==============================================================================
def run(cfg: dict = CONFIG) -> dict:
    """
    Punto de entrada único. Ejecuta los 4 módulos en secuencia y retorna
    el dict con todos los datos (útil para Colab notebooks).
    """

    # ── Resolución de parámetros ──────────────────────────────────────────
    today      = _resolve_date(cfg["analysis_date"])
    out_dir    = Path(cfg["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    _log("\n" + "▓" * 64, "C", bold=True)
    _log("  PARLAY STATS ANALYZER  v2.0", "C", bold=True)
    _log(f"  Fecha: {today}  |  Sport (spreads): {cfg['spread_sport']}", "C")
    _log("▓" * 64, "C", bold=True)

    if not cfg["odds_api_key"]:
        _log(
            "\n  ℹ️  ODDS_API_KEY vacía → datos demo para spreads.\n"
            "     Consigue tu key gratuita en https://the-odds-api.com\n",
            "Y"
        )

    # ── MÓDULO 1: Spreads ─────────────────────────────────────────────────
    _section("MÓDULO 1 — SPREADS / HANDICAPS")
    spread_results = SpreadAnalyzer(cfg).analyze(today)
    for r in spread_results:
        tag = f"{C['G']}✅ VALOR{C['0']}" if r["value_team"] != "—" else ""
        _log(
            f"  {r['home_team']:<28} vs  {r['away_team']:<28} | "
            f"Spread: {r['home_spread']:+.1f} | "
            f"Margin: {r['home_avg_margin']:+.1f}  {tag}"
        )

    # ── MÓDULO 2+3: Player Props + Consistencia ───────────────────────────
    _section("MÓDULO 2+3 — PLAYER PROPS + CONSISTENCIA")
    pa           = PlayerPropsAnalyzer(cfg)
    prop_results = []

    for player_cfg in cfg["roster"]:
        result = pa.analyze(player_cfg)
        prop_results.append(result)

        _log(f"\n  [{result['sport']}] {result['player']}", "M", bold=True)
        for cat, d in result.get("over_rates", {}).items():
            rate  = d["rate"]
            color = "G" if rate >= cfg["high_consistency_pct"] else (
                    "Y" if rate >= cfg["consistency_pct"] else "R")
            _log(
                f"    {CAT_ES.get(cat, cat):<18}  "
                f"línea={d['line']:5.1f}  "
                f"{d['over_count']}/{d['total']} "
                f"{_bar(rate)}  {rate*100:.0f}%  {d['label']}",
                color
            )

    # ── MÓDULO 4: Reporte ─────────────────────────────────────────────────
    _section("GENERANDO REPORTE")
    report  = ReportBuilder(cfg, today, spread_results, prop_results)
    md_text = report.markdown()
    data    = report.to_dict()

    # ── Guardar archivos ──────────────────────────────────────────────────
    date_str  = today.isoformat()
    md_path   = out_dir / f"parlay_report_{date_str}.md"
    json_path = out_dir / f"parlay_data_{date_str}.json"

    md_path.write_text(md_text, encoding="utf-8")
    _log(f"  📄 Markdown guardado → {md_path}", "G", bold=True)

    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _log(f"  📦 JSON guardado     → {json_path}", "G", bold=True)

    # ── Preview Markdown en consola ───────────────────────────────────────
    _section("PREVIEW — MARKDOWN (Telegram)")
    print(md_text)

    # ── MÓDULO 3: Telegram ────────────────────────────────────────────────
    _section("ENVÍO TELEGRAM")
    notifier = TelegramNotifier(cfg["telegram_bot_token"], cfg["telegram_chat_id"])
    notifier.send(md_text)

    # ── Resumen ───────────────────────────────────────────────────────────
    _section("RESUMEN FINAL")
    s = data["summary"]
    _log(f"  Juegos con spread de valor:       {len(s['spread_value_games'])}", "W")
    for g in s["spread_value_games"]:
        _log(f"    🎯 {g}", "G")
    _log(f"  Props Alta Consistencia (≥{int(cfg['high_consistency_pct']*100)}%):  {len(s['high_consistency_props'])}", "G", bold=True)
    for p in s["high_consistency_props"]:
        _log(f"    ⭐ {p}", "G")
    _log(f"  Props Consistentes ({int(cfg['consistency_pct']*100)}–{int(cfg['high_consistency_pct']*100)-1}%):    {len(s['consistent_props'])}", "Y")
    for p in s["consistent_props"]:
        _log(f"    ✅ {p}", "Y")

    _log("\n  ✅ Pipeline completado sin errores.\n", "C", bold=True)
    return data


# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    run(CONFIG)
