"""Job files live in the database instead of GCS.

CALIGA only writes to GCS inside forge/<user email>/, so background job output (built
binaries, generated INIs) is stored here and deleted with its job after a day.

Revision ID: 0006_job_files
Revises: 0005_user_admin_active
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa


revision = '0006_job_files'
down_revision = '0005_user_admin_active'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'job_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.String(32), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key', sa.String(50), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_job_files_job_id', 'job_files', ['job_id'])


def downgrade():
    op.drop_index('ix_job_files_job_id', table_name='job_files')
    op.drop_table('job_files')
