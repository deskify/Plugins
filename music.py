# -*- coding: utf-8 -*-
"""
Музыкальный плеер для Deskify (без VLC).
Играет локальные mp3 / wav / ogg / flac через pygame.mixer.
M4A/AAC — только если pygame собран с FFmpeg.
Сетевые потоки и HLS — не поддерживаются.

Зависимость: pygame (pip install pygame)
Плейлист:   data/music_player_playlist.json
Настройки:  data/music_player_config.json
"""

import os
import json
import random
from datetime import datetime


try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    pygame = None
    HAS_PYGAME = False


PLAYLIST_FILENAME = "music_player_playlist.json"
CONFIG_FILENAME = "music_player_config.json"

SUPPORTED_EXT = {".mp3", ".wav", ".ogg", ".flac"}

REPEAT_ORDER = ["none", "all", "one"]
REPEAT_LABELS = {
    "none": ("Без повтора", "🔁", "gray30"),
    "all":  ("Повтор плейлиста", "🔁", "#1f538d"),
    "one":  ("Повтор одной", "🔂", "#1f538d"),
}


_state = {
    "api": None,
    "app": None,
    "window": None,
    "playlist": [],          # список путей
    "current_index": -1,
    "playing": False,
    "paused": False,
    "config": {"volume": 0.7, "repeat": "none", "shuffle": False},
    "ui": {},
    "initialized": False,
    "tick_job": None,
    "track_duration": 0.0,
}


# ============================================================
#  Конфиг / плейлист
# ============================================================

def _config_path(api):
    return api["PathHelper"].get_path(CONFIG_FILENAME)


def _playlist_path(api):
    return api["PathHelper"].get_path(PLAYLIST_FILENAME)


def _load_config(api):
    data = api["DataManager"].load_json(CONFIG_FILENAME, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("volume", 0.7)
    data.setdefault("repeat", "none")
    data.setdefault("shuffle", False)
    return data


def _save_config(api):
    try:
        with open(_config_path(api), "w", encoding="utf-8") as f:
            json.dump(_state["config"], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_playlist(api):
    data = api["DataManager"].load_json(PLAYLIST_FILENAME, [])
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, str) and os.path.isfile(p)]


