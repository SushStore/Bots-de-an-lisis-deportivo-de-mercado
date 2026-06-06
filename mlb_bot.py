"""
mlb_bot.py - MLB Player Props Consistency Bot
Compatible con Google Colab
Arquitectura CONFIG-first
"""

# ==============================================================================
# CONFIGURACION GLOBAL
# ==============================================================================

TOKEN   = "8697845783:AAHE_CfzGJY144FBlBrnbbInNJ1B1-frz64"       # Token del bot de Telegram
CHAT_ID = "-1003883891563"     # Chat ID del canal/grupo MLB

DEMO_MODE = True                 # True = datos simulados | False = API real

# Lineas de referencia (Player Props - ajustar segun el sportsbook)
PROP_LINES = {
    "strikeouts":     5.5,
    "hits_allowed":   4.5,
    "hits_totales":   1.5,
    "bases_totales":  2.5,
}

# Umbrales de clasificacion
UMBRAL_ALTA     = 80   # >= 80% -> Alta Consistencia
UMBRAL_BUENA    = 70   # 70-79% -> Consistente

# ==============================================================================
# DEPENDENCIAS
# ==============================================================================

import requests
import random
import datetime

# ==============================================================================
# FETCHER - MODO DEMO (datos simulados realistas)
# ==============================================================================

EQUIPOS_MLB = [
    ("New York Yankees", "NYY"), ("Los Angeles Dodgers", "LAD"),
    ("Houston Astros",   "HOU"), ("Atlanta Braves",      "ATL"),
    ("Philadelphia Phillies", "PHI"), ("Texas Rangers",  "TEX"),
    ("Chicago Cubs",     "CHC"), ("San Diego Padres",    "SDP"),
    ("Seattle Mariners", "SEA"), ("Baltimore Orioles",   "BAL"),
    ("Boston Red Sox",   "BOS"), ("New York Mets",       "NYM"),
]

PITCHERS_DEMO = [
    "Gerrit Cole",    "Shohei Ohtani",  "Spencer Strider",
    "Zack Wheeler",   "Logan Webb",     "Framber Valdez",
    "Corbin Burnes",  "Dylan Cease",    "Pablo Lopez",
    "Sonny Gray",     "Hunter Brown",   "Tyler Glasnow",
]

BATEADORES_DEMO = [
    "Freddie Freeman",  "Mookie Betts",    "Aaron Judge",
    "Juan Soto",        "Jose Ramirez",    "Yordan Alvarez",
    "Trea Turner",      "Corey Seager",    "Bo Bichette",
    "Rafael Devers",    "Pete Alonso",     "Julio Rodriguez",
    "Kyle Tucker",      "Adolis Garcia",   "Gunnar Henderson",
]


def generar_ultimos_10_juegos_pitcher(nombre: str) -> dict:
    """Genera estadisticas simuladas de los ultimos 10 juegos para un lanzador."""
    random.seed(hash(nombre) % 9999)
    juegos = []
    for _ in range(10):
        k  = random.randint(2, 11)
        h  = random.randint(1, 9)
        ip = round(random.uniform(3.0, 7.0), 1)
        juegos.append({"strikeouts": k, "hits_allowed": h, "innings": ip})
    return {"nombre": nombre, "tipo": "pitcher", "juegos": juegos}


def generar_ultimos_10_juegos_bateador(nombre: str) -> dict:
    """Genera estadisticas simuladas de los ultimos 10 juegos para un bateador."""
    random.seed(hash(nombre) % 9999 + 1000)
    juegos = []
    for _ in range(10):
        h  = random.randint(0, 4)
        tb = random.randint(0, 6)
        juegos.append({"hits": h, "total_bases": tb})
    return {"nombre": nombre, "tipo": "bateador", "juegos": juegos}


def obtener_partidos_del_dia_demo() -> list:
    """
    Simula los partidos del dia con jugadores representativos.
    Devuelve lista de dicts con pitchers y bateadores por partido.
    """
    random.seed(datetime.date.today().toordinal())
    equipos_hoy = random.sample(EQUIPOS_MLB, k=8)
    partidos    = []

    for i in range(0, len(equipos_hoy), 2):
        local    = equipos_hoy[i]
        visitante = equipos_hoy[i + 1]

        pitcher_l = random.choice(PITCHERS_DEMO)
        pitcher_v = random.choice([p for p in PITCHERS_DEMO if p != pitcher_l])
        bates_l   = random.sample(BATEADORES_DEMO, 3)
        bates_v   = random.sample([b for b in BATEADORES_DEMO if b not in bates_l], 3)

        partidos.append({
            "local":    local,
            "visitante": visitante,
            "pitchers": [pitcher_l, pitcher_v],
            "bateadores": bates_l + bates_v,
        })

    return partidos


