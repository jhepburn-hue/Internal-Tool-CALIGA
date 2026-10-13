from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    """Naive UTC timestamp. The DateTime columns are 'timestamp without time zone'."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name_initial = db.Column(db.String(5), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    role = db.Column(db.String(50), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False) # Can add users and change roles
    is_active = db.Column(db.Boolean, nullable=False, default=True) # Inactive users can't sign in

    @property
    def display_name(self):
        return f"{self.first_name} {self.last_name_initial}."

    def __repr__(self):
        return f"<User {self.first_name} {self.last_name_initial} ({self.role})>"


class Configuration(db.Model):
    __tablename__ = 'configurations'

    id = db.Column(db.Integer, primary_key=True)
    config_name = db.Column(db.String(100), unique=True, nullable=False)
    config_id_hex = db.Column(db.Text, nullable=True)
    config_id_dec = db.Column(db.Integer, index=True, nullable=True) # Not unique: some legacy configs share an ID
    status = db.Column(db.String(50), nullable=False, default='Active')
    previous_status = db.Column(db.String(50), nullable=True)

    et10_ws_2 = db.Column(db.Text, nullable=True)
    et10_ws_3 = db.Column(db.Text, nullable=True)
    et10_ws_6 = db.Column(db.Text, nullable=True)
    et10_ws_7 = db.Column(db.Text, nullable=True)
    et20_ws_2 = db.Column(db.Text, nullable=True)
    et20_ws_3 = db.Column(db.Text, nullable=True)
    et20_ws_6 = db.Column(db.Text, nullable=True)
    et20_ws_7 = db.Column(db.Text, nullable=True)
    et25_ws_2 = db.Column(db.Text, nullable=True)
    et25_ws_3 = db.Column(db.Text, nullable=True)
    et25_ws_6 = db.Column(db.Text, nullable=True)
    et25_ws_7 = db.Column(db.Text, nullable=True)

    et10_f2f = db.Column(db.Text, nullable=True)
    et20_f2f = db.Column(db.Text, nullable=True)
    et25_f2f = db.Column(db.Text, nullable=True)
    et10_mclp = db.Column(db.Text, nullable=True)
    et20_mclp = db.Column(db.Text, nullable=True)
    et25_mclp = db.Column(db.Text, nullable=True)

    idle_led = db.Column(db.Text, nullable=True)
    credential_report_led = db.Column(db.Text, nullable=True)
    beeper = db.Column(db.Text, nullable=True)
    keypad_format = db.Column(db.Text, nullable=True)
    tamper_monitoring = db.Column(db.Text, nullable=True)
    casi_output_format = db.Column(db.Text, nullable=True)
    supervision_state = db.Column(db.Text, nullable=True)
    tamper_monitoring_f2f = db.Column(db.Text, nullable=True)

    leaf_si = db.Column(db.Text, nullable=True)
    leaf_cc = db.Column(db.Text, nullable=True)
    other_custom_hf_application = db.Column(db.Text, nullable=True)
    custom_application_notes = db.Column(db.Text, nullable=True)

    mfc_csn = db.Column(db.Text, nullable=True)
    ev1_ev2_csn = db.Column(db.Text, nullable=True)
    iclass_csn = db.Column(db.Text, nullable=True)
    iso_15693_csn = db.Column(db.Text, nullable=True)
    iso_14443a_csn = db.Column(db.Text, nullable=True)

    fsk_prox = db.Column(db.Text, nullable=True)
    ask_prox = db.Column(db.Text, nullable=True)
    prox_filter = db.Column(db.Text, nullable=True)
    prox_filter_description = db.Column(db.Text, nullable=True)

    ble_advertising_config = db.Column(db.String(100), nullable=True)
    ble_functionality = db.Column(db.String(100), nullable=True)
    nfc_functionality = db.Column(db.Text, nullable=True)
    mobile_keyset = db.Column(db.Text, nullable=True)
    legacy_credentials = db.Column(db.Text, nullable=True)
    transport_mode = db.Column(db.Text, nullable=True)
    mobile_notes = db.Column(db.Text, nullable=True)

    # Fields the INI builder reads that the original configuration form import didn't have.
    keyset_id = db.Column(db.Text, nullable=True) # e.g. "Lk50003"
    custom_keyset = db.Column(db.Text, nullable=True) # 'On' / 'Off': custom LEAF Cc keyset
    ecp_desfire = db.Column(db.Text, nullable=True) # 'On' / 'Off': Apple Wallet
    mifare_2go = db.Column(db.Text, nullable=True) # 'On' / 'Off': Android Wallet
    ecp_tci = db.Column(db.Text, nullable=True) # 6 hex characters
    meridian_bit_count = db.Column(db.Integer, nullable=True) # 32, 40, 57 or 128
    ble_advertising_name = db.Column(db.String(20), nullable=True) # Up to 5 characters

    assigned_part_numbers = db.Column(db.Text, nullable=True)
    card_type = db.Column(db.Text, nullable=True)
    bitstream = db.Column(db.Text, nullable=True)
    facility_code = db.Column(db.Integer, nullable=True)
    starting_badge = db.Column(db.Text, nullable=True)
    card_notes = db.Column(db.Text, nullable=True)
    card_part_numbers = db.Column(db.Text, nullable=True)
    notes_other_products = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    revisions = db.relationship('RevisionHistory', backref='configuration', lazy=True, cascade="all, delete-orphan")

    # leaf_si / leaf_cc hold 'On' / 'Off'. Card Type ("Leaf Si", "Leaf Cc", "Dual") is the fallback.
    def _leaf_enabled(self, column_value, card_types):
        if column_value:
            return column_value.strip().lower() in ('on', 'yes', 'enabled')
        return (self.card_type or '').strip().lower() in card_types

    @property
    def leaf_si_enabled(self):
        return self._leaf_enabled(self.leaf_si, ('leaf si', 'dual'))

    @property
    def leaf_cc_enabled(self):
        return self._leaf_enabled(self.leaf_cc, ('leaf cc', 'dual'))


class RevisionHistory(db.Model):
    __tablename__ = 'revision_history'

    id = db.Column(db.Integer, primary_key=True)
    config_name = db.Column(db.String(100), db.ForeignKey('configurations.config_name'), nullable=False)
    revision_date = db.Column(db.DateTime, default=utcnow, nullable=False)
    user_email = db.Column(db.String(150), nullable=False)
    revision_details = db.Column(db.Text, nullable=False)


class FWRun(db.Model):
    __tablename__ = 'fw_runs'
    id = db.Column(db.Integer, primary_key=True)
    fw_version = db.Column(db.String(20), nullable=False) # 'v5.4.10' or 'v5.4.11'
    run_date = db.Column(db.DateTime, default=utcnow)

    test_groups = db.relationship('ConfigurationTestGroup', backref='fw_run', cascade="all, delete-orphan")

    # Totals are calculated from group statuses so re-tests can't make them drift.
    @property
    def passed_total(self):
        return sum(1 for g in self.test_groups if g.status == 'Passed')

    @property
    def failed_total(self):
        return sum(1 for g in self.test_groups if g.status == 'Failed')


class ConfigurationTestGroup(db.Model):
    __tablename__ = 'configuration_test_groups'
    id = db.Column(db.Integer, primary_key=True)
    fw_run_id = db.Column(db.Integer, db.ForeignKey('fw_runs.id'), nullable=False)
    configuration_id = db.Column(db.Integer, db.ForeignKey('configurations.id'), nullable=True)
    config_name = db.Column(db.String(100), nullable=False)
    assigned_user = db.Column(db.String(120), nullable=True) # User email
    claimed_user = db.Column(db.String(120), nullable=True) # User email
    status = db.Column(db.String(20), default='Untested') # 'Untested', 'Passed', 'Failed'
    jira_key = db.Column(db.String(50), nullable=True)

    configuration = db.relationship('Configuration')
    test_cases = db.relationship('TestCase', backref='test_group', cascade="all, delete-orphan", order_by='TestCase.id')

    @property
    def cases_by_category(self):
        """Returns [(category, [test cases])] in the order the criteria were created."""
        groups = {}
        for tc in self.test_cases:
            groups.setdefault(tc.display_category, []).append(tc)
        return list(groups.items())


class TestCase(db.Model):
    __tablename__ = 'test_cases'
    id = db.Column(db.Integer, primary_key=True)
    test_group_id = db.Column(db.Integer, db.ForeignKey('configuration_test_groups.id'), nullable=False)
    category = db.Column(db.String(200), nullable=True) # e.g. "ACTION: Verify AV and Output Formats."
    criterion_name = db.Column(db.String(200), nullable=False) # e.g. "Idle LED"
    expected_value = db.Column(db.Text, nullable=True) # e.g. "Blue"
    status = db.Column(db.String(20), default='Untested') # 'Untested', 'Passed', 'Failed'
    comment = db.Column(db.Text, nullable=True)

    def _legacy_parts(self):
        # Rows created before the category/expected_value columns stored "category | label:value".
        cat, sep, rest = self.criterion_name.partition(' | ')
        if not sep:
            return 'General Verification', self.criterion_name, ''
        label, _, expected = rest.partition(':')
        return cat, label, expected

    @property
    def display_category(self):
        return self.category if self.category is not None else self._legacy_parts()[0]

    @property
    def display_label(self):
        return self.criterion_name if self.category is not None else self._legacy_parts()[1]

    @property
    def display_expected(self):
        return (self.expected_value or '') if self.category is not None else self._legacy_parts()[2]


class OemSku(db.Model):
    __tablename__ = 'oem_skus'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False) # e.g. "BRIVO"
    sku_id = db.Column(db.Integer, nullable=False)


class Job(db.Model):
    """A queued background job (Forge build, PocketBase sync, ...). Any server process can run or report on it."""
    __tablename__ = 'jobs'
    id = db.Column(db.String(32), primary_key=True)
    kind = db.Column(db.String(50), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending', index=True) # pending, running, done, failed
    message = db.Column(db.Text, nullable=True)
    progress = db.Column(db.Float, nullable=True)
    params = db.Column(db.JSON, nullable=False, default=dict)
    result = db.Column(db.JSON, nullable=True)
    files = db.Column(db.JSON, nullable=True) # {key: {"name": download name}}. Bytes live in job_files.
    error = db.Column(db.Text, nullable=True)
    worker = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    heartbeat_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    stored_files = db.relationship('JobFile', backref='job', cascade="all, delete-orphan", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "progress": self.progress,
            "result": self.result or {},
            "files": sorted(self.files or {}),
            "error": self.error,
        }


class JobFile(db.Model):
    """A file a job produced (a built binary, a generated INI). Kept in the database, not GCS, and
    deleted with its job."""
    __tablename__ = 'job_files'
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(32), db.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    key = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
