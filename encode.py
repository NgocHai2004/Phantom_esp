"""
encode.py — PHANTOM Secure File Encrypt & Transfer
Style: macOS Finder Light — clean white + blue accent (HIG)
Run:   .venv\Scripts\python encode.py
"""

import customtkinter as ctk
from tkinter import filedialog
import sys, socket, threading, os, time, json, tempfile, shutil
import zipfile, hashlib, io, struct, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "project_nen"))
from zipfolder.compressor import compress_folder

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
    n_aes = _secrets.token_bytes(12)
    ct1   = AESGCM(k_aes).encrypt(n_aes, data, None)
    h = _hmac.HMAC(k_hmac, _hashes.SHA256(), backend=_backend())
    h.update(n_aes + ct1)
    hmac_tag = h.finalize()
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

# ── Network ───────────────────────────────────────────────────────────────────
_KNOWN_IPS = [
    ("192.168.4.1", "Phantom-1"), ("192.168.5.1", "Phantom-2"),
    ("192.168.6.1", "Phantom-3"), ("192.168.7.1", "Phantom-4"),
]
TCP_PORT = 8080

def tcp_upload(host, port, data: bytes, timeout=30, filename=""):
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host, port))
        req = (f"POST /upload HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Content-Type: application/octet-stream\r\nContent-Length: {len(data)}\r\n"
               + (f"X-Filename: {filename}\r\n" if filename else "")
               + "Connection: close\r\n\r\n").encode()
        s.sendall(req); sent = 0
        while sent < len(data):
            s.sendall(data[sent:sent+4096]); sent += min(4096, len(data)-sent)
        resp = b""; s.settimeout(12)
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

