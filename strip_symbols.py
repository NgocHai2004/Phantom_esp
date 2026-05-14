"""
Post-build script: strip all symbols from the ELF to harden the binary
against reverse engineering.

Hooked via `extra_scripts = post:../strip_symbols.py` in platformio.ini.

Runs `xtensa-esp32-elf-strip --strip-all` on firmware.elf after link.
The .bin file flashed to the chip is unaffected because esptool only
reads loadable segments (code/data), not the symbol table.
"""

import os
import shutil

Import("env")  # noqa: F821 (provided by PlatformIO/SCons)


def _find_strip_tool(env):
    """Locate xtensa-esp32-elf-strip in the PlatformIO toolchain or PATH."""
    candidates = (
        "toolchain-xtensa-esp-elf",
        "toolchain-xtensa-esp32",
        "toolchain-xtensa32",
    )
    platform = env.PioPlatform()
    for pkg in candidates:
        pkg_dir = platform.get_package_dir(pkg)
        if not pkg_dir:
            continue
        for exe in ("xtensa-esp32-elf-strip", "xtensa-esp32-elf-strip.exe"):
            path = os.path.join(pkg_dir, "bin", exe)
            if os.path.isfile(path):
                return path
    return shutil.which("xtensa-esp32-elf-strip")


def strip_elf(source, target, env):
    elf_path = str(target[0])
    if not os.path.isfile(elf_path):
        print(f"[strip_symbols] ELF not found, skipping: {elf_path}")
        return

    strip_bin = _find_strip_tool(env)
    if not strip_bin:
        print("[strip_symbols] WARNING: xtensa-esp32-elf-strip not found; "
              "symbols will remain in the ELF.")
        return

    size_before = os.path.getsize(elf_path)
    rc = env.Execute(f'"{strip_bin}" --strip-all "{elf_path}"')
    if rc != 0:
        print(f"[strip_symbols] strip failed with exit code {rc}")
        return

    size_after = os.path.getsize(elf_path)
    print(f"[strip_symbols] Stripped {elf_path}: "
          f"{size_before} -> {size_after} bytes "
          f"(-{size_before - size_after} bytes)")


env.AddPostAction("$BUILD_DIR/${PROGNAME}.elf", strip_elf)  # noqa: F821
