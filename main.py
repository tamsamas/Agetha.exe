"""
Desktop AI Companion - Main Application
Requires: pip install pillow pyautogui pytesseract numpy pygame requests
Assets folder must contain: idle-1.gif, idle-2.gif, idle-3.gif,
  talking-1.gif, talking-2.gif, talking-3.gif,
  thinking.gif, sleeping.gif, happy.gif, surprised.gif, sad.gif, excited.gif, angry.gif
Font: barrio.ttf must be in project root
"""

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
ASSETS      = BASE_DIR / "assets"
FONT_PATH   = BASE_DIR / "barrio.ttf"
PROMPT_FILE = BASE_DIR / "prompt.txt"

WINDOW_W = 340
WINDOW_H = 510
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
        import shutil, subprocess, platform
        system = platform.system()
        if system == "Linux":
            font_dir = Path.home() / ".local/share/fonts"
            font_dir.mkdir(parents=True, exist_ok=True)
            dest = font_dir / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
                subprocess.run(["fc-cache", "-f"], capture_output=True)
            print("[Font] Installed barrio.ttf to ~/.local/share/fonts")
            return True
        elif system == "Darwin":
            font_dir = Path.home() / "Library/Fonts"
            font_dir.mkdir(parents=True, exist_ok=True)
            dest = font_dir / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
            print("[Font] Installed barrio.ttf to ~/Library/Fonts")
            return True
        elif system == "Windows":
            import ctypes, winreg
            user_fonts = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
            user_fonts.mkdir(parents=True, exist_ok=True)
            dest = user_fonts / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows NT\CurrentVersion\Fonts",
                    0, winreg.KEY_SET_VALUE
                )
                winreg.SetValueEx(key, "Barrio (TrueType)", 0, winreg.REG_SZ, str(dest))
                winreg.CloseKey(key)
            except Exception:
                pass
            ctypes.windll.gdi32.AddFontResourceW(str(dest))
            # SendMessageW broadcast to all windows can stall for several seconds
            # on Windows 11 — run it in a daemon thread so it never blocks startup.
            def _broadcast():
                ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)
            threading.Thread(target=_broadcast, daemon=True).start()
            print("[Font] Installed barrio.ttf to user fonts dir (Windows)")
            return True
    except Exception as e:
        print(f"[Font] Could not install font: {e}")
    return False


class BleepPlayer:
    """Undertale-style 8-bit bleeps using a square wave with decay envelope."""

    SAMPLE_RATE = 44100

    def __init__(self):
        self._stop_event = threading.Event()
        self._paused = False
        self._thread: threading.Thread | None = None
        self._cache: dict[int, pygame.mixer.Sound] = {}
        self._mixer_ready = False

        # Run pygame mixer init in a background thread — on Windows 11, SDL2's
        # audio device enumeration can deadlock the main thread indefinitely.
        t = threading.Thread(target=self._init_mixer, daemon=True)
        t.start()
        t.join(timeout=5.0)
        if not self._mixer_ready:
            print("[BleepPlayer] WARNING: pygame mixer init timed out — audio disabled.")

    def _init_mixer(self):
        try:
            pygame.mixer.pre_init(self.SAMPLE_RATE, -16, 1, 256)
            pygame.mixer.init()
            self._mixer_ready = True
        except Exception as e:
            print(f"[BleepPlayer] mixer init error: {e}")

    def _make_bleep(self, freq: int) -> "pygame.mixer.Sound | None":
        if not self._mixer_ready:
            return None
        if freq in self._cache:
            return self._cache[freq]

        import array as arr
        duration   = 0.042
        n_samples  = int(self.SAMPLE_RATE * duration)
        volume     = 0.28
        buf        = arr.array("h", [0] * n_samples)

        for i in range(n_samples):
            t = i / self.SAMPLE_RATE
            wave = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
            env  = math.exp(-t * 40)
            buf[i] = int(wave * env * volume * 32767)

        sound = pygame.mixer.Sound(buffer=buf)
        self._cache[freq] = sound
        return sound

    def start_talking(self, tone: str = "neutral"):
        if not self._mixer_ready:
            return
        self.stop()
        self._stop_event.clear()
        freq = BLEEP_TONES.get(tone, 440)
        self._thread = threading.Thread(target=self._loop, args=(freq,), daemon=True)
        self._thread.start()

    def _loop(self, freq: int):
        sound = self._make_bleep(freq)
        if sound is None:
            return
        while not self._stop_event.is_set():
            if self._paused:
                time.sleep(0.02)
                continue
            sound.play()
            time.sleep(random.uniform(0.03, 0.055))

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self):
        self._stop_event.set()
        self._paused = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.4)


