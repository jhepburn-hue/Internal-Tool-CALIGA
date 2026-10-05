import configparser

SETTING_DESCRIPTIONS = {
    ("av", "silence_beeper"): "Unconditionally silences all audio beeper tones on the reader regardless of host panel or scan events.",
    ("av", "host_control_enabled"): "Allows the access control host panel to remotely command and override the reader's LED colors and patterns.",
    ("av", "host_control_exclusive"): "Grants the host panel exclusive control over LEDs, disabling local reader visual scan feedback.",
    ("av", "idle_color"): "The default LED color displayed when the reader is in an idle, standby state waiting for credentials.",
    ("av", "red_intensity"): "Sets the hardware brightness level for the red LED element on a scale from 0 to 6.",
    ("av", "green_intensity"): "Sets the hardware brightness level for the green LED element on a scale from 0 to 5.",
    ("av", "blue_intensity"): "Sets the hardware brightness level for the blue LED element on a scale from 0 to 5.",

    ("ble", "enabled"): "Enables or disables the Bluetooth Low Energy (BLE) radio interface.",
    ("ble", "connection_timeout_ms"): "The inactivity duration in milliseconds before an active BLE mobile connection is automatically terminated.",
    ("ble", "allow_fw_updates"): "Permits over-the-air (OTA) reader firmware updates through the BLE interface.",

    ("ble/adv_params", "interval_min"): "The minimum BLE advertising interval in units of 0.625ms (e.g. 48 = 30ms).",
    ("ble/adv_params", "interval_max"): "The maximum BLE advertising interval in units of 0.625ms (e.g. 96 = 60ms).",
    ("ble/adv_params", "options"): "Bit-field configuring BLE advertising features such as connectability, identity address, and scan response notifications.",

    ("ble/adv_data", "flags"): "BLE advertising data flags field specifying discoverability and BLE capability modes.",
    ("ble/adv_data", "tx_power"): "BLE radio transmit output power in dBm for standard single-gang or mullion wall readers.",
    ("ble/adv_data", "tx_power_module"): "BLE radio transmit output power in dBm specifically for embedded reader modules.",
    ("ble/adv_data", "uuid128_all"): "The 128-bit Service UUID advertised by the reader for mobile application discovery.",
    ("ble/adv_data", "name_complete"): "The complete Bluetooth local name broadcasted by the reader (up to 5 characters).",

    ("ble/av", "override_osdp_leds"): "Allows an active BLE mobile session to temporarily override OSDP-controlled LED indicators.",
    ("ble/av", "color"): "The LED indicator color displayed while a mobile device is actively connected over BLE.",
    ("ble/av", "is_blinking"): "Determines whether the BLE connection LED indicator remains solid or blinks.",
    ("ble/av", "blinking_period"): "The timing interval in milliseconds for the BLE connection LED blinking cycle.",

    ("ble/configure", "secure_transactions"): "Requires AES-encrypted secure transactions when communicating with the WaveLynx Configure mobile app.",
    ("ble/configure", "allow_credentials"): "Allows test credential transmissions initiated directly from the Configure mobile application.",
    ("ble/configure", "bcd_credentials"): "Converts credential payloads sent via the Configure mobile application into Binary Coded Decimal (BCD) format.",
    ("ble/configure", "admin_timeout_s"): "The time window in seconds after reader startup during which administrative configuration access is permitted.",

    ("card_tracker", "enabled"): "Enables the card tracking engine to prevent duplicate credential transmissions during continuous card holds.",
    ("card_tracker", "max_cards"): "The maximum number of distinct card UIDs tracked simultaneously in memory for anti-passback (up to 15).",
    ("card_tracker", "timeout_ms"): "The anti-passback hold duration in milliseconds before a card can trigger another read event.",

    ("host_communication", "mode"): "The primary protocol interface used to send data to the controller panel ('auto_detect', 'osdp', or 'wiegand').",

    ("keypad", "idle_led_on"): "Keeps the keypad backlighting illuminated while the reader is in an idle standby state.",
    ("keypad", "pressed_led_on"): "Briefly pulses the keypad backlight whenever a individual key is pressed.",
    ("keypad_mullion", "idle_led_on"): "Keeps the mullion keypad backlighting illuminated while in standby.",
    ("keypad_mullion", "pressed_led_on"): "Briefly pulses the mullion keypad backlight on keypresses.",

    ("mfg_data", "cfg_id"): "Manufacturer's unique configuration identifier mask assigned to this hardware profile.",

    ("mypass", "allow_credentials"): "Allows mobile credentials to be transmitted using the MyPass application protocol.",
    ("mypass", "bcd_credentials"): "Converts credential data sent via MyPass into Binary Coded Decimal (BCD) format.",
    ("mypass", "km1_se_slot_nb"): "Secure Element (SE) key slot index holding Master Key 1 for MyPass authentication.",
    ("mypass", "km2_se_slot_nb"): "Secure Element (SE) key slot index holding Master Key 2 for MyPass authentication.",
    ("mypass", "kc1_se_slot_nb"): "Secure Element (SE) key slot index holding Credential Key 1 for MyPass decryption.",
    ("mypass", "kc2_se_slot_nb"): "Secure Element (SE) key slot index holding Credential Key 2 for MyPass decryption.",
    ("mypass", "all_keys"): "Enables simultaneous operation of both keyset 1 and keyset 2 for MyPass.",
    ("mypass", "metadata"): "4-byte metadata hexstring transmitted during MyPass authentication exchanges.",
    ("mypass", "allow_key_rolling"): "Allows dynamic rolling key updates after 1 minute of reader operational uptime.",

    ("osdp", "response_time_limit_ms"): "The maximum response window in milliseconds before an OSDP message is flagged as late.",
    ("osdp", "vendor_code"): "The IEEE-assigned 3-byte Vendor OID hexstring identifying WaveLynx (5C2623).",
    ("osdp", "comset_kills_sc"): "Configures whether receiving a COMSET command automatically terminates an active OSDP Secure Channel.",
    ("osdp", "allow_stacked_osdp_av_cmds"): "Permits multiple OSDP Audio/Visual commands to be queued and executed back-to-back.",
    ("osdp", "reverse_red_green_led"): "Swaps the logical red and green LED control signals for non-standard host panels.",
    ("osdp", "prevent_sc_with_ba"): "Disables OSDP Secure Channel establishment when communicating over the broadcast address (0x7F).",
    ("osdp", "prevent_sc_no_scs_15"): "Mandates SCS-15 payload encryption for OSDP Secure Channel formation.",
    ("osdp", "sc_exclusive"): "Enforces exclusive Secure Channel mode, rejecting plain OSDP communications.",
    ("osdp", "scs_timeout_follows_spec"): "Enforces standard OSDP spec timeout (8s when true; non-standard 16s when false).",
    ("osdp", "clear_scbk"): "Wipes the stored OSDP Secure Channel Base Key (SCBK) on startup and returns the reader to Install Mode.",
    ("osdp", "allow_all_commands_on_broadcast"): "Allows processing of all incoming OSDP commands directed to broadcast address 0x7F.",
    ("osdp", "ext_info_ethos_format"): "Formats extended reader information (0x9C) responses to match legacy Ethos structure.",
    ("osdp", "skip_poll_cmd_length_check"): "Bypasses standard OSDP poll message length validation for compatibility with legacy panels.",
    ("osdp", "allow_scb_checksum"): "Accepts OSDP Secure Channel Control Blocks using 1-byte checksums instead of standard 2-byte CRCs.",

    ("osdp/comms", "baud_rate"): "The RS-485 serial communication speed in bits per second (e.g. 9600, 115200).",
    ("osdp/comms", "addr"): "The OSDP Peripheral Device (PD) network polling address (0 to 126).",

    ("rfid", "poll_period_ms"): "The polling cycle frequency in milliseconds for checking active RFID card technologies.",
    ("rfid/av", "beep_enabled"): "Triggers an audible beep tone when a physical card or mobile credential is read.",
    ("rfid/av", "led_enabled"): "Triggers a visual LED flash when a physical card or mobile credential is read.",
    ("rfid/av", "duration_ms"): "The duration in milliseconds for the card read LED flash and audio beep.",
    ("rfid/av", "color"): "The LED color flashed upon a successful card read event.",

    ("rfid/lf", "enabled"): "Enables the 125kHz Low Frequency (LF) proximity card reading subsystem.",
    ("rfid/lf", "filter_enabled"): "Activates bit-level filtering masks on incoming 125kHz prox card bitstreams.",
    ("rfid/lf", "filter_function"): "Comparison operator ('equal', 'less_or_equal', 'greater_or_equal', 'not_equal') for prox bitstream filtering.",
    ("rfid/lf", "filter_bit_len"): "The target bit length evaluated by the prox filtering rules.",
    ("rfid/lf", "filter_mask"): "Hexadecimal bitmask used to isolate specific bit fields within 125kHz prox credentials.",
    ("rfid/lf", "filter_value"): "Expected hexadecimal value matched against masked prox credential bitstreams.",
    ("rfid/lf", "ask_output_format"): "Formatting protocol ('casi_01' or 'casi_02') used for 125kHz ASK prox card outputs.",

    ("rfid/hf/nfc", "enabled"): "Enables 13.56MHz High Frequency (HF) and NFC contactless card polling.",
    ("rfid/hf/nfc", "enabled_protocols"): "Active 13.56MHz RFID ISO protocols ('A' = ISO14443-A, 'B' = ISO14443-B, 'F' = FeliCa, 'V' = ISO15693).",
    ("rfid/hf/nfc", "auto_tune"): "Automatically tunes the HF antenna matching circuit on reader startup for optimal read range.",
    ("rfid/hf/nfc", "baudrate"): "NFC communication transfer speed in Kbps (106, 212, 424, or 848 Kbps).",
    ("rfid/hf/nfc", "append_csn"): "Appends the card's raw Card Serial Number (CSN) to the end of decrypted credential payloads.",

    ("tamper", "wiegand_reporting_enabled"): "Reports optical and physical tamper alarm events over the Wiegand interface.",
    ("tamper", "osdp_reporting_enabled"): "Reports optical and physical tamper alarm events via OSDP status messages.",
    ("tamper", "beeper_during_osdp_enabled"): "Forces continuous local beeper sounding immediately upon OSDP tamper detection.",
    ("tamper", "report_state_change_only_enabled"): "Sends tamper alarm updates strictly on state transitions rather than continuously polling.",
    ("tamper", "sample_frequency_ms"): "The sampling rate in milliseconds for reading physical optical and accelerometer tamper sensors.",
    ("tamper/accel", "sensitivity"): "Motion sensitivity threshold ('high', 'default', 'low') for triggering accelerometer tamper alarms.",
    ("tamper/accel", "x_axis_enabled"): "Monitors X-axis physical reader movement for accelerometer tamper triggers.",
    ("tamper/accel", "y_axis_enabled"): "Monitors Y-axis physical reader movement for accelerometer tamper triggers.",
    ("tamper/accel", "z_axis_enabled"): "Monitors Z-axis physical reader movement for accelerometer tamper triggers.",

    ("wiegand", "space_duration_us"): "The pulse off-time duration in microseconds for Wiegand bit transmission.",
    ("wiegand", "pulse_duration_us"): "The active low pulse width duration in microseconds for Wiegand Data 0/Data 1 lines.",
    ("wiegand", "lines_inverted"): "Inverts the physical voltage logic levels of Wiegand Data 0 (D0) and Data 1 (D1) output lines.",
    ("wiegand", "default_format"): "Default Wiegand output encoding format ('4_bit', '8_bit', '26_bit', 'magstripe_4', 'magstripe_5').",
    ("wiegand", "default_facility_code"): "Default facility code embedded into 26-bit Wiegand credential transmissions (0-255)."
}

