import tkinter as tk
from tkinter import filedialog, messagebox
from Crypto.Cipher import AES
from pathlib import Path

MAGIC = b"PHGCM1\x00\x00"

def decrypt_file(input_path, output_path, key_hex):
    key = bytes.fromhex(key_hex)

    with open(input_path, "rb") as f:
        magic = f.read(8)
        if magic != MAGIC:
            raise Exception("Sai định dạng file")

        iv = f.read(12)
        tag = f.read(16)
        ciphertext = f.read()

    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)

    with open(output_path, "wb") as f:
        f.write(plaintext)


def choose_file():
    file_path = filedialog.askopenfilename(filetypes=[("ENC files", "*.enc")])
    if file_path:
        entry_file.delete(0, tk.END)
        entry_file.insert(0, file_path)


def run_decrypt():
    file_path = entry_file.get()
    key_input = entry_key.get().strip()

    if not file_path or not key_input:
        messagebox.showerror("Lỗi", "Chọn file và nhập key")
        return

    try:
        # Nếu user nhập 88888888 → tự convert sang SHA256
        if len(key_input) == 8:
            import hashlib
            key_hex = hashlib.sha256(key_input.encode()).hexdigest()
        else:
            key_hex = key_input

        output_path = file_path.replace(".enc", "")
        decrypt_file(file_path, output_path, key_hex)

        messagebox.showinfo("OK", f"Đã giải mã:\n{output_path}")

    except Exception as e:
        messagebox.showerror("Lỗi", str(e))


# UI
root = tk.Tk()
root.title("Phantom Decrypt Tool")

tk.Label(root, text="File .enc").pack()
entry_file = tk.Entry(root, width=50)
entry_file.pack()

tk.Button(root, text="Chọn file", command=choose_file).pack()

tk.Label(root, text="Key (nhập 88888888 hoặc key hex)").pack()
entry_key = tk.Entry(root, width=50)
entry_key.pack()

tk.Button(root, text="Giải mã", command=run_decrypt).pack()

root.mainloop()