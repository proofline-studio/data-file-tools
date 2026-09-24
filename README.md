# Native Solana USDC receipt checker

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
