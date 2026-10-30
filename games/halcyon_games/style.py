"""The early-2000s game-client look: olive panels, one-pixel bevels, gold selection."""

BG = "#4C5844"        # window
PANEL = "#3E4637"     # sunken areas
LIGHT = "#889180"     # bevel highlight
DARK = "#282E22"      # bevel shadow
TEXT = "#D8DED3"
DIM = "#A0AA95"
GOLD = "#C4B550"      # titles, selected tab
SELECT = "#958831"    # selection background

FONT = '"Tahoma", "Verdana", "DejaVu Sans", sans-serif'

RAISED = f"border: 1px solid; border-color: {LIGHT} {DARK} {DARK} {LIGHT};"
SUNKEN = f"border: 1px solid; border-color: {DARK} {LIGHT} {LIGHT} {DARK};"

QSS = f"""
* {{ font-family: {FONT}; font-size: 9pt; color: {TEXT}; }}
#root {{ background: {BG}; {RAISED} }}
QDialog, QMessageBox, QInputDialog {{ background: {BG}; }}
#title {{ font-weight: bold; color: {TEXT}; }}
#cap {{ background: {BG}; {RAISED} min-width: 16px; max-width: 16px; min-height: 14px; max-height: 14px;
        padding: 0; font-size: 8pt; font-weight: bold; }}
#cap:pressed {{ {SUNKEN} }}
#bigpic {{ padding: 1px 10px; font-size: 8pt; font-weight: bold; letter-spacing: 1px; }}
#bigpic:hover {{ color: {GOLD}; }}

QPushButton, QToolButton {{ background: {BG}; {RAISED} padding: 4px 14px; }}
QPushButton:hover, QToolButton:hover {{ color: #FFFFFF; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: {PANEL}; {SUNKEN} }}
QPushButton:focus {{ border-color: {GOLD} {DARK} {DARK} {GOLD}; }}
QPushButton:default {{ font-weight: bold; }}
QPushButton:disabled {{ color: {DIM}; }}
QToolButton::menu-indicator {{ image: none; }}
#play {{ font-size: 11pt; font-weight: bold; padding: 6px 26px; color: #FFFFFF; }}

QLineEdit, QSpinBox, QComboBox {{ background: {PANEL}; {SUNKEN} padding: 2px 4px;
           selection-background-color: {SELECT}; selection-color: #FFFFFF; }}
QLineEdit:focus {{ border-color: {GOLD} {LIGHT} {LIGHT} {GOLD}; }}

#panel {{ background: {PANEL}; {SUNKEN} }}
#banner {{ background: {DARK}; {SUNKEN} }}
#gamename {{ font-size: 14pt; font-weight: bold; color: {GOLD}; }}
#gameinfo {{ color: {DIM}; }}
#status {{ color: {DIM}; }}

QTreeWidget {{ background: {PANEL}; alternate-background-color: #434C3B; {SUNKEN} outline: 0;
              selection-background-color: {SELECT}; selection-color: #FFFFFF; }}
QTreeWidget::item {{ padding: 2px 0; border: 0; }}
QTreeWidget::item:selected {{ background: {SELECT}; color: #FFFFFF; }}
QHeaderView {{ background: {BG}; }}
QHeaderView::section {{ background: {BG}; {RAISED} padding: 2px 6px; color: {TEXT}; }}
QHeaderView::section:pressed {{ {SUNKEN} }}

QTabBar::tab {{ background: {BG}; {RAISED} border-bottom: 0; padding: 4px 14px; margin-right: 2px; color: {DIM}; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {GOLD}; font-weight: bold; }}
QTabBar::tab:hover {{ color: #FFFFFF; }}

QScrollBar:vertical {{ background: {PANEL}; width: 16px; margin: 16px 0; border: 0; }}
QScrollBar:horizontal {{ background: {PANEL}; height: 16px; margin: 0 16px; border: 0; }}
QScrollBar::handle {{ background: {BG}; {RAISED} min-height: 20px; min-width: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ background: {BG}; {RAISED} width: 14px; height: 14px;
        subcontrol-origin: margin; }}
QScrollBar::sub-line:vertical {{ subcontrol-position: top; }}
QScrollBar::add-line:vertical {{ subcontrol-position: bottom; }}
QScrollBar::sub-line:horizontal {{ subcontrol-position: left; }}
QScrollBar::add-line:horizontal {{ subcontrol-position: right; }}
QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow, QScrollBar::right-arrow {{
        width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QMenu {{ background: {BG}; {RAISED} padding: 2px; }}
QMenu::item {{ padding: 3px 22px 3px 18px; }}
QMenu::item:selected {{ background: {SELECT}; color: #FFFFFF; }}
QMenu::item:disabled {{ color: {DIM}; }}
QMenu::separator {{ height: 1px; background: {DARK}; margin: 3px 4px; border-bottom: 1px solid {LIGHT}; }}
QMenu::indicator {{ width: 10px; height: 10px; left: 4px; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {DARK}; }}
"""
