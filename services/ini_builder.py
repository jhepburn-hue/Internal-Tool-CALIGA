"""
Builds a configuration's INI from its CALIGA database fields.

settings_default.ini is the starting point and the rulebook: every setting CALIGA writes must
exist there, values are checked against the types and option lists it gives, and the output lists
only the keys that differ from it, in its order. Customer and partner rules (customer apps,
keysets, BLE names, wallet overrides) come from customer_profiles.json. Both files live in the
local INI_PRIVATE_DIR folder.

The rules follow INI-Generator's form rules, which were tuned against the master INIs.
Anything the rules can't settle is returned as a warning and written at the top of the INI.
"""
import logging
import os
import re
import threading

from services.ini_settings import IniGenerationError, check_ini, load_profiles, load_settings, render_ini
from services.prox_filter import build_prox_filter

log = logging.getLogger(__name__)

ON_VALUES = ("on", "yes", "enabled")
OFF_VALUES = ("off", "no", "disabled")

KEYPAD_FORMATS = {
    "4-bit": "4_bit",
    "8-bit": "8_bit",
    "buffered 26-bit": "26_bit",
    "magstripe - 4 digit": "magstripe_4",
    "magstripe - 5 digit": "magstripe_5",
}

# "Standard" CSN format: as-is (no reformatting), as in every master INI whose form says Standard.
# "Standard Ethos" is the 32-bit MSB variant.
CSN_STANDARD = "0x0000"

# CSN dropdown value (lowercase, spaces and dashes ignored) -> rfid/hf/app/csn format
CSN_FORMATS = {
    "on": "0x2001",  # On with no format: 32-bit MSB
    "standard": CSN_STANDARD,
    "standardethos": "0x2001",
    "32bit": "0x2001",
    "32bitmsb": "0x2001",
    "32bitlsb": "0x2000",
    "40bitmsb": "0x2801",
    "56bitmsb": "0x3801",
    "56bitlsb": "0x3800",
}

# CSN row field -> the csn keys it sets
CSN_ROWS = {
    "mfc_csn": ["mifare_classic_format"],
    "ev1_ev2_csn": ["mifare_desfire_format", "mifare_plus_format", "mifare_ultralight_format"],
    "iclass_csn": ["pico15693_format"],
    "iso_15693_csn": ["iso15693_format"],
    "iso_14443a_csn": ["iso14443a_cl1_format", "iso14443a_cl2_format", "iso14443b_format", "felica_format"],
}
CSN_LABELS = {
    "mfc_csn": "MFC CSN", "ev1_ev2_csn": "EV1/EV2 CSN", "iclass_csn": "iClass CSN",
    "iso_15693_csn": "ISO15693 CSN", "iso_14443a_csn": "ISO14443A CSN",
}
# Written as 0xFFFF when every CSN row is Off, like the master INIs
CSN_DISABLED_KEYS = ["mifare_classic_format", "iso14443a_cl1_format", "iso14443a_cl2_format",
                     "iso14443b_format", "iso15693_format", "pico15693_format"]

# Free-text fields searched for customer apps, keysets, "Wiegand only" and INI lines
TEXT_FIELDS = ["custom_application_notes", "other_custom_hf_application", "mobile_notes", "card_notes",
               "card_type", "bitstream", "notes_other_products", "prox_filter_description"]

# Settings a firmware version doesn't accept. Forge rejects the INI if they're present.
# ask_output_format was refused by a v5.4.10 build, and no v5.4.10 or v5.4.11 master INI uses it.
UNSUPPORTED_SETTINGS = {
    "v5.4.10": {("rfid/lf", "ask_output_format")},
    "v5.4.11": {("rfid/lf", "ask_output_format")},
}

_lock = threading.Lock()
_cache = {}


def _rules_data():
    """Loads settings_default.ini and customer_profiles.json once."""
    with _lock:
        if not _cache:
            _cache["settings"] = load_settings()
            _cache["profiles"], _cache["has_profiles"] = load_profiles()
    return _cache["settings"], _cache["profiles"], _cache["has_profiles"]


