#!/usr/bin/env python3
"""Render a protected HTTP/SSE policy for manual installation in Azure Portal."""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import base64
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

from deployment.helpers.apim_api_settings import validate_api_settings_inheritance
from deployment.helpers.apim_audit import render_audit_policy
from deployment.helpers.paths import LocalPaths, REPOSITORY, private_output, private_parent
from deployment.helpers.private_permissions import check_private_permissions, protect_new_path


POLICIES = REPOSITORY / "apps/api-gateway/policies"
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
STATIC_PATH = re.compile(r"/[A-Za-z0-9._~/-]*")
DNS_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
HTTPS_RESOURCE = re.compile(rf"https://(?P<host>(?:{DNS_LABEL}\.)+{DNS_LABEL})(:443)?(?P<path>/.*)?")
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
MAX_APIM_PATH_SEGMENT_LENGTH = 1024
MAX_APIM_POLICY_BYTES = 512 * 1024
CLAUDE_AUDIT_PATHS = {"/anthropic/v1/messages", "/anthropic/v1/messages/count_tokens"}
PORTAL_POLICY_FIELDS = "apiId keyProductId backendId backendService backendAuthentication responseTransport operations"


def require_fields(data, fields):
    if not isinstance(data, dict) or data.keys() != set(fields.split()):
        raise ValueError("Unexpected or missing policy settings")


def require_identifier(value, *, manual=False):
    if (not isinstance(value, str) or not IDENTIFIER.fullmatch(value)
            or manual and (not value.startswith("manual-") or value == "manual-")):
        raise ValueError("Invalid manual API, Product, backend or operation identifier")


def require_integer(value, lower, highest):
    if type(value) is not int or not lower <= value <= highest:
        raise ValueError("Invalid policy limit")


def is_fixed_path(path):
    return (isinstance(path, str) and STATIC_PATH.fullmatch(path) is not None
            and "//" not in path and not {".", ".."}.intersection(path.split("/")))


def backend_auth_xml(authentication):
    if not isinstance(authentication, dict):
        raise ValueError("Expected backend authentication settings")
    mode = authentication.get("mode")
    if mode == "none":
        require_fields(authentication, "mode")
        return ""
    if mode == "auditRelay":
        require_fields(authentication, "mode")
        return '<authentication-managed-identity resource="{{core-audit-relay-audience}}" ignore-error="false" />'
    if mode != "managedIdentity":
        raise ValueError("Use no authentication or fixed managed identity for the manual HTTP example")
    require_fields(authentication, "mode resource")
    resource = authentication["resource"]
    match = HTTPS_RESOURCE.fullmatch(resource) if isinstance(resource, str) and len(resource) <= 2048 else None
    if (match is None or len(match["host"]) > 253 or not match["host"].rsplit(".", 1)[-1][0].isalpha()
            or match["path"] is not None and not is_fixed_path(match["path"])):
        raise ValueError("Managed identity requires an explicit HTTPS resource audience")
    return '<authentication-managed-identity resource=' + quoteattr(resource) + ' ignore-error="false" />'


def operation_settings(operations):
    if not isinstance(operations, list) or not operations or len(operations) > 100:
        raise ValueError("Register one or more fixed HTTP operations")
    routes, controls = ET.Element("choose"), {}
    for operation in operations:
        require_fields(operation, "operationId method backendPath copyUnmatchedParams bodyFormat maxRequestBytes")
        operation_id = operation["operationId"]
        require_identifier(operation_id)
        method, path, body = operation["method"], operation["backendPath"], operation["bodyFormat"]
        allow_query, max_bytes = operation["copyUnmatchedParams"], operation["maxRequestBytes"]
        if (operation_id in controls or not isinstance(method, str) or method not in METHODS or not is_fixed_path(path)
                or any(len(segment) > MAX_APIM_PATH_SEGMENT_LENGTH for segment in path.split("/"))
                or type(allow_query) is not bool or not isinstance(body, str)
                or body not in {"none", "json", "binary", "multipart", "text"}):
            raise ValueError("Manual operations require distinct IDs and a fixed backend path")
        if body == "none":
            if max_bytes is not None:
                raise ValueError("The none body format takes a null size")
        else:
            require_integer(max_bytes, 1, 524288000)
        controls[operation_id] = {"kind": "registeredOperation", "bodyFormat": body}
        if max_bytes is not None:
            controls[operation_id]["maxRequestBytes"] = max_bytes
        route = ET.SubElement(routes, "when", {
            "condition": f'@(context.Operation.Id == "{operation_id}" && context.Request.Method == "{method}")',
        })
        ET.SubElement(route, "rewrite-uri", {"template": path, "copy-unmatched-params": str(allow_query).lower()})
        ET.SubElement(route, "set-variable", {"name": "gatewayManualRouteAllowed", "value": "@(true)"})
    ET.indent(routes, space="  ")
    return ET.tostring(routes, encoding="unicode"), {"operationId": "", "operationConstraints": controls}


def snippet(name):
    source = (POLICIES / "snippets/quota" / name / (name + ".xml")).read_text(encoding="utf-8").strip()
    if ET.fromstring(source).tag != "fragment":
        raise ValueError("Invalid shared policy snippet")
    return source.removeprefix("<fragment>").removesuffix("</fragment>").strip("\n")


def insert_block(text, marker, value):
    pattern = r"(?m)^([ \t]*)" + re.escape(marker) + r"$"
    result, count = re.subn(pattern, lambda match: "\n".join(match[1] + line if line else "" for line in value.splitlines()), text)
    if count != 1:
        raise ValueError("A policy block must have a single insertion point")
    return result


