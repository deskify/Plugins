# -*- coding: utf-8 -*-
"""
Плагин «Интернет-радио» для Deskify.

Каталог станций: Radio Browser (https://www.radio-browser.info) — открытый,
бесплатный, без ключа. Поиск по имени / стране / тегу (жанру).

Воспроизведение: pygame.mixer (pip install pygame).
Избранное: data/radio_favorites.json.
Настройки: data/radio_config.json.

Управление:
  - поиск в верхней строке;
  - фильтр по стране и жанру;
  - клик по станции — играть;
  - звёздочка — добавить/убрать из избранного;
  - вкладка «Избранное» — только избранные;
  - вкладка «Все» — весь каталог;
  - громкость — ползунок справа.
"""

import os
import json
import random
import threading
import time
import urllib.request
import urllib.parse
import ssl
from datetime import datetime


try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    pygame = None
    HAS_PYGAME = False


FAVORITES_FILENAME = "radio_favorites.json"
CONFIG_FILENAME = "radio_config.json"

RADIO_API = "https://de1.api.radio-browser.info"   # европейский сервер
USER_AGENT = "Deskify-Radio/1.0"

# Популярные страны для быстрого фильтра (ISO-код -> отображаемое имя)
COUNTRIES = [
    ("RU", "Россия"),
    ("BY", "Беларусь"),
    ("UA", "Украина"),
    ("KZ", "Казахстан"),
    ("DE", "Германия"),
    ("US", "США"),
    ("GB", "Великобритания"),
    ("FR", "Франция"),
    ("IT", "Италия"),
    ("ES", "Испания"),
    ("JP", "Япония"),
    ("ALL", "Все страны"),
]

GENRES = [
    "ALL", "pop", "rock", "jazz", "classical", "electronic",
    "dance", "chillout", "news", "talk", "hiphop", "metal",
    "lounge", "ambient", "reggae", "country", "oldies",
]


_state = {
    "api": None,
    "app": None,
    "window": None,
    "stations": [],          # текущий загруженный список
    "favorites": {},         # {station_uuid: station_dict}
    "playing_uuid": None,
    "playing_name": "",
    "loading": False,
    "config": {"volume": 0.7},
    "ui": {},
    "initialized_mixer": False,
    "tab": "all",            # all | favorites
    "country": "ALL",
    "genre": "ALL",
    "search": "",
    "refresh_job": None,
}


# ============================================================
#  Хранилище
# ============================================================

def _favorites_path(api):
    return api["PathHelper"].get_path(FAVORITES_FILENAME)


def _config_path(api):
    return api["PathHelper"].get_path(CONFIG_FILENAME)


def _load_favorites(api):
    data = api["DataManager"].load_json(FAVORITES_FILENAME, {})
    return data if isinstance(data, dict) else {}


