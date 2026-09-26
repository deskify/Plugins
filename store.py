# -*- coding: utf-8 -*-
"""
Магазин плагинов Deskify — v1.2.0

- Загружает catalog.json из репозитория deskify/Plugins (raw-URL GitHub).
- Показывает карточки плагинов, устанавливает и обновляет одним кликом.
- Сравнивает версии установленных и доступных плагинов → показывает «⬆ Обновить».
- Фильтр по тегам, поиск, локальная установка .py-файла.
- Кнопка «Перезагрузить плагины» — перезапускает _load_plugins() без рестарта Deskify.
- Кэш каталога: если сети нет, показывает последний удачный вариант.
- Проверка sha256 (если указан в каталоге).
- URL каталога хранится в data/store_config.json и меняется прямо в окне.
"""

import os
import re
import sys
import json
import time
import shutil
import threading
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import ssl
import hashlib
import webbrowser
from datetime import datetime


# --- Ссылки ---
GITHUB_REPO = "deskify/Plugins"
GITHUB_BRANCH = "main"
DEFAULT_CATALOG_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/catalog.json"
)
RAW_BASE_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/"
)
REPO_URL = f"https://github.com/{GITHUB_REPO}"

STORE_CONFIG_FILENAME = "store_config.json"
CACHE_FILENAME = "store_cache.json"
ERRORS_LOG_FILENAME = "plugin_errors.log"

PLUGIN_VERSION = "1.2.0"


# --- Состояние ---
_state = {
    "api": None,
    "app": None,
    "catalog": [],
    "window": None,
    "list_frame": None,
    "status_lbl": None,
    "search_var": None,
    "tag_filter": "все",
    "tag_bar": None,
    "installed_versions": {},   # filename -> version (из локальных .py)
}


# --- Конфиг магазина ---

def _store_config_path(api):
    return api["PathHelper"].get_path(STORE_CONFIG_FILENAME)


def _cache_path(api):
    return api["PathHelper"].get_path(CACHE_FILENAME)


def _load_store_config(api):
    default = {"catalog_url": DEFAULT_CATALOG_URL, "timeout": 10}
    path = _store_config_path(api)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            default.update(data)
    except Exception:
        pass
    return default