# ==============================================================================
# FETCHER - MODO REAL (MLB Stats API publica)
# ==============================================================================

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"


def obtener_partidos_del_dia_real() -> list:
    """
    Consulta la MLB Stats API para los juegos del dia.
    Devuelve estructura compatible con el modo demo.
    """
    hoy = datetime.date.today().strftime("%Y-%m-%d")
    url = f"{MLB_API_BASE}/schedule?sportId=1&date={hoy}&hydrate=probablePitcher,lineScore,team"

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[ERROR] No se pudo conectar a MLB API: {e}")
        print("[INFO] Cambiando a DEMO_MODE automaticamente...")
        return obtener_partidos_del_dia_demo()

    partidos = []
    for fecha in data.get("dates", []):
        for juego in fecha.get("games", []):
            local    = juego["teams"]["home"]["team"]["name"]
            visitante = juego["teams"]["away"]["team"]["name"]
            abr_l    = juego["teams"]["home"]["team"].get("abbreviation", "???")
            abr_v    = juego["teams"]["away"]["team"].get("abbreviation", "???")

            pitcher_l = (juego["teams"]["home"]
                         .get("probablePitcher", {})
                         .get("fullName", "Por confirmar"))
            pitcher_v = (juego["teams"]["away"]
                         .get("probablePitcher", {})
                         .get("fullName", "Por confirmar"))

            partidos.append({
                "local":     (local, abr_l),
                "visitante": (visitante, abr_v),
                "pitchers":  [pitcher_l, pitcher_v],
                "bateadores": [],   # En modo real, bateadores se infieren aparte
            })

    return partidos if partidos else obtener_partidos_del_dia_demo()


# ==============================================================================
# MOTOR DE ANALISIS
# ==============================================================================

def calcular_consistencia_pitcher(stats: dict) -> dict:
    """
    Calcula el porcentaje de Over en Strikeouts e Hits Permitidos
    para los ultimos 10 juegos.
    """
    juegos   = stats["juegos"]
    linea_k  = PROP_LINES["strikeouts"]
    linea_h  = PROP_LINES["hits_allowed"]

    over_k   = sum(1 for j in juegos if j["strikeouts"]  > linea_k)
    over_h   = sum(1 for j in juegos if j["hits_allowed"] < linea_h)   # Under es favorable

    pct_k    = (over_k / len(juegos)) * 100
    pct_h    = (over_h / len(juegos)) * 100

    promedio_k = sum(j["strikeouts"]  for j in juegos) / len(juegos)
    promedio_h = sum(j["hits_allowed"] for j in juegos) / len(juegos)

    return {
        "nombre":        stats["nombre"],
        "tipo":          "pitcher",
        "pct_strikeouts": round(pct_k, 1),
        "pct_hits_under": round(pct_h, 1),
        "prom_k":         round(promedio_k, 1),
        "prom_h":         round(promedio_h, 1),
        "linea_k":        linea_k,
        "linea_h":        linea_h,
        "n_juegos":       len(juegos),
    }


def calcular_consistencia_bateador(stats: dict) -> dict:
    """
    Calcula el porcentaje de Over en Hits Totales y Bases Totales
    para los ultimos 10 juegos.
    """
    juegos   = stats["juegos"]
    linea_h  = PROP_LINES["hits_totales"]
    linea_tb = PROP_LINES["bases_totales"]

    over_h   = sum(1 for j in juegos if j["hits"]        > linea_h)
    over_tb  = sum(1 for j in juegos if j["total_bases"] > linea_tb)

    pct_h    = (over_h  / len(juegos)) * 100
    pct_tb   = (over_tb / len(juegos)) * 100

    promedio_h  = sum(j["hits"]        for j in juegos) / len(juegos)
    promedio_tb = sum(j["total_bases"] for j in juegos) / len(juegos)

    return {
        "nombre":           stats["nombre"],
        "tipo":             "bateador",
        "pct_hits":         round(pct_h,  1),
        "pct_bases_totales": round(pct_tb, 1),
        "prom_hits":        round(promedio_h,  1),
        "prom_bases":       round(promedio_tb, 1),
        "linea_h":          linea_h,
        "linea_tb":         linea_tb,
        "n_juegos":         len(juegos),
    }


