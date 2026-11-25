"""
Module for generating and capturing images of the FTDC result window.
Supports:
1. Native window snapshot capture via Windows PrintWindow API (exact pixel capture of Tkinter window).
2. Pure Python / Pillow programmatic rendering fallback (guarantees image generation in headless / background modes).
"""
import logging
import os
import re
import textwrap
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from apps.ftdc_checker.excel_output import build_summary_and_details, _fmt
from apps.ftdc_checker.guid_analysis import compute_remarks
from apps.ftdc_checker.sg406_analysis import (
    resolve_summary_metric_style,
    format_macid_prefix_outlier_banner,
)


def _wrap_text_to_pixel_width(draw_ctx: Any, text: str, font: Any, max_px: int) -> List[str]:
    """Wrap text so each rendered line fits within ``max_px`` pixels."""
    lines: List[str] = []
    fallback_chars = max(30, int(max_px / 7))
    for para in str(text or "").splitlines() or [""]:
        words = para.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for word in words[1:]:
            candidate = f"{cur} {word}"
            bbox = draw_ctx.textbbox((0, 0), candidate, font=font)
            if (bbox[2] - bbox[0]) <= max_px:
                cur = candidate
            else:
                if (draw_ctx.textbbox((0, 0), cur, font=font)[2]) > max_px:
                    lines.extend(textwrap.wrap(cur, width=fallback_chars) or [cur])
                else:
                    lines.append(cur)
                cur = word
        if (draw_ctx.textbbox((0, 0), cur, font=font)[2]) > max_px:
            lines.extend(textwrap.wrap(cur, width=fallback_chars) or [cur])
        else:
            lines.append(cur)
    return lines or [""]


def get_default_result_image_name(result: Dict[str, Any], timestamp: Optional[str] = None) -> str:
    """Return standard result image filename, e.g. {LOT}_FTDC_Result_{YYYYMMDDHHMMSS}.png."""
    lot_text = str(result.get("mir_lot_id") or result.get("lot_id") or "STDF_GUID_Result").strip().upper() or "STDF_GUID_Result"
    safe_lot = re.sub(r"[^A-Za-z0-9_.-]+", "_", lot_text).strip("._") or "STDF_GUID_Result"
    ts = timestamp or datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{safe_lot}_FTDC_Result_{ts}.png"


def _atomic_save_png(img: Image.Image, out_path: str) -> bool:
    """Save a PIL Image to ``out_path`` atomically via a thread-local .part file."""
    abs_out = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(abs_out), exist_ok=True)
    part_path = f"{abs_out}.{threading.get_ident()}.part"
    try:
        img.save(part_path, format="PNG")
        os.replace(part_path, abs_out)
        return os.path.isfile(abs_out) and os.path.getsize(abs_out) > 0
    except Exception:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
        raise


def capture_window_to_image(window: Any, out_path: str) -> bool:
    """Capture a Tkinter window client area using Windows PrintWindow API.

    Returns True if successfully captured and saved, False otherwise.
    Must be called on the main UI thread; returns False immediately if called from a worker thread.
    """
    if window is None or threading.current_thread() is not threading.main_thread():
        return False
    try:
        if not getattr(window, "winfo_exists", lambda: False)():
            return False
        window.update_idletasks()
        hwnd = int(window.winfo_id())
        if not hwnd:
            return False

        import ctypes
        from ctypes import windll, wintypes, byref

        rect = wintypes.RECT()
        if not windll.user32.GetClientRect(hwnd, byref(rect)):
            return False
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 10 or h <= 10:
            return False

        hdc_screen = windll.user32.GetDC(hwnd)
        if not hdc_screen:
            return False
        hdc_mem = windll.gdi32.CreateCompatibleDC(hdc_screen)
        hbm = windll.gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        windll.gdi32.SelectObject(hdc_mem, hbm)

        # Flag 2: PW_RENDERFULLCONTENT (captures layered/modern content)
        res = windll.user32.PrintWindow(hwnd, hdc_mem, 2)
        if not res:
            res = windll.user32.PrintWindow(hwnd, hdc_mem, 0)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        buf = ctypes.create_string_buffer(w * h * 4)
        windll.gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, byref(bmi), 0)

        windll.gdi32.DeleteObject(hbm)
        windll.gdi32.DeleteDC(hdc_mem)
        windll.user32.ReleaseDC(hwnd, hdc_screen)

        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
        extrema = img.getextrema()
        if all(mn == mx for mn, mx in extrema):
            return False

        return _atomic_save_png(img, out_path)
    except Exception as exc:
        logging.warning("PrintWindow capture failed: %s", exc)
        return False


