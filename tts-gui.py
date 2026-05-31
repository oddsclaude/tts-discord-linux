#!/usr/bin/env python3
import sys, subprocess, json, threading, tarfile, zipfile, tempfile, shutil
from pathlib import Path
from urllib.request import urlopen, Request
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QCheckBox, QDialog, QSystemTrayIcon, QMenu, QMessageBox,
    QInputDialog, QComboBox, QFrame, QGroupBox
)
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import Qt, QThread, pyqtSignal

PIPER_DIR      = Path.home() / ".local/share/piper"
FAVORITES_FILE = PIPER_DIR / "favorites.json"
REPO_RAW = "https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main"


# ── helpers ─────────────────────────────────────────────

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


# ── worker threads ────────────────────────────────────────────────

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
            subprocess.run(["curl", "-fL", "--max-time", "300", f"{base}.onnx",      "-o", str(PIPER_DIR / f"{m}.onnx")],  check=True, capture_output=True)
            subprocess.run(["curl", "-fL", "--max-time", "60",  f"{base}.onnx.json", "-o", str(PIPER_DIR / f"{m}.onnx.json")], check=True, capture_output=True)
            self.done.emit(True, m)
        except subprocess.CalledProcessError:
            (PIPER_DIR / f"{m}.onnx").unlink(missing_ok=True)
            (PIPER_DIR / f"{m}.onnx.json").unlink(missing_ok=True)
            self.done.emit(False, m)

class WideVideoDownloadWorker(QThread):
    """Downloads a voice model from wide-video/piper-voices-v1.0.0."""
    done = pyqtSignal(bool, str)
    BASE = "https://huggingface.co/wide-video/piper-voices-v1.0.0/resolve/main"

    def __init__(self, model):
        super().__init__()
        self.model = model

    def run(self):
        m = self.model
        try:
            lang_region, rest = m.split("-", 1)
            quality = rest.rsplit("-", 1)[-1]
            voice   = rest.rsplit("-", 1)[0]
            lang    = lang_region.split("_")[0]
        except ValueError:
            self.done.emit(False, m)
            return
        base = f"{self.BASE}/{lang}/{lang_region}/{voice}/{quality}/{m}"
        try:
            subprocess.run(["curl", "-fL", "--max-time", "300", f"{base}.onnx",      "-o", str(PIPER_DIR / f"{m}.onnx")],  check=True, capture_output=True)
            subprocess.run(["curl", "-fL", "--max-time", "60",  f"{base}.onnx.json", "-o", str(PIPER_DIR / f"{m}.onnx.json")], check=True, capture_output=True)
            self.done.emit(True, m)
        except subprocess.CalledProcessError:
            (PIPER_DIR / f"{m}.onnx").unlink(missing_ok=True)
            (PIPER_DIR / f"{m}.onnx.json").unlink(missing_ok=True)
            self.done.emit(False, m)

class GladosWorker(QThread):
    # Signal: (success, error_message) - error_message is "" on success
    done = pyqtSignal(bool, str)

    ONNX_URL  = "https://github.com/dnhkng/GlaDOS/releases/download/0.1/glados.onnx"
    JSON_URL1 = "https://github.com/dnhkng/GlaDOS/releases/download/0.1/glados.onnx.json"
    JSON_URL2 = "https://raw.githubusercontent.com/dnhkng/GlaDOS/main/src/glados/glados.onnx.json"

    def run(self):
        onnx_dest = PIPER_DIR / "glados.onnx"
        json_dest = PIPER_DIR / "glados.onnx.json"
        try:
            subprocess.run(["curl", "-fL", "--max-time", "300", self.ONNX_URL, "-o", str(onnx_dest)],
                           check=True, capture_output=True)
            # Try primary JSON URL; fall back to secondary if it fails (e.g. 404)
            result = subprocess.run(["curl", "-fL", "--max-time", "60", self.JSON_URL1, "-o", str(json_dest)],
                                    capture_output=True)
            if result.returncode != 0:
                r2 = subprocess.run(["curl", "-fL", "--max-time", "60", self.JSON_URL2, "-o", str(json_dest)],
                                    capture_output=True)
                if r2.returncode != 0:
                    err = r2.stderr.decode(errors="replace")[:200]
                    onnx_dest.unlink(missing_ok=True)
                    json_dest.unlink(missing_ok=True)
                    self.done.emit(False, err)
                    return
            self.done.emit(True, "")
        except subprocess.CalledProcessError as e:
            onnx_dest.unlink(missing_ok=True)
            json_dest.unlink(missing_ok=True)
            err = e.stderr.decode(errors="replace")[:200] if e.stderr else "unknown error"
            self.done.emit(False, err)

