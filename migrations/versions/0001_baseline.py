"""Baseline: the schema as it existed before migrations were added.

An existing database already has these tables. Mark it as being at this
revision without running it:

    flask --app app db stamp 0001_baseline

A new, empty database runs it normally with `flask --app app db upgrade`.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa


revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('first_name', sa.String(100), nullable=False),
        sa.Column('last_name_initial', sa.String(5), nullable=False),
        sa.Column('email', sa.String(150), nullable=False, unique=True),
        sa.Column('role', sa.String(50), nullable=False),
    )

    text_columns = [
        'et10_ws_2', 'et10_ws_3', 'et10_ws_6', 'et10_ws_7',
        'et20_ws_2', 'et20_ws_3', 'et20_ws_6', 'et20_ws_7',
        'et25_ws_2', 'et25_ws_3', 'et25_ws_6', 'et25_ws_7',
        'et10_f2f', 'et20_f2f', 'et25_f2f', 'et10_mclp', 'et20_mclp', 'et25_mclp',
        'idle_led', 'credential_report_led', 'beeper', 'keypad_format', 'tamper_monitoring',
        'casi_output_format', 'supervision_state', 'tamper_monitoring_f2f',
        'leaf_si', 'leaf_cc', 'other_custom_hf_application', 'custom_application_notes',
        'mfc_csn', 'ev1_ev2_csn', 'iclass_csn', 'iso_15693_csn', 'iso_14443a_csn',
        'fsk_prox', 'ask_prox', 'prox_filter', 'prox_filter_description',
        'nfc_functionality', 'mobile_keyset', 'legacy_credentials', 'transport_mode', 'mobile_notes',
        'assigned_part_numbers', 'card_type', 'bitstream', 'starting_badge', 'card_notes',
        'card_part_numbers', 'notes_other_products',
    ]

    op.create_table(
        'configurations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('config_name', sa.String(100), nullable=False, unique=True),
        sa.Column('config_id_hex', sa.Text(), nullable=True),
        sa.Column('config_id_dec', sa.Text(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('previous_status', sa.String(50), nullable=True),
        sa.Column('ble_advertising_config', sa.String(100), nullable=True),
        sa.Column('ble_functionality', sa.String(100), nullable=True),
        sa.Column('facility_code', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        *[sa.Column(name, sa.Text(), nullable=True) for name in text_columns],
    )

    op.create_table(
        'revision_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('config_name', sa.String(100), sa.ForeignKey('configurations.config_name'), nullable=False),
        sa.Column('revision_date', sa.DateTime(), nullable=False),
        sa.Column('user_email', sa.String(150), nullable=False),
        sa.Column('revision_details', sa.Text(), nullable=False),
    )

    op.create_table(
        'fw_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fw_version', sa.String(20), nullable=False),
        sa.Column('run_date', sa.DateTime(), nullable=True),
        sa.Column('passed_total', sa.Integer(), nullable=True),
        sa.Column('failed_total', sa.Integer(), nullable=True),
    )

    op.create_table(
        'configuration_test_groups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fw_run_id', sa.Integer(), sa.ForeignKey('fw_runs.id'), nullable=False),
        sa.Column('config_name', sa.String(100), nullable=False),
        sa.Column('assigned_user', sa.String(120), nullable=True),
        sa.Column('claimed_user', sa.String(120), nullable=True),
        sa.Column('status', sa.String(20), nullable=True),
        sa.Column('jira_key', sa.String(50), nullable=True),
    )

    op.create_table(
        'test_cases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('test_group_id', sa.Integer(), sa.ForeignKey('configuration_test_groups.id'), nullable=False),
        sa.Column('criterion_name', sa.String(200), nullable=False),
        sa.Column('status', sa.String(20), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
    )

    op.create_table(
        'failures',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('config_name', sa.String(100), nullable=False),
        sa.Column('fw_run', sa.String(50), nullable=False),
        sa.Column('jira_ticket_key', sa.String(50), nullable=True),
        sa.Column('claimed_by', sa.String(150), nullable=True),
        sa.Column('status', sa.String(50), nullable=True),
        sa.Column('ini_details', sa.Text(), nullable=True),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    for table in ['failures', 'test_cases', 'configuration_test_groups', 'fw_runs', 'revision_history',
                  'configurations', 'users']:
        op.drop_table(table)
