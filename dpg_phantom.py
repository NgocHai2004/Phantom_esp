"""
dpg_phantom.py — Phantom File Transfer
Framework : Dear PyGui (GPU-accelerated)
Style     : Modern dark — macOS-inspired sidebar + content panels
Run       : .venv\Scripts\python dpg_phantom.py
"""

import dearpygui.dearpygui as dpg
import threading, socket, os, time, subprocess, json
import struct, zipfile, hashlib, io, urllib.request, urllib.error
from pathlib import Path
from tkinter import filedialog
import tkinter as tk
import sys

# ── PHANTOM 3-layer crypto ────────────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    from cryptography.hazmat.primitives import hmac as _hmac, hashes as _hashes
    from cryptography.hazmat.backends import default_backend as _backend
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

_PHTM_MAGIC, _PHTM_VER, _KEY_SZ = b"PHTM", 2, 32

def _load_key(p):
    d = open(p,"rb").read()
    if len(d) < _KEY_SZ: raise ValueError(f"Key too short ({len(d)} B)")
    return d[:_KEY_SZ]

def _derive(master):
    dk = lambda t: hashlib.sha256(master+t).digest()
    return dk(b"AES-GCM"), dk(b"HMAC-SHA256"), dk(b"CHACHA20")

def _decrypt3(enc, master):
    ka, kh, kc = _derive(master)
    p = ChaCha20Poly1305(kc).decrypt(enc[:12], enc[12:], None)
    tag, inner = p[-32:], p[:-32]
    h = _hmac.HMAC(kh, _hashes.SHA256(), backend=_backend())
    h.update(inner); h.verify(tag)
    return AESGCM(ka).decrypt(inner[:12], inner[12:], None)

def phtm_unpack(bin_path, key_path, out_dir, log_cb=None):
    log = log_cb or print
    raw = open(bin_path,"rb").read()
    if raw[:4] != _PHTM_MAGIC: raise ValueError("Not a PHANTOM file")
    ver = struct.unpack_from("<I", raw, 4)[0]
    if ver != _PHTM_VER: raise ValueError(f"Version {ver} unsupported")
    md5s = raw[8:24]
    plen = struct.unpack_from("<I", raw, 24)[0]
    pay  = raw[28:28+plen]
    if hashlib.md5(pay).digest() != md5s: raise ValueError("MD5 mismatch")
    log(f"OK  header  {plen:,} B  md5={md5s.hex()[:12]}…")
    master = _load_key(key_path)
    os.makedirs(out_dir, exist_ok=True)
    results = []
    with zipfile.ZipFile(io.BytesIO(pay)) as zf:
        entries = zf.namelist()
        log(f"OK  {len(entries)} file(s) in archive")
        for i, entry in enumerate(entries,1):
            orig = entry.removesuffix(".enc")
            log(f"[{i}/{len(entries)}] {orig}")
            try:
                plain = _decrypt3(zf.read(entry), master)
                out_p = os.path.join(out_dir, orig)
                open(out_p,"wb").write(plain)
                log(f"    OK  {orig}  ({len(plain):,} B)")
                results.append((orig, out_p, len(plain), True))
            except Exception as e:
                log(f"    ERR {e}")
                results.append((orig, None, 0, False))
    return results

# ── Paths & network constants ─────────────────────────────────────────────────
DONGBO_DIR    = Path(__file__).parent / "folder_test"
SERVER_IP     = "192.168.4.1"
SERVER_HTTP   = 80
SERVER_UPLOAD = 8081
CLIENT_IP     = "192.168.5.1"
CLIENT_HTTP   = 80

_MIME = {
    ".wav":"audio/wav",".mp3":"audio/mpeg",".ogg":"audio/ogg",
    ".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf":"application/pdf",".jpg":"image/jpeg",".jpeg":"image/jpeg",
    ".png":"image/png",".gif":"image/gif",".bmp":"image/bmp",".txt":"text/plain",
}
def _mime(fn): return _MIME.get(os.path.splitext(fn)[1].lower(), "application/octet-stream")
def _sfname(fn):
    import re; b,e = os.path.splitext(fn)
    b = re.sub(r'[^\w\-.]','_',b); b = re.sub(r'_+','_',b).strip('_')
    return (b or "file")+e.lower()

def tcp_upload(host, port, path, data, timeout=30, filename=""):
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host,port))
        sf = _sfname(filename) if filename else ""
        req = (f"POST {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Content-Type: {_mime(filename)}\r\nContent-Length: {len(data)}\r\n"
               +(f"X-Filename: {sf}\r\n" if sf else "")
               +"Connection: close\r\n\r\n").encode()
        s.sendall(req)
        sent=0
        while sent<len(data):
            s.sendall(data[sent:sent+4096]); sent+=min(4096,len(data)-sent)
        resp=b""
        s.settimeout(12)
        try:
            while True:
                c=s.recv(4096)
                if not c: break
                resp+=c
        except: pass
        return resp.decode(errors="replace"), sent
    except Exception as e: return f"ERROR: {e}", 0
    finally:
        try: s.close()
        except: pass

