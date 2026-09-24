"""Bootstrap seed for reference data (§6): one active reference_data_versions
row (operational tunables and saleyard calendar), one live DNBP model (CIF
buffer, factors, standard weights), plus the open species/product_type
registries. In a real environment
this was a one-time job done by the phase3b_reference_data_and_registries
migration — a data migration only ever runs once, so it can't be used to
restore this after a dev DB reset. This script exists for exactly that:
reseeding reference data into an already-migrated, otherwise-empty DB.

The values come from a bootstrap JSON file YOU supply (pricing parameters and
saleyard arrangements are business-confidential and are not kept in this
repository). Its expected shape is documented in core/reference_seed.py.

Run with: uv run python -m scripts.seed_reference_data --file /path/to/bootstrap.json
"""

import argparse
import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

from core.db import async_session_factory
from core.reference_seed import load_seed, seed_entries
from models.dnbp_model import DnbpModel, DnbpModelSpecies
from models.enums import OrgKind, ReferenceDataTableKey
from models.reference_data import ProductTypeRegistry, ReferenceDataEntry, ReferenceDataVersion, SpeciesRegistry
from repositories import organisations as org_repo
from repositories import reference_data as reference_data_repo


async def main(seed_file: Path) -> None:
    async with async_session_factory() as session:
        existing = await reference_data_repo.get_active_version(session)
        if existing is not None:
            print(f"An active reference data version already exists ({existing.id}) — nothing to do.")
            return

        everhealth = await org_repo.get_by_kind(session, OrgKind.EVERHEALTH)
        if everhealth is None:
            raise RuntimeError("No Everhealth org found — run `uv run python -m scripts.seed` first.")

        data = load_seed(seed_file)

        version = ReferenceDataVersion(
            effective_from=datetime.combine(
                date.fromisoformat(data["effective_from"]), datetime.min.time(), tzinfo=UTC
            ),
            created_by=None,
            note="Initial seed (scripts/seed_reference_data.py)",
            is_active=True,
            activated_at=datetime.now(UTC),
            impact_previewed_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()

        dnbp_entries = []
        for table_key, key1, key2, value, text_value in seed_entries(data):
            if table_key in (
                ReferenceDataTableKey.CIF_BUFFER_PER_KG,
                ReferenceDataTableKey.DNBP_FACTOR,
                ReferenceDataTableKey.STANDARD_WEIGHT,
            ):
                dnbp_entries.append((table_key, key1, value))  # belongs to the DNBP model, not the version
                continue
            session.add(
                ReferenceDataEntry(
                    version_id=version.id, table_key=table_key, key1=key1, key2=key2, value=value, text_value=text_value
                )
            )

        now = datetime.now(UTC)
        effective = version.effective_from
        cif_buffer = next(v for k, _s, v in dnbp_entries if k == ReferenceDataTableKey.CIF_BUFFER_PER_KG)
        model = DnbpModel(
            name=effective.date().isoformat(),
            note="Initial seed (scripts/seed_reference_data.py)",
            cif_buffer_per_kg=cif_buffer,
            activation_at=effective,
            created_by=None,
            impact_previewed_at=now,
            approved_at=now,
        )
        session.add(model)
        await session.flush()
        per_species: dict[str, dict] = {}
        for table_key, species, value in dnbp_entries:
            if table_key == ReferenceDataTableKey.DNBP_FACTOR:
                per_species.setdefault(species, {})["dnbp_factor"] = value
            elif table_key == ReferenceDataTableKey.STANDARD_WEIGHT:
                per_species.setdefault(species, {})["standard_weight"] = value
        for species, params in per_species.items():
            session.add(DnbpModelSpecies(model_id=model.id, species=species, **params))

        for code in data["open_registries"]["species"]["seed_rows"]:
            session.add(SpeciesRegistry(code=code, display_name=code.title(), is_active=True, created_by=None))
        for code in data["open_registries"]["product_type"]["seed_rows"]:
            session.add(ProductTypeRegistry(code=code, display_name=code, is_active=True, created_by=None))

        await session.commit()
        print(f"Seeded reference data version {version.id} and live DNBP model {model.id} for {everhealth.name}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, type=Path, help="Path to your bootstrap JSON (kept outside the repo).")
    asyncio.run(main(parser.parse_args().file))
