"""Core compression logic: tar a folder, try multiple algorithms, pick the smallest."""

import bz2
import gzip
import io
import lzma
import os
import shutil
import tarfile
import tempfile
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
    write_header,
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


def _tar_folder(folder_path: Path, exclude_output: Path | None = None) -> bytes:
    """Create an in-memory tar archive of the folder, sorted by extension.

    Grouping files by extension puts similar data together, which dramatically
    improves compression ratio (same principle as 7-zip's solid archive).

    Args:
        folder_path: Folder to compress.
        exclude_output: If the output .zfld file lives inside folder_path,
                        pass its resolved path here so it is not included in
                        the archive (prevents the output from being packed
                        into itself on repeated runs).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Add the root directory entry itself
        tar.add(str(folder_path), arcname=folder_path.name, recursive=False)

        # Collect all files and directories, then sort files by extension
        dirs = []
        files = []
        for root, dirnames, filenames in os.walk(folder_path):
            for d in dirnames:
                dirs.append(Path(root) / d)
            for f in filenames:
                fp = Path(root) / f
                # Skip .zfld files that live inside the input folder — they
                # are previous output files and must not be re-archived.
                if fp.suffix.lower() == ".zfld":
                    continue
                if exclude_output and fp.resolve() == exclude_output:
                    continue
                files.append(fp)

        # Add directories first (preserves structure)
        for d in sorted(dirs):
            arcname = folder_path.name / d.relative_to(folder_path)
            tar.add(str(d), arcname=str(arcname), recursive=False)

        # Sort files by extension then by name — groups similar data together
        files.sort(key=lambda p: (p.suffix.lower(), p.name.lower()))
        for f in files:
            arcname = folder_path.name / f.relative_to(folder_path)
            tar.add(str(f), arcname=str(arcname), recursive=False)

    return buf.getvalue()


def _compress_gzip(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9)


def _compress_bz2(data: bytes) -> bytes:
    return bz2.compress(data, compresslevel=9)


def _compress_lzma(data: bytes) -> bytes:
    return lzma.compress(
        data,
        format=lzma.FORMAT_XZ,
        filters=[{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}],
    )


def _compress_zstd(data: bytes) -> bytes:
    cctx = zstandard.ZstdCompressor(
        level=22,
        enable_long_distance_matching=True,
        window_size=1 << 27,  # 128 MB window for better long-range deduplication
    )
    return cctx.compress(data)


def _compress_7z(data: bytes) -> bytes:
    """Compress using 7zip LZMA2 với các tham số tương đương:
      7z a -t7z -m0=lzma2 -mx=9 -md=128m -mfb=273 -ms=on

    -m0=lzma2  : thuật toán LZMA2
    -mx=9      : level nén tối đa (ultra)
    -md=128m   : dictionary 128 MB — tăng khả năng tìm pattern lặp ở phạm vi xa
    -mfb=273   : fast bytes 273 — độ dài match tối đa, giúp nén tốt hơn (chậm hơn)
    -ms=on     : solid mode — tất cả file nén chung 1 stream (py7zr mặc định bật)
    """
    buf = io.BytesIO()
    filters = [{
        "id": py7zr.FILTER_LZMA2,
        "preset": 9,
        "dict_size": 1 << 27,  # 128 MB  (-md=128m)
        "nice_len": 273,        # fast bytes  (-mfb=273)
    }]
    with py7zr.SevenZipFile(buf, mode="w", filters=filters) as archive:
        archive.writef(io.BytesIO(data), "data.tar")
    return buf.getvalue()


COMPRESSORS = [
    (ALGO_GZIP, "gzip", _compress_gzip),
    (ALGO_BZ2, "bz2", _compress_bz2),
    (ALGO_LZMA, "lzma", _compress_lzma),
]


def compress_folder(
    folder_path: str | Path,
    output_path: str | Path | None = None,
    optimize: bool = False,
    algorithm: str | None = None,
) -> Path:
    """Compress a folder using the specified or best available algorithm.

    Args:
        folder_path: Path to the folder to compress.
        output_path: Path for the output .zfld file. Defaults to <folder_name>.zfld.
        optimize: If True, optimize images/videos/office docs before compressing.
        algorithm: Force a specific algorithm ('gzip','bz2','lzma','zstd','7z').
                   None or 'auto' = test all and pick the smallest.

    Returns:
        Path to the created .zfld file.
    """
    folder_path = Path(folder_path).resolve()
    if not folder_path.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    if output_path is None:
        output_path = Path.cwd() / f"{folder_path.name}.zfld"
    else:
        output_path = Path(output_path).resolve()

    print(f"Compressing: {folder_path}")

    flags = 0

    # Step 0: Optimize if requested (work on a copy to preserve originals)
    work_folder = folder_path
    temp_dir = None
    if optimize:
        from .optimizer import optimize_folder

        print("  Creating working copy for optimization...")
        temp_dir = Path(tempfile.mkdtemp(prefix="zipfolder_opt_"))
        work_folder = temp_dir / folder_path.name
        # Exclude .zfld files that may live inside the input folder
        shutil.copytree(folder_path, work_folder,
                        ignore=shutil.ignore_patterns("*.zfld"))

        opt_stats = optimize_folder(work_folder)
        if opt_stats["optimized_count"] > 0:
            flags |= FLAG_OPTIMIZED

    try:
        # Step 1: Create tar archive
        print("  Creating tar archive...")
        # Pass output_path so _tar_folder can exclude it if it lives inside
        # the input folder (avoids packing the output .zfld into itself).
        _excl = output_path.resolve() if not optimize else None
        tar_data = _tar_folder(work_folder, exclude_output=_excl)
        original_size = len(tar_data)
        print(f"  Tar size: {format_size(original_size)}")

        # Step 2: Try all compressors (or just the requested one)
        all_compressors = list(COMPRESSORS)
        if HAS_ZSTD:
            all_compressors.append((ALGO_ZSTD, "zstd", _compress_zstd))
        else:
            print("  [Warning] zstandard not installed, skipping zstd.")
        if HAS_7Z:
            all_compressors.append((ALGO_7Z, "7z", _compress_7z))
        else:
            print("  [Warning] py7zr not installed, skipping 7z.")

        if algorithm and algorithm != "auto":
            compressors = [(aid, name, fn) for aid, name, fn in all_compressors if name == algorithm]
            if not compressors:
                available = [name for _, name, _ in all_compressors]
                raise ValueError(f"Unknown algorithm '{algorithm}'. Available: {available}")
        else:
            compressors = all_compressors

        results = []
        for algo_id, name, compress_fn in compressors:
            print(f"  Trying {name}...", end=" ", flush=True)
            compressed = compress_fn(tar_data)
            ratio = len(compressed) / original_size * 100
            print(f"{format_size(len(compressed))} ({ratio:.1f}%)")
            results.append((algo_id, name, compressed))

        # Step 3: Pick the smallest
        best_algo_id, best_name, best_data = min(results, key=lambda r: len(r[2]))
        best_ratio = len(best_data) / original_size * 100

        # Step 4: Write output file
        with open(output_path, "wb") as f:
            write_header(f, best_algo_id, folder_path.name, flags)
            f.write(best_data)

        print()
        print(f"  Best algorithm: {best_name}")
        if optimize:
            print(f"  Optimized:      Yes")
        print(f"  Original size:  {format_size(original_size)}")
        print(f"  Compressed:     {format_size(len(best_data))}")
        print(f"  Ratio:          {best_ratio:.1f}%")
        print(f"  Output:         {output_path}")

        return output_path
    finally:
        # Clean up temp copy
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