def http_download_file(host, port, filename, timeout=45):
    import urllib.parse
    s=socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host,port))
        enc=urllib.parse.quote(filename,safe=".-_")
        s.sendall(f"GET /file/download?name={enc} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        hbuf=b""; dl=time.time()+timeout
        while b"\r\n\r\n" not in hbuf and time.time()<dl:
            try: c=s.recv(512)
            except socket.timeout: break
            if not c: break
            hbuf+=c
        sep=hbuf.find(b"\r\n\r\n")
        if sep<0: return b""
        htxt=hbuf[:sep].decode(errors="replace")
        body=bytearray(hbuf[sep+4:])
        if " 200 " not in htxt.split("\r\n")[0]: return b""
        clen=-1
        for line in htxt.split("\r\n")[1:]:
            if line.lower().startswith("content-length:"):
                try: clen=int(line.split(":",1)[1].strip())
                except: pass
        s.settimeout(timeout)
        while (clen<0 or len(body)<clen) and time.time()<dl:
            try:
                c=s.recv(4096)
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
        s=socket.socket(); s.settimeout(timeout)
        s.connect((host,port))
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        d=b""
        try:
            while True:
                c=s.recv(4096)
                if not c: break
                d+=c
        except: pass
        s.close()
        idx=d.find(b"\r\n\r\n")
        return d[idx+4:].decode(errors="replace") if idx>=0 else ""
    except: return ""

def http_get_json(url, timeout=4):
    try:
        req=urllib.request.Request(url, headers={"User-Agent":"PhantomDPG/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except: return None

def http_post(host, port, path, timeout=6):
    try:
        s=socket.socket(); s.settimeout(timeout)
        s.connect((host,port))
        s.sendall(f"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        d=b""
        try:
            while True:
                c=s.recv(4096)
                if not c: break
                d+=c
        except: pass
        s.close()
        idx=d.find(b"\r\n\r\n")
        return d[idx+4:].decode(errors="replace") if idx>=0 else ""
    except: return ""

# ── File-picker (tkinter hidden root) ────────────────────────────────────────
def pick_file(title="Select file", filetypes=None, initialdir=None):
    root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
    path = filedialog.askopenfilename(title=title,
                                      filetypes=filetypes or [("All","*.*")],
                                      initialdir=initialdir)
    root.destroy()
    return path

def pick_dir(title="Select folder", initialdir=None):
    root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
    path = filedialog.askdirectory(title=title, initialdir=initialdir)
    root.destroy()
    return path

# ── DPG color helpers ─────────────────────────────────────────────────────────
def hex2rgba(h, a=255):
    h = h.lstrip("#")
    r,g,b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
    return (r,g,b,a)

# ── Palette ───────────────────────────────────────────────────────────────────
C_BG       = hex2rgba("#0C0C0E")
C_PANEL    = hex2rgba("#111115")
C_CARD     = hex2rgba("#16161C")
C_SURFACE  = hex2rgba("#1C1C24")
C_BORDER   = hex2rgba("#2A2A35")
C_NAV      = hex2rgba("#0A0A0D")

C_BLUE     = hex2rgba("#0A84FF")
C_BLUE_D   = hex2rgba("#0060D0")
C_GREEN    = hex2rgba("#30D158")
C_RED      = hex2rgba("#FF453A")
C_ORANGE   = hex2rgba("#FF9F0A")
C_TEAL     = hex2rgba("#5AC8FA")
C_PURPLE   = hex2rgba("#BF5AF2")

C_TEXT     = hex2rgba("#F0F0F5")
C_TEXT2    = hex2rgba("#98989F")
C_TEXT3    = hex2rgba("#48484F")
C_ACTIVE   = hex2rgba("#0A84FF", 40)

W = 1140
H = 720
NAV_W = 64
SB_W  = 268

# ══════════════════════════════════════════════════════════════════════════════
# APPLICATION STATE
# ══════════════════════════════════════════════════════════════════════════════
class State:
    detected_node : int   = 0
    active_page   : str   = "devices"
    file_list     : list  = []
    local_files   : list  = []
    upload_path   : str   = ""
    dec_bin       : str   = ""
    dec_key       : str   = ""
    dec_out       : str   = str(Path(__file__).parent / "decode" / "output")
    dec_running   : bool  = False
    spinning      : bool  = True
    spin_frame    : int   = 0

S = State()

# ── Auto-fill key if exists ───────────────────────────────────────────────────
_default_key = Path(__file__).parent / "decode" / "phantom.key"
if _default_key.exists():
    S.dec_key = str(_default_key)

# ══════════════════════════════════════════════════════════════════════════════
# DPG TAG REGISTRY (centralised string IDs)
# ══════════════════════════════════════════════════════════════════════════════
T = type("T", (), {
    # windows / viewports
    "win_main"        : "win_main",
    # pages
    "page_devices"    : "page_devices",
    "page_local"      : "page_local",
    "page_decrypt"    : "page_decrypt",
    # nav buttons
    "nav_devices"     : "nav_devices",
    "nav_local"       : "nav_local",
    "nav_decrypt"     : "nav_decrypt",
    # connection
    "lbl_conn"        : "lbl_conn",
    "lbl_dot"         : "lbl_dot",
    "lbl_ip"          : "lbl_ip",
    # upload
    "inp_file"        : "inp_file",
    "btn_upload"      : "btn_upload",
    "lbl_upload_res"  : "lbl_upload_res",
    "bar_upload"      : "bar_upload",
    # download
    "lbl_dl_status"   : "lbl_dl_status",
    "bar_dl"          : "bar_dl",
    # file list
    "lbl_filelist_hdr": "lbl_filelist_hdr",
    "table_files"     : "table_files",
    # log
    "log_text"        : "log_text",
    # local
    "lbl_local_stat"  : "lbl_local_stat",
    "table_local"     : "table_local",
    # decrypt
    "inp_dec_bin"     : "inp_dec_bin",
    "inp_dec_key"     : "inp_dec_key",
    "inp_dec_out"     : "inp_dec_out",
    "lbl_dec_status"  : "lbl_dec_status",
    "log_dec"         : "log_dec",
    "btn_dec"         : "btn_dec",
    "bar_dec_l1"      : "bar_dec_l1",
    "bar_dec_l2"      : "bar_dec_l2",
    "bar_dec_l3"      : "bar_dec_l3",
    "bar_dec_global"  : "bar_dec_global",
    "lbl_dec_l1"      : "lbl_dec_l1",
    "lbl_dec_l2"      : "lbl_dec_l2",
    "lbl_dec_l3"      : "lbl_dec_l3",
    # toast
    "toast_win"       : "toast_win",
    "toast_lbl"       : "toast_lbl",
})()

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _log(msg, color=None):
    """Append a line to the activity log."""
    if not dpg.does_item_exist(T.log_text):
        return
    try:
        cur = dpg.get_value(T.log_text) or ""
        line = f"› {msg}\n"
        dpg.set_value(T.log_text, cur + line)
        # auto-scroll: move caret to end via configure
    except Exception:
        pass

def _dec_log(msg):
    if not dpg.does_item_exist(T.log_dec):
        return
    try:
        cur = dpg.get_value(T.log_dec) or ""
        dpg.set_value(T.log_dec, cur + msg + "\n")
    except Exception:
        pass

def _show_toast(msg, ok=True):
    color = C_GREEN if ok else C_RED
    try:
        if dpg.does_item_exist(T.toast_win):
            dpg.delete_item(T.toast_win)
        vw = dpg.get_viewport_width()
        with dpg.window(tag=T.toast_win, no_title_bar=True,
                        no_resize=True, no_move=True,
                        no_scrollbar=True, no_close=True,
                        pos=(vw//2 - 200, 56),
                        width=400, height=46,
                        no_background=False):
            with dpg.theme() as tw:
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvThemeCol_WindowBg,
                                        hex2rgba("#111111"), category=dpg.mvThemeCat_Core)
                    dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 12,
                                        category=dpg.mvThemeCat_Core)
                    dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 10,
                                        category=dpg.mvThemeCat_Core)
            dpg.bind_item_theme(T.toast_win, tw)
            dpg.add_text(msg, color=color, tag=T.toast_lbl)
        # auto-hide after 3 s
        def _hide():
            time.sleep(3)
            try:
                if dpg.does_item_exist(T.toast_win):
                    dpg.delete_item(T.toast_win)
            except Exception:
                pass
        threading.Thread(target=_hide, daemon=True).start()
    except Exception:
        pass

def _icon_for(name):
    e = Path(name).suffix.lower()
    if e in (".wav",".mp3",".ogg",".flac",".aac"): return "♪"
    if e in (".png",".jpg",".jpeg",".gif",".bmp",".webp"): return "⬛"
    if e in (".docx",".doc",".odt"): return "📄"
    if e in (".xlsx",".xls",".csv"): return "📊"
    if e == ".pdf": return "📕"
    if e in (".zip",".rar",".7z",".tar",".gz"): return "🗜"
    if e in (".txt",".md",".log"): return "📝"
    if e == ".bin": return "⚙"
    return "📦"

def _sz(n):
    if n >= 1048576: return f"{n/1048576:.1f} MB"
    if n >= 1024:    return f"{n/1024:.1f} KB"
    return f"{n} B"

# ══════════════════════════════════════════════════════════════════════════════
# THEME SETUP
# ══════════════════════════════════════════════════════════════════════════════
def _setup_theme():
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            # Base colors
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,     C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,      C_CARD)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,      C_CARD)
            dpg.add_theme_color(dpg.mvThemeCol_Border,       C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,      C_SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, hex2rgba("#22222C"))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,  hex2rgba("#28283A"))
            # Text
            dpg.add_theme_color(dpg.mvThemeCol_Text,         C_TEXT)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, C_TEXT3)
            # Buttons
            dpg.add_theme_color(dpg.mvThemeCol_Button,       C_SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,hex2rgba("#22222C"))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, hex2rgba("#0A84FF",60))
            # Headers (table)
            dpg.add_theme_color(dpg.mvThemeCol_Header,       hex2rgba("#0A84FF",40))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,hex2rgba("#0A84FF",60))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, hex2rgba("#0A84FF",80))
            # Scrollbar
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,  C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,C_SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, C_BORDER)
            # Separators
            dpg.add_theme_color(dpg.mvThemeCol_Separator,    C_BORDER)
            # Title
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,      C_NAV)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, C_NAV)
            # Tab
            dpg.add_theme_color(dpg.mvThemeCol_Tab,          C_SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered,   hex2rgba("#0A84FF",60))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive,    C_BLUE)
            # Progress
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram,C_BLUE)
            # Check
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark,    C_BLUE)
            # Styles
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,    10)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,     8)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,     6)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding,      4)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding,       6)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize,     6)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,       8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,     14, 12)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,      10, 6)
    dpg.bind_theme(global_theme)


