import math

BIT_MAPS = {
    "W26-0": {"FC": (2, 9),   "BID": (10, 25)},
    "W26-1": {"FC": (2, 9),   "BID": (10, 25)},
    "W26-2": {"FC": (2, 13),  "BID": (14, 25)},
    "W28-0": {"FC": (5, 12),  "BID": (13, 27)},
    "W28-1": {"FC": (5, 12),  "BID": (13, 27)},
    "W28-2": {"FC": (4, 11),  "BID": (12, 27)},
    "W28-3": {"FC": (4, 11),  "BID": (13, 27)},
    "W28-4": {"FC": (5, 12),  "BID": (13, 27)},
    "W30-T": {"TC": (1, 4),   "BID": (5, 30)},
    "W32-0": {"FC": (4, 14),  "BID": (15, 31)},
    "W32-1": {"FC": (1, 14),  "BID": (15, 32)},
    "W32-2": {"BID": (4, 30)},
    "W32-3": {"BID": (2, 31)},
    "W32-4": {"CC": (2, 7), "FC": (8, 15), "BID": (16, 31)},
    "W32-5": {"BID": (1, 32)},
    "W33-0": {"FC": (2, 8),   "BID": (9, 32)},
    "W33-1": {"FC": (2, 8),   "BID": (9, 32)},
    "W34-0": {"FC": (9, 16),  "BID": (17, 32)},
    "W34-1": {"FC": (2, 13),  "BID": (14, 33)},
    "W34-2": {"FC": (1, 16),  "BID": (17, 34)},
    "W34-3": {"FC": (1, 17),  "BID": (18, 34)},
    "W34-4": {"FC": (2, 17),  "BID": (18, 33)},
    "W34-5": {"BID": (2, 33)},
    "W34-6": {"FC": (2, 17),  "BID": (18, 33)},
    "W35-0": {"FC": (3, 14),  "BID": (15, 34)},
    "W35-1": {"FC": (3, 14),  "BID": (15, 34)},
    "W36-0": {"BID": (1, 32)},
    "W36-1": {"FC": (2, 13),  "BID": (14, 34)},
    "W36-2": {"FC": (2, 11),  "BID": (12, 35)},
    "W36-3": {"FC": (2, 17),  "BID": (18, 35)},
    "W36-4": {"FC": (2, 17),  "BID": (18, 35)},
    "W36-5": {"FC": (2, 15),  "BID": (16, 35)},
    "W36-6": {"FC": (2, 17),  "BID": (18, 33)},
    "W36-7": {"BID": (2, 35)},
    "W36-8": {"FC": (1, 5),   "BID": (6, 36)},
    "W36-9": {"FC": (1, 6),   "BID": (7, 36)},
    "W36-10": {"FC": (1, 6),  "BID": (7, 35)},
    "W37-0": {"FC": (2, 17),  "BID": (18, 36)},
    "W37-1": {"BID": (2, 36)},
    "W37-2": {"FC": (2, 13),  "BID": (14, 36)},
    "W37-3": {"FC": (1, 14),  "BID": (15, 32)},
    "W37-4": {"FC": (3, 7),   "BID": (8, 36)},
    "W37-5": {"FC": (2, 7),   "BID": (8, 36)},
    "W38-1": {"FC": (2, 9),   "BID": (10, 37)},
    "W38-2": {"FC": (1, 8),   "BID": (9, 38)},
    "W40-0": {"FC": (2, 11),  "BID": (12, 39)},
    "W40-1": {"FC": (2, 20),  "BID": (21, 39)},
    "W40-2": {"BID": (2, 39)},
    "W40-3": {"FC": (2, 9),   "BID": (10, 39)},
    "W40-4": {"FC": (1, 4),   "BID": (5, 40)},
    "W45-0": {"FC": (2, 11),  "BID": (12, 44)},
    "W46-T": {"TC": (1, 8), "FC": (9, 16),   "BID": (17, 46)},
    "W47-0": {"BID": (1, 30), "IC": (31, 37), "FC": (38, 47)},
    "W48-0": {"IC": (2, 7), "FC": (8, 27),   "BID": (28, 47)},
    "W48-1": {"BID": (1, 48)},
    "W48-2": {"FC": (3, 24),  "BID": (25, 47)},
    "W53-0": {"BID": (2, 53)},
    "W55-0": {"BID": (2, 54)},
    "W56-0": {"BID": (2, 55)},
    "W56-1": {"FC": (1, 24),  "BID": (25, 56)},
    "W57-0": {"BID": (2, 33), "FC": (34, 53)},
    "W63-0": {"BID": (3, 34), "IC": (35, 41), "FC": (42, 61)},
    "W63-T": {"TC": (1, 7), "BID": (11, 63)},
    "W64-T": {"TC": (1, 8), "BID": (12, 64)},
    "W64-S": {"FC": (1, 32), "BID": (33, 64)},
    "W72-0": {"BID": (1, 40), "FC": (41, 72)},
}

def generate_hex_codes(details):
    if not details or "rules" not in details:
        return "0000000000", "0000000000"
    fmt = details.get("format")
    bit_count = details.get("bit_count", 0)

    reg_bits = 40 if bit_count <= 40 else math.ceil(bit_count / 4) * 4
    hex_len = reg_bits // 4

    mask_int = 0
    value_int = 0
    mapping = BIT_MAPS.get(fmt, {"FC": (1, 8), "BID": (2, 36)})

    rule = details["rules"][0]
    target = rule["target"]
    val = rule["value"]
    high_bound = rule.get("high_bound", 0)

    if target == "ALL_FIELDS":
        mask_int = (1 << bit_count) - 1
        value_int = 0
    elif target in mapping:
        start, end = mapping[target]
        if high_bound > 0:
            field_len = 19 if high_bound == 524286 else high_bound.bit_length()
            field_mask = (1 << field_len) - 1
            shift_amount = 1
            mask_int |= (field_mask << shift_amount)
            value_int |= ((val & field_mask) << shift_amount)
        else:
            right_shift = bit_count - end
            field_len = (end - start) + 1
            field_mask = (1 << field_len) - 1
            mask_int |= (field_mask << right_shift)
            value_int |= ((val & field_mask) << right_shift)

    bit_limit = (1 << reg_bits) - 1
    mask_int &= bit_limit
    value_int &= bit_limit
    mask_str = f"{mask_int:0{hex_len}X}"
    val_str = f"{value_int:0{hex_len}X}"

    return mask_str, val_str