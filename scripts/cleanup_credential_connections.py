#!/usr/bin/env python3
"""CM接続の調査一覧。削除はSQL台帳と整合性を確認するbrokerのreconcileで行う。"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
import re
from urllib.parse import urlsplit

from deployment.helpers.configuration import load_configuration
from deployment.helpers.apim_naming import apim_name
from deployment.helpers.credential_pools import physical_providers
from deployment.lifecycle import ArmTransport, require_success


def inventory(configuration, transport, *, now=None, execute=False):
    """永続接続を作成日時で判定せず、設定したproviderのメタデータだけを読む。"""
    if execute:
        raise ValueError("Connection deletion requires the credential broker SQL reconciliation job; this command is read-only")
    names = configuration.get("gateway", {}).get("authentication", {}).get("credentialLogin", {}).get("providerNames", [])
    if not names:
        raise ValueError("Credential login is not configured")
    base = (f"/subscriptions/{configuration['subscriptionId']}/resourceGroups/"
            f"{configuration['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/"
            f"service/{apim_name(configuration)}")
    result = []
    for provider in physical_providers(configuration):
        if provider["sourceName"] not in names:
            continue
        resource = base + "/authorizationProviders/" + provider["name"] + "/authorizations"
        path, visited = resource + "?api-version=2024-05-01", set()
        while path:
            if path in visited or len(visited) >= 100:
                raise ValueError("Credential connection listing did not finish")
            visited.add(path)
            page = require_success(transport.request("GET", path), "Read credential connections")
            if not isinstance(page.get("value"), list):
                raise ValueError("Credential connection listing is invalid")
            for item in page["value"]:
                if not isinstance(item, dict):
                    raise ValueError("Credential connection listing is invalid")
                name = item.get("name", "")
                if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", name)
                        or not isinstance(item.get("id"), str) or item["id"].casefold() != (resource + "/" + name).casefold()):
                    raise ValueError("Credential connection identity does not match its provider")
                result.append({"resourceId": item["id"], "providerName": provider["name"],
                               "status": item.get("properties", {}).get("status", "unknown")})
            path = page.get("nextLink")
            if path:
                parsed = urlsplit(path)
                if (parsed.scheme != "https" or parsed.hostname != "management.azure.com" or parsed.port not in (None, 443)
                        or parsed.username or parsed.password or parsed.fragment or parsed.path.casefold() != resource.casefold()):
                    raise ValueError("Credential connection listing left its provider")
    return {"mode": "inventory", "automaticDeletion": False, "connectionCount": len(result), "connections": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    configuration = load_configuration(args.config)
    print(json.dumps(inventory(configuration, ArmTransport(configuration))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
