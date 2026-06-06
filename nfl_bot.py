"""
╔══════════════════════════════════════════════════════════════╗
║              NFL BOT - ANÁLISIS DE APUESTAS                  ║
║          Configurado para Google Colab + Telegram            ║
╚══════════════════════════════════════════════════════════════╝

INSTRUCCIONES DE USO EN GOOGLE COLAB:
1. Instalar dependencias: !pip install requests
2. Editar la sección CONFIG con tu TOKEN y CHAT_ID de Telegram
3. Ejecutar: exec(open('nfl_bot.py').read())  o simplemente correr la celda
"""

import requests
import json
from datetime import datetime, date
from statistics import mean, stdev

# ════════════════════════════════════════════════════════════════
#  CONFIG — EDITA ESTOS VALORES ANTES DE EJECUTAR
# ════════════════════════════════════════════════════════════════

CONFIG = {
    "TELEGRAM_TOKEN": "8697845783:AAHE_CfzGJY144FBlBrnbbInNJ1B1-frz64",       # Ej: "7234567890:AAFxxx..."
    "TELEGRAM_CHAT_ID": "-1003784545811",        # Ej: "-1001234567890" o "123456789"
    "UMBRAL_EDGE_HIGH": 3.5,    # Puntos mínimos de ventaja para alerta HIGH
    "FACTOR_LOCALÍA": 2.5,      # Puntos de ventaja por jugar en casa
    "MODO_DEMO": True,          # True = usa datos de demostración estructurados
    "TEMPORADA": "2025",
    "SEMANA": "Pre-Temporada / Demo",
}

# ════════════════════════════════════════════════════════════════
#  DATOS DE DEMOSTRACIÓN — Estructura real de temporada NFL
#  Reemplazar con API real (ESPN, The Odds API, etc.) en producción
# ════════════════════════════════════════════════════════════════

JUEGOS_DEMO = [
    {
        "id": "game_001",
        "local": "Kansas City Chiefs",
        "visitante": "Buffalo Bills",
        "spread_mercado": -3.0,          # Chiefs favoritos por 3
        "stats_local": {
            "ppg_favor": 29.4,           # Puntos por partido a favor
            "ppg_contra": 19.1,          # Puntos por partido en contra
            "registro": "14-3",
            "forma": [1, 1, 0, 1, 1],   # 1=victoria, 0=derrota (últimos 5)
        },
        "stats_visitante": {
            "ppg_favor": 27.8,
            "ppg_contra": 20.5,
            "registro": "13-4",
            "forma": [1, 0, 1, 1, 0],
        },
    },
    {
        "id": "game_002",
        "local": "San Francisco 49ers",
        "visitante": "Dallas Cowboys",
        "spread_mercado": -6.5,          # 49ers favoritos por 6.5
        "stats_local": {
            "ppg_favor": 26.9,
            "ppg_contra": 18.3,
            "registro": "12-5",
            "forma": [1, 1, 1, 0, 1],
        },
        "stats_visitante": {
            "ppg_favor": 24.1,
            "ppg_contra": 23.7,
            "registro": "10-7",
            "forma": [0, 1, 0, 0, 1],
        },
    },
    {
        "id": "game_003",
        "local": "Baltimore Ravens",
        "visitante": "Cincinnati Bengals",
        "spread_mercado": -4.0,
        "stats_local": {
            "ppg_favor": 28.6,
            "ppg_contra": 21.2,
            "registro": "13-4",
            "forma": [1, 1, 0, 1, 1],
        },
        "stats_visitante": {
            "ppg_favor": 25.3,
            "ppg_contra": 22.8,
            "registro": "9-8",
            "forma": [0, 0, 1, 1, 0],
        },
    },
]

