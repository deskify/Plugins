# -*- coding: utf-8 -*-
"""
Плагин: Стикеры на рабочий стол для Deskify Workspace.
Позволяет создавать компактные, закрепленные поверх всех окон заметки-стикеры.
"""

import os
import json

api_workspace = None
STICKERS_FILE = "data/stickers_plugin.json"

class StickerWindow:
    def __init__(self, text="", x=None, y=None, is_pinned=True, color="#f1c40f"):
        self.ctk = api_workspace["ctk"]
        
        # Создаем окно через стандартный CTkToplevel (не open_window, чтобы убрать рамки)
        self.win = self.ctk.CTkToplevel()
        self.win.title("Стикер")
        
        # Настройка размеров и позиции
        self.win.geometry(f"240x200+{x if x else 400}+{y if y else 300}")
        self.win.minsize(180, 140)
        
        # Свойства окна: убираем стандартную шапку Windows для красоты
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", is_pinned)
        
        self.sticker_color = color
        self.is_pinned = is_pinned
        
        # Переменные для реализации перетаскивания окна за любое место
        self._drag_data = {"x": 0, "y": 0}
        
        self._build_ui(text)
        
    def _build_ui(self, text):
        # Главный контейнер стикера с заливкой
        self.main_frame = self.ctk.CTkFrame(self.win, fg_color=self.sticker_color, corner_radius=10, border_width=1, border_color="gray40")
        self.main_frame.pack(fill="both", expand=True)
        
        # Кастомная мини-панель управления (шапка стикера)
        self.top_bar = self.ctk.CTkFrame(self.main_frame, fg_color="transparent", height=24)
        self.top_bar.pack(fill="x", padx=6, pady=2)
        
        # Кнопка закрепления поверх окон
        pin_text = "📌" if self.is_pinned else "📍"
        self.pin_btn = self.ctk.CTkButton(self.top_bar, text=pin_text, width=20, height=20, fg_color="transparent", text_color="black", hover_color="#00000011", command=self._toggle_pin)
        self.pin_btn.pack(side="left", padx=2)
        
        # Кнопка смены цвета стикера (желтый / зеленый / сиреневый)
        self.color_btn = self.ctk.CTkButton(self.top_bar, text="🎨", width=20, height=20, fg_color="transparent", text_color="black", hover_color="#00000011", command=self._change_color)
        self.color_btn.pack(side="left", padx=2)
        
        # Кнопка закрытия/удаления
        self.close_btn = self.ctk.CTkButton(self.top_bar, text="✕", width=20, height=20, fg_color="transparent", text_color="black", hover_color="#c0392b", command=self._close_sticker)
        self.close_btn.pack(side="right", padx=2)
        
        # Текстовое поле для самой заметки
        self.textbox = self.ctk.CTkTextbox(
            self.main_frame, 
            fg_color="transparent", 
            text_color="black", 
            font=self.ctk.CTkFont(size=13, family="Courier New", weight="bold"),
            wrap="word"
        )
        self.textbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.textbox.insert("1.0", text)
        
        # Привязка событий мыши для свободного перетаскивания окна по экрану
        self.top_bar.bind("<ButtonPress-1>", self._on_drag_start)
        self.top_bar.bind("<B1-Motion>", self._on_drag_motion)
        
        # Отслеживаем изменения текста для автосохранения
        self.textbox.bind("<KeyRelease>", lambda e: save_all_stickers())

    def _on_drag_start(self, event):
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _on_drag_motion(self, event):
        deltax = event.x - self._drag_data["x"]
        deltay = event.y - self._drag_data["y"]
        x = self.win.winfo_x() + deltax
        y = self.win.winfo_y() + deltay
        self.win.geometry(f"+{x}+{y}")
        save_all_stickers()

    def _toggle_pin(self):
        self.is_pinned = not self.is_pinned
        self.win.attributes("-topmost", self.is_pinned)
        self.pin_btn.configure(text="📌" if self.is_pinned else "📍")
        save_all_stickers()

    def _change_color(self):
        colors = ["#f1c40f", "#2ecc71", "#9b59b6", "#e74c3c", "#3498db"] # Желтый, зеленый, фиолетовый, красный, синий
        try:
            current_idx = colors.index(self.sticker_color)
            next_idx = (current_idx + 1) % len(colors)
        except ValueError:
            next_idx = 0
            
        self.sticker_color = colors[next_idx]
        self.main_frame.configure(fg_color=self.sticker_color)
        save_all_stickers()

    def _close_sticker(self):
        self.win.destroy()
        if self in active_stickers:
            active_stickers.remove(self)
        save_all_stickers()


active_stickers = []

def create_new_sticker(text="", x=None, y=None, is_pinned=True, color="#f1c40f"):
    sticker = StickerWindow(text, x, y, is_pinned, color)
    active_stickers.append(sticker)
    save_all_stickers()

def save_all_stickers():
    """Сохраняет состояние всех открытых стикеров в JSON файл"""
    data = []
    for s in active_stickers:
        if s.win.winfo_exists():
            data.append({
                "text": s.textbox.get("1.0", "end-1c"),
                "x": s.win.winfo_x(),
                "y": s.win.winfo_y(),
                "is_pinned": s.is_pinned,
                "color": s.sticker_color
            })
    try:
        with open(STICKERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_stickers_history():
    """Загружает стикеры, которые были открыты при прошлом запуске программы"""
    if not os.path.exists(STICKERS_FILE):
        return
    try:
        with open(STICKERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data:
                create_new_sticker(
                    text=item.get("text", ""),
                    x=item.get("x"),
                    y=item.get("y"),
                    is_pinned=item.get("is_pinned", True),
                    color=item.get("color", "#f1c40f")
                )
    except Exception:
        pass


def register(api):
    global api_workspace
    api_workspace = api
    
    # Регистрируем кнопку в главном меню Deskify
    api["add_button"]("📌 Создать стикер", lambda: create_new_sticker("Новая заметка..."))
    
    # Автоматически восстанавливаем старые стикеры при загрузке плагина
    # Используем after(100), чтобы дождаться полной инициализации главного окна программы
    api_workspace["ctk"].CTkFrame(api_workspace["ctk"]()).after(100, load_stickers_history)