def _save_favorites(api):
    try:
        with open(_favorites_path(api), "w", encoding="utf-8") as f:
            json.dump(_state["favorites"], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_config(api):
    data = api["DataManager"].load_json(CONFIG_FILENAME, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("volume", 0.7)
    return data


def _save_config(api):
    try:
        with open(_config_path(api), "w", encoding="utf-8") as f:
            json.dump(_state["config"], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
#  Аудио
# ============================================================

def _ensure_mixer(api):
    if not HAS_PYGAME:
        return False, "pygame не установлен.\n\nВыполните: pip install pygame"
    if _state["initialized_mixer"]:
        return True, None
    try:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
        _state["initialized_mixer"] = True
        pygame.mixer.music.set_volume(float(_state["config"].get("volume", 0.7)))
        return True, None
    except Exception as e:
        return False, f"Не удалось инициализировать аудио: {e}"


def _play_station(api, station):
    ok, err = _ensure_mixer(api)
    if not ok:
        api["messagebox"].showerror("Радио", err)
        return

    url = station.get("url_resolved") or station.get("url")
    if not url:
        api["toast"]("У станции нет URL потока", "error")
        return

    name = station.get("name", "?")

    def worker():
        try:
            pygame.mixer.music.load(url)
            pygame.mixer.music.set_volume(float(_state["config"].get("volume", 0.7)))
            pygame.mixer.music.play()
            _state["playing_uuid"] = station.get("stationuuid")
            _state["playing_name"] = name
            api["app"].after(0, lambda: _update_now_playing(api, name))
            api["app"].after(0, lambda: _highlight_current(api))
        except Exception as e:
            err_text = str(e)
            api["app"].after(
                0,
                lambda: api["toast"](f"Не удалось воспроизвести: {err_text}", "error")
            )

    threading.Thread(target=worker, daemon=True).start()


def _stop(api):
    if HAS_PYGAME and _state["initialized_mixer"]:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
    _state["playing_uuid"] = None
    _state["playing_name"] = ""
    _update_now_playing(api, "")
    _highlight_current(api)


def _set_volume(api, value):
    value = max(0.0, min(1.0, float(value)))
    _state["config"]["volume"] = value
    if HAS_PYGAME and _state["initialized_mixer"]:
        try:
            pygame.mixer.music.set_volume(value)
        except Exception:
            pass
    _save_config(api)


# ============================================================
#  Radio Browser API
# ============================================================

def _fetch_json(url, timeout=12):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _search_stations(country="ALL", genre="ALL", name="", limit=200):
    """Возвращает список станций из Radio Browser."""
    params = {
        "limit": str(limit),
        "hidebroken": "true",
        "order": "clickcount",
        "reverse": "true",
    }
    if country and country != "ALL":
        params["countrycode"] = country
    if genre and genre != "ALL":
        params["tag"] = genre
    if name:
        params["name"] = name

    qs = urllib.parse.urlencode(params)
    url = f"{RADIO_API}/json/stations/search?{qs}"
    data = _fetch_json(url)
    return data if isinstance(data, list) else []


# ============================================================
#  UI-обновления
# ============================================================

def _update_now_playing(api, name):
    lbl = _state["ui"].get("now_playing")
    if lbl is None:
        return
    try:
        if not lbl.winfo_exists():
            return
        if name:
            lbl.configure(text=f"▶ Сейчас играет: {name}",
                          text_color="#2ecc71")
        else:
            lbl.configure(text="⏹ Остановлено", text_color="gray60")
    except Exception:
        pass


def _highlight_current(api):
    """Перерисовывает список, чтобы выделить играющую станцию."""
    # просто вызываем refresh, чтобы активная станция была видна
    refresh = _state["ui"].get("refresh_list")
    if refresh:
        try:
            refresh()
        except Exception:
            pass


# ============================================================
#  Главное окно
# ============================================================

def _open_radio_window(api):
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
    win.title("📻 Интернет-радио")
    win.geometry("960x680")
    win.minsize(820, 560)
    _state["window"] = win
    _state["ui"] = {}

    if not HAS_PYGAME:
        warn = ctk.CTkFrame(win, fg_color="#7a3a10", corner_radius=6)
        warn.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            warn,
            text="⚠ pygame не установлен — воспроизведение недоступно.\n"
                 "Установите:  pip install pygame",
            text_color="white", justify="left",
            font=ctk.CTkFont(size=12),
        ).pack(padx=10, pady=8, anchor="w")

    # ---------- Верх ----------
    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=16, pady=(12, 4))

    ctk.CTkLabel(top, text="📻 Интернет-радио",
                 font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")

    now_lbl = ctk.CTkLabel(top, text="⏹ Остановлено",
                           text_color="gray60",
                           font=ctk.CTkFont(size=12))
    now_lbl.pack(side="right")
    _state["ui"]["now_playing"] = now_lbl

    # ---------- Панель поиска ----------
    ctl = ctk.CTkFrame(win, fg_color="transparent")
    ctl.pack(fill="x", padx=16, pady=(4, 4))

    search_var = ctk.StringVar(value=_state["search"])
    search_entry = ctk.CTkEntry(ctl, textvariable=search_var,
                                placeholder_text="🔍 Поиск по названию станции...")
    search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

    country_var = ctk.StringVar(
        value=next((lbl for code, lbl in COUNTRIES if code == _state["country"]),
                   "Все страны"))
    country_menu = ctk.CTkOptionMenu(
        ctl, variable=country_var,
        values=[lbl for _, lbl in COUNTRIES], width=150,
    )
    country_menu.pack(side="left", padx=3)

    genre_var = ctk.StringVar(value=_state["genre"])
    genre_menu = ctk.CTkOptionMenu(
        ctl, variable=genre_var, values=GENRES, width=130,
    )
    genre_menu.pack(side="left", padx=3)

    ctk.CTkButton(ctl, text="Найти", width=90,
                  command=lambda: do_search()).pack(side="left", padx=3)

    # ---------- Вкладки ----------
    tabs = ctk.CTkFrame(win, fg_color="transparent")
    tabs.pack(fill="x", padx=16, pady=(2, 4))

    tab_all_btn = ctk.CTkButton(tabs, text="🌐 Каталог", width=140,
                                 command=lambda: set_tab("all"))
    tab_all_btn.pack(side="left", padx=(0, 6))
    tab_fav_btn = ctk.CTkButton(tabs, text="⭐ Избранное", width=140,
                                 fg_color="gray30",
                                 command=lambda: set_tab("favorites"))
    tab_fav_btn.pack(side="left", padx=(0, 6))

    _state["ui"]["tab_all"] = tab_all_btn
    _state["ui"]["tab_fav"] = tab_fav_btn

    # ---------- Громкость + управление ----------
    vol_row = ctk.CTkFrame(win, fg_color="transparent")
    vol_row.pack(fill="x", padx=16, pady=(0, 6))

    ctk.CTkButton(vol_row, text="⏹ Стоп", width=90, fg_color="#c0392b",
                  hover_color="#e74c3c",
                  command=lambda: _stop(api)).pack(side="left", padx=(0, 10))

    ctk.CTkLabel(vol_row, text="🔊").pack(side="left", padx=(0, 4))

    vol_var = ctk.DoubleVar(value=float(_state["config"].get("volume", 0.7)))
    ctk.CTkSlider(vol_row, from_=0, to=1, variable=vol_var,
                  command=lambda v: _set_volume(api, v),
                  width=180).pack(side="left", padx=(0, 8))

    vol_lbl = ctk.CTkLabel(vol_row, text=f"{int(vol_var.get()*100)}%",
                            text_color="gray60", width=50,
                            font=ctk.CTkFont(size=11))
    vol_lbl.pack(side="left")

    def on_vol_change(*_):
        vol_lbl.configure(text=f"{int(vol_var.get()*100)}%")
    vol_var.trace_add("write", on_vol_change)

    # ---------- Список станций ----------
    list_frame = ctk.CTkScrollableFrame(win, fg_color=("gray90", "gray15"))
    list_frame.pack(fill="both", expand=True, padx=16, pady=(4, 12))
    _state["ui"]["list_frame"] = list_frame

    status_lbl = ctk.CTkLabel(win, text="", text_color="gray60",
                               font=ctk.CTkFont(size=11))
    status_lbl.pack(fill="x", padx=16, pady=(0, 8))
    _state["ui"]["status_lbl"] = status_lbl

    # ---------- Логика ----------

    def set_tab(name):
        _state["tab"] = name
        if name == "all":
            tab_all_btn.configure(fg_color="#1f538d")
            tab_fav_btn.configure(fg_color="gray30")
        else:
            tab_all_btn.configure(fg_color="gray30")
            tab_fav_btn.configure(fg_color="#1f538d")
        refresh_list()

    def do_search():
        # читаем фильтры
        c_label = country_var.get()
        for code, lbl in COUNTRIES:
            if lbl == c_label:
                _state["country"] = code
                break
        _state["genre"] = genre_var.get()
        _state["search"] = search_var.get().strip()
        set_tab("all")
        load_stations()

    def load_stations():
        if _state["loading"]:
            return
        _state["loading"] = True
        status_lbl.configure(text="Загрузка каталога...", text_color="gray60")
        # показать плейсхолдер
        for w in list(list_frame.winfo_children()):
            w.destroy()
        ctk.CTkLabel(list_frame, text="⏳ Загрузка станций...",
                     text_color="gray60").pack(pady=40)

        def worker():
            try:
                stations = _search_stations(
                    country=_state["country"],
                    genre=_state["genre"],
                    name=_state["search"],
                    limit=200,
                )
                _state["stations"] = stations
                app.after(0, lambda: (_state.__setitem__("loading", False),
                                      refresh_list(),
                                      status_lbl.configure(
                                          text=f"Найдено: {len(stations)} станций",
                                          text_color="gray60")))
            except Exception as e:
                err = str(e)
                app.after(0, lambda: (_state.__setitem__("loading", False),
                                      status_lbl.configure(
                                          text=f"✖ Ошибка загрузки: {err}",
                                          text_color="#e74c3c"),
                                      refresh_list()))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_list():
        for w in list(list_frame.winfo_children()):
            w.destroy()

        if _state["tab"] == "favorites":
            stations = list(_state["favorites"].values())
            stations.sort(key=lambda s: s.get("name", "").lower())
            if not stations:
                ctk.CTkLabel(list_frame,
                             text="Избранных станций пока нет.\n"
                                  "Добавьте станцию звёздочкой из каталога.",
                             text_color="gray60", justify="center").pack(pady=40)
                return
        else:
            stations = _state["stations"]
            if not stations:
                ctk.CTkLabel(list_frame,
                             text="Ничего не найдено.\n"
                                  "Попробуйте изменить фильтры или нажмите «Найти».",
                             text_color="gray60", justify="center").pack(pady=40)
                return

        for st in stations:
            uuid = st.get("stationuuid") or ""
            name = st.get("name", "Без названия").strip() or "Без названия"
            country = st.get("country", "")
            tags = st.get("tags", "")
            bitrate = st.get("bitrate", 0)
            codec = st.get("codec", "")
            homepage = st.get("homepage", "")

            is_playing = (_state["playing_uuid"] == uuid)
            is_fav = uuid in _state["favorites"]

            row = ctk.CTkFrame(list_frame, corner_radius=6)
            row.pack(fill="x", padx=4, pady=3)

            # левая часть
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=6)

            head = ctk.CTkFrame(info, fg_color="transparent")
            head.pack(fill="x", anchor="w")

            mark = "▶ " if is_playing else ""
            color = "#2ecc71" if is_playing else None
            title_kwargs = {"text_color": color} if color else {}
            ctk.CTkLabel(head, text=f"{mark}{name}",
                         font=ctk.CTkFont(size=13, weight="bold"),
                         anchor="w", **title_kwargs).pack(side="left")

            if country:
                ctk.CTkLabel(head, text=f"  🌍 {country}",
                             text_color="gray60",
                             font=ctk.CTkFont(size=11)).pack(side="left", padx=6)

            meta = []
            if tags:
                meta.append("🏷 " + ", ".join(tags.split(",")[:4]))
            if bitrate:
                meta.append(f"📶 {bitrate} kbps")
            if codec:
                meta.append(codec.upper())
            if meta:
                ctk.CTkLabel(info, text="   ".join(meta),
                             text_color="gray50",
                             font=ctk.CTkFont(size=10),
                             anchor="w").pack(anchor="w")

            # правая часть: кнопки
            btns = ctk.CTkFrame(row, fg_color="transparent")
            btns.pack(side="right", padx=8, pady=6)

            def make_play(s=st):
                def do():
                    _play_station(api, s)
                return do

            ctk.CTkButton(btns, text="▶ Играть", width=100,
                          fg_color="#1f538d",
                          command=make_play()).pack(pady=1)

            def make_fav(s=st):
                def do():
                    uuid = s.get("stationuuid")
                    if not uuid:
                        return
                    if uuid in _state["favorites"]:
                        _state["favorites"].pop(uuid, None)
                        api["toast"](f"Удалено из избранного: {s.get('name')}",
                                     "info")
                    else:
                        _state["favorites"][uuid] = s
                        api["toast"](f"Добавлено: {s.get('name')}", "success")
                    _save_favorites(api)
                    refresh_list()
                return do

            fav_label = "★ Убрать" if is_fav else "☆ В избранное"
            fav_color = "#e67e22" if is_fav else "gray30"
            ctk.CTkButton(btns, text=fav_label, width=120,
                          fg_color=fav_color,
                          command=make_fav()).pack(pady=1)

            if homepage:
                def make_open(url=homepage):
                    def do():
                        import webbrowser
                        webbrowser.open(url)
                    return do
                ctk.CTkButton(btns, text="🌐 Сайт", width=80,
                              fg_color="gray30",
                              command=make_open()).pack(pady=1)

    _state["ui"]["refresh_list"] = refresh_list

    def on_search_change(*_):
        _state["search"] = search_var.get()
        # поиск по вводу запускается только кнопкой «Найти»

    search_var.trace_add("write", on_search_change)

    # первичная загрузка
    set_tab(_state["tab"])
    if _state["tab"] == "all":
        load_stations()
    else:
        refresh_list()

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
    _state["favorites"] = _load_favorites(api)
    _state["config"] = _load_config(api)

    api["add_plugin_button"](
        "📻 Радио",
        lambda: _open_radio_window(api),
    )