PROPS_JUGADORES_DEMO = [
    # ── QBs ──────────────────────────────────────────────────────
    {
        "nombre": "Patrick Mahomes",
        "equipo": "KC",
        "posicion": "QB",
        "metrica": "Yardas por Pase",
        "linea_mercado": 292.5,
        "ultimos_5": [315, 287, 342, 278, 305],
    },
    {
        "nombre": "Josh Allen",
        "equipo": "BUF",
        "posicion": "QB",
        "metrica": "Yardas por Pase",
        "linea_mercado": 275.5,
        "ultimos_5": [298, 241, 312, 189, 265],
    },
    {
        "nombre": "Lamar Jackson",
        "equipo": "BAL",
        "posicion": "QB",
        "metrica": "Yardas por Pase",
        "linea_mercado": 245.5,
        "ultimos_5": [267, 198, 289, 312, 245],
    },
    # ── RBs ──────────────────────────────────────────────────────
    {
        "nombre": "Christian McCaffrey",
        "equipo": "SF",
        "posicion": "RB",
        "metrica": "Yardas por Tierra",
        "linea_mercado": 78.5,
        "ultimos_5": [112, 67, 95, 88, 71],
    },
    {
        "nombre": "Derrick Henry",
        "equipo": "BAL",
        "posicion": "RB",
        "metrica": "Yardas por Tierra",
        "linea_mercado": 85.5,
        "ultimos_5": [143, 78, 92, 55, 107],
    },
    # ── WRs ──────────────────────────────────────────────────────
    {
        "nombre": "Tyreek Hill",
        "equipo": "MIA",
        "posicion": "WR",
        "metrica": "Yardas por Recepción",
        "linea_mercado": 82.5,
        "ultimos_5": [95, 112, 67, 134, 78],
    },
    {
        "nombre": "CeeDee Lamb",
        "equipo": "DAL",
        "posicion": "WR",
        "metrica": "Yardas por Recepción",
        "linea_mercado": 75.5,
        "ultimos_5": [88, 45, 92, 67, 103],
    },
    {
        "nombre": "Davante Adams",
        "equipo": "LV",
        "posicion": "WR",
        "metrica": "Yardas por Recepción",
        "linea_mercado": 65.5,
        "ultimos_5": [72, 89, 58, 94, 61],
    },
]


# ════════════════════════════════════════════════════════════════
#  MOTOR DE CÁLCULO
# ════════════════════════════════════════════════════════════════

def calcular_spread_proyectado(juego: dict, factor_localía: float) -> dict:
    """
    Proyecta el margen de victoria basado en:
    - Diferencial de PPG (Puntos a Favor - Puntos en Contra de cada equipo)
    - Factor de localía
    
    Fórmula:
      Fuerza_local    = ppg_favor_local    - ppg_contra_local
      Fuerza_visitante = ppg_favor_visit   - ppg_contra_visit
      Spread_proy     = (Fuerza_local - Fuerza_visitante) + factor_localía
      Edge            = Spread_proy - |spread_mercado|  (ajustado por signo)
    """
    sl = juego["stats_local"]
    sv = juego["stats_visitante"]

    fuerza_local = sl["ppg_favor"] - sl["ppg_contra"]
    fuerza_visit = sv["ppg_favor"] - sv["ppg_contra"]

    spread_proy = (fuerza_local - fuerza_visit) + factor_localía

    # spread_mercado negativo = local favorito; positivo = visitante favorito
    # Convertimos a perspectiva del local para comparar
    spread_mkt_local = -juego["spread_mercado"]  # p.ej. -(-3.0) = 3.0 (local gana por 3)

    edge = spread_proy - spread_mkt_local

    alerta = "HIGH 🚨" if abs(edge) >= CONFIG["UMBRAL_EDGE_HIGH"] else "normal"
    direccion = juego["local"] if edge > 0 else juego["visitante"]

    return {
        "spread_proyectado": round(spread_proy, 1),
        "spread_mercado_local": round(spread_mkt_local, 1),
        "edge": round(edge, 1),
        "alerta": alerta,
        "valor_en": direccion,
        "fuerza_local": round(fuerza_local, 1),
        "fuerza_visitante": round(fuerza_visit, 1),
    }


def analizar_prop(jugador: dict) -> dict:
    """
    Calcula métricas de consistencia para un jugador:
    - Promedio en últimos 5 partidos
    - Desviación estándar (consistencia)
    - % de partidos sobre la línea
    - Tendencia (último partido vs promedio)
    - Recomendación: OVER / UNDER / NEUTRAL
    """
    datos = jugador["ultimos_5"]
    linea = jugador["linea_mercado"]
    prom = mean(datos)
    desv = stdev(datos) if len(datos) > 1 else 0
    sobre_linea = sum(1 for x in datos if x > linea)
    pct_over = (sobre_linea / len(datos)) * 100
    tendencia = datos[-1] - prom  # positivo = última actuación mejor que el promedio

    # Lógica de recomendación
    if pct_over >= 60 and prom > linea:
        recomendacion = "OVER 🎯"
    elif pct_over <= 40 and prom < linea:
        recomendacion = "UNDER 🎯"
    else:
        recomendacion = "NEUTRAL"

    consistencia = "Alta" if desv < 25 else "Media" if desv < 45 else "Baja"

    return {
        "promedio": round(prom, 1),
        "desviacion": round(desv, 1),
        "pct_over": round(pct_over, 1),
        "tendencia": round(tendencia, 1),
        "recomendacion": recomendacion,
        "consistencia": consistencia,
    }


