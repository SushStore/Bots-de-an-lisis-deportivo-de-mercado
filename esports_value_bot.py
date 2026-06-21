#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
esports_value_bot.py
====================================================================
ESPORTS QUANT REPORT — SUSH FLOW STUDIO

Bot de automatización que consume SportAPI (sportapi7.p.rapidapi.com)
por la ruta de Esports, captura los eventos agendados del día
(League of Legends / CS:GO), filtra torneos VIP y emite un pick
algorítmico determinístico a Telegram en texto plano.

Compatible con Google Colab:
    !pip install requests
    (definir variables de entorno o editar CONFIG)

NOTA DE TRANSPARENCIA:
    La "consistencia de mapas ganados" se calcula con un hash estable
    del nombre del equipo. Es un heurístico reproducible para el
    prototipo / demo, NO un modelo predictivo entrenado con datos
    históricos reales. Sirve como esqueleto: el método
    ConsistencyEngine.score() es el punto donde se inyectaría un
    modelo real (winrate por mapa, forma reciente, etc.).
====================================================================
"""

from __future__ import annotations

import os
import sys
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # Se valida en runtime; permite import en entornos sin red


# ====================================================================
# CONFIG  (single source of truth)
# ====================================================================
CONFIG: Dict[str, Any] = {
    # --- SportAPI / RapidAPI ---
    "RAPIDAPI_KEY": os.getenv("RAPIDAPI_KEY", "TU_RAPIDAPI_KEY_AQUI"),
    "RAPIDAPI_HOST": "sportapi7.p.rapidapi.com",
    "API_BASE": "https://sportapi7.p.rapidapi.com/api/v1",
    "SPORT_SLUG": "esports",
    "REQUEST_TIMEOUT": 20,

    # --- Telegram ---
    "TELEGRAM_TOKEN": os.getenv("TELEGRAM_TOKEN", "TU_TELEGRAM_TOKEN_AQUI"),
    "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", "TU_CHAT_ID_AQUI"),

    # --- Lógica de negocio ---
    # Palabras clave oficiales para filtrar torneos serios (case-insensitive).
    "VIP_KEYWORDS": [
        "LCK", "LCS", "LEC", "LPL",          # League of Legends Tier 1
        "Major", "Pro League", "ESL",         # CS:GO / CS2 Tier 1
        "Championship", "World", "Masters",   # Eventos cúspide
    ],
    # Umbral para clasificar un equipo como "Prensa Segura".
    "CONSISTENCY_THRESHOLD": 80,
    # Rango del heurístico de consistencia (min, max).
    "CONSISTENCY_RANGE": (55, 95),

    # --- Operación ---
    # Si no hay API key real, corre con datos sintéticos para demo/QA.
    "DEMO_MODE_ON_MISSING_KEY": True,
    # Solo loguear el reporte en consola sin disparar a Telegram (útil en Colab).
    "DRY_RUN": False,
}


# ====================================================================
# LOGGING
# ====================================================================
def build_logger(name: str = "esports_value_bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:  # Evita handlers duplicados en re-ejecuciones (Colab)
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    )
    logger.addHandler(handler)
    return logger


log = build_logger()


# ====================================================================
# MODELO DE DOMINIO
# ====================================================================
@dataclass
class EsportsMatch:
    """Representación normalizada de un partido de esports."""
    team_a: str
    team_b: str
    tournament: str
    game: str = "Esports"
    event_id: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def label(self) -> str:
        return f"{self.team_a} vs {self.team_b} [{self.tournament}]"


# ====================================================================
# CLIENTE DE API
# ====================================================================
class SportAPIClient:
    """Cliente delgado para la ruta de Esports de SportAPI (Sofascore wrapper)."""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.headers = {
            "x-rapidapi-key": cfg["RAPIDAPI_KEY"],
            "x-rapidapi-host": cfg["RAPIDAPI_HOST"],
        }

    def _has_real_key(self) -> bool:
        key = self.cfg["RAPIDAPI_KEY"]
        return bool(key) and not key.startswith("TU_")

    def fetch_scheduled_events(self, date_str: str) -> List[EsportsMatch]:
        """Trae los eventos agendados de esports para una fecha YYYY-MM-DD."""
        if not self._has_real_key():
            if self.cfg["DEMO_MODE_ON_MISSING_KEY"]:
                log.warning("RAPIDAPI_KEY no configurada -> usando DATOS DEMO.")
                return self._demo_events()
            raise RuntimeError("RAPIDAPI_KEY ausente y DEMO_MODE desactivado.")

        if requests is None:
            raise RuntimeError("La librería 'requests' no está instalada.")

        url = (
            f"{self.cfg['API_BASE']}/sport/"
            f"{self.cfg['SPORT_SLUG']}/scheduled-events/{date_str}"
        )
        log.info("GET %s", url)
        try:
            resp = requests.get(
                url, headers=self.headers, timeout=self.cfg["REQUEST_TIMEOUT"]
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # red caída, 429, JSON inválido, etc.
            log.error("Fallo consultando la API (%s). Fallback a DEMO.", exc)
            if self.cfg["DEMO_MODE_ON_MISSING_KEY"]:
                return self._demo_events()
            return []

        return self._parse_events(payload)

    @staticmethod
    def _parse_events(payload: Dict[str, Any]) -> List[EsportsMatch]:
        """Normaliza el JSON de Sofascore a EsportsMatch de forma defensiva."""
        events = payload.get("events", []) if isinstance(payload, dict) else []
        matches: List[EsportsMatch] = []
        for ev in events:
            try:
                tournament = ev.get("tournament", {}) or {}
                unique = tournament.get("uniqueTournament", {}) or {}
                t_name = unique.get("name") or tournament.get("name") or "Unknown"
                category = (tournament.get("category", {}) or {}).get("name", "")
                home = (ev.get("homeTeam", {}) or {}).get("name", "TBD")
                away = (ev.get("awayTeam", {}) or {}).get("name", "TBD")
                matches.append(
                    EsportsMatch(
                        team_a=home,
                        team_b=away,
                        tournament=t_name,
                        game=category or "Esports",
                        event_id=ev.get("id"),
                        raw=ev,
                    )
                )
            except Exception as exc:  # un evento corrupto no tumba el lote
                log.debug("Evento ignorado por parseo (%s).", exc)
        return matches

    @staticmethod
    def _demo_events() -> List[EsportsMatch]:
        """Datos sintéticos representativos para QA / demo offline."""
        return [
            EsportsMatch("T1", "Gen.G", "LCK Spring", "League of Legends", 1),
            EsportsMatch("G2 Esports", "Fnatic", "LEC Summer", "League of Legends", 2),
            EsportsMatch("NAVI", "FaZe Clan", "ESL Pro League", "CS:GO", 3),
            EsportsMatch("Vitality", "Spirit", "PGL Major", "CS:GO", 4),
            # Ruido amateur que debe ser filtrado:
            EsportsMatch("Local Team A", "Local Team B", "Open Qualifier #7", "CS:GO", 5),
            EsportsMatch("Academy X", "Academy Y", "Amateur Cup", "League of Legends", 6),
        ]


# ====================================================================
# FILTRO VIP
# ====================================================================
class VIPFilter:
    """Conserva solo partidos cuyos torneos contengan palabras clave oficiales."""

    def __init__(self, keywords: List[str]):
        self.keywords = [k.lower() for k in keywords]

    def is_vip(self, match: EsportsMatch) -> bool:
        name = match.tournament.lower()
        return any(kw in name for kw in self.keywords)

    def apply(self, matches: List[EsportsMatch]) -> List[EsportsMatch]:
        return [m for m in matches if self.is_vip(m)]


# ====================================================================
# MOTOR DE CONSISTENCIA (heurístico determinístico)
# ====================================================================
class ConsistencyEngine:
    """
    Calcula una tasa de consistencia de mapas ganados por equipo.

    Implementación actual: hash estable (MD5) del nombre del equipo
    mapeado a un porcentaje dentro de CONSISTENCY_RANGE. Es 100%
    reproducible (mismo equipo -> mismo número) y sirve de placeholder
    para un modelo real. Reemplazar score() por winrate histórico,
    forma reciente o ELO cuando esos datos estén disponibles.
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.lo, self.hi = cfg["CONSISTENCY_RANGE"]
        self.threshold = cfg["CONSISTENCY_THRESHOLD"]

    def score(self, team_name: str) -> int:
        digest = hashlib.md5(team_name.strip().lower().encode("utf-8")).hexdigest()
        bucket = int(digest, 16) % (self.hi - self.lo + 1)
        return self.lo + bucket

    def evaluate(self, match: EsportsMatch) -> Dict[str, Any]:
        score_a = self.score(match.team_a)
        score_b = self.score(match.team_b)
        if score_a >= score_b:
            favorite, fav_score = match.team_a, score_a
        else:
            favorite, fav_score = match.team_b, score_b
        return {
            "favorite": favorite,
            "consistency": fav_score,
            "is_safe_press": fav_score > self.threshold,
            "scores": {match.team_a: score_a, match.team_b: score_b},
        }


