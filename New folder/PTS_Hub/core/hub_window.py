"""
Hub window shell, top navigation bar, and tab dispatcher for PTS Hub.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from core.config import APP_NAME, APP_VERSION
from core.theme import (
    NAV_BG, NAV_BORDER, NAV_TAB_BG, NAV_TAB_BORDER, NAV_TAB_HOVER, NAV_TAB_HOVER_BORDER,
    NAV_TAB_HOVER_FG, NAV_TAB_ACTIVE, NAV_TAB_ACTIVE_BORDER, NAV_TEXT, NAV_TEXT_ACTIVE,
    NAV_TITLE_FG, NAV_BADGE_BG, NAV_BADGE_FG, NAV_BADGE_BORDER, STATUS_ONLINE,
    MAIN_BG, CARD_BG, BORDER_COLOR, TEXT_FG,
    BTN_BG, BTN_FG, BTN_BORDER, BTN_HOVER_BG, BTN_HOVER_BORDER, BTN_HOVER_FG,
    BADGE_EMPTY_BG, BADGE_EMPTY_FG
)


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

        self._build_ui()

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

        # Right Side Status Indicator
        status_frame = tk.Frame(nav, bg=NAV_BG)
        status_frame.pack(side="right", padx=(0, 16), fill="y")

        tk.Label(
            status_frame,
            text="● Online",
            font=("Segoe UI", 9, "bold"),
            bg=NAV_BG,
            fg=STATUS_ONLINE,
        ).pack(side="right", pady=16)

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
        tab_btn = tk.Label(
            parent,
            text=name,
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
        )
        tab_btn.pack(side="left", padx=3, pady=11)

        def on_enter(e):
            if self._active_tab != name and self._tab_states.get(name) != "alert":
                tab_btn.configure(
                    bg=NAV_TAB_HOVER, fg=NAV_TAB_HOVER_FG,
                    highlightbackground=NAV_TAB_HOVER_BORDER, highlightcolor=NAV_TAB_HOVER_BORDER
                )

        def on_leave(e):
            if self._active_tab != name and self._tab_states.get(name) != "alert":
                if self._tab_states.get(name) == "busy":
                    tab_btn.configure(
                        bg=NAV_TAB_BG, fg="#38BDF8",
                        highlightbackground=NAV_TAB_BORDER, highlightcolor=NAV_TAB_BORDER
                    )
                else:
                    tab_btn.configure(
                        bg=NAV_TAB_BG, fg=NAV_TEXT,
                        highlightbackground=NAV_TAB_BORDER, highlightcolor=NAV_TAB_BORDER
                    )

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
        except Exception:
            pass

        self._active_tab = tab_name
        self._update_all_nav_styles()

        if hasattr(target_frame, "on_tab_activated"):
            try:
                target_frame.on_tab_activated()
            except Exception:
                pass

    def set_tab_busy(self, tab_name: str, is_busy: bool):
        """Update the busy/processing status of a tab without interrupting navigation."""
        if tab_name not in self._app_frames:
            return
        if is_busy:
            self._tab_states[tab_name] = "busy"
            if self._active_tab != tab_name and self._tab_states.get(tab_name) != "alert":
                tab_btn = self._nav_items.get(tab_name)
                if tab_btn:
                    orig_name = self._tab_original_text.get(tab_name, tab_name)
                    tab_btn.configure(
                        fg="#38BDF8",
                        text=f"{orig_name} ⏳",
                    )
        else:
            if self._tab_states.get(tab_name) == "busy":
                self._tab_states[tab_name] = "idle"
            if self._active_tab != tab_name and self._tab_states.get(tab_name) != "alert":
                tab_btn = self._nav_items.get(tab_name)
                if tab_btn:
                    tab_btn.configure(
                        fg=NAV_TEXT,
                        text=self._tab_original_text[tab_name],
                    )

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
            except Exception:
                pass
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

        if phase:
            # Phase A: High-contrast alert (Vibrant emerald green background with crisp white text)
            tab_btn.configure(
                bg="#059669",
                fg="#FFFFFF",
                highlightbackground="#059669",
                font=("Segoe UI", 9, "bold"),
                text=f"{orig_name} ● Done",
            )
        else:
            # Phase B: Resting button (dark blue button fill with emerald highlight)
            tab_btn.configure(
                bg=NAV_TAB_BG,
                fg="#34D399",
                highlightbackground=NAV_TAB_BORDER,
                font=("Segoe UI", 9, "bold"),
                text=f"{orig_name} ○ Done",
            )

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
            except Exception:
                pass
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
            if name == self._active_tab:
                tab_btn.configure(
                    bg=NAV_TAB_ACTIVE,
                    fg=NAV_TEXT_ACTIVE,
                    highlightbackground=NAV_TAB_ACTIVE_BORDER,
                    font=("Segoe UI", 9, "bold"),
                    text=orig_name,
                )
            elif self._tab_states.get(name) == "alert":
                # Blinking routine controls colors; preserve alert state
                pass
            elif self._tab_states.get(name) == "busy":
                tab_btn.configure(
                    bg=NAV_TAB_BG,
                    fg="#38BDF8",
                    highlightbackground=NAV_TAB_BORDER,
                    font=("Segoe UI", 9, "bold"),
                    text=f"{orig_name} ⏳",
                )
            else:
                tab_btn.configure(
                    bg=NAV_TAB_BG,
                    fg=NAV_TEXT,
                    highlightbackground=NAV_TAB_BORDER,
                    font=("Segoe UI", 9, "bold"),
                    text=orig_name,
                )

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
