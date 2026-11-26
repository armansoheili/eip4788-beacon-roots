"""EIP-4788 beacon block root oracle simulator (pure Python).

EIP-4788 (Cancun) exposes the beacon chain's block roots inside the EVM
through a system contract at 0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02.
The contract keeps a ring buffer of the last HISTORY_BUFFER_LENGTH roots,
indexed by ``timestamp % HISTORY_BUFFER_LENGTH``; a root is only available
while its slot has not been overwritten, i.e. for about 8191 blocks
(~27 hours at 12-second slots).

This module simulates the beacon side (a chain of blocks with deterministic
roots) and the oracle side (the ring-buffer contract), so you can see
exactly when a root is retrievable and when it has expired.

Run the demo:

    $ python beacon_roots.py --demo

Run the doctests:

    $ python beacon_roots.py --test
"""

from __future__ import annotations

import argparse
import hashlib
import sys

__all__ = [
    "HISTORY_BUFFER_LENGTH",
    "SLOT_DURATION",
    "ZERO_ROOT",
    "ORACLE_ADDRESS",
    "block_root",
    "root_hex",
    "BeaconChain",
    "BeaconRootsOracle",
]

#: Number of roots the EIP-4788 ring buffer keeps (2**13 - 1).
HISTORY_BUFFER_LENGTH = 8191

#: Beacon chain slot duration in seconds.
SLOT_DURATION = 12

#: The 32 zero bytes the oracle returns for an unknown/expired timestamp.
ZERO_ROOT = b"\x00" * 32

#: Address of the EIP-4788 beacon roots contract.
ORACLE_ADDRESS = "0x000F3df6D732807Ef1319fB7B8bB8522d0Beac02"


def block_root(parent_root, slot, timestamp, proposer_index=0):
    """Deterministic beacon block root for a block.

    The real root is the hash_tree_root of the beacon block header; here we
    model it as SHA-256 over the parent root and the block's slot,
    timestamp and proposer, which keeps the chaining property that matters
    for the oracle: every root commits to its parent.

    >>> r = block_root(ZERO_ROOT, 0, 0)
    >>> len(r)
    32
    >>> block_root(ZERO_ROOT, 0, 0) == block_root(ZERO_ROOT, 0, 0)
    True
    >>> block_root(ZERO_ROOT, 0, 0) == block_root(ZERO_ROOT, 1, 12)
    False
    """
    material = (
        bytes(parent_root)
        + int(slot).to_bytes(8, "big")
        + int(timestamp).to_bytes(8, "big")
        + int(proposer_index).to_bytes(8, "big")
    )
    return hashlib.sha256(material).digest()


def root_hex(root):
    """Render a 32-byte root as a ``0x``-prefixed hex string.

    >>> root_hex(ZERO_ROOT)
    '0x0000000000000000000000000000000000000000000000000000000000000000'
    """
    return "0x" + bytes(root).hex()


class BeaconChain:
    """A minimal beacon chain producing one block per 12-second slot.

    >>> chain = BeaconChain(genesis_timestamp=1700000000)
    >>> b0 = chain.propose(proposer_index=7)
    >>> b0["slot"]
    0
    >>> b0["timestamp"]
    1700000000
    >>> chain.propose()["slot"]
    1
    >>> chain.tip_root() == chain.blocks[-1]["root"]
    True
    """

    def __init__(self, genesis_timestamp=0):
        self.genesis_timestamp = int(genesis_timestamp)
        self.blocks = []

    def propose(self, proposer_index=0):
        """Append the next block and return its header dict."""
        slot = len(self.blocks)
        timestamp = self.genesis_timestamp + slot * SLOT_DURATION
        parent = self.blocks[-1]["root"] if self.blocks else ZERO_ROOT
        root = block_root(parent, slot, timestamp, proposer_index)
        block = {
            "slot": slot,
            "timestamp": timestamp,
            "proposer_index": int(proposer_index),
            "parent_root": parent,
            "root": root,
        }
        self.blocks.append(block)
        return block

    def tip_root(self):
        """Root of the latest block (zero before any block is proposed)."""
        return self.blocks[-1]["root"] if self.blocks else ZERO_ROOT


