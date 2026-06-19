# ==============================================================================
# parlay_stats_analyzer.py  v4.0 (PRO UNIFICADO - SPREADS REALES)
# ==============================================================================
import hashlib, json, random, time, requests
from datetime import date, datetime, timezone

CONFIG = {
    "analysis_date": "auto",
    "SPORTAPI_KEY": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "spread_value_buffer": 3.0,
    "spread_sports": [
        {"sport_path": "football", "label": "Fútbol", "keywords": ["World Cup", "World", "Copa"]},
        {"sport_path": "basketball", "label": "NBA", "keywords": ["NBA"]},
        {"sport_path": "baseball", "label": "MLB", "keywords": ["MLB", "Major League"]}
    ],
    "handicap_market_names": ["Asian Handicap", "Handicap", "Point Spread", "Run Line"],
    "roster": [
        {"name": "Stephen Curry", "sport": "NBA", "lines": {"points": 27.5}},
        {"name": "Gerrit Cole", "sport": "MLB", "lines": {"strikeouts": 7.5}}
    ]
}

def _resolve_date(cfg_value):
    if cfg_value.lower() == "auto": return date.today()
    try: return date.fromisoformat(cfg_value)
    except ValueError: return date.today()

class SpreadAnalyzer:
    def __init__(self, cfg):
        self.api_key = cfg["SPORTAPI_KEY"]
        self.sports = cfg["spread_sports"]
        self.buffer = cfg["spread_value_buffer"]
        self.market_names = cfg["handicap_market_names"]

    def analyze(self, target_date):
        if not self.api_key: return []
        all_results = []
        for sport_cfg in self.sports:
            sport = sport_cfg["sport_path"]
            url = f"https://sportapi7.p.rapidapi.com/api/v1/sport/{sport}/scheduled-events/{target_date.strftime('%Y-%m-%d')}"
            try:
                resp = requests.get(url, headers={"x-rapidapi-host": "sportapi7.p.rapidapi.com", "x-rapidapi-key": self.api_key}, timeout=15)
                if resp.status_code == 404: continue
                events = resp.json().get("events", [])
                
                # Filtro inteligente por nombre de torneo para evitar eSports y ligas menores
                valid_events = []
                for ev in events:
                    t_name = ev.get("tournament", {}).get("uniqueTournament", {}).get("name", "")
                    if any(k.lower() in t_name.lower() for k in sport_cfg["keywords"]):
                        valid_events.append(ev)
                
                print(f"  Barriendo {sport_cfg['label']}... {len(valid_events)} eventos OFICIALES filtrados.")
                
                for ev in valid_events:
                    ev_id = ev.get("id")
                    home = ev.get("homeTeam", {}).get("name", "TBD")
                    away = ev.get("awayTeam", {}).get("name", "TBD")
                    
                    # Obtener cuotas reales del casino
                    time.sleep(0.35)
                    odds_url = f"https://sportapi7.p.rapidapi.com/api/v1/sport/{sport}/match/{ev_id}/odds"
                    odds_resp = requests.get(odds_url, headers={"x-rapidapi-host": "sportapi7.p.rapidapi.com", "x-rapidapi-key": self.api_key}, timeout=15)
                    
                    home_spread = None
                    if odds_resp.status_code == 200:
                        markets = odds_resp.json().get("markets", []) or odds_resp.json().get("odds", {}).get("markets", [])
                        for m in markets:
                            if any(h.lower() in m.get("marketName", "").lower() for h in self.market_names):
                                for c in m.get("choices", []):
                                    raw = c.get("handicapValue", c.get("handicap"))
                                    if raw is not None and c.get("name") == "1":
                                        home_spread = float(raw)
                                if home_spread is not None: break
                    
                    # Margen histórico simulado para calcular valor (requiere base de datos real a futuro)
                    avg_margin = round(random.Random(int(hashlib.md5(home.encode()).hexdigest()[:8], 16)).gauss(3.5, 8), 1)
                    value_team = home if (home_spread and home_spread > 0 and avg_margin > -(home_spread - self.buffer)) else "-"
                    
                    all_results.append({
                        "sport": sport_cfg["label"], "home_team": home, "away_team": away,
                        "home_spread": home_spread, "value_team": value_team
                    })
            except Exception as e:
                print(f"  [!] Error en {sport_cfg['label']}: {e}")
        return all_results

class PlayerPropsAnalyzer:
    # Nota: Este script mantiene datos simulados de Props porque tu bot oficial para MLB es mlb_bot.py
    def analyze(self, p):
        rng = random.Random(int(hashlib.md5(p["name"].encode()).hexdigest()[:8], 16))
        return {"player": p["name"], "sport": p["sport"], "rate": 0.9 if rng.random() > 0.5 else 0.6}

def run(cfg=CONFIG):
    today = _resolve_date(cfg["analysis_date"])
    print("\n=== PARLAY STATS ANALYZER v4.0 (ODDS REALES) ===")
    
    spreads = SpreadAnalyzer(cfg).analyze(today)
    props = [PlayerPropsAnalyzer().analyze(p) for p in cfg["roster"]]
    
    lines = [f"PARLAY STATS REPORT — {today.strftime('%d/%m/%Y')}\n\nANALISIS DE SPREADS (SportAPI PRO)\n"]
    if not spreads: 
        lines.append("Sin eventos de spreads oficiales para hoy.")
    else:
        for s in spreads:
            spread_val = f"{s['home_spread']:+.1f}" if s['home_spread'] is not None else 'N/A'
            lines.append(f"🏟️ {s['home_team']} vs {s['away_team']} | Spread Local: {spread_val} -> Valor: {s['value_team']}")
    
    report_text = "\n".join(lines)
    print("\n" + report_text + "\n")
    
    if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
        try:
            resp = requests.post(f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage", json={"chat_id": cfg["telegram_chat_id"], "text": report_text})
            if resp.status_code == 200: print("✅ Reporte enviado a Telegram exitosamente.")
            else: print(f"❌ Error Telegram: {resp.text}")
        except Exception as e: print(f"❌ Error de conexión: {e}")

if __name__ == "__main__":
    run(CONFIG)
