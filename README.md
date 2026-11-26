# eip4788-beacon-roots

EIP-4788 beacon block root oracle simulator in pure Python (zero dependencies).

## What is EIP-4788?

EIP-4788 (Cancun) exposes the beacon chain's block roots inside the EVM
through a system contract at
`0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02`. At the start of every execution
block the contract stores the parent beacon block's root, keyed by its
timestamp. Smart contracts can then read a recent beacon root with
`get(timestamp)` — the building block for trust-minimized staking pools,
restaking, and light clients that verify consensus data on-chain.

## The catch: the ring buffer

The contract keeps only the last **8191** roots in a ring buffer indexed by
`timestamp % 8191`. A root is retrievable for about **8191 blocks (~27.3
hours)**; after that its slot is overwritten by a newer root and
`get(timestamp)` returns the zero root. Contracts that need a root must
consume it inside that window — or checkpoint it themselves.

## What this simulates

- `BeaconChain` — a minimal beacon chain, one block per 12-second slot,
  each root chaining to its parent (SHA-256 over parent root + slot +
  timestamp + proposer).
- `BeaconRootsOracle` — the EIP-4788 contract: `set(timestamp, root)`
  (the per-block system call), `get(timestamp)` (returns the root, or the
  32 zero bytes when the timestamp was never stored or has expired),
  `is_available(timestamp)`, and `blocks_until_expiry(timestamp, now)`.

## Usage

```bash
python beacon_roots.py --demo   # walk through storage, lookup and expiry
python beacon_roots.py --test   # run the doctests
```

```python
from beacon_roots import BeaconChain, BeaconRootsOracle, ZERO_ROOT

chain = BeaconChain(genesis_timestamp=1710338135)
oracle = BeaconRootsOracle()
for _ in range(3):
    b = chain.propose()
    oracle.set(b["timestamp"], b["root"])

oracle.get(1710338135) == chain.blocks[0]["root"]  # True
oracle.get(1710338123) == ZERO_ROOT                # never stored -> zero root
oracle.blocks_until_expiry(1710338135, now=1710338135)  # 8191
```

## Files

- `beacon_roots.py` — the simulator (with doctests and a `--demo` CLI)
