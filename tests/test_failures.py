"""Failures dashboard: claim, re-test, lazy INI, and the edited-INI firmware build."""
import os

import pytest

from conftest import JSON, flashed
from models import db, Configuration, ConfigurationTestGroup, FWRun, Job, RevisionHistory
from models import TestCase as CaseModel  # aliased so pytest doesn't treat it as a test class
import routes.failures as failures_routes


@pytest.fixture
def failed_group(app, make_config):
    make_config("CQU1")
    with app.app_context():
        run = FWRun(fw_version="v5.4.10")
        group = ConfigurationTestGroup(fw_run=run, config_name="CQU1", status="Failed", assigned_user="qa@x.com",
                                       configuration_id=Configuration.query.filter_by(config_name="CQU1").one().id)
        db.session.add_all([run, group, CaseModel(test_group=group, category="ACTION: X", criterion_name="Idle LED",
                                                 expected_value="Red", status="Failed", comment="blue")])
        db.session.commit()
        return group.id


@pytest.fixture
def no_slack_or_jira(monkeypatch):
    monkeypatch.setattr(failures_routes, "send_alert_qa_notification", lambda *a, **k: True)
    monkeypatch.setattr(failures_routes, "assign_jira_ticket", lambda **k: True)
    monkeypatch.setattr(failures_routes, "transition_jira_issue_to_complete", lambda key: True)


def test_dashboard_shows_real_jira_state(client, login, failed_group):
    login("SET")
    html = client.get("/failures").text
    assert "No Jira ticket" in html and "SWAG-" not in html


def test_ini_loads_on_demand(client, login, failed_group, monkeypatch):
    monkeypatch.setattr(failures_routes, "get_failure_ini_content", lambda name, fw, email: f"[mfg_data]\ncfg_id = {name}\n")
    login("SET")
    r = client.get(f"/failures/ini/{failed_group}", headers=JSON)
    assert r.status_code == 200 and "cfg_id = CQU1" in r.json["content"]


def test_claim_without_jira_ticket_warns(client, login, failed_group, no_slack_or_jira):
    login("SET")
    assert flashed(client.post(f"/failures/claim/{failed_group}", follow_redirects=True), "No Jira ticket is linked")


def test_retest_resets_the_suite(app, client, login, failed_group, no_slack_or_jira):
    login("SET")
    client.post(f"/failures/claim/{failed_group}")
    client.post(f"/failures/retest/{failed_group}")
    with app.app_context():
        group = db.session.get(ConfigurationTestGroup, failed_group)
        assert group.status == "Untested" and group.claimed_user is None
        assert all(tc.status == "Untested" for tc in group.test_cases)
        assert group.fw_run.failed_total == 0
        assert RevisionHistory.query.filter(RevisionHistory.revision_details.like("Re-test%")).count() == 1


def test_flash_and_run_saves_ini_and_forces_a_rebuild(app, client, login, failed_group, wait_for_job, monkeypatch):
    calls = {}
    monkeypatch.setattr(failures_routes, "save_edited_failure_ini", lambda *a: calls.setdefault("saved", a) or True)

    def fake_firmware(config, fw, email, workdir, force_rebuild=False, **kw):
        calls["force"] = force_rebuild
        path = os.path.join(workdir, "fw.bin")
        open(path, "wb").write(b"FIRMWARE")
        return path
    monkeypatch.setattr(failures_routes, "get_or_build_firmware_bin", fake_firmware)

    login("SET")
    r = client.post(f"/failures/flash_and_run/{failed_group}", data={"ini_content": "[av]\nidle_color = red"}, headers=JSON)
    assert r.status_code == 202
    job = wait_for_job(r.json["job_id"])
    assert job["status"] == "done" and job["files"] == ["bin"] and calls["force"] is True
    assert job["result"]["fw_version"] == "v5.4.10"
    assert client.get(f"/jobs/{job['id']}/files/bin").data == b"FIRMWARE"
    with app.app_context():
        assert db.session.get(Job, job["id"]).files["bin"]["name"] == "CQU1_v5.4.10_firmware.bin"


def test_flash_and_run_stops_when_the_ini_cant_be_saved(client, login, failed_group, wait_for_job, monkeypatch):
    monkeypatch.setattr(failures_routes, "save_edited_failure_ini", lambda *a: False)
    login("SET")
    r = client.post(f"/failures/flash_and_run/{failed_group}", data={"ini_content": "x"}, headers=JSON)
    job = wait_for_job(r.json["job_id"])
    assert job["status"] == "failed" and "Nothing was built" in job["error"]


def test_flash_and_run_needs_ini_content(client, login, failed_group):
    login("SET")
    assert client.post(f"/failures/flash_and_run/{failed_group}", data={}, headers=JSON).status_code == 400
