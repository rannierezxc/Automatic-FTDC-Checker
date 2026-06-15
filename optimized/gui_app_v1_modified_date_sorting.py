"""
Tkinter GUI for the optimized GUID checker.
Extracted from v25 monolith. Uses optimized parser and analysis modules.
"""
import os
import re
import struct
import threading
import traceback
import tkinter.font as tkfont
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
import xlsxwriter

from optimized.stdf_parser import (
    STDFReader, scan_test_list, parse_filter_values, parse_stdf_file,
    sort_paths_by_modified, _count_files_text, _format_limit_text,
    _emit_log, _format_stdf_timestamp, _prepare_scanned_test_row,
    ByteProgressFunc, LogFunc, ProgressFunc,
)
from optimized.guid_analysis import (
    analyze_guid_data, parse_panel_wxy_parts, resolve_wxy_test_numbers,
    MPC_WXY_TEST_MAP, WXY_KEYS, UNSUPPORTED_MPC_WXY_MESSAGE,
    normalize_mpc_key, _parse_wxy_from_mpc_text, _format_coord_value,
)
from optimized.excel_output import (
    write_result_excel, build_summary_and_details, build_details_text, _fmt,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
icon_path = os.path.join(BASE_DIR, 'FTDC_Checker_icon.ico')

def _path_modified_sort_key(path):
    try:
        modified_time = os.path.getmtime(path)
    except OSError:
        modified_time = float("inf")
    return modified_time, os.path.basename(path).lower()


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
        self.panel_files: Dict[str, List[str]] = {panel: [] for panel in self.PANELS}
        self.panel_listboxes: Dict[str, Any] = {}
        self.filter_widgets: List[Any] = []
        self.is_running = False
        self.is_scanning_tests = False
        self._test_window = None
        self._test_tree = None
        self._test_filter_var = None
        self._cached_tests: List[Dict[str, object]] = []
        self._test_scan_cache: Dict[Tuple[str, int, int], List[Dict[str, object]]] = {}
        self._analysis_cache: Dict[Tuple[object, ...], Dict[str, object]] = {}

        self.root.title("Automatic FTDC Checker")
        self.root.iconbitmap(default=icon_path)
        self.root.geometry("1180x720")
        self.root.minsize(980, 620)
        self.style = ttk.Style()
        self.style.configure("GetData.TButton", font=("Segoe UI", 12, "bold"), padding=(18, 14))

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
        self.progress_bar = None
        self._pending_progress = None  # (fraction, message) — atomic shared state
        self._progress_poll_id = None
        self._build_ui()
        self._update_selected_test_summary()
        self._sync_manual_filter_state()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}
        ttk = self.ttk
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        main.rowconfigure(4, weight=0)

        title_row = ttk.Frame(main)
        title_row.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 4))
        title_row.columnconfigure(0, weight=1)
        ttk.Label(title_row, text="Automatic FTDC Checker", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Button(title_row, text="Get FTDC Fail", command=self.start_get_ftdc_fail).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(title_row, text="Get STDF", command=self.start_get_stdf).grid(row=0, column=2, sticky="e", padx=(8, 0))

        inputs = ttk.LabelFrame(main, text="Lot / Quantity Inputs")
        inputs.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 4))
        for col in (1, 3, 5, 7):
            inputs.columnconfigure(col, weight=1)
        labels_vars = [
            ("Lot ID", self.lot_id_var, 18),
            ("MPC", self.mpc_var, 30),
            ("First Pass Actual Good QTY", self.fp_actual_good_qty_var, 14),
            ("Total Actual Good QTY", self.total_actual_good_qty_var, 14),
        ]
        for idx, (label, var, width) in enumerate(labels_vars):
            ttk.Label(inputs, text=label).grid(row=0, column=idx * 2, sticky="w", padx=(8, 4), pady=5)
            ttk.Entry(inputs, textvariable=var, width=width).grid(row=0, column=idx * 2 + 1, sticky="ew", padx=(0, 10), pady=5)

        top = ttk.Frame(main)
        top.grid(row=2, column=0, sticky="nsew", padx=8, pady=(2, 4))
        top.columnconfigure(0, weight=75)
        top.columnconfigure(1, weight=25)
        top.rowconfigure(0, weight=1)

        files_frame = ttk.LabelFrame(top, text="Input STDF Files")
        files_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        files_frame.columnconfigure(0, weight=1)
        for r in range(3):
            files_frame.rowconfigure(r, weight=1)
        for r, panel in enumerate(self.PANELS):
            self._build_file_panel(files_frame, panel, r)

        right_side = ttk.Frame(top)
        right_side.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right_side.columnconfigure(0, weight=1)
        right_side.rowconfigure(0, weight=1)
        right_side.rowconfigure(1, weight=0)

        filter_frame = ttk.LabelFrame(right_side, text="Test Parameter Filter")
        filter_frame.grid(row=0, column=0, sticky="nsew")
        filter_frame.columnconfigure(0, weight=1)

        filter_top = ttk.Frame(filter_frame)
        filter_top.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        filter_top.columnconfigure(2, weight=1)
        self.show_all_tests_button = ttk.Button(filter_top, text="Show All Test", command=self.show_all_tests)
        self.show_all_tests_button.grid(row=0, column=0, sticky="w")
        self.filter_widgets.append(self.show_all_tests_button)
        self.clear_selected_tests_button = ttk.Button(filter_top, text="Clear Selected Tests", command=self._clear_selected_test_filter)
        self.clear_selected_tests_button.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.clear_selected_tests_button.grid_remove()
        ttk.Checkbutton(filter_top, text="Manual Filter", variable=self.manual_filter_var, command=self._sync_manual_filter_state).grid(row=0, column=3, sticky="e")

        selected_frame = ttk.Frame(filter_frame)
        selected_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        selected_frame.columnconfigure(0, weight=1)
        ttk.Label(selected_frame, text="Test Parameter Selection").grid(row=0, column=0, sticky="w")
        self.selected_entry = ttk.Entry(selected_frame, textvariable=self.selected_tests_var, state="readonly")
        self.selected_entry.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.filter_widgets.append(self.selected_entry)
        ttk.Label(selected_frame, textvariable=self._selected_test_count_var, foreground="#555555").grid(row=2, column=0, sticky="w", pady=(2, 4))
        ttk.Label(selected_frame, text="Test Numbers").grid(row=3, column=0, sticky="w")
        self.typed_entry = ttk.Entry(selected_frame, textvariable=self.tests_var)
        self.typed_entry.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        self.filter_widgets.append(self.typed_entry)
        ttk.Label(selected_frame, text='Type numbers like "1001 1002" or "1001,1002"', foreground="#555555").grid(row=5, column=0, sticky="w", pady=(4, 0))

        range_row = ttk.Frame(filter_frame)
        range_row.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 4))
        range_row.columnconfigure(1, weight=1)
        range_row.columnconfigure(3, weight=1)
        ttk.Label(range_row, text="Range From").grid(row=0, column=0, sticky="w")
        self.range_from_entry = ttk.Entry(range_row, textvariable=self.range_from_var, width=12)
        self.range_from_entry.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        self.filter_widgets.append(self.range_from_entry)
        ttk.Label(range_row, text="Range To").grid(row=0, column=2, sticky="w")
        self.range_to_entry = ttk.Entry(range_row, textvariable=self.range_to_var, width=12)
        self.range_to_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        self.filter_widgets.append(self.range_to_entry)

        convert_frame = ttk.Frame(right_side)
        convert_frame.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        convert_frame.columnconfigure(0, weight=1)
        self.convert_button = ttk.Button(convert_frame, text="Get Data", command=self.start_get_data, width=28, style="GetData.TButton")
        self.convert_button.grid(row=0, column=0, sticky="ew", pady=(0, 4))


        log_controls = ttk.Frame(main)
        log_controls.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 2))
        log_controls.columnconfigure(1, weight=1)
        log_controls.columnconfigure(3, weight=0)
        ttk.Button(log_controls, text="Clear All", command=self.clear_all).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.progress_bar = ttk.Progressbar(log_controls, orient="horizontal", mode="determinate", maximum=100)
        self.progress_bar.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(log_controls, textvariable=self.progress_text_var, width=10, anchor="e").grid(row=0, column=2, sticky="e", padx=(0, 12))
        ttk.Label(log_controls, textvariable=self.status_var, anchor="e", width=32).grid(row=0, column=3, sticky="e")

        log_frame = ttk.LabelFrame(main, text="Status / Log")
        log_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 4))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        text_frame = ttk.Frame(log_frame)
        text_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=4)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.log_text = self.tk.Text(text_frame, wrap="word", height=3)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _build_file_panel(self, parent, panel: str, row_index: int):
        frame = self.ttk.LabelFrame(parent, text=panel)
        frame.grid(row=row_index, column=0, sticky="nsew", padx=8, pady=(4 if row_index == 0 else 3, 3))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        list_frame = self.ttk.Frame(frame)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=4)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        listbox = self.tk.Listbox(list_frame, selectmode=self.tk.EXTENDED, height=5)
        listbox.grid(row=0, column=0, sticky="nsew")
        scroll = self.ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        listbox.configure(yscrollcommand=scroll.set)
        self.panel_listboxes[panel] = listbox
        buttons = self.ttk.Frame(frame)
        buttons.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.ttk.Button(buttons, text="Select Files", command=lambda p=panel: self.select_files(p)).pack(side="left", padx=(0, 6))
        self.ttk.Button(buttons, text="Remove Selected", command=lambda p=panel: self.remove_selected_files(p)).pack(side="left", padx=(0, 6))
        self.ttk.Button(buttons, text="Clear Files", command=lambda p=panel: self.clear_files(p)).pack(side="left")

    def _sync_manual_filter_state(self):
        enabled = bool(self.manual_filter_var.get())
        state = "normal" if enabled and not self.is_running and not self.is_scanning_tests else "disabled"
        for widget in self.filter_widgets:
            try:
                widget.configure(state=("readonly" if widget is self.selected_entry and enabled else "disabled" if widget is self.selected_entry else state))
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

    def _file_modified_sort_key(self, path: str) -> Tuple[float, str]:
        return _path_modified_sort_key(path)

    def _sort_panel_files_by_modified(self, panel: str):
        self.panel_files[panel].sort(key=self._file_modified_sort_key)

    def _file_cache_signature(self, path: str) -> Tuple[str, int, int]:
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
        self._sort_panel_files_by_modified(panel)
        listbox = self.panel_listboxes[panel]
        listbox.delete(0, "end")
        for path in self.panel_files[panel]:
            listbox.insert("end", path)

    def refresh_all_file_lists(self):
        for panel in self.PANELS:
            self.refresh_file_list(panel)

    def select_files(self, panel: str):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            title=f"Select one or more {panel} STDF files",
            filetypes=[("STDF files", "*.std* *.old"), ("All files", "*.*")],
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
        for idx in reversed(indices):
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

    def _update_selected_test_summary(self):
        count = len(self.selected_tests_var.get().replace(",", " ").split())
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
        total = sum(max(os.path.getsize(path), 1) for path in files if os.path.exists(path)) or 1
        done = 0
        names = []
        for path in files:
            size = max(os.path.getsize(path), 1) if os.path.exists(path) else 1
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
        self.ttk.Button(buttons, text="Select", command=self._apply_selected_tests_from_window).pack(side="left", padx=(0, 6))
        self.ttk.Button(buttons, text="Clear Selected Tests", command=self._clear_selected_test_filter).pack(side="left")
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
        selected = set(self.selected_tests_var.get().replace(",", " ").split())
        for item in self._test_tree.get_children():
            self._test_tree.delete(item)
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
        nums, seen = [], set()
        for item in items:
            values = self._test_tree.item(item, "values")
            if values and str(values[0]) not in seen:
                seen.add(str(values[0]))
                nums.append(str(values[0]))
        self.selected_tests_var.set(" ".join(nums))
        self._update_selected_test_summary()
        self.log(f"Applied {len(nums)} selected test parameter(s) from Test Parameter Filter.")
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
        if not self.manual_filter_var.get() or self.is_scanning_tests:
            return
        if self.is_running:
            messagebox.showwarning("STDF GUID Checker", "Analysis is currently running.")
            return
        if not self._get_all_selected_files():
            messagebox.showwarning("STDF GUID Checker", "Please select at least one STDF file first.")
            return
        self.log(f"Scanning test parameters from {len(self._get_all_selected_files())} selected file(s)...")
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
        from tkinter import filedialog, messagebox

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
            save_path = filedialog.asksaveasfilename(
                parent=window,
                title="Download Result Excel",
                defaultextension=".xlsx",
                initialfile=default_name,
                filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
            )
            if not save_path:
                return

            try:
                write_result_excel(result, save_path)
                messagebox.showinfo(
                    "Download Excel",
                    f"Result Excel saved successfully:\n\n{save_path}",
                    parent=window,
                )
            except Exception as exc:
                messagebox.showerror("Download Excel Failed", str(exc), parent=window)

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

        title_text = "Automatic FTDC Checker"
        title = tk.Label(container, text=title_text, font=("Segoe UI", 11, "bold"), anchor="w", fg="#000000")
        title.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        panels = ttk.Frame(container)
        panels.grid(row=1, column=0, sticky="nsew")
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)

        for col, (section_title, rows) in enumerate(summary_sections):
            box = tk.Frame(panels, borderwidth=1, relief="solid", bg="#B7B7B7")
            box.grid(row=0, column=col, sticky="nsew", padx=(0, 5) if col == 0 else (5, 0))
            box.columnconfigure(0, weight=1)

            header = tk.Label(box, text=section_title, font=("Segoe UI", 10, "bold"), bg="#F0F0F0", anchor="w", padx=8, pady=4)
            header.grid(row=0, column=0, sticky="ew")

            table = tk.Frame(box, bg="#B7B7B7")
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

        show_details_button = ttk.Button(button_row, text="Show Details", command=toggle_details)
        show_details_button.grid(row=0, column=1, sticky="e", padx=(0, 8))
        ttk.Button(button_row, text="Download Excel", command=download_excel).grid(row=0, column=2, sticky="e", padx=(0, 8))
        ttk.Button(button_row, text="Close", command=window.destroy).grid(row=0, column=3, sticky="e")

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

    # ── Get STDF ───────────────────────────────────────────────────────────

    def start_get_stdf(self):
        """Search a network directory for STDF files matching the Lot ID and copy them locally."""
        from tkinter import messagebox
        from optimized.stdf_fetcher import (
            resolve_mpc_details, search_stdf_files, copy_stdf_files,
            TESTER_NETWORK_PATHS,
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
        if self.is_running:
            messagebox.showwarning(
                "Get STDF",
                "An operation is already running. Please wait for it to finish.",
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
            devices, testers = resolve_mpc_details(mpc_val)
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

        network_base = TESTER_NETWORK_PATHS.get(chosen_tester, TESTER_NETWORK_PATHS["LTX"])

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
        self.log(f"  Network base: {network_base}")

        def _search_worker():
            try:
                found = search_stdf_files(
                    lot_id_val, device_names,
                    network_base=network_base,
                    tester_type=chosen_tester,
                    logger=self.log, progress_callback=self.update_progress,
                )
                if not found:
                    def _no_files():
                        self._set_running(False)
                        self.reset_progress("No STDF files found")
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
                    messagebox.showerror(
                        "Get STDF — Network Error",
                        f"Unable to access the network directory.\n\n"
                        f"Please make sure your device is connected to the correct network "
                        f"and the path is reachable:\n  {network_base}\n\n"
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
        from optimized.stdf_fetcher import copy_stdf_files

        file_list = "\n".join(f"  • {os.path.basename(f)}" for f in found_files)
        confirm_msg = (
            f"Found {len(found_files)} STDF file(s) matching Lot ID '{lot_id}'\n"
            f"in device folder(s): {', '.join(device_names)}\n\n"
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

        if self.is_running:
            messagebox.showwarning(
                "Get FTDC Fail",
                "An analysis is already running. Please wait for it to finish.",
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
                    table = tk.Frame(frm, bg="#B7B7B7")
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
            if self.convert_button:
                self.convert_button.configure(state="disabled" if running else "normal")
            self._sync_manual_filter_state()
        self.root.after(0, apply)

    def start_get_data(self):
        from tkinter import messagebox
        # Always capitalize the Lot ID
        lot_id_upper = self.lot_id_var.get().strip().upper()
        self.lot_id_var.set(lot_id_upper)

        if self.is_running:
            messagebox.showwarning("STDF GUID Checker", "Analysis is already running.")
            return
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

        # Keep the visual file order and the parsing order aligned: Date Modified ascending.
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
        self.log("File parsing order: Date Modified ascending within each panel.")
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

