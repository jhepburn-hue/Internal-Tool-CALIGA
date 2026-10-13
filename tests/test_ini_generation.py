"""INI generation from configuration fields, the form import, and the backfill from master INIs."""
import os
import re
import sys

import pytest

from conftest import INI_PRIVATE_DIR, ROOT
from models import Configuration
from services.ini_builder import Builder, generate_ini_from_config
from services.ini_settings import EMPTY_PROFILES, IniGenerationError, check_value, parse_settings, render_ini

sys.path.insert(0, os.path.join(ROOT, "tools"))
from backfill_ini_fields import fields_from_ini, parse_ini  # noqa: E402
from import_form_fields import box_value, changes_for  # noqa: E402

with open(os.path.join(os.path.dirname(__file__), "fixtures", "settings_minimal.ini")) as f:
    SETTINGS = parse_settings(f.read())

PROFILES = {**EMPTY_PROFILES, "default_tci": "0209FE", "keysets": {"manufacturer_keyset": "Lk00001"}}

needs_private_files = pytest.mark.skipif(
    not (INI_PRIVATE_DIR and os.path.exists(os.path.join(INI_PRIVATE_DIR, "settings_default.ini"))),
    reason="Set CALIGA_TEST_INI_PRIVATE_DIR to the folder with settings_default.ini to run.",
)


def build(fw_version=None, **fields):
    config = Configuration(config_name=fields.pop("config_name", "CTST1"), config_id_dec=fields.pop("config_id_dec", 1), **fields)
    builder = Builder(config, SETTINGS, PROFILES, fw_version).build()
    return builder.values, builder.warnings


def test_settings_file_gives_defaults_options_and_order():
    assert SETTINGS.defaults["av"]["idle_color"] == '"white"'
    assert SETTINGS.allowed[("av", "idle_color")][:2] == ['"off"', '"red"']
    assert list(SETTINGS.defaults)[:3] == ["av", "ble", "ble/adv_data"]


def test_render_writes_only_changed_keys_in_file_order():
    text = render_ini(SETTINGS, {"av": {"idle_color": '"red"', "silence_beeper": "false"}, "mfg_data": {"cfg_id": "0x0010"}})
    assert text == '[av]\nidle_color = "red"\n\n[mfg_data]\ncfg_id = 0x0010\n'


def test_render_refuses_settings_not_in_the_file():
    with pytest.raises(IniGenerationError):
        render_ini(SETTINGS, {"av": {"made_up": "1"}})


@pytest.mark.parametrize("section, key, value, ok", [
    ("av", "idle_color", '"red"', True),
    ("av", "idle_color", '"cyan"', False),
    ("av", "silence_beeper", "yes", False),
    ("rfid/hf/app/wallet", "terminal_id", '"0209F"', False),
    ("mfg_data", "cfg_id", "0x0166", True),
])
def test_values_are_checked_against_the_settings_file(section, key, value, ok):
    assert (check_value(SETTINGS, section, key, value) is None) == ok


@pytest.mark.parametrize("stored, expected", [
    ("On", "0x2001"),
    ("Standard", "0x0000"),     # as-is, like the master INIs
    ("32 Bit MSB", "0x2001"),
    ("32-bit LSB", "0x2000"),
    ("40-bit MSB", "0x2801"),
    ("56-bit MSB", "0x3801"),
    ("56-bit LSB", "0x3800"),
])
def test_csn_formats(stored, expected):
    values, _ = build(mfc_csn=stored)
    assert values["rfid/hf/app/csn"]["mifare_classic_format"] == expected
    assert values["rfid/hf/app/csn"]["iso15693_format"] == "0xFFFF"


def test_unknown_csn_value_is_left_as_is_with_a_warning():
    values, warnings = build(mfc_csn="AMAG:MFC")
    assert values["rfid/hf/app/csn"]["mifare_classic_format"] == "0x0000"
    assert any("AMAG:MFC" in w for w in warnings)


def test_all_csn_rows_off_disables_csn():
    values, _ = build(mfc_csn="Off", iso_14443a_csn="Off")
    assert values["rfid/hf/app/csn"]["enabled"] == "false"


def test_unsupported_setting_is_left_out_for_that_firmware():
    values, warnings = build("v5.4.10", casi_output_format="CASI-4001")
    assert "ask_output_format" not in values.get("rfid/lf", {})
    assert any("v5.4.10" in w for w in warnings)

    values, _ = build(None, casi_output_format="CASI-4001")
    assert values["rfid/lf"]["ask_output_format"] == '"casi_01"'


def test_unsupported_color_is_flagged_not_written():
    values, warnings = build(idle_led="Cyan")
    assert "idle_color" not in values.get("av", {})
    assert any("Cyan" in w for w in warnings)


def test_custom_mobile_keyset_uses_the_keyset_in_mobile_notes():
    values, _ = build(ble_functionality="Admin + Credentials", mobile_keyset="Custom", mobile_notes="Custom mobile Mk50008")
    assert values["keys"]["slot13"] == "Lk50008:Mkm"
    assert values["keys"]["slot14"] == "Lk50008:Mkc"
    assert values["mypass"]["all_keys"] == "true"


