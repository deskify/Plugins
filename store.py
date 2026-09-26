# -*- coding: utf-8 -*-
"""
Магазин плагинов Deskify.
Каталог берётся из репозитория https://github.com/deskify/Plugins

- Загружает catalog.json из raw-URL GitHub.
- Показывает карточки плагинов, позволяет устанавливать/удалять одним кликом.
- Кэширует каталог: если сети нет, показывает последний удачный вариант.
- Проверяет sha256 скачанного файла (если указан в каталоге).
- URL каталога можно менять прямо в окне магазина.
"""

import os
import json
import threading
import urllib.request
import urllib.error
import ssl
import hashlib
from datetime import datetime


# --- Новая ссылка: ваш репозиторий deskify/Plugins ---
GITHUB_REPO = "deskify/Plugins"
GITHUB_BRANCH = "main"
DEFAULT_CATALOG_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/catalog.json"
)
# Шаблон для относительных путей внутри каталога.
# Если в каталоге плагин указан как "weather.py", соберётся:
#   https://raw.githubusercontent.com/deskify/Plugins/main/weather.py
RAW_BASE_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/"
)


STORE_CONFIG_FILENAME = "store_config.json"
CACHE_FILENAME = "store_cache.json"


_state = {
    "api": None,
    "app": None,
    "catalog": [],
    "last_error": None,
    "window": None,
    "list_frame": None,
    "status_lbl": None,
    "search_var": None,
}


# --- Пути и настройки ---

def _store_config_path(api):
    return api["PathHelper"].get_path(STORE_CONFIG_FILENAME)


def _cache_path(api):
    return api["PathHelper"].get_path(CACHE_FILENAME)


def _load_store_config(api):
    path = _store_config_path(api)
    default = {"catalog_url": DEFAULT_CATALOG_URL, "timeout": 10}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        default.update(data or {})
        return default
    except Exception:
        return default


