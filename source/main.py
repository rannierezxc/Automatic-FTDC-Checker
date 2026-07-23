"""Entry point for the optimized GUID checker (CLI + GUI).

Can be run as:
  python -m optimized          (package mode)
  python optimized/main.py     (direct script mode)
"""
import os
import sys

# Handle both direct execution and package import
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_pkg_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from stdf_parser import scan_test_list, parse_filter_values
from guid_analysis import analyze_guid_data


def launch_desktop_app():
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError("tkinter is not available.") from exc
    from gui_app import STDFGuidCheckerApp
    root = tk.Tk()
    STDFGuidCheckerApp(root)
    root.mainloop()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Optimized STDF WXY / Duplicate GUID Checker")
    parser.add_argument("--gui", "--app", action="store_true", help="Launch desktop app")
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

    if args.gui:
        launch_desktop_app()
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

    if not args.first_pass and not args.retest:
        launch_desktop_app()
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
    print(f"\nDone. FP Result: {result['status']} | Total Good: {result.get('total_good_status')}")


if __name__ == "__main__":
    main()



