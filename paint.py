# -*- coding: utf-8 -*-
"""
Плагин «Paint» для Deskify.
Растровый редактор: кисть, ластик, фигуры, заливка, текст, пипетка.

Зависимость (опционально): Pillow (pip install Pillow).
Без Pillow редактор работает, но кнопки Открыть/Сохранить отключены.

Холст — обычный tkinter.Canvas. Отмена/повтор — снимки через Pillow или
встроенный PostScript (если Pillow нет).
"""

import os
import io
from datetime import datetime


try:
    from PIL import Image, ImageTk, ImageDraw, ImageGrab
    HAS_PIL = True
except ImportError:
    Image = ImageTk = ImageDraw = None
    HAS_PIL = False


# ============================================================
#  Палитра
# ============================================================

PALETTE = [
    "#000000", "#404040", "#808080", "#c0c0c0", "#ffffff",
    "#c0392b", "#e74c3c", "#e67e22", "#f1c40f", "#fef08a",
    "#27ae60", "#2ecc71", "#16a085", "#1abc9c", "#2980b9",
    "#3498db", "#8e44ad", "#9b59b6", "#e91e63", "#fbcfe8",
]

TOOLS = {
    "brush":   ("🖌 Кисть",     "ЛКМ — рисовать"),
    "eraser":  ("🧽 Ластик",    "ЛКМ — стирать"),
    "line":    ("╱ Линия",     "ЛКМ — тянуть линию"),
    "rect":    ("▭ Прямоугольник", "ЛКМ — растянуть"),
    "ellipse": ("◯ Эллипс",    "ЛКМ — растянуть"),
    "fill":    ("🪣 Заливка",   "ЛКМ — залить область"),
    "picker":  ("💧 Пипетка",   "ЛКМ — взять цвет"),
    "text":    ("T Текст",     "ЛКМ — поставить текст"),
}


_state = {
    "api": None,
    "app": None,
    "window": None,
    "canvas": None,
    "tool": "brush",
    "color": "#000000",
    "bg_color": "#ffffff",
    "width": 5,
    "last_x": None,
    "last_y": None,
    "shape_id": None,
    "start_x": None,
    "start_y": None,
    "undo_stack": [],
    "redo_stack": [],
    "canvas_w": 800,
    "canvas_h": 600,
    "ui": {},
    "image": None,       # PIL.Image — мастер-копия для сохранения
}


# ============================================================
#  Утилиты
# ============================================================

def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


# ============================================================
#  Работа с холстом
# ============================================================

def _clear_canvas(api):
    cv = _state["canvas"]
    if cv is None:
        return
    cv.delete("all")
    cv.create_rectangle(0, 0, _state["canvas_w"], _state["canvas_h"],
                        fill=_state["bg_color"], outline="", tags=("bg",))
    cv.tag_lower("bg")


def _snapshot(api):
    """Сохраняет текущее состояние холста для undo."""
    cv = _state["canvas"]
    if cv is None:
        return
    try:
        ps = cv.postscript(colormode="color")
    except Exception:
        ps = None
    if ps is None:
        return
    _state["undo_stack"].append(ps)
    if len(_state["undo_stack"]) > 40:
        _state["undo_stack"].pop(0)
    _state["redo_stack"].clear()


def _restore_from_ps(api, ps):
    """Восстанавливает холст из postscript-снимка.

    На чистом Tk это невозможно без ImageMagick или Ghostscript, поэтому
    если есть Pillow — конвертируем через Image.open(io.BytesIO(ps)).
    Если нет — undo/redo отключены.
    """
    if not HAS_PIL:
        return
    try:
        img = Image.open(io.BytesIO(ps.encode("ascii")))
        img.load()
    except Exception:
        return
    _render_image_to_canvas(api, img)


def _render_image_to_canvas(api, img):
    """Рисует PIL.Image на Canvas, растягивая под размер холста."""
    cv = _state["canvas"]
    if cv is None or img is None:
        return
    img = img.convert("RGB")
    img = img.resize((_state["canvas_w"], _state["canvas_h"]), Image.LANCZOS)
    _state["image"] = img
    tkimg = ImageTk.PhotoImage(img)
    cv.delete("all")
    cv.create_image(0, 0, anchor="nw", image=tkimg, tags=("bg",))
    cv.tag_lower("bg")
    _state["ui"]["_tkimg"] = tkimg  # держим ссылку, иначе сборщик мусора удалит


