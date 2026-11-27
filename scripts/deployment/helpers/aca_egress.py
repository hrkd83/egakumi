"""Keep ACA public HTTPS behind the Firewall on redeploy and NSG-only updates."""

from ipaddress import IPv4Address, IPv4Network

from .configuration import RFC1918_CIDRS

_PRIVATE_RANGES = tuple(map(IPv4Network, RFC1918_CIDRS))
_DEFAULT_ROUTE = IPv4Network("0.0.0.0/0")
_UNVERIFIED_ROUTE = "ACA public HTTPS requires the owned Firewall route without a bypass"


def validate_aca_route_snapshot(routes, firewall_subnet, app_gateway_subnet, *,
                                firewall_ip=None, require_default=False):
    """Check ACA's IPv4 UDRs. A partially constructed Core may lack its default child."""
    observed = set()
    try:
        subnet = IPv4Network(firewall_subnet)
        managed_routes = {"default-egress": _DEFAULT_ROUTE, "agw-return": IPv4Network(app_gateway_subnet)}
        expected_hop = IPv4Address(firewall_ip) if firewall_ip is not None else None
        if expected_hop is not None and expected_hop not in subnet:
            raise ValueError()
        for route in routes:
            name = route["name"].casefold()
            fields = route["properties"]
            prefix = IPv4Network(fields["addressPrefix"])
            if name in managed_routes:
                hop = IPv4Address(fields["nextHopIpAddress"])
                if (name in observed or prefix != managed_routes[name]
                        or fields["nextHopType"] != "VirtualAppliance" or hop not in subnet
                        or (expected_hop is not None and hop != expected_hop)):
                    raise ValueError()
                observed.add(name)
            elif not any(prefix.subnet_of(private) for private in _PRIVATE_RANGES):
                # Unowned public or service-tag UDRs can override the fixed default route.
                raise ValueError()
        if require_default and "default-egress" not in observed:
            raise ValueError()
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError(_UNVERIFIED_ROUTE) from None