def _save_playlist(api):
    try:
        with open(_playlist_path(api), "w", encoding="utf-8") as f:
            json.dump(_state["playlist"], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
#  Миксер
# ============================================================

def _ensure_mixer(api):
    if not HAS_PYGAME:
        return False, (
            "Библиотека pygame не установлена.\n\n"
            "Выполните в терминале: pip install pygame\n"
            "и перезапустите Deskify."
        )
    if _state["initialized"]:
        return True, None
    try:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        _state["initialized"] = True
        pygame.mixer.music.set_volume(float(_state["config"].get("volume", 0.7)))
        return True, None
    except Exception as e:
        return False, f"Не удалось инициализировать аудио: {e}"


def _read_duration(path):
    if not HAS_PYGAME:
        return 0.0
    try:
        snd = pygame.mixer.Sound(path)
        return float(snd.get_length())
    except Exception:
        return 0.0


# ============================================================
#  Воспроизведение
# ============================================================

def _track_title(path):
    try:
        base = os.path.basename(path)
        name, _ = os.path.splitext(base)
        return name
    except Exception:
        return str(path)


def _play_index(api, index):
    ok, err = _ensure_mixer(api)
    if not ok:
        api["messagebox"].showerror("Плеер", err)
        return

    if not _state["playlist"]:
        api["toast"]("Плейлист пуст", "error")
        return
    if index < 0 or index >= len(_state["playlist"]):
        return

    path = _state["playlist"][index]
    if not os.path.isfile(path):
        api["toast"](f"Файл не найден: {os.path.basename(path)}", "error")
        return

    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(float(_state["config"].get("volume", 0.7)))
        pygame.mixer.music.play()
        _state["current_index"] = index
        _state["playing"] = True
        _state["paused"] = False
        _state["track_duration"] = _read_duration(path)
        _update_now_playing(api)
        _update_play_button(api)
        _highlight_current(api)
        _start_tick(api)
    except Exception as e:
        api["messagebox"].showerror("Плеер", f"Не удалось воспроизвести:\n{e}")


def _toggle_pause(api):
    if not _state["playing"]:
        idx = _state["current_index"] if _state["current_index"] >= 0 else 0
        _play_index(api, idx)
        return
    try:
        if _state["paused"]:
            pygame.mixer.music.unpause()
            _state["paused"] = False
            _start_tick(api)
        else:
            pygame.mixer.music.pause()
            _state["paused"] = True
    except Exception:
        pass
    _update_play_button(api)
    _update_now_playing(api)


def _stop(api):
    if HAS_PYGAME and _state["initialized"]:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
    _state["playing"] = False
    _state["paused"] = False
    _state["track_duration"] = 0.0
    _update_now_playing(api)
    _update_play_button(api)
    _update_progress(api)
    _highlight_current(api)


def _next_track(api, auto=False):
    if not _state["playlist"]:
        return
    repeat = _state["config"].get("repeat", "none")

    if auto and repeat == "one":
        _play_index(api, _state["current_index"])
        return

    if _state["config"].get("shuffle"):
        if len(_state["playlist"]) == 1:
            idx = 0
        else:
            idx = random.randrange(len(_state["playlist"]))
            while idx == _state["current_index"]:
                idx = random.randrange(len(_state["playlist"]))
    else:
        idx = _state["current_index"] + 1
        if idx >= len(_state["playlist"]):
            if repeat == "all" or not auto:
                idx = 0
            else:
                _stop(api)
                return

    _play_index(api, idx)


def _prev_track(api):
    if not _state["playlist"]:
        return
    idx = _state["current_index"] - 1
    if idx < 0:
        idx = len(_state["playlist"]) - 1
    _play_index(api, idx)


def _set_volume(api, value):
    value = max(0.0, min(1.0, float(value)))
    _state["config"]["volume"] = value
    if HAS_PYGAME and _state["initialized"]:
        try:
            pygame.mixer.music.set_volume(value)
        except Exception:
            pass
    _save_config(api)


def _set_repeat(api, key):
    if key not in REPEAT_LABELS:
        key = "none"
    _state["config"]["repeat"] = key
    _save_config(api)
    _update_repeat_button(api)


def _cycle_repeat(api):
    current = _state["config"].get("repeat", "none")
    try:
        i = REPEAT_ORDER.index(current)
    except ValueError:
        i = 0
    nxt = REPEAT_ORDER[(i + 1) % len(REPEAT_ORDER)]
    _set_repeat(api, nxt)
    api["toast"](REPEAT_LABELS[nxt][0], "info")


def _toggle_shuffle(api):
    _state["config"]["shuffle"] = not _state["config"].get("shuffle", False)
    _save_config(api)
    _update_shuffle_button(api)


def _seek(api, fraction):
    if not _state["playing"] or _state["current_index"] < 0:
        return
    total = _state["track_duration"]
    if total <= 0:
        return
    target = max(0.0, min(total, total * float(fraction)))
    path = _state["playlist"][_state["current_index"]]
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play(start=target)
        pygame.mixer.music.set_volume(float(_state["config"].get("volume", 0.7)))
        _state["paused"] = False
        _start_tick(api)
    except Exception as e:
        api["toast"](f"Перемотка не удалась: {e}", "error")


# ============================================================
#  Тик / прогресс
# ============================================================

def _start_tick(api):
    if _state["tick_job"] is not None:
        try:
            api["app"].after_cancel(_state["tick_job"])
        except Exception:
            pass
    _state["tick_job"] = api["app"].after(500, lambda: _tick(api))


def _tick(api):
    _state["tick_job"] = None
    if not _state["playing"]:
        return

    try:
        busy = pygame.mixer.music.get_busy()
    except Exception:
        busy = False

    if _state["playing"] and not _state["paused"] and not busy:
        _next_track(api, auto=True)
        return

    _update_progress(api)
    _state["tick_job"] = api["app"].after(500, lambda: _tick(api))


def _format_time(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 0
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def _update_progress(api):
    bar = _state["ui"].get("progress")
    time_lbl = _state["ui"].get("time_lbl")
    if bar is None or time_lbl is None:
        return
    try:
        if not bar.winfo_exists() or not time_lbl.winfo_exists():
            return
    except Exception:
        return

    if not _state["playing"]:
        bar.set(0)
        time_lbl.configure(text="00:00 / 00:00")
        return

    try:
        pos_ms = pygame.mixer.music.get_pos()
    except Exception:
        pos_ms = -1

    pos = pos_ms / 1000.0 if pos_ms >= 0 else 0.0
    total = _state["track_duration"]

    if total <= 0:
        time_lbl.configure(text=f"{_format_time(pos)} / --:--")
        bar.set(0)
        return

    fraction = max(0.0, min(1.0, pos / total))
    bar.set(fraction)
    time_lbl.configure(text=f"{_format_time(pos)} / {_format_time(total)}")


# ============================================================
#  UI-обновления
# ============================================================

def _update_now_playing(api):
    lbl = _state["ui"].get("now_playing")
    if lbl is None:
        return
    try:
        if not lbl.winfo_exists():
            return
    except Exception:
        return

    if _state["current_index"] < 0 or not _state["playlist"]:
        lbl.configure(text="Ничего не играет", text_color="gray60")
        return
    title = _track_title(_state["playlist"][_state["current_index"]])
    prefix = "⏸ " if _state["paused"] else "▶ "
    lbl.configure(text=f"{prefix}{title}", text_color="#2ecc71")


def _update_play_button(api):
    btn = _state["ui"].get("play_btn")
    if btn is None:
        return
    try:
        if not btn.winfo_exists():
            return
        btn.configure(text="⏸" if (_state["playing"] and not _state["paused"]) else "▶")
    except Exception:
        pass


def _update_repeat_button(api):
    btn = _state["ui"].get("repeat_btn")
    if btn is None:
        return
    try:
        if not btn.winfo_exists():
            return
        key = _state["config"].get("repeat", "none")
        _, icon, color = REPEAT_LABELS.get(key, REPEAT_LABELS["none"])
        btn.configure(text=icon, fg_color=color)
    except Exception:
        pass


def _update_shuffle_button(api):
    btn = _state["ui"].get("shuffle_btn")
    if btn is None:
        return
    try:
        if not btn.winfo_exists():
            return
        on = _state["config"].get("shuffle", False)
        btn.configure(fg_color="#1f538d" if on else "gray30")
    except Exception:
        pass


def _highlight_current(api):
    refresh = _state["ui"].get("refresh_list")
    if refresh:
        try:
            refresh()
        except Exception:
            pass


# ============================================================
#  Плейлист: добавление / удаление / перемещение
# ============================================================

def _add_files(api, refresh_cb):
    paths = api["filedialog"].askopenfilenames(
        title="Выберите аудиофайлы",
        filetypes=[
            ("Аудио", "*.mp3 *.wav *.ogg *.flac"),
            ("Все файлы", "*.*"),
        ],
    )
    if not paths:
        return
    added = 0
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        if ext not in SUPPORTED_EXT:
            continue
        if p in _state["playlist"]:
            continue
        _state["playlist"].append(p)
        added += 1
    _save_playlist(api)
    if added:
        api["toast"](f"Добавлено треков: {added}", "success")
    else:
        api["toast"]("Новых треков нет", "info")
    refresh_cb()


def _add_folder(api, refresh_cb):
    folder = api["filedialog"].askdirectory(title="Выберите папку с музыкой")
    if not folder:
        return
    added = 0
    for root, _, files in os.walk(folder):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in SUPPORTED_EXT:
                full = os.path.join(root, name)
                if full not in _state["playlist"]:
                    _state["playlist"].append(full)
                    added += 1
    _save_playlist(api)
    if added:
        api["toast"](f"Добавлено треков: {added}", "success")
    else:
        api["toast"]("В папке нет новых аудиофайлов", "info")
    refresh_cb()


def _remove_track(api, index, refresh_cb):
    if not (0 <= index < len(_state["playlist"])):
        return
    if index == _state["current_index"]:
        _stop(api)
        _state["current_index"] = -1
    _state["playlist"].pop(index)
    if _state["current_index"] > index:
        _state["current_index"] -= 1
    _save_playlist(api)
    refresh_cb()


def _clear_playlist(api, refresh_cb):
    if not _state["playlist"]:
        return
    if not api["messagebox"].askyesno(
            "Очистить плейлист",
            f"Удалить все {len(_state['playlist'])} треков?\n\n"
            "Файлы на диске не удалятся."):
        return
    _stop(api)
    _state["playlist"] = []
    _state["current_index"] = -1
    _save_playlist(api)
    refresh_cb()
    api["toast"]("Плейлист очищен", "success")


def _move_track(api, index, delta, refresh_cb):
    new_index = index + delta
    if not (0 <= new_index < len(_state["playlist"])):
        return
    lst = _state["playlist"]
    lst[index], lst[new_index] = lst[new_index], lst[index]
    if _state["current_index"] == index:
        _state["current_index"] = new_index
    elif _state["current_index"] == new_index:
        _state["current_index"] = index
    _save_playlist(api)
    refresh_cb()


# ============================================================
#  Главное окно
# ============================================================

def _open_player_window(api):
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

    win = ctk.CTkToplevel(app)
    win.title("🎧 Музыкальный плеер")
    win.geometry("860x660")
    win.minsize(720, 540)
    _state["window"] = win
    _state["ui"] = {}

    if not HAS_PYGAME:
        warn = ctk.CTkFrame(win, fg_color="#7a3a10", corner_radius=6)
        warn.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            warn,
            text="⚠ Оюновите WorkSpace до версии 1.1 и выше\n"
                 "Не доступна библиотека Pygame",
            text_color="white", justify="left",
            font=ctk.CTkFont(size=12),
        ).pack(padx=10, pady=8, anchor="w")

    # Верх
    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=16, pady=(14, 4))
    ctk.CTkLabel(top, text="🎧 Музыкальный плеер",
                 font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")

    # Карточка "сейчас играет"
    now_card = ctk.CTkFrame(win, corner_radius=8)
    now_card.pack(fill="x", padx=16, pady=(6, 4))

    now_lbl = ctk.CTkLabel(now_card, text="Ничего не играет",
                            font=ctk.CTkFont(size=14, weight="bold"),
                            anchor="w", text_color="gray60")
    now_lbl.pack(fill="x", padx=12, pady=(10, 2))
    _state["ui"]["now_playing"] = now_lbl

    prog = ctk.CTkFrame(now_card, fg_color="transparent")
    prog.pack(fill="x", padx=12, pady=(0, 10))

    time_lbl = ctk.CTkLabel(prog, text="00:00 / 00:00",
                             font=ctk.CTkFont(size=11),
                             text_color="gray60")
    time_lbl.pack(side="right", padx=(8, 0))
    _state["ui"]["time_lbl"] = time_lbl

    progress = ctk.CTkSlider(prog, from_=0, to=1,
                              command=lambda v: _seek(api, v))
    progress.set(0)
    progress.pack(side="left", fill="x", expand=True)
    _state["ui"]["progress"] = progress

    # Кнопки
    controls = ctk.CTkFrame(win, fg_color="transparent")
    controls.pack(pady=(6, 4))

    ctk.CTkButton(controls, text="⏮", width=48, height=44, fg_color="gray30",
                  command=lambda: _prev_track(api)).pack(side="left", padx=3)

    play_btn = ctk.CTkButton(controls, text="▶", width=68, height=44,
                              fg_color="#1f538d",
                              font=ctk.CTkFont(size=20, weight="bold"),
                              command=lambda: _toggle_pause(api))
    play_btn.pack(side="left", padx=3)
    _state["ui"]["play_btn"] = play_btn

    ctk.CTkButton(controls, text="⏹", width=48, height=44, fg_color="gray30",
                  command=lambda: _stop(api)).pack(side="left", padx=3)

    ctk.CTkButton(controls, text="⏭", width=48, height=44, fg_color="gray30",
                  command=lambda: _next_track(api)).pack(side="left", padx=3)

    repeat_btn = ctk.CTkButton(controls, text="🔁", width=48, height=44,
                                fg_color="gray30",
                                command=lambda: _cycle_repeat(api))
    repeat_btn.pack(side="left", padx=(12, 3))
    _state["ui"]["repeat_btn"] = repeat_btn

    shuffle_btn = ctk.CTkButton(controls, text="🔀", width=48, height=44,
                                 fg_color="gray30",
                                 command=lambda: _toggle_shuffle(api))
    shuffle_btn.pack(side="left", padx=3)
    _state["ui"]["shuffle_btn"] = shuffle_btn

    # Громкость
    vol_row = ctk.CTkFrame(win, fg_color="transparent")
    vol_row.pack(fill="x", padx=16, pady=(4, 6))

    ctk.CTkLabel(vol_row, text="🔊").pack(side="left", padx=(0, 6))

    vol_var = ctk.DoubleVar(value=float(_state["config"].get("volume", 0.7)))
    ctk.CTkSlider(vol_row, from_=0, to=1, variable=vol_var,
                  command=lambda v: _set_volume(api, v)).pack(
        side="left", fill="x", expand=True, padx=(0, 8))

    vol_lbl = ctk.CTkLabel(vol_row, text=f"{int(vol_var.get()*100)}%",
                            width=48, text_color="gray60",
                            font=ctk.CTkFont(size=11))
    vol_lbl.pack(side="left")

    def _on_vol(*_):
        vol_lbl.configure(text=f"{int(vol_var.get()*100)}%")
    vol_var.trace_add("write", _on_vol)

    # Заголовок списка
    head = ctk.CTkFrame(win, fg_color="transparent")
    head.pack(fill="x", padx=16, pady=(6, 2))
    ctk.CTkLabel(head, text="Плейлист",
                 font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
    pl_count = ctk.CTkLabel(head, text="", text_color="gray60",
                             font=ctk.CTkFont(size=11))
    pl_count.pack(side="right")
    _state["ui"]["pl_count"] = pl_count

    # Список
    list_frame = ctk.CTkScrollableFrame(win, fg_color=("gray90", "gray15"))
    list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 4))

    def refresh_playlist():
        for w in list(list_frame.winfo_children()):
            w.destroy()

        pl_count.configure(text=f"{len(_state['playlist'])} треков")

        if not _state["playlist"]:
            ctk.CTkLabel(list_frame,
                         text="Плейлист пуст.\nДобавьте файлы или папку.",
                         text_color="gray60", justify="center").pack(pady=30)
            return

        for idx, path in enumerate(_state["playlist"]):
            row = ctk.CTkFrame(list_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)

            is_current = (idx == _state["current_index"])
            num_lbl = ctk.CTkLabel(
                row,
                text=("▶" if is_current else str(idx + 1)),
                width=28,
                text_color="#2ecc71" if is_current else "gray60",
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            num_lbl.pack(side="left", padx=(4, 4))

            title = _track_title(path)
            btn = ctk.CTkButton(
                row, text=title, anchor="w",
                fg_color="#1f538d" if is_current else "transparent",
                hover_color="gray30",
                text_color="white" if is_current else ("gray10", "gray90"),
                command=lambda i=idx: _play_index(api, i),
            )
            btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

            ctk.CTkButton(row, text="⬆", width=28, height=26,
                          fg_color="transparent", hover_color="gray30",
                          command=lambda i=idx: _move_track(api, i, -1, refresh_playlist)
                          ).pack(side="right", padx=1)
            ctk.CTkButton(row, text="⬇", width=28, height=26,
                          fg_color="transparent", hover_color="gray30",
                          command=lambda i=idx: _move_track(api, i, +1, refresh_playlist)
                          ).pack(side="right", padx=1)
            ctk.CTkButton(row, text="✕", width=28, height=26,
                          fg_color="transparent", hover_color="#c0392b",
                          command=lambda i=idx: _remove_track(api, i, refresh_playlist)
                          ).pack(side="right", padx=2)

    _state["ui"]["refresh_list"] = refresh_playlist
    refresh_playlist()

    # Кнопки действий
    actions = ctk.CTkFrame(win, fg_color="transparent")
    actions.pack(fill="x", padx=16, pady=(2, 12))

    ctk.CTkButton(actions, text="+ Файлы", width=110,
                  command=lambda: _add_files(api, refresh_playlist)
                  ).pack(side="left", padx=(0, 4))
    ctk.CTkButton(actions, text="+ Папка", width=110, fg_color="gray30",
                  command=lambda: _add_folder(api, refresh_playlist)
                  ).pack(side="left", padx=4)
    ctk.CTkButton(actions, text="Очистить", width=110, fg_color="gray30",
                  command=lambda: _clear_playlist(api, refresh_playlist)
                  ).pack(side="left", padx=4)

    # Первичный UI-статус
    _update_repeat_button(api)
    _update_shuffle_button(api)
    _update_play_button(api)
    _update_now_playing(api)
    if _state["playing"]:
        _start_tick(api)

    def on_close():
        _state["window"] = None
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", on_close)


# ============================================================
#  Точка входа
# ============================================================

def register(api):
    _state["api"] = api
    _state["app"] = api["app"]
    _state["config"] = _load_config(api)
    _state["playlist"] = _load_playlist(api)

    api["add_plugin_button"](
        "🎧 Плеер",
        lambda: _open_player_window(api),
    )
