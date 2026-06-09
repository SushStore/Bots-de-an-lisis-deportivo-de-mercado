"""
mlb_bot.py - MLB Player Props Consistency Bot
Compatible con Google Colab
Arquitectura CONFIG-first | Datos reales MLB Stats API
"""

# ==============================================================================
# CONFIGURACION GLOBAL
# ==============================================================================

TOKEN   = "TU_TOKEN_AQUI"       # Token del bot de Telegram
CHAT_ID = "TU_CHAT_ID_AQUI"     # Chat ID del canal/grupo MLB

DEMO_MODE = False                # False = MLB Stats API real | True = simulado (solo emergencia)

TEMPORADA = 2026                 # Temporada activa a consultar

# Lineas de referencia (Player Props - ajustar segun el sportsbook)
PROP_LINES = {
    "strikeouts":   5.5,
    "hits_allowed": 4.5,
    "hits_totales": 1.5,
    "bases_totales": 2.5,
}

# Umbrales de clasificacion
UMBRAL_ALTA  = 80   # >= 80% -> Alta Consistencia
UMBRAL_BUENA = 70   # 70-79% -> Consistente

# ==============================================================================
# DEPENDENCIAS
# ==============================================================================

import requests
import datetime
import time

# ==============================================================================
# CONSTANTES DE LA MLB STATS API
# ==============================================================================

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# IDs numericos confirmados — fallback cuando la busqueda por nombre falla
# (jugadores de alto perfil que aparecen frecuentemente en props)
MLB_IDS_CONOCIDOS = {
    # Pitchers
    "Gerrit Cole":      543037,
    "Shohei Ohtani":    660271,
    "Spencer Strider":  675911,
    "Zack Wheeler":     554430,
    "Logan Webb":       657277,
    "Framber Valdez":   664285,
    "Corbin Burnes":    669456,
    "Dylan Cease":      656302,
    "Pablo Lopez":      641154,
    "Sonny Gray":       543243,
    "Hunter Brown":     686613,
    "Tyler Glasnow":    607192,
    "Kevin Gausman":    519293,
    "Blake Snell":      605483,
    "Max Fried":        592332,
    "Tarik Skubal":     669373,
    "Yoshinobu Yamamoto": 808967,
    # Bateadores
    "Freddie Freeman":  518692,
    "Mookie Betts":     605141,
    "Aaron Judge":      592450,
    "Juan Soto":        665742,
    "Jose Ramirez":     608070,
    "Yordan Alvarez":   670541,
    "Trea Turner":      607208,
    "Corey Seager":     608369,
    "Bo Bichette":      666182,
    "Rafael Devers":    646240,
    "Pete Alonso":      624413,
    "Julio Rodriguez":  677594,
    "Kyle Tucker":      663656,
    "Adolis Garcia":    666969,
    "Gunnar Henderson": 683002,
    "Ronald Acuna Jr":  660670,
    "Bryce Harper":     547180,
    "Mike Trout":       545361,
    "Wander Franco":    665833,
    "Vladimir Guerrero Jr": 665489,
    "Bo Bichette":      666182,
    "Xander Bogaerts":  572761,
}

# ==============================================================================
# HELPER: SESION HTTP CON REINTENTOS
# ==============================================================================

_SESSION = None

def _get_session() -> requests.Session:
    """Devuelve una sesion reutilizable con headers de navegador."""
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })
    return _SESSION


def _get_json(url: str, params: dict = None, reintentos: int = 3) -> dict | None:
    """
    GET con reintentos y backoff exponencial.
    Devuelve el dict JSON parseado o None si todos los intentos fallan.
    """
    session = _get_session()
    for intento in range(1, reintentos + 1):
        try:
            resp = session.get(url, params=params, timeout=12)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            print(f"    [HTTP {resp.status_code}] {url} (intento {intento}/{reintentos})")
        except requests.exceptions.RequestException as e:
            print(f"    [RED] {e} (intento {intento}/{reintentos})")
        if intento < reintentos:
            time.sleep(1.5 * intento)   # backoff: 1.5s, 3.0s
    return None

# ==============================================================================
# FETCHER REAL #1: BUSQUEDA DE ID DE JUGADOR
# ==============================================================================

