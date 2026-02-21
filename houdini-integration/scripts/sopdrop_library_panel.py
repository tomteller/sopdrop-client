"""
Sopdrop Library Panel

Native Houdini Python Panel for browsing and managing your local asset library.
Works offline - syncs manually with cloud when desired.
"""

import hou
import os
import json
import zipfile
from datetime import datetime

# Try PySide6 first (Houdini 20+), fall back to PySide2
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    PYSIDE_VERSION = 6
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
        PYSIDE_VERSION = 2
    except ImportError:
        QtCore = None
        QtGui = None
        QtWidgets = None
        PYSIDE_VERSION = 0


# Import local modules
SOPDROP_AVAILABLE = False
SOPDROP_ERROR = None

try:
    from sopdrop import library
    from sopdrop.config import (
        get_token,
        get_library_ui_state,
        save_library_ui_state,
        get_ui_scale,
    )
    from sopdrop.importer import import_items
    SOPDROP_AVAILABLE = True
except ImportError as e:
    SOPDROP_ERROR = f"Import error: {e}"
except Exception as e:
    SOPDROP_ERROR = f"Error loading sopdrop: {e}"

# Import SnippingTool from sopdrop_ui
try:
    from sopdrop_ui import SnippingTool
    SNIPPING_AVAILABLE = True
except ImportError:
    SNIPPING_AVAILABLE = False


# ==============================================================================
# UI Scale
# ==============================================================================

# Load UI scale from config (1.0 = 100%)
if SOPDROP_AVAILABLE:
    UI_SCALE = get_ui_scale()
else:
    UI_SCALE = 1.0


def scale(px):
    """Scale a pixel value by the current UI scale factor."""
    return max(1, int(px * UI_SCALE))


# ==============================================================================
# Theme Colors - Houdini-inspired Dark UI
# ==============================================================================

COLORS = {
    # Backgrounds - layered depth (darker base, brighter cards)
    'bg_base': '#191919',      # Deepest background (panel frame, sidebar)
    'bg_dark': '#1e1e1e',      # Main background
    'bg_medium': '#242424',    # Controls, inputs
    'bg_light': '#2e2e2e',     # Elevated elements (buttons)
    'bg_lighter': '#383838',   # Hover states
    'bg_hover': '#424242',     # Active hover
    'bg_selected': '#3d3020',  # Orange-tinted selection
    'bg_card': '#2a2a2a',      # Card background (brighter than grid area)
    'bg_card_hover': '#323232', # Card hover
    'bg_grid': '#202020',      # Grid area background (between sidebar and cards)

    # Borders - subtle but present
    'border': '#333333',       # Default borders
    'border_light': '#444444', # More visible borders
    'border_focus': '#f97316', # Focus state

    # Text hierarchy - better contrast
    'text': '#cccccc',         # Primary text (Houdini default)
    'text_secondary': '#999999', # Secondary text
    'text_dim': '#777777',     # Dimmed text (more readable)
    'text_bright': '#ffffff',  # Emphasized text

    # Brand colors - Sopdrop orange
    'accent': '#f97316',       # Primary orange
    'accent_dim': '#d96a14',   # Darker orange
    'accent_hover': '#ff8c3a', # Lighter orange hover
    'accent_glow': 'rgba(249, 115, 22, 0.12)', # Subtle glow

    # Status colors
    'success': '#4ade80',
    'success_dim': 'rgba(74, 222, 128, 0.15)',
    'warning': '#facc15',
    'warning_dim': 'rgba(250, 204, 21, 0.15)',
    'error': '#f87171',
    'error_dim': 'rgba(248, 113, 113, 0.15)',

    # Context colors - Houdini-style
    'sop': '#5b9bd5',    # Blue
    'lop': '#f97316',    # Orange
    'obj': '#e6b422',    # Yellow/Gold
    'vop': '#b07dd0',    # Purple
    'dop': '#e06666',    # Red
    'cop': '#4ecdc4',    # Cyan
    'top': '#7bc96f',    # Green
    'chop': '#e091c0',   # Pink
    'rop': '#8b8be0',    # Indigo
    'out': '#8b8be0',    # Indigo (alias)
    'vex': '#e6b422',    # Gold/Amber
}


def get_context_color(context):
    """Get the color for a Houdini context."""
    return COLORS.get(context.lower(), COLORS['text_dim'])


# ==============================================================================
# Modern Stylesheet - Sleek, minimal, polished
# ==============================================================================

def build_stylesheet(s=None):
    """Build the stylesheet with scaled font sizes and paddings."""
    if s is None:
        s = UI_SCALE
    fs = max(8, int(11 * s))  # base font size
    return f"""
/* Base styling - Houdini-like */
QWidget {{
    background-color: {COLORS['bg_dark']};
    color: {COLORS['text']};
    font-size: {fs}px;
}}

/* Text inputs */
QLineEdit {{
    background-color: {COLORS['bg_medium']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 4px 8px;
    color: {COLORS['text']};
    selection-background-color: {COLORS['accent']};
}}

QLineEdit:hover {{
    border-color: {COLORS['border_light']};
}}

QLineEdit:focus {{
    border-color: {COLORS['accent']};
}}

QLineEdit::placeholder {{
    color: {COLORS['text_dim']};
}}

QTextEdit {{
    background-color: {COLORS['bg_medium']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 4px;
    color: {COLORS['text']};
}}

QTextEdit:focus {{
    border-color: {COLORS['accent']};
}}

/* Buttons - Houdini style */
QPushButton {{
    background-color: {COLORS['bg_light']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 4px 12px;
    color: {COLORS['text']};
}}

QPushButton:hover {{
    background-color: {COLORS['bg_lighter']};
    border-color: {COLORS['border_light']};
}}

QPushButton:pressed {{
    background-color: {COLORS['bg_hover']};
}}

QPushButton[class="primary"] {{
    background-color: {COLORS['accent']};
    color: white;
    border: none;
}}

QPushButton[class="primary"]:hover {{
    background-color: {COLORS['accent_hover']};
}}

QPushButton[class="primary"]:pressed {{
    background-color: {COLORS['accent_dim']};
}}

/* Dropdown */
QComboBox {{
    background-color: {COLORS['bg_medium']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 4px 8px;
    min-height: 18px;
}}

QComboBox:hover {{
    border-color: {COLORS['border_light']};
}}

QComboBox:focus {{
    border-color: {COLORS['accent']};
}}

QComboBox::drop-down {{
    border: none;
    width: 16px;
}}

QComboBox::down-arrow {{
    image: none;
    border: none;
}}

QComboBox QAbstractItemView {{
    background-color: {COLORS['bg_light']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 2px;
    selection-background-color: {COLORS['bg_selected']};
    outline: none;
}}

QComboBox QAbstractItemView::item {{
    padding: 4px 8px;
    border-radius: 2px;
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: {COLORS['bg_selected']};
}}

/* Scrollbars */
QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background-color: {COLORS['bg_medium']};
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: {COLORS['border_light']};
    border-radius: 2px;
    min-height: 20px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {COLORS['text_dim']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background-color: {COLORS['bg_medium']};
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background-color: {COLORS['border_light']};
    border-radius: 2px;
    min-width: 20px;
    margin: 2px;
}}

/* Splitter */
QSplitter::handle {{
    background-color: {COLORS['border']};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:hover {{
    background-color: {COLORS['accent']};
}}

/* Menus - compact */
QMenu {{
    background-color: {COLORS['bg_light']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    padding: 4px;
}}

QMenu::item {{
    background-color: transparent;
    padding: 6px 12px;
    border-radius: 2px;
}}

QMenu::item:selected {{
    background-color: {COLORS['bg_selected']};
}}

QMenu::item:disabled {{
    color: {COLORS['text_dim']};
}}

QMenu::separator {{
    height: 1px;
    background-color: {COLORS['border']};
    margin: 4px 2px;
}}

QMenu::indicator {{
    width: 12px;
    height: 12px;
    margin-left: 4px;
}}

QMenu::indicator:checked {{
    background-color: {COLORS['accent']};
    border-radius: 2px;
}}

/* Tooltips */
QToolTip {{
    background-color: {COLORS['bg_light']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 4px 8px;
    font-size: {fs}px;
}}

/* Labels */
QLabel {{
    color: {COLORS['text']};
    background: transparent;
}}

/* Checkboxes */
QCheckBox {{
    spacing: 6px;
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid {COLORS['border_light']};
    background-color: {COLORS['bg_medium']};
}}

QCheckBox::indicator:hover {{
    border-color: {COLORS['accent']};
}}

QCheckBox::indicator:checked {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent']};
}}
"""

STYLESHEET = build_stylesheet()


# ==============================================================================
# Tag Widget
# ==============================================================================

class TagPill(QtWidgets.QFrame):
    """A compact tag widget."""

    clicked = QtCore.Signal(str)
    remove_clicked = QtCore.Signal(str)

    def __init__(self, tag, removable=False, parent=None):
        super().__init__(parent)
        self.tag = tag
        self.removable = removable
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("tagPill")
        self.setFixedHeight(20)
        self.setStyleSheet(f"""
            QFrame#tagPill {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
            }}
            QFrame#tagPill:hover {{
                border-color: {COLORS['accent']};
                background-color: {COLORS['bg_lighter']};
            }}
        """)
        self.setCursor(QtCore.Qt.PointingHandCursor)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6 if not self.removable else 4, 0)
        layout.setSpacing(2)

        self.label = QtWidgets.QLabel(self.tag)
        self.label.setStyleSheet(f"""
            color: {COLORS['text']};
            font-size: 10px;
            background: transparent;
        """)
        layout.addWidget(self.label)

        if self.removable:
            remove_btn = QtWidgets.QLabel("×")
            remove_btn.setStyleSheet(f"""
                color: {COLORS['text_dim']};
                font-size: 12px;
                background: transparent;
            """)
            remove_btn.setCursor(QtCore.Qt.PointingHandCursor)
            remove_btn.mousePressEvent = lambda e: self.remove_clicked.emit(self.tag)
            layout.addWidget(remove_btn)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.tag)


# ==============================================================================
# Toast Notification Widget
# ==============================================================================

class ToastWidget(QtWidgets.QFrame):
    """A toast notification widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        self.setFixedHeight(28)
        self.setStyleSheet(f"""
            QFrame#toast {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
            }}
        """)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)

        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(14, 14)
        self.icon_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.icon_label)

        self.message_label = QtWidgets.QLabel()
        self.message_label.setStyleSheet(f"""
            color: {COLORS['text']};
            font-size: 11px;
            background: transparent;
        """)
        layout.addWidget(self.message_label, 1)

        self.action_btn = QtWidgets.QPushButton()
        self.action_btn.setFixedHeight(18)
        self.action_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.action_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid {COLORS['accent']};
                border-radius: 3px;
                color: {COLORS['accent']};
                font-size: 10px;
                padding: 0 8px;
            }}
            QPushButton:hover {{
                background: {COLORS['accent']};
                color: white;
            }}
        """)
        self.action_btn.hide()
        layout.addWidget(self.action_btn)

    def show_message(self, message, toast_type='info', duration=3000, action_text=None, action_callback=None):
        """Show a toast message with modern styling."""
        # Disconnect previous action if any
        try:
            self.action_btn.clicked.disconnect()
        except RuntimeError:
            pass

        if action_text and action_callback:
            self.action_btn.setText(action_text)
            self.action_btn.clicked.connect(lambda: (action_callback(), self.hide()))
            self.action_btn.show()
        else:
            self.action_btn.hide()
        icons = {
            'info': '●',
            'success': '✓',
            'warning': '!',
            'error': '×',
        }
        colors = {
            'info': COLORS['accent'],
            'success': COLORS['success'],
            'warning': COLORS['warning'],
            'error': COLORS['error'],
        }
        bg_colors = {
            'info': COLORS['bg_light'],
            'success': COLORS['success_dim'],
            'warning': COLORS['warning_dim'],
            'error': COLORS['error_dim'],
        }

        color = colors.get(toast_type, colors['info'])
        bg_color = bg_colors.get(toast_type, bg_colors['info'])
        icon = icons.get(toast_type, icons['info'])

        self.icon_label.setText(icon)
        self.icon_label.setStyleSheet(f"""
            color: {color};
            font-size: 12px;
            background: transparent;
        """)
        self.message_label.setText(message)

        self.setStyleSheet(f"""
            QFrame#toast {{
                background-color: {bg_color};
                border: 1px solid {color};
                border-radius: 4px;
            }}
        """)

        self.show()
        self.raise_()

        if duration > 0:
            self._timer.start(duration)

    def _fade_out(self):
        self.hide()


class _CheckboxPopup(QtWidgets.QFrame):
    """A popup with checkboxes that stays open until the user clicks outside."""

    def __init__(self, parent=None, max_height=0):
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
            }}
            QCheckBox {{
                color: {COLORS['text']};
                font-size: 11px;
                padding: 4px 8px;
                spacing: 6px;
            }}
            QCheckBox:hover {{
                background-color: {COLORS['bg_hover']};
                border-radius: 2px;
            }}
            QCheckBox::indicator {{
                width: 12px;
                height: 12px;
                border: 1px solid {COLORS['border_light']};
                border-radius: 2px;
                background: {COLORS['bg_dark']};
            }}
            QCheckBox::indicator:checked {{
                background-color: {COLORS['accent']};
                border-color: {COLORS['accent']};
            }}
            QLabel {{
                color: {COLORS['text_dim']};
                font-size: 10px;
                padding: 4px 8px;
            }}
            QPushButton {{
                background: transparent;
                border: none;
                color: {COLORS['accent']};
                font-size: 10px;
                padding: 4px 8px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_hover']};
                border-radius: 2px;
            }}
        """)

        self._max_height = max_height
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(4, 6, 4, 6)
        self._layout.setSpacing(0)

        if max_height:
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
            scroll.setMaximumHeight(max_height)
            self._scroll_content = QtWidgets.QWidget()
            self._scroll_content.setStyleSheet("background: transparent;")
            self._inner_layout = QtWidgets.QVBoxLayout(self._scroll_content)
            self._inner_layout.setContentsMargins(0, 0, 0, 0)
            self._inner_layout.setSpacing(0)
            scroll.setWidget(self._scroll_content)
            self._layout.addWidget(scroll)
        else:
            self._inner_layout = self._layout

    def add_checkbox(self, text, checked, callback):
        cb = QtWidgets.QCheckBox(text)
        cb.setChecked(checked)
        cb.toggled.connect(callback)
        self._inner_layout.addWidget(cb)

    def add_separator(self):
        sep = QtWidgets.QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {COLORS['border']}; margin: 3px 4px;")
        self._inner_layout.addWidget(sep)

    def add_label(self, text):
        label = QtWidgets.QLabel(text)
        self._inner_layout.addWidget(label)

    def add_button(self, text, callback):
        btn = QtWidgets.QPushButton(text)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.clicked.connect(callback)
        self._inner_layout.addWidget(btn)

    def show_at(self, pos):
        self.adjustSize()
        self.move(pos)
        self.show()


class FlowLayout(QtWidgets.QLayout):
    """A layout that flows widgets left-to-right, wrapping as needed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._spacing = 3

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def setSpacing(self, spacing):
        self._spacing = spacing

    def spacing(self):
        return self._spacing

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect)

    def _do_layout(self, rect):
        x = rect.x()
        y = rect.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            if not widget:
                continue

            space = self._spacing
            next_x = x + item.sizeHint().width() + space

            if next_x - space > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space
                next_x = x + item.sizeHint().width() + space
                line_height = 0

            item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())


class TagFlowWidget(QtWidgets.QWidget):
    """A widget that displays tags as pills in a flowing layout."""

    tag_clicked = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tags = []
        self._layout = FlowLayout(self)
        self._layout.setSpacing(3)

    def set_tags(self, tags, max_tags=3):
        """Set the tags to display."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.tags = tags or []
        for tag in self.tags[:max_tags]:
            pill = TagPill(tag)
            pill.clicked.connect(self.tag_clicked.emit)
            self._layout.addWidget(pill)

        if len(self.tags) > max_tags:
            more = QtWidgets.QLabel(f"+{len(self.tags) - max_tags}")
            more.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 9px;")
            self._layout.addWidget(more)


# ==============================================================================
# Tag Input with Auto-Complete
# ==============================================================================

class TagInputWidget(QtWidgets.QWidget):
    """Tag input with auto-complete suggestions from existing tags."""

    tags_changed = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tags = []
        self._all_tags = []
        self._setup_ui()
        self._load_existing_tags()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Tag pills container
        self.pills_widget = QtWidgets.QWidget()
        self.pills_layout = FlowLayout(self.pills_widget)
        self.pills_layout.setSpacing(3)
        layout.addWidget(self.pills_widget)

        # Input with completer
        self.input = TagLineEdit()
        self.input.setPlaceholderText("Add tags...")
        self.input.setFixedHeight(22)
        self.input.tag_submitted.connect(self._add_tag)
        layout.addWidget(self.input)

        # Setup completer
        self.completer = QtWidgets.QCompleter(self)
        self.completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.completer.setFilterMode(QtCore.Qt.MatchContains)
        self.completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self.input.setCompleter(self.completer)

        # Clickable suggestions
        self.suggestions_widget = QtWidgets.QWidget()
        self.suggestions_layout = FlowLayout(self.suggestions_widget)
        self.suggestions_layout.setSpacing(3)
        layout.addWidget(self.suggestions_widget)

        self._update_suggestions()

    def _load_existing_tags(self):
        """Load existing tags from the library."""
        if SOPDROP_AVAILABLE:
            try:
                all_tags = library.get_all_tags()
                self._all_tags = [t['tag'] for t in all_tags]
                model = QtCore.QStringListModel(self._all_tags)
                self.completer.setModel(model)
                # Update suggestions now that tags are loaded
                self._update_suggestions()
            except Exception:
                self._all_tags = []

    def _update_suggestions(self):
        """Update clickable tag suggestions."""
        # Clear existing suggestions
        while self.suggestions_layout.count():
            item = self.suggestions_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Show tags not yet added (up to 6)
        remaining = [t for t in self._all_tags if t not in self._tags][:6]
        if remaining:
            # Add "Try:" label
            try_label = QtWidgets.QLabel("Try:")
            try_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 9px; background: transparent;")
            self.suggestions_layout.addWidget(try_label)

            # Add clickable tag pills
            for tag in remaining:
                pill = TagPill(tag, removable=False)
                pill.clicked.connect(self._add_tag)
                self.suggestions_layout.addWidget(pill)

            self.suggestions_widget.show()
        else:
            self.suggestions_widget.hide()

    def _add_tag(self, tag):
        """Add a tag (from input or suggestion click)."""
        tag = tag.strip().lower()
        if tag and tag not in self._tags:
            self._tags.append(tag)
            self._refresh_pills()
            self.tags_changed.emit(self._tags)
        self.input.clear()
        self._update_suggestions()

    def _refresh_pills(self):
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for tag in self._tags:
            pill = TagPill(tag, removable=True)
            pill.remove_clicked.connect(self._remove_tag)
            self.pills_layout.addWidget(pill)

    def _remove_tag(self, tag):
        if tag in self._tags:
            self._tags.remove(tag)
            self._refresh_pills()
            self.tags_changed.emit(self._tags)
            self._update_suggestions()

    def set_tags(self, tags):
        self._tags = list(tags) if tags else []
        self._refresh_pills()
        self._update_suggestions()

    def get_tags(self):
        return self._tags


class TagLineEdit(QtWidgets.QLineEdit):
    """Line edit that properly handles autocomplete + Enter key."""

    tag_submitted = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def keyPressEvent(self, event):
        """Handle Enter key to use completer selection if available."""
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            completer = self.completer()
            if completer and completer.popup() and completer.popup().isVisible():
                # Get the currently highlighted completion
                index = completer.popup().currentIndex()
                if index.isValid():
                    # Use the highlighted completion
                    selected_text = completer.completionModel().data(index)
                    if selected_text:
                        self.tag_submitted.emit(selected_text)
                        completer.popup().hide()
                        return
            # No completion selected, use the typed text
            if self.text().strip():
                self.tag_submitted.emit(self.text())
            return

        super().keyPressEvent(event)


# ==============================================================================
# Asset Hover Popover
# ==============================================================================

class AssetPopover(QtWidgets.QFrame):
    """Floating popover that shows asset details on hover."""

    _instance = None  # Singleton - only one popover visible at a time

    def __init__(self, parent=None):
        # Use Popup flag instead of ToolTip so it hides on alt-tab
        super().__init__(parent, QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
        self.setFixedWidth(scale(280))
        self._asset = None
        self._setup_ui()

        # Watch for application deactivation to hide popover
        app = QtWidgets.QApplication.instance()
        if app:
            app.applicationStateChanged.connect(self._on_app_state_changed)

    def _on_app_state_changed(self, state):
        """Hide popover when application loses focus."""
        if state != QtCore.Qt.ApplicationActive:
            self.hide()

    def _setup_ui(self):
        self.setObjectName("assetPopover")
        self.setStyleSheet(f"""
            QFrame#assetPopover {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border_light']};
                border-radius: 6px;
            }}
            QLabel {{
                background: transparent;
            }}
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Row 1: Name + context badge
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(6)
        self.name_label = QtWidgets.QLabel()
        self.name_label.setStyleSheet(f"color: {COLORS['text_bright']}; font-size: 12px; font-weight: 600;")
        self.name_label.setWordWrap(True)
        row1.addWidget(self.name_label, 1)

        self.ctx_badge = QtWidgets.QLabel()
        self.ctx_badge.setAlignment(QtCore.Qt.AlignCenter)
        row1.addWidget(self.ctx_badge, 0, QtCore.Qt.AlignTop)
        layout.addLayout(row1)

        # Description
        self.desc_label = QtWidgets.QLabel()
        self.desc_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        self.desc_label.setWordWrap(True)
        self.desc_label.setMaximumHeight(40)
        layout.addWidget(self.desc_label)

        # Separator
        sep = QtWidgets.QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {COLORS['border']};")
        layout.addWidget(sep)

        # Metadata row 1: nodes, version, usage
        self.meta_label = QtWidgets.QLabel()
        self.meta_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 9px;")
        layout.addWidget(self.meta_label)

        # Metadata row 2: node types, houdini version, file size
        self.meta2_label = QtWidgets.QLabel()
        self.meta2_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 9px;")
        self.meta2_label.setWordWrap(True)
        layout.addWidget(self.meta2_label)

        # Collections row
        self.colls_label = QtWidgets.QLabel()
        self.colls_label.setStyleSheet(f"color: {COLORS['accent']}; font-size: 9px;")
        layout.addWidget(self.colls_label)

        # Tags row
        self.tags_container = QtWidgets.QWidget()
        self.tags_container.setStyleSheet("background: transparent;")
        self.tags_layout = FlowLayout(self.tags_container)
        self.tags_layout.setSpacing(4)
        layout.addWidget(self.tags_container)

    def show_for_asset(self, asset, global_pos):
        """Show the popover for the given asset near the given position."""
        self._asset = asset
        if not asset:
            self.hide()
            return

        name = asset.get('name', 'Untitled')
        self.name_label.setText(name)

        # Context badge
        context = asset.get('context', 'sop')
        ctx_color = get_context_color(context)
        asset_type = asset.get('asset_type', 'node')
        badge_text = context.upper()
        if asset_type == 'hda':
            badge_text += " HDA"
        elif asset_type == 'vex':
            badge_text = "VEX"
        self.ctx_badge.setText(badge_text)
        self.ctx_badge.setStyleSheet(f"""
            background-color: {ctx_color};
            color: white;
            font-size: 9px;
            font-weight: bold;
            padding: 2px 6px;
            border-radius: 2px;
        """)

        # Description
        desc = asset.get('description', '')
        if desc:
            self.desc_label.setText(desc)
            self.desc_label.show()
        else:
            self.desc_label.hide()

        # Metadata row 1: node count, version, usage, date
        parts = []
        node_count = asset.get('node_count', 0)
        if node_count:
            parts.append(f"{node_count} nodes")

        version = asset.get('remote_version') or asset.get('hda_version')
        if version:
            parts.append(f"v{version}")

        use_count = asset.get('use_count', 0)
        if use_count:
            parts.append(f"used {use_count}x")

        created = asset.get('created_at', '')
        if created:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                parts.append(dt.strftime("%b %d, %Y"))
            except:
                pass

        self.meta_label.setText("  ·  ".join(parts) if parts else "")
        self.meta_label.setVisible(bool(parts))

        # Metadata row 2: node types, houdini version, file size
        parts2 = []
        node_types = asset.get('node_types')
        if node_types:
            if isinstance(node_types, str):
                try:
                    node_types = json.loads(node_types)
                except:
                    node_types = []
            if node_types:
                if len(node_types) <= 3:
                    parts2.append(", ".join(node_types))
                else:
                    parts2.append(f"{', '.join(node_types[:3])} +{len(node_types)-3}")

        houdini_ver = asset.get('houdini_version', '')
        if houdini_ver:
            parts2.append(f"H{houdini_ver}")

        file_size = asset.get('file_size', 0)
        if file_size:
            if file_size > 1048576:
                parts2.append(f"{file_size / 1048576:.1f} MB")
            elif file_size > 1024:
                parts2.append(f"{file_size / 1024:.0f} KB")

        self.meta2_label.setText("  ·  ".join(parts2) if parts2 else "")
        self.meta2_label.setVisible(bool(parts2))

        # Collections
        collections = asset.get('collections', [])
        if collections:
            coll_names = []
            for c in collections:
                if isinstance(c, dict):
                    coll_names.append(c.get('name', ''))
                elif isinstance(c, str):
                    coll_names.append(c)
            coll_text = "▪ " + ", ".join(n for n in coll_names if n)
            self.colls_label.setText(coll_text)
            self.colls_label.show()
        else:
            self.colls_label.hide()

        # Tags
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        tags = asset.get('tags', [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except:
                tags = []

        if tags:
            self.tags_container.show()
            for tag in tags[:6]:
                pill = QtWidgets.QLabel(tag)
                pill.setStyleSheet(f"""
                    background-color: {COLORS['bg_light']};
                    color: {COLORS['text_secondary']};
                    font-size: 9px;
                    padding: 1px 5px;
                    border-radius: 2px;
                """)
                self.tags_layout.addWidget(pill)
        else:
            self.tags_container.hide()

        # Size to content
        self.adjustSize()

        # Position: to the right of the cursor, offset slightly
        # Make sure it doesn't go off-screen
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            screen_rect = screen.availableGeometry()
            x = global_pos.x() + 16
            y = global_pos.y() - 10

            # Flip to left if would go off right edge
            if x + self.width() > screen_rect.right():
                x = global_pos.x() - self.width() - 16

            # Flip up if would go off bottom
            if y + self.height() > screen_rect.bottom():
                y = screen_rect.bottom() - self.height() - 4

            # Clamp top
            y = max(screen_rect.top(), y)

            self.move(x, y)

        self.show()
        self.raise_()

    @classmethod
    def instance(cls):
        """Get or create the singleton popover."""
        if cls._instance is None or not cls._instance.isVisible():
            try:
                # Check if previous instance was deleted
                if cls._instance is not None:
                    cls._instance.objectName()  # Will throw if deleted
            except RuntimeError:
                cls._instance = None
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def hide_popover(cls):
        """Hide the singleton popover if visible."""
        if cls._instance is not None:
            try:
                cls._instance.hide()
            except RuntimeError:
                cls._instance = None


# ==============================================================================
# Collection List Widget
# ==============================================================================

class _DropAwareContainer(QtWidgets.QWidget):
    """Container widget that accepts drops and delegates to its owner for hit-testing.

    Qt sends drag events only to the deepest widget with acceptDrops=True.
    By making the container itself accept drops (rather than a distant ancestor),
    we ensure drag events are always caught regardless of child widget hierarchy.
    """

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/x-sopdrop-assets'):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat('application/x-sopdrop-assets'):
            event.acceptProposedAction()
            self._owner._on_drag_move(event.pos())
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._owner._set_drop_highlight(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat('application/x-sopdrop-assets'):
            self._owner._on_drop(event)
        else:
            event.ignore()


class CollectionListWidget(QtWidgets.QWidget):
    """Sidebar for browsing collections with subfolder support."""

    collection_selected = QtCore.Signal(object)

    # Class-level reference so AssetCardWidget can find us during drag
    _active_instance = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = set()  # Track expanded folder IDs
        self._all_items = []  # Track all item widgets for selection
        self._highlighted_btn = None  # Currently drop-highlighted button
        CollectionListWidget._active_instance = self
        self._setup_ui()
        self.refresh()

    # -- Drag-drop handling ---------------------------------------------------

    def _on_drag_move(self, pos):
        """Handle drag move at the given container-local position."""
        btn = self._find_collection_at_pos(pos)
        self._set_drop_highlight(btn)

    def _on_drop(self, event):
        """Handle drop event from the container."""
        btn = self._find_collection_at_pos(event.pos())
        self._set_drop_highlight(None)
        if btn:
            coll_id = btn.property("item_id")
            data = event.mimeData().data('application/x-sopdrop-assets').data().decode()
            asset_ids = [aid for aid in data.split(',') if aid]
            event.acceptProposedAction()
            if SOPDROP_AVAILABLE and asset_ids:
                for aid in asset_ids:
                    library.add_asset_to_collection(aid, coll_id)
                self.collection_selected.emit(coll_id)
        else:
            event.ignore()

    def _find_collection_at_pos(self, pos):
        """Find the collection button at the given position (container coords)."""
        for btn in self._all_items:
            if not btn or not btn.isVisible():
                continue
            item_id = btn.property("item_id")
            # Only real collections are drop targets (not system items)
            if item_id and item_id is not None and not str(item_id).startswith("__"):
                btn_pos = btn.mapFrom(self.container, pos)
                if btn.rect().contains(btn_pos):
                    return btn
        return None

    def _set_drop_highlight(self, btn):
        """Highlight a collection button as a drop target, unhighlight previous."""
        prev = self._highlighted_btn
        if prev == btn:
            return
        if prev:
            base = prev.property("base_style")
            if base:
                prev.setStyleSheet(base)
        if btn:
            highlight = btn.property("highlight_style")
            if highlight:
                btn.setStyleSheet(highlight)
        self._highlighted_btn = btn

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container (accepts drops — delegates to self for hit-testing)
        self.container = _DropAwareContainer(owner=self)
        self.container.setStyleSheet(f"""
            background-color: {COLORS['bg_base']};
            border-radius: 3px;
        """)
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(8, 12, 8, 12)
        container_layout.setSpacing(4)

        # System items section
        self.system_layout = QtWidgets.QVBoxLayout()
        self.system_layout.setSpacing(0)
        container_layout.addLayout(self.system_layout)

        # Separator
        sep_container = QtWidgets.QWidget()
        sep_container.setFixedHeight(16)
        sep_layout = QtWidgets.QHBoxLayout(sep_container)
        sep_layout.setContentsMargins(8, 0, 8, 0)
        sep = QtWidgets.QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {COLORS['border']};")
        sep_layout.addWidget(sep)
        container_layout.addWidget(sep_container)

        # Collections header
        coll_header = QtWidgets.QHBoxLayout()
        coll_header.setContentsMargins(8, 0, 4, 4)
        coll_label = QtWidgets.QLabel("COLLECTIONS")
        coll_label.setStyleSheet(f"""
            font-size: 9px;
            font-weight: 600;
            letter-spacing: 1px;
            color: {COLORS['text_dim']};
        """)
        coll_header.addWidget(coll_label)
        coll_header.addStretch()

        # Add folder button
        add_btn = QtWidgets.QPushButton("+")
        add_btn.setFixedSize(18, 18)
        add_btn.setCursor(QtCore.Qt.PointingHandCursor)
        add_btn.setToolTip("New collection")
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 2px;
                color: {COLORS['text_secondary']};
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_light']};
                color: {COLORS['accent']};
            }}
        """)
        add_btn.clicked.connect(lambda: self._add_collection(None))
        coll_header.addWidget(add_btn)
        container_layout.addLayout(coll_header)

        # Collections tree container
        self.collections_layout = QtWidgets.QVBoxLayout()
        self.collections_layout.setSpacing(0)
        container_layout.addLayout(self.collections_layout)

        container_layout.addStretch()
        layout.addWidget(self.container)

    def refresh(self):
        """Refresh the collections list."""
        self._all_items = []

        # Clear system layout
        while self.system_layout.count():
            item = self.system_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Clear collections layout
        while self.collections_layout.count():
            item = self.collections_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # System items
        all_item = self._create_item("All Assets", None, icon="◎", bold=True)
        self.system_layout.addWidget(all_item)
        self._all_items.append(all_item)

        recent_item = self._create_item("Recent", "__recent__", icon="◷")
        self.system_layout.addWidget(recent_item)
        self._all_items.append(recent_item)

        favorites_item = self._create_item("Most Used", "__favorites__", icon="★")
        self.system_layout.addWidget(favorites_item)
        self._all_items.append(favorites_item)

        # Build collection tree (only local collections, no cloud auto-collections)
        if SOPDROP_AVAILABLE:
            tree = library.get_collection_tree()
            self._build_tree(tree, self.collections_layout, depth=0)

        # Select "All Assets" by default
        all_item.setProperty("selected", True)
        all_item.style().unpolish(all_item)
        all_item.style().polish(all_item)

    def _build_tree(self, items, layout, depth=0):
        """Recursively build the collection tree."""
        for coll in items:
            # Skip cloud-sourced collections
            if coll.get('source') == 'cloud':
                continue

            has_children = bool(coll.get('children'))
            is_expanded = coll['id'] in self._expanded

            item = self._create_collection_item(coll, depth, has_children, is_expanded)
            layout.addWidget(item)
            self._all_items.append(item)

            # Add children if expanded
            if has_children and is_expanded:
                self._build_tree(coll['children'], layout, depth + 1)

    def _create_item(self, text, item_id, icon="", bold=False):
        """Create a system item (All Assets, Recent, etc)."""
        btn = QtWidgets.QPushButton()
        btn.setProperty("item_id", item_id)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setFixedHeight(20)

        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 2px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_light']};
            }}
            QPushButton[selected="true"] {{
                background-color: {COLORS['accent_glow']};
            }}
        """)

        layout = QtWidgets.QHBoxLayout(btn)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        if icon:
            icon_label = QtWidgets.QLabel(icon)
            icon_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px; background: transparent;")
            icon_label.setFixedWidth(14)
            layout.addWidget(icon_label)

        font_weight = "600" if bold else "400"
        label = QtWidgets.QLabel(text)
        label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: {font_weight}; background: transparent;")
        layout.addWidget(label)
        layout.addStretch()

        btn.clicked.connect(lambda: self._on_item_clicked(btn))
        return btn

    def _create_collection_item(self, coll, depth=0, has_children=False, is_expanded=False):
        """Create a collection item with subfolder support.

        Drag-drop is handled at the CollectionListWidget container level.
        For folders with children, a separate arrow button handles
        expand/collapse independently of collection selection.
        """
        coll_id = coll['id']
        indent = 8 + (depth * 16)

        base_style = f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 2px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_light']};
            }}
            QPushButton[selected="true"] {{
                background-color: {COLORS['accent_glow']};
            }}
        """
        highlight_style = f"""
            QPushButton {{
                background-color: rgba(249, 115, 22, 0.15);
                border: 2px solid {COLORS['accent']};
                border-radius: 2px;
                text-align: left;
            }}
        """

        btn = QtWidgets.QPushButton()
        btn.setProperty("item_id", coll_id)
        btn.setProperty("collection_data", coll)
        btn.setProperty("base_style", base_style)
        btn.setProperty("highlight_style", highlight_style)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setFixedHeight(20)
        btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        btn.customContextMenuRequested.connect(lambda pos, b=btn: self._show_context_menu(b, pos))
        btn.setStyleSheet(base_style)

        # Inner layout: [arrow or spacer] [dot + name]
        inner = QtWidgets.QHBoxLayout(btn)
        inner.setContentsMargins(indent, 0, 8, 0)
        inner.setSpacing(2)

        if has_children:
            arrow_btn = QtWidgets.QToolButton()
            arrow_btn.setText("\u25BC" if is_expanded else "\u25B6")
            arrow_btn.setFixedSize(14, 16)
            arrow_btn.setAutoRaise(True)
            arrow_btn.setCursor(QtCore.Qt.PointingHandCursor)
            arrow_btn.setStyleSheet(f"""
                QToolButton {{
                    background: transparent;
                    border: none;
                    color: {COLORS['text_dim']};
                    font-size: 8px;
                    padding: 0;
                }}
                QToolButton:hover {{
                    color: {COLORS['text']};
                }}
            """)
            arrow_btn.clicked.connect(lambda checked=False, c=coll: self._toggle_expand(c))
            inner.addWidget(arrow_btn)
        else:
            # Invisible spacer same width as arrow so names align across depth levels
            spacer = QtWidgets.QWidget()
            spacer.setFixedSize(14, 16)
            spacer.setStyleSheet("background: transparent;")
            spacer.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            inner.addWidget(spacer)

        coll_color = coll.get('color', '') or COLORS['text_dim']
        color_chip = QtWidgets.QWidget()
        color_chip.setFixedSize(8, 8)
        color_chip.setStyleSheet(f"""
            background-color: {coll_color};
            border-radius: 2px;
        """)
        color_chip.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        inner.addWidget(color_chip)
        inner.addSpacing(4)
        name_label = QtWidgets.QLabel(coll['name'])
        name_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; background: transparent;")
        name_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        inner.addWidget(name_label)
        inner.addStretch()

        # Click always selects (expand/collapse is on the arrow only)
        btn.clicked.connect(lambda: self._on_item_clicked(btn))
        return btn

    def _toggle_expand(self, coll):
        """Toggle folder expansion."""
        if coll['id'] in self._expanded:
            self._expanded.remove(coll['id'])
        else:
            self._expanded.add(coll['id'])
        self.refresh()

    def _on_item_clicked(self, btn):
        """Handle item selection. Ctrl+click toggles multi-select."""
        item_id = btn.property("item_id")
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        ctrl = modifiers & QtCore.Qt.ControlModifier

        # System items (__recent__, __favorites__, None) don't support multi-select
        is_system = not item_id or str(item_id).startswith("__")

        if ctrl and not is_system:
            # Toggle this collection in multi-select mode
            is_selected = btn.property("selected")
            btn.setProperty("selected", not is_selected)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            # Deselect any system items
            for w in self._all_items:
                if w and w != btn:
                    wid = w.property("item_id")
                    if not wid or str(wid).startswith("__"):
                        w.setProperty("selected", False)
                        w.style().unpolish(w)
                        w.style().polish(w)
            # Emit the full set of selected collection IDs
            selected = set()
            for w in self._all_items:
                if w and w.property("selected"):
                    wid = w.property("item_id")
                    if wid and not str(wid).startswith("__"):
                        selected.add(wid)
            self.collection_selected.emit(selected if selected else None)
        else:
            # Single select: clear all, select this one
            for w in self._all_items:
                if w:
                    w.setProperty("selected", False)
                    w.style().unpolish(w)
                    w.style().polish(w)

            btn.setProperty("selected", True)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

            self.collection_selected.emit(item_id)

    def _add_collection(self, parent_id=None):
        """Add a new collection, optionally as a subfolder."""
        name, ok = QtWidgets.QInputDialog.getText(self, "New Collection", "Name:")
        if ok and name and SOPDROP_AVAILABLE:
            library.create_collection(name, parent_id=parent_id)
            self.refresh()

    def _show_context_menu(self, btn, pos):
        """Show context menu for collection."""
        coll = btn.property("collection_data")
        if not coll:
            return

        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 2px;
            }}
            QMenu::item {{
                padding: 4px 12px;
                border-radius: 2px;
            }}
            QMenu::item:selected {{
                background-color: {COLORS['bg_selected']};
            }}
        """)

        menu.addAction("New Subfolder...").triggered.connect(lambda: self._add_collection(coll['id']))
        menu.addSeparator()
        menu.addAction("Rename...").triggered.connect(lambda: self._rename_collection(coll))
        menu.addAction("Change Color...").triggered.connect(lambda: self._change_color(coll))
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(lambda: self._delete_collection(coll))
        menu.exec_(btn.mapToGlobal(pos))

    def _rename_collection(self, coll):
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename", "Name:", text=coll['name'])
        if ok and name and SOPDROP_AVAILABLE:
            library.update_collection(coll['id'], name=name)
            self.refresh()

    def _change_color(self, coll):
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(coll.get('color', '#666666')), self)
        if color.isValid() and SOPDROP_AVAILABLE:
            library.update_collection(coll['id'], color=color.name())
            self.refresh()

    def _delete_collection(self, coll):
        # Check if has children
        has_children = bool(coll.get('children'))
        msg = f"Delete '{coll['name']}'?"
        if has_children:
            msg += "\n\nThis will also delete all subfolders."

        reply = QtWidgets.QMessageBox.question(self, "Delete Collection", msg)
        if reply == QtWidgets.QMessageBox.Yes and SOPDROP_AVAILABLE:
            library.delete_collection(coll['id'], recursive=True)
            self.refresh()

    def select_collection(self, item_id):
        """Programmatically select a collection by ID."""
        for w in self._all_items:
            if w and w.property("item_id") == item_id:
                self._on_item_clicked(w)
                return True
        return False

    def deselect_all(self):
        """Clear all visual selections."""
        for w in self._all_items:
            if w:
                w.setProperty("selected", False)
                w.style().unpolish(w)
                w.style().polish(w)

    def set_selected(self, item_id, selected=True):
        """Set the visual selection state of a collection by ID (no signal emitted)."""
        for w in self._all_items:
            if w and w.property("item_id") == item_id:
                w.setProperty("selected", selected)
                w.style().unpolish(w)
                w.style().polish(w)
                return