def _blue_btn_theme():
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,       C_BLUE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,C_BLUE_D)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, C_BLUE_D)
            dpg.add_theme_color(dpg.mvThemeCol_Text,         (255,255,255,255))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
    return t

def _ghost_btn_theme():
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,       C_SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Text,         C_TEXT2)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
    return t

def _nav_active_theme():
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,       hex2rgba("#0A84FF",50))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,hex2rgba("#0A84FF",70))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, hex2rgba("#0A84FF",90))
            dpg.add_theme_color(dpg.mvThemeCol_Text,         C_BLUE)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 10)
    return t

def _nav_idle_theme():
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,       (0,0,0,0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,hex2rgba("#1C1C24"))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, hex2rgba("#22222C"))
            dpg.add_theme_color(dpg.mvThemeCol_Text,         C_TEXT2)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 10)
    return t

# ══════════════════════════════════════════════════════════════════════════════
# PAGE SWITCHING
# ══════════════════════════════════════════════════════════════════════════════
_PAGES   = ["devices", "local", "decrypt"]
_NAV_TAG = {"devices": T.nav_devices, "local": T.nav_local, "decrypt": T.nav_decrypt}
_PAGE_TAG= {"devices": T.page_devices,"local": T.page_local,"decrypt": T.page_decrypt}
_NAV_ACTIVE = {}
_NAV_IDLE   = {}

def _init_nav_themes():
    _NAV_ACTIVE["t"] = _nav_active_theme()
    _NAV_IDLE["t"]   = _nav_idle_theme()

def _show_page(key):
    S.active_page = key
    for k in _PAGES:
        try:
            if dpg.does_item_exist(_PAGE_TAG[k]):
                dpg.configure_item(_PAGE_TAG[k], show=(k == key))
        except Exception:
            pass
        try:
            if dpg.does_item_exist(_NAV_TAG[k]):
                th = _NAV_ACTIVE["t"] if k == key else _NAV_IDLE["t"]
                dpg.bind_item_theme(_NAV_TAG[k], th)
        except Exception:
            pass
    if key == "local":
        threading.Thread(target=_refresh_local, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# NAV RAIL
# ══════════════════════════════════════════════════════════════════════════════
def _build_nav():
    with dpg.child_window(width=NAV_W, height=-1, border=False,
                          tag="nav_rail",
                          no_scrollbar=True):
        with dpg.theme() as nav_bg:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, C_NAV)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 2)
        dpg.bind_item_theme("nav_rail", nav_bg)

        dpg.add_spacer(height=16)

        # App icon — styled button as logo
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=8)
            logo_btn = dpg.add_button(label="⇅", width=48, height=36)
            with dpg.theme() as logo_theme:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button,       C_BLUE)
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, C_BLUE_D)
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  C_BLUE_D)
                    dpg.add_theme_color(dpg.mvThemeCol_Text,          (255,255,255,255))
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 10)
            dpg.bind_item_theme(logo_btn, logo_theme)

        dpg.add_spacer(height=12)
        dpg.add_separator()
        dpg.add_spacer(height=12)

        # Nav items: (key, icon, tooltip)
        _nav_items = [
            ("devices", "📡", "Devices"),
            ("local",   "📁", "Local Files"),
            ("decrypt", "🔓", "Decrypt"),
        ]
        for key, icon, tip in _nav_items:
            tag = _NAV_TAG[key]
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=8)
                dpg.add_button(label=icon, tag=tag,
                               width=48, height=48,
                               callback=lambda s, a, k=key: _show_page(k))
                if dpg.does_item_exist("tooltip_"+key):
                    pass
                else:
                    with dpg.tooltip(tag):
                        dpg.add_text(tip)

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=12)

        # Connection dot
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=20)
            dpg.add_text("●", tag=T.lbl_dot, color=C_ORANGE)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=14)

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR (devices action panel)
# ══════════════════════════════════════════════════════════════════════════════
def _build_sidebar():
    with dpg.child_window(width=SB_W, height=-1, border=False,
                          tag="sidebar",
                          no_scrollbar=False):
        with dpg.theme() as sb_bg:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, C_PANEL)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 12)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 6)
        dpg.bind_item_theme("sidebar", sb_bg)

        # ── Connection card ────────────────────────────────────────────────
        dpg.add_text("CONNECTION", color=C_TEXT3)
        dpg.add_spacer(height=4)
        with dpg.child_window(width=-1, height=86, border=True, tag="card_conn"):
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text("●", tag=T.lbl_dot+"2", color=C_ORANGE)
                dpg.add_spacer(width=6)
                dpg.add_text("Not Connected", tag=T.lbl_conn,
                             color=C_TEXT2)
            dpg.add_text("Connect to Phantom WiFi",
                         tag=T.lbl_ip, color=C_TEXT3)

        dpg.add_spacer(height=10)

        # ── Send file card ─────────────────────────────────────────────────
        dpg.add_text("SEND FILE", color=C_TEXT3)
        dpg.add_spacer(height=4)
        with dpg.child_window(width=-1, height=130, border=True, tag="card_send"):
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag=T.inp_file, width=162,
                                   hint="Select a file…",
                                   readonly=True)
                dpg.add_spacer(width=4)
                b = dpg.add_button(label="…", width=36, height=28,
                                   callback=_cb_browse)
                dpg.bind_item_theme(b, _ghost_btn_theme())

            dpg.add_spacer(height=6)
            b2 = dpg.add_button(label="  ↑  Upload File",
                                tag=T.btn_upload,
                                width=-1, height=36,
                                callback=lambda: threading.Thread(
                                    target=_do_upload, daemon=True).start())
            dpg.bind_item_theme(b2, _blue_btn_theme())

            dpg.add_spacer(height=4)
            dpg.add_progress_bar(tag=T.bar_upload, default_value=0.0,
                                 width=-1, height=3)
            dpg.configure_item(T.bar_upload, show=False)
            dpg.add_text("", tag=T.lbl_upload_res, color=C_TEXT3, wrap=220)

        dpg.add_spacer(height=10)

        # ── Receive card ───────────────────────────────────────────────────
        dpg.add_text("RECEIVE FILE", color=C_TEXT3)
        dpg.add_spacer(height=4)
        with dpg.child_window(width=-1, height=110, border=True, tag="card_recv"):
            dpg.add_spacer(height=4)
            dpg.add_text("Awaiting connection…",
                         tag=T.lbl_dl_status, color=C_TEXT3, wrap=220)
            dpg.add_spacer(height=6)
            dpg.add_progress_bar(tag=T.bar_dl, default_value=0.0,
                                 width=-1, height=3)
            dpg.configure_item(T.bar_dl, show=False)
            dpg.add_spacer(height=6)
            b3 = dpg.add_button(label="  ↗  Open Downloads",
                                width=-1, height=32,
                                callback=_cb_open_downloads)
            dpg.bind_item_theme(b3, _ghost_btn_theme())

