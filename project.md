# Project Overview

## Active Objective
Implement end-to-end AES-GCM encryption for Phantom firmware so that:
- microphone recordings are encrypted before being written to SD,
- files uploaded from PC are encrypted before being written to SD,
- synchronized files between Phantom-1 and Phantom-2 are encrypted artifacts.

## Scope
- In scope: firmware changes in ESP32 projects, sync verification alignment, build verification.
- Out of scope: desktop UI redesign, cloud services.

## Notes
- Requested onboarding references were not found in workspace:
  - `/skills/pm.yaml`
  - `/docs/codemap.md`
  - `/docs/project.md`
  - `/docs/audit.md`
- PM control file is initialized at root: `project.md`.

---

# Versions

## Baseline
- Firmware projects:
  - `esp32_server/src/main.cpp`
  - `esp32_client/src/main.cpp`
- Existing sync lock/retry logic already implemented in prior iteration.

## Change Target
- Add AES-GCM encryption-at-rest pipeline for both recording and upload ingestion.
- Ensure sync verify checks the latest changed encrypted file (not mic-only tracker).

---

# Task Board

## Complexity Classification
- Level: High (crypto + streaming I/O + cross-node sync correctness)
- Risks:
  - nonce/key misuse,
  - memory pressure on ESP32,
  - sync verify false-negative for non-mic files,
  - backward compatibility of file APIs.

## Pipeline
1. **BA - Requirements & Crypto Contract** — Completed
2. **BE Dev - Firmware Implementation (Phantom-1/2)** — In Progress
3. **FE Dev - Impact Assessment (if any UI/API contract change)** — Pending
4. **Integration - Verifier** — Pending
5. **Vision Parser - Visual Verifier** — Pending (run only if FE artifact changes)
6. **QC - Quality Control** — Pending
7. **Reviewer - Final Authority** — Pending
8. **Deliver - Release Manager** — Pending

## Checkpoint
- DOING: BE implementation delegation and execution for AES-GCM ingest + sync verify fix.
- DONE: Project board initialized; BA contract/spec completed in `research.md`.
- LEFT: Implement, verify, audit, and final sign-off pipeline.
- NEXT: Receive BE code changes and build results; then route to FE impact check.
- BLOCKERS: Missing onboarding docs (`/docs/*`, `/skills/pm.yaml`) in current workspace.

---

## Pipeline BA Specs

### Classification
- **Type:** FEATURE + BUG-FIX
- **Feature:** Encrypt all newly ingested artifacts before SD persistence.
- **Bug-fix:** Replace mic-only sync verify target with latest changed local artifact.

### Contract Summary (Goal / Constraints / Output / Failure)
1. **C-01 Encrypt-at-Ingest**
   - Goal: New mic/upload artifacts are encrypted before SD persistence.
   - Constraints: AES-256-GCM, streaming chunk processing, fail-closed.
   - Output: Encrypted container artifacts synced across both nodes.
   - Failure: Delete partial file; do not mark sync-pending.

2. **C-02 Nonce Rules**
   - Goal: Nonce uniqueness per key.
   - Constraints: 12-byte nonce = boot random prefix + persistent NVS monotonic counter.
   - Output: No nonce reuse for same key.
   - Failure: Counter/NVS errors block ingest.

3. **C-03 Key Provisioning**
   - Goal: Practical ESP32 provisioning with deterministic deployment.
   - Constraints: NVS key (preferred) + build-flag fallback in PlatformIO; no key leak via logs/API.
   - Output: `/status` reports safe crypto metadata only.
   - Failure: Missing/invalid key => ingest fail-closed.

4. **C-04 Container Format**
   - Goal: Versioned, authenticated encrypted artifact format.
   - Constraints: `PHGCM1\0\0` magic, fixed header, AES-GCM tag, AAD over header fields.
   - Output: Tamper-detectable encrypted files.
   - Failure: Parse/auth mismatch => invalid encrypted artifact.

5. **C-05 Backward Compatibility**
   - Goal: Preserve access to legacy plaintext files.
   - Constraints: New writes encrypted; old plaintext remains list/download capable.
   - Output: Mixed storage compatibility with explicit per-file encryption metadata.
   - Failure: Legacy clients may need decrypt adaptation for encrypted downloads.

6. **C-06 RAM/CPU Constraints**
   - Goal: Avoid full-file RAM buffering.
   - Constraints: 512–4096B chunk processing; no full-file `malloc` for ingest encryption.
   - Output: Stable operation on ESP32 heap limits.
   - Failure: Low-memory abort + cleanup partial artifacts.

### API Contract Impact
- `POST /file/upload`, raw 8081 upload, and legacy 8080 POST keep request shape, but persist encrypted artifacts and return additive metadata:
  - `encrypted`, `container`, `key_id`, `plain_size`.
- `GET /file/list` adds per-file metadata:
  - `encrypted`, `container_ver`, `plain_size`, `key_id`.
- `GET /status` adds node crypto metadata:
  - `crypto_enabled`, `crypto_key_id`, `crypto_container_ver`.

### Exact Firmware Touchpoints
- Phantom-1:
  - mic ingest: `recordTriggeredWavToSD()`
  - multipart ingest: `handleFileUploadStream()`
  - raw ingest: `handleRawUpload()` + `handleRawTCP()` POST
  - sync verify bug site: `verifyPeerHasLocalFile(lastMicWavFile)` in `requestPeerPullSync()`
- Phantom-2:
  - mic ingest: `recordTriggeredWavToSD()`
  - multipart ingest: `handleFileUploadStream()`
  - raw ingest: `handleRawUpload()` + `handleRawTCP()` POST
  - symmetric verify risk: `verifyNode1HasLocalFile(lastMicWavFile)` in `requestPeerPullSync()`

### Required Verify Fix
- Introduce generic tracker (e.g., `lastLocalChangedPath`) updated on every successful local write path.
- Use this tracker for priority-sync verification instead of `lastMicWavFile`.

### Acceptance Criteria Snapshot
- New mic and upload writes are encrypted-only at rest.
- Synced artifacts are encrypted artifacts.
- Non-mic verify bug is fixed.
- Legacy plaintext files remain accessible.
- Tamper/wrong-key/nonce misuse paths fail safely.

### Detailed Spec Location
- Full contract + test matrix lives in `research.md`.
