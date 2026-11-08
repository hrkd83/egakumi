"""ARM削除成功・purge404から、非公開CM account解放を推定しない。"""
import unittest

from cleanup_fixtures import CleanupFixture, FakeTransport, ProviderGetInconsistencyTransport


class CredentialStoreReleaseTests(CleanupFixture, unittest.TestCase):
    def test_provider_and_parent_deleted_still_leave_internal_release_unverified(self):
        transport = self.credential_transport()
        result = self.pre(transport)
        self.assertEqual(result["status"], "ready-for-azd-down")
        self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
        self.assertTrue(result["apiManagement"]["credentialProviders"][0]["deletionVerified"])
        self.assertFalse(result["credentialManager"]["internalAccountReleaseVerified"])
        self.assertFalse(result["credentialManager"]["sameNameCredentialRecreationVerified"])

    def test_already_absent_parent_does_not_drop_internal_release_qualification(self):
        transport = self.credential_transport()
        transport.resources.pop(self.state["serviceResourceIds"]["apim"])
        result = self.pre(transport)
        self.assertFalse(result["apiManagement"]["deleteRequested"])
        self.assertFalse(result["credentialManager"]["internalAccountReleaseVerified"])
        self.assertEqual(result["credentialManager"]["configuredProviderCount"], 1)

    def test_provider_get200_and_list_absence_stay_unresolved_after_parent404(self):
        original = self.credential_transport()
        provider = next(iter(original.provider_headers))
        transport = ProviderGetInconsistencyTransport(original, provider)
        result = self.pre(transport)
        item = result["apiManagement"]["credentialProviders"][0]
        self.assertTrue(item["listAbsenceVerified"])
        self.assertTrue(item["parentDeletionVerified"])
        self.assertFalse(item["deletionVerified"])
        self.assertEqual(item["individualGetStatus"], 200)
        self.assertEqual(result["credentialManager"]["status"], "unverified")
        self.assertFalse(result["credentialManager"]["internalAccountReleaseVerified"])

    def test_deleting_parent_resume_keeps_qualification_without_repeated_delete(self):
        transport = self.credential_transport()
        apim = self.state["serviceResourceIds"]["apim"]
        transport.resources[apim]["properties"] = {"provisioningState": "Deleting"}
        transport.pending_delete.add(apim)
        transport.delayed_reads[apim] = 1
        result = self.pre(transport)
        self.assertTrue(result["apiManagement"]["alreadyDeleting"])
        self.assertFalse(any(call["method"] == "DELETE" and call["id"] == apim
                             for call in transport.calls))
        self.assertEqual(result["credentialManager"]["status"], "unverified")

    def test_successful_public_purges_are_not_complete_internal_release(self):
        self.credential_transport()  # CM構成だけを使い、実HTTPやsecretは使わない。
        transport = FakeTransport(self.tombstones())
        result = self.post(transport)
        self.assertEqual(result["status"], "resources-absent-with-unverified-credential-manager-release")
        self.assertEqual(result["resourceCleanupStatus"], "complete")
        cm = result["credentialManager"]
        self.assertTrue(cm["apimSoftDeleteAbsenceVerified"])
        self.assertFalse(cm["internalAccountReleaseVerified"])
        self.assertFalse(cm["sameNameCredentialRecreationVerified"])
        expected = {cleanup_id for cleanup_id in self.tombstones()}
        actual = {call["id"].removesuffix("/purge") for call in transport.mutations()}
        self.assertEqual(actual, expected)
        self.assertEqual(len(transport.mutations()), 3)
        self.assertFalse(any("deleteAll" in call["path"] for call in transport.calls))

    def test_tombstones_already_absent_does_not_prove_reuse_or_add_mutations(self):
        self.credential_transport()
        transport = FakeTransport()
        result = self.post(transport)
        self.assertTrue(result["credentialManager"]["apimSoftDeleteAbsenceVerified"])
        self.assertFalse(result["credentialManager"]["internalAccountReleaseVerified"])
        self.assertEqual(transport.mutations(), [])

    def test_protected_retention_remains_visible_beside_internal_release_unknown(self):
        self.credential_transport()
        self.raw["services"]["vaultPurgeProtection"] = True
        self.write_configuration()
        transport = FakeTransport(self.tombstones())
        result = self.post(transport)
        self.assertEqual(result["resourceCleanupStatus"], "complete-with-protected-retention")
        self.assertEqual(result["credentialManager"]["status"], "unverified")
        self.assertEqual(len(transport.mutations()), 2)
        self.assertTrue(any(item["status"] == "retained-protection" for item in result["softDeletedServices"]))

    def test_api_center_read_failure_stays_visible_beside_internal_release_unknown(self):
        self.credential_transport()
        transport = FakeTransport(self.tombstones())
        collection = self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"
        transport.forced["GET", collection] = 403
        result = self.post(transport)
        self.assertEqual(result["resourceCleanupStatus"], "complete-with-unverified-api-center-retention")
        self.assertEqual(result["apiCenterRetention"]["status"], "unverified")
        self.assertFalse(result["apiCenterRetention"]["listReadVerified"])
        self.assertEqual(result["credentialManager"]["status"], "unverified")

    def test_no_configured_cm_does_not_claim_account_release_or_change_result(self):
        result = self.post(FakeTransport())
        self.assertEqual(result["status"], "complete")
        self.assertNotIn("credentialManager", result)
        self.assertNotIn("resourceCleanupStatus", result)


if __name__ == "__main__":
    unittest.main()
