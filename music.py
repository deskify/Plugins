# -*- coding: utf-8 -*-
"""
Музыкальный плеер Deskify 2.1.

Возможности:
  - воспроизведение mp3 / wav / ogg / flac через pygame.mixer;
  - плейлист с сохранением между запусками;
  - повтор (нет / одна / плейлист) и случайный порядок;
  - живая визуализация: волны, спектр, круги;
  - переливы цвета через HSV;
  - полноэкранный режим (F11 / Esc), стабильный при многократном переключении;
  - регулировка чувствительности визуализации;
  - проверка pygame: если нет — сообщение обновиться до Deskify 1.2.

Зависимости: pygame (pip install pygame), Pillow (есть в ядре 1.2+).
Данные: data/music_playlist.json, data/music_config.json.
"""

import os
import math
import random
import json
import time


try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    pygame = None
    HAS_PYGAME = False


PLAYLIST_FILENAME = "music_playlist.json"
CONFIG_FILENAME = "music_config.json"

SUPPORTED_EXT = {".mp3", ".wav", ".ogg", ".flac"}

REPEAT_ORDER = ["none", "all", "one"]
REPEAT_LABELS = {
    "none": ("Без повтора", "🔁", "gray30"),
    "all":  ("Повтор плейлиста", "🔁", "#1f538d"),
    "one":  ("Повтор одной", "🔂", "#1f538d"),
}

VIS_MODES = ["Волны", "Спектр", "Круги"]


_state = {
    "api": None,
    "app": None,
    "window": None,
    "playlist": [],
    "current_index": -1,
    "playing": False,
    "paused": False,
    "config": {
        "volume": 0.7,
        "repeat": "none",
        "shuffle": False,
        "vis_mode": "Волны",
        "vis_sensitivity": 1.0,
    },
    "ui": {},
    "initialized": False,
    "tick_job": None,
    "vis_job": None,
    "track_duration": 0.0,
    "fullscreen": False,
    "saved_geometry": None,
    "vis_frame_count": 0,
}


# ============================================================
#  Конфиг и плейлист
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
    data.setdefault("vis_mode", "Волны")
    data.setdefault("vis_sensitivity", 1.0)
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
        return False, "pygame не установлен"
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


def _cycle_repeat(api):
    current = _state["config"].get("repeat", "none")
    try:
        i = REPEAT_ORDER.index(current)
    except ValueError:
        i = 0
    nxt = REPEAT_ORDER[(i + 1) % len(REPEAT_ORDER)]
    _state["config"]["repeat"] = nxt
    _save_config(api)
    _update_repeat_button(api)
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
#  Тик прогресса
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
#  Визуализация
# ============================================================

def _start_vis(api):
    if _state["vis_job"] is not None:
        try:
            api["app"].after_cancel(_state["vis_job"])
        except Exception:
            pass
    _state["vis_job"] = api["app"].after(33, lambda: _vis_tick(api))


def _stop_vis(api):
    if _state["vis_job"] is not None:
        try:
            api["app"].after_cancel(_state["vis_job"])
        except Exception:
            pass
        _state["vis_job"] = None


def _vis_tick(api):
    _state["vis_job"] = None
    canvas = _state["ui"].get("vis_canvas")
    if canvas is None:
        return
    try:
        if not canvas.winfo_exists():
            return
    except Exception:
        return

    try:
        _draw_vis(api, canvas)
    except Exception:
        pass

    _state["vis_frame_count"] += 1
    _state["vis_job"] = api["app"].after(33, lambda: _vis_tick(api))


def _get_amplitudes():
    n = 64
    if HAS_PYGAME and _state["initialized"] and _state["playing"] and not _state["paused"]:
        pos = pygame.mixer.music.get_pos() / 1000.0
        result = []
        for i in range(n):
            v = (
                math.sin(pos * 3 + i * 0.3) * 0.4 +
                math.sin(pos * 7 + i * 0.15) * 0.3 +
                math.sin(pos * 13 + i * 0.5) * 0.2
            )
            result.append(abs(v))
        return result
    else:
        pos = time.time()
        result = []
        for i in range(n):
            v = math.sin(pos * 1.5 + i * 0.4) * 0.15 + 0.2
            result.append(abs(v))
        return result


