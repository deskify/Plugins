# -*- coding: utf-8 -*-
"""
Магазин плагинов Deskify.

Возможности:
  - загрузка каталога из raw-URL GitHub с фолбэком в кэш;
  - установка / обновление / удаление плагинов в один клик;
  - индикатор загрузки на кнопке;
  - проверка обновлений по номеру версии (правильно обрабатывает 1.0 == 1.0.0);
  - поиск установленных плагинов по обоим именам: weather.py и plugin_weather.py;
  - автоопределение версии для вручную поставленных плагинов;
  - установка плагина из локального .py-файла;
  - просмотр лога ошибок плагинов;
  - фильтр по тегам;
  - кнопка «Перезагрузить плагины» без перезапуска Deskify;
  - окно поднимается поверх главного окна.

ВАЖНО: sha256 НЕ проверяется. Поле в каталоге игнорируется.

Каталог по умолчанию: https://github.com/deskify/Plugins
"""

import os
import json
import threading
import urllib.request
import urllib.error
import ssl
import webbrowser
import re as _re
from datetime import datetime


GITHUB_REPO = "deskify/Plugins"
GITHUB_BRANCH = "main"
DEFAULT_CATALOG_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/catalog.json"
)
RAW_BASE_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/"
)

STORE_CONFIG_FILENAME = "store_config.json"
CACHE_FILENAME = "store_cache.json"
INSTALLED_VERSIONS_FILENAME = "installed_plugin_versions.json"


_state = {
    "api": None,
    "app": None,
    "catalog": [],
    "window": None,
    "list_frame": None,
    "status_lbl": None,
    "search_var": None,
    "active_tag": None,
    "installed_versions": {},
}


# ============================================================
#  Пути и конфиг
# ============================================================

def _store_config_path(api):
    return api["PathHelper"].get_path(STORE_CONFIG_FILENAME)


def _cache_path(api):
    return api["PathHelper"].get_path(CACHE_FILENAME)


def _installed_versions_path(api):
    return api["PathHelper"].get_path(INSTALLED_VERSIONS_FILENAME)


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


def _load_installed_versions(api):
    path = _installed_versions_path(api)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_installed_versions(api):
    try:
        with open(_installed_versions_path(api), "w", encoding="utf-8") as f:
            json.dump(_state["installed_versions"], f,
                      ensure_ascii=False, indent=2)
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
    candidate = (plugin.get("url") or plugin.get("file") or "").strip()
    if not candidate:
        return None
    if candidate.startswith(("http://", "https://")):
        return candidate
    return RAW_BASE_URL + candidate.lstrip("/")


# ============================================================
#  Сравнение версий
# ============================================================

def _parse_version(v):
    """'1.0.0' -> (1, 0, 0). Хвостовые нули отбрасываются: 1.0.0 == 1.0 == 1."""
    if not v:
        return (0,)
    parts = []
    for chunk in str(v).split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    if not parts:
        return (0,)
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _is_newer(installed_v, catalog_v):
    if not installed_v or not catalog_v:
        return False
    return _parse_version(catalog_v) > _parse_version(installed_v)


def _versions_equal(a, b):
    if not a or not b:
        return False
    return _parse_version(a) == _parse_version(b)


# ============================================================
#  Проверка установки (по basename)
# ============================================================

def _is_file_installed(plugin, installed_set):
    """Проверяет, установлен ли плагин. Сравнивает по basename с учётом
    обоих вариантов имени: weather.py и plugin_weather.py."""
    if not installed_set:
        return False
    candidates = set()

    name_in_catalog = plugin.get("file") or ""
    if name_in_catalog:
        base = os.path.basename(name_in_catalog)
        candidates.add(base)
        if not base.endswith(".py"):
            candidates.add(base + ".py")

    pid = plugin.get("id") or ""
    if pid:
        candidates.add(f"{pid}.py")
        candidates.add(f"plugin_{pid}.py")

    candidates.discard("")

    for c in candidates:
        if c in installed_set:
            return True
    return False


def _heal_installed_versions(api, catalog):
    """
    Если плагин лежит в data/plugins/, но его нет в
    installed_plugin_versions.json (например, поставлен вручную) —
    считаем его версию равной версии из каталога.
    Это предотвращает бесконечное «⬆ Обновить» на неизвестной версии.
    """
    changed = False
    installed = _installed_plugin_files(api)
    for plugin in catalog:
        if not isinstance(plugin, dict):
            continue
        pid = plugin.get("id") or ""
        if not pid:
            continue
        if pid in _state["installed_versions"]:
            continue
        if _is_file_installed(plugin, installed):
            _state["installed_versions"][pid] = plugin.get("version", "")
            changed = True
    if changed:
        _save_installed_versions(api)


