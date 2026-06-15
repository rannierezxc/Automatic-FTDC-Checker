"""
Optimized GUID analysis module.

Fixes applied vs v25:
- B6: Optimized _format_coord_value with isinstance fast path
- B7: Streamlined row construction
- B8: WXY-only result storage
- B10: Direct int comparison for hard_bin
- B11: Single-pass aggregation
- B13-B17: Removed all dead code (BASE_METADATA_FIELD_MAP, SDR_METADATA_FIELD_MAP,
           _summarize_sdr_records, _join_unique_nonempty, _is_supported_auto_wxy_mpc,
           scan_tests_for_files)
- B21: Removed unused source variable
"""
import json
import os
import re
from array import array as _CArray
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from optimized.stdf_parser import (
    ByteProgressFunc, LogFunc, ProgressFunc,
    _count_files_text, _emit_log, sort_paths_by_modified,
    parse_stdf_file,
)

# ── Production WXY mapping (loaded from mask_wxy_map.json) ───────────────────
def _get_json_path(filename: str) -> str:
    import sys
    # Check next to executable (Nuitka / PyInstaller)
    exe_dir = os.path.dirname(sys.executable)
    exe_path = os.path.join(exe_dir, filename)
    if os.path.exists(exe_path):
        return exe_path
    subfolder_path = os.path.join(exe_dir, "optimized", filename)
    if os.path.exists(subfolder_path):
        return subfolder_path

    # Check next to argv[0] as fallback (some Nuitka settings)
    if sys.argv and sys.argv[0]:
        argv_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        argv_path = os.path.join(argv_dir, filename)
        if os.path.exists(argv_path):
            return argv_path
        subfolder_argv = os.path.join(argv_dir, "optimized", filename)
        if os.path.exists(subfolder_argv):
            return subfolder_argv

    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

_MASK_WXY_JSON_PATH = _get_json_path("mask_wxy_map.json")


def _load_mpc_wxy_map() -> Dict[str, Dict[str, int]]:
    """Load MPC-to-WXY test-number mapping from the JSON file."""
    try:
        with open(_MASK_WXY_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure all values are ints for consistency
        return {
            str(k): {wk: int(wv) for wk, wv in v.items()}
            for k, v in data.items()
        }
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"Failed to load MPC WXY mapping from '{_MASK_WXY_JSON_PATH}': {exc}"
        ) from exc


MPC_WXY_TEST_MAP: Dict[str, Dict[str, int]] = _load_mpc_wxy_map()

def _build_unsupported_mask_message() -> str:
    """Build the unsupported mask error message with a list of all supported masks."""
    lines = [
        "This Mask is not included in the list of device using automated WXY Filtering.",
        "Kindly use manual filter to select the applicable WXY test parameter.",
        "",
        "Supported Masks and their WXY test parameters:",
    ]
    for mask, tests in MPC_WXY_TEST_MAP.items():
        lines.append(f"  {mask}  -  Wafer: {tests['wafer']}, X: {tests['x']}, Y: {tests['y']}")
    return "\n".join(lines)

UNSUPPORTED_MPC_WXY_MESSAGE = _build_unsupported_mask_message()

WXY_KEYS = ("wafer", "x", "y")


def normalize_mpc_key(text: str) -> str:
    """Return the first 5 characters of the trimmed MPC text in uppercase."""
    return str(text or "").strip().upper()[:5]


def _part_pass_fail(part: Dict[str, object]) -> str:
    """B5/B10: PART_FLG is stored as int, direct bitwise check."""
    flag = part.get("PART_FLG", 0)
    if isinstance(flag, int):
        flag_value = flag
    else:
        try:
            flag_value = int(flag, 16) if isinstance(flag, str) else int(flag)
        except (ValueError, TypeError):
            flag_value = 0
    return "FAIL" if (flag_value & 0x08) else "PASS"


