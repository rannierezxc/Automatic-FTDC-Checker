"""
Hub window shell, top navigation bar, and tab dispatcher for PTS Hub.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from core.config import APP_NAME, APP_VERSION
from core.theme import (
    NAV_BG, NAV_TAB_HOVER, NAV_TAB_ACTIVE, NAV_TEXT, NAV_TEXT_ACTIVE, STATUS_ONLINE
)


class HubWindow:
    """Main desktop shell hosting modular apps in tabs under a unified top navigation bar."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self._active_tab: Optional[str] = None
        self._nav_items: Dict[str, tk.Label] = {}
        self._app_frames: Dict[str, ttk.Frame] = {}

        # ── Outer Container ───────────────────────────────────────────────
        self.outer = ttk.Frame(self.root)
        self.outer.pack(fill="both", expand=True)

        # ── 1. Modern Top Navigation Bar ──────────────────────────────────
        self._build_top_navbar(self.outer)

        # ── 2. Content Area for Apps ──────────────────────────────────────
        self.content_area = ttk.Frame(self.outer)
        self.content_area.pack(fill="both", expand=True)

    def _build_top_navbar(self, parent: ttk.Frame):
        nav = tk.Frame(parent, bg=NAV_BG, height=52)
        nav.pack(side="top", fill="x")
        nav.pack_propagate(False)

        # Left Branding Block
        brand_frame = tk.Frame(nav, bg=NAV_BG)
        brand_frame.pack(side="left", padx=(16, 12), fill="y")

        # Brand Title
        tk.Label(
            brand_frame,
            text=APP_NAME,
            font=("Segoe UI", 12, "bold"),
            bg=NAV_BG,
            fg="#FFFFFF",
        ).pack(side="left", pady=14)

        # Version Badge
        v_badge = tk.Label(
            brand_frame,
            text=f"{APP_VERSION}",
            font=("Segoe UI", 8, "bold"),
            bg="#1E293B",
            fg="#38BDF8",
            padx=6,
            pady=2,
            relief="flat",
        )
        v_badge.pack(side="left", padx=(8, 12), pady=16)

        # Vertical Divider
        tk.Frame(brand_frame, bg="#334155", width=1, height=22).pack(
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
            font=("Segoe UI", 9),
            bg=NAV_BG,
            fg=STATUS_ONLINE,
        ).pack(side="right", pady=16)

    def register_app(self, name: str, frame: ttk.Frame, is_default: bool = False):
        """Register an application frame into the hub."""
        self._app_frames[name] = frame
        self._build_nav_tab(self.tabs_frame, name)
        if is_default or self._active_tab is None:
            self.switch_tab(name)

    def _build_nav_tab(self, parent: tk.Frame, name: str):
        is_first = (len(self._nav_items) == 0)
        tab_btn = tk.Label(
            parent,
            text=name,
            font=("Segoe UI", 9, "bold" if is_first else "normal"),
            bg=NAV_TAB_ACTIVE if is_first else NAV_BG,
            fg=NAV_TEXT_ACTIVE if is_first else NAV_TEXT,
            padx=14,
            pady=6,
            cursor="hand2",
        )
        tab_btn.pack(side="left", padx=3, pady=11)

        def on_enter(e):
            if self._active_tab != name:
                tab_btn.configure(bg=NAV_TAB_HOVER, fg="#F1F5F9")

        def on_leave(e):
            if self._active_tab != name:
                tab_btn.configure(bg=NAV_BG, fg=NAV_TEXT)

        def on_click(e):
            self.switch_tab(name)

        tab_btn.bind("<Enter>", on_enter)
        tab_btn.bind("<Leave>", on_leave)
        tab_btn.bind("<Button-1>", on_click)

        self._nav_items[name] = tab_btn

    def switch_tab(self, tab_name: str):
        """Switch currently visible application tab."""
        if tab_name not in self._app_frames:
            return

        target_frame = self._app_frames[tab_name]
        if tab_name == self._active_tab and target_frame.winfo_ismapped():
            return

        for frame in self._app_frames.values():
            frame.pack_forget()

        target_frame.pack(fill="both", expand=True)

        for name, tab_btn in self._nav_items.items():
            if name == tab_name:
                tab_btn.configure(
                    bg=NAV_TAB_ACTIVE,
                    fg=NAV_TEXT_ACTIVE,
                    font=("Segoe UI", 9, "bold"),
                )
            else:
                tab_btn.configure(
                    bg=NAV_BG,
                    fg=NAV_TEXT,
                    font=("Segoe UI", 9, "normal"),
                )
        self._active_tab = tab_name

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

        icon_lbl = tk.Label(center_card, text=icon, font=("Segoe UI", 36))
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
            foreground="#64748B",
            wraplength=420,
            justify="center",
        ).pack(pady=(0, 16))

        badge = tk.Label(
            center_card,
            text="MODULE IN DEVELOPMENT • COMING SOON",
            font=("Segoe UI", 8, "bold"),
            bg="#E2E8F0",
            fg="#475569",
            padx=10,
            pady=4,
        )
        badge.pack()

        return container