SECTION_TITLES = {
    "av": "Audio / Visual (A/V) Feedback Settings",
    "ble": "Bluetooth Low Energy (BLE) Configuration",
    "ble/adv_params": "BLE Advertising Parameters",
    "ble/adv_data": "BLE Advertising Data Payload",
    "ble/av": "BLE Connection A/V Override Settings",
    "ble/configure": "BLE Mobile Configure App Settings",
    "card_tracker": "Card Tracker & Anti-Passback Settings",
    "host_communication": "Host Reader Communication Protocol",
    "keypad": "Standard Keypad LED & Key Mappings",
    "keypad_mullion": "Mullion Keypad LED & Key Mappings",
    "mfg_data": "Manufacturing Data",
    "mypass": "MyPass Application Settings",
    "osdp": "OSDP Protocol & Compliance Parameters",
    "osdp/comms": "OSDP Serial Communications",
    "rfid": "RFID Global Polling Settings",
    "rfid/av": "RFID Card Scan Feedback (A/V)",
    "rfid/lf": "125kHz Low Frequency (LF) Prox Settings",
    "rfid/hf/nfc": "13.56MHz High Frequency (HF) & NFC Settings",
    "rfid/hf/app/config": "Configuration Card App Reader Settings",
    "rfid/hf/app/csn": "CSN Card Reading & Bit Format Settings",
    "tamper": "Optical & System Tamper Monitoring",
    "tamper/accel": "Accelerometer Tamper Detection",
    "wiegand": "Wiegand Interface & Control Signal Settings",
    "keys": "Secure Element (SE) Key Slots",
}