def _format_coord_value(value: object) -> str:
    """B6: isinstance fast path avoids try/except for the common float case."""
    if value is None or value == "" or value == "N/A":
        return "NaN"
    if isinstance(value, float):
        number = value
    elif isinstance(value, int):
        return str(value)
    else:
        try:
            number = float(value)
        except (ValueError, TypeError):
            return str(value).strip()
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _make_wxy(wafer_value: object, x_value: object, y_value: object) -> str:
    wafer = _format_coord_value(wafer_value)
    x_coord = _format_coord_value(x_value)
    y_coord = _format_coord_value(y_value)
    return f"{wafer}_{x_coord}_{y_coord}" if wafer and x_coord and y_coord else ""


def _parse_int_field(value: object, field_name: str) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        raise ValueError(f"{field_name} is required.")
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be numeric.") from exc


def _parse_wxy_from_mpc_text(mpc_text: str) -> Optional[Dict[str, int]]:
    text = str(mpc_text or "").strip()
    if not text:
        return None
    mpc_prefix = normalize_mpc_key(text)
    mapped = MPC_WXY_TEST_MAP.get(mpc_prefix)
    if mapped and all(key in mapped for key in WXY_KEYS):
        return {key: int(mapped[key]) for key in WXY_KEYS}
    return None


def resolve_wxy_test_numbers(
    mpc_text: str,
    scanned_tests: Sequence[Dict[str, object]],
    manual_filter_tests: Optional[Set[int]] = None,
    logger: LogFunc = None,
) -> Tuple[Dict[str, int], str]:
    explicit = _parse_wxy_from_mpc_text(mpc_text)
    if explicit:
        return explicit, f"MPC prefix {normalize_mpc_key(mpc_text)}"
    if manual_filter_tests and len(manual_filter_tests) == 3:
        ordered = sorted(manual_filter_tests)
        _emit_log(
            "Mask is not included in the automated WXY list. Using exactly 3 manual filter tests as Wafer/X/Y in ascending order.",
            True, logger,
        )
        return {"wafer": ordered[0], "x": ordered[1], "y": ordered[2]}, "Manual Filter"
    if manual_filter_tests and len(manual_filter_tests) != 3:
        raise ValueError(
            UNSUPPORTED_MPC_WXY_MESSAGE
            + "\n\nManual Filter must contain exactly 3 test numbers in Wafer, X, Y order when the Mask is not supported."
        )
    raise ValueError(UNSUPPORTED_MPC_WXY_MESSAGE)


