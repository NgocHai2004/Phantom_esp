import serial, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
PORT = 'COM7'
BAUD = 115200
DURATION = 45
print(f"[*] Open {PORT} @ {BAUD}, reset ESP32, read {DURATION}s...", flush=True)
try:
    s = serial.Serial(PORT, BAUD, timeout=0.2)
except serial.SerialException as e:
    print(f"[!] Cannot open {PORT}: {e}")
    sys.exit(1)

# Reset ESP32: pulse RTS low while keeping DTR high (avoid entering bootloader)
s.setDTR(False)        # IO0 = HIGH (run mode, not bootloader)
s.setRTS(True)         # EN = LOW (reset)
time.sleep(0.2)
s.setRTS(False)        # EN = HIGH (release reset)
time.sleep(0.05)
s.reset_input_buffer()
print("[*] Reset sent, reading...")

end = time.time() + DURATION
total = 0
while time.time() < end:
    d = s.read(4096)
    if d:
        total += len(d)
        sys.stdout.write(d.decode('utf-8','replace'))
        sys.stdout.flush()
s.close()
print(f"\n[*] Total {total} bytes")
