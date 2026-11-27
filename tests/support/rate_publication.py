"""Shared compiled API publication inputs for policy execution tests."""

import copy

from tests.gateway.publication.test_api_protocols import bound_external_coverage
from tests.support.bicep_helpers import compile_bicep
from tests.support.configuration_fixtures import CORE, configuration


def rate_publication_fixture():
    template = compile_bicep("apps/api-gateway/api-publication.bicep")
    config = configuration.normalize_configuration(bound_external_coverage(), base_directory=CORE / "infra/config")
    api_definitions = copy.deepcopy(config["apiDefinitions"])
    # Publication uses resolved Core URLs. Keep explicit external endpoints.
    for api in api_definitions:
        api.setdefault("backendUrl", "https://backend.example.invalid" + api.get("backendPath", ""))
        for operation in api["operations"]:
            if "backendOverride" in operation:
                operation["backendOverride"].setdefault("backendUrl", "https://backend.example.invalid")
    parameters = {
        "apiDefinitions": api_definitions,
        "credentialLogin": {},
        "subscriptionKeyHeader": config["gateway"]["authentication"]["subscriptionKeyHeader"],
        "publicHostName": config["gateway"]["publicHostName"],
        "entraTenantId": config["entraTenantId"],
    }
    return template, config, parameters
