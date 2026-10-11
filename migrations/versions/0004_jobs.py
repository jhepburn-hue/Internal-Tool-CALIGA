"""Jobs table for the shared background job queue.

Revision ID: 0004_jobs
Revises: 0003_ini_generator_fields
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_jobs'
down_revision = '0003_ini_generator_fields'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'jobs',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('kind', sa.String(50), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('progress', sa.Float(), nullable=True),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('files', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('worker', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_jobs_status', 'jobs', ['status'])
    op.create_index('ix_jobs_created_at', 'jobs', ['created_at'])


def downgrade():
    op.drop_index('ix_jobs_created_at', table_name='jobs')
    op.drop_index('ix_jobs_status', table_name='jobs')
    op.drop_table('jobs')
