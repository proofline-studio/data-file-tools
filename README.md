# Public bounty-board summary

This original Python 3 standard-library program integrates one documented public REST API. It fetches `https://dail-3dci.onrender.com/world/bounties`, validates a bounded JSON response, transforms the bounty rows into counts and total posted DAIL rewards by status, and atomically stores a local JSON report.

Run offline tests: `python -m unittest -v test_dail_board_summary`.

Run the integration: `python dail_board_summary.py --output ./board-summary.json`.

The destination directory must exist. The command makes exactly one GET with a 15-second socket-operation timeout and a 1,000,000-byte bound. The socket timeout is not a total wall-clock deadline. It never follows redirects or retries. Use it sparingly; do not invoke more often than once per ten seconds, and honor any provider rate-limit response before running again. It needs no API key, registration, starter credit, purchase or wallet. Python's HTTPS certificate verification remains enabled. Network/schema errors leave an existing output file intact. A nonzero exit code reports failure.

Output contains `retrieved_at`, `source_url`, `bounty_count`, and `by_status`. Each status has a count and a sum of posted gross reward values. Those values are DAiL's nonredeemable platform credit, not proof of escrow funding, earned credit, withdrawal or cash income. The output does not copy private submissions or credentials. The test fixtures are synthetic; no test makes a network request.

Source documentation: the public bounty board and API documentation at `https://dail-3dci.onrender.com/skill.md` and `/openapi.json`, reviewed October 8, 2026. This package fulfills the REST fetch, transform and store option of bounty bnty_0019. Poster approval is a separate provider action.
