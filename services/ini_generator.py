import os
import re
import configparser
from pathlib import Path
from models import Configuration
from utils.prox_math import generate_hex_codes
from utils.prox_parser import parse_prox_description

DEFAULT_INI_TEMPLATE = "default.ini"

def set_kv(cfg: configparser.ConfigParser, section: str, key: str, value: str):
    if not cfg.has_section(section):
        cfg.add_section(section)
    cfg.set(section, key, value)

def _emit_ini_sorted(cfg: configparser.ConfigParser, out_ini_path: str, keys_entries: dict, prox_filter_block: str = None, notes_block: str = None):
    """
    Emits INI with sections sorted alphabetically, keeping wallet/meridian/mifare_2go grouped together.
    [keys] placed at bottom sorted by slot number, followed by notes.
    """
    KEYS_SEC = "keys"
    GROUP = [
        "rfid/hf/app/wallet",
        "rfid/hf/app/meridian",
        "rfid/hf/app/mifare_2go/generic",
    ]

    all_secs = cfg.sections()
    normal_secs = [s for s in all_secs if s.lower() != KEYS_SEC]
    normal_secs.sort(key=str.lower)

    lower_to_orig = {s.lower(): s for s in normal_secs}
    present_group = [lower_to_orig[g] for g in (g.lower() for g in GROUP) if g in lower_to_orig]
    if present_group:
        anchor_idx = min(normal_secs.index(s) for s in present_group)
        for s in present_group:
            normal_secs.remove(s)
        ordered_present = [lower_to_orig[g] for g in (g.lower() for g in GROUP) if g in lower_to_orig]
        normal_secs[anchor_idx:anchor_idx] = ordered_present

    out_lines = []
    for sec in normal_secs:
        out_lines.append(f"[{sec}]\n")
        items = list(cfg.items(sec))
        enabled_items = [(k, v) for (k, v) in items if k.lower() == "enabled"]
        other_items = [(k, v) for (k, v) in items if k.lower() != "enabled"]
        other_items.sort(key=lambda kv: kv[0].lower())
        for k, v in (enabled_items + other_items):
            out_lines.append(f"{k} = {v}\n")
        out_lines.append("\n")

    if prox_filter_block:
        if not out_lines or not out_lines[-1].endswith("\n"):
            out_lines.append("\n")
        out_lines.append(prox_filter_block if prox_filter_block.endswith("\n") else prox_filter_block + "\n")

    if keys_entries:
        ordered_slots = sorted(keys_entries.keys(), key=lambda x: int(re.sub(r"\D", "", x) or 0))
        out_lines.append("\n[keys]\n")
        for slot in ordered_slots:
            out_lines.append(f"{slot} = {keys_entries[slot]}\n")

    if notes_block:
        if not out_lines or not out_lines[-1].endswith("\n"):
            out_lines.append("\n")
        out_lines.append(notes_block if notes_block.endswith("\n") else notes_block + "\n")

    os.makedirs(os.path.dirname(out_ini_path), exist_ok=True)
    with open(out_ini_path, "w") as f:
        f.write("".join(out_lines).rstrip() + "\n")

def map_csn_to_hex_flag(csn_value):
    if not csn_value or str(csn_value).strip().lower() in ['off', 'disabled', '0', 'false', 'none', '-']:
        return "0xFFFF"
    val_upper = str(csn_value).upper()
    if '56' in val_upper:
        return "0x3701"
    elif '32' in val_upper:
        return "0x2001"
    elif 'STANDARD' in val_upper or 'ON' in val_upper:
        return "0x2001"
    return "0xFFFF"