# ══════════════════════════════════════════════════════════════════════════════
# DEVICES PAGE (file list + activity log)
# ══════════════════════════════════════════════════════════════════════════════
def _build_devices_page(parent):
    with dpg.group(parent=parent):
        # Header bar
        with dpg.group(horizontal=True):
            dpg.add_text("Remote Files", tag=T.lbl_filelist_hdr,
                         color=C_TEXT)
            dpg.add_spacer(width=8)
            b_ref = dpg.add_button(label="↻ Refresh", width=80, height=26,
                                   callback=lambda: threading.Thread(
                                       target=_fetch_filelist, daemon=True).start())
            dpg.bind_item_theme(b_ref, _ghost_btn_theme())
            b_dl = dpg.add_button(label="↓ Download All", width=110, height=26,
                                  callback=lambda: threading.Thread(
                                      target=_download_all, daemon=True).start())
            dpg.bind_item_theme(b_dl, _ghost_btn_theme())

        dpg.add_spacer(height=6)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # File table
        with dpg.table(tag=T.table_files,
                       header_row=True,
                       borders_innerH=True,
                       borders_outerH=True,
                       borders_outerV=True,
                       row_background=True,
                       resizable=True,
                       height=240):
            dpg.add_table_column(label="  File", width_stretch=True,
                                 init_width_or_weight=0.55)
            dpg.add_table_column(label="Size",   width_fixed=True, init_width_or_weight=80)
            dpg.add_table_column(label="Dur.",   width_fixed=True, init_width_or_weight=60)
            dpg.add_table_column(label="",       width_fixed=True, init_width_or_weight=80)

        dpg.add_spacer(height=12)

        # Activity log header
        with dpg.group(horizontal=True):
            dpg.add_text("Activity Log", color=C_TEXT)
            dpg.add_spacer(width=8)
            b_clr = dpg.add_button(label="Clear", width=50, height=22,
                                   callback=lambda: dpg.set_value(T.log_text,""))
            dpg.bind_item_theme(b_clr, _ghost_btn_theme())

        dpg.add_spacer(height=4)

        with dpg.theme() as log_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, hex2rgba("#0A0A0D"))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
        log_inp = dpg.add_input_text(tag=T.log_text,
                                     multiline=True,
                                     readonly=True,
                                     width=-1,
                                     height=-1,
                                     default_value="")
        dpg.bind_item_theme(log_inp, log_theme)

# ══════════════════════════════════════════════════════════════════════════════
# LOCAL PAGE
# ══════════════════════════════════════════════════════════════════════════════
def _build_local_page(parent):
    with dpg.group(parent=parent):
        with dpg.group(horizontal=True):
            dpg.add_text("Local Files", color=C_TEXT)
            dpg.add_text("", tag=T.lbl_local_stat, color=C_TEXT3)
            dpg.add_spacer(width=8)
            b_ref = dpg.add_button(label="↻ Refresh", width=80, height=26,
                                   callback=lambda: threading.Thread(
                                       target=_refresh_local, daemon=True).start())
            dpg.bind_item_theme(b_ref, _ghost_btn_theme())
            b_fol = dpg.add_button(label="↗ Folder", width=80, height=26,
                                   callback=_cb_open_local_folder)
            dpg.bind_item_theme(b_fol, _ghost_btn_theme())

        dpg.add_spacer(height=6)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        with dpg.table(tag=T.table_local,
                       header_row=True,
                       borders_innerH=True,
                       borders_outerH=True,
                       borders_outerV=True,
                       row_background=True,
                       resizable=True,
                       height=-1):
            dpg.add_table_column(label="  File", width_stretch=True,
                                 init_width_or_weight=0.55)
            dpg.add_table_column(label="Size",   width_fixed=True, init_width_or_weight=80)
            dpg.add_table_column(label="Modified", width_fixed=True, init_width_or_weight=110)
            dpg.add_table_column(label="",         width_fixed=True, init_width_or_weight=80)

