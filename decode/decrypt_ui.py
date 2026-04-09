"""
PHANTOM Decrypt UI — Giao diện giải mã file .bin
Dùng tkinter (built-in Python), không cần cài thêm gì ngoài cryptography.
"""

import struct, zipfile, hashlib, io, os, sys, threading, subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ── Crypto (từ decrypt.py gốc) ────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    from cryptography.hazmat.primitives import hmac as crypto_hmac, hashes
    from cryptography.hazmat.backends import default_backend
    CRYPTO_OK = True
except ImportError:
    CRYPTO_OK = False

BIN_MAGIC   = b"PHTM"
BIN_VERSION = 2
KEY_SIZE    = 32


def load_key(path: str) -> bytes:
    data = open(path, "rb").read()
    if len(data) < KEY_SIZE:
        raise ValueError(f"Key file quá ngắn: {len(data)} bytes (cần {KEY_SIZE})")
    return data[:KEY_SIZE]


def derive_subkeys(master: bytes):
    def dk(tag): return hashlib.sha256(master + tag).digest()
    return dk(b"AES-GCM"), dk(b"HMAC-SHA256"), dk(b"CHACHA20")


def decrypt_3layer(enc: bytes, master: bytes) -> bytes:
    k_aes, k_hmac, k_chacha = derive_subkeys(master)
    n_cha  = enc[:12]
    ct3    = enc[12:]
    payload = ChaCha20Poly1305(k_chacha).decrypt(n_cha, ct3, None)
    hmac_tag = payload[-32:]
    inner    = payload[:-32]
    h = crypto_hmac.HMAC(k_hmac, hashes.SHA256(), backend=default_backend())
    h.update(inner)
    h.verify(hmac_tag)
    n_aes = inner[:12]
    ct1   = inner[12:]
    return AESGCM(k_aes).decrypt(n_aes, ct1, None)


def unpack_bin(bin_path: str, key_path: str, out_dir: str, log_cb=None):
    def log(msg):
        if log_cb: log_cb(msg)
        else: print(msg)

    raw = open(bin_path, "rb").read()
    if raw[:4] != BIN_MAGIC:
        raise ValueError("Không phải file PHANTOM (.bin magic sai)")
    version = struct.unpack_from("<I", raw, 4)[0]
    if version != BIN_VERSION:
        raise ValueError(f"Version không hỗ trợ: {version} (cần {BIN_VERSION})")

    md5_stored  = raw[8:24]
    payload_len = struct.unpack_from("<I", raw, 24)[0]
    payload     = raw[28 : 28 + payload_len]

    md5_actual = hashlib.md5(payload).digest()
    if md5_actual != md5_stored:
        raise ValueError("MD5 checksum không khớp — file có thể bị hỏng")

    log(f"✔  Header OK  |  Payload: {payload_len:,} bytes  |  MD5: {md5_stored.hex()}")
    master = load_key(key_path)
    log(f"✔  Key loaded: {key_path}")
    os.makedirs(out_dir, exist_ok=True)

    results = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        entries = zf.namelist()
        log(f"✔  ZIP chứa {len(entries)} file")
        for i, entry in enumerate(entries, 1):
            enc_data      = zf.read(entry)
            original_name = entry.removesuffix(".enc")
            log(f"\n[{i}/{len(entries)}] Giải mã: {entry}  →  {original_name}")
            try:
                plain    = decrypt_3layer(enc_data, master)
                out_path = os.path.join(out_dir, original_name)
                with open(out_path, "wb") as f:
                    f.write(plain)
                log(f"    ✓  Lưu: {out_path}  ({len(plain):,} bytes)")
                results.append((original_name, out_path, len(plain), True))
            except Exception as e:
                log(f"    ✗  Lỗi: {e}")
                results.append((original_name, None, 0, False))
    return results


