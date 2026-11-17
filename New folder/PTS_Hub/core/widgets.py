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
    """High-performance modern entry with crisp 1px border, native paint speed, and optional placeholder."""
    def __init__(self, parent, textvariable=None, font=FONT_ENTRY, placeholder: str = "", **kwargs):
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
        self._placeholder_text = str(placeholder or "")
        self._placeholder_lbl = None
        self._has_focus = False
        self._textvariable = textvariable
        if self._placeholder_text:
            self._init_placeholder(font)

    def _init_placeholder(self, font):
        is_disabled = str(self.cget("state")) in ("disabled", "readonly")
        self._placeholder_lbl = tk.Label(
            self,
            text=self._placeholder_text,
            font=font,
            bg=ENTRY_DISABLED_BG if is_disabled else ENTRY_BG,
            fg=TEXT_DISABLED,
            anchor="w",
            padx=2,
            pady=0,
            bd=0,
            highlightthickness=0,
            cursor="arrow" if is_disabled else "xterm",
        )
        self._placeholder_lbl.bind("<Button-1>", self._on_placeholder_click)
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<KeyRelease>", self._sync_placeholder, add="+")
        if self._textvariable is not None:
            try:
                self._textvariable.trace_add("write", lambda *_a: self._sync_placeholder())
            except Exception:
                pass
        self._sync_placeholder()

    def _on_placeholder_click(self, _e=None):
        try:
            if str(self.cget("state")) not in ("disabled", "readonly"):
                self.focus_set()
                self.icursor(0)
        except Exception:
            pass

    def _on_focus_in(self, _e=None):
        self._has_focus = True
        self._sync_placeholder()

    def _on_focus_out(self, _e=None):
        self._has_focus = False
        self._sync_placeholder()

    def _sync_placeholder(self, _e=None):
        if self._placeholder_lbl is None:
            return
        try:
            val = self._textvariable.get() if self._textvariable is not None else self.get()
            state = str(self.cget("state"))
            is_disabled = state in ("disabled", "readonly")
            self._placeholder_lbl.configure(
                bg=ENTRY_DISABLED_BG if is_disabled else ENTRY_BG,
                cursor="arrow" if is_disabled else "xterm",
            )
            if val or (self._has_focus and not is_disabled):
                self._placeholder_lbl.place_forget()
            else:
                self._placeholder_lbl.place(x=2, y=1, relwidth=1.0, width=-4, relheight=1.0, height=-2)
        except Exception:
            pass

    def configure(self, cnf=None, **kwargs):
        res = super().configure(cnf, **kwargs) if cnf is not None else super().configure(**kwargs)
        if self._placeholder_lbl is not None and ("state" in kwargs or (isinstance(cnf, dict) and "state" in cnf)):
            self._sync_placeholder()
        return res

    config = configure


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



