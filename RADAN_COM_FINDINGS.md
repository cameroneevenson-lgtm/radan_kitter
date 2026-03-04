# RADAN COM Interface Findings (Local Machine)

Date: 2026-03-04  
Repo: `c:\Tools\radan_kitter_controlled_migration`

## Scope

Inspected local COM/registry registration to identify what RADAN automation interfaces are available.

## Method

1. Enumerated `HKCR` keys matching `Radan.*`.
2. Resolved `ProgID -> CLSID -> LocalServer32/InprocServer32/TypeLib`.
3. Queried `HKCR\WOW6432Node\CLSID` for `Radan.*`.
4. Queried `HKCR\TypeLib` for discovered TypeLib GUIDs.

Note: WMI COM class inspection (`Win32_ClassicCOMClassSetting`) returned `Access denied` in this shell.

## Registered `Radan.*` Keys Found

- `Radan.Assembly`
- `Radan.Blocks`
- `Radan.Drawing`
- `Radan.DTM`
- `Radan.Project`
- `Radan.RadviewDNC`
- `Radan.RasterToVector`
- `Radan.RasterToVector.1`
- `Radan.Schedule`
- `Radan.Setup`
- `Radan.Symbol`

## COM-Activatable Class Found

Only `Radan.RasterToVector` appears to be a usable COM automation class on this machine.

- Version-independent ProgID: `Radan.RasterToVector`
- Versioned ProgID: `Radan.RasterToVector.1`
- CLSID: `{47B6A894-68EA-4005-ADC3-1EA9672D888C}`
- Registration location: `HKCR\WOW6432Node\CLSID\{47B6A894-68EA-4005-ADC3-1EA9672D888C}`
- COM server (`LocalServer32`):  
  `C:\PROGRA~1\Mazak\Mazak\nt\i386\bin\radrvc.exe`
- TypeLib GUID: `{4A14B157-FC74-4D89-9C68-E4B409057087}`
- TypeLib name/version: `radrvcLib` / `1.0`
- TypeLib win32 path:  
  `C:\PROGRA~1\Mazak\Mazak\nt\i386\bin\radrvc.exe`

## Non-COM `Radan.*` Keys

Most other `Radan.*` keys appear to be file-association entries (icons + shell open commands), not COM classes.

Examples:

- `Radan.Drawing` -> `"C:\Program Files\Mazak\Mazak\bin\RADRAFT.exe" "%1"`
- `Radan.Symbol` -> `"C:\Program Files\Mazak\Mazak\bin\RADRAFT.exe" "%1"`
- `Radan.Blocks` -> `Notepad.exe "%1"`

These keys did not expose `CLSID` mappings in the queried locations.

## Practical Conclusion

On this machine, the RADAN COM surface currently visible via registry is centered on `RasterToVector` (`radrvc.exe`, 32-bit registration path).

If deeper COM inspection is needed (methods/properties/events), use TypeLib browser tooling against `{4A14B157-FC74-4D89-9C68-E4B409057087}` with a 32-bit COM-capable client/runtime.