def _save_store_config(api, cfg):
    try:
        with open(_store_config_path(api), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _installed_plugin_files(api):
    pd = api["PathHelper"].plugins_dir()
    try:
        return {f for f in os.listdir(pd) if f.endswith(".py")}
    except Exception:
        return set()


def _plugin_filename(plugin):
    return plugin.get("file") or f"plugin_{plugin.get('id', 'unknown')}.py"


def _resolve_url(plugin):
    """
    Возвращает абсолютный URL к .py-файлу плагина.
    Поддерживает:
      - абсолютный http(s)-URL в поле 'url'
      - абсолютный URL в поле 'file' (если это ссылка)
      - относительный путь в 'url' или 'file' (собирается с RAW_BASE_URL)
    """
    candidate = (plugin.get("url") or plugin.get("file") or "").strip()
    if not candidate:
        return None
    if candidate.startswith(("http://", "https://")):
        return candidate
    return RAW_BASE_URL + candidate.lstrip("/")


# --- Сеть ---

def _fetch_url(url, timeout=10):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url, headers={"User-Agent": "Deskify-Plugin-Store/1.1"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _load_catalog(api):
    cfg = _load_store_config(api)
    url = cfg.get("catalog_url", DEFAULT_CATALOG_URL)
    timeout = int(cfg.get("timeout", 10))

    try:
        raw = _fetch_url(url, timeout=timeout)
        data = json.loads(raw.decode("utf-8"))
        plugins = data.get("plugins", []) if isinstance(data, dict) else (data or [])
        try:
            with open(_cache_path(api), "w", encoding="utf-8") as f:
                json.dump(
                    {"saved_at": datetime.now().isoformat(), "plugins": plugins},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception:
            pass
        return plugins, "online"
    except Exception as e:
        _state["last_error"] = str(e)
        cache_file = _cache_path(api)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("plugins", []), "cache"
            except Exception:
                pass
        return [], "error"


# --- Установка / удаление ---

def _install_plugin(api, plugin):
    cfg = _load_store_config(api)
    timeout = int(cfg.get("timeout", 10))

    url = _resolve_url(plugin)
    if not url:
        return False, "В каталоге не указан URL плагина"

    filename = _plugin_filename(plugin)
    if not filename.endswith(".py"):
        filename += ".py"

    plugins_dir = api["PathHelper"].plugins_dir()
    target = os.path.join(plugins_dir, filename)

    try:
        raw = _fetch_url(url, timeout=timeout)
    except Exception as e:
        return False, f"Ошибка загрузки: {e}"

    # Проверка, что это текст (а не HTML-страница)
    try:
        text = raw.decode("utf-8")
    except Exception:
        return False, "Скачанный файл не является текстом UTF-8"

    if "<html" in text[:200].lower():
        return False, "По URL отдаётся HTML (нужна raw-ссылка, а не blob)"

    expected_sha = (plugin.get("sha256") or "").lower().strip()
    if expected_sha:
        actual = hashlib.sha256(raw).hexdigest().lower()
        if actual != expected_sha:
            return False, "Несовпадение sha256 — файл мог быть подменён"

    try:
        with open(target, "wb") as f:
            f.write(raw)
    except Exception as e:
        return False, f"Не удалось записать файл: {e}"

    return True, f"Плагин «{plugin.get('name', plugin.get('id'))}» установлен. Перезапустите Deskify."


def _uninstall_plugin(api, plugin):
    filename = _plugin_filename(plugin)
    target = os.path.join(api["PathHelper"].plugins_dir(), filename)
    if not os.path.exists(target):
        return False, "Файл не найден"
    try:
        os.remove(target)
        return True, f"Плагин «{plugin.get('name', plugin.get('id'))}» удалён."
    except Exception as e:
        return False, f"Не удалось удалить: {e}"


# --- Окно магазина ---

def _open_store_window(api):
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
    win.title("🛒 Магазин плагинов Deskify")
    win.geometry("860x640")
    win.minsize(700, 520)
    _state["window"] = win

    # Верхняя панель
    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=16, pady=(14, 4))
    ctk.CTkLabel(top, text="🛒 Магазин плагинов",
                 font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
    status_lbl = ctk.CTkLabel(top, text="", text_color="gray60", font=ctk.CTkFont(size=12))
    status_lbl.pack(side="right")
    _state["status_lbl"] = status_lbl

    # Ссылка на источник
    ctk.CTkLabel(
        win, text=f"Источник: github.com/{GITHUB_REPO} ({GITHUB_BRANCH})",
        text_color="gray50", font=ctk.CTkFont(size=11),
    ).pack(anchor="w", padx=16)

    # URL каталога
    cfg_row = ctk.CTkFrame(win, fg_color="transparent")
    cfg_row.pack(fill="x", padx=16, pady=(6, 4))
    cfg = _load_store_config(api)
    url_var = ctk.StringVar(value=cfg.get("catalog_url", DEFAULT_CATALOG_URL))
    ctk.CTkEntry(cfg_row, textvariable=url_var,
                 placeholder_text="URL каталога плагинов (JSON)").pack(
        side="left", fill="x", expand=True, padx=(0, 6))

    def save_url():
        cfg["catalog_url"] = url_var.get().strip() or DEFAULT_CATALOG_URL
        _save_store_config(api, cfg)
        api["toast"]("URL каталога сохранён", "success")
        reload_catalog()

    ctk.CTkButton(cfg_row, text="Сохранить URL", width=130,
                  command=save_url).pack(side="left", padx=2)
    ctk.CTkButton(cfg_row, text="↺ По умолчанию", width=130, fg_color="gray30",
                  command=lambda: (url_var.set(DEFAULT_CATALOG_URL), save_url())
                  ).pack(side="left", padx=2)

    # Поиск
    search_row = ctk.CTkFrame(win, fg_color="transparent")
    search_row.pack(fill="x", padx=16, pady=(0, 4))
    search_var = ctk.StringVar()
    _state["search_var"] = search_var
    ctk.CTkEntry(search_row, textvariable=search_var,
                 placeholder_text="🔍 Поиск по названию / автору / тегу...").pack(fill="x")

    # Кнопки действий
    btn_row = ctk.CTkFrame(win, fg_color="transparent")
    btn_row.pack(fill="x", padx=16, pady=(2, 6))
    refresh_btn = ctk.CTkButton(btn_row, text="🔄 Обновить каталог", width=170)
    refresh_btn.pack(side="left", padx=(0, 6))
    ctk.CTkButton(btn_row, text="📂 Папка плагинов", width=150, fg_color="gray30",
                  command=lambda: _open_plugins_folder(api)).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="🔗 Открыть репозиторий", width=170, fg_color="gray30",
                  command=lambda: _open_repo(api)).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="❓ Формат", width=100, fg_color="gray30",
                  command=lambda: _show_format_help(api)).pack(side="left", padx=3)

    # Список
    list_frame = ctk.CTkScrollableFrame(win, fg_color=("gray90", "gray15"))
    list_frame.pack(fill="both", expand=True, padx=16, pady=(4, 12))
    _state["list_frame"] = list_frame

    def reload_catalog():
        _render_loading(list_frame, ctk)
        status_lbl.configure(text="Загрузка каталога...", text_color="gray60")
        refresh_btn.configure(state="disabled")

        def worker():
            plugins, source = _load_catalog(api)
            _state["catalog"] = plugins or []
            app.after(0, lambda: _render_plugins(api, list_frame, plugins, source))
            app.after(0, lambda: refresh_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    refresh_btn.configure(command=reload_catalog)

    def on_search_change(*_):
        _render_plugins(api, list_frame, _state.get("catalog", []), "search")

    search_var.trace_add("write", on_search_change)

    reload_catalog()
    win.protocol("WM_DELETE_WINDOW", lambda: _close_window(win))


def _close_window(win):
    try:
        win.destroy()
    except Exception:
        pass
    _state["window"] = None


def _open_plugins_folder(api):
    pd = api["PathHelper"].plugins_dir()
    try:
        if api["sys"].platform.startswith("win"):
            api["os"].startfile(pd)
        elif api["sys"].platform == "darwin":
            api["subprocess"].Popen(["open", pd])
        else:
            api["subprocess"].Popen(["xdg-open", pd])
    except Exception as e:
        api["messagebox"].showinfo("Папка плагинов", f"Путь: {pd}\n\nНе удалось открыть: {e}")


def _open_repo(api):
    url = f"https://github.com/{GITHUB_REPO}"
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        api["messagebox"].showinfo("Репозиторий", url)


def _show_format_help(api):
    text = (
        "Каталог — это JSON-файл, лежащий в корне репозитория:\n"
        f"github.com/{GITHUB_REPO}\n\n"
        "Пример catalog.json:\n"
        '{\n'
        '  "version": 1,\n'
        '  "plugins": [\n'
        '    {\n'
        '      "id": "weather",\n'
        '      "name": "Погода",\n'
        '      "author": "Deskify",\n'
        '      "description": "Погода в вашем городе.",\n'
        '      "version": "1.0.0",\n'
        '      "icon": "🌤",\n'
        '      "file": "weather.py",\n'
        '      "tags": ["интернет", "погода"],\n'
        '      "sha256": "необязательный-хеш-файла"\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        "Поля:\n"
        "• id — уникальный идентификатор (обязателен)\n"
        "• name — отображаемое имя\n"
        "• file — имя .py-файла в репозитории.\n"
        "   Можно указать полный URL (https://...), тогда поле url не нужно.\n"
        "• url — альтернатива file: прямая ссылка на .py\n"
        "• sha256 — если указан, файл проверяется на целостность\n\n"
        "Важно: для установки нужен raw-URL. Плагин сам собирает его из file,\n"
        f"подставляя {RAW_BASE_URL}\n"
    )
    api["messagebox"].showinfo("Формат каталога плагинов", text)


def _render_loading(frame, ctk):
    for w in list(frame.winfo_children()):
        w.destroy()
    ctk.CTkLabel(frame, text="⏳ Загружаем каталог...",
                 text_color="gray60").pack(pady=40)


def _render_plugins(api, frame, plugins, source):
    ctk = api["ctk"]

    for w in list(frame.winfo_children()):
        w.destroy()

    if source == "online":
        api["app"].after(0, lambda: _state["status_lbl"].configure(
            text="✓ Каталог обновлён из сети", text_color="#2ecc71"))
    elif source == "cache":
        api["app"].after(0, lambda: _state["status_lbl"].configure(
            text="⚠ Нет сети — показан кэш", text_color="#e67e22"))
    elif source == "error":
        api["app"].after(0, lambda: _state["status_lbl"].configure(
            text="✖ Ошибка загрузки", text_color="#e74c3c"))

    if not plugins:
        ctk.CTkLabel(
            frame,
            text=("Каталог пуст или не удалось загрузить.\n\n"
                  f"Проверьте, что файл catalog.json существует в репозитории:\n"
                  f"github.com/{GITHUB_REPO}\n\n"
                  f"И что URL доступен:\n{DEFAULT_CATALOG_URL}"),
            text_color="gray60", justify="left",
        ).pack(pady=30, padx=10, anchor="w")
        return

    search_var = _state.get("search_var")
    query = search_var.get().strip().lower() if search_var else ""
    installed = _installed_plugin_files(api)

    visible = 0
    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        pid = plugin.get("id", "")
        name = plugin.get("name", pid)
        author = plugin.get("author", "")
        desc = plugin.get("description", "")
        icon = plugin.get("icon", "🧩")
        ver = plugin.get("version", "")
        tags = plugin.get("tags", []) or []
        requires_requests = plugin.get("requires_requests", False)

        if query:
            haystack = " ".join([str(name), str(author), str(desc), str(pid)] + [str(t) for t in tags]).lower()
            if query not in haystack:
                continue

        visible += 1

        card = ctk.CTkFrame(frame, corner_radius=8)
        card.pack(fill="x", padx=4, pady=5)

        ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=28)).pack(
            side="left", padx=(12, 6), pady=10)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=4, pady=8)

        title_line = str(name) + (f"  v{ver}" if ver else "")
        ctk.CTkLabel(info, text=title_line,
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(anchor="w")
        if author:
            ctk.CTkLabel(info, text=f"Автор: {author}", text_color="gray60",
                         font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w")
        if desc:
            ctk.CTkLabel(info, text=str(desc), anchor="w", justify="left",
                         wraplength=470, font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 0))

        meta_bits = []
        if tags:
            meta_bits.append("🏷 " + ", ".join(str(t) for t in tags))
        if requires_requests:
            meta_bits.append("🌐 нужен requests")
        if meta_bits:
            ctk.CTkLabel(info, text="   ".join(meta_bits), text_color="gray50",
                         font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w", pady=(2, 0))

        btn_box = ctk.CTkFrame(card, fg_color="transparent")
        btn_box.pack(side="right", padx=10, pady=8)

        is_installed = _plugin_filename(plugin) in installed

        if is_installed:
            ctk.CTkLabel(btn_box, text="✓ установлен", text_color="#2ecc71",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(0, 4))

            def make_uninstall(p=plugin):
                def do_uninstall():
                    if not api["messagebox"].askyesno(
                            "Удаление", f"Удалить плагин «{p.get('name')}»?"):
                        return
                    ok, msg = _uninstall_plugin(api, p)
                    api["toast"](msg, "success" if ok else "error")
                    if ok:
                        api["notify"]("Магазин плагинов", msg)
                    _render_plugins(api, frame, _state.get("catalog", []), "search")
                return do_uninstall

            ctk.CTkButton(btn_box, text="Удалить", width=100,
                          fg_color="#c0392b", hover_color="#e74c3c",
                          command=make_uninstall()).pack(pady=2)
        else:
            def make_install(p=plugin):
                def do_install():
                    api["toast"](f"Устанавливаем «{p.get('name')}»...", "info")

                    def worker():
                        ok, msg = _install_plugin(api, p)
                        api["app"].after(0, lambda: _on_install_done(api, frame, p, ok, msg))
                    threading.Thread(target=worker, daemon=True).start()
                return do_install

            ctk.CTkButton(btn_box, text="⬇ Установить", width=120,
                          fg_color="#1f538d",
                          command=make_install()).pack(pady=2)

    if visible == 0:
        ctk.CTkLabel(frame, text="Ничего не найдено по запросу",
                     text_color="gray60").pack(pady=30)


def _on_install_done(api, frame, plugin, ok, msg):
    if ok:
        api["toast"](msg, "success")
        api["notify"]("Магазин плагинов", msg)
        _render_plugins(api, frame, _state.get("catalog", []), "search")
    else:
        api["toast"](msg, "error")
        api["messagebox"].showerror("Ошибка установки", msg)


# --- Точка входа плагина ---

def register(api):
    _state["api"] = api
    _state["app"] = api["app"]

    api["add_plugin_button"](
        "🛒 Магазин плагинов",
        lambda: _open_store_window(api),
    )