def clasificar(pct: float) -> str:
    """Devuelve etiqueta de consistencia segun el porcentaje."""
    if pct >= UMBRAL_ALTA:
        return "ALTA CONSISTENCIA"
    elif pct >= UMBRAL_BUENA:
        return "CONSISTENTE"
    else:
        return ""


# ==============================================================================
# FORMATEADOR DE REPORTE (texto plano, sin Markdown)
# ==============================================================================

def formatear_prop_pitcher(res: dict) -> str:
    """Genera bloque de texto plano para un lanzador."""
    lineas = []
    nombre = res["nombre"].upper()
    lineas.append(f"LANZADOR: {nombre}")
    lineas.append(f"Basado en ultimos {res['n_juegos']} juegos")

    # Strikeouts
    pct_k  = res["pct_strikeouts"]
    tag_k  = clasificar(pct_k)
    lineas.append(
        f"  Ponches (K) Over {res['linea_k']}: {pct_k}%  (prom {res['prom_k']} K/juego)"
        + (f"  [{tag_k}]" if tag_k else "")
    )

    # Hits permitidos (Under)
    pct_h  = res["pct_hits_under"]
    tag_h  = clasificar(pct_h)
    lineas.append(
        f"  Hits Perm. Under {res['linea_h']}: {pct_h}%  (prom {res['prom_h']} H/juego)"
        + (f"  [{tag_h}]" if tag_h else "")
    )

    return "\n".join(lineas)


def formatear_prop_bateador(res: dict) -> str:
    """Genera bloque de texto plano para un bateador."""
    lineas = []
    nombre = res["nombre"].upper()
    lineas.append(f"BATEADOR: {nombre}")
    lineas.append(f"Basado en ultimos {res['n_juegos']} juegos")

    # Hits
    pct_h  = res["pct_hits"]
    tag_h  = clasificar(pct_h)
    lineas.append(
        f"  Hits Over {res['linea_h']}: {pct_h}%  (prom {res['prom_hits']} H/juego)"
        + (f"  [{tag_h}]" if tag_h else "")
    )

    # Bases Totales
    pct_tb = res["pct_bases_totales"]
    tag_tb = clasificar(pct_tb)
    lineas.append(
        f"  Bases Tot. Over {res['linea_tb']}: {pct_tb}%  (prom {res['prom_bases']} TB/juego)"
        + (f"  [{tag_tb}]" if tag_tb else "")
    )

    return "\n".join(lineas)


def construir_reporte(partidos_analizados: list) -> str:
    """
    Construye el reporte completo en texto plano.
    Sin asteriscos, guiones bajos ni ningun caracter Markdown.
    """
    hoy    = datetime.date.today().strftime("%d/%m/%Y")
    sep    = "-" * 40
    sep_sm = "-" * 30

    secciones = []
    secciones.append("MLB PLAYER PROPS - ANALISIS DEL DIA")
    secciones.append(f"Fecha: {hoy}")
    secciones.append(f"Modo: {'DEMO (datos simulados)' if DEMO_MODE else 'REAL (MLB API)'}")
    secciones.append(f"Lineas: K Over {PROP_LINES['strikeouts']} | H Perm Under {PROP_LINES['hits_allowed']} | Hits Over {PROP_LINES['hits_totales']} | TB Over {PROP_LINES['bases_totales']}")
    secciones.append(f"[ALTA CONSISTENCIA] = 80%+ | [CONSISTENTE] = 70-79%")
    secciones.append(sep)

    for partido in partidos_analizados:
        local_nombre    = partido["local"][0]
        visitante_nombre = partido["visitante"][0]
        local_abr       = partido["local"][1]
        visitante_abr   = partido["visitante"][1]

        secciones.append(f"PARTIDO: {visitante_nombre} ({visitante_abr}) en {local_nombre} ({local_abr})")
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

        secciones.append(sep)

    secciones.append("Reporte generado por mlb_bot.py")
    secciones.append("Usa esta informacion como referencia. Apuesta con responsabilidad.")

    return "\n".join(secciones)


# ==============================================================================
# ENVIO A TELEGRAM
# ==============================================================================

