"""
chuyen.py — PHANTOM Secure File Encrypt & Transfer
Style: Cyberpunk City Night — navy depths + electric blue + neon cyan-teal
Matches gui_phantom.py layout exactly (nav rail | sidebar | content)
Run:   .venv\Scripts\python chuyen.py
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import sys, socket, threading, os, time, subprocess, json
import zipfile, hashlib, io, struct, urllib.request, urllib.error

# ── PHANTOM 3-layer crypto ────────────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    from cryptography.hazmat.primitives import hmac as _hmac, hashes as _hashes
    from cryptography.hazmat.backends import default_backend as _backend
    import secrets as _secrets
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

_PHTM_MAGIC, _PHTM_VERSION, _PHTM_KEY_SZ = b"PHTM", 2, 32

def _phtm_load_key(path):
    d = open(path, "rb").read()
    if len(d) < _PHTM_KEY_SZ:
        raise ValueError(f"Key file too short ({len(d)} bytes)")
    return d[:_PHTM_KEY_SZ]

def _phtm_derive(master):
    dk = lambda tag: hashlib.sha256(master + tag).digest()
    return dk(b"AES-GCM"), dk(b"HMAC-SHA256"), dk(b"CHACHA20")

def _phtm_encrypt_3layer(data: bytes, master: bytes) -> bytes:
    k_aes, k_hmac, k_chacha = _phtm_derive(master)
    # Layer 1: AES-256-GCM
    n_aes = _secrets.token_bytes(12)
    ct1   = AESGCM(k_aes).encrypt(n_aes, data, None)
    # Layer 2: HMAC-SHA256
    h = _hmac.HMAC(k_hmac, _hashes.SHA256(), backend=_backend())
    h.update(n_aes + ct1)
    hmac_tag = h.finalize()
    # Layer 3: ChaCha20-Poly1305
    n_cha   = _secrets.token_bytes(12)
    payload = n_aes + ct1 + hmac_tag
    ct3     = ChaCha20Poly1305(k_chacha).encrypt(n_cha, payload, None)
    return n_cha + ct3

def _phtm_pack_bin(zip_bytes: bytes) -> bytes:
    md5 = hashlib.md5(zip_bytes).digest()
    n   = len(zip_bytes)
    return _PHTM_MAGIC + struct.pack("<I", _PHTM_VERSION) + md5 + struct.pack("<I", n) + zip_bytes

def generate_key_file(path: str):
    master  = _secrets.token_bytes(_PHTM_KEY_SZ)
    pub_fp  = hashlib.sha256(master).hexdigest()
    open(path, "wb").write(master)
    return path, pub_fp

# ── Paths & network ───────────────────────────────────────────────────────────
_KNOWN_IPS = [
    ("192.168.4.1", "Phantom-1"),
    ("192.168.5.1", "Phantom-2"),
    ("192.168.6.1", "Phantom-3"),
    ("192.168.7.1", "Phantom-4"),
]
HTTP_PORT = 80
TCP_PORT  = 8080

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Cyberpunk City Night palette ──────────────────────────────────────────────
C_BG        = "#010326"
C_CARD      = "#010440"
C_SURFACE   = "#020659"
C_NAV       = "#020550"
C_ROW       = "#010440"
C_ROW_ALT   = "#020552"
C_BORDER    = "#0A1580"
C_BLUE      = "#030BA6"
C_BLUE_D    = "#020880"
C_BLUE_BR   = "#1A2FFF"
C_PINK      = "#1BF2DD"
C_PINK_D    = "#13C4B2"
C_GREEN     = "#00FFB3"
C_RED       = "#FF0050"
C_ORANGE    = "#FF6B00"
C_TEAL      = "#00D4FF"
C_PURPLE    = "#1BF2DD"
C_PURPLE_D  = "#13C4B2"
C_LABEL     = "#F0F4FF"
C_LABEL2    = "#A0AAEE"
C_LABEL3    = "#6878CC"
C_FILL3     = "#020756"
C_GLOW      = "#1BF2DD40"

# ── Network helpers ───────────────────────────────────────────────────────────
def tcp_upload(host, port, data: bytes, timeout=30, filename=""):
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host, port))
        req = (f"POST /upload HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Content-Type: application/octet-stream\r\nContent-Length: {len(data)}\r\n"
               + (f"X-Filename: {filename}\r\n" if filename else "")
               + "Connection: close\r\n\r\n").encode()
        s.sendall(req)
        sent = 0
        while sent < len(data):
            s.sendall(data[sent:sent+4096]); sent += min(4096, len(data)-sent)
        resp = b""
        s.settimeout(12)
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                resp += c
        except: pass
        return resp.decode(errors="replace"), sent
    except Exception as e: return f"ERROR: {e}", 0
    finally:
        try: s.close()
        except: pass

def http_get(host, port, path, timeout=4):
    try:
        s = socket.socket(); s.settimeout(timeout)
        s.connect((host, port))
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        d = b""
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                d += c
        except: pass
        s.close()
        idx = d.find(b"\r\n\r\n")
        return d[idx+4:].decode(errors="replace") if idx >= 0 else ""
    except: return ""

def http_get_json(url, timeout=4):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PhantomGUI/4.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except: return None

def http_post(host, port, path, timeout=6):
    try:
        s = socket.socket(); s.settimeout(timeout)
        s.connect((host, port))
        s.sendall(f"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        d = b""
        try:
            while True:
                c = s.recv(4096)
                if not c: break
                d += c
        except: pass
        s.close()
        idx = d.find(b"\r\n\r\n")
        return d[idx+4:].decode(errors="replace") if idx >= 0 else ""
    except: return ""

def scan_phantoms(known=_KNOWN_IPS, timeout=2):
    found = []
    def _check(ip, name):
        try:
            req = urllib.request.Request(f"http://{ip}/status",
                                         headers={"User-Agent": "PhantomGUI/4.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            pk   = d.get("public_key", "")
            last4 = pk[-4:].upper() if len(pk) >= 4 else "????"
            found.append((ip, name, last4, d))
        except: pass
    threads = [threading.Thread(target=_check, args=(ip, nm), daemon=True)
               for ip, nm in known]
    for t in threads: t.start()
    for t in threads: t.join()
    return found

# ═════════════════════════════════════════════════════════════════════════════
# WIDGETS — same helpers as gui_phantom.py
# ═════════════════════════════════════════════════════════════════════════════
def ios_card(parent, **kw):
    defaults = dict(fg_color=C_CARD, corner_radius=12,
                    border_color=C_BORDER, border_width=1)
    defaults.update(kw)
    return ctk.CTkFrame(parent, **defaults)

def ios_btn(parent, text, command, style="fill", **kw):
    base = dict(font=ctk.CTkFont("Segoe UI", 16),
                corner_radius=10, height=44, command=command)
    if style == "fill":
        base.update(fg_color=C_PINK, hover_color=C_PINK_D,
                    text_color="#010326")
    elif style == "purple":
        base.update(fg_color=C_PURPLE, hover_color=C_PURPLE_D,
                    text_color="#010326")
    elif style == "tinted":
        base.update(fg_color=C_BLUE, hover_color=C_BLUE_D,
                    text_color=C_LABEL)
    elif style == "ghost":
        base.update(fg_color=C_FILL3, hover_color=C_SURFACE,
                    border_color=C_BORDER, border_width=1,
                    text_color=C_LABEL2)
    elif style == "danger":
        base.update(fg_color=C_PINK, hover_color=C_PINK_D,
                    border_color=C_PINK, border_width=1,
                    text_color="#010326")
    base.update(kw)
    return ctk.CTkButton(parent, text=text, **base)

def ios_entry(parent, **kw):
    defaults = dict(fg_color=C_FILL3, border_color=C_BORDER,
                    border_width=1, text_color=C_LABEL,
                    placeholder_text_color=C_LABEL3,
                    height=40, corner_radius=8,
                    font=ctk.CTkFont("Consolas", 14))
    defaults.update(kw)
    return ctk.CTkEntry(parent, **defaults)

# ═════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ═════════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Phantom Encrypt")
        self.geometry("1080x680")
        self.minsize(860, 540)
        self.configure(fg_color=C_BG)

        # state
        self._selected_files: list  = []
        self._bin_bytes             = None
        self._bundle_name           = ""
        self._key_path              = ""
        self._key_bytes             = None
        self._key_pub_fp            = ""
        self._spin_angle            = 0
        self._spinning              = False
        self._phantoms: list        = []
        self._active_ip             = ""
        self._active_name           = ""
        self._sync_status: dict     = {}

        self._build_ui()
        self.after(400, self._start_spinner)
        if not _CRYPTO_OK:
            self.after(800, lambda: self._log(
                "Missing: pip install cryptography", "err"))
        threading.Thread(target=self._poll_detect, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # ROOT LAYOUT  (3-column: nav │ sidebar │ content)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=0)  # nav rail
        root.grid_columnconfigure(1, weight=0)  # sidebar
        root.grid_columnconfigure(2, weight=1)  # content

        # ── Nav rail ──────────────────────────────────────────────────────────
        nav = ctk.CTkFrame(root, fg_color=C_NAV, width=64, corner_radius=0)
        nav.grid(row=0, column=0, sticky="nsew")
        nav.grid_propagate(False)
        self._build_nav(nav)
        tk.Frame(root, bg=C_BORDER, width=1).grid(row=0, column=0,
            sticky="nse", padx=(63, 0))

        # ── Sidebar ───────────────────────────────────────────────────────────
        self._sidebar = ctk.CTkFrame(root, fg_color=C_CARD, width=264,
                                     corner_radius=0)
        self._sidebar.grid(row=0, column=1, sticky="nsew")
        self._sidebar.grid_propagate(False)
        tk.Frame(root, bg=C_BORDER, width=1).grid(row=0, column=1,
            sticky="nse", padx=(263, 0))

        # ── Content ───────────────────────────────────────────────────────────
        self._content = ctk.CTkFrame(root, fg_color=C_BG, corner_radius=0)
        self._content.grid(row=0, column=2, sticky="nsew", padx=(1, 0))

        self._build_sidebar(self._sidebar)
        self._build_content(self._content)

    # ─────────────────────────────────────────────────────────────────────────
    # NAV RAIL
    # ─────────────────────────────────────────────────────────────────────────
    def _build_nav(self, parent):
        parent.pack_propagate(False)

        badge = ctk.CTkFrame(parent, fg_color=C_PURPLE, width=36, height=36,
                             corner_radius=9)
        badge.pack(pady=(20, 14))
        badge.pack_propagate(False)
        ctk.CTkLabel(badge, text="🔒", font=ctk.CTkFont("Segoe UI", 16),
                     text_color="#010326").place(relx=0.5, rely=0.5, anchor="center")

        tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkFrame(parent, fg_color="transparent").pack(fill="both", expand=True)

        self._status_dot = ctk.CTkLabel(parent, text="●",
                                        font=ctk.CTkFont("Segoe UI", 11),
                                        text_color=C_ORANGE)
        self._status_dot.pack(pady=(0, 4))

        self._conn_spinner = ctk.CTkLabel(parent, text="◌",
                                          font=ctk.CTkFont("Consolas", 15),
                                          text_color=C_LABEL3)
        self._conn_spinner.pack(pady=(0, 14))

    # ─────────────────────────────────────────────────────────────────────────
    # SIDEBAR — form
    # ─────────────────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        dark = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=0)
        dark.pack(fill="both", expand=True)

        # Title
        title_row = ctk.CTkFrame(dark, fg_color="transparent")
        title_row.pack(fill="x", padx=16, pady=(18, 0))
        ctk.CTkLabel(title_row, text="PHANTOM",
                     font=ctk.CTkFont("Consolas", 18, "bold"),
                     text_color=C_PURPLE).pack(side="left")
        ctk.CTkLabel(title_row, text=" ENCRYPT",
                     font=ctk.CTkFont("Consolas", 18, "bold"),
                     text_color=C_LABEL).pack(side="left")

        ctk.CTkLabel(dark, text="3-Layer Cryptographic Engine",
                     font=ctk.CTkFont("Consolas", 13),
                     text_color=C_LABEL3).pack(anchor="w", padx=16, pady=(2, 12))

        tk.Frame(dark, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 12))

        # ── Connection ────────────────────────────────────────────────────────
        ctk.CTkLabel(dark, text="◈  CONNECTION",
                     font=ctk.CTkFont("Consolas", 13),
                     text_color=C_LABEL2, anchor="w"
                     ).pack(fill="x", padx=16, pady=(0, 4))

        conn_card = ios_card(dark)
        conn_card.pack(fill="x", padx=14, pady=(0, 10))

        cr = ctk.CTkFrame(conn_card, fg_color="transparent")
        cr.pack(fill="x", padx=12, pady=(10, 4))

        self._conn_lbl = ctk.CTkLabel(cr, text="Scanning…",
                                      font=ctk.CTkFont("Consolas", 14),
                                      text_color=C_LABEL3)
        self._conn_lbl.pack(side="left")

        self._ip_lbl = ctk.CTkLabel(conn_card, text="Connect to Phantom WiFi",
                                    font=ctk.CTkFont("Consolas", 13),
                                    text_color=C_LABEL3, anchor="w")
        self._ip_lbl.pack(fill="x", padx=12, pady=(0, 10))

        tk.Frame(dark, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 10))

        # ── Select files ──────────────────────────────────────────────────────
        ctk.CTkLabel(dark, text="◈  INPUT FILES",
                     font=ctk.CTkFont("Consolas", 13),
                     text_color=C_LABEL2, anchor="w"
                     ).pack(fill="x", padx=16, pady=(0, 4))

        file_card = ios_card(dark)
        file_card.pack(fill="x", padx=14, pady=(0, 10))

        list_bg = tk.Frame(file_card, bg=C_FILL3)
        list_bg.pack(fill="x", padx=12, pady=(10, 4))

        self._file_listbox = tk.Listbox(
            list_bg, bg=C_FILL3, fg=C_LABEL,
            selectbackground=C_BLUE, selectforeground="white",
            font=("Consolas", 12), relief="flat", bd=0,
            height=3, activestyle="none", highlightthickness=0)
        self._file_listbox.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        vsb_lb = tk.Scrollbar(list_bg, command=self._file_listbox.yview,
                              bg=C_CARD, troughcolor=C_CARD,
                              bd=0, highlightthickness=0, width=5)
        vsb_lb.pack(side="right", fill="y")
        self._file_listbox.configure(yscrollcommand=vsb_lb.set)

        btn_row = ctk.CTkFrame(file_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(0, 4))

        ios_btn(btn_row, "+ Add", self._browse, style="tinted",
                height=32, font=ctk.CTkFont("Consolas", 12)
                ).pack(side="left", padx=(0, 4))
        ios_btn(btn_row, "Remove", self._remove_selected,
                style="danger", height=32,
                font=ctk.CTkFont("Consolas", 12)
                ).pack(side="left", padx=(0, 4))
        ios_btn(btn_row, "Clear", self._clear_files,
                style="ghost", height=32,
                font=ctk.CTkFont("Consolas", 12)
                ).pack(side="left")

        self._file_count_lbl = ctk.CTkLabel(file_card, text="No files selected",
                                             font=ctk.CTkFont("Consolas", 13),
                                             text_color=C_LABEL3, anchor="w")
        self._file_count_lbl.pack(fill="x", padx=12, pady=(0, 8))

        tk.Frame(dark, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 10))

        # ── Key file ──────────────────────────────────────────────────────────
        ctk.CTkLabel(dark, text="◈  KEY FILE (.key)",
                     font=ctk.CTkFont("Consolas", 13),
                     text_color=C_LABEL2, anchor="w"
                     ).pack(fill="x", padx=16, pady=(0, 4))

        key_card = ios_card(dark)
        key_card.pack(fill="x", padx=14, pady=(0, 10))

        kr = ctk.CTkFrame(key_card, fg_color="transparent")
        kr.pack(fill="x", padx=10, pady=(10, 4))

        self._key_entry_var = ctk.StringVar()
        ke = ctk.CTkEntry(kr, textvariable=self._key_entry_var,
                          fg_color=C_FILL3, border_color=C_BORDER,
                          border_width=1, text_color=C_LABEL,
                          placeholder_text="No key loaded",
                          placeholder_text_color=C_LABEL3,
                          height=36, corner_radius=6,
                          font=ctk.CTkFont("Consolas", 12))
        ke.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(kr, text="…", width=32, height=32,
                      fg_color=C_FILL3, hover_color=C_SURFACE,
                      border_color=C_BORDER, border_width=1,
                      text_color=C_LABEL2, corner_radius=6,
                      command=self._browse_key).pack(side="right")

        gen_row = ctk.CTkFrame(key_card, fg_color="transparent")
        gen_row.pack(fill="x", padx=10, pady=(0, 4))

        ios_btn(gen_row, "⟳ Generate New Key", self._generate_key,
                style="ghost", height=32,
                font=ctk.CTkFont("Consolas", 12)
                ).pack(fill="x")

        self._key_status_lbl = ctk.CTkLabel(key_card, text="",
                                             font=ctk.CTkFont("Consolas", 13),
                                             text_color=C_LABEL3, anchor="w")
        self._key_status_lbl.pack(fill="x", padx=12, pady=(0, 8))

        tk.Frame(dark, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 10))

        # ── Encrypt button ────────────────────────────────────────────────────
        self._enc_btn = ctk.CTkButton(
            dark,
            text="▶  RUN ENCRYPT",
            font=ctk.CTkFont("Consolas", 16, "bold"),
            fg_color=C_PURPLE, hover_color=C_PURPLE_D,
            text_color="#010326",
            height=42, corner_radius=8,
            command=lambda: threading.Thread(
                target=self._do_encrypt, daemon=True).start(),
            state="normal" if _CRYPTO_OK else "disabled")
        self._enc_btn.pack(fill="x", padx=14, pady=(0, 6))

        # Global progress bar
        self._enc_pb = ctk.CTkProgressBar(dark, mode="determinate", height=3,
                                          progress_color=C_PURPLE,
                                          fg_color=C_FILL3, corner_radius=1)
        self._enc_pb.set(0)
        self._enc_pb.pack(fill="x", padx=14, pady=(0, 2))
        self._enc_pb.pack_forget()

        self._enc_status_lbl = ctk.CTkLabel(
            dark,
            text="READY" if _CRYPTO_OK else "⚠  pip install cryptography",
            font=ctk.CTkFont("Consolas", 13),
            text_color=C_GREEN if _CRYPTO_OK else C_ORANGE,
            anchor="w")
        self._enc_status_lbl.pack(fill="x", padx=16, pady=(0, 4))

        # Send / Save row
        sr = ctk.CTkFrame(dark, fg_color="transparent")
        sr.pack(fill="x", padx=14, pady=(0, 4))

        self._send_btn = ios_btn(sr, "📡  Send to Phantom",
                                 command=lambda: threading.Thread(
                                     target=self._do_send, daemon=True).start(),
                                 style="tinted", height=36,
                                 font=ctk.CTkFont("Consolas", 13),
                                 state="disabled")
        self._send_btn.pack(fill="x", pady=(0, 4))

        self._save_btn = ios_btn(sr, "💾  Save .bin",
                                 command=self._save_bin,
                                 style="ghost", height=32,
                                 font=ctk.CTkFont("Consolas", 12),
                                 state="disabled")
        self._save_btn.pack(fill="x")

    # ─────────────────────────────────────────────────────────────────────────
    # CONTENT — 3-layer visualizer + terminal log
    # ─────────────────────────────────────────────────────────────────────────
    def _build_content(self, parent):
        dark_bg = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        dark_bg.pack(fill="both", expand=True)

        # ── Top: 3 layer cards ───────────────────────────────────────────────
        layers_row = ctk.CTkFrame(dark_bg, fg_color="transparent")
        layers_row.pack(fill="x", padx=20, pady=(16, 10))
        layers_row.grid_columnconfigure((0, 1, 2), weight=1)

        _LAYERS = [
            ("LAYER 1",  "AES-256-GCM",        "Initial block encrypt",     C_TEAL),
            ("LAYER 2",  "HMAC-SHA256",          "Integrity authentication",  C_ORANGE),
            ("LAYER 3",  "ChaCha20-Poly1305",    "Final stream encrypt",      C_PURPLE),
        ]

        self._layer_cards = []
        for col, (layer_name, algo, desc, color) in enumerate(_LAYERS):
            card = ctk.CTkFrame(layers_row, fg_color=C_CARD,
                                corner_radius=12, border_color=C_BORDER,
                                border_width=1)
            card.grid(row=0, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 8, 0))

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=14, pady=(12, 4))
            ctk.CTkLabel(top, text=layer_name,
                         font=ctk.CTkFont("Consolas", 15, "bold"),
                         text_color=C_LABEL2).pack(side="left")
            dot = ctk.CTkLabel(top, text="●",
                               font=ctk.CTkFont("Consolas", 15),
                               text_color=C_LABEL3)
            dot.pack(side="right")

            ctk.CTkLabel(card, text=algo,
                         font=ctk.CTkFont("Consolas", 16, "bold"),
                         text_color=color).pack(anchor="w", padx=14, pady=(0, 2))

            ctk.CTkLabel(card, text=desc,
                         font=ctk.CTkFont("Consolas", 13),
                         text_color=C_LABEL2).pack(anchor="w", padx=14, pady=(0, 8))

            hash_lbl = ctk.CTkLabel(card, text="HASH: —",
                                    font=ctk.CTkFont("Consolas", 12),
                                    text_color=C_LABEL3,
                                    wraplength=180, anchor="w", justify="left")
            hash_lbl.pack(fill="x", padx=14, pady=(0, 6))

            bar = ctk.CTkProgressBar(card, mode="determinate", height=2,
                                     progress_color=color, fg_color=C_FILL3,
                                     corner_radius=1)
            bar.set(0)
            bar.pack(fill="x", padx=14, pady=(0, 2))

            pct = ctk.CTkLabel(card, text="0%",
                               font=ctk.CTkFont("Consolas", 15),
                               text_color=color, anchor="e")
            pct.pack(fill="x", padx=14, pady=(0, 10))

            self._layer_cards.append((card, hash_lbl, bar, pct, dot, color))

        # ── Global progress bar ───────────────────────────────────────────────
        gbar_row = ctk.CTkFrame(dark_bg, fg_color="transparent")
        gbar_row.pack(fill="x", padx=20, pady=(0, 4))

        ctk.CTkLabel(gbar_row, text="OVERALL",
                     font=ctk.CTkFont("Consolas", 15),
                     text_color=C_LABEL).pack(side="left")

        self._enc_gbar_pct = ctk.CTkLabel(gbar_row, text="0%",
                                          font=ctk.CTkFont("Consolas", 16, "bold"),
                                          text_color=C_PURPLE)
        self._enc_gbar_pct.pack(side="right")

        self._enc_gbar = ctk.CTkProgressBar(dark_bg, mode="determinate", height=4,
                                            progress_color=C_PURPLE,
                                            fg_color=C_FILL3, corner_radius=2)
        self._enc_gbar.set(0)
        self._enc_gbar.pack(fill="x", padx=20, pady=(0, 10))

        # ── Terminal log ──────────────────────────────────────────────────────
        log_hdr = ctk.CTkFrame(dark_bg, fg_color="transparent")
        log_hdr.pack(fill="x", padx=20, pady=(0, 4))

        ctk.CTkLabel(log_hdr, text="$ TERMINAL OUTPUT",
                     font=ctk.CTkFont("Consolas", 14, "bold"),
                     text_color=C_LABEL2).pack(side="left")

        ctk.CTkButton(log_hdr, text="CLEAR", width=60, height=24,
                      font=ctk.CTkFont("Consolas", 12),
                      fg_color=C_FILL3, hover_color=C_SURFACE,
                      text_color=C_LABEL2, corner_radius=4,
                      command=self._clear_log).pack(side="right")

        # Bundle info label
        self._bundle_lbl = ctk.CTkLabel(dark_bg, text="",
                                        font=ctk.CTkFont("Consolas", 13),
                                        text_color=C_TEAL, anchor="w")
        self._bundle_lbl.pack(fill="x", padx=20, pady=(0, 4))

        log_outer = tk.Frame(dark_bg, bg=C_CARD)
        log_outer.pack(fill="both", expand=True, padx=18, pady=(0, 16))

        self.log = tk.Text(
            log_outer, font=("Consolas", 13),
            bg=C_CARD, fg=C_LABEL,
            relief="flat", bd=0,
            state="disabled", wrap="word",
            highlightthickness=0,
            selectbackground=C_SURFACE,
            padx=14, pady=10,
            insertbackground=C_GREEN)

        vsb = tk.Scrollbar(log_outer, orient="vertical",
                           command=self.log.yview,
                           bg=C_CARD, troughcolor=C_CARD,
                           bd=0, highlightthickness=0, width=5)
        self.log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y", pady=4)
        self.log.pack(side="left", fill="both", expand=True)

        self.log.tag_config("ok",     foreground=C_GREEN)
        self.log.tag_config("err",    foreground=C_RED)
        self.log.tag_config("info",   foreground=C_ORANGE)
        self.log.tag_config("dim",    foreground=C_LABEL2)
        self.log.tag_config("head",   foreground=C_PURPLE,
                            font=("Consolas", 13, "bold"))
        self.log.tag_config("prompt", foreground=C_LABEL2)

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER ANIMATION
    # ─────────────────────────────────────────────────────────────────────────
    def _animate_layer(self, layer_idx: int, hash_hex: str,
                       duration_ms: int, on_done):
        card, hash_lbl, bar, pct, dot, color = self._layer_cards[layer_idx]
        steps    = 40
        interval = max(20, duration_ms // steps)
        start_overall = layer_idx / 3.0

        dot.configure(text="●", text_color=color)
        short = hash_hex[:16] + "…" if len(hash_hex) > 16 else hash_hex
        hash_lbl.configure(text=f"HASH: {short}", text_color=color)

        def _tick(step=0):
            if step > steps:
                bar.set(1.0)
                pct.configure(text="100%")
                dot.configure(text="✓", text_color=color)
                hash_lbl.configure(
                    text=f"HASH: {hash_hex[:32]}…" if len(hash_hex) > 32
                    else f"HASH: {hash_hex}",
                    text_color=color)
                done_overall = (layer_idx + 1) / 3.0
                self._enc_gbar.set(done_overall)
                self._enc_gbar_pct.configure(text=f"{int(done_overall*100)}%")
                on_done()
                return
            frac = step / steps
            bar.set(frac)
            pct.configure(text=f"{int(frac*100)}%")
            g = start_overall + frac / 3.0
            self._enc_gbar.set(g)
            self._enc_gbar_pct.configure(text=f"{int(g*100)}%")
            self.after(interval, lambda: _tick(step + 1))

        _tick()

    def _reset_layers(self):
        for (card, hash_lbl, bar, pct, dot, color) in self._layer_cards:
            bar.set(0)
            pct.configure(text="0%", text_color=color)
            dot.configure(text="●", text_color=C_LABEL3)
            hash_lbl.configure(text="HASH: —", text_color=C_LABEL3)
        self._enc_gbar.set(0)
        self._enc_gbar_pct.configure(text="0%", text_color=C_PURPLE)

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENCRYPT RUNNER
    # ─────────────────────────────────────────────────────────────────────────
    def _do_encrypt(self):
        if not self._selected_files:
            self.after(0, lambda: self._enc_status_lbl.configure(
                text="⚠  NO FILES SELECTED", text_color=C_ORANGE)); return
        if not _CRYPTO_OK:
            self.after(0, lambda: self._enc_status_lbl.configure(
                text="⚠  pip install cryptography", text_color=C_ORANGE)); return
        if not self._key_bytes:
            self.after(0, lambda: self._enc_status_lbl.configure(
                text="⚠  NO KEY FILE", text_color=C_ORANGE)); return

        files = list(self._selected_files)
        master = self._key_bytes

        self.after(0, self._reset_layers)
        self.after(0, self._clear_log)
        self.after(0, lambda: self._enc_btn.configure(state="disabled"))
        self.after(0, lambda: self._send_btn.configure(state="disabled"))
        self.after(0, lambda: self._save_btn.configure(state="disabled"))
        self.after(0, lambda: self._enc_status_lbl.configure(
            text="RUNNING…", text_color=C_TEAL))
        self.after(0, lambda: self._bundle_lbl.configure(text=""))

        def _run():
            self._log_msg("═" * 52)
            self._log_msg(f"  FILES  : {len(files)} file(s)")
            self._log_msg(f"  KEY    : {os.path.basename(self._key_path)}")
            self._log_msg("═" * 52)
            time.sleep(0.3)

            # Derive sub-keys
            try:
                k_aes, k_hmac, k_chacha = _phtm_derive(master)
                h_aes    = hashlib.sha256(k_aes   ).hexdigest()
                h_hmac   = hashlib.sha256(k_hmac  ).hexdigest()
                h_chacha = hashlib.sha256(k_chacha).hexdigest()
            except Exception as e:
                self._log_msg(f"✗  KEY DERIVE ERROR: {e}")
                self.after(0, lambda: self._enc_status_lbl.configure(
                    text=f"ERROR: {e}", text_color=C_RED))
                self.after(0, lambda: self._enc_btn.configure(state="normal"))
                return

            # Layer 1 — AES-256-GCM
            self._log_msg("\n[LAYER 1]  AES-256-GCM  —  initial block encrypt")
            time.sleep(0.2)
            l1_done = threading.Event()
            self.after(0, lambda: self._animate_layer(0, h_aes, 5000, l1_done.set))
            l1_done.wait()
            self._log_msg(f"    ✓  AES-256 key hash  : {h_aes[:32]}…")
            time.sleep(0.3)

            # Layer 2 — HMAC-SHA256
            self._log_msg("\n[LAYER 2]  HMAC-SHA256  —  integrity authentication")
            time.sleep(0.2)
            l2_done = threading.Event()
            self.after(0, lambda: self._animate_layer(1, h_hmac, 6000, l2_done.set))
            l2_done.wait()
            self._log_msg(f"    ✓  HMAC key hash     : {h_hmac[:32]}…")
            time.sleep(0.3)

            # Layer 3 — ChaCha20
            self._log_msg("\n[LAYER 3]  ChaCha20-Poly1305  —  final stream encrypt")
            time.sleep(0.2)
            l3_done = threading.Event()
            self.after(0, lambda: self._animate_layer(2, h_chacha, 5000, l3_done.set))
            l3_done.wait()
            self._log_msg(f"    ✓  ChaCha20 key hash : {h_chacha[:32]}…")
            time.sleep(0.3)

            # Actual encryption
            self._log_msg("\n[OUTPUT]  Building encrypted bundle…")
            try:
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                    for fpath in files:
                        fname = os.path.basename(fpath)
                        self._log_msg(f"    Encrypting: {fname}")
                        raw = open(fpath, "rb").read()
                        enc = _phtm_encrypt_3layer(raw, master)
                        zf.writestr(fname + ".enc", enc)
                        self._log_msg(f"    ✓  {len(raw):,} → {len(enc):,} bytes")

                zip_bytes  = zip_buf.getvalue()
                bin_bytes  = _phtm_pack_bin(zip_bytes)
                md5_str    = hashlib.md5(bin_bytes).hexdigest()
                ts         = time.strftime("%Y%m%d_%H%M%S")
                bundle_name = f"phantom_{ts}"

                self._bin_bytes   = bin_bytes
                self._bundle_name = bundle_name

                size_kb = len(bin_bytes) / 1024
                info = (f"📦  {bundle_name}.bin  ·  {size_kb:.1f} KB  "
                        f"·  {len(files)} file(s)")
                self._log_msg("═" * 52)
                self._log_msg(f"✔  DONE  {bundle_name}.bin")
                self._log_msg(f"    SIZE : {size_kb:.1f} KB")
                self._log_msg(f"    MD5  : {md5_str}")
                self._log_msg("═" * 52)

                self.after(0, lambda i=info: self._bundle_lbl.configure(text=i))
                self.after(0, lambda: self._enc_status_lbl.configure(
                    text=f"DONE  {len(files)} FILE(S) ENCRYPTED",
                    text_color=C_GREEN))
                self.after(0, lambda: self._send_btn.configure(state="normal"))
                self.after(0, lambda: self._save_btn.configure(state="normal"))
                self._show_toast(f"✓  Encrypted {len(files)} file(s)  {size_kb:.1f} KB")

            except Exception as e:
                self._log_msg(f"✗  ENCRYPT ERROR: {e}")
                self.after(0, lambda: self._enc_status_lbl.configure(
                    text=f"ERROR: {e}", text_color=C_RED))

            self.after(0, lambda: self._enc_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # SAVE .bin
    # ─────────────────────────────────────────────────────────────────────────
    def _save_bin(self):
        if not self._bin_bytes: return
        path = filedialog.asksaveasfilename(
            defaultextension=".bin",
            initialfile=self._bundle_name + ".bin",
            filetypes=[("Binary file", "*.bin"), ("All files", "*.*")])
        if path:
            open(path, "wb").write(self._bin_bytes)
            self._log(f"✓  Saved: {path}", "ok")
            self._show_toast(f"✓  Saved {os.path.basename(path)}")

    # ─────────────────────────────────────────────────────────────────────────
    # SEND → Phantom
    # ─────────────────────────────────────────────────────────────────────────
    def _do_send(self):
        if not self._bin_bytes:
            self._log("No bundle — run Encrypt first", "warn"); return
        ip = self._active_ip
        if not ip:
            self._log("Not connected to Phantom", "warn"); return

        fname = self._bundle_name + ".bin"
        data  = self._bin_bytes

        self.after(0, lambda: (
            self._send_btn.configure(state="disabled"),
            self._enc_status_lbl.configure(
                text=f"Sending {len(data)/1024:.1f} KB…", text_color=C_TEAL)
        ))
        self._log(f"[Send] {fname}  {len(data)//1024} KB → {self._active_name}…", "head")

        t0 = time.time()
        resp, sent = tcp_upload(ip, TCP_PORT, data, filename=fname)
        elapsed = time.time() - t0

        status_line = resp.split("\r\n")[0] if resp else ""
        body = resp.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in resp else ""
        spiffs_ok = True
        try:
            rj = json.loads(body)
            spiffs_ok = rj.get("spiffs_saved", rj.get("ok", True))
        except: pass

        http_ok = "200" in status_line or "201" in status_line or '"ok"' in resp
        success = http_ok and "ERROR" not in resp and spiffs_ok

        self.after(0, lambda: self._send_btn.configure(state="normal"))

        if success:
            msg = f"✓  Sent  {sent//1024:.0f} KB  ({elapsed:.1f}s)"
            self.after(0, lambda m=msg: self._enc_status_lbl.configure(
                text=m, text_color=C_GREEN))
            self._log(msg, "ok")
            self._show_toast(msg)
        elif not spiffs_ok:
            msg = "⚠  Storage full on Phantom"
            self.after(0, lambda m=msg: self._enc_status_lbl.configure(
                text=m, text_color=C_ORANGE))
            self._log(msg, "warn")
            self._show_toast(msg, error=True)
        else:
            msg = f"✗  Send failed ({status_line})"
            self.after(0, lambda m=msg: self._enc_status_lbl.configure(
                text=m, text_color=C_RED))
            self._log(f"✗  {resp[:200]}", "err")
            self._show_toast(msg, error=True)

    # ─────────────────────────────────────────────────────────────────────────
    # FILE SELECTION
    # ─────────────────────────────────────────────────────────────────────────
    def _browse(self):
        paths = filedialog.askopenfilenames(
            title="Select files to encrypt",
            filetypes=[
                ("All supported",
                 "*.wav *.mp3 *.ogg *.flac *.aac "
                 "*.doc *.docx *.xls *.xlsx *.pdf "
                 "*.jpg *.jpeg *.png *.gif *.bmp *.webp "
                 "*.txt *.csv *.json *.bin"),
                ("Audio",    "*.wav *.mp3 *.ogg *.flac *.aac"),
                ("Document", "*.doc *.docx *.xls *.xlsx *.pdf *.txt *.csv"),
                ("Image",    "*.jpg *.jpeg *.png *.gif *.bmp *.webp"),
                ("All files", "*.*"),
            ])
        if paths:
            added = 0
            for p in paths:
                if p not in self._selected_files:
                    self._selected_files.append(p)
                    self._file_listbox.insert("end", os.path.basename(p))
                    added += 1
            self._update_file_count()
            if added:
                self._log(f"Added {added} file(s)", "ok")
                self._reset_bundle()

    def _remove_selected(self):
        for idx in reversed(self._file_listbox.curselection()):
            self._file_listbox.delete(idx)
            del self._selected_files[idx]
        self._update_file_count()
        self._reset_bundle()

    def _clear_files(self):
        self._file_listbox.delete(0, "end")
        self._selected_files.clear()
        self._update_file_count()
        self._reset_bundle()

    def _update_file_count(self):
        n = len(self._selected_files)
        self._file_count_lbl.configure(
            text=f"{n} file(s) selected" if n else "No files selected",
            text_color=C_LABEL2 if n else C_LABEL3)

    def _reset_bundle(self):
        self._bin_bytes = None
        self._bundle_lbl.configure(text="")
        self._save_btn.configure(state="disabled")
        self._send_btn.configure(state="disabled")

    # ─────────────────────────────────────────────────────────────────────────
    # KEY MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────
    def _browse_key(self):
        path = filedialog.askopenfilename(
            title="Select key file",
            filetypes=[("Key file", "*.key"), ("All files", "*.*")])
        if not path: return
        try:
            master = _phtm_load_key(path)
            pub_fp = hashlib.sha256(master).hexdigest()
            self._key_path   = path
            self._key_bytes  = master
            self._key_pub_fp = pub_fp
            short = os.path.basename(path)
            last4 = pub_fp[-4:].upper()
            self._key_entry_var.set(short)
            self._key_status_lbl.configure(
                text=f"✓  Key ready  […{last4}]", text_color=C_GREEN)
            self._log(f"Key loaded: {short}  […{last4}]", "ok")
            self._reset_bundle()
        except Exception as e:
            self._key_bytes = None
            self._key_status_lbl.configure(text=f"✗  {e}", text_color=C_RED)
            self._log(f"Key error: {e}", "err")

    def _generate_key(self):
        path = filedialog.asksaveasfilename(
            title="Save new key file",
            defaultextension=".key",
            initialfile="phantom.key",
            filetypes=[("Key file", "*.key"), ("All files", "*.*")])
        if not path: return
        try:
            out, pub_fp = generate_key_file(path)
            master      = _phtm_load_key(out)
            self._key_path   = out
            self._key_bytes  = master
            self._key_pub_fp = pub_fp
            last4 = pub_fp[-4:].upper()
            short = os.path.basename(out)
            self._key_entry_var.set(short)
            self._key_status_lbl.configure(
                text=f"✓  Key ready  […{last4}]", text_color=C_GREEN)
            self._log(f"Generated: {short}  […{last4}]", "ok")
            self._show_toast(f"✓  Key saved: {short}")
            self._reset_bundle()
        except Exception as e:
            self._log(f"Generate key error: {e}", "err")

    # ─────────────────────────────────────────────────────────────────────────
    # SPINNER / DETECT
    # ─────────────────────────────────────────────────────────────────────────
    _SPIN = ["◌", "◍", "●", "◍"]

    def _start_spinner(self):
        self._spinning = True
        self._tick_spinner()

    def _tick_spinner(self):
        if not self._spinning: return
        self._spin_angle = (self._spin_angle + 1) % 4
        try: self._conn_spinner.configure(text=self._SPIN[self._spin_angle])
        except: pass
        self.after(350, self._tick_spinner)

    def _poll_detect(self):
        prev_ips: set = set()
        while True:
            results = scan_phantoms()
            cur_ips = {r[0] for r in results}
            if cur_ips != prev_ips:
                prev_ips = cur_ips
                self.after(0, self._on_scan_result, results)
            if not results and self._active_ip:
                self._active_ip = ""; self._active_name = ""
                self.after(0, self._on_scan_result, [])
            time.sleep(4)

    def _on_scan_result(self, results: list):
        if not results:
            self._conn_lbl.configure(text="Scanning…", text_color=C_LABEL3)
            self._ip_lbl.configure(text="Connect to Phantom WiFi")
            self._status_dot.configure(text_color=C_ORANGE)
            self._conn_spinner.configure(text="◌", text_color=C_LABEL3)
            self._active_ip = ""; self._active_name = ""
            return
        ip, nm, l4, _ = results[0]
        self._active_ip   = ip
        self._active_name = nm
        self._conn_lbl.configure(text=f"{nm} connected", text_color=C_GREEN)
        self._ip_lbl.configure(text=f"IP: {ip}")
        self._status_dot.configure(text_color=C_GREEN)
        self._conn_spinner.configure(text="●", text_color=C_GREEN)
        self._log(f"Detected: {nm}  ({ip})", "ok")

    # ─────────────────────────────────────────────────────────────────────────
    # LOG HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _log_msg(self, msg: str):
        def _append():
            self.log.config(state="normal")
            self.log.insert("end", "> ", "prompt")
            if msg.startswith("    ✓") or msg.startswith("✔"):
                tag = "ok"
            elif msg.startswith("    ✗") or "ERROR" in msg:
                tag = "err"
            elif msg.startswith("["):
                tag = "info"
            elif msg.startswith("─") or msg.startswith("═"):
                tag = "head"
            else:
                tag = "dim"
            self.log.insert("end", msg + "\n", tag)
            self.log.see("end")
            self.log.config(state="disabled")
        self.after(0, _append)

    def _log(self, msg, tag="dim"):
        def _do():
            self.log.config(state="normal")
            self.log.insert("end", ">  ", "prompt")
            self.log.insert("end", f"{msg}\n", tag)
            self.log.see("end")
            self.log.config(state="disabled")
        try: self.after(0, _do)
        except: pass

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self._reset_layers()

    def _show_toast(self, msg, error=False):
        border = C_RED if error else C_GREEN
        fg     = C_RED if error else C_GREEN
        t = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=20,
                         border_color=border, border_width=1)
        t.place(relx=0.5, y=60, anchor="n")
        ctk.CTkLabel(t, text=msg, font=ctk.CTkFont("Consolas", 14, "bold"),
                     text_color=fg, padx=24, pady=10).pack()
        self.after(3000, t.destroy)


if __name__ == "__main__":
    app = App()
    app.mainloop()
