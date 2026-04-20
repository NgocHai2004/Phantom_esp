"""
chuyen.py — ESP32 Audio Transfer GUI (standalone)
===================================================
Chức năng đầy đủ:
  • Auto-detect Node-1 (192.168.4.1) hoặc Node-2 (192.168.5.1)
  • Upload file WAV lên node đang kết nối
  • Danh sách file trong SPIFFS, download, xóa từng file
  • Log hoạt động real-time
  • Dark blue theme (customtkinter)

Cài thư viện (1 lần):
    pip install customtkinter

Chạy:
    python chuyen.py
"""

import customtkinter as ctk
from tkinter import filedialog
import tkinter as tk
import tkinter.ttk as ttk
import socket, threading, os, time, subprocess, json
import urllib.request, urllib.error

# ── Network config ─────────────────────────────────────────────────────────────
NODE1_IP    = "192.168.4.1"
NODE2_IP    = "192.168.5.1"
HTTP_PORT   = 80
TCP_PORT    = 8080

# ── Appearance ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Palette (giống audio_gui.py) ──────────────────────────────────────────────
BG          = "#10131a"
BG_CARD     = "#1a1f2e"
BG_SURFACE  = "#222840"
BG_ROW      = "#1e2438"
BG_ROW_ALT  = "#232b3e"
BG_LOG      = "#141820"
BORDER      = "#2d3550"
ACCENT      = "#2563eb"
ACCENT_GLOW = "#1d4ed8"
ACCENT_ICON = "#3b82f6"
GREEN       = "#22c55e"
WARN        = "#f59e0b"
RED         = "#ef4444"
TEAL        = "#14b8a6"
TEXT        = "#e2e8f0"
MUTED       = "#64748b"
SUBTLE      = "#94a3b8"

# ── Network helpers ────────────────────────────────────────────────────────────
def tcp_upload(host, port, data: bytes, timeout=30, filename=""):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        req = (
            f"POST /upload HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Content-Type: audio/wav\r\n"
            f"Content-Length: {len(data)}\r\n"
            + (f"X-Filename: {filename}\r\n" if filename else "")
            + "Connection: close\r\n\r\n"
        ).encode()
        s.sendall(req)
        sent = 0
        while sent < len(data):
            chunk = data[sent:sent+4096]
            s.sendall(chunk)
            sent += len(chunk)
        resp = b""
        s.settimeout(10)
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                resp += c
        except: pass
        return resp.decode(errors="replace"), sent
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
        s.sendall((
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Connection: close\r\n\r\n"
        ).encode())
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

def http_get_json(url, timeout=4):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ChuyenGUI/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except: return None