def _undo(api):
    if not HAS_PIL:
        api["toast"]("Отмена недоступна без Pillow", "error")
        return
    if not _state["undo_stack"]:
        api["toast"]("Нечего отменять", "info")
        return
    current = None
    try:
        current = _state["canvas"].postscript(colormode="color")
    except Exception:
        pass
    _state["redo_stack"].append(current)
    ps = _state["undo_stack"].pop()
    _restore_from_ps(api, ps)


def _redo(api):
    if not HAS_PIL:
        api["toast"]("Повтор недоступен без Pillow", "error")
        return
    if not _state["redo_stack"]:
        api["toast"]("Нечего повторять", "info")
        return
    try:
        current = _state["canvas"].postscript(colormode="color")
        _state["undo_stack"].append(current)
    except Exception:
        pass
    ps = _state["redo_stack"].pop()
    if ps:
        _restore_from_ps(api, ps)


# ============================================================
#  Рисование
# ============================================================

def _on_press(api, event):
    _snapshot(api)
    _state["start_x"] = event.x
    _state["start_y"] = event.y
    _state["last_x"] = event.x
    _state["last_y"] = event.y

    tool = _state["tool"]
    cv = _state["canvas"]

    if tool == "picker":
        _pick_color(api, event)
        return

    if tool == "fill":
        _flood_fill(api, event.x, event.y)
        return

    if tool == "text":
        _ask_text(api, event.x, event.y)
        return

    if tool in ("line", "rect", "ellipse"):
        # создаём временную фигуру
        color = _state["color"]
        w = _state["width"]
        if tool == "line":
            sid = cv.create_line(event.x, event.y, event.x, event.y,
                                  fill=color, width=w, capstyle="round")
        elif tool == "rect":
            sid = cv.create_rectangle(event.x, event.y, event.x, event.y,
                                       outline=color, width=w)
        else:
            sid = cv.create_oval(event.x, event.y, event.x, event.y,
                                  outline=color, width=w)
        _state["shape_id"] = sid
        return

    if tool in ("brush", "eraser"):
        color = _state["bg_color"] if tool == "eraser" else _state["color"]
        w = _state["width"]
        cv.create_line(event.x, event.y, event.x + 0.01, event.y + 0.01,
                       fill=color, width=w, capstyle="round", smooth=True,
                       tags=("draw",))


def _on_drag(api, event):
    tool = _state["tool"]
    cv = _state["canvas"]

    if tool in ("brush", "eraser"):
        if _state["last_x"] is None:
            return
        color = _state["bg_color"] if tool == "eraser" else _state["color"]
        w = _state["width"]
        cv.create_line(_state["last_x"], _state["last_y"], event.x, event.y,
                       fill=color, width=w, capstyle="round", smooth=True,
                       tags=("draw",))
        _state["last_x"] = event.x
        _state["last_y"] = event.y
        return

    if tool in ("line", "rect", "ellipse") and _state["shape_id"] is not None:
        sid = _state["shape_id"]
        sx, sy = _state["start_x"], _state["start_y"]
        try:
            if tool == "line":
                cv.coords(sid, sx, sy, event.x, event.y)
            elif tool == "rect":
                cv.coords(sid, sx, sy, event.x, event.y)
            elif tool == "ellipse":
                cv.coords(sid, sx, sy, event.x, event.y)
        except Exception:
            pass


def _on_release(api, event):
    tool = _state["tool"]
    if tool in ("line", "rect", "ellipse"):
        _state["shape_id"] = None
        # применяем "закрепление" на мастер-изображении
        if HAS_PIL:
            _commit_to_image(api)
    elif tool in ("brush", "eraser"):
        if HAS_PIL:
            _commit_to_image(api)
    _state["last_x"] = _state["last_y"] = None
    _state["start_x"] = _state["start_y"] = None