def test_old_enabled_mobile_keyset_is_flagged():
    values, warnings = build(ble_functionality="Admin + Credentials", mobile_keyset="Enabled")
    assert "slot13" not in values.get("keys", {})
    assert any("Mobile Keyset 'Enabled'" in w for w in warnings)


def test_leaf_cc_with_wallet_uses_customer_keys():
    values, _ = build(leaf_cc="On", keyset_id="Lk12345", custom_keyset="On", ecp_desfire="On", ecp_tci="0209fe")
    assert values["keys"]["slot11"] == "Lk12345:Kc8"
    assert values["keys"]["slot15"] == "Lk12345:Kr0"
    assert values["rfid/hf/app/wallet"]["terminal_id"] == '"0209FE"'


def test_second_keyset_in_notes_reads_through_desfire_3():
    values, _ = build(leaf_cc="On", keyset_id="Lk12345", custom_keyset="On",
                      custom_application_notes="Site A Lk12345, site B Lk12399")
    assert values["rfid/hf/app/leaf/desfire/3"]["enabled"] == "true"
    assert values["keys"]["slot12"] == "Lk12399:Kc8"


def test_ini_lines_in_notes_are_applied_last():
    values, warnings = build(idle_led="Red", custom_application_notes="[av]\nidle_color = \"amber\"\nmade_up = 1")
    assert values["av"]["idle_color"] == '"amber"'
    assert any("made_up" in w for w in warnings)


def test_form_import_replaces_only_values_caliga_cant_hold():
    rows = [["Mobile Keyset", "Custom"], ["Custom\nApplication\nNotes", "MFC ACTPro"], ["FSK Prox", "Enabled"],
            ["", "", "", "", "", "Notes/Other Products"], ["", "", "", "", "", "SmartMax app supported"]]
    config = Configuration(config_name="C1", mobile_keyset="Enabled", fsk_prox="Off", custom_application_notes="")
    changes = changes_for(config, rows)
    assert changes["mobile_keyset"] == ("Enabled", "Custom")
    assert changes["custom_application_notes"] == ("", "MFC ACTPro")
    assert changes["notes_other_products"] == ("", "SmartMax app supported")
    assert "fsk_prox" not in changes  # "Off" is a valid choice, so it's kept


def test_box_value_reads_the_cell_under_the_label():
    rows = [["x", "Notes"], ["", ""], ["", "Under it"]]
    assert box_value(rows, "Notes") == "Under it"


def test_backfill_reads_settings_from_a_master_ini():
    master = parse_ini("""
[rfid/hf/app/leaf/desfire]
enabled = true
[rfid/hf/app/leaf/desfire/2]
enabled = true
[rfid/hf/app/meridian]
enabled = true
bit_nb = 57
[rfid/hf/app/wallet]
terminal_id = "0209fe"
[ble/adv_data]
name_complete = "BRVO"
[keys]
config_card = Lk70003:Kc5
slot11 = Lk10149:Kc8
""")
    defaults = {("rfid/hf/app/leaf/desfire/1", "enabled"): "false", ("ble/adv_data", "name_complete"): '"APEX"'}
    fields = fields_from_ini({**defaults, **master}, master, manufacturer_keyset="Lk00001")
    assert fields == {
        "leaf_si": "Off", "leaf_cc": "On", "keyset_id": "Lk10149", "custom_keyset": "On",
        "ecp_desfire": "On", "mifare_2go": "Off", "ecp_tci": "0209FE", "meridian_bit_count": 57,
        "ble_advertising_name": "BRVO",
    }


def test_backfill_ignores_default_name_and_config_card_keyset():
    master = parse_ini('[ble/adv_data]\nname_complete = "APEX"\n[keys]\nconfig_card = Lk70003:Kc5\n')
    fields = fields_from_ini(master, master, manufacturer_keyset="Lk00001")
    assert "keyset_id" not in fields and "ble_advertising_name" not in fields


@needs_private_files
def test_generated_ini_with_the_real_settings_file(tmp_path):
    config = Configuration(config_name="CTST1", config_id_dec=1, idle_led="Blue", leaf_cc="On", keyset_id="Lk12345",
                           custom_keyset="On", ecp_desfire="On", ecp_tci="0209FE", meridian_bit_count=57,
                           ble_advertising_name="ABCD", mfc_csn="56-bit LSB", casi_output_format="CASI-4001")
    out = tmp_path / "CTST1.ini"
    generate_ini_from_config(config, str(out), "v5.4.10")
    ini = out.read_text()

    def has(section, key, value):
        return re.search(rf"\[{re.escape(section)}\][^\[]*\n{re.escape(key)} = {re.escape(value)}\n", ini) is not None

    assert ini.startswith("; Generated by CALIGA from the CTST1 database record for v5.4.10.")
    assert has("mfg_data", "cfg_id", "0x0001")
    assert has("av", "idle_color", '"blue"')
    assert has("keys", "slot11", "Lk12345:Kc8")
    assert has("rfid/hf/app/wallet", "terminal_id", '"0209FE"')
    assert has("rfid/hf/app/meridian", "bit_nb", "57")
    assert has("rfid/hf/app/csn", "mifare_classic_format", "0x3800")
    assert has("ble/adv_data", "name_complete", '"ABCD"')
    assert "ask_output_format" not in ini.split("\n\n", 1)[1]
