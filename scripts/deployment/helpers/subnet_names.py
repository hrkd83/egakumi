"""Share the Azure subnet names used by Core Bicep and the deployment tools."""
from __future__ import annotations

import json

from .paths import REPOSITORY


SUBNET_NAMES = json.loads((REPOSITORY / 'infra/modules/network/subnet-names.json').read_text(encoding='utf-8'))


def subnet_resource_id(config: dict, role: str) -> str:
    """Return the owned subnet ID in the selected environment's VNet."""
    return (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['nw']}"
            f"/providers/Microsoft.Network/virtualNetworks/vnet-{config['resourceStem']}/subnets/{SUBNET_NAMES[role]}")
