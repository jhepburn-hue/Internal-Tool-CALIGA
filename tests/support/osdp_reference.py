"""Writes reference OSDP frames from osdp_library for tests/js/osdp-flasher.test.mjs. Usage: osdp_reference.py <repo root> <output.json>"""
import json, sys, os
sys.path.insert(0, sys.argv[1])
from osdp_library.osdp.message_handler import OsdpMessageHandler
from osdp_library.osdp.message import OsdpMessage, OsdpMessageControlInformation, OsdpMessageDirection, CrcEnabledFlag
from osdp_library.osdp.secure_channel import OsdpSecureChannelContext, ScbkMode
from osdp_library.osdp.command_payloads import OsdpPollPayload, OsdpIdReportRequestPayload, OsdpComsetPayload, OsdpFiletransferPayload
from osdp_library.osdp.constants import CommandTags, FileFragmentConstants
from osdp_library.osdp.utils import calc_crc

def command(address, seq, payload):
    h = OsdpMessageHandler(OsdpSecureChannelContext(None, mode=ScbkMode.DEFAULT), lambda: None, use_crc=True)
    h._OsdpMessageHandler__sequence = seq
    return bytes(h.create_outgoing_message(address, payload)).hex()

def reply(address, seq, code, data):
    msg = OsdpMessage(direction=OsdpMessageDirection.REPLY, address=address, command_reply_code=code,
                      message_control_info=OsdpMessageControlInformation(sequence=seq, crc_enabled_flag=CrcEnabledFlag.CRC, has_security_control_block=False),
                      security_block=None, data=bytearray(data) if data else None, mac=None, payload=None)
    return bytes(msg.to_bytearray()).hex()

frag = bytes(range(37))
out = {
    "crc": [{"data": d.hex(), "crc": calc_crc(d)} for d in [b"", b"\x53", bytes(range(256)), b"\x53\x01\x08\x00\x04\x60"]],
    "commands": [
        {"name": "poll a0 s0", "address": 0, "seq": 0, "code": 0x60, "data": "", "hex": command(0, 0, OsdpPollPayload(tag=CommandTags.POLL))},
        {"name": "poll a1 s2", "address": 1, "seq": 2, "code": 0x60, "data": "", "hex": command(1, 2, OsdpPollPayload(tag=CommandTags.POLL))},
        {"name": "id a7 s3", "address": 7, "seq": 3, "code": 0x61, "data": "00", "hex": command(7, 3, OsdpIdReportRequestPayload(tag=CommandTags.ID))},
        {"name": "comset a1 115200 s1", "address": 1, "seq": 1, "comset": [1, 115200],
         "hex": command(1, 1, OsdpComsetPayload(tag=CommandTags.COMSET, address=1, baud_rate=(115200).to_bytes(4, "little")))},
        {"name": "ft a1 s2", "address": 1, "seq": 2, "ft": [70000, 2048, frag.hex()],
         "hex": command(1, 2, OsdpFiletransferPayload(tag=CommandTags.FILETRANSFER, ft_type=FileFragmentConstants.FILE_TYPE_OPAQUE,
                                                     ft_size_total=70000, ft_offset=2048, ft_fragment_size=len(frag), ft_data=frag))},
        {"name": "ft finishing a3 s1", "address": 3, "seq": 1, "ft": [70000, 70000, ""],
         "hex": command(3, 1, OsdpFiletransferPayload(tag=CommandTags.FILETRANSFER, ft_type=FileFragmentConstants.FILE_TYPE_OPAQUE,
                                                     ft_size_total=70000, ft_offset=70000, ft_fragment_size=0, ft_data=b""))},
    ],
    "replies": [
        {"name": "ack", "address": 1, "seq": 1, "code": 0x40, "data": "", "hex": reply(1, 1, 0x40, b"")},
        {"name": "ftstat ok max 256", "address": 1, "seq": 2, "code": 0x7A, "data": "01000000000001",
         "hex": reply(1, 2, 0x7A, bytes.fromhex("01000000000001"))},
        {"name": "ftstat abort", "address": 2, "seq": 3, "code": 0x7A, "data": "0000" + "00" + "ffff" + "0000",
         "hex": reply(2, 3, 0x7A, bytes.fromhex("00" + "0000" + "ffff" + "0000"))},
        {"name": "nak", "address": 0, "seq": 1, "code": 0x41, "data": "03", "hex": reply(0, 1, 0x41, b"\x03")},
    ],
}
json.dump(out, open(sys.argv[2], "w"), indent=1)
print("wrote", sys.argv[2])
