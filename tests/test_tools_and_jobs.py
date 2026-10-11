"""Tool routes that run as background jobs, plus flash logging and the YAML automator."""
import os

import pytest

from conftest import JSON, flashed
from models import db, OemSku, RevisionHistory
import routes.tools as tools_routes


def fake_binary(content=b"BIN"):
    def build(config, fw, email, workdir, **kw):
        path = os.path.join(workdir, "x.bin")
        open(path, "wb").write(content)
        return path
    return build


@pytest.fixture
def config(make_config):
    return make_config("CQU1")


def test_profile_download_runs_as_a_job(client, login, config, wait_for_job, monkeypatch):
    monkeypatch.setattr(tools_routes, "get_or_build_profile_bin", fake_binary(b"PROFILE"))
    login("SET")
    r = client.post("/configurations/CQU1/download-profile-bin", data={"fw_version": "v5.4.11"}, headers=JSON)
    assert r.status_code == 202
    job = wait_for_job(r.json["job_id"])
    file = client.get(f"/jobs/{job['id']}/files/bin")
    assert job["status"] == "done" and file.data == b"PROFILE"
    assert "CQU1_v5.4.11.bin" in file.headers["Content-Disposition"]


def test_a_failed_build_reports_why(client, login, config, wait_for_job, monkeypatch):
    monkeypatch.setattr(tools_routes, "get_or_build_firmware_bin", lambda *a, **k: None)
    login("SET")
    job = wait_for_job(client.post("/configurations/CQU1/download-firmware-bin", headers=JSON).json["job_id"])
    assert job["status"] == "failed" and "could not be found or built" in job["error"]


def test_users_only_see_their_own_jobs(client, login, config, wait_for_job, monkeypatch):
    monkeypatch.setattr(tools_routes, "get_or_build_profile_bin", fake_binary())
    login("SET")
    job_id = client.post("/configurations/CQU1/download-profile-bin", headers=JSON).json["job_id"]
    wait_for_job(job_id)
    login("QA")
    assert client.get(f"/jobs/{job_id}", headers=JSON).status_code == 404


def test_archived_configs_cant_build(client, login, make_config):
    make_config("COLD", status="Archived")
    login("SET")
    r = client.post("/configurations/COLD/download-firmware-bin", headers=JSON)
    assert r.status_code == 403 and "Archived" in r.json["error"]


def test_partial_config_returns_ini_and_bin(client, login, wait_for_job, monkeypatch):
    monkeypatch.setattr(tools_routes, "upload_partial_ini_to_gcs", lambda *a: "gs://x")
    monkeypatch.setattr(tools_routes, "compile_partial_bin_via_forge",
                        lambda name, fw, email, workdir: fake_binary()(None, None, None, workdir))
    login("SET")
    r = client.post("/tools/partial-config", data={"fw_version": "v5.4.10", "build_bin_choice": "true", "ble": "enable"},
                    headers=JSON)
    job = wait_for_job(r.json["job_id"])
    assert job["status"] == "done" and job["files"] == ["bin", "ini"]


def test_partial_config_needs_an_override(client, login):
    login("SET")
    assert client.post("/tools/partial-config", data={"fw_version": "v5.4.10"}, headers=JSON).status_code == 400


def test_token_lookup_is_read_only(client, login, config, monkeypatch):
    monkeypatch.setattr(tools_routes, "lookup_tokens", lambda cfg, fw: {
        "profile": {"name": "p", "token": "c-1", "error": None}, "firmware": {"name": "f", "token": None, "error": "No record"}})
    login("SET")
    r = client.get("/configurations/CQU1/get-tokens?fw_version=v5.4.10", headers=JSON)
    assert r.status_code == 200 and r.json["profile"]["token"] == "c-1"


def test_batch_lookup_job(client, login, make_config, wait_for_job, monkeypatch):
    make_config("CQU1")
    make_config("CQU2")
    monkeypatch.setattr(tools_routes, "lookup_tokens", lambda cfg, fw: {
        "profile": {"name": "p", "token": "c-1", "error": None}, "firmware": {"name": "f", "token": "c-2", "error": None}})
    login("SET")
    r = client.post("/tools/batch-tokens/lookup", data={"fw_version": "v5.4.10", "config_names": ["CQU1", "CQU2"]}, headers=JSON)
    job = wait_for_job(r.json["job_id"])
    assert job["status"] == "done" and len(job["result"]["items"]) == 2


def test_flash_log_records_browser_flashes(app, client, login, config):
    login("QA")
    r = client.post("/configurations/CQU1/flash-log", headers=JSON, data={
        "fw_version": "v5.4.10", "success": "true", "message": "Firmware transferred.", "edited_ini": "true"})
    assert r.status_code == 200
    with app.app_context():
        assert RevisionHistory.query.filter(RevisionHistory.revision_details.like(
            "OSDP Flash with edited INI (v5.4.10, browser). Result: Success%")).count() == 1


def test_flash_log_and_flash_buttons_are_for_set_and_qa(client, login, config):
    login("Sales")
    assert client.post("/configurations/CQU1/flash-log", data={"success": "true"}, headers=JSON).status_code == 403
    assert 'data-flash-prepare-url="' not in client.get("/configurations/CQU1").text
    login("QA")
    assert 'data-flash-prepare-url="' in client.get("/configurations/CQU1").text


def test_flasher_script_is_served(client):
    r = client.get("/static/js/osdp-flasher.js")
    assert r.status_code == 200 and "flashFirmware" in r.text


def test_yaml_automator_persists_new_oems(app, client, login):
    with app.app_context():
        db.session.add(OemSku(name="PDK", sku_id=62))
        db.session.commit()
    login("SET")
    r = client.post("/tools/yaml-automator", data={"oem": "new oem", "tci_choice": "0209FE", "leaf_cc_choice": "Lk50003"})
    assert r.status_code == 200 and b"SKU_NEW_OEM: 63" in r.data and b"tci_1: 0x02" in r.data
    with app.app_context():
        assert OemSku.query.filter_by(name="NEW_OEM").one().sku_id == 63


def test_yaml_automator_rejects_bad_tci(client, login):
    login("SET")
    assert flashed(client.post("/tools/yaml-automator", data={"oem": "PDK", "tci_choice": "zz"}, follow_redirects=True),
                   "TCI must be")