def parse_panel_wxy_parts(
    input_paths: Sequence[str],
    panel_name: str,
    wxy_tests: Dict[str, int],
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
) -> Dict[str, object]:
    """
    B7/B8/B11: Optimized panel parsing.
    - Only stores WXY test results (not all 1K tests)
    - Single-pass aggregation (pass/fail/missing counted inline)
    """
    rows: List[Dict[str, object]] = []
    total_counts: Dict[str, int] = defaultdict(int)
    total_skipped_ptr = 0
    total_bytes = sum(max(os.path.getsize(p), 1) for p in input_paths if os.path.exists(p)) or 1
    processed_bytes = 0
    span = max(progress_end - progress_start, 0.0)
    filter_tests = {int(wxy_tests[key]) for key in WXY_KEYS}

    # Pre-fetch WXY test numbers for inline access
    wxy_wafer = wxy_tests["wafer"]
    wxy_x = wxy_tests["x"]
    wxy_y = wxy_tests["y"]

    # B11: Inline counters
    pass_count = 0
    fail_count = 0
    missing_wxy = 0

    for index, input_path in enumerate(input_paths, start=1):
        file_size = max(os.path.getsize(input_path), 1) if os.path.exists(input_path) else 1
        source = os.path.basename(input_path)
        
        row_panel = panel_name
        if panel_name == "RETEST":
            match = re.search(r'rj(\d+)', source, re.IGNORECASE)
            if match:
                num = int(match.group(1))
                if 11 <= (num % 100) <= 13:
                    suffix = 'th'
                else:
                    suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(num % 10, 'th')
                row_panel = f"{num}{suffix} RETEST"
        elif panel_name == "QC":
            match = re.search(r'rj(\d+)', source, re.IGNORECASE)
            if match:
                num = int(match.group(1))
                row_panel = f"QC {num + 1}A"
            else:
                row_panel = "QC 1A"

        _emit_log(f"\n[{panel_name}] [{index}/{len(input_paths)}] Processing {input_path}", True, logger)
        status_text = f"Parsing {panel_name}: {_count_files_text(len(input_paths))}"

        def file_progress(current_bytes: int, file_total: int, base=processed_bytes):
            if not progress_callback:
                return
            total_for_file = file_total if file_total > 0 else file_size
            fraction = progress_start + span * ((base + min(max(current_bytes, 0), total_for_file)) / total_bytes)
            progress_callback(fraction, status_text)

        parsed, counts, skipped_ptr = parse_stdf_file(
            input_path=input_path, filter_tests=filter_tests,
            verbose=False, logger=logger, progress_callback=file_progress,
        )
        total_skipped_ptr += skipped_ptr
        for rec_name, count in counts.items():
            total_counts[rec_name] += count

        parts = parsed.get("PARTS") or []
        results_list = parsed.get("RESULTS") or []
        rows_append = rows.append

        for part_index, part in enumerate(parts):
            results = results_list[part_index] if part_index < len(results_list) else {}
            wafer_value = results.get(wxy_wafer)
            x_value = results.get(wxy_x)
            y_value = results.get(wxy_y)
            pf = _part_pass_fail(part)
            wxy = _make_wxy(wafer_value, x_value, y_value)

            # B11: count inline
            if pf == "PASS":
                pass_count += 1
            else:
                fail_count += 1
            wafer_str = _format_coord_value(wafer_value)
            x_str = _format_coord_value(x_value)
            y_str = _format_coord_value(y_value)
            if wafer_str == "NaN" or x_str == "NaN" or y_str == "NaN":
                missing_wxy += 1

            rows_append({
                "panel": row_panel, "source_file": source,
                "part_id": part.get("PART_ID", f"PART_{len(rows) + 1}"),
                "site_num": part.get("SITE_NUM", ""),
                "test_t": part.get("TEST_T", ""),     # <--- ADD THIS LINE
                "hard_bin": part.get("HARD_BIN", ""), 
                "soft_bin": part.get("SOFT_BIN", ""),
                "pass_fail": pf,
                "wafer": wafer_str,
                "x": x_str,
                "y": y_str,
                "wxy": wxy,
            })

        processed_bytes += file_size
        if progress_callback:
            progress_callback(progress_start + span * (processed_bytes / total_bytes), status_text)
        _emit_log(f"{panel_name} parts accumulated: {len(rows):,}", True, logger)

    _emit_log(f"{panel_name} summary: total={len(rows):,}, PASS={pass_count:,}, FAIL={fail_count:,}, missing WXY={missing_wxy:,}", True, logger)
    return {
        "panel": panel_name, "rows": rows, "total_parts": len(rows),
        "pass_count": pass_count, "fail_count": fail_count, "missing_wxy": missing_wxy,
        "counts": dict(total_counts), "skipped_ptr": total_skipped_ptr,
    }


