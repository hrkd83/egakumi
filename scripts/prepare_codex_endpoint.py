#!/usr/bin/env python3
"""Render a protected Codex endpoint configuration bundle without installing it."""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
from uuid import UUID

from deployment.helpers.configuration import entra_tenant_id
from deployment.helpers.paths import REPOSITORY, LocalPaths, private_output, private_parent
from deployment.helpers.private_permissions import check_private_permissions, protect_new_path
from operations.common import OperationError, lock_environment


PROVIDER = "aigateway"
RESPONSES_API = REPOSITORY / "apps/api-gateway/apis/openai-inference-v1.json"
HOST_PATTERN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)


def read_protected_json(runtime: LocalPaths, path: Path) -> dict:
    path = runtime.checked(path)
    directory = path.parent
    while directory != runtime.repository:
        check_private_permissions(directory, directory=True)
        directory = directory.parent
    check_private_permissions(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def guid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected an application or tenant GUID")
    identifier = UUID(value)
    if identifier.int == 0 or value.lower() != str(identifier):
        raise ValueError("Expected a nonzero canonical GUID")
    return str(identifier)


def windows_catalog_path(value: str) -> str:
    path = PureWindowsPath(value)
    if (
        not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
        or ".." in path.parts or "%" in value or path.name.lower() != "models.json"
        or any(re.search(r'[<>:"|?*\x00-\x1f]', part) for part in path.parts[1:])
    ):
        raise ValueError("Expected an absolute local Windows path to models.json")
    return str(path)


def check_catalog_metadata(catalog: dict) -> None:
    """モデルの任意メタデータを保持し、認証情報を含む設定の混入を防ぐ。"""
    denied = {"apikey", "accesstoken", "refreshtoken", "bearertoken", "clientsecret",
              "password", "authorization", "authorizationheader", "cookie", "privatekey",
              "credential", "credentials", "connectionstring"}
    pending = [catalog]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if any(re.sub(r"[^a-z0-9]", "", key.lower()) in denied for key in value):
                raise ValueError("A model catalog must not contain credentials")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def render_files(
    params: dict,
    *,
    client_id: str,
    mode: str,
    model: str,
    redirect_port: int,
    enforce_provider: bool,
    catalog: dict | None,
    catalog_install_path: str | None,
) -> dict[str, str]:
    gateway = params["gateway"]
    host = gateway["publicHostName"].lower()
    if not HOST_PATTERN.fullmatch(host):
        raise ValueError("Expected a DNS name without scheme, port or path")
    tenant = guid(entra_tenant_id(params))
    api = guid(gateway["authentication"]["entra"]["audience"])
    client = guid(client_id)
    if len({tenant, api, client}) != 3:
        raise ValueError("Expected separate tenant, API and Codex client identifiers")
    if not 1 <= redirect_port <= 65535 or (enforce_provider and mode != "dedicated"):
        raise ValueError("Invalid redirect port or provider enforcement mode")

    definition = json.loads(RESPONSES_API.read_text(encoding="utf-8"))
    responses = definition["authorization"]["operationConstraints"]["responses-create"]
    allowed_models = set(responses["allowedModels"]) | set(responses.get("modelAliases", {}))
    if model not in allowed_models:
        raise ValueError("Model is outside the initial Responses API contract")
    base_url = f"https://{host}/{definition['path']}"
    identity = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    provider = (
        f"[model_providers.{PROVIDER}]\n"
        'name = "Egakumi Gateway"\n'
        f"base_url = {json.dumps(base_url)}\n"
        'wire_api = "responses"\n'
        "requires_openai_auth = false\n"
        "\n"
        f"[model_providers.{PROVIDER}.gateway_oauth]\n"
        f"authorization_url = {json.dumps(identity + '/authorize')}\n"
        f"token_url = {json.dumps(identity + '/token')}\n"
        f"client_id = {json.dumps(client)}\n"
        f"scopes = {json.dumps([f'api://{api}/Gateway.Invoke', 'offline_access'])}\n"
        f"redirect_port = {redirect_port}\n"
    )
    selection = f"model_provider = {json.dumps(PROVIDER)}\nmodel = {json.dumps(model)}\n"
    files = {}
    if catalog_install_path is not None:
        models = catalog.get("models") if catalog is not None else None
        if not isinstance(models, list) or not all(isinstance(entry, dict) for entry in models):
            raise ValueError("Expected a Codex model catalog")
        slugs = [entry.get("slug") for entry in models]
        if not all(isinstance(slug, str) for slug in slugs):
            raise ValueError("Expected string catalog slugs")
        if len(set(slugs)) != len(slugs) or model not in slugs or not set(slugs) <= allowed_models:
            raise ValueError("Catalog must contain the selected model and only unique, supported slugs")
        check_catalog_metadata(catalog)
        selection += f"model_catalog_json = {json.dumps(windows_catalog_path(catalog_install_path), ensure_ascii=False)}\n"
        files["models.json"] = json.dumps(catalog, ensure_ascii=False, allow_nan=False, indent=2) + "\n"

    if mode == "side-by-side":
        # Publish the provider definition without setting the default or the catalog.
        files["config.toml"] = provider
        files[f"{PROVIDER}.config.toml"] = selection
    else:
        files["config.toml"] = selection + "\n" + provider
    if enforce_provider:
        files["requirements.toml"] = f"model_provider = {json.dumps(PROVIDER)}\n\n" + provider
    return files


def write_bundle(runtime: LocalPaths, revision: str, files: dict[str, str]) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", revision):
        raise ValueError("Expected a lower-case bundle revision")
    output = runtime.checked(runtime.root / "generated" / "codex" / revision)
    private_parent(output, runtime.repository)
    output.mkdir(mode=0o700)
    try:
        protect_new_path(output, directory=True)
        for name, contents in files.items():
            path = output / name
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                protect_new_path(path)
                stream.write(contents)
    except (ValueError, OSError, KeyboardInterrupt):
        shutil.rmtree(output)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--runtime-root", type=Path, help="Explicit absolute checkout containing the protected environment")
    parser.add_argument("--revision", required=True, help="New bundle directory name under generated/codex")
    parser.add_argument("--mode", choices=("side-by-side", "dedicated"), required=True)
    parser.add_argument("--model", required=True, help="Model name or alias from the initial Responses API")
    parser.add_argument("--redirect-port", type=int, default=43127, help="Entra loopback redirect port (default: 43127)")
    parser.add_argument("--enforce-provider", action="store_true", help="Include a requirements file for the dedicated cohort")
    parser.add_argument("--windows-catalog", action="store_true", help="Include the optional protected Windows model catalog")
    args = parser.parse_args()

    try:
        repository = args.runtime_root or REPOSITORY
        if not repository.is_absolute() or not repository.is_dir():
            raise ValueError("Expected an existing absolute checkout")
        private_output(repository / ".local" / ".codex-endpoint-boundary", REPOSITORY)
        runtime = LocalPaths(repository, args.environment)
        with lock_environment(runtime):
            params = read_protected_json(runtime, runtime.params)
            if params.get("environmentName") != runtime.environment:
                raise ValueError("Parameters must match the selected environment")
            settings_path = runtime.checked(runtime.secrets / "codex-endpoint.json")
            settings = read_protected_json(runtime, settings_path) if settings_path.exists() else {}
            # Keep accepting existing endpoint bundles that took the client ID from the deployment parameters.
            client_id = settings["clientId"] if "clientId" in settings else params.get("codex", {}).get("clientId")
            catalog = None
            catalog_install_path = None
            if args.windows_catalog:
                catalog = read_protected_json(runtime, runtime.secrets / "codex-models.json")
                catalog_install_path = settings["catalogInstallPath"]
                if not isinstance(catalog_install_path, str) or not catalog_install_path:
                    raise ValueError("Expected catalogInstallPath")
            files = render_files(
                params, client_id=client_id, mode=args.mode, model=args.model, redirect_port=args.redirect_port,
                enforce_provider=args.enforce_provider, catalog=catalog, catalog_install_path=catalog_install_path,
            )
            write_bundle(runtime, args.revision, files)
    except OperationError as error:
        print(f"CODEX_BUNDLE_FAILED. {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("CODEX_BUNDLE_FAILED. Check the protected input, endpoint options and unused revision.", file=sys.stderr)
        return 1
    print("CODEX_BUNDLE_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
