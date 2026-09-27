# -*- coding: utf-8 -*-
"""
ОБЯЗАТЕЛЬНЫЙ ПАТЧ для Deskify.

Скачивает свежий store.py по прямой raw-ссылке и перезаписывает
старую версию в data/plugins/. Не использует каталог и sha256.
"""

import os
import threading
import urllib.request
from datetime import datetime


STORE_URL = "https://raw.githubusercontent.com/deskify/Plugins/main/store.py"
TARGET_FILENAME = "store.py"
OLD_NAMES = ["plugin_store.py", "plugin_store", "path.py"]


_state = {"api": None, "app": None, "window": None}


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Deskify-Updater/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def _do_update(api, log):
    log("Скачиваем " + STORE_URL)
    try:
        raw = _fetch(STORE_URL)
    except Exception as e:
        msg = "Ошибка загрузки: " + str(e)
        log("✖ " + msg)
        return False, msg

    log("Получено байт: " + str(len(raw)))

    text = raw.decode("utf-8", errors="replace")
    if "<html" in text[:300].lower():
        msg = "По ссылке пришёл HTML, а не Python"
        log("✖ " + msg)
        return False, msg

    if "def register" not in text or "add_plugin_button" not in text:
        msg = "Файл не похож на магазин"
        log("✖ " + msg)
        return False, msg

    pd = api["PathHelper"].plugins_dir()

    # Удаляем старые имена
    for old in OLD_NAMES:
        if old == TARGET_FILENAME:
            continue
        p = os.path.join(pd, old)
        if os.path.exists(p):
            try:
                os.remove(p)
                log("Удалён старый " + old)
            except Exception as e:
                log("⚠ Не удалился " + old + ": " + str(e))

    # Бэкап
    target = os.path.join(pd, TARGET_FILENAME)
    if os.path.exists(target):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = target + ".bak." + ts
        try:
            with open(target, "rb") as f:
                data = f.read()
            with open(bak, "wb") as f:
                f.write(data)
            log("Бэкап: " + os.path.basename(bak))
        except Exception as e:
            log("⚠ Бэкап не сделался: " + str(e))

    # Запись
    try:
        with open(target, "wb") as f:
            f.write(raw)
    except Exception as e:
        msg = "Не удалось записать: " + str(e)
        log("✖ " + msg)
        return False, msg

    log("✓ Записан " + target)
    return True, "Готово. Перезапустите Deskify."


def _open_window(api):
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
    win.title("🩹 ОБЯЗАТЕЛЬНЫЙ ПАТЧ")
    win.geometry("680x480")
    _state["window"] = win

    try:
        win.transient(app)
        win.lift()
        win.focus_force()
        win.attributes("-topmost", True)
        win.after(150, lambda: _unset_topmost(win))
    except Exception:
        pass

    ctk.CTkLabel(win, text="🩹 ОБЯЗАТЕЛЬНЫЙ ПАТЧ",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(16, 4))

    ctk.CTkLabel(win, text="Скачает свежий store.py и перезапишет старый.",
                 text_color="gray60").pack(pady=(0, 8))

    status = ctk.CTkLabel(win, text="Готов.", text_color="gray60")
    status.pack(pady=4)

    log_box = ctk.CTkTextbox(win, height=220, wrap="word",
                              font=ctk.CTkFont(family="Consolas", size=11))
    log_box.pack(fill="both", expand=True, padx=16, pady=8)

    def log(line):
        try:
            log_box.insert("end", line + "\n")
            log_box.see("end")
        except Exception:
            pass

    def on_update():
        status.configure(text="Обновление...", text_color="#e67e22")
        btn.configure(state="disabled")

        def worker():
            ok, msg = _do_update(api, log)

            def finish():
                btn.configure(state="normal")
                if ok:
                    status.configure(text="✓ Готово. Перезапустите Deskify.",
                                      text_color="#2ecc71")
                else:
                    status.configure(text="✖ " + msg, text_color="#e74c3c")

            api["app"].after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    btn = ctk.CTkButton(win, text="⬇ Применить патч", width=200, height=42,
                         fg_color="#1f538d", command=on_update)
    btn.pack(pady=10)

    def on_close():
        _state["window"] = None
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


def register(api):
    _state["api"] = api
    _state["app"] = api["app"]
    api["add_plugin_button"]("🩹 ОБЯЗАТЕЛЬНЫЙ ПАТЧ",
                              lambda: _open_window(api))
