"""dnbp_models_scheduled_activation

Revision ID: b9e2c4d7a1f3
Revises: a7d4e9f1c2b6
Create Date: 2026-09-24 00:00:00.000000

Splits the DNBP pricing parameters (model type, CIF buffer, per-species dnbp
factor and standard weight) out of reference_data_versions into their own
immutable, schedulable `dnbp_models`. Operational tunables and the saleyard
calendar stay in reference_data_versions.

Data migration: every reference_data_versions row that was ever activated
becomes an approved dnbp_models row *with the same id*, so every existing
order_workings.ref_data_version_id stamp still resolves — the FK is simply
repointed. `activation_at` is the version's real `activated_at`, which orders
history exactly as it happened and makes the currently active version the
latest one (checked below). Versions that were never activated never priced
anything and stay behind untouched in reference_data_versions.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b9e2c4d7a1f3'
down_revision: Union[str, None] = 'a7d4e9f1c2b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MONEY = sa.Numeric(18, 10)


def upgrade() -> None:
    op.create_table(
        'dnbp_models',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('model_type', sa.String(), server_default='FACTOR_AFTER_BUFFER', nullable=False),
        sa.Column('cif_buffer_per_kg', MONEY, nullable=False),
        sa.Column('activation_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('impact_previewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id']),
        sa.ForeignKeyConstraint(['cancelled_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('ix_dnbp_models_activation_at', 'dnbp_models', ['activation_at'])
    op.create_index(
        'uq_dnbp_models_approved_activation_at',
        'dnbp_models',
        ['activation_at'],
        unique=True,
        postgresql_where=sa.text('approved_at IS NOT NULL AND cancelled_at IS NULL'),
    )

    op.create_table(
        'dnbp_model_species',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('model_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('species', sa.String(), nullable=False),
        sa.Column('dnbp_factor', MONEY, nullable=True),
        sa.Column('standard_weight', MONEY, nullable=True),
        sa.ForeignKeyConstraint(['model_id'], ['dnbp_models.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('model_id', 'species', name='uq_dnbp_model_species_model_species'),
    )
    op.create_index('ix_dnbp_model_species_model_id', 'dnbp_model_species', ['model_id'])

    _copy_activated_versions_into_models()

    op.drop_constraint('order_workings_ref_data_version_id_fkey', 'order_workings', type_='foreignkey')
    op.create_foreign_key(
        'order_workings_ref_data_version_id_fkey', 'order_workings', 'dnbp_models', ['ref_data_version_id'], ['id']
    )


def _copy_activated_versions_into_models() -> None:
    bind = op.get_bind()
    versions = bind.execute(
        sa.text(
            "SELECT id, effective_from, created_at, created_by, note, model_type, is_active, activated_at, activated_by, "
            "impact_previewed_at FROM reference_data_versions WHERE activated_at IS NOT NULL ORDER BY activated_at"
        )
    ).mappings().all()

    used_names: set[str] = set()
    live_expected = None
    for v in versions:
        entries = bind.execute(
            sa.text("SELECT table_key::text AS table_key, key1, value FROM reference_data_entries WHERE version_id = :v"),
            {"v": v["id"]},
        ).mappings().all()
        cif = next((e["value"] for e in entries if e["table_key"] == "CIF_BUFFER_PER_KG"), None)
        if cif is None:
            raise RuntimeError(f"Reference data version {v['id']} was activated but has no CIF buffer — cannot migrate.")

        name = v["effective_from"].date().isoformat()
        if name in used_names:
            name = f"{name} ({str(v['id'])[:8]})"
        used_names.add(name)

        bind.execute(
            sa.text(
                "INSERT INTO dnbp_models (id, name, note, model_type, cif_buffer_per_kg, activation_at, created_by, "
                "created_at, impact_previewed_at, approved_by, approved_at) VALUES (:id, :name, :note, "
                ":model_type, :cif, :activation_at, :created_by, :created_at, :impact_previewed_at, :approved_by, "
                ":approved_at)"
            ),
            {
                "id": v["id"],
                "name": name,
                "note": v["note"],
                "model_type": v["model_type"],
                "cif": cif,
                "activation_at": v["activated_at"],
                "created_by": v["created_by"],
                "created_at": v["created_at"],  # when the version was made, not when this migration ran
                "impact_previewed_at": v["impact_previewed_at"],
                "approved_by": v["activated_by"],
                "approved_at": v["activated_at"],
            },
        )

        per_species: dict[str, dict] = {}
        for e in entries:
            if e["table_key"] == "DNBP_FACTOR":
                per_species.setdefault(e["key1"], {})["dnbp_factor"] = e["value"]
            elif e["table_key"] == "STANDARD_WEIGHT":
                per_species.setdefault(e["key1"], {})["standard_weight"] = e["value"]
        for species, params in per_species.items():
            bind.execute(
                sa.text(
                    "INSERT INTO dnbp_model_species (id, model_id, species, dnbp_factor, standard_weight) "
                    "VALUES (gen_random_uuid(), :model_id, :species, :factor, :weight)"
                ),
                {
                    "model_id": v["id"],
                    "species": species,
                    "factor": params.get("dnbp_factor"),
                    "weight": params.get("standard_weight"),
                },
            )

        if v["is_active"]:
            live_expected = v["id"]

    # The model resolved as live must be the version that was active. Both
    # follow activated_at order, so this holds unless the data is corrupt —
    # in which case stop rather than silently switch prices.
    if live_expected is not None:
        latest = bind.execute(
            sa.text("SELECT id FROM dnbp_models ORDER BY activation_at DESC LIMIT 1")
        ).scalar_one()
        if latest != live_expected:
            raise RuntimeError(
                f"Migration would make model {latest} live, but reference data version {live_expected} is active."
            )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_constraint('order_workings_ref_data_version_id_fkey', 'order_workings', type_='foreignkey')
    # Stamps that point at a model with no reference_data_versions twin (a
    # model created after this migration) cannot be restored; clear them.
    bind.execute(
        sa.text(
            "UPDATE order_workings SET ref_data_version_id = NULL WHERE ref_data_version_id IS NOT NULL "
            "AND ref_data_version_id NOT IN (SELECT id FROM reference_data_versions)"
        )
    )
    op.create_foreign_key(
        'order_workings_ref_data_version_id_fkey',
        'order_workings',
        'reference_data_versions',
        ['ref_data_version_id'],
        ['id'],
    )
    op.drop_index('ix_dnbp_model_species_model_id', table_name='dnbp_model_species')
    op.drop_table('dnbp_model_species')
    op.drop_index('uq_dnbp_models_approved_activation_at', table_name='dnbp_models')
    op.drop_index('ix_dnbp_models_activation_at', table_name='dnbp_models')
    op.drop_table('dnbp_models')
