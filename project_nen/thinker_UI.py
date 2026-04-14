import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading, sys
from pathlib import Path

# Force-insert the local zipfolder package BEFORE any venv-installed version.
# Remove any previously imported zipfolder modules so the correct local
# version is loaded on the very next import.
_HERE = str(Path(__file__).parent)
sys.path.insert(0, _HERE)
for _k in list(sys.modules.keys()):
    if _k == "zipfolder" or _k.startswith("zipfolder."):
        del sys.modules[_k]

from zipfolder.compressor import compress_folder
from zipfolder.decompressor import decompress_folder

BG = "#0f172a"
BG2 = "#1e293b"
ACCENT = "#f59e0b"
GREEN = "#22c55e"
RED = "#ef4444"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"

def fmt(b):
    if b == 0: return "0 B"
    for u, s in [("GB", 1<<30), ("MB", 1<<20), ("KB", 1<<10)]:
        if b >= s: return f"{b/s:.2f} {u}"
    return f"{b} B"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ZipFolder 7-Zip")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry("520x640")
        self.c_folder = None
        self.d_file = None
        self._build()

    def _build(self):
        tk.Label(self, text="ZipFolder", font=("Segoe UI", 22, "bold"),
                 bg=BG, fg=ACCENT).pack(pady=(28, 4))
        tk.Label(self, text="7-Zip LZMA2 + Smart Optimize",
                 font=("Segoe UI", 10), bg=BG, fg=MUTED).pack()
        tab_fr = tk.Frame(self, bg=BG2)
        tab_fr.pack(fill="x", padx=24, pady=16)
        self.btn_c = tk.Button(tab_fr, text="  Compress  ",
            font=("Segoe UI", 10, "bold"), bg=ACCENT, fg="#0f172a",
            relief="flat", bd=0, cursor="hand2", padx=8, pady=6,
            command=lambda: self._tab("compress"))
        self.btn_c.pack(side="left", padx=4, pady=4)
        self.btn_d = tk.Button(tab_fr, text="  Decompress  ",
            font=("Segoe UI", 10, "bold"), bg=BG2, fg=MUTED,
            relief="flat", bd=0, cursor="hand2", padx=8, pady=6,
            command=lambda: self._tab("decompress"))
        self.btn_d.pack(side="left", padx=4, pady=4)
        self.fr_c = tk.Frame(self, bg=BG)
        self.fr_d = tk.Frame(self, bg=BG)
        self._build_compress(self.fr_c)
        self._build_decompress(self.fr_d)
        self.fr_c.pack(fill="both", expand=True, padx=24)

    def _tab(self, t):
        if t == "compress":
            self.fr_d.pack_forget()
            self.fr_c.pack(fill="both", expand=True, padx=24)
            self.btn_c.config(bg=ACCENT, fg="#0f172a")
            self.btn_d.config(bg=BG2, fg=MUTED)
        else:
            self.fr_c.pack_forget()
            self.fr_d.pack(fill="both", expand=True, padx=24)
            self.btn_d.config(bg=ACCENT, fg="#0f172a")
            self.btn_c.config(bg=BG2, fg=MUTED)

    def _build_compress(self, parent):
        self.c_path = tk.StringVar()
        dz = tk.Frame(parent, bg=BG2)
        dz.pack(fill="x", pady=(8, 12))
        tk.Label(dz, text="\U0001f4c1", font=("Segoe UI", 36), bg=BG2).pack(pady=(20, 4))
        tk.Label(dz, text="Click de chon folder",
                 font=("Segoe UI", 11, "bold"), bg=BG2, fg=TEXT).pack()
        tk.Label(dz, text="Click de chon folder can nen",
                 font=("Segoe UI", 9), bg=BG2, fg=MUTED).pack(pady=(2, 12))
        tk.Button(dz, text="Chon Folder",
                  font=("Segoe UI", 10, "bold"), bg=ACCENT, fg="#0f172a",
                  relief="flat", bd=0, padx=20, pady=8,
                  cursor="hand2", command=self._pick_folder).pack(pady=(0, 20))
        self.c_lbl = tk.Label(parent, textvariable=self.c_path,
                              font=("Segoe UI", 9), bg=BG, fg=MUTED, wraplength=460)
        self.c_lbl.pack()
        opt_fr = tk.Frame(parent, bg=BG2)
        opt_fr.pack(fill="x", pady=8)
        tk.Label(opt_fr, text="Smart Optimize",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=TEXT).pack(side="left", padx=14, pady=12)
        tk.Label(opt_fr, text="Toi uu anh/video/docs truoc khi nen",
                 font=("Segoe UI", 8), bg=BG2, fg=MUTED).pack(side="left")
        self.opt_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt_fr, variable=self.opt_var, bg=BG2,
                       activebackground=BG2, fg=GREEN, selectcolor=BG2,
                       font=("Segoe UI", 14)).pack(side="right", padx=14)
        self.c_btn = tk.Button(parent, text="\U0001f5dc Nen voi 7-Zip LZMA2",
            font=("Segoe UI", 12, "bold"), bg=ACCENT, fg="#0f172a",
            relief="flat", bd=0, pady=14, cursor="hand2", state="disabled",
            command=self._do_compress)
        self.c_btn.pack(fill="x", pady=12)
        self.c_prog = ttk.Progressbar(parent, mode="indeterminate", length=460)
        self.c_status = tk.Label(parent, text="", font=("Segoe UI", 9),
                                 bg=BG, fg=MUTED, wraplength=460)
        self.c_status.pack()

    def _build_decompress(self, parent):
        self.d_path = tk.StringVar()
        dz = tk.Frame(parent, bg=BG2)
        dz.pack(fill="x", pady=(8, 12))
        tk.Label(dz, text="\U0001f4e6", font=("Segoe UI", 36), bg=BG2).pack(pady=(20, 4))
        tk.Label(dz, text="Click de chon file .zfld",
                 font=("Segoe UI", 11, "bold"), bg=BG2, fg=TEXT).pack()
        tk.Label(dz, text="File se duoc giai nen ve folder goc",
                 font=("Segoe UI", 9), bg=BG2, fg=MUTED).pack(pady=(2, 12))
        tk.Button(dz, text="Chon File .zfld",
                  font=("Segoe UI", 10, "bold"), bg="#8b5cf6", fg="#fff",
                  relief="flat", bd=0, padx=20, pady=8,
                  cursor="hand2", command=self._pick_zfld).pack(pady=(0, 20))
        self.d_lbl = tk.Label(parent, textvariable=self.d_path,
                              font=("Segoe UI", 9), bg=BG, fg=MUTED, wraplength=460)
        self.d_lbl.pack()
        self.d_btn = tk.Button(parent, text="\U0001f513 Giai nen file",
            font=("Segoe UI", 12, "bold"), bg="#8b5cf6", fg="#fff",
            relief="flat", bd=0, pady=14, cursor="hand2", state="disabled",
            command=self._do_decompress)
        self.d_btn.pack(fill="x", pady=12)
        self.d_prog = ttk.Progressbar(parent, mode="indeterminate", length=460)
        self.d_status = tk.Label(parent, text="", font=("Segoe UI", 9),
                                 bg=BG, fg=MUTED, wraplength=460)
        self.d_status.pack()

    def _pick_folder(self):
        p = filedialog.askdirectory(title="Chon folder can nen")
        if p:
            self.c_folder = p
            self.c_path.set(f"\U0001f4c1 {Path(p).name}  ({p})")
            self.c_btn.config(state="normal")
            self.c_status.config(text="", fg=MUTED)

    def _pick_zfld(self):
        p = filedialog.askopenfilename(title="Chon file .zfld",
            filetypes=[("ZipFolder", "*.zfld"), ("All", "*.*")])
        if p:
            self.d_file = p
            self.d_path.set(f"\U0001f4e6 {Path(p).name}  ({p})")
            self.d_btn.config(state="normal")
            self.d_status.config(text="", fg=MUTED)

    def _do_compress(self):
        if not self.c_folder: return
        out = filedialog.asksaveasfilename(title="Luu file .zfld",
            defaultextension=".zfld",
            initialfile=Path(self.c_folder).name + ".zfld",
            filetypes=[("ZipFolder", "*.zfld")])
        if not out: return
        self.c_btn.config(state="disabled")
        self.c_prog.pack(fill="x", pady=4)
        self.c_prog.start(10)
        self.c_status.config(text="Dang nen voi 7-Zip LZMA2...", fg=MUTED)
        threading.Thread(target=self._run_compress,
            args=(self.c_folder, out, self.opt_var.get()), daemon=True).start()

    def _run_compress(self, folder, out, opt):
        try:
            # Exclude .zfld files when calculating original size — they are
            # previous output files that will be excluded from the archive too.
            orig = sum(f.stat().st_size for f in Path(folder).rglob("*")
                       if f.is_file() and f.suffix.lower() != ".zfld")
            compress_folder(folder, out, optimize=opt, algorithm="7z")
            csize = Path(out).stat().st_size
            ratio = round((1 - csize / orig) * 100, 1) if orig > 0 else 0
            msg = f"Done! {fmt(orig)} -> {fmt(csize)} (giam {ratio}%)"
            self.after(0, lambda: self.c_status.config(text=msg, fg=GREEN))
            self.after(0, lambda: messagebox.showinfo("Hoan tat",
                f"Nen thanh cong!\n{fmt(orig)} -> {fmt(csize)}\nGiam {ratio}%\n\nFile: {out}"))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.c_status.config(text=f"Loi: {err}", fg=RED))
            self.after(0, lambda: messagebox.showerror("Loi", err))
        finally:
            self.after(0, self.c_prog.stop)
            self.after(0, self.c_prog.pack_forget)
            self.after(0, lambda: self.c_btn.config(state="normal"))

    def _do_decompress(self):
        if not self.d_file: return
        out = filedialog.askdirectory(title="Chon thu muc giai nen")
        if not out: return
        self.d_btn.config(state="disabled")
        self.d_prog.pack(fill="x", pady=4)
        self.d_prog.start(10)
        self.d_status.config(text="Dang giai nen...", fg=MUTED)
        threading.Thread(target=self._run_decompress,
            args=(self.d_file, out), daemon=True).start()

    def _run_decompress(self, f, out):
        try:
            result = decompress_folder(f, out)
            sz = sum(x.stat().st_size for x in Path(result).rglob("*") if x.is_file())
            msg = f"Done! Folder: {result.name} ({fmt(sz)})"
            self.after(0, lambda: self.d_status.config(text=msg, fg=GREEN))
            self.after(0, lambda: messagebox.showinfo("Hoan tat",
                f"Giai nen thanh cong!\nFolder: {result}\nKich thuoc: {fmt(sz)}"))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.d_status.config(text=f"Loi: {err}", fg=RED))
            self.after(0, lambda: messagebox.showerror("Loi", err))
        finally:
            self.after(0, self.d_prog.stop)
            self.after(0, self.d_prog.pack_forget)
            self.after(0, lambda: self.d_btn.config(state="normal"))


if __name__ == "__main__":
    App().mainloop()
