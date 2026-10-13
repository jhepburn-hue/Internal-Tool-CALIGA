"""
Reads settings_default.ini, the reader's full list of settings with their defaults, and checks INI
values against it.

The file is company data, so it's never committed or stored in GCS. CALIGA reads it, and the
optional customer_profiles.json, from the local folder named by INI_PRIVATE_DIR.
"""
import json
import os
import re
from dataclasses import dataclass, field

from utils.errors import UserFacingError

DEFAULTS_FILE = 'settings_default.ini'
PROFILES_FILE = 'customer_profiles.json'

EMPTY_PROFILES = {
    "default_tci": None,
    "keysets": {},
    "ble_names": [],
    "mobile_keysets": {},
    "keyword_apps": [],
    "customer_profiles": [],
    "wallet_overrides": [],
}

# Option lists in settings_default.ini comments sit above one key. These keys share the list
# of the key named here.
SHARED_OPTIONS = {
    ("wiegand", "green_ctrl_mode"): ("wiegand", "red_ctrl_mode"),
    ("wiegand", "buzzer_ctrl_mode"): ("wiegand", "red_ctrl_mode"),
}

# Integer ranges stated in the settings_default.ini comments
RANGES = {
    ("av", "red_intensity"): (0, 6),
    ("av", "green_intensity"): (0, 5),
    ("av", "blue_intensity"): (0, 5),
    ("card_tracker", "max_cards"): (1, 15),
    ("rfid/lf", "filter_bit_len"): (1, 50),
    ("rfid/hf/app/leaf/desfire/1", "card_key_nb"): (0, 13),
    ("rfid/hf/app/leaf/desfire/2", "card_key_nb"): (0, 13),
    ("rfid/hf/app/leaf/desfire/3", "card_key_nb"): (0, 13),
    ("rfid/hf/app/leaf/desfire/4", "card_key_nb"): (0, 13),
    ("rfid/hf/app/meridian", "bit_nb"): (1, 128),
    ("rfid/hf/app/pkoc", "transaction_id_len"): (16, 65),
    ("wiegand", "default_facility_code"): (0, 255),
}


class IniGenerationError(UserFacingError):
    pass


@dataclass
class Settings:
    """settings_default.ini: {section: {key: default}} in file order, plus each key's allowed values."""
    defaults: dict
    allowed: dict = field(default_factory=dict)

    def has(self, section, key):
        return key in self.defaults.get(section, {})


def private_dir():
    local_dir = os.getenv('INI_PRIVATE_DIR')
    if not local_dir:
        raise IniGenerationError(
            "Can't generate INIs: set INI_PRIVATE_DIR in .env to the folder with settings_default.ini "
            "(and customer_profiles.json)."
        )
    if not os.path.exists(os.path.join(local_dir, DEFAULTS_FILE)):
        raise IniGenerationError(f"Can't generate INIs: {DEFAULTS_FILE} isn't in INI_PRIVATE_DIR ({local_dir}).")
    return local_dir


def strip_inline_comment(value):
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[:end + 1] if end != -1 else value
    return value.split(";", 1)[0].strip()


def parse_settings(text):
    """Parses settings_default.ini text into Settings."""
    defaults, allowed = {}, {}
    section, options = None, []

    for line in text.splitlines():
        line = line.strip()
        header = re.match(r"\[(.+)\]$", line)
        if header:
            section = header.group(1)
            defaults[section] = {}
            options = []
            continue

        # Comment lines like '; - "red"' list the allowed values of the next key
        option = re.match(r'^; - ("[^"]*"|\d+)\s*$', line)
        if option:
            options.append(option.group(1))
            continue

        if line and not line.startswith(";") and "=" in line and section:
            key, value = line.split("=", 1)
            key = key.strip()
            defaults[section][key] = strip_inline_comment(value.strip())
            if options:
                allowed[(section, key)] = options
            options = []

    for key, source in SHARED_OPTIONS.items():
        if source in allowed:
            allowed[key] = allowed[source]
    return Settings(defaults, allowed)


def load_settings():
    with open(os.path.join(private_dir(), DEFAULTS_FILE)) as f:
        return parse_settings(f.read())


def load_profiles():
    """Customer and partner rules (customer apps, keysets, BLE names). Optional."""
    path = os.path.join(private_dir(), PROFILES_FILE)
    if not os.path.exists(path):
        return dict(EMPTY_PROFILES), False
    with open(path) as f:
        return {**EMPTY_PROFILES, **json.load(f)}, True


