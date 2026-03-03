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

Use the dev launcher:

```powershell
RADAN Kitter (Dev Hot Reload).bat
```

Behavior:
- Watches `.py` files for changes.
- Restarts the app process automatically after save.
- Does not do in-process module reloading (safer for PySide6).