def _get_font(name: str, size: int) -> ImageFont.ImageFont:
    """Safely load a TrueType font or fallback to Pillow default font."""
    try:
        return ImageFont.truetype(name, size)
    except Exception:
        return ImageFont.load_default()


def render_result_image(result: Dict[str, Any], out_path: str) -> bool:
    """Generate a clean programmatic rendering of the FTDC result window.

    Matches the exact layout and color palette of the Tkinter Result Popup:
    - Title: Automatic FTDC Checker & Lot ID
    - Truncated files warning card (if any)
    - Side-by-side comparative matrices (First Pass & Total Good)
    - Remarks card with green/red status highlighting
    """
    summary_sections, _, fp_status, total_status, _, _ = build_summary_and_details(result)
    remarks_text = str(result.get("remarks") or compute_remarks(result)).strip()

    f_title = _get_font("segoeuib.ttf", 15)
    f_head = _get_font("segoeuib.ttf", 12)
    f_body = _get_font("segoeui.ttf", 11)
    f_bold = _get_font("segoeuib.ttf", 11)
    f_sm = _get_font("segoeui.ttf", 10)

    # Measure exact label and value widths so the right-hand value column auto-adjusts
    _probe_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    max_lbl_px = max(
        (_probe_draw.textbbox((0, 0), f"{m}:", font=f_body)[2] for _, rows in summary_sections for m, _ in rows),
        default=260,
    )
    max_val_px = max(
        (_probe_draw.textbbox((0, 0), _fmt(v), font=f_bold)[2] for _, rows in summary_sections for _, v in rows),
        default=60,
    )
    req_col_w = max(431, max_lbl_px + max_val_px + 44)
    img_w = max(900, req_col_w * 2 + 38)
    max_rows = max(len(sec[1]) for sec in summary_sections) if summary_sections else 8
    truncated_list = result.get("truncated_files") or []
    po_banner = format_macid_prefix_outlier_banner(result)

    warn_h = (28 + len(truncated_list) * 18) if truncated_list else 0
    _measure_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wrapped_po_lines: List[Tuple[str, bool]] = []
    if po_banner:
        po_max_px = max(320, img_w - 60)
        for line_idx, raw_line in enumerate(po_banner["lines"]):
            is_title = (line_idx == 0)
            use_font = f_bold if is_title else f_sm
            for seg in _wrap_text_to_pixel_width(_measure_draw, raw_line, use_font, po_max_px):
                wrapped_po_lines.append((seg, is_title))
    po_h = (12 + len(wrapped_po_lines) * 17) if wrapped_po_lines else 0

    header_h = 42
    table_row_h = 24
    table_content_h = max_rows * table_row_h
    table_box_h = 28 + table_content_h + 4

    wrapped_remarks = _wrap_text_to_pixel_width(_measure_draw, remarks_text, f_bold, max(320, img_w - 115))
    remarks_h = 24 + len(wrapped_remarks) * 18
    footer_h = 24

    img_h = (
        header_h
        + (warn_h + 8 if warn_h else 0)
        + (po_h + 8 if po_h else 0)
        + table_box_h
        + remarks_h
        + footer_h
        + 24
    )

    img = Image.new("RGB", (img_w, img_h), "#F0F0F2")
    draw = ImageDraw.Draw(img)

    y = 10
    lot_id = str(result.get("mir_lot_id") or result.get("lot_id") or "N/A")
    draw.text((15, y), f"Automatic FTDC Checker — {lot_id}", fill="#111111", font=f_title)
    y += 32

    # Truncated warnings card
    if truncated_list:
        draw.rounded_rectangle([15, y, img_w - 15, y + warn_h], radius=4, fill="#FFF3CD", outline="#FFEEBA")
        draw.text((25, y + 5), "⚠️ WARNING: Truncated or corrupted STDF file(s) detected during analysis:", fill="#856404", font=f_bold)
        wy = y + 24
        for tf in truncated_list:
            reasons = []
            if not tf.get("ended_at_eof"):
                reasons.append("Premature EOF")
            if not tf.get("last_record_was_mrr"):
                reasons.append("Missing MRR record")
            if tf.get("pcr_part_cnt") is not None and tf.get("prr_count") != tf.get("pcr_part_cnt"):
                reasons.append(f"PRR ({tf.get('prr_count')}) != PCR ({tf.get('pcr_part_cnt')})")
            r_txt = ", ".join(reasons) if reasons else "Truncated file"
            draw.text((35, wy), f"• [{tf.get('panel')}] {tf.get('filename')}: {r_txt}", fill="#856404", font=f_sm)
            wy += 16
        y += warn_h + 8

    # Layer 2 MACID Prefix Outlier card
    if po_banner and wrapped_po_lines:
        draw.rounded_rectangle([15, y, img_w - 15, y + po_h], radius=4, fill=po_banner["bg"], outline=po_banner["border"])
        py = y + 6
        for seg_txt, is_title in wrapped_po_lines:
            draw.text((25 if is_title else 32, py), seg_txt, fill=po_banner["fg"], font=f_bold if is_title else f_sm)
            py += 17
        y += po_h + 8

    # Side-by-side matrices
    col_w = (img_w - 38) // 2
    for c_idx, (sec_title, rows) in enumerate(summary_sections):
        x0 = 15 if c_idx == 0 else 23 + col_w
        x1 = x0 + col_w
        ty0 = y
        ty1 = y + table_box_h

        # Outer border
        draw.rounded_rectangle([x0, ty0, x1, ty1], radius=4, fill="#FFFFFF", outline="#B7B7B7")
        # Header banner
        draw.rectangle([x0 + 1, ty0 + 1, x1 - 1, ty0 + 26], fill="#F0F0F0")
        draw.line([(x0, ty0 + 26), (x1, ty0 + 26)], fill="#B7B7B7", width=1)
        draw.text((x0 + 8, ty0 + 5), sec_title, fill="#000000", font=f_head)

        # Auto-adjusted vertical separator for the right-hand value column
        panel_max_val_px = max(
            (draw.textbbox((0, 0), _fmt(v), font=f_bold)[2] for _, v in rows),
            default=40,
        )
        val_sep_x = x1 - (panel_max_val_px + 20)
        draw.line([(val_sep_x, ty0 + 26), (val_sep_x, ty1)], fill="#E0E0E0", width=1)

        ry = ty0 + 27
        for r_idx, (metric, val) in enumerate(rows):
            val_str, val_fg = resolve_summary_metric_style(
                metric, val, result, is_total=(c_idx > 0), fmt_fn=_fmt
            )

            # Draw row separator line
            if r_idx > 0:
                draw.line([(x0 + 1, ry), (x1 - 1, ry)], fill="#E0E0E0", width=1)

            # Metric label on the left
            draw.text((x0 + 8, ry + 4), f"{metric}:", fill="#222222", font=f_body)

            # Metric value right-aligned
            bbox = draw.textbbox((0, 0), val_str, font=f_bold)
            val_w = bbox[2] - bbox[0]
            draw.text((x1 - 12 - val_w, ry + 4), val_str, fill=val_fg, font=f_bold)
            ry += table_row_h

    y += table_box_h + 8

    # Remarks Card
    if remarks_text == "No abnormalities or discrepancies were observed.":
        rem_fg = "#008000"
    elif remarks_text.startswith("PASS with Warning:"):
        rem_fg = "#9A5B00"
    else:
        rem_fg = "#C00000"
    draw.rounded_rectangle([15, y, img_w - 15, y + remarks_h], radius=4, fill="#FFFFFF", outline="#B7B7B7")
    draw.text((22, y + 6), "Remarks:", fill="#111111", font=f_bold)

    rem_x = 90
    rem_y = y + 6
    for line in wrapped_remarks:
        draw.text((rem_x, rem_y), line, fill=rem_fg, font=f_bold)
        rem_y += 18

    # Footer
    y += remarks_h + 6
    ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    draw.text((18, y), f"Generated: {ts_now}", fill="#888888", font=f_sm)

    return _atomic_save_png(img, out_path)


def save_result_window_image(result: Dict[str, Any], out_path: str, window: Any = None) -> str:
    """Save result window image, attempting native window capture first, then falling back to programmatic render."""
    if window is not None:
        if capture_window_to_image(window, out_path):
            return out_path
    render_result_image(result, out_path)
    return out_path
