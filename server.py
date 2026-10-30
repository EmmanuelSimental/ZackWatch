#!/usr/bin/env python3
"""Servidor local para ver partidas de chess.com de TheZackES.

- Vigila la cuenta y carga automáticamente la última partida (y cada una nueva
  al terminar), con replay jugada a jugada.
- Recibe estado EN VIVO desde el bookmarklet que corre en la pestaña de
  chess.com mientras juegas (POST /api/push).

Uso:  .venv/bin/python server.py   →  http://localhost:8787
"""

# ============================================================================
# IMPORTS
# ============================================================================
import re           # expresiones regulares (para extraer el ID de partida de una URL)
import threading    # hilos: el vigilante de partidas corre en paralelo al servidor web
import time         # pausas (sleep) y marcas de tiempo (time())
from datetime import datetime, timezone   # fecha actual en UTC, para saber qué mes consultar

import chess        # librería python-chess: tablero, jugadas legales, FEN, SAN
import requests     # peticiones HTTP a la API de chess.com
from flask import Flask, jsonify, request, send_from_directory   # el micro-framework web

from watch import TCN, decode_tcn  # mismo decodificador TCN del visor de terminal

# ============================================================================
# CONFIGURACIÓN
# ============================================================================
USERNAME = "thezackes"   # usuario de chess.com que se vigila
PORT = 8787              # puerto donde escucha el servidor local

# Cabeceras HTTP que se mandan a chess.com (se presenta como un navegador normal)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",   # pedimos respuestas en JSON
}

# La aplicación Flask; static_folder="static" sirve los archivos de esa carpeta
app = Flask(__name__, static_folder="static")

# Estado global compartido entre el hilo vigilante y las rutas web
state = {
    "live": None,    # estado empujado por el bookmarklet
    "latest": None,  # última partida terminada, procesada
    "status": "iniciando…",   # texto de estado que ve la página
}
# Candado para que los dos hilos no lean/escriban "state" al mismo tiempo
lock = threading.Lock()


def fetch_callback(game_id: str) -> dict | None:
    """Descarga el JSON de una partida desde el endpoint callback de chess.com."""
    # Petición GET al endpoint interno de chess.com con el ID de la partida
    r = requests.get(
        f"https://www.chess.com/callback/live/game/{game_id}",
        headers=HEADERS, timeout=10,   # timeout de 10 s para no colgarse
    )
    # 404 = la partida aún no está publicada en ese endpoint
    if r.status_code == 404:
        return None
    # Cualquier otro error HTTP (500, 403…) lanza excepción
    r.raise_for_status()
    # Respuesta correcta: devuelve el JSON ya parseado como dict
    return r.json()


def process_game(game_id: str, data: dict) -> dict:
    """Convierte el JSON del callback en algo listo para la página: FEN por jugada."""
    # Sub-dict con los datos de la partida en sí
    game = data["game"]
    # Sub-dict con los dos jugadores, etiquetados "top" y "bottom"
    players = data.get("players", {})
    top, bottom = players.get("top", {}), players.get("bottom", {})
    # Averigua cuál de los dos juega con blancas mirando su campo "color"
    white = bottom if bottom.get("color") == "white" else top
    # El otro es el de negras
    black = top if white is bottom else bottom

    # Tablero en posición inicial para reproducir la partida completa
    board = chess.Board()
    # Lista de posiciones FEN: una por cada momento de la partida (la inicial incluida)
    fens = [board.fen()]
    # Lista de jugadas en notación SAN legible ("Nf3", "exd5"…)
    sans = []
    # Decodifica el moveList TCN de chess.com a jugadas UCI y las reproduce una a una
    for u in decode_tcn(game.get("moveList", "")):
        mv = chess.Move.from_uci(u)      # convierte "e2e4" a objeto jugada
        sans.append(board.san(mv))       # anota su SAN ANTES de ejecutarla (lo requiere la librería)
        board.push(mv)                   # ejecuta la jugada en el tablero
        fens.append(board.fen())         # guarda la posición resultante

    # Los relojes vienen como texto "600,598,595,…" en décimas de segundo; se parsean a enteros
    clocks = [int(t) for t in game.get("moveTimestamps", "").split(",") if t]

    # Empaqueta todo en el formato que espera la página
    return {
        "id": game_id,   # ID de la partida
        "white": {"name": white.get("username", "?"), "rating": white.get("rating")},   # nombre y rating de blancas
        "black": {"name": black.get("username", "?"), "rating": black.get("rating")},   # nombre y rating de negras
        "sans": sans,     # todas las jugadas en SAN
        "fens": fens,     # todas las posiciones FEN
        "clocks": clocks,  # décimas de segundo restantes, por jugada (blancas, negras, …)
        "result": game.get("resultMessage", ""),        # texto del resultado
        "finished": game.get("isFinished", False),      # ¿ya terminó?
        "timeControl": game.get("timeControl", ""),     # control de tiempo (ej. "180")
        "endTime": game.get("endTime"),                 # cuándo terminó
        "userIsBlack": str(black.get("username", "")).lower() == USERNAME,   # ¿nuestro usuario jugó con negras? (para orientar el tablero)
    }


