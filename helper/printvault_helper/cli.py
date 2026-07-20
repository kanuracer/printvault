from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .http_client import HttpError, UrllibHttpClient
from .launcher import ConfirmDeclined, LaunchError, launch_from_target, redact_secret_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Redeem and launch a PrintVault helper job.")
    parser.add_argument("target", help="printvault://open?... URI or request_id:profile_id")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "config.json"),
        help="Path to helper config JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(Path(args.config))
        launch_from_target(config=config, target=args.target, http_client=UrllibHttpClient())
    except (ConfigError, LaunchError, ConfirmDeclined, HttpError) as exc:
        print(redact_secret_text(f"Error: {exc}"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
