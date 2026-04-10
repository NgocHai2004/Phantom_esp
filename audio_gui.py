"""
audio_gui.py — Phantom File Transfer Console
Style: macOS Sonoma / Ventura — Apple desktop HIG
Run: .venv\Scripts\python audio_gui.py
"""

import customtkinter as ctk
from tkinter import filedialog, scrolledtext
import tkinter as tk
import tkinter.ttk as ttk
import sys
import socket, threading, os, time, subprocess, json
import struct, zipfile, hashlib, io
import urllib.request, urllib.error
from pathlib import Path

# ── PHANTOM Decrypt (3-layer crypto) ─────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    from cryptography.hazmat.primitives import hmac as _crypto_hmac, hashes as _crypto_hashes
    from cryptography.hazmat.backends import default_backend as _crypto_backend
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

_PHTM_MAGIC   = b"PHTM"
_PHTM_VERSION = 2
_PHTM_KEY_SZ  = 32

def _phtm_load_key(path: str) -> bytes:
    data = open(path, "rb").read()
    if len(data) < _PHTM_KEY_SZ:
        raise ValueError(f"Key file too short: {len(data)} bytes (need {_PHTM_KEY_SZ})")
    return data[:_PHTM_KEY_SZ]

def _phtm_derive(master: bytes):
    def dk(tag): return hashlib.sha256(master + tag).digest()
    return dk(b"AES-GCM"), dk(b"HMAC-SHA256"), dk(b"CHACHA20")

def _phtm_decrypt_3layer(enc: bytes, master: bytes) -> bytes:
    k_aes, k_hmac, k_chacha = _phtm_derive(master)
    payload = ChaCha20Poly1305(k_chacha).decrypt(enc[:12], enc[12:], None)
    hmac_tag, inner = payload[-32:], payload[:-32]
    h = _crypto_hmac.HMAC(k_hmac, _crypto_hashes.SHA256(), backend=_crypto_backend())
    h.update(inner); h.verify(hmac_tag)
    return AESGCM(k_aes).decrypt(inner[:12], inner[12:], None)

def _phtm_unpack(bin_path: str, key_path: str, out_dir: str, log_cb=None):
    def log(m):
        if log_cb: log_cb(m)
        else: print(m)
    raw = open(bin_path, "rb").read()
    if raw[:4] != _PHTM_MAGIC:
        raise ValueError("Không phải file PHANTOM (.bin magic sai)")
    ver = struct.unpack_from("<I", raw, 4)[0]
    if ver != _PHTM_VERSION:
        raise ValueError(f"Unsupported version: {ver} (need {_PHTM_VERSION})")
    md5_stored  = raw[8:24]
    plen        = struct.unpack_from("<I", raw, 24)[0]
    payload     = raw[28:28 + plen]
    if hashlib.md5(payload).digest() != md5_stored:
        raise ValueError("MD5 checksum mismatch — file may be corrupted")
    log(f"✔  Header OK  |  Payload: {plen:,} bytes  |  MD5: {md5_stored.hex()}")
    master = _phtm_load_key(key_path)
    log(f"✔  Key loaded: {key_path}")
    os.makedirs(out_dir, exist_ok=True)
    results = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        entries = zf.namelist()
        log(f"✔  ZIP contains {len(entries)} file(s)")
        for i, entry in enumerate(entries, 1):
            orig = entry.removesuffix(".enc")
            log(f"\n[{i}/{len(entries)}] Decrypting: {entry}  →  {orig}")
            try:
                plain    = _phtm_decrypt_3layer(zf.read(entry), master)
                out_p = os.path.join(out_dir, orig)
                open(out_p, "wb").write(plain)
                log(f"    ✓  Saved: {out_p}  ({len(plain):,} bytes)")
                results.append((orig, out_p, len(plain), True))
            except Exception as e:
                log(f"    ✗  Lỗi: {e}")
                results.append((orig, None, 0, False))
    return results

# ── Local folder (same level as audio_gui.py) ─────────────────────────────────
DONGBO_DIR = Path(__file__).parent / "folder_test"

# ── Network config ─────────────────────────────────────────────────────────────
SERVER_IP     = "192.168.4.1"
SERVER_HTTP   = 80
SERVER_AUDIO  = 8080
SERVER_UPLOAD = 8081
CLIENT_IP     = "192.168.5.1"
CLIENT_HTTP   = 80
CLIENT_AUDIO  = 8080
CLIENT_UPLOAD = 8081

# ── Appearance ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ── Palette — macOS Launchpad Gradient ────────────────────────────────────────
# Gradient stops: deep purple → magenta → pink (like the screenshot)
GRAD_TOP    = "#4B1C6E"   # deep purple top
GRAD_MID    = "#A0306A"   # magenta mid
GRAD_BOT    = "#C0446E"   # pink bottom

# Frosted-glass panels (opaque approximation — white with slight tint)
BG          = "#4B1C6E"   # window bg (overridden by gradient canvas)
BG_CARD     = "#FFFFFF"   # white card (frosted glass light)
BG_SURFACE  = "#F4F0FA"   # very light purple tint surface
BG_SIDEBAR  = "#EDE8F5"   # sidebar with subtle purple wash
BG_ROW      = "#FFFFFF"
BG_ROW_ALT  = "#F9F6FD"   # faint purple row alt
BG_LOG      = "#F4F0FA"
BORDER      = "#DDD6ED"   # light purple-tinted separator
TOOLBAR     = "#FFFFFF"   # toolbar white
ACCENT      = "#7B2FBE"   # purple accent (matches gradient)
ACCENT_GLOW = "#5A1F8C"
ACCENT_ICON = "#BF5AF2"   # macOS system purple
GREEN       = "#28CD41"   # macOS green
WARN        = "#FF9F0A"   # macOS orange
RED         = "#FF453A"   # macOS red
TEAL        = "#5AC8FA"
TEXT        = "#1D1D1F"   # dark label on white cards
MUTED       = "#6E6880"   # muted purple-grey
SUBTLE      = "#AEA8BA"   # subtle purple-grey

# ── Gradient helpers ──────────────────────────────────────────────────────────
def _lerp_color(c1: str, c2: str, t: float) -> str:
    """Linear interpolate between two hex colors. t in [0,1]."""
    r1,g1,b1 = int(c1[1:3],16), int(c1[3:5],16), int(c1[5:7],16)
    r2,g2,b2 = int(c2[1:3],16), int(c2[3:5],16), int(c2[5:7],16)
    r = int(r1 + (r2-r1)*t)
    g = int(g1 + (g2-g1)*t)
    b = int(b1 + (b2-b1)*t)
    return f"#{r:02x}{g:02x}{b:02x}"

def _draw_gradient(canvas: tk.Canvas, w: int, h: int):
    """Draw vertical 3-stop gradient on canvas (top→mid→bottom)."""
    canvas.delete("gradient")
    steps = max(h, 2)
    for i in range(steps):
        t = i / (steps - 1)
        if t < 0.5:
            color = _lerp_color(GRAD_TOP, GRAD_MID, t * 2)
        else:
            color = _lerp_color(GRAD_MID, GRAD_BOT, (t - 0.5) * 2)
        canvas.create_line(0, i, w, i, fill=color, tags="gradient")

# ── Network helpers ────────────────────────────────────────────────────────────
_MIME_MAP = {
    ".wav":  "audio/wav",
    ".mp3":  "audio/mpeg",
    ".ogg":  "audio/ogg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".bmp":  "image/bmp",
    ".txt":  "text/plain",
}

def _mime_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, "application/octet-stream")

def _safe_header_filename(filename: str) -> str:
    import re
    base, ext = os.path.splitext(filename)
    safe_base = re.sub(r'[^\w\-.]', '_', base)
    safe_base = re.sub(r'_+', '_', safe_base).strip('_')
    if not safe_base:
        safe_base = "file"
    return safe_base + ext.lower()

