import io
import os
import re
import datetime
import tempfile
import concurrent.futures
import yaml
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from sqlalchemy import func
from models import db, Configuration, OemSku
from auth import require_role, get_current_user
from jobs import start_job, JobError
from services.gcs_service import (get_or_create_ini_file, get_or_build_profile_bin, get_or_build_firmware_bin,
                                  upload_partial_ini_to_gcs)
from services.osdp_service import flash_firmware_osdp, find_rs485_port
from services.pocketbase_service import lookup_tokens, sync_tokens
from services.ini_translator_service import translate_uploaded_ini
from services.forge_service import generate_partial_ini_content, compile_partial_bin_via_forge
from routes.helpers import current_email, get_usable_config, json_error, log_revision, send_and_cleanup
from utils.versions import parse_fw_version

bp = Blueprint('tools', __name__)

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

PARTIAL_META_FIELDS = ('fw_version', 'build_bin_choice', 'csrf_token')


class HexInt(int):
    pass


def hex_int_representer(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:int', f'0x{data:02X}')


yaml.add_representer(HexInt, hex_int_representer)


def _start(kind, fn, *args):
    job = start_job(kind, get_current_user().id, fn, *args)
    return jsonify({"job_id": job.id}), 202


@bp.route('/tools', strict_slashes=False)
def tools():
    tools_list = [
        {
            'title': 'Partial Configuration Generator',
            'endpoint': 'tools.tool_partial_config',
            'description': 'Generate a custom Partial Configuration INI file and compile its corresponding Profile Bin file.'
        },
        {
            'title': 'INI Translator',
            'endpoint': 'tools.tool_ini_translator',
            'description': 'Upload any raw reader INI configuration file to translate technical key-value settings into human-readable English.'
        },
        {
            'title': 'YAML Automator',
            'endpoint': 'tools.tool_yaml_automator',
            'description': 'Automate and streamline the creation of structured YAML configuration profiles for SAM Chips.'
        },
        {
            'title': 'Batch Tokens',
            'endpoint': 'tools.tool_batch_tokens',
            'description': 'Batch retrieve and synchronize multiple Profile and Firmware configuration tokens from PocketBase simultaneously.'
        }
    ]
    return render_template('tools.html', tools=tools_list)


# ---------------------------------------------------------------------------
# Configuration tool actions (download, flash, tokens)
# ---------------------------------------------------------------------------

@bp.route('/configurations/<config_name>/download-ini')
def download_ini(config_name):
    config = get_usable_config(config_name)
    fw_version = parse_fw_version(request.args.get('fw_version'))

    workdir = tempfile.mkdtemp(prefix="caliga_ini_")
    file_path = get_or_create_ini_file(config, fw_version, current_email(), workdir)

    if file_path and os.path.exists(file_path):
        return send_and_cleanup(file_path, f"{config.config_name}_{fw_version}.ini", workdir)

    flash(f"Unable to generate or retrieve INI file for {config_name}.", "danger")
    return redirect(url_for('configurations.configuration_details', config_name=config_name))


def _binary_job(job, config_id, fw_version, user_email, kind):
    config = db.session.get(Configuration, config_id)
    if kind == 'profile':
        builder, download_name = get_or_build_profile_bin, f"{config.config_name}_{fw_version}.bin"
    else:
        builder, download_name = get_or_build_firmware_bin, f"{config.config_name}_{fw_version}_firmware.bin"

    job.message = f"Looking for an existing {kind} binary, or building one with Forge (up to 60s)..."
    path = builder(config, fw_version, user_email, job.workdir)
    if not path:
        raise JobError(f"{kind.title()} BIN for {config.config_name} ({fw_version}) could not be found or built within 60s.")

    job.add_file('bin', path, download_name)
    job.message = "Binary ready."


@bp.route('/configurations/<config_name>/download-profile-bin', methods=['POST'])
def download_profile(config_name):
    config = get_usable_config(config_name)
    fw_version = parse_fw_version(request.form.get('fw_version'))
    return _start('profile_bin', _binary_job, config.id, fw_version, current_email(), 'profile')


@bp.route('/configurations/<config_name>/download-firmware-bin', methods=['POST'])
def download_firmware(config_name):
    config = get_usable_config(config_name)
    fw_version = parse_fw_version(request.form.get('fw_version'))
    return _start('firmware_bin', _binary_job, config.id, fw_version, current_email(), 'firmware')


@bp.route('/configurations/<config_name>/check-rs485')
def check_rs485(config_name):
    port = find_rs485_port()
    if port:
        return jsonify({"connected": True, "port": port})
    return jsonify({"connected": False, "error": "No RS-485 port found. Please connect your RS-485 adapter."}), 404


def _flash_job(job, config_id, fw_version, user_email):
    config = db.session.get(Configuration, config_id)

    def on_progress(percent):
        job.progress = percent
        job.message = f"Transferring firmware... ({percent}%)"

    def on_status(text):
        job.message = text

    success, msg = flash_firmware_osdp(config, fw_version, user_email, job.workdir,
                                       progress_callback=on_progress, status_callback=on_status)

    log_revision(config.config_name,
                 f"OSDP Flash ({fw_version}). Result: {'Success' if success else 'Failed'} - {msg}",
                 user_email)
    db.session.commit()

    if not success:
        raise JobError(msg)
    job.progress = 100
    job.message = msg


@bp.route('/configurations/<config_name>/flash', methods=['POST'])
@require_role('SET', 'QA')
def flash_configuration(config_name):
    config = get_usable_config(config_name)
    fw_version = parse_fw_version(request.form.get('fw_version'))
    return _start('flash', _flash_job, config.id, fw_version, current_email())


@bp.route('/configurations/<config_name>/get-tokens')
def get_tokens(config_name):
    """Read-only lookup of existing PocketBase tokens. Nothing is built or uploaded."""
    config = get_usable_config(config_name)
    fw_version = parse_fw_version(request.args.get('fw_version'))
    return jsonify(lookup_tokens(config, fw_version))


def _sync_tokens_job(job, config_id, fw_version, user_email):
    config = db.session.get(Configuration, config_id)
    job.message = "Building binaries and syncing with PocketBase..."
    job.result = sync_tokens(config, fw_version, user_email, job.workdir)
    job.message = "Sync finished."


@bp.route('/configurations/<config_name>/sync-tokens', methods=['POST'])
def sync_config_tokens(config_name):
    """Builds both binaries and uploads them to PocketBase, creating or overwriting records."""
    config = get_usable_config(config_name)
    fw_version = parse_fw_version(request.form.get('fw_version'))
    return _start('sync_tokens', _sync_tokens_job, config.id, fw_version, current_email())


# ---------------------------------------------------------------------------
# Partial configuration generator
# ---------------------------------------------------------------------------

def _partial_job(job, partial_name, partial_ini_text, fw_version, user_email, build_bin):
    ini_filename = f"{partial_name}.ini"
    local_ini = os.path.join(job.workdir, ini_filename)
    with open(local_ini, "w") as f:
        f.write(partial_ini_text)
    job.add_file('ini', local_ini, ini_filename)

    job.message = "Uploading partial INI to Cloud Storage..."
    if not upload_partial_ini_to_gcs(ini_filename, partial_ini_text, fw_version, user_email):
        raise JobError("Failed to upload partial INI to Cloud Storage.")

    if not build_bin:
        job.result = {"note": "Partial INI uploaded. Profile BIN build was not requested."}
        job.message = "Partial INI ready."
        return

    job.message = "Building Profile BIN with Forge (up to 120s)..."
    bin_path = compile_partial_bin_via_forge(partial_name, fw_version, user_email, job.workdir)
    if bin_path:
        job.add_file('bin', bin_path, f"{partial_name}_{fw_version}.bin")
        job.result = {"note": "Partial INI uploaded and Profile BIN built."}
    else:
        job.result = {"warning": "Partial INI uploaded, but the Forge Profile BIN build failed or timed out."}
    job.message = "Finished."


@bp.route('/tools/partial-config', methods=['GET', 'POST'], strict_slashes=False)
def tool_partial_config():
    if request.method == 'GET':
        return render_template('tools/partial_config.html')

    fw_version = parse_fw_version(request.form.get('fw_version'))
    build_bin = request.form.get('build_bin_choice') == 'true'

    form_data = request.form.to_dict()
    partial_ini_text = generate_partial_ini_content(form_data)

    if not partial_ini_text:
        return json_error("No parameters were changed. Select at least one override.")

    changed_keys = [k for k, v in form_data.items() if v and k not in PARTIAL_META_FIELDS]
    name_summary = "_".join(changed_keys[:3]) if changed_keys else "override"
    partial_name = re.sub(r'[^A-Za-z0-9_-]', '', f"partial_{name_summary}")

    return _start('partial', _partial_job, partial_name, partial_ini_text, fw_version, current_email(), build_bin)


@bp.route('/tools/ini-translator', methods=['GET', 'POST'], strict_slashes=False)
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


# ---------------------------------------------------------------------------
# YAML automator
# ---------------------------------------------------------------------------

def _int_field(name, default, minimum=None, maximum=None):
    """Parses an integer form field. Raises ValueError with a readable message."""
    raw = str(request.form.get(name, default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"'{name}' must be a whole number (got '{raw}').")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"'{name}' must be between {minimum} and {maximum} (got {value}).")
    return value


def _get_or_create_oem(oem_clean):
    oem = OemSku.query.filter_by(name=oem_clean).first()
    if oem is None:
        next_id = (db.session.query(func.max(OemSku.sku_id)).scalar() or 0) + 1
        oem = OemSku(name=oem_clean, sku_id=next_id)
        db.session.add(oem)
        db.session.commit()
    return oem


@bp.route('/tools/yaml-automator', methods=['GET', 'POST'], strict_slashes=False)
def tool_yaml_automator():
    if request.method == 'GET':
        return render_template('tools/yaml_automator.html')

    try:
        oem_clean = request.form.get('oem', '').strip().replace(' ', '_').upper()
        if not re.fullmatch(r'[A-Z0-9_]+', oem_clean):
            raise ValueError("OEM must contain only letters, numbers, spaces, or underscores.")

        tci_raw = str(request.form.get('tci_choice', '')).strip().lower().removeprefix("0x")
        if not re.fullmatch(r'[0-9a-f]{1,6}', tci_raw):
            raise ValueError(f"TCI must be 1 to 6 hex digits, e.g. 0209FE (got '{request.form.get('tci_choice', '')}').")
        tci_raw = tci_raw.zfill(6)

        bit_count = _int_field('ecp_bit_count_choice', 40, minimum=0)

        specialty = request.form.get('specialty_keys_choice') == "true"
        if specialty:
            version_input = _int_field('key_version', 0, minimum=0, maximum=2)
            key_slot = _int_field('key_slot', 64, minimum=0, maximum=255)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for('tools.tool_yaml_automator'))

    sam_info = SAM_TYPE_MAP.get(request.form.get('sam_type_choice'), {"label": "SAM_AV3", "id": 3})
    partner_info = PART_MAP.get(request.form.get('partner_choice'), {"label": "PARTNER_RFIDEAS", "id": 2})
    partner_km_info = PART_KM_MAP.get(request.form.get('partner_km_choice'), {"label": "partnerKm2", "id": 2})

    sku_oem_id = _get_or_create_oem(oem_clean).sku_id
    all_oems = OemSku.query.order_by(OemSku.sku_id, OemSku.name).all()

    tci_1 = HexInt(int(tci_raw[0:2], 16))
    tci_2 = HexInt(int(tci_raw[2:4], 16))
    tci_3 = HexInt(int(tci_raw[4:6], 16))

    specialty_keys_config = None
    if specialty:
        version_labels = {0: "key_version_a", 1: "key_version_b", 2: "key_version_c"}
        type_label, type_hex = KEY_TYPE_MAP.get(request.form.get('key_type_choice'), ("SAM_MIFARE_KEY", 0x10))
        use_label, use_id = KEY_USE_MAP.get(request.form.get('key_use_choice'), ("SAM_PICC_KEY", 1))

        specialty_keys_config = {
            "key2": {
                "key_slot": key_slot,
                "key_version": [{version_labels[version_input]: version_input}],
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
        "sku_oem": [{f"SKU_{o.name}": o.sku_id} for o in all_oems],
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
        "ecp_bit_count": bit_count,
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
        yaml_string = yaml_string.replace(section, f"\n{section}")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    custom_filename = f"SAM_Config_{oem_clean}_{timestamp}.yml"

    return send_file(io.BytesIO(yaml_string.encode('utf-8')), as_attachment=True,
                     download_name=custom_filename, mimetype='application/x-yaml')


# ---------------------------------------------------------------------------
# Batch tokens
# ---------------------------------------------------------------------------

def _token_item(config_obj, tokens):
    return {"config_name": config_obj.config_name, "status": config_obj.status, **tokens}


def _batch_lookup_job(job, config_names, fw_version):
    configs = Configuration.query.filter(Configuration.config_name.in_(config_names)).all()
    job.result = {"items": [_token_item(c, lookup_tokens(c, fw_version)) for c in configs]}
    job.message = f"Looked up tokens for {len(configs)} configuration(s)."


def _batch_sync_job(job, config_names, fw_version, user_email):
    configs = Configuration.query.filter(Configuration.config_name.in_(config_names)).all()
    job.message = f"Building and syncing {len(configs)} configuration(s)..."

    def sync_one(config_obj):
        workdir = os.path.join(job.workdir, config_obj.config_name)
        os.makedirs(workdir, exist_ok=True)
        return _token_item(config_obj, sync_tokens(config_obj, fw_version, user_email, workdir))

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        items = list(executor.map(sync_one, configs))

    job.result = {"items": items}
    job.message = f"Synced {len(items)} configuration(s)."


def _selected_batch_configs():
    names = request.form.getlist('config_names')
    return [c.config_name for c in Configuration.query.filter(
        Configuration.config_name.in_(names), Configuration.status != 'Archived'
    ).all()] if names else []


@bp.route('/tools/batch-tokens', methods=['GET'], strict_slashes=False)
def tool_batch_tokens():
    configurations = Configuration.query.filter(Configuration.status != 'Archived').order_by(
        Configuration.config_id_dec.asc().nulls_last()
    ).all()
    return render_template('tools/batch_tokens.html', configurations=configurations)


@bp.route('/tools/batch-tokens/lookup', methods=['POST'])
def batch_tokens_lookup():
    names = _selected_batch_configs()
    if not names:
        return json_error("Select at least one configuration.")
    fw_version = parse_fw_version(request.form.get('fw_version'))
    return _start('batch_lookup', _batch_lookup_job, names, fw_version)


@bp.route('/tools/batch-tokens/sync', methods=['POST'])
def batch_tokens_sync():
    names = _selected_batch_configs()
    if not names:
        return json_error("Select at least one configuration.")
    fw_version = parse_fw_version(request.form.get('fw_version'))
    return _start('batch_sync', _batch_sync_job, names, fw_version, current_email())