def _hsv_to_rgb(h, s, v):
    if s == 0:
        c = int(v * 255)
        return c, c, c
    h = h % 1.0
    i = int(h * 6)
    f = h * 6 - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return int(r * 255), int(g * 255), int(b * 255)


def _draw_vis(api, canvas):
    canvas.delete("all")

    w = canvas.winfo_width()
    h = canvas.winfo_height()
    if w < 10 or h < 10:
        return

    mode = _state["config"].get("vis_mode", "Волны")
    sensitivity = float(_state["config"].get("vis_sensitivity", 1.0))
    amps = _get_amplitudes()
    if sensitivity != 1.0:
        amps = [min(1.0, a * sensitivity) for a in amps]

    t = _state["vis_frame_count"] / 30.0
    bg = "#0a0a12"

    canvas.create_rectangle(0, 0, w, h, fill=bg, outline="")

    if mode == "Волны":
        _draw_waves(canvas, w, h, amps, t)
    elif mode == "Спектр":
        _draw_spectrum(canvas, w, h, amps, t)
    elif mode == "Круги":
        _draw_circles(canvas, w, h, amps, t)


def _draw_waves(canvas, w, h, amps, t):
    layers = 4
    n = len(amps)
    for layer in range(layers):
        points = []
        phase = t * (0.5 + layer * 0.15) + layer * 1.3
        amplitude = (0.15 + layer * 0.05) * h * 0.4
        offset_y = h / 2 + (layer - layers / 2) * 30

        for i in range(n + 1):
            x = int(w * i / n)
            a = amps[i % n]
            y = offset_y + math.sin(phase + i * 0.25) * amplitude * (0.4 + a * 0.6)
            points.append((x, int(y)))

        hue = (t * 0.1 + layer * 0.15) % 1.0
        r, g, b = _hsv_to_rgb(hue, 0.8, 0.9)
        color = f"#{r:02x}{g:02x}{b:02x}"

        poly = points + [(w, h), (0, h)]
        flat = []
        for p in poly:
            flat.extend(p)
        try:
            canvas.create_polygon(*flat, fill=color, outline="", stipple="gray25")
        except Exception:
            pass

        flat_line = []
        for p in points:
            flat_line.extend(p)
        try:
            canvas.create_line(*flat_line, fill=color, width=2, smooth=True)
        except Exception:
            pass


def _draw_spectrum(canvas, w, h, amps, t):
    n = len(amps)
    bar_w = w / n
    for i in range(n):
        a = amps[i]
        bar_h = max(4, int(a * h * 0.85))
        x1 = int(i * bar_w + 1)
        x2 = int((i + 1) * bar_w - 1)
        y1 = h - bar_h
        y2 = h

        hue = (t * 0.15 + i / n * 0.7) % 1.0
        r, g, b = _hsv_to_rgb(hue, 0.9, 0.9)
        color = f"#{r:02x}{g:02x}{b:02x}"

        canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

        r2, g2, b2 = _hsv_to_rgb(hue, 0.4, 1.0)
        top_color = f"#{r2:02x}{g2:02x}{b2:02x}"
        canvas.create_rectangle(x1, y1, x2, y1 + 3, fill=top_color, outline="")


def _draw_circles(canvas, w, h, amps, t):
    cx, cy = w // 2, h // 2
    max_r = min(w, h) * 0.45

    for layer in range(5):
        idx = int(layer * len(amps) / 5)
        a = amps[idx] if idx < len(amps) else 0
        radius = int(max_r * (0.3 + layer * 0.15) * (0.6 + a * 0.4))
        if radius < 3:
            continue

        hue = (t * 0.1 + layer * 0.2) % 1.0
        r, g, b = _hsv_to_rgb(hue, 0.85, 0.9)
        color = f"#{r:02x}{g:02x}{b:02x}"

        x1 = cx - radius
        y1 = cy - radius
        x2 = cx + radius
        y2 = cy + radius

        try:
            canvas.create_oval(x1, y1, x2, y2,
                               outline=color,
                               width=max(2, 4 - layer))
        except Exception:
            pass

    a0 = amps[0] if amps else 0
    core_r = int(20 + a0 * 40)
    hue = t * 0.3 % 1.0
    r, g, b = _hsv_to_rgb(hue, 1.0, 1.0)
    color = f"#{r:02x}{g:02x}{b:02x}"
    canvas.create_oval(cx - core_r, cy - core_r,
                       cx + core_r, cy + core_r,
                       fill=color, outline="")