def calcular_forma(registro_5: list) -> str:
    """Convierte lista [1,0,1,1,0] en string legible 'G-P-G-G-P'"""
    return "-".join(["G" if x == 1 else "P" for x in registro_5])


# ════════════════════════════════════════════════════════════════
#  GENERADOR DE REPORTE (TEXTO PLANO — SIN MARKDOWN)
# ════════════════════════════════════════════════════════════════

def generar_reporte(juegos: list, props: list) -> str:
    hoy = datetime.now().strftime("%d/%m/%Y %H:%M")
    lineas = []

    # ── ENCABEZADO ───────────────────────────────────────────────
    lineas += [
        "================================================",
        "🏈 NFL BOT — REPORTE DIARIO DE APUESTAS",
        f"Fecha: {hoy}",
        f"Temporada {CONFIG['TEMPORADA']} | {CONFIG['SEMANA']}",
        "================================================",
        "",
    ]

    # ── SECCIÓN 1: ANÁLISIS DE SPREADS ───────────────────────────
    lineas += [
        "------------------------------------------------",
        "📊 ANALISIS DE SPREADS (HANDICAPS)",
        "------------------------------------------------",
        "",
    ]

    alertas_high = []

    for juego in juegos:
        res = calcular_spread_proyectado(juego, CONFIG["FACTOR_LOCALÍA"])

        lineas += [
            f"🏈 {juego['visitante']} @ {juego['local']}",
            f"   Spread Mercado : {juego['spread_mercado']:+.1f} ({juego['local']})",
            f"   Spread Modelo  : {res['spread_proyectado']:+.1f} ({juego['local']})",
            f"   Fuerza Local   : {res['fuerza_local']:+.1f} pts netos",
            f"   Fuerza Visita  : {res['fuerza_visitante']:+.1f} pts netos",
            f"   Edge (Ventaja) : {res['edge']:+.1f} pts",
            f"   Forma Local    : {calcular_forma(juego['stats_local']['forma'])}  ({juego['stats_local']['registro']})",
            f"   Forma Visita   : {calcular_forma(juego['stats_visitante']['forma'])}  ({juego['stats_visitante']['registro']})",
            f"   Alerta         : {res['alerta']}",
        ]

        if res["alerta"].startswith("HIGH"):
            lineas.append(f"   Valor en       : {res['valor_en']}")
            alertas_high.append({
                "partido": f"{juego['visitante']} @ {juego['local']}",
                "valor_en": res["valor_en"],
                "edge": res["edge"],
            })

        lineas.append("")

    # ── SECCIÓN 2: PROPS DE JUGADORES ────────────────────────────
    lineas += [
        "------------------------------------------------",
        "🎯 PROPS DE JUGADORES — ULTIMOS 5 PARTIDOS",
        "------------------------------------------------",
        "",
    ]

    grupos = {"QB": [], "RB": [], "WR": []}
    for j in props:
        grupos[j["posicion"]].append(j)

    etiquetas = {"QB": "QUARTERBACKS — Yardas por Pase",
                 "RB": "RUNNING BACKS — Yardas por Tierra",
                 "WR": "WIDE RECEIVERS — Yardas por Recepcion"}

    for pos, jugadores in grupos.items():
        if not jugadores:
            continue
        lineas += [f"[ {etiquetas[pos]} ]", ""]
        for j in jugadores:
            r = analizar_prop(j)
            ultimos = " / ".join(str(x) for x in j["ultimos_5"])
            lineas += [
                f"  {j['nombre']} ({j['equipo']})",
                f"  Linea Mercado  : {j['linea_mercado']} yds",
                f"  Promedio L5    : {r['promedio']} yds",
                f"  Ultimos 5      : {ultimos}",
                f"  Desviac. Std   : {r['desviacion']} yds  (Consistencia: {r['consistencia']})",
                f"  % Over en L5   : {r['pct_over']}%",
                f"  Tendencia      : {r['tendencia']:+.1f} yds vs promedio",
                f"  Recomendacion  : {r['recomendacion']}",
                "",
            ]

    # ── SECCIÓN 3: RESUMEN DE ALERTAS ────────────────────────────
    lineas += [
        "================================================",
        "🚨 RESUMEN — ALERTAS DE VALOR HIGH",
        "================================================",
        "",
    ]

    if alertas_high:
        for a in alertas_high:
            lineas += [
                f"  PARTIDO : {a['partido']}",
                f"  APUESTA : {a['valor_en']}",
                f"  EDGE    : {a['edge']:+.1f} puntos",
                "",
            ]
    else:
        lineas += ["  Sin alertas HIGH en esta jornada.", ""]

    lineas += [
        "------------------------------------------------",
        "AVISO: Solo con fines informativos y educativos.",
        "Apuesta con responsabilidad.",
        "================================================",
    ]

    return "\n".join(lineas)