# ==============================================================================
# Asset Card Widget
# ==============================================================================

class AssetCardWidget(QtWidgets.QFrame):
    """Card widget for displaying assets in the grid."""

    paste_requested = QtCore.Signal(str)
    edit_requested = QtCore.Signal(str)
    delete_requested = QtCore.Signal(str)
    publish_requested = QtCore.Signal(str)
    update_requested = QtCore.Signal(str)
    tag_clicked = QtCore.Signal(str)
    collection_changed = QtCore.Signal()
    clicked = QtCore.Signal(object)  # Emits full asset dict on single click
    hovered = QtCore.Signal(object)  # Emits asset dict on hover enter, None on leave

    def __init__(self, asset, card_size='medium', library_type='personal', display_settings=None, parent=None):
        super().__init__(parent)
        self.asset = asset
        self.card_size = card_size
        self.library_type = library_type
        self.display_settings = display_settings or {'name': True, 'context': True, 'tags': False}
        self._hovered = False
        self._selected = False
        self._original_pixmap = None  # Store original for resizing
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("assetCard")

        # Card styling with border - brighter than grid bg for depth
        self.setStyleSheet(f"""
            QFrame#assetCard {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
            }}
        """)
        self.setCursor(QtCore.Qt.PointingHandCursor)

        # Sizes for zoom levels
        sizes = {
            'tiny': {'total': 60, 'font': 8, 'badge': 7},
            'small': {'total': 80, 'font': 9, 'badge': 8},
            'medium': {'total': 100, 'font': 10, 'badge': 8},
            'large': {'total': 130, 'font': 11, 'badge': 9},
            'xlarge': {'total': 170, 'font': 12, 'badge': 10},
        }
        s = sizes.get(self.card_size, sizes['medium'])

        self.setFixedHeight(s['total'])

        # Main layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container for thumbnail and overlays
        self.container = QtWidgets.QWidget()
        self.container.setStyleSheet("background: transparent;")
        layout.addWidget(self.container)

        # Thumbnail - fills entire card
        self.thumb_label = QtWidgets.QLabel(self.container)
        self.thumb_label.setAlignment(QtCore.Qt.AlignCenter)
        self.thumb_label.setStyleSheet("background: transparent;")

        # Top overlay - badges
        self.top_overlay = QtWidgets.QWidget(self.container)
        self.top_overlay.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.top_overlay.setStyleSheet("background: transparent;")
        top_layout = QtWidgets.QHBoxLayout(self.top_overlay)
        top_layout.setContentsMargins(4, 4, 4, 0)
        top_layout.setSpacing(3)

        # Context badge
        context = self.asset.get('context', 'sop')
        if self.display_settings.get('context', True):
            self.ctx_badge = QtWidgets.QLabel(context.upper())
            self.ctx_badge.setStyleSheet(f"""
                background-color: {get_context_color(context)};
                color: white;
                font-size: {s['badge']}px;
                font-weight: bold;
                padding: 2px 5px;
                border-radius: 2px;
            """)
            top_layout.addWidget(self.ctx_badge, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)

        # HDA badge (hidden when context badges are off)
        if self.asset.get('asset_type') == 'hda' and self.display_settings.get('context', True):
            license_type = self.asset.get('license_type', '')
            # Show license tier in HDA badge if non-commercial
            if license_type in ('apprentice', 'education'):
                hda_text = "HDA \u26A0 NC"
                hda_color = "rgba(200, 80, 80, 0.9)"
                hda_tip = f"Non-Commercial HDA — loading in Commercial Houdini will downgrade your session\nType: {self.asset.get('hda_type_name', '')}"
            elif license_type == 'indie':
                hda_text = "HDA \u26A0 Indie"
                hda_color = "rgba(200, 160, 60, 0.9)"
                hda_tip = f"Indie HDA — loading in Commercial Houdini will downgrade your session\nType: {self.asset.get('hda_type_name', '')}"
            else:
                hda_text = "HDA"
                hda_color = "rgba(224, 145, 192, 0.9)"
                hda_tip = f"Digital Asset: {self.asset.get('hda_type_name', '')}"
            hda_badge = QtWidgets.QLabel(hda_text)
            hda_badge.setStyleSheet(f"""
                background-color: {hda_color};
                color: white;
                font-size: {s['badge']}px;
                padding: 2px 4px;
                border-radius: 2px;
            """)
            hda_badge.setToolTip(hda_tip)
            top_layout.addWidget(hda_badge, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)

        top_layout.addStretch()

        # Cloud sync indicator
        sync_status = self.asset.get('sync_status', 'local_only')
        if sync_status in ('synced', 'syncing', 'modified'):
            sync_icon = QtWidgets.QLabel("●")
            if sync_status == 'synced':
                sync_icon.setStyleSheet(f"color: {COLORS['success']}; font-size: 8px; background: transparent;")
                sync_icon.setToolTip("Synced to cloud")
            elif sync_status == 'syncing':
                sync_icon.setStyleSheet(f"color: {COLORS['accent']}; font-size: 8px; background: transparent;")
                sync_icon.setToolTip("Syncing...")
            else:
                sync_icon.setStyleSheet(f"color: {COLORS['warning']}; font-size: 8px; background: transparent;")
                sync_icon.setToolTip("Modified locally \u2014 right-click to push update")
            top_layout.addWidget(sync_icon)

        # Bottom overlay - gradient for text (hidden when name display is off)
        show_name = self.display_settings.get('name', True)
        show_tags = self.display_settings.get('tags', False)

        self.bottom_overlay = QtWidgets.QWidget(self.container)
        # Only pass through mouse events if tags aren't shown (tags need clicks)
        if not show_tags:
            self.bottom_overlay.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        if show_name or show_tags:
            self.bottom_overlay.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0,0,0,0),
                    stop:0.3 rgba(0,0,0,0.5),
                    stop:1 rgba(0,0,0,0.85));
                border-radius: 0 0 3px 3px;
            """)
        else:
            self.bottom_overlay.setStyleSheet("background: transparent;")

        bottom_layout = QtWidgets.QVBoxLayout(self.bottom_overlay)
        bottom_layout.setContentsMargins(6, 8, 6, 6)
        bottom_layout.setSpacing(2)
        bottom_layout.addStretch()

        # Asset name - clean typography
        name_text = self.asset.get('name', 'Untitled')
        if show_name:
            name = QtWidgets.QLabel(name_text)
            name.setStyleSheet(f"""
                color: {COLORS['text_bright']};
                font-size: {s['font']}px;
                font-weight: 600;
                background: transparent;
            """)
            name.setWordWrap(False)
            fm = QtGui.QFontMetrics(name.font())
            max_width = {
                'tiny': 70, 'small': 90, 'medium': 120, 'large': 160, 'xlarge': 210
            }.get(self.card_size, 120)
            elided = fm.elidedText(name_text, QtCore.Qt.ElideRight, max_width)
            name.setText(elided)
            name.setToolTip(name_text)
            bottom_layout.addWidget(name)

        # Tag pills (optional, clickable to filter)
        if show_tags:
            tags = self.asset.get('tags', [])
            if tags:
                tags_layout = QtWidgets.QHBoxLayout()
                tags_layout.setContentsMargins(0, 0, 0, 0)
                tags_layout.setSpacing(3)
                tag_font = max(9, s['font'] - 1)
                for tag_text in tags[:3]:
                    tag_btn = QtWidgets.QPushButton(tag_text)
                    tag_btn.setCursor(QtCore.Qt.PointingHandCursor)
                    tag_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: rgba(255,255,255,0.15);
                            color: {COLORS['text_secondary']};
                            font-size: {tag_font}px;
                            padding: 1px 5px;
                            border-radius: 3px;
                            border: none;
                        }}
                        QPushButton:hover {{
                            background-color: rgba(255,255,255,0.3);
                            color: {COLORS['text']};
                        }}
                    """)
                    tag_btn.clicked.connect(lambda checked=False, t=tag_text: self.tag_clicked.emit(t))
                    tags_layout.addWidget(tag_btn)
                tags_layout.addStretch()
                bottom_layout.addLayout(tags_layout)

        # Always set tooltip even if name not shown
        if not show_name:
            self.setToolTip(name_text)

        self._load_thumbnail(s['total'])

        # Hover popover timer
        self._hover_timer = QtCore.QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(400)
        self._hover_timer.timeout.connect(self._show_popover)

    def _update_border(self):
        """Update card border based on selected/hovered state."""
        if self._selected:
            border = f"2px solid {COLORS['accent']}"
            bg = COLORS['bg_card_hover']
        elif self._hovered:
            border = f"1px solid {COLORS['accent']}"
            bg = COLORS['bg_card_hover']
        else:
            border = f"1px solid {COLORS['border']}"
            bg = COLORS['bg_card']
        self.setStyleSheet(f"""
            QFrame#assetCard {{
                background-color: {bg};
                border: {border};
                border-radius: 4px;
            }}
        """)

    def enterEvent(self, event):
        """Hover enter - highlight border and start popover timer."""
        self._hovered = True
        self._update_border()
        self._hover_timer.start()
        self.hovered.emit(self.asset)
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Hover leave - restore border and hide popover."""
        self._hovered = False
        self._hover_timer.stop()
        AssetPopover.hide_popover()
        self._update_border()
        self.hovered.emit(None)
        super().leaveEvent(event)

    def _show_popover(self):
        """Show the hover popover for this asset."""
        if not self._hovered:
            return
        # Position at the right edge of the card
        card_rect = self.rect()
        global_pos = self.mapToGlobal(card_rect.topRight())
        popover = AssetPopover.instance()
        popover.show_for_asset(self.asset, global_pos)

    def resizeEvent(self, event):
        """Position overlays and scale thumbnail when card resizes."""
        super().resizeEvent(event)
        w = self.container.width()
        h = self.container.height()

        # Thumbnail fills entire container
        self.thumb_label.setGeometry(0, 0, w, h)

        # Scale thumbnail to fill the container
        self._update_thumbnail_display(w, h)

        # Top overlay - sized for badge row
        self.top_overlay.setGeometry(0, 0, w, 22)

        # Bottom overlay (bottom third)
        bottom_h = max(24, h // 3)
        self.bottom_overlay.setGeometry(0, h - bottom_h, w, bottom_h)

    def _load_thumbnail(self, height):
        """Load thumbnail and store original for dynamic resizing."""
        thumb_path_str = self.asset.get('thumbnail_path')
        if thumb_path_str and SOPDROP_AVAILABLE:
            try:
                thumb_dir = library.get_library_thumbnails_dir()
                thumb_path = thumb_dir / thumb_path_str
                thumb_path_resolved = str(thumb_path.resolve())

                if thumb_path.exists():
                    with open(thumb_path_resolved, 'rb') as f:
                        image_data = f.read()

                    pixmap = QtGui.QPixmap()
                    if pixmap.loadFromData(image_data):
                        self._original_pixmap = pixmap
                        return
                    else:
                        print(f"[Sopdrop] loadFromData failed for: {thumb_path_resolved}")
                else:
                    print(f"[Sopdrop] Thumbnail file not found: {thumb_path_resolved}")
            except Exception as e:
                print(f"[Sopdrop] Thumbnail load error for {self.asset.get('name')}: {e}")
                import traceback
                traceback.print_exc()

        # Create placeholder pixmap
        self._original_pixmap = None

    def _update_thumbnail_display(self, width, height):
        """Scale and display thumbnail to fill the given dimensions with rounded corners."""
        if width <= 0 or height <= 0:
            return

        radius = 3  # Match card border-radius minus border

        if self._original_pixmap and not self._original_pixmap.isNull():
            # Scale to fill container, cropping as needed
            scaled = self._original_pixmap.scaled(
                width, height,
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation
            )
            # Crop to exact size from center
            if scaled.width() > width or scaled.height() > height:
                x = (scaled.width() - width) // 2
                y = (scaled.height() - height) // 2
                scaled = scaled.copy(x, y, width, height)

            # Apply rounded corners
            rounded = QtGui.QPixmap(width, height)
            rounded.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(rounded)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            path = QtGui.QPainterPath()
            path.addRoundedRect(0, 0, width, height, radius, radius)
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, scaled)
            painter.end()
            self.thumb_label.setPixmap(rounded)
        else:
            # Placeholder with context letter and rounded corners
            context = self.asset.get('context', 'sop')
            pixmap = QtGui.QPixmap(width, height)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)

            # Draw rounded background
            path = QtGui.QPainterPath()
            path.addRoundedRect(0, 0, width, height, radius, radius)
            painter.setClipPath(path)
            painter.fillRect(0, 0, width, height, QtGui.QColor(COLORS['bg_medium']))

            # Draw context letter or VEX code icon
            is_vex = self.asset.get('asset_type') == 'vex' or context == 'vex'
            painter.setPen(QtGui.QColor(get_context_color(context)))
            font_size = max(12, min(height // 3, 24))
            painter.setFont(QtGui.QFont("Arial", font_size, QtGui.QFont.Bold))
            painter.setOpacity(0.2)
            if is_vex:
                # Show code brackets icon for VEX snippets
                painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, "{ }")
            else:
                painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, context[0].upper())
            painter.end()
            self.thumb_label.setPixmap(pixmap)

    # -- Drag tracking ---------------------------------------------------------
    # macOS: grabMouse() approach (QDrag enters native Cocoa loop that blocks
    #        Qt timers and stylesheet repaints)
    # Windows/Linux: standard QDrag with mime data (works natively with
    #        _DropAwareContainer's dragMoveEvent for live highlighting)
    _custom_drag_active = False
    _custom_drag_ids = []
    _IS_MACOS = __import__('sys').platform == 'darwin'

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_pos = event.pos()
            self._did_drag = False
            # Don't emit clicked yet — wait for release to preserve multi-select during drag
        super().mousePressEvent(event)

    def _get_drag_asset_ids(self):
        """Get the list of asset IDs to drag (respects multi-select)."""
        parent_grid = self.parent()
        while parent_grid and not isinstance(parent_grid, AssetGridWidget):
            parent_grid = parent_grid.parent()

        asset_ids = [self.asset['id']]
        if parent_grid and hasattr(parent_grid, '_selected_assets') and parent_grid._selected_assets:
            if self.asset['id'] in parent_grid._selected_assets:
                asset_ids = list(parent_grid._selected_assets)
        return asset_ids

    def mouseMoveEvent(self, event):
        """Start drag when mouse moves beyond threshold."""
        # macOS custom drag: track cursor over collections
        if AssetCardWidget._custom_drag_active:
            coll_widget = CollectionListWidget._active_instance
            if coll_widget:
                container_pos = coll_widget.container.mapFromGlobal(event.globalPos())
                btn = coll_widget._find_collection_at_pos(container_pos)
                coll_widget._set_drop_highlight(btn)
            return

        if not (event.buttons() & QtCore.Qt.LeftButton):
            return
        if not hasattr(self, '_drag_start_pos'):
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QtWidgets.QApplication.startDragDistance():
            return

        asset_ids = self._get_drag_asset_ids()
        self._did_drag = True

        if self._IS_MACOS:
            # macOS: grabMouse keeps us in Qt's normal event loop
            AssetCardWidget._custom_drag_active = True
            AssetCardWidget._custom_drag_ids = asset_ids
            self.grabMouse()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.DragMoveCursor)
        else:
            # Windows/Linux: standard QDrag — _DropAwareContainer handles highlighting
            drag = QtGui.QDrag(self)
            mime = QtCore.QMimeData()
            mime.setData('application/x-sopdrop-assets',
                         QtCore.QByteArray(','.join(asset_ids).encode()))
            drag.setMimeData(mime)
            drag.exec_(QtCore.Qt.MoveAction)

    def mouseReleaseEvent(self, event):
        if AssetCardWidget._custom_drag_active:
            # macOS custom drag release
            AssetCardWidget._custom_drag_active = False
            self.releaseMouse()
            QtWidgets.QApplication.restoreOverrideCursor()

            # Check if dropped on a collection
            coll_widget = CollectionListWidget._active_instance
            if coll_widget:
                container_pos = coll_widget.container.mapFromGlobal(event.globalPos())
                btn = coll_widget._find_collection_at_pos(container_pos)
                coll_widget._set_drop_highlight(None)
                if btn:
                    coll_id = btn.property("item_id")
                    if SOPDROP_AVAILABLE and AssetCardWidget._custom_drag_ids:
                        for aid in AssetCardWidget._custom_drag_ids:
                            library.add_asset_to_collection(aid, coll_id)
                        coll_widget.collection_selected.emit(coll_id)

            AssetCardWidget._custom_drag_ids = []
            return

        # No drag happened — emit clicked now (deferred from mousePressEvent)
        if event.button() == QtCore.Qt.LeftButton and not getattr(self, '_did_drag', False):
            self.clicked.emit(self.asset)

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            # VEX snippets: copy code to clipboard on double-click
            if self.asset.get('asset_type') == 'vex' or self.asset.get('context') == 'vex':
                self._copy_vex_to_clipboard()
            else:
                self.paste_requested.emit(self.asset['id'])

    def _copy_vex_to_clipboard(self):
        """Copy VEX snippet code to clipboard."""
        if SOPDROP_AVAILABLE:
            package = library.load_asset_package(self.asset['id'])
            if package and 'code' in package:
                clipboard = QtWidgets.QApplication.clipboard()
                clipboard.setText(package['code'])
                # Find parent panel to show toast
                parent = self.parent()
                while parent and not isinstance(parent, LibraryPanel):
                    parent = parent.parent()
                if parent and hasattr(parent, 'show_toast'):
                    parent.show_toast("Copied to clipboard", 'success', 2000)

    def _edit_vex_code(self):
        """Open a dialog to edit VEX snippet code."""
        if not SOPDROP_AVAILABLE:
            return
        package = library.load_asset_package(self.asset['id'])
        if not package or 'code' not in package:
            return

        dialog = QtWidgets.QDialog(self.window())
        dialog.setWindowTitle(f"Edit VEX: {self.asset.get('name', 'Snippet')}")
        dialog.setMinimumSize(scale(500), scale(400))
        dialog.setStyleSheet(STYLESHEET)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        editor = QtWidgets.QPlainTextEdit()
        editor.setPlainText(package['code'])
        editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {COLORS['bg_dark']};
                color: {COLORS['text_bright']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                padding: 8px;
                font-family: 'Source Code Pro', 'Fira Code', 'Consolas', monospace;
                font-size: 12px;
            }}
        """)
        layout.addWidget(editor)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QtWidgets.QPushButton("Save")
        save_btn.setStyleSheet(f"background-color: {COLORS['accent']}; color: white; font-weight: bold;")
        save_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            new_code = editor.toPlainText()
            package['code'] = new_code
            package['metadata']['line_count'] = len(new_code.splitlines())
            try:
                library.update_asset_package(self.asset['id'], package)
                parent = self.parent()
                while parent and not isinstance(parent, LibraryPanel):
                    parent = parent.parent()
                if parent and hasattr(parent, 'show_toast'):
                    parent.show_toast("VEX code saved", 'success', 2000)
            except Exception as e:
                QtWidgets.QMessageBox.critical(dialog, "Error", f"Failed to save: {e}")

    def set_selected(self, selected):
        """Set visual selection state."""
        self._selected = selected
        self._update_border()

    def _get_selected_asset_ids(self):
        """Get all selected asset IDs from the parent grid, or just this asset."""
        parent_grid = self.parent()
        while parent_grid and not isinstance(parent_grid, AssetGridWidget):
            parent_grid = parent_grid.parent()
        if parent_grid and hasattr(parent_grid, '_selected_assets') and parent_grid._selected_assets:
            if self.asset['id'] in parent_grid._selected_assets:
                return list(parent_grid._selected_assets)
        return [self.asset['id']]

    def contextMenuEvent(self, event):
        selected_ids = self._get_selected_asset_ids()
        multi = len(selected_ids) > 1

        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                padding: 3px;
            }}
            QMenu::item {{
                background-color: transparent;
                padding: 5px 10px;
                color: {COLORS['text']};
                border-radius: 2px;
            }}
            QMenu::item:selected {{
                background-color: {COLORS['bg_selected']};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {COLORS['border']};
                margin: 3px 2px;
            }}
        """)

        # Different actions based on asset type (single-item actions)
        is_hda = self.asset.get('asset_type') == 'hda'
        is_vex = self.asset.get('asset_type') == 'vex' or self.asset.get('context') == 'vex'

        if not multi:
            if is_vex:
                menu.addAction("\u25B6  Copy Code").triggered.connect(self._copy_vex_to_clipboard)
                menu.addAction("\u270E  Edit Code...").triggered.connect(lambda: self._edit_vex_code())
                menu.addSeparator()
            elif is_hda:
                menu.addAction("\u25B6  Place HDA").triggered.connect(lambda: self.paste_requested.emit(self.asset['id']))
                menu.addSeparator()
            else:
                menu.addAction("\u25B6  Paste into Network").triggered.connect(lambda: self.paste_requested.emit(self.asset['id']))
                menu.addSeparator()

            # Update with current selection (not for HDAs or VEX)
            if not is_hda and not is_vex:
                menu.addAction("\u2191  Version Up with Selection...").triggered.connect(lambda: self.update_requested.emit(self.asset['id']))

        # Collections submenu — works on all selected
        count_label = f" ({len(selected_ids)})" if multi else ""
        coll_menu = menu.addMenu(f"\u25A3  Add to Collection{count_label}")
        if SOPDROP_AVAILABLE:
            tree = library.get_collection_tree()
            current = set(c['id'] for c in self.asset.get('collections', []))
            self._build_collection_submenu_bulk(coll_menu, tree, current, selected_ids)
            if tree:
                coll_menu.addSeparator()
            coll_menu.addAction("+ New...").triggered.connect(
                lambda checked=False, ids=selected_ids: self._create_and_add_collection_bulk(ids))

        menu.addSeparator()
        if not multi:
            menu.addAction("\u25C9  View Details").triggered.connect(self._view_details)
            menu.addAction("\u270E  Edit Details").triggered.connect(lambda: self.edit_requested.emit(self.asset['id']))

            # Cloud actions
            remote_slug = self.asset.get('remote_slug')
            sync_status = self.asset.get('sync_status', 'local_only')
            if remote_slug:
                menu.addAction("\u2197  View on Website").triggered.connect(self._open_on_website)
                if sync_status == 'modified':
                    menu.addAction("\u2191  Push Update to Cloud...").triggered.connect(self._push_version_to_cloud)
            elif sync_status == 'local_only':
                menu.addAction("\u2601  Publish to Cloud...").triggered.connect(lambda: self.publish_requested.emit(self.asset['id']))

        # Cross-library copy/move — works on all selected
        if SOPDROP_AVAILABLE:
            other_lib = library.get_other_library_type()
            if other_lib:
                menu.addSeparator()
                lib_name = "Team Library" if other_lib == "team" else "Personal Library"
                menu.addAction(f"\u2295  Copy to {lib_name}{count_label}").triggered.connect(
                    lambda checked=False, t=other_lib, ids=selected_ids: self._copy_to_library_bulk(t, ids)
                )
                menu.addAction(f"\u2192  Move to {lib_name}{count_label}").triggered.connect(
                    lambda checked=False, t=other_lib, ids=selected_ids: self._move_to_library_bulk(t, ids)
                )

        menu.addSeparator()
        delete_label = f"\u2715  Delete ({len(selected_ids)})" if multi else "\u2715  Delete"
        menu.addAction(delete_label).triggered.connect(
            lambda checked=False, ids=selected_ids: self._delete_bulk(ids))

        menu.exec_(event.globalPos())

    def _add_to_collection(self, cid):
        if SOPDROP_AVAILABLE:
            library.add_asset_to_collection(self.asset['id'], cid)
            self.collection_changed.emit()

    def _remove_from_collection(self, cid):
        if SOPDROP_AVAILABLE:
            library.remove_asset_from_collection(self.asset['id'], cid)
            self.collection_changed.emit()

    def _create_and_add_collection(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "New Collection", "Name:")
        if ok and name and SOPDROP_AVAILABLE:
            coll = library.create_collection(name)
            library.add_asset_to_collection(self.asset['id'], coll['id'])
            self.collection_changed.emit()

    # -- Bulk action methods for multi-select context menu -----------------------

    def _add_to_collection_bulk(self, cid, asset_ids):
        if SOPDROP_AVAILABLE:
            for aid in asset_ids:
                library.add_asset_to_collection(aid, cid)
            self.collection_changed.emit()

    def _remove_from_collection_bulk(self, cid, asset_ids):
        if SOPDROP_AVAILABLE:
            for aid in asset_ids:
                library.remove_asset_from_collection(aid, cid)
            self.collection_changed.emit()

    def _create_and_add_collection_bulk(self, asset_ids):
        name, ok = QtWidgets.QInputDialog.getText(self, "New Collection", "Name:")
        if ok and name and SOPDROP_AVAILABLE:
            coll = library.create_collection(name)
            for aid in asset_ids:
                library.add_asset_to_collection(aid, coll['id'])
            self.collection_changed.emit()

    def _build_collection_submenu_bulk(self, parent_menu, tree, current_ids, asset_ids):
        """Build collection submenu that operates on all selected assets."""
        for coll in tree:
            has_children = bool(coll.get('children'))
            is_in = coll['id'] in current_ids

            if has_children:
                prefix = "\u2713 " if is_in else "    "
                sub = parent_menu.addMenu(prefix + coll['name'])
                sub.addAction("Add here").triggered.connect(
                    lambda checked=False, c=coll['id'], ids=asset_ids: self._add_to_collection_bulk(c, ids))
                sub.addAction("Remove from here").triggered.connect(
                    lambda checked=False, c=coll['id'], ids=asset_ids: self._remove_from_collection_bulk(c, ids))
                sub.addSeparator()
                self._build_collection_submenu_bulk(sub, coll['children'], current_ids, asset_ids)
            else:
                prefix = "\u2713 " if is_in else "    "
                action = parent_menu.addAction(prefix + coll['name'])
                if is_in:
                    action.triggered.connect(
                        lambda checked=False, c=coll['id'], ids=asset_ids: self._remove_from_collection_bulk(c, ids))
                else:
                    action.triggered.connect(
                        lambda checked=False, c=coll['id'], ids=asset_ids: self._add_to_collection_bulk(c, ids))

    def _delete_bulk(self, asset_ids):
        """Delete all selected assets via the panel's bulk delete."""
        parent = self.parent()
        while parent and not isinstance(parent, LibraryPanel):
            parent = parent.parent()
        if parent and hasattr(parent, '_delete_assets_bulk'):
            parent._delete_assets_bulk(asset_ids)
        else:
            # Fallback: emit one by one
            for aid in asset_ids:
                self.delete_requested.emit(aid)

    def _copy_to_library_bulk(self, target_library, asset_ids):
        """Copy all selected assets to another library."""
        if not SOPDROP_AVAILABLE:
            return
        lib_name = "Team Library" if target_library == "team" else "Personal Library"
        count = 0
        for aid in asset_ids:
            try:
                QtWidgets.QApplication.processEvents()
                result = library.copy_asset_to_library(aid, target_library)
                if result:
                    count += 1
            except Exception as e:
                print(f"[Sopdrop] Failed to copy asset {aid}: {e}")
        parent = self.parent()
        while parent and not isinstance(parent, LibraryPanel):
            parent = parent.parent()
        if parent and hasattr(parent, 'show_toast'):
            parent.show_toast(f"Copied {count} assets to {lib_name}", 'success', 2000)

    def _move_to_library_bulk(self, target_library, asset_ids):
        """Move all selected assets to another library."""
        if not SOPDROP_AVAILABLE:
            return
        lib_name = "Team Library" if target_library == "team" else "Personal Library"
        reply = QtWidgets.QMessageBox.question(
            self, "Move Assets",
            f"Move {len(asset_ids)} assets to {lib_name}?\n\n"
            "This will remove them from the current library.")
        if reply != QtWidgets.QMessageBox.Yes:
            return
        count = 0
        for aid in asset_ids:
            try:
                result = library.move_asset_to_library(aid, target_library)
                if result:
                    count += 1
            except Exception as e:
                print(f"[Sopdrop] Failed to move asset {aid}: {e}")
        parent = self.parent()
        while parent and not isinstance(parent, LibraryPanel):
            parent = parent.parent()
        if parent and hasattr(parent, 'show_toast'):
            parent.show_toast(f"Moved {count} assets to {lib_name}", 'success', 2000)

    def _build_collection_submenu(self, parent_menu, tree, current_ids):
        """Recursively build the collection submenu with nested children."""
        for coll in tree:
            has_children = bool(coll.get('children'))
            is_in = coll['id'] in current_ids
            prefix = "\u2713 " if is_in else "    "

            if has_children:
                sub = parent_menu.addMenu(prefix + coll['name'])
                # Action for this folder itself
                if is_in:
                    sub.addAction("Remove from here").triggered.connect(
                        lambda checked=False, c=coll['id']: self._remove_from_collection(c))
                else:
                    sub.addAction("Add here").triggered.connect(
                        lambda checked=False, c=coll['id']: self._add_to_collection(c))
                sub.addSeparator()
                # Recurse into children
                self._build_collection_submenu(sub, coll['children'], current_ids)
            else:
                action = parent_menu.addAction(prefix + coll['name'])
                if is_in:
                    action.triggered.connect(
                        lambda checked=False, c=coll['id']: self._remove_from_collection(c))
                else:
                    action.triggered.connect(
                        lambda checked=False, c=coll['id']: self._add_to_collection(c))

    def _view_details(self):
        """Open the asset detail viewer."""
        # Refresh asset data for latest info
        if SOPDROP_AVAILABLE:
            fresh = library.get_asset(self.asset['id'])
            if fresh:
                self.asset = fresh
        dialog = AssetDetailDialog(self.asset, self.window())
        dialog.tag_clicked.connect(self.tag_clicked.emit)
        dialog.exec_()

    def _open_on_website(self):
        """Open this asset on the website."""
        remote_slug = self.asset.get('remote_slug')
        if remote_slug and SOPDROP_AVAILABLE:
            import webbrowser
            from sopdrop.config import get_config
            config = get_config()
            base_url = config.get('server_url', 'https://sopdrop.com').rstrip('/')
            url = f"{base_url}/assets/{remote_slug}"
            webbrowser.open(url)

    def _push_version_to_cloud(self):
        """Push a new version of this asset to the cloud."""
        if not SOPDROP_AVAILABLE:
            return

        parent = self.parent()
        while parent and not isinstance(parent, LibraryPanel):
            parent = parent.parent()

        try:
            result = library.push_version_to_cloud(self.asset['id'])
            if parent and hasattr(parent, 'show_toast'):
                parent.show_toast("Version draft created — complete in browser", 'success', 4000)
            if parent:
                parent._refresh_assets()
        except Exception as e:
            if parent and hasattr(parent, 'show_toast'):
                parent.show_toast(f"Push failed: {e}", 'error', 5000)
            else:
                print(f"[Sopdrop] Push version failed: {e}")
            import traceback
            traceback.print_exc()

    def _copy_to_library(self, target_library):
        """Copy this asset to another library."""
        if not SOPDROP_AVAILABLE:
            return

        lib_name = "Team Library" if target_library == "team" else "Personal Library"
        try:
            # Process events before heavy operation to avoid UI freeze
            QtWidgets.QApplication.processEvents()
            new_asset = library.copy_asset_to_library(self.asset['id'], target_library)
            if new_asset:
                # Find parent panel for toast
                parent = self.parent()
                while parent and not isinstance(parent, LibraryPanel):
                    parent = parent.parent()
                if parent and hasattr(parent, 'show_toast'):
                    parent.show_toast(f"Copied to {lib_name}", 'success', 2000)
                else:
                    print(f"[Sopdrop] Copied '{self.asset['name']}' to {lib_name}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                hou.ui.displayMessage(f"Failed to copy: {e}", severity=hou.severityType.Error)
            except Exception:
                print(f"[Sopdrop] Failed to copy: {e}")

    def _move_to_library(self, target_library):
        """Move this asset to another library."""
        if not SOPDROP_AVAILABLE:
            return

        lib_name = "Team Library" if target_library == "team" else "Personal Library"
        reply = QtWidgets.QMessageBox.question(
            self,
            "Move Asset",
            f"Move '{self.asset['name']}' to {lib_name}?\n\n"
            "This will remove it from the current library."
        )

        if reply != QtWidgets.QMessageBox.Yes:
            return

        try:
            new_asset = library.move_asset_to_library(self.asset['id'], target_library)
            if new_asset:
                hou.ui.displayMessage(
                    f"Moved '{self.asset['name']}' to {lib_name}",
                    title="Asset Moved"
                )
                # Refresh the grid to remove this asset
                self.collection_changed.emit()
        except Exception as e:
            hou.ui.displayMessage(f"Failed to move: {e}", severity=hou.severityType.Error)


# ==============================================================================
# Asset Grid Widget
# ==============================================================================

class AssetGridWidget(QtWidgets.QWidget):
    """Grid view for browsing assets."""

    paste_requested = QtCore.Signal(str)
    edit_requested = QtCore.Signal(str)
    delete_requested = QtCore.Signal(str)
    publish_requested = QtCore.Signal(str)
    update_requested = QtCore.Signal(str)
    tag_clicked = QtCore.Signal(str)
    collection_changed = QtCore.Signal()
    navigate_to_collection = QtCore.Signal(str)  # Emits collection ID to navigate sidebar
    asset_selected = QtCore.Signal(object)  # Emits full asset dict when selected/hovered

    def __init__(self, parent=None):
        super().__init__(parent)
        self._card_size = 'medium'
        self._assets = []
        self._groups = None  # Store group data for resize reflow
        self._library_type = 'personal'
        self._display_settings = {'name': True, 'context': True, 'tags': False}
        self._last_columns = 0
        self._resize_timer = None
        self._selected_asset = None
        self._setup_ui()

    def set_empty_message(self, message):
        """Update the empty state message."""
        self.empty_label.setText(message)

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"border: none; background-color: {COLORS['bg_grid']};")

        # Main container with vertical layout to allow proper alignment
        self.container = QtWidgets.QWidget()
        self.container.setStyleSheet(f"background-color: {COLORS['bg_grid']};")
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(6, 6, 6, 6)
        container_layout.setSpacing(0)

        # Grid widget inside container
        self.grid_widget = QtWidgets.QWidget()
        self.grid_widget.setStyleSheet(f"background-color: {COLORS['bg_grid']};")
        self.grid_layout = QtWidgets.QGridLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(6)
        self.grid_layout.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)

        container_layout.addWidget(self.grid_widget)
        container_layout.addStretch(1)  # Push grid to top

        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

        # Empty state
        self.empty_widget = QtWidgets.QWidget()
        empty_layout = QtWidgets.QVBoxLayout(self.empty_widget)
        empty_layout.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_label = QtWidgets.QLabel("No assets yet\nSave nodes using + Save Nodes")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 12px;")
        self.empty_label.setWordWrap(True)
        empty_layout.addWidget(self.empty_label)
        self.empty_widget.hide()
        layout.addWidget(self.empty_widget)

        # Loading state
        self.loading_widget = QtWidgets.QWidget()
        loading_layout = QtWidgets.QVBoxLayout(self.loading_widget)
        loading_layout.setAlignment(QtCore.Qt.AlignCenter)
        loading_label = QtWidgets.QLabel("Loading...")
        loading_label.setAlignment(QtCore.Qt.AlignCenter)
        loading_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
        loading_layout.addWidget(loading_label)
        self.loading_widget.hide()
        layout.addWidget(self.loading_widget)

    def set_loading(self, loading):
        """Show or hide the loading indicator."""
        if loading:
            self.loading_widget.show()
            self.scroll.hide()
            self.empty_widget.hide()
        else:
            self.loading_widget.hide()

    def resizeEvent(self, event):
        """Handle resize to reflow grid columns."""
        super().resizeEvent(event)
        # Debounce resize - only reflow if column count changes
        if self._assets:
            self._schedule_reflow()

    def _schedule_reflow(self):
        """Schedule a grid reflow with debouncing."""
        if self._resize_timer is not None:
            self.killTimer(self._resize_timer)
        self._resize_timer = self.startTimer(100)  # 100ms debounce

    def timerEvent(self, event):
        """Handle debounced resize timer."""
        if event.timerId() == self._resize_timer:
            self.killTimer(self._resize_timer)
            self._resize_timer = None
            self._reflow_grid()

    def _reflow_grid(self):
        """Reflow grid if column count has changed."""
        if not self._assets:
            return

        widths = {'tiny': 80, 'small': 100, 'medium': 130, 'large': 170, 'xlarge': 220}
        card_width = widths.get(self._card_size, 130)
        width = self.scroll.viewport().width() or 400
        columns = max(1, (width - 10) // (card_width + 6))

        # Only rebuild if columns changed
        if columns != self._last_columns:
            if self._groups:
                # Grouped layout — must re-call set_grouped_assets to rebuild properly
                self.set_grouped_assets(self._groups)
            else:
                self._rebuild_grid(columns, card_width)

    def _rebuild_grid(self, columns, card_width):
        """Rebuild the flat (non-grouped) grid with the specified column count."""
        # Store existing cards
        cards = []
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                cards.append(item.widget())

        # Re-add cards in new layout
        for i, card in enumerate(cards):
            self.grid_layout.addWidget(card, i // columns, i % columns)

        self._last_columns = columns

    def set_card_size(self, size):
        if size != self._card_size:
            self._card_size = size
            self._last_columns = 0  # Force rebuild
            self.set_assets(self._assets)

    def set_library_type(self, library_type):
        """Set the current library type for badge display."""
        if library_type != self._library_type:
            self._library_type = library_type
            self._last_columns = 0  # Force rebuild
            self.set_assets(self._assets)

    def set_assets(self, assets):
        self._assets = assets
        self._groups = None  # Clear groups — flat layout
        self.loading_widget.hide()

        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not assets:
            self.empty_widget.show()
            self.scroll.hide()
            self._last_columns = 0
            return

        self.empty_widget.hide()
        self.scroll.show()

        widths = {'tiny': 80, 'small': 100, 'medium': 130, 'large': 170, 'xlarge': 220}
        card_width = widths.get(self._card_size, 130)

        width = self.scroll.viewport().width() or 400
        columns = max(1, (width - 10) // (card_width + 6))
        self._last_columns = columns

        for i, asset in enumerate(assets):
            card = AssetCardWidget(asset, self._card_size, self._library_type, self._display_settings)
            card.setFixedWidth(card_width)
            card.paste_requested.connect(self.paste_requested.emit)
            card.edit_requested.connect(self.edit_requested.emit)
            card.delete_requested.connect(self.delete_requested.emit)
            card.publish_requested.connect(self.publish_requested.emit)
            card.update_requested.connect(self.update_requested.emit)
            card.tag_clicked.connect(self.tag_clicked.emit)
            card.collection_changed.connect(self.collection_changed.emit)
            card.clicked.connect(self._on_card_clicked)
            card.hovered.connect(self._on_card_hovered)
            self.grid_layout.addWidget(card, i // columns, i % columns)

    def set_grouped_assets(self, groups):
        """Display assets grouped by collection with section headers."""
        self.loading_widget.hide()
        self._groups = groups  # Store for resize reflow
        # Flatten for _assets tracking
        self._assets = []
        for g in groups:
            self._assets.extend(g['assets'])

        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._assets:
            self.empty_widget.show()
            self.scroll.hide()
            self._last_columns = 0
            return

        self.empty_widget.hide()
        self.scroll.show()

        widths = {'tiny': 80, 'small': 100, 'medium': 130, 'large': 170, 'xlarge': 220}
        card_width = widths.get(self._card_size, 130)

        width = self.scroll.viewport().width() or 400
        columns = max(1, (width - 10) // (card_width + 6))
        self._last_columns = columns

        row = 0
        for group in groups:
            if not group['assets']:
                continue

            # Add section header with clear visual separator
            header_container = QtWidgets.QWidget()
            header_layout = QtWidgets.QHBoxLayout(header_container)
            header_layout.setContentsMargins(0, 12, 0, 6)
            header_layout.setSpacing(8)

            # Left line
            left_line = QtWidgets.QFrame()
            left_line.setFixedHeight(1)
            left_line.setStyleSheet(f"background-color: {COLORS['border']};")
            header_layout.addWidget(left_line, 1)

            # Clickable collection name with folder icon and count
            coll_id = group.get('id')
            header_label = QtWidgets.QPushButton(f"\u25A3  {group['name']}  \u2022  {len(group['assets'])}")
            header_label.setFlat(True)
            header_label.setCursor(QtCore.Qt.PointingHandCursor)
            header_label.setStyleSheet(f"""
                QPushButton {{
                    color: {COLORS['text']};
                    font-size: 11px;
                    font-weight: 600;
                    background: transparent;
                    border: none;
                    padding: 0 4px;
                }}
                QPushButton:hover {{
                    color: {COLORS['accent']};
                }}
            """)
            if coll_id:
                header_label.clicked.connect(
                    lambda checked=False, cid=coll_id: self.navigate_to_collection.emit(cid)
                )
            header_layout.addWidget(header_label, 0)

            # Right line
            right_line = QtWidgets.QFrame()
            right_line.setFixedHeight(1)
            right_line.setStyleSheet(f"background-color: {COLORS['border']};")
            header_layout.addWidget(right_line, 1)

            self.grid_layout.addWidget(header_container, row, 0, 1, columns)
            row += 1

            # Add asset cards
            for i, asset in enumerate(group['assets']):
                card = AssetCardWidget(asset, self._card_size, self._library_type, self._display_settings)
                card.setFixedWidth(card_width)
                card.paste_requested.connect(self.paste_requested.emit)
                card.edit_requested.connect(self.edit_requested.emit)
                card.delete_requested.connect(self.delete_requested.emit)
                card.publish_requested.connect(self.publish_requested.emit)
                card.update_requested.connect(self.update_requested.emit)
                card.tag_clicked.connect(self.tag_clicked.emit)
                card.collection_changed.connect(self.collection_changed.emit)
                card.clicked.connect(self._on_card_clicked)
                card.hovered.connect(self._on_card_hovered)
                self.grid_layout.addWidget(card, row + (i // columns), i % columns)

            # Move to next row after this group
            row += (len(group['assets']) + columns - 1) // columns

    def _on_card_clicked(self, asset):
        """Handle card click - emit asset_selected signal with multi-select support."""
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        asset_id = asset.get('id')

        if not hasattr(self, '_selected_assets'):
            self._selected_assets = set()
        if not hasattr(self, '_last_clicked_index'):
            self._last_clicked_index = -1

        # Find index of clicked asset
        clicked_index = -1
        for i, a in enumerate(self._assets):
            if a.get('id') == asset_id:
                clicked_index = i
                break

        if modifiers & QtCore.Qt.ControlModifier:
            # Ctrl+click: toggle selection
            if asset_id in self._selected_assets:
                self._selected_assets.discard(asset_id)
            else:
                self._selected_assets.add(asset_id)
        elif modifiers & QtCore.Qt.ShiftModifier and self._last_clicked_index >= 0:
            # Shift+click: range select
            start = min(self._last_clicked_index, clicked_index)
            end = max(self._last_clicked_index, clicked_index)
            for i in range(start, end + 1):
                if i < len(self._assets):
                    self._selected_assets.add(self._assets[i].get('id'))
        else:
            # Plain click: single select
            self._selected_assets = {asset_id}

        self._last_clicked_index = clicked_index

        # Update visual selection on all cards
        self._update_card_selections()

        self._selected_asset = asset
        self.asset_selected.emit(asset)

    def _update_card_selections(self):
        """Update visual selection state on all cards."""
        if not hasattr(self, '_selected_assets'):
            return
        for i in range(self.grid_layout.count()):
            item = self.grid_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), AssetCardWidget):
                card = item.widget()
                card.set_selected(card.asset.get('id') in self._selected_assets)

    def _on_card_hovered(self, asset):
        """Forward hover events to asset_selected signal.
        On leave (None), revert to clicked selection if one exists."""
        if asset is None and self._selected_asset:
            self.asset_selected.emit(self._selected_asset)
        else:
            self.asset_selected.emit(asset)


# ==============================================================================
# Main Library Panel
# ==============================================================================

class LibraryPanel(QtWidgets.QWidget):
    """Main library panel widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_collection = None  # str ID, "__recent__", "__favorites__", or None
        self.current_collections = set()  # Multi-select collection IDs
        self.current_context_filter = None
        self.current_tag_filters = set()  # Multi-select tags
        self.current_tag_filter = None  # Legacy compat
        self._selected_asset_index = -1
        self._display_settings = {
            'name': True,
            'context': True,
            'tags': False,
        }
        self._selected_assets = set()  # Multi-select asset IDs
        self._setup_ui()
        self._setup_shortcuts()
        self._restore_ui_state()
        self._refresh_assets()

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts for the panel."""
        # QShortcut is in QtGui for PySide6, QtWidgets for PySide2
        QShortcut = QtGui.QShortcut if hasattr(QtGui, 'QShortcut') else QtWidgets.QShortcut

        # Ctrl+F / Cmd+F: Focus search
        search_shortcut = QShortcut(QtGui.QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(self._focus_search)

        # Escape: Clear search/filters
        escape_shortcut = QShortcut(QtGui.QKeySequence("Escape"), self)
        escape_shortcut.activated.connect(self._clear_all_filters)

        # Ctrl+R / Cmd+R: Refresh
        refresh_shortcut = QShortcut(QtGui.QKeySequence("Ctrl+R"), self)
        refresh_shortcut.activated.connect(self._refresh_assets)

        # Enter: Paste selected asset
        enter_shortcut = QShortcut(QtGui.QKeySequence("Return"), self)
        enter_shortcut.activated.connect(self._paste_selected)

    def _focus_search(self):
        """Focus the search input."""
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _clear_all_filters(self):
        """Clear search and all filters."""
        self.search_input.clear()
        self.context_combo.setCurrentIndex(0)
        if self.current_tag_filters:
            self._clear_tag_filter()
        self._selected_asset_index = -1

    def _paste_selected(self):
        """Paste the currently selected asset."""
        # Get first asset from current grid
        if self.asset_grid._assets and len(self.asset_grid._assets) > 0:
            idx = max(0, self._selected_asset_index)
            if idx < len(self.asset_grid._assets):
                asset = self.asset_grid._assets[idx]
                self._paste_asset(asset['id'])

    def _restore_ui_state(self):
        """Restore UI state from previous session."""
        if not SOPDROP_AVAILABLE:
            return

        try:
            state = get_library_ui_state()

            # Restore search query
            if state.get('search_query'):
                self.search_input.setText(state['search_query'])

            # Restore context filter
            context = state.get('context_filter')
            if context:
                for i in range(self.context_combo.count()):
                    if self.context_combo.itemData(i) == context:
                        self.context_combo.setCurrentIndex(i)
                        self.current_context_filter = context
                        self._update_context_combo_style()
                        break

            # Restore sort order
            sort_by = state.get('sort_by')
            if sort_by:
                for i in range(self.sort_combo.count()):
                    if self.sort_combo.itemData(i) == sort_by:
                        self.sort_combo.setCurrentIndex(i)
                        break

            # Restore tag filters (multi-select)
            tag_filters = state.get('tag_filters', [])
            if tag_filters:
                self.current_tag_filters = set(tag_filters)
                self.current_tag_filter = tag_filters[0] if tag_filters else None  # Legacy compat
                self._update_tag_chips()
                self._update_tags_btn_style()

            # Restore collection selection (may be a single ID or list for multi-select)
            collection_id = state.get('collection_id')
            if isinstance(collection_id, list):
                self.current_collections = set(collection_id)
                self.current_collection = None
                for cid in collection_id:
                    self.collections.set_selected(cid, True)
            elif collection_id:
                self.current_collection = collection_id
                self.current_collections = {collection_id} if not str(collection_id).startswith("__") else set()
                self.collections.select_collection(collection_id)

            # Restore group by collection
            group_by = state.get('group_by_collection', False)
            self.group_btn.setChecked(group_by)

            # Restore subcontent toggle
            show_subs = state.get('show_subcontent', False)
            self.subcontent_btn.setChecked(show_subs)

        except Exception as e:
            print(f"[Sopdrop] Could not restore UI state: {e}")

    def _save_ui_state(self):
        """Save current UI state for next session."""
        if not SOPDROP_AVAILABLE:
            return

        try:
            # Save single collection or multi-select list
            coll_value = self.current_collection
            if self.current_collections:
                coll_value = list(self.current_collections)
            save_library_ui_state(
                search_query=self.search_input.text(),
                context_filter=self.current_context_filter,
                tag_filters=list(self.current_tag_filters),
                sort_by=self.sort_combo.currentData(),
                collection_id=coll_value,
                group_by_collection=self.group_btn.isChecked(),
                show_subcontent=self.subcontent_btn.isChecked(),
            )
        except Exception as e:
            print(f"[Sopdrop] Could not save UI state: {e}")

    def _setup_ui(self):
        self.setStyleSheet(STYLESHEET)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # Top bar
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(6)

        # Library toggle
        lib_toggle_frame = QtWidgets.QFrame()
        lib_toggle_frame.setFixedHeight(22)
        lib_toggle_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_base']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
            }}
        """)
        lib_toggle_layout = QtWidgets.QHBoxLayout(lib_toggle_frame)
        lib_toggle_layout.setContentsMargins(1, 1, 1, 1)
        lib_toggle_layout.setSpacing(1)

        self.personal_btn = QtWidgets.QPushButton("Personal")
        self.personal_btn.setFixedHeight(18)
        self.personal_btn.setCheckable(True)
        self.personal_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.personal_btn.clicked.connect(lambda: self._select_library("personal"))

        self.team_btn = QtWidgets.QPushButton("Team")
        self.team_btn.setFixedHeight(18)
        self.team_btn.setCheckable(True)
        self.team_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.team_btn.clicked.connect(lambda: self._select_library("team"))

        lib_toggle_layout.addWidget(self.personal_btn)
        lib_toggle_layout.addWidget(self.team_btn)

        top_bar.addWidget(lib_toggle_frame)
        self._update_library_toggle()

        # Search input
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Search...  (Ctrl+F)")
        self.search_input.setFixedHeight(scale(22))
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 6px;
                color: {COLORS['text']};
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._execute_search)
        self.search_input.textChanged.connect(self._on_search)
        top_bar.addWidget(self.search_input, 1)

        # Save button
        save_btn = QtWidgets.QPushButton("+ Save Nodes")
        save_btn.setToolTip("Save selected nodes to library")
        save_btn.setFixedHeight(22)
        save_btn.setCursor(QtCore.Qt.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 2px;
                padding: 2px 10px;
                color: white;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        save_btn.clicked.connect(self._save_selection)
        top_bar.addWidget(save_btn)

        # Save VEX button
        save_vex_btn = QtWidgets.QPushButton("+ VEX")
        save_vex_btn.setToolTip("Save a VEX snippet to library")
        save_vex_btn.setFixedHeight(22)
        save_vex_btn.setCursor(QtCore.Qt.PointingHandCursor)
        save_vex_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 8px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
                color: {COLORS['accent']};
            }}
        """)
        save_vex_btn.clicked.connect(self._save_vex_snippet)
        top_bar.addWidget(save_vex_btn)

        # Sync button
        sync_btn = QtWidgets.QPushButton("Pull")
        sync_btn.setToolTip("Pull from sopdrop.com")
        sync_btn.setFixedHeight(22)
        sync_btn.setCursor(QtCore.Qt.PointingHandCursor)
        sync_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 8px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
            }}
        """)
        sync_btn.clicked.connect(self._sync_from_cloud)
        top_bar.addWidget(sync_btn)

        # Settings button - draw gear icon as pixmap for reliable rendering
        gear_pixmap = QtGui.QPixmap(16, 16)
        gear_pixmap.fill(QtCore.Qt.transparent)
        gp = QtGui.QPainter(gear_pixmap)
        gp.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(COLORS['text']), 1.5)
        gp.setPen(pen)
        gp.setBrush(QtCore.Qt.NoBrush)
        import math
        cx, cy, r_outer, r_inner = 8, 8, 6.5, 4.0
        teeth = 6
        path = QtGui.QPainterPath()
        for i in range(teeth * 2):
            angle = math.radians(i * 360 / (teeth * 2) - 90)
            r = r_outer if i % 2 == 0 else r_inner
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        gp.drawPath(path)
        gp.drawEllipse(QtCore.QPointF(cx, cy), 2.0, 2.0)
        gp.end()

        settings_btn = QtWidgets.QPushButton()
        settings_btn.setIcon(QtGui.QIcon(gear_pixmap))
        settings_btn.setIconSize(QtCore.QSize(14, 14))
        settings_btn.setToolTip("Settings")
        settings_btn.setFixedSize(scale(22), scale(22))
        settings_btn.setCursor(QtCore.Qt.PointingHandCursor)
        settings_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['border_light']};
            }}
        """)
        settings_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(settings_btn)

        main_layout.addLayout(top_bar)

        # Second row - filtering controls
        filter_bar = QtWidgets.QHBoxLayout()
        filter_bar.setSpacing(6)

        # Context filter dropdown with icon prefix
        ctx_container = QtWidgets.QFrame()
        ctx_container.setFixedHeight(20)
        ctx_container.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
            }}
            QFrame:hover {{
                border-color: {COLORS['border_light']};
            }}
        """)
        ctx_layout = QtWidgets.QHBoxLayout(ctx_container)
        ctx_layout.setContentsMargins(4, 0, 0, 0)
        ctx_layout.setSpacing(2)

        ctx_icon = QtWidgets.QLabel("◉")
        ctx_icon.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px; background: transparent;")
        ctx_icon.setFixedWidth(12)
        ctx_layout.addWidget(ctx_icon)

        self.context_combo = QtWidgets.QComboBox()
        self.context_combo.addItem("All", None)
        self.context_combo.addItem("Current Context", "__current__")
        for ctx in ['sop', 'lop', 'obj', 'vop', 'dop', 'cop', 'top', 'chop', 'vex']:
            color = QtGui.QColor(get_context_color(ctx))
            pixmap = QtGui.QPixmap(8, 8)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setBrush(color)
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawEllipse(0, 0, 8, 8)
            painter.end()
            icon = QtGui.QIcon(pixmap)
            self.context_combo.addItem(icon, ctx.upper(), ctx)
        self.context_combo.setCursor(QtCore.Qt.PointingHandCursor)
        self.context_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: transparent;
                border: none;
                padding: 0 2px;
                min-width: 40px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 10px;
            }}
        """)
        self.context_combo.currentIndexChanged.connect(self._on_context_changed)
        ctx_layout.addWidget(self.context_combo)
        filter_bar.addWidget(ctx_container)

        # Tags button with icon
        self.tags_btn = QtWidgets.QPushButton("⊞ Tags")
        self.tags_btn.setToolTip("Filter by tags")
        self.tags_btn.setFixedHeight(20)
        self.tags_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.tags_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                color: {COLORS['text']};
                padding: 1px 6px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['border_light']};
            }}
        """)
        self.tags_btn.clicked.connect(self._show_tags_menu)
        filter_bar.addWidget(self.tags_btn)

        # Sort dropdown with icon prefix
        sort_container = QtWidgets.QFrame()
        sort_container.setFixedHeight(20)
        sort_container.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
            }}
            QFrame:hover {{
                border-color: {COLORS['border_light']};
            }}
        """)
        sort_layout = QtWidgets.QHBoxLayout(sort_container)
        sort_layout.setContentsMargins(4, 0, 0, 0)
        sort_layout.setSpacing(2)

        sort_icon = QtWidgets.QLabel("↕")
        sort_icon.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px; background: transparent;")
        sort_icon.setFixedWidth(10)
        sort_layout.addWidget(sort_icon)

        self.sort_combo = QtWidgets.QComboBox()
        self.sort_combo.addItem("Recent", "recent")
        self.sort_combo.addItem("A-Z", "name_asc")
        self.sort_combo.addItem("Z-A", "name_desc")
        self.sort_combo.addItem("Used", "use_count")
        self.sort_combo.addItem("Nodes", "node_count")
        self.sort_combo.setCursor(QtCore.Qt.PointingHandCursor)
        self.sort_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: transparent;
                border: none;
                padding: 0 2px;
                min-width: 50px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 10px;
            }}
        """)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        sort_layout.addWidget(self.sort_combo)
        filter_bar.addWidget(sort_container)

        # Group toggle
        self.group_btn = QtWidgets.QPushButton("Group")
        self.group_btn.setToolTip("Group assets by collection")
        self.group_btn.setFixedHeight(20)
        self.group_btn.setCheckable(True)
        self.group_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.group_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                color: {COLORS['text_secondary']};
                font-size: 10px;
                padding: 0 6px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['border_light']};
                color: {COLORS['text']};
            }}
            QPushButton:checked {{
                background-color: {COLORS['accent']};
                border-color: {COLORS['accent']};
                color: white;
            }}
        """)
        self.group_btn.clicked.connect(self._on_group_changed)
        filter_bar.addWidget(self.group_btn)

        # Subcontent toggle (show assets from child collections too)
        self.subcontent_btn = QtWidgets.QPushButton("Subs")
        self.subcontent_btn.setToolTip("Include assets from child collections")
        self.subcontent_btn.setFixedHeight(20)
        self.subcontent_btn.setCheckable(True)
        self.subcontent_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.subcontent_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                color: {COLORS['text_secondary']};
                font-size: 10px;
                padding: 0 6px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['border_light']};
                color: {COLORS['text']};
            }}
            QPushButton:checked {{
                background-color: {COLORS['accent']};
                border-color: {COLORS['accent']};
                color: white;
            }}
        """)
        self.subcontent_btn.clicked.connect(self._on_subcontent_changed)
        filter_bar.addWidget(self.subcontent_btn)

        # Display toggle
        self.display_btn = QtWidgets.QPushButton("Display ▾")
        self.display_btn.setToolTip("Toggle what info shows on cards")
        self.display_btn.setFixedHeight(20)
        self.display_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.display_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                color: {COLORS['text_secondary']};
                font-size: 10px;
                padding: 0 6px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['border_light']};
                color: {COLORS['text']};
            }}
        """)
        self.display_btn.clicked.connect(self._show_display_menu)
        filter_bar.addWidget(self.display_btn)

        filter_bar.addStretch()

        # Zoom controls
        zoom_container = QtWidgets.QFrame()
        zoom_container.setFixedHeight(20)
        zoom_container.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
            }}
        """)
        zoom_layout = QtWidgets.QHBoxLayout(zoom_container)
        zoom_layout.setContentsMargins(1, 0, 1, 0)
        zoom_layout.setSpacing(0)

        zoom_out = QtWidgets.QPushButton("−")
        zoom_out.setToolTip("Smaller")
        zoom_out.setFixedSize(18, 18)
        zoom_out.setCursor(QtCore.Qt.PointingHandCursor)
        zoom_out.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                color: {COLORS['accent']};
            }}
        """)
        zoom_out.clicked.connect(self._zoom_out)
        zoom_layout.addWidget(zoom_out)

        zoom_in = QtWidgets.QPushButton("+")
        zoom_in.setToolTip("Larger")
        zoom_in.setFixedSize(18, 18)
        zoom_in.setCursor(QtCore.Qt.PointingHandCursor)
        zoom_in.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                color: {COLORS['accent']};
            }}
        """)
        zoom_in.clicked.connect(self._zoom_in)
        zoom_layout.addWidget(zoom_in)

        filter_bar.addWidget(zoom_container)

        # Refresh button
        refresh_btn = QtWidgets.QPushButton("↻")
        refresh_btn.setToolTip("Refresh (Ctrl+R)")
        refresh_btn.setFixedSize(20, 20)
        refresh_btn.setCursor(QtCore.Qt.PointingHandCursor)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                border-color: {COLORS['border_light']};
            }}
        """)
        refresh_btn.clicked.connect(self._refresh_assets)
        filter_bar.addWidget(refresh_btn)

        main_layout.addLayout(filter_bar)

        # Active filter bar - persistent, shows active filters as removable chips
        self.active_filter_bar = QtWidgets.QFrame()
        self.active_filter_bar.setFixedHeight(20)
        self.active_filter_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_base']};
                border: none;
                border-bottom: 1px solid {COLORS['border']};
            }}
        """)
        filter_bar_layout = QtWidgets.QHBoxLayout(self.active_filter_bar)
        filter_bar_layout.setContentsMargins(6, 1, 6, 1)
        filter_bar_layout.setSpacing(4)

        self.filter_icon_label = QtWidgets.QLabel("▸")
        self.filter_icon_label.setStyleSheet(f"color: {COLORS['accent']}; background: transparent; font-size: 9px;")
        filter_bar_layout.addWidget(self.filter_icon_label)

        # Container for filter chips (will be populated dynamically)
        self.filter_chips_container = QtWidgets.QWidget()
        self.filter_chips_container.setStyleSheet("background: transparent;")
        self.filter_chips_layout = QtWidgets.QHBoxLayout(self.filter_chips_container)
        self.filter_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_chips_layout.setSpacing(6)
        filter_bar_layout.addWidget(self.filter_chips_container, 1)

        # Backwards compat aliases
        self.tag_filter_bar = self.active_filter_bar
        self.tag_chips_container = self.filter_chips_container
        self.tag_chips_layout = self.filter_chips_layout
        self.tag_filter_name = QtWidgets.QLabel("")
        self.tag_filter_name.hide()

        self.clear_all_filters_btn = QtWidgets.QPushButton("× Clear All")
        self.clear_all_filters_btn.setFixedHeight(18)
        self.clear_all_filters_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.clear_all_filters_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 2px;
                color: {COLORS['text_secondary']};
                padding: 1px 6px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                color: {COLORS['text']};
            }}
        """)
        self.clear_all_filters_btn.clicked.connect(self._clear_all_filters)
        filter_bar_layout.addWidget(self.clear_all_filters_btn)

        main_layout.addWidget(self.active_filter_bar)

        # Splitter
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setHandleWidth(1)

        self.collections = CollectionListWidget()
        self.collections.setMinimumWidth(100)
        self.collections.setMaximumWidth(300)
        self.collections.collection_selected.connect(self._on_collection_selected)
        splitter.addWidget(self.collections)

        self.asset_grid = AssetGridWidget()
        self.asset_grid.paste_requested.connect(self._paste_asset)
        self.asset_grid.edit_requested.connect(self._edit_asset)
        self.asset_grid.delete_requested.connect(self._delete_asset)
        self.asset_grid.publish_requested.connect(self._publish_asset)
        self.asset_grid.update_requested.connect(self._update_asset)
        self.asset_grid.tag_clicked.connect(self._on_tag_clicked)
        self.asset_grid.collection_changed.connect(self._on_collection_changed)
        self.asset_grid.navigate_to_collection.connect(self._navigate_to_collection)
        self.asset_grid.asset_selected.connect(self._on_asset_selected)
        splitter.addWidget(self.asset_grid)

        splitter.setSizes([120, 300])
        main_layout.addWidget(splitter, 1)  # Give splitter the stretch

        # Info footer - shows library stats or asset details on hover/select
        self.info_footer = QtWidgets.QFrame()
        self.info_footer.setFixedHeight(scale(24))
        self.info_footer.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_base']};
                border-top: 1px solid {COLORS['border']};
            }}
        """)
        info_footer_layout = QtWidgets.QHBoxLayout(self.info_footer)
        info_footer_layout.setContentsMargins(8, 2, 8, 2)
        info_footer_layout.setSpacing(6)

        # Asset info area (name, context, artist) — hidden by default
        self.footer_asset_info = QtWidgets.QLabel("")
        self.footer_asset_info.setStyleSheet(f"color: {COLORS['text']}; font-size: 10px; background: transparent;")
        self.footer_asset_info.hide()
        info_footer_layout.addWidget(self.footer_asset_info)

        # Tag chips container for footer
        self.footer_tags_container = QtWidgets.QWidget()
        self.footer_tags_container.setStyleSheet("background: transparent;")
        self.footer_tags_layout = QtWidgets.QHBoxLayout(self.footer_tags_container)
        self.footer_tags_layout.setContentsMargins(0, 0, 0, 0)
        self.footer_tags_layout.setSpacing(4)
        self.footer_tags_container.hide()
        info_footer_layout.addWidget(self.footer_tags_container)

        info_footer_layout.addStretch()

        # Stats label (always at the right)
        self.stats_label = QtWidgets.QLabel("")
        self.stats_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 9px; background: transparent;")
        info_footer_layout.addWidget(self.stats_label)

        main_layout.addWidget(self.info_footer, 0)  # No stretch

        # Toast notification (overlay)
        self.toast = ToastWidget(self)
        self.toast.setFixedHeight(scale(32))
        self.toast.move(10, 10)

        self._update_stats()

    def resizeEvent(self, event):
        """Handle resize to reposition toast."""
        super().resizeEvent(event)
        # Position toast at top center
        if hasattr(self, 'toast'):
            toast_width = min(300, self.width() - 20)
            self.toast.setFixedWidth(toast_width)
            x = (self.width() - toast_width) // 2
            self.toast.move(x, 10)

    def show_toast(self, message, toast_type='info', duration=3000, action_text=None, action_callback=None):
        """Show a toast notification."""
        self.toast.show_message(message, toast_type, duration, action_text, action_callback)

    def _zoom_in(self):
        sizes = ['tiny', 'small', 'medium', 'large', 'xlarge']
        idx = sizes.index(self.asset_grid._card_size) if self.asset_grid._card_size in sizes else 2
        if idx < len(sizes) - 1:
            self.asset_grid._card_size = sizes[idx + 1]
            self.asset_grid._last_columns = 0
            self._refresh_assets()  # Respects grouping state

    def _zoom_out(self):
        sizes = ['tiny', 'small', 'medium', 'large', 'xlarge']
        idx = sizes.index(self.asset_grid._card_size) if self.asset_grid._card_size in sizes else 2
        if idx > 0:
            self.asset_grid._card_size = sizes[idx - 1]
            self.asset_grid._last_columns = 0
            self._refresh_assets()  # Respects grouping state

    def _refresh_assets(self):
        if not SOPDROP_AVAILABLE:
            self.asset_grid.set_assets([])
            return

        # Clean up stale 'syncing' statuses (drafts expire after 24h)
        try:
            library.cleanup_stale_syncing()
        except Exception:
            pass

        self.asset_grid.set_loading(True)
        QtWidgets.QApplication.processEvents()

        from sopdrop.config import get_active_library

        # Update the grid's library type for badge display
        current_library = get_active_library()
        self.asset_grid.set_library_type(current_library)
        self.asset_grid._display_settings = self._display_settings

        # Convert tag filters set to list for API
        active_tags = list(self.current_tag_filters) if self.current_tag_filters else None

        # Resolve "Current Context" on each refresh
        context_filter = self.current_context_filter
        if self.context_combo.currentData() == "__current__":
            context_filter = self._detect_current_context()

        kwargs = {
            'query': self.search_input.text() or "",
            'context': context_filter,
            'tags': active_tags,
            'limit': 100,
        }

        if self.current_collection == "__recent__":
            assets = library.get_recent_assets(limit=50)
            # Apply filters to recent assets
            if kwargs['query']:
                q = kwargs['query'].lower()
                assets = [a for a in assets if q in a['name'].lower()]
            if kwargs['context']:
                assets = [a for a in assets if a.get('context') == kwargs['context']]
            if active_tags:
                assets = [a for a in assets if all(
                    t.lower() in [x.lower() for x in (a.get('tags') or [])]
                    for t in active_tags
                )]
        elif self.current_collection == "__favorites__":
            assets = library.get_frequent_assets(limit=50)
            # Apply filters to frequent assets
            if kwargs['query']:
                q = kwargs['query'].lower()
                assets = [a for a in assets if q in a['name'].lower()]
            if kwargs['context']:
                assets = [a for a in assets if a.get('context') == kwargs['context']]
            if active_tags:
                assets = [a for a in assets if all(
                    t.lower() in [x.lower() for x in (a.get('tags') or [])]
                    for t in active_tags
                )]
        elif self.current_collection or self.current_collections:
            # Get assets from selected collection(s) (and children if subcontent enabled)
            # Multi-select: use the set; single-select: use the single ID
            if self.current_collections:
                coll_ids = list(self.current_collections)
            else:
                coll_ids = [self.current_collection]
            if self.subcontent_btn.isChecked():
                for cid in list(coll_ids):
                    coll_ids.extend(self._get_descendant_collection_ids(cid))

            seen_ids = set()
            assets = []
            for cid in coll_ids:
                for a in library.get_collection_assets(cid):
                    if a['id'] not in seen_ids:
                        seen_ids.add(a['id'])
                        a['_source_collection'] = cid
                        assets.append(a)

            if kwargs['query']:
                q = kwargs['query'].lower()
                assets = [a for a in assets if q in a['name'].lower()]
            if kwargs['context']:
                assets = [a for a in assets if a['context'] == kwargs['context']]
            if active_tags:
                # Filter to assets that have ALL selected tags
                assets = [a for a in assets if all(
                    t.lower() in [x.lower() for x in (a.get('tags') or [])]
                    for t in active_tags
                )]
        else:
            assets = library.search_assets(**kwargs)

        # Hide pending-delete assets (single and bulk)
        pending_id = getattr(self, '_pending_delete_id', None)
        pending_bulk = set(getattr(self, '_pending_bulk_delete_ids', []))
        if pending_id:
            pending_bulk.add(pending_id)
        if pending_bulk:
            assets = [a for a in assets if a.get('id') not in pending_bulk]

        # Apply sorting
        sort_key = self.sort_combo.currentData()
        if sort_key == "name_asc":
            assets = sorted(assets, key=lambda a: a.get('name', '').lower())
        elif sort_key == "name_desc":
            assets = sorted(assets, key=lambda a: a.get('name', '').lower(), reverse=True)
        elif sort_key == "use_count":
            assets = sorted(assets, key=lambda a: a.get('use_count', 0), reverse=True)
        elif sort_key == "node_count":
            assets = sorted(assets, key=lambda a: a.get('node_count', 0), reverse=True)
        elif sort_key == "recent":
            assets = sorted(assets, key=lambda a: a.get('updated_at', a.get('created_at', '')), reverse=True)

        # Set contextual empty message
        has_coll = self.current_collection or self.current_collections
        if not assets:
            query = self.search_input.text().strip()
            if query:
                self.asset_grid.set_empty_message(f"No results for \"{query}\"")
            elif context_filter:
                self.asset_grid.set_empty_message(f"No {context_filter.upper()} assets")
            elif self.current_collection == "__recent__":
                self.asset_grid.set_empty_message("No recently used assets")
            elif self.current_collection == "__favorites__":
                self.asset_grid.set_empty_message("No frequently used assets yet")
            elif has_coll:
                self.asset_grid.set_empty_message("This collection is empty\nDrag assets here to add them")
            else:
                self.asset_grid.set_empty_message("No assets yet\nSave nodes using + Save Nodes")

        # Handle grouping by collection
        # Group when: (1) no collection selected, (2) multi-select, or (3) single collection with subs
        should_group = self.group_btn.isChecked() and (
            not has_coll
            or len(self.current_collections) > 1
            or (has_coll and self.subcontent_btn.isChecked())
        )
        if should_group:
            groups = self._group_assets_by_collection(assets)
            self.asset_grid.set_grouped_assets(groups)
        else:
            self.asset_grid.set_assets(assets)

        self._update_stats()

    def _get_descendant_collection_ids(self, collection_id):
        """Get all descendant collection IDs for subcontent inclusion."""
        if not SOPDROP_AVAILABLE:
            return []
        result = []
        children = library.list_collections(parent_id=collection_id)
        for child in children:
            result.append(child['id'])
            result.extend(self._get_descendant_collection_ids(child['id']))
        return result

    def _flatten_collection_tree(self, tree, parent_path="", use_paths=True):
        """Flatten a nested collection tree into a dict of id -> display name.

        Args:
            tree: Nested collection tree from library.get_collection_tree()
            parent_path: Accumulated parent path for breadcrumb-style names
            use_paths: If True, show "Parent / Child" paths. If False, just names.
        """
        result = {}
        for coll in tree:
            name = coll['name']
            if use_paths and parent_path:
                display = f"{parent_path} / {name}"
            else:
                display = name
            result[coll['id']] = display
            if coll.get('children'):
                next_path = f"{parent_path} / {name}" if parent_path else name
                result.update(self._flatten_collection_tree(
                    coll['children'], next_path, use_paths
                ))
        return result

    def _group_assets_by_collection(self, assets):
        """Group assets by their collections."""
        if not SOPDROP_AVAILABLE:
            return [{'name': 'All', 'id': None, 'assets': assets}]

        # Get ALL collections (including children) via tree flattening
        # Use full paths in "All Assets" view, short names when viewing subcontent
        tree = library.get_collection_tree()
        has_coll = self.current_collection or self.current_collections
        use_paths = not has_coll  # Full paths only for top-level view
        coll_map = self._flatten_collection_tree(tree, use_paths=use_paths)

        # Group assets
        grouped = {}  # coll_id -> list of assets
        ungrouped = []

        for asset in assets:
            # Prefer _source_collection (set during subcontent fetch) over collections[0]
            source_coll = asset.get('_source_collection')
            if source_coll and source_coll in coll_map:
                coll_id = source_coll
            else:
                asset_colls = asset.get('collections', [])
                if asset_colls:
                    coll_id = asset_colls[0]['id'] if isinstance(asset_colls[0], dict) else asset_colls[0]
                else:
                    coll_id = None

            if coll_id:
                if coll_id not in grouped:
                    grouped[coll_id] = []
                grouped[coll_id].append(asset)
            else:
                ungrouped.append(asset)

        # Build result list sorted by collection name
        result = []
        for coll_id in sorted(grouped.keys(), key=lambda cid: coll_map.get(cid, '').lower()):
            result.append({
                'name': coll_map.get(coll_id, 'Unknown'),
                'id': coll_id,
                'assets': grouped[coll_id],
            })
        if ungrouped:
            result.append({'name': 'Uncategorized', 'id': None, 'assets': ungrouped})

        return result

    def _update_stats(self):
        if SOPDROP_AVAILABLE:
            stats = library.get_library_stats()
            self.stats_label.setText(f"{stats['asset_count']} assets \u2022 {stats['collection_count']} collections \u2022 {stats['total_size_mb']} MB")

    def _on_asset_selected(self, asset):
        """Update the info footer when an asset is clicked/selected."""
        if not asset:
            self._clear_footer_info()
            return

        # Build info text: name + context + node count / file size
        name = asset.get('name', 'Untitled')
        context = asset.get('context', '').upper()
        asset_type = asset.get('asset_type', 'node')
        node_count = asset.get('node_count', 0)
        file_size = asset.get('file_size', 0)

        parts = [f"<b>{name}</b>"]
        if context:
            ctx_color = get_context_color(context.lower())
            parts.append(f'<span style="color: {ctx_color};">{context}</span>')
        if asset_type == 'hda':
            parts.append('HDA')
        elif node_count:
            parts.append(f'{node_count} nodes')
        if file_size:
            if file_size > 1024 * 1024:
                parts.append(f'{file_size / (1024*1024):.1f} MB')
            elif file_size > 1024:
                parts.append(f'{file_size / 1024:.0f} KB')

        self.footer_asset_info.setText(' \u2022 '.join(parts))
        self.footer_asset_info.setTextFormat(QtCore.Qt.RichText)
        self.footer_asset_info.show()

        # Build tag chips
        while self.footer_tags_layout.count():
            item = self.footer_tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        tags = asset.get('tags', [])
        if isinstance(tags, str):
            import json as _json
            try:
                tags = _json.loads(tags)
            except Exception:
                tags = []

        if tags:
            for tag_text in tags[:5]:
                tag_btn = QtWidgets.QPushButton(tag_text)
                tag_btn.setCursor(QtCore.Qt.PointingHandCursor)
                tag_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: rgba(255,255,255,0.1);
                        color: {COLORS['text_secondary']};
                        font-size: 9px;
                        padding: 1px 5px;
                        border-radius: 3px;
                        border: none;
                    }}
                    QPushButton:hover {{
                        background-color: rgba(255,255,255,0.25);
                        color: {COLORS['text']};
                    }}
                """)
                tag_btn.clicked.connect(
                    lambda checked=False, t=tag_text: self._on_tag_clicked(t)
                )
                self.footer_tags_layout.addWidget(tag_btn)
            self.footer_tags_container.show()
        else:
            self.footer_tags_container.hide()

    def _clear_footer_info(self):
        """Clear asset info from the footer."""
        self.footer_asset_info.hide()
        self.footer_tags_container.hide()
        while self.footer_tags_layout.count():
            item = self.footer_tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_search(self, text):
        self._update_filter_chips()
        self._search_timer.start()

    def _execute_search(self):
        self._refresh_assets()
        self._save_ui_state()

    def _on_context_changed(self, index):
        self.current_context_filter = self.context_combo.currentData()
        # Resolve "Current Context" to actual Houdini network context
        if self.current_context_filter == "__current__":
            self.current_context_filter = self._detect_current_context()
        self._update_context_combo_style()
        self._update_filter_chips()
        self._refresh_assets()
        self._save_ui_state()

    def _detect_current_context(self):
        """Detect the active network editor's context."""
        try:
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if pane:
                ctx = pane.pwd().childTypeCategory().name().lower()
                # Map Houdini category names to our context names
                ctx_map = {
                    'sop': 'sop', 'object': 'obj', 'cop2': 'cop',
                    'vop': 'vop', 'dop': 'dop', 'top': 'top',
                    'lop': 'lop', 'chop': 'chop', 'rop': 'out',
                    'driver': 'out',
                }
                return ctx_map.get(ctx, ctx)
        except Exception:
            pass
        return None

    def _update_context_combo_style(self):
        """Update context combo style to match selected context."""
        ctx = self.current_context_filter
        if ctx:
            color = get_context_color(ctx)
            self.context_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {COLORS['bg_medium']};
                    border: 1px solid {color};
                    border-radius: 2px;
                    padding: 1px 4px;
                    color: {color};
                }}
                QComboBox:hover {{
                    background-color: {COLORS['bg_light']};
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 12px;
                }}
            """)
        else:
            self.context_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {COLORS['bg_medium']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 2px;
                    padding: 1px 4px;
                }}
                QComboBox:hover {{
                    border-color: {COLORS['border_light']};
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 12px;
                }}
            """)

    def _on_collection_selected(self, coll_id):
        """Handle collection selection. coll_id can be a single ID, a set of IDs, or None."""
        if isinstance(coll_id, set):
            # Multi-select: store both the set and a compat single value
            self.current_collections = coll_id
            self.current_collection = None  # Not a single collection
        else:
            # Single select (including system items like __recent__)
            self.current_collection = coll_id
            self.current_collections = {coll_id} if coll_id and not str(coll_id).startswith("__") else set()
            # Auto-set sort to match system views
            if coll_id == "__recent__":
                self.sort_combo.setCurrentIndex(self.sort_combo.findData("recent"))
            elif coll_id == "__favorites__":
                self.sort_combo.setCurrentIndex(self.sort_combo.findData("use_count"))
        self._update_filter_chips()
        self._save_ui_state()
        self._refresh_assets()

    def _on_sort_changed(self, index):
        self._refresh_assets()
        self._save_ui_state()

    def _on_group_changed(self):
        self._refresh_assets()
        self._save_ui_state()

    def _on_subcontent_changed(self):
        self._refresh_assets()
        self._save_ui_state()

    def _navigate_to_collection(self, coll_id):
        """Navigate to a collection (from group separator click)."""
        self.collections.select_collection(coll_id)

    def _show_display_menu(self):
        """Show display settings popup that stays open for multiple toggles."""
        popup = _CheckboxPopup(self)

        popup.add_checkbox("Name", self._display_settings.get('name', True),
                           lambda v: self._toggle_display('name', v))
        popup.add_checkbox("Context Badge", self._display_settings.get('context', True),
                           lambda v: self._toggle_display('context', v))
        popup.add_checkbox("Tags", self._display_settings.get('tags', False),
                           lambda v: self._toggle_display('tags', v))

        pos = self.display_btn.mapToGlobal(self.display_btn.rect().bottomLeft())
        popup.show_at(pos)

    def _toggle_display(self, key, value):
        """Toggle a display setting and refresh."""
        self._display_settings[key] = value
        self._refresh_assets()
        self._save_ui_state()

    def _show_tags_menu(self):
        """Show a popup with checkboxes for multi-select tag filtering (stays open)."""
        if not SOPDROP_AVAILABLE:
            return

        all_tags = library.get_all_tags()
        popup = _CheckboxPopup(self, max_height=300)

        if not all_tags:
            popup.add_label("No tags yet")
        else:
            if self.current_tag_filters:
                def _clear_and_close():
                    self._clear_tag_filter()
                    popup.close()
                popup.add_button(f"Clear all ({len(self.current_tag_filters)})", _clear_and_close)
                popup.add_separator()

            for tag_info in all_tags[:30]:
                tag = tag_info['tag']
                count = tag_info['count']
                is_active = tag in self.current_tag_filters

                def make_handler(t):
                    return lambda checked: self._toggle_tag_filter(t, checked)
                popup.add_checkbox(f"{tag}  ({count})", is_active, make_handler(tag))

            if len(all_tags) > 30:
                popup.add_label(f"... and {len(all_tags) - 30} more")

        pos = self.tags_btn.mapToGlobal(self.tags_btn.rect().bottomLeft())
        popup.show_at(pos)

    def _toggle_tag_filter(self, tag, checked):
        """Toggle a tag in the filter set."""
        if checked:
            self.current_tag_filters.add(tag)
        else:
            self.current_tag_filters.discard(tag)

        # Legacy compat
        self.current_tag_filter = list(self.current_tag_filters)[0] if self.current_tag_filters else None

        self._update_tag_chips()
        self._update_tags_btn_style()
        self._refresh_assets()
        self._save_ui_state()

    def _on_tag_clicked(self, tag):
        """Handle clicking a tag (from asset card) - adds to filters."""
        self.current_tag_filters.add(tag)
        self.current_tag_filter = tag  # Legacy compat

        self._update_tag_chips()
        self._update_tags_btn_style()
        self._refresh_assets()
        self._save_ui_state()

    def _clear_tag_filter(self):
        """Clear all tag filters."""
        self.current_tag_filters.clear()
        self.current_tag_filter = None

        self._update_filter_chips()
        self._update_tags_btn_style()
        self._refresh_assets()
        self._save_ui_state()

    def _clear_all_filters(self):
        """Clear all active filters (search, context, tags, collections)."""
        self.search_input.setText("")
        self.context_combo.setCurrentIndex(0)  # "All"
        self.current_context_filter = None
        self.current_tag_filters.clear()
        self.current_tag_filter = None
        self.current_collection = None
        self.current_collections.clear()
        self.collections.deselect_all()
        # Re-select "All Assets" in sidebar
        self.collections.select_collection(None)

        self._update_filter_chips()
        self._update_tags_btn_style()
        self._update_context_combo_style()
        self._refresh_assets()
        self._save_ui_state()

    def _remove_single_tag_filter(self, tag):
        """Remove a single tag from filters."""
        self.current_tag_filters.discard(tag)
        self.current_tag_filter = list(self.current_tag_filters)[0] if self.current_tag_filters else None

        self._update_filter_chips()
        self._update_tags_btn_style()
        self._refresh_assets()
        self._save_ui_state()

    def _remove_collection_filter(self, coll_id):
        """Remove a collection from the active filter (chip X clicked)."""
        if coll_id and coll_id in self.current_collections:
            self.current_collections.discard(coll_id)
            if not self.current_collections:
                self.current_collection = None
        else:
            # Single collection mode — clear it
            self.current_collection = None
            self.current_collections.clear()

        # Update sidebar selection to match
        self.collections.deselect_all()
        for cid in self.current_collections:
            self.collections.set_selected(cid, True)

        self._update_filter_chips()
        self._refresh_assets()
        self._save_ui_state()

    def _make_filter_chip(self, text, color, on_remove):
        """Create a removable filter chip widget."""
        chip = QtWidgets.QFrame()
        chip.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 3px;
            }}
        """)
        chip_layout = QtWidgets.QHBoxLayout(chip)
        chip_layout.setContentsMargins(6, 1, 4, 1)
        chip_layout.setSpacing(3)

        label = QtWidgets.QLabel(text)
        label.setStyleSheet(f"color: white; font-size: 10px; background: transparent;")
        chip_layout.addWidget(label)

        remove_btn = QtWidgets.QPushButton("×")
        remove_btn.setFixedSize(12, 12)
        remove_btn.setCursor(QtCore.Qt.PointingHandCursor)
        remove_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: rgba(255,255,255,0.7);
                font-size: 11px;
                padding: 0;
            }}
            QPushButton:hover {{
                color: white;
            }}
        """)
        remove_btn.clicked.connect(on_remove)
        chip_layout.addWidget(remove_btn)

        return chip

    def _update_filter_chips(self):
        """Update the active filter bar with chips for all active filters."""
        # Clear existing chips
        while self.filter_chips_layout.count():
            item = self.filter_chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_filters = False

        # Search query chip
        query = self.search_input.text().strip()
        if query:
            has_filters = True
            chip = self._make_filter_chip(
                f'"{query}"', COLORS['bg_lighter'],
                lambda: self.search_input.setText("")
            )
            self.filter_chips_layout.addWidget(chip)

        # Context filter chip
        ctx = self.context_combo.currentData()
        if ctx and ctx != "__current__":
            has_filters = True
            chip = self._make_filter_chip(
                ctx.upper(), get_context_color(ctx),
                lambda: self.context_combo.setCurrentIndex(0)
            )
            self.filter_chips_layout.addWidget(chip)
        elif ctx == "__current__":
            resolved = self._detect_current_context()
            if resolved:
                has_filters = True
                chip = self._make_filter_chip(
                    f"Current: {resolved.upper()}", get_context_color(resolved),
                    lambda: self.context_combo.setCurrentIndex(0)
                )
                self.filter_chips_layout.addWidget(chip)

        # Collection chips
        if self.current_collections:
            for coll_id in sorted(self.current_collections):
                has_filters = True
                coll_name = coll_id  # Fallback
                if SOPDROP_AVAILABLE:
                    coll = library.get_collection(coll_id)
                    if coll:
                        coll_name = coll.get('name', coll_id)
                chip = self._make_filter_chip(
                    f"\u25A3 {coll_name}", COLORS['accent_dim'],
                    lambda checked=False, cid=coll_id: self._remove_collection_filter(cid)
                )
                self.filter_chips_layout.addWidget(chip)
        elif self.current_collection and not str(self.current_collection).startswith("__"):
            has_filters = True
            coll_name = self.current_collection
            if SOPDROP_AVAILABLE:
                coll = library.get_collection(self.current_collection)
                if coll:
                    coll_name = coll.get('name', self.current_collection)
            chip = self._make_filter_chip(
                f"\u25A3 {coll_name}", COLORS['accent_dim'],
                lambda: self._remove_collection_filter(None)
            )
            self.filter_chips_layout.addWidget(chip)

        # Tag chips
        for tag in sorted(self.current_tag_filters):
            has_filters = True
            chip = self._make_filter_chip(
                tag, COLORS['accent_dim'],
                lambda checked=False, t=tag: self._remove_single_tag_filter(t)
            )
            self.filter_chips_layout.addWidget(chip)

        self.filter_chips_layout.addStretch()

        # Toggle "Filter:" label and "Clear All" button visibility
        self.filter_icon_label.setVisible(has_filters)
        self.clear_all_filters_btn.setVisible(has_filters)

        # Keep bar styling consistent — chips already indicate active filters
        self.active_filter_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_base']};
                border: none;
                border-bottom: 1px solid {COLORS['border']};
            }}
        """)

    # Backwards compat alias
    def _update_tag_chips(self):
        self._update_filter_chips()

    def _update_tags_btn_style(self):
        """Update tags button style to show active filters."""
        count = len(self.current_tag_filters)
        if count > 0:
            self.tags_btn.setText(f"Tags ({count}) ▾")
            self.tags_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLORS['accent']};
                    border: 1px solid {COLORS['accent']};
                    border-radius: 2px;
                    color: white;
                    padding: 1px 6px;
                }}
                QPushButton:hover {{
                    background-color: {COLORS['accent_hover']};
                }}
            """)
        else:
            self.tags_btn.setText("Tags ▾")
            self.tags_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLORS['bg_medium']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 2px;
                    color: {COLORS['text']};
                    padding: 1px 6px;
                }}
                QPushButton:hover {{
                    border-color: {COLORS['border_light']};
                }}
            """)

    def _on_collection_changed(self):
        self.collections.refresh()
        self._refresh_assets()

    def _save_selection(self):
        print("[Sopdrop] Save button clicked")
        if not SOPDROP_AVAILABLE:
            hou.ui.displayMessage("Sopdrop library not available")
            return

        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if not pane:
            hou.ui.displayMessage("No network editor found")
            return

        items = pane.pwd().selectedItems()
        if not items:
            hou.ui.displayMessage("Please select some nodes first")
            return

        print(f"[Sopdrop] Opening save dialog with {len(items)} items")
        try:
            # Use Houdini's main window as parent for better compatibility
            parent = hou.qt.mainWindow()
            dialog = SaveToLibraryDialog(items, parent=parent)
            dialog.raise_()
            dialog.activateWindow()
            result = dialog.exec_()
            print(f"[Sopdrop] Dialog result: {result}")
            if result == QtWidgets.QDialog.Accepted:
                self.collections.refresh()
                self._refresh_assets()
                self.show_toast("Asset saved to library", 'success', 2500)
        except Exception as e:
            print(f"[Sopdrop] Error showing save dialog: {e}")
            import traceback
            traceback.print_exc()
            hou.ui.displayMessage(f"Error opening save dialog: {e}")

    def _save_vex_snippet(self):
        """Open the Save VEX Snippet dialog."""
        if not SOPDROP_AVAILABLE:
            hou.ui.displayMessage("Sopdrop library not available")
            return

        # Try to grab code from active wrangle parameter
        initial_code = ""
        try:
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if pane:
                selected = pane.pwd().selectedItems()
                if selected and len(selected) == 1:
                    node = selected[0]
                    if hasattr(node, 'parm'):
                        for pname in ('snippet', 'code', 'vexcode', 'vex_code', 'script'):
                            p = node.parm(pname)
                            if p:
                                initial_code = p.eval() or ""
                                break
        except Exception:
            pass

        try:
            parent = hou.qt.mainWindow()
            dialog = SaveVexDialog(initial_code=initial_code, parent=parent)
            dialog.raise_()
            dialog.activateWindow()
            result = dialog.exec_()
            if result == QtWidgets.QDialog.Accepted:
                self.collections.refresh()
                self._refresh_assets()
                self.show_toast("VEX snippet saved to library", 'success', 2500)
        except Exception as e:
            print(f"[Sopdrop] Error showing VEX save dialog: {e}")
            import traceback
            traceback.print_exc()

    def _paste_asset(self, asset_id):
        if not SOPDROP_AVAILABLE:
            return

        try:
            asset = library.get_asset(asset_id)
            if not asset:
                self.show_toast("Asset not found", 'error', 3000)
                return

            # Check if this is an HDA
            if asset.get('asset_type') == 'hda':
                self._install_hda(asset_id)
                return

            # Regular node package paste
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if not pane:
                hou.ui.displayMessage("No network editor found")
                return

            target = pane.pwd()
            package = library.load_asset_package(asset_id)
            if not package:
                hou.ui.displayMessage("Failed to load asset")
                return

            target_ctx = target.childTypeCategory().name().lower()
            pkg_ctx = package.get('context', '').lower()
            ctx_map = {'sop': 'sop', 'object': 'obj', 'vop': 'vop', 'dop': 'dop', 'cop2': 'cop', 'top': 'top', 'lop': 'lop', 'chop': 'chop'}
            target_ctx = ctx_map.get(target_ctx, target_ctx)

            if pkg_ctx and target_ctx and pkg_ctx != target_ctx:
                reply = hou.ui.displayMessage(
                    f"This is a {pkg_ctx.upper()} asset but you're in a {target_ctx.upper()} network.\n\n"
                    f"Navigate to a {pkg_ctx.upper()} network first, or paste anyway (may not work).",
                    buttons=("Paste Anyway", "Cancel"),
                    severity=hou.severityType.Warning,
                    default_choice=1,
                    title="Context Mismatch",
                )
                if reply != 0:
                    return

            # Get paste position - use center of visible area since user is browsing the library panel
            # (cursor is over the library, not the network editor)
            bounds = pane.visibleBounds()
            center = bounds.center()
            position = (center[0], center[1])

            import_items(package, target, position=position)
            library.record_asset_use(asset_id)

            name = asset['name'] if asset else "Asset"
            self.show_toast(f"Pasted {name}", 'success', 2000)

        except Exception as e:
            self.show_toast(f"Paste failed: {e}", 'error', 4000)

    def _install_hda(self, asset_id):
        """Install an HDA and place an instance of it."""
        if not SOPDROP_AVAILABLE:
            return

        try:
            asset = library.get_asset(asset_id)
            if not asset:
                self.show_toast("HDA not found", 'error', 3000)
                return

            hda_type_name = asset.get('hda_type_name', '')
            name = asset.get('name', 'HDA')

            # Check if HDA is already installed
            already_installed = False
            if hda_type_name:
                try:
                    node_type = hou.nodeType(hou.sopNodeTypeCategory(), hda_type_name)
                    if node_type is None:
                        # Try other categories
                        for cat in [hou.objNodeTypeCategory(), hou.dopNodeTypeCategory(),
                                    hou.cop2NodeTypeCategory(), hou.topNodeTypeCategory(),
                                    hou.lopNodeTypeCategory(), hou.chopNodeTypeCategory()]:
                            node_type = hou.nodeType(cat, hda_type_name)
                            if node_type:
                                break
                    already_installed = node_type is not None
                except:
                    pass

            # Warn about license compatibility before installing
            if not already_installed:
                hda_license = asset.get('license_type', '')
                current_license = library.detect_houdini_license() or 'commercial'
                license_rank = {'apprentice': 0, 'education': 0, 'indie': 1, 'commercial': 2}
                hda_rank = license_rank.get(hda_license, 2)
                cur_rank = license_rank.get(current_license, 2)

                if hda_license and hda_rank < cur_rank:
                    tier_name = 'Non-Commercial' if hda_license in ('apprentice', 'education') else 'Indie'
                    result = hou.ui.displayMessage(
                        f"This HDA was saved with a {tier_name} license.\n\n"
                        f"Installing it will downgrade your {current_license.title()} "
                        f"session to {tier_name} mode.\n\n"
                        "All files saved in this session will use the lower-tier format.",
                        buttons=("Install Anyway", "Cancel"),
                        severity=hou.severityType.Warning,
                        default_choice=1,
                        title="License Compatibility Warning",
                    )
                    if result == 1:
                        return

                library.install_hda(asset_id)
                self.show_toast(f"Installed {name}", 'success', 1500)
                print(f"[Sopdrop] HDA installed: {name} ({hda_type_name})")

            # Now place an instance in the network
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if not pane:
                if not already_installed:
                    # Already showed install toast, just tell them where to find it
                    category = asset.get('hda_category', 'Sop')
                    print(f"[Sopdrop] Find it in the Tab menu under {category}")
                return

            target = pane.pwd()

            # Find the node type
            node_type = None
            target_category = target.childTypeCategory()

            if hda_type_name:
                node_type = hou.nodeType(target_category, hda_type_name)

            if not node_type:
                # HDA might be for a different context
                hda_context = asset.get('context', '').lower()
                if hda_context:
                    ctx_to_category = {
                        'sop': hou.sopNodeTypeCategory(),
                        'obj': hou.objNodeTypeCategory(),
                        'dop': hou.dopNodeTypeCategory(),
                        'cop': hou.cop2NodeTypeCategory(),
                        'cop2': hou.cop2NodeTypeCategory(),
                        'top': hou.topNodeTypeCategory(),
                        'lop': hou.lopNodeTypeCategory(),
                        'chop': hou.chopNodeTypeCategory(),
                        'vop': hou.vopNodeTypeCategory(),
                    }
                    expected_category = ctx_to_category.get(hda_context)
                    if expected_category and expected_category != target_category:
                        self.show_toast(f"HDA is {hda_context.upper()}, navigate to a {hda_context.upper()} network first", 'warning', 3000)
                        return
                    if expected_category:
                        node_type = hou.nodeType(expected_category, hda_type_name)

            if not node_type:
                self.show_toast(f"Could not find node type: {hda_type_name}", 'error', 3000)
                return

            # Get paste position
            bounds = pane.visibleBounds()
            center = bounds.center()

            # Create the node
            new_node = target.createNode(hda_type_name)
            new_node.setPosition(hou.Vector2(center[0], center[1]))
            new_node.setSelected(True, clear_all_selected=True)

            library.record_asset_use(asset_id)
            self.show_toast(f"Placed {name}", 'success', 2000)

        except Exception as e:
            self.show_toast(f"Failed: {e}", 'error', 4000)
            import traceback
            traceback.print_exc()

    def _edit_asset(self, asset_id):
        if not SOPDROP_AVAILABLE:
            return
        asset = library.get_asset(asset_id)
        if asset:
            dialog = EditAssetDialog(asset, self)
            if dialog.exec_() == QtWidgets.QDialog.Accepted:
                self._refresh_assets()

    def _delete_asset(self, asset_id):
        if not SOPDROP_AVAILABLE:
            return
        asset = library.get_asset(asset_id)
        if not asset:
            return

        name = asset['name']

        # Store pending delete info so undo can cancel it
        if not hasattr(self, '_pending_delete_timer'):
            self._pending_delete_timer = None
        if not hasattr(self, '_pending_delete_id'):
            self._pending_delete_id = None

        # Cancel any previous pending delete and execute it immediately
        if self._pending_delete_timer and self._pending_delete_timer.isActive():
            self._pending_delete_timer.stop()
            if self._pending_delete_id:
                library.delete_asset(self._pending_delete_id)

        # Set up deferred delete
        self._pending_delete_id = asset_id

        self._pending_delete_timer = QtCore.QTimer(self)
        self._pending_delete_timer.setSingleShot(True)
        self._pending_delete_timer.setInterval(5000)
        self._pending_delete_timer.timeout.connect(lambda: self._finalize_delete(asset_id))
        self._pending_delete_timer.start()

        # Hide from grid immediately
        self._refresh_assets()

        def undo_delete():
            if self._pending_delete_timer:
                self._pending_delete_timer.stop()
            self._pending_delete_id = None
            self._refresh_assets()

        self.show_toast(f"Deleted {name}", 'warning', 5000, action_text="Undo", action_callback=undo_delete)

    def _finalize_delete(self, asset_id):
        """Actually delete the asset after the undo period."""
        if self._pending_delete_id == asset_id:
            library.delete_asset(asset_id)
            self._pending_delete_id = None
            self.collections.refresh()

    def _delete_assets_bulk(self, asset_ids):
        """Delete multiple assets with a single undo toast."""
        if not SOPDROP_AVAILABLE or not asset_ids:
            return

        # Finalize any pending single delete first
        if hasattr(self, '_pending_delete_timer') and self._pending_delete_timer and self._pending_delete_timer.isActive():
            self._pending_delete_timer.stop()
            if self._pending_delete_id:
                library.delete_asset(self._pending_delete_id)
                self._pending_delete_id = None

        # Store pending bulk delete
        if not hasattr(self, '_pending_bulk_delete_ids'):
            self._pending_bulk_delete_ids = []
        self._pending_bulk_delete_ids = list(asset_ids)

        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(5000)
        timer.timeout.connect(lambda ids=list(asset_ids): self._finalize_bulk_delete(ids))
        self._pending_bulk_delete_timer = timer
        timer.start()

        # Hide from grid immediately
        self._refresh_assets()

        count = len(asset_ids)

        def undo_bulk():
            if hasattr(self, '_pending_bulk_delete_timer') and self._pending_bulk_delete_timer:
                self._pending_bulk_delete_timer.stop()
            self._pending_bulk_delete_ids = []
            self._refresh_assets()

        self.show_toast(f"Deleted {count} assets", 'warning', 5000, action_text="Undo", action_callback=undo_bulk)

    def _finalize_bulk_delete(self, asset_ids):
        """Actually delete bulk assets after undo period."""
        if hasattr(self, '_pending_bulk_delete_ids') and self._pending_bulk_delete_ids == asset_ids:
            for aid in asset_ids:
                library.delete_asset(aid)
            self._pending_bulk_delete_ids = []
            self.collections.refresh()

    def _update_asset(self, asset_id):
        """Update an existing asset with currently selected nodes."""
        if not SOPDROP_AVAILABLE:
            return

        asset = library.get_asset(asset_id)
        if not asset:
            return

        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if not pane:
            hou.ui.displayMessage("No network editor found")
            return

        items = pane.pwd().selectedItems()
        if not items:
            hou.ui.displayMessage("Please select some nodes to update this asset with")
            return

        # Show save dialog with existing asset for update
        dialog = SaveToLibraryDialog(items, existing_asset=asset, parent=self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.collections.refresh()
            self._refresh_assets()
            self.show_toast(f"Updated {asset['name']}", 'success', 2500)

    def _open_settings(self):
        """Open the settings dialog."""
        dialog = SettingsDialog(self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._update_library_toggle()
            self.collections.refresh()
            self._refresh_assets()

    def _update_library_toggle(self):
        """Update the library toggle buttons."""
        if not SOPDROP_AVAILABLE:
            self.personal_btn.setEnabled(False)
            self.team_btn.setEnabled(False)
            return

        from sopdrop.config import get_active_library, get_team_library_path, get_team_info

        active = get_active_library()
        team_path = get_team_library_path()
        team_info = get_team_info()
        has_team = team_path is not None

        active_style = f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 2px;
                color: white;
                padding: 1px 8px;
            }}
        """
        inactive_style = f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 2px;
                color: {COLORS['text_secondary']};
                padding: 1px 8px;
            }}
            QPushButton:hover {{
                color: {COLORS['text']};
                background-color: {COLORS['bg_light']};
            }}
        """
        disabled_style = f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 2px;
                color: {COLORS['text_dim']};
                padding: 1px 8px;
            }}
        """

        is_personal = (active == "personal")
        self.personal_btn.setChecked(is_personal)
        self.personal_btn.setStyleSheet(active_style if is_personal else inactive_style)
        self.personal_btn.setToolTip("Personal library")

        is_team = (active == "team")
        self.team_btn.setChecked(is_team)
        self.team_btn.setEnabled(has_team)
        if has_team and team_info:
            team_name = team_info.get("name", "Team")
            if len(team_name) > 10:
                team_name = team_name[:9] + "…"
            self.team_btn.setText(team_name)
            self.team_btn.setStyleSheet(active_style if is_team else inactive_style)
            self.team_btn.setToolTip(f"{team_info.get('name', 'Team')}\n{team_path}")
        else:
            self.team_btn.setText("Team")
            self.team_btn.setStyleSheet(disabled_style)
            self.team_btn.setToolTip("Set up in Settings")

    def _select_library(self, library_type):
        """Switch to the specified library."""
        if not SOPDROP_AVAILABLE:
            return

        from sopdrop.config import get_active_library, get_team_library_path, set_active_library
        from sopdrop.library import close_db

        current = get_active_library()
        if library_type == current:
            return  # Already on this library

        if library_type == "team":
            team_path = get_team_library_path()
            if not team_path:
                hou.ui.displayMessage(
                    "Team library not configured.\n\n"
                    "Open Settings and set a Team Library Path first.",
                    title="Sopdrop"
                )
                self._update_library_toggle()  # Reset toggle state
                return

        set_active_library(library_type)

        # Close existing DB connections to force reconnect to new library
        close_db()

        # Reset to "All Assets" since collections are different per library
        self.current_collection = None
        self.current_collections.clear()

        # Refresh UI
        self._update_library_toggle()
        self.collections.refresh()
        self._refresh_assets()

    def _sync_from_cloud(self):
        """Sync saved assets from cloud to local library."""
        if not SOPDROP_AVAILABLE:
            hou.ui.displayMessage("Sopdrop library not available")
            return

        from sopdrop.config import get_token, get_active_library, get_team_slug
        if not get_token():
            hou.ui.displayMessage("Please log in first using the Settings tool")
            return

        is_team_mode = get_active_library() == "team"
        team_slug = get_team_slug() if is_team_mode else None

        if is_team_mode and not team_slug:
            hou.ui.displayMessage(
                "Team library mode is active but no team is configured.\n\n"
                "Go to Settings and set a Team Slug to sync team assets.",
                title="Team Sync"
            )
            return

        # Show a toast immediately so the user knows something is happening
        self.show_toast("Pulling from cloud...", 'info', 30000)
        QtWidgets.QApplication.processEvents()

        try:
            # Cleanup stale syncing (fast DB query)
            library.cleanup_stale_syncing()

            # Use the existing working sync functions
            if is_team_mode:
                result = library.sync_team_library(team_slug)
            else:
                result = library.sync_saved_assets_with_folders()

            if result.get('error'):
                self.show_toast(f"Sync failed: {result['error']}", 'error', 5000)
            elif result.get('synced', 0) == 0 and result.get('skipped', 0) == 0:
                source = f"team '{team_slug}'" if is_team_mode else "cloud"
                self.show_toast(f"No assets found in {source}", 'warning', 3000)
            else:
                msg = f"Synced {result['synced']} new assets"
                if result.get('skipped'):
                    msg += f" ({result['skipped']} already local)"
                self.show_toast(msg, 'success')
                if result.get('errors'):
                    for err in result['errors'][:5]:
                        print(f"[Sopdrop] Sync error: {err}")

        except Exception as e:
            self.show_toast(f"Sync failed: {e}", 'error', 5000)
            import traceback
            traceback.print_exc()

        self.collections.refresh()
        self._refresh_assets()

    def _publish_asset(self, asset_id):
        """Publish a local asset to the cloud, pre-filling with existing data."""
        if not SOPDROP_AVAILABLE:
            return

        asset = library.get_asset(asset_id)
        if not asset:
            return

        # Open publish dialog with pre-filled data
        try:
            import sopdrop_publish
            import importlib
            importlib.reload(sopdrop_publish)

            # Load package to get the code
            package = library.load_asset_package(asset_id)
            if not package:
                hou.ui.displayMessage("Failed to load asset package")
                return

            # Get thumbnail if exists
            thumbnail_image = None
            if asset.get('thumbnail_path'):
                thumb_path = library.get_library_thumbnails_dir() / asset['thumbnail_path']
                if thumb_path.exists():
                    thumbnail_image = QtGui.QImage(str(thumb_path))

            # Launch publish with pre-filled data
            sopdrop_publish.publish_from_library(
                package=package,
                name=asset.get('name', ''),
                description=asset.get('description', ''),
                tags=asset.get('tags', []),
                thumbnail_image=thumbnail_image,
                library_asset_id=asset_id,
            )

        except ImportError:
            # Fallback - just show message
            hou.ui.displayMessage(
                f"To publish '{asset['name']}' to sopdrop.com:\n\n"
                "1. Use the Publish shelf tool\n"
                "2. Or run: sopdrop.publish() from Python",
                title="Publish to Cloud"
            )
        except Exception as e:
            # Reset sync status since publish failed
            try:
                library.reset_syncing_status(asset_id)
            except Exception:
                pass
            hou.ui.displayMessage(f"Publish failed: {e}")


