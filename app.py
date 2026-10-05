import os
import time
from sqlalchemy import func
from flask import Flask, render_template, session, request, redirect, url_for, send_file, request, Response, stream_with_context, jsonify, flash, json
from models import db, User, Configuration, RevisionHistory
from dotenv import load_dotenv
from services.slack_service import send_alert_qa_notification
from services.gcs_service import get_or_create_ini_file, get_or_build_profile_bin, get_or_build_firmware_bin
from services.osdp_service import flash_firmware_osdp, find_rs485_port
from services.pocketbase_service import process_get_tokens

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'caliga-dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

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

if __name__ == '__main__':
    app.run(debug=True, port=5000)