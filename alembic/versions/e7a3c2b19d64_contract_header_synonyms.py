"""ingestion contract: accepted header spellings, header scan window, match threshold

The header spellings the parser accepts (previously a dictionary in code) and
the two header-detection constants (rows scanned, matches needed) become part
of the versioned, audited ingestion contract, so a renamed column header is a
new contract version rather than a code change. Existing contracts are
backfilled with the values the parser has been using.

Revision ID: e7a3c2b19d64
Revises: d5f1a8c39b47
Create Date: 2026-09-21 20:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e7a3c2b19d64'
down_revision: Union[str, None] = 'd5f1a8c39b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copy of the spellings the parser accepted when this migration was written.
_DEFAULT_HEADER_SYNONYMS = json.loads("""
{
    "contract_no": [
        "contract no",
        "contract no.",
        "order number",
        "row labels"
    ],
    "customer_name": [
        "customer name"
    ],
    "species": [
        "species",
        "type"
    ],
    "loadout_date": [
        "loadout date"
    ],
    "qty_kg": [
        "qty",
        "sum of total qty",
        "total qty"
    ],
    "avg_price_aud": [
        "average of price aud",
        "avg price aud",
        "price aud"
    ],
    "amount_aud": [
        "amount aud",
        "sum of amount aud"
    ],
    "product_type": [
        "product type"
    ],
    "incoterm": [
        "cif or fas",
        "cif or fas ?",
        "incoterm"
    ],
    "nrv_per_kg": [
        "nrv (per kg)",
        "nrv per kg"
    ],
    "expected_livestock_cost_per_kg": [
        "expected livestock cost per kg hscw",
        "livestock cost per kg hscw"
    ],
    "pack_cost_ph": [
        "pack cost"
    ],
    "offal_return_ph": [
        "offal return ph"
    ],
    "skin_return_ph": [
        "avg skin return",
        "skin return"
    ],
    "avg_weight_kg": [
        "average weight",
        "avg weight"
    ],
    "mom_ph": [
        "mom ph"
    ],
    "deposit_received": [
        "deposit received"
    ],
    "comments": [
        "comments"
    ],
    "dnbp_benchmark": [
        "do not buy price",
        "do not buy price using method instructed by the financier"
    ],
    "estimated_heads": [
        "estimated number of heads required"
    ],
    "total_livestock_cost": [
        "total livestock cost"
    ]
}
""")


def upgrade() -> None:
    op.add_column('ingestion_contracts', sa.Column('header_synonyms', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('ingestion_contracts', sa.Column('header_scan_rows', sa.Integer(), nullable=False, server_default='20'))
    op.add_column('ingestion_contracts', sa.Column('min_header_matches', sa.Integer(), nullable=False, server_default='6'))
    op.execute(
        sa.text("UPDATE ingestion_contracts SET header_synonyms = CAST(:synonyms AS JSONB)").bindparams(
            synonyms=json.dumps(_DEFAULT_HEADER_SYNONYMS)
        )
    )
    op.alter_column('ingestion_contracts', 'header_synonyms', nullable=False)
    op.alter_column('ingestion_contracts', 'header_scan_rows', server_default=None)
    op.alter_column('ingestion_contracts', 'min_header_matches', server_default=None)


def downgrade() -> None:
    op.drop_column('ingestion_contracts', 'min_header_matches')
    op.drop_column('ingestion_contracts', 'header_scan_rows')
    op.drop_column('ingestion_contracts', 'header_synonyms')
