# -*- coding: utf-8 -*-
"""
Плагин «Блокнот на максималках» для Deskify.

Многофайловый текстовый редактор с форматированием:
жирный/курсив/подчёркнутый/зачёркнутый, размер и семейство шрифта,
цвет текста и фона, выравнивание, заголовки, списки,
поиск-замена, статистика, автосохранение,
экспорт в .txt/.md/.html/.rtf, импорт из .txt/.md.

Зависимостей кроме tkinter нет.
"""

import os
import re
import json
from datetime import datetime


# ============================================================
#  Опции
# ============================================================

FONT_FAMILIES = ["Consolas", "Segoe UI", "Arial", "Times New Roman",
                 "Courier New", "Calibri", "Verdana", "Georgia"]

FONT_SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48]

TEXT_COLORS = {
    "Чёрный": "#000000",
    "Серый": "#808080",
    "Красный": "#c0392b",
    "Оранжевый": "#e67e22",
    "Зелёный": "#27ae60",
    "Синий": "#2980b9",
    "Фиолетовый": "#8e44ad",
}

HIGHLIGHT_COLORS = {
    "Нет": "",
    "Жёлтый": "#fef08a",
    "Зелёный": "#bbf7d0",
    "Голубой": "#bfdbfe",
    "Розовый": "#fbcfe8",
    "Оранжевый": "#fed7aa",
}

AUTOSAVE_INTERVAL_MS = 5000


_state = {
    "api": None,
    "app": None,
    "window": None,
    "docs": [],           # список dict: {path, title, dirty, undo, redo}
    "active_doc": 0,
    "ui": {},
    "autosave_job": None,
    "search_win": None,
}


# ============================================================
#  Документ
# ============================================================

def _new_doc(title="Без имени", path=None, content=""):
    return {
        "path": path,
        "title": title,
        "dirty": False,
        "content": content,
        "undo_stack": [],
        "redo_stack": [],
    }


def _current_doc():
    if not _state["docs"]:
        return None
    idx = _state["active_doc"]
    if 0 <= idx < len(_state["docs"]):
        return _state["docs"][idx]
    return None


# ============================================================
#  Undo/redo
# ============================================================

def _snapshot(api):
    """Снимает снимок текста для undo."""
    doc = _current_doc()
    if doc is None:
        return
    textbox = _state["ui"].get("text")
    if textbox is None:
        return
    try:
        current = textbox.get("1.0", "end-1c")
    except Exception:
        return
    if not doc["undo_stack"] or doc["undo_stack"][-1] != current:
        doc["undo_stack"].append(current)
        if len(doc["undo_stack"]) > 100:
            doc["undo_stack"].pop(0)
        doc["redo_stack"].clear()


def _undo(api):
    doc = _current_doc()
    if doc is None or not doc["undo_stack"]:
        api["toast"]("Нечего отменять", "info")
        return
    textbox = _state["ui"].get("text")
    try:
        current = textbox.get("1.0", "end-1c")
        doc["redo_stack"].append(current)
        prev = doc["undo_stack"].pop()
        textbox.delete("1.0", "end")
        textbox.insert("1.0", prev)
        doc["dirty"] = True
        _update_title(api)
    except Exception:
        pass


def _redo(api):
    doc = _current_doc()
    if doc is None or not doc["redo_stack"]:
        api["toast"]("Нечего повторять", "info")
        return
    textbox = _state["ui"].get("text")
    try:
        current = textbox.get("1.0", "end-1c")
        doc["undo_stack"].append(current)
        nxt = doc["redo_stack"].pop()
        textbox.delete("1.0", "end")
        textbox.insert("1.0", nxt)
        doc["dirty"] = True
        _update_title(api)
    except Exception:
        pass


# ============================================================
#  Форматирование
# ============================================================

def _apply_tag(api, tag, value):
    textbox = _state["ui"].get("text")
    raw = _get_raw_textbox(api)
    if textbox is None:
        return
    try:
        sel_start, sel_end = raw.tag_ranges("sel")[:2]
    except (ValueError, IndexError):
        api["toast"]("Сначала выделите текст", "info")
        return

    # toggle
    has_any = bool(raw.tag_nextrange(tag, sel_start, sel_end))
    if has_any:
        raw.tag_remove(tag, sel_start, sel_end)
    else:
        if value is not None:
            raw.tag_configure(tag, **value)
        raw.tag_add(tag, sel_start, sel_end)
    _snapshot(api)
    _current_doc()["dirty"] = True
    _update_title(api)


