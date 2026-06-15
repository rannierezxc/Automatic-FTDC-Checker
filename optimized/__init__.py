"""
Optimized STDF WXY / Duplicate GUID Checker
============================================
Multi-module optimized version of stdf_wxy_duplicate_guid_checker_v25.py.

Key optimizations:
- Pre-compiled struct objects (zero format-string reconstruction)
- Inline PTR parsing (no dict-per-record in hot path)
- Integer-based record dispatch (no string comparisons)
- WXY-only result storage (3 values per part instead of 1000)
- Single-pass aggregation
- Dead code removed
"""

from optimized.stdf_parser import (
    STDFReader,
    parse_stdf_file,
    scan_test_list,
    parse_filter_values,
    sort_paths_by_modified,
)
from optimized.guid_analysis import (
    analyze_guid_data,
    parse_panel_wxy_parts,
    resolve_wxy_test_numbers,
    MPC_WXY_TEST_MAP,
    WXY_KEYS,
    normalize_mpc_key,
)
from optimized.excel_output import write_result_excel
from optimized.stdf_fetcher import (
    resolve_device_names,
    resolve_mpc_details,
    search_stdf_files,
    copy_stdf_files,
    load_mpc_partnumber_map,
    TESTER_NETWORK_PATHS,
)
from optimized.main import main, launch_desktop_app
