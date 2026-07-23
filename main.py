"""
Desktop AI Companion — Agetha v5.0.2
Requires: pip install pillow pyautogui pytesseract numpy pygame requests pywin32 SpeechRecognition pyaudio
Assets: idle-1..3.gif, talking-1..3.gif, thinking.gif, sleeping.gif, happy.gif, surprised.gif,
        sad.gif, angry.gif, happy-static.gif, sad-static.gif, angry-static.gif, thinking-static.gif,
        loaf.gif, barrio.ttf (all in assets/ folder)
"""

AGETHA_VERSION = "5.0.2"
# Join the Discord — also linked in the TikTok bio and on the website below.
DISCORD_INVITE_URL = "https://discord.gg/agetha"
AGETHA_WEBSITE_URL = "https://chocolatebread.ddns.net/agetha.html"

import tkinter as tk
from tkinter import font as tkfont
import threading
import time
import random
import json
import math
import os
import platform
import webbrowser
from pathlib import Path
from PIL import Image, ImageTk, ImageSequence
import pygame

from ai_engine import AIEngine
from screen_reader import ScreenReader

import sys
BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
ASSETS   = BASE_DIR / "assets"
FONT_PATH = ASSETS / "barrio.ttf"


def native_error_popup(title: str, message: str) -> None:
    print(f"[ERROR] {title}: {message}")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10 | 0x1000)
        return
    except Exception:
        pass
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk(); _r.withdraw(); _r.attributes("-topmost", True)
        _mb.showerror(title, message, parent=_r); _r.destroy()
    except Exception:
        pass


WINDOW_W = 340
WINDOW_H = 560
GIF_W    = 340
GIF_H    = 300

SCREEN_POLL_INTERVAL_MS = 2 * 60 * 1000

BLEEP_TONES = {
    "neutral":   440,
    "happy":     523,
    "excited":   659,
    "sad":       294,
    "surprised": 587,
    "thinking":  370,
    "whisper":   220,
    "angry":     185,
}

W95_BG        = "#c0c0c0"
W95_TITLE_BG  = "#000080"
W95_TITLE_FG  = "#ffffff"
W95_TEXT      = "#000000"
W95_INPUT_BG  = "#ffffff"
W95_SHADOW    = "#808080"
W95_BTN_BG    = "#c0c0c0"
W95_BTN_ACT   = "#000080"
W95_BTN_AFG   = "#ffffff"
W95_FONT      = ("MS Sans Serif", 8)
W95_FONT_BOLD = ("MS Sans Serif", 8, "bold")

# ── Blacklisted usernames ──────────────────────────────────────────────────────
# Add any usernames that should not be allowed to run this application.
BLACKLISTED_USERNAMES: list[str] = [
    "janie"
]


def _safe_win_font(size: int = 8, bold: bool = False) -> tuple:
    weight = "bold" if bold else "normal"
    for family in ("MS Sans Serif", "Segoe UI", "Arial", "TkDefaultFont"):
        try:
            tkfont.Font(family=family, size=size, weight=weight)
            return (family, size, weight) if bold else (family, size)
        except Exception:
            continue
    return ("TkDefaultFont", size)


def _register_barrio_font():
    if not FONT_PATH.exists():
        print(f"[Font] barrio.ttf not found at {FONT_PATH}")
        return False
    try:
        import tkextrafont
        tkextrafont.load(str(FONT_PATH))
        print("[Font] Loaded barrio.ttf via tkextrafont")
        return True
    except (ImportError, AttributeError):
        pass
    try:
        import shutil, subprocess
        system = platform.system()
        if system == "Linux":
            font_dir = Path.home() / ".local/share/fonts"
            font_dir.mkdir(parents=True, exist_ok=True)
            dest = font_dir / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
                subprocess.run(["fc-cache", "-f"], capture_output=True)
            return True
        elif system == "Darwin":
            font_dir = Path.home() / "Library/Fonts"
            font_dir.mkdir(parents=True, exist_ok=True)
            dest = font_dir / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
            return True
        elif system == "Windows":
            import ctypes, winreg
            user_fonts = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
            user_fonts.mkdir(parents=True, exist_ok=True)
            dest = user_fonts / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows NT\CurrentVersion\Fonts",
                                     0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, "Barrio (TrueType)", 0, winreg.REG_SZ, str(dest))
                winreg.CloseKey(key)
            except Exception:
                pass
            ctypes.windll.gdi32.AddFontResourceW(str(dest))
            threading.Thread(target=lambda: ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0),
                             daemon=True).start()
            return True
    except Exception as e:
        print(f"[Font] Could not install font: {e}")
    return False


# ── Bleep player ───────────────────────────────────────────────────────────────

class BleepPlayer:
    SAMPLE_RATE = 44100

    def __init__(self):
        self._stop_event = threading.Event()
        self._paused = False
        self._thread: threading.Thread | None = None
        self._cache: dict[int, pygame.mixer.Sound] = {}
        self._mixer_ready = False
        t = threading.Thread(target=self._init_mixer, daemon=True)
        t.start(); t.join(timeout=5.0)
        if not self._mixer_ready:
            print("[BleepPlayer] WARNING: pygame mixer init timed out — audio disabled.")

    def _init_mixer(self):
        try:
            pygame.mixer.pre_init(self.SAMPLE_RATE, -16, 1, 256)
            pygame.mixer.init()
            self._mixer_ready = True
        except Exception as e:
            print(f"[BleepPlayer] mixer init error: {e}")

    def _make_bleep(self, freq: int):
        if not self._mixer_ready: return None
        if freq in self._cache: return self._cache[freq]
        import array as arr
        duration  = 0.042
        n_samples = int(self.SAMPLE_RATE * duration)
        volume    = 0.28
        buf       = arr.array("h", [0] * n_samples)
        for i in range(n_samples):
            t = i / self.SAMPLE_RATE
            wave = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
            env  = math.exp(-t * 40)
            buf[i] = int(wave * env * volume * 32767)
        sound = pygame.mixer.Sound(buffer=buf)
        self._cache[freq] = sound
        return sound

    def start_talking(self, tone: str = "neutral"):
        if not self._mixer_ready: return
        self.stop()
        self._stop_event.clear()
        freq = BLEEP_TONES.get(tone, 440)
        self._thread = threading.Thread(target=self._loop, args=(freq,), daemon=True)
        self._thread.start()

    def _loop(self, freq: int):
        sound = self._make_bleep(freq)
        if sound is None: return
        while not self._stop_event.is_set():
            if self._paused:
                time.sleep(0.02); continue
            sound.play()
            time.sleep(random.uniform(0.03, 0.055))

    def pause(self):  self._paused = True
    def resume(self): self._paused = False

    def stop(self):
        self._stop_event.set()
        self._paused = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.4)


# ── Voice input (microphone) ───────────────────────────────────────────────────

def _settings_path() -> Path:
    """Return path to memory/settings.json next to main.py / the executable."""
    base = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent
    return base / "memory" / "settings.json"


def _load_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(data: dict) -> None:
    p = _settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[Settings] Saved to {p}")
    except Exception as e:
        print(f"[Settings] Could not save: {e}")


def _list_microphones() -> list[tuple[int, str]]:
    """Return [(index, name), ...] for every available input device."""
    try:
        import speech_recognition as sr
        mics = []
        for i, name in enumerate(sr.Microphone.list_microphone_names()):
            mics.append((i, name))
        return mics
    except Exception as e:
        print(f"[Voice] Could not list microphones: {e}")
        return []


