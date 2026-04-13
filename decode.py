"""
decode.py — PHANTOM Secure File Decrypt
Style: macOS Finder Light — clean white + blue accent (HIG)
Run:   .venv\Scripts\python decode.py
"""

import customtkinter as ctk
from tkinter import filedialog
import sys, os, time, subprocess, threading
import struct, zipfile, hashlib, io
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
C_TEAL      = "#5AC8FA"    # ChaCha20 layer accent
C_VIOLET    = "#AF52DE"    # purple layer accent

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

# legacy alias
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
        self.title("PHANTOM — Decrypt")
        self.geometry("1200x760")
        self.minsize(960, 620)
        self.configure(fg_color=C_BG)

        self._dec_bin = ctk.StringVar()
        self._dec_bin_full = ""           # actual full path — never displayed
        self._dec_key = ctk.StringVar()   # display only (blank)
        self._dec_key_path = ""           # actual full path — never displayed
        self._dec_out = ctk.StringVar()
        self._dec_out_full = str(Path(__file__).parent / "decode" / "output")  # real path

        _def_key = Path(__file__).parent / "decode" / "phantom.key"
        if _def_key.exists():
            self._dec_key_path = str(_def_key)
            # Leave _dec_key display empty — key loaded silently
        self._dec_out.set("decode/output")   # display label only — no absolute path

        self._build_ui()

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

        content = ctk.CTkFrame(body, fg_color=C_BG, corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew")

        self._build_sidebar(sb)
        self._build_content(content)

    # ── TITLE BAR ─────────────────────────────────────────────────────────────
    def _build_titlebar(self):
        bar = ctk.CTkFrame(self, fg_color=C_PANEL, height=46, corner_radius=0)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Center — app title (teal accent color for decrypt)
        center = ctk.CTkFrame(bar, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(center, text="PHANTOM — Decrypt",
                     font=_font(14, "bold"), text_color=C_TEXT).pack()

        # Bottom border
        ctk.CTkFrame(self, fg_color=C_BORDER, height=1, corner_radius=0).pack(fill="x")

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        import tkinter as _tk
        sc = ctk.CTkFrame(parent, fg_color=C_PANEL, corner_radius=0)
        sc.pack(fill="both", expand=True)

        # ── FAVORITES ─────────────────────────────────────────────────────────
        tg_sec(sc, "Favorites").pack(fill="x", padx=16, pady=(10, 3))
        nav_outer = ctk.CTkFrame(sc, fg_color=C_WHITE, corner_radius=8, height=36)
        nav_outer.pack(fill="x", padx=8, pady=(0, 4))
        nav_outer.pack_propagate(False)
        ctk.CTkFrame(nav_outer, fg_color=C_BLUE, width=3, height=36,
                     corner_radius=0).pack(side="left")
        ctk.CTkLabel(nav_outer, text="🔓  Decrypt",
                     font=_font(13, "bold"), text_color=C_TEXT,
                     anchor="w").pack(side="left", padx=10)

        hr(sc, pady=4)

        # ── INPUT FILE (.bin) ─────────────────────────────────────────────────
        tg_sec(sc, "Input File").pack(fill="x", padx=16, pady=(4, 2))

        _bz_browse = lambda e: self._dec_pick_bin()
        self._bz = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=8,
                                border_color=C_TEXT3, border_width=1, height=36)
        self._bz.pack(fill="x", padx=12, pady=(0, 2))
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
        self._bin_list_frame = _tk.Frame(sc, bg=C_INPUT, height=24,
                                         highlightbackground=C_BORDER,
                                         highlightthickness=1, bd=0)
        self._bin_list_frame.pack(fill="x", padx=12, pady=(0, 2))
        self._bin_list_frame.pack_propagate(False)
        self._bin_placeholder = _tk.Label(
            self._bin_list_frame, text="No file selected",
            bg=C_INPUT, fg=C_TEXT3, font=("Segoe UI", 9), anchor="w")
        self._bin_placeholder.pack(side="left", padx=8, fill="x", expand=True)
        self._bin_display_lbl = None

        tg_btn(sc, "Browse .bin", self._dec_pick_bin, style="outline",
               height=26, font=_font(10), corner_radius=20
               ).pack(fill="x", padx=12, pady=(0, 2))

        hr(sc, pady=2)

        # ── KEY FILE ──────────────────────────────────────────────────────────
        tg_sec(sc, "Key File").pack(fill="x", padx=16, pady=(4, 2))

        _kz_browse = lambda e: self._dec_pick_key()
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

        # key display row
        self._key_list_frame = _tk.Frame(sc, bg=C_INPUT, height=24,
                                         highlightbackground=C_BORDER,
                                         highlightthickness=1, bd=0)
        self._key_list_frame.pack(fill="x", padx=12, pady=(0, 2))
        self._key_list_frame.pack_propagate(False)
        # show default key if pre-loaded
        _def_name = os.path.basename(self._dec_key_path) if self._dec_key_path else ""
        self._key_placeholder = _tk.Label(
            self._key_list_frame,
            text=f"\U0001f511 {_def_name}" if _def_name else "No key loaded",
            bg=C_INPUT,
            fg=C_BLUE if _def_name else C_TEXT3,
            font=("Segoe UI", 9), anchor="w")
        self._key_placeholder.pack(side="left", padx=8, fill="x", expand=True)
        self._key_display_widgets: list = []

        key_btn_row = ctk.CTkFrame(sc, fg_color="transparent")
        key_btn_row.pack(fill="x", padx=12, pady=(0, 2))
        key_btn_row.grid_columnconfigure((0, 1), weight=1)
        tg_btn(key_btn_row, "Load Key", self._dec_pick_key, style="outline",
               height=26, font=_font(10), corner_radius=20
               ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        tg_btn(key_btn_row, "↗ Output Folder", self._dec_pick_out, style="ghost",
               height=26, font=_font(10), corner_radius=20
               ).grid(row=0, column=1, sticky="ew")

        hr(sc, pady=2)

        # ── ACTIONS ───────────────────────────────────────────────────────────
        tg_sec(sc, "Actions").pack(fill="x", padx=16, pady=(4, 4))

        self._dec_btn = tg_btn(
            sc, "▶  Run Decrypt",
            command=lambda: threading.Thread(target=self._dec_start, daemon=True).start(),
            style="primary", height=40, font=_font(13, "bold"), corner_radius=20,
            state="normal" if _CRYPTO_OK else "disabled")
        self._dec_btn.pack(fill="x", padx=12, pady=(0, 4))

        tg_btn(sc, "↗ Open Output", self._dec_open_output,
               style="outline", height=28, font=_font(10), corner_radius=20
               ).pack(fill="x", padx=12, pady=(0, 4))

        # Progress row
        pr = ctk.CTkFrame(sc, fg_color="transparent")
        pr.pack(fill="x", padx=12, pady=(2, 1))
        ctk.CTkLabel(pr, text="OVERALL", font=_font(10),
                     text_color=C_TEXT3, anchor="w").pack(side="left")
        self._dec_pct = ctk.CTkLabel(pr, text="0 %",
                                     font=_font(10, "bold"), text_color=C_TEXT)
        self._dec_pct.pack(side="right")

        self._dec_bar = ctk.CTkProgressBar(sc, mode="determinate", height=4,
                                           corner_radius=2,
                                           progress_color=C_BLUE, fg_color=C_BORDER)
        self._dec_bar.set(0)
        self._dec_bar.pack(fill="x", padx=12, pady=(0, 3))

        self._dec_status = ctk.CTkLabel(
            sc,
            text="Ready" if _CRYPTO_OK else "⚠  pip install cryptography",
            font=_font(10), anchor="w",
            text_color=C_BLUE if _CRYPTO_OK else C_ORANGE)
        self._dec_status.pack(fill="x", padx=14, pady=(0, 3))

        if not _CRYPTO_OK:
            err = ctk.CTkFrame(sc, fg_color=C_SURFACE, corner_radius=8,
                               border_color=C_RED, border_width=1)
            err.pack(fill="x", padx=12, pady=(0, 6))
            ctk.CTkLabel(err, text="⚠  pip install cryptography",
                         font=_font(11), text_color=C_RED, anchor="w"
                         ).pack(padx=12, pady=6)

    # ── CONTENT ───────────────────────────────────────────────────────────────
    def _build_content(self, parent):
        # Sub-toolbar (42px, C_SURFACE bg)
        hdr = ctk.CTkFrame(parent, fg_color=C_SURFACE, height=42, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Left: DECRYPTION ENGINE · 3-LAYER VISUALIZER
        ctk.CTkLabel(hdr, text="DECRYPTION ENGINE",
                     font=_font(13, "bold"), text_color=C_TEXT).pack(side="left", padx=(14, 0))
        ctk.CTkLabel(hdr, text="  ·  3-LAYER VISUALIZER",
                     font=_font(12), text_color=C_SEC_HDR).pack(side="left")

        ctk.CTkFrame(parent, fg_color=C_BORDER, height=1, corner_radius=0).pack(fill="x")

        inner = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=20, pady=16)

        # ── 3 Layer Cards ─────────────────────────────────────────────────────
        _LAYERS = [
            ("L1", "DEC",  "Decryption Layer 1", "First pass",   C_BLUE, "#EBF1FF"),
            ("L2", "INT",  "Decryption Layer 2", "Second pass",  C_BLUE, "#EBF1FF"),
            ("L3", "FIN",  "Decryption Layer 3", "Third pass",   C_BLUE, "#EBF1FF"),
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

            # Top row: pill badges + status dot
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

            # Hash display box
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
        self._dec_pct2 = ctk.CTkLabel(op, text="0 %",
                                      font=_font(13, "bold"), text_color=C_TEXT)
        self._dec_pct2.pack(side="right")

        self._dec_bar2 = ctk.CTkProgressBar(inner, mode="determinate",
                                            height=4, corner_radius=2,
                                            progress_color=C_BLUE, fg_color=C_BORDER)
        self._dec_bar2.set(0)
        self._dec_bar2.pack(fill="x", pady=(0, 14))

        # ── Terminal log ──────────────────────────────────────────────────────
        log_hdr = ctk.CTkFrame(inner, fg_color="transparent")
        log_hdr.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(log_hdr, text="TERMINAL OUTPUT",
                     font=_font(11, "bold"), text_color=C_TEXT2).pack(side="left")
        tg_btn(log_hdr, "CLR", self._dec_clear_log,
               style="ghost", height=24, width=48,
               font=_font(10), corner_radius=20).pack(side="right")

        self._dec_log = ctk.CTkTextbox(
            inner,
            fg_color=C_SURFACE, text_color=C_TEXT, font=_mono(11),
            corner_radius=8, border_color=C_BORDER, border_width=1,
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
            card.configure(border_color=C_BORDER, border_width=1)
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
    def _phantom_dir():
        """Default picker start dir: ~/Documents/phantom (created if needed)."""
        d = Path.home() / "Documents" / "phantom"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

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
            initialdir=self._phantom_dir())
        if p:
            self._dec_bin_full = p
            self._dec_bin.set(os.path.basename(p))
            _out_full = str(Path(p).parent / "output")
            self._dec_out_full = _out_full
            self._dec_out.set("output")
            self._refresh_bin_display(os.path.basename(p))

    def _dec_pick_key(self):
        p = filedialog.askopenfilename(
            title="Select phantom.key",
            filetypes=[("Key file", "*.key"), ("All files", "*.*")],
            initialdir=self._phantom_dir())
        if p:
            self._dec_key_path = p
            self._dec_key.set("")
            self._refresh_key_display(os.path.basename(p))

    def _dec_pick_out(self):
        _init = self._dec_out_full if self._dec_out_full else self._phantom_dir()
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
        # Resolve the real output path (stored internally)
        d = self._dec_out_full if self._dec_out_full else str(Path(__file__).parent / self._dec_out.get())
        if os.path.isdir(d):
            try:
                if sys.platform == "win32":   subprocess.Popen(f'explorer "{d}"')
                elif sys.platform == "darwin": subprocess.Popen(["open", d])
                else:                          subprocess.Popen(["xdg-open", d])
            except: pass
        else:
            self._show_toast("⚠  Output folder not found yet", error=True)

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

            self._dec_log_msg("\n[OUT]  Writing decrypted files…")
            os.makedirs(out_d, exist_ok=True)
            results = []
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    entries = zf.namelist()
                    self._dec_log_msg(f"  Archive: {len(entries)} file(s)")
                    for i, entry in enumerate(entries, 1):
                        orig = entry.removesuffix(".enc")
                        self._dec_log_msg(f"  [{i}/{len(entries)}] {orig}")
                        try:
                            plain = _phtm_decrypt_3layer(zf.read(entry), master)
                            out_p = os.path.join(out_d, orig)
                            open(out_p, "wb").write(plain)
                            self._dec_log_msg(f"  ✓  {len(plain):,} bytes → {orig}")
                            results.append((orig, out_p, len(plain), True))
                        except Exception as e2:
                            self._dec_log_msg(f"  ✗  {e2}")
                            results.append((orig, None, 0, False))
            except Exception as e:
                self._dec_log_msg(f"✗  UNPACK ERROR: {e}")
                self.after(0, lambda: self._dec_status.configure(
                    text=f"Error: {e}", text_color=C_RED))
                self.after(0, lambda: self._dec_btn.configure(state="normal"))
                return

            ok  = sum(1 for r in results if r[3])
            err = len(results) - ok

            self._dec_log_msg("══════════════════════════════════════")
            for orig, out_path, size, success in results:
                if success and out_path:
                    sz_str = f"{size/1024:.1f} KB" if size >= 1024 else f"{size} B"
                    self._dec_log_msg(f"  ▶  {orig}  ({sz_str})")
            self._dec_log_msg(f"\n  DONE   {ok} OK  ·  {err} ERROR(S)")
            self._dec_log_msg("══════════════════════════════════════")
            # ── Green completion banner ───────────────────────────────────────
            _ok_files = [r[0] for r in results if r[3]]
            _bn = _ok_files[0] if _ok_files else os.path.basename(bin_p)
            self._log_banner(_bn, layers=3, total=3, mode="decrypted")

            self.after(0, lambda: self._dec_status.configure(
                text=f"Done — {ok}/{len(results)} decrypted",
                text_color=C_BLUE))
            self._show_toast(f"✓  Decrypt: {ok} file(s) done")
            self.after(0, lambda: self._dec_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
