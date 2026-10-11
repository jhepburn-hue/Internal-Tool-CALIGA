"""Review fixes: integer config IDs, test case columns, calculated run totals,
test group foreign key, OEM table, and removal of the unused failures table.

Before changing config_id_dec to an integer, this migration checks for values that
aren't whole numbers. If it finds any it stops and lists them, so nothing is silently
nulled. Fix those rows, then run it again.

config_id_dec is indexed but not unique: some existing configurations legitimately
share an ID (e.g. CBA6 and CAM3 are both 8). New IDs are allocated under a lock instead.

Revision ID: 0002_review_fixes
Revises: 0001_baseline
Create Date: 2026-10-08
"""
import logging
from alembic import op, context
import sqlalchemy as sa


log = logging.getLogger('alembic.runtime.migration')

revision = '0002_review_fixes'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None

# Copied from the in-memory map the YAML automator used before. FIL and QUADREAL
# both used 8 there. They're kept as-is until the correct IDs are confirmed.
OEM_SEED = {
    "BUTTERFLY_MX": 1, "BOA": 2, "REDHAT": 3, "UGA": 4, "CWO1": 5,
    "LEAF_SI": 6, "WELLS_FARGO": 7, "FIL": 8, "QUADREAL": 8,
    "SOLOINSIGHT": 10, "CARDINAL_HEALTH": 11, "BRIVO": 27,
    "TEST": 18, "TIAA": 61, "PDK": 62,
}


def _check_config_ids():
    if context.is_offline_mode():
        return
    bind = op.get_bind()

    bad = bind.execute(sa.text(
        "SELECT config_name, config_id_dec FROM configurations "
        "WHERE config_id_dec IS NOT NULL AND trim(config_id_dec) <> '' "
        "AND trim(config_id_dec) !~ '^[0-9]+$'"
    )).fetchall()
    if bad:
        rows = ", ".join(f"{name}={value!r}" for name, value in bad)
        raise RuntimeError(f"config_id_dec has values that aren't whole numbers: {rows}")

    dupes = bind.execute(sa.text(
        "SELECT trim(config_id_dec)::integer AS id, string_agg(config_name, ', ') "
        "FROM configurations WHERE trim(coalesce(config_id_dec, '')) <> '' "
        "GROUP BY 1 HAVING count(*) > 1"
    )).fetchall()
    if dupes:
        rows = "; ".join(f"{cid}: {names}" for cid, names in dupes)
        log.warning("config_id_dec has shared IDs (allowed, kept as-is): %s", rows)


def upgrade():
    # Integer config IDs, indexed. Not unique, because some existing configs share an ID.
    _check_config_ids()
    op.alter_column(
        'configurations', 'config_id_dec',
        existing_type=sa.Text(), type_=sa.Integer(), existing_nullable=True,
        postgresql_using="NULLIF(trim(config_id_dec), '')::integer"
    )
    op.create_index('ix_configurations_config_id_dec', 'configurations', ['config_id_dec'])

    # Test cases store category and expected value in their own columns.
    op.add_column('test_cases', sa.Column('category', sa.String(200), nullable=True))
    op.add_column('test_cases', sa.Column('expected_value', sa.Text(), nullable=True))

    # Run totals are calculated from group statuses.
    op.drop_column('fw_runs', 'passed_total')
    op.drop_column('fw_runs', 'failed_total')

    # Test groups link to their configuration by foreign key.
    op.add_column('configuration_test_groups', sa.Column('configuration_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_test_groups_configuration_id', 'configuration_test_groups', 'configurations',
                          ['configuration_id'], ['id'])
    op.execute(
        "UPDATE configuration_test_groups g SET configuration_id = c.id "
        "FROM configurations c WHERE c.config_name = g.config_name"
    )

    # The failures table was never used. Failures are test groups with status 'Failed'.
    op.drop_table('failures')

    # OEM SKU IDs for the YAML automator.
    oem_skus = op.create_table(
        'oem_skus',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('sku_id', sa.Integer(), nullable=False),
    )
    op.bulk_insert(oem_skus, [{"name": name, "sku_id": sku_id} for name, sku_id in OEM_SEED.items()])


def downgrade():
    op.drop_table('oem_skus')

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

    op.drop_constraint('fk_test_groups_configuration_id', 'configuration_test_groups', type_='foreignkey')
    op.drop_column('configuration_test_groups', 'configuration_id')

    op.add_column('fw_runs', sa.Column('passed_total', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('fw_runs', sa.Column('failed_total', sa.Integer(), nullable=True, server_default='0'))
    op.execute(
        "UPDATE fw_runs r SET "
        "passed_total = (SELECT count(*) FROM configuration_test_groups g WHERE g.fw_run_id = r.id AND g.status = 'Passed'), "
        "failed_total = (SELECT count(*) FROM configuration_test_groups g WHERE g.fw_run_id = r.id AND g.status = 'Failed')"
    )

    op.drop_column('test_cases', 'expected_value')
    op.drop_column('test_cases', 'category')

    op.drop_index('ix_configurations_config_id_dec', table_name='configurations')
    op.alter_column(
        'configurations', 'config_id_dec',
        existing_type=sa.Integer(), type_=sa.Text(), existing_nullable=True,
        postgresql_using="config_id_dec::text"
    )