class Hal9000Worker(QThread):
    # Signal: (success, error_message)
    done = pyqtSignal(bool, str)
    ONNX_URL = "https://huggingface.co/campwill/HAL-9000-Piper-TTS/resolve/main/hal.onnx"
    JSON_URL = "https://huggingface.co/campwill/HAL-9000-Piper-TTS/resolve/main/hal.onnx.json"
    STEM = "hal9000"

    def run(self):
        onnx_dest = PIPER_DIR / f"{self.STEM}.onnx"
        json_dest = PIPER_DIR / f"{self.STEM}.onnx.json"
        try:
            subprocess.run(["curl", "-fL", "--max-time", "300", self.ONNX_URL, "-o", str(onnx_dest)],
                           check=True, capture_output=True)
            subprocess.run(["curl", "-fL", "--max-time", "60",  self.JSON_URL, "-o", str(json_dest)],
                           check=True, capture_output=True)
            self.done.emit(True, "")
        except subprocess.CalledProcessError as e:
            onnx_dest.unlink(missing_ok=True)
            json_dest.unlink(missing_ok=True)
            err = e.stderr.decode(errors="replace")[:200] if e.stderr else "unknown error"
            self.done.emit(False, err)

class TrumpWorker(QThread):
    """Downloads Trump voice from BibEBobberson/Piper (tar.gz archive, extracts .onnx files)."""
    # Signal: (success, error_message)
    done = pyqtSignal(bool, str)
    ARCHIVE_URL = "https://huggingface.co/BibEBobberson/Piper/resolve/main/Donald%20Trump.tar.gz"
    STEM = "trump"

    def run(self):
        onnx_dest = PIPER_DIR / f"{self.STEM}.onnx"
        json_dest = PIPER_DIR / f"{self.STEM}.onnx.json"
        tmp_archive = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tf:
                tmp_archive = Path(tf.name)
            subprocess.run(
                ["curl", "-fL", "--max-time", "300", self.ARCHIVE_URL, "-o", str(tmp_archive)],
                check=True, capture_output=True
            )
            # Extract .onnx and .onnx.json from the archive
            with tarfile.open(tmp_archive, "r:gz") as tar:
                members = tar.getmembers()
                onnx_members = [m for m in members if m.name.endswith(".onnx") and not m.name.endswith(".onnx.json")]
                json_members = [m for m in members if m.name.endswith(".onnx.json")]
                if not onnx_members:
                    raise RuntimeError("No .onnx file found in archive")
                onnx_member = onnx_members[0]
                onnx_member.name = onnx_dest.name
                tar.extract(onnx_member, path=PIPER_DIR)
                if json_members:
                    json_member = json_members[0]
                    json_member.name = json_dest.name
                    tar.extract(json_member, path=PIPER_DIR)
            self.done.emit(True, "")
        except subprocess.CalledProcessError as e:
            onnx_dest.unlink(missing_ok=True)
            json_dest.unlink(missing_ok=True)
            err = e.stderr.decode(errors="replace")[:200] if e.stderr else "curl failed"
            self.done.emit(False, err)
        except (tarfile.TarError, RuntimeError, OSError) as e:
            onnx_dest.unlink(missing_ok=True)
            json_dest.unlink(missing_ok=True)
            self.done.emit(False, str(e)[:200])
        finally:
            if tmp_archive and tmp_archive.exists():
                tmp_archive.unlink(missing_ok=True)

