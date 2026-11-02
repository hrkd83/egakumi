"""公開DNS設定を合成ARM応答で検査する。実Azure・実DNS・秘密入力へ接続しない。"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import public_dns


CORE_SUB = "11111111-1111-1111-1111-111111111111"
DNS_SUB = "22222222-2222-2222-2222-222222222222"
PIP_ID = f"/subscriptions/{CORE_SUB}/resourceGroups/rg-test-unit-nw/providers/Microsoft.Network/publicIPAddresses/pip-agw-test-unit"
ADDRESS = "203.0.113.10"


def response(body=None, status=200, headers=None):
    return {"statusCode": status, "body": copy.deepcopy(body), "headers": headers or {}}


def zone(name="example.com", group="separate-dns-rg", subscription=DNS_SUB):
    return {"id": f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Network/dnsZones/{name}",
            "name": name, "type": "Microsoft.Network/dnsZones", "location": "global"}


def record(name, *, selected_zone=None, kind="A", target=None, addresses=None, ttl=300, metadata=None):
    selected_zone = selected_zone or zone()
    props = {"TTL": ttl}
    if target is not None:
        props["targetResource"] = {"id": target}
    if addresses is not None:
        props["ARecords"] = copy.deepcopy(addresses)
    if metadata is not None:
        props["metadata"] = copy.deepcopy(metadata)
    return {"id": selected_zone["id"] + "/" + kind + "/" + name,
            "name": name, "type": "Microsoft.Network/dnsZones/" + kind,
            "etag": '"record-v1"', "properties": props}


class CoreTransport:
    def __init__(self):
        self.calls = []
        self.body = {"id": PIP_ID, "name": "pip-agw-test-unit", "type": "Microsoft.Network/publicIPAddresses",
                     "properties": {"provisioningState": "Succeeded", "publicIPAllocationMethod": "Static",
                                    "publicIPAddressVersion": "IPv4", "ipAddress": ADDRESS}}

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path))
        if method != "GET":
            raise AssertionError("Public IP is read-only")
        return response(self.body)


class DnsTransport:
    def __init__(self, *, zones=None, records=None):
        self.zones = copy.deepcopy([zone()] if zones is None else zones)
        self.records = {item["id"].casefold(): copy.deepcopy(item) for item in (records or [])}
        self.calls = []
        self.hook = lambda call: None
        self.pages = None

    def request(self, method, path, body=None, headers=None):
        call = {"method": method, "path": path, "body": copy.deepcopy(body), "headers": copy.deepcopy(headers)}
        self.calls.append(call)
        replacement = self.hook(call)
        if replacement is not None:
            return replacement
        uri = urlparse(path if path.startswith("https://") else "https://management.azure.com" + path)
        resource_id = uri.path
        collection = f"/subscriptions/{DNS_SUB}/providers/Microsoft.Network/dnsZones"
        if method == "GET" and resource_id.casefold() == collection.casefold():
            if self.pages is None:
                return response({"value": self.zones})
            number = parse_qs(uri.query).get("$skiptoken", ["0"])[0]
            return response(self.pages[int(number)])
        key = resource_id.casefold()
        if method == "GET":
            return response(self.records[key]) if key in self.records else response(status=404)
        if method not in ("PUT", "PATCH"):
            raise AssertionError("Unexpected DNS mutation")
        item = {"id": resource_id, "name": resource_id.rsplit("/", 1)[1], "type": "Microsoft.Network/dnsZones/A",
                "etag": '"record-v2"', "properties": copy.deepcopy(body["properties"])}
        self.records[key] = item
        return response(item, status=201 if method == "PUT" else 200)

    def mutations(self):
        return [call for call in self.calls if call["method"] != "GET"]


class PublicDnsTests(unittest.TestCase):
    def setUp(self):
        self.config = {"subscriptionId": CORE_SUB, "azureTenantId": "33333333-3333-3333-3333-333333333333",
                       "resourceStem": "test-unit", "resourceGroupNames": {"nw": "rg-test-unit-nw"},
                       "gateway": {"publicHostName": "gateway.example.com"},
                       "portal": {"publicHostName": "portal.example.com"},
                       "certificate": {"mode": "dnsPlugin", "plugin": "Azure", "azureDnsSubscriptionId": DNS_SUB,
                                       "azureDnsManagedIdentityResourceId": f"/subscriptions/{DNS_SUB}/resourceGroups/id-only-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dns-id"}}
        self.outputs = {"gateway": {"publicIpId": PIP_ID, "publicIp": ADDRESS}}
        self.core = CoreTransport()
        self.dns = DnsTransport()

    def configure(self):
        return public_dns.configure_public_dns(self.config, self.outputs, self.core, self.dns)

    def test_new_cross_subscription_aliases_are_conditionally_created_and_read_back(self):
        original = copy.deepcopy(self.config)
        result = self.configure()
        self.assertEqual(result["status"], "configured")
        self.assertEqual(result["dnsSubscriptionId"], DNS_SUB)
        self.assertEqual([item["status"] for item in result["records"]], ["created", "created"])
        self.assertTrue(all(CORE_SUB in path for _, path in self.core.calls))
        self.assertTrue(all(DNS_SUB in call["path"] for call in self.dns.calls))
        writes = self.dns.mutations()
        self.assertEqual(len(writes), 2)
        for call in writes:
            self.assertEqual(call["method"], "PUT")
            self.assertEqual(call["headers"], {"If-None-Match": "*"})
            self.assertEqual(call["body"]["properties"], {"TTL": 300, "targetResource": {"id": PIP_ID}, "ARecords": []})
            self.assertIn("separate-dns-rg", call["path"])
            self.assertNotIn("id-only-rg", call["path"])
        self.assertEqual(self.config, original)

    def test_repeat_run_leaves_matching_aliases_unchanged(self):
        self.configure()
        self.dns.calls.clear()
        result = self.configure()
        self.assertEqual([item["status"] for item in result["records"]], ["unchanged", "unchanged"])
        self.assertEqual(self.dns.mutations(), [])

    def test_cleared_alias_is_reconnected_with_original_ttl_metadata_and_etag(self):
        items = [record(name, ttl=1200, metadata={"owner": "dns-team"}, addresses=[]) for name in ("gateway", "portal")]
        for item in items:
            item["properties"]["targetResource"] = {}
        self.dns = DnsTransport(records=items)
        result = self.configure()
        self.assertEqual([item["status"] for item in result["records"]], ["updated", "updated"])
        for call in self.dns.mutations():
            self.assertEqual(call["method"], "PATCH")
            self.assertEqual(call["headers"], {"If-Match": '"record-v1"'})
            self.assertEqual(call["body"]["properties"]["TTL"], 1200)
            self.assertEqual(call["body"]["properties"]["metadata"], {"owner": "dns-team"})

    def test_current_raw_ipv4_is_converted_to_resource_alias(self):
        self.dns = DnsTransport(records=[record("gateway", addresses=[{"ipv4Address": ADDRESS}])])
        self.configure()
        call = self.dns.mutations()[0]
        self.assertEqual(call["method"], "PATCH")
        self.assertEqual(call["body"]["properties"]["ARecords"], [])
        self.assertEqual(call["body"]["properties"]["targetResource"], {"id": PIP_ID})

    def test_portal_conflict_is_found_before_gateway_is_changed(self):
        conflicts = [record("portal", target=PIP_ID + "-another"),
                     record("portal", addresses=[{"ipv4Address": "203.0.113.11"}]),
                     record("portal", addresses=[{"ipv4Address": ADDRESS}, {"ipv4Address": ADDRESS}]),
                     record("portal", kind="CNAME")]
        for item in conflicts:
            with self.subTest(item=item):
                self.dns = DnsTransport(records=[item])
                before = copy.deepcopy(self.dns.records)
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.dns.mutations(), [])
                self.assertEqual(self.dns.records, before)

    def test_longest_matching_zone_is_selected_for_each_host(self):
        self.config["gateway"]["publicHostName"] = "gateway.sub.example.com"
        self.config["portal"]["publicHostName"] = "portal.sub.example.com"
        self.dns = DnsTransport(zones=[zone(), zone("sub.example.com", "child-zone-rg")])
        result = self.configure()
        self.assertTrue(all(item["zoneId"].endswith("/dnsZones/sub.example.com") for item in result["records"]))
        self.assertTrue(all("child-zone-rg" in item["zoneId"] for item in result["records"]))

    def test_distinct_zones_can_serve_gateway_and_portal(self):
        self.config["portal"]["publicHostName"] = "portal.other.example.net"
        self.dns = DnsTransport(zones=[zone(), zone("other.example.net")])
        result = self.configure()
        self.assertEqual(result["records"][0]["zoneId"], zone()["id"])
        self.assertEqual(result["records"][1]["zoneId"], zone("other.example.net")["id"])

    def test_matching_zone_apex_uses_at_record_name(self):
        self.config["gateway"]["publicHostName"] = "example.com"
        result = self.configure()
        self.assertTrue(result["records"][0]["recordSetId"].endswith("/A/@"))

    def test_no_matching_or_ambiguous_longest_zone_stops_before_writes(self):
        for zones in ([], [zone("other.com")], [zone(), zone(group="another-rg")]):
            with self.subTest(zones=zones):
                self.dns = DnsTransport(zones=zones)
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.dns.mutations(), [])

    def test_shorter_duplicate_zones_do_not_make_unique_child_ambiguous(self):
        self.config["gateway"]["publicHostName"] = "gateway.sub.example.com"
        self.config["portal"]["publicHostName"] = "portal.sub.example.com"
        self.dns = DnsTransport(zones=[zone(), zone(group="another-rg"), zone("sub.example.com")])
        self.assertEqual(self.configure()["status"], "configured")

    def test_paginated_zone_list_is_exhausted_before_zone_selection(self):
        collection = f"https://management.azure.com/subscriptions/{DNS_SUB}/providers/Microsoft.Network/dnsZones"
        self.dns.pages = [{"value": [zone()], "nextLink": collection + "?api-version=2018-05-01&$skiptoken=1"},
                          {"value": [zone(group="another-rg")], "nextLink": ""}]
        with self.assertRaisesRegex(public_dns.PublicDnsError, "more than one"):
            self.configure()
        self.assertEqual(self.dns.mutations(), [])

    def test_untrusted_pagination_destinations_are_rejected(self):
        collection = f"https://management.azure.com/subscriptions/{DNS_SUB}/providers/Microsoft.Network/dnsZones"
        links = ["https://untrusted.example.com/?api-version=2018-05-01", collection.replace(DNS_SUB, CORE_SUB) + "?api-version=2018-05-01",
                 collection + "?api-version=2020-01-01", collection + "?api-version=2018-05-01&api-version=2018-05-01",
                 collection + "/foreign?api-version=2018-05-01", collection + "?api-version=2018-05-01#fragment",
                 collection + "?api-version=2018-05-01", collection + "?api-version=2018-05-01\n"]
        for link in links:
            with self.subTest(link=link):
                self.dns = DnsTransport()
                self.dns.pages = [{"value": [zone()], "nextLink": link}]
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.dns.mutations(), [])

    def test_pagination_is_bounded_even_when_every_link_is_unique(self):
        collection = f"https://management.azure.com/subscriptions/{DNS_SUB}/providers/Microsoft.Network/dnsZones"
        self.dns.pages = [{"value": [], "nextLink": collection + f"?api-version=2018-05-01&$skiptoken={index + 1}"}
                          for index in range(public_dns.MAX_PAGES + 1)]
        with self.assertRaisesRegex(public_dns.PublicDnsError, "pagination"):
            self.configure()
        self.assertEqual(len(self.dns.calls), public_dns.MAX_PAGES)
        self.assertEqual(self.dns.mutations(), [])

    def test_malformed_zone_list_and_continuation_fail_without_writes(self):
        for page in ({"value": None}, {"value": [], "nextLink": 123}, {"value": [None]},
                     {"value": [zone(), zone()]}):
            with self.subTest(page=page):
                self.dns = DnsTransport()
                self.dns.pages = [page]
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.dns.mutations(), [])

    def test_zone_identity_must_belong_to_the_explicit_dns_subscription(self):
        invalid = [zone(subscription=CORE_SUB), {**zone(), "type": "Microsoft.Network/privateDnsZones"},
                   {**zone(), "name": "mismatched.example.com"}, {**zone(), "name": "bad/zone"}]
        for item in invalid:
            with self.subTest(item=item):
                self.dns = DnsTransport(zones=[item])
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.dns.mutations(), [])

    def test_public_ip_output_must_match_the_expected_environment(self):
        self.outputs["gateway"]["publicIpId"] = PIP_ID + "-another"
        with self.assertRaises(public_dns.PublicDnsError):
            self.configure()
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.dns.calls, [])

    def test_public_ip_must_be_static_succeeded_ipv4_and_equal_to_output(self):
        changes = [("id", PIP_ID + "-another"), ("type", "Microsoft.Network/privateEndpoints"),
                   ("name", "another"), ("provisioningState", "Updating"), ("publicIPAllocationMethod", "Dynamic"),
                   ("publicIPAddressVersion", "IPv6"), ("ipAddress", "203.0.113.11"), ("ipAddress", None)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.core = CoreTransport()
                target = self.core.body if key in ("id", "type", "name") else self.core.body["properties"]
                target[key] = value
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.dns.calls, [])

    def test_missing_or_invalid_etag_stops_before_any_write(self):
        for etag in (None, "", "\r\ninvalid"):
            with self.subTest(etag=etag):
                item = record("portal")
                item["etag"] = etag
                self.dns = DnsTransport(records=[item])
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.dns.mutations(), [])

    def test_case_insensitive_etag_header_can_supply_concurrency_guard(self):
        self.dns = DnsTransport(records=[record("gateway"), record("portal")])

        def header_only(call):
            if call["method"] == "GET":
                key = urlparse(call["path"]).path.casefold()
                if key in self.dns.records:
                    item = copy.deepcopy(self.dns.records[key])
                    item.pop("etag")
                    return response(item, headers={"eTaG": '"header-version"'})
            return None

        self.dns.hook = header_only
        self.configure()
        self.assertTrue(all(call["headers"] == {"If-Match": '"header-version"'} for call in self.dns.mutations()))

    def test_record_shape_and_ownership_are_checked(self):
        modifiers = [lambda item: item.update(type="Microsoft.Network/dnsZones/TXT"),
                     lambda item: item.update(id=item["id"] + "-another"),
                     lambda item: item["properties"].update(TTL=True),
                     lambda item: item["properties"].update(TTL=0),
                     lambda item: item["properties"].update(metadata={"owner": 1}),
                     lambda item: item["properties"].update(targetResource={"id": PIP_ID, "other": "value"}),
                     lambda item: item["properties"].update(targetResource=[]),
                     lambda item: item["properties"].update(ARecords=None)]
        for change in modifiers:
            with self.subTest(change=change):
                item = record("portal")
                change(item)
                self.dns = DnsTransport()
                # Keep the lookup key stable even when the returned resource identity is wrong.
                self.dns.records[record("portal")["id"].casefold()] = item
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.dns.mutations(), [])

    def test_denied_preflight_is_not_treated_as_missing_record(self):
        self.dns.hook = lambda call: response({"message": "PRIVATE_RESPONSE"}, status=403) if "/A/portal" in call["path"] else None
        with self.assertRaises(public_dns.PublicDnsError) as error:
            self.configure()
        self.assertNotIn("PRIVATE_RESPONSE", str(error.exception))
        self.assertEqual(self.dns.mutations(), [])

    def test_etag_conflict_is_not_retried_or_overwritten(self):
        self.dns = DnsTransport(records=[record("gateway"), record("portal")])
        self.dns.hook = lambda call: response(status=412) if call["method"] == "PATCH" else None
        with self.assertRaisesRegex(public_dns.PublicDnsError, "HTTP 412"):
            self.configure()
        self.assertEqual(len(self.dns.mutations()), 1)
        self.assertTrue(self.dns.mutations()[0]["path"].endswith("/A/gateway?api-version=2018-05-01"))

    def test_readback_checks_alias_address_ttl_and_metadata(self):
        for field in ("target", "address", "ttl", "metadata"):
            with self.subTest(field=field):
                self.dns = DnsTransport()
                written = set()

                def alter_readback(call):
                    if call["method"] in ("PUT", "PATCH"):
                        written.add(urlparse(call["path"]).path.casefold())
                    if call["method"] == "GET" and urlparse(call["path"]).path.casefold() in written:
                        item = record("gateway", target=PIP_ID, addresses=[])
                        props = item["properties"]
                        if field == "target": props["targetResource"]["id"] += "-other"
                        elif field == "address": props["ARecords"] = [{"ipv4Address": ADDRESS}]
                        elif field == "ttl": props["TTL"] = 999
                        else: props["metadata"] = {"owner": "other"}
                        return response(item)
                    return None

                self.dns.hook = alter_readback
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(len(self.dns.mutations()), 1)

    def test_non_azure_certificate_providers_return_external_without_cloud_calls(self):
        for certificate in ({"mode": "prepared"}, {"mode": "dnsPlugin", "plugin": "Cloudflare"},
                            {"mode": "dnsPlugin", "plugin": "Route53"}):
            with self.subTest(certificate=certificate):
                self.config["certificate"] = certificate
                result = self.configure()
                self.assertEqual(result["status"], "external")
                self.assertEqual(result["hostnames"], ["gateway.example.com", "portal.example.com"])
                self.assertEqual(self.core.calls, [])
                self.assertEqual(self.dns.calls, [])

    def test_both_hostnames_are_required_and_must_be_distinct(self):
        for value in (None, "gateway.example.com", "https://portal.example.com/", "portal..example.com"):
            with self.subTest(value=value):
                self.config["portal"]["publicHostName"] = value
                with self.assertRaises(public_dns.PublicDnsError):
                    self.configure()
                self.assertEqual(self.core.calls, [])
                self.assertEqual(self.dns.calls, [])


if __name__ == "__main__":
    unittest.main()
