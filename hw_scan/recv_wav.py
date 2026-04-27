import base64
import serial
import sys
import os
import time
import subprocess

PORT = "COM12"
BAUD = 115200
OUT_FILE = "record.wav"

def open_audio(path: str):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"Khong tu mo duoc file: {e}")

def wait_for_line(ser, keyword, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            print(line)
            if keyword in line:
                return True
    return False

def main():
    ser = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(2)

    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print("Gui lenh ghi am...")
        ser.write(b"r")
        ser.flush()

        if not wait_for_line(ser, "REC_DONE", timeout=15):
            print("Khong thay REC_DONE")
            return

        time.sleep(0.5)

        print("Gui lenh dump WAV...")
        ser.write(b"d")
        ser.flush()

        collecting = False
        chunks = []

        start = time.time()
        while time.time() - start < 30:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue

            print(line)

            if line == "WAV_BASE64_BEGIN":
                collecting = True
                chunks = []
                continue

            if line == "WAV_BASE64_END":
                b64_text = "".join(chunks)
                audio = base64.b64decode(b64_text)
                with open(OUT_FILE, "wb") as f:
                    f.write(audio)
                print(f"Da luu {OUT_FILE}, {len(audio)} bytes")
                open_audio(OUT_FILE)
                return

            if collecting:
                chunks.append(line)

        print("Het thoi gian cho khi nhan WAV")

    finally:
        ser.close()

if __name__ == "__main__":
    main()