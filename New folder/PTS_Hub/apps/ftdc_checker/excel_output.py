"""
Excel output module for GUID checker results.
Rewritten to use xlsxwriter for high-performance streaming Excel generation.
"""
import logging
from typing import Callable, Dict, List, Optional

from apps.ftdc_checker.guid_analysis import compute_remarks
from apps.ftdc_checker.guid_analysis import _is_valid_wxy as _shared_is_valid_wxy


def _is_valid_wxy(wxy: Optional[str]) -> bool:
    """Check if WXY coordinate is valid and not a placeholder/dummy
    (contains 'NaN', Wafer (first W_X_Y value) is 0 regardless of X/Y such as 0_1_2,
    or two/more W/X/Y components are zero such as 0_0_0 or 24_0_0)."""
    return _shared_is_valid_wxy(wxy)


def _fmt(value):
    if value is None or value == "":
        return "N/A"
    if isinstance(value, int):
        return f"{value:,}"
    try:
        if isinstance(value, float) and value.is_integer():
            return f"{int(value):,}"
    except Exception as exc:
        logging.warning("Failed to format numeric value %r: %s", value, exc)
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
    cache_sig = (
        result.get("is_sg406"),
        result.get("status"),
        result.get("total_good_status"),
        result.get("remarks"),
        result.get("check1_result"),
        result.get("check2_result"),
        result.get("check4_result"),
        result.get("first_pass_pass_count"),
        result.get("total_pass_parts"),
    )
    cached_entry = result.get("_cached_summary_and_details")
    if isinstance(cached_entry, tuple) and len(cached_entry) == 2 and cached_entry[0] == cache_sig:
        return cached_entry[1]

    if result.get("is_sg406"):
        from apps.ftdc_checker.sg406_analysis import build_sg406_summary_and_details
        out = build_sg406_summary_and_details(result, _hard_bin_equals, _soft_bin_equals)
        result["_cached_summary_and_details"] = (cache_sig, out)
        return out

    def panel_data(panel_key: str) -> Dict[str, object]:
        data = result.get(panel_key) or {}
        return data if isinstance(data, dict) else {}

    def panel_rows(panel_key: str) -> List[Dict[str, object]]:
        rows = panel_data(panel_key).get("rows") or []
        return rows if isinstance(rows, list) else []

    fp_rows = panel_rows("first_pass")
    retest_rows = panel_rows("retest")

    fp_hard_bin3_rejects = 0
    for r in fp_rows:
        if r.get("pass_fail") == "FAIL" and _hard_bin_equals(r, 3):
            fp_hard_bin3_rejects += 1
    retest_bin90 = sum(1 for r in retest_rows if _soft_bin_equals(r, 90))

    fp_status = result.get("status", "")
    total_status = result.get("total_good_status") or "SKIPPED"
    qc_info = panel_data("qc")

    summary_sections = [
        (
            "FIRST PASS",
            [
                ("First Pass STDF Good", result.get("first_pass_pass_count")),
                ("Duplicate Good UID", result.get("duplicate_guid")),
                ("First Pass Good w/o Duplicate UID", result.get("calculated_first_pass_qty")),
                ("First Pass Actual Good", result.get("first_pass_actual_good_qty")),
                ("Difference between Good w/o Duplicate vs FP Actual Good", result.get("delta_vs_fp_actual_good")),
                ("Total QC Units Good At Prev. Operation", result.get("qc_sampling_good_in_prev_op")),
                ("Result", fp_status),
            ],
        ),
        (
            "TOTAL (FP + RETEST)",
            [
                ("STDF Total Good", result.get("total_pass_parts")),
                ("Duplicate Good UID", result.get("total_duplicate_guid")),
                ("Total Good w/o Duplicate UID", result.get("total_good")),
                ("Actual Total Good", result.get("total_actual_good_qty")),
                ("Difference between Good w/o Duplicate vs Actual Total Good", result.get("delta_vs_total_actual_good")),
                ("Total QC Tested Good units", qc_info.get("pass_count")),
                ("Result", total_status),
            ],
        ),
    ]

    wxy_tests = result.get("wxy_tests") or {}
    remarks = str(result.get("remarks") or compute_remarks(result))

    detail_rows = [
        ("General", "Lot ID", result.get("lot_id")),
        ("General", "MPC", result.get("mpc")),
        ("General", "Remarks", remarks),
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
        ("QC", "Total parts", qc_info.get("total_parts")),
        ("QC", "PASS parts", qc_info.get("pass_count")),
        ("QC", "FAIL parts", qc_info.get("fail_count")),
        ("QC", "Missing WXY", qc_info.get("missing_wxy")),
        ("QC", "Duplicate GUID within QC", result.get("qc_duplicate_guid")),
        ("QC", "QC Sampling units good in previous operation", result.get("qc_sampling_good_in_prev_op")),
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
        "remarks": remarks,
        "delta_vs_total_actual_good": result.get("delta_vs_total_actual_good"),
        "delta_vs_fp_actual_good": result.get("delta_vs_fp_actual_good"),
        "total_duplicate_guid": result.get("total_duplicate_guid"),
        "fp_datalog_duplicate_count": result.get("fp_datalog_duplicate_count", 0),
        "qc_failed_or_missing_prev_op_qty": result.get("qc_failed_or_missing_prev_op_qty"),
        "qc_failed_or_missing_prev_op_wxy": result.get("qc_failed_or_missing_prev_op_wxy"),
    }

    extra = {"fp_hard_bin3_rejects": fp_hard_bin3_rejects, "retest_bin90": retest_bin90}
    out = (summary_sections, detail_rows, fp_status, total_status, extra, ftdc_criteria)
    result["_cached_summary_and_details"] = (cache_sig, out)
    return out


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
    if ftdc_criteria.get("is_sg406"):
        from apps.ftdc_checker.sg406_analysis import build_sg406_ftdc_criteria_text
        return build_sg406_ftdc_criteria_text(ftdc_criteria)

    check1 = ftdc_criteria.get("check1", "N/A")
    check2 = ftdc_criteria.get("check2", "N/A")
    check4 = ftdc_criteria.get("check4", "N/A")
    fp_status = ftdc_criteria.get("fp_status", "")
    total_status = ftdc_criteria.get("total_status", "")
    delta_vs_total_actual_good = ftdc_criteria.get("delta_vs_total_actual_good", "")
    delta_vs_fp_actual_good = ftdc_criteria.get("delta_vs_fp_actual_good", "")
    total_duplicate_guid = ftdc_criteria.get("total_duplicate_guid", "")
    fp_datalog_duplicate_count = ftdc_criteria.get("fp_datalog_duplicate_count", 0)
    qc_failed_or_missing_prev_op_qty = ftdc_criteria.get("qc_failed_or_missing_prev_op_qty", "")
    qc_failed_or_missing_prev_op_wxy = ftdc_criteria.get("qc_failed_or_missing_prev_op_wxy", "")
    remarks = ftdc_criteria.get("remarks", "")

    lines = ["[FTDC Criteria]"]
    if remarks:
        lines.append(f"Remarks: {remarks}")
        lines.append("")
    lines.append("[Check 1] - If Total Actual Good QTY is greater than Total Tested Good w/o Duplicate UID, then result will be 'FAIL'. Otherwise, result will be 'PASS'.")
    if check1 == "FAIL":
        if fp_datalog_duplicate_count and int(fp_datalog_duplicate_count) > 0:
            lines.append(
                f"[Result: {check1}] - {fp_datalog_duplicate_count} duplicate WXY unit(s) in FIRST PASS datalog | "
                f"{delta_vs_fp_actual_good} excess units in FP actual compared to STDF | "
                f"{delta_vs_total_actual_good} excess units in TOTAL actual compared to STDF"
            )
        else:
            lines.append(f"[Result: {check1}] - {delta_vs_fp_actual_good} excess units in FP actual compared to STDF | {delta_vs_total_actual_good} excess units in TOTAL actual compared to STDF")
    else:
        lines.append(f"[Result: {check1}] - No negative variance was detected.")

    lines.append("")

    lines.append("[Check 2] - If there are too many good units that were retested, then result will be 'FAIL'.")
    if check2 == "FAIL":
        lines.append(f"[Result: {check2}] - There are total of {total_duplicate_guid} duplicate good units which exceeds the limit of 72 units.")
    else:
        lines.append(f"[Result: {check2}] - Total duplicate good units are less than or equal to 72 units.")

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


