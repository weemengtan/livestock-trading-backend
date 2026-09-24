
## Audit log integrity

`audit_log` is append-only and tamper-evident:

- **Append-only in the database.** A trigger rejects `UPDATE`, `DELETE` and `TRUNCATE` (migration `c8e2f4a6b1d3`), even for the table owner.
- **Production role.** Run the app as a role that does not own the tables and is denied those privileges on `audit_log`: `scripts/create_app_role.sql` (run once as the owner; not part of migrations). That role also cannot disable the trigger.
- **Hash chain.** Every entry stores the previous entry's hash and its own, written in the same transaction as the change it records. Verify with `uv run python -m scripts.verify_audit_chain` (exit 1 if broken) — schedule it and alert on failure.
- **Dev resets.** `scripts/wipe_business_data.py` is the one sanctioned way to clear the log, and only when `ENVIRONMENT=dev` (default is `prod`): as the table owner it disables the truncate trigger, wipes and re-arms it in a single transaction.

## Tests

Pure-logic unit tests (which DNBP model is live, Singapore activation dates) use the standard library — no test framework or database needed:

    .venv/bin/python -m unittest discover -s tests -t .