def _apply_align(api, justify):
    textbox = _state["ui"].get("text")
    raw = _get_raw_textbox(api)
    if textbox is None:
        return
    try:
        sel_start, sel_end = raw.tag_ranges("sel")[:2]
    except (ValueError, IndexError):
        # если нет выделения — применяем к текущей строке
        sel_start = raw.index("insert linestart")
        sel_end = raw.index("insert lineend")

    # снимаем все выравнивания в диапазоне
    for t in ("align_left", "align_center", "align_right", "align_justify"):
        raw.tag_remove(t, sel_start, sel_end)

    tag = f"align_{justify}"
    raw.tag_configure(tag, justify=justify)
    # выравнивание должно действовать на всю строку — расширяем до начала строки
    raw.tag_add(tag, f"{sel_start} linestart", f"{sel_end} lineend")
    _snapshot(api)
    _current_doc()["dirty"] = True
    _update_title(api)


def _apply_heading(api, level):
    """level: 0=обычный, 1/2/3 — заголовки."""
    raw = _get_raw_textbox(api)
    try:
        sel_start, sel_end = raw.tag_ranges("sel")[:2]
    except (ValueError, IndexError):
        sel_start = raw.index("insert linestart")
        sel_end = raw.index("insert lineend")

    for t in ("h1", "h2", "h3"):
        raw.tag_remove(t, sel_start, sel_end)
    if level == 0:
        return
    tag = f"h{level}"
    sizes = {1: 24, 2: 18, 3: 15}
    raw.tag_configure(tag, font=(_state["ui"].get("font_family") or "Segoe UI",
                                  sizes[level], "bold"))
    raw.tag_add(tag, f"{sel_start} linestart", f"{sel_end} lineend")
    _snapshot(api)
    _current_doc()["dirty"] = True
    _update_title(api)


def _insert_list(api, kind):
    """kind: 'bullet' | 'number'."""
    textbox = _state["ui"].get("text")
    if textbox is None:
        return
    try:
        start = textbox.index("sel.first linestart")
        end = textbox.index("sel.last lineend")
    except Exception:
        start = textbox.index("insert linestart")
        end = textbox.index("insert lineend")

    text = textbox.get(start, end)
    lines = text.split("\n")
    out = []
    for i, line in enumerate(lines):
        clean = re.sub(r"^(\s*)([-*•]|\d+\.)\s+", r"\1", line)
        prefix = "• " if kind == "bullet" else f"{i + 1}. "
        out.append(prefix + clean)
    new_text = "\n".join(out)

    textbox.delete(start, end)
    textbox.insert(start, new_text)
    _snapshot(api)
    _current_doc()["dirty"] = True


def _get_raw_textbox(api):
    """Возвращает tkinter.Text внутри CTkTextbox."""
    tb = _state["ui"].get("text")
    if tb is None:
        return None
    return getattr(tb, "_textbox", tb)


# ============================================================
#  Статистика
# ============================================================

def _update_stats(api):
    lbl = _state["ui"].get("stats")
    textbox = _state["ui"].get("text")
    if lbl is None or textbox is None:
        return
    try:
        if not lbl.winfo_exists() or not textbox.winfo_exists():
            return
        text = textbox.get("1.0", "end-1c")
    except Exception:
        return
    chars = len(text)
    words = len(re.findall(r"\S+", text))
    lines = text.count("\n") + (1 if text else 0)
    paragraphs = len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
    try:
        lbl.configure(text=f"Слов: {words}  •  Символов: {chars}  •  Строк: {lines}  •  Абзацев: {paragraphs}")
    except Exception:
        pass


# ============================================================
#  Файловые операции
# ============================================================

def _update_title(api):
    win = _state.get("window")
    if win is None:
        return
    doc = _current_doc()
    if doc is None:
        return
    name = doc["title"]
    mark = "•" if doc["dirty"] else ""
    try:
        win.title(f"📄 Word-редактор — {mark}{name}")
    except Exception:
        pass


def _set_content(api, content):
    textbox = _state["ui"].get("text")
    if textbox is None:
        return
    textbox.delete("1.0", "end")
    textbox.insert("1.0", content)