# ====================================================================
# REPORTERO DE TELEGRAM (texto plano, sin parse_mode)
# ====================================================================
class TelegramReporter:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.token = cfg["TELEGRAM_TOKEN"]
        self.chat_id = cfg["TELEGRAM_CHAT_ID"]

    def _is_configured(self) -> bool:
        return (
            bool(self.token) and not self.token.startswith("TU_")
            and bool(self.chat_id) and not str(self.chat_id).startswith("TU_")
        )

    @staticmethod
    def build_message(match: EsportsMatch, evaluation: Dict[str, Any]) -> str:
        """Arma el reporte en texto plano. Sin Markdown, sin parse_mode."""
        lines = [
            "🎮 ESPORTS QUANT REPORT — SUSH FLOW STUDIO",
            f"🎮 Juego: {match.team_a} vs {match.team_b} [{match.tournament}]",
            f"⭐ Pick Algorítmico: Ganador de Mapa 1 -> {evaluation['favorite']}",
            f"📊 Consistencia del algoritmo: {evaluation['consistency']}%",
        ]
        if evaluation["is_safe_press"]:
            lines.append("🔒 Clasificación: PRENSA SEGURA")
        return "\n".join(lines)

    def send(self, text: str) -> bool:
        if self.cfg["DRY_RUN"]:
            log.info("DRY_RUN activo. Reporte NO enviado a Telegram.")
            return False
        if not self._is_configured():
            log.warning("Telegram no configurado. Se omite el envío.")
            return False
        if requests is None:
            log.error("'requests' no instalada; no se puede enviar a Telegram.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        # SIN parse_mode -> Telegram interpreta el cuerpo como texto plano.
        payload = {"chat_id": self.chat_id, "text": text}
        try:
            resp = requests.post(url, data=payload, timeout=self.cfg["REQUEST_TIMEOUT"])
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Error enviando a Telegram: %s", exc)
            return False


# ====================================================================
# ORQUESTADOR
# ====================================================================
class EsportsValueBot:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.client = SportAPIClient(cfg)
        self.vip_filter = VIPFilter(cfg["VIP_KEYWORDS"])
        self.engine = ConsistencyEngine(cfg)
        self.reporter = TelegramReporter(cfg)

    def run(self, date_str: Optional[str] = None) -> None:
        date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log.info("=== Esports Value Bot — fecha objetivo: %s ===", date_str)

        # 1. Consumir eventos
        matches = self.client.fetch_scheduled_events(date_str)
        log.info("Eventos de Esports encontrados: %d", len(matches))

        # 2. Filtro VIP
        vip_matches = self.vip_filter.apply(matches)
        log.info("Eventos que pasaron el filtro VIP: %d", len(vip_matches))

        if not vip_matches:
            log.info("Sin partidos VIP para hoy. Nada que reportar.")
            return

        # 3 + 4. Evaluar y reportar
        sent = 0
        for match in vip_matches:
            evaluation = self.engine.evaluate(match)
            message = self.reporter.build_message(match, evaluation)
            log.info("Reporte generado para %s", match.label)
            print("\n" + message + "\n" + "-" * 48)
            if self.reporter.send(message):
                sent += 1

        log.info("=== Proceso terminado. Reportes enviados a Telegram: %d ===", sent)


# ====================================================================
# ENTRYPOINT
# ====================================================================
def main() -> None:
    bot = EsportsValueBot(CONFIG)
    # Permite pasar una fecha por argumento: python esports_value_bot.py 2026-06-18
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    bot.run(date_arg)


if __name__ == "__main__":
    main()
