#!/usr/bin/env python3
"""Visor en tiempo real de una partida de chess.com.

Uso:
    python watch.py <url-o-id-de-partida> [--user <tu_usuario>] [--interval 2]

Ejemplo:
    python watch.py https://www.chess.com/game/live/186167189226 --user emm
"""

# ============================================================================
# IMPORTS
# ============================================================================
import argparse   # parseo de argumentos de línea de comandos (--user, --interval…)
import os         # os.system("clear") para limpiar la terminal
import re         # expresiones regulares (extraer el ID de la URL)
import sys        # salida con código de error y escritura a stderr
import time       # pausas entre consultas

import chess      # librería python-chess: tablero, jugadas, FEN, SAN
import requests   # peticiones HTTP a chess.com

# Codificación TCN que usa chess.com para moveList
# Cada carácter representa un índice de casilla (0-63) o un código de promoción
TCN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!?{~}(^)[_]@#$,./&-*++="
# Orden de las piezas de promoción en el código TCN: dama, caballo, torre, alfil…
PROMO = "qnrbkp"

# Cabeceras HTTP: nos presentamos como un navegador normal y pedimos JSON
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}

# Símbolos Unicode para dibujar las piezas en la terminal
PIECES = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",   # piezas blancas
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",   # piezas negras
}


