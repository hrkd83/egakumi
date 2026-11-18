#!/usr/bin/env python3
"""既存APIMを現在の全API資材へ整合する。一時的に社員Bearer経路を閉じる。

既定はローカル計画のみ。--executeでも基盤・モデル・Broker・Portalを配備
しない。旧生成元の不足を読取XMLの自己申告で補わず、閉じた現在資材の配備と
完全なv2生成元の照合によって復旧する、管理者向けの明示的な工程。
"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
from pathlib import Path
import secrets

from deployment.helpers.configuration import load_configuration_source
from deployment.helpers.paths import REPOSITORY, local_paths
from deployment.lifecycle import ArmTransport, DeploymentError, protected_json, run as run_json
from standard_apis import resolve_publication_configuration

sys.path.insert(0, str(REPOSITORY / "apps/gateway/deploy"))
import api_publication


def backup_document(inventory):
    """競合・復旧用の実読取を保存する。生成元の証明へ変換しない。"""
    return {"schemaVersion": 1, "provenance": "readback-backup-not-generated-source",
            "apimId": inventory.apim_id, "apiIds": sorted(inventory.api_ids),
            "protectedApiIds": sorted(inventory.protected_api_ids),
            "previousMembers": sorted(inventory.previous_members),
            "snapshot": {rid: {"path": path, "headers": headers, "response": response}
                         for rid, (path, headers, response) in inventory.snapshot.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    source = load_configuration_source(args.config)
    workspace = local_paths(source, REPOSITORY)
    workspace.checked(args.config)
    workspace.check_selection(source)
    state_file = workspace.checked(args.state_file or workspace.state / "latest.json")
    output = workspace.checked(args.output or workspace.state / "apim-source-reconciliation-plan.json")
    checkpoint_file = workspace.checked(workspace.state / "gateway-api-source.json")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    config = resolve_publication_configuration(source, state)
    if checkpoint_file.is_file():
        state["apiPublicationSourceCheckpoint"] = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    plan = api_publication.prepare_plan(config, state)
    summary = {"status": "planned", "sourceReconciliation": True, "azureWrites": 0,
               "apiCount": len(plan["publishedApiIds"]), "employeePathInterruptedDuringExecution": True,
               "modelsDeployed": False, "foundationDeployed": False, "brokerDeployed": False,
               "portalDeployed": False, "runtimeVerified": False}
    protected_json(output, {**summary, "apiPublication": plan})
    if not args.execute:
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    # Compilation and all existing execute_plan prerequisites precede mutation.
    # No model/infra/Gateway application template is compiled or deployed here.
    template = run_json(["az", "bicep", "build", "--file", str(api_publication.MODULE), "--stdout"])
    evidence = workspace.checked(workspace.root / "evidence" / ("apim-source-reconciliation-" + secrets.token_hex(6)))
    transport = ArmTransport(config)
    result = api_publication.execute_plan(config, state, plan, template, transport,
        reconcile_existing_source=True,
        backup=lambda inventory: protected_json(evidence / "inventory-before.json", backup_document(inventory)),
        checkpoint=lambda value: protected_json(checkpoint_file, value))
    protected_json(workspace.state / "apim-source-reconciliation.json", result)
    authentication = result.get("entraAuthentication") or {}
    # Console output contains counts/status only, never environment values or XML.
    print(json.dumps({"status": result["status"], "apiCount": len(result["publishedApiIds"]),
                      "sourceReconciled": authentication.get("sourceReconciled", False),
                      "managementPlaneVerified": authentication.get("managementPlaneVerified", False),
                      "employeePathOpen": authentication.get("employeePathOpen", False),
                      "runtimeVerified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, DeploymentError) as error:
        detail = str(error) if isinstance(error, (ValueError, DeploymentError)) else "check the selected configuration and protected state"
        raise SystemExit("APIM source reconciliation stopped: " + detail) from None
