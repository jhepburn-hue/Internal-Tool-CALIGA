from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name_initial = db.Column(db.String(5), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    role = db.Column(db.String(50), nullable=False)

    def __repr__(self):
        return f"<User {self.first_name} {self.last_name_initial} ({self.role})>"


class Configuration(db.Model):
    __tablename__ = 'configurations'

    id = db.Column(db.Integer, primary_key=True)
    config_name = db.Column(db.String(100), unique=True, nullable=False)
    config_id_hex = db.Column(db.Text, nullable=True)
    config_id_dec = db.Column(db.Text, nullable=True)
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

    ble_advertising_config = db.Column(db.Text, nullable=True)
    ble_functionality = db.Column(db.Text, nullable=True)
    nfc_functionality = db.Column(db.Text, nullable=True)
    mobile_keyset = db.Column(db.Text, nullable=True)
    legacy_credentials = db.Column(db.Text, nullable=True)
    transport_mode = db.Column(db.Text, nullable=True)
    mobile_notes = db.Column(db.Text, nullable=True)

    assigned_part_numbers = db.Column(db.Text, nullable=True)
    card_type = db.Column(db.Text, nullable=True)
    bitstream = db.Column(db.Text, nullable=True)
    facility_code = db.Column(db.Integer, nullable=True)  
    starting_badge = db.Column(db.Text, nullable=True)
    card_notes = db.Column(db.Text, nullable=True)
    card_part_numbers = db.Column(db.Text, nullable=True)
    notes_other_products = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    revisions = db.relationship('RevisionHistory', backref='configuration', lazy=True, cascade="all, delete-orphan")


class RevisionHistory(db.Model):
    __tablename__ = 'revision_history'

    id = db.Column(db.Integer, primary_key=True)
    config_name = db.Column(db.String(100), db.ForeignKey('configurations.config_name'), nullable=False)
    revision_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user_email = db.Column(db.String(150), nullable=False)
    revision_details = db.Column(db.Text, nullable=False)


class FWRun(db.Model):
    __tablename__ = 'fw_runs'
    id = db.Column(db.Integer, primary_key=True)
    fw_version = db.Column(db.String(20), nullable=False) # 'v5.4.10' or 'v5.4.11'
    run_date = db.Column(db.DateTime, default=datetime.utcnow)
    passed_total = db.Column(db.Integer, default=0)
    failed_total = db.Column(db.Integer, default=0)
    
    test_groups = db.relationship('ConfigurationTestGroup', backref='fw_run', cascade="all, delete-orphan")

class ConfigurationTestGroup(db.Model):
    __tablename__ = 'configuration_test_groups'
    id = db.Column(db.Integer, primary_key=True)
    fw_run_id = db.Column(db.Integer, db.ForeignKey('fw_runs.id'), nullable=False)
    config_name = db.Column(db.String(100), nullable=False)
    assigned_user = db.Column(db.String(120), nullable=True) # User email or name
    claimed_user = db.Column(db.String(120), nullable=True) # User email or name
    status = db.Column(db.String(20), default='Untested') # 'Untested', 'In Progress', 'Passed', 'Failed'
    jira_key = db.Column(db.String(50), nullable=True)
    
    test_cases = db.relationship('TestCase', backref='test_group', cascade="all, delete-orphan")

class TestCase(db.Model):
    __tablename__ = 'test_cases'
    id = db.Column(db.Integer, primary_key=True)
    test_group_id = db.Column(db.Integer, db.ForeignKey('configuration_test_groups.id'), nullable=False)
    criterion_name = db.Column(db.String(200), nullable=False) # e.g. "Idle LED: Blue"
    status = db.Column(db.String(20), default='Untested') # 'Untested', 'Passed', 'Failed'
    comment = db.Column(db.Text, nullable=True)


class Failure(db.Model):
    __tablename__ = 'failures'

    id = db.Column(db.Integer, primary_key=True)
    config_name = db.Column(db.String(100), nullable=False)
    fw_run = db.Column(db.String(50), nullable=False)
    jira_ticket_key = db.Column(db.String(50), nullable=True)
    claimed_by = db.Column(db.String(150), nullable=True)
    status = db.Column(db.String(50), default='Open')
    ini_details = db.Column(db.Text, nullable=True)
    comments = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)