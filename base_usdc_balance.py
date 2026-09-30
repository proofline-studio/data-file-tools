"""Read a Base wallet's native USDC balance; no key, signing or transaction.

Python 3.10+, requests>=2.32.2,<3. Only eth_chainId and eth_call(balanceOf)
are sent to https://mainnet.base.org. This is a standalone buyer deliverable;
marketplace acquisition, funding, delivery and payment are separate operations.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import math
from pathlib import Path
import queue
import re
import socket
import sys
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3 import HTTPSConnectionPool

RPC_URL = "https://mainnet.base.org"
RPC_HOST = "mainnet.base.org"
CHAIN_ID = 8453
USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
DECIMALS = 6
MAX_RESPONSE_BYTES = 4096
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
QUANTITY = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z")
WORD = re.compile(r"0x[0-9a-fA-F]{64}\Z")


class BalanceError(Exception):
    """Finite diagnostic code; remote bodies and exception text are not exposed."""


def validate_address(address: str) -> str:
    if not isinstance(address, str) or not ADDRESS.fullmatch(address):
        raise BalanceError("invalid_wallet_address")
    # Syntax validation, not a claim of EIP-55 checksum verification.
    return address.lower()


def _public_addresses(timeout_seconds: float) -> list[str]:
    results = queue.Queue(maxsize=1)

    def lookup():
        try:
            results.put(socket.getaddrinfo(RPC_HOST, 443, type=socket.SOCK_STREAM))
        except OSError:
            results.put(None)

    threading.Thread(target=lookup, daemon=True).start()
    try:
        rows = results.get(timeout=min(2.0, timeout_seconds))
    except queue.Empty:
        raise BalanceError("rpc_dns_timeout") from None
    if not rows or len(rows) > 32:
        raise BalanceError("rpc_dns_unavailable")
    addresses = []
    for family, kind, _, _, sockaddr in rows:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM or sockaddr[1] != 443:
            raise BalanceError("rpc_dns_invalid")
        try:
            value = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise BalanceError("rpc_dns_invalid") from None
        if not value.is_global or value.is_multicast or value.is_reserved:
            raise BalanceError("rpc_dns_nonpublic")
        addresses.append(str(value))
    # Prefer IPv4 when both families are available; exactly one connection,
    # no retry through another address after an uncertain network failure.
    return sorted(set(addresses), key=lambda value: (ipaddress.ip_address(value).version, value))


class _PinnedAdapter(HTTPAdapter):
    """Connect to one validated public IP while validating the original TLS host."""

    def __init__(self, address: str):
        super().__init__(max_retries=0)
        self._pool = HTTPSConnectionPool(address, port=443, maxsize=1,
                                         assert_hostname=RPC_HOST,
                                         server_hostname=RPC_HOST,
                                         cert_reqs="CERT_REQUIRED")

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        if request.url not in (RPC_URL, RPC_URL + "/") or verify is not True or proxies or cert:
            raise BalanceError("rpc_transport_configuration_invalid")
        return self._pool

    def close(self):
        self._pool.close()
        super().close()


def _session(timeout_seconds: float):
    session = requests.Session()
    session.trust_env = False
    session.auth = None
    session.proxies.clear()
    session.headers.clear()
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json",
                            "Accept-Encoding": "identity", "Host": RPC_HOST,
                            "User-Agent": "base-usdc-balance/1.0"})
    session.mount("https://", _PinnedAdapter(_public_addresses(timeout_seconds)[0]))
    return session


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise BalanceError("rpc_duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_):
    raise BalanceError("rpc_invalid_json")


def _rpc(session, method: str, params: list, request_id: int, timeout_seconds: float):
    if (method, request_id) not in (("eth_chainId", 1), ("eth_call", 2)):
        raise BalanceError("rpc_method_not_allowed")
    if method == "eth_chainId" and params != []:
        raise BalanceError("rpc_params_not_allowed")
    if method == "eth_call":
        if (not isinstance(params, list) or len(params) != 2 or params[1] != "latest"
                or not isinstance(params[0], dict) or set(params[0]) != {"to", "data"}
                or params[0]["to"] != USDC_CONTRACT
                or not re.fullmatch(r"0x70a08231[0]{24}[0-9a-f]{40}", params[0].get("data", ""))):
            raise BalanceError("rpc_params_not_allowed")
    started = time.monotonic()
    try:
        with session.post(RPC_URL, json={"jsonrpc": "2.0", "id": request_id,
                                        "method": method, "params": params},
                          allow_redirects=False, stream=True, verify=True,
                          timeout=(min(3.0, timeout_seconds), timeout_seconds)) as response:
            if response.status_code != 200:
                raise BalanceError("rpc_http_status_" + str(response.status_code))
            if response.headers.get("Content-Encoding", "identity").strip().lower() not in ("", "identity"):
                raise BalanceError("rpc_compressed_response")
            if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise BalanceError("rpc_response_not_json")
            length = response.headers.get("Content-Length")
            if length is not None and (not re.fullmatch(r"[0-9]{1,10}", length) or int(length) > MAX_RESPONSE_BYTES):
                raise BalanceError("rpc_response_too_large")
            body = bytearray()
            # A one-byte chunk checks elapsed time even for a slowly trickled
            # body. With this 4 KiB cap, the loop remains small.
            for piece in response.iter_content(chunk_size=1):
                if time.monotonic() - started > timeout_seconds:
                    raise BalanceError("rpc_read_deadline")
                body.extend(piece)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise BalanceError("rpc_response_too_large")
            try:
                data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant)
            except (ValueError, UnicodeError):
                raise BalanceError("rpc_invalid_json") from None
    except requests.exceptions.Timeout:
        raise BalanceError("rpc_timeout") from None
    except requests.exceptions.RequestException:
        raise BalanceError("rpc_network_error") from None
    if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or type(data.get("id")) is not int or data["id"] != request_id:
        raise BalanceError("rpc_envelope_invalid")
    if "error" in data:
        raise BalanceError("rpc_application_error")
    if set(data) != {"jsonrpc", "id", "result"}:
        raise BalanceError("rpc_envelope_invalid")
    return data["result"]


def fetch_balance(address: str, *, timeout_seconds: float = 10.0,
                  session_factory=None, network_guard=None) -> dict:
    """Return the latest native Base USDC balance without transacting.

    session_factory and network_guard are trusted application/test injection,
    never buyer input. The CLI exposes neither URLs nor methods nor credentials.
    Network timeouts apply per operation; response size and streaming time are
    additionally bounded. A balance is an observation, not a payment receipt.
    """
    wallet = validate_address(address)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
        raise BalanceError("invalid_timeout")
    guard = network_guard if network_guard is not None else (lambda: None)
    if not callable(guard) or (session_factory is not None and not callable(session_factory)):
        raise BalanceError("invalid_configuration")
    guard()
    with (session_factory or _session)(float(timeout_seconds)) as session:
        guard()
        chain = _rpc(session, "eth_chainId", [], 1, timeout_seconds)
        if not isinstance(chain, str) or not QUANTITY.fullmatch(chain) or len(chain) > 66:
            raise BalanceError("rpc_chain_id_invalid")
        if int(chain, 16) != CHAIN_ID:
            raise BalanceError("rpc_wrong_network")
        guard()
        value = _rpc(session, "eth_call", [{"to": USDC_CONTRACT,
                      "data": "0x70a08231" + "0" * 24 + wallet[2:]}, "latest"], 2, timeout_seconds)
    if not isinstance(value, str) or not WORD.fullmatch(value):
        raise BalanceError("rpc_balance_invalid")
    units = int(value, 16)
    whole, fraction = divmod(units, 10 ** DECIMALS)
    return {"wallet": wallet, "chain_id": CHAIN_ID, "network": "Base",
            "token": USDC_CONTRACT, "symbol": "USDC", "decimals": DECIMALS,
            "balance_atomic": str(units), "balance_usdc": f"{whole}.{fraction:06d}",
            "rpc_url": RPC_URL, "block_tag": "latest",
            "observed_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read a wallet's native USDC balance on Base. No private key required.")
    parser.add_argument("address", help="0x-prefixed 20-byte wallet address (syntax validated)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        result = fetch_balance(args.address)
    except BalanceError as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['balance_usdc']} USDC on Base ({result['wallet']})")
    return 0


def validate_parameters(parameters):
    """The agreed deliverable has no buyer-supplied code or network options."""
    if not isinstance(parameters, dict) or parameters:
        raise BalanceError("source_package_parameters_must_be_empty")


def source_files(parameters) -> dict[str, bytes]:
    """Build the standalone delivery from reviewed local files, with no RPC call."""
    validate_parameters(parameters)
    directory = Path(__file__).resolve().parent
    names = ("base_usdc_balance.py", "test_base_usdc_balance.py",
             "requirements-base-usdc-balance.json")
    result = {name: (directory / name).read_bytes() for name in names}
    readme = directory / "README-base-usdc-balance.md"
    # The delivered standalone package uses the publisher's standard README
    # filename. This also lets its offline package test rerun after extraction.
    if not readme.is_file():
        readme = directory / "README.md"
    result["README.md"] = readme.read_bytes()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
