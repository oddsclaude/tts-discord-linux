#!/usr/bin/env python3
import sys, subprocess, json, threading, time
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QCheckBox, QDialog, QSystemTrayIcon, QMenu, QMessageBox,
    QInputDialog
)
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QMetaObject, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage, QDBusObjectPath, QDBusVariant, QDBus

PIPER_DIR = Path.home() / ".local/share/piper"
REPO_RAW = "https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main"


# ── helpers ───────────────────────────────────────────────────────────────────────────

def get_active():
    try:
        return Path((PIPER_DIR / "active_model").read_text().strip()).stem
    except:
        return None

def get_models():
    return sorted(f.stem for f in PIPER_DIR.glob("*.onnx"))

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


# ── worker threads ────────────────────────────────────────────────────────────────────────────

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


# ── XDG global shortcuts portal ──────────────────────────────────────────────────────────────────

class XDGShortcutManager(QObject):
    triggered = pyqtSignal()

    _DEST  = "org.freedesktop.portal.Desktop"
    _PATH  = "/org/freedesktop/portal/desktop"
    _IFACE = "org.freedesktop.portal.GlobalShortcuts"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus = QDBusConnection.sessionBus()
        self._session_path = None
        ts = str(int(time.time()))
        self._htok = f"tts_h{ts}"
        self._stok = f"tts_s{ts}"

    def start(self):
        iface = QDBusInterface(self._DEST, self._PATH, self._IFACE, self._bus)
        if not iface.isValid():
            return False
        sender = self._bus.baseService()[1:].replace(".", "_")
        req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{self._htok}"
        self._bus.connect(self._DEST, req_path, "org.freedesktop.portal.Request",
                          "Response", self._on_session)
        msg = QDBusMessage.createMethodCall(self._DEST, self._PATH, self._IFACE, "CreateSession")
        msg.setArguments([{
            "handle_token":         QDBusVariant("s", self._htok),
            "session_handle_token": QDBusVariant("s", self._stok),
        }])
        self._bus.call(msg, QDBus.CallMode.NoBlock)
        return True

    @pyqtSlot("uint", "QVariantMap")
    def _on_session(self, response, results):
        if response != 0:
            return
        self._session_path = results.get("session_handle", "")
        btok = f"tts_b{int(time.time())}"
        self._bus.connect(self._DEST, self._session_path, self._IFACE,
                          "Activated", self._on_activated)
        msg = QDBusMessage.createMethodCall(self._DEST, self._PATH, self._IFACE, "BindShortcuts")
        msg.setArguments([
            QDBusObjectPath(self._session_path),
            [("tts-speak", {
                "description":       QDBusVariant("s", "TTS Speak"),
                "preferred_trigger": QDBusVariant("s", "CTRL+PRINT"),
            })],
            "",
            {"handle_token": QDBusVariant("s", btok)},
        ])
        self._bus.call(msg, QDBus.CallMode.NoBlock)

    @pyqtSlot("QDBusObjectPath", str, "qulonglong", "QVariantMap")
    def _on_activated(self, session_handle, shortcut_id, timestamp, options):
        if shortcut_id == "tts-speak":
            self.triggered.emit()

    def stop(self):
        if self._session_path:
            iface = QDBusInterface(self._DEST, self._session_path,
                                   "org.freedesktop.portal.Session", self._bus)
            iface.call("Close")
            self._session_path = None


# ── speak dialog ───────────────────────────────────────────────────────────────────────────────

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


# ── main window ───────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TTS Manager")
        self.setWindowIcon(QIcon.fromTheme("audio-headset"))
        self._workers = []
        self._build()
        self._setup_tray()
        self.refresh()
        self._start_pipewire()
        self._register_shortcut()

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
        layout.addWidget(self.model_list)

        btn_row = QHBoxLayout()
        for label, slot in [
            ("Switch", self._switch),
            ("Test",   self._test),
            ("Download", self._download),
            ("Remove", self._remove),
            ("Update", self._update),
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
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(say_action)
        menu.addAction(open_action)
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
        self.model_list.clear()
        for m in models:
            item = QListWidgetItem(("* " if m == active else "  ") + m)
            self.model_list.addItem(item)
            if m == active:
                self.model_list.setCurrentItem(item)

    def _selected(self):
        item = self.model_list.currentItem()
        return item.text().strip().lstrip("* ") if item else None

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

    def _start_pipewire(self):
        mic_init = Path.home() / ".local/bin/tts-mic-init"
        if mic_init.exists():
            subprocess.Popen([str(mic_init)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _stop_pipewire(self):
        try:
            result = subprocess.run(["pactl", "list", "short", "modules"],
                                    capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if "tts_sink" in line or "tts_mic" in line:
                    mod_id = line.split()[0]
                    subprocess.run(["pactl", "unload-module", mod_id],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _register_shortcut(self):
        self._shortcut_mgr = XDGShortcutManager(self)
        self._shortcut_mgr.triggered.connect(self._tray_speak)
        if not self._shortcut_mgr.start():
            self._register_kde_shortcut()

    def _register_kde_shortcut(self):
        cfg = Path.home() / ".config/kglobalshortcutsrc"
        try:
            lines = cfg.read_text().splitlines() if cfg.exists() else []
            section = "[services][net.local.tts-speak.desktop]"
            new_lines = []
            in_section = False
            for line in lines:
                if line.strip() == section:
                    in_section = True
                    continue
                if in_section and line.startswith("["):
                    in_section = False
                if not in_section:
                    new_lines.append(line)
            new_lines += [section, "_k_friendly_name=TTS Speak",
                          "tts-speak=Ctrl+Print,none,TTS Speak", ""]
            cfg.write_text("\n".join(new_lines) + "\n")
        except Exception:
            pass
        qdbus = "qdbus6" if subprocess.run(["which", "qdbus6"], capture_output=True).returncode == 0 else "qdbus"
        subprocess.Popen([qdbus, "org.kde.kglobalaccel", "/kglobalaccel",
                          "org.kde.KGlobalAccel.loadComponent", "net.local.tts-speak.desktop"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _unregister_shortcut(self):
        if hasattr(self, "_shortcut_mgr"):
            self._shortcut_mgr.stop()
        cfg = Path.home() / ".config/kglobalshortcutsrc"
        try:
            if cfg.exists():
                lines = cfg.read_text().splitlines()
                section = "[services][net.local.tts-speak.desktop]"
                new_lines, in_section = [], False
                for line in lines:
                    if line.strip() == section:
                        in_section = True
                        continue
                    if in_section and line.startswith("["):
                        in_section = False
                    if not in_section:
                        new_lines.append(line)
                cfg.write_text("\n".join(new_lines) + "\n")
        except Exception:
            pass
        qdbus = "qdbus6" if subprocess.run(["which", "qdbus6"], capture_output=True).returncode == 0 else "qdbus"
        subprocess.Popen([qdbus, "org.kde.kglobalaccel", "/kglobalaccel",
                          "org.kde.KGlobalAccel.unloadComponent", "net.local.tts-speak.desktop"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _quit_app(self):
        self._unregister_shortcut()
        self._stop_pipewire()
        QApplication.quit()


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
