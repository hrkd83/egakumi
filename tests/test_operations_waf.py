"""WAF保守入口が既存の限定reconcilerと所有境界を保持する。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operations import waf


class OperationsWafTests(unittest.TestCase):
    def test_plan_has_no_arm_identity_check_or_mutations(self):
        context = mock.Mock(config={})
        with mock.patch.object(waf.waf_policy, "load_manifest", return_value={}), \
                mock.patch.object(waf.waf_policy, "reconcile", return_value={"status": "planned"}) as reconcile:
            waf.run(context, scope="portal")
        context.verify_resource.assert_not_called()
        self.assertIsNone(reconcile.call_args.args[1])
        self.assertFalse(reconcile.call_args.kwargs["execute"])
        self.assertEqual(reconcile.call_args.kwargs["scopes"], ("portal",))

    def test_execute_verifies_gateway_before_delegation_and_retains_result(self):
        context = mock.Mock(config={})
        result = {"status": "reconciled", "azureWrites": 0}
        with mock.patch.object(waf.waf_policy, "load_manifest", return_value={}), \
                mock.patch.object(waf.waf_policy, "gateway_id", return_value="owned-id"), \
                mock.patch.object(waf.waf_policy, "reconcile", return_value=result) as reconcile:
            self.assertEqual(waf.run(context, execute=True), result)
        context.verify_resource.assert_called_once_with("owned-id", "2024-05-01", "Microsoft.Network/applicationGateways")
        self.assertIs(reconcile.call_args.args[1], context.transport)
        # 既存reconcilerは長時間の反映待ちでprint互換のflushを渡す。
        reconcile.call_args.kwargs["progress"]("waiting", flush=True)
        context.record.assert_called_once_with("waf", {"execute": True, "result": result})

    def test_wrong_owner_stops_before_any_policy_update(self):
        context = mock.Mock(config={})
        context.verify_resource.side_effect = waf.OperationError("wrong owner")
        with mock.patch.object(waf.waf_policy, "load_manifest", return_value={}), \
                mock.patch.object(waf.waf_policy, "gateway_id", return_value="owned-id"), \
                mock.patch.object(waf.waf_policy, "reconcile") as reconcile:
            with self.assertRaises(waf.OperationError):
                waf.run(context, execute=True)
        reconcile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
