"""Portable plugin export and credential-free connection doctor. Never installs globally."""

import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/mcp/"
        or parsed.hostname.endswith(".invalid")
    ):
        raise ValueError("Expected an issued HTTPS /mcp/ endpoint without credentials")
    return value


def package(destination: Path, url: str, client_id: str, *, apply: bool = False) -> None:
    endpoint(url)
    if not client_id or len(client_id) > 256 or any(ord(c) < 32 for c in client_id):
        raise ValueError("Invalid public OAuth client ID")
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Destination must be new; existing installations are never overwritten")
    if not apply:
        print("Plan: export plugin with public endpoint/client ID; no installation or login.")
        return
    shutil.copytree(ROOT / "plugins" / "greenaurum-smm", destination)
    config = {"mcpServers": {"smm": {"type": "http", "url": url, "oauth": {"clientId": client_id}}}}
    (destination / ".mcp.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(
        "Plugin exported. Register it through the personal marketplace, then sign in individually."
    )


def doctor(url: str, client: httpx.Client | None = None) -> bool:
    endpoint(url)
    origin = urlsplit(url)
    metadata_url = f"{origin.scheme}://{origin.netloc}/.well-known/oauth-protected-resource/mcp/"
    own_client = client is None
    connection = client or httpx.Client(timeout=5, follow_redirects=False, trust_env=False)
    try:
        response = connection.get(metadata_url)
        response.raise_for_status()
        if len(response.content) > 32_768:
            raise ValueError("Metadata too large")
        meta = response.json()
        if not isinstance(meta, dict):
            raise ValueError("Invalid metadata")
        issuers = meta.get("authorization_servers", [])
        metadata_ok = (
            meta.get("resource") == url
            and "smm:access" in meta.get("scopes_supported", [])
            and isinstance(issuers, list)
            and len(issuers) == 1
            and isinstance(issuers[0], str)
            and urlsplit(issuers[0]).scheme == "https"
        )
        challenge = connection.get(url)
        protected = challenge.status_code == 401 and "resource_metadata=" in challenge.headers.get(
            "www-authenticate", ""
        )
        installed = shutil.which("codex") is not None
        print(
            json.dumps(
                {
                    "https_metadata": metadata_ok,
                    "anonymous_denied": protected,
                    "codex_cli_found": installed,
                    "personal_oauth_verified": False,
                }
            )
        )
        return metadata_ok and protected and installed
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        print("Connection not ready. Check network, HTTPS, proxy and identity. Details withheld.")
        return False
    finally:
        if own_client:
            connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--endpoint", required=True)
    export.add_argument("--client-id", required=True)
    export.add_argument("--destination", type=Path, required=True)
    export.add_argument("--apply", action="store_true")
    check = commands.add_parser("doctor")
    check.add_argument("--endpoint", required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            package(args.destination, args.endpoint, args.client_id, apply=args.apply)
            return 0
        return 0 if doctor(args.endpoint) else 1
    except (ValueError, OSError):
        print("Refused: verify configuration and use a new destination. No credentials printed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