def decode_tcn(move_list: str) -> list[str]:
    """Decodifica el moveList TCN de chess.com a movimientos UCI."""
    moves = []   # resultado: lista de jugadas tipo "e2e4"
    # Cada jugada ocupa 2 caracteres: origen y destino
    for i in range(0, len(move_list), 2):
        o = TCN.index(move_list[i])       # índice de la casilla de ORIGEN (0-63)
        s = TCN.index(move_list[i + 1])   # índice de la casilla de DESTINO (o código de promoción si >63)
        promotion = ""   # letra de promoción a añadir al final (si la hay)
        if s > 63:  # promoción
            promotion = PROMO[(s - 64) // 3]   # qué pieza corona (grupos de 3 códigos por pieza)
            s = o + (-8 if o < 16 else 8) + (s - 1) % 3 - 1   # reconstruye la casilla de destino real (una fila adelante, con posible captura en diagonal)
        frm = TCN[o % 8] + str(o // 8 + 1)   # casilla de origen en algebraico: columna (a-h) + fila (1-8)
        to = TCN[s % 8] + str(s // 8 + 1)    # casilla de destino en algebraico
        moves.append(frm + to + promotion)   # jugada UCI completa, ej. "e7e8q"
    return moves


def game_id_from_arg(arg: str) -> str:
    """Extrae el ID numérico de partida de una URL o texto."""
    m = re.search(r"(\d{6,})", arg)   # busca un número de 6+ dígitos
    if not m:
        sys.exit(f"No pude extraer un ID de partida de: {arg}")   # sin ID no se puede seguir
    return m.group(1)   # devuelve solo el número


def fetch(game_id: str) -> dict | None:
    """Devuelve el JSON de la partida, o None si aún no está disponible (404)."""
    # GET al endpoint callback de chess.com
    r = requests.get(
        f"https://www.chess.com/callback/live/game/{game_id}",
        headers=HEADERS,
        timeout=10,   # no colgarse más de 10 segundos
    )
    # 404 = chess.com todavía no publica los datos de esta partida
    if r.status_code == 404:
        return None
    # Otros errores HTTP lanzan excepción
    r.raise_for_status()
    # Respuesta correcta: JSON parseado
    return r.json()


def fmt_clock(tenths: int) -> str:
    """Convierte décimas de segundo a formato m:ss."""
    s = tenths / 10            # décimas → segundos
    m, s = divmod(int(s), 60)  # separa minutos y segundos
    return f"{m}:{s:02d}"      # "3:05"


def render(data: dict, user: str | None) -> str:
    """Construye el texto completo que se imprime en la terminal."""
    game = data["game"]                      # datos de la partida
    players = data.get("players", {})        # los dos jugadores
    top = players.get("top", {})             # jugador mostrado arriba por chess.com
    bottom = players.get("bottom", {})       # jugador mostrado abajo

    # ¿De qué color juega cada uno?
    white = bottom if bottom.get("color") == "white" else top   # el que tenga color "white" es blancas
    black = top if white is bottom else bottom                  # el otro es negras

    uci_moves = decode_tcn(game.get("moveList", ""))   # decodifica todas las jugadas
    board = chess.Board()   # tablero en posición inicial
    sans = []               # jugadas en notación SAN
    # Reproduce la partida jugada a jugada para obtener el tablero final y los SAN
    for u in uci_moves:
        mv = chess.Move.from_uci(u)   # texto UCI → objeto jugada
        sans.append(board.san(mv))    # anota el SAN antes de mover
        board.push(mv)                # ejecuta la jugada

    # Relojes: moveTimestamps son décimas de segundo restantes por jugada
    ts = [int(t) for t in game.get("moveTimestamps", "").split(",") if t]   # parsea la lista de relojes
    base = game.get("baseTime1", 0)   # tiempo inicial de la partida (para cuando faltan datos)
    w_clock = fmt_clock(ts[-2] if len(ts) >= 2 else (ts[0] if ts and len(ts) % 2 else base))   # reloj de blancas: penúltimo registro
    b_clock = fmt_clock(ts[-1] if len(ts) >= 2 else base)   # reloj de negras: último registro
    if len(ts) == 1:   # caso especial: solo se ha hecho una jugada
        w_clock, b_clock = fmt_clock(ts[0]), fmt_clock(base)

    # Orientación: si --user coincide con negras, girar el tablero
    flip = bool(user) and user.lower() == str(black.get("username", "")).lower()

    lines = []   # líneas de texto que formarán la salida
    w_name = f"{white.get('username','?')} ({white.get('rating','?')})"   # "nombre (rating)" de blancas
    b_name = f"{black.get('username','?')} ({black.get('rating','?')})"   # "nombre (rating)" de negras

    header_top = f"{b_name}  ⏱ {b_clock}" if not flip else f"{w_name}  ⏱ {w_clock}"   # quién va arriba según la orientación
    header_bot = f"{w_name}  ⏱ {w_clock}" if not flip else f"{b_name}  ⏱ {b_clock}"   # quién va abajo

    lines.append(header_top)   # primera línea: jugador de arriba con su reloj
    ranks = range(7, -1, -1) if not flip else range(8)    # orden de filas según orientación
    files = range(8) if not flip else range(7, -1, -1)    # orden de columnas según orientación
    # Dibuja el tablero fila por fila
    for r in ranks:
        row = [str(r + 1)]   # la fila empieza con su número (1-8)
        for f in files:
            piece = board.piece_at(chess.square(f, r))               # pieza en esa casilla (o None)
            row.append(PIECES[piece.symbol()] if piece else "·")     # símbolo Unicode o punto si está vacía
        lines.append(" ".join(row))   # une la fila con espacios
    file_labels = "abcdefgh" if not flip else "hgfedcba"   # letras de las columnas según orientación
    lines.append("  " + " ".join(file_labels))   # línea con las letras bajo el tablero
    lines.append(header_bot)   # jugador de abajo con su reloj
    lines.append("")           # línea en blanco separadora

    # Lista de jugadas en SAN
    pretty = []   # pares "1. e4 e5"
    for i in range(0, len(sans), 2):
        num = i // 2 + 1           # número de la jugada completa
        pair = f"{num}. {sans[i]}" # jugada de blancas con número
        if i + 1 < len(sans):
            pair += f" {sans[i+1]}"   # jugada de negras si existe
        pretty.append(pair)
    lines.append(" ".join(pretty[-12:]))  # últimas 12 jugadas completas
    lines.append("")   # otra línea en blanco

    # Pie: resultado final o a quién le toca mover
    if game.get("isFinished"):
        lines.append(f"🏁 {game.get('resultMessage', 'Partida terminada')}")   # la partida terminó
    else:
        turn = "blancas" if board.turn == chess.WHITE else "negras"   # ¿quién mueve?
        lines.append(f"Jugada {len(sans)} — mueven {turn}")
    return "\n".join(lines)   # todo el texto unido con saltos de línea


def main():
    """Punto de entrada: parsea argumentos y entra al bucle de vigilancia."""
    # Define los argumentos de línea de comandos
    ap = argparse.ArgumentParser(description="Visor en vivo de partidas de chess.com")
    ap.add_argument("game", help="URL o ID de la partida (p.ej. https://www.chess.com/game/live/123456789)")   # obligatorio
    ap.add_argument("--user", help="Tu usuario de chess.com, para orientar el tablero desde tu lado")           # opcional
    ap.add_argument("--interval", type=float, default=2.0, help="Segundos entre consultas (default 2)")         # frecuencia de refresco
    ap.add_argument("--once", action="store_true", help="Mostrar el estado actual una sola vez y salir")        # modo una-sola-vez
    ap.add_argument("--plain", action="store_true", help="No limpiar pantalla; imprimir cada actualización (útil para logs)")   # modo log
    args = ap.parse_args()   # parsea lo que se escribió en la terminal

    game_id = game_id_from_arg(args.game)   # extrae el ID de la URL/texto
    last_ply = -1              # última jugada mostrada (para no repintar sin cambios)
    waiting_notified = False   # ¿ya avisamos de que la partida no está publicada?
    # Bucle principal: consulta → pinta si hay cambios → duerme → repite
    while True:
        try:
            data = fetch(game_id)   # descarga el estado actual de la partida
        except Exception as e:
            # Error de red/API: avisa por stderr y reintenta (o sale en modo --once)
            print(f"Error consultando la partida: {e}", file=sys.stderr, flush=True)
            if args.once:
                sys.exit(1)   # en modo una-sola-vez, el error es fatal
            time.sleep(max(args.interval, 5))   # espera al menos 5 s antes de reintentar
            continue   # vuelve a empezar el bucle

        if data is None:   # 404: la partida aún no está publicada
            if args.once:
                print("La partida aún no está disponible en el endpoint (404).", flush=True)
                sys.exit(2)   # en modo una-sola-vez, salir con código 2
            if not waiting_notified:   # avisa solo la primera vez
                print("Partida en curso: chess.com aún no publica los datos (404). Esperando…", flush=True)
                waiting_notified = True
            time.sleep(max(args.interval, 10))   # espera al menos 10 s (no martillar el endpoint)
            continue   # y reintenta

        ply = data["game"].get("plyCount", 0)   # cuántas medias jugadas lleva la partida
        # Solo repinta si hubo jugada nueva o si la partida acaba de terminar
        if ply != last_ply or data["game"].get("isFinished"):
            if args.plain:
                print("\n" + "=" * 40, flush=True)   # modo log: separador en vez de limpiar
            else:
                os.system("clear")   # limpia la terminal para repintar encima
            print(render(data, args.user), flush=True)   # imprime el tablero y las jugadas
            last_ply = ply   # recuerda hasta dónde pintamos

        # Condiciones de salida: modo una-sola-vez o partida terminada
        if args.once or data["game"].get("isFinished"):
            break
        time.sleep(args.interval)   # espera antes de la siguiente consulta


# Solo ejecuta main() si el archivo se corre directamente (no al importarlo desde server.py)
if __name__ == "__main__":
    main()
