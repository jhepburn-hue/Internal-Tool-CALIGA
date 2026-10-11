"""Configuration fields INI-Generator reads: keyset, wallet, and BLE advertising name.

Existing configurations start with these blank. Their generated INIs leave those
settings at defaults, with a warning, until the fields are filled in.

Revision ID: 0003_ini_generator_fields
Revises: 0002_review_fixes
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_ini_generator_fields'
down_revision = '0002_review_fixes'
branch_labels = None
depends_on = None

COLUMNS = [
    ('keyset_id', sa.Text()),
    ('custom_keyset', sa.Text()),
    ('ecp_desfire', sa.Text()),
    ('mifare_2go', sa.Text()),
    ('ecp_tci', sa.Text()),
    ('meridian_bit_count', sa.Integer()),
    ('ble_advertising_name', sa.String(20)),
]


def upgrade():
    for name, type_ in COLUMNS:
        op.add_column('configurations', sa.Column(name, type_, nullable=True))


def downgrade():
    for name, _ in reversed(COLUMNS):
        op.drop_column('configurations', name)
