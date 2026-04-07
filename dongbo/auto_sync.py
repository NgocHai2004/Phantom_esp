"""
dongbo/auto_sync.py -- Tu dong dong bo khi ket noi WiFi ESP32
=============================================================
- Script chay lien tuc (daemon mode)
- Cu 3 giay kiem tra xem laptop dang ket noi WiFi nao
- Neu phat hien ket noi vao ESP32-Node-1 (192.168.4.1)
  hoac ESP32-Node-2 (192.168.5.1) --> sync ngay lap tuc
- Sau khi sync xong, doi 30s roi kiem tra lai
  (tranh spam request lien tuc)
- LED ESP32 tu dong nhay 3 cai khi co file moi duoc download
  (firmware da xu ly phia ESP32)

Cach chay:
  python dongbo/auto_sync.py          <- chay den khi Ctrl+C
  python dongbo/auto_sync.py --once   <- chi sync 1 lan roi thoat

Yeu cau: Python 3.6+, khong can thu vien ngoai
=============================================================
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

# Fix encoding tren Windows cp1252
if sys.platform == "win32":
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except AttributeError:
        pass

# ── Cau hinh (dung dict de tranh loi global trong Python) ────────────────────
CFG = {
    "node_a_ip"      : "192.168.4.1",  # ESP32-Node-1
    "node_b_ip"      : "192.168.5.1",  # ESP32-Node-2
    # Ten SSID ESP32 (dung de detect bang netsh)
    "node_a_ssid"    : "ESP32-Node-1",
    "node_b_ssid"    : "ESP32-Node-2",
    "probe_timeout"  : 2,              # timeout kiem tra node (giay)
    "dl_timeout"     : 30,             # timeout download file (giay)
    "check_interval" : 3,              # giay giua cac lan kiem tra WiFi
    "cooldown"       : 5,              # giay doi sau khi sync xong
}

SYNC_DIR = Path(__file__).parent.resolve()

# ── Mau ANSI ──────────────────────────────────────────────────────────────────
GRN = "\033[92m"; YLW = "\033[93m"; RED = "\033[91m"
CYN = "\033[96m"; BLD = "\033[1m";  RST = "\033[0m"

def ts():
    return datetime.now().strftime("%H:%M:%S")

def log_ok(msg):   print(f"{GRN}[{ts()}] OK   {msg}{RST}", flush=True)
def log_info(msg): print(f"{CYN}[{ts()}]      {msg}{RST}", flush=True)
def log_warn(msg): print(f"{YLW}[{ts()}] WARN {msg}{RST}", flush=True)
def log_err(msg):  print(f"{RED}[{ts()}] ERR  {msg}{RST}", flush=True)
def log_sep(msg):  print(f"\n{BLD}{CYN}{'='*55}\n  {msg}\n{'='*55}{RST}", flush=True)

# ── Doc SSID WiFi hien tai (Windows: netsh, macOS/Linux: iwgetid) ─────────────
def get_current_ssid() -> str:
    """
    Lay ten SSID WiFi laptop dang ket noi.
    Tra ve chuoi rong neu khong co ket noi hoac loi.
    """
    try:
        if sys.platform == "win32":
            import subprocess
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                encoding="utf-8", errors="replace",
                creationflags=0x08000000  # CREATE_NO_WINDOW
            )
            for line in out.splitlines():
                line = line.strip()
                if line.lower().startswith("ssid") and "bssid" not in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        return parts[1].strip()
        else:
            import subprocess
            out = subprocess.check_output(
                ["iwgetid", "-r"], encoding="utf-8", errors="replace"
            )
            return out.strip()
    except Exception:
        pass
    return ""

# ── Kiem tra node ─────────────────────────────────────────────────────────────
def probe_node(ip: str):
    """Goi /status voi timeout ngan. Tra ve dict hoac None."""
    try:
        url = f"http://{ip}/status"
        req = urllib.request.Request(url, headers={"Connection": "close"})
        with urllib.request.urlopen(req, timeout=CFG["probe_timeout"]) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None

def get_file_list(ip: str, retries: int = 4, retry_delay: float = 3.0):
    """
    Lay danh sach file tu ESP32. Retry neu bi timeout (Node dang ban sync noi bo).
    """
    for attempt in range(1, retries + 1):
        try:
            url = f"http://{ip}/file/list"
            req = urllib.request.Request(url, headers={"Connection": "close"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                return [f for f in data.get("files", []) if f.get("size", 0) > 0]
        except Exception as e:
            if attempt < retries:
                log_info(f"  file/list({ip}) lan {attempt} that bai -- thu lai sau {retry_delay}s ({e})")
                time.sleep(retry_delay)
            else:
                log_err(f"file/list({ip}): {e}")
    return []

def download_file(ip: str, remote_name: str, local_path: Path) -> bool:
    try:
        url = f"http://{ip}/file/download?name={remote_name}"
        req = urllib.request.Request(url, headers={"Connection": "close"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=CFG["dl_timeout"]) as resp:
            data = resp.read()
        elapsed = time.time() - t0
        if len(data) < 44:
            log_err(f"  '{remote_name}' qua nho ({len(data)}B) -- bo qua")
            return False
        local_path.write_bytes(data)
        log_ok(f"  {local_path.name:<38} {len(data)//1024:>5}KB ({elapsed:.1f}s)")
        return True
    except Exception as e:
        log_err(f"  Download '{remote_name}': {e}")
        return False

def resolve_local_name(remote_name: str, node_label: str, existing: set) -> str:
    """
    Giu nguyen ten goc neu chua co trong dongbo/.
    Chi them _nodeX neu file ten do DA TON TAI va kich thuoc khac nhau
    (tranh ghi de file tu node khac, nhung van uu tien ten goc).
    """
    local_path = SYNC_DIR / remote_name
    if not local_path.exists():
        return remote_name  # Chua co -> dung ten goc
    # File da ton tai -> dung ten goc (bo qua, se skip trong sync_node)
    return remote_name

# ── Sync tu 1 node ────────────────────────────────────────────────────────────
def sync_node(ip: str, node_label: str) -> int:
    files = get_file_list(ip)
    if not files:
        log_info(f"Node {node_label}: SPIFFS khong co file")
        return 0

    log_info(f"Node {node_label}: {len(files)} file -- {[f['name'] for f in files]}")
    downloaded = 0

    for fi in files:
        rname = fi["name"]
        lpath = SYNC_DIR / rname  # Luon dung ten goc, khong them suffix

        if lpath.exists() and lpath.stat().st_size >= 44:
            log_info(f"  Bo qua '{rname}' -- da co ({lpath.stat().st_size//1024}KB)")
            continue

        log_info(f"  Tai '{rname}'...")
        if download_file(ip, rname, lpath):
            downloaded += 1

    return downloaded

# ── Vong lap chinh ────────────────────────────────────────────────────────────
def run_daemon(once: bool = False):
    if sys.platform == "win32":
        os.system("color")

    node_a    = CFG["node_a_ip"]
    node_b    = CFG["node_b_ip"]
    ssid_a    = CFG["node_a_ssid"]
    ssid_b    = CFG["node_b_ssid"]
    interval  = CFG["check_interval"]
    cooldown  = CFG["cooldown"]

    log_sep("Auto-Sync Daemon -- ESP32 -> dongbo/")
    log_info(f"Thu muc : {SYNC_DIR}")
    log_info(f"Node-A  : {ssid_a} ({node_a})")
    log_info(f"Node-B  : {ssid_b} ({node_b})")
    if not once:
        log_info(f"Check moi {interval}s | Cooldown {cooldown}s | Ctrl+C de dung")
    print("")

    SYNC_DIR.mkdir(parents=True, exist_ok=True)

    last_synced_a  = 0.0
    last_synced_b  = 0.0
    prev_ssid      = ""   # SSID lan truoc de phat hien thay doi
    dots           = 0

    try:
        while True:
            now          = time.time()
            current_ssid = get_current_ssid()
            found_any    = False

            # Bao hieu khi WiFi thay doi
            if current_ssid != prev_ssid:
                if current_ssid:
                    print("")  # xuong dong sau dots
                    log_info(f"WiFi thay doi: '{prev_ssid}' -> '{current_ssid}'")
                prev_ssid = current_ssid

            # --- Phat hien dang ket noi Node-A ---
            is_node_a = (current_ssid == ssid_a)
            if is_node_a and (now - last_synced_a >= cooldown):
                log_sep(f"WiFi: {current_ssid} -- Bat dau sync Node-A")
                n = sync_node(node_a, "nodeA")
                log_ok(f"Node-A: +{n} file moi -> dongbo/") if n > 0 else log_info("Node-A: khong co file moi")
                last_synced_a = time.time()
                found_any = True
                if once:
                    return

            # --- Phat hien dang ket noi Node-B ---
            is_node_b = (current_ssid == ssid_b)
            if is_node_b and (now - last_synced_b >= cooldown):
                log_sep(f"WiFi: {current_ssid} -- Bat dau sync Node-B")
                n = sync_node(node_b, "nodeB")
                log_ok(f"Node-B: +{n} file moi -> dongbo/") if n > 0 else log_info("Node-B: khong co file moi")
                last_synced_b = time.time()
                found_any = True
                if once:
                    return

            # --- Fallback: SSID an (hidden) hoac khong doc duoc SSID ---
            # Neu khong match SSID nhung van probe duoc HTTP -> van sync
            if not found_any and not current_ssid:
                # Thu probe ca 2 node neu khong doc duoc SSID
                if now - last_synced_a >= cooldown:
                    s = probe_node(node_a)
                    if s:
                        log_sep(f"Ket noi (fallback probe): Node-A ({node_a})")
                        n = sync_node(node_a, "nodeA")
                        log_ok(f"Node-A: +{n} file moi -> dongbo/") if n > 0 else log_info("Node-A: khong co file moi")
                        last_synced_a = time.time()
                        found_any = True
                        if once:
                            return
                if now - last_synced_b >= cooldown:
                    s = probe_node(node_b)
                    if s:
                        log_sep(f"Ket noi (fallback probe): Node-B ({node_b})")
                        n = sync_node(node_b, "nodeB")
                        log_ok(f"Node-B: +{n} file moi -> dongbo/") if n > 0 else log_info("Node-B: khong co file moi")
                        last_synced_b = time.time()
                        found_any = True
                        if once:
                            return

            if not found_any:
                # Hien thi waiting dots
                dots = (dots + 1) % 4
                dot_str = "." * (dots + 1) + " " * (3 - dots)
                wifi_disp = f"[{current_ssid}]" if current_ssid else "(chua bat WiFi ESP32)"
                print(f"\r{CYN}[{ts()}] Cho {wifi_disp}{dot_str}{RST}   ", end="", flush=True)

            if once:
                print("")
                log_warn("Khong ket noi duoc node nao -- hay bat WiFi ESP32-Node-1/2")
                return

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n{YLW}Da dung auto-sync.{RST}")
        wavs = sorted(SYNC_DIR.glob("*.wav"))
        if wavs:
            print(f"\n{BLD}  File trong dongbo/ ({len(wavs)} file):{RST}")
            for w in wavs:
                print(f"    {GRN}{w.name:<40}{RST}  {w.stat().st_size//1024:>5}KB")
            print("")


def main():
    parser = argparse.ArgumentParser(
        description="Tu dong dong bo ESP32 -> dongbo/ khi ket noi WiFi")
    parser.add_argument("--once", action="store_true",
        help="Chi sync 1 lan roi thoat (khong loop)")
    parser.add_argument("--node-a", default=CFG["node_a_ip"],
        help=f"IP Node-A (mac dinh: {CFG['node_a_ip']})")
    parser.add_argument("--node-b", default=CFG["node_b_ip"],
        help=f"IP Node-B (mac dinh: {CFG['node_b_ip']})")
    parser.add_argument("--interval", type=int, default=CFG["check_interval"],
        help=f"Khoang cach kiem tra WiFi (giay, mac dinh: {CFG['check_interval']})")
    parser.add_argument("--cooldown", type=int, default=CFG["cooldown"],
        help=f"Thoi gian cho sau khi sync (giay, mac dinh: {CFG['cooldown']})")
    args = parser.parse_args()

    # Cap nhat CFG tu args
    CFG["node_a_ip"]       = args.node_a
    CFG["node_b_ip"]       = args.node_b
    CFG["check_interval"]  = args.interval
    CFG["cooldown"]        = args.cooldown

    run_daemon(once=args.once)


if __name__ == "__main__":
    main()