# ============================================================
#  Сеть
# ============================================================

def _fetch_url(url, timeout=10):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url, headers={"User-Agent": "Deskify-Plugin-Store/1.4"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _humanize_network_error(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 404:
            return "Файл не найден (404). Проверьте, что catalog.json в корне репозитория и ветка — main."
        if e.code == 403:
            return "Доступ запрещён (403). Возможно, репозиторий приватный."
        return f"HTTP {e.code}: {e.reason}"
    if isinstance(e, urllib.error.URLError):
        return "Нет соединения с интернетом или сервер недоступен."
    if isinstance(e, ssl.SSLError):
        return "Ошибка SSL. Проверьте системное время и сертификаты."
    if "timed out" in str(e).lower():
        return "Превышено время ожидания. Проверьте соединение."
    return str(e)


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
        return plugins, "online", None
    except Exception as e:
        msg = _humanize_network_error(e)
        cache_file = _cache_path(api)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("plugins", []), "cache", msg
            except Exception:
                pass
        return [], "error", msg


# ============================================================
#  Установка / удаление (без sha256)
# ============================================================

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
        return False, f"Не удалось скачать: {_humanize_network_error(e)}"

    try:
        text = raw.decode("utf-8")
    except Exception:
        return False, "Скачанный файл не является текстом UTF-8"

    if "<html" in text[:200].lower():
        return False, "По ссылке пришёл HTML. Нужна raw-ссылка, а не blob."

    # sha256 НЕ проверяется

    try:
        with open(target, "wb") as f:
            f.write(raw)
    except Exception as e:
        return False, f"Не удалось записать файл: {e}"

    pid = plugin.get("id") or filename
    _state["installed_versions"][pid] = plugin.get("version", "")
    _save_installed_versions(api)

    name = plugin.get("name", plugin.get("id", filename))
    return True, f"Плагин «{name}» установлен. Нажмите «Перезагрузить плагины» или перезапустите Deskify."


def _uninstall_plugin(api, plugin):
    filename = _plugin_filename(plugin)
    target = os.path.join(api["PathHelper"].plugins_dir(), filename)
    if not os.path.exists(target):
        return False, "Файл не найден"
    try:
        os.remove(target)
        pid = plugin.get("id") or filename
        _state["installed_versions"].pop(pid, None)
        _save_installed_versions(api)
        name = plugin.get("name", plugin.get("id", filename))
        return True, f"Плагин «{name}» удалён."
    except Exception as e:
        return False, f"Не удалось удалить: {e}"


def _install_from_file(api, refresh_cb):
    path = api["filedialog"].askopenfilename(
        title="Выберите .py-файл плагина",
        filetypes=[("Python", "*.py"), ("Все файлы", "*.*")],
    )
    if not path:
        return
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:
        api["messagebox"].showerror("Ошибка", f"Не удалось прочитать файл: {e}")
        return

    filename = os.path.basename(path)
    if not filename.endswith(".py"):
        filename += ".py"

    target = os.path.join(api["PathHelper"].plugins_dir(), filename)
    try:
        with open(target, "wb") as f:
            f.write(raw)
    except Exception as e:
        api["messagebox"].showerror("Ошибка", f"Не удалось записать: {e}")
        return

    # Пытаемся вытащить версию из кода плагина
    version = ""
    try:
        text = raw.decode("utf-8", errors="ignore")
        m = _re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        if not m:
            m = _re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            version = m.group(1)
    except Exception:
        pass

    if version:
        stem = filename[:-3]
        pid = stem[len("plugin_"):] if stem.startswith("plugin_") else stem
        _state["installed_versions"][pid] = version
        _save_installed_versions(api)

    api["toast"](f"Установлен «{filename}». Не забудьте перезагрузить плагины.", "success")
    if refresh_cb:
        refresh_cb()


# ============================================================
#  Перезагрузка плагинов
# ============================================================

def _reload_plugins(api):
    app = api["app"]
    try:
        pf = getattr(app, "plugins_frame", None)
        if pf is not None:
            for w in list(pf.winfo_children()):
                try:
                    w.destroy()
                except Exception:
                    pass
        app._load_plugins()
        api["toast"]("Плагины перезагружены", "success")
        api["notify"]("Магазин плагинов", "Плагины перезагружены без перезапуска.")
    except Exception as e:
        api["messagebox"].showerror("Ошибка", f"Не удалось перезагрузить: {e}")


# ============================================================
#  Логи ошибок
# ============================================================

def _show_plugin_errors(api):
    log_path = api["PathHelper"].get_path("plugin_errors.log")
    if not os.path.exists(log_path):
        api["messagebox"].showinfo("Ошибки плагинов",
                                    "Лог пуст — плагины загрузились без ошибок.")
        return
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        api["messagebox"].showerror("Ошибка", f"Не удалось прочитать лог: {e}")
        return

    if not content:
        api["messagebox"].showinfo("Ошибки плагинов", "Лог пуст.")
        return

    win = api["ctk"].CTkToplevel(_state["window"] or api["app"])
    win.title("⚠ Ошибки загрузки плагинов")
    win.geometry("700x460")
    _raise_window(win, api["app"])

    box = api["ctk"].CTkTextbox(
        win, wrap="word",
        font=api["ctk"].CTkFont(family="Consolas", size=12)
    )
    box.pack(fill="both", expand=True, padx=12, pady=12)
    box.insert("1.0", content)

    def clear_log():
        if api["messagebox"].askyesno("Очистить лог", "Удалить файл лога?"):
            try:
                os.remove(log_path)
                win.destroy()
                api["toast"]("Лог очищен", "success")
            except Exception as e:
                api["messagebox"].showerror("Ошибка", str(e))

    api["ctk"].CTkButton(win, text="Очистить лог", fg_color="#c0392b",
                         command=clear_log).pack(pady=(0, 12))


# ============================================================
#  Поднятие окна поверх главного
# ============================================================

def _raise_window(win, parent=None):
    """Поднимает Toplevel поверх главного окна."""
    try:
        if parent is not None:
            win.transient(parent)
    except Exception:
        pass
    try:
        win.lift()
    except Exception:
        pass
    try:
        win.focus_force()
    except Exception:
        pass
    try:
        win.attributes("-topmost", True)
        win.after(150, lambda: _safe_unset_topmost(win))
    except Exception:
        pass


def _safe_unset_topmost(win):
    try:
        if win.winfo_exists():
            win.attributes("-topmost", False)
    except Exception:
        pass


# ============================================================
#  Открытие папки и репозитория
# ============================================================

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
        api["messagebox"].showinfo("Папка плагинов",
                                    f"Путь: {pd}\n\nНе удалось открыть: {e}")


def _open_repo(api):
    webbrowser.open(f"https://github.com/{GITHUB_REPO}")


def _show_format_help(api):
    text = (
        "Каталог — это JSON-файл, лежащий в корне репозитория:\n"
        f"github.com/{GITHUB_REPO}\n\n"
        "Минимальная запись плагина:\n"
        '{\n'
        '  "id": "weather",\n'
        '  "name": "Погода",\n'
        '  "version": "2.0.0",\n'
        '  "icon": "🌤",\n'
        '  "file": "weather.py",\n'
        '  "description": "Погода в вашем городе.",\n'
        '  "author": "Deskify",\n'
        '  "tags": ["интернет", "погода"]\n'
        '}\n\n'
        "Поля:\n"
        "• id — уникальный идентификатор (обязателен)\n"
        "• name, description, author — для отображения\n"
        "• version — сравнение с установленной версией\n"
        "• file — имя .py в репозитории (или полный URL)\n"
        "• url — альтернатива file: прямая ссылка\n"
        "• tags — массив строк для фильтра\n"
        "• requires_requests — пометка про пакет requests\n\n"
        "sha256 в этой версии магазина НЕ проверяется —\n"
        "поле игнорируется, даже если указано в каталоге.\n\n"
        "Сравнение версий: 1.0 == 1.0.0 == 1.0.0.0.\n"
        "Хвостовые нули не считаются различием."
    )
    api["messagebox"].showinfo("Формат каталога", text)


# ============================================================
#  Отрисовка
# ============================================================

def _render_loading(frame, ctk):
    for w in list(frame.winfo_children()):
        w.destroy()
    ctk.CTkLabel(frame, text="⏳ Загружаем каталог...",
                 text_color="gray60").pack(pady=40)


def _collect_tags(plugins):
    tags = set()
    for p in plugins:
        if isinstance(p, dict):
            for t in (p.get("tags") or []):
                if t:
                    tags.add(str(t))
    return sorted(tags)


def _render_plugins(api, frame, plugins, source):
    ctk = api["ctk"]

    for w in list(frame.winfo_children()):
        w.destroy()

    # Чиним версии установленных плагинов, если их нет в файле версий
    _heal_installed_versions(api, plugins)

    if source == "online":
        _state["status_lbl"].configure(text="✓ Каталог обновлён",
                                        text_color="#2ecc71")
    elif source == "cache":
        _state["status_lbl"].configure(text="⚠ Нет сети — кэш",
                                        text_color="#e67e22")
    elif source == "error":
        _state["status_lbl"].configure(text="✖ Ошибка загрузки",
                                        text_color="#e74c3c")
    else:
        _state["status_lbl"].configure(text="", text_color="gray60")

    if not plugins:
        ctk.CTkLabel(
            frame,
            text=("Каталог пуст или не загрузился.\n\n"
                  f"Проверьте, что файл catalog.json есть в репозитории:\n"
                  f"github.com/{GITHUB_REPO}\n\n"
                  f"URL по умолчанию:\n{DEFAULT_CATALOG_URL}"),
            text_color="gray60", justify="left",
        ).pack(pady=30, padx=10, anchor="w")
        return

    search_var = _state.get("search_var")
    query = search_var.get().strip().lower() if search_var else ""
    active_tag = _state.get("active_tag")
    installed = _installed_plugin_files(api)
    installed_versions = _state["installed_versions"]

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
            haystack = " ".join([str(name), str(author), str(desc), str(pid)]
                                + [str(t) for t in tags]).lower()
            if query not in haystack:
                continue
        if active_tag and active_tag not in tags:
            continue

        visible += 1

        # --- Определяем статус плагина ---
        is_installed = _is_file_installed(plugin, installed)
        installed_v = installed_versions.get(pid, "")

        if not is_installed:
            status = "not_installed"
            update_available = False
        elif not installed_v:
            # файл есть, но версия неизвестна — не дёргаем «обновить»
            status = "installed"
            update_available = False
        elif _is_newer(installed_v, ver):
            status = "update_available"
            update_available = True
        else:
            status = "installed"
            update_available = False

        card = ctk.CTkFrame(frame, corner_radius=8)
        card.pack(fill="x", padx=4, pady=5)

        ctk.CTkLabel(card, text=icon,
                     font=ctk.CTkFont(size=28)).pack(side="left",
                                                     padx=(12, 6), pady=10)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=4, pady=8)

        title_line = str(name)
        if ver:
            title_line += f"  v{ver}"
        if update_available:
            title_line += f"  ⬆ (установлен v{installed_v or '?'})"

        ctk.CTkLabel(info, text=title_line,
                     font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(anchor="w")
        if author:
            ctk.CTkLabel(info, text=f"Автор: {author}",
                         text_color="gray60",
                         font=ctk.CTkFont(size=11),
                         anchor="w").pack(anchor="w")
        if desc:
            ctk.CTkLabel(info, text=str(desc), anchor="w", justify="left",
                         wraplength=470,
                         font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 0))

        meta_bits = []
        if tags:
            meta_bits.append("🏷 " + ", ".join(str(t) for t in tags))
        if requires_requests:
            meta_bits.append("🌐 нужен requests")
        if meta_bits:
            ctk.CTkLabel(info, text="   ".join(meta_bits),
                         text_color="gray50",
                         font=ctk.CTkFont(size=11),
                         anchor="w").pack(anchor="w", pady=(2, 0))

        btn_box = ctk.CTkFrame(card, fg_color="transparent")
        btn_box.pack(side="right", padx=10, pady=8)

        if status == "installed":
            ctk.CTkLabel(btn_box, text="✓ установлен",
                         text_color="#2ecc71",
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
            if status == "update_available":
                btn_label = "⬆ Обновить"
                btn_color = "#e67e22"
            else:
                btn_label = "⬇ Установить"
                btn_color = "#1f538d"

            def make_install(p=plugin, box=btn_box):
                def do_install():
                    for w in list(box.winfo_children()):
                        try:
                            w.configure(state="disabled")
                        except Exception:
                            pass
                    btn_holder = api["ctk"].CTkButton(
                        box, text="⏳ Загрузка...", width=120,
                        fg_color="gray40", state="disabled",
                    )
                    btn_holder.pack(pady=2)

                    def worker():
                        ok, msg = _install_plugin(api, p)
                        api["app"].after(
                            0,
                            lambda: _on_install_done(api, frame, p, ok, msg,
                                                      box, btn_holder),
                        )
                    threading.Thread(target=worker, daemon=True).start()
                return do_install

            ctk.CTkButton(btn_box, text=btn_label, width=120,
                          fg_color=btn_color,
                          command=make_install()).pack(pady=2)

            if update_available:
                ctk.CTkLabel(btn_box, text="доступно обновление",
                             text_color="#e67e22",
                             font=ctk.CTkFont(size=10)).pack()

    if visible == 0:
        ctk.CTkLabel(frame, text="Ничего не найдено по фильтру",
                     text_color="gray60").pack(pady=30)


def _on_install_done(api, frame, plugin, ok, msg, box, btn_holder):
    try:
        btn_holder.destroy()
    except Exception:
        pass
    if ok:
        api["toast"](msg, "success")
        api["notify"]("Магазин плагинов", msg)
        _render_plugins(api, frame, _state.get("catalog", []), "search")
    else:
        api["toast"](msg, "error")
        api["messagebox"].showerror("Ошибка установки", msg)
        _render_plugins(api, frame, _state.get("catalog", []), "search")


def _render_tag_filter(api, tag_row, plugins, list_frame):
    ctk = api["ctk"]
    for w in list(tag_row.winfo_children()):
        w.destroy()

    tags = _collect_tags(plugins)
    if not tags:
        return

    def set_tag(tag):
        _state["active_tag"] = tag
        _render_tag_filter(api, tag_row, plugins, list_frame)
        _render_plugins(api, list_frame, _state.get("catalog", []), "search")

    def style_btn(btn, active):
        if active:
            btn.configure(fg_color="#1f538d", text_color="white",
                          hover_color="#1c497d")
        else:
            btn.configure(fg_color="transparent",
                          text_color=("gray10", "gray90"),
                          hover_color=("gray70", "gray30"))

    all_btn = ctk.CTkButton(tag_row, text="Все", width=70, height=26,
                            command=lambda: set_tag(None))
    all_btn.pack(side="left", padx=2)
    style_btn(all_btn, _state["active_tag"] is None)

    for tag in tags:
        b = ctk.CTkButton(tag_row, text=tag, width=110, height=26,
                          command=lambda t=tag: set_tag(t))
        b.pack(side="left", padx=2)
        style_btn(b, _state["active_tag"] == tag)


# ============================================================
#  Главное окно
# ============================================================

def _open_store_window(api):
    win = _state.get("window")
    if win is not None:
        try:
            if win.winfo_exists():
                win.deiconify()
                _raise_window(win, api["app"])
                return
        except Exception:
            pass

    ctk = api["ctk"]
    app = api["app"]

    _state["installed_versions"] = _load_installed_versions(api)

    win = ctk.CTkToplevel(app)
    win.title("🛒 Магазин плагинов Deskify")
    win.geometry("900x680")
    win.minsize(720, 540)
    _state["window"] = win

    # Поднять поверх главного окна
    _raise_window(win, app)

    # Заголовок + статус
    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=16, pady=(14, 4))
    ctk.CTkLabel(top, text="🛒 Магазин плагинов",
                 font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
    status_lbl = ctk.CTkLabel(top, text="", text_color="gray60",
                              font=ctk.CTkFont(size=12))
    status_lbl.pack(side="right")
    _state["status_lbl"] = status_lbl

    ctk.CTkLabel(
        win,
        text=f"Источник: github.com/{GITHUB_REPO} ({GITHUB_BRANCH})",
        text_color="gray50",
        font=ctk.CTkFont(size=11),
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
        api["toast"]("URL сохранён", "success")
        reload_catalog()

    ctk.CTkButton(cfg_row, text="Сохранить", width=110,
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
                 placeholder_text="🔍 Поиск...").pack(fill="x")

    # Теги
    tag_row = ctk.CTkFrame(win, fg_color="transparent")
    tag_row.pack(fill="x", padx=16, pady=(4, 4))

    #
