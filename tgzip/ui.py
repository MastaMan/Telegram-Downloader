import json
import time
from pathlib import Path
import qrcode
from PySide6.QtCore import Qt, QTimer, QUrl, QRect, QPointF
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QImage, QPainter, QPixmap, QPalette, QPen, QPolygonF, QIntValidator
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QScrollArea, QStyledItemDelegate, QStyleOptionViewItem, QStyleOptionComboBox, QStyle,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from .security import validate_keys
from .storage import parallel_downloads, speed_limit, SPEED_LIMITS, QUEUE_PAGE_SIZE
from . import __version__

STYLE = '''
QWidget { background: #0d1422; color: #eaf0fa; font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }
QMainWindow { background: #0d1422; }
QFrame#card { background: #151f30; border: 1px solid #253247; border-radius: 8px; }
QFrame#card QLabel, QFrame#card QWidget#transparent { background: transparent; }
QLabel#muted { color: #99abc3; }
QLabel#title { font-size: 18px; font-weight: 700; }
QLabel#section { font-size: 14px; font-weight: 600; }
QLabel#metric { font-size: 14px; font-weight: 600; }
QLabel#badge { color: #69dcca; background: #153e3e; border-radius: 6px; padding: 4px 8px; }
QLabel#logo { background: #4187ff; color: white; border-radius: 7px; font-size: 15px; font-weight: 800; }
QPushButton { background: #233149; border: 1px solid #34445f; border-radius: 5px; padding: 4px 8px; min-height: 20px; font-weight: 600; }
QPushButton:hover { background: #304460; border-color: #6a91c7; }
QPushButton:pressed { background: #17243a; }
QPushButton:disabled { color: #6d7b90; background: #172134; border-color: #263249; }
QPushButton#primary { background: #4689ff; color: white; border-color: #4689ff; }
QPushButton#primary:hover { background: #639bff; }
QPushButton#primary:disabled { background: #273d60; color: #7d91af; border-color: #273d60; }
QLineEdit, QComboBox { background: #0f1828; border: 1px solid #33445f; border-radius: 5px; padding: 4px 6px; min-height: 20px; selection-background-color: #3868ae; }
QLineEdit:focus, QComboBox:focus { border-color: #5996ff; }
QComboBox { padding-right: 28px; }
QComboBox QAbstractItemView { background: #162236; color: #eaf0fa; selection-background-color: #305588; }
QComboBox::drop-down { width: 24px; border: none; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QProgressBar { background: #26334a; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #5c9aff; border-radius: 3px; }
QProgressBar#overall::chunk { background: #48cbb5; }
QTableWidget { background: #151f30; alternate-background-color: #182337; border: none; gridline-color: #263247; selection-background-color: #25446b; }
QTableWidget::item { padding: 3px 5px; border-bottom: 1px solid #253147; }
QHeaderView::section { background: #1b283c; color: #a8b9cf; padding: 5px; border: none; font-weight: 600; }
QPlainTextEdit { background: #101929; border: 1px solid #26354c; border-radius: 8px; padding: 8px; color: #acbdd3; font-size: 12px; }
QScrollBar:horizontal { background: #131d2e; height: 9px; border: none; border-radius: 4px; margin: 0; }
QScrollBar:vertical { background: #131d2e; width: 9px; border: none; border-radius: 4px; margin: 0; }
QScrollBar::handle:horizontal { background: #394b68; min-width: 24px; border: 1px solid #131d2e; border-radius: 4px; }
QScrollBar::handle:vertical { background: #394b68; min-height: 24px; border: 1px solid #131d2e; border-radius: 4px; }
QScrollBar::handle:horizontal:hover, QScrollBar::handle:vertical:hover { background: #59769b; }
QScrollBar::handle:horizontal:pressed, QScrollBar::handle:vertical:pressed { background: #78adff; }
QScrollBar::handle:horizontal:disabled, QScrollBar::handle:vertical:disabled { background: #253247; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: none; background: transparent; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; background: transparent; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow, QScrollBar::right-arrow { width: 0; height: 0; background: transparent; }
QAbstractScrollArea::corner { background: #131d2e; border: none; }
QToolTip { background: #23344e; color: #ffffff; border: 1px solid #567398; padding: 7px; }
'''


