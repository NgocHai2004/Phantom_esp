# Huong dan su dung ESP32 Audio Transfer

## Gioi thieu

He thong gom 2 ESP32 phat WiFi an:

| Node    | SSID           | Mat khau | IP           |
|---------|----------------|----------|--------------|
| Node-1  | ESP32-Node-1   | 12345678 | 192.168.4.1  |
| Node-2  | ESP32-Node-2   | 12345678 | 192.168.5.1  |

Ca 2 node dong bo file voi nhau — bat WiFi node nao cung duoc.

---

## Yeu cau

- **Python 3.7+** — tai tai [python.org/downloads](https://www.python.org/downloads/)
- **customtkinter** — giao dien do hoa cua `chuyen.py`

Cai thu vien (chi 1 lan):
```
pip install customtkinter
```

---

## Su dung chuyen.py

```
python chuyen.py
```

### Cach dung

1. Bat WiFi `ESP32-Node-1` hoac `ESP32-Node-2` (mat khau: `12345678`)
2. Chay `python chuyen.py`
3. Doi thanh trang thai **"Node-1 ket noi"** hoac **"Node-2 ket noi"** (chu xanh)

### Upload file len ESP32

- Click nut **"Gui file len ESP32"**
- Chon file `.wav` trong hop thoai
- File duoc upload tu dong, hien thi tien trinh

### Tai file ve may

- Xem danh sach file o khu vuc chinh
- Click nut **⬇** ben canh file muon tai
- File luu vao thu muc **Downloads**
- Click **"Mo thu muc Downloads"** de mo nhanh

### Xoa file khoi ESP32

- Click nut **✕** ben canh file muon xoa
- Danh sach tu cap nhat

### Lam moi danh sach

- Click nut **"Lam moi"** goc tren phai

---

## Nut BOOT tren ESP32

Moi ESP32 co nut **BOOT** de tat/bat WiFi:

| Trang thai   | Nhan BOOT | Ket qua                   |
|--------------|-----------|---------------------------|
| Dang **BAT** | 1 lan     | WiFi tat, LED tat         |
| Dang **TAT** | 1 lan     | WiFi bat lai, LED sang    |

> **LED sang** = node dang hoat dong  
> **LED tat** = node da tat

---

## Xu ly su co

| Trieu chung | Cach xu ly |
|-------------|------------|
| Khong thay WiFi ESP32 | Nhan nut BOOT tren ESP32 de bat lai |
| Loi "No module named customtkinter" | Chay: `pip install customtkinter` |
| Upload that bai | Kiem tra WiFi dang bat dung mang ESP32 |
| "Bo nho day" | Xoa bot file cu trong danh sach roi thu lai |
| Nut ⬇ khong hoat dong | Thu click nut "Lam moi" truoc |
