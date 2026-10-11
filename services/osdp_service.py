import os
import time
import logging
import threading
import serial
import serial.tools.list_ports
from services.gcs_service import get_or_build_firmware_bin
from utils.errors import UserFacingError
from osdp_library.osdp.data_trafficking.osdp_traffic_conductor import OsdpTrafficConductor

log = logging.getLogger(__name__)

HIGH_SPEED_BAUD = 115200
STANDARD_BAUDS = [9600, 19200, 38400, 57600, 115200]

# Only one reader can be flashed at a time over the shared RS-485 adapter.
_flash_lock = threading.Lock()


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
    """Scans baud rates and addresses to locate reader. Returns (baud, address), or None when no reader answers."""
    # 115200 first: it's the most common setting and saves scanning the slower rates.
    for baud in [HIGH_SPEED_BAUD] + [b for b in STANDARD_BAUDS if b != HIGH_SPEED_BAUD]:
        try:
            ser = serial.Serial(port_path, baudrate=baud, timeout=0.3)
        except serial.SerialException as e:
            log.error("Couldn't open %s at %s baud: %s", port_path, baud, e)
            return None

        with ser:
            for addr in range(8):
                # An address that doesn't answer raises TimeoutError. Catch it per address
                # so one silent address doesn't skip the rest of the scan at this baud rate.
                try:
                    ser.reset_input_buffer()
                    conductor = OsdpTrafficConductor(ser, device_address=addr, use_crc=True)
                    if conductor.conduct_polling_transaction() is not None:
                        log.info("Reader discovered on %s @ %s baud (Addr: %s)", port_path, baud, addr)
                        return baud, addr
                except Exception as e:
                    log.debug("No reply at %s baud, address %s: %s", baud, addr, e)

    return None


def flash_firmware_osdp(config, fw_version, user_email, output_dir, progress_callback=None, status_callback=None,
                        force_rebuild=False):
    """
    1. Finds RS-485 port.
    2. Downloads/compiles firmware BIN (force_rebuild builds from the user's current Forge INI).
    3. Elevates baud rate to 115200 via COMSET.
    4. Flashes reader and tracks percentage.
    5. Safely attempts COMSET restoration.
    Returns (success, message).
    """
    def status(text):
        log.info(text)
        if status_callback:
            status_callback(text)

    if not _flash_lock.acquire(blocking=False):
        return False, "Another reader is being flashed right now. Wait for it to finish and try again."

    try:
        port_path = find_rs485_port()
        if not port_path:
            return False, "No RS-485 port found. Please connect your RS-485 serial adapter to proceed."

        status("Building firmware with Forge..." if force_rebuild else "Retrieving firmware binary...")
        firmware_bin_path = get_or_build_firmware_bin(config, fw_version, user_email, output_dir, force_rebuild=force_rebuild)
        if not firmware_bin_path or not os.path.exists(firmware_bin_path):
            return False, f"Firmware binary file for {config.config_name} could not be retrieved or generated."

        status(f"Searching for a reader on {port_path}...")
        comms = discover_reader_comms(port_path)
        if comms is None:
            return False, (f"No reader answered on {port_path} at any standard baud rate (addresses 0-7). "
                           "Check wiring and power, then try again.")
        original_baud, address = comms

        with serial.Serial(port_path, baudrate=original_baud, timeout=2.0) as ser:
            conductor = OsdpTrafficConductor(ser, device_address=address, use_crc=True)

            if original_baud != HIGH_SPEED_BAUD:
                status(f"Elevating baud rate from {original_baud} to {HIGH_SPEED_BAUD}...")
                try:
                    conductor.conduct_comset_transaction(address=address, baud_rate=HIGH_SPEED_BAUD)
                    ser.baudrate = HIGH_SPEED_BAUD
                    time.sleep(0.3)
                except Exception as e:
                    log.warning("COMSET elevation failed (%s). Proceeding at %s...", e, original_baud)

            def internal_progress_cb(progress):
                if progress_callback:
                    progress_callback(round(progress.percent_complete, 1))

            status("Transferring firmware...")
            conductor.conduct_file_transfer(
                file_path=firmware_bin_path,
                file_fragment_size=1024,
                on_progress_callback=internal_progress_cb
            )

            if original_baud != HIGH_SPEED_BAUD:
                log.info("Attempting to restore original baud rate (%s)...", original_baud)
                try:
                    conductor.conduct_comset_transaction(address=address, baud_rate=original_baud)
                except Exception as e:
                    log.info("Reader already rebooting (COMSET restoration skipped: %s)", e)

        msg = f"Flashing complete for {config.config_name}. Reader is now rebooting. Please wait until reboot is complete."
        log.info(msg)
        return True, msg

    except UserFacingError as e:
        return False, str(e)
    except Exception as e:
        log.exception("Exception during OSDP flash: %s", e)
        return False, f"Communication error: {str(e)}"
    finally:
        _flash_lock.release()
