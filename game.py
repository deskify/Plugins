# -*- coding: utf-8 -*-
"""
Плагин «Игра» для Deskify.

Аркада «Собери монеты»: управляй квадратом стрелками, собирай жёлтые
монеты, уворачивайся от красных врагов. С каждым уровнем врагов больше
и скорость выше.

Зависимостей нет — работает на tkinter.Canvas.
Рекорд сохраняется в data/game_scores.json.
"""

import os
import json
import random
from datetime import datetime


SCORES_FILENAME = "game_scores.json"

# Игровое поле
CELL = 30          # размер клетки в пикселях
COLS = 21          # ширина поля в клетках
ROWS = 17          # высота поля в клетках

DIFFICULTIES = {
    "Лёгкая":  {"enemy_speed": 0.7, "enemy_base": 1, "coins": 5},
    "Средняя": {"enemy_speed": 1.0, "enemy_base": 2, "coins": 5},
    "Сложная": {"enemy_speed": 1.4, "enemy_base": 3, "coins": 6},
}


_state = {
    "api": None,
    "app": None,
    "window": None,
    "canvas": None,
    "scores": {"best": 0, "games": 0, "last": 0},
    "difficulty": "Средняя",
    # --- игровое состояние ---
    "running": False,
    "paused": False,
    "player": (10, 8),
    "coins": [],
    "enemies": [],
    "score": 0,
    "level": 1,
    "coins_collected": 0,
    "tick_job": None,
    "key_state": {"up": False, "down": False, "left": False, "right": False},
    "flash": None,       # (x, y, ttl) для визуальной вспышки
    "game_over": False,
}


# ============================================================
#  Хранилище
# ============================================================

def _scores_path(api):
    return api["PathHelper"].get_path(SCORES_FILENAME)


def _load_scores(api):
    data = api["DataManager"].load_json(SCORES_FILENAME, {})
    if not isinstance(data, dict):
        return {"best": 0, "games": 0, "last": 0}
    data.setdefault("best", 0)
    data.setdefault("games", 0)
    data.setdefault("last", 0)
    return data


def _save_scores(api):
    api["DataManager"].save_json(SCORES_FILENAME, _state["scores"])


# ============================================================
#  Звук (winsound, если доступен)
# ============================================================

def _beep(api, freq, dur=60):
    try:
        if api.get("has_winsound") and api.get("winsound"):
            api["winsound"].Beep(int(freq), int(dur))
    except Exception:
        pass


# ============================================================
#  Логика игры
# ============================================================

def _spawn_coin():
    while True:
        x = random.randint(0, COLS - 1)
        y = random.randint(0, ROWS - 1)
        if (x, y) == _state["player"]:
            continue
        if any(c == (x, y) for c in _state["coins"]):
            continue
        if any(e[0] == x and e[1] == y for e in _state["enemies"]):
            continue
        return (x, y)


def _spawn_enemy():
    while True:
        x = random.randint(0, COLS - 1)
        y = random.randint(0, ROWS - 1)
        if (x, y) == _state["player"]:
            continue
        if any(c == (x, y) for c in _state["coins"]):
            continue
        if any(e[0] == x and e[1] == y for e in _state["enemies"]):
            continue
        # не спавним слишком близко к игроку
        px, py = _state["player"]
        if abs(x - px) + abs(y - py) < 4:
            continue
        return [x, y, random.choice([(-1, 0), (1, 0), (0, -1), (0, 1)])]


def _setup_level(api):
    diff = DIFFICULTIES.get(_state["difficulty"], DIFFICULTIES["Средняя"])
    num_coins = diff["coins"]
    num_enemies = diff["enemy_base"] + _state["level"] - 1

    _state["coins"] = [_spawn_coin() for _ in range(num_coins)]
    _state["enemies"] = [_spawn_enemy() for _ in range(num_enemies)]


