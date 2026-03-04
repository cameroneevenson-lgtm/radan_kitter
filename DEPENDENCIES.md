# Dependency Versioning

This repo ignores virtual environments (`.venv/`) and tracks dependencies in `requirements.txt`.

## Install

```powershell
python -m pip install -r requirements.txt
```

## When adding/updating a package

1. Install/update in your active venv.
2. Update `requirements.txt` with an appropriate version constraint.
3. Commit code + `requirements.txt` together.

## Notes

- Keep `.venv/` out of git.
- Use bounded ranges (`>=x,<y`) to reduce accidental major-version breakage.

## Dev Hot Reload (Safe Restart)

Hot reload is handled by `RADAN Kitter.bat` via `dev_hot_restart.py`.

Enable or disable:

```powershell
# Enable
$env:RADAN_KITTER_HOT_RELOAD = "1"

# Disable (stable one-shot launch)
$env:RADAN_KITTER_HOT_RELOAD = "0"
```

Behavior:
- Watches `.py` files for changes.
- Restarts the app process after a debounce window.
- Does not do in-process module reloading (safer for PySide6).

## Known Behavior

- Generated kit `.sym` files can require opening and saving once in RADAN to finalize displayed geometry and thumbnail preview.
  - Kit membership and references are written correctly by the generator.
  - This is treated as an expected post-generation RADAN refresh step.
