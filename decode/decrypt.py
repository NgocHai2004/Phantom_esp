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

    # Unpeel Layer 3: ChaCha20-Poly1305
    n_cha  = enc[:12]
    ct3    = enc[12:]
    payload = ChaCha20Poly1305(k_chacha).decrypt(n_cha, ct3, None)

    hmac_tag = payload[-32:]
    inner    = payload[:-32]

    # Verify Layer 2: HMAC-SHA-256
    h = crypto_hmac.HMAC(k_hmac, hashes.SHA256(), backend=default_backend())
    h.update(inner)
    h.verify(hmac_tag)

    # Unpeel Layer 1: AES-256-GCM
    n_aes = inner[:12]
    ct1   = inner[12:]
    return AESGCM(k_aes).decrypt(n_aes, ct1, None)


def unpack_bin(bin_path: str, key_path: str, out_dir: str):
    raw = open(bin_path, "rb").read()

    if raw[:4] != BIN_MAGIC:
        raise ValueError("Không phải file PHANTOM (.bin magic sai)")
    version = struct.unpack_from("<I", raw, 4)[0]
    if version != BIN_VERSION:
        raise ValueError(f"Version không hỗ trợ: {version} (cần {BIN_VERSION})")

    md5_stored = raw[8:24]
    payload_len = struct.unpack_from("<I", raw, 24)[0]
    payload = raw[28 : 28 + payload_len]

    md5_actual = hashlib.md5(payload).digest()
    if md5_actual != md5_stored:
        raise ValueError("MD5 checksum không khớp — file có thể bị hỏng")

    master = load_key(key_path)
    os.makedirs(out_dir, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for entry in zf.namelist():
            enc_data = zf.read(entry)
            original_name = entry.removesuffix(".enc")
            print(f"  Giải mã: {entry}  →  {original_name}")
            try:
                plain = decrypt_3layer(enc_data, master)
                out_path = os.path.join(out_dir, original_name)
                with open(out_path, "wb") as f:
                    f.write(plain)
                print(f"  OK  Đã lưu: {out_path}  ({len(plain):,} bytes)")
            except Exception as e:
                print(f"  LOI  Lỗi giải mã {entry}: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Cách dùng:")
        print("  python decrypt.py <file.bin> <phantom.key> [output_folder]")
        sys.exit(1)

    bin_file   = sys.argv[1]
    key_file   = sys.argv[2]
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
