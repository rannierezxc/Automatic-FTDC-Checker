"""
Theme setup, ttk styling, and palette constants for PTS Hub.
Single source of truth for all UI styling properties.
"""
from tkinter import ttk
import sv_ttk

# ── Main Background & Surface Colors ───────────────────────────────────────────
MAIN_BG = "#F0F0F0"              # Overarching application background
CARD_BG = "#F0F0F0"              # Card / container frame background
CONTAINER_BG = "#F0F0F0"         # Workspace and inner container background
CARD_BORDER = "#B0B0B0"          # Card perimeter border
BORDER_COLOR = "#B0B0B0"         # Generic border color

# ── Text Colors ───────────────────────────────────────────────────────────────
TEXT_FG = "#0F172A"              # Primary text color (deep slate)
TEXT_MUTED = "#475569"           # Secondary / muted text color
TEXT_DISABLED = "#94A3B8"        # Disabled state text

# ── Input Field Colors (ModernEntry) ─────────────────────────────────────────
ENTRY_BG = "#FFFFFF"             # Crisp white input fill
ENTRY_FG = "#0F172A"             # Dark text
ENTRY_DISABLED_BG = "#F0F0F0"    # Gray disabled background
ENTRY_DISABLED_FG = "#94A3B8"    # Muted disabled text
ENTRY_BORDER = "#E0E0E0"         # 1px perimeter border
ENTRY_FOCUS_BORDER = "#2563EB"   # Focused border highlight
ENTRY_SELECT_BG = "#2563EB"
ENTRY_SELECT_FG = "#FFFFFF"

# ── Standard Button Colors (ModernHoverButton) ────────────────────────────────
BTN_BG = "#FFFFFF"               # Button resting surface (pure white)
BTN_FG = "#0F172A"               # Button text
BTN_BORDER = "#E0E0E0"           # Button outline
BTN_HOVER_BG = "#E0F2FE"         # Sky light blue hover fill
BTN_HOVER_BORDER = "#7DD3FC"     # Hover border
BTN_HOVER_FG = "#0369A1"         # Hover text color
BTN_DISABLED_BG = "#F0F0F0"      # Blends with app background when disabled
BTN_DISABLED_FG = "#94A3B8"      # Muted disabled label
BTN_DISABLED_BORDER = "#E0E0E0"  # Faint border when disabled

# ── Primary Accent Button Colors (ModernAccentButton) ─────────────────────────
ACCENT_BTN_BG = "#2563EB"        # Vibrant blue (Run Analysis retains prominent accent)
ACCENT_BTN_FG = "#FFFFFF"        # White text
ACCENT_BTN_HOVER_BG = "#1D4ED8"  # Darker blue hover
ACCENT_BTN_HOVER_FG = "#FFFFFF"
ACCENT_BTN_DISABLED_BG = "#94A3B8"
ACCENT_BTN_DISABLED_FG = "#E2E8F0"

# ── Listbox & Console Log Colors ──────────────────────────────────────────────
LIST_BG = "#FFFFFF"              # Listbox background (explicit alias)
LISTBOX_BG = "#FFFFFF"           # Listbox fill
LISTBOX_FG = "#0F172A"
LISTBOX_SELECT_BG = "#2563EB"
LISTBOX_SELECT_FG = "#FFFFFF"
LISTBOX_BORDER = "#E0E0E0"

LOG_BG = "#FFFFFF"
LOG_FG = "#0F172A"
LOG_SELECT_BG = "#2563EB"
LOG_SELECT_FG = "#FFFFFF"
LOG_BORDER = "#E0E0E0"

# ── Status Badges & Pills ─────────────────────────────────────────────────────
BADGE_EMPTY_BG = "#CBD5E1"       # 0 files pill
BADGE_EMPTY_FG = "#334155"
BADGE_ACTIVE_BG = "#DBEAFE"      # Files loaded pill
BADGE_ACTIVE_FG = "#1D4ED8"
BADGE_READY_BG = "#DCFCE7"       # Ready / all panels loaded
BADGE_READY_FG = "#15803D"

# ── Top Navigation Bar Tokens (Dark Blue Theme) ──────────────────────────────
NAV_BG = "#172554"               # Dark blue header (Tailwind blue-950)
NAV_BORDER = "#1E3A8A"           # Dark blue bottom border (Tailwind blue-900)
NAV_TAB_BG = "#1E3A8A"           # Inactive button surface (rich dark blue)
NAV_TAB_BORDER = "#2563EB"       # Inactive button border (blue-600)
NAV_TAB_HOVER = "#2563EB"        # Hover fill
NAV_TAB_HOVER_BORDER = "#38BDF8" # Hover border
NAV_TAB_HOVER_FG = "#FFFFFF"     # Hover text
NAV_TAB_ACTIVE = "#2563EB"       # Vibrant blue active tab
NAV_TAB_ACTIVE_BORDER = "#60A5FA"# Active tab border highlight
NAV_TEXT = "#E2E8F0"             # Crisp light text for inactive tab
NAV_TEXT_ACTIVE = "#FFFFFF"      # Pure white text for active tab
NAV_TITLE_FG = "#FFFFFF"         # Pure white for navbar brand title
NAV_BADGE_BG = "#1E3A8A"         # Dark blue badge background
NAV_BADGE_FG = "#38BDF8"         # Sky blue badge text
NAV_BADGE_BORDER = "#2563EB"     # Badge outline
STATUS_ONLINE = "#10B981"        # Emerald online dot indicator

