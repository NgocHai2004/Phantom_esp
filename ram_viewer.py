c#!/usr/bin/env python3
"""
ram_viewer.py — Xem RAM buffer của ESP32 Server và Client từ PC
═══════════════════════════════════════════════════════════════
Kết nối qua WiFi tới ESP32-Audio-AP (192.168.4.1)
Cho phép xem:
  - Danh sách file đã nhận vào RAM của Server
  - Chi tiết WAV header trong RAM
  - Raw hex bytes của buffer
  - Trạng thái Client (nếu biết IP)

Usage:
  python ram_viewer.py                  # interactive menu
  python ram_viewer.py --server-only    # chỉ xem Server
  python ram_viewer.py --client 192.168.4.2  # xem cả Client
  python ram_viewer.py --watch 5        # auto-refresh mỗi 5 giây
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

# ── Cấu hình ─────────────────────────────────────────────────────────────────
SERVER_IP          = "192.168.4.1"
SERVER_HTTP_PORT   = 80
SERVER_AUDIO_PORT  = 8080
CLIENT_HTTP_PORT   = 81
CLIENT_AUDIO_PORT  = 8081
TIMEOUT            = 5  # giây

# ── ANSI Colors ───────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    GRAY   = "\033[90m"
    BG_DARK= "\033[40m"

def colored(text, color):
    return f"{color}{text}{C.RESET}"

def ok(s):    return colored(s, C.GREEN)
def warn(s):  return colored(s, C.YELLOW)
def err(s):   return colored(s, C.RED)
def info(s):  return colored(s, C.CYAN)
def bold(s):  return colored(s, C.BOLD)
def gray(s):  return colored(s, C.GRAY)

# ── HTTP helpers ──────────────────────────────────────────────────────────────
def http_get(url: str, timeout: int = TIMEOUT) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RAM-Viewer/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.URLError as e:
        print(err(f"  [ERROR] GET {url} → {e.reason}"))
        return None
    except json.JSONDecodeError as e:
        print(err(f"  [ERROR] JSON parse: {e}"))
        return None
    except Exception as e:
        print(err(f"  [ERROR] {e}"))
        return None


def http_post(url: str, body: str = "", timeout: int = TIMEOUT) -> dict | None:
    try:
        data = body.encode("utf-8") if body else b""
        req  = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type",   "application/json")
        req.add_header("Content-Length", str(len(data)))
        req.add_header("User-Agent",     "RAM-Viewer/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.URLError as e:
        print(err(f"  [ERROR] POST {url} → {e.reason}"))
        return None
    except json.JSONDecodeError:
        return {"status": "ok"}
    except Exception as e:
        print(err(f"  [ERROR] {e}"))
        return None


# ── Formatter helpers ─────────────────────────────────────────────────────────
def fmt_bytes(n: int) -> str:
    if n < 1024:       return f"{n} B"
    if n < 1024*1024:  return f"{n/1024:.1f} KB"
    return f"{n/(1024*1024):.2f} MB"

def fmt_duration(sec: float) -> str:
    if sec <= 0: return "0s"
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}m {s:.2f}s" if m > 0 else f"{s:.2f}s"

def separator(char="─", width=60):
    print(gray(char * width))

# ── Hiển thị Server Status ────────────────────────────────────────────────────
def show_server_status():
    url = f"http://{SERVER_IP}:{SERVER_HTTP_PORT}/status"
    print(f"\n{bold('═══ ESP32 SERVER STATUS ═══')}  {gray(url)}")
    d = http_get(url)
    if not d:
        print(err("  ❌ Không thể kết nối Server"))
        return False
    separator()
    print(f"  IP          : {ok(d.get('ip', '?'))}")
    print(f"  SSID        : {info(d.get('ssid', '?'))}")
    print(f"  Uptime      : {d.get('uptime', '?')}")
    print(f"  WiFi Clients: {ok(str(d.get('stations_connected', 0)))} stations kết nối")
    print(f"  Registered  : {ok(str(d.get('registered_clients', 0)))} clients đăng ký")
    heap = d.get('free_heap', 0)
    heap_color = ok if heap > 100000 else (warn if heap > 50000 else err)
    print(f"  Free Heap   : {heap_color(fmt_bytes(heap))}")
    separator()
    ram_ready = d.get('ram_audio_ready', False)
    ram_bytes = d.get('ram_audio_bytes', 0)
    files_rx  = d.get('ram_files_received', 0)
    builtin   = d.get('builtin_wav_bytes', 0)
    print(f"  RAM Buffer  : {ok('READY ' + fmt_bytes(ram_bytes)) if ram_ready else warn('EMPTY')}")
    print(f"  Files Rx'd  : {ok(str(files_rx))} file(s) đã nhận vào RAM")
    print(f"  Built-in WAV: {gray(fmt_bytes(builtin))}")
    return True


# ── Hiển thị RAM List (Server) ────────────────────────────────────────────────
def show_server_ram_list():
    url = f"http://{SERVER_IP}:{SERVER_HTTP_PORT}/ram/list"
    print(f"\n{bold('═══ SERVER RAM FILE LIST ═══')}  {gray(url)}")
    d = http_get(url)
    if not d:
        print(err("  ❌ Không lấy được danh sách RAM"))
        return

    total = d.get('total_received', 0)
    active_slot = d.get('current_active', -1)
    ram_used = d.get('ram_used_bytes', 0)
    free_heap = d.get('free_heap', 0)
    audio_ready = d.get('audio_ready', False)

    separator()
    print(f"  Tổng file đã nhận : {ok(str(total))}")
    print(f"  RAM đang dùng     : {ok(fmt_bytes(ram_used)) if audio_ready else warn('0 B (empty)')}")
    print(f"  Free Heap         : {fmt_bytes(free_heap)}")
    separator()

    files = d.get('files', [])
    if not files:
        print(warn("  ⚠ Chưa có file nào được nhận vào RAM"))
        print(gray("  (Hãy chạy demo: Client boot lên và push file test.wav)"))
        return

    print(f"  {'#':<4} {'ACTIVE':<7} {'FROM IP':<16} {'SOURCE':<14} {'SIZE':<10} {'RECEIVED AT':<12} {'WAV?'}")
    separator("─", 80)
    for f in files:
        slot      = f.get('slot', 0)
        is_active = f.get('active', False)
        from_ip   = f.get('from', '?')
        source    = f.get('source', '?')
        size_kb   = f.get('size_kb', 0)
        size_b    = f.get('size_bytes', 0)
        rx_at     = f.get('received_at', '?')
        is_wav    = f.get('is_wav', False)

        active_mark = ok("★ ACTIVE") if is_active else gray("  slot")
        wav_str = ok("WAV ✓") if is_wav else warn("raw")
        size_str = f"{size_b:,} B ({size_kb} KB)"

        print(f"  [{slot}] {active_mark:<18} {from_ip:<16} {source:<14} {size_str:<18} {rx_at:<12} {wav_str}")

        # WAV details
        if is_wav and 'wav' in f:
            w = f['wav']
            ch   = w.get('channels', 0)
            sr   = w.get('sample_rate', 0)
            bits = w.get('bits_per_sample', 0)
            dur  = w.get('duration_sec', 0)
            print(f"       {gray('└─ WAV: ' + str(sr) + 'Hz, ' + str(ch) + 'ch, ' + str(bits) + 'bit, ' + fmt_duration(dur))}")
    separator("─", 80)


# ── Hiển thị RAM Info chi tiết (Server) ──────────────────────────────────────
def show_server_ram_info():
    url = f"http://{SERVER_IP}:{SERVER_HTTP_PORT}/ram/info"
    print(f"\n{bold('═══ SERVER RAM BUFFER INFO ═══')}  {gray(url)}")
    d = http_get(url)
    if not d:
        print(err("  ❌ Không lấy được RAM info"))
        return

    if not d.get('ram_ready', False):
        print(warn(f"  ⚠ {d.get('message', 'Chưa có file trong RAM')}"))
        heap = d.get('free_heap', 0)
        print(f"  Free Heap: {fmt_bytes(heap)}")
        return

    separator()
    size_b  = d.get('size_bytes', 0)
    size_kb = d.get('size_kb', 0)  # đây là string từ firmware
    magic   = d.get('magic', '???')
    is_wav  = d.get('is_wav', False)
    from_ip = d.get('received_from', 'unknown')
    source  = d.get('source', 'unknown')
    rx_at   = d.get('received_at', 'unknown')
    heap    = d.get('free_heap', 0)

    print(f"  {'✅ FILE TRONG RAM':}")
    print(f"  Kích thước  : {ok(f'{size_b:,} bytes ({size_kb} KB)')}")
    print(f"  Magic bytes : {info(magic)}")
    print(f"  Nhận từ     : {ok(from_ip)}")
    print(f"  Nguồn       : {info(source)}")
    print(f"  Nhận lúc    : {d.get('received_at', '?')}")
    print(f"  Free Heap   : {fmt_bytes(heap)}")

    if is_wav and 'wav_header' in d:
        w = d['wav_header']
        fmt_name = w.get('format_name', '?')
        channels = w.get('channels', 0)
        sr       = w.get('sample_rate', 0)
        bits     = w.get('bits_per_sample', 0)
        dur      = w.get('duration_sec', 0.0)
        data_sz  = w.get('data_size', 0)
        byte_rate= w.get('byte_rate', 0)
        separator("─")
        print(f"  {bold('WAV HEADER')}")
        print(f"  Format      : {ok(fmt_name)} (code {w.get('audio_format', 0)})")
        print(f"  Channels    : {ok(str(channels))} {'(Stereo)' if channels==2 else '(Mono)' if channels==1 else ''}")
        print(f"  Sample Rate : {ok(str(sr))} Hz")
        print(f"  Bits/Sample : {ok(str(bits))} bit")
        print(f"  Byte Rate   : {fmt_bytes(byte_rate)}/s")
        print(f"  Data Size   : {fmt_bytes(data_sz)}")
        print(f"  Duration    : {ok(fmt_duration(dur))}")
    elif not is_wav:
        print(warn("  ⚠ Không phải file WAV hợp lệ (magic header sai)"))
    separator()


# ── Hiển thị RAM Hex (Server) ─────────────────────────────────────────────────
def show_hex_dump(device: str, ip: str, port: int, offset: int = 0, length: int = 64):
    url = f"http://{ip}:{port}/ram/hex?offset={offset}&len={length}"
    print(f"\n{bold(f'═══ {device} RAM HEX DUMP ═══')}  {gray(url)}")
    d = http_get(url)
    if not d:
        print(err("  ❌ Không lấy được hex dump"))
        return
    if 'error' in d:
        print(err(f"  ❌ {d['error']}"))
        return

    source    = d.get('source', '?')
    total_sz  = d.get('total_size', 0)
    off       = d.get('offset', 0)
    ln        = d.get('len', 0)
    hex_str   = d.get('hex', '')
    ascii_str = d.get('ascii', '')

    separator()
    print(f"  Source: {info(source)}  Total: {fmt_bytes(total_sz)}  "
          f"Offset: {off}  Showing: {ln} bytes")
    separator()

    # Parse hex bytes
    hex_bytes = hex_str.split()
    cols = 16  # bytes per row
    for row in range(0, len(hex_bytes), cols):
        chunk = hex_bytes[row:row+cols]
        addr  = off + row
        hex_part   = " ".join(chunk).ljust(cols * 3)
        ascii_part = ascii_str[row:row+cols] if row < len(ascii_str) else ""
        print(f"  {gray(f'{addr:06X}:')}  {info(hex_part)}  {gray('|')}{ok(ascii_part)}{gray('|')}")
    separator()


# ── Hiển thị Client RAM Info ──────────────────────────────────────────────────
def show_client_ram_info(client_ip: str):
    url = f"http://{client_ip}:{CLIENT_HTTP_PORT}/ram/info"
    print(f"\n{bold('═══ CLIENT RAM BUFFER INFO ═══')}  {gray(url)}")
    d = http_get(url)
    if not d:
        print(err(f"  ❌ Không thể kết nối Client {client_ip}"))
        return

    if not d.get('ram_ready', False):
        print(warn(f"  ⚠ {d.get('message', 'Chưa có file trong RAM')}"))
        heap = d.get('free_heap', 0)
        print(f"  Free Heap: {fmt_bytes(heap)}")
        return

    separator()
    size_b  = d.get('size_bytes', 0)
    is_wav  = d.get('is_wav', False)
    from_ip = d.get('received_from', 'unknown')
    source  = d.get('source', 'unknown')
    heap    = d.get('free_heap', 0)

    print(f"  {'✅ FILE TRONG RAM CLIENT':}")
    print(f"  Kích thước  : {ok(f'{size_b:,} bytes')}")
    print(f"  Nhận từ     : {ok(from_ip)}")
    print(f"  Nguồn       : {info(source)}")
    print(f"  Nhận lúc    : {d.get('received_at', '?')}")
    print(f"  Free Heap   : {fmt_bytes(heap)}")

    if is_wav and 'wav_header' in d:
        w = d['wav_header']
        channels = w.get('channels', 0)
        sr       = w.get('sample_rate', 0)
        bits     = w.get('bits_per_sample', 0)
        dur      = w.get('duration_sec', 0.0)
        separator("─")
        print(f"  {bold('WAV HEADER')}")
        print(f"  Format      : {ok(w.get('format_name', '?'))}")
        print(f"  Channels    : {ok(str(channels))} {'(Stereo)' if channels==2 else '(Mono)'}")
        print(f"  Sample Rate : {ok(str(sr))} Hz")
        print(f"  Bits/Sample : {ok(str(bits))} bit")
        print(f"  Duration    : {ok(fmt_duration(dur))}")
    elif not is_wav:
        print(warn("  ⚠ Không phải file WAV hợp lệ"))
    separator()


# ── Hiển thị Client Status ────────────────────────────────────────────────────
def show_client_status(client_ip: str):
    url = f"http://{client_ip}:{CLIENT_HTTP_PORT}/status"
    print(f"\n{bold('═══ ESP32 CLIENT STATUS ═══')}  {gray(url)}")
    d = http_get(url)
    if not d:
        print(err(f"  ❌ Không thể kết nối Client {client_ip}"))
        return False
    separator()
    print(f"  IP          : {ok(d.get('ip', '?'))}")
    print(f"  Uptime      : {d.get('uptime', '?')}")
    print(f"  Server      : {info(d.get('server', '?'))}")
    heap = d.get('free_heap', 0)
    heap_color = ok if heap > 100000 else (warn if heap > 50000 else err)
    print(f"  Free Heap   : {heap_color(fmt_bytes(heap))}")
    ram_ready = d.get('ram_audio_ready', False)
    ram_bytes = d.get('ram_audio_bytes', 0)
    builtin   = d.get('builtin_wav_bytes', 0)
    print(f"  RAM Buffer  : {ok('READY ' + fmt_bytes(ram_bytes)) if ram_ready else warn('EMPTY')}")
    print(f"  Built-in WAV: {gray(fmt_bytes(builtin))}")
    return True


# ── Demo: trigger transfer và xem kết quả ─────────────────────────────────────
def demo_transfer_and_view(client_ip: str = None):
    print(f"\n{bold('═══ DEMO: CLIENT → SERVER TRANSFER ═══')}")
    print(info("  Bước 1: Kiểm tra Server trước..."))
    if not show_server_status():
        print(err("  Server không phản hồi. Hãy chắc chắn ESP32 Server đang chạy."))
        return

    print(f"\n{info('  Bước 2: Xem RAM Server hiện tại...')}")
    show_server_ram_list()

    if client_ip:
        print(f"\n{info(f'  Bước 3: Yêu cầu Client {client_ip} push file lên Server...')}")
        url = f"http://{client_ip}:{CLIENT_HTTP_PORT}/audio/push_builtin"
        print(f"  POST {url}")
        r = http_post(url)
        if r:
            if r.get('status') == 'sent':
                print(ok(f"  ✅ Client đã push {r.get('bytes', 0):,} bytes lên Server"))
            else:
                print(err(f"  ❌ Push thất bại: {r}"))
        else:
            print(warn("  ⚠ Không có response từ Client"))

        print(f"\n{info('  Chờ 2 giây...')}")
        time.sleep(2)

        print(f"\n{info('  Bước 4: Xem RAM Server sau khi nhận file...')}")
        show_server_ram_list()
        show_server_ram_info()

        print(f"\n{info('  Bước 5: Xem RAM Client...')}")
        show_client_ram_info(client_ip)
    else:
        print(info("\n  (Không có client_ip → bỏ qua bước push)"))
        print(info("  Dùng: python ram_viewer.py --client 192.168.4.2 --demo"))


# ── Interactive menu ──────────────────────────────────────────────────────────
def interactive_menu(client_ip: str = None):
    while True:
        print(f"\n{bold('╔══════════════════════════════════════════╗')}")
        print(f"{bold('║      ESP32 RAM VIEWER - MENU            ║')}")
        print(f"{bold('╠══════════════════════════════════════════╣')}")
        print(f"  {ok('1')}. Xem Server Status")
        print(f"  {ok('2')}. Xem RAM List (Server) ← danh sách file đã nhận")
        print(f"  {ok('3')}. Xem RAM Info chi tiết (Server) ← WAV header")
        print(f"  {ok('4')}. Xem RAM Hex dump (Server)")
        if client_ip:
            print(f"  {ok('5')}. Xem Client Status ({client_ip})")
            print(f"  {ok('6')}. Xem RAM Client ({client_ip})")
            print(f"  {ok('7')}. Xem RAM Hex Client")
            print(f"  {ok('8')}. Demo: Client push → Server → Xem kết quả")
            print(f"  {ok('9')}. Yêu cầu Server broadcast tới tất cả")
        print(f"  {ok('c')}. Xóa RAM Server")
        print(f"  {ok('q')}. Thoát")
        print(f"{bold('╚══════════════════════════════════════════╝')}")
        print(f"  Server: {info(SERVER_IP)}:{SERVER_HTTP_PORT}   "
              + (f"Client: {info(client_ip)}:{CLIENT_HTTP_PORT}" if client_ip else gray("Client: (chưa set --client IP)")))

        choice = input(f"\n{bold('Chọn:')} ").strip().lower()

        if choice == '1':
            show_server_status()
        elif choice == '2':
            show_server_ram_list()
        elif choice == '3':
            show_server_ram_info()
        elif choice == '4':
            try:
                off = int(input("  Offset (mặc định 0): ") or "0")
                ln  = int(input("  Số bytes (mặc định 64, max 256): ") or "64")
            except ValueError:
                off, ln = 0, 64
            show_hex_dump("SERVER", SERVER_IP, SERVER_HTTP_PORT, off, ln)
        elif choice == '5' and client_ip:
            show_client_status(client_ip)
        elif choice == '6' and client_ip:
            show_client_ram_info(client_ip)
        elif choice == '7' and client_ip:
            try:
                off = int(input("  Offset (mặc định 0): ") or "0")
                ln  = int(input("  Số bytes (mặc định 64, max 256): ") or "64")
            except ValueError:
                off, ln = 0, 64
            show_hex_dump("CLIENT", client_ip, CLIENT_HTTP_PORT, off, ln)
        elif choice == '8' and client_ip:
            demo_transfer_and_view(client_ip)
        elif choice == '9' and client_ip:
            url = f"http://{SERVER_IP}:{SERVER_HTTP_PORT}/broadcast"
            print(f"  POST {url}")
            r = http_post(url)
            if r:
                print(ok(f"  ✅ Broadcast: {r}"))
        elif choice == 'c':
            url = f"http://{SERVER_IP}:{SERVER_HTTP_PORT}/ram/clear"
            r = http_post(url)
            if r:
                print(ok(f"  ✅ RAM đã xóa: {r.get('message', '')}"))
        elif choice == 'q':
            print(gray("  Thoát."))
            break
        else:
            print(warn("  ⚠ Không hợp lệ. Chọn lại."))


# ── Watch mode ─────────────────────────────────────────────────────────────────
def watch_mode(interval: int, client_ip: str = None):
    print(info(f"  Watch mode: refresh mỗi {interval}s. Ctrl+C để thoát.\n"))
    try:
        while True:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n{gray(f'[{ts}] Refreshing...')}")
            show_server_status()
            show_server_ram_list()
            if client_ip:
                show_client_status(client_ip)
                show_client_ram_info(client_ip)
            print(info(f"  Đợi {interval}s... (Ctrl+C để thoát)"))
            time.sleep(interval)
    except KeyboardInterrupt:
        print(gray("\n  Watch mode dừng."))


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="ESP32 RAM Viewer - Xem file trong RAM của ESP32",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python ram_viewer.py                           # interactive menu
  python ram_viewer.py --client 192.168.4.2      # thêm client vào menu
  python ram_viewer.py --watch 5                 # auto-refresh 5s
  python ram_viewer.py --demo                    # quick demo không cần menu
  python ram_viewer.py --client 192.168.4.2 --demo  # demo có client
  python ram_viewer.py --server-ip 192.168.4.1   # custom server IP
        """
    )
    parser.add_argument("--server-ip",    default=SERVER_IP,   help=f"IP của ESP32 Server (mặc định: {SERVER_IP})")
    parser.add_argument("--client",       default=None,        help="IP của ESP32 Client (vd: 192.168.4.2)")
    parser.add_argument("--watch",        type=int, default=0, help="Auto-refresh mỗi N giây")
    parser.add_argument("--demo",         action="store_true", help="Chạy demo transfer và xem kết quả")
    parser.add_argument("--server-only",  action="store_true", help="Chỉ show Server info và thoát")
    parser.add_argument("--hex",          action="store_true", help="Hiển thị hex dump của RAM")
    parser.add_argument("--hex-offset",   type=int, default=0, help="Offset cho hex dump (mặc định: 0)")
    parser.add_argument("--hex-len",      type=int, default=64, help="Số bytes hex dump (mặc định: 64)")
    args = parser.parse_args()

    # Override global
    global SERVER_IP
    SERVER_IP = args.server_ip

    print(f"\n{bold('ESP32 RAM VIEWER')}")
    print(gray(f"  Server: {SERVER_IP}:{SERVER_HTTP_PORT}"))
    if args.client:
        print(gray(f"  Client: {args.client}:{CLIENT_HTTP_PORT}"))
    print()

    if args.server_only:
        show_server_status()
        show_server_ram_list()
        show_server_ram_info()
        return

    if args.hex:
        show_hex_dump("SERVER", SERVER_IP, SERVER_HTTP_PORT, args.hex_offset, args.hex_len)
        if args.client:
            show_hex_dump("CLIENT", args.client, CLIENT_HTTP_PORT, args.hex_offset, args.hex_len)
        return

    if args.demo:
        demo_transfer_and_view(args.client)
        return

    if args.watch > 0:
        watch_mode(args.watch, args.client)
        return

    # Default: interactive menu
    interactive_menu(args.client)


if __name__ == "__main__":
    main()
