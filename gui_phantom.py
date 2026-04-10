"""
gui_phantom.py — Phantom File Transfer
Style: Cyberpunk City Night — navy depths + electric blue + neon cyan-teal
Run:   .venv\Scripts\python gui_phantom.py
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog
import sys, socket, threading, os, time, subprocess, json
import struct, zipfile, hashlib, io, urllib.request, urllib.error
from pathlib import Path

# ── PHANTOM 3-layer crypto ────────────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    from cryptography.hazmat.primitives import hmac as _hmac, hashes as _hashes
    from cryptography.hazmat.backends import default_backend as _backend
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

def _phtm_decrypt_3layer(enc, master):
    k_aes, k_hmac, k_chacha = _phtm_derive(master)
    payload = ChaCha20Poly1305(k_chacha).decrypt(enc[:12], enc[12:], None)
    hmac_tag, inner = payload[-32:], payload[:-32]
    h = _hmac.HMAC(k_hmac, _hashes.SHA256(), backend=_backend())
    h.update(inner); h.verify(hmac_tag)
    return AESGCM(k_aes).decrypt(inner[:12], inner[12:], None)

def _phtm_unpack(bin_path, key_path, out_dir, log_cb=None):
    log = log_cb or print
    raw = open(bin_path, "rb").read()
    if raw[:4] != _PHTM_MAGIC:
        raise ValueError("Not a PHANTOM file")
    ver = struct.unpack_from("<I", raw, 4)[0]
    if ver != _PHTM_VERSION:
        raise ValueError(f"Unsupported version {ver}")
    md5_stored = raw[8:24]
    plen       = struct.unpack_from("<I", raw, 24)[0]
    payload    = raw[28:28 + plen]
    if hashlib.md5(payload).digest() != md5_stored:
        raise ValueError("MD5 mismatch — file corrupted")
    log(f"✔  Header OK  |  {plen:,} bytes  |  MD5: {md5_stored.hex()}")
    master = _phtm_load_key(key_path)
    log(f"✔  Key: {key_path}")
    os.makedirs(out_dir, exist_ok=True)
    results = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        entries = zf.namelist()
        log(f"✔  {len(entries)} file(s) in archive")
        for i, entry in enumerate(entries, 1):
            orig = entry.removesuffix(".enc")
            log(f"\n[{i}/{len(entries)}] {entry} → {orig}")
            try:
                plain = _phtm_decrypt_3layer(zf.read(entry), master)
                out_p = os.path.join(out_dir, orig)
                open(out_p, "wb").write(plain)
                log(f"    ✓  {out_p}  ({len(plain):,} B)")
                results.append((orig, out_p, len(plain), True))
            except Exception as e:
                log(f"    ✗  {e}")
                results.append((orig, None, 0, False))
    return results

# ── Paths & network ───────────────────────────────────────────────────────────
DONGBO_DIR    = Path(__file__).parent / "folder_test"
SERVER_IP     = "192.168.4.1"
SERVER_HTTP   = 80
SERVER_UPLOAD = 8081
CLIENT_IP     = "192.168.5.1"
CLIENT_HTTP   = 80
CLIENT_UPLOAD = 8081

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Cyberpunk City Night palette ──────────────────────────────────────────────
C_BG        = "#010326"   # near-black navy — deepest background
C_CARD      = "#010440"   # midnight navy — card/panel surface
C_SURFACE   = "#020659"   # dark electric navy — secondary surface
C_NAV       = "#020550"   # nav rail background
C_ROW       = "#010440"   # table row
C_ROW_ALT   = "#020552"   # table row alternate
C_BORDER    = "#0A1580"   # electric blue separator
C_BLUE      = "#030BA6"   # electric blue — primary accent
C_BLUE_D    = "#020880"   # deep electric blue hover
C_BLUE_BR   = "#1A2FFF"   # bright electric blue highlight
C_PINK      = "#1BF2DD"   # neon cyan-teal — calls to action / alert
C_PINK_D    = "#13C4B2"   # dark cyan-teal hover
C_GREEN     = "#00FFB3"   # neon cyan-green
C_RED       = "#FF0050"   # neon red
C_ORANGE    = "#FF6B00"   # neon orange
C_TEAL      = "#00D4FF"   # electric cyan
C_LABEL     = "#F0F4FF"   # cool white-blue — primary text
C_LABEL2    = "#A0AAEE"   # muted blue — secondary text
C_LABEL3    = "#6878CC"   # dim blue — placeholder / muted
C_FILL3     = "#020756"   # input fill
C_GLOW      = "#1BF2DD40" # cyan-teal glow (semi-transparent, for borders)

# ── Fonts — plain tuples (safe before Tk root; CTkFont created lazily) ────────
# Use helper _f() inside App to get CTkFont objects
F_MONO      = ("Consolas", 15)
F_MONO_SM   = ("Consolas", 14)

def _mkfont(family, size, weight="normal"):
    return ctk.CTkFont(family=family, size=size, weight=weight)

# ── Network helpers ───────────────────────────────────────────────────────────
_MIME = {
    ".wav":"audio/wav",".mp3":"audio/mpeg",".ogg":"audio/ogg",
    ".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf":"application/pdf",".jpg":"image/jpeg",".jpeg":"image/jpeg",
    ".png":"image/png",".gif":"image/gif",".bmp":"image/bmp",".txt":"text/plain",
}

def _mime_for(fn): return _MIME.get(os.path.splitext(fn)[1].lower(), "application/octet-stream")

def _safe_fname(fn):
    import re
    b, e = os.path.splitext(fn)
    b = re.sub(r'[^\w\-.]', '_', b); b = re.sub(r'_+', '_', b).strip('_')
    return (b or "file") + e.lower()

def tcp_upload(host, port, path, data, timeout=30, filename=""):
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host, port))
        mime = _mime_for(filename) if filename else "application/octet-stream"
        sf   = _safe_fname(filename) if filename else ""
        req  = (f"POST {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                f"Content-Type: {mime}\r\nContent-Length: {len(data)}\r\n"
                + (f"X-Filename: {sf}\r\n" if sf else "")
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

def http_download_file(host, port, filename, timeout=45):
    import urllib.parse
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host, port))
        enc  = urllib.parse.quote(filename, safe=".-_")
        s.sendall((f"GET /file/download?name={enc} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n").encode())
        hbuf = b""; dl = time.time() + timeout
        while b"\r\n\r\n" not in hbuf and time.time() < dl:
            try: c = s.recv(512)
            except socket.timeout: break
            if not c: break
            hbuf += c
            if len(hbuf) > 8192: break
        sep = hbuf.find(b"\r\n\r\n")
        if sep < 0: return b""
        htxt  = hbuf[:sep].decode(errors="replace")
        body  = bytearray(hbuf[sep+4:])
        if " 200 " not in htxt.split("\r\n")[0]: return b""
        clen  = -1
        for line in htxt.split("\r\n")[1:]:
            if line.lower().startswith("content-length:"):
                try: clen = int(line.split(":",1)[1].strip())
                except: pass
        s.settimeout(timeout)
        while (clen < 0 or len(body) < clen) and time.time() < dl:
            try:
                c = s.recv(4096)
                if not c: break
                body.extend(c)
            except socket.timeout: break
        return bytes(body)
    except: return b""
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

# ═════════════════════════════════════════════════════════════════════════════
# WIDGETS — reusable iOS-style components
# ═════════════════════════════════════════════════════════════════════════════

def ios_card(parent, **kw):
    """Cyberpunk card — electric navy with glowing blue border."""
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

def ios_label(parent, text, style="body", **kw):
    styles = {
        "title":   dict(font=ctk.CTkFont("Segoe UI", 24, "bold"), text_color=C_LABEL),
        "section": dict(font=ctk.CTkFont("Segoe UI", 17, "bold"), text_color=C_TEAL),
        "body":    dict(font=ctk.CTkFont("Segoe UI", 15),          text_color=C_LABEL),
        "caption": dict(font=ctk.CTkFont("Segoe UI", 14),          text_color=C_LABEL2),
        "micro":   dict(font=ctk.CTkFont("Segoe UI", 13),          text_color=C_LABEL3),
    }
    cfg = styles.get(style, styles["body"])
    cfg.update(kw)
    return ctk.CTkLabel(parent, text=text, **cfg)

def separator(parent, **kw):
    f = tk.Frame(parent, bg=C_BORDER, height=1)
    return f

# ═════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ═════════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Phantom Transfer")
        self.geometry("1080x680")
        self.minsize(860, 540)
        self.configure(fg_color=C_BG)

        # state
        self.wav_path        = ctk.StringVar()
        self.client_ip       = ctk.StringVar(value=CLIENT_IP)
        self._server_online  = False
        self._client_online  = False
        self._detected_node  = 0
        self._spin_angle     = 0
        self._spinning       = False
        self._sync_proc      = None
        self._local_selected = set()

        self._build_ui()
        self.after(800, self._auto_refresh)
        threading.Thread(target=self._poll_detect, daemon=True).start()
        self._start_auto_sync()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────────────────────
    # ROOT LAYOUT  (3-column: nav │ sidebar │ content)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=0)  # nav rail
        root.grid_columnconfigure(1, weight=0)  # action sidebar
        root.grid_columnconfigure(2, weight=1)  # main content

        # ── Nav rail ──────────────────────────────────────────────────────────
        nav = ctk.CTkFrame(root, fg_color=C_NAV, width=64, corner_radius=0)
        nav.grid(row=0, column=0, sticky="nsew")
        nav.grid_propagate(False)
        self._build_nav(nav)

        tk.Frame(root, bg=C_BORDER, width=1).grid(row=0, column=0,
            sticky="nse", padx=(63, 0))

        # ── Action sidebar ────────────────────────────────────────────────────
        self._sidebar = ctk.CTkFrame(root, fg_color=C_CARD, width=264,
                                     corner_radius=0)
        self._sidebar.grid(row=0, column=1, sticky="nsew")
        self._sidebar.grid_propagate(False)

        tk.Frame(root, bg=C_BORDER, width=1).grid(row=0, column=1,
            sticky="nse", padx=(263, 0))

        # ── Main content ──────────────────────────────────────────────────────
        self._content = ctk.CTkFrame(root, fg_color=C_BG, corner_radius=0)
        self._content.grid(row=0, column=2, sticky="nsew", padx=(1, 0))

        # Build pages (stacked)
        self._pages: dict[str, ctk.CTkFrame] = {}

        for key, build_fn in [
            ("devices", self._build_devices_page),
            ("local",   self._build_local_page),
            ("decrypt", self._build_decrypt_page),
        ]:
            p = ctk.CTkFrame(self._content, fg_color=C_BG, corner_radius=0)
            p.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._pages[key] = p
            build_fn(p)

        # Build sidebar content (reuse for devices; others override below)
        self._build_devices_sidebar(self._sidebar)

        self._show_page("devices")
        self._start_spinner()

    # ─────────────────────────────────────────────────────────────────────────
    # NAV RAIL
    # ─────────────────────────────────────────────────────────────────────────
    def _build_nav(self, parent):
        parent.pack_propagate(False)

        # App icon — hot-pink neon badge
        app_icon = ctk.CTkFrame(parent, fg_color=C_PINK, width=36, height=36,
                                corner_radius=9)
        app_icon.pack(pady=(20, 2))
        app_icon.pack_propagate(False)
        ctk.CTkLabel(app_icon, text="⇅", font=ctk.CTkFont("Consolas", 17, "bold"),
                     text_color="white").place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(parent, text="PH", font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color=C_LABEL3).pack(pady=(0, 12))

        tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x", padx=10, pady=(0, 8))

        self._nav_btns = {}
        items = [
            ("devices", "📡", "Devices"),
            ("local",   "📁", "Local"),
            ("decrypt", "🔓", "Decrypt"),
        ]
        for key, icon, label in items:
            col = ctk.CTkFrame(parent, fg_color="transparent")
            col.pack(fill="x", pady=2)

            # Active indicator bar
            dot = ctk.CTkFrame(col, fg_color="transparent", width=3, height=36,
                               corner_radius=1)
            dot.pack(side="left", padx=(4, 0))

            btn = ctk.CTkButton(col, text=icon, width=44, height=48,
                                font=ctk.CTkFont("Segoe UI", 22),
                                fg_color="transparent",
                                hover_color=C_SURFACE,
                                text_color=C_LABEL2,
                                corner_radius=10,
                                command=lambda k=key: self._show_page(k))
            btn.pack(side="left", padx=2)

            ctk.CTkLabel(col, text=label,
                         font=ctk.CTkFont("Consolas", 11),
                         text_color=C_LABEL3).pack(side="left")

            self._nav_btns[key] = (btn, dot)

        ctk.CTkFrame(parent, fg_color="transparent").pack(fill="both", expand=True)

        self._status_dot = ctk.CTkLabel(parent, text="●",
                                         font=ctk.CTkFont("Segoe UI", 11),
                                         text_color=C_ORANGE)
        self._status_dot.pack(pady=(0, 4))
        self._detect_lbl = ctk.CTkLabel(parent, text="—",
                                         font=ctk.CTkFont("Consolas", 10),
                                         text_color=C_LABEL3,
                                         wraplength=56, justify="center")
        self._detect_lbl.pack(pady=(0, 14))

    def _show_page(self, key: str):
        for k, p in self._pages.items():
            p.lift() if k == key else p.lower()

        for k, (btn, dot) in self._nav_btns.items():
            if k == key:
                btn.configure(fg_color=C_SURFACE, text_color=C_PINK,
                              hover_color=C_SURFACE)
                dot.configure(fg_color=C_PINK)
            else:
                btn.configure(fg_color="transparent", text_color=C_LABEL2,
                              hover_color=C_SURFACE)
                dot.configure(fg_color="transparent")

        # Swap sidebar content
        for w in self._sidebar.winfo_children():
            w.destroy()
        if key == "devices":
            self._build_devices_sidebar(self._sidebar)
        elif key == "local":
            self._build_local_sidebar(self._sidebar)
        elif key == "decrypt":
            self._build_decrypt_sidebar(self._sidebar)

        if key == "local":
            threading.Thread(target=self._refresh_local_page, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # DEVICES — sidebar
    # ─────────────────────────────────────────────────────────────────────────
    def _build_devices_sidebar(self, parent):
        pad = dict(padx=14)

        ctk.CTkLabel(parent, text="TRANSFER",
                     font=ctk.CTkFont("Consolas", 15, "bold"),
                     text_color=C_LABEL, anchor="w"
                     ).pack(fill="x", **pad, pady=(18, 2))
        tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x", padx=14, pady=(0, 10))

        # Connection card
        c1 = ios_card(parent)
        c1.pack(fill="x", **pad, pady=(0, 8))

        r = ctk.CTkFrame(c1, fg_color="transparent")
        r.pack(fill="x", padx=12, pady=(10, 4))

        self._conn_spinner = ctk.CTkLabel(r, text="◌",
                                          font=ctk.CTkFont("Consolas", 15),
                                          text_color=C_LABEL3)
        self._conn_spinner.pack(side="left", padx=(0, 6))

        self._conn_lbl = ctk.CTkLabel(r, text="Scanning…",
                                      font=ctk.CTkFont("Consolas", 14),
                                      text_color=C_LABEL3)
        self._conn_lbl.pack(side="left")

        self._ip_lbl = ctk.CTkLabel(c1, text="Connect to Phantom WiFi",
                                    font=ctk.CTkFont("Consolas", 13),
                                    text_color=C_LABEL3, anchor="w")
        self._ip_lbl.pack(fill="x", padx=12, pady=(0, 10))

        # Send file card
        c2 = ios_card(parent)
        c2.pack(fill="x", **pad, pady=(0, 8))

        ctk.CTkLabel(c2, text="SEND FILE",
                     font=ctk.CTkFont("Consolas", 13),
                     text_color=C_LABEL3, anchor="w"
                     ).pack(fill="x", padx=12, pady=(10, 4))

        fr = ctk.CTkFrame(c2, fg_color="transparent")
        fr.pack(fill="x", padx=10, pady=(0, 6))

        self._file_entry = ios_entry(fr, textvariable=self.wav_path,
                                     placeholder_text="Select a file…",
                                     font=ctk.CTkFont("Consolas", 13))
        self._file_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(fr, text="…", width=36, height=36,
                      font=ctk.CTkFont("Segoe UI", 17),
                      fg_color=C_FILL3, hover_color=C_SURFACE,
                      border_color=C_BORDER, border_width=1,
                      text_color=C_LABEL2, corner_radius=8,
                      command=self._browse).pack(side="right")

        self._upload_btn = ios_btn(c2, "↑  Upload to Device",
                                   command=lambda: threading.Thread(
                                       target=self._upload_to_server,
                                       daemon=True).start(),
                                       font=ctk.CTkFont("Consolas", 14))
        self._upload_btn.pack(fill="x", padx=10, pady=(0, 4))

        self._upload_pb = ctk.CTkProgressBar(c2, mode="indeterminate", height=2,
                                              progress_color=C_BLUE,
                                              fg_color=C_FILL3, corner_radius=1)
        self._upload_pb.pack(fill="x", padx=10, pady=(0, 2))
        self._upload_pb.pack_forget()

        self._upload_result_lbl = ctk.CTkLabel(c2, text="",
                                                font=ctk.CTkFont("Consolas", 13),
                                                text_color=C_LABEL3, anchor="w")
        self._upload_result_lbl.pack(fill="x", padx=12, pady=(0, 8))

        # Receive card
        c3 = ios_card(parent)
        c3.pack(fill="x", **pad, pady=(0, 8))

        ctk.CTkLabel(c3, text="RECEIVE FILE",
                     font=ctk.CTkFont("Consolas", 13),
                     text_color=C_LABEL3, anchor="w"
                     ).pack(fill="x", padx=12, pady=(10, 4))

        self._dl_status_lbl = ctk.CTkLabel(c3, text="Awaiting connection…",
                                            font=ctk.CTkFont("Consolas", 13),
                                            text_color=C_LABEL3, anchor="w")
        self._dl_status_lbl.pack(fill="x", padx=12, pady=(0, 4))

        self._dl_pb = ctk.CTkProgressBar(c3, mode="indeterminate", height=2,
                                          progress_color=C_BLUE,
                                          fg_color=C_FILL3, corner_radius=1)
        self._dl_pb.pack(fill="x", padx=10, pady=(0, 4))
        self._dl_pb.pack_forget()

        ios_btn(c3, "↓  Open Downloads",
                command=self._open_downloads,
                style="ghost", height=36,
                font=ctk.CTkFont("Consolas", 13)
                ).pack(fill="x", padx=10, pady=(0, 10))

    # ─────────────────────────────────────────────────────────────────────────
    # DEVICES — main content
    # ─────────────────────────────────────────────────────────────────────────
    def _build_devices_page(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        wrap.pack(fill="both", expand=True, padx=18, pady=14)

        # ── Section header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(wrap, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 6))

        self._filelist_title = ctk.CTkLabel(hdr, text="REMOTE FILES",
                                             font=ctk.CTkFont("Consolas", 15, "bold"),
                                             text_color=C_LABEL)
        self._filelist_title.pack(side="left")

        for icon, cmd in [
            ("↻", lambda: threading.Thread(target=self._fetch_filelist, daemon=True).start()),
            ("↓", lambda: threading.Thread(target=self._download, args=("server",), daemon=True).start()),
            ("✕", lambda: threading.Thread(target=self._delete_selected_file, daemon=True).start()),
        ]:
            ctk.CTkButton(hdr, text=icon, width=32, height=30,
                          font=ctk.CTkFont("Consolas", 15),
                          fg_color="transparent", hover_color=C_SURFACE,
                          text_color=C_LABEL3, corner_radius=6,
                          command=cmd).pack(side="right", padx=1)

        # ── File table card ───────────────────────────────────────────────────
        tbl = ios_card(wrap)
        tbl.pack(fill="x", pady=(0, 10))

        ch = tk.Frame(tbl, bg=C_SURFACE)
        ch.pack(fill="x", padx=1, pady=(1, 0))
        for txt, anchor, expand in [
            ("Filename", "w", True), ("Size", "center", False),
            ("Duration", "center", False), ("", "center", False),
        ]:
            tk.Label(ch, text=txt, bg=C_SURFACE, fg=C_LABEL3,
                     font=("Consolas", 13), anchor=anchor,
                     padx=12 if anchor == "w" else 0
                     ).pack(side="left", fill="x", expand=expand,
                            ipadx=6, ipady=5)

        self._rows_frame = tk.Frame(tbl, bg=C_CARD)
        self._rows_frame.pack(fill="x", padx=1, pady=(0, 1))

        self._empty_lbl = tk.Label(self._rows_frame, text="No files on device",
                                   bg=C_CARD, fg=C_LABEL3,
                                   font=("Consolas", 14), pady=22)
        self._empty_lbl.pack()

        # ── Activity log ──────────────────────────────────────────────────────
        log_hdr = ctk.CTkFrame(wrap, fg_color="transparent")
        log_hdr.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(log_hdr, text="ACTIVITY LOG",
                     font=ctk.CTkFont("Consolas", 14, "bold"),
                     text_color=C_LABEL).pack(side="left")

        ctk.CTkButton(log_hdr, text="CLEAR", width=60, height=24,
                      font=ctk.CTkFont("Consolas", 12),
                      fg_color=C_FILL3, hover_color=C_SURFACE,
                      text_color=C_LABEL3, corner_radius=4,
                      command=self._clear_log).pack(side="right")

        log_card = ios_card(wrap)
        log_card.pack(fill="both", expand=True)

        self.log = tk.Text(log_card, font=("Consolas", 13),
                           bg=C_CARD, fg=C_LABEL,
                           relief="flat", bd=0,
                           state="disabled", wrap="word",
                           highlightthickness=0,
                           selectbackground=C_SURFACE,
                           insertbackground=C_GREEN,
                           padx=12, pady=8)
        self.log.pack(fill="both", expand=True, padx=2, pady=2)

        vsb = tk.Scrollbar(log_card, command=self.log.yview,
                            bg=C_CARD, troughcolor=C_CARD,
                            bd=0, highlightthickness=0, width=5)
        vsb.pack(side="right", fill="y", padx=(0, 3), pady=4)
        self.log.configure(yscrollcommand=vsb.set)

        self.log.tag_config("ok",     foreground=C_GREEN)
        self.log.tag_config("err",    foreground=C_RED)
        self.log.tag_config("info",   foreground=C_TEAL)
        self.log.tag_config("warn",   foreground=C_ORANGE)
        self.log.tag_config("header", foreground=C_PINK,
                             font=("Consolas", 13, "bold"))
        self.log.tag_config("data",   foreground=C_GREEN)
        self.log.tag_config("prompt", foreground=C_LABEL2)

    # ─────────────────────────────────────────────────────────────────────────
    # LOCAL FILES — sidebar
    # ─────────────────────────────────────────────────────────────────────────
    def _build_local_sidebar(self, parent):
        ctk.CTkLabel(parent, text="LOCAL FILES",
                     font=ctk.CTkFont("Consolas", 15, "bold"),
                     text_color=C_LABEL, anchor="w"
                     ).pack(fill="x", padx=14, pady=(18, 2))
        tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x", padx=14, pady=(0, 10))

        c = ios_card(parent)
        c.pack(fill="x", padx=14, pady=(0, 8))

        self._local_stat_lbl = ctk.CTkLabel(c, text="Loading…",
                                             font=ctk.CTkFont("Consolas", 13),
                                             text_color=C_LABEL3, anchor="w")
        self._local_stat_lbl.pack(fill="x", padx=12, pady=(10, 8))

        for lbl, cmd, st in [
            ("↻  Refresh",          lambda: threading.Thread(target=self._refresh_local_page, daemon=True).start(), "tinted"),
            ("↗  Open Folder",      self._open_dongbo_folder, "ghost"),
            ("✕  Delete Selected",  self._delete_local_selected, "danger"),
        ]:
            ios_btn(c, lbl, command=cmd, style=st,
                    height=38, font=ctk.CTkFont("Consolas", 13)
                    ).pack(fill="x", padx=10, pady=(0, 6))

        ctk.CTkFrame(c, fg_color="transparent", height=4).pack()

    # ─────────────────────────────────────────────────────────────────────────
    # LOCAL FILES — page
    # ─────────────────────────────────────────────────────────────────────────
    def _build_local_page(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        wrap.pack(fill="both", expand=True, padx=18, pady=14)

        ctk.CTkLabel(wrap, text="LOCAL STORAGE",
                     font=ctk.CTkFont("Consolas", 15, "bold"),
                     text_color=C_LABEL, anchor="w"
                     ).pack(fill="x", pady=(0, 6))

        tbl = ios_card(wrap)
        tbl.pack(fill="both", expand=True)

        ch = tk.Frame(tbl, bg=C_SURFACE)
        ch.pack(fill="x", padx=1, pady=(1, 0))
        for txt, anchor, expand in [
            ("Filename", "w", True), ("Size", "center", False),
            ("Modified", "center", False), ("", "center", False),
        ]:
            tk.Label(ch, text=txt, bg=C_SURFACE, fg=C_LABEL3,
                     font=("Consolas", 13), anchor=anchor,
                     padx=12 if anchor == "w" else 0
                     ).pack(side="left", fill="x", expand=expand,
                            ipadx=6, ipady=5)

        scroll_wrap = tk.Frame(tbl, bg=C_CARD)
        scroll_wrap.pack(fill="both", expand=True, padx=1, pady=(0, 1))

        vsb = tk.Scrollbar(scroll_wrap, orient="vertical",
                           bg=C_CARD, troughcolor=C_CARD,
                           bd=0, highlightthickness=0, width=5)
        vsb.pack(side="right", fill="y", padx=(0, 2), pady=4)

        self._local_canvas = tk.Canvas(scroll_wrap, bg=C_CARD,
                                        highlightthickness=0, bd=0,
                                        yscrollcommand=vsb.set)
        self._local_canvas.pack(side="left", fill="both", expand=True)
        vsb.configure(command=self._local_canvas.yview)

        self._local_rows = tk.Frame(self._local_canvas, bg=C_CARD)
        self._local_rows_id = self._local_canvas.create_window(
            (0, 0), window=self._local_rows, anchor="nw")

        self._local_rows.bind("<Configure>",
            lambda e: self._local_canvas.configure(
                scrollregion=self._local_canvas.bbox("all")))
        self._local_canvas.bind("<Configure>",
            lambda e: self._local_canvas.itemconfig(
                self._local_rows_id, width=e.width))
        self._local_canvas.bind_all("<MouseWheel>",
            lambda e: self._local_canvas.yview_scroll(-1*(e.delta//120), "units"))

        tk.Label(self._local_rows, text="No files in folder_test/",
                 bg=C_CARD, fg=C_LABEL3,
                 font=("Consolas", 14), pady=28).pack()

        threading.Thread(target=self._refresh_local_page, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # DECRYPT — sidebar (form)  ·  Dark terminal style
    # ─────────────────────────────────────────────────────────────────────────
    def _build_decrypt_sidebar(self, parent):
        # Cyberpunk panel background
        dark = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=0)
        dark.pack(fill="both", expand=True)

        # Title row
        title_row = ctk.CTkFrame(dark, fg_color="transparent")
        title_row.pack(fill="x", padx=16, pady=(18, 0))
        ctk.CTkLabel(title_row, text="PHANTOM",
                     font=ctk.CTkFont("Consolas", 18, "bold"),
                     text_color=C_PINK).pack(side="left")
        ctk.CTkLabel(title_row, text=" DECRYPT",
                     font=ctk.CTkFont("Consolas", 18, "bold"),
                     text_color=C_LABEL).pack(side="left")

        ctk.CTkLabel(dark, text="3-Layer Cryptographic Engine",
                     font=ctk.CTkFont("Consolas", 13),
                     text_color=C_LABEL3).pack(anchor="w", padx=16, pady=(2, 12))

        # Divider
        tk.Frame(dark, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 14))

        self._dec_bin = ctk.StringVar()
        self._dec_key = ctk.StringVar()
        self._dec_out = ctk.StringVar()

        _def_key = Path(__file__).parent / "decode" / "phantom.key"
        if _def_key.exists(): self._dec_key.set(str(_def_key))
        self._dec_out.set(str(Path(__file__).parent / "decode" / "output"))

        def _dark_field(label, var, pick):
            ctk.CTkLabel(dark, text=label,
                         font=ctk.CTkFont("Consolas", 13),
                         text_color=C_LABEL2, anchor="w"
                         ).pack(fill="x", padx=16, pady=(4, 0))
            r = ctk.CTkFrame(dark, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=(2, 0))
            e = ctk.CTkEntry(r, textvariable=var,
                             fg_color=C_FILL3, border_color=C_BORDER,
                             border_width=1, text_color=C_LABEL,
                             placeholder_text_color=C_LABEL3,
                             height=36, corner_radius=6,
                             font=ctk.CTkFont("Consolas", 13))
            e.pack(side="left", fill="x", expand=True, padx=(0, 6))
            ctk.CTkButton(r, text="…", width=32, height=32,
                          fg_color=C_FILL3, hover_color=C_SURFACE,
                          border_color=C_BORDER, border_width=1,
                          text_color=C_LABEL2, corner_radius=6,
                          command=pick).pack(side="right")

        _dark_field("◈  INPUT FILE (.bin)", self._dec_bin, self._dec_pick_bin)
        _dark_field("◈  KEY FILE (.key)",   self._dec_key, self._dec_pick_key)
        _dark_field("◈  OUTPUT FOLDER",     self._dec_out, self._dec_pick_out)

        tk.Frame(dark, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=(16, 10))

        # Decrypt button — hot-pink neon CTA
        self._dec_btn = ctk.CTkButton(
            dark,
            text="▶  RUN DECRYPT",
            font=ctk.CTkFont("Consolas", 16, "bold"),
            fg_color=C_PINK, hover_color=C_PINK_D,
            text_color="white",
            height=42, corner_radius=8,
            command=lambda: threading.Thread(
                target=self._dec_start, daemon=True).start(),
            state="normal" if _CRYPTO_OK else "disabled")
        self._dec_btn.pack(fill="x", padx=14, pady=(0, 6))

        # Global progress bar
        self._dec_pb = ctk.CTkProgressBar(dark, mode="determinate", height=3,
                                          progress_color=C_PINK,
                                          fg_color=C_FILL3, corner_radius=1)
        self._dec_pb.set(0)
        self._dec_pb.pack(fill="x", padx=14, pady=(0, 2))
        self._dec_pb.pack_forget()

        # Global % label
        self._dec_pct_lbl = ctk.CTkLabel(dark, text="",
                                          font=ctk.CTkFont("Consolas", 13),
                                          text_color=C_GREEN, anchor="e")
        self._dec_pct_lbl.pack(fill="x", padx=14, pady=(0, 4))
        self._dec_pct_lbl.pack_forget()

        self._dec_status_lbl = ctk.CTkLabel(
            dark,
            text="READY" if _CRYPTO_OK else "⚠  pip install cryptography",
            font=ctk.CTkFont("Consolas", 13),
            text_color=C_GREEN if _CRYPTO_OK else C_ORANGE,
            anchor="w")
        self._dec_status_lbl.pack(fill="x", padx=16, pady=(0, 6))

        ctk.CTkButton(dark,
                      text="↗  OPEN OUTPUT",
                      font=ctk.CTkFont("Consolas", 13),
                      fg_color=C_FILL3, hover_color=C_SURFACE,
                      border_color=C_BORDER, border_width=1,
                      text_color=C_LABEL2, height=32, corner_radius=6,
                      command=self._dec_open_output
                      ).pack(fill="x", padx=14, pady=(0, 14))

        if not _CRYPTO_OK:
            ctk.CTkLabel(dark, text="pip install cryptography",
                         font=ctk.CTkFont("Consolas", 13),
                         text_color=C_RED, anchor="w"
                         ).pack(fill="x", padx=16, pady=(0, 8))

    # ─────────────────────────────────────────────────────────────────────────
    # DECRYPT — page (dark terminal + 3-layer visualizer)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_decrypt_page(self, parent):
        # Cyberpunk midnight-navy background
        dark_bg = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        dark_bg.pack(fill="both", expand=True)

        # ── Top: 3 layer cards ───────────────────────────────────────────────
        layers_row = ctk.CTkFrame(dark_bg, fg_color="transparent")
        layers_row.pack(fill="x", padx=20, pady=(16, 10))
        layers_row.grid_columnconfigure((0,1,2), weight=1)

        # Layer colors: cyan / orange / hot-pink
        _LAYERS = [
            ("LAYER 1",  "ChaCha20-Poly1305", "Symmetric stream decrypt",  C_TEAL),
            ("LAYER 2",  "HMAC-SHA256",        "Integrity verification",    C_ORANGE),
            ("LAYER 3",  "AES-256-GCM",        "Final block decrypt",       C_PINK),
        ]

        self._layer_cards = []
        for col, (layer_name, algo, desc, color) in enumerate(_LAYERS):
            card = ctk.CTkFrame(layers_row, fg_color=C_CARD,
                                corner_radius=12, border_color=C_BORDER,
                                border_width=1)
            card.grid(row=0, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 8, 0))

            # layer label + indicator dot
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

            # hash display
            hash_lbl = ctk.CTkLabel(card, text="HASH: —",
                                    font=ctk.CTkFont("Consolas", 12),
                                    text_color=C_LABEL3,
                                    wraplength=180, anchor="w", justify="left")
            hash_lbl.pack(fill="x", padx=14, pady=(0, 6))

            # per-layer progress
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

        self._dec_gbar_lbl = ctk.CTkLabel(gbar_row, text="OVERALL",
                                          font=ctk.CTkFont("Consolas", 15),
                                          text_color=C_LABEL)
        self._dec_gbar_lbl.pack(side="left")

        self._dec_gbar_pct = ctk.CTkLabel(gbar_row, text="0%",
                                          font=ctk.CTkFont("Consolas", 16, "bold"),
                                          text_color=C_PINK)
        self._dec_gbar_pct.pack(side="right")

        self._dec_gbar = ctk.CTkProgressBar(dark_bg, mode="determinate", height=4,
                                            progress_color=C_PINK,
                                            fg_color=C_FILL3, corner_radius=2)
        self._dec_gbar.set(0)
        self._dec_gbar.pack(fill="x", padx=20, pady=(0, 10))

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
                      command=self._dec_clear_log).pack(side="right")

        log_outer = tk.Frame(dark_bg, bg=C_CARD)
        log_outer.pack(fill="both", expand=True, padx=18, pady=(0, 16))

        self._dec_log = tk.Text(
            log_outer, font=("Consolas", 13),
            bg=C_CARD, fg=C_LABEL,
            relief="flat", bd=0,
            state="disabled", wrap="word",
            highlightthickness=0,
            selectbackground=C_SURFACE,
            padx=14, pady=10,
            insertbackground=C_GREEN)
        vsb = tk.Scrollbar(log_outer, orient="vertical",
                           command=self._dec_log.yview,
                           bg=C_CARD, troughcolor=C_CARD,
                           bd=0, highlightthickness=0, width=5)
        self._dec_log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y", pady=4)
        self._dec_log.pack(side="left", fill="both", expand=True)

        # Color tags — cyberpunk palette
        self._dec_log.tag_config("ok",    foreground=C_GREEN)
        self._dec_log.tag_config("err",   foreground=C_RED)
        self._dec_log.tag_config("info",  foreground=C_ORANGE)
        self._dec_log.tag_config("dim",   foreground=C_LABEL2)
        self._dec_log.tag_config("head",  foreground=C_PINK,
                                 font=("Consolas", 13, "bold"))
        self._dec_log.tag_config("prompt",foreground=C_LABEL2)

    # ─────────────────────────────────────────────────────────────────────────
    # SPINNER
    # ─────────────────────────────────────────────────────────────────────────
    _SPIN = ["◌", "◍", "●", "◍"]

    def _start_spinner(self):
        self._spinning = True
        self._tick_spinner()

    def _stop_spinner(self):
        self._spinning = False

    def _tick_spinner(self):
        if not self._spinning: return
        self._spin_angle = (self._spin_angle + 1) % 4
        try: self._conn_spinner.configure(text=self._SPIN[self._spin_angle])
        except: pass
        self.after(350, self._tick_spinner)

    # ─────────────────────────────────────────────────────────────────────────
    # FILE ROW RENDERING
    # ─────────────────────────────────────────────────────────────────────────
    def _update_filelist_ui(self, files):
        for w in self._rows_frame.winfo_children():
            w.destroy()

        if not files:
            tk.Label(self._rows_frame, text="No files on device",
                     bg=C_CARD, fg=C_LABEL3,
                     font=("Consolas", 14), pady=22).pack()
            self._log("File list is empty", "warn")
            return

        for i, f in enumerate(files):
            name = f.get("name", "?")
            sz   = f.get("size", 0)
            try: sz = int(sz)
            except: sz = 0
            dur = f.get("duration_sec", 0)
            try: dur = float(dur)
            except: dur = 0.0

            sz_str  = (f"{sz/1024/1024:.1f} MB" if sz >= 1048576
                       else f"{sz/1024:.1f} KB" if sz >= 1024
                       else f"{sz} B")
            dur_str = f"{int(dur)//60:02d}:{int(dur)%60:02d}"

            bg = C_ROW if i % 2 == 0 else C_ROW_ALT
            icon_txt, icon_color = self._icon_for(Path(name))

            row = tk.Frame(self._rows_frame, bg=bg, cursor="hand2")
            row.pack(fill="x")
            tk.Frame(row, bg=C_BORDER, height=1).pack(fill="x")

            inner = tk.Frame(row, bg=bg)
            inner.pack(fill="x", padx=4, pady=1)

            tk.Label(inner, text=icon_txt, bg=C_SURFACE, fg=icon_color,
                     font=("Segoe UI", 17), width=3, pady=5,
                     relief="flat").pack(side="left", padx=(8, 8), pady=3)

            tk.Label(inner, text=name, bg=bg, fg=C_LABEL,
                     font=("Consolas", 13), anchor="w"
                     ).pack(side="left", fill="x", expand=True)

            tk.Label(inner, text=sz_str, bg=bg, fg=C_LABEL3,
                     font=("Consolas", 13), width=10,
                     anchor="center").pack(side="left", padx=4)

            tk.Label(inner, text=dur_str, bg=bg, fg=C_LABEL3,
                     font=("Consolas", 13), width=7,
                     anchor="center").pack(side="left", padx=4)

            btn_f = tk.Frame(inner, bg=bg)
            btn_f.pack(side="right", padx=(0, 8))

            fname = name

            dl = tk.Label(btn_f, text="↓", bg=bg, fg=C_BLUE,
                          font=("Consolas", 15), width=3, pady=4,
                          relief="flat", cursor="hand2")
            dl.pack(side="left", padx=2)
            dl.bind("<Button-1>", lambda e, fn=fname: threading.Thread(
                target=self._download_file,
                args=(fn, "client" if self._detected_node == 2 else "server"),
                daemon=True).start())
            dl.bind("<Enter>", lambda e, b=dl: b.configure(fg=C_GREEN))
            dl.bind("<Leave>", lambda e, b=dl: b.configure(fg=C_BLUE))

            rm = tk.Label(btn_f, text="✕", bg=bg, fg=C_LABEL3,
                          font=("Consolas", 14), width=3, pady=4,
                          relief="flat", cursor="hand2")
            rm.pack(side="left", padx=2)
            rm.bind("<Button-1>", lambda e, fn=fname: threading.Thread(
                target=self._delete_file, args=(fn,), daemon=True).start())
            rm.bind("<Enter>", lambda e, b=rm: b.configure(fg=C_RED))
            rm.bind("<Leave>", lambda e, b=rm: b.configure(fg=C_LABEL3))

            row.bind("<Enter>", lambda e, w=row: w.configure(bg=C_SURFACE))
            row.bind("<Leave>", lambda e, w=row, c=bg: w.configure(bg=c))

        self._log(f"File list: {len(files)} file(s)", "ok")

    # ─────────────────────────────────────────────────────────────────────────
    # LOCAL ROWS
    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_local_page(self):
        DONGBO_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(
            [p for p in DONGBO_DIR.iterdir()
             if p.is_file() and not p.name.startswith(".")],
            key=lambda p: p.stat().st_mtime, reverse=True)
        self.after(0, self._update_local_rows, files)

    # same as _refresh_local_tab (alias)
    def _refresh_local_tab(self):
        self._refresh_local_page()

    @staticmethod
    def _icon_for(path: Path):
        e = path.suffix.lower()
        if e in (".wav",".mp3",".ogg",".flac",".aac"): return "♪", C_TEAL
        if e in (".png",".jpg",".jpeg",".gif",".bmp",".webp"): return "🖼", C_PINK
        if e in (".docx",".doc",".odt"): return "📄", C_BLUE
        if e in (".xlsx",".xls",".csv"): return "📊", C_GREEN
        if e == ".pdf": return "📕", C_RED
        if e in (".zip",".rar",".7z",".tar",".gz"): return "🗜", C_ORANGE
        if e in (".txt",".md",".log"): return "📝", C_LABEL2
        return "📦", C_LABEL3

    def _update_local_rows(self, files):
        for w in self._local_rows.winfo_children():
            w.destroy()
        self._local_selected.clear()

        try:
            self._local_stat_lbl.configure(
                text=f"{len(files)} file(s)" if files else "No files")
        except: pass

        if not files:
            tk.Label(self._local_rows, text="No files in folder_test/",
                     bg=C_CARD, fg=C_LABEL3,
                     font=("Consolas", 14), pady=26).pack()
            return

        total_kb = sum(p.stat().st_size for p in files) // 1024
        try:
            self._local_stat_lbl.configure(
                text=f"{len(files)} file(s)  ·  {total_kb} KB")
        except: pass

        for i, p in enumerate(files):
            sz    = p.stat().st_size
            mtime = time.strftime("%m/%d  %H:%M", time.localtime(p.stat().st_mtime))
            sz_str = (f"{sz/1024/1024:.2f} MB" if sz >= 1048576
                      else f"{sz/1024:.1f} KB")
            bg = C_ROW if i % 2 == 0 else C_ROW_ALT
            icon, icon_color = self._icon_for(p)

            row = tk.Frame(self._local_rows, bg=bg, cursor="hand2")
            row.pack(fill="x")
            tk.Frame(row, bg=C_BORDER, height=1).pack(fill="x")

            inner = tk.Frame(row, bg=bg)
            inner.pack(fill="x", padx=4, pady=1)

            chk = tk.Label(inner, text="○", bg=bg, fg=C_LABEL3,
                           font=("Consolas", 15), width=2, cursor="hand2")
            chk.pack(side="left", padx=(6, 0), pady=3)

            tk.Label(inner, text=icon, bg=C_SURFACE, fg=icon_color,
                     font=("Segoe UI", 17), width=3, pady=5
                     ).pack(side="left", padx=(4, 8), pady=3)

            name_lbl = tk.Label(inner, text=p.name, bg=bg, fg=C_LABEL,
                                font=("Consolas", 13), anchor="w")
            name_lbl.pack(side="left", fill="x", expand=True)

            tk.Label(inner, text=sz_str, bg=bg, fg=C_LABEL3,
                     font=("Consolas", 13), width=9,
                     anchor="center").pack(side="left", padx=4)

            tk.Label(inner, text=mtime, bg=bg, fg=C_LABEL3,
                     font=("Consolas", 13), width=12,
                     anchor="center").pack(side="left", padx=4)

            btn_f = tk.Frame(inner, bg=bg)
            btn_f.pack(side="right", padx=(0, 8))

            pp = p

            ob = tk.Label(btn_f, text="↗", bg=bg, fg=C_BLUE,
                          font=("Consolas", 15), width=3, pady=4,
                          relief="flat", cursor="hand2")
            ob.pack(side="left", padx=2)
            ob.bind("<Button-1>", lambda e, x=pp: subprocess.Popen(
                f'explorer /select,"{x}"'))
            ob.bind("<Enter>", lambda e, b=ob: b.configure(fg=C_GREEN))
            ob.bind("<Leave>", lambda e, b=ob: b.configure(fg=C_BLUE))

            db = tk.Label(btn_f, text="✕", bg=bg, fg=C_LABEL3,
                          font=("Consolas", 14), width=3, pady=4,
                          relief="flat", cursor="hand2")
            db.pack(side="left", padx=2)
            db.bind("<Button-1>", lambda e, x=pp: threading.Thread(
                target=self._delete_local_file, args=(x,), daemon=True).start())
            db.bind("<Enter>", lambda e, b=db: b.configure(fg=C_RED))
            db.bind("<Leave>", lambda e, b=db: b.configure(fg=C_LABEL3))

            def _toggle(e, x=pp, c=chk):
                if x.name in self._local_selected:
                    self._local_selected.discard(x.name)
                    c.configure(text="○", fg=C_LABEL3)
                else:
                    self._local_selected.add(x.name)
                    c.configure(text="●", fg=C_BLUE)

            chk.bind("<Button-1>", _toggle)
            name_lbl.bind("<Button-1>", _toggle)
            row.bind("<Enter>", lambda e, w=row: w.configure(bg=C_SURFACE))
            row.bind("<Leave>", lambda e, w=row, c=bg: w.configure(bg=c))

    # ─────────────────────────────────────────────────────────────────────────
    # TOAST
    # ─────────────────────────────────────────────────────────────────────────
    def _show_toast(self, msg, error=False):
        border = C_RED  if error else C_GREEN
        fg     = C_RED  if error else C_GREEN
        t = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=20,
                         border_color=border, border_width=1)
        t.place(relx=0.5, y=60, anchor="n")
        ctk.CTkLabel(t, text=msg, font=ctk.CTkFont("Consolas", 14, "bold"),
                     text_color=fg, padx=24, pady=10).pack()
        self.after(3000, t.destroy)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _log(self, msg, tag="info"):
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

    def _dec_clear_log(self):
        self._dec_log.config(state="normal")
        self._dec_log.delete("1.0", "end")
        self._dec_log.config(state="disabled")

    def _statusbar_set(self, msg): pass

    def _browse(self):
        p = filedialog.askopenfilename(
            title="Select File",
            filetypes=[
                ("All Supported", "*.wav *.mp3 *.ogg *.docx *.xlsx *.pdf *.jpg *.jpeg *.png *.gif *.bmp *.txt"),
                ("Audio", "*.wav *.mp3 *.ogg"),
                ("Documents", "*.docx *.xlsx *.pdf *.txt"),
                ("Images", "*.jpg *.jpeg *.png *.gif *.bmp"),
                ("All Files", "*.*"),
            ])
        if p: self.wav_path.set(p)

    def _get_client_ip(self):
        return self.client_ip.get().strip() or CLIENT_IP

    def _open_downloads(self):
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        try: subprocess.Popen(f'explorer "{dl}"')
        except: pass

    def _busy(self, on):
        def _do():
            if on:
                self._upload_pb.pack(fill="x", padx=12, pady=(0, 2))
                self._upload_pb.start()
            else:
                self._upload_pb.stop()
                self._upload_pb.pack_forget()
        try: self.after(0, _do)
        except: pass

    # ─────────────────────────────────────────────────────────────────────────
    # AUTO REFRESH
    # ─────────────────────────────────────────────────────────────────────────
    def _auto_refresh(self):
        threading.Thread(target=self._refresh_status, daemon=True).start()
        self.after(30_000, self._auto_refresh)

    def _refresh_status(self):
        resp = http_get(SERVER_IP, SERVER_HTTP, "/status", timeout=3)
        self._server_online = bool(resp and ("ip" in resp or "node" in resp))
        resp = http_get(self._get_client_ip(), CLIENT_HTTP, "/status", timeout=3)
        self._client_online = bool(resp and ("ip" in resp or "node" in resp))

    def _update_pill(self, which, online): pass

    # ─────────────────────────────────────────────────────────────────────────
    # NODE AUTO-DETECT
    # ─────────────────────────────────────────────────────────────────────────
    def _poll_detect(self):
        ips = [("192.168.4.1", 1), ("192.168.5.1", 2)]
        miss = 0; MISS_TH = 3
        while True:
            found = 0
            for ip, num in ips:
                try:
                    req = urllib.request.Request(
                        f"http://{ip}/status",
                        headers={"User-Agent": "PhantomGUI/4.0"})
                    with urllib.request.urlopen(req, timeout=2) as r:
                        d = json.loads(r.read().decode())
                        if d.get("node") == num:
                            found = num
                            self.after(0, lambda n=num, i=ip: self._on_node_detected(n, i))
                            break
                except: pass
            if not found:
                miss += 1
                if miss >= MISS_TH:
                    self.after(0, self._on_node_lost)
            else:
                miss = 0
            time.sleep(5)

    def _on_node_detected(self, node, ip):
        label = "P1" if node == 1 else "P2"
        full  = "Phantom 1" if node == 1 else "Phantom 2"
        self._detected_node = node
        self._detect_lbl.configure(text=label, text_color=C_GREEN)
        self._status_dot.configure(text="●", text_color=C_GREEN)
        try:
            self._conn_lbl.configure(text=f"{full} ONLINE", text_color=C_GREEN)
            self._ip_lbl.configure(text=f"{full}  ·  {ip}")
        except: pass
        threading.Thread(target=self._fetch_filelist, daemon=True).start()

    def _on_node_lost(self):
        self._detected_node = 0
        self._detect_lbl.configure(text="—", text_color=C_LABEL3)
        self._status_dot.configure(text="●", text_color=C_ORANGE)
        try:
            self._conn_lbl.configure(text="Scanning…", text_color=C_LABEL3)
            self._ip_lbl.configure(text="Connect to Phantom WiFi")
        except: pass

    # ─────────────────────────────────────────────────────────────────────────
    # FILE LIST FETCH
    # ─────────────────────────────────────────────────────────────────────────
    def _fetch_filelist(self):
        node = self._detected_node
        if node == 0:
            self._log("No device connected", "warn"); return
        ip  = "192.168.4.1" if node == 1 else "192.168.5.1"
        lbl = "Phantom 1"   if node == 1 else "Phantom 2"
        self._log(f"Fetching file list from {lbl}…", "header")
        d = http_get_json(f"http://{ip}/file/list", timeout=6)
        if not d:
            self._log("Failed to fetch file list", "err"); return
        files = d.get("files", [])
        free  = d.get("spiffs_free", 0)
        title = f"{lbl.upper()}  ·  {len(files)} FILES  ·  {free//1024} KB FREE"
        self.after(0, lambda: self._filelist_title.configure(text=title))
        self.after(0, lambda: self._update_filelist_ui(files))

    # ─────────────────────────────────────────────────────────────────────────
    # UPLOAD
    # ─────────────────────────────────────────────────────────────────────────
    def _upload_to_server(self):
        path = self.wav_path.get().strip()
        if not path:
            self._log("No file selected", "warn")
            self._show_toast("⚠  Select a file first", error=True); return
        if not os.path.isfile(path):
            self._log(f"File not found: {path}", "err"); return
        self._upload_to_server_do()

    def _upload_to_server_do(self):
        path = self.wav_path.get().strip()
        if not path or not os.path.isfile(path): return
        node = self._detected_node
        if node == 0:
            self._log("No device connected", "err")
            self._show_toast("⚠  No device connected", error=True); return
        filename = os.path.basename(path)
        host = "192.168.4.1" if node == 1 else "192.168.5.1"
        try:
            data = open(path, "rb").read()
        except Exception as e:
            self._log(f"Read error: {e}", "err"); return
        kb  = len(data)/1024
        lbl = "Phantom 1" if node == 1 else "Phantom 2"
        self._log(f"Uploading '{filename}'  ({kb:.1f} KB)  → {lbl}", "header")
        self._busy(True)
        try:
            self._upload_result_lbl.configure(text="Uploading…",
                                              text_color=C_LABEL3)
        except: pass
        t0 = time.time()
        resp, sent = tcp_upload(host, SERVER_UPLOAD, "/file/upload",
                                data, timeout=60, filename=filename)
        elapsed = time.time() - t0
        self._busy(False)
        sz = f"{kb:.1f} KB" if kb >= 1 else f"{len(data)} B"
        if "error" in resp.lower() or sent < len(data):
            self._log(f"Upload FAILED: {resp[:80]}", "err")
            try: self._upload_result_lbl.configure(text="Upload failed",
                                                   text_color=C_RED)
            except: pass
            self._show_toast("✗  Upload failed", error=True)
        else:
            self._log(f"✓  Sent: '{filename}'  ({sz}  {elapsed:.1f}s)", "ok")
            try: self._upload_result_lbl.configure(
                    text=f"✓  {filename}  ({sz})", text_color=C_GREEN)
            except: pass
            self._show_toast(f"✓  Uploaded: {filename}")
            threading.Thread(target=self._fetch_filelist, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # DOWNLOAD ALL
    # ─────────────────────────────────────────────────────────────────────────
    def _download(self, source="server"):
        node = self._detected_node
        if node == 0:
            self._log("No device connected", "warn"); return
        ip  = "192.168.4.1" if node == 1 else "192.168.5.1"
        d   = http_get_json(f"http://{ip}/file/list", timeout=6)
        if not d:
            self._log("Cannot retrieve file list", "err"); return
        for f in d.get("files", []):
            name = f.get("name", "")
            if name:
                threading.Thread(target=self._download_file,
                                 args=(name, source), daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # DOWNLOAD SINGLE FILE
    # ─────────────────────────────────────────────────────────────────────────
    def _download_file(self, filename, source="server"):
        node = self._detected_node
        host = ("192.168.4.1" if node == 1
                else "192.168.5.1" if source == "client"
                else "192.168.4.1")
        self._log(f"Downloading '{filename}'…", "header")
        try:
            self._dl_status_lbl.configure(
                text=f"Downloading  {filename}…", text_color=C_BLUE)
            self._dl_pb.pack(fill="x", padx=12, pady=(0, 4))
            self._dl_pb.start()
        except: pass
        t0   = time.time()
        data = http_download_file(host, SERVER_HTTP, filename, timeout=45)
        elapsed = time.time() - t0
        try:
            self._dl_pb.stop(); self._dl_pb.pack_forget()
        except: pass
        if not data:
            self._log(f"Download FAILED: '{filename}'", "err")
            try: self._dl_status_lbl.configure(text="Download failed",
                                               text_color=C_RED)
            except: pass
            self._show_toast(f"✗  Download failed: {filename}", error=True); return

        DONGBO_DIR.mkdir(parents=True, exist_ok=True)
        abs_path = DONGBO_DIR / filename
        open(abs_path, "wb").write(data)

        kb = len(data)/1024
        sz = f"{kb:.0f} KB" if kb >= 1 else f"{len(data)} B"
        self._log(f"✓  Saved: {filename}  ({sz}  {elapsed:.1f}s)", "ok")
        try: self._dl_status_lbl.configure(
                text=f"✓  {filename}  ({sz})", text_color=C_GREEN)
        except: pass
        self._show_toast(f"✓  Downloaded: {filename}")
        try: subprocess.Popen(f'explorer /select,"{abs_path}"')
        except: pass

    # ─────────────────────────────────────────────────────────────────────────
    # DELETE REMOTE
    # ─────────────────────────────────────────────────────────────────────────
    def _delete_selected_file(self):
        self._log("Select a file from the list to delete", "warn")

    def _delete_file(self, fname):
        safe   = str(fname).lstrip("/")
        target = "192.168.4.1" if self._detected_node == 1 else "192.168.5.1"
        self._log(f"Deleting: {safe}…", "header")
        resp = http_post(target, 80, f"/file/delete?name={safe}")
        if resp and ("ok" in resp or "deleted" in resp) and "error" not in resp.lower():
            self._log(f"✓  Deleted: {safe}", "ok")
            self._show_toast(f"✓  Deleted: {safe}")
            self.after(500, lambda: threading.Thread(
                target=self._fetch_filelist, daemon=True).start())
        else:
            self._log("Delete failed", "err")

    # ─────────────────────────────────────────────────────────────────────────
    # LOCAL DELETE
    # ─────────────────────────────────────────────────────────────────────────
    def _open_dongbo_folder(self):
        DONGBO_DIR.mkdir(parents=True, exist_ok=True)
        try: subprocess.Popen(f'explorer "{DONGBO_DIR}"')
        except Exception as ex: self._log(f"Cannot open folder: {ex}", "err")

    def _delete_local_file(self, path: Path):
        try:
            path.unlink()
            self._log(f"✓  Deleted: {path.name}", "ok")
            self._show_toast(f"✓  Deleted: {path.name}")
        except Exception as ex:
            self._log(f"Delete failed: {ex}", "err")
        threading.Thread(target=self._refresh_local_page, daemon=True).start()

    def _delete_local_selected(self):
        if not self._local_selected:
            self._log("No files selected", "warn"); return
        names = list(self._local_selected)
        for name in names:
            p = DONGBO_DIR / name
            if p.exists():
                try:
                    p.unlink(); self._log(f"✓  Deleted: {name}", "ok")
                except Exception as ex:
                    self._log(f"Error: {ex}", "err")
        threading.Thread(target=self._refresh_local_page, daemon=True).start()
        self._show_toast(f"✓  Deleted {len(names)} file(s)")

    # ─────────────────────────────────────────────────────────────────────────
    # DECRYPT HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _dec_pick_bin(self):
        p = filedialog.askopenfilename(
            title="Select PHANTOM .bin",
            filetypes=[("PHANTOM bin", "*.bin"), ("All files", "*.*")],
            initialdir=str(Path(__file__).parent / "decode"))
        if p:
            self._dec_bin.set(p)
            self._dec_out.set(str(Path(p).parent / "output"))

    def _dec_pick_key(self):
        p = filedialog.askopenfilename(
            title="Select phantom.key",
            filetypes=[("Key file", "*.key"), ("All files", "*.*")],
            initialdir=str(Path(__file__).parent / "decode"))
        if p: self._dec_key.set(p)

    def _dec_pick_out(self):
        p = filedialog.askdirectory(
            title="Select output folder",
            initialdir=self._dec_out.get())
        if p: self._dec_out.set(p)

    def _dec_open_output(self):
        d = self._dec_out.get()
        if os.path.isdir(d):
            try: subprocess.Popen(f'explorer "{d}"')
            except: pass
        else:
            from tkinter import messagebox
            messagebox.showinfo("Folder not found",
                                "No files decrypted to this folder yet.")

    # ─────────────────────────────────────────────────────────────────────────
    # DECRYPT LOG HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _dec_log_msg(self, msg: str):
        def _append():
            self._dec_log.config(state="normal")
            self._dec_log.insert("end", "> ", "prompt")
            if msg.startswith("    ✓") or msg.startswith("✔"):
                tag = "ok"
            elif msg.startswith("    ✗") or "ERROR" in msg or "Lỗi" in msg:
                tag = "err"
            elif msg.startswith("["):
                tag = "info"
            elif msg.startswith("─") or msg.startswith("═"):
                tag = "head"
            else:
                tag = "dim"
            self._dec_log.insert("end", msg + "\n", tag)
            self._dec_log.see("end")
            self._dec_log.config(state="disabled")
        self.after(0, _append)

    def _dec_log_file_link(self, out_path: str, size: int):
        def _append():
            self._dec_log.config(state="normal")
            fname  = os.path.basename(out_path)
            sz_str = f"{size/1024:.1f} KB" if size >= 1024 else f"{size} B"
            label  = f"    ▶  {fname}  ({sz_str})  — click to reveal"
            ltag   = f"link_{id(out_path)}_{self._dec_log.index('end')}"
            self._dec_log.tag_config(ltag, foreground=C_GREEN,
                                      underline=True, font=("Consolas", 13))
            self._dec_log.tag_bind(ltag, "<Button-1>",
                lambda e, p=out_path: subprocess.Popen(
                    f'explorer /select,"{p}"'))
            self._dec_log.tag_bind(ltag, "<Enter>",
                lambda e: self._dec_log.config(cursor="hand2"))
            self._dec_log.tag_bind(ltag, "<Leave>",
                lambda e: self._dec_log.config(cursor=""))
            self._dec_log.insert("end", label + "\n", ltag)
            self._dec_log.see("end")
            self._dec_log.config(state="disabled")
        self.after(0, _append)

    def _dec_clear_log(self):
        self._dec_log.config(state="normal")
        self._dec_log.delete("1.0", "end")
        self._dec_log.config(state="disabled")
        # Reset layer cards
        for (card, hash_lbl, bar, pct, dot, color) in self._layer_cards:
            bar.set(0)
            pct.configure(text="0%", text_color=color)
            dot.configure(text="●", text_color=C_LABEL3)
            hash_lbl.configure(text="HASH: —", text_color=C_LABEL3)
        self._dec_gbar.set(0)
        self._dec_gbar_pct.configure(text="0%", text_color=C_PINK)

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER ANIMATION HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _dec_animate_layer(self, layer_idx: int, hash_hex: str,
                           duration_ms: int, on_done):
        """Animate a single layer bar from 0→100% over duration_ms."""
        card, hash_lbl, bar, pct, dot, color = self._layer_cards[layer_idx]
        steps    = 40
        interval = max(20, duration_ms // steps)
        start_overall = layer_idx / 3.0

        # Activate dot
        dot.configure(text="●", text_color=color)
        # Show truncated hash immediately
        short = hash_hex[:16] + "…" if len(hash_hex) > 16 else hash_hex
        hash_lbl.configure(text=f"HASH: {short}", text_color=color)

        def _tick(step=0):
            if step > steps:
                bar.set(1.0)
                pct.configure(text="100%")
                dot.configure(text="✓", text_color=color)
                # Full hash reveal
                hash_lbl.configure(
                    text=f"HASH: {hash_hex[:32]}…" if len(hash_hex) > 32 else f"HASH: {hash_hex}",
                    text_color=color)
                # Update global bar
                done_overall = (layer_idx + 1) / 3.0
                self._dec_gbar.set(done_overall)
                self._dec_gbar_pct.configure(text=f"{int(done_overall*100)}%")
                on_done()
                return
            frac = step / steps
            bar.set(frac)
            pct.configure(text=f"{int(frac*100)}%")
            # Global bar interpolated within this layer's slice
            g = start_overall + frac / 3.0
            self._dec_gbar.set(g)
            self._dec_gbar_pct.configure(text=f"{int(g*100)}%")
            self.after(interval, lambda: _tick(step + 1))

        _tick()

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN DECRYPT RUNNER
    # ─────────────────────────────────────────────────────────────────────────
    def _dec_start(self):
        bin_p = self._dec_bin.get().strip()
        key_p = self._dec_key.get().strip()
        out_d = self._dec_out.get().strip()

        if not bin_p or not os.path.isfile(bin_p):
            self.after(0, lambda: self._dec_status_lbl.configure(
                text="⚠  NO .BIN FILE", text_color=C_ORANGE)); return
        if not key_p or not os.path.isfile(key_p):
            self.after(0, lambda: self._dec_status_lbl.configure(
                text="⚠  NO KEY FILE", text_color=C_ORANGE)); return
        if not out_d:
            self.after(0, lambda: self._dec_status_lbl.configure(
                text="⚠  NO OUTPUT FOLDER", text_color=C_ORANGE)); return

        # Reset UI
        self.after(0, self._dec_clear_log)
        self.after(0, lambda: self._dec_btn.configure(state="disabled"))
        self.after(0, lambda: self._dec_status_lbl.configure(
            text="RUNNING…", text_color=C_TEAL))

        def _run():
            # ── Stage 0: parse header ────────────────────────────────────
            self._dec_log_msg("═" * 52)
            self._dec_log_msg(f"  TARGET : {os.path.basename(bin_p)}")
            self._dec_log_msg(f"  KEY    : {os.path.basename(key_p)}")
            self._dec_log_msg(f"  OUTPUT : {out_d}")
            self._dec_log_msg("═" * 52)
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
                self.after(0, lambda: self._dec_status_lbl.configure(
                    text=f"ERROR: {e}", text_color=C_RED))
                self.after(0, lambda: self._dec_btn.configure(state="normal"))
                return

            self._dec_log_msg(f"✔  Header OK  |  {plen:,} bytes")
            self._dec_log_msg(f"    MD5   : {md5_stored.hex()}")

            # ── Derive subkeys ───────────────────────────────────────────
            k_aes, k_hmac, k_chacha = _phtm_derive(master)
            h_chacha = hashlib.sha256(k_chacha).hexdigest()
            h_hmac   = hashlib.sha256(k_hmac  ).hexdigest()
            h_aes    = hashlib.sha256(k_aes   ).hexdigest()

            # ── Animate Layer 1 — ChaCha20 ───────────────────────────────
            self._dec_log_msg("\n[LAYER 1]  ChaCha20-Poly1305  —  stream decrypt")
            time.sleep(0.2)

            l1_done = threading.Event()
            self.after(0, lambda: self._dec_animate_layer(
                0, h_chacha, 6000, l1_done.set))
            l1_done.wait()

            self._dec_log_msg(f"    ✓  ChaCha20 key hash : {h_chacha[:32]}…")
            time.sleep(0.3)

            # ── Animate Layer 2 — HMAC ───────────────────────────────────
            self._dec_log_msg("\n[LAYER 2]  HMAC-SHA256  —  integrity verify")
            time.sleep(0.2)

            l2_done = threading.Event()
            self.after(0, lambda: self._dec_animate_layer(
                1, h_hmac, 7000, l2_done.set))
            l2_done.wait()

            self._dec_log_msg(f"    ✓  HMAC key hash     : {h_hmac[:32]}…")
            time.sleep(0.3)

            # ── Animate Layer 3 — AES-GCM ────────────────────────────────
            self._dec_log_msg("\n[LAYER 3]  AES-256-GCM  —  final block decrypt")
            time.sleep(0.2)

            l3_done = threading.Event()
            self.after(0, lambda: self._dec_animate_layer(
                2, h_aes, 6000, l3_done.set))
            l3_done.wait()

            self._dec_log_msg(f"    ✓  AES-256 key hash  : {h_aes[:32]}…")
            time.sleep(0.3)

            # ── Actual decrypt ────────────────────────────────────────────
            self._dec_log_msg("\n[OUTPUT]  Writing decrypted files…")
            os.makedirs(out_d, exist_ok=True)
            results = []
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    entries = zf.namelist()
                    self._dec_log_msg(f"    Archive: {len(entries)} file(s)")
                    for i, entry in enumerate(entries, 1):
                        orig = entry.removesuffix(".enc")
                        self._dec_log_msg(f"    [{i}/{len(entries)}] {orig}")
                        try:
                            plain = _phtm_decrypt_3layer(zf.read(entry), master)
                            out_p = os.path.join(out_d, orig)
                            open(out_p, "wb").write(plain)
                            self._dec_log_msg(f"    ✓  {len(plain):,} bytes → {orig}")
                            results.append((orig, out_p, len(plain), True))
                        except Exception as e2:
                            self._dec_log_msg(f"    ✗  {e2}")
                            results.append((orig, None, 0, False))
            except Exception as e:
                self._dec_log_msg(f"✗  UNPACK ERROR: {e}")
                self.after(0, lambda: self._dec_status_lbl.configure(
                    text=f"ERROR: {e}", text_color=C_RED))
                self.after(0, lambda: self._dec_btn.configure(state="normal"))
                return

            ok  = sum(1 for r in results if r[3])
            err = len(results) - ok

            self._dec_log_msg("═" * 52)
            for (orig, out_path, size, success) in results:
                if success and out_path:
                    self._dec_log_file_link(out_path, size)
            if ok:
                self._dec_log_msg("")
            self._dec_log_msg(f"✔  DONE  {ok} FILE(S) OK  ·  {err} ERROR(S)")
            self._dec_log_msg("═" * 52)

            self.after(0, lambda: self._dec_status_lbl.configure(
                text=f"DONE  {ok}/{len(results)} DECRYPTED",
                text_color=C_GREEN))
            self._show_toast(f"✓  Decrypt: {ok} file(s) done")

            if ok and Path(out_d).resolve() == DONGBO_DIR.resolve():
                threading.Thread(target=self._refresh_local_page, daemon=True).start()

            self.after(0, lambda: self._dec_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # AUTO-SYNC
    # ─────────────────────────────────────────────────────────────────────────
    def _start_auto_sync(self):
        try:
            script = Path(__file__).parent / "dongbo" / "auto_sync.py"
            if not script.exists(): return
            kw = {}
            if sys.platform == "win32":
                kw["creationflags"] = 0x08000000
            self._sync_proc = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        except Exception as ex:
            print(f"[auto_sync] {ex}")

    def _stop_auto_sync(self):
        p = self._sync_proc
        if p and p.poll() is None:
            try:
                p.terminate()
                try: p.wait(timeout=3)
                except subprocess.TimeoutExpired: p.kill()
            except: pass
        self._sync_proc = None

    def _on_close(self):
        self._stop_auto_sync()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
