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
- Perl Reliability Enhancements:
  * Logs all parser warnings/errors with file, record type, and byte offset to Activity Log
  * Enforces strict validation during Run Analysis: blocks files with leftover/extra bytes,
    invalid FAR header (CPU_TYP / STDF_VER), or MIR LOT_ID mismatch against user Lot ID
  * Normalizes unset values (65535 bins, TEST_T == 0)
"""
import json
import logging
import os
import re
import sys
from array import array as _CArray
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_HUB_ROOT = os.path.dirname(os.path.dirname(_CURRENT_DIR))
if _HUB_ROOT not in sys.path:
    sys.path.insert(0, _HUB_ROOT)

from apps.ftdc_checker.stdf_parser import (
    LogFunc, ProgressFunc,
    _count_files_text, _emit_log, sort_paths_by_filename_timestamp,
    parse_stdf_file, _parse_stdf_worker, enforce_strict_analysis_validation,
    normalize_lot_id_for_match,
)
from core.config import get_asset_path

# -- Parallelism configuration ------------------------------------------------
PARALLEL_DEFAULT_ENABLED = True   # set False to hard-disable parallel parsing


def _resolve_worker_count(max_workers, n_files):
    """Return number of worker processes to use (1 == sequential).

    max_workers: None -> auto min(cpu_count, n_files); int -> clamp to [1, n].
    """
    if n_files <= 1 or not PARALLEL_DEFAULT_ENABLED:
        return 1
    if max_workers is None:
        cpu = os.cpu_count() or 1
        return max(1, min(cpu, n_files))
    try:
        w = int(max_workers)
    except (TypeError, ValueError):
        return 1
    return max(1, min(w, n_files))


# ── Production WXY mapping (loaded from mask_wxy_map.json) ───────────────────
def _get_json_path(filename: str) -> str:
    return get_asset_path(filename)


_MASK_WXY_JSON_PATH = _get_json_path("mask_wxy_map.json")
_RJ_RE = re.compile(r'rj(\d+)', re.IGNORECASE)
_MPC_12CHAR_RE = re.compile(r'^[A-Za-z0-9]{12}$')
INVALID_WXY = "NaN_NaN_NaN"
INVALID_WXYS = {INVALID_WXY, "0_0_0"}


def _is_zero_coord(part: str) -> bool:
    """Return True if a W/X/Y coordinate component parses numerically as zero."""
    p = part.strip()
    if not p:
        return False
    try:
        return float(p) == 0.0
    except ValueError:
        return False


def _wxy_zero_count(val: str) -> int:
    """Return how many of the W/X/Y components are exactly zero (e.g. '0', '00', '-0', '0.0')."""
    return sum(1 for part in val.split("_") if _is_zero_coord(part))


def _is_valid_wxy(wxy: Optional[str]) -> bool:
    """Check if WXY coordinate is valid and not a placeholder/dummy.

    Invalid when:
      - empty / contains 'NaN' (e.g. 'NaN_NaN_NaN'), or
      - Wafer (the first value in W_X_Y) has value 0 regardless of X and Y
        (e.g. '0_1_2', '0_137_136', '0_0_5', '0_7_0', '0_0_0'), or
      - ANY TWO (or more) of the W/X/Y components are zero (e.g. '24_0_0').
    """
    if not wxy:
        return False
    val = str(wxy).strip()
    if not val or val in INVALID_WXYS or "NaN" in val:
        return False
    if "0" not in val:
        return True
    parts = val.split("_")
    if parts and _is_zero_coord(parts[0]):
        return False
    zeros = 0
    for part in parts:
        if _is_zero_coord(part):
            zeros += 1
            if zeros >= 2:
                return False
    return True


def validate_mpc_code(mpc_text: object) -> str:
    """Validate that MPC is a 12-character alphanumeric code, normalized to uppercase."""
    cleaned = str(mpc_text or "").strip().upper()
    if not cleaned:
        raise ValueError("MPC is required.")
    if not _MPC_12CHAR_RE.fullmatch(cleaned):
        raise ValueError(
            f"Invalid MPC '{cleaned}'. MPC must be a 12-digit/character code (e.g., 59793QRTBC01)."
        )
    return cleaned


def _load_mpc_wxy_map() -> Dict[str, Dict[str, Any]]:
    """Load MPC-to-WXY test-number mapping from the JSON file."""
    try:
        with open(_MASK_WXY_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        result: Dict[str, Dict[str, Any]] = {}
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            entry: Dict[str, Any] = {}
            for wk, wv in v.items():
                if isinstance(wv, list):
                    entry[wk] = [
                        int(x) if isinstance(x, int) or (isinstance(x, str) and x.strip().isdigit()) else x
                        for x in wv
                    ]
                elif isinstance(wv, (int, str)) and str(wv).strip().isdigit():
                    entry[wk] = int(wv)
                else:
                    entry[wk] = wv
            result[str(k)] = entry
        return result
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"Failed to load MPC WXY mapping from '{_MASK_WXY_JSON_PATH}': {exc}"
        ) from exc


MPC_WXY_TEST_MAP: Dict[str, Dict[str, Any]] = _load_mpc_wxy_map()


def _build_unsupported_mask_message() -> str:
    """Build the unsupported mask error message with a list of all supported masks."""
    lines = [
        "This Mask is not included in the list of device using automated WXY Filtering.",
        "Kindly use manual filter to select the applicable WXY test parameter.",
        "",
        "Supported Masks and their WXY test parameters:",
    ]
    for mask, tests in MPC_WXY_TEST_MAP.items():
        lines.append(f"  {mask}  -  Wafer: {tests.get('wafer')}, X: {tests.get('x')}, Y: {tests.get('y')}")
    return "\n".join(lines)


UNSUPPORTED_MPC_WXY_MESSAGE = _build_unsupported_mask_message()


def reload_wxy_config() -> Dict[str, Dict[str, Any]]:
    """Reload mask_wxy_map.json in-place so all callers see updated WXY mappings immediately."""
    global UNSUPPORTED_MPC_WXY_MESSAGE
    fresh = _load_mpc_wxy_map()
    MPC_WXY_TEST_MAP.clear()
    MPC_WXY_TEST_MAP.update(fresh)
    UNSUPPORTED_MPC_WXY_MESSAGE = _build_unsupported_mask_message()
    return MPC_WXY_TEST_MAP


def save_mask_wxy_entry(
    mask: str,
    wafer: int,
    x: int,
    y: int,
    uid: Optional[Any] = None,
    bt: Optional[Sequence[int]] = None,
    zb: Optional[Sequence[int]] = None,
    macid_prefix: Optional[Any] = None,
    bt_prefix: Optional[Any] = None,
    zb_prefix: Optional[Any] = None,
    macid_invalid: Optional[Any] = None,
) -> Dict[str, Any]:
    """Save or update a 5-character mask WXY + optional UID/MACID mapping in mask_wxy_map.json and reload in-memory cache."""
    mask_key = str(mask or "").strip().upper()[:5]
    if len(mask_key) != 5:
        raise ValueError(f"Mask must be 5 characters (got '{mask}').")
    data: Dict[str, Dict[str, Any]] = {}
    if os.path.isfile(_MASK_WXY_JSON_PATH):
        with open(_MASK_WXY_JSON_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
    entry = dict(data.get(mask_key, {}))
    entry["wafer"] = int(wafer)
    entry["x"] = int(x)
    entry["y"] = int(y)
    if uid is not None:
        if isinstance(uid, (list, tuple)):
            uid_list = [int(v) for v in uid]
            if len(uid_list) == 1:
                entry["uid"] = uid_list[0]
            elif len(uid_list) > 1:
                entry["uid"] = uid_list
            else:
                entry.pop("uid", None)
        elif str(uid).strip() == "":
            entry.pop("uid", None)
        else:
            entry["uid"] = int(uid)
    if bt is not None:
        bt_list = [int(v) for v in bt]
        if bt_list:
            entry["bt"] = bt_list
        else:
            entry.pop("bt", None)
    if zb is not None:
        zb_list = [int(v) for v in zb]
        if zb_list:
            entry["zb"] = zb_list
        else:
            entry.pop("zb", None)
    for pfx_k, pfx_v in (
        ("macid_prefix", macid_prefix),
        ("bt_prefix", bt_prefix),
        ("zb_prefix", zb_prefix),
        ("macid_invalid", macid_invalid),
    ):
        if pfx_v is not None:
            if isinstance(pfx_v, (list, tuple)):
                cleaned_pfx = [str(p).strip() for p in pfx_v if str(p).strip()]
                if cleaned_pfx:
                    entry[pfx_k] = cleaned_pfx[0] if (len(cleaned_pfx) == 1 and pfx_k != "macid_invalid") else cleaned_pfx
                else:
                    entry.pop(pfx_k, None)
            elif str(pfx_v).strip():
                entry[pfx_k] = str(pfx_v).strip()
            else:
                entry.pop(pfx_k, None)
    data[mask_key] = entry
    tmp_path = _MASK_WXY_JSON_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, _MASK_WXY_JSON_PATH)
    reload_wxy_config()
    try:
        from apps.ftdc_checker.sg406_analysis import clear_macid_validation_cache
        clear_macid_validation_cache()
    except Exception as exc:
        logging.debug("MACID validation cache clear skipped: %s", exc)
    return entry

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


def _parse_int_field(value: object, field_name: str) -> int:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        raise ValueError(f"{field_name} is required.")
    cleaned = text[1:] if text.startswith("+") else text
    if not cleaned.isdigit():
        raise ValueError(
            f"{field_name} must be a valid integer (whole number with no decimals).\n\n"
            f"Provided value: '{value}'\n"
            f"Proper Input Example: 12500 (or any whole number, e.g., 500, 1000, 25000)."
        )
    return int(cleaned)


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
    max_workers: Optional[int] = None,
    pre_parsed: Optional[Dict[str, object]] = None,
    expected_lot_id: str = "",
    strict_validation: bool = False,
    truncated_files_out: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, object]:
    """
    B7/B8/B11: Optimized panel parsing.
    - Only stores WXY test results (not all 1K tests)
    - Single-pass aggregation (pass/fail/missing counted inline)
    - Logs all warnings/errors to Activity Log and enforces strict validation
      when strict_validation=True (blocking leftover/extra bytes, invalid FAR,
      or MIR LOT_ID mismatch).
    """
    rows: List[Dict[str, object]] = []
    total_counts: Dict[str, int] = defaultdict(int)
    _file_sizes: List[int] = []
    for _p in input_paths:
        try:
            _file_sizes.append(max(os.path.getsize(_p), 1))
        except OSError:
            _file_sizes.append(1)
    total_bytes = sum(_file_sizes) or 1
    processed_bytes = 0
    span = max(progress_end - progress_start, 0.0)
    filter_tests = {int(wxy_tests[key]) for key in WXY_KEYS}

    wxy_wafer = wxy_tests["wafer"]
    wxy_x = wxy_tests["x"]
    wxy_y = wxy_tests["y"]

    pass_count = 0
    fail_count = 0
    missing_wxy = 0
    mir_lot_id = ""

    status_text = f"Parsing {panel_name}: {_count_files_text(len(input_paths))}"

    def _row_panel_for(source: str) -> str:
        """Derive the per-row panel label from the source filename."""
        row_panel = panel_name
        if panel_name == "RETEST":
            match = _RJ_RE.search(source)
            if match:
                num = int(match.group(1))
                if 11 <= (num % 100) <= 13:
                    suffix = 'th'
                else:
                    suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(num % 10, 'th')
                row_panel = f"{num}{suffix} RETEST"
        elif panel_name == "QC":
            match = _RJ_RE.search(source)
            if match:
                num = int(match.group(1))
                row_panel = f"QC {num + 1}A"
            else:
                row_panel = "QC 1A"
        return row_panel

    def _accumulate(index: int, input_path: str, parsed, counts) -> None:
        """Append rows for one parsed file and update counters."""
        nonlocal pass_count, fail_count, missing_wxy, mir_lot_id
        source = os.path.basename(input_path)
        row_panel = _row_panel_for(source)

        if not mir_lot_id and isinstance(parsed, dict):
            raw_mir_lot = str((parsed.get("MIR") or {}).get("LOT_ID") or "").strip().upper()
            if raw_mir_lot:
                mir_lot_id = normalize_lot_id_for_match(raw_mir_lot) or raw_mir_lot

        diag = parsed.get("DIAGNOSTICS") or {}
        integrity = parsed.get("INTEGRITY") or {}
        is_trunc = bool(
            integrity.get("is_truncated")
            or diag.get("is_truncated")
            or diag.get("truncated")
            or not integrity.get("ended_at_eof", True)
            or not integrity.get("last_record_was_mrr", True)
        )
        if is_trunc:
            t_info = {
                "filename": source,
                "filepath": input_path,
                "panel": panel_name,
                "ended_at_eof": integrity.get("ended_at_eof", diag.get("ended_at_eof", False)),
                "last_record_was_mrr": integrity.get("last_record_was_mrr", diag.get("last_record_was_mrr", False)),
                "pir_without_prr": integrity.get("pir_without_prr", diag.get("pir_without_prr", 0)),
                "prr_count": integrity.get("prr_count", diag.get("prr_count", len(parsed.get("PARTS", [])))),
                "pcr_part_cnt": integrity.get("pcr_part_cnt", diag.get("pcr_part_cnt")),
            }
            if truncated_files_out is not None:
                truncated_files_out.append(t_info)
            _emit_log(
                f"[WARNING] TRUNCATED STDF FILE in {panel_name}: '{t_info['filename']}'! "
                f"ended_at_eof={t_info['ended_at_eof']}, last_record_was_mrr={t_info['last_record_was_mrr']}, "
                f"pir_without_prr={t_info['pir_without_prr']}, PRR count={t_info['prr_count']} "
                f"vs PCR PART_CNT={t_info['pcr_part_cnt']}. Partial data recovered.",
                True, logger,
            )

        # Log any warnings/errors collected during parsing to Activity Log (Perl 3)
        diag = parsed.get("DIAGNOSTICS") if isinstance(parsed, dict) else None
        if isinstance(diag, dict) and not diag.get("_logged"):
            for w in (diag.get("warnings") or []):
                _emit_log(w, True, logger)
            for e in (diag.get("errors") or []):
                _emit_log(e, True, logger)
            diag["_logged"] = True

        # Enforce strict header / extra-bytes / MIR Lot ID validation during Run Analysis
        if strict_validation and isinstance(parsed, dict):
            enforce_strict_analysis_validation(parsed, input_path)

        for rec_name, count in (counts or {}).items():
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
            wafer_str = _format_coord_value(wafer_value)
            x_str = _format_coord_value(x_value)
            y_str = _format_coord_value(y_value)
            wxy = f"{wafer_str}_{x_str}_{y_str}" if wafer_str and x_str and y_str else ""

            if pf == "PASS":
                pass_count += 1
            else:
                fail_count += 1
            if wafer_str == "NaN" or x_str == "NaN" or y_str == "NaN":
                missing_wxy += 1

            raw_test_t = part.get("TEST_T")
            raw_hb = part.get("HARD_BIN")
            raw_sb = part.get("SOFT_BIN")

            rows_append({
                "panel": row_panel, "source_file": source,
                "part_id": part.get("PART_ID") or f"PART_{len(rows) + 1}",
                "site_num": part.get("SITE_NUM", ""),
                "test_t": "" if raw_test_t is None else raw_test_t,
                "hard_bin": "" if raw_hb is None else raw_hb,
                "soft_bin": "" if raw_sb is None else raw_sb,
                "pass_fail": pf,
                "wafer": wafer_str,
                "x": x_str,
                "y": y_str,
                "wxy": wxy,
            })

    # -- Cross-panel fast path: rows-only assembly from pre-parsed results ------
    if pre_parsed is not None:
        for index, input_path in enumerate(input_paths, start=1):
            _emit_log(f"\n[{panel_name}] [{index}/{len(input_paths)}] Processing {input_path}", True, logger)
            parsed = pre_parsed.get(input_path)
            if parsed is None:
                parsed, _c, _ = parse_stdf_file(
                    input_path=input_path, filter_tests=filter_tests,
                    verbose=False, logger=logger, progress_callback=None,
                    expected_lot_id=expected_lot_id, strict_validation=strict_validation,
                )
            _accumulate(index, input_path, parsed, {})
            _emit_log(f"{panel_name} parts accumulated: {len(rows):,}", True, logger)
        _emit_log(f"{panel_name} summary: total={len(rows):,}, PASS={pass_count:,}, FAIL={fail_count:,}, missing WXY={missing_wxy:,}", True, logger)
        return {
            "panel": panel_name, "rows": rows, "total_parts": len(rows),
            "pass_count": pass_count, "fail_count": fail_count, "missing_wxy": missing_wxy,
            "counts": dict(total_counts), "skipped_ptr": 0, "mir_lot_id": mir_lot_id,
        }

    workers = _resolve_worker_count(max_workers, len(input_paths))
    used_parallel = False

    if workers > 1:
        filter_list = tuple(sorted(filter_tests))
        tasks = [(p, filter_list, expected_lot_id) for p in input_paths]
        parsed_by_path: Dict[str, object] = {}
        try:
            _emit_log(f"[{panel_name}] Parsing {len(input_paths)} file(s) in parallel using {workers} worker process(es).", True, logger)
            done_bytes = 0
            with ProcessPoolExecutor(max_workers=workers) as ex:
                future_to_path = {ex.submit(_parse_stdf_worker, t): t[0] for t in tasks}
                for fut in as_completed(future_to_path):
                    fpath, parsed = fut.result()
                    parsed_by_path[fpath] = parsed
                    try:
                        done_bytes += max(os.path.getsize(fpath), 1)
                    except OSError:
                        done_bytes += 1
                    if progress_callback:
                        progress_callback(progress_start + span * (done_bytes / total_bytes), status_text)
            used_parallel = True
        except Exception as exc:
            _emit_log(f"[{panel_name}] Parallel parsing unavailable ({exc}); falling back to sequential.", True, logger)
            rows.clear()
            total_counts.clear()
            pass_count = fail_count = missing_wxy = 0
            used_parallel = False

        if used_parallel:
            for index, input_path in enumerate(input_paths, start=1):
                _emit_log(f"\n[{panel_name}] [{index}/{len(input_paths)}] Processing {input_path}", True, logger)
                _accumulate(index, input_path, parsed_by_path[input_path], {})
                _emit_log(f"{panel_name} parts accumulated: {len(rows):,}", True, logger)

    if not used_parallel:
        for index, input_path in enumerate(input_paths, start=1):
            file_size = _file_sizes[index - 1]
            _emit_log(f"\n[{panel_name}] [{index}/{len(input_paths)}] Processing {input_path}", True, logger)

            def file_progress(current_bytes: int, file_total: int, base=processed_bytes, fsz=file_size):
                if not progress_callback:
                    return
                total_for_file = file_total if file_total > 0 else fsz
                fraction = progress_start + span * ((base + min(max(current_bytes, 0), total_for_file)) / total_bytes)
                progress_callback(fraction, status_text)

            parsed, counts, _ = parse_stdf_file(
                input_path=input_path, filter_tests=filter_tests,
                verbose=False, logger=logger, progress_callback=file_progress,
                expected_lot_id=expected_lot_id, strict_validation=strict_validation,
            )
            _accumulate(index, input_path, parsed, counts)

            processed_bytes += file_size
            if progress_callback:
                progress_callback(progress_start + span * (processed_bytes / total_bytes), status_text)
            _emit_log(f"{panel_name} parts accumulated: {len(rows):,}", True, logger)

    _emit_log(f"{panel_name} summary: total={len(rows):,}, PASS={pass_count:,}, FAIL={fail_count:,}, missing WXY={missing_wxy:,}", True, logger)
    return {
        "panel": panel_name, "rows": rows, "total_parts": len(rows),
        "pass_count": pass_count, "fail_count": fail_count, "missing_wxy": missing_wxy,
        "counts": dict(total_counts), "skipped_ptr": 0,
    }


def analyze_guid_data(
    first_pass_paths: Sequence[str],
    retest_paths: Sequence[str],
    qc_paths: Sequence[str] = (),
    lot_id: str = "",
    mpc_text: str = "",
    first_pass_actual_good_qty: object = "",
    total_actual_good_qty: object = "",
    manual_filter_tests: Optional[Set[int]] = None,
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
    max_workers: Optional[int] = None,
) -> Dict[str, object]:
    lot_id = str(lot_id or "").strip().upper()
    mpc_text = str(mpc_text or "").strip()
    first_pass_actual_good_qty = str(first_pass_actual_good_qty or "").strip()
    total_actual_good_qty = str(total_actual_good_qty or "").strip()

    first_pass_paths = sort_paths_by_filename_timestamp(first_pass_paths)
    retest_paths = sort_paths_by_filename_timestamp(retest_paths)
    qc_paths = sort_paths_by_filename_timestamp(qc_paths or [])

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
    mpc_text = validate_mpc_code(mpc_text)

    if not manual_filter_tests:
        from apps.ftdc_checker.sg406_analysis import get_macid_mask_key, analyze_sg406_guid_data
        if get_macid_mask_key(mpc_text, MPC_WXY_TEST_MAP) is not None:
            return analyze_sg406_guid_data(
                first_pass_paths=first_pass_paths,
                retest_paths=retest_paths,
                qc_paths=qc_paths,
                lot_id=lot_id,
                mpc_text=mpc_text,
                fp_actual=fp_actual,
                total_actual=total_actual,
                mpc_wxy_map=MPC_WXY_TEST_MAP,
                format_coord_fn=_format_coord_value,
                part_pass_fail_fn=_part_pass_fail,
                resolve_worker_count_fn=_resolve_worker_count,
                logger=logger,
                progress_callback=progress_callback,
                max_workers=max_workers,
            )

    _emit_log("=" * 80, True, logger)
    _emit_log("Starting STDF WXY / Duplicate GUID analysis", True, logger)
    if lot_id:
        _emit_log(f"Lot ID: {lot_id}", True, logger)
    _emit_log(f"MPC: {mpc_text}", True, logger)
    _emit_log(f"First Pass Actual Good QTY: {fp_actual:,}", True, logger)
    _emit_log(f"Total Actual Good QTY: {total_actual:,}", True, logger)

    wxy_tests, wxy_source = resolve_wxy_test_numbers(
        mpc_text=mpc_text,
        manual_filter_tests=manual_filter_tests, logger=logger,
    )
    _emit_log(f"WXY tests resolved from {wxy_source}: Wafer={wxy_tests['wafer']}, X={wxy_tests['x']}, Y={wxy_tests['y']}", True, logger)

    # === Cross-panel parallel pre-parse =======================================
    pre_parsed = None
    _all_paths = list(first_pass_paths) + list(retest_paths) + list(qc_paths)
    _combined_workers = _resolve_worker_count(max_workers, len(_all_paths))
    if _combined_workers > 1:
        _filter_list = tuple(sorted(int(wxy_tests[_k]) for _k in WXY_KEYS))
        _seen = set()
        _unique_paths = [p for p in _all_paths if not (p in _seen or _seen.add(p))]
        try:
            _sizes = {}
            for _p in _unique_paths:
                try:
                    _sizes[_p] = max(os.path.getsize(_p), 1)
                except OSError:
                    _sizes[_p] = 1

            _biggest = max(_unique_paths, key=lambda _p: _sizes[_p])
            _pool_paths = [_p for _p in _unique_paths if _p != _biggest]
            _pool_workers = max(1, min(_combined_workers, len(_pool_paths))) if _pool_paths else 1
            _big_name = os.path.basename(_biggest)
            _big_total = _sizes[_biggest]

            _emit_log(
                f"Parsing {len(_unique_paths)} file(s) across ALL panels "
                f"(FIRST PASS + RETEST + QC): {len(_pool_paths)} smaller file(s) in "
                f"{_pool_workers} worker process(es) + the largest datalog "
                f"('{_big_name}') in the main process with live progress.",
                True, logger,
            )

            def _big_progress(_cur, _tot, _bt=_big_total):
                if progress_callback:
                    _t = _tot if _tot and _tot > 0 else _bt
                    _frac = (min(max(_cur, 0), _t) / _t) if _t else 1.0
                    progress_callback(0.15 + 0.77 * _frac, f"Parsing largest datalog: {_big_name}")

            pre_parsed = {}
            _filter_set = set(_filter_list)
            if _pool_paths:
                with ProcessPoolExecutor(max_workers=_pool_workers) as _ex:
                    _futs = {_ex.submit(_parse_stdf_worker, (_p, _filter_list, lot_id)): _p for _p in _pool_paths}
                    _big_parsed, _bc, _ = parse_stdf_file(
                        input_path=_biggest, filter_tests=_filter_set,
                        verbose=False, logger=None, progress_callback=_big_progress,
                        expected_lot_id=lot_id, strict_validation=False,
                    )
                    pre_parsed[_biggest] = _big_parsed
                    for _fut in as_completed(_futs):
                        _path, _parsed = _fut.result()
                        pre_parsed[_path] = _parsed
            else:
                _big_parsed, _bc, _ = parse_stdf_file(
                    input_path=_biggest, filter_tests=_filter_set,
                    verbose=False, logger=None, progress_callback=_big_progress,
                    expected_lot_id=lot_id, strict_validation=False,
                )
                pre_parsed[_biggest] = _big_parsed

            if progress_callback:
                progress_callback(0.92, "Finished parsing all panels")
        except Exception as _exc:
            _emit_log(f"Cross-panel parallel parse failed ({_exc}); using per-panel parsing.", True, logger)
            pre_parsed = None

    truncated_files: List[Dict[str, Any]] = []

    fp_data = parse_panel_wxy_parts(
        first_pass_paths, "FIRST PASS", wxy_tests, logger, progress_callback,
        0.15, 0.55, max_workers=max_workers, pre_parsed=pre_parsed,
        expected_lot_id=lot_id, strict_validation=True,
        truncated_files_out=truncated_files,
    )
    retest_data = parse_panel_wxy_parts(
        retest_paths, "RETEST", wxy_tests, logger, progress_callback,
        0.55, 0.90, max_workers=max_workers, pre_parsed=pre_parsed,
        expected_lot_id=lot_id, strict_validation=True,
        truncated_files_out=truncated_files,
    )
    qc_data = parse_panel_wxy_parts(
        qc_paths, "QC", wxy_tests, logger, progress_callback,
        0.90, 0.97, max_workers=max_workers, pre_parsed=pre_parsed,
        expected_lot_id=lot_id, strict_validation=True,
        truncated_files_out=truncated_files,
    )

    if progress_callback and pre_parsed is not None:
        progress_callback(0.95, "Analyzing duplicates and QC")

    fp_rows = fp_data["rows"]
    retest_rows = retest_data["rows"]

    # ── WXY string interning ──────────────────────────────────────────────────
    _wxy_id_map: Dict[str, int] = {}
    _id_to_wxy: List[str] = [""]      # index 0 = unused sentinel

    def _get_wid(wxy: str) -> int:
        try:
            return _wxy_id_map[wxy]
        except KeyError:
            wid = len(_id_to_wxy)
            _wxy_id_map[wxy] = wid
            _id_to_wxy.append(wxy)
            return wid

    # ── Pre-extract retest rows → C-level arrays (1 pass over dicts) ─────────
    # Exclude "NaN_NaN_NaN" WXYs so missing coordinates never inflate duplicate GUID counts
    rt_wids = _CArray('i')
    rt_pass = bytearray()

    for _row in retest_rows:
        _wxy = _row.get("wxy")
        if _is_valid_wxy(_wxy):
            rt_wids.append(_get_wid(_wxy))
            rt_pass.append(_row["pass_fail"] == "PASS")
        else:
            rt_wids.append(0)
            rt_pass.append(0)

    # ── Pre-extract fp rows → C-level arrays (1 pass over dicts) ─────────────
    # Exclude invalid/dummy WXYs ("NaN_NaN_NaN", "0_0_0") so they never inflate duplicate GUID counts
    fp_wids = _CArray('i')
    fp_pf   = bytearray()
    fp_hbin = _CArray('i')            # hard_bin as int; -1 = absent/None

    for _row in fp_rows:
        _wxy = _row.get("wxy")
        if _is_valid_wxy(_wxy):
            fp_wids.append(_get_wid(_wxy))
            fp_pf.append(_row["pass_fail"] == "PASS")
        else:
            fp_wids.append(0)
            fp_pf.append(0)
        _hb = _row.get("hard_bin")
        fp_hbin.append(_hb if isinstance(_hb, int) else -1)

    # ── Single retest pass: build all retest ID sets ──────────────────────────
    retest_all_ids:  Set[int] = set()
    retest_pass_ids: Set[int] = set()

    for _wid, _is_pass in zip(rt_wids, rt_pass):
        if _wid:
            retest_all_ids.add(_wid)
            if _is_pass:
                retest_pass_ids.add(_wid)

    # ── Single FP pass: duplicate detection + fp_pass build + reject count ──────
    _fp_len = len(fp_rows)
    _rt_len = len(retest_rows)
    _fp_dup_hits = _CArray('i', bytes(4 * _fp_len))
    _rt_dup_hits = _CArray('i', bytes(4 * _rt_len))
    _rt_retest_dup_hits = _CArray('i', bytes(4 * _rt_len))

    fp_pass_ids:       Set[int] = set()
    wxy_head_pass_ids: Set[int] = set()
    duplicate_guid = 0
    duplicate_wxy_list: List[Tuple[str, int]] = []

    retest_duplicate_guid = 0
    retest_duplicate_wxy_list: List[Tuple[str, int]] = []
    retest_pass_seen_ids: Set[int] = set()

    FP_ORIGINAL_LABEL = "FP Original"
    FP_DUPLICATE_LABEL = "FP Duplicate"
    RETEST_ORIGINAL_LABEL = "RETEST Original"
    RETEST_DUPLICATE_LABEL = "RETEST Duplicate"
    NO_DUPLICATE_LABEL = "No Duplicate"

    _fp_duplicate_labels: List[str] = [NO_DUPLICATE_LABEL] * _fp_len
    _rt_duplicate_labels: List[str] = [NO_DUPLICATE_LABEL] * _rt_len

    _fp_source_idx_by_wid: Dict[int, int] = {}
    _fp_first_idx_by_wid: Dict[int, int] = {}
    _fp_dup_wids: Set[int] = set()
    fp_datalog_duplicate_count = 0
    fp_datalog_duplicate_wxy_list: List[Tuple[str, int]] = []
    _rt_source_idx_by_wid: Dict[int, int] = {}

    fp_rejects_not_in_retest = 0

    _fp_add  = fp_pass_ids.add
    _h_add   = wxy_head_pass_ids.add
    _h_dis   = wxy_head_pass_ids.discard
    _dup_app = duplicate_wxy_list.append
    _fp_dl_dup_app = fp_datalog_duplicate_wxy_list.append
    _rt_dup_app = retest_duplicate_wxy_list.append
    _rt_pass_seen_add = retest_pass_seen_ids.add
    _rt_pass_seen_dis = retest_pass_seen_ids.discard

    _i = 0
    for _wid, _is_pass, _hb in zip(fp_wids, fp_pf, fp_hbin):
        if _wid:
            _pn = _i + 1
            # Detect ANY duplicated WXY in FP datalog regardless of test result (PASS or FAIL)
            _first_fp_i = _fp_first_idx_by_wid.get(_wid)
            if _first_fp_i is not None:
                fp_datalog_duplicate_count += 1
                _fp_dl_dup_app((_id_to_wxy[_wid], _pn))
                _fp_dup_wids.add(_wid)
                _fp_dup_hits[_i] = _pn
                _fp_duplicate_labels[_i] = FP_DUPLICATE_LABEL
                if _fp_duplicate_labels[_first_fp_i] == NO_DUPLICATE_LABEL:
                    _fp_duplicate_labels[_first_fp_i] = FP_ORIGINAL_LABEL
            else:
                _fp_first_idx_by_wid[_wid] = _i

            if _wid in fp_pass_ids and _wid in wxy_head_pass_ids:
                duplicate_guid += 1
                _dup_app((_id_to_wxy[_wid], _pn))
                _fp_dup_hits[_i] = _pn
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
                if _hb != 3 and _wid not in retest_all_ids:
                    fp_rejects_not_in_retest += 1
        _i += 1

    # ── Single RETEST pass: FP-original duplicate + RETEST duplicate detection ──
    _i = 0
    for _wid, _is_pass in zip(rt_wids, rt_pass):
        if _wid:
            _pn = _fp_len + _i + 1
            if _wid in retest_pass_seen_ids:
                retest_duplicate_guid += 1
                _rt_dup_app((_id_to_wxy[_wid], _pn))
                _rt_retest_dup_hits[_i] = _pn
                _rt_duplicate_labels[_i] = RETEST_DUPLICATE_LABEL
                _src_i = _rt_source_idx_by_wid.get(_wid)
                if _src_i is not None:
                    if _rt_duplicate_labels[_src_i] == NO_DUPLICATE_LABEL:
                        _rt_duplicate_labels[_src_i] = RETEST_ORIGINAL_LABEL
                    elif _rt_duplicate_labels[_src_i] == FP_DUPLICATE_LABEL:
                        _rt_duplicate_labels[_src_i] = f"{FP_DUPLICATE_LABEL} / {RETEST_ORIGINAL_LABEL}"
                    elif _rt_duplicate_labels[_src_i] == RETEST_DUPLICATE_LABEL:
                        _rt_duplicate_labels[_src_i] = f"{RETEST_DUPLICATE_LABEL} / {RETEST_ORIGINAL_LABEL}"
            elif _wid in fp_pass_ids and _wid in wxy_head_pass_ids:
                duplicate_guid += 1
                _dup_app((_id_to_wxy[_wid], _pn))
                _rt_dup_hits[_i] = _pn
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
                _rt_pass_seen_dis(_wid)
                _rt_source_idx_by_wid.pop(_wid, None)
        _i += 1

    # ── Bulk write-back: datalog + duplicate_wxy columns ─────────────────────
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
        _row["is_fp_datalog_duplicate"] = (_wid in _fp_dup_wids) if _wid else False
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
    total_good_ids = fp_pass_ids | retest_pass_ids

    _qc_rows = qc_data["rows"]
    qc_wids  = _CArray('i', (_get_wid(_r["wxy"]) if _is_valid_wxy(_r.get("wxy")) else 0 for _r in _qc_rows))
    qc_seen_ids:   Set[int] = set()
    qc_unique_ids: Set[int] = set()
    qc_duplicate_guid = 0
    _qc_seen_add   = qc_seen_ids.add
    _qc_unique_add = qc_unique_ids.add

    for _wid in qc_wids:
        if not _wid:
            continue
        _qc_wxy = _id_to_wxy[_wid] if 0 <= _wid < len(_id_to_wxy) else ""
        if not _is_valid_wxy(_qc_wxy):
            continue
        if _wid in qc_seen_ids:
            qc_duplicate_guid += 1
        else:
            _qc_seen_add(_wid)
            _qc_unique_add(_wid)

    qc_sampling_good_in_prev_op = len(qc_unique_ids & total_good_ids)

    qc_failed_or_missing_prev_op_ids = qc_unique_ids - total_good_ids
    qc_failed_or_missing_prev_op_qty = len(qc_failed_or_missing_prev_op_ids)
    qc_failed_or_missing_prev_op_wxy = sorted(_id_to_wxy[_wid] for _wid in qc_failed_or_missing_prev_op_ids)

    # Tag each QC row directly based on Check 4 business rule
    qc_failed_set = set(qc_failed_or_missing_prev_op_ids)
    for _row, _wid in zip(_qc_rows, qc_wids):
        _row["is_qc_fail_prev_step"] = (_wid in qc_failed_set) if _wid else False

    total_duplicate_guid = duplicate_guid + retest_duplicate_guid

    calculated_fp_qty = int(fp_data["pass_count"]) - duplicate_guid
    delta = calculated_fp_qty - fp_actual

    total_pass_parts = int(fp_data["pass_count"]) + int(retest_data["pass_count"])
    total_good = total_pass_parts - total_duplicate_guid
    total_delta = total_good - total_actual

    # ── FTDC Criteria Checks ──────────────────────────────────────────────
    has_fp_duplicate_wxy = fp_datalog_duplicate_count > 0
    check1_result = "PASS" if (total_delta >= 0 and not has_fp_duplicate_wxy) else "FAIL"
    check2_result = "PASS" if total_duplicate_guid <= 72 else "FAIL"
    check4_all_qc_good = qc_failed_or_missing_prev_op_qty == 0
    check4_result = "PASS" if check4_all_qc_good else "FAIL"

    status = "PASS" if (delta >= 0 and check4_result == "PASS" and not has_fp_duplicate_wxy) else "FAIL"
    total_status = "PASS" if (total_delta >= 0 and check4_result == "PASS" and not has_fp_duplicate_wxy) else "FAIL"

    _emit_log("-" * 80, True, logger)
    _emit_log(f"Duplicate WXY in FIRST PASS datalog (any test result): {fp_datalog_duplicate_count:,}", True, logger)
    _emit_log(f"Duplicate GUID - FIRST PASS original duplicate: {duplicate_guid:,}", True, logger)
    _emit_log("Duplicate GUID rule: the first duplicate against an established FIRST PASS PASS original is counted here.", True, logger)
    _emit_log(f"Duplicate GUID - RETEST duplicate UID: {retest_duplicate_guid:,}", True, logger)
    _emit_log("RETEST duplicate UID rule: a RETEST row whose immediately preceding RETEST occurrence for the same WXY was PASS is counted here.", True, logger)
    _emit_log(f"Total Duplicate GUID: {total_duplicate_guid:,}", True, logger)
    _emit_log(f"FP rejects not in RETEST: {fp_rejects_not_in_retest:,}", True, logger)
    _emit_log("FP rejects not in RETEST rule: FIRST PASS FAIL WXY missing from RETEST WXY, excluding FIRST PASS HARD_BIN 3 rejects.", True, logger)
    _emit_log(f"QC Sampling units good in previous operation: {qc_sampling_good_in_prev_op:,}", True, logger)
    _emit_log(f"QC Sampling units failed/missing in previous operation: {qc_failed_or_missing_prev_op_qty:,}", True, logger)
    if qc_failed_or_missing_prev_op_wxy:
        _emit_log(f"QC failed/missing WXY list: {', '.join(qc_failed_or_missing_prev_op_wxy)}", True, logger)
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

    mir_lot_id = (
        fp_data.get("mir_lot_id")
        or retest_data.get("mir_lot_id")
        or qc_data.get("mir_lot_id")
        or lot_id
    )

    res_dict: Dict[str, Any] = {
        "status": status, "lot_id": lot_id, "mir_lot_id": mir_lot_id, "mpc": mpc_text,
        "wxy_tests": wxy_tests, "wxy_source": wxy_source,
        "first_pass": fp_data, "retest": retest_data, "qc": qc_data,
        "duplicate_guid": duplicate_guid, "duplicate_wxy_list": duplicate_wxy_list,
        "fp_datalog_duplicate_count": fp_datalog_duplicate_count,
        "fp_datalog_duplicate_wxy_list": fp_datalog_duplicate_wxy_list,
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
        "qc_failed_or_missing_prev_op_qty": qc_failed_or_missing_prev_op_qty,
        "qc_failed_or_missing_prev_op_wxy": qc_failed_or_missing_prev_op_wxy,
        "check4_result": check4_result,
        "truncated_files": truncated_files,
    }
    remarks = compute_remarks(res_dict)
    res_dict["remarks"] = remarks
    _emit_log(f"Remarks: {remarks}", True, logger)
    return res_dict


def compute_remarks(result: Dict[str, object]) -> str:
    """Compute the remarks description across First Pass and Total Good.

    Evaluation order:
    1. Check if all QC units have PASS result in ANY FIRST PASS or RETEST datalog
       using the existing Check 4 business rule (check4_result and qc_failed_or_missing_prev_op_qty).
       If not, report the count of untested/rejected QC units.
    2. Check if there is any duplicated WXY in FIRST PASS datalog.
       If yes, report duplicate WXY coordinates observed during FIRST PASS.
    3. Check FP and Total Good status combination (both PASS, FP PASS / Total FAIL,
       FP FAIL / Total PASS, or both FAIL).
    If multiple failure violations occur (e.g. QC failure and duplicate WXY in FP),
    all violations are included in Remarks.
    """
    if result.get("is_sg406"):
        from apps.ftdc_checker.sg406_analysis import compute_sg406_remarks
        return compute_sg406_remarks(result)

    violations: List[str] = []

    # 1. First, check if all QC units have PASS result in ANY FIRST PASS or RETEST datalog (Check 4 logic)
    check4_result = str(result.get("check4_result") or "").strip().upper()
    qc_failed_qty = int(result.get("qc_failed_or_missing_prev_op_qty") or 0)
    if check4_result == "FAIL" or qc_failed_qty > 0:
        count = qc_failed_qty if qc_failed_qty > 0 else 1
        violations.append(
            f"There are {count} QC units that are either UNTESTED/TESTED REJECT "
            "from previous FT operation. Please hold lot for review and assessment."
        )

    # 2. Second, check if there is any duplicated wxy in FIRST PASS datalog
    fp_dup_cnt = int(result.get("fp_datalog_duplicate_count") or 0)
    if fp_dup_cnt == 0:
        fp_rows = (result.get("first_pass") or {}).get("rows") or []
        if fp_rows:
            w_counts: Dict[str, int] = {}
            for r in fp_rows:
                w = r.get("wxy")
                if _is_valid_wxy(w):
                    w_counts[w] = w_counts.get(w, 0) + 1
            fp_dup_cnt = sum(cnt - 1 for cnt in w_counts.values() if cnt >= 2)

    if fp_dup_cnt > 0:
        violations.append(
            "Duplicate WXY coordinate(s) observed during FIRST PASS. Please hold lot for review and assessment."
        )

    if violations:
        return " ".join(violations)

    # 3. Third, check FP and Total Good status combination
    fp_status = str(result.get("status") or "").strip().upper()
    tot_status = str(result.get("total_good_status") or "").strip().upper()

    fp_pass = (fp_status == "PASS")
    tot_pass = (tot_status == "PASS")

    if fp_pass and tot_pass:
        return "No abnormalities or discrepancies were observed."
    elif fp_pass and not tot_pass:
        return "There is abnormality or discrepancy observed in RETEST process. Please check."
    elif not fp_pass and tot_pass:
        return "There is abnormality or discrepancy observed in FIRST PASS process. Please check."
    else:
        return "There was an anomaly during lot processing. Please hold lot for review and assessment."