def buscar_id_jugador(nombre: str) -> int | None:
    """
    Obtiene el ID numerico de un jugador.
    Estrategia de tres capas:
      1. Lookup en MLB_IDS_CONOCIDOS (instantaneo, sin red)
      2. GET /people/search?names={nombre} (MLB Stats API)
      3. Devuelve None si ambas fallan (el caller maneja la omision)
    """
    # Capa 1: cache local de IDs conocidos
    pid = MLB_IDS_CONOCIDOS.get(nombre)
    if pid:
        return pid

    # Capa 2: busqueda por nombre en la API
    nombre_encoded = nombre.replace(" ", "+")
    url  = f"{MLB_API_BASE}/people/search"
    data = _get_json(url, params={"names": nombre, "sportId": 1})

    if data:
        personas = data.get("people", [])
        if personas:
            pid = personas[0].get("id")
            if pid:
                MLB_IDS_CONOCIDOS[nombre] = pid   # cachear para esta sesion
                return pid

    print(f"    [AVISO] No se encontro ID para: {nombre}")
    return None

# ==============================================================================
# FETCHER REAL #2: ULTIMOS 10 JUEGOS DE PITCHER (DATOS REALES)
# ==============================================================================

def generar_ultimos_10_juegos_pitcher(nombre: str) -> dict:
    """
    Consulta la MLB Stats API para obtener los ultimos 10 juegos
    reales de bitacora (gameLog) del lanzador indicado.

    Endpoint:
        GET /api/v1/people/{id}/stats
            ?stats=gameLog&group=pitching&season={TEMPORADA}

    Campos extraidos por juego:
        - strikeOuts  -> mapeado a 'strikeouts'
        - hits        -> mapeado a 'hits_allowed'
        - inningsPitched -> mapeado a 'innings'

    Si la API falla o el jugador no tiene historial, devuelve
    una lista vacia para que el caller lo maneje con gracia.
    """
    pid = buscar_id_jugador(nombre)
    if pid is None:
        return {"nombre": nombre, "tipo": "pitcher", "juegos": []}

    url  = f"{MLB_API_BASE}/people/{pid}/stats"
    data = _get_json(url, params={
        "stats":  "gameLog",
        "group":  "pitching",
        "season": TEMPORADA,
    })

    if data is None:
        return {"nombre": nombre, "tipo": "pitcher", "juegos": []}

    # La respuesta tiene una lista 'stats'; el primer elemento con type.displayName
    # == "Log" contiene los splits ordenados cronologicamente (mas antiguo primero).
    splits = []
    for bloque in data.get("stats", []):
        if bloque.get("type", {}).get("displayName", "") == "Log":
            splits = bloque.get("splits", [])
            break

    if not splits:
        # Intentar con el primer bloque disponible como fallback
        for bloque in data.get("stats", []):
            splits = bloque.get("splits", [])
            if splits:
                break

    # Tomar los ultimos 10 (los mas recientes estan al final)
    ultimos = splits[-10:] if len(splits) >= 10 else splits

    juegos = []
    for s in ultimos:
        stat = s.get("stat", {})
        juegos.append({
            "strikeouts":  int(stat.get("strikeOuts", 0)),
            "hits_allowed": int(stat.get("hits", 0)),
            "innings":     float(str(stat.get("inningsPitched", "0.0")).replace(",", ".")),
        })

    if not juegos:
        print(f"    [AVISO] Sin juegos en gameLog para pitcher: {nombre} (ID {pid})")

    return {"nombre": nombre, "tipo": "pitcher", "juegos": juegos}

# ==============================================================================
# FETCHER REAL #3: ULTIMOS 10 JUEGOS DE BATEADOR (DATOS REALES)
# ==============================================================================

