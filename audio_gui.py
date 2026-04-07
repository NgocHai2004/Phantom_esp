"""
audio_gui.py — Audio Transfer GUI
Phong cách: Modern dark blue (theo mockup)
Chạy: .venv\Scripts\python audio_gui.py
"""

import customtkinter as ctk
from tkinter import filedialog
import tkinter as tk
import tkinter.ttk as ttk
import socket, threading, os, time, subprocess, json
import urllib.request, urllib.error
from pathlib import Path

# ── Thư mục dongbo/ (cùng cấp với audio_gui.py) ──────────────────────────────
DONGBO_DIR = Path(__file__).parent / "dongbo"

# ── Network config ─────────────────────────────────────────────────────────────
SERVER_IP    = "192.168.4.1"
SERVER_HTTP  = 80
SERVER_AUDIO = 8080
CLIENT_IP    = "192.168.5.1"
CLIENT_HTTP  = 80
CLIENT_AUDIO = 8080

# ── Appearance ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Palette ───────────────────────────────────────────────────────────────────
BG          = "#10131a"   # window background (deep navy-black)
BG_CARD     = "#1a1f2e"   # card/sidebar background
BG_SURFACE  = "#222840"   # elevated surface (inputs, rows)
BG_ROW      = "#1e2438"   # table row background
BG_ROW_ALT  = "#232b3e"   # alternating row
BG_LOG      = "#141820"   # log area
BORDER      = "#2d3550"   # subtle border
ACCENT      = "#2563eb"   # primary blue (like mockup send button)
ACCENT_GLOW = "#1d4ed8"   # hover blue
ACCENT_ICON = "#3b82f6"   # icon blue (music note)
GREEN       = "#22c55e"   # success
WARN        = "#f59e0b"   # warning / dot color when disconnected
RED         = "#ef4444"   # error
TEAL        = "#14b8a6"   # download/receive
TEXT        = "#e2e8f0"   # primary text
MUTED       = "#64748b"   # secondary text
SUBTLE      = "#94a3b8"   # labels/captions

# ── Network helpers ────────────────────────────────────────────────────────────
def tcp_upload(host, port, path, data: bytes, timeout=20, filename=""):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        req = (f"POST {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Content-Type: audio/wav\r\nContent-Length: {len(data)}\r\n"
               + (f"X-Filename: {filename}\r\n" if filename else "")
               + "Connection: close\r\n\r\n").encode()
        s.sendall(req)
        sent = 0
        while sent < len(data):
            s.sendall(data[sent:sent+4096]); sent += 4096
        resp = b""
        s.settimeout(8)
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                resp += c
        except: pass
        return resp.decode(errors="replace"), min(sent, len(data))
    except Exception as e:
        return f"ERROR: {e}", 0
    finally:
        try: s.close()
        except: pass

def tcp_download(host, port, path, timeout=20):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                   "Connection: close\r\n\r\n").encode())
        raw = b""
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                raw += c
        except: pass
        idx = raw.find(b"\r\n\r\n")
        if idx < 0: return b""
        hdrs = raw[:idx].decode(errors="replace")
        if "200 OK" not in hdrs.split("\r\n")[0]: return b""
        return raw[idx+4:]
    except: return b""
    finally:
        try: s.close()
        except: pass

def http_get(host, port, path, timeout=4):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                data += c
        except: pass
        s.close()
        idx = data.find(b"\r\n\r\n")
        return data[idx+4:].decode(errors="replace") if idx >= 0 else ""
    except: return ""

def http_get_json(url, timeout=4):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AudioGUI/3.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except: return None

