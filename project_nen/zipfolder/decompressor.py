"""Core decompression logic: read .zfld file, decompress, extract tar."""

import bz2
import gzip
import io
import lzma
import tarfile
import tempfile
import os
from pathlib import Path

from .formats import (
    ALGO_7Z,
    ALGO_BZ2,
    ALGO_GZIP,
    ALGO_LZMA,
    ALGO_NAMES,
    ALGO_ZSTD,
    FLAG_OPTIMIZED,
    format_size,
    read_header,
)

try:
    import zstandard

    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

try:
    import py7zr

    HAS_7Z = True
except ImportError:
    HAS_7Z = False


def _decompress_gzip(data: bytes) -> bytes:
    return gzip.decompress(data)


def _decompress_bz2(data: bytes) -> bytes:
    return bz2.decompress(data)


def _decompress_lzma(data: bytes) -> bytes:
    return lzma.decompress(data)


def _decompress_zstd(data: bytes) -> bytes:
    dctx = zstandard.ZstdDecompressor()
    return dctx.decompress(data, max_output_size=2 * 1024 * 1024 * 1024)  # 2GB max


def _decompress_7z(data: bytes) -> bytes:
    """Decompress 7zip LZMA2 data created by _compress_7z.

    Extracts the single 'data.tar' entry from the 7z container via a temp dir.
    """
    buf = io.BytesIO(data)
    with tempfile.TemporaryDirectory() as tmpdir:
        with py7zr.SevenZipFile(buf, mode="r") as archive:
            archive.extractall(path=tmpdir)
        with open(os.path.join(tmpdir, "data.tar"), "rb") as f:
            return f.read()


DECOMPRESSORS = {
    ALGO_GZIP: ("gzip", _decompress_gzip),
    ALGO_BZ2: ("bz2", _decompress_bz2),
    ALGO_LZMA: ("lzma", _decompress_lzma),
    ALGO_ZSTD: ("zstd", _decompress_zstd),
    ALGO_7Z: ("7z", _decompress_7z),
}


def decompress_folder(file_path: str | Path, output_dir: str | Path | None = None) -> Path:
    """Decompress a .zfld file back to the original folder.

    Args:
        file_path: Path to the .zfld file.
        output_dir: Directory where the folder will be extracted. Defaults to cwd.

    Returns:
        Path to the extracted folder.
    """
    file_path = Path(file_path).resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    if output_dir is None:
        output_dir = Path.cwd()
    else:
        output_dir = Path(output_dir).resolve()

    print(f"Decompressing: {file_path}")

    # Step 1: Read header
    with open(file_path, "rb") as f:
        algo_id, folder_name, flags = read_header(f)
        compressed_data = f.read()

    is_optimized = bool(flags & FLAG_OPTIMIZED)

    algo_name = ALGO_NAMES[algo_id]
    print(f"  Algorithm: {algo_name}")
    print(f"  Folder name: {folder_name}")
    print(f"  Optimized: {'Yes' if is_optimized else 'No'}")
    print(f"  Compressed size: {format_size(len(compressed_data))}")

    # Step 2: Decompress
    if algo_id == ALGO_ZSTD and not HAS_ZSTD:
        raise RuntimeError(
            "This file was compressed with zstd but zstandard is not installed. "
            "Install with: pip install zstandard"
        )
    if algo_id == ALGO_7Z and not HAS_7Z:
        raise RuntimeError(
            "This file was compressed with 7z but py7zr is not installed. "
            "Install with: pip install py7zr"
        )

    _, decompress_fn = DECOMPRESSORS[algo_id]
    print(f"  Decompressing with {algo_name}...", flush=True)
    tar_data = decompress_fn(compressed_data)
    print(f"  Decompressed tar size: {format_size(len(tar_data))}")

    # Step 3: Extract tar
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted_path = output_dir / folder_name

    print(f"  Extracting to: {extracted_path}")
    buf = io.BytesIO(tar_data)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        tar.extractall(path=str(output_dir))

    # Step 4: Restore original file formats if optimized
    if is_optimized:
        from .optimizer import restore_from_manifest
        restore_from_manifest(extracted_path)

    print()
    print(f"  Restored: {extracted_path}")

    return extracted_path
