"""
Comprehensive Automated Test Suite for PTS Hub Architecture.
"""
import os
import shutil
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
    setup_theme, MAIN_BG, CARD_BG, BORDER_COLOR, LIST_BG, LISTBOX_BG, BTN_BG, ENTRY_BG, ACCENT_BTN_BG,
    NAV_BG, NAV_TAB_BG, NAV_TAB_ACTIVE, NAV_TITLE_FG, NAV_BADGE_BG, NAV_BADGE_FG,
    BADGE_EMPTY_BG, BADGE_EMPTY_FG, BADGE_READY_BG, BADGE_READY_FG
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

        # Check Lot Details variables, hidden PPO#, & neon green progress bar
        def _iter_widgets(w):
            yield w
            for child in w.winfo_children():
                yield from _iter_widgets(child)

        self.assertFalse(ftdc.ENABLE_TARGETED_PPO_ATTACH)
        ppo_labels = [
            w for w in _iter_widgets(ftdc)
            if isinstance(w, tk.Label) and w.cget("text") == "PPO#"
        ]
        self.assertEqual(ppo_labels, [])
        self.assertTrue(hasattr(ftdc, "lot_id_var"))
        self.assertTrue(hasattr(ftdc, "mpc_var"))
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
        ftdc.ppo_var.set("")
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
        ftdc._mpc_cache["LOT_PRESERVE_123"] = "MPC_ABC"
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
        self.assertEqual(BORDER_COLOR, "#B0B0B0")
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

    def test_13_blue_home_icons_and_navbar_icons(self):
        """Verify blue home launcher icons and top navigation bar icons across all registered apps."""
        from core.hub_window import HomeFrame, NAV_ICON_BITMAPS, create_nav_icon

        # 1. Verify HomeFrame blue palette constants
        self.assertEqual(HomeFrame.BLUE_DARK, "#1E3A8A")
        self.assertEqual(HomeFrame.BLUE_PRIMARY, "#2563EB")
        self.assertEqual(HomeFrame.BLUE_LIGHT, "#38BDF8")
        self.assertEqual(HomeFrame.NAVY, "#1E3A8A")
        self.assertEqual(HomeFrame.ORANGE, "#2563EB")

        # 2. Verify navigation icon bitmaps exist for all core tabs
        expected_tabs = ["Home", "FTDC Checker", "STDF2SUM", "Data Analysis"]
        for tab_name in expected_tabs:
            self.assertIn(tab_name, NAV_ICON_BITMAPS)
            icon = create_nav_icon(self.root, tab_name, "#FFFFFF")
            self.assertIsNotNone(icon)
            self.assertEqual(icon.width(), 16)
            self.assertEqual(icon.height(), 16)

        # 3. Verify HubWindow attaches icons and compound='left' to every tab
        hub = HubWindow(self.root)
        f_ftdc = ttk.Frame(hub.content_area)
        f_stdf2sum = ttk.Frame(hub.content_area)
        f_data_analysis = ttk.Frame(hub.content_area)

        hub.register_app("FTDC Checker", f_ftdc)
        hub.register_app("STDF2SUM", f_stdf2sum)
        hub.register_app("Data Analysis", f_data_analysis)

        for tab_name in expected_tabs:
            tab_btn = hub._nav_items.get(tab_name)
            self.assertIsNotNone(tab_btn, f"Tab {tab_name} missing from nav items")
            self.assertEqual(tab_btn.cget("compound"), "left")
            self.assertNotEqual(tab_btn.cget("image"), "")
            self.assertTrue(tab_btn.cget("text").startswith(f"  {tab_name}"))

        # 4. Verify tab switching swaps active and inactive icons seamlessly
        hub.switch_tab("STDF2SUM")
        self.assertEqual(hub._active_tab, "STDF2SUM")
        stdf2sum_btn = hub._nav_items["STDF2SUM"]
        home_btn = hub._nav_items["Home"]
        self.assertEqual(stdf2sum_btn.cget("image"), str(hub._nav_icons_active["STDF2SUM"]))
        self.assertEqual(home_btn.cget("image"), str(hub._nav_icons_inactive["Home"]))

        # 5. Verify busy and alert states preserve navigation icons
        hub.set_tab_busy("FTDC Checker", True)
        ftdc_btn = hub._nav_items["FTDC Checker"]
        self.assertIn("⏳", ftdc_btn.cget("text"))
        self.assertEqual(ftdc_btn.cget("image"), str(hub._nav_icons_inactive["FTDC Checker"]))

        hub.set_tab_busy("FTDC Checker", False)
        self.assertNotIn("⏳", ftdc_btn.cget("text"))
        self.assertEqual(ftdc_btn.cget("image"), str(hub._nav_icons_inactive["FTDC Checker"]))

        # 6. Verify canvas icon drawing routines execute without errors
        cv = tk.Canvas(self.root, width=60, height=60)
        HomeFrame._draw_ftdc_icon(cv)
        HomeFrame._draw_stdf2sum_icon(cv)
        HomeFrame._draw_data_analysis_icon(cv)
        cv.destroy()