def _pick_color(api, event):
    """Пипетка: берёт цвет пикселя из мастер-изображения."""
    if not HAS_PIL or _state["image"] is None:
        api["toast"]("Пипетка требует Pillow и загруженного холста", "error")
        return
    try:
        x = max(0, min(_state["canvas_w"] - 1, event.x))
        y = max(0, min(_state["canvas_h"] - 1, event.y))
        rgb = _state["image"].getpixel((x, y))
        hex_color = _rgb_to_hex(rgb)
        _set_color(api, hex_color)
    except Exception as e:
        api["toast"](f"Не удалось взять цвет: {e}", "error")


def _flood_fill(api, x, y):
    """Заливка области. Работает через Pillow на мастер-изображении."""
    if not HAS_PIL or _state["image"] is None:
        api["toast"]("Заливка требует Pillow", "error")
        return

    # Сначала фиксируем всё, что нарисовано на холсте, в мастер-изображение
    _commit_to_image(api)

    img = _state["image"]
    try:
        ImageDraw.floodfill(img, (x, y), _hex_to_rgb(_state["color"]), thresh=30)
    except Exception as e:
        api["toast"](f"Ошибка заливки: {e}", "error")
        return
    _render_image_to_canvas(api, img)


def _ask_text(api, x, y):
    """Спрашивает текст и рисует его на холсте."""
    from tkinter import simpledialog
    text = simpledialog.askstring("Текст", "Введите текст:", parent=_state["window"])
    if not text:
        return
    cv = _state["canvas"]
    cv.create_text(x, y, text=text, fill=_state["color"],
                   font=("Segoe UI", max(10, _state["width"] * 3)),
                   anchor="nw", tags=("draw",))
    if HAS_PIL:
        _commit_to_image(api)


def _commit_to_image(api):
    """Рендерит текущий Canvas в PIL.Image, чтобы заливка/сохранение работали."""
    if not HAS_PIL:
        return
    cv = _state["canvas"]
    if cv is None:
        return
    try:
        ps = cv.postscript(colormode="color")
        img = Image.open(io.BytesIO(ps.encode("ascii")))
        img.load()
        img = img.convert("RGB").resize((_state["canvas_w"], _state["canvas_h"]),
                                         Image.LANCZOS)
        _state["image"] = img
    except Exception:
        # если PostScript не читается — оставляем как есть
        pass


# ============================================================
#  UI
# ============================================================

def _set_tool(api, name):
    if name not in TOOLS:
        return
    _state["tool"] = name
    for key, btn in _state["ui"].get("tool_buttons", {}).items():
        try:
            btn.configure(fg_color="#1f538d" if key == name else "gray30")
        except Exception:
            pass
    hint = TOOLS[name][1]
    lbl = _state["ui"].get("hint")
    if lbl is not None:
        try:
            lbl.configure(text=hint)
        except Exception:
            pass


def _set_color(api, hex_color):
    _state["color"] = hex_color
    swatch = _state["ui"].get("color_swatch")
    if swatch is not None:
        try:
            swatch.configure(fg_color=hex_color)
        except Exception:
            pass


def _set_bg_color(api, hex_color):
    _state["bg_color"] = hex_color


def _pick_custom_color(api):
    from tkinter import colorchooser
    rgb, hex_color = colorchooser.askcolor(color=_state["color"], parent=_state["window"])
    if hex_color:
        _set_color(api, hex_color)


def _clear(api):
    _snapshot(api)
    _clear_canvas(api)
    if HAS_PIL:
        _state["image"] = Image.new("RGB", (_state["canvas_w"], _state["canvas_h"]),
                                     _hex_to_rgb(_state["bg_color"]))


def _save_png(api):
    if not HAS_PIL:
        api["messagebox"].showerror("Paint", "Сохранение требует Pillow.")
        return
    _commit_to_image(api)
    img = _state["image"]
    if img is None:
        api["toast"]("Холст пуст", "error")
        return
    path = api["filedialog"].asksaveasfilename(
        title="Сохранить как PNG",
        defaultextension=".png",
        filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("BMP", "*.bmp")],
        initialfile=f"paint_{datetime.now():%Y%m%d_%H%M%S}.png",
    )
    if not path:
        return
    try:
        img.save(path)
        api["toast"]("Сохранено", "success")
    except Exception as e:
        api["messagebox"].showerror("Ошибка", str(e))