def _get_content(api):
    textbox = _state["ui"].get("text")
    if textbox is None:
        return ""
    return textbox.get("1.0", "end-1c")


def _new_file(api):
    doc = _new_doc()
    _state["docs"].append(doc)
    _state["active_doc"] = len(_state["docs"]) - 1
    _refresh_tabs(api)
    _load_active(api)
    _update_title(api)


def _open_file(api):
    paths = api["filedialog"].askopenfilenames(
        title="Открыть файлы",
        filetypes=[
            ("Текст и Markdown", "*.txt *.md *.markdown"),
            ("HTML", "*.html *.htm"),
            ("Все файлы", "*.*"),
        ],
    )
    if not paths:
        return
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            try:
                with open(path, "r", encoding="cp1251") as f:
                    content = f.read()
            except Exception as e:
                api["messagebox"].showerror("Ошибка", f"Не удалось открыть:\n{path}\n{e}")
                continue
        except Exception as e:
            api["messagebox"].showerror("Ошибка", f"Не удалось открыть:\n{path}\n{e}")
            continue

        # если это HTML — срежем теги в простейшем виде
        if path.lower().endswith((".html", ".htm")):
            content = _html_to_text(content)

        doc = _new_doc(title=os.path.basename(path), path=path, content=content)
        _state["docs"].append(doc)
        _state["active_doc"] = len(_state["docs"]) - 1
    _refresh_tabs(api)
    _load_active(api)
    _update_title(api)
    api["toast"](f"Открыто: {len(paths)}", "success")


def _save_file(api, save_as=False):
    doc = _current_doc()
    if doc is None:
        return
    path = doc["path"]
    if save_as or not path:
        path = api["filedialog"].asksaveasfilename(
            title="Сохранить как",
            defaultextension=".txt",
            filetypes=[("Текст", "*.txt"), ("Markdown", "*.md"), ("Все файлы", "*.*")],
            initialfile=doc["title"] if doc["title"] != "Без имени" else "document.txt",
        )
        if not path:
            return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_get_content(api))
        doc["path"] = path
        doc["title"] = os.path.basename(path)
        doc["dirty"] = False
        _refresh_tabs(api)
        _update_title(api)
        api["toast"]("Сохранено", "success")
    except Exception as e:
        api["messagebox"].showerror("Ошибка сохранения", str(e))


def _export(api, fmt):
    doc = _current_doc()
    if doc is None:
        return
    content = _get_content(api)
    ext_map = {"txt": ".txt", "md": ".md", "html": ".html", "rtf": ".rtf"}
    default_name = os.path.splitext(doc["title"])[0] + ext_map[fmt]
    path = api["filedialog"].asksaveasfilename(
        title=f"Экспорт в {fmt.upper()}",
        defaultextension=ext_map[fmt],
        filetypes=[(fmt.upper(), f"*{ext_map[fmt]}"), ("Все файлы", "*.*")],
        initialfile=default_name,
    )
    if not path:
        return
    try:
        if fmt == "txt":
            data = content
        elif fmt == "md":
            data = content
        elif fmt == "html":
            data = _text_to_html(content)
        elif fmt == "rtf":
            data = _text_to_rtf(content)
        else:
            data = content
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        api["toast"](f"Экспортировано в {fmt.upper()}", "success")
    except Exception as e:
        api["messagebox"].showerror("Ошибка экспорта", str(e))


def _html_to_text(html):
    """Грубое превращение HTML в текст (без BeautifulSoup)."""
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</p\s*>", "\n\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    html = (html.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"'))
    return html.strip()


def _text_to_html(text):
    """Оборачивает текст в HTML. Простейшая разметка: абзацы по пустым строкам."""
    paragraphs = re.split(r"\n\s*\n", text)
    body = "\n".join(
        f"<p>{_escape_html(p).replace(chr(10), '<br>')}</p>" for p in paragraphs
    )
    return (
        "<!DOCTYPE html>\n"
        "<html lang='ru'><head><meta charset='utf-8'>"
        "<title>Документ</title>"
        "<style>body{font-family:Arial,sans-serif;font-size:14px;line-height:1.5;"
        "max-width:800px;margin:40px auto;padding:0 20px;}</style>"
        "</head><body>\n"
        f"{body}\n"
        "</body></html>"
    )


def _text_to_rtf(text):
    """Минимальный RTF: сохраним абзацы, кириллицу через \\uN."""
    def esc_rtf_char(ch):
        code = ord(ch)
        if ch == "\\":
            return "\\\\"
        if ch == "{":
            return "\\{"
        if ch == "}":
            return "\\}"
        if ch == "\n":
            return "\\par\n"
        if code < 128:
            return ch
        if code > 32767:
            code -= 65536
        return f"\\u{code}?"

    header = r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}\fs24 "
    return header + "".join(esc_rtf_char(c) for c in text) + "}"