def _change_vis_mode(api, mode):
    if mode not in VIS_MODES:
        return
    _state["config"]["vis_mode"] = mode
    _save_config(api)


def _set_sensitivity(api, value):
    try:
        value = max(0.2, min(3.0, float(value)))
    except Exception:
        value = 1.0
    _state["config"]["vis_sensitivity"] = value
    _save_config(api)


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
#  Плейлист
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
#  Полноэкранный режим
# ============================================================

def _toggle_fullscreen(api):
    win = _state["window"]
    if win is None:
        return
    try:
        if not win.winfo_exists():
            return
    except Exception:
        return

    if not _state.get("fullscreen"):
        _enter_fullscreen(api, win)
    else:
        _exit_fullscreen(api, win)


def _enter_fullscreen(api, win):
    ui = _state["ui"]

    try:
        _state["saved_geometry"] = win.geometry()
    except Exception:
        _state["saved_geometry"] = None

    _state["fullscreen"] = True

    # Скрываем нижний блок (плейлист + кнопки)
    bottom = ui.get("bottom_block")
    if bottom is not None:
        try:
            bottom.pack_forget()
        except Exception:
            pass

    # Скрываем всё, кроме панели визуализации
    for key in ("top_bar", "now_card", "controls", "vol_row", "head_row"):
        w = ui.get(key)
        if w is not None:
            try:
                w.pack_forget()
            except Exception:
                pass

    # Панель визуализации — на весь экран
    vis_panel = ui.get("vis_panel")
    if vis_panel is not None:
        try:
            vis_panel.pack_forget()
            try:
                vis_panel.configure(fg_color="black", corner_radius=0)
            except Exception:
                pass
            vis_panel.pack(fill="both", expand=True, padx=0, pady=0)
        except Exception:
            pass

    # Скрываем верхнюю панель визуализации (переключатели)
    vis_top = ui.get("vis_top")
    if vis_top is not None:
        try:
            vis_top.pack_forget()
        except Exception:
            pass

    # Холст — на весь размер
    vis_wrap = ui.get("vis_wrap")
    if vis_wrap is not None:
        try:
            vis_wrap.pack_forget()
            try:
                vis_wrap.configure(fg_color="black", corner_radius=0)
            except Exception:
                pass
            vis_wrap.pack(fill="both", expand=True, padx=0, pady=0)
        except Exception:
            pass

    canvas = ui.get("vis_canvas")
    if canvas is not None:
        try:
            canvas.configure(bg="black")
        except Exception:
            pass

    # Фуллскрин
    try:
        win.attributes("-fullscreen", True)
    except Exception:
        pass

    try:
        win.focus_force()
    except Exception:
        pass

    # Подсказка
    hint = ui.get("hint_lbl")
    if hint is not None:
        try:
            hint.configure(text="F11 или Esc — выйти из полного экрана")
            hint.place(relx=0.5, rely=0.96, anchor="s")
            hint.lift()
            win.after(3000, lambda: hint.place_forget())
        except Exception:
            pass


