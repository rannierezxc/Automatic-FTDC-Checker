"""
Shared custom UI widgets for PTS Hub.
"""
import tkinter as tk
from core.theme import (
    MAIN_BG, CARD_BG, CARD_BORDER, BORDER_COLOR, TEXT_FG, TEXT_MUTED, TEXT_DISABLED,
    ENTRY_BG, ENTRY_FG, ENTRY_DISABLED_BG, ENTRY_DISABLED_FG, ENTRY_BORDER, ENTRY_FOCUS_BORDER,
    ENTRY_SELECT_BG, ENTRY_SELECT_FG,
    BTN_BG, BTN_FG, BTN_BORDER, BTN_HOVER_BG, BTN_HOVER_BORDER, BTN_HOVER_FG,
    BTN_DISABLED_BG, BTN_DISABLED_FG, BTN_DISABLED_BORDER,
    ACCENT_BTN_BG, ACCENT_BTN_FG, ACCENT_BTN_HOVER_BG, ACCENT_BTN_HOVER_FG,
    ACCENT_BTN_DISABLED_BG, ACCENT_BTN_DISABLED_FG,
    FONT_CARD_TITLE, FONT_LABEL, FONT_ENTRY, FONT_BUTTON, FONT_BUTTON_ACCENT,
    PAD_CARD_X, PAD_CARD_Y, PAD_BTN_X, PAD_BTN_Y, PAD_ACCENT_BTN_X, PAD_ACCENT_BTN_Y
)


class ModernHoverButton(tk.Label):
    """Modern flat button with light-blue hover fill and darker border (single HWND)."""
    def __init__(
        self, parent, text="", command=None, font=FONT_BUTTON,
        padx=PAD_BTN_X, pady=PAD_BTN_Y, bg=BTN_BG, border_color=BTN_BORDER,
        hover_bg=BTN_HOVER_BG, hover_border=BTN_HOVER_BORDER, hover_fg=BTN_HOVER_FG,
        disabled_bg=BTN_DISABLED_BG, disabled_fg=BTN_DISABLED_FG,
        **kwargs
    ):
        super().__init__(
            parent, text=text, font=font, bg=bg, fg=BTN_FG,
            padx=padx, pady=pady, cursor="hand2",
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=border_color, highlightcolor=border_color,
            **kwargs
        )
        self.inner = self  # Backward-compatible alias
        self.command = command
        self._state = "normal"
        self._bg = bg
        self._border = border_color
        self._hover_bg = hover_bg
        self._hover_border = hover_border
        self._hover_fg = hover_fg
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._disabled_border = BTN_DISABLED_BORDER
        self._default_fg = BTN_FG

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, e):
        if self._state == "normal":
            self.configure(bg=self._hover_bg, fg=self._hover_fg, highlightbackground=self._hover_border)

    def _on_leave(self, e):
        if self._state == "normal":
            self.configure(bg=self._bg, fg=self._default_fg, highlightbackground=self._border)

    def _on_click(self, e):
        if self._state == "normal" and self.command:
            self.command()

    def configure(self, **kwargs):
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "state" in kwargs:
            st = kwargs.pop("state")
            self._state = st
            if st == "disabled":
                super().configure(bg=self._disabled_bg, fg=self._disabled_fg, highlightbackground=self._disabled_border, cursor="arrow")
            else:
                super().configure(bg=self._bg, fg=self._default_fg, highlightbackground=self._border, cursor="hand2")
        if kwargs:
            super().configure(**kwargs)

    def config(self, **kwargs):
        self.configure(**kwargs)

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "command":
            return self.command
        return super().cget(key)

    def __getitem__(self, key):
        if key == "state":
            return self._state
        if key == "command":
            return self.command
        return super().__getitem__(key)


