# RADAN Kitter

RADAN Kitter is a Windows desktop utility for assigning RADAN `.rpd` parts into production kits, writing kit metadata back to the RADAN project, generating kit `.sym` files from a donor template, and building quantity print packets from the matching PDF assets.

The app is built with PySide6 and is intended for the Battleshield fabrication workflow where `.rpd`, `.sym`, `.pdf`, and `.dxf` files live on shared RADAN/release drives.

## What It Does

- Opens a RADAN `.rpd` file and lists the project parts in a sortable table.
- Assigns parts to the standard kit set:
  - Bottoms
  - Sides
  - Tops
  - Backs
  - Tall Sides
  - Brackets
  - Wheel Wells
  - Walls
  - Flat Parts
- Supports fast numpad-driven kit assignment and navigation.
- Previews the matching part PDF while reviewing rows.
- Writes kit and priority values back to the original `.rpd`, with backups.
- Generates kit `.sym` files from `KitDonor-100Instances.sym`.
- Builds a combined print packet PDF in natural Windows part order.
- Logs labeled training data and computes PDF/DXF signals for the RF suggestion workflow.

## Requirements

- Windows
- Python 3.10+ recommended
- Access to the expected RADAN and release-drive paths
- Python packages from `requirements.txt`

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

For dependency policy and hot-reload notes, see `DEPENDENCIES.md`.

## Launching

From the repo:

```powershell
python main.py
```

Or use the launcher:

```powershell
.\radan_kitter.bat
```

The launcher looks for Python in this order:

- `C:\Tools\.venv`
- `.\.venv`
- `C:\Tools\radan_venv`
- `python.exe` or `py.exe` on `PATH`

The batch file starts in hot-reload mode by default. To run a stable one-shot launch:

```powershell
$env:RADAN_KITTER_HOT_RELOAD = "0"
.\radan_kitter.bat
```

An `.rpd` path can also be passed as the first argument:

```powershell
.\radan_kitter.bat "L:\BATTLESHIELD\F-LARGE FLEET\Example\job.rpd"
```

## Operator Workflow

1. Click **Open RPD** and choose the RADAN project file.
2. Review the part list and PDF preview.
3. Assign kits manually in the table, with the numpad, or with RF suggestions.
4. Use **Prepare Kits** to update part `.sym` comments and generate kit `.sym` files.
5. Use **Write RPD** to write kit and priority values to the original `.rpd`.
6. Use **Print Packet** to build the quantity packet PDF under the job `_out` folder.

Backups are written under the job `_bak` folder before destructive updates.

## Keyboard Flow

The numpad maps to the nine canonical kits in a 3x3 grid:

```text
7 Wheel Wells   8 Walls       9 Flat Parts
4 Backs         5 Tall Sides  6 Brackets
1 Bottoms       2 Sides       3 Tops
```

Additional controls:

- `0`: clear the current row kit and set priority to `5`
- `Enter`: accept the current RF suggestion
- second `Enter`: advance one row
- `+` or `Down`: move down
- `-` or `Up`: move up

## Asset Lookup

PDF and DXF lookup is handled by `assets.py`.

Defaults are configured in `config.py`:

- Default RPD open root: `L:\BATTLESHIELD\F-LARGE FLEET`
- Release asset root: `W:\LASER\For Battleshield Fabrication`
- Engineering-to-release mapping from `L:\BATTLESHIELD...` to the release root

The UI also has controls for setting or resetting the PDF/DXF root. Saved overrides are stored in `_runtime\asset_lookup_settings.json`.

## Machine Learning Helpers

RADAN Kitter includes an RF suggestion workflow:

- `ML Log` scans labeled rows and appends/upserts training rows in `ml_dataset.csv`.
- `_ml_runs` stores per-run scan summaries.
- `_ml_models` stores the trained random forest predictor artifacts.
- `RF Suggest` computes features for the loaded project and fills suggested kit labels.

The current feature schema is defined in `config.py` and implemented by `ml_pipeline.py`, `ml_pdf_features.py`, and `ml_dxf_features.py`.

## Project Layout

- `main.py` - Qt application launcher and window placement.
- `radan_kitter.py` - main window orchestration.
- `ui_*.py` - table, layout, preview, numpad, and event modules.
- `rpd_io.py` - RADAN project read/write helpers.
- `sym_io.py` and `kit_service.py` - part `.sym` updates and kit `.sym` generation.
- `packet_service.py`, `packet_worker.py`, `pdf_packet.py` - print packet generation.
- `assets.py` - PDF/DXF path resolution and root overrides.
- `ml_*.py`, `rf_*.py` - ML feature extraction, dataset handling, and kit prediction.
- `tests/` - regression and smoke coverage.

## Development

Create and activate a virtual environment if needed:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run tests:

```powershell
python -m pytest
```

Run the headless smoke test:

```powershell
python smoke_headless.py
```

## Runtime Outputs

Common generated output locations:

- Job folder `_bak` - backups before RPD and SYM writes.
- Job folder `_out` - generated print packets.
- Job folder `_kits` - generated kit `.sym` files.
- Repo `_runtime` - local runtime state, traces, and settings.
- Repo `_ml_runs` - ML scan summaries.
- Repo `radan_kitter_launch.log` - launcher log.

Treat runtime traces, one-off ML runs, packets, and local launch logs as generated data unless you are intentionally updating them.

## Operational Notes

- `Write RPD` writes kit and priority metadata to the original `.rpd` after creating a backup.
- `Prepare Kits` writes kit labels into part `.sym` Attr 109 and generates donor-based kit `.sym` files.
- Generated kit `.sym` files may need to be opened and saved once in RADAN so RADAN refreshes displayed geometry and thumbnails.
- Headless RADAN refresh plumbing exists in `automation_bridge.py`, but it is not wired into the main UI flow yet.
