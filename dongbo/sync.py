"""
dongbo/sync.py -- Dong bo file WAV tu ESP32 Node-A/Node-B ve folder dongbo/
=============================================================================
Cach dung:
  python dongbo/sync.py           <- chay 1 lan roi thoat
  python dongbo/sync.py --watch   <- tu dong sync moi 15 giay
  python dongbo/sync.py --list    <- liet ke file da co trong dongbo/

Logic:
  1. Thu ket noi Node-A (192.168.4.1) -- neu duoc thi sync tu Node-A
  2. Thu ket noi Node-B (192.168.5.1) -- neu duoc thi sync tu Node-B
  3. Download file chua co trong dongbo/ (khong ghi de file cu)
  4. In ket qua ra man hinh

Vi du:
  - Bat WiFi Phantom-1 -> chay script -> file ve dongbo/
  - Doi sang WiFi Phantom-2 -> chay lai  -> them file moi (neu co)
=============================================================================
"""

import os
import sys
import time
import json
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# Fix UnicodeEncodeError tren Windows terminal (cp1252)
if sys.platform == "win32":
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except AttributeError:
        pass

# ── Cau hinh ──────────────────────────────────────────────────────────────────
NODE_A_IP       = "192.168.4.1"   # Phantom-1
NODE_B_IP       = "192.168.5.1"   # Phantom-2
HTTP_PORT       = 80
TIMEOUT_S       = 5               # timeout ket noi HTTP (giay)
WATCH_INTERVAL  = 15              # giay giua cac lan sync (--watch)

# Thu muc luu file = cung thu muc voi script nay (dongbo/)
SYNC_DIR = Path(__file__).parent.parent / "phantom"

# ── Mau ANSI (Windows 10+ ho tro) ────────────────────────────────────────────
GRN = "\033[92m"
YLW = "\033[93m"
RED = "\033[91m"
CYN = "\033[96m"
BLD = "\033[1m"
RST = "\033[0m"

def ts():
    return datetime.now().strftime("%H:%M:%S")

def log_ok(msg):   print(f"{GRN}[{ts()}] OK   {msg}{RST}")
def log_info(msg): print(f"{CYN}[{ts()}]      {msg}{RST}")
def log_warn(msg): print(f"{YLW}[{ts()}] WARN {msg}{RST}")
def log_err(msg):  print(f"{RED}[{ts()}] ERR  {msg}{RST}")
def log_sep(msg):  print(f"{BLD}{CYN}{'='*55}\n  {msg}\n{'='*55}{RST}")

