#!/usr/bin/env python3
import argparse
import os
import pathlib
import platform
import subprocess
import tempfile
import time
from typing import Optional

import requests


TARGET_SSID = "7068616e746f6d303030303030300002"
WIFI_PASSWORD = "12345678"
WIFI_CONNECT_TIMEOUT_SEC = 8

UPLOAD_HOST = "10.42.0.1"
UPLOAD_PORT = 8765
UPLOAD_PATH = "/api/upload"


def log(msg: str) -> None:
    print(msg, flush=True)


def current_ssid_windows() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["netsh", "wlan", "show", "interfaces"],
            text=True,
            timeout=4,
            encoding="utf-8",
            errors="ignore",
        )
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("SSID") and "BSSID" not in s:
                parts = s.split(":", 1)
                if len(parts) == 2:
                    v = parts[1].strip()
                    return v if v else None
    except Exception:
        return None
    return None


def current_ssid() -> Optional[str]:
    if platform.system().lower() == "windows":
        return current_ssid_windows()
    try:
        out = subprocess.check_output(["iwgetid", "-r"], text=True, timeout=2).strip()
        return out or None
    except Exception:
        return None


def connect_hidden_wifi_windows() -> bool:
    if current_ssid() == TARGET_SSID:
        log(f"[WIFI] Already connected: {TARGET_SSID}")
        return True

    xml_path = pathlib.Path(tempfile.gettempdir()) / "hidden_wifi_profile.xml"
    profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{TARGET_SSID}</name>
    <SSIDConfig>
        <SSID>
            <name>{TARGET_SSID}</name>
        </SSID>
        <nonBroadcast>true</nonBroadcast>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{WIFI_PASSWORD}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>
"""
    xml_path.write_text(profile_xml, encoding="ascii")

    cmds = [
        ["netsh", "wlan", "add", "profile", f"filename={xml_path}", "user=current"],
        ["netsh", "wlan", "connect", f"name={TARGET_SSID}"],
    ]

    for cmd in cmds:
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=12,
                check=False,
                encoding="utf-8",
                errors="ignore",
            )
            log(f"[WIFI] cmd={' '.join(cmd)} rc={proc.returncode}")
            if proc.stdout.strip():
                log(f"[WIFI] stdout: {proc.stdout.strip()}")
            if proc.stderr.strip():
                log(f"[WIFI] stderr: {proc.stderr.strip()}")
        except Exception as e:
            log(f"[WIFI] Exception: {e}")

    time.sleep(3)
    return current_ssid() == TARGET_SSID


def connect_hidden_wifi() -> bool:
    if platform.system().lower() == "windows":
        return connect_hidden_wifi_windows()

    if current_ssid() == TARGET_SSID:
        log(f"[WIFI] Already connected: {TARGET_SSID}")
        return True

    cmds = [
        [
            "nmcli",
            "con",
            "modify",
            TARGET_SSID,
            "802-11-wireless.hidden",
            "yes",
            "wifi-sec.key-mgmt",
            "wpa-psk",
            "wifi-sec.psk",
            WIFI_PASSWORD,
        ],
        ["nmcli", "-w", str(WIFI_CONNECT_TIMEOUT_SEC), "con", "up", "id", TARGET_SSID],
        [
            "nmcli",
            "-w",
            str(WIFI_CONNECT_TIMEOUT_SEC),
            "dev",
            "wifi",
            "connect",
            TARGET_SSID,
            "password",
            WIFI_PASSWORD,
            "hidden",
            "yes",
        ],
    ]

    for cmd in cmds:
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=WIFI_CONNECT_TIMEOUT_SEC + 8,
                check=False,
            )
            safe_cmd = " ".join(cmd).replace(WIFI_PASSWORD, "********")
            log(f"[WIFI] cmd={safe_cmd} rc={proc.returncode}")
            if proc.stdout.strip():
                log(f"[WIFI] stdout: {proc.stdout.strip()}")
            if proc.stderr.strip():
                log(f"[WIFI] stderr: {proc.stderr.strip()}")
            time.sleep(1)
            if current_ssid() == TARGET_SSID:
                log(f"[WIFI] Connected to hidden SSID: {TARGET_SSID}")
                return True
        except Exception as e:
            log(f"[WIFI] Exception: {e}")

    return False


def make_temp_file(size_mb: int) -> pathlib.Path:
    fd, path = tempfile.mkstemp(prefix="hidden_wifi_speed_", suffix=".bin")
    os.close(fd)
    p = pathlib.Path(path)
    total = size_mb * 1024 * 1024
    block = b"\x00" * (1024 * 1024)
    written = 0
    with p.open("wb") as f:
        while written < total:
            n = min(len(block), total - written)
            f.write(block[:n])
            written += n
    return p


def upload_once(file_path: pathlib.Path) -> tuple[int, float, int, str]:
    url = f"http://{UPLOAD_HOST}:{UPLOAD_PORT}{UPLOAD_PATH}"
    size = file_path.stat().st_size
    t0 = time.perf_counter()
    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f, "application/octet-stream")}
        resp = requests.post(url, files=files, timeout=180)
    elapsed = time.perf_counter() - t0
    return size, elapsed, resp.status_code, resp.text[:500]


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure upload speed over one hidden Wi-Fi SSID.")
    parser.add_argument("--size-mb", type=int, default=20, help="Temp test file size in MB (default: 20)")
    parser.add_argument("--repeat", type=int, default=1, help="Number of test runs (default: 1)")
    args = parser.parse_args()

    if not connect_hidden_wifi():
        log("[RESULT] FAIL: cannot connect hidden Wi-Fi")
        raise SystemExit(1)

    p = make_temp_file(args.size_mb)
    try:
        for i in range(1, args.repeat + 1):
            size, elapsed, status, body = upload_once(p)
            mb_s = size / elapsed / (1024 * 1024)
            mbps = size * 8 / elapsed / 1_000_000
            log(f"\n[TEST {i}] status={status}")
            log(f"[TEST {i}] sent={size} bytes, time={elapsed:.3f} s")
            log(f"[TEST {i}] speed={mb_s:.3f} MB/s ({mbps:.2f} Mbps)")
            if body.strip():
                log(f"[TEST {i}] server: {body.strip()}")
    finally:
        p.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