def http_post(host, port, path, timeout=6):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.sendall(f"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        data = b""
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                data += c
        except: pass
        s.close()
        idx = data.find(b"\r\n\r\n")
        return data[idx+4:].decode(errors="replace") if idx >= 0 else ""
    except: return ""

# ══════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ══════════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Audio Transfer")
        self.geometry("1000x650")
        self.minsize(820, 520)
        self.configure(fg_color=BG)

        self.wav_path       = ctk.StringVar(value="")
        self.client_ip      = ctk.StringVar(value=CLIENT_IP)
        self._server_online = False
        self._client_online = False
        self._detected_node = 0
        self._bg_downloaded = False
        self._spin_angle    = 0
        self._spinning      = False

        self._build_ui()
        self.after(600, self._auto_refresh)
        threading.Thread(target=self._poll_detect, daemon=True).start()
        # Refresh tab dongbo khi focus vào
        self.bind("<FocusIn>", lambda e: None)

    # ─────────────────────────────────────────────────────────────────────────
    # BUILD UI
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Outer container (rounded window feel) ────────────────────────────
        outer = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=16)
        outer.pack(fill="both", expand=True, padx=12, pady=10)

        # ── Header bar ───────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(outer, fg_color="transparent", height=52)
        hdr.pack(fill="x", padx=20, pady=(14, 0))
        hdr.pack_propagate(False)

        icon_box = ctk.CTkFrame(hdr, fg_color=ACCENT,
                                width=28, height=28, corner_radius=7)
        icon_box.pack(side="left", pady=12)
        icon_box.pack_propagate(False)
        ctk.CTkLabel(icon_box, text="▶", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="white").place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(hdr, text="  Audio Transfer",
                     font=ctk.CTkFont("Segoe UI", 16, "bold"),
                     text_color=TEXT).pack(side="left", pady=12)

        right_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        right_hdr.pack(side="right", pady=8)

        self._conn_spinner = ctk.CTkLabel(
            right_hdr, text="◌",
            font=ctk.CTkFont("Segoe UI", 14), text_color=MUTED)
        self._conn_spinner.pack(side="left", padx=(0, 4))

        self._conn_lbl = ctk.CTkLabel(
            right_hdr, text="Đang tìm thiết bị…",
            font=ctk.CTkFont("Segoe UI", 11), text_color=MUTED)
        self._conn_lbl.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(right_hdr, text="···",
                     font=ctk.CTkFont("Segoe UI", 16, "bold"),
                     text_color=MUTED).pack(side="left")

        # ── Thin separator ───────────────────────────────────────────────────
        ctk.CTkFrame(outer, fg_color=BORDER, height=1, corner_radius=0
                     ).pack(fill="x", padx=0, pady=(12, 0))

        # ── Tab view ─────────────────────────────────────────────────────────
        self._tabs = ctk.CTkTabview(
            outer,
            fg_color=BG_CARD,
            segmented_button_fg_color=BG_SURFACE,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_GLOW,
            segmented_button_unselected_color=BG_SURFACE,
            segmented_button_unselected_hover_color=BORDER,
            text_color=TEXT,
            corner_radius=12,
        )
        self._tabs.pack(fill="both", expand=True, padx=8, pady=(6, 8))

        self._tabs.add("📡  ESP32")
        self._tabs.add("📁  Thư mục dongbo")

        # Tab 1: ESP32 — layout sidebar + main
        tab_esp = self._tabs.tab("📡  ESP32")
        body = ctk.CTkFrame(tab_esp, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=0, pady=0)

        sidebar = ctk.CTkFrame(body, fg_color="transparent", width=280)
        sidebar.pack(side="left", fill="y", padx=(4, 8), pady=6)
        sidebar.pack_propagate(False)

        main = ctk.CTkFrame(body, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=(0, 4), pady=6)

        self._build_sidebar(sidebar)
        self._build_main(main)

        # Tab 2: Thư mục dongbo
        tab_local = self._tabs.tab("📁  Thư mục dongbo")
        self._build_local_tab(tab_local)

        # Khi chuyển sang tab dongbo → tự refresh
        self._tabs.configure(command=self._on_tab_change)

        # Start spinner animation
        self._start_spinner()

    # ─────────────────────────────────────────────────────────────────────────
    # SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        # ── Card: Trạng thái ─────────────────────────────────────────────────
        card1 = ctk.CTkFrame(parent, fg_color=BG_SURFACE,
                              corner_radius=14)
        card1.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(card1, text="Trạng thái",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=TEXT, anchor="w"
                     ).pack(fill="x", padx=16, pady=(14, 6))

        status_row = ctk.CTkFrame(card1, fg_color="transparent")
        status_row.pack(fill="x", padx=16, pady=(0, 4))

        self._status_dot = ctk.CTkLabel(
            status_row, text="●",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=WARN)
        self._status_dot.pack(side="left", padx=(0, 6))

        self._detect_lbl = ctk.CTkLabel(
            status_row, text="Chưa kết nối",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=SUBTLE)
        self._detect_lbl.pack(side="left")

        self._ip_lbl = ctk.CTkLabel(
            card1, text="Nhấn để kết nối WiFi",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color=MUTED, anchor="w")
        self._ip_lbl.pack(fill="x", padx=16, pady=(0, 14))

        # ── Card: Chọn file ───────────────────────────────────────────────────
        card2 = ctk.CTkFrame(parent, fg_color=BG_SURFACE,
                              corner_radius=14)
        card2.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(card2, text="Chọn file",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=TEXT, anchor="w"
                     ).pack(fill="x", padx=16, pady=(14, 8))

        file_row = ctk.CTkFrame(card2, fg_color="transparent")
        file_row.pack(fill="x", padx=12, pady=(0, 10))

        self._file_entry = ctk.CTkEntry(
            file_row,
            textvariable=self.wav_path,
            placeholder_text="Chọn file…",
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=BG_CARD,
            border_color=BORDER,
            border_width=1,
            text_color=TEXT,
            height=36,
            corner_radius=8)
        self._file_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(file_row, text="···",
                      width=36, height=36,
                      font=ctk.CTkFont("Segoe UI", 13, "bold"),
                      fg_color=BG_CARD,
                      hover_color=BORDER,
                      border_color=BORDER,
                      border_width=1,
                      text_color=SUBTLE,
                      corner_radius=8,
                      command=self._browse
                      ).pack(side="right")

        # Upload button — blue gradient style
        self._upload_btn = ctk.CTkButton(
            card2,
            text="🚀   Gửi file",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_GLOW,
            text_color="white",
            height=44,
            corner_radius=10,
            command=lambda: threading.Thread(
                target=self._upload_to_server, daemon=True).start())
        self._upload_btn.pack(fill="x", padx=12, pady=(0, 4))

        # Upload progress (hidden)
        self._upload_pb = ctk.CTkProgressBar(
            card2, mode="indeterminate", height=3,
            progress_color=ACCENT, fg_color=BORDER,
            corner_radius=2)
        self._upload_pb.pack(fill="x", padx=12, pady=(0, 2))
        self._upload_pb.pack_forget()

        self._upload_result_lbl = ctk.CTkLabel(
            card2, text="",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color=TEAL, anchor="w", wraplength=240)
        self._upload_result_lbl.pack(fill="x", padx=16, pady=(0, 10))

        # ── Card: Nhận file ───────────────────────────────────────────────────
        card3 = ctk.CTkFrame(parent, fg_color=BG_SURFACE,
                              corner_radius=14)
        card3.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(card3, text="Nhận file",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=TEXT, anchor="w"
                     ).pack(fill="x", padx=16, pady=(14, 6))

        self._dl_status_lbl = ctk.CTkLabel(
            card3, text="Chờ kết nối…",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color=MUTED, anchor="w", wraplength=240)
        self._dl_status_lbl.pack(fill="x", padx=16, pady=(0, 6))

        self._dl_pb = ctk.CTkProgressBar(
            card3, mode="indeterminate", height=3,
            progress_color=TEAL, fg_color=BORDER, corner_radius=2)
        self._dl_pb.pack(fill="x", padx=12, pady=(0, 4))
        self._dl_pb.pack_forget()

        ctk.CTkButton(card3,
                      text="🗁   Mở thư mục Downloads",
                      font=ctk.CTkFont("Segoe UI", 10),
                      fg_color=BG_CARD,
                      hover_color=BORDER,
                      border_color=BORDER,
                      border_width=1,
                      text_color=SUBTLE,
                      height=36, corner_radius=8,
                      command=self._open_downloads
                      ).pack(fill="x", padx=12, pady=(0, 12))

        # hidden compat labels
        self._upload_section_lbl = ctk.CTkLabel(parent, text="", width=0, height=0)

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN AREA
    # ─────────────────────────────────────────────────────────────────────────
    def _build_main(self, parent):
        # ── File list header ─────────────────────────────────────────────────
        fhdr = ctk.CTkFrame(parent, fg_color="transparent")
        fhdr.pack(fill="x", pady=(0, 8))

        self._filelist_title = ctk.CTkLabel(
            fhdr, text="Danh sách file",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=TEXT, anchor="w")
        self._filelist_title.pack(side="left")

        # Icon action buttons (right)
        for icon, tip, cmd in [
            ("↻",  "Làm mới",   lambda: threading.Thread(target=self._fetch_filelist, daemon=True).start()),
            ("↓",  "Tải về",    lambda: threading.Thread(target=self._download, args=("server",), daemon=True).start()),
            ("🗑", "Xóa",       lambda: threading.Thread(target=self._delete_selected_file, daemon=True).start()),
        ]:
            ctk.CTkButton(fhdr, text=icon, width=32, height=28,
                          font=ctk.CTkFont("Segoe UI", 13),
                          fg_color="transparent",
                          hover_color=BG_SURFACE,
                          text_color=MUTED,
                          corner_radius=6,
                          command=cmd
                          ).pack(side="right", padx=2)

        # ── File list table ───────────────────────────────────────────────────
        table_card = ctk.CTkFrame(parent, fg_color=BG_SURFACE,
                                   corner_radius=14)
        table_card.pack(fill="x", pady=(0, 14))

        # Column header row
        col_hdr = tk.Frame(table_card, bg=BG_CARD)
        col_hdr.pack(fill="x", padx=2, pady=(2, 0))

        for txt, w, anchor in [
            ("Tên file",    360, "w"),
            ("Kích thước",  110, "center"),
            ("Thời lượng",  100, "center"),
            ("",             80, "center"),   # actions col
        ]:
            lbl = tk.Label(col_hdr, text=txt,
                           bg=BG_CARD, fg=MUTED,
                           font=("Segoe UI", 9),
                           padx=10 if anchor == "w" else 0,
                           anchor=anchor, width=0)
            lbl.pack(side="left", fill="x",
                     expand=(txt == "Tên file"),
                     ipadx=6, ipady=6)

        # Scrollable file rows container
        self._rows_frame = tk.Frame(table_card, bg=BG_SURFACE)
        self._rows_frame.pack(fill="x", padx=2, pady=(0, 2))

        # Placeholder when empty
        self._empty_lbl = tk.Label(self._rows_frame,
                                    text="Không có file",
                                    bg=BG_SURFACE, fg=MUTED,
                                    font=("Segoe UI", 10),
                                    pady=24)
        self._empty_lbl.pack()

        # ── Log area ──────────────────────────────────────────────────────────
        log_hdr = ctk.CTkFrame(parent, fg_color="transparent")
        log_hdr.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(log_hdr, text="Nhật ký",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=TEXT
                     ).pack(side="left")

        ctk.CTkButton(log_hdr, text="🗑", width=28, height=24,
                      font=ctk.CTkFont("Segoe UI", 11),
                      fg_color="transparent", hover_color=BG_SURFACE,
                      text_color=MUTED, corner_radius=6,
                      command=self._clear_log
                      ).pack(side="right")

        log_card = ctk.CTkFrame(parent, fg_color=BG_LOG,
                                 corner_radius=14)
        log_card.pack(fill="both", expand=True)

        self.log = tk.Text(
            log_card,
            font=("Consolas", 10),
            bg=BG_LOG, fg="#94a3b8",
            relief="flat", bd=0,
            insertbackground=TEXT,
            state="disabled", wrap="word",
            highlightthickness=0,
            selectbackground=BG_SURFACE,
            selectforeground=TEXT,
            padx=14, pady=10)
        self.log.pack(fill="both", expand=True, padx=2, pady=2)

        # Scrollbar
        vsb = tk.Scrollbar(log_card, command=self.log.yview,
                            bg=BG_LOG, troughcolor=BG_LOG,
                            bd=0, highlightthickness=0,
                            width=6)
        vsb.pack(side="right", fill="y", padx=(0, 2), pady=4)
        self.log.configure(yscrollcommand=vsb.set)

        # Tags
        self.log.tag_config("ok",     foreground="#22c55e")
        self.log.tag_config("err",    foreground="#ef4444")
        self.log.tag_config("info",   foreground="#60a5fa")
        self.log.tag_config("warn",   foreground="#fbbf24")
        self.log.tag_config("header", foreground=ACCENT,
                             font=("Consolas", 10, "bold"))
        self.log.tag_config("data",   foreground="#86efac")
        self.log.tag_config("prompt", foreground="#475569")

    # ─────────────────────────────────────────────────────────────────────────
    # FILE ROWS (custom, like mockup)
    # ─────────────────────────────────────────────────────────────────────────
    def _update_filelist_ui(self, files):
        # Clear existing rows
        for w in self._rows_frame.winfo_children():
            w.destroy()

        if not files:
            tk.Label(self._rows_frame, text="Không có file",
                     bg=BG_SURFACE, fg=MUTED,
                     font=("Segoe UI", 10), pady=24
                     ).pack()
            self._statusbar_set("Không có file")
            self._log("Danh sách trống", "warn")
            return

        icons = ["♪", "♫", "♩", "♬"]
        for i, f in enumerate(files):
            name = f.get("name", "?")
            sz   = f.get("size", 0)
            try: sz = int(sz)
            except: sz = 0
            dur = f.get("duration_sec", 0)
            try: dur = float(dur)
            except: dur = 0.0

            if sz >= 1024*1024: sz_str = f"{sz/1024/1024:.1f} MB"
            elif sz >= 1024:    sz_str = f"{sz/1024:.1f} KB"
            else:               sz_str = f"{sz} B"

            m = int(dur) // 60
            s = int(dur) % 60
            dur_str = f"{m:02d}:{s:02d}"

            row_bg = BG_ROW if i % 2 == 0 else BG_ROW_ALT

            row = tk.Frame(self._rows_frame, bg=row_bg, cursor="hand2")
            row.pack(fill="x")

            # Thin top border
            tk.Frame(row, bg=BORDER, height=1).pack(fill="x")

            inner = tk.Frame(row, bg=row_bg)
            inner.pack(fill="x", padx=4, pady=2)

            # Icon
            icon_lbl = tk.Label(inner,
                                 text=icons[i % len(icons)],
                                 bg=BG_SURFACE, fg=ACCENT_ICON,
                                 font=("Segoe UI", 14),
                                 width=3, pady=10,
                                 relief="flat")
            icon_lbl.pack(side="left", padx=(8, 8), pady=6)

            # Name
            tk.Label(inner, text=name,
                     bg=row_bg, fg=TEXT,
                     font=("Segoe UI", 10),
                     anchor="w"
                     ).pack(side="left", fill="x", expand=True)

            # Size
            tk.Label(inner, text=sz_str,
                     bg=row_bg, fg=SUBTLE,
                     font=("Segoe UI", 10),
                     width=10, anchor="center"
                     ).pack(side="left", padx=4)

            # Duration
            tk.Label(inner, text=dur_str,
                     bg=row_bg, fg=SUBTLE,
                     font=("Segoe UI", 10),
                     width=7, anchor="center"
                     ).pack(side="left", padx=4)

            # Row action buttons
            btn_frame = tk.Frame(inner, bg=row_bg)
            btn_frame.pack(side="right", padx=(0, 8))

            fname_cap = name  # capture for lambda

            dl_btn = tk.Label(btn_frame, text="↓",
                               bg=BG_SURFACE, fg=SUBTLE,
                               font=("Segoe UI", 12),
                               width=3, pady=4,
                               relief="flat", cursor="hand2")
            dl_btn.pack(side="left", padx=2)
            dl_btn.bind("<Button-1>", lambda e, fn=fname_cap: threading.Thread(
                target=self._download_file, args=(fn,), daemon=True).start())
            dl_btn.bind("<Enter>", lambda e, b=dl_btn: b.configure(bg=ACCENT))
            dl_btn.bind("<Leave>", lambda e, b=dl_btn: b.configure(bg=BG_SURFACE))

            rm_btn = tk.Label(btn_frame, text="🗑",
                               bg=BG_SURFACE, fg=SUBTLE,
                               font=("Segoe UI", 10),
                               width=3, pady=4,
                               relief="flat", cursor="hand2")
            rm_btn.pack(side="left", padx=2)
            rm_btn.bind("<Button-1>", lambda e, fn=fname_cap: threading.Thread(
                target=self._delete_file, args=(fn,), daemon=True).start())
            rm_btn.bind("<Enter>", lambda e, b=rm_btn: b.configure(bg=RED))
            rm_btn.bind("<Leave>", lambda e, b=rm_btn: b.configure(bg=BG_SURFACE))

            # Hover effect on row
            def on_enter(e, w=row, c=row_bg): w.configure(bg=c)
            def on_leave(e, w=row, c=row_bg): w.configure(bg=c)
            row.bind("<Enter>", on_enter)
            row.bind("<Leave>", on_leave)

        count = len(files)
        self._statusbar_set(f"{count} file trên thiết bị")
        self._log(f"Danh sách: {count} file", "ok")

    # ─────────────────────────────────────────────────────────────────────────
    # SPINNER ANIMATION
    # ─────────────────────────────────────────────────────────────────────────
    _SPIN_FRAMES = ["◌", "◍", "●", "◍"]

    def _start_spinner(self):
        self._spinning = True
        self._tick_spinner()

    def _stop_spinner(self):
        self._spinning = False

    def _tick_spinner(self):
        if not self._spinning: return
        self._spin_angle = (self._spin_angle + 1) % len(self._SPIN_FRAMES)
        try:
            self._conn_spinner.configure(text=self._SPIN_FRAMES[self._spin_angle])
        except: pass
        self.after(300, self._tick_spinner)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _log(self, msg, tag="info"):
        def _do():
            self.log.config(state="normal")
            ts = time.strftime("%H:%M:%S")
            self.log.insert("end", f">  ", "prompt")
            self.log.insert("end", f"{msg}\n", tag)
            self.log.see("end")
            self.log.config(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _statusbar_set(self, msg):
        pass  # no separate statusbar in this layout

    def _busy(self, on):
        def _do():
            if on:
                self._upload_pb.pack(fill="x", padx=12, pady=(0, 2))
                self._upload_pb.start()
            else:
                self._upload_pb.stop()
                self._upload_pb.pack_forget()
        self.after(0, _do)

    def _browse(self):
        path = filedialog.askopenfilename(
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")])
        if path: self.wav_path.set(path)

    def _browse_then_upload(self):
        path = filedialog.askopenfilename(
            title="Chọn file để gửi",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")])
        if path:
            self.wav_path.set(path)
            threading.Thread(target=self._upload_to_server_do, daemon=True).start()

    def _get_client_ip(self):
        return self.client_ip.get().strip() or CLIENT_IP

    def _open_downloads(self):
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        try: subprocess.Popen(f'explorer "{dl}"')
        except: pass

    def _show_toast(self, msg, error=False):
        color = RED if error else "#1e4d2b"
        border_color = RED if error else GREEN
        toast = ctk.CTkFrame(self, fg_color=color,
                              corner_radius=10,
                              border_color=border_color,
                              border_width=1)
        toast.place(relx=0.5, y=70, anchor="n")
        ctk.CTkLabel(toast, text=msg,
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=TEXT, padx=18, pady=10
                     ).pack()
        self.after(3500, toast.destroy)

    # ─────────────────────────────────────────────────────────────────────────
    # STATUS REFRESH
    # ─────────────────────────────────────────────────────────────────────────
    def _auto_refresh(self):
        threading.Thread(target=self._refresh_status, daemon=True).start()
        self.after(30_000, self._auto_refresh)

    def _refresh_status(self):
        def upd_a(online):
            color = GREEN if online else MUTED
            self.after(0, lambda: self._update_pill("A", online))
        def upd_b(online):
            self.after(0, lambda: self._update_pill("B", online))

        resp = http_get(SERVER_IP, SERVER_HTTP, "/status", timeout=3)
        self._server_online = bool(resp and ("ip" in resp or "node" in resp))
        upd_a(self._server_online)

        cip = self._get_client_ip()
        resp = http_get(cip, CLIENT_HTTP, "/status", timeout=3)
        self._client_online = bool(resp and ("ip" in resp or "node" in resp))
        upd_b(self._client_online)

    def _update_pill(self, which, online):
        pass  # Handled via _on_node_detected

    # ─────────────────────────────────────────────────────────────────────────
    # NODE AUTO-DETECT
    # ─────────────────────────────────────────────────────────────────────────
    def _poll_detect(self):
        ips = [("192.168.4.1", 1), ("192.168.5.1", 2)]
        _miss, _MISS_TH = 0, 3
        while True:
            found = 0
            for ip, num in ips:
                try:
                    req = urllib.request.Request(
                        f"http://{ip}/status",
                        headers={"User-Agent": "AudioGUI/3.0"})
                    with urllib.request.urlopen(req, timeout=2) as r:
                        d = json.loads(r.read().decode())
                        if d.get("node") == num:
                            found = num; break
                except: pass
            if found:
                _miss = 0
                if found != self._detected_node:
                    self._detected_node = found
                    self.after(0, self._on_node_detected, found)
            else:
                _miss += 1
                if _miss >= _MISS_TH and self._detected_node != 0:
                    self._detected_node = 0
                    self.after(0, self._on_node_detected, 0)
            time.sleep(3)

    def _on_node_detected(self, node_num):
        if node_num == 1:
            self._detect_lbl.configure(text="Đã kết nối", text_color=GREEN)
            self._status_dot.configure(text_color=GREEN)
            self._ip_lbl.configure(text="Thiết bị A — sẵn sàng gửi file")
            self._conn_spinner.configure(text="●", text_color=GREEN)
            self._conn_lbl.configure(text="Thiết bị A đã kết nối",
                                      text_color=GREEN)
            self._filelist_title.configure(text="Danh sách file — Thiết bị A")
            self._log("Đã kết nối Thiết bị A", "ok")
            self._refresh_filelist()
        elif node_num == 2:
            self._detect_lbl.configure(text="Đã kết nối", text_color=GREEN)
            self._status_dot.configure(text_color=GREEN)
            self._ip_lbl.configure(text="Thiết bị B — đang nhận file…")
            self._conn_spinner.configure(text="●", text_color=GREEN)
            self._conn_lbl.configure(text="Thiết bị B đã kết nối",
                                      text_color=GREEN)
            self._filelist_title.configure(text="Danh sách file — Thiết bị B")
            self.after(0, lambda: (
                self._dl_pb.pack(fill="x", padx=12, pady=(0, 4)),
                self._dl_pb.start()
            ))
            self._dl_status_lbl.configure(text="Đang nhận file…",
                                           text_color=TEAL)
            self._log("Đã kết nối Thiết bị B", "ok")
            self._refresh_filelist()
            self._start_bg_download()
        else:
            self._detect_lbl.configure(text="Chưa kết nối", text_color=SUBTLE)
            self._status_dot.configure(text_color=WARN)
            self._ip_lbl.configure(text="Nhấn để kết nối WiFi")
            self._conn_spinner.configure(text="◌", text_color=MUTED)
            self._conn_lbl.configure(text="Đang tìm thiết bị…",
                                      text_color=MUTED)
            self._filelist_title.configure(text="Danh sách file")
            self._dl_status_lbl.configure(text="Chờ kết nối…",
                                           text_color=MUTED)

    # ─────────────────────────────────────────────────────────────────────────
    # FILE LIST
    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_filelist(self):
        threading.Thread(target=self._fetch_filelist, daemon=True).start()

    def _fetch_filelist(self):
        data = None
        node = self._detected_node

        if node == 2:
            raw = http_get(CLIENT_IP, CLIENT_HTTP, "/file/list", timeout=4)
            try: data = json.loads(raw) if raw else None
            except: data = None
            if data is None:
                info = http_get_json(f"http://{CLIENT_IP}/file/info", timeout=4)
                if info:
                    if info.get("has_file"):
                        sz = info.get("size", 0); dur = 0.0
                        wi = info.get("wav_info", {})
                        if isinstance(wi, dict): dur = wi.get("duration_sec", 0.0)
                        data = {"files": [{"name": "audio.wav", "size": sz,
                                           "duration_sec": dur}], "count": 1}
                    else:
                        data = {"files": [], "count": 0}
        else:
            raw = http_get(SERVER_IP, SERVER_HTTP, "/file/list", timeout=4)
            try: data = json.loads(raw) if raw else None
            except: data = None

        if data:
            self.after(0, self._update_filelist_ui, data.get("files", []))
        else:
            self._log("Không lấy được danh sách file", "warn")

    # ─────────────────────────────────────────────────────────────────────────
    # ACTIONS
    # ─────────────────────────────────────────────────────────────────────────
    def _check_wav(self):
        wav = self.wav_path.get().strip()
        if not wav or not os.path.exists(wav):
            self._log("Chưa chọn file hoặc file không tồn tại", "warn")
            return None
        data = open(wav, "rb").read()
        self._log(f"File: {os.path.basename(wav)}  ({len(data)//1024:.0f} KB)", "data")
        return data

    def _upload_to_server(self):
        self.after(0, self._browse_then_upload)

    def _upload_to_server_do(self):
        self.after(0, lambda: (
            self._upload_pb.pack(fill="x", padx=12, pady=(0, 2)),
            self._upload_pb.start()
        ))
        upload_ip   = CLIENT_IP   if self._detected_node == 2 else SERVER_IP
        upload_port = CLIENT_AUDIO if self._detected_node == 2 else SERVER_AUDIO
        node_label  = "Thiết bị B" if self._detected_node == 2 else "Thiết bị A"
        wav  = self.wav_path.get()
        data = self._check_wav()
        if data:
            fname = os.path.basename(wav)
            self._log(f"Đang gửi {fname} → {node_label}…", "header")
            t0 = time.time()
            resp, sent = tcp_upload(upload_ip, upload_port, "/upload", data, filename=fname)
            elapsed = time.time() - t0

            spiffs_saved = True
            try:
                rj = json.loads(resp.split("\r\n\r\n", 1)[-1])
                spiffs_saved = rj.get("spiffs_saved", True)
            except: pass

            if ('"ok"' in resp or "200" in resp) and "ERROR" not in resp and spiffs_saved:
                result = f"✓  {fname}  —  {sent//1024:.0f} KB  ({elapsed:.1f}s)"
                self.after(0, lambda t=result: (
                    self._upload_result_lbl.configure(text=t, text_color=GREEN),
                    self._upload_pb.stop(),
                    self._upload_pb.pack_forget()
                ))
                self._log(f"Gửi thành công  {sent//1024:.0f} KB  ({elapsed:.1f}s)", "ok")
                self._show_toast(f"✓  Gửi thành công — {fname}")
                self.after(1500, self._refresh_filelist)
            elif not spiffs_saved:
                result = f"⚠  Bộ nhớ đầy ({sent//1024:.0f} KB)"
                self.after(0, lambda t=result: (
                    self._upload_result_lbl.configure(text=t, text_color=WARN),
                    self._upload_pb.stop(),
                    self._upload_pb.pack_forget()
                ))
                self._log("Bộ nhớ thiết bị đầy — xóa bớt file rồi thử lại", "warn")
                self._show_toast("⚠  Bộ nhớ đầy — xóa file cũ trước", error=True)
                self.after(1500, self._refresh_filelist)
            else:
                self.after(0, lambda: (
                    self._upload_result_lbl.configure(
                        text="✗  Gửi thất bại", text_color=RED),
                    self._upload_pb.stop(),
                    self._upload_pb.pack_forget()
                ))
                self._log("Gửi thất bại", "err")
                self._show_toast("✗  Gửi file thất bại", error=True)
        else:
            self.after(0, lambda: (
                self._upload_pb.stop(),
                self._upload_pb.pack_forget()
            ))

    def _download(self, source="server"):
        """Download file đang chọn từ filelist (fallback audio.wav)."""
        self._download_file("/audio.wav", source)

    def _download_file(self, fname, source="server"):
        host = SERVER_IP if source == "server" else self._get_client_ip()
        port = SERVER_AUDIO if source == "server" else CLIENT_AUDIO
        if not fname.startswith("/"): fname = "/" + fname
        self._log(f"Đang tải: {fname}…", "header")
        t0 = time.time()
        data = tcp_download(host, port, fname)
        elapsed = time.time() - t0
        if len(data) < 44:
            self._log(f"Tải thất bại ({len(data)} bytes)", "err"); return
        save = os.path.basename(fname) or "downloaded.wav"
        with open(save, "wb") as fout: fout.write(data)
        abs_path = os.path.abspath(save)
        self._log(f"✓  Lưu: {save}  ({len(data)//1024:.0f} KB  {elapsed:.1f}s)", "ok")
        self._show_toast(f"✓  Đã tải: {save}")
        try: subprocess.Popen(f'explorer /select,"{abs_path}"')
        except: pass

    def _delete_selected_file(self):
        self._log("Chọn file trong danh sách để xóa", "warn")

    def _delete_file(self, fname):
        safe = str(fname).lstrip("/")
        target = "192.168.4.1" if self._detected_node == 1 else "192.168.5.1"
        self._log(f"Xóa: {safe}…", "header")
        resp = http_post(target, 80, f"/file/delete?name={safe}")
        if resp and ("ok" in resp or "deleted" in resp) and "error" not in resp.lower():
            self._log(f"✓  Đã xóa: {safe}", "ok")
            self._show_toast(f"✓  Đã xóa: {safe}")
            self.after(500, self._refresh_filelist)
        else:
            self._log(f"Xóa thất bại", "err")

    # ─────────────────────────────────────────────────────────────────────────
    # BACKGROUND DOWNLOAD
    # ─────────────────────────────────────────────────────────────────────────
    def _start_bg_download(self):
        if self._bg_downloaded: return
        threading.Thread(target=self._bg_download_thread, daemon=True).start()

    def _bg_download_thread(self):
        save_path = os.path.join(os.path.expanduser("~"), "Downloads", "audio_esp32.wav")
        if os.path.exists(save_path) and os.path.getsize(save_path) > 44:
            self._bg_downloaded = True
            kb = os.path.getsize(save_path) // 1024
            self.after(0, lambda: (
                self._dl_status_lbl.configure(
                    text=f"✓  Đã có sẵn  ({kb} KB)", text_color=GREEN),
                self._dl_pb.stop(), self._dl_pb.pack_forget()
            ))
            self._log(f"File sẵn sàng  ({kb} KB)", "ok")
            return

        retries = 0
        while retries < 12 and not self._bg_downloaded:
            data = tcp_download("192.168.5.1", 8080, "/audio.wav", timeout=15)
            if len(data) > 44:
                try:
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    with open(save_path, "wb") as fout: fout.write(data)
                    self._bg_downloaded = True
                    kb = len(data) // 1024
                    self.after(0, lambda k=kb: (
                        self._dl_status_lbl.configure(
                            text=f"✓  Đã nhận  ({k} KB)", text_color=GREEN),
                        self._dl_pb.stop(), self._dl_pb.pack_forget()
                    ))
                    self._show_toast(f"✓  File sẵn sàng trong Downloads  ({kb} KB)")
                    self._log(f"Nhận thành công  ({kb} KB)", "ok")
                    return
                except PermissionError:
                    save_path = os.path.join(
                        os.path.expanduser("~"), "Desktop", "audio_esp32.wav")
            retries += 1
            time.sleep(5)

        self.after(0, lambda: (
            self._dl_status_lbl.configure(
                text="Không nhận được — thử lại sau", text_color=RED),
            self._dl_pb.stop(), self._dl_pb.pack_forget()
        ))


    # ─────────────────────────────────────────────────────────────────────────
    # TAB CALLBACK
    # ─────────────────────────────────────────────────────────────────────────
    def _on_tab_change(self):
        name = self._tabs.get()
        if "dongbo" in name:
            threading.Thread(target=self._refresh_local_tab, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 2 — THƯ MỤC DONGBO
    # ─────────────────────────────────────────────────────────────────────────
    def _build_local_tab(self, parent):
        # ── Header ──────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", pady=(6, 8), padx=4)

        ctk.CTkLabel(hdr, text="File WAV trong  dongbo/",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=TEXT, anchor="w"
                     ).pack(side="left")

        # Nút hành động
        for icon, tip, cmd in [
            ("↻", "Làm mới",        lambda: threading.Thread(target=self._refresh_local_tab, daemon=True).start()),
            ("🗁", "Mở thư mục",    self._open_dongbo_folder),
            ("🗑", "Xóa đã chọn",   self._delete_local_selected),
        ]:
            ctk.CTkButton(hdr, text=icon, width=32, height=28,
                          font=ctk.CTkFont("Segoe UI", 12),
                          fg_color="transparent", hover_color=BG_SURFACE,
                          text_color=MUTED, corner_radius=6,
                          command=cmd
                          ).pack(side="right", padx=2)

        # ── Stat bar ────────────────────────────────────────────────────────
        self._local_stat_lbl = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color=MUTED, anchor="w")
        self._local_stat_lbl.pack(fill="x", padx=8, pady=(0, 6))

        # ── Table header ────────────────────────────────────────────────────
        table_card = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=14)
        table_card.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        col_hdr = tk.Frame(table_card, bg=BG_CARD)
        col_hdr.pack(fill="x", padx=2, pady=(2, 0))
        for txt, w, anchor in [
            ("Tên file",    0,   "w"),
            ("Kích thước",  90,  "center"),
            ("Ngày sửa",    130, "center"),
            ("",            80,  "center"),
        ]:
            tk.Label(col_hdr, text=txt,
                     bg=BG_CARD, fg=MUTED,
                     font=("Segoe UI", 9),
                     padx=10 if anchor == "w" else 0,
                     anchor=anchor, width=0
                     ).pack(side="left",
                            fill="x", expand=(txt == "Tên file"),
                            ipadx=6, ipady=6)

        # ── Scrollable rows ─────────────────────────────────────────────────
        scroll_outer = tk.Frame(table_card, bg=BG_SURFACE)
        scroll_outer.pack(fill="both", expand=True, padx=2, pady=(0, 2))

        vsb = tk.Scrollbar(scroll_outer, orient="vertical",
                           bg=BG_SURFACE, troughcolor=BG_SURFACE,
                           bd=0, highlightthickness=0, width=6)
        vsb.pack(side="right", fill="y", padx=(0, 2), pady=4)

        self._local_canvas = tk.Canvas(
            scroll_outer, bg=BG_SURFACE,
            highlightthickness=0, bd=0,
            yscrollcommand=vsb.set)
        self._local_canvas.pack(side="left", fill="both", expand=True)
        vsb.configure(command=self._local_canvas.yview)

        self._local_rows = tk.Frame(self._local_canvas, bg=BG_SURFACE)
        self._local_rows_id = self._local_canvas.create_window(
            (0, 0), window=self._local_rows, anchor="nw")

        self._local_rows.bind("<Configure>",
            lambda e: self._local_canvas.configure(
                scrollregion=self._local_canvas.bbox("all")))
        self._local_canvas.bind("<Configure>",
            lambda e: self._local_canvas.itemconfig(
                self._local_rows_id, width=e.width))
        # Scroll chuột
        self._local_canvas.bind_all("<MouseWheel>",
            lambda e: self._local_canvas.yview_scroll(-1*(e.delta//120), "units"))

        # Placeholder
        self._local_empty_lbl = tk.Label(
            self._local_rows, text="Thư mục dongbo/ chưa có file WAV",
            bg=BG_SURFACE, fg=MUTED,
            font=("Segoe UI", 10), pady=28)
        self._local_empty_lbl.pack()

        self._local_selected = set()  # tên file đang chọn

        # Load lần đầu
        threading.Thread(target=self._refresh_local_tab, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_local_tab(self):
        """Quét dongbo/ và cập nhật UI."""
        DONGBO_DIR.mkdir(parents=True, exist_ok=True)
        wavs = sorted(DONGBO_DIR.glob("*.wav"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        self.after(0, self._update_local_rows, wavs)

    def _update_local_rows(self, wavs):
        for w in self._local_rows.winfo_children():
            w.destroy()
        self._local_selected.clear()

        if not wavs:
            tk.Label(self._local_rows,
                     text="Thư mục dongbo/ chưa có file WAV",
                     bg=BG_SURFACE, fg=MUTED,
                     font=("Segoe UI", 10), pady=28).pack()
            self._local_stat_lbl.configure(text="Không có file")
            return

        total_kb = sum(p.stat().st_size for p in wavs) // 1024
        self._local_stat_lbl.configure(
            text=f"{len(wavs)} file  •  {total_kb} KB  •  {DONGBO_DIR}")

        icons = ["♪", "♫", "♩", "♬"]
        for i, p in enumerate(wavs):
            sz    = p.stat().st_size
            mtime = time.strftime("%d/%m  %H:%M", time.localtime(p.stat().st_mtime))
            sz_str = f"{sz/1024:.1f} KB" if sz < 1024*1024 else f"{sz/1024/1024:.2f} MB"
            row_bg = BG_ROW if i % 2 == 0 else BG_ROW_ALT

            row = tk.Frame(self._local_rows, bg=row_bg, cursor="hand2")
            row.pack(fill="x")
            tk.Frame(row, bg=BORDER, height=1).pack(fill="x")

            inner = tk.Frame(row, bg=row_bg)
            inner.pack(fill="x", padx=4, pady=2)

            # Checkbox (dùng Label làm toggle)
            chk_var = tk.BooleanVar(value=False)
            chk_lbl = tk.Label(inner, text="☐",
                                bg=row_bg, fg=MUTED,
                                font=("Segoe UI", 13),
                                width=2, cursor="hand2")
            chk_lbl.pack(side="left", padx=(6, 0), pady=4)

            # Icon nhạc
            tk.Label(inner, text=icons[i % len(icons)],
                     bg=BG_SURFACE, fg=ACCENT_ICON,
                     font=("Segoe UI", 14),
                     width=3, pady=10
                     ).pack(side="left", padx=(4, 8), pady=6)

            # Tên file
            name_lbl = tk.Label(inner, text=p.name,
                                 bg=row_bg, fg=TEXT,
                                 font=("Segoe UI", 10),
                                 anchor="w")
            name_lbl.pack(side="left", fill="x", expand=True)

            # Kích thước
            tk.Label(inner, text=sz_str,
                     bg=row_bg, fg=SUBTLE,
                     font=("Segoe UI", 10),
                     width=9, anchor="center"
                     ).pack(side="left", padx=4)

            # Ngày sửa
            tk.Label(inner, text=mtime,
                     bg=row_bg, fg=MUTED,
                     font=("Segoe UI", 9),
                     width=12, anchor="center"
                     ).pack(side="left", padx=4)

            # Action buttons
            btn_frame = tk.Frame(inner, bg=row_bg)
            btn_frame.pack(side="right", padx=(0, 8))

            path_cap = p  # capture

            # Nút mở (play / reveal in explorer)
            open_btn = tk.Label(btn_frame, text="⬡",
                                 bg=BG_SURFACE, fg=SUBTLE,
                                 font=("Segoe UI", 12),
                                 width=3, pady=4,
                                 relief="flat", cursor="hand2")
            open_btn.pack(side="left", padx=2)
            open_btn.bind("<Button-1>",
                lambda e, pp=path_cap: subprocess.Popen(
                    f'explorer /select,"{pp}"'))
            open_btn.bind("<Enter>", lambda e, b=open_btn: b.configure(bg=TEAL))
            open_btn.bind("<Leave>", lambda e, b=open_btn: b.configure(bg=BG_SURFACE))

            # Nút xóa
            del_btn = tk.Label(btn_frame, text="🗑",
                                bg=BG_SURFACE, fg=SUBTLE,
                                font=("Segoe UI", 10),
                                width=3, pady=4,
                                relief="flat", cursor="hand2")
            del_btn.pack(side="left", padx=2)
            del_btn.bind("<Button-1>",
                lambda e, pp=path_cap: threading.Thread(
                    target=self._delete_local_file, args=(pp,), daemon=True).start())
            del_btn.bind("<Enter>", lambda e, b=del_btn: b.configure(bg=RED))
            del_btn.bind("<Leave>", lambda e, b=del_btn: b.configure(bg=BG_SURFACE))

            # Toggle checkbox
            def _toggle(e, pp=path_cap, cl=chk_lbl):
                if pp.name in self._local_selected:
                    self._local_selected.discard(pp.name)
                    cl.configure(text="☐", fg=MUTED)
                else:
                    self._local_selected.add(pp.name)
                    cl.configure(text="☑", fg=ACCENT)

            chk_lbl.bind("<Button-1>", _toggle)
            name_lbl.bind("<Button-1>", _toggle)
            row.bind("<Button-1>", _toggle)

    # ─────────────────────────────────────────────────────────────────────────
    def _open_dongbo_folder(self):
        DONGBO_DIR.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(f'explorer "{DONGBO_DIR}"')
        except Exception as ex:
            self._log(f"Không mở được thư mục: {ex}", "err")

    def _delete_local_file(self, path: Path):
        try:
            path.unlink()
            self._log(f"✓  Đã xóa local: {path.name}", "ok")
            self._show_toast(f"✓  Đã xóa: {path.name}")
        except Exception as ex:
            self._log(f"Xóa thất bại: {ex}", "err")
        threading.Thread(target=self._refresh_local_tab, daemon=True).start()

    def _delete_local_selected(self):
        if not self._local_selected:
            self._log("Chưa chọn file nào (click tên hoặc ô vuông)", "warn")
            return
        names = list(self._local_selected)
        for name in names:
            p = DONGBO_DIR / name
            if p.exists():
                try:
                    p.unlink()
                    self._log(f"✓  Đã xóa: {name}", "ok")
                except Exception as ex:
                    self._log(f"Lỗi xóa {name}: {ex}", "err")
        threading.Thread(target=self._refresh_local_tab, daemon=True).start()
        self._show_toast(f"✓  Đã xóa {len(names)} file")


if __name__ == "__main__":
    app = App()
    app.mainloop()
