"""
AES-128-CTR Decrypt UI for ESP32 record files.

Input format:
    PHA128 + IV(16 bytes) + ciphertext(raw PCM16LE mono)

Output:
    WAV (16kHz, mono, 16-bit)

Run:
    pip install cryptography
    python decrypt_aes128ctr_ui.py
"""

import os
import re
import time
import wave
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"PHA128"
IV_LEN = 16
MIN_SIZE = len(MAGIC) + IV_LEN + 1

PCM_RATE = 16000
PCM_CHANNELS = 1
PCM_WIDTH = 2  # bytes/sample (16-bit)


def extract_hex_key(text: str) -> str:
    cleaned = (
        text.replace(" ", "")
        .replace(":", "")
        .replace("-", "")
        .replace("\r", "")
        .replace("\n", "")
    )
    m = re.findall(r"[a-fA-F0-9]{32}", cleaned)
    if not m:
        raise ValueError("No AES-128 hex key found (need 32 hex chars).")
    return m[-1]


def load_key_from_txt(path: str) -> bytes:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        key_hex = extract_hex_key(f.read())
    key = bytes.fromhex(key_hex)
    if len(key) != 16:
        raise ValueError("Invalid key length, expected 16 bytes.")
    return key


def decrypt_bin_to_pcm(raw: bytes, key: bytes) -> bytes:
    if len(raw) < MIN_SIZE:
        raise ValueError("File too small.")
    if raw[: len(MAGIC)] != MAGIC:
        raise ValueError("Invalid magic. Expected PHA128.")

    pos = len(MAGIC)
    iv = raw[pos : pos + IV_LEN]
    ct = raw[pos + IV_LEN :]
    if not ct:
        raise ValueError("No ciphertext payload.")

    cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
    dec = cipher.decryptor()
    pcm = dec.update(ct) + dec.finalize()
    return pcm


def save_pcm_as_wav(pcm: bytes, wav_path: str):
    # Ensure 16-bit sample alignment.
    if len(pcm) % 2 != 0:
        pcm = pcm[:-1]
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(PCM_CHANNELS)
        w.setsampwidth(PCM_WIDTH)
        w.setframerate(PCM_RATE)
        w.writeframes(pcm)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ESP32 AES-128-CTR Decrypt")
        self.geometry("900x600")

        self.files = []
        self.key_path = tk.StringVar()
        self.out_dir = tk.StringVar(value=str(Path.cwd() / "decrypted_wav"))

        self._build_ui()
        self._log("Ready. Select .bin files and key txt to decrypt.")

    def _build_ui(self):
        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        tk.Button(top, text="Add .bin files", command=self._pick_files).pack(side="left")
        tk.Button(top, text="Clear files", command=self._clear_files).pack(side="left", padx=6)
        tk.Button(top, text="Load key .txt", command=self._pick_key).pack(side="left", padx=6)
        tk.Button(top, text="Output folder", command=self._pick_out).pack(side="left", padx=6)
        tk.Button(top, text="Decrypt", command=self._start_decrypt, bg="#2f6fed", fg="white").pack(side="right")

        row2 = tk.Frame(self)
        row2.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(row2, text="Key:").pack(side="left")
        tk.Entry(row2, textvariable=self.key_path).pack(side="left", fill="x", expand=True, padx=6)

        row3 = tk.Frame(self)
        row3.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(row3, text="Output:").pack(side="left")
        tk.Entry(row3, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=6)

        split = tk.PanedWindow(self, orient="horizontal", sashrelief="raised")
        split.pack(fill="both", expand=True, padx=10, pady=10)

        left = tk.Frame(split)
        right = tk.Frame(split)
        split.add(left, minsize=300)
        split.add(right, minsize=300)

        tk.Label(left, text="Selected files").pack(anchor="w")
        self.file_list = tk.Listbox(left)
        self.file_list.pack(fill="both", expand=True)

        tk.Label(right, text="Log").pack(anchor="w")
        self.log = tk.Text(right, state="disabled")
        self.log.pack(fill="both", expand=True)

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _pick_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose encrypted files",
            filetypes=[("BIN files", "*.bin"), ("All files", "*.*")],
        )
        added = 0
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                added += 1
        self._refresh_list()
        self._log(f"Added {added} file(s).")

    def _clear_files(self):
        self.files.clear()
        self._refresh_list()
        self._log("Cleared file list.")

    def _pick_key(self):
        p = filedialog.askopenfilename(
            title="Choose key txt",
            filetypes=[("Text files", "*.txt *.key"), ("All files", "*.*")],
        )
        if p:
            self.key_path.set(p)
            self._log(f"Key file: {os.path.basename(p)}")

    def _pick_out(self):
        p = filedialog.askdirectory(title="Choose output folder", initialdir=self.out_dir.get())
        if p:
            self.out_dir.set(p)
            self._log(f"Output folder: {p}")

    def _refresh_list(self):
        self.file_list.delete(0, "end")
        for i, p in enumerate(self.files, start=1):
            self.file_list.insert("end", f"{i}. {os.path.basename(p)}")

    def _start_decrypt(self):
        if not self.files:
            messagebox.showwarning("Missing files", "Please add .bin files first.")
            return
        if not self.key_path.get().strip():
            messagebox.showwarning("Missing key", "Please load key txt file.")
            return
        threading.Thread(target=self._decrypt_worker, daemon=True).start()

    def _decrypt_worker(self):
        try:
            key = load_key_from_txt(self.key_path.get().strip())
        except Exception as e:
            self._log(f"Key error: {e}")
            messagebox.showerror("Key error", str(e))
            return

        out_dir = Path(self.out_dir.get().strip() or "decrypted_wav")
        out_dir.mkdir(parents=True, exist_ok=True)

        ok = 0
        fail = 0
        for idx, in_path in enumerate(list(self.files), start=1):
            name = Path(in_path).name
            self._log(f"[{idx}/{len(self.files)}] Decrypting {name}")
            try:
                raw = Path(in_path).read_bytes()
                pcm = decrypt_bin_to_pcm(raw, key)
                out_name = Path(in_path).stem + ".wav"
                out_path = str(out_dir / out_name)
                save_pcm_as_wav(pcm, out_path)
                self._log(f"OK -> {out_name} ({len(pcm)} bytes PCM)")
                ok += 1
            except Exception as e:
                self._log(f"FAIL -> {name}: {e}")
                fail += 1

        self._log(f"Done. OK={ok}, FAIL={fail}")
        if fail == 0:
            messagebox.showinfo("Done", f"Decrypted {ok} file(s).")
        else:
            messagebox.showwarning("Done with errors", f"OK={ok}, FAIL={fail}")


if __name__ == "__main__":
    App().mainloop()