class InfoTooltipButton(tk.Label):
    """Small circular 'i' info icon that shows a tooltip bubble.

    - Hover: shows the tooltip; leaving the icon hides it (unless pinned).
    - Click: pins / unpins the tooltip (so it can be read without holding the mouse).
    - The tooltip also closes on <Escape> or when the icon is destroyed.
    """
    ICON_TEXT = "\u24D8"  # ⓘ circled latin small letter i
    ICON_FG = "#2563EB"
    ICON_HOVER_FG = "#1D4ED8"
    TIP_BG = "#FFFFE1"
    TIP_FG = "#1E293B"
    TIP_BORDER = "#94A3B8"
    TIP_FONT = ("Segoe UI", 8)

    def __init__(self, parent, text: str, bg=None, wraplength: int = 340, font=("Segoe UI", 9, "bold"), **kwargs):
        if bg is None:
            try:
                bg = parent.cget("bg")
            except Exception:
                bg = CARD_BG
        super().__init__(
            parent, text=self.ICON_TEXT, font=font, fg=self.ICON_FG, bg=bg,
            cursor="hand2", padx=1, pady=0, bd=0, highlightthickness=0, **kwargs
        )
        self.tooltip_text = text
        self._wraplength = wraplength
        self._tip = None
        self._pinned = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Destroy>", lambda _e: self._hide(force=True), add="+")

    def _on_enter(self, _e=None):
        self.configure(fg=self.ICON_HOVER_FG)
        self._show()

    def _on_leave(self, _e=None):
        self.configure(fg=self.ICON_FG)
        if not self._pinned:
            self._hide()

    def _on_click(self, _e=None):
        if self._pinned:
            self._pinned = False
            self._hide(force=True)
        else:
            self._pinned = True
            self._show()

    def _show(self):
        if self._tip is not None:
            return
        try:
            tip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except tk.TclError:
                pass
            frame = tk.Frame(tip, bg=self.TIP_BORDER, bd=0)
            frame.pack()
            tk.Label(
                frame, text=self.tooltip_text, justify="left", anchor="w",
                bg=self.TIP_BG, fg=self.TIP_FG, font=self.TIP_FONT,
                wraplength=self._wraplength, padx=7, pady=4,
            ).pack(padx=1, pady=1)
            tip.update_idletasks()
            x = self.winfo_rootx() + self.winfo_width() + 4
            y = self.winfo_rooty() + self.winfo_height() + 2
            sw = self.winfo_screenwidth()
            tw = tip.winfo_reqwidth()
            if x + tw > sw - 4:
                x = max(4, self.winfo_rootx() - tw - 4)
            tip.wm_geometry(f"+{x}+{y}")
            tip.bind("<Button-1>", lambda _e: self._on_click())
            tip.bind("<Escape>", lambda _e: self._hide(force=True))
            self._tip = tip
        except tk.TclError:
            self._tip = None

    def _hide(self, force: bool = False):
        if force:
            self._pinned = False
        tip, self._tip = self._tip, None
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass


class WidgetTooltip:
    """Hover tooltip attached to any widget (such as action buttons or Help button)."""
    TIP_BG = InfoTooltipButton.TIP_BG
    TIP_FG = InfoTooltipButton.TIP_FG
    TIP_BORDER = InfoTooltipButton.TIP_BORDER
    TIP_FONT = InfoTooltipButton.TIP_FONT

    def __init__(self, widget, text: str, wraplength: int = 320):
        self.widget = widget
        self.tooltip_text = text
        self._wraplength = wraplength
        self._tip = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress-1>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_leave, add="+")

    def _on_enter(self, _e=None):
        self._show()

    def _on_leave(self, _e=None):
        self._hide()

    def _show(self):
        if self._tip is not None or not self.tooltip_text:
            return
        try:
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except tk.TclError:
                pass
            frame = tk.Frame(tip, bg=self.TIP_BORDER, bd=0)
            frame.pack()
            tk.Label(
                frame, text=self.tooltip_text, justify="left", anchor="w",
                bg=self.TIP_BG, fg=self.TIP_FG, font=self.TIP_FONT,
                wraplength=self._wraplength, padx=7, pady=4,
            ).pack(padx=1, pady=1)
            tip.update_idletasks()
            x = self.widget.winfo_rootx() + 4
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            sw = self.widget.winfo_screenwidth()
            sh = self.widget.winfo_screenheight()
            tw = tip.winfo_reqwidth()
            th = tip.winfo_reqheight()
            if x + tw > sw - 4:
                x = max(4, sw - tw - 4)
            if y + th > sh - 8:
                y = max(4, self.widget.winfo_rooty() - th - 4)
            tip.wm_geometry(f"+{x}+{y}")
            self._tip = tip
        except tk.TclError:
            self._tip = None

    def _hide(self):
        tip, self._tip = self._tip, None
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass


def attach_tooltip(widget, text: str, wraplength: int = 320) -> WidgetTooltip:
    """Attach a hover WidgetTooltip to *widget* and store a reference on widget._tooltip."""
    tip = WidgetTooltip(widget, text=text, wraplength=wraplength)
    try:
        widget._tooltip = tip
        widget.tooltip_text = text
    except Exception:
        pass
    return tip