def scan_phantoms(known=_KNOWN_IPS, timeout=2):
    found = []
    def _check(ip, name):
        try:
            req = urllib.request.Request(f"http://{ip}/status",
                                         headers={"User-Agent": "PhantomGUI/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            pk = d.get("public_key", "")
            found.append((ip, name, pk[-4:].upper() if len(pk) >= 4 else "????", d))
        except: pass
    threads = [threading.Thread(target=_check, args=(ip, nm), daemon=True) for ip, nm in known]
    for t in threads: t.start()
    for t in threads: t.join()
    return found

# ── Telegram White Theme ──────────────────────────────────────────────────────
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

C_BG        = "#FFFFFF"    # main window / content background
C_PANEL     = "#EEF2F8"    # sidebar background (very light blue-gray, like Telegram)
C_CARD      = "#FFFFFF"    # card surface
C_SURFACE   = "#F5F7FA"    # elevated surface / hover state
C_INPUT     = "#F5F7FA"    # input field background
C_BORDER    = "#E5E5EA"    # subtle border
C_BORDER_HI = "#2979FF"    # focus/active border (blue, thin)

# Text hierarchy
C_TEXT      = "#1C1C1E"    # primary — near black
C_TEXT2     = "#6E6E73"    # secondary — medium gray
C_TEXT3     = "#AEAEB2"    # tertiary / placeholder — light gray
C_WHITE     = "#FFFFFF"
C_BLACK     = "#1C1C1E"

# Accent colors — status indicators + layer card colors
C_BLUE      = "#2979FF"    # primary blue (progress bars, links)
C_GREEN     = "#34C759"    # success status
C_ORANGE    = "#FF9500"    # warning status / HMAC layer
C_RED       = "#FF3B30"    # error status
C_TEAL      = "#5AC8FA"    # info / teal layer accent
C_VIOLET    = "#AF52DE"    # ChaCha20 / purple layer accent

# Section header
C_SEC_HDR   = "#8E8E93"

# ── Fonts ─────────────────────────────────────────────────────────────────────
def _font(size=13, weight="normal"):
    for f in ["SF Pro Display", "Segoe UI", "Helvetica Neue"]:
        try: return ctk.CTkFont(f, size, weight)
        except: pass
    return ctk.CTkFont(size=size, weight=weight)

def _mono(size=13, weight="normal"):
    for f in ["JetBrains Mono", "Cascadia Code", "Consolas"]:
        try: return ctk.CTkFont(f, size, weight)
        except: pass
    return ctk.CTkFont("Consolas", size, weight)

# ── Widget factories ──────────────────────────────────────────────────────────
def tg_card(parent, **kw):
    d = dict(fg_color=C_CARD, corner_radius=12,
             border_color=C_BORDER, border_width=1)
    d.update(kw)
    return ctk.CTkFrame(parent, **d)

# keep legacy alias so any remaining call sites don't break
mac_card = tg_card

def tg_btn(parent, text, command, style="primary", **kw):
    # ALL styles = black pill + white text + subtle border for "bubble" depth
    _BLACK    = "#1C1C1E"
    _BLACK_HV = "#3A3A3C"
    _BLACK_BR = "#4A4A4C"   # slightly lighter border → gives 3-D raised look
    base = dict(corner_radius=20, height=38, command=command, font=_font(13, "bold"),
                fg_color=_BLACK, hover_color=_BLACK_HV,
                text_color="#FFFFFF",
                border_color=_BLACK_BR, border_width=1)
    # style kwarg kept for API compatibility — ignored, all black now
    base.update(kw)
    return ctk.CTkButton(parent, text=text, **base)

def pill(parent, text, color, tint):
    f = ctk.CTkFrame(parent, fg_color=tint, corner_radius=10, border_width=0)
    ctk.CTkLabel(f, text=text, font=_mono(9, "bold"),
                 text_color=color, padx=6, pady=2).pack()
    return f

# legacy alias
mac_btn = tg_btn

def tg_entry(parent, **kw):
    d = dict(fg_color=C_INPUT, border_color=C_BORDER, border_width=1,
             text_color=C_TEXT, placeholder_text_color=C_TEXT3,
             height=38, corner_radius=10, font=_font(13))
    d.update(kw)
    return ctk.CTkEntry(parent, **d)

# legacy alias
mac_entry = tg_entry

def tg_sec(parent, text):
    return ctk.CTkLabel(parent, text=text.upper(),
                        font=_font(10, "bold"),
                        text_color=C_SEC_HDR, anchor="w")

# legacy alias
sec_hdr = tg_sec

def hr(parent, color=None, padx=12, pady=6):
    ctk.CTkFrame(parent, fg_color=color or C_BORDER, height=1,
                 corner_radius=0).pack(fill="x", padx=padx, pady=pady)

# ═════════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PHANTOM — Encrypt")
        self.geometry("1200x760")
        self.minsize(960, 620)
        self.configure(fg_color=C_BG)

        self._selected_files: list = []
        self._bin_bytes  = None
        self._bundle_name = ""
        self._key_path   = ""
        self._key_bytes  = None
        self._key_pub_fp = ""
        self._spin_angle = 0
        self._spinning   = False
        self._active_ip  = ""
        self._active_name = ""

        self._build_ui()
        self.after(400, self._start_spinner)
        if not _CRYPTO_OK:
            self.after(800, lambda: self._log("⚠  pip install cryptography"))
        threading.Thread(target=self._poll_detect, daemon=True).start()

    # ── ROOT ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_titlebar()
        body = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)

        # Sidebar — 360px, C_PANEL background, 1px right border
        sb = ctk.CTkFrame(body, fg_color=C_PANEL, width=360, corner_radius=0,
                          border_color=C_BORDER, border_width=0)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)

        # 1px right border line on sidebar
        ctk.CTkFrame(body, fg_color=C_BORDER, width=1,
                     corner_radius=0).grid(row=0, column=0, sticky="nse")

        self._cf = ctk.CTkFrame(body, fg_color=C_BG, corner_radius=0)
        self._cf.grid(row=0, column=1, sticky="nsew")

        self._build_sidebar(sb)
        self._build_content(self._cf)

    # ── TITLE BAR ─────────────────────────────────────────────────────────────
    def _build_titlebar(self):
        bar = ctk.CTkFrame(self, fg_color=C_PANEL, height=46, corner_radius=0)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Center — app title
        center = ctk.CTkFrame(bar, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(center, text="PHANTOM — Encrypt",
                     font=_font(14, "bold"), text_color=C_TEXT).pack()

        # Right — connection status
        cr = ctk.CTkFrame(bar, fg_color="transparent")
        cr.pack(side="right", padx=18)
        self._conn_dot = ctk.CTkLabel(cr, text="●", font=_font(11),
                                      text_color=C_TEXT3)
        self._conn_dot.pack(side="left", padx=(0, 4))
        self._conn_lbl = ctk.CTkLabel(cr, text="NO SIGNAL",
                                      font=_font(11), text_color=C_TEXT3)
        self._conn_lbl.pack(side="left")
        self._conn_spinner = ctk.CTkLabel(cr, text="▼",
                                          font=_font(10), text_color=C_TEXT3)
        self._conn_spinner.pack(side="left", padx=(4, 0))

        # Bottom border
        ctk.CTkFrame(self, fg_color=C_BORDER, height=1,
                     corner_radius=0).pack(fill="x")

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        # Plain frame — NO scroll, top-aligned, no expand
        sc = ctk.CTkFrame(parent, fg_color=C_PANEL, corner_radius=0)
        sc.pack(fill="x", expand=False, anchor="n")

        # ── FAVORITES section ─────────────────────────────────────────────────
        tg_sec(sc, "Favorites").pack(fill="x", padx=16, pady=(8, 2))

        # Nav row with 3px left blue accent bar
        nav_outer = ctk.CTkFrame(sc, fg_color=C_WHITE, corner_radius=8, height=36)
        nav_outer.pack(fill="x", padx=8, pady=(0, 3))
        nav_outer.pack_propagate(False)
        ctk.CTkFrame(nav_outer, fg_color=C_BLUE, width=3, height=36,
                     corner_radius=0).pack(side="left")
        ctk.CTkLabel(nav_outer, text="🔒  Encrypt",
                     font=_font(13, "bold"), text_color=C_TEXT,
                     anchor="w").pack(side="left", padx=10)

        hr(sc, pady=4)

        # ── FILES section ─────────────────────────────────────────────────────
        tg_sec(sc, "Files").pack(fill="x", padx=16, pady=(4, 2))

        # Drop zone — compact single-row style
        _dz_browse = lambda e: self._browse()
        self._dz = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=8,
                                border_color=C_TEXT3, border_width=1, height=36)
        self._dz.pack(fill="x", padx=12, pady=(0, 2))
        self._dz.pack_propagate(False)
        dz_inner = ctk.CTkFrame(self._dz, fg_color="transparent")
        dz_inner.place(relx=0.5, rely=0.5, anchor="center")
        self._dz_icon  = ctk.CTkLabel(dz_inner, text="📄", font=_font(12))
        self._dz_icon.pack(side="left", padx=(0, 4))
        self._dz_title = ctk.CTkLabel(dz_inner, text="Drop files here",
                                      font=_font(10, "bold"), text_color=C_TEXT2)
        self._dz_title.pack(side="left")
        self._dz_sub   = ctk.CTkLabel(dz_inner, text="or click 'Add' to select files",
                                      font=_font(9), text_color=C_TEXT3)
        # self._dz_sub hidden
        for w in (self._dz, dz_inner, self._dz_icon, self._dz_title, self._dz_sub):
            w.bind("<Button-1>", _dz_browse)

        # File list — tk.Frame for exact height control (CTkFrame ignores small heights)
        import tkinter as _tk
        self._file_list_frame = _tk.Frame(sc, bg=C_INPUT, height=24,
                                          highlightbackground=C_BORDER,
                                          highlightthickness=1, bd=0)
        self._file_list_frame.pack(fill="x", padx=12, pady=(0, 2))
        self._file_list_frame.pack_propagate(False)
        self._file_widgets: list = []
        self._file_placeholder = _tk.Label(
            self._file_list_frame, text="No files selected",
            bg=C_INPUT, fg=C_TEXT3, font=("Segoe UI", 9), anchor="w")
        self._file_placeholder.pack(side="left", padx=8, fill="x", expand=True)

        self._file_count_lbl = ctk.CTkLabel(
            sc, text="No files selected",
            font=_font(10), text_color=C_TEXT3, anchor="w")
        # hidden — filename already shown in _file_list_frame above

        btn_row = ctk.CTkFrame(sc, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 2))
        btn_row.grid_columnconfigure((0, 1, 2), weight=1)
        tg_btn(btn_row, "Add", self._browse, style="outline",
               height=26, font=_font(10), corner_radius=20
               ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        tg_btn(btn_row, "Remove", self._remove_selected, style="ghost",
               height=26, font=_font(10), corner_radius=20
               ).grid(row=0, column=1, sticky="ew", padx=(0, 3))
        tg_btn(btn_row, "Clear", self._clear_files, style="ghost",
               height=26, font=_font(10), corner_radius=20
               ).grid(row=0, column=2, sticky="ew")

        hr(sc, pady=2)

        # ── KEY section ───────────────────────────────────────────────────────
        tg_sec(sc, "Key").pack(fill="x", padx=16, pady=(4, 2))

        # Key drop zone — compact single-row style
        _kz_browse = lambda e: self._browse_key()
        self._kz = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=8,
                                border_color=C_TEXT3, border_width=1, height=36)
        self._kz.pack(fill="x", padx=12, pady=(0, 2))
        self._kz.pack_propagate(False)
        kz_inner = ctk.CTkFrame(self._kz, fg_color="transparent")
        kz_inner.place(relx=0.5, rely=0.5, anchor="center")
        self._kz_icon  = ctk.CTkLabel(kz_inner, text="🔑", font=_font(12))
        self._kz_icon.pack(side="left", padx=(0, 4))
        self._kz_title = ctk.CTkLabel(kz_inner, text="Click to load key",
                                      font=_font(10, "bold"), text_color=C_TEXT3)
        self._kz_title.pack(side="left")
        for w in (self._kz, kz_inner, self._kz_icon, self._kz_title):
            w.bind("<Button-1>", _kz_browse)

        # Key file display — tk.Frame for exact height control
        self._key_list_frame = _tk.Frame(sc, bg=C_INPUT, height=24,
                                         highlightbackground=C_BORDER,
                                         highlightthickness=1, bd=0)
        self._key_list_frame.pack(fill="x", padx=12, pady=(0, 2))
        self._key_list_frame.pack_propagate(False)
        self._key_file_widgets: list = []
        self._key_placeholder = _tk.Label(
            self._key_list_frame, text="No key loaded",
            bg=C_INPUT, fg=C_TEXT3, font=("Segoe UI", 9), anchor="w")
        self._key_placeholder.pack(side="left", padx=8, fill="x", expand=True)

        # Key action buttons
        key_btn_row = ctk.CTkFrame(sc, fg_color="transparent")
        key_btn_row.pack(fill="x", padx=12, pady=(0, 3))
        key_btn_row.grid_columnconfigure((0, 1), weight=1)
        tg_btn(key_btn_row, "Load Key", self._browse_key, style="outline",
               height=26, font=_font(10), corner_radius=20
               ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        tg_btn(key_btn_row, "⟳ Generate", self._generate_key, style="ghost",
               height=26, font=_font(10), corner_radius=20
               ).grid(row=0, column=1, sticky="ew")

        self._key_status_lbl = ctk.CTkLabel(
            sc, text="",
            font=_font(10), text_color=C_TEXT3, anchor="w")
        # hidden — key name already shown in _key_list_frame above

        # keep StringVar for internal use (unused visually now)
        self._key_var = ctk.StringVar()

        hr(sc, pady=2)

        # ── ACTIONS section ───────────────────────────────────────────────────
        tg_sec(sc, "Actions").pack(fill="x", padx=16, pady=(2, 4))

        self._enc_btn = tg_btn(
            sc, "▶  Encrypt Files",
            command=lambda: threading.Thread(target=self._do_encrypt, daemon=True).start(),
            style="primary", height=40, font=_font(13, "bold"), corner_radius=20,
            state="normal" if _CRYPTO_OK else "disabled")
        self._enc_btn.pack(fill="x", padx=12, pady=(0, 5))

        # Sub-buttons row
        sub_row = ctk.CTkFrame(sc, fg_color="transparent")
        sub_row.pack(fill="x", padx=12, pady=(0, 5))
        sub_row.grid_columnconfigure((0, 1), weight=1)
        self._save_btn = tg_btn(sub_row, "💾 Save", self._save_bin,
                                style="outline", height=30, font=_font(10),
                                corner_radius=20, state="disabled")
        self._save_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._send_btn = tg_btn(sub_row, "📤 Sync",
                                command=lambda: threading.Thread(
                                    target=self._do_send, daemon=True).start(),
                                style="outline", height=30, font=_font(10),
                                corner_radius=20, state="disabled")
        self._send_btn.grid(row=0, column=1, sticky="ew")

        # Progress row
        pr = ctk.CTkFrame(sc, fg_color="transparent")
        pr.pack(fill="x", padx=12, pady=(3, 1))
        ctk.CTkLabel(pr, text="OVERALL", font=_font(10), text_color=C_TEXT3,
                     anchor="w").pack(side="left")
        self._enc_pct = ctk.CTkLabel(pr, text="0 %",
                                     font=_font(10, "bold"), text_color=C_TEXT)
        self._enc_pct.pack(side="right")

        self._enc_bar = ctk.CTkProgressBar(sc, mode="determinate", height=4,
                                           corner_radius=2,
                                           progress_color=C_BLUE, fg_color=C_BORDER)
        self._enc_bar.set(0)
        self._enc_bar.pack(fill="x", padx=12, pady=(0, 4))

        self._enc_status = ctk.CTkLabel(
            sc,
            text="Ready" if _CRYPTO_OK else "⚠  pip install cryptography",
            font=_font(10), anchor="w",
            text_color=C_BLUE if _CRYPTO_OK else C_ORANGE)
        self._enc_status.pack(fill="x", padx=14, pady=(0, 3))

        # .bin filename shown in blue after encrypt
        self._bundle_lbl = ctk.CTkLabel(sc, text="",
                                        font=_font(10, "bold"), text_color=C_BLUE,
                                        anchor="w", wraplength=215, justify="left")
        self._bundle_lbl.pack(fill="x", padx=14, pady=(0, 4))

        # Sync status shown after send
        self._sync_status_lbl = ctk.CTkLabel(sc, text="",
                                             font=_font(10, "bold"), text_color=C_BLUE,
                                             anchor="w", wraplength=215)
        self._sync_status_lbl.pack(fill="x", padx=14, pady=(0, 2))

        # _ip_lbl kept as hidden widget so _on_scan_result doesn't crash
        self._ip_lbl = ctk.CTkLabel(sc, text="",
                                    font=_font(11), text_color=C_TEXT3, anchor="w")
        # not packed — hidden

    # ── CONTENT ───────────────────────────────────────────────────────────────
    def _build_content(self, parent):
        # Split content into left (engine) and right (phantom files) panes
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=3)
        parent.grid_columnconfigure(1, weight=0)
        parent.grid_columnconfigure(2, weight=2)

        # Sub-toolbar (42px, C_SURFACE bg) — spans full width
        hdr = ctk.CTkFrame(parent, fg_color=C_SURFACE, height=42, corner_radius=0)
        hdr.grid(row=0, column=0, columnspan=3, sticky="ew")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="ENCRYPTION ENGINE",
                     font=_font(13, "bold"), text_color=C_TEXT).pack(side="left", padx=(14, 0))
        ctk.CTkLabel(hdr, text="  ·  3-LAYER VISUALIZER",
                     font=_font(12), text_color=C_SEC_HDR).pack(side="left")

        # divider spanning full width
        div = ctk.CTkFrame(parent, fg_color=C_BORDER, height=1, corner_radius=0)
        div.grid(row=0, column=0, columnspan=3, sticky="sew")

        # ── LEFT: engine panel ────────────────────────────────────────────────
        left = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        left.grid(row=1, column=0, sticky="nsew")
        inner = ctk.CTkFrame(left, fg_color=C_BG, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=20, pady=16)

        # ── vertical divider ──────────────────────────────────────────────────
        ctk.CTkFrame(parent, fg_color=C_BORDER, width=1,
                     corner_radius=0).grid(row=1, column=1, sticky="ns")

        # ── RIGHT: phantom files panel ────────────────────────────────────────
        right = ctk.CTkFrame(parent, fg_color=C_PANEL, corner_radius=0)
        right.grid(row=1, column=2, sticky="nsew")
        self._build_phantom_panel(right)

        # ── 3 Layer Cards ─────────────────────────────────────────────────────
        _LAYERS = [
            ("L1", "ENC",  "Encryption Layer 1", "First pass",   C_BLUE, "#EBF1FF"),
            ("L2", "INT",  "Encryption Layer 2", "Second pass",  C_BLUE, "#EBF1FF"),
            ("L3", "STR",  "Encryption Layer 3", "Third pass",   C_BLUE, "#EBF1FF"),
        ]
        lf = ctk.CTkFrame(inner, fg_color="transparent")
        lf.pack(fill="x", pady=(0, 14))
        lf.grid_columnconfigure((0, 1, 2), weight=1)

        self._layer_cards = []
        for col, (lnum, lshort, algo, desc, color, tint) in enumerate(_LAYERS):
            card = ctk.CTkFrame(lf, fg_color=C_CARD, corner_radius=12,
                                border_color=C_BORDER, border_width=1)
            card.grid(row=0, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 10, 0))

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=14, pady=(14, 4))
            pill(top, lnum, color, tint).pack(side="left", padx=(0, 4))
            pill(top, lshort, color, tint).pack(side="left")
            dot = ctk.CTkLabel(top, text="●", font=_font(11), text_color=C_TEXT3)
            dot.pack(side="right")

            title_lbl = ctk.CTkLabel(card, text=algo, font=_font(15, "bold"),
                                     text_color=C_TEXT)
            title_lbl.pack(anchor="w", padx=14, pady=(4, 2))
            desc_lbl = ctk.CTkLabel(card, text=desc, font=_font(11),
                                    text_color=C_TEXT2)
            desc_lbl.pack(anchor="w", padx=14, pady=(0, 8))

            hash_f = ctk.CTkFrame(card, fg_color=C_SURFACE, corner_radius=6)
            hash_f.pack(fill="x", padx=14, pady=(0, 8))
            hash_lbl = ctk.CTkLabel(hash_f, text="HASH ——",
                                    font=_mono(10), text_color=C_TEXT3,
                                    anchor="w", justify="left")
            hash_lbl.pack(fill="x", padx=8, pady=5)

            bar = ctk.CTkProgressBar(card, mode="determinate", height=4,
                                     progress_color=color, fg_color=C_BORDER,
                                     corner_radius=2)
            bar.set(0)
            bar.pack(fill="x", padx=14, pady=(0, 4))

            pct = ctk.CTkLabel(card, text="0 %", font=_font(20, "bold"),
                               text_color=color, anchor="e")
            pct.pack(fill="x", padx=14, pady=(0, 12))

            self._layer_cards.append((card, hash_lbl, bar, pct, dot, color, title_lbl, desc_lbl))

        # ── Overall progress ──────────────────────────────────────────────────
        op = ctk.CTkFrame(inner, fg_color="transparent")
        op.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(op, text="OVERALL PROGRESS",
                     font=_font(11), text_color=C_TEXT2).pack(side="left")
        self._enc_pct2 = ctk.CTkLabel(op, text="0 %",
                                      font=_font(13, "bold"), text_color=C_TEXT)
        self._enc_pct2.pack(side="right")

        self._enc_bar2 = ctk.CTkProgressBar(inner, mode="determinate",
                                            height=4, corner_radius=2,
                                            progress_color=C_BLUE, fg_color=C_BORDER)
        self._enc_bar2.set(0)
        self._enc_bar2.pack(fill="x", pady=(0, 14))

        # ── Terminal log ──────────────────────────────────────────────────────
        log_hdr = ctk.CTkFrame(inner, fg_color="transparent")
        log_hdr.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(log_hdr, text="TERMINAL OUTPUT",
                     font=_font(11, "bold"), text_color=C_TEXT2).pack(side="left")
        tg_btn(log_hdr, "CLR", self._clear_log,
               style="ghost", height=24, width=48,
               font=_font(10), corner_radius=20).pack(side="right")

        self.log = ctk.CTkTextbox(
            inner,
            fg_color=C_SURFACE, text_color=C_TEXT, font=_mono(11),
            corner_radius=8, border_color=C_BORDER, border_width=1,
            wrap="word",
            scrollbar_button_color=C_BORDER,
            scrollbar_button_hover_color=C_TEXT3,
            activate_scrollbars=True)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    # ── FILE INFO PANEL (right) — macOS Finder style ──────────────────────────
    def _build_phantom_panel(self, parent):
        import tkinter as _tk
        tg_sec(parent, "File Info").pack(fill="x", padx=14, pady=(14, 6))
        hr(parent, pady=2)

        # Scrollable container for file info rows
        self._phantom_list = ctk.CTkScrollableFrame(
            parent, fg_color="transparent",
            scrollbar_button_color=C_BORDER,
            scrollbar_button_hover_color=C_TEXT3,
            corner_radius=0)
        self._phantom_list.pack(fill="both", expand=True, padx=0, pady=0)
        self._phantom_rows: list = []

        # Placeholder shown when no files yet
        self._fi_placeholder = ctk.CTkLabel(
            self._phantom_list,
            text="No files encrypted yet",
            font=_font(11), text_color=C_TEXT3)
        self._fi_placeholder.pack(pady=24)

    def _phantom_add_file(self, filename: str, size_kb: float):
        """Add a macOS Finder-style file info card to the right panel."""
        import tkinter as _tk
        # Hide placeholder on first file
        try: self._fi_placeholder.pack_forget()
        except: pass

        card = ctk.CTkFrame(self._phantom_list, fg_color=C_CARD, corner_radius=10,
                            border_color=C_BORDER, border_width=1)
        card.pack(fill="x", padx=10, pady=(6, 2))

        # ── File icon + name header ────────────────────────────────────────────
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(hdr, text="📦", font=_font(24)).pack(side="left", padx=(0, 8))
        name_lbl = ctk.CTkLabel(hdr, text=filename,
                                font=_font(11, "bold"), text_color=C_TEXT,
                                anchor="w", wraplength=130, justify="left")
        name_lbl.pack(side="left", fill="x", expand=True)

        hr(card, padx=12, pady=4)

        # ── Info rows ─────────────────────────────────────────────────────────
        def _row(key, val, val_color=C_TEXT):
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=2)
            ctk.CTkLabel(r, text=key, font=_font(10), text_color=C_TEXT3,
                         width=72, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=val, font=_font(10, "bold"), text_color=val_color,
                         anchor="w").pack(side="left", fill="x", expand=True)

        _row("Kind:",     "PHANTOM Bundle")
        _row("Size:",     f"{size_kb:.1f} KB", C_BLUE)
        _row("Status:",   "✓ encrypted", C_BLUE)
        _row("Layers:",   "3 / 3", C_TEAL)
        _row("Created:",  time.strftime("%d/%m/%Y  %H:%M"))

        # bottom padding
        ctk.CTkFrame(card, fg_color="transparent", height=8).pack()
        self._phantom_rows.append(card)

    # ── FILE LIST ─────────────────────────────────────────────────────────────
    def _refresh_file_list(self, uploaded: set = None):
        """Render file list. Files in `uploaded` set shown in blue, others black."""
        for w in self._file_widgets: w.destroy()
        self._file_widgets.clear()
        uploaded = uploaded or set()
        if not self._selected_files:
            self._file_placeholder.pack(side="left", padx=8, fill="x", expand=True)
            return
        self._file_placeholder.pack_forget()
        # Show first file name + count badge
        import tkinter as _tk
        first = os.path.basename(self._selected_files[0])
        n = len(self._selected_files)
        display = first if n == 1 else f"{first}  +{n-1}"
        color = C_BLUE if first in (uploaded or set()) else C_TEXT
        lbl = _tk.Label(self._file_list_frame,
                        text=f"  \U0001f4c4 {display}",
                        bg=C_INPUT, fg=color,
                        font=("Segoe UI", 9), anchor="w")
        lbl.pack(side="left", padx=4, fill="x", expand=True)
        self._file_widgets.append(lbl)

    # ── LAYER ANIMATION ───────────────────────────────────────────────────────
    def _animate_layer(self, idx, hash_hex, duration_ms, on_done):
        card, hash_lbl, bar, pct, dot, color, title_lbl, desc_lbl = self._layer_cards[idx]
        steps = 40; interval = max(20, duration_ms // steps)
        start = idx / 3.0
        dot.configure(text="◌", text_color=color)
        hash_lbl.configure(text=f"HASH  {hash_hex[:12]}…", text_color="#1C1C1E")
        title_lbl.configure(text_color="#1C1C1E")
        desc_lbl.configure(text_color="#1C1C1E")
        card.configure(border_color=C_BLUE, border_width=2)

        blink_active = [True]
        def _blink(on=True):
            if not blink_active[0]:
                return
            card.configure(border_color=C_BLUE if on else C_BORDER,
                            border_width=2 if on else 1)
            self.after(400, lambda: _blink(not on))
        self.after(400, lambda: _blink(False))

        def _tick(step=0):
            if step > steps:
                blink_active[0] = False
                bar.set(1.0); pct.configure(text="100 %")
                dot.configure(text="●", text_color=color)
                hash_lbl.configure(text=f"HASH  {hash_hex[:24]}…", text_color=color)
                title_lbl.configure(text_color=C_TEXT)
                desc_lbl.configure(text_color=C_TEXT2)
                card.configure(border_color=C_BLUE, border_width=2)
                self._set_ov((idx + 1) / 3.0); on_done(); return
            frac = step / steps
            bar.set(frac); pct.configure(text=f"{int(frac*100)} %")
            self._set_ov(start + frac / 3.0)
            self.after(interval, lambda: _tick(step + 1))
        _tick()

    def _set_ov(self, v):
        txt = f"{int(v*100)} %"
        self._enc_bar.set(v);  self._enc_pct.configure(text=txt)
        self._enc_bar2.set(v); self._enc_pct2.configure(text=txt)

    def _reset_layers(self):
        for card, hash_lbl, bar, pct, dot, color, title_lbl, desc_lbl in self._layer_cards:
            bar.set(0); pct.configure(text="0 %", text_color=color)
            dot.configure(text="●", text_color=C_TEXT3)
            hash_lbl.configure(text="HASH ——", text_color=C_TEXT3)
            title_lbl.configure(text_color=C_TEXT)
            desc_lbl.configure(text_color=C_TEXT2)
            card.configure(border_color=C_BORDER, border_width=1)
        self._set_ov(0)

    # ── LOG ───────────────────────────────────────────────────────────────────
    def _log(self, msg):
        def _do():
            self.log.configure(state="normal")
            self.log.insert("end", f"  ›  {msg}\n")
            self.log.configure(state="disabled")
            self.log.see("end")
        try: self.after(0, _do)
        except: pass

    def _log_msg(self, msg): self._log(msg)

    def _log_banner(self, filename: str, layers: int = 3, total: int = 3):
        """Append a green ✔ Task completed banner to the terminal log."""
        lines = [
            "┌─────────────────────────────────────┐",
            f"│  ✔  Task completed                  │",
            f"│  \"{filename}\"",
            f"│  Finish encrypted                   │",
            f"│  Status : {layers}/{total} layers               │",
            "└─────────────────────────────────────┘",
        ]
        def _do():
            self.log.configure(state="normal")
            # configure green tag once
            try:
                self.log._textbox.tag_configure("banner_ok",
                    foreground="#34C759",
                    font=("Consolas", 11, "bold"))
            except Exception:
                pass
            self.log._textbox.insert("end", "\n")
            for line in lines:
                try:
                    self.log._textbox.insert("end", f"  {line}\n", "banner_ok")
                except Exception:
                    self.log._textbox.insert("end", f"  {line}\n")
            self.log._textbox.insert("end", "\n")
            self.log.configure(state="disabled")
            self.log.see("end")
        try: self.after(0, _do)
        except: pass

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._reset_layers()

    # ── TOAST ─────────────────────────────────────────────────────────────────
    def _show_toast(self, msg, error=False):
        c = C_RED if error else C_BLUE
        t = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=12,
                         border_color=c, border_width=1)
        t.place(relx=0.5, y=54, anchor="n")
        ctk.CTkLabel(t, text=msg, font=_font(12, "bold"),
                     text_color=c, padx=22, pady=9).pack()
        self.after(3000, t.destroy)

    # ── FILE OPS ──────────────────────────────────────────────────────────────
    def _browse(self):
        paths = filedialog.askopenfilenames(
            title="Select files to encrypt",
            filetypes=[("All supported",
                        "*.wav *.mp3 *.ogg *.flac *.aac "
                        "*.doc *.docx *.xls *.xlsx *.pdf "
                        "*.jpg *.jpeg *.png *.gif *.bmp *.webp "
                        "*.txt *.csv *.json *.bin"),
                       ("All files", "*.*")])
        if paths:
            added = sum(1 for p in paths
                        if p not in self._selected_files
                        and not self._selected_files.append(p))
            self._refresh_file_list(); self._update_count()
            if added:
                self._log(f"Added {added} file(s)")
                self._reset_bundle()
                self._flash_dropzone(added)

    def _reset_dropzone(self):
        """Reset drop zone to idle/initial state."""
        self._dz.configure(border_color=C_TEXT3, border_width=1)
        self._dz_icon.configure(text="📄")
        self._dz_title.configure(text="Drop files here", text_color=C_TEXT2)

    def _remove_selected(self):
        if self._selected_files:
            r = self._selected_files.pop()
            self._refresh_file_list(); self._update_count()
            self._log(f"Removed: {os.path.basename(r)}"); self._reset_bundle()
            if not self._selected_files:
                self._reset_dropzone()

    def _clear_files(self):
        self._selected_files.clear()
        self._refresh_file_list(); self._update_count(); self._reset_bundle()
        self._reset_dropzone()

    def _update_count(self):
        n = len(self._selected_files)
        self._file_count_lbl.configure(
            text=f"{n} file(s) selected" if n else "No files selected",
            text_color=C_TEXT if n else C_TEXT3)

    def _reset_bundle(self):
        self._bin_bytes = None
        self._bundle_lbl.configure(text="")
        self._sync_status_lbl.configure(text="")
        self._save_btn.configure(state="disabled")
        self._send_btn.configure(state="disabled")

    # ── KEY LIST ──────────────────────────────────────────────────────────────
    def _refresh_key_list(self, name: str = ""):
        """Show the loaded key filename in the key list frame."""
        for w in self._key_file_widgets:
            w.destroy()
        self._key_file_widgets.clear()
        if not name:
            self._key_placeholder.pack(side="left", padx=8, fill="x", expand=True)
            return
        self._key_placeholder.pack_forget()
        import tkinter as _tk
        icon = _tk.Label(self._key_list_frame, text="\U0001f511",
                         bg=C_INPUT, fg=C_BLUE, font=("Segoe UI", 9), width=2)
        icon.pack(side="left", padx=(6, 2))
        lbl = _tk.Label(self._key_list_frame, text=name,
                        bg=C_INPUT, fg=C_TEXT, font=("Segoe UI", 9), anchor="w")
        lbl.pack(side="left", fill="x", expand=True)
        self._key_file_widgets.extend([icon, lbl])

    # ── KEY ZONE FLASH ────────────────────────────────────────────────────────
    def _flash_key_zone(self, name: str):
        """Set key zone border blue permanently when key is loaded."""
        self._kz.configure(border_color=C_BLUE)
        self._kz_icon.configure(text="✓")
        self._kz_title.configure(text=name, text_color=C_BLUE)

    # ── DROP ZONE FLASH ───────────────────────────────────────────────────────
    def _flash_dropzone(self, count: int):
        """Set drop zone border blue permanently when files are loaded."""
        n = len(self._selected_files)
        self._dz.configure(border_color=C_BLUE)
        self._dz_icon.configure(text="✓")
        self._dz_title.configure(
            text=f"{n} file(s) ready" if n else "Drop files here",
            text_color=C_BLUE)

    # ── KEY ───────────────────────────────────────────────────────────────────
    def _browse_key(self):
        path = filedialog.askopenfilename(title="Select key file",
            filetypes=[("Key file", "*.key"), ("All files", "*.*")])
        if not path: return
        try:
            master = _phtm_load_key(path)
            pub_fp = hashlib.sha256(master).hexdigest()
            self._key_path = path; self._key_bytes = master; self._key_pub_fp = pub_fp
            last4 = pub_fp[-4:].upper()
            name = os.path.basename(path)
            self._key_var.set(name)
            self._refresh_key_list(name)
            self._key_status_lbl.configure(text=f"✓  […{last4}]", text_color=C_BLUE)
            self._log(f"Key loaded: {name}  […{last4}]")
            self._reset_bundle()
            self._flash_key_zone(name)
        except Exception as e:
            self._key_bytes = None
            self._key_status_lbl.configure(text=f"✗  {e}", text_color=C_RED)

    def _generate_key(self):
        path = filedialog.asksaveasfilename(title="Save new key file",
            defaultextension=".key", initialfile="phantom.key",
            filetypes=[("Key file", "*.key"), ("All files", "*.*")])
        if not path: return
        try:
            out, pub_fp = generate_key_file(path)
            master = _phtm_load_key(out)
            self._key_path = out; self._key_bytes = master; self._key_pub_fp = pub_fp
            last4 = pub_fp[-4:].upper()
            name = os.path.basename(out)
            self._key_var.set(name)
            self._refresh_key_list(name)
            self._key_status_lbl.configure(text=f"✓  […{last4}]", text_color=C_BLUE)
            self._log(f"Generated: {name}  […{last4}]")
            self._show_toast(f"✓  Key saved: {name}")
            self._reset_bundle()
            self._flash_key_zone(name)
        except Exception as e:
            self._log(f"Generate error: {e}")

    # ── SPINNER ───────────────────────────────────────────────────────────────
    _SPIN = ["◌", "◍", "◎", "◍"]
    def _start_spinner(self): self._spinning = True; self._tick_spinner()
    def _tick_spinner(self):
        if not self._spinning: return
        self._spin_angle = (self._spin_angle + 1) % 4
        try: self._conn_spinner.configure(text=self._SPIN[self._spin_angle])
        except: pass
        self.after(350, self._tick_spinner)

    def _poll_detect(self):
        prev: set = set()
        while True:
            results = scan_phantoms()
            cur = {r[0] for r in results}
            if cur != prev: prev = cur; self.after(0, self._on_scan_result, results)
            if not results and self._active_ip:
                self._active_ip = ""; self.after(0, self._on_scan_result, [])
            time.sleep(4)

    def _on_scan_result(self, results):
        if not results:
            self._conn_lbl.configure(text="NO SIGNAL", text_color=C_TEXT3)
            self._conn_dot.configure(text_color=C_TEXT3)
            self._ip_lbl.configure(text="Connect to Phantom WiFi")
            self._active_ip = ""; self._active_name = ""; return
        ip, nm, l4, _ = results[0]
        self._active_ip = ip; self._active_name = nm
        self._conn_lbl.configure(text=f"{nm}  ONLINE", text_color=C_BLUE)
        self._conn_dot.configure(text_color=C_BLUE)
        self._ip_lbl.configure(text=f"{ip}  ·  KEY …{l4}")
        self._conn_spinner.configure(text="▼", text_color=C_TEXT3)
        self._log(f"Detected: {nm}")

    # ── SAVE ──────────────────────────────────────────────────────────────────
    def _save_bin(self):
        if not self._bin_bytes: return
        path = filedialog.asksaveasfilename(
            defaultextension=".bin", initialfile=self._bundle_name + ".bin",
            filetypes=[("Binary", "*.bin"), ("All files", "*.*")])
        if path:
            open(path, "wb").write(self._bin_bytes)
            # self._log(f"✓  Saved: {os.path.basename(path)}")
            # self._show_toast(f"✓  Saved {os.path.basename(path)}")

    # ── SEND / SYNC ───────────────────────────────────────────────────────────
    def _do_send(self):
        if not self._bin_bytes:
            self._log("No bundle — run Encrypt first"); return
        if not self._active_ip:
            self._log("Not connected to Phantom"); return
        fname = self._bundle_name + ".bin"; data = self._bin_bytes
        size_kb = len(data) / 1024
        self.after(0, lambda: (
            self._send_btn.configure(state="disabled"),
            self._sync_status_lbl.configure(text=""),
            self._enc_status.configure(
                text=f"Syncing {size_kb:.1f} KB…", text_color=C_BLUE)))
        # Animate sync progress bar (indeterminate feel via steps)
        self._sync_progress(0)
        t0 = time.time()
        resp, sent = tcp_upload(self._active_ip, TCP_PORT, data, filename=fname)
        elapsed = time.time() - t0
        sl = resp.split("\r\n")[0] if resp else ""
        body = resp.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in resp else ""
        spiffs_ok = True
        try:
            rj = json.loads(body); spiffs_ok = rj.get("spiffs_saved", rj.get("ok", True))
        except: pass
        ok = ("200" in sl or "201" in sl or '"ok"' in resp) and "ERROR" not in resp and spiffs_ok
        self.after(0, lambda: self._send_btn.configure(state="normal"))
        if ok:
            msg = f"✓  Sync complete  ·  {sent//1024:.0f} KB  ({elapsed:.1f}s)"
            _key_name = os.path.basename(self._key_path) if self._key_path else ""
            self.after(0, lambda m=msg: (
                self._enc_status.configure(text=m, text_color=C_BLUE),
                self._sync_status_lbl.configure(text="✓  Complete", text_color=C_BLUE)))
            # Show key filename in entry after successful sync
            if _key_name:
                self.after(0, lambda k=_key_name: self._key_var.set(k))
            # Mark all selected files as uploaded (blue)
            _uploaded = {os.path.basename(p) for p in self._selected_files}
            self.after(0, lambda u=_uploaded: self._refresh_file_list(u))
            # Add file card to phantom panel
            self.after(0, lambda: self._phantom_add_file(fname, size_kb))
            self._show_toast(msg)
        elif not spiffs_ok:
            msg = "⚠  Storage full"
            self.after(0, lambda m=msg: self._enc_status.configure(text=m, text_color=C_ORANGE))
            self._show_toast(msg, error=True)
        else:
            msg = f"✗  Sync failed ({sl})"
            self.after(0, lambda m=msg: self._enc_status.configure(text=m, text_color=C_RED))
            self._show_toast(msg, error=True)

    def _sync_progress(self, step):
        """Animate the overall progress bar 0→100% during sync."""
        if step > 40: return
        self._set_ov(step / 40.0)
        self.after(80, lambda: self._sync_progress(step + 1))

    # ── ENCRYPT ───────────────────────────────────────────────────────────────
    def _do_encrypt(self):
        if not self._selected_files:
            self.after(0, lambda: self._enc_status.configure(
                text="⚠  No files selected", text_color=C_ORANGE)); return
        if not _CRYPTO_OK:
            self.after(0, lambda: self._enc_status.configure(
                text="⚠  pip install cryptography", text_color=C_ORANGE)); return
        if not self._key_bytes:
            self.after(0, lambda: self._enc_status.configure(
                text="⚠  No key file loaded", text_color=C_ORANGE)); return

        files = list(self._selected_files); master = self._key_bytes
        self.after(0, self._reset_layers)
        self.after(0, self._clear_log)
        self.after(0, lambda: self._enc_btn.configure(state="disabled"))
        self.after(0, lambda: self._send_btn.configure(state="disabled"))
        self.after(0, lambda: self._save_btn.configure(state="disabled"))
        self.after(0, lambda: self._enc_status.configure(text="Running…", text_color=C_BLUE))
        self.after(0, lambda: self._bundle_lbl.configure(text=""))

        def _run():
            self._log_msg("══════════════════════════════════════")
            self._log_msg(f"  FILES  : {len(files)} file(s)")
            self._log_msg(f"  KEY    : {os.path.basename(self._key_path)}")
            self._log_msg("══════════════════════════════════════")
            time.sleep(0.3)
            try:
                k_aes, k_hmac, k_chacha = _phtm_derive(master)
                h_aes    = hashlib.sha256(k_aes   ).hexdigest()
                h_hmac   = hashlib.sha256(k_hmac  ).hexdigest()
                h_chacha = hashlib.sha256(k_chacha).hexdigest()
            except Exception as e:
                self._log_msg(f"✗  KEY DERIVE ERROR: {e}")
                self.after(0, lambda: self._enc_status.configure(
                    text=f"Error: {e}", text_color=C_RED))
                self.after(0, lambda: self._enc_btn.configure(state="normal")); return

            for idx, (hx, label) in enumerate([
                (h_aes,    "[L1]  Encryption Layer 1"),
                (h_hmac,   "[L2]  Encryption Layer 2"),
                (h_chacha, "[L3]  Encryption Layer 3"),
            ]):
                self._log_msg(f"\n{label}")
                time.sleep(0.2)
                ev = threading.Event()
                self.after(0, lambda i=idx, h=hx: self._animate_layer(i, h, 5000, ev.set))
                ev.wait(); time.sleep(0.25)

            # ── Step 1 — copy files into a temp folder ───────────────────────
            self._log_msg("\n[1/3]  Packaging files into folder…")
            try:
                with tempfile.TemporaryDirectory(prefix="phantom_enc_") as tmpdir:
                    work_folder = Path(tmpdir) / "phantom_bundle"
                    work_folder.mkdir()
                    for fpath in files:
                        shutil.copy2(fpath, work_folder / os.path.basename(fpath))
                        self._log_msg(f"  ›  {os.path.basename(fpath)}")

                    # ── Step 2 — compress folder to .zfld ────────────────────
                    self._log_msg("\n[2/3]  Compressing with 7-Zip LZMA2…")
                    zfld_path = Path(tmpdir) / "bundle.zfld"
                    compress_folder(str(work_folder), str(zfld_path), optimize=True, algorithm="7z")
                    zfld_bytes = zfld_path.read_bytes()
                    orig_kb = sum(Path(f).stat().st_size for f in files) / 1024
                    comp_kb = len(zfld_bytes) / 1024
                    ratio   = round((1 - comp_kb / orig_kb) * 100, 1) if orig_kb > 0 else 0
                    self._log_msg(f"  Compressed: {orig_kb:.1f} KB → {comp_kb:.1f} KB  (↓{ratio}%)")

                    # ── Step 3 — encrypt the entire .zfld as one blob ────────
                    self._log_msg("\n[3/3]  Encrypting with 3-layer PHANTOM…")
                    enc_bytes = _phtm_encrypt_3layer(zfld_bytes, master)
                    bin_bytes = _phtm_pack_bin(enc_bytes)
                md5_str     = hashlib.md5(bin_bytes).hexdigest()
                ts          = time.strftime("%Y%m%d_%H%M%S")
                bundle_name = f"phantom_{ts}"
                self._bin_bytes = bin_bytes; self._bundle_name = bundle_name
                size_kb = len(bin_bytes) / 1024
                orig_kb_total = sum(Path(f).stat().st_size for f in files) / 1024

                # ── AUTO-SAVE to output/ folder next to encode.py ─────────────
                _script_dir = os.path.dirname(os.path.abspath(__file__))
                out_dir     = os.path.join(_script_dir, "output")
                os.makedirs(out_dir, exist_ok=True)
                auto_path   = os.path.join(out_dir, bundle_name + ".bin")
                _save_ok    = False
                try:
                    with open(auto_path, "wb") as _f:
                        _f.write(bin_bytes)
                    _save_ok = True
                    # self._log_msg(f"  💾  Saved → {auto_path}")
                except Exception as _se:
                    self._log_msg(f"  ⚠  Auto-save error: {_se}")
                _comp_kb = len(enc_bytes) / 1024   # zfld+crypto (before pack header)
                save_info = f"📦  {bundle_name}.bin  ·  {size_kb:.1f} KB  (orig {orig_kb_total:.1f} KB)"

                self._log_msg("══════════════════════════════════════")
                self._log_msg(f"  DONE   {bundle_name}.bin")
                self._log_msg(f"  ORIG : {orig_kb_total:.1f} KB")
                self._log_msg(f"  COMP : {_comp_kb:.1f} KB  (compressed+encrypted)")
                self._log_msg(f"  BIN  : {size_kb:.1f} KB  (with PHANTOM header)")
                self._log_msg(f"  MD5  : {md5_str}")
                self._log_msg("══════════════════════════════════════")
                # ── Green completion banner ───────────────────────────────────
                _first_fname = os.path.basename(files[0]) if files else "file"
                self._log_banner(_first_fname, layers=3, total=3)
                self.after(0, lambda i=save_info: self._bundle_lbl.configure(text=i))
                self.after(0, lambda: self._enc_status.configure(
                    text=f"Done — {len(files)} file(s) encrypted", text_color=C_BLUE))
                self.after(0, lambda: self._send_btn.configure(state="normal"))
                self.after(0, lambda: self._save_btn.configure(state="normal"))
                self._show_toast(f"✓  Saved → output/{bundle_name}.bin")
                # ── AUTO-SEND after 20s countdown ────────────────────────────
                if self._active_ip:
                    for _remaining in range(20, 0, -1):
                        self.after(0, lambda r=_remaining: self._enc_status.configure(
                            text=f"Auto-upload in {r}s…", text_color=C_BLUE))
                        time.sleep(1)
                    # After countdown, run the send logic inline (same as _do_send but non-blocking)
                    _fname = self._bundle_name + ".bin"
                    _data  = self._bin_bytes
                    _ip    = self._active_ip
                    if _data and _ip:
                        self.after(0, lambda: self._send_btn.configure(state="disabled"))
                        self.after(0, lambda: self._enc_status.configure(
                            text=f"Auto-sending {len(_data)/1024:.1f} KB…", text_color=C_BLUE))
                        _t0 = time.time()
                        _resp, _sent = tcp_upload(_ip, TCP_PORT, _data, filename=_fname)
                        _elapsed = time.time() - _t0
                        _sl   = _resp.split("\r\n")[0] if _resp else ""
                        _body = _resp.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in _resp else ""
                        _spiffs_ok = True
                        try:
                            import json as _json
                            _rj = _json.loads(_body)
                            _spiffs_ok = _rj.get("spiffs_saved", _rj.get("ok", True))
                        except: pass
                        _ok = ("200" in _sl or "201" in _sl or '"ok"' in _resp) and "ERROR" not in _resp and _spiffs_ok
                        self.after(0, lambda: self._send_btn.configure(state="normal"))
                        if _ok:
                            _msg = f"✓  Auto-sent  {_sent//1024:.0f} KB  ({_elapsed:.1f}s)"
                            self.after(0, lambda m=_msg: self._enc_status.configure(text=m, text_color=C_BLUE))
                            self._show_toast(_msg)
                        elif not _spiffs_ok:
                            _msg = "⚠  Storage full"
                            self.after(0, lambda m=_msg: self._enc_status.configure(text=m, text_color=C_ORANGE))
                            self._show_toast(_msg, error=True)
                        else:
                            _msg = f"✗  Auto-send failed ({_sl})"
                            self.after(0, lambda m=_msg: self._enc_status.configure(text=m, text_color=C_RED))
                            self._show_toast(_msg, error=True)
                else:
                    self._log_msg("ℹ  No device connected — skipping auto-upload")
            except Exception as e:
                self._log_msg(f"✗  ERROR: {e}")
                self.after(0, lambda: self._enc_status.configure(
                    text=f"Error: {e}", text_color=C_RED))
            self.after(0, lambda: self._enc_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
