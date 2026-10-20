import json
import time
from pathlib import Path
import qrcode
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from .security import validate_keys
from .storage import parallel_downloads

STYLE = '''
QWidget { background: #0d1422; color: #eaf0fa; font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }
QMainWindow { background: #0d1422; }
QFrame#card { background: #151f30; border: 1px solid #253247; border-radius: 14px; }
QFrame#card QLabel, QFrame#card QWidget#transparent { background: transparent; }
QLabel#muted { color: #99abc3; }
QLabel#title { font-size: 21px; font-weight: 700; }
QLabel#section { font-size: 16px; font-weight: 600; }
QLabel#metric { font-size: 18px; font-weight: 600; }
QLabel#badge { color: #69dcca; background: #153e3e; border-radius: 10px; padding: 6px 12px; }
QLabel#logo { background: #4187ff; color: white; border-radius: 13px; font-size: 21px; font-weight: 800; }
QPushButton { background: #233149; border: 1px solid #34445f; border-radius: 8px; padding: 8px 10px; min-height: 18px; font-weight: 600; }
QPushButton:hover { background: #304460; border-color: #6a91c7; }
QPushButton:pressed { background: #17243a; }
QPushButton:disabled { color: #6d7b90; background: #172134; border-color: #263249; }
QPushButton#primary { background: #4689ff; color: white; border-color: #4689ff; }
QPushButton#primary:hover { background: #639bff; }
QPushButton#primary:disabled { background: #273d60; color: #7d91af; border-color: #273d60; }
QLineEdit, QComboBox { background: #0f1828; border: 1px solid #33445f; border-radius: 7px; padding: 8px; min-height: 20px; selection-background-color: #3868ae; }
QLineEdit:focus, QComboBox:focus { border-color: #5996ff; }
QComboBox QAbstractItemView { background: #162236; color: #eaf0fa; selection-background-color: #305588; }
QComboBox::drop-down { width: 24px; border: none; }
QProgressBar { background: #26334a; border: none; border-radius: 5px; min-height: 10px; max-height: 10px; }
QProgressBar::chunk { background: #5c9aff; border-radius: 5px; }
QProgressBar#overall::chunk { background: #48cbb5; }
QTableWidget { background: #151f30; alternate-background-color: #182337; border: none; gridline-color: #263247; selection-background-color: #25446b; }
QTableWidget::item { padding: 5px; border-bottom: 1px solid #253147; }
QHeaderView::section { background: #1b283c; color: #a8b9cf; padding: 7px; border: none; font-weight: 600; }
QPlainTextEdit { background: #101929; border: 1px solid #26354c; border-radius: 8px; padding: 8px; color: #acbdd3; font-size: 12px; }
QScrollBar:vertical { background: #131d2e; width: 9px; }
QScrollBar::handle:vertical { background: #394b68; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
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
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)
    return widget, layout


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
        self.folder = ''
        self.current_job = None
        self.transfers_info = None
        self.queue_rows = {}
        self.qr_dialog = None
        self.password_dialog = None
        self.closing = False
        self.can_close = False
        self.last_progress = 0
        self.checker_path = None
        self.verification_info = None
        self.update_plan = None
        self.setWindowTitle('Telegram Archive Desktop' + (' — Preview' if demo else ''))
        self.resize(1220, 850)
        self.setMinimumSize(1000, 680)
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
        page.setContentsMargins(16, 14, 16, 10)
        page.setSpacing(10)
        header = QHBoxLayout()
        logo = label('TA', 'logo')
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(36, 36)
        header.addWidget(logo)
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title_box.addWidget(label('Telegram Archive Desktop', 'title'))
        header.addLayout(title_box)
        header.addStretch()
        self.update_btn = button('Check updates', lambda: self.send('update'))
        self.update_btn.setToolTip('Install the latest GitHub version and restart. Stop active operations first.')
        header.addWidget(self.update_btn)
        self.badge = label('NOT CONNECTED', 'badge')
        header.addWidget(self.badge)
        page.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(12)
        sidebar = QVBoxLayout()
        sidebar.setSpacing(10)
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)
        sidebar_widget.setFixedWidth(240)
        sidebar.setContentsMargins(0, 0, 0, 0)
        account_card, account_layout = card()
        self.account_name = label('Connect Telegram', 'section')
        self.account_name.setWordWrap(True)
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
        sidebar.addWidget(account_card)
        source_card, source_layout = card()
        source_layout.addWidget(label('Groups & channels', 'section'))
        self.chat_box = QComboBox()
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
        sidebar.addWidget(source_card)
        dest_card, dest_layout = card()
        dest_layout.addWidget(label('SAVE TO', 'muted'))
        self.folder_label = label('Choose a download folder', 'section')
        self.folder_label.setWordWrap(True)
        dest_layout.addWidget(self.folder_label)
        self.folder_btn = button('Choose…', self.choose_folder)
        folder_actions = QHBoxLayout()
        folder_actions.addWidget(self.folder_btn)
        self.open_btn = button('Open', self.open_folder)
        folder_actions.addWidget(self.open_btn)
        dest_layout.addLayout(folder_actions)
        parallel_row = QHBoxLayout()
        parallel_row.addWidget(label('Parallel downloads', 'muted'))
        self.parallel_box = QComboBox()
        for count in range(1, 21):
            self.parallel_box.addItem(str(count), count)
        self.parallel_box.setCurrentIndex(19)
        self.parallel_box.setToolTip('Simultaneous downloads. Stop the queue before changing this setting.')
        self.parallel_box.currentIndexChanged.connect(
            lambda: self.send('parallel', value=self.parallel_box.currentData()))
        parallel_row.addWidget(self.parallel_box)
        dest_layout.addLayout(parallel_row)
        sidebar.addWidget(dest_card)
        sidebar.addStretch()
        body.addWidget(sidebar_widget)
        content = QVBoxLayout()
        content.setSpacing(10)
        current_card, current = card()
        file_heading = QHBoxLayout()
        self.file_name = label('No active download', 'section')
        self.file_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        file_heading.addWidget(self.file_name, 1)
        self.file_status = label('Ready', 'muted')
        file_heading.addWidget(self.file_status)
        current.addLayout(file_heading)
        self.file_bar = QProgressBar()
        self.file_bar.setRange(0, 1000)
        self.file_bar.setValue(0)
        self.file_bar.setTextVisible(False)
        current.addWidget(self.file_bar)
        byte_row = QHBoxLayout()
        self.byte_label = label('Choose a chat → Scan archives → Start', 'muted')
        self.percent_label = label('0%', 'section')
        byte_row.addWidget(self.byte_label)
        byte_row.addStretch()
        byte_row.addWidget(self.percent_label)
        current.addLayout(byte_row)
        metrics = QHBoxLayout()
        self.speed_value, self.eta_value, self.done_value = label('—', 'metric'), label('—', 'metric'), label('0 / 0', 'metric')
        for caption, value in [('Speed', self.speed_value), ('Time left', self.eta_value), ('Downloaded', self.done_value)]:
            column = QHBoxLayout()
            column.setSpacing(8)
            column.addWidget(label(caption, 'muted'))
            column.addWidget(value)
            metrics.addLayout(column)
        current.addLayout(metrics)
        controls = QHBoxLayout()
        self.scan_btn = button('Scan archives', lambda: self.send('scan'))
        self.start_btn = button('▶  Start', lambda: self.send('start'), True)
        self.pause_btn = button('Ⅱ  Pause', self.pause_resume)
        self.stop_btn = button('■  Stop', lambda: self.send('stop'))
        for btn in (self.scan_btn, self.start_btn, self.pause_btn, self.stop_btn):
            controls.addWidget(btn)
        current.addLayout(controls)
        checks = QHBoxLayout()
        self.checker_label = label('WinRAR not configured', 'muted')
        checks.addWidget(self.checker_label, 1)
        self.checker_btn = button('WinRAR…', self.choose_checker)
        self.checker_btn.setToolTip('Select the installed WinRAR.exe for automatic archive checks.')
        self.verify_btn = button('Check archives', lambda: self.send('verify'))
        self.verify_btn.setToolTip('Test downloaded files in this chat without a password. Confirmed failures move to wrong_archives.')
        checks.addWidget(self.checker_btn)
        checks.addWidget(self.verify_btn)
        current.addLayout(checks)
        content.addWidget(current_card)
        queue_card, queue = card()
        queue_heading = QHBoxLayout()
        queue_heading.addWidget(label('Download queue', 'section'))
        queue_heading.addStretch()
        self.queue_caption = label('No archives yet', 'muted')
        queue_heading.addWidget(self.queue_caption)
        queue.addLayout(queue_heading)
        self.overall_bar = QProgressBar()
        self.overall_bar.setObjectName('overall')
        self.overall_bar.setRange(0, 1000)
        self.overall_bar.setValue(0)
        self.overall_bar.setTextVisible(False)
        queue.addWidget(self.overall_bar)
        self.total_label = label('No files queued', 'muted')
        queue.addWidget(self.total_label)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['ARCHIVE', 'SIZE', 'PROGRESS', 'STATUS'])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index, width in [(1, 92), (2, 102), (3, 126)]:
            self.table.setColumnWidth(index, width)
        self.table.setMinimumHeight(240)
        queue.addWidget(self.table, 1)
        content.addWidget(queue_card, 1)
        activity_header = QHBoxLayout()
        self.activity_toggle = button('Activity', lambda: self.activity.setVisible(self.activity_toggle.isChecked()))
        self.activity_toggle.setCheckable(True)
        activity_header.addWidget(self.activity_toggle)
        activity_header.addStretch()
        content.addLayout(activity_header)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(500)
        self.activity.setFixedHeight(72)
        self.activity.hide()
        self.activity.setPlaceholderText('Connection, scan, and download updates will appear here.')
        content.addWidget(self.activity)
        body.addLayout(content, 1)
        page.addLayout(body, 1)
        self.footer = label('Ready', 'muted')
        self.footer.setWordWrap(True)
        page.addWidget(self.footer)

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
        if self.folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder))

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
        self.pause_btn.setEnabled(self.state in {'downloading', 'scanning', 'verifying', 'paused'})
        self.checker_btn.setEnabled(idle)
        self.verify_btn.setEnabled(ready and idle)
        self.pause_btn.setText('▶  Resume' if self.state == 'paused' else 'Ⅱ  Pause')
        self.stop_btn.setEnabled(not idle)
        self.connect_btn.setEnabled(idle and not self.account)
        self.connect_btn.setVisible(not self.account)
        self.settings_btn.setEnabled(idle)
        self.update_btn.setEnabled(idle)
        self.parallel_box.setEnabled(idle)
        self.logout_btn.setEnabled(idle and bool(self.account))
        self.logout_btn.setVisible(bool(self.account))
        self.refresh_btn.setEnabled(idle and bool(self.account))
        self.chat_box.setEnabled(bool(self.account))
        self.open_btn.setEnabled(bool(self.folder))

    def update_summary(self, summary):
        self.done_value.setText(f'{summary["complete"]} / {summary["total"]}')
        self.queue_caption.setText(f'{summary["total"]:,} files · {summary["failed"]} failed · {summary.get("quarantined", 0)} quarantined')
        self.overall_bar.setValue(round(1000 * summary['received'] / max(1, summary['bytes'])))
        self.total_label.setText(f'{size_text(summary["received"])} of {size_text(summary["bytes"])} · {summary.get("verified", 0)} verified · {summary.get("unchecked", 0)} unchecked · {summary.get("check_failed", 0)} check errors')

    def render_transfers(self):
        value = self.transfers_info
        summary = value['summary']
        percent = summary['received'] / max(1, summary['bytes'])
        speed = value['speed'] if self.state == 'downloading' else 0
        self.file_name.setText(f"{value['active']} active downloads" if value['active'] else 'Download queue')
        self.file_name.setToolTip('Total progress across all archives. Individual progress is shown in the queue.')
        self.file_bar.setValue(round(percent * 1000))
        self.percent_label.setText(f'{percent:.0%}')
        self.byte_label.setText(f"{size_text(summary['received'])} of {size_text(summary['bytes'])}")
        self.speed_value.setText(size_text(speed) + '/s' if speed else '—')
        self.eta_value.setText(eta_text((summary['bytes'] - summary['received']) / speed if speed else None))
        self.update_summary(summary)

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
            titles = {'idle': 'Ready', 'connecting': 'Connecting…', 'authorizing': 'Waiting for sign-in…', 'refreshing': 'Refreshing chats…', 'scanning': 'Scanning messages…', 'downloading': 'Downloading', 'verifying': 'Checking archives…', 'updating': 'Checking updates…', 'paused': 'Paused — progress saved'}
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
        elif name == 'transfers':
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
        elif name == 'configured':
            self.configured = value
            if not value:
                self.log('Welcome. Open API settings to connect your Telegram account.')
        elif name == 'settings':
            self.parallel_box.blockSignals(True)
            self.parallel_box.setCurrentIndex(parallel_downloads(value.get('parallel_downloads')) - 1)
            self.parallel_box.blockSignals(False)
            self.folder = value.get('download_dir', '')
            self.folder_label.setText(Path(self.folder).name if self.folder else 'Choose a download folder')
            self.folder_label.setToolTip(self.folder)
        elif name == 'account':
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
            self.selected = value
            self.chat_box.blockSignals(True)
            self.chat_box.setCurrentIndex(self.chat_box.findData(value) if value is not None else -1)
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
                self.table.item(row, 2).setText(f'{percent:.0%}')
                self.table.item(row, 3).setText('Paused' if self.state == 'paused' else 'Downloading')
                self.table.item(row, 3).setForeground(QColor('#78adff'))
            if 'summary' in value:
                self.update_summary(value['summary'])
        elif name == 'queue':
            scroll = self.table.verticalScrollBar().value()
            self.table.setRowCount(len(value['rows']))
            self.queue_rows = {}
            for index, row in enumerate(value['rows']):
                self.queue_rows[row['message']] = index
                status = {'queued': 'Queued', 'downloading': 'Downloading', 'done': 'Completed', 'error': 'Failed'}.get(row['state'], row['state'])
                verification = row.get('verification', 'pending')
                if row['state'] == 'done':
                    status = {'pending': 'Unchecked', 'checking': 'Checking…', 'ok': 'Verified', 'error': 'Check failed',
                              'quarantining': 'Moving…', 'quarantined': 'Quarantined'}.get(verification, 'Unchecked')
                if row['state'] == 'downloading' and self.state == 'paused':
                    status = 'Paused'
                texts = [row['name'], size_text(row['size']), f'{row["done"] / max(row["size"], 1):.0%}', status]
                for col, text in enumerate(texts):
                    item = QTableWidgetItem(text)
                    item.setToolTip('\n'.join(filter(None, [row['error'], row.get('verification_error'), row.get('quarantine_path') or row['path']])))
                    self.table.setItem(index, col, item)
                self.table.item(index, 3).setForeground(QColor({'done': '#69ddc3', 'error': '#ff9a9d', 'downloading': '#78adff'}.get(row['state'], '#a6b6cc')))
                if row['state'] == 'done' and verification != 'ok':
                    self.table.item(index, 3).setForeground(QColor('#ff9a9d' if verification in {'error', 'quarantined'} else '#e8c77f'))
            self.table.verticalScrollBar().setValue(scroll)
            self.update_summary(value['summary'])
            if self.current_job and self.state == 'idle':
                index = self.queue_rows.get(self.current_job['message'])
                if index is not None:
                    self.file_status.setText(self.table.item(index, 3).text())
            if value['summary']['total'] > 1000:
                self.queue_caption.setText(self.queue_caption.text() + ' · first 1,000 shown')
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
        self.update_controls()

    def tick(self):
        if self.state == 'downloading' and time.monotonic() - self.last_progress > 4:
            self.on_event('speed_reset', None)

    def closeEvent(self, event):
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
        self.on_event('queue', {'rows': rows, 'summary': summary})
        self.on_event('current', rows[2])
        self.on_event('state', 'downloading')
        self.on_event('progress', {'done': rows[2]['done'], 'size': rows[2]['size'], 'speed': 18.6 * 1024**2, 'message': 3, 'summary': summary})
        self.on_event('transfers', {'active': 4, 'speed': 18.6 * 1024**2, 'summary': summary})
        self.on_event('verification', {'name': rows[1]['name'], 'detail': 'Checking archive without a password: attempt 2/3…'})
        self.log('Preview mode — sample data. No Telegram connection.')
        self.log('Last selected chat restored: Design Resources.')
        self.log('Checking Architectural materials pack.zip in the background. Downloads continue.')
        self.log('Downloading 4 archives in parallel. Partial files are saved automatically.')
        self.badge.setText('PREVIEW · SAMPLE DATA')
        self.footer.setText('Preview mode · Interface demonstration only · No real download is running')
