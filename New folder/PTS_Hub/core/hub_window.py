"""
Hub window shell, top navigation bar, and tab dispatcher for PTS Hub.
"""
import logging
import os
import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional

from core.config import (
    APP_NAME, APP_VERSION, ICON_PATH,
    APP_MODE_PRODUCTION, APP_MODE_DEVELOPER,
    get_app_mode, set_app_mode as _set_config_app_mode, is_developer_mode,
)
from core.theme import (
    NAV_BG, NAV_BORDER, NAV_TAB_BG, NAV_TAB_BORDER, NAV_TAB_HOVER, NAV_TAB_HOVER_BORDER,
    NAV_TAB_HOVER_FG, NAV_TAB_ACTIVE, NAV_TAB_ACTIVE_BORDER, NAV_TEXT, NAV_TEXT_ACTIVE,
    NAV_TITLE_FG, NAV_BADGE_BG, NAV_BADGE_FG, NAV_BADGE_BORDER,
    MAIN_BG, BADGE_EMPTY_BG, BADGE_EMPTY_FG, TEXT_FG, TEXT_MUTED, CARD_BORDER, ACCENT_BTN_BG
)
from core.widgets import ModernHoverButton


NAV_ICON_BITMAPS: Dict[str, list] = {
    "Home": [
        ".......##.......",
        "......####......",
        ".....######.....",
        "....########....",
        "...##########...",
        "..############..",
        ".##############.",
        "...##########...",
        "...##########...",
        "...###....###...",
        "...###....###...",
        "...###....###...",
        "...###....###...",
        "...###....###...",
        "................",
        "................",
    ],
    "FTDC Checker": [
        "................",
        ".##############.",
        ".#............#.",
        ".#............#.",
        ".##############.",
        ".#.........####.",
        ".#.........####.",
        ".#.....###.####.",
        ".#.....###.####.",
        ".#.....###.####.",
        ".#.###.###.####.",
        ".#.###.###.####.",
        ".#.###.###.####.",
        ".#............#.",
        ".##############.",
        "................",
    ],
    "STDF2SUM": [
        "......####......",
        "....##....##....",
        "...#........#...",
        "..#..##..##..#..",
        ".#...##..##...#.",
        ".#............#.",
        "#....##..##....#",
        "#....##..##....#",
        "#..............#",
        "#....##..##....#",
        ".#...##..##...#.",
        ".#............#.",
        "..#..##..##..#..",
        "...#........#...",
        "....##....##....",
        "......####......",
    ],
    "Data Analysis": [
        "................",
        "................",
        ".#...........##.",
        ".#..........####",
        ".#.........##.#.",
        ".#........##....",
        ".#...##..##.....",
        ".#..######......",
        ".#..#.###.......",
        ".#.#...#........",
        ".#.##...........",
        ".####...........",
        ".##.............",
        ".#..............",
        ".##############.",
        "................",
    ],
    "Settings": [
        "......####......",
        "......####......",
        "..##.######.##..",
        ".####......####.",
        ".###..####..###.",
        "..#..##..##..#..",
        "###..#....#..###",
        "###..#....#..###",
        "###..#....#..###",
        "###..#....#..###",
        "..#..##..##..#..",
        ".###..####..###.",
        ".####......####.",
        "..##.######.##..",
        "......####......",
        "......####......",
    ],
}


def create_nav_icon(root: tk.Misc, name: str, color: str = "#FFFFFF") -> Optional[tk.PhotoImage]:
    """Create a crisp 16x16 PhotoImage icon for the navigation bar by tab name."""
    bitmap = NAV_ICON_BITMAPS.get(name)
    if not bitmap:
        return None
    try:
        img = tk.PhotoImage(master=root, width=16, height=16)
        for y, row in enumerate(bitmap):
            for x, ch in enumerate(row):
                if ch == "#":
                    img.put(color, (x, y))
        return img
    except Exception as exc:
        logging.debug("Could not create nav icon for '%s': %s", name, exc)
        return None


def _create_home_icon(root: tk.Misc, color: str = "#FFFFFF") -> tk.PhotoImage:
    """Create a crisp 16x16 house icon PhotoImage for the navigation bar."""
    icon = create_nav_icon(root, "Home", color)
    if icon is not None:
        return icon
    return tk.PhotoImage(master=root, width=16, height=16)