# ── Kiem tra node co online khong ────────────────────────────────────────────
def probe_node(ip: str, timeout: float = TIMEOUT_S):
    """Goi /status -- tra ve dict hoac None neu offline."""
    try:
        url = f"http://{ip}/status"
        req = urllib.request.Request(url, headers={"Connection": "close"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None

# ── Lay danh sach file tu node ───────────────────────────────────────────────
def get_file_list(ip: str, timeout: float = TIMEOUT_S):
    """Goi /file/list -- tra ve list[dict] cac file co size > 0."""
    try:
        url = f"http://{ip}/file/list"
        req = urllib.request.Request(url, headers={"Connection": "close"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return [f for f in data.get("files", []) if f.get("size", 0) > 0]
    except Exception as e:
        log_err(f"get_file_list({ip}): {e}")
        return []

# ── Download 1 file ──────────────────────────────────────────────────────────
def download_file(ip: str, remote_name: str, local_path: Path, timeout: float = 30) -> bool:
    """Download /file/download?name=<remote_name> -> local_path."""
    try:
        url = f"http://{ip}/file/download?name={remote_name}"
        req = urllib.request.Request(url, headers={"Connection": "close"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        elapsed = time.time() - t0
        if len(data) < 44:
            log_err(f"  File qua nho ({len(data)} bytes) -- bo qua")
            return False
        local_path.write_bytes(data)
        log_ok(f"  {local_path.name:<35}  {len(data)//1024:>5} KB  ({elapsed:.1f}s)")
        return True
    except Exception as e:
        log_err(f"  Download '{remote_name}' FAILED: {e}")
        return False

# ── Dat ten file local (tranh ghi de neu 2 node co file cung ten) ─────────────
def resolve_local_name(remote_name: str, node_label: str, existing_names: set) -> str:
    """
    Neu <remote_name> da co trong existing_names:
      -> luu thanh <stem>_<node_label>.wav
    Nguoc lai giu nguyen ten.
    """
    if remote_name not in existing_names:
        return remote_name
    stem = Path(remote_name).stem
    ext  = Path(remote_name).suffix or ".wav"
    return f"{stem}_{node_label}{ext}"

# ── Sync tu 1 node ────────────────────────────────────────────────────────────
def sync_from_node(ip: str, node_label: str) -> int:
    """Tai tat ca file moi tu node ve SYNC_DIR. Tra ve so file da download."""
    files = get_file_list(ip)
    if not files:
        log_warn(f"Node {node_label} ({ip}): khong co file nao trong SPIFFS")
        return 0

    names = [f["name"] for f in files]
    log_info(f"Node {node_label}: {len(files)} file -- {names}")

    # Lay ten file da co trong dongbo/
    existing = {f.name for f in SYNC_DIR.glob("*.wav")}

    downloaded = 0
    for fi in files:
        remote_name = fi["name"]
        local_name  = resolve_local_name(remote_name, node_label, existing)
        local_path  = SYNC_DIR / local_name

        if local_path.exists() and local_path.stat().st_size >= 44:
            log_info(f"  Bo qua '{local_name}' -- da co ({local_path.stat().st_size//1024} KB)")
            continue

        log_info(f"  Tai '{remote_name}' -> '{local_name}'...")
        ok = download_file(ip, remote_name, local_path)
        if ok:
            downloaded += 1
            existing.add(local_name)

    return downloaded

# ── Phat hien node va sync ────────────────────────────────────────────────────
def detect_and_sync() -> int:
    """
    Thu Node-A roi Node-B, sync tu node nao online.
    Tra ve tong so file downloaded.
    """
    total = 0

    # --- Node-A ---
    log_info(f"Kiem tra Node-A ({NODE_A_IP})...")
    status_a = probe_node(NODE_A_IP)
    if status_a:
        ssid = status_a.get("ap_ssid", f"Node-A")
        heap = status_a.get("free_heap", 0) // 1024
        log_ok(f"Ket noi Node-A: {ssid}  heap={heap}KB")
        n = sync_from_node(NODE_A_IP, "nodeA")
        total += n
        log_ok(f"Node-A: +{n} file moi") if n > 0 else log_info("Node-A: khong co file moi")
    else:
        log_warn(f"Node-A ({NODE_A_IP}) -- khong ket noi duoc")

    # --- Node-B ---
    log_info(f"Kiem tra Node-B ({NODE_B_IP})...")
    status_b = probe_node(NODE_B_IP)
    if status_b:
        ssid = status_b.get("ap_ssid", f"Node-B")
        heap = status_b.get("free_heap", 0) // 1024
        log_ok(f"Ket noi Node-B: {ssid}  heap={heap}KB")
        n = sync_from_node(NODE_B_IP, "nodeB")
        total += n
        log_ok(f"Node-B: +{n} file moi") if n > 0 else log_info("Node-B: khong co file moi")
    else:
        log_warn(f"Node-B ({NODE_B_IP}) -- khong ket noi duoc")

    if not status_a and not status_b:
        log_err("Khong ket noi duoc ca 2 node!")
        log_err("  -> Hay bat WiFi: Phantom-1 hoac Phantom-2 (mat khau: 12345678)")

    return total

# ── Liet ke file trong dongbo/ ───────────────────────────────────────────────
def list_local():
    wavs = sorted(SYNC_DIR.glob("*.wav"))
    if not wavs:
        log_warn("dongbo/ chua co file WAV nao")
        return
    print(f"\n{BLD}  File trong dongbo/ ({len(wavs)} file):{RST}")
    total_kb = 0
    for w in wavs:
        sz = w.stat().st_size
        total_kb += sz // 1024
        print(f"    {GRN}{w.name:<40}{RST}  {sz//1024:>5} KB")
    print(f"    {'':40}  {'-'*8}")
    print(f"    {'Tong':40}  {total_kb:>5} KB")
    print("")

# ── Entry point ──────────────────────────────────────────────────────────────
def main():
    # Enable ANSI color tren Windows
    if sys.platform == "win32":
        os.system("color")

    parser = argparse.ArgumentParser(
        description="Dong bo file WAV tu ESP32 Node-A/B ve dongbo/")
    parser.add_argument("--watch", action="store_true",
        help=f"Tu dong sync moi {WATCH_INTERVAL}s (Ctrl+C de dung)")
    parser.add_argument("--list", action="store_true",
        help="Liet ke file dang co trong dongbo/ roi thoat")
    parser.add_argument("--interval", type=int, default=WATCH_INTERVAL,
        help=f"Khoang cach giua cac lan sync (giay, mac dinh {WATCH_INTERVAL})")
    parser.add_argument("--node", choices=["a", "b", "both"], default="both",
        help="Chi sync tu node cu the: a / b / both (mac dinh: both)")
    args = parser.parse_args()

    SYNC_DIR.mkdir(parents=True, exist_ok=True)

    if args.list:
        list_local()
        return

    if args.watch:
        log_sep(f"Watch mode -- sync moi {args.interval}s  |  Ctrl+C de dung")
        log_info(f"Thu muc dich: {SYNC_DIR}")
        cycle = 0
        try:
            while True:
                cycle += 1
                print(f"\n{BLD}-- Lan #{cycle}  {ts()} {'─'*32}{RST}")
                total = _run_sync(args.node)
                list_local()
                log_info(f"Cho {args.interval}s...")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print(f"\n{YLW}Da dung watch mode.{RST}")
    else:
        log_sep("Dong bo 1 lan tu ESP32 -> dongbo/")
        log_info(f"Thu muc dich: {SYNC_DIR}")
        total = _run_sync(args.node)
        print("")
        list_local()
        if total > 0:
            log_ok(f"Xong! Da tai {total} file moi vao dongbo/")
        else:
            log_info("Khong co file moi can tai")


def _run_sync(node: str) -> int:
    """Sync theo lua chon node: a / b / both."""
    if node == "a":
        log_info(f"Chi sync tu Node-A ({NODE_A_IP})...")
        status = probe_node(NODE_A_IP)
        if not status:
            log_err(f"Node-A ({NODE_A_IP}) offline -- hay bat WiFi Phantom-1")
            return 0
        log_ok(f"Node-A: {status.get('ap_ssid','?')}  heap={status.get('free_heap',0)//1024}KB")
        n = sync_from_node(NODE_A_IP, "nodeA")
        log_ok(f"Node-A: +{n} file moi") if n > 0 else log_info("Node-A: khong co file moi")
        return n
    elif node == "b":
        log_info(f"Chi sync tu Node-B ({NODE_B_IP})...")
        status = probe_node(NODE_B_IP)
        if not status:
            log_err(f"Node-B ({NODE_B_IP}) offline -- hay bat WiFi Phantom-2")
            return 0
        log_ok(f"Node-B: {status.get('ap_ssid','?')}  heap={status.get('free_heap',0)//1024}KB")
        n = sync_from_node(NODE_B_IP, "nodeB")
        log_ok(f"Node-B: +{n} file moi") if n > 0 else log_info("Node-B: khong co file moi")
        return n
    else:
        return detect_and_sync()


if __name__ == "__main__":
    main()