def check_value(settings, section, key, value):
    """Returns a problem with one INI value, or None."""
    default = settings.defaults[section][key]
    where = f"[{section}] {key} = {value}"

    if section == "keys":
        if value != '"N/A"' and not re.fullmatch(r"[\w.-]+:[\w.-]+", value):
            return f"{where}: key slots must be \"N/A\" or KEYSET:KEY"
        return None

    if default in ("true", "false") and value not in ("true", "false"):
        return f"{where}: must be true or false"
    if default.startswith('"') and not (value.startswith('"') and value.endswith('"') and len(value) >= 2):
        return f"{where}: must be a quoted string"
    if default.lower().startswith("0x") and not re.fullmatch(r"0x[0-9A-Fa-f]+", value):
        return f"{where}: must be a hex number like 0x2001"
    if re.fullmatch(r"-?\d+", default) and not re.fullmatch(r"-?\d+", value):
        return f"{where}: must be a whole number"

    allowed = settings.allowed.get((section, key))
    if allowed and value not in allowed:
        return f"{where}: must be one of {', '.join(allowed)}"

    # A quoted hex default ("000000") fixes the length of its value
    if re.fullmatch(r'"[0-9A-Fa-f]{4,}"', default):
        length = len(default) - 2
        if not re.fullmatch(rf'"[0-9A-Fa-f]{{{length}}}"', value):
            return f"{where}: must be {length} hex characters"

    if (section, key) in RANGES:
        low, high = RANGES[(section, key)]
        if not low <= int(value) <= high:
            return f"{where}: must be between {low} and {high}"

    if (section, key) == ("rfid/hf/nfc", "enabled_protocols"):
        letters = value.strip('"')
        if not letters or set(letters) - set("ABFV"):
            return f"{where}: must use only the letters A, B, F and V"

    if (section, key) == ("ble/adv_data", "name_complete") and len(value.strip('"')) > 5:
        return f"{where}: BLE name is limited to 5 characters"
    return None


def _effective(settings, values):
    merged = {section: dict(keys) for section, keys in settings.defaults.items()}
    for section, keys in values.items():
        merged.setdefault(section, {}).update(keys)
    return merged


def _is_active(section, values):
    # mypass has no "enabled" key. Its key slots matter when MyPass credentials are allowed.
    if section == "mypass":
        return values[section].get("allow_credentials") == "true"
    return values[section].get("enabled", "true") == "true"


def check_ini(settings, values, keysets=None):
    """Returns every problem with the INI that settings_default.ini plus values produce."""
    problems = [p for (section, keys) in values.items() for key, value in keys.items()
                if (p := check_value(settings, section, key, value))]
    merged = _effective(settings, values)

    # Every SE key number of an enabled app points at a keys slot, which must hold a key
    for section, keys in merged.items():
        if section == "keys" or not _is_active(section, merged):
            continue
        for key, value in keys.items():
            if not re.search(r"se_(key|slot)_nb$", key) or not re.fullmatch(r"\d+", value):
                continue
            # MyPass keyset 2 (km2/kc2) is only used when all_keys is on
            if section == "mypass" and key[:2] in ("km", "kc") and key[2] == "2" and keys.get("all_keys") != "true":
                continue
            slot = f"slot{int(value):02d}"
            slot_value = merged["keys"].get(slot)
            if slot_value is None:
                problems.append(f"[{section}] {key} = {value}: there is no keys {slot}")
            elif slot_value == '"N/A"':
                problems.append(f"[{section}] {key} = {value}: keys {slot} is empty (\"N/A\")")

    # A LEAF app reads the card key whose number matches card_key_nb: Kc8 -> 8
    for section, keys in merged.items():
        if re.fullmatch(r"rfid/hf/app/leaf/desfire/\d", section) and keys.get("enabled") == "true":
            slot_value = merged["keys"].get(f"slot{int(keys['se_key_nb']):02d}", "")
            number = re.search(r":K[cv](\d+)$", slot_value)
            if number and number.group(1) != keys["card_key_nb"]:
                problems.append(f"[{section}] card_key_nb = {keys['card_key_nb']} but its key slot holds {slot_value}")

    families = (keysets or {}).get("families")
    if families:
        manufacturer = keysets.get("manufacturer_keyset")
        for slot, slot_value in merged["keys"].items():
            keyset = re.match(r"Lk(\d)(\d{4}):", slot_value)
            if keyset and keyset.group(1) not in families and slot_value.split(":")[0] != manufacturer:
                problems.append(f"[keys] {slot} = {slot_value}: Lk{keyset.group(1)}XXXX is not a known keyset family")
    return problems


def render_ini(settings, values):
    """Writes only the keys that differ from settings_default.ini, in its order."""
    for section, keys in values.items():
        unknown = set(keys) - set(settings.defaults.get(section, {}))
        if section not in settings.defaults or unknown:
            raise IniGenerationError(f"[{section}] {sorted(unknown) or ''} isn't in settings_default.ini.")

    blocks = []
    for section, defaults in settings.defaults.items():
        changed = values.get(section, {})
        lines = [f"{key} = {changed[key]}" for key in defaults if key in changed and changed[key] != defaults[key]]
        if lines:
            blocks.append("\n".join([f"[{section}]"] + lines))
    return "\n\n".join(blocks) + "\n"