def generar_ultimos_10_juegos_bateador(nombre: str) -> dict:
    """
    Consulta la MLB Stats API para obtener los ultimos 10 juegos
    reales de bitacora (gameLog) del bateador indicado.

    Endpoint:
        GET /api/v1/people/{id}/stats
            ?stats=gameLog&group=hitting&season={TEMPORADA}

    Campos extraidos por juego:
        - hits        -> mapeado a 'hits'
        - totalBases  -> mapeado a 'total_bases'

    Si la API falla o el jugador no tiene historial, devuelve
    una lista vacia para que el caller lo maneje con gracia.
    """
    pid = buscar_id_jugador(nombre)
    if pid is None:
        return {"nombre": nombre, "tipo": "bateador", "juegos": []}

    url  = f"{MLB_API_BASE}/people/{pid}/stats"
    data = _get_json(url, params={
        "stats":  "gameLog",
        "group":  "hitting",
        "season": TEMPORADA,
    })

    if data is None:
        return {"nombre": nombre, "tipo": "bateador", "juegos": []}

    splits = []
    for bloque in data.get("stats", []):
        if bloque.get("type", {}).get("displayName", "") == "Log":
            splits = bloque.get("splits", [])
            break

    if not splits:
        for bloque in data.get("stats", []):
            splits = bloque.get("splits", [])
            if splits:
                break

    # Tomar los ultimos 10 (los mas recientes estan al final)
    ultimos = splits[-10:] if len(splits) >= 10 else splits

    juegos = []
    for s in ultimos:
        stat = s.get("stat", {})
        juegos.append({
            "hits":        int(stat.get("hits", 0)),
            "total_bases": int(stat.get("totalBases", 0)),
        })

    if not juegos:
        print(f"    [AVISO] Sin juegos en gameLog para bateador: {nombre} (ID {pid})")

    return {"nombre": nombre, "tipo": "bateador", "juegos": juegos}

# ==============================================================================
# FETCHER DE PARTIDOS DEL DIA
# ==============================================================================

def obtener_partidos_del_dia_real() -> list:
    """
    Consulta el schedule de la MLB para hoy.
    Extrae pitcher probable y roster de bateadores titulares por partido.
    """
    hoy = datetime.date.today().strftime("%Y-%m-%d")
    url = f"{MLB_API_BASE}/schedule"
    data = _get_json(url, params={
        "sportId": 1,
        "date":    hoy,
        "hydrate": "probablePitcher,lineScore,team,roster(group=hitting)",
    })

    if data is None:
        print("[ERROR] No se pudo obtener el schedule. Verificar conexion a MLB API.")
        return []

    partidos = []
    for fecha in data.get("dates", []):
        for juego in fecha.get("games", []):
            local    = juego["teams"]["home"]["team"]["name"]
            visitante = juego["teams"]["away"]["team"]["name"]
            abr_l    = juego["teams"]["home"]["team"].get("abbreviation", "???")
            abr_v    = juego["teams"]["away"]["team"].get("abbreviation", "???")

            pitcher_l = (juego["teams"]["home"]
                         .get("probablePitcher", {})
                         .get("fullName", ""))
            pitcher_v = (juego["teams"]["away"]
                         .get("probablePitcher", {})
                         .get("fullName", ""))

            # Bateadores: intentar obtener del roster hydratado
            bateadores = []
            for lado in ("home", "away"):
                roster = juego["teams"][lado].get("roster", [])
                for jugador in roster[:5]:   # top 5 por equipo
                    nombre_b = jugador.get("person", {}).get("fullName", "")
                    if nombre_b:
                        bateadores.append(nombre_b)

            partidos.append({
                "local":      (local, abr_l),
                "visitante":  (visitante, abr_v),
                "pitchers":   [p for p in [pitcher_l, pitcher_v] if p],
                "bateadores": bateadores,
            })

    return partidos


# ==============================================================================
# MODO DEMO MINIMO (solo como red de seguridad de ultimo recurso)
# ==============================================================================

def obtener_partidos_del_dia_demo() -> list:
    """
    Partidos estaticos de emergencia.
    Solo se usa si DEMO_MODE=True o si la API del schedule falla completamente.
    Los fetchers de jugadores siguen intentando la API real aunque este modo este activo.
    """
    import random
    EQUIPOS = [
        ("New York Yankees","NYY"),("Los Angeles Dodgers","LAD"),
        ("Houston Astros","HOU"),  ("Atlanta Braves","ATL"),
        ("Philadelphia Phillies","PHI"),("Texas Rangers","TEX"),
        ("Chicago Cubs","CHC"),    ("San Diego Padres","SDP"),
    ]
    PITCHERS = [
        "Gerrit Cole","Shohei Ohtani","Spencer Strider","Zack Wheeler",
        "Logan Webb","Framber Valdez","Corbin Burnes","Dylan Cease",
    ]
    BATEADORES = [
        "Freddie Freeman","Mookie Betts","Aaron Judge","Juan Soto",
        "Jose Ramirez","Yordan Alvarez","Trea Turner","Corey Seager",
        "Gunnar Henderson","Pete Alonso","Rafael Devers","Julio Rodriguez",
    ]
    random.seed(datetime.date.today().toordinal())
    equipos_hoy = random.sample(EQUIPOS, k=6)
    partidos = []
    for i in range(0, len(equipos_hoy), 2):
        p1 = random.choice(PITCHERS)
        p2 = random.choice([p for p in PITCHERS if p != p1])
        bs = random.sample(BATEADORES, 6)
        partidos.append({
            "local":      equipos_hoy[i],
            "visitante":  equipos_hoy[i+1],
            "pitchers":   [p1, p2],
            "bateadores": bs,
        })
    return partidos