# ══════════════════════════════════════════════════════════════════════════════
# DECRYPT PAGE
# ══════════════════════════════════════════════════════════════════════════════
def _build_decrypt_page(parent):
    with dpg.group(parent=parent):
        dpg.add_text("Decrypt", color=C_TEXT)
        dpg.add_text("AES-256-GCM · HMAC-SHA256 · ChaCha20-Poly1305",
                     color=C_TEXT3)
        dpg.add_spacer(height=6)
        dpg.add_separator()
        dpg.add_spacer(height=10)

        with dpg.group(horizontal=True):
            # ── Left: form ─────────────────────────────────────────────────
            with dpg.child_window(width=300, height=-1, border=True,
                                  tag="dec_form"):
                dpg.add_text("INPUT FILES", color=C_TEXT3)
                dpg.add_spacer(height=6)

                # bin file
                dpg.add_text("PHANTOM .bin file", color=C_TEXT2)
                with dpg.group(horizontal=True):
                    dpg.add_input_text(tag=T.inp_dec_bin, width=200,
                                       hint="phantom_*.bin",
                                       default_value=S.dec_bin,
                                       readonly=True)
                    b = dpg.add_button(label="…", width=36, height=28,
                                       callback=_cb_dec_pick_bin)
                    dpg.bind_item_theme(b, _ghost_btn_theme())

                dpg.add_spacer(height=8)

                # key file
                dpg.add_text("Key file (phantom.key)", color=C_TEXT2)
                with dpg.group(horizontal=True):
                    dpg.add_input_text(tag=T.inp_dec_key, width=200,
                                       hint="phantom.key",
                                       default_value=S.dec_key,
                                       readonly=True)
                    b = dpg.add_button(label="…", width=36, height=28,
                                       callback=_cb_dec_pick_key)
                    dpg.bind_item_theme(b, _ghost_btn_theme())

                dpg.add_spacer(height=8)

                # output folder
                dpg.add_text("Output folder", color=C_TEXT2)
                with dpg.group(horizontal=True):
                    dpg.add_input_text(tag=T.inp_dec_out, width=200,
                                       default_value=S.dec_out,
                                       readonly=True)
                    b = dpg.add_button(label="…", width=36, height=28,
                                       callback=_cb_dec_pick_out)
                    dpg.bind_item_theme(b, _ghost_btn_theme())

                dpg.add_spacer(height=16)
                dpg.add_separator()
                dpg.add_spacer(height=12)

                # Layer progress bars
                _layers = [
                    (T.bar_dec_l1, T.lbl_dec_l1, "ChaCha20-Poly1305", C_TEAL),
                    (T.bar_dec_l2, T.lbl_dec_l2, "HMAC-SHA256",       C_ORANGE),
                    (T.bar_dec_l3, T.lbl_dec_l3, "AES-256-GCM",       C_PURPLE),
                ]
                for bar_tag, lbl_tag, name, color in _layers:
                    with dpg.group(horizontal=True):
                        dpg.add_text(f"L{_layers.index((bar_tag,lbl_tag,name,color))+1}", color=color)
                        dpg.add_spacer(width=4)
                        dpg.add_text(name, color=C_TEXT2)
                    with dpg.theme() as pbar_theme:
                        with dpg.theme_component(dpg.mvAll):
                            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram,
                                                color)
                    pb = dpg.add_progress_bar(tag=bar_tag, default_value=0.0,
                                              width=-1, height=6)
                    dpg.bind_item_theme(pb, pbar_theme)
                    dpg.add_text("0%", tag=lbl_tag, color=color)
                    dpg.add_spacer(height=6)

                dpg.add_spacer(height=4)
                dpg.add_text("TOTAL", color=C_TEXT3)
                with dpg.theme() as gbar_theme:
                    with dpg.theme_component(dpg.mvAll):
                        dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, C_BLUE)
                dpg.add_progress_bar(tag=T.bar_dec_global, default_value=0.0,
                                     width=-1, height=8)
                dpg.bind_item_theme(T.bar_dec_global, gbar_theme)

                dpg.add_spacer(height=12)

                b_dec = dpg.add_button(
                    label="  🔓  Decrypt Now",
                    tag=T.btn_dec,
                    width=-1, height=42,
                    callback=lambda: threading.Thread(
                        target=_do_decrypt, daemon=True).start(),
                    enabled=_CRYPTO_OK)
                dpg.bind_item_theme(b_dec, _blue_btn_theme())

                dpg.add_spacer(height=6)
                dpg.add_text("Ready" if _CRYPTO_OK else "⚠  pip install cryptography",
                             tag=T.lbl_dec_status,
                             color=C_GREEN if _CRYPTO_OK else C_ORANGE,
                             wrap=260)

                dpg.add_spacer(height=8)
                b_fol = dpg.add_button(label="  ↗  Open Output Folder",
                                       width=-1, height=32,
                                       callback=_cb_dec_open_output)
                dpg.bind_item_theme(b_fol, _ghost_btn_theme())

            dpg.add_spacer(width=12)

            # ── Right: log ─────────────────────────────────────────────────
            with dpg.child_window(width=-1, height=-1, border=True,
                                  tag="dec_log_panel"):
                with dpg.group(horizontal=True):
                    dpg.add_text("Log", color=C_TEXT)
                    dpg.add_spacer(width=8)
                    b_clr = dpg.add_button(label="Clear", width=50, height=22,
                                           callback=_cb_dec_clear_log)
                    dpg.bind_item_theme(b_clr, _ghost_btn_theme())

                dpg.add_spacer(height=4)

                with dpg.theme() as dec_log_theme:
                    with dpg.theme_component(dpg.mvAll):
                        dpg.add_theme_color(dpg.mvThemeCol_FrameBg,
                                            hex2rgba("#07070A"))
                        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_input_text(tag=T.log_dec,
                                   multiline=True,
                                   readonly=True,
                                   width=-1,
                                   height=-1,
                                   default_value="")
                dpg.bind_item_theme(T.log_dec, dec_log_theme)

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACKS — file browse / open
# ══════════════════════════════════════════════════════════════════════════════
def _cb_browse():
    p = pick_file("Select File to Upload",
                  filetypes=[("Supported","*.wav *.mp3 *.ogg *.docx *.xlsx *.pdf *.jpg *.jpeg *.png *.txt *.bin"),
                              ("All Files","*.*")])
    if p:
        S.upload_path = p
        dpg.set_value(T.inp_file, os.path.basename(p))

def _cb_open_downloads():
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    try: subprocess.Popen(f'explorer "{dl}"')
    except: pass

def _cb_open_local_folder():
    DONGBO_DIR.mkdir(parents=True, exist_ok=True)
    try: subprocess.Popen(f'explorer "{DONGBO_DIR}"')
    except: pass

def _cb_dec_pick_bin():
    p = pick_file("Select PHANTOM .bin",
                  filetypes=[("PHANTOM bin","*.bin"),("All","*.*")],
                  initialdir=str(Path(__file__).parent / "decode"))
    if p:
        S.dec_bin = p
        dpg.set_value(T.inp_dec_bin, os.path.basename(p))
        auto_out = str(Path(p).parent / "output")
        S.dec_out = auto_out
        dpg.set_value(T.inp_dec_out, auto_out)

def _cb_dec_pick_key():
    p = pick_file("Select phantom.key",
                  filetypes=[("Key","*.key"),("All","*.*")],
                  initialdir=str(Path(__file__).parent / "decode"))
    if p:
        S.dec_key = p
        dpg.set_value(T.inp_dec_key, os.path.basename(p))

def _cb_dec_pick_out():
    p = pick_dir("Select output folder", initialdir=S.dec_out)
    if p:
        S.dec_out = p
        dpg.set_value(T.inp_dec_out, p)

def _cb_dec_open_output():
    d = S.dec_out
    if os.path.isdir(d):
        try: subprocess.Popen(f'explorer "{d}"')
        except: pass

def _cb_dec_clear_log():
    dpg.set_value(T.log_dec, "")
    for bar, lbl, pct in [(T.bar_dec_l1, T.lbl_dec_l1, "0%"),
                          (T.bar_dec_l2, T.lbl_dec_l2, "0%"),
                          (T.bar_dec_l3, T.lbl_dec_l3, "0%")]:
        dpg.set_value(bar, 0.0)
        dpg.set_value(lbl, pct)
    dpg.set_value(T.bar_dec_global, 0.0)

