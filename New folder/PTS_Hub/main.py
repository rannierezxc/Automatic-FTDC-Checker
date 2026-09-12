"""
PTS Hub - Main Application Entry Point (Desktop GUI + CLI).

Launches the unified multi-application hub featuring:
  - FTDC Checker
  - STDF2SUM Converter
  - Data Analysis Hub
"""
import os
import sys
import multiprocessing

# Ensure PTS_Hub directory is on sys.path for direct script and package execution
HUB_DIR = os.path.dirname(os.path.abspath(__file__))
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)
FTDC_DIR = os.path.join(HUB_DIR, "apps", "ftdc_checker")
if FTDC_DIR not in sys.path:
    sys.path.insert(0, FTDC_DIR)

# Set Windows AppUserModelID for proper taskbar grouping
try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PTS_Hub")
except Exception:
    pass

from core.config import APP_NAME, APP_VERSION, ICON_PATH
from core.theme import setup_theme
from core.hub_window import HubWindow
from apps.ftdc_checker.ui import FTDCCheckerFrame
from apps.stdf2sum.ui import STDF2SumFrame
from apps.data_analysis.ui import DataAnalysisFrame
from apps.ftdc_checker.guid_analysis import analyze_guid_data
from apps.ftdc_checker.stdf_parser import scan_test_list


def launch_hub():
    """Initialize and run the primary PTS Hub desktop application."""
    import tkinter as tk

    root = tk.Tk()
    setup_theme(root)

    root.title(f"{APP_NAME} {APP_VERSION}")
    try:
        if os.path.isfile(ICON_PATH):
            root.iconbitmap(default=ICON_PATH)
    except Exception:
        pass

    root.geometry("1260x820")
    root.minsize(1060, 680)

    # Create Hub Window
    hub = HubWindow(root)

    # Instantiate Application Frames inside the Hub content area
    ftdc_frame = FTDCCheckerFrame(hub.content_area, hub=hub)
    stdf2sum_frame = STDF2SumFrame(hub.content_area, hub=hub)
    data_analysis_frame = DataAnalysisFrame(hub.content_area, hub=hub)

    # Register tabs in the Hub
    hub.register_app("FTDC Checker", ftdc_frame, is_default=True)
    hub.register_app("STDF2SUM", stdf2sum_frame)
    hub.register_app("Data Analysis", data_analysis_frame)

    root.mainloop()


def main():
    """Command-line entry point with automatic GUI fallback."""
    import argparse

    parser = argparse.ArgumentParser(description=f"{APP_NAME} {APP_VERSION} (Multi-App Engineering Hub)")
    parser.add_argument("--gui", "--app", action="store_true", help="Launch desktop GUI app")
    parser.add_argument("--first-pass", nargs="+", help="FIRST PASS STDF paths")
    parser.add_argument("--retest", nargs="+", help="RETEST STDF paths")
    parser.add_argument("--qc", nargs="+", default=[], help="QC STDF paths")
    parser.add_argument("--lot-id", default="", help="Lot ID")
    parser.add_argument("--mpc", default="", help="MPC value")
    parser.add_argument("--fp-actual-good-qty", help="First Pass Actual Good QTY")
    parser.add_argument("--total-actual-good-qty", help="Total Actual Good QTY")
    parser.add_argument("-q", "--quiet", action="store_true")
    filt = parser.add_argument_group("Manual PTR test filtering")
    filt.add_argument("--tests", nargs="+", type=int, metavar="N")
    filt.add_argument("--test-range", nargs=2, type=int, metavar=("FROM", "TO"))
    filt.add_argument("--list-tests", metavar="FILE")
    args = parser.parse_args()

    if args.gui or (not args.first_pass and not args.retest and not args.list_tests):
        launch_hub()
        return

    if args.list_tests:
        if not os.path.exists(args.list_tests):
            print(f"Error: file not found: {args.list_tests}", file=sys.stderr)
            sys.exit(1)
        tests = scan_test_list(args.list_tests)
        if not tests:
            print("No PTR records found.")
        else:
            print(f"{'TEST_NUM':>10}  {'TEST_TXT':<30}  {'LO_LIMIT':>12}  {'HI_LIMIT':>12}  UNITS")
            print("-" * 78)
            for t in tests:
                lo = f"{t['LO_LIMIT']:.6g}" if t["LO_LIMIT"] == t["LO_LIMIT"] else "n/a"
                hi = f"{t['HI_LIMIT']:.6g}" if t["HI_LIMIT"] == t["HI_LIMIT"] else "n/a"
                print(f"{t['TEST_NUM']:>10}  {t['TEST_TXT']:<30}  {lo:>12}  {hi:>12}  {t['UNITS']}")
        return

    manual_filter_tests = None
    if args.tests or args.test_range:
        manual_filter_tests = set(args.tests or [])
        if args.test_range:
            lo, hi = args.test_range
            manual_filter_tests |= set(range(lo, hi + 1))

    def cli_log(msg):
        if not args.quiet:
            print(msg)

    result = analyze_guid_data(
        first_pass_paths=args.first_pass, retest_paths=args.retest,
        qc_paths=args.qc, lot_id=args.lot_id, mpc_text=args.mpc,
        first_pass_actual_good_qty=args.fp_actual_good_qty,
        total_actual_good_qty=args.total_actual_good_qty,
        manual_filter_tests=manual_filter_tests, logger=cli_log,
    )
    print(f"\\nDone. FP Result: {result['status']} | Total Good: {result.get('total_good_status')}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
