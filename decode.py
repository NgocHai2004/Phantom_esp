"""
decode.py — PHANTOM Secure File Decrypt
Style: Cyberpunk City Night — navy depths + electric blue + neon cyan-teal
Run:   .venv\Scripts\python decode.py
"""

import customtkinter as ctk
import tkinter as tk
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

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Cyberpunk City Night palette ──────────────────────────────────────────────
C_BG        = "#010326"
C_CARD      = "#010440"
C_SURFACE   = "#020659"
C_NAV       = "#020550"
C_BORDER    = "#0A1580"
C_BLUE      = "#030BA6"
C_BLUE_D    = "#020880"
C_PINK      = "#1BF2DD"
C_PINK_D    = "#13C4B2"
C_GREEN     = "#00FFB3"
C_RED       = "#FF0050"
C_ORANGE    = "#FF6B00"
C_TEAL      = "#00D4FF"
C_LABEL     = "#F0F4FF"
C_LABEL2    = "#A0AAEE"
C_LABEL3    = "#6878CC"
C_FILL3     = "#020756"

# ─────────────────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Phantom Decrypt")
        self.geometry("1080x680")
        self.minsize(860, 540)
        self.configure(fg_color=C_BG)

        # state
        self._dec_bin = ctk.StringVar()
        self._dec_key = ctk.StringVar()
        self._dec_out = ctk.StringVar()

        # set defaults
        _def_key = Path(__file__).parent / "decode" / "phantom.key"
        if _def_key.exists():
            self._dec_key.set(str(_def_key))
        self._dec_out.set(str(Path(__file__).parent / "decode" / "output"))

        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # ROOT LAYOUT  (sidebar | content)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=0)  # nav accent bar
        root.grid_columnconfigure(1, weight=0)  # sidebar
        root.grid_columnconfigure(2, weight=1)  # content

        # ── Accent bar (6px cyan strip left edge) ─────────────────────────────
        accent_bar = ctk.CTkFrame(root, fg_color=C_PINK, width=6, corner_radius=0)
        accent_bar.grid(row=0, column=0, sticky="nsew")
        accent_bar.grid_propagate(False)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(root, fg_color=C_CARD, width=264, corner_radius=0)
        sidebar.grid(row=0, column=1, sticky="nsew")
        sidebar.grid_propagate(False)
        tk.Frame(root, bg=C_BORDER, width=1).grid(row=0, column=1,
            sticky="nse", padx=(263, 0))

        # ── Content ───────────────────────────────────────────────────────────
        content = ctk.CTkFrame(root, fg_color=C_BG, corner_radius=0)
        content.grid(row=0, column=2, sticky="nsew", padx=(1, 0))

        self._build_sidebar(sidebar)
        self._build_content(content)

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
                     text_color=C_PINK).pack(side="left")
        ctk.CTkLabel(title_row, text=" DECRYPT",
                     font=ctk.CTkFont("Consolas", 18, "bold"),
                     text_color=C_LABEL).pack(side="left")

        ctk.CTkLabel(dark, text="3-Layer Cryptographic Engine",
                     font=ctk.CTkFont("Consolas", 13),
                     text_color=C_LABEL3).pack(anchor="w", padx=16, pady=(2, 12))

        tk.Frame(dark, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 14))

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

        # Decrypt button
        self._dec_btn = ctk.CTkButton(
            dark,
            text="▶  RUN DECRYPT",
            font=ctk.CTkFont("Consolas", 16, "bold"),
            fg_color=C_PINK, hover_color=C_PINK_D,
            text_color="#010326",
            height=42, corner_radius=8,
            command=lambda: threading.Thread(
                target=self._dec_start, daemon=True).start(),
            state="normal" if _CRYPTO_OK else "disabled")
        self._dec_btn.pack(fill="x", padx=14, pady=(0, 6))

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
    # CONTENT — 3-layer visualizer + terminal log
    # ─────────────────────────────────────────────────────────────────────────
    def _build_content(self, parent):
        dark_bg = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=0)
        dark_bg.pack(fill="both", expand=True)

        # ── 3 layer cards ─────────────────────────────────────────────────────
        layers_row = ctk.CTkFrame(dark_bg, fg_color="transparent")
        layers_row.pack(fill="x", padx=20, pady=(16, 10))
        layers_row.grid_columnconfigure((0, 1, 2), weight=1)

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

        self._dec_log.tag_config("ok",    foreground=C_GREEN)
        self._dec_log.tag_config("err",   foreground=C_RED)
        self._dec_log.tag_config("info",  foreground=C_ORANGE)
        self._dec_log.tag_config("dim",   foreground=C_LABEL2)
        self._dec_log.tag_config("head",  foreground=C_PINK,
                                 font=("Consolas", 13, "bold"))
        self._dec_log.tag_config("prompt", foreground=C_LABEL2)

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER ANIMATION
    # ─────────────────────────────────────────────────────────────────────────
    def _dec_animate_layer(self, layer_idx: int, hash_hex: str,
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
                self._dec_gbar.set(done_overall)
                self._dec_gbar_pct.configure(text=f"{int(done_overall*100)}%")
                on_done()
                return
            frac = step / steps
            bar.set(frac)
            pct.configure(text=f"{int(frac*100)}%")
            g = start_overall + frac / 3.0
            self._dec_gbar.set(g)
            self._dec_gbar_pct.configure(text=f"{int(g*100)}%")
            self.after(interval, lambda: _tick(step + 1))

        _tick()

    # ─────────────────────────────────────────────────────────────────────────
    # LOG HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _dec_log_msg(self, msg: str):
        def _append():
            self._dec_log.config(state="normal")
            self._dec_log.insert("end", "> ", "prompt")
            if msg.startswith("    ✓") or msg.startswith("✔"):
                tag = "ok"
            elif msg.startswith("    ✗") or "ERROR" in msg or "error" in msg.lower():
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
                lambda e, p=out_path: subprocess.Popen(f'explorer /select,"{p}"'))
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
        for (card, hash_lbl, bar, pct, dot, color) in self._layer_cards:
            bar.set(0)
            pct.configure(text="0%", text_color=color)
            dot.configure(text="●", text_color=C_LABEL3)
            hash_lbl.configure(text="HASH: —", text_color=C_LABEL3)
        self._dec_gbar.set(0)
        self._dec_gbar_pct.configure(text="0%", text_color=C_PINK)

    # ─────────────────────────────────────────────────────────────────────────
    # FILE PICKERS
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

    def _show_toast(self, msg, error=False):
        border = C_RED if error else C_GREEN
        fg     = C_RED if error else C_GREEN
        t = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=20,
                         border_color=border, border_width=1)
        t.place(relx=0.5, y=20, anchor="n")
        ctk.CTkLabel(t, text=msg, font=ctk.CTkFont("Consolas", 14, "bold"),
                     text_color=fg, padx=24, pady=10).pack()
        self.after(3000, t.destroy)

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

        self.after(0, self._dec_clear_log)
        self.after(0, lambda: self._dec_btn.configure(state="disabled"))
        self.after(0, lambda: self._dec_status_lbl.configure(
            text="RUNNING…", text_color=C_TEAL))

        def _run():
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

            k_aes, k_hmac, k_chacha = _phtm_derive(master)
            h_chacha = hashlib.sha256(k_chacha).hexdigest()
            h_hmac   = hashlib.sha256(k_hmac  ).hexdigest()
            h_aes    = hashlib.sha256(k_aes   ).hexdigest()

            # Layer 1 — ChaCha20
            self._dec_log_msg("\n[LAYER 1]  ChaCha20-Poly1305  —  stream decrypt")
            time.sleep(0.2)
            l1_done = threading.Event()
            self.after(0, lambda: self._dec_animate_layer(0, h_chacha, 6000, l1_done.set))
            l1_done.wait()
            self._dec_log_msg(f"    ✓  ChaCha20 key hash : {h_chacha[:32]}…")
            time.sleep(0.3)

            # Layer 2 — HMAC
            self._dec_log_msg("\n[LAYER 2]  HMAC-SHA256  —  integrity verify")
            time.sleep(0.2)
            l2_done = threading.Event()
            self.after(0, lambda: self._dec_animate_layer(1, h_hmac, 7000, l2_done.set))
            l2_done.wait()
            self._dec_log_msg(f"    ✓  HMAC key hash     : {h_hmac[:32]}…")
            time.sleep(0.3)

            # Layer 3 — AES-GCM
            self._dec_log_msg("\n[LAYER 3]  AES-256-GCM  —  final block decrypt")
            time.sleep(0.2)
            l3_done = threading.Event()
            self.after(0, lambda: self._dec_animate_layer(2, h_aes, 6000, l3_done.set))
            l3_done.wait()
            self._dec_log_msg(f"    ✓  AES-256 key hash  : {h_aes[:32]}…")
            time.sleep(0.3)

            # Actual decrypt
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
            self.after(0, lambda: self._dec_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