class GifPlayer:
    """Loads and animates a GIF on a tk.Label, looping automatically."""

    def __init__(self, label: tk.Label, gif_path: str, after_cb):
        self._label  = label
        self._after  = after_cb
        self._frames: list[ImageTk.PhotoImage] = []
        self._delays: list[int] = []
        self._idx    = 0
        self._job    = None
        self._running = False
        self._load(gif_path)

    def _load(self, path: str):
        try:
            img = Image.open(path)
            for frame in ImageSequence.Iterator(img):
                f = frame.convert("RGBA")
                f.thumbnail((GIF_W, GIF_H), Image.LANCZOS)
                canvas = Image.new("RGBA", (GIF_W, GIF_H), (10, 10, 15, 255))
                ox = (GIF_W - f.width)  // 2
                oy = (GIF_H - f.height) // 2
                canvas.paste(f, (ox, oy), f)
                self._frames.append(ImageTk.PhotoImage(canvas))
                delay = frame.info.get("duration", 80)
                self._delays.append(max(delay, 40))
        except Exception as e:
            print(f"[GifPlayer] Could not load {path}: {e}")

    def play(self):
        if not self._frames:
            return
        self._running = True
        self._idx = 0
        self._tick()

    def stop(self):
        self._running = False
        if self._job:
            try:
                self._label.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _tick(self):
        if not self._running or not self._frames:
            return
        self._label.config(image=self._frames[self._idx])
        delay = self._delays[self._idx]
        self._idx = (self._idx + 1) % len(self._frames)
        self._job = self._after(delay, self._tick)


