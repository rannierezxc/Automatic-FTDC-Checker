"""
Tkinter GUI for the optimized GUID checker.
Extracted from v25 monolith. Uses optimized parser and analysis modules.
"""
import os
import sv_ttk
import multiprocessing
import re
import threading
import traceback
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from stdf_parser import (
    STDFReader, scan_test_list, parse_filter_values, parse_stdf_file,
    _count_files_text, _format_limit_text,
    _emit_log, _format_stdf_timestamp, _prepare_scanned_test_row,
    ByteProgressFunc, LogFunc, ProgressFunc,
)
from guid_analysis import (
    analyze_guid_data, parse_panel_wxy_parts, resolve_wxy_test_numbers,
    MPC_WXY_TEST_MAP, WXY_KEYS, UNSUPPORTED_MPC_WXY_MESSAGE,
    normalize_mpc_key, _parse_wxy_from_mpc_text, _format_coord_value,
)
from excel_output import (
    write_result_excel, build_summary_and_details, build_details_text, _fmt,
)


# ── Custom Modern UX Widgets ──────────────────────────────────────────────────

class ModernHoverButton(tk.Frame):
    """Modern flat button with light-blue hover fill and darker border."""
    def __init__(
        self, parent, text="", command=None, font=("Segoe UI", 9, "bold"),
        padx=10, pady=5, bg="#FFFFFF", border_color="#E0E0E0",
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
        self._default_fg = "#0F172A"

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

    def configure(self, cnf=None, **kwargs):
        if cnf is not None:
            kwargs.update(cnf)
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "state" in kwargs:
            st = kwargs.pop("state")
            self._state = st
            if st == "disabled":
                super().configure(bg="#E0E0E0")
                self.inner.configure(bg="#F0F0F0", fg="#94A3B8", cursor="arrow")
            else:
                super().configure(bg=self._border)
                self.inner.configure(bg=self._bg, fg=self._default_fg, cursor="hand2")
        if "text" in kwargs:
            self.inner.configure(text=kwargs.pop("text"))
        if kwargs:
            super().configure(**kwargs)

    def config(self, cnf=None, **kwargs):
        self.configure(cnf=cnf, **kwargs)

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
    """Thick, responsive modern green progress bar."""
    def __init__(
        self, parent, height=18, bg="#FFFFFF", bar_color="#10B981",
        border_color="#E0E0E0", **kwargs
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


class ModernCard(tk.LabelFrame):
    """High-performance modern card container with centralized background."""
    def __init__(self, parent, text="", font=("Segoe UI", 9, "bold"), padx=8, pady=6, **kwargs):
        super().__init__(
            parent,
            text=f" {text.strip()} " if text.strip() else "",
            font=font,
            fg="#0F172A",
            bg="#F0F0F0",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#E0E0E0",
            highlightcolor="#E0E0E0",
            padx=padx,
            pady=pady,
            **kwargs
        )


BASE_DIR = os.path.dirname(os.path.abspath(__file__)) #os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
icon_path = os.path.join(BASE_DIR, 'FTDC_Checker_icon.ico')
_FILENAME_TIMESTAMP_RE = re.compile(r"\d{14}")
APP_VERSION = "v1.3"


def _extract_first_filename_timestamp(path: str) -> str:
    """Return the first valid YYYYMMDDHHMMSS timestamp found in the filename.

    Some STDF files can represent the same datalog using different naming
    conventions, for example:
      - mmt-271300725.900_e_20260714082531_6140_2607142300.std.old
      - ATA5831_MMT-271300725.900_1__20260714082531.std

    Plain filename sorting puts the ATA-prefixed version first because of
    lexicographical order. This helper extracts the first 14-digit timestamp
    from left to right and validates it as YYYYMMDDHHMMSS, so both naming
    conventions are arranged by the actual datalog timestamp instead.
    """
    filename = os.path.basename(os.path.abspath(path))
    for match in _FILENAME_TIMESTAMP_RE.finditer(filename):
        timestamp = match.group(0)
        try:
            datetime.strptime(timestamp, "%Y%m%d%H%M%S")
        except ValueError:
            continue
        return timestamp
    return ""


def _path_filename_sort_key(path: str) -> Tuple[int, str, str, str]:
    """Return a deterministic ascending datalog sort key.

    Files are ordered primarily by the first valid YYYYMMDDHHMMSS timestamp
    found in the filename, scanning from left to right. The base filename and
    absolute path are used only as deterministic tie-breakers.

    Files with no valid 14-digit timestamp fall back behind timestamped files
    and are then ordered by filename, case-insensitively.
    """
    abs_path = os.path.abspath(path)
    base_name = os.path.basename(abs_path).casefold()
    timestamp = _extract_first_filename_timestamp(abs_path)
    if timestamp:
        return 0, timestamp, base_name, abs_path.casefold()
    return 1, base_name, "", abs_path.casefold()


def _merge_test_rows(rows):
    merged = {}
    for row in rows:
        try:
            test_num = int(row.get("TEST_NUM"))
        except (TypeError, ValueError):
            continue
        if test_num not in merged:
            merged[test_num] = dict(row)
        else:
            existing = merged[test_num]
            if not existing.get("TEST_TXT") and row.get("TEST_TXT"):
                existing["TEST_TXT"] = row.get("TEST_TXT", "")
            if not existing.get("UNITS") and row.get("UNITS"):
                existing["UNITS"] = row.get("UNITS", "")
            existing["_SEARCH_TEXT"] = " ".join([
                str(existing.get("TEST_NUM", "")), str(existing.get("TEST_TXT", "")),
                str(existing.get("UNITS", "")), str(existing.get("_LO_LIMIT_TEXT", "")),
                str(existing.get("_HI_LIMIT_TEXT", "")),
            ]).lower()
    return sorted(merged.values(), key=lambda item: int(item["TEST_NUM"]))


class STDFGuidCheckerApp:
    PANELS = ("FIRST PASS", "RETEST", "QC")

    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        import ctypes
        try:
    # Set an arbitrary string as your App ID
            myappid = 'FTDC_Checker' 
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception as e:
            pass # Handle gracefully if not running on Windows
        self.root = root
        self.tk = tk
        self.ttk = ttk
        sv_ttk.set_theme("light")
        try:
            self.root.tk.eval("""
                proc configure_colors {} {
                    ttk::style configure . -background "#F0F0F0" -font SunValleyBodyFont
                    tk_setPalette background "#F0F0F0" foreground "#0F172A" selectBackground "#2563EB" selectForeground "#FFFFFF"
                }
            """)
            self.root.tk.call("tk_setPalette", "background", "#F0F0F0")
            self.root.update_idletasks()
        except Exception:
            pass

        self.panel_files: Dict[str, List[str]] = {panel: [] for panel in self.PANELS}
        self.panel_listboxes: Dict[str, Any] = {}
        self._panel_scrollbars: Dict[str, Any] = {}
        self._panel_count_vars: Dict[str, tk.StringVar] = {panel: tk.StringVar(value="0 files") for panel in self.PANELS}
        self._panel_badges: Dict[str, Any] = {}
        self.filter_widgets: List[Any] = []
        self.is_running = False
        self.is_scanning_tests = False
        self._test_window = None
        self._test_tree = None
        self._test_filter_var = None
        self._cached_tests: List[Dict[str, object]] = []
        self._test_scan_cache: Dict[Tuple[str, int, int], List[Dict[str, object]]] = {}
        self._analysis_cache: Dict[Tuple[object, ...], Dict[str, object]] = {}

        self.root.title(f"Automatic FTDC Checker {APP_VERSION}")
        self.root.iconbitmap(default=icon_path)
        self.root.geometry("1260x820")
        self.root.minsize(1060, 680)
        self.style = ttk.Style()
        self.style.configure(".", background="#F0F0F0")
        self.style.configure("TFrame", background="#F0F0F0")
        self.style.configure("TLabel", background="#F0F0F0", foreground="#0F172A")
        self.style.configure("TCheckbutton", background="#F0F0F0", foreground="#0F172A")
        self.style.configure("White.TCheckbutton", background="#FFFFFF", foreground="#0F172A")
        self.style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 14))
        self.style.configure("Action.TButton", font=("Segoe UI", 9, "bold"), padding=(10, 8))
        self.style.configure("MiniAction.TButton", font=("Segoe UI", 8), padding=(6, 3))
        self.style.configure("CardHeader.TLabel", font=("Segoe UI", 10, "bold"))
        self.style.configure("Subtext.TLabel", font=("Segoe UI", 8), foreground="#64748B")

        # Register Neon Green fill style on default tkinter Progressbar
        try:
            self.root.tk.eval('''
image create photo neon_pbar -width 20 -height 12
neon_pbar put #00E676 -to 0 0 20 12
catch {ttk::style element create Neon.pbar image neon_pbar}
ttk::style layout Neon.Horizontal.TProgressbar {
    Horizontal.Progressbar.trough -sticky nswe -children {
        Neon.pbar -side left -sticky ns
    }
}
''')
        except Exception:
            pass

        self.lot_id_var = tk.StringVar()
        self.mpc_var = tk.StringVar()
        self.fp_actual_good_qty_var = tk.StringVar()
        self.total_actual_good_qty_var = tk.StringVar()
        self.manual_filter_var = tk.BooleanVar(value=False)
        self.tests_var = tk.StringVar()
        self.selected_tests_var = tk.StringVar()
        self.range_from_var = tk.StringVar()
        self.range_to_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.progress_text_var = tk.StringVar(value="")
        self._selected_test_count_var = tk.StringVar(value="No test parameters selected")
        self.show_all_tests_button = None
        self.clear_selected_tests_button = None
        self.convert_button = None
        self.get_ftdc_button = None
        self.get_stdf_button = None
        self.check_stdf_button = None
        self._action_buttons = []
        # Check STDF per-file summary cache (keyed by file signature).
        self._check_summary_cache: Dict[Tuple[str, int, int], Tuple[str, str, str, str]] = {}
        self._check_cache_lock = threading.Lock()
        self.progress_bar = None
        self._pending_progress = None  # (fraction, message) — atomic shared state
        self._progress_poll_id = None
        self._mpc_lookup_after_id = None  # debounce timer for MPC auto-lookup
        # ── MPC lookup performance state ──────────────────────────────────
        # In-memory cache: LotID (upper) -> resolved MPC string. A cache hit
        # populates the MPC field with ZERO network delay (instant on repeats).
        self._mpc_cache: Dict[str, str] = {}
        # Monotonic request id: guards against a slow older lookup overwriting
        # the field after the user has already moved on to a newer Lot ID.
        self._mpc_lookup_seq = 0
        # Shared HTTP session for keep-alive (avoids a fresh TCP/DNS handshake
        # on every lookup). Created lazily; reused across all MPC lookups.
        self._http_session = None
        self._http_session_lock = threading.Lock()
        self._mpc_lookup_url = "http://mth-vm-eaprd1/MPHL/getlot/mes.asmx/GetLotByLotId"
        self._build_ui()
        self._update_selected_test_summary()
        self._sync_manual_filter_state()
        # Attach MPC auto-lookup: fires 500ms after the user stops typing in Lot ID
        self.lot_id_var.trace_add("write", self._on_lot_id_changed)
        # Fix 6: prewarm DNS + TCP to the MES host at startup so the FIRST real
        # lookup does not pay cold connection setup. Runs in the background and
        # never blocks the UI (best-effort; failures are ignored).
        self.root.after(200, self._prewarm_mpc_connection)

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk

        # ── Color Palette & Theming Constants (Dark Blue Navbar) ───────────
        self._nav_bg = "#172554"         # Dark blue header (Tailwind blue-950)
        self._nav_border = "#1E3A8A"     # Dark blue bottom border
        self._nav_tab_bg = "#1E3A8A"     # Inactive button surface (rich dark blue)
        self._nav_tab_border = "#2563EB" # Inactive button border
        self._nav_tab_hover = "#2563EB"  # Hover fill
        self._nav_tab_hover_border = "#38BDF8"
        self._nav_tab_hover_fg = "#FFFFFF"
        self._nav_tab_active = "#2563EB" # Vibrant blue active tab
        self._nav_tab_active_border = "#60A5FA"
        self._nav_text = "#E2E8F0"       # Crisp light text for inactive tab
        self._nav_text_active = "#FFFFFF"# Crisp white active text
        self._nav_title_fg = "#FFFFFF"   # White navbar brand title
        self._nav_badge_bg = "#1E3A8A"   # Dark blue badge background
        self._nav_badge_fg = "#38BDF8"   # Sky blue badge text
        self._nav_badge_border = "#2563EB"
        self._active_tab = None
        self._nav_items = {}
        self._tab_frames = {}

        # ── Root Outer Container ───────────────────────────────────────────
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        # ── 1. Modern Top Navigation Bar ───────────────────────────────────
        self._build_top_navbar(outer)

        # ── 2. Content Area for Tabs ───────────────────────────────────────
        self._content_area = ttk.Frame(outer)
        self._content_area.pack(fill="both", expand=True)

        # ── 3. Tab Pages ───────────────────────────────────────────────────
        # Tab 1: FTDC Checker (Main app)
        ftdc_tab = ttk.Frame(self._content_area)
        self._tab_frames["FTDC Checker"] = ftdc_tab
        self._build_ftdc_tab(ftdc_tab)

        # Tab 2: STDF2SUM (Placeholder)
        stdf2sum_tab = ttk.Frame(self._content_area)
        self._tab_frames["STDF2SUM"] = stdf2sum_tab
        self._build_placeholder_tab(
            stdf2sum_tab,
            title="STDF2SUM Converter",
            subtitle="Batch-convert STDF datalogs to standardized summary formats.",
            icon="📊",
        )

        # Tab 3: Data Analysis (Placeholder)
        analysis_tab = ttk.Frame(self._content_area)
        self._tab_frames["Data Analysis"] = analysis_tab
        self._build_placeholder_tab(
            analysis_tab,
            title="Data Analysis Hub",
            subtitle="Advanced parametric distribution, yield trend analytics, and outlier detection.",
            icon="📈",
        )

        # Default active tab
        self._switch_tab("FTDC Checker")

    # ── Top Navigation Bar ─────────────────────────────────────────────────

    def _build_top_navbar(self, parent):
        tk = self.tk
        nav = tk.Frame(parent, bg=self._nav_bg, height=52, highlightthickness=1, highlightbackground=self._nav_border, bd=0)
        nav.pack(side="top", fill="x")
        nav.pack_propagate(False)

        # Left branding block
        brand_frame = tk.Frame(nav, bg=self._nav_bg)
        brand_frame.pack(side="left", padx=(16, 12), fill="y")

        # Brand Title
        tk.Label(
            brand_frame,
            text="Automatic FTDC Checker",
            font=("Segoe UI", 12, "bold"),
            bg=self._nav_bg,
            fg=self._nav_title_fg,
        ).pack(side="left", pady=14)

        # Version Badge
        v_badge = tk.Label(
            brand_frame,
            text=f"{APP_VERSION}",
            font=("Segoe UI", 8, "bold"),
            bg=self._nav_badge_bg,
            fg=self._nav_badge_fg,
            padx=6,
            pady=2,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=self._nav_badge_border,
        )
        v_badge.pack(side="left", padx=(8, 12), pady=16)

        # Subtle Vertical Divider
        tk.Frame(brand_frame, bg=self._nav_border, width=1, height=22).pack(
            side="left", padx=(0, 8), pady=15
        )

        # Tabs Container
        tabs_frame = tk.Frame(nav, bg=self._nav_bg)
        tabs_frame.pack(side="left", fill="y", padx=4)

        nav_tabs = ["FTDC Checker", "STDF2SUM", "Data Analysis"]
        for tab_name in nav_tabs:
            self._build_nav_tab(tabs_frame, tab_name)

        # Right side status indicator
        status_frame = tk.Frame(nav, bg=self._nav_bg)
        status_frame.pack(side="right", padx=(0, 16), fill="y")

        tk.Label(
            status_frame,
            text="● Online",
            font=("Segoe UI", 9, "bold"),
            bg=self._nav_bg,
            fg="#10B981",
        ).pack(side="right", pady=16)

    def _build_nav_tab(self, parent, name: str):
        tk = self.tk
        is_active = (name == "FTDC Checker")
        tab_btn = tk.Label(
            parent,
            text=name,
            font=("Segoe UI", 9, "bold"),
            bg=self._nav_tab_active if is_active else self._nav_tab_bg,
            fg=self._nav_text_active if is_active else self._nav_text,
            padx=14,
            pady=6,
            cursor="hand2",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=self._nav_tab_active_border if is_active else self._nav_tab_border,
        )
        tab_btn.pack(side="left", padx=3, pady=11)

        def on_enter(e):
            if self._active_tab != name:
                tab_btn.configure(bg=self._nav_tab_hover, fg=self._nav_tab_hover_fg, highlightbackground=self._nav_tab_hover_border)

        def on_leave(e):
            if self._active_tab != name:
                tab_btn.configure(bg=self._nav_tab_bg, fg=self._nav_text, highlightbackground=self._nav_tab_border)

        def on_click(e):
            self._switch_tab(name)

        tab_btn.bind("<Enter>", on_enter)
        tab_btn.bind("<Leave>", on_leave)
        tab_btn.bind("<Button-1>", on_click)

        self._nav_items[name] = tab_btn

    def _switch_tab(self, tab_name: str):
        if tab_name == self._active_tab and self._tab_frames[tab_name].winfo_ismapped():
            return
        for frame in self._tab_frames.values():
            frame.pack_forget()
        self._tab_frames[tab_name].pack(fill="both", expand=True)

        for name, tab_btn in self._nav_items.items():
            if name == tab_name:
                tab_btn.configure(
                    bg=self._nav_tab_active,
                    fg=self._nav_text_active,
                    font=("Segoe UI", 9, "bold"),
                    highlightbackground=self._nav_tab_active_border,
                )
            else:
                tab_btn.configure(
                    bg=self._nav_tab_bg,
                    fg=self._nav_text,
                    font=("Segoe UI", 9, "bold"),
                    highlightbackground=self._nav_tab_border,
                )
        self._active_tab = tab_name

    def _build_placeholder_tab(self, parent, title: str, subtitle: str, icon: str = "🚀"):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        center_card = self.ttk.LabelFrame(parent, text="", padding=30)
        center_card.grid(row=0, column=0)

        icon_lbl = self.tk.Label(center_card, text=icon, font=("Segoe UI", 36))
        icon_lbl.pack(pady=(0, 10))

        self.ttk.Label(
            center_card,
            text=title,
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(0, 6))

        self.ttk.Label(
            center_card,
            text=subtitle,
            font=("Segoe UI", 10),
            foreground="#64748B",
            wraplength=420,
            justify="center",
        ).pack(pady=(0, 16))

        badge = self.tk.Label(
            center_card,
            text="MODULE IN DEVELOPMENT • COMING SOON",
            font=("Segoe UI", 8, "bold"),
            bg="#E2E8F0",
            fg="#475569",
            padx=10,
            pady=4,
        )
        badge.pack()

    # ── FTDC Checker Tab (Redesigned Modern Layout) ────────────────────────

    def _build_ftdc_tab(self, main):
        tk = self.tk
        ttk = self.ttk

        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        # ── Upper Workspace Frame (Stable: never resizes on log height change) ──
        workspace = ttk.Frame(main)
        workspace.pack(fill="both", expand=True, padx=8, pady=(6, 125))

        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(1, weight=1)

        # ── 1. Lot & Production Configuration Card (No hint text, compact & clean) ──
        input_card = ttk.LabelFrame(workspace, text=" Lot & Production Configuration ", padding=(12, 6))
        input_card.grid(row=0, column=0, sticky="ew", padx=2, pady=(0, 6))
        for c in range(4):
            input_card.columnconfigure(c, weight=1, uniform="input_cols")

        # Col 0: Lot ID
        col0 = ttk.Frame(input_card)
        col0.grid(row=0, column=0, sticky="ew", padx=6, pady=3)
        ttk.Label(col0, text="Lot ID", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Entry(col0, textvariable=self.lot_id_var).pack(fill="x", pady=(3, 2))

        # Col 1: MPC
        col1 = ttk.Frame(input_card)
        col1.grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        mpc_lbl_frame = ttk.Frame(col1)
        mpc_lbl_frame.pack(anchor="w", fill="x")
        ttk.Label(mpc_lbl_frame, text="MPC", font=("Segoe UI", 9, "bold")).pack(side="left")
        auto_badge = tk.Label(
            mpc_lbl_frame,
            text="Auto-resolved",
            font=("Segoe UI", 7, "bold"),
            bg="#EFF6FF",
            fg="#2563EB",
            padx=4,
            pady=0,
            relief="flat",
        )
        auto_badge.pack(side="left", padx=(6, 0))
        ttk.Entry(col1, textvariable=self.mpc_var).pack(fill="x", pady=(3, 2))

        # Col 2: First Pass Actual Good QTY
        col2 = ttk.Frame(input_card)
        col2.grid(row=0, column=2, sticky="ew", padx=6, pady=3)
        ttk.Label(col2, text="First Pass Actual Good QTY", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Entry(col2, textvariable=self.fp_actual_good_qty_var).pack(fill="x", pady=(3, 2))

        # Col 3: Total Actual Good QTY
        col3 = ttk.Frame(input_card)
        col3.grid(row=0, column=3, sticky="ew", padx=6, pady=3)
        ttk.Label(col3, text="Total Actual Good QTY", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Entry(col3, textvariable=self.total_actual_good_qty_var).pack(fill="x", pady=(3, 2))

        # ── 2. Central Split: File Management (Left) vs Controls & Filter (Right) ─
        center = ttk.Frame(workspace)
        center.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        center.columnconfigure(0, weight=62)  # Files
        center.columnconfigure(1, weight=38)  # Filter & Controls
        center.rowconfigure(0, weight=1)

        # ── Left: Input STDF Files Container ──────────────────────────────
        files_box = ttk.LabelFrame(center, text=" Input STDF Datalogs ", padding=(8, 6))
        files_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        files_box.columnconfigure(0, weight=1)
        for r in range(3):
            files_box.rowconfigure(r, weight=1)

        # Build FIRST PASS, RETEST, QC panels
        for r, panel in enumerate(self.PANELS):
            self._build_file_panel(files_box, panel, r)

        # ── Right: Operations & Parameter Filters Container ────────────────
        right_box = ttk.Frame(center)
        right_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right_box.columnconfigure(0, weight=1)
        right_box.rowconfigure(0, weight=1)  # Test Parameter Filter (Top)
        right_box.rowconfigure(1, weight=0)  # Action Buttons (Bottom)

        # ── Right Top: Test Parameter Filter Card (Swapped to Top) ─────────
        filter_card = ModernCard(right_box, text="Test Parameter Filter", padx=8, pady=6)
        filter_card.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        filter_card.columnconfigure(0, weight=1)
        self.filter_card = filter_card

        # Header Row: Show All Test + Clear Selected + Manual Filter toggle
        filter_header = tk.Frame(filter_card, bg="#F0F0F0")
        filter_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        filter_header.columnconfigure(1, weight=1)
        self.filter_header = filter_header

        self.show_all_tests_button = ModernHoverButton(
            filter_header,
            text="Show All Test",
            command=self.show_all_tests,
            padx=8, pady=3,
        )
        self.show_all_tests_button.grid(row=0, column=0, sticky="w")
        self.filter_widgets.append(self.show_all_tests_button)

        self.clear_selected_tests_button = ModernHoverButton(
            filter_header,
            text="Clear Selected",
            command=self._clear_selected_test_filter,
            padx=8, pady=3,
        )
        self.clear_selected_tests_button.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.clear_selected_tests_button.grid_remove()

        self.manual_filter_cb = ttk.Checkbutton(
            filter_header,
            text="Manual Filter",
            variable=self.manual_filter_var,
            command=self._sync_manual_filter_state,
        )
        self.manual_filter_cb.grid(row=0, column=2, sticky="e")

        self._filter_card_labels = []

        # Parameter Selection Field
        param_label = tk.Label(filter_card, text="Test Parameter Selection", font=("Segoe UI", 9, "bold"), bg="#F0F0F0", fg="#0F172A")
        param_label.grid(row=1, column=0, sticky="w", pady=(3, 1))
        self._filter_card_labels.append(param_label)

        self.selected_entry = ttk.Entry(filter_card, textvariable=self.selected_tests_var, state="readonly", font=("Segoe UI", 8))
        self.selected_entry.grid(row=2, column=0, sticky="ew")
        self.filter_widgets.append(self.selected_entry)

        count_label = tk.Label(filter_card, textvariable=self._selected_test_count_var, font=("Segoe UI", 8), bg="#F0F0F0", fg="#64748B")
        count_label.grid(row=3, column=0, sticky="w", pady=(1, 4))
        self._filter_card_labels.append(count_label)

        # Direct Test Numbers Input
        num_label = tk.Label(filter_card, text="Test Numbers", font=("Segoe UI", 9, "bold"), bg="#F0F0F0", fg="#0F172A")
        num_label.grid(row=4, column=0, sticky="w", pady=(2, 1))
        self._filter_card_labels.append(num_label)

        self.typed_entry = ttk.Entry(filter_card, textvariable=self.tests_var)
        self.typed_entry.grid(row=5, column=0, sticky="ew")
        self.filter_widgets.append(self.typed_entry)

        fmt_label = tk.Label(
            filter_card,
            text='Format: space or comma separated (e.g. "1001 1002")',
            font=("Segoe UI", 8), bg="#F0F0F0", fg="#64748B",
        )
        fmt_label.grid(row=6, column=0, sticky="w", pady=(1, 6))
        self._filter_card_labels.append(fmt_label)

        # Range Filtering
        range_frame = tk.Frame(filter_card, bg="#F0F0F0")
        range_frame.grid(row=7, column=0, sticky="ew", pady=(2, 2))
        range_frame.columnconfigure(1, weight=1)
        range_frame.columnconfigure(3, weight=1)
        self.range_frame = range_frame

        rf_label = tk.Label(range_frame, text="Range From:", font=("Segoe UI", 8), bg="#F0F0F0", fg="#64748B")
        rf_label.grid(row=0, column=0, sticky="w", padx=(0, 4))
        self._filter_card_labels.append(rf_label)

        self.range_from_entry = ttk.Entry(range_frame, textvariable=self.range_from_var)
        self.range_from_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.filter_widgets.append(self.range_from_entry)

        rt_label = tk.Label(range_frame, text="Range To:", font=("Segoe UI", 8), bg="#F0F0F0", fg="#64748B")
        rt_label.grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._filter_card_labels.append(rt_label)

        self.range_to_entry = ttk.Entry(range_frame, textvariable=self.range_to_var)
        self.range_to_entry.grid(row=0, column=3, sticky="ew")
        self.filter_widgets.append(self.range_to_entry)

        # ── Right Bottom: Action Buttons Card (Swapped to Bottom, Renamed) ─
        action_card = ttk.LabelFrame(right_box, text=" Action Buttons ", padding=(10, 8))
        action_card.grid(row=1, column=0, sticky="ew")
        action_card.columnconfigure(0, weight=1)

        # Top Row of Action Buttons: 3 utility buttons from left to right:
        # 1. Get FTDC Fail, 2. Get STDF, 3. Check STDF
        sec_frame = ttk.Frame(action_card)
        sec_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        for c in range(3):
            sec_frame.columnconfigure(c, weight=1, uniform="action_btns")

        self.get_ftdc_button = ModernHoverButton(
            sec_frame,
            text="Get FTDC Fail",
            command=self.start_get_ftdc_fail,
            padx=6, pady=10, font=("Segoe UI", 9, "bold"),
        )
        self.get_ftdc_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.get_stdf_button = ModernHoverButton(
            sec_frame,
            text="Get STDF",
            command=self.start_get_stdf,
            padx=6, pady=10, font=("Segoe UI", 9, "bold"),
        )
        self.get_stdf_button.grid(row=0, column=1, sticky="ew", padx=3)

        self.check_stdf_button = ModernHoverButton(
            sec_frame,
            text="Check STDF",
            command=self.start_check_stdf,
            padx=6, pady=10, font=("Segoe UI", 9, "bold"),
        )
        self.check_stdf_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        # Bottom Row: Run Analysis (Get Data) kept as Accent.TButton with larger height
        self.convert_button = ttk.Button(
            action_card,
            text="⚡ Run Analysis (Get Data)",
            command=self.start_get_data,
            style="Accent.TButton",
        )
        self.convert_button.grid(row=1, column=0, sticky="ew", pady=(4, 2))

        # All 4 action buttons registered
        self._action_buttons = [
            self.get_ftdc_button,
            self.get_stdf_button,
            self.check_stdf_button,
            self.convert_button,
        ]

        # ── 3. Live Progress & Execution Status Bar ────────────────────────
        progress_card = ttk.LabelFrame(workspace, text="", padding=(10, 6))
        progress_card.grid(row=2, column=0, sticky="ew", padx=2, pady=(4, 0))
        progress_card.columnconfigure(1, weight=1)

        # Status text & percentage
        status_row = ttk.Frame(progress_card)
        status_row.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        status_row.columnconfigure(1, weight=1)

        ttk.Label(status_row, text="Status:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            status_row,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            foreground="#1E293B",
        ).grid(row=0, column=1, sticky="w", padx=(6, 0))

        ttk.Label(
            status_row,
            textvariable=self.progress_text_var,
            font=("Segoe UI", 9, "bold"),
            foreground="#000000",
            anchor="e",
        ).grid(row=0, column=2, sticky="e")

        # Clear All button + Default Tkinter Progressbar with Neon Green fill
        self.clear_all_button = ModernHoverButton(
            progress_card,
            text="Clear All",
            command=self.clear_all,
            padx=8, pady=3,
        )
        self.clear_all_button.grid(row=1, column=0, sticky="w", padx=(0, 10))

        self.progress_bar = ttk.Progressbar(
            progress_card,
            style="Neon.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress_bar.grid(row=1, column=1, columnspan=2, sticky="ew")

        # ── 4. Overlay Log Drawer (Covers over bottom area with ZERO lag!) ──
        self._build_log_overlay_drawer(main)

    def _build_log_overlay_drawer(self, parent):
        tk = self.tk
        ttk = self.ttk

        self._log_cur_height = 120
        self._log_drawer = tk.Frame(parent, bg="#F0F0F0", highlightthickness=1, highlightbackground="#E0E0E0", bd=0, relief="flat")
        self._log_drawer.place(relx=0, rely=1.0, relwidth=1.0, height=self._log_cur_height, anchor="sw")

        # Drag handle bar (Clean default light styling)
        drag_bar = tk.Frame(self._log_drawer, bg="#F0F0F0", height=28, cursor="sb_v_double_arrow")
        drag_bar.pack(fill="x")

        tk.Label(
            drag_bar,
            text="⠿ Execution Console & Activity Log",
            font=("Segoe UI", 9, "bold"),
            bg="#F0F0F0",
            fg="#0F172A",
            cursor="sb_v_double_arrow",
        ).pack(side="left", padx=10)

        tk.Label(
            drag_bar,
            text="(drag handle to resize)",
            font=("Segoe UI", 8),
            bg="#F0F0F0",
            fg="#475569",
            cursor="sb_v_double_arrow",
        ).pack(side="left")

        # Console Tool Buttons
        btn_box = tk.Frame(drag_bar, bg="#F0F0F0")
        btn_box.pack(side="right", padx=6, pady=2)

        def set_h(h):
            self._log_cur_height = h
            self._log_drawer.place_configure(height=h)

        def make_preset(text, h):
            b = ModernHoverButton(btn_box, text=text, command=lambda: set_h(h), padx=5, pady=1, font=("Segoe UI", 8))
            b.pack(side="left", padx=2)

        make_preset("Min", 28)
        make_preset("Compact", 120)
        make_preset("Half", 280)
        make_preset("Max", 480)

        # Copy & Clear log buttons with ModernHoverButton
        copy_btn = ModernHoverButton(btn_box, text="Copy Log", command=self.copy_log, padx=6, pady=1, font=("Segoe UI", 8))
        copy_btn.pack(side="left", padx=(6, 2))

        clear_btn = ModernHoverButton(btn_box, text="Clear Log", command=self.clear_log, padx=6, pady=1, font=("Segoe UI", 8))
        clear_btn.pack(side="left", padx=2)

        # Smooth drag handlers without reflowing underlying workspace!
        start_y = [0]

        def on_start(e):
            start_y[0] = e.y_root

        def on_drag(e):
            dy = start_y[0] - e.y_root
            max_h = parent.winfo_height() - 40
            new_h = max(28, min(max_h, self._log_cur_height + dy))
            self._log_drawer.place_configure(height=new_h)

        def on_stop(e):
            dy = start_y[0] - e.y_root
            max_h = parent.winfo_height() - 40
            self._log_cur_height = max(28, min(max_h, self._log_cur_height + dy))

        for w in (drag_bar,):
            w.bind("<Button-1>", on_start)
            w.bind("<B1-Motion>", on_drag)
            w.bind("<ButtonRelease-1>", on_stop)

        # Console Text Box with Scrollbar (Default background color)
        console_body = tk.Frame(self._log_drawer, bg="#FFFFFF")
        console_body.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            console_body,
            wrap="word",
            font=("Consolas", 9),
            bg="#FFFFFF",
            fg="#0F172A",
            selectbackground="#2563EB",
            selectforeground="#FFFFFF",
            insertbackground="#0F172A",
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=4,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.configure(state="disabled")

        scroll = ttk.Scrollbar(console_body, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

    def copy_log(self):
        """Copy entire log buffer to system clipboard."""
        try:
            content = self.log_text.get("1.0", "end-1c")
            if content.strip():
                self.root.clipboard_clear()
                self.root.clipboard_append(content)
                self.set_status("Log copied to clipboard")
        except Exception:
            pass

    def clear_log(self):
        """Clear execution log console."""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", self.tk.END)
        self.log_text.configure(state="disabled")

    def _build_file_panel(self, parent, panel: str, row_index: int):
        tk = self.tk
        ttk = self.ttk

        panel_colors = {
            "FIRST PASS": "#2563EB",
            "RETEST": "#D97706",
            "QC": "#7C3AED",
        }
        accent_color = panel_colors.get(panel, "#475569")

        frame = ttk.Frame(parent)
        frame.grid(row=row_index, column=0, sticky="nsew", padx=2, pady=3)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        # ── Panel Card Header ─────────────────────────────────────────────
        hdr = ttk.Frame(frame)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        hdr.columnconfigure(2, weight=1)

        dot = tk.Label(hdr, text="■", font=("Segoe UI", 9, "bold"), fg=accent_color)
        dot.grid(row=0, column=0, sticky="w", padx=(0, 4))

        ttk.Label(hdr, text=panel, style="PanelTitle.TLabel").grid(row=0, column=1, sticky="w")

        count_var = self._panel_count_vars[panel]
        badge = tk.Label(
            hdr,
            textvariable=count_var,
            font=("Segoe UI", 8, "bold"),
            bg="#CBD5E1",
            fg="#334155",
            padx=7,
            pady=1,
            relief="flat",
        )
        badge.grid(row=0, column=2, sticky="w", padx=(6, 0))
        self._panel_badges[panel] = badge

        # Header Action Buttons with ModernHoverButton
        btn_frame = ttk.Frame(hdr)
        btn_frame.grid(row=0, column=3, sticky="e")

        ModernHoverButton(
            btn_frame,
            text="+ Add Files",
            command=lambda p=panel: self.select_files(p),
            padx=6, pady=2, font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=(0, 3))

        ModernHoverButton(
            btn_frame,
            text="Remove Selected",
            command=lambda p=panel: self.remove_selected_files(p),
            padx=6, pady=2, font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=2)

        ModernHoverButton(
            btn_frame,
            text="Clear Files",
            command=lambda p=panel: self.clear_files(p),
            padx=6, pady=2, font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=(2, 0))

        # ── Listbox with Dynamic Auto-Hiding Scrollbar ─────────────────────
        list_frame = ttk.Frame(frame)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        listbox = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            height=3,
            font=("Segoe UI", 9),
            bg="#FFFFFF",
            fg="#0F172A",
            selectbackground="#2563EB",
            selectforeground="#FFFFFF",
            relief="solid",
            borderwidth=1,
        )
        listbox.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        self.panel_listboxes[panel] = listbox
        self._panel_scrollbars[panel] = scroll

        # Dynamic scrollbar: only shown if files exceed visible area!
        self._update_panel_scrollbar(panel)

    def _update_panel_scrollbar(self, panel: str):
        """Show vertical scrollbar only if STDF files exceed the visible listbox height."""
        lb = self.panel_listboxes.get(panel)
        sb = self._panel_scrollbars.get(panel)
        if not lb or not sb:
            return
        n = lb.size()
        visible_height = int(lb.cget("height") or 3)
        if self.root.winfo_ismapped():
            self.root.update_idletasks()
            first_box = lb.bbox(0)
            last_box = lb.bbox(n - 1) if n > 0 else None
            all_visible = (n == 0) or (first_box is not None and last_box is not None)
        else:
            all_visible = (n <= visible_height)

        if all_visible:
            sb.pack_forget()
        else:
            sb.pack(side="right", fill="y")

    def _sync_manual_filter_state(self):
        enabled = bool(self.manual_filter_var.get())
        state = "normal" if enabled and not self.is_running and not self.is_scanning_tests else "disabled"

        card_bg = "#FFFFFF" if enabled else "#F0F0F0"
        if hasattr(self, "filter_card") and self.filter_card:
            try:
                self.filter_card.configure(bg=card_bg)
            except Exception:
                pass
        if hasattr(self, "filter_header") and self.filter_header:
            try:
                self.filter_header.configure(bg=card_bg)
            except Exception:
                pass
        if hasattr(self, "range_frame") and self.range_frame:
            try:
                self.range_frame.configure(bg=card_bg)
            except Exception:
                pass
        if hasattr(self, "_filter_card_labels"):
            for lbl in self._filter_card_labels:
                try:
                    lbl.configure(bg=card_bg)
                except Exception:
                    pass
        if hasattr(self, "manual_filter_cb") and self.manual_filter_cb:
            try:
                self.manual_filter_cb.configure(style="White.TCheckbutton" if enabled else "TCheckbutton")
            except Exception:
                pass

        for widget in self.filter_widgets:
            try:
                # Do not disable show_all_tests_button when manual_filter is False;
                # clicking it will automatically enable manual filter for convenience.
                if widget is self.show_all_tests_button:
                    widget.configure(state="disabled" if self.is_running or self.is_scanning_tests else "normal")
                elif widget is self.selected_entry:
                    widget.configure(state="readonly" if enabled else "disabled")
                else:
                    widget.configure(state=state)
            except Exception:
                pass
        self._sync_clear_selected_tests_button(bool(self.selected_tests_var.get().strip()))

    def _get_all_selected_files(self) -> List[str]:
        return [path for panel in self.PANELS for path in self.panel_files[panel]]

    def log(self, message: str):
        def append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(0, append)

    def set_status(self, message: str):
        self.root.after(0, lambda: self.status_var.set(message))

    def update_progress(self, fraction: float, message: str = ""):
        # Thread-safe: just overwrite the shared state; the UI timer reads it
        self._pending_progress = (fraction, message)

    def _poll_progress(self):
        """Repeating UI timer — reads the latest progress and paints it once."""
        pending = self._pending_progress
        if pending is not None:
            fraction, message = pending
            self._pending_progress = None  # consume
            pct = max(0.0, min(100.0, float(fraction) * 100.0))
            if self.progress_bar is not None:
                self.progress_bar.configure(value=pct)
            self.progress_text_var.set(f"{pct:5.1f}%")
            if message:
                self.status_var.set(str(message).replace("Converting:", "Converting"))
        self._progress_poll_id = self.root.after(50, self._poll_progress)

    def _start_progress_polling(self):
        if self._progress_poll_id is None:
            self._pending_progress = None
            self._progress_poll_id = self.root.after(50, self._poll_progress)

    def _stop_progress_polling(self):
        if self._progress_poll_id is not None:
            self.root.after_cancel(self._progress_poll_id)
            self._progress_poll_id = None
        # Flush any remaining pending update
        pending = self._pending_progress
        if pending is not None:
            fraction, message = pending
            self._pending_progress = None
            pct = max(0.0, min(100.0, float(fraction) * 100.0))
            if self.progress_bar is not None:
                self.progress_bar.configure(value=pct)
            self.progress_text_var.set(f"{pct:5.1f}%")
            if message:
                self.status_var.set(str(message))

    def reset_progress(self, message: str = "Ready"):
        def apply():
            if self.progress_bar is not None:
                self.progress_bar.configure(value=0)
            self.progress_text_var.set("")
            self.status_var.set(message)
        self.root.after(0, apply)

    def _file_name_sort_key(self, path: str) -> Tuple[str, str]:
        return _path_filename_sort_key(path)

    def _sort_panel_files_by_name(self, panel: str):
        self.panel_files[panel].sort(key=self._file_name_sort_key)

    # Backwards-compatible aliases for any internal/older calls.
    # Behavior is intentionally timestamp ascending from filename, not Date Modified.
    def _file_modified_sort_key(self, path: str) -> Tuple[str, str]:
        return self._file_name_sort_key(path)

    def _sort_panel_files_by_modified(self, panel: str):
        self._sort_panel_files_by_name(panel)

    def _file_cache_signature(self, path: str) -> Tuple[str, int, int]:
        # Keep modified time in the cache signature for correctness: if an STDF
        # file is edited/replaced while keeping the same filename, the cached
        # analysis/test scan must be invalidated. This is not used for sorting.
        try:
            stat = os.stat(path)
            modified_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
            return (os.path.abspath(path), int(stat.st_size), int(modified_ns))
        except OSError:
            return (os.path.abspath(path), -1, -1)

    def _build_analysis_cache_key(
        self,
        lot_id_text: str,
        mpc_text: str,
        fp_actual_good_qty_text: str,
        total_actual_good_qty_text: str,
        manual_filter_tests: Optional[Set[int]],
    ) -> Tuple[object, ...]:
        panel_signatures = tuple(
            (panel, tuple(self._file_cache_signature(path) for path in self.panel_files[panel]))
            for panel in self.PANELS
        )
        return (
            "analysis-cache-v25",
            str(lot_id_text or "").strip(),
            str(mpc_text or "").strip(),
            str(fp_actual_good_qty_text or "").strip(),
            str(total_actual_good_qty_text or "").strip(),
            tuple(sorted(manual_filter_tests or [])),
            panel_signatures,
        )

    def refresh_file_list(self, panel: str):
        self._sort_panel_files_by_name(panel)
        listbox = self.panel_listboxes[panel]
        listbox.delete(0, "end")
        for path in self.panel_files[panel]:
            listbox.insert("end", path)
        count = len(self.panel_files[panel])
        if hasattr(self, "_panel_count_vars") and panel in self._panel_count_vars:
            self._panel_count_vars[panel].set(f"{count} file{'s' if count != 1 else ''}")
        if hasattr(self, "_panel_badges") and panel in self._panel_badges:
            badge = self._panel_badges[panel]
            if count > 0:
                badge.configure(bg="#DCFCE7", fg="#15803D")
            else:
                badge.configure(bg="#CBD5E1", fg="#334155")
        if hasattr(self, "_update_panel_scrollbar"):
            self._update_panel_scrollbar(panel)

    def refresh_all_file_lists(self):
        for panel in self.PANELS:
            self.refresh_file_list(panel)

    def select_files(self, panel: str):
        from tkinter import filedialog
        from  stdf_fetcher import LOCAL_DEST_BASE

        # Default to the FTDC extraction folder for the current Lot ID
        initial_dir = None
        lot_id = self.lot_id_var.get().strip()
        if lot_id:
            candidate = os.path.join(LOCAL_DEST_BASE, lot_id.upper())
            if os.path.isdir(candidate):
                initial_dir = candidate

        paths = filedialog.askopenfilenames(
            title=f"Select one or more {panel} STDF files",
            filetypes=[("STDF files", "*.std* *.old"), ("All files", "*.*")],
            initialdir=initial_dir,
        )
        if not paths:
            return
        added = 0
        for path in paths:
            if path not in self.panel_files[panel]:
                self.panel_files[panel].append(path)
                added += 1
        self.refresh_file_list(panel)
        self.log(f"[{panel}] Added {added} file(s). Total loaded: {len(self.panel_files[panel])}")

    def remove_selected_files(self, panel: str):
        indices = list(self.panel_listboxes[panel].curselection())
        if not indices:
            return
        for idx in sorted([int(i) for i in indices], reverse=True):
            del self.panel_files[panel][idx]
        self.refresh_file_list(panel)
        self.log(f"[{panel}] Removed {len(indices)} selected file(s).")

    def clear_files(self, panel: str):
        count = len(self.panel_files[panel])
        self.panel_files[panel].clear()
        self.refresh_file_list(panel)
        self.log(f"[{panel}] Cleared {count} file(s).")

    def clear_all(self):
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, self.tk.END)
        self.log_text.config(state="disabled")
        self.progress_bar.configure(value=0)
        self.status_var.set("Ready")
        self.progress_text_var.set("")
        
        self.lot_id_var.set("")
        self.mpc_var.set("")
        self.fp_actual_good_qty_var.set("")
        self.total_actual_good_qty_var.set("")
        
        self.manual_filter_var.set(False)
        self.tests_var.set("")
        self.selected_tests_var.set("")
        self.range_from_var.set("")
        self.range_to_var.set("")
        self._selected_test_count_var.set("No test parameters selected")
        self._sync_manual_filter_state()
        
        for panel in self.PANELS:
            self.panel_files[panel].clear()
            self.refresh_file_list(panel)
            if hasattr(self, "_panel_count_vars") and panel in self._panel_count_vars:
                self._panel_count_vars[panel].set("0 files")
            if hasattr(self, "_panel_badges") and panel in self._panel_badges:
                self._panel_badges[panel].configure(bg="#CBD5E1", fg="#334155")
            if hasattr(self, "_update_panel_scrollbar"):
                self._update_panel_scrollbar(panel)

    def _update_selected_test_summary(self):
        count = len([x.strip() for x in self.selected_tests_var.get().split(",") if x.strip()])
        if count == 0:
            self._selected_test_count_var.set("No test parameters selected")
        elif count == 1:
            self._selected_test_count_var.set("1 test parameter selected")
        else:
            self._selected_test_count_var.set(f"{count} test parameters selected")
        self._sync_clear_selected_tests_button(count > 0)

    def _sync_clear_selected_tests_button(self, has_selected_tests: bool):
        if self.clear_selected_tests_button is None:
            return
        if has_selected_tests:
            self.clear_selected_tests_button.grid()
            self.clear_selected_tests_button.configure(state=("normal" if self.manual_filter_var.get() and not self.is_running and not self.is_scanning_tests else "disabled"))
        else:
            self.clear_selected_tests_button.grid_remove()

    def _build_test_cache_key(self, file_path: str) -> Optional[Tuple[str, int, int]]:
        try:
            stat = os.stat(file_path)
        except OSError:
            return None
        return (os.path.abspath(file_path), stat.st_size, getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))

    def _scan_tests_for_file(self, file_path: str, progress_callback: ByteProgressFunc = None) -> List[Dict[str, object]]:
        key = self._build_test_cache_key(file_path)
        cached = self._test_scan_cache.get(key) if key else None
        if cached is not None:
            self.log(f"Using cached test list for: {file_path}")
            return cached
        tests = scan_test_list(file_path, progress_callback=progress_callback)
        if key:
            self._test_scan_cache[key] = tests
        return tests

    def _scan_tests_for_selected_files(self, progress_callback: ProgressFunc = None) -> Tuple[List[Dict[str, object]], str]:
        files = self._get_all_selected_files()
        rows: List[Dict[str, object]] = []
        _sizes = []
        for _p in files:
            try:
                _sizes.append(max(os.path.getsize(_p), 1))
            except OSError:
                _sizes.append(1)
        total = sum(_sizes) or 1
        done = 0
        names = []
        for _fi, path in enumerate(files):
            size = _sizes[_fi]
            name = os.path.basename(path)
            names.append(name)
            status_text = f"Scanning tests: {_count_files_text(len(files))}"

            def file_progress(current_bytes: int, file_total: int, base=done):
                if not progress_callback:
                    return
                total_for_file = file_total if file_total > 0 else size
                progress_callback((base + min(max(current_bytes, 0), total_for_file)) / total, status_text)
            rows.extend(self._scan_tests_for_file(path, progress_callback=file_progress))
            done += size
            if progress_callback:
                progress_callback(done / total, status_text)
        source = ", ".join(names[:3]) + (f" + {len(names)-3} more" if len(names) > 3 else "")
        return _merge_test_rows(rows), source

    def _set_test_scan_running(self, running: bool):
        self.is_scanning_tests = running
        def apply():
            if running:
                self._start_progress_polling()
            else:
                self._stop_progress_polling()
            self._sync_manual_filter_state()
        self.root.after(0, apply)

    def _open_test_window(self, tests: List[Dict[str, object]], source_label: str):
        import tkinter as tk
        if self._test_window and self._test_window.winfo_exists():
            self._test_window.destroy()
        self._cached_tests = list(tests)
        self._test_window = tk.Toplevel(self.root)
        self._test_window.title("Show All Test - Combined STDF Selection")
        self._test_window.geometry("980x560")
        self._test_window.minsize(820, 420)

        container = self.ttk.Frame(self._test_window, padding=10)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)
        self.ttk.Label(container, text=f"Source: {source_label}   |   Total tests: {len(tests)}").grid(row=0, column=0, sticky="w", pady=(0, 8))
        filter_row = self.ttk.Frame(container)
        filter_row.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        filter_row.columnconfigure(1, weight=1)
        self.ttk.Label(filter_row, text="Filter").grid(row=0, column=0, sticky="w")
        self._test_filter_var = tk.StringVar()
        entry = self.ttk.Entry(filter_row, textvariable=self._test_filter_var)
        entry.grid(row=0, column=1, sticky="ew", padx=(8, 10))
        buttons = self.ttk.Frame(filter_row)
        buttons.grid(row=0, column=2, sticky="e")
        ModernHoverButton(buttons, text="Select", command=self._apply_selected_tests_from_window, padx=10, pady=3).pack(side="left", padx=(0, 6))
        ModernHoverButton(buttons, text="Clear Selected Tests", command=self._clear_selected_test_filter, padx=10, pady=3).pack(side="left")
        self.ttk.Label(container, text='Type text like "Wafer" to filter matching parameters', foreground="#555555").grid(row=2, column=0, sticky="w", pady=(0, 8))
        entry.bind("<KeyRelease>", lambda _event: self._refresh_test_tree())
        entry.focus_set()

        table = self.ttk.Frame(container)
        table.grid(row=3, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ("TEST_NUM", "TEST_TXT", "LO_LIMIT", "HI_LIMIT", "UNITS")
        self._test_tree = self.ttk.Treeview(table, columns=columns, show="headings", selectmode="extended")
        self._test_tree.grid(row=0, column=0, sticky="nsew")
        widths = {"TEST_NUM": 110, "TEST_TXT": 420, "LO_LIMIT": 120, "HI_LIMIT": 120, "UNITS": 120}
        for col in columns:
            self._test_tree.heading(col, text=col)
            self._test_tree.column(col, width=widths[col], anchor="w")
        yscroll = self.ttk.Scrollbar(table, orient="vertical", command=self._test_tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self._test_tree.configure(yscrollcommand=yscroll.set)
        xscroll = self.ttk.Scrollbar(table, orient="horizontal", command=self._test_tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self._test_tree.configure(xscrollcommand=xscroll.set)
        self._refresh_test_tree()

    def _refresh_test_tree(self):
        if not self._test_tree:
            return
        keyword = self._test_filter_var.get().strip().lower() if self._test_filter_var else ""
        raw_sel = [x.strip() for x in self.selected_tests_var.get().split(",") if x.strip()]
        selected = set()
        for s in raw_sel:
            m = re.match(r"^(\d+)", s)
            if m:
                selected.add(m.group(1))
            else:
                tok = s.split()[0] if s.split() else ""
                if tok:
                    selected.add(tok)
        children = self._test_tree.get_children()
        if children:
            self._test_tree.delete(*children)
        for test in self._cached_tests:
            if keyword and keyword not in test.get("_SEARCH_TEXT", ""):
                continue
            item_id = self._test_tree.insert(
                "", "end",
                values=(
                    test.get("TEST_NUM", ""),
                    test.get("TEST_TXT", ""),
                    test.get("_LO_LIMIT_TEXT", _format_limit_text(test.get("LO_LIMIT"))),
                    test.get("_HI_LIMIT_TEXT", _format_limit_text(test.get("HI_LIMIT"))),
                    test.get("UNITS", ""),
                ),
            )
            if str(test.get("TEST_NUM", "")) in selected:
                self._test_tree.selection_add(item_id)

    def _apply_selected_tests_from_window(self):
        from tkinter import messagebox
        if not self._test_tree:
            return
        items = self._test_tree.selection()
        if not items:
            messagebox.showwarning("STDF GUID Checker", "Please select at least one test from the list.")
            return
        formatted_tests, seen = [], set()
        for item in items:
            values = self._test_tree.item(item, "values")
            if values:
                t_num = str(values[0]).strip()
                t_txt = str(values[1]).strip() if len(values) > 1 else ""
                if t_num and t_num not in seen:
                    seen.add(t_num)
                    if t_txt:
                        formatted_tests.append(f"{t_num}_{t_txt}")
                    else:
                        formatted_tests.append(t_num)
        self.selected_tests_var.set(", ".join(formatted_tests))
        self._update_selected_test_summary()
        self.log(f"Applied {len(formatted_tests)} selected test parameter(s) from Test Parameter Filter.")
        self.set_status("Test parameter selection updated")
        if self._test_window and self._test_window.winfo_exists():
            self._test_window.destroy()
            self._test_window = None
            self._test_tree = None
            self._test_filter_var = None

    def _clear_selected_test_filter(self):
        self.selected_tests_var.set("")
        if self._test_tree:
            for item in self._test_tree.selection():
                self._test_tree.selection_remove(item)
        self._update_selected_test_summary()
        self.log("Cleared test parameter selection.")
        self.set_status("Test parameter selection cleared")
        self._sync_manual_filter_state()

    def show_all_tests(self):
        from tkinter import messagebox
        if self.is_scanning_tests:
            return
        if not self.manual_filter_var.get():
            self.manual_filter_var.set(True)
            self._sync_manual_filter_state()
        if self.is_running:
            messagebox.showwarning("STDF GUID Checker", "Analysis is currently running.")
            return
        all_files = self._get_all_selected_files()
        if not all_files:
            messagebox.showwarning("STDF GUID Checker", "Please select at least one STDF file first.")
            return
        self.log(f"Scanning test parameters from {len(all_files)} selected file(s)...")
        self.update_progress(0.0, "Scanning tests")
        self._set_test_scan_running(True)

        def worker():
            try:
                tests, source = self._scan_tests_for_selected_files(progress_callback=self.update_progress)
                self.update_progress(1.0, "Show All Test ready")
                self.log(f"Found {len(tests)} unique test parameter(s).")
                self.set_status("Select test parameters to use as manual filter")
                self.root.after(0, lambda: self._open_test_window(tests, source))
            except Exception as exc:
                self.log(f"Error while scanning tests: {exc}")
                self.reset_progress("Test scan failed")
                self.root.after(0, lambda: messagebox.showerror("STDF GUID Checker", str(exc)))
            finally:
                self._set_test_scan_running(False)

        threading.Thread(target=worker, daemon=True).start()

    def _show_result_popup(self, result: Dict[str, object]):
        from tkinter import messagebox

        tk = self.tk
        ttk = self.ttk

        summary_sections, detail_rows, fp_status, total_status, _, ftdc_criteria = build_summary_and_details(result)
        fmt = _fmt

        def download_excel():
            try:
                import xlsxwriter
            except ImportError:
                messagebox.showerror(
                    "Missing Dependency",
                    "The 'xlsxwriter' package is required for Excel export.\n\n"
                    "Install it with:  pip install xlsxwriter",
                    parent=window,
                )
                return

            lot_text = str(result.get("lot_id") or "STDF_GUID_Result").strip() or "STDF_GUID_Result"
            safe_lot = re.sub(r"[^A-Za-z0-9_-]+", "_", lot_text).strip("_") or "STDF_GUID_Result"
            default_name = f"{safe_lot}_FTDC_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"

            # Derive {mask} = first 5 non-space characters of MPC value
            mpc_raw = str(result.get("mpc") or "").strip()
            mask = "".join(mpc_raw.split())[:5]
            # Derive {lot_id} = capitalized Lot ID
            lot_id_upper = str(result.get("lot_id") or "").strip().upper()

            if not mask or not lot_id_upper:
                messagebox.showerror(
                    "Download Excel Failed",
                    "Cannot determine download path: MPC or Lot ID is missing.",
                    parent=window,
                )
                return

            base_path = os.path.join(r"\\chip\PPOonline", mask, lot_id_upper)

            # Check if network base_path exists and has subfolders
            subfolders = []
            if os.path.isdir(base_path):
                try:
                    subfolders = [
                        os.path.join(base_path, d)
                        for d in os.listdir(base_path)
                        if os.path.isdir(os.path.join(base_path, d))
                    ]
                except OSError:
                    subfolders = []

            if not subfolders:
                from tkinter import filedialog
                reason = "does not exist" if not os.path.isdir(base_path) else "has no subfolders"
                prompt_text = (
                    f"The target network folder {reason}:\n\n{base_path}\n\n"
                    "Would you like to save the Excel file to your local computer instead?"
                )
                if messagebox.askyesno("Save Excel Locally?", prompt_text, parent=window):
                    save_dest = filedialog.asksaveasfilename(
                        parent=window,
                        title="Save FTDC Result Excel",
                        initialfile=default_name,
                        defaultextension=".xlsx",
                        filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")],
                    )
                    if save_dest:
                        try:
                            write_result_excel(result, save_dest)
                            messagebox.showinfo(
                                "Download Excel",
                                f"Result Excel saved successfully to:\n\n{save_dest}",
                                parent=window,
                            )
                        except Exception as exc:
                            messagebox.showerror(
                                "Download Excel Failed",
                                f"Failed to save Excel file:\n\n{exc}",
                                parent=window,
                            )
                return

            # Write once to a local temp file, then copy in parallel — much
            # faster than regenerating the Excel workbook for every subfolder.
            import shutil
            import tempfile

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
            os.close(tmp_fd)
            try:
                write_result_excel(result, tmp_path)
            except Exception as exc:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                messagebox.showerror("Download Excel Failed", str(exc), parent=window)
                return

            def _copy_one(folder):
                dest = os.path.join(folder, default_name)
                shutil.copy2(tmp_path, dest)
                return dest

            saved_paths = []
            errors = []
            # Parallel copy — network I/O is the bottleneck, so threads help a lot
            with ThreadPoolExecutor(max_workers=min(len(subfolders), 8)) as pool:
                futures = {pool.submit(_copy_one, f): f for f in subfolders}
                for fut in futures:
                    folder = futures[fut]
                    try:
                        saved_paths.append(fut.result())
                    except Exception as exc:
                        errors.append(f"{folder}: {exc}")

            try:
                os.remove(tmp_path)
            except OSError:
                pass

            if saved_paths and not errors:
                paths_text = "\n".join(saved_paths)
                messagebox.showinfo(
                    "Download Excel",
                    f"Result Excel saved successfully to {len(saved_paths)} subfolder(s):\n\n{paths_text}",
                    parent=window,
                )
            elif saved_paths and errors:
                paths_text = "\n".join(saved_paths)
                errors_text = "\n".join(errors)
                messagebox.showwarning(
                    "Download Excel",
                    f"Saved to {len(saved_paths)} subfolder(s):\n{paths_text}\n\n"
                    f"Failed in {len(errors)} subfolder(s):\n{errors_text}",
                    parent=window,
                )
            else:
                errors_text = "\n".join(errors)
                messagebox.showerror(
                    "Download Excel Failed",
                    f"Failed to save in all subfolders:\n\n{errors_text}",
                    parent=window,
                )

        window = tk.Toplevel(self.root)
        window.title(f"FTDC Result - {result.get('lot_id', '')}")
        window.transient(self.root)
        window.minsize(760, 180)
        window.columnconfigure(0, weight=1)

        container = ttk.Frame(window, padding=(10, 6, 10, 6))
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)

        def status_color(value: object) -> str:
            text = str(value or "").strip().upper()
            if text == "PASS":
                return "#008000"
            if text == "FAIL":
                return "#C00000"
            return "#000000"

        title_text = f"Automatic FTDC Checker {APP_VERSION}"
        title = tk.Label(container, text=title_text, font=("Segoe UI", 11, "bold"), anchor="w", fg="#000000")
        title.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        panels = ttk.Frame(container)
        panels.grid(row=1, column=0, sticky="nsew")
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)

        for col, (section_title, rows) in enumerate(summary_sections):
            box = tk.Frame(panels, borderwidth=1, relief="solid", bg="#E0E0E0")
            box.grid(row=0, column=col, sticky="nsew", padx=(0, 5) if col == 0 else (5, 0))
            box.columnconfigure(0, weight=1)

            header = tk.Label(box, text=section_title, font=("Segoe UI", 10, "bold"), bg="#F0F0F0", anchor="w", padx=8, pady=4)
            header.grid(row=0, column=0, sticky="ew")

            table = tk.Frame(box, bg="#E0E0E0")
            table.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
            table.columnconfigure(0, weight=1)
            table.columnconfigure(1, weight=0)
            for r, (metric, value) in enumerate(rows):
                value_text = fmt(value)
                value_fg = "#000000"
                if metric == "Result":
                    value_fg = status_color(value_text)
                elif "Difference between Good" in metric:
                    try:
                        num_val = float(str(value).replace(',', ''))
                        value_fg = "#008000" if num_val >= 0 else "#C00000"
                    except (ValueError, TypeError):
                        pass

                metric_label = tk.Label(
                    table, text=f"{metric}:", font=("Segoe UI", 9), bg="white",
                    anchor="w", padx=7, pady=4, borderwidth=1, relief="solid"
                )
                metric_label.grid(row=r, column=0, sticky="ew")
                value_label = tk.Label(
                    table, text=value_text, font=("Segoe UI", 9, "bold"), fg=value_fg, bg="white",
                    anchor="e", padx=7, pady=4, borderwidth=1, relief="solid"
                )
                value_label.grid(row=r, column=1, sticky="ew")

        details_frame = ttk.LabelFrame(container, text="Details")
        details_frame.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)
        details_text = tk.Text(details_frame, height=10, wrap="word")
        details_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=5)
        details_scroll = ttk.Scrollbar(details_frame, orient="vertical", command=details_text.yview)
        details_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=5)
        details_text.configure(yscrollcommand=details_scroll.set)
        details_text.insert("1.0", build_details_text(detail_rows, ftdc_criteria=ftdc_criteria, fmt=fmt))
        details_text.configure(state="disabled")
        details_frame.grid_remove()

        button_row = ttk.Frame(container)
        button_row.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        button_row.columnconfigure(0, weight=1)

        details_visible = {"value": False}

        def _resize_result_window(width: int = 860):
            window.update_idletasks()
            try:
                natural_height = container.winfo_reqheight() + 6
                x = self.root.winfo_rootx() + max((self.root.winfo_width() - width) // 2, 0)
                y = self.root.winfo_rooty() + max((self.root.winfo_height() - natural_height) // 2, 0)
                window.geometry(f"{width}x{natural_height}+{x}+{y}")
            except Exception:
                window.geometry(f"{width}x{container.winfo_reqheight() + 6}")

        def toggle_details():
            details_visible["value"] = not details_visible["value"]
            if details_visible["value"]:
                details_frame.grid()
                show_details_button.configure(text="Hide Details")
            else:
                details_frame.grid_remove()
                show_details_button.configure(text="Show Details")
            _resize_result_window()

        show_details_button = ModernHoverButton(button_row, text="Show Details", command=toggle_details, padx=10, pady=4)
        show_details_button.grid(row=0, column=1, sticky="e", padx=(0, 8))
        ModernHoverButton(button_row, text="Download Excel", command=download_excel, padx=10, pady=4).grid(row=0, column=2, sticky="e", padx=(0, 8))
        ModernHoverButton(button_row, text="Close", command=window.destroy, padx=10, pady=4).grid(row=0, column=3, sticky="e")

        _resize_result_window()

    def _prompt_selection(self, title: str, prompt: str, options: List[str]) -> Optional[str]:
        """Display a modal dialog with buttons for each option and return the chosen option."""
        import tkinter as tk
        from tkinter import ttk
        
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        
        dialog.withdraw()  # Hide momentarily while setting up
        
        selected = [None]
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        
        lbl = ttk.Label(
            frame, 
            text=prompt, 
            font=("Segoe UI", 10, "bold"), 
            wraplength=350, 
            justify="center"
        )
        lbl.pack(pady=(0, 15))
        
        def _on_select(opt):
            selected[0] = opt
            dialog.destroy()
            
        for opt in options:
            btn = ttk.Button(frame, text=opt, command=lambda o=opt: _on_select(o))
            btn.pack(fill="x", pady=5)
            
        cancel_btn = ttk.Button(frame, text="Cancel", command=dialog.destroy)
        cancel_btn.pack(fill="x", pady=(15, 0))
        
        dialog.update_idletasks()
        try:
            parent_x = self.root.winfo_rootx()
            parent_y = self.root.winfo_rooty()
            parent_w = self.root.winfo_width()
            parent_h = self.root.winfo_height()
            
            w = dialog.winfo_reqwidth()
            h = dialog.winfo_reqheight()
            x = parent_x + (parent_w - w) // 2
            y = parent_y + (parent_h - h) // 2
            dialog.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass
            
        dialog.deiconify()
        dialog.focus_force()
        self.root.wait_window(dialog)
        return selected[0]

    # ── MPC Auto-Lookup ────────────────────────────────────────────────────

    # Lot IDs are usually SCANNED (arrive as one complete paste) but are
    # sometimes hand-typed. We balance the two: a very short debounce when the
    # value already looks like a complete lot id (scan/paste), a slightly longer
    # one while it still looks partially typed.
    _LOT_ID_COMPLETE_RE = re.compile(r"^[A-Za-z]{2,}[-\s]?\d{6,}(?:\.\d+)?$")

    def _looks_like_complete_lot(self, lot_id: str) -> bool:
        """Heuristic: does this look like a finished lot id (scanned/pasted)?
        Used only to pick the debounce delay; never blocks a lookup."""
        return bool(self._LOT_ID_COMPLETE_RE.match(lot_id.strip()))

    def _get_http_session(self):
        """Return a shared requests.Session (keep-alive) or None if requests is
        unavailable. Created lazily and reused across all MPC lookups so the
        TCP/DNS handshake is paid once, not on every keystroke-triggered call."""
        with self._http_session_lock:
            if self._http_session is not None:
                return self._http_session
            try:
                import requests as _requests
            except ImportError:
                return None
            try:
                sess = _requests.Session()
                # A small connection pool is plenty for single-user lookups.
                adapter = _requests.adapters.HTTPAdapter(
                    pool_connections=2, pool_maxsize=4, max_retries=0
                )
                sess.mount("http://", adapter)
                sess.mount("https://", adapter)
                self._http_session = sess
            except Exception:
                self._http_session = None
            return self._http_session

    def _prewarm_mpc_connection(self):
        """Fix 6: open the TCP/DNS connection to the MES host at startup in the
        background so the FIRST real lookup does not pay cold setup. Best-effort;
        any failure is silently ignored (the field still works on first use)."""
        def _warm():
            sess = self._get_http_session()
            if sess is None:
                return
            try:
                # A cheap HEAD/GET just to establish the socket into the pool.
                # Short timeout; we don't care about the response body/status.
                sess.get(self._mpc_lookup_url, timeout=(1.5, 2.5))
            except Exception:
                pass
        threading.Thread(target=_warm, daemon=True).start()

    def _on_lot_id_changed(self, *args):
        """Debounce handler. Waits a short, adaptive delay after the last change
        before looking up the MPC (shorter for scanned/complete lot ids)."""
        if self._mpc_lookup_after_id is not None:
            self.root.after_cancel(self._mpc_lookup_after_id)
            self._mpc_lookup_after_id = None

        lot_id = self.lot_id_var.get().strip()
        if not lot_id:
            self.mpc_var.set("")
            return

        # Fix 1: instant cache hit — no network, no debounce wait.
        cached = self._mpc_cache.get(lot_id.upper())
        if cached is not None:
            self.mpc_var.set(cached)
            return

        # Fix 3: adaptive debounce. Scanned/complete -> snappy; typing -> a bit
        # longer so we don't fire on every intermediate keystroke.
        delay = 150 if self._looks_like_complete_lot(lot_id) else 350
        self._mpc_lookup_after_id = self.root.after(
            delay, lambda: self._lookup_mpc_for_lot_id(lot_id)
        )

    def _lookup_mpc_for_lot_id(self, lot_id: str):
        """Look up the MPC for a lot id via the MES web service and populate the
        MPC field. Uses a shared keep-alive session, an in-memory cache, and a
        request-sequence guard so a slow old call can't overwrite a newer one."""
        self._mpc_lookup_after_id = None

        # Fix 1: re-check the cache (the value may have arrived during debounce).
        cached = self._mpc_cache.get(lot_id.upper())
        if cached is not None:
            self.mpc_var.set(cached)
            return

        # Fix 5: immediate visual feedback while the network call runs.
        if self.lot_id_var.get().strip().upper() == lot_id.upper():
            self.mpc_var.set("Looking up…")

        # Sequence guard: only the newest request may write the field.
        self._mpc_lookup_seq += 1
        my_seq = self._mpc_lookup_seq

        def worker():
            sess = self._get_http_session()
            if sess is None:
                return

            def _set_val(val: str, cache: bool = False):
                # Only apply if (a) this is still the newest lookup, and
                # (b) the user hasn't changed/cleared the Lot ID meanwhile.
                if my_seq != self._mpc_lookup_seq:
                    return
                if self.lot_id_var.get().strip().upper() != lot_id.upper():
                    return
                if cache:
                    self._mpc_cache[lot_id.upper()] = val
                self.mpc_var.set(val)

            # Fix 4: split (connect, read) timeout — fail fast on a dead host,
            # still allow a slow-but-alive server to answer.
            try:
                resp = sess.post(
                    self._mpc_lookup_url, data={"LotId": lot_id}, timeout=(2, 8)
                )
            except Exception as exc:
                # Broad except so any requests error class is handled uniformly
                # (ConnectionError/Timeout/etc.), then show a short message.
                name = type(exc).__name__
                if "Timeout" in name:
                    self.root.after(0, lambda: _set_val("Network error: request timed out"))
                elif "Connection" in name:
                    self.root.after(0, lambda: _set_val("Network error: unable to connect"))
                else:
                    msg = str(exc)[:60]
                    self.root.after(0, lambda m=msg: _set_val(f"Lookup failed: {m}"))
                return

            # ── Parse the XML response ─────────────────────────────────────
            try:
                root_el = ET.fromstring(resp.text)
                ns = {"mes": "http://microchip.com/mes/"}

                reply_code_el = root_el.find("mes:ReplyCode", ns)
                reply_code = reply_code_el.text.strip() if reply_code_el is not None and reply_code_el.text else ""

                if reply_code == "-1":
                    self.root.after(0, lambda: _set_val("Lot ID not found"))
                    return

                mpc_el = root_el.find(".//mes:MPC", ns)
                if mpc_el is not None and mpc_el.text and mpc_el.text.strip():
                    mpc_value = mpc_el.text.strip()
                    # Fix 1: cache successful resolutions for instant repeats.
                    self.root.after(0, lambda v=mpc_value: _set_val(v, cache=True))
                else:
                    self.root.after(0, lambda: _set_val(""))
            except ET.ParseError:
                self.root.after(0, lambda: _set_val("Lookup failed: invalid response"))
            except Exception as exc:
                msg = str(exc)[:60]
                self.root.after(0, lambda m=msg: _set_val(f"Lookup failed: {m}"))

        threading.Thread(target=worker, daemon=True).start()


    # ── Check STDF ─────────────────────────────────────────────────────────

    def _prompt_panel_choice(self, parent=None):
        """Show a small popup with FIRST PASS / RETEST / QC buttons and return the choice."""
        import tkinter as tk

        dialog = tk.Toplevel(parent or self.root)
        dialog.title("Select Panel")
        dialog.resizable(False, False)
        dialog.transient(parent or self.root)
        dialog.grab_set()
        dialog.withdraw()

        selected: List[Optional[str]] = [None]

        frame = self.ttk.Frame(dialog, padding=15)
        frame.pack(fill="both", expand=True)

        self.ttk.Label(
            frame, text="Load file into which panel?",
            font=("Segoe UI", 9),
        ).pack(pady=(0, 10))

        btn_row = self.ttk.Frame(frame)
        btn_row.pack()

        for panel in ("FIRST PASS", "RETEST", "QC"):
            def _on_click(p=panel):
                selected[0] = p
                dialog.destroy()
            self.ttk.Button(btn_row, text=panel, command=_on_click).pack(
                side="left", padx=4,
            )

        dialog.update_idletasks()
        # Center over parent
        pw = (parent or self.root).winfo_rootx()
        ph = (parent or self.root).winfo_rooty()
        px = (parent or self.root).winfo_width()
        py = (parent or self.root).winfo_height()
        dw = dialog.winfo_reqwidth()
        dh = dialog.winfo_reqheight()
        x = pw + (px - dw) // 2
        y = ph + (py - dh) // 2
        dialog.geometry(f"+{x}+{y}")
        dialog.deiconify()
        dialog.focus_force()
        (parent or self.root).wait_window(dialog)
        return selected[0]

    def start_check_stdf(self):
        """Open a mini-GUI showing STDF files in the local FTDC folder with integrity status.

        The window opens INSTANTLY and file discovery + parsing happens in the
        background so the user gets immediate visual feedback.
        """
        from tkinter import messagebox
        from  stdf_fetcher import LOCAL_DEST_BASE, STDF_EXTENSIONS

        lot_id_val = self.lot_id_var.get().strip().upper()
        if not lot_id_val:
            messagebox.showwarning(
                "Check STDF",
                "Please enter a Lot ID first.",
                parent=self.root,
            )
            return

        folder_path = os.path.join(LOCAL_DEST_BASE, lot_id_val)

        # Open window immediately — all heavy work happens inside it.
        self._open_check_stdf_window(lot_id_val, folder_path, STDF_EXTENSIONS)

    def _open_check_stdf_window(
        self, lot_id: str, folder_path: str, stdf_extensions: set
    ):
        """Build the Check STDF mini-GUI and kick off background file discovery + parsing.

        The window opens instantly with a 'Scanning folder…' banner. File rows
        are added live as the background thread discovers and parses each STDF.
        """
        import tkinter as tk
        from tkinter import messagebox
        from  stdf_parser import parse_check_summary

        win = tk.Toplevel(self.root)
        win.title(f"Check STDF \u2014 {lot_id}")
        win.geometry("960x490")
        win.minsize(880, 360)
        win.transient(self.root)

        container = self.ttk.Frame(win, padding=10)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        # ── Path label ─────────────────────────────────────────────────────
        self.ttk.Label(
            container,
            text=f"STDF Files in: {folder_path}",
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        # ── List area (header + scrollable canvas) ─────────────────────────
        list_frame = self.ttk.Frame(container)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)
        col_weights = [48, 11, 11, 11, 19]
        header_texts = ["File Name", "Total Parts", "Tested Good", "Status", "Action"]

        # ── Column header row ──────────────────────────────────────────────
        header_frame = tk.Frame(list_frame, bg="#E0E0E0")
        header_frame.grid(row=0, column=0, sticky="ew")
        for ci, (txt, wt) in enumerate(zip(header_texts, col_weights)):
            header_frame.columnconfigure(ci, weight=wt, uniform="col")
            align = "w" if ci == 0 else "center"
            px = 8 if ci == 0 else 4
            tk.Label(
                header_frame, text=txt,
                font=("Segoe UI", 9, "bold"), bg="#F0F0F0", fg="#0F172A",
                anchor=align, padx=px, pady=4,
            ).grid(row=0, column=ci, sticky="nsew", padx=(0, 1), pady=(0, 1))

        # ── Scrollable canvas ──────────────────────────────────────────────
        canvas = tk.Canvas(
            list_frame, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E0E0E0", borderwidth=0,
        )
        canvas.grid(row=1, column=0, sticky="nsew")
        v_scroll = self.ttk.Scrollbar(
            list_frame, orient="vertical", command=canvas.yview,
        )
        v_scroll.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=v_scroll.set)

        inner = tk.Frame(canvas, bg="#E0E0E0")
        canvas_win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_canvas_cfg(event):
            canvas.itemconfig(canvas_win_id, width=event.width)
        canvas.bind("<Configure>", _on_canvas_cfg)

        def _on_inner_cfg(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_cfg)

        # Mouse-wheel scrolling (scoped to canvas hover)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mw(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mw(_event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mw)
        canvas.bind("<Leave>", _unbind_mw)

        for ci, wt in enumerate(col_weights):
            inner.columnconfigure(ci, weight=wt, uniform="col")

        # ── Scanning status banner (shown while discovering files) ─────────
        scan_frame = tk.Frame(inner, bg="#FFFFFF")
        scan_frame.grid(row=0, column=0, columnspan=5, sticky="ew", padx=0, pady=(8, 8))
        scan_frame.columnconfigure(0, weight=1)

        scan_inner = tk.Frame(scan_frame, bg="#FFFFFF")
        scan_inner.grid(row=0, column=0, sticky="")

        scan_pbar = self.ttk.Progressbar(
            scan_inner, mode="indeterminate", length=80,
        )
        scan_pbar.pack(side="left", padx=(0, 8))
        scan_pbar.start(15)

        scan_lbl = tk.Label(
            scan_inner, text="Scanning folder for STDF files…",
            font=("Segoe UI", 9), bg="#FFFFFF", fg="#888888",
        )
        scan_lbl.pack(side="left")

        # ── Close button ───────────────────────────────────────────────────
        btn_frame = self.ttk.Frame(container)
        btn_frame.grid(row=2, column=0, sticky="e", pady=(8, 0))
        ModernHoverButton(
            btn_frame, text="Close", command=lambda: _on_win_close(), padx=12, pady=4,
        ).pack(side="right")

        # ── FORCE INSTANT PAINT OF WINDOW, HEADERS AND SPINNER ─────────────
        # Force Tkinter to draw the HWND, title, path label, column headers,
        # scrollbars, Close button, and scan spinner IMMEDIATELY so the user
        # never sees a white/blank unrendered window.
        try:
            win.update_idletasks()
            win.update()
        except Exception:
            pass

        # ── Shared state ──────────────────────────────────────────────────
        cell_font = ("Segoe UI", 9)
        row_widgets: Dict[str, Dict] = {}
        _STDF_MISSING = 4294967295  # 0xFFFFFFFF — STDF "missing value"
        _next_row = [0]  # mutable counter for the next grid row in `inner`
        _executor_ref = [None]  # will hold the ThreadPoolExecutor
        _owns_running = [False]
        _remaining = [0]
        _remaining_lock = threading.Lock()
        _win_closed = [False]

        # ── Row builder (called on main thread to add a file row) ─────────
        def _add_file_row(filename: str, filepath_full: str):
            """Add a new row to the grid for a discovered file. Returns the widgets dict."""
            ri = _next_row[0]
            _next_row[0] += 1
            bg = "#FFFFFF"

            # col 0 — File Name
            fn_lbl = tk.Label(
                inner, text=filename, font=cell_font, bg=bg,
                anchor="w", padx=8, pady=3,
            )
            fn_lbl.grid(row=ri, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))

            # col 1 — Total Parts
            tp_lbl = tk.Label(
                inner, text="--", font=cell_font, bg=bg,
                anchor="center", padx=4, pady=3,
            )
            tp_lbl.grid(row=ri, column=1, sticky="nsew", padx=(0, 1), pady=(0, 1))

            # col 2 — Tested Good
            tg_lbl = tk.Label(
                inner, text="--", font=cell_font, bg=bg,
                anchor="center", padx=4, pady=3,
            )
            tg_lbl.grid(row=ri, column=2, sticky="nsew", padx=(0, 1), pady=(0, 1))

            # col 3 — Status (text label)
            st_lbl = tk.Label(
                inner, text="Checking…", font=cell_font,
                bg=bg, fg="#888888", anchor="center", padx=4, pady=3,
            )
            st_lbl.grid(row=ri, column=3, sticky="nsew", padx=(0, 1), pady=(0, 1))

            # col 4 — Action (Open + Load + Delete centered as a singular object)
            act_frame = tk.Frame(inner, bg=bg)
            act_frame.grid(row=ri, column=4, sticky="nsew", padx=(0, 1), pady=(0, 1))
            act_frame.columnconfigure(0, weight=1)
            act_frame.rowconfigure(0, weight=1)

            act_inner = tk.Frame(act_frame, bg=bg)
            act_inner.grid(row=0, column=0, sticky="")

            rw: Dict[str, Any] = {
                "fn_lbl": fn_lbl, "tp_lbl": tp_lbl, "tg_lbl": tg_lbl,
                "st_lbl": st_lbl, "act_frame": act_frame,
                "open_btn": None, "load_btn": None, "del_btn": None,
                "bg": bg, "filepath": filepath_full,
            }
            row_widgets[filename] = rw

            # ── Button factories ──────────────────────────────────────
            def _make_open(fp):
                def _cmd():
                    try:
                        os.startfile(fp)
                    except OSError as exc:
                        messagebox.showerror(
                            "Open Failed", str(exc), parent=win,
                        )
                return _cmd

            def _make_load(fn, fp):
                def _cmd():
                    # Normalize path to forward slashes to match filedialog format
                    norm_fp = os.path.abspath(fp).replace("\\", "/")
                    name_lower = fn.lower()
                    if "_e_" in name_lower:
                        target = "FIRST PASS"
                    elif re.search(r"rj\d+_w", name_lower) or re.search(r"w_rj\d+", name_lower):
                        target = "RETEST"
                    elif re.search(r"rj\d+_q", name_lower) or "_q_" in name_lower:
                        target = "QC"
                    else:
                        target = self._prompt_panel_choice(win)
                        if target is None:
                            return
                    if norm_fp not in self.panel_files[target]:
                        self.panel_files[target].append(norm_fp)
                        self.refresh_file_list(target)
                        self.log(f"Loaded '{fn}' into {target} panel.")
                    else:
                        self.log(f"'{fn}' already in {target} panel.")
                return _cmd

            def _make_delete(fn, fp, rw_ref):
                def _cmd():
                    if not messagebox.askyesno(
                        "Delete File",
                        f"Are you sure you want to delete:\n\n{fn}",
                        parent=win,
                    ):
                        return
                    try:
                        os.remove(fp)
                        for lbl in (rw_ref["fn_lbl"], rw_ref["tp_lbl"],
                                    rw_ref["tg_lbl"]):
                            lbl.configure(fg="#AAAAAA")
                        rw_ref["st_lbl"].configure(text="Deleted", fg="#AAAAAA")
                        rw_ref["open_btn"].configure(state="disabled")
                        rw_ref["load_btn"].configure(state="disabled")
                        rw_ref["del_btn"].configure(state="disabled")
                        self.log(f"Deleted: {fn}")
                    except OSError as exc:
                        messagebox.showerror(
                            "Delete Failed", str(exc), parent=win,
                        )
                return _cmd

            ob = ModernHoverButton(
                act_inner, text="Open", padx=6, pady=2, font=("Segoe UI", 8, "bold"),
                command=_make_open(filepath_full),
            )
            ob.pack(side="left", padx=(0, 2), pady=2)
            rw["open_btn"] = ob

            lb = ModernHoverButton(
                act_inner, text="Load", padx=6, pady=2, font=("Segoe UI", 8, "bold"),
                command=_make_load(filename, filepath_full),
            )
            lb.pack(side="left", padx=2, pady=2)
            rw["load_btn"] = lb

            db = ModernHoverButton(
                act_inner, text="Delete", padx=6, pady=2, font=("Segoe UI", 8, "bold"),
                command=_make_delete(filename, filepath_full, rw),
            )
            db.pack(side="left", padx=(2, 0), pady=2)
            rw["del_btn"] = db

            return rw

        # ── Result painter (main thread) ──────────────────────────────────
        def _paint_row(widgets, status, tag, pc_str, gc_str):
            """Apply a parsed summary to a row's widgets (main thread only)."""
            fg = "#008000" if tag == "completed" else "#C00000"
            try:
                if _win_closed[0] or not win.winfo_exists():
                    return
                widgets["st_lbl"].configure(text=status, fg=fg, font=("Segoe UI", 9, "bold"))
                widgets["tp_lbl"].configure(text=pc_str)
                widgets["tg_lbl"].configure(text=gc_str)
            except Exception:
                pass

        # ── File parser (background thread) ───────────────────────────────
        def _parse_file(filename: str, sig=None):
            """Parse MRR + PCR and return (filename, status, tag, part_cnt, good_cnt, sig)."""
            fpath = os.path.join(folder_path, filename)
            if sig is None: sig = self._file_cache_signature(fpath)
            try:
                summary = parse_check_summary(fpath)
                mrr = summary.get("mrr")
                pcr = summary.get("pcr")

                # Integrity status from MRR FINISH_T
                if mrr is not None:
                    ft = mrr.get("FINISH_T")
                    if isinstance(ft, int) and ft > 0:
                        status, tag = "Completed", "completed"
                    elif isinstance(ft, str) and ft.strip():
                        status, tag = "Completed", "completed"
                    else:
                        status, tag = "Corrupted", "corrupted"
                else:
                    status, tag = "Corrupted", "corrupted"

                # PCR counts
                if pcr is not None:
                    pc = pcr.get("PART_CNT")
                    gc = pcr.get("GOOD_CNT")
                    pc_str = str(pc) if pc is not None and pc != _STDF_MISSING else "N/A"
                    gc_str = str(gc) if gc is not None and gc != _STDF_MISSING else "N/A"
                else:
                    pc_str = gc_str = "N/A"

                return filename, status, tag, pc_str, gc_str, sig
            except Exception:
                return filename, "Corrupted", "corrupted", "N/A", "N/A", sig

        # ── Completion tracking ───────────────────────────────────────────
        def _release_running_if_done():
            with _remaining_lock:
                _remaining[0] -= 1
                done = _remaining[0] <= 0
            if done and _owns_running[0]:
                _owns_running[0] = False
                self._set_running(False)

        def _on_future_done(future):
            try:
                fname, status, tag, pc_str, gc_str, sig = future.result()
            except Exception:
                _release_running_if_done()
                return
            # Cache the result for future instant loads.
            with self._check_cache_lock:
                self._check_summary_cache[sig] = (status, tag, pc_str, gc_str)
            widgets = row_widgets.get(fname)

            def _update():
                if widgets is not None:
                    _paint_row(widgets, status, tag, pc_str, gc_str)
                _release_running_if_done()

            if not _win_closed[0]:
                self.root.after(0, _update)
            else:
                _release_running_if_done()

        # ── Fast Filename Sort Key Helper ─────────────────────────────────
        def _fast_sort_key(filename: str):
            ts = _extract_first_filename_timestamp(filename)
            if ts:
                return 0, ts, filename.casefold()
            return 1, filename.casefold(), ""

        # ── Background discovery + parsing thread ─────────────────────────
        def _discover_and_parse():
            """Discover STDF files using os.scandir for 10x faster listing off main thread."""
            try:
                # Phase 1: Fast scan using os.scandir (no individual isfile stat calls)
                if not os.path.isdir(folder_path):
                    def _no_folder():
                        if _win_closed[0]:
                            return
                        scan_pbar.stop()
                        scan_lbl.configure(
                            text=f"No STDF folder found for Lot ID '{lot_id}'.",
                            fg="#C00000",
                        )
                        scan_pbar.pack_forget()
                        self.log(
                            f"Check STDF: Folder not found: {folder_path}"
                        )
                    self.root.after(0, _no_folder)
                    return

                try:
                    stdf_files = []
                    with os.scandir(folder_path) as entries:
                        for entry in entries:
                            if entry.is_file(follow_symlinks=False):
                                ext = os.path.splitext(entry.name)[1].lower()
                                if ext in stdf_extensions:
                                    stdf_files.append(entry.name)
                except OSError as exc:
                    def _list_err(m=str(exc)):
                        if _win_closed[0]:
                            return
                        scan_pbar.stop()
                        scan_lbl.configure(text=f"Error reading folder: {m}", fg="#C00000")
                        scan_pbar.pack_forget()
                    self.root.after(0, _list_err)
                    return

                if not stdf_files:
                    def _no_files():
                        if _win_closed[0]:
                            return
                        scan_pbar.stop()
                        scan_lbl.configure(
                            text=f"No STDF files found in folder.",
                            fg="#C00000",
                        )
                        scan_pbar.pack_forget()
                    self.root.after(0, _no_files)
                    return

                # Fast timestamp sort
                stdf_files.sort(key=_fast_sort_key)

                # Phase 2: Remove scanning banner and stream file rows on main thread
                def _build_rows():
                    if _win_closed[0]:
                        return
                    # Remove scanning banner
                    scan_frame.grid_forget()
                    scan_pbar.stop()

                    # Add rows for all discovered files
                    for filename in stdf_files:
                        filepath_full = os.path.join(folder_path, filename)
                        _add_file_row(filename, filepath_full)

                    try:
                        win.update_idletasks()
                    except Exception:
                        pass

                    # Phase 3: Start parsing (cached instantly, uncached in threads)
                    _uncached_sigs: Dict[str, Any] = {}
                    uncached_files: List[str] = []
                    cached_count = 0
                    for filename in stdf_files:
                        fpath = os.path.join(folder_path, filename)
                        sig = self._file_cache_signature(fpath)
                        with self._check_cache_lock:
                            cached = self._check_summary_cache.get(sig)
                        widgets = row_widgets.get(filename)
                        if cached is not None and widgets is not None:
                            status, tag, pc_str, gc_str = cached
                            _paint_row(widgets, status, tag, pc_str, gc_str)
                            cached_count += 1
                        else:
                            uncached_files.append(filename)
                            _uncached_sigs[filename] = sig

                    if cached_count:
                        self.log(
                            f"Check STDF: {cached_count} file(s) loaded from cache instantly; "
                            f"{len(uncached_files)} new/changed file(s) to process."
                        )

                    _remaining[0] = len(uncached_files)

                    if uncached_files:
                        _owns_running[0] = True
                        self._set_running(True)
                        max_workers = min(len(uncached_files), os.cpu_count() or 4)
                        executor = ThreadPoolExecutor(max_workers=max_workers)
                        _executor_ref[0] = executor
                        for filename in uncached_files:
                            future = executor.submit(_parse_file, filename, _uncached_sigs.get(filename))
                            future.add_done_callback(_on_future_done)
                    else:
                        self.log(
                            f"Check STDF: All {len(stdf_files)} file(s) loaded from cache."
                        )

                self.root.after(0, _build_rows)

            except Exception as exc:
                _msg = str(exc)
                def _gen_err(m=_msg):
                    if _win_closed[0]:
                        return
                    scan_pbar.stop()
                    scan_lbl.configure(text=f"Error: {m}", fg="#C00000")
                    scan_pbar.pack_forget()
                self.root.after(0, _gen_err)

        # ── Window close handler ──────────────────────────────────────────
        def _on_win_close():
            _win_closed[0] = True
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            if _executor_ref[0] is not None:
                _executor_ref[0].shutdown(wait=False, cancel_futures=True)
            if _owns_running[0]:
                _owns_running[0] = False
                self._set_running(False)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_win_close)

        # ── Kick off background discovery immediately ─────────────────────
        threading.Thread(target=_discover_and_parse, daemon=True).start()

    # ── Get STDF ───────────────────────────────────────────────────────────

    def start_get_stdf(self):
        """Search a network directory for STDF files matching the Lot ID and copy them locally."""
        from tkinter import messagebox
        from  stdf_fetcher import (
            resolve_mpc_details, search_stdf_files, copy_stdf_files,
            resolve_network_paths, existing_stdf_basenames,
        )

        lot_id_val = self.lot_id_var.get().strip().upper()
        self.lot_id_var.set(lot_id_val)
        mpc_val = self.mpc_var.get().strip()

        missing = []
        if not lot_id_val:
            missing.append("Lot ID")
        if not mpc_val:
            missing.append("MPC")
        if missing:
            messagebox.showerror(
                "Missing Required Input",
                "Please fill in the following field(s) before running Get STDF:\n\n"
                + ", ".join(missing),
                parent=self.root,
            )
            return

        # ── Start progress immediately on click ─────────────────────────────
        self._set_running(True)
        self.update_progress(0.02, "Resolving device…")
        self.log("=" * 80)
        self.log(f"Get STDF: Lot ID '{lot_id_val}', MPC '{mpc_val}'")

        # ── Resolve device and tester name(s) from MPC ───────────────────────
        try:
            devices, testers, network_paths = resolve_mpc_details(mpc_val)
        except ValueError as exc:
            self._set_running(False)
            self.reset_progress("Unsupported MPC")
            messagebox.showerror("Unsupported MPC", str(exc), parent=self.root)
            return
        except RuntimeError as exc:
            self._set_running(False)
            self.reset_progress("Configuration error")
            messagebox.showerror("Configuration Error", str(exc), parent=self.root)
            return

        # Prompt for Tester if multiple exist
        if len(testers) > 1:
            chosen_tester = self._prompt_selection(
                "Select Tester Type",
                f"MPC '{mpc_val}' is capable of multiple testers.\n\n"
                f"Please select the tester used:",
                testers
            )
            if not chosen_tester:
                self._set_running(False)
                self.reset_progress("Cancelled tester selection")
                self.log("Get STDF: Tester selection cancelled by user.")
                return
        elif len(testers) == 1:
            chosen_tester = testers[0]
        else:
            chosen_tester = "LTX"

        # Resolve the directories to search for the chosen tester: its
        # tester-specific base directory plus the shared common paths. All of
        # these are defined only once in mpc_partnumber.json ("_network_paths").
        network_bases = resolve_network_paths([chosen_tester])
        if not network_bases:
            # Fall back to every path configured for this MPC.
            network_bases = network_paths

        # Prompt for Device if multiple exist
        if len(devices) > 1:
            chosen_device = self._prompt_selection(
                "Select Device Name",
                f"MPC '{mpc_val}' is associated with multiple devices.\n\n"
                f"Please select the device:",
                devices
            )
            if not chosen_device:
                self._set_running(False)
                self.reset_progress("Cancelled device selection")
                self.log("Get STDF: Device selection cancelled by user.")
                return
            device_names = [chosen_device]
        else:
            device_names = devices

        # ── Phase 1: search (background thread) ──────────────────────────────
        self.update_progress(0.05, "Connecting to network…")
        self.log(f"  Tester: {chosen_tester}")
        self.log(f"  Device folder(s): {', '.join(device_names)}")
        self.log("  Network base(s):")
        for _nb in network_bases:
            self.log(f"    - {_nb}")

        def _search_worker():
            try:
                # Local destination cache scan off main thread
                already_have = existing_stdf_basenames(lot_id_val)
                if already_have:
                    self.log(
                        f"  Cache: {len(already_have)} file(s) already in "
                        f"C:\\FTDC\\{lot_id_val} will be skipped."
                    )

                found = search_stdf_files(
                    lot_id_val, device_names,
                    network_bases=network_bases,
                    tester_type=chosen_tester,
                    skip_existing_in_dest=True,
                    logger=self.log, progress_callback=self.update_progress,
                )
                if not found:
                    def _no_files():
                        self._set_running(False)
                        self.reset_progress("No new STDF files")
                        if already_have:
                            messagebox.showinfo(
                                "Get STDF",
                                f"No new STDF files for Lot ID '{lot_id_val}'.\n\n"
                                f"All matching files are already in "
                                f"C:\\FTDC\\{lot_id_val} "
                                f"({len(already_have)} file(s)); nothing new to fetch.",
                                parent=self.root,
                            )
                        else:
                            messagebox.showinfo(
                                "Get STDF",
                                f"No STDF files found matching Lot ID '{lot_id_val}'\n"
                                f"in device folder(s): {', '.join(device_names)}\n\n"
                                f"Please verify the Lot ID and MPC values.",
                                parent=self.root,
                            )
                    self.root.after(0, _no_files)
                    return
                # Post results to main thread for confirmation
                self.root.after(
                    0, lambda f=list(found): self._get_stdf_confirm(f, lot_id_val, device_names)
                )
            except OSError as exc:
                _msg = str(exc)
                def _net_err(m=_msg):
                    self._set_running(False)
                    self.reset_progress("Network error")
                    _paths_text = "\n".join(f"  {p}" for p in network_bases)
                    messagebox.showerror(
                        "Get STDF — Network Error",
                        f"Unable to access the network directory.\n\n"
                        f"Please make sure your device is connected to the correct network "
                        f"and the path(s) are reachable:\n{_paths_text}\n\n"
                        f"Details: {m}",
                        parent=self.root,
                    )
                self.root.after(0, _net_err)
            except Exception as exc:
                _msg = str(exc)
                def _gen_err(m=_msg):
                    self._set_running(False)
                    self.reset_progress("Get STDF failed")
                    messagebox.showerror(
                        "Get STDF Error",
                        f"An unexpected error occurred:\n\n{m}",
                        parent=self.root,
                    )
                self.root.after(0, _gen_err)

        threading.Thread(target=_search_worker, daemon=True).start()

    def _get_stdf_confirm(self, found_files: list, lot_id: str, device_names: list):
        """Show a confirmation dialog on the main thread, then start the copy phase."""
        from tkinter import messagebox
        from  stdf_fetcher import copy_stdf_files, get_common_paths

        def _shorten_path(path: str, keep: int = 2) -> str:
            """Return a short, readable tail of a network path, e.g.
            '...\\backend\\data' or '...\\Shared_Engineering_Directory\\RETRIEVED_DATALOG'."""
            parts = [p for p in re.split(r"[\\/]+", path.strip()) if p]
            if len(parts) <= keep:
                return "\\".join(parts)
            return "...\\" + "\\".join(parts[-keep:])

        # Common paths that are also searched for every MPC (shown shortened).
        try:
            common_short = [_shorten_path(p) for p in get_common_paths()]
        except Exception:
            common_short = []
        if common_short:
            common_line = "and in common folder(s): " + ", ".join(common_short) + "\n\n"
        else:
            common_line = "\n"

        file_list = "\n".join(f"  • {os.path.basename(f)}" for f in found_files)
        confirm_msg = (
            f"Found {len(found_files)} STDF file(s) matching Lot ID '{lot_id}'\n"
            f"in device folder(s): {', '.join(device_names)}\n"
            f"{common_line}"
            f"{file_list}\n\n"
            f"Copy all files to C:\\FTDC\\{lot_id}?"
        )

        if not messagebox.askyesno("Get STDF — Confirm Copy", confirm_msg, parent=self.root):
            self._set_running(False)
            self.reset_progress("Copy cancelled")
            self.log("Get STDF: Copy cancelled by user.")
            return

        # ── Phase 2: copy (background thread) ─────────────────────────────
        self.log(f"Get STDF: Copying {len(found_files)} file(s)…")

        def _copy_worker():
            try:
                dest_dir = copy_stdf_files(
                    found_files, lot_id,
                    logger=self.log, progress_callback=self.update_progress,
                )
                self.update_progress(1.0, "STDF files copied")

                def _done():
                    self._set_running(False)
                    self.set_status(f"Copied {len(found_files)} STDF file(s)")
                    messagebox.showinfo(
                        "Get STDF",
                        f"Successfully copied {len(found_files)} STDF file(s) to:\n\n"
                        f"{dest_dir}",
                        parent=self.root,
                    )
                    # Open the destination folder in Explorer
                    try:
                        os.startfile(dest_dir)
                    except Exception:
                        pass
                self.root.after(0, _done)

            except Exception as exc:
                _msg = str(exc)
                def _copy_err(m=_msg):
                    self._set_running(False)
                    self.reset_progress("Copy failed")
                    messagebox.showerror(
                        "Get STDF — Copy Error",
                        f"An error occurred while copying files:\n\n{m}",
                        parent=self.root,
                    )
                self.root.after(0, _copy_err)

        threading.Thread(target=_copy_worker, daemon=True).start()

    # ── Get FTDC Fail ─────────────────────────────────────────────────────

    def start_get_ftdc_fail(self):
        """POST to the FTDC filter endpoint using the current Lot ID and display FAIL rows."""
        import tkinter as tk
        from tkinter import messagebox

        # ── Dependency check ───────────────────────────────────────────────
        try:
            import requests as _requests
            from lxml import html as _lxml_html
        except ImportError:
            messagebox.showerror(
                "Missing Dependency",
                "The 'requests' and 'lxml' packages are required for FTDC lookup.\n\n"
                "Install them with:  pip install requests lxml",
                parent=self.root,
            )
            return

        lot_id_val = self.lot_id_var.get().strip().upper()
        self.lot_id_var.set(lot_id_val)
        if not lot_id_val:
            messagebox.showwarning(
                "Get FTDC Fail",
                "No Lot ID entered. Please fill in the Lot ID field before running.",
                parent=self.root,
            )
            return

        # ── Lock controls and start progress ──────────────────────────────
        self._set_running(True)
        self.update_progress(0.05, "Connecting to FTDC server…")

        # ── Helpers ────────────────────────────────────────────────────────
        def _cell_text(td) -> str:
            """Return all visible text inside a <td> element, joined and stripped."""
            return "".join(td.xpath(".//text()")).strip()

        def _ftdc_check_text(text: str) -> str:
            """Extract the human-readable check name from a Reply Message cell."""
            if "[" in text:
                return text.split("[")[0].strip()
            matches = re.findall(r'(\b\w+\b)\s+Failed', text)
            return ", ".join(m.strip() for m in matches) if matches else text.strip()

        def _extract_y_value(extra_data: str) -> str:
            """Return the numeric part after 'Y=' from a semicolon-delimited string."""
            for part in extra_data.split(";"):
                part = part.strip()
                if part.upper().startswith("Y="):
                    return part[2:].strip()
            return ""

        # ── Background worker ──────────────────────────────────────────────
        def _worker():
            TIMEOUT_SECONDS = 15
            url = "http://mph-vm-mphl2prd:8080/ftdc/filter.php"
            payload = {
                "lotid": lot_id_val,
                "mpc": "", "equipment": "", "stepname": "",
                "device": "", "rcode": "", "rstr": "",
                "bizrule": "", "submit": "",
            }

            def _finish(ok: bool, msg: str = ""):
                self._set_running(False)
                if ok:
                    self.update_progress(1.0, "FTDC fetch complete")
                    self.set_status("FTDC fetch complete")
                else:
                    self.reset_progress(msg or "FTDC fetch failed")

            try:
                self.root.after(0, lambda: self.update_progress(0.25, "Sending request to FTDC server…"))
                resp = _requests.post(url, data=payload, timeout=TIMEOUT_SECONDS)

                # ── HTTP error check ───────────────────────────────────────
                if resp.status_code != 200:
                    err_msg = (
                        f"FTDC server returned HTTP {resp.status_code}.\n\n"
                        f"Reason: {resp.reason or 'Unknown'}\n\n"
                        "Please check the FTDC server or try again later."
                    )
                    self.root.after(0, lambda m=err_msg: (
                        _finish(False, f"FTDC HTTP {resp.status_code}"),
                        messagebox.showerror("FTDC HTTP Error", m, parent=self.root),
                    ))
                    return

                self.root.after(0, lambda: self.update_progress(0.60, "Parsing FTDC response…"))

                # ── Parse HTML table ───────────────────────────────────────
                # Use ".//td" + join all text nodes so nested elements (spans, etc.)
                # are included — fixes the "no data" issue with .//td/text() which
                # only captures direct text children.
                parsed = _lxml_html.fromstring(resp.text)
                raw_rows: List[List[str]] = []
                for tr in parsed.xpath("//table//tr"):
                    cells = [_cell_text(td) for td in tr.xpath(".//td")]
                    if any(cells):       # skip pure-header <th> rows
                        raw_rows.append(cells)

                self.root.after(0, lambda: self.update_progress(0.80, "Scanning for FAIL rows…"))

                fail_entries: List[tuple] = []
                for r in raw_rows:
                    try:
                        if len(r) <= 6:
                            continue
                        if "FAIL" not in r[6].upper():
                            continue
                        reply_msg = r[7] if len(r) > 7 else ""
                        comment   = r[9] if len(r) > 9 else ""
                        fail_entries.append((reply_msg, comment))
                    except Exception:
                        continue

                self.root.after(0, lambda: self.update_progress(1.0, "FTDC fetch complete"))

                # ── Show result popup ──────────────────────────────────────
                def _show_popup():
                    _finish(True)

                    if not fail_entries:
                        messagebox.showinfo(
                            "FTDC Logs Result",
                            "No FTDC Fail found on FTDC Logs, kindly check MES FTDC Data.",
                            parent=self.root,
                        )
                        return

                    MAX_WIN_W = 550   # px — cap before word-wrap kicks in
                    MAX_WIN_H = 700   # px — cap before vertical scroll kicks in
                    CELL_PAD  = 20    # px — horizontal padding allowance per cell

                    cell_font  = tkfont.Font(family="Segoe UI", size=9)
                    char_w_px  = max(cell_font.measure("0"), 1)
                    line_h_px  = max(cell_font.metrics("linespace"), 1)

                    # Measure natural pixel width of each column from its content
                    def _col_natural_px(texts, header):
                        return max(
                            cell_font.measure(header),
                            *(cell_font.measure(t) for t in texts),
                            1,
                        ) + CELL_PAD * 2

                    reply_natural   = _col_natural_px([rm for rm, _ in fail_entries], "Reply Message")
                    comment_natural = _col_natural_px([cm for _, cm in fail_entries], "Comment")

                    avail_w        = MAX_WIN_W - 28          # subtract frm padding + borders
                    reply_col_px   = min(reply_natural,   int(avail_w * 0.65))
                    comment_col_px = min(comment_natural, avail_w - reply_col_px)

                    reply_chars   = max(8, reply_col_px   // char_w_px)
                    comment_chars = max(8, comment_col_px // char_w_px)

                    # Simulate word-wrap to get the lines each cell needs
                    def _lines_needed(text: str, col_px: int) -> int:
                        if not text:
                            return 1
                        inner_w = max(col_px - CELL_PAD * 2, char_w_px)
                        total = 0
                        for para in (text.splitlines() or [""]):
                            if not para:
                                total += 1
                                continue
                            cur_w = line_count = 0
                            line_count = 1
                            for word in para.split():
                                ww = cell_font.measure(word + " ")
                                if cur_w + ww > inner_w and cur_w > 0:
                                    line_count += 1
                                    cur_w = ww
                                else:
                                    cur_w += ww
                            total += line_count
                        return max(1, total)

                    row_heights = [
                        max(
                            _lines_needed(rm, reply_col_px),
                            _lines_needed(cm, comment_col_px),
                        )
                        for rm, cm in fail_entries
                    ]

                    # ── Build window ───────────────────────────────────────
                    pop = tk.Toplevel(self.root)
                    pop.title(f"FTDC Logs Result - {lot_id_val}")
                    pop.resizable(True, True)
                    pop.transient(self.root)
                    pop.columnconfigure(0, weight=1)
                    pop.rowconfigure(0, weight=1)

                    frm = self.ttk.Frame(pop, padding=12)
                    frm.grid(row=0, column=0, sticky="nsew")
                    frm.columnconfigure(0, weight=1)

                    # ── Single flat table — header in row 0, data in rows 1+ ──
                    # No outer box, no canvas. One parent = perfect column alignment,
                    # zero gray area. Window height comes purely from widget sizes.
                    table = tk.Frame(frm, bg="#E0E0E0")
                    table.grid(row=0, column=0, sticky="nsew")
                    table.columnconfigure(0, weight=1)
                    table.columnconfigure(1, weight=1)

                    # Header row
                    for col_idx, (lbl, chars) in enumerate((
                        ("Reply Message", reply_chars),
                        ("Comment",       comment_chars),
                    )):
                        tk.Label(
                            table, text=lbl,
                            font=("Segoe UI", 9, "bold"), bg="#F0F0F0",
                            anchor="w", padx=8, pady=4,
                            width=chars,
                            borderwidth=1, relief="solid",
                        ).grid(row=0, column=col_idx, sticky="ew")

                    # Data rows
                    for r_idx, ((reply_msg, comment), h) in enumerate(
                        zip(fail_entries, row_heights), start=1
                    ):
                        for c_idx, (text, chars) in enumerate((
                            (reply_msg, reply_chars),
                            (comment,   comment_chars),
                        )):
                            cell = tk.Text(
                                table,
                                width=chars, height=h,
                                wrap="word",
                                font=("Segoe UI", 9),
                                bg="white", relief="solid", bd=1,
                                padx=6, pady=4,
                                cursor="xterm",
                            )
                            cell.insert("1.0", text)
                            cell.configure(state="disabled")
                            cell.grid(row=r_idx, column=c_idx, sticky="nsew")

                    btn_row = self.ttk.Frame(frm)
                    btn_row.grid(row=1, column=0, sticky="e", pady=(10, 0))
                    self.ttk.Button(btn_row, text="Close", command=pop.destroy).pack(side="right")

                    # Let tkinter measure the true content size, then apply it
                    def _fit_window():
                        pop.update_idletasks()
                        w = min(pop.winfo_reqwidth(),  MAX_WIN_W)
                        h = min(pop.winfo_reqheight(), MAX_WIN_H)
                        # Centre over main window
                        rx = self.root.winfo_rootx()
                        ry = self.root.winfo_rooty()
                        rw = self.root.winfo_width()
                        rh = self.root.winfo_height()
                        x  = rx + max((rw - w) // 2, 0)
                        y  = ry + max((rh - h) // 2, 0)
                        pop.geometry(f"{w}x{h}+{x}+{y}")

                    pop.after(1, _fit_window)

                self.root.after(0, _show_popup)

            # ── Network / HTTP error handlers ──────────────────────────────
            except _requests.exceptions.ConnectionError:
                def _conn_err():
                    _finish(False, "FTDC: connection failed")
                    messagebox.showerror(
                        "FTDC Connection Error",
                        "Unable to connect to the FTDC server.\n\n"
                        "Please make sure your device is connected to the correct network\n"
                        "and that the FTDC server is reachable:\n\n"
                        "http://mph-vm-mphl2prd:8080/ftdc/filter.php",
                        parent=self.root,
                    )
                self.root.after(0, _conn_err)

            except _requests.exceptions.Timeout:
                def _timeout_err():
                    _finish(False, f"FTDC: timed out after {TIMEOUT_SECONDS}s")
                    messagebox.showerror(
                        "FTDC Request Timeout",
                        f"The FTDC server did not respond within {TIMEOUT_SECONDS} seconds.\n\n"
                        "This may be caused by a slow network connection or a busy server.\n"
                        "Please try again later.",
                        parent=self.root,
                    )
                self.root.after(0, _timeout_err)

            except Exception as exc:
                _msg = str(exc)
                def _gen_err(m=_msg):
                    _finish(False, "FTDC fetch failed")
                    messagebox.showerror("FTDC Error", f"An unexpected error occurred:\n\n{m}", parent=self.root)
                self.root.after(0, _gen_err)

        threading.Thread(target=_worker, daemon=True).start()

    def _set_running(self, running: bool):
        self.is_running = running
        def apply():
            if running:
                self._start_progress_polling()
            else:
                self._stop_progress_polling()
            # Disable ALL primary action buttons while any operation runs, and
            # re-enable them together when it finishes. This is why the old
            # "an operation is already running" pop-ups are no longer needed:
            # a busy action simply cannot be clicked again.
            state = "disabled" if running else "normal"
            for btn in self._action_buttons:
                if btn is not None:
                    try:
                        btn.configure(state=state)
                    except Exception:
                        pass
            self._sync_manual_filter_state()
        self.root.after(0, apply)

    def start_get_data(self):
        from tkinter import messagebox
        # Always capitalize the Lot ID
        lot_id_upper = self.lot_id_var.get().strip().upper()
        self.lot_id_var.set(lot_id_upper)

        empty_panels = [panel for panel in self.PANELS if not self.panel_files[panel]]
        if empty_panels:
            messagebox.showerror(
                "Missing STDF Input",
                "Please select at least one STDF file for each panel.\n\nMissing panel(s): " + ", ".join(empty_panels),
            )
            return

        required_fields = [
            ("Lot ID", self.lot_id_var),
            ("MPC", self.mpc_var),
            ("First Pass Actual Good QTY", self.fp_actual_good_qty_var),
            ("Total Actual Good QTY", self.total_actual_good_qty_var),
        ]
        empty_fields = [label for label, var in required_fields if not var.get().strip()]
        if empty_fields:
            messagebox.showerror(
                "Missing Required Input",
                "Please fill in all required input fields.\n\nMissing field(s): " + ", ".join(empty_fields),
            )
            return
        lot_id_text = self.lot_id_var.get().strip()
        mpc_text = self.mpc_var.get().strip()
        fp_actual_good_qty_text = self.fp_actual_good_qty_var.get().strip()
        total_actual_good_qty_text = self.total_actual_good_qty_var.get().strip()

        # Validate that First Pass and Total Actual Good QTY are valid integers
        invalid_qty_fields = []
        for field_label, field_val in [
            ("First Pass Actual Good QTY", fp_actual_good_qty_text),
            ("Total Actual Good QTY", total_actual_good_qty_text),
        ]:
            cleaned = field_val.replace(",", "").strip()
            if cleaned.startswith("+"):
                cleaned = cleaned[1:]
            if not cleaned.isdigit():
                invalid_qty_fields.append(f"• {field_label}: '{field_val}'")

        if invalid_qty_fields:
            messagebox.showerror(
                "Invalid Integer Input",
                "The following input field(s) must be a valid integer (whole number with no decimals):\n\n"
                + "\n".join(invalid_qty_fields)
                + "\n\nProper Input Example: 12500 (or any whole number, e.g., 500, 1000, 25000)."
            )
            return

        try:
            manual_filter_tests = None
            if self.manual_filter_var.get():
                manual_filter_tests = parse_filter_values(
                    self.tests_var.get(), self.selected_tests_var.get(), self.range_from_var.get(), self.range_to_var.get()
                )
        except ValueError as exc:
            messagebox.showerror("STDF GUID Checker", str(exc))
            return

        if not _parse_wxy_from_mpc_text(mpc_text):
            if not manual_filter_tests:
                messagebox.showerror("Unsupported Mask", UNSUPPORTED_MPC_WXY_MESSAGE)
                return
            if len(manual_filter_tests) != 3:
                messagebox.showerror(
                    "Unsupported Mask",
                    UNSUPPORTED_MPC_WXY_MESSAGE
                    + "\n\nManual Filter must contain exactly 3 test numbers in Wafer, X, Y order when the Mask is not supported.",
                )
                return

        # Keep the visual file order and the parsing order aligned:
        # timestamp ascending from the first valid YYYYMMDDHHMMSS in each filename.
        self.refresh_all_file_lists()
        first_pass_paths = list(self.panel_files["FIRST PASS"])
        retest_paths = list(self.panel_files["RETEST"])
        qc_paths = list(self.panel_files["QC"])

        cache_key = self._build_analysis_cache_key(
            lot_id_text,
            mpc_text,
            fp_actual_good_qty_text,
            total_actual_good_qty_text,
            manual_filter_tests,
        )
        cached_result = self._analysis_cache.get(cache_key)
        if cached_result is not None:
            self.log("=" * 80)
            self.log("Using cached result for the current session. No STDF reprocessing was needed.")
            self.update_progress(1.0, "Loaded cached result")
            total_status_text = cached_result.get('total_good_status') or 'SKIPPED'
            self.set_status(f"Cached result: FP {cached_result['status']} | Total Good {total_status_text}")
            self.root.after(0, lambda result=cached_result: self._show_result_popup(result))
            return

        self._update_selected_test_summary()
        self.log("=" * 80)
        self.log("Starting Get Data...")
        self.log(f"FIRST PASS files: {len(first_pass_paths)}")
        self.log(f"RETEST files: {len(retest_paths)}")
        self.log(f"QC files: {len(qc_paths)}")
        self.log(f"Manual filter: {sorted(manual_filter_tests) if manual_filter_tests else 'Disabled / not used'}")
        self.log("File parsing order: Timestamp ascending within each panel (first valid YYYYMMDDHHMMSS in filename; filename fallback).")
        self.update_progress(0.0, "Starting analysis")
        self._set_running(True)

        def worker():
            try:
                result = analyze_guid_data(
                    first_pass_paths=first_pass_paths,
                    retest_paths=retest_paths,
                    qc_paths=qc_paths,
                    lot_id=lot_id_text,
                    mpc_text=mpc_text,
                    first_pass_actual_good_qty=fp_actual_good_qty_text,
                    total_actual_good_qty=total_actual_good_qty_text,
                    manual_filter_tests=manual_filter_tests,
                    logger=self.log,
                    progress_callback=self.update_progress,
                    # max_workers=None -> auto: parse panel files across all CPU cores
                    # (set to 1 to force sequential; safe fallback if pools are unavailable).
                    max_workers=None,
                )
                self._analysis_cache[cache_key] = result
                total_status_text = result.get('total_good_status') or 'SKIPPED'
                self.set_status(f"Analysis completed: FP {result['status']} | Total Good {total_status_text}")
                self.root.after(0, lambda result=result: self._show_result_popup(result))
            except Exception as exc:
                self.log("Analysis failed.")
                self.log(str(exc))
                self.log(traceback.format_exc())
                self.reset_progress("Analysis failed")
                self.root.after(0, lambda: messagebox.showerror("STDF GUID Checker", str(exc)))
            finally:
                self._set_running(False)

        threading.Thread(target=worker, daemon=True).start()



# =============================================================================
# Application entry point
# -----------------------------------------------------------------------------
# IMPORTANT (multiprocessing + Nuitka onefile):
#   multiprocessing.freeze_support() MUST run as the very first statement of the
#   real program entry point. With the 'spawn' start method used on Windows (and
#   by a Nuitka --onefile binary), each worker process re-launches this same
#   executable; freeze_support() intercepts that re-launch so the child runs the
#   worker instead of starting a second copy of the GUI. Without it you would see
#   multiple windows open (or a fork-bomb-like relaunch) when parallel parsing
#   engages.
#
# If your program is started from a SEPARATE launcher file (e.g. main.py), put
# these two lines at the TOP of that file's __main__ guard as well:
#
#       import multiprocessing
#       multiprocessing.freeze_support()
# =============================================================================
def main():
    try:
        import sys, os
        hub_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PTS_Hub")
        if hub_dir not in sys.path:
            sys.path.insert(0, hub_dir)
        from PTS_Hub.main import launch_hub
        launch_hub()
    except Exception:
        import tkinter as tk
        root = tk.Tk()
        STDFGuidCheckerApp(root)
        root.mainloop()


if __name__ == "__main__":
    # Must be the first thing that runs in the frozen/child process.
    multiprocessing.freeze_support()
    main()
