# BA Research Spec — AES-GCM At-Rest Encryption for Phantom Firmware

## 1) Request Classification
- **Type:** FEATURE + BUG-FIX
- **Feature:** Encrypt all newly ingested artifacts before SD persistence (mic recording + PC uploads) and ensure sync artifacts are encrypted.
- **Bug-fix:** Current priority-sync verify is mic-specific and may verify wrong file for non-mic writes.

## 2) Scope
### In scope
- Firmware behavior in [`esp32_server/src/main.cpp`](esp32_server/src/main.cpp) and [`esp32_client/src/main.cpp`](esp32_client/src/main.cpp).
- Ingest encryption path for:
  - mic trigger recording,
  - HTTP multipart upload,
  - raw TCP upload (8081 and legacy 8080 POST).
- Sync verify correctness for “latest changed file”, not only “last mic file”.
- API/endpoint contract updates for encrypted file metadata.

### Out of scope
- New cloud KMS service.
- Desktop UI redesign.
- Full legacy plaintext migration of all old files in one release.

---

## 3) Crypto Contract (Goal / Constraints / Output / Failure)

## Contract C-01 — AES-GCM Encryption-at-Ingest
**Goal**
- Any file created/ingested after this release MUST be persisted to SD as encrypted container bytes only.

**Constraints**
- Algorithm: AES-256-GCM (key 32 bytes, nonce 12 bytes, tag 16 bytes).
- Implementation MUST use incremental/streaming GCM API (Arduino-ESP32 mbedTLS available by default) to avoid full-file RAM buffering.
- Nonce uniqueness MUST be guaranteed per key.
- No plaintext temporary file on SD for new ingest paths.
- Existing `MAX_FILE_SIZE` checks remain enforced on plaintext input size.

**Output**
- New persisted artifacts are encrypted container files (recommended extension: `.penc`; if preserving original extension, metadata field `encrypted=true` is mandatory).
- Sync between Phantom-1/Phantom-2 transfers these encrypted artifacts unchanged.

**Failure**
- If crypto init/key invalid/random failure/write mismatch/tag finalize failure: operation FAIL-CLOSED.
- Partial output file must be deleted.
- No `markLocalFileChanged(...)` on failed ingest.

## Contract C-02 — Nonce / IV Rules
**Goal**
- Never reuse nonce with same key.

**Constraints**
- Nonce size fixed 96-bit.
- Nonce generation format (device-local uniqueness):
  - 4 bytes `boot_nonce_prefix` from TRNG at boot,
  - 8 bytes monotonic `gcm_counter` persisted in NVS (`crypto.nonce_ctr`) and incremented per file.
- Counter increment must be atomic before file finalize.

**Output**
- Every encrypted file has unique nonce under same key.

**Failure**
- NVS counter read/write failure -> reject new ingest (`503` / `500`) and report `crypto_nonce_counter_error`.

## Contract C-03 — Key Management / Provisioning (ESP32 Practical Model)
**Goal**
- Deterministic provisioning without extra infra; safe enough for current AP-local deployment.

**Constraints**
- Active key source priority:
  1. NVS key blob (`crypto:key32`, 32 bytes) + `crypto:key_id`.
  2. Build flag fallback in [`esp32_server/platformio.ini`](esp32_server/platformio.ini) and [`esp32_client/platformio.ini`](esp32_client/platformio.ini) using `-DPHANTOM_KEY_HEX=...`, `-DPHANTOM_KEY_ID=...`.
- Both nodes MUST run same key bytes + key_id for operational compatibility.
- Key bytes NEVER exposed by endpoint/log.

**Output**
- `/status` exposes only safe metadata: `crypto_enabled`, `crypto_key_id`, `crypto_container_ver`.

**Failure**
- Missing/invalid key -> node starts with `crypto_enabled=false`; all ingest endpoints return fail-closed until provisioned.

## Contract C-04 — File Container / Header Format
**Goal**
- Self-describing encrypted artifact with versioning and authenticated metadata.

**Constraints**
- Header v1 (fixed 64 bytes):
  - `magic[8]` = `PHGCM1\0\0` (aligned with existing decrypt tooling prefix from [`decode.py`](decode.py:6)).
  - `ver[1]` = `0x01`.
  - `flags[1]` (bit0=mic_source, bit1=pc_upload, bit2=sync_downloaded, bit7=legacy_plain_migrated).
  - `key_id[1]`.
  - `alg_id[1]` = `0x01` (AES-256-GCM).
  - `nonce[12]`.
  - `plain_size_u64[8]`.
  - `reserved[16]` (future metadata; zero now).
  - `tag[16]` (written at finalize).
