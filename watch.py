#!/usr/bin/env python3
"""Visor en tiempo real de una partida de chess.com.

Uso:
    python watch.py <url-o-id-de-partida> [--user <tu_usuario>] [--interval 2]

Ejemplo:
    python watch.py https://www.chess.com/game/live/186167189226 --user emm
"""

import argparse
import os
import re
import sys
import time

import chess
import requests

# Codificación TCN que usa chess.com para moveList
TCN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!?{~}(^)[_]@#$,./&-*++="
PROMO = "qnrbkp"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}

PIECES = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}


def decode_tcn(move_list: str) -> list[str]:
    """Decodifica el moveList TCN de chess.com a movimientos UCI."""
    moves = []
    for i in range(0, len(move_list), 2):
        o = TCN.index(move_list[i])
        s = TCN.index(move_list[i + 1])
        promotion = ""
        if s > 63:  # promoción
            promotion = PROMO[(s - 64) // 3]
            s = o + (-8 if o < 16 else 8) + (s - 1) % 3 - 1
        frm = TCN[o % 8] + str(o // 8 + 1)
        to = TCN[s % 8] + str(s // 8 + 1)
        moves.append(frm + to + promotion)
    return moves


def game_id_from_arg(arg: str) -> str:
    m = re.search(r"(\d{6,})", arg)
    if not m:
        sys.exit(f"No pude extraer un ID de partida de: {arg}")
    return m.group(1)


def fetch(game_id: str) -> dict | None:
    """Devuelve el JSON de la partida, o None si aún no está disponible (404)."""
    r = requests.get(
        f"https://www.chess.com/callback/live/game/{game_id}",
        headers=HEADERS,
        timeout=10,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def fmt_clock(tenths: int) -> str:
    s = tenths / 10
    m, s = divmod(int(s), 60)
    return f"{m}:{s:02d}"


def render(data: dict, user: str | None) -> str:
    game = data["game"]
    players = data.get("players", {})
    top = players.get("top", {})
    bottom = players.get("bottom", {})

    # ¿De qué color juega cada uno?
    white = bottom if bottom.get("color") == "white" else top
    black = top if white is bottom else bottom

    uci_moves = decode_tcn(game.get("moveList", ""))
    board = chess.Board()
    sans = []
    for u in uci_moves:
        mv = chess.Move.from_uci(u)
        sans.append(board.san(mv))
        board.push(mv)

    # Relojes: moveTimestamps son décimas de segundo restantes por jugada
    ts = [int(t) for t in game.get("moveTimestamps", "").split(",") if t]
    base = game.get("baseTime1", 0)
    w_clock = fmt_clock(ts[-2] if len(ts) >= 2 else (ts[0] if ts and len(ts) % 2 else base))
    b_clock = fmt_clock(ts[-1] if len(ts) >= 2 else base)
    if len(ts) == 1:
        w_clock, b_clock = fmt_clock(ts[0]), fmt_clock(base)

    # Orientación: si --user coincide con negras, girar el tablero
    flip = bool(user) and user.lower() == str(black.get("username", "")).lower()

    lines = []
    w_name = f"{white.get('username','?')} ({white.get('rating','?')})"
    b_name = f"{black.get('username','?')} ({black.get('rating','?')})"

    header_top = f"{b_name}  ⏱ {b_clock}" if not flip else f"{w_name}  ⏱ {w_clock}"
    header_bot = f"{w_name}  ⏱ {w_clock}" if not flip else f"{b_name}  ⏱ {b_clock}"

    lines.append(header_top)
    ranks = range(7, -1, -1) if not flip else range(8)
    files = range(8) if not flip else range(7, -1, -1)
    for r in ranks:
        row = [str(r + 1)]
        for f in files:
            piece = board.piece_at(chess.square(f, r))
            row.append(PIECES[piece.symbol()] if piece else "·")
        lines.append(" ".join(row))
    file_labels = "abcdefgh" if not flip else "hgfedcba"
    lines.append("  " + " ".join(file_labels))
    lines.append(header_bot)
    lines.append("")

    # Lista de jugadas en SAN
    pretty = []
    for i in range(0, len(sans), 2):
        num = i // 2 + 1
        pair = f"{num}. {sans[i]}"
        if i + 1 < len(sans):
            pair += f" {sans[i+1]}"
        pretty.append(pair)
    lines.append(" ".join(pretty[-12:]))  # últimas 12 jugadas completas
    lines.append("")

    if game.get("isFinished"):
        lines.append(f"🏁 {game.get('resultMessage', 'Partida terminada')}")
    else:
        turn = "blancas" if board.turn == chess.WHITE else "negras"
        lines.append(f"Jugada {len(sans)} — mueven {turn}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Visor en vivo de partidas de chess.com")
    ap.add_argument("game", help="URL o ID de la partida (p.ej. https://www.chess.com/game/live/123456789)")
    ap.add_argument("--user", help="Tu usuario de chess.com, para orientar el tablero desde tu lado")
    ap.add_argument("--interval", type=float, default=2.0, help="Segundos entre consultas (default 2)")
    ap.add_argument("--once", action="store_true", help="Mostrar el estado actual una sola vez y salir")
    ap.add_argument("--plain", action="store_true", help="No limpiar pantalla; imprimir cada actualización (útil para logs)")
    args = ap.parse_args()

    game_id = game_id_from_arg(args.game)
    last_ply = -1
    waiting_notified = False
    while True:
        try:
            data = fetch(game_id)
        except Exception as e:
            print(f"Error consultando la partida: {e}", file=sys.stderr, flush=True)
            if args.once:
                sys.exit(1)
            time.sleep(max(args.interval, 5))
            continue

        if data is None:
            if args.once:
                print("La partida aún no está disponible en el endpoint (404).", flush=True)
                sys.exit(2)
            if not waiting_notified:
                print("Partida en curso: chess.com aún no publica los datos (404). Esperando…", flush=True)
                waiting_notified = True
            time.sleep(max(args.interval, 10))
            continue

        ply = data["game"].get("plyCount", 0)
        if ply != last_ply or data["game"].get("isFinished"):
            if args.plain:
                print("\n" + "=" * 40, flush=True)
            else:
                os.system("clear")
            print(render(data, args.user), flush=True)
            last_ply = ply

        if args.once or data["game"].get("isFinished"):
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
