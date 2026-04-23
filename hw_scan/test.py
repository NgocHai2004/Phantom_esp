import wave

try:
    with wave.open("record_7295.wav", "rb") as w:
        print("OK WAV")
        print("channels:", w.getnchannels())
        print("rate:", w.getframerate())
        print("frames:", w.getnframes())
except Exception as e:
    print("WAV ERROR:", e)