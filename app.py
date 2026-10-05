import os
import time
import yaml
import datetime
from sqlalchemy import func
import concurrent.futures
from flask import Flask, render_template, session, request, redirect, url_for, send_file, request, Response, stream_with_context, jsonify, flash, json
from models import db, User, Configuration, RevisionHistory, FWRun, ConfigurationTestGroup, TestCase
from dotenv import load_dotenv
from services.slack_service import send_alert_qa_notification, send_failure_message
from services.gcs_service import get_or_create_ini_file, get_or_build_profile_bin, get_or_build_firmware_bin, upload_partial_ini_to_gcs, get_failure_ini_content, save_edited_failure_ini
from services.osdp_service import flash_firmware_osdp, find_rs485_port
from services.pocketbase_service import process_get_tokens
from services.ini_translator_service import translate_uploaded_ini
from services.forge_service import generate_partial_ini_content, compile_partial_bin_via_forge
from services.jira_service import create_failure_ticket, assign_jira_ticket, transition_jira_issue_to_complete
from services.osdp_service import flash_reader_osdp

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'caliga-dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,  
    "pool_recycle": 300     
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

SAM_TYPE_MAP = {
    "sam_av2": {"label": "SAM_AV2", "id": 2},
    "sam_av3": {"label": "SAM_AV3", "id": 3}
}
PART_MAP = {
    "elatec": {"label": "PARTNER_ELATEC", "id": 1},
    "rfideas": {"label": "PARTNER_RFIDEAS", "id": 2},
    "brivo": {"label": "PARTNER_BRIVO", "id": 3},
    "test": {"label": "PARTNER_TEST", "id": 4}
}
PART_KM_MAP = {
    "test": {"label": "partnerKm0", "id": 0},
    "elatec": {"label": "partnerKm1", "id": 1},
    "rfideas": {"label": "partnerKm2", "id": 2},
    "brivo": {"label": "partnerKm3", "id": 3},
    "pdq": {"label": "partnerKm4", "id": 4}
}
OEM_ID_MAP = {
    "BUTTERFLY_MX": 1, "BOA": 2, "REDHAT": 3, "UGA": 4, "CWO1": 5,
    "LEAF_SI": 6, "WELLS_FARGO": 7, "FIL": 8, "QUADREAL": 8,
    "SOLOINSIGHT": 10, "CARDINAL_HEALTH": 11, "BRIVO": 27,
    "TEST": 18, "TIAA": 61, "PDK": 62
}
KEY_TYPE_MAP = {
    "aes_128": ("SAM_AES_128_KEY", 0x20),
    "mifare": ("SAM_MIFARE_KEY", 0x10)
}
KEY_USE_MAP = {
    "picc": ("SAM_PICC_KEY", 1),
    "offline_crypto": ("SAM_OFFLINE_CRYPTO_KEY", 1)
}
ECP_TERMINAL_MAP = {
    "tra_express": ("ECP_WL_TERMINAL_INFO", 0xC3),
    "tra_enabled": ("ECP_WL_TERMINAL_INFO_USER_AUTH", 0x83)
}
ECP_SUBTYPE_MAP = {
    "university": ("ECP_TERMINAL_SUBTYPE_UNIVERSITY", 0),
    "corporate": ("ECP_TERMINAL_SUBTYPE_CORPORATE", 2),
    "hospitality": ("ECP_TERMINAL_SUBTYPE_HOSPITALITY", 3),
    "residential": ("ECP_TERMINAL_SUBTYPE_RESIDENTIAL", 4)
}
ECP_APP_MAP = {
    "credential_app": ("ECP_OPTIONS_1", 1),
    "secure_app": ("ECP_OPTIONS_2", 2)
}

class HexInt(int):
    pass

