"""
Comprehensive Automated Test Suite for PTS Hub Architecture.
"""
import os
import sys
import unittest
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk

# Add PTS_Hub directory to path
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PTS_HUB_DIR = os.path.dirname(TEST_DIR)
if PTS_HUB_DIR not in sys.path:
    sys.path.insert(0, PTS_HUB_DIR)

from core.config import APP_NAME, APP_VERSION, APP_ID, get_asset_path, ICON_PATH
from core.theme import setup_theme
from core.widgets import ModernHoverButton, ModernGreenProgressBar
from core.hub_window import HubWindow
from apps.ftdc_checker.ui import FTDCCheckerFrame
from apps.stdf2sum.ui import STDF2SumFrame
from apps.data_analysis.ui import DataAnalysisFrame
from apps.ftdc_checker.stdf_parser import parse_filter_values
from apps.ftdc_checker.guid_analysis import _parse_int_field


class TestPTSHubArchitecture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        setup_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_01_config_and_assets(self):
        """Verify app constants and multi-level asset path resolution."""
        self.assertEqual(APP_NAME, "PTS Hub")
        self.assertEqual(APP_VERSION, "v1.3")
        self.assertEqual(APP_ID, "PTS_Hub")

        icon_resolved = get_asset_path("FTDC_Checker_icon.ico")
        self.assertTrue(os.path.isfile(icon_resolved), f"Icon missing: {icon_resolved}")

        mask_resolved = get_asset_path("mask_wxy_map.json")
        self.assertTrue(os.path.isfile(mask_resolved), f"Mask JSON missing: {mask_resolved}")

        mpc_resolved = get_asset_path("mpc_partnumber.json")
        self.assertTrue(os.path.isfile(mpc_resolved), f"MPC JSON missing: {mpc_resolved}")

    def test_02_core_widgets(self):
        """Verify ModernHoverButton and ModernGreenProgressBar behavior."""
        btn_clicked = [False]
        def on_click():
            btn_clicked[0] = True

        btn = ModernHoverButton(self.root, text="Test Button", command=on_click)
        self.assertEqual(btn.cget("state"), "normal")
        self.assertEqual(btn["state"], "normal")

        # Simulate click
        btn._on_click(None)
        self.assertTrue(btn_clicked[0])

        # Test disable state
        btn.configure(state="disabled")
        self.assertEqual(btn["state"], "disabled")
        btn.configure(state="normal")
        self.assertEqual(btn["state"], "normal")

        # Canvas progress bar fallback
        pbar = ModernGreenProgressBar(self.root, height=18)
        pbar.configure(maximum=100.0, value=50.0)
        self.assertEqual(pbar._value, 50.0)

    def test_03_hub_window_and_navigation(self):
        """Verify HubWindow top navbar and tab switching."""
        hub = HubWindow(self.root)
        self.assertIsNotNone(hub.tabs_frame)
        self.assertIsNotNone(hub.content_area)

        # Register all 3 modular applications
        ftdc = FTDCCheckerFrame(hub.content_area, hub=hub)
        stdf2sum = STDF2SumFrame(hub.content_area, hub=hub)
        data_analysis = DataAnalysisFrame(hub.content_area, hub=hub)

        hub.register_app("FTDC Checker", ftdc, is_default=True)
        hub.register_app("STDF2SUM", stdf2sum)
        hub.register_app("Data Analysis", data_analysis)

        self.assertEqual(hub._active_tab, "FTDC Checker")

        # Test tab switching
        hub.switch_tab("STDF2SUM")
        self.assertEqual(hub._active_tab, "STDF2SUM")

        hub.switch_tab("Data Analysis")
        self.assertEqual(hub._active_tab, "Data Analysis")

        hub.switch_tab("FTDC Checker")
        self.assertEqual(hub._active_tab, "FTDC Checker")

    def test_04_ftdc_checker_frame_components(self):
        """Verify FTDCCheckerFrame widgets, action buttons, and panels."""
        ftdc = FTDCCheckerFrame(self.root)

        # Check action buttons
        self.assertEqual(len(ftdc._action_buttons), 4)
        self.assertIsNotNone(ftdc.convert_button)
        self.assertIsNotNone(ftdc.get_ftdc_button)
        self.assertIsNotNone(ftdc.get_stdf_button)
        self.assertIsNotNone(ftdc.check_stdf_button)

        # Check file panels and badges
        for panel in ("FIRST PASS", "RETEST", "QC"):
            self.assertIn(panel, ftdc.panel_listboxes)
            self.assertIn(panel, ftdc._panel_count_vars)
            self.assertIn(panel, ftdc._panel_badges)
            self.assertEqual(ftdc._panel_count_vars[panel].get(), "0 files")
            self.assertEqual(ftdc._panel_badges[panel].cget("bg"), "#F1F5F9")

        # Check neon green progress bar
        self.assertIsInstance(ftdc.progress_bar, ttk.Progressbar)
        self.assertEqual(ftdc.progress_bar.cget("style"), "Neon.Horizontal.TProgressbar")

    def test_05_dynamic_badge_color_transitions(self):
        """Verify dynamic status pill transitions on file addition and reset."""
        ftdc = FTDCCheckerFrame(self.root)
        badge = ftdc._panel_badges["FIRST PASS"]

        # Initial: empty (slate)
        self.assertEqual(badge.cget("bg"), "#F1F5F9")
        self.assertEqual(ftdc._panel_count_vars["FIRST PASS"].get(), "0 files")

        # Add file: loaded (emerald)
        ftdc.panel_files["FIRST PASS"].append(r"C:\test\sample.std")
        ftdc.refresh_file_list("FIRST PASS")
        self.assertEqual(badge.cget("bg"), "#DCFCE7")
        self.assertEqual(badge.cget("fg"), "#15803D")
        self.assertEqual(ftdc._panel_count_vars["FIRST PASS"].get(), "1 file")

        # Clear all: empty (slate)
        ftdc.clear_all()
        self.assertEqual(badge.cget("bg"), "#F1F5F9")
        self.assertEqual(badge.cget("fg"), "#94A3B8")
        self.assertEqual(ftdc._panel_count_vars["FIRST PASS"].get(), "0 files")

    def test_06_integer_validation_and_example_popup(self):
        """Verify strict integer validation and proper input example popup."""
        self.assertEqual(_parse_int_field("12500", "Field"), 12500)
        self.assertEqual(_parse_int_field("12,500", "Field"), 12500)

        with self.assertRaises(ValueError) as ctx:
            _parse_int_field("12.5", "First Pass Actual Good QTY")
        self.assertIn("Proper Input Example: 12500", str(ctx.exception))

        ftdc = FTDCCheckerFrame(self.root)
        for p in ftdc.PANELS:
            ftdc.panel_files[p] = [f"C:\\test\\{p}.std"]
        ftdc.lot_id_var.set("LOT123")
        ftdc.mpc_var.set("MPC456")
        ftdc.fp_actual_good_qty_var.set("123.45")  # invalid float
        ftdc.total_actual_good_qty_var.set("100")

        with patch("tkinter.messagebox.showerror") as mock_err:
            ftdc.start_get_data()
            self.assertTrue(mock_err.called)
            title, msg = mock_err.call_args[0]
            self.assertEqual(title, "Invalid Integer Input")
            self.assertIn("Proper Input Example: 12500", msg)

    def test_07_parameter_filter_testnum_testtext(self):
        """Verify TestNum_TestText parameter filtering format."""
        parsed = parse_filter_values("1001 1002", "2005_CurrentTest, 3001_VoltageTest", "", "")
        self.assertEqual(parsed, {1001, 1002, 2005, 3001})


if __name__ == "__main__":
    unittest.main()