class HomeFrame(ttk.Frame):
    """Welcome / Home dashboard screen for PTS Hub with interactive tool launcher cards."""

    BLUE_DARK = "#1E3A8A"      # Deep Navy Blue outline (Tailwind blue-900)
    BLUE_PRIMARY = "#2563EB"   # Vibrant Royal Blue (Tailwind blue-600)
    BLUE_LIGHT = "#38BDF8"     # Crisp Sky Blue (Tailwind sky-400)
    NAVY = BLUE_DARK
    ORANGE = BLUE_PRIMARY
    CARD_SURFACE = "#FFFFFF"
    CARD_BORDER_IDLE = "#E2E8F0"
    CARD_BORDER_HOVER = "#3B82F6"
    CARD_BG_HOVER = "#F8FAFC"

    def __init__(self, parent: tk.Misc, hub: Optional["HubWindow"] = None):
        super().__init__(parent)
        self.hub = hub
        self.cards: Dict[str, tk.Frame] = {}
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Header Section ───────────────────────────────────────────────────
        header = tk.Frame(self, bg=MAIN_BG)
        header.grid(row=0, column=0, sticky="ew", padx=44, pady=(36, 18))

        tk.Label(
            header,
            text=f"Welcome to {APP_NAME}",
            font=("Segoe UI", 20, "bold"),
            bg=MAIN_BG,
            fg=TEXT_FG,
            anchor="w",
        ).pack(side="top", anchor="w")

        tk.Label(
            header,
            text="Unified engineering hub for automated FTDC verification, STDF summary conversion, and test data analysis.",
            font=("Segoe UI", 10),
            bg=MAIN_BG,
            fg="#64748B",
            anchor="w",
        ).pack(side="top", anchor="w", pady=(6, 0))

        tk.Label(
            header,
            text="Pick a view below to get started.",
            font=("Segoe UI", 10),
            bg=MAIN_BG,
            fg="#64748B",
            anchor="w",
        ).pack(side="top", anchor="w", pady=(10, 0))

        # ── Cards Grid (3-Column Responsive Layout) ──────────────────────────
        grid_wrap = tk.Frame(self, bg=MAIN_BG)
        grid_wrap.grid(row=1, column=0, sticky="nsew", padx=36, pady=(4, 16))
        for c in range(3):
            grid_wrap.columnconfigure(c, weight=1, uniform="home_cards")

        card_specs = [
            {
                "tab": "FTDC Checker",
                "title": "FTDC Checker",
                "desc": "Automated STDF vs. MES good quantity verification, WXY duplicate GUID check, and QC sampling analysis.",
                "icon_fn": self._draw_ftdc_icon,
            },
            {
                "tab": "STDF2SUM",
                "title": "STDF2SUM",
                "desc": "Batch-convert First Pass, Retest, and QC STDF datalogs into standardized summary reports.",
                "icon_fn": self._draw_stdf2sum_icon,
            },
            {
                "tab": "Data Analysis",
                "title": "Data Analysis",
                "desc": "Interactive parametric distributions, yield trend analytics, capability (Cpk), and outlier detection.",
                "icon_fn": self._draw_data_analysis_icon,
            },
        ]

        for idx, spec in enumerate(card_specs):
            r, c = divmod(idx, 3)
            card = self._create_launcher_card(
                grid_wrap,
                tab_name=spec["tab"],
                title=spec["title"],
                desc=spec["desc"],
                icon_fn=spec["icon_fn"],
            )
            card.grid(row=r, column=c, sticky="nsew", padx=8, pady=8)
            self.cards[spec["tab"]] = card

        # ── Bottom-Right Watermark ───────────────────────────────────────────
        footer = tk.Frame(self, bg=MAIN_BG)
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        tk.Label(
            footer,
            text=f"{APP_NAME} {APP_VERSION}",
            font=("Segoe UI", 9),
            bg=MAIN_BG,
            fg="#94A3B8",
        ).pack(side="right")

    def _create_launcher_card(
        self,
        parent: tk.Misc,
        tab_name: str,
        title: str,
        desc: str,
        icon_fn,
    ) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=self.CARD_SURFACE,
            padx=20,
            pady=20,
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=self.CARD_BORDER_IDLE,
            highlightcolor=self.CARD_BORDER_IDLE,
        )
        card.columnconfigure(1, weight=1)

        # Vector Icon Canvas (left)
        icon_canvas = tk.Canvas(
            card,
            width=58,
            height=58,
            bg=self.CARD_SURFACE,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        icon_canvas.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 18), pady=2)
        icon_fn(icon_canvas)

        # Text Column (right)
        title_lbl = tk.Label(
            card,
            text=title,
            font=("Segoe UI", 11, "bold"),
            bg=self.CARD_SURFACE,
            fg=TEXT_FG,
            anchor="w",
            justify="left",
            cursor="hand2",
        )
        title_lbl.grid(row=0, column=1, sticky="ew", pady=(2, 4))

        desc_lbl = tk.Label(
            card,
            text=desc,
            font=("Segoe UI", 9),
            bg=self.CARD_SURFACE,
            fg="#64748B",
            anchor="nw",
            justify="left",
            wraplength=250,
            cursor="hand2",
        )
        desc_lbl.grid(row=1, column=1, sticky="nsew")

        all_widgets = (card, icon_canvas, title_lbl, desc_lbl)

        def _set_hover(active: bool):
            bg_c = self.CARD_BG_HOVER if active else self.CARD_SURFACE
            bd_c = self.CARD_BORDER_HOVER if active else self.CARD_BORDER_IDLE
            card.configure(bg=bg_c, highlightbackground=bd_c, highlightcolor=bd_c)
            icon_canvas.configure(bg=bg_c)
            title_lbl.configure(bg=bg_c)
            desc_lbl.configure(bg=bg_c)

        def _on_click(_event=None):
            if self.hub is not None:
                self.hub.switch_tab(tab_name)

        for w in all_widgets:
            w.bind("<Enter>", lambda _e: _set_hover(True))
            w.bind("<Leave>", lambda _e: _set_hover(False))
            w.bind("<Button-1>", _on_click)

        return card

    @classmethod
    def _draw_ftdc_icon(cls, cv: tk.Canvas):
        """Draw the blue-themed dashboard & bar chart icon."""
        # Outer window frame
        cv.create_rectangle(4, 6, 54, 52, outline=cls.BLUE_DARK, width=2.5)
        # Header divider line
        cv.create_line(4, 18, 54, 18, fill=cls.BLUE_DARK, width=2.0)
        # Window header dots
        cv.create_oval(10, 10, 14, 14, fill=cls.BLUE_LIGHT, outline=cls.BLUE_LIGHT)
        cv.create_oval(18, 10, 22, 14, fill=cls.BLUE_LIGHT, outline=cls.BLUE_LIGHT)
        # Vertical bar chart columns inside window
        cv.create_line(16, 44, 16, 30, fill=cls.BLUE_PRIMARY, width=4.0)
        cv.create_line(29, 44, 29, 24, fill=cls.BLUE_PRIMARY, width=4.0)
        cv.create_line(42, 44, 42, 34, fill=cls.BLUE_PRIMARY, width=4.0)

    @classmethod
    def _draw_stdf2sum_icon(cls, cv: tk.Canvas):
        """Draw the blue-themed wafer/die summary grid icon."""
        # Outer wafer circle
        cv.create_oval(5, 5, 53, 53, outline=cls.BLUE_DARK, width=2.5)
        # 3x3 blue die matrix inside wafer
        for r in range(3):
            for c in range(3):
                x0 = 19 + c * 8
                y0 = 19 + r * 8
                cv.create_rectangle(x0, y0, x0 + 5, y0 + 5, fill=cls.BLUE_PRIMARY, outline=cls.BLUE_DARK)

    @classmethod
    def _draw_data_analysis_icon(cls, cv: tk.Canvas):
        """Draw the blue-themed trend & axes analytics icon."""
        # L-shaped X/Y axes
        cv.create_line(8, 8, 8, 50, 52, 50, fill=cls.BLUE_DARK, width=2.5)
        # Rising multi-point blue trend polyline
        pts = [14, 42, 24, 30, 33, 36, 48, 14]
        cv.create_line(*pts, fill=cls.BLUE_PRIMARY, width=3.0)
        for i in range(0, len(pts), 2):
            px, py = pts[i], pts[i + 1]
            cv.create_oval(px - 3, py - 3, px + 3, py + 3, fill=cls.BLUE_LIGHT, outline=cls.BLUE_PRIMARY)


