"""File-type aware optimization: shrink images, videos, and office docs before compression."""

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

MANIFEST_NAME = ".zfld_manifest.json"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
PDF_EXTENSIONS = {".pdf"}

# Image settings
IMAGE_QUALITY = 65
IMAGE_TARGET_FORMAT = "webp"
IMAGE_MAX_DIMENSION = 2048  # Resize images larger than this

# Video settings (ffmpeg)
VIDEO_CRF = "28"
VIDEO_PRESET = "slow"
VIDEO_AUDIO_BITRATE = "64k"


def _find_ffmpeg() -> str | None:
    """Find ffmpeg executable."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    return None


def _find_ghostscript() -> str | None:
    """Find Ghostscript executable."""
    for name in ("gs", "gswin64c", "gswin32c"):
        if shutil.which(name):
            return name
    return None


def _optimize_pdf(file_path: Path, gs_path: str) -> tuple[Path, int, int]:
    """Optimize a PDF via Ghostscript. Returns (path, old_size, new_size)."""
    old_size = file_path.stat().st_size
    temp_path = file_path.with_suffix(".tmp.pdf")

    try:
        cmd = [
            gs_path, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook",  # 150 dpi — good balance of quality/size
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            "-dColorImageResolution=150",
            "-dGrayImageResolution=150",
            "-dMonoImageResolution=300",
            f"-sOutputFile={temp_path}",
            str(file_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)

        if result.returncode != 0 or not temp_path.exists():
            if temp_path.exists():
                temp_path.unlink()
            return file_path, old_size, old_size

        new_size = temp_path.stat().st_size

        if new_size < old_size:
            file_path.unlink()
            temp_path.rename(file_path)
            return file_path, old_size, new_size
        else:
            temp_path.unlink()
            return file_path, old_size, old_size
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        return file_path, old_size, old_size


def _optimize_image(file_path: Path) -> tuple[Path, int, int]:
    """Optimize a single image. Returns (new_path, old_size, new_size)."""
    old_size = file_path.stat().st_size
    new_path = file_path.with_suffix(f".{IMAGE_TARGET_FORMAT}")

    try:
        with Image.open(file_path) as img:
            # Strip EXIF and other metadata by rebuilding pixel data
            data = list(img.getdata())
            clean_img = Image.new(img.mode, img.size)
            clean_img.putdata(data)
            img = clean_img

            # Convert to RGB if needed (RGBA/P modes can't save as JPEG/WebP directly)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGBA")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Resize if too large
            w, h = img.size
            if max(w, h) > IMAGE_MAX_DIMENSION:
                ratio = IMAGE_MAX_DIMENSION / max(w, h)
                new_w, new_h = int(w * ratio), int(h * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            img.save(str(new_path), IMAGE_TARGET_FORMAT, quality=IMAGE_QUALITY, method=6)

        new_size = new_path.stat().st_size

        # Only keep optimized version if it's actually smaller
        if new_size < old_size:
            if new_path != file_path:
                file_path.unlink()
            return new_path, old_size, new_size
        else:
            # Revert: optimized version is larger
            if new_path != file_path and new_path.exists():
                new_path.unlink()
            return file_path, old_size, old_size
    except Exception:
        # If optimization fails, keep original
        if new_path != file_path and new_path.exists():
            new_path.unlink()
        return file_path, old_size, old_size


def _optimize_video(file_path: Path, ffmpeg_path: str) -> tuple[Path, int, int]:
    """Optimize a single video via ffmpeg. Returns (new_path, old_size, new_size)."""
    old_size = file_path.stat().st_size
    temp_path = file_path.with_suffix(".tmp.mp4")

    try:
        cmd = [
            ffmpeg_path, "-y", "-i", str(file_path),
            "-c:v", "libx265", "-crf", VIDEO_CRF, "-preset", VIDEO_PRESET,
            "-c:a", "aac", "-b:a", VIDEO_AUDIO_BITRATE,
            "-movflags", "+faststart",
            "-loglevel", "error",
            str(temp_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)

        if result.returncode != 0 or not temp_path.exists():
            if temp_path.exists():
                temp_path.unlink()
            return file_path, old_size, old_size

        new_size = temp_path.stat().st_size

        if new_size < old_size:
            new_path = file_path.with_suffix(".mp4")
            file_path.unlink()
            temp_path.rename(new_path)
            return new_path, old_size, new_size
        else:
            temp_path.unlink()
            return file_path, old_size, old_size
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        return file_path, old_size, old_size


def _optimize_office(file_path: Path) -> tuple[Path, int, int]:
    """Re-zip office documents with maximum compression."""
    old_size = file_path.stat().st_size
    temp_path = file_path.with_suffix(file_path.suffix + ".tmp")

    try:
        with zipfile.ZipFile(file_path, "r") as zin:
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)

                    # Optimize embedded images inside office docs
                    if HAS_PILLOW and any(item.filename.lower().endswith(ext) for ext in (".png", ".bmp", ".tiff")):
                        try:
                            import io
                            with Image.open(io.BytesIO(data)) as img:
                                if img.mode not in ("RGB", "RGBA"):
                                    img = img.convert("RGB")
                                buf = io.BytesIO()
                                img.save(buf, "webp", quality=IMAGE_QUALITY, method=6)
                                optimized = buf.getvalue()
                                if len(optimized) < len(data):
                                    # Write with new extension
                                    new_name = str(Path(item.filename).with_suffix(".webp"))
                                    zout.writestr(new_name, optimized)
                                    continue
                        except Exception:
                            pass

                    zout.writestr(item, data)

        new_size = temp_path.stat().st_size

        if new_size < old_size:
            file_path.unlink()
            temp_path.rename(file_path)
            return file_path, old_size, new_size
        else:
            temp_path.unlink()
            return file_path, old_size, old_size
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        return file_path, old_size, old_size


def optimize_folder(folder_path: Path) -> dict:
    """Optimize all files in folder for maximum compression.

    Modifies files IN-PLACE and creates a manifest for restoring originals.

    Returns dict with stats:
        {
            "optimized_count": int,
            "original_total": int,  # bytes
            "optimized_total": int,  # bytes
            "saved": int,  # bytes saved
            "manifest": dict,  # renamed files mapping
        }
    """
    folder_path = Path(folder_path).resolve()
    renamed = {}  # new_relative_path -> original_relative_path
    stats = {"optimized_count": 0, "original_total": 0, "optimized_total": 0}

    ffmpeg_path = _find_ffmpeg()
    gs_path = _find_ghostscript()

    print("  Optimizing files...")

    for root, _dirs, files in os.walk(folder_path):
        for fname in files:
            file_path = Path(root) / fname
            ext = file_path.suffix.lower()
            rel_path = file_path.relative_to(folder_path)

            if ext in IMAGE_EXTENSIONS and HAS_PILLOW:
                print(f"    Image: {rel_path}", end=" ", flush=True)
                new_path, old_size, new_size = _optimize_image(file_path)
                stats["original_total"] += old_size
                stats["optimized_total"] += new_size
                if new_path != file_path:
                    new_rel = new_path.relative_to(folder_path)
                    renamed[str(new_rel)] = str(rel_path)
                    stats["optimized_count"] += 1
                    saved_pct = (1 - new_size / old_size) * 100 if old_size > 0 else 0
                    print(f"-> {new_rel.suffix} ({saved_pct:.0f}% saved)")
                else:
                    print("(kept)")

            elif ext in VIDEO_EXTENSIONS and ffmpeg_path:
                print(f"    Video: {rel_path}", end=" ", flush=True)
                new_path, old_size, new_size = _optimize_video(file_path, ffmpeg_path)
                stats["original_total"] += old_size
                stats["optimized_total"] += new_size
                if new_path != file_path:
                    new_rel = new_path.relative_to(folder_path)
                    renamed[str(new_rel)] = str(rel_path)
                    stats["optimized_count"] += 1
                    saved_pct = (1 - new_size / old_size) * 100 if old_size > 0 else 0
                    print(f"-> .mp4 ({saved_pct:.0f}% saved)")
                elif new_size < old_size:
                    stats["optimized_count"] += 1
                    saved_pct = (1 - new_size / old_size) * 100 if old_size > 0 else 0
                    print(f"({saved_pct:.0f}% saved)")
                else:
                    print("(kept)")

            elif ext in OFFICE_EXTENSIONS:
                print(f"    Office: {rel_path}", end=" ", flush=True)
                _, old_size, new_size = _optimize_office(file_path)
                stats["original_total"] += old_size
                stats["optimized_total"] += new_size
                if new_size < old_size:
                    stats["optimized_count"] += 1
                    saved_pct = (1 - new_size / old_size) * 100 if old_size > 0 else 0
                    print(f"({saved_pct:.0f}% saved)")
                else:
                    print("(kept)")

            elif ext in PDF_EXTENSIONS and gs_path:
                print(f"    PDF: {rel_path}", end=" ", flush=True)
                _, old_size, new_size = _optimize_pdf(file_path, gs_path)
                stats["original_total"] += old_size
                stats["optimized_total"] += new_size
                if new_size < old_size:
                    stats["optimized_count"] += 1
                    saved_pct = (1 - new_size / old_size) * 100 if old_size > 0 else 0
                    print(f"({saved_pct:.0f}% saved)")
                else:
                    print("(kept)")

            else:
                file_size = file_path.stat().st_size
                stats["original_total"] += file_size
                stats["optimized_total"] += file_size

    # Write manifest if any files were renamed
    if renamed:
        manifest_path = folder_path / MANIFEST_NAME
        manifest_path.write_text(json.dumps({"renamed": renamed}, indent=2), encoding="utf-8")

    stats["saved"] = stats["original_total"] - stats["optimized_total"]
    stats["manifest"] = renamed

    saved_pct = stats["saved"] / stats["original_total"] * 100 if stats["original_total"] > 0 else 0
    print(f"  Optimization done: {stats['optimized_count']} files optimized, "
          f"{format_size(stats['saved'])} saved ({saved_pct:.1f}%)")

    return stats


def restore_from_manifest(folder_path: Path) -> None:
    """Restore original file names/formats from manifest after decompression."""
    folder_path = Path(folder_path).resolve()
    manifest_path = folder_path / MANIFEST_NAME

    if not manifest_path.exists():
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return

    renamed = manifest.get("renamed", {})
    if not renamed:
        manifest_path.unlink(missing_ok=True)
        return

    print("  Restoring original file formats...")

    for new_rel, orig_rel in renamed.items():
        new_path = folder_path / new_rel
        orig_path = folder_path / orig_rel

        if not new_path.exists():
            continue

        orig_ext = Path(orig_rel).suffix.lower()
        new_ext = Path(new_rel).suffix.lower()

        # For images: convert back to original format
        if new_ext == ".webp" and orig_ext in IMAGE_EXTENSIONS and HAS_PILLOW:
            try:
                with Image.open(new_path) as img:
                    if orig_ext in (".jpg", ".jpeg"):
                        if img.mode == "RGBA":
                            img = img.convert("RGB")
                        img.save(str(orig_path), "JPEG", quality=92)
                    elif orig_ext == ".png":
                        img.save(str(orig_path), "PNG")
                    elif orig_ext in (".bmp",):
                        img.save(str(orig_path), "BMP")
                    elif orig_ext in (".tiff", ".tif"):
                        img.save(str(orig_path), "TIFF")
                    elif orig_ext == ".gif":
                        img.save(str(orig_path), "GIF")
                    else:
                        orig_path = new_path  # keep as webp
                new_path.unlink(missing_ok=True)
                print(f"    Restored: {orig_rel}")
            except Exception:
                # If conversion fails, just rename
                orig_path.parent.mkdir(parents=True, exist_ok=True)
                new_path.rename(orig_path)
        else:
            # For videos or other: just rename back
            orig_path.parent.mkdir(parents=True, exist_ok=True)
            if new_path != orig_path:
                new_path.rename(orig_path)
            print(f"    Restored: {orig_rel}")

    manifest_path.unlink(missing_ok=True)
    print("  All files restored.")


# Import here to avoid circular
from .formats import format_size
