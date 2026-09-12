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
from core.theme import (
    setup_theme, MAIN_BG, CARD_BG, BORDER_COLOR, LIST_BG, BTN_BG, ENTRY_BG, ACCENT_BTN_BG,
    NAV_BG, BADGE_EMPTY_BG, BADGE_EMPTY_FG, BADGE_READY_BG, BADGE_READY_FG
)
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
            self.assertEqual(ftdc._panel_badges[panel].cget("bg"), BADGE_EMPTY_BG)

        # Check neon green progress bar
        self.assertIsInstance(ftdc.progress_bar, ttk.Progressbar)
        self.assertEqual(ftdc.progress_bar.cget("style"), "Neon.Horizontal.TProgressbar")

    def test_05_dynamic_badge_color_transitions(self):
        """Verify dynamic status pill transitions on file addition and reset."""
        ftdc = FTDCCheckerFrame(self.root)
        badge = ftdc._panel_badges["FIRST PASS"]

        # Initial: empty (slate)
        self.assertEqual(badge.cget("bg"), BADGE_EMPTY_BG)
        self.assertEqual(ftdc._panel_count_vars["FIRST PASS"].get(), "0 files")

        # Add file: loaded (emerald)
        ftdc.panel_files["FIRST PASS"].append(r"C:\test\sample.std")
        ftdc.refresh_file_list("FIRST PASS")
        self.assertEqual(badge.cget("bg"), BADGE_READY_BG)
        self.assertEqual(badge.cget("fg"), BADGE_READY_FG)
        self.assertEqual(ftdc._panel_count_vars["FIRST PASS"].get(), "1 file")

        # Clear all: empty (slate)
        ftdc.clear_all()
        self.assertEqual(badge.cget("bg"), BADGE_EMPTY_BG)
        self.assertEqual(badge.cget("fg"), BADGE_EMPTY_FG)
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

    def test_08_instant_tab_switching_geometry(self):
        """Verify zero-latency stacked grid layout without pack_forget."""
        hub = HubWindow(self.root)
        f1 = ttk.Frame(hub.content_area)
        f2 = ttk.Frame(hub.content_area)
        hub.register_app("Tab1", f1, is_default=True)
        hub.register_app("Tab2", f2)

        # Both frames should be persistently gridded in content_area
        grid_info_1 = f1.grid_info()
        grid_info_2 = f2.grid_info()
        self.assertEqual(int(grid_info_1.get("row", -1)), 0)
        self.assertEqual(int(grid_info_1.get("column", -1)), 0)
        self.assertEqual(int(grid_info_2.get("row", -1)), 0)
        self.assertEqual(int(grid_info_2.get("column", -1)), 0)

        # Switch to Tab2: instant lift, no pack_forget
        hub.switch_tab("Tab2")
        self.assertEqual(hub._active_tab, "Tab2")
        self.assertEqual(int(f1.grid_info().get("row", -1)), 0)
        self.assertEqual(int(f2.grid_info().get("row", -1)), 0)

    def test_09_tab_data_preservation(self):
        """Verify all inputs, listbox files, logs, and progress persist across tab switches."""
        hub = HubWindow(self.root)
        ftdc = FTDCCheckerFrame(hub.content_area, hub=hub)
        stdf2sum = STDF2SumFrame(hub.content_area, hub=hub)
        data_analysis = DataAnalysisFrame(hub.content_area, hub=hub)

        hub.register_app("FTDC Checker", ftdc, is_default=True)
        hub.register_app("STDF2SUM", stdf2sum)
        hub.register_app("Data Analysis", data_analysis)

        # Populate FTDC Checker data
        ftdc.lot_id_var.set("LOT_PRESERVE_123")
        ftdc.mpc_var.set("MPC_ABC")
        ftdc.fp_actual_good_qty_var.set("7500")
        ftdc.total_actual_good_qty_var.set("7800")
        ftdc.panel_files["FIRST PASS"].append(r"C:\test\lot_123_fp.std")
        ftdc.refresh_file_list("FIRST PASS")
        ftdc.log("Logging critical data line 1")
        ftdc.log("Logging critical data line 2")
        ftdc.progress_bar.configure(value=65.0)
        self.root.update()

        # Navigate away to STDF2SUM and Data Analysis
        hub.switch_tab("STDF2SUM")
        self.assertEqual(hub._active_tab, "STDF2SUM")

        hub.switch_tab("Data Analysis")
        self.assertEqual(hub._active_tab, "Data Analysis")

        # Navigate back to FTDC Checker
        hub.switch_tab("FTDC Checker")
        self.assertEqual(hub._active_tab, "FTDC Checker")

        # Assert 100% data preservation
        self.assertEqual(ftdc.lot_id_var.get(), "LOT_PRESERVE_123")
        self.assertEqual(ftdc.mpc_var.get(), "MPC_ABC")
        self.assertEqual(ftdc.fp_actual_good_qty_var.get(), "7500")
        self.assertEqual(ftdc.total_actual_good_qty_var.get(), "7800")
        self.assertIn(r"C:\test\lot_123_fp.std", ftdc.panel_files["FIRST PASS"])
        self.assertEqual(ftdc.panel_listboxes["FIRST PASS"].get(0), r"C:\test\lot_123_fp.std")
        self.assertIn("Logging critical data line 1", ftdc.log_text.get("1.0", "end"))
        self.assertAlmostEqual(ftdc.progress_bar["value"], 65.0, places=1)

    def test_10_background_task_non_blocking_and_hub_sync(self):
        """Verify background task execution while navigating tabs."""
        hub = HubWindow(self.root)
        ftdc = FTDCCheckerFrame(hub.content_area, hub=hub)
        stdf2sum = STDF2SumFrame(hub.content_area, hub=hub)

        hub.register_app("FTDC Checker", ftdc, is_default=True)
        hub.register_app("STDF2SUM", stdf2sum)

        # Start task on FTDC Checker
        ftdc._set_running(True)
        self.assertTrue(ftdc.is_running)

        # Switch to STDF2SUM while task is active
        hub.switch_tab("STDF2SUM")
        self.assertEqual(hub._active_tab, "STDF2SUM")
        self.assertEqual(hub._tab_states["FTDC Checker"], "busy")
        self.assertIn("⏳", hub._nav_items["FTDC Checker"].cget("text"))

        # Task completes in the background
        ftdc._set_running(False)
        self.assertFalse(ftdc.is_running)

        # Since user is on STDF2SUM, FTDC Checker should enter blinking alert state
        self.assertEqual(hub._tab_states["FTDC Checker"], "alert")
        self.assertIsNotNone(hub._tab_blink_jobs["FTDC Checker"])
        self.assertIn("Done", hub._nav_items["FTDC Checker"].cget("text"))

        # Clean up timer
        hub.stop_tab_alert("FTDC Checker")

    def test_11_blinking_tab_highlight_on_completion(self):
        """Verify blinking alert cycles and cancellation upon user tab selection."""
        hub = HubWindow(self.root)
        f1 = ttk.Frame(hub.content_area)
        f2 = ttk.Frame(hub.content_area)
        hub.register_app("AppA", f1, is_default=True)
        hub.register_app("AppB", f2)

        # User is on AppB
        hub.switch_tab("AppB")
        self.assertEqual(hub._active_tab, "AppB")

        # AppA task finishes in background
        hub.notify_tab_finished("AppA")
        self.assertEqual(hub._tab_states["AppA"], "alert")
        self.assertIsNotNone(hub._tab_blink_jobs["AppA"])

        tab_a_btn = hub._nav_items["AppA"]
        # Cycle blink step
        hub._blink_step("AppA")
        self.assertIn("Done", tab_a_btn.cget("text"))

        # User clicks/switches to AppA: blinking stops immediately
        hub.switch_tab("AppA")
        self.assertEqual(hub._active_tab, "AppA")
        self.assertEqual(hub._tab_states["AppA"], "idle")
        self.assertIsNone(hub._tab_blink_jobs["AppA"])
        self.assertEqual(tab_a_btn.cget("text"), "AppA")
        self.assertEqual(tab_a_btn.cget("bg"), "#2563EB")  # Active blue

    def test_12_filter_buttons_state_and_theme_properties(self):
        """Verify centralized #F0F0F0 theme, #E0E0E0 borders, and button/entry styling."""
        # 1. Verify theme constants
        self.assertEqual(MAIN_BG, "#F0F0F0")
        self.assertEqual(CARD_BG, "#F0F0F0")
        self.assertEqual(LIST_BG, "#FFFFFF")
        self.assertEqual(BORDER_COLOR, "#E0E0E0")
        self.assertEqual(BTN_BG, "#FFFFFF")
        self.assertEqual(ENTRY_BG, "#FFFFFF")
        self.assertEqual(ACCENT_BTN_BG, "#2563EB")
        self.assertEqual(NAV_BG, "#172554")

        ftdc = FTDCCheckerFrame(self.root)

        # Verify initial card bg of Test Parameter Filter frame is #F0F0F0 when manual filter is disabled
        self.assertEqual(ftdc.filter_card.cget("bg"), "#F0F0F0")

        # 2. Verify ModernHoverButton instances for both filter buttons
        self.assertIsInstance(ftdc.show_all_tests_button, ModernHoverButton)
        self.assertIsInstance(ftdc.clear_selected_tests_button, ModernHoverButton)
        self.assertIn(ftdc.show_all_tests_button, ftdc.filter_widgets)
        self.assertIn(ftdc.clear_selected_tests_button, ftdc.filter_widgets)

        # 3. Both buttons must be disabled by default and Clear Selected hidden
        self.assertFalse(ftdc.manual_filter_var.get())
        self.assertEqual(ftdc.show_all_tests_button.cget("state"), "disabled")
        self.assertEqual(ftdc.clear_selected_tests_button.grid_info(), {})  # Hidden by default

        # 4. Clicking show_all_tests while manual filter is unchecked returns early (no auto-enable)
        with patch.object(ftdc, "_get_all_selected_files") as mock_files:
            ftdc.show_all_tests()
            self.assertFalse(mock_files.called)
            self.assertFalse(ftdc.manual_filter_var.get())

        # 5. Enable Manual Filter: Show All Test becomes normal, Clear Selected stays hidden (no tests selected)
        # and filter_card bg becomes #FFFFFF
        ftdc.manual_filter_var.set(True)
        ftdc._sync_manual_filter_state()
        self.assertEqual(ftdc.filter_card.cget("bg"), "#FFFFFF")
        self.assertEqual(ftdc.show_all_tests_button.cget("state"), "normal")
        self.assertEqual(ftdc.clear_selected_tests_button.grid_info(), {})  # Hidden (no parameters selected)

        # 6. Set selected tests: Clear Selected shows up and becomes normal
        ftdc.selected_tests_var.set("1001, 1002")
        ftdc._update_selected_test_summary()
        self.assertNotEqual(ftdc.clear_selected_tests_button.grid_info(), {})  # Shows up!
        self.assertEqual(ftdc.clear_selected_tests_button.cget("state"), "normal")

        # 7. Clear Selected: tests reset, Clear Selected hides again
        ftdc._clear_selected_test_filter()
        self.assertEqual(ftdc.selected_tests_var.get(), "")
        self.assertEqual(ftdc.clear_selected_tests_button.grid_info(), {})  # Hides!
        self.assertEqual(ftdc.show_all_tests_button.cget("state"), "normal")

        # 8. Uncheck Manual Filter: Show All Test disabled, Clear Selected remains hidden, filter_card bg becomes #F0F0F0
        ftdc.manual_filter_var.set(False)
        ftdc._sync_manual_filter_state()
        self.assertEqual(ftdc.filter_card.cget("bg"), "#F0F0F0")
        self.assertEqual(ftdc.show_all_tests_button.cget("state"), "disabled")
        self.assertEqual(ftdc.clear_selected_tests_button.grid_info(), {})  # Hidden


if __name__ == "__main__":
    unittest.main()
