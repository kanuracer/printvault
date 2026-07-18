# PrintVault Slicer Helper Protocol

## Purpose

A browser and Docker container cannot directly launch a native slicer on a user's computer. PrintVault therefore always offers a file download and optionally invokes a locally installed helper through a custom protocol.

## Flow

```text
Browser → PrintVault authenticated API → short-lived one-time launch request
        → printvault://open?request=<opaque-id>
        → local PrintVault Helper
        → authenticated download
        → configured native slicer process
```

The opaque request is not a filesystem path and contains no shell command. It expires quickly and is single-use.

## Security rules

- Helper opens only the configured PrintVault origin.
- Helper redeems request IDs over HTTPS and checks origin/certificate.
- API returns a file URL plus validated filename/content type; it never returns a host path.
- Helper downloads into an application-controlled temporary directory.
- Slicer definitions use executable path plus argument array, never `shell=True`, `cmd.exe /c`, `sh -c`, or interpolated command strings.
- Helper rejects executables outside explicitly configured paths unless user confirms them in its own settings UI.
- Helper deletes temporary copies on exit when the slicer no longer needs them; original server library remains unchanged.

## Slicer profiles

Each profile has a display label, executable and argument array. `{file}` is substituted as one complete argument only.

```json
{
  "profiles": [
    {
      "id": "orca",
      "label": "OrcaSlicer",
      "executable": "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
      "args": ["{file}"]
    },
    {
      "id": "custom",
      "label": "Custom slicer",
      "executable": "/absolute/path/to/slicer",
      "args": ["{file}"]
    }
  ]
}
```

The same model supports PrusaSlicer, Cura, Bambu Studio, SuperSlicer and any compatible slicer. Platform installers provide common defaults but users can modify profiles.

## Fallback

If no helper is installed, disabled, unavailable, or rejects a request, PrintVault uses an ordinary authenticated download. Browser file association then lets the user select their slicer.

## Platform packaging target

- Windows: signed helper installer and registered `printvault` scheme.
- Linux: package/desktop entry and `xdg-mime`/scheme registration.
- macOS: notarized app/helper bundle and URL scheme registration.

No server-side slicer, GUI forwarding or remote execution is part of PrintVault.
