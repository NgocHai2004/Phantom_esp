#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import json
import threading
from pathlib import Path
from contextlib import ExitStack

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    import requests
except ImportError:
    requests = None


# Các giá trị nhạy cảm được ẩn khỏi giao diện.
# Lưu ý: đây chỉ là che trong app/client, không phải bảo mật tuyệt đối.
_HIDDEN_DEVICE = "aHR0cDovLzEwLjQyLjAuMTo4NzY1"  # http://10.42.0.1:8765
_HIDDEN_KEY = ""  # Nếu ESP32 có token, base64 token rồi đặt vào đây.

_ENDPOINTS = {
    "status": "L2FwaS9zdGF0dXM=",
    "list": "L2FwaS9maWxlbGlzdA==",
    "upload_one": "L2FwaS91cGxvYWQ=",
    "upload_many": "L2FwaS91cGxvYWQtYWxs",
    "download": "L2FwaS9kb3dubG9hZA==",
    "delete_one": "L2FwaS9kZWxldGU=",
    "delete_all": "L2FwaS9kZWxldGUtYWxs",
}


class AutoPrivateSDManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Private SD Manager")
        self.geometry("850x590")
        self.minsize(780, 530)

        self.files_data = []
        self.connected = False
        self.busy = False

        self._build_ui()
        self._set_connection_state(False, "Chưa kiểm tra kết nối.")
        self._set_status("Mở app xong sẽ tự kiểm tra thiết bị.")

        if requests is None:
            messagebox.showerror(
                "Thiếu thư viện",
                "Máy chưa có thư viện requests.\n\nCài bằng lệnh:\npip install requests"
            )
        else:
            self.after(500, self.check_status)

    # ===================== UI =====================
    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        cfg = ttk.LabelFrame(root, text="Kết nối thiết bị", padding=10)
        cfg.pack(fill=tk.X)

        self.connection_var = tk.StringVar(value="Chưa kết nối")
        ttk.Label(cfg, text="Trạng thái:").pack(side=tk.LEFT)
        ttk.Label(cfg, textvariable=self.connection_var, width=45).pack(side=tk.LEFT, padx=8)
        ttk.Button(cfg, text="Kết nối lại", command=self.check_status).pack(side=tk.LEFT, padx=4)
        ttk.Button(cfg, text="Làm mới file", command=self.refresh_files).pack(side=tk.LEFT, padx=4)

        actions = ttk.LabelFrame(root, text="Thao tác file", padding=10)
        actions.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(actions, text="Upload 1 file", command=self.upload_one_file).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Upload nhiều file", command=self.upload_many_files).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Tải file đã chọn", command=self.download_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Xóa file đã chọn", command=self.delete_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Xóa tất cả", command=self.delete_all).pack(side=tk.LEFT, padx=4)

        info = ttk.LabelFrame(root, text="Thông tin", padding=10)
        info.pack(fill=tk.X, pady=(10, 0))

        self.safe_info_var = tk.StringVar(value="Chưa kết nối.")
        ttk.Label(info, textvariable=self.safe_info_var, anchor=tk.W, justify=tk.LEFT).pack(fill=tk.X)

        files_frame = ttk.LabelFrame(root, text="Danh sách file", padding=10)
        files_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        columns = ("name", "size_human")
        self.tree = ttk.Treeview(files_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Tên file")
        self.tree.heading("size_human", text="Dung lượng")

        self.tree.column("name", width=560, anchor=tk.W)
        self.tree.column("size_human", width=130, anchor=tk.CENTER)

        yscroll = ttk.Scrollbar(files_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.status_var = tk.StringVar()
        status_bar = ttk.Label(root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=6)
        status_bar.pack(fill=tk.X, pady=(10, 0))

    # ===================== Helpers =====================
    def _decode(self, value):
        if not value:
            return ""
        return base64.b64decode(value).decode("utf-8")

    def _endpoint(self, name):
        return self._decode(_ENDPOINTS[name])

    def _base_url(self):
        return self._decode(_HIDDEN_DEVICE).rstrip("/")

    def _api_url(self, endpoint_name):
        return self._base_url() + self._endpoint(endpoint_name)

    def _headers(self):
        key = self._decode(_HIDDEN_KEY).strip()
        if not key:
            return {}
        return {"X-App-Key": key}

    def _set_status(self, msg):
        self.status_var.set(msg)

    def _set_safe_info(self, msg):
        self.safe_info_var.set(msg)

    def _set_connection_state(self, ok, info=None):
        self.connected = bool(ok)
        if ok:
            self.connection_var.set("Đã kết nối đúng WiFi / thiết bị sẵn sàng")
            if info:
                self._set_safe_info(info)
        else:
            self.connection_var.set("Chưa kết nối thiết bị")
            self._set_safe_info(
                info or "Không kết nối được. Hãy kết nối máy tính vào WiFi của thiết bị rồi bấm Kết nối lại."
            )
            self._clear_tree()

    def _clear_tree(self):
        self.files_data = []
        for row in self.tree.get_children():
            self.tree.delete(row)

    def _run_async(self, title, fn, failure_text=None):
        if self.busy:
            self._set_status("Đang xử lý, vui lòng chờ...")
            return

        def worker():
            self.busy = True
            try:
                self.after(0, lambda: self._set_status(title))
                fn()
            except requests.exceptions.RequestException:
                self.after(0, lambda: self._set_connection_state(False))
                self.after(0, lambda: self._set_status(failure_text or "Kết nối thất bại."))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Lỗi", str(e)))
                self.after(0, lambda: self._set_status("Có lỗi xảy ra."))
            finally:
                self.busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _request_timeout(self):
        return 6

    def _selected_file_name(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Chưa chọn file", "Bạn hãy chọn 1 file trong danh sách.")
            return None

        item = self.tree.item(selected[0])
        values = item.get("values", [])
        if not values:
            return None
        return values[0]

    def _safe_status_summary(self, data):
        if not isinstance(data, dict):
            return "Đã kết nối thiết bị."

        allowed = []
        for key in ("ok", "status", "sd", "mounted", "file_count", "total_bytes", "used_bytes", "free_bytes"):
            if key in data:
                allowed.append(f"{key}: {data.get(key)}")

        if allowed:
            return "Đã kết nối thiết bị.\n" + "\n".join(allowed)

        return "Đã kết nối thiết bị. Thông tin nhạy cảm đã được ẩn."

    def _require_connected(self):
        if self.connected:
            return True
        messagebox.showwarning(
            "Chưa kết nối",
            "Máy chưa kết nối được thiết bị.\n\nHãy kết nối đúng WiFi của thiết bị rồi bấm Kết nối lại."
        )
        return False

    # ===================== API actions =====================
    def check_status(self):
        if requests is None:
            messagebox.showerror("Thiếu thư viện", "Cài: pip install requests")
            return

        def task():
            r = requests.get(
                self._api_url("status"),
                headers=self._headers(),
                timeout=self._request_timeout()
            )
            r.raise_for_status()

            try:
                data = r.json()
            except Exception:
                data = {}

            def update_ui():
                self._set_connection_state(True, self._safe_status_summary(data))
                self._set_status("Kết nối thành công.")
                self.refresh_files()

            self.after(0, update_ui)

        self._run_async(
            "Đang kiểm tra kết nối thiết bị...",
            task,
            failure_text="Kết nối thất bại. Kiểm tra lại WiFi rồi thử lại."
        )

    def refresh_files(self):
        if requests is None:
            messagebox.showerror("Thiếu thư viện", "Cài: pip install requests")
            return

        def task():
            r = requests.get(
                self._api_url("list"),
                headers=self._headers(),
                timeout=self._request_timeout()
            )
            r.raise_for_status()

            data = r.json()
            files = data.get("files", [])

            def update_ui():
                self._set_connection_state(True)
                self.files_data = files
                for row in self.tree.get_children():
                    self.tree.delete(row)

                for f in files:
                    self.tree.insert(
                        "",
                        tk.END,
                        values=(
                            f.get("name", ""),
                            f.get("size_human", ""),
                        )
                    )

                self._set_safe_info(f"Đã kết nối thiết bị.\nSố file: {len(files)}")
                self._set_status(f"Đã làm mới danh sách: {len(files)} file.")

            self.after(0, update_ui)

        self._run_async(
            "Đang tải danh sách file...",
            task,
            failure_text="Làm mới thất bại. Kiểm tra lại WiFi rồi thử lại."
        )

    def upload_one_file(self):
        if requests is None:
            messagebox.showerror("Thiếu thư viện", "Cài: pip install requests")
            return
        if not self._require_connected():
            return

        file_path = filedialog.askopenfilename(title="Chọn 1 file để upload")
        if not file_path:
            return

        def task():
            path = Path(file_path)
            with open(path, "rb") as f:
                files = {"file": (path.name, f, "application/octet-stream")}
                r = requests.post(
                    self._api_url("upload_one"),
                    headers=self._headers(),
                    files=files,
                    timeout=300
                )
            r.raise_for_status()

            self.after(0, lambda: self._set_safe_info(f"Upload xong: {path.name}"))
            self.after(0, lambda: self._set_status("Upload thành công."))
            self.after(0, self.refresh_files)

        self._run_async("Đang upload...", task, failure_text="Upload thất bại. Kiểm tra lại WiFi rồi thử lại.")

    def upload_many_files(self):
        if requests is None:
            messagebox.showerror("Thiếu thư viện", "Cài: pip install requests")
            return
        if not self._require_connected():
            return

        file_paths = filedialog.askopenfilenames(title="Chọn nhiều file để upload")
        if not file_paths:
            return

        def task():
            with ExitStack() as stack:
                multipart_files = []
                for fp in file_paths:
                    path = Path(fp)
                    f = stack.enter_context(open(path, "rb"))
                    multipart_files.append(("files", (path.name, f, "application/octet-stream")))

                r = requests.post(
                    self._api_url("upload_many"),
                    headers=self._headers(),
                    files=multipart_files,
                    timeout=600
                )
            r.raise_for_status()

            self.after(0, lambda: self._set_safe_info(f"Upload xong {len(file_paths)} file."))
            self.after(0, lambda: self._set_status("Upload thành công."))
            self.after(0, self.refresh_files)

        self._run_async("Đang upload nhiều file...", task, failure_text="Upload thất bại. Kiểm tra lại WiFi rồi thử lại.")

    def download_selected(self):
        if requests is None:
            messagebox.showerror("Thiếu thư viện", "Cài: pip install requests")
            return
        if not self._require_connected():
            return

        name = self._selected_file_name()
        if not name:
            return

        save_path = filedialog.asksaveasfilename(title="Lưu file tải về", initialfile=name)
        if not save_path:
            return

        def task():
            r = requests.get(
                self._api_url("download"),
                headers=self._headers(),
                params={"name": name},
                stream=True,
                timeout=300
            )
            r.raise_for_status()

            with open(save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            self.after(0, lambda: self._set_safe_info(f"Đã tải về: {name}"))
            self.after(0, lambda: self._set_status("Tải file thành công."))

        self._run_async("Đang tải file...", task, failure_text="Tải file thất bại. Kiểm tra lại WiFi rồi thử lại.")

    def delete_selected(self):
        if requests is None:
            messagebox.showerror("Thiếu thư viện", "Cài: pip install requests")
            return
        if not self._require_connected():
            return

        name = self._selected_file_name()
        if not name:
            return

        ok = messagebox.askyesno("Xác nhận xóa", f"Bạn chắc chắn muốn xóa file này?\n\n{name}")
        if not ok:
            return

        def task():
            r = requests.delete(
                self._api_url("delete_one"),
                headers=self._headers(),
                params={"name": name},
                timeout=self._request_timeout()
            )
            r.raise_for_status()

            self.after(0, lambda: self._set_safe_info(f"Đã xóa: {name}"))
            self.after(0, lambda: self._set_status("Xóa file thành công."))
            self.after(0, self.refresh_files)

        self._run_async("Đang xóa file...", task, failure_text="Xóa file thất bại. Kiểm tra lại WiFi rồi thử lại.")

    def delete_all(self):
        if requests is None:
            messagebox.showerror("Thiếu thư viện", "Cài: pip install requests")
            return
        if not self._require_connected():
            return

        confirm = simpledialog.askstring("Xác nhận bảo mật", "Nhập DELETE để xóa tất cả file:")
        if confirm != "DELETE":
            self._set_status("Đã hủy xóa tất cả.")
            return

        def task():
            r = requests.delete(
                self._api_url("delete_all"),
                headers=self._headers(),
                timeout=120
            )
            r.raise_for_status()

            self.after(0, lambda: self._set_safe_info("Đã xóa tất cả file."))
            self.after(0, lambda: self._set_status("Xóa tất cả thành công."))
            self.after(0, self.refresh_files)

        self._run_async("Đang xóa tất cả...", task, failure_text="Xóa tất cả thất bại. Kiểm tra lại WiFi rồi thử lại.")


if __name__ == "__main__":
    app = AutoPrivateSDManagerApp()
    app.mainloop()
