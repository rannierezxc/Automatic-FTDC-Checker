"""
STDF2SUM Application User Interface Frame for PTS Hub.
"""
import logging
import os
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from core.config import get_asset_path
from core.theme import (
    BADGE_EMPTY_BG,
    BADGE_EMPTY_FG,
    BADGE_READY_BG,
    BADGE_READY_FG,
    BORDER_COLOR,
    CARD_BG,
    FONT_BADGE,
    FONT_BUTTON_MINI,
    FONT_ENTRY,
    FONT_LABEL,
    FONT_LOG,
    FONT_STATUS,
    FONT_SUBTITLE,
    LISTBOX_BG,
    LISTBOX_BORDER,
    LISTBOX_FG,
    LISTBOX_SELECT_BG,
    LISTBOX_SELECT_FG,
    LOG_BG,
    LOG_FG,
    LOG_SELECT_BG,
    LOG_SELECT_FG,
    MAIN_BG,
    TEXT_FG,
    TEXT_MUTED,
)
from core.widgets import (
    InfoTooltipButton,
    ModernAccentButton,
    ModernCard,
    ModernEntry,
    ModernHoverButton,
    attach_tooltip,
)
from apps.stdf2sum.excel_output import (
    clear_parse_cache,
    convert_multiple_stdf_to_single_csv,
)
from apps.stdf2sum.stdf_parser import (
    ByteProgressFunc,
    ProgressFunc,
    _format_limit_text,
    build_default_output_prefix,
    parse_filter_values,
    scan_test_list,
)

STDF_FILEDIALOG_EXTENSIONS = "*.std* *.old* *.bak* *.gz"
TOOLTIP_INPUT_FILES = (
    "Add one or more STDF V4 datalog files (including .gz) to combine into a single Excel workbook and PDF summary."
)
TOOLTIP_SHOW_ALL_TESTS = "Scan loaded STDF files and select specific test parameters to export."
TOOLTIP_CLEAR_SELECTED_TESTS = "Clear selected test parameters from the filter."
TOOLTIP_OUTPUT_PREFIX = (
    "Base file path prefix for generated Excel (.xlsx) and PDF Summary (.pdf) files. "
    "Auto-generated from input files unless manually edited."
)
TOOLTIP_CONVERT = "Parse all loaded STDF files and generate the combined Excel workbook and Summary PDF."
TOOLTIP_CLEAR_ALL = "Reset loaded files, filters, output prefix, logs, and caches."


