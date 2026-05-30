#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import subprocess, json, threading
from pathlib import Path
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

PIPER_DIR = Path.home() / ".local/share/piper"
REPO_RAW = "https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main"
BG, FG, ACC, BTN = "#1e1e2e", "#cdd6f4", "#89b4fa", "#313244"


# ── helpers ──────────────────────────────────────────────────────────────────

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

def download_model(model, on_done=None):
    def _dl():
        lang_region, rest = model.split("-", 1)
        quality = rest.rsplit("-", 1)[-1]
        voice   = rest.rsplit("-", 1)[0]
        lang    = lang_region.split("_")[0]
        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{lang_region}/{voice}/{quality}/{model}"
        try:
            subprocess.run(["curl", "-fL", f"{base}.onnx",      "-o", str(PIPER_DIR / f"{model}.onnx")],  check=True, capture_output=True)
            subprocess.run(["curl", "-fL", f"{base}.onnx.json", "-o", str(PIPER_DIR / f"{model}.onnx.json")], check=True, capture_output=True)
            if on_done: on_done(True, model)
        except subprocess.CalledProcessError:
            (PIPER_DIR / f"{model}.onnx").unlink(missing_ok=True)
            (PIPER_DIR / f"{model}.onnx.json").unlink(missing_ok=True)
            if on_done: on_done(False, model)
    threading.Thread(target=_dl, daemon=True).start()

def make_tray_icon():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        theme = Gtk.IconTheme.get_default()
        pixbuf = theme.load_icon("audio-headset", 64, 0)
        data = pixbuf.get_pixels()
        w, h = pixbuf.get_width(), pixbuf.get_height()
        mode = "RGBA" if pixbuf.get_has_alpha() else "RGB"
        return Image.frombytes(mode, (w, h), data, "raw", mode, pixbuf.get_rowstride())
    except Exception:
        pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([20, 4, 44, 36], fill="#89b4fa")
    d.rectangle([28, 36, 36, 48], fill="#89b4fa")
    d.arc([16, 30, 48, 54], 0, 180, fill="#89b4fa", width=4)
    d.line([32, 54, 32, 60], fill="#89b4fa", width=4)
    d.line([22, 60, 42, 60], fill="#89b4fa", width=4)
    return img


# ── speak popup ──────────────────────────────────────────────────────────────

class SpeakDialog(tk.Toplevel):
    def __init__(self, parent=None):
        if parent:
            super().__init__(parent)
        else:
            self._root = tk.Tk()
            self._root.withdraw()
            super().__init__(self._root)
        self.title("Say")
        self.configure(bg=BG, padx=12, pady=10)
        self.resizable(False, False)
        self.attributes("-topmost", True)

        tk.Label(self, text="Say:", bg=BG, fg=FG).pack(side="left")
        self.entry = tk.Entry(self, width=34, bg=BTN, fg=FG, insertbackground=FG, relief="flat")
        self.entry.pack(side="left", padx=6)
        self.entry.focus()
        self.entry.bind("<Return>", self._say)
        self.entry.bind("<Escape>", lambda _: self.destroy())

        self.mic_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self, text="mic", variable=self.mic_var, bg=BG, fg=FG,
                       selectcolor=BTN, activebackground=BG, relief="flat").pack(side="left")
        tk.Button(self, text="Say", command=self._say, bg=BTN, fg=FG, relief="flat",
                  activebackground="#45475a").pack(side="left", padx=(4,0))

    def _say(self, *_):
        text = self.entry.get().strip()
        if text:
            threading.Thread(target=speak_text, args=(text, self.mic_var.get()), daemon=True).start()
        self.destroy()