# ════════════════════════════════════════════════════════════════
#  ENVÍO A TELEGRAM
# ════════════════════════════════════════════════════════════════

def enviar_telegram(texto: str, token: str, chat_id: str) -> dict:
    """
    Envía mensaje a Telegram usando parse_mode='' (texto plano puro).
    Divide automáticamente si el mensaje supera 4096 caracteres.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    MAX_LEN = 4096
    bloques = [texto[i:i + MAX_LEN] for i in range(0, len(texto), MAX_LEN)]

    resultados = []
    for idx, bloque in enumerate(bloques):
        payload = {
            "chat_id": chat_id,
            "text": bloque,
            # Sin parse_mode = texto plano, evita errores de Markdown
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            data = resp.json()
            if data.get("ok"):
                print(f"  [OK] Bloque {idx + 1}/{len(bloques)} enviado a Telegram")
            else:
                print(f"  [ERROR] Bloque {idx + 1}: {data.get('description', 'Error desconocido')}")
            resultados.append(data)
        except requests.RequestException as e:
            print(f"  [EXCEPCION] Bloque {idx + 1}: {e}")
            resultados.append({"ok": False, "error": str(e)})

    return resultados


def validar_config() -> bool:
    """Verifica que el TOKEN y CHAT_ID no sean los valores placeholder."""
    token = CONFIG["TELEGRAM_TOKEN"]
    chat = CONFIG["TELEGRAM_CHAT_ID"]

    if "AQUI_VA" in token or "AQUI_VA" in chat:
        print("\n[!] ATENCIÓN: Debes configurar TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en CONFIG")
        print("    El reporte se mostrará en consola pero NO se enviará a Telegram.\n")
        return False
    if len(token) < 30 or ":" not in token:
        print("\n[!] El TELEGRAM_TOKEN no parece válido (formato: XXXXXXXXXX:AAAA...)")
        return False
    return True


# ════════════════════════════════════════════════════════════════
#  PUNTO DE ENTRADA PRINCIPAL
# ════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 56)
    print("  🏈 NFL BOT — INICIANDO ANÁLISIS")
    print("=" * 56)

    # Seleccionar fuente de datos
    if CONFIG["MODO_DEMO"]:
        print(f"\n  Modo: DEMOSTRACIÓN (datos estructurados de ejemplo)")
        juegos = JUEGOS_DEMO
        props = PROPS_JUGADORES_DEMO
    else:
        # ── AQUÍ INTEGRAR API REAL (ESPN / The Odds API) ──────────
        # Ejemplo de endpoint para The Odds API:
        # API_KEY = "tu_api_key"
        # url = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/"
        # params = {"apiKey": API_KEY, "regions": "us", "markets": "spreads"}
        # resp = requests.get(url, params=params)
        # juegos = transformar_respuesta_api(resp.json())  # función a implementar
        print("  [!] MODO_DEMO=False pero no hay API conectada. Usando datos demo.")
        juegos = JUEGOS_DEMO
        props = PROPS_JUGADORES_DEMO

    print(f"\n  Juegos a analizar  : {len(juegos)}")
    print(f"  Props de jugadores : {len(props)}")
    print(f"  Umbral Edge HIGH   : >= {CONFIG['UMBRAL_EDGE_HIGH']} puntos")
    print(f"  Factor localía     : +{CONFIG['FACTOR_LOCALÍA']} puntos")

    # Generar reporte
    print("\n  Calculando spreads y analizando props...\n")
    reporte = generar_reporte(juegos, props)

    # Preview en consola
    print("\n" + "=" * 56)
    print("  PREVIEW DEL REPORTE (lo que se enviará a Telegram):")
    print("=" * 56 + "\n")
    print(reporte)

    # Enviar a Telegram
    print("\n" + "=" * 56)
    print("  ENVIANDO A TELEGRAM...")
    print("=" * 56)

    config_ok = validar_config()
    if config_ok:
        resultados = enviar_telegram(
            reporte,
            CONFIG["TELEGRAM_TOKEN"],
            CONFIG["TELEGRAM_CHAT_ID"],
        )
        exitos = sum(1 for r in resultados if r.get("ok"))
        print(f"\n  Resultado: {exitos}/{len(resultados)} bloques enviados correctamente.")
    else:
        print("  (Envío omitido — configura las credenciales en CONFIG)")

    print("\n" + "=" * 56)
    print("  🏈 NFL BOT — FINALIZADO")
    print("=" * 56 + "\n")


# ════════════════════════════════════════════════════════════════
#  EJECUCIÓN
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    main()
else:
    # Soporte para ejecución directa en Google Colab con exec() o run
    main()
