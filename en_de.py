"""
en_de.py - PHANTOM Encrypt + Decrypt tabbed
Run: python en_de.py
"""

"""
encode.py — PHANTOM Secure File Encrypt & Transfer
Style: macOS Finder Light — clean white + blue accent (HIG)
Run:   .venv\Scripts\python encode.py
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import sys, socket, threading, os, time, json, tempfile, shutil, subprocess
import zipfile, hashlib, io, struct, urllib.request, urllib.error
from pathlib import Path

def _resource_path(relative: str) -> str:
    """Return correct path whether running as .py or PyInstaller .exe."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    return str(base / relative)

sys.path.insert(0, _resource_path("project_nen"))
from zipfolder.compressor import compress_folder
from zipfolder.decompressor import decompress_folder

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

def _phtm_decrypt_3layer(enc: bytes, master: bytes) -> bytes:
    k_aes, k_hmac, k_chacha = _phtm_derive(master)
    payload = ChaCha20Poly1305(k_chacha).decrypt(enc[:12], enc[12:], None)
    hmac_tag, inner = payload[-32:], payload[:-32]
    h = _hmac.HMAC(k_hmac, _hashes.SHA256(), backend=_backend())
    h.update(inner)
    h.verify(hmac_tag)
    return AESGCM(k_aes).decrypt(inner[:12], inner[12:], None)

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
C_BORDER    = "#C7C7CC"    # visible border
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
             border_color=C_BORDER, border_width=2)
    d.update(kw)
    return ctk.CTkFrame(parent, **d)

# keep legacy alias so any remaining call sites don't break
mac_card = tg_card

def tg_btn(parent, text, command, style="primary", **kw):
    # ALL styles = gray pill + white text + subtle border for "bubble" depth
    _BLACK    = "#6E6E73"   # gray (was black #1C1C1E)
    _BLACK_HV = "#3A3A3C"   # darker gray on hover (was lighter #8E8E93)
    _BLACK_BR = "#AEAEB2"   # border gray
    base = dict(corner_radius=20, height=38, command=command, font=_font(13, "bold"),
                fg_color=_BLACK, hover_color=_BLACK_HV,
                text_color="#FFFFFF",
                border_color=_BLACK_BR, border_width=1)
    # style kwarg kept for API compatibility — ignored, all black now
    base.update(kw)
    btn = ctk.CTkButton(parent, text=text, **base)
    _attach_hover(btn)
    _attach_shadow(btn)
    return btn

def _attach_shadow(btn: ctk.CTkButton):
    """Draw a subtle drop-shadow beneath the button using a lower-z tk Frame."""
    import tkinter as _tk
    _SHADOW_COLOR = "#C8C8CC"   # light gray shadow
    _OFF = 2                     # shadow offset px

    shadow_frames: list = []

    def _make_shadow():
        nonlocal shadow_frames
        for f in shadow_frames:
            try: f.destroy()
            except: pass
        shadow_frames.clear()
        try:
            p = btn.nametowidget(btn.winfo_parent())
        except Exception:
            return
        try:
            bx = btn.winfo_x(); by = btn.winfo_y()
            bw = btn.winfo_width(); bh = btn.winfo_height()
        except Exception:
            return
        if bw < 4 or bh < 4:
            return
        cr = btn.cget("corner_radius") if hasattr(btn, "cget") else 10
        cr = int(cr) if cr else 10
        # Right shadow strip
        rs = _tk.Frame(p, bg=_SHADOW_COLOR, bd=0, highlightthickness=0)
        rs.place(x=bx + _OFF, y=by + cr, width=_OFF, height=bh - cr)
        rs.lower(btn)
        shadow_frames.append(rs)
        # Bottom shadow strip
        bs = _tk.Frame(p, bg=_SHADOW_COLOR, bd=0, highlightthickness=0)
        bs.place(x=bx + cr, y=by + bh, width=bw - cr, height=_OFF)
        bs.lower(btn)
        shadow_frames.append(bs)

    def _on_map(e):
        btn.after(20, _make_shadow)

    def _on_configure(e):
        btn.after(20, _make_shadow)

    btn.bind("<Map>", _on_map, add="+")
    btn.bind("<Configure>", _on_configure, add="+")

def _attach_hover(btn: ctk.CTkButton):
    """Attach corner-bracket hover effect to a CTkButton."""
    _HOVER_COLOR  = "#2979FF"   # blue corner brackets
    _NORMAL_BR    = btn.cget("border_color") if btn.cget("border_color") else "#4A4A4C"
    _NORMAL_BW    = btn.cget("border_width") if btn.cget("border_width") else 1
    _NORMAL_FG    = btn.cget("fg_color") if btn.cget("fg_color") else "#6E6E73"
    _HOVER_FG     = "#3A3A3C"   # darker background on hover
    _SZ           = 6    # bracket arm length (px)
    _TH           = 2    # bracket thickness (px)

    corners: list = []

    def _make_corners():
        nonlocal corners
        # Remove old overlays if any
        for c in corners:
            try: c.destroy()
            except: pass
        corners.clear()

        p = btn.winfo_parent()
        try:
            parent_widget = btn.nametowidget(p)
        except Exception:
            return

        # We'll place overlays on the button itself using place() inside it
        # 4 L-shaped corners: top-left, top-right, bottom-left, bottom-right
        # Each corner = 2 thin frames (horizontal + vertical arm)
        specs = [
            # (x_rel, y_rel, w_h, w_v, anchor)
            # top-left
            ("tl_h", dict(relx=0.0, rely=0.0, x=0,    y=0,    width=_SZ, height=_TH)),
            ("tl_v", dict(relx=0.0, rely=0.0, x=0,    y=0,    width=_TH, height=_SZ)),
            # top-right
            ("tr_h", dict(relx=1.0, rely=0.0, x=-_SZ, y=0,    width=_SZ, height=_TH)),
            ("tr_v", dict(relx=1.0, rely=0.0, x=-_TH, y=0,    width=_TH, height=_SZ)),
            # bottom-left
            ("bl_h", dict(relx=0.0, rely=1.0, x=0,    y=-_TH, width=_SZ, height=_TH)),
            ("bl_v", dict(relx=0.0, rely=1.0, x=0,    y=-_SZ, width=_TH, height=_SZ)),
            # bottom-right
            ("br_h", dict(relx=1.0, rely=1.0, x=-_SZ, y=-_TH, width=_SZ, height=_TH)),
            ("br_v", dict(relx=1.0, rely=1.0, x=-_TH, y=-_SZ, width=_TH, height=_SZ)),
        ]
        import tkinter as _tk
        for name, pkw in specs:
            arm = _tk.Frame(btn, bg=_HOVER_COLOR, bd=0, highlightthickness=0)
            arm.place(**pkw)
            arm.lower()   # keep below button text — raise on hover
            corners.append(arm)

    def _on_enter(e):
        try:
            btn.configure(border_color=_HOVER_COLOR, border_width=2,
                          fg_color=_HOVER_FG)
            if not corners:
                _make_corners()
            for c in corners:
                try: c.lift(); c.place_configure()
                except: pass
        except Exception:
            pass

    def _on_leave(e):
        try:
            btn.configure(border_color=_NORMAL_BR, border_width=_NORMAL_BW,
                          fg_color=_NORMAL_FG)
            for c in corners:
                try: c.lower()
                except: pass
        except Exception:
            pass

    btn.bind("<Enter>", _on_enter, add="+")
    btn.bind("<Leave>", _on_leave, add="+")

def pill(parent, text, color, tint):
    f = ctk.CTkFrame(parent, fg_color=tint, corner_radius=10, border_width=0)
    ctk.CTkLabel(f, text=text, font=_mono(9, "bold"),
                 text_color=color, padx=6, pady=2).pack()
    return f

# legacy alias
mac_btn = tg_btn

def tg_entry(parent, **kw):
    d = dict(fg_color=C_INPUT, border_color=C_BORDER, border_width=2,
             text_color=C_TEXT, placeholder_text_color=C_TEXT3,
             height=38, corner_radius=10, font=_font(13))
    d.update(kw)
    return ctk.CTkEntry(parent, **d)

# legacy alias
mac_entry = tg_entry

def tg_sec(parent, text):
    return ctk.CTkLabel(parent, text=text.upper(),
                        font=_font(9, "bold"),
                        text_color=C_SEC_HDR, anchor="w")

# legacy alias
sec_hdr = tg_sec

def hr(parent, color=None, padx=12, pady=3):
    ctk.CTkFrame(parent, fg_color=color or C_BORDER, height=1,
                 corner_radius=0).pack(fill="x", padx=padx, pady=pady)

# ── Phantom directory helper ──────────────────────────────────────────────────
def _phantom_dir(sub: str) -> str:
    """Return ~/Documents/Phantom/<sub>, falling back to <script_dir>/<sub>."""
    primary = Path.home() / "Documents" / "Phantom" / sub
    try:
        primary.mkdir(parents=True, exist_ok=True)
        return str(primary)
    except Exception:
        fallback = Path(__file__).parent / sub
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)