class STDF2SumFrame(ttk.Frame):
    """STDF to Summary & Excel converter application frame inside PTS Hub."""

    def __init__(self, parent, hub=None):
        import tkinter as tk

        super().__init__(parent)
        self.parent = parent
        self.hub = hub
        self.app_name = "STDF2SUM"
        self.root = self.winfo_toplevel() if hasattr(self, "winfo_toplevel") else parent
        self.tk_mod = tk
        self.ttk = ttk
        self.style = ttk.Style()

        self.selected_files: List[str] = []
        self.is_running = False
        self.is_scanning_tests = False
        self.output_is_auto = True
        self._setting_output_programmatically = False

        self.output_var = tk.StringVar()
        self.tests_var = tk.StringVar()
        self.selected_tests_var = tk.StringVar()
        self.range_from_var = tk.StringVar()
        self.range_to_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.progress_text_var = tk.StringVar(value="")
        self._selected_test_count_var = tk.StringVar(value="No test parameters selected")
        self._files_count_var = tk.StringVar(value="0 files")

        self._test_scan_cache: Dict[Tuple[str, int, int], List[Dict]] = {}
        self._cached_tests: List[Dict] = []
        self._cached_test_rows: List[Tuple] = []
        self._test_filter_after_id = None
        self._test_window = None
        self._test_tree = None
        self._test_filter_var = None

        self._pending_progress: Optional[Tuple[float, str]] = None
        self._pending_status: Optional[str] = None
        self._pending_reset_progress: Optional[str] = None
        self._pending_log_messages: List[str] = []
        self._log_lock = threading.Lock()
        self._progress_poll_id = None
        self._pending_running_state: Optional[bool] = None
        self._pending_scan_state: Optional[bool] = None
        self._pending_completion_dialog: Optional[Tuple[Optional[str], Optional[str]]] = None
        self._pending_success_dialog: Optional[Tuple[Optional[str], Optional[str]]] = None
        self._pending_test_window: Optional[Tuple[List[Dict], str]] = None
        self._pending_warning_dialog: Optional[str] = None
        self._pending_error_dialog: Optional[str] = None

        self.files_listbox = None
        self._files_scrollbar = None
        self.files_badge = None
        self.add_files_button = None
        self.remove_files_button = None
        self.clear_files_button = None
        self.show_all_tests_button = None
        self.clear_selected_tests_button = None
        self.selected_entry = None
        self.typed_entry = None
        self.range_from_entry = None
        self.range_to_entry = None
        self.output_entry = None
        self.browse_output_button = None
        self.convert_button = None
        self.clear_all_button = None
        self.progress_bar = None
        self._action_buttons: List[Any] = []
        self._auxiliary_buttons: List[Any] = []

        self.output_var.trace_add("write", self._on_output_var_changed)
        self._build_ui()
        self._update_selected_test_summary()

    def _build_ui(self):
        tk = self.tk_mod
        ttk = self.ttk

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Upper workspace leaves 125px bottom clearance for the overlay log console drawer
        workspace = ttk.Frame(self)
        workspace.pack(fill="both", expand=True, padx=8, pady=(6, 125))
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(0, weight=1)

        # ── Central 2-Column Split ────────────────────────────────────────
        center = ttk.Frame(workspace)
        center.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        center.columnconfigure(0, weight=58)
        center.columnconfigure(1, weight=42)
        center.rowconfigure(0, weight=1)

        # ── Left: Input STDF Files Card ───────────────────────────────────
        files_card = ModernCard(center, text="Input STDF Datalogs", padx=8, pady=6)
        files_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        files_card.columnconfigure(0, weight=1)
        files_card.rowconfigure(1, weight=1)

        hdr = ttk.Frame(files_card)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        hdr.columnconfigure(3, weight=1)

        tk.Label(hdr, text="■", font=("Segoe UI", 9, "bold"), fg="#2563EB", bg=CARD_BG).grid(
            row=0, column=0, sticky="w", padx=(0, 4)
        )
        title_lbl = ttk.Label(hdr, text="INPUT STDF FILES", style="PanelTitle.TLabel")
        title_lbl.grid(row=0, column=1, sticky="w")
        attach_tooltip(title_lbl, TOOLTIP_INPUT_FILES)
        InfoTooltipButton(hdr, text=TOOLTIP_INPUT_FILES, bg=CARD_BG).grid(row=0, column=2, sticky="w", padx=(4, 0))

        self.files_badge = tk.Label(
            hdr,
            textvariable=self._files_count_var,
            font=FONT_BADGE,
            bg=BADGE_EMPTY_BG,
            fg=BADGE_EMPTY_FG,
            padx=7,
            pady=1,
            relief="flat",
        )
        self.files_badge.grid(row=0, column=3, sticky="w", padx=(8, 0))

        btn_frame = ttk.Frame(hdr)
        btn_frame.grid(row=0, column=4, sticky="e")

        self.add_files_button = ModernHoverButton(
            btn_frame,
            text="+ Add Files",
            command=self.select_files,
            padx=6,
            pady=2,
            font=FONT_BUTTON_MINI,
        )
        self.add_files_button.pack(side="left", padx=(0, 3))
        attach_tooltip(self.add_files_button, "Browse and add STDF datalog files.")

        self.remove_files_button = ModernHoverButton(
            btn_frame,
            text="Remove Selected",
            command=self.remove_selected_files,
            padx=6,
            pady=2,
            font=FONT_BUTTON_MINI,
        )
        self.remove_files_button.pack(side="left", padx=2)
        attach_tooltip(self.remove_files_button, "Remove selected STDF file(s) from the list.")

        self.clear_files_button = ModernHoverButton(
            btn_frame,
            text="Clear Files",
            command=self.clear_files,
            padx=6,
            pady=2,
            font=FONT_BUTTON_MINI,
        )
        self.clear_files_button.pack(side="left", padx=(2, 0))
        attach_tooltip(self.clear_files_button, "Clear all loaded STDF files.")
        self._auxiliary_buttons.extend((self.add_files_button, self.remove_files_button, self.clear_files_button))

        list_frame = ttk.Frame(files_card)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.files_listbox = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            height=12,
            font=FONT_ENTRY,
            bg=LISTBOX_BG,
            fg=LISTBOX_FG,
            selectbackground=LISTBOX_SELECT_BG,
            selectforeground=LISTBOX_SELECT_FG,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=LISTBOX_BORDER,
            highlightcolor=LISTBOX_BORDER,
        )
        self.files_listbox.pack(side="left", fill="both", expand=True)

        self._files_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.files_listbox.yview)
        self.files_listbox.configure(yscrollcommand=self._files_scrollbar.set)
        self._update_files_scrollbar()

        # ── Right: Test Filter (Top) + Output & Conversion (Bottom) ───────
        right_box = ttk.Frame(center)
        right_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right_box.columnconfigure(0, weight=1)
        right_box.rowconfigure(0, weight=1)
        right_box.rowconfigure(1, weight=0)

        # Top Card: Test Parameter Filter
        filter_card = ModernCard(right_box, text="Test Parameter Filter", padx=8, pady=6)
        filter_card.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        filter_card.columnconfigure(0, weight=1)

        filter_header = tk.Frame(filter_card, bg=CARD_BG)
        filter_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        filter_header.columnconfigure(2, weight=1)

        self.show_all_tests_button = ModernHoverButton(
            filter_header,
            text="Show All Test",
            command=self.show_all_tests,
            padx=8,
            pady=3,
        )
        self.show_all_tests_button.grid(row=0, column=0, sticky="w")
        attach_tooltip(self.show_all_tests_button, TOOLTIP_SHOW_ALL_TESTS)

        self.clear_selected_tests_button = ModernHoverButton(
            filter_header,
            text="Clear Selected",
            command=self._clear_selected_test_filter,
            padx=8,
            pady=3,
        )
        self.clear_selected_tests_button.grid(row=0, column=1, sticky="w", padx=(6, 0))
        attach_tooltip(self.clear_selected_tests_button, TOOLTIP_CLEAR_SELECTED_TESTS)
        self.clear_selected_tests_button.grid_remove()

        tk.Label(filter_card, text="Test Parameter Selection", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(
            row=1, column=0, sticky="w", pady=(3, 1)
        )
        self.selected_entry = ModernEntry(
            filter_card, textvariable=self.selected_tests_var, state="readonly", font=("Segoe UI", 8)
        )
        self.selected_entry.grid(row=2, column=0, sticky="ew")

        tk.Label(
            filter_card, textvariable=self._selected_test_count_var, font=FONT_SUBTITLE, bg=CARD_BG, fg=TEXT_MUTED
        ).grid(row=3, column=0, sticky="w", pady=(1, 4))

        tk.Label(filter_card, text="Test Numbers", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(
            row=4, column=0, sticky="w", pady=(2, 1)
        )
        self.typed_entry = ModernEntry(filter_card, textvariable=self.tests_var)
        self.typed_entry.grid(row=5, column=0, sticky="ew")

        tk.Label(
            filter_card,
            text='Format: space or comma separated (e.g. "1001 1002")',
            font=FONT_SUBTITLE,
            bg=CARD_BG,
            fg=TEXT_MUTED,
        ).grid(row=6, column=0, sticky="w", pady=(1, 6))

        range_frame = tk.Frame(filter_card, bg=CARD_BG)
        range_frame.grid(row=7, column=0, sticky="ew", pady=(2, 2))
        range_frame.columnconfigure(1, weight=1)
        range_frame.columnconfigure(3, weight=1)

        tk.Label(range_frame, text="Range From:", font=("Segoe UI", 8), bg=CARD_BG, fg=TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 4)
        )
        self.range_from_entry = ModernEntry(range_frame, textvariable=self.range_from_var)
        self.range_from_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        tk.Label(range_frame, text="Range To:", font=("Segoe UI", 8), bg=CARD_BG, fg=TEXT_MUTED).grid(
            row=0, column=2, sticky="w", padx=(4, 4)
        )
        self.range_to_entry = ModernEntry(range_frame, textvariable=self.range_to_var)
        self.range_to_entry.grid(row=0, column=3, sticky="ew")

        # Bottom Card: Output & Conversion
        output_card = ModernCard(right_box, text="Output & Conversion", padx=8, pady=10)
        output_card.grid(row=1, column=0, sticky="ew")
        output_card.columnconfigure(0, weight=1)

        out_lbl_row = tk.Frame(output_card, bg=CARD_BG)
        out_lbl_row.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        tk.Label(out_lbl_row, text="Excel Output Prefix:", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).pack(side="left")
        InfoTooltipButton(out_lbl_row, text=TOOLTIP_OUTPUT_PREFIX, bg=CARD_BG).pack(side="left", padx=(4, 0))

        out_input_row = tk.Frame(output_card, bg=CARD_BG)
        out_input_row.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        out_input_row.columnconfigure(0, weight=1)

        self.output_entry = ModernEntry(out_input_row, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.output_entry.bind("<KeyRelease>", self._mark_output_manual)

        self.browse_output_button = ModernHoverButton(
            out_input_row,
            text="Browse",
            command=self.choose_output,
            padx=10,
            pady=4,
        )
        self.browse_output_button.grid(row=0, column=1, sticky="e")
        self._auxiliary_buttons.append(self.browse_output_button)

        self.convert_button = ModernAccentButton(
            output_card,
            text="⚡ Convert STDF to Excel & Summary",
            command=self.start_conversion,
            pady=18,
        )
        self.convert_button.grid(row=2, column=0, sticky="ew", pady=(2, 2))
        attach_tooltip(self.convert_button, TOOLTIP_CONVERT)

        self._action_buttons = [self.convert_button, self.show_all_tests_button]

        # ── 3. Progress & Status Bar Card ─────────────────────────────────
        progress_card = ModernCard(workspace, text="", padx=10, pady=6)
        progress_card.grid(row=1, column=0, sticky="ew", padx=2, pady=(4, 0))
        progress_card.columnconfigure(1, weight=1)

        status_row = tk.Frame(progress_card, bg=CARD_BG)
        status_row.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        status_row.columnconfigure(1, weight=1)

        tk.Label(status_row, text="Status:", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(row=0, column=0, sticky="w")
        tk.Label(status_row, textvariable=self.status_var, font=FONT_STATUS, bg=CARD_BG, fg=TEXT_FG).grid(
            row=0, column=1, sticky="w", padx=(6, 0)
        )
        tk.Label(
            status_row,
            textvariable=self.progress_text_var,
            font=FONT_LABEL,
            bg=CARD_BG,
            fg=TEXT_FG,
            anchor="e",
        ).grid(row=0, column=2, sticky="e")

        self.clear_all_button = ModernHoverButton(
            progress_card,
            text="Clear All",
            command=self.clear_all,
            padx=8,
            pady=3,
        )
        self.clear_all_button.grid(row=1, column=0, sticky="w", padx=(0, 10))
        attach_tooltip(self.clear_all_button, TOOLTIP_CLEAR_ALL)
        self._auxiliary_buttons.append(self.clear_all_button)

        self.progress_bar = ttk.Progressbar(
            progress_card,
            style="Neon.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress_bar.grid(row=1, column=1, columnspan=2, sticky="ew")

        # ── 4. Overlay Log Console Drawer ─────────────────────────────────
        self._build_log_overlay_drawer(self)

    def _build_log_overlay_drawer(self, parent):
        tk = self.tk_mod
        ttk = self.ttk

        self._log_cur_height = 120
        self._log_drawer = tk.Frame(
            parent,
            bg=MAIN_BG,
            bd=0,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
        )
        self._log_drawer.place(x=10, rely=1, relwidth=0.985, height=self._log_cur_height, anchor="sw")

        drag_bar = tk.Frame(self._log_drawer, bg=MAIN_BG, height=28, cursor="sb_v_double_arrow")
        drag_bar.pack(fill="x")

        tk.Label(
            drag_bar,
            text="⠿ Execution Console & Activity Log",
            font=FONT_LABEL,
            bg=MAIN_BG,
            fg=TEXT_FG,
            cursor="sb_v_double_arrow",
        ).pack(side="left", padx=10)

        tk.Label(
            drag_bar,
            text="(drag handle to resize)",
            font=FONT_SUBTITLE,
            bg=MAIN_BG,
            fg=TEXT_MUTED,
            cursor="sb_v_double_arrow",
        ).pack(side="left")

        btn_box = tk.Frame(drag_bar, bg=MAIN_BG)
        btn_box.pack(side="right", padx=6, pady=2)

        def set_h(h):
            self._log_cur_height = h
            self._log_drawer.place_configure(height=h)

        def make_preset(text, h):
            b = ModernHoverButton(btn_box, text=text, command=lambda: set_h(h), padx=5, pady=1, font=FONT_BUTTON_MINI)
            b.pack(side="left", padx=2)

        make_preset("Min", 28)
        make_preset("Compact", 120)
        make_preset("Half", 280)
        make_preset("Max", 480)

        copy_btn = ModernHoverButton(btn_box, text="Copy Log", command=self.copy_log, padx=6, pady=1, font=FONT_BUTTON_MINI)
        copy_btn.pack(side="left", padx=(6, 2))

        clear_btn = ModernHoverButton(btn_box, text="Clear Log", command=self.clear_log, padx=6, pady=1, font=FONT_BUTTON_MINI)
        clear_btn.pack(side="left", padx=2)

        start_y = [0]

        def on_start(e):
            start_y[0] = e.y_root

        def on_drag(e):
            dy = start_y[0] - e.y_root
            max_h = max(60, parent.winfo_height() - 40)
            new_h = max(28, min(max_h, self._log_cur_height + dy))
            self._log_drawer.place_configure(height=new_h)

        def on_stop(e):
            dy = start_y[0] - e.y_root
            max_h = max(60, parent.winfo_height() - 40)
            self._log_cur_height = max(28, min(max_h, self._log_cur_height + dy))

        drag_bar.bind("<Button-1>", on_start)
        drag_bar.bind("<B1-Motion>", on_drag)
        drag_bar.bind("<ButtonRelease-1>", on_stop)

        console_body = tk.Frame(self._log_drawer, bg=LOG_BG)
        console_body.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            console_body,
            wrap="word",
            font=FONT_LOG,
            bg=LOG_BG,
            fg=LOG_FG,
            selectbackground=LOG_SELECT_BG,
            selectforeground=LOG_SELECT_FG,
            insertbackground=LOG_FG,
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

    # ── Output Prefix Sync ────────────────────────────────────────────────
    def _on_output_var_changed(self, *_):
        if self._setting_output_programmatically:
            return
        self.output_is_auto = not bool(self.output_var.get().strip())

    def _mark_output_manual(self, _event=None):
        if not self._setting_output_programmatically and self.output_var.get().strip():
            self.output_is_auto = False

    def _set_output_value(self, value: str, auto_managed: bool = True):
        self._setting_output_programmatically = True
        try:
            self.output_var.set(value)
        finally:
            self._setting_output_programmatically = False
        self.output_is_auto = auto_managed

    def _get_suggested_output_prefix(self) -> str:
        return build_default_output_prefix(self.selected_files)

    def _sync_auto_output_prefix(self, force: bool = False):
        if not self.selected_files:
            if force or self.output_is_auto:
                self._set_output_value("", auto_managed=True)
            return
        if force or self.output_is_auto or not self.output_var.get().strip():
            self._set_output_value(self._get_suggested_output_prefix(), auto_managed=True)

    # ── Logging, Progress, & 50ms UI Polling ──────────────────────────────
    def _flush_pending_logs(self):
        with self._log_lock:
            if not self._pending_log_messages:
                return
            msgs = list(self._pending_log_messages)
            self._pending_log_messages.clear()
        try:
            if not self.root.winfo_exists():
                return
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(msgs) + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception as exc:
            logging.warning("Failed to append message to STDF2SUM log_text: %s", exc)

    def log(self, message: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}"
        with self._log_lock:
            self._pending_log_messages.append(line)
        if threading.current_thread() is threading.main_thread():
            try:
                self.root.after(0, self._flush_pending_logs)
            except Exception:
                pass
        elif self._progress_poll_id is None:
            try:
                self.root.after(0, self._flush_pending_logs)
            except RuntimeError:
                pass

    def copy_log(self):
        try:
            content = self.log_text.get("1.0", "end-1c")
            if content.strip():
                self.root.clipboard_clear()
                self.root.clipboard_append(content)
                self.set_status("Log copied to clipboard")
        except Exception as exc:
            logging.warning("Failed to copy STDF2SUM log: %s", exc)

    def clear_log(self):
        with self._log_lock:
            self._pending_log_messages.clear()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.reset_progress("Ready")

    def set_status(self, message: str):
        self._pending_status = message

        def apply():
            msg = self._pending_status
            if msg is None:
                return
            self._pending_status = None
            try:
                if self.root.winfo_exists():
                    self.status_var.set(msg)
            except Exception:
                pass

        if threading.current_thread() is threading.main_thread():
            apply()
        elif self._progress_poll_id is None:
            try:
                self.root.after(0, apply)
            except Exception:
                pass

    def update_progress(self, fraction: float, message: str = ""):
        self._pending_progress = (fraction, message)

    def _flush_pending_progress(self):
        reset_msg = self._pending_reset_progress
        if reset_msg is not None:
            self._pending_reset_progress = None
            if self.progress_bar is not None:
                self.progress_bar.configure(value=0)
            self.progress_text_var.set("")
            self.status_var.set(reset_msg)

        pending = self._pending_progress
        if pending is not None:
            fraction, message = pending
            self._pending_progress = None
            pct = max(0.0, min(100.0, float(fraction) * 100.0))
            if self.progress_bar is not None:
                self.progress_bar.configure(value=pct)
            self.progress_text_var.set(f"{pct:5.1f}%")
            if message:
                self.status_var.set(f"{message} ({pct:5.1f}%)" if pct < 100.0 else message)

        pending_status = self._pending_status
        if pending_status is not None:
            self._pending_status = None
            self.status_var.set(pending_status)

    def _flush_pending_dialogs(self):
        test_win_args = self._pending_test_window
        if test_win_args is not None:
            self._pending_test_window = None
            tests, source_label = test_win_args
            self._open_test_window(tests, source_label)

        success_args = self._pending_success_dialog
        if success_args is not None:
            self._pending_success_dialog = None
            pdf_path, xlsx_path = success_args
            if self.hub and getattr(self.hub, "_active_tab", None) != self.app_name:
                self._pending_completion_dialog = (pdf_path, xlsx_path)
            else:
                self._show_success_dialog(pdf_path, xlsx_path)

        warn_msg = self._pending_warning_dialog
        if warn_msg is not None:
            self._pending_warning_dialog = None
            messagebox.showwarning("STDF2SUM", warn_msg)

        err_msg = self._pending_error_dialog
        if err_msg is not None:
            self._pending_error_dialog = None
            messagebox.showerror("STDF2SUM", err_msg)

    def _poll_progress(self):
        self._flush_pending_progress()
        self._flush_pending_logs()
        if self._pending_scan_state is not None:
            self._flush_pending_scan_state()
        if self._pending_running_state is not None:
            self._flush_pending_running_state()
        if self.is_running or self.is_scanning_tests:
            self._progress_poll_id = self.root.after(50, self._poll_progress)
        else:
            self._progress_poll_id = None
            self._flush_pending_dialogs()

    def _start_progress_polling(self):
        if self._progress_poll_id is None:
            self._progress_poll_id = self.root.after(50, self._poll_progress)

    def _stop_progress_polling(self):
        if self._progress_poll_id is not None:
            try:
                self.root.after_cancel(self._progress_poll_id)
            except Exception:
                pass
            self._progress_poll_id = None
        self._flush_pending_progress()
        self._flush_pending_logs()
        self._flush_pending_dialogs()

    def reset_progress(self, message: str = "Ready"):
        self._pending_progress = None
        self._pending_status = None
        self._pending_reset_progress = message

        def apply():
            msg = self._pending_reset_progress
            if msg is None:
                return
            self._pending_reset_progress = None
            try:
                if self.root.winfo_exists():
                    if self.progress_bar is not None:
                        self.progress_bar.configure(value=0)
                    self.progress_text_var.set("")
                    self.status_var.set(msg)
            except Exception:
                pass

        if threading.current_thread() is threading.main_thread():
            apply()
        elif self._progress_poll_id is None:
            try:
                self.root.after(0, apply)
            except Exception:
                pass

    # ── File Management ───────────────────────────────────────────────────
    def _update_files_scrollbar(self):
        lb = self.files_listbox
        sb = self._files_scrollbar
        if not lb or not sb:
            return
        n = lb.size()
        visible_height = int(lb.cget("height") or 12)
        if self.root.winfo_ismapped():
            self.root.update_idletasks()
            first_box = lb.bbox(0)
            last_box = lb.bbox(n - 1) if n > 0 else None
            all_visible = (n == 0) or (first_box is not None and last_box is not None)
        else:
            all_visible = n <= visible_height

        if all_visible:
            sb.pack_forget()
        else:
            sb.pack(side="right", fill="y")

    def refresh_file_list(self):
        self.files_listbox.delete(0, "end")
        for path in self.selected_files:
            self.files_listbox.insert("end", path)
        count = len(self.selected_files)
        self._files_count_var.set("1 file" if count == 1 else f"{count} files")
        if self.files_badge is not None:
            if count > 0:
                self.files_badge.configure(bg=BADGE_READY_BG, fg=BADGE_READY_FG)
            else:
                self.files_badge.configure(bg=BADGE_EMPTY_BG, fg=BADGE_EMPTY_FG)
        self._update_files_scrollbar()
        self._sync_auto_output_prefix()

    def select_files(self):
        file_paths = filedialog.askopenfilenames(
            title="Select one or more STDF files",
            filetypes=[("STDF files", STDF_FILEDIALOG_EXTENSIONS), ("All files", "*.*")],
        )
        if not file_paths:
            return
        added = 0
        existing = set(self.selected_files)
        for path in file_paths:
            if path not in existing:
                self.selected_files.append(path)
                existing.add(path)
                added += 1
        self.refresh_file_list()
        self.log(f"Added {added} file(s). Total loaded: {len(self.selected_files)}")

    def remove_selected_files(self):
        selected_indices = list(self.files_listbox.curselection())
        if not selected_indices:
            return
        for index in sorted((int(i) for i in selected_indices), reverse=True):
            if 0 <= index < len(self.selected_files):
                del self.selected_files[index]
        self.refresh_file_list()
        self.log(f"Removed {len(selected_indices)} selected file(s).")

    def clear_files(self):
        count = len(self.selected_files)
        self.selected_files.clear()
        self.refresh_file_list()
        self.log(f"Cleared {count} file(s).")

    def choose_output(self):
        suggested_prefix = self.output_var.get().strip() or self._get_suggested_output_prefix()
        initial_dir = os.path.dirname(suggested_prefix) or os.getcwd()
        initial_name = f"{os.path.basename(suggested_prefix) or 'combined_stdf'}.xlsx"
        save_path = filedialog.asksaveasfilename(
            title="Choose output Excel location",
            defaultextension=".xlsx",
            initialdir=initial_dir,
            initialfile=initial_name,
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if save_path:
            output_prefix, _ = os.path.splitext(save_path)
            self._set_output_value(output_prefix, auto_managed=False)
            self.log(f"Output prefix set to: {output_prefix}")

    def clear_all_caches(self) -> None:
        self._test_scan_cache.clear()
        self._cached_tests.clear()
        self._cached_test_rows.clear()
        try:
            clear_parse_cache()
        except Exception as exc:
            logging.debug("Failed to clear STDF2SUM parse cache: %s", exc)

    def clear_all(self):
        with self._log_lock:
            self._pending_log_messages.clear()
        self._pending_progress = None
        self._pending_completion_dialog = None
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        if self.progress_bar is not None:
            self.progress_bar.configure(value=0)
        self.status_var.set("Ready")
        self.progress_text_var.set("")

        self.selected_files.clear()
        self.refresh_file_list()
        self._set_output_value("", auto_managed=True)
        self.tests_var.set("")
        self.selected_tests_var.set("")
        self.range_from_var.set("")
        self.range_to_var.set("")
        self._update_selected_test_summary()
        self.clear_all_caches()
        if self.is_running:
            self._set_running(False)

    # ── Test Parameter Scan & Filter Window ───────────────────────────────
    def _update_selected_test_summary(self):
        raw_val = self.selected_tests_var.get().strip()
        if "," in raw_val:
            tokens = [x.strip() for x in raw_val.split(",") if x.strip()]
        else:
            tokens = raw_val.split()
        count = len(tokens)
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
            btn_state = "disabled" if (self.is_running or self.is_scanning_tests) else "normal"
            self.clear_selected_tests_button.configure(state=btn_state)
        else:
            self.clear_selected_tests_button.grid_remove()

    def _build_test_cache_key(self, file_path: str) -> Optional[Tuple[str, int, int]]:
        try:
            stat = os.stat(file_path)
        except OSError:
            return None
        return (
            os.path.abspath(file_path),
            stat.st_size,
            getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
        )

    def _scan_tests_for_selected_files(self, progress_callback: ProgressFunc = None) -> Tuple[List[Dict], str]:
        merged_tests: Dict[int, Dict] = {}
        source_names: List[str] = [os.path.basename(p) for p in self.selected_files]
        file_sizes = [max(os.path.getsize(p), 1) if os.path.exists(p) else 1 for p in self.selected_files]
        total_bytes = sum(file_sizes) or 1

        uncached = []
        tests_by_idx: Dict[int, List[Dict]] = {}
        for idx, file_path in enumerate(self.selected_files):
            cache_key = self._build_test_cache_key(file_path)
            cached = self._test_scan_cache.get(cache_key) if cache_key else None
            if cached is not None:
                self.log(f"Using cached test list for: {file_path}")
                tests_by_idx[idx] = cached
            else:
                uncached.append((idx, file_path, cache_key))

        if len(uncached) >= 2:
            done_bytes = sum(file_sizes[i] for i in tests_by_idx)
            max_workers = min(4, len(uncached))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(scan_test_list, fpath): (idx, fpath, ckey)
                    for idx, fpath, ckey in uncached
                }
                for fut in as_completed(futures):
                    idx, fpath, ckey = futures[fut]
                    scanned = fut.result()
                    tests_by_idx[idx] = scanned
                    if ckey:
                        self._test_scan_cache[ckey] = scanned
                    done_bytes += file_sizes[idx]
                    if progress_callback:
                        progress_callback(done_bytes / total_bytes, f"Scanning tests: {source_names[idx]}")
        else:
            processed_bytes = sum(file_sizes[i] for i in tests_by_idx)
            for idx, file_path, ckey in uncached:
                file_size = file_sizes[idx]
                source_name = source_names[idx]

                def file_progress(current_bytes: int, file_total: int, base=processed_bytes, display_name=source_name, fsz=file_size):
                    if not progress_callback:
                        return
                    total_for_file = file_total if file_total > 0 else fsz
                    overall = (base + min(max(current_bytes, 0), total_for_file)) / total_bytes
                    progress_callback(overall, f"Scanning tests: {display_name}")

                scanned = scan_test_list(file_path, progress_callback=file_progress)
                tests_by_idx[idx] = scanned
                if ckey:
                    self._test_scan_cache[ckey] = scanned
                processed_bytes += file_size
                if progress_callback:
                    progress_callback(processed_bytes / total_bytes, f"Scanning tests: {source_name}")

        for idx in range(len(self.selected_files)):
            for test in tests_by_idx.get(idx, ()):
                test_num = test.get("TEST_NUM")
                if test_num is None:
                    continue
                existing = merged_tests.get(test_num)
                if existing is None:
                    merged_tests[test_num] = dict(test)
                    continue
                if not existing.get("TEST_TXT") and test.get("TEST_TXT"):
                    existing["TEST_TXT"] = test.get("TEST_TXT", "")
                if existing.get("_LO_LIMIT_TEXT") == "n/a" and test.get("_LO_LIMIT_TEXT") != "n/a":
                    existing["LO_LIMIT"] = test.get("LO_LIMIT")
                    existing["_LO_LIMIT_TEXT"] = test.get("_LO_LIMIT_TEXT")
                if existing.get("_HI_LIMIT_TEXT") == "n/a" and test.get("_HI_LIMIT_TEXT") != "n/a":
                    existing["HI_LIMIT"] = test.get("HI_LIMIT")
                    existing["_HI_LIMIT_TEXT"] = test.get("_HI_LIMIT_TEXT")
                if not existing.get("UNITS") and test.get("UNITS"):
                    existing["UNITS"] = test.get("UNITS", "")

        for existing in merged_tests.values():
            existing["_SEARCH_TEXT"] = " ".join(
                [
                    str(existing.get("TEST_NUM", "")),
                    str(existing.get("TEST_TXT", "")),
                    str(existing.get("UNITS", "")),
                    existing.get("_LO_LIMIT_TEXT", _format_limit_text(existing.get("LO_LIMIT"))),
                    existing.get("_HI_LIMIT_TEXT", _format_limit_text(existing.get("HI_LIMIT"))),
                ]
            ).lower()

        display_source = ", ".join(source_names[:3])
        if len(source_names) > 3:
            display_source += f" + {len(source_names) - 3} more"
        return sorted(merged_tests.values(), key=lambda row: row["TEST_NUM"]), display_source

    def _sync_hub_state(self, is_busy: bool):
        if not self.hub:
            return
        try:
            self.hub.set_tab_busy(self.app_name, is_busy)
            if not is_busy:
                self.hub.notify_tab_finished(self.app_name)
        except Exception as exc:
            logging.warning("Suppressed exception syncing STDF2SUM hub state: %s", exc)

    def _flush_pending_scan_state(self):
        running = self._pending_scan_state
        if running is None:
            return
        self._pending_scan_state = None
        self.is_scanning_tests = running
        self._sync_hub_state(running or self.is_running)
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        if running:
            self._start_progress_polling()
        elif not self.is_running:
            self._stop_progress_polling()

        scan_state = "disabled" if (running or self.is_running) else "normal"
        if self.show_all_tests_button is not None:
            self.show_all_tests_button.configure(state=scan_state)
        if self.clear_selected_tests_button is not None and self.selected_tests_var.get().strip():
            self.clear_selected_tests_button.configure(state=scan_state)

    def _set_test_scan_running(self, running: bool):
        self._pending_scan_state = running
        if threading.current_thread() is threading.main_thread():
            self._flush_pending_scan_state()
        elif self._progress_poll_id is None:
            self.is_scanning_tests = running
            try:
                self.root.after(0, self._flush_pending_scan_state)
            except Exception:
                pass

    def _apply_dialog_icon(self, win):
        try:
            ico = get_asset_path("FTDC_Checker_icon.ico")
            if os.path.isfile(ico):
                win.iconbitmap(ico)
        except Exception:
            pass

    def _open_test_window(self, tests: List[Dict], source_label: str):
        tk = self.tk_mod
        ttk = self.ttk
        if self._test_window and self._test_window.winfo_exists():
            self._test_window.destroy()
        self._cached_tests = list(tests)
        self._cached_test_rows = [
            (
                t.get("TEST_NUM", ""),
                t.get("TEST_TXT", ""),
                t.get("_LO_LIMIT_TEXT", _format_limit_text(t.get("LO_LIMIT"))),
                t.get("_HI_LIMIT_TEXT", _format_limit_text(t.get("HI_LIMIT"))),
                t.get("UNITS", ""),
            )
            for t in self._cached_tests
        ]
        self._test_filter_after_id = None
        self._test_window = tk.Toplevel(self.root)
        self._apply_dialog_icon(self._test_window)
        self._test_window.configure(bg=MAIN_BG)
        self._test_window.title("Show All Test - Combined STDF Selection")
        self._test_window.geometry("980x560")
        self._test_window.minsize(820, 420)

        container = ttk.Frame(self._test_window, padding=10)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        info_text = f"Source: {source_label}   |   Total tests: {len(tests)}"
        ttk.Label(container, text=info_text).grid(row=0, column=0, sticky="w", pady=(0, 8))

        filter_row = ttk.Frame(container)
        filter_row.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        filter_row.columnconfigure(1, weight=1)

        ttk.Label(filter_row, text="Filter").grid(row=0, column=0, sticky="w")
        self._test_filter_var = tk.StringVar()
        filter_entry = ModernEntry(filter_row, textvariable=self._test_filter_var)
        filter_entry.grid(row=0, column=1, sticky="ew", padx=(8, 10))

        button_frame = ttk.Frame(filter_row)
        button_frame.grid(row=0, column=2, sticky="e")
        ModernHoverButton(
            button_frame,
            text="Select",
            command=self._apply_selected_tests_from_window,
            padx=10,
            pady=3,
        ).pack(side="left", padx=(0, 6))
        ModernHoverButton(
            button_frame,
            text="Clear Selected Tests",
            command=self._clear_selected_test_filter,
            padx=10,
            pady=3,
        ).pack(side="left")

        ttk.Label(
            container,
            text='Type text like "Wafer" to filter matching parameters',
            foreground="#555555",
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))

        filter_entry.bind("<KeyRelease>", self._on_test_filter_keyrelease)
        filter_entry.focus_set()

        table_frame = ttk.Frame(container)
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("TEST_NUM", "TEST_TXT", "LO_LIMIT", "HI_LIMIT", "UNITS")
        self._test_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        self._test_tree.grid(row=0, column=0, sticky="nsew")

        widths = {"TEST_NUM": 110, "TEST_TXT": 420, "LO_LIMIT": 120, "HI_LIMIT": 120, "UNITS": 120}
        for col in columns:
            self._test_tree.heading(col, text=col)
            self._test_tree.column(col, width=widths[col], anchor="w")

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self._test_tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self._test_tree.configure(yscrollcommand=yscroll.set)

        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self._test_tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self._test_tree.configure(xscrollcommand=xscroll.set)

        self._refresh_test_tree()

    def _on_test_filter_keyrelease(self, _event=None):
        if self._test_filter_after_id is not None:
            try:
                self.root.after_cancel(self._test_filter_after_id)
            except Exception:
                pass
        self._test_filter_after_id = self.root.after(250, self._refresh_test_tree)

    def _refresh_test_tree(self):
        if not self._test_tree:
            return
        self._test_filter_after_id = None
        keyword = self._test_filter_var.get().strip().lower() if self._test_filter_var is not None else ""
        existing_selected = set()
        for tok in self.selected_tests_var.get().replace(",", " ").split():
            m = re.match(r"^(\d+)", tok)
            if m:
                existing_selected.add(m.group(1))
        children = self._test_tree.get_children()
        if children:
            self._test_tree.delete(*children)
        cached_tests = self._cached_tests
        cached_rows = self._cached_test_rows
        tree_insert = self._test_tree.insert
        to_select = []
        for i in range(len(cached_tests)):
            if keyword and keyword not in cached_tests[i].get("_SEARCH_TEXT", ""):
                continue
            item_id = tree_insert("", "end", values=cached_rows[i])
            if str(cached_tests[i].get("TEST_NUM", "")) in existing_selected:
                to_select.append(item_id)
        if to_select:
            self._test_tree.selection_set(to_select)

    def _apply_selected_tests_from_window(self):
        if not self._test_tree:
            return
        selected_items = self._test_tree.selection()
        if not selected_items:
            messagebox.showwarning("STDF2SUM", "Please select at least one test from the list.")
            return
        formatted_tests = []
        seen = set()
        for item in selected_items:
            values = self._test_tree.item(item, "values")
            if values:
                t_num = str(values[0]).strip()
                t_txt = str(values[1]).strip() if len(values) > 1 else ""
                if t_num and t_num not in seen:
                    seen.add(t_num)
                    formatted_tests.append(f"{t_num}_{t_txt}" if t_txt else t_num)
        self.selected_tests_var.set(", ".join(formatted_tests))
        self._update_selected_test_summary()
        self.log(f"Applied {len(formatted_tests)} selected test parameter(s) from Test Parameter Filter.")
        self.set_status("Test parameter selection updated")
        if self._test_window and self._test_window.winfo_exists():
            self._test_window.destroy()
            self._test_window = None
            self._test_tree = None
            self._test_filter_var = None
            if self._test_filter_after_id is not None:
                try:
                    self.root.after_cancel(self._test_filter_after_id)
                except Exception:
                    pass
                self._test_filter_after_id = None
            self._cached_test_rows = []

    def _clear_selected_test_filter(self):
        self.selected_tests_var.set("")
        if self._test_tree:
            for item in self._test_tree.selection():
                self._test_tree.selection_remove(item)
        self._update_selected_test_summary()
        self.log("Cleared test parameter selection.")
        self.set_status("Test parameter selection cleared")

    def show_all_tests(self):
        if self.is_running:
            messagebox.showwarning("STDF2SUM", "Conversion is currently running.")
            return
        if self.is_scanning_tests:
            return
        if not self.selected_files:
            messagebox.showwarning("STDF2SUM", "Please select at least one STDF file first.")
            return
        self.log(f"Scanning test parameters from {len(self.selected_files)} selected file(s)...")
        self.update_progress(0.0, "Scanning tests")
        self._set_test_scan_running(True)

        def worker():
            try:
                tests, source_label = self._scan_tests_for_selected_files(progress_callback=self.update_progress)
                self.update_progress(1.0, "Show All Test ready")
                self.log(f"Found {len(tests)} unique test parameter(s) across the selected STDF file(s).")
                self.set_status("Select test parameters to use as filter")
                self._pending_test_window = (tests, source_label)
            except Exception as exc:
                self.log(f"Error while scanning tests: {exc}")
                self.reset_progress("Test scan failed")
                self._pending_error_dialog = str(exc)
            finally:
                self._set_test_scan_running(False)

        threading.Thread(target=worker, daemon=True).start()

    # ── Conversion Execution & Hub Integration ────────────────────────────
    def _flush_pending_running_state(self):
        running = self._pending_running_state
        if running is None:
            return
        self._pending_running_state = None
        self.is_running = running
        self._sync_hub_state(running)
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        if running:
            self._start_progress_polling()
        else:
            self._stop_progress_polling()

        state = "disabled" if running else "normal"
        for btn in self._action_buttons:
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass
        for btn in self._auxiliary_buttons:
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass
        if self.clear_selected_tests_button is not None and self.selected_tests_var.get().strip():
            try:
                self.clear_selected_tests_button.configure(state=state)
            except Exception:
                pass

    def _set_running(self, running: bool):
        self._pending_running_state = running
        if threading.current_thread() is threading.main_thread():
            self._flush_pending_running_state()
        elif self._progress_poll_id is None:
            self.is_running = running
            try:
                self.root.after(0, self._flush_pending_running_state)
            except RuntimeError:
                pass

    def on_tab_activated(self):
        """Called by HubWindow when switching back to the STDF2SUM tab."""
        if self._pending_completion_dialog is not None:
            pdf_path, xlsx_path = self._pending_completion_dialog
            self._pending_completion_dialog = None
            self.root.after(50, lambda: self._show_success_dialog(pdf_path, xlsx_path))

    def start_conversion(self):
        if self.is_running:
            messagebox.showwarning("STDF2SUM", "Conversion is already running.")
            return
        if not self.selected_files:
            messagebox.showwarning("STDF2SUM", "Please select at least one STDF file.")
            return
        missing_files = [path for path in self.selected_files if not os.path.exists(path)]
        if missing_files:
            messagebox.showerror("STDF2SUM", "These files do not exist anymore:\n\n" + "\n".join(missing_files))
            return
        output_prefix = self.output_var.get().strip()
        if not output_prefix:
            output_prefix = self._get_suggested_output_prefix()
            self._set_output_value(output_prefix, auto_managed=True)
        try:
            filter_tests = parse_filter_values(
                self.tests_var.get(),
                self.selected_tests_var.get(),
                self.range_from_var.get(),
                self.range_to_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("STDF2SUM", str(exc))
            return

        self._update_selected_test_summary()
        self.log("=" * 80)
        self.log(f"Starting conversion for {len(self.selected_files)} file(s)...")
        self.log(f"Output prefix: {output_prefix}")
        self.log(f"PTR filter: {sorted(filter_tests) if filter_tests else 'All tests'}")
        self.update_progress(0.0, "Converting")
        self._set_running(True)

        files_snapshot = list(self.selected_files)

        def worker():
            try:
                files = convert_multiple_stdf_to_single_csv(
                    input_paths=files_snapshot,
                    output_prefix=output_prefix,
                    filter_tests=filter_tests,
                    verbose=False,
                    logger=self.log,
                    progress_callback=self.update_progress,
                )
                if files:
                    self.update_progress(1.0, "Conversion completed")
                    self.log("Conversion finished successfully.")
                    self.log("Created file(s):")
                    for file_path in files:
                        size_text = (
                            f"{os.path.getsize(file_path):,} bytes" if os.path.exists(file_path) else "size unavailable"
                        )
                        self.log(f"  {file_path}  ({size_text})")
                    self.set_status("Conversion completed")
                    xlsx_path = next((f for f in files if f.endswith(".xlsx")), None)
                    pdf_path = next((f for f in files if f.endswith(".pdf")), None)
                    self._pending_success_dialog = (pdf_path, xlsx_path)
                else:
                    self.log("No output file was created.")
                    self.reset_progress("No output created")
                    self._pending_warning_dialog = "No output file was created."
            except Exception as exc:
                self.log("Conversion failed.")
                self.log(str(exc))
                self.log(traceback.format_exc())
                self.reset_progress("Conversion failed")
                self._pending_error_dialog = str(exc)
            finally:
                self._set_running(False)

        threading.Thread(target=worker, daemon=True).start()

    def _show_success_dialog(self, pdf_path: Optional[str] = None, xlsx_path: Optional[str] = None):
        tk = self.tk_mod
        ttk = self.ttk
        dialog = tk.Toplevel(self.root)
        self._apply_dialog_icon(dialog)
        dialog.title("STDF2SUM Converter")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        msg_frame = ttk.Frame(dialog, padding=(20, 20, 20, 5))
        msg_frame.pack(fill="x")
        ttk.Label(msg_frame, text="Conversion finished successfully.", font=("Segoe UI", 10, "bold")).pack(anchor="w")

        info_frame = ttk.Frame(dialog, padding=(20, 5, 20, 5))
        info_frame.pack(fill="x")
        if pdf_path and os.path.exists(pdf_path):
            ttk.Label(
                info_frame,
                text='Click "Print Summary" to automatically generate and print summary.',
                foreground="#555555",
            ).pack(anchor="w")
        ttk.Label(
            info_frame,
            text='Click "OK" to open the output path folder.',
            foreground="#555555",
        ).pack(anchor="w")

        btn_frame = ttk.Frame(dialog, padding=(20, 5, 20, 15))
        btn_frame.pack(fill="x")

        def _on_ok():
            dialog.destroy()
            if xlsx_path:
                try:
                    output_dir = os.path.dirname(os.path.abspath(xlsx_path))
                    os.startfile(output_dir)
                except Exception as e_open:
                    self.log(f"Failed to open output directory: {e_open}")

        ok_btn = ModernHoverButton(btn_frame, text="OK", command=_on_ok, padx=16, pady=4)
        ok_btn.pack(side="right", padx=(6, 0))

        if pdf_path and os.path.exists(pdf_path):
            print_btn = ModernHoverButton(
                btn_frame,
                text="Print Summary",
                command=lambda: self._print_summary(pdf_path),
                padx=14,
                pady=4,
            )
            print_btn.pack(side="right", padx=(6, 0))

        dialog.update_idletasks()
        dlg_w = dialog.winfo_reqwidth()
        dlg_h = dialog.winfo_reqheight()
        x = self.root.winfo_x() + max((self.root.winfo_width() - dlg_w) // 2, 0)
        y = self.root.winfo_y() + max((self.root.winfo_height() - dlg_h) // 2, 0)
        dialog.geometry(f"+{x}+{y}")

        ok_btn.focus_set()
        dialog.wait_window()

    def _print_summary(self, pdf_path: str):
        try:
            abs_path = os.path.abspath(pdf_path)
            os.startfile(abs_path, "print")
            self.log(f"Print dialog opened for: {pdf_path}")
        except Exception as e:
            self.log(f"Direct printing failed: {e}. Falling back to opening the PDF file.")
            try:
                abs_path = os.path.abspath(pdf_path)
                os.startfile(abs_path)
                messagebox.showinfo(
                    "Print PDF",
                    "No application is associated to print PDF files directly on this system.\n\n"
                    "The PDF file has been opened in your default viewer. "
                    "Please use the Print option (usually Ctrl+P or the printer icon) inside the viewer to print.",
                )
            except Exception as open_err:
                self.log(f"Failed to open PDF file: {open_err}")
                self.log(traceback.format_exc())
                messagebox.showerror("Print Error", f"Failed to open PDF file: {open_err}")
