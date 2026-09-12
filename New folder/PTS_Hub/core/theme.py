"""
Theme setup, ttk styling, and palette constants for PTS Hub.
"""
from tkinter import ttk
import sv_ttk

# ── Color Palette Constants ──────────────────────────────────────────────────
NAV_BG = "#0F172A"          # Slate-900 navigation bar background
NAV_TAB_HOVER = "#1E293B"    # Slate-800 hover state
NAV_TAB_ACTIVE = "#2563EB"   # Vibrant blue active tab
NAV_TEXT = "#94A3B8"         # Slate-400 inactive text
NAV_TEXT_ACTIVE = "#FFFFFF"  # Crisp white active text
STATUS_ONLINE = "#10B981"    # Emerald online dot indicator
BORDER_COLOR = "#CBD5E1"     # Light slate border


def setup_theme(root):
    """Initialize sv_ttk light theme and register custom ttk element styles."""
    sv_ttk.set_theme("light")
    style = ttk.Style()

    style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 14))
    style.configure("Action.TButton", font=("Segoe UI", 9, "bold"), padding=(10, 8))
    style.configure("MiniAction.TButton", font=("Segoe UI", 8), padding=(6, 3))
    style.configure("CardHeader.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Subtext.TLabel", font=("Segoe UI", 8), foreground="#64748B")
    style.configure("PanelTitle.TLabel", font=("Segoe UI", 9, "bold"))

    # Register Neon Green fill style on default tkinter ttk.Progressbar
    try:
        root.tk.eval('''
image create photo neon_pbar -width 20 -height 12
neon_pbar put #00E676 -to 0 0 20 12
catch {ttk::style element create Neon.pbar image neon_pbar}
ttk::style layout Neon.Horizontal.TProgressbar {
    Horizontal.Progressbar.trough -sticky nswe -children {
        Neon.pbar -side left -sticky ns
    }
}
''')
    except Exception:
        pass

    return style
