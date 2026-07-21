from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from .config import ConfigError, load_config
from .http_client import HttpError, UrllibHttpClient
from .launcher import ConfirmDeclined, LaunchError, launch_from_target, redact_secret_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Redeem and launch a PrintVault helper job.")
    parser.add_argument("target", nargs="?", help="printvault://open?... URI or request_id:profile_id")
    parser.add_argument("--register", action="store_true", help="Register this device using a pairing code")
    parser.add_argument("--origin", help="PrintVault HTTPS origin for device registration")
    parser.add_argument("--pairing-code", help="One-time pairing code issued in PrintVault settings")
    parser.add_argument("--device-name", help="Visible local device name for registration")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "config.json"),
        help="Path to helper config JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.register:
        if args.target or not args.origin or not args.pairing_code or not args.device_name:
            parser.error("--register requires --origin, --pairing-code, and --device-name")
        parsed = urlparse(args.origin)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
            parser.error("--origin must be an HTTPS origin without path, query, or fragment")
        try:
            registration = UrllibHttpClient().register_device(
                origin=f"https://{parsed.netloc}", pairing_code=args.pairing_code, device_name=args.device_name
            )
            required = ("user_id", "device_id", "device_credential")
            if not all(isinstance(registration.get(key), str) and registration[key] for key in required):
                raise HttpError("Registration response was invalid")
        except HttpError as exc:
            print(redact_secret_text(f"Error: {exc}"), file=sys.stderr)
            return 1
        print(json.dumps({key: registration[key] for key in required}))
        return 0
    if not args.target:
        parser.error("target is required unless --register is used")
    try:
        config = load_config(Path(args.config))
        launch_from_target(config=config, target=args.target, http_client=UrllibHttpClient())
    except (ConfigError, LaunchError, ConfirmDeclined, HttpError) as exc:
        print(redact_secret_text(f"Error: {exc}"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
