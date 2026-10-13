"""
Turns a prox filter description like "Ignore W36-8 with a facility code 15" into rfid/lf filter
settings. Copied from INI-Generator (ini_generator/prox_filter.py).
"""
import re

# Field bit positions per Wiegand format, numbered 1..N from the first (most significant) bit.
# FC = facility code, BID = badge ID, TC/CC/IC = other code fields.
BIT_MAPS = {
    "W26-0": {"FC": (2, 9), "BID": (10, 25)},
    "W26-1": {"FC": (2, 9), "BID": (10, 25)},
    "W26-2": {"FC": (2, 13), "BID": (14, 25)},

    "W28-0": {"FC": (5, 12), "BID": (13, 27)},
    "W28-1": {"FC": (5, 12), "BID": (13, 27)},
    "W28-2": {"FC": (4, 11), "BID": (12, 27)},
    "W28-3": {"FC": (4, 11), "BID": (12, 27)},
    "W28-4": {"FC": (5, 12), "BID": (13, 27)},

    "W30-T": {"TC": (1, 4), "BID": (5, 30)},

    "W32-0": {"FC": (4, 14), "BID": (15, 31)},
    "W32-1": {"FC": (1, 14), "BID": (15, 32)},
    "W32-2": {"BID": (4, 30)},
    "W32-3": {"BID": (2, 31)},
    "W32-4": {"CC": (2, 7), "FC": (8, 15), "BID": (16, 31)},
    "W32-5": {"BID": (1, 32)},

    "W33-0": {"FC": (2, 8), "BID": (9, 32)},
    "W33-1": {"FC": (2, 8), "BID": (9, 32)},

    "W34-0": {"FC": (9, 16), "BID": (17, 32)},
    "W34-1": {"FC": (2, 13), "BID": (14, 33)},
    "W34-2": {"FC": (1, 16), "BID": (17, 34)},
    "W34-3": {"FC": (1, 17), "BID": (18, 34)},
    "W34-4": {"FC": (2, 17), "BID": (18, 33)},
    "W34-5": {"BID": (2, 33)},
    "W34-6": {"FC": (2, 17), "BID": (18, 33)},

    "W35-0": {"FC": (3, 14), "BID": (15, 34)},
    "W35-1": {"FC": (3, 14), "BID": (15, 34)},

    "W36-0": {"BID": (1, 32)},
    "W36-1": {"FC": (2, 13), "BID": (14, 34)},
    "W36-2": {"FC": (2, 11), "BID": (12, 35)},
    "W36-3": {"FC": (2, 17), "BID": (18, 35)},
    "W36-4": {"FC": (2, 17), "BID": (18, 35)},
    "W36-5": {"FC": (2, 15), "BID": (16, 35)},
    "W36-6": {"FC": (2, 17), "BID": (18, 33)},
    "W36-7": {"BID": (2, 35)},
    "W36-8": {"FC": (1, 5), "BID": (6, 36)},
    "W36-9": {"FC": (1, 6), "BID": (7, 36)},
    "W36-10": {"FC": (1, 6), "BID": (7, 35)},

    "W37-0": {"FC": (2, 17), "BID": (18, 36)},
    "W37-1": {"BID": (2, 36)},
    "W37-2": {"FC": (2, 13), "BID": (14, 36)},
    "W37-3": {"FC": (1, 14), "BID": (15, 32)},
    "W37-4": {"FC": (3, 7), "BID": (8, 36)},
    "W37-5": {"FC": (2, 7), "BID": (8, 36)},
    # Assumed: a reference filter confirms BID = bits 7-36. FC is taken as the remaining bits 2-6.
    "W37-6": {"FC": (2, 6), "BID": (7, 36)},

    "W38-1": {"FC": (2, 9), "BID": (10, 37)},
    "W38-2": {"FC": (1, 8), "BID": (9, 38)},

    "W40-0": {"FC": (2, 11), "BID": (12, 39)},
    "W40-1": {"FC": (2, 20), "BID": (21, 39)},
    "W40-2": {"BID": (2, 39)},
    "W40-3": {"FC": (2, 9), "BID": (10, 39)},
    "W40-4": {"FC": (1, 4), "BID": (5, 40)},

    "W45-0": {"FC": (2, 11), "BID": (12, 44)},

    "W46-T": {"TC": (1, 8), "FC": (9, 16), "BID": (17, 46)},

    "W47-0": {"BID": (1, 30), "IC": (31, 37), "FC": (38, 47)},

    "W48-0": {"IC": (2, 7), "FC": (8, 27), "BID": (28, 47)},
    "W48-1": {"BID": (1, 48)},
    "W48-2": {"FC": (3, 24), "BID": (25, 47)},

    "W53-0": {"BID": (2, 53)},

    "W55-0": {"BID": (2, 54)},

    "W56-0": {"BID": (2, 55)},
    "W56-1": {"FC": (1, 24), "BID": (25, 56)},

    "W57-0": {"BID": (2, 33), "FC": (34, 53)},

    "W63-0": {"BID": (3, 34), "IC": (35, 41), "FC": (42, 61)},
    "W63-T": {"TC": (1, 7), "BID": (11, 63)},

    "W64-T": {"TC": (1, 8), "BID": (12, 64)},
    "W64-S": {"FC": (1, 32), "BID": (33, 64)},

    "W72-0": {"BID": (1, 40), "FC": (41, 72)},
}

