"""Sign-in rules and the admin Users page."""
import pytest

from auth import LoginBlocked, find_login_user
from conftest import flashed
from models import db, User


def user(app, email):
    with app.app_context():
        found = User.query.filter_by(email=email).first()
        if found:
            db.session.expunge(found)
        return found


def test_only_known_active_wavelynx_users_can_sign_in(app):
    with app.app_context():
        db.session.add_all([User(first_name="Ann", last_name_initial="B", email="ann@wavelynx.com", role="QA"),
                            User(first_name="Old", last_name_initial="C", email="old@wavelynx.com", role="QA", is_active=False)])
        db.session.commit()
        assert find_login_user("ANN@wavelynx.com").email == "ann@wavelynx.com"
        with pytest.raises(LoginBlocked, match="Ask James H"):
            find_login_user("stranger@wavelynx.com")
        with pytest.raises(LoginBlocked, match="Ask James H"):
            find_login_user("old@wavelynx.com")
        with pytest.raises(LoginBlocked, match="Only @wavelynx.com"):
            find_login_user("someone@gmail.com")


def test_users_page_is_admin_only(client, login):
    login("SET", "set2@x.com")
    assert flashed(client.get("/admin/users", follow_redirects=True), "Only CALIGA admins")
    login("SET", "set@x.com")
    r = client.get("/admin/users")
    assert r.status_code == 200 and "Add a User" in r.text


def test_manage_users_link_shows_for_admin_only(client, login):
    login("SET", "set@x.com")
    assert "Manage Users" in client.get("/").text
    login("QA")
    assert "Manage Users" not in client.get("/").text


def test_admin_adds_a_user(app, client, login):
    login("SET", "set@x.com")
    r = client.post("/admin/users", data={"first_name": "Ann", "last_name_initial": "b", "email": "Ann@Wavelynx.com",
                                          "role": "QA"}, follow_redirects=True)
    assert flashed(r, "Added Ann B.")
    added = user(app, "ann@wavelynx.com")
    assert added.role == "QA" and added.is_active and not added.is_admin


@pytest.mark.parametrize("form, error", [
    ({"first_name": "Ann", "last_name_initial": "B", "email": "ann@gmail.com", "role": "QA"}, "@wavelynx.com"),
    ({"first_name": "Ann", "last_name_initial": "B", "email": "ann@wavelynx.com", "role": "Admin"}, "Pick a role"),
    ({"first_name": "", "last_name_initial": "B", "email": "ann@wavelynx.com", "role": "QA"}, "first name"),
])
def test_add_user_validation(client, login, form, error):
    login("SET", "set@x.com")
    assert flashed(client.post("/admin/users", data=form, follow_redirects=True), error)


def test_duplicate_email_is_rejected(client, login):
    login("SET", "set@x.com")
    form = {"first_name": "Ann", "last_name_initial": "B", "email": "ann@wavelynx.com", "role": "QA"}
    client.post("/admin/users", data=form)
    assert flashed(client.post("/admin/users", data=form, follow_redirects=True), "already a user")


def test_admin_changes_a_role(app, client, login):
    login("SET", "set@x.com")
    qa = user(app, "qa@x.com")
    client.post(f"/admin/users/{qa.id}", data={"role": "SET"})
    assert user(app, "qa@x.com").role == "SET"


def test_deactivated_users_are_signed_out_and_hidden(app, client, login):
    qa = user(app, "qa@x.com")
    login("QA", "qa@x.com")
    assert client.get("/configurations").status_code == 200

    other = app.test_client()
    other.get("/switch_user/SET?email=set@x.com")
    other.post(f"/admin/users/{qa.id}", data={"is_active": "false"})

    assert client.get("/configurations").status_code == 302         # signed out mid-session
    assert "Rebecca R." not in client.get("/").text                   # gone from the dev login menu
    assert flashed(client.get("/switch_user/QA?email=qa@x.com", follow_redirects=True), "No QA user found")


def test_admin_cant_deactivate_themselves(app, client, login):
    login("SET", "set@x.com")
    me = user(app, "set@x.com")
    assert flashed(client.post(f"/admin/users/{me.id}", data={"is_active": "false"}, follow_redirects=True),
                   "deactivate your own account")
    assert user(app, "set@x.com").is_active


def test_non_admins_cant_change_users(app, client, login):
    login("SET", "set2@x.com")
    qa = user(app, "qa@x.com")
    client.post(f"/admin/users/{qa.id}", data={"role": "SET"})
    assert user(app, "qa@x.com").role == "QA"


def test_sign_out(client, login):
    login("QA")
    assert flashed(client.post("/logout", follow_redirects=True), "signed out")
    assert client.get("/configurations").status_code == 302


def test_dev_login_can_be_turned_off(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    assert "Development login" in client.get("/").text
    # Only "dev" exists today, so any other mode is refused rather than silently allowing anyone in.
    monkeypatch.setenv("AUTH_MODE", "google")
    with pytest.raises(RuntimeError, match="isn't supported yet"):
        client.get("/")