class TestSTDFParserAndReliability(unittest.TestCase):
    """Comprehensive tests for STDF V4 parser strictness, Perl-inspired reliability features,
    count reconciliation, LOT_ID validation, extra-bytes blocking, .gz support, and Check STDF UI."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        setup_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    @staticmethod
    def _cn(s: str) -> bytes:
        b = s.encode("latin-1")
        return bytes([len(b)]) + b

    @classmethod
    def _rec(cls, rec_typ: int, rec_sub: int, payload: bytes, endian: str = "<") -> bytes:
        import struct
        return struct.pack(f"{endian}HBB", len(payload), rec_typ, rec_sub) + payload

    @classmethod
    def _build_synthetic_stdf(
        cls,
        endian: str = "<",
        cpu_typ: int = 2,
        stdf_ver: int = 4,
        far_extra: bytes = b"",
        lot_id: str = "LOT123.00",
        part_typ: str = "DEV1",
        node_nam: str = "NODE1",
        tstr_typ: str = "V93K",
        job_nam: str = "JOB1",
        mir_extra: bytes = b"",
        parts=None,
        prr_extra: bytes = b"",
        pcr_counts=None,
        sbr_bins=None,
        hbr_bins=None,
        include_mrr: bool = True,
        mrr_finish_t: int = 1700001000,
        mrr_extra: bytes = b"",
        ptr_opt_flag: int = 0,
    ) -> bytes:
        import struct
        out = bytearray()
        # FAR (0, 10)
        out += cls._rec(0, 10, bytes([cpu_typ, stdf_ver]) + far_extra, endian)

        # MIR (1, 10)
        mir_payload = bytearray(struct.pack(f"{endian}IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
        mir_payload += ord(" ").to_bytes(1, "little")
        for s in (lot_id, part_typ, node_nam, tstr_typ, job_nam):
            mir_payload += cls._cn(s)
        # Remaining 25 optional C*n fields up to _MIR_FIELD_NAMES (total 30 strings)
        for _ in range(25):
            mir_payload += b"\x00"
        mir_payload += mir_extra
        out += cls._rec(1, 10, bytes(mir_payload), endian)

        # SDR (1, 80)
        sdr_payload = bytes([1, 2, 1, 1]) + cls._cn("HAND1")
        out += cls._rec(1, 80, sdr_payload, endian)

        if parts is None:
            parts = [
                {"part_id": "1", "pass": True, "hard_bin": 1, "soft_bin": 1, "test_t": 10, "w": 5, "x": 10, "y": 20},
                {"part_id": "2", "pass": False, "hard_bin": 65535, "soft_bin": 65535, "test_t": 0, "w": 5, "x": 11, "y": 21},
            ]

        for p in parts:
            # PIR (5, 10)
            out += cls._rec(5, 10, bytes([1, 1]), endian)
            # PTRs (15, 10) for W=101, X=102, Y=103
            for tnum, val in ((101, p["w"]), (102, p["x"]), (103, p["y"])):
                ptr_body = bytearray(struct.pack(f"{endian}IBBBBf", tnum, 1, 1, 0, 0, float(val)))
                ptr_body += cls._cn(f"T_{tnum}") + cls._cn("")
                ptr_body += bytes([ptr_opt_flag, 0, 0, 0])
                ptr_body += struct.pack(f"{endian}ff", -100.0, 100.0)
                out += cls._rec(15, 10, bytes(ptr_body), endian)

            # PRR (5, 20)
            part_flg = 0x00 if p["pass"] else 0x08
            prr_body = bytearray(
                struct.pack(
                    f"{endian}BBBHHHhhI",
                    1,
                    1,
                    part_flg,
                    3,
                    p["hard_bin"],
                    p["soft_bin"],
                    -32768,
                    -32768,
                    p["test_t"],
                )
            )
            prr_body += cls._cn(p["part_id"]) + b"\x00" + b"\x00" + prr_extra
            out += cls._rec(5, 20, bytes(prr_body), endian)

        # HBR (1, 40)
        if hbr_bins:
            for hbin_num, hbin_cnt, hbin_pf, hbin_nam in hbr_bins:
                hbr_body = struct.pack(f"{endian}BBHI", 255, 0, hbin_num, hbin_cnt) + hbin_pf.encode("latin-1") + cls._cn(hbin_nam)
                out += cls._rec(1, 40, hbr_body, endian)

        # SBR (1, 50)
        if sbr_bins:
            for sbin_num, sbin_cnt, sbin_pf, sbin_nam in sbr_bins:
                sbr_body = struct.pack(f"{endian}BBHI", 255, 0, sbin_num, sbin_cnt) + sbin_pf.encode("latin-1") + cls._cn(sbin_nam)
                out += cls._rec(1, 50, sbr_body, endian)

        # PCR (1, 30)
        if pcr_counts is not None:
            part_cnt, rtst_cnt, abrt_cnt, good_cnt, func_cnt = pcr_counts
            pcr_body = struct.pack(f"{endian}BBIIIII", 255, 0, part_cnt, rtst_cnt, abrt_cnt, good_cnt, func_cnt)
            out += cls._rec(1, 30, pcr_body, endian)

        # MRR (1, 20)
        if include_mrr:
            mrr_body = struct.pack(f"{endian}I", mrr_finish_t) + b" " + b"\x00" + b"\x00" + mrr_extra
            out += cls._rec(1, 20, mrr_body, endian)

        return bytes(out)

    def test_13_strict_far_header_and_big_endian_walk(self):
        """Verify strict FAR header validation (length endian cross-check + STDF_VER=4) and Big-Endian walk."""
        import tempfile
        from apps.ftdc_checker.stdf_parser import STDFReader, parse_stdf_file, scan_test_list, parse_check_summary

        # 1. Valid Big-Endian STDF V4 (CPU_TYP=1, endian='>')
        be_bytes = self._build_synthetic_stdf(
            endian=">", cpu_typ=1, stdf_ver=4, pcr_counts=(2, 0, 0, 1, 0)
        )
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(be_bytes)
            be_path = tf.name
        try:
            with STDFReader(be_path) as reader:
                spans = list(reader.record_spans())
            self.assertGreater(len(spans), 5)
            tests = scan_test_list(be_path)
            self.assertEqual([t["TEST_NUM"] for t in tests], [101, 102, 103])
            parsed, _, _ = parse_stdf_file(be_path, verbose=False)
            self.assertTrue(parsed["DIAGNOSTICS"]["far_valid"])
            self.assertEqual(parsed["DIAGNOSTICS"]["endian"], ">")
            self.assertEqual(len(parsed["PARTS"]), 2)
            summary = parse_check_summary(be_path, expected_lot_id="LOT123.00")
            self.assertTrue(summary["far_valid"])
            self.assertEqual(summary["discrepancy_text"], "None")
        finally:
            os.remove(be_path)

        # 2. Invalid STDF version (STDF_VER=3) -> rejected
        bad_ver_bytes = self._build_synthetic_stdf(endian="<", cpu_typ=2, stdf_ver=3)
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(bad_ver_bytes)
            bad_ver_path = tf.name
        try:
            logs = []
            summary = parse_check_summary(bad_ver_path, logger=logs.append)
            self.assertFalse(summary["far_valid"])
            self.assertTrue(any("STDF_VER=3" in m for m in logs))
        finally:
            os.remove(bad_ver_path)

        # 3. CPU_TYP / FAR length endianness mismatch (FAR length is little-endian, CPU_TYP=1 claims big-endian)
        mismatch_bytes = self._build_synthetic_stdf(endian="<", cpu_typ=1, stdf_ver=4)
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(mismatch_bytes)
            mismatch_path = tf.name
        try:
            logs = []
            summary = parse_check_summary(mismatch_path, logger=logs.append)
            self.assertFalse(summary["far_valid"])
            self.assertTrue(any("FAR endianness mismatch" in m for m in logs))
        finally:
            os.remove(mismatch_path)

    def test_14_extra_bytes_warning_in_check_stdf_and_error_in_analysis(self):
        """Verify extra bytes in a record warn in Check STDF (and log to Activity Log) but block Run Analysis."""
        import tempfile
        from apps.ftdc_checker.stdf_parser import parse_check_summary, parse_stdf_file, validate_stdf_data
        from apps.ftdc_checker.guid_analysis import analyze_guid_data

        stdf_bytes = self._build_synthetic_stdf(
            lot_id="LOT123.00",
            prr_extra=b"\xDE\xAD",
            pcr_counts=(2, 0, 0, 1, 0),
        )
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(stdf_bytes)
            fpath = tf.name
        try:
            check_logs = []
            summary = parse_check_summary(fpath, expected_lot_id="LOT123.00", logger=check_logs.append)
            self.assertGreater(summary["extra_bytes_count"], 0)
            self.assertTrue(any("[WARNING]" in m and "leftover/extra byte(s)" in m and "5-20 (PRR)" in m for m in check_logs))

            parsed, _, _ = parse_stdf_file(fpath, expected_lot_id="LOT123.00", verbose=False)
            val = validate_stdf_data(parsed, expected_lot_id="LOT123.00")
            self.assertIn("EXTRA_BYTES", val["reasons"])

            analysis_logs = []
            with self.assertRaises(ValueError) as ctx:
                analyze_guid_data(
                    first_pass_paths=[fpath],
                    retest_paths=[fpath],
                    qc_paths=[fpath],
                    lot_id="LOT123.00",
                    mpc_text="59793QRTBC01",
                    first_pass_actual_good_qty="1",
                    total_actual_good_qty="1",
                    manual_filter_tests=[101, 102, 103],
                    logger=analysis_logs.append,
                )
            self.assertIn("extra/leftover bytes", str(ctx.exception))
            self.assertTrue(any("leftover/extra byte(s)" in m for m in analysis_logs))
        finally:
            os.remove(fpath)

    def test_15_truncation_and_malformed_record_logging(self):
        """Verify truncated files and malformed records log filename, offset, and record type to Activity Log."""
        import tempfile
        from apps.ftdc_checker.stdf_parser import parse_stdf_file, parse_check_summary, validate_stdf_data

        full_bytes = self._build_synthetic_stdf(lot_id="LOT123.00", pcr_counts=(2, 0, 0, 1, 0))
        # Cut off 5 bytes before the end (mid-MRR record)
        trunc_bytes = full_bytes[:-5]
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(trunc_bytes)
            tpath = tf.name
        try:
            logs = []
            summary = parse_check_summary(tpath, logger=logs.append)
            self.assertTrue(summary["truncated"])
            self.assertIn("Truncated File", summary["discrepancy_text"])
            self.assertTrue(any("[ERROR]" in m and "@ offset" in m and "Truncated record body at EOF" in m for m in logs))

            parsed, _, _ = parse_stdf_file(tpath, verbose=False)
            val = validate_stdf_data(parsed)
            self.assertFalse(val["valid"])
            self.assertIn("TRUNCATED_FILE", val["reasons"])
        finally:
            os.remove(tpath)

    def test_16_unset_value_normalization_and_opt_flag(self):
        """Verify 65535 bins, TEST_T==0, 0xFFFFFFFF counts, -32768 coords, and PTR OPT_FLAG limit validity."""
        import math
        import tempfile
        from apps.ftdc_checker.stdf_parser import parse_stdf_file, scan_test_list

        # OPT_FLAG = 0x40 (bit 6: LO_LIMIT invalid) | 0x80 (bit 7: HI_LIMIT invalid) = 0xC0
        stdf_bytes = self._build_synthetic_stdf(
            lot_id="LOT123.00",
            pcr_counts=(2, 0xFFFFFFFF, 0xFFFFFFFF, 1, 0xFFFFFFFF),
            hbr_bins=[(1, 1, "P", "PASS"), (65535, 0xFFFFFFFF, "F", "UNSET")],
            sbr_bins=[(1, 1, "P", "SBIN_PASS"), (65535, 0xFFFFFFFF, "F", "SBIN_UNSET")],
            ptr_opt_flag=0xC0,
        )
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(stdf_bytes)
            fpath = tf.name
        try:
            parsed, _, _ = parse_stdf_file(fpath, verbose=False)
            # MIR BURN_TIM was 65535 -> normalized to None
            self.assertIsNone(parsed["MIR"]["BURN_TIM"])
            # Part 1: valid bins and TEST_T=10, coords=-32768 -> None
            p0 = parsed["PARTS"][0]
            self.assertEqual(p0["HARD_BIN"], 1)
            self.assertEqual(p0["SOFT_BIN"], 1)
            self.assertEqual(p0["TEST_T"], 10)
            self.assertIsNone(p0["X_COORD"])
            self.assertIsNone(p0["Y_COORD"])
            # Part 2: 65535 bins -> None, TEST_T=0 -> None
            p1 = parsed["PARTS"][1]
            self.assertIsNone(p1["HARD_BIN"])
            self.assertIsNone(p1["SOFT_BIN"])
            self.assertIsNone(p1["TEST_T"])
            # PCR 0xFFFFFFFF counts -> None
            pcr = parsed["PCR"]
            self.assertEqual(pcr["PART_CNT"], 2)
            self.assertEqual(pcr["GOOD_CNT"], 1)
            self.assertIsNone(pcr["RTST_CNT"])
            self.assertIsNone(pcr["ABRT_CNT"])
            self.assertIsNone(pcr["FUNC_CNT"])
            # HBR / SBR 65535 bin number and 0xFFFFFFFF count -> None
            self.assertIsNone(parsed["HBR"][1]["HBIN_NUM"])
            self.assertIsNone(parsed["HBR"][1]["HBIN_CNT"])
            self.assertIsNone(parsed["SBR"][1]["SBIN_NUM"])
            self.assertIsNone(parsed["SBR"][1]["SBIN_CNT"])
            # PTR OPT_FLAG 0xC0 -> LO_LIMIT and HI_LIMIT normalized to None / 'n/a' in scan_test_list
            scanned = scan_test_list(fpath)
            self.assertTrue(scanned[0]["LO_LIMIT"] is None or math.isnan(scanned[0]["LO_LIMIT"]))
            self.assertTrue(scanned[0]["HI_LIMIT"] is None or math.isnan(scanned[0]["HI_LIMIT"]))
            self.assertEqual(scanned[0]["_LO_LIMIT_TEXT"], "n/a")
            self.assertEqual(scanned[0]["_HI_LIMIT_TEXT"], "n/a")
        finally:
            os.remove(fpath)

        # Also verify OPT_FLAG = 0x0E (bits 1, 2, 3 set: NO LO_SPEC / HI_SPEC, but LO_LIMIT / HI_LIMIT valid!)
        stdf_bytes_0e = self._build_synthetic_stdf(
            lot_id="LOT123.00",
            pcr_counts=(2, 0, 0, 1, 0),
            ptr_opt_flag=0x0E,
        )
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf2:
            tf2.write(stdf_bytes_0e)
            fpath2 = tf2.name
        try:
            scanned_0e = scan_test_list(fpath2)
            self.assertEqual(scanned_0e[0]["LO_LIMIT"], -100.0)
            self.assertEqual(scanned_0e[0]["HI_LIMIT"], 100.0)
            self.assertEqual(scanned_0e[0]["_LO_LIMIT_TEXT"], "-100")
            self.assertEqual(scanned_0e[0]["_HI_LIMIT_TEXT"], "100")
        finally:
            os.remove(fpath2)

    def test_17_lot_id_validation_and_count_reconciliation(self):
        """Verify MIR LOT_ID validation (including .RJ1 retest suffix) and PCR/SBR/PRR count reconciliation."""
        import tempfile
        from apps.ftdc_checker.stdf_parser import parse_check_summary, is_lot_id_matching
        from apps.ftdc_checker.guid_analysis import analyze_guid_data

        # 1. Retest suffix matching
        self.assertTrue(is_lot_id_matching("MMT-271200266.D00.RJ1", "MMT-271200266.D00"))
        self.assertFalse(is_lot_id_matching("WRONG-LOT.00", "MMT-271200266.D00"))

        # 2. Count discrepancy (2 PRR parts: 1 good; PCR claims 5 parts, 4 good; SBR claims 3 parts, 2 good)
        stdf_bytes = self._build_synthetic_stdf(
            lot_id="LOT999.00",
            pcr_counts=(5, 0, 0, 4, 0),
            sbr_bins=[(1, 2, "P", "GOOD_BIN"), (2, 1, "F", "BAD_BIN")],
        )
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(stdf_bytes)
            fpath = tf.name
        try:
            summary = parse_check_summary(fpath, expected_lot_id="LOT123.00")
            disc = summary["discrepancy_text"]
            self.assertIn("Lot ID Mismatch (MIR: LOT999.00)", disc)
            self.assertIn("PCR Part(5) != PRR(2)", disc)
            self.assertIn("PCR Good(4) != PRR Good(1)", disc)
            self.assertIn("SBR Part(3) != PRR(2)", disc)
            self.assertIn("SBR Good(2) != PRR Good(1)", disc)

            # Run Analysis must reject LOT_ID mismatch with a ValueError
            with self.assertRaises(ValueError) as ctx:
                analyze_guid_data(
                    first_pass_paths=[fpath],
                    retest_paths=[fpath],
                    qc_paths=[fpath],
                    lot_id="LOT123.00",
                    mpc_text="59793QRTBC01",
                    first_pass_actual_good_qty="1",
                    total_actual_good_qty="1",
                    manual_filter_tests=[101, 102, 103],
                )
            self.assertIn("MIR LOT_ID", str(ctx.exception))
        finally:
            os.remove(fpath)

    def test_18_gzip_stdf_support_and_check_stdf_ui_column(self):
        """Verify .gz STDF decompression and Check STDF 'Discrepancy / Mismatch' UI column."""
        import gzip
        import tempfile
        import shutil
        from apps.ftdc_checker.stdf_parser import parse_stdf_file, parse_check_summary, scan_test_list
        from apps.ftdc_checker.stdf_fetcher import STDF_EXTENSIONS

        self.assertIn(".gz", STDF_EXTENSIONS)

        stdf_bytes = self._build_synthetic_stdf(
            lot_id="LOT_GZ.00",
            pcr_counts=(2, 0, 0, 1, 0),
            sbr_bins=[(1, 1, "P", "PASS"), (2, 1, "F", "FAIL")],
        )
        temp_dir = tempfile.mkdtemp()
        gz_path = os.path.join(temp_dir, "sample_20250101120000.std.gz")
        try:
            with gzip.open(gz_path, "wb") as gf:
                gf.write(stdf_bytes)

            tests = scan_test_list(gz_path)
            self.assertEqual([t["TEST_NUM"] for t in tests], [101, 102, 103])

            parsed, _, _ = parse_stdf_file(gz_path, expected_lot_id="LOT_GZ.00", verbose=False)
            self.assertEqual(len(parsed["PARTS"]), 2)
            self.assertEqual(parsed["MIR"]["LOT_ID"], "LOT_GZ.00")

            summary = parse_check_summary(gz_path, expected_lot_id="LOT_GZ.00")
            self.assertEqual(summary["discrepancy_text"], "None")
            self.assertEqual(summary["prr_total"], 2)
            self.assertEqual(summary["prr_good"], 1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_19_file_picker_extensions_alignment(self):
        """Item 1: Verify STDF_EXTENSIONS and STDF_FILEDIALOG_EXTENSIONS include .bak and .stdf_open and align across all pickers."""
        from apps.ftdc_checker.stdf_fetcher import STDF_EXTENSIONS, STDF_FILEDIALOG_EXTENSIONS
        import apps.ftdc_checker.ui as ftdc_ui
        import inspect

        for ext in (".stdf", ".std", ".bak", ".old", ".stdf_open", ".std_open", ".gz"):
            self.assertIn(ext, STDF_EXTENSIONS)
            self.assertIn(f"*{ext}", STDF_FILEDIALOG_EXTENSIONS.split())

        src = inspect.getsource(ftdc_ui.FTDCCheckerFrame.select_files)
        self.assertIn("STDF_FILEDIALOG_EXTENSIONS", src)

    def test_20_ftdc_fail_backup_url_column_validation_and_deduplication(self):
        """Item 3: Verify Get FTDC Fail backup URL fallback, column header validation (6, 7, 9), and deduplicated parser."""
        from unittest.mock import patch, MagicMock
        from apps.ftdc_checker.ui import (
            FTDC_FAIL_PRIMARY_URL,
            FTDC_FAIL_BACKUP_URL,
            parse_ftdc_fail_html,
            fetch_and_parse_ftdc_fail,
        )

        self.assertEqual(
            {FTDC_FAIL_PRIMARY_URL, FTDC_FAIL_BACKUP_URL},
            {
                "http://mph-vm-mphl2prd:8080/ftdc/filter.php",
                "http://mph-vm-mitprod.mchp-main.com:8080/ftdc/filter.php",
            },
        )

        # 1. Valid HTML with matching headers at col 6 ('Reply String'), col 7 ('Reply Message'), col 9 ('Comment')
        valid_html = """
        <html><body><table>
          <tr>
            <th>ID</th><th>Lot</th><th>MPC</th><th>Tester</th><th>Step</th><th>Device</th>
            <th>Reply String</th><th>Reply Message</th><th>BizRule</th><th>Comment</th>
          </tr>
          <tr>
            <td>1</td><td>LOT1</td><td>MPC1</td><td>V93K-01</td><td>FT1</td><td>DEV1</td>
            <td>FAIL</td><td>Yield below limit</td><td>BR1</td><td>Hold lot</td>
          </tr>
          <tr>
            <td>2</td><td>LOT1</td><td>MPC1</td><td>V93K-01</td><td>FT1</td><td>DEV1</td>
            <td>PASS</td><td>OK</td><td>BR1</td><td>Release</td>
          </tr>
        </table></body></html>
        """
        rows = parse_ftdc_fail_html(valid_html)
        self.assertEqual(rows, [("Yield below limit", "Hold lot", "V93K-01")])

        # 2. Missing or renamed required column must raise ValueError with layout message
        bad_html_col6 = valid_html.replace("<th>Reply String</th>", "<th>Status</th>")
        with self.assertRaises(ValueError) as ctx6:
            parse_ftdc_fail_html(bad_html_col6)
        self.assertIn("FTDC page layout changed. Expected columns: Result, Reply Message, Comment. Found :", str(ctx6.exception))

        bad_html_col7 = valid_html.replace("<th>Reply Message</th>", "<th>Notes</th>")
        with self.assertRaises(ValueError) as ctx7:
            parse_ftdc_fail_html(bad_html_col7)
        self.assertIn("FTDC page layout changed. Expected columns: Result, Reply Message, Comment. Found :", str(ctx7.exception))

        bad_html_col9 = valid_html.replace("<th>Comment</th>", "<th>Notes</th>")
        with self.assertRaises(ValueError) as ctx9:
            parse_ftdc_fail_html(bad_html_col9)
        self.assertIn("FTDC page layout changed. Expected columns: Result, Reply Message, Comment. Found :", str(ctx9.exception))

        # 3. Layout changes (shifted/rearranged columns) must survive and parse correctly
        shifted_html = """
        <html><body><table>
          <tr>
            <th>Comment</th><th>Tester</th><th>Reply Message</th><th>Result</th>
          </tr>
          <tr>
            <td>Hold lot</td><td>V93K-01</td><td>Yield below limit</td><td>FAIL</td>
          </tr>
        </table></body></html>
        """
        shifted_rows = parse_ftdc_fail_html(shifted_html)
        self.assertEqual(shifted_rows, [("Yield below limit", "Hold lot", "V93K-01")])

        # 4. Primary link broken -> automatically falls back to backup link
        import requests
        called_urls = []

        def fake_post(url, data=None, timeout=None):
            called_urls.append(url)
            if url == FTDC_FAIL_PRIMARY_URL:
                raise requests.exceptions.ConnectionError("Primary server down")
            resp = MagicMock()
            resp.status_code = 200
            resp.text = valid_html
            return resp

        with patch("requests.post", side_effect=fake_post):
            res = fetch_and_parse_ftdc_fail("LOT1")
        self.assertEqual(called_urls, [FTDC_FAIL_PRIMARY_URL, FTDC_FAIL_BACKUP_URL])
        self.assertEqual(res, [("Yield below limit", "Hold lot", "V93K-01")])

    def test_21_scan_test_list_discovers_later_part_tests_and_tsr(self):
        """Item 4: Verify scan_test_list never misses tests that only appear in later parts."""
        import struct
        import tempfile
        from apps.ftdc_checker.stdf_parser import scan_test_list

        # Build a file where the first 25 parts are failing parts (only test 101) and part 26 is a good part with test 999
        out = bytearray()
        out += self._rec(0, 10, bytes([2, 4]), "<")
        for i in range(25):
            out += self._rec(5, 10, bytes([1, 1]), "<")
            ptr_body = bytearray(struct.pack("<IBBBBf", 101, 1, 1, 0, 0, 1.0))
            ptr_body += self._cn("T_101") + self._cn("V") + bytes([0x0E, 0, 0, 0]) + struct.pack("<ff", -1.0, 5.0)
            out += self._rec(15, 10, bytes(ptr_body), "<")
            prr_body = bytearray(struct.pack("<BBBHHHhhI", 1, 1, 0x08, 1, 2, 2, -32768, -32768, 5))
            prr_body += self._cn(str(i + 1)) + b"\x00\x00"
            out += self._rec(5, 20, bytes(prr_body), "<")

        # Part 26: passing part that executes test 101 AND test 999
        out += self._rec(5, 10, bytes([1, 1]), "<")
        for tnum in (101, 999):
            ptr_body = bytearray(struct.pack("<IBBBBf", tnum, 1, 1, 0, 0, 2.5))
            ptr_body += self._cn(f"T_{tnum}") + self._cn("mA") + bytes([0x0E, 0, 0, 0]) + struct.pack("<ff", -2.0, 10.0)
            out += self._rec(15, 10, bytes(ptr_body), "<")
        prr_body = bytearray(struct.pack("<BBBHHHhhI", 1, 1, 0x00, 2, 1, 1, -32768, -32768, 10))
        prr_body += self._cn("26") + b"\x00\x00"
        out += self._rec(5, 20, bytes(prr_body), "<")

        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(bytes(out))
            fpath = tf.name
        try:
            tests = scan_test_list(fpath)
            test_nums = [t["TEST_NUM"] for t in tests]
            self.assertEqual(test_nums, [101, 999])
            t999 = [t for t in tests if t["TEST_NUM"] == 999][0]
            self.assertEqual(t999["LO_LIMIT"], -2.0)
            self.assertEqual(t999["HI_LIMIT"], 10.0)
        finally:
            os.remove(fpath)

    def test_22_copy_stdf_part_staging_copyfileexw_and_telemetry(self):
        """Item 5: Verify CopyFileExW/fallback copies via .part staging, renames on completion, and emits telemetry stats."""
        import tempfile
        import shutil
        from unittest.mock import patch
        from apps.ftdc_checker.stdf_fetcher import (
            copy_stdf_files_per_file,
            format_copy_stats_text,
            format_parallel_copy_stats_text,
            _fast_copy_file_with_progress,
        )

        # 1. Verify telemetry string format matches both single-file and Option 2 parallel format
        sample_label = format_copy_stats_text(
            2,
            5,
            int(1.8 * (1024 ** 3)),
            int(3.4 * (1024 ** 3)),
            (1.8 * (1024 ** 3)) / (410.0 * (1024 ** 2)),
        )
        self.assertIn("File 2/5 | 1.8GB/3.4GB | 410MB/s | ETA ", sample_label)

        parallel_label = format_parallel_copy_stats_text(
            2,
            5,
            int(1.8 * (1024 ** 3)),
            int(3.4 * (1024 ** 3)),
            (1.8 * (1024 ** 3)) / (410.0 * (1024 ** 2)),
        )
        self.assertIn("Done 2/5 | 1.8GB/3.4GB | 410MB/s | ETA ", parallel_label)

        # 2. Verify Option 2 parallel copy uses .part staging, cleans up .part on rename,
        # emits monotonic aggregate progress fractions, and reports Done X/2 telemetry
        src_dir = tempfile.mkdtemp()
        dst_base = tempfile.mkdtemp()
        try:
            f1 = os.path.join(src_dir, "partA_20250101120000.std")
            f2 = os.path.join(src_dir, "partB_20250101130000.std.bak")
            with open(f1, "wb") as fp:
                fp.write(b"A" * 65536)
            with open(f2, "wb") as fp:
                fp.write(b"B" * 131072)

            stats_seen = []
            progress_seen = []
            with patch("apps.ftdc_checker.stdf_fetcher.LOCAL_DEST_BASE", dst_base):
                dest_dir = copy_stdf_files_per_file(
                    [f1, f2],
                    "LOT_COPY_TEST",
                    per_file_progress_callback=lambda frac, msg: progress_seen.append((frac, msg)),
                    copy_stats_callback=stats_seen.append,
                )
                copied_files = sorted(os.listdir(dest_dir))
                self.assertEqual(copied_files, ["partA_20250101120000.std", "partB_20250101130000.std.bak"])
                # Ensure no leftover .part files exist
                self.assertFalse(any(name.endswith(".part") for name in copied_files))
                # Ensure copy_stats_callback was invoked with Done X/2 telemetry (Option 2)
                self.assertTrue(any("Done 0/2 |" in s or "Done 1/2 |" in s for s in stats_seen))
                self.assertTrue(any("Done 2/2 |" in s for s in stats_seen))
                # Ensure aggregate progress bar fractions are monotonically non-decreasing and end at 1.0
                self.assertTrue(len(progress_seen) >= 2)
                fractions = [p[0] for p in progress_seen]
                self.assertEqual(fractions, sorted(fractions))
                self.assertAlmostEqual(fractions[-1], 1.0, places=4)
                self.assertIn("Copied 2/2 file(s)", progress_seen[-1][1])

            # 3. Also verify non-Windows Python loop fallback works identically
            dst_fallback = os.path.join(dst_base, "fallback_test.std")
            with patch("os.name", "posix"):
                _fast_copy_file_with_progress(f1, dst_fallback)
            self.assertTrue(os.path.isfile(dst_fallback))
            self.assertEqual(os.path.getsize(dst_fallback), 65536)
        finally:
            shutil.rmtree(src_dir, ignore_errors=True)
            shutil.rmtree(dst_base, ignore_errors=True)

    def test_23_check_stdf_mrr_must_be_last_record_and_prr_counts_used(self):
        """Items 6 & 7: Verify MRR must be the last record (offset + len == file_size) and Check STDF uses PRR counts."""
        import tempfile
        from apps.ftdc_checker.stdf_parser import parse_check_summary, validate_stdf_data, parse_stdf_file

        # Build a valid STDF file with MRR, then append another record after MRR so MRR is NOT the last record
        valid_bytes = self._build_synthetic_stdf(
            lot_id="LOT123.00",
            pcr_counts=(10, 0, 0, 9, 0),  # PCR intentionally different from PRR (2 total, 1 good)
            include_mrr=True,
        )
        trailing_dtr = self._rec(50, 30, self._cn("TRAILING_DTR_AFTER_MRR"), "<")
        non_terminal_mrr_bytes = valid_bytes + trailing_dtr

        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(non_terminal_mrr_bytes)
            fpath = tf.name
        try:
            summary = parse_check_summary(fpath, expected_lot_id="LOT123.00")
            # MRR was present, but NOT the last record at EOF!
            self.assertIsNotNone(summary["mrr"])
            self.assertFalse(summary["mrr_is_last"])
            self.assertIn("MRR Not at EOF", summary["discrepancy_text"])
            # PRR counts are 2 total and 1 good (independent of PCR's 10 and 9)
            self.assertEqual(summary["prr_total"], 2)
            self.assertEqual(summary["prr_good"], 1)

            parsed, _, _ = parse_stdf_file(fpath, verbose=False)
            val = validate_stdf_data(parsed)
            self.assertFalse(val["valid"])
            self.assertIn("NO_MRR", val["reasons"])
            self.assertTrue(any("not the last record" in m for m in val["messages"]))
        finally:
            os.remove(fpath)


    def test_24_new_13_requirements_verification(self):
        """Verify Items 1-13: NaN_NaN_NaN exclusion, mandatory QC, Check 2 <= 72, 12-char MPC validation,
        Excel background progress & optimization, dead code removal, session logging, and zero bare except pass."""
        import ast
        import inspect
        import tempfile
        import shutil
        from apps.ftdc_checker.guid_analysis import (
            analyze_guid_data,
            validate_mpc_code,
            resolve_wxy_test_numbers,
            INVALID_WXY,
        )
        from apps.ftdc_checker.excel_output import write_result_excel
        import apps.ftdc_checker.stdf_parser as stdf_parser
        import apps.ftdc_checker.ui as ftdc_ui
        import core.hub_window as hub_window

        # Item 8: 12-character MPC validation
        self.assertEqual(validate_mpc_code("59793QRTBC01"), "59793QRTBC01")
        with self.assertRaises(ValueError):
            validate_mpc_code("59793")
        with self.assertRaises(ValueError):
            validate_mpc_code("Looking up…")
        with self.assertRaises(ValueError):
            validate_mpc_code("Lot ID not found")

        # Item 6: Static "● Online" label removed from HubWindow
        hub_src = inspect.getsource(hub_window.HubWindow)
        self.assertNotIn("Online", hub_src)

        # Item 12: Dead code removed (TEST_META, SDR, scanned_tests parameter, chosen_tester = "LTX")
        self.assertFalse(hasattr(stdf_parser, "parse_sdr_compact"))
        self.assertFalse(hasattr(stdf_parser, "_SDR_STRING_KEYS"))
        sig = inspect.signature(resolve_wxy_test_numbers)
        self.assertNotIn("scanned_tests", sig.parameters)
        ui_src = inspect.getsource(ftdc_ui)
        self.assertNotIn('chosen_tester = "LTX"', ui_src)

        # Item 13: No bare `except Exception: pass` across codebase
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        py_files = [
            os.path.join(project_root, "main.py"),
            os.path.join(project_root, "core", "hub_window.py"),
            os.path.join(project_root, "core", "theme.py"),
            os.path.join(project_root, "apps", "ftdc_checker", "stdf_parser.py"),
            os.path.join(project_root, "apps", "ftdc_checker", "guid_analysis.py"),
            os.path.join(project_root, "apps", "ftdc_checker", "excel_output.py"),
            os.path.join(project_root, "apps", "ftdc_checker", "ui.py"),
        ]
        for py_path in py_files:
            with open(py_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=py_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    is_bare_or_exception = node.type is None or (isinstance(node.type, ast.Name) and node.type.id == "Exception")
                    if is_bare_or_exception and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        self.fail(f"Found bare 'except Exception: pass' in {py_path} at line {node.lineno}")

        # Build synthetic STDF where passing part has NaN / missing WXY tests so WXY is NaN_NaN_NaN
        # Verify Item 2 (NaN_NaN_NaN excluded from FP/RT duplicate count), Item 3 (QC mandatory), Item 4 (Check 2 <= 72)
        stdf_bytes = self._build_synthetic_stdf(
            lot_id="LOT123.00",
            pcr_counts=(2, 0, 0, 1, 0),
        )
        temp_dir = tempfile.mkdtemp()
        try:
            fp_file = os.path.join(temp_dir, "fp_e_20250101120000.std")
            rt_file = os.path.join(temp_dir, "rt_rj1_w_20250101130000.std")
            qc_file = os.path.join(temp_dir, "qc_rj1_q_20250101140000.std")
            for p in (fp_file, rt_file, qc_file):
                with open(p, "wb") as f:
                    f.write(stdf_bytes)

            # Item 3: Missing QC file must raise ValueError
            with self.assertRaises(ValueError) as qc_ctx:
                analyze_guid_data(
                    first_pass_paths=[fp_file],
                    retest_paths=[rt_file],
                    qc_paths=[],
                    lot_id="LOT123.00",
                    mpc_text="59793QRTBC01",
                    first_pass_actual_good_qty="1",
                    total_actual_good_qty="2",
                    manual_filter_tests=[101, 102, 103],
                )
            self.assertIn("QC STDF file", str(qc_ctx.exception))

            # Item 2 & Item 4: Run analysis with manual filter pointing to non-existent test numbers (901, 902, 903)
            # so every good part gets WXY = "NaN_NaN_NaN". Ensure duplicate count is 0 (not inflated by NaN_NaN_NaN)
            # and Check 2 is PASS (0 <= 72).
            res_nan = analyze_guid_data(
                first_pass_paths=[fp_file, fp_file],
                retest_paths=[rt_file, rt_file],
                qc_paths=[qc_file],
                lot_id="LOT123.00",
                mpc_text="59793QRTBC01",
                first_pass_actual_good_qty="2",
                total_actual_good_qty="4",
                manual_filter_tests=[901, 902, 903],
            )
            self.assertEqual(INVALID_WXY, "NaN_NaN_NaN")
            self.assertEqual(res_nan["duplicate_guid"], 0)
            self.assertEqual(res_nan["retest_duplicate_guid"], 0)
            self.assertEqual(res_nan["total_duplicate_guid"], 0)
            self.assertEqual(res_nan["check2_result"], "PASS")

            # Item 5: Excel output writes cleanly with progress_callback and updated Check 2 description
            progress_events = []
            excel_path = os.path.join(temp_dir, "result.xlsx")
            write_result_excel(
                res_nan,
                save_path=excel_path,
                progress_callback=lambda frac, msg: progress_events.append((frac, msg)),
            )
            self.assertTrue(os.path.isfile(excel_path))
            self.assertTrue(len(progress_events) >= 2)
            self.assertEqual(progress_events[-1][0], 1.0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_25_new_6_requirements_verification(self):
        """Verify the 6 new requirements:
        1. STDF parser integrity flags (ended_at_eof, last_record_was_mrr, pir_without_prr, PRR vs PCR) & warnings
        2. FTDC fail parsing survives layout shifts, fails loudly with layout error, and saves raw HTML
        3. Advance Excel generation in background thread, temp file caching, in-progress warning, cleanup on clear_all
        4. Normalized inputs (case-insensitive Lot ID, MPC, integer qty validation)
        5. settings.json configuration for URLs and base paths
        6. Clean package imports
        """
        import tempfile
        import shutil
        from unittest.mock import patch, MagicMock
        from core.config import (
            load_settings, get_mes_url, get_ftdc_fail_urls, get_local_dest_base, get_ppo_base_path
        )
        from apps.ftdc_checker.stdf_parser import parse_stdf_file, parse_check_summary
        from apps.ftdc_checker.guid_analysis import analyze_guid_data, validate_mpc_code
        from apps.ftdc_checker.ui import (
            parse_ftdc_fail_html, fetch_and_parse_ftdc_fail, FTDCCheckerFrame
        )

        # ── Requirement 5: settings.json endpoints and paths ──
        settings = load_settings()
        self.assertIn("mes_url", settings)
        self.assertIn("ftdc_fail_primary_url", settings)
        self.assertIn("ftdc_fail_backup_url", settings)
        self.assertIn("local_dest_base", settings)
        self.assertIn("ppo_base_path", settings)
        self.assertTrue(get_mes_url().startswith("http"))
        self.assertEqual(len(get_ftdc_fail_urls()), 2)
        self.assertTrue(os.path.isabs(get_local_dest_base()) or get_local_dest_base().startswith("C:"))
        self.assertTrue(get_ppo_base_path().startswith("\\\\"))

        # ── Requirement 1: STDF integrity flags ──
        valid_stdf_bytes = self._build_synthetic_stdf(endian="<", cpu_typ=2, stdf_ver=4, pcr_counts=(2, 0, 0, 1, 0))
        temp_dir = tempfile.mkdtemp()
        try:
            valid_path = os.path.join(temp_dir, "valid.std")
            with open(valid_path, "wb") as f:
                f.write(valid_stdf_bytes)

            parsed, _, _ = parse_stdf_file(valid_path, verbose=False)
            self.assertTrue(parsed.get("ended_at_eof"))
            self.assertTrue(parsed.get("last_record_was_mrr"))
            self.assertEqual(parsed.get("pir_without_prr"), 0)
            self.assertEqual(parsed.get("prr_count"), 2)
            self.assertEqual(parsed.get("pcr_part_cnt"), 2)
            self.assertTrue(parsed.get("prr_vs_pcr_match"))
            self.assertFalse(parsed.get("is_truncated"))

            summary = parse_check_summary(valid_path)
            self.assertTrue(summary.get("ended_at_eof"))
            self.assertTrue(summary.get("last_record_was_mrr"))
            self.assertEqual(summary.get("prr_count"), 2)
            self.assertFalse(summary.get("is_truncated"))

            # Truncated STDF: slice off last 10 bytes (breaks MRR and EOF)
            trunc_path = os.path.join(temp_dir, "trunc.std")
            with open(trunc_path, "wb") as f:
                f.write(valid_stdf_bytes[:-10])

            parsed_tr, _, _ = parse_stdf_file(trunc_path, verbose=False)
            self.assertFalse(parsed_tr.get("ended_at_eof"))
            self.assertFalse(parsed_tr.get("last_record_was_mrr"))
            self.assertTrue(parsed_tr.get("is_truncated"))

            # Analysis with truncated file must log warning and report truncated_files
            logs = []
            res_trunc = analyze_guid_data(
                first_pass_paths=[trunc_path],
                retest_paths=[valid_path],
                qc_paths=[valid_path],
                lot_id="LOT123.00",
                mpc_text="59793QRTBC01",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="2",
                manual_filter_tests=[101, 102, 103],
                logger=logs.append,
            )
            self.assertTrue(any("TRUNCATED STDF FILE" in log for log in logs))
            self.assertIn("truncated_files", res_trunc)
            self.assertGreater(len(res_trunc["truncated_files"]), 0)
            self.assertEqual(res_trunc["truncated_files"][0]["filename"], "trunc.std")

            # ── Requirement 2: FTDC fail layout shift survival & loud layout error with HTML save ──
            # Reordered columns (Comment, Reply Message, Result in non-standard indices) must survive
            shifted_html = (
                "<html><body><table>"
                "<tr><th>Lot</th><th>Comment</th><th>Tester</th><th>Reply Message</th><th>Result</th></tr>"
                "<tr><td>LOT1</td><td>Hold lot</td><td>LTX-01</td><td>Yield below limit</td><td>FAIL</td></tr>"
                "</table></body></html>"
            )
            rows = parse_ftdc_fail_html(shifted_html)
            self.assertEqual(rows, [("Yield below limit", "Hold lot", "LTX-01")])

            # Missing required column must fail loudly with expected layout message
            broken_html = (
                "<html><body><table>"
                "<tr><th>Lot</th><th>Tester</th><th>Status</th></tr>"
                "<tr><td>LOT1</td><td>LTX-01</td><td>FAIL</td></tr>"
                "</table></body></html>"
            )
            with self.assertRaises(ValueError) as err_ctx:
                parse_ftdc_fail_html(broken_html)
            self.assertIn("FTDC page layout changed. Expected columns: Result, Reply Message, Comment. Found :", str(err_ctx.exception))

            # fetch_and_parse_ftdc_fail must save HTML to destination folder when layout error occurs
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.text = broken_html
                mock_post.return_value = mock_resp

                with patch("apps.ftdc_checker.ui.get_local_dest_base", return_value=temp_dir):
                    with self.assertRaises(ValueError):
                        fetch_and_parse_ftdc_fail("TESTLOT")
                    saved_files = os.listdir(os.path.join(temp_dir, "TESTLOT"))
                    self.assertTrue(any(f.startswith("ftdc_fail_layout_error_") and f.endswith(".html") for f in saved_files))

            # ── Requirement 4: Normalized inputs (case-insensitive) ──
            self.assertEqual(validate_mpc_code("  59793qrtbc01  "), "59793QRTBC01")
            res_lower = analyze_guid_data(
                first_pass_paths=[valid_path],
                retest_paths=[valid_path],
                qc_paths=[valid_path],
                lot_id="  lot123.00  ",
                mpc_text="  59793qrtbc01  ",
                first_pass_actual_good_qty=" 1 ",
                total_actual_good_qty=" 2 ",
                manual_filter_tests=[101, 102, 103],
            )
            self.assertEqual(res_lower["lot_id"], "LOT123.00")
            self.assertEqual(res_lower["mpc"], "59793QRTBC01")

            # ── Requirement 3: Advance Excel generation & in-progress handling ──
            ftdc = FTDCCheckerFrame(self.root)
            ftdc._start_background_excel_generation(res_lower, auto_attach=False)
            self.assertIn(ftdc._excel_state, ("IN_PROGRESS", "READY"))
            self.assertIsNotNone(ftdc._excel_temp_path)
            self.assertIn(ftdc._excel_temp_path, ftdc._excel_temp_files)
            if ftdc._excel_bg_thread:
                ftdc._excel_bg_thread.join(timeout=5.0)
            self.assertEqual(ftdc._excel_state, "READY")
            self.assertTrue(os.path.isfile(ftdc._excel_temp_path))

            ftdc.clear_all()
            self.assertEqual(ftdc._excel_state, "IDLE")
            self.assertEqual(len(ftdc._excel_temp_files), 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_26_fast_tail_scan_check_stdf_optimization(self):
        """Verify Check STDF C-accelerated PRR jump-scan on large STDF (> 4 MB) and MIR LOT_ID folder preservation:
        - Sub-250ms execution instead of multi-second full sequential PTR walk.
        - True PRR count (e.g. 2000 parts) even when PCR reports 0, plus PCR & SBR vs PRR mismatch reporting.
        - MIR LOT_ID with period (e.g. MMT-271901180.000) preserved without replacing '.' with '_'.
        """
        import re
        import struct
        import tempfile
        import time
        from apps.ftdc_checker.stdf_parser import parse_check_summary
        from apps.ftdc_checker.guid_analysis import analyze_guid_data

        # Build a synthetic > 5 MB file with 2,000 PRR parts (1,800 pass + 200 fail) x 80 PTRs = 160,000 PTRs
        header = bytearray()
        header += self._rec(0, 10, bytes([2, 4]), "<")  # FAR
        mir = bytearray(struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
        mir += ord(" ").to_bytes(1, "little")
        for s in ("MMT-271901180.000", "DEV1", "NODE1", "V93K", "JOB1"):
            mir += self._cn(s)
        for _ in range(25):
            mir += b"\x00"
        header += self._rec(1, 10, bytes(mir), "<")

        part_pass = bytearray()
        part_pass += self._rec(5, 10, bytes([1, 1]), "<")
        for t in range(80):
            ptr = struct.pack("<IBBBBf", t + 1, 1, 1, 0, 0, 1.0) + bytes([1]) + b"T" + bytes([0, 0x0E, 0, 0, 0]) + struct.pack("<ff", -1.0, 5.0)
            part_pass += self._rec(15, 10, ptr, "<")
        prr_p = struct.pack("<BBBHHHhhI", 1, 1, 0x00, 80, 1, 1, -32768, -32768, 5) + bytes([1]) + b"1" + b"\x00\x00"
        part_pass += self._rec(5, 20, prr_p, "<")

        part_fail = bytearray(part_pass[:-len(prr_p) - 4])
        prr_f = struct.pack("<BBBHHHhhI", 1, 1, 0x08, 80, 2, 2, -32768, -32768, 5) + bytes([1]) + b"2" + b"\x00\x00"
        part_fail += self._rec(5, 20, prr_f, "<")

        parts_blob = part_pass * 1800 + part_fail * 200
        self.assertGreater(len(parts_blob), 4 * 1024 * 1024, "Test file must exceed 4 MB")

        # Case A: Matching SBR (2000 total, 1800 good) and PCR (2000 total, 1800 good)
        tail_ok = bytearray()
        tail_ok += self._rec(1, 50, struct.pack("<BBHI", 255, 0, 1, 1800) + b"P" + self._cn("PASS"), "<")
        tail_ok += self._rec(1, 50, struct.pack("<BBHI", 255, 0, 2, 200) + b"F" + self._cn("FAIL"), "<")
        tail_ok += self._rec(1, 30, struct.pack("<BBIIIII", 255, 0, 2000, 0, 0, 1800, 0), "<")
        tail_ok += self._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")

        # Case B: Broken PCR (reports 0 parts) while PRR actually has 2,000 parts!
        tail_zero_pcr = bytearray()
        tail_zero_pcr += self._rec(1, 50, struct.pack("<BBHI", 255, 0, 1, 1800) + b"P" + self._cn("PASS"), "<")
        tail_zero_pcr += self._rec(1, 30, struct.pack("<BBIIIII", 255, 0, 0, 0, 0, 0, 0), "<")
        tail_zero_pcr += self._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")

        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf_ok:
            tf_ok.write(bytes(header + parts_blob + tail_ok))
            fpath_ok = tf_ok.name
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf_zero:
            tf_zero.write(bytes(header + parts_blob + tail_zero_pcr))
            fpath_zero = tf_zero.name

        try:
            # 1. Verify matching file completes in milliseconds with exact 2,000 PRR count and "None" discrepancy
            t0 = time.time()
            summary_ok = parse_check_summary(fpath_ok, expected_lot_id="MMT-271901180.000")
            elapsed = time.time() - t0
            self.assertLess(elapsed, 1.5, f"Check STDF too slow: took {elapsed:.3f}s")
            self.assertEqual(summary_ok["prr_total"], 2000)
            self.assertEqual(summary_ok["prr_good"], 1800)
            self.assertTrue(summary_ok["mrr_is_last"])
            self.assertTrue(summary_ok["ended_at_eof"])
            self.assertFalse(summary_ok["truncated"])
            self.assertEqual(summary_ok["discrepancy_text"], "None")
            self.assertEqual(summary_ok["mir"]["LOT_ID"], "MMT-271901180.000")

            # 2. Verify file with PCR=0 still reports 2,000 PRR parts (NOT 0!) and flags PCR/SBR vs PRR mismatch
            summary_zero = parse_check_summary(fpath_zero, expected_lot_id="MMT-271901180.000")
            self.assertEqual(summary_zero["prr_total"], 2000)
            self.assertEqual(summary_zero["prr_good"], 1800)
            self.assertIn("PCR Part(0) != PRR(2000)", summary_zero["discrepancy_text"])
            self.assertIn("PCR Good(0) != PRR Good(1800)", summary_zero["discrepancy_text"])
            self.assertIn("SBR Part(1800) != PRR(2000)", summary_zero["discrepancy_text"])

            # 3. Verify MIR LOT_ID folder preservation (period '.' preserved in MMT-271901180.000)
            small_stdf = self._build_synthetic_stdf(lot_id="MMT-271901180.000", pcr_counts=(2, 0, 0, 1, 0))
            with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf_sm:
                tf_sm.write(small_stdf)
                sm_path = tf_sm.name
            try:
                res = analyze_guid_data(
                    first_pass_paths=[sm_path],
                    retest_paths=[sm_path],
                    qc_paths=[sm_path],
                    lot_id="MMT-271901180.000",
                    mpc_text="59793QRTBC01",
                    first_pass_actual_good_qty="1",
                    total_actual_good_qty="1",
                    manual_filter_tests=[101, 102, 103],
                )
                self.assertEqual(res.get("mir_lot_id"), "MMT-271901180.000")
                mir_lot_text = str(res.get("mir_lot_id") or res.get("lot_id")).strip().upper()
                safe_lot_folder = re.sub(r'[<>:"/\\|?*]+', "_", mir_lot_text).strip(" .")
                self.assertEqual(safe_lot_folder, "MMT-271901180.000")
                self.assertNotIn("MMT-271901180_000", safe_lot_folder)
            finally:
                os.remove(sm_path)
        finally:
            os.remove(fpath_ok)
            os.remove(fpath_zero)

    def test_27_truncated_no_pcr_auto_ppo_attach_and_local_download(self):
        """Verify:
        - parse_stdf_file no longer crashes when a truncated file has no PCR (summary_pcr is None).
        - Excel is automatically attached to PPO after background generation (atomic .part -> rename).
        - Failed PPO attach is retried automatically, then a local copy is saved to C:\\FTDC\\{MIR LOT_ID}.
        - Download path uses MIR LOT_ID with periods preserved.
        """
        import struct
        import tempfile
        from unittest import mock
        from apps.ftdc_checker.stdf_parser import parse_stdf_file
        from apps.ftdc_checker import ui as ftdc_ui
        from apps.ftdc_checker.ui import FTDCCheckerFrame

        # ── 1. Truncated STDF: FAR + MIR + 1 complete part + 4 parts without PRR, no PCR/MRR ──
        data = bytearray(self._rec(0, 10, bytes([2, 4]), "<"))
        mir = bytearray(struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
        mir += b" "
        for s in ("MMT-272900204.000", "DEV1", "NODE1", "V93K", "JOB1"):
            mir += self._cn(s)
        mir += b"\x00" * 25
        data += self._rec(1, 10, bytes(mir), "<")
        ptr = struct.pack("<IBBBBf", 1, 1, 1, 0, 0, 1.0) + bytes([1]) + b"T" + bytes([0, 0x0E, 0, 0, 0]) + struct.pack("<ff", -1.0, 5.0)
        data += self._rec(5, 10, bytes([1, 1]), "<") + self._rec(15, 10, ptr, "<")
        data += self._rec(5, 20, struct.pack("<BBBHHHhhI", 1, 1, 0, 1, 1, 1, -32768, -32768, 5) + b"\x011\x00\x00", "<")
        for _ in range(4):
            data += self._rec(5, 10, bytes([1, 1]), "<") + self._rec(15, 10, ptr, "<")
        with tempfile.NamedTemporaryFile(suffix=".std", delete=False) as tf:
            tf.write(bytes(data))
            trunc_path = tf.name
        try:
            parsed, _counts, _ = parse_stdf_file(trunc_path, filter_tests={1}, verbose=False)
            self.assertIsNone(parsed["PCR"])
            self.assertIsNone(parsed["pcr_part_cnt"])
            self.assertEqual(parsed["pir_without_prr"], 4)
            self.assertEqual(parsed["prr_count"], 1)
            self.assertTrue(parsed["is_truncated"])
        finally:
            os.remove(trunc_path)

        # ── 2. Automatic PPO attach (success) + 3. retry then local fallback ──
        temp_dir = tempfile.mkdtemp()
        try:
            ppo_base = os.path.join(temp_dir, "PPO")
            local_base = os.path.join(temp_dir, "LOCAL")
            sub_a = os.path.join(ppo_base, "59793", "MMT-272900204.000", "OPER_A")
            sub_b = os.path.join(ppo_base, "59793", "MMT-272900204.000", "OPER_B")
            os.makedirs(sub_a)
            os.makedirs(sub_b)
            src_xlsx = os.path.join(temp_dir, "src.xlsx")
            with open(src_xlsx, "wb") as fh:
                fh.write(b"PK-dummy-xlsx")
            result = {"lot_id": "MMT-272900204.000", "mir_lot_id": "MMT-272900204.000", "mpc": "59793QRTBC01"}

            ftdc = FTDCCheckerFrame(self.root)
            ftdc.PPO_ATTACH_RETRY_DELAYS = (0.0, 0.0)
            notices = []
            ftdc._queue_ppo_notice = lambda kind, title, msg, open_dir=None: notices.append((kind, title, msg, open_dir))
            gen_id = ftdc._excel_generation_id
            with mock.patch.object(ftdc_ui, "is_developer_mode", return_value=False), \
                 mock.patch.object(ftdc_ui, "get_ppo_base_path", return_value=ppo_base), \
                 mock.patch.object(ftdc_ui, "get_local_dest_base", return_value=local_base):
                ftdc._auto_attach_excel_to_ppo(result, src_xlsx, "MMT-272900204.000_FTDC_X.xlsx", gen_id)
                self.assertEqual(ftdc._ppo_attach_state, "SUCCESS")
                self.assertTrue(os.path.isfile(os.path.join(sub_a, "MMT-272900204.000_FTDC_X.xlsx")))
                self.assertTrue(os.path.isfile(os.path.join(sub_b, "MMT-272900204.000_FTDC_X.xlsx")))
                self.assertFalse(any(f.endswith(".part") for f in os.listdir(sub_a)))
                self.assertEqual(notices[-1][0], "info")

                # Missing PPO lot folder -> retried 3 times, then local copy saved for manual transfer
                notices.clear()
                bad = dict(result, lot_id="MISSING.000")
                calls = {"n": 0}
                real_isdir = os.path.isdir

                def _counting_isdir(p):
                    if p.endswith("MISSING.000"):
                        calls["n"] += 1
                    return real_isdir(p)

                with mock.patch.object(ftdc_ui.os.path, "isdir", side_effect=_counting_isdir):
                    ftdc._auto_attach_excel_to_ppo(bad, src_xlsx, "MMT-272900204.000_FTDC_Y.xlsx", gen_id)
                self.assertEqual(ftdc._ppo_attach_state, "FAILED")
                self.assertEqual(calls["n"], ftdc.PPO_ATTACH_MAX_ATTEMPTS)
                self.assertEqual(notices[-1][0], "error")
                local_copy = os.path.join(local_base, "MMT-272900204.000", "MMT-272900204.000_FTDC_Y.xlsx")
                self.assertTrue(os.path.isfile(local_copy))
                self.assertIn(local_copy, notices[-1][2])

                # Download path keeps the period in MIR LOT_ID
                dest = FTDCCheckerFrame._local_excel_dest(result, "a.xlsx")
                self.assertEqual(os.path.basename(os.path.dirname(dest)), "MMT-272900204.000")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_28_fp_duplicate_fail_and_new_excel_sheets(self):
        """Verify:
        1) Duplicated WXY in FP datalog triggers FAIL (First Pass Result, Total Good Result, Check 1)
           regardless of PASS/FAIL test result and regardless of negative variance, while keeping
           Duplicate Good UID subtraction for PASS duplicates only.
        2) Excel sheet 'Duplicate GUID First Pass' contains all duplicate WXYs and their original WXYs
           in FP datalog using the FT Total columns without summary metrics.
        3) Excel sheet 'QC units FAIL in prev FT Step' contains all QC units not seen with PASS result
           across all FP and RETEST datalogs using the QC Total columns without summary metrics.
        """
        import struct
        import tempfile
        import zipfile
        import xml.etree.ElementTree as ET
        from apps.ftdc_checker.guid_analysis import analyze_guid_data
        from apps.ftdc_checker.excel_output import write_result_excel

        def _build_stdf_with_parts(lot_id: str, parts_spec):
            """parts_spec: list of (part_id_str, is_pass, hbin, sbin, wafer, x, y)."""
            buf = bytearray(self._rec(0, 10, bytes([2, 4]), "<"))  # FAR
            mir = bytearray(struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
            mir += b" "
            for s in (lot_id, "DEV1", "NODE1", "V93K", "JOB1"):
                mir += self._cn(s)
            mir += b"\x00" * 25
            buf += self._rec(1, 10, bytes(mir), "<")
            good_cnt = 0
            for pid, is_pass, hbin, sbin, w, x, y in parts_spec:
                buf += self._rec(5, 10, bytes([1, 1]), "<")  # PIR
                for tnum, val in ((101, float(w)), (102, float(x)), (103, float(y))):
                    ptr = (
                        struct.pack("<IBBBBf", tnum, 1, 1, 0, 0, val)
                        + bytes([1]) + b"T" + bytes([0, 0x0E, 0, 0, 0])
                        + struct.pack("<ff", -1000.0, 1000.0)
                    )
                    buf += self._rec(15, 10, ptr, "<")
                flg = 0x00 if is_pass else 0x08
                if is_pass:
                    good_cnt += 1
                prr = (
                    struct.pack("<BBBHHHhhI", 1, 1, flg, 3, hbin, sbin, -32768, -32768, 5)
                    + self._cn(pid) + b"\x00\x00"
                )
                buf += self._rec(5, 20, prr, "<")
            buf += self._rec(1, 30, struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt, 0), "<")
            buf += self._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
            return bytes(buf)

        def _read_xlsx_sheets(xlsx_path: str):
            """Parse all worksheet names and cell text rows from an xlsx file via zipfile+xml."""
            ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            with zipfile.ZipFile(xlsx_path, "r") as zf:
                shared_strings = []
                if "xl/sharedStrings.xml" in zf.namelist():
                    ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                    for si in ss_root.findall("main:si", ns):
                        texts = [t.text or "" for t in si.findall(".//main:t", ns)]
                        shared_strings.append("".join(texts))
                wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
                sheets = {}
                for idx, sh in enumerate(wb_root.findall(".//main:sheet", ns), start=1):
                    name = sh.attrib["name"]
                    ws_root = ET.fromstring(zf.read(f"xl/worksheets/sheet{idx}.xml"))
                    rows_out = []
                    for r_el in ws_root.findall(".//main:sheetData/main:row", ns):
                        row_vals = []
                        for c_el in r_el.findall("main:c", ns):
                            c_type = c_el.attrib.get("t", "")
                            v_el = c_el.find("main:v", ns)
                            if v_el is not None and v_el.text is not None:
                                if c_type == "s":
                                    row_vals.append(shared_strings[int(v_el.text)])
                                else:
                                    row_vals.append(v_el.text)
                            else:
                                is_el = c_el.find(".//main:t", ns)
                                row_vals.append(is_el.text if is_el is not None and is_el.text else "")
                        rows_out.append(row_vals)
                    sheets[name] = rows_out
                return sheets

        temp_dir = tempfile.mkdtemp()
        try:
            # FP has PART123 (FAIL, 1_2_3), PART456 (PASS, 1_2_3), and PART789 (PASS, 1_5_6)
            # Notice: 1_2_3 is duplicated in FP (one FAIL + one PASS), and another unit 1_9_9 is FAIL in FP
            fp_path = os.path.join(temp_dir, "fp.std")
            rt_path = os.path.join(temp_dir, "rt.std")
            qc_path = os.path.join(temp_dir, "qc.std")
            with open(fp_path, "wb") as f:
                f.write(_build_stdf_with_parts("LOT777.00", [
                    ("PART123", False, 2, 2, 1, 2, 3),  # Original of 1_2_3 in FP (FAIL!)
                    ("PART456", True, 1, 1, 1, 2, 3),   # Duplicate of 1_2_3 in FP (PASS!)
                    ("PART789", True, 1, 1, 1, 5, 6),   # Unique PASS in FP (1_5_6)
                    ("PART888", False, 2, 2, 1, 9, 9),  # Unique FAIL in FP (1_9_9)
                ]))
            with open(rt_path, "wb") as f:
                f.write(_build_stdf_with_parts("LOT777.00", [
                    ("RT_PART1", False, 2, 2, 1, 9, 9),  # Still FAIL in RETEST (1_9_9)
                ]))
            with open(qc_path, "wb") as f:
                f.write(_build_stdf_with_parts("LOT777.00", [
                    ("QC_GOOD", True, 1, 1, 1, 5, 6),  # Passed in FP -> should NOT be in QC fail sheet
                    ("QC_DUP", True, 1, 1, 1, 2, 3),   # Passed in FP (on PART456) -> should NOT be in QC fail sheet
                ]))

            # Even though delta >= 0 (2 - 2 = 0), total_delta >= 0 (2 - 2 = 0), and all QC units passed (Check 4 = PASS),
            # the duplicated WXY 1_2_3 in FP (PART123 FAIL + PART456 PASS) MUST trigger FAIL!
            res = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id="LOT777.00",
                mpc_text="59793QRTBC01",
                first_pass_actual_good_qty="2",
                total_actual_good_qty="2",
                manual_filter_tests=[101, 102, 103],
            )
            self.assertEqual(res["delta_vs_fp_actual_good"], 0)
            self.assertEqual(res["delta_vs_total_actual_good"], 0)
            self.assertEqual(res["check4_result"], "PASS")
            # Duplicate Good UID subtraction is for PASS duplicates only (PART123 was FAIL, so duplicate_guid == 0)
            self.assertEqual(res["duplicate_guid"], 0)
            self.assertEqual(res["fp_datalog_duplicate_count"], 1)
            # Trigger FAIL on First Pass Result, Total Good Result, and Check 1!
            self.assertEqual(res["status"], "FAIL")
            self.assertEqual(res["total_good_status"], "FAIL")
            self.assertEqual(res["check1_result"], "FAIL")

            # When Check 4 is PASS, the 'QC units FAIL in prev FT Step' worksheet should have 0 data rows (header only)
            xlsx_all_pass_out = os.path.join(temp_dir, "all_qc_pass.xlsx")
            write_result_excel(res, xlsx_all_pass_out)
            sheets_pass = _read_xlsx_sheets(xlsx_all_pass_out)
            self.assertIn("QC units FAIL in prev FT Step", sheets_pass)
            self.assertEqual(len(sheets_pass["QC units FAIL in prev FT Step"]), 1)  # Only header row!

            # Now test with QC having units that did NOT pass in FP or RETEST (1_9_9 failed in FP/RT; 1_7_7 missing)
            with open(qc_path, "wb") as f:
                f.write(_build_stdf_with_parts("LOT777.00", [
                    ("QC_GOOD", True, 1, 1, 1, 5, 6),     # Seen with PASS in FP
                    ("QC_BAD_FT", True, 1, 1, 1, 9, 9),   # Only seen with FAIL in FP/RT -> must be in QC fail sheet
                    ("QC_MISSING", True, 1, 1, 1, 7, 7),  # Never seen in FP/RT -> must be in QC fail sheet
                ]))
            res2 = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id="LOT777.00",
                mpc_text="59793QRTBC01",
                first_pass_actual_good_qty="2",
                total_actual_good_qty="2",
                manual_filter_tests=[101, 102, 103],
            )
            xlsx_out = os.path.join(temp_dir, "out.xlsx")
            write_result_excel(res2, xlsx_out)
            sheets = _read_xlsx_sheets(xlsx_out)

            # Verify new worksheets exist
            self.assertIn("Duplicate GUID First Pass", sheets)
            self.assertIn("QC units FAIL in prev FT Step", sheets)

            # Verify 'Duplicate GUID First Pass' has FT Total headers at row 0 (no summary metrics)
            # and contains PART123 (FP Original) and PART456 (FP Duplicate)
            fp_dup_sheet = sheets["Duplicate GUID First Pass"]
            self.assertEqual(fp_dup_sheet[0][0], "Total Parts")
            self.assertEqual(fp_dup_sheet[0][1], "Part ID")
            self.assertEqual(fp_dup_sheet[0][12], "Duplicate")
            self.assertEqual(len(fp_dup_sheet), 3)  # 1 header + 2 rows (PART123 & PART456)
            self.assertEqual(fp_dup_sheet[1][1], "PART123")
            self.assertEqual(fp_dup_sheet[1][6], "FAIL")
            self.assertEqual(fp_dup_sheet[1][10], "1_2_3")
            self.assertEqual(fp_dup_sheet[1][12], "FP Original")
            self.assertEqual(fp_dup_sheet[2][1], "PART456")
            self.assertEqual(fp_dup_sheet[2][6], "PASS")
            self.assertEqual(fp_dup_sheet[2][10], "1_2_3")
            self.assertEqual(fp_dup_sheet[2][12], "FP Duplicate")

            # Verify 'QC units FAIL in prev FT Step' has QC Total headers at row 0 (no summary metrics)
            # and contains only QC_BAD_FT (FAIL in prev FT) and QC_MISSING (N/A in prev FT), excluding QC_GOOD
            qc_fail_sheet = sheets["QC units FAIL in prev FT Step"]
            self.assertEqual(qc_fail_sheet[0][0], "Total Parts")
            self.assertEqual(qc_fail_sheet[0][1], "Part ID")
            self.assertEqual(qc_fail_sheet[0][12], "Result in previous FT step")
            self.assertEqual(len(qc_fail_sheet), 3)  # 1 header + 2 rows (QC_BAD_FT & QC_MISSING)
            self.assertEqual(qc_fail_sheet[1][1], "QC_BAD_FT")
            self.assertEqual(qc_fail_sheet[1][10], "1_9_9")
            self.assertEqual(qc_fail_sheet[1][12], "FAIL")
            self.assertEqual(qc_fail_sheet[2][1], "QC_MISSING")
            self.assertEqual(qc_fail_sheet[2][10], "1_7_7")
            self.assertEqual(qc_fail_sheet[2][12], "N/A")

            # Verify the worksheet rows match Check 4 business rule results exactly
            self.assertEqual(res2["check4_result"], "FAIL")
            self.assertEqual(res2["qc_failed_or_missing_prev_op_qty"], 2)
            self.assertEqual(res2["qc_failed_or_missing_prev_op_wxy"], ["1_7_7", "1_9_9"])
            sheet_qc_fail_wxys = sorted([qc_fail_sheet[1][10], qc_fail_sheet[2][10]])
            self.assertEqual(sheet_qc_fail_wxys, res2["qc_failed_or_missing_prev_op_wxy"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_29_remarks_row_and_evaluation(self):
        """Verify:
        1) compute_remarks accurately evaluates all 6 branches according to user rules:
           - Branch 1: QC units untested/rejected in previous FT operation
           - Branch 2: Duplicate WXY coordinates in FIRST PASS
           - Branch 3: Both FP and Total Good PASS -> 'No abnormalities or discrepancies were observed.'
           - Branch 4: FP PASS and Total Good FAIL -> 'There is abnormality or discrepancy observed in RETEST process. Please check.'
           - Branch 5: FP FAIL and Total Good PASS -> 'There is abnormality or discrepancy observed in FIRST PASS process. Please check.'
           - Branch 6: FP FAIL and Total Good FAIL -> 'There was an anomaly during lot processing. Please hold lot for review and assessment.'
        2) FTDCCheckerFrame._show_result_popup creates a 'Remarks: ' row displaying the remarks text across FP and Total good.
        """
        import tkinter as tk
        from apps.ftdc_checker.guid_analysis import compute_remarks
        from apps.ftdc_checker.ui import FTDCCheckerFrame

        # Branch 1: Single QC failure
        res_qc_fail = {
            "qc_failed_or_missing_prev_op_qty": 3,
            "fp_datalog_duplicate_count": 0,
            "status": "PASS",
            "total_good_status": "PASS",
        }
        self.assertEqual(
            compute_remarks(res_qc_fail),
            "There are 3 QC units that are either UNTESTED/TESTED REJECT from previous FT operation. Please hold lot for review and assessment."
        )

        # Multiple violations: QC failure + FP duplicate WXY
        res_multi_fail = {
            "qc_failed_or_missing_prev_op_qty": 3,
            "fp_datalog_duplicate_count": 2,
            "status": "PASS",
            "total_good_status": "PASS",
        }
        self.assertEqual(
            compute_remarks(res_multi_fail),
            "There are 3 QC units that are either UNTESTED/TESTED REJECT from previous FT operation. Please hold lot for review and assessment. "
            "Duplicate WXY coordinate(s) observed during FIRST PASS. Please hold lot for review and assessment."
        )

        # Branch 2: FP duplicate WXY takes second priority (regardless of FTDC PASS or FAIL)
        res_fp_dup = {
            "qc_failed_or_missing_prev_op_qty": 0,
            "fp_datalog_duplicate_count": 1,
            "status": "PASS",
            "total_good_status": "PASS",
        }
        self.assertEqual(
            compute_remarks(res_fp_dup),
            "Duplicate WXY coordinate(s) observed during FIRST PASS. Please hold lot for review and assessment."
        )

        # Branch 3: Both PASS
        res_both_pass = {
            "qc_failed_or_missing_prev_op_qty": 0,
            "fp_datalog_duplicate_count": 0,
            "status": "PASS",
            "total_good_status": "PASS",
        }
        self.assertEqual(
            compute_remarks(res_both_pass),
            "No abnormalities or discrepancies were observed."
        )

        # Branch 4: FP PASS, Total FAIL
        res_fp_p_tot_f = {
            "qc_failed_or_missing_prev_op_qty": 0,
            "fp_datalog_duplicate_count": 0,
            "status": "PASS",
            "total_good_status": "FAIL",
        }
        self.assertEqual(
            compute_remarks(res_fp_p_tot_f),
            "There is abnormality or discrepancy observed in RETEST process. Please check."
        )

        # Branch 5: FP FAIL, Total PASS
        res_fp_f_tot_p = {
            "qc_failed_or_missing_prev_op_qty": 0,
            "fp_datalog_duplicate_count": 0,
            "status": "FAIL",
            "total_good_status": "PASS",
        }
        self.assertEqual(
            compute_remarks(res_fp_f_tot_p),
            "There is abnormality or discrepancy observed in FIRST PASS process. Please check."
        )

        # Branch 6: Both FAIL
        res_both_fail = {
            "qc_failed_or_missing_prev_op_qty": 0,
            "fp_datalog_duplicate_count": 0,
            "status": "FAIL",
            "total_good_status": "FAIL",
        }
        self.assertEqual(
            compute_remarks(res_both_fail),
            "There was an anomaly during lot processing. Please hold lot for review and assessment."
        )

        # Verify UI popup window renders the Remarks: row
        ftdc = FTDCCheckerFrame(self.root)
        popup_res = {
            "lot_id": "TEST_LOT",
            "status": "PASS",
            "total_good_status": "PASS",
            "first_pass_pass_count": 100,
            "duplicate_guid": 0,
            "calculated_first_pass_qty": 100,
            "first_pass_actual_good_qty": 100,
            "delta_vs_fp_actual_good": 0,
            "qc_sampling_good_in_prev_op": 10,
            "total_pass_parts": 100,
            "total_duplicate_guid": 0,
            "total_good": 100,
            "total_actual_good_qty": 100,
            "delta_vs_total_actual_good": 0,
            "remarks": "No abnormalities or discrepancies were observed.",
            "first_pass": {"total_parts": 100, "pass_count": 100, "fail_count": 0, "missing_wxy": 0, "rows": []},
            "retest": {"total_parts": 0, "pass_count": 0, "fail_count": 0, "missing_wxy": 0, "rows": []},
            "qc": {"total_parts": 10, "pass_count": 10, "fail_count": 0, "missing_wxy": 0, "rows": []},
        }
        ftdc._show_result_popup(popup_res)
        win = ftdc._result_window
        self.assertIsNotNone(win)
        self.assertTrue(win.winfo_exists())

        # Find the Remarks: label in the popup window
        found_remarks_label = False
        found_remarks_val = False
        for widget in win.winfo_children():
            for child in widget.winfo_children():
                if isinstance(child, tk.Frame):
                    for sub in child.winfo_children():
                        if isinstance(sub, tk.Label):
                            txt = sub.cget("text")
                            if "Remarks:" in txt:
                                found_remarks_label = True
                            if "No abnormalities or discrepancies were observed." in txt:
                                found_remarks_val = True

        self.assertTrue(found_remarks_label, "Remarks: label should be present in the result popup window")
        self.assertTrue(found_remarks_val, "Remarks value should be present in the result popup window")
        win.destroy()

    def test_30_session_log_saved_in_app_logs_folder(self):
        r"""Verify save_session_log_on_close saves logs to <PTS_Hub>\logs\FTDC_Logs_{TIMESTAMP}.log."""
        from apps.ftdc_checker.ui import FTDCCheckerFrame, HUB_ROOT

        ftdc = FTDCCheckerFrame(self.root)
        ftdc.log("Test session log line for PTS Hub logs folder")
        saved_path = ftdc.save_session_log_on_close()
        try:
            self.assertIsNotNone(saved_path)
            expected_logs_dir = os.path.join(HUB_ROOT, "logs")
            self.assertEqual(os.path.dirname(saved_path), expected_logs_dir)
            filename = os.path.basename(saved_path)
            self.assertRegex(filename, r"^FTDC_Logs_\d{8}_\d{6}\.log$")
            self.assertTrue(os.path.isfile(saved_path))
            with open(saved_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Test session log line for PTS Hub logs folder", content)
        finally:
            if saved_path and os.path.isfile(saved_path):
                try:
                    os.remove(saved_path)
                except OSError:
                    pass

    def test_31_check_stdf_optimizations_and_cache(self):
        """Verify all Check STDF optimizations:
        1) O(1) c_end pre-filter & single-pass PRR consumption handle PTR blocks containing
           decoy '\\x05\\x14' bytes (e.g. TEST_NUM=0x1405=5125) identically to full scan.
        2) Single-open STDFReader transparently handles gzip magic bytes on non-.gz files and caches drive type.
        3) _scan_stdf_folder / _signature_from_stat match _file_cache_signature without extra stat calls.
        4) Persistent disk cache & background pre-warming populate _check_summary_cache and survive reload.
        """
        import gzip
        import struct
        import tempfile
        import time
        from apps.ftdc_checker.stdf_parser import parse_check_summary, _DRIVE_REMOTE_CACHE, _is_network_path
        from apps.ftdc_checker.ui import FTDCCheckerFrame

        temp_dir = tempfile.mkdtemp()
        try:
            # Build a >300 KB STDF file with decoy \x05\x14 bytes inside PTR records (TEST_NUM = 0x1405 = 5125)
            header = bytearray(self._rec(0, 10, bytes([2, 4]), "<"))
            mir = bytearray(struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
            mir += b" "
            for s in ("LOT_OPT.000", "DEV1", "NODE1", "V93K", "JOB1"):
                mir += self._cn(s)
            mir += b"\x00" * 25
            header += self._rec(1, 10, bytes(mir), "<")

            pir = self._rec(5, 10, bytes([1, 1]), "<")
            # PTR with TEST_NUM=0x1405 (little-endian \x05\x14\x00\x00) to exercise O(1) false-hit pre-filter
            ptr_decoy = self._rec(
                15, 10,
                struct.pack("<IBBBBf", 0x1405, 1, 1, 0, 0, 1.25)
                + bytes([4]) + b"TEST" + bytes([0, 0x0E, 0, 0, 0])
                + struct.pack("<ff", -10.0, 10.0),
                "<",
            )
            prr_pass = self._rec(
                5, 20,
                struct.pack("<BBBHHHhhI", 1, 1, 0x00, 10, 1, 1, 0, 0, 5) + self._cn("P1") + b"\x00\x00",
                "<",
            )
            one_part = pir + (ptr_decoy * 12) + prr_pass
            total_parts = 600
            body = one_part * total_parts  # > 300 KB so use_prr_jump is active
            tail = bytearray()
            tail += self._rec(1, 50, struct.pack("<BBHI", 255, 0, 1, total_parts) + b"P" + self._cn("GOOD"), "<")
            tail += self._rec(1, 30, struct.pack("<BBIIIII", 255, 0, total_parts, 0, 0, total_parts, 0), "<")
            tail += self._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
            raw_stdf = bytes(header) + body + bytes(tail)
            self.assertGreater(len(raw_stdf), 256 * 1024)

            std_path = os.path.join(temp_dir, "opt_lot_e_20261030100000.std")
            with open(std_path, "wb") as f:
                f.write(raw_stdf)

            # 1. Jump scan vs full scan parity
            res_jump = parse_check_summary(std_path, expected_lot_id="LOT_OPT.000", force_full_scan=False)
            res_full = parse_check_summary(std_path, expected_lot_id="LOT_OPT.000", force_full_scan=True)
            self.assertEqual(res_jump["prr_total"], total_parts)
            self.assertEqual(res_jump["prr_good"], total_parts)
            self.assertEqual(res_jump["prr_total"], res_full["prr_total"])
            self.assertEqual(res_jump["prr_good"], res_full["prr_good"])
            self.assertTrue(res_jump["mrr_is_last"])
            self.assertEqual(res_jump["discrepancy_text"], "None")

            # 2. Single-open gzip magic detection on a non-.gz extension + drive cache
            gz_disguised = os.path.join(temp_dir, "gzipped_without_ext.std")
            with gzip.open(gz_disguised, "wb") as gf:
                gf.write(raw_stdf)
            res_gz = parse_check_summary(gz_disguised, expected_lot_id="LOT_OPT.000")
            self.assertEqual(res_gz["prr_total"], total_parts)
            self.assertFalse(_is_network_path(std_path))
            if os.name == "nt":
                drv = os.path.splitdrive(os.path.abspath(std_path))[0].upper()
                self.assertIn(drv, _DRIVE_REMOTE_CACHE)

            # 3. _scan_stdf_folder signature parity + chronological timestamp table-order sort
            early_path = os.path.join(temp_dir, "z_early_20261030080000.std")
            with open(early_path, "wb") as ef:
                ef.write(raw_stdf)
            ftdc = FTDCCheckerFrame(self.root)
            scanned_list = ftdc._scan_stdf_folder(temp_dir, {".std"})
            scanned_names = [name for name, _ in scanned_list]
            # Even though 'z_early_...' starts with 'z', its 08:00:00 timestamp sorts before 10:00:00,
            # and files without a timestamp ('gzipped_without_ext.std') sort last.
            self.assertEqual(
                scanned_names,
                [
                    "z_early_20261030080000.std",
                    "opt_lot_e_20261030100000.std",
                    "gzipped_without_ext.std",
                ],
            )
            scanned = dict(scanned_list)
            self.assertIn("opt_lot_e_20261030100000.std", scanned)
            self.assertEqual(
                scanned["opt_lot_e_20261030100000.std"],
                ftdc._file_cache_signature(std_path),
            )

            # 4. Pre-warming + persistent disk cache round-trip
            with ftdc._check_cache_lock:
                ftdc._check_summary_cache.clear()
            ftdc.prewarm_check_stdf_cache(temp_dir, "LOT_OPT.000")
            sig_key = (ftdc._file_cache_signature(std_path), "LOT_OPT.000")
            deadline = time.time() + 5.0
            cached_row = None
            while time.time() < deadline:
                with ftdc._check_cache_lock:
                    cached_row = ftdc._check_summary_cache.get(sig_key)
                if cached_row is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(cached_row)
            self.assertEqual(cached_row[0], "Completed")
            self.assertEqual(cached_row[2], str(total_parts))
            self.assertEqual(cached_row[3], str(total_parts))

            # Persist to disk, clear memory, reload from disk, and verify entry is restored
            ftdc._save_check_cache_to_disk()
            with ftdc._check_cache_lock:
                ftdc._check_summary_cache.clear()
            ftdc._load_check_cache_from_disk()
            with ftdc._check_cache_lock:
                restored_row = ftdc._check_summary_cache.get(sig_key)
            self.assertEqual(restored_row, cached_row)
            ftdc.shutdown_background_workers()
            ftdc._clear_check_cache_from_disk()
            ftdc._session_log_saved = True
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_32_valid_stdf_filename_conventions_and_summary_general_without_remarks(self):
        """Get STDF fetches ONLY files matching the per-mask/tester conventions in
        stdf_filename_conventions.json; Summary GENERAL table has no Remarks row."""
        import tempfile
        import shutil
        from unittest.mock import patch
        from apps.ftdc_checker.stdf_fetcher import (
            resolve_stdf_filename_conventions,
            compile_stdf_filename_patterns,
            match_stdf_filename_convention,
            search_stdf_files,
        )

        lot = "MMT-272500536.800"
        v93k = compile_stdf_filename_patterns(lot, resolve_stdf_filename_conventions("77A21QRTBC01", "V93K"))
        ltx = compile_stdf_filename_patterns(lot, resolve_stdf_filename_conventions("58909QRTBC01", "LTX"))
        m = lambda name, pats: match_stdf_filename_convention(name, pats)

        # V93K conventions
        self.assertEqual(m("mmt-272500536.800_e_20261029201620_ft1_2610302255.stdf.old", v93k), "FIRST PASS")
        self.assertEqual(m("DEV_mmt-272500536.800_e_20261029201620_ft1.stdf", v93k), "FIRST PASS")
        self.assertEqual(m("mmt-272500536.800_w_rj1_20261030225712_ft1_2610310237.stdf.old", v93k), "RETEST")
        self.assertEqual(m("DEV_MMT-272500536.800_w_rj12_20261030225712.std", v93k), "RETEST")
        self.assertEqual(m("mmt-272500536.800_q_20261030225712_ft1.stdf", v93k), "QC")
        self.assertEqual(m("mmt-272500536.800_q_rj2_20261030225712_ft1.stdf", v93k), "QC RETEST")
        self.assertIsNone(m("mmt-272500536.800_w_20261030225712_ft1.stdf", v93k))       # sole _w_
        self.assertIsNone(m("mmt-272500536.800.rj1_w_20261030225712.stdf", v93k))       # LTX style
        self.assertIsNone(m("ATA5293_MMT-272500536.800_1__20261004072705.std.bak", v93k)) # LTX style
        self.assertIsNone(m("mmt-272500536.800_e_2026corr_ft1.stdf", v93k))
        self.assertIsNone(m("mmt-272500536.8001_e_20261029201620.stdf", v93k))           # different lot
        self.assertIsNone(m("Xmmt-272500536.800_e_20261029201620.stdf", v93k))           # partial lot boundary

        # LTX conventions (including _1__ and .rjN_1__ with prefix in middle)
        self.assertEqual(m("mmt-272500536.800_e_20261029201620_6140_2608181405.std", ltx), "FIRST PASS")
        self.assertEqual(m("ATA5293_MMT-272500536.800_1__20260704135724.std.bak", ltx), "FIRST PASS")
        self.assertEqual(m("mmt-272500536.800.rj1_w_20261030225712_6140.std", ltx), "RETEST")
        self.assertEqual(m("ATA5293_MMT-272500536.800.RJ1_1__20260705182644.std.bak", ltx), "RETEST")
        self.assertEqual(m("mmt-272500536.800_q_20261030225712.std", ltx), "QC")
        self.assertEqual(m("mmt-272500536.800.rj3_q_20261030225712.std", ltx), "QC RETEST")
        self.assertIsNone(m("mmt-272500536.800_w_rj1_20261030225712.std", ltx))         # V93K style

        # Mask 58975 supports both testers; unknown mask / tester raises
        self.assertIn("RETEST", resolve_stdf_filename_conventions("58975QRTBC01", "LTX"))
        self.assertIn("RETEST", resolve_stdf_filename_conventions("58975QRTBC01", "V93K"))
        with self.assertRaises(ValueError):
            resolve_stdf_filename_conventions("ZZZZZQRTBC01", "V93K")
        with self.assertRaises(ValueError):
            resolve_stdf_filename_conventions("77A21QRTBC01", "LTX")

        # search_stdf_files applies ONLY the conventions (no legacy exclude regex)
        base = tempfile.mkdtemp()
        try:
            dev_dir = os.path.join(base, "DEV1")
            os.makedirs(dev_dir)
            names = [
                "mmt-272500536.800_e_20261029201620_ft1_2610302255.stdf.old",
                "DEV_mmt-272500536.800_w_rj1_20261030225712_ft1_2610310237.stdf.old", # prefix in middle
                "mmt-272500536.800_q_rj1_20261030225712_os_pa.stdf",       # legacy regex would drop this
                "mmt-272500536.800_w_20261030225712_ft1.stdf",            # sole _w_ -> invalid
                "mmt-272500536.800.rj1_w_20261030225712.stdf",            # LTX style -> invalid on V93K
                "otherlot99_mmt-272500536.800_1__20261029201620.stdf",    # LTX style -> invalid on V93K
            ]
            for n in names:
                with open(os.path.join(dev_dir, n), "wb") as fp:
                    fp.write(b"x")
            with patch("apps.ftdc_checker.stdf_fetcher.get_common_paths", return_value=[]):
                found = search_stdf_files(lot, ["DEV1"], network_bases=[base], tester_type="V93K", mpc="77A21QRTBC01")
            self.assertEqual(sorted(os.path.basename(f) for f in found), sorted(names[:3]))
        finally:
            shutil.rmtree(base, ignore_errors=True)

        # Summary worksheet GENERAL table has no Remarks row
        import inspect
        from apps.ftdc_checker import excel_output
        src = inspect.getsource(excel_output.write_result_excel)
        self.assertIn('m != "Remarks"', src)






class TestSMBPushdown(unittest.TestCase):
    def test_33_smb_server_side_filter_pushdown_with_fallback(self):
        """FindFirstFileExW pushdown returns the same matches (with correct size/mtime)
        as os.scandir, gracefully falls back, and search results are identical."""
        import tempfile
        import shutil
        from unittest.mock import patch
        from apps.ftdc_checker import stdf_fetcher as sf

        lot = "MMT-272500536.800"
        base = tempfile.mkdtemp()
        try:
            dev_dir = os.path.join(base, "PIC32CX1012BZ25048")
            os.makedirs(dev_dir)
            os.makedirs(os.path.join(base, "OTHERDEVICE"))
            names = [
                "mmt-272500536.800_e_20261029201620_ft1_2610302255.stdf.old",
                "mmt-272500536.800_w_rj1_20261030225712_ft1_2610310237.stdf.old",
                "MMT-272500536.800_q_20261030225712_ft1.stdf",
                "mmt-272500536.801_e_20261029201620.stdf",   # different lot
                "unrelated_e_20261029201620.stdf",
            ]
            for i, n in enumerate(names):
                with open(os.path.join(dev_dir, n), "wb") as fp:
                    fp.write(b"x" * (100 + i))

            if os.name == "nt":
                entries = sf._pushdown_scandir(dev_dir, f"*{lot}*")
                got = {e.name: e for e in entries}
                self.assertEqual(set(got), set(names[:3]))   # server-side, case-insensitive
                for n, e in got.items():
                    st = os.stat(os.path.join(dev_dir, n))
                    self.assertEqual(e.stat().st_size, st.st_size)
                    self.assertLessEqual(abs(e.stat().st_mtime_ns - st.st_mtime_ns), 1000)
                    self.assertTrue(e.is_file())
                # Directory prefix pushdown
                dirs = sf._pushdown_scandir(base, "PIC32CX1012BZ*", dirs_only=True)
                self.assertEqual([d.name for d in dirs], ["PIC32CX1012BZ25048"])
                # No match -> empty list; missing dir -> FileNotFoundError
                self.assertEqual(sf._pushdown_scandir(dev_dir, "*NOPE_LOT*"), [])
                with self.assertRaises(FileNotFoundError):
                    sf._pushdown_scandir(os.path.join(base, "missing"), "*x*")

            # Fallback path: unavailable pushdown -> full os.scandir listing
            with patch.object(sf, "_pushdown_scandir", side_effect=sf._PushdownUnavailable("test")):
                entries, used = sf.scandir_filtered(dev_dir, f"*{lot}*")
                self.assertFalse(used)
                self.assertEqual(len(entries), len(names))

            # End-to-end: identical search results with pushdown ON and OFF
            def run_search():
                with patch.object(sf, "get_common_paths", return_value=[]):
                    return sorted(sf.search_stdf_files(
                        lot, ["PIC32CX1012BZ*"], network_bases=[base],
                        tester_type="V93K", mpc="SG4054MYXA00",
                    ))
            with_pushdown = run_search()
            with patch.object(sf, "_SMB_PUSHDOWN_ENABLED", False):
                without_pushdown = run_search()
            self.assertEqual(with_pushdown, without_pushdown)
            self.assertEqual(sorted(os.path.basename(p) for p in with_pushdown), sorted(names[:3]))

            # Safety net: pushdown returning nothing for the lot triggers a verifying full scan
            real_pushdown = sf._pushdown_scandir

            def lot_empty(directory, pattern, dirs_only=False):
                if dirs_only:
                    return real_pushdown(directory, pattern, dirs_only=True)
                return []
            if os.name == "nt":
                with patch.object(sf, "_pushdown_scandir", side_effect=lot_empty):
                    self.assertEqual(run_search(), with_pushdown)
        finally:
            shutil.rmtree(base, ignore_errors=True)


    def test_34_add_device_button_and_device_settings_manager(self):
        """Verify 'Device Settings' button beside MPC label, Device Settings Manager dialog UX,
        and atomic save/delete across mpc_partnumber.json, mask_wxy_map.json, and stdf_filename_conventions.json."""
        import json
        import shutil
        import tempfile
        from unittest.mock import patch
        from apps.ftdc_checker import stdf_fetcher as sf
        from apps.ftdc_checker import guid_analysis as ga

        ui_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "apps", "ftdc_checker", "ui.py")
        with open(ui_path, "r", encoding="utf-8") as f:
            ui_src = f.read()

        # Verify button text is "Device Settings" and placed inside mpc_lbl_frame
        self.assertIn('ModernHoverButton(\n            mpc_lbl_frame,\n            text="Device Settings"', ui_src)
        # Verify STDF Convention column removed (5 columns only)
        self.assertIn('cols = ("mpc", "mask", "device", "tester", "wxy")', ui_src)
        # Verify horizontal scrollbar and auto-adjusting stretch=False columns
        self.assertIn('orient="horizontal"', ui_src)
        self.assertIn("stretch=False", ui_src)
        # Verify debounced search via win.after
        self.assertIn("win.after(250, _render_filtered_rows)", ui_src)
        # Verify multi-select note under Tester Platform
        self.assertIn("Note: Multi-select is supported for devices that can be processed on different testers.", ui_src)
        # Verify background pre-warming of Device Settings cache & hidden window
        self.assertIn("_prewarm_device_settings_manager", ui_src)
        self.assertNotIn("_on_mpc_var_changed", ui_src)
        self.assertIn("self._on_mpc_changed()", ui_src)

        tmp_dir = tempfile.mkdtemp()
        try:
            mpc_json = os.path.join(tmp_dir, "mpc_partnumber.json")
            wxy_json = os.path.join(tmp_dir, "mask_wxy_map.json")
            conv_json = os.path.join(tmp_dir, "stdf_filename_conventions.json")
            shutil.copy2(sf._MPC_PARTNUMBER_JSON_PATH, mpc_json)
            shutil.copy2(ga._MASK_WXY_JSON_PATH, wxy_json)
            shutil.copy2(sf._get_json_path(sf._CONVENTIONS_JSON_NAME), conv_json)

            with (
                patch.object(sf, "_MPC_PARTNUMBER_JSON_PATH", mpc_json),
                patch.object(ga, "_MASK_WXY_JSON_PATH", wxy_json),
                patch.object(sf, "_get_json_path", return_value=conv_json),
            ):
                sf.reload_config()
                ga.reload_wxy_config()

                saved = sf.save_device_configuration(
                    mpc="ZZ999QRTBA01",
                    devices="PIC32NEWDEV*",
                    testers=["V93K"],
                    wxy_tests={"wafer": 1001, "x": 1002, "y": 1003},
                )
                self.assertEqual(saved["mpc"], "ZZ999QRTBA01")
                self.assertEqual(saved["mask"], "ZZ999")
                self.assertEqual(saved["device"], "PIC32NEWDEV*")
                self.assertEqual(saved["tester"], "V93K")

                devs, tsts, _ = sf.resolve_mpc_details("ZZ999QRTBA01")
                self.assertEqual(devs, ["PIC32NEWDEV*"])
                self.assertEqual(tsts, ["V93K"])
                self.assertEqual(ga.MPC_WXY_TEST_MAP.get("ZZ999"), {"wafer": 1001, "x": 1002, "y": 1003})
                convs = sf.resolve_stdf_filename_conventions("ZZ999QRTBA01", "V93K")
                self.assertIn("FIRST PASS", convs)

                removed = sf.delete_mpc_configuration("ZZ999QRTBA01")
                self.assertTrue(removed)
                with self.assertRaises(ValueError):
                    sf.resolve_mpc_details("ZZ999QRTBA01")
        finally:
            sf.reload_config()
            ga.reload_wxy_config()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_35_sg406_multi_identifier_analysis_and_excel(self):
        """Verify SG406 duplicate MACID logic detection:
        - Specific BT MACID and ZB MACID uniquely assigned to one UID only.
        - Same MACID assigned to multiple UIDs -> Duplicate MACID counted, triggers FAIL for any occurrence (>0).
        - Same MACID assigned to unique UID across FP and Retest -> NOT duplicate MACID.
        - Result overhauled: UID, BT MACID, and ZB MACID columns removed, standard 2-column comparative layout with Duplicate MACID row.
        """
        import struct
        import tempfile
        import openpyxl
        from apps.ftdc_checker.guid_analysis import analyze_guid_data, compute_remarks
        from apps.ftdc_checker.excel_output import (
            build_summary_and_details, build_details_text, write_result_excel,
        )

        def build_sg406_stdf(lot_id: str, parts_spec: list) -> bytes:
            out = bytearray()
            out += TestSTDFParserAndReliability._rec(0, 10, bytes([2, 4]), "<")
            mir_payload = bytearray(
                struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535)
            )
            mir_payload += ord(" ").to_bytes(1, "little")
            for s in (lot_id, "SG406", "NODE1", "V93K", "JOB1"):
                mir_payload += TestSTDFParserAndReliability._cn(s)
            for _ in range(25):
                mir_payload += b"\x00"
            out += TestSTDFParserAndReliability._rec(1, 10, bytes(mir_payload), "<")
            out += TestSTDFParserAndReliability._rec(
                1, 80, bytes([1, 2, 1, 1]) + TestSTDFParserAndReliability._cn("HAND1"), "<"
            )

            for idx, p in enumerate(parts_spec, start=1):
                out += TestSTDFParserAndReliability._rec(5, 10, bytes([1, 1]), "<")
                tmap = {}
                if "wxy" in p and p["wxy"] is not None:
                    tmap[93005], tmap[93006], tmap[93007] = p["wxy"]
                if "uid" in p and p["uid"] is not None:
                    tmap[93009] = p["uid"]
                if "bt" in p and p["bt"] is not None:
                    for b_i, b_val in enumerate(p["bt"]):
                        tmap[94016 + b_i] = b_val
                if "zb" in p and p["zb"] is not None:
                    for z_i, z_val in enumerate(p["zb"]):
                        tmap[94022 + z_i] = z_val

                for tnum, val in sorted(tmap.items()):
                    if val is None:
                        continue
                    ptr_body = bytearray(struct.pack("<IBBBBf", tnum, 1, 1, 0, 0, float(val)))
                    ptr_body += TestSTDFParserAndReliability._cn(f"T_{tnum}") + b"\x00"
                    ptr_body += bytes([0, 0, 0, 0])
                    ptr_body += struct.pack("<ff", -1e6, 1e6)
                    out += TestSTDFParserAndReliability._rec(15, 10, bytes(ptr_body), "<")

                part_flg = 0x00 if p.get("pass", True) else 0x08
                hb = p.get("hard_bin", 1 if p.get("pass", True) else 5)
                sb = p.get("soft_bin", 1 if p.get("pass", True) else 5)
                prr_body = bytearray(
                    struct.pack("<BBBHHHhhI", 1, 1, part_flg, len(tmap), hb, sb, -32768, -32768, 12)
                )
                prr_body += TestSTDFParserAndReliability._cn(str(p.get("part_id", idx))) + b"\x00\x00"
                out += TestSTDFParserAndReliability._rec(5, 20, bytes(prr_body), "<")

            good_cnt = sum(1 for p in parts_spec if p.get("pass", True))
            pcr_body = struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt, 0)
            out += TestSTDFParserAndReliability._rec(1, 30, pcr_body, "<")
            mrr_body = struct.pack("<I", 1700001000) + b" \x00\x00"
            out += TestSTDFParserAndReliability._rec(1, 20, mrr_body, "<")
            return bytes(out)

        tmp_dir = tempfile.mkdtemp()
        try:
            lot_id = "MMT-260000999.000"
            fp_path = os.path.join(tmp_dir, f"{lot_id}_e_20261105100000.std")
            rt_path = os.path.join(tmp_dir, f"{lot_id}_w_rj1_20261105110000.std")
            qc_path = os.path.join(tmp_dir, f"{lot_id}_q_20261105120000.std")

            # Scenario A: BT MACID collision (same valid BT MACID 64_132_3_4_5_6 assigned to multiple UIDs: 1001 and 1004)
            # - Unit 1 (FP PASS): WXY=(1,10,20), UID=1001, BT=(64,132,3,4,5,6), ZB=(64,132,12,13,14,15,16,17)
            # - Unit 2 (FP PASS): WXY=(1,10,21), UID=1002, BT=(64,132,3,4,5,7), ZB=(64,132,12,13,14,15,16,18)
            # - Unit 3 (FP FAIL): WXY=(1,10,22), UID=1003, BT=None (missing->NaN), ZB=None
            # In Retest:
            # - Unit R1 (PASS): retests Unit 3 with WXY=(1,10,22), UID=1003, BT=(64,132,3,4,5,8), ZB=(64,132,12,13,14,15,16,19)
            # - Unit R2 (PASS): has unique WXY=(1,10,23), UID=1004, unique ZB=(64,132,12,13,14,15,16,20),
            #   BUT duplicates Unit 1's BT=(64,132,3,4,5,6) on a different UID (1004 vs 1001)!
            #   This MUST trigger Duplicate MACID = 1 and automatic FAIL.
            fp_parts = [
                {"part_id": "1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "2", "pass": True, "wxy": (1, 10, 21), "uid": 1002, "bt": (64, 132, 3, 4, 5, 7), "zb": (64, 132, 12, 13, 14, 15, 16, 18)},
                {"part_id": "3", "pass": False, "wxy": (1, 10, 22), "uid": 1003, "bt": None, "zb": None},
            ]
            rt_parts = [
                {"part_id": "R1", "pass": True, "wxy": (1, 10, 22), "uid": 1003, "bt": (64, 132, 3, 4, 5, 8), "zb": (64, 132, 12, 13, 14, 15, 16, 19)},
                {"part_id": "R2", "pass": True, "wxy": (1, 10, 23), "uid": 1004, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 20)},
            ]
            qc_parts = [
                {"part_id": "Q1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "Q2", "pass": True, "wxy": (1, 10, 21), "uid": 1002, "bt": (64, 132, 3, 4, 5, 7), "zb": (64, 132, 12, 13, 14, 15, 16, 18)},
            ]

            for path, spec in ((fp_path, fp_parts), (rt_path, rt_parts), (qc_path, qc_parts)):
                with open(path, "wb") as f:
                    f.write(build_sg406_stdf(lot_id, spec))

            res = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id=lot_id,
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="3",
                max_workers=1,
            )

            self.assertTrue(res.get("is_sg406"))
            # Verify formatting of WXY, UID, BT, ZB and missing NaN values
            fp_rows = res["first_pass"]["rows"]
            self.assertEqual(fp_rows[0]["wxy"], "1_10_20")
            self.assertEqual(fp_rows[0]["uid"], "1001")
            self.assertEqual(fp_rows[0]["bt"], "64_132_3_4_5_6")
            self.assertEqual(fp_rows[0]["zb"], "64_132_12_13_14_15_16_17")
            self.assertEqual(fp_rows[2]["bt"], "NaN_NaN_NaN_NaN_NaN_NaN")
            self.assertEqual(fp_rows[2]["zb"], "NaN_NaN_NaN_NaN_NaN_NaN_NaN_NaN")
            self.assertEqual(res["first_pass"]["missing_bt"], 1)
            self.assertEqual(res["first_pass"]["missing_zb"], 1)

            # Under the SG406 logic:
            # Valid BT MACID '64_132_3_4_5_6' (40:84:03:04:05:06) is assigned to PASS UID 1001 and PASS UID 1004 -> Duplicate BT MACID = 1, ZB MACID = 0
            # Only PASS parts are tracked for duplicate MACIDs; FAIL parts are excluded.
            self.assertEqual(res["duplicate_bt_macid"], 1)
            self.assertEqual(res["duplicate_bt_macid_count"], 1)
            self.assertEqual(res["duplicate_zb_macid"], 0)
            self.assertEqual(res["duplicate_zb_macid_count"], 0)
            self.assertEqual(res["duplicate_macid"], 1)
            self.assertIn("64_132_3_4_5_6", res["duplicate_bt_macids"])
            self.assertEqual(res["duplicate_zb_macids"], {})

            # Duplicate MACID automatically triggers FAIL across FP and Total Good
            self.assertEqual(res["status"], "FAIL")
            self.assertEqual(res["total_good_status"], "FAIL")
            self.assertEqual(res["check1_result"], "FAIL")
            self.assertEqual(res["check2_result"], "PASS")
            self.assertEqual(res["check4_result"], "PASS")
            self.assertEqual(res["fp_good_unique_discrepancy"], 0)
            self.assertEqual(res["total_good_unique_discrepancy"], 1)
            self.assertEqual(
                res["remarks"],
                "Duplicate BT MACID (1) was observed. Please hold lot and inform Product Owner for review and assessment. "
                "Discrepancy was observed in Good Unique Count of (WXY vs BT MACID, UID vs BT MACID, BT MACID vs ZB MACID). Please hold and inform Product Owner for review and assessment.",
            )

            # Verify Summary & Details
            summary_sections, detail_rows, fp_st, tot_st, _, ftdc_crit = build_summary_and_details(res)
            self.assertEqual(summary_sections[0][0], "FIRST PASS")
            self.assertEqual(summary_sections[1][0], "TOTAL (FP + RETEST)")
            fp_sec_metrics = [m for m, _ in summary_sections[0][1]]
            tot_sec_metrics = [m for m, _ in summary_sections[1][1]]
            fp_sec_dict = dict(summary_sections[0][1])
            tot_sec_dict = dict(summary_sections[1][1])
            self.assertIn("Duplicate MACID (BT / ZB)", fp_sec_metrics)
            self.assertIn("Unique Valid Good (WXY / UID / BT / ZB)", fp_sec_metrics)
            self.assertIn("Duplicate MACID (BT / ZB)", tot_sec_metrics)
            self.assertIn("Unique Valid Good (WXY / UID / BT / ZB)", tot_sec_metrics)
            self.assertEqual(fp_sec_dict["Duplicate MACID (BT / ZB)"], "1 / 0")
            self.assertEqual(fp_sec_dict["Unique Valid Good (WXY / UID / BT / ZB)"], "2 / 2 / 2 / 2")
            self.assertEqual(tot_sec_dict["Duplicate MACID (BT / ZB)"], "1 / 0")
            self.assertEqual(tot_sec_dict["Unique Valid Good (WXY / UID / BT / ZB)"], "4 / 4 / 3 / 4")
            self.assertIn("Duplicate Good UID", fp_sec_metrics)
            self.assertIn("Duplicate Good UID", tot_sec_metrics)

            # Verify Duplicate MACID (BT / ZB) and Unique Valid Good (WXY / UID / BT / ZB) are placed under the Total QC row
            qc_fp_idx = fp_sec_metrics.index("Total QC Units Good At Prev. Operation")
            self.assertEqual(fp_sec_metrics.index("Duplicate MACID (BT / ZB)"), qc_fp_idx + 1)
            self.assertEqual(fp_sec_metrics.index("Unique Valid Good (WXY / UID / BT / ZB)"), qc_fp_idx + 2)
            self.assertEqual(fp_sec_metrics.index("Result"), qc_fp_idx + 3)

            qc_tot_idx = tot_sec_metrics.index("Total QC Tested Good units")
            self.assertEqual(tot_sec_metrics.index("Duplicate MACID (BT / ZB)"), qc_tot_idx + 1)
            self.assertEqual(tot_sec_metrics.index("Unique Valid Good (WXY / UID / BT / ZB)"), qc_tot_idx + 2)
            self.assertEqual(tot_sec_metrics.index("Result"), qc_tot_idx + 3)

            details_txt = build_details_text(detail_rows, ftdc_criteria=ftdc_crit)
            self.assertIn("Duplicate BT MACID", details_txt)
            self.assertIn("Duplicate ZB MACID", details_txt)

            # Verify Decimal to Hex MACID conversion helper
            from apps.ftdc_checker.sg406_analysis import macid_decimal_to_hex
            self.assertEqual(
                macid_decimal_to_hex("64_132_50_12_192_114"),
                "40:84:32:0C:C0:72",
            )
            self.assertEqual(
                macid_decimal_to_hex("64_132_50_0_0_12_194_204"),
                "40:84:32:00:00:0C:C2:CC",
            )

            xlsx_path = os.path.join(tmp_dir, "sg406_result.xlsx")
            write_result_excel(res, xlsx_path)
            self.assertTrue(os.path.isfile(xlsx_path))
            self.assertGreater(os.path.getsize(xlsx_path), 1000)

            # Verify Excel columns: BT MACID HEX beside BT MACID, ZB MACID HEX beside ZB MACID
            import openpyxl
            wb_check = openpyxl.load_workbook(xlsx_path)
            self.assertIn("FT Total", wb_check.sheetnames)
            ws_ft = wb_check["FT Total"]
            ft_hdr_vals = []
            hdr_r_num = 0
            for r in range(1, 15):
                row_vals = [ws_ft.cell(r, c).value for c in range(1, 25)]
                if "UID" in row_vals and "BT MACID" in row_vals:
                    ft_hdr_vals = row_vals
                    hdr_r_num = r
                    break
            self.assertIn("BT MACID HEX", ft_hdr_vals)
            self.assertIn("ZB MACID HEX", ft_hdr_vals)
            bt_idx = ft_hdr_vals.index("BT MACID")
            bt_hex_idx = ft_hdr_vals.index("BT MACID HEX")
            zb_idx = ft_hdr_vals.index("ZB MACID")
            zb_hex_idx = ft_hdr_vals.index("ZB MACID HEX")
            self.assertEqual(bt_hex_idx, bt_idx + 1, "BT MACID HEX must be right beside BT MACID")
            self.assertEqual(zb_hex_idx, zb_idx + 1, "ZB MACID HEX must be right beside ZB MACID")
            # Verify first data row converted hex value (64_132_3_4_5_6 -> 40:84:03:04:05:06)
            first_bt_hex = ws_ft.cell(hdr_r_num + 1, bt_hex_idx + 1).value
            self.assertEqual(first_bt_hex, "40:84:03:04:05:06")

            # Verify 'Duplicate MACID Details' and 'Valid Good Count Cross-Check' worksheets exist
            self.assertIn("Duplicate MACID Details", wb_check.sheetnames)
            self.assertIn("Valid Good Count Cross-Check", wb_check.sheetnames)
            ws_dup_mac = wb_check["Duplicate MACID Details"]
            dup_mac_hdrs = [ws_dup_mac.cell(1, c).value for c in range(1, 5)]
            self.assertEqual(
                dup_mac_hdrs,
                [
                    "BT MACID HEX",
                    "ZB MACID HEX",
                    "Number of UIDs per BT & ZB MACID pair",
                    "Assigned UIDS per BT & ZB MACID pair",
                ],
            )
            ws_gu_check = wb_check["Valid Good Count Cross-Check"]
            self.assertEqual(
                [ws_gu_check.cell(1, c).value for c in range(1, 4)],
                ["Parameter / Check Metric", "FIRST PASS", "TOTAL (FP + RETEST)"],
            )

            # Verify unique (BT MACID, ZB MACID) pair grouping in 'Duplicate MACID Details':
            # Same BT MACID (40:84:32:04:86:70) paired with two different ZB MACIDs (...:4B and ...:4C),
            # each having different multiple assigned UIDs -> must produce 2 separate rows!
            from apps.ftdc_checker.sg406_analysis import compute_duplicate_macid_pairs
            pair_test_rows = [
                {"uid": "288022", "bt": "64_132_50_4_134_112", "zb": "64_132_50_0_0_4_134_75"},
                {"uid": "288044", "bt": "64_132_50_4_134_112", "zb": "64_132_50_0_0_4_134_75"},
                {"uid": "288055", "bt": "64_132_50_4_134_112", "zb": "64_132_50_0_0_4_134_76"},
                {"uid": "288066", "bt": "64_132_50_4_134_112", "zb": "64_132_50_0_0_4_134_76"},
            ]
            pair_details = compute_duplicate_macid_pairs(pair_test_rows)
            self.assertEqual(len(pair_details), 2)
            self.assertEqual(pair_details[0]["bt_hex"], "40:84:32:04:86:70")
            self.assertEqual(pair_details[0]["zb_hex"], "40:84:32:00:00:04:86:4B")
            self.assertEqual(pair_details[0]["num_assigned_uids"], 2)
            self.assertEqual(pair_details[0]["assigned_uids_str"], "288022, 288044")
            self.assertEqual(pair_details[1]["bt_hex"], "40:84:32:04:86:70")
            self.assertEqual(pair_details[1]["zb_hex"], "40:84:32:00:00:04:86:4C")
            self.assertEqual(pair_details[1]["num_assigned_uids"], 2)
            self.assertEqual(pair_details[1]["assigned_uids_str"], "288055, 288066")

            # Scenario A2: Valid repeat test of the SAME chip (same UID 1001) across FP and Retest
            # MACID 64_132_3_4_5_6 is assigned to ONLY UID 1001 -> NOT a duplicate MACID!
            rt_parts_same_uid = [
                {"part_id": "R1", "pass": True, "wxy": (1, 10, 22), "uid": 1003, "bt": (64, 132, 3, 4, 5, 8), "zb": (64, 132, 12, 13, 14, 15, 16, 19)},
                {"part_id": "R2", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
            ]
            with open(rt_path, "wb") as f:
                f.write(build_sg406_stdf(lot_id, rt_parts_same_uid))

            res_same_uid = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id=lot_id,
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="3",
                max_workers=1,
            )
            self.assertEqual(res_same_uid["duplicate_macid"], 0)
            self.assertEqual(res_same_uid["status"], "PASS")
            self.assertEqual(res_same_uid["total_good_status"], "PASS")
            self.assertEqual(res_same_uid["check1_result"], "PASS")
            self.assertEqual(res_same_uid["remarks"], "No abnormalities or discrepancies were observed.")

            # Scenario B: Internal duplicate in FIRST PASS for ZB MACID across multiple UIDs -> FAIL & Remarks
            fp_parts_dup_zb = [
                {"part_id": "1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "2", "pass": True, "wxy": (1, 10, 21), "uid": 1002, "bt": (64, 132, 3, 4, 5, 7), "zb": (64, 132, 12, 13, 14, 15, 16, 18)},
                {"part_id": "3", "pass": True, "wxy": (1, 10, 24), "uid": 1005, "bt": (64, 132, 3, 4, 5, 9), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
            ]
            with open(fp_path, "wb") as f:
                f.write(build_sg406_stdf(lot_id, fp_parts_dup_zb))

            res_dup = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id=lot_id,
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="2",
                max_workers=1,
            )
            self.assertEqual(res_dup["status"], "FAIL")
            self.assertEqual(res_dup["total_good_status"], "FAIL")
            self.assertEqual(res_dup["check1_result"], "FAIL")
            self.assertEqual(res_dup["check4_result"], "PASS")
            self.assertEqual(res_dup["duplicate_bt_macid"], 0)
            self.assertEqual(res_dup["duplicate_zb_macid"], 1)
            self.assertEqual(res_dup["duplicate_macid"], 1)
            self.assertEqual(res_dup["fp_good_unique_discrepancy"], 1)
            self.assertEqual(res_dup["total_good_unique_discrepancy"], 1)
            self.assertEqual(
                compute_remarks(res_dup),
                "Duplicate ZB MACID (1) was observed. Please hold lot and inform Product Owner for review and assessment. "
                "Discrepancy was observed in Good Unique Count of (WXY vs ZB MACID, UID vs ZB MACID, BT MACID vs ZB MACID). Please hold and inform Product Owner for review and assessment.",
            )

            # Scenario C: Check 4 failure when a QC unit was not tested/passed in FT
            fp_parts_qc_fail = [
                {"part_id": "1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "2", "pass": True, "wxy": (1, 10, 99), "uid": 1002, "bt": (64, 132, 3, 4, 5, 7), "zb": (64, 132, 12, 13, 14, 15, 16, 18)},
            ]
            with open(fp_path, "wb") as f:
                f.write(build_sg406_stdf(lot_id, fp_parts_qc_fail))

            res_qc_fail = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id=lot_id,
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="2",
                max_workers=1,
            )
            self.assertEqual(res_qc_fail["check4_result"], "FAIL")
            self.assertEqual(
                compute_remarks(res_qc_fail),
                "There are 1 QC units that are either UNTESTED/TESTED REJECT from previous FT operation. Please hold lot for review and assessment.",
            )

            # Scenario D: Only PASS units are counted as Duplicate MACID (FAIL units are excluded)
            # 1) Unit 1 (PASS, UID 1001) + Unit 2 (PASS, UID 1002) have distinct MACIDs, while FAIL units
            #    (UID 1006, UID 1007) share Unit 1's valid BT & ZB MACIDs -> MUST NOT trigger Duplicate MACID!
            fp_parts_fail_no_dup = [
                {"part_id": "1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "2", "pass": True, "wxy": (1, 10, 21), "uid": 1002, "bt": (64, 132, 3, 4, 5, 7), "zb": (64, 132, 12, 13, 14, 15, 16, 18)},
                {"part_id": "3", "pass": False, "wxy": (1, 10, 25), "uid": 1006, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "4", "pass": False, "wxy": (1, 10, 26), "uid": 1007, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
            ]
            qc_parts_fail_dup = [
                {"part_id": "Q1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
            ]
            with open(fp_path, "wb") as f:
                f.write(build_sg406_stdf(lot_id, fp_parts_fail_no_dup))
            with open(qc_path, "wb") as f:
                f.write(build_sg406_stdf(lot_id, qc_parts_fail_dup))

            res_fail_no_dup = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id=lot_id,
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="2",
                max_workers=1,
            )
            self.assertEqual(res_fail_no_dup["duplicate_bt_macid"], 0)
            self.assertEqual(res_fail_no_dup["duplicate_zb_macid"], 0)
            self.assertEqual(res_fail_no_dup["duplicate_macid"], 0)
            self.assertEqual(res_fail_no_dup["status"], "PASS")
            self.assertEqual(res_fail_no_dup["total_good_status"], "PASS")

            # 2) Two PASS units (UID 1001, UID 1002) sharing BT MACID + a 3rd FAIL unit (UID 1006) sharing it
            #    -> Only the 2 PASS UIDs are tracked/flagged; FAIL unit is excluded in Excel & Details!
            fp_parts_mixed_dup = [
                {"part_id": "1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "2", "pass": True, "wxy": (1, 10, 21), "uid": 1002, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 18)},
                {"part_id": "3", "pass": False, "wxy": (1, 10, 25), "uid": 1006, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 21)},
            ]
            with open(fp_path, "wb") as f:
                f.write(build_sg406_stdf(lot_id, fp_parts_mixed_dup))

            res_mixed_dup = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id=lot_id,
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="2",
                max_workers=1,
            )
            self.assertEqual(res_mixed_dup["duplicate_bt_macid"], 1)
            self.assertEqual(res_mixed_dup["duplicate_zb_macid"], 0)
            self.assertEqual(res_mixed_dup["status"], "FAIL")
            self.assertIn("Duplicate BT MACID (1) was observed", res_mixed_dup["remarks"])
            self.assertEqual(res_mixed_dup["duplicate_bt_macids"]["64_132_3_4_5_6"], ["1001", "1002"])

            mixed_xlsx = os.path.join(tmp_dir, "sg406_mixed_dup.xlsx")
            write_result_excel(res_mixed_dup, mixed_xlsx)
            wb_mixed = openpyxl.load_workbook(mixed_xlsx)
            ws_ft_m = wb_mixed["FT Total"]
            m_hdr_row = next(r for r in range(1, 20) if ws_ft_m.cell(r, 1).value == "Total Parts")
            m_hdrs = [ws_ft_m.cell(m_hdr_row, c).value for c in range(1, ws_ft_m.max_column + 1)]
            uid_col = m_hdrs.index("UID") + 1
            pf_col = m_hdrs.index("Pass/Fail") + 1
            flag_col = m_hdrs.index("Duplicate MACID Flag") + 1
            panel_col = m_hdrs.index("Datalog") + 1
            fp_flags_by_uid = {}
            for r_i in range(m_hdr_row + 1, ws_ft_m.max_row + 1):
                if ws_ft_m.cell(r_i, panel_col).value == "FIRST PASS":
                    fp_flags_by_uid[(str(ws_ft_m.cell(r_i, uid_col).value), str(ws_ft_m.cell(r_i, pf_col).value))] = ws_ft_m.cell(r_i, flag_col).value
            self.assertEqual(fp_flags_by_uid[("1001", "PASS")], "Duplicate BT MACID")
            self.assertEqual(fp_flags_by_uid[("1002", "PASS")], "Duplicate BT MACID")
            self.assertEqual(fp_flags_by_uid[("1006", "FAIL")], "No Duplicate")

            ws_dup_m = wb_mixed["Duplicate MACID Details"]
            dup_m_hdrs = [ws_dup_m.cell(1, c).value for c in range(1, ws_dup_m.max_column + 1)]
            uids_col = dup_m_hdrs.index("Assigned UIDS per BT & ZB MACID pair") + 1
            detail_uid_tokens = {
                tok.strip()
                for r_i in range(2, ws_dup_m.max_row + 1)
                for tok in str(ws_dup_m.cell(r_i, uids_col).value or "").split(",")
                if tok.strip()
            }
            self.assertEqual(detail_uid_tokens, {"1001", "1002"})

            # Scenario D2: Invalid MACIDs (not starting with 64_132 / 40:84, such as 00:00:00:00:00:00 and 11:22:33:44:55:66)
            # on FAIL rows MUST NOT be counted as duplicate MACIDs even when shared across multiple different UIDs!
            fp_parts_invalid_macid = [
                {"part_id": "1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "2", "pass": False, "wxy": (1, 10, 30), "uid": 2001, "bt": (0, 0, 0, 0, 0, 0), "zb": (0, 0, 0, 0, 0, 0, 0, 0)},
                {"part_id": "3", "pass": False, "wxy": (1, 10, 31), "uid": 2002, "bt": (0, 0, 0, 0, 0, 0), "zb": (0, 0, 0, 0, 0, 0, 0, 0)},
                {"part_id": "4", "pass": False, "wxy": (1, 10, 32), "uid": 2003, "bt": (17, 34, 51, 68, 85, 102), "zb": (17, 34, 51, 68, 85, 102, 119, 136)},
                {"part_id": "5", "pass": False, "wxy": (1, 10, 33), "uid": 2004, "bt": (17, 34, 51, 68, 85, 102), "zb": (17, 34, 51, 68, 85, 102, 119, 136)},
                {"part_id": "6", "pass": True, "wxy": (1, 10, 21), "uid": 1002, "bt": (64, 132, 3, 4, 5, 7), "zb": (64, 132, 12, 13, 14, 15, 16, 18)},
            ]
            with open(fp_path, "wb") as f:
                f.write(build_sg406_stdf(lot_id, fp_parts_invalid_macid))
            res_inv_macid = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id=lot_id,
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="2",
                max_workers=1,
            )
            self.assertEqual(res_inv_macid["duplicate_bt_macid"], 0)
            self.assertEqual(res_inv_macid["duplicate_zb_macid"], 0)
            self.assertEqual(res_inv_macid["duplicate_macid"], 0)
            self.assertEqual(res_inv_macid["fp_good_unique_discrepancy"], 0)
            self.assertEqual(res_inv_macid["total_good_unique_discrepancy"], 0)
            self.assertEqual(res_inv_macid["status"], "PASS")
            self.assertEqual(res_inv_macid["total_good_status"], "PASS")

            # Scenario D3: A GOOD (PASS) unit has an invalid/unprogrammed BT MACID (11:22:33:44:55:66)
            # Duplicate BT/ZB MACID is still 0, but Good Unique Count of WXY (2) vs UID (2) vs BT MACID (1) vs ZB MACID (2)
            # has discrepancy == 1, which MUST trigger FAIL and produce the Good Unique Count discrepancy remark!
            fp_parts_good_uniq_disc = [
                {"part_id": "1", "pass": True, "wxy": (1, 10, 20), "uid": 1001, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
                {"part_id": "2", "pass": True, "wxy": (1, 10, 33), "uid": 2004, "bt": (17, 34, 51, 68, 85, 102), "zb": (64, 132, 12, 13, 14, 15, 16, 25)},
            ]
            with open(fp_path, "wb") as f:
                f.write(build_sg406_stdf(lot_id, fp_parts_good_uniq_disc))
            res_good_disc = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id=lot_id,
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="2",
                max_workers=1,
            )
            self.assertEqual(res_good_disc["duplicate_bt_macid"], 0)
            self.assertEqual(res_good_disc["duplicate_zb_macid"], 0)
            self.assertEqual(res_good_disc["fp_good_unique_discrepancy"], 1)
            self.assertEqual(res_good_disc["total_good_unique_discrepancy"], 1)
            self.assertEqual(res_good_disc["status"], "FAIL")
            self.assertEqual(res_good_disc["total_good_status"], "FAIL")
            self.assertEqual(res_good_disc["check1_result"], "FAIL")
            self.assertEqual(
                res_good_disc["remarks"],
                "Discrepancy was observed in Good Unique Count of (WXY vs BT MACID, UID vs BT MACID, BT MACID vs ZB MACID). Please hold and inform Product Owner for review and assessment.",
            )

            # Scenario E: Remarks logic for SG406
            from apps.ftdc_checker.sg406_analysis import compute_sg406_remarks
            # Duplicate MACID remarks (BT only, ZB only, both)
            res_dup_bt = {"is_sg406": True, "duplicate_bt_macid": 1, "duplicate_zb_macid": 0, "status": "FAIL", "total_good_status": "FAIL"}
            self.assertEqual(
                compute_sg406_remarks(res_dup_bt),
                "Duplicate BT MACID (1) was observed. Please hold lot and inform Product Owner for review and assessment.",
            )
            self.assertEqual(
                compute_remarks(res_dup_bt),
                "Duplicate BT MACID (1) was observed. Please hold lot and inform Product Owner for review and assessment.",
            )

            res_dup_zb = {"is_sg406": True, "duplicate_bt_macid": 0, "duplicate_zb_macid": 2, "status": "FAIL", "total_good_status": "FAIL"}
            self.assertEqual(
                compute_sg406_remarks(res_dup_zb),
                "Duplicate ZB MACID (2) was observed. Please hold lot and inform Product Owner for review and assessment.",
            )

            res_dup_both = {"is_sg406": True, "duplicate_bt_macid": 1, "duplicate_zb_macid": 2, "status": "FAIL", "total_good_status": "FAIL"}
            self.assertEqual(
                compute_sg406_remarks(res_dup_both),
                "Duplicate BT MACID (1) & Duplicate ZB MACID (2) were observed. Please hold lot and inform Product Owner for review and assessment.",
            )

            # Duplicate WXY in FIRST PASS datalog
            res_dup_wxy = {"is_sg406": True, "fp_datalog_duplicate_count": 1, "status": "FAIL", "total_good_status": "FAIL"}
            self.assertEqual(
                compute_sg406_remarks(res_dup_wxy),
                "Duplicate WXY coordinate(s) observed during FIRST PASS. Please hold lot for review and assessment.",
            )

            # First Pass abnormality
            res_fp_fail = {"is_sg406": True, "status": "FAIL", "total_good_status": "PASS", "check4_result": "PASS"}
            self.assertEqual(
                compute_sg406_remarks(res_fp_fail),
                "There is abnormality or discrepancy observed in FIRST PASS process. Please check.",
            )

            # Retest abnormality
            res_rt_fail = {"is_sg406": True, "status": "PASS", "total_good_status": "FAIL", "check4_result": "PASS"}
            self.assertEqual(
                compute_sg406_remarks(res_rt_fail),
                "There is abnormality or discrepancy observed in RETEST process. Please check.",
            )

            # Multiple violations in SG406: Check 4 QC fail + Duplicate WXY + Duplicate BT/ZB MACID
            res_sg406_multi = {
                "is_sg406": True,
                "check4_result": "FAIL",
                "qc_failed_or_missing_prev_op_qty": 2,
                "fp_datalog_duplicate_count": 1,
                "duplicate_bt_macid": 3,
                "duplicate_zb_macid": 4,
                "status": "FAIL",
                "total_good_status": "FAIL",
            }
            self.assertEqual(
                compute_sg406_remarks(res_sg406_multi),
                "There are 2 QC units that are either UNTESTED/TESTED REJECT from previous FT operation. Please hold lot for review and assessment. "
                "Duplicate WXY coordinate(s) observed during FIRST PASS. Please hold lot for review and assessment. "
                "Duplicate BT MACID (3) & Duplicate ZB MACID (4) were observed. Please hold lot and inform Product Owner for review and assessment.",
            )

            # Scenario E: Verify dynamic test config resolution from JSON and Device Settings save
            from apps.ftdc_checker.sg406_analysis import resolve_sg406_test_config
            from apps.ftdc_checker.stdf_fetcher import save_device_configuration
            from apps.ftdc_checker.guid_analysis import MPC_WXY_TEST_MAP

            cfg_from_json = resolve_sg406_test_config()
            self.assertEqual(cfg_from_json["uid"], 93009)
            self.assertEqual(list(cfg_from_json["bt"]), [94016, 94017, 94018, 94019, 94020, 94021])
            self.assertEqual(list(cfg_from_json["zb"]), [94022, 94023, 94024, 94025, 94026, 94027, 94028, 94029])

            # Verify save_device_configuration updates mask_wxy_map.json with custom multi-parameters
            saved_dev = save_device_configuration(
                mpc="SG406TEST001",
                devices="PIC32CX5109BZ*",
                testers=["V93K"],
                wxy_tests={
                    "wafer": 93005,
                    "x": 93006,
                    "y": 93007,
                    "uid": 93009,
                    "bt": [94016, 94017, 94018, 94019, 94020, 94021],
                    "zb": [94022, 94023, 94024, 94025, 94026, 94027, 94028, 94029],
                },
            )
            self.assertIn("uid", saved_dev["wxy"])
            self.assertEqual(saved_dev["wxy"]["uid"], 93009)
            self.assertEqual(len(saved_dev["wxy"]["bt"]), 6)
            self.assertEqual(len(saved_dev["wxy"]["zb"]), 8)
            self.assertIn("SG406", MPC_WXY_TEST_MAP)
            self.assertEqual(MPC_WXY_TEST_MAP["SG406"]["uid"], 93009)
        finally:
            from apps.ftdc_checker.stdf_fetcher import delete_mpc_configuration
            try:
                delete_mpc_configuration("SG406TEST001")
            except Exception:
                pass
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_36_sg406_and_standard_result_popup_rendering(self):
        """Verify Result Popup UI overhaul for SG406 horizontal matrix and standard 2-column."""
        import tkinter as tk
        from apps.ftdc_checker.ui import FTDCCheckerFrame

        root = tk.Tk()
        root.withdraw()
        try:
            hub_frame = FTDCCheckerFrame(root, hub=None)

            # 1. Test SG406 Result Popup (standard 2-column layout with Duplicate MACID row)
            sg406_res = {
                "is_sg406": True,
                "lot_id": "LOT-SG406-001",
                "mir_lot_id": "LOT-SG406-001",
                "mpc": "SG4064RTBA10",
                "status": "PASS",
                "total_good_status": "PASS",
                "first_pass_pass_count": 1000,
                "first_pass_actual_good_qty": 998,
                "total_pass_parts": 1000,
                "total_actual_good_qty": 998,
                "duplicate_guid": 0,
                "duplicate_macid": 0,
                "calculated_first_pass_qty": 1000,
                "delta_vs_fp_actual_good": 2,
                "total_duplicate_guid": 0,
                "total_good": 1000,
                "delta_vs_total_actual_good": 2,
                "qc_sampling_good_in_prev_op": 50,
                "check1_result": "PASS",
                "check2_result": "PASS",
                "check4_result": "PASS",
                "remarks": "No abnormalities or discrepancies were observed.",
                "first_pass": {"total_parts": 1000, "pass_count": 1000, "fail_count": 0, "rows": []},
                "retest": {"total_parts": 0, "pass_count": 0, "fail_count": 0, "rows": []},
                "qc": {"total_parts": 50, "pass_count": 50, "fail_count": 0, "rows": []},
            }

            hub_frame._show_result_popup(sg406_res)
            win = hub_frame._result_window
            self.assertIsNotNone(win)
            self.assertTrue(win.winfo_exists())
            win.update_idletasks()

            # Find all label texts and widgets in popup window
            def _gather_labels(w):
                texts = []
                for child in w.winfo_children():
                    if isinstance(child, tk.Label):
                        texts.append(child.cget("text"))
                    texts.extend(_gather_labels(child))
                return texts

            def _gather_label_widgets(w):
                widgets = []
                for child in w.winfo_children():
                    if isinstance(child, tk.Label):
                        widgets.append(child)
                    widgets.extend(_gather_label_widgets(child))
                return widgets

            labels = _gather_labels(win)
            # Verify SG406 classic key-value layout elements (Option 2 slash format)
            self.assertIn("Automatic FTDC Checker v1.3", labels)
            self.assertIn("FIRST PASS", labels)
            self.assertIn("TOTAL (FP + RETEST)", labels)
            self.assertIn("First Pass STDF Good:", labels)
            self.assertIn("Duplicate Good UID:", labels)
            self.assertIn("Duplicate MACID (BT / ZB):", labels)
            self.assertIn("Unique Valid Good (WXY / UID / BT / ZB):", labels)
            self.assertIn("0 / 0", labels)
            self.assertIn("1,000 / 1,000 / 1,000 / 1,000", labels)
            self.assertIn("First Pass Good w/o Duplicate UID:", labels)
            self.assertIn("First Pass Actual Good:", labels)
            self.assertIn("Difference between Good w/o Duplicate vs FP Actual Good:", labels)
            self.assertIn("STDF Total Good:", labels)
            self.assertIn("Total Good w/o Duplicate UID:", labels)
            self.assertIn("Actual Total Good:", labels)
            self.assertIn("Difference between Good w/o Duplicate vs Actual Total Good:", labels)
            self.assertIn("No abnormalities or discrepancies were observed.", labels)

            # Verify that Duplicate MACID (BT / ZB) and Unique Valid Good (WXY / UID / BT / ZB) are under the Total QC row
            idx_qc = labels.index("Total QC Units Good At Prev. Operation:")
            idx_mac = labels.index("Duplicate MACID (BT / ZB):")
            idx_gu = labels.index("Unique Valid Good (WXY / UID / BT / ZB):")
            self.assertTrue(idx_qc < idx_mac < idx_gu, "Duplicate MACID and Unique Valid Good must be placed under Total QC row")

            # Verify green highlight (fg #008000) and right column auto-adjusted minsize for all counts
            lws = _gather_label_widgets(win)
            green_mac = [lw for lw in lws if lw.cget("text") == "0 / 0" and lw.cget("fg") == "#008000"]
            green_gu = [lw for lw in lws if lw.cget("text") == "1,000 / 1,000 / 1,000 / 1,000" and lw.cget("fg") == "#008000"]
            self.assertEqual(len(green_mac), 2, "Expected green '0 / 0' labels across FP and Total")
            self.assertEqual(len(green_gu), 2, "Expected green '1,000 / 1,000 / 1,000 / 1,000' labels across FP and Total")
            # Verify right column minsize >= width of '1,000 / 1,000 / 1,000 / 1,000' label
            gu_tbl = green_gu[0].master
            self.assertGreaterEqual(int(gu_tbl.grid_columnconfigure(1)["minsize"]), green_gu[0].winfo_reqwidth())
            win.destroy()

            # 1b. Test SG406 popup with Duplicate MACID failure -> Hold lot remark and Red highlight
            sg406_res_neg = dict(sg406_res)
            sg406_res_neg["status"] = "FAIL"
            sg406_res_neg["total_good_status"] = "FAIL"
            sg406_res_neg["duplicate_bt_macid"] = 1
            sg406_res_neg["duplicate_zb_macid"] = 0
            sg406_res_neg["fp_good_unique_discrepancy"] = 1
            sg406_res_neg["total_good_unique_discrepancy"] = 1
            sg406_res_neg["fp_good_unique_counts"] = {"WXY": 1000, "UID": 1000, "BT MACID": 999, "ZB MACID": 1000}
            sg406_res_neg["total_good_unique_counts"] = {"WXY": 1000, "UID": 1000, "BT MACID": 999, "ZB MACID": 1000}
            sg406_res_neg["remarks"] = None  # Force compute_remarks to evaluate
            hub_frame._show_result_popup(sg406_res_neg)
            win_neg = hub_frame._result_window
            self.assertTrue(win_neg.winfo_exists())
            win_neg.update_idletasks()
            labels_neg = _gather_labels(win_neg)
            self.assertTrue(any("Duplicate BT MACID (1) was observed." in lbl for lbl in labels_neg))

            # Verify red highlight (fg #C00000) when count > 0 / discrepancy > 0
            lws_neg = _gather_label_widgets(win_neg)
            red_mac = [lw for lw in lws_neg if lw.cget("text") == "1 / 0" and lw.cget("fg") == "#C00000"]
            red_gu = [lw for lw in lws_neg if lw.cget("text") == "1,000 / 1,000 / 999 / 1,000" and lw.cget("fg") == "#C00000"]
            self.assertEqual(len(red_mac), 2, "Expected red '1 / 0' labels across FP and Total")
            self.assertEqual(len(red_gu), 2, "Expected red Unique Valid Good labels when discrepancy > 0")
            win_neg.destroy()

            # 2. Test standard non-SG406 Result Popup
            std_res = {
                "lot_id": "LOT-STD-001",
                "mpc": "STD123456789",
                "status": "PASS",
                "total_good_status": "SKIPPED",
                "first_pass_pass_count": 500,
                "duplicate_guid": 0,
                "calculated_first_pass_qty": 500,
                "first_pass_actual_good_qty": 500,
                "delta_vs_fp_actual_good": 0,
                "qc_sampling_good_in_prev_op": 20,
                "total_pass_parts": 500,
                "total_duplicate_guid": 0,
                "total_good": 500,
                "total_actual_good_qty": 500,
                "delta_vs_total_actual_good": 0,
                "check1_result": "PASS",
                "check2_result": "PASS",
                "check4_result": "PASS",
                "remarks": "No abnormalities or discrepancies were observed.",
                "first_pass": {"total_parts": 500, "pass_count": 500, "fail_count": 0, "rows": []},
                "retest": {"total_parts": 0, "pass_count": 0, "fail_count": 0, "rows": []},
                "qc": {"total_parts": 20, "pass_count": 20, "fail_count": 0, "rows": []},
            }

            hub_frame._show_result_popup(std_res)
            win2 = hub_frame._result_window
            self.assertIsNotNone(win2)
            self.assertTrue(win2.winfo_exists())
            win2.update_idletasks()

            labels2 = _gather_labels(win2)
            self.assertIn("Automatic FTDC Checker v1.3", labels2)
            self.assertIn("FIRST PASS", labels2)
            self.assertIn("TOTAL (FP + RETEST)", labels2)
            self.assertIn("First Pass STDF Good:", labels2)
            self.assertIn("Duplicate Good UID:", labels2)
            self.assertIn("First Pass Good w/o Duplicate UID:", labels2)
            self.assertIn("First Pass Actual Good:", labels2)
            self.assertIn("Difference between Good w/o Duplicate vs FP Actual Good:", labels2)
            self.assertIn("STDF Total Good:", labels2)
            self.assertIn("Total Good w/o Duplicate UID:", labels2)
            self.assertNotIn("Duplicate MACID:", labels2)
            self.assertNotIn("Duplicate BT MACID:", labels2)
            self.assertNotIn("Duplicate ZB MACID:", labels2)
            self.assertNotIn("Duplicate Good BT MACID:", labels2)
            self.assertNotIn("Duplicate Good ZB MACID:", labels2)
            # Verify 0 difference is rendered as "0" (green #008000) and NOT "N/A"!
            lws2 = _gather_label_widgets(win2)
            diff_zero_labels = [lw for lw in lws2 if lw.cget("text") == "0" and lw.cget("fg") == "#008000"]
            self.assertTrue(len(diff_zero_labels) >= 2, "Difference == 0 must render as '0' in green (#008000), not 'N/A'")
            win2.destroy()

        finally:
            try:
                root.destroy()
            except Exception:
                pass

    def test_37_excel_summary_worksheet_matrix_format_and_highlighting(self):
        """Verify Excel Summary worksheet layout and highlighting constraint:
        - Side-by-side comparative matrices for FIRST PASS GOOD and TOTAL GOOD aligned with FTDC App Pop-up Result Window.
        - Highlighting (fill color) is ONLY applied to Difference, Duplicate MACID (SG406), and Result.
        - Quantity rows (First Pass STDF Good, Duplicate Good UID, First Pass Good w/o Duplicate UID, First Pass Actual Good, QC Good)
          and all metric labels must have NO highlight.
        - Difference == 0 displays '0' (green highlight), NOT 'N/A'.
        """
        import tempfile
        import openpyxl
        from apps.ftdc_checker.excel_output import write_result_excel

        # 1. Standard Device Excel Verification (with FP Difference = 0 and Total SKIPPED)
        std_res = {
            "lot_id": "LOT-STD-777",
            "mpc": "STD123456789",
            "status": "PASS",
            "total_good_status": "SKIPPED",
            "first_pass_pass_count": 100,
            "first_pass_actual_good_qty": 100,
            "duplicate_guid": 0,
            "calculated_first_pass_qty": 100,
            "delta_vs_fp_actual_good": 0,
            "qc_sampling_good_in_prev_op": 10,
            "total_pass_parts": 0,
            "total_duplicate_guid": 0,
            "total_good": 0,
            "total_actual_good_qty": 0,
            "delta_vs_total_actual_good": None,
            "remarks": "No abnormalities or discrepancies were observed.",
            "qc": None,
        }

        with tempfile.TemporaryDirectory() as td:
            std_path = os.path.join(td, "std_summary.xlsx")
            write_result_excel(std_res, std_path)
            self.assertTrue(os.path.isfile(std_path))

            wb = openpyxl.load_workbook(std_path)
            self.assertIn("Summary", wb.sheetnames)
            ws = wb["Summary"]

            # Locate FIRST PASS and TOTAL (FP + RETEST) section headers
            fp_hdr_cell = None
            tot_hdr_cell = None
            for r in range(1, ws.max_row + 1):
                if ws.cell(r, 1).value == "FIRST PASS":
                    fp_hdr_cell = (r, 1)
                if ws.cell(r, 4).value == "TOTAL (FP + RETEST)":
                    tot_hdr_cell = (r, 4)

            self.assertIsNotNone(fp_hdr_cell, "FIRST PASS header not found at col 1")
            self.assertIsNotNone(tot_hdr_cell, "TOTAL (FP + RETEST) header not found at col 4")
            hdr_r = fp_hdr_cell[0]

            # Check rows 1 to 7 directly below header (aligned with FTDC App Pop-up Result Window)
            expected_fp_metrics = [
                "First Pass STDF Good",
                "Duplicate Good UID",
                "First Pass Good w/o Duplicate UID",
                "First Pass Actual Good",
                "Difference between Good w/o Duplicate vs FP Actual Good",
                "Total QC Units Good At Prev. Operation",
                "Result",
            ]
            expected_tot_metrics = [
                "STDF Total Good",
                "Duplicate Good UID",
                "Total Good w/o Duplicate UID",
                "Actual Total Good",
                "Difference between Good w/o Duplicate vs Actual Total Good",
                "Total QC Tested Good units",
                "Result",
            ]

            for idx, m_fp in enumerate(expected_fp_metrics):
                row_num = hdr_r + 1 + idx
                self.assertEqual(ws.cell(row_num, 1).value, m_fp)
                self.assertEqual(ws.cell(row_num, 4).value, expected_tot_metrics[idx])

                # Metric labels must have NO highlight
                self.assertIsNone(ws.cell(row_num, 1).fill.fill_type, f"Metric '{m_fp}' should not be highlighted")
                self.assertIsNone(ws.cell(row_num, 4).fill.fill_type, f"Metric '{expected_tot_metrics[idx]}' should not be highlighted")

                fp_val_cell = ws.cell(row_num, 2)
                tot_val_cell = ws.cell(row_num, 5)

                if "Difference between Good" in m_fp:
                    # FP Diff is 0 -> '0' (NOT 'N/A') with green highlight
                    self.assertEqual(str(fp_val_cell.value), "0")
                    self.assertEqual(fp_val_cell.fill.fill_type, "solid")
                    self.assertIn("90EE90", str(fp_val_cell.fill.start_color.rgb))
                    # Total is None (SKIPPED) -> N/A, no highlight
                    self.assertEqual(tot_val_cell.value, "N/A")
                    self.assertIsNone(tot_val_cell.fill.fill_type)
                elif m_fp == "Result":
                    # FP Result is PASS -> green
                    self.assertEqual(fp_val_cell.fill.fill_type, "solid")
                    self.assertIn("90EE90", str(fp_val_cell.fill.start_color.rgb))
                    # Total Result is SKIPPED -> no highlight
                    self.assertEqual(tot_val_cell.value, "SKIPPED")
                    self.assertIsNone(tot_val_cell.fill.fill_type)
                else:
                    # All other quantity rows must have NO highlight!
                    self.assertIsNone(fp_val_cell.fill.fill_type, f"FP '{m_fp}' value should not be highlighted")
                    self.assertIsNone(tot_val_cell.fill.fill_type, f"Total '{expected_tot_metrics[idx]}' value should not be highlighted")

            # 2. SG406 Device Excel Verification (Aligned with FTDC App Pop-up Result Window, Difference = 0)
            sg406_res = {
                "is_sg406": True,
                "lot_id": "LOT-SG406-777",
                "mpc": "SG4064RTBA10",
                "status": "PASS",
                "total_good_status": "PASS",
                "first_pass_pass_count": 1000,
                "first_pass_actual_good_qty": 1000,
                "total_pass_parts": 1000,
                "total_actual_good_qty": 1000,
                "duplicate_guid": 0,
                "duplicate_macid": 0,
                "calculated_first_pass_qty": 1000,
                "delta_vs_fp_actual_good": 0,
                "total_duplicate_guid": 0,
                "total_good": 1000,
                "delta_vs_total_actual_good": 0,
                "qc_sampling_good_in_prev_op": 50,
                "check1_result": "PASS", "check2_result": "PASS", "check4_result": "PASS",
                "remarks": "No abnormalities or discrepancies were observed.",
                "first_pass": {"total_parts": 1000, "pass_count": 1000, "fail_count": 0, "rows": []},
                "retest": {"total_parts": 0, "pass_count": 0, "fail_count": 0, "rows": []},
                "qc": {"total_parts": 50, "pass_count": 50, "fail_count": 0, "rows": []},
            }

            sg406_path = os.path.join(td, "sg406_summary.xlsx")
            write_result_excel(sg406_res, sg406_path)
            self.assertTrue(os.path.isfile(sg406_path))

            wb_sg = openpyxl.load_workbook(sg406_path)
            ws_sg = wb_sg["Summary"]

            # Locate FIRST PASS and TOTAL (FP + RETEST) headers (at col 1 and col 4)
            sg_fp_hdr = None
            sg_tot_hdr = None
            for r in range(1, ws_sg.max_row + 1):
                if ws_sg.cell(r, 1).value == "FIRST PASS":
                    sg_fp_hdr = (r, 1)
                if ws_sg.cell(r, 4).value == "TOTAL (FP + RETEST)":
                    sg_tot_hdr = (r, 4)

            self.assertIsNotNone(sg_fp_hdr, "SG406 FIRST PASS header not found at col 1")
            self.assertIsNotNone(sg_tot_hdr, "SG406 TOTAL (FP + RETEST) header not found at col 4")
            hdr_sg_r = sg_fp_hdr[0]

            # Check rows with Option 2 Duplicate MACID (BT / ZB) and Unique Valid Good (WXY / UID / BT / ZB) under Total QC row (aligned with Popup)
            expected_sg_fp_metrics = [
                "First Pass STDF Good",
                "Duplicate Good UID",
                "First Pass Good w/o Duplicate UID",
                "First Pass Actual Good",
                "Difference between Good w/o Duplicate vs FP Actual Good",
                "Total QC Units Good At Prev. Operation",
                "Duplicate MACID (BT / ZB)",
                "Unique Valid Good (WXY / UID / BT / ZB)",
                "Result",
            ]
            expected_sg_tot_metrics = [
                "STDF Total Good",
                "Duplicate Good UID",
                "Total Good w/o Duplicate UID",
                "Actual Total Good",
                "Difference between Good w/o Duplicate vs Actual Total Good",
                "Total QC Tested Good units",
                "Duplicate MACID (BT / ZB)",
                "Unique Valid Good (WXY / UID / BT / ZB)",
                "Result",
            ]

            for idx, m_fp in enumerate(expected_sg_fp_metrics):
                row_num = hdr_sg_r + 1 + idx
                self.assertEqual(ws_sg.cell(row_num, 1).value, m_fp)
                self.assertEqual(ws_sg.cell(row_num, 4).value, expected_sg_tot_metrics[idx])

                # Metric labels must have NO highlight
                self.assertIsNone(ws_sg.cell(row_num, 1).fill.fill_type, f"Metric '{m_fp}' should not be highlighted")
                self.assertIsNone(ws_sg.cell(row_num, 4).fill.fill_type, f"Metric '{expected_sg_tot_metrics[idx]}' should not be highlighted")

                fp_cell = ws_sg.cell(row_num, 2)
                tot_cell = ws_sg.cell(row_num, 5)

                if "Difference between Good" in m_fp:
                    self.assertEqual(str(fp_cell.value), "0")
                    self.assertEqual(str(tot_cell.value), "0")
                    self.assertEqual(fp_cell.fill.fill_type, "solid")
                    self.assertIn("90EE90", str(fp_cell.fill.start_color.rgb))
                    self.assertEqual(tot_cell.fill.fill_type, "solid")
                    self.assertIn("90EE90", str(tot_cell.fill.start_color.rgb))
                elif m_fp == "Result":
                    self.assertEqual(fp_cell.fill.fill_type, "solid")
                    self.assertIn("90EE90", str(fp_cell.fill.start_color.rgb))
                    self.assertEqual(tot_cell.fill.fill_type, "solid")
                    self.assertIn("90EE90", str(tot_cell.fill.start_color.rgb))
                elif m_fp in ("Duplicate MACID (BT / ZB)", "Unique Valid Good (WXY / UID / BT / ZB)"):
                    # Count == 0 / Discrepancy == 0 -> Green highlight
                    self.assertEqual(fp_cell.fill.fill_type, "solid")
                    self.assertIn("90EE90", str(fp_cell.fill.start_color.rgb))
                    self.assertEqual(tot_cell.fill.fill_type, "solid")
                    self.assertIn("90EE90", str(tot_cell.fill.start_color.rgb))
                else:
                    # Quantity rows: NO highlight!
                    self.assertIsNone(fp_cell.fill.fill_type, f"SG406 FP '{m_fp}' should not be highlighted")
                    self.assertIsNone(tot_cell.fill.fill_type, f"SG406 Total '{expected_sg_tot_metrics[idx]}' should not be highlighted")

            # 2b. Test SG406 with Duplicate BT MACID > 0 and Good Unique Discrepancy > 0 -> Red highlight in Excel (#FF5050)
            sg406_fail = dict(sg406_res)
            sg406_fail["duplicate_bt_macid"] = 3
            sg406_fail["fp_good_unique_discrepancy"] = 2
            sg406_fail["total_good_unique_discrepancy"] = 2
            sg406_fail["status"] = "FAIL"
            sg406_fail["total_good_status"] = "FAIL"
            sg406_fail_path = os.path.join(td, "sg406_fail.xlsx")
            write_result_excel(sg406_fail, sg406_fail_path)
            wb_fail = openpyxl.load_workbook(sg406_fail_path)
            ws_fail = wb_fail["Summary"]
            bt_row = None
            gu_row = None
            for r in range(hdr_sg_r + 1, hdr_sg_r + 14):
                if ws_fail.cell(r, 1).value == "Duplicate MACID (BT / ZB)":
                    bt_row = r
                if ws_fail.cell(r, 1).value == "Unique Valid Good (WXY / UID / BT / ZB)":
                    gu_row = r
            self.assertIsNotNone(bt_row)
            self.assertIsNotNone(gu_row)
            self.assertEqual(ws_fail.cell(bt_row, 2).fill.fill_type, "solid")
            self.assertIn("FF5050", str(ws_fail.cell(bt_row, 2).fill.start_color.rgb))
            self.assertEqual(ws_fail.cell(bt_row, 5).fill.fill_type, "solid")
            self.assertIn("FF5050", str(ws_fail.cell(bt_row, 5).fill.start_color.rgb))
            self.assertEqual(ws_fail.cell(gu_row, 2).fill.fill_type, "solid")
            self.assertIn("FF5050", str(ws_fail.cell(gu_row, 2).fill.start_color.rgb))
            self.assertEqual(ws_fail.cell(gu_row, 5).fill.fill_type, "solid")
            self.assertIn("FF5050", str(ws_fail.cell(gu_row, 5).fill.start_color.rgb))

    def test_38_zero_zero_zero_wxy_excluded_from_duplicates(self):
        """Verify:
        1) For standard devices: 0_0_0 WXY coordinate is invalid and excluded from duplicate counts.
           Multiple 0_0_0 units in FP do NOT trigger fp_datalog_duplicate_count or duplicate_guid.
        2) For SG406 devices: 0_0_0 WXY coordinate is invalid and excluded from duplicate counts.
        3) In Excel: 0_0_0 rows are not classified as duplicate WXY.
        """
        import struct
        import tempfile
        import openpyxl
        from apps.ftdc_checker.guid_analysis import analyze_guid_data
        from apps.ftdc_checker.excel_output import write_result_excel

        def _build_std(lot_id: str, parts_spec: list, tests=(101, 102, 103)) -> bytes:
            out = bytearray()
            out += TestSTDFParserAndReliability._rec(0, 10, bytes([2, 4]), "<")
            mir_payload = bytearray(
                struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535)
            )
            mir_payload += ord(" ").to_bytes(1, "little")
            for s in (lot_id, "DEV", "NODE1", "V93K", "JOB1"):
                mir_payload += TestSTDFParserAndReliability._cn(s)
            for _ in range(25):
                mir_payload += b"\x00"
            out += TestSTDFParserAndReliability._rec(1, 10, bytes(mir_payload), "<")
            w_t, x_t, y_t = tests
            good_cnt = 0
            for idx, (pid, is_pass, w, x, y) in enumerate(parts_spec, start=1):
                out += TestSTDFParserAndReliability._rec(5, 10, bytes([1, 1]), "<")
                for tnum, val in ((w_t, float(w)), (x_t, float(x)), (y_t, float(y))):
                    ptr_body = bytearray(struct.pack("<IBBBBf", tnum, 1, 1, 0, 0, float(val)))
                    ptr_body += TestSTDFParserAndReliability._cn(f"T_{tnum}") + b"\x00"
                    ptr_body += bytes([0, 0, 0, 0])
                    ptr_body += struct.pack("<ff", -1e6, 1e6)
                    out += TestSTDFParserAndReliability._rec(15, 10, bytes(ptr_body), "<")
                if is_pass:
                    good_cnt += 1
                part_flg = 0x00 if is_pass else 0x08
                prr_body = bytearray(
                    struct.pack("<BBBHHHhhI", 1, 1, part_flg, 3, 1 if is_pass else 5, 1 if is_pass else 5, -32768, -32768, 12)
                )
                prr_body += TestSTDFParserAndReliability._cn(str(pid)) + b"\x00\x00"
                out += TestSTDFParserAndReliability._rec(5, 20, bytes(prr_body), "<")
            out += TestSTDFParserAndReliability._rec(1, 30, struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt, 0), "<")
            out += TestSTDFParserAndReliability._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
            return bytes(out)

        tmp_dir = tempfile.mkdtemp()
        try:
            fp_path = os.path.join(tmp_dir, "fp.std")
            rt_path = os.path.join(tmp_dir, "rt.std")
            qc_path = os.path.join(tmp_dir, "qc.std")

            # 1. Standard device: FP has two 0_0_0 parts and one 1_5_10 part
            with open(fp_path, "wb") as f:
                f.write(_build_std("LOT_ZERO.000", [
                    ("P1", True, 0, 0, 0),
                    ("P2", True, 0, 0, 0),
                    ("P3", True, 1, 5, 10),
                ]))
            with open(rt_path, "wb") as f:
                f.write(_build_std("LOT_ZERO.000", [
                    ("RT1", True, 1, 5, 11),
                ]))
            with open(qc_path, "wb") as f:
                f.write(_build_std("LOT_ZERO.000", [
                    ("Q1", True, 1, 5, 10),
                ]))

            res_std = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id="LOT_ZERO.000",
                mpc_text="59793QRTBC01",
                first_pass_actual_good_qty="3",
                total_actual_good_qty="3",
                manual_filter_tests=[101, 102, 103],
            )
            # Two 0_0_0 units MUST NOT be counted as duplicates
            self.assertEqual(res_std["fp_datalog_duplicate_count"], 0)
            self.assertEqual(res_std["duplicate_guid"], 0)
            self.assertEqual(res_std["check1_result"], "PASS")

            # Verify in Excel: "Duplicate GUID First Pass" sheet has 0 duplicate data rows
            xlsx_path = os.path.join(tmp_dir, "std_zero.xlsx")
            write_result_excel(res_std, xlsx_path)
            wb_std = openpyxl.load_workbook(xlsx_path)
            self.assertIn("Duplicate GUID First Pass", wb_std.sheetnames)
            ws_dup = wb_std["Duplicate GUID First Pass"]
            self.assertEqual(ws_dup.max_row, 1)  # header only!

            # 2. SG406 device: FP has two 0_0_0 parts with unique UIDs and MACIDs
            def _build_sg_parts(parts_spec):
                out = bytearray()
                out += TestSTDFParserAndReliability._rec(0, 10, bytes([2, 4]), "<")
                mir = bytearray(struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
                mir += b" "
                for s in ("LOT_SG_ZERO.000", "SG406", "NODE1", "V93K", "JOB1"):
                    mir += TestSTDFParserAndReliability._cn(s)
                mir += b"\x00" * 25
                out += TestSTDFParserAndReliability._rec(1, 10, bytes(mir), "<")
                out += TestSTDFParserAndReliability._rec(1, 80, bytes([1, 2, 1, 1]) + TestSTDFParserAndReliability._cn("H"), "<")
                for p in parts_spec:
                    out += TestSTDFParserAndReliability._rec(5, 10, bytes([1, 1]), "<")
                    tmap = {}
                    tmap[93005], tmap[93006], tmap[93007] = p["wxy"]
                    tmap[93009] = p["uid"]
                    for bi, bv in enumerate(p["bt"]):
                        tmap[94016 + bi] = bv
                    for zi, zv in enumerate(p["zb"]):
                        tmap[94022 + zi] = zv
                    for tnum, val in sorted(tmap.items()):
                        ptr = bytearray(struct.pack("<IBBBBf", tnum, 1, 1, 0, 0, float(val)))
                        ptr += TestSTDFParserAndReliability._cn(f"T_{tnum}") + b"\x00\x00\x00\x00\x00"
                        ptr += struct.pack("<ff", -1e6, 1e6)
                        out += TestSTDFParserAndReliability._rec(15, 10, bytes(ptr), "<")
                    flg = 0x00 if p.get("pass", True) else 0x08
                    hb = 1 if p.get("pass", True) else 5
                    prr = bytearray(struct.pack("<BBBHHHhhI", 1, 1, flg, len(tmap), hb, hb, -32768, -32768, 12))
                    prr += TestSTDFParserAndReliability._cn(str(p["part_id"])) + b"\x00\x00"
                    out += TestSTDFParserAndReliability._rec(5, 20, bytes(prr), "<")
                good_cnt_sg = sum(1 for p in parts_spec if p.get("pass", True))
                out += TestSTDFParserAndReliability._rec(1, 30, struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt_sg, 0), "<")
                out += TestSTDFParserAndReliability._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
                return bytes(out)

            sg_fp = [
                {"part_id": "1", "pass": False, "wxy": (0, 0, 0), "uid": 1001, "bt": (0, 0, 0, 0, 0, 0), "zb": (0, 0, 0, 0, 0, 0, 0, 0)},
                {"part_id": "2", "pass": False, "wxy": (0, 0, 0), "uid": 1002, "bt": (0, 0, 0, 0, 0, 0), "zb": (0, 0, 0, 0, 0, 0, 0, 0)},
                {"part_id": "1b", "pass": False, "wxy": (0, 1, 2), "uid": 1005, "bt": (0, 0, 0, 0, 0, 0), "zb": (0, 0, 0, 0, 0, 0, 0, 0)},
                {"part_id": "2b", "pass": False, "wxy": (0, 1, 2), "uid": 1006, "bt": (0, 0, 0, 0, 0, 0), "zb": (0, 0, 0, 0, 0, 0, 0, 0)},
                {"part_id": "3", "pass": True, "wxy": (1, 10, 20), "uid": 1003, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
            ]
            sg_rt = [
                {"part_id": "4", "pass": True, "wxy": (1, 10, 21), "uid": 1004, "bt": (64, 132, 3, 4, 5, 7), "zb": (64, 132, 12, 13, 14, 15, 16, 18)},
            ]
            sg_qc = [
                {"part_id": "Q1", "pass": True, "wxy": (1, 10, 20), "uid": 1003, "bt": (64, 132, 3, 4, 5, 6), "zb": (64, 132, 12, 13, 14, 15, 16, 17)},
            ]
            with open(fp_path, "wb") as f:
                f.write(_build_sg_parts(sg_fp))
            with open(rt_path, "wb") as f:
                f.write(_build_sg_parts(sg_rt))
            with open(qc_path, "wb") as f:
                f.write(_build_sg_parts(sg_qc))

            res_sg = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id="LOT_SG_ZERO.000",
                mpc_text="SG4064RTBA10",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="2",
                max_workers=1,
            )
            self.assertEqual(res_sg["fp_datalog_duplicate_count"], 0)
            self.assertEqual(res_sg["duplicate_guid"], 0)
            self.assertEqual(res_sg["duplicate_bt_macid"], 0)
            self.assertEqual(res_sg["duplicate_zb_macid"], 0)
            self.assertEqual(res_sg["fp_good_unique_discrepancy"], 0)
            self.assertEqual(res_sg["total_good_unique_discrepancy"], 0)
            self.assertEqual(res_sg["status"], "PASS")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_39_auto_attach_ppo_with_result_window_image(self):
        """Verify:
        1) Proper naming for result window image: {safe_lot}_FTDC_Result_{timestamp}.png.
        2) Programmatic and native capture generating valid PNG images with full comparative metrics.
        3) Auto-attachment to PPO copies BOTH the Excel file (.xlsx) and Result window image (.png)
           to every PPO subfolder with atomic rename (.part -> .ext).
        4) Local fallback copies BOTH the Excel file and Result image to C:\\FTDC\\{MIR LOT_ID}\\ on failure.
        """
        import tempfile
        import tkinter as tk
        from unittest import mock
        from apps.ftdc_checker import ui as ftdc_ui
        from apps.ftdc_checker.ui import FTDCCheckerFrame
        from apps.ftdc_checker.result_image import (
            get_default_result_image_name, render_result_image, save_result_window_image,
        )

        # 1. Verify proper naming logic
        res = {
            "lot_id": "MMT-272900204.000",
            "mir_lot_id": "MMT-272900204.000",
            "mpc": "59793QRTBC01",
            "status": "PASS",
            "total_good_status": "PASS",
            "first_pass_pass_count": 100,
            "first_pass_actual_good": 100,
            "retest_pass_count": 0,
            "qc_good_prev_op_count": 10,
            "qc_failed_or_missing_prev_op_qty": 0,
            "first_pass": {"rows": []},
            "qc": {"rows": []},
        }
        img_name = get_default_result_image_name(res, "20261109153000")
        self.assertEqual(img_name, "MMT-272900204.000_FTDC_Result_20261109153000.png")

        temp_dir = tempfile.mkdtemp()
        try:
            # 2. Verify render_result_image generates valid non-empty PNG
            img_path = os.path.join(temp_dir, img_name)
            ok = render_result_image(res, img_path)
            self.assertTrue(ok)
            self.assertTrue(os.path.isfile(img_path))
            self.assertGreater(os.path.getsize(img_path), 1000)

            # 3. Verify PPO attachment attaches BOTH Excel and Result Image to all subfolders
            ppo_base = os.path.join(temp_dir, "PPO")
            local_base = os.path.join(temp_dir, "LOCAL")
            sub_a = os.path.join(ppo_base, "59793", "MMT-272900204.000", "OPER_1")
            sub_b = os.path.join(ppo_base, "59793", "MMT-272900204.000", "OPER_2")
            os.makedirs(sub_a)
            os.makedirs(sub_b)

            src_xlsx = os.path.join(temp_dir, "src.xlsx")
            with open(src_xlsx, "wb") as fh:
                fh.write(b"PK-dummy-excel-data")

            xlsx_name = "MMT-272900204.000_FTDC_20261109153000.xlsx"
            root = tk.Tk()
            root.withdraw()
            try:
                ftdc = FTDCCheckerFrame(root)
                ftdc.PPO_ATTACH_RETRY_DELAYS = (0.0, 0.0)
                notices = []
                ftdc._queue_ppo_notice = lambda kind, title, msg, open_dir=None: notices.append((kind, title, msg, open_dir))
                gen_id = ftdc._excel_generation_id

                with mock.patch.object(ftdc_ui, "is_developer_mode", return_value=False), \
                     mock.patch.object(ftdc_ui, "get_ppo_base_path", return_value=ppo_base), \
                     mock.patch.object(ftdc_ui, "get_local_dest_base", return_value=local_base):
                    ftdc._auto_attach_excel_to_ppo(
                        res, src_xlsx, xlsx_name, gen_id,
                        extra_attachments=[(img_path, img_name)],
                    )
                    self.assertEqual(ftdc._ppo_attach_state, "SUCCESS")
                    # Both files must exist in both PPO subfolders
                    self.assertTrue(os.path.isfile(os.path.join(sub_a, xlsx_name)))
                    self.assertTrue(os.path.isfile(os.path.join(sub_a, img_name)))
                    self.assertTrue(os.path.isfile(os.path.join(sub_b, xlsx_name)))
                    self.assertTrue(os.path.isfile(os.path.join(sub_b, img_name)))
                    # No leftover .part files
                    self.assertFalse(any(f.endswith(".part") for f in os.listdir(sub_a)))
                    self.assertFalse(any(f.endswith(".part") for f in os.listdir(sub_b)))
                    # Notice was queued
                    self.assertEqual(notices[-1][0], "info")
                    self.assertIn("Result files", notices[-1][2])

                    # 4. Verify Local fallback copies BOTH files on PPO failure
                    notices.clear()
                    bad_res = dict(res, lot_id="MISSING_LOT.000")
                    ftdc._auto_attach_excel_to_ppo(
                        bad_res, src_xlsx, xlsx_name, gen_id,
                        extra_attachments=[(img_path, img_name)],
                    )
                    self.assertEqual(ftdc._ppo_attach_state, "FAILED")
                    local_dir = os.path.join(local_base, "MMT-272900204.000")
                    self.assertTrue(os.path.isfile(os.path.join(local_dir, xlsx_name)))
                    self.assertTrue(os.path.isfile(os.path.join(local_dir, img_name)))
            finally:
                root.destroy()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_40_retest_duplicate_counting_and_dual_labels(self):
        """Verify RETEST duplicate counting and dual labels:
        1) When a unit passes in RETEST (attempt 1), fails in RETEST (attempt 2), and passes again in RETEST (attempt 3):
           - Attempt 2 is counted as a RETEST duplicate (preceded by attempt 1 PASS).
           - Attempt 3 is NOT counted as a RETEST duplicate (preceded by attempt 2 FAIL, which resets retest_pass_seen_ids).
        2) When a unit passes in FP (row A), passes in RETEST (row B), and passes again in RETEST (row C):
           - Row A is labeled 'FP Original'
           - Row B is labeled 'FP Duplicate / RETEST Original'
           - Row C is labeled 'RETEST Duplicate'
        3) When a unit fails in FP, then passes 3 times in a row in RETEST (row D, row E, row F):
           - Row D is labeled 'RETEST Original'
           - Row E is labeled 'RETEST Duplicate / RETEST Original'
           - Row F is labeled 'RETEST Duplicate'
        """
        import struct
        import tempfile
        from apps.ftdc_checker.guid_analysis import analyze_guid_data
        from apps.ftdc_checker.sg406_analysis import _is_valid_macid

        # Verify _is_valid_macid directly
        self.assertTrue(_is_valid_macid("64_132_50_12_192_114"))
        self.assertTrue(_is_valid_macid("64_132_50_0_0_12_194_204"))
        self.assertTrue(_is_valid_macid("40:84:32:0C:C0:72"))
        self.assertFalse(_is_valid_macid("0_0_0_0_0_0"))
        self.assertFalse(_is_valid_macid("00:00:00:00:00:00"))
        self.assertFalse(_is_valid_macid("17_34_51_68_85_102"))
        self.assertFalse(_is_valid_macid("11:22:33:44:55:66"))
        self.assertFalse(_is_valid_macid("NaN_NaN_NaN_NaN_NaN_NaN"))
        self.assertFalse(_is_valid_macid(""))

        def _build_std(lot_id: str, parts_spec: list, tests=(101, 102, 103)) -> bytes:
            out = bytearray()
            out += TestSTDFParserAndReliability._rec(0, 10, bytes([2, 4]), "<")
            mir_payload = bytearray(
                struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535)
            )
            mir_payload += ord(" ").to_bytes(1, "little")
            for s in (lot_id, "DEV", "NODE1", "V93K", "JOB1"):
                mir_payload += TestSTDFParserAndReliability._cn(s)
            for _ in range(25):
                mir_payload += b"\x00"
            out += TestSTDFParserAndReliability._rec(1, 10, bytes(mir_payload), "<")
            w_t, x_t, y_t = tests
            good_cnt = 0
            for pid, is_pass, w, x, y in parts_spec:
                out += TestSTDFParserAndReliability._rec(5, 10, bytes([1, 1]), "<")
                for tnum, val in ((w_t, float(w)), (x_t, float(x)), (y_t, float(y))):
                    ptr_body = bytearray(struct.pack("<IBBBBf", tnum, 1, 1, 0, 0, float(val)))
                    ptr_body += TestSTDFParserAndReliability._cn(f"T_{tnum}") + b"\x00"
                    ptr_body += bytes([0, 0, 0, 0])
                    ptr_body += struct.pack("<ff", -1e6, 1e6)
                    out += TestSTDFParserAndReliability._rec(15, 10, bytes(ptr_body), "<")
                if is_pass:
                    good_cnt += 1
                part_flg = 0x00 if is_pass else 0x08
                prr_body = bytearray(
                    struct.pack("<BBBHHHhhI", 1, 1, part_flg, 3, 1 if is_pass else 5, 1 if is_pass else 5, -32768, -32768, 12)
                )
                prr_body += TestSTDFParserAndReliability._cn(str(pid)) + b"\x00\x00"
                out += TestSTDFParserAndReliability._rec(5, 20, bytes(prr_body), "<")
            out += TestSTDFParserAndReliability._rec(1, 30, struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt, 0), "<")
            out += TestSTDFParserAndReliability._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
            return bytes(out)

        tmp_dir = tempfile.mkdtemp()
        try:
            fp_path = os.path.join(tmp_dir, "fp.std")
            rt_path = os.path.join(tmp_dir, "rt.std")
            qc_path = os.path.join(tmp_dir, "qc.std")

            # Unit A (10_20_30): FP FAIL -> RT PASS (R1) -> RT FAIL (R2) -> RT PASS (R3)
            #   R2 is RETEST Duplicate of R1; R3 is recovery of R2 FAIL so NOT a duplicate! (1 retest_duplicate_guid)
            # Unit B (11_22_33): FP PASS (F2) -> RT PASS (R4) -> RT PASS (R5)
            #   F2 = 'FP Original', R4 = 'FP Duplicate / RETEST Original', R5 = 'RETEST Duplicate'
            #   (1 duplicate_guid, 1 retest_duplicate_guid)
            # Unit C (12_23_34): FP FAIL (F3) -> RT PASS (R6) -> RT PASS (R7) -> RT PASS (R8)
            #   R6 = 'RETEST Original', R7 = 'RETEST Duplicate / RETEST Original', R8 = 'RETEST Duplicate'
            #   (2 retest_duplicate_guid)
            with open(fp_path, "wb") as f:
                f.write(_build_std("LOT_RT_LOGIC.000", [
                    ("F1", False, 10, 20, 30),
                    ("F2", True,  11, 22, 33),
                    ("F3", False, 12, 23, 34),
                ]))
            with open(rt_path, "wb") as f:
                f.write(_build_std("LOT_RT_LOGIC.000", [
                    ("R1", True,  10, 20, 30),  # Unit A PASS 1 -> RETEST Original
                    ("R2", False, 10, 20, 30),  # Unit A FAIL (after R1 PASS) -> RETEST Duplicate (resets seen!)
                    ("R3", True,  10, 20, 30),  # Unit A PASS 2 (after R2 FAIL) -> No Duplicate!
                    ("R4", True,  11, 22, 33),  # Unit B PASS in RT (after F2 PASS) -> FP Duplicate / RETEST Original
                    ("R5", True,  11, 22, 33),  # Unit B PASS again in RT -> RETEST Duplicate
                    ("R6", True,  12, 23, 34),  # Unit C PASS 1 in RT -> RETEST Original
                    ("R7", True,  12, 23, 34),  # Unit C PASS 2 in RT -> RETEST Duplicate / RETEST Original
                    ("R8", True,  12, 23, 34),  # Unit C PASS 3 in RT -> RETEST Duplicate
                ]))
            with open(qc_path, "wb") as f:
                f.write(_build_std("LOT_RT_LOGIC.000", [
                    ("Q1", True, 10, 20, 30),
                    ("Q2", True, 11, 22, 33),
                    ("Q3", True, 12, 23, 34),
                ]))

            res = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id="LOT_RT_LOGIC.000",
                mpc_text="59793QRTBC01",
                first_pass_actual_good_qty="1",
                total_actual_good_qty="3",
                manual_filter_tests=[101, 102, 103],
            )

            # Verify counts:
            # FP original duplicate = 1 (R4)
            # RETEST duplicate = 1 (R2) + 1 (R5) + 2 (R7, R8) = 4 (R3 is NOT counted!)
            self.assertEqual(res["duplicate_guid"], 1)
            self.assertEqual(res["retest_duplicate_guid"], 4)
            self.assertEqual(res["total_duplicate_guid"], 5)

            fp_rows = res["first_pass"]["rows"]
            rt_rows = res["retest"]["rows"]

            self.assertEqual(fp_rows[1]["is_total_duplicate"], "FP Original")
            self.assertEqual(rt_rows[0]["is_total_duplicate"], "RETEST Original")
            self.assertEqual(rt_rows[1]["is_total_duplicate"], "RETEST Duplicate")
            self.assertEqual(rt_rows[2]["is_total_duplicate"], "No Duplicate")
            self.assertEqual(rt_rows[3]["is_total_duplicate"], "FP Duplicate / RETEST Original")
            self.assertEqual(rt_rows[4]["is_total_duplicate"], "RETEST Duplicate")
            self.assertEqual(rt_rows[5]["is_total_duplicate"], "RETEST Original")
            self.assertEqual(rt_rows[6]["is_total_duplicate"], "RETEST Duplicate / RETEST Original")
            self.assertEqual(rt_rows[7]["is_total_duplicate"], "RETEST Duplicate")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_41_two_zeros_wxy_streaming_prewarm_help_and_tooltips(self):
        """Verify:
        1) Any WXY coordinate with two (or more) 0's in any order is invalid and excluded from duplicates.
        2) Streaming per-file pre-warm cache during copy_stdf_files_per_file (2 workers + single disk flush).
        3) Help button opens PPT directly inside get_help_dir().
        4) Tooltip buttons on First Pass Actual Good QTY, Total Actual Good QTY, and Duplicate Good UID (FP & Total).
        """
        import shutil
        import struct
        import tempfile
        import time
        import tkinter as tk
        from unittest.mock import patch
        from apps.ftdc_checker.guid_analysis import _is_valid_wxy, analyze_guid_data
        from apps.ftdc_checker.sg406_analysis import _is_valid_identifier, _is_valid_sg406_wxy
        from apps.ftdc_checker.excel_output import _is_valid_wxy as _excel_is_valid_wxy, write_result_excel
        from apps.ftdc_checker.ui import (
            FTDCCheckerFrame,
            TOOLTIP_FP_ACTUAL_GOOD,
            TOOLTIP_TOTAL_ACTUAL_GOOD,
            TOOLTIP_FP_DUPLICATE_GOOD_UID,
            TOOLTIP_TOTAL_DUPLICATE_GOOD_UID,
        )
        from core.widgets import InfoTooltipButton
        from core.config import get_help_dir

        # ── 1. Wafer == 0 (regardless of X/Y) & two-zeros WXY validity check ──
        for invalid_coord in (
            "0_1_2", "0_137_136", "00_1_2", "-0_1_2", "0.0_1_2",
            "0_0_0", "0_0_5", "0_12_0", "24_0_0", "00_0_99", "0_0.0_15", "NaN_NaN_NaN",
        ):
            self.assertFalse(_is_valid_wxy(invalid_coord), f"Expected '{invalid_coord}' to be invalid")
            self.assertFalse(_excel_is_valid_wxy(invalid_coord), f"Expected '{invalid_coord}' to be invalid in excel_output")
            self.assertFalse(_is_valid_identifier(invalid_coord, "wxy"), f"Expected '{invalid_coord}' to be invalid in sg406")
            self.assertFalse(_is_valid_sg406_wxy(invalid_coord), f"Expected '{invalid_coord}' to be invalid in _is_valid_sg406_wxy")

        for valid_coord in ("24_137_136", "24_0_136", "24_137_0", "1_0_5", "12_7_0"):
            self.assertTrue(_is_valid_wxy(valid_coord), f"Expected '{valid_coord}' (non-zero wafer, <=1 zero) to be valid")
            self.assertTrue(_excel_is_valid_wxy(valid_coord), f"Expected '{valid_coord}' to be valid in excel_output")
            self.assertTrue(_is_valid_identifier(valid_coord, "wxy"), f"Expected '{valid_coord}' to be valid in sg406")
            self.assertTrue(_is_valid_sg406_wxy(valid_coord), f"Expected '{valid_coord}' to be valid in _is_valid_sg406_wxy")

        # Verify in full analyze_guid_data that repeated 0_1_2 / 0_0_5 / 0_12_0 / 24_0_0 do NOT count as duplicates
        def _build_std(lot_id: str, parts_spec: list, tests=(101, 102, 103)) -> bytes:
            out = bytearray()
            out += TestSTDFParserAndReliability._rec(0, 10, bytes([2, 4]), "<")
            mir_payload = bytearray(
                struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535)
            )
            mir_payload += ord(" ").to_bytes(1, "little")
            for s in (lot_id, "DEV", "NODE1", "V93K", "JOB1"):
                mir_payload += TestSTDFParserAndReliability._cn(s)
            for _ in range(25):
                mir_payload += b"\x00"
            out += TestSTDFParserAndReliability._rec(1, 10, bytes(mir_payload), "<")
            w_t, x_t, y_t = tests
            good_cnt = 0
            for pid, is_pass, w, x, y in parts_spec:
                out += TestSTDFParserAndReliability._rec(5, 10, bytes([1, 1]), "<")
                for tnum, val in ((w_t, float(w)), (x_t, float(x)), (y_t, float(y))):
                    ptr_body = bytearray(struct.pack("<IBBBBf", tnum, 1, 1, 0, 0, float(val)))
                    ptr_body += TestSTDFParserAndReliability._cn(f"T_{tnum}") + b"\x00"
                    ptr_body += bytes([0, 0, 0, 0])
                    ptr_body += struct.pack("<ff", -1e6, 1e6)
                    out += TestSTDFParserAndReliability._rec(15, 10, bytes(ptr_body), "<")
                if is_pass:
                    good_cnt += 1
                part_flg = 0x00 if is_pass else 0x08
                prr_body = bytearray(
                    struct.pack("<BBBHHHhhI", 1, 1, part_flg, 3, 1 if is_pass else 5, 1 if is_pass else 5, -32768, -32768, 12)
                )
                prr_body += TestSTDFParserAndReliability._cn(str(pid)) + b"\x00\x00"
                out += TestSTDFParserAndReliability._rec(5, 20, bytes(prr_body), "<")
            out += TestSTDFParserAndReliability._rec(1, 30, struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt, 0), "<")
            out += TestSTDFParserAndReliability._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
            return bytes(out)

        tmp_dir = tempfile.mkdtemp(prefix="test_two_zeros_")
        try:
            fp_path = os.path.join(tmp_dir, "LOT_2ZERO.000_e_20261110100000.std")
            rt_path = os.path.join(tmp_dir, "LOT_2ZERO.000_w_rj1_20261110110000.std")
            qc_path = os.path.join(tmp_dir, "LOT_2ZERO.000_q_20261110120000.std")

            with open(fp_path, "wb") as f:
                f.write(_build_std("LOT_2ZERO.000", [
                    ("1", True, 10, 20, 30),
                    ("2", True, 0, 0, 5),     # invalid: two 0's (W=0, X=0)
                    ("3", True, 0, 0, 5),     # repeated invalid -> must NOT count as duplicate!
                    ("4", True, 0, 12, 0),    # invalid: two 0's (W=0, Y=0)
                    ("5", True, 0, 12, 0),    # repeated invalid -> must NOT count as duplicate!
                    ("6", True, 24, 0, 0),    # invalid: two 0's (X=0, Y=0)
                    ("7", True, 24, 0, 0),    # repeated invalid -> must NOT count as duplicate!
                    ("7a", True, 0, 1, 2),    # invalid: wafer == 0 regardless of X and Y
                    ("7b", True, 0, 1, 2),    # repeated wafer==0 -> must NOT count as duplicate!
                ]))
            with open(rt_path, "wb") as f:
                f.write(_build_std("LOT_2ZERO.000", [
                    ("8", True, 0, 0, 5),
                    ("9", True, 24, 0, 0),
                    ("9a", True, 0, 1, 2),
                    ("9b", True, 0, 1, 2),
                ]))
            with open(qc_path, "wb") as f:
                f.write(_build_std("LOT_2ZERO.000", [
                    ("10", True, 10, 20, 30),
                    ("11", True, 0, 1, 2),
                    ("12", True, 0, 1, 2),
                ]))

            res = analyze_guid_data(
                first_pass_paths=[fp_path],
                retest_paths=[rt_path],
                qc_paths=[qc_path],
                lot_id="LOT_2ZERO.000",
                mpc_text="59793QRTBC01",
                first_pass_actual_good_qty="9",
                total_actual_good_qty="13",
                manual_filter_tests=[101, 102, 103],
            )
            self.assertEqual(res["fp_datalog_duplicate_count"], 0)
            self.assertEqual(res["duplicate_guid"], 0)
            self.assertEqual(res["retest_duplicate_guid"], 0)
            self.assertEqual(res["total_duplicate_guid"], 0)
            self.assertEqual(res["qc_duplicate_guid"], 0)
            self.assertEqual(res["check4_result"], "PASS")
            self.assertEqual(res["status"], "PASS")

            import openpyxl
            zero_xlsx = os.path.join(tmp_dir, "zero_wafer_check.xlsx")
            write_result_excel(res, zero_xlsx)
            wb_zero = openpyxl.load_workbook(zero_xlsx)
            self.assertEqual(wb_zero["Duplicate GUID First Pass"].max_row, 1)

            # ── 2. Streaming per-file pre-warm cache during copy ─────────────
            root = tk.Tk()
            root.withdraw()
            try:
                hub_frame = FTDCCheckerFrame(root, hub=None)
                self.assertEqual(hub_frame.CHECK_PREWARM_WORKERS_DURING_COPY, 2)

                src_dir = os.path.join(tmp_dir, "net_src")
                dest_base = os.path.join(tmp_dir, "local_ftdc")
                os.makedirs(src_dir, exist_ok=True)
                s1 = os.path.join(src_dir, "LOT_2ZERO.000_e_20261110100000.std")
                s2 = os.path.join(src_dir, "LOT_2ZERO.000_q_20261110120000.std")
                shutil.copy2(fp_path, s1)
                shutil.copy2(qc_path, s2)

                from apps.ftdc_checker.stdf_fetcher import copy_stdf_files_per_file
                copied_hook_paths = []
                on_file_copied, stream_ex, stream_futs = hub_frame._start_streaming_prewarm("LOT_2ZERO.000")
                self.assertEqual(stream_ex._max_workers, 2)

                def _wrapped_hook(dst_p):
                    self.assertFalse(dst_p.endswith(".part"), "Hook must fire only after .part rename!")
                    self.assertTrue(os.path.isfile(dst_p))
                    copied_hook_paths.append(dst_p)
                    on_file_copied(dst_p)

                with patch("apps.ftdc_checker.stdf_fetcher.get_local_dest_dir", return_value=dest_base):
                    copy_stdf_files_per_file([s1, s2], "LOT_2ZERO.000", on_file_copied=_wrapped_hook)

                self.assertEqual(len(copied_hook_paths), 2)
                self.assertEqual(len(stream_futs), 2)
                for fut in stream_futs:
                    fut.result(timeout=5.0)
                hub_frame._release_stream_executor(stream_ex)

                # Both files must already be in _check_summary_cache in memory!
                cached_lots = [sig[1] for sig in hub_frame._check_summary_cache.keys()]
                self.assertGreaterEqual(cached_lots.count("LOT_2ZERO.000"), 2)

                # ── 4. Help button opens PPT directly inside get_help_dir() ──
                from apps.ftdc_checker.ui import (
                    TOOLTIP_HELP_BUTTON,
                    TOOLTIP_DUPLICATE_MACID,
                    TOOLTIP_UNIQUE_VALID_GOOD,
                    PANEL_TOOLTIPS,
                )
                self.assertEqual(
                    get_help_dir(),
                    r"\\acpnetapp02\tsti\backend\data\Shared_Engineering_Directory\Automatic_FTDC_Checker",
                )
                self.assertIsNotNone(hub_frame.help_button)
                self.assertIn("Help", hub_frame.help_button.cget("text"))
                self.assertEqual(getattr(hub_frame.help_button, "tooltip_text", ""), "Click to open the App Manual.")
                self.assertEqual(TOOLTIP_HELP_BUTTON, "Click to open the App Manual.")
                self.assertEqual(InfoTooltipButton.TIP_FONT, ("Segoe UI", 8))

                # Action buttons and STDF panels must have tooltips attached
                for act_btn in (
                    hub_frame.get_ftdc_button,
                    hub_frame.get_stdf_button,
                    hub_frame.check_stdf_button,
                    hub_frame.convert_button,
                    hub_frame.clear_all_button,
                    hub_frame.add_device_button,
                    hub_frame.show_all_tests_button,
                ):
                    self.assertTrue(bool(getattr(act_btn, "tooltip_text", "")))
                for p_name in ("FIRST PASS", "RETEST", "QC"):
                    self.assertIn(p_name, hub_frame._panel_tooltip_buttons)
                    self.assertEqual(
                        hub_frame._panel_tooltip_buttons[p_name].tooltip_text,
                        PANEL_TOOLTIPS[p_name],
                    )

                help_test_dir = os.path.join(tmp_dir, "help_folder")
                os.makedirs(help_test_dir, exist_ok=True)
                dummy_ppt = os.path.join(help_test_dir, "FTDC_Guide.pptx")
                with open(dummy_ppt, "wb") as f:
                    f.write(b"PK\x03\x04dummy-pptx")

                opened_files = []
                with patch("apps.ftdc_checker.ui.get_help_dir", return_value=help_test_dir), \
                     patch("os.startfile", side_effect=lambda p: opened_files.append(p)):
                    hub_frame.open_help_presentation()
                    self.assertIsNotNone(hub_frame._help_loading_win)
                    deadline = time.time() + 3.0
                    while not opened_files and time.time() < deadline:
                        time.sleep(0.02)
                    hub_frame._close_help_loading_dialog()

                self.assertEqual(opened_files, [dummy_ppt])

                # ── 5. Tooltip buttons on main UI and Result Popup ───────────
                def _gather_tooltips(w):
                    tips = []
                    for ch in w.winfo_children():
                        if isinstance(ch, InfoTooltipButton):
                            tips.append(ch.tooltip_text)
                        tips.extend(_gather_tooltips(ch))
                    return tips

                main_tips = _gather_tooltips(hub_frame)
                self.assertIn(TOOLTIP_FP_ACTUAL_GOOD, main_tips)
                self.assertIn(TOOLTIP_TOTAL_ACTUAL_GOOD, main_tips)
                for p_tip in PANEL_TOOLTIPS.values():
                    self.assertIn(p_tip, main_tips)

                res_sg_tip = dict(res, is_sg406=True)
                hub_frame._show_result_popup(res_sg_tip)
                pop_win = hub_frame._result_window
                pop_tips = _gather_tooltips(pop_win)
                self.assertIn(TOOLTIP_FP_DUPLICATE_GOOD_UID, pop_tips)
                self.assertIn(TOOLTIP_TOTAL_DUPLICATE_GOOD_UID, pop_tips)
                self.assertIn(TOOLTIP_FP_ACTUAL_GOOD, pop_tips)
                self.assertIn(TOOLTIP_TOTAL_ACTUAL_GOOD, pop_tips)
                self.assertIn(TOOLTIP_DUPLICATE_MACID, pop_tips)
                self.assertIn(TOOLTIP_UNIQUE_VALID_GOOD, pop_tips)
                pop_win.destroy()
            finally:
                root.destroy()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_42_flexible_multi_device_macid_bt_only_zb_only_and_two_way_validation(self):
        """Verify generalized multi-device MACID checking:
        1) 2-way MACID validation (custom prefix inclusion + NaN/zeros/FF/dummy 11:22:33... exclusion).
        2) BT-only device with variable parameter counts (2 UID tests + 4 BT tests + custom prefix):
           - Dynamic label 'Duplicate MACID (BT)' with value '0' (not '0 / N/A').
           - Dynamic label 'Unique Valid Good (WXY / UID / BT)' with value '2 / 2 / 2'.
        3) ZB-only device with variable parameter counts (1 UID test + 5 ZB tests):
           - Dynamic label 'Duplicate MACID (ZB)' with value '0' (not 'N/A / 0').
           - Dynamic label 'Unique Valid Good (WXY / UID / ZB)' with value '2 / 2 / 2'.
        4) Excel output and summary sections reflect active parameters only.
        """
        import shutil
        import struct
        import tempfile
        import openpyxl
        from unittest.mock import patch
        from apps.ftdc_checker.guid_analysis import analyze_guid_data, MPC_WXY_TEST_MAP
        from apps.ftdc_checker.sg406_analysis import (
            _is_valid_macid,
            resolve_macid_device_config,
            format_duplicate_macid_row,
            format_unique_valid_good_count_row,
            get_duplicate_macid_label,
            get_unique_valid_good_label,
        )
        from apps.ftdc_checker.excel_output import build_summary_and_details, write_result_excel

        # 1. Verify 2-way MACID validation (prefix inclusion + invalid exclusion)
        self.assertTrue(_is_valid_macid("160_177_1_2", valid_prefixes=("A0:B1",)))
        self.assertFalse(_is_valid_macid("64_132_1_2", valid_prefixes=("A0:B1",)))
        self.assertFalse(_is_valid_macid("0_0_0_0", valid_prefixes=("00:00",)))  # all-zeros always excluded
        self.assertFalse(_is_valid_macid("255_255_255_255", valid_prefixes=("FF:FF",)))  # all-FF always excluded
        self.assertFalse(_is_valid_macid("17_34_51_68", valid_prefixes=("11:22",)))  # dummy 11:22:33:44 always excluded
        self.assertFalse(
            _is_valid_macid("160_177_9_9", valid_prefixes=("A0:B1",), invalid_macids={"A0:B1:09:09"})
        )

        # Helper to build STDF with arbitrary test map per part
        def _build_custom_stdf(lot_id: str, dev_name: str, parts_spec: list) -> bytes:
            out = bytearray()
            out += TestSTDFParserAndReliability._rec(0, 10, bytes([2, 4]), "<")
            mir = bytearray(struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
            mir += b" "
            for s in (lot_id, dev_name, "NODE1", "V93K", "JOB1"):
                mir += TestSTDFParserAndReliability._cn(s)
            mir += b"\x00" * 25
            out += TestSTDFParserAndReliability._rec(1, 10, bytes(mir), "<")
            out += TestSTDFParserAndReliability._rec(1, 80, bytes([1, 2, 1, 1]) + TestSTDFParserAndReliability._cn("H"), "<")
            for p in parts_spec:
                out += TestSTDFParserAndReliability._rec(5, 10, bytes([1, 1]), "<")
                tmap = dict(p["tests"])
                for tnum, val in sorted(tmap.items()):
                    ptr = bytearray(struct.pack("<IBBBBf", int(tnum), 1, 1, 0, 0, float(val)))
                    ptr += TestSTDFParserAndReliability._cn(f"T_{tnum}") + b"\x00\x00\x00\x00\x00"
                    ptr += struct.pack("<ff", -1e6, 1e6)
                    out += TestSTDFParserAndReliability._rec(15, 10, bytes(ptr), "<")
                is_p = p.get("pass", True)
                flg = 0x00 if is_p else 0x08
                hb = 1 if is_p else 5
                prr = bytearray(struct.pack("<BBBHHHhhI", 1, 1, flg, len(tmap), hb, hb, -32768, -32768, 12))
                prr += TestSTDFParserAndReliability._cn(str(p["part_id"])) + b"\x00\x00"
                out += TestSTDFParserAndReliability._rec(5, 20, bytes(prr), "<")
            good_cnt = sum(1 for p in parts_spec if p.get("pass", True))
            out += TestSTDFParserAndReliability._rec(1, 30, struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt, 0), "<")
            out += TestSTDFParserAndReliability._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
            return bytes(out)

        tmp_dir = tempfile.mkdtemp(prefix="test_flex_macid_")
        try:
            fp_path = os.path.join(tmp_dir, "fp.std")
            rt_path = os.path.join(tmp_dir, "rt.std")
            qc_path = os.path.join(tmp_dir, "qc.std")

            custom_map = dict(MPC_WXY_TEST_MAP)
            # BT-only device with 2 UID tests, 4 BT tests, and custom prefix A0:B1 (160, 177)
            custom_map["BTDEV"] = {
                "wafer": 70001,
                "x": 70002,
                "y": 70003,
                "uid": [80001, 80002],
                "bt": [81001, 81002, 81003, 81004],
                "macid_prefix": "A0:B1",
            }
            # ZB-only device with 1 UID test, 5 ZB tests, default prefix 40:84 (64, 132)
            custom_map["ZBDEV"] = {
                "wafer": 70001,
                "x": 70002,
                "y": 70003,
                "uid": 80001,
                "zb": [82001, 82002, 82003, 82004, 82005],
            }

            with patch.dict(MPC_WXY_TEST_MAP, custom_map, clear=False):
                # ── 2. Test BT-only device (PASS case) ────────────────────────
                bt_fp_parts = [
                    {
                        "part_id": "1",
                        "pass": True,
                        "tests": {
                            70001: 1, 70002: 10, 70003: 20,
                            80001: 500, 80002: 1,
                            81001: 160, 81002: 177, 81003: 10, 81004: 1,
                        },
                    },
                    {
                        "part_id": "2",
                        "pass": True,
                        "tests": {
                            70001: 1, 70002: 10, 70003: 21,
                            80001: 500, 80002: 2,
                            81001: 160, 81002: 177, 81003: 10, 81004: 2,
                        },
                    },
                ]
                bt_rt_parts = [
                    {
                        "part_id": "3",
                        "pass": True,
                        "tests": {
                            70001: 1, 70002: 10, 70003: 22,
                            80001: 500, 80002: 3,
                            81001: 160, 81002: 177, 81003: 10, 81004: 3,
                        },
                    },
                ]
                bt_qc_parts = [bt_fp_parts[0]]
                with open(fp_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_BT_ONLY.000", "BTDEV", bt_fp_parts))
                with open(rt_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_BT_ONLY.000", "BTDEV", bt_rt_parts))
                with open(qc_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_BT_ONLY.000", "BTDEV", bt_qc_parts))

                res_bt = analyze_guid_data(
                    first_pass_paths=[fp_path],
                    retest_paths=[rt_path],
                    qc_paths=[qc_path],
                    lot_id="LOT_BT_ONLY.000",
                    mpc_text="BTDEV1RTBA10",
                    first_pass_actual_good_qty="2",
                    total_actual_good_qty="3",
                    max_workers=1,
                )
                self.assertTrue(res_bt["is_sg406"])
                self.assertTrue(res_bt["has_bt"])
                self.assertFalse(res_bt["has_zb"])
                self.assertEqual(res_bt["status"], "PASS")
                self.assertEqual(res_bt["total_good_status"], "PASS")
                self.assertEqual(res_bt["duplicate_bt_macid"], 0)
                self.assertEqual(res_bt["fp_good_unique_discrepancy"], 0)
                self.assertEqual(list(res_bt["fp_good_unique_counts"].keys()), ["WXY", "UID", "BT MACID"])

                summary_bt, details_bt, _, _, _, _ = build_summary_and_details(res_bt)
                fp_sec_bt = dict(summary_bt[0][1])
                tot_sec_bt = dict(summary_bt[1][1])
                self.assertIn("Duplicate MACID (BT)", fp_sec_bt)
                self.assertNotIn("Duplicate MACID (BT / ZB)", fp_sec_bt)
                self.assertEqual(fp_sec_bt["Duplicate MACID (BT)"], "0")
                self.assertEqual(tot_sec_bt["Duplicate MACID (BT)"], "0")
                self.assertIn("Unique Valid Good (WXY / UID / BT)", fp_sec_bt)
                self.assertEqual(fp_sec_bt["Unique Valid Good (WXY / UID / BT)"], "2 / 2 / 2")
                self.assertEqual(tot_sec_bt["Unique Valid Good (WXY / UID / BT)"], "3 / 3 / 3")

                # Verify Excel output for BT-only device
                bt_xlsx = os.path.join(tmp_dir, "bt_only.xlsx")
                write_result_excel(res_bt, bt_xlsx)
                wb_bt = openpyxl.load_workbook(bt_xlsx)
                ws_bt_mac = wb_bt["Duplicate MACID Details"]
                self.assertEqual(ws_bt_mac.cell(1, 1).value, "BT MACID HEX")
                self.assertEqual(ws_bt_mac.cell(1, 2).value, "Number of UIDs per BT MACID")
                self.assertEqual(ws_bt_mac.cell(1, 3).value, "Assigned UIDS per BT MACID")

                # ── 3. Test ZB-only device (PASS case) ────────────────────────
                zb_fp_parts = [
                    {
                        "part_id": "1",
                        "pass": True,
                        "tests": {
                            70001: 2, 70002: 5, 70003: 10,
                            80001: 9001,
                            82001: 64, 82002: 132, 82003: 50, 82004: 1, 82005: 11,
                        },
                    },
                    {
                        "part_id": "2",
                        "pass": True,
                        "tests": {
                            70001: 2, 70002: 5, 70003: 11,
                            80001: 9002,
                            82001: 64, 82002: 132, 82003: 50, 82004: 1, 82005: 12,
                        },
                    },
                ]
                zb_rt_parts = [
                    {
                        "part_id": "3",
                        "pass": True,
                        "tests": {
                            70001: 2, 70002: 5, 70003: 12,
                            80001: 9003,
                            82001: 64, 82002: 132, 82003: 50, 82004: 1, 82005: 13,
                        },
                    },
                ]
                zb_qc_parts = [zb_fp_parts[0]]
                with open(fp_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_ZB_ONLY.000", "ZBDEV", zb_fp_parts))
                with open(rt_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_ZB_ONLY.000", "ZBDEV", zb_rt_parts))
                with open(qc_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_ZB_ONLY.000", "ZBDEV", zb_qc_parts))

                res_zb = analyze_guid_data(
                    first_pass_paths=[fp_path],
                    retest_paths=[rt_path],
                    qc_paths=[qc_path],
                    lot_id="LOT_ZB_ONLY.000",
                    mpc_text="ZBDEV1RTBA10",
                    first_pass_actual_good_qty="2",
                    total_actual_good_qty="3",
                    max_workers=1,
                )
                self.assertTrue(res_zb["is_sg406"])
                self.assertFalse(res_zb["has_bt"])
                self.assertTrue(res_zb["has_zb"])
                self.assertEqual(res_zb["status"], "PASS")
                self.assertEqual(list(res_zb["fp_good_unique_counts"].keys()), ["WXY", "UID", "ZB MACID"])

                summary_zb, _, _, _, _, _ = build_summary_and_details(res_zb)
                fp_sec_zb = dict(summary_zb[0][1])
                tot_sec_zb = dict(summary_zb[1][1])
                self.assertIn("Duplicate MACID (ZB)", fp_sec_zb)
                self.assertNotIn("Duplicate MACID (BT / ZB)", fp_sec_zb)
                self.assertEqual(fp_sec_zb["Duplicate MACID (ZB)"], "0")
                self.assertEqual(tot_sec_zb["Duplicate MACID (ZB)"], "0")
                self.assertIn("Unique Valid Good (WXY / UID / ZB)", fp_sec_zb)
                self.assertEqual(fp_sec_zb["Unique Valid Good (WXY / UID / ZB)"], "2 / 2 / 2")
                self.assertEqual(tot_sec_zb["Unique Valid Good (WXY / UID / ZB)"], "3 / 3 / 3")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_43_bt_zb_duplicate_macid_fail_dynamic_excel_image_and_style(self):
        """Verify:
        1) Shared resolve_summary_metric_style() across UI and image renderer.
        2) BT-only and ZB-only Duplicate MACID FAIL cases + single-pass compute_duplicate_macid_pairs.
        3) Dynamic BT/ZB column, stats, and panel log omission across all Excel sheets.
        4) Dynamic pixel-width Remarks wrapping in render_result_image().
        """
        import shutil
        import struct
        import tempfile
        import openpyxl
        from unittest.mock import patch
        from apps.ftdc_checker.guid_analysis import analyze_guid_data, MPC_WXY_TEST_MAP
        from apps.ftdc_checker.sg406_analysis import (
            compute_duplicate_macid_pairs,
            resolve_summary_metric_style,
        )
        from apps.ftdc_checker.excel_output import write_result_excel
        from apps.ftdc_checker.result_image import render_result_image

        # 1. Shared summary metric styling checks
        sample_res = {
            "duplicate_bt_macid": 0,
            "duplicate_zb_macid": 0,
            "duplicate_macid": 0,
            "fp_good_unique_discrepancy": 0,
            "total_good_unique_discrepancy": 2,
        }
        self.assertEqual(resolve_summary_metric_style("Result", "PASS", sample_res), ("PASS", "#008000"))
        self.assertEqual(resolve_summary_metric_style("Result", "FAIL", sample_res), ("FAIL", "#C00000"))
        self.assertEqual(resolve_summary_metric_style("Result", "SKIPPED", sample_res), ("SKIPPED", "#6C757D"))
        self.assertEqual(
            resolve_summary_metric_style("Difference between Good w/o Duplicate vs FP Actual Good", None, sample_res),
            ("N/A", "#6C757D"),
        )
        self.assertEqual(
            resolve_summary_metric_style("Difference between Good w/o Duplicate vs Actual Total Good", -3, sample_res, is_total=True),
            ("-3", "#C00000"),
        )
        self.assertEqual(
            resolve_summary_metric_style("Duplicate MACID (BT)", "0", sample_res),
            ("0", "#008000"),
        )
        self.assertEqual(
            resolve_summary_metric_style("Unique Valid Good (WXY / UID / BT)", "3 / 3 / 2", sample_res, is_total=True),
            ("3 / 3 / 2", "#C00000"),
        )

        # Single-pass duplicate pair computation check (BT-only)
        bt_cfg = {
            "active_macids": ("bt",),
            "macid_prefixes": {"bt": ("A0:B1",), "zb": ("40:84",)},
            "macid_invalid": (),
        }
        dup_pairs = compute_duplicate_macid_pairs(
            [
                {"uid": "101", "bt": "160_177_0_1", "zb": ""},
                {"uid": "102", "bt": "160_177_0_1", "zb": ""},
            ],
            cfg=bt_cfg,
        )
        self.assertEqual(len(dup_pairs), 1)
        self.assertTrue(dup_pairs[0]["is_dup_bt"])
        self.assertFalse(dup_pairs[0]["is_dup_zb"])
        self.assertEqual(dup_pairs[0]["num_assigned_uids"], 2)

        def _build_custom_stdf(lot_id: str, dev_name: str, parts_spec: list) -> bytes:
            out = bytearray()
            out += TestSTDFParserAndReliability._rec(0, 10, bytes([2, 4]), "<")
            mir = bytearray(struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
            mir += b" "
            for s in (lot_id, dev_name, "NODE1", "V93K", "JOB1"):
                mir += TestSTDFParserAndReliability._cn(s)
            mir += b"\x00" * 25
            out += TestSTDFParserAndReliability._rec(1, 10, bytes(mir), "<")
            out += TestSTDFParserAndReliability._rec(1, 80, bytes([1, 2, 1, 1]) + TestSTDFParserAndReliability._cn("H"), "<")
            for p in parts_spec:
                out += TestSTDFParserAndReliability._rec(5, 10, bytes([1, 1]), "<")
                tmap = dict(p["tests"])
                for tnum, val in sorted(tmap.items()):
                    ptr = bytearray(struct.pack("<IBBBBf", int(tnum), 1, 1, 0, 0, float(val)))
                    ptr += TestSTDFParserAndReliability._cn(f"T_{tnum}") + b"\x00\x00\x00\x00\x00"
                    ptr += struct.pack("<ff", -1e6, 1e6)
                    out += TestSTDFParserAndReliability._rec(15, 10, bytes(ptr), "<")
                is_p = p.get("pass", True)
                prr = bytearray(struct.pack("<BBBHHHhhI", 1, 1, 0x00 if is_p else 0x08, len(tmap), 1 if is_p else 5, 1 if is_p else 5, -32768, -32768, 12))
                prr += TestSTDFParserAndReliability._cn(str(p["part_id"])) + b"\x00\x00"
                out += TestSTDFParserAndReliability._rec(5, 20, bytes(prr), "<")
            good_cnt = sum(1 for p in parts_spec if p.get("pass", True))
            out += TestSTDFParserAndReliability._rec(1, 30, struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt, 0), "<")
            out += TestSTDFParserAndReliability._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
            return bytes(out)

        tmp_dir = tempfile.mkdtemp(prefix="test_flex_fail_")
        try:
            fp_path = os.path.join(tmp_dir, "fp.std")
            rt_path = os.path.join(tmp_dir, "rt.std")
            qc_path = os.path.join(tmp_dir, "qc.std")

            custom_map = dict(MPC_WXY_TEST_MAP)
            custom_map["BTDEV"] = {
                "wafer": 70001, "x": 70002, "y": 70003,
                "uid": [80001, 80002],
                "bt": [81001, 81002, 81003, 81004],
                "macid_prefix": "A0:B1",
            }
            custom_map["ZBDEV"] = {
                "wafer": 70001, "x": 70002, "y": 70003,
                "uid": 80001,
                "zb": [82001, 82002, 82003, 82004, 82005],
            }

            with patch.dict(MPC_WXY_TEST_MAP, custom_map, clear=False):
                # 2. BT-only Duplicate MACID FAIL case
                bt_logs: list = []
                bt_fp_parts = [
                    {"part_id": "1", "pass": True, "tests": {70001: 1, 70002: 10, 70003: 1, 80001: 500, 80002: 1, 81001: 160, 81002: 177, 81003: 1, 81004: 99}},
                    {"part_id": "2", "pass": True, "tests": {70001: 1, 70002: 10, 70003: 2, 80001: 500, 80002: 2, 81001: 160, 81002: 177, 81003: 1, 81004: 99}},
                ]
                bt_qc_parts = [bt_fp_parts[0]]
                with open(fp_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_BT_FAIL.000", "BTDEV", bt_fp_parts))
                with open(rt_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_BT_FAIL.000", "BTDEV", []))
                with open(qc_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_BT_FAIL.000", "BTDEV", bt_qc_parts))

                res_bt = analyze_guid_data(
                    first_pass_paths=[fp_path], retest_paths=[rt_path], qc_paths=[qc_path],
                    lot_id="LOT_BT_FAIL.000", mpc_text="BTDEV1RTBA10",
                    first_pass_actual_good_qty="2", total_actual_good_qty="2",
                    logger=bt_logs.append, max_workers=1,
                )
                self.assertEqual(res_bt["status"], "FAIL")
                self.assertEqual(res_bt["duplicate_bt_macid"], 1)
                self.assertEqual(res_bt["duplicate_zb_macid"], 0)
                self.assertIn("Duplicate BT MACID (1)", res_bt["remarks"])

                bt_log_joined = "\n".join(bt_logs)
                self.assertIn("missing BT=", bt_log_joined)
                self.assertNotIn("missing ZB=", bt_log_joined)

                bt_xlsx = os.path.join(tmp_dir, "bt_fail.xlsx")
                write_result_excel(res_bt, bt_xlsx)
                wb_bt = openpyxl.load_workbook(bt_xlsx)

                ws_ft = wb_bt["FT Total"]
                ft_stat_metrics = [ws_ft.cell(r, 1).value for r in range(2, 12)] + [ws_ft.cell(r, 4).value for r in range(2, 12)]
                self.assertIn("Duplicate BT MACID", ft_stat_metrics)
                self.assertNotIn("Duplicate ZB MACID", ft_stat_metrics)
                self.assertIn("FIRST PASS No BT MACID", ft_stat_metrics)
                self.assertNotIn("FIRST PASS No ZB MACID", ft_stat_metrics)
                ft_hdr_row = next(r for r in range(1, 20) if ws_ft.cell(r, 1).value == "Total Parts")
                ft_hdrs = [ws_ft.cell(ft_hdr_row, c).value for c in range(1, ws_ft.max_column + 1) if ws_ft.cell(ft_hdr_row, c).value]
                self.assertIn("BT MACID", ft_hdrs)
                self.assertNotIn("ZB MACID", ft_hdrs)
                self.assertNotIn("ZB MACID HEX", ft_hdrs)

                ws_qc = wb_bt["QC Total"]
                qc_hdr_row = next(r for r in range(1, 20) if ws_qc.cell(r, 1).value == "Total Parts")
                qc_hdrs = [ws_qc.cell(qc_hdr_row, c).value for c in range(1, ws_qc.max_column + 1) if ws_qc.cell(qc_hdr_row, c).value]
                self.assertIn("BT MACID", qc_hdrs)
                self.assertNotIn("ZB MACID", qc_hdrs)

                ws_gu = wb_bt["Valid Good Count Cross-Check"]
                gu_hdr_row = next(r for r in range(1, 30) if ws_gu.cell(r, 1).value == "Good Part #")
                gu_hdrs = [ws_gu.cell(gu_hdr_row, c).value for c in range(1, ws_gu.max_column + 1) if ws_gu.cell(gu_hdr_row, c).value]
                self.assertIn("Valid BT MACID", gu_hdrs)
                self.assertIn("Unique Good BT MACID", gu_hdrs)
                self.assertNotIn("ZB MACID", gu_hdrs)
                self.assertNotIn("Valid ZB MACID", gu_hdrs)
                self.assertNotIn("Unique Good ZB MACID", gu_hdrs)

                # Result image renders cleanly with long Remarks wrapped dynamically
                img_path = os.path.join(tmp_dir, "bt_fail.png")
                res_bt_long = dict(res_bt)
                res_bt_long["remarks"] = (res_bt["remarks"] + " ") * 4
                self.assertTrue(render_result_image(res_bt_long, img_path))
                self.assertTrue(os.path.isfile(img_path) and os.path.getsize(img_path) > 500)

                # 3. ZB-only Duplicate MACID FAIL case
                zb_logs: list = []
                zb_fp_parts = [
                    {"part_id": "1", "pass": True, "tests": {70001: 2, 70002: 5, 70003: 10, 80001: 9001, 82001: 64, 82002: 132, 82003: 50, 82004: 1, 82005: 77}},
                    {"part_id": "2", "pass": True, "tests": {70001: 2, 70002: 5, 70003: 11, 80001: 9002, 82001: 64, 82002: 132, 82003: 50, 82004: 1, 82005: 77}},
                ]
                zb_qc_parts = [zb_fp_parts[0]]
                with open(fp_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_ZB_FAIL.000", "ZBDEV", zb_fp_parts))
                with open(rt_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_ZB_FAIL.000", "ZBDEV", []))
                with open(qc_path, "wb") as f:
                    f.write(_build_custom_stdf("LOT_ZB_FAIL.000", "ZBDEV", zb_qc_parts))

                res_zb = analyze_guid_data(
                    first_pass_paths=[fp_path], retest_paths=[rt_path], qc_paths=[qc_path],
                    lot_id="LOT_ZB_FAIL.000", mpc_text="ZBDEV1RTBA10",
                    first_pass_actual_good_qty="2", total_actual_good_qty="2",
                    logger=zb_logs.append, max_workers=1,
                )
                self.assertEqual(res_zb["status"], "FAIL")
                self.assertEqual(res_zb["duplicate_zb_macid"], 1)
                self.assertIn("Duplicate ZB MACID (1)", res_zb["remarks"])

                zb_log_joined = "\n".join(zb_logs)
                self.assertIn("missing ZB=", zb_log_joined)
                self.assertNotIn("missing BT=", zb_log_joined)

                zb_xlsx = os.path.join(tmp_dir, "zb_fail.xlsx")
                write_result_excel(res_zb, zb_xlsx)
                wb_zb = openpyxl.load_workbook(zb_xlsx)
                ws_ft_z = wb_zb["FT Total"]
                ft_hdr_z_row = next(r for r in range(1, 20) if ws_ft_z.cell(r, 1).value == "Total Parts")
                ft_hdrs_z = [ws_ft_z.cell(ft_hdr_z_row, c).value for c in range(1, ws_ft_z.max_column + 1) if ws_ft_z.cell(ft_hdr_z_row, c).value]
                self.assertIn("ZB MACID", ft_hdrs_z)
                self.assertNotIn("BT MACID", ft_hdrs_z)
                self.assertNotIn("BT MACID HEX", ft_hdrs_z)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_44_custom_conventions_load_and_complete_cache_clearing(self):
        """Verify:
        1) Device Settings Manager _populate_form_for_mpc loads existing custom STDF filename conventions.
        2) Clear All button clears ALL caches (memory, Device Settings, module caches, temp files, disk Check STDF cache)
           and invalidates stale background disk cache writes.
        3) save_session_log_on_close() clears ALL caches (even if session log has no lines / already saved).
        """
        import shutil
        import tempfile
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from apps.ftdc_checker.ui import FTDCCheckerFrame
        from apps.ftdc_checker import stdf_fetcher as sf
        from apps.ftdc_checker import guid_analysis as ga
        from apps.ftdc_checker import stdf_parser as sp
        from apps.ftdc_checker import sg406_analysis as sg
        from core import config as hub_config

        root = tk.Tk()
        root.withdraw()
        tmp_dir = tempfile.mkdtemp(prefix="test_cache_clear_")
        try:
            mpc_json = os.path.join(tmp_dir, "mpc_partnumber.json")
            wxy_json = os.path.join(tmp_dir, "mask_wxy_map.json")
            conv_json = os.path.join(tmp_dir, "stdf_filename_conventions.json")
            disk_cache_json = os.path.join(tmp_dir, "check_stdf_cache.json")
            shutil.copy2(sf._MPC_PARTNUMBER_JSON_PATH, mpc_json)
            shutil.copy2(ga._MASK_WXY_JSON_PATH, wxy_json)
            shutil.copy2(sf._get_json_path(sf._CONVENTIONS_JSON_NAME), conv_json)

            with (
                patch.object(sf, "_MPC_PARTNUMBER_JSON_PATH", mpc_json),
                patch.object(ga, "_MASK_WXY_JSON_PATH", wxy_json),
                patch.object(sf, "_get_json_path", return_value=conv_json),
            ):
                sf.reload_config()
                ga.reload_wxy_config()

                custom_conv = {
                    "FIRST PASS": ["cust_fp_{lot}"],
                    "RETEST": ["cust_rt_{lot}"],
                    "QC": ["cust_qc_{lot}"],
                    "QC RETEST": ["cust_qcrt_{lot}"],
                }
                sf.save_device_configuration(
                    mpc="ZZ888QRTBA01",
                    devices="CUSTDEV*",
                    testers=["V93K"],
                    wxy_tests={"wafer": 1001, "x": 1002, "y": 1003},
                    custom_conventions=custom_conv,
                )

                ftdc = FTDCCheckerFrame(root)
                ftdc._session_log_saved = True  # do not write into real logs/ during Clear All phase
                ftdc._check_cache_file_path = lambda: disk_cache_json

                # 1. Verify Device Settings loads custom conventions instead of resetting to template
                ftdc.mpc_var.set("ZZ888QRTBA01")
                ftdc.open_device_settings_dialog()
                root.update()
                ds_win = ftdc._device_settings_win
                self.assertIsNotNone(ds_win)

                def _walk(w):
                    yield w
                    for c in w.winfo_children():
                        yield from _walk(c)

                tree = next(w for w in _walk(ds_win) if isinstance(w, ttk.Treeview))
                self.assertTrue(tree.exists("ZZ888QRTBA01"))
                tree.selection_set("ZZ888QRTBA01")
                tree.event_generate("<<TreeviewSelect>>")
                root.after(40, root.quit)
                root.mainloop()

                # Collect ModernEntry values and states (using Entry.get())
                conv_vals = []
                for w in _walk(ds_win):
                    if w.__class__.__name__ == "ModernEntry":
                        conv_vals.append((w.get(), str(w.cget("state"))))
                for expected_pat in ("cust_fp_{lot}", "cust_rt_{lot}", "cust_qc_{lot}", "cust_qcrt_{lot}"):
                    self.assertIn((expected_pat, "normal"), conv_vals)
                ds_win.withdraw()

                # 2. Populate all caches & temp artifacts and verify Clear All wipes everything
                def _populate_caches():
                    tmp_xl = os.path.join(tmp_dir, "tmp_out.xlsx")
                    tmp_img = os.path.join(tmp_dir, "tmp_out.png")
                    with open(tmp_xl, "wb") as fx:
                        fx.write(b"xlsx")
                    with open(tmp_img, "wb") as fi:
                        fi.write(b"png")
                    ftdc._test_scan_cache[("a.std", 1, 2)] = [{"num": 1}]
                    ftdc._analysis_cache[("k",)] = {"status": "PASS"}
                    ftdc._cached_tests = [{"num": 1}]
                    ftdc._mpc_cache["LOT1"] = "MPC1"
                    ftdc._ftdc_fail_cache["LOT1"] = [("m", "c", "t")]
                    ftdc._ftdc_fail_threads.add("LOT1") if isinstance(ftdc._ftdc_fail_threads, set) else ftdc._ftdc_fail_threads.update({"LOT1": object()})
                    with ftdc._check_cache_lock:
                        ftdc._check_summary_cache[(("f.std", 10, 20), "LOT1")] = ("Completed", "pass", "1", "1", "None")
                    ftdc._save_check_cache_to_disk()
                    with ftdc._excel_lock:
                        ftdc._excel_temp_files.update({tmp_xl, tmp_img})
                        ftdc._excel_temp_path = tmp_xl
                        ftdc._image_temp_path = tmp_img
                    with ftdc._ds_cache_lock:
                        ftdc._ds_cache_ready = True
                    sp._DRIVE_REMOTE_CACHE["X:"] = True
                    sg._is_valid_macid("40:84:00:00:00:01", valid_prefixes=("40:84",))
                    return tmp_xl, tmp_img

                xl_p, img_p = _populate_caches()
                old_gen = ftdc._check_cache_generation
                self.assertTrue(os.path.isfile(disk_cache_json))

                ftdc.clear_all()

                self.assertEqual(ftdc._test_scan_cache, {})
                self.assertEqual(ftdc._analysis_cache, {})
                self.assertEqual(ftdc._cached_tests, [])
                self.assertEqual(ftdc._mpc_cache, {})
                self.assertEqual(ftdc._ftdc_fail_cache, {})
                self.assertEqual(len(ftdc._ftdc_fail_threads), 0)
                self.assertEqual(ftdc._check_summary_cache, {})
                self.assertFalse(ftdc._ds_cache_ready)
                self.assertEqual(ftdc._ds_cached_rows, [])
                self.assertEqual(ftdc._ds_cached_by_mpc, {})
                self.assertIsNone(ftdc._excel_temp_path)
                self.assertIsNone(ftdc._image_temp_path)
                self.assertFalse(os.path.exists(xl_p))
                self.assertFalse(os.path.exists(img_p))
                self.assertFalse(os.path.exists(disk_cache_json))
                self.assertEqual(sp._DRIVE_REMOTE_CACHE, {})
                self.assertEqual(sg._compile_macid_rules.cache_info().currsize, 0)

                # Stale async disk cache write must not resurrect deleted cache
                ftdc._save_check_cache_to_disk(expected_gen=old_gen)
                self.assertFalse(os.path.exists(disk_cache_json))

                # 3. Verify save_session_log_on_close() clears ALL caches even when session log has no lines
                xl_p2, img_p2 = _populate_caches()
                ftdc._session_log_lines.clear()
                ftdc._session_log_saved = False
                self.assertTrue(os.path.isfile(disk_cache_json))

                ftdc.save_session_log_on_close()

                self.assertEqual(ftdc._test_scan_cache, {})
                self.assertEqual(ftdc._analysis_cache, {})
                self.assertEqual(ftdc._mpc_cache, {})
                self.assertEqual(ftdc._ftdc_fail_cache, {})
                self.assertEqual(ftdc._check_summary_cache, {})
                self.assertFalse(ftdc._ds_cache_ready)
                self.assertFalse(os.path.exists(xl_p2))
                self.assertFalse(os.path.exists(img_p2))
                self.assertFalse(os.path.exists(disk_cache_json))
                self.assertIsNone(hub_config._SETTINGS_CACHE)
                self.assertEqual(sf._load_mpc_partnumber_file.cache_info().currsize, 0)
                self.assertEqual(sf._load_stdf_filename_conventions_file.cache_info().currsize, 0)
                hub_config.reload_settings()
        finally:
            try:
                root.destroy()
            except Exception:
                pass
            sf.reload_config()
            ga.reload_wxy_config()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_46_ppo_auto_attach_status_visibility_and_race_guards(self):
        """Verify:
        1) Status bar clearly shows 'On-going' while preparing/attaching/retrying PPO and 'Done' / 'Failed' on completion.
        2) Re-calling _start_background_excel_generation while IN_PROGRESS does not prematurely set _excel_state='READY'.
        3) Stale _set_status_async updates from a cleared generation do not overwrite 'Ready' after clear_all().
        """
        import tempfile
        import threading
        import time
        import tkinter as tk
        from unittest import mock
        from apps.ftdc_checker import ui as ftdc_ui
        from apps.ftdc_checker.ui import FTDCCheckerFrame

        tmp_dir = tempfile.mkdtemp()
        try:
            ppo_base = os.path.join(tmp_dir, "PPO")
            local_base = os.path.join(tmp_dir, "LOCAL")
            sub_a = os.path.join(ppo_base, "59793", "MMT-272900204.000", "OP1")
            sub_b = os.path.join(ppo_base, "59793", "MMT-272900204.000", "OP2")
            os.makedirs(sub_a)
            os.makedirs(sub_b)

            root = tk.Tk()
            root.withdraw()
            try:
                ftdc = FTDCCheckerFrame(root)
                ftdc.PPO_ATTACH_RETRY_DELAYS = (0.0, 0.0)
                ftdc._queue_ppo_notice = lambda *args, **kwargs: None

                seen_statuses = []

                def _capture_status():
                    time.sleep(0.06)
                    root.update()
                    seen_statuses.append(ftdc.status_var.get())

                # 1. Track live On-going -> Done status during background generation + PPO attach
                gate = threading.Event()
                started = threading.Event()

                def _slow_write_excel(_result, out_path, progress_callback=None):
                    if progress_callback:
                        progress_callback(0.50, "Writing rows…")
                    started.set()
                    gate.wait(timeout=3.0)
                    with open(out_path, "wb") as fh:
                        fh.write(b"PK-dummy-xlsx")
                    if progress_callback:
                        progress_callback(1.0, "Excel workbook generated")

                res = {
                    "lot_id": "MMT-272900204.000",
                    "mir_lot_id": "MMT-272900204.000",
                    "mpc": "59793QRTBC01",
                    "status": "PASS",
                    "total_good_status": "PASS",
                    "first_pass_pass_count": 10,
                    "first_pass_actual_good": 10,
                    "retest_pass_count": 0,
                    "qc_good_prev_op_count": 5,
                    "qc_failed_or_missing_prev_op_qty": 0,
                    "first_pass": {"rows": []},
                    "qc": {"rows": []},
                }

                with mock.patch.object(ftdc_ui, "is_developer_mode", return_value=False), \
                     mock.patch.object(ftdc_ui, "write_result_excel", side_effect=_slow_write_excel) as mock_write_excel, \
                     mock.patch.object(ftdc_ui, "get_ppo_base_path", return_value=ppo_base), \
                     mock.patch.object(ftdc_ui, "get_local_dest_base", return_value=local_base):
                    ftdc._start_background_excel_generation(res, auto_attach=True)
                    self.assertTrue(started.wait(timeout=2.0))
                    ftdc._flush_pending_progress()
                    ftdc._flush_pending_copy_stats()
                    # Stage 1 (Excel generation) has its own 0-100% progress bar & ETA
                    self.assertEqual(ftdc.progress_text_var.get().strip(), "50.0%")
                    self.assertIn("Generating Excel | ETA", ftdc.copy_stats_var.get())
                    # User is forced to wait while auto-attach PPO is in progress (all main UI buttons disabled)
                    self.assertTrue(ftdc.is_running)
                    self.assertEqual(str(ftdc.clear_all_button["state"]), "disabled")
                    self.assertFalse(ftdc._excel_ready_event.is_set())
                    _capture_status()
                    self.assertIn("Auto-attach to PPO: On-going", seen_statuses[-1])

                    # Calling a second time (like _deliver_analysis_result does) must NOT flip READY prematurely
                    ftdc._start_background_excel_generation(res, auto_attach=True)
                    self.assertEqual(ftdc._excel_state, "IN_PROGRESS")

                    gate.set()
                    deadline = time.monotonic() + 5.0
                    while ftdc._excel_bg_thread.is_alive() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    _capture_status()
                    self.assertFalse(ftdc._excel_bg_thread.is_alive())
                    self.assertFalse(ftdc.is_running)
                    self.assertEqual(str(ftdc.clear_all_button["state"]), "normal")
                    self.assertTrue(ftdc._excel_ready_event.is_set())
                    self.assertEqual(ftdc._excel_state, "READY")
                    self.assertEqual(ftdc._ppo_attach_state, "SUCCESS")
                    self.assertIn("Analysis completed: FP PASS | Total Good PASS", seen_statuses[-1])
                    self.assertIn("Auto-attach to PPO: Done (2 subfolder(s))", seen_statuses[-1])
                    # Verify main progress bar and Done X/N | MB/s | ETA telemetry
                    self.assertEqual(ftdc.progress_text_var.get().strip(), "100.0%")
                    stats_text = ftdc.copy_stats_var.get()
                    self.assertIn("Done 4/4 |", stats_text)
                    self.assertIn("MB/s | ETA", stats_text)
                    self.assertEqual(mock_write_excel.call_count, 1)

                    # Verify Cache-Hit Re-Attach: if prior attach had not succeeded (_ppo_attached_paths cleared),
                    # re-calling _start_background_excel_generation re-attaches without regenerating Excel
                    res.pop("_ppo_attached_paths", None)
                    ftdc._ppo_attach_state = "FAILED"
                    ftdc._start_background_excel_generation(res, auto_attach=True)
                    deadline = time.monotonic() + 5.0
                    while ftdc._excel_bg_thread.is_alive() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    _capture_status()
                    self.assertEqual(mock_write_excel.call_count, 1)  # Excel NOT regenerated!
                    self.assertEqual(ftdc._ppo_attach_state, "SUCCESS")
                    self.assertFalse(ftdc.is_running)
                    self.assertTrue(bool(res.get("_ppo_attached_paths")))

                # 2. Failed PPO (missing MPC) reports Failed in status
                bad_mpc_res = {"lot_id": "MMT-272900204.000", "status": "FAIL", "total_good_status": "PASS"}
                src_xlsx = os.path.join(tmp_dir, "src.xlsx")
                with open(src_xlsx, "wb") as fh:
                    fh.write(b"PK-dummy")
                with mock.patch.object(ftdc_ui, "is_developer_mode", return_value=False):
                    ftdc._auto_attach_excel_to_ppo(bad_mpc_res, src_xlsx, "a.xlsx", ftdc._excel_generation_id)
                _capture_status()
                self.assertIn("Auto-attach to PPO: Failed (missing MPC/Lot ID)", seen_statuses[-1])

                # 3. Stale _set_status_async queued before clear_all() must NOT overwrite 'Ready'
                old_gen = ftdc._excel_generation_id
                ftdc._set_status_async("Auto-attach to PPO: Done (99 subfolder(s))", gen_id=old_gen)
                ftdc.clear_all()
                root.update()
                self.assertEqual(ftdc.status_var.get(), "Ready")
            finally:
                root.destroy()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_frozen_and_nuitka_app_root_resolution(self):
        """Verify external assets beside the executable always win across PyInstaller & Nuitka modes."""
        import tempfile
        from types import SimpleNamespace
        from core import config as hub_config

        tmp_dir = tempfile.mkdtemp(prefix="pts_hub_frozen_test_")
        try:
            exe_dir = os.path.join(tmp_dir, "deploy_pts_hub")
            bundle_dir = os.path.join(exe_dir, "_internal")
            os.makedirs(os.path.join(exe_dir, "assets"), exist_ok=True)
            os.makedirs(os.path.join(bundle_dir, "assets"), exist_ok=True)

            ext_cfg = os.path.join(exe_dir, "assets", "settings.json")
            int_cfg = os.path.join(bundle_dir, "assets", "settings.json")
            bundle_only = os.path.join(bundle_dir, "assets", "fallback_only.json")
            for p in (ext_cfg, int_cfg, bundle_only):
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write("{}")

            exe_path = os.path.join(exe_dir, "PTS_Hub.exe")

            # 1. Source mode (unfrozen)
            self.assertFalse(hub_config.is_frozen_app())
            self.assertEqual(os.path.abspath(hub_config.get_app_root()), os.path.abspath(hub_config.BUNDLE_ROOT))

            # 2. PyInstaller --onedir (_internal bundle fallback, external overrides)
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", exe_path), \
                 patch.object(sys, "argv", [exe_path]), \
                 patch.object(hub_config, "BUNDLE_ROOT", bundle_dir):
                self.assertTrue(hub_config.is_frozen_app())
                self.assertEqual(os.path.abspath(hub_config.get_app_root()), os.path.abspath(exe_dir))
                self.assertEqual(os.path.abspath(FTDCCheckerFrame._app_base_dir()), os.path.abspath(exe_dir))
                self.assertEqual(os.path.abspath(hub_config.get_asset_path("settings.json")), os.path.abspath(ext_cfg))
                self.assertEqual(os.path.abspath(hub_config.get_asset_path("fallback_only.json")), os.path.abspath(bundle_only))
                # Non-existent file must still default to external assets dir (not _internal)
                missing_target = hub_config.get_asset_path("new_device.json")
                self.assertEqual(os.path.abspath(missing_target), os.path.abspath(os.path.join(exe_dir, "assets", "new_device.json")))

            # 3. Nuitka --standalone (__compiled__.containing_dir is parent dir; exe_dir comes from sys.executable)
            nuitka_standalone = SimpleNamespace(standalone=True, onefile=False, containing_dir=tmp_dir)
            with patch.dict(hub_config.__dict__, {"__compiled__": nuitka_standalone}), \
                 patch.object(sys, "executable", exe_path), \
                 patch.object(sys, "argv", [exe_path]), \
                 patch.object(hub_config, "BUNDLE_ROOT", bundle_dir):
                self.assertTrue(hub_config.is_frozen_app())
                self.assertEqual(os.path.abspath(hub_config.get_app_root()), os.path.abspath(exe_dir))
                self.assertEqual(os.path.abspath(hub_config.get_asset_path("settings.json")), os.path.abspath(ext_cfg))

            # 4. Nuitka --onefile (sys.executable is in temp; __compiled__.containing_dir is outer exe folder)
            temp_exe = os.path.join(tmp_dir, "temp_extract", "PTS_Hub.exe")
            nuitka_onefile = SimpleNamespace(standalone=True, onefile=True, containing_dir=exe_dir)
            with patch.dict(hub_config.__dict__, {"__compiled__": nuitka_onefile}), \
                 patch.object(sys, "executable", temp_exe), \
                 patch.object(sys, "argv", [temp_exe]), \
                 patch.object(hub_config, "BUNDLE_ROOT", os.path.join(tmp_dir, "temp_extract")):
                self.assertTrue(hub_config.is_frozen_app())
                self.assertEqual(os.path.abspath(hub_config.get_app_root()), os.path.abspath(exe_dir))
                self.assertEqual(os.path.abspath(hub_config.get_asset_path("settings.json")), os.path.abspath(ext_cfg))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_48_targeted_ppo_disabled_and_ppo_attach_timeout(self):
        """Verify:
        1) Targeted PPO# attachment is disabled (ENABLE_TARGETED_PPO_ATTACH is False),
           no 'PPO#' UI label exists, blank ppo_var is allowed, normalization handlers are no-ops,
           and _get_effective_ppo_num() returns ''.
        2) Auto-attach copies Excel (.xlsx) and snapshot (.png) to ALL existing PPO subfolders
           without creating a targeted PPO subfolder.
        3) get_ppo_attach_timeout_sec() loads the configured 60s timeout from settings.json.
        4) PPO attachment aborts promptly on slow file copy or slow directory scan timeouts,
           marks state FAILED with 'Timed out', saves local fallback copies, and unlocks is_running.
        """
        import tempfile
        import time
        import tkinter as tk
        from unittest import mock
        from core.config import get_ppo_attach_timeout_sec, reload_settings
        from apps.ftdc_checker import ui as ftdc_ui
        from apps.ftdc_checker.ui import FTDCCheckerFrame

        reload_settings()
        self.assertEqual(get_ppo_attach_timeout_sec(), 60.0)

        tmp_dir = tempfile.mkdtemp(prefix="pts_hub_ppo_disabled_")
        try:
            ppo_base = os.path.join(tmp_dir, "PPO")
            local_base = os.path.join(tmp_dir, "LOCAL")
            lot_dir = os.path.join(ppo_base, "59793", "MMT-272900204.000")
            ppo_100 = os.path.join(lot_dir, "PPO100")
            ppo_200 = os.path.join(lot_dir, "PPO200")
            ppo_300 = os.path.join(lot_dir, "PPO300")
            os.makedirs(ppo_100)
            os.makedirs(ppo_200)
            os.makedirs(ppo_300)

            root = tk.Tk()
            root.withdraw()
            try:
                ftdc = FTDCCheckerFrame(root)
                ftdc.PPO_ATTACH_RETRY_DELAYS = (0.0, 0.0)
                ftdc._queue_ppo_notice = lambda *args, **kwargs: None

                def _iter_widgets(w):
                    yield w
                    for child in w.winfo_children():
                        yield from _iter_widgets(child)

                # 1. Targeted PPO# disabled in UI & helpers
                self.assertFalse(FTDCCheckerFrame.ENABLE_TARGETED_PPO_ATTACH)
                ppo_labels = [
                    w for w in _iter_widgets(ftdc)
                    if isinstance(w, tk.Label) and w.cget("text") == "PPO#"
                ]
                self.assertEqual(ppo_labels, [])

                for p in ftdc.PANELS:
                    ftdc.panel_files[p] = [f"C:\\test\\{p}.std"]
                ftdc.lot_id_var.set("MMT-272900204.000")
                ftdc.mpc_var.set("59793QRTBC01")
                ftdc.ppo_var.set("")
                ftdc.fp_actual_good_qty_var.set("12.5")  # fails int validation only after required check passes
                ftdc.total_actual_good_qty_var.set("10")
                with mock.patch("tkinter.messagebox.showerror") as mock_err:
                    ftdc.start_get_data()
                    self.assertTrue(mock_err.called)
                    title, _msg = mock_err.call_args[0]
                    self.assertEqual(title, "Invalid Integer Input")

                ftdc.ppo_var.set("  ppo200  ")
                self.assertEqual(ftdc._normalize_ppo_var(), "")
                ftdc._on_ppo_focus_out()
                self.assertEqual(ftdc.ppo_var.get(), "  ppo200  ")
                self.assertEqual(ftdc._get_effective_ppo_num({"ppo_num": "PPO200"}), "")

                # 2. Stage 1 + Stage 2 attaches BOTH Excel and PNG to ALL PPO subfolders
                def _dummy_write_excel(_result, out_path, progress_callback=None):
                    with open(out_path, "wb") as fh:
                        fh.write(b"PK-dummy-xlsx")

                def _dummy_render_img(_result, out_path, **_kwargs):
                    with open(out_path, "wb") as fh:
                        fh.write(b"\x89PNG\r\n\x1a\ndummy")

                res = {
                    "lot_id": "MMT-272900204.000",
                    "mir_lot_id": "MMT-272900204.000",
                    "mpc": "59793QRTBC01",
                    "ppo_num": "  ppo200  ",
                    "status": "PASS",
                    "total_good_status": "PASS",
                    "first_pass_pass_count": 10,
                    "first_pass_actual_good": 10,
                    "retest_pass_count": 0,
                    "qc_good_prev_op_count": 5,
                    "qc_failed_or_missing_prev_op_qty": 0,
                    "first_pass": {"rows": []},
                    "qc": {"rows": []},
                }

                def _wait_bg_and_ui(timeout_s: float = 5.0):
                    dl = time.monotonic() + timeout_s
                    while (
                        ftdc._excel_bg_thread.is_alive()
                        or ftdc.is_running
                        or ftdc._pending_running_state is not None
                        or ftdc._pending_ppo_status is not None
                        or bool(ftdc._pending_log_messages)
                    ) and time.monotonic() < dl:
                        root.update()
                        time.sleep(0.01)
                    root.update()

                with mock.patch.object(ftdc_ui, "is_developer_mode", return_value=False), \
                     mock.patch.object(ftdc_ui, "write_result_excel", side_effect=_dummy_write_excel) as mock_xl, \
                     mock.patch.object(ftdc_ui, "render_result_image", side_effect=_dummy_render_img), \
                     mock.patch.object(ftdc_ui, "get_ppo_base_path", return_value=ppo_base), \
                     mock.patch.object(ftdc_ui, "get_local_dest_base", return_value=local_base):
                    ftdc._start_background_excel_generation(res, auto_attach=True)
                    _wait_bg_and_ui(5.0)

                    self.assertFalse(ftdc.is_running)
                    self.assertEqual(ftdc._ppo_attach_state, "SUCCESS")
                    self.assertEqual(res.get("ppo_num"), "")
                    self.assertEqual(res.get("_ppo_attached_ppo_num"), "")
                    xl_name = res["_cached_excel_name"]
                    img_name = res["_cached_image_name"]
                    for sub in (ppo_100, ppo_200, ppo_300):
                        self.assertTrue(os.path.isfile(os.path.join(sub, xl_name)), f"Missing {xl_name} in {sub}")
                        self.assertTrue(os.path.isfile(os.path.join(sub, img_name)), f"Missing {img_name} in {sub}")
                    self.assertEqual(mock_xl.call_count, 1)

                    # Setting non-existent PPO# on result still attaches to ALL existing folders & creates none
                    direct_res = dict(res, ppo_num="  ppo999_new  ")
                    direct_res.pop("_ppo_attached_paths", None)
                    ftdc._auto_attach_excel_to_ppo(
                        direct_res,
                        res["_cached_excel_path"],
                        "DIRECT.xlsx",
                        ftdc._excel_generation_id,
                        extra_attachments=[(res["_cached_image_path"], "DIRECT.png")],
                    )
                    self.assertEqual(ftdc._ppo_attach_state, "SUCCESS")
                    self.assertFalse(os.path.exists(os.path.join(lot_dir, "PPO999_NEW")))
                    for sub in (ppo_100, ppo_200, ppo_300):
                        self.assertTrue(os.path.isfile(os.path.join(sub, "DIRECT.xlsx")))
                        self.assertTrue(os.path.isfile(os.path.join(sub, "DIRECT.png")))

                    # 3. Timeout during slow SMB copy
                    def _slow_copy(_src, dst, **_kwargs):
                        ce = _kwargs.get("cancel_event")
                        if ce is not None:
                            ce.wait(timeout=2.0)
                        else:
                            time.sleep(0.5)
                        raise TimeoutError(f"Simulated slow copy timeout for {dst}")

                    ftdc.PPO_ATTACH_TIMEOUT_SEC = 0.25
                    res_timeout_copy = dict(res)
                    for k in ("_cached_excel_ready", "_cached_excel_path", "_cached_image_path", "_ppo_attached_paths"):
                        res_timeout_copy.pop(k, None)

                    t_start = time.monotonic()
                    with mock.patch.object(ftdc_ui, "_fast_copy_file_with_progress", side_effect=_slow_copy):
                        ftdc._start_background_excel_generation(res_timeout_copy, auto_attach=True)
                        _wait_bg_and_ui(4.0)
                    elapsed_copy = time.monotonic() - t_start

                    self.assertLess(elapsed_copy, 3.0)
                    self.assertFalse(ftdc.is_running)
                    self.assertEqual(ftdc._ppo_attach_state, "FAILED")
                    self.assertIn("Timed out", ftdc.status_var.get())
                    self.assertIn("Timed out", ftdc.log_text.get("1.0", "end"))
                    local_lot_dir = os.path.join(local_base, "MMT-272900204.000")
                    self.assertTrue(os.path.isfile(os.path.join(local_lot_dir, res_timeout_copy["_cached_excel_name"])))
                    self.assertTrue(os.path.isfile(os.path.join(local_lot_dir, res_timeout_copy["_cached_image_name"])))

                    # 4. Timeout during slow PPO directory scan
                    def _slow_scan(*_args, **_kwargs):
                        time.sleep(0.6)
                        return [ppo_100]

                    ftdc.PPO_ATTACH_TIMEOUT_SEC = 0.2
                    res_timeout_scan = dict(res)
                    for k in ("_cached_excel_ready", "_cached_excel_path", "_cached_image_path", "_ppo_attached_paths"):
                        res_timeout_scan.pop(k, None)

                    t_start = time.monotonic()
                    with mock.patch.object(ftdc, "_resolve_ppo_target_subfolders", side_effect=_slow_scan):
                        ftdc._start_background_excel_generation(res_timeout_scan, auto_attach=True)
                        _wait_bg_and_ui(4.0)
                    elapsed_scan = time.monotonic() - t_start

                    self.assertLess(elapsed_scan, 3.0)
                    self.assertFalse(ftdc.is_running)
                    self.assertEqual(ftdc._ppo_attach_state, "FAILED")
                    self.assertIn("Timed out", ftdc.status_var.get())
                    self.assertIn("Timed out", ftdc.log_text.get("1.0", "end"))
                    self.assertTrue(os.path.isfile(os.path.join(local_lot_dir, res_timeout_scan["_cached_excel_name"])))
                    self.assertTrue(os.path.isfile(os.path.join(local_lot_dir, res_timeout_scan["_cached_image_name"])))
            finally:
                ftdc.PPO_ATTACH_TIMEOUT_SEC = None
                root.destroy()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_49_config_only_macid_prefix_and_no_hardcoded_defaults(self):
        """Verify:
        1) Hardcoded default MACID prefixes & SG406 parameter constants are removed; DEFAULT_MACID_PREFIXES == ().
        2) resolve_macid_device_config() raises ValueError when mask/WXY/UID/BT-or-ZB config is missing.
        3) Blank/omitted macid_prefix disables MACID prefix checking while still rejecting dummy MACIDs.
        4) Non-blank macid_prefix strictly enforces only the configured prefix.
        5) End-to-end analyze_guid_data() with blank macid_prefix accepts arbitrary non-40:84 MACIDs
           and detects duplicates properly.
        6) save_mask_wxy_entry() with blank prefixes removes prefix keys from JSON and clears cache.
        """
        import json
        import struct
        import tempfile
        from apps.ftdc_checker import guid_analysis as ga
        from apps.ftdc_checker import sg406_analysis as sg
        from apps.ftdc_checker.guid_analysis import analyze_guid_data, MPC_WXY_TEST_MAP, reload_wxy_config, save_mask_wxy_entry

        # 1. Removed hardcoded defaults
        self.assertEqual(sg.DEFAULT_MACID_PREFIXES, ())
        for removed_name in (
            "SG406_DEFAULT_WXY_TESTS",
            "SG406_UID_TEST",
            "SG406_BT_TESTS",
            "SG406_ZB_TESTS",
            "INVALID_BT",
            "INVALID_ZB",
        ):
            self.assertFalse(hasattr(sg, removed_name), f"{removed_name} should be removed")

        # 2. resolve_macid_device_config requires complete config (no hardcoded fallbacks even for SG406)
        with self.assertRaises(ValueError):
            sg.resolve_macid_device_config("UNCFG", {})
        with self.assertRaises(ValueError):
            sg.resolve_macid_device_config("SG406", {})
        with self.assertRaises(ValueError):
            sg.resolve_macid_device_config("SG406", {"SG406": {"wafer": 1, "x": 2, "uid": 3, "bt": [4]}})
        with self.assertRaises(ValueError):
            sg.resolve_macid_device_config("TESTD", {"TESTD": {"wafer": 1, "x": 2, "y": 3, "bt": [4]}})
        with self.assertRaises(ValueError):
            sg.resolve_macid_device_config("TESTD", {"TESTD": {"wafer": 1, "x": 2, "y": 3, "uid": 4}})

        # 3. Blank or omitted macid_prefix disables prefix checking while still excluding dummies
        for extra_cfg in ({"macid_prefix": ""}, {}):
            cfg_blank = sg.resolve_macid_device_config(
                "BLANK",
                {"BLANK": {"wafer": 1, "x": 2, "y": 3, "uid": 4, "bt": [5], "zb": [6], **extra_cfg}},
            )
            self.assertEqual(cfg_blank["macid_prefixes"], {"bt": (), "zb": ()})
            for valid_mac in ("AA:BB:11:22:33:44", "12:34:56:78:9A:BC", "40:84:32:0C:C0:72", "170_187_1_2_3_4"):
                self.assertTrue(sg._is_valid_macid_cfg(valid_mac, "bt", cfg_blank), valid_mac)
                self.assertTrue(sg._is_valid_macid_cfg(valid_mac, "zb", cfg_blank), valid_mac)
            for dummy_mac in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF", "11:22:33:44:55:66", "0_0_0_0_0_0", "NaN"):
                self.assertFalse(sg._is_valid_macid_cfg(dummy_mac, "bt", cfg_blank), dummy_mac)
                self.assertFalse(sg._is_valid_macid_cfg(dummy_mac, "zb", cfg_blank), dummy_mac)

        # 4. Configured prefix strictly enforces only that prefix
        cfg_pfx = sg.resolve_macid_device_config(
            "PFXDV",
            {"PFXDV": {"wafer": 1, "x": 2, "y": 3, "uid": 4, "bt": [5], "macid_prefix": "A0:B1"}},
        )
        self.assertTrue(sg._is_valid_macid_cfg("A0:B1:00:00:00:01", "bt", cfg_pfx))
        self.assertFalse(sg._is_valid_macid_cfg("40:84:32:0C:C0:72", "bt", cfg_pfx))
        self.assertFalse(sg._is_valid_macid_cfg("AA:BB:11:22:33:44", "bt", cfg_pfx))

        # 5. End-to-end analyze_guid_data() with blank macid_prefix
        def _build_macid_stdf(lot_id: str, dev_name: str, parts_spec: list) -> bytes:
            out = bytearray()
            out += TestSTDFParserAndReliability._rec(0, 10, bytes([2, 4]), "<")
            mir = bytearray(struct.pack("<IIBBBBH", 1700000000, 1700000010, 1, ord("P"), ord(" "), ord(" "), 65535))
            mir += b" "
            for s in (lot_id, dev_name, "NODE1", "V93K", "JOB1"):
                mir += TestSTDFParserAndReliability._cn(s)
            mir += b"\x00" * 25
            out += TestSTDFParserAndReliability._rec(1, 10, bytes(mir), "<")
            for p in parts_spec:
                out += TestSTDFParserAndReliability._rec(5, 10, bytes([1, 1]), "<")
                tmap = dict(p["tests"])
                for tnum, val in sorted(tmap.items()):
                    ptr = bytearray(struct.pack("<IBBBBf", int(tnum), 1, 1, 0, 0, float(val)))
                    ptr += TestSTDFParserAndReliability._cn(f"T_{tnum}") + b"\x00\x00\x00\x00\x00"
                    ptr += struct.pack("<ff", -1e6, 1e6)
                    out += TestSTDFParserAndReliability._rec(15, 10, bytes(ptr), "<")
                is_p = p.get("pass", True)
                prr = bytearray(struct.pack("<BBBHHHhhI", 1, 1, 0x00 if is_p else 0x08, len(tmap), 1 if is_p else 5, 1 if is_p else 5, -32768, -32768, 12))
                prr += TestSTDFParserAndReliability._cn(str(p["part_id"])) + b"\x00\x00"
                out += TestSTDFParserAndReliability._rec(5, 20, bytes(prr), "<")
            good_cnt = sum(1 for p in parts_spec if p.get("pass", True))
            out += TestSTDFParserAndReliability._rec(1, 30, struct.pack("<BBIIIII", 255, 0, len(parts_spec), 0, 0, good_cnt, 0), "<")
            out += TestSTDFParserAndReliability._rec(1, 20, struct.pack("<IB", 1700001000, ord(" ")) + b"\x00\x00", "<")
            return bytes(out)

        tmp_dir = tempfile.mkdtemp(prefix="pts_hub_cfg_only_")
        orig_json_path = ga._MASK_WXY_JSON_PATH
        try:
            fp_path = os.path.join(tmp_dir, "fp.std")
            rt_path = os.path.join(tmp_dir, "rt.std")
            qc_path = os.path.join(tmp_dir, "qc.std")

            custom_map = dict(MPC_WXY_TEST_MAP)
            custom_map["NOPFX"] = {
                "wafer": 1001, "x": 1002, "y": 1003,
                "uid": 1004,
                "bt": [2001, 2002, 2003, 2004, 2005, 2006],
                "macid_prefix": "",
            }
            with patch.dict(MPC_WXY_TEST_MAP, custom_map, clear=False):
                pass_fp_parts = [
                    {"part_id": "1", "pass": True, "tests": {1001: 1, 1002: 10, 1003: 1, 1004: 5001, 2001: 170, 2002: 187, 2003: 1, 2004: 2, 2005: 3, 2006: 4}},
                    {"part_id": "2", "pass": True, "tests": {1001: 1, 1002: 10, 1003: 2, 1004: 5002, 2001: 170, 2002: 187, 2003: 1, 2004: 2, 2005: 3, 2006: 5}},
                ]
                with open(fp_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX.000", "NOPFX", pass_fp_parts))
                with open(rt_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX.000", "NOPFX", []))
                with open(qc_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX.000", "NOPFX", [pass_fp_parts[0]]))

                res_pass = analyze_guid_data(
                    first_pass_paths=[fp_path], retest_paths=[rt_path], qc_paths=[qc_path],
                    lot_id="LOT_NOPFX.000", mpc_text="NOPFX1RTBA10",
                    first_pass_actual_good_qty="2", total_actual_good_qty="2",
                    max_workers=1,
                )
                self.assertEqual(res_pass["status"], "PASS")
                self.assertEqual(res_pass["duplicate_bt_macid"], 0)
                self.assertEqual(res_pass["fp_good_unique_discrepancy"], 0)

                dup_fp_parts = [
                    {"part_id": "1", "pass": True, "tests": {1001: 1, 1002: 10, 1003: 1, 1004: 5001, 2001: 170, 2002: 187, 2003: 1, 2004: 2, 2005: 3, 2006: 4}},
                    {"part_id": "2", "pass": True, "tests": {1001: 1, 1002: 10, 1003: 2, 1004: 5002, 2001: 170, 2002: 187, 2003: 1, 2004: 2, 2005: 3, 2006: 4}},
                ]
                with open(fp_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX_DUP.000", "NOPFX", dup_fp_parts))
                with open(rt_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX_DUP.000", "NOPFX", []))
                with open(qc_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX_DUP.000", "NOPFX", [dup_fp_parts[0]]))

                res_dup = analyze_guid_data(
                    first_pass_paths=[fp_path], retest_paths=[rt_path], qc_paths=[qc_path],
                    lot_id="LOT_NOPFX_DUP.000", mpc_text="NOPFX1RTBA10",
                    first_pass_actual_good_qty="2", total_actual_good_qty="2",
                    max_workers=1,
                )
                self.assertEqual(res_dup["status"], "FAIL")
                self.assertEqual(res_dup["duplicate_bt_macid"], 1)

                # FAIL units sharing the same non-dummy MACID (06:80:FC:81:01:07) across different UIDs
                # MUST NOT be counted as Duplicate MACID when prefix checking is disabled.
                fail_shared_mac_parts = [
                    {"part_id": "1", "pass": True, "tests": {1001: 1, 1002: 10, 1003: 1, 1004: 5001, 2001: 170, 2002: 187, 2003: 1, 2004: 2, 2005: 3, 2006: 4}},
                    {"part_id": "2", "pass": True, "tests": {1001: 1, 1002: 10, 1003: 2, 1004: 5002, 2001: 170, 2002: 187, 2003: 1, 2004: 2, 2005: 3, 2006: 5}},
                    {"part_id": "3", "pass": False, "tests": {1001: 1, 1002: 20, 1003: 1, 1004: 6001, 2001: 6, 2002: 128, 2003: 252, 2004: 129, 2005: 1, 2006: 7}},
                    {"part_id": "4", "pass": False, "tests": {1001: 1, 1002: 20, 1003: 2, 1004: 6002, 2001: 6, 2002: 128, 2003: 252, 2004: 129, 2005: 1, 2006: 7}},
                ]
                with open(fp_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX_FAIL_MAC.000", "NOPFX", fail_shared_mac_parts))
                with open(rt_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX_FAIL_MAC.000", "NOPFX", []))
                with open(qc_path, "wb") as f:
                    f.write(_build_macid_stdf("LOT_NOPFX_FAIL_MAC.000", "NOPFX", [fail_shared_mac_parts[0]]))

                res_fail_mac = analyze_guid_data(
                    first_pass_paths=[fp_path], retest_paths=[rt_path], qc_paths=[qc_path],
                    lot_id="LOT_NOPFX_FAIL_MAC.000", mpc_text="NOPFX1RTBA10",
                    first_pass_actual_good_qty="2", total_actual_good_qty="2",
                    max_workers=1,
                )
                self.assertEqual(res_fail_mac["status"], "PASS")
                self.assertEqual(res_fail_mac["total_good_status"], "PASS")
                self.assertEqual(res_fail_mac["duplicate_bt_macid"], 0)
                self.assertEqual(res_fail_mac["duplicate_macid"], 0)

            # 6. save_mask_wxy_entry() with blank prefixes removes prefix keys and clears cache
            tmp_mask_json = os.path.join(tmp_dir, "mask_wxy_map.json")
            with open(tmp_mask_json, "w", encoding="utf-8") as f:
                json.dump({
                    "TMPMK": {
                        "wafer": 10, "x": 11, "y": 12,
                        "uid": 13, "bt": [14],
                        "macid_prefix": "40:84",
                        "bt_prefix": "40:84",
                        "zb_prefix": "40:84",
                    }
                }, f)

            ga._MASK_WXY_JSON_PATH = tmp_mask_json
            reload_wxy_config()
            sg._compile_macid_rules(("40:84",), ())
            self.assertGreater(sg._compile_macid_rules.cache_info().currsize, 0)

            saved = save_mask_wxy_entry(
                "TMPMK", 10, 11, 12,
                uid=13, bt=[14],
                macid_prefix="", bt_prefix="", zb_prefix="",
            )
            self.assertEqual(sg._compile_macid_rules.cache_info().currsize, 0)
            for k in ("macid_prefix", "bt_prefix", "zb_prefix"):
                self.assertNotIn(k, saved)
            with open(tmp_mask_json, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            for k in ("macid_prefix", "bt_prefix", "zb_prefix"):
                self.assertNotIn(k, on_disk["TMPMK"])
        finally:
            ga._MASK_WXY_JSON_PATH = orig_json_path
            reload_wxy_config()
            sg.clear_macid_validation_cache()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_50_hub_settings_button_and_developer_mode_disables_ppo_auto_attach(self):
        """Verify:
        1) HubWindow top navbar includes a Settings button (outside nav tabs) and hides DEV MODE badge in Production Mode.
        2) Clicking Settings opens a single-instance Settings dialog with Production / Developer Mode toggle.
        3) Switching to Developer Mode shows the DEV MODE badge, persists app_mode='developer',
           updates dialog description, and notifies FTDCCheckerFrame.
        4) In Developer Mode, generated Excel stays local and auto-attachment to PPO subfolders is skipped
           (both via _start_background_excel_generation and direct _auto_attach_excel_to_ppo), leaving is_running=False.
        5) Switching back to Production Mode hides DEV MODE badge, persists app_mode='production',
           and re-enables auto-attachment of cached Excel + snapshot image to all PPO subfolders.
        """
        import json
        import tempfile
        import time
        import tkinter as tk
        from unittest import mock
        import core.config as app_cfg
        from core.hub_window import HubWindow, NAV_ICON_BITMAPS
        from apps.ftdc_checker import ui as ftdc_ui
        from apps.ftdc_checker.ui import FTDCCheckerFrame

        self.assertIn("Settings", NAV_ICON_BITMAPS)
        orig_get_asset_path = app_cfg.get_asset_path

        tmp_dir = tempfile.mkdtemp(prefix="pts_hub_settings_mode_")
        tmp_settings_json = os.path.join(tmp_dir, "settings.json")
        real_settings_path = orig_get_asset_path("settings.json")
        if os.path.isfile(real_settings_path):
            shutil.copy2(real_settings_path, tmp_settings_json)
            with open(tmp_settings_json, "r", encoding="utf-8") as fh:
                init_cfg = json.load(fh)
        else:
            init_cfg = {}
        init_cfg["app_mode"] = "production"
        with open(tmp_settings_json, "w", encoding="utf-8") as fh:
            json.dump(init_cfg, fh, indent=2)

        ppo_base = os.path.join(tmp_dir, "PPO")
        local_base = os.path.join(tmp_dir, "LOCAL")
        lot_dir = os.path.join(ppo_base, "59793", "MMT-272900204.000")
        ppo_100 = os.path.join(lot_dir, "PPO100")
        ppo_200 = os.path.join(lot_dir, "PPO200")
        ppo_300 = os.path.join(lot_dir, "PPO300")
        os.makedirs(ppo_100)
        os.makedirs(ppo_200)
        os.makedirs(ppo_300)

        def _patched_get_asset_path(filename: str) -> str:
            if filename == "settings.json":
                return tmp_settings_json
            return orig_get_asset_path(filename)

        with mock.patch.object(app_cfg, "get_asset_path", side_effect=_patched_get_asset_path):
            app_cfg.reload_settings()
            root = tk.Tk()
            root.withdraw()
            try:
                setup_theme(root)
                hub = HubWindow(root)
                ftdc = FTDCCheckerFrame(hub.content_area, hub=hub)
                hub.register_app("FTDC Checker", ftdc, is_default=True)
                ftdc.PPO_ATTACH_RETRY_DELAYS = (0.0, 0.0)
                ftdc._queue_ppo_notice = lambda *args, **kwargs: None

                # 1. Navbar Settings button & default Production Mode state
                self.assertTrue(hasattr(hub, "settings_btn"))
                self.assertEqual(hub.settings_btn.cget("compound"), "left")
                self.assertNotEqual(hub.settings_btn.cget("image"), "")
                self.assertEqual(hub.settings_btn.cget("text"), "  Settings")
                self.assertNotIn("Settings", hub._nav_items)
                self.assertEqual(app_cfg.get_app_mode(), "production")
                self.assertFalse(app_cfg.is_developer_mode())
                self.assertEqual(hub.mode_badge.winfo_manager(), "")

                # 2. Open Settings dialog (single-instance check)
                win = hub.open_settings_dialog()
                self.assertTrue(win.winfo_exists())
                self.assertIs(hub.open_settings_dialog(), win)
                self.assertEqual(win.mode_status_label.cget("text"), "Production Mode")
                self.assertIn("automatically attach", win.mode_desc_label.cget("text"))

                # 3. Toggle to Developer Mode
                win.toggle_command()
                root.update()
                self.assertEqual(app_cfg.get_app_mode(), "developer")
                self.assertTrue(app_cfg.is_developer_mode())
                self.assertNotEqual(hub.mode_badge.winfo_manager(), "")
                self.assertEqual(hub.mode_badge.cget("text"), "DEV MODE")
                self.assertEqual(win.mode_status_label.cget("text"), "Developer Mode")
                self.assertIn("disabled", win.mode_desc_label.cget("text").lower())
                with open(tmp_settings_json, "r", encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh).get("app_mode"), "developer")
                while ftdc._pending_log_messages:
                    root.update()
                    time.sleep(0.01)
                self.assertIn("Developer Mode", ftdc.log_text.get("1.0", "end"))

                # 4. In Developer Mode, Excel is generated locally and PPO auto-attach is skipped
                def _dummy_write_excel(_result, out_path, progress_callback=None):
                    with open(out_path, "wb") as fh:
                        fh.write(b"PK-dummy-xlsx")

                def _dummy_render_img(_result, out_path, **_kwargs):
                    with open(out_path, "wb") as fh:
                        fh.write(b"\x89PNG\r\n\x1a\ndummy")

                def _wait_ui(timeout_s: float = 5.0):
                    dl = time.monotonic() + timeout_s
                    while (
                        (ftdc._excel_bg_thread is not None and ftdc._excel_bg_thread.is_alive())
                        or ftdc.is_running
                        or ftdc._pending_running_state is not None
                        or ftdc._pending_ppo_status is not None
                        or bool(ftdc._pending_log_messages)
                    ) and time.monotonic() < dl:
                        root.update()
                        time.sleep(0.01)
                    root.update()

                res = {
                    "lot_id": "MMT-272900204.000",
                    "mir_lot_id": "MMT-272900204.000",
                    "mpc": "59793QRTBC01",
                    "status": "PASS",
                    "total_good_status": "PASS",
                    "first_pass_pass_count": 10,
                    "first_pass_actual_good": 10,
                    "retest_pass_count": 0,
                    "qc_good_prev_op_count": 5,
                    "qc_failed_or_missing_prev_op_qty": 0,
                    "first_pass": {"rows": []},
                    "qc": {"rows": []},
                }

                with mock.patch.object(ftdc_ui, "write_result_excel", side_effect=_dummy_write_excel) as mock_xl, \
                     mock.patch.object(ftdc_ui, "render_result_image", side_effect=_dummy_render_img), \
                     mock.patch.object(ftdc_ui, "get_ppo_base_path", return_value=ppo_base), \
                     mock.patch.object(ftdc_ui, "get_local_dest_base", return_value=local_base):
                    ftdc._start_background_excel_generation(res, auto_attach=True)
                    _wait_ui(5.0)

                    self.assertFalse(ftdc.is_running)
                    self.assertEqual(ftdc._excel_state, "READY")
                    self.assertTrue(res.get("_cached_excel_ready"))
                    self.assertTrue(os.path.isfile(res["_cached_excel_path"]))
                    self.assertEqual(ftdc._ppo_attach_state, "IDLE")
                    self.assertIn("Developer Mode", ftdc.status_var.get())
                    self.assertIn("Developer Mode", ftdc.log_text.get("1.0", "end"))
                    for sub in (ppo_100, ppo_200, ppo_300):
                        self.assertEqual(os.listdir(sub), [], f"PPO folder {sub} should be empty in Developer Mode")
                    self.assertEqual(mock_xl.call_count, 1)

                    # Direct _auto_attach_excel_to_ppo call is also skipped in Developer Mode
                    ftdc._auto_attach_excel_to_ppo(
                        res,
                        res["_cached_excel_path"],
                        res["_cached_excel_name"],
                        ftdc._excel_generation_id,
                    )
                    _wait_ui(2.0)
                    self.assertEqual(ftdc._ppo_attach_state, "IDLE")
                    for sub in (ppo_100, ppo_200, ppo_300):
                        self.assertEqual(os.listdir(sub), [])

                    # 5. Toggle back to Production Mode -> auto-attaches cached Excel + image to all PPO folders
                    win.toggle_command()
                    root.update()
                    self.assertEqual(app_cfg.get_app_mode(), "production")
                    self.assertFalse(app_cfg.is_developer_mode())
                    self.assertEqual(hub.mode_badge.winfo_manager(), "")
                    self.assertEqual(win.mode_status_label.cget("text"), "Production Mode")
                    with open(tmp_settings_json, "r", encoding="utf-8") as fh:
                        self.assertEqual(json.load(fh).get("app_mode"), "production")

                    ftdc._start_background_excel_generation(res, auto_attach=True)
                    _wait_ui(5.0)

                    self.assertFalse(ftdc.is_running)
                    self.assertEqual(ftdc._ppo_attach_state, "SUCCESS")
                    self.assertEqual(mock_xl.call_count, 1)  # Cached Excel reused
                    xl_name = res["_cached_excel_name"]
                    img_name = res["_cached_image_name"]
                    for sub in (ppo_100, ppo_200, ppo_300):
                        self.assertTrue(os.path.isfile(os.path.join(sub, xl_name)), f"Missing {xl_name} in {sub}")
                        self.assertTrue(os.path.isfile(os.path.join(sub, img_name)), f"Missing {img_name} in {sub}")
            finally:
                root.destroy()
                app_cfg.reload_settings()
                shutil.rmtree(tmp_dir, ignore_errors=True)


class TestSTDF2SumIntegration(unittest.TestCase):
    """Integration tests for optimized STDF2SUM inside PTS Hub."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        setup_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    @staticmethod
    def _build_synthetic_stdf_file(path: str):
        import struct

        def cn(s: str) -> bytes:
            b = s.encode("latin-1", errors="replace")
            return struct.pack("<B", len(b)) + b

        def rec(rt, body: bytes) -> bytes:
            return struct.pack("<HBB", len(body), rt[0], rt[1]) + body

        out = bytearray()
        out += rec((0, 10), struct.pack("<BB", 0, 4))
        mir_body = (
            struct.pack("<IIB", 1700000000, 1700000060, 1)
            + b"P  "
            + struct.pack("<H", 0)
            + b" "
            + cn("LOT_HUB_1")
            + cn("PROD_HUB")
        )
        out += rec((1, 10), mir_body)
        out += rec((1, 80), struct.pack("<BBBB", 1, 1, 1, 1) + cn("HND"))
        out += rec((1, 40), struct.pack("<BBHI", 1, 1, 1, 1) + b"P" + cn("PASS_BIN"))
        out += rec((1, 50), struct.pack("<BBHI", 1, 1, 1, 1) + b"P" + cn("GOOD_SBIN"))

        # Part 1 (PASS)
        out += rec((5, 10), struct.pack("<BB", 1, 1))
        ptr1 = (
            struct.pack("<IBBBBf", 9000, 1, 1, 0, 0, 5.5)
            + cn("VOLTS")
            + cn("")
            + bytes(4)
            + struct.pack("<ff", 0.0, 10.0)
            + cn("V")
        )
        out += rec((15, 10), ptr1)
        prr1 = bytearray(17)
        prr1[0] = 1
        prr1[1] = 1
        prr1[2] = 0
        struct.pack_into("<HH", prr1, 5, 1, 1)
        struct.pack_into("<I", prr1, 13, 100)
        prr1 += cn("P1")
        out += rec((5, 20), bytes(prr1))

        # Part 2 (FAIL)
        out += rec((5, 10), struct.pack("<BB", 1, 1))
        ptr2 = (
            struct.pack("<IBBBBf", 9000, 1, 1, 0, 0, 12.5)
            + cn("VOLTS")
            + cn("")
            + bytes(4)
            + struct.pack("<ff", 0.0, 10.0)
            + cn("V")
        )
        out += rec((15, 10), ptr2)
        prr2 = bytearray(17)
        prr2[0] = 1
        prr2[1] = 1
        prr2[2] = 0x08
        struct.pack_into("<HH", prr2, 5, 2, 2)
        struct.pack_into("<I", prr2, 13, 120)
        prr2 += cn("P2")
        out += rec((5, 20), bytes(prr2))

        out += rec((1, 20), struct.pack("<I", 1700000120) + b" " + cn("") + cn(""))
        with open(path, "wb") as f:
            f.write(bytes(out))

    def test_01_stdf2sum_frame_widgets_and_file_badges(self):
        """Verify STDF2SumFrame widgets, Neon progress bar, badge transitions, auto output prefix, and PTS Hub colors."""
        hub = HubWindow(self.root)
        ftdc = FTDCCheckerFrame(hub.content_area, hub=hub)
        stdf2sum = STDF2SumFrame(hub.content_area, hub=hub)
        data_analysis = DataAnalysisFrame(hub.content_area, hub=hub)
        hub.register_app("FTDC Checker", ftdc)
        hub.register_app("STDF2SUM", stdf2sum)
        hub.register_app("Data Analysis", data_analysis)
        hub.switch_tab("Home")
        self.root.update()

        # Verify HubWindow navbar, tabs, Home cards, FTDCCheckerFrame, and STDF2SumFrame retain exact design colors
        nav = hub.tabs_frame.master
        self.assertEqual(nav.cget("bg"), NAV_BG)
        all_labels = []
        def _collect_labels(w):
            for ch in w.winfo_children():
                if isinstance(ch, tk.Label):
                    all_labels.append(ch)
                _collect_labels(ch)
        _collect_labels(nav)
        brand_lbl = next(lbl for lbl in all_labels if lbl.cget("text") == APP_NAME)
        badge_lbl = next(lbl for lbl in all_labels if lbl.cget("text") == APP_VERSION)
        self.assertEqual(brand_lbl.cget("fg"), NAV_TITLE_FG)
        self.assertEqual(badge_lbl.cget("bg"), NAV_BADGE_BG)
        self.assertEqual(badge_lbl.cget("fg"), NAV_BADGE_FG)
        self.assertEqual(hub._nav_items["Home"].cget("bg"), NAV_TAB_ACTIVE)
        for tab_name in ("FTDC Checker", "STDF2SUM", "Data Analysis"):
            self.assertEqual(hub._nav_items[tab_name].cget("bg"), NAV_TAB_BG)

        for card_name, card in hub.home_frame.cards.items():
            self.assertEqual(card.cget("bg"), hub.home_frame.CARD_SURFACE, f"Home card {card_name} bg")
            self.assertEqual(
                card.cget("highlightbackground"),
                hub.home_frame.CARD_BORDER_IDLE,
                f"Home card {card_name} border",
            )

        self.assertEqual(ftdc.add_device_button.cget("bg"), BTN_BG)
        for panel in ftdc.PANELS:
            self.assertEqual(ftdc.panel_listboxes[panel].cget("bg"), LISTBOX_BG)
            self.assertEqual(ftdc._panel_badges[panel].cget("bg"), BADGE_EMPTY_BG)
        self.assertEqual(ftdc.get_stdf_button.cget("bg"), BTN_BG)
        self.assertEqual(ftdc.convert_button.cget("bg"), ACCENT_BTN_BG)

        self.assertEqual(stdf2sum.files_listbox.cget("bg"), LISTBOX_BG)
        self.assertEqual(stdf2sum.files_badge.cget("bg"), BADGE_EMPTY_BG)
        self.assertEqual(stdf2sum.add_files_button.cget("bg"), BTN_BG)
        self.assertEqual(stdf2sum.convert_button.cget("bg"), ACCENT_BTN_BG)

        for attr in (
            "files_listbox",
            "files_badge",
            "convert_button",
            "show_all_tests_button",
            "clear_selected_tests_button",
            "output_entry",
            "progress_bar",
            "log_text",
        ):
            self.assertIsNotNone(getattr(stdf2sum, attr), f"Missing attribute: {attr}")

        self.assertEqual(stdf2sum.progress_bar.cget("style"), "Neon.Horizontal.TProgressbar")
        self.assertEqual(stdf2sum._files_count_var.get(), "0 files")
        self.assertEqual(stdf2sum.files_badge.cget("bg"), BADGE_EMPTY_BG)
        self.assertEqual(stdf2sum.clear_selected_tests_button.grid_info(), {})

        # Add file -> badge green, count 1 file, output_var auto-populated
        stdf2sum.selected_files.append(r"C:\tmp\lot_sample.std")
        stdf2sum.refresh_file_list()
        self.assertEqual(stdf2sum._files_count_var.get(), "1 file")
        self.assertEqual(stdf2sum.files_badge.cget("bg"), BADGE_READY_BG)
        self.assertEqual(stdf2sum.files_badge.cget("fg"), BADGE_READY_FG)
        self.assertTrue(stdf2sum.output_var.get().endswith("lot_sample"))

        # Select & remove file -> back to 0 files
        stdf2sum.files_listbox.selection_set(0)
        stdf2sum.remove_selected_files()
        self.assertEqual(stdf2sum._files_count_var.get(), "0 files")
        self.assertEqual(stdf2sum.files_badge.cget("bg"), BADGE_EMPTY_BG)
        self.assertEqual(stdf2sum.output_var.get(), "")

        # Add again & clear_files -> back to 0 files
        stdf2sum.selected_files.append(r"C:\tmp\lot_sample2.std")
        stdf2sum.refresh_file_list()
        self.assertEqual(stdf2sum._files_count_var.get(), "1 file")
        stdf2sum.clear_files()
        self.assertEqual(stdf2sum._files_count_var.get(), "0 files")
        self.assertEqual(stdf2sum.files_badge.cget("bg"), BADGE_EMPTY_BG)

    def test_02_stdf2sum_end_to_end_conversion_and_sheets(self):
        """Verify end-to-end STDF2SUM Excel generation & STDF2SumFrame background conversion."""
        import glob
        import tempfile
        import time
        from openpyxl import load_workbook
        from apps.stdf2sum.excel_output import convert_multiple_stdf_to_single_csv, clear_parse_cache

        tmp_dir = tempfile.mkdtemp()
        try:
            stdf_path = os.path.join(tmp_dir, "hub_sample.std")
            self._build_synthetic_stdf_file(stdf_path)
            clear_parse_cache()

            out_prefix = os.path.join(tmp_dir, "direct_out")
            files = convert_multiple_stdf_to_single_csv([stdf_path], output_prefix=out_prefix, verbose=False)
            self.assertTrue(files and os.path.isfile(files[0]))
            wb = load_workbook(files[0], data_only=True)
            for sheet in ("Summary", "Data", "Hard Bins", "Soft Bins", "Test Parameters", "Bin Mapping"):
                self.assertIn(sheet, wb.sheetnames)
            data_rows = list(wb["Data"].iter_rows(min_row=6, values_only=True))
            self.assertEqual(len(data_rows), 2)
            self.assertEqual(data_rows[0][1], "P1")
            self.assertEqual(data_rows[0][7], "PASS")
            self.assertEqual(data_rows[1][1], "P2")
            self.assertEqual(data_rows[1][7], "FAIL")
            wb.close()

            # Also test conversion triggered from STDF2SumFrame
            hub = HubWindow(self.root)
            stdf2sum = STDF2SumFrame(hub.content_area, hub=hub)
            hub.register_app("STDF2SUM", stdf2sum)
            hub.switch_tab("STDF2SUM")

            ui_prefix = os.path.join(tmp_dir, "ui_out")
            stdf2sum.selected_files = [stdf_path]
            stdf2sum.refresh_file_list()
            stdf2sum._set_output_value(ui_prefix, auto_managed=False)

            with patch.object(stdf2sum, "_show_success_dialog") as mock_dlg:
                stdf2sum.start_conversion()
                deadline = time.monotonic() + 10.0
                while stdf2sum.is_running and time.monotonic() < deadline:
                    self.root.update()
                    time.sleep(0.01)
                self.root.update()
                self.assertFalse(stdf2sum.is_running)
                self.assertTrue(mock_dlg.called)

            created_xlsx = glob.glob(ui_prefix + "_*.xlsx")
            self.assertEqual(len(created_xlsx), 1)
            stdf2sum.clear_all()
            self.assertEqual(stdf2sum.selected_files, [])
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_03_stdf2sum_hub_busy_and_alert_sync(self):
        """Verify STDF2SUM background task busy and blinking Done alert synchronization in HubWindow."""
        hub = HubWindow(self.root)
        ftdc = FTDCCheckerFrame(hub.content_area, hub=hub)
        stdf2sum = STDF2SumFrame(hub.content_area, hub=hub)
        hub.register_app("FTDC Checker", ftdc, is_default=True)
        hub.register_app("STDF2SUM", stdf2sum)

        hub.switch_tab("STDF2SUM")
        stdf2sum._set_running(True)
        self.assertTrue(stdf2sum.is_running)

        # Navigate away while STDF2SUM is busy
        hub.switch_tab("FTDC Checker")
        self.assertEqual(hub._tab_states["STDF2SUM"], "busy")
        self.assertIn("⏳", hub._nav_items["STDF2SUM"].cget("text"))

        # Task finishes while user is on FTDC Checker -> blinking alert
        stdf2sum._set_running(False)
        self.assertFalse(stdf2sum.is_running)
        self.assertEqual(hub._tab_states["STDF2SUM"], "alert")
        self.assertIsNotNone(hub._tab_blink_jobs["STDF2SUM"])
        self.assertIn("Done", hub._nav_items["STDF2SUM"].cget("text"))

        hub.stop_tab_alert("STDF2SUM")


if __name__ == "__main__":
    unittest.main()
