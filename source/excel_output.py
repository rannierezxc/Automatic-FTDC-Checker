"""
Excel output module for GUID checker results.
Rewritten to use xlsxwriter for high-performance streaming Excel generation.
"""
import re
from datetime import datetime
from typing import Dict, List, Tuple


def _fmt(value):
    if value is None or value == "":
        return "N/A"
    if isinstance(value, int):
        return f"{value:,}"
    try:
        if isinstance(value, float) and value.is_integer():
            return f"{int(value):,}"
    except Exception:
        pass
    return str(value)


def _soft_bin_equals(row: Dict[str, object], bin_num: int) -> bool:
    sb = row.get("soft_bin", "")
    if isinstance(sb, int):
        return sb == bin_num
    try:
        return int(float(str(sb).strip())) == bin_num
    except (TypeError, ValueError):
        return False


def _hard_bin_equals(row: Dict[str, object], bin_num: int) -> bool:
    hb = row.get("hard_bin", "")
    if isinstance(hb, int):
        return hb == bin_num
    try:
        return int(float(str(hb).strip())) == bin_num
    except (TypeError, ValueError):
        return False


def build_summary_and_details(result: Dict[str, object]):
    """Build summary_sections and detail_rows from a GUID analysis result."""

    def panel_data(panel_key: str) -> Dict[str, object]:
        data = result.get(panel_key) or {}
        return data if isinstance(data, dict) else {}

    def panel_rows(panel_key: str) -> List[Dict[str, object]]:
        rows = panel_data(panel_key).get("rows") or []
        return rows if isinstance(rows, list) else []

    fp_rows = panel_rows("first_pass")
    retest_rows = panel_rows("retest")
    qc_rows = panel_rows("qc")

    fp_bin90 = sum(1 for r in fp_rows if _soft_bin_equals(r, 90))
    retest_bin90 = sum(1 for r in retest_rows if _soft_bin_equals(r, 90))
    qc_bin90 = sum(1 for r in qc_rows if _soft_bin_equals(r, 90))
    total_bin90 = fp_bin90 + retest_bin90 + qc_bin90
    fp_hard_bin3_rejects = sum(
        1 for r in fp_rows
        if r.get("pass_fail") == "FAIL" and _hard_bin_equals(r, 3)
    )

    fp_status = result.get("status", "")
    total_status = result.get("total_good_status") or "SKIPPED"

    summary_sections = [
        (
            "FIRST PASS GOOD",
            [
                ("First Pass STDF Good", result.get("first_pass_pass_count")),
                ("Duplicate Good UID", result.get("duplicate_guid")),
                ("First Pass Good w/o Duplicate UID", result.get("calculated_first_pass_qty")),
                ("First Pass Actual Good", result.get("first_pass_actual_good_qty")),
                ("Difference between Good w/o Duplicate vs FP Actual Good", result.get("delta_vs_fp_actual_good")),
                ("Total QC Units Good At Prev. Operation", result.get("qc_sampling_good_in_prev_op") if result.get("qc") else "No QC files"),
                ("Result", fp_status),
            ],
        ),
        (
            "TOTAL GOOD",
            [
                ("STDF Total Good", result.get("total_pass_parts")),
                ("Duplicate Good UID", result.get("total_duplicate_guid")),
                ("Total Good w/o Duplicate UID", result.get("total_good")),
                ("Actual Total Good", result.get("total_actual_good_qty")),
                ("Difference between Good w/o Duplicate vs Actual Total Good", result.get("delta_vs_total_actual_good")),
                ("Total QC Tested Good units", result.get("qc", {}).get("pass_count") if result.get("qc") else "No QC files"),
                ("Result", total_status),
            ],
        ),
    ]

    wxy_tests = result.get("wxy_tests") or {}

    detail_rows = [
        ("General", "Lot ID", result.get("lot_id")),
        ("General", "MPC", result.get("mpc")),
        ("General", "Wafer Test Number", wxy_tests.get("wafer") if isinstance(wxy_tests, dict) else ""),
        ("General", "X Test Number", wxy_tests.get("x") if isinstance(wxy_tests, dict) else ""),
        ("General", "Y Test Number", wxy_tests.get("y") if isinstance(wxy_tests, dict) else ""),
        ("FT", "Duplicate Good UID", result.get("total_duplicate_guid")),
        ("FT", "FP rejects not in RETEST", result.get("fp_rejects_not_in_retest")),
        ("FT", "BIN3 NO RETEST", fp_hard_bin3_rejects),
        ("FT", "FIRST PASS Total parts", panel_data("first_pass").get("total_parts")),
        ("FT", "FIRST PASS PASS parts", panel_data("first_pass").get("pass_count")),
        ("FT", "FIRST PASS FAIL parts", panel_data("first_pass").get("fail_count")),
        ("FT", "FIRST PASS Missing WXY", panel_data("first_pass").get("missing_wxy")),
        ("FT", "RETEST Total parts", panel_data("retest").get("total_parts")),
        ("FT", "RETEST PASS parts", panel_data("retest").get("pass_count")),
        ("FT", "RETEST FAIL parts", panel_data("retest").get("fail_count")),
        ("FT", "RETEST Missing WXY", panel_data("retest").get("missing_wxy")),
        ("QC", "Total parts", panel_data("qc").get("total_parts") if result.get("qc") else "No QC files selected"),
        ("QC", "PASS parts", panel_data("qc").get("pass_count") if result.get("qc") else "No QC files selected"),
        ("QC", "FAIL parts", panel_data("qc").get("fail_count") if result.get("qc") else "No QC files selected"),
        ("QC", "Missing WXY", panel_data("qc").get("missing_wxy") if result.get("qc") else "No QC files selected"),
        ("QC", "Duplicate GUID within QC", result.get("qc_duplicate_guid") if result.get("qc") else "No QC files selected"),
        ("QC", "QC Sampling units good in previous operation", result.get("qc_sampling_good_in_prev_op") if result.get("qc") else "No QC files selected"),
        ("FT", "Calculated First Pass QTY", result.get("calculated_first_pass_qty")),
        ("FT", "Total PASS parts", result.get("total_pass_parts")),
        ("FT", "Total Good", result.get("total_good")),
    ]

    # FTDC Criteria check results
    check1 = result.get("check1_result", "N/A")
    check2 = result.get("check2_result", "N/A")
    check4 = result.get("check4_result", "N/A")

    ftdc_criteria = {
        "check1": check1,
        "check2": check2,
        "check4": check4,
        "fp_status": fp_status,
        "total_status": total_status,
        "delta_vs_total_actual_good": result.get("delta_vs_total_actual_good"),
        "delta_vs_fp_actual_good": result.get("delta_vs_fp_actual_good"),
        "total_duplicate_guid": result.get("total_duplicate_guid"),
        "qc_failed_or_missing_prev_op_qty": result.get("qc_failed_or_missing_prev_op_qty"),
        "qc_failed_or_missing_prev_op_wxy": result.get("qc_failed_or_missing_prev_op_wxy")

    }

    extra = {"fp_hard_bin3_rejects": fp_hard_bin3_rejects, "retest_bin90": retest_bin90}
    return summary_sections, detail_rows, fp_status, total_status, extra, ftdc_criteria


