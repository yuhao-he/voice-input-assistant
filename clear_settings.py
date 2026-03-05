"""Clear all saved VIA settings (QSettings)."""

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
import sys

app = QApplication(sys.argv)
app.setOrganizationName("VIA")
app.setApplicationName("VIA")

settings = QSettings()
settings.clear()
settings.sync()

print("Settings cleared.")