FILTER_FUNCTIONS = {
    "EQUAL": "equal",
    "GREATER_OR_EQUAL": "greater_or_equal",
    "LESS_OR_EQUAL": "less_or_equal",
}

# filter_mask and filter_value are 10 hex characters (40 bits) in settings_default.ini
REGISTER_BITS = 40


def parse_prox_description(text):
    # Turns "Ignore W36-8 with a facility code 15" into a format, bit count and one rule.
    if not text or not isinstance(text, str):
        return None

    text = text.replace("≥", ">=").replace("≤", "<=")
    lower_text = text.lower()

    # Clean up broken spaces around numbers/commas like "524, 286" -> "524,286"
    normalized_text = re.sub(r'(\d+),\s+(\d+)', r'\1,\2', text.strip())

    # "W37-1", "37-4" (W left off) or "26-bit" / "26 bit"
    format_match = re.search(r'\bW?(\d{2})-(\d+|[A-Z])\b|\b(\d{2})[\s-]bit\b', normalized_text, re.IGNORECASE)
    if not format_match:
        return None

    if format_match.group(3):
        bit_count = int(format_match.group(3))
        fmt = f"W{bit_count}-1" if bit_count == 37 else f"W{bit_count}-0"
    else:
        bit_count = int(format_match.group(1))
        fmt = f"W{bit_count}-{format_match.group(2).upper()}"

    working_text = normalized_text.replace(format_match.group(0), " ")
    digits = [int(d.replace(",", "")) for d in re.findall(r'[0-9,]+', working_text) if d.replace(",", "").isdigit()]

    is_blanket_filter = ("all" in lower_text or "credentials" in lower_text) and not digits
    is_explicit_disable = any(phrase in lower_text for phrase in ["not block", "not ignore", "allow existing"])

    if is_blanket_filter:
        rule = {"target": "ALL_FIELDS", "value": 0, "high_bound": 0, "logic": "GREATER_OR_EQUAL", "disabled": False}
    elif digits:
        if "fc" in lower_text or "facility" in lower_text or "fac" in lower_text:
            target = "FC"
        elif "bid" in lower_text or "badge" in lower_text:
            target = "BID"
        else:
            target = "BID" if digits[0] > 255 else "FC"

        if "less" in lower_text or "<" in lower_text:
            logic = "GREATER_OR_EQUAL" if (">=" in lower_text or "greater" in lower_text) else "LESS_OR_EQUAL"
        elif "greater" in lower_text or ">" in lower_text or len(digits) >= 2:
            logic = "GREATER_OR_EQUAL"
        else:
            logic = "EQUAL"

        rule = {
            "target": target,
            "value": digits[0],
            "high_bound": digits[1] if len(digits) >= 2 else 0,
            "logic": logic,
            "disabled": is_explicit_disable,
        }
    else:
        return None

    return {"format": fmt, "bit_count": bit_count, "rules": [rule], "raw": text}


def generate_hex_codes(details):
    # Returns (mask, value) as 10-character hex strings, or None if the format is unknown.
    mapping = BIT_MAPS.get(details["format"])
    bit_count = details["bit_count"]
    rule = details["rules"][0]
    target = rule["target"]
    value = rule["value"]
    high_bound = rule.get("high_bound", 0)

    mask_int = 0
    value_int = 0

    if target == "ALL_FIELDS":
        mask_int = (1 << bit_count) - 1
    elif mapping and target in mapping:
        start, end = mapping[target]
        if high_bound > 0:
            # A range "X <= BID <= Y" compares only the low bits that Y needs (524286 -> 19 bits),
            # one bit left of the trailing parity bit
            field_mask = (1 << high_bound.bit_length()) - 1
            mask_int = field_mask << 1
            value_int = (value & field_mask) << 1
        else:
            right_shift = bit_count - end
            field_mask = (1 << (end - start + 1)) - 1
            mask_int = field_mask << right_shift
            value_int = (value & field_mask) << right_shift
    else:
        return None

    limit = (1 << REGISTER_BITS) - 1
    hex_len = REGISTER_BITS // 4
    return f"{mask_int & limit:0{hex_len}X}", f"{value_int & limit:0{hex_len}X}"


def build_prox_filter(description):
    # Returns ({ini key: value}, problem). problem is None when the filter was fully built.
    if description and re.search(r"\bexcept\b", description, re.IGNORECASE):
        return None, "filters with exceptions ('all except ...') cannot be expressed as one mask/value"

    details = parse_prox_description(description)
    if not details:
        return None, "could not find a Wiegand format (like W37-1) and a rule in the description"

    if details["bit_count"] > REGISTER_BITS:
        return None, f"{details['format']} is longer than the 40-bit filter register"

    rule = details["rules"][0]
    if rule["disabled"]:
        return None, "the description says not to block these credentials"

    codes = generate_hex_codes(details)
    if not codes:
        return None, f"no bit layout for {rule['target']} in {details['format']}"

    mask, value = codes
    return {
        "filter_enabled": "true",
        "filter_function": f'"{FILTER_FUNCTIONS[rule["logic"]]}"',
        "filter_bit_len": str(details["bit_count"]),
        "filter_mask": f'"{mask}"',
        "filter_value": f'"{value}"',
    }, None