def poll_archives():
    """Hilo que vigila el archivo del usuario y carga cada partida nueva."""
    # Bucle infinito: consulta, procesa, duerme 10 s, repite
    while True:
        try:
            # Fecha actual en UTC (la API de chess.com organiza partidas por mes)
            now = datetime.now(timezone.utc)
            # Mes actual…
            months = [(now.year, now.month)]
            # …y el mes anterior (por si acaba de cambiar el mes y aún no hay partidas)
            prev = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
            months.append(prev)

            games = []   # aquí quedarán las partidas del mes que tenga datos
            # Prueba el mes actual primero; si está vacío, el anterior
            for y, m in months:
                r = requests.get(
                    f"https://api.chess.com/pub/player/{USERNAME}/games/{y}/{m:02d}",   # API pública mensual
                    headers=HEADERS, timeout=10,
                )
                if r.ok:
                    games = r.json().get("games", [])   # lista de partidas del mes
                if games:
                    break   # ya hay partidas: no hace falta mirar el mes anterior

            if games:
                # La API devuelve las partidas en orden cronológico: la última es la más reciente
                last = games[-1]
                # Extrae el ID numérico de la URL de la partida
                gid = re.search(r"(\d{6,})", last["url"]).group(1)
                # Lee (bajo candado) cuál es la última partida que ya teníamos cargada
                with lock:
                    known = state["latest"]["id"] if state["latest"] else None
                # ¿Es una partida nueva que no hemos procesado?
                if gid != known:
                    data = fetch_callback(gid)   # descarga sus datos completos
                    if data:
                        processed = process_game(gid, data)   # los convierte al formato de la página
                        # Guarda la partida procesada en el estado compartido
                        with lock:
                            state["latest"] = processed
                            state["status"] = "ok"
                        print(f"[poller] Nueva partida cargada: {gid} — {processed['result']}")   # log en consola
                else:
                    # Nada nuevo: solo confirma que todo va bien
                    with lock:
                        state["status"] = "ok"
        except Exception as e:
            # Cualquier error (red caída, API cambiada…): se registra y se sigue intentando
            with lock:
                state["status"] = f"error consultando chess.com: {e}"
            print(f"[poller] {e}")
        # Espera 10 segundos antes de la siguiente consulta
        time.sleep(10)


@app.get("/")
def index():
    """Ruta raíz: entrega la página principal (static/index.html)."""
    return send_from_directory("static", "index.html")


@app.get("/api/state")
def api_state():
    """La página consulta esta ruta cada segundo: devuelve todo el estado actual."""
    with lock:   # lee el estado bajo candado
        live = state["live"]   # lo último que empujó el bookmarklet
        # el feed en vivo caduca si el bookmarklet lleva >10 s sin reportar
        if live and time.time() - live["received"] > 10:
            live = None
        # Responde con el estado completo en JSON
        return jsonify({
            "username": USERNAME,                     # a quién se vigila
            "isLive": live is not None,               # booleano: ¿hay partida en vivo ahora mismo?
            "canUseStockfish": live is None,          # fair play: sin motor mientras hay partida en vivo
            "live": live,                             # datos en vivo (fen, relojes, color) o None
            "latest": state["latest"],                # última partida terminada procesada
            "status": state["status"],                # texto de estado del vigilante
            "now": time.time(),                       # hora del servidor (por si la página la necesita)
        })


@app.route("/api/push", methods=["POST", "OPTIONS"])
def api_push():
    """Recibe la posición en vivo que manda el bookmarklet desde chess.com."""
    # OPTIONS = preflight de CORS del navegador: responde vacío pero con permisos
    if request.method == "OPTIONS":
        resp = app.make_response("")
    else:
        try:
            # Lee el cuerpo como JSON (force=True porque el bookmarklet manda Content-Type text/plain)
            payload = request.get_json(force=True, silent=True) or {}
            # Extrae el FEN como texto
            fen = str(payload.get("fen", ""))
            chess.Board(fen)  # valida el FEN; lanza si es basura
            # playingAs: 1 = blancas, 2 = negras; cualquier otra cosa se descarta
            try:
                playing_as = int(payload.get("playingAs") or 0)
            except (TypeError, ValueError):
                playing_as = 0
            # Guarda el estado en vivo bajo candado
            with lock:
                state["live"] = {
                    "fen": fen,   # la posición actual
                    "clocks": [str(c)[:12] for c in payload.get("clocks", [])[:2]],   # máx 2 relojes, recortados a 12 caracteres (sanea la entrada)
                    "playingAs": playing_as if playing_as in (1, 2) else None,        # color validado o None
                    "received": time.time(),   # cuándo llegó (para la caducidad de 10 s)
                }
            resp = jsonify({"ok": True})   # confirma la recepción
        except Exception as e:
            # FEN inválido u otro problema: responde 400 con el error
            resp = jsonify({"ok": False, "error": str(e)})
            resp.status_code = 400
    # Cabeceras CORS: el bookmarklet manda desde chess.com (otro origen) y el navegador lo exige
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


# Punto de entrada: solo corre si se ejecuta directamente (no al importarlo)
if __name__ == "__main__":
    # Arranca el hilo vigilante en segundo plano (daemon = muere con el programa)
    threading.Thread(target=poll_archives, daemon=True).start()
    # Anuncia la URL en consola
    print(f"Visor listo → http://localhost:{PORT}")
    # Arranca el servidor web, solo accesible desde esta máquina (127.0.0.1)
    app.run(host="127.0.0.1", port=PORT)
