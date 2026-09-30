import contextlib
import io
import json
import socket
import unittest
from unittest.mock import patch

import requests

import base_usdc_balance as balance

WALLET = "0x" + "01" * 19 + "AB"


class Response:
    def __init__(self, request_id, result=None, *, body=None, status=200, headers=None):
        self.status_code = status
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.body = body if body is not None else json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}).encode()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def session_for(units, chain="0x2105"):
    return Session([Response(1, chain), Response(2, "0x" + f"{units:064x}")])


class BalanceTests(unittest.TestCase):
    def fetch(self, session, address=WALLET, **kwargs):
        return balance.fetch_balance(address, session_factory=lambda timeout: session, **kwargs)

    def test_exact_fraction_and_request_scope(self):
        session = session_for(123456789)
        guards = []
        result = self.fetch(session, network_guard=lambda: guards.append(True))
        self.assertEqual(result["balance_usdc"], "123.456789")
        self.assertEqual(result["balance_atomic"], "123456789")
        self.assertEqual(result["wallet"], WALLET.lower())
        self.assertEqual(result["chain_id"], 8453)
        self.assertEqual(result["decimals"], 6)
        self.assertTrue(result["observed_at"].endswith("+00:00"))
        self.assertEqual(len(guards), 3)
        self.assertTrue(session.closed)
        self.assertEqual([call[1]["json"]["method"] for call in session.calls], ["eth_chainId", "eth_call"])
        for url, options in session.calls:
            self.assertEqual(url, "https://mainnet.base.org")
            self.assertFalse(options["allow_redirects"])
            self.assertTrue(options["verify"])
            self.assertTrue(options["stream"])
            self.assertEqual(options["timeout"], (3.0, 10.0))
        payload = session.calls[1][1]["json"]
        self.assertEqual(payload["params"], [{"to": balance.USDC_CONTRACT,
                         "data": "0x70a08231" + "0" * 24 + WALLET[2:].lower()}, "latest"])

    def test_zero_one_atom_and_max_uint256_exact(self):
        expected = [(0, "0.000000"), (1, "0.000001"),
                    (2**256-1, "115792089237316195423570985008687907853269984665640564039457584007913129.639935")]
        for units, text in expected:
            with self.subTest(units=units):
                self.assertEqual(self.fetch(session_for(units))["balance_usdc"], text)

    def test_invalid_wallet_and_timeouts_make_no_request(self):
        session = session_for(0)
        for address in [None, "", "0x1", "0x" + "a"*41, "0x"+"g"*40, WALLET+"\n", "https://example.test"]:
            with self.subTest(address=address), self.assertRaisesRegex(balance.BalanceError, "invalid_wallet_address"):
                self.fetch(session, address)
        for value in [True, None, 0, -1, 31, float("inf"), float("nan")]:
            with self.subTest(timeout=value), self.assertRaisesRegex(balance.BalanceError, "invalid_timeout"):
                self.fetch(session, timeout_seconds=value)
        self.assertEqual(session.calls, [])

    def test_wrong_network_stops_before_balance(self):
        session = session_for(1, chain="0x1")
        with self.assertRaisesRegex(balance.BalanceError, "rpc_wrong_network"):
            self.fetch(session)
        self.assertEqual(len(session.calls), 1)
        for value in [8453, "0x02105", "0X2105", "0x", "0x"+"f"*65]:
            with self.subTest(value=value), self.assertRaisesRegex(balance.BalanceError, "rpc_chain_id_invalid"):
                self.fetch(session_for(1, chain=value))

    def test_balance_must_be_exact_abi_word(self):
        for value in ["0x1", "0x"+"f"*65, "0x"+"z"*64, 0, None, "0x"+"0"*64+"\n"]:
            with self.subTest(value=value), self.assertRaisesRegex(balance.BalanceError, "rpc_balance_invalid"):
                self.fetch(Session([Response(1, "0x2105"), Response(2, value)]))

    def test_rpc_envelope_and_duplicate_json_fail_closed(self):
        cases = [
            (b'{"jsonrpc":"2.0","id":true,"result":"0x2105"}', "rpc_envelope_invalid"),
            (b'{"jsonrpc":"2.0","id":2,"result":"0x2105"}', "rpc_envelope_invalid"),
            (b'{"jsonrpc":"2.0","id":1,"result":"0x2105","x":1}', "rpc_envelope_invalid"),
            (b'{"jsonrpc":"2.0","id":1,"error":{"message":"SECRET"}}', "rpc_application_error"),
            (b'{"jsonrpc":"2.0","id":1,"result":"0x2105","id":1}', "rpc_duplicate_json_key"),
            (b'{"jsonrpc":"2.0","id":1,"result":NaN}', "rpc_invalid_json"),
            (b'\xff', "rpc_invalid_json"), (b'[]', "rpc_envelope_invalid")]
        for body, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(balance.BalanceError, error):
                self.fetch(Session([Response(1, body=body)]))

    def test_http_redirects_compression_and_size_rejected(self):
        cases = [(Response(1, status=302), "rpc_http_status_302"),
                 (Response(1, headers={"Content-Type":"text/html"}), "rpc_response_not_json"),
                 (Response(1, headers={"Content-Encoding":"gzip"}), "rpc_compressed_response"),
                 (Response(1, headers={"Content-Length":"9"*5000}), "rpc_response_too_large"),
                 (Response(1, body=b'x'*4097), "rpc_response_too_large")]
        for response, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(balance.BalanceError, error):
                self.fetch(Session([response]))
            self.assertTrue(response.closed)

    def test_finite_network_errors_and_stream_deadline(self):
        for error, code in [(requests.exceptions.Timeout("secret"), "rpc_timeout"),
                            (requests.exceptions.ConnectionError("secret"), "rpc_network_error")]:
            with self.assertRaisesRegex(balance.BalanceError, "^" + code + "$"):
                self.fetch(Session([error]))
        with patch.object(balance.time, "monotonic", side_effect=[0, 20]), self.assertRaisesRegex(balance.BalanceError, "rpc_read_deadline"):
            self.fetch(session_for(0))

    def test_public_dns_validation_and_ipv4_preference(self):
        rows = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1111", 443, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]
        with patch.object(balance.socket, "getaddrinfo", return_value=rows):
            self.assertEqual(balance._public_addresses(1)[0], "1.1.1.1")
        for ip in ["127.0.0.1", "10.0.0.1", "169.254.169.254", "224.0.0.1", "192.0.2.1"]:
            rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]
            with self.subTest(ip=ip), patch.object(balance.socket, "getaddrinfo", return_value=rows), self.assertRaisesRegex(balance.BalanceError, "rpc_dns_nonpublic"):
                balance._public_addresses(1)

    def test_transport_pins_ip_tls_host_and_disables_environment(self):
        with patch.object(balance, "_public_addresses", return_value=["1.1.1.1"]):
            with balance._session(1) as session:
                self.assertFalse(session.trust_env)
                self.assertEqual(session.proxies, {})
                self.assertIsNone(session.auth)
                adapter = session.get_adapter(balance.RPC_URL)
                request = requests.Request("POST", balance.RPC_URL).prepare()
                pool = adapter.get_connection_with_tls_context(request, True)
                self.assertEqual(pool.host, "1.1.1.1")
                self.assertEqual(pool.assert_hostname, balance.RPC_HOST)
                self.assertEqual(pool.conn_kw["server_hostname"], balance.RPC_HOST)
                for verify, proxies, cert in [(False, None, None), (True, {"https":"http://proxy"}, None), (True, None, "cert")]:
                    with self.assertRaises(balance.BalanceError):
                        adapter.get_connection_with_tls_context(request, verify, proxies, cert)
                other = requests.Request("POST", "https://example.test").prepare()
                with self.assertRaises(balance.BalanceError):
                    adapter.get_connection_with_tls_context(other, True)

    def test_rpc_cannot_send_write_or_arbitrary_call(self):
        session = Session([])
        with self.assertRaisesRegex(balance.BalanceError, "rpc_method_not_allowed"):
            balance._rpc(session, "eth_sendTransaction", [], 1, 1)
        with self.assertRaisesRegex(balance.BalanceError, "rpc_params_not_allowed"):
            balance._rpc(session, "eth_call", [{"to":WALLET,"data":"0x"},"latest"], 2, 1)
        self.assertEqual(session.calls, [])

    def test_cli_json_success_and_finite_error_exit(self):
        result = self.fetch(session_for(1))
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(balance, "fetch_balance", return_value=result), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(balance.main([WALLET, "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["balance_usdc"], "0.000001")
        self.assertEqual(errors.getvalue(), "")
        with patch.object(balance, "fetch_balance", side_effect=balance.BalanceError("rpc_timeout")), contextlib.redirect_stderr(errors):
            self.assertEqual(balance.main([WALLET]), 1)
        self.assertEqual(errors.getvalue(), "error: rpc_timeout\n")

    def test_source_package_rejects_parameters_and_never_networks(self):
        with patch.object(balance, "fetch_balance", side_effect=AssertionError("no RPC in package")):
            files = balance.source_files({})
        self.assertEqual(set(files), {"base_usdc_balance.py", "test_base_usdc_balance.py",
                        "README.md", "requirements-base-usdc-balance.json"})
        self.assertEqual(json.loads(files["requirements-base-usdc-balance.json"])["dependencies"]["requests"], ">=2.32.2,<3")
        compile(files["base_usdc_balance.py"], "base_usdc_balance.py", "exec")
        for parameters in [None, [], {"address": WALLET}, {"url": "https://example.test"}]:
            with self.subTest(parameters=parameters), self.assertRaises(balance.BalanceError):
                balance.source_files(parameters)


if __name__ == "__main__":
    unittest.main()