class HubWindow:
    """Main desktop shell hosting modular apps in tabs under a unified top navigation bar."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self._active_tab: Optional[str] = None
        self._nav_items: Dict[str, tk.Label] = {}
        self._app_frames: Dict[str, ttk.Frame] = {}

        self._tab_original_text: Dict[str, str] = {}
        self._tab_states: Dict[str, str] = {}          # "idle", "busy", "alert"
        self._tab_blink_jobs: Dict[str, Optional[str]] = {}
        self._tab_blink_phase: Dict[str, bool] = {}

        self._settings_dialog: Optional[tk.Toplevel] = None
        self._settings_dialog_refresh = None
        self._app_mode: str = get_app_mode()

        self._nav_icons_active: Dict[str, tk.PhotoImage] = {}
        self._nav_icons_inactive: Dict[str, tk.PhotoImage] = {}
        self._init_nav_icons()
        self._home_icon_active = self._nav_icons_active.get("Home")
        self._home_icon_inactive = self._nav_icons_inactive.get("Home")

        self._build_ui()

        # Register the built-in Home page as the leftmost navigation item and default screen
        self.home_frame = HomeFrame(self.content_area, hub=self)
        self.register_app("Home", self.home_frame, is_default=True)

    def _init_nav_icons(self):
        """Pre-render 16x16 PhotoImage navigation icons for all registered apps in both active and inactive states."""
        for name in NAV_ICON_BITMAPS:
            try:
                act = create_nav_icon(self.root, name, "#FFFFFF")
                inact = create_nav_icon(self.root, name, NAV_TEXT)
                if act is not None:
                    self._nav_icons_active[name] = act
                if inact is not None:
                    self._nav_icons_inactive[name] = inact
            except Exception as exc:
                logging.debug("Could not create nav icon for '%s': %s", name, exc)

    def _get_nav_icon(self, name: str, active: bool = True) -> Optional[tk.PhotoImage]:
        """Get or lazily create a navigation icon for the specified tab name."""
        cache = self._nav_icons_active if active else self._nav_icons_inactive
        if name in cache:
            return cache[name]
        color = "#FFFFFF" if active else NAV_TEXT
        icon = create_nav_icon(self.root, name, color)
        if icon is not None:
            cache[name] = icon
        return icon

    def _build_ui(self):
        # Master container
        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True)

        # Top Navigation Bar
        self._build_top_navbar(container)

        # Application Content Area (Holds persistently gridded app frames)
        self.content_area = ttk.Frame(container)
        self.content_area.pack(fill="both", expand=True)
        self.content_area.rowconfigure(0, weight=1)
        self.content_area.columnconfigure(0, weight=1)

    def _build_top_navbar(self, parent: ttk.Frame):
        nav = tk.Frame(parent, bg=NAV_BG, height=52)
        nav.pack(side="top", fill="x")
        nav.pack_propagate(False)

        # Bottom 1px divider border
        tk.Frame(nav, bg=NAV_BORDER, height=1).pack(side="bottom", fill="x")

        # Left Branding Block
        brand_frame = tk.Frame(nav, bg=NAV_BG)
        brand_frame.pack(side="left", padx=(16, 12), fill="y")

        # Brand Title
        tk.Label(
            brand_frame,
            text=APP_NAME,
            font=("Segoe UI", 12, "bold"),
            bg=NAV_BG,
            fg=NAV_TITLE_FG,
        ).pack(side="left", pady=14)

        # Version Badge
        v_badge = tk.Label(
            brand_frame,
            text=f"{APP_VERSION}",
            font=("Segoe UI", 8, "bold"),
            bg=NAV_BADGE_BG,
            fg=NAV_BADGE_FG,
            padx=6,
            pady=2,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=NAV_BADGE_BORDER,
            highlightcolor=NAV_BADGE_BORDER,
        )
        v_badge.pack(side="left", padx=(8, 12), pady=16)

        # Vertical Divider
        tk.Frame(brand_frame, bg=NAV_BORDER, width=1, height=22).pack(
            side="left", padx=(0, 8), pady=15
        )

        # Tabs Container
        self.tabs_frame = tk.Frame(nav, bg=NAV_BG)
        self.tabs_frame.pack(side="left", fill="y", padx=4)

        # Right-side Action Container (Developer Mode badge + Settings button)
        self.nav_right_frame = tk.Frame(nav, bg=NAV_BG)
        self.nav_right_frame.pack(side="right", fill="y", padx=(8, 14))

        set_act_icon = self._get_nav_icon("Settings", active=True)
        set_inact_icon = self._get_nav_icon("Settings", active=False)
        set_kwargs = {}
        if set_inact_icon is not None:
            set_kwargs["image"] = set_inact_icon
            set_kwargs["compound"] = "left"
            set_text = "  Settings"
        else:
            set_text = "⚙ Settings"

        self.settings_btn = tk.Label(
            self.nav_right_frame,
            text=set_text,
            font=("Segoe UI", 9, "bold"),
            bg=NAV_TAB_BG,
            fg=NAV_TEXT,
            padx=12,
            pady=6,
            cursor="hand2",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=NAV_TAB_BORDER,
            highlightcolor=NAV_TAB_BORDER,
            **set_kwargs,
        )
        self.settings_btn.pack(side="right", padx=(6, 0), pady=11)

        def _on_settings_enter(_e):
            cfg = dict(
                bg=NAV_TAB_HOVER,
                fg=NAV_TAB_HOVER_FG,
                highlightbackground=NAV_TAB_HOVER_BORDER,
                highlightcolor=NAV_TAB_HOVER_BORDER,
            )
            if set_act_icon is not None:
                cfg["image"] = set_act_icon
            self.settings_btn.configure(**cfg)

        def _on_settings_leave(_e):
            cfg = dict(
                bg=NAV_TAB_BG,
                fg=NAV_TEXT,
                highlightbackground=NAV_TAB_BORDER,
                highlightcolor=NAV_TAB_BORDER,
            )
            if set_inact_icon is not None:
                cfg["image"] = set_inact_icon
            self.settings_btn.configure(**cfg)

        self.settings_btn.bind("<Enter>", _on_settings_enter)
        self.settings_btn.bind("<Leave>", _on_settings_leave)
        self.settings_btn.bind("<Button-1>", lambda _e: self.open_settings_dialog())

        self.mode_badge = tk.Label(
            self.nav_right_frame,
            text="DEV MODE",
            font=("Segoe UI", 8, "bold"),
            bg="#D97706",
            fg="#FFFFFF",
            padx=8,
            pady=4,
            cursor="hand2",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#F59E0B",
            highlightcolor="#F59E0B",
        )
        self.mode_badge.bind("<Button-1>", lambda _e: self.open_settings_dialog())
        self._sync_mode_badge()

    def _sync_mode_badge(self):
        """Show the navbar DEV MODE indicator only while Developer Mode is active."""
        if getattr(self, "mode_badge", None) is None:
            return
        if is_developer_mode():
            if not self.mode_badge.winfo_manager():
                self.mode_badge.pack(side="right", padx=(0, 4), pady=13)
        else:
            if self.mode_badge.winfo_manager():
                self.mode_badge.pack_forget()

    def get_app_mode(self) -> str:
        """Return the active operating mode ('production' or 'developer')."""
        return get_app_mode()

    def set_app_mode(self, mode: str, persist: bool = True) -> str:
        """Update the active operating mode, refresh navbar/dialog UI, and notify registered apps."""
        normalized = _set_config_app_mode(mode, persist=persist)
        self._app_mode = normalized
        self._sync_mode_badge()
        if callable(self._settings_dialog_refresh):
            try:
                self._settings_dialog_refresh()
            except Exception as exc:
                logging.debug("Failed to refresh settings dialog: %s", exc)
        for name, frame in list(self._app_frames.items()):
            if hasattr(frame, "on_app_mode_changed"):
                try:
                    frame.on_app_mode_changed(normalized)
                except Exception as exc:
                    logging.warning("Error notifying '%s' of mode change: %s", name, exc)
        return normalized

    def toggle_app_mode(self, persist: bool = True) -> str:
        """Toggle between Production Mode and Developer Mode."""
        target = APP_MODE_PRODUCTION if is_developer_mode() else APP_MODE_DEVELOPER
        return self.set_app_mode(target, persist=persist)

    def open_settings_dialog(self) -> tk.Toplevel:
        """Open (or focus) the PTS Hub Settings dialog with Operating Mode toggle."""
        if self._settings_dialog is not None:
            try:
                if self._settings_dialog.winfo_exists():
                    if callable(self._settings_dialog_refresh):
                        self._settings_dialog_refresh()
                    self._settings_dialog.deiconify()
                    self._settings_dialog.lift()
                    self._settings_dialog.focus_set()
                    return self._settings_dialog
            except Exception as exc:
                logging.debug("Previous settings dialog handle invalid: %s", exc)
            self._settings_dialog = None
            self._settings_dialog_refresh = None

        win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} - Settings")
        win.configure(bg=MAIN_BG)
        win.transient(self.root)
        win.resizable(False, False)
        try:
            if os.path.isfile(ICON_PATH):
                win.iconbitmap(ICON_PATH)
        except Exception as exc:
            logging.debug("Could not set settings dialog icon: %s", exc)

        body = tk.Frame(win, bg=MAIN_BG, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        # Header
        tk.Label(
            body,
            text="Settings",
            font=("Segoe UI", 14, "bold"),
            bg=MAIN_BG,
            fg=TEXT_FG,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            body,
            text="Configure PTS Hub operating mode and preferences.",
            font=("Segoe UI", 9),
            bg=MAIN_BG,
            fg=TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", pady=(2, 12))

        # Operating Mode Card
        card = tk.Frame(
            body,
            bg="#FFFFFF",
            padx=16,
            pady=14,
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            highlightcolor=CARD_BORDER,
        )
        card.pack(fill="x")

        top_row = tk.Frame(card, bg="#FFFFFF")
        top_row.pack(fill="x")

        title_col = tk.Frame(top_row, bg="#FFFFFF")
        title_col.pack(side="left", fill="x", expand=True)

        tk.Label(
            title_col,
            text="Operating Mode",
            font=("Segoe UI", 10, "bold"),
            bg="#FFFFFF",
            fg=TEXT_FG,
            anchor="w",
        ).pack(anchor="w")

        mode_status_label = tk.Label(
            title_col,
            text="",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=2,
            relief="flat",
            bd=0,
        )
        mode_status_label.pack(anchor="w", pady=(4, 0))

        switch_row = tk.Frame(top_row, bg="#FFFFFF")
        switch_row.pack(side="right", padx=(16, 0))

        prod_lbl = tk.Label(
            switch_row,
            text="Production",
            font=("Segoe UI", 9, "bold"),
            bg="#FFFFFF",
            fg=TEXT_FG,
            cursor="hand2",
        )
        prod_lbl.pack(side="left", padx=(0, 8))

        toggle_switch = tk.Canvas(
            switch_row,
            width=54,
            height=28,
            bg="#FFFFFF",
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        toggle_switch.pack(side="left")

        dev_lbl = tk.Label(
            switch_row,
            text="Developer",
            font=("Segoe UI", 9),
            bg="#FFFFFF",
            fg=TEXT_MUTED,
            cursor="hand2",
        )
        dev_lbl.pack(side="left", padx=(8, 0))

        tk.Frame(card, bg="#E2E8F0", height=1).pack(fill="x", pady=(12, 10))

        tk.Label(
            card,
            text="Auto-Attach to PPO Behavior",
            font=("Segoe UI", 8, "bold"),
            bg="#FFFFFF",
            fg=TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        mode_desc_label = tk.Label(
            card,
            text="",
            font=("Segoe UI", 9),
            bg="#FFFFFF",
            fg=TEXT_FG,
            wraplength=430,
            justify="left",
            anchor="w",
        )
        mode_desc_label.pack(fill="x")

        def _draw_switch(dev_active: bool):
            toggle_switch.delete("all")
            track_color = "#D97706" if dev_active else ACCENT_BTN_BG
            toggle_switch.create_oval(2, 2, 26, 26, fill=track_color, outline="")
            toggle_switch.create_oval(28, 2, 52, 26, fill=track_color, outline="")
            toggle_switch.create_rectangle(14, 2, 40, 26, fill=track_color, outline="")
            if dev_active:
                toggle_switch.create_oval(31, 5, 49, 23, fill="#FFFFFF", outline="")
            else:
                toggle_switch.create_oval(5, 5, 23, 23, fill="#FFFFFF", outline="")

        def _refresh_dialog():
            if not win.winfo_exists():
                return
            dev_active = is_developer_mode()
            _draw_switch(dev_active)
            if dev_active:
                mode_status_label.configure(
                    text="Developer Mode",
                    bg="#FEF3C7",
                    fg="#B45309",
                )
                prod_lbl.configure(font=("Segoe UI", 9), fg=TEXT_MUTED)
                dev_lbl.configure(font=("Segoe UI", 9, "bold"), fg="#B45309")
                mode_desc_label.configure(
                    text=(
                        "Developer Mode: Auto-attachment to PPO is disabled. "
                        "Once the Excel report is generated, it will remain local only "
                        "and will NOT be automatically attached to PPO folders for the lot."
                    )
                )
            else:
                mode_status_label.configure(
                    text="Production Mode",
                    bg="#DBEAFE",
                    fg="#1D4ED8",
                )
                prod_lbl.configure(font=("Segoe UI", 9, "bold"), fg="#1D4ED8")
                dev_lbl.configure(font=("Segoe UI", 9), fg=TEXT_MUTED)
                mode_desc_label.configure(
                    text=(
                        "Production Mode (Default): Runs normally. "
                        "Once the Excel report is generated, it will automatically attach "
                        "the Excel file and result image to all PPO folders for the lot."
                    )
                )

        def _on_toggle(_event=None):
            self.toggle_app_mode(persist=True)
            _refresh_dialog()

        def _select_production(_event=None):
            if is_developer_mode():
                self.set_app_mode(APP_MODE_PRODUCTION, persist=True)
                _refresh_dialog()

        def _select_developer(_event=None):
            if not is_developer_mode():
                self.set_app_mode(APP_MODE_DEVELOPER, persist=True)
                _refresh_dialog()

        toggle_switch.bind("<Button-1>", _on_toggle)
        prod_lbl.bind("<Button-1>", _select_production)
        dev_lbl.bind("<Button-1>", _select_developer)

        # Footer
        footer = tk.Frame(body, bg=MAIN_BG)
        footer.pack(fill="x", pady=(14, 0))

        def _close(_event=None):
            self._settings_dialog = None
            self._settings_dialog_refresh = None
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception as exc:
                logging.debug("Failed to destroy settings dialog: %s", exc)

        close_btn = ModernHoverButton(footer, text="Close", command=_close, padx=18, pady=5)
        close_btn.pack(side="right")

        win.toggle_switch = toggle_switch
        win.mode_status_label = mode_status_label
        win.mode_desc_label = mode_desc_label
        win.toggle_command = _on_toggle

        self._settings_dialog = win
        self._settings_dialog_refresh = _refresh_dialog
        win.protocol("WM_DELETE_WINDOW", _close)
        win.bind("<Escape>", _close)
        _refresh_dialog()

        try:
            win.update_idletasks()
            if self.root.winfo_viewable():
                rx = self.root.winfo_rootx()
                ry = self.root.winfo_rooty()
                rw = self.root.winfo_width()
                rh = self.root.winfo_height()
                ww = win.winfo_reqwidth()
                wh = win.winfo_reqheight()
                px = max(0, rx + (rw - ww) // 2)
                py = max(0, ry + (rh - wh) // 2)
                win.geometry(f"+{px}+{py}")
        except Exception as exc:
            logging.debug("Skipped centering settings dialog: %s", exc)

        return win

    def register_app(self, name: str, frame: ttk.Frame, is_default: bool = False):
        """Register an application frame into the hub."""
        self._app_frames[name] = frame
        self._tab_original_text[name] = name
        self._tab_states[name] = "idle"
        self._tab_blink_jobs[name] = None
        self._tab_blink_phase[name] = False

        # Persistent stack: every frame is gridded into (0, 0) once and remains alive in memory
        frame.grid(row=0, column=0, sticky="nsew")

        self._build_nav_tab(self.tabs_frame, name)
        if is_default or self._active_tab is None:
            self.switch_tab(name)

    def _build_nav_tab(self, parent: tk.Frame, name: str):
        is_first = (len(self._nav_items) == 0)
        active_icon = self._get_nav_icon(name, active=True)
        inactive_icon = self._get_nav_icon(name, active=False)

        # Pad text slightly when icon is present so icon and label breathe
        if active_icon is not None:
            lbl_text = f"  {name}"
        elif name == "Home":
            lbl_text = "⌂ Home"
        else:
            lbl_text = name

        self._tab_original_text[name] = lbl_text

        extra_kwargs = {}
        if active_icon is not None:
            extra_kwargs["image"] = active_icon if is_first else inactive_icon
            extra_kwargs["compound"] = "left"

        tab_btn = tk.Label(
            parent,
            text=lbl_text,
            font=("Segoe UI", 9, "bold"),
            bg=NAV_TAB_ACTIVE if is_first else NAV_TAB_BG,
            fg=NAV_TEXT_ACTIVE if is_first else NAV_TEXT,
            padx=14,
            pady=6,
            cursor="hand2",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=NAV_TAB_ACTIVE_BORDER if is_first else NAV_TAB_BORDER,
            highlightcolor=NAV_TAB_ACTIVE_BORDER if is_first else NAV_TAB_BORDER,
            **extra_kwargs,
        )
        tab_btn.pack(side="left", padx=3, pady=11)

        def on_enter(e):
            if self._active_tab != name and self._tab_states.get(name) != "alert":
                cfg = dict(
                    bg=NAV_TAB_HOVER, fg=NAV_TAB_HOVER_FG,
                    highlightbackground=NAV_TAB_HOVER_BORDER, highlightcolor=NAV_TAB_HOVER_BORDER
                )
                if active_icon is not None:
                    cfg["image"] = active_icon
                tab_btn.configure(**cfg)

        def on_leave(e):
            if self._active_tab != name and self._tab_states.get(name) != "alert":
                if self._tab_states.get(name) == "busy":
                    cfg = dict(
                        bg=NAV_TAB_BG, fg="#38BDF8",
                        highlightbackground=NAV_TAB_BORDER, highlightcolor=NAV_TAB_BORDER
                    )
                else:
                    cfg = dict(
                        bg=NAV_TAB_BG, fg=NAV_TEXT,
                        highlightbackground=NAV_TAB_BORDER, highlightcolor=NAV_TAB_BORDER
                    )
                if inactive_icon is not None:
                    cfg["image"] = inactive_icon
                tab_btn.configure(**cfg)

        def on_click(e):
            self.switch_tab(name)

        tab_btn.bind("<Enter>", on_enter)
        tab_btn.bind("<Leave>", on_leave)
        tab_btn.bind("<Button-1>", on_click)

        self._nav_items[name] = tab_btn

    def switch_tab(self, tab_name: str):
        """Switch currently visible application tab instantly with zero-latency lifting."""
        if tab_name not in self._app_frames:
            return

        # If switching to an alerting/blinking tab, stop the alert immediately
        if self._tab_states.get(tab_name) == "alert":
            self.stop_tab_alert(tab_name)

        target_frame = self._app_frames[tab_name]

        # Instantaneous Z-order raise: pure Tkinter tkraise() without geometry reflow
        target_frame.tkraise()
        try:
            target_frame.focus_set()
        except Exception as exc:
            logging.warning("Failed to set focus on tab '%s': %s", tab_name, exc)

        self._active_tab = tab_name
        self._update_all_nav_styles()

        if hasattr(target_frame, "on_tab_activated"):
            try:
                target_frame.on_tab_activated()
            except Exception as exc:
                logging.warning("Error in on_tab_activated for '%s': %s", tab_name, exc)

    def set_tab_busy(self, tab_name: str, is_busy: bool):
        """Update the busy/processing status of a tab without interrupting navigation."""
        if tab_name not in self._app_frames:
            return
        inact_icon = self._get_nav_icon(tab_name, active=False)
        if is_busy:
            self._tab_states[tab_name] = "busy"
            if self._active_tab != tab_name and self._tab_states.get(tab_name) != "alert":
                tab_btn = self._nav_items.get(tab_name)
                if tab_btn:
                    orig_name = self._tab_original_text.get(tab_name, tab_name)
                    cfg = dict(
                        fg="#38BDF8",
                        text=f"{orig_name} ⏳",
                    )
                    if inact_icon is not None:
                        cfg["image"] = inact_icon
                    tab_btn.configure(**cfg)
        else:
            if self._tab_states.get(tab_name) == "busy":
                self._tab_states[tab_name] = "idle"
            if self._active_tab != tab_name and self._tab_states.get(tab_name) != "alert":
                tab_btn = self._nav_items.get(tab_name)
                if tab_btn:
                    cfg = dict(
                        fg=NAV_TEXT,
                        text=self._tab_original_text[tab_name],
                    )
                    if inact_icon is not None:
                        cfg["image"] = inact_icon
                    tab_btn.configure(**cfg)

    def notify_tab_finished(self, tab_name: str, status_msg: str = ""):
        """Notify the hub that a background task on tab_name has completed."""
        if tab_name not in self._app_frames:
            return

        # If user is currently looking at this tab, no alert is needed
        if self._active_tab == tab_name:
            self._tab_states[tab_name] = "idle"
            self._update_all_nav_styles()
            return

        # User is on a different tab -> Trigger the blinking highlight!
        self.start_tab_alert(tab_name)

    def start_tab_alert(self, tab_name: str):
        """Start a high-visibility blinking highlight on a tab whose task finished in the background."""
        if tab_name not in self._nav_items:
            return

        # Don't alert if user is already on the tab
        if self._active_tab == tab_name:
            return

        # Cancel any previous timer job
        if self._tab_blink_jobs.get(tab_name):
            try:
                self.root.after_cancel(self._tab_blink_jobs[tab_name])
            except Exception as exc:
                logging.warning("Failed to cancel tab blink job for '%s': %s", tab_name, exc)
            self._tab_blink_jobs[tab_name] = None

        self._tab_states[tab_name] = "alert"
        self._tab_blink_phase[tab_name] = True
        self._blink_step(tab_name)

    def _blink_step(self, tab_name: str):
        """Execute one cycle of the blinking tab highlight."""
        if self._tab_states.get(tab_name) != "alert" or self._active_tab == tab_name:
            self.stop_tab_alert(tab_name)
            return

        tab_btn = self._nav_items.get(tab_name)
        if not tab_btn:
            return

        phase = self._tab_blink_phase.get(tab_name, True)
        orig_name = self._tab_original_text.get(tab_name, tab_name)
        act_icon = self._get_nav_icon(tab_name, active=True)
        inact_icon = self._get_nav_icon(tab_name, active=False)

        if phase:
            # Phase A: High-contrast alert (Vibrant emerald green background with crisp white text)
            cfg = dict(
                bg="#059669",
                fg="#FFFFFF",
                highlightbackground="#059669",
                font=("Segoe UI", 9, "bold"),
                text=f"{orig_name} ● Done",
            )
            if act_icon is not None:
                cfg["image"] = act_icon
            tab_btn.configure(**cfg)
        else:
            # Phase B: Resting button (dark blue button fill with emerald highlight)
            cfg = dict(
                bg=NAV_TAB_BG,
                fg="#34D399",
                highlightbackground=NAV_TAB_BORDER,
                font=("Segoe UI", 9, "bold"),
                text=f"{orig_name} ○ Done",
            )
            if inact_icon is not None:
                cfg["image"] = inact_icon
            tab_btn.configure(**cfg)

        self._tab_blink_phase[tab_name] = not phase

        # Schedule next blink cycle in 500ms
        self._tab_blink_jobs[tab_name] = self.root.after(
            500, lambda: self._blink_step(tab_name)
        )

    def stop_tab_alert(self, tab_name: str):
        """Cancel blinking and restore normal tab appearance."""
        if tab_name in self._tab_blink_jobs and self._tab_blink_jobs[tab_name]:
            try:
                self.root.after_cancel(self._tab_blink_jobs[tab_name])
            except Exception as exc:
                logging.warning("Failed to cancel tab alert job for '%s': %s", tab_name, exc)
            self._tab_blink_jobs[tab_name] = None

        self._tab_states[tab_name] = "idle"
        tab_btn = self._nav_items.get(tab_name)
        if tab_btn:
            tab_btn.configure(text=self._tab_original_text.get(tab_name, tab_name))
        self._update_all_nav_styles()

    def _update_all_nav_styles(self):
        """Apply the correct colors, fonts, and labels to every tab item."""
        for name, tab_btn in self._nav_items.items():
            orig_name = self._tab_original_text.get(name, name)
            act_icon = self._get_nav_icon(name, active=True)
            inact_icon = self._get_nav_icon(name, active=False)

            if name == self._active_tab:
                cfg = dict(
                    bg=NAV_TAB_ACTIVE,
                    fg=NAV_TEXT_ACTIVE,
                    highlightbackground=NAV_TAB_ACTIVE_BORDER,
                    font=("Segoe UI", 9, "bold"),
                    text=orig_name,
                )
                if act_icon is not None:
                    cfg["image"] = act_icon
                tab_btn.configure(**cfg)
            elif self._tab_states.get(name) == "alert":
                # Blinking routine controls colors; preserve alert state
                pass
            elif self._tab_states.get(name) == "busy":
                cfg = dict(
                    bg=NAV_TAB_BG,
                    fg="#38BDF8",
                    highlightbackground=NAV_TAB_BORDER,
                    font=("Segoe UI", 9, "bold"),
                    text=f"{orig_name} ⏳",
                )
                if inact_icon is not None:
                    cfg["image"] = inact_icon
                tab_btn.configure(**cfg)
            else:
                cfg = dict(
                    bg=NAV_TAB_BG,
                    fg=NAV_TEXT,
                    highlightbackground=NAV_TAB_BORDER,
                    font=("Segoe UI", 9, "bold"),
                    text=orig_name,
                )
                if inact_icon is not None:
                    cfg["image"] = inact_icon
                tab_btn.configure(**cfg)

    @staticmethod
    def create_placeholder_frame(
        parent: ttk.Frame, title: str, subtitle: str, icon: str = "🚀"
    ) -> ttk.Frame:
        """Create a standard placeholder view for apps currently in development."""
        container = ttk.Frame(parent)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        center_card = ttk.LabelFrame(container, text="", padding=30)
        center_card.grid(row=0, column=0)

        icon_lbl = tk.Label(center_card, text=icon, font=("Segoe UI", 36), bg=MAIN_BG)
        icon_lbl.pack(pady=(0, 10))

        ttk.Label(
            center_card,
            text=title,
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(0, 6))

        ttk.Label(
            center_card,
            text=subtitle,
            font=("Segoe UI", 10),
            wraplength=420,
            justify="center",
        ).pack(pady=(0, 16))

        badge = tk.Label(
            center_card,
            text="MODULE IN DEVELOPMENT • COMING SOON",
            font=("Segoe UI", 8, "bold"),
            bg=BADGE_EMPTY_BG,
            fg=BADGE_EMPTY_FG,
            padx=10,
            pady=4,
        )
        badge.pack()

        return container
