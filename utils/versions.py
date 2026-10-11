SUPPORTED_FW_VERSIONS = ("v5.4.10", "v5.4.11")
DEFAULT_FW_VERSION = "v5.4.10"


def normalize_fw_version(fw_version):
    """Returns the version with and without the leading 'v', e.g. ('v5.4.10', '5.4.10')."""
    no_v = (fw_version or "").strip().lstrip("v")
    return f"v{no_v}", no_v


def parse_fw_version(value):
    """Returns a supported 'vX.Y.Z' version string, or the default when the value is unknown."""
    with_v, _ = normalize_fw_version(value)
    return with_v if with_v in SUPPORTED_FW_VERSIONS else DEFAULT_FW_VERSION
