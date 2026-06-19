# ==============================================================================
# parlay_stats_analyzer.py  v3.0 (PRO UNIFICADO)
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
        "G": Fore.GREEN, "Y": Fore.YELLOW, "R": Fore.RED,
        "C": Fore.CYAN, "M": Fore.MAGENTA, "W": Fore.WHITE,
        "B": Style.BRIGHT, "0": Style.RESET_ALL,
    }
except ImportError:
    C = {k: "" for k in ["G", "Y", "R", "C", "M", "W", "B", "0"]}

# ==============================================================================
# SECCION 0 — CONFIG CENTRAL
# ==============================================================================
CONFIG = {
    "analysis_date": "auto",
    "output_dir": ".",
    "SPORTAPI_KEY": "KEY_RAPIDAPI_AQUI",
    "balldontlie_key": "",
    "telegram_bot_token": "TOKEN_TELEGRAM_AQUI",
    "telegram_chat_id": "CHAT_ID_AQUI",
    "n_games": 10,
    "spread_value_buffer": 3.0,
    "high_consistency_pct": 0.80,
    "consistency_pct": 0.70,
    "spread_sports": [
        {"sport_path": "football", "label": "Fútbol", "league_ids": [1239, 17, 8, 23, 35, 34, 352]},
        {"sport_path": "basketball", "label": "NBA", "league_ids": [132]},
        {"sport_path": "baseball", "label": "MLB", "league_ids": [150]},
    ],
    "handicap_market_names": ["Asian Handicap", "Handicap", "Point Spread", "Run Line"],
    "roster": [
        {"name": "Stephen Curry", "sport": "NBA", "lines": {"points": 27.5, "rebounds": 4.5, "assists": 5.5, "three_pointers_made": 4.5}},
        {"name": "Nikola Jokic", "sport": "NBA", "lines": {"points": 24.5, "rebounds": 11.5, "assists": 8.5, "three_pointers_made": 0.5}},
        {"name": "Shai Gilgeous-Alexander", "sport": "NBA", "lines": {"points": 30.5, "rebounds": 4.5, "assists": 5.5, "three_pointers_made": 1.5}},
        {"name": "Jayson Tatum", "sport": "NBA", "lines": {"points": 26.5, "rebounds": 7.5, "assists": 4.5, "three_pointers_made": 2.5}},
        {"name": "Anthony Edwards", "sport": "NBA", "lines": {"points": 25.5, "rebounds": 5.5, "assists": 5.5, "three_pointers_made": 3.5}},
        {"name": "LeBron James", "sport": "NBA", "lines": {"points": 23.5, "rebounds": 7.5, "assists": 7.5, "three_pointers_made": 1.5}},
        {"name": "Gerrit Cole", "sport": "MLB", "role": "pitcher", "lines": {"strikeouts": 7.5}},
        {"name": "Spencer Strider", "sport": "MLB", "role": "pitcher", "lines": {"strikeouts": 8.5}},
        {"name": "Zack Wheeler", "sport": "MLB", "role": "pitcher", "lines": {"strikeouts": 7.5}},
        {"name": "Freddie Freeman", "sport": "MLB", "role": "batter", "lines": {"hits": 1.5}},
        {"name": "Mookie Betts", "sport": "MLB", "role": "batter", "lines": {"hits": 1.5}},
    ],
}

NBA_CATS = ["points", "rebounds", "assists", "three_pointers_made"]
MLB_CATS = ["strikeouts", "hits"]
CAT_ES = {"points": "Puntos", "rebounds": "Rebotes", "assists": "Asistencias", "three_pointers_made": "Triples", "strikeouts": "Ponches", "hits": "Hits"}
SPORTAPI_HOST = "sportapi7.p.rapidapi.com"
SPORTAPI_BASE = "https://sportapi7.p.rapidapi.com/api"
TIMEOUT = 12

def _log(msg: str, color: str = "W", bold: bool = False) -> None:
    print(f"{(C['B'] if bold else '')}{C.get(color, '')}{msg}{C['0']}")

def _section(title: str) -> None:
    _log("\n" + "=" * 66, "C", bold=True)
    _log(f"  {title}", "C", bold=True)
    _log("=" * 66, "C", bold=True)