def hex_int_representer(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:int', f'0x{data:02X}')

yaml.add_representer(HexInt, hex_int_representer)

@app.context_processor
def inject_user():
    user_id = session.get('user_id')
    current_user = None
    if user_id:
        current_user = User.query.get(user_id)
    if not current_user:
        current_user = User.query.filter_by(email="jhepburn@wavelynx.com").first() or User.query.first()
    return dict(current_user=current_user)

@app.route('/')
def home():
    role_descriptions = {
        'SET': [
            'Initialize and manage Firmware execution runs',
            'Modify parameters across reader configurations',
            'Execute test groups and validate individual criteria',
            'Review revision history logs and modification tracking',
            'Monitor Firmware run metrics and aggregate analytics',
            'Inspect issue details within the Failures repository',
            'Dispatch automated test requests to QA'
        ],
        'QA': [
            'Initiate firmware execution test cycles',
            'Execute assigned test suites and record validation criteria',
            'Analyze firmware test run telemetry and aggregate statistics'
        ],
        'Sales': [
            'Provision new reader configuration profiles',
            'Modify parameter details for draft configurations',
            'Toggle configuration deployment state between POC and Active',
            'Dispatch testing notification alerts to QA',
            'Review Firmware execution analytics and run statistics'
        ]
    }

    status_descriptions = {
        'Production': 'Validated configuration profile approved for official deployment and manufacturing.',
        'Active': 'Deployed profile requiring verification and testing prior to Production promotion.',
        'POC': 'Proof of Concept profile open for editing and preliminary experimentation.',
        'Archived': 'Deprecated configuration profile retained strictly for historical reference.'
    }

    return render_template(
        'home.html',
        role_descriptions=role_descriptions,
        status_descriptions=status_descriptions
    )

@app.route('/configurations')
def configurations_list():
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()

    query = Configuration.query

    if search_query:
        query = query.filter(
            (Configuration.config_name.ilike(f'%{search_query}%')) |
            (Configuration.config_id_hex.ilike(f'%{search_query}%')) |
            (Configuration.config_id_dec.ilike(f'%{search_query}%'))
        )

    if status_filter:
        query = query.filter(Configuration.status == status_filter)

    configurations = query.order_by(
        db.cast(db.cast(db.func.nullif(Configuration.config_id_dec, ''), db.Numeric), db.Integer).asc().nulls_last()
    ).all()

    return render_template(
        'configurations.html',
        configurations=configurations,
        search_query=search_query,
        status_filter=status_filter
    )

@app.route('/configurations/<config_name>')
def configuration_details(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    revisions = RevisionHistory.query.filter_by(config_name=config_name).order_by(RevisionHistory.revision_date.desc()).all()
    
    return render_template('configuration_details.html', config=config, revisions=revisions)

@app.route('/configurations/<config_name>/toggle-status/<new_status>')
def toggle_status(config_name, new_status):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    
    config.previous_status = config.status
    config.status = new_status

    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "system@wavelynx.com"

    rev = RevisionHistory(
        config_name=config.config_name,
        user_email=user_email,
        revision_details=f"Status changed from {config.previous_status} to {new_status}."
    )
    db.session.add(rev)
    db.session.commit()

    return redirect(url_for('configuration_details', config_name=config_name))

@app.route('/configurations/<config_name>/alert-qa', endpoint='alert_qa')
def alert_qa(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "system@wavelynx.com"
    fw_version = request.args.get('fw_version', 'v5.4.10').strip()

    slack_sent = send_alert_qa_notification(config.config_name, config.status, user_email, fw_version=fw_version)

    if slack_sent:
        flash(f"QA team alerted for {config_name} via Slack!", "success")

    return redirect(url_for('configuration_details', config_name=config_name))

@app.route('/configurations/<config_name>/download-ini')
def download_ini(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"
    
    fw_version = request.args.get('fw_version', 'v5.4.10').strip()

    file_path = get_or_create_ini_file(config, fw_version, user_email)
    
    if file_path and os.path.exists(file_path):
        return send_file(
            file_path, 
            as_attachment=True, 
            download_name=f"{config.config_name}_{fw_version}.ini"
        )

    return f"Unable to generate or retrieve INI file for {config_name}.", 404

@app.route('/configurations/<config_name>/download-profile-bin')
def download_profile(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"
    
    fw_version = request.args.get('fw_version', 'v5.4.10').strip()

    file_path = get_or_build_profile_bin(config, fw_version, user_email)
    
    if file_path and os.path.exists(file_path):
        return send_file(
            file_path, 
            as_attachment=True, 
            download_name=f"{config.config_name}_{fw_version}.bin"
        )

    return f"Profile BIN file for {config_name} could not be found or built within 60s.", 504

@app.route('/configurations/<config_name>/download-firmware-bin')
def download_firmware(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"
    
    fw_version = request.args.get('fw_version', 'v5.4.10').strip()

    file_path = get_or_build_firmware_bin(config, fw_version, user_email)
    
    if file_path and os.path.exists(file_path):
        return send_file(
            file_path, 
            as_attachment=True, 
            download_name=f"{config.config_name}_{fw_version}_firmware.bin"
        )

    return f"Firmware BIN file for {config_name} ({fw_version}) could not be found or built within 60s.", 504

@app.route('/configurations/<config_name>/check-rs485', endpoint='check_rs485')
def check_rs485(config_name):
    port = find_rs485_port()
    if port:
        return jsonify({"connected": True, "port": port})
    return jsonify({"connected": False, "error": "No RS-485 port found. Please connect your RS-485 adapter."}), 404

@app.route('/configurations/<config_name>/flash-stream', endpoint='flash_stream')
def flash_stream(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"
    fw_version = request.args.get('fw_version', 'v5.4.10').strip()

    def generate_events():
        progress_queue = []

        def progress_cb(percent):
            progress_queue.append(percent)

        yield f"data: {json.dumps({'type': 'status', 'text': 'Retrieved firmware. Connecting to RS-485 port...'})}\n\n"

        import threading
        result_holder = {}

        def run_flash():
            success, msg = flash_firmware_osdp(config, fw_version, user_email, progress_callback=progress_cb)
            result_holder['success'] = success
            result_holder['msg'] = msg

        thread = threading.Thread(target=run_flash)
        thread.start()

        last_percent = 0
        while thread.is_alive() or progress_queue:
            if progress_queue:
                p = progress_queue.pop(0)
                if p != last_percent:
                    last_percent = p
                    yield f"data: {json.dumps({'type': 'progress', 'percent': p})}\n\n"
            time.sleep(0.05)

        thread.join()

        success = result_holder.get('success', False)
        msg = result_holder.get('msg', 'Flashing failed.')

        try:
            rev = RevisionHistory(
                config_name=config.config_name,
                user_email=user_email,
                revision_details=f"OSDP Flash ({fw_version}). Result: {'Success' if success else 'Failed'} - {msg}"
            )
            db.session.add(rev)
            db.session.commit()
        except Exception as e:
            print(f"[REVISION LOG ERROR] {e}")

        yield f"data: {json.dumps({'type': 'complete', 'success': success, 'msg': msg})}\n\n"

    return Response(stream_with_context(generate_events()), mimetype='text/event-stream')

@app.route('/configurations/<config_name>/flash', endpoint='flash_configuration')
def flash_configuration(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"
    fw_version = request.args.get('fw_version', 'v5.4.10').strip()

    if not find_rs485_port():
        flash("No RS-485 port found on host system. Please connect an RS-485 serial adapter.", "error")
        return redirect(url_for('configuration_details', config_name=config_name))

    success, message = flash_firmware_osdp(config, fw_version, user_email)

    if success:
        flash(f"{message}", "success")
    else:
        flash(f"OSDP Flash Failed: {message}", "error")

    return redirect(url_for('configuration_details', config_name=config_name))

@app.route('/configurations/<config_name>/get-tokens', endpoint='get_tokens')
def get_tokens(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"
    fw_version = request.args.get('fw_version', 'v5.4.10').strip()

    tokens_result = process_get_tokens(config, fw_version, user_email)

    return jsonify({
        "success": True,
        "profile_name": tokens_result["profile_name"],
        "profile_token": tokens_result["profile_token"],
        "firmware_name": tokens_result["firmware_name"],
        "firmware_token": tokens_result["firmware_token"]
    })

@app.route("/configurations/<int:config_id>/toggle_status", methods=["POST"])
def toggle_config_status(config_id):
    user_email = session.get("user_email")
    user = User.query.filter_by(email=user_email).first() if user_email else None
    
    if not user or user.role not in ["Sales", "SET"]:
        flash("You do not have permission to change configuration statuses.", "danger")
        return redirect(url_for("configuration_details", config_id=config_id))
    
    config = Configuration.query.get_or_404(config_id)
    
    if config.status == "POC":
        config.previous_status = "POC"
        config.status = "Active"
        flash_msg = "Configuration status changed to Active."
    elif config.status == "Active":
        config.previous_status = "Active"
        config.status = "POC"
        flash_msg = "Configuration status changed to POC."
    else:
        flash("Sales users can only toggle between POC and Active statuses.", "warning")
        return redirect(url_for("configuration_details", config_id=config_id))
    
    flash(flash_msg, "success")
    return redirect(url_for("configuration_details", config_id=config_id))

@app.route('/configurations/<config_name>/update', methods=['POST'])
def update_configuration(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    current_user = inject_user()['current_user']

    if not current_user or current_user.role not in ['Sales']:
        flash("You do not have permission to edit configuration details.", "danger")
        return redirect(url_for('configuration_details', config_name=config_name))

    if config.status != 'POC':
        flash("Only POC configurations can be edited.", "warning")
        return redirect(url_for('configuration_details', config_name=config_name))

    editable_fields = [
        'idle_led', 'credential_report_led', 'beeper', 'keypad_format',
        'tamper_monitoring', 'casi_output_format', 'supervision_state',
        'card_type', 'facility_code', 'starting_badge', 'bitstream',
        'fsk_prox', 'ask_prox', 'prox_filter', 'prox_filter_description',
        'ble_functionality', 'nfc_functionality', 'mobile_keyset',
        'legacy_credentials', 'transport_mode',
        'mfc_csn', 'ev1_ev2_csn', 'iclass_csn', 'iso_15693_csn', 'iso_14443a_csn'
    ]

    def normalize(val):
        """Normalizes None, 'None', empty strings, and whitespace for clean diffing."""
        if val is None:
            return ""
        s = str(val).strip()
        if s.lower() in ["none", "null", "-"]:
            return ""
        return s

    changes = []
    for field in editable_fields:
        if field in request.form:
            old_raw = getattr(config, field)
            new_raw = request.form.get(field, "").strip()

            old_norm = normalize(old_raw)
            new_norm = normalize(new_raw)

            if field in ['facility_code', 'starting_badge']:
                new_val = int(new_raw) if new_raw.isdigit() else None
            else:
                new_val = new_raw if new_norm != "" else None

            if old_norm != new_norm:
                setattr(config, field, new_val)
                disp_old = old_raw if old_raw is not None else "None"
                disp_new = new_val if new_val is not None else "None"
                changes.append(f"{field.replace('_', ' ').title()}: '{disp_old}' → '{disp_new}'")

    if changes:
        rev = RevisionHistory(
            config_name=config.config_name,
            user_email=current_user.email if current_user else "system@wavelynx.com",
            revision_details=f"Updated configuration details: {', '.join(changes)}"
        )
        db.session.add(rev)
        db.session.commit()
        flash("Configuration updated successfully!", "success")
    else:
        flash("No changes detected.", "info")

    return redirect(url_for('configuration_details', config_name=config_name))

@app.route('/configurations/new', methods=['GET'])
def new_configuration():
    current_user = inject_user()['current_user']
    if not current_user or current_user.role not in ['Sales']:
        flash("You do not have permission to create new configurations.", "danger")
        return redirect(url_for('configurations_list'))

    max_dec = db.session.query(
        func.max(db.cast(db.func.nullif(Configuration.config_id_dec, ''), db.Integer))
    ).scalar() or 0

    next_id_dec = max_dec + 1
    next_id_hex = f"0x{next_id_dec:04X}"

    config = Configuration(
        config_name=f"CONFIG_{next_id_dec}",
        config_id_dec=str(next_id_dec),
        config_id_hex=next_id_hex,
        status="POC"
    )

    return render_template('create_configuration.html', config=config, is_new=True)

@app.route('/configurations/create', methods=['POST'])
def create_configuration():
    current_user = inject_user()['current_user']
    if not current_user or current_user.role not in ['Sales']:
        flash("You do not have permission to create configurations.", "danger")
        return redirect(url_for('configurations_list'))

    config_name = request.form.get('config_name', '').strip()
    if not config_name:
        flash("Configuration name is required.", "danger")
        return redirect(url_for('new_configuration'))

    if Configuration.query.filter_by(config_name=config_name).first():
        flash(f"A configuration with name '{config_name}' already exists.", "danger")
        return redirect(url_for('new_configuration'))

    max_dec = db.session.query(
        func.max(db.cast(db.func.nullif(Configuration.config_id_dec, ''), db.Integer))
    ).scalar() or 0

    next_id_dec = max_dec + 1
    next_id_hex = f"0x{next_id_dec:04X}"

    new_config = Configuration(
        config_name=config_name,
        config_id_dec=str(next_id_dec),
        config_id_hex=next_id_hex,
        status="POC",
        previous_status="POC",
        idle_led=request.form.get('idle_led'),
        credential_report_led=request.form.get('credential_report_led'),
        beeper=request.form.get('beeper'),
        keypad_format=request.form.get('keypad_format'),
        tamper_monitoring=request.form.get('tamper_monitoring'),
        casi_output_format=request.form.get('casi_output_format'),
        supervision_state=request.form.get('supervision_state'),
        card_type=request.form.get('card_type') or None,
        facility_code=int(request.form.get('facility_code')) if request.form.get('facility_code', '').isdigit() else None,
        starting_badge=request.form.get('starting_badge') or None,
        bitstream=request.form.get('bitstream') or None,
        fsk_prox=request.form.get('fsk_prox'),
        ask_prox=request.form.get('ask_prox'),
        prox_filter=request.form.get('prox_filter'),
        prox_filter_description=request.form.get('prox_filter_description') or None,
        ble_functionality=request.form.get('ble_functionality'),
        nfc_functionality=request.form.get('nfc_functionality'),
        mobile_keyset=request.form.get('mobile_keyset'),
        legacy_credentials=request.form.get('legacy_credentials'),
        transport_mode=request.form.get('transport_mode'),
        mfc_csn=request.form.get('mfc_csn'),
        ev1_ev2_csn=request.form.get('ev1_ev2_csn'),
        iclass_csn=request.form.get('iclass_csn'),
        iso_15693_csn=request.form.get('iso_15693_csn'),
        iso_14443a_csn=request.form.get('iso_14443a_csn')
    )

    db.session.add(new_config)

    rev = RevisionHistory(
        config_name=config_name,
        user_email=current_user.email if current_user else "system@wavelynx.com",
        revision_details=f"Created new configuration (ID Dec: {next_id_dec}, Hex: {next_id_hex}) in POC status."
    )
    db.session.add(rev)
    db.session.commit()

    flash(f"Configuration '{config_name}' successfully created!", "success")
    return redirect(url_for('configuration_details', config_name=config_name))

@app.route('/tools', strict_slashes=False)
def tools():
    tools_list = [
        {
            'title': 'Partial Configuration Generator',
            'endpoint': 'tool_partial_config',
            'description': 'Generate a custom Partial Configuration INI file and compile its corresponding Profile Bin file.'
        },
        {
            'title': 'INI Translator',
            'endpoint': 'tool_ini_translator',
            'description': 'Upload any raw reader INI configuration file to translate technical key-value settings into human-readable English.'
        },
        {
            'title': 'YAML Automator',
            'endpoint': 'tool_yaml_automator',
            'description': 'Automate and streamline the creation of structured YAML configuration profiles for SAM Chips.'
        },
        {
            'title': 'Batch Tokens',
            'endpoint': 'tool_batch_tokens',
            'description': 'Batch retrieve and synchronize multiple Profile and Firmware configuration tokens from PocketBase simultaneously.'
        }
    ]
    return render_template('tools.html', tools=tools_list)

@app.route('/tools/partial-config', methods=['GET', 'POST'], strict_slashes=False)
def tool_partial_config():
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    if request.method == 'POST':
        fw_version = request.form.get('fw_version', 'v5.4.10').strip()
        build_bin = request.form.get('build_bin_choice') == 'true'

        form_data = request.form.to_dict()
        partial_ini_text = generate_partial_ini_content(form_data)

        if not partial_ini_text:
            flash("No parameters were changed. Select at least one override.", "warning")
            return redirect(url_for('tool_partial_config'))

        changed_keys = [k for k, v in form_data.items() if v and k not in ['fw_version', 'build_bin_choice']]
        name_summary = "_".join(changed_keys[:3]) if changed_keys else "override"
        partial_name = f"partial_{name_summary}"
        ini_filename = f"{partial_name}.ini"

        gcs_path = upload_partial_ini_to_gcs(ini_filename, partial_ini_text, fw_version, user_email)

        if not gcs_path:
            flash("Failed to upload partial INI to Cloud Storage.", "danger")
            return redirect(url_for('tool_partial_config'))

        if build_bin:
            forge_triggered = compile_partial_bin_via_forge(partial_name, fw_version, user_email)
            if forge_triggered:
                flash(f"Partial INI uploaded & Forge Profile BIN build triggered for '{partial_name}'!", "success")
            else:
                flash(f"Partial INI uploaded to GCS, but Forge build trigger failed.", "warning")

        local_ini_path = f"/tmp/{ini_filename}"
        with open(local_ini_path, "w") as f:
            f.write(partial_ini_text)

        return send_file(local_ini_path, as_attachment=True, download_name=ini_filename)

    return render_template('tools/partial_config.html')

@app.route('/tools/ini-translator', methods=['GET', 'POST'], strict_slashes=False)
def tool_ini_translator():
    translation_result = None

    if request.method == 'POST':
        file = request.files.get('ini_file')

        if file and file.filename != '':
            ini_content = file.read().decode('utf-8', errors='ignore')
            translation_result = translate_uploaded_ini(ini_content)
            
            if translation_result.get("error"):
                flash(translation_result["error"], "danger")
            else:
                flash(f"Successfully translated '{file.filename}' using default settings context.", "success")
        else:
            flash("Please upload an INI file to translate.", "warning")

    return render_template('tools/ini_translator.html', result=translation_result)

@app.route('/tools/yaml-automator', methods=['GET', 'POST'], strict_slashes=False)
def tool_yaml_automator():
    global OEM_ID_MAP

    if request.method == 'POST':
        sam_choice = request.form.get('sam_type_choice')
        sam_info = SAM_TYPE_MAP.get(sam_choice, {"label": "SAM_AV3", "id": 3})

        partner_choice = request.form.get('partner_choice')
        partner_info = PART_MAP.get(partner_choice, {"label": "PARTNER_RFIDEAS", "id": 2})

        partner_km_choice = request.form.get('partner_km_choice')
        partner_km_info = PART_KM_MAP.get(partner_km_choice, {"label": "partnerKm2", "id": 2})

        raw_oem_input = request.form.get('oem', '').strip()
        oem_clean = raw_oem_input.replace(' ', '_').upper()
        if oem_clean and oem_clean not in OEM_ID_MAP:
            next_id = max(OEM_ID_MAP.values()) + 1
            OEM_ID_MAP[oem_clean] = next_id
        sku_oem_id = OEM_ID_MAP.get(oem_clean, 62)

        tci_raw = str(request.form.get('tci_choice', '0x000000')).strip().lower().replace("0x", "").zfill(6)
        tci_1 = HexInt(int(tci_raw[0:2], 16))
        tci_2 = HexInt(int(tci_raw[2:4], 16))
        tci_3 = HexInt(int(tci_raw[4:6], 16))

        specialty_keys_config = None
        if request.form.get('specialty_keys_choice') == "true":
            version_input = int(request.form.get('key_version', '0'))
            version_labels = {0: "key_version_a", 1: "key_version_b", 2: "key_version_c"}
            type_label, type_hex = KEY_TYPE_MAP.get(request.form.get('key_type_choice'), ("SAM_MIFARE_KEY", 0x10))
            use_label, use_id = KEY_USE_MAP.get(request.form.get('key_use_choice'), ("SAM_PICC_KEY", 1))

            specialty_keys_config = {
                "key2": {
                    "key_slot": int(request.form.get('key_slot', 64)),
                    "key_version": [{version_labels.get(version_input, "key_version_a"): version_input}],
                    "sel_key_version": version_input,
                    "key_type": [{type_label: HexInt(type_hex)}],
                    "sel_key_type": HexInt(type_hex),
                    "key_use": [{use_label: use_id}],
                    "sel_key_use": use_id
                }
            }

        m2g_choice = request.form.get('mifare_2go_app_id_choice')
        m2g_msb, m2g_hsb, m2g_lsb = (0xF5, 0x1C, 0xDF) if m2g_choice == "leaf" else (0xF5, 0x32, 0xF0)

        term_label, term_hex = ECP_TERMINAL_MAP.get(request.form.get('ecp_terminal_choice'), ("ECP_WL_TERMINAL_INFO", 0xC3))
        sub_label, sub_id = ECP_SUBTYPE_MAP.get(request.form.get('ecp_terminal_subtype_choice'), ("ECP_TERMINAL_SUBTYPE_CORPORATE", 2))
        app_label, app_id = ECP_APP_MAP.get(request.form.get('ecp_app_choice'), ("ECP_OPTIONS_1", 1))
        bit_count_raw = request.form.get('ecp_bit_count_choice')

        yaml_structure = {
            "sam_type": [{"SAM_AV2": 2}, {"SAM_AV3": 3}],
            "sel_sam_type": sam_info["id"],
            "partner": [{"PARTNER_ELATEC": 1}, {"PARTNER_RFIDEAS": 2}, {"PARTNER_BRIVO": 3}, {"PARTNER_TEST": 4}],
            "sel_partner": partner_info["id"],
            "partner_km": [
                {"partnerKm0": 0}, {"partnerKm1": 1}, {"partnerKm2": 2}, {"partnerKm3": 3},
                {"partnerKm4": 4}, {"partnerKm5": 5}, {"partnerKm6": 6}, {"partnerKm7": 7},
                {"partnerKm8": 8}, {"partnerKm9": 9}, {"partnerKm10": 10}, {"partnerKm11": 11},
                {"partnerKm12": 12}, {"partnerKm13": 13}, {"partnerKm14": 14}, {"partnerKm15": 15}
            ],
            "sel_partner_km": partner_km_info["id"],
            "sku_oem": [{f"SKU_{k}": v} for k, v in OEM_ID_MAP.items()],
            "sel_sku_oem": sku_oem_id,
            "sel_Wl_lk": request.form.get('leaf_si_choice') == "true",
            "OEM_lk1_name": request.form.get('leaf_cc_choice') or "Lk50003",
            "OEM_lk2_name": request.form.get('second_leaf_cc_choice') or None,
            "specialty_keys": specialty_keys_config,
            "tci_1": tci_1,
            "tci_2": tci_2,
            "tci_3": tci_3,
            "ecp_format": [{"ECP_FORMAT_1": 1}, {"ECP_FORMAT_2": 2}],
            "sel_ecp_format": 2,
            "ecp_terminal_info_mode": [
                {"ECP_WL_TERMINAL_INFO": HexInt(0xC3)},
                {"ECP_WL_TERMINAL_INFO_USER_AUTH": HexInt(0x83)}
            ],
            "sel_ecp_terminal_info_mode": HexInt(term_hex),
            "ecp_terminal_type": 2,
            "ecp_terminal_subtype": [
                {"ECP_TERMINAL_SUBTYPE_UNIVERSITY": 0},
                {"ECP_TERMINAL_SUBTYPE_CORPORATE": 2},
                {"ECP_TERMINAL_SUBTYPE_HOSPITALITY": 3},
                {"ECP_TERMINAL_SUBTYPE_RESIDENTIAL": 4}
            ],
            "sel_ecp_terminal_subtype": sub_id,
            "ecp_bit_count": int(bit_count_raw) if bit_count_raw else 40,
            "ecp_app_options": [{"ECP_OPTIONS_1": 1}, {"ECP_OPTIONS_2": 2}],
            "sel_ecp_app_options": app_id,
            "M2G_AID_MSB": HexInt(m2g_msb),
            "M2G_AID_HSB": HexInt(m2g_hsb),
            "M2G_AID_LSB": HexInt(m2g_lsb),
            "PF_func": [
                {"PF_FUNC_NO_FILTER": 0}, {"PF_FUNC_EQ": 1},
                {"PF_FUNC_ME": 2}, {"PF_FUNC_LE": 3}, {"PF_FUNC_NE": 4}
            ],
            "sel_PF_func": 0,
            "PF_num_bits": 0,
            "filter_all": False,
            "PF_value": 0,
            "bit_stream": None,
            "filtered_field_tag": [
                {"UNUSED": 0}, {"BS_BID_MAP": HexInt(0xB2)},
                {"BS_FAC_MAP": HexInt(0xB3)}, {"BS_CITY_CODE_MAP": HexInt(0xB5)},
                {"BS_TECH_NUM_MAP": HexInt(0xB6)}
            ],
            "sel_filtered_field_tag": 0
        }

        yaml_string = yaml.dump(yaml_structure, default_flow_style=False, sort_keys=False)
        section_breaks = [
            "\npartner:", "\npartner_km:", "\nsku_oem:", "\nsel_Wl_lk:",
            "\nspecialty_keys:", "\ntci_1:", "\necp_format:",
            "\necp_terminal_info_mode:", "\necp_terminal_type:",
            "\necp_terminal_subtype:", "\necp_bit_count:", "\necp_app_options:",
            "\nM2G_AID_MSB:", "\nPF_func:", "\nPF_num_bits:", "\nfilter_all:",
            "\nPF_value:", "\nbit_stream:", "\nfiltered_field_tag:"
        ]
        for section in section_breaks:
            yaml_string = yaml_string.replace(f"{section}", f"\n{section}")

        oem_filename_part = oem_clean if oem_clean else "CONFIG"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        custom_filename = f"SAM_Config_{oem_filename_part}_{timestamp}.yml"

        output_path = f"/tmp/{custom_filename}"
        with open(output_path, 'w') as f:
            f.write(yaml_string)

        return send_file(output_path, as_attachment=True, download_name=custom_filename)

    return render_template('tools/yaml_automator.html')

@app.route('/tools/batch-tokens', methods=['GET', 'POST'], strict_slashes=False)
def tool_batch_tokens():
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    configurations = Configuration.query.filter(Configuration.status != 'Archived').order_by(
        db.cast(db.func.nullif(Configuration.config_id_dec, ''), db.Integer).asc().nulls_last()
    ).all()

    tokens_results = []
    selected_fw = request.form.get('fw_version', 'v5.4.10').strip() if request.method == 'POST' else 'v5.4.10'
    selected_config_names = request.form.getlist('config_names') if request.method == 'POST' else []

    if request.method == 'POST' and selected_config_names:
        configs_to_process = Configuration.query.filter(Configuration.config_name.in_(selected_config_names)).all()

        def process_single_config(config_obj):
            try:
                res = process_get_tokens(config_obj, selected_fw, user_email)
                return {
                    "config_name": config_obj.config_name,
                    "status": config_obj.status,
                    "profile_name": res.get("profile_name"),
                    "profile_token": res.get("profile_token"),
                    "firmware_name": res.get("firmware_name"),
                    "firmware_token": res.get("firmware_token"),
                    "success": True,
                    "error": None
                }
            except Exception as e:
                return {
                    "config_name": config_obj.config_name,
                    "status": config_obj.status,
                    "profile_name": f"{selected_fw.replace('v', '')} {config_obj.config_name} PROFILE",
                    "profile_token": "Error",
                    "firmware_name": f"{selected_fw.replace('v', '')} {config_obj.config_name} FIRMWARE",
                    "firmware_token": "Error",
                    "success": False,
                    "error": str(e)
                }

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            tokens_results = list(executor.map(process_single_config, configs_to_process))

        flash(f"Successfully processed batch tokens for {len(tokens_results)} configurations.", "success")

    return render_template(
        'tools/batch_tokens.html',
        configurations=configurations,
        tokens_results=tokens_results,
        selected_fw=selected_fw,
        selected_config_names=selected_config_names
    )

@app.route('/testing', methods=['GET'], strict_slashes=False)
def testing_dashboard():
    current_fw = request.args.get('fw_version', 'v5.4.10').strip()
    
    runs = FWRun.query.filter_by(fw_version=current_fw).order_by(FWRun.run_date.desc()).all()
    selectable_configs = Configuration.query.filter(Configuration.status != 'Archived').order_by(Configuration.config_name.asc()).all()

    return render_template(
        'testing.html',
        current_fw=current_fw,
        runs=runs,
        selectable_configs=selectable_configs
    )

@app.route('/testing/start-run', methods=['POST'])
def start_new_run():
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    fw_version = request.form.get('fw_version', 'v5.4.10').strip()
    selected_config_names = request.form.getlist('config_names')

    if not selected_config_names:
        flash("Please select at least one configuration to start a firmware run.", "warning")
        return redirect(url_for('testing_dashboard', fw_version=fw_version))

    new_run = FWRun(fw_version=fw_version)
    db.session.add(new_run)
    db.session.flush()

    for config_name in selected_config_names:
        config_obj = Configuration.query.filter_by(config_name=config_name).first()
        if not config_obj:
            continue

        test_group = ConfigurationTestGroup(
            fw_run_id=new_run.id,
            config_name=config_name,
            status='Untested'
        )
        db.session.add(test_group)
        db.session.flush()

        action_categories = [
            {
                "category": "ACTION: Verify AV and Output Formats.",
                "items": [
                    ("Idle LED", config_obj.idle_led or "Off"),
                    ("Credential Report LED", config_obj.credential_report_led or "Off"),
                    ("Beeper", config_obj.beeper or "Off"),
                    ("Keypad Format", config_obj.keypad_format or "8-bit"),
                    ("Tamper Monitoring", config_obj.tamper_monitoring or "Off"),
                    ("Config ID", f"{config_obj.config_id_dec or '0'} ({config_obj.config_id_hex or '0x00'})")
                ]
            },
            {
                "category": "ACTION: Present the HF credential.",
                "items": [
                    ("Leaf Si Application (Kv1)", "Leaf Si" if config_obj.card_type in ["Leaf Si", "Dual"] else "None"),
                    ("Leaf Cc Application (Kc1)", "Leaf Cc" if config_obj.card_type in ["Leaf Cc", "Dual"] else "None"),
                    ("Other Custom HF Application", "None")
                ]
            },
            {
                "category": "ACTION: Verify Card Serial Number (CSN) credentials.",
                "items": [
                    ("MFC CSN", config_obj.mfc_csn or "Off"),
                    ("EV1/EV2 CSN", config_obj.ev1_ev2_csn or "Off"),
                    ("iClass CSN", config_obj.iclass_csn or "Off"),
                    ("ISO 15693 CSN", config_obj.iso_15693_csn or "Off"),
                    ("ISO 14443A", config_obj.iso_14443a_csn or "Off")
                ]
            },
            {
                "category": "ACTION: Verify Low Frequency (LF) credentials.",
                "items": [
                    ("FSK Prox", config_obj.fsk_prox or "Off"),
                    ("ASK Prox", config_obj.ask_prox or "Off"),
                    ("Prox Filter", config_obj.prox_filter or "Off"),
                    ("Prox Filter Description", config_obj.prox_filter_description or "None")
                ]
            },
            {
                "category": "ACTION: Verify Mobile configurations.",
                "items": [
                    ("BLE Advertising Config", "Unique (Differs)"),
                    ("BLE Functionality", config_obj.ble_functionality or "Admin + Credentials"),
                    ("NFC Functionality", config_obj.nfc_functionality or "Enabled"),
                    ("MyPass Keyset", config_obj.mobile_keyset or "Standard"),
                    ("Legacy Credentials", config_obj.legacy_credentials or "Off"),
                    ("Wallet", "Off")
                ]
            }
        ]

        for cat in action_categories:
            cat_header = cat["category"]
            for label, val_str in cat["items"]:
                criterion_label = f"{cat_header} | {label}:{val_str}"
                tc = TestCase(test_group_id=test_group.id, criterion_name=criterion_label, status='Untested')
                db.session.add(tc)

    db.session.commit()
    flash(f"Started new FW {fw_version} run with Squash-style test suites.", "success")
    return redirect(url_for('testing_dashboard', fw_version=fw_version))

@app.route('/testing/assign-group/<int:group_id>', methods=['POST'])
def assign_test_group(group_id):
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    action = request.form.get('action', 'assign')

    if action == 'assign':
        group.assigned_user = user_email
        flash(f"Assigned test group '{group.config_name}' to {user_email}.", "info")
    else:
        group.assigned_user = None
        flash(f"Unassigned test group '{group.config_name}'.", "info")

    db.session.commit()
    return redirect(url_for('testing_dashboard', fw_version=group.fw_run.fw_version))

@app.route('/testing/submit-group/<int:group_id>', methods=['POST'])
def submit_test_group(group_id):
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    fw_run = group.fw_run

    if not group.assigned_user:
        flash("Cannot submit test group until a tester is assigned.", "danger")
        return redirect(url_for('testing_dashboard', fw_version=fw_run.fw_version))

    has_untested = False
    failed_cases = []

    for tc in group.test_cases:
        status = request.form.get(f"status_{tc.id}")
        comment = request.form.get(f"comment_{tc.id}", "").strip()

        if status == "Failed" and not comment:
            flash(f"Failure comments are required for failing criteria: '{tc.criterion_name}'", "danger")
            return redirect(url_for('testing_dashboard', fw_version=fw_run.fw_version))

        if status:
            tc.status = status
            tc.comment = comment

        if tc.status == 'Untested':
            has_untested = True
        elif tc.status == 'Failed':
            failed_cases.append(tc)

    if has_untested:
        flash("All criteria must be evaluated (Passed or Failed) before submitting.", "warning")
        return redirect(url_for('testing_dashboard', fw_version=fw_run.fw_version))

    config_obj = Configuration.query.filter_by(config_name=group.config_name).first()

    if failed_cases:
        group.status = 'Failed'
        fw_run.failed_total += 1

        if config_obj and config_obj.status == 'Production':
            config_obj.status = config_obj.previous_status or 'Active'

        crit_details = ", ".join([f"{c.criterion_name} ({c.comment})" for c in failed_cases])
        
        created_key = create_failure_ticket(group.config_name, user_email, fw_run.fw_version, crit_details)
        group.jira_key = created_key
        
        send_failure_message(group.config_name, user_email, fw_run.fw_version, crit_details)
        flash(f"Test group '{group.config_name}' marked as FAILED. Jira ticket {created_key} created.", "danger")

    else:
        group.status = 'Passed'
        fw_run.passed_total += 1

        if config_obj:
            config_obj.previous_status = config_obj.status
            config_obj.status = 'Production'
            flash(f"Test group '{group.config_name}' PASSED! Configuration promoted to Production.", "success")

    db.session.commit()
    return redirect(url_for('testing_dashboard', fw_version=fw_run.fw_version))

@app.route('/failures', methods=['GET'], strict_slashes=False)
def failures_dashboard():
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"
    
    if not current_user or current_user.role != 'SET':
        flash("Access Restricted: The Failures dashboard is only accessible to SET team members.", "danger")
        return redirect(url_for('home'))

    failed_groups = ConfigurationTestGroup.query.filter_by(status='Failed').order_by(ConfigurationTestGroup.id.desc()).all()

    failures_data = []
    for group in failed_groups:
        fw_run = db.session.get(FWRun, group.fw_run_id)
        failed_cases = [tc for tc in group.test_cases if tc.status == 'Failed']
        
        fw_ver = fw_run.fw_version if fw_run else 'v5.4.10'
        ini_content = get_failure_ini_content(group.config_name, fw_ver, user_email)

        failures_data.append({
            'group_id': group.id,
            'config_name': group.config_name,
            'fw_version': fw_ver,
            'run_date': fw_run.run_date if fw_run else None,
            'tester_user': group.assigned_user,
            'claimed_user': group.claimed_user, 
            'failed_cases': failed_cases,
            'failure_count': len(failed_cases),
            'jira_key': f"SWAG-{group.id + 100}",
            'ini_content': ini_content
        })

    return render_template(
        'failures.html',
        failures=failures_data,
        total_failures=len(failures_data)
    )

@app.route('/failures/retest/<int:group_id>', methods=['POST'])
def retest_failure(group_id):
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    fw_run = db.session.get(FWRun, group.fw_run_id)
    fw_ver = fw_run.fw_version if fw_run else 'v5.4.10'

    target_key = group.jira_key or "SWAG-126"

    group.status = 'Untested'
    group.assigned_user = None
    group.claimed_user = None

    for tc in group.test_cases:
        tc.status = 'Untested'
        tc.comment = None

    db.session.commit()

    transition_jira_issue_to_complete(target_key)

    send_alert_qa_notification(group.config_name, "Re-Testing Requested", user_email, fw_ver)

    flash(f"Reset test suite for '{group.config_name}'. Jira ticket {target_key} moved to 'Complete' and Slack alert dispatched.", "success")
    return redirect(url_for('failures_dashboard'))

@app.route('/failures/flash-and-run/<int:group_id>', methods=['POST'])
def failure_flash_and_run(group_id):
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    fw_run = FWRun.query.get(group.fw_run_id)
    fw_ver = fw_run.fw_version if fw_run else 'v5.4.10'

    ini_content = request.form.get('ini_content', '').strip()
    baud_rate = request.form.get('baud_rate', '115200')
    osdp_addr = request.form.get('osdp_address', '0')

    if ini_content:
        save_edited_failure_ini(group.config_name, ini_content, fw_ver, user_email)

    config_obj = Configuration.query.filter_by(config_name=group.config_name).first()
    if not config_obj:
        flash(f"Configuration object for '{group.config_name}' not found.", "danger")
        return redirect(url_for('failures_dashboard'))

    fw_bin_path = get_or_build_firmware_bin(config_obj, fw_ver, user_email)

    if not fw_bin_path:
        flash("Failed to retrieve or compile firmware BIN from Forge.", "danger")
        return redirect(url_for('failures_dashboard'))

    success, msg = flash_reader_osdp(fw_bin_path, baud_rate=int(baud_rate), address=int(osdp_addr))

    if success:
        flash(f"Successfully flashed reader for '{group.config_name}' with FW {fw_ver}!", "success")
    else:
        flash(f"OSDP Flash Failed: {msg}", "danger")

    return redirect(url_for('failures_dashboard'))

@app.route('/failures/save-ini/<int:group_id>', methods=['POST'])
def save_failure_ini(group_id):
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    fw_run = FWRun.query.get(group.fw_run_id)
    fw_ver = fw_run.fw_version if fw_run else 'v5.4.10'

    edited_ini = request.form.get('ini_content', '').strip()

    if not edited_ini:
        flash("INI content cannot be empty.", "warning")
        return redirect(url_for('failures_dashboard'))

    success = save_edited_failure_ini(group.config_name, edited_ini, fw_ver, user_email)

    if success:
        flash(f"Updated INI for '{group.config_name}' saved to Forge bucket (`forge/{user_email}/{fw_ver}/`)!", "success")
    else:
        flash("Failed to save edited INI to Cloud Storage.", "danger")

    return redirect(url_for('failures_dashboard'))

@app.route('/failures/claim/<int:group_id>', methods=['POST'])
def claim_failure(group_id):
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    group = ConfigurationTestGroup.query.get_or_404(group_id)
    group.claimed_user = user_email
    db.session.commit()

    target_key = group.jira_key or "SWAG-126"
    jira_assigned = assign_jira_ticket(issue_key=target_key, assignee_email=user_email)

    if jira_assigned:
        flash(f"Claimed failure for '{group.config_name}' and assigned Jira ticket {target_key} to {user_email}.", "success")
    else:
        flash(f"Claimed failure locally for '{group.config_name}', but could not update Jira ticket assignment.", "warning")

    return redirect(url_for('failures_dashboard'))

@app.route('/failures/unclaim/<int:group_id>', methods=['POST'])
def unclaim_failure(group_id):
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "jhepburn@wavelynx.com"

    group = ConfigurationTestGroup.query.get_or_404(group_id)

    if group.claimed_user == user_email:
        group.claimed_user = None
        db.session.commit()

        target_key = group.jira_key or "SWAG-126"
        assign_jira_ticket(issue_key=target_key, assignee_email=None)

        flash(f"Unclaimed failure for '{group.config_name}' and unassigned Jira ticket {target_key}.", "info")
    else:
        flash("You can only unclaim failures that you currently own.", "warning")

    return redirect(url_for('failures_dashboard'))

@app.route('/configurations/archive/<config_name>', methods=['POST'])
def archive_configuration(config_name):
    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "sales@wavelynx.com"
    user_role = current_user.role if current_user else "Sales"

    if user_role not in ['Sales']:
        flash("You do not have permission to archive configurations.", "danger")
        return redirect(url_for('configuration_details', config_name=config_name))

    config_obj = Configuration.query.filter_by(config_name=config_name).first_or_404()

    if config_obj.status == 'Archived':
        flash(f"Configuration '{config_name}' is already archived.", "info")
        return redirect(url_for('configuration_details', config_name=config_name))

    config_obj.previous_status = config_obj.status
    config_obj.status = 'Archived'

    rev = RevisionHistory(
        config_name=config_name,
        user_email=user_email,
        revision_details=f"Configuration status changed from '{config_obj.previous_status}' to 'Archived'."
    )
    db.session.add(rev)
    db.session.commit()

    flash(f"Configuration '{config_name}' archived successfully.", "success")
    return redirect(url_for('configuration_details', config_name=config_name))

if __name__ == '__main__':
    app.run(debug=True, port=5000)