# ══════════════════════════════════════════════════════════════════════════════
# FILE LIST (remote)
# ══════════════════════════════════════════════════════════════════════════════
def _update_filelist_ui(files):
    try:
        dpg.delete_item(T.table_files, children_only=True, slot=1)
    except Exception:
        pass
    S.file_list = files
    if not files:
        with dpg.table_row(parent=T.table_files):
            dpg.add_text("  No files on device", color=C_TEXT3)
            dpg.add_text("")
            dpg.add_text("")
            dpg.add_text("")
        return
    for f in files:
        name = f.get("name","?")
        sz   = f.get("size",0)
        try: sz = int(sz)
        except: sz=0
        dur = f.get("duration_sec",0)
        try: dur = float(dur)
        except: dur=0.0
        dur_s = f"{int(dur)//60:02d}:{int(dur)%60:02d}"
        icon = _icon_for(name)
        with dpg.table_row(parent=T.table_files):
            dpg.add_text(f"  {icon}  {name}")
            dpg.add_text(_sz(sz), color=C_TEXT2)
            dpg.add_text(dur_s,   color=C_TEXT2)
            with dpg.group(horizontal=True):
                fn_cap = name
                b_dl = dpg.add_button(label="↓", width=28, height=22,
                                      callback=lambda s,a,fn=fn_cap:
                                          threading.Thread(target=_download_file,
                                                           args=(fn,), daemon=True).start())
                dpg.bind_item_theme(b_dl, _ghost_btn_theme())
                dpg.add_spacer(width=4)
                b_rm = dpg.add_button(label="✕", width=28, height=22,
                                      callback=lambda s,a,fn=fn_cap:
                                          threading.Thread(target=_delete_file,
                                                           args=(fn,), daemon=True).start())
                dpg.bind_item_theme(b_rm, _ghost_btn_theme())

def _fetch_filelist():
    node = S.detected_node
    if node == 0:
        _log("No device connected", ); return
    ip  = "192.168.4.1" if node==1 else "192.168.5.1"
    lbl = "Phantom 1"   if node==1 else "Phantom 2"
    _log(f"Fetching file list from {lbl}…")
    d = http_get_json(f"http://{ip}/file/list", timeout=6)
    if not d:
        _log("Failed to fetch file list"); return
    files = d.get("files",[])
    free  = d.get("spiffs_free",0)
    title = f"Remote Files  ·  {lbl}  ·  {len(files)} file(s)  ·  {free//1024} KB free"
    try:
        dpg.set_value(T.lbl_filelist_hdr, title)
    except Exception:
        pass
    _update_filelist_ui(files)
    _log(f"OK  {len(files)} file(s)")

# ══════════════════════════════════════════════════════════════════════════════
# LOCAL FILES
# ══════════════════════════════════════════════════════════════════════════════
def _refresh_local():
    DONGBO_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [p for p in DONGBO_DIR.iterdir()
         if p.is_file() and not p.name.startswith(".")],
        key=lambda p: p.stat().st_mtime, reverse=True)
    S.local_files = files
    try:
        dpg.delete_item(T.table_local, children_only=True, slot=1)
    except Exception:
        pass
    total_kb = sum(p.stat().st_size for p in files)//1024
    try:
        dpg.set_value(T.lbl_local_stat,
                      f"  {len(files)} file(s)  ·  {total_kb} KB" if files else "  No files")
    except Exception:
        pass
    if not files:
        with dpg.table_row(parent=T.table_local):
            dpg.add_text("  No files in folder_test/", color=C_TEXT3)
            dpg.add_text(""); dpg.add_text(""); dpg.add_text("")
        return
    for p in files:
        sz   = p.stat().st_size
        mt   = time.strftime("%m/%d %H:%M", time.localtime(p.stat().st_mtime))
        icon = _icon_for(p.name)
        with dpg.table_row(parent=T.table_local):
            dpg.add_text(f"  {icon}  {p.name}")
            dpg.add_text(_sz(sz), color=C_TEXT2)
            dpg.add_text(mt,      color=C_TEXT3)
            pp = p
            with dpg.group(horizontal=True):
                b_op = dpg.add_button(label="↗", width=28, height=22,
                                      callback=lambda s,a,x=pp:
                                          subprocess.Popen(f'explorer /select,"{x}"'))
                dpg.bind_item_theme(b_op, _ghost_btn_theme())
                dpg.add_spacer(width=4)
                b_del = dpg.add_button(label="✕", width=28, height=22,
                                       callback=lambda s,a,x=pp:
                                           threading.Thread(target=_delete_local,
                                                            args=(x,), daemon=True).start())
                dpg.bind_item_theme(b_del, _ghost_btn_theme())

def _delete_local(path: Path):
    try:
        path.unlink()
        _log(f"Deleted: {path.name}")
        _show_toast(f"✓  Deleted: {path.name}")
    except Exception as e:
        _log(f"Delete failed: {e}")
    _refresh_local()

