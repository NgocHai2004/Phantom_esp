import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import serial
import serial.tools.list_ports
import threading
import time
import base64
import os

BAUD = 115200


class ESP32MicSDTester:
    def __init__(self, root):
        self.root = root
        self.root.title("ESP32 Mic + SD Card Tester")
        self.root.geometry("900x600")

        self.ser = None
        self.reader_thread = None
        self.running = False
        self.downloading = False
        self.file_list = []

        self.build_ui()
        self.refresh_ports()

    def build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="COM Port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=15, state="readonly")
        self.port_combo.pack(side="left", padx=5)

        ttk.Button(top, text="Refresh", command=self.refresh_ports).pack(side="left", padx=5)
        ttk.Button(top, text="Connect", command=self.connect).pack(side="left", padx=5)
        ttk.Button(top, text="Disconnect", command=self.disconnect).pack(side="left", padx=5)

        cmd = ttk.Frame(self.root, padding=10)
        cmd.pack(fill="x")

        ttk.Button(cmd, text="SD Info", command=lambda: self.send_cmd("sdinfo")).pack(side="left", padx=5)
        ttk.Button(cmd, text="List Files", command=lambda: self.send_cmd("ls")).pack(side="left", padx=5)
        ttk.Button(cmd, text="Record 5s", command=self.record_audio).pack(side="left", padx=5)
        ttk.Button(cmd, text="Download Selected", command=self.download_selected).pack(side="left", padx=5)

        mid = ttk.Panedwindow(self.root, orient="horizontal")
        mid.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Frame(mid)
        right = ttk.Frame(mid)
        mid.add(left, weight=1)
        mid.add(right, weight=2)

        ttk.Label(left, text="Files on SD").pack(anchor="w")
        self.filebox = tk.Listbox(left, height=20)
        self.filebox.pack(fill="both", expand=True)

        ttk.Label(right, text="Log").pack(anchor="w")
        self.log_text = tk.Text(right, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def log(self, msg):
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.root.update_idletasks()

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports:
            current = self.port_var.get().strip()
            if not current or current not in ports:
                self.port_var.set(ports[0])

    def connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Chọn COM port trước")
            return

        try:
            if self.ser and self.ser.is_open:
                self.disconnect()

            self.ser = serial.Serial(port, BAUD, timeout=1)

            # reset ESP32 cho sạch session
            self.ser.setDTR(False)
            self.ser.setRTS(True)
            time.sleep(0.2)
            self.ser.setRTS(False)
            time.sleep(0.2)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()

            self.running = True
            self.reader_thread = threading.Thread(target=self.read_loop, daemon=True)
            self.reader_thread.start()

            self.log(f"[*] Connected {port}")
        except Exception as e:
            self.ser = None
            messagebox.showerror("Connect error", str(e))

    def disconnect(self):
        self.running = False
        self.downloading = False

        if self.ser:
            try:
                if self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass
            self.ser = None

        self.log("[*] Disconnected")

    def send_cmd(self, cmd):
        if not self.ser or not self.ser.is_open:
            messagebox.showerror("Error", "Chưa connect")
            return

        if self.downloading:
            messagebox.showwarning("Busy", "Đang tải file, chờ xong rồi thử lại")
            return

        try:
            self.ser.write((cmd + "\n").encode("utf-8"))
            self.ser.flush()
            self.log(f">>> {cmd}")
        except Exception as e:
            messagebox.showerror("Serial error", str(e))

    def record_audio(self):
        self.send_cmd("rec")

    def read_loop(self):
        in_ls = False

        while self.running and self.ser and self.ser.is_open:
            try:
                if self.downloading:
                    time.sleep(0.05)
                    continue

                raw = self.ser.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue

                self.log(line)

                if line == "[LS_BEGIN]":
                    self.file_list.clear()
                    self.filebox.delete(0, "end")
                    in_ls = True
                    continue

                if line == "[LS_END]":
                    in_ls = False
                    continue

                if in_ls:
                    parts = line.split("\t")
                    if len(parts) >= 1:
                        filename = parts[0].strip()
                        self.file_list.append(filename)
                        self.filebox.insert("end", line)

            except Exception as e:
                if self.running:
                    self.log(f"[ERR] read_loop: {e}")
                break

    def download_selected(self):
        if not self.ser or not self.ser.is_open:
            messagebox.showerror("Error", "Chưa connect")
            return

        sel = self.filebox.curselection()
        if not sel:
            messagebox.showerror("Error", "Chọn file trong danh sách trước")
            return

        raw_line = self.filebox.get(sel[0])
        filename = raw_line.split("\t")[0].strip()

        save_path = filedialog.asksaveasfilename(
            title="Lưu WAV",
            defaultextension=".wav",
            initialfile=os.path.basename(filename),
            filetypes=[("WAV files", "*.wav")]
        )
        if not save_path:
            return

        threading.Thread(
            target=self.download_file,
            args=(filename, save_path),
            daemon=True
        ).start()

    def download_file(self, remote_name, save_path):
        if not self.ser or not self.ser.is_open:
            self.log("[ERR] Chưa connect")
            return

        try:
            self.downloading = True
            time.sleep(0.2)  # cho read_loop nhả serial

            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()

            cmd = f"dump {remote_name}\n"
            self.ser.write(cmd.encode("utf-8"))
            self.ser.flush()
            self.log(f">>> dump {remote_name}")

            expected_bytes = 0
            wav_data = bytearray()
            state = "WAIT"
            got_begin = False
            got_end = False
            start = time.time()
            last_progress = -1

            while time.time() - start < 90:
                raw = self.ser.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue

                if state == "WAIT":
                    if line.startswith("WAV_BEGIN"):
                        got_begin = True
                        try:
                            expected_bytes = int(line.split()[1])
                        except Exception:
                            expected_bytes = 0

                        self.log(f"[*] Receiving {remote_name}, expected {expected_bytes} bytes")
                        state = "STREAM"
                    else:
                        self.log(f"[ESP] {line}")

                elif state == "STREAM":
                    if line == "WAV_END":
                        got_end = True
                        break

                    # bỏ qua log chen vào giữa stream nếu có
                    if line.startswith("["):
                        self.log(f"[ESP] {line}")
                        continue

                    try:
                        chunk = base64.b64decode(line, validate=False)
                        if chunk:
                            wav_data.extend(chunk)

                            if expected_bytes > 0:
                                progress = int(len(wav_data) * 100 / expected_bytes)
                                if progress != last_progress and progress % 5 == 0:
                                    self.log(f"... {len(wav_data)}/{expected_bytes} bytes ({progress}%)")
                                    last_progress = progress
                    except Exception as e:
                        self.log(f"[decode err] {e}: {line[:80]}")

            if not got_begin:
                self.log("[ERR] Khong nhan duoc WAV_BEGIN")
                messagebox.showerror("Download error", "Không nhận được WAV_BEGIN từ ESP32")
                return

            if not got_end:
                self.log("[ERR] Khong nhan duoc WAV_END")
                messagebox.showerror("Download error", "Không nhận được WAV_END từ ESP32")
                return

            if len(wav_data) == 0:
                self.log("[ERR] File rong, khong co du lieu")
                messagebox.showerror("Download error", "File tải về rỗng")
                return

            if expected_bytes and len(wav_data) != expected_bytes:
                self.log(f"[WARN] size mismatch: got {len(wav_data)}, expected {expected_bytes}")

            with open(save_path, "wb") as f:
                f.write(wav_data)

            self.log(f"[✓] Saved {len(wav_data)} bytes -> {save_path}")

        except Exception as e:
            self.log(f"[ERR] download_file: {e}")
            messagebox.showerror("Download error", str(e))

        finally:
            self.downloading = False

    def on_close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = ESP32MicSDTester(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()