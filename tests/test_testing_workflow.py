"""Firmware runs: starting, assigning, submitting, promotion and demotion."""
import pytest

from conftest import flashed
from models import db, Configuration, ConfigurationTestGroup, FWRun, RevisionHistory
import routes.testing as testing_routes


@pytest.fixture(autouse=True)
def no_jira_or_slack(monkeypatch):
    monkeypatch.setattr(testing_routes, "create_failure_ticket", lambda *a: None)
    monkeypatch.setattr(testing_routes, "send_failure_message", lambda *a: True)


def start_run(client, names, fw="v5.4.10"):
    return client.post("/testing/start-run", data={"fw_version": fw, "config_names": names}, follow_redirects=True)


def latest_group(app, name):
    with app.app_context():
        group = (ConfigurationTestGroup.query.filter_by(config_name=name)
                 .order_by(ConfigurationTestGroup.id.desc()).first())
        return group.id, [tc.id for tc in group.test_cases]


def submit(client, group_id, case_ids, fail_first=False, comment="LED was blue"):
    form = {f"status_{i}": "Passed" for i in case_ids}
    if fail_first:
        form[f"status_{case_ids[0]}"] = "Failed"
        if comment:
            form[f"comment_{case_ids[0]}"] = comment
    return client.post(f"/testing/submit-group/{group_id}", data=form, follow_redirects=True)


def run_and_submit(app, client, name, fail=False):
    start_run(client, [name])
    group_id, cases = latest_group(app, name)
    client.post(f"/testing/assign-group/{group_id}", data={"action": "assign"})
    submit(client, group_id, cases, fail_first=fail)
    return group_id


def config_status(app, name):
    with app.app_context():
        config = Configuration.query.filter_by(config_name=name).first()
        return config.status, config.previous_status


def test_poc_configs_are_skipped(app, client, login, make_config):
    make_config("CQU1", idle_led="Red")
    make_config("POCONLY", status="POC")
    login("QA")
    r = start_run(client, ["CQU1", "POCONLY"])
    assert flashed(r, "POCONLY")
    with app.app_context():
        assert [g.config_name for g in FWRun.query.one().test_groups] == ["CQU1"]


def test_test_cases_store_category_and_expected_value(app, client, login, make_config):
    make_config("CQU1", idle_led="Red", leaf_si="On", ecp_desfire="On")
    login("QA")
    start_run(client, ["CQU1"])
    with app.app_context():
        group = ConfigurationTestGroup.query.one()
        first = group.test_cases[0]
        assert first.category.startswith("ACTION") and first.criterion_name == "Idle LED" and first.expected_value == "Red"
        assert group.configuration_id is not None
        assert group.cases_by_category[0][0] == "ACTION: Verify AV and Output Formats."
    html = client.get("/testing?fw_version=v5.4.10").text
    for expected in ("Red", "Leaf Si", "Apple"):
        assert f'<code class="text-warning fs-7">{expected}</code>' in html


def test_submit_rules(app, client, login, make_config):
    make_config("CQU1")
    login("QA", "qa@x.com")
    start_run(client, ["CQU1"])
    group_id, cases = latest_group(app, "CQU1")

    assert flashed(submit(client, group_id, cases), "tester is assigned")
    client.post(f"/testing/assign-group/{group_id}", data={"action": "assign"})

    login("QA", "qa2@x.com")
    assert flashed(submit(client, group_id, cases), "Only the assigned tester")

    login("QA", "qa@x.com")
    assert flashed(submit(client, group_id, cases, fail_first=True, comment=""), "comments are required")
    assert flashed(submit(client, group_id, cases, fail_first=True), "could not be created")  # no Jira in tests
    assert flashed(submit(client, group_id, cases), "already submitted")


def test_pass_promotes_and_totals_are_calculated(app, client, login, make_config):
    make_config("CQU1")
    make_config("CQU2")
    login("QA")
    run_and_submit(app, client, "CQU1", fail=True)
    run_and_submit(app, client, "CQU2")
    assert config_status(app, "CQU2") == ("Production", "Active")
    with app.app_context():
        assert RevisionHistory.query.filter(RevisionHistory.revision_details.like("Promoted%")).count() == 1
        totals = [(r.passed_total, r.failed_total) for r in FWRun.query.all()]
        assert sorted(totals) == [(0, 1), (1, 0)]


def test_repeat_pass_keeps_previous_status_and_fail_demotes(app, client, login, make_config):
    make_config("CQU2")
    login("QA")
    run_and_submit(app, client, "CQU2")
    run_and_submit(app, client, "CQU2")
    assert config_status(app, "CQU2") == ("Production", "Active")
    run_and_submit(app, client, "CQU2", fail=True)
    assert config_status(app, "CQU2")[0] == "Active"


def test_start_run_is_for_qa_and_set(client, login, make_config):
    make_config("CQU1")
    login("Sales")
    assert flashed(start_run(client, ["CQU1"]), "requires the QA or SET role")