def _start_game(api):
    _state["player"] = (COLS // 2, ROWS // 2)
    _state["score"] = 0
    _state["level"] = 1
    _state["coins_collected"] = 0
    _state["game_over"] = False
    _state["paused"] = False
    _state["running"] = True
    _state["key_state"] = {"up": False, "down": False, "left": False, "right": False}
    _setup_level(api)
    _render(api)
    _start_tick(api)


def _game_over(api):
    _state["running"] = False
    _state["game_over"] = True
    _beep(api, 200, 200)

    # обновляем рекорд
    _state["scores"]["last"] = _state["score"]
    _state["scores"]["games"] += 1
    if _state["score"] > _state["scores"]["best"]:
        _state["scores"]["best"] = _state["score"]
        api["toast"](f"Новый рекорд: {_state['score']}!", "success")
    _save_scores(api)

    _render(api)


def _start_tick(api):
    if _state["tick_job"] is not None:
        try:
            api["app"].after_cancel(_state["tick_job"])
        except Exception:
            pass
    speed = int(180 / DIFFICULTIES.get(_state["difficulty"], DIFFICULTIES["Средняя"])["enemy_speed"])
    speed = max(60, min(400, speed))
    _state["tick_job"] = api["app"].after(speed, lambda: _tick(api))


def _tick(api):
    _state["tick_job"] = None
    if not _state["running"] or _state["paused"]:
        return

    # перемещение игрока
    px, py = _state["player"]
    ks = _state["key_state"]
    nx, ny = px, py
    if ks["up"]:
        ny -= 1
    elif ks["down"]:
        ny += 1
    elif ks["left"]:
        nx -= 1
    elif ks["right"]:
        nx += 1
    nx = max(0, min(COLS - 1, nx))
    ny = max(0, min(ROWS - 1, ny))
    if (nx, ny) != (px, py):
        _state["player"] = (nx, ny)

    # движение врагов
    new_enemies = []
    for ex, ey, (dx, dy) in _state["enemies"]:
        nex, ney = ex + dx, ey + dy
        # отскок от стен
        if nex < 0 or nex >= COLS:
            dx = -dx
            nex = ex + dx
        if ney < 0 or ney >= ROWS:
            dy = -dy
            ney = ey + dy
        # иногда меняют направление
        if random.random() < 0.02:
            dx, dy = random.choice([(-1, 0), (1, 0), (0, -1), (0, 1)])
            nex, ney = ex + dx, ey + dy
            nex = max(0, min(COLS - 1, nex))
            ney = max(0, min(ROWS - 1, ney))
        new_enemies.append([nex, ney, (dx, dy)])
    _state["enemies"] = new_enemies

    # столкновение с врагом
    for ex, ey, _ in _state["enemies"]:
        if (ex, ey) == _state["player"]:
            _game_over(api)
            return

    # сбор монет
    remaining_coins = []
    collected = 0
    for c in _state["coins"]:
        if c == _state["player"]:
            collected += 1
        else:
            remaining_coins.append(c)
    if collected:
        _state["coins"] = remaining_coins
        _state["coins_collected"] += collected
        _state["score"] += collected * 10 * _state["level"]
        _state["flash"] = (*_state["player"], 2)
        _beep(api, 880, 40)

        # если все монеты собраны — новый уровень
        if not _state["coins"]:
            _state["level"] += 1
            _state["score"] += 50
            _beep(api, 1200, 120)
            api["toast"](f"Уровень {_state['level']}!", "success")
            _setup_level(api)

    # вспышка
    if _state["flash"]:
        x, y, ttl = _state["flash"]
        if ttl <= 1:
            _state["flash"] = None
        else:
            _state["flash"] = (x, y, ttl - 1)

    _render(api)
    _start_tick(api)


# ============================================================
#  Отрисовка
# ============================================================

def _cell_rect(x, y):
    return x * CELL, y * CELL, (x + 1) * CELL, (y + 1) * CELL


def _render(api):
    cv = _state["canvas"]
    if cv is None:
        return
    cv.delete("all")

    width = COLS * CELL
    height = ROWS * CELL

    # фон
    cv.create_rectangle(0, 0, width, height, fill="#1b1b1b", outline="")

    # сетка
    for i in range(COLS + 1):
        cv.create_line(i * CELL, 0, i * CELL, height, fill="#2a2a2a")
    for j in range(ROWS + 1):
        cv.create_line(0, j * CELL, width, j * CELL, fill="#2a2a2a")

    # монеты
    for cx, cy in _state["coins"]:
        x1, y1, x2, y2 = _cell_rect(cx, cy)
        cv.create_oval(x1 + 5, y1 + 5, x2 - 5, y2 - 5,
                       fill="#f1c40f", outline="#b8860b", width=2)

    # враги
    for ex, ey, _ in _state["enemies"]:
        x1, y1, x2, y2 = _cell_rect(ex, ey)
        cv.create_rectangle(x1 + 3, y1 + 3, x2 - 3, y2 - 3,
                            fill="#c0392b", outline="#7a1f17", width=2)

    # игрок
    px, py = _state["player"]
    x1, y1, x2, y2 = _cell_rect(px, py)
    cv.create_rectangle(x1 + 3, y1 + 3, x2 - 3, y2 - 3,
                        fill="#3498db", outline="#1f538d", width=2)

    # вспышка
    if _state["flash"]:
        fx, fy, _ = _state["flash"]
        x1, y1, x2, y2 = _cell_rect(fx, fy)
        cv.create_oval(x1, y1, x2, y2, outline="#f1c40f", width=3)

    # HUD
    cv.create_text(10, 10, anchor="nw", fill="#ecf0f1",
                   text=f"Очки: {_state['score']}   Уровень: {_state['level']}   "
                        f"Монет: {_state['coins_collected']}",
                   font=("Segoe UI", 12, "bold"))
    cv.create_text(width - 10, 10, anchor="ne", fill="#95a5a6",
                   text=f"Рекорд: {_state['scores']['best']}",
                   font=("Segoe UI", 12, "bold"))

    if _state["game_over"]:
        cv.create_rectangle(0, height // 2 - 50, width, height // 2 + 50,
                            fill="#000000", outline="", stipple="gray50")
        cv.create_text(width // 2, height // 2 - 20,
                       fill="#e74c3c",
                       text="ИГРА ОКОНЧЕНА",
                       font=("Segoe UI", 22, "bold"))
        cv.create_text(width // 2, height // 2 + 15,
                       fill="#ecf0f1",
                       text=f"Очки: {_state['score']}   •   Нажмите «Старт»",
                       font=("Segoe UI", 13))

    if _state["paused"] and _state["running"]:
        cv.create_rectangle(0, height // 2 - 40, width, height // 2 + 40,
                            fill="#000000", outline="", stipple="gray50")
        cv.create_text(width // 2, height // 2,
                       fill="#f1c40f",
                       text="ПАУЗА",
                       font=("Segoe UI", 24, "bold"))


# ============================================================
#  Управление
# ============================================================

def _on_key_press(api, event):
    key = event.keysym.lower()
    ks = _state["key_state"]
    if key in ("up", "w"):
        ks["up"] = True
    elif key in ("down", "s"):
        ks["down"] = True
    elif key in ("left", "a"):
        ks["left"] = True
    elif key in ("right", "d"):
        ks["right"] = True
    elif key == "space":
        _toggle_pause(api)


def _on_key_release(api, event):
    key = event.keysym.lower()
    ks = _state["key_state"]
    if key in ("up", "w"):
        ks["up"] = False
    elif key in ("down", "s"):
        ks["down"] = False
    elif key in ("left", "a"):
        ks["left"] = False
    elif key in ("right", "d"):
        ks["right"] = False


def _toggle_pause(api):
    if not _state["running"]:
        return
    _state["paused"] = not _state["paused"]
    if not _state["paused"]:
        _start_tick(api)
    _render(api)
    _update_pause_button(api)


def _update_pause_button(api):
    btn = _state["ui"].get("pause_btn") if "ui" in _state else None
    # безопасная версия, если ui нет
    ui = _state.get("ui") or {}
    btn = ui.get("pause_btn")
    if btn is None:
        return
    try:
        if btn.winfo_exists():
            btn.configure(text="▶ Продолжить" if _state["paused"] else "⏸ Пауза")
    except Exception:
        pass


# ============================================================
#  Главное окно
# ============================================================

def _open_game_window(api):
    win = _state.get("window")
    if win is not None:
        try:
            if win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_force()
                return
        except Exception:
            pass

    ctk = api["ctk"]
    app = api["app"]

    _state["scores"] = _load_scores(api)
    _state["ui"] = {}

    win = ctk.CTkToplevel(app)
    win.title("🎮 Собери монеты")
    win.geometry("720x700")
    win.minsize(660, 640)
    _state["window"] = win

    # Заголовок
    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=16, pady=(14, 4))
    ctk.CTkLabel(top, text="🎮 Собери монеты",
                 font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")

    # Управление
    ctrl = ctk.CTkFrame(win, fg_color="transparent")
    ctrl.pack(fill="x", padx=16, pady=(4, 6))

    diff_var = ctk.StringVar(value=_state["difficulty"])
    ctk.CTkLabel(ctrl, text="Сложность:").pack(side="left", padx=(0, 4))
    ctk.CTkOptionMenu(ctrl, variable=diff_var,
                      values=list(DIFFICULTIES.keys()),
                      width=130,
                      command=lambda v: _set_difficulty(api, v)).pack(side="left", padx=(0, 8))

    def on_start():
        _start_game(api)
        _update_pause_button(api)

    ctk.CTkButton(ctrl, text="▶ Старт", width=100,
                  command=on_start).pack(side="left", padx=3)

    def on_pause():
        _toggle_pause(api)

    pause_btn = ctk.CTkButton(ctrl, text="⏸ Пауза", width=130,
                               fg_color="gray30",
                               command=on_pause)
    pause_btn.pack(side="left", padx=3)
    _state["ui"]["pause_btn"] = pause_btn

    ctk.CTkButton(ctrl, text="↺ Сброс рекорда", width=160,
                  fg_color="gray30",
                  command=lambda: _reset_best(api)).pack(side="right", padx=3)

    # Игровое поле
    board_frame = ctk.CTkFrame(win, fg_color="transparent")
    board_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

    width = COLS * CELL
    height = ROWS * CELL

    canvas = api["tk"].Canvas(
        board_frame, width=width, height=height,
        bg="#1b1b1b", highlightthickness=1, highlightbackground="#333",
        takefocus=1,
    )
    canvas.pack()
    canvas.focus_set()
    _state["canvas"] = canvas

    # Привязки клавиш
    canvas.bind("<KeyPress>", lambda e: _on_key_press(api, e))
    canvas.bind("<KeyRelease>", lambda e: _on_key_release(api, e))
    win.bind("<KeyPress>", lambda e: _on_key_press(api, e))
    win.bind("<KeyRelease>", lambda e: _on_key_release(api, e))

    # Подсказка
    hint = ctk.CTkLabel(win,
                        text="Управление: стрелки или WASD   •   Пробел — пауза",
                        text_color="gray60", font=ctk.CTkFont(size=11))
    hint.pack(fill="x", padx=16, pady=(0, 10))

    # Начальное поле
    _state["player"] = (COLS // 2, ROWS // 2)
    _state["coins"] = []
    _state["enemies"] = []
    _state["score"] = 0
    _state["level"] = 1
    _state["coins_collected"] = 0
    _state["running"] = False
    _state["paused"] = False
    _state["game_over"] = False
    _render(api)

    def on_close():
        _state["running"] = False
        if _state["tick_job"] is not None:
            try:
                app.after_cancel(_state["tick_job"])
            except Exception:
                pass
            _state["tick_job"] = None
        _state["window"] = None
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", on_close)


def _set_difficulty(api, value):
    _state["difficulty"] = value
    api["toast"](f"Сложность: {value}", "info")


def _reset_best(api):
    if not api["messagebox"].askyesno("Сброс рекорда",
                                       f"Сбросить рекорд ({_state['scores']['best']} очков)?"):
        return
    _state["scores"]["best"] = 0
    _state["scores"]["games"] = 0
    _state["scores"]["last"] = 0
    _save_scores(api)
    _render(api)
    api["toast"]("Рекорд сброшен", "success")


# ============================================================
#  Точка входа
# ============================================================

def register(api):
    _state["api"] = api
    _state["app"] = api["app"]
    _state["scores"] = _load_scores(api)
    _state["ui"] = {}

    api["add_plugin_button"](
        "🎮 Игра",
        lambda: _open_game_window(api),
    )