def write_result_excel(
    result: Dict[str, object],
    save_path: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
):
    """Write GUID analysis result to an Excel file using xlsxwriter."""
    if result.get("is_sg406"):
        from apps.ftdc_checker.sg406_analysis import write_sg406_result_excel
        return write_sg406_result_excel(
            result=result,
            save_path=save_path,
            fmt_fn=_fmt,
            to_num_fn=_to_num,
            col_tracker_cls=_ColTracker,
            hard_bin_equals_fn=_hard_bin_equals,
            soft_bin_equals_fn=_soft_bin_equals,
            progress_callback=progress_callback,
        )

    import xlsxwriter

    if progress_callback:
        progress_callback(0.05, "Initializing Excel workbook…")

    wb = xlsxwriter.Workbook(save_path, {'constant_memory': False, 'strings_to_urls': False})

    # ── Format definitions ────────────────────────────────────────────────
    hdr_fmt = wb.add_format({
        'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#4472C4',
        'align': 'center', 'valign': 'vcenter',
        'border': 1,
    })
    subhdr_fmt = wb.add_format({
        'bold': True, 'bg_color': '#EDF2F7',
        'align': 'center', 'valign': 'vcenter',
        'border': 1,
    })
    border_fmt = wb.add_format({'border': 1, 'valign': 'vcenter'})
    center_border_fmt = wb.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter'})
    green_center_fmt = wb.add_format({
        'border': 1, 'bg_color': '#90EE90', 'bold': True,
        'align': 'center', 'valign': 'vcenter',
    })
    red_center_fmt = wb.add_format({
        'border': 1, 'bg_color': '#FF5050', 'bold': True,
        'align': 'center', 'valign': 'vcenter',
    })
    center_fmt = wb.add_format({'align': 'center', 'valign': 'vcenter'})
    summary_sections, detail_rows, _, _, extra, _ = build_summary_and_details(result)

    # ══════════════════════════════════════════════════════════════════════
    # Summary worksheet
    # ══════════════════════════════════════════════════════════════════════
    ws_sum = wb.add_worksheet("Summary")
    sum_tracker = _ColTracker()

    # The Remarks entry is intentionally excluded from the GENERAL table of the
    # Summary worksheet (remarks are still shown in the row below the panels).
    general_rows = [(m, v) for s, m, v in detail_rows if s == "General" and m != "Remarks"]

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

    # Comparative matrix rows for First Pass and Total Good (aligned with FTDC App Pop-up Result Window)
    fp_stdf = result.get("first_pass_pass_count")
    fp_dup = result.get("duplicate_guid", 0)
    if fp_dup is None:
        fp_dup = 0
    fp_good_wo_dup = result.get("calculated_first_pass_qty")
    fp_actual = result.get("first_pass_actual_good_qty")
    fp_diff = result.get("delta_vs_fp_actual_good")
    if fp_diff is None and fp_good_wo_dup is not None and fp_actual is not None:
        try:
            fp_diff = int(fp_good_wo_dup) - int(fp_actual)
        except (ValueError, TypeError):
            pass
    fp_qc = result.get("qc_sampling_good_in_prev_op")
    fp_status = str(result.get("status", "") or "").strip().upper()

    tot_stdf = result.get("total_pass_parts")
    tot_dup = result.get("total_duplicate_guid", 0)
    if tot_dup is None:
        tot_dup = 0
    tot_good_wo_dup = result.get("total_good")
    tot_actual = result.get("total_actual_good_qty")
    tot_status = str(result.get("total_good_status") or "SKIPPED").strip().upper()
    tot_diff = result.get("delta_vs_total_actual_good")
    if tot_diff is None and tot_good_wo_dup is not None and tot_actual is not None and tot_status != "SKIPPED":
        try:
            tot_diff = int(tot_good_wo_dup) - int(tot_actual)
        except (ValueError, TypeError):
            pass
    qc_pass = (result.get("qc") or {}).get("pass_count") if isinstance(result.get("qc"), dict) else None

    fp_matrix_rows = [
        ("First Pass STDF Good", fp_stdf, "normal"),
        ("Duplicate Good UID", fp_dup, "normal"),
        ("First Pass Good w/o Duplicate UID", fp_good_wo_dup, "normal"),
        ("First Pass Actual Good", fp_actual, "normal"),
        ("Difference between Good w/o Duplicate vs FP Actual Good", fp_diff, "diff"),
        ("Total QC Units Good At Prev. Operation", fp_qc, "normal"),
        ("Result", fp_status, "result"),
    ]

    tot_matrix_rows = [
        ("STDF Total Good", tot_stdf, "normal"),
        ("Duplicate Good UID", tot_dup, "normal"),
        ("Total Good w/o Duplicate UID", tot_good_wo_dup, "normal"),
        ("Actual Total Good", tot_actual, "normal"),
        ("Difference between Good w/o Duplicate vs Actual Total Good", tot_diff, "diff"),
        ("Total QC Tested Good units", qc_pass, "normal"),
        ("Result", tot_status, "result"),
    ]

    # Side-by-side tables: (col_start, table_title, rows, overall_status, is_total)
    tables = [
        (0, "FIRST PASS", fp_matrix_rows, fp_status, False),
        (3, "TOTAL (FP + RETEST)", tot_matrix_rows, tot_status, True),
    ]

    for c_start, tbl_title, rows_data, overall_st, is_tot in tables:
        # Table Header (aligned with FTDC App Pop-up Result Window)
        ws_sum.merge_range(start_row_for_panels, c_start, start_row_for_panels, c_start + 1, tbl_title, hdr_fmt)
        sum_tracker.track(c_start, tbl_title)

        # Table Data Rows
        for r_offset, (m_label, val, r_type) in enumerate(rows_data, start=1):
            curr_r = start_row_for_panels + r_offset
            # Metric label cell: standard border, NO highlight
            ws_sum.write(curr_r, c_start, m_label, border_fmt)
            sum_tracker.track(c_start, m_label)

            # Highlighting applies ONLY to Difference and Result
            if r_type == "diff":
                if val is None or str(val).strip().upper() in ("N/A", ""):
                    v_str = "N/A"
                    cell_fmt = center_border_fmt
                else:
                    try:
                        num = float(str(val).replace(',', ''))
                        v_str = f"{int(num):,}" if num.is_integer() else f"{num:,.2f}"
                        cell_fmt = green_center_fmt if num >= 0 else red_center_fmt
                    except (ValueError, TypeError):
                        v_str = _fmt(val)
                        cell_fmt = center_border_fmt
            elif r_type == "result":
                res_str = str(val or "").strip().upper()
                v_str = res_str
                if res_str == "PASS":
                    cell_fmt = green_center_fmt
                elif res_str == "FAIL":
                    cell_fmt = red_center_fmt
                else:
                    cell_fmt = center_border_fmt
            else:
                # Normal rows (STDF Good, Duplicate Good UID, Good w/o Duplicate UID, Actual Good, QC Good): NO highlight!
                v_str = _fmt(val)
                cell_fmt = center_border_fmt

            ws_sum.write(curr_r, c_start + 1, v_str, cell_fmt)
            sum_tracker.track(c_start + 1, v_str)

    # Spacer column between tables (Col C = index 2)
    ws_sum.set_column(2, 2, 4)

    # Remarks row spanning across summary panels (Cols B to E)
    remarks_text = str(result.get("remarks") or compute_remarks(result))
    remarks_row = start_row_for_panels + len(fp_matrix_rows) + 2
    ws_sum.write(remarks_row, 0, "Remarks:", border_fmt)
    ws_sum.merge_range(remarks_row, 1, remarks_row, 4, remarks_text, border_fmt)
    sum_tracker.track(0, "Remarks:")

    sum_tracker.apply(ws_sum)

    if progress_callback:
        progress_callback(0.15, "Writing FT Total worksheet…")

    # ══════════════════════════════════════════════════════════════════════
    # FT Total worksheet
    # ══════════════════════════════════════════════════════════════════════
    fp_panel = result.get("first_pass") or {}
    rt_panel = result.get("retest") or {}
    fp_rows_list = fp_panel.get("rows") or []
    rt_rows_list = rt_panel.get("rows") or []
    ft_all_rows = list(fp_rows_list) + list(rt_rows_list)

    qc_panel = result.get("qc") or {}
    qc_rows_list = qc_panel.get("rows") or []
    total_excel_rows = max(len(ft_all_rows) + len(qc_rows_list), 1)
    written_rows = 0

    # Build ft_lookup OUTSIDE the if block so QC always has access.
    # If a WXY is seen with PASS in any FP or RETEST datalog, preserve that PASS entry
    # so a later FAIL retest never masks a unit that passed in a previous FT step.
    ft_lookup = {}
    for part_num, row in enumerate(ft_all_rows, start=1):
        wxy = row.get("wxy")
        if _is_valid_wxy(wxy):
            pf_val = row.get("pass_fail", "")
            existing = ft_lookup.get(wxy)
            if existing is None or existing.get("result") != "PASS" or pf_val == "PASS":
                ft_lookup[wxy] = {
                    "result": pf_val,
                    "part_num": part_num,
                    "datalog": row.get("panel", ""),
                }

    ft_headers = [
        "Total Parts", "Part ID", "Site", "Test Time (ms)", "Hard Bin", "Soft Bin",
        "Pass/Fail", "Wafer (W)", "X", "Y", "WXY (W_X_Y)", "Datalog",
        "Duplicate", "Reject In Retest",
    ]
    qc_headers = [
        "Total Parts", "Part ID", "Site", "Test Time (ms)", "Hard Bin", "Soft Bin",
        "Pass/Fail", "Wafer (W)", "X", "Y", "WXY (W_X_Y)",
        "Datalog", "Result in previous FT step",
        "Part Number in previous FT step", "Datalog in previous FT step",
    ]

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
        ws_ft.write_row(parts_start_row, 0, ft_headers, hdr_fmt)
        ft_tracker.track_row(0, ft_headers)

        # ── Parts data (batch write_row for high speed) ───────────────────
        row_get = dict.get
        write_ft_row = ws_ft.write_row
        for part_num, row in enumerate(ft_all_rows, start=1):
            r = parts_start_row + part_num
            values = (
                part_num,
                row_get(row, "part_id", ""),
                row_get(row, "site_num", ""),
                row_get(row, "test_t", ""),
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
            )
            write_ft_row(r, 0, values, center_fmt)
            if part_num <= 100:
                ft_tracker.track_row(0, values)
            written_rows += 1
            if progress_callback and (written_rows & 255) == 0:
                frac = 0.15 + 0.60 * (written_rows / total_excel_rows)
                progress_callback(frac, f"Writing FT Total rows ({part_num:,}/{len(ft_all_rows):,})…")

        ft_tracker.apply(ws_ft)

    # ══════════════════════════════════════════════════════════════════════
    # QC Total worksheet
    # ══════════════════════════════════════════════════════════════════════
    if qc_rows_list:
        if progress_callback:
            progress_callback(0.75, "Writing QC Total worksheet…")
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
        ws_qc.write_row(qc_parts_start_row, 0, qc_headers, hdr_fmt)
        qc_tracker.track_row(0, qc_headers)

        # ── QC parts data (batch write_row for high speed) ────────────────
        ft_lookup_get = ft_lookup.get
        row_get = dict.get
        write_qc_row = ws_qc.write_row
        for part_num, row in enumerate(qc_rows_list, start=1):
            r = qc_parts_start_row + part_num
            wxy = row_get(row, "wxy", "")
            ft_info = ft_lookup_get(wxy, {})

            values = (
                part_num,
                row_get(row, "part_id", ""),
                row_get(row, "site_num", ""),
                row_get(row, "test_t", ""),
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
            )
            write_qc_row(r, 0, values, center_fmt)
            if part_num <= 100:
                qc_tracker.track_row(0, values)
            written_rows += 1
            if progress_callback and (written_rows & 255) == 0:
                frac = 0.15 + 0.65 * (written_rows / total_excel_rows)
                progress_callback(frac, f"Writing QC Total rows ({part_num:,}/{len(qc_rows_list):,})…")

        qc_tracker.apply(ws_qc)

    # ══════════════════════════════════════════════════════════════════════
    # Duplicate GUID First Pass worksheet (FT Total format without summary metrics)
    # ══════════════════════════════════════════════════════════════════════
    if progress_callback:
        progress_callback(0.84, "Writing Duplicate GUID First Pass worksheet…")
    fp_wxy_counts: Dict[str, int] = {}
    for r in fp_rows_list:
        w = r.get("wxy")
        if _is_valid_wxy(w):
            fp_wxy_counts[w] = fp_wxy_counts.get(w, 0) + 1
    fp_dup_wxy_set = {w for w, cnt in fp_wxy_counts.items() if cnt >= 2}
    fp_dup_rows = [
        r for r in fp_rows_list
        if r.get("is_fp_datalog_duplicate") or (r.get("wxy") in fp_dup_wxy_set)
    ]

    ws_fp_dup = wb.add_worksheet("Duplicate GUID First Pass")
    fp_dup_tracker = _ColTracker()
    ws_fp_dup.write_row(0, 0, ft_headers, hdr_fmt)
    fp_dup_tracker.track_row(0, ft_headers)

    row_get = dict.get
    write_fp_dup_row = ws_fp_dup.write_row
    seen_fp_dup_wxy = set()
    for part_num, row in enumerate(fp_dup_rows, start=1):
        wxy_val = row_get(row, "wxy", "")
        dup_label = row_get(row, "is_total_duplicate", "")
        if dup_label not in ("FP Original", "FP Duplicate"):
            if wxy_val in seen_fp_dup_wxy:
                dup_label = "FP Duplicate"
            else:
                dup_label = "FP Original"
        if wxy_val:
            seen_fp_dup_wxy.add(wxy_val)
        values = (
            part_num,
            row_get(row, "part_id", ""),
            row_get(row, "site_num", ""),
            row_get(row, "test_t", ""),
            row_get(row, "hard_bin", ""),
            row_get(row, "soft_bin", ""),
            row_get(row, "pass_fail", ""),
            row_get(row, "wafer", ""),
            row_get(row, "x", ""),
            row_get(row, "y", ""),
            wxy_val,
            row_get(row, "panel", ""),
            dup_label,
            row_get(row, "reject_in_retest", "N/A"),
        )
        write_fp_dup_row(part_num, 0, values, center_fmt)
        if part_num <= 100:
            fp_dup_tracker.track_row(0, values)
    fp_dup_tracker.apply(ws_fp_dup)

    # ══════════════════════════════════════════════════════════════════════
    # QC units FAIL in prev FT Step worksheet (QC Total format without summary metrics)
    # Strictly based on Check 4 business rule (qc_failed_or_missing_prev_op_wxy / is_qc_fail_prev_step)
    # ══════════════════════════════════════════════════════════════════════
    ft_lookup_get = ft_lookup.get
    check4_fail_wxy = set(result.get("qc_failed_or_missing_prev_op_wxy") or [])

    if check4_fail_wxy or "qc_failed_or_missing_prev_op_wxy" in result:
        qc_fail_prev_rows = [
            r for r in qc_rows_list
            if r.get("is_qc_fail_prev_step") or (r.get("wxy") in check4_fail_wxy)
        ]
    else:
        # Fallback applying the identical Check 4 business rule:
        # QC units with valid WXY not seen with PASS in previous FT steps (total good)
        qc_fail_prev_rows = [
            r for r in qc_rows_list
            if _is_valid_wxy(r.get("wxy"))
            and ft_lookup_get(r.get("wxy", ""), {}).get("result") != "PASS"
        ]

    ws_qc_fail = wb.add_worksheet("QC units FAIL in prev FT Step")
    qc_fail_tracker = _ColTracker()
    ws_qc_fail.write_row(0, 0, qc_headers, hdr_fmt)
    qc_fail_tracker.track_row(0, qc_headers)

    write_qc_fail_row = ws_qc_fail.write_row
    for part_num, row in enumerate(qc_fail_prev_rows, start=1):
        wxy = row_get(row, "wxy", "")
        ft_info = ft_lookup_get(wxy, {})
        values = (
            part_num,
            row_get(row, "part_id", ""),
            row_get(row, "site_num", ""),
            row_get(row, "test_t", ""),
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
        )
        write_qc_fail_row(part_num, 0, values, center_fmt)
        if part_num <= 100:
            qc_fail_tracker.track_row(0, values)
    qc_fail_tracker.apply(ws_qc_fail)

    if progress_callback:
        progress_callback(0.85, "Finalizing Excel workbook…")
    wb.close()
    if progress_callback:
        progress_callback(1.0, "Excel workbook generated")
