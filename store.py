# -*- coding: utf-8 -*-
"""
Магазин плагинов Deskify (v2.0).

Возможности:
  - загрузка каталога из raw-URL GitHub с фолбэком в кэш;
  - установка / обновление / удаление плагинов из каталога;
  - вкладка «Локальные» — все .py в data/plugins/, включая поставленные вручную;
  - удаление любого локального плагина с бэкапом в .trash/;
  - индикатор загрузки на кнопке;
  - проверка обновлений по номеру версии (1.0 == 1.0.0);
  - поиск по каталогу и по локальным;
  - фильтр по тегам;
  - открытие папки плагинов в проводнике.

sha256 НЕ проверяется.

Каталог по умолчанию: https://github.com/deskify/Plugins
"""

import os
import json
import shutil
import threading
import urllib.request
import urllib.error
import ssl
import webbrowser
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

TRASH_DIRNAME = ".trash"


_state = {
    "api": None,
    "app": None,
    "catalog": [],
    "window": None,
    "list_frame": None,
    "status_lbl": None,
    "search_var": None,
    "active_tag": None,
    "active_tab": "catalog",     # catalog | installed | local
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


def _trash_dir(api):
    d = os.path.join(api["PathHelper"].plugins_dir(), TRASH_DIRNAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


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


def _is_file_installed(plugin, installed_set):
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
        url, headers={"User-Agent": "Deskify-Plugin-Store/2.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _humanize_network_error(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 404:
            return "Файл не найден (404). Проверьте URL и ветку."
        if e.code == 403:
            return "Доступ запрещён (403). Репозиторий приватный?"
        return f"HTTP {e.code}: {e.reason}"
    if isinstance(e, urllib.error.URLError):
        return "Нет соединения. Проверьте интернет."
    if isinstance(e, ssl.SSLError):
        return "Ошибка SSL. Проверьте системное время."
    if "timed out" in str(e).lower():
        return "Превышено время ожидания."
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
#  Установка / удаление
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
        return False, "По ссылке пришёл HTML, а не Python"

    # Делаем бэкап старой версии, если файл уже был
    if os.path.exists(target):
        _backup_file(api, filename)

    try:
        with open(target, "wb") as f:
            f.write(raw)
    except Exception as e:
        return False, f"Не удалось записать файл: {e}"

    pid = plugin.get("id") or filename
    _state["installed_versions"][pid] = plugin.get("version", "")
    _save_installed_versions(api)

    name = plugin.get("name", plugin.get("id", filename))
    return True, f"Плагин «{name}» установлен. Перезагрузите плагины."


def _uninstall_plugin(api, plugin):
    """Удаляет по данным каталога (с поиском правильного файла)."""
    installed = _installed_plugin_files(api)
    candidates = []

    name_in_catalog = plugin.get("file") or ""
    if name_in_catalog:
        candidates.append(os.path.basename(name_in_catalog))
    pid = plugin.get("id") or ""
    if pid:
        candidates.append(f"{pid}.py")
        candidates.append(f"plugin_{pid}.py")

    target_name = None
    for c in candidates:
        if c in installed:
            target_name = c
            break

    if not target_name:
        return False, "Файл плагина не найден в data/plugins/"

    return _delete_local_plugin(api, target_name)


def _delete_local_plugin(api, filename):
    """Удаляет локальный плагин, копируя его в .trash/ с временной меткой."""
    pd = api["PathHelper"].plugins_dir()
    target = os.path.join(pd, filename)
    if not os.path.exists(target):
        return False, "Файл не найден"

    # Копия в корзину
    trash = _trash_dir(api)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    trash_name = f"{filename}.{ts}"
    trash_path = os.path.join(trash, trash_name)

    try:
        shutil.copy2(target, trash_path)
    except Exception:
        # если не удалось скопировать — не рискуем удалять
        return False, "Не удалось сделать бэкап в .trash/"

    try:
        os.remove(target)
    except Exception as e:
        return False, f"Не удалось удалить: {e}"

    # удаляем версию из учёта
    for pid in list(_state["installed_versions"].keys()):
        if pid in (filename, filename[:-3] if filename.endswith(".py") else filename):
            _state["installed_versions"].pop(pid, None)
    _save_installed_versions(api)

    return True, f"«{filename}» удалён (бэкап: .trash/{trash_name})"


def _backup_file(api, filename):
    """Бэкап файла в .trash/ (используется при перезаписи)."""
    pd = api["PathHelper"].plugins_dir()
    src = os.path.join(pd, filename)
    if not os.path.exists(src):
        return None
    trash = _trash_dir(api)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(trash, f"{filename}.{ts}")
    try:
        shutil.copy2(src, dst)
        return dst
    except Exception:
        return None


# ============================================================
#  Список локальных плагинов
# ============================================================

def _collect_local_plugins(api):
    """Возвращает список словарей о каждом .py в data/plugins/."""
    pd = api["PathHelper"].plugins_dir()
    catalog_by_file = {}
    for p in _state["catalog"]:
        if not isinstance(p, dict):
            continue
        f = _plugin_filename(p)
        catalog_by_file[f] = p
        # и альтернативные варианты имени
        pid = p.get("id") or ""
        if pid:
            catalog_by_file.setdefault(f"{pid}.py", p)
            catalog_by_file.setdefault(f"plugin_{pid}.py", p)

    items = []
    try:
        names = sorted(os.listdir(pd))
    except Exception:
        names = []

    for name in names:
        if not name.endswith(".py"):
            continue
        path = os.path.join(pd, name)
        if not os.path.isfile(path):
            continue

        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
            mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            size = 0
            mtime_str = "?"

        # Ищем запись в каталоге
        plugin = catalog_by_file.get(name)
        # пробуем по нормализованному имени
        if plugin is None:
            stem = name[:-3]  # без .py
            if stem.startswith("plugin_"):
                stem2 = stem[len("plugin_"):]
                plugin = catalog_by_file.get(f"{stem2}.py")

        # читаем первую строку docstring/имя
        title = plugin.get("name") if plugin else None
        icon = plugin.get("icon", "🧩") if plugin else "🧩"

        if not title:
            # попытка вытащить имя из кода (простая эвристика)
            title = _guess_name_from_code(path) or name[:-3]

        items.append({
            "filename": name,
            "path": path,
            "title": title,
            "icon": icon,
            "size": size,
            "mtime": mtime_str,
            "in_catalog": plugin is not None,
            "catalog_plugin": plugin,
        })

    return items


def _guess_name_from_code(path):
    """Пытается вытащить человекочитаемое имя плагина из его .py."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(2000)
    except Exception:
        return None

    # ищем первую строку docstring
    m = None
    import re as _re
    m = _re.search(r'"""(.+?)\n', head, _re.DOTALL)
    if m:
        line = m.group(1).strip().splitlines()[0].strip()
        # убираем хвосты типа «— плагин для Deskify»
        line = line.replace("— плагин для Deskify", "").strip()
        if line and len(line) < 80:
            return line

    return None


# ============================================================
#  Открытие папки / репозитория
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


# ============================================================
#  Отрисовка — вкладка «Каталог»
# ============================================================

def _render_catalog_tab(api, frame):
    ctk = api["ctk"]

    for w in list(frame.winfo_children()):
        w.destroy()

    _heal_installed_versions(api, _state["catalog"])

    search_var = _state.get("search_var")
    query = search_var.get().strip().lower() if search_var else ""
    active_tag = _state.get("active_tag")
    installed = _installed_plugin_files(api)
    installed_versions = _state["installed_versions"]
    plugins = _state["catalog"]

    if not plugins:
        ctk.CTkLabel(frame,
                     text="Каталог пуст или не загрузился.",
                     text_color="gray60").pack(pady=40)
        return

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

        is_installed = _is_file_installed(plugin, installed)
        installed_v = installed_versions.get(pid, "")
        update_available = (
            is_installed and installed_v and _is_newer(installed_v, ver)
            and not _versions_equal(installed_v, ver)
        )

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
            title_line += f"  ⬆ (установлен v{installed_v})"

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
            meta_bits.append("🌐 requests")
        if meta_bits:
            ctk.CTkLabel(info, text="   ".join(meta_bits),
                         text_color="gray50", font=ctk.CTkFont(size=11),
                         anchor="w").pack(anchor="w", pady=(2, 0))

        btn_box = ctk.CTkFrame(card, fg_color="transparent")
        btn_box.pack(side="right", padx=10, pady=8)

        if is_installed and not update_available:
            ctk.CTkLabel(btn_box, text="✓ установлен",
                         text_color="#2ecc71",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(0, 4))

            def mk_del(p=plugin):
                def do():
                    if not api["messagebox"].askyesno(
                            "Удаление", f"Удалить плагин «{p.get('name')}»?"):
                        return
                    ok, msg = _uninstall_plugin(api, p)
                    api["toast"](msg, "success" if ok else "error")
                    _refresh_all(api)
                return do

            ctk.CTkButton(btn_box, text="Удалить", width=100,
                          fg_color="#c0392b", hover_color="#e74c3c",
                          command=mk_del()).pack(pady=2)
        else:
            btn_label = "⬆ Обновить" if update_available else "⬇ Установить"
            btn_color = "#e67e22" if update_available else "#1f538d"

            def mk_install(p=plugin, box=btn_box):
                def do():
                    for w in list(box.winfo_children()):
                        try:
                            w.configure(state="disabled")
                        except Exception:
                            pass
                    holder = api["ctk"].CTkButton(
                        box, text="⏳ Загрузка...", width=120,
                        fg_color="gray40", state="disabled",
                    )
                    holder.pack(pady=2)

                    def worker():
                        ok, msg = _install_plugin(api, p)
                        api["app"].after(
                            0,
                            lambda: _on_install_done(api, frame, p, ok, msg, box, holder),
                        )
                    threading.Thread(target=worker, daemon=True).start()
                return do

            ctk.CTkButton(btn_box, text=btn_label, width=120,
                          fg_color=btn_color,
                          command=mk_install()).pack(pady=2)

    if visible == 0:
        ctk.CTkLabel(frame, text="Ничего не найдено",
                     text_color="gray60").pack(pady=30)


def _on_install_done(api, frame, plugin, ok, msg, box, holder):
    try:
        holder.destroy()
    except Exception:
        pass
    if ok:
        api["toast"](msg, "success")
        api["notify"]("Магазин плагинов", msg)
    else:
        api["toast"](msg, "error")
        api["messagebox"].showerror("Ошибка установки", msg)
    _refresh_all(api)


# ============================================================
#  Отрисовка — вкладка «Установленные»
# ============================================================

def _render_installed_tab(api, frame):
    ctk = api["ctk"]

    for w in list(frame.winfo_children()):
        w.destroy()

    installed = _installed_plugin_files(api)
    if not installed:
        ctk.CTkLabel(frame,
                     text="Установленных плагинов нет.",
                     text_color="gray60").pack(pady=40)
        return

    # Сопоставляем файлы с записями из каталога
    by_file = {}
    for p in _state["catalog"]:
        if not isinstance(p, dict):
            continue
        by_file[_plugin_filename(p)] = p
        pid = p.get("id") or ""
        if pid:
            by_file.setdefault(f"{pid}.py", p)
            by_file.setdefault(f"plugin_{pid}.py", p)

    visible = 0
    for filename in sorted(installed):
        plugin = by_file.get(filename)
        card = ctk.CTkFrame(frame, corner_radius=8)
        card.pack(fill="x", padx=4, pady=5)

        icon = plugin.get("icon", "🧩") if plugin else "🧩"
        name = plugin.get("name") if plugin else filename
        ver = plugin.get("version", "") if plugin else ""

        ctk.CTkLabel(card, text=icon,
                     font=ctk.CTkFont(size=26)).pack(side="left",
                                                     padx=(12, 6), pady=10)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=4, pady=8)

        title_line = str(name)
        if ver:
            title_line += f"  v{ver}"
        ctk.CTkLabel(info, text=title_line,
                     font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(anchor="w")
        ctk.CTkLabel(info, text=f"Файл: {filename}",
                     text_color="gray60",
                     font=ctk.CTkFont(size=11),
                     anchor="w").pack(anchor="w")

        btn_box = ctk.CTkFrame(card, fg_color="transparent")
        btn_box.pack(side="right", padx=10, pady=8)

        def mk_del(fn=filename):
            def do():
                if not api["messagebox"].askyesno(
                        "Удаление", f"Удалить «{fn}»?"):
                    return
                ok, msg = _delete_local_plugin(api, fn)
                api["toast"](msg, "success" if ok else "error")
                _refresh_all(api)
            return do

        ctk.CTkButton(btn_box, text="✕ Удалить", width=110,
                      fg_color="#c0392b", hover_color="#e74c3c",
                      command=mk_del()).pack(pady=2)

        visible += 1

    if visible == 0:
        ctk.CTkLabel(frame, text="Пусто", text_color="gray60").pack(pady=30)


# ============================================================
#  Отрисовка — вкладка «Локальные»
# ============================================================

def _render_local_tab(api, frame):
    ctk = api["ctk"]

    for w in list(frame.winfo_children()):
        w.destroy()

    items = _collect_local_plugins(api)

    search_var = _state.get("search_var")
    query = search_var.get().strip().lower() if search_var else ""
    if query:
        items = [it for it in items if query in it["title"].lower()
                 or query in it["filename"].lower()]

    if not items:
        ctk.CTkLabel(frame,
                     text="В data/plugins/ нет .py файлов"
                          + ("" if not query else " по запросу"),
                     text_color="gray60").pack(pady=40)
        return

    # Заголовок со счётчиком
    head = ctk.CTkFrame(frame, fg_color="transparent")
    head.pack(fill="x", padx=4, pady=(4, 2))
    ctk.CTkLabel(head, text=f"Найдено: {len(items)}",
                 text_color="gray60",
                 font=ctk.CTkFont(size=11)).pack(side="left")

    for it in items:
        card = ctk.CTkFrame(frame, corner_radius=8)
        card.pack(fill="x", padx=4, pady=5)

        ctk.CTkLabel(card, text=it["icon"],
                     font=ctk.CTkFont(size=26)).pack(side="left",
                                                     padx=(12, 6), pady=10)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=4, pady=8)

        title = it["title"]
        if it["in_catalog"]:
            marker = "✓ в каталоге"
            marker_color = "#2ecc71"
        else:
            marker = "⚠ нет в каталоге"
            marker_color = "#f1c40f"

        title_row = ctk.CTkFrame(info, fg_color="transparent")
        title_row.pack(fill="x", anchor="w")
        ctk.CTkLabel(title_row, text=title,
                     font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(side="left")
        ctk.CTkLabel(title_row, text="  " + marker,
                     text_color=marker_color,
                     font=ctk.CTkFont(size=11)).pack(side="left")

        size_kb = max(1, it["size"] // 1024)
        ctk.CTkLabel(info,
                     text=f"{it['filename']}  •  {size_kb} КБ  •  {it['mtime']}",
                     text_color="gray60",
                     font=ctk.CTkFont(size=11),
                     anchor="w").pack(anchor="w")

        btn_box = ctk.CTkFrame(card, fg_color="transparent")
        btn_box.pack(side="right", padx=10, pady=8)

        def mk_open_path(p=it["path"]):
            def do():
                try:
                    if api["sys"].platform.startswith("win"):
                        api["os"].startfile(os.path.dirname(p))
                    elif api["sys"].platform == "darwin":
                        api["subprocess"].Popen(["open", os.path.dirname(p)])
                    else:
                        api["subprocess"].Popen(["xdg-open", os.path.dirname(p)])
                except Exception:
                    pass
            return do

        def mk_del(fn=it["filename"]):
            def do():
                if not api["messagebox"].askyesno(
                        "Удаление",
                        f"Удалить «{fn}»?\n\nБэкап будет сохранён в .trash/"):
                    return
                ok, msg = _delete_local_plugin(api, fn)
                api["toast"](msg, "success" if ok else "error")
                _refresh_all(api)
            return do

        ctk.CTkButton(btn_box, text="📂 Папка", width=90, fg_color="gray30",
                      command=mk_open_path()).pack(side="left", padx=2)
        ctk.CTkButton(btn_box, text="✕ Удалить", width=110,
                      fg_color="#c0392b", hover_color="#e74c3c",
                      command=mk_del()).pack(side="left", padx=2)


# ============================================================
#  Перерисовка
# ============================================================

def _refresh_all(api):
    list_frame = _state.get("list_frame")
    if list_frame is None:
        return
    try:
        if not list_frame.winfo_exists():
            return
    except Exception:
        return

    # Полная очистка
    for w in list(list_frame.winfo_children()):
        w.destroy()

    tab = _state.get("active_tab", "catalog")
    if tab == "catalog":
        _render_catalog_tab(api, list_frame)
    elif tab == "installed":
        _render_installed_tab(api, list_frame)
    elif tab == "local":
        _render_local_tab(api, list_frame)


# ============================================================
#  Теги (только для вкладки «Каталог»)
# ============================================================

def _collect_tags(plugins):
    tags = set()
    for p in plugins:
        if isinstance(p, dict):
            for t in (p.get("tags") or []):
                if t:
                    tags.add(str(t))
    return sorted(tags)


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
        _refresh_all(api)

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
#  Поднятие окна
# ============================================================

def _raise_window(win, parent=None):
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


def _close_window(win):
    try:
        win.destroy()
    except Exception:
        pass
    _state["window"] = None


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
        api["notify"]("Магазин плагинов", "Плагины перезагружены.")
    except Exception as e:
        api["messagebox"].showerror("Ошибка", f"Не удалось перезагрузить: {e}")


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
    win.geometry("920x700")
    win.minsize(760, 560)
    _state["window"] = win

    _raise_window(win, app)

    # ---------- Заголовок ----------
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

    # ---------- Вкладки ----------
    tabs_row = ctk.CTkFrame(win, fg_color="transparent")
    tabs_row.pack(fill="x", padx=16, pady=(6, 4))

    tab_buttons = {}

    def style_tabs(active):
        for k, b in tab_buttons.items():
            try:
                if k == active:
                    b.configure(fg_color="#1f538d", text_color="white")
                else:
                    b.configure(fg_color="transparent",
                                text_color=("gray10", "gray90"))
            except Exception:
                pass

    # ---------- Строка каталога (URL + кнопки) ----------
    cfg_row = ctk.CTkFrame(win, fg_color="transparent")
    cfg_row.pack(fill="x", padx=16, pady=(4, 4))

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

    # ---------- Поиск ----------
    search_row = ctk.CTkFrame(win, fg_color="transparent")
    search_row.pack(fill="x", padx=16, pady=(0, 4))
    search_var = ctk.StringVar()
    _state["search_var"] = search_var
    ctk.CTkEntry(search_row, textvariable=search_var,
                 placeholder_text="🔍 Поиск...").pack(fill="x")

    # ---------- Теги ----------
    tag_row = ctk.CTkFrame(win, fg_color="transparent")
    tag_row.pack(fill="x", padx=16, pady=(4, 4))

    # ---------- Кнопки действий ----------
    btn_row = ctk.CTkFrame(win, fg_color="transparent")
    btn_row.pack(fill="x", padx=16, pady=(2, 6))

    refresh_btn = ctk.CTkButton(btn_row, text="🔄 Обновить каталог", width=170)
    refresh_btn.pack(side="left", padx=(0, 6))
    ctk.CTkButton(btn_row, text="♻ Перезагрузить плагины", width=190,
                  fg_color="gray30",
                  command=lambda: _reload_plugins(api)).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="📁 Папка плагинов", width=150, fg_color="gray30",
                  command=lambda: _open_plugins_folder(api)).pack(side="left", padx=3)
    ctk.CTkButton(btn_row, text="🔗 Репозиторий", width=130, fg_color="gray30",
                  command=lambda: _open_repo(api)).pack(side="left", padx=3)

    # ---------- Список ----------
    list_frame = ctk.CTkScrollableFrame(win, fg_color=("gray90", "gray15"))
    list_frame.pack(fill="both", expand=True, padx=16, pady=(4, 12))
    _state["list_frame"] = list_frame

    # ---------- Переключение вкладок ----------
    def select_tab(key):
        _state["active_tab"] = key
        style_tabs(key)

        # Теги видны только на каталоге
        if key == "catalog":
            tag_row.pack(fill="x", padx=16, pady=(4, 4),
                         before=btn_row)
        else:
            tag_row.pack_forget()

        _refresh_all(api)

    for key, label in [("catalog", "🌐 Каталог"),
                       ("installed", "⭐ Установленные"),
                       ("local", "📁 Локальные")]:
        b = ctk.CTkButton(tabs_row, text=label, width=170, height=32,
                          fg_color="transparent",
                          command=lambda k=key: select_tab(k))
        b.pack(side="left", padx=2)
        tab_buttons[key] = b

    # ---------- Загрузка каталога ----------
    def reload_catalog():
        status_lbl.configure(text="Загрузка каталога...", text_color="gray60")
        refresh_btn.configure(state="disabled")
        _state["installed_versions"] = _load_installed_versions(api)

        def worker():
            plugins, source, err = _load_catalog(api)
            _state["catalog"] = plugins or []

            def apply():
                _render_tag_filter(api, tag_row, plugins, list_frame)
                _refresh_all(api)
                refresh_btn.configure(state="normal")
                if source == "online":
                    status_lbl.configure(text="✓ Каталог обновлён",
                                          text_color="#2ecc71")
                elif source == "cache":
                    status_lbl.configure(text="⚠ Нет сети — кэш",
                                          text_color="#e67e22")
                elif source == "error":
                    status_lbl.configure(text="✖ Ошибка загрузки",
                                          text_color="#e74c3c")
                    if err:
                        api["toast"](err, "error")

            app.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    refresh_btn.configure(command=reload_catalog)

    def on_search_change(*_):
        _refresh_all(api)

    search_var.trace_add("write", on_search_change)

    # Первый запуск
    reload_catalog()
    win.protocol("WM_DELETE_WINDOW", lambda: _close_window(win))


# ============================================================
#  Точка входа
# ============================================================

def register(api):
    _state["api"] = api
    _state["app"] = api["app"]

    api["add_plugin_button"](
        "🛒 Магазин плагинов",
        lambda: _open_store_window(api),
    )