class ModernAccentButton(tk.Label):
    """High-performance prominent accent button (replaces slow composite Accent.TButton)."""
    def __init__(
        self, parent, text="", command=None, font=FONT_BUTTON_ACCENT,
        padx=PAD_ACCENT_BTN_X, pady=PAD_ACCENT_BTN_Y, bg=ACCENT_BTN_BG, fg=ACCENT_BTN_FG,
        border_color=BORDER_COLOR,
        hover_bg=ACCENT_BTN_HOVER_BG, hover_fg=ACCENT_BTN_HOVER_FG, **kwargs
    ):
        super().__init__(
            parent, text=text, font=font, bg=bg, fg=fg,
            padx=padx, pady=pady, cursor="hand2",
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=border_color, highlightcolor=border_color,
            **kwargs
        )
        self.inner = self
        self.command = command
        self._state = "normal"
        self._bg = bg
        self._border = border_color
        self._hover_bg = hover_bg
        self._fg = fg
        self._hover_fg = hover_fg

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, e):
        if self._state == "normal":
            self.configure(bg=self._hover_bg, fg=self._hover_fg)

    def _on_leave(self, e):
        if self._state == "normal":
            self.configure(bg=self._bg, fg=self._fg)

    def _on_click(self, e):
        if self._state == "normal" and self.command:
            self.command()

    def configure(self, **kwargs):
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "state" in kwargs:
            st = kwargs.pop("state")
            self._state = st
            if st == "disabled":
                super().configure(bg=ACCENT_BTN_DISABLED_BG, fg=ACCENT_BTN_DISABLED_FG, cursor="arrow")
            else:
                super().configure(bg=self._bg, fg=self._fg, cursor="hand2")
        if kwargs:
            super().configure(**kwargs)

    def config(self, **kwargs):
        self.configure(**kwargs)

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "command":
            return self.command
        return super().cget(key)

    def __getitem__(self, key):
        if key == "state":
            return self._state
        if key == "command":
            return self.command
        return super().__getitem__(key)


class ModernEntry(tk.Entry):
    """High-performance modern entry with crisp 1px border and native paint speed."""
    def __init__(self, parent, textvariable=None, font=FONT_ENTRY, **kwargs):
        super().__init__(
            parent,
            textvariable=textvariable,
            font=font,
            bg=ENTRY_BG,
            fg=ENTRY_FG,
            disabledbackground=ENTRY_DISABLED_BG,
            disabledforeground=ENTRY_DISABLED_FG,
            readonlybackground=ENTRY_DISABLED_BG,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=ENTRY_BORDER,
            highlightcolor=ENTRY_FOCUS_BORDER,
            insertbackground=ENTRY_FG,
            selectbackground=ENTRY_SELECT_BG,
            selectforeground=ENTRY_SELECT_FG,
            **kwargs
        )


class ModernCard(tk.LabelFrame):
    """High-performance modern card container with centralized background."""
    def __init__(self, parent, text="", font=FONT_CARD_TITLE, padx=PAD_CARD_X, pady=PAD_CARD_Y, **kwargs):
        super().__init__(
            parent,
            text=f" {text.strip()} " if text.strip() else "",
            font=font,
            fg=TEXT_FG,
            bg=CARD_BG,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            highlightcolor=CARD_BORDER,
            padx=padx,
            pady=pady,
            **kwargs
        )


class ModernGreenProgressBar(tk.Canvas):
    """Thick, responsive modern green progress bar fallback."""
    def __init__(
        self, parent, height=18, bg=ENTRY_BG, bar_color="#10B981",
        border_color=BORDER_COLOR, **kwargs
    ):
        super().__init__(
            parent, height=height, bg=bg, highlightthickness=1,
            highlightbackground=border_color, **kwargs
        )
        self.bar_color = bar_color
        self.bg_color = bg
        self._value = 0.0
        self._max = 100.0
        self.bind("<Configure>", lambda e: self._draw())

    def configure(self, **kwargs):
        if "value" in kwargs:
            self._value = max(0.0, min(self._max, float(kwargs.pop("value"))))
            self._draw()
        if "maximum" in kwargs:
            self._max = float(kwargs.pop("maximum"))
            self._draw()
        if kwargs:
            super().configure(**kwargs)

    def config(self, **kwargs):
        self.configure(**kwargs)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        fraction = self._value / self._max if self._max > 0 else 0
        bar_w = int(w * fraction)
        if bar_w > 0:
            self.create_rectangle(0, 0, bar_w, h, fill=self.bar_color, width=0)
