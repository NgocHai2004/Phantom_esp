"""zipfolder - Compress folders to the smallest possible size."""

from .compressor import compress_folder
from .decompressor import decompress_folder
from .optimizer import optimize_folder

__all__ = ["compress_folder", "decompress_folder", "optimize_folder"]
