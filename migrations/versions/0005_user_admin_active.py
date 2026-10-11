"""User admin and active flags. James H (jhepburn@wavelynx.com) is the only admin.

Revision ID: 0005_user_admin_active
Revises: 0004_jobs
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_user_admin_active'
down_revision = '0004_jobs'
branch_labels = None
depends_on = None

ADMIN_EMAIL = 'jhepburn@wavelynx.com'


def upgrade():
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute(sa.text("UPDATE users SET is_admin = true WHERE lower(email) = :email").bindparams(email=ADMIN_EMAIL))


def downgrade():
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'is_admin')
