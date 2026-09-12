"""
STDF2SUM Application User Interface Frame.
"""
from tkinter import ttk
from core.hub_window import HubWindow


class STDF2SumFrame(ttk.Frame):
    """STDF to Summary converter application frame."""

    def __init__(self, parent, hub=None):
        super().__init__(parent)
        self.hub = hub
        self._build_ui()

    def _build_ui(self):
        # Build standard placeholder view (ready for module implementation)
        card = HubWindow.create_placeholder_frame(
            self,
            title="STDF2SUM Converter",
            subtitle="Batch-convert STDF datalogs to standardized summary formats.",
            icon="📊",
        )
        card.pack(fill="both", expand=True)
