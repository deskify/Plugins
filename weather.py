# -*- coding: utf-8 -*-
"""Плагин «Погода» для Deskify. Open-Meteo, без ключа."""

import json
import urllib.request


def register(api):
    def show_weather():
        win = api["open_window"]("Погода", 420, 360)
        ctk = api["ctk"]

        ctk.CTkLabel(win, text="Город:",
                     font=ctk.CTkFont(weight="bold")).pack(pady=(14, 2))
        city_entry = ctk.CTkEntry(win, width=220, placeholder_text="Москва")
        city_entry.pack()
        result = ctk.CTkLabel(win, text="", justify="left",
                              font=ctk.CTkFont(size=13))
        result.pack(pady=14, padx=16, fill="both", expand=True)

        def fetch():
            city = city_entry.get().strip() or "Москва"
            result.configure(text="Загрузка...")

            def worker():
                try:
                    # 1) геокодинг
                    geo_url = ("https://geocoding-api.open-meteo.com/v1/search?"
                               f"name={urllib.parse.quote(city)}&count=1&language=ru")
                    with urllib.request.urlopen(geo_url, timeout=8) as r:
                        geo = json.loads(r.read().decode("utf-8"))
                    if not geo.get("results"):
                        win.after(0, lambda: result.configure(text="Город не найден"))
                        return
                    g = geo["results"][0]
                    lat, lon = g["latitude"], g["longitude"]

                    # 2) погода
                    w_url = ("https://api.open-meteo.com/v1/forecast?"
                             f"latitude={lat}&longitude={lon}&current_weather=true")
                    with urllib.request.urlopen(w_url, timeout=8) as r:
                        w = json.loads(r.read().decode("utf-8"))
                    cw = w["current_weather"]
                    text = (f"{g['name']}, {g.get('country', '')}\n"
                            f"🌡 {cw['temperature']}°C\n"
                            f"💨 {cw['windspeed']} км/ч\n"
                            f"🧭 {cw['winddirection']}°")
                    win.after(0, lambda: result.configure(text=text))
                except Exception as e:
                    err = str(e)
                    win.after(0, lambda: result.configure(text=f"Ошибка: {err}"))

            api["threading"].Thread(target=worker, daemon=True).start()

        ctk.CTkButton(win, text="Узнать погоду", command=fetch).pack(pady=6)
        city_entry.bind("<Return>", lambda e: fetch())

    api["add_plugin_button"]("🌤 Погода", show_weather)