# ══════════════════════════════════════════════════════════════════════════════
# UPLOAD
# ══════════════════════════════════════════════════════════════════════════════
def _do_upload():
    path = S.upload_path
    if not path or not os.path.isfile(path):
        _log("No file selected")
        _show_toast("⚠  Select a file first", ok=False); return
    node = S.detected_node
    if node == 0:
        _log("No device connected")
        _show_toast("⚠  No device connected", ok=False); return
    filename = os.path.basename(path)
    host = "192.168.4.1" if node==1 else "192.168.5.1"
    try:
        data = open(path,"rb").read()
    except Exception as e:
        _log(f"Read error: {e}"); return
    kb  = len(data)/1024
    lbl = "Phantom 1" if node==1 else "Phantom 2"
    _log(f"Uploading '{filename}'  ({kb:.1f} KB)  → {lbl}")
    try:
        dpg.configure_item(T.bar_upload, show=True)
        dpg.set_value(T.bar_upload, 0.3)
        dpg.set_value(T.lbl_upload_res, "Uploading…")
    except Exception: pass
    t0 = time.time()
    resp, sent = tcp_upload(host, SERVER_UPLOAD, "/file/upload",
                            data, timeout=60, filename=filename)
    elapsed = time.time()-t0
    try:
        dpg.configure_item(T.bar_upload, show=False)
    except Exception: pass
    sz = f"{kb:.1f} KB" if kb>=1 else f"{len(data)} B"
    if "error" in resp.lower() or sent<len(data):
        _log(f"Upload FAILED: {resp[:80]}")
        try: dpg.set_value(T.lbl_upload_res, "Upload failed")
        except: pass
        _show_toast("✗  Upload failed", ok=False)
    else:
        _log(f"OK  Sent: '{filename}'  ({sz}  {elapsed:.1f}s)")
        try: dpg.set_value(T.lbl_upload_res, f"✓  {filename}  ({sz})")
        except: pass
        _show_toast(f"✓  Uploaded: {filename}")
        threading.Thread(target=_fetch_filelist, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════
def _download_file(filename, source="server"):
    node = S.detected_node
    host = "192.168.4.1" if node==1 else "192.168.5.1"
    _log(f"Downloading '{filename}'…")
    try:
        dpg.set_value(T.lbl_dl_status, f"Downloading  {filename}…")
        dpg.configure_item(T.bar_dl, show=True)
        dpg.set_value(T.bar_dl, 0.3)
    except Exception: pass
    t0   = time.time()
    data = http_download_file(host, SERVER_HTTP, filename, timeout=45)
    elapsed = time.time()-t0
    try:
        dpg.configure_item(T.bar_dl, show=False)
    except Exception: pass
    if not data:
        _log(f"Download FAILED: '{filename}'")
        try: dpg.set_value(T.lbl_dl_status, "Download failed")
        except: pass
        _show_toast(f"✗  Failed: {filename}", ok=False); return
    DONGBO_DIR.mkdir(parents=True, exist_ok=True)
    abs_path = DONGBO_DIR / filename
    open(abs_path,"wb").write(data)
    sz = _sz(len(data))
    _log(f"OK  Saved: {filename}  ({sz}  {elapsed:.1f}s)")
    try: dpg.set_value(T.lbl_dl_status, f"✓  {filename}  ({sz})")
    except: pass
    _show_toast(f"✓  Downloaded: {filename}")
    try: subprocess.Popen(f'explorer /select,"{abs_path}"')
    except: pass

def _download_all():
    node = S.detected_node
    if node == 0:
        _log("No device connected"); return
    ip = "192.168.4.1" if node==1 else "192.168.5.1"
    d  = http_get_json(f"http://{ip}/file/list", timeout=6)
    if not d:
        _log("Cannot retrieve file list"); return
    for f in d.get("files",[]):
        nm = f.get("name","")
        if nm:
            threading.Thread(target=_download_file, args=(nm,), daemon=True).start()

def _delete_file(fname):
    node = S.detected_node
    host = "192.168.4.1" if node==1 else "192.168.5.1"
    _log(f"Deleting: {fname}…")
    resp = http_post(host, 80, f"/file/delete?name={fname.lstrip('/')}")
    if resp and ("ok" in resp or "deleted" in resp) and "error" not in resp.lower():
        _log(f"OK  Deleted: {fname}")
        _show_toast(f"✓  Deleted: {fname}")
        threading.Thread(target=_fetch_filelist, daemon=True).start()
    else:
        _log("Delete failed")

# ══════════════════════════════════════════════════════════════════════════════
# NODE AUTO-DETECT
# ══════════════════════════════════════════════════════════════════════════════
def _poll_detect():
    ips = [("192.168.4.1",1), ("192.168.5.1",2)]
    miss = 0; MISS_TH = 3
    while True:
        found = 0
        for ip, num in ips:
            try:
                req = urllib.request.Request(f"http://{ip}/status",
                                             headers={"User-Agent":"PhantomDPG/1.0"})
                with urllib.request.urlopen(req, timeout=2) as r:
                    d = json.loads(r.read().decode())
                    if d.get("node") == num:
                        found = num
                        _on_node_detected(num, ip)
                        break
            except Exception:
                pass
        if not found:
            miss += 1
            if miss >= MISS_TH:
                _on_node_lost()
        else:
            miss = 0
        time.sleep(5)

def _on_node_detected(node, ip):
    S.detected_node = node
    label = "Phantom 1" if node==1 else "Phantom 2"
    try:
        dpg.set_value(T.lbl_dot,    "●")
        dpg.configure_item(T.lbl_dot,    color=C_GREEN)
        dpg.configure_item(T.lbl_dot+"2",color=C_GREEN)
        dpg.set_value(T.lbl_conn,   f"{label} ● ONLINE")
        dpg.configure_item(T.lbl_conn, color=C_GREEN)
        dpg.set_value(T.lbl_ip,     f"{label}  ·  {ip}")
    except Exception: pass
    threading.Thread(target=_fetch_filelist, daemon=True).start()

def _on_node_lost():
    S.detected_node = 0
    try:
        dpg.configure_item(T.lbl_dot,    color=C_ORANGE)
        dpg.configure_item(T.lbl_dot+"2",color=C_ORANGE)
        dpg.set_value(T.lbl_conn,   "Not Connected")
        dpg.configure_item(T.lbl_conn, color=C_TEXT3)
        dpg.set_value(T.lbl_ip,     "Connect to Phantom WiFi")
    except Exception: pass

# ══════════════════════════════════════════════════════════════════════════════
# DECRYPT
# ══════════════════════════════════════════════════════════════════════════════
def _do_decrypt():
    if S.dec_running: return
    bin_p = S.dec_bin
    key_p = S.dec_key
    out_d = S.dec_out
    if not bin_p or not os.path.isfile(bin_p):
        try: dpg.set_value(T.lbl_dec_status, "⚠  NO .BIN FILE")
        except: pass
        return
    if not key_p or not os.path.isfile(key_p):
        try: dpg.set_value(T.lbl_dec_status, "⚠  NO KEY FILE")
        except: pass
        return
    if not out_d:
        try: dpg.set_value(T.lbl_dec_status, "⚠  NO OUTPUT FOLDER")
        except: pass
        return

    S.dec_running = True
    try:
        dpg.configure_item(T.btn_dec, enabled=False)
        dpg.set_value(T.lbl_dec_status, "RUNNING…")
        dpg.configure_item(T.lbl_dec_status, color=C_TEAL)
    except Exception: pass

    # Reset bars
    for bar, lbl in [(T.bar_dec_l1, T.lbl_dec_l1),
                     (T.bar_dec_l2, T.lbl_dec_l2),
                     (T.bar_dec_l3, T.lbl_dec_l3)]:
        try:
            dpg.set_value(bar, 0.0)
            dpg.set_value(lbl, "0%")
        except: pass
    try: dpg.set_value(T.bar_dec_global, 0.0)
    except: pass
    try: dpg.set_value(T.log_dec, "")
    except: pass

    _dec_log(f"TARGET  : {os.path.basename(bin_p)}")
    _dec_log(f"KEY     : {os.path.basename(key_p)}")
    _dec_log(f"OUTPUT  : {out_d}")
    _dec_log("─" * 48)

    def _anim_bar(bar_tag, lbl_tag, steps=40, duration=5.0, start_g=0.0):
        """Animate a layer progress bar."""
        for i in range(steps+1):
            frac = i/steps
            g    = start_g + frac/3.0
            try:
                dpg.set_value(bar_tag, frac)
                dpg.set_value(lbl_tag, f"{int(frac*100)}%")
                dpg.set_value(T.bar_dec_global, g)
            except Exception: pass
            time.sleep(duration/steps)

    try:
        raw = open(bin_p,"rb").read()
        if raw[:4] != _PHTM_MAGIC: raise ValueError("Not a PHANTOM file")
        ver = struct.unpack_from("<I",raw,4)[0]
        if ver != _PHTM_VER: raise ValueError(f"Version {ver} unsupported")
        md5s = raw[8:24]; plen = struct.unpack_from("<I",raw,24)[0]
        pay  = raw[28:28+plen]
        if hashlib.md5(pay).digest() != md5s: raise ValueError("MD5 mismatch")
        master = _load_key(key_p)
        k_aes, k_hmac, k_chacha = _derive(master)
        h_chacha = hashlib.sha256(k_chacha).hexdigest()
        h_hmac   = hashlib.sha256(k_hmac  ).hexdigest()
        h_aes    = hashlib.sha256(k_aes   ).hexdigest()
    except Exception as e:
        _dec_log(f"ERROR: {e}")
        try:
            dpg.set_value(T.lbl_dec_status, f"ERROR: {e}")
            dpg.configure_item(T.lbl_dec_status, color=C_RED)
            dpg.configure_item(T.btn_dec, enabled=True)
        except Exception: pass
        S.dec_running = False
        return

    _dec_log(f"Header OK  |  {plen:,} bytes  |  MD5={md5s.hex()[:12]}…")

    # Layer 1 — ChaCha20
    _dec_log(f"\n[L1] ChaCha20-Poly1305  key={h_chacha[:16]}…")
    _anim_bar(T.bar_dec_l1, T.lbl_dec_l1, steps=40, duration=4.0, start_g=0.0)

    # Layer 2 — HMAC
    _dec_log(f"\n[L2] HMAC-SHA256        key={h_hmac[:16]}…")
    _anim_bar(T.bar_dec_l2, T.lbl_dec_l2, steps=40, duration=4.0, start_g=1/3)

    # Layer 3 — AES-GCM
    _dec_log(f"\n[L3] AES-256-GCM        key={h_aes[:16]}…")
    _anim_bar(T.bar_dec_l3, T.lbl_dec_l3, steps=40, duration=4.0, start_g=2/3)

    _dec_log("\n[OUTPUT]  Writing files…")
    os.makedirs(out_d, exist_ok=True)
    results = []
    try:
        with zipfile.ZipFile(io.BytesIO(pay)) as zf:
            entries = zf.namelist()
            _dec_log(f"  Archive: {len(entries)} file(s)")
            for i, entry in enumerate(entries,1):
                orig = entry.removesuffix(".enc")
                _dec_log(f"  [{i}/{len(entries)}] {orig}")
                try:
                    plain = _decrypt3(zf.read(entry), master)
                    out_p = os.path.join(out_d, orig)
                    open(out_p,"wb").write(plain)
                    _dec_log(f"  OK  {orig}  ({len(plain):,} B)")
                    results.append((orig, out_p, len(plain), True))
                except Exception as e2:
                    _dec_log(f"  ERR {e2}")
                    results.append((orig, None, 0, False))
    except Exception as e:
        _dec_log(f"UNPACK ERROR: {e}")
        try:
            dpg.set_value(T.lbl_dec_status, f"ERROR: {e}")
            dpg.configure_item(T.lbl_dec_status, color=C_RED)
            dpg.configure_item(T.btn_dec, enabled=True)
        except Exception: pass
        S.dec_running = False
        return

    ok  = sum(1 for r in results if r[3])
    err = len(results) - ok
    _dec_log("─"*48)
    _dec_log(f"DONE  {ok} OK  ·  {err} ERR")

    try:
        dpg.set_value(T.bar_dec_global, 1.0)
        dpg.set_value(T.lbl_dec_status, f"DONE  {ok}/{len(results)} DECRYPTED")
        dpg.configure_item(T.lbl_dec_status, color=C_GREEN)
        dpg.configure_item(T.btn_dec, enabled=True)
    except Exception: pass

    _show_toast(f"✓  Decrypt: {ok} file(s) done")
    if ok and Path(out_d).resolve() == DONGBO_DIR.resolve():
        _refresh_local()
    S.dec_running = False

# ══════════════════════════════════════════════════════════════════════════════
# SPINNER (connection scanning animation)
# ══════════════════════════════════════════════════════════════════════════════
_SPIN = ["◌","◍","●","◍"]

def _tick_spinner():
    while S.spinning:
        if S.detected_node == 0:
            frame = _SPIN[S.spin_frame % len(_SPIN)]
            try:
                dpg.set_value(T.lbl_conn, frame + " Scanning…")
                dpg.configure_item(T.lbl_conn, color=C_TEXT3)
            except Exception:
                pass
            S.spin_frame += 1
        time.sleep(0.35)

# ══════════════════════════════════════════════════════════════════════════════
# AUTO-REFRESH
# ══════════════════════════════════════════════════════════════════════════════
def _auto_refresh():
    while True:
        time.sleep(30)
        if S.detected_node != 0:
            _fetch_filelist()

# ══════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW BUILDER
# ══════════════════════════════════════════════════════════════════════════════
def build_ui():
    with dpg.window(tag=T.win_main, label="Phantom Transfer",
                    no_title_bar=True,
                    no_resize=True,
                    no_move=True,
                    no_close=True,
                    no_scrollbar=True,
                    width=W, height=H,
                    pos=(0,0)):

        with dpg.theme() as win_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, C_BG)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 0)
        dpg.bind_item_theme(T.win_main, win_theme)

        with dpg.group(horizontal=True):
            # ── 1. Nav rail ──────────────────────────────────────────────────
            _build_nav()

            # ── 2. Sidebar (action panel, only visible on devices page) ──────
            _build_sidebar()

            # ── 3. Content area ──────────────────────────────────────────────
            content_w = W - NAV_W - SB_W
            with dpg.child_window(width=content_w, height=-1, border=False,
                                  tag="content_area",
                                  no_scrollbar=True):
                with dpg.theme() as content_theme:
                    with dpg.theme_component(dpg.mvAll):
                        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, C_BG)
                        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 14)
                        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
                dpg.bind_item_theme("content_area", content_theme)

                # Page: Devices
                with dpg.child_window(tag=T.page_devices,
                                      width=-1, height=-1, border=False,
                                      show=True):
                    _build_devices_page(T.page_devices)

                # Page: Local
                with dpg.child_window(tag=T.page_local,
                                      width=-1, height=-1, border=False,
                                      show=False):
                    _build_local_page(T.page_local)

                # Page: Decrypt
                with dpg.child_window(tag=T.page_decrypt,
                                      width=-1, height=-1, border=False,
                                      show=False):
                    _build_decrypt_page(T.page_decrypt)

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main():
    dpg.create_context()
    _setup_theme()
    _init_nav_themes()

    dpg.create_viewport(
        title="Phantom Transfer",
        width=W, height=H,
        resizable=True,
        min_width=860, min_height=540,
        small_icon="", large_icon="",
        always_on_top=False,
        decorated=True,
        clear_color=C_BG[:3]+(255,),
    )

    build_ui()

    dpg.set_primary_window(T.win_main, True)
    dpg.setup_dearpygui()
    dpg.show_viewport()

    # Apply initial nav active state
    _show_page("devices")

    # Background threads
    threading.Thread(target=_poll_detect, daemon=True).start()
    threading.Thread(target=_tick_spinner, daemon=True).start()
    threading.Thread(target=_auto_refresh, daemon=True).start()
    threading.Thread(target=_refresh_local, daemon=True).start()

    # Viewport resize callback — keep primary window full size
    def _on_resize():
        vw = dpg.get_viewport_width()
        vh = dpg.get_viewport_height()
        try:
            dpg.set_item_width(T.win_main, vw)
            dpg.set_item_height(T.win_main, vh)
        except Exception:
            pass

    dpg.set_viewport_resize_callback(_on_resize)

    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()

    S.spinning = False
    dpg.destroy_context()


if __name__ == "__main__":
    main()
