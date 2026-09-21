"""Verify the audit log's hash chain (tamper-evidence check).

Recomputes every entry's hash in order and checks each links to its
predecessor. Exit code 0 = intact, 1 = broken (prints where and why).
Schedule this and alert on a non-zero exit.

Run with: uv run python -m scripts.verify_audit_chain
"""

import asyncio
import sys

from core.db import async_session_factory
from services import audit_service


async def main() -> int:
    async with async_session_factory() as session:
        result = await audit_service.verify_chain(session)
    print(
        f"audit_log: {result.total} rows — {result.verified} verified, "
        f"{result.unchained_legacy} legacy (written before the chain existed)"
    )
    if result.ok:
        print("Chain intact.")
        return 0
    print(f"CHAIN BROKEN at seq {result.first_bad_seq}: {result.problem}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
