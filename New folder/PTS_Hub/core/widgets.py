"""
Shared custom UI widgets for PTS Hub.
"""
import tkinter as tk


class ModernHoverButton(tk.Frame):
    """Modern flat button with light-blue hover fill and darker border."""
    def __init__(
        self, parent, text="", command=None, font=("Segoe UI", 9, "bold"),
        padx=10, pady=5, bg="#F8FAFC", border_color="#CBD5E1",
        hover_bg="#E0F2FE", hover_border="#7DD3FC", hover_fg="#0369A1",
        **kwargs
    ):
        super().__init__(parent, bg=border_color, padx=1, pady=1)
        self.command = command
        self._state = "normal"
        self._bg = bg
        self._border = border_color
        self._hover_bg = hover_bg
        self._hover_border = hover_border
        self._hover_fg = hover_fg
        self._default_fg = "#1E293B"

        self.inner = tk.Label(
            self, text=text, font=font, bg=bg, fg=self._default_fg,
            padx=padx, pady=pady, cursor="hand2"
        )
        self.inner.pack(fill="both", expand=True)

        for w in (self, self.inner):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<Button-1>", self._on_click)

    def _on_enter(self, e):
        if self._state == "normal":
            super().configure(bg=self._hover_border)
            self.inner.configure(bg=self._hover_bg, fg=self._hover_fg)

    def _on_leave(self, e):
        if self._state == "normal":
            super().configure(bg=self._border)
            self.inner.configure(bg=self._bg, fg=self._default_fg)

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
                super().configure(bg="#E2E8F0")
                self.inner.configure(bg="#F1F5F9", fg="#94A3B8", cursor="arrow")
            else:
                super().configure(bg=self._border)
                self.inner.configure(bg=self._bg, fg=self._default_fg, cursor="hand2")
        if "text" in kwargs:
            self.inner.configure(text=kwargs.pop("text"))
        if kwargs:
            super().configure(**kwargs)

    def config(self, **kwargs):
        self.configure(**kwargs)

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "command":
            return self.command
        if key == "text":
            return self.inner.cget("text")
        return super().cget(key)

    def __getitem__(self, key):
        if key == "state":
            return self._state
        if key == "command":
            return self.command
        if key == "text":
            return self.inner.cget("text")
        return super().__getitem__(key)


class ModernGreenProgressBar(tk.Canvas):
    """Thick, responsive modern green progress bar fallback."""
    def __init__(
        self, parent, height=18, bg="#E2E8F0", bar_color="#10B981",
        border_color="#CBD5E1", **kwargs
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