def size_text(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
        n /= 1024


def eta_text(seconds):
    if seconds is None:
        return '—'
    seconds = max(0, int(seconds))
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f'{h}h {m:02d}m' if h else f'{m}m {s:02d}s'


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


class ArrowComboBox(QComboBox):
    """Draw a Lucide chevron in Qt's native clickable dropdown area."""

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxArrow, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(arrow.center().x() - 8, arrow.center().y() - 8)
        painter.scale(16 / 24, 16 / 24)
        color = '#a8b9cf' if self.isEnabled() else '#53647d'
        painter.setPen(QPen(QColor(color), 2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        # Lucide chevron-down: m6 9 6 6 6-6. See THIRD_PARTY_NOTICES.md.
        painter.drawPolyline(QPolygonF([QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)]))


class ElidedLabel(QLabel):
    """Keep long names/statuses on one line without losing the full tooltip."""

    def __init__(self, text, name=None):
        super().__init__(text)
        if name:
            self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.contentsRect().width())
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class ActiveTransfer(QWidget):
    """A narrow, stable tile for one member of the active download pool."""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(48)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.percent = label('0%')
        self.percent.setStyleSheet('font-size: 12px; font-weight: 600; background: transparent;')
        self.percent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.percent.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name = ElidedLabel('', 'muted')
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        layout.addWidget(self.percent)
        layout.addWidget(self.name)
        layout.addWidget(self.bar)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep 100% readable even with twenty tiles at the minimum window width.
        font = QFont(self.percent.font())
        for pixels in (12, 11, 10, 9):
            font.setPixelSize(pixels)
            if QFontMetrics(font).horizontalAdvance('100%') <= self.width():
                break
        self.percent.setStyleSheet(f'font-size: {pixels}px; font-weight: 600; background: transparent;')

    def update_job(self, job, paused=False):
        fraction = max(0, min(1, job['done'] / max(1, job['size'])))
        self.percent.setText(f'{fraction:.0%}')
        self.name.setText(job['name'])
        self.bar.setValue(round(fraction * 1000))
        detail = (f'{job["name"]}\n{size_text(job["done"])} / {size_text(job["size"])}'
                  f'\n{"Paused" if paused else "Downloading"}\n{job["path"]}')
        for widget in (self, self.name, self.percent, self.bar):
            widget.setToolTip(detail)


class ProgressDelegate(QStyledItemDelegate):
    """Paint compact progress in each row without creating per-row widgets."""

    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ''
        style = styled.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        rect = option.rect.adjusted(7, 0, -7, -1)
        text_width = option.fontMetrics.horizontalAdvance('100%') + 6
        track = QRect(rect.left(), rect.center().y() - 3, max(0, rect.width() - text_width - 6), 6)
        fraction = max(0, min(1, index.data(Qt.ItemDataRole.UserRole) or 0))
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#2b3b53'))
        painter.drawRoundedRect(track, 3, 3)
        fill = QRect(track)
        fill.setWidth(round(track.width() * fraction))
        painter.setBrush(QColor('#48cbb5' if fraction == 1 else '#78adff'))
        painter.drawRoundedRect(fill, 3, 3)
        painter.setPen(QColor('#eaf0fa'))
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()


def button(text, slot, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(slot)
    return widget


def card():
    widget = QFrame()
    widget.setObjectName('card')
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(10, 8, 10, 8)
    layout.setSpacing(6)
    return widget, layout


class SettingsPanel(QWidget):
    """Reflow the same live settings cards between one and two columns."""

    def __init__(self, cards):
        super().__init__()
        self.cards = cards
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 4, 0)
        self.grid.setSpacing(10)
        for widget in cards:
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        columns = 2 if self.width() >= 640 else 1
        if columns == self.columns:
            return
        self.columns = columns
        for widget in self.cards:
            self.grid.removeWidget(widget)
        for row in range(len(self.cards) + 1):
            self.grid.setRowStretch(row, 0)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if columns == 2 else 0)
        for index, widget in enumerate(self.cards):
            self.grid.addWidget(widget, index // columns, index % columns, Qt.AlignmentFlag.AlignTop)
        self.grid.setRowStretch((len(self.cards) + columns - 1) // columns, 1)


class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('API credentials')
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.addWidget(label('Connect your Telegram account', 'section'))
        hint = label('Create an API application at my.telegram.org → API development tools.\nYour API credentials and Telegram session are encrypted with Windows DPAPI.', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.api_id, self.api_hash = QLineEdit(), QLineEdit()
        self.api_id.setPlaceholderText('e.g. 12345678')
        self.api_hash.setPlaceholderText('32-character API Hash')
        self.api_hash.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('API ID', self.api_id)
        form.addRow('API Hash', self.api_hash)
        layout.addLayout(form)
        layout.addWidget(button('Open Telegram API settings', lambda: QDesktopServices.openUrl(QUrl('https://my.telegram.org/apps'))))
        row = QHBoxLayout()
        row.addWidget(button('Import old config…', self.import_config))
        row.addWidget(button('Import old session…', self.import_session))
        layout.addLayout(row)
        self.legacy = ''
        self.import_label = label('Import is optional. Close the old scripts before importing.', 'muted')
        self.import_label.setWordWrap(True)
        layout.addWidget(self.import_label)
        self.error = label('')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import telegram_config.json', '', 'JSON files (*.json)')
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
                api_id, api_hash = validate_keys(data.get('api_id', data.get('API_ID')), data.get('api_hash', data.get('API_HASH')))
                self.api_id.setText(str(api_id))
                self.api_hash.setText(api_hash)
                self.error.setText('Credentials loaded. Click Save to encrypt them.')
            except Exception:
                self.error.setText('Cannot import this file. Expected api_id and api_hash fields.')

    def import_session(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import Telethon session', '', 'Telethon session (*.session)')
        if path:
            self.legacy = path
            self.import_label.setText('Selected: ' + Path(path).name + '\nThe original file will remain unchanged.')

    def validate(self):
        try:
            validate_keys(self.api_id.text(), self.api_hash.text())
        except (ValueError, TypeError):
            self.error.setText('Enter a positive API ID and a 32-character hexadecimal API Hash.')
            return
        self.accept()


class QRDialog(QDialog):
    def __init__(self, parent, cancel):
        super().__init__(parent)
        self.setWindowTitle('Sign in with Telegram')
        self.setFixedWidth(440)
        self.cancel = cancel
        self.finished_by_app = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        layout.addWidget(label('Scan to connect', 'title'))
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.picture)
        instructions = label('1. Open Telegram on your phone.\n2. Go to Settings → Devices → Link Desktop Device.\n3. Scan this code and confirm the sign-in.', 'muted')
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        layout.addWidget(label('The code refreshes automatically. Do not share it.', 'muted'))
        layout.addWidget(button('Cancel sign-in', self.reject))

    def set_url(self, url):
        qr = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        scale = max(1, 300 // len(matrix))
        edge = len(matrix) * scale
        image = QImage(edge, edge, QImage.Format.Format_RGB32)
        image.fill(QColor('white'))
        painter = QPainter(image)
        for y, row in enumerate(matrix):
            for x, cell in enumerate(row):
                if cell:
                    painter.fillRect(x * scale, y * scale, scale, scale, QColor('black'))
        painter.end()
        self.picture.setPixmap(QPixmap.fromImage(image))

    def reject(self):
        if not self.finished_by_app:
            self.cancel()
        super().reject()


class Window(QMainWindow):
    def __init__(self, bridge=None, demo=False):
        super().__init__()
        self.bridge, self.demo = bridge, demo
        self.account = None
        self.configured = False
        self.selected = None
        self.state = 'idle'
        self.operation_state = 'idle'
        self.finish_requested = False
        self.folder = ''
        self.current_job = None
        self.transfers_info = None
        self.queue_rows = {}
        self.queue_jobs = {}
        self.active_files = {}
        self.transfer_tiles = {}
        self.queue_snapshot = None
        self.queue_page = 0
        self.queue_pages = 1
        self.queue_request = 0
        self.demo_rows = []
        self.demo_summary = None
        self.qr_dialog = None
        self.password_dialog = None
        self.closing = False
        self.can_close = False
        self.last_progress = 0
        self.checker_path = None
        self.verification_info = None
        self.update_plan = None
        self.setWindowTitle(f'Telegram Archive Desktop v{__version__}' + (' — Preview' if demo else ''))
        self.resize(1150, 640)
        self.setMinimumSize(500, 340)
        self.setStyleSheet(STYLE)
        self.build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        if bridge:
            bridge.event.connect(self.on_event)
            QTimer.singleShot(50, self.boot)
        if demo:
            self.populate_demo()
        self.update_controls()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(10, 8, 10, 6)
        page.setSpacing(6)
        header = QHBoxLayout()
        logo = label('TA', 'logo')
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(28, 28)
        header.addWidget(logo)
        title_box = QHBoxLayout()
        title_box.setSpacing(8)
        self.title_label = ElidedLabel('Telegram Archive Desktop', 'title')
        title_box.addWidget(self.title_label, 1)
        self.version_label = label(f'v{__version__}', 'muted')
        self.version_label.setToolTip('Application version')
        title_box.addWidget(self.version_label)
        header.addLayout(title_box)
        header.addStretch()
        self.panel_btn = button('Settings', self.show_panel)
        header.addWidget(self.panel_btn)
        self.update_btn = button('Check updates', lambda: self.send('update'))
        self.update_btn.setToolTip('Install the latest GitHub version and restart. Stop active operations first.')
        header.addWidget(self.update_btn)
        self.badge = label('NOT CONNECTED', 'badge')
        header.addWidget(self.badge)
        page.addLayout(header)
        body = self.body_layout = QHBoxLayout()
        body.setSpacing(8)
        account_card, account_layout = card()
        self.account_name = ElidedLabel('Connect Telegram', 'section')
        account_layout.addWidget(self.account_name)
        self.account_detail = label('Sign in securely with a QR code.', 'muted')
        self.account_detail.setWordWrap(True)
        self.account_detail.setToolTip('Your Telegram session is encrypted and saved locally.')
        account_layout.addWidget(self.account_detail)
        account_actions = QHBoxLayout()
        self.connect_btn = button('Connect', self.connect_account, True)
        account_actions.addWidget(self.connect_btn)
        self.settings_btn = button('API settings', self.show_settings)
        self.logout_btn = button('Sign out', lambda: self.send('logout'))
        account_actions.addWidget(self.logout_btn)
        account_actions.addWidget(self.settings_btn)
        account_layout.addLayout(account_actions)
        source_card, source_layout = card()
        source_layout.addWidget(label('Groups & channels', 'section'))
        self.chat_box = ArrowComboBox()
        self.chat_box.setEditable(True)
        self.chat_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.chat_box.setMinimumContentsLength(10)
        self.chat_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.chat_box.lineEdit().setPlaceholderText('Choose or search a chat…')
        self.chat_box.activated.connect(self.choose_chat)
        self.chat_box.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.chat_box.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        source_layout.addWidget(self.chat_box)
        self.chat_box.setToolTip('Your last selection is remembered. Switch chats at any time.')
        self.refresh_btn = button('Refresh chats', lambda: self.send('refresh'))
        source_layout.addWidget(self.refresh_btn)
        dest_card, dest_layout = card()
        dest_layout.addWidget(label('Settings', 'section'))
        dest_layout.addWidget(label('Download folder', 'muted'))
        self.folder_label = ElidedLabel('Choose a download folder', 'section')
        dest_layout.addWidget(self.folder_label)
        self.folder_btn = button('Choose…', self.choose_folder)
        folder_actions = QHBoxLayout()
        folder_actions.addWidget(self.folder_btn)
        self.open_btn = button('Open', self.open_folder)
        self.open_btn.setToolTip('Open the archive folder for the selected chat.')
        folder_actions.addWidget(self.open_btn)
        dest_layout.addLayout(folder_actions)
        limits = QFormLayout()
        limits.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        limits.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        limits.setHorizontalSpacing(10)
        limits.setVerticalSpacing(8)
        limits.setContentsMargins(0, 4, 0, 0)
        self.parallel_box = ArrowComboBox()
        for count in range(1, 21):
            self.parallel_box.addItem(str(count), count)
        self.parallel_box.setCurrentIndex(19)
        self.parallel_box.setToolTip('Change the limit live. Increasing starts more files; decreasing lets active files finish. Paused downloads wait for Resume.')
        self.parallel_box.currentIndexChanged.connect(
            lambda: self.send('parallel', value=self.parallel_box.currentData()))
        self.parallel_box.setMinimumWidth(106)
        limits.addRow(label('Parallel downloads', 'muted'), self.parallel_box)
        self.speed_box = ArrowComboBox()
        for limit in SPEED_LIMITS:
            self.speed_box.addItem('Unlimited' if not limit else f'{limit:g} MB/s', limit)
        self.speed_box.setToolTip('Total download speed across all files. Changes apply while downloading.')
        self.speed_box.currentIndexChanged.connect(
            lambda: self.send('speed_limit', value=self.speed_box.currentData()))
        self.speed_box.setMinimumWidth(106)
        limits.addRow(label('Speed limit', 'muted'), self.speed_box)
        dest_layout.addLayout(limits)
        check_card, checks = card()
        checks.addWidget(label('Archive checks', 'section'))
        self.checker_label = ElidedLabel('WinRAR not configured', 'muted')
        checks.addWidget(self.checker_label)
        check_actions = QHBoxLayout()
        self.checker_btn = button('WinRAR…', self.choose_checker)
        self.checker_btn.setToolTip('Select the installed WinRAR.exe for automatic archive checks.')
        self.verify_btn = button('Check archives', lambda: self.send('verify'))
        self.verify_btn.setToolTip('Test downloaded files in this chat without a password. Confirmed failures move to wrong_archives.')
        check_actions.addWidget(self.checker_btn)
        check_actions.addWidget(self.verify_btn)
        checks.addLayout(check_actions)
        self.settings_panel = SettingsPanel([account_card, source_card, dest_card, check_card])
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_scroll.setFixedWidth(240)
        self.sidebar_scroll.setWidget(self.settings_panel)
        body.addWidget(self.sidebar_scroll)
        self.panel_dialog = QDialog(self)
        self.panel_dialog.setWindowTitle('Settings')
        self.panel_dialog.resize(480, 600)
        self.panel_dialog.setMinimumSize(320, 340)
        self.panel_layout = QVBoxLayout(self.panel_dialog)
        self.panel_layout.setContentsMargins(12, 12, 12, 12)
        self.panel_layout.setSpacing(10)
        panel_close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        panel_close.rejected.connect(self.panel_dialog.reject)
        self.panel_layout.addWidget(panel_close)
        self.panel_dialog.finished.connect(self.restore_panel)
        content = QVBoxLayout()
        content.setSpacing(6)
        current_card, current = card()
        file_heading = QHBoxLayout()
        self.file_name = ElidedLabel('No active download', 'section')
        file_heading.addWidget(self.file_name, 1)
        self.file_status = ElidedLabel('Ready', 'muted')
        current.addLayout(file_heading)
        self.file_bar = QProgressBar()
        self.file_bar.setObjectName('overall')
        self.file_bar.setRange(0, 1000)
        self.file_bar.setValue(0)
        self.file_bar.setTextVisible(False)
        self.byte_label = ElidedLabel('Choose a chat → Scan archives → Start', 'muted')
        self.percent_label = label('0%', 'section')
        file_heading.addWidget(self.byte_label, 1)
        file_heading.addWidget(self.percent_label)
        current.addWidget(self.file_bar)
        self.metrics_widget = QWidget()
        self.metrics_widget.setObjectName('transparent')
        metrics = QHBoxLayout(self.metrics_widget)
        metrics.setContentsMargins(0, 0, 0, 0)
        self.speed_value, self.eta_value, self.done_value = label('—', 'metric'), label('—', 'metric'), label('0 / 0', 'metric')
        for caption, value in [('Speed', self.speed_value), ('Time left', self.eta_value), ('Downloaded', self.done_value)]:
            column = QHBoxLayout()
            column.setSpacing(5)
            column.addWidget(label(caption, 'muted'))
            column.addWidget(value)
            metrics.addLayout(column)
        current.addWidget(self.metrics_widget)
        self.active_strip = QWidget()
        self.active_strip.setObjectName('transparent')
        self.active_layout = QHBoxLayout(self.active_strip)
        self.active_layout.setContentsMargins(0, 0, 0, 0)
        self.active_layout.setSpacing(6)
        self.active_scroll = QScrollArea()
        self.active_scroll.setObjectName('transparent')
        self.active_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.active_scroll.setWidgetResizable(True)
        self.active_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.active_scroll.setWidget(self.active_strip)
        self.active_scroll.hide()
        current.addWidget(self.active_scroll)
        controls = QHBoxLayout()
        self.scan_btn = button('Scan archives', lambda: self.send('scan'))
        self.start_btn = button('▶  Start', lambda: self.send('start'), True)
        self.pause_btn = button('Ⅱ  Pause', self.pause_resume)
        self.stop_btn = button('■  Stop', lambda: self.send('stop'))
        self.finish_btn = button('Finish current', lambda: self.send('finish_current'))
        self.finish_btn.setToolTip('Finish active downloads and pending archive checks, then stop. No new downloads will start. Resume if paused.')
        self.stop_btn.setToolTip('Stop immediately and keep partial files for the next Start.')
        for btn in (self.scan_btn, self.start_btn, self.pause_btn, self.finish_btn, self.stop_btn):
            controls.addWidget(btn)
        controls.addWidget(self.file_status, 1)
        current.addLayout(controls)
        content.addWidget(current_card)
        queue_card, queue = card()
        queue_heading = QHBoxLayout()
        queue_heading.addWidget(label('Download queue', 'section'))
        self.queue_caption = ElidedLabel('No archives yet', 'muted')
        queue_heading.addWidget(self.queue_caption, 1)
        self.queue_filter = ArrowComboBox()
        for text, mode in [('Unfinished', 'unfinished'), ('Downloaded', 'downloaded'), ('All', 'all')]:
            self.queue_filter.addItem(text, mode)
        self.queue_filter.setToolTip('Filter the saved queue without changing downloads. Downloaded includes unchecked and quarantined archives.')
        self.queue_filter.currentIndexChanged.connect(lambda: self.request_queue_page(0))
        queue_heading.addWidget(self.queue_filter)
        self.activity_toggle = button('Activity', self.toggle_activity)
        self.activity_toggle.setCheckable(True)
        self.activity_toggle.setToolTip('Show or hide the session log.')
        queue_heading.addWidget(self.activity_toggle)
        queue.addLayout(queue_heading)
        self.total_label = ElidedLabel('No files queued', 'muted')
        queue.addWidget(self.total_label)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['ARCHIVE', 'TRANSFERRED', 'PROGRESS', 'STATUS'])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setItemDelegateForColumn(2, ProgressDelegate(self.table))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index, width in [(1, 158), (2, 112), (3, 110)]:
            self.table.setColumnWidth(index, width)
        self.table.setMinimumHeight(60)
        queue.addWidget(self.table, 1)
        paging = QHBoxLayout()
        self.first_page = button('First', lambda: self.request_queue_page(0))
        self.prev_page = button('Previous', lambda: self.request_queue_page(self.queue_page - 1))
        self.next_page = button('Next', lambda: self.request_queue_page(self.queue_page + 1))
        self.last_page = button('Last', lambda: self.request_queue_page(self.queue_pages - 1))
        paging.addWidget(self.first_page)
        paging.addWidget(self.prev_page)
        paging.addStretch()
        paging.addWidget(label('Page', 'muted'))
        self.page_number = QLineEdit('1')
        self.page_number.setValidator(QIntValidator(1, 1, self.page_number))
        self.page_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_number.setFixedWidth(76)
        self.page_number.setToolTip('Type a page number and press Enter.')
        self.page_number.editingFinished.connect(self.go_to_typed_page)
        paging.addWidget(self.page_number)
        self.page_count = label('of 1', 'muted')
        paging.addWidget(self.page_count)
        paging.addStretch()
        paging.addWidget(self.next_page)
        paging.addWidget(self.last_page)
        queue.addLayout(paging)
        self.update_pager()
        content.addWidget(queue_card, 1)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(500)
        self.activity_dialog = QDialog(self)
        self.activity_dialog.setWindowTitle('Activity')
        self.activity_dialog.resize(480, 280)
        activity_layout = QVBoxLayout(self.activity_dialog)
        activity_layout.addWidget(self.activity)
        self.activity_dialog.finished.connect(lambda: self.activity_toggle.setChecked(False))
        self.activity.setPlaceholderText('Connection, scan, and download updates will appear here.')
        body.addLayout(content, 1)
        page.addLayout(body, 1)
        self.footer = ElidedLabel('Ready', 'muted')
        page.addWidget(self.footer)
        self.apply_window_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'footer'):
            self.apply_window_layout()

    def apply_window_layout(self):
        narrow = self.width() <= 900 or self.height() < 540
        small = self.width() < 700
        short = self.height() < 500
        self.panel_btn.setVisible(narrow)
        if not self.panel_dialog.isVisible():
            self.sidebar_scroll.setVisible(not narrow)
        self.badge.setVisible(not narrow)
        self.title_label.setText('Telegram Archives' if small else 'Telegram Archive Desktop')
        self.title_label.setFixedWidth(160 if small else 260)
        self.scan_btn.setText('Scan' if small else 'Scan archives')
        self.scan_btn.setToolTip('Scan archives in the selected chat.')
        self.file_status.setVisible(not small)
        self.metrics_widget.setVisible(not short)
        self.total_label.setVisible(not short)
        self.footer.setVisible(not short)
        self.queue_caption.setVisible(not small)
        count = len(self.transfer_tiles)
        strip_width = max(0, count * (40 if small else 32) + (count - 1) * 6)
        self.active_strip.setMinimumWidth(strip_width)
        available_width = self.width() - 40 - (248 if not narrow else 0)
        self.active_scroll.setFixedHeight((28 if short else 48) + (12 if strip_width > available_width else 0))
        for tile in self.transfer_tiles.values():
            tile.name.setVisible(not short)
            tile.setFixedHeight(28 if short else 48)
        for widget, full, compact in ((self.first_page, 'First', '«'), (self.prev_page, 'Previous', '‹'),
                                      (self.next_page, 'Next', '›'), (self.last_page, 'Last', '»')):
            widget.setText(compact if small else full)
            widget.setToolTip(full + ' page')
        self.page_number.setFixedWidth(52 if small else 76)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive if small
                                                         else QHeaderView.ResizeMode.Stretch)
        if small:
            self.table.setColumnWidth(0, 180)

    def show_panel(self):
        if self.panel_dialog.isVisible():
            self.panel_dialog.raise_()
            return
        self.body_layout.removeWidget(self.sidebar_scroll)
        self.sidebar_scroll.setMinimumWidth(0)
        self.sidebar_scroll.setMaximumWidth(16777215)
        self.panel_layout.insertWidget(0, self.sidebar_scroll, 1)
        self.sidebar_scroll.show()
        self.panel_dialog.show()

    def restore_panel(self):
        self.panel_layout.removeWidget(self.sidebar_scroll)
        self.sidebar_scroll.setFixedWidth(240)
        self.body_layout.insertWidget(0, self.sidebar_scroll)
        self.apply_window_layout()

    def toggle_activity(self):
        self.activity_dialog.setVisible(self.activity_toggle.isChecked())
        if self.activity_toggle.isChecked():
            self.activity_dialog.raise_()

    def boot(self):
        if self.bridge.ready.is_set():
            self.send('boot')
        elif self.bridge.thread.is_alive():
            QTimer.singleShot(50, self.boot)
        else:
            QMessageBox.critical(self, 'Startup failed', 'Cannot open the local application data. Check folder permissions.')

    def send(self, command, **kwargs):
        if self.demo:
            self.log('Preview mode: sample data only. No Telegram connection is made.')
            return
        if self.bridge and self.bridge.ready.is_set():
            self.bridge.send(command, **kwargs)

    def log(self, text):
        self.activity.appendPlainText(time.strftime('%H:%M:%S') + '  ' + str(text))

    def show_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.send('configure', api_id=dialog.api_id.text(), api_hash=dialog.api_hash.text(), legacy=dialog.legacy)
        dialog.api_hash.clear()

    def connect_account(self):
        if self.configured:
            self.send('connect')
        else:
            self.show_settings()

    def choose_chat(self, index):
        chat = self.chat_box.itemData(index)
        if chat is not None and chat != self.selected:
            self.send('select', chat=chat)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose download folder', self.folder)
        if folder:
            self.send('folder', path=folder)
            self.log('Folder updated for newly discovered files. Existing queue paths are preserved.')

    def open_folder(self):
        if not self.folder:
            return
        folder = Path(self.folder)
        if self.account and self.selected is not None:
            folder = folder / f'account_{self.account["id"]}' / f'chat_{self.selected}'
        if not folder.is_dir():
            QMessageBox.information(self, 'Archive folder',
                                    'The archive folder does not exist yet. Start a download to create it.\n\n' + str(folder))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, 'Cannot open folder',
                                'Could not open the archive folder in your file manager.\n\n' + str(folder))

    def choose_checker(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select installed WinRAR.exe', '', 'WinRAR (WinRAR.exe)')
        if path:
            self.send('checker', path=path)

    def pause_resume(self):
        self.send('resume' if self.state == 'paused' else 'pause')

    def update_controls(self):
        idle = self.state == 'idle'
        ready = bool(self.account and self.selected)
        self.scan_btn.setEnabled(ready and idle and bool(self.folder))
        self.start_btn.setEnabled(ready and idle)
        self.pause_btn.setEnabled(self.state in {'downloading', 'scanning', 'verifying', 'renaming', 'paused'})
        self.checker_btn.setEnabled(idle)
        self.verify_btn.setEnabled(ready and idle)
        self.pause_btn.setText('▶  Resume' if self.state == 'paused' else 'Ⅱ  Pause')
        self.stop_btn.setEnabled(not idle)
        self.finish_btn.setEnabled(self.operation_state == 'downloading'
                                   and self.state in {'downloading', 'paused'}
                                   and not self.finish_requested)
        self.connect_btn.setEnabled(idle and not self.account)
        self.connect_btn.setVisible(not self.account)
        self.settings_btn.setEnabled(idle)
        self.update_btn.setEnabled(idle)
        self.parallel_box.setEnabled(self.state != 'updating')
        self.logout_btn.setEnabled(idle and bool(self.account))
        self.logout_btn.setVisible(bool(self.account))
        self.refresh_btn.setEnabled(idle and bool(self.account))
        self.chat_box.setEnabled(bool(self.account))
        self.open_btn.setEnabled(bool(self.folder))

    def update_summary(self, summary):
        self.done_value.setText(f'{summary["complete"]} / {summary["total"]}')
        remaining = summary['total'] - summary['complete']
        self.queue_caption.setText(f'{remaining:,} remaining · {summary["complete"]:,} downloaded · {summary["failed"]} failed')
        mode = self.queue_filter.currentData()
        matching = {'all': summary['total'], 'downloaded': summary['complete'], 'unfinished': remaining}[mode]
        start = self.queue_page * QUEUE_PAGE_SIZE + 1 if self.table.rowCount() else 0
        end = start + self.table.rowCount() - 1 if start else 0
        scope = 'files' if mode == 'all' else mode
        self.total_label.setText(f'{start:,}–{end:,} of {matching:,} {scope} · {summary.get("verified", 0)} verified · {summary.get("unchecked", 0)} unchecked · {summary.get("quarantined", 0)} quarantined · {summary.get("check_failed", 0)} check errors')
        if self.state in {'idle', 'scanning'}:
            # The single aggregate bar also represents a restored or stopped queue.
            # Active operations such as filename restoration own it until they finish.
            percent = summary['received'] / max(1, summary['bytes'])
            self.file_name.setText('Download queue' if summary['total'] else 'No archives queued')
            self.file_bar.setValue(round(percent * 1000))
            self.percent_label.setText(f'{percent:.0%}')
            self.byte_label.setText(f'{size_text(summary["received"])} of {size_text(summary["bytes"])}')

    def render_transfers(self):
        value = self.transfers_info
        summary = value['summary']
        percent = summary['received'] / max(1, summary['bytes'])
        speed = value['speed'] if self.state == 'downloading' else 0
        self.file_name.setText(f"{value['active']} active · Limit {self.parallel_box.currentData()}" if value['active'] else 'Download queue')
        self.file_name.setToolTip('Total progress across all archives. Active files are shown below and individual progress is available in the queue.')
        self.file_bar.setValue(round(percent * 1000))
        self.percent_label.setText(f'{percent:.0%}')
        self.byte_label.setText(f"{size_text(summary['received'])} of {size_text(summary['bytes'])}")
        self.speed_value.setText(size_text(speed) + '/s' if speed else '—')
        self.eta_value.setText(eta_text((summary['bytes'] - summary['received']) / speed if speed else None))
        self.update_summary(summary)

    def reset_archive(self):
        self.queue_jobs = {}
        self.active_files = {}
        self.queue_snapshot = None
        self.queue_rows = {}
        self.queue_page = 0
        self.queue_pages = 1
        self.update_pager(force=True)
        self.render_active_transfers()
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.setRowCount(0)
        self.table.blockSignals(False)

    def render_active_transfers(self):
        for message in list(self.transfer_tiles):
            if message not in self.active_files:
                tile = self.transfer_tiles.pop(message)
                self.active_layout.removeWidget(tile)
                tile.hide()
                tile.deleteLater()
        # Retain positions while active; newly occupied slots append at the right.
        for message, job in self.active_files.items():
            if message not in self.transfer_tiles:
                tile = ActiveTransfer()
                self.transfer_tiles[message] = tile
                self.active_layout.addWidget(tile, 1)
            self.transfer_tiles[message].update_job(job, paused=self.state == 'paused')
        self.active_scroll.setVisible(bool(self.transfer_tiles))
        self.apply_window_layout()

    def update_pager(self, force=False):
        self.page_number.validator().setTop(self.queue_pages)
        if force or not self.page_number.hasFocus():
            self.page_number.setText(str(self.queue_page + 1))
        self.page_count.setText(f'of {self.queue_pages:,}')
        self.first_page.setEnabled(self.queue_page > 0)
        self.prev_page.setEnabled(self.queue_page > 0)
        self.next_page.setEnabled(self.queue_page + 1 < self.queue_pages)
        self.last_page.setEnabled(self.queue_page + 1 < self.queue_pages)

    def go_to_typed_page(self):
        if self.page_number.hasAcceptableInput():
            page = int(self.page_number.text()) - 1
            if page != self.queue_page:
                self.request_queue_page(page)
        self.page_number.setText(str(self.queue_page + 1))

    def request_queue_page(self, page):
        self.queue_request += 1
        self.queue_page = max(0, min(page, self.queue_pages - 1))
        self.table.setRowCount(0)
        self.queue_rows = {}
        self.queue_jobs = {}
        self.total_label.setText('Loading page…')
        self.update_pager(force=True)
        if self.demo:
            self.render_demo_queue()
        else:
            self.send('queue_view', mode=self.queue_filter.currentData(), page=self.queue_page,
                      request=self.queue_request)

    def render_demo_queue(self):
        if self.demo_summary is None:
            return
        mode = self.queue_filter.currentData()
        rows = [row for row in self.demo_rows if mode == 'all' or (row['state'] == 'done') == (mode == 'downloaded')]
        def priority(row):
            return (0 if row['state'] == 'downloading' else
                    1 if row['state'] == 'done' and row.get('verification') == 'checking' else
                    2 if row['state'] != 'done' else 3, row['message'])
        rows.sort(key=priority)
        pages = max(1, (len(rows) + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE)
        page = min(self.queue_page, pages - 1)
        self.on_event('queue', dict(rows=rows[page * QUEUE_PAGE_SIZE:(page + 1) * QUEUE_PAGE_SIZE],
                                   summary=self.demo_summary, mode=mode, page=page, pages=pages,
                                   matching=len(rows), request=self.queue_request))

    def update_verification_indicator(self):
        if self.verification_info:
            detail = self.verification_info['detail']
            attempt = detail.split('attempt ', 1)[-1] if 'attempt ' in detail else ''
            text = 'Checks paused' if self.state == 'paused' else 'Checking archive'
            self.checker_label.setText(text + (f' · {attempt}' if attempt else '…'))
            self.checker_label.setToolTip(self.verification_info['name'] + '\n' + detail)
        else:
            self.checker_label.setText('Automatic checks on' if self.checker_path else 'WinRAR not configured')
            self.checker_label.setToolTip(str(self.checker_path) if self.checker_path else 'Downloads remain Unchecked until WinRAR is configured. Install WinRAR and select WinRAR.exe.')

    def on_event(self, name, value):
        if name == 'state':
            previous_state = self.state
            self.state = value
            if value != 'paused':
                self.operation_state = value
            if value == 'idle':
                self.finish_requested = False
            titles = {'idle': 'Ready', 'connecting': 'Connecting…', 'authorizing': 'Waiting for sign-in…', 'refreshing': 'Refreshing chats…', 'renaming': 'Restoring filenames…', 'scanning': 'Scanning messages…', 'downloading': 'Downloading', 'verifying': 'Checking archives…', 'updating': 'Checking updates…', 'paused': 'Paused — progress saved'}
            self.file_status.setText(titles.get(value, value))
            if not (previous_state == 'updating' and value == 'idle'):
                self.footer.setText(titles.get(value, value))
            if value in {'idle', 'paused', 'verifying'}:
                self.on_event('speed_reset', None)
            self.update_verification_indicator()
            if value in {'paused', 'downloading'}:
                for row in range(self.table.rowCount()):
                    status = self.table.item(row, 3)
                    if status and status.text() in {'Downloading', 'Paused'}:
                        status.setText('Paused' if value == 'paused' else 'Downloading')
            if value == 'idle' and self.current_job:
                row = self.queue_rows.get(self.current_job['message'])
                if row is not None:
                    self.file_status.setText(self.table.item(row, 3).text())
        elif name == 'finish_current':
            self.finish_requested = bool(value)
        elif name == 'transfers':
            if 'files' in value:
                self.active_files = {row['message']: dict(row) for row in value['files']}
            self.transfers_info = value
            self.current_job = None
            self.last_progress = time.monotonic()
            self.render_transfers()
        elif name == 'update_progress':
            self.footer.setText(value)
        elif name == 'update_ready':
            self.update_plan = value
            self.footer.setText('Update ready. Saving progress and restarting…')
            self.close()
        elif name == 'checker':
            self.checker_path = value
            self.update_verification_indicator()
        elif name == 'verification':
            self.verification_info = value
            self.update_verification_indicator()
        elif name == 'phase':
            self.file_status.setText('Checking archive…')
            self.footer.setText(value)
            self.on_event('speed_reset', None)
        elif name == 'rename_progress':
            self.file_name.setText('Restoring archive names')
            self.file_status.setText('Restoring filenames…')
            percent = value['done'] / max(1, value['total'])
            self.file_bar.setValue(round(1000 * percent))
            self.percent_label.setText(f'{percent:.0%}')
            self.byte_label.setText(f"{value['done']} of {value['total']} archives processed")
            self.footer.setText(f"Restoring filenames: {value['done']} / {value['total']}")
            self.on_event('speed_reset', None)
        elif name == 'rename_result':
            self.file_name.setText('Filename restoration finished')
            self.byte_label.setText(f"{value['restored']} restored · {value['skipped']} skipped")
            self.file_bar.setValue(1000)
            self.percent_label.setText('100%')
        elif name == 'configured':
            self.configured = value
            if not value:
                self.log('Welcome. Open API settings to connect your Telegram account.')
        elif name == 'settings':
            self.speed_box.blockSignals(True)
            self.speed_box.setCurrentIndex(self.speed_box.findData(speed_limit(value.get('speed_limit'))))
            self.speed_box.blockSignals(False)
            self.parallel_box.blockSignals(True)
            self.parallel_box.setCurrentIndex(parallel_downloads(value.get('parallel_downloads')) - 1)
            self.parallel_box.blockSignals(False)
            if self.transfers_info is not None and self.state in {'downloading', 'paused', 'verifying'}:
                self.render_transfers()
            self.folder = value.get('download_dir', '')
            self.folder_label.setText(Path(self.folder).name if self.folder else 'Choose a download folder')
            self.folder_label.setToolTip(self.folder)
        elif name == 'account':
            if (self.account or {}).get('id') != (value or {}).get('id'):
                self.reset_archive()
            self.account = value
            self.account_name.setText(value['name'] if value else 'Connect Telegram')
            self.account_detail.setText(f'ID: {value["id"]}' if value else 'Sign in securely with a QR code.')
            self.badge.setText('CONNECTED' if value else 'NOT CONNECTED')
        elif name == 'dialogs':
            self.selected = None
            self.chat_box.blockSignals(True)
            self.chat_box.clear()
            for row in value:
                self.chat_box.addItem(f'{row["name"]} · {row["kind"]}', row['id'])
                self.chat_box.setItemData(self.chat_box.count() - 1, str(row['id']), Qt.ItemDataRole.ToolTipRole)
            self.chat_box.setCurrentIndex(-1)
            self.chat_box.blockSignals(False)
        elif name == 'selected':
            if self.selected != value:
                self.reset_archive()
            self.selected = value
            self.chat_box.blockSignals(True)
            self.chat_box.setCurrentIndex(self.chat_box.findData(value) if value is not None else -1)
            self.chat_box.lineEdit().setCursorPosition(0)
            self.chat_box.blockSignals(False)
            self.chat_box.setToolTip(self.chat_box.currentText())
        elif name == 'current':
            if value and self.transfers_info is not None and self.state == 'downloading':
                return
            self.transfers_info = None
            self.current_job = value
            if value and self.state == 'downloading':
                self.file_status.setText('Downloading')
            self.file_name.setText(value['name'] if value else 'No active download')
            self.file_name.setToolTip(value['path'] if value else '')
            if not value:
                self.file_bar.setValue(0)
                self.percent_label.setText('0%')
                self.byte_label.setText('Choose a chat → Scan archives → Start')
                self.on_event('speed_reset', None)
        elif name == 'progress':
            for job in (self.active_files.get(value['message']), self.queue_jobs.get(value['message'])):
                if job is not None and job['message'] == value['message']:
                    job.update(done=value['done'], size=value['size'])
            if value['message'] in self.transfer_tiles and value['message'] in self.active_files:
                self.transfer_tiles[value['message']].update_job(self.active_files[value['message']], self.state == 'paused')
            self.last_progress = time.monotonic()
            percent = value['done'] / max(1, value['size'])
            if self.transfers_info is None:
                self.file_bar.setValue(round(percent * 1000))
                self.percent_label.setText(f'{percent:.0%}')
                self.byte_label.setText(f'{size_text(value["done"])} of {size_text(value["size"])}')
                self.speed_value.setText(size_text(value['speed']) + '/s' if value['speed'] else '—')
                self.eta_value.setText(eta_text((value['size'] - value['done']) / value['speed'] if value['speed'] else None))
            if value['message'] in self.queue_rows:
                row = self.queue_rows[value['message']]
                self.table.item(row, 1).setText(f'{size_text(value["done"])} / {size_text(value["size"])}')
                self.table.item(row, 2).setText(f'{percent:.0%}')
                self.table.item(row, 2).setData(Qt.ItemDataRole.UserRole, percent)
                self.table.item(row, 3).setText('Paused' if self.state == 'paused' else 'Downloading')
                self.table.item(row, 3).setForeground(QColor('#78adff'))
            if 'summary' in value:
                self.update_summary(value['summary'])
        elif name == 'queue':
            if value.get('request', self.queue_request) != self.queue_request or value.get('mode', self.queue_filter.currentData()) != self.queue_filter.currentData():
                return
            if 'chat' in value and (value['chat'] != self.selected or value['account'] != (self.account or {}).get('id')):
                return
            self.queue_snapshot = value
            old_page = self.queue_page
            self.queue_page = value.get('page', 0)
            self.queue_pages = value.get('pages', 1)
            self.update_pager(force=self.queue_page != old_page)
            mode = self.queue_filter.currentData()
            rows = [row for row in value['rows'] if mode == 'all' or (row['state'] == 'done') == (mode == 'downloaded')]
            selected_item = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
            selected_message = selected_item.data(Qt.ItemDataRole.UserRole) if selected_item else None
            self.table.blockSignals(True)
            scroll = self.table.verticalScrollBar().value()
            self.table.setRowCount(len(rows))
            self.queue_rows = {}
            self.queue_jobs = {row['message']: dict(row) for row in rows}
            for index, row in enumerate(rows):
                self.queue_rows[row['message']] = index
                status = {'queued': 'Queued', 'downloading': 'Downloading', 'done': 'Completed', 'error': 'Failed'}.get(row['state'], row['state'])
                verification = row.get('verification', 'pending')
                if row['state'] == 'done':
                    status = {'pending': 'Unchecked', 'checking': 'Checking…', 'ok': 'Verified', 'error': 'Check failed',
                              'quarantining': 'Moving…', 'quarantined': 'Quarantined'}.get(verification, 'Unchecked')
                if row['state'] == 'downloading' and self.state == 'paused':
                    status = 'Paused'
                fraction = row['done'] / max(row['size'], 1)
                texts = [row['name'], f'{size_text(row["done"])} / {size_text(row["size"])}', f'{fraction:.0%}', status]
                for col, text in enumerate(texts):
                    item = QTableWidgetItem(text)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, row['message'])
                    elif col == 2:
                        item.setData(Qt.ItemDataRole.UserRole, fraction)
                    item.setToolTip('\n'.join(filter(None, [row['error'], row.get('verification_error'), row.get('quarantine_path') or row['path']])))
                    self.table.setItem(index, col, item)
                self.table.item(index, 3).setForeground(QColor({'done': '#69ddc3', 'error': '#ff9a9d', 'downloading': '#78adff'}.get(row['state'], '#a6b6cc')))
                if row['state'] == 'done' and verification != 'ok':
                    self.table.item(index, 3).setForeground(QColor('#ff9a9d' if verification in {'error', 'quarantined'} else '#e8c77f'))
            self.table.verticalScrollBar().setValue(scroll if self.queue_page == old_page else 0)
            if selected_message in self.queue_rows:
                self.table.setCurrentCell(self.queue_rows[selected_message], 0)
            else:
                self.table.setCurrentCell(-1, -1)
            self.table.blockSignals(False)
            self.update_summary(value['summary'])
            if self.current_job and self.state == 'idle':
                index = self.queue_rows.get(self.current_job['message'])
                if index is not None:
                    self.file_status.setText(self.table.item(index, 3).text())
        elif name == 'scan':
            self.footer.setText(f'Scanning history · {value:,} new messages checked · ZIP and RAR files added automatically')
        elif name == 'qr':
            if value:
                if self.qr_dialog is None:
                    self.qr_dialog = QRDialog(self, lambda: self.send('stop'))
                self.qr_dialog.set_url(value)
                self.qr_dialog.show()
                self.qr_dialog.raise_()
            elif self.qr_dialog:
                self.qr_dialog.finished_by_app = True
                self.qr_dialog.close()
                self.qr_dialog = None
        elif name == 'password':
            dialog = QInputDialog(self)
            self.password_dialog = dialog
            dialog.setWindowTitle('Telegram two-step verification')
            dialog.setLabelText('Enter your Telegram password. It will not be saved.')
            dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
            dialog.accepted.connect(lambda: self.send('password', value=dialog.textValue()))
            dialog.rejected.connect(lambda: self.send('password', value=None))
            dialog.finished.connect(lambda: dialog.setTextValue(''))
            dialog.open()
        elif name == 'speed_reset':
            self.speed_value.setText('—')
            self.eta_value.setText('—')
        elif name == 'log':
            self.log(value)
        elif name == 'error':
            self.log(value)
            self.footer.setText(value)
            if not self.closing:
                QMessageBox.warning(self, 'Action needed', value)
        elif name == 'closed':
            self.can_close = True
            self.close()
        if name in {'transfers', 'state'}:
            self.render_active_transfers()
        if self.finish_requested and self.state in {'downloading', 'paused', 'verifying'}:
            status = {'downloading': 'Finishing current…', 'paused': 'Finish pending · paused',
                      'verifying': 'Finishing checks…'}[self.state]
            self.file_status.setText(status)
            self.footer.setText(status + ' · No new downloads will start')
        self.update_controls()

    def tick(self):
        if self.state == 'downloading' and time.monotonic() - self.last_progress > 4:
            self.on_event('speed_reset', None)

    def closeEvent(self, event):
        self.panel_dialog.close()
        self.activity_dialog.close()
        if self.can_close or self.demo or not self.bridge or not self.bridge.thread.is_alive():
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.closing = True
            self.centralWidget().setEnabled(False)
            self.footer.setText('Saving progress and disconnecting safely…')
            if self.password_dialog:
                self.password_dialog.reject()
            self.send('close')

    def populate_demo(self):
        self.configured = True
        self.on_event('account', {'name': 'Alex Morgan', 'id': 123456789})
        self.on_event('dialogs', [{'id': -100123, 'name': 'Design Resources', 'kind': 'Group'}, {'id': -100456, 'name': 'Archive Library', 'kind': 'Channel'}])
        self.on_event('selected', -100123)
        self.on_event('settings', {'download_dir': 'C:/Users/Alex/Downloads/Telegram Archives'})
        self.on_event('checker', 'C:/Program Files/WinRAR/WinRAR.exe')
        rows = []
        for i, (name, size, done, state) in enumerate([
            ('Studio textures — collection 01.zip', 428, 428, 'done'),
            ('Architectural materials pack.zip', 820, 820, 'done'),
            ('Environment assets — autumn.rar', 1240, 768.8, 'downloading'),
            ('Lighting presets collection.part01.rar', 156, 62.4, 'downloading'),
            ('Reference photography — vol. 04.zip', 592, 177.6, 'downloading'),
            ('Concrete surfaces.rar', 318, 31.8, 'downloading'),
            ('Wood textures — 4K.zip', 760, 0, 'queued'),
            ('Interior lighting references.zip', 96, 0, 'queued'),
            ('Outdoor furniture pack.rar', 684, 0, 'queued'),
            ('Studio HDR environments.zip', 512, 0, 'queued'),
            ('Fabric collection.rar', 245, 0, 'queued'),
            ('Landscape references.zip', 128, 0, 'queued')]):
            rows.append({'message': i + 1, 'name': name, 'size': int(size * 1024**2), 'done': int(done * 1024**2), 'state': state, 'path': name, 'error': '',
                         'verification': 'ok' if i == 0 else 'checking' if i == 1 else 'pending'})
        summary = {'total': len(rows), 'bytes': sum(row['size'] for row in rows), 'received': sum(row['done'] for row in rows), 'complete': 2, 'failed': 0, 'verified': 1, 'quarantined': 0}
        self.demo_rows, self.demo_summary = rows, summary
        self.render_demo_queue()
        self.on_event('current', rows[2])
        self.on_event('state', 'downloading')
        self.on_event('progress', {'done': rows[2]['done'], 'size': rows[2]['size'], 'speed': 18.6 * 1024**2, 'message': 3, 'summary': summary})
        self.on_event('transfers', {'active': 4, 'speed': 18.6 * 1024**2, 'summary': summary,
                                   'files': [row for row in rows if row['state'] == 'downloading']})
        self.on_event('verification', {'name': rows[1]['name'], 'detail': 'Checking archive without a password: attempt 2/3…'})
        self.log('Preview mode — sample data. No Telegram connection.')
        self.log('Last selected chat restored: Design Resources.')
        self.log('Checking Architectural materials pack.zip in the background. Downloads continue.')
        self.log('Downloading 4 archives in parallel. Partial files are saved automatically.')
        self.badge.setText('PREVIEW · SAMPLE DATA')
        self.footer.setText('Preview mode · Interface demonstration only · No real download is running')
