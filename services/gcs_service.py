import os
import time
import logging
from datetime import datetime, timezone
from services.gcs_client import get_gcs_client, write_to_forge, forge_root, GCS_BUCKET_NAME
from services.ini_builder import generate_ini_from_config
from services.forge_service import trigger_forge_build
from utils.versions import normalize_fw_version

log = logging.getLogger(__name__)

BUILD_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 3


def find_latest_matching_blob(bucket, prefix, filename_targets, min_updated=None):
    """
    Searches GCS under `prefix` (including subdirectories like output/v5.4.10/YYYYMMDD_HHMMSS_.../)
    for target filenames and returns the newest blob based on creation time.
    When min_updated is set, blobs last updated before it are ignored.
    """
    matching_blobs = []

    for target in filename_targets:
        # match_glob narrows the listing server-side. The exact-name check drops
        # files that only share a suffix (e.g. 'XCQU1.ini' when looking for 'CQU1.ini').
        for blob in bucket.list_blobs(prefix=prefix, match_glob=f"{prefix}**{target}"):
            if not (blob.name.endswith(f"/{target}") or blob.name == target):
                continue
            if min_updated and blob.updated < min_updated:
                continue
            matching_blobs.append(blob)

    if not matching_blobs:
        return None

    matching_blobs.sort(key=lambda b: b.time_created, reverse=True)
    return matching_blobs[0]


def _poll_for_blob(bucket, prefixes, targets, timeout_seconds, min_updated=None):
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        for prefix in prefixes:
            blob = find_latest_matching_blob(bucket, prefix, targets, min_updated=min_updated)
            if blob:
                return blob
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def get_or_create_ini_file(config, fw_version, user_email, output_dir):
    file_name = f"{config.config_name}.ini"
    local_path = os.path.join(output_dir, file_name)
    os.makedirs(output_dir, exist_ok=True)

    ver_with_v, ver_no_v = normalize_fw_version(fw_version)

    candidate_prefixes = [
        f"input/{ver_with_v}/",
        f"input/{ver_no_v}/",
        f"{forge_root()}{ver_with_v}/",
        f"{forge_root()}{ver_no_v}/",
    ]

    client = get_gcs_client()

    if client:
        try:
            bucket = client.bucket(GCS_BUCKET_NAME)
            for prefix in candidate_prefixes:
                blob = find_latest_matching_blob(bucket, prefix, [file_name])
                if blob:
                    blob.download_to_filename(local_path)
                    log.info("Found and downloaded INI -> gs://%s/%s", GCS_BUCKET_NAME, blob.name)
                    return local_path
        except Exception as e:
            log.exception("Exception checking bucket: %s", e)

    log.info("'%s' not found in GCS. Generating from DB model...", file_name)
    generate_ini_from_config(config, local_path, ver_with_v)

    if client:
        forge_upload_path = f"{forge_root()}{ver_with_v}/{file_name}"
        try:
            bucket = client.bucket(GCS_BUCKET_NAME)
            write_to_forge(bucket, forge_upload_path, local_path=local_path)
            log.info("Uploaded generated INI to Forge bucket -> gs://%s/%s", GCS_BUCKET_NAME, forge_upload_path)
        except Exception as e:
            log.exception("Could not upload to Forge bucket: %s", e)

    return local_path


def _get_or_build_binary(config, fw_version, user_email, output_dir, targets, local_name, label,
                         timeout_seconds, force_rebuild):
    """
    Shared flow for profile and firmware binaries.
    Normal mode: return an existing binary from output/ or forge/, else build one.
    force_rebuild: skip existing binaries, build from the INI already in forge/<FORGE_FOLDER_EMAIL>/,
    and only accept a binary written after the build was triggered.
    """
    ver_with_v, ver_no_v = normalize_fw_version(fw_version)
    local_path = os.path.join(output_dir, local_name)
    os.makedirs(output_dir, exist_ok=True)

    versions = [ver_with_v, ver_no_v]
    output_prefixes = [f"output/{v}/" for v in versions]
    forge_prefixes = [f"{forge_root()}{v}/" for v in versions]

    client = get_gcs_client()
    if not client:
        log.error("GCS client unavailable. Can't fetch %s for %s.", label, config.config_name)
        return None

    bucket = client.bucket(GCS_BUCKET_NAME)

    if not force_rebuild:
        for prefix in output_prefixes + forge_prefixes:
            blob = find_latest_matching_blob(bucket, prefix, targets)
            if blob:
                blob.download_to_filename(local_path)
                log.info("Found %s -> gs://%s/%s", label, GCS_BUCKET_NAME, blob.name)
                return local_path

        get_or_create_ini_file(config, ver_with_v, user_email, output_dir)

    build_started = datetime.now(timezone.utc)
    if not trigger_forge_build(config.config_name, ver_with_v, source="user"):
        return None

    log.info("Polling GCS for %s %s (up to %ss)...", config.config_name, label, timeout_seconds)
    blob = _poll_for_blob(
        bucket, forge_prefixes, targets, timeout_seconds,
        min_updated=build_started if force_rebuild else None
    )
    if blob:
        blob.download_to_filename(local_path)
        log.info("%s landed in Forge bucket -> gs://%s/%s", label, GCS_BUCKET_NAME, blob.name)
        return local_path

    log.error("Timed out waiting for %s %s from Forge.", config.config_name, label)
    return None