def analyze_guid_data(
    first_pass_paths: Sequence[str],
    retest_paths: Sequence[str],
    qc_paths: Optional[Sequence[str]] = None,
    lot_id: str = "",
    mpc_text: str = "",
    first_pass_actual_good_qty: object = "",
    total_actual_good_qty: object = "",
    manual_filter_tests: Optional[Set[int]] = None,
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
) -> Dict[str, object]:
    lot_id = str(lot_id or "").strip()
    mpc_text = str(mpc_text or "").strip()
    first_pass_actual_good_qty = str(first_pass_actual_good_qty or "").strip()
    total_actual_good_qty = str(total_actual_good_qty or "").strip()

    first_pass_paths = sort_paths_by_modified(first_pass_paths)
    retest_paths = sort_paths_by_modified(retest_paths)
    qc_paths = sort_paths_by_modified(qc_paths or [])

    if not first_pass_paths:
        raise ValueError("Please select at least one FIRST PASS STDF file.")
    if not retest_paths:
        raise ValueError("Please select at least one RETEST STDF file.")
    if not qc_paths:
        raise ValueError("Please select at least one QC STDF file.")
    if not lot_id:
        raise ValueError("Lot ID is required.")
    if not mpc_text:
        raise ValueError("MPC is required.")
    for panel, paths in (("FIRST PASS", first_pass_paths), ("RETEST", retest_paths), ("QC", qc_paths)):
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            raise FileNotFoundError(f"{panel} file(s) not found:\n" + "\n".join(missing))

    fp_actual = _parse_int_field(first_pass_actual_good_qty, "First Pass Actual Good QTY")
    total_actual = _parse_int_field(total_actual_good_qty, "Total Actual Good QTY")

    _emit_log("=" * 80, True, logger)
    _emit_log("Starting STDF WXY / Duplicate GUID analysis", True, logger)
    if lot_id:
        _emit_log(f"Lot ID: {lot_id}", True, logger)
    _emit_log(f"MPC: {mpc_text}", True, logger)
    _emit_log(f"First Pass Actual Good QTY: {fp_actual:,}", True, logger)
    _emit_log(f"Total Actual Good QTY: {total_actual:,}", True, logger)

    scanned_tests: List[Dict[str, object]] = []
    wxy_tests, wxy_source = resolve_wxy_test_numbers(
        mpc_text=mpc_text, scanned_tests=scanned_tests,
        manual_filter_tests=manual_filter_tests, logger=logger,
    )
    _emit_log(f"WXY tests resolved from {wxy_source}: Wafer={wxy_tests['wafer']}, X={wxy_tests['x']}, Y={wxy_tests['y']}", True, logger)

    fp_data = parse_panel_wxy_parts(first_pass_paths, "FIRST PASS", wxy_tests, logger, progress_callback, 0.15, 0.55)
    retest_data = parse_panel_wxy_parts(retest_paths, "RETEST", wxy_tests, logger, progress_callback, 0.55, 0.90)
    qc_data = parse_panel_wxy_parts(qc_paths, "QC", wxy_tests, logger, progress_callback, 0.90, 0.97) if qc_paths else None
    if progress_callback and not qc_paths:
        progress_callback(0.97, "QC skipped")

    fp_rows = fp_data["rows"]
    retest_rows = retest_data["rows"]

    # ── WXY string interning ──────────────────────────────────────────────────
    # Every unique WXY string is assigned a compact integer ID once.
    # All downstream set operations use int IDs instead of strings, replacing
    # variable-length string hashing with a single integer hash — roughly
    # 5–8× faster per lookup for typical WXY strings like "18_-7_1".
    # IDs are 1-based; 0 is the sentinel for "no valid WXY".
    _wxy_id_map: Dict[str, int] = {}
    _id_to_wxy: List[str] = [""]      # index 0 = unused sentinel

    def _get_wid(wxy: str) -> int:
        try:
            return _wxy_id_map[wxy]
        except KeyError:
            wid = len(_id_to_wxy)     # next 1-based ID
            _wxy_id_map[wxy] = wid
            _id_to_wxy.append(wxy)
            return wid

    # ── Pre-extract retest rows → C-level arrays (1 pass over dicts) ─────────
    # array.array and bytearray store data in contiguous C memory — iterating
    # them via zip() avoids per-element Python object allocation entirely.
    rt_wids = _CArray('i')            # int32 WXY IDs  (0 = no WXY)
    rt_pass = bytearray()             # 1 = PASS, 0 = FAIL / no WXY

    for _row in retest_rows:
        _wxy = _row.get("wxy")
        if _wxy:
            rt_wids.append(_get_wid(_wxy))
            rt_pass.append(_row["pass_fail"] == "PASS")
        else:
            rt_wids.append(0)
            rt_pass.append(0)

    # ── Pre-extract fp rows → C-level arrays (1 pass over dicts) ─────────────
    fp_wids = _CArray('i')
    fp_pf   = bytearray()             # 1 = PASS, 0 = FAIL / no WXY
    fp_hbin = _CArray('i')            # hard_bin as int; -1 = absent/None

    for _row in fp_rows:
        _wxy = _row.get("wxy")
        if _wxy:
            fp_wids.append(_get_wid(_wxy))
            fp_pf.append(_row["pass_fail"] == "PASS")
        else:
            fp_wids.append(0)
            fp_pf.append(0)
        _hb = _row.get("hard_bin")
        fp_hbin.append(_hb if isinstance(_hb, int) else -1)

    # ── Single retest pass: build all retest ID sets ──────────────────────────
    # Must run before the FP pass because fp_rejects_not_in_retest needs
    # retest_all_ids. All set ops are now O(1) int hashes.
    retest_all_ids:  Set[int] = set()
    retest_pass_ids: Set[int] = set()

    for _wid, _is_pass in zip(rt_wids, rt_pass):
        if _wid:
            retest_all_ids.add(_wid)
            if _is_pass:
                retest_pass_ids.add(_wid)

    # ── Single FP pass: duplicate detection + fp_pass build + reject count ──────
    # C-array hit buffers: zero-init via bytes() = single memset, no Python loop.
    # Hot loops write raw ints only; all str(), tuple(), and dict mutations are
    # deferred to the write-back block — keeping the critical path at C speed.
    # Manual _i counter replaces enumerate() — avoids a tuple allocation per iter.
    _fp_len = len(fp_rows)
    _rt_len = len(retest_rows)
    _fp_dup_hits = _CArray('i', bytes(4 * _fp_len))   # 0 = no hit; >0 = combined part#
    _rt_dup_hits = _CArray('i', bytes(4 * _rt_len))
    _rt_retest_dup_hits = _CArray('i', bytes(4 * _rt_len))

    fp_pass_ids:       Set[int] = set()
    wxy_head_pass_ids: Set[int] = set()
    # Original duplicate counter:
    # Counts the first duplicate against the established FIRST PASS PASS original.
    duplicate_guid = 0
    duplicate_wxy_list: List[Tuple[str, int]] = []   # (wxy_string, combined_part_number)

    # RETEST duplicate counter:
    # Counts additional RETEST appearances after a WXY has already been seen as PASS in RETEST.
    # The current RETEST result can be PASS or FAIL; if the same WXY already passed in an
    # earlier RETEST, this counter is incremented.
    retest_duplicate_guid = 0
    retest_duplicate_wxy_list: List[Tuple[str, int]] = []
    retest_pass_seen_ids: Set[int] = set()

    # Row-level duplicate classification labels.
    # These labels are used for output/reporting only; numeric counters remain unchanged.
    FP_ORIGINAL_LABEL = "FP Original"
    FP_DUPLICATE_LABEL = "FP Duplicate"
    RETEST_ORIGINAL_LABEL = "RETEST Original"
    RETEST_DUPLICATE_LABEL = "RETEST Duplicate"
    NO_DUPLICATE_LABEL = "No Duplicate"

    _fp_duplicate_labels: List[str] = [NO_DUPLICATE_LABEL] * _fp_len
    _rt_duplicate_labels: List[str] = [NO_DUPLICATE_LABEL] * _rt_len

    # Track the current source/original row index per WXY so that the source row
    # can be labeled when a later duplicate is actually found.
    _fp_source_idx_by_wid: Dict[int, int] = {}
    _rt_source_idx_by_wid: Dict[int, int] = {}

    fp_rejects_not_in_retest = 0

    _fp_add  = fp_pass_ids.add
    _h_add   = wxy_head_pass_ids.add
    _h_dis   = wxy_head_pass_ids.discard
    _dup_app = duplicate_wxy_list.append
    _rt_dup_app = retest_duplicate_wxy_list.append
    _rt_pass_seen_add = retest_pass_seen_ids.add

    _i = 0
    for _wid, _is_pass, _hb in zip(fp_wids, fp_pf, fp_hbin):
        if _wid:
            if _wid in fp_pass_ids and _wid in wxy_head_pass_ids:
                _pn = _i + 1
                duplicate_guid += 1
                _dup_app((_id_to_wxy[_wid], _pn))
                _fp_dup_hits[_i] = _pn          # raw C int write — no Python object
                _fp_duplicate_labels[_i] = FP_DUPLICATE_LABEL
                _src_i = _fp_source_idx_by_wid.get(_wid)
                if _src_i is not None and _fp_duplicate_labels[_src_i] == NO_DUPLICATE_LABEL:
                    _fp_duplicate_labels[_src_i] = FP_ORIGINAL_LABEL
            if _is_pass:
                _fp_add(_wid)
                _h_add(_wid)
                _fp_source_idx_by_wid[_wid] = _i
            else:
                _h_dis(_wid)
                _fp_source_idx_by_wid.pop(_wid, None)
                if _hb != 3 and _wid not in retest_all_ids:  # B10: inline reject check
                    fp_rejects_not_in_retest += 1
        _i += 1

    # ── Single RETEST pass: FP-original duplicate + RETEST duplicate detection ──
    _i = 0
    for _wid, _is_pass in zip(rt_wids, rt_pass):
        if _wid:
            _pn = _fp_len + _i + 1

            # Classification priority:
            # 1) If WXY already had a PASS in an earlier RETEST, this row is a
            #    RETEST Duplicate regardless of the current row PASS/FAIL result.
            # 2) Otherwise, if WXY has a FIRST PASS PASS original and active PASS
            #    chain-head, this row is an FP Duplicate.
            # 3) If current RETEST row is PASS, it becomes the RETEST source for
            #    future RETEST Duplicate detection.
            if _wid in retest_pass_seen_ids:
                retest_duplicate_guid += 1
                _rt_dup_app((_id_to_wxy[_wid], _pn))
                _rt_retest_dup_hits[_i] = _pn
                _rt_duplicate_labels[_i] = RETEST_DUPLICATE_LABEL
                _src_i = _rt_source_idx_by_wid.get(_wid)
                if _src_i is not None and _rt_duplicate_labels[_src_i] == NO_DUPLICATE_LABEL:
                    _rt_duplicate_labels[_src_i] = RETEST_ORIGINAL_LABEL
            elif _wid in fp_pass_ids and _wid in wxy_head_pass_ids:
                duplicate_guid += 1
                _dup_app((_id_to_wxy[_wid], _pn))
                _rt_dup_hits[_i] = _pn          # raw C int write — no Python object
                _rt_duplicate_labels[_i] = FP_DUPLICATE_LABEL
                _src_i = _fp_source_idx_by_wid.get(_wid)
                if _src_i is not None and _fp_duplicate_labels[_src_i] == NO_DUPLICATE_LABEL:
                    _fp_duplicate_labels[_src_i] = FP_ORIGINAL_LABEL

            if _is_pass:
                _h_add(_wid)
                _rt_pass_seen_add(_wid)
                _rt_source_idx_by_wid[_wid] = _i
            else:
                _h_dis(_wid)
        _i += 1

    # ── Bulk write-back: datalog + duplicate_wxy columns ─────────────────────
    # Single fused pass per row list — each row dict is touched exactly once.
    # tuple() and "N/A" allocation only fires for actual duplicate hits (sparse).
    # _id_to_wxy[_wid] resolves the WXY string for the duplicate row itself.
    _sf = "source_file"
    _dl = "datalog"
    _dw = "duplicate_wxy"
    for _row, _hit, _wid, _is_pass, _hb, _label in zip(fp_rows, _fp_dup_hits, fp_wids, fp_pf, fp_hbin, _fp_duplicate_labels):
        _row[_dl] = _row[_sf]
        _row[_dw] = (_id_to_wxy[_wid], _hit) if _hit else "N/A"
        _row["is_duplicate"] = FP_DUPLICATE_LABEL if _hit else NO_DUPLICATE_LABEL
        _row["retest_duplicate_wxy"] = "N/A"
        _row["is_retest_duplicate"] = NO_DUPLICATE_LABEL
        _row["is_total_duplicate"] = _label
        _row["duplicate_label"] = _label
        if not _is_pass and _hb != 3 and _wid:
            _row["reject_in_retest"] = "YES" if _wid in retest_all_ids else "NO"
        else:
            _row["reject_in_retest"] = "N/A"
            
    for _row, _hit, _rt_hit, _wid, _label in zip(retest_rows, _rt_dup_hits, _rt_retest_dup_hits, rt_wids, _rt_duplicate_labels):
        _row[_dl] = _row[_sf]
        _row[_dw] = (_id_to_wxy[_wid], _hit) if _hit else "N/A"
        _row["is_duplicate"] = FP_DUPLICATE_LABEL if _hit else NO_DUPLICATE_LABEL
        _row["retest_duplicate_wxy"] = (_id_to_wxy[_wid], _rt_hit) if _rt_hit else "N/A"
        _row["is_retest_duplicate"] = RETEST_DUPLICATE_LABEL if _rt_hit else NO_DUPLICATE_LABEL
        _row["is_total_duplicate"] = _label
        _row["duplicate_label"] = _label
        _row["reject_in_retest"] = "N/A"

    # ── QC analysis ───────────────────────────────────────────────────────────
    # total_good_ids: C-level set union (no Python loop).
    # QC rows pre-extracted to a compact int array — eliminates dict lookups
    # inside the QC hot loop the same way fp/rt rows were handled above.
    total_good_ids = fp_pass_ids | retest_pass_ids

    _qc_rows = qc_data["rows"] if qc_data else []
    qc_wids  = _CArray('i', (_get_wid(_r["wxy"]) if _r.get("wxy") else 0 for _r in _qc_rows))
    qc_seen_ids:   Set[int] = set()
    qc_unique_ids: Set[int] = set()
    qc_duplicate_guid = 0
    _qc_seen_add   = qc_seen_ids.add
    _qc_unique_add = qc_unique_ids.add

    #Exclude invalid/blank QC coordinate key from Check 4 and QC-good matching.
    # _id_to_wxy is a 1-based LIST, not a dict, so use list indexing instead of .get().
    # "NaN_NaN_NaN" means Wafer/X/Y were all missing and should not be treated
    # as a real QC unit for previous-operation good-part verification.
    INVALID_QC_WXY = "NaN_NaN_NaN"

    for _wid in qc_wids:
        if not _wid:
            continue

        # Guard the list lookup to keep the code safe even if an unexpected WID appears.
        _qc_wxy = _id_to_wxy[_wid] if 0 <= _wid < len(_id_to_wxy) else ""
        if _qc_wxy == INVALID_QC_WXY:
            continue

        if _wid in qc_seen_ids:
            qc_duplicate_guid += 1
        else:
            _qc_seen_add(_wid)
            _qc_unique_add(_wid)


    # C-level set intersection replaces the sum(1 for ...) Python loop
    qc_sampling_good_in_prev_op = len(qc_unique_ids & total_good_ids)

    total_duplicate_guid = duplicate_guid + retest_duplicate_guid

    calculated_fp_qty = int(fp_data["pass_count"]) - duplicate_guid
    delta = calculated_fp_qty - fp_actual

    total_pass_parts = int(fp_data["pass_count"]) + int(retest_data["pass_count"])
    total_good = total_pass_parts - total_duplicate_guid
    total_delta = total_good - total_actual

    # ── FTDC Criteria Checks ──────────────────────────────────────────────
    # Check 1: Total Actual Good QTY vs Total Tested Good w/o Duplicate UID
    check1_result = "PASS" if total_delta >= 0 else "FAIL"
    # Check 2: Too many good units retested (duplicate > 72)
    check2_result = "FAIL" if total_duplicate_guid > 72 else "PASS"
    # Check 4: All unique QC WXY verified PASS in combined FP+RETEST
    if qc_unique_ids:
        check4_all_qc_good = qc_unique_ids <= total_good_ids
    else:
        check4_all_qc_good = True  # no QC parts = vacuously true
    check4_result = "PASS" if check4_all_qc_good else "FAIL"

    # Final statuses now require BOTH the existing quantity condition and Check 4.
    # If either condition fails, the corresponding status must be FAIL.
    status = "PASS" if (delta >= 0 and check4_result == "PASS") else "FAIL"
    total_status = "PASS" if (total_delta >= 0 and check4_result == "PASS") else "FAIL"

    _emit_log("-" * 80, True, logger)
    _emit_log(f"Duplicate GUID - FIRST PASS original duplicate: {duplicate_guid:,}", True, logger)
    _emit_log("Duplicate GUID rule: the first duplicate against an established FIRST PASS PASS original is counted here.", True, logger)
    _emit_log(f"Duplicate GUID - RETEST duplicate UID: {retest_duplicate_guid:,}", True, logger)
    _emit_log("RETEST duplicate UID rule: once a WXY has already passed in RETEST, any later RETEST row with the same WXY is counted here regardless of the current row PASS/FAIL result.", True, logger)
    _emit_log(f"Total Duplicate GUID: {total_duplicate_guid:,}", True, logger)
    _emit_log(f"FP rejects not in RETEST: {fp_rejects_not_in_retest:,}", True, logger)
    _emit_log("FP rejects not in RETEST rule: FIRST PASS FAIL WXY missing from RETEST WXY, excluding FIRST PASS HARD_BIN 3 rejects.", True, logger)
    _emit_log(f"QC Sampling units good in previous operation: {qc_sampling_good_in_prev_op:,}", True, logger)
    _emit_log(f"QC Duplicate GUID (within QC): {qc_duplicate_guid:,}", True, logger)
    _emit_log(f"Calculated First Pass QTY = FIRST PASS PASS parts - Duplicate GUID = {calculated_fp_qty:,}", True, logger)
    _emit_log(f"Comparison = Calculated First Pass QTY ({calculated_fp_qty:,}) - First Pass Actual Good QTY ({fp_actual:,}) = {delta:,}", True, logger)
    _emit_log(f"First Pass Result: {status}", True, logger)
    _emit_log(f"Total PASS parts = FIRST PASS PASS ({int(fp_data['pass_count']):,}) + RETEST PASS ({int(retest_data['pass_count']):,}) = {total_pass_parts:,}", True, logger)
    _emit_log(f"Total Good = Total PASS parts - Total Duplicate GUID = {total_good:,}", True, logger)
    _emit_log(f"Total Good Comparison = Total Good ({total_good:,}) - Total Actual Good QTY ({total_actual:,}) = {total_delta:,}", True, logger)
    _emit_log(f"Total Good Result: {total_status}", True, logger)
    _emit_log(f"FTDC Check 1: {check1_result}  |  Check 2: {check2_result}  |  Check 4: {check4_result}", True, logger)
    if progress_callback:
        progress_callback(1.0, "Analysis completed")

    return {
        "status": status, "lot_id": lot_id, "mpc": mpc_text,
        "wxy_tests": wxy_tests, "wxy_source": wxy_source,
        "first_pass": fp_data, "retest": retest_data, "qc": qc_data,
        "duplicate_guid": duplicate_guid, "duplicate_wxy_list": duplicate_wxy_list,
        "retest_duplicate_guid": retest_duplicate_guid,
        "retest_duplicate_wxy_list": retest_duplicate_wxy_list,
        "total_duplicate_guid": total_duplicate_guid,
        "fp_rejects_not_in_retest": fp_rejects_not_in_retest,
        "qc_sampling_good_in_prev_op": qc_sampling_good_in_prev_op,
        "qc_duplicate_guid": qc_duplicate_guid,
        "first_pass_total_parts": fp_data["total_parts"],
        "first_pass_pass_count": fp_data["pass_count"],
        "retest_pass_count": retest_data["pass_count"],
        "calculated_first_pass_qty": calculated_fp_qty,
        "first_pass_actual_good_qty": fp_actual,
        "delta_vs_fp_actual_good": delta,
        "total_pass_parts": total_pass_parts,
        "total_good": total_good,
        "total_actual_good_qty": total_actual,
        "delta_vs_total_actual_good": total_delta,
        "total_good_status": total_status,
        "check1_result": check1_result,
        "check2_result": check2_result,
        "check4_result": check4_result,
    }