def _open_image(api):
    if not HAS_PIL:
        api["messagebox"].showerror("Paint", "Открытие требует Pillow.")
        return
    path = api["filedialog"].askopenfilename(
        title="Открыть изображение",
        filetypes=[("Изображения", "*.png *.jpg *.jpeg *.gif *.bmp"), ("Все файлы", "*.*")],
    )
    if not path:
        return
    try:
        img = Image.open(path).convert("RGB")
    except Exception as e:
        api["messagebox"].showerror("Ошибка", str(e))
        return
    # подстраиваем размер холста под картинку
    w, h = img.size
    _state["canvas_w"] = w
    _state["canvas_h"] = h
    cv = _state["canvas"]
    cv.configure(width=w, height=h)
    cv.delete("all")
    _render_image_to_canvas(api, img)
    _state["undo_stack"].clear()
    _state["redo_stack"].clear()
    api["toast"](f"Загружено: {os.path.basename(path)}", "success")


def _new_canvas(api):
    from tkinter import simpledialog
    w = simpledialog.askinteger("Новый холст", "Ширина:", initialvalue=800,
                                 minvalue=50, maxvalue=4000, parent=_state["window"])
    if not w:
        return
    h = simpledialog.askinteger("Новый холст", "Высота:", initialvalue=600,
                                 minvalue=50, maxvalue=4000, parent=_state["window"])
    if not h:
        return
    _state["canvas_w"] = w
    _state["canvas_h"] = h
    cv = _state["canvas"]
    cv.configure(width=w, height=h)
    cv.delete("all")
    cv.create_rectangle(0, 0, w, h, fill=_state["bg_color"], outline="", tags=("bg",))
    cv.tag_lower("bg")
    _state["undo_stack"].clear()
    _state["redo_stack"].clear()
    if HAS_PIL:
        _state["image"] = Image.new("RGB", (w, h), _hex_to_rgb(_state["bg_color"]))


# ============================================================
#  Главное окно
# ============================================================