def tcp_upload(host, port, path, data: bytes, timeout=20, filename=""):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        mime = _mime_for(filename) if filename else "application/octet-stream"
        safe_fname = _safe_header_filename(filename) if filename else ""
        req = (f"POST {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Content-Type: {mime}\r\nContent-Length: {len(data)}\r\n"
               + (f"X-Filename: {safe_fname}\r\n" if safe_fname else "")
               + "Connection: close\r\n\r\n").encode()
        s.sendall(req)
        sent = 0
        chunk = 4096
        while sent < len(data):
            end = min(sent + chunk, len(data))
            s.sendall(data[sent:end])
            sent = end
        resp = b""
        s.settimeout(12)
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

def http_upload(host, port, filename: str, data: bytes, timeout=30):
    safe_fname = _safe_header_filename(filename) if filename else "file.bin"
    mime = _mime_for(filename) if filename else "application/octet-stream"
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        req = (
            f"POST /file/upload HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"Content-Type: {mime}\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"X-Filename: {safe_fname}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("ascii")
        s.sendall(req)
        sent = 0
        chunk_sz = 1024
        while sent < len(data):
            end = min(sent + chunk_sz, len(data))
            s.sendall(data[sent:end])
            sent = end
        resp = b""
        s.settimeout(15)
        try:
            while True:
                c = s.recv(4096)
                if not c:
                    break
                resp += c
        except Exception:
            pass
        return resp.decode(errors="replace"), sent
    except Exception as e:
        return f"ERROR: {e}", 0
    finally:
        try:
            s.close()
        except Exception:
            pass

def http_download_file(host, port, filename: str, timeout=45) -> bytes:
    import urllib.parse
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        encoded_name = urllib.parse.quote(filename, safe=".-_")
        path = f"/file/download?name={encoded_name}"
        s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                   "Connection: close\r\n\r\n").encode())

        header_buf = b""
        deadline = time.time() + timeout
        while b"\r\n\r\n" not in header_buf and time.time() < deadline:
            try:
                chunk = s.recv(512)
            except socket.timeout:
                break
            if not chunk:
                break
            header_buf += chunk
            if len(header_buf) > 8192:
                break

        sep = header_buf.find(b"\r\n\r\n")
        if sep < 0:
            return b""

        header_text = header_buf[:sep].decode(errors="replace")
        body_start  = header_buf[sep + 4:]

        status_line = header_text.split("\r\n")[0]
        if " 200 " not in status_line and not status_line.endswith(" 200"):
            return b""

        content_length = -1
        chunked = False
        for line in header_text.split("\r\n")[1:]:
            lo = line.lower()
            if lo.startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif lo.startswith("transfer-encoding:") and "chunked" in lo:
                chunked = True

        if content_length >= 0:
            body = bytearray(body_start)
            s.settimeout(timeout)
            deadline = time.time() + timeout
            while len(body) < content_length and time.time() < deadline:
                want = min(4096, content_length - len(body))
                try:
                    chunk = s.recv(want)
                    if not chunk:
                        break
                    body.extend(chunk)
                except socket.timeout:
                    break
            return bytes(body)

        elif chunked:
            body = bytearray()
            buf  = bytearray(body_start)
            s.settimeout(timeout)
            deadline = time.time() + timeout

            def _read_until_crlf():
                nonlocal buf
                while True:
                    idx = buf.find(b"\r\n")
                    if idx >= 0:
                        line = buf[:idx]
                        buf  = buf[idx + 2:]
                        return line
                    if time.time() > deadline:
                        return None
                    try:
                        more = s.recv(256)
                        if not more:
                            return None
                        buf.extend(more)
                    except socket.timeout:
                        return None

            def _read_exact(n):
                nonlocal buf
                while len(buf) < n and time.time() < deadline:
                    try:
                        more = s.recv(min(4096, n - len(buf)))
                        if not more:
                            break
                        buf.extend(more)
                    except socket.timeout:
                        break
                data = bytes(buf[:n])
                buf  = buf[n:]
                return data

            while time.time() < deadline:
                size_line = _read_until_crlf()
                if size_line is None:
                    break
                try:
                    chunk_size = int(size_line.split(b";")[0].strip(), 16)
                except (ValueError, IndexError):
                    break
                if chunk_size == 0:
                    break
                data = _read_exact(chunk_size)
                body.extend(data)
                _read_until_crlf()

            return bytes(body)

        else:
            body = bytearray(body_start)
            s.settimeout(timeout)
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    body.extend(chunk)
            except socket.timeout:
                pass
            return bytes(body)

    except Exception:
        return b""
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
        self.title("Phantom Transfer")
        self.geometry("1060x680")
        self.minsize(860, 540)
        self.configure(fg_color=GRAD_TOP)

        self.wav_path       = ctk.StringVar(value="")
        self.client_ip      = ctk.StringVar(value=CLIENT_IP)
        self._server_online = False
        self._client_online = False
        self._detected_node = 0
        self._bg_downloaded = False
        self._spin_angle    = 0
        self._spinning      = False
        self._sync_proc     = None
        self._active_page   = "devices"

        self._build_ui()
        self.after(600, self._auto_refresh)
        threading.Thread(target=self._poll_detect, daemon=True).start()
        self.bind("<FocusIn>", lambda e: None)
        self._start_auto_sync()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────────────────────
    # BUILD UI  — macOS Launchpad gradient + frosted glass
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Gradient canvas (fills entire window, behind everything) ─────────
        self._grad_canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self._grad_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.bind("<Configure>", self._on_resize)
        # draw immediately with initial size
        self.update_idletasks()
        self._on_resize(None)

        # ── Semi-transparent toolbar ─────────────────────────────────────────
        toolbar = ctk.CTkFrame(self, fg_color="#FFFFFF",
                               height=48, corner_radius=0)
        toolbar.place(x=0, y=0, relwidth=1)
        toolbar.pack_propagate(False)

        # Divider below toolbar
        div = tk.Frame(self, bg="#FFFFFF", height=1)
        div.place(x=0, y=48, relwidth=1)

        # App icon
        icon_box = ctk.CTkFrame(toolbar, fg_color=ACCENT,
                                width=24, height=24, corner_radius=7)
        icon_box.place(x=16, y=12)
        icon_box.pack_propagate(False)
        ctk.CTkLabel(icon_box, text="⇅",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="white").place(relx=0.5, rely=0.5, anchor="center")

        # Title centered
        ctk.CTkLabel(toolbar, text="Phantom Transfer",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=TEXT,
                     fg_color="transparent"
                     ).place(relx=0.5, rely=0.5, anchor="center")

        # Status right
        status_badge = ctk.CTkFrame(toolbar, fg_color="transparent")
        status_badge.place(relx=1.0, rely=0.5, anchor="e", x=-16)

        self._conn_spinner = ctk.CTkLabel(
            status_badge, text="◌",
            font=ctk.CTkFont("Segoe UI", 11), text_color=MUTED)
        self._conn_spinner.pack(side="left", padx=(0, 3))

        self._conn_lbl = ctk.CTkLabel(
            status_badge, text="Scanning…",
            font=ctk.CTkFont("Segoe UI", 11), text_color=MUTED)
        self._conn_lbl.pack(side="left")

        # ── Main body below toolbar — use tk.Frame for place() geometry ──────
        TOOLBAR_H = 49   # toolbar 48px + 1px divider

        # Sidebar container (plain tk.Frame supports width/height in place)
        _sidebar_tk = tk.Frame(self, bg="#FFFFFF", width=200)
        _sidebar_tk.place(x=0, y=TOOLBAR_H, width=200, relheight=1.0,
                          height=-TOOLBAR_H)

        # CTkFrame fills sidebar_tk
        sidebar_outer = ctk.CTkFrame(_sidebar_tk, fg_color="#FFFFFF",
                                     corner_radius=0)
        sidebar_outer.pack(fill="both", expand=True)

        # Sidebar right-edge divider
        tk.Frame(self, bg=BORDER).place(x=200, y=TOOLBAR_H,
                                        width=1, relheight=1.0,
                                        height=-TOOLBAR_H)

        # Content container
        _content_tk = tk.Frame(self, bg=GRAD_TOP)
        _content_tk.place(x=201, y=TOOLBAR_H, relwidth=1.0,
                          width=-201, relheight=1.0, height=-TOOLBAR_H)

        content_outer = ctk.CTkFrame(_content_tk, fg_color="transparent",
                                     corner_radius=0)
        content_outer.pack(fill="both", expand=True)

        # Build sidebar nav
        self._build_macos_sidebar(sidebar_outer)

        # Build content pages (stacked)
        self._pages = {}

        p_dev = ctk.CTkFrame(content_outer, fg_color="transparent", corner_radius=0)
        p_dev.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._pages["devices"] = p_dev
        self._build_devices_page(p_dev)

        p_local = ctk.CTkFrame(content_outer, fg_color="transparent", corner_radius=0)
        p_local.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._pages["local"] = p_local
        self._build_local_tab(p_local)

        p_dec = ctk.CTkFrame(content_outer, fg_color="transparent", corner_radius=0)
        p_dec.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._pages["decrypt"] = p_dec
        self._build_decrypt_tab(p_dec)

        self._show_page("devices")
        self._start_spinner()

    def _on_resize(self, event):
        """Redraw gradient canvas on window resize."""
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 2 or h < 2:
            return
        self._grad_canvas.config(width=w, height=h)
        _draw_gradient(self._grad_canvas, w, h)

    # ─────────────────────────────────────────────────────────────────────────
    # macOS SOURCE-LIST SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_macos_sidebar(self, parent):
        # Section label — dark text on white sidebar
        ctk.CTkLabel(parent, text="PHANTOM",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=MUTED, anchor="w"
                     ).pack(fill="x", padx=16, pady=(20, 6))

        self._nav_btns = {}
        nav_items = [
            ("devices",  "📡",  "Devices"),
            ("local",    "📁",  "Local Files"),
            ("decrypt",  "🔓",  "Decrypt"),
        ]
        for key, icon, label in nav_items:
            btn = ctk.CTkButton(
                parent,
                text=f"  {icon}  {label}",
                font=ctk.CTkFont("Segoe UI", 13),
                anchor="w",
                fg_color="transparent",
                hover_color=BG_SURFACE,
                text_color=TEXT,
                height=36,
                corner_radius=8,
                command=lambda k=key: self._show_page(k))
            btn.pack(fill="x", padx=8, pady=2)
            self._nav_btns[key] = btn

        # Divider
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(10, 4))

        # Spacer
        ctk.CTkFrame(parent, fg_color="transparent").pack(fill="both", expand=True)

        # Bottom: connection status
        btm = ctk.CTkFrame(parent, fg_color="transparent")
        btm.pack(fill="x", padx=12, pady=(0, 16))

        self._status_dot = ctk.CTkLabel(btm, text="●",
                                         font=ctk.CTkFont("Segoe UI", 9),
                                         text_color=WARN)
        self._status_dot.pack(side="left", padx=(0, 5))

        self._detect_lbl = ctk.CTkLabel(btm, text="Not Connected",
                                         font=ctk.CTkFont("Segoe UI", 11),
                                         text_color=MUTED)
        self._detect_lbl.pack(side="left")

    def _show_page(self, key: str):
        self._active_page = key
        for k, p in self._pages.items():
            if k == key:
                p.lift()
            else:
                p.lower()
        # Highlight active nav item
        for k, btn in self._nav_btns.items():
            if k == key:
                btn.configure(fg_color=ACCENT, text_color="white",
                              hover_color=ACCENT_GLOW)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT,
                              hover_color=BG_SURFACE)
        # Trigger local refresh
        if key == "local":
            threading.Thread(target=self._refresh_local_tab, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # DEVICES PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_devices_page(self, parent):
        # Page header bar (white frosted glass)
        phdr = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=0, height=46)
        phdr.pack(fill="x")
        phdr.pack_propagate(False)
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x")

        ctk.CTkLabel(phdr, text="Devices",
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=TEXT, anchor="w"
                     ).pack(side="left", padx=20, pady=12)

        self._ip_lbl = ctk.CTkLabel(
            phdr, text="Connect to Phantom WiFi to begin",
            font=ctk.CTkFont("Segoe UI", 11), text_color=MUTED)
        self._ip_lbl.pack(side="right", padx=16)

        # Body — transparent to show gradient through
        body = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        # Action column — white frosted glass card
        action_col = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=12,
                                  border_color=BORDER, border_width=1,
                                  width=258)
        action_col.pack(side="left", fill="y", padx=(0, 10))
        action_col.pack_propagate(False)

        main_col = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        main_col.pack(side="left", fill="both", expand=True)

        self._build_sidebar(action_col)
        self._build_main(main_col)

    # ─────────────────────────────────────────────────────────────────────────
    # ACTION COLUMN (right of devices page sidebar)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        # ── Section: Send File ────────────────────────────────────────────────
        ctk.CTkLabel(parent, text="SEND",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=MUTED, anchor="w"
                     ).pack(fill="x", padx=16, pady=(16, 6))

        # File picker row
        file_row = ctk.CTkFrame(parent, fg_color="transparent")
        file_row.pack(fill="x", padx=12, pady=(0, 8))

        self._file_entry = ctk.CTkEntry(
            file_row,
            textvariable=self.wav_path,
            placeholder_text="Choose a file…",
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=BG_CARD,
            border_color=BORDER,
            border_width=1,
            text_color=TEXT,
            placeholder_text_color=MUTED,
            height=30,
            corner_radius=6)
        self._file_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(file_row, text="…",
                      width=30, height=30,
                      font=ctk.CTkFont("Segoe UI", 13),
                      fg_color=BG_CARD,
                      hover_color=BORDER,
                      border_color=BORDER,
                      border_width=1,
                      text_color=MUTED,
                      corner_radius=6,
                      command=self._browse
                      ).pack(side="right")

        self._upload_btn = ctk.CTkButton(
            parent,
            text="Upload to Device",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_GLOW,
            text_color="white",
            height=34,
            corner_radius=8,
            command=lambda: threading.Thread(
                target=self._upload_to_server, daemon=True).start())
        self._upload_btn.pack(fill="x", padx=12, pady=(0, 4))

        self._upload_pb = ctk.CTkProgressBar(
            parent, mode="indeterminate", height=2,
            progress_color=ACCENT, fg_color=BORDER, corner_radius=1)
        self._upload_pb.pack(fill="x", padx=12, pady=(0, 2))
        self._upload_pb.pack_forget()

        self._upload_result_lbl = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color=GREEN, anchor="w", wraplength=230)
        self._upload_result_lbl.pack(fill="x", padx=14, pady=(0, 4))

        # Divider
        ctk.CTkFrame(parent, fg_color=BORDER, height=1, corner_radius=0
                     ).pack(fill="x", padx=12, pady=(8, 0))

        # ── Section: Receive File ─────────────────────────────────────────────
        ctk.CTkLabel(parent, text="RECEIVE",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=MUTED, anchor="w"
                     ).pack(fill="x", padx=16, pady=(12, 4))

        self._dl_status_lbl = ctk.CTkLabel(
            parent, text="Awaiting connection…",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=MUTED, anchor="w", wraplength=230)
        self._dl_status_lbl.pack(fill="x", padx=14, pady=(0, 6))

        self._dl_pb = ctk.CTkProgressBar(
            parent, mode="indeterminate", height=2,
            progress_color=ACCENT, fg_color=BORDER, corner_radius=1)
        self._dl_pb.pack(fill="x", padx=12, pady=(0, 6))
        self._dl_pb.pack_forget()

        ctk.CTkButton(parent,
                      text="Open Downloads Folder",
                      font=ctk.CTkFont("Segoe UI", 11),
                      fg_color=BG_CARD,
                      hover_color=BORDER,
                      border_color=BORDER,
                      border_width=1,
                      text_color=MUTED,
                      height=30, corner_radius=6,
                      command=self._open_downloads
                      ).pack(fill="x", padx=12, pady=(0, 8))

        # Spacer pushes content up
        ctk.CTkFrame(parent, fg_color="transparent").pack(fill="both", expand=True)
        self._upload_section_lbl = ctk.CTkLabel(parent, text="", width=0, height=0)

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN CONTENT AREA
    # ─────────────────────────────────────────────────────────────────────────
    def _build_main(self, parent):
        # ── File list header ──────────────────────────────────────────────────
        fhdr = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, height=36)
        fhdr.pack(fill="x", pady=(0, 4))
        fhdr.pack_propagate(False)

        self._filelist_title = ctk.CTkLabel(
            fhdr, text="Remote Files",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color="white", anchor="w")
        self._filelist_title.pack(side="left", pady=8)

        for icon, tip, cmd in [
            ("↻",  "Refresh",  lambda: threading.Thread(target=self._fetch_filelist, daemon=True).start()),
            ("↓",  "Download", lambda: threading.Thread(target=self._download, args=("server",), daemon=True).start()),
            ("✕",  "Delete",   lambda: threading.Thread(target=self._delete_selected_file, daemon=True).start()),
        ]:
            ctk.CTkButton(fhdr, text=icon, width=28, height=26,
                          font=ctk.CTkFont("Segoe UI", 11),
                          fg_color="transparent",
                          hover_color="#7B4FAA",
                          text_color="white",
                          corner_radius=6,
                          command=cmd
                          ).pack(side="right", padx=1, pady=5)

        # ── File list table — white frosted glass card ─────────────────────
        table_wrap = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12,
                                  border_color=BORDER, border_width=1)
        table_wrap.pack(fill="x", pady=(0, 10))
        table_card = table_wrap

        col_hdr = tk.Frame(table_card, bg=BG_SURFACE)
        col_hdr.pack(fill="x", padx=1, pady=(1, 0))

        for txt, w, anchor in [
            ("Filename",   360, "w"),
            ("Size",       110, "center"),
            ("Duration",   100, "center"),
            ("",            80, "center"),
        ]:
            lbl = tk.Label(col_hdr, text=txt,
                           bg=BG_SURFACE, fg=MUTED,
                           font=("Segoe UI", 9),
                           padx=12 if anchor == "w" else 0,
                           anchor=anchor, width=0)
            lbl.pack(side="left", fill="x",
                     expand=(txt == "Filename"),
                     ipadx=6, ipady=5)

        self._rows_frame = tk.Frame(table_card, bg=BG_CARD)
        self._rows_frame.pack(fill="x", padx=1, pady=(0, 1))

        self._empty_lbl = tk.Label(self._rows_frame,
                                    text="No files found",
                                    bg=BG_CARD, fg=MUTED,
                                    font=("Segoe UI", 10),
                                    pady=24)
        self._empty_lbl.pack()

        # ── Activity Log header ───────────────────────────────────────────────
        log_hdr = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, height=34)
        log_hdr.pack(fill="x", pady=(8, 4))
        log_hdr.pack_propagate(False)

        ctk.CTkLabel(log_hdr, text="Activity Log",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color="white"
                     ).pack(side="left", pady=8)

        ctk.CTkButton(log_hdr, text="Clear", width=50, height=22,
                      font=ctk.CTkFont("Segoe UI", 10),
                      fg_color="transparent", hover_color="#7B4FAA",
                      text_color="white", corner_radius=6,
                      command=self._clear_log
                      ).pack(side="right", pady=6)

        # White frosted glass card for log
        log_wrap = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12,
                                border_color=BORDER, border_width=1)
        log_wrap.pack(fill="both", expand=True, pady=(0, 0))
        log_card = log_wrap

        self.log = tk.Text(
            log_card,
            font=("Consolas", 10),
            bg=BG_CARD, fg=TEXT,
            relief="flat", bd=0,
            insertbackground=TEXT,
            state="disabled", wrap="word",
            highlightthickness=0,
            selectbackground=BORDER,
            selectforeground=TEXT,
            padx=14, pady=10)
        self.log.pack(fill="both", expand=True, padx=2, pady=2)

        vsb = tk.Scrollbar(log_card, command=self.log.yview,
                            bg=BG_CARD, troughcolor=BG_CARD,
                            bd=0, highlightthickness=0, width=5)
        vsb.pack(side="right", fill="y", padx=(0, 3), pady=6)
        self.log.configure(yscrollcommand=vsb.set)

        self.log.tag_config("ok",     foreground=GREEN)
        self.log.tag_config("err",    foreground=RED)
        self.log.tag_config("info",   foreground=ACCENT)
        self.log.tag_config("warn",   foreground=WARN)
        self.log.tag_config("header", foreground=ACCENT,
                             font=("Consolas", 10, "bold"))
        self.log.tag_config("data",   foreground=GREEN)
        self.log.tag_config("prompt", foreground=SUBTLE)

    # ─────────────────────────────────────────────────────────────────────────
    # FILE ROWS
    # ─────────────────────────────────────────────────────────────────────────
    def _update_filelist_ui(self, files):
        for w in self._rows_frame.winfo_children():
            w.destroy()

        if not files:
            tk.Label(self._rows_frame, text="No files found",
                     bg=BG_CARD, fg=MUTED,
                     font=("Segoe UI", 10), pady=24
                     ).pack()
            self._statusbar_set("No files found")
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

            if sz >= 1024*1024: sz_str = f"{sz/1024/1024:.1f} MB"
            elif sz >= 1024:    sz_str = f"{sz/1024:.1f} KB"
            else:               sz_str = f"{sz} B"

            m = int(dur) // 60
            s = int(dur) % 60
            dur_str = f"{m:02d}:{s:02d}"

            row_bg = BG_ROW if i % 2 == 0 else BG_ROW_ALT

            # ── Icon by file type ─────────────────────────────────────────
            from pathlib import Path as _P
            icon_txt, icon_color = self._icon_for(_P(name))

            row = tk.Frame(self._rows_frame, bg=row_bg, cursor="hand2")
            row.pack(fill="x")

            tk.Frame(row, bg=BORDER, height=1).pack(fill="x")

            inner = tk.Frame(row, bg=row_bg)
            inner.pack(fill="x", padx=4, pady=2)

            icon_lbl = tk.Label(inner,
                                 text=icon_txt,
                                 bg=BG_SURFACE, fg=icon_color,
                                 font=("Segoe UI", 14),
                                 width=3, pady=8,
                                 relief="flat")
            icon_lbl.pack(side="left", padx=(8, 8), pady=4)

            tk.Label(inner, text=name,
                     bg=row_bg, fg=TEXT,
                     font=("Segoe UI", 10),
                     anchor="w"
                     ).pack(side="left", fill="x", expand=True)

            tk.Label(inner, text=sz_str,
                     bg=row_bg, fg=MUTED,
                     font=("Segoe UI", 10),
                     width=10, anchor="center"
                     ).pack(side="left", padx=4)

            tk.Label(inner, text=dur_str,
                     bg=row_bg, fg=MUTED,
                     font=("Segoe UI", 10),
                     width=7, anchor="center"
                     ).pack(side="left", padx=4)

            btn_frame = tk.Frame(inner, bg=row_bg)
            btn_frame.pack(side="right", padx=(0, 8))

            fname_cap = name

            dl_btn = tk.Label(btn_frame, text="↓",
                               bg=BG_SURFACE, fg=ACCENT,
                               font=("Segoe UI", 12),
                               width=3, pady=4,
                               relief="flat", cursor="hand2")
            dl_btn.pack(side="left", padx=2)
            dl_btn.bind("<Button-1>", lambda e, fn=fname_cap: threading.Thread(
                target=self._download_file,
                args=(fn, "client" if self._detected_node == 2 else "server"),
                daemon=True).start())
            dl_btn.bind("<Enter>", lambda e, b=dl_btn: b.configure(fg=ACCENT_GLOW))
            dl_btn.bind("<Leave>", lambda e, b=dl_btn: b.configure(fg=ACCENT))

            rm_btn = tk.Label(btn_frame, text="✕",
                               bg=BG_SURFACE, fg=MUTED,
                               font=("Segoe UI", 10),
                               width=3, pady=4,
                               relief="flat", cursor="hand2")
            rm_btn.pack(side="left", padx=2)
            rm_btn.bind("<Button-1>", lambda e, fn=fname_cap: threading.Thread(
                target=self._delete_file, args=(fn,), daemon=True).start())
            rm_btn.bind("<Enter>", lambda e, b=rm_btn: b.configure(fg=RED))
            rm_btn.bind("<Leave>", lambda e, b=rm_btn: b.configure(fg=MUTED))

            def on_enter(e, w=row, c=row_bg): w.configure(bg=BORDER)
            def on_leave(e, w=row, c=row_bg): w.configure(bg=c)
            row.bind("<Enter>", on_enter)
            row.bind("<Leave>", on_leave)

        count = len(files)
        self._statusbar_set(f"{count} file(s) on device")
        self._log(f"File list: {count} file(s)", "ok")

    # ─────────────────────────────────────────────────────────────────────────
    # SPINNER
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
        pass

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
            title="Select File to Upload",
            filetypes=[
                ("All Supported",
                 "*.wav *.mp3 *.ogg *.docx *.xlsx *.pdf *.jpg *.jpeg *.png *.gif *.bmp *.txt"),
                ("Audio Files",     "*.wav *.mp3 *.ogg"),
                ("Documents",       "*.docx *.xlsx *.pdf *.txt"),
                ("Images",          "*.jpg *.jpeg *.png *.gif *.bmp"),
                ("All Files",       "*.*"),
            ])
        if path: self.wav_path.set(path)

    def _browse_then_upload(self):
        path = filedialog.askopenfilename(
            title="Select File to Send",
            filetypes=[
                ("All Supported",
                 "*.wav *.mp3 *.ogg *.docx *.xlsx *.pdf *.jpg *.jpeg *.png *.gif *.bmp *.txt"),
                ("Audio Files",     "*.wav *.mp3 *.ogg"),
                ("Documents",       "*.docx *.xlsx *.pdf *.txt"),
                ("Images",          "*.jpg *.jpeg *.png *.gif *.bmp"),
                ("All Files",       "*.*"),
            ])
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
        # White frosted glass toast floating over gradient
        border_color = RED   if error else GREEN
        text_color   = RED   if error else GREEN
        toast = ctk.CTkFrame(self, fg_color="#FFFFFF",
                              corner_radius=22,
                              border_color=border_color,
                              border_width=1)
        toast.place(relx=0.5, y=68, anchor="n")
        ctk.CTkLabel(toast, text=msg,
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=text_color, padx=22, pady=10
                     ).pack()
        self.after(3000, toast.destroy)

    # ─────────────────────────────────────────────────────────────────────────
    # STATUS REFRESH
    # ─────────────────────────────────────────────────────────────────────────
    def _auto_refresh(self):
        threading.Thread(target=self._refresh_status, daemon=True).start()
        self.after(30_000, self._auto_refresh)

    def _refresh_status(self):
        def upd_a(online):
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
        pass

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
                            found = num
                            self.after(0, lambda n=num, i=ip: self._on_node_detected(n, i))
                            break
                except Exception:
                    pass
            if not found:
                _miss += 1
                if _miss >= _MISS_TH:
                    self.after(0, self._on_node_lost)
            else:
                _miss = 0
            time.sleep(5)

    def _on_node_detected(self, node: int, ip: str):
        dev_label = "Phantom 1" if node == 1 else "Phantom 2"
        self._detected_node = node
        self._detect_lbl.configure(text=dev_label, text_color=GREEN)
        self._status_dot.configure(text="●", text_color=GREEN)
        self._ip_lbl.configure(text=f"Connected  ·  {dev_label}  ·  {ip}")
        self._conn_lbl.configure(text=f"{dev_label} Online", text_color=GREEN)
        threading.Thread(target=self._fetch_filelist, daemon=True).start()

    def _on_node_lost(self):
        self._detected_node = 0
        self._detect_lbl.configure(text="Not Connected", text_color=TEXT)
        self._status_dot.configure(text="●", text_color=WARN)
        self._ip_lbl.configure(text="Connect to Phantom WiFi to begin")
        self._conn_lbl.configure(text="No device", text_color=MUTED)

    # ─────────────────────────────────────────────────────────────────────────
    # FILE LIST FETCH
    # ─────────────────────────────────────────────────────────────────────────
    def _fetch_filelist(self):
        node = self._detected_node
        if node == 0:
            self._log("No device connected", "warn")
            return
        ip  = "192.168.4.1" if node == 1 else "192.168.5.1"
        url = f"http://{ip}/file/list"
        dev_label = "Phantom 1" if node == 1 else "Phantom 2"
        self._log(f"Fetching file list from {dev_label}…", "header")
        d = http_get_json(url, timeout=6)
        if not d:
            self._log("Failed to fetch file list", "err")
            return
        files = d.get("files", [])
        spiffs_free = d.get("spiffs_free", 0)
        title = f"{dev_label}  ―  {len(files)} file(s)  •  {spiffs_free//1024} KB free"
        self.after(0, lambda: self._filelist_title.configure(text=title))
        self.after(0, lambda: self._update_filelist_ui(files))

    # ─────────────────────────────────────────────────────────────────────────
    # UPLOAD
    # ─────────────────────────────────────────────────────────────────────────
    def _upload_to_server(self):
        path = self.wav_path.get().strip()
        if not path:
            self._log("No file selected", "warn")
            self._show_toast("⚠  Please select a file first", error=True)
            return
        if not os.path.isfile(path):
            self._log(f"File not found: {path}", "err")
            return
        self._upload_to_server_do()

    def _upload_to_server_do(self):
        path = self.wav_path.get().strip()
        if not path or not os.path.isfile(path):
            return
        node = self._detected_node
        if node == 0:
            self._log("No device connected — cannot upload", "err")
            self._show_toast("⚠  No device connected", error=True)
            return

        filename = os.path.basename(path)
        host = "192.168.4.1" if node == 1 else "192.168.5.1"

        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            self._log(f"Read error: {e}", "err")
            return

        kb = len(data) / 1024
        dev_label = "Phantom 1" if node == 1 else "Phantom 2"
        self._log(f"Uploading '{filename}'  ({kb:.1f} KB)  → {dev_label}", "header")
        self._busy(True)
        self.after(0, lambda: self._upload_result_lbl.configure(text="Uploading…"))

        t0 = time.time()
        resp, sent = tcp_upload(host, SERVER_UPLOAD, "/file/upload", data, timeout=60, filename=filename)
        elapsed = time.time() - t0

        self._busy(False)
        if "error" in resp.lower() or sent < len(data):
            self._log(f"Upload FAILED: {resp[:80]}", "err")
            self.after(0, lambda: self._upload_result_lbl.configure(
                text="Upload failed", text_color=RED))
            self._show_toast("✗  Upload failed", error=True)
        else:
            sz_str = f"{kb:.1f} KB" if kb >= 1 else f"{len(data)} B"
            self._log(f"✓  Sent: '{filename}'  ({sz_str}  {elapsed:.1f}s)", "ok")
            self.after(0, lambda: self._upload_result_lbl.configure(
                text=f"✓  {filename}  ({sz_str})", text_color=GREEN))
            self._show_toast(f"✓  Uploaded: {filename}")
            threading.Thread(target=self._fetch_filelist, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # DOWNLOAD ALL
    # ─────────────────────────────────────────────────────────────────────────
    def _download(self, source="server"):
        node = self._detected_node
        if node == 0:
            self._log("No device connected", "warn")
            return
        ip  = "192.168.4.1" if node == 1 else "192.168.5.1"
        url = f"http://{ip}/file/list"
        d   = http_get_json(url, timeout=6)
        if not d:
            self._log("Cannot retrieve file list", "err")
            return
        files = d.get("files", [])
        if not files:
            self._log("No files on device", "warn")
            return
        for f in files:
            name = f.get("name", "")
            if name:
                threading.Thread(
                    target=self._download_file,
                    args=(name, source), daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # DOWNLOAD SINGLE FILE
    # ─────────────────────────────────────────────────────────────────────────
    def _download_file(self, filename, source="server"):
        node = self._detected_node
        host = "192.168.4.1" if node == 1 else (
               "192.168.5.1" if source == "client" else "192.168.4.1")

        self._log(f"Downloading '{filename}'…", "header")
        self.after(0, lambda: self._dl_status_lbl.configure(
            text=f"Downloading  {filename}…", text_color=ACCENT))
        self.after(0, lambda: [self._dl_pb.pack(fill="x", padx=12, pady=(0, 4)),
                               self._dl_pb.start()])

        t0   = time.time()
        data = http_download_file(host, SERVER_HTTP, filename, timeout=45)
        elapsed = time.time() - t0

        self.after(0, lambda: [self._dl_pb.stop(), self._dl_pb.pack_forget()])

        if not data:
            self._log(f"Download FAILED: '{filename}'", "err")
            self.after(0, lambda: self._dl_status_lbl.configure(
                text="Download failed", text_color=RED))
            self._show_toast(f"✗  Download failed: {filename}", error=True)
            return

        # Save to local folder
        DONGBO_DIR.mkdir(parents=True, exist_ok=True)
        save_name = filename
        abs_path  = DONGBO_DIR / save_name

        with open(abs_path, "wb") as fout:
            fout.write(data)

        # Optional: open in Explorer
        try:
            subprocess.run(
                ["explorer", "/select,", str(abs_path)],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

        kb = len(data) / 1024
        size_str = f"{kb:.0f} KB" if kb >= 1 else f"{len(data)} B"
        self._log(f"✓  Saved: {save_name}  ({size_str}  {elapsed:.1f}s)", "ok")
        self.after(0, lambda: self._dl_status_lbl.configure(
            text=f"✓  Saved: {save_name}  ({size_str})", text_color=GREEN))
        self._show_toast(f"✓  Downloaded: {save_name}")
        try: subprocess.Popen(f'explorer /select,"{abs_path}"')
        except: pass

    def _delete_selected_file(self):
        self._log("Select a file from the list to delete", "warn")

    def _delete_file(self, fname):
        safe = str(fname).lstrip("/")
        target = "192.168.4.1" if self._detected_node == 1 else "192.168.5.1"
        self._log(f"Deleting: {safe}…", "header")
        resp = http_post(target, 80, f"/file/delete?name={safe}")
        if resp and ("ok" in resp or "deleted" in resp) and "error" not in resp.lower():
            self._log(f"✓  Deleted: {safe}", "ok")
            self._show_toast(f"✓  Deleted: {safe}")
            self.after(500, self._refresh_filelist)
        else:
            self._log("Delete failed", "err")

    def _refresh_filelist(self):
        threading.Thread(target=self._fetch_filelist, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE: DECRYPT
    # ─────────────────────────────────────────────────────────────────────────
    def _build_decrypt_tab(self, parent):
        # ── Transparent header on gradient ────────────────────────────────────
        hdr = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, height=46)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="Decrypt",
                     font=ctk.CTkFont("Segoe UI", 17, "bold"),
                     text_color="white", anchor="w"
                     ).pack(side="left", padx=4, pady=12)

        ctk.CTkLabel(hdr,
                     text="AES-256-GCM · HMAC-SHA256 · ChaCha20",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color="#D4C8E8", anchor="e"
                     ).pack(side="right", padx=4)

        # ── Body: frosted glass cards on gradient ─────────────────────────────
        body = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True)

        # ── Left: form card ───────────────────────────────────────────────────
        form_card = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=14,
                                  border_color=BORDER, border_width=1, width=320)
        form_card.pack(side="left", fill="y", padx=(4, 8), pady=4)
        form_card.pack_propagate(False)

        ctk.CTkLabel(form_card, text="Input Files",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=MUTED, anchor="w"
                     ).pack(fill="x", padx=16, pady=(14, 6))

        # Helper — label + entry + browse button
        def _field(lbl_text, var, pick_cmd):
            ctk.CTkLabel(form_card, text=lbl_text,
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color=MUTED, anchor="w"
                         ).pack(fill="x", padx=16, pady=(6, 0))
            row = ctk.CTkFrame(form_card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(2, 0))
            e = ctk.CTkEntry(row, textvariable=var,
                             fg_color=BG_SURFACE, border_color=BORDER, border_width=1,
                             text_color=TEXT, placeholder_text_color=MUTED,
                             height=34, corner_radius=10,
                             font=ctk.CTkFont("Consolas", 9))
            e.pack(side="left", fill="x", expand=True, padx=(0, 6))
            ctk.CTkButton(row, text="…", width=34, height=34,
                          fg_color=BG_SURFACE, hover_color=BORDER,
                          border_color=BORDER, border_width=1,
                          text_color=MUTED, corner_radius=10,
                          command=pick_cmd).pack(side="right")

        self._dec_bin = ctk.StringVar()
        self._dec_key = ctk.StringVar()
        self._dec_out = ctk.StringVar()

        # Auto-fill phantom.key nếu tồn tại cạnh audio_gui.py
        _default_key = Path(__file__).parent / "decode" / "phantom.key"
        if _default_key.exists():
            self._dec_key.set(str(_default_key))
        _default_out = Path(__file__).parent / "decode" / "output"
        self._dec_out.set(str(_default_out))

        _field("File .bin (PHANTOM)",   self._dec_bin, self._dec_pick_bin)
        _field("Key file (phantom.key)", self._dec_key, self._dec_pick_key)
        _field("Output folder",          self._dec_out, self._dec_pick_out)

        ctk.CTkFrame(form_card, fg_color=BORDER, height=1, corner_radius=0
                     ).pack(fill="x", padx=16, pady=(16, 0))

        # ── Decrypt button ────────────────────────────────────────────────────
        self._dec_btn = ctk.CTkButton(
            form_card,
            text="Decrypt Now",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_GLOW,
            text_color="white",
            height=42, corner_radius=12,
            command=lambda: threading.Thread(
                target=self._dec_start, daemon=True).start(),
            state="normal" if _CRYPTO_OK else "disabled")
        self._dec_btn.pack(fill="x", padx=12, pady=(12, 4))

        self._dec_pb = ctk.CTkProgressBar(
            form_card, mode="indeterminate", height=2,
            progress_color=ACCENT, fg_color=BORDER, corner_radius=1)
        self._dec_pb.pack(fill="x", padx=12, pady=(0, 4))
        self._dec_pb.pack_forget()

        self._dec_status_lbl = ctk.CTkLabel(
            form_card, text="Ready" if _CRYPTO_OK else "⚠  pip install cryptography",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color=GREEN if _CRYPTO_OK else WARN,
            anchor="w", wraplength=280)
        self._dec_status_lbl.pack(fill="x", padx=16, pady=(0, 6))

        # ── Nút Open folder ───────────────────────────────────────────────────
        ctk.CTkButton(
            form_card,
            text="↗   Open Output Folder",
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=BG_SURFACE, hover_color=BORDER,
            border_color=BORDER, border_width=1,
            text_color=MUTED, height=36, corner_radius=10,
            command=self._dec_open_output
        ).pack(fill="x", padx=12, pady=(4, 12))

        if not _CRYPTO_OK:
            ctk.CTkLabel(form_card,
                         text="Run:\npip install cryptography",
                         font=ctk.CTkFont("Consolas", 9),
                         text_color=RED, anchor="w", justify="left"
                         ).pack(fill="x", padx=16, pady=(0, 10))

        # ── Right: log panel ──────────────────────────────────────────────────
        log_card = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=14,
                                border_color=BORDER, border_width=1)
        log_card.pack(side="left", fill="both", expand=True, padx=(0, 4), pady=4)

        ctk.CTkLabel(log_card, text="Log",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=TEXT, anchor="w"
                     ).pack(fill="x", padx=16, pady=(14, 4))

        # Dùng tk.Text thay vì CTkTextbox để hỗ trợ color tags
        log_outer = tk.Frame(log_card, bg=BG_SURFACE)
        log_outer.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._dec_log = tk.Text(
            log_outer, bg=BG_SURFACE, fg=TEXT,
            font=("Consolas", 9),
            relief="flat", bd=0,
            insertbackground=TEXT,
            state="disabled", wrap="word")
        vsb = tk.Scrollbar(log_outer, orient="vertical",
                           command=self._dec_log.yview,
                           bg=BG_SURFACE, troughcolor=BG_SURFACE, bd=0,
                           highlightthickness=0, width=5)
        self._dec_log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y", pady=4)
        self._dec_log.pack(side="left", fill="both", expand=True)

        # Color tags — iOS light palette
        self._dec_log.tag_config("ok",   foreground=GREEN)
        self._dec_log.tag_config("err",  foreground=RED)
        self._dec_log.tag_config("info", foreground=WARN)
        self._dec_log.tag_config("dim",  foreground=MUTED)
        self._dec_log.tag_config("head", foreground=ACCENT)

    # ── Decrypt helpers ───────────────────────────────────────────────────────
    def _dec_pick_bin(self):
        p = filedialog.askopenfilename(
            title="Select PHANTOM .bin file",
            filetypes=[("PHANTOM bin", "*.bin"), ("All files", "*.*")],
            initialdir=str(Path(__file__).parent / "decode"))
        if p:
            self._dec_bin.set(p)
            self._dec_out.set(str(Path(p).parent / "output"))

    def _dec_pick_key(self):
        p = filedialog.askopenfilename(
            title="Select phantom.key file",
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
                                "No files have been decrypted to this folder yet.")

    def _dec_log_msg(self, msg: str):
        """Thread-safe ghi log vào _dec_log với màu sắc."""
        def _append():
            self._dec_log.config(state="normal")
            if msg.startswith("    ✓") or msg.startswith("✔"):
                tag = "ok"
            elif msg.startswith("    ✗") or "Lỗi" in msg:
                tag = "err"
            elif msg.startswith("["):
                tag = "info"
            elif msg.startswith("─"):
                tag = "head"
            else:
                tag = "dim"
            self._dec_log.insert("end", msg + "\n", tag)
            self._dec_log.see("end")
            self._dec_log.config(state="disabled")
        self.after(0, _append)

    def _dec_log_file_link(self, out_path: str, size: int):
        """Thêm dòng clickable vào log — click mở file bằng Explorer."""
        def _append():
            self._dec_log.config(state="normal")
            fname = os.path.basename(out_path)
            sz_str = f"{size/1024:.1f} KB" if size >= 1024 else f"{size} B"
            label  = f"    📂  {fname}  ({sz_str})  ← click to open"
            # Tạo unique tag cho từng link
            link_tag = f"link_{id(out_path)}_{self._dec_log.index('end')}"
            self._dec_log.tag_config(link_tag,
                                          foreground=ACCENT,
                                          underline=True,
                                          font=("Consolas", 9))
            self._dec_log.tag_bind(link_tag, "<Button-1>",
                lambda e, p=out_path: subprocess.Popen(
                    f'explorer /select,"{p}"'))
            self._dec_log.tag_bind(link_tag, "<Enter>",
                lambda e: self._dec_log.config(cursor="hand2"))
            self._dec_log.tag_bind(link_tag, "<Leave>",
                lambda e: self._dec_log.config(cursor=""))
            self._dec_log.insert("end", label + "\n", link_tag)
            self._dec_log.see("end")
            self._dec_log.config(state="disabled")
        self.after(0, _append)

    def _dec_start(self):
        bin_p = self._dec_bin.get().strip()
        key_p = self._dec_key.get().strip()
        out_d = self._dec_out.get().strip()

        if not bin_p or not os.path.isfile(bin_p):
            self.after(0, lambda: self._dec_status_lbl.configure(
                text="⚠  No .bin file selected", text_color=WARN))
            return
        if not key_p or not os.path.isfile(key_p):
            self.after(0, lambda: self._dec_status_lbl.configure(
                text="⚠  No key file selected", text_color=WARN))
            return
        if not out_d:
            self.after(0, lambda: self._dec_status_lbl.configure(
                text="⚠  No output folder selected", text_color=WARN))
            return

        # Clear log
        self.after(0, lambda: [
            self._dec_log.config(state="normal"),
            self._dec_log.delete("1.0", "end"),
            self._dec_log.config(state="disabled")])

        self.after(0, lambda: self._dec_btn.configure(state="disabled"))
        self.after(0, lambda: [self._dec_pb.pack(fill="x", padx=12, pady=(0, 4)),
                                self._dec_pb.start()])
        self.after(0, lambda: self._dec_status_lbl.configure(
            text="Decrypting…", text_color=ACCENT))

        self._dec_log_msg(f"File .bin : {bin_p}")
        self._dec_log_msg(f"Key file  : {key_p}")
        self._dec_log_msg(f"Output    : {out_d}")
        self._dec_log_msg("─" * 55)

        try:
            results = _phtm_unpack(bin_p, key_p, out_d, log_cb=self._dec_log_msg)
            ok  = sum(1 for r in results if r[3])
            err = len(results) - ok
            self._dec_log_msg("─" * 55)
            # Render clickable link cho mỗi file thành công
            for (orig, out_path, size, success) in results:
                if success and out_path:
                    self._dec_log_file_link(out_path, size)
            if ok > 0:
                self._dec_log_msg("")
            self._dec_log_msg(f"✔  Done: {ok} file(s) OK,  {err} error(s)")
            self.after(0, lambda: self._dec_status_lbl.configure(
                text=f"✓  {ok}/{len(results)} file(s) decrypted successfully",
                text_color=GREEN))
            self._show_toast(f"✓  Decrypt: {ok} file(s) done")
            if ok > 0:
                # Refresh Local Storage tab nếu output nằm trong DONGBO_DIR
                if Path(out_d).resolve() == DONGBO_DIR.resolve():
                    threading.Thread(target=self._refresh_local_tab, daemon=True).start()
        except Exception as e:
            self._dec_log_msg(f"\n✗  ERROR: {e}")
            self.after(0, lambda: self._dec_status_lbl.configure(
                text=f"Error: {e}", text_color=RED))
            self._log(f"Decrypt ERROR: {e}", "err")
        finally:
            self.after(0, lambda: [self._dec_pb.stop(), self._dec_pb.pack_forget()])
            self.after(0, lambda: self._dec_btn.configure(state="normal"))

    # ─────────────────────────────────────────────────────────────────────────
    # TAB CALLBACK
    # ─────────────────────────────────────────────────────────────────────────
    def _on_tab_change(self):
        # Legacy stub — navigation now handled by _show_page()
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 2 — LOCAL STORAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_local_tab(self, parent):
        # ── Transparent header on gradient ────────────────────────────────────
        hdr = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, height=46)
        hdr.pack(fill="x", pady=(0, 0))
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="Local Files",
                     font=ctk.CTkFont("Segoe UI", 17, "bold"),
                     text_color="white", anchor="w"
                     ).pack(side="left", padx=4, pady=12)

        for icon, tip, cmd in [
            ("↻",  "Refresh",         lambda: threading.Thread(target=self._refresh_local_tab, daemon=True).start()),
            ("↗",  "Open Folder",     self._open_dongbo_folder),
            ("✕",  "Delete Selected", self._delete_local_selected),
        ]:
            ctk.CTkButton(hdr, text=icon, width=28, height=26,
                          font=ctk.CTkFont("Segoe UI", 11),
                          fg_color="transparent", hover_color="#7B4FAA",
                          text_color="white", corner_radius=6,
                          command=cmd
                          ).pack(side="right", padx=2, pady=10)

        # ── Stat bar ────────────────────────────────────────────────────────
        self._local_stat_lbl = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color="#D4C8E8", anchor="w")
        self._local_stat_lbl.pack(fill="x", padx=4, pady=(2, 4))

        # ── Table — white frosted glass ───────────────────────────────────────
        table_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=14,
                                  border_color=BORDER, border_width=1)
        table_card.pack(fill="both", expand=True, pady=(0, 0))

        col_hdr = tk.Frame(table_card, bg=BG_SURFACE)
        col_hdr.pack(fill="x", padx=1, pady=(1, 0))
        for txt, w, anchor in [
            ("Filename",    0,   "w"),
            ("Size",        90,  "center"),
            ("Modified",    130, "center"),
            ("",            80,  "center"),
        ]:
            tk.Label(col_hdr, text=txt,
                     bg=BG_SURFACE, fg=MUTED,
                     font=("Segoe UI", 9),
                     padx=12 if anchor == "w" else 0,
                     anchor=anchor, width=0
                     ).pack(side="left",
                            fill="x", expand=(txt == "Filename"),
                            ipadx=6, ipady=5)

        scroll_outer = tk.Frame(table_card, bg=BG_CARD)
        scroll_outer.pack(fill="both", expand=True, padx=1, pady=(0, 1))

        vsb = tk.Scrollbar(scroll_outer, orient="vertical",
                           bg=BG_CARD, troughcolor=BG_CARD,
                           bd=0, highlightthickness=0, width=5)
        vsb.pack(side="right", fill="y", padx=(0, 2), pady=4)

        self._local_canvas = tk.Canvas(
            scroll_outer, bg=BG_CARD,
            highlightthickness=0, bd=0,
            yscrollcommand=vsb.set)
        self._local_canvas.pack(side="left", fill="both", expand=True)
        vsb.configure(command=self._local_canvas.yview)

        self._local_rows = tk.Frame(self._local_canvas, bg=BG_CARD)
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

        self._local_empty_lbl = tk.Label(
            self._local_rows, text="No files in folder_test/",
            bg=BG_CARD, fg=MUTED,
            font=("Segoe UI", 10), pady=28)
        self._local_empty_lbl.pack()

        self._local_selected = set()
        threading.Thread(target=self._refresh_local_tab, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    def _refresh_local_tab(self):
        DONGBO_DIR.mkdir(parents=True, exist_ok=True)
        all_files = sorted(
            [p for p in DONGBO_DIR.iterdir()
             if p.is_file() and not p.name.startswith(".")],
            key=lambda p: p.stat().st_mtime, reverse=True)
        self.after(0, self._update_local_rows, all_files)

    @staticmethod
    def _icon_for(path: Path) -> tuple:
        ext = path.suffix.lower()
        if ext in (".wav", ".mp3", ".ogg", ".flac", ".aac"):
            return "♪", "#3b82f6"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            return "🖼", "#a855f7"
        if ext in (".docx", ".doc", ".odt"):
            return "📄", "#2563eb"
        if ext in (".xlsx", ".xls", ".csv"):
            return "📊", "#16a34a"
        if ext == ".pdf":
            return "📕", "#ef4444"
        if ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
            return "🗜", "#f59e0b"
        if ext in (".txt", ".md", ".log"):
            return "📝", "#94a3b8"
        return "📦", "#64748b"

    def _update_local_rows(self, all_files):
        for w in self._local_rows.winfo_children():
            w.destroy()
        self._local_selected.clear()

        if not all_files:
            tk.Label(self._local_rows,
                     text="No files in folder_test/",
                     bg=BG_CARD, fg=MUTED,
                     font=("Segoe UI", 10), pady=28).pack()
            self._local_stat_lbl.configure(text="No files")
            return

        total_kb = sum(p.stat().st_size for p in all_files) // 1024
        self._local_stat_lbl.configure(
            text=f"{len(all_files)} file(s)  ·  {total_kb} KB  ·  {DONGBO_DIR}")

        for i, p in enumerate(all_files):
            sz    = p.stat().st_size
            mtime = time.strftime("%m/%d  %H:%M", time.localtime(p.stat().st_mtime))
            sz_str = f"{sz/1024:.1f} KB" if sz < 1024*1024 else f"{sz/1024/1024:.2f} MB"
            row_bg = BG_ROW if i % 2 == 0 else BG_ROW_ALT
            icon, icon_color = self._icon_for(p)

            row = tk.Frame(self._local_rows, bg=row_bg, cursor="hand2")
            row.pack(fill="x")
            tk.Frame(row, bg=BORDER, height=1).pack(fill="x")

            inner = tk.Frame(row, bg=row_bg)
            inner.pack(fill="x", padx=4, pady=2)

            chk_lbl = tk.Label(inner, text="○",
                                bg=row_bg, fg=MUTED,
                                font=("Segoe UI", 13),
                                width=2, cursor="hand2")
            chk_lbl.pack(side="left", padx=(6, 0), pady=4)

            tk.Label(inner, text=icon,
                     bg=BG_SURFACE, fg=icon_color,
                     font=("Segoe UI", 13),
                     width=3, pady=8
                     ).pack(side="left", padx=(4, 8), pady=4)

            name_lbl = tk.Label(inner, text=p.name,
                                 bg=row_bg, fg=TEXT,
                                 font=("Segoe UI", 10),
                                 anchor="w")
            name_lbl.pack(side="left", fill="x", expand=True)

            tk.Label(inner, text=sz_str,
                     bg=row_bg, fg=MUTED,
                     font=("Segoe UI", 10),
                     width=9, anchor="center"
                     ).pack(side="left", padx=4)

            tk.Label(inner, text=mtime,
                     bg=row_bg, fg=SUBTLE,
                     font=("Segoe UI", 9),
                     width=12, anchor="center"
                     ).pack(side="left", padx=4)

            btn_frame = tk.Frame(inner, bg=row_bg)
            btn_frame.pack(side="right", padx=(0, 8))

            path_cap = p

            open_btn = tk.Label(btn_frame, text="↗",
                                 bg=row_bg, fg=ACCENT,
                                 font=("Segoe UI", 12),
                                 width=3, pady=4,
                                 relief="flat", cursor="hand2")
            open_btn.pack(side="left", padx=2)
            open_btn.bind("<Button-1>",
                lambda e, pp=path_cap: subprocess.Popen(
                    f'explorer /select,"{pp}"'))
            open_btn.bind("<Enter>", lambda e, b=open_btn: b.configure(fg=ACCENT_GLOW))
            open_btn.bind("<Leave>", lambda e, b=open_btn: b.configure(fg=ACCENT))

            del_btn = tk.Label(btn_frame, text="✕",
                                bg=row_bg, fg=MUTED,
                                font=("Segoe UI", 10),
                                width=3, pady=4,
                                relief="flat", cursor="hand2")
            del_btn.pack(side="left", padx=2)
            del_btn.bind("<Button-1>",
                lambda e, pp=path_cap: threading.Thread(
                    target=self._delete_local_file, args=(pp,), daemon=True).start())
            del_btn.bind("<Enter>", lambda e, b=del_btn: b.configure(fg=RED))
            del_btn.bind("<Leave>", lambda e, b=del_btn: b.configure(fg=MUTED))

            def _toggle(e, pp=path_cap, cl=chk_lbl):
                if pp.name in self._local_selected:
                    self._local_selected.discard(pp.name)
                    cl.configure(text="○", fg=MUTED)
                else:
                    self._local_selected.add(pp.name)
                    cl.configure(text="●", fg=ACCENT)

            chk_lbl.bind("<Button-1>", _toggle)
            name_lbl.bind("<Button-1>", _toggle)
            row.bind("<Button-1>", _toggle)

    # ─────────────────────────────────────────────────────────────────────────
    def _open_dongbo_folder(self):
        DONGBO_DIR.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(f'explorer "{DONGBO_DIR}"')
        except Exception as ex:
            self._log(f"Cannot open folder: {ex}", "err")

    def _delete_local_file(self, path: Path):
        try:
            path.unlink()
            self._log(f"✓  Deleted local: {path.name}", "ok")
            self._show_toast(f"✓  Deleted: {path.name}")
        except Exception as ex:
            self._log(f"Delete failed: {ex}", "err")
        threading.Thread(target=self._refresh_local_tab, daemon=True).start()

    def _delete_local_selected(self):
        if not self._local_selected:
            self._log("No files selected  (click filename or checkbox)", "warn")
            return
        names = list(self._local_selected)
        for name in names:
            p = DONGBO_DIR / name
            if p.exists():
                try:
                    p.unlink()
                    self._log(f"✓  Deleted: {name}", "ok")
                except Exception as ex:
                    self._log(f"Error deleting {name}: {ex}", "err")
        threading.Thread(target=self._refresh_local_tab, daemon=True).start()
        self._show_toast(f"✓  Deleted {len(names)} file(s)")

    # ─────────────────────────────────────────────────────────────────────────
    # AUTO-SYNC LIFECYCLE
    # ─────────────────────────────────────────────────────────────────────────
    def _start_auto_sync(self):
        try:
            script = Path(__file__).parent / "dongbo" / "auto_sync.py"
            if not script.exists():
                return
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            self._sync_proc = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs
            )
        except Exception as ex:
            print(f"[auto_sync] Failed to start: {ex}")

    def _stop_auto_sync(self):
        proc = self._sync_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        self._sync_proc = None

    def _on_close(self):
        self._stop_auto_sync()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
