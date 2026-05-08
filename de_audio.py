"""
phantom_decrypt_pro_ui.py
PHANTOM AES-GCM Decrypt Pro UI

Run:
    pip install customtkinter cryptography
    python phantom_decrypt_pro_ui.py

Supports firmware file format:
    PHGCM1 + IV(12 bytes) + ciphertext + TAG(16 bytes)

Input:  .bin or .enc
Output: .wav
"""

import os
import re
import sys
import time
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
except ImportError:
    raise SystemExit("Missing customtkinter. Install: pip install customtkinter")

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

# ═══════════════════════════════════════════════════════════════════════════════
# PHANTOM AES-GCM FORMAT
# ═══════════════════════════════════════════════════════════════════════════════

MAGIC = b"PHGCM1"
IV_LEN = 12
TAG_LEN = 16
MIN_ENCRYPTED_SIZE = len(MAGIC) + IV_LEN + TAG_LEN


def parse_key(key_text: str) -> bytes:
    """Parse AES key from hex text. Accepts spaces, colons, dashes, newlines."""
    key_text = (key_text or "").strip()
    if not key_text:
        raise ValueError("Chưa có key.")

    hex_candidate = (
        key_text.replace(" ", "")
        .replace(":", "")
        .replace("-", "")
        .replace("\n", "")
        .replace("\r", "")
        .strip()
    )

    try:
        key = bytes.fromhex(hex_candidate)
    except ValueError:
        raise ValueError("Key không phải HEX hợp lệ.")

    if len(key) not in (16, 24, 32):
        raise ValueError("Key phải là AES-128/192/256: 32/48/64 ký tự HEX.")

    return key


def extract_hex_key_from_text(content: str) -> str:
    """Find 128/192/256-bit hex key in a text file. Prefer longest, latest match."""
    cleaned = (
        content.replace(" ", "")
        .replace(":", "")
        .replace("-", "")
        .replace("\n", "")
        .replace("\r", "")
    )
    matches = re.findall(r"[a-fA-F0-9]{64}|[a-fA-F0-9]{48}|[a-fA-F0-9]{32}", cleaned)
    if not matches:
        raise ValueError("Không tìm thấy key HEX trong file.")
    matches.sort(key=len)
    return matches[-1]