# ── main window ──────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TTS Manager")
        self.resizable(False, False)
        self.configure(padx=12, pady=12, bg=BG)
        self._tray = None
        self._build()
        self.refresh()
        if HAS_TRAY:
            self._start_tray()
            self.protocol("WM_DELETE_WINDOW", self.withdraw)
        else:
            self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TLabel",    background=BG, foreground=FG)
        style.configure("TButton",   background=BTN, foreground=FG, relief="flat", padding=4)
        style.map("TButton",         background=[("active", "#45475a")])
        style.configure("TEntry",    fieldbackground=BTN, foreground=FG, insertcolor=FG)
        style.configure("TFrame",    background=BG)
        style.configure("TCheckbutton", background=BG, foreground=FG)

        ttk.Label(self, text="Active:", foreground=ACC).grid(row=0, column=0, sticky="w")
        self.active_var = tk.StringVar(value="none")
        ttk.Label(self, textvariable=self.active_var).grid(row=0, column=1, columnspan=4, sticky="w", padx=(6,0))

        ttk.Label(self, text="Say:").grid(row=1, column=0, sticky="w", pady=(10,0))
        self.speak_entry = ttk.Entry(self, width=34)
        self.speak_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(10,0), padx=(6,4))
        self.speak_entry.bind("<Return>", lambda _: self._speak())
        self.mic_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self, text="mic", variable=self.mic_var, bg=BG, fg=FG,
                       selectcolor=BTN, activebackground=BG, relief="flat").grid(row=1, column=4, pady=(10,0))
        ttk.Button(self, text="Say", command=self._speak).grid(row=1, column=5, pady=(10,0), padx=(4,0))

        ttk.Label(self, text="Installed models:").grid(row=2, column=0, columnspan=6, sticky="w", pady=(10,4))
        frame = ttk.Frame(self)
        frame.grid(row=3, column=0, columnspan=6, sticky="nsew")
        self.listbox = tk.Listbox(frame, height=8, width=46, bg=BTN, fg=FG,
                                  selectbackground=ACC, selectforeground=BG,
                                  relief="flat", highlightthickness=0, font=("monospace", 10))
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both")
        sb.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda _: self._switch())

        bf = ttk.Frame(self)
        bf.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(8,0))
        for i, (lbl, cmd) in enumerate([
            ("Switch",   self._switch),
            ("Test",     self._test),
            ("Download", self._download),
            ("Remove",   self._remove),
            ("Update",   self._update),
        ]):
            ttk.Button(bf, text=lbl, command=cmd).grid(row=0, column=i, padx=(0,4))

        self.status_var = tk.StringVar(value="ready")
        ttk.Label(self, textvariable=self.status_var, foreground="#6c7086").grid(
            row=5, column=0, columnspan=6, sticky="w", pady=(8,0))

    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Say...", lambda: self.after(0, self._tray_speak), default=True),
            pystray.MenuItem("Open",  lambda: self.after(0, self._show_window)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",  lambda: self.after(0, self._quit)),
        )
        self._tray = pystray.Icon("tts", make_tray_icon(), "TTS Manager", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_speak(self):
        SpeakDialog(self)

    def _quit(self):
        if self._tray:
            self._tray.stop()
        self.destroy()

    def refresh(self):
        active = get_active()
        rate = get_rate(active) if active else "?"
        self.active_var.set(f"{active}  ({rate} Hz)" if active else "none")
        models = get_models()
        self.listbox.delete(0, tk.END)
        for m in models:
            self.listbox.insert(tk.END, f"{'* ' if m == active else '  '}{m}")
        if active and active in models:
            idx = models.index(active)
            self.listbox.selection_set(idx)
            self.listbox.see(idx)

    def _selected(self):
        sel = self.listbox.curselection()
        return self.listbox.get(sel[0]).strip().lstrip("* ") if sel else None

    def _speak(self):
        text = self.speak_entry.get().strip()
        if not text:
            return
        self.speak_entry.delete(0, tk.END)
        self.status_var.set(f"speaking...")
        threading.Thread(target=speak_text, args=(text, self.mic_var.get()), daemon=True).start()
        self.after(2000, lambda: self.status_var.set("ready"))

    def _switch(self):
        m = self._selected()
        if not m: return
        switch_model(m)
        self.refresh()
        self.status_var.set(f"switched to {m}")

    def _test(self):
        m = self._selected()
        if not m: return
        old = get_active()
        switch_model(m)
        self.status_var.set(f"testing {m}...")
        def _run():
            speak_text(m, to_mic=False)
            if old: switch_model(old)
            self.after(0, lambda: (self.refresh(), self.status_var.set("ready")))
        threading.Thread(target=_run, daemon=True).start()

    def _download(self):
        m = simpledialog.askstring("Download", "Model name (e.g. en_GB-alan-medium):", parent=self)
        if not m: return
        m = m.strip()
        self.status_var.set(f"downloading {m}...")
        def done(ok, name):
            self.after(0, lambda: (
                self.refresh(),
                self.status_var.set(f"downloaded {name}" if ok else f"FAILED: {name}")
            ))
        download_model(m, on_done=done)

    def _remove(self):
        m = self._selected()
        if not m: return
        if m == get_active():
            messagebox.showerror("Remove", "Can't remove active model - switch first", parent=self)
            return
        if messagebox.askyesno("Remove", f"Delete {m}?", parent=self):
            (PIPER_DIR / f"{m}.onnx").unlink(missing_ok=True)
            (PIPER_DIR / f"{m}.onnx.json").unlink(missing_ok=True)
            self.refresh()
            self.status_var.set(f"removed {m}")

    def _update(self):
        self.status_var.set("updating...")
        def _run():
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
            self.after(0, lambda: self.status_var.set("updated" if ok else "update failed"))
        threading.Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    import sys
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1 and sys.argv[1] == "--speak":
        root = tk.Tk()
        root.withdraw()
        d = SpeakDialog(root)
        root.wait_window(d)
    else:
        App().mainloop()