- Ciphertext bytes follow header.
- AAD = header bytes `[0..47]` (excluding tag), so metadata tampering is detected.

**Output**
- Container validates via AES-GCM tag and authenticated header.

**Failure**
- Header parse invalid/magic mismatch/tag fail => report encrypted file invalid; do not treat as decryptable plaintext.

## Contract C-05 — Backward Compatibility
**Goal**
- Keep existing plaintext artifacts usable while enforcing encrypted writes for new data.

**Constraints**
- Existing plaintext files remain listable/downloadable/syncable unchanged.
- New writes are encrypted only.
- File list APIs must indicate per-file encryption state.
- Duration parsing (`.wav`) is best-effort only when plaintext WAV; encrypted files return `duration_sec` absent/null.

**Output**
- Mixed fleet of old plaintext files + new encrypted files works at API level.

**Failure**
- If peer runs old firmware and cannot handle metadata fields, default behavior remains transferable by raw bytes; operational policy requires both nodes upgraded for guaranteed encrypted-sync invariant.

## Contract C-06 — RAM/CPU Streaming Design
**Goal**
- Keep memory bounded and avoid large buffers.

**Constraints**
- Chunk encryption/decryption pipeline only (recommended 512–4096 bytes chunk size).
- No full-file `malloc` for encryption path.
- CPU overhead accepted; functional priority over throughput.

**Output**
- Upload/record/sync continue without OOM on typical ESP32 heap.

**Failure**
- Low heap during crypto context init/chunk process -> abort operation and delete partial file.

---

## 4) Endpoint / API Contract Impact

## Existing endpoints with behavior changes
1. `POST /file/upload` (multipart)
   - Input: unchanged (plaintext upload stream).
   - Persisted output: encrypted container file.
   - Response JSON adds:
     - `encrypted: true`
     - `container: "PHGCM1"`
     - `key_id: <int>`
     - `plain_size: <int>`

2. Raw upload on 8081 (`handleRawUpload`) and legacy 8080 POST (`handleRawTCP`)
   - Same request shape.
   - Must persist encrypted container.
   - Response adds same encryption metadata fields.

3. `GET /file/list`
   - Per file item adds:
     - `encrypted` (bool)
     - `container_ver` (int|null)
     - `plain_size` (int|null)
     - `key_id` (int|null)

4. `GET /status`
   - Adds node crypto state:
     - `crypto_enabled` (bool)
     - `crypto_key_id` (int)
     - `crypto_container_ver` (int)

## Compatibility expectations
- Request formats remain backward-compatible.
- Response JSON is additive (new fields only).
- Clients assuming downloaded bytes are plaintext WAV must adapt/decrypt when `encrypted=true`.

---

## 5) Exact Firmware Touchpoints

## Phantom-1 (server firmware)
- Mic ingest path: [`recordTriggeredWavToSD()`](esp32_server/src/main.cpp:530)
- HTTP multipart ingest: [`handleFileUploadStream()`](esp32_server/src/main.cpp:1822)
- Raw TCP upload ingest: [`handleRawUpload()`](esp32_server/src/main.cpp:2386)
- Legacy 8080 POST ingest path: [`handleRawTCP()`](esp32_server/src/main.cpp:2543)
- Priority sync marker: [`markLocalFileChanged()`](esp32_server/src/main.cpp:2089)
- Priority pull-sync request: [`requestPeerPullSync()`](esp32_server/src/main.cpp:2105)
- Verify bug site (must fix): [`verifyPeerHasLocalFile(lastMicWavFile)`](esp32_server/src/main.cpp:2215)
- File listing metadata: [`handleFileList()`](esp32_server/src/main.cpp:1531)
- Status metadata: [`handleStatus()`](esp32_server/src/main.cpp:1462)

## Phantom-2 (client firmware)
- Mic ingest path: [`recordTriggeredWavToSD()`](esp32_client/src/main.cpp:540)
- HTTP multipart ingest: [`handleFileUploadStream()`](esp32_client/src/main.cpp:2149)
- Raw TCP upload ingest: [`handleRawUpload()`](esp32_client/src/main.cpp:2349)
- Legacy 8080 POST ingest path: [`handleRawTCP()`](esp32_client/src/main.cpp:2496)
- Priority sync marker: [`markLocalFileChanged()`](esp32_client/src/main.cpp:762)
- Priority pull-sync request: [`requestPeerPullSync()`](esp32_client/src/main.cpp:778)
- Symmetric verify bug risk: [`verifyNode1HasLocalFile(lastMicWavFile)`](esp32_client/src/main.cpp:888)
- File list metadata: [`handleFileList()`](esp32_client/src/main.cpp:1807)
- Status metadata: [`handleStatus()`](esp32_client/src/main.cpp:1741)

