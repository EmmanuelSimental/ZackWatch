# Chess Project

## Visor web local (ZackWatch)

```bash
.venv/bin/python server.py   # → http://localhost:8787
```

- Detecta y carga automáticamente tu última partida de chess.com (y cada nueva
  al terminar), con replay jugada a jugada, relojes y resultado.
- **Modo EN VIVO**: arrastra el botón "🔴 ZackWatch Live" de la página a tu barra
  de marcadores; durante una partida en chess.com haz clic en él y la página
  local muestra tu tablero en tiempo real (el bookmarklet lee el tablero de tu
  propia pestaña y lo envía a `localhost:8787`, porque la API de chess.com no
  publica movimientos de partidas en curso).

## Visor de terminal

Visor en tiempo real de partidas de chess.com desde la terminal, sin necesidad
de la extensión del navegador. Consulta el endpoint público
`https://www.chess.com/callback/live/game/{id}` cada pocos segundos, decodifica
la lista de movimientos (formato TCN) y dibuja el tablero.

## Uso

```bash
.venv/bin/python watch.py <url-o-id-de-partida> [--user <tu_usuario>]
```

- `--user tu_usuario` orienta el tablero desde tu lado (si juegas con negras, lo gira).
- `--interval 2` cambia la frecuencia de consulta en segundos.
- `--once` muestra el estado actual y sale (útil para partidas terminadas).

El visor se refresca solo cuando hay una jugada nueva y termina automáticamente
cuando acaba la partida, mostrando el resultado.

## ¿De dónde saco el ID de la partida?

Cuando estés jugando (o viendo una partida), la URL tiene la forma
`https://www.chess.com/game/live/186167189226` — ese número es el ID.
Puedes pegar la URL completa o solo el número.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install python-chess requests
```