# ── Typography / Fonts ────────────────────────────────────────────────────────
FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_SUBTITLE = ("Segoe UI", 10)
FONT_CARD_TITLE = ("Segoe UI", 9, "bold")
FONT_LABEL = ("Segoe UI", 9, "bold")
FONT_LABEL_NORMAL = ("Segoe UI", 9, "normal")
FONT_ENTRY = ("Segoe UI", 9)
FONT_BUTTON = ("Segoe UI", 9, "bold")
FONT_BUTTON_ACCENT = ("Segoe UI", 12, "bold")
FONT_BUTTON_MINI = ("Segoe UI", 8, "bold")
FONT_BADGE = ("Segoe UI", 8, "bold")
FONT_STATUS = ("Segoe UI", 9)
FONT_LOG = ("Consolas", 9)

# ── Paddings & Metrics ────────────────────────────────────────────────────────
PAD_CARD_X = 8
PAD_CARD_Y = 6
PAD_BTN_X = 10
PAD_BTN_Y = 5
PAD_BTN_MINI_X = 6
PAD_BTN_MINI_Y = 2
PAD_ACCENT_BTN_X = 14
PAD_ACCENT_BTN_Y = 8


def setup_theme(root):
    """Initialize sv_ttk light theme and configure centralized #F0F0F0 background."""
    sv_ttk.set_theme("light")

    # Prevent sv_ttk's <<ThemeChanged>> handler from injecting #fafafa via tk_setPalette
    try:
        root.tk.eval(f"""
            proc configure_colors {{}} {{
                ttk::style configure . -background "{MAIN_BG}" -font SunValleyBodyFont
                tk_setPalette background "{MAIN_BG}" foreground "{TEXT_FG}" selectBackground "{ACCENT_BTN_BG}" selectForeground "#FFFFFF"
            }}
        """)
        root.tk.call("tk_setPalette", "background", MAIN_BG)
        root.update_idletasks()
    except Exception:
        pass

    style = ttk.Style()

    # Apply app background to root window
    try:
        root.configure(bg=MAIN_BG)
    except Exception:
        pass

    # Centralize ttk style backgrounds to MAIN_BG (#F0F0F0) and borders to BORDER_COLOR (#E0E0E0)
    style.configure(".", background=MAIN_BG)
    style.configure("TFrame", background=MAIN_BG)
    style.configure("TLabel", background=MAIN_BG, foreground=TEXT_FG)
    style.configure("TCheckbutton", background=MAIN_BG, foreground=TEXT_FG)
    style.configure("White.TCheckbutton", background="#FFFFFF", foreground=TEXT_FG)
    style.configure("TLabelframe", background=MAIN_BG, bordercolor=BORDER_COLOR)
    style.configure("TLabelframe.Label", background=MAIN_BG, foreground=TEXT_FG, font=FONT_CARD_TITLE)

    # Buttons and Inputs default styling
    style.configure("TButton", background=BTN_BG, foreground=BTN_FG, bordercolor=BORDER_COLOR)
    style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=ENTRY_FG, bordercolor=BORDER_COLOR)
    style.configure("Treeview", background=LIST_BG, fieldbackground=LIST_BG, foreground=TEXT_FG, bordercolor=BORDER_COLOR)
    style.configure("Treeview.Heading", background=MAIN_BG, foreground=TEXT_FG, bordercolor=BORDER_COLOR)

    style.configure("Accent.TButton", font=FONT_BUTTON_ACCENT, padding=(16, 14))
    style.configure("Action.TButton", font=FONT_BUTTON, padding=(10, 8))
    style.configure("MiniAction.TButton", font=FONT_BUTTON_MINI, padding=(6, 3))
    style.configure("CardHeader.TLabel", font=FONT_LABEL, background=MAIN_BG, foreground=TEXT_FG)
    style.configure("Subtext.TLabel", font=FONT_LABEL_NORMAL, background=MAIN_BG, foreground=TEXT_MUTED)
    style.configure("PanelTitle.TLabel", font=FONT_LABEL, background=MAIN_BG, foreground=TEXT_FG)

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