def decrypt_phantom_file(input_path: str, output_path: str, key: bytes) -> int:
    """Decrypt Phantom AES-GCM .bin/.enc to WAV. Return output byte size."""
    if AESGCM is None:
        raise RuntimeError("Thiếu thư viện cryptography. Cài: pip install cryptography")

    with open(input_path, "rb") as f:
        data = f.read()

    if len(data) <= MIN_ENCRYPTED_SIZE:
        raise ValueError("File quá nhỏ/rỗng, không phải file Phantom hợp lệ.")

    if data[: len(MAGIC)] != MAGIC:
        raise ValueError("Sai định dạng. Không thấy magic PHGCM1.")

    pos = len(MAGIC)
    iv = data[pos : pos + IV_LEN]
    pos += IV_LEN
    encrypted_plus_tag = data[pos:]

    if len(iv) != IV_LEN or len(encrypted_plus_tag) <= TAG_LEN:
        raise ValueError("File thiếu IV/ciphertext/tag. Có thể file ghi dở hoặc lỗi.")

    try:
        wav = AESGCM(key).decrypt(iv, encrypted_plus_tag, None)
    except Exception:
        raise ValueError("Giải mã thất bại. Có thể sai key hoặc file bị hỏng.")

    if not wav.startswith(b"RIFF") or b"WAVE" not in wav[:16]:
        raise ValueError("Giải mã xong nhưng không phải WAV. Có thể sai key/file lỗi.")

    with open(output_path, "wb") as f:
        f.write(wav)

    return len(wav)


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def open_folder(path: str):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        messagebox.showerror("Không mở được thư mục", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# THEME
# ═══════════════════════════════════════════════════════════════════════════════

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

C_BG = "#F4F7FB"
C_PANEL = "#FFFFFF"
C_CARD = "#FFFFFF"
C_SURFACE = "#EEF3FA"
C_INPUT = "#F7F9FC"
C_BORDER = "#D7DEE9"
C_TEXT = "#172033"
C_TEXT2 = "#667085"
C_TEXT3 = "#98A2B3"
C_BLUE = "#2563EB"
C_BLUE_DARK = "#1D4ED8"
C_GREEN = "#16A34A"
C_RED = "#DC2626"
C_ORANGE = "#F59E0B"
C_VIOLET = "#7C3AED"


def font(size=13, weight="normal"):
    return ctk.CTkFont("Segoe UI", size=size, weight=weight)


def mono(size=12, weight="normal"):
    return ctk.CTkFont("Consolas", size=size, weight=weight)


class PhantomDecryptPro(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PHANTOM Decrypt Pro")
        self.geometry("1120x700")
        self.minsize(980, 620)
        self.configure(fg_color=C_BG)

        self.selected_files: list[str] = []
        self.key_path = tk.StringVar(value="")
        self.key_text = tk.StringVar(value="")
        self.output_dir = tk.StringVar(value=str(Path.home() / "Documents" / "Phantom" / "decrypted"))
        self.show_key = False
        self.decrypting = False

        Path(self.output_dir.get()).mkdir(parents=True, exist_ok=True)

        self._build_ui()
        self._write_log("PHANTOM Decrypt Pro ready")
        self._write_log("Chọn file .bin/.enc và file key HEX để giải mã ra WAV")
        if AESGCM is None:
            self._write_log("⚠ Thiếu cryptography: pip install cryptography", error=True)

    # ── UI BUILD ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, fg_color=C_PANEL, width=330, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        main = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        self._build_sidebar(sidebar)
        self._build_main(main)

    def _build_sidebar(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=22, pady=(22, 16))
        ctk.CTkLabel(header, text="PHANTOM", font=font(24, "bold"), text_color=C_TEXT).pack(anchor="w")
        ctk.CTkLabel(header, text="AES-GCM Decrypt Console", font=font(12), text_color=C_TEXT2).pack(anchor="w")

        self._side_section(parent, "1. Encrypted files")
        self.file_card = self._drop_card(parent, "📦", "Chọn file .bin/.enc", "Hỗ trợ chọn nhiều file", self.choose_files)
        self.file_count_label = ctk.CTkLabel(parent, text="Chưa chọn file", font=font(12), text_color=C_TEXT3, anchor="w")
        self.file_count_label.pack(fill="x", padx=22, pady=(6, 12))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 12))
        row.grid_columnconfigure((0, 1), weight=1)
        self._button(row, "Thêm file", self.choose_files, style="secondary").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._button(row, "Xóa chọn", self.clear_files, style="ghost").grid(row=0, column=1, sticky="ew")

        self._divider(parent)
        self._side_section(parent, "2. Key")
        self.key_card = self._drop_card(parent, "🔑", "Chọn file key", "TXT chứa HEX key 32/48/64 ký tự", self.choose_key)

        key_box = ctk.CTkFrame(parent, fg_color="transparent")
        key_box.pack(fill="x", padx=22, pady=(6, 12))
        key_box.grid_columnconfigure(0, weight=1)
        self.key_entry = ctk.CTkEntry(
            key_box,
            textvariable=self.key_text,
            show="*",
            fg_color=C_INPUT,
            border_color=C_BORDER,
            height=36,
            corner_radius=10,
            font=mono(11),
            placeholder_text="Key HEX sẽ hiện ở đây",
        )
        self.key_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._button(key_box, "👁", self.toggle_key, width=42, style="secondary").grid(row=0, column=1)

        self._divider(parent)
        self._side_section(parent, "3. Output")
        out_row = ctk.CTkFrame(parent, fg_color="transparent")
        out_row.pack(fill="x", padx=22, pady=(4, 12))
        out_row.grid_columnconfigure(0, weight=1)
        self.output_entry = ctk.CTkEntry(
            out_row,
            textvariable=self.output_dir,
            fg_color=C_INPUT,
            border_color=C_BORDER,
            height=36,
            corner_radius=10,
            font=font(11),
        )
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._button(out_row, "...", self.choose_output_dir, width=42, style="secondary").grid(row=0, column=1)

        self.decrypt_btn = self._button(parent, "GIẢI MÃ RA WAV", self.start_decrypt, style="primary", height=46)
        self.decrypt_btn.pack(fill="x", padx=22, pady=(8, 6))

        self._button(parent, "Mở thư mục output", lambda: open_folder(self.output_dir.get()), style="secondary", height=38).pack(fill="x", padx=22, pady=(0, 12))

        ctk.CTkFrame(parent, fg_color="transparent").pack(expand=True, fill="both")
        ctk.CTkLabel(
            parent,
            text="Format: PHGCM1 + IV + CIPHERTEXT + TAG",
            font=mono(10),
            text_color=C_TEXT3,
        ).pack(fill="x", padx=22, pady=(0, 16))

    def _build_main(self, parent):
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 12))
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(top, text="Decrypt Dashboard", font=font(26, "bold"), text_color=C_TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(top, text="Giải mã file ghi âm Phantom .bin/.enc thành WAV", font=font(13), text_color=C_TEXT2).grid(row=1, column=0, sticky="w", pady=(3, 0))

        self.status_badge = ctk.CTkLabel(
            top,
            text="READY",
            font=font(12, "bold"),
            text_color=C_BLUE,
            fg_color="#EAF1FF",
            corner_radius=999,
            padx=14,
            pady=7,
        )
        self.status_badge.grid(row=0, column=1, rowspan=2, sticky="e")

        cards = ctk.CTkFrame(parent, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 14))
        cards.grid_columnconfigure((0, 1, 2), weight=1)

        self.card_files_num = self._metric_card(cards, "Files", "0", "đang chờ", C_BLUE, 0)
        self.card_key_num = self._metric_card(cards, "Key", "—", "chưa load", C_VIOLET, 1)
        self.card_done_num = self._metric_card(cards, "Done", "0", "file WAV", C_GREEN, 2)

        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 24))
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=3)
        body.grid_rowconfigure(0, weight=1)

        list_panel = self._panel(body, "Selected Files")
        list_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        list_panel.grid_rowconfigure(1, weight=1)
        self.file_list = ctk.CTkScrollableFrame(list_panel, fg_color="transparent", corner_radius=0)
        self.file_list.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self._refresh_file_list()

        log_panel = self._panel(body, "Terminal Output")
        log_panel.grid(row=0, column=1, sticky="nsew")
        log_panel.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(log_panel, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(0, 6))
        toolbar.grid_columnconfigure(0, weight=1)
        self.progress_label = ctk.CTkLabel(toolbar, text="0 %", font=font(12, "bold"), text_color=C_TEXT2)
        self.progress_label.grid(row=0, column=1, sticky="e")
        self.progress = ctk.CTkProgressBar(log_panel, height=8, corner_radius=999, progress_color=C_BLUE, fg_color=C_BORDER)
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

        self.log = ctk.CTkTextbox(
            log_panel,
            fg_color="#0B1220",
            text_color="#D1D5DB",
            font=mono(12),
            corner_radius=12,
            border_width=0,
            wrap="word",
        )
        self.log.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log.configure(state="disabled")

    # ── WIDGET HELPERS ───────────────────────────────────────────────────────
    def _button(self, parent, text, command, style="primary", height=36, width=None):
        styles = {
            "primary": dict(fg_color=C_BLUE, hover_color=C_BLUE_DARK, text_color="white", border_width=0),
            "secondary": dict(fg_color=C_SURFACE, hover_color="#E0E8F5", text_color=C_TEXT, border_color=C_BORDER, border_width=1),
            "ghost": dict(fg_color="transparent", hover_color=C_SURFACE, text_color=C_TEXT2, border_color=C_BORDER, border_width=1),
        }
        kw = styles.get(style, styles["primary"])
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            height=height,
            width=width or 120,
            corner_radius=14,
            font=font(12, "bold"),
            **kw,
        )

    def _drop_card(self, parent, icon, title, subtitle, command):
        card = ctk.CTkFrame(parent, fg_color=C_SURFACE, corner_radius=16, border_color=C_BORDER, border_width=1)
        card.pack(fill="x", padx=22, pady=(4, 4))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=14)
        ctk.CTkLabel(inner, text=icon, font=font(24)).pack(side="left", padx=(0, 12))
        txt = ctk.CTkFrame(inner, fg_color="transparent")
        txt.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(txt, text=title, font=font(13, "bold"), text_color=C_TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(txt, text=subtitle, font=font(11), text_color=C_TEXT2, anchor="w").pack(fill="x", pady=(2, 0))
        card.bind("<Button-1>", lambda _e: command())
        for child in card.winfo_children():
            child.bind("<Button-1>", lambda _e: command())
            for sub in child.winfo_children():
                sub.bind("<Button-1>", lambda _e: command())
        return card

    def _side_section(self, parent, title):
        ctk.CTkLabel(parent, text=title.upper(), font=font(10, "bold"), text_color=C_TEXT3, anchor="w").pack(fill="x", padx=22, pady=(8, 2))

    def _divider(self, parent):
        ctk.CTkFrame(parent, fg_color=C_BORDER, height=1).pack(fill="x", padx=22, pady=8)

    def _metric_card(self, parent, title, value, sub, color, col):
        card = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=18, border_color=C_BORDER, border_width=1)
        card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 10, 0))
        ctk.CTkLabel(card, text=title.upper(), font=font(10, "bold"), text_color=C_TEXT3).pack(anchor="w", padx=18, pady=(16, 0))
        lbl = ctk.CTkLabel(card, text=value, font=font(28, "bold"), text_color=color)
        lbl.pack(anchor="w", padx=18, pady=(4, 0))
        ctk.CTkLabel(card, text=sub, font=font(12), text_color=C_TEXT2).pack(anchor="w", padx=18, pady=(0, 16))
        return lbl

    def _panel(self, parent, title):
        panel = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=18, border_color=C_BORDER, border_width=1)
        ctk.CTkLabel(panel, text=title, font=font(15, "bold"), text_color=C_TEXT, anchor="w").grid(row=0, column=0, sticky="ew", padx=16, pady=14)
        panel.grid_columnconfigure(0, weight=1)
        return panel

    # ── ACTIONS ──────────────────────────────────────────────────────────────
    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="Chọn file Phantom .bin/.enc",
            filetypes=[
                ("Phantom encrypted files", "*.bin *.enc"),
                ("BIN files", "*.bin"),
                ("Legacy ENC files", "*.enc"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        added = 0
        for p in paths:
            if p not in self.selected_files:
                self.selected_files.append(p)
                added += 1
        self._refresh_file_list()
        self._update_metrics()
        self._write_log(f"Added {added} encrypted file(s)")

    def clear_files(self):
        self.selected_files.clear()
        self._refresh_file_list()
        self._update_metrics()
        self._write_log("Cleared selected files")

    def choose_key(self):
        path = filedialog.askopenfilename(
            title="Chọn file key HEX",
            filetypes=[("Text/key file", "*.txt *.key"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                key = extract_hex_key_from_text(f.read())
            # validate immediately
            parse_key(key)
            self.key_path.set(path)
            self.key_text.set(key)
            self.key_card.configure(border_color=C_VIOLET)
            self._write_log(f"Key loaded: {os.path.basename(path)}")
            self._update_metrics()
        except Exception as e:
            self._write_log(f"Key error: {e}", error=True)
            messagebox.showerror("Lỗi key", str(e))

    def toggle_key(self):
        self.show_key = not self.show_key
        self.key_entry.configure(show="" if self.show_key else "*")

    def choose_output_dir(self):
        path = filedialog.askdirectory(title="Chọn thư mục lưu WAV", initialdir=self.output_dir.get())
        if path:
            self.output_dir.set(path)
            Path(path).mkdir(parents=True, exist_ok=True)
            self._write_log(f"Output folder: {path}")

    def start_decrypt(self):
        if self.decrypting:
            return
        if not self.selected_files:
            messagebox.showwarning("Thiếu file", "Chưa chọn file .bin/.enc.")
            return
        if not self.key_text.get().strip():
            messagebox.showwarning("Thiếu key", "Chưa chọn file key hoặc nhập key.")
            return
        Path(self.output_dir.get()).mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._decrypt_worker, daemon=True).start()

    def _decrypt_worker(self):
        self.decrypting = True
        self.after(0, lambda: self._set_busy(True))
        done = 0
        failed = 0
        total = len(self.selected_files)
        try:
            key = parse_key(self.key_text.get())
            for idx, input_file in enumerate(list(self.selected_files), start=1):
                base = Path(input_file).stem + ".wav"
                output = str(Path(self.output_dir.get()) / base)
                self._write_log(f"Decrypting [{idx}/{total}]: {os.path.basename(input_file)}")
                try:
                    size = decrypt_phantom_file(input_file, output, key)
                    done += 1
                    self._write_log(f"OK → {base} ({fmt_size(size)})")
                except Exception as e:
                    failed += 1
                    self._write_log(f"FAIL → {os.path.basename(input_file)}: {e}", error=True)
                self.after(0, lambda v=idx / total: self._set_progress(v))
                time.sleep(0.05)
        except Exception as e:
            failed += total
            self._write_log(f"Decrypt session error: {e}", error=True)
            self.after(0, lambda: messagebox.showerror("Giải mã thất bại", str(e)))
        finally:
            self.decrypting = False
            self.after(0, lambda: self._set_busy(False))
            self.after(0, lambda: self.card_done_num.configure(text=str(done)))
            if failed == 0 and done > 0:
                self._write_log(f"Completed: {done}/{total} file(s)")
                self.after(0, lambda: messagebox.showinfo("Thành công", f"Đã giải mã {done} file WAV."))
            elif done > 0:
                self._write_log(f"Completed with errors: OK {done}, FAIL {failed}", error=True)
                self.after(0, lambda: messagebox.showwarning("Có lỗi", f"OK: {done}\nFail: {failed}"))

    # ── UI STATE ─────────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool):
        self.decrypt_btn.configure(state="disabled" if busy else "normal")
        self.status_badge.configure(
            text="DECRYPTING" if busy else "READY",
            text_color=C_ORANGE if busy else C_BLUE,
            fg_color="#FFF7E6" if busy else "#EAF1FF",
        )
        if not busy:
            self._set_progress(0)

    def _set_progress(self, value: float):
        value = max(0, min(1, value))
        self.progress.set(value)
        self.progress_label.configure(text=f"{int(value * 100)} %")

    def _update_metrics(self):
        self.card_files_num.configure(text=str(len(self.selected_files)))
        self.file_count_label.configure(
            text=f"{len(self.selected_files)} file đã chọn" if self.selected_files else "Chưa chọn file",
            text_color=C_TEXT if self.selected_files else C_TEXT3,
        )
        if self.key_text.get().strip():
            try:
                key = parse_key(self.key_text.get())
                self.card_key_num.configure(text=f"AES-{len(key) * 8}")
            except Exception:
                self.card_key_num.configure(text="ERR")
        else:
            self.card_key_num.configure(text="—")

    def _refresh_file_list(self):
        for w in self.file_list.winfo_children():
            w.destroy()
        if not self.selected_files:
            ctk.CTkLabel(
                self.file_list,
                text="Chưa có file nào.\nBấm 'Thêm file' hoặc ô chọn file bên trái.",
                font=font(13),
                text_color=C_TEXT3,
                justify="center",
            ).pack(expand=True, pady=80)
            return
        for i, path in enumerate(self.selected_files, start=1):
            self._file_row(self.file_list, i, path).pack(fill="x", padx=4, pady=5)

    def _file_row(self, parent, idx, path):
        row = ctk.CTkFrame(parent, fg_color=C_SURFACE, corner_radius=12, border_color=C_BORDER, border_width=1)
        icon = ctk.CTkLabel(row, text="🎧", font=font(22), width=38)
        icon.pack(side="left", padx=(12, 6), pady=10)
        body = ctk.CTkFrame(row, fg_color="transparent")
        body.pack(side="left", fill="x", expand=True, pady=9)
        name = os.path.basename(path)
        ctk.CTkLabel(body, text=f"{idx}. {name}", font=font(12, "bold"), text_color=C_TEXT, anchor="w").pack(fill="x")
        try:
            size = fmt_size(os.path.getsize(path))
        except Exception:
            size = "unknown"
        ctk.CTkLabel(body, text=f"{size}  ·  {path}", font=font(10), text_color=C_TEXT2, anchor="w").pack(fill="x", pady=(2, 0))
        ctk.CTkButton(
            row,
            text="×",
            width=30,
            height=30,
            corner_radius=10,
            fg_color="transparent",
            hover_color="#FEE2E2",
            text_color=C_RED,
            command=lambda p=path: self._remove_file(p),
        ).pack(side="right", padx=10)
        return row

    def _remove_file(self, path):
        if path in self.selected_files:
            self.selected_files.remove(path)
        self._refresh_file_list()
        self._update_metrics()

    def _write_log(self, message: str, error: bool = False):
        def _do():
            self.log.configure(state="normal")
            tag = "error" if error else "normal"
            try:
                self.log._textbox.tag_configure("error", foreground="#FCA5A5")
                self.log._textbox.tag_configure("normal", foreground="#D1D5DB")
                self.log._textbox.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n", tag)
            except Exception:
                self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
            self.log.configure(state="disabled")
            self.log.see("end")
        try:
            self.after(0, _do)
        except Exception:
            pass


if __name__ == "__main__":
    app = PhantomDecryptPro()
    app.mainloop()
