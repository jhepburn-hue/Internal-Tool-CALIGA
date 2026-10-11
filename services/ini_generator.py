"""
Builds a configuration's INI with the INI-Generator package (github.com/jhepburn-hue/INI-Generator).

CALIGA passes the configuration's database fields as form rows (a label, then its value), the
same layout as the CSV configuration form. INI-Generator applies settings_default.ini, the
customer profiles, and its validation checks, and returns the INI plus anything that needs a
manual review.

INI-Generator needs two private files, settings_default.ini and customer_profiles.json:
- INI_GENERATOR_PRIVATE_DIR in .env points at a local folder with them, or
- they're downloaded once per run from gs://<GCS_BUCKET_NAME>/<INI_GENERATOR_GCS_PREFIX>.
"""
import os
import logging
import tempfile
import threading
from google.api_core.exceptions import NotFound
from services.gcs_client import get_gcs_client, GCS_BUCKET_NAME
from utils.errors import UserFacingError

log = logging.getLogger(__name__)

GCS_PREFIX = os.getenv('INI_GENERATOR_GCS_PREFIX', 'private/ini-generator/')
CACHE_DIR = os.path.join(tempfile.gettempdir(), 'caliga_ini_generator')
DEFAULTS_FILE = 'settings_default.ini'
PROFILES_FILE = 'customer_profiles.json'

# CALIGA CSN dropdown value -> INI-Generator CSN format name. Unknown values pass through
# unchanged, and INI-Generator flags them for manual review.
CSN_FORMAT_NAMES = {
    'on': '',                           # CSN On with no format: 32-bit MSB (0x2001)
    'standard': 'standard ethos',       # CALIGA's "Standard" is 0x2001
    '32 bit msb': '32-bit msb',
    '32-bit msb': '32-bit msb',
    '32-bit lsb': '32-bit lsb',
    '40-bit msb': 'csn_40_bit_msb_lrc',
    '56-bit msb': 'csn_56_bit_msb',
    '56-bit lsb': 'csn_56_bit',
}
CSN_OFF_VALUES = ('off', 'disabled', 'none', '-', '0', 'false')

_lock = threading.Lock()
_csv_parser = None


class IniGenerationError(UserFacingError):
    pass


def _download_private_files():
    client = get_gcs_client()
    if client is None:
        raise IniGenerationError(
            "Can't generate INIs: Google Cloud Storage is unavailable and INI_GENERATOR_PRIVATE_DIR isn't set."
        )

    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    bucket = client.bucket(GCS_BUCKET_NAME)

    for name in (DEFAULTS_FILE, PROFILES_FILE):
        path = os.path.join(CACHE_DIR, name)
        try:
            bucket.blob(GCS_PREFIX + name).download_to_filename(path)
            log.info("Downloaded gs://%s/%s%s", GCS_BUCKET_NAME, GCS_PREFIX, name)
        except NotFound:
            # Don't let a stale copy from an earlier run stand in for a missing file.
            if os.path.exists(path):
                os.remove(path)
            if name == DEFAULTS_FILE:
                raise IniGenerationError(
                    f"Can't generate INIs: gs://{GCS_BUCKET_NAME}/{GCS_PREFIX}{DEFAULTS_FILE} doesn't exist. "
                    "Upload it, or set INI_GENERATOR_PRIVATE_DIR in .env."
                )
            log.warning("%s not found in GCS. Customer and partner apps won't be generated.", name)
    return CACHE_DIR


def _private_dir():
    local_dir = os.getenv('INI_GENERATOR_PRIVATE_DIR')
    if not local_dir:
        return _download_private_files()
    if not os.path.exists(os.path.join(local_dir, DEFAULTS_FILE)):
        raise IniGenerationError(f"Can't generate INIs: {DEFAULTS_FILE} isn't in INI_GENERATOR_PRIVATE_DIR ({local_dir}).")
    return local_dir