# ==============================================================================
# Houdini Icon Browser
# ==============================================================================

class HoudiniIconBrowser(QtWidgets.QDialog):
    """Dialog to browse and select Houdini icons."""

    # Fallback icons used when icons.zip is not found
    _FALLBACK_ICONS = {
        'SOP': ['SOP_scatter', 'SOP_attribwrangle', 'SOP_copy', 'SOP_blast', 'SOP_transform',
                'SOP_merge', 'SOP_subnet', 'SOP_switch', 'SOP_null', 'SOP_group',
                'SOP_vdbfrompolygons', 'SOP_polyextrude', 'SOP_subdivide', 'SOP_smooth',
                'SOP_polybevel', 'SOP_boolean', 'SOP_remesh', 'SOP_mountain', 'SOP_noise',
                'SOP_ray', 'SOP_file', 'SOP_cache', 'SOP_output', 'SOP_foreach',
                'SOP_pointwrangle', 'SOP_primwrangle', 'SOP_volumewrangle', 'SOP_detailwrangle'],
        'OBJ': ['OBJ_geo', 'OBJ_null', 'OBJ_subnet', 'OBJ_camera', 'OBJ_light',
                'OBJ_bone', 'OBJ_muscle', 'OBJ_blend', 'OBJ_fetch', 'OBJ_instance'],
        'LOP': ['LOP_material', 'LOP_sublayer', 'LOP_merge', 'LOP_switch', 'LOP_null',
                'LOP_primitiveconfigure', 'LOP_light', 'LOP_camera', 'LOP_edit', 'LOP_prune'],
        'VOP': ['VOP_constant', 'VOP_add', 'VOP_multiply', 'VOP_noise', 'VOP_texture',
                'VOP_output', 'VOP_parameter', 'VOP_mix', 'VOP_clamp', 'VOP_fit'],
        'DOP': ['DOP_rbdobject', 'DOP_rbdpackedobject', 'DOP_dopsolve', 'DOP_popnet',
                'DOP_pyrosolver', 'DOP_flipsolver', 'DOP_gravity', 'DOP_merge', 'DOP_switch'],
        'TOP': ['TOP_generic', 'TOP_ffmpegencodevideo', 'TOP_filepattern', 'TOP_imagemagick',
                'TOP_pythonscript', 'TOP_ropfetch', 'TOP_wedge', 'TOP_wait', 'TOP_partitionbyattribute'],
        'COP': ['COP2_colorwheel', 'COP2_file', 'COP2_composite', 'COP2_over', 'COP2_multiply',
                'COP2_vopcop2gen', 'COP2_color', 'COP2_blur', 'COP2_scale', 'COP2_crop'],
        'CHOP': ['CHOP_channel', 'CHOP_math', 'CHOP_noise', 'CHOP_wave', 'CHOP_lag',
                 'CHOP_filter', 'CHOP_blend', 'CHOP_export', 'CHOP_delete', 'CHOP_merge'],
        'ROP': ['ROP_mantra', 'ROP_karma', 'ROP_geometry', 'ROP_alembic', 'ROP_fbx',
                'ROP_opengl', 'ROP_fetch', 'ROP_merge', 'ROP_switch', 'ROP_comp'],
        'MISC': ['MISC_python', 'MISC_digital_asset', 'MISC_hda_unlocked', 'MISC_subnet',
                 'MISC_locked', 'COMMON_null', 'COMMON_subnet', 'COMMON_switch',
                 'NETWORKS_sop', 'NETWORKS_lop', 'NETWORKS_obj', 'NETWORKS_dop'],
    }

    # Session-level cache for discovered icons
    _discovered_icons = None

    # Preferred category display order
    _CATEGORY_ORDER = [
        'SOP', 'OBJ', 'LOP', 'VOP', 'DOP', 'TOP', 'COP2', 'CHOP', 'ROP',
    ]

    @classmethod
    def _discover_icons(cls):
        """Scan $HH/help/icons.zip for all available icons. Cached per session."""
        if cls._discovered_icons is not None:
            return cls._discovered_icons

        icons_zip_path = None
        try:
            hh = hou.text.expandString('$HH')
            if hh:
                candidate = os.path.join(hh, 'help', 'icons.zip')
                if os.path.isfile(candidate):
                    icons_zip_path = candidate
        except Exception:
            pass

        if not icons_zip_path:
            hh_env = os.environ.get('HH', '')
            if hh_env:
                candidate = os.path.join(hh_env, 'help', 'icons.zip')
                if os.path.isfile(candidate):
                    icons_zip_path = candidate

        if not icons_zip_path:
            cls._discovered_icons = cls._FALLBACK_ICONS
            return cls._discovered_icons

        try:
            categories = {}
            with zipfile.ZipFile(icons_zip_path, 'r') as z:
                for entry in z.namelist():
                    if not entry.lower().endswith('.svg'):
                        continue
                    # entries like "SOP/scatter.svg" or "MISC/python.svg"
                    parts = entry.replace('\\', '/').split('/')
                    if len(parts) < 2:
                        continue
                    cat = parts[-2]  # category folder
                    name = os.path.splitext(parts[-1])[0]  # icon name without .svg
                    icon_name = f"{cat}_{name}"
                    if cat not in categories:
                        categories[cat] = []
                    categories[cat].append(icon_name)

            # Sort icons within each category
            for cat in categories:
                categories[cat].sort()

            if categories:
                cls._discovered_icons = categories
            else:
                cls._discovered_icons = cls._FALLBACK_ICONS
        except Exception:
            cls._discovered_icons = cls._FALLBACK_ICONS

        return cls._discovered_icons

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_icon = None
        self._active_category = None
        self._icon_categories = self._discover_icons()
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Select Icon")
        self.setFixedSize(scale(500), scale(500))
        self.setStyleSheet(STYLESHEET)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Search bar
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Search icons...")
        self.search_input.setFixedHeight(scale(28))
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 4px 8px;
                color: {COLORS['text']};
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        self.search_input.textChanged.connect(self._on_search)
        layout.addWidget(self.search_input)

        # Category tab bar
        cat_bar = QtWidgets.QWidget()
        cat_bar_layout = FlowLayout(cat_bar)
        cat_bar_layout.setSpacing(3)
        self._cat_buttons = {}

        # Build ordered category list: preferred order first, then remaining sorted
        ordered_cats = []
        for cat in self._CATEGORY_ORDER:
            if cat in self._icon_categories:
                ordered_cats.append(cat)
        for cat in sorted(self._icon_categories.keys()):
            if cat not in ordered_cats:
                ordered_cats.append(cat)
        self._ordered_categories = ordered_cats

        for cat in ordered_cats:
            count = len(self._icon_categories[cat])
            btn = QtWidgets.QPushButton(f"{cat} ({count})")
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(self._cat_btn_style(False))
            btn.clicked.connect(lambda checked=False, c=cat: self._select_category(c))
            cat_bar_layout.addWidget(btn)
            self._cat_buttons[cat] = btn

        layout.addWidget(cat_bar)

        # Scrollable icon grid (populated per-category)
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                background: {COLORS['bg_medium']};
            }}
        """)

        self._icon_container = QtWidgets.QWidget()
        self._icon_container.setStyleSheet(f"background: {COLORS['bg_medium']};")
        self._icon_flow = FlowLayout(self._icon_container)
        self._icon_flow.setSpacing(4)
        self._scroll.setWidget(self._icon_container)
        layout.addWidget(self._scroll, 1)

        self._all_icon_buttons = []
        self._category_sections = []  # kept for compatibility

        # Show first category by default
        if ordered_cats:
            self._select_category(ordered_cats[0])

        # Selected icon preview + buttons row
        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setSpacing(8)

        self.preview_btn = QtWidgets.QLabel()
        self.preview_btn.setFixedSize(scale(28), scale(28))
        self.preview_btn.setStyleSheet(f"""
            QLabel {{
                background: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
            }}
        """)
        self.preview_btn.setAlignment(QtCore.Qt.AlignCenter)
        bottom_row.addWidget(self.preview_btn)

        self.preview_name = QtWidgets.QLabel("None selected")
        self.preview_name.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        bottom_row.addWidget(self.preview_name)

        bottom_row.addStretch()

        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setFixedHeight(scale(26))
        cancel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 4px 16px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(cancel_btn)

        select_btn = QtWidgets.QPushButton("Select")
        select_btn.setFixedHeight(scale(26))
        select_btn.setCursor(QtCore.Qt.PointingHandCursor)
        select_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 3px;
                padding: 4px 16px;
                color: white;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        select_btn.clicked.connect(self.accept)
        bottom_row.addWidget(select_btn)

        layout.addLayout(bottom_row)

    def _cat_btn_style(self, active):
        if active:
            return f"""
                QPushButton {{
                    background: {COLORS['accent']};
                    border: none;
                    border-radius: 3px;
                    padding: 3px 8px;
                    color: white;
                    font-size: 9px;
                    font-weight: 600;
                }}
            """
        return f"""
            QPushButton {{
                background: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 3px 8px;
                color: {COLORS['text_secondary']};
                font-size: 9px;
            }}
            QPushButton:hover {{
                background: {COLORS['bg_lighter']};
                color: {COLORS['text']};
            }}
        """

    def _select_category(self, category):
        """Show icons for the selected category."""
        if self._active_category == category:
            return
        self._active_category = category

        # Update tab styling
        for cat, btn in self._cat_buttons.items():
            btn.setStyleSheet(self._cat_btn_style(cat == category))

        # Clear and repopulate the icon grid
        self._all_icon_buttons.clear()
        self._category_sections.clear()

        # Remove old container and create fresh one
        old_widget = self._scroll.takeWidget()
        if old_widget:
            old_widget.deleteLater()

        container = QtWidgets.QWidget()
        container.setStyleSheet(f"background: {COLORS['bg_medium']};")
        flow = FlowLayout(container)
        flow.setSpacing(4)

        icons = self._icon_categories.get(category, [])
        for icon_name in icons:
            btn = self._create_icon_button(icon_name)
            flow.addWidget(btn)
            self._all_icon_buttons.append(btn)

        self._scroll.setWidget(container)
        self._icon_container = container
        self._icon_flow = flow

    def _create_icon_button(self, icon_name):
        """Create a clickable icon button."""
        btn = QtWidgets.QPushButton()
        btn.setFixedSize(scale(40), scale(40))
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setToolTip(icon_name)
        btn.setProperty("icon_name", icon_name)
        btn.setProperty("is_selected", False)
        btn.setStyleSheet(self._icon_btn_style(False))

        # Try to load the icon
        try:
            icon = hou.qt.Icon(icon_name, 28, 28)
            if icon and not icon.isNull():
                btn.setIcon(icon)
                btn.setIconSize(QtCore.QSize(28, 28))
            else:
                btn.setText(icon_name[:2])
        except:
            btn.setText(icon_name[:2])

        btn.clicked.connect(lambda: self._select_icon(icon_name))
        return btn

    def _icon_btn_style(self, selected):
        if selected:
            return f"""
                QPushButton {{
                    background: {COLORS['bg_light']};
                    border: 2px solid {COLORS['accent']};
                    border-radius: 3px;
                }}
            """
        return f"""
            QPushButton {{
                background: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
                background: {COLORS['bg_light']};
            }}
        """

    def _select_icon(self, icon_name):
        """Handle icon selection."""
        self.selected_icon = icon_name

        # Update preview
        try:
            icon = hou.qt.Icon(icon_name, 28, 28)
            if icon and not icon.isNull():
                pixmap = icon.pixmap(28, 28)
                self.preview_btn.setPixmap(pixmap)
            else:
                self.preview_btn.clear()
        except:
            self.preview_btn.clear()

        self.preview_name.setText(icon_name)
        self.preview_name.setStyleSheet(f"color: {COLORS['text']}; font-size: 10px;")

        # Highlight selected, unhighlight others
        for btn in self._all_icon_buttons:
            is_sel = btn.property("icon_name") == icon_name
            btn.setProperty("is_selected", is_sel)
            btn.setStyleSheet(self._icon_btn_style(is_sel))

    def _on_search(self, text):
        """Filter icons based on search text. Shows results across all categories."""
        text = text.lower().strip()

        if not text:
            # No search - restore category view
            if self._active_category:
                cat = self._active_category
                self._active_category = None  # force refresh
                self._select_category(cat)
            return

        # Search mode: show matches from ALL categories
        self._all_icon_buttons.clear()

        old_widget = self._scroll.takeWidget()
        if old_widget:
            old_widget.deleteLater()

        container = QtWidgets.QWidget()
        container.setStyleSheet(f"background: {COLORS['bg_medium']};")
        grid_layout = QtWidgets.QVBoxLayout(container)
        grid_layout.setContentsMargins(8, 8, 8, 8)
        grid_layout.setSpacing(6)

        total_matches = 0
        for cat in self._ordered_categories:
            icons = self._icon_categories.get(cat, [])
            matches = [ic for ic in icons if text in ic.lower()]
            if not matches:
                continue

            cat_label = QtWidgets.QLabel(f"{cat} ({len(matches)})")
            cat_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 9px; font-weight: bold; padding: 2px 0;")
            grid_layout.addWidget(cat_label)

            flow_widget = QtWidgets.QWidget()
            flow = FlowLayout(flow_widget)
            flow.setSpacing(4)
            for icon_name in matches:
                btn = self._create_icon_button(icon_name)
                flow.addWidget(btn)
                self._all_icon_buttons.append(btn)
            grid_layout.addWidget(flow_widget)
            total_matches += len(matches)

        if total_matches == 0:
            no_results = QtWidgets.QLabel("No icons found")
            no_results.setStyleSheet(f"color: {COLORS['text_dim']}; padding: 20px;")
            no_results.setAlignment(QtCore.Qt.AlignCenter)
            grid_layout.addWidget(no_results)

        grid_layout.addStretch()
        self._scroll.setWidget(container)


# ==============================================================================
# Save to Library Dialog
# ==============================================================================

class SaveToLibraryDialog(QtWidgets.QDialog):
    """Dialog for saving nodes/HDAs to the local library with optional cloud publish."""

    def __init__(self, items, existing_asset=None, parent=None):
        """
        Initialize save dialog.

        Args:
            items: Houdini items to save
            existing_asset: Optional existing asset dict to update (creates new version)
            parent: Parent widget
        """
        super().__init__(parent)
        self.items = items
        self.nodes = [i for i in items if isinstance(i, hou.Node)]
        self.existing_asset = existing_asset
        self._screenshots = []  # List of QImage objects
        self._selected_icon = None  # Houdini icon name

        # Check if this is an HDA
        self.hda_info = None
        self.is_hda = False
        if SOPDROP_AVAILABLE and len(self.nodes) == 1:
            try:
                from sopdrop.export import detect_publishable_hda
                self.hda_info = detect_publishable_hda(self.nodes)
                self.is_hda = self.hda_info is not None
            except Exception as e:
                print(f"[Sopdrop] HDA detection error: {e}")

        self._setup_ui()

    def _setup_ui(self):
        is_update = self.existing_asset is not None

        if self.is_hda:
            title_text = "Update HDA" if is_update else "Save HDA to Library"
        else:
            title_text = "Update Asset" if is_update else "Save to Library"

        self.setWindowTitle(title_text)
        self.setFixedWidth(scale(460))
        self.setStyleSheet(STYLESHEET)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        input_style = f"""
            QLineEdit, QTextEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 4px 8px;
                color: {COLORS['text']};
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """
        label_style = f"color: {COLORS['text_dim']}; font-size: 10px;"

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)

        # ── Row 1: Icon + Name + Badges ──
        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(12)

        self.icon_btn = QtWidgets.QPushButton()
        self.icon_btn.setFixedSize(scale(48), scale(48))
        self.icon_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.icon_btn.setToolTip("Click to choose icon")
        self.icon_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                font-size: 10px;
                color: {COLORS['text_dim']};
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
            }}
        """)
        self.icon_btn.setText("Icon")
        self.icon_btn.clicked.connect(self._show_icon_browser)
        self._set_default_icon()
        top_row.addWidget(self.icon_btn)

        # Name input (large, prominent)
        name_col = QtWidgets.QVBoxLayout()
        name_col.setSpacing(2)

        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("Asset name...")
        self.name_input.setFixedHeight(30)
        self.name_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: transparent;
                border: none;
                border-bottom: 2px solid {COLORS['border']};
                border-radius: 0;
                padding: 2px 0;
                font-size: 15px;
                font-weight: 600;
                color: {COLORS['text_bright']};
            }}
            QLineEdit:focus {{
                border-bottom: 2px solid {COLORS['accent']};
            }}
        """)
        if is_update:
            self.name_input.setText(self.existing_asset.get('name', ''))
        name_col.addWidget(self.name_input)

        # Stats line (subtle, under name)
        if self.is_hda:
            hda_label = self.hda_info.get('type_label', '') or self.hda_info.get('type_name', 'Unknown')
            stats_text = f"HDA · {hda_label}"
        else:
            node_count = len(self.nodes)
            total = node_count + sum(len(n.allSubChildren()) for n in self.nodes)
            stats_text = f"{node_count} nodes · {total} total"
        stats_label = QtWidgets.QLabel(stats_text)
        stats_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        name_col.addWidget(stats_label)

        top_row.addLayout(name_col, 1)

        # Badges (right side)
        badge_col = QtWidgets.QVBoxLayout()
        badge_col.setAlignment(QtCore.Qt.AlignTop)
        context = self._get_context()
        badge_row = QtWidgets.QHBoxLayout()
        badge_row.setSpacing(4)
        if self.is_hda:
            hda_badge = QtWidgets.QLabel("HDA")
            hda_badge.setStyleSheet(f"background-color: {COLORS['warning']}; color: black; font-size: 9px; font-weight: bold; padding: 2px 6px; border-radius: 3px;")
            badge_row.addWidget(hda_badge)
        ctx_badge = QtWidgets.QLabel(context.upper())
        ctx_badge.setStyleSheet(f"background-color: {get_context_color(context)}; color: white; font-size: 9px; font-weight: bold; padding: 2px 6px; border-radius: 3px;")
        badge_row.addWidget(ctx_badge)
        badge_col.addLayout(badge_row)
        top_row.addLayout(badge_col)

        layout.addLayout(top_row)

        # Update notice
        if is_update:
            update_notice = QtWidgets.QLabel(f"Updating: {self.existing_asset.get('name', 'Unknown')}")
            update_notice.setStyleSheet(f"color: {COLORS['accent']}; font-size: 10px;")
            layout.addWidget(update_notice)

        # ── Description ──
        self.desc_input = QtWidgets.QTextEdit()
        self.desc_input.setFixedHeight(48)
        self.desc_input.setPlaceholderText("Description (optional)")
        self.desc_input.setStyleSheet(f"""
            QTextEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
                color: {COLORS['text']};
            }}
            QTextEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        if is_update:
            self.desc_input.setText(self.existing_asset.get('description', ''))
        layout.addWidget(self.desc_input)

        # ── Tags ──
        self.tags_widget = TagInputWidget()
        if is_update:
            self.tags_widget.set_tags(self.existing_asset.get('tags', []))
        layout.addWidget(self.tags_widget)

        # ── Save-to + Collection (inline row) ──
        options_row = QtWidgets.QHBoxLayout()
        options_row.setSpacing(16)

        # Library selector (left — collections depend on which library)
        self._has_team_library = False
        self.library_selector = QtWidgets.QComboBox()
        self.library_selector.setFixedHeight(24)
        self.library_selector.addItem("Personal Library", "personal")

        if SOPDROP_AVAILABLE:
            from sopdrop.config import get_team_info
            team_info = get_team_info()
            if team_info:
                team_name = team_info.get('name', 'Team Library')
                self.library_selector.addItem(f"\u2630 {team_name}", "team")
                self._has_team_library = True

        lib_col = QtWidgets.QVBoxLayout()
        lib_col.setSpacing(2)
        lib_lbl = QtWidgets.QLabel("Save to")
        lib_lbl.setStyleSheet(label_style)
        lib_col.addWidget(lib_lbl)
        lib_col.addWidget(self.library_selector)
        options_row.addLayout(lib_col, 1)

        # Collection (right — refreshes when library changes)
        coll_col = QtWidgets.QVBoxLayout()
        coll_col.setSpacing(2)
        coll_lbl = QtWidgets.QLabel("Collection")
        coll_lbl.setStyleSheet(label_style)
        coll_col.addWidget(coll_lbl)
        self.coll_combo = QtWidgets.QComboBox()
        self.coll_combo.setFixedHeight(24)
        self._populate_collection_combo()
        coll_col.addWidget(self.coll_combo)
        options_row.addLayout(coll_col, 1)

        # Refresh collections when library changes
        self.library_selector.currentIndexChanged.connect(self._on_library_changed)

        layout.addLayout(options_row)

        # ── Thumbnail ──
        thumb_lbl = QtWidgets.QLabel("Thumbnail")
        thumb_lbl.setStyleSheet(label_style)
        layout.addWidget(thumb_lbl)

        self.ss_scroll = QtWidgets.QScrollArea()
        self.ss_scroll.setFixedHeight(80)
        self.ss_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.ss_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.ss_scroll.setWidgetResizable(True)
        self.ss_scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
        """)

        self.ss_gallery = QtWidgets.QWidget()
        self.ss_gallery_layout = QtWidgets.QHBoxLayout(self.ss_gallery)
        self.ss_gallery_layout.setContentsMargins(6, 6, 6, 6)
        self.ss_gallery_layout.setSpacing(6)
        self.ss_gallery_layout.setAlignment(QtCore.Qt.AlignLeft)

        self.ss_placeholder = QtWidgets.QLabel("No thumbnail — click buttons below to add one")
        self.ss_placeholder.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        self.ss_placeholder.setAlignment(QtCore.Qt.AlignCenter)
        self.ss_gallery_layout.addWidget(self.ss_placeholder)
        self.ss_status = QtWidgets.QLabel()  # Hidden, kept for compat
        self.ss_status.hide()

        self.ss_scroll.setWidget(self.ss_gallery)
        layout.addWidget(self.ss_scroll)

        # Screenshot buttons
        ss_btns = QtWidgets.QHBoxLayout()
        ss_btns.setSpacing(6)

        snip_btn = QtWidgets.QPushButton("+ Screenshot")
        snip_btn.setFixedHeight(22)
        snip_btn.setCursor(QtCore.Qt.PointingHandCursor)
        snip_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 2px 10px;
                color: {COLORS['text']};
                font-size: 10px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
                color: {COLORS['accent']};
            }}
        """)
        snip_btn.clicked.connect(self._take_screenshot)
        ss_btns.addWidget(snip_btn)

        clip_btn = QtWidgets.QPushButton("+ Clipboard")
        clip_btn.setFixedHeight(22)
        clip_btn.setCursor(QtCore.Qt.PointingHandCursor)
        clip_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 2px 10px;
                color: {COLORS['text']};
                font-size: 10px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
                color: {COLORS['accent']};
            }}
        """)
        clip_btn.clicked.connect(self._paste_clipboard)
        ss_btns.addWidget(clip_btn)
        ss_btns.addStretch()
        layout.addLayout(ss_btns)

        # ── Separator ──
        sep = QtWidgets.QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {COLORS['border']};")
        layout.addWidget(sep)

        # ── Action buttons ──
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(8)

        cancel = QtWidgets.QPushButton("Cancel")
        cancel.setFixedHeight(30)
        cancel.setCursor(QtCore.Qt.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 4px 16px;
                color: {COLORS['text_secondary']};
            }}
            QPushButton:hover {{
                border-color: {COLORS['text_dim']};
                color: {COLORS['text']};
            }}
        """)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)

        btns.addStretch()

        save_local = QtWidgets.QPushButton("Save Local")
        save_local.setFixedHeight(30)
        save_local.setCursor(QtCore.Qt.PointingHandCursor)
        save_local.setToolTip("Save to your local library only")
        save_local.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border_light']};
                border-radius: 3px;
                padding: 4px 16px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
            }}
        """)
        save_local.clicked.connect(self._save_local)
        btns.addWidget(save_local)

        save_publish = QtWidgets.QPushButton("Publish to Sopdrop")
        save_publish.setFixedHeight(30)
        save_publish.setCursor(QtCore.Qt.PointingHandCursor)
        save_publish.setToolTip("Save locally and publish to sopdrop.com")
        save_publish.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 3px;
                padding: 4px 18px;
                color: white;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        save_publish.clicked.connect(self._save_and_publish)
        btns.addWidget(save_publish)

        layout.addLayout(btns)

        # If updating, try to load existing thumbnail and icon
        if is_update:
            if self.existing_asset.get('thumbnail_path'):
                self._load_existing_thumbnail()
            if self.existing_asset.get('icon'):
                self._set_icon(self.existing_asset.get('icon'))

    def _set_default_icon(self):
        """Set a default icon based on the node context."""
        context = self._get_context()
        default_icons = {
            'sop': 'SOP_scatter',
            'lop': 'LOP_material',
            'obj': 'OBJ_geo',
            'vop': 'VOP_constant',
            'dop': 'DOP_rbdobject',
            'cop': 'COP2_colorwheel',
            'top': 'TOP_generic',
            'chop': 'CHOP_channel',
        }
        icon_name = default_icons.get(context, 'MISC_python')
        self._set_icon(icon_name)

    def _set_icon(self, icon_name):
        """Set the icon button to display a Houdini icon."""
        self._selected_icon = icon_name
        try:
            # Try to get the icon from Houdini
            icon = hou.qt.Icon(icon_name, 48, 48)
            if icon and not icon.isNull():
                self.icon_btn.setIcon(icon)
                self.icon_btn.setIconSize(QtCore.QSize(48, 48))
                self.icon_btn.setText("")
            else:
                self.icon_btn.setText("Icon")
                self.icon_btn.setIcon(QtGui.QIcon())
        except:
            self.icon_btn.setText("Icon")
            self.icon_btn.setIcon(QtGui.QIcon())

    def _show_icon_browser(self):
        """Show a dialog to browse Houdini icons."""
        dialog = HoudiniIconBrowser(self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted and dialog.selected_icon:
            self._set_icon(dialog.selected_icon)

    def _get_context(self):
        if not self.nodes:
            return "unknown"
        try:
            cat = self.nodes[0].parent().childTypeCategory().name().lower()
            return {'sop': 'sop', 'object': 'obj', 'vop': 'vop', 'dop': 'dop', 'cop2': 'cop', 'top': 'top', 'lop': 'lop', 'chop': 'chop'}.get(cat, cat)
        except:
            return 'unknown'

    def _populate_collection_combo(self):
        """Populate the collection combo with nested collections from the active library."""
        self.coll_combo.clear()
        self.coll_combo.addItem("None", None)
        if SOPDROP_AVAILABLE:
            tree = library.get_collection_tree()
            self._add_tree_to_combo(tree, depth=0)

    def _add_tree_to_combo(self, items, depth=0):
        """Recursively add collection tree items to the combo with indentation."""
        for coll in items:
            if coll.get('source') == 'cloud':
                continue
            indent = "\u2003" * depth  # em-space for indentation
            prefix = "\u25B8 " if depth > 0 else ""
            self.coll_combo.addItem(f"{indent}{prefix}{coll['name']}", coll['id'])
            if coll.get('children'):
                self._add_tree_to_combo(coll['children'], depth + 1)

    def _on_library_changed(self, index):
        """Refresh collections when the library selector changes."""
        lib_type = self.library_selector.currentData()
        if SOPDROP_AVAILABLE and lib_type:
            try:
                from sopdrop.config import set_active_library, get_active_library
                from sopdrop.library import close_db
                prev = get_active_library()
                if lib_type != prev:
                    close_db()
                    set_active_library(lib_type)
                self._populate_collection_combo()
                if lib_type != prev:
                    close_db()
                    set_active_library(prev)
            except Exception as e:
                print(f"[Sopdrop] Failed to load collections for {lib_type}: {e}")
                self.coll_combo.clear()
                self.coll_combo.addItem("None", None)

    def _take_screenshot(self):
        print(f"[Sopdrop] Take screenshot clicked, SNIPPING_AVAILABLE={SNIPPING_AVAILABLE}")
        if not SNIPPING_AVAILABLE:
            hou.ui.displayMessage("Screenshot tool not available. Use 'From Clipboard' instead.")
            return
        # Use setWindowOpacity instead of hide() — hiding a modal dialog on
        # Windows exits the exec_() event loop which closes the dialog entirely.
        print("[Sopdrop] Making dialog transparent for screenshot")
        self.setWindowOpacity(0)
        QtWidgets.QApplication.processEvents()
        QtCore.QTimer.singleShot(200, self._show_snipping)

    def _show_snipping(self):
        print("[Sopdrop] Showing snipping tool")
        try:
            self.snip = SnippingTool()
            self.snip.captured.connect(self._on_captured)
            self.snip.show()
            self.snip.raise_()
            self.snip.activateWindow()
            print("[Sopdrop] Snipping tool shown successfully")
        except Exception as e:
            print(f"[Sopdrop] Snipping error: {e}")
            import traceback
            traceback.print_exc()
            self.setWindowOpacity(1)

    def _on_captured(self, image):
        print(f"[Sopdrop] Screenshot captured: {image}")
        self.setWindowOpacity(1)
        self.raise_()
        self.activateWindow()
        if image and not image.isNull():
            self._add_screenshot(image)

    def _paste_clipboard(self):
        try:
            clip = QtWidgets.QApplication.clipboard()
            if clip.mimeData().hasImage():
                img = clip.image()
                if not img.isNull():
                    self._add_screenshot(img)
                else:
                    hou.ui.displayMessage("Invalid clipboard image")
            else:
                hou.ui.displayMessage("No image in clipboard")
        except Exception as e:
            hou.ui.displayMessage(f"Failed: {e}")

    def _add_screenshot(self, image):
        """Add a screenshot to the gallery."""
        self._screenshots.append(image)
        self._refresh_gallery()

    def _remove_screenshot(self, index):
        """Remove a screenshot from the gallery."""
        if 0 <= index < len(self._screenshots):
            self._screenshots.pop(index)
            self._refresh_gallery()

    def _refresh_gallery(self):
        """Refresh the screenshot gallery display."""
        # Clear existing items
        while self.ss_gallery_layout.count():
            item = self.ss_gallery_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._screenshots:
            # Show placeholder
            self.ss_placeholder = QtWidgets.QLabel("No screenshots - add below")
            self.ss_placeholder.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
            self.ss_placeholder.setAlignment(QtCore.Qt.AlignCenter)
            self.ss_gallery_layout.addWidget(self.ss_placeholder)
        else:
            # Add screenshot thumbnails
            for i, img in enumerate(self._screenshots):
                thumb = self._create_screenshot_thumb(img, i)
                self.ss_gallery_layout.addWidget(thumb)

        # Update status
        count = len(self._screenshots)
        if count == 0:
            self.ss_status.setText("First = thumbnail")
            self.ss_status.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 9px;")
        elif count == 1:
            self.ss_status.setText("1 image (thumbnail)")
            self.ss_status.setStyleSheet(f"color: {COLORS['success']}; font-size: 9px;")
        else:
            self.ss_status.setText(f"{count} images (first = thumbnail)")
            self.ss_status.setStyleSheet(f"color: {COLORS['success']}; font-size: 9px;")

    def _create_screenshot_thumb(self, image, index):
        """Create a thumbnail widget for a screenshot."""
        frame = QtWidgets.QFrame()
        frame.setFixedSize(scale(60), scale(60))
        is_first = index == 0
        border_color = COLORS['accent'] if is_first else COLORS['border']
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_dark']};
                border: 2px solid {border_color};
                border-radius: 3px;
            }}
        """)

        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)

        # Image
        thumb_label = QtWidgets.QLabel()
        thumb_label.setAlignment(QtCore.Qt.AlignCenter)
        pixmap = QtGui.QPixmap.fromImage(image)
        scaled = pixmap.scaled(54, 44, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        thumb_label.setPixmap(scaled)
        layout.addWidget(thumb_label)

        # Remove button
        remove_btn = QtWidgets.QPushButton("\u00D7")
        remove_btn.setFixedSize(16, 12)
        remove_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0,0,0,0.5);
                color: white;
                border: none;
                border-radius: 2px;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background: {COLORS['error']};
            }}
        """)
        remove_btn.clicked.connect(lambda checked=False, idx=index: self._remove_screenshot(idx))
        layout.addWidget(remove_btn, 0, QtCore.Qt.AlignCenter)

        if is_first:
            frame.setToolTip("Thumbnail (primary image)")
        else:
            frame.setToolTip(f"Additional image {index + 1}")

        return frame

    def _load_existing_thumbnail(self):
        """Load thumbnail from existing asset being updated."""
        try:
            thumb_path = library.get_library_thumbnails_dir() / self.existing_asset['thumbnail_path']
            if thumb_path.exists():
                image = QtGui.QImage(str(thumb_path))
                if not image.isNull():
                    self._add_screenshot(image)
        except Exception as e:
            print(f"[Sopdrop] Failed to load existing thumbnail: {e}")

    def _get_thumbnail_data(self):
        """Get thumbnail data as bytes, or None if no screenshot."""
        if self._screenshots:
            image = self._screenshots[0]  # First image is thumbnail
            if not image.isNull():
                ba = QtCore.QByteArray()
                buf = QtCore.QBuffer(ba)
                buf.open(QtCore.QIODevice.WriteOnly)
                image.save(buf, "PNG")
                buf.close()
                return bytes(ba)
        return None

    def _get_additional_images_data(self):
        """Get additional images (not thumbnail) as list of bytes."""
        additional = []
        for img in self._screenshots[1:]:  # Skip first (thumbnail)
            if not img.isNull():
                ba = QtCore.QByteArray()
                buf = QtCore.QBuffer(ba)
                buf.open(QtCore.QIODevice.WriteOnly)
                img.save(buf, "PNG")
                buf.close()
                additional.append(bytes(ba))
        return additional

    def _save_local(self):
        """Save to local library only."""
        name = self.name_input.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Error", "Please enter a name")
            return

        try:
            from sopdrop.config import get_active_library, set_active_library
            from sopdrop.library import close_db

            tags = self.tags_widget.get_tags()
            coll_id = self.coll_combo.currentData()
            thumb_data = self._get_thumbnail_data()

            # Check if saving to different library than current
            target_library = self.library_selector.currentData() if self._has_team_library else "personal"
            original_library = get_active_library()
            switched = False

            if target_library != original_library:
                # Temporarily switch to target library
                close_db()
                set_active_library(target_library)
                switched = True

            try:
                if self.is_hda:
                    # Save HDA
                    if self.existing_asset:
                        # TODO: HDA version update - for now, warn user
                        QtWidgets.QMessageBox.warning(
                            self, "Not Supported",
                            "Updating HDAs is not yet supported.\n"
                            "Please delete the old version and save as new."
                        )
                        return
                    else:
                        # Add houdini_version to hda_info
                        self.hda_info['houdini_version'] = hou.applicationVersionString()
                        library.save_hda(
                            name=name,
                            hda_info=self.hda_info,
                            description=self.desc_input.toPlainText().strip(),
                            tags=tags,
                            thumbnail_data=thumb_data,
                            collection_ids=[coll_id] if coll_id else None,
                            icon=self._selected_icon,
                        )
                        lib_name = "Team Library" if target_library == "team" else "Personal Library"
                        print(f"[Sopdrop] Saved HDA to {lib_name}: {name}")
                else:
                    # Save node package
                    from sopdrop.export import export_items
                    package = export_items(self.items)

                    if self.existing_asset:
                        # Update existing asset with new version
                        library.save_asset_version(
                            asset_id=self.existing_asset['id'],
                            package_data=package,
                            description=self.desc_input.toPlainText().strip(),
                            tags=tags,
                            thumbnail_data=thumb_data,
                        )
                        print(f"[Sopdrop] Updated: {name}")
                    else:
                        # Create new asset
                        library.save_asset(
                            name=name,
                            context=self._get_context(),
                            package_data=package,
                            description=self.desc_input.toPlainText().strip(),
                            tags=tags,
                            thumbnail_data=thumb_data,
                            collection_ids=[coll_id] if coll_id else None,
                            icon=self._selected_icon,
                        )
                        lib_name = "Team Library" if target_library == "team" else "Personal Library"
                        print(f"[Sopdrop] Saved to {lib_name}: {name}")
            finally:
                if switched:
                    # Switch back to original library
                    close_db()
                    set_active_library(original_library)

            self.accept()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed: {e}")
            import traceback
            traceback.print_exc()

    def _save_and_publish(self):
        """Save locally and then publish to cloud."""
        name = self.name_input.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Error", "Please enter a name")
            return

        try:
            from sopdrop.config import get_token, get_active_library, set_active_library
            from sopdrop.library import close_db

            # Check if logged in
            if not get_token():
                result = hou.ui.displayMessage(
                    "You need to log in to publish.\n\n"
                    "Would you like to save locally instead?",
                    buttons=("Save Local", "Cancel"),
                    default_choice=0,
                    close_choice=1,
                    title="Sopdrop - Login Required",
                )
                if result == 0:
                    self._save_local()
                return

            tags = self.tags_widget.get_tags()
            coll_id = self.coll_combo.currentData()
            thumb_data = self._get_thumbnail_data()

            # Check if saving to different library than current
            target_library = self.library_selector.currentData() if self._has_team_library else "personal"
            original_library = get_active_library()
            switched = False

            if target_library != original_library:
                # Temporarily switch to target library
                close_db()
                set_active_library(target_library)
                switched = True

            try:
                if self.is_hda:
                    # HDA: save locally
                    self.hda_info['houdini_version'] = hou.applicationVersionString()
                    result = library.save_hda(
                        name=name,
                        hda_info=self.hda_info,
                        description=self.desc_input.toPlainText().strip(),
                        tags=tags,
                        thumbnail_data=thumb_data,
                        collection_ids=[coll_id] if coll_id else None,
                    )
                    asset_id = result['id']
                    lib_name = "Team Library" if target_library == "team" else "Personal Library"
                    print(f"[Sopdrop] Saved HDA to {lib_name}: {name}")
                else:
                    # Node package
                    from sopdrop.export import export_items
                    package = export_items(self.items)

                    # Save locally first
                    if self.existing_asset:
                        library.save_asset_version(
                            asset_id=self.existing_asset['id'],
                            package_data=package,
                            description=self.desc_input.toPlainText().strip(),
                            tags=tags,
                            thumbnail_data=thumb_data,
                        )
                        asset_id = self.existing_asset['id']
                        print(f"[Sopdrop] Updated locally: {name}")
                    else:
                        result = library.save_asset(
                            name=name,
                            context=self._get_context(),
                            package_data=package,
                            description=self.desc_input.toPlainText().strip(),
                            tags=tags,
                            thumbnail_data=thumb_data,
                            collection_ids=[coll_id] if coll_id else None,
                        )
                        asset_id = result['id']
                        lib_name = "Team Library" if target_library == "team" else "Personal Library"
                        print(f"[Sopdrop] Saved to {lib_name}: {name}")
            finally:
                if switched:
                    # Switch back to original library
                    close_db()
                    set_active_library(original_library)

            self.accept()

            # Now publish to cloud
            try:
                if self.is_hda:
                    # Publish HDA to cloud
                    import sopdrop
                    sopdrop.publish_hda(
                        hda_info=self.hda_info,
                        name=name,
                        description=self.desc_input.toPlainText().strip(),
                        tags=tags,
                    )
                else:
                    # Publish node package to cloud
                    import sopdrop_publish
                    import importlib
                    importlib.reload(sopdrop_publish)

                    sopdrop_publish.publish_from_library(
                        package=package,
                        name=name,
                        description=self.desc_input.toPlainText().strip(),
                        tags=tags,
                        thumbnail_image=self._screenshots[0] if self._screenshots else None,
                        additional_images=self._screenshots[1:] if len(self._screenshots) > 1 else [],
                        library_asset_id=asset_id,
                    )
            except Exception as e:
                # Reset sync status since publish failed
                try:
                    library.reset_syncing_status(asset_id)
                except Exception:
                    pass
                hou.ui.displayMessage(
                    f"Asset saved locally but publish failed:\n\n{e}\n\n"
                    "You can try publishing later from the library.",
                    title="Sopdrop - Partial Success",
                    severity=hou.severityType.Warning
                )

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed: {e}")
            import traceback
            traceback.print_exc()


# ==============================================================================
# Save VEX Snippet Dialog
# ==============================================================================

class SaveVexDialog(QtWidgets.QDialog):
    """Dialog for saving a VEX snippet to the library."""

    def __init__(self, initial_code="", parent=None):
        super().__init__(parent)
        self._initial_code = initial_code
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Save VEX Snippet")
        self.setFixedWidth(scale(500))
        self.setMinimumHeight(400)
        self.setStyleSheet(STYLESHEET)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Save VEX Snippet")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {COLORS['text_bright']};")
        header.addWidget(title)
        header.addStretch()
        vex_badge = QtWidgets.QLabel("VEX")
        vex_badge.setStyleSheet(f"background-color: {get_context_color('vex')}; color: white; font-size: 10px; font-weight: bold; padding: 3px 8px; border-radius: 3px;")
        header.addWidget(vex_badge)
        layout.addLayout(header)

        # Code editor
        code_label = QtWidgets.QLabel("VEX Code")
        code_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        layout.addWidget(code_label)

        self.code_input = QtWidgets.QPlainTextEdit()
        self.code_input.setPlainText(self._initial_code)
        self.code_input.setMinimumHeight(150)
        self.code_input.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 6px;
                font-family: "Source Code Pro", "Consolas", "Courier New", monospace;
                font-size: 11px;
                color: {COLORS['text']};
            }}
            QPlainTextEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        layout.addWidget(self.code_input, 1)

        if not self._initial_code:
            hint = QtWidgets.QLabel("Tip: Select a wrangle node before opening this dialog to auto-fill the code")
            hint.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 9px;")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        # Form fields
        form = QtWidgets.QFrame()
        form.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
            }}
        """)
        form_layout = QtWidgets.QVBoxLayout(form)
        form_layout.setContentsMargins(12, 12, 12, 12)
        form_layout.setSpacing(8)

        # Name
        name_label = QtWidgets.QLabel("Name")
        name_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        form_layout.addWidget(name_label)

        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("e.g., Color by Curvature, Point Relax")
        self.name_input.setFixedHeight(28)
        self.name_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 12px;
                color: {COLORS['text']};
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        form_layout.addWidget(self.name_input)

        # Description
        desc_label = QtWidgets.QLabel("Description")
        desc_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        form_layout.addWidget(desc_label)

        self.desc_input = QtWidgets.QLineEdit()
        self.desc_input.setPlaceholderText("What does this snippet do?")
        self.desc_input.setFixedHeight(28)
        self.desc_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
                color: {COLORS['text']};
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        form_layout.addWidget(self.desc_input)

        # Tags + Collection row
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)

        # Tags
        tag_col = QtWidgets.QVBoxLayout()
        tags_label = QtWidgets.QLabel("Tags")
        tags_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        tag_col.addWidget(tags_label)
        self.tags_widget = TagInputWidget()
        tag_col.addWidget(self.tags_widget)
        row.addLayout(tag_col, 1)

        # Collection (with nested subfolders)
        coll_col = QtWidgets.QVBoxLayout()
        coll_label = QtWidgets.QLabel("Collection")
        coll_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
        coll_col.addWidget(coll_label)
        self.coll_combo = QtWidgets.QComboBox()
        self.coll_combo.setFixedHeight(24)
        self.coll_combo.setMinimumWidth(120)
        self.coll_combo.addItem("None", None)
        if SOPDROP_AVAILABLE:
            self._add_tree_to_combo(library.get_collection_tree())
        coll_col.addWidget(self.coll_combo)
        row.addLayout(coll_col)

        form_layout.addLayout(row)
        layout.addWidget(form)

        # Buttons
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(10)

        cancel = QtWidgets.QPushButton("Cancel")
        cancel.setFixedHeight(28)
        cancel.setCursor(QtCore.Qt.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 6px 16px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
            }}
        """)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addStretch()

        save_btn = QtWidgets.QPushButton("Save VEX Snippet")
        save_btn.setFixedHeight(28)
        save_btn.setCursor(QtCore.Qt.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 3px;
                padding: 6px 18px;
                color: white;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        save_btn.clicked.connect(self._save)
        btns.addWidget(save_btn)

        layout.addLayout(btns)

    def _add_tree_to_combo(self, items, depth=0):
        """Recursively add collection tree items to the combo with indentation."""
        for coll in items:
            if coll.get('source') == 'cloud':
                continue
            indent = "\u2003" * depth
            prefix = "\u25B8 " if depth > 0 else ""
            self.coll_combo.addItem(f"{indent}{prefix}{coll['name']}", coll['id'])
            if coll.get('children'):
                self._add_tree_to_combo(coll['children'], depth + 1)

    def _save(self):
        """Save the VEX snippet."""
        name = self.name_input.text().strip()
        code = self.code_input.toPlainText().strip()

        if not name:
            QtWidgets.QMessageBox.warning(self, "Error", "Please enter a name")
            return
        if not code:
            QtWidgets.QMessageBox.warning(self, "Error", "Please enter some VEX code")
            return

        try:
            tags = self.tags_widget.get_tags()
            coll_id = self.coll_combo.currentData()

            library.save_vex_snippet(
                name=name,
                code=code,
                description=self.desc_input.text().strip(),
                tags=tags,
                collection_id=coll_id,
            )
            self.accept()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to save: {e}")
            import traceback
            traceback.print_exc()


# ==============================================================================
# Settings Dialog
# ==============================================================================

class SettingsDialog(QtWidgets.QDialog):
    """Settings dialog for Sopdrop library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        self.setWindowTitle("Sopdrop Settings")
        self.setFixedWidth(scale(360))
        self.setStyleSheet(STYLESHEET)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Header
        title = QtWidgets.QLabel("Settings")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {COLORS['text_bright']};")
        layout.addWidget(title)

        # Houdini-style groupbox
        groupbox_style = f"""
            QGroupBox {{
                font-weight: 600;
                color: {COLORS['text']};
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                margin-top: 10px;
                padding: 12px 8px 8px 8px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: {COLORS['text_secondary']};
                font-size: 10px;
            }}
        """

        # Account section
        account_group = QtWidgets.QGroupBox("ACCOUNT")
        account_group.setStyleSheet(groupbox_style)
        account_layout = QtWidgets.QVBoxLayout(account_group)
        account_layout.setSpacing(8)

        # Login status
        self.login_status = QtWidgets.QLabel()
        self.login_status.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
        account_layout.addWidget(self.login_status)

        # Login/logout button
        self.login_btn = QtWidgets.QPushButton("Login")
        self.login_btn.setFixedHeight(24)
        self.login_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.login_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 3px;
                padding: 4px 12px;
                color: white;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        self.login_btn.clicked.connect(self._toggle_login)
        account_layout.addWidget(self.login_btn)

        layout.addWidget(account_group)

        # TAB Menu section
        tab_group = QtWidgets.QGroupBox("TAB MENU")
        tab_group.setStyleSheet(groupbox_style)
        tab_layout = QtWidgets.QVBoxLayout(tab_group)
        tab_layout.setSpacing(8)

        self.tab_menu_checkbox = QtWidgets.QCheckBox("Show library assets in TAB menu")
        self.tab_menu_checkbox.setToolTip(
            "When enabled, your library assets appear in Houdini's TAB menu.\n"
            "Disable this if you prefer to only use the Library panel."
        )
        tab_layout.addWidget(self.tab_menu_checkbox)

        tab_note = QtWidgets.QLabel("Changes take effect after restarting Houdini.")
        tab_note.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        tab_layout.addWidget(tab_note)

        clean_tab_btn = QtWidgets.QPushButton("Clean TAB Menu")
        clean_tab_btn.setFixedHeight(22)
        clean_tab_btn.setCursor(QtCore.Qt.PointingHandCursor)
        clean_tab_btn.setToolTip("Remove stale entries and regenerate TAB menu from current library")
        clean_tab_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 10px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        clean_tab_btn.clicked.connect(self._clean_tab_menu)
        tab_layout.addWidget(clean_tab_btn)

        layout.addWidget(tab_group)

        # Personal Library section
        personal_group = QtWidgets.QGroupBox("PERSONAL LIBRARY")
        personal_group.setStyleSheet(groupbox_style)
        personal_layout = QtWidgets.QVBoxLayout(personal_group)
        personal_layout.setSpacing(8)

        personal_path_label = QtWidgets.QLabel("Library Path:")
        personal_path_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        personal_layout.addWidget(personal_path_label)

        personal_path_row = QtWidgets.QHBoxLayout()
        self.personal_path_input = QtWidgets.QLineEdit()
        self.personal_path_input.setPlaceholderText("~/.sopdrop/library/ (default)")
        self.personal_path_input.setFixedHeight(22)
        self.personal_path_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 6px;
                color: {COLORS['text']};
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        personal_path_row.addWidget(self.personal_path_input)

        personal_browse_btn = QtWidgets.QPushButton("Browse")
        personal_browse_btn.setFixedHeight(22)
        personal_browse_btn.setCursor(QtCore.Qt.PointingHandCursor)
        personal_browse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 10px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        personal_browse_btn.clicked.connect(self._browse_personal_path)
        personal_path_row.addWidget(personal_browse_btn)

        personal_default_btn = QtWidgets.QPushButton("Default")
        personal_default_btn.setFixedHeight(22)
        personal_default_btn.setCursor(QtCore.Qt.PointingHandCursor)
        personal_default_btn.setToolTip("Reset to ~/.sopdrop/library/")
        personal_default_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 10px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        personal_default_btn.clicked.connect(self._reset_personal_path)
        personal_path_row.addWidget(personal_default_btn)
        personal_layout.addLayout(personal_path_row)

        self.personal_info = QtWidgets.QLabel()
        self.personal_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        personal_layout.addWidget(self.personal_info)

        layout.addWidget(personal_group)

        # Team Library section
        team_group = QtWidgets.QGroupBox("TEAM LIBRARY")
        team_group.setStyleSheet(groupbox_style)
        team_layout = QtWidgets.QVBoxLayout(team_group)
        team_layout.setSpacing(8)

        # Library type selector
        lib_type_row = QtWidgets.QHBoxLayout()
        lib_type_row.addWidget(QtWidgets.QLabel("Active Library:"))
        self.library_combo = QtWidgets.QComboBox()
        self.library_combo.addItem("Personal", "personal")
        self.library_combo.addItem("Team", "team")
        self.library_combo.currentIndexChanged.connect(self._on_library_changed)
        lib_type_row.addWidget(self.library_combo, 1)
        team_layout.addLayout(lib_type_row)

        # Team library path
        path_label = QtWidgets.QLabel("Team Library Path:")
        path_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px; margin-top: 4px;")
        team_layout.addWidget(path_label)
        path_row = QtWidgets.QHBoxLayout()
        self.team_path_input = QtWidgets.QLineEdit()
        self.team_path_input.setPlaceholderText("/path/to/shared/library")
        self.team_path_input.setFixedHeight(22)
        self.team_path_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 6px;
                color: {COLORS['text']};
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        path_row.addWidget(self.team_path_input)

        browse_btn = QtWidgets.QPushButton("Browse")
        browse_btn.setFixedHeight(22)
        browse_btn.setCursor(QtCore.Qt.PointingHandCursor)
        browse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 10px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        browse_btn.clicked.connect(self._browse_team_path)
        path_row.addWidget(browse_btn)
        team_layout.addLayout(path_row)

        # Team selection (fetch from server)
        team_select_label = QtWidgets.QLabel("Team:")
        team_select_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px; margin-top: 4px;")
        team_layout.addWidget(team_select_label)
        team_select_row = QtWidgets.QHBoxLayout()
        self.team_combo = QtWidgets.QComboBox()
        self.team_combo.setFixedHeight(22)
        self.team_combo.addItem("None", "")
        self.team_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 6px;
                color: {COLORS['text']};
            }}
            QComboBox:focus {{
                border-color: {COLORS['accent']};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
        """)
        self.team_combo.currentIndexChanged.connect(self._on_team_selected)
        team_select_row.addWidget(self.team_combo, 1)

        self.fetch_teams_btn = QtWidgets.QPushButton("Fetch Teams")
        self.fetch_teams_btn.setFixedHeight(22)
        self.fetch_teams_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.fetch_teams_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 10px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        self.fetch_teams_btn.setToolTip("Fetch your teams from the server")
        self.fetch_teams_btn.clicked.connect(self._fetch_teams)
        team_select_row.addWidget(self.fetch_teams_btn)
        team_layout.addLayout(team_select_row)

        # Hidden fields to store team name/slug (kept for save_settings compatibility)
        self.team_name_input = QtWidgets.QLineEdit()
        self.team_name_input.setVisible(False)
        self.team_slug_input = QtWidgets.QLineEdit()
        self.team_slug_input.setVisible(False)
        team_layout.addWidget(self.team_name_input)
        team_layout.addWidget(self.team_slug_input)

        # Team library info
        self.team_info = QtWidgets.QLabel()
        self.team_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        self.team_info.setWordWrap(True)
        team_layout.addWidget(self.team_info)

        layout.addWidget(team_group)

        # UI Scale section
        scale_group = QtWidgets.QGroupBox("UI SCALE")
        scale_group.setStyleSheet(groupbox_style)
        scale_layout = QtWidgets.QVBoxLayout(scale_group)
        scale_layout.setSpacing(8)

        scale_row = QtWidgets.QHBoxLayout()
        scale_row.setSpacing(6)

        self.scale_down_btn = QtWidgets.QPushButton("\u2212")  # minus sign
        self.scale_down_btn.setFixedSize(scale(24), scale(24))
        self.scale_down_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.scale_down_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                color: {COLORS['text']};
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        self.scale_down_btn.clicked.connect(self._scale_down)
        scale_row.addWidget(self.scale_down_btn)

        self.scale_label = QtWidgets.QLabel("100%")
        self.scale_label.setAlignment(QtCore.Qt.AlignCenter)
        self.scale_label.setFixedWidth(scale(48))
        self.scale_label.setStyleSheet(f"color: {COLORS['text']}; font-weight: 600;")
        scale_row.addWidget(self.scale_label)

        self.scale_up_btn = QtWidgets.QPushButton("+")
        self.scale_up_btn.setFixedSize(scale(24), scale(24))
        self.scale_up_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.scale_up_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                color: {COLORS['text']};
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        self.scale_up_btn.clicked.connect(self._scale_up)
        scale_row.addWidget(self.scale_up_btn)

        scale_reset_btn = QtWidgets.QPushButton("Reset")
        scale_reset_btn.setFixedHeight(scale(24))
        scale_reset_btn.setCursor(QtCore.Qt.PointingHandCursor)
        scale_reset_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 10px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        scale_reset_btn.clicked.connect(self._scale_reset)
        scale_row.addWidget(scale_reset_btn)

        scale_row.addStretch()
        scale_layout.addLayout(scale_row)

        scale_note = QtWidgets.QLabel("Changes take effect after reopening the library panel.")
        scale_note.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        scale_layout.addWidget(scale_note)

        layout.addWidget(scale_group)

        # Server section
        server_group = QtWidgets.QGroupBox("SERVER")
        server_group.setStyleSheet(groupbox_style)
        server_layout = QtWidgets.QVBoxLayout(server_group)
        server_layout.setSpacing(8)

        url_label = QtWidgets.QLabel("Server URL")
        url_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        server_layout.addWidget(url_label)
        self.server_input = QtWidgets.QLineEdit()
        self.server_input.setPlaceholderText("https://sopdrop.com")
        self.server_input.setFixedHeight(22)
        self.server_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_dark']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 2px 6px;
                color: {COLORS['text']};
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        server_layout.addWidget(self.server_input)

        layout.addWidget(server_group)

        layout.addStretch()

        # Buttons
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(10)

        cancel = QtWidgets.QPushButton("Cancel")
        cancel.setFixedHeight(24)
        cancel.setCursor(QtCore.Qt.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px;
                padding: 4px 12px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)

        btns.addStretch()

        save = QtWidgets.QPushButton("Save Settings")
        save.setFixedHeight(24)
        save.setCursor(QtCore.Qt.PointingHandCursor)
        save.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 3px;
                padding: 4px 14px;
                color: white;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        save.clicked.connect(self._save_settings)
        btns.addWidget(save)

        layout.addLayout(btns)

    def _load_settings(self):
        """Load current settings."""
        if SOPDROP_AVAILABLE:
            from sopdrop.config import get_config, get_token, get_active_library, get_team_library_path, get_team_slug, get_team_name, get_personal_library_path, get_ui_scale as _get_ui_scale

            config = get_config()
            self.server_input.setText(config.get('server_url', 'https://sopdrop.com'))

            # Check login status
            token = get_token()
            if token:
                try:
                    from sopdrop.api import SopdropClient
                    client = SopdropClient()
                    user = client._get("auth/me")
                    username = user.get('username', user.get('email', 'Unknown'))
                    self.login_status.setText(f"Logged in as: {username}")
                    self.login_status.setStyleSheet(f"color: {COLORS['success']}; font-size: 11px;")
                    self.login_btn.setText("Logout")
                except Exception:
                    self.login_status.setText("Token invalid or expired")
                    self.login_status.setStyleSheet(f"color: {COLORS['warning']}; font-size: 11px;")
                    self.login_btn.setText("Login")
            else:
                self.login_status.setText("Not logged in")
                self.login_btn.setText("Login")

            # TAB menu setting
            tab_menu_enabled = config.get('tab_menu_enabled', True)
            self.tab_menu_checkbox.setChecked(tab_menu_enabled)

            # Personal library path
            custom_path = config.get('personal_library_path')
            if custom_path:
                self.personal_path_input.setText(custom_path)
            self._update_personal_info()

            # Team library settings
            active_lib = get_active_library()
            self.library_combo.setCurrentIndex(0 if active_lib == "personal" else 1)

            team_path = get_team_library_path()
            if team_path:
                self.team_path_input.setText(str(team_path))

            # Team name and slug - restore into hidden fields and combo
            team_name = get_team_name()
            team_slug = get_team_slug()
            if team_name:
                self.team_name_input.setText(team_name)
            if team_slug:
                self.team_slug_input.setText(team_slug)
                # Add current team to combo if not already there
                idx = self.team_combo.findData(team_slug)
                if idx < 0:
                    display = team_name if team_name else team_slug
                    self.team_combo.addItem(display, team_slug)
                    idx = self.team_combo.findData(team_slug)
                if idx >= 0:
                    self.team_combo.setCurrentIndex(idx)

            self._update_team_info()

            # UI scale
            self._current_scale = _get_ui_scale()
            self.scale_label.setText(f"{int(self._current_scale * 100)}%")
        else:
            self.login_status.setText("Sopdrop not installed")
            self.login_btn.setEnabled(False)
            self.tab_menu_checkbox.setChecked(True)
            self.personal_path_input.setEnabled(False)
            self.library_combo.setEnabled(False)
            self.team_path_input.setEnabled(False)
            self.team_combo.setEnabled(False)
            self.fetch_teams_btn.setEnabled(False)
            self._current_scale = 1.0

    def _toggle_login(self):
        """Login or logout."""
        if not SOPDROP_AVAILABLE:
            return

        from sopdrop.config import get_token, clear_token

        if get_token():
            # Logout
            clear_token()
            self.login_status.setText("Not logged in")
            self.login_status.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
            self.login_btn.setText("Login")
        else:
            # Login - use inline login flow
            self._do_login()

    def _do_login(self):
        """Perform login flow."""
        if not SOPDROP_AVAILABLE:
            return

        import webbrowser
        from sopdrop.config import get_config, save_token, clear_token

        config = get_config()
        auth_url = f"{config.get('server_url', 'https://sopdrop.com')}/auth/cli"

        webbrowser.open(auth_url)

        token, ok = QtWidgets.QInputDialog.getText(
            self, "Enter Token",
            "After logging in on the website, paste your API token here:",
            QtWidgets.QLineEdit.Normal
        )

        if not ok or not token.strip():
            return

        token = token.strip()
        save_token(token)

        try:
            from sopdrop.api import SopdropClient
            client = SopdropClient()
            user = client._get("auth/me")
            username = user.get("username", user.get("email"))
            QtWidgets.QMessageBox.information(
                self, "Login Success",
                f"Welcome, {username}!\n\nYou can now publish assets to Sopdrop."
            )
        except Exception as e:
            clear_token()
            QtWidgets.QMessageBox.critical(
                self, "Login Failed",
                f"Could not verify token:\n{e}"
            )

        self._load_settings()  # Refresh status

    def _scale_down(self):
        """Decrease UI scale by 10%."""
        current = self._current_scale
        new_val = max(0.8, round(current - 0.1, 2))
        self._current_scale = new_val
        self.scale_label.setText(f"{int(new_val * 100)}%")

    def _scale_up(self):
        """Increase UI scale by 10%."""
        current = self._current_scale
        new_val = min(1.5, round(current + 0.1, 2))
        self._current_scale = new_val
        self.scale_label.setText(f"{int(new_val * 100)}%")

    def _scale_reset(self):
        """Reset UI scale to 100%."""
        self._current_scale = 1.0
        self.scale_label.setText("100%")

    def _save_settings(self):
        """Save settings and close."""
        if SOPDROP_AVAILABLE:
            from sopdrop.config import get_config, save_config, set_team_library_path, set_active_library, set_team_slug, set_team_name, set_personal_library_path, set_ui_scale

            # Save personal library path
            personal_path = self.personal_path_input.text().strip()
            if personal_path:
                try:
                    set_personal_library_path(personal_path)
                except Exception as e:
                    hou.ui.displayMessage(f"Invalid personal library path: {e}")
                    return
            else:
                set_personal_library_path(None)

            # Save team library path first
            team_path = self.team_path_input.text().strip()
            if team_path:
                try:
                    set_team_library_path(team_path)
                except Exception as e:
                    hou.ui.displayMessage(f"Invalid team library path: {e}")
                    return
            else:
                set_team_library_path(None)

            # Save team name
            team_name = self.team_name_input.text().strip()
            set_team_name(team_name if team_name else None)

            # Save team slug (normalize to lowercase to match server)
            team_slug = self.team_slug_input.text().strip().lower()
            set_team_slug(team_slug if team_slug else None)

            # Save active library
            active_lib = self.library_combo.currentData()
            if active_lib == "team" and not team_path:
                hou.ui.displayMessage("Please set a team library path first.")
                return
            set_active_library(active_lib)

            # Now get fresh config and update other settings
            config = get_config()
            config['server_url'] = self.server_input.text().strip() or 'https://sopdrop.com'
            config['tab_menu_enabled'] = self.tab_menu_checkbox.isChecked()
            save_config(config)

            # Save UI scale
            set_ui_scale(self._current_scale)

        self.accept()

    def _browse_personal_path(self):
        """Browse for personal library folder."""
        current = self.personal_path_input.text() or ""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Personal Library Folder",
            current,
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if path:
            path = path.rstrip("/\\")
            self.personal_path_input.setText(path)
            self._update_personal_info()

    def _reset_personal_path(self):
        """Reset personal library path to default."""
        self.personal_path_input.clear()
        self._update_personal_info()

    def _update_personal_info(self):
        """Update personal library info label."""
        if not SOPDROP_AVAILABLE:
            return

        from pathlib import Path
        from sopdrop.config import get_config_dir

        custom_path = self.personal_path_input.text().strip()
        if custom_path:
            lib_path = Path(custom_path)
        else:
            lib_path = get_config_dir() / "library"

        if not lib_path.exists():
            self.personal_info.setText(f"Path: {lib_path}\nFolder will be created on first save.")
            self.personal_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
            return

        db_path = lib_path / "library.db"
        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                count = conn.execute("SELECT COUNT(*) FROM library_assets").fetchone()[0]
                conn.close()
                self.personal_info.setText(f"Path: {lib_path}\n{count} asset(s)")
                self.personal_info.setStyleSheet(f"color: {COLORS['success']}; font-size: 10px;")
            except Exception:
                self.personal_info.setText(f"Path: {lib_path}")
                self.personal_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        else:
            self.personal_info.setText(f"Path: {lib_path}\nEmpty library (no database yet)")
            self.personal_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")

    def _browse_team_path(self):
        """Browse for team library folder."""
        current = self.team_path_input.text() or ""
        # Use Qt file dialog for more reliable directory selection
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Team Library Folder",
            current,
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if path:
            # Remove trailing slash if present
            path = path.rstrip("/\\")
            self.team_path_input.setText(path)
            self._update_team_info()

            # Try to auto-detect team from existing library database
            self._try_detect_team(path)

    def _on_library_changed(self, index):
        """Handle library type change."""
        lib_type = self.library_combo.currentData()
        if lib_type == "team" and not self.team_path_input.text().strip():
            self.team_info.setText("Set a team library path to enable team mode.")
            self.team_info.setStyleSheet(f"color: {COLORS['warning']}; font-size: 10px;")
        else:
            self._update_team_info()

    def _update_team_info(self):
        """Update team library info label."""
        if not SOPDROP_AVAILABLE:
            return

        team_path = self.team_path_input.text().strip()
        if not team_path:
            self.team_info.setText(
                "Set a shared folder path to enable team library.\n"
                "All team members should point to the same folder."
            )
            self.team_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
            return

        import os
        from pathlib import Path

        path = Path(team_path)
        lib_path = path / "library"

        if not path.exists():
            self.team_info.setText(f"Folder does not exist. It will be created on save.")
            self.team_info.setStyleSheet(f"color: {COLORS['warning']}; font-size: 10px;")
        elif lib_path.exists():
            # Count assets in team library
            db_path = lib_path / "library.db"
            if db_path.exists():
                try:
                    import sqlite3
                    conn = sqlite3.connect(str(db_path))
                    count = conn.execute("SELECT COUNT(*) FROM library_assets").fetchone()[0]
                    conn.close()
                    self.team_info.setText(f"Team library found: {count} assets")
                    self.team_info.setStyleSheet(f"color: {COLORS['success']}; font-size: 10px;")
                except Exception:
                    self.team_info.setText("Team library folder found (new)")
                    self.team_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
            else:
                self.team_info.setText("Team library folder found (empty)")
                self.team_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        else:
            self.team_info.setText("Library will be created in this folder.")
            self.team_info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")

    def _fetch_teams(self):
        """Fetch teams from the server and populate the dropdown."""
        if not SOPDROP_AVAILABLE:
            return

        from sopdrop.config import get_token

        if not get_token():
            QtWidgets.QMessageBox.warning(self, "Login Required", "Please login first to fetch your teams.")
            return

        try:
            from sopdrop.library import get_user_teams
            teams = get_user_teams()

            if not teams:
                QtWidgets.QMessageBox.information(
                    self, "No Teams",
                    "You are not a member of any teams.\nCreate or join a team on the website first."
                )
                return

            # Remember current selection
            current_slug = self.team_slug_input.text()

            # Clear and repopulate combo
            self.team_combo.blockSignals(True)
            self.team_combo.clear()
            self.team_combo.addItem("None", "")

            for team in teams:
                slug = team.get('slug', '')
                name = team.get('name', slug)
                role = team.get('role', 'member')
                self.team_combo.addItem(f"{name} ({role})", slug)

            # Restore selection if it exists
            if current_slug:
                idx = self.team_combo.findData(current_slug)
                if idx >= 0:
                    self.team_combo.setCurrentIndex(idx)

            self.team_combo.blockSignals(False)

            self.team_info.setText(f"Found {len(teams)} team(s)")
            self.team_info.setStyleSheet(f"color: {COLORS['success']}; font-size: 10px;")

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to fetch teams: {e}")

    def _on_team_selected(self, index):
        """Handle team selection from dropdown."""
        slug = self.team_combo.currentData()
        name = self.team_combo.currentText()
        # Strip role suffix like " (admin)" from display text
        if ' (' in name:
            name = name.rsplit(' (', 1)[0]

        self.team_slug_input.setText(slug or "")
        self.team_name_input.setText(name if slug else "")

    def _try_detect_team(self, path):
        """Try to detect team identity from an existing library database."""
        if not SOPDROP_AVAILABLE:
            return

        try:
            from sopdrop.library import detect_team_from_library
            result = detect_team_from_library(path)
            if result:
                team_name = result.get('team_name', '')
                team_slug = result.get('team_slug', '')

                if team_slug:
                    self.team_slug_input.setText(team_slug)
                if team_name:
                    self.team_name_input.setText(team_name)

                # Add to combo if not already there
                if team_slug:
                    idx = self.team_combo.findData(team_slug)
                    if idx < 0:
                        display = team_name if team_name else team_slug
                        self.team_combo.blockSignals(True)
                        self.team_combo.addItem(display, team_slug)
                        self.team_combo.blockSignals(False)
                        idx = self.team_combo.findData(team_slug)
                    if idx >= 0:
                        self.team_combo.blockSignals(True)
                        self.team_combo.setCurrentIndex(idx)
                        self.team_combo.blockSignals(False)

                self.team_info.setText(f"Detected team: {team_name or team_slug}")
                self.team_info.setStyleSheet(f"color: {COLORS['success']}; font-size: 10px;")
        except Exception as e:
            print(f"[Sopdrop] Team detection failed: {e}")

    def _clean_tab_menu(self):
        """Clean and regenerate the TAB menu."""
        try:
            from sopdrop.menu import cleanup_menu
            result = cleanup_menu()
            if result:
                hou.ui.displayMessage(
                    "TAB menu cleaned and regenerated successfully.\n"
                    "Stale entries have been removed.",
                    title="Sopdrop"
                )
            else:
                hou.ui.displayMessage(
                    "TAB menu cleanup completed with warnings.\n"
                    "Check the console for details.",
                    title="Sopdrop"
                )
        except Exception as e:
            hou.ui.displayMessage(f"Failed to clean TAB menu: {e}", title="Sopdrop")


# ==============================================================================
# Edit Asset Dialog
# ==============================================================================

class AssetDetailDialog(QtWidgets.QDialog):
    """Read-only detail view of an asset — full name, description, thumbnail, tags, metadata."""

    tag_clicked = QtCore.Signal(str)

    def __init__(self, asset, parent=None):
        super().__init__(parent)
        self.asset = asset
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(self.asset.get('name', 'Asset Details'))
        self.setMinimumSize(scale(420), scale(360))
        self.setStyleSheet(STYLESHEET)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- Thumbnail area (top, large) --
        self.thumb_frame = QtWidgets.QFrame()
        self.thumb_frame.setFixedHeight(scale(200))
        self.thumb_frame.setStyleSheet(f"background-color: {COLORS['bg_base']};")
        thumb_layout = QtWidgets.QVBoxLayout(self.thumb_frame)
        thumb_layout.setContentsMargins(0, 0, 0, 0)
        thumb_layout.setAlignment(QtCore.Qt.AlignCenter)

        self.thumb_label = QtWidgets.QLabel()
        self.thumb_label.setAlignment(QtCore.Qt.AlignCenter)
        thumb_layout.addWidget(self.thumb_label)
        layout.addWidget(self.thumb_frame)

        self._load_thumbnail()

        # -- Info area --
        info_widget = QtWidgets.QWidget()
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setSpacing(8)

        # Name + context badge row
        name_row = QtWidgets.QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QtWidgets.QLabel(self.asset.get('name', 'Untitled'))
        name_label.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {COLORS['text_bright']};")
        name_label.setWordWrap(True)
        name_row.addWidget(name_label, 1)

        context = self.asset.get('context', 'sop')
        ctx_badge = QtWidgets.QLabel(context.upper())
        ctx_badge.setStyleSheet(f"""
            background-color: {get_context_color(context)};
            color: white; font-size: 10px; font-weight: bold;
            padding: 3px 8px; border-radius: 3px;
        """)
        name_row.addWidget(ctx_badge, 0, QtCore.Qt.AlignTop)

        if self.asset.get('asset_type') == 'hda':
            hda_badge = QtWidgets.QLabel("HDA")
            hda_badge.setStyleSheet(f"""
                background-color: rgba(224, 145, 192, 0.9);
                color: white; font-size: 10px; font-weight: bold;
                padding: 3px 8px; border-radius: 3px;
            """)
            name_row.addWidget(hda_badge, 0, QtCore.Qt.AlignTop)

        info_layout.addLayout(name_row)

        # Description
        desc = self.asset.get('description', '')
        if desc:
            desc_label = QtWidgets.QLabel(desc)
            desc_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
            desc_label.setWordWrap(True)
            info_layout.addWidget(desc_label)

        # Tags
        tags = self.asset.get('tags', [])
        if isinstance(tags, str):
            import json as _json
            try:
                tags = _json.loads(tags)
            except Exception:
                tags = []
        if tags:
            tags_row = QtWidgets.QHBoxLayout()
            tags_row.setSpacing(4)
            for t in tags:
                tag_btn = QtWidgets.QPushButton(t)
                tag_btn.setCursor(QtCore.Qt.PointingHandCursor)
                tag_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: rgba(255,255,255,0.1);
                        color: {COLORS['text_secondary']};
                        font-size: 10px; padding: 2px 8px;
                        border-radius: 3px; border: none;
                    }}
                    QPushButton:hover {{
                        background-color: rgba(255,255,255,0.25);
                        color: {COLORS['text']};
                    }}
                """)
                tag_btn.clicked.connect(
                    lambda checked=False, tag=t: self._on_tag_clicked(tag)
                )
                tags_row.addWidget(tag_btn)
            tags_row.addStretch()
            info_layout.addLayout(tags_row)

        # Separator
        sep = QtWidgets.QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {COLORS['border']};")
        info_layout.addWidget(sep)

        # Metadata grid
        meta_grid = QtWidgets.QGridLayout()
        meta_grid.setSpacing(4)
        meta_grid.setColumnStretch(1, 1)
        row = 0

        def add_meta(label, value):
            nonlocal row
            if not value:
                return
            lbl = QtWidgets.QLabel(label)
            lbl.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
            val = QtWidgets.QLabel(str(value))
            val.setStyleSheet(f"color: {COLORS['text']}; font-size: 10px;")
            val.setWordWrap(True)
            meta_grid.addWidget(lbl, row, 0, QtCore.Qt.AlignTop)
            meta_grid.addWidget(val, row, 1)
            row += 1

        add_meta("Type", self.asset.get('asset_type', 'node').upper())
        add_meta("Nodes", self.asset.get('node_count'))

        node_types = self.asset.get('node_types', [])
        if isinstance(node_types, str):
            import json as _json
            try:
                node_types = _json.loads(node_types)
            except Exception:
                node_types = []
        if node_types:
            add_meta("Node Types", ', '.join(node_types[:8]))

        add_meta("Houdini", self.asset.get('houdini_version'))

        file_size = self.asset.get('file_size', 0)
        if file_size:
            if file_size > 1024 * 1024:
                add_meta("Size", f"{file_size / (1024*1024):.1f} MB")
            elif file_size > 1024:
                add_meta("Size", f"{file_size / 1024:.0f} KB")
            else:
                add_meta("Size", f"{file_size} bytes")

        add_meta("Used", f"{self.asset.get('use_count', 0)} times")

        created = self.asset.get('created_at', '')
        if created:
            add_meta("Created", created[:10])

        # Collections
        if SOPDROP_AVAILABLE:
            colls = library.get_asset_collections(self.asset['id'])
            if colls:
                add_meta("Collections", ', '.join(c['name'] for c in colls))

        if self.asset.get('hda_type_name'):
            add_meta("HDA Type", self.asset['hda_type_name'])

        license_type = self.asset.get('license_type')
        if license_type:
            add_meta("License", license_type.title())

        info_layout.addLayout(meta_grid)

        # -- Version History --
        if SOPDROP_AVAILABLE:
            versions = library.get_asset_versions(self.asset['id'])
            if versions:
                ver_sep = QtWidgets.QFrame()
                ver_sep.setFixedHeight(1)
                ver_sep.setStyleSheet(f"background-color: {COLORS['border']};")
                info_layout.addWidget(ver_sep)

                ver_header = QtWidgets.QLabel(f"Version History ({len(versions)})")
                ver_header.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;")
                info_layout.addWidget(ver_header)

                for v in versions:
                    ver_row = QtWidgets.QFrame()
                    ver_row.setStyleSheet(f"""
                        QFrame {{
                            background-color: {COLORS['bg_medium']};
                            border: 1px solid {COLORS['border']};
                            border-radius: 3px;
                        }}
                    """)
                    vr_layout = QtWidgets.QHBoxLayout(ver_row)
                    vr_layout.setContentsMargins(8, 4, 8, 4)
                    vr_layout.setSpacing(8)

                    # Version label
                    ver_label = QtWidgets.QLabel(f"v{v.get('version', '?')}")
                    ver_label.setStyleSheet(f"color: {COLORS['accent']}; font-size: 11px; font-weight: 700; border: none; background: transparent;")
                    vr_layout.addWidget(ver_label)

                    # Changelog or metadata
                    changelog = v.get('changelog', '')
                    node_ct = v.get('node_count', 0)
                    detail_parts = []
                    if changelog:
                        detail_parts.append(changelog)
                    elif node_ct:
                        detail_parts.append(f"{node_ct} nodes")
                    created = v.get('created_at', '')
                    if created:
                        detail_parts.append(created[:10])
                    detail_text = ' \u2022 '.join(detail_parts) if detail_parts else ''
                    if detail_text:
                        detail_label = QtWidgets.QLabel(detail_text)
                        detail_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px; border: none; background: transparent;")
                        vr_layout.addWidget(detail_label, 1)
                    else:
                        vr_layout.addStretch()

                    # Action buttons
                    ver_btn_style = f"""
                        QPushButton {{
                            background-color: {COLORS['bg_light']};
                            border: 1px solid {COLORS['border']};
                            border-radius: 2px; padding: 2px 8px;
                            color: {COLORS['text']}; font-size: 9px;
                        }}
                        QPushButton:hover {{
                            border-color: {COLORS['accent']};
                            color: {COLORS['accent']};
                        }}
                    """

                    paste_btn = QtWidgets.QPushButton("Paste")
                    paste_btn.setFixedHeight(20)
                    paste_btn.setCursor(QtCore.Qt.PointingHandCursor)
                    paste_btn.setToolTip("Paste this version into the network")
                    paste_btn.setStyleSheet(ver_btn_style)
                    paste_btn.clicked.connect(
                        lambda checked=False, vid=v['id']: self._paste_version(vid)
                    )
                    vr_layout.addWidget(paste_btn)

                    revert_btn = QtWidgets.QPushButton("Revert")
                    revert_btn.setFixedHeight(20)
                    revert_btn.setCursor(QtCore.Qt.PointingHandCursor)
                    revert_btn.setToolTip("Revert asset to this version")
                    revert_btn.setStyleSheet(ver_btn_style)
                    revert_btn.clicked.connect(
                        lambda checked=False, vid=v['id'], vv=v.get('version', '?'): self._revert_version(vid, vv)
                    )
                    vr_layout.addWidget(revert_btn)

                    info_layout.addWidget(ver_row)

        info_layout.addStretch()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(info_widget)
        scroll.setStyleSheet("border: none;")
        layout.addWidget(scroll, 1)

        # Bottom buttons
        btn_bar = QtWidgets.QHBoxLayout()
        btn_bar.setContentsMargins(16, 8, 16, 12)
        btn_bar.setSpacing(8)

        edit_btn = QtWidgets.QPushButton("Edit Details")
        edit_btn.setFixedHeight(26)
        edit_btn.setCursor(QtCore.Qt.PointingHandCursor)
        edit_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px; padding: 4px 14px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
            }}
        """)
        edit_btn.clicked.connect(self._open_edit)
        btn_bar.addWidget(edit_btn)

        btn_bar.addStretch()

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setFixedHeight(26)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none; border-radius: 3px;
                padding: 4px 14px; color: white; font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        close_btn.clicked.connect(self.accept)
        btn_bar.addWidget(close_btn)

        layout.addLayout(btn_bar)

    def _load_thumbnail(self):
        thumb_path_str = self.asset.get('thumbnail_path')
        if thumb_path_str and SOPDROP_AVAILABLE:
            try:
                thumb_dir = library.get_library_thumbnails_dir()
                thumb_path = thumb_dir / thumb_path_str
                if thumb_path.exists():
                    pixmap = QtGui.QPixmap(str(thumb_path))
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(
                            400, 190, QtCore.Qt.KeepAspectRatio,
                            QtCore.Qt.SmoothTransformation
                        )
                        self.thumb_label.setPixmap(scaled)
                        return
            except Exception:
                pass
        # Fallback placeholder
        context = self.asset.get('context', 'sop')
        self.thumb_label.setText(context.upper())
        self.thumb_label.setStyleSheet(f"""
            color: {get_context_color(context)};
            font-size: 28px; font-weight: bold; opacity: 0.3;
        """)

    def _on_tag_clicked(self, tag):
        self.tag_clicked.emit(tag)

    def _paste_version(self, version_id):
        """Paste a specific version into the current Houdini network."""
        if not SOPDROP_AVAILABLE:
            return
        try:
            package = library.load_version_package(version_id)
            if not package:
                try:
                    hou.ui.displayMessage("Could not load version data")
                except Exception:
                    pass
                return

            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if not pane:
                hou.ui.displayMessage("No network editor found")
                return

            target = pane.pwd()
            cursor_pos = pane.cursorPosition()

            from sopdrop.importer import import_items
            import_items(package, target, position=cursor_pos)
            self.accept()
        except Exception as e:
            try:
                hou.ui.displayMessage(f"Failed to paste version: {e}")
            except Exception:
                pass

    def _revert_version(self, version_id, version_label):
        """Revert asset to a previous version."""
        if not SOPDROP_AVAILABLE:
            return
        try:
            result = hou.ui.displayMessage(
                f"Revert this asset to v{version_label}?\n\n"
                "The current version will be saved in version history.",
                buttons=("Revert", "Cancel"),
                default_choice=1,
            )
            if result == 1:
                return

            updated = library.revert_to_version(self.asset['id'], version_id)
            if updated:
                self.asset = updated
                # Refresh the parent panel
                parent = self.parent()
                while parent and not isinstance(parent, LibraryPanel):
                    parent = parent.parent()
                if parent:
                    parent._refresh_assets()
                    parent.show_toast(f"Reverted to v{version_label}", 'success', 3000)
                self.accept()
            else:
                hou.ui.displayMessage("Failed to revert — version file may be missing")
        except Exception as e:
            try:
                hou.ui.displayMessage(f"Failed to revert: {e}")
            except Exception:
                pass

    def _open_edit(self):
        self.accept()
        # Find parent panel and open edit dialog
        parent = self.parent()
        while parent and not isinstance(parent, LibraryPanel):
            parent = parent.parent()
        if parent:
            parent._edit_asset(self.asset['id'])


class EditAssetDialog(QtWidgets.QDialog):
    """Dialog for editing asset metadata including thumbnail."""

    def __init__(self, asset, parent=None):
        super().__init__(parent)
        self.asset = asset
        self._new_thumbnail = None  # bytes if changed
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Edit Asset")
        self.setMinimumWidth(scale(400))
        self.setStyleSheet(STYLESHEET)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("Edit Asset")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {COLORS['text_bright']};")
        layout.addWidget(title)

        # -- Thumbnail section --
        thumb_section = QtWidgets.QFrame()
        thumb_section.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_base']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
            }}
        """)
        thumb_layout = QtWidgets.QHBoxLayout(thumb_section)
        thumb_layout.setContentsMargins(10, 10, 10, 10)
        thumb_layout.setSpacing(10)

        # Current thumbnail preview
        self.thumb_preview = QtWidgets.QLabel()
        self.thumb_preview.setFixedSize(scale(100), scale(75))
        self.thumb_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.thumb_preview.setStyleSheet(f"""
            background-color: {COLORS['bg_dark']};
            border: 1px solid {COLORS['border']};
            border-radius: 3px;
        """)
        self._load_current_thumbnail()
        thumb_layout.addWidget(self.thumb_preview)

        # Thumbnail actions
        thumb_actions = QtWidgets.QVBoxLayout()
        thumb_actions.setSpacing(4)

        thumb_label = QtWidgets.QLabel("Thumbnail")
        thumb_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px; font-weight: 600; border: none; background: transparent;")
        thumb_actions.addWidget(thumb_label)

        btn_style = f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px; padding: 3px 10px;
                color: {COLORS['text']}; font-size: 10px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
            }}
        """

        screenshot_btn = QtWidgets.QPushButton("Screenshot")
        screenshot_btn.setFixedHeight(22)
        screenshot_btn.setCursor(QtCore.Qt.PointingHandCursor)
        screenshot_btn.setStyleSheet(btn_style)
        screenshot_btn.clicked.connect(self._take_screenshot)
        thumb_actions.addWidget(screenshot_btn)

        paste_btn = QtWidgets.QPushButton("From Clipboard")
        paste_btn.setFixedHeight(22)
        paste_btn.setCursor(QtCore.Qt.PointingHandCursor)
        paste_btn.setStyleSheet(btn_style)
        paste_btn.clicked.connect(self._paste_clipboard)
        thumb_actions.addWidget(paste_btn)

        browse_btn = QtWidgets.QPushButton("Browse File...")
        browse_btn.setFixedHeight(22)
        browse_btn.setCursor(QtCore.Qt.PointingHandCursor)
        browse_btn.setStyleSheet(btn_style)
        browse_btn.clicked.connect(self._browse_image)
        thumb_actions.addWidget(browse_btn)

        thumb_actions.addStretch()
        thumb_layout.addLayout(thumb_actions, 1)
        layout.addWidget(thumb_section)

        # -- Name --
        name_label = QtWidgets.QLabel("Name")
        name_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
        layout.addWidget(name_label)
        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setText(self.asset.get('name', ''))
        self.name_input.setFixedHeight(24)
        layout.addWidget(self.name_input)

        # -- Description --
        desc_label = QtWidgets.QLabel("Description")
        desc_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
        layout.addWidget(desc_label)
        self.desc_input = QtWidgets.QTextEdit()
        self.desc_input.setMaximumHeight(60)
        self.desc_input.setText(self.asset.get('description', ''))
        layout.addWidget(self.desc_input)

        # -- Tags --
        tags_label = QtWidgets.QLabel("Tags")
        tags_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
        layout.addWidget(tags_label)
        self.tags_widget = TagInputWidget()
        self.tags_widget.set_tags(self.asset.get('tags', []))
        layout.addWidget(self.tags_widget)

        layout.addStretch()

        # -- Buttons --
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(10)

        cancel = QtWidgets.QPushButton("Cancel")
        cancel.setFixedHeight(26)
        cancel.setCursor(QtCore.Qt.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                border: 1px solid {COLORS['border']};
                border-radius: 3px; padding: 4px 12px;
                color: {COLORS['text']};
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_lighter']};
                border-color: {COLORS['border_light']};
            }}
        """)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)

        btns.addStretch()

        save = QtWidgets.QPushButton("Save Changes")
        save.setFixedHeight(26)
        save.setCursor(QtCore.Qt.PointingHandCursor)
        save.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none; border-radius: 3px;
                padding: 4px 14px; color: white; font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        save.clicked.connect(self._save)
        btns.addWidget(save)

        layout.addLayout(btns)

    def _load_current_thumbnail(self):
        thumb_path_str = self.asset.get('thumbnail_path')
        if thumb_path_str and SOPDROP_AVAILABLE:
            try:
                thumb_dir = library.get_library_thumbnails_dir()
                thumb_path = thumb_dir / thumb_path_str
                if thumb_path.exists():
                    pixmap = QtGui.QPixmap(str(thumb_path))
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(
                            96, 71, QtCore.Qt.KeepAspectRatio,
                            QtCore.Qt.SmoothTransformation
                        )
                        self.thumb_preview.setPixmap(scaled)
                        return
            except Exception:
                pass
        self.thumb_preview.setText("No thumbnail")
        self.thumb_preview.setStyleSheet(
            self.thumb_preview.styleSheet() +
            f" color: {COLORS['text_dim']}; font-size: 9px;"
        )

    def _set_preview_from_image(self, image):
        """Update preview from a QImage and store the data."""
        if image.isNull():
            return
        ba = QtCore.QByteArray()
        buf = QtCore.QBuffer(ba)
        buf.open(QtCore.QIODevice.WriteOnly)
        image.save(buf, "PNG")
        buf.close()
        self._new_thumbnail = bytes(ba)

        pixmap = QtGui.QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            96, 71, QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        )
        self.thumb_preview.setPixmap(scaled)

    def _take_screenshot(self):
        if not SNIPPING_AVAILABLE:
            try:
                hou.ui.displayMessage("Screenshot tool not available. Use 'From Clipboard' instead.")
            except Exception:
                pass
            return
        self.hide()
        QtWidgets.QApplication.processEvents()
        QtCore.QTimer.singleShot(200, self._show_snipping)

    def _show_snipping(self):
        try:
            self.snip = SnippingTool()
            self.snip.captured.connect(self._on_captured)
            self.snip.show()
            self.snip.raise_()
            self.snip.activateWindow()
        except Exception:
            self.show()

    def _on_captured(self, image):
        self.show()
        self.raise_()
        self.activateWindow()
        if image and not image.isNull():
            self._set_preview_from_image(image)

    def _paste_clipboard(self):
        try:
            clip = QtWidgets.QApplication.clipboard()
            if clip.mimeData().hasImage():
                img = clip.image()
                if not img.isNull():
                    self._set_preview_from_image(img)
                    return
        except Exception:
            pass
        try:
            hou.ui.displayMessage("No image in clipboard")
        except Exception:
            pass

    def _browse_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff);;All Files (*)"
        )
        if path:
            img = QtGui.QImage(path)
            if not img.isNull():
                self._set_preview_from_image(img)

    def _save(self):
        name = self.name_input.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Error", "Please enter a name")
            return
        try:
            library.update_asset(
                self.asset['id'],
                name=name,
                description=self.desc_input.toPlainText().strip(),
                tags=self.tags_widget.get_tags()
            )
            if self._new_thumbnail:
                library.update_asset_thumbnail(self.asset['id'], self._new_thumbnail)
            self.accept()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed: {e}")


# ==============================================================================
# Panel Creation
# ==============================================================================

def create_panel():
    """Create the library panel widget for Houdini."""
    if not SOPDROP_AVAILABLE:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setAlignment(QtCore.Qt.AlignCenter)
        label = QtWidgets.QLabel(f"Sopdrop Library Not Available\n\n{SOPDROP_ERROR or 'Unknown error'}")
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setStyleSheet(f"color: {COLORS['text_dim']};")
        layout.addWidget(label)
        return widget
    return LibraryPanel()