def build_details_text(detail_rows, ftdc_criteria=None, fmt=_fmt) -> str:
    lines = []
    current_section = None
    for section, metric, value in detail_rows:
        if section != current_section:
            if current_section == "General" and ftdc_criteria:
                # Insert FTDC Criteria section right after General
                lines.append("")
                lines.extend(_build_ftdc_criteria_text(ftdc_criteria))
            if lines:
                lines.append("")
            lines.append(f"[{section}]")
            current_section = section
        lines.append(f"{metric}: {fmt(value)}")

    # If General was the last section, still insert FTDC Criteria
    if current_section == "General" and ftdc_criteria:
        lines.append("")
        lines.extend(_build_ftdc_criteria_text(ftdc_criteria))

    return "\n".join(lines)


def _build_ftdc_criteria_text(ftdc_criteria: dict) -> list:
    """Build the [FTDC Criteria] section lines."""
    check1 = ftdc_criteria.get("check1", "N/A")
    check2 = ftdc_criteria.get("check2", "N/A")
    check4 = ftdc_criteria.get("check4", "N/A")
    fp_status = ftdc_criteria.get("fp_status", "")
    total_status = ftdc_criteria.get("total_status", "")
    delta_vs_total_actual_good = ftdc_criteria.get("delta_vs_total_actual_good", "")
    delta_vs_fp_actual_good = ftdc_criteria.get("delta_vs_fp_actual_good", "")
    total_duplicate_guid = ftdc_criteria.get("total_duplicate_guid", "")
    qc_failed_or_missing_prev_op_qty = ftdc_criteria.get("qc_failed_or_missing_prev_op_qty", "")
    qc_failed_or_missing_prev_op_wxy = ftdc_criteria.get("qc_failed_or_missing_prev_op_wxy", "")





    lines = ["[FTDC Criteria]"]
    lines.append("[Check 1] - If Total Actual Good QTY is greater than Total Tested Good w/o Duplicate UID, then result will be 'FAIL'. Otherwise, result will be 'PASS'.")
    if check1 == "FAIL":
        lines.append(f"[Result: {check1}] - {delta_vs_fp_actual_good} excess units in FP actual compared to STDF | {delta_vs_total_actual_good} excess units in TOTAL actual compared to STDF")
    else:
        lines.append(f"[Result: {check1}] - No negative variance was detected.")
    
    lines.append("")
    
    lines.append("[Check 2] - If there are too many good units that were retested, then result will be 'FAIL'.")
    if check2 == "FAIL":
        lines.append(f"[Result: {check2}] - There are total of {total_duplicate_guid} duplicate good units which exceeds the limit of 72 units.")
    else:
        lines.append(f"[Result: {check2}] - Total duplicate good units are less than 72 units.")

    lines.append("")
    lines.append("[Check 4] - If all QC samples are confirmed FT good parts, then result will be 'PASS'. Otherwise, it will be 'FAIL'.")
    if check4 == "FAIL":
        lines.append(f"[Result: {check4}] - {qc_failed_or_missing_prev_op_qty} unit/s in QC that did not PASS in previous operation")
        lines.append(f"QC UIDS: {', '.join(qc_failed_or_missing_prev_op_wxy)}")
    else:
        lines.append(f"[Result: {check4}] - All QC units are confirmed PASS in previous operation.")

    # Show failure reasons if either FP GOOD or TOTAL GOOD is FAIL
    if fp_status == "FAIL" or total_status == "FAIL":
        lines.append("")
        lines.append("These are the possible reasons for Check 1 and/or Check 4 Fail:")
        lines.append("1) Corrupted STDF - Please double check each STDF, retrieve all corrupted datalog.")
        lines.append("")
        lines.append("2) Wrong/Incomplete STDF files attached - Kindly make sure you attached the previous FT step stdf and all RETESTs stdf. Also, if the datalog/summary was cut off, then attach the continuation of that STDF/datalog.")
        lines.append("")
        lines.append("3) Duplicate WXY in FIRST PASS - Please verify and confirm if there are duplicate WXYs in the FIRST PASS stdf.")
        lines.append("")
        lines.append("4) Incorrect Actual Good QTY input - Kindly double check the ACTUAL GOOD QUANTITY, perform recount if possible.")

    return lines


