# -*- coding: utf-8 -*-
"""
JSON-форма для Deskify.

Строит форму по схеме (поля разных типов), собирает значения и отдаёт
результат в виде JSON. Умеет:
  - добавлять/удалять поля прямо в окне;
  - импортировать/экспортировать схему формы;
  - копировать результат в буфер обмена;
  - сохранять результат в .json файл;
  - отправлять результат в блокнот Deskify (как новую заметку).

Схема формы — это список полей:
  {
    "key": "name",          # ключ в результирующем JSON
    "type": "text",         # text | number | textarea | checkbox | select
    "label": "Имя",         # подпись
    "default": "",          # значение по умолчанию
    "required": false,      # обязательное ли поле
    "options": ["a", "b"]   # только для type=select
  }
"""

import json
import copy
from datetime import datetime


_state = {
    "api": None,
    "app": None,
    "window": None,
    "schema": [],           # текущая схема полей
    "schema_path": None,    # путь к загруженному файлу схемы
    "fields_ui": [],        # список кортежей (frame, field_dict, value_getter)
}


# --- Схема по умолчанию ---

DEFAULT_SCHEMA = [
    {"key": "name",     "type": "text",     "label": "Имя",       "default": "", "required": True},
    {"key": "age",      "type": "number",   "label": "Возраст",   "default": 18, "required": False},
    {"key": "about",    "type": "textarea", "label": "О себе",    "default": "", "required": False},
    {"key": "agree",    "type": "checkbox", "label": "Согласен",  "default": False, "required": False},
    {"key": "city",     "type": "select",   "label": "Город",     "default": "Москва",
     "options": ["Москва", "Санкт-Петербург", "Казань", "Другое"], "required": False},
]

FIELD_TYPES = ["text", "number", "textarea", "checkbox", "select"]


# --- Сборка значения из виджета ---

def _make_value_getter(field, widget, var):
    ftype = field.get("type", "text")

    if ftype == "checkbox":
        return lambda: bool(var.get())

    if ftype == "number":
        def get():
            raw = (widget.get() or "").strip()
            if raw == "":
                return None
            # поддержка как int, так и float
            try:
                if "." in raw or "," in raw:
                    return float(raw.replace(",", "."))
                return int(raw)
            except ValueError:
                return raw  # если не число — вернём как есть, потом покажем предупреждение
        return get

    if ftype == "select":
        return lambda: var.get()

    # text / textarea
    return lambda: widget.get()


def _validate_field(field, value):
    """Возвращает (ok, error_message)."""
    if field.get("required"):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return False, f"Поле «{field.get('label', field.get('key'))}» обязательно"
        if field.get("type") == "checkbox" and value is False:
            return False, f"Нужно отметить «{field.get('label', field.get('key'))}»"
    if field.get("type") == "number" and value is not None and not isinstance(value, (int, float)):
        return False, f"Поле «{field.get('label', field.get('key'))}» должно быть числом"
    return True, None


def _collect_values():
    """Проходит по всем полям, собирает значения и ошибки валидации."""
    result = {}
    errors = []
    for field, getter in _state["fields_ui"]:
        key = field.get("key")
        if not key:
            continue
        try:
            value = getter()
        except Exception as e:
            errors.append(f"Поле «{key}»: {e}")
            continue
        ok, err = _validate_field(field, value)
        if not ok:
            errors.append(err)
        result[key] = value
    return result, errors


# --- Окно плагина ---

