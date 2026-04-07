"""
Integrity check: upload file → download lại → so sánh MD5/bytes
Kết nối WiFi vào ESP32-Node-1 (192.168.4.1) trước khi chạy.
"""
import socket, hashlib, os, sys, time

ESP_IP   = "192.168.4.1"
HTTP_PORT = 80
UPLOAD_PORT = 8081

def upload_raw(ip, port, fname, data):
    safe = fname.replace(" ", "_")
    s = socket.socket(); s.settimeout(30)
    s.connect((ip, port))
    req = (f"POST /upload HTTP/1.0\r\n"
           f"Host: {ip}\r\nContent-Type: application/octet-stream\r\n"
           f"Content-Length: {len(data)}\r\nX-Filename: {safe}\r\nConnection: close\r\n\r\n").encode()
    s.sendall(req)
    sent = 0
    while sent < len(data):
        end = min(sent+1024, len(data))
        s.sendall(data[sent:end]); sent = end
    resp = b""
    s.settimeout(10)
    try:
        while True:
            c = s.recv(4096)
            if not c: break
            resp += c
    except: pass
    s.close()
    return resp.decode(errors="replace"), safe

def download_raw(ip, port, fname):
    import urllib.parse
    s = socket.socket(); s.settimeout(30)
    s.connect((ip, port))
    enc = urllib.parse.quote(fname, safe=".-_")
    req = f"GET /file/download?name={enc} HTTP/1.1\r\nHost: {ip}\r\nConnection: close\r\n\r\n"
    s.sendall(req.encode())
    data = b""
    while True:
        try:
            c = s.recv(4096)
            if not c: break
            data += c
        except: break
    s.close()
    sep = data.find(b"\r\n\r\n")
    if sep < 0: return b""
    hdrs = data[:sep].decode(errors="replace")
    body = data[sep+4:]
    # parse content-length
    cl = -1
    for line in hdrs.split("\r\n"):
        if line.lower().startswith("content-length:"):
            try: cl = int(line.split(":",1)[1].strip())
            except: pass
    print(f"  Headers: {hdrs.split(chr(13))[0]}  CL={cl}  body_got={len(body)}")
    return body

# --- Test với file nhỏ ---
test_files = []
# Tìm file trong thư mục dongbo/
for root, dirs, files in os.walk("dongbo"):
    for f in files:
        path = os.path.join(root, f)
        sz = os.path.getsize(path)
        if sz < 200000:  # < 200KB
            test_files.append((path, f, sz))

if not test_files:
    print("Không tìm thấy file test trong dongbo/")
    sys.exit(1)

print(f"=== Integrity Check ESP32 {ESP_IP} ===\n")
for fpath, fname, fsz in test_files[:4]:  # test tối đa 4 file
    with open(fpath, "rb") as fi:
        orig = fi.read()
    md5_orig = hashlib.md5(orig).hexdigest()
    print(f"[Test] {fname} ({fsz} bytes)  MD5={md5_orig[:8]}...")

    # Upload
    t0 = time.time()
    resp, saved_name = upload_raw(ESP_IP, UPLOAD_PORT, fname, orig)
    print(f"  Upload resp: {resp.strip()[-80:]}")

    # Chờ SPIFFS ghi xong
    time.sleep(0.5)

    # Download lại
    dl_data = download_raw(ESP_IP, HTTP_PORT, saved_name)
    md5_dl = hashlib.md5(dl_data).hexdigest() if dl_data else "N/A"
    if md5_orig == md5_dl:
        match = "OK"
    else:
        match = f"CORRUPT orig={len(orig)} dl={len(dl_data)}"
    print(f"  MD5 match: {match}")

    # So sánh first bytes nếu corrupt
    if md5_orig != md5_dl and dl_data:
        print(f"  Orig[0:8]={orig[:8].hex()}  DL[0:8]={dl_data[:8].hex()}")
        # Tìm vị trí đầu tiên khác nhau
        for i, (a,b) in enumerate(zip(orig, dl_data)):
            if a != b:
                print(f"  First diff at byte {i}: orig={a:02x} dl={b:02x}")
                break

    # Lưu file download để kiểm tra
    out = f"_dl_{saved_name}"
    with open(out, "wb") as fo: fo.write(dl_data)
    print(f"  Saved → {out}  ({time.time()-t0:.1f}s)\n")

print("=== Done ===")
