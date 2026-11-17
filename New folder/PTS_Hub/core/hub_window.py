"""
Hub window shell, top navigation bar, and tab dispatcher for PTS Hub.
"""
import logging
import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional

from core.config import APP_NAME, APP_VERSION
from core.theme import (
    NAV_BG, NAV_BORDER, NAV_TAB_BG, NAV_TAB_BORDER, NAV_TAB_HOVER, NAV_TAB_HOVER_BORDER,
    NAV_TAB_HOVER_FG, NAV_TAB_ACTIVE, NAV_TAB_ACTIVE_BORDER, NAV_TEXT, NAV_TEXT_ACTIVE,
    NAV_TITLE_FG, NAV_BADGE_BG, NAV_BADGE_FG, NAV_BADGE_BORDER,
    MAIN_BG, BADGE_EMPTY_BG, BADGE_EMPTY_FG, TEXT_FG, TEXT_MUTED
)


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
