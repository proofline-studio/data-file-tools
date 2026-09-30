# Base USDC balance CLI

Read a wallet's native USDC balance on Base with Python 3.10 or newer. This
utility uses `requests` and does not need a wallet, private key or account.
It sends only `eth_chainId` and a read-only `eth_call` for `balanceOf(address)`.

Install the dependency in your own environment:

```sh
python -m pip install 'requests>=2.32.2,<3'
```

Run with an address you want to inspect:

```sh
python base_usdc_balance.py 0x0000000000000000000000000000000000000000
python base_usdc_balance.py 0x0000000000000000000000000000000000000000 --json
```

The zero address above is a usage example, not a suggested receiving address.
Default output has six decimal places, for example `1.234567 USDC on Base (...)`.
JSON includes `balance_atomic` as an exact integer string, `balance_usdc` as an
exact decimal string, network, token address, observation time and RPC URL.
The program never uses floating-point arithmetic for token amounts.

The fixed public RPC is `https://mainnet.base.org`, chain ID 8453. The native
USDC token is `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, with six decimals.
The balance is read at `latest`; it is not a finalized payment receipt or proof
that a specific job was paid. Address syntax is validated, but mixed-case
EIP-55 checksums are not checked. No other networks or tokens are accepted.

Exit status is 0 on success, 1 on an RPC/validation error and 2 for invalid CLI
arguments. Errors are concise codes on stderr, such as `rpc_wrong_network`,
`rpc_timeout`, `rpc_application_error` or `invalid_wallet_address`; remote error
messages are not echoed. Retry later if the public RPC is unavailable or rate
limited. The program does not automatically retry.

Connections use a validated public DNS address, pinned for the session, with
TLS verification of the original hostname. Environment proxies, redirects,
credentials and compressed responses are disabled. Default timeouts are three
seconds for connecting and ten seconds for each read operation; streaming time
and response size are checked as well. These are operation timeouts, not a
guaranteed process wall-clock limit. Responses larger than 4 KiB are rejected.

Run the offline tests:

```sh
python -B -m unittest test_base_usdc_balance -v
```

Trusted applications can call `fetch_balance(address, network_guard=...)` to
check an external network/VPN policy before each RPC. The optional test session
factory is not exposed through the CLI. No marketplace registration, claim,
delivery, payment collection or live job commitment is part of this package.