def reload_rules():
    """Forgets the loaded files, so the next INI reads them again."""
    with _lock:
        _cache.clear()


def quoted(value):
    return f'"{value}"'


def ini_bool(flag):
    return "true" if flag else "false"


class Builder:
    def __init__(self, config, settings, profiles, fw_version=None):
        self.config = config
        self.settings = settings
        self.profiles = profiles
        self.fw_version = fw_version
        self.values = {}
        self.warnings = []

    # Field access

    def text(self, field):
        value = getattr(self.config, field, None)
        return "" if value is None else str(value).strip()

    def lower(self, field):
        return self.text(field).lower()

    def is_on(self, field):
        return self.lower(field) in ON_VALUES

    def is_off(self, field):
        return self.lower(field) in OFF_VALUES

    def notes(self):
        return [self.text(f) for f in TEXT_FIELDS if self.text(f)]

    def mentions(self, pattern):
        return any(re.search(pattern, cell, re.IGNORECASE) for cell in self.notes())

    def cells_mentioning(self, pattern):
        return [cell for cell in self.notes() if re.search(pattern, cell, re.IGNORECASE)]

    # Output

    def set(self, section, key, value):
        if (section, key) in UNSUPPORTED_SETTINGS.get(self.fw_version, set()):
            self.warn(f"[{section}] {key} = {value} isn't supported by {self.fw_version} firmware. Left out.")
            return
        self.values.setdefault(section, {})[key] = value

    def get(self, section, key):
        return self.values.get(section, {}).get(key)

    def warn(self, message):
        self.warnings.append(message)

    def allowed(self, section, key):
        return [v.strip('"') for v in self.settings.allowed.get((section, key), [])]

    # Profiles

    def keyset_setting(self, name, default=None):
        return self.profiles["keysets"].get(name, default)

    def set_keys(self, keys, keyset_id):
        # Profile key values may contain {keyset}, filled with the configuration's LEAF keyset
        for slot, value in keys.items():
            if "{keyset}" in value and not keyset_id:
                self.warn(f"keys {slot} needs a Keyset ID ({value}). Set it manually.")
                continue
            self.set("keys", slot, value.format(keyset=keyset_id))

    def apply_rule(self, rule, keyset_id):
        for section, settings in rule.get("settings", {}).items():
            for key, value in settings.items():
                self.set(section, key, value)
        self.set_keys(rule.get("keys", {}), keyset_id)
        if rule.get("warning"):
            self.warn(rule["warning"])

    # Keysets

    def keyset_id(self):
        match = re.search(r"\b(?:LM|L|M)k(\d{5})\b", self.text("keyset_id"), re.IGNORECASE)
        if match:
            return "Lk" + match.group(1)
        # Older configurations only name the customer keyset in the notes
        for keyset in re.findall(r"\bLk(\d{5})\b", self.text("custom_application_notes"), re.IGNORECASE):
            if "Lk" + keyset != self.keyset_setting("manufacturer_keyset"):
                return "Lk" + keyset
        return None

    def second_keyset_id(self, keyset_id):
        for keyset in re.findall(r"\bLk(\d{5})\b", self.text("custom_application_notes"), re.IGNORECASE):
            keyset = "Lk" + keyset
            if keyset not in (keyset_id, self.keyset_setting("manufacturer_keyset")):
                return keyset
        return None

    # Rules, in the order they're applied

    def build(self):
        keyset_id = self.keyset_id()
        self.mfg_data()
        self.av()
        self.rfid_av()
        self.wiegand()
        self.host_communication()
        self.tamper()
        self.ble()
        self.leaf(keyset_id)
        self.wallet(keyset_id)
        self.wallet_override()
        csn_rows_on = self.csn()
        self.lf()
        self.custom_hf(keyset_id)
        self.nfc_protocols(csn_rows_on)
        self.ini_lines_in_notes()
        return self

    def mfg_data(self):
        config_id = self.config.config_id_dec
        if config_id is None:
            self.warn(f"{self.config.config_name} has no Config ID. cfg_id left at default.")
            return
        self.set("mfg_data", "cfg_id", f"0x{int(config_id):04X}")

    def color(self, field, label, section, key):
        value = self.lower(field)
        if not value:
            return None
        if value not in self.allowed(section, key):
            self.warn(f"{label} '{self.text(field)}' isn't a color the reader supports "
                      f"({', '.join(self.allowed(section, key))}). Left at default.")
            return None
        return value

    def av(self):
        idle = self.color("idle_led", "Idle LED", "av", "idle_color")
        if idle:
            self.set("av", "idle_color", quoted(idle))
        if self.is_off("beeper"):
            self.set("av", "silence_beeper", "true")

    def rfid_av(self):
        color = self.color("credential_report_led", "Credential Report LED", "rfid/av", "color")
        if color == "off":
            self.set("rfid/av", "led_enabled", "false")
        if color:
            self.set("rfid/av", "color", quoted(color))

    def wiegand(self):
        keypad = self.lower("keypad_format")
        if not keypad:
            return
        default_format = KEYPAD_FORMATS.get(keypad)
        if default_format:
            self.set("wiegand", "default_format", quoted(default_format))
        else:
            self.warn(f"Keypad Format '{self.text('keypad_format')}' isn't recognized. Left at default.")

    def host_communication(self):
        if self.mentions(r"wiegand[\s-]*only|only\s+wiegand"):
            self.set("host_communication", "mode", quoted("wiegand"))

    def tamper(self):
        if self.is_off("tamper_monitoring"):
            self.set("tamper", "wiegand_reporting_enabled", "false")
            self.set("tamper", "osdp_reporting_enabled", "false")

    def ble(self):
        functionality = self.lower("ble_functionality")
        disabled = functionality == "disabled"
        credentials = any(s in functionality for s in
                          ("mypass", "admin + credentials", "custom credential", "credentials only"))

        # A custom advertising name still needs BLE broadcasting, so BLE stays on for those
        custom_name = self.ble_name()
        if disabled and not custom_name:
            self.set("ble", "enabled", "false")

        self.set("mypass", "km1_se_slot_nb", "03")
        self.set("mypass", "km2_se_slot_nb", "13")
        self.set("mypass", "kc1_se_slot_nb", "04")
        self.set("mypass", "kc2_se_slot_nb", "14")
        self.set("mypass", "allow_credentials", ini_bool(credentials))

        legacy = self.is_on("legacy_credentials")
        if legacy:
            self.set("ble/configure", "allow_credentials", "true")
            entry = next((e for e in self.profiles["ble_names"] if e["name"] == custom_name), None)
            if entry and entry.get("if_legacy_credentials"):
                self.apply_rule(entry["if_legacy_credentials"], None)

        # Legacy credential configurations leave the MyPass NFC app off
        self.set("rfid/hf/app/mypass", "enabled", ini_bool(credentials and not legacy))
        self.mobile_keyset(credentials)

        # all_keys turns on MyPass keyset 2, which lives in keys slot13/slot14
        self.set("mypass", "all_keys", ini_bool(self.get("keys", "slot13") is not None))

    def ble_name(self):
        name = self.text("ble_advertising_name").upper()
        if not name:
            adv_config = self.lower("ble_advertising_config")
            name = next((e["name"] for e in self.profiles["ble_names"] if re.search(e["pattern"], adv_config)), None)
        if name and name != "APEX":
            if len(name) > 5:
                self.warn(f"BLE name '{name}' is longer than 5 characters.")
            self.set("ble/adv_data", "name_complete", quoted(name))
            return name
        return None

    def mobile_keyset(self, credentials):
        mobile_keyset = self.lower("mobile_keyset")

        # Transport keysets ("<Vendor> Transport") come from customer_profiles.json
        transport = self.profiles["mobile_keysets"].get(mobile_keyset)
        if transport:
            if credentials or not transport.get("requires_credentials", True):
                self.set_keys(transport.get("keys", {}), None)
            return
        if not credentials:
            return
        if mobile_keyset != "custom":
            if mobile_keyset:
                self.warn(f"Mobile Keyset '{self.text('mobile_keyset')}' isn't WaveLynx Transport, AMAG Transport "
                          "or Custom. Mobile keys (slot13/slot14) left unset.")
            return

        mobile_notes = self.text("mobile_notes")
        for test_keyset in self.keyset_setting("test_mobile_keysets", []):
            if re.search(test_keyset["pattern"], mobile_notes, re.IGNORECASE):
                self.set_keys(test_keyset["keys"], None)
                return

        # Mobile Notes like "Custom mobile Mk5XXXX" name the keyset. Otherwise it's the LEAF keyset.
        match = re.search(r"\bL?Mk(\d{5})\b", mobile_notes, re.IGNORECASE)
        keyset = "Lk" + match.group(1) if match else self.keyset_id()
        if not keyset:
            self.warn("Mobile Keyset is 'Custom' but no Mk/Lk keyset was found. Set keys slot13/slot14 (Mkm/Mkc) manually.")
            return

        if int(keyset[2:]) <= self.keyset_setting("last_separate_mobile_keyset", -1):
            # Older keysets keep their mobile keys in a separate MkXXXXX keyset
            keyset = "Mk" + keyset[2:]
            self.warn(f"Mobile keys use the separate {keyset} keyset (older keyset). Verify on a reader.")

        # Key pair 2 (notes mention Mkm2/Mkc2): pair 1 goes in MyPass keyset 1, pair 2 in keyset 2
        if re.search(r"\bmk[mc]2\b", mobile_notes, re.IGNORECASE):
            self.set("keys", "slot03", f"{keyset}:Mkm")
            self.set("keys", "slot04", f"{keyset}:Mkc")
            self.set("keys", "slot13", f"{keyset}:Mkm2")
            self.set("keys", "slot14", f"{keyset}:Mkc2")
        else:
            self.set("keys", "slot13", f"{keyset}:Mkm")
            self.set("keys", "slot14", f"{keyset}:Mkc")

    def leaf(self, keyset_id):
        leaf_si = self.config.leaf_si_enabled
        leaf_cc = self.config.leaf_cc_enabled or self.is_on("custom_keyset")

        if leaf_cc and self.text("keyset_id") and not keyset_id:
            self.warn(f"Keyset ID '{self.text('keyset_id')}' isn't a LEAF keyset (LkXXXXX). Leaf Cc left off.")
            leaf_cc = False
        if not (leaf_si or leaf_cc):
            return

        self.set("rfid/hf/app/leaf/desfire", "enabled", "true")
        self.set("rfid/hf/app/leaf/desfire/1", "enabled", ini_bool(leaf_si))
        if leaf_si:
            # LEAF Verified (DUOX OpenID) is on whenever LEAF Si is on
            self.set("rfid/hf/app/leaf/duox/openid", "enabled", "true")
        self.set("rfid/hf/app/leaf/desfire/1", "app_id", quoted("F51CD8"))
        self.set("rfid/hf/app/leaf/desfire/1", "card_key_nb", "2")
        self.set("rfid/hf/app/leaf/desfire/1", "se_key_nb", "2")

        self.set("rfid/hf/app/leaf/desfire/2", "enabled", ini_bool(leaf_cc))
        self.set("rfid/hf/app/leaf/desfire/2", "app_id", quoted("F51CDB"))
        self.set("rfid/hf/app/leaf/desfire/2", "card_key_nb", "8")
        self.set("rfid/hf/app/leaf/desfire/2", "se_key_nb", "11")

        if not leaf_cc:
            return
        if not keyset_id and not self.is_on("custom_keyset"):
            keyset_id = self.keyset_setting("manufacturer_keyset")  # No custom keyset: manufacturer keyset
        if keyset_id:
            self.set("keys", "slot11", f"{keyset_id}:Kc8")  # Always Kc8, as in nearly every master INI
        else:
            self.warn("Leaf Cc is on but no Keyset ID (LkXXXXX) was found. Set keys slot11 manually.")

        # A second customer keyset in the notes reads through desfire/3, with its Kc8 in slot12
        second = self.second_keyset_id(keyset_id)
        if second:
            self.set("rfid/hf/app/leaf/desfire/3", "enabled", "true")
            self.set("rfid/hf/app/leaf/desfire/3", "app_id", quoted("F51CDB"))
            self.set("rfid/hf/app/leaf/desfire/3", "card_key_nb", "8")
            self.set("rfid/hf/app/leaf/desfire/3", "se_key_nb", "12")
            self.set("keys", "slot12", f"{second}:Kc8")

    def wallet(self, keyset_id):
        apple = self.is_on("ecp_desfire")
        android = self.is_on("mifare_2go")
        any_wallet = apple or android

        nfc = self.lower("nfc_functionality")
        if nfc == "disabled" and any_wallet:
            self.warn("NFC Functionality is 'Disabled' but a wallet is On. Check the configuration.")
        elif nfc == "wallet" and not any_wallet:
            self.warn("NFC Functionality is 'Wallet' but both wallets are Off. Check the configuration.")

        # Without a custom keyset the wallet uses the manufacturer wallet keys in slots 05-07.
        # Otherwise the customer's wallet keys go in slots 15-17.
        manufacturer_wallet = any_wallet and self.is_off("custom_keyset")
        first_slot = 5 if manufacturer_wallet else 15
        self.set("rfid/hf/app/meridian", "privacy_se_key_nb", f"{first_slot:02d}")
        self.set("rfid/hf/app/meridian", "credential_se_key_nb", f"{first_slot + 1:02d}")
        self.set("rfid/hf/app/mifare_2go/generic", "se_key_nb", f"{first_slot + 2:02d}")

        if apple:
            self.set("rfid/hf/app/meridian", "enabled", "true")
            bit_count = self.config.meridian_bit_count
            if bit_count in (32, 40, 57, 128):
                self.set("rfid/hf/app/meridian", "bit_nb", str(bit_count))
            elif bit_count:
                self.warn(f"Apple Meridian Bit Count '{bit_count}' isn't valid. bit_nb left at 40.")
        if android:
            self.set("rfid/hf/app/mifare_2go/generic", "enabled", "true")

        tci = self.text("ecp_tci")
        default_tci = self.profiles["default_tci"]
        if re.fullmatch(r"[0-9A-Fa-f]{6}", tci):
            self.set("rfid/hf/app/wallet", "terminal_id", quoted(tci.upper()))
        elif default_tci:
            self.set("rfid/hf/app/wallet", "terminal_id", quoted(default_tci))
            if any_wallet:
                self.warn(f"A wallet is On but ECP TCI '{tci}' isn't 6 hex characters. Used the default TCI {default_tci}.")
        elif any_wallet:
            self.warn(f"A wallet is On but ECP TCI '{tci}' isn't 6 hex characters. Set terminal_id manually.")

        # A wallet override (customer_profiles.json) brings its own wallet keys
        if not any_wallet or manufacturer_wallet or self.find_wallet_override():
            return
        if keyset_id:
            self.set("keys", "slot15", f"{keyset_id}:Kr0")
            self.set("keys", "slot16", f"{keyset_id}:Kr1")
            self.set("keys", "slot17", f"{keyset_id}:Kr5")
        else:
            self.warn("A wallet is On but no Keyset ID was found. Set keys slot15-17 (Kr0/Kr1/Kr5) manually.")

    def find_wallet_override(self):
        name = self.config.config_name.upper()
        for override in self.profiles["wallet_overrides"]:
            if re.search(override["config_name_pattern"], name):
                return override
            exclude = override.get("exclude_pattern")
            for cell in self.cells_mentioning(override["mention_pattern"]):
                if not exclude or not re.search(exclude, cell, re.IGNORECASE):
                    return override
        return None

    def wallet_override(self):
        override = self.find_wallet_override()
        if not override:
            return
        self.apply_rule(override, None)
        for extra in override.get("if_mentioned", []):
            if self.mentions(extra["pattern"]):
                self.apply_rule(extra, None)

    def csn(self):
        rows_on = [f for f in CSN_ROWS if self.lower(f) and self.lower(f) not in OFF_VALUES]
        if not rows_on:
            self.set("rfid/hf/app/csn", "enabled", "false")
            for key in CSN_DISABLED_KEYS:
                self.set("rfid/hf/app/csn", key, "0xFFFF")
            return rows_on

        for field, keys in CSN_ROWS.items():
            if field in rows_on:
                value = self.text(field)
                csn_format = CSN_FORMATS.get(re.sub(r"[\s_-]", "", value.lower()))
                if csn_format is None:
                    self.warn(f"{CSN_LABELS[field]} '{value}' has no known CSN format. Left as-is (0x0000).")
                    csn_format = "0x0000"
            else:
                csn_format = "0xFFFF"
            for key in keys:
                self.set("rfid/hf/app/csn", key, csn_format)
        return rows_on

    def nfc_protocols(self, csn_rows_on):
        # A = ISO14443-A, B = ISO14443-B, F = FeliCa, V = ISO/PICO15693. All CSNs on keeps "ABFV".
        if len(csn_rows_on) == len(CSN_ROWS):
            return
        iclass_app = self.get("rfid/hf/app/iclass", "enabled") == "true"
        needs_v = iclass_app or "iclass_csn" in csn_rows_on or "iso_15693_csn" in csn_rows_on
        self.set("rfid/hf/nfc", "enabled_protocols", quoted("AV" if needs_v else "A"))

    def lf(self):
        filter_on = self.is_on("prox_filter")

        # ASK needs LF. Both prox types off turns LF off, unless a prox filter still needs it.
        if self.is_off("fsk_prox") and self.is_off("ask_prox") and not filter_on:
            self.set("rfid/lf", "enabled", "false")

        if self.text("casi_output_format").upper() == "CASI-4001":
            self.set("rfid/lf", "ask_output_format", quoted("casi_01"))

        if filter_on:
            description = self.text("prox_filter_description")
            settings, problem = build_prox_filter(description)
            if problem:
                self.warn(f"Prox Filter is On but {problem}: '{description}'. Set rfid/lf filter_* values manually.")
            else:
                for key, value in settings.items():
                    self.set("rfid/lf", key, value)
        else:
            # Disabled filter values used by the master INIs
            self.set("rfid/lf", "filter_function", quoted("equal"))
            self.set("rfid/lf", "filter_bit_len", "40")
            self.set("rfid/lf", "filter_mask", quoted("0000000000"))
            self.set("rfid/lf", "filter_value", quoted("0000000000"))

    def apply_keyword_app(self, app, keyset_id):
        # Turns an app on when the notes mention it. With variants (e.g. classic / desfire), a variant
        # is used when the text mentions it, or when the text mentions none of the variants.
        cells = self.cells_mentioning(app["pattern"])
        if not cells:
            return False
        self.apply_rule(app, keyset_id)
        variants = app.get("variants", [])
        for text in (cells if app.get("per_cell") else [" ".join(cells)]):
            matched = [v for v in variants if re.search(v["pattern"], text, re.IGNORECASE)]
            for variant in matched or variants:
                self.apply_rule(variant, keyset_id)
        return True

    def custom_hf(self, keyset_id):
        detected = []
        # Only an explicit "iClass enabled" / "enable iClass" / "iClass app", not CSN descriptions
        if self.mentions(r"enabl\w*\s+iclass|iclass\s+(app\w*\s+)?(is\s+)?enabl|iclass\s+(app|application|support)"):
            self.set("rfid/hf/app/iclass", "enabled", "true")
            self.set("rfid/hf/app/iclass", "auth_err_report_csn", "true")
            detected.append("iClass")

        for app in self.profiles["keyword_apps"]:
            if self.apply_keyword_app(app, keyset_id):
                detected.append(app["name"])

        # Customer profiles match the letters in the config name ("CXY4" -> "XY")
        match = re.match(r"C([A-Z]+)\d", self.config.config_name.upper())
        profile = match and next((p for p in self.profiles["customer_profiles"] if p["config_prefix"] == match.group(1)), None)
        if profile:
            self.apply_rule(profile, keyset_id)
            if self.is_on("legacy_credentials") and profile.get("if_legacy_credentials"):
                self.apply_rule(profile["if_legacy_credentials"], keyset_id)
            detected.append(profile["name"])

        if self.mentions(r"nexpacs") and self.get("rfid/hf/app/nexpacs", "enabled") != "true":
            self.warn("NexPacs is mentioned but this customer has no NexPacs profile. Configure [rfid/hf/app/nexpacs] manually.")

        other = self.text("other_custom_hf_application")
        if other and other.lower() != "none":
            if detected:
                self.warn(f"Other Custom HF Application is '{other}'. Found {', '.join(detected)}. Check nothing else is needed.")
            else:
                self.warn(f"Other Custom HF Application is '{other}' but no known app is mentioned. Configure it manually.")

    def ini_lines_in_notes(self):
        # Engineers sometimes write INI settings straight into the notes (e.g. panel-specific [osdp]
        # flags). They're applied last, so they win over every other rule.
        applied = []
        for cell in self.notes():
            if not re.search(r"^\s*\[[\w/]+\]\s*$", cell, re.M):
                continue
            section = None
            for line in cell.splitlines():
                line = line.strip()
                header = re.fullmatch(r"\[([\w/]+)\]", line)
                if header:
                    section = header.group(1)
                    continue
                setting = re.fullmatch(r"(\w+)\s*=\s*(.+?)\s*(;.*)?", line)
                if not section or not setting:
                    continue
                key, value = setting.group(1), setting.group(2)
                if not self.settings.has(section, key):
                    self.warn(f"The notes set [{section}] {key}, which isn't in settings_default.ini. Ignored.")
                    continue
                self.set(section, key, value)
                applied.append(f"[{section}] {key} = {value}")
        if applied:
            self.warn(f"Applied {len(applied)} INI settings written in the notes: {'; '.join(applied)}.")


def build_ini(config, fw_version=None):
    """Returns (ini_text, warnings) for a configuration. fw_version leaves out settings it doesn't support."""
    settings, profiles, has_profiles = _rules_data()
    builder = Builder(config, settings, profiles, fw_version).build()

    warnings = list(builder.warnings)
    if not has_profiles:
        warnings.append("customer_profiles.json is missing from INI_PRIVATE_DIR. Customer and partner apps weren't generated.")
    warnings += [f"Check: {p}" for p in check_ini(settings, builder.values, profiles["keysets"])]
    return render_ini(settings, builder.values), warnings


def generate_ini_from_config(config, output_path, fw_version=None):
    """
    Writes the configuration's INI to output_path and returns its warnings, which are also
    written at the top of the file as comments.
    """
    ini_text, warnings = build_ini(config, fw_version)

    header = [f"; Generated by CALIGA from the {config.config_name} database record"
              + (f" for {fw_version}." if fw_version else ".")]
    if warnings:
        header.append("; Needs manual review:")
        header.extend(f";   - {w}" for w in warnings)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(header) + "\n\n" + ini_text)

    log.info("Generated INI for %s -> %s (%d warnings)", config.config_name, output_path, len(warnings))
    return warnings
