#!/usr/bin/env python3
import sys, subprocess, json, threading
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QCheckBox, QDialog, QSystemTrayIcon, QMenu, QMessageBox,
    QInputDialog
)
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import Qt, QThread, pyqtSignal

PIPER_DIR      = Path.home() / ".local/share/piper"
FAVORITES_FILE = PIPER_DIR / "favorites.json"
REPO_RAW = "https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main"


# ── helpers ───────────────────────────────────────────────────────────────────

def get_active():
    try:
        return Path((PIPER_DIR / "active_model").read_text().strip()).stem
    except:
        return None

def get_models():
    return sorted(f.stem for f in PIPER_DIR.glob("*.onnx"))

def get_favorites():
    try:
        return set(json.loads(FAVORITES_FILE.read_text()))
    except:
        return set()

def set_favorites(favs):
    FAVORITES_FILE.write_text(json.dumps(sorted(favs)))

def get_rate(model):
    try:
        return json.loads((PIPER_DIR / f"{model}.onnx.json").read_text())["audio"]["sample_rate"]
    except:
        return 22050

def speak_text(text, to_mic=True):
    active = get_active()
    if not active:
        return
    model = str(PIPER_DIR / f"{active}.onnx")
    rate = get_rate(active)
    piper = subprocess.Popen(["piper-tts", "--model", model, "--output_raw"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if to_mic:
        sink = subprocess.Popen(["bash", "-c",
            f"tee >(pacat --device=tts_sink --volume=65536 --format=s16le --rate={rate} --channels=1)"
            f" | pacat --volume=65536 --format=s16le --rate={rate} --channels=1"],
            stdin=piper.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        sink = subprocess.Popen(["pacat", "--volume=65536", "--format=s16le", f"--rate={rate}", "--channels=1"],
                                stdin=piper.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    piper.stdout.close()
    piper.stdin.write(text.encode())
    piper.stdin.close()
    sink.wait()

def switch_model(model):
    (PIPER_DIR / "active_model").write_text(str(PIPER_DIR / f"{model}.onnx"))
    (PIPER_DIR / "active_rate").write_text(str(get_rate(model)))


# ── worker threads ────────────────────────────────────────────────────────────────

class SpeakWorker(QThread):
    def __init__(self, text, to_mic):
        super().__init__()
        self.text, self.to_mic = text, to_mic
    def run(self):
        speak_text(self.text, self.to_mic)

class DownloadWorker(QThread):
    done = pyqtSignal(bool, str)
    def __init__(self, model):
        super().__init__()
        self.model = model
    def run(self):
        m = self.model
        lang_region, rest = m.split("-", 1)
        quality = rest.rsplit("-", 1)[-1]
        voice   = rest.rsplit("-", 1)[0]
        lang    = lang_region.split("_")[0]
        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{lang_region}/{voice}/{quality}/{m}"
        try:
            subprocess.run(["curl", "-fL", f"{base}.onnx",      "-o", str(PIPER_DIR / f"{m}.onnx")],  check=True, capture_output=True)
            subprocess.run(["curl", "-fL", f"{base}.onnx.json", "-o", str(PIPER_DIR / f"{m}.onnx.json")], check=True, capture_output=True)
            self.done.emit(True, m)
        except subprocess.CalledProcessError:
            (PIPER_DIR / f"{m}.onnx").unlink(missing_ok=True)
            (PIPER_DIR / f"{m}.onnx.json").unlink(missing_ok=True)
            self.done.emit(False, m)

class UpdateWorker(QThread):
    done = pyqtSignal(bool)
    def run(self):
        files = [("tts-manage.sh","tts-manage"),("tts-speak.sh","tts-speak"),
                 ("tts-mic-init.sh","tts-mic-init"),("tts-gui.py","tts-gui")]
        bin_dir = Path.home() / ".local/bin"
        ok = True
        for src, dst in files:
            try:
                subprocess.run(["curl", "-fL", f"{REPO_RAW}/{src}", "-o", str(bin_dir/dst)],
                               check=True, capture_output=True)
                (bin_dir/dst).chmod(0o755)
            except subprocess.CalledProcessError:
                ok = False
        self.done.emit(ok)


# ── speak dialog ──────────────────────────────────────────────────────────────

class SpeakDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TTS")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._worker = None

        row = QHBoxLayout()
        row.addWidget(QLabel("Say:"))
        self.edit = QLineEdit()
        self.edit.setMinimumWidth(300)
        row.addWidget(self.edit)
        self.mic = QCheckBox("mic")
        self.mic.setChecked(True)
        row.addWidget(self.mic)
        btn = QPushButton("Say")
        btn.clicked.connect(self._say)
        row.addWidget(btn)

        self.setLayout(row)
        self.edit.returnPressed.connect(self._say)
        self.edit.setFocus()

    def _say(self):
        text = self.edit.text().strip()
        if text:
            self._worker = SpeakWorker(text, self.mic.isChecked())
            self._worker.start()
        self.accept()


# ── main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TTS Manager")
        self.setWindowIcon(QIcon.fromTheme("audio-headset"))
        self._workers = []
        self._build()
        self._setup_tray()
        self.refresh()

    def _build(self):
        w = QWidget()
        self.setCentralWidget(w)
        layout = QVBoxLayout(w)

        self.active_label = QLabel("Active: none")
        layout.addWidget(self.active_label)

        speak_row = QHBoxLayout()
        speak_row.addWidget(QLabel("Say:"))
        self.speak_edit = QLineEdit()
        self.speak_edit.returnPressed.connect(self._speak)
        speak_row.addWidget(self.speak_edit)
        self.mic_check = QCheckBox("mic")
        self.mic_check.setChecked(True)
        speak_row.addWidget(self.mic_check)
        say_btn = QPushButton("Say")
        say_btn.clicked.connect(self._speak)
        speak_row.addWidget(say_btn)
        layout.addLayout(speak_row)

        layout.addWidget(QLabel("Installed models:"))
        self.model_list = QListWidget()
        self.model_list.setMinimumHeight(200)
        self.model_list.itemDoubleClicked.connect(self._switch)
        self.model_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.model_list.customContextMenuRequested.connect(self._model_context_menu)
        layout.addWidget(self.model_list)

        btn_row = QHBoxLayout()
        for label, slot in [
            ("Switch",   self._switch),
            ("Test",     self._test),
            ("Download", self._download),
            ("Remove",   self._remove),
            ("Update",   self._update),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self.status = QLabel("ready")
        layout.addWidget(self.status)

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(QIcon.fromTheme("audio-headset"), self)
        menu = QMenu()
        say_action = QAction("Say...", self)
        say_action.triggered.connect(self._tray_speak)
        open_action = QAction("Open", self)
        open_action.triggered.connect(self._show_window)
        reload_action = QAction("Reload models", self)
        reload_action.triggered.connect(self.refresh)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(say_action)
        menu.addAction(open_action)
        menu.addAction(reload_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def _show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._tray_speak()

    def _tray_speak(self):
        d = SpeakDialog(self)
        d.exec()

    def refresh(self):
        active = get_active()
        rate = get_rate(active) if active else "?"
        self.active_label.setText(f"Active: {active}  ({rate} Hz)" if active else "Active: none")
        models = get_models()
        favorites = get_favorites()
        favs   = sorted(m for m in models if m in favorites)
        others = sorted(m for m in models if m not in favorites)
        self.model_list.clear()
        for m in favs + others:
            prefix = ("★ " if m in favorites else "  ") + ("* " if m == active else "  ")
            item = QListWidgetItem(prefix + m)
            item.setData(Qt.ItemDataRole.UserRole, m)
            self.model_list.addItem(item)
            if m == active:
                self.model_list.setCurrentItem(item)

    def _selected(self):
        item = self.model_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _model_context_menu(self, pos):
        m = self._selected()
        if not m:
            return
        favs = get_favorites()
        menu = QMenu(self)
        label = "Remove from favorites" if m in favs else "Add to favorites"
        action = QAction(label, self)
        action.triggered.connect(lambda: self._toggle_favorite(m))
        menu.addAction(action)
        menu.exec(self.model_list.mapToGlobal(pos))

    def _toggle_favorite(self, model):
        favs = get_favorites()
        if model in favs:
            favs.discard(model)
        else:
            favs.add(model)
        set_favorites(favs)
        self.refresh()

    def _speak(self):
        text = self.speak_edit.text().strip()
        if not text:
            return
        self.speak_edit.clear()
        self.status.setText("speaking...")
        w = SpeakWorker(text, self.mic_check.isChecked())
        w.finished.connect(lambda: self.status.setText("ready"))
        self._workers.append(w)
        w.start()

    def _switch(self):
        m = self._selected()
        if not m:
            return
        switch_model(m)
        self.refresh()
        self.status.setText(f"switched to {m}")

    def _test(self):
        m = self._selected()
        if not m:
            return
        old = get_active()
        switch_model(m)
        self.status.setText(f"testing {m}...")
        def _run():
            speak_text(m, to_mic=False)
            if old:
                switch_model(old)
        w = QThread()
        w.run = _run
        w.finished.connect(lambda: (self.refresh(), self.status.setText("ready")))
        self._workers.append(w)
        w.start()

    def _download(self):
        m, ok = QInputDialog.getText(self, "Download", "Model name (e.g. en_GB-alan-medium):")
        if not ok or not m.strip():
            return
        m = m.strip()
        self.status.setText(f"downloading {m}...")
        w = DownloadWorker(m)
        def _done(success, name):
            self.refresh()
            self.status.setText(f"downloaded {name}" if success else f"FAILED: {name}")
        w.done.connect(_done)
        self._workers.append(w)
        w.start()

    def _remove(self):
        m = self._selected()
        if not m:
            return
        if m == get_active():
            QMessageBox.warning(self, "Remove", "Can't remove active model - switch first")
            return
        if QMessageBox.question(self, "Remove", f"Delete {m}?") == QMessageBox.StandardButton.Yes:
            (PIPER_DIR / f"{m}.onnx").unlink(missing_ok=True)
            (PIPER_DIR / f"{m}.onnx.json").unlink(missing_ok=True)
            self.refresh()
            self.status.setText(f"removed {m}")

    def _update(self):
        self.status.setText("updating...")
        w = UpdateWorker()
        w.done.connect(lambda ok: self.status.setText("updated" if ok else "update failed"))
        self._workers.append(w)
        w.start()


if __name__ == "__main__":
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if len(sys.argv) > 1 and sys.argv[1] == "--speak":
        d = SpeakDialog()
        d.exec()
        sys.exit(0)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())
