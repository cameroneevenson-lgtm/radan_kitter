# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this app does

RADAN Kitter is a PySide6 Windows desktop utility for assigning RADAN `.rpd` parts into production kits (Bottoms, Sides, Tops, Backs, Tall Sides, Brackets, Wheel Wells, Walls, Flat Parts), writing kit metadata back to the RADAN project, generating kit `.sym` files from a donor template (`KitDonor-100Instances.sym`), and building quantity print packets from matching PDF assets. It's part of the Battleshield fabrication toolchain — see `README.md` for the full operator workflow, numpad keyboard flow, and asset-lookup path conventions, which are shop conventions not derivable from the code.

## Commands

Launch:

```powershell
python main.py
# or
.\radan_kitter.bat
```

`radan_kitter.bat` resolves Python in this order: `C:\Tools\.venv` → `.\.venv` → `C:\Tools\radan_venv` → `python`/`py` on PATH. It hot-reloads by default; set `$env:RADAN_KITTER_HOT_RELOAD = "0"` for a stable one-shot run. An `.rpd` path can be passed as the first argument.

Tests:

```powershell
python -m pytest
python -m pytest tests/test_rpd_io.py -q
python -m pytest tests/test_name.py -k test_name
```

Headless smoke test (exercises the app without a display):

```powershell
python smoke_headless.py
```

See `DEPENDENCIES.md` for dependency-update policy and hot-reload internals.

## Architecture

**Read/write of the actual RADAN project is split by file type and is where correctness matters most** — this tool's entire job is safely mutating shop-floor `.rpd`/`.sym` files in place:
- `rpd_io.py` — loads/writes the `.rpd` XML (parses with a `TreeBuilder` configured to preserve comments/PIs — do not swap back to the default parser, it silently drops them on write)
- `sym_io.py` — part `.sym` text read/write; `read_text_fallback` does BOM-aware UTF-16 detection → UTF-8 → cp1252, in that order deliberately (encoding misdetection here silently corrupts the whole file on the next save)
- `kit_service.py` — generates kit `.sym` files from the donor template, and orchestrates backup-before-write
- `file_utils.py` — `atomic_write_bytes` (temp file + `os.replace`); all writes to real project files should go through this, not raw `open(...).write()`
- Every destructive write (`Write RPD`, `Prepare Kits`) creates a backup under the job's `_bak` folder first — preserve that ordering in any change to these paths.

**`radan_kitter.py` is the main window orchestrator**; UI is split into focused `ui_*.py` modules (table, layout, preview pane, numpad controller/legend, main events) rather than living in one file. `ui_actions.py` is the largest UI module and holds most button/menu action handlers.

**PDF/DXF layer (OCG) visibility logic is centralized, not duplicated** — `packet_layers.py` provides shared helpers (`iter_ui_layer_entries`, `iter_ocg_entries`, `set_ui_config_safe`, `set_ocg_visibility_safe`, `apply_packet_layer_policy`) used by both packet generation and `pdf_preview.py`. If you need layer/OCG visibility behavior anywhere else, extend these helpers rather than reimplementing PyMuPDF's `layer_ui_configs()`/`get_ocgs()`/`set_layer_ui_config()` calls again.

**ML suggestion workflow (RF Suggest)** is a distinct subsystem: `ml_dxf_features.py`/`ml_pdf_features.py` compute the locked feature schema (`ML_SIGNAL_LOCK.md` documents the locked column order — the true source of truth is `config.py`'s `ML_SIGNAL_COLS`, don't reorder it without a version bump), `ml_dataset_store.py` upserts labeled rows into `ml_dataset.csv` **keyed by part name, not job/PDF/DXF path** — re-scanning a part intentionally supersedes its prior training row across jobs (deliberate "supersede-on-rescan" behavior, not a bug), `ml_pipeline.py` trains, and `rf_model.py`/`rf_service.py` serve predictions back into the UI.

**`automation_bridge.py`** is a not-yet-wired-in foundation for headless RADAN refresh (calling the sibling `radan_automation/refresh_document_headless.py`) — kit `.sym` files currently still need a manual open+save in RADAN to refresh displayed geometry/thumbnails.

**Generated/runtime paths to treat as disposable** (already gitignored): `_bak`, `_out`, `_kits` (per job folder), `_runtime`, `_ml_runs`, `_ml_models`, `_packet_debug_out`, `radan_kitter_launch.log`. `ml_dataset.csv` and `_runtime/asset_lookup_settings.json` are tracked, plausibly-intentional state — don't casually gitignore or delete them.
