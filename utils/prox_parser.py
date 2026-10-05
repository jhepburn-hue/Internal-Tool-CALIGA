import re

def parse_prox_description(text):
    if not text or not isinstance(text, str):
        return None
    clean_text = text.strip()
    lower_text = clean_text.lower()

    normalized_text = re.sub(r'(\d+),\s+(\d+)', r'\1,\2', clean_text)
    format_match = re.search(r'(W\d{2}-\d+|W\d{2}-[A-Z]|\d{2}-bit)', normalized_text, re.IGNORECASE)
    if not format_match:
        return None

    fmt_raw = format_match.group(0).upper()
    if "BIT" in fmt_raw:
        bit_count = int(re.findall(r'\d+', fmt_raw)[0])
        fmt = f"W{bit_count}-1" if bit_count == 37 else f"W{bit_count}-0"
    else:
        fmt = fmt_raw
        bit_count = int(fmt[1:3])

    working_text = normalized_text.replace(format_match.group(0), " ")
    digits = [int(d.replace(",", "")) for d in re.findall(r'[0-9,]+', working_text) if d.replace(",", "").isdigit()]

    is_blanket_filter = ("all" in lower_text or "credentials" in lower_text) and not digits
    is_explicit_disable = any(phrase in lower_text for phrase in ["not block", "not ignore", "allow existing"])

    conditions = []
    if is_blanket_filter:
        conditions.append({
            "target": "ALL_FIELDS", "value": 0, "high_bound": 0, "logic": "GREATER_OR_EQUAL", "disabled": False
        })
    elif digits:
        target = "FC" if "fc" in lower_text or "facility" in lower_text or "fac" in lower_text else ("BID" if "bid" in lower_text or "badge" in lower_text else ("BID" if digits[0] > 255 else "FC"))
        if "less" in lower_text or "<" in lower_text:
            logic = "GREATER_OR_EQUAL" if (">=" in lower_text or "greater" in lower_text) else "LESS_OR_EQUAL"
        elif "greater" in lower_text or ">" in lower_text or len(digits) >= 2:
            logic = "GREATER_OR_EQUAL"
        else:
            logic = "EQUAL"

        val = digits[0]
        high_bound = digits[1] if len(digits) >= 2 else 0

        conditions.append({
            "target": target, "value": val, "high_bound": high_bound, "logic": logic, "disabled": is_explicit_disable
        })
    else:
        return None

    return {
        "format": fmt,
        "bit_count": bit_count,
        "rules": conditions,
        "raw": text
    }