# ==============================================================================
# MOTOR DE ANALISIS
# ==============================================================================

def calcular_consistencia_pitcher(stats: dict) -> dict | None:
    """
    Calcula el porcentaje de Over en Strikeouts e Under en Hits Permitidos.
    Devuelve None si no hay suficientes juegos (minimo 3).
    """
    juegos = stats["juegos"]
    if len(juegos) < 3:
        return None

    linea_k = PROP_LINES["strikeouts"]
    linea_h = PROP_LINES["hits_allowed"]

    over_k  = sum(1 for j in juegos if j["strikeouts"]   > linea_k)
    over_h  = sum(1 for j in juegos if j["hits_allowed"] < linea_h)  # Under favorable

    n = len(juegos)
    return {
        "nombre":         stats["nombre"],
        "tipo":           "pitcher",
        "pct_strikeouts": round((over_k / n) * 100, 1),
        "pct_hits_under": round((over_h / n) * 100, 1),
        "prom_k":         round(sum(j["strikeouts"]   for j in juegos) / n, 1),
        "prom_h":         round(sum(j["hits_allowed"] for j in juegos) / n, 1),
        "linea_k":        linea_k,
        "linea_h":        linea_h,
        "n_juegos":       n,
    }


def calcular_consistencia_bateador(stats: dict) -> dict | None:
    """
    Calcula el porcentaje de Over en Hits Totales y Bases Totales.
    Devuelve None si no hay suficientes juegos (minimo 3).
    """
    juegos = stats["juegos"]
    if len(juegos) < 3:
        return None

    linea_h  = PROP_LINES["hits_totales"]
    linea_tb = PROP_LINES["bases_totales"]

    over_h  = sum(1 for j in juegos if j["hits"]        > linea_h)
    over_tb = sum(1 for j in juegos if j["total_bases"] > linea_tb)

    n = len(juegos)
    return {
        "nombre":            stats["nombre"],
        "tipo":              "bateador",
        "pct_hits":          round((over_h  / n) * 100, 1),
        "pct_bases_totales": round((over_tb / n) * 100, 1),
        "prom_hits":         round(sum(j["hits"]        for j in juegos) / n, 1),
        "prom_bases":        round(sum(j["total_bases"] for j in juegos) / n, 1),
        "linea_h":           linea_h,
        "linea_tb":          linea_tb,
        "n_juegos":          n,
    }


def clasificar(pct: float) -> str:
    if pct >= UMBRAL_ALTA:
        return "ALTA CONSISTENCIA"
    elif pct >= UMBRAL_BUENA:
        return "CONSISTENTE"
    return ""

# ==============================================================================
# FORMATEADOR DE REPORTE (texto plano, cero Markdown)
# ==============================================================================

def formatear_prop_pitcher(res: dict) -> str:
    lineas = [
        f"LANZADOR: {res['nombre'].upper()}",
        f"Muestra: {res['n_juegos']} juegos | Temporada {TEMPORADA}",
    ]
    tag_k = clasificar(res["pct_strikeouts"])
    lineas.append(
        f"  Ponches (K) Over {res['linea_k']}: {res['pct_strikeouts']}%"
        f"  (prom {res['prom_k']} K/juego)"
        + (f"  [{tag_k}]" if tag_k else "")
    )
    tag_h = clasificar(res["pct_hits_under"])
    lineas.append(
        f"  Hits Perm. Under {res['linea_h']}: {res['pct_hits_under']}%"
        f"  (prom {res['prom_h']} H/juego)"
        + (f"  [{tag_h}]" if tag_h else "")
    )
    return "\n".join(lineas)