def _exit_fullscreen(api, win):
    ui = _state["ui"]

    _state["fullscreen"] = False

    try:
        win.attributes("-fullscreen", False)
    except Exception:
        pass

    # Геометрия
    saved = _state.get("saved_geometry")
    if saved:
        try:
            win.geometry(saved)
        except Exception:
            pass

    # Возвращаем фон панели визуализации
    vis_panel = ui.get("vis_panel")
    if vis_panel is not None:
        try:
            vis_panel.pack_forget()
            vis_panel.configure(fg_color=("gray14", "gray17"), corner_radius=8)
        except Exception:
            pass

    # vis_wrap
    vis_wrap = ui.get("vis_wrap")
    if vis_wrap is not None:
        try:
            vis_wrap.pack_forget()
            try:
                vis_wrap.configure(fg_color="#0a0a12", corner_radius=6)
            except Exception:
                pass
        except Exception:
            pass

    canvas = ui.get("vis_canvas")
    if canvas is not None:
        try:
            canvas.configure(bg="#0a0a12")
        except Exception:
            pass

    # Возвращаем верхнюю панель визуализации
    vis_top = ui.get("vis_top")
    if vis_top is not None:
        try:
            vis_top.pack(fill="x", padx=10, pady=(6, 2), before=vis_wrap)
        except Exception:
            pass

    if vis_wrap is not None:
        try:
            vis_wrap.pack(fill="x", padx=10, pady=(4, 10))
        except Exception:
            pass

    # Возвращаем панели в правильном порядке
    order = [
        ("top_bar",   {"fill": "x", "padx": 16, "pady": (10, 4)}),
        ("now_card",  {"fill": "x", "padx": 16, "pady": (4, 4)}),
        ("controls",  {"pady": (6, 4)}),
        ("vol_row",   {"fill": "x", "padx": 16, "pady": (4, 6)}),
        ("vis_panel", {"fill": "x", "padx": 16, "pady": (4, 4)}),
        ("head_row",  {"fill": "x", "padx": 16, "pady": (6, 2)}),
    ]
    for key, opts in order:
        w = ui.get(key)
        if w is not None:
            try:
                w.pack_forget()
                w.pack(**opts)
            except Exception:
                pass

    # Возвращаем нижний блок (плейлист)
    bottom = ui.get("bottom_block")
    if bottom is not None:
        try:
            bottom.pack_forget()
            bottom.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        except Exception:
            pass


# ============================================================
#  Проверка pygame
# ============================================================

def _open_requires_update_window(api):
    """Показывает окно: обновите Deskify до 1.2, чтобы плеер работал."""
    ctk = api["ctk"]
    app = api["app"]

    win = ctk.CTkToplevel(app)
    win.title("🎵 Музыка — требуется обновление")
    win.geometry("560x340")
    win.resizable(False, False)
    _state["window"] = win

    try:
        win.transient(app)
        win.lift()
        win.focus_force()
        win.attributes("-topmost", True)
        win.after(150, lambda: _unset_topmost(win))
    except Exception:
        pass

    # Заголовок
    ctk.CTkLabel(win, text="🎵 Музыкальный плеер недоступен",
                 font=ctk.CTkFont(size=17, weight="bold")).pack(pady=(24, 8))

    # Сообщение
    ctk.CTkLabel(
        win,
        text=("Для работы музыкального плеера нужна библиотека pygame.\n\n"
              "В Deskify 1.2 она уже встроена — просто обновитесь\n"
              "до версии 1.2 или новее.\n\n"
              "Если вы уже на 1.2, но видите это окно — значит сборка\n"
              "была сделана без поддержки pygame. Скачайте свежий\n"
              "установщик с GitHub."),
        justify="center", text_color="gray60",
        font=ctk.CTkFont(size=12),
    ).pack(padx=30, pady=(0, 14))

    # Кнопки
    btns = ctk.CTkFrame(win, fg_color="transparent")
    btns.pack(pady=(0, 20))

    def open_releases():
        try:
            import webbrowser
            webbrowser.open("https://github.com/deskify/Workspace/releases")
        except Exception:
            pass

    ctk.CTkButton(btns, text="📄 Открыть релизы", width=180, height=40,
                  fg_color="#1f538d",
                  command=open_releases).pack(side="left", padx=6)
    ctk.CTkButton(btns, text="Закрыть", width=120, height=40,
                  fg_color="gray30",
                  command=win.destroy).pack(side="left", padx=6)

    def on_close():
        _state["window"] = None
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", on_close)


# ============================================================
#  Главное окно
# ============================================================