class BeaconRootsOracle:
    """The EIP-4788 ring-buffer contract.

    ``set`` is what the system call does at the start of every execution
    block: it stores the parent beacon block's root under its timestamp.
    ``get`` is the contract's ``get(timestamp)`` view: it returns the root
    only if the exact timestamp is still in the buffer.

    >>> oracle = BeaconRootsOracle()
    >>> chain = BeaconChain(genesis_timestamp=1000)
    >>> for _ in range(3):
    ...     b = chain.propose()
    ...     oracle.set(b["timestamp"], b["root"])
    >>> oracle.get(1000) == chain.blocks[0]["root"]
    True
    >>> oracle.get(988) == ZERO_ROOT  # never stored
    True
    >>> oracle.get(1000 + 12 * 8191) == ZERO_ROOT  # future timestamp
    True
    """

    def __init__(self):
        # slot index -> (timestamp, root)
        self._buffer = {}

    @staticmethod
    def _index(timestamp):
        return int(timestamp) % HISTORY_BUFFER_LENGTH

    def set(self, timestamp, root):
        """Store ``root`` for ``timestamp`` (overwrites the old occupant)."""
        if len(root) != 32:
            raise ValueError("root must be 32 bytes")
        self._buffer[self._index(timestamp)] = (int(timestamp), bytes(root))

    def get(self, timestamp):
        """Return the root stored for ``timestamp``, else ``ZERO_ROOT``."""
        entry = self._buffer.get(self._index(timestamp))
        if entry is None or entry[0] != int(timestamp):
            return ZERO_ROOT
        return entry[1]

    def is_available(self, timestamp):
        """True when ``get(timestamp)`` returns a real (non-zero) root.

        >>> oracle = BeaconRootsOracle()
        >>> oracle.set(1000, block_root(ZERO_ROOT, 0, 1000))
        >>> oracle.is_available(1000)
        True
        >>> oracle.is_available(1012)
        False
        """
        return self.get(timestamp) != ZERO_ROOT

    def blocks_until_expiry(self, timestamp, now):
        """Whole beacon blocks before ``timestamp``'s root is overwritten.

        The root lives in the buffer for HISTORY_BUFFER_LENGTH blocks after
        it is stored; returns 0 once it is already gone.

        >>> oracle = BeaconRootsOracle()
        >>> oracle.blocks_until_expiry(1000, now=1000)
        8191
        >>> oracle.blocks_until_expiry(1000, now=1000 + 12 * 8191)
        0
        >>> oracle.blocks_until_expiry(1000, now=1000 + 12 * 100)
        8091
        """
        age_blocks = (int(now) - int(timestamp)) // SLOT_DURATION
        remaining = HISTORY_BUFFER_LENGTH - age_blocks
        return max(0, remaining)

    def stored_count(self):
        """Number of distinct timestamps currently in the buffer."""
        return len(self._buffer)


def _demo():
    chain = BeaconChain(genesis_timestamp=1710338135)
    oracle = BeaconRootsOracle()
    print("EIP-4788 beacon roots oracle demo")
    print(f"oracle contract: {ORACLE_ADDRESS}")
    print(f"ring buffer length: {HISTORY_BUFFER_LENGTH} roots (~27.3 h)")
    for i in range(3):
        b = chain.propose(proposer_index=1000 + i)
        oracle.set(b["timestamp"], b["root"])
        print(f"slot {b['slot']:>3} ts {b['timestamp']} root "
              f"{root_hex(b['root'])[:18]}...")
    ts = chain.blocks[0]["timestamp"]
    print(f"get({ts}) -> {root_hex(oracle.get(ts))[:18]}... (still available)")
    print(f"get({ts - 12}) -> zero root (never stored): "
          f"{oracle.get(ts - 12) == ZERO_ROOT}")
    for _ in range(HISTORY_BUFFER_LENGTH):
        b = chain.propose()
        oracle.set(b["timestamp"], b["root"])
    print(f"after {HISTORY_BUFFER_LENGTH} more blocks, "
          f"get({ts}) -> zero root: {oracle.get(ts) == ZERO_ROOT}")
    print(f"buffer holds {oracle.stored_count()} roots")


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="run the demo")
    parser.add_argument("--test", action="store_true", help="run doctests")
    args = parser.parse_args(argv)
    if args.test:
        import doctest

        failures, tests = doctest.testmod(verbose=False)
        print(f"{tests} doctests, {failures} failures")
        return 1 if failures else 0
    _demo()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
