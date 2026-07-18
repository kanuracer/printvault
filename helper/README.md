# PrintVault Helper

Planned cross-platform companion for native slicer launch.

The helper is intentionally separate from the Docker server. It registers the `printvault://` URL scheme on the local desktop, redeems one-time launch requests from the configured HTTPS PrintVault origin, downloads the selected file into a private temporary directory and executes a configured slicer with an explicit argument array.

See [`../docs/slicer-helper-protocol.md`](../docs/slicer-helper-protocol.md) and [`config.example.json`](config.example.json).

Current server UI must retain the ordinary download fallback until packaged helpers exist for Windows, Linux and macOS.