def get_or_build_profile_bin(config, fw_version, user_email, output_dir, timeout_seconds=BUILD_TIMEOUT_SECONDS,
                             force_rebuild=False):
    """
    Downloads profile .bin file if it exists in GCS output/ (including timestamp folders) or forge/.
    If missing, ensures .ini is in Forge bucket, triggers Forge build,
    and polls GCS until the .bin file lands.
    """
    ver_with_v, _ = normalize_fw_version(fw_version)
    name = config.config_name
    targets = [f"{name}.bin", f"{name}_profile.bin", f"{name}.BIN", f"{name}_profile.BIN"]
    return _get_or_build_binary(
        config, fw_version, user_email, output_dir, targets,
        local_name=f"{name}_{ver_with_v}.bin", label="Profile BIN",
        timeout_seconds=timeout_seconds, force_rebuild=force_rebuild
    )


def get_or_build_firmware_bin(config, fw_version, user_email, output_dir, timeout_seconds=BUILD_TIMEOUT_SECONDS,
                              force_rebuild=False):
    """
    Downloads firmware DCK binary if it exists in GCS output/ or forge/.
    If missing, ensures .ini is in Forge bucket, triggers Forge build,
    and polls GCS until the file lands.
    """
    ver_with_v, ver_no_v = normalize_fw_version(fw_version)
    name = config.config_name
    targets = [
        f"{name}_wall_DCK.bin",
        f"{name}_DCK.bin",
        f"{name}_wall_dck.bin",
        f"{name}_dck.bin",
        f"{name}_{ver_with_v}_DCK.bin",
        f"{name}_{ver_no_v}_DCK.bin",
    ]
    return _get_or_build_binary(
        config, fw_version, user_email, output_dir, targets,
        local_name=f"{name}_{ver_with_v}_firmware.dck.bin", label="Firmware BIN",
        timeout_seconds=timeout_seconds, force_rebuild=force_rebuild
    )


def upload_partial_ini_to_gcs(ini_filename, ini_content, fw_version, user_email):
    """
    Uploads a partial .ini file directly to:
    forge/<FORGE_FOLDER_EMAIL>/{fw_version}/partials/{ini_filename}
    """
    client = get_gcs_client()
    if not client:
        log.error("GCS client not available for partial upload.")
        return None

    ver_with_v, _ = normalize_fw_version(fw_version)

    try:
        bucket = client.bucket(GCS_BUCKET_NAME)
        gcs_path = f"{forge_root()}{ver_with_v}/partials/{ini_filename}"
        write_to_forge(bucket, gcs_path, text=ini_content)
        log.info("Uploaded partial INI to gs://%s/%s", GCS_BUCKET_NAME, gcs_path)
        return gcs_path
    except Exception as e:
        log.exception("Failed to upload partial INI: %s", e)
        return None


def get_failure_ini_content(config_name, fw_version, user_email):
    """
    Reads the INI content for a configuration.
    Checks forge/ user directory first; falls back to master input/ bucket.
    """
    ver_with_v, _ = normalize_fw_version(fw_version)
    file_name = f"{config_name}.ini"

    candidate_prefixes = [
        f"{forge_root()}{ver_with_v}/",
        f"input/{ver_with_v}/"
    ]

    client = get_gcs_client()
    if client:
        try:
            bucket = client.bucket(GCS_BUCKET_NAME)
            for prefix in candidate_prefixes:
                blob = find_latest_matching_blob(bucket, prefix, [file_name])
                if blob:
                    content = blob.download_as_text()
                    log.info("Read INI for failure edit -> gs://%s/%s", GCS_BUCKET_NAME, blob.name)
                    return content
        except Exception as e:
            log.exception("Exception reading failure INI: %s", e)

    return f"; Default generated INI for {config_name}\n[general]\nconfig_name = {config_name}\n"


def save_edited_failure_ini(config_name, ini_content, fw_version, user_email):
    """
    Saves an edited failure INI into the user's Forge bucket:
    forge/<FORGE_FOLDER_EMAIL>/{fw_version}/{config_name}.ini
    """
    client = get_gcs_client()
    if not client:
        log.error("GCS client unavailable for saving edited INI.")
        return False

    ver_with_v, _ = normalize_fw_version(fw_version)
    gcs_path = f"{forge_root()}{ver_with_v}/{config_name}.ini"

    try:
        bucket = client.bucket(GCS_BUCKET_NAME)
        write_to_forge(bucket, gcs_path, text=ini_content)
        log.info("Saved edited failure INI -> gs://%s/%s", GCS_BUCKET_NAME, gcs_path)
        return True
    except Exception as e:
        log.exception("Failed to upload edited INI: %s", e)
        return False
