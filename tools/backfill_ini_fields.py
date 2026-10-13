"""
Fills the LEAF, wallet, and BLE name fields of configurations from their master INIs in GCS.

Reads input/<version>/<config_name>.ini and works backwards from the INI builder's rules. Only
blank fields are filled, so values entered by hand are never overwritten. Each changed
configuration gets a revision history entry.

    python tools/backfill_ini_fields.py            # dry run: shows what would change
    python tools/backfill_ini_fields.py --apply    # writes the changes
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import app  # noqa: E402
from models import db, Configuration, RevisionHistory  # noqa: E402
from services.gcs_client import get_gcs_client, GCS_BUCKET_NAME  # noqa: E402
from services.ini_settings import load_profiles, load_settings  # noqa: E402

VERSIONS = ("v5.4.10", "v5.4.11")
REVISION_USER = "system (backfill from master INI)"
KEYSET_PATTERN = re.compile(r"\bLk\d{5}\b")


def parse_ini(text):
    """Returns {(section, key): value} with comments stripped."""
    values, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        header = re.match(r"\[(.+)\]$", line)
        if header:
            section = header.group(1)
            continue
        if "=" in line and section:
            key, value = line.split("=", 1)
            value = value.strip()
            value = value[:value.find('"', 1) + 1] if value.startswith('"') else value.split(";")[0].strip()
            values[(section, key.strip())] = value
    return values


def fields_from_ini(ini, master, manufacturer_keyset):
    """
    Works out CALIGA field values from a master INI.
    ini: effective settings (defaults with the master applied), used for enabled flags and bit count.
    master: only the master's own lines, used for keysets, TCI and BLE name, so default values
    (e.g. config_card = Lk70003:Kc5, name_complete = "APEX") aren't mistaken for config settings.
    """
    def on(section, key="enabled"):
        return ini.get((section, key)) == "true"

    def on_off(flag):
        return "On" if flag else "Off"

    fields = {}

    leaf = on("rfid/hf/app/leaf/desfire")
    fields["leaf_si"] = on_off(leaf and on("rfid/hf/app/leaf/desfire/1"))
    fields["leaf_cc"] = on_off(leaf and on("rfid/hf/app/leaf/desfire/2"))

    # The customer keyset is whichever Lk keyset the key slots use, other than the manufacturer's.
    keysets = {
        m for (section, key), value in master.items()
        if section == "keys" and key != "config_card" for m in KEYSET_PATTERN.findall(value)
    }
    customer_keysets = sorted(keysets - {manufacturer_keyset})
    if customer_keysets:
        # Prefer the Leaf Cc keyset in slot11 when there are several.
        slot11 = KEYSET_PATTERN.findall(master.get(("keys", "slot11"), ""))
        fields["keyset_id"] = slot11[0] if slot11 and slot11[0] in customer_keysets else customer_keysets[0]
        fields["custom_keyset"] = "On"
    elif manufacturer_keyset in keysets or master.get(("rfid/hf/app/meridian", "privacy_se_key_nb")) == "05":
        fields["custom_keyset"] = "Off"

    meridian = on("rfid/hf/app/meridian")
    fields["ecp_desfire"] = on_off(meridian)
    fields["mifare_2go"] = on_off(on("rfid/hf/app/mifare_2go/generic"))

    tci = master.get(("rfid/hf/app/wallet", "terminal_id"), "").strip('"')
    if re.fullmatch(r"[0-9A-Fa-f]{6}", tci) and tci != "000000":
        fields["ecp_tci"] = tci.upper()

    bit_nb = ini.get(("rfid/hf/app/meridian", "bit_nb"), "")
    if meridian and bit_nb in ("32", "40", "57", "128"):
        fields["meridian_bit_count"] = int(bit_nb)

    # APEX is the standard name, which the INI builder treats as no custom name.
    name = master.get(("ble/adv_data", "name_complete"), "").strip('"')
    if 0 < len(name) <= 5 and name.upper() != "APEX":
        fields["ble_advertising_name"] = name.upper()

    return fields


def main(apply):
    settings = load_settings()
    defaults = {(s, k): v for s, kv in settings.defaults.items() for k, v in kv.items()}
    manufacturer_keyset = load_profiles()[0]["keysets"].get("manufacturer_keyset")
    bucket = get_gcs_client().bucket(GCS_BUCKET_NAME)

    changed = skipped = 0
    for config in Configuration.query.filter(Configuration.status != 'Archived').order_by(Configuration.config_name):
        blob = next((b for b in (bucket.blob(f"input/{v}/{config.config_name}.ini") for v in VERSIONS) if b.exists()), None)
        if blob is None:
            skipped += 1
            continue

        master = parse_ini(blob.download_as_text())
        updates = {
            field: value for field, value in fields_from_ini({**defaults, **master}, master, manufacturer_keyset).items()
            if getattr(config, field) in (None, "")
        }
        if not updates:
            continue

        changed += 1
        summary = ", ".join(f"{field}={value}" for field, value in updates.items())
        print(f"{config.config_name}: {summary}")

        if apply:
            for field, value in updates.items():
                setattr(config, field, value)
            db.session.add(RevisionHistory(
                config_name=config.config_name,
                user_email=REVISION_USER,
                revision_details=f"Filled from master INI gs://{GCS_BUCKET_NAME}/{blob.name}: {summary}"
            ))

    if apply:
        db.session.commit()
    print(f"\n{'Updated' if apply else 'Would update'} {changed} configuration(s). "
          f"{skipped} had no master INI and were skipped.")


if __name__ == "__main__":
    with app.app_context():
        main(apply="--apply" in sys.argv)