def _escape_html(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ============================================================
#  Поиск и замена
# ============================================================

def _open_search_window(api):
    if _state.get("search_win") is not None:
        try:
            if _state["search_win"].winfo_exists():
                _state["search_win"].deiconify()
                _state["search_win"].lift()
                return
        except Exception:
            pass

    ctk = api["ctk"]
    win = ctk.CTkToplevel(_state["window"] or api["app"])
    win.title("Поиск и замена")
    win.geometry("420x260")
    _state["search_win"] = win

    find_var = ctk.StringVar()
    repl_var = ctk.StringVar()
    case_var = ctk.BooleanVar(value=False)

    ctk.CTkLabel(win, text="Найти:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=14, pady=(14, 2))
    ctk.CTkEntry(win, textvariable=find_var).pack(fill="x", padx=14)

    ctk.CTkLabel(win, text="Заменить на:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=14, pady=(8, 2))
    ctk.CTkEntry(win, textvariable=repl_var).pack(fill="x", padx=14)

    ctk.CTkCheckBox(win, text="Учитывать регистр", variable=case_var).pack(anchor="w", padx=14, pady=8)

    raw = _get_raw_textbox(api)
    if raw is not None:
        raw.tag_configure("search_hit", background="#fde047")

    def clear_hits():
        if raw is None:
            return
        try:
            raw.tag_remove("search_hit", "1.0", "end")
        except Exception:
            pass

    def highlight_all():
        clear_hits()
        if raw is None:
            return 0
        needle = find_var.get()
        if not needle:
            return 0
        text_widget = _state["ui"].get("text")
        content = text_widget.get("1.0", "end-1c")
        flags = 0 if case_var.get() else re.IGNORECASE
        count = 0
        for m in re.finditer(re.escape(needle), content, flags):
            start_idx = f"1.0+{m.start()}c"
            end_idx = f"1.0+{m.end()}c"
            raw.tag_add("search_hit", start_idx, end_idx)
            count += 1
        return count

    def do_find():
        count = highlight_all()
        api["toast"](f"Найдено: {count}", "info" if count else "error")

    def do_replace():
        needle = find_var.get()
        repl = repl_var.get()
        if not needle:
            return
        textbox = _state["ui"].get("text")
        content = textbox.get("1.0", "end-1c")
        flags = 0 if case_var.get() else re.IGNORECASE
        new_content, n = re.subn(re.escape(needle), repl, content, flags=flags)
        if n == 0:
            api["toast"]("Ничего не найдено", "error")
            return
        _snapshot(api)
        textbox.delete("1.0", "end")
        textbox.insert("1.0", new_content)
        _current_doc()["dirty"] = True
        _update_title(api)
        api["toast"](f"Заменено: {n}", "success")
        clear_hits()
        highlight_all()

    btns = ctk.CTkFrame(win, fg_color="transparent")
    btns.pack(fill="x", padx=14, pady=(4, 14))
    ctk.CTkButton(btns, text="Найти", command=do_find).pack(side="left", padx=(0, 4))
    ctk.CTkButton(btns, text="Заменить все", command=do_replace).pack(side="left", padx=4)
    ctk.CTkButton(btns, text="Закрыть", fg_color="gray30",
                  command=lambda: (clear_hits(), win.destroy())).pack(side="right")

    def on_close():
        clear_hits()
        _state["search_win"] = None
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", on_close)


# ============================================================
#  Вкладки документов
# ============================================================

def _refresh_tabs(api):
    tab_frame = _state["ui"].get("tabs")
    if tab_frame is None:
        return
    for w in list(tab_frame.winfo_children()):
        w.destroy()

    ctk = api["ctk"]
    for i, doc in enumerate(_state["docs"]):
        is_active = (i == _state["active_doc"])
        name = doc["title"] + ("•" if doc["dirty"] else "")
        btn = ctk.CTkButton(
            tab_frame, text=name, height=28,
            fg_color="#1f538d" if is_active else "gray30",
            hover_color="#1c497d" if is_active else "gray40",
            command=lambda idx=i: _switch_doc(api, idx),
        )
        btn.pack(side="left", padx=(0, 2))

        # крестик закрыть
        if len(_state["docs"]) > 1:
            close_btn = ctk.CTkButton(
                tab_frame, text="✕", width=22, height=28,
                fg_color="transparent", hover_color="#c0392b",
                command=lambda idx=i: _close_doc(api, idx),
            )
            close_btn.pack(side="left", padx=(0, 6))


def _switch_doc(api, idx):
    # сохраним текущий текст в модель
    _sync_current(api)
    _state["active_doc"] = idx
    _load_active(api)
    _refresh_tabs(api)
    _update_title(api)


def _close_doc(api, idx):
    if len(_state["docs"]) <= 1:
        return
    doc = _state["docs"][idx]
    if doc["dirty"]:
        if not api["messagebox"].askyesno(
                "Закрыть документ",
                f"«{doc['title']}» не сохранён. Закрыть без сохранения?"):
            return
    _state["docs"].pop(idx)
    _state["active_doc"] = max(0, min(_state["active_doc"], len(_state["docs"]) - 1))
    _load_active(api)
    _refresh_tabs(api)
    _update_title(api)


def _sync_current(api):
    doc = _current_doc()
    if doc is None:
        return
    textbox = _state["ui"].get("text")
    if textbox is None:
        return
    try:
        doc["content"] = textbox.get("1.0", "end-1c")
    except Exception:
        pass


def _load_active(api):
    doc = _current_doc()
    if doc is None:
        return
    _set_content(api, doc.get("content", ""))
    _update_stats(api)


# ============================================================
#  Автосохранение
# ============================================================

def _start_autosave(api):
    if _state["autosave_job"] is not None:
        try:
            api["app"].after_cancel(_state["autosave_job"])
        except Exception:
            pass
    _state["autosave_job"] = api["app"].after(AUTOSAVE_INTERVAL_MS,
                                              lambda: _autosave_tick(api))


def _autosave_tick(api):
    _state["autosave_job"] = None
    _sync_current(api)
    _update_stats(api)

    # автосохранение только для документов, у которых уже есть путь
    for doc in _state["docs"]:
        if doc["dirty"] and doc["path"]:
            try:
                text = doc["content"]
                with open(doc["path"], "w", encoding="utf-8") as f:
                    f.write(text)
                doc["dirty"] = False
            except Exception:
                pass
    _refresh_tabs(api)

    if _state["window"] is not None:
        try:
            if _state["window"].winfo_exists():
                _start_autosave(api)
        except Exception:
            pass


# ============================================================
#  Главное окно
# ============================================================

def _open_word_window(api):
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
    tk = api["tk"]

    win = ctk.CTkToplevel(app)
    win.title("📄 Word-редактор")
    win.geometry("1020x720")
    win.minsize(820, 560)
    _state["window"] = win
    _state["ui"] = {}

    if not _state["docs"]:
        _state["docs"].append(_new_doc())
        _state["active_doc"] = 0

    # ---------- Меню-строка (действия) ----------
    menubar = ctk.CTkFrame(win, height=38)
    menubar.pack(fill="x", side="top")

    def menu_action(label, command):
        ctk.CTkButton(menubar, text=label, height=30, width=110,
                      fg_color="transparent", hover_color="gray30",
                      command=command).pack(side="left", padx=1, pady=3)

    menu_action("📄 Новый", lambda: _new_file(api))
    menu_action("📂 Открыть", lambda: _open_file(api))
    menu_action("💾 Сохранить", lambda: _save_file(api))
    menu_action("💾 Сохранить как", lambda: _save_file(api, save_as=True))

    # подменю экспорта
    exp_btn = ctk.CTkOptionMenu(
        menubar, values=["Экспорт TXT", "Экспорт MD", "Экспорт HTML", "Экспорт RTF"],
        width=140, command=lambda v: _export(api, v.split()[-1].lower()),
    )
    exp_btn.pack(side="left", padx=4, pady=3)

    ctk.CTkButton(menubar, text="🔍 Поиск", height=30, width=100,
                  fg_color="transparent", hover_color="gray30",
                  command=lambda: _open_search_window(api)).pack(side="right", padx=4, pady=3)

    # ---------- Вкладки ----------
    tabs_outer = ctk.CTkFrame(win, fg_color="transparent", height=34)
    tabs_outer.pack(fill="x", side="top")
    tabs_frame = ctk.CTkFrame(tabs_outer, fg_color="transparent")
    tabs_frame.pack(fill="x", padx=10, pady=4)
    _state["ui"]["tabs"] = tabs_frame

    ctk.CTkButton(tabs_outer, text="+", width=28, height=26,
                  fg_color="gray30",
                  command=lambda: _new_file(api)).pack(side="right", padx=8, pady=4)

    # ---------- Панель форматирования ----------
    toolbar = ctk.CTkFrame(win, height=44)
    toolbar.pack(fill="x", side="top", pady=(4, 0))

    # шрифт
    fam_var = ctk.StringVar(value="Segoe UI")
    ctk.CTkOptionMenu(toolbar, variable=fam_var, values=FONT_FAMILIES,
                      width=150, command=lambda v: _set_font_family(api, v)
                      ).pack(side="left", padx=(8, 4), pady=6)

    size_var = ctk.StringVar(value="14")
    ctk.CTkOptionMenu(toolbar, variable=size_var,
                      values=[str(s) for s in FONT_SIZES],
                      width=70, command=lambda v: _set_font_size(api, v)
                      ).pack(side="left", padx=4, pady=6)

    # жирный/курсив/подчёркнутый/зачёркнутый
    def mk_btn(text, tag, conf, width=34):
        ctk.CTkButton(toolbar, text=text, width=width, height=30,
                      fg_color="gray30",
                      command=lambda: _apply_tag(api, tag, conf)
                      ).pack(side="left", padx=2, pady=6)

    mk_btn("Ж", "bold", {"weight": "bold"})
    mk_btn("К", "italic", {"slant": "italic"})
    mk_btn("Ч", "underline", {"underline": True})
    mk_btn("З", "strike", {"overstrike": True})

    # цвет текста
    color_var = ctk.StringVar(value="Чёрный")
    ctk.CTkOptionMenu(toolbar, variable=color_var,
                      values=list(TEXT_COLORS.keys()),
                      width=110,
                      command=lambda v: _apply_tag(api, "color",
                                                    {"foreground": TEXT_COLORS[v]})
                      ).pack(side="left", padx=6, pady=6)

    # фон выделения
    hl_var = ctk.StringVar(value="Нет")
    def apply_hl(v):
        if not v or v == "Нет":
            _apply_tag(api, "highlight", None)
        else:
            _apply_tag(api, "highlight", {"background": HIGHLIGHT_COLORS[v]})
    ctk.CTkOptionMenu(toolbar, variable=hl_var,
                      values=list(HIGHLIGHT_COLORS.keys()),
                      width=120, command=apply_hl
                      ).pack(side="left", padx=6, pady=6)

    # выравнивание
    for symbol, j in (("⯇", "left"), ("≡", "center"), ("⯈", "right"), ("☰", "justify")):
        ctk.CTkButton(toolbar, text=symbol, width=32, height=30,
                      fg_color="gray30",
                      command=lambda jj=j: _apply_align(api, jj)
                      ).pack(side="left", padx=2, pady=6)

    # заголовки
    h_var = ctk.StringVar(value="Обычный")
    def apply_h(v):
        level = {"Обычный": 0, "Заголовок 1": 1, "Заголовок 2": 2, "Заголовок 3": 3}.get(v, 0)
        _apply_heading(api, level)
    ctk.CTkOptionMenu(toolbar, variable=h_var,
                      values=["Обычный", "Заголовок 1", "Заголовок 2", "Заголовок 3"],
                      width=140, command=apply_h
                      ).pack(side="left", padx=6, pady=6)

    # списки
    ctk.CTkButton(toolbar, text="• Список", width=90, height=30, fg_color="gray30",
                  command=lambda: _insert_list(api, "bullet")
                  ).pack(side="left", padx=2, pady=6)
    ctk.CTkButton(toolbar, text="1. Список", width=90, height=30, fg_color="gray30",
                  command=lambda: _insert_list(api, "number")
                  ).pack(side="left", padx=2, pady=6)

    ctk.CTkButton(toolbar, text="↶", width=32, height=30, fg_color="gray30",
                  command=lambda: _undo(api)).pack(side="right", padx=2, pady=6)
    ctk.CTkButton(toolbar, text="↷", width=32, height=30, fg_color="gray30",
                  command=lambda: _redo(api)).pack(side="right", padx=2, pady=6)

    # ---------- Текстовое поле ----------
    text_outer = ctk.CTkFrame(win, fg_color="transparent")
    text_outer.pack(fill="both", expand=True, padx=12, pady=8)

    textbox = ctk.CTkTextbox(text_outer, wrap="word",
                             font=ctk.CTkFont(size=14),
                             undo=False)
    textbox.pack(fill="both", expand=True)
    _state["ui"]["text"] = textbox
    _state["ui"]["font_family"] = "Segoe UI"

    raw = _get_raw_textbox(api)

    # Горячие клавиши
    def on_key(e=None):
        _update_stats(api)
        _current_doc()["dirty"] = True
        _update_title(api)
        _refresh_tabs(api)
        return None

    raw.bind("<KeyRelease>", on_key)

    def bind_format(seq, tag, conf):
        def handler(e):
            _apply_tag(api, tag, conf)
            return "break"
        raw.bind(seq, handler)

    bind_format("<Control-b>", "bold", {"weight": "bold"})
    bind_format("<Control-i>", "italic", {"slant": "italic"})
    bind_format("<Control-u>", "underline", {"underline": True})
    raw.bind("<Control-z>", lambda e: (_undo(api), "break")[1])
    raw.bind("<Control-y>", lambda e: (_redo(api), "break")[1])
    raw.bind("<Control-s>", lambda e: (_save_file(api), "break")[1])
    raw.bind("<Control-f>", lambda e: (_open_search_window(api), "break")[1])
    raw.bind("<Control-n>", lambda e: (_new_file(api), "break")[1])
    raw.bind("<Control-o>", lambda e: (_open_file(api), "break")[1])

    # Ctrl+колесо мыши — быстрое изменение размера
    def on_wheel(e):
        try:
            if e.delta > 0:
                _bump_font_size(api, 1)
            else:
                _bump_font_size(api, -1)
        except Exception:
            pass
        return "break"

    raw.bind("<Control-MouseWheel>", on_wheel)

    # ---------- Статистика ----------
    stats = ctk.CTkLabel(win, text="Слов: 0  •  Символов: 0  •  Строк: 0  •  Абзацев: 0",
                         font=ctk.CTkFont(size=11), text_color="gray60")
    stats.pack(fill="x", side="bottom", padx=16, pady=(0, 6))
    _state["ui"]["stats"] = stats

    # ---------- Загрузка ----------
    _refresh_tabs(api)
    _load_active(api)
    _update_title(api)
    _start_autosave(api)

    def on_close():
        _sync_current(api)
        dirty = [d for d in _state["docs"] if d["dirty"]]
        if dirty:
            if not api["messagebox"].askyesno(
                    "Закрыть редактор",
                    f"Есть несохранённые документы ({len(dirty)}). Закрыть без сохранения?"):
                return
        if _state["autosave_job"] is not None:
            try:
                api["app"].after_cancel(_state["autosave_job"])
            except Exception:
                pass
            _state["autosave_job"] = None
        _state["window"] = None
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", on_close)


# ============================================================
#  Шрифт: семейство и размер
# ============================================================

def _set_font_family(api, family):
    raw = _get_raw_textbox(api)
    if raw is None:
        return
    size = _state["ui"].get("font_size", 14)
    raw.configure(font=(family, size))
    _state["ui"]["font_family"] = family


def _set_font_size(api, size_str):
    try:
        size = int(size_str)
    except Exception:
        return
    raw = _get_raw_textbox(api)
    if raw is None:
        return
    family = _state["ui"].get("font_family", "Segoe UI")
    raw.configure(font=(family, size))
    _state["ui"]["font_size"] = size


def _bump_font_size(api, delta):
    current = _state["ui"].get("font_size", 14)
    new = max(8, min(72, current + delta))
    _state["ui"]["font_size"] = new
    raw = _get_raw_textbox(api)
    if raw is not None:
        family = _state["ui"].get("font_family", "Segoe UI")
        raw.configure(font=(family, new))


# ============================================================
#  Точка входа
# ============================================================

def register(api):
    _state["api"] = api
    _state["app"] = api["app"]

    api["add_plugin_button"](
        "📄 Редактор текста",
        lambda: _open_word_window(api),
    )