def generate_ini_from_config(config: Configuration, output_path: str) -> bool:
    """
    Converts a Configuration database model instance into a spec-compliant .ini file.
    """
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str

    template_file = DEFAULT_INI_TEMPLATE if os.path.exists(DEFAULT_INI_TEMPLATE) else "settings_default.ini"
    if os.path.exists(template_file):
        cfg.read(template_file)

    keys_entries = {}

    cfg_id_dec = str(config.config_id_dec or "").strip()
    if cfg_id_dec.isdigit():
        set_kv(cfg, "mfg_data", "cfg_id", f"0x{int(cfg_id_dec):04X}")
    elif config.config_id_hex:
        set_kv(cfg, "mfg_data", "cfg_id", str(config.config_id_hex))

    idle_color = (config.idle_led or "Red").strip().lower()
    cred_color = (config.credential_report_led or "Green").strip().lower()
    if idle_color != "undefined":
        set_kv(cfg, "av", "idle_color", f'"{idle_color}"')
    if cred_color != "undefined":
        set_kv(cfg, "av", "credential_report_color", f'"{cred_color}"')

    beeper_on = "true" if config.beeper and config.beeper.lower() in ['on', 'enabled', 'yes', 'true'] else "false"
    set_kv(cfg, "rfid/av", "beep_enabled", beeper_on)

    leaf_si_active = config.leaf_si and "si" in config.leaf_si.lower() or config.leaf_si == "Yes"
    if leaf_si_active:
        set_kv(cfg, "rfid/hf/app/leaf/desfire", "enabled", "true")
        set_kv(cfg, "rfid/hf/app/leaf/desfire", "check_legacy_signature", "true")
        set_kv(cfg, "rfid/hf/app/leaf/desfire/1", "enabled", "true")
        set_kv(cfg, "rfid/hf/app/leaf/desfire/1", "app_id", '"F51CD8"')
        set_kv(cfg, "rfid/hf/app/leaf/desfire/1", "card_key_nb", "2")
        set_kv(cfg, "rfid/hf/app/leaf/desfire/1", "se_key_nb", "2")
        set_kv(cfg, "rfid/hf/app/leaf/duox/openid", "enabled", "true")

    lk_number = ""
    notes_combined = f"{config.custom_application_notes or ''} {config.card_notes or ''}"
    lk_match = re.search(r'[Ll][Kk]\d{5}', notes_combined)
    if lk_match:
        lk_number = f"Lk{lk_match.group(0)[2:]}"

    if lk_number:
        set_kv(cfg, "rfid/hf/app/leaf/desfire/2", "enabled", "true")
        set_kv(cfg, "rfid/hf/app/leaf/desfire/2", "app_id", '"F51CDB"')
        set_kv(cfg, "rfid/hf/app/leaf/desfire/2", "card_key_nb", "8")
        set_kv(cfg, "rfid/hf/app/leaf/desfire/2", "se_key_nb", "11")
        keys_entries["slot11"] = f"{lk_number}:Kc8"

    ble_val = (config.ble_functionality or "").lower()
    if "mypass" in ble_val or "admin + credentials" in ble_val or "custom" in ble_val:
        set_kv(cfg, "mypass", "allow_credentials", "true")
        set_kv(cfg, "mypass", "bcd_credentials", "false")
        set_kv(cfg, "mypass", "km1_se_slot_nb", "03")
        set_kv(cfg, "mypass", "km2_se_slot_nb", "13")
        set_kv(cfg, "mypass", "kc1_se_slot_nb", "04")
        set_kv(cfg, "mypass", "kc2_se_slot_nb", "14")
        set_kv(cfg, "mypass", "all_keys", "true")
        set_kv(cfg, "mypass", "metadata", '"00000000"')
        set_kv(cfg, "mypass", "allow_key_rolling", "true")
        set_kv(cfg, "rfid/hf/app/mypass", "enabled", "true")

        if "mypass" in ble_val:
            keys_entries["slot13"] = "Ck00001:KM2"
            keys_entries["slot14"] = "Ck00001:KC2"
        elif lk_number:
            keys_entries["slot13"] = f"{lk_number}:Km2"
            keys_entries["slot14"] = f"{lk_number}:Kc2"

    csn_sec = "rfid/hf/app/csn"
    mfc_flag = map_csn_to_hex_flag(config.mfc_csn)
    ev_flag = map_csn_to_hex_flag(config.ev1_ev2_csn)
    iclass_flag = map_csn_to_hex_flag(config.iclass_csn)
    iso15_flag = map_csn_to_hex_flag(config.iso_15693_csn)
    iso14_flag = map_csn_to_hex_flag(config.iso_14443a_csn)

    any_csn = any(f != "0xFFFF" for f in [mfc_flag, ev_flag, iclass_flag, iso15_flag, iso14_flag])
    set_kv(cfg, csn_sec, "enabled", "true" if any_csn else "false")

    if mfc_flag != "0xFFFF": set_kv(cfg, csn_sec, "mifare_classic_format", mfc_flag)
    if ev_flag != "0xFFFF": set_kv(cfg, csn_sec, "mifare_desfire_format", ev_flag)
    if iso14_flag != "0xFFFF":
        set_kv(cfg, csn_sec, "iso14443a_cl1_format", iso14_flag)
        set_kv(cfg, csn_sec, "iso14443a_cl2_format", iso14_flag)
    if iso15_flag != "0xFFFF": set_kv(cfg, csn_sec, "iso15693_format", iso15_flag)
    if iclass_flag != "0xFFFF": set_kv(cfg, csn_sec, "pico15693_format", iclass_flag)

    prox_on = config.fsk_prox and config.fsk_prox.lower() in ['on', 'enabled', 'yes']
    prox_filter_enabled = config.prox_filter and config.prox_filter.lower() in ['on', 'enabled', 'yes']
    prox_filter_block = None

    if prox_on or prox_filter_enabled:
        set_kv(cfg, "rfid/lf", "enabled", "true")

        if prox_filter_enabled:
            prox_notes = (config.prox_filter_description or "").strip()
            if prox_notes:
                lines = prox_notes.splitlines()
                prox_filter_block = "\n; Prox Filter\n" + ("\n".join(f"; {ln}" if ln.strip() else ";" for ln in lines) + "\n")

            details = parse_prox_description(prox_notes)
            if details and "rules" in details and details["rules"]:
                m_hex, v_hex = generate_hex_codes(details)
                rule = details["rules"][0]
                is_rule_disabled = rule.get("disabled", False)
                bit_count = 40 if is_rule_disabled else details.get("bit_count", 40)

                set_kv(cfg, "rfid/lf", "filter_mask", f'"{m_hex}"')
                set_kv(cfg, "rfid/lf", "filter_value", f'"{v_hex}"')
                set_kv(cfg, "rfid/lf", "filter_bit_len", str(bit_count))
                set_kv(cfg, "rfid/lf", "filter_enabled", "false" if is_rule_disabled else "true")

                logic_map = {"GREATER_OR_EQUAL": '"greater_or_equal"', "LESS_OR_EQUAL": '"less_or_equal"', "EQUAL": '"equal"'}
                set_kv(cfg, "rfid/lf", "filter_function", logic_map.get(rule["logic"], '"equal"'))
            else:
                set_kv(cfg, "rfid/lf", "filter_bit_len", "40")
                set_kv(cfg, "rfid/lf", "filter_enabled", "false")
                set_kv(cfg, "rfid/lf", "filter_function", '"equal"')
                set_kv(cfg, "rfid/lf", "filter_mask", '"0000000000"')
                set_kv(cfg, "rfid/lf", "filter_value", '"0000000000"')

    notes_txt = (config.card_notes or "").strip()
    notes_block = None
    if notes_txt:
        lines = notes_txt.splitlines()
        notes_block = "\n; Notes\n" + "\n".join(f"; {ln}" if ln.strip() else ";" for ln in lines) + "\n"

    _emit_ini_sorted(cfg, output_path, keys_entries, prox_filter_block, notes_block)
    print(f"[INI GENERATOR] Successfully generated complete INI for {config.config_name} -> {output_path}")
    return True