def formatear_prop_bateador(res: dict) -> str:
    lineas = [
        f"BATEADOR: {res['nombre'].upper()}",
        f"Muestra: {res['n_juegos']} juegos | Temporada {TEMPORADA}",
    ]
    tag_h = clasificar(res["pct_hits"])
    lineas.append(
        f"  Hits Over {res['linea_h']}: {res['pct_hits']}%"
        f"  (prom {res['prom_hits']} H/juego)"
        + (f"  [{tag_h}]" if tag_h else "")
    )
    tag_tb = clasificar(res["pct_bases_totales"])
    lineas.append(
        f"  Bases Tot. Over {res['linea_tb']}: {res['pct_bases_totales']}%"
        f"  (prom {res['prom_bases']} TB/juego)"
        + (f"  [{tag_tb}]" if tag_tb else "")
    )
    return "\n".join(lineas)


def construir_reporte(partidos_analizados: list) -> str:
    hoy    = datetime.date.today().strftime("%d/%m/%Y")
    sep    = "-" * 40
    sep_sm = "-" * 30
    modo   = "DEMO (emergencia)" if DEMO_MODE else f"REAL (MLB API {TEMPORADA})"

    secciones = [
        "MLB PLAYER PROPS - ANALISIS DEL DIA",
        f"Fecha: {hoy}",
        f"Modo: {modo}",
        f"Lineas: K Over {PROP_LINES['strikeouts']} | H Perm Under {PROP_LINES['hits_allowed']}"
        f" | Hits Over {PROP_LINES['hits_totales']} | TB Over {PROP_LINES['bases_totales']}",
        f"[ALTA CONSISTENCIA] = 80%+  |  [CONSISTENTE] = 70-79%",
        sep,
    ]

    for partido in partidos_analizados:
        local_n    = partido["local"][0]
        visitante_n = partido["visitante"][0]
        local_a    = partido["local"][1]
        visitante_a = partido["visitante"][1]

        secciones.append(
            f"PARTIDO: {visitante_n} ({visitante_a}) en {local_n} ({local_a})"
        )
        secciones.append(sep_sm)

        if partido["resultados_pitchers"]:
            secciones.append("-- LANZADORES --")
            for res in partido["resultados_pitchers"]:
                secciones.append(formatear_prop_pitcher(res))
                secciones.append("")

        if partido["resultados_bateadores"]:
            secciones.append("-- BATEADORES DESTACADOS --")
            for res in partido["resultados_bateadores"]:
                secciones.append(formatear_prop_bateador(res))
                secciones.append("")

        if not partido["resultados_pitchers"] and not partido["resultados_bateadores"]:
            secciones.append("Sin datos suficientes para este partido.")
            secciones.append("")

        secciones.append(sep)

    secciones.append("Reporte generado por mlb_bot.py")
    secciones.append("Fuente: MLB Stats API (statsapi.mlb.com)")
    secciones.append("Apuesta con responsabilidad.")

    return "\n".join(secciones)

# ==============================================================================
# ENVIO A TELEGRAM
# ==============================================================================