def _open_player_window(api):
    # Проверка pygame
    if not HAS_PYGAME:
        _open_requires_update_window(api)
        return

    win = _state["window"]
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
    win.title("🎵 Музыкальный плеер")
    win.geometry("900x780")
    win.minsize(760, 640)
    _state["window"] = win
    _state["ui"] = {}
    _state["fullscreen"] = False

    try:
        win.transient(app)
        win.lift()
        win.focus_force()
        win.attributes("-topmost", True)
        win.after(150, lambda: _unset_topmost(win))
    except Exception:
        pass

    # ---------- Верхняя панель ----------
    top_bar = ctk.CTkFrame(win, fg_color="transparent")
    top_bar.pack(fill="x", padx=16, pady=(10, 4))
    _state["ui"]["top_bar"] = top_bar

    ctk.CTkLabel(top_bar, text="🎵 Музыкальный плеер",
                 font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")

    ctk.CTkButton(top_bar, text="⛶ Полный экран", width=150,
                  fg_color="gray30",
                  command=lambda: _toggle_fullscreen(api)).pack(side="right", padx=4)

    # ---------- Карточка "сейчас играет" ----------
    now_card = ctk.CTkFrame(win, corner_radius=8)
    now_card.pack(fill="x", padx=16, pady=(4, 4))
    _state["ui"]["now_card"] = now_card

    now_lbl = ctk.CTkLabel(now_card, text="Ничего не играет",
                            font=ctk.CTkFont(size=14, weight="bold"),
                            anchor="w", text_color="gray60")
    now_lbl.pack(fill="x", padx=12, pady=(10, 2))
    _state["ui"]["now_playing"] = now_lbl

    prog = ctk.CTkFrame(now_card, fg_color="transparent")
    prog.pack(fill="x", padx=12, pady=(0, 10))

    time_lbl = ctk.CTkLabel(prog, text="00:00 / 00:00",
                             font=ctk.CTkFont(size=11), text_color="gray60")
    time_lbl.pack(side="right", padx=(8, 0))
    _state["ui"]["time_lbl"] = time_lbl

    progress = ctk.CTkSlider(prog, from_=0, to=1,
                              command=lambda v: _seek(api, v))
    progress.set(0)
    progress.pack(side="left", fill="x", expand=True)
    _state["ui"]["progress"] = progress

    # ---------- Управление ----------
    controls = ctk.CTkFrame(win, fg_color="transparent")
    controls.pack(pady=(6, 4))
    _state["ui"]["controls"] = controls

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

    # ---------- Громкость ----------
    vol_row = ctk.CTkFrame(win, fg_color="transparent")
    vol_row.pack(fill="x", padx=16, pady=(4, 6))
    _state["ui"]["vol_row"] = vol_row

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
        try:
            vol_lbl.configure(text=f"{int(vol_var.get()*100)}%")
        except Exception:
            pass
    vol_var.trace_add("write", _on_vol)

    # ---------- Панель визуализации ----------
    vis_panel = ctk.CTkFrame(win, corner_radius=8)
    vis_panel.pack(fill="x", padx=16, pady=(4, 4))
    _state["ui"]["vis_panel"] = vis_panel

    vis_top = ctk.CTkFrame(vis_panel, fg_color="transparent")
    vis_top.pack(fill="x", padx=10, pady=(6, 2))
    _state["ui"]["vis_top"] = vis_top

    ctk.CTkLabel(vis_top, text="Визуализация:",
                 font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 6))

    vis_var = ctk.StringVar(value=_state["config"].get("vis_mode", "Волны"))
    ctk.CTkOptionMenu(vis_top, variable=vis_var, values=VIS_MODES, width=130,
                      command=lambda v: _change_vis_mode(api, v)).pack(side="left", padx=4)

    ctk.CTkLabel(vis_top, text="Чувствительность:").pack(side="left", padx=(16, 4))

    sens_var = ctk.DoubleVar(value=float(_state["config"].get("vis_sensitivity", 1.0)))
    sens_lbl = ctk.CTkLabel(vis_top, text=f"{sens_var.get():.1f}",
                             width=40, text_color="gray60",
                             font=ctk.CTkFont(size=11))
    sens_lbl.pack(side="right", padx=4)
    ctk.CTkSlider(vis_top, from_=0.2, to=3.0, variable=sens_var,
                  width=120,
                  command=lambda v: (_set_sensitivity(api, v),
                                      sens_lbl.configure(text=f"{float(v):.1f}"))
                  ).pack(side="right", padx=4)

    vis_wrap = ctk.CTkFrame(vis_panel, fg_color="#0a0a12", corner_radius=6,
                             height=200)
    vis_wrap.pack(fill="x", padx=10, pady=(4, 10))
    vis_wrap.pack_propagate(False)
    _state["ui"]["vis_wrap"] = vis_wrap

    vis_canvas = api["tk"].Canvas(vis_wrap, bg="#0a0a12",
                                    highlightthickness=0)
    vis_canvas.pack(fill="both", expand=True)
    _state["ui"]["vis_canvas"] = vis_canvas

    # ---------- Плейлист ----------
    head_row = ctk.CTkFrame(win, fg_color="transparent")
    head_row.pack(fill="x", padx=16, pady=(6, 2))
    _state["ui"]["head_row"] = head_row

    ctk.CTkLabel(head_row, text="Плейлист",
                 font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
    pl_count = ctk.CTkLabel(head_row, text="", text_color="gray60",
                             font=ctk.CTkFont(size=11))
    pl_count.pack(side="right")
    _state["ui"]["pl_count"] = pl_count

    # Нижний блок — контейнер плейлиста и кнопок
    bottom_block = ctk.CTkFrame(win, fg_color="transparent")
    bottom_block.pack(fill="both", expand=True, padx=16, pady=(0, 12))
    _state["ui"]["bottom_block"] = bottom_block

    list_frame = ctk.CTkScrollableFrame(bottom_block, fg_color=("gray90", "gray15"))
    list_frame.pack(fill="both", expand=True)
    _state["ui"]["list_frame"] = list_frame

    bottom_bar = ctk.CTkFrame(bottom_block, fg_color="transparent")
    bottom_bar.pack(fill="x", pady=(6, 0))
    _state["ui"]["bottom_bar"] = bottom_bar

    def refresh_playlist():
        for w in list(list_frame.winfo_children()):
            w.destroy()

        try:
            pl_count.configure(text=f"{len(_state['playlist'])} треков")
        except Exception:
            pass

        if not _state["playlist"]:
            ctk.CTkLabel(list_frame,
                         text="Плейлист пуст.\nДобавьте файлы или папку кнопками ниже.",
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
                font=ctk.CTkFont(size=11, weight="bold"))
            num_lbl.pack(side="left", padx=(4, 4))

            title = _track_title(path)
            ctk.CTkButton(
                row, text=title, anchor="w",
                fg_color="#1f538d" if is_current else "transparent",
                hover_color="gray30",
                text_color="white" if is_current else ("gray10", "gray90"),
                command=lambda i=idx: _play_index(api, i),
            ).pack(side="left", fill="x", expand=True, padx=(0, 4))

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

    ctk.CTkButton(bottom_bar, text="+ Файлы", width=110,
                  command=lambda: _add_files(api, refresh_playlist)
                  ).pack(side="left", padx=(0, 4))
    ctk.CTkButton(bottom_bar, text="+ Папка", width=110, fg_color="gray30",
                  command=lambda: _add_folder(api, refresh_playlist)
                  ).pack(side="left", padx=4)
    ctk.CTkButton(bottom_bar, text="Очистить", width=110, fg_color="gray30",
                  command=lambda: _clear_playlist(api, refresh_playlist)
                  ).pack(side="left", padx=4)

    refresh_playlist()

    # ---------- Горячие клавиши ----------
    def on_key(event):
        key = event.keysym.lower()
        if key == "space":
            _toggle_pause(api)
            return "break"
        elif key == "f11":
            _toggle_fullscreen(api)
            return "break"
        elif key == "escape":
            if _state.get("fullscreen"):
                _toggle_fullscreen(api)
                return "break"
        elif key == "right":
            _next_track(api)
        elif key == "left":
            _prev_track(api)

    win.bind("<KeyPress>", on_key)

    # Оверлей для подсказок
    hint_lbl = ctk.CTkLabel(win, text="", text_color="white",
                             fg_color="#000000", corner_radius=8,
                             font=ctk.CTkFont(size=13, weight="bold"))
    _state["ui"]["hint_lbl"] = hint_lbl

    _start_vis(api)

    _update_repeat_button(api)
    _update_shuffle_button(api)
    _update_play_button(api)
    _update_now_playing(api)
    if _state["playing"]:
        _start_tick(api)

    def on_close():
        _stop_vis(api)
        if _state["tick_job"] is not None:
            try:
                api["app"].after_cancel(_state["tick_job"])
            except Exception:
                pass
            _state["tick_job"] = None
        _state["window"] = None
        _state["fullscreen"] = False
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", on_close)


def _unset_topmost(win):
    try:
        if win.winfo_exists():
            win.attributes("-topmost", False)
    except Exception:
        pass


# ============================================================
#  Точка входа
# ============================================================

def register(api):
    _state["api"] = api
    _state["app"] = api["app"]
    _state["config"] = _load_config(api)
    _state["playlist"] = _load_playlist(api)

    api["add_plugin_button"](
        "🎵 Музыка",
        lambda: _open_player_window(api),
    )