class HomerWorker(QThread):
    """Downloads Homer voice from BibEBobberson/Piper (zip archive, extracts .onnx files)."""
    # Signal: (success, error_message)
    done = pyqtSignal(bool, str)
    ARCHIVE_URL = "https://huggingface.co/BibEBobberson/Piper/resolve/main/Homer.zip"
    STEM = "homer"

    def run(self):
        onnx_dest = PIPER_DIR / f"{self.STEM}.onnx"
        json_dest = PIPER_DIR / f"{self.STEM}.onnx.json"
        tmp_archive = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
                tmp_archive = Path(tf.name)
            subprocess.run(
                ["curl", "-fL", "--max-time", "300", self.ARCHIVE_URL, "-o", str(tmp_archive)],
                check=True, capture_output=True
            )
            # Extract .onnx and .onnx.json from the zip
            with zipfile.ZipFile(tmp_archive, "r") as zf:
                names = zf.namelist()
                onnx_names = [n for n in names if n.endswith(".onnx") and not n.endswith(".onnx.json")]
                json_names = [n for n in names if n.endswith(".onnx.json")]
                if not onnx_names:
                    raise RuntimeError("No .onnx file found in archive")
                with zf.open(onnx_names[0]) as src, open(onnx_dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if json_names:
                    with zf.open(json_names[0]) as src, open(json_dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            self.done.emit(True, "")
        except subprocess.CalledProcessError as e:
            onnx_dest.unlink(missing_ok=True)
            json_dest.unlink(missing_ok=True)
            err = e.stderr.decode(errors="replace")[:200] if e.stderr else "curl failed"
            self.done.emit(False, err)
        except (zipfile.BadZipFile, RuntimeError, OSError) as e:
            onnx_dest.unlink(missing_ok=True)
            json_dest.unlink(missing_ok=True)
            self.done.emit(False, str(e)[:200])
        finally:
            if tmp_archive and tmp_archive.exists():
                tmp_archive.unlink(missing_ok=True)

class VoicesWorker(QThread):
    done = pyqtSignal(dict)

    VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"

    def run(self):
        try:
            req = Request(self.VOICES_URL, headers={"User-Agent": "tts-gui/1.0"})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            self.done.emit(data)
        except Exception:
            self.done.emit({})

class WideVideoVoicesWorker(QThread):
    done = pyqtSignal(dict)

    VOICES_URL = "https://huggingface.co/wide-video/piper-voices-v1.0.0/resolve/main/voices.json"

    def run(self):
        try:
            req = Request(self.VOICES_URL, headers={"User-Agent": "tts-gui/1.0"})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            self.done.emit(data)
        except Exception:
            self.done.emit({})

class DirectUrlWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        from urllib.parse import urlparse
        url = self.url
        parsed = urlparse(url)
        basename = Path(parsed.path).name
        # Strip .onnx extension to get the stem name
        stem = basename[:-5] if basename.endswith(".onnx") else basename
        onnx_url = url if url.endswith(".onnx") else url + ".onnx"
        json_url = onnx_url + ".json"
        onnx_dest = PIPER_DIR / f"{stem}.onnx"
        json_dest = PIPER_DIR / f"{stem}.onnx.json"
        try:
            subprocess.run(["curl", "-fL", "--max-time", "300", onnx_url, "-o", str(onnx_dest)],
                           check=True, capture_output=True)
            subprocess.run(["curl", "-fL", "--max-time", "60",  json_url, "-o", str(json_dest)],
                           check=True, capture_output=True)
            self.done.emit(True, stem)
        except subprocess.CalledProcessError:
            onnx_dest.unlink(missing_ok=True)
            json_dest.unlink(missing_ok=True)
            self.done.emit(False, stem)

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


# ── speak dialog ────────────────────────────────────────────

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


# ── download dialog ─────────────────────────────────────────

def _hline():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class DownloadDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download Voice Model")
        self.setMinimumWidth(520)
        self._workers = []
        self._voices_data = {}
        self._wv_voices_data = {}

        layout = QVBoxLayout(self)

        # ── A) Official piper voices section ───────────────────────────────
        piper_box = QGroupBox("Official piper voices")
        piper_layout = QVBoxLayout(piper_box)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Loading...")
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        lang_row.addWidget(self.lang_combo)
        piper_layout.addLayout(lang_row)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Voice:"))
        self.voice_combo = QComboBox()
        voice_row.addWidget(self.voice_combo)
        piper_layout.addLayout(voice_row)

        piper_dl_btn = QPushButton("Download")
        piper_dl_btn.clicked.connect(self._download_piper)
        piper_layout.addWidget(piper_dl_btn)

        layout.addWidget(piper_box)

        # ── B) wide-video piper voices section ───────────────────────────
        wv_box = QGroupBox("wide-video piper voices")
        wv_layout = QVBoxLayout(wv_box)

        wv_lang_row = QHBoxLayout()
        wv_lang_row.addWidget(QLabel("Language:"))
        self.wv_lang_combo = QComboBox()
        self.wv_lang_combo.addItem("Loading...")
        self.wv_lang_combo.currentIndexChanged.connect(self._on_wv_lang_changed)
        wv_lang_row.addWidget(self.wv_lang_combo)
        wv_layout.addLayout(wv_lang_row)

        wv_voice_row = QHBoxLayout()
        wv_voice_row.addWidget(QLabel("Voice:"))
        self.wv_voice_combo = QComboBox()
        wv_voice_row.addWidget(self.wv_voice_combo)
        wv_layout.addLayout(wv_voice_row)

        wv_dl_btn = QPushButton("Download")
        wv_dl_btn.clicked.connect(self._download_wide_video)
        wv_layout.addWidget(wv_dl_btn)

        layout.addWidget(wv_box)

        # ── C) Custom URL / model name section ─────────────────────────
        custom_box = QGroupBox("Custom URL or model name")
        custom_layout = QVBoxLayout(custom_box)

        custom_row = QHBoxLayout()
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("HuggingFace .onnx URL or model name (e.g. en_GB-alan-medium)")
        custom_row.addWidget(self.custom_edit)
        custom_dl_btn = QPushButton("Download")
        custom_dl_btn.clicked.connect(self._download_custom)
        custom_row.addWidget(custom_dl_btn)
        custom_layout.addLayout(custom_row)

        layout.addWidget(custom_box)

        # ── D) Characters section ────────────────────────────────────
        chars_box = QGroupBox("Characters")
        chars_layout = QVBoxLayout(chars_box)

        glados_btn = QPushButton("Download GLaDOS")
        glados_btn.clicked.connect(self._download_glados)
        chars_layout.addWidget(glados_btn)

        hal_btn = QPushButton("Download HAL-9000")
        hal_btn.clicked.connect(self._download_hal9000)
        chars_layout.addWidget(hal_btn)

        trump_btn = QPushButton("Download Trump")
        trump_btn.clicked.connect(self._download_trump)
        chars_layout.addWidget(trump_btn)

        homer_btn = QPushButton("Download Homer")
        homer_btn.clicked.connect(self._download_homer)
        chars_layout.addWidget(homer_btn)

        layout.addWidget(chars_box)

        # ── Status + Close ───────────────────────────────────────────
        self.status_label = QLabel("Ready.")
        layout.addWidget(self.status_label)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        # Start fetching voices lists in background
        self._load_voices()
        self._load_wv_voices()

    # ── official piper voices ────────────────────────────────────────

    def _load_voices(self):
        w = VoicesWorker()
        w.done.connect(self._on_voices_loaded)
        self._workers.append(w)
        w.start()

    def _on_voices_loaded(self, data):
        self._voices_data = data
        self.lang_combo.clear()
        if not data:
            self.lang_combo.addItem("(failed to load)")
            self.status_label.setText("Failed to load voices list.")
            return
        langs = sorted(set(k.split("-")[0] for k in data.keys()))
        for lang in langs:
            self.lang_combo.addItem(lang)
        self.status_label.setText(f"Loaded {len(data)} voices.")

    def _on_lang_changed(self, index):
        self.voice_combo.clear()
        if not self._voices_data:
            return
        lang = self.lang_combo.currentText()
        if not lang or lang in ("Loading...", "(failed to load)"):
            return
        entries = []
        for key in self._voices_data.keys():
            parts = key.split("-")
            if len(parts) >= 3 and parts[0] == lang:
                name    = "-".join(parts[1:-1])
                quality = parts[-1]
                entries.append((f"{name} ({quality})", key))
        entries.sort(key=lambda x: x[0])
        for display, key in entries:
            self.voice_combo.addItem(display, userData=key)

    def _download_piper(self):
        key = self.voice_combo.currentData()
        if not key:
            self.status_label.setText("No voice selected.")
            return
        self.status_label.setText(f"Downloading {key}...")
        w = DownloadWorker(key)
        w.done.connect(lambda ok, name: self.status_label.setText(
            f"Downloaded {name}." if ok else f"FAILED to download {name}."
        ))
        self._workers.append(w)
        w.start()

    # ── wide-video piper voices ─────────────────────────────────────

    def _load_wv_voices(self):
        w = WideVideoVoicesWorker()
        w.done.connect(self._on_wv_voices_loaded)
        self._workers.append(w)
        w.start()

    def _on_wv_voices_loaded(self, data):
        self._wv_voices_data = data
        self.wv_lang_combo.clear()
        if not data:
            self.wv_lang_combo.addItem("(failed to load)")
            return
        langs = sorted(set(k.split("-")[0] for k in data.keys()))
        for lang in langs:
            self.wv_lang_combo.addItem(lang)

    def _on_wv_lang_changed(self, index):
        self.wv_voice_combo.clear()
        if not self._wv_voices_data:
            return
        lang = self.wv_lang_combo.currentText()
        if not lang or lang in ("Loading...", "(failed to load)"):
            return
        entries = []
        for key in self._wv_voices_data.keys():
            parts = key.split("-")
            if len(parts) >= 3 and parts[0] == lang:
                name    = "-".join(parts[1:-1])
                quality = parts[-1]
                entries.append((f"{name} ({quality})", key))
        entries.sort(key=lambda x: x[0])
        for display, key in entries:
            self.wv_voice_combo.addItem(display, userData=key)

    def _download_wide_video(self):
        key = self.wv_voice_combo.currentData()
        if not key:
            self.status_label.setText("No voice selected.")
            return
        self.status_label.setText(f"Downloading {key} (wide-video)...")
        w = WideVideoDownloadWorker(key)
        w.done.connect(lambda ok, name: self.status_label.setText(
            f"Downloaded {name}." if ok else f"FAILED to download {name}."
        ))
        self._workers.append(w)
        w.start()

    # ── custom URL ──────────────────────────────────────────────

    def _download_custom(self):
        text = self.custom_edit.text().strip()
        if not text:
            self.status_label.setText("Please enter a URL or model name.")
            return
        if text.startswith("http"):
            self.status_label.setText("Downloading from URL...")
            w = DirectUrlWorker(text)
            w.done.connect(lambda ok, name: self.status_label.setText(
                f"Downloaded {name}." if ok else f"FAILED to download {name}."
            ))
            self._workers.append(w)
            w.start()
        else:
            self.status_label.setText(f"Downloading {text}...")
            w = DownloadWorker(text)
            w.done.connect(lambda ok, name: self.status_label.setText(
                f"Downloaded {name}." if ok else f"FAILED to download {name}."
            ))
            self._workers.append(w)
            w.start()

    # ── characters ──────────────────────────────────────────────

    def _download_glados(self):
        self.status_label.setText("Downloading GLaDOS model (~50MB)...")
        w = GladosWorker()
        w.done.connect(lambda ok, err: self.status_label.setText(
            "GLaDOS downloaded successfully." if ok else f"GLaDOS download FAILED: {err}"
        ))
        self._workers.append(w)
        w.start()

    def _download_hal9000(self):
        self.status_label.setText("Downloading HAL-9000 model (~64MB)...")
        w = Hal9000Worker()
        w.done.connect(lambda ok, err: self.status_label.setText(
            "HAL-9000 downloaded successfully." if ok else f"HAL-9000 download FAILED: {err}"
        ))
        self._workers.append(w)
        w.start()

    def _download_trump(self):
        self.status_label.setText("Downloading Trump model (~106MB archive)...")
        w = TrumpWorker()
        w.done.connect(lambda ok, err: self.status_label.setText(
            "Trump downloaded successfully." if ok else f"Trump download FAILED: {err}"
        ))
        self._workers.append(w)
        w.start()

    def _download_homer(self):
        self.status_label.setText("Downloading Homer model (~223MB archive)...")
        w = HomerWorker()
        w.done.connect(lambda ok, err: self.status_label.setText(
            "Homer downloaded successfully." if ok else f"Homer download FAILED: {err}"
        ))
        self._workers.append(w)
        w.start()


# ── main window ─────────────────────────────────────────────

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
        restart_action = QAction("Restart", self)
        restart_action.triggered.connect(self._restart)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(say_action)
        menu.addAction(open_action)
        menu.addAction(reload_action)
        menu.addAction(restart_action)
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

    def _restart(self):
        subprocess.Popen([sys.executable] + sys.argv)
        QApplication.quit()

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
        # Use speak box text if non-empty; otherwise fall back to model name
        text = self.speak_edit.text().strip() or m
        old = get_active()
        switch_model(m)
        self.status.setText(f"testing {m}...")
        def _run():
            speak_text(text, to_mic=False)
            if old:
                switch_model(old)
        w = QThread()
        w.run = _run
        w.finished.connect(lambda: (self.refresh(), self.status.setText("ready")))
        self._workers.append(w)
        w.start()

    def _download(self):
        d = DownloadDialog(self)
        d.exec()
        self.refresh()

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