def _generator():
    """Imports INI-Generator once, after pointing it at the private files. It reads them at import time."""
    global _csv_parser
    with _lock:
        if _csv_parser is None:
            os.environ['INI_GENERATOR_PRIVATE_DIR'] = _private_dir()
            from ini_generator import csv_parser
            _csv_parser = csv_parser
    return _csv_parser


def _row(label, value):
    return [label, "", "" if value is None else str(value).strip()]


def _csn_row(label, value):
    value = (value or "").strip()
    if not value:
        return [label, ""]
    if value.lower() in CSN_OFF_VALUES:
        return [label, "", "Off"]
    format_name = CSN_FORMAT_NAMES.get(value.lower(), value)
    return [label, "", "On", format_name] if format_name else [label, "", "On"]


def config_to_rows(config):
    """Returns the configuration as INI-Generator form rows."""
    return [
        _row("Config Name", config.config_name),
        _row("Config ID", config.config_id_dec),

        _row("Idle LED", config.idle_led),
        _row("Credential Report LED", config.credential_report_led),
        _row("Beeper", config.beeper),
        _row("Keypad Format", config.keypad_format),
        _row("Tamper Monitoring", config.tamper_monitoring),
        _row("Casi Output Format", config.casi_output_format),

        _row("Leaf Si Application (Kv1)", "Leaf Si" if config.leaf_si_enabled else "None"),
        _row("Leaf Cc Application (Kc1)", "Leaf Cc" if config.leaf_cc_enabled else "None"),
        _row("Keyset ID (LkXXXXX)", config.keyset_id),
        _row("Custom Keyset (LEAF Cc)", config.custom_keyset),
        _row("Other Custom HF Application", config.other_custom_hf_application),
        _row("Custom / Application / Notes", config.custom_application_notes),
        _row("Card Notes", config.card_notes),

        _csn_row("MFC CSN", config.mfc_csn),
        _csn_row("EV1/EV2 CSN", config.ev1_ev2_csn),
        _csn_row("iClass CSN", config.iclass_csn),
        _csn_row("ISO15693 CSN", config.iso_15693_csn),
        _csn_row("ISO14443A CSN", config.iso_14443a_csn),

        _row("FSK Prox", config.fsk_prox),
        _row("ASK Prox", config.ask_prox),
        _row("Prox Filter", config.prox_filter),
        _row("Prox Filter Description", config.prox_filter_description),

        _row("BLE Functionality", config.ble_functionality),
        _row("BLE Advertising Config", config.ble_advertising_config),
        _row("BLE Advertising Name", config.ble_advertising_name),
        _row("Mobile Keyset", config.mobile_keyset),
        _row("Mobile Notes", config.mobile_notes),
        _row("Legacy Credentials", config.legacy_credentials),
        _row("NFC Functionality", config.nfc_functionality),

        _row("ECP DESFire", config.ecp_desfire),
        _row("MiFare2Go", config.mifare_2go),
        _row("ECP TCI", config.ecp_tci),
        _row("Apple Meridian Bit Count", config.meridian_bit_count),
    ]


def generate_ini_from_config(config, output_path):
    """
    Writes the configuration's INI to output_path and returns INI-Generator's review warnings.
    The warnings are also written at the top of the file as comments.
    Raises IniGenerationError when the INI can't be generated.
    """
    csv_parser = _generator()

    try:
        ini_text, warnings = csv_parser.generate_ini(config_to_rows(config))
    except ValueError as e:
        raise IniGenerationError(f"Couldn't generate the INI for {config.config_name}: {e}")

    header = [f"; Generated by CALIGA from the {config.config_name} database record with INI-Generator."]
    if warnings:
        header.append("; Needs manual review:")
        header.extend(f";   - {w}" for w in warnings)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(header) + "\n\n" + ini_text)

    log.info("Generated INI for %s -> %s (%d review warnings)", config.config_name, output_path, len(warnings))
    return warnings