# ── UI ────────────────────────────────────────────────────────
class PhantomDecryptUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PHANTOM Decrypt")
        self.geometry("720x580")
        self.resizable(True, True)
        self.configure(bg="#1e1e2e")
        self._build_ui()
        self._check_deps()

    # ── Build widgets ─────────────────────────────────────────
    def _build_ui(self):
        PAD  = {"padx": 12, "pady": 6}
        C_BG = "#1e1e2e"
        C_FG = "#cdd6f4"
        C_EN = "#313244"
        C_AC = "#89b4fa"
        C_GR = "#a6e3a1"
        C_RD = "#f38ba8"
        C_YL = "#f9e2af"

        self.option_add("*Font", ("Segoe UI", 10))

        # ── Title ──────────────────────────────────────────────
        tk.Label(self, text="🔓  PHANTOM Decrypt",
                 bg=C_BG, fg=C_AC,
                 font=("Segoe UI", 16, "bold")).pack(pady=(16, 4))
        tk.Label(self, text="Giải mã file .bin bảo mật 3 lớp (AES-GCM · HMAC · ChaCha20)",
                 bg=C_BG, fg="#7f849c",
                 font=("Segoe UI", 9)).pack(pady=(0, 12))

        # ── Frame chọn file ────────────────────────────────────
        frm = tk.Frame(self, bg=C_BG)
        frm.pack(fill="x", **PAD)
        frm.columnconfigure(1, weight=1)

        def row(r, label, var, cmd, hint=""):
            tk.Label(frm, text=label, bg=C_BG, fg=C_FG,
                     width=12, anchor="w").grid(row=r, column=0, sticky="w", pady=4)
            e = tk.Entry(frm, textvariable=var, bg=C_EN, fg=C_FG,
                         insertbackground=C_FG, relief="flat", bd=4)
            e.grid(row=r, column=1, sticky="ew", padx=(4, 6))
            tk.Button(frm, text="Chọn…", bg="#45475a", fg=C_FG,
                      activebackground=C_AC, activeforeground="#1e1e2e",
                      relief="flat", bd=0, padx=8,
                      command=cmd).grid(row=r, column=2)
            if hint:
                tk.Label(frm, text=hint, bg=C_BG, fg="#7f849c",
                         font=("Segoe UI", 8)).grid(row=r+1, column=1,
                                                     sticky="w", padx=4)

        self.var_bin = tk.StringVar()
        self.var_key = tk.StringVar()
        self.var_out = tk.StringVar(value=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "output"))

        row(0, "File .bin",    self.var_bin, self._pick_bin)
        row(2, "Key file",     self.var_key, self._pick_key)
        row(4, "Output folder",self.var_out, self._pick_out)

        # Auto-fill key nếu phantom.key cạnh script
        default_key = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phantom.key")
        if os.path.exists(default_key):
            self.var_key.set(default_key)

        # ── Nút Decrypt ────────────────────────────────────────
        btn_frm = tk.Frame(self, bg=C_BG)
        btn_frm.pack(pady=(8, 4))

        self.btn_decrypt = tk.Button(
            btn_frm, text="🔓  Giải mã ngay",
            bg=C_AC, fg="#1e1e2e",
            font=("Segoe UI", 11, "bold"),
            relief="flat", bd=0, padx=20, pady=8,
            activebackground="#74c7ec", activeforeground="#1e1e2e",
            command=self._start_decrypt)
        self.btn_decrypt.pack(side="left", padx=6)

        self.btn_open = tk.Button(
            btn_frm, text="📂  Mở thư mục output",
            bg="#45475a", fg=C_FG,
            relief="flat", bd=0, padx=12, pady=8,
            activebackground="#585b70",
            command=self._open_output)
        self.btn_open.pack(side="left", padx=6)

        # ── Progressbar ────────────────────────────────────────
        self.progress = ttk.Progressbar(self, mode="indeterminate", length=400)
        self.progress.pack(pady=(4, 0))

        # ── Log box ────────────────────────────────────────────
        tk.Label(self, text="Log", bg=C_BG, fg="#7f849c",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14)
        self.log_box = scrolledtext.ScrolledText(
            self, height=14, bg="#181825", fg=C_FG,
            font=("Consolas", 9), relief="flat", bd=0,
            insertbackground=C_FG, state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(2, 12))

        # Color tags
        self.log_box.tag_config("ok",   foreground=C_GR)
        self.log_box.tag_config("err",  foreground=C_RD)
        self.log_box.tag_config("info", foreground=C_YL)
        self.log_box.tag_config("dim",  foreground="#7f849c")

        # ── Status bar ─────────────────────────────────────────
        self.status_var = tk.StringVar(value="Sẵn sàng")
        tk.Label(self, textvariable=self.status_var,
                 bg="#181825", fg="#7f849c",
                 font=("Segoe UI", 8), anchor="w").pack(
            fill="x", ipady=3, side="bottom")

    # ── Helpers ───────────────────────────────────────────────
    def _pick_bin(self):
        p = filedialog.askopenfilename(
            title="Chọn file .bin PHANTOM",
            filetypes=[("PHANTOM bin", "*.bin"), ("All files", "*.*")],
            initialdir=os.path.dirname(os.path.abspath(__file__)))
        if p:
            self.var_bin.set(p)
            # Auto-fill output folder cạnh file .bin
            self.var_out.set(os.path.join(os.path.dirname(p), "output"))

    def _pick_key(self):
        p = filedialog.askopenfilename(
            title="Chọn file phantom.key",
            filetypes=[("Key file", "*.key"), ("All files", "*.*")],
            initialdir=os.path.dirname(os.path.abspath(__file__)))
        if p: self.var_key.set(p)

    def _pick_out(self):
        p = filedialog.askdirectory(
            title="Chọn thư mục lưu file giải mã",
            initialdir=self.var_out.get())
        if p: self.var_out.set(p)

    def _open_output(self):
        d = self.var_out.get()
        if os.path.isdir(d):
            os.startfile(d) if sys.platform == "win32" else subprocess.Popen(["xdg-open", d])
        else:
            messagebox.showinfo("Thư mục chưa tồn tại",
                                "Chưa có file nào được giải mã vào thư mục này.")

    def _log(self, msg: str):
        """Thread-safe log vào text box."""
        def _append():
            self.log_box.config(state="normal")
            if msg.startswith("    ✓") or msg.startswith("✔"):
                tag = "ok"
            elif msg.startswith("    ✗") or "Lỗi" in msg or "ERROR" in msg:
                tag = "err"
            elif msg.startswith("["):
                tag = "info"
            else:
                tag = "dim"
            self.log_box.insert("end", msg + "\n", tag)
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _append)

    def _set_status(self, msg: str):
        self.after(0, lambda: self.status_var.set(msg))

    def _check_deps(self):
        if not CRYPTO_OK:
            self._log("✗  Thiếu thư viện 'cryptography'. Chạy: pip install cryptography")
            self._log("   Sau đó khởi động lại ứng dụng.")
            self.btn_decrypt.config(state="disabled")

    # ── Decrypt logic ─────────────────────────────────────────
    def _start_decrypt(self):
        bin_path = self.var_bin.get().strip()
        key_path = self.var_key.get().strip()
        out_dir  = self.var_out.get().strip()

        if not bin_path or not os.path.isfile(bin_path):
            messagebox.showerror("Lỗi", "Vui lòng chọn file .bin hợp lệ.")
            return
        if not key_path or not os.path.isfile(key_path):
            messagebox.showerror("Lỗi", "Vui lòng chọn file phantom.key hợp lệ.")
            return
        if not out_dir:
            messagebox.showerror("Lỗi", "Vui lòng chọn thư mục output.")
            return

        # Clear log
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

        self.btn_decrypt.config(state="disabled")
        self.progress.start(10)
        self._set_status("Đang giải mã…")

        self._log(f"File .bin : {bin_path}")
        self._log(f"Key file  : {key_path}")
        self._log(f"Output    : {out_dir}")
        self._log("─" * 60)

        def _worker():
            try:
                results = unpack_bin(bin_path, key_path, out_dir, log_cb=self._log)
                ok  = sum(1 for r in results if r[3])
                err = len(results) - ok
                self._log("\n" + "─" * 60)
                self._log(f"✔  Hoàn tất: {ok} file OK,  {err} lỗi")
                self._set_status(f"Xong — {ok}/{len(results)} file giải mã thành công")
                if ok > 0:
                    self.after(0, lambda: messagebox.showinfo(
                        "Giải mã hoàn tất",
                        f"✓  {ok} file đã giải mã thành công\n"
                        f"📂 Lưu tại: {out_dir}"))
            except Exception as e:
                self._log(f"\n✗  LỖI: {e}")
                self._set_status(f"Lỗi: {e}")
                self.after(0, lambda: messagebox.showerror("Lỗi giải mã", str(e)))
            finally:
                self.after(0, self.progress.stop)
                self.after(0, lambda: self.btn_decrypt.config(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    app = PhantomDecryptUI()
    app.mainloop()
