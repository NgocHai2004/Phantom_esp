# APK Phantom-1 Replacement API Spec

## 1) Muc tieu

Tai lieu nay dinh nghia API va network contract de mot APK Android thay the hoan toan `Phantom-1` cho `Phantom-2` (ESP32 client) upload file.

APK can:
- Phat Wi-Fi AP de Phantom-2 ket noi.
- Cung cap HTTP API de Phantom-2 list file, register IP.
- Cung cap raw TCP upload port `8081` de nhan file `.bin`.

---

## 2) Network contract

### 2.1 Wi-Fi AP
- SSID: `Phantom-1`
- Password: `12345678`
- Security: WPA2-PSK
- AP IP (khuyen nghi): `192.168.4.1`

> Neu khong dung `192.168.4.1`, phai sua `PEER_IP` tren firmware Phantom-2.

### 2.2 Port
- HTTP API: `80`
- Raw upload: `8081`

---

## 3) Data model

## 3.1 File luu tru
- Thu muc logic: `rec/`
- Kieu file chinh: `.bin`
- Ten file vi du: `rec2_no_time_000001.bin`

### 3.2 Quy tac trung ten
- Neu trung ten + cung size: coi nhu da ton tai (idempotent).
- Neu trung ten + khac size: tao ten moi theo hau to:
  - `name.bin` -> `name[1].bin` -> `name[2].bin` -> ...

---

## 4) API bat buoc

Tat ca endpoint quan trong ben duoi deu yeu cau header:
- `X-Auth: <API_KEY>`

`API_KEY` la pre-shared key cau hinh san giong nhau tren APK va Phantom-2.

## 4.1 `GET /file/list`

Muc dich: Phantom-2 lay danh sach file da co tren APK.

### Response 200
```json
{
  "files": [
    {"name":"rec2_no_time_000001.bin","size":496206},
    {"name":"rec2_no_time_000002.bin","size":2825806}
  ],
  "count": 2
}
```

### Rule
- `name`: chi ten file (khong bat buoc prefix `/rec/`).
- `size`: kich thuoc byte.
- Luon tra JSON hop le.

---

## 4.2 `POST /peer/register`

Muc dich: Phantom-2 gui STA IP cua no cho peer.

### Request
- Header: `X-Auth: <API_KEY>`
- Content-Type: `application/x-www-form-urlencoded`
- Body: `ip=<phantom2_sta_ip>`

Vi du:
```http
POST /peer/register HTTP/1.1
Content-Type: application/x-www-form-urlencoded

ip=192.168.4.2
```

### Response 200
```json
{
  "status": "ok",
  "peer_ip": "192.168.4.2"
}
```

### Error 400
```json
{
  "status":"fail",
  "error":"bad ip"
}
```

---

## 4.3 `GET /busy`

Muc dich: tuong thich voi luong check trang thai cua firmware.

### Response 200 (khuyen nghi)
```json
{
  "node": 1,
  "busy": false,
  "recording": false,
  "sync_in_progress": false,
  "peer_sync_request_in_progress": false,
  "peer_sync_queued": false,
  "sync_pending": false,
  "reason": "idle",
  "sync_msg": "apk ready",
  "last_mic_file": "none"
}
```

### Rule
- APK khong ghi am, nhung van nen giu schema nay de Phantom-2 parse on dinh.
- Neu APK dang ghi file upload lon, co the dat `busy=true`, `reason="uploading"`.

---

## 5) Raw upload API (bat buoc)

## 5.1 `POST /` tren port `8081`

Muc dich: Phantom-2 upload file binary truc tiep.

### Request format
- TCP socket toi `<apk_ip>:8081`
- HTTP-like request line: `POST / HTTP/1.1`
- Header bat buoc:
  - `Content-Length: <int>`
  - `X-Filename: <filename>`
  - `X-Auth: <API_KEY>`
- Body: bytes file

### Vi du request
```http
POST / HTTP/1.1
Host: 192.168.4.1
Content-Length: 496206
X-Filename: rec2_no_time_000001.bin
Connection: close

<binary bytes>
```

### Response success (bat buoc co `sd_saved:true`)
```json
{
  "status":"ok",
  "filename":"rec2_no_time_000001.bin",
  "sd_saved":true
}
```

### Response fail
```json
{
  "status":"fail",
  "filename":"rec2_no_time_000001.bin",
  "sd_saved":false
}
```

### Auth fail
```json
{"error":"bad auth"}
```

### Rule quan trong
- Phantom-2 chi coi upload thanh cong khi:
  - HTTP status 200
  - Body co `"status":"ok"`
  - Body co `"sd_saved":true`

Neu thieu 1 trong 3 dieu kien tren, Phantom-2 se tinh la `failed`.

---

## 6) Upload behavior bat buoc

Khi nhan file tren 8081:
1. Validate `Content-Length > 0`.
2. Lay `X-Filename`, sanitize ten.
3. Resolve ten theo quy tac trung ten:
   - cung ten + cung size -> co the ghi de cung noi dung hoac bo qua nhung response van thanh cong.
   - cung ten + khac size -> doi sang `[1]`, `[2]`, ...
4. Ghi file atomically (temp -> rename la tot nhat).
5. Verify size file sau ghi == `Content-Length`.
6. Tra response JSON co `sd_saved:true/false`.

---

## 7) Error handling

## 7.1 `/file/list`
- Neu co loi tam thoi, van nen tra JSON:
```json
{"files":[],"count":0}
```
Tranh tra HTML/plain text.

## 7.2 `/peer/register`
- `400` khi ip invalid.

## 7.3 `8081 upload`
- `400` khi `Content-Length` khong hop le.
- `500` khi khong ghi duoc file.
- Luon tra JSON co field `status` va `sd_saved`.

---

## 8) Non-functional requirements

- Handle file toi thieu 50 MB.
- Timeout ghi doc socket >= 30s.
- Khong crash neu client ngat giua chung.
- Persist file sau khi app restart.
- Foreground service de giu server song.

---

## 9) Android implementation notes (goi y)

- Dung 1 HTTP server embedded (Ktor/NanoHTTPD) cho port 80.
- Dung ServerSocket rieng cho port 8081.
- Luu file vao app private storage (vi du: `<app_files>/rec`).
- Neu can cho user xem file, bo sung SAF export rieng.

---

## 10) Compatibility checklist (QA)

- [ ] APK phat AP `Phantom-1` ket noi duoc tu ESP32.
- [ ] `GET /file/list` tra dung schema.
- [ ] `POST /peer/register` tra `status=ok`.
- [ ] Upload 8081 file moi -> `sd_saved:true`, file ton tai dung size.
- [ ] Upload file trung ten cung size -> khong tao file loi.
- [ ] Upload file trung ten khac size -> tao `...[1]`, `...[2]`...
- [ ] Trong 100 lan upload lien tuc khong crash service.

---

## 11) Sample end-to-end flow

1. Phantom-2 ket noi AP `Phantom-1`.
2. Phantom-2 `GET /file/list`.
3. Phantom-2 so sanh local/remote:
   - cung ten + cung size => skip
   - con lai => upload `8081`
4. Sau moi file upload, APK tra:
```json
{"status":"ok","filename":"...","sd_saved":true}
```
5. Phantom-2 tiep tuc den het danh sach.