def _save_store_config(api, cfg):
    try:
        with open(_store_config_path(api), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --- Список установленных плагинов ---

def _installed_plugin_files(api):
    pd = api["PathHelper"].plugins_dir()
    try:
        return {f for f in os.listdir(pd) if f.endswith(".py")}
    except Exception:
        return set()


def _read_plugin_version(api, filename):
    """
    Пытается вытащить версию из файла плагина.
    Ищем в первых 4 КБ:
      VERSION = "1.2.3"
      __version__ = "1.2.3"
      # __plugin_version__ = "1.2.3"
    """
    path = os.path.join(api["PathHelper"].plugins_dir(), filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096)
    except Exception:
        return None

    patterns = [
        r'^\s*VERSION\s*=\s*["\']([^"\']+)["\']',
        r'^\s*__version__\s*=\s*["\']([^"\']+)["\']',
        r'__plugin_version__\s*=\s*["\']([^"\']+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, head, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


def _refresh_installed_versions(api):
    versions = {}
    for fname in _installed_plugin_files(api):
        v = _read_plugin_version(api, fname)
        if v:
            versions[fname] = v
    _state["installed_versions"] = versions


def _compare_versions(a, b):
    """
    Возвращает:
      1  если a > b
      0  если a == b
     -1  если a < b
      0  если не удалось распарсить (считаем равными)
    """
    if not a or not b:
        return 0

    def parse(s):
        return [int(x) if x.isdigit() else 0 for x in re.split(r"[^\d]+", str(s)) if x != ""]

    pa, pb = parse(a), parse(b)
    # добиваем нулями
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    if pa > pb:
        return 1
    if pa < pb:
        return -1
    return 0


# --- Имена / URL ---

def _plugin_filename(plugin):
    return plugin.get("file") or f"plugin_{plugin.get('id', 'unknown')}.py"


def _resolve_url(plugin):
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
        url, headers={"User-Agent": f"Deskify-Plugin-Store/{PLUGIN_VERSION}"}
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
    except Exception:
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

def _install_plugin(api, plugin, is_update=False):
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
    tmp_target = target + ".tmp"

    try:
        raw = _fetch_url(url, timeout=timeout)
    except Exception as e:
        return False, f"Ошибка загрузки: {e}"

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

    # атомарная запись: сначала во временный файл, потом rename
    try:
        with open(tmp_target, "wb") as f:
            f.write(raw)
        if os.path.exists(target):
            backup = target + ".bak"
            try:
                shutil.copy2(target, backup)
            except Exception:
                pass
        os.replace(tmp_target, target)
    except Exception as e:
        try:
            if os.path.exists(tmp_target):
                os.remove(tmp_target)
        except Exception:
            pass
        return False, f"Не удалось записать файл: {e}"

    name = plugin.get("name", plugin.get("id"))
    if is_update:
        return True, f"Плагин «{name}» обновлён. Перезапустите Deskify."
    return True, f"Плагин «{name}» установлен. Перезапустите Deskify."


def _uninstall_plugin(api, plugin):
    filename = _plugin_filename(plugin)
    target = os.path.join(api["PathHelper"].plugins_dir(), filename)
    if not os.path.exists(target):
        return False, "Файл не найден"
    try:
        os.remove(target)
        # подчистим .bak если остался
        bak = target + ".bak"
        if os.path.exists(bak):
            try:
                os.remove(bak)
            except Exception:
                pass
        return True, f"Плагин «{plugin.get('name', plugin.get('id'))}» удалён."
    except Exception as e:
        return False, f"Не удалось удалить: {e}"


def _install_from_local_file(api):
    """Локальная установка .py-файла: пользователь выбирает файл вручную."""
    path = api["filedialog"].askopenfilename(
        title="Выберите .py-файл плагина",
        filetypes=[("Python", "*.py"), ("Все файлы", "*.*")],
    )
    if not path:
        return
    filename = os.path.basename(path)
    if not filename.endswith(".py"):
        api["toast"]("Нужен .py-файл", "error")
        return
    if filename == "plugin_store.py":
        if not api["messagebox"].askyesno(
                "Внимание", "Вы устанавливаете сам магазин. Продолжить?"):
            return
    target = os.path.join(api["PathHelper"].plugins_dir(), filename)
    try:
        shutil.copy2(path, target)
        api["toast"](f"Файл «{filename}» установлен. Перезапустите Deskify.", "success")
        _refresh_installed_versions(api)
    except Exception as e:
        api["messagebox"].showerror("Ошибка", f"Не удалось установить: {e}")


# --- Открытие папки / репозитория ---

def _open_plugins_folder(api):
    pd = api["PathHelper"].plugins_dir()
    try:
        if sys.platform.startswith("win"):
            os.startfile(pd)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", pd])
        else:
            subprocess.Popen(["xdg-open", pd])
    except Exception as e:
        api["messagebox"].showinfo("Папка плагинов", f"Путь: {pd}\n\nНе удалось открыть: {e}")


def _open_repo(api):
    try:
        webbrowser.open(REPO_URL)
    except Exception:
        api["messagebox"].showinfo("Репозиторий", REPO_URL)


def _show_errors_log(api):
    path = api["PathHelper"].get_path(ERRORS_LOG_FILENAME)
    if not os.path.exists(path):
        api["messagebox"].showinfo("Ошибки плагинов", "Журнал ошибок пуст.")
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        api["messagebox"].showerror("Ошибка", str(e))
        return
    win = api["open_window"]("Ошибки загрузки плагинов", 700, 480)
    ctk = api["ctk"]
    tb = ctk.CTkTextbox(win, wrap="none", font=ctk.CTkFont(family="Consolas", size=11))
    tb.pack(fill="both", expand=True, padx=10, pady=10)
    tb.insert("1.0", content or "(пусто)")
    tb.configure(state="disabled")

    def clear_log():
        if api["messagebox"].askyesno("Очистить", "Удалить журнал ошибок?"):
            try:
                os.remove(path)
                win.destroy()
                api["toast"]("Журнал ошибок очищен", "success")
            except Exception as e:
                api["messagebox"].showerror("Ошибка", str(e))

    ctk.CTkButton(win, text="Очистить журнал", fg_color="#c0392b",
                  command=clear_log).pack(pady=(0, 10))


def _show_format_help(api):
    text = (
        "Каталог — это JSON-файл в корне репозитория:\n"
        f"{REPO_URL}\n\n"
        "Пример catalog.json:\n"
        '{\n'
        '  "version": 3,\n'
        '  "plugins": [\n'
        '    {\n'
        '      "id": "weather",\n'
        '      "name": "Погода",\n'
        '      "author": "Deskify",\n'
        '      "description": "Погода в вашем городе.",\n'
        '      "version": "2.0.0",\n'
        '      "icon": "🌤",\n'
        '      "file": "weather.py",\n'
        '      "tags": ["интернет", "погода"],\n'
        '      "sha256": "необязательно"\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        "Поля:\n"
        "• id — уникальный идентификатор (обязателен)\n"
        "• name — отображаемое имя\n"
        "• version — версия плагина (для сравнения «установлен / есть новее»)\n"
        "• file — имя .py в репозитории. Можно указать полный URL (https://...)\n"
        "• tags — список тегов для фильтра сверху\n"
        "• sha256 — если указан, файл проверяется на целостность\n\n"
        "Чтобы магазин видел версию установленного плагина, добавьте в его код:\n"
        '  VERSION = "1.2.3"\n'
        "или __version__ = \"1.2.3\" — версия ищется в первых 4 КБ файла."
    )
    api["messagebox"].showinfo("Формат каталога плагинов", text)


# --- Окно магазина ---

def _open_store_window(api):
    win = _state.get("window")
    if win is not None:
        try:
            if win.winfo_exists():
                win.deiconify(); win.lift(); win.focus_force()
                _refresh_installed_versions(api)
                _render_plugins(api, _state["list_frame"], _state.get("catalog", []), "search")
                return
        except Exception:
            pass

    ctk = api["ctk"]
    app = api["app"]

    win = ctk.CTkToplevel(app)
    win.title(f"🛒 Магазин плагинов Deskify v{PLUGIN_VERSION}")
    win.geometry("920x700")
    win.minsize(760, 560)
    _state["window"] = win

    _refresh_installed_versions(api)

    # Верх
    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=16, pady=(14, 2))
    ctk.CTkLabel(top, text="🛒 Магазин плагинов",
                 font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
    status_lbl = ctk.CTkLabel(top, text="", text_color="gray60",
                              font=ctk.CTkFont(size=12))
    status_lbl.pack(side="right")
    _state["status_lbl"] = status_lbl

    ctk.CTkLabel(
        win, text=f"Источник: {REPO_URL}",
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
                 placeholder_text="🔍 Поиск по названию / автору / описанию / тегу..."
                 ).pack(fill="x")

    # Теги — заполняются при загрузке каталога
    tag_bar = ctk.CTkFrame(win, fg_color="transparent")
    tag_bar.pack(fill="x", padx=16, pady=(2, 4))
    _state["tag_bar"] = tag_bar

    # Кнопки действий
    btn_row = ctk.CTkFrame(win, fg_color="transparent")
    btn_row.pack(fill="x", padx=16, pady=(0, 6))

    refresh_btn = ctk.CTkButton(btn_row, text="🔄 Обновить каталог", width=170)
    refresh_btn.pack(side="left", padx=(0, 6))
    ctk.CTkButton(btn_row, text="📥 Установить из .py", width=170, fg_color="gray30",
                  command=lambda: (_install_from_local_file(api),
                                   _render_plugins(api, list_frame,
                                                   _state.get("catalog", []), "search"))
                  ).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="🔁 Перезагрузить плагины", width=190, fg_color="gray30",
                  command=lambda: _reload_plugins_in_app(api)).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="📂 Папка плагинов", width=150, fg_color="gray30",
                  command=lambda: _open_plugins_folder(api)).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="⚠ Ошибки", width=100, fg_color="gray30",
                  command=lambda: _show_errors_log(api)).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="🔗 Репозиторий", width=150, fg_color="gray30",
                  command=lambda: _open_repo(api)).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="❓", width=40, fg_color="gray30",
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
            _state["tag_filter"] = "все"
            app.after(0, lambda: _rebuild_tag_bar(api))
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


