"""Script to extract GUI class from v25 and create gui_app.py for the optimized package."""
import os

SRC = r"c:\Users\ranniereramirez\STDF2CSV\stdf_wxy_duplicate_guid_checker_v25.py"
DST = r"c:\Users\ranniereramirez\STDF2CSV\optimized\gui_app.py"

with open(SRC, "r") as f:
    lines = f.readlines()

# Find GUI class boundaries
start_idx = next(i for i, l in enumerate(lines) if "class STDFGuidCheckerApp" in l)
end_idx = next(i for i, l in enumerate(lines) if l.startswith("def launch_desktop_app"))

gui_lines = lines[start_idx:end_idx]

header = [
    '"""',
    "Tkinter GUI for the optimized GUID checker.",
    "Extracted from v25 monolith. Uses optimized parser and analysis modules.",
    '"""',
    "import os",
    "import re",
    "import struct",
    "import threading",
    "import traceback",
    "import tkinter.font as tkfont",
    "from collections import defaultdict",
    "from datetime import datetime, timezone",
    "from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple",
    "",
    "from .stdf_parser import (",
    "    STDFReader, scan_test_list, parse_filter_values, parse_stdf_file,",
    "    sort_paths_by_modified, _count_files_text, _format_limit_text,",
    "    _emit_log, _format_stdf_timestamp, _prepare_scanned_test_row,",
    "    ByteProgressFunc, LogFunc, ProgressFunc,",
    ")",
    "from .guid_analysis import (",
    "    analyze_guid_data, parse_panel_wxy_parts, resolve_wxy_test_numbers,",
    "    MPC_WXY_TEST_MAP, WXY_KEYS, UNSUPPORTED_MPC_WXY_MESSAGE,",
    "    normalize_mpc_key, _parse_wxy_from_mpc_text, _format_coord_value,",
    ")",
    "from .excel_output import (",
    "    write_result_excel, build_summary_and_details, build_details_text, _fmt,",
    ")",
    "",
    "",
    "def _path_modified_sort_key(path):",
    "    try:",
    "        modified_time = os.path.getmtime(path)",
    "    except OSError:",
    '        modified_time = float("inf")',
    "    return modified_time, os.path.basename(path).lower()",
    "",
    "",
    "def _merge_test_rows(rows):",
    "    merged = {}",
    "    for row in rows:",
    "        try:",
    '            test_num = int(row.get("TEST_NUM"))',
    "        except (TypeError, ValueError):",
    "            continue",
    "        if test_num not in merged:",
    "            merged[test_num] = dict(row)",
    "        else:",
    "            existing = merged[test_num]",
    '            if not existing.get("TEST_TXT") and row.get("TEST_TXT"):',
    '                existing["TEST_TXT"] = row.get("TEST_TXT", "")',
    '            if not existing.get("UNITS") and row.get("UNITS"):',
    '                existing["UNITS"] = row.get("UNITS", "")',
    '            existing["_SEARCH_TEXT"] = " ".join([',
    '                str(existing.get("TEST_NUM", "")), str(existing.get("TEST_TXT", "")),',
    '                str(existing.get("UNITS", "")), str(existing.get("_LO_LIMIT_TEXT", "")),',
    '                str(existing.get("_HI_LIMIT_TEXT", "")),',
    "            ]).lower()",
    '    return sorted(merged.values(), key=lambda item: int(item["TEST_NUM"]))',
    "",
    "",
]

with open(DST, "w") as f:
    for line in header:
        f.write(line + "\n")
    for line in gui_lines:
        f.write(line)

print(f"Created {DST} with {len(header) + len(gui_lines)} lines")
