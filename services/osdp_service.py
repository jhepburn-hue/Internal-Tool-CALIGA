import os
import time
import serial
import serial.tools.list_ports
from services.gcs_service import get_or_build_firmware_bin
from osdp_library.osdp.data_trafficking.osdp_traffic_conductor import OsdpTrafficConductor
from osdp_library.osdp.secure_channel import OsdpSecureChannelContext, ScbkMode

HIGH_SPEED_BAUD = 115200
STANDARD_BAUDS = [9600, 19200, 38400, 57600, 115200]

def find_rs485_port():
    """Scans system serial ports to detect connected RS-485 adapters."""
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc_lower = (p.description or "").lower()
        if any(term in desc_lower for term in ['rs485', 'rs-485', 'ftdi', 'ch340', 'cp210x', 'usb-serial', 'ttyusb', 'tty.usbserial']):
            return p.device
    
    env_port = os.getenv('OSDP_SERIAL_PORT')
    if env_port and os.path.exists(env_port):
        return env_port

    return None

def discover_reader_comms(port_path):
    """Scans baud rates and addresses to locate reader."""
    for baud in STANDARD_BAUDS:
        try:
            ser = serial.Serial(port_path, baudrate=baud, timeout=0.2)
            for addr in range(8):
                conductor = OsdpTrafficConductor(ser, device_address=addr, use_crc=True)
                reply = conductor.conduct_polling_transaction()
                if reply is not None:
                    ser.close()
                    print(f"[OSDP SERVICE] Reader discovered on {port_path} @ {baud} baud (Addr: {addr})")
                    return baud, addr
            ser.close()
        except Exception:
            continue
            
    return 9600, 0

def flash_firmware_osdp(config, fw_version, user_email, progress_callback=None):
    """
    1. Finds RS-485 port.
    2. Downloads/compiles firmware BIN.
    3. Elevates baud rate to 115200 via COMSET.
    4. Flashes reader and tracks percentage.
    5. Safely attempts COMSET restoration.
    """
    port_path = find_rs485_port()
    if not port_path:
        return False, "No RS-485 port found. Please connect your RS-485 serial adapter to proceed."

    firmware_bin_path = get_or_build_firmware_bin(config, fw_version, user_email)
    if not firmware_bin_path or not os.path.exists(firmware_bin_path):
        return False, f"Firmware binary file for {config.config_name} could not be retrieved or generated."

    original_baud, address = discover_reader_comms(port_path)

    try:
        ser = serial.Serial(port_path, baudrate=original_baud, timeout=2.0)
        conductor = OsdpTrafficConductor(ser, device_address=address, use_crc=True)

        if original_baud != HIGH_SPEED_BAUD:
            print(f"[OSDP SERVICE] Elevating baud rate from {original_baud} to {HIGH_SPEED_BAUD}...")
            try:
                conductor.conduct_comset_transaction(address=address, baud_rate=HIGH_SPEED_BAUD)
                ser.baudrate = HIGH_SPEED_BAUD
                time.sleep(0.3)
            except Exception as e:
                print(f"[OSDP SERVICE] Warning: COMSET elevation failed ({e}). Proceeding at {original_baud}...")

        def internal_progress_cb(progress):
            percent = round(progress.percent_complete, 1)
            if progress_callback:
                progress_callback(percent)

        print(f"[OSDP SERVICE] Flashing {firmware_bin_path} over OSDP...")
        ack_reply = conductor.conduct_file_transfer(
            file_path=firmware_bin_path,
            file_fragment_size=1024,
            on_progress_callback=internal_progress_cb
        )

        if original_baud != HIGH_SPEED_BAUD:
            print(f"[OSDP SERVICE] Attempting to restore original baud rate ({original_baud})...")
            try:
                conductor.conduct_comset_transaction(address=address, baud_rate=original_baud)
            except Exception as e:
                print(f"[OSDP SERVICE] Reader already rebooting (COMSET restoration skipped: {e})")

        ser.close()

        msg = f"Flashing complete for {config.config_name}. Reader is now rebooting. Please wait until reboot is complete."
        print(f"[OSDP SERVICE] SUCCESS: {msg}")
        return True, msg

    except Exception as e:
        print(f"[OSDP SERVICE] Exception during OSDP Flash: {e}")
        return False, f"Communication error: {str(e)}"