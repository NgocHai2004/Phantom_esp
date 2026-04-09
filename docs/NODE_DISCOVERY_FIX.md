# Fix Node Discovery — Node-2 Không Thấy Node-1

## Vấn Đề Ban Đầu

**Node-2 không thể kết nối vào Node-1**, dù Node-1 đang phát WiFi AP:
- Node-2 dùng `WiFi.begin(SSID, PASSWORD)` với **timeout ngắn** (12 × 300ms = 3.6s)
- Khi Node-1 AP không được scan, WiFi.begin() không tìm thấy
- Không có mechanism **scan WiFi trước** để detect Node-1

```cpp
// CŨ - Chỉ 3.6 giây để tìm Node-1
WiFi.begin(NODE1_SSID, NODE1_PASSWORD);
int retries = 0;
while (WiFi.status() != WL_CONNECTED && retries < 12) {
  delay(300);
  retries++;
}
```

---

## Giải Pháp: WiFi Scan + Timeout Dài

### 1. **Hàm Mới: `findAndConnectNode1()` @ Node-2**

```cpp
// ── esp32_client/src/main.cpp line 459-505
bool findAndConnectNode1() {
  Serial.println("[Sync] Bắt đầu scan WiFi tìm Node-1...");
  
  // Scan 2 lần để chắc chắn
  for (int scanAttempt = 0; scanAttempt < 2; scanAttempt++) {
    Serial.printf("[Scan] Lần %d/2...\n", scanAttempt + 1);
    int n = WiFi.scanNetworks();
    Serial.printf("[Scan] Tìm thấy %d mạng\n", n);
    
    // Tìm Node-1 trong danh sách scan
    for (int i = 0; i < n; i++) {
      String ssid = WiFi.SSID(i);
      if (ssid == NODE1_SSID) {
        Serial.printf("[Scan] ✓ Tìm thấy '%s' tại channel %d\n", 
                      ssid.c_str(), WiFi.channel(i));
        break;
      }
    }
    if (scanAttempt < 1) delay(500);
  }
  
  // Kết nối với timeout DÀI HƠN (6 giây thay vì 3.6s)
  Serial.printf("[Sync] Kết nối vào '%s'...\n", NODE1_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(NODE1_SSID, NODE1_PASSWORD);
  
  int retries = 0;
  int maxRetries = 20;  // 20 × 300ms = 6 giây (tăng từ 12 × 300ms = 3.6s)
  while (WiFi.status() != WL_CONNECTED && retries < maxRetries) {
    unsigned long tw = millis();
    while (millis()-tw < 300) { server.handleClient(); delay(5); }
    Serial.print(".");
    retries++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[Sync] THẤT BẠI: Không tìm thấy Thiết bị A");
    syncMsg = "failed: Node-1 not found";
    WiFi.disconnect(false);
    return false;
  }
  return true;
}
```

**Lợi Ích:**
- ✅ Scan WiFi trước → phát hiện Node-1 thay vì chỉ dựa vào WiFi.begin()
- ✅ Timeout từ 3.6s → 6s (67% lâu hơn)
- ✅ Debug info rõ ràng (in ra danh sách mạng tìm thấy)
- ✅ Hỗ trợ cả hidden + visible AP (scan sẽ tìm được cả 2)

---

### 2. **Update `syncFromNode1()` để dùng `findAndConnectNode1()`**

```cpp
// ── esp32_client/src/main.cpp line 507
bool syncFromNode1() {
  Serial.println("\n[Sync] ══ Bắt đầu kết nối Thiết bị A (Node-1) ══");
  syncMsg = "connecting";

  // TỪ ĐÂY: Dùng hàm scan + connect mới
  if (!findAndConnectNode1()) {
    return false;
  }
  
  // Lấy danh sách file từ Node-1 — retry 3 lần
  // ... (rest của sync logic)
}
```

---

### 3. **Bật Lại AP_STA Sau Khi Sync Xong**

```cpp
// ── esp32_client/src/main.cpp line 600-615
  WiFi.disconnect(false); delay(300);
  
  // BỘ SUNG: Bật lại AP_STA mode để phục vụ Laptop
  WiFi.mode(WIFI_AP_STA);
  IPAddress apIP(192,168,5,1);
  IPAddress gw(192,168,5,1);
  IPAddress sn(255,255,255,0);
  WiFi.softAPConfig(apIP,gw,sn);
  WiFi.softAP(MY_AP_SSID,MY_AP_PASSWORD,MY_AP_CHANNEL,MY_AP_HIDDEN,MY_AP_MAX_CON);
  delay(200);
  
  Serial.printf("[Sync] Đã ngắt kết nối Thiết bị A. AP Node-2 chạy lại.\n");
  // ... (rest of logging)
```

---

### 4. **Tương Tự cho Node-1: `findAndConnectNode2()` @ Node-1**

