#!/usr/bin/env python3
"""Servidor local para ver partidas de chess.com de TheZackES.

- Vigila la cuenta y carga automáticamente la última partida (y cada una nueva
  al terminar), con replay jugada a jugada.
- Recibe estado EN VIVO desde el bookmarklet que corre en la pestaña de
  chess.com mientras juegas (POST /api/push).

Uso:  .venv/bin/python server.py   →  http://localhost:8787
"""

import re
import threading
import time
from datetime import datetime, timezone

import chess
import requests
from flask import Flask, jsonify, request, send_from_directory

from watch import TCN, decode_tcn  # mismo decodificador TCN del visor de terminal

USERNAME = "thezackes"
PORT = 8787

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}

app = Flask(__name__, static_folder="static")

state = {
    "live": None,    # estado empujado por el bookmarklet
    "latest": None,  # última partida terminada, procesada
    "status": "iniciando…",
}
lock = threading.Lock()


def fetch_callback(game_id: str) -> dict | None:
    r = requests.get(
        f"https://www.chess.com/callback/live/game/{game_id}",
        headers=HEADERS, timeout=10,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def process_game(game_id: str, data: dict) -> dict:
    """Convierte el JSON del callback en algo listo para la página: FEN por jugada."""
    game = data["game"]
    players = data.get("players", {})
    top, bottom = players.get("top", {}), players.get("bottom", {})
    white = bottom if bottom.get("color") == "white" else top
    black = top if white is bottom else bottom

    board = chess.Board()
    fens = [board.fen()]
    sans = []
    for u in decode_tcn(game.get("moveList", "")):
        mv = chess.Move.from_uci(u)
        sans.append(board.san(mv))
        board.push(mv)
        fens.append(board.fen())

    clocks = [int(t) for t in game.get("moveTimestamps", "").split(",") if t]

    return {
        "id": game_id,
        "white": {"name": white.get("username", "?"), "rating": white.get("rating")},
        "black": {"name": black.get("username", "?"), "rating": black.get("rating")},
        "sans": sans,
        "fens": fens,
        "clocks": clocks,  # décimas de segundo restantes, por jugada (blancas, negras, …)
        "result": game.get("resultMessage", ""),
        "finished": game.get("isFinished", False),
        "timeControl": game.get("timeControl", ""),
        "endTime": game.get("endTime"),
        "userIsBlack": str(black.get("username", "")).lower() == USERNAME,
    }


def poll_archives():
    """Hilo que vigila el archivo del usuario y carga cada partida nueva."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            months = [(now.year, now.month)]
            prev = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
            months.append(prev)

            games = []
            for y, m in months:
                r = requests.get(
                    f"https://api.chess.com/pub/player/{USERNAME}/games/{y}/{m:02d}",
                    headers=HEADERS, timeout=10,
                )
                if r.ok:
                    games = r.json().get("games", [])
                if games:
                    break

            if games:
                last = games[-1]
                gid = re.search(r"(\d{6,})", last["url"]).group(1)
                with lock:
                    known = state["latest"]["id"] if state["latest"] else None
                if gid != known:
                    data = fetch_callback(gid)
                    if data:
                        processed = process_game(gid, data)
                        with lock:
                            state["latest"] = processed
                            state["status"] = "ok"
                        print(f"[poller] Nueva partida cargada: {gid} — {processed['result']}")
                else:
                    with lock:
                        state["status"] = "ok"
        except Exception as e:
            with lock:
                state["status"] = f"error consultando chess.com: {e}"
            print(f"[poller] {e}")
        time.sleep(10)


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/state")
def api_state():
    with lock:
        live = state["live"]
        # el feed en vivo caduca si el bookmarklet lleva >10 s sin reportar
        if live and time.time() - live["received"] > 10:
            live = None
        return jsonify({
            "username": USERNAME,
            "live": live,
            "latest": state["latest"],
            "status": state["status"],
            "now": time.time(),
        })


@app.route("/api/push", methods=["POST", "OPTIONS"])
def api_push():
    if request.method == "OPTIONS":
        resp = app.make_response("")
    else:
        try:
            payload = request.get_json(force=True, silent=True) or {}
            fen = str(payload.get("fen", ""))
            chess.Board(fen)  # valida el FEN; lanza si es basura
            try:
                playing_as = int(payload.get("playingAs") or 0)
            except (TypeError, ValueError):
                playing_as = 0
            with lock:
                state["live"] = {
                    "fen": fen,
                    "clocks": [str(c)[:12] for c in payload.get("clocks", [])[:2]],
                    "playingAs": playing_as if playing_as in (1, 2) else None,
                    "received": time.time(),
                }
            resp = jsonify({"ok": True})
        except Exception as e:
            resp = jsonify({"ok": False, "error": str(e)})
            resp.status_code = 400
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


if __name__ == "__main__":
    threading.Thread(target=poll_archives, daemon=True).start()
    print(f"Visor listo → http://localhost:{PORT}")
    app.run(host="127.0.0.1", port=PORT)