def _open_paint_window(api):
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
    win.title("🎨 Paint")
    win.geometry("1100x760")
    win.minsize(900, 600)
    _state["window"] = win
    _state["ui"] = {}

    if not HAS_PIL:
        warn = ctk.CTkFrame(win, fg_color="#7a3a10", corner_radius=6)
        warn.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            warn,
            text="⚠ Обновите workspace до версии 1.1 и выше. Открытие/сохранение, заливка, "
                 "пипетка и отмена недоступны.\n"
                 "Нет библиотеки pillow",
            text_color="white", justify="left",
            font=ctk.CTkFont(size=12),
        ).pack(padx=10, pady=8, anchor="w")

    # ---------- Верхнее меню ----------
    menubar = ctk.CTkFrame(win, height=38)
    menubar.pack(fill="x", side="top", padx=8, pady=(6, 0))

    def act(text, command, color="transparent"):
        ctk.CTkButton(menubar, text=text, height=30, width=110, fg_color=color,
                      hover_color="gray30", command=command).pack(side="left", padx=2, pady=3)

    act("🆕 Новый", lambda: _new_canvas(api))
    act("📂 Открыть", lambda: _open_image(api))
    act("💾 Сохранить PNG", lambda: _save_png(api))
    act("🧹 Очистить", lambda: _clear(api))
    act("↶ Отмена", lambda: _undo(api))
    act("↷ Повтор", lambda: _redo(api))

    # ---------- Инструменты ----------
    tools_frame = ctk.CTkFrame(win)
    tools_frame.pack(fill="x", padx=8, pady=6)

    _state["ui"]["tool_buttons"] = {}
    for key, (label, hint) in TOOLS.items():
        b = ctk.CTkButton(tools_frame, text=label, height=32, width=130,
                          fg_color="gray30",
                          command=lambda k=key: _set_tool(api, k))
        b.pack(side="left", padx=2)
        _state["ui"]["tool_buttons"][key] = b

    ctk.CTkButton(tools_frame, text="⚙", width=32, height=32, fg_color="gray30",
                  command=lambda: _pick_custom_color(api)).pack(side="left", padx=(12, 2))

    swatch = ctk.CTkLabel(tools_frame, text="", width=32, height=32,
                          fg_color=_state["color"], corner_radius=4)
    swatch.pack(side="left", padx=4)
    _state["ui"]["color_swatch"] = swatch

    # Толщина
    ctk.CTkLabel(tools_frame, text="Толщина:").pack(side="left", padx=(14, 4))
    width_var = ctk.IntVar(value=_state["width"])
    width_lbl = ctk.CTkLabel(tools_frame, text=str(_state["width"]), width=32)
    width_lbl.pack(side="left")

    def on_width(v):
        _state["width"] = int(float(v))
        width_lbl.configure(text=str(_state["width"]))

    w_slider = ctk.CTkSlider(tools_frame, from_=1, to=50, width=140,
                              variable=width_var, command=on_width)
    w_slider.pack(side="left", padx=(4, 14))

    # ---------- Палитра ----------
    palette_frame = ctk.CTkFrame(win)
    palette_frame.pack(fill="x", padx=8, pady=(0, 6))

    ctk.CTkLabel(palette_frame, text="Цвет:",
                 font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(8, 6))

    def mk_color_btn(color_hex):
        return ctk.CTkButton(
            palette_frame, text="", width=24, height=24,
            fg_color=color_hex, hover_color=color_hex,
            border_width=1, border_color="#333",
            command=lambda c=color_hex: _set_color(api, c),
        )

    for c in PALETTE:
        mk_color_btn(c).pack(side="left", padx=1, pady=6)

    ctk.CTkButton(palette_frame, text="…", width=32, height=24, fg_color="gray30",
                  command=lambda: _pick_custom_color(api)).pack(side="left", padx=6)

    # ---------- Холст ----------
    canvas_outer = ctk.CTkFrame(win, fg_color="transparent")
    canvas_outer.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    # скролл
    xscroll = ctk.CTkScrollbar(canvas_outer, orientation="horizontal")
    yscroll = ctk.CTkScrollbar(canvas_outer, orientation="vertical")

    cv = api["tk"].Canvas(
        canvas_outer,
        width=_state["canvas_w"], height=_state["canvas_h"],
        bg=_state["bg_color"], highlightthickness=1,
        highlightbackground="#333",
        xscrollcommand=xscroll.set, yscrollcommand=yscroll.set,
        cursor="crosshair",
    )
    xscroll.configure(command=cv.xview)
    yscroll.configure(command=cv.yview)

    cv.grid(row=0, column=0, sticky="nsew")
    yscroll.grid(row=0, column=1, sticky="ns")
    xscroll.grid(row=1, column=0, sticky="ew")
    canvas_outer.rowconfigure(0, weight=1)
    canvas_outer.columnconfigure(0, weight=1)

    cv.configure(scrollregion=(0, 0, _state["canvas_w"], _state["canvas_h"]))
    _state["canvas"] = cv

    _clear_canvas(api)
    if HAS_PIL:
        _state["image"] = Image.new("RGB", (_state["canvas_w"], _state["canvas_h"]),
                                     _hex_to_rgb(_state["bg_color"]))

    # привязки
    cv.bind("<Button-1>", lambda e: _on_press(api, e))
    cv.bind("<B1-Motion>", lambda e: _on_drag(api, e))
    cv.bind("<ButtonRelease-1>", lambda e: _on_release(api, e))

    win.bind("<Control-z>", lambda e: (_undo(api), "break")[1])
    win.bind("<Control-y>", lambda e: (_redo(api), "break")[1])
    win.bind("<Control-s>", lambda e: (_save_png(api), "break")[1])
    win.bind("<Control-o>", lambda e: (_open_image(api), "break")[1])
    win.bind("<Control-n>", lambda e: (_new_canvas(api), "break")[1])

    # ---------- Подвал: подсказка ----------
    hint = ctk.CTkLabel(win, text=TOOLS[_state["tool"]][1],
                         font=ctk.CTkFont(size=11), text_color="gray60")
    hint.pack(fill="x", padx=14, pady=(0, 8))
    _state["ui"]["hint"] = hint

    _set_tool(api, _state["tool"])
    _set_color(api, _state["color"])

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

    api["add_plugin_button"](
        "🎨 Paint",
        lambda: _open_paint_window(api),
    )
