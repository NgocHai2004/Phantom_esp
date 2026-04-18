# Project Overview

## Current Request
- User reported decrypt runtime failure: [`_phtm_decrypt_3layer()`](en_de.py:50) not defined during decrypt flow in [`en_de.py`](en_de.py).
- User requested Open Output to always open fixed folder [`C:\Users\Ad\Documents\Phantom\output\return_user`](en_de.py:1873).

## Versions
- Affected app path: [`en_de.py`](en_de.py)
- Related reference implementation: [`_phtm_decrypt_3layer()`](decode.py:36)
- Open Output handler pinned to fixed target path at [`def _dec_open_output(self):`](en_de.py:1873)

## Task Board
- [x] Reproduce/locate NameError in decrypt flow at [`zfld_bytes = _phtm_decrypt_3layer(payload, master)`](en_de.py:1962)
- [x] Confirm missing symbol definition in [`en_de.py`](en_de.py)
- [x] Add missing helper [`def _phtm_decrypt_3layer(enc: bytes, master: bytes) -> bytes:`](en_de.py:50)
- [x] Validate callable state via debug log at [`self._dec_log_msg(...)`](en_de.py:1958)
- [x] Verify crypto roundtrip import test passed in debug subtask
- [x] Implement Open Output fixed folder behavior at [`def _dec_open_output(self):`](en_de.py:1873)
- [x] Ensure target folder auto-create before open at [`target.mkdir(parents=True, exist_ok=True)`](en_de.py:1878)

## Checkpoint
- DOING: none
- DONE: decrypt NameError fix + Open Output fixed-path behavior delivered in [`en_de.py`](en_de.py)
- LEFT: user runtime confirm in UI
- NEXT: if any new runtime error appears, run next debug cycle on the new traceback
- BLOCKERS: exact sample [`phantom_20260417_180958.bin`](phantom_20260417_180958.bin) not present in workspace for end-to-end replay