def enviar_telegram(texto: str) -> bool:
    """POST directo sin parse_mode — cero riesgo de Error 400."""
    url     = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": texto}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("[OK] Mensaje enviado a Telegram.")
            return True
        print(f"[ERROR Telegram] {resp.status_code} - {resp.text[:200]}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"[ERROR Telegram] Conexion: {e}")
        return False


def enviar_en_partes(texto: str, limite: int = 4096) -> bool:
    """Divide el reporte en chunks respetando el limite de Telegram."""
    if len(texto) <= limite:
        return enviar_telegram(texto)

    partes, parte_act, chars_act = [], [], 0
    for linea in texto.split("\n"):
        c = len(linea) + 1
        if chars_act + c > limite:
            partes.append("\n".join(parte_act))
            parte_act, chars_act = [linea], c
        else:
            parte_act.append(linea)
            chars_act += c
    if parte_act:
        partes.append("\n".join(parte_act))

    print(f"[INFO] Reporte dividido en {len(partes)} partes.")
    exito = True
    for i, parte in enumerate(partes, 1):
        print(f"[INFO] Enviando parte {i}/{len(partes)}...")
        if not enviar_telegram(f"[{i}/{len(partes)}]\n{parte}"):
            exito = False
    return exito

# ==============================================================================
# PIPELINE PRINCIPAL
# ==============================================================================

def run():
    print("=" * 50)
    print("MLB PLAYER PROPS BOT - INICIANDO")
    print("=" * 50)

    # PASO 1: Obtener partidos del dia
    print(f"[PASO 1] Obteniendo partidos del dia ({datetime.date.today()})...")
    if DEMO_MODE:
        partidos_raw = obtener_partidos_del_dia_demo()
        print(f"         {len(partidos_raw)} partidos en modo DEMO.")
    else:
        partidos_raw = obtener_partidos_del_dia_real()
        if not partidos_raw:
            print("         Sin partidos en la API. Usando fallback DEMO.")
            partidos_raw = obtener_partidos_del_dia_demo()
        else:
            print(f"         {len(partidos_raw)} partidos obtenidos de MLB API.")

    # PASO 2: Analizar props por partido
    print("[PASO 2] Consultando gameLog de jugadores en MLB API...")
    partidos_analizados = []

    for partido in partidos_raw:
        local_n    = partido["local"][0]
        visitante_n = partido["visitante"][0]
        print(f"  Partido: {visitante_n} @ {local_n}")

        resultados_pitchers   = []
        resultados_bateadores = []

        # Analizar lanzadores
        for nombre in partido["pitchers"]:
            if not nombre or nombre == "Por confirmar":
                continue
            print(f"    Pitcher: {nombre}...")
            raw = generar_ultimos_10_juegos_pitcher(nombre)
            if raw["juegos"]:
                res = calcular_consistencia_pitcher(raw)
                if res:
                    resultados_pitchers.append(res)
                    print(f"      K Over {PROP_LINES['strikeouts']}: {res['pct_strikeouts']}% | "
                          f"H Under {PROP_LINES['hits_allowed']}: {res['pct_hits_under']}%"
                          f" ({res['n_juegos']} juegos)")

        # Analizar bateadores — filtrar solo los con consistencia >= UMBRAL_BUENA
        for nombre in partido["bateadores"]:
            if not nombre:
                continue
            print(f"    Bateador: {nombre}...")
            raw = generar_ultimos_10_juegos_bateador(nombre)
            if raw["juegos"]:
                res = calcular_consistencia_bateador(raw)
                if res and (res["pct_hits"] >= UMBRAL_BUENA or
                            res["pct_bases_totales"] >= UMBRAL_BUENA):
                    resultados_bateadores.append(res)
                    print(f"      Hits Over {PROP_LINES['hits_totales']}: {res['pct_hits']}% | "
                          f"TB Over {PROP_LINES['bases_totales']}: {res['pct_bases_totales']}%"
                          f" ({res['n_juegos']} juegos)  [INCLUIDO]")

        partidos_analizados.append({
            "local":                 partido["local"],
            "visitante":             partido["visitante"],
            "resultados_pitchers":   resultados_pitchers,
            "resultados_bateadores": resultados_bateadores,
        })

    total_p = sum(len(p["resultados_pitchers"])   for p in partidos_analizados)
    total_b = sum(len(p["resultados_bateadores"]) for p in partidos_analizados)
    print(f"\n[PASO 2 OK] Lanzadores con datos: {total_p} | "
          f"Bateadores con consistencia >= {UMBRAL_BUENA}%: {total_b}")

    # PASO 3: Construir reporte
    print("[PASO 3] Construyendo reporte en texto plano...")
    reporte = construir_reporte(partidos_analizados)
    print(f"         {len(reporte)} caracteres generados.")

    # Preview en consola
    print("\n" + "=" * 50)
    print("PREVIEW:")
    print("=" * 50)
    print(reporte)
    print("=" * 50 + "\n")

    # PASO 4: Enviar a Telegram
    print("[PASO 4] Enviando a Telegram...")
    if TOKEN == "TU_TOKEN_AQUI" or CHAT_ID == "TU_CHAT_ID_AQUI":
        print("[AVISO] Configura TOKEN y CHAT_ID al inicio del script.")
        print("        El reporte fue generado correctamente (ver preview).")
    else:
        ok = enviar_en_partes(reporte)
        print("[LISTO] Pipeline completado." if ok
              else "[ALERTA] Reporte generado pero hubo errores en el envio.")

    return reporte


# ==============================================================================
# PUNTO DE ENTRADA
# ==============================================================================

if __name__ == "__main__":
    run()