def _reload_plugins_in_app(api):
    """Перезагружает плагины в главном окне без перезапуска Deskify."""
    app = api["app"]
    try:
        # удаляем все кнопки плагинов из сайдбара
        frame = getattr(app, "plugins_frame", None)
        if frame is not None:
            for w in list(frame.winfo_children()):
                w.destroy()
        # перезагружаем
        app._load_plugins()
        _refresh_installed_versions(api)
        _render_plugins(api, _state["list_frame"], _state.get("catalog", []), "search")
        api["toast"]("Плагины перезагружены", "success")
        api["notify"]("Магазин плагинов", "Плагины перезагружены.")
    except Exception as e:
        api["messagebox"].showerror("Ошибка", f"Не удалось перезагрузить: {e}")


def _render_loading(frame, ctk):
    for w in list(frame.winfo_children()):
        w.destroy()
    ctk.CTkLabel(frame, text="⏳ Загружаем каталог...",
                 text_color="gray60").pack(pady=40)


def _rebuild_tag_bar(api):
    """Строит чипы тегов над списком."""
    bar = _state.get("tag_bar")
    if bar is None:
        return
    ctk = api["ctk"]
    for w in list(bar.winfo_children()):
        w.destroy()

    tags = set()
    for p in _state.get("catalog", []):
        if not isinstance(p, dict):
            continue
        for t in (p.get("tags") or []):
            tags.add(str(t))

    all_tags = ["все"] + sorted(tags)
    current = _state.get("tag_filter", "все")

    def make_btn(name):
        def on_click():
            _state["tag_filter"] = name
            _rebuild_tag_bar(api)
            _render_plugins(api, _state["list_frame"], _state.get("catalog", []), "search")
        return on_click

    for name in all_tags:
        active = (name == current)
        ctk.CTkButton(
            bar, text=name, height=24, width=max(50, 12 * len(name)),
            fg_color="#1f538d" if active else "gray30",
            hover_color="#1c497d" if active else "gray40",
            command=make_btn(name),
        ).pack(side="left", padx=2, pady=2)


