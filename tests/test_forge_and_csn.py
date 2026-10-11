"""Forge IAP cookie handling and the partial config tool's CSN output."""
import pytest

import services.forge_service as forge
import services.gcs_service as gcs
from conftest import JSON


class FakeResponse:
    def __init__(self, status, location=""):
        self.status_code = status
        self.headers = {"Location": location}
        self.text = ""


def rejected(response):
    try:
        forge._check_iap_auth(response)
        return False
    except forge.ForgeAuthError:
        return True


@pytest.mark.parametrize("response, expired", [
    (FakeResponse(302, "https://accounts.google.com/o/oauth2/v2/auth?client_id=x"), True),
    (FakeResponse(403), True),
    (FakeResponse(302, "/configs/builds/123"), False),
    (FakeResponse(200), False),
])
def test_iap_cookie_detection(response, expired):
    assert rejected(response) is expired


def test_missing_cookie_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(forge, "ACTIVE_IAP_COOKIE", "")
    with pytest.raises(forge.ForgeAuthError, match="ACTIVE_IAP_COOKIE is not set"):
        forge.trigger_forge_build("CQU1", "v5.4.10")


def test_expired_cookie_fails_the_build_job_fast(client, login, make_config, wait_for_job, monkeypatch):
    class EmptyBucket:
        def list_blobs(self, **kw):
            return []

    class FakeClient:
        def bucket(self, name):
            return EmptyBucket()

    monkeypatch.setattr(forge, "ACTIVE_IAP_COOKIE", "stale-cookie")
    monkeypatch.setattr(forge.requests, "post", lambda *a, **k: FakeResponse(302, "https://accounts.google.com/ServiceLogin"))
    monkeypatch.setattr(gcs, "get_gcs_client", lambda: FakeClient())
    monkeypatch.setattr(gcs, "get_or_create_ini_file", lambda *a, **k: "x.ini")

    make_config("CQU1")
    login("SET")
    job = wait_for_job(client.post("/configurations/CQU1/download-firmware-bin", headers=JSON).json["job_id"])
    assert job["status"] == "failed" and "IAP cookie" in job["error"] and "Unexpected" not in job["error"]


def test_partial_csn_codes_and_keys():
    text = forge.generate_partial_ini_content({"csn_mfc": "on_56", "csn_ev1ev2": "on_56_lsb", "csn_iclass": "on_32"})
    assert "mifare_classic_format = 0x3801" in text
    assert "mifare_desfire_format = 0x3800" in text and "mifare_ultralight_format = 0x3800" in text
    assert "pico15693_format = 0x2001" in text


@pytest.mark.parametrize("path, allowed", [
    ("forge/jhepburn@wavelynx.com/v5.4.10/CQU1.ini", True),
    ("forge/jhepburn@wavelynx.com/v5.4.10/partials/partial_ble.ini", True),
    ("forge/qa@x.com/v5.4.10/CQU1.ini", False),            # the signed-in user's own folder isn't used
    ("private/ini-generator/settings_default.ini", False),
    ("caliga-jobs/abc/x.bin", False),
    ("input/v5.4.10/CQU1.ini", False),
    ("output/v5.4.10/CQU1.bin", False),
    ("forge/jhepburn@wavelynx.com/../other@x.com/x.ini", False),
    ("forge/jhepburn@wavelynx.com/", False),
])
def test_gcs_writes_stay_inside_the_forge_folder(path, allowed):
    from services.gcs_client import check_forge_path, GcsWriteRefused
    if allowed:
        assert check_forge_path(path) == path
    else:
        with pytest.raises(GcsWriteRefused):
            check_forge_path(path)


def test_uploads_go_to_james_folder_whoever_is_signed_in(monkeypatch):
    written = []

    class FakeBlob:
        def __init__(self, name):
            self.name = name

        def upload_from_string(self, *a, **k):
            written.append(self.name)

    class FakeBucket:
        def blob(self, name):
            return FakeBlob(name)

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    monkeypatch.setattr(gcs, "get_gcs_client", lambda: FakeClient())
    gcs.upload_partial_ini_to_gcs("partial_ble.ini", "[ble]\nenabled = true", "v5.4.10", "qa@x.com")
    gcs.save_edited_failure_ini("CQU1", "[av]", "v5.4.10", "sales@x.com")
    assert written == ["forge/jhepburn@wavelynx.com/v5.4.10/partials/partial_ble.ini",
                       "forge/jhepburn@wavelynx.com/v5.4.10/CQU1.ini"]


def test_every_gcs_upload_goes_through_the_guard():
    """No module may call the GCS upload methods directly, except the guard itself."""
    import pathlib
    from conftest import ROOT
    offenders = []
    for path in pathlib.Path(ROOT).glob("**/*.py"):
        if any(part in (".venv", "venv", "tests", "migrations") for part in path.parts):
            continue
        text = path.read_text()
        if ("upload_from_" in text or ".compose(" in text or ".rewrite(" in text) and path.name != "gcs_client.py":
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
