"""Creating, editing, toggling, archiving, and alerting QA for configurations."""
from conftest import flashed
from models import db, Configuration, RevisionHistory


def get_config(app, name):
    with app.app_context():
        config = Configuration.query.filter_by(config_name=name).first()
        if config:
            db.session.expunge(config)
        return config


def revisions(app, name, like="%"):
    with app.app_context():
        return RevisionHistory.query.filter(RevisionHistory.config_name == name,
                                            RevisionHistory.revision_details.like(like)).count()


def test_create_assigns_the_next_integer_id(app, client, login):
    login("Sales")
    assert "CONFIG_1" in client.get("/configurations/new").text

    r = client.post("/configurations/create", data={"config_name": "CQU1", "idle_led": "Blue", "facility_code": "12",
                                                     "starting_badge": "0042"}, follow_redirects=True)
    assert flashed(r, "successfully created")

    config = get_config(app, "CQU1")
    assert config.config_id_dec == 1 and config.config_id_hex == "0x0001"
    assert config.starting_badge == "0042"  # kept as text, leading zeros intact
    assert config.status == "POC"
    assert revisions(app, "CQU1") == 1


def test_create_rejects_a_bad_facility_code(client, login):
    login("Sales")
    r = client.post("/configurations/create", data={"config_name": "BAD", "facility_code": "12a"}, follow_redirects=True)
    assert flashed(r, "whole number")


def test_create_and_edit_are_sales_only(client, login, make_config):
    make_config("CQU1", status="POC")
    login("QA")
    assert flashed(client.post("/configurations/CQU1/update", data={"idle_led": "Green"}, follow_redirects=True),
                   "requires the Sales role")


def test_details_page_has_no_nested_forms(client, login, make_config):
    make_config("CQU1", status="POC")
    login("Sales")
    html = client.get("/configurations/CQU1").text
    assert html.count("<form") == html.count("</form>")
    assert 'form="toggle-status-form"' in html and 'form="archive-form"' in html and "Save Changes" in html


def test_revision_history_is_for_sales_and_set(client, login, make_config):
    make_config("CQU1")
    for role, visible in [("Sales", True), ("SET", True), ("QA", False)]:
        login(role)
        assert ("Revision History" in client.get("/configurations/CQU1").text) is visible, role


def test_update_saves_changes_and_rolls_back_on_a_bad_value(app, client, login, make_config):
    make_config("CQU1", status="POC", idle_led="Blue")
    login("Sales")

    client.post("/configurations/CQU1/update", data={"idle_led": "Red", "facility_code": "x"})
    assert get_config(app, "CQU1").idle_led == "Blue"

    client.post("/configurations/CQU1/update", data={"idle_led": "Red", "facility_code": "15"})
    config = get_config(app, "CQU1")
    assert config.idle_led == "Red" and config.facility_code == 15


def test_only_poc_configs_can_be_edited(app, client, login, make_config):
    make_config("CQU1", status="Active", idle_led="Blue")
    login("Sales")
    assert flashed(client.post("/configurations/CQU1/update", data={"idle_led": "Red"}, follow_redirects=True),
                   "Only POC configurations")
    assert get_config(app, "CQU1").idle_led == "Blue"


def test_toggle_moves_between_poc_and_active(app, client, login, make_config):
    make_config("CQU1", status="POC")
    login("Sales")
    client.post("/configurations/toggle_status/CQU1")
    config = get_config(app, "CQU1")
    assert config.status == "Active" and config.previous_status == "POC"
    assert revisions(app, "CQU1", "Status changed%") == 1


def test_old_get_toggle_route_is_gone(client, login, make_config):
    make_config("CQU1")
    login("Sales")
    assert client.get("/configurations/CQU1/toggle-status/Production").status_code == 404


def test_alert_qa_is_post_only_and_needs_active(client, login, make_config):
    make_config("CQU1", status="Active")
    make_config("CQU2", status="POC")
    login("Sales")
    assert client.get("/configurations/CQU1/alert-qa").status_code == 405
    assert flashed(client.post("/configurations/CQU1/alert-qa", follow_redirects=True), "could not be sent")  # no webhook in tests
    assert flashed(client.post("/configurations/CQU2/alert-qa", follow_redirects=True), "only be alerted for Active")


def test_archive_disables_tools(client, login, make_config):
    make_config("CQU1")
    login("Sales")
    client.post("/configurations/archive/CQU1")
    assert flashed(client.get("/configurations/CQU1/download-ini", follow_redirects=True), "Archived")


def test_search_matches_integer_ids(client, login, make_config):
    make_config("CQU1", config_id=42)
    login("Sales")
    assert "CQU1" in client.get("/configurations?search=42").text


def test_leaf_and_wallet_fields_are_validated_and_normalized(app, client, login):
    login("Sales")
    client.post("/configurations/create", data={
        "config_name": "CTST1", "leaf_si": "On", "leaf_cc": "On", "keyset_id": "Lk12345", "custom_keyset": "On",
        "ecp_desfire": "On", "mifare_2go": "Off", "ecp_tci": "0209fe", "meridian_bit_count": "57",
        "ble_advertising_name": "abcd"})
    config = get_config(app, "CTST1")
    assert (config.ecp_tci, config.meridian_bit_count, config.ble_advertising_name) == ("0209FE", 57, "ABCD")
    assert config.leaf_si_enabled and config.leaf_cc_enabled

    assert flashed(client.post("/configurations/create", data={"config_name": "B1", "ecp_tci": "XYZ"}, follow_redirects=True),
                   "6 hex characters")
    assert flashed(client.post("/configurations/create", data={"config_name": "B2", "meridian_bit_count": "33"},
                               follow_redirects=True), "must be one of")
    assert flashed(client.post("/configurations/CTST1/update", data={"ble_advertising_name": "TOOLONG"},
                               follow_redirects=True), "at most 5")


def test_leaf_falls_back_to_card_type(app, make_config):
    make_config("CDUAL", card_type="Dual")
    config = get_config(app, "CDUAL")
    assert config.leaf_si_enabled and config.leaf_cc_enabled