# --- Отрисовка списка ---

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
                  f"Проверьте, что catalog.json существует в репозитории:\n{REPO_URL}\n\n"
                  f"И что URL доступен:\n{DEFAULT_CATALOG_URL}"),
            text_color="gray60", justify="left",
        ).pack(pady=30, padx=10, anchor="w")
        return

    search_var = _state.get("search_var")
    query = search_var.get().strip().lower() if search_var else ""
    tag_filter = _state.get("tag_filter", "все")

    installed = _installed_plugin_files(api)
    _refresh_installed_versions(api)
    local_versions = _state.get("installed_versions", {})

    visible = 0
    total_installed = 0
    total_updates = 0

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
            haystack = " ".join(
                [str(name), str(author), str(desc), str(pid)] + [str(t) for t in tags]
            ).lower()
            if query not in haystack:
                continue

        if tag_filter != "все" and tag_filter not in [str(t) for t in tags]:
            continue

        filename = _plugin_filename(plugin)
        is_installed = filename in installed
        local_ver = local_versions.get(filename)
        update_available = False
        if is_installed and ver and local_ver:
            if _compare_versions(ver, local_ver) > 0:
                update_available = True

        if is_installed:
            total_installed += 1
        if update_available:
            total_updates += 1

        visible += 1

        card = ctk.CTkFrame(frame, corner_radius=8)
        card.pack(fill="x", padx=4, pady=5)

        ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=28)).pack(
            side="left", padx=(12, 6), pady=10)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=4, pady=8)

        title_line = str(name)
        if ver:
            title_line += f"  v{ver}"
        if is_installed and local_ver and local_ver != ver:
            title_line += f"  (у вас v{local_ver})"
        ctk.CTkLabel(info, text=title_line,
                     font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(anchor="w")
        if author:
            ctk.CTkLabel(info, text=f"Автор: {author}", text_color="gray60",
                         font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w")
        if desc:
            ctk.CTkLabel(info, text=str(desc), anchor="w", justify="left",
                         wraplength=520, font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 0))

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

        if update_available:
            def make_update(p=plugin):
                def do_update():
                    btn.configure(text="⏳ Обновляется...", state="disabled")

                    def worker():
                        ok, msg = _install_plugin(api, p, is_update=True)
                        api["app"].after(0, lambda: _on_action_done(
                            api, frame, p, ok, msg, "update"))
                    threading.Thread(target=worker, daemon=True).start()
                return do_update

            btn = ctk.CTkButton(btn_box, text="⬆ Обновить", width=140,
                                fg_color="#e67e22", hover_color="#cf711f",
                                command=make_update())
            btn.pack(pady=2)
            ctk.CTkButton(btn_box, text="Удалить", width=100,
                          fg_color="#c0392b", hover_color="#e74c3c",
                          command=_make_uninstall_handler(api, frame, plugin)
                          ).pack(pady=2)

        elif is_installed:
            ctk.CTkLabel(btn_box, text="✓ установлен",
                         text_color="#2ecc71",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(0, 4))
            ctk.CTkButton(btn_box, text="Удалить", width=100,
                          fg_color="#c0392b", hover_color="#e74c3c",
                          command=_make_uninstall_handler(api, frame, plugin)
                          ).pack(pady=2)

        else:
            def make_install(p=plugin):
                def do_install():
                    btn.configure(text="⏳ Устанавливается...", state="disabled")

                    def worker():
                        ok, msg = _install_plugin(api, p, is_update=False)
                        api["app"].after(0, lambda: _on_action_done(
                            api, frame, p, ok, msg, "install"))
                    threading.Thread(target=worker, daemon=True).start()
                return do_install

            btn = ctk.CTkButton(btn_box, text="⬇ Установить", width=140,
                                fg_color="#1f538d",
                                command=make_install())
            btn.pack(pady=2)

    # счётчик в статус-баре
    parts = [f"плагинов: {len(plugins)}", f"установлено: {total_installed}"]
    if total_updates:
        parts.append(f"⬆ обновлений: {total_updates}")
    api["app"].after(0, lambda: _state["status_lbl"].configure(
        text="  ·  ".join(parts),
        text_color="#e67e22" if
