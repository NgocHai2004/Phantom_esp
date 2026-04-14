"""Binary format definition for .zfld compressed folder files."""

import struct

MAGIC = b"ZFLD"
VERSION = 2  # v2: added flags byte

ALGO_GZIP = 0
ALGO_BZ2 = 1
ALGO_LZMA = 2
ALGO_ZSTD = 3
ALGO_7Z = 4

ALGO_NAMES = {
    ALGO_GZIP: "gzip",
    ALGO_BZ2: "bz2",
    ALGO_LZMA: "lzma",
    ALGO_ZSTD: "zstd",
    ALGO_7Z: "7z",
}

FLAG_OPTIMIZED = 0x01  # bit 0: files were optimized before compression

# Header v2: MAGIC(4) + VERSION(1) + ALGO(1) + FLAGS(1) + NAME_LEN(2) + NAME(N)
HEADER_PREFIX_FORMAT = ">4sBBBH"
HEADER_PREFIX_SIZE = struct.calcsize(HEADER_PREFIX_FORMAT)  # 9 bytes


def write_header(f, algorithm_id: int, folder_name: str, flags: int = 0) -> None:
    """Write the .zfld file header."""
    name_bytes = folder_name.encode("utf-8")
    f.write(struct.pack(HEADER_PREFIX_FORMAT, MAGIC, VERSION, algorithm_id, flags, len(name_bytes)))
    f.write(name_bytes)


def read_header(f) -> tuple[int, str, int]:
    """Read and validate the .zfld file header.

    Returns (algorithm_id, folder_name, flags).
    Supports both v1 (no flags) and v2 (with flags) headers.
    """
    # Peek at first 8 bytes to detect version
    start = f.tell()
    peek = f.read(9)
    f.seek(start)

    if len(peek) < 8:
        raise ValueError("File too small to be a valid .zfld file")

    magic = peek[:4]
    if magic != MAGIC:
        raise ValueError(f"Invalid file: expected magic {MAGIC!r}, got {magic!r}")

    version = peek[4]

    if version == 1:
        # v1 format: MAGIC(4) + VERSION(1) + ALGO(1) + NAME_LEN(2) -- no flags
        v1_format = ">4sBBH"
        v1_size = struct.calcsize(v1_format)
        prefix = f.read(v1_size)
        _, _, algorithm_id, name_len = struct.unpack(v1_format, prefix)
        flags = 0
    elif version == 2:
        # v2 format: MAGIC(4) + VERSION(1) + ALGO(1) + FLAGS(1) + NAME_LEN(2)
        prefix = f.read(HEADER_PREFIX_SIZE)
        if len(prefix) < HEADER_PREFIX_SIZE:
            raise ValueError("Truncated v2 header")
        _, _, algorithm_id, flags, name_len = struct.unpack(HEADER_PREFIX_FORMAT, prefix)
    else:
        raise ValueError(f"Unsupported version: {version}")

    if algorithm_id not in ALGO_NAMES:
        raise ValueError(f"Unknown algorithm ID: {algorithm_id}")

    name_bytes = f.read(name_len)
    if len(name_bytes) < name_len:
        raise ValueError("Truncated header: folder name incomplete")

    return algorithm_id, name_bytes.decode("utf-8"), flags


def format_size(size_bytes: int) -> str:
    """Format byte count into human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"