## Required verify logic fix
- Add `lastLocalChangedPath` state (or equivalent) updated in every successful local write path (mic/upload/raw).
- Replace mic-only verify argument in both directions:
  - Phantom-1: replace `lastMicWavFile` usage in [`requestPeerPullSync()`](esp32_server/src/main.cpp:2105).
  - Phantom-2: replace `lastMicWavFile` usage in [`requestPeerPullSync()`](esp32_client/src/main.cpp:778).
- Verify must target “latest changed local artifact path + expected encrypted size”.

---

## 6) BE Dev Task Breakdown
1. Introduce crypto module layer (init key, nonce counter, stream encrypt writer, container parser helpers) in both firmware files.
2. Wire encrypt-at-ingest into all write paths listed above.
3. Add encrypted metadata to list/status/upload responses.
4. Implement verify-path bug fix with generic `lastLocalChangedPath` state.
5. Keep sync transfer raw-byte transparent; ensure encrypted files are copied as-is.
6. Add fail-closed handling and cleanup for partial writes.
7. Add build flag + NVS key loading contract support.

---

## 7) Acceptance Criteria (AC)
- AC-01: New mic recording is persisted encrypted (`magic=PHGCM1`) and not plaintext WAV on SD.
- AC-02: New HTTP multipart upload is persisted encrypted.
- AC-03: New raw TCP upload (8081 and 8080 POST) is persisted encrypted.
- AC-04: Sync from Phantom-1↔Phantom-2 results in encrypted artifact on receiver.
- AC-05: Priority verify after non-mic upload validates the correct latest changed file (no mic-only false verify).
- AC-06: Tampering any byte in encrypted file causes decrypt/auth failure.
- AC-07: Nonce uniqueness test across N>=10,000 new files shows zero nonce reuse per key.
- AC-08: Old plaintext files remain listable/downloadable after firmware update.
- AC-09: If key invalid/missing, ingest endpoints fail-closed and do not write plaintext fallback.

---

## 8) Functional + Security Test Matrix

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| F-01 | Mic encrypt | Trigger voice record once | New file is encrypted container, `encrypted=true` in list |
| F-02 | Multipart encrypt | Upload `sample.wav` via `/file/upload` | SD file is container; response includes `encrypted=true` |
| F-03 | Raw 8081 encrypt | Send HTTP-like upload to 8081 | Saved file encrypted; size/check passes |
| F-04 | Raw 8080 POST encrypt | Upload via legacy port 8080 | Saved file encrypted |
| F-05 | Sync encrypted artifact | Create encrypted file on Node A, run priority sync | Node B receives same encrypted artifact |
| F-06 | Verify non-mic fix | Upload non-mic file, trigger priority sync verify | Verify targets uploaded file, not `lastMicWavFile` |
| F-07 | Mixed legacy compatibility | Keep old plaintext + add new encrypted | Both appear in list; metadata differentiates |
| S-01 | Tag tamper | Modify 1 byte in ciphertext/tag | Decrypt/auth fails deterministically |
| S-02 | Header tamper | Change `plain_size` or `key_id` bytes | Auth fails (AAD protected) |
| S-03 | Wrong key | Decrypt with wrong key or key_id mismatch | Auth fails; no plaintext output |
| S-04 | Nonce reuse guard | Generate many files reboot/no-reboot | No nonce collision per key |
| R-01 | Low memory handling | Force low heap during encrypt | Operation aborts + partial file deleted |
| R-02 | Power-loss mid-write | Reset during ingest | Incomplete temp/partial file not reported as valid |

---

## 9) Practical Library/Platform Notes
- Current [`platformio.ini`](esp32_server/platformio.ini) and [`platformio.ini`](esp32_client/platformio.ini) use Arduino ESP32; mbedTLS is bundled, so AES-GCM can be implemented without adding heavyweight dependencies.
- Existing desktop decrypt helper in [`decode.py`](decode.py:6) already expects `PHGCM1` style magic; align firmware container prefix to reduce tooling mismatch risk.