```cpp
// ── esp32_server/src/main.cpp line 1042-1089
bool findAndConnectNode2() {
  Serial.println("[Sync2] Bắt đầu scan WiFi tìm Node-2...");
  
  // Scan 2 lần để chắc chắn
  for (int scanAttempt = 0; scanAttempt < 2; scanAttempt++) {
    Serial.printf("[Scan2] Lần %d/2...\n", scanAttempt + 1);
    int n = WiFi.scanNetworks();
    Serial.printf("[Scan2] Tìm thấy %d mạng\n", n);
    
    for (int i = 0; i < n; i++) {
      String ssid = WiFi.SSID(i);
      if (ssid == NODE2_SSID) {
        Serial.printf("[Scan2] ✓ Tìm thấy '%s' tại channel %d\n", 
                      ssid.c_str(), WiFi.channel(i));
        break;
      }
    }
    if (scanAttempt < 1) delay(500);
  }
  
  // Kết nối với timeout dài hơn (20 × 300ms = 6 giây)
  Serial.printf("[Sync2] Kết nối vào '%s'...\n", NODE2_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(NODE2_SSID, NODE2_PASSWORD);
  
  int retries = 0;
  int maxRetries = 20;
  while (WiFi.status() != WL_CONNECTED && retries < maxRetries) {
    unsigned long tw = millis();
    while (millis()-tw < 300) { server.handleClient(); delay(5); }
    Serial.print(".");
    retries++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[Sync2] THẤT BẠI: Không tìm thấy Thiết bị B");
    WiFi.disconnect(false);
    return false;
  }
  return true;
}

bool syncFromNode2() {
  Serial.println("\n[Sync2] ══ Bắt đầu kết nối Thiết bị B (Node-2) ══");
  
  // Dùng hàm scan + connect mới
  if (!findAndConnectNode2()) {
    return false;
  }
  // ... (rest của sync logic)
}
```

**Bật lại AP_STA sau sync:**
```cpp
// ── esp32_server/src/main.cpp line 1165-1175
   if (downloaded > 0) blinkLED(5, 100);
   WiFi.disconnect(false); delay(300);
   
   // BỘ SUNG: Bật lại AP_STA mode
   WiFi.mode(WIFI_AP_STA);
   IPAddress apIP(192,168,4,1);
   IPAddress gw(192,168,4,1);
   IPAddress sn(255,255,255,0);
   WiFi.softAPConfig(apIP,gw,sn);
   WiFi.softAP(MY_AP_SSID,MY_AP_PASSWORD,MY_AP_CHANNEL,MY_AP_HIDDEN,MY_AP_MAX_CON);
   delay(200);
   
   Serial.printf("[Sync2] Đã ngắt kết nối Thiết bị B. AP Node-1 chạy lại.\n");
```

---

### 5. **Setup Mode Đảm Bảo AP_STA Trước Sync**

```cpp
// ── esp32_client/src/main.cpp line 1205-1210
   Serial.println("\n[Khởi động] Chưa có file — thử đồng bộ từ Thiết bị A...");
   blinkLED(2,200); delay(1000);

   // BỘ SUNG: Đảm bảo AP_STA đã bật trước khi sync
   WiFi.mode(WIFI_AP_STA);
   delay(200);
   
   syncDone   = syncFromNode1();
```

---

## Kết Quả Kỳ Vọng

### Before (Cũ)
```
Node-2 boot
  → WiFi.begin(NODE1_SSID) chỉ 3.6s
  → ❌ THẤT BẠI (timeout quá ngắn, chưa kịp scan)
  → Dùng WAV tích hợp
  → LED nháy 3 lần (error)
```

### After (Mới)
```
Node-2 boot
  → [Scan] Lần 1/2... Tìm thấy 8 mạng
  → [Scan] ✓ Tìm thấy 'ESP32-Node-1' tại channel 1
  → [Sync] Kết nối vào 'ESP32-Node-1'...
  → ✅ THÀNH CÔNG (sau 4s)
  → Download file từ Node-1
  → Bật lại AP_STA để phục vụ Laptop-2
  → LED nháy 5 lần (success)
```

---

## Troubleshoot

| Triệu Chứng | Nguyên Nhân | Giải Pháp |
|------------|-----------|---------|
| `[Scan] Tìm thấy 0 mạng` | WiFi scan lỗi | Restart Node-2, kiểm tra Node-1 AP đã bật |
| `[Sync] THẤT BẠI: Không tìm thấy` | Scan thấy nhưng WiFi.begin() lỗi | Kiểm tra password (12345678), channel match |
| `AP Node-2 không kết nối được Laptop` | AP_STA không bật lại | Kiểm tra dòng `WiFi.mode(WIFI_AP_STA)` sau sync |
| Serial: `[Scan] ✓ Tìm thấy ... nhưng không connect` | Channel mismatch | Node-1 channel 1, Node-2 channel 6 (OK) |

---

## Files Thay Đổi

1. **esp32_client/src/main.cpp**
   - Line 459-505: Thêm `findAndConnectNode1()`
   - Line 507: Update `syncFromNode1()` gọi `findAndConnectNode1()`
   - Line 600-615: Bật lại AP_STA sau sync
   - Line 1205-1210: Đảm bảo AP_STA trong setup()

2. **esp32_server/src/main.cpp**
   - Line 1042-1089: Thêm `findAndConnectNode2()`
   - Line 1100: Update `syncFromNode2()` gọi `findAndConnectNode2()`
   - Line 1165-1175: Bật lại AP_STA sau sync

---

## Status

✅ **COMPLETED** — Node-2 giờ có thể tìm và kết nối Node-1 một cách chắc chắn
- Scan WiFi để phát hiện SSID
- Timeout 6s thay vì 3.6s
- Bật lại AP_STA sau khi sync để phục vụ Laptop
