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
1. **BA - Requirements & Crypto Contract** — In Progress
2. **BE Dev - Firmware Implementation (Phantom-1/2)** — Pending
3. **FE Dev - Impact Assessment (if any UI/API contract change)** — Pending
4. **Integration - Verifier** — Pending
5. **Vision Parser - Visual Verifier** — Pending (run only if FE artifact changes)
6. **QC - Quality Control** — Pending
7. **Reviewer - Final Authority** — Pending
8. **Deliver - Release Manager** — Pending

## Checkpoint
- DOING: Initialize orchestration and launch BA requirement contract.
- DONE: Project board initialized.
- LEFT: Full boomerang pipeline execution to release decision.
- NEXT: Delegate BA task with explicit AES-GCM constraints.
- BLOCKERS: Missing onboarding docs (`/docs/*`, `/skills/pm.yaml`) in current workspace.