def _to_num(value):
    """Convert value to a numeric type for Excel, fallback to string."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


class _ColTracker:
    """Tracks max character width per column for autofit.
    
    xlsxwriter is write-only, so we track widths during writes and apply at the end.
    """
    __slots__ = ('_widths',)

    def __init__(self):
        self._widths: Dict[int, int] = {}

    def track(self, col: int, value):
        """Update the tracked max width for a column based on a cell value."""
        if value is not None:
            length = len(str(value))
            if length > self._widths.get(col, 0):
                self._widths[col] = length

    def track_row(self, start_col: int, values):
        """Track widths for a sequence of values starting at start_col."""
        for i, v in enumerate(values):
            self.track(start_col + i, v)

    def apply(self, ws, padding: int = 2, max_width: int = 60):
        """Apply tracked widths to the worksheet."""
        for col, width in self._widths.items():
            ws.set_column(col, col, min(width + padding, max_width))


def write_result_excel(result: Dict[str, object], save_path: str):
    """Write GUID analysis result to an Excel file using xlsxwriter."""
    import xlsxwriter

    wb = xlsxwriter.Workbook(save_path, {'constant_memory': False})

    # ── Format definitions ────────────────────────────────────────────────
    hdr_fmt = wb.add_format({
        'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#4472C4',
        'align': 'center', 'valign': 'vcenter',
        'border': 1,
    })
    border_fmt = wb.add_format({'border': 1})
    border_fmt_right = wb.add_format({'border': 1, 'align': 'right'})
    green_border_fmt = wb.add_format({
        'border': 1, 'bg_color': '#90EE90',
    })
    red_border_fmt = wb.add_format({
        'border': 1, 'bg_color': '#FF5050',
    })
    center_fmt = wb.add_format({'align': 'center', 'valign': 'vcenter'})
    default_fmt = wb.add_format()

    summary_sections, detail_rows, _, _, extra, _ = build_summary_and_details(result)

    # ══════════════════════════════════════════════════════════════════════
    # Summary worksheet
    # ══════════════════════════════════════════════════════════════════════
    ws_sum = wb.add_worksheet("Summary")
    sum_tracker = _ColTracker()

    general_rows = [(m, v) for s, m, v in detail_rows if s == "General"]

    # "GENERAL" merged title row (row 0 in xlsxwriter, 0-indexed)
    row = 0
    ws_sum.merge_range(row, 0, row, 1, "GENERAL", hdr_fmt)
    sum_tracker.track(0, "GENERAL")
    row += 1

    # General data rows
    for metric, value in general_rows:
        formatted = _fmt(value)
        ws_sum.write(row, 0, metric, border_fmt)
        ws_sum.write(row, 1, formatted, border_fmt)
        sum_tracker.track(0, metric)
        sum_tracker.track(1, formatted)
        row += 1

    # 2-row gap before panels
    start_row_for_panels = row + 2
    col_offsets = [0, 5]  # 0-indexed: columns A and F

    for idx, (section_title, rows) in enumerate(summary_sections):
        col_offset = col_offsets[idx] if idx < len(col_offsets) else (idx * 5)

        # Panel title — merged across 2 columns
        ws_sum.merge_range(start_row_for_panels, col_offset,
                           start_row_for_panels, col_offset + 1,
                           section_title, hdr_fmt)
        sum_tracker.track(col_offset, section_title)

        row_offset = start_row_for_panels + 1
        for metric, value in rows:
            formatted = _fmt(value)
            ws_sum.write(row_offset, col_offset, metric, border_fmt)

            # Conditional formatting: determine fill color inline
            cell_fmt = border_fmt
            if "Difference between" in metric:
                try:
                    if value is not None and value != "":
                        val_num = float(value)
                        cell_fmt = green_border_fmt if val_num >= 0 else red_border_fmt
                except (ValueError, TypeError):
                    pass
            elif "Result" in metric:
                try:
                    if value is not None and value != "":
                        result_val = str(value)
                        cell_fmt = green_border_fmt if result_val == "PASS" else red_border_fmt
                except (ValueError, TypeError):
                    pass

            ws_sum.write(row_offset, col_offset + 1, formatted, cell_fmt)
            sum_tracker.track(col_offset, metric)
            sum_tracker.track(col_offset + 1, formatted)
            row_offset += 1

    sum_tracker.apply(ws_sum)

    # ══════════════════════════════════════════════════════════════════════
    # FT Total worksheet
    # ══════════════════════════════════════════════════════════════════════
    fp_panel = result.get("first_pass") or {}
    rt_panel = result.get("retest") or {}
    fp_rows_list = fp_panel.get("rows") or []
    rt_rows_list = rt_panel.get("rows") or []
    ft_all_rows = list(fp_rows_list) + list(rt_rows_list)

    # Build ft_lookup OUTSIDE the if block so QC always has access
    ft_lookup = {}
    for part_num, row in enumerate(ft_all_rows, start=1):
        wxy = row.get("wxy")
        if wxy:
            ft_lookup[wxy] = {
                "result": row.get("pass_fail", ""),
                "part_num": part_num,
                "datalog": row.get("panel", "")
            }

    if ft_all_rows:
        ws_ft = wb.add_worksheet("FT Total")
        ft_tracker = _ColTracker()

        # ── Summary stats table (3 column-pairs) ─────────────────────────
        ft_stats_groups = [
            (0, 1, [  # Columns A, B (0-indexed)
                ("Duplicate Good UID", result.get("total_duplicate_guid")),
                ("Rejects not in RETEST", result.get("fp_rejects_not_in_retest")),
                ("BIN3 NO RETEST", extra.get("fp_hard_bin3_rejects")),
                ("RETEST SBIN90", extra.get("retest_bin90")),
            ]),
            (3, 4, [  # Columns D, E
                ("FIRST PASS Total Parts", fp_panel.get("total_parts")),
                ("FIRST PASS Good Parts", fp_panel.get("pass_count")),
                ("FIRST PASS Fail Parts", fp_panel.get("fail_count")),
                ("FIRST PASS No WXY", fp_panel.get("missing_wxy")),
            ]),
            (6, 7, [  # Columns G, H
                ("RETEST Total Parts", rt_panel.get("total_parts")),
                ("RETEST Good Parts", rt_panel.get("pass_count")),
                ("RETEST Fail Parts", rt_panel.get("fail_count")),
                ("RETEST No WXY", rt_panel.get("missing_wxy")),
            ]),
        ]

        for col_m, col_v, stats_rows in ft_stats_groups:
            ws_ft.write(0, col_m, "Metric", hdr_fmt)
            ws_ft.write(0, col_v, "Value", hdr_fmt)
            ft_tracker.track(col_m, "Metric")
            ft_tracker.track(col_v, "Value")
            for r_idx, (metric, value) in enumerate(stats_rows, start=1):
                num_val = _to_num(value)
                ws_ft.write(r_idx, col_m, metric)
                ws_ft.write(r_idx, col_v, num_val)
                ft_tracker.track(col_m, metric)
                ft_tracker.track(col_v, num_val)

        # 1-row space after stats table (row 5 is empty, 0-indexed)
        parts_start_row = 6  # 0-indexed row 6

        # ── Parts list headers ────────────────────────────────────────────
        # ── Parts list headers ────────────────────────────────────────────
        ft_headers = [
            "Total Parts", "Part ID", "Site", "Test Time (ms)", "Hard Bin", "Soft Bin",
            "Pass/Fail", "Wafer (W)", "X", "Y", "WXY (W_X_Y)", "Datalog",
            "Duplicate", "Reject In Retest",
        ]
        for col_idx, label in enumerate(ft_headers):
            ws_ft.write(parts_start_row, col_idx, label, hdr_fmt)
            ft_tracker.track(col_idx, label)

        # ── Parts data ────────────────────────────────────────────────────
        row_get = dict.get
        for part_num, row in enumerate(ft_all_rows, start=1):
            r = parts_start_row + part_num
            values = [
                part_num,
                row_get(row, "part_id", ""),
                row_get(row, "site_num", ""),
                row_get(row, "test_t", ""),          # <--- ADD THIS LINE
                row_get(row, "hard_bin", ""),
                row_get(row, "soft_bin", ""),
                row_get(row, "pass_fail", ""),
                row_get(row, "wafer", ""),
                row_get(row, "x", ""),
                row_get(row, "y", ""),
                row_get(row, "wxy", ""),
                row_get(row, "panel", ""),
                row_get(row, "is_total_duplicate", "NO"),
                row_get(row, "reject_in_retest", "N/A"),
            ]
            for col_idx, val in enumerate(values):
                ws_ft.write(r, col_idx, val, center_fmt)
            ft_tracker.track_row(0, values)

        ft_tracker.apply(ws_ft)

    # ══════════════════════════════════════════════════════════════════════
    # QC Total worksheet
    # ══════════════════════════════════════════════════════════════════════
    qc_panel = result.get("qc") or {}
    qc_rows_list = qc_panel.get("rows") or []

    if qc_rows_list:
        ws_qc = wb.add_worksheet("QC Total")
        qc_tracker = _ColTracker()

        # ── QC stats table ────────────────────────────────────────────────
        qc_detail_rows = [(m, v) for s, m, v in detail_rows if s == "QC"]
        if qc_detail_rows:
            ws_qc.write(0, 0, "Metric", hdr_fmt)
            ws_qc.write(0, 1, "Value", hdr_fmt)
            qc_tracker.track(0, "Metric")
            qc_tracker.track(1, "Value")
            for r_idx, (metric, value) in enumerate(qc_detail_rows, start=1):
                num_val = _to_num(value)
                ws_qc.write(r_idx, 0, metric)
                ws_qc.write(r_idx, 1, num_val)
                qc_tracker.track(0, metric)
                qc_tracker.track(1, num_val)
            qc_parts_start_row = len(qc_detail_rows) + 2  # +1 header, +1 gap (0-indexed)
        else:
            qc_parts_start_row = 0

        # ── QC parts list headers ─────────────────────────────────────────
        # ── QC parts list headers ─────────────────────────────────────────
        qc_headers = [
            "Total Parts", "Part ID", "Site", "Test Time (ms)", "Hard Bin", "Soft Bin",
            "Pass/Fail", "Wafer (W)", "X", "Y", "WXY (W_X_Y)",
            "Datalog", "Result in previous FT step",
            "Part Number in previous FT step", "Datalog in previous FT step"
        ]
        for col_idx, label in enumerate(qc_headers):
            ws_qc.write(qc_parts_start_row, col_idx, label, hdr_fmt)
            qc_tracker.track(col_idx, label)

        # ── QC parts data ─────────────────────────────────────────────────
        ft_lookup_get = ft_lookup.get
        row_get = dict.get
        for part_num, row in enumerate(qc_rows_list, start=1):
            r = qc_parts_start_row + part_num
            wxy = row_get(row, "wxy", "")
            ft_info = ft_lookup_get(wxy, {})

            values = [
                part_num,
                row_get(row, "part_id", ""),
                row_get(row, "site_num", ""),
                row_get(row, "test_t", ""),          # <--- ADD THIS LINE
                row_get(row, "hard_bin", ""),
                row_get(row, "soft_bin", ""),
                row_get(row, "pass_fail", ""),
                row_get(row, "wafer", ""),
                row_get(row, "x", ""),
                row_get(row, "y", ""),
                wxy,
                row_get(row, "panel", ""),
                ft_info.get("result", "N/A"),
                ft_info.get("part_num", "N/A"),
                ft_info.get("datalog", "N/A"),
            ]
            for col_idx, val in enumerate(values):
                ws_qc.write(r, col_idx, val, center_fmt)
            qc_tracker.track_row(0, values)

        qc_tracker.apply(ws_qc)

    wb.close()