def render_portal_policy(configuration):
    fields = PORTAL_POLICY_FIELDS
    if isinstance(configuration, dict) and "bodyAudit" in configuration:
        fields += " bodyAudit"
    require_fields(configuration, fields)
    for field in ("apiId", "keyProductId"):
        require_identifier(configuration[field], manual=True)
    if configuration["keyProductId"] == "manual-entra-users":
        raise ValueError("Use a distinct subscription-required Product")
    require_identifier(configuration["backendService"])
    transport = configuration["responseTransport"]
    if transport not in {"rest", "sse"}:
        raise ValueError("Use a REST or SSE HTTP response transport")
    body_audit = configuration.get("bodyAudit", False)
    if type(body_audit) is not bool or (body_audit and transport != "rest"):
        raise ValueError("APIM body submission requires a buffered REST policy")
    auth = configuration["backendAuthentication"]
    backend_auth = backend_auth_xml(auth)
    routes, control = operation_settings(configuration["operations"])
    use_audit_relay = auth["mode"] == "auditRelay"
    if use_audit_relay:
        if (configuration["backendId"] != "core-audit-relay" or transport != "sse"
                or configuration["backendService"] != "foundry"
                or any(operation["method"] != "POST" or operation["backendPath"] not in CLAUDE_AUDIT_PATHS
                       or operation["bodyFormat"] != "json" for operation in configuration["operations"])):
            raise ValueError("Use the fixed Claude Messages audit backend and SSE policy")
    else:
        require_identifier(configuration["backendId"], manual=True)
    audit = {"serviceClass": "platform", "backendService": configuration["backendService"],
             "backendAuthentication": "managedIdentity" if use_audit_relay else auth["mode"],
             "measurementRole": "execution", "llmOperationIds": []}
    settings = "\n".join(("http", json.dumps(audit, separators=(",", ":")), json.dumps(control, separators=(",", ":"))))
    xml = (POLICIES / "portal-examples/rest.xml").read_text(encoding="utf-8")
    for key, value in {
        "API_SETTINGS": base64.b64encode(settings.encode()).decode(),
        "API_ID": configuration["apiId"], "KEY_PRODUCT_ID": configuration["keyProductId"],
        "BACKEND_ID": configuration["backendId"], "BUFFER_RESPONSE": str(transport == "rest").lower(),
    }.items():
        xml = xml.replace(f"__{key}__", value)
    rate_error = snippet("rate-limit-error").replace("__GATEWAY_TELEMETRY_XML__", '<include-fragment fragment-id="core-gateway-telemetry" />')
    for marker, block in (("REGISTERED_ROUTES_XML", routes), ("BACKEND_AUTH_XML", backend_auth),
                          ("RATE_LIMIT_ERROR_XML", rate_error)):
        xml = insert_block(xml, f"__{marker}__", block)
    for stage in ("snapshot", "request", "response"):
        block = (f'<set-variable name="gatewayEvidenceDirection" value="{stage}" />\n'
                 '<include-fragment fragment-id="core-http-body-evidence" />') if body_audit else ""
        marker = f"__BODY_AUDIT_{stage.upper()}_XML__"
        if not block:
            xml = re.sub(r"(?m)^[ \t]*" + marker + r"\n", "", xml)
        else:
            xml = insert_block(xml, marker, block)
    # C#の展開で字下げだけが入った空行を手動貼り付け用のXMLでは空行に揃える。
    xml = re.sub(r"(?m)^[ \t]+$", "", render_audit_policy(xml))
    if len(xml.encode("utf-8")) > MAX_APIM_POLICY_BYTES:
        raise ValueError("The generated policy exceeds the APIM policy document size limit")
    if re.search(r"__[A-Z][A-Z0-9_]+__", xml) or not validate_api_settings_inheritance(ET.fromstring(xml)):
        raise ValueError("Policy settings or source marker did not render")
    return xml


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--runtime-root", type=Path, help="Absolute checkout storing the selected environment")
    parser.add_argument("--config", type=Path, required=True, help="Protected JSON under the selected environment")
    parser.add_argument("--output", type=Path, required=True, help="New protected XML path under the selected environment")
    arguments = parser.parse_args(argv)
    try:
        repository = arguments.runtime_root or REPOSITORY
        if not repository.is_absolute() or not repository.is_dir():
            raise ValueError("Select an existing checkout")
        private_output(repository / ".local" / ".apim-portal-boundary", REPOSITORY)
        runtime = LocalPaths(repository, arguments.environment)
        source = runtime.checked(arguments.config)
        for parent in source.parents:
            if parent == runtime.repository:
                break
            check_private_permissions(parent, directory=True)
        check_private_permissions(source)
        if source.stat().st_size > 1048576:
            raise ValueError("The manual policy configuration is too large")
        xml = render_portal_policy(json.loads(source.read_text(encoding="utf-8")))
        output = private_parent(runtime.checked(arguments.output), runtime.repository)
        created = False
        try:
            with output.open("x", encoding="utf-8", newline="\n") as destination:
                created = True
                protect_new_path(output)
                destination.write(xml)
        except (OSError, ValueError, KeyboardInterrupt):
            if created:
                output.unlink()
            raise
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError):
        print("Manual APIM policy rendering stopped. Check the protected paths and JSON settings.", file=sys.stderr)
        return 1
    print("Manual APIM policy XML rendered for review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