VALUE_TRANSLATIONS = {
    "0": "Disabled / Off",
    "1": "Enabled / On",
    "true": "Enabled / On",
    "false": "Disabled / Off",
    "off": "Disabled / Off",
    "on": "Enabled / On",
}

def translate_uploaded_ini(uploaded_ini_text):
    """
    Parses an uploaded INI file and maps every directive to a clear, comprehensive description.
    """
    parser = configparser.ConfigParser(allow_no_value=True)
    if not any(line.strip().startswith('[') for line in uploaded_ini_text.splitlines() if line.strip()):
        uploaded_ini_text = "[GENERAL]\n" + uploaded_ini_text

    try:
        parser.read_string(uploaded_ini_text)
    except Exception as e:
        return {"error": f"Failed to parse uploaded INI file: {str(e)}", "sections": []}

    translated_sections = []

    for section_name in parser.sections():
        clean_section = section_name.lower().strip()
        section_title = SECTION_TITLES.get(clean_section, f"Section: [{section_name}]")

        section_data = {
            "section": section_name,
            "section_title": section_title,
            "entries": []
        }

        for key, raw_val in parser.items(section_name):
            clean_key = key.lower().strip()
            clean_val = raw_val.strip().strip('"') if raw_val else ""

            comment_suffix = ""
            if ";" in clean_val:
                parts = clean_val.split(";", 1)
                clean_val = parts[0].strip()
                comment_suffix = f" ({parts[1].strip()})"

            description = SETTING_DESCRIPTIONS.get(
                (clean_section, clean_key),
                key.replace('_', ' ').title()
            )

            val_translated = VALUE_TRANSLATIONS.get(clean_val.lower(), clean_val if clean_val else "(Not Specified)")
            if comment_suffix:
                val_translated += comment_suffix

            if "keymap_" in clean_key:
                key_name = clean_key.replace("keymap_kp", "").replace("asterisk", "*").replace("pound", "#")
                description = f"Keypad key '{key_name}' output character mapping."

            section_data["entries"].append({
                "raw_key": key,
                "description": description,
                "translated_value": val_translated
            })

        if section_data["entries"]:
            translated_sections.append(section_data)

    return {"error": None, "sections": translated_sections}