class SubtitleRenderer:
    """Typewriter-style subtitles on a Canvas using the Barrio font."""

    CHAR_DELAY = 0.030

    def __init__(self, canvas: tk.Canvas, font_size: int = 17, bleep_player=None):
        self._canvas     = canvas
        self._font_size  = font_size
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._bleep = bleep_player

        self._canvas.config(bg="#0a0a0f")
        self._font = self._load_font(font_size)

    def _load_font(self, size: int) -> tkfont.Font:
        available = tkfont.families()
        for name in ("Barrio", "barrio"):
            if name in available:
                print(f"[Font] Using '{name}' from Tk font families")
                return tkfont.Font(family=name, size=size)
        print("[Font] Barrio not found in Tk families, using Courier fallback")
        return tkfont.Font(family="Courier", size=size, weight="bold")

    def clear(self):
        self._canvas.delete("all")

    def show_thinking(self, raw_text: str):
        """Show streaming tokens in grey while waiting for a response."""
        import re
        texts = re.findall(r'"text"\s*:\s*"([^"]*)', raw_text)
        preview = " ".join(texts).strip() or "…"
        self._canvas.after(0, lambda p=preview: self._draw(p, color="#555566"))

    def speak(self, segments: list, on_done=None):
        self.stop()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, args=(segments, on_done), daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self, segments: list, on_done):
        self._canvas.after(0, self.clear)
        full_text = ""
        for i, seg in enumerate(segments):
            if self._stop_event.is_set():
                break
            chunk = seg.get("text", "").strip()
            pause = seg.get("pause", 0.0)
            if full_text and not full_text.endswith(" "):
                full_text += " "
            for ch in chunk:
                if self._stop_event.is_set():
                    break
                full_text += ch
                t = full_text
                self._canvas.after(0, lambda txt=t: self._draw(txt))
                time.sleep(self.CHAR_DELAY)
            if pause > 0 and not self._stop_event.is_set():
                if self._bleep:
                    self._bleep.pause()
                time.sleep(pause)
                if self._bleep:
                    self._bleep.resume()
        if on_done:
            self._canvas.after(0, on_done)

    def _draw(self, text: str, color: str = "#ffffff"):
        self._canvas.delete("all")
        cw = self._canvas.winfo_width()  or WINDOW_W
        ch = self._canvas.winfo_height() or 110

        words   = text.split()
        lines   = []
        line    = ""
        max_w   = cw - 24
        char_w  = self._font_size * 0.62

        for w in words:
            test  = (line + " " + w).strip()
            est_w = len(test) * char_w
            if est_w > max_w and line:
                lines.append(line)
                line = w
            else:
                line = test
        if line:
            lines.append(line)

        lines  = lines[-3:]
        line_h = self._font_size + 7
        total_h = len(lines) * line_h
        y = max(6, (ch - total_h) // 2)

        for ln in lines:
            if color == "#ffffff":
                self._canvas.create_text(
                    cw // 2 + 2, y + 2,
                    text=ln, fill="#000000",
                    font=self._font, anchor="n"
                )
            self._canvas.create_text(
                cw // 2, y,
                text=ln, fill=color,
                font=self._font, anchor="n"
            )
            y += line_h


class AgethaPopup:
    """Fake Windows-style error popup spawned by Agetha."""

    def __init__(self, parent: tk.Tk, messages: list, mood: str = "neutral"):
        self._win = tk.Toplevel(parent)
        self._win.title("Agetha.exe")
        self._win.resizable(False, False)
        self._win.attributes("-topmost", True)
        self._win.configure(bg="#0a0a0f")

        accent = {
            "angry":     "#8b1a1a",
            "sad":       "#1a3a6b",
            "happy":     "#1a6b3a",
            "excited":   "#7a4a10",
            "surprised": "#6b5010",
            "thinking":  "#1a4466",
            "whisper":   "#3a3a55",
            "neutral":   "#1e2d3d",
        }.get(mood, "#1e2d3d")

        title_bar = tk.Frame(self._win, bg=accent, height=26)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)
        tk.Label(
            title_bar, text="⚠  Agetha.exe — System Message",
            bg=accent, fg="#ccccdd",
            font=("Courier", 9, "bold"),
            anchor="w", padx=8,
        ).pack(side="left", fill="y")

        body = tk.Frame(self._win, bg="#0a0a0f", padx=18, pady=14)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="⚠", fg="#cc9900", bg="#0a0a0f",
                 font=("Courier", 26)).grid(row=0, column=0,
                 rowspan=max(len(messages), 1) + 1, sticky="n", padx=(0, 14))

        for i, msg in enumerate(messages):
            tk.Label(
                body, text=msg,
                fg="#ccccdd", bg="#0a0a0f",
                font=("Courier", 10),
                wraplength=270, justify="left", anchor="w",
            ).grid(row=i, column=1, sticky="w", pady=2)

        tk.Frame(self._win, bg=accent, height=1).pack(fill="x", pady=(4, 0))

        btn_row = tk.Frame(self._win, bg="#0a0a0f", pady=8)
        btn_row.pack(fill="x")
        tk.Button(
            btn_row, text="[  OK  ]",
            font=("Courier", 10, "bold"),
            bg=accent, fg="#ccccdd",
            activebackground="#556677", activeforeground="#ffffff",
            relief="flat", bd=0, padx=16, pady=4,
            command=self._win.destroy,
        ).pack()

        self._win.update_idletasks()
        px = parent.winfo_x()
        py = parent.winfo_y()
        pw = parent.winfo_width()
        ww = self._win.winfo_width()
        wh = self._win.winfo_height()
        x  = px + (pw - ww) // 2
        y  = max(0, py - wh - 10)
        self._win.geometry(f"+{x}+{y}")

        self._win.bind("<Return>", lambda _: self._win.destroy())
        self._win.bind("<Escape>", lambda _: self._win.destroy())
        self._win.focus_force()


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
        "excited":   "excited.gif",
        "angry":     "angry.gif",
        "thinking":  "thinking.gif",
        "sleeping":  "sleeping.gif",
    }

    def __init__(self):
        # Register font before creating the Tk window so families() sees it
        _register_barrio_font()

        self.root = tk.Tk()
        self.root.title("Agetha.exe")
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+80+80")
        self.root.configure(bg="#0a0a0f")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._state      = self.STATE_SLEEPING
        self._current_gif_player: GifPlayer | None = None
        self._gif_cache: dict[str, GifPlayer] = {}
        self._talking_rotate_job = None
        self._poll_job = None

        self._bleep  = BleepPlayer()
        self._screen = ScreenReader()
        self._ai     = AIEngine(prompt_file=PROMPT_FILE)
        self._last_screen_text: str = ""

        self._build_ui()
        self._preload_gifs()
        self._start_wake_sequence()

        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>",     self._drag_motion)
        self._drag_x = self._drag_y = 0

    def _build_ui(self):
        self._gif_label = tk.Label(self.root, bg="#0a0a0f", bd=0,
                                   width=GIF_W, height=GIF_H)
        self._gif_label.pack(pady=0)

        status_frame = tk.Frame(self.root, bg="#0a0a0f")
        status_frame.pack(fill="x", padx=10)
        self._dot = tk.Label(status_frame, text="●", fg="#555566",
                             bg="#0a0a0f", font=("Courier", 10))
        self._dot.pack(side="left")
        self._status_var = tk.StringVar(value="zzz…")
        tk.Label(status_frame, textvariable=self._status_var,
                 fg="#555566", bg="#0a0a0f",
                 font=("Courier", 10)).pack(side="left", padx=4)

        self._sub_canvas = tk.Canvas(self.root, width=WINDOW_W, height=110,
                                     bg="#0a0a0f", bd=0, highlightthickness=0)
        self._sub_canvas.pack(fill="x", pady=(4, 4))
        self._subtitle = SubtitleRenderer(self._sub_canvas, font_size=17, bleep_player=self._bleep)

        input_frame = tk.Frame(self.root, bg="#0a0a0f")
        input_frame.pack(fill="x", padx=10, pady=(0, 8))

        self._input_var = tk.StringVar()
        self._input_box = tk.Entry(
            input_frame,
            textvariable=self._input_var,
            font=("Courier", 11),
            bg="#1a1a2e", fg="#ccccdd",
            insertbackground="#ccccdd",
            relief="flat", bd=4,
        )
        self._input_box.pack(side="left", fill="x", expand=True, ipady=4)
        self._input_box.bind("<Return>", self._on_user_input)

        self._send_btn = tk.Button(
            input_frame, text="→",
            font=("Courier", 12, "bold"),
            bg="#223344", fg="#88bbdd",
            activebackground="#334455", activeforeground="#ffffff",
            relief="flat", bd=0, padx=8,
            command=self._on_user_input,
        )
        self._send_btn.pack(side="left", padx=(4, 0), ipady=4)

    def _drag_start(self, e):
        self._drag_x, self._drag_y = e.x, e.y

    def _drag_motion(self, e):
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def _on_user_input(self, event=None):
        text = self._input_var.get().strip()
        if not text:
            return
        if self._input_box["state"] == "disabled":
            return
        self._input_var.set("")
        self._input_box.config(state="disabled")
        self._send_btn.config(state="disabled")
        threading.Thread(target=self._ai_tick, kwargs={"user_message": text}, daemon=True).start()

    def _re_enable_input(self):
        self._input_box.config(state="normal")
        self._send_btn.config(state="normal")
        self._input_box.focus_set()

    def _preload_gifs(self):
        for name in self.IDLE_GIFS + self.TALKING_GIFS + list(self.EXTRA_GIFS.values()):
            path = ASSETS / name
            if path.exists():
                self._gif_cache[name] = GifPlayer(self._gif_label, str(path), self.root.after)
            else:
                print(f"[WARN] Missing asset: {path}")

    def _play_gif(self, name: str):
        if self._current_gif_player:
            self._current_gif_player.stop()
        player = self._gif_cache.get(name)
        if player:
            self._current_gif_player = player
            player.play()
        else:
            print(f"[WARN] GIF not loaded: {name}")

    def _start_talking_rotation(self):
        self._rotate_talking()

    def _rotate_talking(self):
        if self._state != self.STATE_TALKING:
            return
        available = [g for g in self.TALKING_GIFS if g in self._gif_cache]
        if available:
            self._play_gif(random.choice(available))
        delay = random.randint(1800, 3200)
        self._talking_rotate_job = self.root.after(delay, self._rotate_talking)

    def _stop_talking_rotation(self):
        if self._talking_rotate_job:
            self.root.after_cancel(self._talking_rotate_job)
            self._talking_rotate_job = None

    def _set_state(self, state: str, mood: str = "neutral"):
        self._state = state
        dot_colors = {
            self.STATE_SLEEPING: "#334455",
            self.STATE_THINKING: "#aa8833",
            self.STATE_IDLE:     "#334433",
            self.STATE_TALKING:  "#44aa66",
        }
        labels = {
            self.STATE_SLEEPING: "",
            self.STATE_THINKING: "",
            self.STATE_IDLE:     "",
            self.STATE_TALKING:  "",
        }
        self._dot.config(fg=dot_colors.get(state, "#555566"))
        self._status_var.set(labels.get(state, state))

        self._stop_talking_rotation()
        self._bleep.stop()

        if state == self.STATE_SLEEPING:
            self._play_gif("sleeping.gif")
        elif state == self.STATE_THINKING:
            self._play_gif("thinking.gif")
        elif state == self.STATE_IDLE:
            mood_gif = self.EXTRA_GIFS.get(mood)
            if mood_gif and mood_gif in self._gif_cache:
                self._play_gif(mood_gif)
            else:
                available = [g for g in self.IDLE_GIFS if g in self._gif_cache]
                if available:
                    self._play_gif(random.choice(available))
        elif state == self.STATE_TALKING:
            mood_gif = self.EXTRA_GIFS.get(mood)
            if mood_gif and mood_gif in self._gif_cache and mood != "neutral":
                self._play_gif(mood_gif)
            else:
                self._start_talking_rotation()
            self._bleep.start_talking(tone=mood)

    def _start_wake_sequence(self):
        self._set_state(self.STATE_SLEEPING)
        self.root.after(2500, self._finish_wake)

    def _finish_wake(self):
        self._set_state(self.STATE_IDLE, "neutral")
        self.root.after(1000, self._schedule_screen_poll)

    def _schedule_screen_poll(self):
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        threading.Thread(target=self._ai_tick, daemon=True).start()

    def _reschedule_screen_poll(self):
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
        self._poll_job = self.root.after(SCREEN_POLL_INTERVAL_MS, self._schedule_screen_poll)

    def _ai_tick(self, user_message: str | None = None):
        is_user = user_message is not None

        self.root.after(0, lambda: self._input_box.config(state="disabled"))
        self.root.after(0, lambda: self._send_btn.config(state="disabled"))

        screen_text = ""
        if not is_user:
            screen_text = self._screen.capture_text()
            self._last_screen_text = screen_text

        self.root.after(0, lambda: self._set_state(self.STATE_THINKING))

        def _on_token(raw_so_far: str):
            self._subtitle.show_thinking(raw_so_far)

        response = self._ai.query_streaming(
            screen_context=screen_text if not is_user else self._last_screen_text,
            user_message=user_message or "",
            on_token=_on_token,
        )

        print("\n" + "─" * 52)
        if user_message:
            print(f"[USER]  {user_message}")
        print(f"[AI]    {json.dumps(response, ensure_ascii=False)}")
        print("─" * 52)

        self.root.after(0, self._re_enable_input)
        self._dispatch_response(response, user_message)

    def _dispatch_response(self, response: dict, user_message: str | None = None):
        command  = response.get("command", "idle")
        mood     = response.get("mood", "neutral")
        segments = response.get("segments", [])
        popup_msgs = response.get("popup", None)
        shutdown_requested = bool(response.get("shutdown", False))

        def _speak_and_continue(resp_segments, resp_mood, resp_shutdown):
            if resp_segments:
                self.root.after(0, lambda: self._set_state(self.STATE_TALKING, resp_mood))
                self.root.after(0, lambda: self._subtitle.speak(
                    resp_segments,
                    on_done=lambda: self._on_speech_done(resp_shutdown)
                ))
            else:
                self.root.after(0, lambda: self._set_state(self.STATE_IDLE, resp_mood))
                self._reschedule_screen_poll()

        if command == "request_path":
            hint = response.get("path_hint", "").strip()
            lines = [hint] if hint else (
                [seg.get("text", "") for seg in segments if seg.get("text", "")] or
                ["Path resolved automatically."]
            )
            self.root.after(0, lambda: AgethaPopup(self.root, lines[:4], mood))
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        if command == "create_folder":
            path = response.get("path", "").strip()
            if path:
                try:
                    os.makedirs(path, exist_ok=True)
                    print(f"[FS] Created folder: {path}")
                except Exception as e:
                    print(f"[FS] Failed to create folder {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "create_file":
            file_path = response.get("file_path", "").strip()
            if not file_path:
                path      = response.get("path",      "").strip()
                file_name = response.get("file_name", "").strip()
                if path and file_name:
                    file_path = os.path.join(path, file_name)
            content = response.get("content", "")
            if file_path:
                try:
                    parent = os.path.dirname(file_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(f"[FS] Created file: {file_path}")
                except Exception as e:
                    print(f"[FS] Failed to create file {file_path}: {e}")
            else:
                print("[FS] create_file: missing path/file_name")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "delete_file":
            path = response.get("path", "").strip()
            if path:
                import shutil
                try:
                    p = Path(path)
                    if p.is_dir():
                        shutil.rmtree(p)
                        print(f"[FS] Deleted folder: {path}")
                    elif p.exists():
                        p.unlink()
                        print(f"[FS] Deleted file: {path}")
                    else:
                        print(f"[FS] delete_file: not found: {path}")
                except Exception as e:
                    print(f"[FS] Failed to delete {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "rename_file":
            path     = response.get("path",     "").strip()
            new_name = response.get("new_name", "").strip()
            if path and new_name:
                try:
                    p    = Path(path)
                    dest = p.parent / new_name
                    p.rename(dest)
                    print(f"[FS] Renamed: {path} → {dest}")
                except Exception as e:
                    print(f"[FS] Failed to rename {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command in ("list_dir", "list_directory"):
            req_path = response.get("path", "").strip() or str(self._ai._system_path)
            try:
                p = Path(req_path)
                if not p.exists():
                    lines = [f"[not found: {req_path}]"]
                elif not p.is_dir():
                    lines = [f"[not a directory: {req_path}]"]
                else:
                    entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
                    lines = [e.name + ("/" if e.is_dir() else "") for e in entries]
                    if not lines:
                        lines = ["[empty directory]"]
            except Exception as e:
                lines = [f"[error listing: {e}]"]

            self.root.after(0, lambda: AgethaPopup(self.root, lines[:12], mood))
            if not segments:
                segments = [{"text": f"{len(lines)} items in {req_path}", "pause": 0.0}]
            print(f"[FS] Listed {req_path}: {len(lines)} entries")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "set_clipboard":
            text = response.get("text", "").strip()
            if text:
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(text)
                    self.root.update()
                    print(f"[CLIP] Set clipboard: {text[:60]}")
                except Exception as e:
                    print(f"[CLIP] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "play_sound":
            sound_name = response.get("sound", "beep").strip().lower()
            _sound_map = {
                "beep":   440,
                "chime":  880,
                "error":  185,
                "notify": 523,
            }
            freq = _sound_map.get(sound_name, 440)
            try:
                self._bleep.start_talking(tone={v: k for k, v in {
                    "neutral": 440, "happy": 523, "excited": 659,
                    "sad": 294, "surprised": 587, "thinking": 370,
                    "whisper": 220, "angry": 185,
                }.items()}.get(freq, "neutral"))
                threading.Timer(0.8, self._bleep.stop).start()
                print(f"[SOUND] Played: {sound_name}")
            except Exception as e:
                print(f"[SOUND] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "take_screenshot":
            save_path = response.get("save_path", "").strip()
            if not save_path:
                import datetime as _dt
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = os.path.join(self._ai._system_path, f"screenshot_{ts}.png")
            try:
                img = self._screen.capture_image()
                if img:
                    img.save(save_path)
                    print(f"[SCREEN] Screenshot saved: {save_path}")
                else:
                    print("[SCREEN] capture_image returned None")
            except Exception as e:
                print(f"[SCREEN] Failed to save screenshot: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "show_notification":
            title   = response.get("title",   "Agetha").strip()
            message = response.get("message", "").strip()
            if message:
                try:
                    _sys = platform.system()
                    import subprocess as _sp
                    if _sys == "Darwin":
                        script = f'display notification "{message}" with title "{title}"'
                        _sp.Popen(["osascript", "-e", script])
                    elif _sys == "Linux":
                        _sp.Popen(["notify-send", title, message])
                    elif _sys == "Windows":
                        ps = (
                            f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, '
                            f'ContentType = WindowsRuntime] > $null;'
                            f'$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;'
                            f'$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t);'
                            f'$x.GetElementsByTagName("text")[0].AppendChild($x.CreateTextNode("{title}"));'
                            f'$x.GetElementsByTagName("text")[1].AppendChild($x.CreateTextNode("{message}"));'
                            f'$n = [Windows.UI.Notifications.ToastNotification]::new($x);'
                            f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Agetha").Show($n);'
                        )
                        _sp.Popen(["powershell", "-Command", ps], shell=False)
                    print(f"[NOTIFY] {title}: {message}")
                except Exception as e:
                    print(f"[NOTIFY] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "run_command":
            cmd_str = response.get("cmd", "").strip()
            use_shell = bool(response.get("shell", True))
            if cmd_str:
                try:
                    import subprocess as _sp
                    result_proc = _sp.run(
                        cmd_str, shell=use_shell, capture_output=True,
                        text=True, timeout=15
                    )
                    out = (result_proc.stdout or "").strip()
                    err = (result_proc.stderr or "").strip()
                    print(f"[CMD] Ran: {cmd_str}")
                    if out:
                        print(f"[CMD] stdout: {out[:200]}")
                    if err:
                        print(f"[CMD] stderr: {err[:200]}")
                except Exception as e:
                    print(f"[CMD] Failed to run '{cmd_str}': {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "read_document":
            doc_path = response.get("path", "").strip()
            doc_content = self._ai.read_document(doc_path) if doc_path else "[no path provided]"
            print(f"[DOC] Read '{doc_path}': {doc_content[:80]}")
            def _requery_with_doc():
                self.root.after(0, lambda: self._set_state(self.STATE_THINKING))
                def _on_token(raw_so_far: str):
                    self._subtitle.show_thinking(raw_so_far)
                follow = self._ai.query_streaming(
                    screen_context=self._last_screen_text,
                    user_message="",
                    doc_content=doc_content,
                    on_token=_on_token,
                )
                print(f"[AI]    {json.dumps(follow, ensure_ascii=False)}")
                self._dispatch_response(follow, user_message)
            threading.Thread(target=_requery_with_doc, daemon=True).start()
            return

        if command == "open_app":
            app_name = response.get("app", "").strip()
            if app_name:
                print(f"[APP] Opening {app_name}...")
                try:
                    import subprocess as _sp
                    if platform.system() == "Windows":
                        try:
                            os.startfile(app_name)
                        except OSError:
                            _sp.Popen([app_name])
                    elif platform.system() == "Darwin":
                        _sp.Popen(["open", app_name])
                    else:
                        _sp.Popen([app_name])
                except Exception as e:
                    print(f"[APP] Failed to open {app_name}: {e}")
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        if command == "open_browser":
            url    = response.get("url",    "").strip()
            search = response.get("search", "").strip()
            engine = response.get("engine", "google").strip()
            if not url and search:
                _engines = {
                    "google":     "https://www.google.com/search?q=",
                    "duckduckgo": "https://duckduckgo.com/?q=",
                    "bing":       "https://www.bing.com/search?q=",
                }
                url = _engines.get(engine, _engines["google"]) + search.replace(" ", "+")
                print(f"[BROWSER] Searching: {search} ({engine})")
            if url:
                try:
                    webbrowser.open(url)
                except Exception as e:
                    print(f"[BROWSER] Failed: {e}")
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        if command == "request_screen_read":
            print("[SCREEN] AI requesting screen read...")
            screen_text = self._screen.capture_text()
            self._last_screen_text = screen_text
            print(f"[SCREEN] Captured {len(screen_text)} chars")
            def _requery_with_screen():
                self.root.after(0, lambda: self._set_state(self.STATE_THINKING))
                def _on_token(raw_so_far: str):
                    self._subtitle.show_thinking(raw_so_far)
                follow = self._ai.query_streaming(
                    screen_context=screen_text,
                    user_message=user_message or "",
                    on_token=_on_token,
                )
                print(f"[AI]    {json.dumps(follow, ensure_ascii=False)}")
                self._dispatch_response(follow, user_message)
            threading.Thread(target=_requery_with_screen, daemon=True).start()
            return

        if popup_msgs and isinstance(popup_msgs, list) and len(popup_msgs) > 0:
            self.root.after(0, lambda: AgethaPopup(self.root, popup_msgs, mood))
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        if command == "wake_user" and segments:
            self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
            self.root.after(0, lambda: self._subtitle.speak(
                segments,
                on_done=lambda: self._on_speech_done(shutdown_requested)
            ))
        elif command == "speak" and segments:
            self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
            self.root.after(0, lambda: self._subtitle.speak(
                segments,
                on_done=lambda: self._on_speech_done(shutdown_requested)
            ))
        else:
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()

    def _on_speech_done(self, shutdown: bool = False):
        self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
        if shutdown:
            self.root.after(50, self._shutdown)
        else:
            self.root.after(0, self._reschedule_screen_poll)

    def _shutdown(self):
        self._stop_talking_rotation()
        self._bleep.stop()
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self.root.quit()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self._bleep.stop()


def _early_config_check():
    """
    Run before pygame or any heavy import. If config.txt is missing,
    create it, show the setup popup, and exit — before pygame can interfere.
    """
    import sys
    from pathlib import Path

    if getattr(sys, "frozen", False):
        base = Path(sys.argv[0]).resolve().parent
    else:
        base = Path(__file__).parent

    config_path = base / "config.txt"
    if config_path.exists():
        return  # Nothing to do

    default_config = """# Agetha configuration file
# Values are case-insensitive. Use yes/no, true/false, 1/0.
# On first run this file will be created and the program will exit.

ENABLE_GROQ = yes
GROQ_API_KEY = 
GROQ_API_KEY_2 = 
GROQ_API_KEY_3 = 
GROQ_API_KEY_4 = 

# Groq model to use.
# Default: llama-3.3-70b-versatile
# Use qwen/qwen3-32b for more messages (higher rate limits).
GROQ_MODEL = llama-3.3-70b-versatile

ENABLE_GEMINI = yes
GEMINI_API_KEY = 
GEMINI_MODELS = models/gemini-2.5-flash,models/gemini-2.0-flash,models/gemini-1.5-flash,models/gemini-1.5-flash-002

ENABLE_COMMAND_EXECUTION = yes
"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(default_config, encoding="utf-8")
    print(f"[Agetha] Created config.txt at {config_path}")
    print("[Agetha] Please fill in your API keys and restart.")

    msg   = "Please configure Agetha with your API keys.\nRead the README.txt for setup guide."
    title = "Agetha \u2014 First Run"
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x40 | 0x1000)
    except Exception:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            messagebox.showinfo(title, msg, parent=root)
            root.destroy()
        except Exception as e:
            print(f"[Agetha] Could not show popup: {e}")

    sys.exit(0)


if __name__ == "__main__":
    _early_config_check()
    app = CompanionApp()
    app.run()