def _fmt_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes/1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes/(1024*1024):.2f} MB"
    return f"{num_bytes/(1024*1024*1024):.2f} GB"


def _fmt_dt(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))


def _file_kind(path: Path) -> str:
    if path.is_dir():
        return "Folder"
    ext = path.suffix.lower()
    if not ext:
        return "File"
    return f"{ext[1:].upper()} file"

# ═════════════════════════════════════════════════════════════════════════════

class EncryptPage(ctk.CTkFrame):
    def __init__(self, parent, app):
        self._app = app
        super().__init__(parent, fg_color=C_BG, corner_radius=0)

        self._selected_files: list = []
        self._bin_bytes  = None
        self._bundle_name = ""
        self._last_auto_bin = ""
        self._key_path   = ""
        self._key_bytes  = None
        self._key_pub_fp = ""
        self._spin_angle = 0
        self._spinning   = False
        self._active_ip  = ""
        self._active_name = ""

        self._build_ui()
        self.after(400, self._start_spinner)
        self.after(200, self._show_startup_message)
        if not _CRYPTO_OK:
            self.after(800, lambda: self._log("⚠  pip install cryptography"))
        threading.Thread(target=self._poll_detect, daemon=True).start()

    # ── ROOT ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        sb = ctk.CTkFrame(self, fg_color=C_PANEL, width=360, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        ctk.CTkFrame(self, fg_color=C_BORDER, width=1,
                     corner_radius=0).grid(row=0, column=0, sticky="nse")
        self._cf = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        self._cf.grid(row=0, column=1, sticky="nsew")
        self._build_sidebar(sb)
        self._build_content(self._cf)

    def _build_titlebar_UNUSED(self):
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
        ctk.CTkFrame(self, fg_color=C_BORDER, height=2,
                     corner_radius=0).pack(fill="x")

    # ── SIDEBAR (compact) ─────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        sc = ctk.CTkFrame(parent, fg_color=C_PANEL, corner_radius=0)
        sc.pack(fill="x", expand=False, anchor="n")

        # ── FILES section ─────────────────────────────────────────────────────
        tg_sec(sc, "Files").pack(fill="x", padx=10, pady=(2, 0))

        # Drop zone — compact single-row style
        _dz_browse = lambda e: self._browse()
        self._dz = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=6,
                                border_color=C_TEXT3, border_width=1, height=28)
        self._dz.pack(fill="x", padx=8, pady=(0, 1))
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
        self._file_list_frame = _tk.Frame(sc, bg=C_INPUT, height=22,
                                          highlightbackground=C_BORDER,
                                          highlightthickness=1, bd=0)
        self._file_list_frame.pack(fill="x", padx=8, pady=(0, 1))
        self._file_list_frame.pack_propagate(False)
        self._file_widgets: list = []
        self._file_placeholder = _tk.Label(
            self._file_list_frame, text="No files selected",
            bg=C_INPUT, fg=C_TEXT3, font=("Segoe UI", 10), anchor="w")
        self._file_placeholder.pack(side="left", padx=8, fill="x", expand=True)

        self._file_count_lbl = ctk.CTkLabel(
            sc, text="No files selected",
            font=_font(10), text_color=C_TEXT3, anchor="w")
        # hidden — filename already shown in _file_list_frame above

        btn_row = ctk.CTkFrame(sc, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=(0, 0))
        btn_row.grid_columnconfigure((0, 1), weight=1)
        tg_btn(btn_row, "Add", self._browse, style="outline",
               height=22, font=_font(9), corner_radius=12
               ).grid(row=0, column=0, sticky="ew", padx=(0, 2))
        tg_btn(btn_row, "Clear", self._clear_files, style="ghost",
               height=22, font=_font(9), corner_radius=12
               ).grid(row=0, column=1, sticky="ew")

        hr(sc, pady=0)

        # ── KEY section ───────────────────────────────────────────────────────
        tg_sec(sc, "Key").pack(fill="x", padx=8, pady=(1, 0))

        # Key drop zone — compact single-row style
        _kz_browse = lambda e: self._browse_key()
        self._kz = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=6,
                                border_color=C_TEXT3, border_width=1, height=28)
        self._kz.pack(fill="x", padx=8, pady=(0, 1))
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
        self._key_list_frame = _tk.Frame(sc, bg=C_INPUT, height=22,
                                         highlightbackground=C_BORDER,
                                         highlightthickness=1, bd=0)
        self._key_list_frame.pack(fill="x", padx=8, pady=(0, 1))
        self._key_list_frame.pack_propagate(False)
        self._key_file_widgets: list = []
        self._key_placeholder = _tk.Label(
            self._key_list_frame, text="No key loaded",
            bg=C_INPUT, fg=C_TEXT3, font=("Segoe UI", 10), anchor="w")
        self._key_placeholder.pack(side="left", padx=8, fill="x", expand=True)

        # Key action buttons
        key_btn_row = ctk.CTkFrame(sc, fg_color="transparent")
        key_btn_row.pack(fill="x", padx=8, pady=(0, 1))
        key_btn_row.grid_columnconfigure((0, 1), weight=1)
        tg_btn(key_btn_row, "Load Key", self._browse_key, style="outline",
               height=22, font=_font(9), corner_radius=12
               ).grid(row=0, column=0, sticky="ew", padx=(0, 2))
        tg_btn(key_btn_row, "⟳ Generate", self._generate_key, style="ghost",
               height=22, font=_font(9), corner_radius=12
               ).grid(row=0, column=1, sticky="ew")

        self._key_status_lbl = ctk.CTkLabel(
            sc, text="",
            font=_font(10), text_color=C_TEXT3, anchor="w")
        # hidden — key name already shown in _key_list_frame above

        # keep StringVar for internal use (unused visually now)
        self._key_var = ctk.StringVar()

        hr(sc, pady=0)

        # ── ACTIONS section ───────────────────────────────────────────────────
        tg_sec(sc, "Actions").pack(fill="x", padx=8, pady=(1, 0))

        self._enc_btn = tg_btn(
            sc, "▶  Encrypt Files",
            command=lambda: threading.Thread(target=self._do_encrypt, daemon=True).start(),
            style="primary", height=24, font=_font(9, "bold"), corner_radius=12,
            state="normal" if _CRYPTO_OK else "disabled")
        self._enc_btn.pack(fill="x", padx=8, pady=(0, 1))

        # Sub-buttons row
        sub_row = ctk.CTkFrame(sc, fg_color="transparent")
        sub_row.pack(fill="x", padx=8, pady=(0, 1))
        sub_row.grid_columnconfigure((0, 1), weight=1)
        self._save_btn = tg_btn(sub_row, "💾 Save", self._save_bin,
                                style="outline", height=22, font=_font(9),
                                corner_radius=12, state="disabled")
        self._save_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        self._send_btn = tg_btn(sub_row, "📤 Sync",
                                command=lambda: threading.Thread(
                                    target=self._do_send, daemon=True).start(),
                                style="outline", height=22, font=_font(9),
                                corner_radius=12, state="disabled")
        self._send_btn.grid(row=0, column=1, sticky="ew")

        # Progress row
        pr = ctk.CTkFrame(sc, fg_color="transparent")
        pr.pack(fill="x", padx=8, pady=(0, 0))
        ctk.CTkLabel(pr, text="OVERALL", font=_font(9), text_color=C_TEXT3,
                     anchor="w").pack(side="left")
        self._enc_pct = ctk.CTkLabel(pr, text="0 %",
                                     font=_font(9, "bold"), text_color=C_TEXT)
        self._enc_pct.pack(side="right")

        self._enc_bar = ctk.CTkProgressBar(sc, mode="determinate", height=3,
                                           corner_radius=2,
                                           progress_color=C_BLUE, fg_color=C_BORDER)
        self._enc_bar.set(0)
        self._enc_bar.pack(fill="x", padx=8, pady=(0, 1))

        self._enc_status = ctk.CTkLabel(
            sc,
            text="Ready" if _CRYPTO_OK else "⚠  pip install cryptography",
            font=_font(9), anchor="w",
            text_color=C_BLUE if _CRYPTO_OK else C_ORANGE)
        self._enc_status.pack(fill="x", padx=8, pady=(0, 0))

        # .bin filename shown in blue after encrypt — hidden until text is set
        self._bundle_lbl = ctk.CTkLabel(sc, text="",
                                        font=_font(9, "bold"), text_color=C_BLUE,
                                        anchor="w", wraplength=215, justify="left")
        # not packed initially

        # Sync status shown after send — hidden until text is set
        self._sync_status_lbl = ctk.CTkLabel(sc, text="",
                                             font=_font(9, "bold"), text_color=C_BLUE,
                                             anchor="w", wraplength=215)
        # not packed initially

        self._enc_file_info_lbl = ctk.CTkLabel(
            sc, text="", font=_font(9), text_color=C_TEXT2,
            anchor="w", justify="left", wraplength=240)
        # not packed initially

        tg_btn(sc, "↗ Open Output Folder", self._enc_open_output,
               style="outline", height=20, font=_font(9), corner_radius=10
               ).pack(fill="x", padx=8, pady=(0, 1))

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

        # Sub-toolbar — spans full width
        hdr = ctk.CTkFrame(parent, fg_color=C_SURFACE, height=32, corner_radius=0)
        hdr.grid(row=0, column=0, columnspan=3, sticky="ew")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="ENCRYPTION ENGINE",
                     font=_font(11, "bold"), text_color=C_TEXT).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(hdr, text="  ·  3-LAYER VISUALIZER",
                     font=_font(10), text_color=C_SEC_HDR).pack(side="left")

        # divider spanning full width
        div = ctk.CTkFrame(parent, fg_color=C_BORDER, height=2, corner_radius=0)
        div.grid(row=0, column=0, columnspan=3, sticky="sew")

        # ── LEFT: engine panel ────────────────────────────────────────────────
        left = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        left.grid(row=1, column=0, sticky="nsew")
        inner = ctk.CTkFrame(left, fg_color=C_BG, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=10, pady=8)

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
        lf.pack(fill="x", pady=(0, 6))
        lf.grid_columnconfigure((0, 1, 2), weight=1)

        self._layer_cards = []
        for col, (lnum, lshort, algo, desc, color, tint) in enumerate(_LAYERS):
            card = ctk.CTkFrame(lf, fg_color=C_CARD, corner_radius=8,
                                border_color=C_BORDER, border_width=2)
            card.grid(row=0, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 6, 0))

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=8, pady=(8, 2))
            pill(top, lnum, color, tint).pack(side="left", padx=(0, 3))
            pill(top, lshort, color, tint).pack(side="left")
            dot = ctk.CTkLabel(top, text="●", font=_font(9), text_color=C_TEXT3)
            dot.pack(side="right")

            title_lbl = ctk.CTkLabel(card, text=algo, font=_font(11, "bold"),
                                     text_color=C_TEXT)
            title_lbl.pack(anchor="w", padx=8, pady=(2, 1))
            desc_lbl = ctk.CTkLabel(card, text=desc, font=_font(9),
                                    text_color=C_TEXT2)
            desc_lbl.pack(anchor="w", padx=8, pady=(0, 4))

            hash_f = ctk.CTkFrame(card, fg_color=C_SURFACE, corner_radius=4)
            hash_f.pack(fill="x", padx=8, pady=(0, 4))
            hash_lbl = ctk.CTkLabel(hash_f, text="HASH ——",
                                    font=_mono(9), text_color=C_TEXT3,
                                    anchor="w", justify="left")
            hash_lbl.pack(fill="x", padx=6, pady=3)

            bar = ctk.CTkProgressBar(card, mode="determinate", height=3,
                                     progress_color=color, fg_color=C_BORDER,
                                     corner_radius=2)
            bar.set(0)
            bar.pack(fill="x", padx=8, pady=(0, 2))

            pct = ctk.CTkLabel(card, text="0 %", font=_font(14, "bold"),
                               text_color=color, anchor="e")
            pct.pack(fill="x", padx=8, pady=(0, 6))

            self._layer_cards.append((card, hash_lbl, bar, pct, dot, color, title_lbl, desc_lbl))

        # ── Overall progress ──────────────────────────────────────────────────
        op = ctk.CTkFrame(inner, fg_color="transparent")
        op.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(op, text="OVERALL PROGRESS",
                     font=_font(9), text_color=C_TEXT2).pack(side="left")
        self._enc_pct2 = ctk.CTkLabel(op, text="0 %",
                                      font=_font(11, "bold"), text_color=C_TEXT)
        self._enc_pct2.pack(side="right")

        self._enc_bar2 = ctk.CTkProgressBar(inner, mode="determinate",
                                            height=3, corner_radius=2,
                                            progress_color=C_BLUE, fg_color=C_BORDER)
        self._enc_bar2.set(0)
        self._enc_bar2.pack(fill="x", pady=(0, 6))

        # ── Terminal log ──────────────────────────────────────────────────────
        log_hdr = ctk.CTkFrame(inner, fg_color="transparent")
        log_hdr.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(log_hdr, text="TERMINAL OUTPUT",
                     font=_font(10, "bold"), text_color=C_TEXT2).pack(side="left")
        tg_btn(log_hdr, "CLR", self._clear_log,
               style="ghost", height=20, width=40,
               font=_font(9), corner_radius=10).pack(side="right")

        self.log = ctk.CTkTextbox(
            inner,
            fg_color=C_SURFACE, text_color=C_TEXT, font=_mono(11),
            corner_radius=8, border_color=C_BORDER, border_width=2,
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
            card.configure(border_color=C_BORDER, border_width=2)
        self._set_ov(0)

    # ── STARTUP WELCOME ───────────────────────────────────────────────────────
    def _show_startup_message(self):
        tick = "\u2714" if _CRYPTO_OK else "\u2717"
        color_aes    = C_GREEN if _CRYPTO_OK else C_RED
        color_hmac   = C_GREEN if _CRYPTO_OK else C_RED
        color_chacha = C_GREEN if _CRYPTO_OK else C_RED
        lines = [
            "\u2550" * 38,
            "  PHANTOM Secure Encrypt \u2014 v2.0",
            "\u2550" * 38,
            f"  {tick}  AES-256-GCM engine       ready",
            f"  {tick}  HMAC-SHA256 engine       ready",
            f"  {tick}  ChaCha20-Poly1305 engine ready",
            "\u2550" * 38,
            "  Encryption engines ready",
            "  Select files and upload above \u2191",
            "\u2550" * 38,
        ]
        for i, line in enumerate(lines):
            self.after(i * 80, lambda l=line: self._log(l))

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
        _input_dir = _phantom_dir("input")
        paths = filedialog.askopenfilenames(
            title="Select files to encrypt",
            initialdir=_input_dir,
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
        self._bundle_lbl.configure(text=""); self._bundle_lbl.pack_forget()
        self._sync_status_lbl.configure(text=""); self._sync_status_lbl.pack_forget()
        self._save_btn.configure(state="disabled")
        self._send_btn.configure(state="disabled")
        self._last_auto_bin = ""
        self._enc_file_info_lbl.configure(text="")
        self._enc_file_info_lbl.pack_forget()

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
        _input_dir = _phantom_dir("input")
        path = filedialog.askopenfilename(title="Select key file",
            initialdir=_input_dir,
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
            self._app._show_toast(f"✓  Key saved: {name}")
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
        try: self._app._conn_spinner.configure(text=self._SPIN[self._spin_angle])
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
            try: self._app._conn_lbl.configure(text="NO SIGNAL", text_color=C_TEXT3)
            except: pass
            try: self._app._conn_dot.configure(text_color=C_TEXT3)
            except: pass
            try: self._ip_lbl.configure(text="Connect to Phantom WiFi")
            except: pass
            self._active_ip = ""; self._active_name = ""; return
        ip, nm, l4, _ = results[0]
        self._active_ip = ip; self._active_name = nm
        try: self._app._conn_lbl.configure(text=f"{nm}  ONLINE", text_color=C_GREEN)
        except: pass
        try: self._app._conn_dot.configure(text_color=C_GREEN)
        except: pass
        try: self._ip_lbl.configure(text=f"{ip}  ·  KEY …{l4}")
        except: pass
        try: self._app._conn_spinner.configure(text="▼", text_color=C_TEXT3)
        except: pass
        self._log(f"Detected: {nm}")

    # ── SAVE ──────────────────────────────────────────────────────────────────
    def _save_bin(self):
        if not self._bin_bytes: return
        _output_dir = _phantom_dir("output")
        path = filedialog.asksaveasfilename(
            defaultextension=".bin", initialfile=self._bundle_name + ".bin",
            initialdir=_output_dir,
            filetypes=[("Binary", "*.bin"), ("All files", "*.*")])
        if path:
            open(path, "wb").write(self._bin_bytes)
            # self._log(f"✓  Saved: {os.path.basename(path)}")
            # self._app._show_toast(f"✓  Saved {os.path.basename(path)}")

    def _enc_open_output(self):
        target = Path(_phantom_dir("output"))
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            if sys.platform == "win32":
                try:
                    os.startfile(str(target))  # type: ignore[attr-defined]
                except Exception:
                    subprocess.Popen(["explorer", str(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception:
            self._app._show_toast("⚠  Could not open output folder", error=True)

    def _update_enc_file_info(self, output_path: str):
        try:
            p = Path(output_path)
            st = p.stat()
            ext = p.suffix.lower() if p.suffix else "(none)"
            info = (
                f"📦  {p.name}\n"
                f"Kind: {_file_kind(p)}  ·  Ext: {ext}\n"
                f"Size: {_fmt_size(st.st_size)}  ·  Created: {_fmt_dt(st.st_ctime)}"
            )
            self._enc_file_info_lbl.configure(text=info, text_color=C_BLUE)
            self._enc_file_info_lbl.pack(fill="x", padx=8, pady=(0, 1))
        except Exception:
            pass

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
            self._sync_status_lbl.configure(text="") or self._sync_status_lbl.pack_forget(),
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
                self._sync_status_lbl.configure(text="✓  Complete", text_color=C_BLUE) or
                self._sync_status_lbl.pack(fill="x", padx=8, pady=(0, 1))))
            # Show key filename in entry after successful sync
            if _key_name:
                self.after(0, lambda k=_key_name: self._key_var.set(k))
            # Mark all selected files as uploaded (blue)
            _uploaded = {os.path.basename(p) for p in self._selected_files}
            self.after(0, lambda u=_uploaded: self._refresh_file_list(u))
            self._app._show_toast(msg)
        elif not spiffs_ok:
            msg = "⚠  Storage full"
            self.after(0, lambda m=msg: self._enc_status.configure(text=m, text_color=C_ORANGE))
            self._app._show_toast(msg, error=True)
        else:
            msg = f"✗  Sync failed ({sl})"
            self.after(0, lambda m=msg: self._enc_status.configure(text=m, text_color=C_RED))
            self._app._show_toast(msg, error=True)

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
        self.after(0, lambda: (self._bundle_lbl.configure(text=""), self._bundle_lbl.pack_forget()))

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

                # ── AUTO-SAVE to ~/Documents/Phantom/output ───────────────────
                out_dir   = _phantom_dir("output")
                auto_path = os.path.join(out_dir, bundle_name + ".bin")
                _save_ok    = False
                try:
                    with open(auto_path, "wb") as _f:
                        _f.write(bin_bytes)
                    _save_ok = True
                    self._last_auto_bin = auto_path
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
                self.after(0, lambda i=save_info: (
                    self._bundle_lbl.configure(text=i),
                    self._bundle_lbl.pack(fill="x", padx=8, pady=(0, 1))))
                self.after(0, lambda: self._enc_status.configure(
                    text=f"Done — {len(files)} file(s) encrypted", text_color=C_BLUE))
                self.after(0, lambda: self._send_btn.configure(state="normal"))
                self.after(0, lambda: self._save_btn.configure(state="normal"))
                self.after(0, lambda: self._phantom_add_file(f"{bundle_name}.bin", size_kb))
                if _save_ok:
                    self.after(0, lambda p=auto_path: self._update_enc_file_info(p))
                self._app._show_toast(f"✓  Saved → output/{bundle_name}.bin")
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
                            self._app._show_toast(_msg)
                        elif not _spiffs_ok:
                            _msg = "⚠  Storage full"
                            self.after(0, lambda m=_msg: self._enc_status.configure(text=m, text_color=C_ORANGE))
                            self._app._show_toast(_msg, error=True)
                        else:
                            _msg = f"✗  Auto-send failed ({_sl})"
                            self.after(0, lambda m=_msg: self._enc_status.configure(text=m, text_color=C_RED))
                            self._app._show_toast(_msg, error=True)
                else:
                    self._log_msg("ℹ  No device connected — skipping auto-upload")
            except Exception as e:
                self._log_msg(f"✗  ERROR: {e}")
                self.after(0, lambda: self._enc_status.configure(
                    text=f"Error: {e}", text_color=C_RED))
            self.after(0, lambda: self._enc_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()


class DecryptPage(ctk.CTkFrame):
    def __init__(self, parent, app):
        self._app = app
        super().__init__(parent, fg_color=C_BG, corner_radius=0)

        self._dec_bin = ctk.StringVar()
        self._dec_bin_full = ""           # actual full path — never displayed
        self._dec_key = ctk.StringVar()   # display only (blank)
        self._dec_key_path = ""           # actual full path — never displayed
        self._dec_out = ctk.StringVar()
        self._dec_last_result = ""

        # Default output → fixed Windows path requested by user
        _out_default = Path(r"C:\Users\Ad\Documents\Phantom\output\return_user")
        try:
            _out_default.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._dec_out_full = str(_out_default)

        # Do NOT auto-load phantom.key — user must explicitly load a key
        self._dec_out.set("Phantom/output/return_user")   # display label only

        self._build_ui()
        self.after(300, self._show_startup_message)

    # ── ROOT ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        sb = ctk.CTkFrame(self, fg_color=C_PANEL, width=360, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        ctk.CTkFrame(self, fg_color=C_BORDER, width=1,
                     corner_radius=0).grid(row=0, column=0, sticky="nse")
        self._cf = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        self._cf.grid(row=0, column=1, sticky="nsew")
        self._build_sidebar(sb)
        self._build_content(self._cf)

    def _build_titlebar_UNUSED(self):
        bar = ctk.CTkFrame(self, fg_color=C_PANEL, height=46, corner_radius=0)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Center — app title (teal accent color for decrypt)
        center = ctk.CTkFrame(bar, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(center, text="PHANTOM — Decrypt",
                     font=_font(14, "bold"), text_color=C_TEXT).pack()

        # Bottom border
        ctk.CTkFrame(self, fg_color=C_BORDER, height=2, corner_radius=0).pack(fill="x")

    # ── SIDEBAR (compact) ─────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        import tkinter as _tk
        sc = ctk.CTkFrame(parent, fg_color=C_PANEL, corner_radius=0)
        sc.pack(fill="both", expand=True)

        # ── INPUT FILE (.bin) ─────────────────────────────────────────────────
        tg_sec(sc, "Input File").pack(fill="x", padx=8, pady=(2, 0))

        _bz_browse = lambda e: self._dec_pick_bin()
        self._bz = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=6,
                                border_color=C_TEXT3, border_width=1, height=28)
        self._bz.pack(fill="x", padx=8, pady=(0, 1))
        self._bz.pack_propagate(False)
        bz_inner = ctk.CTkFrame(self._bz, fg_color="transparent")
        bz_inner.place(relx=0.5, rely=0.5, anchor="center")
        self._bz_icon  = ctk.CTkLabel(bz_inner, text="📦", font=_font(12))
        self._bz_icon.pack(side="left", padx=(0, 4))
        self._bz_title = ctk.CTkLabel(bz_inner, text="Click to select .bin file",
                                      font=_font(10, "bold"), text_color=C_TEXT2)
        self._bz_title.pack(side="left")
        for w in (self._bz, bz_inner, self._bz_icon, self._bz_title):
            w.bind("<Button-1>", _bz_browse)

        # .bin display row
        self._bin_list_frame = _tk.Frame(sc, bg=C_INPUT, height=22,
                                         highlightbackground=C_BORDER,
                                         highlightthickness=1, bd=0)
        self._bin_list_frame.pack(fill="x", padx=8, pady=(0, 1))
        self._bin_list_frame.pack_propagate(False)
        self._bin_placeholder = _tk.Label(
            self._bin_list_frame, text="No file selected",
            bg=C_INPUT, fg=C_TEXT3, font=("Segoe UI", 10), anchor="w")
        self._bin_placeholder.pack(side="left", padx=6, fill="x", expand=True)
        self._bin_display_lbl = None

        _bin_btn_row = ctk.CTkFrame(sc, fg_color="transparent")
        _bin_btn_row.pack(fill="x", padx=8, pady=(0, 1))
        _bin_btn_row.grid_columnconfigure((0, 1), weight=1)
        tg_btn(_bin_btn_row, "Browse .bin", self._dec_pick_bin, style="outline",
               height=20, font=_font(9), corner_radius=10
               ).grid(row=0, column=0, sticky="ew", padx=(0, 2))
        tg_btn(_bin_btn_row, "Clear", lambda: self._refresh_bin_display(""), style="ghost",
               height=20, font=_font(9), corner_radius=10
               ).grid(row=0, column=1, sticky="ew")

        hr(sc, pady=0)

        # ── KEY FILE ──────────────────────────────────────────────────────────
        tg_sec(sc, "Key File").pack(fill="x", padx=8, pady=(1, 0))

        _kz_browse = lambda e: self._dec_pick_key()
        self._kz = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=6,
                                border_color=C_TEXT3, border_width=1, height=28)
        self._kz.pack(fill="x", padx=8, pady=(0, 1))
        self._kz.pack_propagate(False)
        kz_inner = ctk.CTkFrame(self._kz, fg_color="transparent")
        kz_inner.place(relx=0.5, rely=0.5, anchor="center")
        self._kz_icon  = ctk.CTkLabel(kz_inner, text="🔑", font=_font(10))
        self._kz_icon.pack(side="left", padx=(0, 3))
        self._kz_title = ctk.CTkLabel(kz_inner, text="Click to load key",
                                      font=_font(9, "bold"), text_color=C_TEXT3)
        self._kz_title.pack(side="left")
        for w in (self._kz, kz_inner, self._kz_icon, self._kz_title):
            w.bind("<Button-1>", _kz_browse)

        # key display row
        self._key_list_frame = _tk.Frame(sc, bg=C_INPUT, height=22,
                                         highlightbackground=C_BORDER,
                                         highlightthickness=1, bd=0)
        self._key_list_frame.pack(fill="x", padx=8, pady=(0, 1))
        self._key_list_frame.pack_propagate(False)
        # Always show "No key loaded" on startup — user must explicitly load
        self._key_placeholder = _tk.Label(
            self._key_list_frame,
            text="No key loaded",
            bg=C_INPUT,
            fg=C_TEXT3,
            font=("Segoe UI", 10), anchor="w")
        self._key_placeholder.pack(side="left", padx=8, fill="x", expand=True)
        self._key_display_widgets: list = []

        tg_btn(sc, "Load Key", self._dec_pick_key, style="outline",
               height=22, font=_font(9), corner_radius=12
               ).pack(fill="x", padx=8, pady=(0, 0))

        hr(sc, pady=0)

        # ── ACTIONS ───────────────────────────────────────────────────────────
        tg_sec(sc, "Actions").pack(fill="x", padx=8, pady=(1, 0))

        self._dec_btn = tg_btn(
            sc, "▶  Run Decrypt",
            command=lambda: threading.Thread(target=self._dec_start, daemon=True).start(),
            style="primary", height=24, font=_font(9, "bold"), corner_radius=12,
            state="normal" if _CRYPTO_OK else "disabled")
        self._dec_btn.pack(fill="x", padx=8, pady=(0, 1))

        tg_btn(sc, "↗ Open Output", self._dec_open_output,
               style="outline", height=20, font=_font(9), corner_radius=10
               ).pack(fill="x", padx=8, pady=(0, 1))

        # Progress row
        pr = ctk.CTkFrame(sc, fg_color="transparent")
        pr.pack(fill="x", padx=8, pady=(0, 0))
        ctk.CTkLabel(pr, text="OVERALL", font=_font(9),
                     text_color=C_TEXT3, anchor="w").pack(side="left")
        self._dec_pct = ctk.CTkLabel(pr, text="0 %",
                                     font=_font(9, "bold"), text_color=C_TEXT)
        self._dec_pct.pack(side="right")

        self._dec_bar = ctk.CTkProgressBar(sc, mode="determinate", height=3,
                                           corner_radius=2,
                                           progress_color=C_BLUE, fg_color=C_BORDER)
        self._dec_bar.set(0)
        self._dec_bar.pack(fill="x", padx=8, pady=(0, 1))

        self._dec_status = ctk.CTkLabel(
            sc,
            text="Ready" if _CRYPTO_OK else "⚠  pip install cryptography",
            font=_font(9), anchor="w",
            text_color=C_BLUE if _CRYPTO_OK else C_ORANGE)
        self._dec_status.pack(fill="x", padx=8, pady=(0, 0))

        self._dec_result_info_lbl = ctk.CTkLabel(
            sc, text="", font=_font(9), text_color=C_TEXT2,
            anchor="w", justify="left", wraplength=240)
        # not packed initially

        if not _CRYPTO_OK:
            err = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=6,
                               border_color=C_RED, border_width=1)
            err.pack(fill="x", padx=8, pady=(0, 4))
            ctk.CTkLabel(err, text="⚠  pip install cryptography",
                         font=_font(9), text_color=C_RED, anchor="w"
                         ).pack(padx=8, pady=4)

    # ── CONTENT ───────────────────────────────────────────────────────────────
    def _build_content(self, parent):
        # Sub-toolbar
        hdr = ctk.CTkFrame(parent, fg_color=C_SURFACE, height=32, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Left: DECRYPTION ENGINE · 3-LAYER VISUALIZER
        ctk.CTkLabel(hdr, text="DECRYPTION ENGINE",
                     font=_font(11, "bold"), text_color=C_TEXT).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(hdr, text="  ·  3-LAYER VISUALIZER",
                     font=_font(10), text_color=C_SEC_HDR).pack(side="left")

        ctk.CTkFrame(parent, fg_color=C_BORDER, height=2, corner_radius=0).pack(fill="x")

        inner = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=10, pady=8)

        # ── 3 Layer Cards ─────────────────────────────────────────────────────
        _LAYERS = [
            ("L1", "DEC",  "Decryption Layer 1", "First pass",   C_BLUE, "#EBF1FF"),
            ("L2", "INT",  "Decryption Layer 2", "Second pass",  C_BLUE, "#EBF1FF"),
            ("L3", "FIN",  "Decryption Layer 3", "Third pass",   C_BLUE, "#EBF1FF"),
        ]
        lf = ctk.CTkFrame(inner, fg_color="transparent")
        lf.pack(fill="x", pady=(0, 6))
        lf.grid_columnconfigure((0, 1, 2), weight=1)

        self._layer_cards = []
        for col, (lnum, lshort, algo, desc, color, tint) in enumerate(_LAYERS):
            card = ctk.CTkFrame(lf, fg_color=C_CARD, corner_radius=8,
                                border_color=C_BORDER, border_width=2)
            card.grid(row=0, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 6, 0))

            # Top row: pill badges + status dot
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=8, pady=(8, 2))
            pill(top, lnum, color, tint).pack(side="left", padx=(0, 3))
            pill(top, lshort, color, tint).pack(side="left")
            dot = ctk.CTkLabel(top, text="●", font=_font(9), text_color=C_TEXT3)
            dot.pack(side="right")

            title_lbl = ctk.CTkLabel(card, text=algo, font=_font(11, "bold"),
                                     text_color=C_TEXT)
            title_lbl.pack(anchor="w", padx=8, pady=(2, 1))
            desc_lbl = ctk.CTkLabel(card, text=desc, font=_font(9),
                                    text_color=C_TEXT2)
            desc_lbl.pack(anchor="w", padx=8, pady=(0, 4))

            # Hash display box
            hash_f = ctk.CTkFrame(card, fg_color=C_SURFACE, corner_radius=4)
            hash_f.pack(fill="x", padx=8, pady=(0, 4))
            hash_lbl = ctk.CTkLabel(hash_f, text="HASH ——",
                                    font=_mono(9), text_color=C_TEXT3,
                                    anchor="w", justify="left")
            hash_lbl.pack(fill="x", padx=6, pady=3)

            bar = ctk.CTkProgressBar(card, mode="determinate", height=3,
                                     progress_color=color, fg_color=C_BORDER,
                                     corner_radius=2)
            bar.set(0)
            bar.pack(fill="x", padx=8, pady=(0, 2))

            pct = ctk.CTkLabel(card, text="0 %", font=_font(14, "bold"),
                               text_color=color, anchor="e")
            pct.pack(fill="x", padx=8, pady=(0, 6))

            self._layer_cards.append((card, hash_lbl, bar, pct, dot, color, title_lbl, desc_lbl))

        # ── Overall progress ──────────────────────────────────────────────────
        op = ctk.CTkFrame(inner, fg_color="transparent")
        op.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(op, text="OVERALL PROGRESS",
                     font=_font(9), text_color=C_TEXT2).pack(side="left")
        self._dec_pct2 = ctk.CTkLabel(op, text="0 %",
                                      font=_font(11, "bold"), text_color=C_TEXT)
        self._dec_pct2.pack(side="right")

        self._dec_bar2 = ctk.CTkProgressBar(inner, mode="determinate",
                                            height=3, corner_radius=2,
                                            progress_color=C_BLUE, fg_color=C_BORDER)
        self._dec_bar2.set(0)
        self._dec_bar2.pack(fill="x", pady=(0, 6))

        # ── Terminal log ──────────────────────────────────────────────────────
        log_hdr = ctk.CTkFrame(inner, fg_color="transparent")
        log_hdr.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(log_hdr, text="TERMINAL OUTPUT",
                     font=_font(10, "bold"), text_color=C_TEXT2).pack(side="left")
        tg_btn(log_hdr, "CLR", self._dec_clear_log,
               style="ghost", height=20, width=40,
               font=_font(9), corner_radius=10).pack(side="right")

        self._dec_log = ctk.CTkTextbox(
            inner,
            fg_color=C_SURFACE, text_color=C_TEXT, font=_mono(11),
            corner_radius=8, border_color=C_BORDER, border_width=2,
            wrap="word",
            scrollbar_button_color=C_BORDER,
            scrollbar_button_hover_color=C_TEXT3,
            activate_scrollbars=True)
        self._dec_log.pack(fill="both", expand=True)
        self._dec_log.configure(state="disabled")

    # ── LAYER ANIMATION ───────────────────────────────────────────────────────
    def _dec_animate_layer(self, idx, hash_hex, duration_ms, on_done):
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
        self._dec_bar.set(v);  self._dec_pct.configure(text=txt)
        self._dec_bar2.set(v); self._dec_pct2.configure(text=txt)

    # ── STARTUP WELCOME ───────────────────────────────────────────────────────
    def _show_startup_message(self):
        tick = "\u2714" if _CRYPTO_OK else "\u2717"
        lines = [
            "\u2550" * 38,
            "  PHANTOM Secure Decrypt \u2014 v2.0",
            "\u2550" * 38,
            f"  {tick}  ChaCha20-Poly1305 layer  ready",
            f"  {tick}  HMAC-SHA256 verify       ready",
            f"  {tick}  AES-256-GCM layer        ready",
            "\u2550" * 38,
            "  Ready to decrypt \u2014 select .bin file",
            "  and key to begin \u2191",
            "\u2550" * 38,
        ]
        for i, line in enumerate(lines):
            self.after(i * 80, lambda l=line: self._dec_log_msg(l))

    # ── LOG ───────────────────────────────────────────────────────────────────
    def _dec_log_msg(self, msg: str):
        def _append():
            self._dec_log.configure(state="normal")
            self._dec_log.insert("end", f"  ›  {msg}\n")
            self._dec_log.configure(state="disabled")
            self._dec_log.see("end")
        self.after(0, _append)

    def _log_banner(self, filename: str, layers: int = 3, total: int = 3, mode: str = "decrypted"):
        """Append a green ✔ Task completed banner to the terminal log."""
        action = "Finish encrypted" if mode == "encrypted" else "Finish decrypted"
        lines = [
            "┌─────────────────────────────────────┐",
            f"│  ✔  Task completed                  │",
            f"│  \"{filename}\"",
            f"│  {action:<37}│",
            f"│  Status : {layers}/{total} layers               │",
            "└─────────────────────────────────────┘",
        ]
        def _do():
            self._dec_log.configure(state="normal")
            try:
                self._dec_log._textbox.tag_configure("banner_ok",
                    foreground="#34C759",
                    font=("Consolas", 11, "bold"))
            except Exception:
                pass
            self._dec_log._textbox.insert("end", "\n")
            for line in lines:
                try:
                    self._dec_log._textbox.insert("end", f"  {line}\n", "banner_ok")
                except Exception:
                    self._dec_log._textbox.insert("end", f"  {line}\n")
            self._dec_log._textbox.insert("end", "\n")
            self._dec_log.configure(state="disabled")
            self._dec_log.see("end")
        try: self.after(0, _do)
        except: pass

    def _dec_clear_log(self):
        self._dec_log.configure(state="normal")
        self._dec_log.delete("1.0", "end")
        self._dec_log.configure(state="disabled")
        for card, hash_lbl, bar, pct, dot, color, title_lbl, desc_lbl in self._layer_cards:
            bar.set(0); pct.configure(text="0 %", text_color=color)
            dot.configure(text="●", text_color=C_TEXT3)
            hash_lbl.configure(text="HASH ——", text_color=C_TEXT3)
            title_lbl.configure(text_color=C_TEXT)
            desc_lbl.configure(text_color=C_TEXT2)
            card.configure(border_color=C_BORDER, border_width=2)
        self._set_ov(0)

    # ── TOAST ─────────────────────────────────────────────────────────────────
    def _show_toast(self, msg, error=False):
        c = C_RED if error else C_BLUE
        t = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=12,
                         border_color=c, border_width=1)
        t.place(relx=0.5, y=54, anchor="n")
        ctk.CTkLabel(t, text=msg, font=_font(12, "bold"),
                     text_color=c, padx=22, pady=9).pack()
        self.after(3000, t.destroy)

    # ── FILE PICKERS ──────────────────────────────────────────────────────────
    @staticmethod
    def _phantom_input_dir() -> str:
        """Default picker dir for .bin files: ~/Documents/Phantom/output"""
        d = Path.home() / "Documents" / "Phantom" / "output"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return str(d) if d.exists() else str(Path.home())

    @staticmethod
    def _phantom_key_dir() -> str:
        """Default picker dir for key files: ~/Documents/Phantom/output"""
        d = Path.home() / "Documents" / "Phantom" / "output"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return str(d) if d.exists() else str(Path.home())

    def _refresh_bin_display(self, name: str = ""):
        """Update the .bin file display row."""
        import tkinter as _tk
        # clear old widgets except placeholder
        for w in self._bin_list_frame.winfo_children():
            if w is not self._bin_placeholder:
                w.destroy()
        if name:
            self._bin_placeholder.pack_forget()
            lbl = _tk.Label(self._bin_list_frame,
                            text=f"  \U0001f4e6 {name}",
                            bg=C_INPUT, fg=C_TEXT,
                            font=("Segoe UI", 9), anchor="w")
            lbl.pack(side="left", padx=4, fill="x", expand=True)
            # update drop zone — stay green
            self._bz_icon.configure(text="✓")
            self._bz_title.configure(text=name, text_color=C_BLUE)
            self._bz.configure(border_color=C_BLUE)
        else:
            self._bin_placeholder.pack(side="left", padx=8, fill="x", expand=True)
            self._bz_icon.configure(text="📦")
            self._bz_title.configure(text="Click to select .bin file", text_color=C_TEXT2)
            self._bz.configure(border_color=C_TEXT3)

    def _refresh_key_display(self, name: str = ""):
        """Update the key file display row."""
        import tkinter as _tk
        for w in self._key_list_frame.winfo_children():
            if w is not self._key_placeholder:
                w.destroy()
        if name:
            self._key_placeholder.pack_forget()
            lbl = _tk.Label(self._key_list_frame,
                            text=f"  \U0001f511 {name}",
                            bg=C_INPUT, fg=C_BLUE,
                            font=("Segoe UI", 9), anchor="w")
            lbl.pack(side="left", padx=4, fill="x", expand=True)
            # update drop zone — stay blue
            self._kz_icon.configure(text="✓")
            self._kz_title.configure(text=name, text_color=C_BLUE)
            self._kz.configure(border_color=C_BLUE)
        else:
            self._key_placeholder.pack(side="left", padx=8, fill="x", expand=True)
            self._kz_icon.configure(text="🔑")
            self._kz_title.configure(text="Click to load key", text_color=C_TEXT3)
            self._kz.configure(border_color=C_TEXT3)

    def _dec_pick_bin(self):
        p = filedialog.askopenfilename(
            title="Select PHANTOM .bin",
            filetypes=[("PHANTOM bin", "*.bin"), ("All files", "*.*")],
            initialdir=self._phantom_input_dir())
        if p:
            self._dec_bin_full = p
            self._dec_bin.set(os.path.basename(p))
            # Output always goes to fixed Windows target folder
            _out_default = Path(r"C:\Users\Ad\Documents\Phantom\output\return_user")
            try:
                _out_default.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            self._dec_out_full = str(_out_default)
            self._dec_out.set("Phantom/output/return_user")
            self._refresh_bin_display(os.path.basename(p))

    def _dec_pick_key(self):
        p = filedialog.askopenfilename(
            title="Select phantom.key",
            filetypes=[("Key file", "*.key"), ("All files", "*.*")],
            initialdir=self._phantom_key_dir())
        if p:
            self._dec_key_path = p
            self._dec_key.set("")
            self._refresh_key_display(os.path.basename(p))

    def _dec_pick_out(self):
        _init = self._dec_out_full if self._dec_out_full else _phantom_dir("output")
        p = filedialog.askdirectory(title="Select output folder", initialdir=_init)
        if p:
            self._dec_out_full = p                      # real path — never shown
            # Show a short relative label, stripping any leading drive/project path
            try:
                rel = os.path.relpath(p, str(Path(__file__).parent))
                self._dec_out.set(rel.replace("\\", "/"))
            except ValueError:
                self._dec_out.set(os.path.basename(p))  # different drive — show folder name

    def _dec_open_output(self):
        # Must always open exactly this Windows folder
        target = Path(r"C:\Users\Ad\Documents\Phantom\output\return_user")

        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            if sys.platform == "win32":
                try:
                    os.startfile(str(target))  # type: ignore[attr-defined]
                except Exception:
                    subprocess.Popen(["explorer", str(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception:
            self._app._show_toast("⚠  Could not open output folder", error=True)

    def _update_dec_result_info(self, result_path: Path, files_out: list):
        try:
            path_obj = Path(result_path)
            if path_obj.is_file():
                st = path_obj.stat()
                ext = path_obj.suffix.lower() if path_obj.suffix else "(none)"
                info = (
                    f"📄  {path_obj.name}\n"
                    f"Kind: {_file_kind(path_obj)}  ·  Ext: {ext}\n"
                    f"Size: {_fmt_size(st.st_size)}  ·  Created: {_fmt_dt(st.st_ctime)}"
                )
            else:
                total_bytes = sum(f.stat().st_size for f in files_out) if files_out else 0
                latest_ts = max((f.stat().st_mtime for f in files_out), default=time.time())
                info = (
                    f"📁  {path_obj.name}\n"
                    f"Kind: Folder  ·  Items: {len(files_out)}\n"
                    f"Total: {_fmt_size(total_bytes)}  ·  Updated: {_fmt_dt(latest_ts)}"
                )
            self._dec_result_info_lbl.configure(text=info, text_color=C_GREEN)
            self._dec_result_info_lbl.pack(fill="x", padx=8, pady=(0, 1))
            self._dec_last_result = str(path_obj)
        except Exception:
            pass

    # ── MAIN DECRYPT ──────────────────────────────────────────────────────────
    def _dec_start(self):
        # Always use internally stored full paths — display vars contain only labels
        bin_p = self._dec_bin_full if self._dec_bin_full else self._dec_bin.get().strip()
        key_p = self._dec_key_path if self._dec_key_path else self._dec_key.get().strip()
        _disp = self._dec_out.get().strip()
        out_d = self._dec_out_full if self._dec_out_full else (
            _disp if os.path.isabs(_disp) else str(Path(__file__).parent / _disp))

        if not bin_p or not os.path.isfile(bin_p):
            self.after(0, lambda: self._dec_status.configure(
                text="⚠  No .bin file selected", text_color=C_ORANGE)); return
        if not key_p or not os.path.isfile(key_p):
            self.after(0, lambda: self._dec_status.configure(
                text="⚠  No key file selected", text_color=C_ORANGE)); return
        if not out_d:
            self.after(0, lambda: self._dec_status.configure(
                text="⚠  No output folder", text_color=C_ORANGE)); return

        self.after(0, self._dec_clear_log)
        self.after(0, lambda: self._dec_result_info_lbl.configure(text="") or self._dec_result_info_lbl.pack_forget())
        self.after(0, lambda: self._dec_btn.configure(state="disabled"))
        self.after(0, lambda: self._dec_status.configure(
            text="Running…", text_color=C_BLUE))

        def _run():
            self._dec_log_msg("══════════════════════════════════════")
            self._dec_log_msg(f"  TARGET : {os.path.basename(bin_p)}")
            self._dec_log_msg(f"  KEY    : {os.path.basename(key_p)}")
            self._dec_log_msg(f"  OUTPUT : {self._dec_out.get()}")
            self._dec_log_msg("══════════════════════════════════════")
            time.sleep(0.4)

            try:
                raw = open(bin_p, "rb").read()
                if raw[:4] != _PHTM_MAGIC:
                    raise ValueError("NOT A PHANTOM FILE")
                ver = struct.unpack_from("<I", raw, 4)[0]
                if ver != _PHTM_VERSION:
                    raise ValueError(f"UNSUPPORTED VERSION {ver}")
                md5_stored = raw[8:24]
                plen       = struct.unpack_from("<I", raw, 24)[0]
                payload    = raw[28:28 + plen]
                if hashlib.md5(payload).digest() != md5_stored:
                    raise ValueError("MD5 MISMATCH — FILE CORRUPTED")
                master = _phtm_load_key(key_p)
            except Exception as e:
                self._dec_log_msg(f"✗  HEADER ERROR: {e}")
                self.after(0, lambda: self._dec_status.configure(
                    text=f"Error: {e}", text_color=C_RED))
                self.after(0, lambda: self._dec_btn.configure(state="normal"))
                return

            self._dec_log_msg(f"✔  Header OK  |  {plen:,} bytes")
            self._dec_log_msg(f"  MD5  : {md5_stored.hex()}")

            k_aes, k_hmac, k_chacha = _phtm_derive(master)
            h_chacha = hashlib.sha256(k_chacha).hexdigest()
            h_hmac   = hashlib.sha256(k_hmac  ).hexdigest()
            h_aes    = hashlib.sha256(k_aes   ).hexdigest()

            for idx, (hx, label) in enumerate([
                (h_chacha, "[L1]  Decryption Layer 1"),
                (h_hmac,   "[L2]  Decryption Layer 2"),
                (h_aes,    "[L3]  Decryption Layer 3"),
            ]):
                self._dec_log_msg(f"\n{label}")
                time.sleep(0.2)
                ev = threading.Event()
                self.after(0, lambda i=idx, h=hx:
                           self._dec_animate_layer(i, h, 6000, ev.set))
                ev.wait(); time.sleep(0.25)

            self._dec_log_msg("\n[OUT]  Decrypting & decompressing…")
            self._dec_log_msg(f"  [DBG] _phtm_decrypt_3layer callable={callable(_phtm_decrypt_3layer)}")

            # ── Step 1: decrypt the entire payload → .zfld bytes ─────────────
            try:
                zfld_bytes = _phtm_decrypt_3layer(payload, master)
            except Exception as e:
                self._dec_log_msg(f"✗  DECRYPT ERROR: {e}")
                self.after(0, lambda: self._dec_status.configure(
                    text=f"Error: {e}", text_color=C_RED))
                self.after(0, lambda: self._dec_btn.configure(state="normal"))
                return

            self._dec_log_msg(f"  Decrypted: {len(zfld_bytes):,} bytes (.zfld)")

            # ── Step 2: write .zfld to temp, decompress to output_dir ────────
            os.makedirs(out_d, exist_ok=True)
            try:
                with tempfile.TemporaryDirectory(prefix="phantom_dec_") as tmpdir:
                    zfld_tmp = os.path.join(tmpdir, "bundle.zfld")
                    with open(zfld_tmp, "wb") as f:
                        f.write(zfld_bytes)

                    result_folder = decompress_folder(zfld_tmp, out_d)
                    result_path = result_folder if isinstance(result_folder, Path) else Path(result_folder)
                    files_out = [f for f in result_path.rglob("*") if f.is_file()]
            except Exception as e:
                self._dec_log_msg(f"✗  DECOMPRESS ERROR: {e}")
                self.after(0, lambda: self._dec_status.configure(
                    text=f"Error: {e}", text_color=C_RED))
                self.after(0, lambda: self._dec_btn.configure(state="normal"))
                return

            ok  = len(files_out)
            err = 0

            self._dec_log_msg("══════════════════════════════════════")
            for f in files_out:
                sz_str = f"{f.stat().st_size/1024:.1f} KB" if f.stat().st_size >= 1024 else f"{f.stat().st_size} B"
                self._dec_log_msg(f"  ▶  {f.name}  ({sz_str})")
            self._dec_log_msg(f"\n  DONE   {ok} file(s) extracted")
            self._dec_log_msg("══════════════════════════════════════")
            # ── Green completion banner ───────────────────────────────────────
            _bn = files_out[0].name if files_out else os.path.basename(bin_p)
            self._log_banner(_bn, layers=3, total=3, mode="decrypted")

            self.after(0, lambda: self._dec_status.configure(
                text=f"Done — {ok} file(s) decrypted",
                text_color=C_BLUE))
            self.after(0, lambda rp=result_path, fo=files_out: self._update_dec_result_info(rp, fo))
            self._app._show_toast(f"✓  Decrypt: {ok} file(s) done")
            self.after(0, lambda: self._dec_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()





# =============================================================================
class App(ctk.CTk):
    """
    Single unified layout — NO tab switching:
    Titlebar  ─ title  +  signal
    Body      ─ sidebar (360 px scrollable) | divider | content (left enc | divider | right dec)
    """
    def __init__(self):
        super().__init__()
        self.title("PHANTOM \u2014 Encrypt & Decrypt")
        self.geometry("1280x800")
        self.minsize(1100, 700)
        self.configure(fg_color=C_BG)
        self._build_ui()

    # ── ROOT ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_titlebar()

        # Create logic-only page objects (no UI built yet)
        _dummy = ctk.CTkFrame(self, fg_color="transparent", width=0, height=0)
        self._enc_page = EncryptPage(_dummy, self)
        self._dec_page = DecryptPage(_dummy, self)

        body = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=0)   # sidebar
        body.grid_columnconfigure(1, weight=0)   # divider
        body.grid_columnconfigure(2, weight=1)   # content

        # ── Sidebar ────────────────────────────────────────────────────────
        sb = ctk.CTkFrame(body, fg_color=C_PANEL, width=360, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)

        # 1-px right border
        ctk.CTkFrame(body, fg_color=C_BORDER, width=1,
                     corner_radius=0).grid(row=0, column=1, sticky="ns")

        # ── Content area ───────────────────────────────────────────────────
        cf = ctk.CTkFrame(body, fg_color=C_BG, corner_radius=0)
        cf.grid(row=0, column=2, sticky="nsew")

        self._build_sidebar(sb)
        self._build_content(cf)
        self._build_menubar()

    # ── MENUBAR ───────────────────────────────────────────────────────────────
    def _build_menubar(self):
        menubar = tk.Menu(self)

        # ── File menu ─────────────────────────────────────────────────────────
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="Open Encrypted File (.bin)",
            command=lambda: (hasattr(self, "_dec_page") and
                             hasattr(self._dec_page, "_dec_pick_bin") and
                             self._dec_page._dec_pick_bin()))
        file_menu.add_command(
            label="Open Key File (.key)",
            command=lambda: (hasattr(self, "_enc_page") and
                             hasattr(self._enc_page, "_browse_key") and
                             self._enc_page._browse_key()))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        # ── Edit menu ─────────────────────────────────────────────────────────
        edit_menu = tk.Menu(menubar, tearoff=0)
        def _clear_all_logs():
            if hasattr(self, "_enc_page") and hasattr(self._enc_page, "_clear_log"):
                self._enc_page._clear_log()
            if hasattr(self, "_dec_page") and hasattr(self._dec_page, "_dec_clear_log"):
                self._dec_page._dec_clear_log()
        edit_menu.add_command(label="Clear Logs", command=_clear_all_logs)
        edit_menu.add_separator()
        def _copy_log():
            try:
                content = ""
                if hasattr(self, "_enc_page"):
                    content += self._enc_page.log.get("1.0", "end")
                self.clipboard_clear()
                self.clipboard_append(content)
            except Exception:
                pass
        edit_menu.add_command(label="Copy Log", command=_copy_log)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        # ── Help menu ─────────────────────────────────────────────────────────
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(
            label="How to Use",
            command=lambda: messagebox.showinfo(
                "How to Use — PHANTOM",
                "═══════════════════════════════════\n"
                "  ENCRYPT (Left Panel)\n"
                "═══════════════════════════════════\n"
                "1. Click 'Add Files' or drop files into the drop zone.\n"
                "2. Click 'Generate Key' to create a new .key file,\n"
                "   or 'Load Key' to use an existing one.\n"
                "3. Click 'Encrypt & Send' to encrypt and upload\n"
                "   to the connected PHANTOM device.\n"
                "   — or —\n"
                "   Click 'Save .bin' to save the encrypted file locally.\n\n"
                "═══════════════════════════════════\n"
                "  DECRYPT (Right Panel)\n"
                "═══════════════════════════════════\n"
                "1. Click the 📦 zone to select a .bin encrypted file.\n"
                "2. Click the 🔑 zone to load the matching .key file.\n"
                "3. Click 'Decrypt' to extract the original files.\n"
                "   Output is saved to Phantom/output/return_user.\n\n"
                "═══════════════════════════════════\n"
                "  TIPS\n"
                "═══════════════════════════════════\n"
                "• The same .key used to encrypt must be used to decrypt.\n"
                "• Keep your .key file secret — without it, decryption fails.\n"
                "• 3-layer encryption: AES-256-GCM + HMAC-SHA256 + ChaCha20.\n"
                "• Use Settings → Always on Top to keep window visible.\n"
                "═══════════════════════════════════"))
        help_menu.add_command(
            label="About PHANTOM",
            command=lambda: messagebox.showinfo(
                "About PHANTOM",
                "PHANTOM Encrypt & Decrypt v2.0\n\n"
                "3-layer encryption: AES-256-GCM + HMAC-SHA256 + ChaCha20-Poly1305\n\n"
                "Secure file transfer for PHANTOM devices.\n"
                "\u00a9 2024 PHANTOM Project"))
        help_menu.add_separator()
        help_menu.add_command(
            label="Check for Updates",
            command=lambda: messagebox.showinfo(
                "Updates", "You are on the latest version."))
        menubar.add_cascade(label="Help", menu=help_menu)

        # ── Settings menu ─────────────────────────────────────────────────────
        self._theme_dark = [False]  # mutable container for toggle state
        self._topmost_on = [False]

        settings_menu = tk.Menu(menubar, tearoff=0)
        def _toggle_theme():
            self._theme_dark[0] = not self._theme_dark[0]
            ctk.set_appearance_mode("dark" if self._theme_dark[0] else "light")
        settings_menu.add_command(label="Toggle Theme (Light/Dark)", command=_toggle_theme)
        settings_menu.add_separator()
        def _toggle_topmost():
            self._topmost_on[0] = not self._topmost_on[0]
            self.wm_attributes("-topmost", self._topmost_on[0])
        settings_menu.add_command(label="Always on Top", command=_toggle_topmost)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        self.config(menu=menubar)

    # ── TITLEBAR ──────────────────────────────────────────────────────────────
    def _build_titlebar(self):
        bar = ctk.CTkFrame(self, fg_color=C_PANEL, height=28, corner_radius=0)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Right — connection status
        cr = ctk.CTkFrame(bar, fg_color="transparent")
        cr.pack(side="right", padx=18)
        self._conn_dot = ctk.CTkLabel(cr, text="\u25cf", font=_font(11), text_color=C_TEXT3)
        self._conn_dot.pack(side="left", padx=(0, 4))
        self._conn_lbl = ctk.CTkLabel(cr, text="NO SIGNAL", font=_font(11), text_color=C_TEXT3)
        self._conn_lbl.pack(side="left")
        self._conn_spinner = ctk.CTkLabel(cr, text="\u25bc", font=_font(10), text_color=C_TEXT3)
        self._conn_spinner.pack(side="left", padx=(4, 0))

        ctk.CTkFrame(self, fg_color=C_BORDER, height=2, corner_radius=0).pack(fill="x")

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, sb):
        """Scrollable sidebar with Encrypt controls on top, Decrypt below."""
        scroll = ctk.CTkFrame(sb, fg_color=C_PANEL, corner_radius=0)
        scroll.pack(fill="both", expand=True)

        # ── ENCRYPT section header ─────────────────────────────────────────
        enc_hdr = ctk.CTkFrame(scroll, fg_color=C_WHITE, corner_radius=4, height=22)
        enc_hdr.pack(fill="x", padx=6, pady=(4, 1))
        enc_hdr.pack_propagate(False)
        ctk.CTkFrame(enc_hdr, fg_color=C_BLUE, width=3, height=22,
                     corner_radius=0).pack(side="left")
        ctk.CTkLabel(enc_hdr, text="\U0001f512  Encrypt",
                     font=_font(10, "bold"), text_color=C_TEXT,
                     anchor="w").pack(side="left", padx=6)

        # Enc controls
        enc_frame = ctk.CTkFrame(scroll, fg_color=C_PANEL, corner_radius=0)
        enc_frame.pack(fill="x")
        self._enc_page._build_sidebar(enc_frame)

        # ── Divider between sections ───────────────────────────────────────
        ctk.CTkFrame(scroll, fg_color=C_BORDER, height=2,
                     corner_radius=0).pack(fill="x", padx=0, pady=(2, 0))

        # ── DECRYPT section header ─────────────────────────────────────────
        dec_hdr = ctk.CTkFrame(scroll, fg_color=C_WHITE, corner_radius=4, height=22)
        dec_hdr.pack(fill="x", padx=6, pady=(2, 1))
        dec_hdr.pack_propagate(False)
        ctk.CTkFrame(dec_hdr, fg_color=C_TEAL, width=3, height=22,
                     corner_radius=0).pack(side="left")
        ctk.CTkLabel(dec_hdr, text="\U0001f513  Decrypt",
                     font=_font(10, "bold"), text_color=C_TEXT,
                     anchor="w").pack(side="left", padx=6)

        # Dec controls
        dec_frame = ctk.CTkFrame(scroll, fg_color=C_PANEL, corner_radius=0)
        dec_frame.pack(fill="x")
        self._dec_page._build_sidebar(dec_frame)

    # ── CONTENT ───────────────────────────────────────────────────────────────
    def _build_content(self, cf):
        """Content area: Encrypt panel (top) ── divider ── Decrypt panel (bottom)."""
        cf.grid_rowconfigure(0, weight=1)   # encrypt
        cf.grid_rowconfigure(1, weight=0)   # divider
        cf.grid_rowconfigure(2, weight=1)   # decrypt
        cf.grid_columnconfigure(0, weight=1)

        # Encrypt content (top)
        enc_cf = ctk.CTkFrame(cf, fg_color=C_BG, corner_radius=0)
        enc_cf.grid(row=0, column=0, sticky="nsew")
        self._enc_page._build_content(enc_cf)

        # Horizontal divider
        ctk.CTkFrame(cf, fg_color=C_BORDER, height=2,
                     corner_radius=0).grid(row=1, column=0, sticky="ew")

        # Decrypt content (bottom)
        dec_cf = ctk.CTkFrame(cf, fg_color=C_BG, corner_radius=0)
        dec_cf.grid(row=2, column=0, sticky="nsew")
        self._dec_page._build_content(dec_cf)

    # ── TOAST ─────────────────────────────────────────────────────────────────
    def _show_toast(self, msg, error=False):
        c = C_RED if error else C_BLUE
        t = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=12,
                         border_color=c, border_width=1)
        t.place(relx=0.5, y=54, anchor="n")
        ctk.CTkLabel(t, text=msg, font=_font(12, "bold"),
                     text_color=c, padx=22, pady=9).pack()
        self.after(3000, t.destroy)


if __name__ == "__main__":
    app = App()
    app.mainloop()