def enviar_telegram(texto: str) -> bool:
    """
    Envia el reporte al CHAT_ID usando requests.post directo.
    Sin parse_mode para evitar errores 400 por caracteres especiales.
    """
    url     = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text":    texto,
        # Sin parse_mode: Telegram interpreta el mensaje como texto plano puro
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("[OK] Mensaje enviado a Telegram correctamente.")
            return True
        else:
            print(f"[ERROR] Telegram respondio con status {resp.status_code}")
            print(f"        Detalle: {resp.text}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Fallo la conexion con Telegram: {e}")
        return False


def enviar_en_partes(texto: str, limite: int = 4096) -> bool:
    """
    Telegram tiene un limite de 4096 caracteres por mensaje.
    Esta funcion divide el reporte en partes si es necesario.
    """
    if len(texto) <= limite:
        return enviar_telegram(texto)

    partes    = []
    lineas    = texto.split("\n")
    parte_act = []
    chars_act = 0

    for linea in lineas:
        chars_linea = len(linea) + 1   # +1 por el \n
        if chars_act + chars_linea > limite:
            partes.append("\n".join(parte_act))
            parte_act = [linea]
            chars_act = chars_linea
        else:
            parte_act.append(linea)
            chars_act += chars_linea

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
    """Funcion principal que orquesta todo el pipeline."""

    print("=" * 50)
    print("MLB PLAYER PROPS BOT - INICIANDO")
    print("=" * 50)

    # 1. Obtener partidos del dia
    print("[PASO 1] Obteniendo partidos del dia...")
    if DEMO_MODE:
        partidos_raw = obtener_partidos_del_dia_demo()
        print(f"         {len(partidos_raw)} partidos cargados en modo DEMO.")
    else:
        partidos_raw = obtener_partidos_del_dia_real()
        print(f"         {len(partidos_raw)} partidos obtenidos de MLB API.")

    # 2. Analizar props por partido
    print("[PASO 2] Analizando Player Props...")
    partidos_analizados = []

    for partido in partidos_raw:
        resultados_pitchers   = []
        resultados_bateadores = []

        # Analizar lanzadores
        for nombre in partido["pitchers"]:
            if nombre and nombre != "Por confirmar":
                raw   = generar_ultimos_10_juegos_pitcher(nombre)
                res   = calcular_consistencia_pitcher(raw)
                resultados_pitchers.append(res)

        # Analizar bateadores (solo los que tienen alta/buena consistencia en al menos un prop)
        for nombre in partido["bateadores"]:
            raw = generar_ultimos_10_juegos_bateador(nombre)
            res = calcular_consistencia_bateador(raw)
            if (res["pct_hits"] >= UMBRAL_BUENA or
                    res["pct_bases_totales"] >= UMBRAL_BUENA):
                resultados_bateadores.append(res)

        partidos_analizados.append({
            "local":                  partido["local"],
            "visitante":              partido["visitante"],
            "resultados_pitchers":    resultados_pitchers,
            "resultados_bateadores":  resultados_bateadores,
        })

    total_pitchers   = sum(len(p["resultados_pitchers"])   for p in partidos_analizados)
    total_bateadores = sum(len(p["resultados_bateadores"]) for p in partidos_analizados)
    print(f"         Lanzadores analizados: {total_pitchers}")
    print(f"         Bateadores con consistencia >= {UMBRAL_BUENA}%: {total_bateadores}")

    # 3. Construir reporte en texto plano
    print("[PASO 3] Construyendo reporte...")
    reporte = construir_reporte(partidos_analizados)
    print(f"         Reporte generado ({len(reporte)} caracteres).")

    # 4. Preview en consola
    print("\n" + "=" * 50)
    print("PREVIEW DEL REPORTE:")
    print("=" * 50)
    print(reporte)
    print("=" * 50 + "\n")

    # 5. Enviar a Telegram
    print("[PASO 4] Enviando a Telegram...")
    if TOKEN == "TU_TOKEN_AQUI" or CHAT_ID == "TU_CHAT_ID_AQUI":
        print("[AVISO] TOKEN o CHAT_ID no configurados.")
        print("        Edita las variables globales al inicio del script y vuelve a ejecutar.")
        print("        El reporte fue generado correctamente (ver preview arriba).")
    else:
        exito = enviar_en_partes(reporte)
        if exito:
            print("[LISTO] Pipeline completado exitosamente.")
        else:
            print("[ALERTA] El reporte se genero pero hubo errores en el envio.")

    return reporte


# ==============================================================================
# PUNTO DE ENTRADA
# ==============================================================================

if __name__ == "__main__":
    run()
