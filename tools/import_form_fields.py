"""
Fills configuration fields from the original configuration forms (CSV exports), where CALIGA's
value is missing or isn't a value CALIGA's dropdowns offer.

When the configurations were first loaded, the notes columns came in empty, Mobile Keyset came in
as "Enabled", and some notes landed in the wrong column. The INI builder needs these fields.

A value someone could have picked in CALIGA (a dropdown option, or text in a text field) is never
overwritten. Each changed configuration gets a revision history entry.

    python tools/import_form_fields.py <forms folder>            # dry run: shows what would change
    python tools/import_form_fields.py <forms folder> --apply    # writes the changes

The forms folder holds one <config name>.csv per configuration, like INI-Generator's
tests/fixtures/forms.
"""
import csv
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REVISION_USER = "system (import from configuration form)"

ON_OFF = ["On", "Off"]

# CALIGA field -> (form labels, allowed values). None means a free-text field, filled only when blank.
FIELDS = {
    "mobile_keyset": (("Mobile Keyset",), ["WaveLynx Transport", "AMAG Transport", "Custom"]),
    "custom_application_notes": (("Custom / Application / Notes",), None),
    "mobile_notes": (("Mobile Notes",), None),
    "other_custom_hf_application": (("Other Custom HF Application",), None),
    "prox_filter_description": (("Prox / Filter / Description", "Prox Filter Description"), None),
    "ble_functionality": (("BLE Functionality", "Mobile Functionality"),
                          ["Disabled", "Admin + Credentials", "Credentials Only", "Admin Only",
                           "Admin + Custom Credential", "Admin + MyPass"]),
    "nfc_functionality": (("NFC Functionality",), ["Disabled", "Enabled", "Wallet", "MyPass"]),
    "fsk_prox": (("FSK Prox",), ON_OFF),
    "ask_prox": (("ASK Prox",), ON_OFF),
    "prox_filter": (("Prox Filter",), ON_OFF),
    "legacy_credentials": (("Legacy Credentials",), ON_OFF),
    "transport_mode": (("Transport Mode",), ON_OFF),
    "ecp_desfire": (("ECP DESFire", "ECP DESFire (Apple Wallet)"), ON_OFF),
    "mifare_2go": (("MiFare2Go", "MiFare2Go (Android Wallet)"), ON_OFF),
}

# Note boxes on the right of the form: the text is in the cell under the label
BOX_FIELDS = {
    "notes_other_products": "Notes/Other Products",
    "card_notes": "Notes",
}

# Form wording -> CALIGA's On/Off
ON_OFF_WORDS = {"on": "On", "yes": "On", "enabled": "On", "off": "Off", "no": "Off", "disabled": "Off"}


def normalize_label(label):
    # "Custom\nApplication\nNotes" and "Custom / Application / Notes" both become "custom application notes"
    return re.sub(r"\s+", " ", re.sub(r"\s+/\s+", " ", label)).strip().lower()


def read_form(path):
    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig", errors="ignore")
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]


def form_value(rows, labels):
    """The first value after a label. Labels can be a prefix ("Legacy Credentials (Sym Blue, Brivo)")."""
    targets = [normalize_label(label) for label in labels]
    for row in rows:
        for i, cell in enumerate(row):
            label = normalize_label(cell)
            if any(label == t or label.startswith(t + " (") for t in targets):
                # Only the next 4 cells, so the Card Details column on the right is never read
                values = [c for c in row[i + 1:i + 5] if c]
                if values:
                    return values[0]
    return None


def box_value(rows, label):
    """The text under a note box label, in the same column within the next 3 rows."""
    target = normalize_label(label)
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if normalize_label(cell) == target:
                for below in rows[r + 1:r + 4]:
                    if c < len(below) and below[c]:
                        return below[c]
                return None
    return None


def matching_option(value, options):
    if value is None:
        return None
    if options is ON_OFF:
        return ON_OFF_WORDS.get(value.strip().lower())
    return next((o for o in options if o.lower() == value.strip().lower()), None)


def changes_for(config, rows):
    """Returns {field: (old, new)} for the fields the form should fill."""
    changes = {}
    for field, (labels, options) in FIELDS.items():
        current = (getattr(config, field) or "").strip()
        new = form_value(rows, labels)
        if not new or new.lower() == "none":
            continue
        if options is None:
            if not current or current.lower() == "none":
                changes[field] = (current, new)
        elif matching_option(current, options) is None:
            option = matching_option(new, options)
            if option:
                changes[field] = (current, option)

    for field, label in BOX_FIELDS.items():
        current = (getattr(config, field) or "").strip()
        new = box_value(rows, label)
        if new and not current:
            changes[field] = (current, new)
    return changes


def match_forms(configs, forms_dir, unmatched):
    """
    Yields (configuration, form file). "CPV1 (200-bit PIV).csv" matches CPV1. An exact file name
    wins over a suffixed one, so "CMC2(1).csv" is skipped when CMC2.csv exists. "Copy of ..." forms
    never match.
    """
    names = sorted(f[:-4] for f in os.listdir(forms_dir) if f.endswith(".csv"))
    exact = {n.upper() for n in names}
    for name in names:
        config = configs.get(name.upper())
        if config is None:
            lead = re.match(r"[A-Za-z0-9]+", name)
            lead = lead.group(0).upper() if lead else ""
            if lead not in exact:
                config = configs.get(lead)
        if config is None:
            unmatched.append(name)
        else:
            yield config, name + ".csv"


def main(forms_dir, apply):
    from app import app
    from models import db, Configuration, RevisionHistory

    with app.app_context():
        configs = {c.config_name.upper(): c for c in Configuration.query.all()}
        changed, unmatched = 0, []

        for config, file_name in match_forms(configs, forms_dir, unmatched):
            changes = changes_for(config, read_form(os.path.join(forms_dir, file_name)))
            if not changes:
                continue
            changed += 1
            print(f"\n{config.config_name}")
            for field, (old, new) in changes.items():
                print(f"  {field}: {old[:60]!r} -> {new[:60]!r}")
                if apply:
                    setattr(config, field, new)
            if apply:
                db.session.add(RevisionHistory(
                    config_name=config.config_name, user_email=REVISION_USER,
                    revision_details="Filled from the configuration form: " + ", ".join(changes) + "."))

        if apply:
            db.session.commit()
        print(f"\n{changed} configurations {'updated' if apply else 'would change'}.")
        if unmatched:
            print(f"{len(unmatched)} forms have no CALIGA configuration: {', '.join(unmatched)}")
        if not apply:
            print("Dry run only. Run again with --apply to write the changes.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    if len(args) != 1 or not os.path.isdir(args[0]):
        sys.exit(__doc__)
    os.environ.setdefault("JOB_WORKERS", "0")  # no background workers in a one-off script
    main(args[0], "--apply" in sys.argv)
