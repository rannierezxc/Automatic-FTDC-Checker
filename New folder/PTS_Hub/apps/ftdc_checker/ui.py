"""
Automatic FTDC Checker Application Frame.
Encapsulated within FTDCCheckerFrame (Zone 1, Zone 2, Zone 3).
"""
import atexit
import json
import logging
import os
import sys
import multiprocessing
import re
import threading
import traceback
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

APP_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
for p in (HUB_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.widgets import (
    ModernHoverButton, ModernEntry, ModernCard, ModernAccentButton, InfoTooltipButton,
    WidgetTooltip, attach_tooltip,
)
from core.config import (
    get_asset_path, get_app_root, APP_VERSION,
    get_mes_url, get_ftdc_fail_urls, get_local_dest_base, get_ppo_base_path,
    get_ppo_attach_timeout_sec, get_help_dir, is_developer_mode,
    FTDC_FAIL_PRIMARY_URL, FTDC_FAIL_BACKUP_URL, FTDC_FAIL_URLS,
    LOCAL_DEST_BASE, PPO_BASE_PATH, MES_URL,
)
from core.theme import (
    setup_theme, MAIN_BG, CARD_BG, BORDER_COLOR, TEXT_FG, TEXT_MUTED,
    LIST_BG, LISTBOX_BG, LISTBOX_FG, LISTBOX_SELECT_BG, LISTBOX_SELECT_FG,
    LISTBOX_BORDER, LOG_BG, LOG_FG, LOG_SELECT_BG, LOG_SELECT_FG,
    BADGE_EMPTY_BG, BADGE_EMPTY_FG, BADGE_ACTIVE_BG, BADGE_ACTIVE_FG, BADGE_READY_BG, BADGE_READY_FG,
    FONT_SUBTITLE, FONT_LABEL, FONT_LABEL_NORMAL, FONT_ENTRY,
    FONT_BUTTON_MINI, FONT_BADGE, FONT_STATUS, FONT_LOG
)
from apps.ftdc_checker.stdf_parser import (
    scan_test_list, parse_filter_values,
    _count_files_text, _format_limit_text,
    ByteProgressFunc, ProgressFunc, parse_check_summary,
    _path_filename_sort_key, _extract_first_filename_timestamp,
)
from apps.ftdc_checker.guid_analysis import (
    analyze_guid_data, UNSUPPORTED_MPC_WXY_MESSAGE,
    _parse_wxy_from_mpc_text, validate_mpc_code,
    compute_remarks, MPC_WXY_TEST_MAP, reload_wxy_config,
)
from apps.ftdc_checker.excel_output import (
    write_result_excel, build_summary_and_details, build_details_text, _fmt,
)
import tkinter.font as tkfont

from apps.ftdc_checker.result_image import (
    capture_window_to_image, render_result_image,
    save_result_window_image, get_default_result_image_name,
)
from apps.ftdc_checker.sg406_analysis import (
    resolve_summary_metric_style, clear_macid_validation_cache,
    format_macid_prefix_outlier_banner,
)
from apps.ftdc_checker.stdf_fetcher import (
    STDF_EXTENSIONS, STDF_FILEDIALOG_EXTENSIONS,
    resolve_mpc_details, search_stdf_files,
    copy_stdf_files_per_file, resolve_network_paths,
    existing_stdf_basenames, get_common_paths, reload_config,
    resolve_stdf_filename_conventions, compile_stdf_filename_patterns,
    load_mpc_partnumber_map, _load_stdf_filename_conventions_file,
    save_device_configuration, delete_mpc_configuration,
    format_parallel_copy_stats_text, _fast_copy_file_with_progress,
    _pushdown_scandir, _format_eta,
)

icon_path = get_asset_path("FTDC_Checker_icon.ico")

# ── Tooltip texts (info "i" buttons & hover tooltips) ────────────────────────
TOOLTIP_HELP_BUTTON = "Click to open the App Manual."
TOOLTIP_FP_ACTUAL_GOOD = (
    "Actual FP pass qty of last FT step prior QC (Ex: FT1,FT2,QC1 flow, FT2 is last ft step prior QC). "
    "See actual count in TSO/OTSR"
)
TOOLTIP_TOTAL_ACTUAL_GOOD = (
    "Sum of Actual FP pass qty & All RETEST pass qty of last FT step prior QC "
    "(Ex: FT1,FT2,QC1 flow, FT2 is last ft step prior QC). See actual count in TSO/OTSR"
)
TOOLTIP_FP_DUPLICATE_GOOD_UID = (
    "Count of PASS/GOOD Units in FP that have been RETESTED. "
    "Ex: 1_2_3 WXY  Already PASS in FP, but still seen same 1_2_3 WXY in any RETEST datalog. "
    "Possible affected of Handler Errors."
)
TOOLTIP_TOTAL_DUPLICATE_GOOD_UID = (
    "Sum of Duplicate UID in FP + Count of PASS/GOOD Units in RETEST that have been RETESTED again. "
    "Ex: 1_2_3 WXY  Already PASS in RETEST (1st RETEST) , but still seen same 1_2_3 WXY in other "
    "RETEST (2nd/3rd/nth RETEST) datalog. Possible affected of Handler Errors."
)
TOOLTIP_DUPLICATE_MACID = (
    "Duplicate MACID means for every 1 BT MACID or 1 ZB MACID among PASS units, it has multiple unique assigned UIDs. "
    "Ex: 1 BT MACID (40:84:32:04:95:52) has 2 PASS UIDs (1274021 & 1274023). "
    "One MACID should only be assigned to one UID. FAIL units are not counted as duplicate MACIDs."
)
TOOLTIP_UNIQUE_VALID_GOOD = (
    "Should be 1 UNIT =  1 WXY = 1 UID = 1 BT MACID = 1 ZB MACID. Any discrepancy is not allowed."
)

PANEL_TOOLTIPS: Dict[str, str] = {
    "FIRST PASS": (
        "Attach all First Pass (FP) STDF datalog files of the last FT step prior to QC (e.g., _e_ datalogs)."
    ),
    "RETEST": (
        "Attach all Retest (RT) STDF datalog files of the last FT step prior to QC "
        "(e.g., _w_rj1_, _w_rj2_,etc. datalogs)."
    ),
    "QC": (
        "Attach all QC Sampling STDF datalog files (e.g., _q_ or _q_rj1_ datalogs) "
        "to verify that all QC units passed in the previous FT operation."
    ),
}

TOOLTIP_BTN_GET_FTDC_FAIL = "Query the FTDC Logs server for any FAIL logs recorded for the entered Lot ID."
TOOLTIP_BTN_GET_STDF = (
    "Automatically search and copy First Pass, Retest, and QC STDF datalog files "
    "from the network for the certain device."
)
TOOLTIP_BTN_CHECK_STDF = (
    "Inspect Total Parts Tested, Total Good Parts, and Status "
    "of all loaded STDF files without manually opening before running analysis."
)
TOOLTIP_BTN_RUN_ANALYSIS = (
    "Run FTDC duplicate WXY / UID / MACID, quantity comparison, "
    "and QC verification analysis on the loaded STDF files."
)
TOOLTIP_BTN_DEVICE_SETTINGS = (
    "Open Device Settings to view, add, or update MPC, Mask, Tester, "
    "and WXY / MACID test number configurations."
)
TOOLTIP_BTN_CLEAR_ALL = "Clear all inputs, loaded STDF files, and reset status."
TOOLTIP_BTN_SHOW_ALL_TESTS = "Scan loaded STDF files and list all available test parameters."
TOOLTIP_BTN_CLEAR_SELECTED_TESTS = "Clear all selected manual filter test parameters."
HELP_PRESENTATION_EXTENSIONS = (".pptx", ".ppsx", ".ppt", ".pps", ".pptm")


class FTDCHttpError(RuntimeError):
    """Raised when both primary and backup FTDC endpoints return non-200 HTTP status."""
    def __init__(self, status_code: int, reason: str = ""):
        super().__init__(f"HTTP {status_code}: {reason or 'Unknown'}")
        self.status_code = status_code
        self.reason = reason or "Unknown"


def parse_ftdc_fail_html(html_text: str) -> List[Tuple[str, str, str]]:
    """Parse FTDC filter.php HTML table, dynamically locate required columns by header name, and return FAIL rows.

    Survives layout changes by dynamically locating:
      - 'Result' or 'Reply String'
      - 'Reply Message'
      - 'Comment'
    Raises ValueError with 'FTDC page layout changed. Expected columns: Result, Reply Message, Comment. Found : ...'
    if any required column is missing.
    """
    from lxml import html as _lxml_html

    if not html_text or not html_text.strip():
        return []

    def _cell_text(elem) -> str:
        return "".join(elem.xpath(".//text()")).strip()

    parsed = _lxml_html.fromstring(html_text)
    header_cells: List[str] = []
    for tr in parsed.xpath("//table//tr"):
        th_cells = [_cell_text(th) for th in tr.xpath(".//th")]
        if any(th_cells):
            header_cells = th_cells
            break

    raw_rows: List[List[str]] = []
    for tr in parsed.xpath("//table//tr"):
        cells = [_cell_text(td) for td in tr.xpath(".//td")]
        if any(cells):
            raw_rows.append(cells)

    # Fallback: if no <th> elements were used, check if the first <td> row is the header row
    if not header_cells and raw_rows:
        first_row = raw_rows[0]
        first_row_joined = " ".join(first_row).lower()
        if any(kw in first_row_joined for kw in ("reply", "comment", "result", "message")):
            header_cells = first_row
            raw_rows = raw_rows[1:]

    # Locate required columns dynamically by name
    result_col_idx: Optional[int] = None
    reply_msg_col_idx: Optional[int] = None
    comment_col_idx: Optional[int] = None
    tester_col_idx: Optional[int] = None

    for idx, h in enumerate(header_cells):
        h_norm = h.strip().lower()
        if result_col_idx is None and (
            h_norm in ("result", "reply string", "rstr")
            or "reply string" in h_norm
            or "result" in h_norm
        ):
            result_col_idx = idx
        elif reply_msg_col_idx is None and (
            h_norm == "reply message"
            or "reply message" in h_norm
        ):
            reply_msg_col_idx = idx
        elif comment_col_idx is None and (
            h_norm in ("comment", "comments")
            or "comment" in h_norm
        ):
            comment_col_idx = idx
        elif tester_col_idx is None and (
            "tester" in h_norm
            or "equip" in h_norm
        ):
            tester_col_idx = idx

    # Validate presence of all three required columns
    missing_cols = []
    if result_col_idx is None:
        missing_cols.append("Result")
    if reply_msg_col_idx is None:
        missing_cols.append("Reply Message")
    if comment_col_idx is None:
        missing_cols.append("Comment")

    if missing_cols or not header_cells:
        found_str = ", ".join(header_cells) if header_cells else "<none>"
        raise ValueError(
            f"FTDC page layout changed. Expected columns: Result, Reply Message, Comment. Found : {found_str}"
        )

    fail_entries: List[Tuple[str, str, str]] = []
    for r in raw_rows:
        if len(r) <= result_col_idx:
            continue
        if "FAIL" not in r[result_col_idx].upper():
            continue
        reply_msg = r[reply_msg_col_idx] if len(r) > reply_msg_col_idx else ""
        comment = r[comment_col_idx] if len(r) > comment_col_idx else ""

        tester_val = ""
        if tester_col_idx is not None and len(r) > tester_col_idx:
            tester_val = r[tester_col_idx].strip()
        else:
            for c in r:
                c_up = c.upper()
                if any(kw in c_up for kw in ("V93K", "LTX", "TESTER", "ADV", "FUSION", "D10")):
                    tester_val = c.strip()
                    break
            if not tester_val and len(r) > 3:
                tester_val = r[3].strip()

        fail_entries.append((reply_msg, comment, tester_val))

    return fail_entries


def fetch_and_parse_ftdc_fail(
    lot_id: str,
    timeout: int = 15,
    logger=None,
    on_response_received=None,
    on_scanning_rows=None,
) -> List[Tuple[str, str, str]]:
    """Fetch FTDC FAIL rows from primary URL with automatic fallback to backup URL, then parse."""
    import requests as _requests

    lot_id_upper = lot_id.strip().upper()
    payload = {
        "lotid": lot_id_upper,
        "mpc": "", "equipment": "", "stepname": "",
        "device": "", "rcode": "", "rstr": "",
        "bizrule": "", "submit": "",
    }
    last_exc: Optional[Exception] = None
    resp = None
    used_url = ""

    for idx, url in enumerate(FTDC_FAIL_URLS):
        link_label = "primary link" if idx == 0 else "backup link"
        try:
            r = _requests.post(url, data=payload, timeout=timeout)
            if r.status_code != 200:
                http_err = FTDCHttpError(r.status_code, r.reason or "Unknown")
                last_exc = http_err
                if logger:
                    if idx == 0:
                        logger(
                            f"Get FTDC Fail: Primary link ({url}) returned HTTP {r.status_code}; "
                            f"trying backup link {FTDC_FAIL_BACKUP_URL}…"
                        )
                    else:
                        logger(
                            f"Get FTDC Fail: Backup link ({url}) failed with HTTP {r.status_code}."
                        )
                continue
            resp = r
            used_url = url
            if logger:
                logger(f"Get FTDC Fail: Successfully connected using {link_label}: {used_url}")
            break
        except Exception as exc:
            last_exc = exc
            if logger:
                if idx == 0:
                    logger(
                        f"Get FTDC Fail: Primary link ({url}) failed ({exc}); "
                        f"trying backup link {FTDC_FAIL_BACKUP_URL}…"
                    )
                else:
                    logger(f"Get FTDC Fail: Backup link ({url}) failed ({exc}).")
            continue

    if resp is None:
        if logger:
            logger(
                f"Get FTDC Fail: FAILED for Lot ID '{lot_id_upper}' — both primary ({FTDC_FAIL_PRIMARY_URL}) "
                f"and backup ({FTDC_FAIL_BACKUP_URL}) links failed. Last error: {last_exc}"
            )
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Failed to reach FTDC server.")

    if on_response_received is not None:
        on_response_received()
    if on_scanning_rows is not None:
        on_scanning_rows()

    try:
        fail_rows = parse_ftdc_fail_html(resp.text)
    except ValueError as val_err:
        err_msg = str(val_err)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_dir = os.path.join(get_local_dest_base(), lot_id_upper)
        try:
            os.makedirs(dest_dir, exist_ok=True)
            html_path = os.path.join(dest_dir, f"ftdc_fail_layout_error_{timestamp_str}.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(resp.text)
        except Exception as save_err:
            html_path = f"<failed to save: {save_err}>"
            if logger:
                logger(f"[WARNING] Failed to save FTDC fail layout error HTML: {save_err}")
        if logger:
            logger(f"[ERROR] {err_msg}")
            if not html_path.startswith("<"):
                logger(f"Saved FTDC page HTML to: {html_path}")
        raise

    if logger:
        logger(
            f"Get FTDC Fail: SUCCESS for Lot ID '{lot_id_upper}' via {used_url} — "
            f"found {len(fail_rows)} FAIL row(s)."
        )
        for f_idx, (r_msg, r_comm, r_tst) in enumerate(fail_rows, start=1):
            logger(
                f"  FTDC Fail #{f_idx}: Reply Message='{r_msg}' | Comment='{r_comm}' | Tester='{r_tst}'"
            )
    return fail_rows


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
            if existing.get("LO_LIMIT") is None and row.get("LO_LIMIT") is not None:
                existing["LO_LIMIT"] = row.get("LO_LIMIT")
                existing["_LO_LIMIT_TEXT"] = row.get("_LO_LIMIT_TEXT", _format_limit_text(row.get("LO_LIMIT")))
            if existing.get("HI_LIMIT") is None and row.get("HI_LIMIT") is not None:
                existing["HI_LIMIT"] = row.get("HI_LIMIT")
                existing["_HI_LIMIT_TEXT"] = row.get("_HI_LIMIT_TEXT", _format_limit_text(row.get("HI_LIMIT")))
            existing["_SEARCH_TEXT"] = " ".join([
                str(existing.get("TEST_NUM", "")), str(existing.get("TEST_TXT", "")),
                str(existing.get("UNITS", "")), str(existing.get("_LO_LIMIT_TEXT", "")),
                str(existing.get("_HI_LIMIT_TEXT", "")),
            ]).lower()
    return sorted(merged.values(), key=lambda item: int(item["TEST_NUM"]))



class FTDCCheckerFrame(ttk.Frame):
    PANELS = ("FIRST PASS", "RETEST", "QC")
    ENABLE_TARGETED_PPO_ATTACH = False

    def __init__(self, parent, hub=None):
        import tkinter as tk
        from tkinter import ttk
        super().__init__(parent)
        self.parent = parent
        self.hub = hub
        self.app_name = "FTDC Checker"
        self.root = self.winfo_toplevel() if hasattr(self, "winfo_toplevel") else parent
        self.tk_mod = tk
        self.ttk = ttk
        try:
            if not getattr(self.root, "_pts_theme_initialized", False):
                self.style = setup_theme(self.root)
            else:
                self.style = ttk.Style(master=self.root)
        except Exception:
            self.style = ttk.Style()

        self.panel_files: Dict[str, List[str]] = {panel: [] for panel in self.PANELS}
        self.panel_listboxes: Dict[str, Any] = {}
        self._panel_scrollbars: Dict[str, Any] = {}
        self._panel_count_vars: Dict[str, tk.StringVar] = {panel: tk.StringVar(value="0 files") for panel in self.PANELS}
        self._panel_badges: Dict[str, Any] = {}
        self._panel_tooltip_buttons: Dict[str, Any] = {}
        self._help_loading_win = None
        self.filter_widgets: List[Any] = []
        self.is_running = False
        self.is_scanning_tests = False
        self._test_window = None
        self._test_tree = None
        self._test_filter_var = None
        self._cached_tests: List[Dict[str, object]] = []
        self._test_scan_cache: Dict[Tuple[str, int, int], List[Dict[str, object]]] = {}
        self._analysis_cache: Dict[Tuple[object, ...], Dict[str, object]] = {}

        self.lot_id_var = tk.StringVar()
        self.mpc_var = tk.StringVar()
        self.mpc_status_var = tk.StringVar(value="")
        self.mpc_status_label = None
        self.ppo_var = tk.StringVar()
        self._last_ppo_value = ""
        self.fp_actual_good_qty_var = tk.StringVar()
        self.total_actual_good_qty_var = tk.StringVar()
        self._session_log_lines: List[str] = []
        self._pending_log_messages: List[str] = []
        self._session_log_lock = threading.Lock()
        self._session_log_saved = False
        self._last_logged_lot_id = ""
        self._last_logged_details: Dict[str, str] = {}
        self._detail_log_after_ids: Dict[str, Any] = {}
        self.manual_filter_var = tk.BooleanVar(value=False)
        self.tests_var = tk.StringVar()
        self.selected_tests_var = tk.StringVar()
        self.range_from_var = tk.StringVar()
        self.range_to_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.progress_text_var = tk.StringVar(value="")
        self.copy_stats_var = tk.StringVar(value="")
        self.copy_stats_label = None
        self._selected_test_count_var = tk.StringVar(value="No test parameters selected")
        self.show_all_tests_button = None
        self.clear_selected_tests_button = None
        self.convert_button = None
        self.get_ftdc_button = None
        self.get_stdf_button = None
        self.check_stdf_button = None
        self._action_buttons = []
        self._auxiliary_buttons = []
        # Check STDF per-file summary cache, keyed by ((abs_path, size, mtime_ns), lot_id).
        # Persisted to disk so previously checked lots load instantly after a restart.
        self._check_summary_cache: Dict[Any, Tuple[str, str, str, str, str]] = {}
        self._check_cache_lock = threading.Lock()
        self._check_cache_generation: int = 0
        # In-flight Check STDF parses (sig -> Future) so a Check STDF window opened while a
        # background pre-warm is still parsing a file attaches to it instead of re-parsing.
        self._check_inflight: Dict[Any, Any] = {}
        self._check_prewarm_executor: Optional[ThreadPoolExecutor] = None
        # Streaming (per-file) Check STDF pre-warm executors that are active while Get STDF is copying.
        self._check_stream_executors: List[ThreadPoolExecutor] = []
        self._check_disk_save_lock = threading.Lock()
        self._load_check_cache_from_disk()
        self.progress_bar = None
        self._pending_progress = None  # (fraction, message) — atomic shared state
        self._pending_copy_stats: Optional[str] = None
        self._pending_running_state: Optional[bool] = None
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
        self._mpc_lookup_url = get_mes_url()
        self._pending_result_popup = None
        self._ftdc_fail_cache: Dict[str, List[Tuple[str, str, str]]] = {}
        self._ftdc_fail_threads: Dict[str, threading.Thread] = {}
        self._excel_lock = threading.Lock()
        self._excel_state = "IDLE"  # "IDLE", "IN_PROGRESS", "READY", "FAILED"
        self._excel_temp_path: Optional[str] = None
        self._excel_temp_filename: Optional[str] = None
        self._image_temp_path: Optional[str] = None
        self._image_temp_filename: Optional[str] = None
        self._excel_temp_files: Set[str] = set()
        self._excel_error: Optional[str] = None
        self._excel_bg_thread: Optional[threading.Thread] = None
        # Automatic PPO attachment (runs right after background Excel generation)
        self._excel_generation_id = 0  # bumped on every new generation / Clear All to cancel stale attaches
        self._excel_cancel_event = threading.Event()
        self._excel_ready_event = threading.Event()
        self._excel_ready_event.set()
        self._ppo_attach_state = "IDLE"  # "IDLE", "IN_PROGRESS", "SUCCESS", "PARTIAL", "FAILED"
        self._ppo_attach_thread: Optional[threading.Thread] = None
        self._pending_ppo_notices: List[Tuple[str, str, str, Optional[str]]] = []
        self._pending_ppo_status: Optional[Tuple[Optional[int], str]] = None
        self._ppo_status_poll_id = None
        self._result_window = None
        # Pre-warmed Device Settings Manager cache & state
        self._ds_cached_rows: List[Tuple[str, Tuple[str, str, str, str, str], Dict[str, Any]]] = []
        self._ds_cached_by_mpc: Dict[str, Dict[str, Any]] = {}
        self._ds_max_len_str: Dict[str, str] = {}
        self._ds_cache_ready: bool = False
        self._ds_cache_lock = threading.Lock()
        self._device_settings_win: Optional[tk.Toplevel] = None
        self._device_settings_sync_open_fn = None
        self._device_settings_refresh_fn = None
        self._build_ui()
        self._update_selected_test_summary()
        self._sync_manual_filter_state()
        # Attach MPC auto-lookup: fires after the user stops typing in Lot ID
        self.lot_id_var.trace_add("write", self._on_lot_id_changed)
        self.mpc_var.trace_add("write", self._on_mpc_changed)
        if self.ENABLE_TARGETED_PPO_ATTACH:
            self.ppo_var.trace_add("write", self._on_ppo_changed)
        self.fp_actual_good_qty_var.trace_add("write", self._on_fp_qty_changed)
        self.total_actual_good_qty_var.trace_add("write", self._on_tot_qty_changed)
        atexit.register(self._atexit_save_session_log)
        # Fix 6: prewarm DNS + TCP to the MES host at startup so the FIRST real
        # lookup does not pay cold connection setup. Runs in the background and
        # never blocks the UI (best-effort; failures are ignored).
        self.root.after(200, self._prewarm_mpc_connection)
        # Pre-warm the Device Settings Manager cache and dialog in the background
        # so clicking "Device Settings" opens and scrolls with zero lag.
        self.root.after(250, self._prewarm_device_settings_manager)


    def _build_ui(self):

        tk = self.tk_mod
        ttk = self.ttk

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # ── Upper Workspace Frame (Stable: never resizes on log height change) ──
        workspace = ttk.Frame(self)
        workspace.pack(fill="both", expand=True, padx=8, pady=(6, 125))

        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(1, weight=1)

        # ── 1. Lot & Production Configuration Card (No hint text, compact & clean) ──
        input_card = ModernCard(workspace, text="Lot Details", padx=10, pady=4)
        input_card.grid(row=0, column=0, sticky="ew", padx=2, pady=(0, 6))
        num_input_cols = 5 if self.ENABLE_TARGETED_PPO_ATTACH else 4
        for c in range(num_input_cols):
            input_card.columnconfigure(c, weight=1, uniform="input_cols")

        # Col 0: Lot ID
        tk.Label(input_card, text="Lot ID", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(
            row=0, column=0, sticky="w", padx=6, pady=(2, 1)
        )
        lot_entry = ModernEntry(input_card, textvariable=self.lot_id_var)
        lot_entry.grid(row=1, column=0, sticky="ew", padx=6, pady=(1, 4))
        lot_entry.bind("<FocusOut>", lambda _e: self._log_lot_detail_if_changed("Lot ID", self.lot_id_var.get()))

        # Col 1: MPC
        mpc_lbl_frame = tk.Frame(input_card, bg=CARD_BG)
        mpc_lbl_frame.grid(row=0, column=1, sticky="w", padx=6, pady=(2, 1))
        tk.Label(mpc_lbl_frame, text="MPC", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).pack(side="left")
        auto_badge = tk.Label(
            mpc_lbl_frame,
            text="Auto-fill",
            font=FONT_BADGE,
            bg=BADGE_ACTIVE_BG,
            fg=BADGE_ACTIVE_FG,
            padx=4,
            pady=0,
            relief="flat",
        )
        auto_badge.pack(side="left", padx=(6, 0))
        self.add_device_button = ModernHoverButton(
            mpc_lbl_frame,
            text="Device Settings",
            command=self.open_device_settings_dialog,
            padx=6,
            pady=1,
            font=("Segoe UI", 8, "bold"),
        )
        self.add_device_button.pack(side="left", padx=(6, 0))
        attach_tooltip(self.add_device_button, TOOLTIP_BTN_DEVICE_SETTINGS)
        self.mpc_status_label = tk.Label(
            mpc_lbl_frame,
            textvariable=self.mpc_status_var,
            font=("Segoe UI", 8),
            bg=CARD_BG,
            fg=TEXT_MUTED,
            padx=4,
            pady=0,
        )
        self.mpc_status_label.pack(side="left", padx=(4, 0))
        mpc_entry = ModernEntry(input_card, textvariable=self.mpc_var)
        mpc_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=(1, 4))
        mpc_entry.bind("<FocusOut>", lambda _e: self._log_lot_detail_if_changed("MPC", self.mpc_var.get()))

        qty_col_offset = 1 if self.ENABLE_TARGETED_PPO_ATTACH else 0
        if self.ENABLE_TARGETED_PPO_ATTACH:
            # Col 2: PPO# (hidden while targeted PPO attachment is disabled)
            tk.Label(input_card, text="PPO#", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(
                row=0, column=2, sticky="w", padx=6, pady=(2, 1)
            )
            ppo_entry = ModernEntry(input_card, textvariable=self.ppo_var)
            ppo_entry.grid(row=1, column=2, sticky="ew", padx=6, pady=(1, 4))
            ppo_entry.bind("<FocusOut>", self._on_ppo_focus_out)

        # Col 2/3: First Pass Actual Good QTY
        fp_col = 2 + qty_col_offset
        fp_lbl_frame = tk.Frame(input_card, bg=CARD_BG)
        fp_lbl_frame.grid(row=0, column=fp_col, sticky="w", padx=6, pady=(2, 1))
        tk.Label(fp_lbl_frame, text="First Pass Actual Good QTY", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).pack(side="left")
        InfoTooltipButton(fp_lbl_frame, text=TOOLTIP_FP_ACTUAL_GOOD, bg=CARD_BG).pack(side="left", padx=(4, 0))
        fp_qty_entry = ModernEntry(input_card, textvariable=self.fp_actual_good_qty_var)
        fp_qty_entry.grid(row=1, column=fp_col, sticky="ew", padx=6, pady=(1, 4))
        fp_qty_entry.bind("<FocusOut>", lambda _e: self._log_lot_detail_if_changed("First Pass Actual Good QTY", self.fp_actual_good_qty_var.get()))

        # Col 3/4: Total Actual Good QTY (+ Help button on the right of the label row)
        tot_col = 3 + qty_col_offset
        tot_lbl_frame = tk.Frame(input_card, bg=CARD_BG)
        tot_lbl_frame.grid(row=0, column=tot_col, sticky="ew", padx=6, pady=(2, 1))
        tk.Label(tot_lbl_frame, text="Total Actual Good QTY", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).pack(side="left")
        InfoTooltipButton(tot_lbl_frame, text=TOOLTIP_TOTAL_ACTUAL_GOOD, bg=CARD_BG).pack(side="left", padx=(4, 0))
        self.help_button = ModernHoverButton(
            tot_lbl_frame,
            text="\u2753 Help",
            command=self.open_help_presentation,
            padx=6,
            pady=1,
            font=("Segoe UI", 8, "bold"),
        )
        self.help_button.pack(side="right")
        attach_tooltip(self.help_button, TOOLTIP_HELP_BUTTON)
        tot_qty_entry = ModernEntry(input_card, textvariable=self.total_actual_good_qty_var)
        tot_qty_entry.grid(row=1, column=tot_col, sticky="ew", padx=6, pady=(1, 4))
        tot_qty_entry.bind("<FocusOut>", lambda _e: self._log_lot_detail_if_changed("Total Actual Good QTY", self.total_actual_good_qty_var.get()))

        # ── 2. Central Split: File Management (Left) vs Controls & Filter (Right) ─
        center = ttk.Frame(workspace)
        center.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        center.columnconfigure(0, weight=62)  # Files
        center.columnconfigure(1, weight=38)  # Filter & Controls
        center.rowconfigure(0, weight=1)

        # ── Left: Input STDF Files Container ──────────────────────────────
        files_box = ModernCard(center, text="Input STDF Datalogs", padx=6, pady=4)
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
        filter_header = tk.Frame(filter_card, bg=CARD_BG)
        filter_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        filter_header.columnconfigure(2, weight=1)
        self.filter_header = filter_header

        self.show_all_tests_button = ModernHoverButton(
            filter_header,
            text="Show All Test",
            command=self.show_all_tests,
            padx=8, pady=3,
        )
        self.show_all_tests_button.grid(row=0, column=0, sticky="w")
        attach_tooltip(self.show_all_tests_button, TOOLTIP_BTN_SHOW_ALL_TESTS)
        self.filter_widgets.append(self.show_all_tests_button)

        self.clear_selected_tests_button = ModernHoverButton(
            filter_header,
            text="Clear Selected",
            command=self._clear_selected_test_filter,
            padx=8, pady=3,
        )
        self.clear_selected_tests_button.grid(row=0, column=1, sticky="w", padx=(6, 0))
        attach_tooltip(self.clear_selected_tests_button, TOOLTIP_BTN_CLEAR_SELECTED_TESTS)
        self.clear_selected_tests_button.grid_remove()
        self.filter_widgets.append(self.clear_selected_tests_button)

        self.manual_filter_cb = ttk.Checkbutton(
            filter_header,
            text="Manual Filter",
            variable=self.manual_filter_var,
            command=self._sync_manual_filter_state,
        )
        self.manual_filter_cb.grid(row=0, column=2, sticky="e")

        self._filter_card_labels = []

        # Parameter Selection Field
        param_label = tk.Label(filter_card, text="Test Parameter Selection", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG)
        param_label.grid(row=1, column=0, sticky="w", pady=(3, 1))
        self._filter_card_labels.append(param_label)

        self.selected_entry = ModernEntry(filter_card, textvariable=self.selected_tests_var, state="readonly", font=("Segoe UI", 8))
        self.selected_entry.grid(row=2, column=0, sticky="ew")
        self.filter_widgets.append(self.selected_entry)

        count_label = tk.Label(filter_card, textvariable=self._selected_test_count_var, font=FONT_SUBTITLE, bg=CARD_BG, fg=TEXT_MUTED)
        count_label.grid(row=3, column=0, sticky="w", pady=(1, 4))
        self._filter_card_labels.append(count_label)

        # Direct Test Numbers Input
        num_label = tk.Label(filter_card, text="Test Numbers", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG)
        num_label.grid(row=4, column=0, sticky="w", pady=(2, 1))
        self._filter_card_labels.append(num_label)

        self.typed_entry = ModernEntry(filter_card, textvariable=self.tests_var)
        self.typed_entry.grid(row=5, column=0, sticky="ew")
        self.filter_widgets.append(self.typed_entry)

        fmt_label = tk.Label(
            filter_card,
            text='Format: space or comma separated (e.g. "1001 1002")',
            font=FONT_SUBTITLE, bg=CARD_BG, fg=TEXT_MUTED,
        )
        fmt_label.grid(row=6, column=0, sticky="w", pady=(1, 6))
        self._filter_card_labels.append(fmt_label)

        # Range Filtering
        range_frame = tk.Frame(filter_card, bg=CARD_BG)
        range_frame.grid(row=7, column=0, sticky="ew", pady=(2, 2))
        range_frame.columnconfigure(1, weight=1)
        range_frame.columnconfigure(3, weight=1)
        self.range_frame = range_frame

        rf_label = tk.Label(range_frame, text="Range From:", font=("Segoe UI", 8), bg=CARD_BG, fg=TEXT_MUTED)
        rf_label.grid(row=0, column=0, sticky="w", padx=(0, 4))
        self._filter_card_labels.append(rf_label)

        self.range_from_entry = ModernEntry(range_frame, textvariable=self.range_from_var)
        self.range_from_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.filter_widgets.append(self.range_from_entry)

        rt_label = tk.Label(range_frame, text="Range To:", font=("Segoe UI", 8), bg=CARD_BG, fg=TEXT_MUTED)
        rt_label.grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._filter_card_labels.append(rt_label)

        self.range_to_entry = ModernEntry(range_frame, textvariable=self.range_to_var)
        self.range_to_entry.grid(row=0, column=3, sticky="ew")
        self.filter_widgets.append(self.range_to_entry)

        # ── Right Bottom: Action Buttons Card (Swapped to Bottom, Renamed) ─
        action_card = ModernCard(right_box, text="Action Buttons", padx=8, pady=14)
        action_card.grid(row=1, column=0, sticky="ew")
        action_card.columnconfigure(0, weight=1)
        self.action_card = action_card

        # Top Row of Action Buttons: 3 utility buttons from left to right:
        # 1. Get FTDC Fail, 2. Get STDF, 3. Check STDF
        sec_frame = tk.Frame(action_card, bg=CARD_BG)
        sec_frame.grid(row=0, column=0, sticky="ew", pady=(2, 10))
        for c in range(3):
            sec_frame.columnconfigure(c, weight=1, uniform="action_btns")

        self.get_ftdc_button = ModernHoverButton(
            sec_frame,
            text="Get FTDC Fail",
            command=self.start_get_ftdc_fail,
            padx=6, pady=16, font=("Segoe UI", 12, "bold"),
        )
        self.get_ftdc_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        attach_tooltip(self.get_ftdc_button, TOOLTIP_BTN_GET_FTDC_FAIL)

        self.get_stdf_button = ModernHoverButton(
            sec_frame,
            text="Get STDF",
            command=self.start_get_stdf,
            padx=6, pady=16, font=("Segoe UI", 12, "bold"),
        )
        self.get_stdf_button.grid(row=0, column=1, sticky="ew", padx=3)
        attach_tooltip(self.get_stdf_button, TOOLTIP_BTN_GET_STDF)

        self.check_stdf_button = ModernHoverButton(
            sec_frame,
            text="Check STDF",
            command=self.start_check_stdf,
            padx=6, pady=16, font=("Segoe UI", 12, "bold"),
        )
        self.check_stdf_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        attach_tooltip(self.check_stdf_button, TOOLTIP_BTN_CHECK_STDF)

        # Bottom Row: Run Analysis (Get Data)
        self.convert_button = ModernAccentButton(
            action_card,
            text="⚡ Run Analysis (Get Data)",
            command=self.start_get_data,
            pady=20,
        )
        self.convert_button.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        attach_tooltip(self.convert_button, TOOLTIP_BTN_RUN_ANALYSIS)

        # All 4 action buttons registered
        self._action_buttons = [
            self.get_ftdc_button,
            self.get_stdf_button,
            self.check_stdf_button,
            self.convert_button,
        ]

        # ── 3. Live Progress & Execution Status Bar ────────────────────────
        progress_card = ModernCard(workspace, text="", padx=10, pady=6)
        progress_card.grid(row=2, column=0, sticky="ew", padx=2, pady=(4, 0))
        progress_card.columnconfigure(1, weight=1)

        # Status text, copy telemetry label & percentage
        status_row = tk.Frame(progress_card, bg=CARD_BG)
        status_row.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        status_row.columnconfigure(1, weight=1)

        tk.Label(status_row, text="Status:", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(row=0, column=0, sticky="w")
        tk.Label(
            status_row,
            textvariable=self.status_var,
            font=FONT_STATUS,
            bg=CARD_BG,
            fg=TEXT_FG,
        ).grid(row=0, column=1, sticky="w", padx=(6, 0))

        self.copy_stats_label = tk.Label(
            status_row,
            textvariable=self.copy_stats_var,
            font=FONT_LABEL_NORMAL,
            bg=CARD_BG,
            fg=TEXT_MUTED,
            anchor="e",
        )
        self.copy_stats_label.grid(row=0, column=2, sticky="e", padx=(10, 12))

        tk.Label(
            status_row,
            textvariable=self.progress_text_var,
            font=FONT_LABEL,
            bg=CARD_BG,
            fg=TEXT_FG,
            anchor="e",
        ).grid(row=0, column=3, sticky="e")

        # Clear All button + Default Tkinter Progressbar with Neon Green fill
        self.clear_all_button = ModernHoverButton(
            progress_card,
            text="Clear All",
            command=self.clear_all,
            padx=8, pady=3,
        )
        self.clear_all_button.grid(row=1, column=0, sticky="w", padx=(0, 10))
        attach_tooltip(self.clear_all_button, TOOLTIP_BTN_CLEAR_ALL)
        self._auxiliary_buttons.append(self.clear_all_button)
        if hasattr(self, "add_device_button") and self.add_device_button is not None:
            self._auxiliary_buttons.append(self.add_device_button)

        self.progress_bar = ttk.Progressbar(
            progress_card,
            style="Neon.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress_bar.grid(row=1, column=1, columnspan=3, sticky="ew")

        # ── 4. Overlay Log Drawer (Covers over bottom area with ZERO lag!) ──
        self._build_log_overlay_drawer(self)

    def _build_log_overlay_drawer(self, parent):
        tk = self.tk_mod
        ttk = self.ttk

        self._log_cur_height = 120
        self._log_drawer = tk.Frame(
            parent, bg=MAIN_BG, bd=0, relief="flat",
            highlightthickness=1, highlightbackground=BORDER_COLOR, highlightcolor=BORDER_COLOR
        )
    
        self._log_drawer.place(x=10, rely=1, relwidth=0.985, height=self._log_cur_height, anchor="sw")#

        # Drag handle bar (Clean default light styling)
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

        # Console Tool Buttons
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

        # Copy & Clear log buttons with ModernHoverButton
        copy_btn = ModernHoverButton(btn_box, text="Copy Log", command=self.copy_log, padx=6, pady=1, font=FONT_BUTTON_MINI)
        copy_btn.pack(side="left", padx=(6, 2))

        clear_btn = ModernHoverButton(btn_box, text="Clear Log", command=self.clear_log, padx=6, pady=1, font=FONT_BUTTON_MINI)
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

    def copy_log(self):
        """Copy entire log buffer to system clipboard."""
        try:
            content = self.log_text.get("1.0", "end-1c")
            if content.strip():
                self.root.clipboard_clear()
                self.root.clipboard_append(content)
                self.set_status("Log copied to clipboard")
        except Exception as exc:
            logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

    def clear_log(self):
        """Clear execution log console."""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _build_file_panel(self, parent, panel: str, row_index: int):
        tk = self.tk_mod
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
        hdr.columnconfigure(3, weight=1)

        dot = tk.Label(hdr, text="■", font=("Segoe UI", 9, "bold"), fg=accent_color)
        dot.grid(row=0, column=0, sticky="w", padx=(0, 4))

        panel_title_lbl = ttk.Label(hdr, text=panel, style="PanelTitle.TLabel")
        panel_title_lbl.grid(row=0, column=1, sticky="w")
        panel_tip_text = PANEL_TOOLTIPS.get(panel, "")
        if panel_tip_text:
            attach_tooltip(panel_title_lbl, panel_tip_text)
            panel_tip_btn = InfoTooltipButton(hdr, text=panel_tip_text, bg=CARD_BG)
            panel_tip_btn.grid(row=0, column=2, sticky="w", padx=(4, 0))
            self._panel_tooltip_buttons[panel] = panel_tip_btn

        count_var = self._panel_count_vars[panel]
        badge = tk.Label(
            hdr,
            textvariable=count_var,
            font=FONT_BADGE,
            bg=BADGE_EMPTY_BG,
            fg=BADGE_EMPTY_FG,
            padx=7,
            pady=1,
            relief="flat",
        )
        badge.grid(row=0, column=3, sticky="w", padx=(6, 0))
        self._panel_badges[panel] = badge

        # Header Action Buttons with ModernHoverButton
        btn_frame = ttk.Frame(hdr)
        btn_frame.grid(row=0, column=4, sticky="e")

        add_btn = ModernHoverButton(
            btn_frame,
            text="+ Add Files",
            command=lambda p=panel: self.select_files(p),
            padx=6, pady=2, font=FONT_BUTTON_MINI,
        )
        add_btn.pack(side="left", padx=(0, 3))
        attach_tooltip(add_btn, f"Browse and add {panel} STDF datalog files manually.")

        rem_btn = ModernHoverButton(
            btn_frame,
            text="Remove Selected",
            command=lambda p=panel: self.remove_selected_files(p),
            padx=6, pady=2, font=FONT_BUTTON_MINI,
        )
        rem_btn.pack(side="left", padx=2)
        attach_tooltip(rem_btn, f"Remove the selected file(s) from the {panel} list.")

        clr_btn = ModernHoverButton(
            btn_frame,
            text="Clear Files",
            command=lambda p=panel: self.clear_files(p),
            padx=6, pady=2, font=FONT_BUTTON_MINI,
        )
        clr_btn.pack(side="left", padx=(2, 0))
        attach_tooltip(clr_btn, f"Clear all files from the {panel} list.")
        self._auxiliary_buttons.extend((add_btn, rem_btn, clr_btn))

        # ── Listbox with Dynamic Auto-Hiding Scrollbar ─────────────────────
        list_frame = ttk.Frame(frame)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        listbox = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            height=3,
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
        is_busy = self.is_running or self.is_scanning_tests
        has_selected = bool(self.selected_tests_var.get().strip())
        state = "normal" if enabled and not is_busy else "disabled"

        # Requirement: Card BG is #F0F0F0 if manual filter is NOT enabled, otherwise #FFFFFF
        card_bg = "#FFFFFF" if enabled else "#F0F0F0"
        if hasattr(self, "filter_card") and self.filter_card:
            try:
                self.filter_card.configure(bg=card_bg)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        if hasattr(self, "filter_header") and self.filter_header:
            try:
                self.filter_header.configure(bg=card_bg)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        if hasattr(self, "range_frame") and self.range_frame:
            try:
                self.range_frame.configure(bg=card_bg)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        if hasattr(self, "_filter_card_labels"):
            for lbl in self._filter_card_labels:
                try:
                    lbl.configure(bg=card_bg)
                except Exception as exc:
                    logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        if hasattr(self, "manual_filter_cb") and self.manual_filter_cb:
            try:
                self.manual_filter_cb.configure(
                    style="White.TCheckbutton" if enabled else "TCheckbutton",
                    state="disabled" if is_busy else "normal",
                )
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

        for widget in self.filter_widgets:
            if widget is self.show_all_tests_button:
                btn_state = "normal" if enabled and not is_busy else "disabled"
                try:
                    widget.configure(state=btn_state)
                except Exception as exc:
                    logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
            elif widget is self.clear_selected_tests_button:
                self._sync_clear_selected_tests_button(has_selected)
            elif widget is self.selected_entry:
                try:
                    widget.configure(state="readonly" if enabled else "disabled")
                except Exception as exc:
                    logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
            else:
                try:
                    widget.configure(state=state)
                except Exception as exc:
                    logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        self._sync_clear_selected_tests_button(has_selected)

    def _get_all_selected_files(self) -> List[str]:
        return [path for panel in self.PANELS for path in self.panel_files[panel]]

    def _flush_pending_logs(self):
        with self._session_log_lock:
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
            logging.warning("Failed to append message to log_text widget: %s", exc)

    def log(self, message: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._session_log_lock:
            for line in str(message).splitlines() or [""]:
                self._session_log_lines.append(f"[{ts}] {line}")
            self._pending_log_messages.append(str(message))
            self._session_log_saved = False
        is_main = threading.current_thread() is threading.main_thread()
        if is_main:
            try:
                lot_cur = self.lot_id_var.get().strip().upper()
                if lot_cur:
                    self._last_logged_lot_id = lot_cur
            except Exception as exc:
                logging.debug("Could not read lot_id_var in log(): %s", exc)
            try:
                self.root.after(0, self._flush_pending_logs)
            except Exception as exc:
                logging.warning("Failed to schedule log append on root: %s", exc)
        elif self._progress_poll_id is None and self._ppo_status_poll_id is None:
            try:
                self.root.after(0, self._flush_pending_logs)
            except RuntimeError:
                pass
            except Exception as exc:
                logging.warning("Failed to schedule log append on root: %s", exc)

    DETAIL_LOG_DEBOUNCE_MS = 1000

    def _cancel_detail_log_timer(self, field_name: Optional[str] = None):
        if field_name is not None:
            aid = self._detail_log_after_ids.pop(field_name, None)
            if aid is not None:
                try:
                    self.root.after_cancel(aid)
                except Exception as exc:
                    logging.warning("Failed to cancel detail log timer for %s: %s", field_name, exc)
        else:
            for fname, aid in list(self._detail_log_after_ids.items()):
                if aid is not None:
                    try:
                        self.root.after_cancel(aid)
                    except Exception as exc:
                        logging.warning("Failed to cancel detail log timer for %s: %s", fname, exc)
            self._detail_log_after_ids.clear()

    def _schedule_log_lot_detail(self, field_name: str, delay_ms: Optional[int] = None):
        """Debounce logging of live input changes so intermediate keystrokes are not logged."""
        if delay_ms is None:
            delay_ms = self.DETAIL_LOG_DEBOUNCE_MS
        self._cancel_detail_log_timer(field_name)
        try:
            self._detail_log_after_ids[field_name] = self.root.after(
                delay_ms,
                lambda f=field_name: self._flush_debounced_lot_detail(f),
            )
        except Exception as exc:
            logging.warning("Failed to schedule detail log timer for %s: %s", field_name, exc)

    def _flush_debounced_lot_detail(self, field_name: str):
        self._detail_log_after_ids.pop(field_name, None)
        if field_name == "Lot ID":
            val = self.lot_id_var.get().strip()
            if val:
                self._log_lot_detail_if_changed("Lot ID", val.upper())
        elif field_name == "MPC":
            val = self.mpc_var.get().strip()
            if val:
                self._log_lot_detail_if_changed("MPC", val)
        elif field_name == "PPO#" and self.ENABLE_TARGETED_PPO_ATTACH:
            val = self.ppo_var.get().strip().upper()
            if val:
                self._log_lot_detail_if_changed("PPO#", val)
        elif field_name == "First Pass Actual Good QTY":
            val = self.fp_actual_good_qty_var.get().strip()
            if val and val.isdigit():
                self._log_lot_detail_if_changed("First Pass Actual Good QTY", val)
        elif field_name == "Total Actual Good QTY":
            val = self.total_actual_good_qty_var.get().strip()
            if val and val.isdigit():
                self._log_lot_detail_if_changed("Total Actual Good QTY", val)

    def _log_lot_detail_if_changed(self, field_name: str, raw_val: str):
        self._cancel_detail_log_timer(field_name)
        if field_name == "PPO#" and not self.ENABLE_TARGETED_PPO_ATTACH:
            return
        val = str(raw_val or "").strip()
        if not val:
            return
        if field_name == "Lot ID":
            val = val.upper()
            self._last_logged_lot_id = val
        elif field_name == "PPO#":
            val = val.upper()
        prev = self._last_logged_details.get(field_name)
        if prev != val:
            self._last_logged_details[field_name] = val
            self.log(f"Lot Details Input: {field_name} = '{val}'")

    def _log_current_lot_details_snapshot(self, context: str = ""):
        self._cancel_detail_log_timer()
        lot_val = self.lot_id_var.get().strip().upper()
        mpc_val = self.mpc_var.get().strip()
        ppo_val = self.ppo_var.get().strip().upper() if self.ENABLE_TARGETED_PPO_ATTACH else ""
        fp_qty = self.fp_actual_good_qty_var.get().strip()
        tot_qty = self.total_actual_good_qty_var.get().strip()
        if lot_val:
            self._last_logged_lot_id = lot_val
        detail_pairs = [
            ("Lot ID", lot_val),
            ("MPC", mpc_val),
        ]
        if self.ENABLE_TARGETED_PPO_ATTACH:
            detail_pairs.append(("PPO#", ppo_val))
        detail_pairs.extend([
            ("First Pass Actual Good QTY", fp_qty),
            ("Total Actual Good QTY", tot_qty),
        ])
        for k, v in detail_pairs:
            if v and self._last_logged_details.get(k) != v:
                self._last_logged_details[k] = v
                self.log(f"Lot Details Input: {k} = '{v}'")
        prefix = f"[{context}] " if context else ""
        ppo_snap = f"PPO#='{ppo_val or 'N/A'}', " if self.ENABLE_TARGETED_PPO_ATTACH else ""
        self.log(
            f"{prefix}Lot Details: Lot ID='{lot_val or 'N/A'}', MPC='{mpc_val or 'N/A'}', "
            f"{ppo_snap}"
            f"First Pass Actual Good QTY='{fp_qty or 'N/A'}', Total Actual Good QTY='{tot_qty or 'N/A'}'"
        )

    def _normalize_ppo_var(self) -> str:
        """Strip whitespace and capitalize the PPO# input field (no-op when disabled)."""
        if not self.ENABLE_TARGETED_PPO_ATTACH:
            self._last_ppo_value = ""
            return ""
        raw = self.ppo_var.get()
        norm = raw.strip().upper()
        self._last_ppo_value = norm
        if raw != norm:
            self.ppo_var.set(norm)
        return norm

    def _on_ppo_focus_out(self, _event=None):
        if not self.ENABLE_TARGETED_PPO_ATTACH:
            return
        norm = self._normalize_ppo_var()
        self._log_lot_detail_if_changed("PPO#", norm)

    def _on_ppo_changed(self, *_args):
        if not self.ENABLE_TARGETED_PPO_ATTACH:
            return
        ppo_v = self.ppo_var.get().strip().upper()
        self._last_ppo_value = ppo_v
        if ppo_v:
            self._schedule_log_lot_detail("PPO#")
        else:
            self._cancel_detail_log_timer("PPO#")

    def _on_fp_qty_changed(self, *args):
        fp_v = self.fp_actual_good_qty_var.get().strip()
        if fp_v and fp_v.isdigit():
            self._schedule_log_lot_detail("First Pass Actual Good QTY")
        else:
            self._cancel_detail_log_timer("First Pass Actual Good QTY")

    def _on_tot_qty_changed(self, *args):
        tot_v = self.total_actual_good_qty_var.get().strip()
        if tot_v and tot_v.isdigit():
            self._schedule_log_lot_detail("Total Actual Good QTY")
        else:
            self._cancel_detail_log_timer("Total Actual Good QTY")

    def _set_mpc_status(self, status_text: str, is_error: bool = False):
        self.mpc_status_var.set(status_text)
        if self.mpc_status_label is not None:
            try:
                fg_color = "#C00000" if is_error else TEXT_MUTED
                self.mpc_status_label.configure(fg=fg_color)
            except Exception as exc:
                logging.warning("Failed to update MPC status label color: %s", exc)

    def save_session_log_on_close(self) -> Optional[str]:
        r"""Save the session activity log to <PTS_Hub>\logs\FTDC_Logs_{timestamp}.log and clear all caches when the app closes."""
        self.shutdown_background_workers()
        for fname in list(self._detail_log_after_ids.keys()):
            try:
                self._flush_debounced_lot_detail(fname)
            except Exception as exc:
                logging.warning("Failed to flush pending detail log for %s on close: %s", fname, exc)
        try:
            with self._session_log_lock:
                if self._session_log_saved or not self._session_log_lines:
                    return None
                lines_snapshot = list(self._session_log_lines)
                self._session_log_saved = True

            app_dir = get_app_root()
            log_dir = os.path.join(app_dir, "logs")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = os.path.join(log_dir, f"FTDC_Logs_{timestamp}.log")
            try:
                os.makedirs(log_dir, exist_ok=True)
                close_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                lines_snapshot.append(f"[{close_ts}] Application closed. Log saved to {log_path}")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines_snapshot) + "\n")
                return log_path
            except Exception as exc:
                logging.warning("Failed to save session log to '%s': %s", log_path, exc)
                return None
        finally:
            self.clear_all_caches(clear_disk=True, reload_json=False)

    def _atexit_save_session_log(self):
        try:
            self.save_session_log_on_close()
        except Exception as exc:
            logging.warning("atexit session log save failed: %s", exc)

    def set_status(self, message: str):
        def apply():
            try:
                if self.root.winfo_exists():
                    self.status_var.set(message)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        try:
            self.root.after(0, apply)
        except Exception as exc:
            logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

    def update_progress(self, fraction: float, message: str = ""):
        # Thread-safe: just overwrite the shared state; the UI timer reads it
        self._pending_progress = (fraction, message)

    def _flush_pending_copy_stats(self):
        pending = self._pending_copy_stats
        if pending is None:
            return
        self._pending_copy_stats = None
        try:
            if self.root.winfo_exists():
                self.copy_stats_var.set(pending)
        except Exception as exc:
            logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

    def _flush_pending_progress(self):
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

    def _poll_progress(self):
        """Repeating UI timer — reads the latest progress and paints it once."""
        self._flush_pending_progress()
        self._flush_pending_copy_stats()
        self._flush_pending_logs()
        self._flush_pending_running_state()
        self._progress_poll_id = self.root.after(50, self._poll_progress)

    def _start_progress_polling(self):
        if self._progress_poll_id is None:
            self._progress_poll_id = self.root.after(50, self._poll_progress)

    def _stop_progress_polling(self):
        if self._progress_poll_id is not None:
            self.root.after_cancel(self._progress_poll_id)
            self._progress_poll_id = None
        # Flush any remaining pending update
        self._flush_pending_progress()
        self._flush_pending_copy_stats()
        self._flush_pending_logs()

    def set_copy_stats(self, text: str = ""):
        self._pending_copy_stats = text
        if threading.current_thread() is threading.main_thread():
            try:
                self.root.after(0, self._flush_pending_copy_stats)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        elif self._progress_poll_id is None and self._ppo_status_poll_id is None:
            try:
                self.root.after(0, self._flush_pending_copy_stats)
            except RuntimeError:
                pass
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

    def reset_progress(self, message: str = "Ready"):
        def apply():
            if self.progress_bar is not None:
                self.progress_bar.configure(value=0)
            self.progress_text_var.set("")
            self.copy_stats_var.set("")
            self.status_var.set(message)
        self.root.after(0, apply)

    def _file_name_sort_key(self, path: str) -> Tuple[str, str]:
        return _path_filename_sort_key(path)

    def _sort_panel_files_by_name(self, panel: str):
        self.panel_files[panel].sort(key=self._file_name_sort_key)

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

    # ── Check STDF: shared parse / cache / pre-warm machinery ────────────────

    CHECK_CACHE_MAX_ENTRIES = 5000
    CHECK_WORKER_CAP = 4  # bounded thread pool: limits GIL contention with the Tk main thread
    CHECK_PREWARM_WORKERS_DURING_COPY = 2  # streaming pre-warm cap while Get STDF copy threads are active

    @staticmethod
    def _signature_from_stat(path: str, st) -> Tuple[str, int, int]:
        """Build the same (abs_path, size, mtime_ns) signature as _file_cache_signature
        from an existing stat result (e.g. os.scandir DirEntry.stat(), which is free on Windows)."""
        modified_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        return (os.path.abspath(path), int(st.st_size), int(modified_ns))

    @staticmethod
    def _app_base_dir() -> str:
        """PTS Hub application directory (exe folder when frozen/compiled, project root otherwise)."""
        return get_app_root()

    def _check_cache_file_path(self) -> str:
        return os.path.join(self._app_base_dir(), "cache", "check_stdf_cache.json")

    def _load_check_cache_from_disk(self):
        """Load the persistent Check STDF cache. Invalid/corrupt files are ignored (cache miss)."""
        path = self._check_cache_file_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries", []) if isinstance(data, dict) else []
            loaded = 0
            with self._check_cache_lock:
                for item in entries:
                    try:
                        (fpath, size, mtime_ns, lot), value = item
                        if not isinstance(value, list) or len(value) != 5:
                            continue
                        sig = ((str(fpath), int(size), int(mtime_ns)), str(lot))
                        self._check_summary_cache[sig] = tuple(str(v) for v in value)
                        loaded += 1
                    except (TypeError, ValueError):
                        continue
            logging.info("Loaded %d Check STDF cache entr(ies) from '%s'", loaded, path)
        except Exception as exc:
            logging.warning("Failed to load Check STDF cache from '%s': %s", path, exc)

    def _save_check_cache_to_disk(self, expected_gen: Optional[int] = None):
        """Atomically persist the Check STDF cache (write temp file, then os.replace)."""
        path = self._check_cache_file_path()
        with self._check_cache_lock:
            if expected_gen is not None and expected_gen != self._check_cache_generation:
                return
            gen_snapshot = self._check_cache_generation
            items = list(self._check_summary_cache.items())
        if len(items) > self.CHECK_CACHE_MAX_ENTRIES:
            items = items[-self.CHECK_CACHE_MAX_ENTRIES:]  # dict preserves insertion order: keep newest
        entries = [
            [[sig[0][0], sig[0][1], sig[0][2], sig[1]], list(value[:5])]
            for sig, value in items
            if sig[0][1] >= 0  # never persist entries for files that could not be stat'ed
        ]
        with self._check_disk_save_lock:
            with self._check_cache_lock:
                if self._check_cache_generation != gen_snapshot:
                    return
            tmp_path = path + ".tmp"
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump({"version": 1, "entries": entries}, f)
                with self._check_cache_lock:
                    if self._check_cache_generation != gen_snapshot:
                        if os.path.isfile(tmp_path):
                            os.remove(tmp_path)
                        return
                os.replace(tmp_path, path)
            except Exception as exc:
                logging.warning("Failed to save Check STDF cache to '%s': %s", path, exc)

    def _save_check_cache_async(self):
        with self._check_cache_lock:
            gen = self._check_cache_generation
        threading.Thread(target=self._save_check_cache_to_disk, args=(gen,), daemon=True).start()

    def _clear_check_cache_from_disk(self):
        path = self._check_cache_file_path()
        with self._check_disk_save_lock:
            for candidate in (path, path + ".tmp"):
                try:
                    if os.path.isfile(candidate):
                        os.remove(candidate)
                except OSError as exc:
                    logging.warning("Failed to delete Check STDF cache '%s': %s", candidate, exc)

    def clear_all_caches(self, *, clear_disk: bool = True, reload_json: bool = True) -> None:
        """Clear all in-memory, module-level, temporary-file, and persistent disk caches."""
        self._test_scan_cache.clear()
        self._analysis_cache.clear()
        self._cached_tests.clear()
        self._mpc_cache.clear()
        self._ftdc_fail_cache.clear()
        self._ftdc_fail_threads.clear()
        self._last_logged_details.clear()
        self._pending_result_popup = None
        self._pending_ppo_notices.clear()
        try:
            self._cancel_detail_log_timer()
        except Exception as exc:
            logging.debug("Failed to cancel detail log timer while clearing caches: %s", exc)

        with self._check_cache_lock:
            self._check_cache_generation += 1
            self._check_summary_cache.clear()
        if clear_disk:
            self._clear_check_cache_from_disk()

        with self._excel_lock:
            self._excel_cancel_event.set()
            self._excel_ready_event.set()
            temp_candidates = set(self._excel_temp_files)
            if self._excel_temp_path:
                temp_candidates.add(self._excel_temp_path)
            if self._image_temp_path:
                temp_candidates.add(self._image_temp_path)
            for tf in temp_candidates:
                try:
                    if tf and os.path.isfile(tf):
                        os.remove(tf)
                except Exception as exc:
                    logging.warning("Failed to remove temp file '%s' while clearing caches: %s", tf, exc)
            self._excel_temp_files.clear()
            self._excel_temp_path = None
            self._excel_temp_filename = None
            self._image_temp_path = None
            self._image_temp_filename = None
            self._excel_state = "IDLE"
            self._excel_error = None
            self._excel_generation_id += 1  # cancel any pending PPO auto-attach retries
            self._ppo_attach_state = "IDLE"
            self._pending_ppo_status = None
            self._pending_copy_stats = None
            self._result_window = None

        with self._ds_cache_lock:
            self._ds_cached_rows.clear()
            self._ds_cached_by_mpc.clear()
            if isinstance(getattr(self, "_ds_max_len_str", None), dict):
                self._ds_max_len_str.clear()
            self._ds_cache_ready = False

        try:
            clear_macid_validation_cache()
        except Exception as exc:
            logging.debug("Failed to clear MACID validation cache: %s", exc)

        try:
            from apps.ftdc_checker import stdf_parser
            stdf_parser._DRIVE_REMOTE_CACHE.clear()
        except Exception as exc:
            logging.debug("Failed to clear STDF drive cache: %s", exc)

        try:
            from core import config as hub_config
            if reload_json:
                hub_config.reload_settings()
            else:
                hub_config._SETTINGS_CACHE = None
        except Exception as exc:
            logging.debug("Failed to clear settings cache: %s", exc)

        try:
            reload_config()
            if reload_json:
                reload_wxy_config()
        except Exception as exc:
            logging.warning("Failed to reload fetcher config cache while clearing caches: %s", exc)

    def _compute_check_stdf_row(self, fpath: str, lot_id: str):
        """Parse one STDF for Check STDF and derive the row values.

        Returns (status, tag, pc_str, gc_str, disc_str, log_lines). Warnings/errors are
        returned as lines (not logged one-by-one) so the caller can emit them in a single
        batched log call.
        """
        filename = os.path.basename(fpath)
        try:
            summary = parse_check_summary(fpath, expected_lot_id=lot_id, logger=None)
            log_lines: List[str] = list(summary.get("warnings") or []) + list(summary.get("errors") or [])
            mrr = summary.get("mrr")
            pcr = summary.get("pcr")
            far_valid = summary.get("far_valid", True)
            truncated = summary.get("truncated", False)
            malformed_cnt = summary.get("malformed_count", 0)
            extra_bytes_cnt = summary.get("extra_bytes_count", 0)
            disc_str = summary.get("discrepancy_text") or "None"

            # Integrity status from FAR + MRR FINISH_T + MRR as last record at EOF + truncation/malformed/extra-bytes
            mrr_is_last = summary.get("mrr_is_last", False)
            mrr_ok = False
            if mrr is not None and mrr_is_last:
                ft = mrr.get("FINISH_T")
                if (isinstance(ft, int) and ft > 0) or (isinstance(ft, str) and ft.strip()):
                    mrr_ok = True

            if not far_valid or truncated or malformed_cnt > 0 or not mrr_ok:
                status, tag = "Corrupted", "corrupted"
            elif extra_bytes_cnt > 0:
                status, tag = "Warning (Extra Bytes)", "warning"
            elif disc_str != "None":
                status, tag = "Mismatch", "warning"
            else:
                status, tag = "Completed", "completed"

            # Use PRR counts (prr_total and prr_good) for Total Parts and Tested Good
            prr_total = summary.get("prr_total")
            prr_good = summary.get("prr_good")
            if not far_valid and not prr_total and mrr is None and pcr is None:
                pc_str = gc_str = "N/A"
            elif prr_total is not None and (prr_total > 0 or mrr is not None or pcr is not None):
                pc_str = str(prr_total)
                gc_str = str(prr_good if prr_good is not None else 0)
            else:
                pc_str = gc_str = "N/A"

            log_lines.append(
                f"Check STDF [{filename}]: Status={status}, Total Parts={pc_str}, "
                f"Tested Good={gc_str}, Mismatch/Discrepancy={disc_str}"
            )
            return status, tag, pc_str, gc_str, disc_str, log_lines
        except Exception as exc:
            return (
                "Corrupted", "corrupted", "N/A", "N/A", f"Error: {exc}",
                [f"[ERROR] Check STDF failed for {filename}: {exc}"],
            )

    def _check_task(self, fpath: str, lot_id: str, sig):
        """Worker body: parse, cache the row under `sig`, and log all lines in ONE call."""
        status, tag, pc_str, gc_str, disc_str, log_lines = self._compute_check_stdf_row(fpath, lot_id)
        row = (status, tag, pc_str, gc_str, disc_str)
        # Only cache successful parses of files that could be stat'ed (errors are retried next time).
        if not disc_str.startswith("Error:") and sig[0][1] >= 0:
            with self._check_cache_lock:
                self._check_summary_cache.pop(sig, None)
                self._check_summary_cache[sig] = row  # (re)insert as newest
        if log_lines:
            self.log("\n".join(log_lines))
        return row

    def _submit_check_task(self, executor: ThreadPoolExecutor, fpath: str, lot_id: str, sig):
        """Submit a Check STDF parse, or return the already in-flight Future for the same sig."""
        with self._check_cache_lock:
            fut = self._check_inflight.get(sig)
            if fut is not None and not fut.cancelled():
                return fut
            fut = executor.submit(self._check_task, fpath, lot_id, sig)
            self._check_inflight[sig] = fut

        def _drop_inflight(f, s=sig):
            with self._check_cache_lock:
                if self._check_inflight.get(s) is f:
                    del self._check_inflight[s]

        fut.add_done_callback(_drop_inflight)
        return fut

    @staticmethod
    def _check_stdf_filename_sort_key(filename: str):
        """Sort key matching the Check STDF table row order: chronological filename
        timestamp ascending (first valid YYYYMMDDHHMMSS), then case-insensitive filename."""
        ts = _extract_first_filename_timestamp(filename)
        if ts:
            return 0, ts, filename.casefold()
        return 1, filename.casefold(), ""

    def _scan_stdf_folder(self, folder_path: str, stdf_extensions) -> List[Tuple[str, Tuple[str, int, int]]]:
        """List STDF files with their cache signatures using os.scandir, sorted in
        Check STDF table order (chronological filename timestamp ascending).

        On Windows, DirEntry.stat() is served from the directory enumeration data, so no
        per-file stat syscall is needed (and none happens on the Tk main thread).
        """
        found: List[Tuple[str, Tuple[str, int, int]]] = []
        with os.scandir(folder_path) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if os.path.splitext(entry.name)[1].lower() not in stdf_extensions:
                    continue
                try:
                    fsig = self._signature_from_stat(entry.path, entry.stat(follow_symlinks=False))
                except OSError:
                    fsig = (os.path.abspath(entry.path), -1, -1)
                found.append((entry.name, fsig))
        found.sort(key=lambda item: self._check_stdf_filename_sort_key(item[0]))
        return found

    def prewarm_check_stdf_cache(
        self,
        folder_path: str,
        lot_id: str,
        pending_futures: Optional[List[Any]] = None,
        stream_executor: Optional[ThreadPoolExecutor] = None,
    ):
        """Background pre-warm of the Check STDF cache (e.g. right after Get STDF copies files),
        so opening Check STDF afterwards is served from cache.

        ``pending_futures`` are streaming pre-warm tasks already submitted per-file while the
        copy was running (see ``_start_streaming_prewarm``); they are awaited here so the
        persistent cache JSON is flushed to disk exactly ONCE at the end of the batch.
        ``stream_executor`` (the 2-worker copy-phase executor) is shut down afterwards.
        """
        lot_id = (lot_id or "").strip().upper()
        pending_futures = list(pending_futures or [])

        def _run():
            try:
                futures: List[Any] = list(pending_futures)
                if os.path.isdir(folder_path):
                    files = self._scan_stdf_folder(folder_path, STDF_EXTENSIONS)
                    todo = []
                    with self._check_cache_lock:
                        for name, fsig in files:
                            sig = (fsig, lot_id)
                            if sig not in self._check_summary_cache:
                                todo.append((os.path.join(folder_path, name), sig))
                    if todo:
                        if self._check_prewarm_executor is None:
                            self._check_prewarm_executor = ThreadPoolExecutor(
                                max_workers=self.CHECK_WORKER_CAP, thread_name_prefix="check_stdf_prewarm",
                            )
                        # Files already in flight from the streaming pre-warm are de-duplicated by
                        # _submit_check_task (it returns the existing Future for the same signature).
                        self.log(f"Check STDF pre-warm: Checking {len(todo)} file(s) in background for '{lot_id}'.")
                        futures.extend(
                            self._submit_check_task(self._check_prewarm_executor, fpath, lot_id, sig)
                            for fpath, sig in todo
                        )
                if not futures:
                    return
                for fut in futures:
                    try:
                        fut.result()
                    except Exception as exc:
                        logging.warning("Check STDF pre-warm task failed: %s", exc)
                # Flush the persistent cache to disk ONCE for the whole batch.
                self._save_check_cache_to_disk()
            except Exception as exc:
                logging.warning("Check STDF pre-warm failed for '%s': %s", folder_path, exc)
            finally:
                if stream_executor is not None:
                    self._release_stream_executor(stream_executor)

        threading.Thread(target=_run, daemon=True, name="check_stdf_prewarm_scan").start()

    def _start_streaming_prewarm(self, lot_id: str):
        """Create a 2-worker executor for per-file Check STDF pre-warm during Get STDF copy.

        Returns ``(on_file_copied, executor, futures)``. ``on_file_copied(dest_path)`` is meant
        to be passed to ``copy_stdf_files_per_file`` and fires right after each file's atomic
        ``.part`` -> final rename. The executor is capped at
        ``CHECK_PREWARM_WORKERS_DURING_COPY`` so it never contends with the 4 copy threads and
        the Tk UI. Results are cached in memory only; the disk flush happens once at the end.
        """
        lot_id = (lot_id or "").strip().upper()
        executor = ThreadPoolExecutor(
            max_workers=self.CHECK_PREWARM_WORKERS_DURING_COPY,
            thread_name_prefix="check_stdf_stream_prewarm",
        )
        self._check_stream_executors.append(executor)
        futures: List[Any] = []
        futures_lock = threading.Lock()

        def on_file_copied(dest_path: str):
            if os.path.splitext(dest_path)[1].lower() not in STDF_EXTENSIONS:
                return
            try:
                fsig = self._signature_from_stat(dest_path, os.stat(dest_path))
            except OSError:
                return
            sig = (fsig, lot_id)
            with self._check_cache_lock:
                if sig in self._check_summary_cache:
                    return
            try:
                fut = self._submit_check_task(executor, dest_path, lot_id, sig)
            except RuntimeError:
                return  # executor already shut down (app closing)
            with futures_lock:
                futures.append(fut)

        return on_file_copied, executor, futures

    def _release_stream_executor(self, executor: ThreadPoolExecutor):
        try:
            self._check_stream_executors.remove(executor)
        except ValueError:
            pass
        try:
            executor.shutdown(wait=False)
        except Exception as exc:
            logging.warning("Failed to shut down streaming Check STDF executor: %s", exc)

    def shutdown_background_workers(self):
        """Cancel pending Check STDF pre-warm work and PPO auto-attach retries (called on application close)."""
        with self._excel_lock:
            self._excel_generation_id += 1
            self._excel_cancel_event.set()
        for sx in list(self._check_stream_executors):
            try:
                sx.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:
                logging.warning("Failed to shut down streaming Check STDF executor: %s", exc)
        self._check_stream_executors.clear()
        ex = self._check_prewarm_executor
        self._check_prewarm_executor = None
        if ex is not None:
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:
                logging.warning("Failed to shut down Check STDF pre-warm executor: %s", exc)

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

    def _show_help_loading_dialog(self):
        """Show a transient loading popup so the user knows the App Manual PPT is opening."""
        self._close_help_loading_dialog()
        try:
            if hasattr(self, "help_button") and self.help_button:
                self.help_button.configure(text="\u23F3 Opening…", state="disabled")
            self.set_status("Opening App Manual (PowerPoint)…")
            win = self.tk_mod.Toplevel(self.root)
            win.title("Opening App Manual…")
            win.configure(bg=CARD_BG)
            win.resizable(False, False)
            win.transient(self.root)
            try:
                win.attributes("-topmost", True)
            except Exception as exc:
                logging.debug("Could not set -topmost on help loading dialog: %s", exc)
            frm = self.tk_mod.Frame(win, bg=CARD_BG, padx=22, pady=16)
            frm.pack(fill="both", expand=True)
            self.tk_mod.Label(
                frm,
                text="\u23F3 Loading App Manual (PowerPoint)…",
                font=("Segoe UI", 10, "bold"),
                bg=CARD_BG,
                fg=TEXT_FG,
            ).pack(anchor="center", pady=(0, 6))
            self.tk_mod.Label(
                frm,
                text="Please wait while the presentation is being opened.",
                font=("Segoe UI", 8),
                bg=CARD_BG,
                fg=TEXT_MUTED,
            ).pack(anchor="center", pady=(0, 10))
            pbar = self.ttk.Progressbar(frm, orient="horizontal", mode="indeterminate", length=260)
            pbar.pack(fill="x")
            pbar.start(12)
            win.update_idletasks()
            w = max(win.winfo_reqwidth(), 310)
            h = max(win.winfo_reqheight(), 105)
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = max(self.root.winfo_width(), 400)
            rh = max(self.root.winfo_height(), 300)
            x = max(rx + (rw - w) // 2, 20)
            y = max(ry + (rh - h) // 2, 20)
            win.geometry(f"{w}x{h}+{x}+{y}")
            self._help_loading_win = win
        except Exception:
            self._help_loading_win = None

    def _close_help_loading_dialog(self, status_msg: Optional[str] = None):
        win, self._help_loading_win = getattr(self, "_help_loading_win", None), None
        if win is not None:
            try:
                win.destroy()
            except Exception as exc:
                logging.debug("Failed to destroy help loading dialog: %s", exc)
        try:
            if hasattr(self, "help_button") and self.help_button:
                self.help_button.configure(text="\u2753 Help", state="normal")
            if status_msg:
                self.set_status(status_msg)
        except Exception as exc:
            logging.debug("Failed to restore Help button state: %s", exc)

    def open_help_presentation(self):
        """Find the PowerPoint presentation inside the configured Help folder and open the PPT file directly."""
        help_dir = get_help_dir()
        self._show_help_loading_dialog()

        def _worker():
            try:
                if not os.path.isdir(help_dir):
                    def _no_dir():
                        self._close_help_loading_dialog("Ready")
                        messagebox.showerror(
                            "Help — Folder Not Accessible",
                            f"Unable to access the Help directory:\n\n{help_dir}\n\n"
                            "Please make sure you are connected to the network and try again.",
                            parent=self.root,
                        )
                    self.root.after(0, _no_dir)
                    return

                ppt_candidates: List[Tuple[float, str, str]] = []
                with os.scandir(help_dir) as it:
                    for entry in it:
                        try:
                            if not entry.is_file():
                                continue
                            name = entry.name
                            if name.startswith("~$"):
                                continue
                            if name.lower().endswith(HELP_PRESENTATION_EXTENSIONS):
                                st = entry.stat()
                                ppt_candidates.append((float(getattr(st, "st_mtime", 0.0)), name.lower(), entry.path))
                        except OSError:
                            continue

                if not ppt_candidates:
                    def _no_ppt():
                        self._close_help_loading_dialog("Ready")
                        messagebox.showwarning(
                            "Help — Presentation Not Found",
                            f"No PowerPoint presentation (.pptx / .ppt) was found in:\n\n{help_dir}",
                            parent=self.root,
                        )
                    self.root.after(0, _no_ppt)
                    return

                # Pick the most recently modified presentation file in the Help folder
                ppt_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                chosen_ppt = ppt_candidates[0][2]
                self.log(f"Help: Opening presentation '{os.path.basename(chosen_ppt)}' from {help_dir}")
                os.startfile(chosen_ppt)
                self.root.after(1200, lambda: self._close_help_loading_dialog("App Manual opened"))
            except Exception as exc:
                _msg = str(exc)
                def _err(m=_msg):
                    self._close_help_loading_dialog("Ready")
                    messagebox.showerror(
                        "Help Error",
                        f"Failed to open Help presentation from:\n{help_dir}\n\nDetails: {m}",
                        parent=self.root,
                    )
                self.root.after(0, _err)

        threading.Thread(target=_worker, daemon=True, name="open_help_ppt").start()

    def refresh_file_list(self, panel: str):
        self._sort_panel_files_by_name(panel)
        listbox = self.panel_listboxes[panel]
        listbox.delete(0, "end")
        if self.panel_files[panel]:
            listbox.insert("end", *self.panel_files[panel])
        count = len(self.panel_files[panel])
        if hasattr(self, "_panel_count_vars") and panel in self._panel_count_vars:
            self._panel_count_vars[panel].set(f"{count} file{'s' if count != 1 else ''}")
        if hasattr(self, "_panel_badges") and panel in self._panel_badges:
            badge = self._panel_badges[panel]
            if count > 0:
                badge.configure(bg=BADGE_READY_BG, fg=BADGE_READY_FG)
            else:
                badge.configure(bg=BADGE_EMPTY_BG, fg=BADGE_EMPTY_FG)
        if hasattr(self, "_update_panel_scrollbar"):
            self._update_panel_scrollbar(panel)

    def refresh_all_file_lists(self):
        for panel in self.PANELS:
            self.refresh_file_list(panel)

    def select_files(self, panel: str):
        # Default to the FTDC extraction folder for the current Lot ID
        initial_dir = None
        lot_id = self.lot_id_var.get().strip()
        if lot_id:
            candidate = os.path.join(LOCAL_DEST_BASE, lot_id.upper())
            if os.path.isdir(candidate):
                initial_dir = candidate

        paths = filedialog.askopenfilenames(
            title=f"Select one or more {panel} STDF files",
            filetypes=[("STDF files", STDF_FILEDIALOG_EXTENSIONS), ("All files", "*.*")],
            initialdir=initial_dir,
        )
        if not paths:
            return
        added = 0
        existing = set(self.panel_files[panel])
        for path in paths:
            if path not in existing:
                self.panel_files[panel].append(path)
                existing.add(path)
                added += 1
        self.refresh_file_list(panel)
        self.log(f"[{panel}] Added {added} file(s). Total loaded: {len(self.panel_files[panel])}")

    def remove_selected_files(self, panel: str):
        indices = list(self.panel_listboxes[panel].curselection())
        if not indices:
            return
        for idx in sorted([int(i) for i in indices], reverse=True):
            if 0 <= idx < len(self.panel_files[panel]):
                del self.panel_files[panel][idx]
        self.refresh_file_list(panel)
        self.log(f"[{panel}] Removed {len(indices)} selected file(s).")

    def clear_files(self, panel: str):
        count = len(self.panel_files[panel])
        self.panel_files[panel].clear()
        self.refresh_file_list(panel)
        self.log(f"[{panel}] Cleared {count} file(s).")

    def clear_all(self):
        with self._session_log_lock:
            self._pending_log_messages.clear()
        self._pending_copy_stats = None
        self._pending_progress = None
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state="disabled")
        self.progress_bar.configure(value=0)
        self.status_var.set("Ready")
        self.progress_text_var.set("")
        self.copy_stats_var.set("")
        
        self.lot_id_var.set("")
        self.mpc_var.set("")
        self.ppo_var.set("")
        self._last_ppo_value = ""
        self.fp_actual_good_qty_var.set("")
        self.total_actual_good_qty_var.set("")
        
        self.manual_filter_var.set(False)
        self.tests_var.set("")
        self.selected_tests_var.set("")
        self.range_from_var.set("")
        self.range_to_var.set("")
        self._selected_test_count_var.set("No test parameters selected")
        self._sync_manual_filter_state()
        
        self.mpc_status_var.set("")
        self._set_mpc_status("", is_error=False)
        self.clear_all_caches(clear_disk=True, reload_json=True)
        if self.is_running:
            self._set_running(False)
        
        for panel in self.PANELS:
            self.panel_files[panel].clear()
            self.refresh_file_list(panel)
            if hasattr(self, "_panel_count_vars") and panel in self._panel_count_vars:
                self._panel_count_vars[panel].set("0 files")
            if hasattr(self, "_panel_badges") and panel in self._panel_badges:
                self._panel_badges[panel].configure(bg=BADGE_EMPTY_BG, fg=BADGE_EMPTY_FG)
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
        enabled = bool(self.manual_filter_var.get())
        is_busy = self.is_running or self.is_scanning_tests
        if has_selected_tests:
            self.clear_selected_tests_button.grid()
            btn_state = "normal" if enabled and not is_busy else "disabled"
            try:
                self.clear_selected_tests_button.configure(state=btn_state)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
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
        if self.hub:
            try:
                self.hub.set_tab_busy(self.app_name, running)
                if not running:
                    self.hub.notify_tab_finished(self.app_name)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        def apply():
            try:
                if not self.root.winfo_exists():
                    return
            except Exception:
                return
            if running:
                self._start_progress_polling()
            else:
                self._stop_progress_polling()
            self._sync_manual_filter_state()
        try:
            self.root.after(0, apply)
        except Exception as exc:
            logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

    def _open_test_window(self, tests: List[Dict[str, object]], source_label: str):
        import tkinter as tk
        if self._test_window and self._test_window.winfo_exists():
            self._test_window.destroy()
        self._cached_tests = list(tests)
        self._test_window = tk.Toplevel(self.root)
        self._test_window.configure(bg=MAIN_BG)
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
        entry = ModernEntry(filter_row, textvariable=self._test_filter_var)
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
        to_select = []
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
                to_select.append(item_id)
        if to_select:
            self._test_tree.selection_set(to_select)

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
        if not self.manual_filter_var.get():
            return
        if self.is_scanning_tests:
            return
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
                self.root.after(0, lambda m=str(exc): messagebox.showerror("STDF GUID Checker", m))
            finally:
                self._set_test_scan_running(False)

        threading.Thread(target=worker, daemon=True).start()


    def on_tab_activated(self):
        """Called by HubWindow when switching to the FTDC Checker tab."""
        if getattr(self, "_pending_result_popup", None) is not None:
            pending = self._pending_result_popup
            self._pending_result_popup = None
            self.root.after(50, lambda: self._show_result_popup(pending))
        if self._pending_ppo_notices:
            self.root.after(400, self._flush_ppo_notices)

    def _get_effective_ppo_num(self, result: Optional[Dict[str, object]] = None) -> str:
        """Return stripped & capitalized PPO# when targeted PPO attach is enabled, else ''."""
        if not self.ENABLE_TARGETED_PPO_ATTACH:
            return ""
        if result is not None and "ppo_num" in result:
            raw = result.get("ppo_num")
        else:
            try:
                raw = self.ppo_var.get()
                self._last_ppo_value = str(raw or "").strip().upper()
            except Exception:
                raw = getattr(self, "_last_ppo_value", "")
        return str(raw or "").strip().upper()

    def _resolve_ppo_target_subfolders(
        self,
        base_path: str,
        ppo_num: str = "",
        pre_discovered: Optional[List[str]] = None,
        create_if_missing: bool = True,
    ) -> List[str]:
        """Resolve target PPO subfolder(s) inside ``base_path`` for attachment.

        When targeted PPO# attachment is enabled and ``ppo_num`` is provided,
        searches for or creates only that matching subfolder. Otherwise (default
        when disabled), returns all existing subfolders inside ``base_path``.
        """
        ppo_target = str(ppo_num or "").strip().upper() if self.ENABLE_TARGETED_PPO_ATTACH else ""
        base_exists: Optional[bool] = None

        def _base_isdir() -> bool:
            nonlocal base_exists
            if base_exists is None:
                base_exists = os.path.isdir(base_path)
            return base_exists

        if pre_discovered is not None:
            subfolders = [p for p in pre_discovered if os.path.isdir(p)]
        else:
            subfolders = []

        if not subfolders and _base_isdir():
            try:
                push_entries = _pushdown_scandir(base_path, "*", dirs_only=True)
                subfolders = [e.path for e in push_entries] if push_entries else []
            except Exception:
                subfolders = []
            if not subfolders:
                with os.scandir(base_path) as entries:
                    subfolders = [e.path for e in entries if e.is_dir()]

        if ppo_target:
            matched = [
                p for p in subfolders
                if os.path.basename(os.path.normpath(p)).strip().upper() == ppo_target
            ]
            if matched:
                return matched

            # If pre_discovered was provided and missed ppo_target, check disk directly
            if pre_discovered is not None and _base_isdir():
                try:
                    push_entries = _pushdown_scandir(base_path, "*", dirs_only=True)
                    disk_subs = [e.path for e in push_entries] if push_entries else []
                except Exception:
                    disk_subs = []
                if not disk_subs:
                    with os.scandir(base_path) as entries:
                        disk_subs = [e.path for e in entries if e.is_dir()]
                matched = [
                    p for p in disk_subs
                    if os.path.basename(os.path.normpath(p)).strip().upper() == ppo_target
                ]
                if matched:
                    return matched

            if not create_if_missing:
                raise FileNotFoundError(
                    f"PPO# subfolder '{ppo_target}' does not exist in: {base_path}"
                )

            if not _base_isdir() and not os.path.isdir(get_ppo_base_path()):
                raise FileNotFoundError(f"PPO target folder does not exist: {base_path}")

            target_ppo_dir = os.path.join(base_path, ppo_target)
            os.makedirs(target_ppo_dir, exist_ok=True)
            self.log(
                f"Attach to PPO: PPO# '{ppo_target}' not found in '{base_path}' — "
                f"created folder '{target_ppo_dir}'."
            )
            return [target_ppo_dir]

        if not subfolders and not _base_isdir():
            raise FileNotFoundError(f"PPO target folder does not exist: {base_path}")
        if not subfolders:
            raise FileNotFoundError(f"PPO target folder has no subfolders: {base_path}")
        return subfolders

    @staticmethod
    def _is_auto_ppo_attach_enabled() -> bool:
        """Return True unless Developer Mode is active (which disables auto-attach to PPO)."""
        return not is_developer_mode()

    def on_app_mode_changed(self, mode: str) -> None:
        """Log operating mode changes triggered from the PTS Hub Settings dialog."""
        if str(mode or "").strip().lower() == "developer":
            self.log("Settings: Switched to Developer Mode — automatic attachment to PPO is disabled.")
        else:
            self.log("Settings: Switched to Production Mode — automatic attachment to PPO is enabled.")

    def _start_background_excel_generation(self, result: Dict[str, object], auto_attach: bool = True):
        """Build the Excel workbook in advance in a background thread as soon as analysis finishes.

        When ``auto_attach`` is True (and Production Mode is active), the finished workbook and
        result image are automatically attached to the PPO network folder (with retries, main
        progress bar, and Done X/N | MB/s | ETA telemetry) while keeping ``is_running`` active
        until auto-attachment completes. In Developer Mode, auto-attachment to PPO is skipped.
        """
        import tempfile
        dev_mode_blocked = bool(auto_attach) and not self._is_auto_ppo_attach_enabled()
        auto_attach = bool(auto_attach) and self._is_auto_ppo_attach_enabled()
        reuse_cached_excel = False
        ppo_num = self._get_effective_ppo_num(result)
        result["ppo_num"] = ppo_num
        with self._excel_lock:
            if self._excel_state == "IN_PROGRESS" or (auto_attach and self._ppo_attach_state == "IN_PROGRESS"):
                return

            cached_path = result.get("_cached_excel_path")
            if result.get("_cached_excel_ready") and cached_path and os.path.isfile(str(cached_path)):
                attached_ppo = str(result.get("_ppo_attached_ppo_num") or "").strip().upper()
                if not auto_attach or (result.get("_ppo_attached_paths") and attached_ppo == ppo_num):
                    self._excel_state = "READY"
                    self._excel_temp_path = str(cached_path)
                    self._excel_temp_filename = str(result.get("_cached_excel_name", os.path.basename(str(cached_path))))
                    if dev_mode_blocked:
                        if self._ppo_attach_state == "IN_PROGRESS":
                            self._ppo_attach_state = "IDLE"
                        if not result.get("_dev_mode_skip_logged"):
                            result["_dev_mode_skip_logged"] = True
                            self.log(
                                "Attach to PPO: Disabled (Developer Mode) - Excel generated locally; "
                                "skipping automatic PPO attachment."
                            )
                        self._set_status_async(
                            self._format_ppo_status(result, "Auto-attach to PPO: Disabled (Developer Mode)"),
                            gen_id=self._excel_generation_id,
                        )
                    return
                # Cached Excel is ready, but previous PPO auto-attach did not succeed -> re-attach using cached files
                reuse_cached_excel = True
                target_path = str(cached_path)
                default_name = str(result.get("_cached_excel_name", os.path.basename(target_path)))
                cached_img = result.get("_cached_image_path")
                if cached_img and os.path.isfile(str(cached_img)) and os.path.getsize(str(cached_img)) > 0:
                    target_img_path = str(cached_img)
                    image_default_name = str(result.get("_cached_image_name") or get_default_result_image_name(result))
                else:
                    lot_text = str(result.get("mir_lot_id") or result.get("lot_id") or "STDF_GUID_Result").strip().upper() or "STDF_GUID_Result"
                    safe_lot = re.sub(r"[^A-Za-z0-9_.-]+", "_", lot_text).strip("._") or "STDF_GUID_Result"
                    ts = datetime.now().strftime('%Y%m%d%H%M%S')
                    image_default_name = get_default_result_image_name(result, ts)
                    try:
                        tmp_img_fd, target_img_path = tempfile.mkstemp(prefix=f"{safe_lot}_FTDC_Result_", suffix=".png")
                        os.close(tmp_img_fd)
                        self._excel_temp_files.add(target_img_path)
                    except OSError:
                        target_img_path = ""
                    result["_cached_image_path"] = target_img_path
                    result["_cached_image_name"] = image_default_name
                self._excel_temp_path = target_path
                self._excel_temp_filename = default_name
                self._image_temp_path = target_img_path
                self._image_temp_filename = image_default_name
                self._excel_state = "READY"
                self._excel_error = None
                self._excel_generation_id += 1
                gen_id = self._excel_generation_id
                self._excel_cancel_event.clear()
                self._excel_ready_event.set()
                self._ppo_attach_state = "IN_PROGRESS"
            else:
                lot_text = str(result.get("mir_lot_id") or result.get("lot_id") or "STDF_GUID_Result").strip().upper() or "STDF_GUID_Result"
                safe_lot = re.sub(r"[^A-Za-z0-9_.-]+", "_", lot_text).strip("._") or "STDF_GUID_Result"
                ts = datetime.now().strftime('%Y%m%d%H%M%S')
                default_name = f"{safe_lot}_FTDC_{ts}.xlsx"
                image_default_name = get_default_result_image_name(result, ts)

                try:
                    tmp_fd, target_path = tempfile.mkstemp(prefix=f"{safe_lot}_FTDC_", suffix=".xlsx")
                    os.close(tmp_fd)
                    tmp_img_fd, target_img_path = tempfile.mkstemp(prefix=f"{safe_lot}_FTDC_Result_", suffix=".png")
                    os.close(tmp_img_fd)
                except OSError as err:
                    self._excel_state = "FAILED"
                    self._excel_error = str(err)
                    self._excel_ready_event.set()
                    if auto_attach:
                        self._ppo_attach_state = "FAILED"
                        self._set_status_async(
                            self._format_ppo_status(result, "Auto-attach to PPO: Failed (temp file error)"),
                        )
                        self._set_running(False)
                    elif dev_mode_blocked:
                        self._ppo_attach_state = "IDLE"
                    return

                self._excel_temp_files.add(target_path)
                self._excel_temp_files.add(target_img_path)
                self._excel_temp_path = target_path
                self._excel_temp_filename = default_name
                self._image_temp_path = target_img_path
                self._image_temp_filename = image_default_name
                self._excel_state = "IN_PROGRESS"
                self._excel_error = None
                self._excel_generation_id += 1
                gen_id = self._excel_generation_id
                self._excel_cancel_event.clear()
                self._excel_ready_event.clear()
                if auto_attach:
                    self._ppo_attach_state = "IN_PROGRESS"
                    result.pop("_dev_mode_skip_logged", None)
                elif dev_mode_blocked:
                    self._ppo_attach_state = "IDLE"
                    result["_dev_mode_skip_logged"] = True
                result["_cached_excel_ready"] = False
                result["_cached_excel_path"] = target_path
                result["_cached_excel_name"] = default_name
                result["_cached_image_path"] = target_img_path
                result["_cached_image_name"] = image_default_name

        if auto_attach:
            self._set_running(True)
            self.update_progress(0.0, "")
            if reuse_cached_excel:
                self.set_copy_stats(format_parallel_copy_stats_text(0, 1, 0, 0, 0.001))
                self._set_status_async(
                    self._format_ppo_status(result, "Auto-attach to PPO: On-going (attaching to PPO)..."),
                    gen_id=gen_id,
                )
            else:
                self.set_copy_stats("Generating Excel | ETA 0:00s")
                self._set_status_async(
                    self._format_ppo_status(result, "Auto-attach to PPO: On-going (generating Excel)..."),
                    gen_id=gen_id,
                )
            self._start_ppo_status_polling()
        elif dev_mode_blocked:
            self.log(
                "Attach to PPO: Disabled (Developer Mode) - Excel generated locally; "
                "skipping automatic PPO attachment."
            )
            self._set_status_async(
                self._format_ppo_status(result, "Auto-attach to PPO: Disabled (Developer Mode)"),
                gen_id=gen_id,
            )

        def _bg_excel_worker():
            import time as _time
            try:
                pre_subfolders: Optional[List[str]] = None
                prep_state: Dict[str, Any] = {"subfolders": None, "pre_copied": {}}
                stage_timeout = self._resolve_ppo_attach_timeout()
                stage_cancel = threading.Event()
                stage_deadline = _time.monotonic() + min(stage_timeout, 10.0)
                prep_join_timeout = min(1.5, max(0.05, stage_timeout * 0.5))

                def _scan_ppo_subfolders():
                    if not auto_attach:
                        return
                    mpc_raw = str(result.get("mpc") or "").strip().upper()
                    mask = "".join(mpc_raw.split())[:5]
                    lot_id_u = str(result.get("lot_id") or "").strip().upper()
                    if not mask or not lot_id_u:
                        return
                    ppo_lot_dir = os.path.join(get_ppo_base_path(), mask, lot_id_u)
                    try:
                        subs = self._resolve_ppo_target_subfolders(
                            ppo_lot_dir,
                            ppo_num=ppo_num,
                            create_if_missing=True,
                        )
                        if subs:
                            prep_state["subfolders"] = subs
                    except Exception as scan_exc:
                        logging.debug("Parallel PPO subfolder pre-scan skipped: %s", scan_exc)

                def _render_png_and_prestage():
                    if target_img_path:
                        try:
                            if not (os.path.isfile(target_img_path) and os.path.getsize(target_img_path) > 0):
                                render_result_image(result, target_img_path)
                        except Exception as img_exc:
                            logging.debug("Parallel result image render skipped/failed: %s", img_exc)
                    # Once PNG and PPO subfolders are ready, pre-copy PNG to PPO subfolders in parallel with Excel generation
                    # so SMB connections are already warm and only the .xlsx remains when Stage 2 starts.
                    if auto_attach and target_img_path and os.path.isfile(target_img_path):
                        try:
                            img_sz = max(os.path.getsize(target_img_path), 0)
                            if img_sz > 0:
                                scan_thread.join(timeout=prep_join_timeout)
                                subs = prep_state.get("subfolders") or []
                                for folder in subs:
                                    if (
                                        gen_id != self._excel_generation_id
                                        or self._excel_cancel_event.is_set()
                                        or stage_cancel.is_set()
                                        or _time.monotonic() >= stage_deadline
                                    ):
                                        break
                                    dest_img = os.path.join(folder, image_default_name)
                                    try:
                                        _fast_copy_file_with_progress(
                                            target_img_path,
                                            dest_img,
                                            cancel_event=stage_cancel,
                                            deadline=stage_deadline,
                                        )
                                        prep_state["pre_copied"][(folder, image_default_name)] = (dest_img, img_sz)
                                    except Exception as copy_exc:
                                        logging.debug("Pre-copy PNG skipped for '%s': %s", dest_img, copy_exc)
                        except Exception as pre_exc:
                            logging.debug("Parallel PNG PPO pre-copy skipped: %s", pre_exc)

                scan_thread = threading.Thread(target=_scan_ppo_subfolders, daemon=True)
                img_thread = threading.Thread(target=_render_png_and_prestage, daemon=True)
                scan_thread.start()
                img_thread.start()

                if not reuse_cached_excel:
                    excel_t0 = _time.monotonic()

                    def _excel_prep_progress(frac: float, _msg: str = ""):
                        if auto_attach and gen_id == self._excel_generation_id:
                            clamped = max(0.0, min(1.0, float(frac)))
                            elapsed = max(_time.monotonic() - excel_t0, 0.001)
                            eta_sec = (elapsed * (1.0 - clamped) / clamped) if 0.01 < clamped < 1.0 else 0.0
                            self.update_progress(clamped, "")
                            self.set_copy_stats(f"Generating Excel | ETA {_format_eta(eta_sec)}")

                    try:
                        write_result_excel(
                            result,
                            target_path,
                            progress_callback=_excel_prep_progress if auto_attach else None,
                        )
                        if auto_attach and gen_id == self._excel_generation_id:
                            self.update_progress(1.0, "")
                            self.set_copy_stats(f"Generating Excel | ETA {_format_eta(0.0)}")
                        with self._excel_lock:
                            if gen_id != self._excel_generation_id:
                                self._excel_ready_event.set()
                                return  # Cleared / superseded while generating
                            self._excel_state = "READY"
                            result["_cached_excel_ready"] = True
                            self._excel_ready_event.set()
                        logging.info("Advance Excel generation completed: %s", target_path)
                    except Exception as exc:
                        with self._excel_lock:
                            if gen_id != self._excel_generation_id:
                                self._excel_ready_event.set()
                                return
                            self._excel_state = "FAILED"
                            self._excel_error = str(exc)
                            self._excel_ready_event.set()
                        logging.warning("Advance Excel generation failed: %s", exc)
                        if auto_attach:
                            self._set_status_async(
                                self._format_ppo_status(result, "Auto-attach to PPO: Failed (Excel error)"),
                                gen_id=gen_id,
                            )
                            self._ppo_attach_state = "FAILED"
                            self.log(f"Attach to PPO: FAILED - Excel generation failed: {exc}")
                            self._queue_ppo_notice(
                                "error", "Attach to PPO Failed",
                                f"The result Excel could not be generated, so it was NOT attached to PPO:\n\n{exc}",
                            )
                        return

                # Immediately switch UI to Stage 2 before waiting on prep threads
                if auto_attach and gen_id == self._excel_generation_id:
                    self._set_status_async(
                        self._format_ppo_status(result, "Auto-attach to PPO: On-going (attaching to PPO)..."),
                        gen_id=gen_id,
                    )
                    early_subs = prep_state.get("subfolders")
                    early_tasks = (len(early_subs) * (2 if target_img_path else 1)) if early_subs else (2 if target_img_path else 1)
                    self.update_progress(0.0, "")
                    self.set_copy_stats(format_parallel_copy_stats_text(0, max(early_tasks, 1), 0, 0, 0.001))

                scan_thread.join(timeout=prep_join_timeout)
                img_thread.join(timeout=prep_join_timeout)
                if img_thread.is_alive():
                    stage_cancel.set()
                pre_subfolders = prep_state.get("subfolders")
                pre_copied_map = dict(prep_state.get("pre_copied") or {})

                # Ensure the result window image is ready
                try:
                    if target_img_path and not (os.path.isfile(target_img_path) and os.path.getsize(target_img_path) > 0):
                        save_result_window_image(result, target_img_path, window=getattr(self, "_result_window", None))
                except Exception as exc:
                    logging.warning("Advance result image generation failed: %s", exc)

                if auto_attach:
                    extra_atts = []
                    if target_img_path and os.path.isfile(target_img_path) and os.path.getsize(target_img_path) > 0:
                        extra_atts.append((target_img_path, image_default_name))
                    self._auto_attach_excel_to_ppo(
                        result,
                        target_path,
                        default_name,
                        gen_id,
                        extra_attachments=extra_atts,
                        pre_discovered_subfolders=pre_subfolders,
                        pre_copied_tasks=pre_copied_map,
                    )
            finally:
                if auto_attach and gen_id == self._excel_generation_id:
                    self._set_running(False)

        th = threading.Thread(target=_bg_excel_worker, daemon=True)
        self._excel_bg_thread = th
        th.start()

    # ── Local download path + automatic PPO attachment ──────────────────
    PPO_ATTACH_MAX_ATTEMPTS = 3
    PPO_ATTACH_RETRY_DELAYS = (3.0, 8.0)  # seconds to wait before attempt 2, 3
    PPO_ATTACH_TIMEOUT_SEC: Optional[float] = None  # None -> use settings.json ("ppo_attach_timeout_sec", default 60s)

    def _resolve_ppo_attach_timeout(self) -> float:
        raw = self.PPO_ATTACH_TIMEOUT_SEC
        if raw is not None:
            try:
                val = float(raw)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                pass
        try:
            return max(0.05, float(get_ppo_attach_timeout_sec()))
        except Exception:
            return 60.0

    @staticmethod
    def _local_excel_dest(result: Dict[str, object], filename: str) -> str:
        """Return C:\\FTDC\\{MIR LOT_ID}\\{filename} (periods preserved, folder created)."""
        mir_lot_text = str(result.get("mir_lot_id") or result.get("lot_id") or "STDF_GUID_Result").strip().upper() or "STDF_GUID_Result"
        safe_lot_folder = re.sub(r'[<>:"/\\|?*]+', "_", mir_lot_text).strip(" .") or "STDF_GUID_Result"
        dest_dir = os.path.join(get_local_dest_base(), safe_lot_folder)
        os.makedirs(dest_dir, exist_ok=True)
        return os.path.join(dest_dir, filename)

    @staticmethod
    def _format_ppo_status(result: Optional[Dict[str, object]], ppo_status: str) -> str:
        """Build a clear Status bar message preserving the analysis verdict when available."""
        if isinstance(result, dict) and result.get("status"):
            tot_text = result.get("total_good_status") or "SKIPPED"
            return f"Analysis completed: FP {result['status']} | Total Good {tot_text} | {ppo_status}"
        return ppo_status

    def _flush_pending_ppo_status(self):
        pending = self._pending_ppo_status
        if pending is None:
            return
        gen_id, text = pending
        self._pending_ppo_status = None
        try:
            if gen_id is not None and gen_id != self._excel_generation_id:
                return
            if self.root.winfo_exists():
                self.status_var.set(text)
        except Exception as exc:
            logging.warning("Suppressed exception in FTDC Checker status update: %s", exc)

    def _poll_ppo_status(self):
        self._ppo_status_poll_id = None
        self._flush_pending_ppo_status()
        self._flush_pending_progress()
        self._flush_pending_copy_stats()
        self._flush_pending_logs()
        self._flush_pending_running_state()
        if (
            self._ppo_attach_state == "IN_PROGRESS"
            or self._excel_state == "IN_PROGRESS"
            or self._pending_ppo_status is not None
            or self._pending_progress is not None
            or self._pending_copy_stats is not None
            or self._pending_running_state is not None
        ):
            try:
                if self.root.winfo_exists():
                    self._ppo_status_poll_id = self.root.after(40, self._poll_ppo_status)
            except Exception as exc:
                logging.debug("Suppressed PPO status poll reschedule: %s", exc)

    def _start_ppo_status_polling(self):
        if self._ppo_status_poll_id is not None:
            return
        if threading.current_thread() is not threading.main_thread():
            try:
                self.root.after(0, self._start_ppo_status_polling)
            except Exception as exc:
                logging.debug("Suppressed off-thread PPO status poll start: %s", exc)
            return
        try:
            if self.root.winfo_exists():
                self._ppo_status_poll_id = self.root.after(40, self._poll_ppo_status)
        except Exception as exc:
            logging.debug("Suppressed PPO status poll start: %s", exc)

    def _set_status_async(self, text: str, gen_id: Optional[int] = None):
        """Thread-safe status bar update (optionally guarded by _excel_generation_id)."""
        self._pending_ppo_status = (gen_id, text)
        if threading.current_thread() is threading.main_thread():
            try:
                self.root.after(0, self._flush_pending_ppo_status)
            except Exception as exc:
                logging.warning("Failed to schedule status update '%s': %s", text, exc)
        elif self._ppo_status_poll_id is None:
            try:
                self.root.after(0, self._flush_pending_ppo_status)
            except RuntimeError:
                # Mainloop may be inactive during shutdown or headless tests; main-thread polling handles it.
                pass
            except Exception as exc:
                logging.warning("Failed to schedule status update '%s': %s", text, exc)

    def _queue_ppo_notice(self, kind: str, title: str, message: str, open_dir: Optional[str] = None):
        """Queue a PPO popup (thread-safe). Deferred while the user is on another hub tab."""
        self._pending_ppo_notices.append((kind, title, message, open_dir))
        try:
            self.root.after(0, self._flush_ppo_notices)
        except Exception as exc:
            logging.warning("Failed to schedule PPO notice: %s", exc)

    def _flush_ppo_notices(self):
        """Show queued PPO popups on the UI thread (only when the FTDC Checker tab is active)."""
        if self.hub is not None and getattr(self.hub, "_active_tab", None) != self.app_name:
            return  # Shown later by on_tab_activated()
        while self._pending_ppo_notices:
            kind, title, message, open_dir = self._pending_ppo_notices.pop(0)
            self._show_ppo_notice(kind, title, message, open_dir)

    def _show_ppo_notice(self, kind: str, title: str, message: str, open_dir: Optional[str] = None):
        parent = self._result_window
        try:
            if parent is None or not parent.winfo_exists():
                parent = self.root
        except Exception:
            parent = self.root
        if kind == "info":
            messagebox.showinfo(title, message, parent=parent)
        elif kind == "warning":
            messagebox.showwarning(title, message, parent=parent)
        else:
            if open_dir and os.path.isdir(open_dir):
                if messagebox.askyesno(title, message + "\n\nOpen the folder containing the Excel file now?",
                                       icon="error", parent=parent):
                    try:
                        os.startfile(open_dir)
                    except OSError as exc:
                        logging.warning("Failed to open folder '%s': %s", open_dir, exc)
            else:
                messagebox.showerror(title, message, parent=parent)

    def _auto_attach_excel_to_ppo(self, result: Dict[str, object], src_excel_path: str,
                                  default_name: str, gen_id: int,
                                  extra_attachments: Optional[List[Tuple[str, str]]] = None,
                                  pre_discovered_subfolders: Optional[List[str]] = None,
                                  pre_copied_tasks: Optional[Dict[Tuple[str, str], Tuple[str, int]]] = None):
        """Copy the generated Excel and Result window image to every PPO lot subfolder,
        retrying failed files automatically while updating the main progress bar and
        ``Done X/N | copied/total | MB/s | ETA`` telemetry.

        Runs on a background thread. Each copy goes to ``{name}.part`` and is atomically renamed, so a
        failed/interrupted transfer never leaves half-written files in PPO.
        """
        import shutil
        import time as _time

        if not self._is_auto_ppo_attach_enabled():
            self._ppo_attach_state = "IDLE"
            self.log("Attach to PPO: Skipped (Developer Mode is active - auto-attach to PPO is disabled).")
            self._set_status_async(
                self._format_ppo_status(result, "Auto-attach to PPO: Disabled (Developer Mode)"),
                gen_id=gen_id,
            )
            return

        def _cancelled() -> bool:
            return gen_id != self._excel_generation_id or self._excel_cancel_event.is_set()

        timeout_sec = self._resolve_ppo_attach_timeout()
        attach_deadline = _time.monotonic() + timeout_sec
        attach_cancel_event = threading.Event()
        timed_out = False

        def _remaining() -> float:
            return attach_deadline - _time.monotonic()

        def _is_timed_out() -> bool:
            return _time.monotonic() >= attach_deadline

        all_attachments: List[Tuple[str, str]] = [(src_excel_path, default_name)]
        if extra_attachments:
            for extra_path, extra_name in extra_attachments:
                if os.path.isfile(extra_path) and os.path.getsize(extra_path) > 0:
                    all_attachments.append((extra_path, extra_name))

        all_names_desc = " & ".join(f"'{fn}'" for _, fn in all_attachments)
        mpc_raw = str(result.get("mpc") or "").strip().upper()
        mask = "".join(mpc_raw.split())[:5]
        lot_id_upper = str(result.get("lot_id") or "").strip().upper()
        if not mask or not lot_id_upper:
            self._set_status_async(
                self._format_ppo_status(result, "Auto-attach to PPO: Failed (missing MPC/Lot ID)"),
                gen_id=gen_id,
            )
            self._ppo_attach_state = "FAILED"
            self.log("Attach to PPO: FAILED - MPC or Lot ID is missing.")
            self._queue_ppo_notice("error", "Attach to PPO Failed",
                                   "Cannot determine PPO path: MPC or Lot ID is missing.")
            return

        base_path = os.path.join(get_ppo_base_path(), mask, lot_id_upper)
        ppo_num = self._get_effective_ppo_num(result)
        self._ppo_attach_state = "IN_PROGRESS"
        self._start_ppo_status_polling()
        ppo_desc = f" (PPO#: '{ppo_num}')" if ppo_num else ""
        self.log(
            f"Attach to PPO: Files ready - automatically attaching {all_names_desc} to "
            f"'{base_path}'{ppo_desc} (timeout {timeout_sec:g}s)."
        )
        self._set_status_async(
            self._format_ppo_status(result, "Auto-attach to PPO: On-going (attaching to PPO)..."),
            gen_id=gen_id,
        )

        # Pre-read attachment sizes once
        att_sizes: Dict[str, int] = {}
        for s_path, _fname in all_attachments:
            try:
                if os.path.isfile(s_path):
                    att_sizes[s_path] = max(os.path.getsize(s_path), 0)
            except OSError:
                pass

        progress_lock = threading.Lock()
        copy_t0 = _time.monotonic()
        total_tasks = 0
        total_bytes = 0
        done_tasks = 0
        bytes_by_task: Dict[Tuple[str, str], int] = {}

        # Immediately transition progress bar to 0% and Done 0/N for Stage 2
        est_init_tasks = (len(pre_discovered_subfolders) * len(all_attachments)) if pre_discovered_subfolders else len(all_attachments)
        est_init_bytes = sum(att_sizes.values()) * (len(pre_discovered_subfolders) if pre_discovered_subfolders else 1)
        self.update_progress(0.0, "")
        self.set_copy_stats(
            format_parallel_copy_stats_text(0, max(est_init_tasks, 1), 0, est_init_bytes, 0.001)
        )

        def _emit_ppo_telemetry():
            with progress_lock:
                agg_copied = sum(bytes_by_task.values())
                done = done_tasks
                frac = (
                    (agg_copied / total_bytes)
                    if total_bytes > 0
                    else (done / max(total_tasks, 1))
                )
                frac = min(max(0.0, frac), 1.0 if done >= total_tasks else 0.99)
                elapsed = max(_time.monotonic() - copy_t0, 0.001)
                stats_txt = format_parallel_copy_stats_text(
                    done, total_tasks, agg_copied, total_bytes, elapsed
                )
            self.update_progress(frac, "")
            self.set_copy_stats(stats_txt)

        def _copy_single_task(task: Tuple[str, str, str, int]) -> str:
            nonlocal done_tasks
            folder, s_path, fname, fsize = task
            if _is_timed_out() or attach_cancel_event.is_set():
                raise TimeoutError(f"PPO attachment timed out after {timeout_sec:g}s.")
            os.makedirs(folder, exist_ok=True)
            dest = os.path.join(folder, fname)
            part = dest + ".part"
            task_key = (folder, fname)

            def _on_bytes(cur_bytes: int, tot_bytes: int):
                with progress_lock:
                    limit = max(fsize, tot_bytes, cur_bytes)
                    bytes_by_task[task_key] = min(max(0, cur_bytes), limit)
                _emit_ppo_telemetry()

            try:
                _fast_copy_file_with_progress(
                    s_path,
                    dest,
                    progress_callback=None,
                    stats_callback=None,
                    byte_progress_callback=_on_bytes,
                    cancel_event=attach_cancel_event,
                    deadline=attach_deadline,
                )
            except TimeoutError:
                raise
            except Exception:
                try:
                    if _is_timed_out() or attach_cancel_event.is_set():
                        raise TimeoutError(f"PPO attachment timed out after {timeout_sec:g}s.")
                    shutil.copyfile(s_path, part)
                    os.replace(part, dest)
                except Exception:
                    try:
                        if os.path.exists(part):
                            os.remove(part)
                    except OSError:
                        pass
                    raise

            with progress_lock:
                bytes_by_task[task_key] = fsize
                done_tasks += 1
            _emit_ppo_telemetry()
            return dest

        saved_paths_by_task: Dict[Tuple[str, str], str] = {}
        all_subfolders: Optional[List[str]] = None
        pending_tasks: Optional[List[Tuple[str, str, str, int]]] = None
        errors: Dict[str, str] = {}
        max_attempts = max(1, int(self.PPO_ATTACH_MAX_ATTEMPTS))

        for attempt in range(1, max_attempts + 1):
            if _cancelled():
                attach_cancel_event.set()
                self.log("Attach to PPO: Cancelled (results cleared).")
                return
            if _is_timed_out():
                timed_out = True
                attach_cancel_event.set()
                if not errors:
                    errors[base_path] = f"Timed out after {timeout_sec:g}s"
                self.log(f"Attach to PPO: Timed out after {timeout_sec:g}s.")
                break
            if attempt > 1:
                delays = self.PPO_ATTACH_RETRY_DELAYS
                delay = float(delays[min(attempt - 2, len(delays) - 1)]) if delays else 0.0
                rem_delay = min(delay, max(0.0, _remaining()))
                self.log(f"Attach to PPO: Retrying in {rem_delay:g}s (attempt {attempt}/{max_attempts})...")
                self._set_status_async(
                    self._format_ppo_status(result, f"Auto-attach to PPO: On-going (retrying {attempt}/{max_attempts})..."),
                    gen_id=gen_id,
                )
                if rem_delay > 0 and self._excel_cancel_event.wait(timeout=rem_delay):
                    attach_cancel_event.set()
                    self.log("Attach to PPO: Cancelled (results cleared).")
                    return
                if _cancelled():
                    attach_cancel_event.set()
                    self.log("Attach to PPO: Cancelled (results cleared).")
                    return
                if _is_timed_out():
                    timed_out = True
                    attach_cancel_event.set()
                    if not errors:
                        errors[base_path] = f"Timed out after {timeout_sec:g}s"
                    self.log(f"Attach to PPO: Timed out after {timeout_sec:g}s.")
                    break
            try:
                for s_path, fname in all_attachments:
                    if not os.path.isfile(s_path):
                        raise FileNotFoundError(f"Generated attachment file no longer exists: {s_path}")
                    if s_path not in att_sizes:
                        att_sizes[s_path] = max(os.path.getsize(s_path), 0)

                if all_subfolders is None:
                    scan_holder: Dict[str, Any] = {}

                    def _scan_dir_worker():
                        try:
                            scan_holder["subfolders"] = self._resolve_ppo_target_subfolders(
                                base_path,
                                ppo_num=ppo_num,
                                pre_discovered=pre_discovered_subfolders,
                                create_if_missing=True,
                            )
                        except BaseException as scan_exc:
                            scan_holder["error"] = scan_exc

                    scan_worker = threading.Thread(target=_scan_dir_worker, daemon=True, name="PPODirScan")
                    scan_worker.start()
                    scan_worker.join(timeout=max(0.01, _remaining()))
                    if scan_worker.is_alive():
                        timed_out = True
                        attach_cancel_event.set()
                        raise TimeoutError(
                            f"PPO attachment timed out after {timeout_sec:g}s while accessing '{base_path}'."
                        )
                    if "error" in scan_holder:
                        raise scan_holder["error"]
                    all_subfolders = scan_holder["subfolders"]
                    all_task_specs = [
                        (f, s_path, fname, att_sizes.get(s_path, 0))
                        for f in all_subfolders
                        for s_path, fname in all_attachments
                    ]
                    total_tasks = len(all_task_specs)
                    total_bytes = sum(t[3] for t in all_task_specs)
                    copy_t0 = _time.monotonic()
                    pending_tasks = []
                    for t in all_task_specs:
                        f, _s_path, fname, fsize = t
                        t_key = (f, fname)
                        if pre_copied_tasks and t_key in pre_copied_tasks:
                            pre_dest, pre_sz = pre_copied_tasks[t_key]
                            if os.path.isfile(pre_dest):
                                saved_paths_by_task[t_key] = pre_dest
                                bytes_by_task[t_key] = max(fsize, pre_sz)
                                done_tasks += 1
                                continue
                        pending_tasks.append(t)
                    init_copied = sum(bytes_by_task.values())
                    init_frac = (init_copied / total_bytes) if total_bytes > 0 else (done_tasks / max(total_tasks, 1))
                    self.update_progress(min(max(0.0, init_frac), 0.99 if pending_tasks else 1.0), "")
                    self.set_copy_stats(
                        format_parallel_copy_stats_text(done_tasks, total_tasks, init_copied, total_bytes, 0.001)
                    )

                errors = {}
                failed_tasks: List[Tuple[str, str, str, int]] = []
                if pending_tasks:
                    if _is_timed_out():
                        timed_out = True
                        attach_cancel_event.set()
                        for folder, _s_path, fname, _fsize in pending_tasks:
                            errors[folder] = f"Timed out after {timeout_sec:g}s while copying '{fname}'"
                            failed_tasks.append((folder, _s_path, fname, _fsize))
                    else:
                        pool = ThreadPoolExecutor(max_workers=max(1, min(len(pending_tasks), 8)))
                        try:
                            futures = {pool.submit(_copy_single_task, t): t for t in pending_tasks}
                            done_futs, not_done_futs = _futures_wait(
                                futures.keys(),
                                timeout=max(0.01, _remaining()),
                            )
                            for fut in done_futs:
                                task = futures[fut]
                                folder, _s_path, fname, _fsize = task
                                try:
                                    dest_path = fut.result()
                                    saved_paths_by_task[(folder, fname)] = dest_path
                                except TimeoutError as exc:
                                    timed_out = True
                                    errors[folder] = str(exc)
                                    failed_tasks.append(task)
                                except Exception as exc:
                                    errors[folder] = str(exc)
                                    failed_tasks.append(task)
                            if not_done_futs:
                                timed_out = True
                                attach_cancel_event.set()
                                for fut in not_done_futs:
                                    fut.cancel()
                                    task = futures[fut]
                                    folder, _s_path, fname, _fsize = task
                                    errors[folder] = (
                                        f"Timed out after {timeout_sec:g}s while copying '{fname}'"
                                    )
                                    failed_tasks.append(task)
                        finally:
                            pool.shutdown(wait=False, cancel_futures=True)
                pending_tasks = failed_tasks
                if not pending_tasks:
                    break
                if timed_out:
                    attach_cancel_event.set()
                    self.log(
                        f"Attach to PPO: Timed out after {timeout_sec:g}s - "
                        + "; ".join(f"{k}: {v}" for k, v in errors.items())
                    )
                    break
                self.log(
                    f"Attach to PPO: Attempt {attempt}/{max_attempts} failed for {len(errors)} subfolder(s): "
                    + "; ".join(f"{k}: {v}" for k, v in errors.items())
                )
            except TimeoutError as exc:
                timed_out = True
                attach_cancel_event.set()
                errors = {base_path: str(exc)}
                self.log(f"Attach to PPO: Timed out after {timeout_sec:g}s - {exc}")
                break
            except Exception as exc:
                errors = {base_path: str(exc)}
                self.log(f"Attach to PPO: Attempt {attempt}/{max_attempts} failed - {exc}")
                if _is_timed_out():
                    timed_out = True
                    attach_cancel_event.set()
                    break

        if _cancelled():
            attach_cancel_event.set()
            return

        saved_paths: List[str] = []
        if all_subfolders:
            for f in all_subfolders:
                if f not in errors:
                    for _s_path, fname in all_attachments:
                        dp = saved_paths_by_task.get((f, fname))
                        if dp:
                            saved_paths.append(dp)

        num_files = len(all_attachments)
        num_folders = len(saved_paths) // max(num_files, 1)

        if saved_paths and not errors:
            elapsed = max(_time.monotonic() - copy_t0, 0.001)
            self.update_progress(1.0, "")
            self.set_copy_stats(
                format_parallel_copy_stats_text(total_tasks, total_tasks, total_bytes, total_bytes, elapsed)
            )
            result["_ppo_attached_paths"] = list(saved_paths)
            result["_ppo_attached_ppo_num"] = ppo_num
            self._set_status_async(
                self._format_ppo_status(result, f"Auto-attach to PPO: Done ({num_folders} subfolder(s))"),
                gen_id=gen_id,
            )
            self._ppo_attach_state = "SUCCESS"
            self.log(
                f"Attach to PPO: SUCCESS - Attached {all_names_desc} to "
                f"{num_folders} PPO subfolder(s): {', '.join(saved_paths)}"
            )
            self._queue_ppo_notice(
                "info", "Attach to PPO",
                f"Result files ({', '.join(fname for _, fname in all_attachments)}) were automatically attached to {num_folders} PPO subfolder(s):\n\n"
                + "\n".join(saved_paths),
            )
            return

        # Final failure or timeout (full or partial): keep an atomic local copy of all attachments so the user can transfer them manually.
        local_saved: List[str] = []
        for s_path, fname in all_attachments:
            try:
                lp = self._local_excel_dest(result, fname)
                if os.path.abspath(lp) != os.path.abspath(s_path):
                    lp_part = lp + ".part"
                    try:
                        shutil.copy2(s_path, lp_part)
                        os.replace(lp_part, lp)
                    except Exception:
                        if os.path.exists(lp_part):
                            try:
                                os.remove(lp_part)
                            except OSError:
                                pass
                        raise
                local_saved.append(lp)
            except Exception as exc:
                logging.warning("Failed to save local copy for manual PPO transfer (%s): %s", fname, exc)

        local_path = local_saved[0] if local_saved else None
        errors_text = "\n".join(f"{k}: {v}" for k, v in errors.items())
        local_text = (
            f"\n\nCopies were saved locally for manual transfer:\n" + "\n".join(local_saved) if local_saved
            else "\n\nUse 'Download & Open Excel' to save a local copy for manual transfer."
        )
        manual_target = os.path.join(base_path, ppo_num) if ppo_num else base_path
        manual_text = f"\n\nPlease copy the files manually to:\n{manual_target}"
        if saved_paths:
            if timed_out:
                status_msg = f"Auto-attach to PPO: Partial / Timed out ({num_folders} attached, {len(errors)} failed)"
                title = "Attach to PPO - Partially Timed Out"
                fail_header = f"Timed out after {timeout_sec:g}s in {len(errors)} subfolder(s)"
            else:
                status_msg = f"Auto-attach to PPO: Partial ({num_folders} attached, {len(errors)} failed)"
                title = "Attach to PPO - Partially Failed"
                fail_header = f"Failed after {max_attempts} attempt(s) in {len(errors)} subfolder(s)"
            self._set_status_async(self._format_ppo_status(result, status_msg), gen_id=gen_id)
            self._ppo_attach_state = "PARTIAL"
            self.log(
                f"Attach to PPO: PARTIAL - Attached to {num_folders} subfolder(s) "
                f"({', '.join(saved_paths)}), {fail_header}: "
                + "; ".join(f"{k}: {v}" for k, v in errors.items())
                + (f". Local copies: {', '.join(local_saved)}" if local_saved else "")
            )
            message = (
                f"Result files were attached to {num_folders} subfolder(s):\n" + "\n".join(saved_paths)
                + f"\n\n{fail_header}:\n{errors_text}"
                + local_text + manual_text
            )
        else:
            if timed_out:
                status_msg = "Auto-attach to PPO: Timed out (local copy saved)" if local_saved else "Auto-attach to PPO: Timed out"
                title = "Attach to PPO Timed Out"
                fail_intro = f"Automatic attachment of the result files to PPO timed out after {timeout_sec:g}s:"
                log_prefix = f"Attach to PPO: FAILED - Timed out after {timeout_sec:g}s"
            else:
                status_msg = "Auto-attach to PPO: Failed (local copy saved)" if local_saved else "Auto-attach to PPO: Failed"
                title = "Attach to PPO Failed"
                fail_intro = f"Automatic attachment of the result files to PPO failed after {max_attempts} attempt(s):"
                log_prefix = f"Attach to PPO: FAILED after {max_attempts} attempt(s)"
            self._set_status_async(self._format_ppo_status(result, status_msg), gen_id=gen_id)
            self._ppo_attach_state = "FAILED"
            self.log(
                f"{log_prefix} - "
                + "; ".join(f"{k}: {v}" for k, v in errors.items())
                + (f". Local copies saved for manual transfer: {', '.join(local_saved)}" if local_saved else "")
            )
            message = f"{fail_intro}\n\n{errors_text}" + local_text + manual_text
        self._queue_ppo_notice("error", title, message,
                               open_dir=os.path.dirname(local_path) if local_path else None)

    def _deliver_analysis_result(self, result: Dict[str, object]):
        """Deliver the analysis result popup, restraining it to the FTDC Checker tab,
        and trigger background Excel generation + Auto-Attach to PPO."""
        if self.hub is not None and getattr(self.hub, "_active_tab", None) != self.app_name:
            # User is on a different tab — hold the popup until they switch back
            self._pending_result_popup = result
        else:
            self._show_result_popup(result)
        self._start_background_excel_generation(result)

    def _show_result_popup(self, result: Dict[str, object]):
        from tkinter import messagebox

        tk = self.tk_mod
        ttk = self.ttk

        summary_sections, detail_rows, fp_status, total_status, _, ftdc_criteria = build_summary_and_details(result)
        fmt = _fmt

        excel_busy = {"running": False}

        def _get_or_generate_cached_excel(progress_cb=None) -> Tuple[str, str]:
            """Get pre-generated Excel workbook or generate it if not already ready."""
            import tempfile
            with self._excel_lock:
                cached_path = self._excel_temp_path or result.get("_cached_excel_path")
                cached_name = self._excel_temp_filename or result.get("_cached_excel_name")
                if cached_path and cached_name and os.path.isfile(str(cached_path)):
                    if progress_cb:
                        progress_cb(1.0, "Using cached Excel file")
                    return str(cached_path), str(cached_name)

                lot_text = str(result.get("mir_lot_id") or result.get("lot_id") or "STDF_GUID_Result").strip().upper() or "STDF_GUID_Result"
                safe_lot = re.sub(r"[^A-Za-z0-9_.-]+", "_", lot_text).strip("_.") or "STDF_GUID_Result"
                default_name = f"{safe_lot}_FTDC_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"

                tmp_fd, target_path = tempfile.mkstemp(prefix=f"{safe_lot}_FTDC_", suffix=".xlsx")
                os.close(tmp_fd)

                self._excel_temp_files.add(target_path)
                self._excel_temp_path = target_path
                self._excel_temp_filename = default_name
                self._excel_state = "IN_PROGRESS"

            write_result_excel(result, target_path, progress_callback=progress_cb)
            with self._excel_lock:
                self._excel_state = "READY"
                result["_cached_excel_path"] = target_path
                result["_cached_excel_name"] = default_name
            return target_path, default_name

        def _set_excel_buttons_state(disabled: bool):
            st = "disabled" if disabled else "normal"
            for btn in (open_excel_button,):
                if btn is not None:
                    try:
                        if window.winfo_exists():
                            btn.configure(state=st)
                    except Exception as exc:
                        logging.warning("Failed to update Excel button state: %s", exc)

        def open_excel():
            if excel_busy["running"]:
                return
            with self._excel_lock:
                st = self._excel_state
                err = self._excel_error
            if st == "IN_PROGRESS":
                messagebox.showwarning(
                    "Excel Generation In-Progress",
                    "Excel file generation is still on-going.\n\n"
                    "Please wait a moment for the Excel file to finish generating and try again.",
                    parent=window,
                )
                return
            if st == "FAILED":
                messagebox.showerror(
                    "Excel Generation Failed",
                    f"Excel generation failed:\n\n{err}",
                    parent=window,
                )
                return
            try:
                import xlsxwriter  # noqa: F401
            except ImportError:
                self.log("Download & Open Excel: FAILED — Missing 'xlsxwriter' dependency.")
                messagebox.showerror(
                    "Missing Dependency",
                    "The 'xlsxwriter' package is required for Excel export.\n\n"
                    "Install it with:  pip install xlsxwriter",
                    parent=window,
                )
                return

            excel_busy["running"] = True
            _set_excel_buttons_state(True)
            ppo_ongoing_at_start = (
                self._ppo_attach_state == "IN_PROGRESS" or self._excel_state == "IN_PROGRESS"
            )
            if not ppo_ongoing_at_start:
                self._set_running(True)
                self.update_progress(0.02, "Preparing Excel file to open…")
            self.log("Download & Open Excel: Preparing Excel workbook…")

            def _open_worker():
                import shutil
                try:
                    # If background Excel generation is still in flight, wait for it to finish
                    if self._excel_state == "IN_PROGRESS":
                        self._excel_ready_event.wait(timeout=120.0)
                    with self._excel_lock:
                        cur_st = self._excel_state
                        cur_err = self._excel_error
                    if cur_st == "FAILED":
                        raise RuntimeError(cur_err or "Excel generation failed")

                    ppo_still_ongoing = (
                        self._ppo_attach_state == "IN_PROGRESS" or self._excel_state == "IN_PROGRESS"
                    )
                    src_path, default_name = _get_or_generate_cached_excel(
                        progress_cb=None if ppo_still_ongoing else self.update_progress
                    )
                    # Download path: C:\FTDC\{MIR LOT_ID}\ (periods preserved)
                    dest_path = self._local_excel_dest(result, default_name)
                    if os.path.abspath(src_path) != os.path.abspath(dest_path):
                        dest_part = dest_path + ".open.part"
                        try:
                            shutil.copy2(src_path, dest_part)
                            os.replace(dest_part, dest_path)
                        except Exception:
                            if os.path.exists(dest_part):
                                try:
                                    os.remove(dest_part)
                                except OSError:
                                    pass
                            raise
                    final_open_path = dest_path
                    self.log(f"Download & Open Excel: Downloaded Excel to '{dest_path}'")

                    # Also save a copy of the result window image locally
                    cached_img = result.get("_cached_image_path")
                    cached_img_name = result.get("_cached_image_name")
                    if cached_img and cached_img_name and os.path.isfile(cached_img):
                        try:
                            dest_img_path = self._local_excel_dest(result, cached_img_name)
                            if os.path.abspath(cached_img) != os.path.abspath(dest_img_path):
                                img_part = dest_img_path + ".open.part"
                                shutil.copy2(cached_img, img_part)
                                os.replace(img_part, dest_img_path)
                        except Exception as exc:
                            logging.warning("Failed to save local copy of result image: %s", exc)

                    if not (self._ppo_attach_state == "IN_PROGRESS" or self._excel_state == "IN_PROGRESS"):
                        self.update_progress(1.0, "Opening Excel file…")
                    self.log(f"Download & Open Excel: Opening '{final_open_path}'")

                    def _finish_open():
                        excel_busy["running"] = False
                        _set_excel_buttons_state(False)
                        if self._ppo_attach_state == "IN_PROGRESS" or self._excel_state == "IN_PROGRESS":
                            # Keep is_running=True and preserve Auto PPO progress bar & status while auto-attach continues
                            pass
                        else:
                            self.update_progress(1.0, "")
                            self._set_running(False)
                            self.set_status("Excel file opened")
                        try:
                            os.startfile(final_open_path)
                        except OSError as exc:
                            self.log(f"Download & Open Excel: FAILED to launch Excel viewer for '{final_open_path}': {exc}")
                            messagebox.showerror("Download & Open Excel Failed", str(exc), parent=window)
                    self.root.after(0, _finish_open)
                except Exception as exc:
                    err_txt = str(exc)
                    self.log(f"Download & Open Excel: FAILED to generate Excel file — {err_txt}")
                    def _err_open(m=err_txt):
                        excel_busy["running"] = False
                        _set_excel_buttons_state(False)
                        if not (self._ppo_attach_state == "IN_PROGRESS" or self._excel_state == "IN_PROGRESS"):
                            self._set_running(False)
                            self.reset_progress("Download & Open Excel failed")
                        messagebox.showerror("Download & Open Excel Failed", m, parent=window)
                    self.root.after(0, _err_open)

            threading.Thread(target=_open_worker, daemon=True).start()

        truncated_list = result.get("truncated_files") or []
        if truncated_list:
            self.log(f"[WARNING] Analysis result contains {len(truncated_list)} truncated STDF file(s):")
            for tf in truncated_list:
                self.log(
                    f"  Truncated STDF in {tf.get('panel')}: {tf.get('filename')} "
                    f"(ended_at_eof={tf.get('ended_at_eof')}, last_record_was_mrr={tf.get('last_record_was_mrr')}, "
                    f"pir_without_prr={tf.get('pir_without_prr')}, PRR={tf.get('prr_count')}, PCR={tf.get('pcr_part_cnt')})"
                )

        window = tk.Toplevel(self.root)
        self._result_window = window
        window.configure(bg=MAIN_BG)
        window.title(f"FTDC Result - {result.get('lot_id', '')}")
        window.transient(self.root)
        window.minsize(860, 180)
        window.columnconfigure(0, weight=1)

        container = ttk.Frame(window, padding=(10, 6, 10, 6))
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)

        title_text = f"Automatic FTDC Checker {APP_VERSION}"
        title = tk.Label(container, text=title_text, font=("Segoe UI", 11, "bold"), anchor="w", bg=MAIN_BG, fg=TEXT_FG)
        title.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        curr_row = 1
        if truncated_list:
            warn_card = tk.Frame(container, bg="#FFF3CD", highlightthickness=1, highlightbackground="#FFEEBA", bd=0)
            warn_card.grid(row=curr_row, column=0, sticky="ew", pady=(0, 6))
            curr_row += 1
            w_lines = ["⚠️ WARNING: Truncated or corrupted STDF file(s) detected during analysis:"]
            for tf in truncated_list:
                reasons = []
                if not tf.get("ended_at_eof"):
                    reasons.append("Premature EOF")
                if not tf.get("last_record_was_mrr"):
                    reasons.append("Missing MRR record")
                if tf.get("pcr_part_cnt") is not None and tf.get("prr_count") != tf.get("pcr_part_cnt"):
                    reasons.append(f"PRR count ({tf.get('prr_count')}) != PCR ({tf.get('pcr_part_cnt')})")
                r_txt = ", ".join(reasons) if reasons else "Truncated file"
                w_lines.append(f"  • [{tf.get('panel')}] {tf.get('filename')}: {r_txt}")
            warn_label = tk.Label(
                warn_card, text="\n".join(w_lines), font=("Segoe UI", 9, "bold"),
                bg="#FFF3CD", fg="#856404", justify="left", anchor="w", padx=8, pady=6
            )
            warn_label.pack(fill="x")

        po_banner = format_macid_prefix_outlier_banner(result)
        po_label = None
        if po_banner:
            po_card = tk.Frame(
                container,
                bg=po_banner["bg"],
                highlightthickness=1,
                highlightbackground=po_banner["border"],
                bd=0,
            )
            po_card.grid(row=curr_row, column=0, sticky="ew", pady=(0, 6))
            curr_row += 1
            po_label = tk.Label(
                po_card,
                text="\n".join(po_banner["lines"]),
                font=("Segoe UI", 9, "bold"),
                bg=po_banner["bg"],
                fg=po_banner["fg"],
                justify="left",
                anchor="w",
                padx=8,
                pady=6,
                wraplength=820,
            )
            po_label.pack(fill="x")

        panels = ttk.Frame(container)
        panels.grid(row=curr_row, column=0, sticky="nsew")
        curr_row += 1
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)

        val_font = tkfont.Font(root=window, family="Segoe UI", size=9, weight="bold")
        for col, (section_title, rows) in enumerate(summary_sections):
            box = tk.Frame(panels, borderwidth=1, relief="solid", bg="#B7B7B7")
            box.grid(row=0, column=col, sticky="nsew", padx=(0, 5) if col == 0 else (5, 0))
            box.columnconfigure(0, weight=1)

            header = tk.Label(
                box, text=section_title, font=("Segoe UI", 10, "bold"), bg="#F0F0F0", fg="#000000",
                anchor="w", padx=8, pady=4
            )
            header.grid(row=0, column=0, sticky="ew")

            table = tk.Frame(box, bg="#B7B7B7")
            table.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
            table.columnconfigure(0, weight=1)
            table.columnconfigure(1, weight=0)
            max_val_px = 32

            for r, (metric, value) in enumerate(rows):
                value_text, value_fg = resolve_summary_metric_style(
                    metric, value, result, is_total=(col > 0), fmt_fn=fmt
                )
                txt_px = val_font.measure(value_text)
                if txt_px > max_val_px:
                    max_val_px = txt_px

                tip_text = None
                if metric == "Duplicate Good UID":
                    tip_text = (
                        TOOLTIP_FP_DUPLICATE_GOOD_UID
                        if col == 0
                        else TOOLTIP_TOTAL_DUPLICATE_GOOD_UID
                    )
                elif metric == "First Pass Actual Good":
                    tip_text = TOOLTIP_FP_ACTUAL_GOOD
                elif metric == "Actual Total Good":
                    tip_text = TOOLTIP_TOTAL_ACTUAL_GOOD
                elif metric.startswith("Duplicate MACID"):
                    tip_text = TOOLTIP_DUPLICATE_MACID
                elif metric.startswith("Unique Valid Good"):
                    tip_text = TOOLTIP_UNIQUE_VALID_GOOD

                if tip_text:
                    metric_cell = tk.Frame(
                        table, bg="white", borderwidth=1, relief="solid"
                    )
                    metric_cell.grid(row=r, column=0, sticky="ew")
                    metric_label = tk.Label(
                        metric_cell, text=f"{metric}:", font=("Segoe UI", 9), bg="white", fg="#000000",
                        anchor="w", padx=7, pady=4, bd=0
                    )
                    metric_label.pack(side="left")
                    InfoTooltipButton(
                        metric_cell, text=tip_text, bg="white"
                    ).pack(side="left", padx=(0, 6))
                else:
                    metric_label = tk.Label(
                        table, text=f"{metric}:", font=("Segoe UI", 9), bg="white", fg="#000000",
                        anchor="w", padx=7, pady=4, borderwidth=1, relief="solid"
                    )
                    metric_label.grid(row=r, column=0, sticky="ew")
                value_label = tk.Label(
                    table, text=value_text, font=("Segoe UI", 9, "bold"), fg=value_fg, bg="white",
                    anchor="e", padx=7, pady=4, borderwidth=1, relief="solid"
                )
                value_label.grid(row=r, column=1, sticky="ew")

            # Size the right value column from measured font width without blocking on update_idletasks()
            table.columnconfigure(1, weight=0, minsize=max(56, max_val_px + 24))

        # Remarks row across FP and Total Good
        remarks_text = str(result.get("remarks") or compute_remarks(result)).strip()
        if remarks_text == "No abnormalities or discrepancies were observed.":
            remarks_fg = "#008000"
        elif remarks_text.startswith("PASS with Warning:"):
            remarks_fg = "#9A5B00"
        else:
            remarks_fg = "#C00000"

        remarks_card = tk.Frame(container, bg=BORDER_COLOR, highlightthickness=1, highlightbackground=BORDER_COLOR, bd=0)
        remarks_card.grid(row=curr_row, column=0, sticky="ew", pady=(6, 0))
        curr_row += 1
        remarks_card.columnconfigure(1, weight=1)

        remarks_label = tk.Label(
            remarks_card, text="Remarks: ", font=("Segoe UI", 9, "bold"), bg=LIST_BG, fg=TEXT_FG,
            anchor="nw", padx=7, pady=5, bd=0
        )
        remarks_label.grid(row=0, column=0, sticky="nw")

        remarks_val = tk.Label(
            remarks_card, text=remarks_text, font=("Segoe UI", 9, "bold"), fg=remarks_fg, bg=LIST_BG,
            anchor="w", justify="left", padx=7, pady=5, bd=0, wraplength=700
        )
        remarks_val.grid(row=0, column=1, sticky="ew")

        details_frame = ttk.LabelFrame(container, text="Details")
        details_frame.grid(row=curr_row, column=0, sticky="nsew", pady=(6, 0))
        curr_row += 1
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)
        details_text = tk.Text(details_frame, height=10, wrap="word", bg=LOG_BG, fg=LOG_FG,
                               highlightthickness=1, highlightbackground=BORDER_COLOR, relief="flat", bd=0)
        details_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=5)
        details_scroll = ttk.Scrollbar(details_frame, orient="vertical", command=details_text.yview)
        details_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=5)
        details_text.configure(yscrollcommand=details_scroll.set)
        details_text.insert("1.0", build_details_text(detail_rows, ftdc_criteria=ftdc_criteria, fmt=fmt))
        details_text.configure(state="disabled")
        details_frame.grid_remove()

        button_row = ttk.Frame(container)
        button_row.grid(row=curr_row, column=0, sticky="ew", pady=(6, 0))
        button_row.columnconfigure(0, weight=1)

        details_visible = {"value": False}

        def _resize_result_window(width: Optional[int] = None):
            window.update_idletasks()
            req_w = container.winfo_reqwidth() + 12
            target_w = max(width or 860, req_w)
            remarks_val.configure(wraplength=max(target_w - 110, 700))
            if po_label is not None:
                po_label.configure(wraplength=max(target_w - 40, 820))
            window.update_idletasks()
            try:
                screen_w = window.winfo_screenwidth()
                screen_h = max(window.winfo_screenheight() - 80, 480)
                target_w = min(target_w, max(screen_w - 40, 760))
                window.minsize(target_w, 180)
                natural_height = min(container.winfo_reqheight() + 6, screen_h)
                x = max(self.root.winfo_rootx() + max((self.root.winfo_width() - target_w) // 2, 0), 10)
                y = max(self.root.winfo_rooty() + max((self.root.winfo_height() - natural_height) // 2, 0), 20)
                window.geometry(f"{target_w}x{natural_height}+{x}+{y}")
            except Exception:
                window.geometry(f"{target_w}x{container.winfo_reqheight() + 6}")

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
        attach_tooltip(show_details_button, "Show or hide detailed FTDC analysis breakdown and criteria.")
        open_excel_button = ModernHoverButton(button_row, text="Download & Open Excel", command=open_excel, padx=10, pady=4)
        open_excel_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
        attach_tooltip(open_excel_button, "Save and open the detailed FTDC analysis Excel workbook.")
        close_res_button = ModernHoverButton(button_row, text="Close", command=window.destroy, padx=10, pady=4)
        close_res_button.grid(row=0, column=3, sticky="e")
        attach_tooltip(close_res_button, "Close the FTDC Result window.")

        _resize_result_window()
        target_img_path = result.get("_cached_image_path")
        if target_img_path:
            window.after(
                60,
                lambda p=target_img_path: (
                    capture_window_to_image(window, p)
                    if not (os.path.isfile(p) and os.path.getsize(p) > 0)
                    else None
                ),
            )
        if truncated_list:
            window.after(100, lambda: messagebox.showwarning(
                "Truncated STDF Warning",
                f"Warning: {len(truncated_list)} STDF file(s) used in this analysis were truncated or corrupted.\n\n"
                "Please review the warnings in the Result window and Activity Log.",
                parent=window,
            ))

    def _prompt_selection(self, title: str, prompt: str, options: List[str]) -> Optional[str]:
        """Display a modal dialog with buttons for each option and return the chosen option."""
        import tkinter as tk
        from tkinter import ttk
        
        dialog = tk.Toplevel(self.root)
        dialog.configure(bg=MAIN_BG)
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
            btn = ModernHoverButton(frame, text=opt, command=lambda o=opt: _on_select(o), padx=14, pady=6)
            btn.pack(fill="x", pady=5)
            
        cancel_btn = ModernHoverButton(frame, text="Cancel", command=dialog.destroy, padx=14, pady=6)
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
        except Exception as exc:
            logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
            
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
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        threading.Thread(target=_warm, daemon=True).start()

    def _on_lot_id_changed(self, *args):
        """Debounce handler. Waits a short, adaptive delay after the last change
        before looking up the MPC (shorter for scanned/complete lot ids)."""
        if self._mpc_lookup_after_id is not None:
            self.root.after_cancel(self._mpc_lookup_after_id)
            self._mpc_lookup_after_id = None

        lot_id = self.lot_id_var.get().strip()
        if not lot_id:
            self._cancel_detail_log_timer("Lot ID")
            self.mpc_var.set("")
            self._set_mpc_status("", is_error=False)
            return

        self._last_logged_lot_id = lot_id.upper()
        self._schedule_log_lot_detail("Lot ID")

        # Fix 1: instant cache hit — no network, no debounce wait.
        cached = self._mpc_cache.get(lot_id.upper())
        if cached is not None:
            self.mpc_var.set(cached)
            self._set_mpc_status("", is_error=False)
            return

        # Fix 3: adaptive debounce. Scanned/complete -> snappy; typing -> a bit
        # longer so we don't fire on every intermediate keystroke.
        delay = 150 if self._looks_like_complete_lot(lot_id) else 350
        self._mpc_lookup_after_id = self.root.after(
            delay, lambda: self._lookup_mpc_for_lot_id(lot_id)
        )

    def _lookup_mpc_for_lot_id(self, lot_id: str):
        """Look up the MPC for a lot id via the MES web service and populate the
        MPC field. Status/error messages are shown in self.mpc_status_label and
        NEVER placed inside the MPC entry field."""
        self._mpc_lookup_after_id = None

        # Fix 1: re-check the cache (the value may have arrived during debounce).
        cached = self._mpc_cache.get(lot_id.upper())
        if cached is not None:
            self.mpc_var.set(cached)
            self._set_mpc_status("", is_error=False)
            return

        # Fix 5: immediate visual feedback in status label (never in MPC entry!).
        if self.lot_id_var.get().strip().upper() == lot_id.upper():
            self._set_mpc_status("Looking up…", is_error=False)

        # Sequence guard: only the newest request may write the field.
        self._mpc_lookup_seq += 1
        my_seq = self._mpc_lookup_seq

        def worker():
            sess = self._get_http_session()
            if sess is None:
                return

            def _set_mpc_result(val: str, status_msg: str = "", is_err: bool = False, cache: bool = False):
                # Only apply if (a) this is still the newest lookup, and
                # (b) the user hasn't changed/cleared the Lot ID meanwhile.
                if my_seq != self._mpc_lookup_seq:
                    return
                if self.lot_id_var.get().strip().upper() != lot_id.upper():
                    return
                if cache and val:
                    self._mpc_cache[lot_id.upper()] = val
                self.mpc_var.set(val)
                self._set_mpc_status(status_msg, is_error=is_err)
                if status_msg and is_err:
                    self.log(f"MPC Lookup [{lot_id.upper()}]: {status_msg}")

            # Fix 4: split (connect, read) timeout — fail fast on a dead host,
            # still allow a slow-but-alive server to answer.
            try:
                resp = sess.post(
                    self._mpc_lookup_url, data={"LotId": lot_id}, timeout=(2, 8)
                )
            except Exception as exc:
                name = type(exc).__name__
                if "Timeout" in name:
                    self.root.after(0, lambda: _set_mpc_result("", "Network error: request timed out", True))
                elif "Connection" in name:
                    self.root.after(0, lambda: _set_mpc_result("", "Network error: unable to connect", True))
                else:
                    msg = str(exc)[:60]
                    self.root.after(0, lambda m=msg: _set_mpc_result("", f"Lookup failed: {m}", True))
                return

            # ── Parse the XML response ─────────────────────────────────────
            try:
                root_el = ET.fromstring(resp.text)
                ns = {"mes": "http://microchip.com/mes/"}

                reply_code_el = root_el.find("mes:ReplyCode", ns)
                reply_code = reply_code_el.text.strip() if reply_code_el is not None and reply_code_el.text else ""

                if reply_code == "-1":
                    self.root.after(0, lambda: _set_mpc_result("", "Lot ID not found", True))
                    return

                mpc_el = root_el.find(".//mes:MPC", ns)
                if mpc_el is not None and mpc_el.text and mpc_el.text.strip():
                    mpc_value = mpc_el.text.strip()
                    self.root.after(0, lambda v=mpc_value: _set_mpc_result(v, "", False, cache=True))
                else:
                    self.root.after(0, lambda: _set_mpc_result("", "MPC not found", True))
            except ET.ParseError:
                self.root.after(0, lambda: _set_mpc_result("", "Lookup failed: invalid response", True))
            except Exception as exc:
                msg = str(exc)[:60]
                self.root.after(0, lambda m=msg: _set_mpc_result("", f"Lookup failed: {m}", True))

        threading.Thread(target=worker, daemon=True).start()


    def _on_mpc_changed(self, *args):
        lot_id = self.lot_id_var.get().strip().upper()
        mpc_val = self.mpc_var.get().strip()
        if not mpc_val:
            self._cancel_detail_log_timer("MPC")
            return
        if len(mpc_val) == 12:
            self._set_mpc_status("", is_error=False)
        self._schedule_log_lot_detail("MPC")
        if lot_id and len(mpc_val) == 12:
            self._trigger_background_ftdc_fail_if_needed(lot_id, mpc_val)

    def _trigger_background_ftdc_fail_if_needed(self, lot_id: str, mpc_val: str):
        """Automatically trigger Get FTDC Fail in the background if MPC has 2 or more tester types."""
        lot_id_upper = lot_id.strip().upper()
        mpc_clean = mpc_val.strip()
        if not lot_id_upper or len(mpc_clean) != 12:
            return
        if lot_id_upper in self._ftdc_fail_cache:
            return
        th = self._ftdc_fail_threads.get(lot_id_upper)
        if th is not None and th.is_alive():
            return
        try:
            _, testers, _ = resolve_mpc_details(mpc_clean)
        except Exception:
            return
        if len(testers) < 2:
            return

        def _bg_worker():
            self.log(
                f"Get FTDC Fail: Background fetch triggered for Lot ID '{lot_id_upper}' "
                f"(MPC '{mpc_clean}' has {len(testers)} tester types: {', '.join(testers)})…"
            )
            entries = self._fetch_ftdc_fail_data(lot_id_upper)
            if entries:
                testers_found = [t for _, _, t in entries if t]
                t_str = f" (detected tester(s): {', '.join(testers_found)})" if testers_found else ""
                self.log(f"Get FTDC Fail: Cached {len(entries)} FAIL row(s) for '{lot_id_upper}'{t_str}.")
            else:
                self.log(f"Get FTDC Fail: No FAIL rows found or server unavailable for '{lot_id_upper}'.")

        t = threading.Thread(target=_bg_worker, daemon=True)
        self._ftdc_fail_threads[lot_id_upper] = t
        t.start()

    def _parse_ftdc_fail_html(self, html_text: str) -> List[Tuple[str, str, str]]:
        """Validate FTDC table headers and parse FAIL rows using shared parser."""
        return parse_ftdc_fail_html(html_text)

    def _fetch_ftdc_fail_data(self, lot_id: str) -> List[Tuple[str, str, str]]:
        """POST to the FTDC filter endpoint (with backup fallback) for lot_id and return FAIL rows as
        [(reply_msg, comment, tester_val), ...]. Results are cached in self._ftdc_fail_cache."""
        lot_id_upper = lot_id.strip().upper()
        if not lot_id_upper:
            return []
        if lot_id_upper in self._ftdc_fail_cache:
            return self._ftdc_fail_cache[lot_id_upper]

        try:
            fail_entries = fetch_and_parse_ftdc_fail(
                lot_id_upper,
                timeout=15,
                logger=self.log,
            )
            self._ftdc_fail_cache[lot_id_upper] = fail_entries
            return fail_entries
        except Exception as exc:
            self.log(f"Get FTDC Fail background fetch error for '{lot_id_upper}': {exc}")
            return []


    # ── Check STDF ─────────────────────────────────────────────────────────

    @staticmethod
    def _classify_stdf_panel(filename: str) -> Optional[str]:
        """Return designated panel ('FIRST PASS', 'RETEST', 'QC') for filename, or None if undesignated."""
        name_lower = filename.lower()
        if "_e_" in name_lower:
            return "FIRST PASS"
        if re.search(r"rj\d+_w", name_lower) or re.search(r"w_rj\d+", name_lower):
            return "RETEST"
        if re.search(r"rj\d+_q", name_lower) or "_q_" in name_lower:
            return "QC"
        return None

    def _prompt_panel_choice(self, parent=None, filename: str = ""):
        """Show a small popup with FIRST PASS / RETEST / QC buttons and return the choice."""
        import tkinter as tk

        dialog = tk.Toplevel(parent or self.root)
        dialog.configure(bg=MAIN_BG)
        dialog.title("Select Panel")
        dialog.resizable(False, False)
        dialog.transient(parent or self.root)
        dialog.grab_set()
        dialog.withdraw()

        selected: List[Optional[str]] = [None]

        frame = self.ttk.Frame(dialog, padding=15)
        frame.pack(fill="both", expand=True)

        prompt_msg = f"Load '{filename}' into which panel?" if filename else "Load file into which panel?"
        self.ttk.Label(
            frame, text=prompt_msg,
            font=("Segoe UI", 9),
            wraplength=420,
            justify="center",
        ).pack(pady=(0, 10))

        btn_row = self.ttk.Frame(frame)
        btn_row.pack()

        for panel in ("FIRST PASS", "RETEST", "QC"):
            def _on_click(p=panel):
                selected[0] = p
                dialog.destroy()
            ModernHoverButton(btn_row, text=panel, command=_on_click, padx=12, pady=4).pack(
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

        lot_id_val = self.lot_id_var.get().strip().upper()
        if not lot_id_val:
            messagebox.showwarning(
                "Check STDF",
                "Please enter a Lot ID first.",
                parent=self.root,
            )
            return

        self._log_current_lot_details_snapshot("Check STDF")
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

        win = tk.Toplevel(self.root)
        win.configure(bg=MAIN_BG)
        win.title(f"Check STDF \u2014 {lot_id}")
        win.geometry("1120x490")
        win.minsize(980, 360)
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
        col_weights = [36, 9, 9, 10, 19, 17]
        header_texts = ["File Name", "Total Parts", "Tested Good", "Status", "Discrepancy / Mismatch", "Action"]

        # ── Column header row ──────────────────────────────────────────────
        header_frame = tk.Frame(list_frame, bg=BORDER_COLOR)
        header_frame.grid(row=0, column=0, sticky="ew")
        for ci, (txt, wt) in enumerate(zip(header_texts, col_weights)):
            header_frame.columnconfigure(ci, weight=wt, uniform="col")
            align = "w" if ci == 0 else "center"
            px = 8 if ci == 0 else 4
            tk.Label(
                header_frame, text=txt,
                font=("Segoe UI", 9, "bold"), bg=MAIN_BG, fg=TEXT_FG,
                anchor=align, padx=px, pady=4,
            ).grid(row=0, column=ci, sticky="nsew", padx=(0, 1), pady=(0, 1))

        # ── Scrollable canvas ──────────────────────────────────────────────
        canvas = tk.Canvas(
            list_frame, bg=LIST_BG, highlightthickness=1, highlightbackground=BORDER_COLOR, borderwidth=0,
        )
        canvas.grid(row=1, column=0, sticky="nsew")
        v_scroll = self.ttk.Scrollbar(
            list_frame, orient="vertical", command=canvas.yview,
        )
        v_scroll.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=v_scroll.set)

        inner = tk.Frame(canvas, bg=BORDER_COLOR)
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
        scan_frame = tk.Frame(inner, bg=LIST_BG)
        scan_frame.grid(row=0, column=0, columnspan=6, sticky="ew", padx=0, pady=(8, 8))
        scan_frame.columnconfigure(0, weight=1)

        scan_inner = tk.Frame(scan_frame, bg=LIST_BG)
        scan_inner.grid(row=0, column=0, sticky="")

        scan_pbar = self.ttk.Progressbar(
            scan_inner, mode="indeterminate", length=80,
        )
        scan_pbar.pack(side="left", padx=(0, 8))
        scan_pbar.start(15)

        scan_lbl = tk.Label(
            scan_inner, text="Scanning folder for STDF files…",
            font=("Segoe UI", 9), bg=LIST_BG, fg=TEXT_MUTED,
        )
        scan_lbl.pack(side="left")

        # ── Load All + Close buttons ───────────────────────────────────────
        btn_frame = self.ttk.Frame(container)
        btn_frame.grid(row=2, column=0, sticky="e", pady=(8, 0))
        ModernHoverButton(
            btn_frame, text="Close", command=lambda: _on_win_close(), padx=12, pady=4,
        ).pack(side="right")
        load_all_btn = ModernHoverButton(
            btn_frame, text="Load All", command=lambda: _load_all_files(), padx=12, pady=4,
        )
        load_all_btn.pack(side="right", padx=(0, 8))

        # ── FORCE INSTANT PAINT OF WINDOW, HEADERS AND SPINNER ─────────────
        # Force Tkinter to draw the HWND, title, path label, column headers,
        # scrollbars, Close button, and scan spinner IMMEDIATELY so the user
        # never sees a white/blank unrendered window.
        try:
            win.update_idletasks()
            win.update()
        except Exception as exc:
            logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

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
            bg = LIST_BG

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
                bg=bg, fg=TEXT_MUTED, anchor="center", padx=4, pady=3,
            )
            st_lbl.grid(row=ri, column=3, sticky="nsew", padx=(0, 1), pady=(0, 1))

            # col 4 — Discrepancy / Mismatch
            disc_lbl = tk.Label(
                inner, text="--", font=cell_font,
                bg=bg, fg=TEXT_MUTED, anchor="center", padx=4, pady=3,
            )
            disc_lbl.grid(row=ri, column=4, sticky="nsew", padx=(0, 1), pady=(0, 1))

            # col 5 — Action (Open + Load + Delete centered as a singular object)
            act_frame = tk.Frame(inner, bg=bg)
            act_frame.grid(row=ri, column=5, sticky="nsew", padx=(0, 1), pady=(0, 1))
            act_frame.columnconfigure(0, weight=1)
            act_frame.rowconfigure(0, weight=1)

            act_inner = tk.Frame(act_frame, bg=bg)
            act_inner.grid(row=0, column=0, sticky="")

            rw: Dict[str, Any] = {
                "fn_lbl": fn_lbl, "tp_lbl": tp_lbl, "tg_lbl": tg_lbl,
                "st_lbl": st_lbl, "disc_lbl": disc_lbl, "act_frame": act_frame,
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
                    target = self._classify_stdf_panel(fn)
                    if target is None:
                        target = self._prompt_panel_choice(win, filename=fn)
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
                                    rw_ref["tg_lbl"], rw_ref["disc_lbl"]):
                            lbl.configure(fg="#AAAAAA")
                        rw_ref["st_lbl"].configure(text="Deleted", fg="#AAAAAA")
                        rw_ref["status"] = "Deleted"
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
        def _paint_row(widgets, status, tag, pc_str, gc_str, disc_str="None"):
            """Apply a parsed summary to a row's widgets (main thread only)."""
            if tag == "completed":
                fg = "#008000"
            elif tag == "warning":
                fg = "#D97706"
            else:
                fg = "#C00000"
            disc_fg = "#008000" if disc_str == "None" else "#C00000"
            disc_font = cell_font if disc_str == "None" else ("Segoe UI", 8, "bold")
            widgets["status"] = status  # Used by Load All (only 'Completed' files are loaded)
            try:
                if _win_closed[0] or not win.winfo_exists():
                    return
                widgets["st_lbl"].configure(text=status, fg=fg, font=("Segoe UI", 9, "bold"))
                widgets["tp_lbl"].configure(text=pc_str)
                widgets["tg_lbl"].configure(text=gc_str)
                if "disc_lbl" in widgets and widgets["disc_lbl"] is not None:
                    widgets["disc_lbl"].configure(text=disc_str, fg=disc_fg, font=disc_font)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

        # ── File parser (background thread) ───────────────────────────────
        # Parsing, row derivation, caching and batched logging are shared with the
        # Get STDF pre-warm via self._submit_check_task / self._check_task.

        _file_summaries: Dict[str, Tuple[str, str, str, str]] = {}

        def _log_lot_check_summary():
            tot_parts = 0
            tot_good = 0
            mismatches_found = []
            for fn, (st, pc_s, gc_s, dc_s) in _file_summaries.items():
                if str(pc_s).isdigit():
                    tot_parts += int(pc_s)
                if str(gc_s).isdigit():
                    tot_good += int(gc_s)
                if dc_s and dc_s != "None":
                    mismatches_found.append(f"{fn}: {dc_s}")
                elif st != "Completed":
                    mismatches_found.append(f"{fn}: {st}")
            mm_txt = "; ".join(mismatches_found) if mismatches_found else "None"
            self.log(
                f"Check STDF Lot Summary [{lot_id}]: Files={len(_file_summaries)}, "
                f"Quantity Total Parts={tot_parts:,}, Quantity Tested Good={tot_good:,}, "
                f"Mismatches={mm_txt}"
            )

        def _load_all_files():
            existing_rows = [
                (fn, rw) for fn, rw in row_widgets.items()
                if os.path.isfile(rw["filepath"])
            ]
            # Only files whose Check STDF status is exactly 'Completed' are loaded.
            active_rows = [
                (fn, rw["filepath"]) for fn, rw in existing_rows
                if rw.get("status") == "Completed"
            ]
            skipped_rows = [(fn, rw.get("status") or "Pending") for fn, rw in existing_rows
                            if rw.get("status") != "Completed"]
            for fn, st in skipped_rows:
                self.log(f"Check STDF [Load All]: Skipped '{fn}' (status: {st}; only 'Completed' files are loaded).")
            if not active_rows:
                if skipped_rows:
                    messagebox.showinfo(
                        "Load All",
                        f"No STDF files with 'Completed' status to load.\n\n"
                        f"{len(skipped_rows)} file(s) were skipped because their status is not 'Completed' "
                        "(still checking, corrupted, mismatch or warning).",
                        parent=win,
                    )
                else:
                    messagebox.showinfo("Load All", "No STDF files available to load.", parent=win)
                return

            designated: List[Tuple[str, str, str]] = []
            undesignated: List[Tuple[str, str]] = []
            for fn, fp in active_rows:
                panel = self._classify_stdf_panel(fn)
                if panel is not None:
                    designated.append((fn, fp, panel))
                else:
                    undesignated.append((fn, fp))

            loaded_by_panel: Dict[str, int] = {p: 0 for p in self.PANELS}
            # Load all STDF files with a designated panel simultaneously
            for fn, fp, target in designated:
                norm_fp = os.path.abspath(fp).replace("\\", "/")
                if norm_fp not in self.panel_files[target]:
                    self.panel_files[target].append(norm_fp)
                    loaded_by_panel[target] += 1
                    self.log(f"Check STDF [Load All]: Loaded '{fn}' into {target} panel.")
                else:
                    self.log(f"Check STDF [Load All]: '{fn}' already in {target} panel.")

            # Prompt the user for STDF files that do not have a designated panel
            for fn, fp in undesignated:
                target = self._prompt_panel_choice(win, filename=fn)
                if target is None:
                    self.log(f"Check STDF [Load All]: Skipped '{fn}' (panel selection cancelled).")
                    continue
                norm_fp = os.path.abspath(fp).replace("\\", "/")
                if norm_fp not in self.panel_files[target]:
                    self.panel_files[target].append(norm_fp)
                    loaded_by_panel[target] += 1
                    self.log(f"Check STDF [Load All]: Loaded '{fn}' into {target} panel (user selected).")
                else:
                    self.log(f"Check STDF [Load All]: '{fn}' already in {target} panel.")

            self.refresh_all_file_lists()
            total_added = sum(loaded_by_panel.values())
            self.log(
                f"Check STDF [Load All]: Loaded {total_added} new file(s) "
                f"(FIRST PASS: {loaded_by_panel['FIRST PASS']}, "
                f"RETEST: {loaded_by_panel['RETEST']}, QC: {loaded_by_panel['QC']})."
            )

        # ── Completion tracking ───────────────────────────────────────────
        def _release_running_if_done():
            with _remaining_lock:
                _remaining[0] -= 1
                done = _remaining[0] <= 0
            if done:
                _log_lot_check_summary()
                self._save_check_cache_async()
                if _owns_running[0]:
                    _owns_running[0] = False
                    self._set_running(False)

        def _on_future_done(fname, future):
            try:
                status, tag, pc_str, gc_str, disc_str = future.result()
            except Exception as exc:
                logging.warning("Check STDF worker future failed: %s", exc)
                _release_running_if_done()
                return
            # Result already cached (and logged) by self._check_task.
            _file_summaries[fname] = (status, pc_str, gc_str, disc_str)
            widgets = row_widgets.get(fname)

            def _update():
                if widgets is not None:
                    _paint_row(widgets, status, tag, pc_str, gc_str, disc_str)
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
                    # Signatures come from DirEntry.stat() (free on Windows) - computed here on the
                    # background thread so the Tk main thread never performs per-file stat calls.
                    scanned = self._scan_stdf_folder(folder_path, stdf_extensions)
                    stdf_files = [name for name, _ in scanned]
                    file_sigs: Dict[str, Any] = {name: (fsig, lot_id) for name, fsig in scanned}
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
                    except Exception as exc:
                        logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

                    # Phase 3: Start parsing (cached instantly, uncached in threads)
                    _uncached_sigs: Dict[str, Any] = {}
                    uncached_files: List[str] = []
                    cached_count = 0
                    for filename in stdf_files:
                        sig = file_sigs[filename]
                        with self._check_cache_lock:
                            cached = self._check_summary_cache.get(sig)
                        widgets = row_widgets.get(filename)
                        if cached is not None and widgets is not None and len(cached) == 5:
                            status, tag, pc_str, gc_str, disc_str = cached
                            _file_summaries[filename] = (status, pc_str, gc_str, disc_str)
                            _paint_row(widgets, status, tag, pc_str, gc_str, disc_str)
                            self.log(
                                f"Check STDF [{filename}] (cached): Status={status}, Total Parts={pc_str}, "
                                f"Tested Good={gc_str}, Mismatch/Discrepancy={disc_str}"
                            )
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
                        max_workers = min(len(uncached_files), self.CHECK_WORKER_CAP)
                        executor = ThreadPoolExecutor(max_workers=max_workers)
                        _executor_ref[0] = executor
                        for filename in uncached_files:
                            future = self._submit_check_task(
                                executor, os.path.join(folder_path, filename), lot_id,
                                _uncached_sigs[filename],
                            )
                            future.add_done_callback(lambda f, fn=filename: _on_future_done(fn, f))
                    else:
                        self.log(
                            f"Check STDF: All {len(stdf_files)} file(s) loaded from cache."
                        )
                        _log_lot_check_summary()

                self.root.after(0, _build_rows)

            except Exception as exc:
                _msg = str(exc)
                def _gen_err(m=_msg):
                    if _win_closed[0]:
                        return
                    scan_pbar.stop()
                    scan_lbl.configure(text=f"Error: {m}", fg="#C00000")
                    scan_pbar.pack_forget()
                    if _owns_running[0]:
                        _owns_running[0] = False
                        self._set_running(False)
                self.root.after(0, _gen_err)

        # ── Window close handler ──────────────────────────────────────────
        def _on_win_close():
            _win_closed[0] = True
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
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

        try:
            mpc_val = validate_mpc_code(mpc_val)
        except ValueError as exc:
            self.log(f"Get STDF: Blocked — {exc}")
            messagebox.showerror("Invalid MPC", str(exc), parent=self.root)
            return

        # ── Start progress immediately on click ─────────────────────────────
        self._set_running(True)
        self.update_progress(0.02, "Resolving device…")
        self.log("=" * 80)
        self._log_current_lot_details_snapshot("Get STDF")
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

        # Prompt for Tester if multiple exist (with automatic resolution via FTDC Fail cache)
        chosen_tester = None
        if len(testers) > 1:
            # If background FTDC Fail thread is in progress, wait briefly
            th = self._ftdc_fail_threads.get(lot_id_val)
            if th is not None and th.is_alive():
                self.update_progress(0.03, "Checking FTDC tester logs…")
                th.join(timeout=2.5)

            cached_fails = self._ftdc_fail_cache.get(lot_id_val)
            if cached_fails:
                for _, _, ftdc_tester in cached_fails:
                    if not ftdc_tester:
                        continue
                    ftdc_tester_up = ftdc_tester.upper()
                    for t in testers:
                        if t.strip().upper() in ftdc_tester_up:
                            chosen_tester = t
                            break
                    if chosen_tester:
                        self.log(
                            f"Get STDF: Automatically selected tester '{chosen_tester}' "
                            f"matching FTDC tester '{ftdc_tester}'."
                        )
                        break

            if not chosen_tester:
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
        else:
            chosen_tester = testers[0]

        # Resolve the valid STDF filename conventions for this mask + tester
        # (assets/stdf_filename_conventions.json). Only matching files are fetched.
        try:
            filename_conventions = resolve_stdf_filename_conventions(mpc_val, chosen_tester)
            filename_patterns = compile_stdf_filename_patterns(lot_id_val, filename_conventions)
        except ValueError as exc:
            self._set_running(False)
            self.reset_progress("No STDF filename convention")
            self.log(f"Get STDF: {exc}")
            messagebox.showerror("STDF Filename Convention", str(exc), parent=self.root)
            return
        except RuntimeError as exc:
            self._set_running(False)
            self.reset_progress("Configuration error")
            self.log(f"Get STDF: {exc}")
            messagebox.showerror("Configuration Error", str(exc), parent=self.root)
            return

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
        self.log(f"  Valid STDF filename conventions (mask '{mpc_val.strip().upper()[:5]}', {chosen_tester}):")
        for _cat, _pats in filename_conventions.items():
            self.log(f"    - {_cat}: {', '.join(_pats)}")

        def _search_worker():
            try:
                # Local destination cache scan off main thread
                already_have = existing_stdf_basenames(lot_id_val)
                if already_have:
                    self.log(
                        f"  Cache: {len(already_have)} file(s) already in "
                        f"{get_local_dest_base()}\\{lot_id_val} will be skipped."
                    )

                found = search_stdf_files(
                    lot_id_val, device_names,
                    network_bases=network_bases,
                    tester_type=chosen_tester,
                    skip_existing_in_dest=True,
                    logger=self.log, progress_callback=self.update_progress,
                    mpc=mpc_val,
                    filename_patterns=filename_patterns,
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
                                f"{get_local_dest_base()}\\{lot_id_val} "
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
                    0, lambda f=list(found), ct=chosen_tester: self._get_stdf_confirm(f, lot_id_val, device_names, ct)
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

    def _get_stdf_confirm(self, found_files: list, lot_id: str, device_names: list, chosen_tester: str = ""):
        """Automatically start the copy phase without pausing for confirmation."""
        from tkinter import messagebox

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
        except Exception as exc:
            logging.warning("Failed to get common paths in _get_stdf_confirm: %s", exc)
            common_short = []
        if common_short:
            common_line = " and common folder(s): " + ", ".join(common_short)
        else:
            common_line = ""

        source_folders = []
        for f in found_files:
            parent_dir = os.path.dirname(f)
            folder_name = os.path.basename(parent_dir) or parent_dir
            if folder_name not in source_folders:
                source_folders.append(folder_name)

        self.log(
            f"Get STDF Summary: Tester Used='{chosen_tester or 'N/A'}', "
            f"Files Found={len(found_files)}, "
            f"Source Device Folder(s)={', '.join(source_folders) if source_folders else ', '.join(device_names)}"
        )
        self.log(
            f"Get STDF: Found {len(found_files)} matching STDF file(s) for Lot ID '{lot_id}' "
            f"in device folder(s) {', '.join(device_names)}{common_line}."
        )
        for f in found_files:
            src_folder = os.path.basename(os.path.dirname(f)) or os.path.dirname(f)
            self.log(f"  • {os.path.basename(f)}  [from folder: {src_folder} ({os.path.dirname(f)})]")
        self.log(f"Get STDF: Automatically proceeding to copy all files to C:\\FTDC\\{lot_id}…")

        # ── Phase 2: copy (background thread) ─────────────────────────────
        self.log(f"Get STDF: Copying {len(found_files)} file(s)…")

        def _copy_worker():
            # Streaming Check STDF pre-warm: each file is parsed/cached (in memory) right after
            # its atomic .part -> final rename, on a 2-worker pool while other files still copy.
            on_file_copied, stream_executor, stream_futures = self._start_streaming_prewarm(lot_id)
            try:
                # Option 2 parallel copy progress: aggregate batch bar (0→100% of total lot bytes),
                # largest-active-file spotlight status text, and 'Done X/N | copied/total | speed | ETA' stats.
                dest_dir = copy_stdf_files_per_file(
                    found_files, lot_id,
                    logger=self.log,
                    per_file_progress_callback=self.update_progress,
                    copy_stats_callback=self.set_copy_stats,
                    on_file_copied=on_file_copied,
                )
                self.update_progress(1.0, "STDF files copied")
                # Finish the pre-warm for any file not yet cached (full 4-worker pool now that
                # copying is done), await the streamed tasks, then flush the cache JSON ONCE.
                self.prewarm_check_stdf_cache(
                    dest_dir, lot_id,
                    pending_futures=stream_futures,
                    stream_executor=stream_executor,
                )

                def _done():
                    self._set_running(False)
                    self.copy_stats_var.set("")
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
                    except Exception as exc:
                        logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
                self.root.after(0, _done)

            except Exception as exc:
                _msg = str(exc)
                # Keep whatever was streamed into the cache, flush it once, and release the pool.
                self.prewarm_check_stdf_cache(
                    os.path.join(get_local_dest_base(), lot_id),
                    lot_id,
                    pending_futures=stream_futures,
                    stream_executor=stream_executor,
                )
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
        """POST to the FTDC filter endpoint using the current Lot ID and display FAIL rows.
        If results are already cached in memory, displays them immediately without re-querying."""
        import tkinter as tk
        from tkinter import messagebox

        # ── Dependency check ───────────────────────────────────────────────
        try:
            import requests as _requests
            from lxml import html as _lxml_html  # noqa: F401
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
        self._log_current_lot_details_snapshot("Get FTDC Fail")

        # ── 1. Check cache first ───────────────────────────────────────────
        if lot_id_val in self._ftdc_fail_cache:
            self.log(f"Get FTDC Fail: Displaying cached FTDC logs result for '{lot_id_val}'.")
            self._set_running(True)
            self.update_progress(1.0, "FTDC fetch complete")
            self.set_status("FTDC fetch complete")
            self._show_ftdc_fail_popup(lot_id_val, self._ftdc_fail_cache[lot_id_val])
            return

        # ── Background worker (uses shared fetch_and_parse_ftdc_fail) ──────
        def _worker():
            TIMEOUT_SECONDS = 15

            def _finish(ok: bool, msg: str = ""):
                self._set_running(False)
                if ok:
                    self.update_progress(1.0, "FTDC fetch complete")
                    self.set_status("FTDC fetch complete")
                else:
                    self.reset_progress(msg or "FTDC fetch failed")

            try:
                self.root.after(0, lambda: self.update_progress(0.25, "Sending request to FTDC server…"))
                fail_entries = fetch_and_parse_ftdc_fail(
                    lot_id_val,
                    timeout=TIMEOUT_SECONDS,
                    logger=self.log,
                    on_response_received=lambda: self.root.after(
                        0, lambda: self.update_progress(0.60, "Parsing FTDC response…")
                    ),
                    on_scanning_rows=lambda: self.root.after(
                        0, lambda: self.update_progress(0.80, "Scanning for FAIL rows…")
                    ),
                )

                # Cache the results
                self._ftdc_fail_cache[lot_id_val] = fail_entries
                self.root.after(0, lambda: self.update_progress(1.0, "FTDC fetch complete"))
                self.root.after(0, lambda: self._show_ftdc_fail_popup(lot_id_val, fail_entries))

            # ── Network / HTTP / Layout error handlers ─────────────────────
            except FTDCHttpError as http_exc:
                err_msg = (
                    f"FTDC server returned HTTP {http_exc.status_code}.\n\n"
                    f"Reason: {http_exc.reason}\n\n"
                    "Please check the FTDC server or try again later:\n\n"
                    f"{FTDC_FAIL_PRIMARY_URL}\n"
                    f"{FTDC_FAIL_BACKUP_URL}"
                )
                self.root.after(0, lambda m=err_msg, sc=http_exc.status_code: (
                    _finish(False, f"FTDC HTTP {sc}"),
                    messagebox.showerror("FTDC HTTP Error", m, parent=self.root),
                ))

            except _requests.exceptions.ConnectionError:
                def _conn_err():
                    _finish(False, "FTDC: connection failed")
                    messagebox.showerror(
                        "FTDC Connection Error",
                        "Unable to connect to the FTDC server.\n\n"
                        "Please make sure your device is connected to the correct network\n"
                        "and that the FTDC server is reachable:\n\n"
                        f"{FTDC_FAIL_PRIMARY_URL}\n"
                        f"{FTDC_FAIL_BACKUP_URL}",
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
                        "Please try again later:\n\n"
                        f"{FTDC_FAIL_PRIMARY_URL}\n"
                        f"{FTDC_FAIL_BACKUP_URL}",
                        parent=self.root,
                    )
                self.root.after(0, _timeout_err)

            except ValueError as val_err:
                _msg = str(val_err)
                def _val_err(m=_msg):
                    _finish(False, "FTDC page layout error")
                    messagebox.showerror("FTDC Page Layout Error", m, parent=self.root)
                self.root.after(0, _val_err)

            except Exception as exc:
                _msg = str(exc)
                def _gen_err(m=_msg):
                    _finish(False, "FTDC fetch failed")
                    messagebox.showerror("FTDC Error", f"An unexpected error occurred:\n\n{m}", parent=self.root)
                self.root.after(0, _gen_err)

        # ── 2. Check if background fetch is running ────────────────────────
        th = self._ftdc_fail_threads.get(lot_id_val)
        if th is not None and th.is_alive():
            self._set_running(True)
            self.update_progress(0.50, "Waiting for background FTDC fetch…")
            def _wait_and_show():
                th.join(timeout=3.0)
                cached = self._ftdc_fail_cache.get(lot_id_val)
                if cached is not None:
                    self.log(f"Get FTDC Fail: Displaying cached FTDC logs result for '{lot_id_val}'.")
                    self.root.after(0, lambda: self._show_ftdc_fail_popup(lot_id_val, cached))
                else:
                    threading.Thread(target=_worker, daemon=True).start()
            threading.Thread(target=_wait_and_show, daemon=True).start()
            return

        # ── 3. Fresh fetch ────────────────────────────────────────────────
        self._set_running(True)
        self.update_progress(0.05, "Connecting to FTDC server…")
        threading.Thread(target=_worker, daemon=True).start()

    def _show_ftdc_fail_popup(self, lot_id_val: str, fail_entries: List[Tuple[str, str, str]]):
        """Display the FTDC FAIL popup table with Reply Message and Comment columns."""
        import tkinter as tk
        from tkinter import messagebox

        self._set_running(False)
        self.update_progress(1.0, "FTDC fetch complete")
        self.set_status("FTDC fetch complete")

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

        reply_natural   = _col_natural_px([rm for rm, _, _ in fail_entries], "Reply Message")
        comment_natural = _col_natural_px([cm for _, cm, _ in fail_entries], "Comment")

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
            for rm, cm, _ in fail_entries
        ]

        # ── Build window ───────────────────────────────────────
        pop = tk.Toplevel(self.root)
        pop.configure(bg=MAIN_BG)
        pop.title(f"FTDC Logs Result - {lot_id_val}")
        pop.resizable(True, True)
        pop.transient(self.root)
        pop.columnconfigure(0, weight=1)
        pop.rowconfigure(0, weight=1)

        frm = self.ttk.Frame(pop, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")
        frm.columnconfigure(0, weight=1)

        # ── Single flat table — header in row 0, data in rows 1+ ──
        table = tk.Frame(frm, bg=BORDER_COLOR)
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
                font=("Segoe UI", 9, "bold"), bg=MAIN_BG, fg=TEXT_FG,
                anchor="w", padx=8, pady=4,
                width=chars,
                highlightthickness=1, highlightbackground=BORDER_COLOR, relief="flat", bd=0,
            ).grid(row=0, column=col_idx, sticky="ew")

        # Data rows
        for r_idx, ((reply_msg, comment, _), h) in enumerate(
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
                    bg=LIST_BG, fg=TEXT_FG,
                    highlightthickness=1, highlightbackground=BORDER_COLOR, relief="flat", bd=0,
                    padx=6, pady=4,
                    cursor="xterm",
                )
                cell.insert("1.0", text)
                cell.configure(state="disabled")
                cell.grid(row=r_idx, column=c_idx, sticky="nsew")

        btn_row = self.ttk.Frame(frm)
        btn_row.grid(row=1, column=0, sticky="e", pady=(10, 0))
        ModernHoverButton(btn_row, text="Close", command=pop.destroy, padx=14, pady=4).pack(side="right")

        # Let tkinter measure the true content size, then apply it
        def _fit_window():
            pop.update_idletasks()
            w = min(pop.winfo_reqwidth(),  MAX_WIN_W)
            h = min(pop.winfo_reqheight(), MAX_WIN_H)
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            x  = rx + max((rw - w) // 2, 0)
            y  = ry + max((rh - h) // 2, 0)
            pop.geometry(f"{w}x{h}+{x}+{y}")

        pop.after(1, _fit_window)

    start_ftdc_fail = start_get_ftdc_fail

    def _flush_pending_running_state(self):
        running = self._pending_running_state
        if running is None:
            return
        self._pending_running_state = None
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
                except Exception as exc:
                    logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        for btn in getattr(self, "_auxiliary_buttons", ()):
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception as exc:
                    logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        self._sync_manual_filter_state()

    def _set_running(self, running: bool):
        self.is_running = running
        self._pending_running_state = running
        if self.hub:
            try:
                self.hub.set_tab_busy(self.app_name, running)
                if not running:
                    self.hub.notify_tab_finished(self.app_name)
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)
        if threading.current_thread() is threading.main_thread():
            self._flush_pending_running_state()
        elif self._progress_poll_id is None and self._ppo_status_poll_id is None:
            try:
                self.root.after(0, self._flush_pending_running_state)
            except RuntimeError:
                pass
            except Exception as exc:
                logging.warning("Suppressed exception in FTDC Checker UI: %s", exc)

    def start_get_data(self):
        from tkinter import messagebox
        # Always capitalize the Lot ID (and normalize PPO# when targeted attach is enabled)
        lot_id_upper = self.lot_id_var.get().strip().upper()
        self.lot_id_var.set(lot_id_upper)
        if self.ENABLE_TARGETED_PPO_ATTACH:
            self._normalize_ppo_var()

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
        ]
        if self.ENABLE_TARGETED_PPO_ATTACH:
            required_fields.append(("PPO#", self.ppo_var))
        required_fields.extend([
            ("First Pass Actual Good QTY", self.fp_actual_good_qty_var),
            ("Total Actual Good QTY", self.total_actual_good_qty_var),
        ])
        empty_fields = [label for label, var in required_fields if not var.get().strip()]
        if empty_fields:
            messagebox.showerror(
                "Missing Required Input",
                "Please fill in all required input fields.\n\nMissing field(s): " + ", ".join(empty_fields),
            )
            return
        lot_id_text = self.lot_id_var.get().strip().upper()
        mpc_text = self.mpc_var.get().strip().upper()
        ppo_text = self._normalize_ppo_var() if self.ENABLE_TARGETED_PPO_ATTACH else ""
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
            mpc_text = validate_mpc_code(mpc_text)
        except ValueError as exc:
            self.log(f"Run Analysis: Blocked — {exc}")
            messagebox.showerror("Invalid MPC", str(exc))
            return

        self._log_current_lot_details_snapshot("Run Analysis")

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
            cached_result["ppo_num"] = ppo_text if self.ENABLE_TARGETED_PPO_ATTACH else ""
            self.log("=" * 80)
            self.log("Using cached result for the current session. No STDF reprocessing was needed.")
            self.update_progress(1.0, "")
            total_status_text = cached_result.get('total_good_status') or 'SKIPPED'
            ppo_attached = cached_result.get("_ppo_attached_paths") or []
            attached_ppo = str(cached_result.get("_ppo_attached_ppo_num") or "").strip().upper()
            if ppo_attached and (not self.ENABLE_TARGETED_PPO_ATTACH or attached_ppo == ppo_text):
                folder_cnt = len({os.path.dirname(str(p)) for p in ppo_attached})
                ppo_suffix = f" | Auto-attach to PPO: Done ({folder_cnt} subfolder(s))"
            elif not self._is_auto_ppo_attach_enabled():
                ppo_suffix = " | Auto-attach to PPO: Disabled (Developer Mode)"
            elif self._ppo_attach_state == "IN_PROGRESS":
                ppo_suffix = " | Auto-attach to PPO: On-going..."
            else:
                ppo_suffix = ""
            self.set_status(f"Cached result: FP {cached_result['status']} | Total Good {total_status_text}{ppo_suffix}")
            self.root.after(0, lambda result=cached_result: self._deliver_analysis_result(result))
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
            handed_off_to_ppo = False
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
                result["ppo_num"] = ppo_text if self.ENABLE_TARGETED_PPO_ATTACH else ""
                self._analysis_cache[cache_key] = result
                self.update_progress(1.0, "")
                # Log FTDC fails (from FTDC server logs if fetched + FTDC Criteria check details) and final result
                _, detail_rows, fp_st, tot_st, _, ftdc_crit = build_summary_and_details(result)
                cached_ftdc_fails = self._ftdc_fail_cache.get(lot_id_text.upper())
                if cached_ftdc_fails:
                    self.log(f"FTDC Server Fails for Lot '{lot_id_text.upper()}' ({len(cached_ftdc_fails)} fail row(s)):")
                    for f_i, (r_m, r_c, r_t) in enumerate(cached_ftdc_fails, start=1):
                        self.log(f"  FTDC Server Fail #{f_i}: Reply Message='{r_m}' | Comment='{r_c}' | Tester='{r_t}'")
                details_report = build_details_text(detail_rows, ftdc_criteria=ftdc_crit, fmt=_fmt)
                self.log("-" * 80)
                self.log("Run Analysis Detailed Report (WXY, Counts, FTDC Criteria & Result):")
                for line in details_report.splitlines():
                    if line.strip():
                        self.log(f"  {line}")
                total_status_text = result.get('total_good_status') or 'SKIPPED'
                self.log(
                    f"Run Analysis Final Result: First Pass Result={fp_st} | "
                    f"Total Good Result={total_status_text} | "
                    f"Check 1={result.get('check1_result')} | "
                    f"Check 2={result.get('check2_result')} | "
                    f"Check 4={result.get('check4_result')}"
                )
                remarks_log = str(result.get("remarks") or compute_remarks(result)).strip()
                self.log(f"Run Analysis Remarks: {remarks_log}")
                if self._is_auto_ppo_attach_enabled():
                    self.set_status(f"Analysis completed: FP {result['status']} | Total Good {total_status_text}")
                else:
                    self.set_status(
                        f"Analysis completed: FP {result['status']} | Total Good {total_status_text} | "
                        "Auto-attach to PPO: Disabled (Developer Mode)"
                    )
                self.root.after(0, lambda result=result: self._deliver_analysis_result(result))
                self._start_background_excel_generation(result, auto_attach=True)
                handed_off_to_ppo = (self._ppo_attach_state == "IN_PROGRESS")
            except Exception as exc:
                self.log("Analysis failed.")
                self.log(str(exc))
                self.log(traceback.format_exc())
                self.reset_progress("Analysis failed")
                self.root.after(0, lambda m=str(exc): messagebox.showerror("STDF GUID Checker", m))
            finally:
                if not handed_off_to_ppo:
                    self._set_running(False)

        threading.Thread(target=worker, daemon=True).start()

    # ── Device Settings Manager (Pre-warmed Cache & Dialog) ──────────────────
    @staticmethod
    def _format_device_display(val) -> str:
        """Format a device configuration value (str or list) for table display."""
        if isinstance(val, list):
            return ", ".join(str(x) for x in val)
        return str(val or "")

    @staticmethod
    def _format_tester_list(val) -> List[str]:
        if isinstance(val, list):
            return [str(x).strip().upper() for x in val if str(x).strip()]
        if isinstance(val, str):
            return [x.strip().upper() for x in val.split(",") if x.strip()]
        return []

    def _rebuild_device_settings_cache_data(self, reload_disk: bool = True):
        """Build the in-memory table rows, MPC lookup map, and max-length strings per column.
        Safe to call from a background pre-warm thread or after Save/Delete."""
        if reload_disk:
            reload_config()
            reload_wxy_config()
        mpc_map = load_mpc_partnumber_map()
        rows: List[Tuple[str, Tuple[str, str, str, str, str], Dict[str, Any]]] = []
        by_mpc: Dict[str, Dict[str, Any]] = {}
        cols = ("mpc", "mask", "device", "tester", "wxy")
        max_len_str: Dict[str, str] = {
            "mpc": "MPC",
            "mask": "Mask",
            "device": "Device Folder(s)",
            "tester": "Tester",
            "wxy": "WXY Tests (W/X/Y)",
        }

        for mpc_k in sorted(mpc_map.keys()):
            info = mpc_map[mpc_k] if isinstance(mpc_map[mpc_k], dict) else {}
            mask_k = mpc_k[:5]
            dev_str = self._format_device_display(info.get("device"))
            t_list = self._format_tester_list(info.get("tester"))
            tst_str = ", ".join(t_list)
            wxy_info = MPC_WXY_TEST_MAP.get(mask_k)
            if wxy_info:
                extras = [lbl for k, lbl in (("uid", "UID"), ("bt", "BT"), ("zb", "ZB")) if wxy_info.get(k)]
                if extras:
                    wxy_str = f"{wxy_info.get('wafer')} / {wxy_info.get('x')} / {wxy_info.get('y')} (+{'/'.join(extras)})"
                else:
                    wxy_str = f"{wxy_info.get('wafer')} / {wxy_info.get('x')} / {wxy_info.get('y')}"
            else:
                wxy_str = "—"
            vals = (mpc_k, mask_k, dev_str, tst_str, wxy_str)
            for cid, val_txt in zip(cols, vals):
                s_val = str(val_txt)
                if len(s_val) > len(max_len_str[cid]):
                    max_len_str[cid] = s_val

            haystack = f"{mpc_k} {mask_k} {dev_str} {tst_str} {wxy_str}".upper()
            row_meta = {
                "mpc": mpc_k,
                "mask": mask_k,
                "device": dev_str,
                "testers": t_list,
                "wxy": wxy_info,
            }
            rows.append((haystack, vals, row_meta))
            by_mpc[mpc_k] = row_meta

        with self._ds_cache_lock:
            self._ds_cached_rows = rows
            self._ds_cached_by_mpc = by_mpc
            self._ds_max_len_str = max_len_str
            self._ds_cache_ready = True

    def _prewarm_device_settings_manager(self):
        """Pre-warm the Device Settings data cache in a background thread and pre-build
        the hidden Toplevel window on idle so opening 'Device Settings' has zero lag."""
        def _bg_worker():
            try:
                self._rebuild_device_settings_cache_data(reload_disk=False)
                try:
                    self.root.after(50, lambda: self._ensure_device_settings_dialog(show=False))
                except Exception as exc:
                    logging.debug("Pre-warm UI schedule skipped: %s", exc)
            except Exception as exc:
                logging.debug("Pre-warm device settings cache failed: %s", exc)

        t = threading.Thread(target=_bg_worker, name="PrewarmDeviceSettings", daemon=True)
        t.start()

    def open_device_settings_dialog(self):
        """Open the full Device Settings Manager dialog to view, search, add, edit,
        or delete MPC / Device / Mask WXY / STDF Filename Convention configurations
        across all JSON files without manual file editing."""
        self._ensure_device_settings_dialog(show=True)

    def _ensure_device_settings_dialog(self, show: bool = True):
        """Create (or reuse) the pre-warmed Device Settings Manager window."""
        rebuilt_ds = False
        if not self._ds_cache_ready:
            self._rebuild_device_settings_cache_data(reload_disk=True)
            rebuilt_ds = True

        if getattr(self, "_device_settings_win", None) is not None:
            try:
                if self._device_settings_win.winfo_exists():
                    if show:
                        if rebuilt_ds and callable(getattr(self, "_device_settings_refresh_fn", None)):
                            self._device_settings_refresh_fn()
                        if callable(self._device_settings_sync_open_fn):
                            self._device_settings_sync_open_fn()
                        self._device_settings_win.deiconify()
                        self._device_settings_win.lift()
                        self._device_settings_win.focus_force()
                    return
            except Exception as exc:
                logging.debug("Existing device settings window check failed: %s", exc)

        win = tk.Toplevel(self.root)
        if not show:
            win.withdraw()
        self._device_settings_win = win
        win.title("Device Settings Manager — MPC / WXY / STDF Conventions")
        win.configure(bg=MAIN_BG)
        win.geometry("1160x660")
        win.minsize(980, 560)
        win.transient(self.root)
        try:
            if os.path.isfile(icon_path):
                win.iconbitmap(default=icon_path)
        except Exception as exc:
            logging.debug("Could not set icon on Device Settings dialog: %s", exc)

        win.columnconfigure(0, weight=54)
        win.columnconfigure(1, weight=46)
        win.rowconfigure(0, weight=1)

        # ── Left: Searchable Table of Existing Devices & MPCs ────────────────
        left_card = ModernCard(win, text="Configured Devices & MPCs", padx=10, pady=8)
        left_card.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        left_card.columnconfigure(0, weight=1)
        left_card.rowconfigure(1, weight=1)

        search_row = tk.Frame(left_card, bg=CARD_BG)
        search_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        search_row.columnconfigure(1, weight=1)

        tk.Label(search_row, text="Search:", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        search_var = tk.StringVar()
        search_entry = ModernEntry(
            search_row,
            textvariable=search_var,
            placeholder="e.g. SG4064RTBA10, SG406, PIC32CX...",
        )
        search_entry.grid(row=0, column=1, sticky="ew")

        count_badge_var = tk.StringVar(value="0 MPCs")
        tk.Label(
            search_row,
            textvariable=count_badge_var,
            font=FONT_BADGE,
            bg=BADGE_ACTIVE_BG,
            fg=BADGE_ACTIVE_FG,
            padx=6,
            pady=2,
        ).grid(row=0, column=2, sticky="e", padx=(8, 0))

        tree_frame = tk.Frame(left_card, bg=CARD_BG)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        cols = ("mpc", "mask", "device", "tester", "wxy")
        col_headers = {
            "mpc": "MPC",
            "mask": "Mask",
            "device": "Device Folder(s)",
            "tester": "Tester",
            "wxy": "WXY Tests (W/X/Y)",
        }
        col_anchors = {
            "mpc": "w",
            "mask": "center",
            "device": "w",
            "tester": "center",
            "wxy": "center",
        }
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        for cid in cols:
            tree.heading(cid, text=col_headers[cid])
            tree.column(cid, width=100, minwidth=60, anchor=col_anchors[cid], stretch=False)

        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        # Smooth 6-rows-per-notch vertical scrolling + Shift+Wheel horizontal scrolling
        def _on_tree_mousewheel(event):
            try:
                steps = int(-6 * (event.delta / 120)) if event.delta else 0
                if steps != 0:
                    tree.yview_scroll(steps, "units")
                    return "break"
            except Exception as exc:
                logging.debug("Treeview mousewheel scroll error: %s", exc)
            return None

        def _on_tree_shift_mousewheel(event):
            try:
                steps = int(-6 * (event.delta / 120)) if event.delta else 0
                if steps != 0:
                    tree.xview_scroll(steps, "units")
                    return "break"
            except Exception as exc:
                logging.debug("Treeview horizontal scroll error: %s", exc)
            return None

        tree.bind("<MouseWheel>", _on_tree_mousewheel)
        tree.bind("<Shift-MouseWheel>", _on_tree_shift_mousewheel)

        # ── Right: Add / Edit Device Form ────────────────────────────────────
        right_card = ModernCard(win, text="Add / Edit Device Configuration", padx=12, pady=10)
        right_card.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        right_card.columnconfigure(0, weight=1)

        form_mpc_var = tk.StringVar(value="")
        form_mask_var = tk.StringVar(value="—")
        form_device_var = tk.StringVar(value="")
        form_v93k_var = tk.BooleanVar(value=False)
        form_ltx_var = tk.BooleanVar(value=False)
        form_wafer_var = tk.StringVar(value="")
        form_x_var = tk.StringVar(value="")
        form_y_var = tk.StringVar(value="")
        form_uid_var = tk.StringVar(value="")
        form_bt_var = tk.StringVar(value="")
        form_zb_var = tk.StringVar(value="")
        form_macid_prefix_var = tk.StringVar(value="")
        form_conv_mode_var = tk.StringVar(value="template")
        form_fp_conv_var = tk.StringVar(value="")
        form_rt_conv_var = tk.StringVar(value="")
        form_qc_conv_var = tk.StringVar(value="")
        form_qcrt_conv_var = tk.StringVar(value="")
        status_msg_var = tk.StringVar(value="Select a row on the left to edit, or enter a new 12-character MPC.")

        # Row 0: MPC & Auto Mask
        r0 = tk.Frame(right_card, bg=CARD_BG)
        r0.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        r0.columnconfigure(0, weight=1)
        tk.Label(r0, text="MPC (12 characters)", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(r0, text="Mask (5 chars)", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )
        mpc_input = ModernEntry(r0, textvariable=form_mpc_var, placeholder="e.g. SG4064RTBA10")
        mpc_input.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        mask_badge = tk.Label(
            r0,
            textvariable=form_mask_var,
            font=("Segoe UI", 10, "bold"),
            bg=BADGE_ACTIVE_BG,
            fg=BADGE_ACTIVE_FG,
            width=8,
            padx=8,
            pady=3,
        )
        mask_badge.grid(row=1, column=1, sticky="e", padx=(10, 0), pady=(2, 0))

        # Row 1: Device Folder Pattern
        r1 = tk.Frame(right_card, bg=CARD_BG)
        r1.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        r1.columnconfigure(0, weight=1)
        tk.Label(
            r1,
            text="Device Folder Pattern(s)",
            font=FONT_LABEL,
            bg=CARD_BG,
            fg=TEXT_FG,
        ).grid(row=0, column=0, sticky="w")
        dev_input = ModernEntry(
            r1,
            textvariable=form_device_var,
            placeholder="e.g. PIC32CX5109BZ* or ATA5830*",
        )
        dev_input.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        tk.Label(
            r1,
            text="Tip: Use '*' wildcard for suffix matching; comma-separate if multiple folders.",
            font=FONT_SUBTITLE,
            bg=CARD_BG,
            fg=TEXT_MUTED,
        ).grid(row=2, column=0, sticky="w", pady=(1, 0))

        # Row 2: Tester Platform + multi-select note
        r2 = tk.Frame(right_card, bg=CARD_BG)
        r2.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        r2_top = tk.Frame(r2, bg=CARD_BG)
        r2_top.pack(side="top", fill="x")
        tk.Label(r2_top, text="Tester Platform:", font=FONT_LABEL, bg=CARD_BG, fg=TEXT_FG).pack(
            side="left", padx=(0, 12)
        )
        ttk.Checkbutton(r2_top, text="V93K", variable=form_v93k_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(r2_top, text="LTX", variable=form_ltx_var).pack(side="left")
        tk.Label(
            r2,
            text="Note: Multi-select is supported for devices that can be processed on different testers.",
            font=FONT_SUBTITLE,
            bg=CARD_BG,
            fg=TEXT_MUTED,
        ).pack(side="top", anchor="w", pady=(2, 0))

        # Row 3: Automated WXY Test Numbers (Mask-level)
        r3 = tk.Frame(right_card, bg=CARD_BG)
        r3.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        for c in range(3):
            r3.columnconfigure(c, weight=1)
        tk.Label(
            r3,
            text="Automated WXY Test Numbers for Mask (optional — updates mask_wxy_map.json):",
            font=FONT_LABEL,
            bg=CARD_BG,
            fg=TEXT_FG,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 2))
        tk.Label(r3, text="Wafer Test #", font=FONT_SUBTITLE, bg=CARD_BG, fg=TEXT_MUTED).grid(
            row=1, column=0, sticky="w"
        )
        tk.Label(r3, text="X Test #", font=FONT_SUBTITLE, bg=CARD_BG, fg=TEXT_MUTED).grid(
            row=1, column=1, sticky="w", padx=6
        )
        tk.Label(r3, text="Y Test #", font=FONT_SUBTITLE, bg=CARD_BG, fg=TEXT_MUTED).grid(
            row=1, column=2, sticky="w"
        )
        ModernEntry(r3, textvariable=form_wafer_var, placeholder="e.g. 93005").grid(
            row=2, column=0, sticky="ew", padx=(0, 4)
        )
        ModernEntry(r3, textvariable=form_x_var, placeholder="e.g. 93006").grid(
            row=2, column=1, sticky="ew", padx=4
        )
        ModernEntry(r3, textvariable=form_y_var, placeholder="e.g. 93007").grid(
            row=2, column=2, sticky="ew", padx=(4, 0)
        )

        # Row 3b: Multi-Parameter Test Numbers (UID / BT / ZB / MACID Prefix) — 2 rows x 2 columns
        r3b = tk.Frame(right_card, bg=CARD_BG)
        r3b.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        r3b.columnconfigure(1, weight=1, uniform="macid_cols")
        r3b.columnconfigure(3, weight=1, uniform="macid_cols")
        tk.Label(
            r3b,
            text="Multi-Parameter / MACID Test Numbers (optional — updates mask_wxy_map.json):",
            font=FONT_LABEL,
            bg=CARD_BG,
            fg=TEXT_FG,
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 2))

        macid_fields = [
            (1, 0, "UID Test #(s):", form_uid_var, "e.g. 93009"),
            (1, 2, "BT MACID #s:", form_bt_var, "e.g. 94016, 94017, ..."),
            (2, 0, "ZB MACID #s:", form_zb_var, "e.g. 94022, 94023, ..."),
            (2, 2, "MACID Prefix:", form_macid_prefix_var, "Leave blank to disable (e.g. 40:84)"),
        ]
        for r_idx, c_base, lbl_txt, var, ph_txt in macid_fields:
            left_pad = (8, 4) if c_base == 2 else (0, 4)
            tk.Label(r3b, text=lbl_txt, font=FONT_SUBTITLE, bg=CARD_BG, fg=TEXT_MUTED).grid(
                row=r_idx, column=c_base, sticky="w", padx=left_pad, pady=2
            )
            ModernEntry(r3b, textvariable=var, placeholder=ph_txt).grid(
                row=r_idx, column=c_base + 1, sticky="ew", pady=2
            )

        # Row 5: STDF Filename Convention (Mask-level) — 2 rows x 2 columns
        r4 = tk.Frame(right_card, bg=CARD_BG)
        r4.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        r4.columnconfigure(1, weight=1, uniform="conv_cols")
        r4.columnconfigure(3, weight=1, uniform="conv_cols")
        tk.Label(
            r4,
            text="STDF Filename Convention (updates stdf_filename_conventions.json):",
            font=FONT_LABEL,
            bg=CARD_BG,
            fg=TEXT_FG,
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 2))

        mode_row = tk.Frame(r4, bg=CARD_BG)
        mode_row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 4))

        custom_entries: List[ModernEntry] = []
        last_conv_state = [None]

        def _sync_conv_mode(*_args):
            is_custom = (form_conv_mode_var.get() == "custom")
            state = "normal" if is_custom else "disabled"
            if last_conv_state[0] == state:
                return
            last_conv_state[0] = state
            for ent in custom_entries:
                try:
                    ent.configure(state=state)
                except Exception as exc:
                    logging.debug("Failed to toggle convention entry state: %s", exc)

        ttk.Radiobutton(
            mode_row,
            text="Use Platform Template (V93K / LTX)",
            value="template",
            variable=form_conv_mode_var,
            command=_sync_conv_mode,
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(
            mode_row,
            text="Custom Patterns",
            value="custom",
            variable=form_conv_mode_var,
            command=_sync_conv_mode,
        ).pack(side="left")

        conv_fields = [
            (2, 0, "FIRST PASS:", form_fp_conv_var, "e.g. {lot}_e_{timestamp}"),
            (2, 2, "RETEST:", form_rt_conv_var, "e.g. {lot}_w_rj{N}_{timestamp}"),
            (3, 0, "QC:", form_qc_conv_var, "e.g. {lot}_q_{timestamp}"),
            (3, 2, "QC RETEST:", form_qcrt_conv_var, "e.g. {lot}_q_rj{N}_{timestamp}"),
        ]
        for r_idx, c_base, lbl_txt, var, ph_txt in conv_fields:
            left_pad = (8, 4) if c_base == 2 else (0, 4)
            tk.Label(r4, text=lbl_txt, font=FONT_SUBTITLE, bg=CARD_BG, fg=TEXT_MUTED).grid(
                row=r_idx, column=c_base, sticky="w", padx=left_pad, pady=2
            )
            ent = ModernEntry(r4, textvariable=var, placeholder=ph_txt)
            ent.grid(row=r_idx, column=c_base + 1, sticky="ew", pady=2)
            custom_entries.append(ent)
        _sync_conv_mode()

        # Status message label
        status_lbl = tk.Label(
            right_card,
            textvariable=status_msg_var,
            font=FONT_SUBTITLE,
            bg=CARD_BG,
            fg=TEXT_MUTED,
            wraplength=420,
            justify="left",
        )
        status_lbl.grid(row=6, column=0, sticky="w", pady=(2, 6))

        # Row 7: Action Buttons (Save / Clear / Delete)
        btn_row = tk.Frame(right_card, bg=CARD_BG)
        btn_row.grid(row=7, column=0, sticky="ew", pady=(4, 0))
        btn_row.columnconfigure(0, weight=2)
        btn_row.columnconfigure(1, weight=1)
        btn_row.columnconfigure(2, weight=1)

        suppress_mpc_trace = [False]
        search_after_id: List[Optional[str]] = [None]
        chunk_after_id: List[Optional[str]] = [None]
        select_after_id: List[Optional[str]] = [None]

        cell_font = tkfont.Font(font=("Segoe UI", 9))
        hdr_font = tkfont.Font(font=("Segoe UI", 9, "bold"))

        def _apply_column_widths():
            """Measure only the single longest string per column (5 calls total instead of 17,275)."""
            with self._ds_cache_lock:
                max_strs = dict(self._ds_max_len_str)
            for cid in cols:
                hdr_w = hdr_font.measure(col_headers[cid]) + 28
                val_w = cell_font.measure(max_strs.get(cid, "")) + 28
                w_px = max(hdr_w, val_w, 64)
                tree.column(cid, width=w_px, minwidth=w_px, stretch=False)

        def _cancel_chunk_stream():
            if chunk_after_id[0] is not None:
                try:
                    win.after_cancel(chunk_after_id[0])
                except Exception as exc:
                    logging.debug("Failed to cancel chunk render timer: %s", exc)
                chunk_after_id[0] = None

        def _render_filtered_rows():
            """Render matching rows non-blockingly: insert the first 250 rows immediately (<5ms)
            so the visible viewport updates right away, then stream remaining rows in idle slices."""
            search_after_id[0] = None
            _cancel_chunk_stream()
            q = search_var.get().strip().upper()
            children = tree.get_children()
            if children:
                tree.delete(*children)

            with self._ds_cache_lock:
                rows_snapshot = list(self._ds_cached_rows)

            if not q:
                matched = [vals for (_h, vals, _m) in rows_snapshot]
            else:
                matched = [vals for (haystack, vals, _m) in rows_snapshot if q in haystack]

            total_matched = len(matched)
            total_all = len(rows_snapshot)
            count_badge_var.set(f"{total_matched} / {total_all} MPCs")

            # Insert first visible batch synchronously so viewport is never blank
            initial_batch = 250
            for vals in matched[:initial_batch]:
                tree.insert("", "end", iid=vals[0], values=vals)

            if total_matched > initial_batch:
                state_idx = [initial_batch]

                def _stream_next_chunk():
                    chunk_after_id[0] = None
                    start = state_idx[0]
                    end = min(start + 350, total_matched)
                    for vals in matched[start:end]:
                        if not tree.exists(vals[0]):
                            tree.insert("", "end", iid=vals[0], values=vals)
                    state_idx[0] = end
                    if end < total_matched:
                        chunk_after_id[0] = win.after(8, _stream_next_chunk)

                chunk_after_id[0] = win.after(8, _stream_next_chunk)

        def _on_search_keystroke(*_args):
            if search_after_id[0] is not None:
                try:
                    win.after_cancel(search_after_id[0])
                except Exception as exc:
                    logging.debug("Failed to cancel search debounce timer: %s", exc)
                search_after_id[0] = None
            # 250ms debounce delay for smooth typing response
            search_after_id[0] = win.after(250, _render_filtered_rows)

        search_var.trace_add("write", _on_search_keystroke)
        search_entry.bind("<Return>", lambda _e: _render_filtered_rows())

        def _on_mpc_text_change(*_args):
            if suppress_mpc_trace[0]:
                return
            raw = form_mpc_var.get().strip().upper()
            mask_k = raw[:5] if len(raw) >= 5 else (raw or "—")
            form_mask_var.set(mask_k)

        form_mpc_var.trace_add("write", _on_mpc_text_change)

        def _populate_form_for_mpc(mpc_k: str):
            with self._ds_cache_lock:
                meta = self._ds_cached_by_mpc.get(mpc_k)
            if not meta:
                return
            suppress_mpc_trace[0] = True
            try:
                mask_k = str(meta["mask"])
                form_mpc_var.set(mpc_k)
                form_mask_var.set(mask_k)
                form_device_var.set(str(meta["device"]))
                t_list = meta["testers"]
                form_v93k_var.set("V93K" in t_list)
                form_ltx_var.set("LTX" in t_list)

                wxy = meta.get("wxy")
                if isinstance(wxy, dict):
                    form_wafer_var.set(str(wxy.get("wafer", "")))
                    form_x_var.set(str(wxy.get("x", "")))
                    form_y_var.set(str(wxy.get("y", "")))
                    raw_uid = wxy.get("uid")
                    form_uid_var.set(
                        ", ".join(str(v) for v in raw_uid)
                        if isinstance(raw_uid, (list, tuple))
                        else str(raw_uid or "")
                    )
                    raw_bt = wxy.get("bt")
                    form_bt_var.set(
                        ", ".join(str(v) for v in raw_bt)
                        if isinstance(raw_bt, (list, tuple))
                        else str(raw_bt or "")
                    )
                    raw_zb = wxy.get("zb")
                    form_zb_var.set(
                        ", ".join(str(v) for v in raw_zb)
                        if isinstance(raw_zb, (list, tuple))
                        else str(raw_zb or "")
                    )
                    raw_pfx = wxy.get("macid_prefix")
                    form_macid_prefix_var.set(
                        ", ".join(str(v) for v in raw_pfx)
                        if isinstance(raw_pfx, (list, tuple))
                        else str(raw_pfx or "")
                    )
                else:
                    form_wafer_var.set("")
                    form_x_var.set("")
                    form_y_var.set("")
                    form_uid_var.set("")
                    form_bt_var.set("")
                    form_zb_var.set("")
                    form_macid_prefix_var.set("")

                conv_mode = "template"
                fp_pat = rt_pat = qc_pat = qcrt_pat = ""
                try:
                    conv_doc = _load_stdf_filename_conventions_file() or {}
                    masks_map = conv_doc.get("masks") if isinstance(conv_doc, dict) else None
                    mask_convs = masks_map.get(mask_k) if isinstance(masks_map, dict) else None
                    if isinstance(mask_convs, dict):
                        selected_conv = None
                        for tst_key in list(t_list) + [
                            k for k in mask_convs.keys() if not str(k).startswith("_")
                        ]:
                            raw_c = mask_convs.get(tst_key)
                            if raw_c is None:
                                tst_upper = str(tst_key).strip().upper()
                                raw_c = next(
                                    (
                                        v for k, v in mask_convs.items()
                                        if str(k).strip().upper() == tst_upper
                                    ),
                                    None,
                                )
                            if isinstance(raw_c, dict):
                                selected_conv = raw_c
                                break
                        if selected_conv is not None:
                            conv_mode = "custom"
                            upper_conv = {str(k).strip().upper(): v for k, v in selected_conv.items()}

                            def _fmt_pat(cat_key: str) -> str:
                                raw_val = upper_conv.get(cat_key)
                                if isinstance(raw_val, (list, tuple)):
                                    return ", ".join(str(p).strip() for p in raw_val if str(p).strip())
                                return str(raw_val or "").strip()

                            fp_pat = _fmt_pat("FIRST PASS")
                            rt_pat = _fmt_pat("RETEST")
                            qc_pat = _fmt_pat("QC")
                            qcrt_pat = _fmt_pat("QC RETEST")
                except Exception as exc:
                    logging.debug("Failed to load STDF conventions for '%s': %s", mask_k, exc)

                form_conv_mode_var.set(conv_mode)
                form_fp_conv_var.set(fp_pat)
                form_rt_conv_var.set(rt_pat)
                form_qc_conv_var.set(qc_pat)
                form_qcrt_conv_var.set(qcrt_pat)
                _sync_conv_mode()
                status_msg_var.set(f"Loaded MPC '{mpc_k}' (Mask '{mask_k}'). Edit fields and click Save.")
            finally:
                suppress_mpc_trace[0] = False

        def _on_tree_select(_event=None):
            if select_after_id[0] is not None:
                try:
                    win.after_cancel(select_after_id[0])
                except Exception as exc:
                    logging.debug("Failed to cancel tree select debounce: %s", exc)
                select_after_id[0] = None

            def _do_select():
                select_after_id[0] = None
                sel = tree.selection()
                if sel:
                    _populate_form_for_mpc(sel[0])

            select_after_id[0] = win.after(20, _do_select)

        tree.bind("<<TreeviewSelect>>", _on_tree_select)

        def _clear_form():
            sel = tree.selection()
            if sel:
                tree.selection_remove(*sel)
            suppress_mpc_trace[0] = True
            try:
                form_mpc_var.set("")
                form_mask_var.set("—")
                form_device_var.set("")
                form_v93k_var.set(False)
                form_ltx_var.set(False)
                form_wafer_var.set("")
                form_x_var.set("")
                form_y_var.set("")
                form_uid_var.set("")
                form_bt_var.set("")
                form_zb_var.set("")
                form_macid_prefix_var.set("")
                form_conv_mode_var.set("template")
                form_fp_conv_var.set("")
                form_rt_conv_var.set("")
                form_qc_conv_var.set("")
                form_qcrt_conv_var.set("")
                _sync_conv_mode()
                status_msg_var.set("All fields cleared. Enter a 12-character MPC to configure a new device.")
            finally:
                suppress_mpc_trace[0] = False

        def _save_device():
            mpc_clean = form_mpc_var.get().strip().upper()
            dev_clean = form_device_var.get().strip()
            testers_sel: List[str] = []
            if form_v93k_var.get():
                testers_sel.append("V93K")
            if form_ltx_var.get():
                testers_sel.append("LTX")

            w_str = form_wafer_var.get().strip()
            x_str = form_x_var.get().strip()
            y_str = form_y_var.get().strip()
            uid_str = form_uid_var.get().strip()
            bt_str = form_bt_var.get().strip()
            zb_str = form_zb_var.get().strip()
            pfx_str = form_macid_prefix_var.get().strip()

            wxy_dict: Optional[Dict[str, Any]] = None
            if w_str or x_str or y_str or uid_str or bt_str or zb_str or pfx_str:
                if not (w_str and x_str and y_str):
                    messagebox.showerror(
                        "Invalid WXY Test Numbers",
                        "Please provide all three WXY test numbers (Wafer, X, and Y) or leave all three blank.",
                        parent=win,
                    )
                    return
                try:
                    wxy_dict = {"wafer": int(w_str), "x": int(x_str), "y": int(y_str)}
                except ValueError:
                    messagebox.showerror(
                        "Invalid WXY Test Numbers",
                        "Wafer, X, and Y test numbers must be integers.",
                        parent=win,
                    )
                    return

                if (bt_str or zb_str) and not uid_str:
                    messagebox.showerror(
                        "UID Test Number Required",
                        "Please provide UID Test #(s) when configuring BT or ZB MACID test numbers.\n\n"
                        "UID is required to verify 1 MACID = 1 UID (there is no default UID test number).",
                        parent=win,
                    )
                    return

                if uid_str:
                    uid_parts = [p.strip() for p in uid_str.replace(",", " ").split() if p.strip()]
                    try:
                        uid_ints = [int(p) for p in uid_parts]
                        wxy_dict["uid"] = uid_ints[0] if len(uid_ints) == 1 else uid_ints
                    except ValueError:
                        messagebox.showerror(
                            "Invalid UID Test Number(s)",
                            f"All UID test numbers must be integers (got '{uid_str}').",
                            parent=win,
                        )
                        return
                else:
                    wxy_dict["uid"] = []

                if bt_str:
                    bt_parts = [p.strip() for p in bt_str.replace(",", " ").split() if p.strip()]
                    try:
                        wxy_dict["bt"] = [int(p) for p in bt_parts]
                    except ValueError:
                        messagebox.showerror(
                            "Invalid BT MACID Test Numbers",
                            "All BT MACID test numbers must be integers.",
                            parent=win,
                        )
                        return
                else:
                    wxy_dict["bt"] = []

                if zb_str:
                    zb_parts = [p.strip() for p in zb_str.replace(",", " ").split() if p.strip()]
                    try:
                        wxy_dict["zb"] = [int(p) for p in zb_parts]
                    except ValueError:
                        messagebox.showerror(
                            "Invalid ZB MACID Test Numbers",
                            "All ZB MACID test numbers must be integers.",
                            parent=win,
                        )
                        return
                else:
                    wxy_dict["zb"] = []

                if pfx_str:
                    pfx_parts = [p.strip() for p in pfx_str.split(",") if p.strip()]
                    wxy_dict["macid_prefix"] = (
                        (pfx_parts[0] if len(pfx_parts) == 1 else pfx_parts) if pfx_parts else ""
                    )
                else:
                    wxy_dict["macid_prefix"] = ""
                wxy_dict["bt_prefix"] = ""
                wxy_dict["zb_prefix"] = ""

            custom_conv: Optional[Dict[str, object]] = None
            if form_conv_mode_var.get() == "custom":
                custom_conv = {
                    "FIRST PASS": [p.strip() for p in form_fp_conv_var.get().split(",") if p.strip()],
                    "RETEST": [p.strip() for p in form_rt_conv_var.get().split(",") if p.strip()],
                    "QC": [p.strip() for p in form_qc_conv_var.get().split(",") if p.strip()],
                    "QC RETEST": [p.strip() for p in form_qcrt_conv_var.get().split(",") if p.strip()],
                }

            try:
                saved = save_device_configuration(
                    mpc=mpc_clean,
                    devices=dev_clean,
                    testers=testers_sel,
                    wxy_tests=wxy_dict,
                    custom_conventions=custom_conv,
                )
            except Exception as exc:
                messagebox.showerror("Save Device Failed", str(exc), parent=win)
                return

            self._rebuild_device_settings_cache_data(reload_disk=False)
            _apply_column_widths()
            with self._ds_cache_lock:
                meta = self._ds_cached_by_mpc.get(mpc_clean)
                total_all = len(self._ds_cached_rows)
            if meta:
                wxy_info = meta.get("wxy")
                if wxy_info:
                    extras = [lbl for k, lbl in (("uid", "UID"), ("bt", "BT"), ("zb", "ZB")) if wxy_info.get(k)]
                    if extras:
                        wxy_str = f"{wxy_info.get('wafer')} / {wxy_info.get('x')} / {wxy_info.get('y')} (+{'/'.join(extras)})"
                    else:
                        wxy_str = f"{wxy_info.get('wafer')} / {wxy_info.get('x')} / {wxy_info.get('y')}"
                else:
                    wxy_str = "—"
                vals = (
                    mpc_clean,
                    str(meta["mask"]),
                    str(meta["device"]),
                    ", ".join(meta["testers"]),
                    wxy_str,
                )
                if tree.exists(mpc_clean):
                    tree.item(mpc_clean, values=vals)
                else:
                    tree.insert("", 0, iid=mpc_clean, values=vals)
                    count_badge_var.set(f"{len(tree.get_children())} / {total_all} MPCs")
                tree.selection_set(mpc_clean)
                tree.see(mpc_clean)

            # Sync the main FTDC Checker MPC field and WXY filter immediately
            if not self.mpc_var.get().strip() or self.mpc_var.get().strip().upper() == mpc_clean:
                self.mpc_var.set(mpc_clean)
                self._on_mpc_changed()

            wxy_note = f", WXY={saved['wxy']}" if saved.get("wxy") else ""
            self.log(
                f"Device Settings: Saved MPC '{saved['mpc']}' "
                f"(Mask '{saved['mask']}', Device={saved['device']}, Tester={saved['tester']}{wxy_note})."
            )
            status_msg_var.set(
                f"Saved MPC '{saved['mpc']}' (Mask '{saved['mask']}') across JSON configuration files."
            )
            messagebox.showinfo(
                "Device Saved",
                f"MPC '{saved['mpc']}' (Mask '{saved['mask']}') has been saved to:\n"
                f"  • assets/mpc_partnumber.json\n"
                f"  • assets/stdf_filename_conventions.json\n"
                + ("  • assets/mask_wxy_map.json\n" if saved.get("wxy") else "")
                + "\nChanges are active immediately.",
                parent=win,
            )

        def _delete_device():
            mpc_clean = form_mpc_var.get().strip().upper()
            if not mpc_clean:
                messagebox.showwarning("Delete MPC", "Select or enter an MPC to delete.", parent=win)
                return
            if not messagebox.askyesno(
                "Confirm Delete",
                f"Remove MPC '{mpc_clean}' from assets/mpc_partnumber.json?",
                parent=win,
            ):
                return
            try:
                removed = delete_mpc_configuration(mpc_clean)
            except Exception as exc:
                messagebox.showerror("Delete Failed", str(exc), parent=win)
                return
            if removed:
                self.log(f"Device Settings: Deleted MPC '{mpc_clean}' from mpc_partnumber.json.")
                self._rebuild_device_settings_cache_data(reload_disk=False)
                if tree.exists(mpc_clean):
                    tree.delete(mpc_clean)
                with self._ds_cache_lock:
                    total_all = len(self._ds_cached_rows)
                count_badge_var.set(f"{len(tree.get_children())} / {total_all} MPCs")
                _clear_form()
                status_msg_var.set(f"Deleted MPC '{mpc_clean}'.")
            else:
                messagebox.showinfo("Delete MPC", f"MPC '{mpc_clean}' was not found.", parent=win)

        save_btn = ModernAccentButton(
            btn_row,
            text="💾 Save / Update Device",
            command=_save_device,
            pady=10,
        )
        save_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        clear_btn = ModernHoverButton(
            btn_row,
            text="New / Clear",
            command=_clear_form,
            padx=8,
            pady=10,
        )
        clear_btn.grid(row=0, column=1, sticky="ew", padx=4)

        del_btn = ModernHoverButton(
            btn_row,
            text="🗑 Delete MPC",
            command=_delete_device,
            padx=8,
            pady=10,
        )
        del_btn.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        def _sync_on_open():
            """Sync the Right Panel to the main MPC entry when the user opens Device Settings."""
            current_mpc = self.mpc_var.get().strip().upper()
            with self._ds_cache_lock:
                is_configured = bool(current_mpc and current_mpc in self._ds_cached_by_mpc)
            if is_configured:
                if not tree.exists(current_mpc):
                    with self._ds_cache_lock:
                        meta = self._ds_cached_by_mpc.get(current_mpc)
                    if meta:
                        wxy_info = meta.get("wxy")
                        if wxy_info:
                            if "bt" in wxy_info or "uid" in wxy_info or "zb" in wxy_info:
                                wxy_str = f"{wxy_info.get('wafer')} / {wxy_info.get('x')} / {wxy_info.get('y')} (+UID/BT/ZB)"
                            else:
                                wxy_str = f"{wxy_info.get('wafer')} / {wxy_info.get('x')} / {wxy_info.get('y')}"
                        else:
                            wxy_str = "—"
                        vals = (
                            current_mpc,
                            str(meta["mask"]),
                            str(meta["device"]),
                            ", ".join(meta["testers"]),
                            wxy_str,
                        )
                        tree.insert("", 0, iid=current_mpc, values=vals)
                tree.selection_set(current_mpc)
                tree.see(current_mpc)
                _populate_form_for_mpc(current_mpc)
            else:
                # For unconfigured or blank MPC, keep all Right Panel fields blank
                _clear_form()
                mpc_input.focus_set()

        self._device_settings_sync_open_fn = _sync_on_open
        self._device_settings_refresh_fn = _render_filtered_rows

        def _on_dialog_close():
            """Hide instead of destroying so reopening Device Settings is instantaneous."""
            _cancel_chunk_stream()
            win.withdraw()

        win.protocol("WM_DELETE_WINDOW", _on_dialog_close)

        _apply_column_widths()
        _render_filtered_rows()
        if show:
            _sync_on_open()
            win.deiconify()
            win.lift()
            win.focus_force()




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
# Backward compatibility alias
STDFGuidCheckerApp = FTDCCheckerFrame


def main():
    root = tk.Tk()
    setup_theme(root)
    root.title(f"Automatic FTDC Checker {APP_VERSION}")
    try:
        root.iconbitmap(default=icon_path)
    except Exception as exc:
        logging.warning("Failed to set window icon '%s': %s", icon_path, exc)
    root.geometry("1260x820")
    root.minsize(1060, 680)
    app = FTDCCheckerFrame(root)
    app.pack(fill="both", expand=True)

    def _on_close():
        try:
            app.save_session_log_on_close()
        except Exception as exc:
            logging.warning("Failed to save session log on close: %s", exc)
        try:
            root.destroy()
        except Exception as exc:
            logging.warning("Failed to destroy root window on close: %s", exc)

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