class MicPickerDialog:
    """Win95-style dialog that lets the user choose a microphone device."""

    def __init__(self, parent: tk.Tk, mics: list[tuple[int, str]]):
        self.result: int | None = None   # chosen device index, or None = cancelled

        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=W95_BG)
        self._win.resizable(False, False)
        self._drag_x = self._drag_y = 0

        outer = tk.Frame(self._win, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)

        # Title bar
        title_bar = tk.Frame(outer, bg=W95_TITLE_BG, height=18)
        title_bar.pack(fill="x", padx=2, pady=(2, 0))
        title_bar.pack_propagate(False)
        tk.Label(title_bar, text="🎤  Select Microphone",
                 bg=W95_TITLE_BG, fg=W95_TITLE_FG,
                 font=W95_FONT_BOLD, anchor="w", padx=4).pack(side="left", fill="y")
        for w in title_bar.winfo_children():
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_motion)
        title_bar.bind("<ButtonPress-1>", self._drag_start)
        title_bar.bind("<B1-Motion>",     self._drag_motion)

        body = tk.Frame(outer, bg=W95_BG, padx=12, pady=10)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="Choose which microphone Agetha should use:",
                 fg=W95_TEXT, bg=W95_BG, font=W95_FONT,
                 wraplength=260, justify="left").pack(anchor="w", pady=(0, 6))

        list_frame = tk.Frame(body, bg=W95_BG, relief="sunken", bd=2)
        list_frame.pack(fill="both", expand=True)

        scrollbar = tk.Scrollbar(list_frame, orient="vertical")
        self._listbox = tk.Listbox(list_frame, font=W95_FONT,
                                    bg=W95_INPUT_BG, fg=W95_TEXT,
                                    selectbackground=W95_TITLE_BG,
                                    selectforeground=W95_TITLE_FG,
                                    relief="flat", bd=0, height=min(len(mics), 8),
                                    yscrollcommand=scrollbar.set)
        scrollbar.config(command=self._listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self._listbox.pack(side="left", fill="both", expand=True)

        for idx, name in mics:
            self._listbox.insert("end", f"[{idx}]  {name}")

        if mics:
            self._listbox.selection_set(0)

        self._mics = mics

        tk.Frame(outer, bg=W95_SHADOW, height=1).pack(fill="x", padx=2, pady=(6, 0))
        tk.Frame(outer, bg="#ffffff",  height=1).pack(fill="x", padx=2)

        btn_row = tk.Frame(outer, bg=W95_BG, pady=6)
        btn_row.pack(fill="x")

        tk.Button(btn_row, text="OK", font=W95_FONT_BOLD,
                  bg=W95_BTN_BG, fg=W95_TEXT,
                  activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
                  relief="raised", bd=2, width=8, pady=2,
                  command=self._ok).pack(side="left", padx=(16, 4))
        tk.Button(btn_row, text="Cancel", font=W95_FONT_BOLD,
                  bg=W95_BTN_BG, fg=W95_TEXT,
                  activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
                  relief="raised", bd=2, width=8, pady=2,
                  command=self._cancel).pack(side="left", padx=4)

        self._win.update_idletasks()
        px = parent.winfo_x(); py = parent.winfo_y()
        pw = parent.winfo_width(); ph = parent.winfo_height()
        ww = self._win.winfo_width(); wh = self._win.winfo_height()
        x = px + (pw - ww) // 2; y = py + (ph - wh) // 2
        self._win.geometry(f"+{max(0,x)}+{max(0,y)}")
        self._win.bind("<Return>", lambda _: self._ok())
        self._win.bind("<Escape>", lambda _: self._cancel())
        try: self._win.focus_force(); self._listbox.focus_set()
        except Exception: pass

    def _ok(self):
        sel = self._listbox.curselection()
        if sel:
            self.result = self._mics[sel[0]][0]
        self._win.destroy()

    def _cancel(self):
        self.result = None
        self._win.destroy()

    def _drag_start(self, e):
        self._drag_x, self._drag_y = e.x_root, e.y_root

    def _drag_motion(self, e):
        dx = e.x_root - self._drag_x; dy = e.y_root - self._drag_y
        self._win.geometry(f"+{self._win.winfo_x()+dx}+{self._win.winfo_y()+dy}")
        self._drag_x, self._drag_y = e.x_root, e.y_root

    def wait(self):
        self._win.wait_window()
        return self.result


class VoiceInput:
    """Continuous microphone listener. Sends transcribed text via callback when
    the user pauses for SILENCE_SECONDS (default 3 s) after speaking."""

    SILENCE_SECONDS = 1.2

    def __init__(self, on_text_callback, device_index: int | None = None):
        self._cb           = on_text_callback
        self._device_index = device_index
        self._active = False
        self._thread: threading.Thread | None = None
        self._stop   = threading.Event()
        self._sr_ok  = False
        self._error: str | None = None

        try:
            import speech_recognition as sr  # noqa: F401
            self._sr_ok = True
        except ImportError:
            self._error = "SpeechRecognition not installed.\nRun: pip install SpeechRecognition pyaudio"

    @property
    def available(self) -> bool:
        return self._sr_ok

    @property
    def error(self) -> str | None:
        return self._error

    def start(self):
        if not self._sr_ok or self._active:
            return
        self._active = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._active = False
        self._stop.set()

    def _run(self):
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        recognizer.pause_threshold = self.SILENCE_SECONDS
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True

        mic_kwargs = {}
        if self._device_index is not None:
            mic_kwargs["device_index"] = self._device_index

        with sr.Microphone(**mic_kwargs) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            while not self._stop.is_set():
                try:
                    audio = recognizer.listen(source, timeout=None, phrase_time_limit=30)
                    if self._stop.is_set():
                        break
                    # Run recognition in a thread so we can keep listening
                    threading.Thread(target=self._recognise, args=(recognizer, audio),
                                     daemon=True).start()
                except Exception:
                    if not self._stop.is_set():
                        time.sleep(0.5)

    def _recognise(self, recognizer, audio):
        try:
            if _USE_LOCAL_STT:
                model = _get_whisper_model()
                if model is None:
                    return
                import io, wave, numpy as np
                raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                segments, _ = model.transcribe(arr, language="en", beam_size=1,
                                               vad_filter=True, vad_parameters={"min_silence_duration_ms": 300})
                text = " ".join(s.text for s in segments).strip()
            else:
                import speech_recognition as sr
                text = recognizer.recognize_google(audio)
            if text:
                print(f"[Voice] Recognised: {text}")
                self._cb(text)
        except Exception:
            pass  # silence / unrecognised audio — ignore


# ── Animation speed ────────────────────────────────────────────────────────────

def _read_animation_speed() -> float:
    try:
        _base = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent
        _cfg = _base / "config.txt"
        if _cfg.exists():
            for ln in _cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                s = ln.strip()
                if s.startswith("#") or "=" not in s: continue
                k, v = s.split("=", 1)
                if k.strip().upper() == "ANIMATION_SPEED":
                    return float(v.strip())
    except Exception:
        pass
    return 0.6

_ANIMATION_SPEED = _read_animation_speed()


def _read_faster_mode() -> bool:
    try:
        _base = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent
        _cfg = _base / "config.txt"
        if _cfg.exists():
            for ln in _cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                s = ln.strip()
                if s.startswith("#") or "=" not in s: continue
                k, v = s.split("=", 1)
                if k.strip().upper() == "FASTER_MODE":
                    return v.strip().lower() in ("yes", "true", "1", "on")
    except Exception:
        pass
    return False

_FASTER_MODE = _read_faster_mode()


def _read_local_stt() -> bool:
    try:
        _base = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent
        _cfg = _base / "config.txt"
        if _cfg.exists():
            for ln in _cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                s = ln.strip()
                if s.startswith("#") or "=" not in s: continue
                k, v = s.split("=", 1)
                if k.strip().upper() == "USE_LOCAL_STT":
                    return v.strip().lower() in ("yes", "true", "1", "on")
    except Exception:
        pass
    return False

_USE_LOCAL_STT = _read_local_stt()


def _read_show_mic_button() -> bool:
    try:
        _base = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent
        _cfg = _base / "config.txt"
        if _cfg.exists():
            for ln in _cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                s = ln.strip()
                if s.startswith("#") or "=" not in s: continue
                k, v = s.split("=", 1)
                if k.strip().upper() == "SHOW_MIC_BUTTON":
                    return v.strip().lower() in ("yes", "true", "1", "on")
    except Exception:
        pass
    return True  # enabled by default

_SHOW_MIC_BUTTON = _read_show_mic_button()

# Lazy-load faster-whisper model once
_whisper_model = None
_whisper_lock  = threading.Lock()

def _get_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            try:
                from faster_whisper import WhisperModel
                print("[STT] Loading faster-whisper model (tiny.en) — first run only...")
                _whisper_model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
                print("[STT] faster-whisper ready.")
            except Exception as e:
                print(f"[STT] faster-whisper load failed: {e}")
                _whisper_model = None
    return _whisper_model


def _load_gif_frames_offthread(path: str) -> tuple[list[Image.Image], list[int]]:
    pil_frames: list[Image.Image] = []
    delays: list[int] = []
    is_sleeping = Path(path).name == "sleeping.gif"
    speed = 1.0 if is_sleeping else _ANIMATION_SPEED
    try:
        img = Image.open(path)
        for frame in ImageSequence.Iterator(img):
            f = frame.convert("RGBA")
            f.thumbnail((GIF_W, GIF_H), Image.LANCZOS)
            canvas = Image.new("RGBA", (GIF_W, GIF_H), (10, 10, 15, 255))
            ox = (GIF_W - f.width) // 2
            oy = (GIF_H - f.height) // 2
            canvas.paste(f, (ox, oy), f)
            pil_frames.append(canvas)
            delay = frame.info.get("duration", 80)
            delays.append(max(int(delay * speed), 40))
    except Exception as e:
        print(f"[GifPlayer] Could not load {path}: {e}")
    return pil_frames, delays


class GifPlayer:
    def __init__(self, label: tk.Label, gif_path: str, after_cb,
                 pil_frames: list | None = None, delays: list | None = None):
        self._label   = label
        self._after   = after_cb
        self._frames: list[ImageTk.PhotoImage] = []
        self._delays: list[int] = delays or []
        self._idx     = 0
        self._job     = None
        self._running = False
        self._once_counter: int | None = None
        self._on_once_done = None

        src_frames = pil_frames
        if src_frames is None:
            src_frames, self._delays = _load_gif_frames_offthread(gif_path)
        for pil_img in src_frames:
            try:
                self._frames.append(ImageTk.PhotoImage(pil_img))
            except Exception as e:
                print(f"[GifPlayer] ImageTk failed for {gif_path}: {e}")

    def play(self):
        if not self._frames: return
        self._running = True
        self._idx = 0
        self._once_counter = None
        self._on_once_done = None
        self._tick()

    def stop(self):
        self._running = False
        self._once_counter = None
        self._on_once_done = None
        if self._job:
            try: self._label.after_cancel(self._job)
            except Exception: pass
            self._job = None

    def _tick(self):
        if not self._running or not self._frames: return
        self._label.config(image=self._frames[self._idx])
        delay = self._delays[self._idx]
        self._idx += 1
        if self._once_counter is not None:
            self._once_counter -= 1
            if self._once_counter <= 0:
                self._running = False
                self._job = None
                cb = self._on_once_done
                self._on_once_done = None
                self._once_counter = None
                if cb:
                    try: cb()
                    except Exception: pass
                return
            self._idx %= len(self._frames)
            self._job = self._after(delay, self._tick)
            return
        self._idx %= len(self._frames)
        self._job = self._after(delay, self._tick)

    def play_once(self, on_done=None):
        if not self._frames:
            if on_done:
                try: on_done()
                except Exception: pass
            return
        self.stop()
        self._running = True
        self._idx = 0
        self._once_counter = len(self._frames)
        self._on_once_done = on_done
        self._tick()


class SubtitleRenderer:
    CHAR_DELAY = 0.035

    def __init__(self, canvas: tk.Canvas, font_size: int = 17, bleep_player=None):
        self._canvas     = canvas
        self._font_size  = font_size
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._bleep = bleep_player
        self._canvas.config(bg="#a0a0a0")
        self._font = self._load_font(font_size)

    def _load_font(self, size: int) -> tkfont.Font:
        available = tkfont.families()
        for name in ("Barrio", "barrio"):
            if name in available:
                return tkfont.Font(family=name, size=size)
        return tkfont.Font(family="Courier", size=size, weight="bold")

    def clear(self):
        self._canvas.delete("all")

    def show_thinking(self, raw_text: str):
        import re
        texts = re.findall(r'"text"\s*:\s*"([^"]*)', raw_text)
        preview = " ".join(texts).strip() or "…"
        self._canvas.after(0, lambda p=preview: self._draw(p, color="#888899"))

    def show_message(self, text: str, color: str = "#ffffff", duration: float = 6.0):
        self.stop()
        self._canvas.after(0, lambda: self._draw(text, color))
        try:
            if duration and duration > 0:
                self._canvas.after(int(duration * 1000), self.clear)
        except Exception:
            pass

    def speak(self, segments: list, on_done=None):
        self.stop()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(segments, on_done), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self, segments: list, on_done):
        self._canvas.after(0, self.clear)
        full_text = ""
        for seg in segments:
            if self._stop_event.is_set(): break
            chunk = seg.get("text", "").strip()
            pause = seg.get("pause", 0.0)
            if full_text and not full_text.endswith(" "):
                full_text += " "
            for ch in chunk:
                if self._stop_event.is_set(): break
                full_text += ch
                t = full_text
                self._canvas.after(0, lambda txt=t: self._draw(txt))
                time.sleep(self.CHAR_DELAY)
            if pause > 0 and not self._stop_event.is_set():
                if self._bleep: self._bleep.pause()
                time.sleep(pause)
                if self._bleep: self._bleep.resume()
        try:
            if self._bleep: self._bleep.stop()
        except Exception:
            pass
        if on_done:
            self._canvas.after(0, on_done)

    def _draw(self, text: str, color: str = "#ffffff"):
        cw = self._canvas.winfo_width() or WINDOW_W
        ch = self._canvas.winfo_height() or 130
        max_w = max(40, cw - 24)
        max_lines = 3
        min_font_size = 8
        import re

        def estimate_lines(word_list, chars_per_line):
            line_chars = 0; lines = 1
            for w in word_list:
                needed = len(w) + (1 if line_chars > 0 else 0)
                if line_chars > 0 and line_chars + needed > chars_per_line:
                    lines += 1; line_chars = len(w)
                else:
                    line_chars += needed
            return lines

        words = text.split()
        if not words:
            self._canvas.delete("all"); return

        font_size = self._font_size
        font = self._font

        while font_size >= min_font_size:
            char_w = max(4, font_size * 0.62)
            chars_per_line = max(1, int(max_w // char_w))
            parts = []
            for w in re.split(r'(\s+)', text):
                if w.isspace() or not w:
                    parts.append(w); continue
                if len(w) <= chars_per_line:
                    parts.append(w)
                else:
                    chunks = [w[i:i+chars_per_line] for i in range(0, len(w), chars_per_line)]
                    parts.append(" ".join(chunks))
            candidate_words = "".join(parts).strip().split()
            if estimate_lines(candidate_words, chars_per_line) <= max_lines: break
            font_size -= 1; font = self._load_font(font_size)

        candidate = " ".join(candidate_words)
        x = cw // 2

        while font_size >= min_font_size:
            self._canvas.delete("all")
            try:
                shadow_id = self._canvas.create_text(x+2, 6+2, text=candidate, fill="#000000",
                                                     font=font, anchor="n", width=max_w, justify="center")
                text_id   = self._canvas.create_text(x,   6,   text=candidate, fill=color,
                                                     font=font, anchor="n", width=max_w, justify="center")
                bbox = self._canvas.bbox(text_id)
                if bbox:
                    height = bbox[3] - bbox[1]
                    if height <= ch - 12:
                        y = max(6, (ch - height) // 2)
                        self._canvas.coords(shadow_id, x+2, y+2)
                        self._canvas.coords(text_id,   x,   y)
                        break
                    font_size -= 1; font = self._load_font(font_size)
                else:
                    break
            except Exception:
                break


class AgethaPopup:
    def __init__(self, parent: tk.Tk, messages: list, mood: str = "neutral"):
        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=W95_BG)
        self._win.resizable(False, False)
        self._drag_x = self._drag_y = 0

        outer = tk.Frame(self._win, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)

        title_bar = tk.Frame(outer, bg=W95_TITLE_BG, height=18)
        title_bar.pack(fill="x", padx=2, pady=(2, 0))
        title_bar.pack_propagate(False)

        tk.Label(title_bar, text="⚠  Agetha.exe",
                 bg=W95_TITLE_BG, fg=W95_TITLE_FG,
                 font=W95_FONT_BOLD, anchor="w", padx=4).pack(side="left", fill="y")
        tk.Button(title_bar, text="✕", bg=W95_BTN_BG, fg=W95_TEXT,
                  font=("MS Sans Serif", 7, "bold"), relief="raised", bd=2, width=2,
                  activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
                  command=self._win.destroy).pack(side="right", padx=2, pady=1)

        for w in (title_bar,) + tuple(title_bar.winfo_children()):
            if not isinstance(w, tk.Button):
                w.bind("<ButtonPress-1>", self._drag_start)
                w.bind("<B1-Motion>",     self._drag_motion)

        body = tk.Frame(outer, bg=W95_BG, padx=12, pady=10)
        body.pack(fill="both", expand=True, padx=2)

        icon_frame = tk.Frame(body, bg=W95_BG, bd=2, relief="sunken", width=36, height=36)
        icon_frame.grid(row=0, column=0, rowspan=max(len(messages), 1)+1,
                        sticky="n", padx=(0, 12), pady=2)
        icon_frame.pack_propagate(False)
        tk.Label(icon_frame, text="⚠", fg="#ff8000", bg=W95_BG,
                 font=("MS Sans Serif", 16, "bold")).pack(expand=True)

        for i, msg in enumerate(messages):
            tk.Label(body, text=msg, fg=W95_TEXT, bg=W95_BG, font=W95_FONT,
                     wraplength=240, justify="left", anchor="w").grid(row=i, column=1, sticky="w", pady=1)

        tk.Frame(outer, bg=W95_SHADOW, height=1).pack(fill="x", padx=2, pady=(4, 0))
        tk.Frame(outer, bg="#ffffff",  height=1).pack(fill="x", padx=2)

        btn_row = tk.Frame(outer, bg=W95_BG, pady=6)
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="OK", font=W95_FONT_BOLD,
                  bg=W95_BTN_BG, fg=W95_TEXT,
                  activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
                  relief="raised", bd=2, width=8, pady=2,
                  command=self._win.destroy).pack()

        self._win.update_idletasks()
        px = parent.winfo_x(); py = parent.winfo_y()
        pw = parent.winfo_width()
        ww = self._win.winfo_width(); wh = self._win.winfo_height()
        x = px + (pw - ww) // 2; y = max(0, py - wh - 10)
        self._win.geometry(f"+{x}+{y}")
        self._win.bind("<Return>", lambda _: self._win.destroy())
        self._win.bind("<Escape>", lambda _: self._win.destroy())
        try: self._win.focus_force()
        except Exception: pass

    def _drag_start(self, event):
        self._drag_x, self._drag_y = event.x_root, event.y_root

    def _drag_motion(self, event):
        dx = event.x_root - self._drag_x; dy = event.y_root - self._drag_y
        self._win.geometry(f"+{self._win.winfo_x()+dx}+{self._win.winfo_y()+dy}")
        self._drag_x, self._drag_y = event.x_root, event.y_root


# ── Startup glitch effect ──────────────────────────────────────────────────────

def _run_glitch_effect(root: tk.Tk, duration_ms: int = 1800):
    """Briefly glitch the window position/opacity on startup, then settle."""
    start = time.time()
    original_x = root.winfo_x()
    original_y = root.winfo_y()

    def _glitch_step():
        elapsed = (time.time() - start) * 1000
        if elapsed >= duration_ms:
            # Snap back to original position cleanly
            try:
                root.geometry(f"+{original_x}+{original_y}")
                root.attributes("-alpha", 1.0)
            except Exception:
                pass
            return
        # Random offset glitch
        ox = original_x + random.randint(-8, 8)
        oy = original_y + random.randint(-4, 4)
        try:
            root.geometry(f"+{ox}+{oy}")
            # Flicker alpha
            alpha = random.choice([0.7, 0.8, 0.9, 1.0, 0.6, 1.0])
            root.attributes("-alpha", alpha)
        except Exception:
            pass
        interval = random.randint(40, 120)
        root.after(interval, _glitch_step)

    root.after(200, _glitch_step)


# ── Main app ───────────────────────────────────────────────────────────────────

class CompanionApp:

    STATE_SLEEPING = "sleeping"
    STATE_THINKING = "thinking"
    STATE_IDLE     = "idle"
    STATE_TALKING  = "talking"

    IDLE_GIFS    = ["idle-1.gif", "idle-2.gif", "idle-3.gif"]
    TALKING_GIFS = ["talking-1.gif", "talking-2.gif", "talking-3.gif"]
    EXTRA_GIFS   = {
        "happy":     "happy.gif",
        "surprised": "surprised.gif",
        "sad":       "sad.gif",
        "excited":   "happy.gif",
        "angry":     "angry.gif",
        "thinking":  "thinking.gif",
        "sleeping":  "sleeping.gif",
        "loaf":      "loaf.gif",
        "want":      "want.gif",
    }
    EXTRA_STATIC_GIFS = {
        "happy":    "happy-static.gif",
        "sad":      "sad-static.gif",
        "angry":    "angry-static.gif",
        "thinking": "thinking-static.gif",
    }

    # Smooth slide animation constants
    _SLIDE_STEPS    = 20
    _SLIDE_INTERVAL = 16   # ms per step (~60 fps)

    # Shake animation constants
    _SHAKE_STEPS    = 12
    _SHAKE_INTERVAL = 30   # ms per step
    _SHAKE_AMPLITUDE = 10  # px

    # Bounce animation constants
    _BOUNCE_STEPS    = 16
    _BOUNCE_INTERVAL = 20

    def __init__(self):
        try:
            import ctypes
            _shcore = ctypes.windll.shcore
            try:    _shcore.SetProcessDpiAwareness(2)
            except Exception:
                try: _shcore.SetProcessDpiAwareness(1)
                except Exception: ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

        _register_barrio_font()

        # Try tkinterdnd2 for file drag-and-drop support
        _dnd_ok = False
        try:
            from tkinterdnd2 import TkinterDnD
            self.root = TkinterDnD.Tk()
            _dnd_ok = True
            print("[DnD] tkinterdnd2 loaded — file drag-and-drop enabled")
        except Exception:
            self.root = tk.Tk()
            print("[DnD] tkinterdnd2 not available — file drag-and-drop disabled (pip install tkinterdnd2)")
        self.root.title("Agetha.exe")
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+80+80")
        self.root.configure(bg=W95_BG)
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._state       = self.STATE_SLEEPING
        self._current_gif_player: GifPlayer | None = None
        self._gif_cache: dict[str, GifPlayer] = {}
        self._talking_rotate_job = None
        self._poll_job   = None
        self._persistent_mood: str | None = None
        self._bleep  = None
        self._screen = None
        self._ai     = None
        self._last_screen_text: str = ""
        self._loaf_job    = None
        self._is_loafing  = False
        self._pending_shutdown = False
        self._last_touch_time: float = 0.0
        self._slide_job   = None   # for smooth window animation
        self._shake_job   = None   # for shake animation
        self._bounce_job  = None   # for bounce animation
        self._voice: VoiceInput | None = None
        self._mic_active  = False
        self._dragging_file = False          # True while a file is being dragged over
        self._last_dragged_file = ""         # Path of last file dragged onto window
        self._close_hover_job = None         # debounce timer for close button hover
        self._close_hover_notified = False   # prevent spam

        self._build_ui()

        # Loading overlay
        self._loading_label = tk.Frame(self._outer, bg=W95_BG)
        self._loading_label.place(x=0, y=20, relwidth=1.0, relheight=1.0)
        tk.Label(self._loading_label, text="Loading Agetha.exe",
                 fg=W95_TEXT, bg=W95_BG, font=W95_FONT_BOLD).pack(pady=(40, 4))
        self._load_status_var = tk.StringVar(value="Initializing…")
        tk.Label(self._loading_label, textvariable=self._load_status_var,
                 fg=W95_SHADOW, bg=W95_BG, font=W95_FONT).pack(pady=(0, 8))
        _pb_outer = tk.Frame(self._loading_label, bg=W95_BG, relief="sunken", bd=2,
                             width=WINDOW_W - 60, height=20)
        _pb_outer.pack(pady=(0, 4))
        _pb_outer.pack_propagate(False)
        self._pb_canvas = tk.Canvas(_pb_outer, bg=W95_INPUT_BG, highlightthickness=0, bd=0)
        self._pb_canvas.pack(fill="both", expand=True)
        self._load_pct_var = tk.StringVar(value="0%")
        tk.Label(self._loading_label, textvariable=self._load_pct_var,
                 fg=W95_TEXT, bg=W95_BG, font=W95_FONT).pack()

        # Discord / community links — shown while loading
        _link_frame = tk.Frame(self._loading_label, bg=W95_BG)
        _link_frame.pack(pady=(10, 0))
        _discord_lbl = tk.Label(_link_frame, text="Join the Discord server!",
                                 fg=W95_TITLE_BG, bg=W95_BG, font=("MS Sans Serif", 7, "underline"),
                                 cursor="hand2")
        _discord_lbl.pack()
        _discord_lbl.bind("<Button-1>", lambda e: webbrowser.open(DISCORD_INVITE_URL))
        tk.Label(_link_frame, text="(link in TikTok bio, or the website below)",
                 fg=W95_SHADOW, bg=W95_BG, font=("MS Sans Serif", 6)).pack()
        _site_lbl = tk.Label(_link_frame, text=AGETHA_WEBSITE_URL,
                              fg=W95_TITLE_BG, bg=W95_BG, font=("MS Sans Serif", 6, "underline"),
                              cursor="hand2")
        _site_lbl.pack(pady=(2, 0))
        _site_lbl.bind("<Button-1>", lambda e: webbrowser.open(AGETHA_WEBSITE_URL))

        self._load_total = 3; self._load_done = 0

        def _draw_progress():
            try:
                self._pb_canvas.update_idletasks()
                w = self._pb_canvas.winfo_width(); h = self._pb_canvas.winfo_height()
                if w < 2 or h < 2: return
                pct = min(self._load_done / max(self._load_total, 1), 1.0)
                fill_w = max(0, int(w * pct))
                self._pb_canvas.delete("all")
                block = 16; gap = 2; x = 0
                while x + block <= fill_w:
                    self._pb_canvas.create_rectangle(x, 1, x+block-gap, h-1, fill=W95_TITLE_BG, outline="")
                    x += block
                self._load_pct_var.set(f"{int(pct*100)}%")
            except Exception:
                pass

        self._draw_progress = _draw_progress

        def _advance_progress(status: str, steps: int = 1):
            def _on_main():
                self._load_done += steps
                self._load_status_var.set(status)
                self._draw_progress()
            try: self.root.after(0, _on_main)
            except Exception: pass

        self._advance_progress = _advance_progress
        self._loading_label.after(50, _draw_progress)

        try:
            self.root.update_idletasks(); self.root.update()
            self.root.deiconify(); self.root.lift(); self.root.update()
        except Exception:
            pass

        self._show_desktop_loading_indicator()

        threading.Thread(target=self._init_background, daemon=True).start()
        self._drag_x = self._drag_y = 0
        self._is_minimized = False

    def _build_ui(self):
        global W95_FONT, W95_FONT_BOLD
        W95_FONT      = _safe_win_font(8, bold=False)
        W95_FONT_BOLD = _safe_win_font(8, bold=True)

        self._outer = tk.Frame(self.root, bg=W95_BG, relief="raised", bd=2)
        self._outer.pack(fill="both", expand=True)

        # Title bar
        title_bar = tk.Frame(self._outer, bg=W95_TITLE_BG, height=18)
        title_bar.pack(fill="x", padx=2, pady=(2, 0))
        title_bar.pack_propagate(False)
        title_lbl = tk.Label(title_bar, text="⚠  Agetha.exe",
                             bg=W95_TITLE_BG, fg=W95_TITLE_FG,
                             font=W95_FONT_BOLD, anchor="w", padx=4)
        title_lbl.pack(side="left", fill="y")
        if _FASTER_MODE:
            tk.Label(title_bar, text="FAST MODE",
                     bg=W95_TITLE_BG, fg="#1a3a6b",
                     font=("MS Sans Serif", 7, "bold"), anchor="w", padx=2).pack(side="left", fill="y")
        self._close_btn = tk.Button(title_bar, text="✕", bg=W95_BTN_BG, fg=W95_TEXT,
                  font=("MS Sans Serif", 7, "bold"), relief="raised", bd=2, width=2,
                  activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
                  command=self._close_with_animation)
        self._close_btn.pack(side="right", padx=(0, 2), pady=1)
        self._close_btn.bind("<Enter>", self._on_close_hover)
        self._close_btn.bind("<Leave>", self._on_close_leave)
        tk.Button(title_bar, text="□", bg=W95_BTN_BG, fg=W95_TEXT,
                  font=("MS Sans Serif", 7, "bold"), relief="raised", bd=2, width=2,
                  activebackground=W95_BTN_BG, command=lambda: None).pack(side="right", padx=(0, 1), pady=1)
        tk.Button(title_bar, text="─", bg=W95_BTN_BG, fg=W95_TEXT,
                  font=("MS Sans Serif", 7, "bold"), relief="raised", bd=2, width=2,
                  activebackground=W95_BTN_BG, command=self._minimize).pack(side="right", padx=(0, 1), pady=1)
        for w in (title_bar, title_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_motion)

        # GIF area
        gif_border = tk.Frame(self._outer, bg="#000000", relief="raised", bd=2, highlightthickness=2,
                              highlightbackground="#000000", highlightcolor="#000000")
        gif_border.pack(fill="x", padx=4, pady=(4, 0))
        self._gif_border = gif_border
        self._glow_phase = 0.0
        self._glow_mood  = "neutral"
        self._glow_active = False
        self._gif_label = tk.Label(gif_border, bg="#000000", bd=0,
                                   width=GIF_W, height=GIF_H, anchor="center")
        self._gif_label.pack(fill="both", expand=True)
        self._gif_label.bind("<Button-1>", self._on_gif_click)
        # File drag-and-drop support (Windows tkinter DnD)
        try:
            self._gif_label.drop_target_register("DND_Files")  # type: ignore
            self._gif_label.dnd_bind("<<DropEnter>>",  self._on_file_drag_enter)  # type: ignore
            self._gif_label.dnd_bind("<<DropLeave>>",  self._on_file_drag_leave)  # type: ignore
            self._gif_label.dnd_bind("<<Drop>>",       self._on_file_drop)       # type: ignore
        except Exception:
            # tkinterdnd2 not available — use standard Tk DnD fallback
            try:
                self._gif_label.bind("<<B1-Enter>>", self._on_file_drag_enter)
            except Exception:
                pass

        # Status bar
        status_frame = tk.Frame(self._outer, bg=W95_BG, bd=1, relief="sunken")
        status_frame.pack(fill="x", padx=4, pady=(2, 0))
        self._status_var = tk.StringVar(value="zzz…")
        tk.Label(status_frame, textvariable=self._status_var,
                 fg=W95_SHADOW, bg=W95_BG, font=W95_FONT, anchor="w").pack(side="left", padx=4, pady=1)

        # Subtitle canvas
        self._sub_canvas = tk.Canvas(self._outer, width=WINDOW_W, height=130,
                                     bg="#a0a0a0", bd=2, relief="sunken", highlightthickness=0)
        self._sub_canvas.pack(fill="x", padx=4, pady=(4, 0))
        self._subtitle = SubtitleRenderer(self._sub_canvas, font_size=17, bleep_player=self._bleep)

        # Input row
        input_frame = tk.Frame(self._outer, bg=W95_BG)
        input_frame.pack(fill="x", padx=4, pady=(6, 8))

        families = tkfont.families()
        input_font = tkfont.Font(family="Barrio", size=11) if "Barrio" in families \
                     else tkfont.Font(family="MS Sans Serif", size=8)

        self._input_var = tk.StringVar()

        # Wrapper frame so we can stack the placeholder label on top of the Entry
        entry_wrapper = tk.Frame(input_frame, bg=W95_INPUT_BG, relief="sunken", bd=2)
        entry_wrapper.pack(side="left", fill="x", expand=True)

        self._input_box = tk.Entry(entry_wrapper, textvariable=self._input_var, font=input_font,
                                   bg=W95_INPUT_BG, fg=W95_TEXT, insertbackground=W95_TEXT,
                                   relief="flat", bd=0)
        self._input_box.pack(fill="both", expand=True, ipady=6, padx=2)

        # Placeholder label — overlaid, hidden on focus / when text is present
        placeholder_font = tkfont.Font(family="MS Sans Serif", size=7)
        self._placeholder_lbl = tk.Label(entry_wrapper, text="", font=placeholder_font,
                                         bg=W95_INPUT_BG, fg="#888888",
                                         anchor="w", padx=4, pady=0)
        self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._placeholder_lbl.bind("<Button-1>", lambda e: self._input_box.focus_set())

        # Bind focus / keystrokes to show/hide placeholder
        self._input_box.bind("<FocusIn>",  lambda e: self._update_placeholder(focused=True))
        self._input_box.bind("<FocusOut>", lambda e: self._update_placeholder(focused=False))
        self._input_var.trace_add("write", lambda *_: self._update_placeholder())

        self._input_box.bind("<Return>", self._on_user_input)

        # Mic toggle button (🎤 / 🔴) — hidden entirely if SHOW_MIC_BUTTON = no
        self._mic_btn_var = tk.StringVar(value="🎤")
        self._mic_btn = tk.Button(input_frame, textvariable=self._mic_btn_var,
                                  font=W95_FONT_BOLD, bg=W95_BTN_BG, fg=W95_TEXT,
                                  activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
                                  relief="raised", bd=2, padx=6, pady=5,
                                  command=self._toggle_mic)
        if _SHOW_MIC_BUTTON:
            self._mic_btn.pack(side="left", padx=(2, 0))

        # Send button
        tk.Button(input_frame, text="OK", font=W95_FONT_BOLD,
                  bg=W95_BTN_BG, fg=W95_TEXT,
                  activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
                  relief="raised", bd=2, padx=10, pady=5,
                  command=self._on_user_input).pack(side="left", padx=(4, 0))

    # ── Input placeholder hint ─────────────────────────────────────────────────

    def _update_placeholder(self, focused=None):
        """Show/hide the placeholder label based on content only (always visible when empty)."""
        has_text = bool(self._input_var.get())
        if has_text:
            self._placeholder_lbl.place_forget()
        else:
            txt = self._get_placeholder_text()
            self._placeholder_lbl.config(text=txt)
            self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _get_placeholder_text(self):
        """Build the placeholder string from current key/token status."""
        try:
            status = self._ai.get_token_status()
            if not status.get("using_groq"):
                if status.get("provider") == "openrouter":
                    return "OpenRouter (experimental)  •  type here..."
                return "local AI  •  type here..."
            idx   = status.get("key_index", 1)
            total = status.get("key_count", 1)
            pct   = status.get("pct_left", 100)
            return f"key {idx}/{total}  \u2022  {pct}% tokens left"
        except Exception:
            return "type here..."

    def _start_placeholder_refresh(self):
        """Refresh placeholder every 10 s so token% stays current."""
        def _tick():
            try:
                self._update_placeholder()
            except Exception:
                pass
            self.root.after(10000, _tick)
        self.root.after(10000, _tick)

    # ── Mic toggle ─────────────────────────────────────────────────────────────

    def _toggle_mic(self):
        if self._voice is None:
            # Check for saved microphone preference
            settings = _load_settings()
            device_index = settings.get("mic_device_index", None)

            if device_index is None:
                # No saved preference — ask the user to pick one
                mics = _list_microphones()
                if not mics:
                    native_error_popup(
                        "Agetha — Microphone",
                        "No microphone devices found.\n"
                        "Make sure a microphone is connected and pyaudio is installed."
                    )
                    return
                picker = MicPickerDialog(self.root, mics)
                chosen = picker.wait()
                if chosen is None:
                    return   # user cancelled
                device_index = chosen
                mic_name = next((n for i, n in mics if i == chosen), str(chosen))
                settings["mic_device_index"] = device_index
                settings["mic_device_name"]  = mic_name
                _save_settings(settings)
                print(f"[Voice] Microphone saved: [{device_index}] {mic_name}")

            self._voice = VoiceInput(on_text_callback=self._on_voice_text,
                                     device_index=device_index)
            if not self._voice.available:
                native_error_popup("Agetha — Microphone", self._voice.error or "SpeechRecognition unavailable.")
                self._voice = None
                return

        if self._mic_active:
            self._mic_active = False
            if self._voice: self._voice.stop()
            self._mic_btn_var.set("🎤")
            self._mic_btn.config(bg=W95_BTN_BG, fg=W95_TEXT, activebackground=W95_BTN_BG)
            print("[Voice] Microphone off")
        else:
            self._mic_active = True
            if self._voice: self._voice.start()
            self._mic_btn_var.set("🔴")
            self._mic_btn.config(bg="#cc0000", fg="#ffffff", activebackground="#990000")
            print("[Voice] Microphone on — listening…")

    def _on_voice_text(self, text: str):
        """Called from VoiceInput thread when speech is transcribed."""
        # Show in input box briefly, then send
        def _send():
            self._input_var.set(text)
            self.root.update_idletasks()
            self._on_user_input()
        self.root.after(0, _send)

    # ── Drag ───────────────────────────────────────────────────────────────────

    def _drag_start(self, e):
        self._drag_x, self._drag_y = e.x_root, e.y_root

    def _drag_motion(self, e):
        dx = e.x_root - self._drag_x; dy = e.y_root - self._drag_y
        self.root.geometry(f"+{self.root.winfo_x()+dx}+{self.root.winfo_y()+dy}")
        self._drag_x, self._drag_y = e.x_root, e.y_root

    # ── Minimize ───────────────────────────────────────────────────────────────

    def _minimize(self):
        try:
            self.root.overrideredirect(False)
            self.root.iconify()
        except Exception:
            return
        def _bind_restore():
            def _on_map(event):
                try:
                    if self.root.state() != "iconic":
                        self.root.overrideredirect(True)
                        self.root.attributes("-topmost", True)
                        self.root.lift()
                        self.root.unbind("<Map>")
                except Exception:
                    pass
            self.root.bind("<Map>", _on_map)
        self.root.after(250, _bind_restore)

    # ── Close animation (wide → skinny → gone) ────────────────────────────────

    def _close_with_animation(self):
        """Quick close sequence: window flares wide, then squeezes down to a
        sliver, then vanishes — all fast — before actually quitting."""
        if getattr(self, "_closing", False):
            return
        self._closing = True

        try:
            self._stop_talking_rotation()
        except Exception:
            pass

        try:
            cur_x = self.root.winfo_x()
            cur_y = self.root.winfo_y()
            cur_w = self.root.winfo_width()  or WINDOW_W
            cur_h = self.root.winfo_height() or WINDOW_H
        except Exception:
            self.root.quit(); return

        cx = cur_x + cur_w // 2
        cy = cur_y + cur_h // 2

        wide_w   = int(cur_w * 1.35)
        wide_h   = int(cur_h * 0.9)
        skinny_w = max(2, int(cur_w * 0.04))
        skinny_h = cur_h

        WIDE_STEPS   = 4
        SKINNY_STEPS = 6
        STEP_MS      = 12  # very fast

        def _set_geom(w, h):
            x = cx - w // 2
            y = cy - h // 2
            try:
                self.root.geometry(f"{max(2,int(w))}x{max(2,int(h))}+{x}+{y}")
            except Exception:
                pass

        def _phase_wide(i=0):
            if i > WIDE_STEPS:
                self.root.after(0, lambda: _phase_skinny(0)); return
            t = i / WIDE_STEPS
            w = cur_w + (wide_w - cur_w) * t
            h = cur_h + (wide_h - cur_h) * t
            _set_geom(w, h)
            self.root.after(STEP_MS, lambda: _phase_wide(i + 1))

        def _phase_skinny(i=0):
            if i > SKINNY_STEPS:
                self.root.after(0, _phase_gone); return
            t = i / SKINNY_STEPS
            w = wide_w + (skinny_w - wide_w) * t
            h = wide_h + (skinny_h - wide_h) * t
            _set_geom(w, h)
            try:
                self.root.attributes("-alpha", max(0.0, 1.0 - t * 0.6))
            except Exception:
                pass
            self.root.after(STEP_MS, lambda: _phase_skinny(i + 1))

        def _phase_gone():
            try:
                self.root.attributes("-alpha", 0.0)
            except Exception:
                pass
            self.root.after(30, self.root.quit)

        _phase_wide(0)

    # ── Smooth window slide ────────────────────────────────────────────────────

    def _slide_window_to(self, tx: int, ty: int, on_done=None):
        """Animate the window smoothly from its current position to (tx, ty)."""
        if self._slide_job:
            try: self.root.after_cancel(self._slide_job)
            except Exception: pass
            self._slide_job = None

        sx = self.root.winfo_x()
        sy = self.root.winfo_y()
        steps = self._SLIDE_STEPS
        step_num = [0]

        def _step():
            n = step_num[0]
            if n >= steps:
                try: self.root.geometry(f"+{tx}+{ty}")
                except Exception: pass
                self._slide_job = None
                if on_done:
                    try: on_done()
                    except Exception: pass
                return
            # Ease-out cubic: t goes 0→1
            t = n / steps
            ease = 1 - (1 - t) ** 3
            cx = int(sx + (tx - sx) * ease)
            cy = int(sy + (ty - sy) * ease)
            try: self.root.geometry(f"+{cx}+{cy}")
            except Exception: pass
            step_num[0] += 1
            self._slide_job = self.root.after(self._SLIDE_INTERVAL, _step)

        _step()

    def _snap_to_center(self):
        """Instantly snap to center and pull to foreground."""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = self.root.winfo_width()  or WINDOW_W
        wh = self.root.winfo_height() or WINDOW_H
        cx = (sw - ww) // 2
        cy = (sh - wh) // 2
        try:
            self.root.geometry(f"+{cx}+{cy}")
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        print(f"[UI] Snapped to center: {cx},{cy}")

    def _shake_window(self, on_done=None):
        """Rapid shake animation — looks alarmed or excited."""
        if self._shake_job:
            try: self.root.after_cancel(self._shake_job)
            except Exception: pass
        ox = self.root.winfo_x()
        oy = self.root.winfo_y()
        step_num = [0]
        amp = self._SHAKE_AMPLITUDE

        def _step():
            n = step_num[0]
            if n >= self._SHAKE_STEPS:
                try: self.root.geometry(f"+{ox}+{oy}")
                except Exception: pass
                self._shake_job = None
                if on_done:
                    try: on_done()
                    except Exception: pass
                return
            # Decaying sinusoidal horizontal shake
            progress = n / self._SHAKE_STEPS
            decay = 1.0 - progress
            offset = int(math.sin(n * math.pi * 1.5) * amp * decay)
            try: self.root.geometry(f"+{ox + offset}+{oy}")
            except Exception: pass
            step_num[0] += 1
            self._shake_job = self.root.after(self._SHAKE_INTERVAL, _step)

        _step()

    def _bounce_window(self, on_done=None):
        """Quick vertical bounce — happy/excited feel."""
        if self._bounce_job:
            try: self.root.after_cancel(self._bounce_job)
            except Exception: pass
        ox = self.root.winfo_x()
        oy = self.root.winfo_y()
        step_num = [0]

        def _step():
            n = step_num[0]
            if n >= self._BOUNCE_STEPS:
                try: self.root.geometry(f"+{ox}+{oy}")
                except Exception: pass
                self._bounce_job = None
                if on_done:
                    try: on_done()
                    except Exception: pass
                return
            t = n / self._BOUNCE_STEPS
            # Two arcs: 0→0.5 go up, 0.5→1.0 come down
            arc = 1.0 - abs(2.0 * t - 1.0)
            offset = int(-arc * 18)  # negative = move up
            try: self.root.geometry(f"+{ox}+{oy + offset}")
            except Exception: pass
            step_num[0] += 1
            self._bounce_job = self.root.after(self._BOUNCE_INTERVAL, _step)

        _step()

    def _show_angry_glitch_screenshot(self):
        """Take a screenshot, apply pixel glitch distortion, show fullscreen for 0.5s. 30% chance."""
        if random.random() > 1:
            return
        def _do_glitch():
            try:
                import pyautogui
                import numpy as np
                from PIL import Image as _Image
                # Take screenshot
                shot = pyautogui.screenshot()
                img = shot.convert("RGB")
                arr = np.array(img)

                # Glitch: random horizontal slice shifts + colour channel swaps
                height, width = arr[:, :, 0].shape
                glitched = arr.copy()
                n_slices = random.randint(18, 40)
                for _ in range(n_slices):
                    y = random.randint(0, height - 1)
                    h = random.randint(2, max(3, height // 20))
                    shift = random.randint(-width // 6, width // 6)
                    y2 = min(y + h, height)
                    glitched[y:y2] = np.roll(glitched[y:y2], shift, axis=1)
                # Random channel swap on some slices
                n_channel = random.randint(6, 14)
                for _ in range(n_channel):
                    y = random.randint(0, height - 1)
                    h = random.randint(1, max(2, height // 30))
                    y2 = min(y + h, height)
                    c1, c2 = random.sample([0, 1, 2], 2)
                    glitched[y:y2, :, c1], glitched[y:y2, :, c2] = (
                        glitched[y:y2, :, c2].copy(), glitched[y:y2, :, c1].copy())
                # Random bright scan lines
                n_lines = random.randint(4, 12)
                for _ in range(n_lines):
                    y = random.randint(0, height - 1)
                    glitched[y] = [random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)]

                glitch_img = _Image.fromarray(glitched.astype(np.uint8))
                self.root.after(0, lambda: self._show_fullscreen_image_then_close(glitch_img, 500))
            except Exception as e:
                print(f"[GLITCH] Screenshot glitch failed: {e}")
        threading.Thread(target=_do_glitch, daemon=True).start()

    def _show_fullscreen_image_then_close(self, pil_img, duration_ms: int = 500):
        """Show a PIL image fullscreen, then destroy after duration_ms."""
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            resized = pil_img.resize((sw, sh), Image.LANCZOS)

            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.geometry(f"{sw}x{sh}+0+0")
            win.configure(bg="black")

            tk_img = ImageTk.PhotoImage(resized)
            lbl = tk.Label(win, image=tk_img, bd=0, bg="black")
            lbl.image = tk_img  # prevent GC
            lbl.pack(fill="both", expand=True)

            win.after(duration_ms, win.destroy)
        except Exception as e:
            print(f"[GLITCH] Fullscreen show failed: {e}")

    def _show_bsod_fullscreen(self):
        """Show bsod.png fullscreen (scaled to fit any monitor) for 2 seconds."""
        def _do():
            try:
                bsod_path = ASSETS / "bsod.png"
                if not bsod_path.exists():
                    # fallback: look next to main.py
                    bsod_path = BASE_DIR / "bsod.png"
                if not bsod_path.exists():
                    print("[BSOD] bsod.png not found — place it in assets/ or next to main.py")
                    return
                bsod_img = Image.open(str(bsod_path)).convert("RGB")
                self.root.after(0, lambda: self._show_fullscreen_image_then_close(bsod_img, 2000))
            except Exception as e:
                print(f"[BSOD] Failed to show BSOD: {e}")
        threading.Thread(target=_do, daemon=True).start()

    def _slide_to_random_position(self):
        """Slide to a random screen position — idle wandering behavior."""
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            ww = self.root.winfo_width()  or WINDOW_W
            wh = self.root.winfo_height() or WINDOW_H
            margin = 20
            nx = random.randint(margin, max(margin + 1, sw - ww - margin))
            ny = random.randint(margin, max(margin + 1, sh - wh - margin - 40))
            self._slide_window_to(nx, ny)
            print(f"[UI] Random wander to: {nx},{ny}")
        except Exception as e:
            print(f"[UI] Random wander failed: {e}")

    # ── Close button hover ──────────────────────────────────────────────────────

    def _on_close_hover(self, event=None):
        """User hovered over the close button — maybe dodge, maybe panic."""
        if not getattr(self, '_ai', None): return
        if self._state in (self.STATE_THINKING, self.STATE_SLEEPING): return
        # Debounce: only notify once per 8s
        now = time.time()
        last = getattr(self, '_last_close_hover_time', 0.0)
        if now - last < 8.0: return
        self._last_close_hover_time = now

        # 30% chance she doesn't react at all (lets user close)
        if random.random() < 0.30:
            print("[UI] Close hover — ignoring (letting user win)")
            return

        print("[UI] Close hover — Agetha noticed!")
        # Always do a quick shake first for drama
        self.root.after(0, self._shake_window)

        # Tell the AI about it (async, non-blocking)
        def _notify():
            if self._input_box["state"] == "disabled": return
            threading.Thread(
                target=self._ai_tick,
                kwargs={"user_message": "[system] close_hover"},
                daemon=True
            ).start()

        # Small delay so shake plays first
        self.root.after(200, _notify)

    def _on_close_leave(self, event=None):
        pass  # Reserved for future use

    # ── File drag and drop ─────────────────────────────────────────────────────

    def _on_file_drag_enter(self, event=None):
        """User is dragging a file over the gif."""
        if self._dragging_file: return
        self._dragging_file = True
        print("[DnD] File drag enter")
        # Play want.gif while hovering
        want_player = self._gif_cache.get("want.gif")
        if want_player:
            if self._current_gif_player:
                self._current_gif_player.stop()
            self._current_gif_player = want_player
            want_player.play()

    def _on_file_drag_leave(self, event=None):
        """User dragged the file away without dropping."""
        if not self._dragging_file: return
        self._dragging_file = False
        print("[DnD] File drag leave")
        # Return to idle state
        self._set_state(self.STATE_IDLE)

    def _on_file_drop(self, event=None):
        """User dropped a file onto the gif."""
        self._dragging_file = False
        try:
            # tkinterdnd2 passes file path in event.data
            file_path = getattr(event, 'data', '') or ''
            file_path = file_path.strip().strip('{}')  # Windows wraps paths with spaces in {}
            filename = Path(file_path).name if file_path else "unknown file"
        except Exception:
            filename = "a file"
            file_path = ""
        print(f"[DnD] File dropped: {filename} at {file_path}")

        # Store the dragged file path for AI to use in commands
        self._last_dragged_file = file_path if file_path else filename

        # Return to normal state first
        self._set_state(self.STATE_IDLE)

        if self._input_box["state"] == "disabled": return
        # Pass both filename and full path to the AI so it knows exactly what file was dragged
        msg = f"[system] file_dragged: \"{filename}\" (path: {file_path})" if file_path else f"[system] file_dragged: \"{filename}\""
        threading.Thread(target=self._ai_tick, kwargs={"user_message": msg}, daemon=True).start()

    # ── GIF / state ────────────────────────────────────────────────────────────

    def _on_gif_click(self, event=None):
        now = time.time()
        if now - self._last_touch_time < 10.0: return
        self._last_touch_time = now
        if self._input_box["state"] == "disabled": return
        self._persistent_mood = None
        threading.Thread(target=self._ai_tick, kwargs={"user_message": "__touch__"}, daemon=True).start()

    def _on_user_input(self, event=None):
        text = self._input_var.get().strip()
        if not text: return
        if self._input_box["state"] == "disabled": return
        self._input_var.set("")
        self._input_box.config(state="disabled")
        self._placeholder_lbl.config(text="Processing...")
        self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._persistent_mood = None
        threading.Thread(target=self._ai_tick, kwargs={"user_message": text}, daemon=True).start()

    def _re_enable_input(self):
        self._input_box.config(state="normal")
        self._input_box.focus_set()
        try:
            self._update_placeholder()
        except Exception:
            pass

    def _play_gif(self, name: str):
        if self._current_gif_player:
            self._current_gif_player.stop()
        player = self._gif_cache.get(name)
        if player:
            self._current_gif_player = player
            player.play()
        else:
            print(f"[WARN] GIF not loaded: {name}")

    def _play_gif_once_then(self, anim_name: str, static_name: str, guard=None):
        player = self._gif_cache.get(anim_name)
        static = self._gif_cache.get(static_name)
        if not player: return
        if self._current_gif_player and self._current_gif_player is not player:
            self._current_gif_player.stop()
        self._current_gif_player = player
        def _done():
            if guard is None or guard():
                if static:
                    if self._current_gif_player: self._current_gif_player.stop()
                    self._current_gif_player = static
                    static.play()
        player.play_once(lambda: self.root.after(0, _done))

    def _play_gif_once_then_loop(self, anim_name: str, mood: str):
        player = self._gif_cache.get(anim_name)
        if not player:
            self._start_talking_rotation(); return
        if self._current_gif_player and self._current_gif_player is not player:
            self._current_gif_player.stop()
        self._current_gif_player = player
        def _done():
            if self._state == self.STATE_TALKING and self._persistent_mood == mood:
                if self._current_gif_player: self._current_gif_player.stop()
                self._current_gif_player = player
                player.play()
        player.play_once(lambda: self.root.after(0, _done))

    def _start_talking_rotation(self):
        self._rotate_talking()

    def _rotate_talking(self):
        if self._state != self.STATE_TALKING: return
        available = [g for g in self.TALKING_GIFS if g in self._gif_cache]
        if available: self._play_gif(random.choice(available))
        delay = random.randint(1800, 3200)
        self._talking_rotate_job = self.root.after(delay, self._rotate_talking)

    def _stop_talking_rotation(self):
        if self._talking_rotate_job:
            self.root.after_cancel(self._talking_rotate_job)
            self._talking_rotate_job = None

    def _update_token_status(self):
        """Update status bar with Groq token usage info."""
        try:
            if not self._ai:
                return
            status = self._ai.get_token_status()
            if status.get("using_groq"):
                key_info = f"Key {status['key_index']}/{status['key_count']}"
                pct = status.get("pct_left", 0)
                self._status_var.set(f"{key_info} | {pct}% left")
            else:
                # Local AI or no Groq
                self._status_var.set("")
        except Exception as e:
            print(f"[Token Status] Error: {e}")

    # ── Reactive glow effect (eye-candy) ───────────────────────────────────────
    # A soft pulsing colored border around the GIF frame that reacts to mood/state,
    # making Agetha feel more alive/reactive at a glance.

    _GLOW_COLORS = {
        "neutral":   "#4488cc",
        "happy":     "#ffd24d",
        "excited":   "#ff9d1a",
        "sad":       "#5566aa",
        "surprised": "#cc66ff",
        "thinking":  "#33cccc",
        "whisper":   "#8899aa",
        "angry":     "#ff3b3b",
        "sleeping":  "#222233",
    }

    @staticmethod
    def _lerp_color(c1: str, c2: str, t: float) -> str:
        c1 = c1.lstrip("#"); c2 = c2.lstrip("#")
        r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
        r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
        r = int(r1 + (r2 - r1) * t); g = int(g1 + (g2 - g1) * t); b = int(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _set_glow_mood(self, mood: str):
        self._glow_mood = mood if mood in self._GLOW_COLORS else "neutral"
        if not self._glow_active:
            self._glow_active = True
            self._glow_tick()

    def _glow_tick(self):
        if not getattr(self, "_glow_active", False):
            return
        try:
            base = self._GLOW_COLORS.get(self._glow_mood, "#4488cc")
            # breathing pulse: oscillate brightness via lerp toward black/white
            self._glow_phase += 0.12
            pulse = (math.sin(self._glow_phase) + 1) / 2  # 0..1
            speed_boost = 1.0 if self._state != self.STATE_TALKING else 1.8
            self._glow_phase += 0.0  # (kept for clarity; speed applied via increment below)
            lit_color = self._lerp_color("#000000", base, 0.35 + pulse * 0.65)
            self._gif_border.configure(highlightbackground=lit_color, highlightcolor=lit_color)
        except Exception:
            pass
        interval = 55 if self._state == self.STATE_TALKING else 90
        self.root.after(interval, self._glow_tick)

    def _set_state(self, state: str, mood: str = "neutral"):
        # Cancel loaf timer on any state change
        try:
            if getattr(self, "_loaf_job", None):
                self.root.after_cancel(self._loaf_job)
                self._loaf_job = None
        except Exception:
            self._loaf_job = None
        try:
            if getattr(self, "_is_loafing", False):
                self._is_loafing = False
        except Exception:
            self._is_loafing = False

        self._state = state
        self._status_var.set("")
        self._stop_talking_rotation()
        if self._bleep:
            try: self._bleep.stop()
            except Exception: pass

        _STICKY_MOODS = {"sad", "angry", "happy", "thinking"}

        if state == self.STATE_SLEEPING:
            self._persistent_mood = None
            self._play_gif("sleeping.gif")
            self._set_glow_mood("sleeping")

        elif state == self.STATE_THINKING:
            self._persistent_mood = None
            self._play_gif_once_then("thinking.gif", "thinking-static.gif",
                                     guard=lambda: self._state == self.STATE_THINKING)
            self._set_glow_mood("thinking")

        elif state == self.STATE_IDLE:
            effective_mood = self._persistent_mood if self._persistent_mood else mood
            self._set_glow_mood(effective_mood)
            static_name = None
            try: static_name = self.EXTRA_STATIC_GIFS.get(effective_mood)
            except Exception: pass

            if static_name and static_name in self._gif_cache:
                self._play_gif(static_name)
            else:
                mood_gif = self.EXTRA_GIFS.get(effective_mood)
                if mood_gif and mood_gif in self._gif_cache:
                    self._play_gif(mood_gif)
                else:
                    available = [g for g in self.IDLE_GIFS if g in self._gif_cache]
                    if available: self._play_gif(random.choice(available))

        elif state == self.STATE_TALKING:
            if mood in _STICKY_MOODS: self._persistent_mood = mood
            else: self._persistent_mood = None
            self._set_glow_mood(mood)
            if mood in ("excited", "surprised", "angry", "happy"):
                try: self._bounce_window()
                except Exception: pass
            mood_gif    = self.EXTRA_GIFS.get(mood)
            static_name = self.EXTRA_STATIC_GIFS.get(mood)
            if mood != "neutral" and mood_gif and mood_gif in self._gif_cache:
                if static_name and static_name in self._gif_cache:
                    self._play_gif_once_then_loop(mood_gif, mood)
                else:
                    self._play_gif(mood_gif)
            else:
                self._start_talking_rotation()
            if self._bleep:
                self._bleep.start_talking(tone=mood)

    def _enter_loaf(self):
        """Enter loaf.gif state after 10 minutes of idle."""
        try:
            if self._state == self.STATE_IDLE:
                loaf_player = self._gif_cache.get("loaf.gif")
                if loaf_player:
                    if self._current_gif_player:
                        self._current_gif_player.stop()
                    self._current_gif_player = loaf_player
                    loaf_player.play()
                    self._is_loafing = True
                    print("[UI] Entered loaf state")
                else:
                    print("[WARN] loaf.gif not in cache — cannot enter loaf state")
        except Exception as e:
            print(f"[UI] _enter_loaf error: {e}")

    def _enter_loaf_persistent(self):
        """Enter loaf.gif and stay there until the AI gives a real response."""
        try:
            loaf_player = self._gif_cache.get("loaf.gif")
            if loaf_player:
                if self._current_gif_player:
                    self._current_gif_player.stop()
                self._current_gif_player = loaf_player
                loaf_player.play()
                self._is_loafing = True
                print("[UI] Entered persistent loaf state (5+ consecutive idles)")
            else:
                print("[WARN] loaf.gif not in cache")
        except Exception as e:
            print(f"[UI] _enter_loaf_persistent error: {e}")
        # Don't reschedule poll — stay loafing until user interacts

    # ── Background init ────────────────────────────────────────────────────────

    def _init_background(self):
        try:
            bleep = screen = ai = None

            def _show_error_popup(lines: list):
                native_error_popup("Agetha — Error", "\n".join(lines))

            try:
                bleep = BleepPlayer()
            except Exception as e:
                print(f"[BackgroundInit] Bleep init failed: {e}")
                native_error_popup("Agetha — Audio Error", f"Audio init failed:\n{e}")
            try: self._advance_progress("Audio engine ready…")
            except Exception: pass

            try:
                _ocr_focused = True
                try:
                    _base = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent
                    _cfg_p = _base / "config.txt"
                    if _cfg_p.exists():
                        for _ln in _cfg_p.read_text(encoding="utf-8", errors="replace").splitlines():
                            _s = _ln.strip()
                            if _s.startswith("#") or "=" not in _s: continue
                            _k, _v = _s.split("=", 1)
                            if _k.strip().upper() == "OCR_FOCUSED_WINDOW":
                                _ocr_focused = _v.strip().lower() in ("yes", "true", "1", "on")
                except Exception:
                    pass
                screen = ScreenReader(ocr_focused_window=_ocr_focused)
            except Exception as e:
                print(f"[BackgroundInit] ScreenReader init failed: {e}")
                native_error_popup("Agetha — Screen Reader Error", f"Screen reader failed:\n{e}")
            try: self._advance_progress("Screen reader ready…")
            except Exception: pass

            try:
                ai = AIEngine(on_error=_show_error_popup)
            except Exception as e:
                print(f"[BackgroundInit] AIEngine init failed: {e}")
                native_error_popup("Agetha — AI Engine Error", f"AI engine failed:\n{e}")
            try: self._advance_progress("AI engine ready…")
            except Exception: pass

            def _finish():
                try:
                    self._bleep  = bleep
                    self._screen = screen
                    self._ai     = ai
                    try:
                        if hasattr(self, "_subtitle") and self._subtitle:
                            self._subtitle._bleep = self._bleep
                    except Exception:
                        pass
                    try:
                        self._preload_gifs()
                    except Exception as e:
                        print(f"[BackgroundInit] preload_gifs failed: {e}")
                        native_error_popup("Agetha — Asset Error", f"Failed to load GIF assets:\n{e}")
                    # Show initial placeholder with key info now that AI is ready
                    try:
                        self._update_placeholder()
                        self._start_placeholder_refresh()
                    except Exception:
                        pass
                except Exception:
                    pass

            try: self.root.after(0, _finish)
            except Exception: _finish()
        except Exception as e:
            print(f"[BackgroundInit] Unexpected error: {e}")
            try: self.root.after(0, self._hide_desktop_loading_indicator)
            except Exception: pass
            native_error_popup("Agetha — Unexpected Error", f"Unexpected startup error:\n{e}")

    def _show_desktop_loading_indicator(self):
        """Show thinking-static.gif pinned to the bottom-right corner of the
        DESKTOP (not Agetha's window) while the program is loading."""
        self._desktop_loading_win = None
        try:
            gif_path = ASSETS / "thinking-static.gif"
            if not gif_path.exists():
                print(f"[LoadingIndicator] Missing asset: {gif_path}")
                return

            img = Image.open(gif_path).convert("RGBA")
            img.thumbnail((96, 96), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-transparentcolor", "#010101")
                win.configure(bg="#010101")
                bg = "#010101"
            except Exception:
                win.configure(bg=W95_BG)
                bg = W95_BG

            lbl = tk.Label(win, image=photo, bg=bg, bd=0)
            lbl.image = photo  # keep reference
            lbl.pack()

            win.update_idletasks()
            sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
            ww = img.width; wh = img.height
            margin = 12
            x = sw - ww - margin
            y = sh - wh - margin
            win.geometry(f"{ww}x{wh}+{x}+{y}")

            self._desktop_loading_win = win
        except Exception as e:
            print(f"[LoadingIndicator] Failed to show desktop indicator: {e}")

    def _hide_desktop_loading_indicator(self):
        win = getattr(self, "_desktop_loading_win", None)
        if win is not None:
            try: win.destroy()
            except Exception: pass
            self._desktop_loading_win = None

    def _preload_gifs(self):
        static_vals = list(self.EXTRA_STATIC_GIFS.values()) if getattr(self, 'EXTRA_STATIC_GIFS', None) else []
        all_names = list(dict.fromkeys(
            self.IDLE_GIFS + self.TALKING_GIFS + list(self.EXTRA_GIFS.values()) + static_vals
        ))

        def _phase1():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            results: dict[str, tuple[list, list]] = {}
            missing: list[str] = []
            try:
                self.root.after(0, lambda: setattr(self, '_load_total', 3 + len(all_names)))
            except Exception:
                pass
            to_load = []
            for name in all_names:
                asset_path = ASSETS / name
                if asset_path.exists():
                    to_load.append((name, str(asset_path)))
                else:
                    print(f"[WARN] Missing asset: {asset_path}")
                    missing.append(name)
                    try: self._advance_progress(f"Missing {name}…")
                    except Exception: pass

            n_workers = min(len(to_load), 8) if to_load else 1

            def _load_one(name_and_path):
                n, p = name_and_path
                frames, delays = _load_gif_frames_offthread(p)
                try: self._advance_progress(f"Loading {n}…")
                except Exception: pass
                return n, frames, delays

            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = {pool.submit(_load_one, item): item[0] for item in to_load}
                for fut in as_completed(futures):
                    try:
                        n, frames, delays = fut.result()
                        results[n] = (frames, delays)
                    except Exception as e:
                        print(f"[GifPlayer] Failed to load {futures[fut]}: {e}")

            self.root.after(0, lambda: _phase2(results, missing))

        def _phase2(results: dict, missing: list):
            for name, (pil_frames, delays) in results.items():
                try:
                    self._gif_cache[name] = GifPlayer(
                        self._gif_label, name, self.root.after,
                        pil_frames=pil_frames, delays=delays)
                except Exception as e:
                    print(f"[GifPlayer] Failed to create player for {name}: {e}")

            if missing:
                msg = "Missing asset files:\n" + "\n".join(missing[:8])
                if len(missing) > 8: msg += f"\n...and {len(missing)-8} more."
                native_error_popup("Agetha — Missing Assets", msg)

            try:
                if hasattr(self, "_loading_label") and self._loading_label:
                    self._loading_label.destroy()
                    self._loading_label = None
            except Exception:
                pass
            try:
                self._hide_desktop_loading_indicator()
            except Exception:
                pass
            try:
                self._start_wake_sequence()
            except Exception as e:
                print(f"[BackgroundInit] start_wake_sequence failed: {e}")
                native_error_popup("Agetha — Startup Error", f"Startup sequence failed:\n{e}")

        threading.Thread(target=_phase1, daemon=True).start()

    def _start_wake_sequence(self):
        self._set_state(self.STATE_SLEEPING)
        # Glitch effect on startup
        _run_glitch_effect(self.root, duration_ms=1800)
        self.root.after(8000, self._finish_wake)

    def _finish_wake(self):
        self._set_state(self.STATE_IDLE, "neutral")
        self.root.after(1000, self._schedule_screen_poll)

    # ── Poll ───────────────────────────────────────────────────────────────────

    def _schedule_screen_poll(self):
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        threading.Thread(target=self._ai_tick, daemon=True).start()

    def _reschedule_screen_poll(self):
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
        self._poll_job = self.root.after(SCREEN_POLL_INTERVAL_MS, self._schedule_screen_poll)

    # ── AI tick ────────────────────────────────────────────────────────────────

    def _ai_tick(self, user_message: str | None = None):
        is_user = user_message is not None
        self.root.after(0, lambda: self._input_box.config(state="disabled"))

        screen_text = ""
        if not is_user:
            # Randomly wander to a new position on some ambient polls (~25% of the time)
            if random.random() < 0.25 and self._state == self.STATE_IDLE:
                self.root.after(0, self._slide_to_random_position)
            screen_text = self._screen.capture_text()
            self._last_screen_text = screen_text

        if is_user:
            self.root.after(0, lambda: self._set_state(self.STATE_THINKING))

        def _on_token(raw_so_far: str):
            if is_user:
                self._subtitle.show_thinking(raw_so_far)

        try:
            response = self._ai.query_streaming(
                screen_context=screen_text if not is_user else self._last_screen_text,
                user_message=user_message or "",
                on_token=_on_token,
            )
        except Exception as exc:
            err_str = str(exc)
            print(f"[AI_TICK] Unhandled exception: {err_str}")
            _groq_limit_keywords = ("rate_limit", "rate limit", "429", "quota", "groq_exhausted")
            is_groq_limit = any(kw in err_str.lower() for kw in _groq_limit_keywords)
            _connection_keywords = ("connection", "network", "timeout", "unreachable", "eoferror", "ssl")
            is_connection_error = any(kw in err_str.lower() for kw in _connection_keywords)
            if is_connection_error:
                # Connection error → show error.gif permanently
                def _show_err_gif():
                    path = str(ASSETS / "error.gif")
                    try:
                        from PIL import Image as _Img, ImageSequence as _IS
                        from pathlib import Path as _Path
                        gif_path = _Path(path)
                        if not gif_path.exists():
                            return
                        player = GifPlayer(self._gif_label, path, self.root.after)
                        if self._current_gif_player:
                            self._current_gif_player.stop()
                        self._current_gif_player = player
                        player.play()
                        self._status_var.set("connection error")
                    except Exception as eg:
                        print(f"[ERROR_GIF] {eg}")
                self.root.after(0, _show_err_gif)
                self.root.after(0, self._re_enable_input)
                return
            if not is_groq_limit:
                _short = err_str[:200]
                native_error_popup("Agetha — Error", f"An error occurred:\n{_short}")
            self.root.after(0, self._re_enable_input)
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
            self._reschedule_screen_poll()
            return

        print("\n" + "─" * 52)
        if user_message and user_message != "__touch__":
            print(f"[USER]  {user_message}")
        print(f"[AI]    {json.dumps(response, ensure_ascii=False)}")
        print("─" * 52)

        self.root.after(0, self._re_enable_input)
        self.root.after(0, self._update_token_status)
        self._dispatch_response(response, user_message)

    # ── Response dispatcher ────────────────────────────────────────────────────

    def _dispatch_response(self, response: dict, user_message: str | None = None):
        try: self.root.after(0, lambda: self._subtitle.clear())
        except Exception: pass

        command           = response.get("command", "idle")
        mood              = response.get("mood", "neutral")
        segments          = response.get("segments", [])
        popup_msgs        = response.get("popup", None)
        shutdown_requested = bool(response.get("shutdown", False))
        persistent_loaf   = bool(response.get("_persistent_loaf", False))

        if response.get("groq_exhausted"):
            self.root.after(0, lambda: self._subtitle.show_message(
                "You reached your limit with your Groq keys", "#ff4444"))
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
            self._reschedule_screen_poll()
            return

        def _speak_and_continue(resp_segments, resp_mood, resp_shutdown):
            if resp_segments:
                self.root.after(0, lambda: self._set_state(self.STATE_TALKING, resp_mood))
                # Hardcoded animation reactions for expressive moods
                if resp_mood == "surprised":
                    self.root.after(0, self._shake_window)
                elif resp_mood in ("excited", "happy"):
                    self.root.after(0, self._bounce_window)
                elif resp_mood == "angry":
                    self.root.after(0, self._shake_window)
                    self.root.after(100, self._show_angry_glitch_screenshot)
                self.root.after(0, lambda: self._subtitle.speak(
                    resp_segments, on_done=lambda: self._on_speech_done(resp_shutdown)))
            else:
                self.root.after(0, lambda: self._set_state(self.STATE_IDLE, resp_mood))
                self._reschedule_screen_poll()

        # Short-response static gif optimisation
        try:
            short_moods = {"happy", "excited", "surprised"}
            is_short = (command == "speak" and isinstance(segments, list) and len(segments) == 1 and
                        len(segments[0].get("text","").split()) <= 6)
            if is_short and mood in short_moods:
                static_name = (self.EXTRA_STATIC_GIFS.get(mood) if getattr(self, 'EXTRA_STATIC_GIFS', None) else None)
                if not static_name and mood in ("excited","surprised"):
                    static_name = (self.EXTRA_STATIC_GIFS.get("happy") if getattr(self, 'EXTRA_STATIC_GIFS', None) else None)
                if static_name and static_name in self._gif_cache:
                    self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
                    self.root.after(12, lambda: self._play_gif(static_name))
                    self.root.after(0, lambda: self._subtitle.speak(
                        segments, on_done=lambda: self._on_speech_done(shutdown_requested)))
                    return
        except Exception:
            pass

        # ── show_error_gif ──
        if command == "show_error_gif":
            path = response.get("path", "") or str(ASSETS / "error.gif")
            try:
                gif_path = Path(path)
                if not gif_path.exists(): gif_path = ASSETS / "error.gif"
                name = str(gif_path)
                player = GifPlayer(self._gif_label, name, self.root.after)
                if self._current_gif_player: self._current_gif_player.stop()
                self._current_gif_player = player
                player.play()
                self._status_var.set("connection error")
                # Do NOT reschedule poll — stay on error.gif permanently
                return
            except Exception as e:
                print(f"[ERROR_GIF] Failed to show error gif: {e}")

        # ── snap_to_center ──
        if command == "snap_to_center":
            self.root.after(0, self._snap_to_center)
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── move_window (with smooth slide) ──
        if command == "move_window":
            try:
                x   = response.get("x", None)
                y   = response.get("y", None)
                direction = (response.get("direction","") or "").lower()
                sw  = self.root.winfo_screenwidth()
                sh  = self.root.winfo_screenheight()
                ww  = self.root.winfo_width()  or WINDOW_W
                wh  = self.root.winfo_height() or WINDOW_H

                if x is not None and y is not None:
                    nx, ny = int(x), int(y)
                else:
                    curx = self.root.winfo_x(); cury = self.root.winfo_y()
                    if direction == "left":   nx, ny = 10, cury
                    elif direction == "right": nx, ny = max(0, sw - ww - 10), cury
                    elif direction == "up":    nx, ny = curx, 10
                    elif direction == "down":  nx, ny = curx, max(0, sh - wh - 50)
                    elif direction == "center":
                        nx = max(0, (sw - ww) // 2); ny = max(0, (sh - wh) // 2)
                    else:
                        nx, ny = 10, cury

                # Smooth slide to new position
                self.root.after(0, lambda _nx=nx, _ny=ny: self._slide_window_to(_nx, _ny))
                # Small shake at destination for personality
                if random.random() < 0.4:
                    self.root.after(self._SLIDE_STEPS * self._SLIDE_INTERVAL + 50,
                                    self._shake_window)
                print(f"[UI] Sliding window to: {nx},{ny}")
            except Exception as e:
                print(f"[UI] Failed to move window: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── monitor_process ──
        if command == "monitor_process":
            process_name = (response.get("process","") or "").strip()
            if process_name:
                def _check_proc():
                    import subprocess as _sp
                    running = False
                    try:
                        if platform.system() == "Windows":
                            r = _sp.run(["tasklist", "/FI", f"IMAGENAME eq {process_name}"],
                                        capture_output=True, text=True, timeout=5)
                            running = process_name.lower() in r.stdout.lower()
                        else:
                            r = _sp.run(["pgrep", "-f", process_name], capture_output=True, timeout=5)
                            running = r.returncode == 0
                    except Exception:
                        running = False

                    status = f"RUNNING" if running else "NOT RUNNING"
                    print(f"[PROC] {process_name}: {status}")
                    # Feed result back as a new user message so AI can react
                    feedback = f"[system] process_status: {process_name} is {status.lower()}"
                    self.root.after(0, self._re_enable_input)
                    self.root.after(500, lambda: threading.Thread(
                        target=self._ai_tick, kwargs={"user_message": feedback}, daemon=True).start())

                threading.Thread(target=_check_proc, daemon=True).start()
                _speak_and_continue(segments, mood, shutdown_requested)
                return

        # ── request_path ──
        if command == "request_path":
            hint = response.get("path_hint","").strip()
            lines = [hint] if hint else (
                [seg.get("text","") for seg in segments if seg.get("text","")] or ["Path resolved automatically."])
            self.root.after(0, lambda: AgethaPopup(self.root, lines[:4], mood))
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        # ── create_folder ──
        if command == "create_folder":
            path = response.get("path","").strip()
            if path:
                try: os.makedirs(path, exist_ok=True); print(f"[FS] Created folder: {path}")
                except Exception as e: print(f"[FS] Failed to create folder {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── create_file ──
        if command == "create_file":
            file_path = response.get("file_path","").strip()
            if not file_path:
                path = response.get("path","").strip()
                file_name = response.get("file_name","").strip()
                if path and file_name: file_path = os.path.join(path, file_name)
            content = response.get("content","")
            if file_path:
                try:
                    parent = os.path.dirname(file_path)
                    if parent: os.makedirs(parent, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as f: f.write(content)
                    print(f"[FS] Created file: {file_path}")
                except Exception as e:
                    print(f"[FS] Failed to create file {file_path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── delete_file ──
        if command == "delete_file":
            path = response.get("path","").strip()
            if path:
                import shutil
                try:
                    p = Path(path)
                    if p.is_dir(): shutil.rmtree(p); print(f"[FS] Deleted folder: {path}")
                    elif p.exists(): p.unlink(); print(f"[FS] Deleted file: {path}")
                except Exception as e: print(f"[FS] Failed to delete {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── rename_file ──
        if command == "rename_file":
            path = response.get("path","").strip()
            new_name = response.get("new_name","").strip()
            if path and new_name:
                try:
                    p = Path(path); p.rename(p.parent / new_name); print(f"[FS] Renamed: {path}")
                except Exception as e: print(f"[FS] Failed to rename {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── list_dir ──
        if command in ("list_dir", "list_directory"):
            req_path = response.get("path","").strip() or str(self._ai._system_path)
            try:
                p = Path(req_path)
                if not p.exists(): lines = [f"[not found: {req_path}]"]
                elif not p.is_dir(): lines = [f"[not a directory: {req_path}]"]
                else:
                    entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
                    lines = [e.name + ("/" if e.is_dir() else "") for e in entries] or ["[empty directory]"]
            except Exception as e: lines = [f"[error listing: {e}]"]
            self.root.after(0, lambda: AgethaPopup(self.root, lines[:12], mood))
            if not segments: segments = [{"text": f"{len(lines)} items in {req_path}", "pause": 0.0}]
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── set_clipboard ──
        if command == "set_clipboard":
            text = response.get("text","").strip()
            if text:
                try:
                    self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()
                    print(f"[CLIP] Set: {text[:60]}")
                except Exception as e: print(f"[CLIP] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── play_sound ──
        if command == "play_sound":
            sound_name = response.get("sound","beep").strip().lower()
            _sound_map = {"beep": 440, "chime": 880, "error": 185, "notify": 523}
            freq = _sound_map.get(sound_name, 440)
            try:
                tone = {v: k for k, v in BLEEP_TONES.items()}.get(freq, "neutral")
                self._bleep.start_talking(tone=tone)
                threading.Timer(0.8, self._bleep.stop).start()
            except Exception as e: print(f"[SOUND] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── take_screenshot ──
        if command == "take_screenshot":
            save_path = response.get("save_path","").strip()
            if not save_path:
                import datetime as _dt
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = os.path.join(self._ai._system_path, f"screenshot_{ts}.png")
            try:
                img = self._screen.capture_image()
                if img: img.save(save_path); print(f"[SCREEN] Screenshot saved: {save_path}")
            except Exception as e: print(f"[SCREEN] Failed to save screenshot: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── show_notification ──
        if command == "show_notification":
            title   = response.get("title","Agetha").strip()
            message = response.get("message","").strip()
            if message:
                try:
                    _sys = platform.system()
                    import subprocess as _sp
                    if _sys == "Darwin":
                        _sp.Popen(["osascript", "-e", f'display notification "{message}" with title "{title}"'])
                    elif _sys == "Linux":
                        _sp.Popen(["notify-send", title, message])
                    elif _sys == "Windows":
                        ps = (f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, '
                              f'ContentType = WindowsRuntime] > $null;$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;'
                              f'$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t);'
                              f'$x.GetElementsByTagName("text")[0].AppendChild($x.CreateTextNode("{title}"));'
                              f'$x.GetElementsByTagName("text")[1].AppendChild($x.CreateTextNode("{message}"));'
                              f'$n = [Windows.UI.Notifications.ToastNotification]::new($x);'
                              f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Agetha").Show($n);')
                        _sp.Popen(["powershell", "-Command", ps], shell=False)
                except Exception as e: print(f"[NOTIFY] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── fake_crash ──
        if command == "fake_crash":
            self.root.after(0, self._show_bsod_fullscreen)
            # Let speech finish normally after the BSOD disappears
            if segments:
                self.root.after(2200, lambda: _speak_and_continue(segments, mood, shutdown_requested))
            else:
                self.root.after(2200, lambda: self._set_state(self.STATE_IDLE, mood))
                self.root.after(2200, self._reschedule_screen_poll)
            return

        # ── run_command ──
        if command == "run_command":
            cmd_str = response.get("cmd","").strip()
            use_shell = bool(response.get("shell", True))
            if cmd_str:
                try:
                    import subprocess as _sp
                    result_proc = _sp.run(cmd_str, shell=use_shell, capture_output=True,
                                          text=True, timeout=15)
                    out = (result_proc.stdout or "").strip()
                    err = (result_proc.stderr or "").strip()
                    print(f"[CMD] Ran: {cmd_str}")
                    if out: print(f"[CMD] stdout: {out[:200]}")
                    if err: print(f"[CMD] stderr: {err[:200]}")
                except Exception as e: print(f"[CMD] Failed to run '{cmd_str}': {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── read_document ──
        if command == "read_document":
            doc_path = response.get("path","").strip()
            doc_content = self._ai.read_document(doc_path) if doc_path else "[no path provided]"
            print(f"[DOC] Read '{doc_path}': {doc_content[:80]}")
            def _requery_with_doc():
                self.root.after(0, lambda: self._set_state(self.STATE_THINKING))
                def _on_token(raw_so_far: str): self._subtitle.show_thinking(raw_so_far)
                follow = self._ai.query_streaming(screen_context=self._last_screen_text,
                                                  user_message="", doc_content=doc_content,
                                                  on_token=_on_token)
                print(f"[AI]    {json.dumps(follow, ensure_ascii=False)}")
                self._dispatch_response(follow, user_message)
            threading.Thread(target=_requery_with_doc, daemon=True).start()
            return

        # ── open_app ──
        if command == "open_app":
            app_name = response.get("app","").strip()
            if app_name:
                print(f"[APP] Opening {app_name}...")
                try:
                    import subprocess as _sp
                    if platform.system() == "Windows":
                        try: os.startfile(app_name)
                        except OSError: _sp.Popen([app_name])
                    elif platform.system() == "Darwin": _sp.Popen(["open", app_name])
                    else: _sp.Popen([app_name])
                except Exception as e: print(f"[APP] Failed to open {app_name}: {e}")
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        # ── force_close ──
        if command == "force_close":
            target = (response.get("app","") or response.get("process","") or response.get("name","")).strip()
            if target:
                try:
                    import subprocess as _sp
                    if platform.system() == "Windows":
                        _sp.run(["taskkill", "/IM", os.path.basename(target), "/F"], capture_output=True, check=False)
                    else:
                        _sp.run(["pkill", "-f", target], check=False)
                    print(f"[APP] Force-closed: {target}")
                except Exception as e: print(f"[APP] Failed to force-close {target}: {e}")
            if not segments: segments = [{"text": "Talk to me.", "pause": 0.0}]
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        # ── open_browser ──
        if command == "open_browser":
            url    = response.get("url","").strip()
            search = response.get("search","").strip()
            engine = response.get("engine","google").strip()
            if not url and search:
                _engines = {"google":     "https://www.google.com/search?q=",
                            "duckduckgo": "https://duckduckgo.com/?q=",
                            "bing":       "https://www.bing.com/search?q="}
                url = _engines.get(engine, _engines["google"]) + search.replace(" ","+")
            if url:
                try: webbrowser.open(url)
                except Exception as e: print(f"[BROWSER] Failed: {e}")
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        # ── request_screen_read ──
        if command == "request_screen_read":
            print("[SCREEN] AI requesting screen read...")
            screen_text = self._screen.capture_text()
            self._last_screen_text = screen_text
            print(f"[SCREEN] Captured {len(screen_text)} chars")
            def _requery_with_screen():
                self.root.after(0, lambda: self._set_state(self.STATE_THINKING))
                def _on_token(raw_so_far: str): self._subtitle.show_thinking(raw_so_far)
                follow = self._ai.query_streaming(screen_context=screen_text,
                                                  user_message=user_message or "",
                                                  on_token=_on_token)
                print(f"[AI]    {json.dumps(follow, ensure_ascii=False)}")
                self._dispatch_response(follow, user_message)
            threading.Thread(target=_requery_with_screen, daemon=True).start()
            return

        # ── popup ──
        if popup_msgs and isinstance(popup_msgs, list) and len(popup_msgs) > 0:
            self.root.after(0, lambda: AgethaPopup(self.root, popup_msgs, mood))
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        # ── wake_user / speak / idle ──
        if command == "wake_user" and segments:
            self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
            self.root.after(0, lambda: self._subtitle.speak(
                segments, on_done=lambda: self._on_speech_done(shutdown_requested)))
        elif command == "speak" and segments:
            try: self.root.after(0, lambda: self._subtitle.clear())
            except Exception: pass
            self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
            self.root.after(0, lambda: self._subtitle.speak(
                segments, on_done=lambda: self._on_speech_done(shutdown_requested)))
        else:
            self._persistent_mood = None
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            if persistent_loaf:
                # Stay in loaf — enter it immediately and don't reschedule poll
                self.root.after(100, self._enter_loaf_persistent)
            else:
                self._reschedule_screen_poll()

    def _on_speech_done(self, shutdown: bool = False):
        # AI spoke — exit loaf state and reset consecutive idle counter
        if getattr(self, "_is_loafing", False):
            self._is_loafing = False
        if self._ai:
            try: self._ai._consecutive_idle_count = 0
            except Exception: pass
        self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
        if shutdown:
            self.root.after(50, self._shutdown)
        else:
            self.root.after(0, self._reschedule_screen_poll)

    def _shutdown(self):
        self._stop_talking_rotation()
        if self._bleep: self._bleep.stop()
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        if self._voice: self._voice.stop()
        self._close_with_animation()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            if self._bleep: self._bleep.stop()
            if self._voice: self._voice.stop()


# ── Username blacklist check ───────────────────────────────────────────────────

def _check_blacklisted_username():
    """Exits the application with a generic error if the current OS user
    matches any entry in BLACKLISTED_USERNAMES (case-insensitive)."""
    if not BLACKLISTED_USERNAMES:
        return
    try:
        current_user = os.getlogin()
    except Exception:
        current_user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if current_user.lower() in [u.lower() for u in BLACKLISTED_USERNAMES]:
        native_error_popup("Unknown Error", "An unknown error has occurred (0x000F).")
        sys.exit(1)


# ── First-run config check ─────────────────────────────────────────────────────

def _early_config_check():
    import sys
    from pathlib import Path

    if getattr(sys, "frozen", False):
        base = Path(sys.argv[0]).resolve().parent
    else:
        base = Path(__file__).parent

    config_path = base / "config.txt"
    if config_path.exists():
        return

    default_config = """# Agetha v5.0.2 config — @tomiszivacs on TikTok

# Set to "yes" to use Ollama instead of Groq.
USE_LOCAL_AI = no

# Set to "yes" to use local Whisper (faster-whisper) for voice input instead of Google STT.
# Much faster — no internet needed. Requires: pip install faster-whisper numpy
# Downloads a small ~75 MB model (tiny.en) on first run.
USE_LOCAL_STT = yes

# Groq API keys (use separate accounts to avoid rate limits)
GROQ_API_KEY = 
GROQ_API_KEY_2 = 
GROQ_API_KEY_3 = 
GROQ_API_KEY_4 = 
GROQ_API_KEY_5 = 
GROQ_API_KEY_6 = 
GROQ_API_KEY_7 = 
GROQ_API_KEY_8 = 
GROQ_API_KEY_9 = 
GROQ_API_KEY_10 = 
GROQ_MODEL = llama-3.3-70b-versatile

# EXPERIMENTAL: OpenRouter support.
# If enabled, OpenRouter is used and Groq is bypassed entirely.
# (The default model is kind of stupid, so you may want to change it to something else.)
ENABLE_OPENROUTER = no
OPENROUTER_API_KEY = 
OPENROUTER_MODEL = nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free

# Ollama / local model
LOCAL_AI_MODEL = 
LOCAL_AI_TIMEOUT = 30

# Allow Agetha to run commands on your machine?
ENABLE_COMMAND_EXECUTION = yes

# Memory / history settings (higher = more context but more tokens)
MEMORY_CHARS = 600
HISTORY_LIMIT = 6
FILE_READ_CHARS = 200

# GIF animation speed (lower = faster). Default: 0.6
ANIMATION_SPEED = 0.6

# OCR: capture only the focused/foreground window (yes) or full screen (no).
# yes = faster, more relevant; no = captures everything visible
OCR_FOCUSED_WINDOW = false

# Show the microphone button next to the input box? Set to "no" to hide it.
SHOW_MIC_BUTTON = yes

# FASTER_MODE: removes character awareness and personality details to save tokens.
# Agetha will respond correctly to commands but with less personality.
# Set to yes if you are hitting token limits or want faster, cheaper responses.
FASTER_MODE = no
"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(default_config, encoding="utf-8")
    print(f"[Agetha] Created config.txt at {config_path}")
    print("[Agetha] Please fill in your API keys and restart.")

    msg   = "Please configure Agetha with your API keys.\nRead the README.txt for setup guide."
    title = "Agetha — First Run"
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x40 | 0x1000)
    except Exception:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            messagebox.showinfo(title, msg, parent=root); root.destroy()
        except Exception as e:
            print(f"[Agetha] Could not show popup: {e}")

    sys.exit(0)


if __name__ == "__main__":
    _check_blacklisted_username()
    _early_config_check()
    app = CompanionApp()
    app.run()
