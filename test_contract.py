import copy
import json
from pathlib import Path
import unittest
import fulfill

class ContractTests(unittest.TestCase):
    def test_conformance(self):
        self.assertTrue(fulfill.self_test()["passed"])
    def test_packaged_sample(self):
        sample = json.loads(Path(__file__).with_name("input.data").read_text())
        got = fulfill.verify_receipt(sample["evidence"], sample["recipient"], sample["amount"], sample["signature"])
        self.assertEqual(got["amountAtomic"], "1250000")
        self.assertEqual(got["attribution"], "unattributed_wallet_net_credit")
    def test_read_only_transport_contract(self):
        sample = fulfill.example_evidence()
        calls = []
        def rpc(endpoint, method, params):
            calls.append((method, params))
            return {"getGenesisHash": sample["evidence"]["genesisHash"],
                    "getTransaction": sample["evidence"]["transaction"],
                    "getSignatureStatuses": {"value": [sample["evidence"]["signatureStatus"]]}}[method]
        got = fulfill.check_payment(sample["recipient"], "1.25", sample["signature"], rpc=rpc)
        self.assertTrue(got["verified"])
        self.assertEqual([call[0] for call in calls], ["getGenesisHash", "getTransaction", "getSignatureStatuses"])
        self.assertEqual(calls[1][1][1]["commitment"], "finalized")
    def test_account_owner_change_cannot_count_balance(self):
        s = fulfill.example_evidence()
        s["evidence"]["transaction"]["meta"]["preTokenBalances"][0]["owner"] = "1" * 31 + "4"
        with self.assertRaises(fulfill.FulfillmentError):
            fulfill.verify_receipt(s["evidence"], s["recipient"], "3.25", s["signature"])
    def test_no_rounded_amount_acceptance(self):
        for value in [1.25, "1.2500001", "NaN", "0", "-1", "1e0"]:
            with self.assertRaises(fulfill.FulfillmentError):
                fulfill.atomic_amount(value)

if __name__ == "__main__":
    unittest.main()
