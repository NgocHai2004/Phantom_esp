#!/usr/bin/env python3
import argparse
import http.client
import os
import tempfile
import time
import uuid


def make_temp_file(size_mb: int) -> str:
    fd, path = tempfile.mkstemp(prefix="esp32_speed_", suffix=".bin")
    os.close(fd)
    total = size_mb * 1024 * 1024
    chunk = b"\x00" * (1024 * 1024)
    written = 0
    with open(path, "wb") as f:
        while written < total:
            n = min(len(chunk), total - written)
            f.write(chunk[:n])
            written += n
    return path


def upload_file(host: str, port: int, file_path: str, endpoint: str) -> tuple[int, float, str]:
    boundary = "----ESP32Boundary" + uuid.uuid4().hex
    filename = os.path.basename(file_path)

    preamble = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")
    file_size = os.path.getsize(file_path)
    content_length = len(preamble) + file_size + len(epilogue)

    conn = http.client.HTTPConnection(host, port, timeout=120)
    conn.putrequest("POST", endpoint)
    conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
    conn.putheader("Content-Length", str(content_length))
    conn.endheaders()

    start = time.perf_counter()
    conn.send(preamble)
    sent = 0
    with open(file_path, "rb") as f:
        while True:
            data = f.read(64 * 1024)
            if not data:
                break
            conn.send(data)
            sent += len(data)
    conn.send(epilogue)

    response = conn.getresponse()
    body = response.read().decode("utf-8", errors="replace")
    elapsed = time.perf_counter() - start
    conn.close()
    return sent, elapsed, body


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test upload speed to ESP32 AP server in src/main.cpp"
    )
    parser.add_argument("--host", default="192.168.4.1", help="ESP32 IP (default: 192.168.4.1)")
    parser.add_argument("--port", type=int, default=80, help="ESP32 HTTP port (default: 80)")
    parser.add_argument("--endpoint", default="/upload", help="Upload endpoint (default: /upload)")
    parser.add_argument("--file", help="Path to file to upload")
    parser.add_argument("--size-mb", type=int, default=20, help="Temp file size if --file is omitted")
    args = parser.parse_args()

    temp_file = None
    target_file = args.file
    if not target_file:
        temp_file = make_temp_file(args.size_mb)
        target_file = temp_file

    try:
        print(f"Uploading: {target_file}")
        sent_bytes, elapsed, server_text = upload_file(args.host, args.port, target_file, args.endpoint)
        mbps = (sent_bytes * 8) / elapsed / 1_000_000 if elapsed > 0 else 0.0
        mbs = sent_bytes / elapsed / (1024 * 1024) if elapsed > 0 else 0.0

        print("\nClient result")
        print(f"Sent bytes : {sent_bytes}")
        print(f"Time       : {elapsed:.3f} s")
        print(f"Speed      : {mbs:.3f} MB/s ({mbps:.2f} Mbps)")

        print("\nESP32 response")
        print(server_text.strip())
    finally:
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)


if __name__ == "__main__":
    main()
