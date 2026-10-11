"""User switching, role gates, CSRF, and that every page renders for every role."""
import pytest

from conftest import JSON, flashed

PAGES = ["/", "/configurations", "/configurations/CQU1", "/configurations/CQU3", "/testing", "/testing?fw_version=v5.4.11",
         "/tools", "/tools/partial-config", "/tools/ini-translator", "/tools/yaml-automator", "/tools/batch-tokens"]


def test_home_works_without_a_user(client):
    assert client.get("/").status_code == 200


def test_pages_redirect_home_until_a_user_is_picked(client):
    r = client.get("/configurations")
    assert r.status_code == 302 and r.location.endswith("/")


def test_json_requests_get_401_until_a_user_is_picked(client):
    assert client.get("/configurations/x/get-tokens", headers=JSON).status_code == 401


def test_switch_user_picks_a_specific_user(client, login):
    r = login("QA", "qa2@x.com")
    assert r.status_code == 302
    assert "Jake S." in client.get("/").text


def test_unknown_role_is_404(client, login):
    assert login("Admin").status_code == 404


@pytest.mark.parametrize("role", ["Sales", "QA", "SET"])
def test_every_page_renders_for_every_role(client, login, make_config, role):
    make_config("CQU1")
    make_config("CQU3", status="Archived")
    login(role)
    for url in PAGES:
        assert client.get(url).status_code == 200, url
    assert client.get("/failures").status_code == (200 if role == "SET" else 302)


def test_failures_dashboard_is_set_only(client, login):
    login("QA")
    assert flashed(client.get("/failures", follow_redirects=True), "requires the SET role")


def test_forms_without_a_csrf_token_are_rejected(app, client, login, make_config, monkeypatch):
    make_config("CQU1", status="POC")
    login("Sales")
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    r = client.post("/configurations/toggle_status/CQU1", follow_redirects=True)
    assert flashed(r, "expired")
