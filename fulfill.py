"""Read-only native Solana USDC receipt checking and reproducible source packaging.

Python 3.10+, standard library only. No private key, transfer, signing, transaction
submission, account creation, model, invoice attribution, or payment ledger.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ENGINE_VERSION = "solana-receipt-1.0.0"
OPERATION = "verify_solana_usdc"
MAINNET_GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
MAX_BYTES = 4 * 1024 * 1024
MAX_U64 = (1 << 64) - 1
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
PACKAGE_NAMES = {"fulfill.py", "README.md", "test_contract.py", "contract.json", "package-spec.json", "input.data"}


class FulfillmentError(ValueError):
    """Stable, non-secret error code; an error is never a positive receipt."""


def require(condition, code):
    if not condition:
        raise FulfillmentError(code)


def canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise FulfillmentError("INVALID_JSON") from None


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _constant(_value):
    raise FulfillmentError("NONFINITE_JSON")


def decode_json(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, "JSON_TOO_LARGE")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise FulfillmentError("INVALID_JSON") from None


def _base58(value, size, code):
    require(type(value) is str and 1 <= len(value) <= 90 and all(c in ALPHABET for c in value), code)
    number = 0
    for char in value:
        number = number * 58 + ALPHABET.index(char)
    raw = b"\0" * (len(value) - len(value.lstrip("1")))
    if number:
        raw += number.to_bytes((number.bit_length() + 7) // 8, "big")
    require(len(raw) == size, code)
    return value


def atomic_amount(amount):
    """USDC decimal text to exact integer units, without binary floating point."""
    require(type(amount) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,6})?", amount), "AMOUNT_INVALID")
    whole, _, fraction = amount.partition(".")
    result = int(whole) * 1_000_000 + int(fraction.ljust(6, "0") or "0")
    require(0 < result <= MAX_U64, "AMOUNT_OUT_OF_RANGE")
    return result


def _integer(value, code, maximum=MAX_U64):
    require(type(value) is int and 0 <= value <= maximum, code)
    return value


def _balance_map(rows, accounts):
    require(type(rows) is list and len(rows) <= len(accounts), "TOKEN_BALANCES_UNAVAILABLE")
    result = {}
    for row in rows:
        require(type(row) is dict, "TOKEN_BALANCE_INVALID")
        index = _integer(row.get("accountIndex"), "TOKEN_ACCOUNT_INDEX_INVALID", len(accounts) - 1)
        require(index not in result, "DUPLICATE_TOKEN_ACCOUNT")
        mint = _base58(row.get("mint"), 32, "TOKEN_MINT_INVALID")
        # Other assets must still reserve the account index to detect asset changes.
        if mint != USDC_MINT:
            result[index] = {"mint": mint}
            continue
        owner = _base58(row.get("owner"), 32, "USDC_OWNER_UNAVAILABLE")
        require(row.get("programId") == TOKEN_PROGRAM, "USDC_PROGRAM_UNAVAILABLE")
        token = row.get("uiTokenAmount")
        require(type(token) is dict and type(token.get("decimals")) is int and token["decimals"] == 6,
                "USDC_DECIMALS_INVALID")
        amount = token.get("amount")
        require(type(amount) is str and re.fullmatch(r"0|[1-9][0-9]{0,19}", amount), "TOKEN_AMOUNT_INVALID")
        result[index] = {"mint": mint, "owner": owner, "amount": _integer(int(amount), "TOKEN_AMOUNT_RANGE")}
    return result


def verify_receipt(evidence, recipient, amount, signature, *, min_block_time=None):
    """Verify a trusted RPC evidence bundle; offline fixtures are not live proof.

    Evidence keys: genesisHash (getGenesisHash result), transaction (getTransaction
    result, jsonParsed), signatureStatus (the single getSignatureStatuses value).
    Checks exact recipient net USDC increase across all transaction token accounts.
    """
    _base58(recipient, 32, "RECIPIENT_INVALID")
    _base58(signature, 64, "SIGNATURE_INVALID")
    expected = atomic_amount(amount)
    if min_block_time is not None:
        _integer(min_block_time, "MIN_BLOCK_TIME_INVALID")
    require(type(evidence) is dict, "EVIDENCE_INVALID")
    require(evidence.get("genesisHash") == MAINNET_GENESIS, "WRONG_NETWORK")
    transaction = evidence.get("transaction")
    require(type(transaction) is dict, "TRANSACTION_UNAVAILABLE")
    version = transaction.get("version", "legacy")
    require(version == "legacy" or type(version) is int and version == 0, "TRANSACTION_VERSION_UNSUPPORTED")
    slot = _integer(transaction.get("slot"), "TRANSACTION_SLOT_INVALID")
    meta = transaction.get("meta")
    require(type(meta) is dict and "err" in meta and meta["err"] is None, "TRANSACTION_FAILED_OR_UNKNOWN")
    status = evidence.get("signatureStatus")
    require(type(status) is dict and status.get("confirmationStatus") == "finalized", "NOT_FINALIZED")
    require("err" in status and status["err"] is None, "SIGNATURE_FAILED_OR_UNKNOWN")
    require(type(status.get("slot")) is int and status["slot"] == slot, "FINALITY_SLOT_MISMATCH")
    tx = transaction.get("transaction")
    require(type(tx) is dict and type(tx.get("signatures")) is list and tx["signatures"]
            and tx["signatures"][0] == signature, "TRANSACTION_SIGNATURE_MISMATCH")
    message = tx.get("message")
    require(type(message) is dict and type(message.get("accountKeys")) is list, "ACCOUNT_KEYS_UNAVAILABLE")
    keys = message["accountKeys"]
    require(1 <= len(keys) <= 256, "ACCOUNT_KEYS_INVALID")
    accounts = []
    for key in keys:
        require(type(key) is dict, "JSON_PARSED_ACCOUNT_KEYS_REQUIRED")
        accounts.append(_base58(key.get("pubkey"), 32, "ACCOUNT_KEY_INVALID"))
    require(len(set(accounts)) == len(accounts), "DUPLICATE_ACCOUNT_KEY")
    pre = _balance_map(meta.get("preTokenBalances"), accounts)
    post = _balance_map(meta.get("postTokenBalances"), accounts)
    pre_lamports, post_lamports = meta.get("preBalances"), meta.get("postBalances")
    require(type(pre_lamports) is list and type(post_lamports) is list
            and len(pre_lamports) == len(accounts) == len(post_lamports), "ACCOUNT_BALANCES_UNAVAILABLE")
    for value in pre_lamports + post_lamports:
        _integer(value, "ACCOUNT_BALANCE_INVALID")
    delta = 0
    changes = []
    for index in sorted(set(pre) | set(post)):
        before, after = pre.get(index), post.get(index)
        owns_before = before and before.get("mint") == USDC_MINT and before.get("owner") == recipient
        owns_after = after and after.get("mint") == USDC_MINT and after.get("owner") == recipient
        if not (owns_before or owns_after):
            continue
        if before is not None and after is not None:
            require(before.get("mint") == after.get("mint") == USDC_MINT
                    and before.get("owner") == after.get("owner") == recipient,
                    "RECIPIENT_ACCOUNT_IDENTITY_CHANGED")
        elif before is None:
            require(pre_lamports[index] == 0, "PRE_BALANCE_MISSING_FOR_EXISTING_ACCOUNT")
        else:
            require(post_lamports[index] == 0, "POST_BALANCE_MISSING_FOR_EXISTING_ACCOUNT")
        change = (after["amount"] if after else 0) - (before["amount"] if before else 0)
        delta += change
        changes.append({"tokenAccount": accounts[index], "deltaAtomic": str(change)})
    require(delta == expected, "RECEIVED_AMOUNT_MISMATCH")
    block_time = transaction.get("blockTime")
    require(block_time is None or type(block_time) is int and block_time >= 0, "BLOCK_TIME_INVALID")
    if min_block_time is not None:
        require(block_time is not None and block_time >= min_block_time, "PAYMENT_TIME_UNVERIFIABLE_OR_TOO_OLD")
    return {"schemaVersion": 1, "verified": True, "network": "solana-mainnet-beta", "asset": "USDC",
            "mint": USDC_MINT, "recipient": recipient, "signature": signature,
            "amountAtomic": str(delta), "decimals": 6, "slot": slot, "blockTime": block_time,
            "finalized": True, "tokenAccountChanges": changes,
            "receiptKey": "solana:" + signature + ":" + USDC_MINT + ":" + recipient,
            "evidenceSha256": sha256(canonical_json(evidence)),
            "attribution": "unattributed_wallet_net_credit", "ledgerRecorded": False}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FulfillmentError("RPC_REDIRECT_REJECTED")


def rpc_call(endpoint, method, params):
    """Only three explicitly read-only RPC methods. Error strings omit URLs/tokens."""
    require(method in {"getGenesisHash", "getTransaction", "getSignatureStatuses"}, "RPC_METHOD_UNSUPPORTED")
    try:
        parsed = urllib.parse.urlsplit(endpoint)
    except (TypeError, ValueError):
        raise FulfillmentError("HTTPS_RPC_REQUIRED") from None
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.fragment, "HTTPS_RPC_REQUIRED")
    payload = canonical_json({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json",
                                      "User-Agent": "solana-usdc-receipt/1.0"}, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=20) as response:
            require(response.status == 200, "RPC_HTTP_ERROR")
            raw = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        raise FulfillmentError("RPC_UNAVAILABLE") from None
    reply = decode_json(raw)
    require(type(reply) is dict and reply.get("jsonrpc") == "2.0"
            and type(reply.get("id")) is int and reply["id"] == 1, "RPC_ENVELOPE_INVALID")
    require("error" not in reply and "result" in reply, "RPC_REPORTED_ERROR")
    return reply["result"]


def check_payment(recipient, amount, signature, endpoint=DEFAULT_RPC, *, min_block_time=None, rpc=rpc_call):
    _base58(recipient, 32, "RECIPIENT_INVALID")
    _base58(signature, 64, "SIGNATURE_INVALID")
    atomic_amount(amount)
    if min_block_time is not None:
        _integer(min_block_time, "MIN_BLOCK_TIME_INVALID")
    genesis = rpc(endpoint, "getGenesisHash", [])
    require(genesis == MAINNET_GENESIS, "WRONG_NETWORK")
    transaction = rpc(endpoint, "getTransaction", [signature, {"commitment": "finalized",
                      "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    require(transaction is not None, "TRANSACTION_UNAVAILABLE")
    statuses = rpc(endpoint, "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
    require(type(statuses) is dict and type(statuses.get("value")) is list
            and len(statuses["value"]) == 1, "SIGNATURE_RESPONSE_INVALID")
    result = verify_receipt({"genesisHash": genesis, "transaction": transaction,
                            "signatureStatus": statuses["value"][0]}, recipient, amount, signature,
                            min_block_time=min_block_time)
    result["evidenceSource"] = "caller_selected_https_rpc"
    return result


def example_evidence():
    """Synthetic addresses/signature, never a real wallet or payment claim."""
    recipient, sender, account, signature = "1" * 32, "1" * 31 + "2", "1" * 31 + "3", "1" * 64
    def balance(index, owner, amount):
        return {"accountIndex": index, "mint": USDC_MINT, "owner": owner, "programId": TOKEN_PROGRAM,
                "uiTokenAmount": {"amount": str(amount), "decimals": 6, "uiAmount": None}}
    evidence = {"genesisHash": MAINNET_GENESIS, "signatureStatus": {"slot": 123, "err": None,
                "confirmationStatus": "finalized"}, "transaction": {"slot": 123, "blockTime": 1700000000,
                "version": 0, "transaction": {"signatures": [signature], "message": {
                "accountKeys": [{"pubkey": key} for key in (sender, account)]}}, "meta": {
                "err": None, "preBalances": [3000000, 2039280], "postBalances": [2995000, 2039280],
                "preTokenBalances": [balance(1, recipient, 2000000)],
                "postTokenBalances": [balance(1, recipient, 3250000)]}}}
    return {"recipient": recipient, "amount": "1.25", "signature": signature, "evidence": evidence}


def self_test():
    sample = example_evidence()
    verify_receipt(sample["evidence"], sample["recipient"], sample["amount"], sample["signature"])
    cases = []
    wrong = copy.deepcopy(sample); wrong["evidence"]["genesisHash"] = "devnet"; cases.append(wrong)
    wrong = copy.deepcopy(sample); wrong["evidence"]["signatureStatus"]["confirmationStatus"] = "confirmed"; cases.append(wrong)
    wrong = copy.deepcopy(sample); wrong["evidence"]["transaction"]["meta"]["err"] = {"InstructionError": [0, "InvalidArgument"]}; cases.append(wrong)
    wrong = copy.deepcopy(sample); wrong["amount"] = "1.250001"; cases.append(wrong)
    wrong = copy.deepcopy(sample); wrong["recipient"] = "1" * 31 + "4"; cases.append(wrong)
    wrong = copy.deepcopy(sample); wrong["evidence"]["transaction"]["meta"]["postTokenBalances"][0]["mint"] = "1" * 32; cases.append(wrong)
    for case in cases:
        try:
            verify_receipt(case["evidence"], case["recipient"], case["amount"], case["signature"])
        except FulfillmentError:
            pass
        else:
            raise FulfillmentError("NEGATIVE_CONFORMANCE_FAILED")
    return {"passed": True, "examples": 1 + len(cases), "liveReceipt": False}


def validate_spec(spec):
    require(type(spec) is dict and set(spec) == {"schema_version", "mode", "operation", "parameters"}, "SPEC_KEYS_INVALID")
    require(type(spec["schema_version"]) is int and spec["schema_version"] == 1, "SPEC_VERSION_INVALID")
    require(spec["mode"] == "source_package" and spec["operation"] == OPERATION
            and type(spec["parameters"]) is dict and not spec["parameters"], "SPEC_UNSUPPORTED")
    return spec


def spec_digest(spec):
    return sha256(canonical_json(validate_spec(spec)))


def _confined(root, relative, must_exist=True):
    require(type(relative) is str and not any(c in relative for c in "\\:\0") and not relative.startswith("/"), "ARTIFACT_PATH_INVALID")
    path = root
    for component in relative.split("/"):
        require(component not in {"", ".", ".."}, "ARTIFACT_PATH_INVALID")
        path /= component
        require(not path.is_symlink() and path.resolve().is_relative_to(root), "ARTIFACT_PATH_ESCAPE")
    if must_exist:
        require(path.is_file() and path.stat().st_nlink == 1, "ARTIFACT_FILE_INVALID")
    return path


def _read(path):
    require(path.stat().st_size <= MAX_BYTES, "ARTIFACT_TOO_LARGE")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "ARTIFACT_TOO_LARGE")
    return raw


def _source_files(spec):
    sample = example_evidence()
    readme = '''# Native Solana USDC receipt checker

Python 3.10+; standard library only. This utility reads an existing transaction.
It does not create, send, sign, simulate or fund a transaction or maintain a ledger.

Run the included offline tests: `python -m unittest -v test_contract.py`
Run the synthetic fixture: `python fulfill.py fixture input.data`
Check your own payment (replace every placeholder; amount is decimal USDC text):

    python fulfill.py check --recipient WALLET --amount 1.25 --signature SIGNATURE
    python fulfill.py check --recipient WALLET --amount 1.25 --signature SIGNATURE --rpc-url https://YOUR-RPC
    python fulfill.py check --recipient WALLET --amount 1.25 --signature SIGNATURE --min-block-time 1700000000

Only an HTTPS RPC URL is accepted; HTTP redirects are rejected. Prefer supplying
an authenticated endpoint through SOLANA_RPC_URL instead of a shell argument.
The endpoint is never echoed. The default public mainnet-beta endpoint is rate
limited; this program cannot guarantee provider availability. It makes at most
three read-only calls with 20-second I/O timeouts and 4-MiB response limits.

Output is one JSON object. Exit 0 means the expected net wallet credit was
verified against that RPC's reported finalized state. Exit 2 emits verified:false
and a stable error code for mismatch, unknown history, malformed data or RPC failure.
Retry unavailable/unfinalized transactions later; a missing history record is
not proof that no payment occurred. Offline fixture success says only that the
supplied evidence matched; it is not independently fetched blockchain proof.

Checks: mainnet genesis, successful transaction, matching finalized signature
status and slot, exact signature, native USDC mint, SPL Token program, six decimals,
token-account owner, and exact aggregate net USDC increase for the wallet across
all token accounts involved. Decimal amounts never use binary floats. New token
accounts require zero pre-transaction lamports when pre-token balance is absent;
closed accounts similarly require zero post-lamports. Ownership/mint changes,
missing required metadata and unsupported transaction versions fail explicitly.
Legacy and version-0 jsonParsed transactions are supported. This checks net
wallet credit: simultaneous outgoing transfers are subtracted, so gross incoming
amounts may not match. Overpayment also fails an exact-amount check.
Optional --min-block-time is an inclusive UNIX-seconds cutoff. With that setting,
an absent blockTime or an older transaction fails. Without it, blockTime may be
null; finality and wallet credit still require all the other checks.

Trust the chosen RPC operator; this is an RPC-backed verifier, not a light client
or a cryptographic proof of consensus. It does not identify the payer, match an
invoice, prove a job was completed, or prevent a merchant from crediting one
receipt twice. Persist receiptKey atomically in your application's ledger before
crediting an order; use one unique receiptKey only once, bind it to an order, and
apply your own payer/invoice rules. receiptKey includes chain, transaction, native
mint and recipient. No fiat valuation is made. No runtime inputs are published.

The synthetic sample is not an actual payment. This source package contains no
customer transaction, endpoint credential, buyer brief, private data or wallet.
Source generation and tests run entirely offline. Buyer selects runtime parameters;
custom features, automatic revisions and arbitrary code repair are not included.

Protocol references (checked 2026-09-23):
- https://solana.com/docs/rpc/http/gettransaction
- https://solana.com/docs/rpc/http/getsignaturestatuses
- https://solana.com/docs/rpc/http/getgenesishash
- https://solana.com/docs/rpc/json-structures
- https://github.com/solana-labs/solana/blob/master/sdk/src/genesis_config.rs
- https://help.circle.com/support/en/usdc-supported-blockchains-minting-redemption-faqs?id=kb_article_view&sysparm_article=KB0010590
'''
    tests = '''import copy
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
'''
    contract = {"schema_version": 1, "operation": OPERATION, "runtime_inputs": ["recipient", "amount", "signature", "rpc_url", "min_block_time"],
                "network": "solana-mainnet-beta", "mint": USDC_MINT, "exact_net_amount": True,
                "read_only": True, "requires_model": False, "limits": {"rpc_response_bytes": MAX_BYTES, "rpc_timeout_seconds": 20}}
    return {"fulfill.py": _read(Path(__file__).resolve()), "README.md": readme.encode(),
            "test_contract.py": tests.encode(), "contract.json": canonical_json(contract) + b"\n",
            "package-spec.json": canonical_json(spec) + b"\n", "input.data": canonical_json(sample) + b"\n"}


def verify_manifest(manifest, job_root):
    root = Path(job_root).resolve(strict=True)
    require(type(manifest) is dict and manifest.get("engine_version") == ENGINE_VERSION
            and manifest.get("validation", {}).get("passed") is True, "MANIFEST_INVALID")
    digest = manifest.get("spec_sha256")
    require(type(digest) is str and re.fullmatch("[a-f0-9]{64}", digest), "MANIFEST_HASH_INVALID")
    prefix = "artifacts/" + digest + "/"
    spec = decode_json(_read(_confined(root, prefix + "specification.json")))
    require(spec_digest(spec) == digest and manifest.get("mode") == spec["mode"]
            and manifest.get("operation") == OPERATION, "MANIFEST_SPEC_MISMATCH")
    expected = {prefix + "specification.json", prefix + "source-package.zip"} | {prefix + "package/" + n for n in PACKAGE_NAMES}
    artifacts = manifest.get("artifacts")
    require(type(artifacts) is list and len(artifacts) == len(expected)
            and all(type(item) is dict for item in artifacts)
            and {item.get("path") for item in artifacts} == expected, "ARTIFACT_INVENTORY_INVALID")
    for item in artifacts:
        raw = _read(_confined(root, item["path"]))
        require(sha256(raw) == item.get("sha256") and len(raw) == item.get("size_bytes"), "ARTIFACT_HASH_MISMATCH")
    return manifest


def execute_spec(spec, job_root):
    digest = spec_digest(spec)
    root = Path(job_root)
    require(not root.is_symlink() and root.is_dir(), "WORKSPACE_INVALID")
    root = root.resolve(strict=True)
    output = _confined(root, "artifacts", must_exist=False)
    output.mkdir(exist_ok=True)
    final = _confined(root, "artifacts/" + digest, must_exist=False)
    if final.exists():
        return verify_manifest(decode_json(_read(_confined(root, "artifacts/" + digest + "/manifest.json"))), root)
    conformance = self_test()
    package = _source_files(spec)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(package.items()):
            item = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            item.external_attr = 0o100644 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, raw)
    files = {"specification.json": canonical_json(spec) + b"\n", "source-package.zip": stream.getvalue()}
    files.update({"package/" + name: raw for name, raw in package.items()})
    require(sum(map(len, files.values())) <= MAX_BYTES, "PACKAGE_TOO_LARGE")
    with zipfile.ZipFile(io.BytesIO(files["source-package.zip"])) as archive:
        require(set(archive.namelist()) == PACKAGE_NAMES and all(archive.read(n) == raw for n, raw in package.items()), "ZIP_MISMATCH")
    prefix = "artifacts/" + digest + "/"
    artifacts = [{"path": prefix + name, "sha256": sha256(raw), "size_bytes": len(raw),
                  "media_type": "application/octet-stream"} for name, raw in sorted(files.items())]
    manifest = {"schema_version": 1, "engine_version": ENGINE_VERSION, "spec_sha256": digest,
                "mode": spec["mode"], "operation": OPERATION, "artifacts": artifacts,
                "metrics": {"source_files": len(package), "conformance_examples": conformance["examples"]},
                "validation": {"passed": True, "checks": ["strict_spec", "offline_synthetic_conformance", "zip_byte_identity"],
                "limitations": ["Offline tests are not live payment evidence or buyer acceptance."]}}
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=output))
    try:
        for name, raw in {**files, "manifest.json": canonical_json(manifest) + b"\n"}.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        stage.rename(final)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return verify_manifest(manifest, root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--recipient", required=True)
    check.add_argument("--amount", required=True)
    check.add_argument("--signature", required=True)
    check.add_argument("--rpc-url", default=os.environ.get("SOLANA_RPC_URL", DEFAULT_RPC))
    check.add_argument("--min-block-time", type=int)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("path")
    sub.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            result = check_payment(args.recipient, args.amount, args.signature, args.rpc_url, min_block_time=args.min_block_time)
        elif args.command == "fixture":
            sample = decode_json(_read(_confined(Path.cwd().resolve(), args.path)))
            require(type(sample) is dict and set(sample) == {"recipient", "amount", "signature", "evidence"}, "FIXTURE_INVALID")
            result = verify_receipt(sample["evidence"], sample["recipient"], sample["amount"], sample["signature"])
            result["evidenceSource"] = "caller_supplied_offline_fixture"
            result["liveVerified"] = False
        else:
            result = self_test()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (FulfillmentError, OSError) as error:
        print(json.dumps({"verified": False, "error": str(error) if isinstance(error, FulfillmentError) else "FILE_IO_ERROR"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