def _open_form_window(api):
    win = _state.get("window")
    if win is not None:
        try:
            if win.winfo_exists():
                win.deiconify(); win.lift(); win.focus_force()
                return
        except Exception:
            pass

    ctk = api["ctk"]
    app = api["app"]

    win = ctk.CTkToplevel(app)
    win.title("🧾 JSON-форма")
    win.geometry("900x680")
    win.minsize(780, 560)
    _state["window"] = win
    _state["fields_ui"] = []

    if not _state["schema"]:
        _state["schema"] = copy.deepcopy(DEFAULT_SCHEMA)

    # ---------- Верхняя панель ----------
    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=16, pady=(14, 4))
    ctk.CTkLabel(top, text="🧾 JSON-форма",
                 font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
    status_lbl = ctk.CTkLabel(top, text="", text_color="gray60",
                              font=ctk.CTkFont(size=12))
    status_lbl.pack(side="right")

    # ---------- Панель управления схемой ----------
    ctrl = ctk.CTkFrame(win, fg_color="transparent")
    ctrl.pack(fill="x", padx=16, pady=(0, 6))

    def load_schema_from_file():
        path = api["filedialog"].askopenfilename(
            title="Загрузить схему формы",
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("fields", [])
            if not isinstance(data, list):
                raise ValueError("Ожидается список полей")
            _state["schema"] = data
            _state["schema_path"] = path
            status_lbl.configure(text=f"Схема загружена: {path}",
                                 text_color="#2ecc71")
            _rebuild_fields(api, fields_container, result_box)
        except Exception as e:
            api["messagebox"].showerror("Ошибка схемы", str(e))

    def save_schema_to_file():
        path = api["filedialog"].asksaveasfilename(
            title="Сохранить схему формы",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="form_schema.json",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"fields": _state["schema"]}, f,
                          ensure_ascii=False, indent=2)
            status_lbl.configure(text=f"Схема сохранена: {path}",
                                 text_color="#2ecc71")
        except Exception as e:
            api["messagebox"].showerror("Ошибка сохранения", str(e))

    def reset_schema():
        if api["messagebox"].askyesno(
                "Сброс формы",
                "Вернуть схему формы к стандартной? Текущие поля будут потеряны."):
            _state["schema"] = copy.deepcopy(DEFAULT_SCHEMA)
            _state["schema_path"] = None
            _rebuild_fields(api, fields_container, result_box)

    ctk.CTkButton(ctrl, text="📂 Загрузить схему", width=160,
                  command=load_schema_from_file).pack(side="left", padx=(0, 6))
    ctk.CTkButton(ctrl, text="💾 Сохранить схему", width=160, fg_color="gray30",
                  command=save_schema_to_file).pack(side="left", padx=3)
    ctk.CTkButton(ctrl, text="↺ По умолчанию", width=140, fg_color="gray30",
                  command=reset_schema).pack(side="left", padx=3)

    # ---------- Двухколоночный layout ----------
    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    left = ctk.CTkFrame(body)
    left.pack(side="left", fill="both", expand=True, padx=(0, 8))

    right = ctk.CTkFrame(body, width=340)
    right.pack(side="right", fill="y")
    right.pack_propagate(False)

    # ---------- Левая колонка: конструктор полей + сами поля ----------
    ctk.CTkLabel(left, text="Поля формы",
                 font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=4, pady=(4, 2))

    fields_container = ctk.CTkScrollableFrame(left, fg_color=("gray90", "gray15"))
    fields_container.pack(fill="both", expand=True, padx=2, pady=(0, 6))

    # ---------- Добавление нового поля ----------
    add_row = ctk.CTkFrame(left, fg_color="transparent")
    add_row.pack(fill="x", padx=2, pady=(0, 2))

    new_key_var = ctk.StringVar()
    new_label_var = ctk.StringVar()
    new_type_var = ctk.StringVar(value="text")

    ctk.CTkEntry(add_row, textvariable=new_key_var, width=140,
                 placeholder_text="ключ (key)").pack(side="left", padx=2)
    ctk.CTkEntry(add_row, textvariable=new_label_var, width=170,
                 placeholder_text="подпись").pack(side="left", padx=2)
    ctk.CTkOptionMenu(add_row, variable=new_type_var, values=FIELD_TYPES,
                      width=110).pack(side="left", padx=2)

    def add_field():
        key = new_key_var.get().strip()
        label = new_label_var.get().strip() or key
        ftype = new_type_var.get()
        if not key:
            api["toast"]("Укажите ключ поля (key)", "error")
            return
        if any(f.get("key") == key for f in _state["schema"]):
            api["toast"](f"Ключ «{key}» уже существует", "error")
            return
        field = {"key": key, "type": ftype, "label": label,
                 "default": "", "required": False}
        if ftype == "select":
            field["options"] = ["Вариант 1", "Вариант 2"]
        if ftype == "checkbox":
            field["default"] = False
        _state["schema"].append(field)
        new_key_var.set("")
        new_label_var.set("")
        _rebuild_fields(api, fields_container, result_box)

    ctk.CTkButton(add_row, text="+ Добавить", width=110,
                  command=add_field).pack(side="left", padx=2)

    # ---------- Правая колонка: результат ----------
    ctk.CTkLabel(right, text="Результат (JSON)",
                 font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=4, pady=(4, 2))

    result_box = ctk.CTkTextbox(right, wrap="none", font=ctk.CTkFont(family="Consolas", size=12))
    result_box.pack(fill="both", expand=True, padx=2, pady=(0, 6))

    result_actions = ctk.CTkFrame(right, fg_color="transparent")
    result_actions.pack(fill="x", padx=2)

    def update_preview():
        data, errors = _collect_values()
        try:
            pretty = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            pretty = str(data)
        if errors:
            pretty = "// Ошибки валидации:\n// " + "\n// ".join(errors) + "\n\n" + pretty
        result_box.delete("1.0", "end")
        result_box.insert("1.0", pretty)

    _state["update_preview"] = update_preview

    def copy_json():
        data, errors = _collect_values()
        if errors:
            if not api["messagebox"].askyesno(
                    "Есть ошибки", "Найдены ошибки валидации:\n\n" +
                    "\n".join(errors) + "\n\nВсё равно скопировать?"):
                return
        text = json.dumps(data, ensure_ascii=False, indent=2)
        win.clipboard_clear()
        win.clipboard_append(text)
        win.update()
        status_lbl.configure(text="Скопировано в буфер", text_color="#2ecc71")
        api["toast"]("JSON скопирован в буфер", "success")

    def save_json():
        data, errors = _collect_values()
        if errors:
            if not api["messagebox"].askyesno(
                    "Есть ошибки", "Найдены ошибки валидации:\n\n" +
                    "\n".join(errors) + "\n\nВсё равно сохранить?"):
                return
        path = api["filedialog"].asksaveasfilename(
            title="Сохранить результат",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"form_result_{datetime.now():%Y%m%d_%H%M%S}.json",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            status_lbl.configure(text=f"Сохранено: {path}", text_color="#2ecc71")
            api["toast"]("Результат сохранён", "success")
        except Exception as e:
            api["messagebox"].showerror("Ошибка сохранения", str(e))

    def send_to_notepad():
        data, errors = _collect_values()
        if errors:
            if not api["messagebox"].askyesno(
                    "Есть ошибки", "Найдены ошибки валидации:\n\n" +
                    "\n".join(errors) + "\n\nВсё равно отправить?"):
                return
        text = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            docs = api["get_notes_docs"]()
            new_id = str(int(datetime.now().timestamp() * 1000))
            title = f"JSON-форма {datetime.now():%d.%m %H:%M}"
            docs.append({
                "id": new_id,
                "title": title,
                "content": text,
                "formatting": [],
            })
            api["DataManager"].save_json("notes_docs.json", docs)
            api["notify"]("JSON-форма", f"Создана заметка «{title}»")
            status_lbl.configure(text="Отправлено в блокнот", text_color="#2ecc71")
        except Exception as e:
            api["messagebox"].showerror("Ошибка", f"Не удалось создать заметку: {e}")

    def clear_form():
        for field, _ in _state["fields_ui"]:
            # сбрасываем виджеты к default
            pass
        _state["schema"] = copy.deepcopy(_state["schema"])
        # Проще всего — перерисовать поля, что сбросит их к default
        for f in _state["schema"]:
            pass
        _rebuild_fields(api, fields_container, result_box, use_defaults=True)

    ctk.CTkButton(result_actions, text="📋 Копировать", width=150,
                  command=copy_json).pack(fill="x", pady=2)
    ctk.CTkButton(result_actions, text="💾 Сохранить в .json", width=150,
                  fg_color="gray30", command=save_json).pack(fill="x", pady=2)
    ctk.CTkButton(result_actions, text="📝 В блокнот Deskify", width=150,
                  fg_color="gray30", command=send_to_notepad).pack(fill="x", pady=2)
    ctk.CTkButton(result_actions, text="↺ Очистить форму", width=150,
                  fg_color="gray30", command=clear_form).pack(fill="x", pady=2)

    # ---------- Первичная отрисовка ----------
    _rebuild_fields(api, fields_container, result_box)

    win.protocol("WM_DELETE_WINDOW", lambda: _close_window(win))


def _close_window(win):
    try:
        win.destroy()
    except Exception:
        pass
    _state["window"] = None


# --- Перерисовка полей ---

def _rebuild_fields(api, container, result_box, use_defaults=True):
    ctk = api["ctk"]

    for w in list(container.winfo_children()):
        w.destroy()
    _state["fields_ui"] = []

    if not _state["schema"]:
        ctk.CTkLabel(container, text="Полей пока нет — добавьте первое ниже.",
                     text_color="gray60").pack(pady=20)
        _state["update_preview"]()
        return

    for idx, field in enumerate(list(_state["schema"])):
        ftype = field.get("type", "text")
        key = field.get("key", "")
        label = field.get("label", key)
        default = field.get("default", "" if ftype != "checkbox" else False)
        if not use_defaults:
            default = field.get("_current", default)

        card = ctk.CTkFrame(container, corner_radius=6)
        card.pack(fill="x", padx=4, pady=4)

        # Заголовок карточки: подпись, тип, обязательность, удалить
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(6, 2))

        title = f"{label}  ({key})"
        if field.get("required"):
            title += "  *"
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(weight="bold"),
                     anchor="w").pack(side="left")
        ctk.CTkLabel(head, text=f"[{ftype}]", text_color="gray60",
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=6)

        def make_remove(i=idx, k=key):
            def do_remove():
                try:
                    _state["schema"].pop(i)
                except IndexError:
                    return
                _rebuild_fields(api, container, result_box)
            return do_remove

        ctk.CTkButton(head, text="✕", width=26, height=22,
                      fg_color="transparent", hover_color="#c0392b",
                      command=make_remove()).pack(side="right")

        # Чекбокс "обязательное"
        req_var = ctk.BooleanVar(value=bool(field.get("required", False)))

        def make_required(f=field, v=req_var):
            def on_toggle():
                f["required"] = bool(v.get())
            return on_toggle

        ctk.CTkCheckBox(head, text="обяз.", variable=req_var,
                        width=70, command=make_required()).pack(side="right", padx=6)

        # Тело: сам виджет ввода
        body_row = ctk.CTkFrame(card, fg_color="transparent")
        body_row.pack(fill="x", padx=8, pady=(2, 8))

        if ftype == "checkbox":
            var = ctk.BooleanVar(value=bool(default))
            ctk.CTkCheckBox(body_row, text="Да / включено",
                            variable=var).pack(anchor="w")
            getter = _make_value_getter(field, None, var)
            _state["fields_ui"].append((field, getter))
            # мгновенное обновление превью
            var.trace_add("write", lambda *_: _state["update_preview"]())

        elif ftype == "textarea":
            widget = ctk.CTkTextbox(body_row, height=70, wrap="word")
            widget.pack(fill="x")
            if default:
                widget.insert("1.0", str(default))
            widget.bind("<KeyRelease>", lambda e: _state["update_preview"]())

            def getter(w=widget):
                return w.get("1.0", "end-1c")
            _state["fields_ui"].append((field, getter))

        elif ftype == "select":
            options = field.get("options") or ["Вариант 1"]
            current = default if default in options else options[0]
            var = ctk.StringVar(value=str(current))
            ctk.CTkOptionMenu(body_row, variable=var,
                              values=[str(o) for o in options]).pack(anchor="w")
            getter = _make_value_getter(field, None, var)
            _state["fields_ui"].append((field, getter))
            var.trace_add("write", lambda *_: _state["update_preview"]())

        elif ftype == "number":
            widget = ctk.CTkEntry(body_row, width=180)
            widget.pack(anchor="w")
            if default not in ("", None):
                widget.insert(0, str(default))
            widget.bind("<KeyRelease>", lambda e: _state["update_preview"]())
            getter = _make_value_getter(field, widget, None)
            _state["fields_ui"].append((field, getter))

        else:  # text
            widget = ctk.CTkEntry(body_row)
            widget.pack(fill="x")
            if default not in ("", None):
                widget.insert(0, str(default))
            widget.bind("<KeyRelease>", lambda e: _state["update_preview"]())
            getter = _make_value_getter(field, widget, None)
            _state["fields_ui"].append((field, getter))

    # Первичный предпросмотр
    _state["update_preview"]()


# --- Точка входа ---

def register(api):
    _state["api"] = api
    _state["app"] = api["app"]

    api["add_plugin_button"](
        "🧾 JSON-форма",
        lambda: _open_form_window(api),
    )