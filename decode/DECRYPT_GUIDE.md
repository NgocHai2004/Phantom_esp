# Hướng dẫn giải mã file `.bin` từ PHANTOM

## Tổng quan

File `.bin` được tạo bởi PHANTOM sử dụng **mã hóa 3 lớp**:

```
Layer 1 — AES-256-GCM         (mã hóa từng file)
Layer 2 — HMAC-SHA-256         (xác thực tính toàn vẹn)
Layer 3 — ChaCha20-Poly1305    (bọc toàn bộ)
```

Để giải mã, bạn cần:
- File **`phantom.key`** (do người gửi cung cấp — 32 bytes ngẫu nhiên)
- File **`phantom_YYYYMMDD_HHMMSS.bin`** (file nhận được)
- Python 3.8+ với thư viện `cryptography`

---

## Cài đặt

```bash
pip install cryptography
```

---

## Cấu trúc file `.bin`

| Offset | Kích thước | Nội dung |
|--------|-----------|----------|
| 0      | 4 bytes   | Magic: `PHTM` |
| 4      | 4 bytes   | Version: `2` (uint32 LE) |
| 8      | 16 bytes  | MD5 checksum của payload |
| 24     | 4 bytes   | Độ dài payload (uint32 LE) |
| 28     | N bytes   | Payload: ZIP chứa các file `.enc` |

Mỗi file `.enc` bên trong ZIP là 1 file gốc đã qua mã hóa 3 lớp.

---

## Script giải mã (`decrypt.py`)

Lưu đoạn code sau thành file `decrypt.py` rồi chạy:

```python
import struct, zipfile, hashlib, io, os, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives import hmac as crypto_hmac, hashes
from cryptography.hazmat.backends import default_backend

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

    # Bóc Layer 3: ChaCha20-Poly1305
    n_cha  = enc[:12]
    ct3    = enc[12:]
    payload = ChaCha20Poly1305(k_chacha).decrypt(n_cha, ct3, None)
    # payload = nonce_aes(12) + ct1 + hmac_tag(32)

    hmac_tag = payload[-32:]
    inner    = payload[:-32]   # nonce_aes + ct1

    # Xác thực Layer 2: HMAC-SHA-256
    h = crypto_hmac.HMAC(k_hmac, hashes.SHA256(), backend=default_backend())
    h.update(inner)
    h.verify(hmac_tag)         # ném InvalidSignature nếu sai

    # Bóc Layer 1: AES-256-GCM
    n_aes = inner[:12]
    ct1   = inner[12:]
    return AESGCM(k_aes).decrypt(n_aes, ct1, None)


def unpack_bin(bin_path: str, key_path: str, out_dir: str):
    raw = open(bin_path, "rb").read()

    # Kiểm tra header
    if raw[:4] != BIN_MAGIC:
        raise ValueError("Không phải file PHANTOM (.bin magic sai)")
    version = struct.unpack_from("<I", raw, 4)[0]
    if version != BIN_VERSION:
        raise ValueError(f"Version không hỗ trợ: {version} (cần {BIN_VERSION})")

    md5_stored = raw[8:24]
    payload_len = struct.unpack_from("<I", raw, 24)[0]
    payload = raw[28 : 28 + payload_len]

    # Xác minh MD5
    md5_actual = hashlib.md5(payload).digest()
    if md5_actual != md5_stored:
        raise ValueError("MD5 checksum không khớp — file có thể bị hỏng")

    master = load_key(key_path)
    os.makedirs(out_dir, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for entry in zf.namelist():
            enc_data = zf.read(entry)
            original_name = entry.removesuffix(".enc")   # bỏ đuôi .enc
            print(f"  Giải mã: {entry}  →  {original_name}")
            try:
                plain = decrypt_3layer(enc_data, master)
                out_path = os.path.join(out_dir, original_name)
                with open(out_path, "wb") as f:
                    f.write(plain)
                print(f"  ✓  Đã lưu: {out_path}  ({len(plain):,} bytes)")
            except Exception as e:
                print(f"  ✗  Lỗi giải mã {entry}: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Cách dùng:")
        print("  python decrypt.py <file.bin> <phantom.key> [output_folder]")
        print()
        print("Ví dụ:")
        print("  python decrypt.py phantom_20260408_103000.bin phantom.key ./output")
        sys.exit(1)

    bin_file  = sys.argv[1]
    key_file  = sys.argv[2]
    out_folder = sys.argv[3] if len(sys.argv) > 3 else "output"

    print(f"File .bin : {bin_file}")
    print(f"Key file  : {key_file}")
    print(f"Output    : {out_folder}")
    print()

    try:
        unpack_bin(bin_file, key_file, out_folder)
        print()
        print("Giải mã hoàn tất!")
    except Exception as e:
        print(f"\nLỗi: {e}")
        sys.exit(1)
```

---

## Cách chạy

```bash
# Cú pháp
python decrypt.py <file.bin> <phantom.key> [thư_mục_output]

# Ví dụ
python decrypt.py phantom_20260408_103000.bin phantom.key ./output
```

Kết quả:
```
File .bin : phantom_20260408_103000.bin
Key file  : phantom.key
Output    : ./output

  Giải mã: document.pdf.enc  →  document.pdf
  ✓  Đã lưu: ./output/document.pdf  (245,760 bytes)
  Giải mã: audio.wav.enc     →  audio.wav
  ✓  Đã lưu: ./output/audio.wav     (1,048,576 bytes)

Giải mã hoàn tất!
```

---

## Lưu ý bảo mật

> **Giữ bí mật file `phantom.key`** — bất kỳ ai có file này đều có thể giải mã toàn bộ dữ liệu.
> Chỉ chia sẻ qua kênh bảo mật (gặp trực tiếp, USB, hoặc kênh mã hóa end-to-end).

- Mỗi file `.bin` dùng **nonce ngẫu nhiên** khác nhau mỗi lần mã hóa — không bao giờ lặp lại.
- Script sẽ **báo lỗi ngay** nếu file bị giả mạo hoặc chỉnh sửa (HMAC xác thực thất bại).
- Tương thích Python 3.8 trở lên trên Windows, macOS, Linux.