# ══════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ══════════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ESP32 Audio Transfer")
        self.geometry("1000x660")
        self.minsize(820, 520)
        self.configure(fg_color=BG)

        self.wav_path        = ctk.StringVar(value="")
        self._detected_node  = 0   # 0=none, 1=Node-1, 2=Node-2
        self._active_ip      = ""
        self._spinning       = False
        self._spin_angle     = 0
        self._file_rows      = []  # list of (frame, fname)

        self._build_ui()
        self.after(500, self._start_spinner)
        threading.Thread(target=self._poll_detect, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # BUILD UI
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=16)
        outer.pack(fill="both", expand=True, padx=12, pady=10)

        # Header
        hdr = ctk.CTkFrame(outer, fg_color="transparent", height=52)
        hdr.pack(fill="x", padx=20, pady=(14, 0))
        hdr.pack_propagate(False)

        icon_box = ctk.CTkFrame(hdr, fg_color=ACCENT, width=28, height=28, corner_radius=7)
        icon_box.pack(side="left", pady=12)
        icon_box.pack_propagate(False)
        ctk.CTkLabel(icon_box, text="▶", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="white").place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(hdr, text="  ESP32 Audio Transfer",
                     font=ctk.CTkFont("Segoe UI", 16, "bold"),
                     text_color=TEXT).pack(side="left", pady=12)

        right_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        right_hdr.pack(side="right", pady=8)

        self._conn_spinner = ctk.CTkLabel(right_hdr, text="◌",
                                          font=ctk.CTkFont("Segoe UI", 14),
                                          text_color=MUTED)
        self._conn_spinner.pack(side="left", padx=(0, 4))

        self._conn_lbl = ctk.CTkLabel(right_hdr, text="Đang tìm thiết bị…",
                                      font=ctk.CTkFont("Segoe UI", 11),
                                      text_color=MUTED)
        self._conn_lbl.pack(side="left", padx=(0, 10))

        # Separator
        ctk.CTkFrame(outer, fg_color=BORDER, height=1, corner_radius=0
                     ).pack(fill="x", padx=0, pady=(12, 0))

        # Body
        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True)

        sidebar = ctk.CTkFrame(body, fg_color="transparent", width=280)
        sidebar.pack(side="left", fill="y", padx=(16, 8), pady=14)
        sidebar.pack_propagate(False)

        main = ctk.CTkFrame(body, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=(0, 16), pady=14)

        self._build_sidebar(sidebar)
        self._build_main(main)

    # ─────────────────────────────────────────────────────────────────────────
    # SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        # Card: Trạng thái
        card1 = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=14)
        card1.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(card1, text="Trạng thái",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=TEXT, anchor="w"
                     ).pack(fill="x", padx=16, pady=(14, 6))

        row = ctk.CTkFrame(card1, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 4))

        self._status_dot = ctk.CTkLabel(row, text="●",
                                        font=ctk.CTkFont("Segoe UI", 10),
                                        text_color=WARN)
        self._status_dot.pack(side="left", padx=(0, 6))

        self._detect_lbl = ctk.CTkLabel(row, text="Chưa kết nối",
                                        font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                        text_color=SUBTLE)
        self._detect_lbl.pack(side="left")

        self._ip_lbl = ctk.CTkLabel(card1, text="Bắt WiFi Phantom-1/2",
                                    font=ctk.CTkFont("Segoe UI", 9),
                                    text_color=MUTED, anchor="w")
        self._ip_lbl.pack(fill="x", padx=16, pady=(0, 14))

        # Card: Chọn file
        card2 = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=14)
        card2.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(card2, text="Chọn file WAV",
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
            fg_color=BG_CARD, border_color=BORDER, border_width=1,
            text_color=TEXT, height=36, corner_radius=8)
        self._file_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(file_row, text="···", width=36, height=36,
                      font=ctk.CTkFont("Segoe UI", 13, "bold"),
                      fg_color=BG_CARD, hover_color=BORDER,
                      border_color=BORDER, border_width=1,
                      text_color=SUBTLE, corner_radius=8,
                      command=self._browse
                      ).pack(side="right")

        # Upload button
        self._upload_btn = ctk.CTkButton(
            card2,
            text="🚀   Gửi file lên ESP32",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_GLOW,
            text_color="white", height=44, corner_radius=10,
            command=lambda: threading.Thread(
                target=self._do_upload, daemon=True).start())
        self._upload_btn.pack(fill="x", padx=12, pady=(0, 4))

        self._upload_pb = ctk.CTkProgressBar(
            card2, mode="indeterminate", height=3,
            progress_color=ACCENT, fg_color=BORDER, corner_radius=2)
        self._upload_pb.pack(fill="x", padx=12, pady=(0, 2))
        self._upload_pb.pack_forget()

        self._upload_result_lbl = ctk.CTkLabel(
            card2, text="",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color=TEAL, anchor="w", wraplength=240)
        self._upload_result_lbl.pack(fill="x", padx=16, pady=(0, 10))

        # Card: Tải file
        card3 = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=14)
        card3.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(card3, text="Tải file về máy",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=TEXT, anchor="w"
                     ).pack(fill="x", padx=16, pady=(14, 6))

        self._dl_status_lbl = ctk.CTkLabel(
            card3, text="Chọn file trong danh sách để tải",
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
                      fg_color=BG_CARD, hover_color=BORDER,
                      border_color=BORDER, border_width=1,
                      text_color=SUBTLE, height=36, corner_radius=8,
                      command=self._open_downloads
                      ).pack(fill="x", padx=12, pady=(0, 12))

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN AREA
    # ─────────────────────────────────────────────────────────────────────────
    def _build_main(self, parent):
        # File list header
        list_hdr = ctk.CTkFrame(parent, fg_color="transparent")
        list_hdr.pack(fill="x", pady=(0, 8))

        self._filelist_title = ctk.CTkLabel(
            list_hdr, text="Danh sách file",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=TEXT, anchor="w")
        self._filelist_title.pack(side="left")

        ctk.CTkButton(list_hdr, text="↻  Làm mới",
                      width=90, height=28,
                      font=ctk.CTkFont("Segoe UI", 10),
                      fg_color=BG_SURFACE, hover_color=BORDER,
                      border_color=BORDER, border_width=1,
                      text_color=SUBTLE, corner_radius=8,
                      command=self._refresh_filelist
                      ).pack(side="right")

        # Column headers
        col_hdr = ctk.CTkFrame(parent, fg_color=BG_SURFACE, corner_radius=8, height=30)
        col_hdr.pack(fill="x", pady=(0, 4))
        col_hdr.pack_propagate(False)

        for text, anchor, relx in [
            ("Tên file", "w", 0.01),
            ("Kích thước", "center", 0.55),
            ("Thời lượng", "center", 0.70),
            ("Thao tác", "center", 0.87),
        ]:
            ctk.CTkLabel(col_hdr, text=text,
                         font=ctk.CTkFont("Segoe UI", 9, "bold"),
                         text_color=MUTED, anchor=anchor
                         ).place(relx=relx, rely=0.5, anchor="w")

        # Scrollable file list
        self._filelist_frame = ctk.CTkScrollableFrame(
            parent, fg_color=BG_LOG, corner_radius=10,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=ACCENT)
        self._filelist_frame.pack(fill="both", expand=True, pady=(0, 8))

        self._empty_lbl = ctk.CTkLabel(
            self._filelist_frame,
            text="Chưa có file — kết nối WiFi ESP32 để xem danh sách",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=MUTED)
        self._empty_lbl.pack(pady=40)

        # Log
        log_hdr = ctk.CTkFrame(parent, fg_color="transparent", height=24)
        log_hdr.pack(fill="x")
        log_hdr.pack_propagate(False)

        ctk.CTkLabel(log_hdr, text="Log",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=SUBTLE, anchor="w").pack(side="left")

        ctk.CTkButton(log_hdr, text="Xóa log",
                      width=70, height=22,
                      font=ctk.CTkFont("Segoe UI", 9),
                      fg_color="transparent", hover_color=BORDER,
                      text_color=MUTED, corner_radius=6,
                      command=self._clear_log
                      ).pack(side="right")

        self.log = tk.Text(parent, height=5, state="disabled",
                           bg=BG_LOG, fg=SUBTLE,
                           font=("Consolas", 9),
                           relief="flat", bd=0,
                           selectbackground=ACCENT,
                           insertbackground=TEXT,
                           wrap="word")
        self.log.pack(fill="x", pady=(2, 0))
        self.log.tag_config("ok",     foreground=GREEN)
        self.log.tag_config("err",    foreground=RED)
        self.log.tag_config("warn",   foreground=WARN)
        self.log.tag_config("header", foreground=ACCENT_ICON,
                            font=("Consolas", 9, "bold"))
        self.log.tag_config("data",   foreground=TEAL)
        self.log.tag_config("info",   foreground=SUBTLE)
        self.log.tag_config("prompt", foreground=MUTED)

    # ─────────────────────────────────────────────────────────────────────────
    # SPINNER ANIMATION
    # ─────────────────────────────────────────────────────────────────────────
    def _start_spinner(self):
        self._spinning = True
        self._animate_spinner()

    def _animate_spinner(self):
        if not self._spinning: return
        frames = ["◐", "◓", "◑", "◒"]
        self._spin_angle = (self._spin_angle + 1) % 4
        if self._detected_node == 0:
            self._conn_spinner.configure(text=frames[self._spin_angle],
                                         text_color=MUTED)
        self.after(250, self._animate_spinner)

    # ─────────────────────────────────────────────────────────────────────────
    # AUTO-DETECT NODE
    # ─────────────────────────────────────────────────────────────────────────
    def _poll_detect(self):
        ips = [(NODE1_IP, 1), (NODE2_IP, 2)]
        miss = 0
        while True:
            found = 0
            for ip, num in ips:
                try:
                    req = urllib.request.Request(
                        f"http://{ip}/status",
                        headers={"User-Agent": "ChuyenGUI/1.0"})
                    with urllib.request.urlopen(req, timeout=2) as r:
                        d = json.loads(r.read().decode())
                        if d.get("node") == num:
                            found = num
                            self._active_ip = ip
                            break
                except: pass

            if found:
                miss = 0
                if found != self._detected_node:
                    self._detected_node = found
                    self.after(0, self._on_node_detected, found)
            else:
                miss += 1
                if miss >= 3 and self._detected_node != 0:
                    self._detected_node = 0
                    self._active_ip = ""
                    self.after(0, self._on_node_detected, 0)
            time.sleep(3)

    def _on_node_detected(self, node_num):
        if node_num == 1:
            self._detect_lbl.configure(text="Node-1 kết nối", text_color=GREEN)
            self._status_dot.configure(text_color=GREEN)
            self._ip_lbl.configure(text=f"IP: {NODE1_IP}")
            self._conn_spinner.configure(text="●", text_color=GREEN)
            self._conn_lbl.configure(text="Thiết bị A (Node-1) đã kết nối",
                                     text_color=GREEN)
            self._filelist_title.configure(text="Danh sách file — Node-1")
            self._log("Đã kết nối Node-1 (192.168.4.1)", "ok")
            self._refresh_filelist()
        elif node_num == 2:
            self._detect_lbl.configure(text="Node-2 kết nối", text_color=GREEN)
            self._status_dot.configure(text_color=GREEN)
            self._ip_lbl.configure(text=f"IP: {NODE2_IP}")
            self._conn_spinner.configure(text="●", text_color=GREEN)
            self._conn_lbl.configure(text="Thiết bị B (Node-2) đã kết nối",
                                     text_color=GREEN)
            self._filelist_title.configure(text="Danh sách file — Node-2")
            self._log("Đã kết nối Node-2 (192.168.5.1)", "ok")
            self._refresh_filelist()
        else:
            self._detect_lbl.configure(text="Chưa kết nối", text_color=SUBTLE)
            self._status_dot.configure(text_color=WARN)
            self._ip_lbl.configure(text="Bắt WiFi Phantom-1 hoặc Phantom-2")
            self._conn_spinner.configure(text="◌", text_color=MUTED)
            self._conn_lbl.configure(text="Đang tìm thiết bị…", text_color=MUTED)
            self._filelist_title.configure(text="Danh sách file")
            self._dl_status_lbl.configure(text="Chờ kết nối…", text_color=MUTED)
            self._clear_filelist_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # FILE LIST
    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_filelist(self):
        threading.Thread(target=self._fetch_filelist, daemon=True).start()

    def _fetch_filelist(self):
        ip = self._active_ip
        if not ip:
            return
        raw = http_get(ip, HTTP_PORT, "/file/list", timeout=5)
        try:
            data = json.loads(raw) if raw else None
        except:
            data = None

        if not data:
            # Fallback: /file/info
            info = http_get_json(f"http://{ip}/file/info", timeout=4)
            if info and info.get("has_file"):
                sz = info.get("size", 0)
                dur = 0.0
                wi = info.get("wav_info", {})
                if isinstance(wi, dict):
                    dur = wi.get("duration_sec", 0.0)
                data = {"files": [{"name": "audio.wav", "size": sz,
                                   "duration_sec": dur}], "count": 1}
            else:
                data = {"files": [], "count": 0}

        files = data.get("files", []) if data else []
        self.after(0, self._update_filelist_ui, files)

    def _clear_filelist_ui(self):
        for w in self._filelist_frame.winfo_children():
            w.destroy()
        self._empty_lbl = ctk.CTkLabel(
            self._filelist_frame,
            text="Chưa có file — kết nối WiFi ESP32 để xem danh sách",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=MUTED)
        self._empty_lbl.pack(pady=40)

    def _update_filelist_ui(self, files):
        for w in self._filelist_frame.winfo_children():
            w.destroy()

        if not files:
            ctk.CTkLabel(self._filelist_frame,
                         text="Chưa có file trong SPIFFS",
                         font=ctk.CTkFont("Segoe UI", 10),
                         text_color=MUTED).pack(pady=40)
            return

        for i, fi in enumerate(files):
            fname   = fi.get("name", "?")
            size_b  = fi.get("size", 0)
            dur     = fi.get("duration_sec", 0.0)
            size_kb = f"{size_b/1024:.1f} KB"
            dur_str = f"{dur:.1f}s" if dur > 0 else "—"

            row_bg = BG_ROW if i % 2 == 0 else BG_ROW_ALT
            row = ctk.CTkFrame(self._filelist_frame,
                               fg_color=row_bg, corner_radius=8, height=40)
            row.pack(fill="x", padx=4, pady=2)
            row.pack_propagate(False)

            # Music icon + filename
            ctk.CTkLabel(row, text="♪",
                         font=ctk.CTkFont("Segoe UI", 11),
                         text_color=ACCENT_ICON, width=20
                         ).place(relx=0.01, rely=0.5, anchor="w")

            ctk.CTkLabel(row, text=fname,
                         font=ctk.CTkFont("Segoe UI", 10),
                         text_color=TEXT, anchor="w"
                         ).place(relx=0.06, rely=0.5, anchor="w")

            ctk.CTkLabel(row, text=size_kb,
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color=SUBTLE
                         ).place(relx=0.55, rely=0.5, anchor="center")

            ctk.CTkLabel(row, text=dur_str,
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color=SUBTLE
                         ).place(relx=0.70, rely=0.5, anchor="center")

            # Action buttons
            btn_frame = ctk.CTkFrame(row, fg_color="transparent")
            btn_frame.place(relx=0.99, rely=0.5, anchor="e")

            ctk.CTkButton(btn_frame, text="⬇",
                          width=30, height=26,
                          font=ctk.CTkFont("Segoe UI", 11),
                          fg_color=BG_SURFACE, hover_color=TEAL,
                          text_color=TEAL, corner_radius=6,
                          command=lambda f=fname: threading.Thread(
                              target=self._download_file, args=(f,),
                              daemon=True).start()
                          ).pack(side="left", padx=(0, 4))

            ctk.CTkButton(btn_frame, text="✕",
                          width=30, height=26,
                          font=ctk.CTkFont("Segoe UI", 10),
                          fg_color=BG_SURFACE, hover_color=RED,
                          text_color=RED, corner_radius=6,
                          command=lambda f=fname: threading.Thread(
                              target=self._delete_file, args=(f,),
                              daemon=True).start()
                          ).pack(side="left", padx=(0, 6))

    # ─────────────────────────────────────────────────────────────────────────
    # UPLOAD
    # ─────────────────────────────────────────────────────────────────────────
    def _browse(self):
        path = filedialog.askopenfilename(
            title="Chọn file WAV",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")])
        if path:
            self.wav_path.set(path)

    def _do_upload(self):
        wav = self.wav_path.get().strip()

        # Nếu chưa chọn file → mở dialog trước
        if not wav:
            self.after(0, self._browse_then_upload)
            return

        if not os.path.exists(wav):
            self._log("File không tồn tại", "err")
            return

        ip = self._active_ip
        if not ip:
            self._log("Chưa kết nối node nào — bắt WiFi ESP32 trước", "warn")
            return

        data = open(wav, "rb").read()
        fname = os.path.basename(wav)

        self.after(0, lambda: (
            self._upload_pb.pack(fill="x", padx=12, pady=(0, 2)),
            self._upload_pb.start(),
            self._upload_result_lbl.configure(text="Đang gửi…", text_color=MUTED)
        ))
        self._log(f"Gửi {fname} ({len(data)//1024} KB) → {ip}…", "header")

        t0 = time.time()
        resp, sent = tcp_upload(ip, TCP_PORT, data, filename=fname)
        elapsed = time.time() - t0

        # Kiểm tra SPIFFS
        spiffs_ok = True
        try:
            body = resp.split("\r\n\r\n", 1)[-1]
            rj = json.loads(body)
            spiffs_ok = rj.get("spiffs_saved", True)
        except: pass

        success = ("200" in resp or '"ok"' in resp) and "ERROR" not in resp and spiffs_ok

        if success:
            msg = f"✓  {fname}  {sent//1024:.0f} KB  ({elapsed:.1f}s)"
            self.after(0, lambda m=msg: (
                self._upload_pb.stop(),
                self._upload_pb.pack_forget(),
                self._upload_result_lbl.configure(text=m, text_color=GREEN)
            ))
            self._log(f"Gửi thành công {sent//1024:.0f} KB ({elapsed:.1f}s)", "ok")
            self._show_toast(f"✓  Gửi thành công — {fname}")
            self.after(1000, self._refresh_filelist)
        elif not spiffs_ok:
            self.after(0, lambda: (
                self._upload_pb.stop(),
                self._upload_pb.pack_forget(),
                self._upload_result_lbl.configure(
                    text="⚠  Bộ nhớ đầy — xóa file cũ trước", text_color=WARN)
            ))
            self._log("Bộ nhớ SPIFFS đầy", "warn")
            self._show_toast("⚠  Bộ nhớ đầy — xóa file cũ trước", error=True)
        else:
            self.after(0, lambda: (
                self._upload_pb.stop(),
                self._upload_pb.pack_forget(),
                self._upload_result_lbl.configure(
                    text="✗  Gửi thất bại", text_color=RED)
            ))
            self._log(f"Gửi thất bại: {resp[:80]}", "err")
            self._show_toast("✗  Gửi thất bại", error=True)

    def _browse_then_upload(self):
        path = filedialog.askopenfilename(
            title="Chọn file WAV để gửi",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")])
        if path:
            self.wav_path.set(path)
            threading.Thread(target=self._do_upload, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # DOWNLOAD
    # ─────────────────────────────────────────────────────────────────────────
    def _download_file(self, fname: str):
        ip = self._active_ip
        if not ip:
            self._log("Chưa kết nối", "warn"); return

        self.after(0, lambda: (
            self._dl_pb.pack(fill="x", padx=12, pady=(0, 4)),
            self._dl_pb.start(),
            self._dl_status_lbl.configure(text=f"Đang tải {fname}…",
                                          text_color=TEAL)
        ))
        self._log(f"Tải file: {fname}…", "header")

        # Thử HTTP trước (/file/download?name=...), fallback TCP
        url = f"http://{ip}/file/download?name={fname}"
        data = b""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ChuyenGUI/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
        except:
            path = f"/audio.wav" if fname == "audio.wav" else f"/{fname}"
            data = tcp_download(ip, TCP_PORT, path, timeout=20)

        self.after(0, lambda: (
            self._dl_pb.stop(),
            self._dl_pb.pack_forget()
        ))

        if len(data) < 44:
            self._log(f"Tải thất bại ({len(data)} bytes)", "err")
            self.after(0, lambda: self._dl_status_lbl.configure(
                text="Tải thất bại", text_color=RED))
            return

        # Lưu vào Downloads
        dl_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(dl_dir, exist_ok=True)
        save_path = os.path.join(dl_dir, fname)
        try:
            with open(save_path, "wb") as f:
                f.write(data)
            kb = len(data) // 1024
            self._log(f"✓  Đã lưu: {save_path}  ({kb} KB)", "ok")
            self.after(0, lambda k=kb, n=fname: self._dl_status_lbl.configure(
                text=f"✓  {n}  ({k} KB)", text_color=GREEN))
            self._show_toast(f"✓  Đã tải: {fname}  ({kb} KB)")
            try:
                subprocess.Popen(f'explorer /select,"{save_path}"')
            except: pass
        except PermissionError:
            save_path2 = os.path.join(os.path.expanduser("~"), "Desktop", fname)
            with open(save_path2, "wb") as f:
                f.write(data)
            self._log(f"✓  Lưu vào Desktop: {fname}", "ok")
            self._show_toast(f"✓  Đã tải: {fname} (Desktop)")

    # ─────────────────────────────────────────────────────────────────────────
    # DELETE
    # ─────────────────────────────────────────────────────────────────────────
    def _delete_file(self, fname: str):
        ip = self._active_ip
        if not ip:
            self._log("Chưa kết nối", "warn"); return

        safe = fname.lstrip("/")
        self._log(f"Xóa: {safe}…", "header")
        resp = http_post(ip, HTTP_PORT, f"/file/delete?name={safe}")
        if resp and ("ok" in resp or "deleted" in resp) and "error" not in resp.lower():
            self._log(f"✓  Đã xóa: {safe}", "ok")
            self._show_toast(f"✓  Đã xóa: {safe}")
            self.after(500, self._refresh_filelist)
        else:
            self._log(f"Xóa thất bại: {resp[:80]}", "err")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _open_downloads(self):
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        try: subprocess.Popen(f'explorer "{dl}"')
        except: pass

    def _log(self, msg, tag="info"):
        def _do():
            self.log.config(state="normal")
            ts = time.strftime("%H:%M:%S")
            self.log.insert("end", f"[{ts}]  ", "prompt")
            self.log.insert("end", f"{msg}\n", tag)
            self.log.see("end")
            self.log.config(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _show_toast(self, msg, error=False):
        color        = RED if error else "#1e4d2b"
        border_color = RED if error else GREEN
        toast = ctk.CTkFrame(self, fg_color=color, corner_radius=10,
                              border_color=border_color, border_width=1)
        toast.place(relx=0.5, y=70, anchor="n")
        ctk.CTkLabel(toast, text=msg,
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=TEXT, padx=18, pady=10).pack()
        self.after(3500, toast.destroy)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, os
    if sys.platform == "win32":
        os.system("color")   # kích hoạt ANSI
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("Thiếu thư viện customtkinter.")
        print("Cài bằng lệnh:  pip install customtkinter")
        input("Nhấn Enter để thoát...")
        sys.exit(1)

    app = App()
    app.mainloop()