def _bar(rate: float, width: int = 10) -> str:
    filled = round(rate * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"

def _resolve_date(cfg_value: str) -> date:
    if cfg_value.lower() == "auto": return date.today()
    try: return date.fromisoformat(cfg_value)
    except ValueError: return date.today()

def _sportapi_headers(api_key: str) -> dict:
    return {"x-rapidapi-host": SPORTAPI_HOST, "x-rapidapi-key": api_key}

class SpreadAnalyzer:
    ODDS_DELAY_SECONDS = 0.35
    def __init__(self, cfg: dict) -> None:
        self.api_key = cfg["SPORTAPI_KEY"]
        self.sports = cfg["spread_sports"]
        self.buffer = cfg["spread_value_buffer"]
        self.n_games = cfg["n_games"]
        self.market_names = cfg["handicap_market_names"]

    def analyze(self, target_date: date) -> list:
        if not self.api_key or not HAS_REQUESTS: return []
        all_results = []
        for sport_cfg in self.sports:
            _log(f"\n  Barriendo {sport_cfg['label']}...", "C")
            events = self._fetch_scheduled(sport_cfg, target_date)
            _log(f"  {len(events)} eventos encontrados para hoy.", "W")
            for event in events:
                result = self._process_event(event, sport_cfg, target_date)
                if result: all_results.append(result)
        return all_results

    def _fetch_scheduled(self, sport_cfg: dict, target_date: date) -> list:
        sport = sport_cfg["sport_path"]
        url = f"https://sportapi7.p.rapidapi.com/api/v1/sport/{sport}/scheduled-events/{target_date.strftime('%Y-%m-%d')}"
        try:
            resp = requests.get(url, headers=_sportapi_headers(self.api_key), timeout=TIMEOUT)
            resp.raise_for_status()
            events = resp.json().get("events", [])
            league_ids = sport_cfg.get("league_ids", [])
            if league_ids: events = [ev for ev in events if ev.get("tournament", {}).get("uniqueTournament", {}).get("id") in league_ids]
            return events
        except Exception:
            return []

    def _fetch_odds(self, event_id: int, sport: str) -> dict:
        import time; time.sleep(self.ODDS_DELAY_SECONDS)
        url = f"{SPORTAPI_BASE}/{sport}/match/{event_id}/odds"
        try:
            resp = requests.get(url, headers=_sportapi_headers(self.api_key), timeout=TIMEOUT)
            if resp.status_code == 404: return {}
            resp.raise_for_status()
            return self._parse_handicap(resp.json())
        except Exception: return {}

    def _parse_handicap(self, data: dict) -> dict:
        markets = data.get("markets", []) or data.get("odds", {}).get("markets", [])
        for market in markets:
            if not any(h.lower() in market.get("marketName", "").lower() for h in self.market_names): continue
            choices = market.get("choices", [])
            home_hcap = away_hcap = None
            bookmaker = market.get("sourceId", "bet365")
            for choice in choices:
                raw_hcap = choice.get("handicapValue", choice.get("handicap"))
                hcap = float(raw_hcap) if raw_hcap is not None else None
                if choice.get("name") == "1": home_hcap = hcap
                elif choice.get("name") == "2": away_hcap = hcap
            if home_hcap is not None and away_hcap is not None:
                return {"home_spread": home_hcap, "away_spread": away_hcap, "bookmaker": str(bookmaker), "market": market.get("marketName", "")}
        return {}

    def _process_event(self, event: dict, sport_cfg: dict, target_date: date) -> dict:
        event_id = event.get("id")
        home_team = event.get("homeTeam", {}).get("name", "TBD")
        away_team = event.get("awayTeam", {}).get("name", "TBD")
        tournament = event.get("tournament", {}).get("uniqueTournament", {}).get("name", sport_cfg["label"])
        start_ts = event.get("startTimestamp", 0)
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%H:%M UTC") if start_ts else "--:--"

        odds = self._fetch_odds(event_id, sport_cfg["sport_path"])
        home_spread, away_spread = odds.get("home_spread"), odds.get("away_spread")
        
        h_avg, h_hist = self._estimate_margin(home_team, True)
        a_avg, a_hist = self._estimate_margin(away_team, False)
        
        h_val = self._is_value(h_avg, home_spread) if home_spread is not None else False
        a_val = self._is_value(a_avg, away_spread) if away_spread is not None else False
        value_team = home_team if h_val else (away_team if a_val else "-")
        
        return {
            "sport": sport_cfg["label"], "sport_path": sport_cfg["sport_path"], "tournament": tournament,
            "event_id": event_id, "home_team": home_team, "away_team": away_team, "start_time": start_dt,
            "home_spread": home_spread, "away_spread": away_spread, "bookmaker": odds.get("bookmaker", "N/A"),
            "market": odds.get("market", "N/A"), "home_avg_margin": h_avg, "away_avg_margin": a_avg,
            "value_team": value_team,
        }

    def _estimate_margin(self, team: str, home: bool) -> tuple:
        rng = random.Random(int(hashlib.md5(f"{team}{home}".encode()).hexdigest()[:8], 16))
        hist = [round(rng.gauss(3.5 if home else -2.0, 8), 1) for _ in range(self.n_games)]
        return round(sum(hist) / len(hist), 2), hist

    def _is_value(self, avg_margin: float, casino_spread: float) -> bool:
        return casino_spread > 0 and avg_margin > -(casino_spread - self.buffer)

class PlayerPropsAnalyzer:
    _NBA_BASES = {"Stephen Curry": (29, 5.0, 6.0, 4.5), "LeBron James": (26, 7.0, 7.0, 1.5), "Nikola Jokic": (25, 12.0, 9.0, 0.5), "Shai Gilgeous-Alexander": (31, 5.0, 6.0, 1.5), "Anthony Edwards": (27, 5.0, 5.0, 3.5), "Jayson Tatum": (27, 8.0, 5.0, 2.5)}
    _MLB_K = {"Gerrit Cole": 8.5, "Spencer Strider": 9.5, "Zack Wheeler": 8.0}
    _MLB_HITS = {"Freddie Freeman": 1.4, "Mookie Betts": 1.3}

    def __init__(self, cfg: dict) -> None:
        self.n, self.hi_pct, self.con_pct, self.bdl_key = cfg["n_games"], cfg["high_consistency_pct"], cfg["consistency_pct"], cfg["balldontlie_key"]

    def analyze(self, player_cfg: dict) -> dict:
        name, sport, role, lines = player_cfg["name"], player_cfg.get("sport", "NBA").upper(), player_cfg.get("role", "player"), player_cfg.get("lines", {})
        logs = self._nba_logs(name) if sport == "NBA" else self._mlb_logs(name, role)
        cats = NBA_CATS if sport == "NBA" else MLB_CATS
        
        over_rates = {}
        for cat in cats:
            vals = [g.get(cat, 0) for g in logs[-self.n:]]
            if not vals: continue
            line = lines.get(cat, round((sum(vals) / len(vals)) - 0.5, 1))
            overs = sum(1 for v in vals if v > line)
            rate = round(overs / len(vals), 3)
            label = "ALTA CONSISTENCIA" if rate >= self.hi_pct else ("Consistente" if rate >= self.con_pct else ("Moderado" if rate >= 0.5 else "Inconsistente"))
            over_rates[cat] = {"line": line, "over_count": overs, "total": len(vals), "rate": rate, "label": label}
        return {"player": name, "sport": sport, "over_rates": over_rates}

    def _nba_logs(self, name: str) -> list:
        rng = random.Random(int(hashlib.md5(name.encode()).hexdigest()[:8], 16))
        base = self._NBA_BASES.get(name, (20, 5.0, 5.0, 2.0))
        return [{"points": max(0, round(rng.gauss(base[0], 5))), "rebounds": max(0, round(rng.gauss(base[1], 2.5))), "assists": max(0, round(rng.gauss(base[2], 2.0))), "three_pointers_made": max(0, round(rng.gauss(base[3], 1.5)))} for _ in range(self.n)]

    def _mlb_logs(self, name: str, role: str) -> list:
        rng = random.Random(int(hashlib.md5(f"{name}{role}".encode()).hexdigest()[:8], 16))
        if role == "pitcher": return [{"strikeouts": max(0, round(rng.gauss(self._MLB_K.get(name, 6.5), 2.5))), "hits": 0} for _ in range(self.n)]
        return [{"strikeouts": 0, "hits": max(0, round(rng.gauss(self._MLB_HITS.get(name, 1.0) * 4, 1.2)))} for _ in range(self.n)]

def run(cfg: dict = None) -> dict:
    if cfg is None: cfg = CONFIG
    today = _resolve_date(cfg["analysis_date"])
    
    spreads = SpreadAnalyzer(cfg).analyze(today)
    props = [PlayerPropsAnalyzer(cfg).analyze(p) for p in cfg["roster"]]
    
    lines = [f"PARLAY STATS REPORT — {today.strftime('%d/%m/%Y')}\n\nANALISIS MULTIDEPORTE (SportAPI)\n"]
    if not spreads: lines.append("Sin eventos de spreads para hoy.")
    else:
        for s in spreads:
            if s["value_team"] != "-": lines.append(f"✅ {s['home_team']} vs {s['away_team']} | Spread: {s['home_spread'] or 'N/A'} -> Valor en: {s['value_team']}")
    
    lines.append("\nPROPS ALTA CONSISTENCIA (>=80%)")
    for p in props:
        for c, d in p["over_rates"].items():
            if d["rate"] >= 0.8: lines.append(f"⭐ {p['player']} ({CAT_ES.get(c, c)}) Over {d['line']} -> {d['rate']*100:.0f}%")
    
    report_text = "\n".join(lines)
    print("\n" + report_text + "\n")
    
    if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
        try:
            resp = requests.post(f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage", json={"chat_id": cfg["telegram_chat_id"], "text": report_text, "parse_mode": None})
            if resp.status_code == 200: _log("✅ Reporte enviado a Telegram.", "G", bold=True)
            else: _log(f"❌ Error Telegram: {resp.text}", "R")
        except Exception as e: _log(f"❌ Error: {e}", "R")
    
    return {"spreads": spreads, "props": props}

if __name__ == "__main__":
    run(CONFIG)
