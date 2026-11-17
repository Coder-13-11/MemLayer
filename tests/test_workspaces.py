"""Teams: workspaces, roles, invitations, API keys and provider keys, through the signed-in app's API."""

import json

import httpx
import pytest

from nexus.accounts import AccountStore
from nexus.crypto import KeyRing, SecretsUnavailable, generate_key
from nexus.models import ValidationError
from nexus.tenancy import PermissionDenied
from tests.conftest import PASSWORD, browser, sign_up, start_nexus

W = {"X-Nexus-Workspace": "acme"}


@pytest.fixture
def team(nexus):
    """A workspace with an owner (olga), an admin (adam), an editor (eve) and a viewer (vera)."""
    base, accounts = nexus
    olga = sign_up(base, "olga@example.com", name="Olga", workspace="Acme")
    people = {"olga": olga}
    for name, role in (("adam", "admin"), ("eve", "editor"), ("vera", "viewer")):
        r = olga.post("/api/workspace/invitations", json={"email": f"{name}@example.com", "role": role})
        assert r.status_code == 201 and r.json()["emailed"] is False  # no mail server: the link is shown instead
        people[name] = sign_up(base, f"{name}@example.com", name=name.title(),
                               invitation=r.json()["link"].rsplit("/", 1)[1])
    yield base, accounts, people
    for b in people.values():
        b.close()


def test_invited_people_join_with_their_role_and_see_the_same_projects(team):
    base, accounts, p = team
    assert p["olga"].post("/api/projects", json={"name": "Shared project"}).status_code == 201
    for name, role in (("adam", "admin"), ("eve", "editor"), ("vera", "viewer")):
        me = p[name].get("/api/me").json()
        assert me["workspace"]["name"] == "Acme" and me["workspace"]["role"] == role
        assert len(me["workspaces"]) == 1  # an invited person does not also get an empty personal workspace
        assert me["user"]["email_verified_at"] is None  # joining by link does not prove the mailbox is theirs
        assert [x["name"] for x in p[name].get("/api/projects").json()["projects"]] == ["Shared project"]
    members = p["vera"].get("/api/workspace/members").json()
    assert [m["role"] for m in members["members"]] == ["owner", "admin", "editor", "viewer"]
    assert members["invitations"] == []  # a viewer does not see pending invitations


def test_roles_limit_what_each_person_can_do(team):
    base, accounts, p = team
    pid = p["eve"].post("/api/projects", json={"name": "Editors can write"}).json()["id"]
    rid = p["eve"].post(f"/api/projects/{pid}/records", json={"kind": "decision", "title": "Use Postgres"}).json()["id"]
    # viewer: reads, nothing else
    assert p["vera"].get(f"/api/records/{rid}").json()["created_by"] == "eve@example.com"
    for call in (lambda: p["vera"].post("/api/projects", json={"name": "No"}),
                 lambda: p["vera"].patch(f"/api/records/{rid}", json={"reason": "x", "title": "Y"}),
                 lambda: p["vera"].patch(f"/api/records/{rid}", json={"confirm": True}),
                 lambda: p["vera"].delete(f"/api/records/{rid}"),
                 lambda: p["vera"].post(f"/api/projects/{pid}/import", json={"text": "## Notes\n- [N1] x"}),
                 lambda: p["vera"].post("/api/workspace/demo", json={})):
        r = call()
        assert r.status_code == 403 and "viewer" in r.json()["error"]
    # editor: writes records, cannot administer or delete projects
    for call in (lambda: p["eve"].post("/api/workspace/invitations", json={"email": "x@example.com"}),
                 lambda: p["eve"].post("/api/workspace/keys", json={"name": "k"}),
                 lambda: p["eve"].get("/api/workspace/keys"),
                 lambda: p["eve"].post("/api/workspace/providers", json={"provider": "openai", "secret": "sk-12345678"}),
                 lambda: p["eve"].get("/api/workspace/audit"),
                 lambda: p["eve"].get("/api/workspace/export"),
                 lambda: p["eve"].patch("/api/workspace", json={"name": "Mine now"}),
                 lambda: p["eve"].delete(f"/api/projects/{pid}"),
                 lambda: p["eve"].patch(f"/api/workspace/members/{accounts.find_user('vera@example.com')['id']}",
                                        json={"role": "admin"})):
        assert call().status_code == 403
    # admin: manages people and keys, cannot touch owners or delete the workspace
    olga_id, eve_id = accounts.find_user("olga@example.com")["id"], accounts.find_user("eve@example.com")["id"]
    assert p["adam"].patch(f"/api/workspace/members/{eve_id}", json={"role": "viewer"}).status_code == 200
    assert p["adam"].patch(f"/api/workspace/members/{eve_id}", json={"role": "owner"}).status_code == 403
    assert p["adam"].patch(f"/api/workspace/members/{olga_id}", json={"role": "editor"}).status_code == 403
    assert p["adam"].delete(f"/api/workspace/members/{olga_id}").status_code == 403
    assert p["adam"].request("DELETE", "/api/workspace", json={"confirm": "Acme"}).status_code == 403
    assert p["adam"].delete(f"/api/projects/{pid}").status_code == 200
    # the last owner cannot leave or demote themselves
    assert p["olga"].patch(f"/api/workspace/members/{olga_id}", json={"role": "admin"}).status_code == 400
    assert p["olga"].delete(f"/api/workspace/members/{olga_id}").status_code == 400
    # anyone can leave
    assert p["vera"].delete(f"/api/workspace/members/{accounts.find_user('vera@example.com')['id']}").status_code == 200
    assert p["vera"].get("/api/me").json()["workspace"]["name"] != "Acme"  # back to a workspace of their own
    actions = [a["action"] for a in p["olga"].get("/api/workspace/audit").json()["audit"]]
    assert {"invitation.created", "member.joined", "member.role_changed", "member.left"} <= set(actions)


def test_only_a_signed_in_person_can_confirm_a_record(team):
    base, accounts, p = team
    pid = p["eve"].post("/api/projects", json={"name": "P"}).json()["id"]
    rid = p["eve"].post(f"/api/projects/{pid}/records", json={"kind": "decision", "title": "Use Postgres",
                                                              "origin_model": "claude"}).json()["id"]
    done = p["eve"].patch(f"/api/records/{rid}", json={"confirm": True}).json()
    assert done["confirmed_by"] == "eve@example.com" and done["history"][-1]["change_reason"] == "Confirmed"
    key = p["olga"].post("/api/workspace/keys", json={"name": "agent", "role": "editor"}).json()["secret"]
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}) as api:
        refused = api.patch(f"/v1/records/{rid}", json={"confirm": True})
        assert refused.status_code == 400 and "signed-in person" in refused.json()["error"]
        # A key rewording the record removes the person's confirmation.
        edited = api.patch(f"/v1/records/{rid}", json={"reason": "agent edit", "title": "Use MySQL"}).json()
        assert edited["confirmed_by"] is None and edited["history"][-1]["changed_by"] == "key:agent"
        assert api.get(f"/v1/records/{rid}").json()["title"] == "Use MySQL"


def test_invitation_is_for_one_address_and_one_use(nexus):
    base, accounts = nexus
    olga = sign_up(base, "olga@example.com", workspace="Acme")
    link = olga.post("/api/workspace/invitations", json={"email": "Ned@Example.com", "role": "editor"}).json()["link"]
    token = link.rsplit("/", 1)[1]
    assert olga.post("/api/workspace/invitations", json={"email": "olga@example.com"}).status_code == 400  # a member
    assert olga.post("/api/workspace/invitations", json={"email": "x@example.com", "role": "owner"}).status_code == 400
    mallory = sign_up(base, "mallory@example.com")
    page = mallory.get(f"/invite/{token}")
    assert "ned@example.com" in page.text and "signed in as mallory@example.com" in page.text
    assert mallory.post(f"/invite/{token}").status_code == 400  # a forwarded link does not let someone else in
    assert [w["name"] for w in mallory.get("/api/me").json()["workspaces"]] != ["Acme"]
    with browser(base) as anon:
        landing = anon.get(f"/invite/{token}")
        assert "Join Acme" in landing.text and f"/signup?invitation={token}" in landing.text
        form = anon.get(f"/signup?invitation={token}")
        assert "ned@example.com" in form.text and "readonly" in form.text
    ned = sign_up(base, "someone-else@example.com", invitation=token)  # the form's email is ignored for invitations
    assert ned.get("/api/me").json()["user"]["email"] == "ned@example.com"
    assert ned.get(f"/invite/{token}").status_code == 400  # used
    # A replaced or withdrawn invitation stops working.
    first = olga.post("/api/workspace/invitations", json={"email": "pat@example.com"}).json()["link"]
    second = olga.post("/api/workspace/invitations", json={"email": "pat@example.com"}).json()
    with browser(base) as anon:
        assert anon.get(first.replace(base, "")).status_code == 400
        assert anon.get(second["link"].replace(base, "")).status_code == 200
        assert olga.delete(f"/api/workspace/invitations/{second['invitation']['id']}").status_code == 200
        assert anon.get(second["link"].replace(base, "")).status_code == 400
    for b in (olga, mallory, ned):
        b.close()


def test_member_limit_counts_pending_invitations(nexus):
    base, accounts = nexus
    olga = sign_up(base, "olga@example.com", workspace="Acme")
    for i in range(4):
        assert olga.post("/api/workspace/invitations", json={"email": f"p{i}@example.com"}).status_code == 201
    over = olga.post("/api/workspace/invitations", json={"email": "p9@example.com"})
    assert over.status_code == 402 and "5 members" in over.json()["error"]
    olga.close()


def test_api_keys_are_shown_once_scoped_and_revocable(team):
    base, accounts, p = team
    made = p["adam"].post("/api/workspace/keys", json={"name": "ci", "role": "viewer", "scopes": ["memory"]}).json()
    secret = made["secret"]
    assert secret.startswith("nxk_") and made["key"]["prefix"] == secret[:12]
    listed = p["adam"].get("/api/workspace/keys").json()["keys"]
    assert [k["name"] for k in listed] == ["ci"] and "secret" not in json.dumps(listed) and secret not in json.dumps(listed)
    assert p["adam"].post("/api/workspace/keys", json={"name": "bad", "role": "admin"}).status_code == 400
    with httpx.Client(base_url=base) as api:
        auth = {"Authorization": f"Bearer {secret}"}
        assert api.get("/v1/projects").status_code == 401
        assert api.get("/v1/projects", headers={"Authorization": "Bearer nxk_wrong"}).status_code == 401
        meta = api.get("/v1/meta", headers=auth).json()
        assert meta["workspace"]["name"] == "Acme" and meta["role"] == "viewer" and meta["key"]["name"] == "ci"
        assert api.post("/v1/projects", headers=auth, json={"name": "x"}).status_code == 403
        # no gateway scope
        gw = api.post("/v1/chat/completions", headers=auth, json={"model": "mock-1", "messages": []})
        assert gw.status_code == 403 and gw.json()["error"]["type"] == "permission_error"
        assert api.get("/api/me", headers=auth).status_code == 401  # a key is not a browser session
        assert p["adam"].delete(f"/api/workspace/keys/{made['key']['id']}").status_code == 200
        assert api.get("/v1/projects", headers=auth).status_code == 401


def test_provider_keys_are_sealed_and_never_returned(team):
    base, accounts, p = team
    real = "sk-live-AbCdEf0123456789"
    made = p["adam"].post("/api/workspace/providers", json={"provider": "openai", "secret": real})
    assert made.status_code == 201 and made.json()["credential"]["hint"] == "6789"
    everything = "".join(p[n].get(u).text for n in ("adam", "olga") for u in
                         ("/api/workspace/providers", "/api/workspace", "/api/me", "/api/workspace/audit",
                          "/api/workspace/export"))
    assert real not in everything
    ws = accounts.get_workspace("acme")
    row = dict(accounts.conn.execute("SELECT * FROM provider_credentials").fetchone())
    assert real not in json.dumps(row) and row["key_version"] == accounts.keyring.primary_id
    assert accounts.open_credential(ws["id"], "openai")["secret"] == real
    assert p["adam"].post("/api/workspace/providers", json={"provider": "openai_compatible", "secret": real}).status_code == 400
    assert p["adam"].post("/api/workspace/providers", json={"provider": "openai_compatible", "secret": real,
                                                            "base_url": "http://10.0.0.5/v1"}).status_code == 400
    assert p["adam"].post("/api/workspace/providers", json={"provider": "nope", "secret": real}).status_code == 400
    assert p["adam"].delete(f"/api/workspace/providers/{made.json()['credential']['id']}").status_code == 200
    with pytest.raises(LookupError):
        accounts.open_credential(ws["id"], "openai")


def test_envelope_encryption_binds_a_secret_to_its_row_and_supports_rotation(tmp_path):
    old, new = generate_key(), generate_key()
    ring = KeyRing(__import__("nexus.crypto", fromlist=["_decode_key"])._decode_key(old))
    sealed = ring.seal("sk-secret", "ws1:crd1")
    assert ring.open(**sealed, context="ws1:crd1") == "sk-secret"
    assert "sk-secret" not in json.dumps(sealed)
    with pytest.raises(SecretsUnavailable):
        ring.open(**sealed, context="ws2:crd1")  # copied into another workspace: does not decrypt
    tampered = {**sealed, "ciphertext": sealed["ciphertext"][:-4] + "AAAA"}
    with pytest.raises(SecretsUnavailable):
        ring.open(**tampered, context="ws1:crd1")
    assert ring.seal("sk-secret", "ws1:crd1")["ciphertext"] != sealed["ciphertext"]  # fresh data key and nonce

    acc = AccountStore(tmp_path, keyring=ring)
    user = acc.create_user("k@example.com", PASSWORD)
    ws = acc.create_workspace("K", user["id"])
    acc.set_credential(ws["id"], "anthropic", "sk-ant-0123456789")
    acc.close()
    decode = __import__("nexus.crypto", fromlist=["_decode_key"])._decode_key
    rotated = AccountStore(tmp_path, keyring=KeyRing(decode(new), [decode(old)]))
    assert rotated.open_credential(ws["id"], "anthropic")["secret"] == "sk-ant-0123456789"  # old key still reads
    assert rotated.rotate_credentials() == 1 and rotated.rotate_credentials() == 0
    rotated.close()
    only_new = AccountStore(tmp_path, keyring=KeyRing(decode(new)))
    assert only_new.open_credential(ws["id"], "anthropic")["secret"] == "sk-ant-0123456789"
    only_new.close()
    lost = AccountStore(tmp_path, keyring=KeyRing(decode(generate_key())))
    with pytest.raises(SecretsUnavailable):
        lost.open_credential(ws["id"], "anthropic")
    lost.close()


def test_password_change_reset_and_account_deletion(nexus):
    base, accounts = nexus
    olga = sign_up(base, "olga@example.com", workspace="Acme")
    other = browser(base)
    assert other.post("/login", data={"email": "olga@example.com", "password": PASSWORD}).status_code == 303
    assert olga.post("/api/me/password", json={"current": "wrong", "new": "a brand new password"}).status_code == 400
    assert olga.post("/api/me/password", json={"current": PASSWORD, "new": "short"}).status_code == 400
    assert olga.post("/api/me/password", json={"current": PASSWORD, "new": "a brand new password"}).status_code == 200
    assert olga.get("/api/me").status_code == 200 and other.get("/api/me").status_code == 401  # other browsers signed out

    # Forgotten password: the page never says whether the address exists; a local server shows the link.
    with browser(base) as anon:
        assert "on its way" in anon.post("/forgot", data={"email": "nobody@example.com"}).text
        sent = anon.post("/forgot", data={"email": "olga@example.com"})
        token = sent.text.split("/reset/")[1].split("'")[0].split("<")[0]
        assert anon.post(f"/reset/{token}", data={"password": "tiny"}).status_code == 400
        assert anon.post(f"/reset/{token}", data={"password": "the third password!"}).status_code == 303
        assert anon.get(f"/reset/{token}").status_code == 400  # single use
        assert anon.post("/login", data={"email": "olga@example.com", "password": "a brand new password"}).status_code == 400
        assert anon.post("/login", data={"email": "olga@example.com", "password": "the third password!"}).status_code == 303
    assert olga.get("/api/me").status_code == 401  # a reset signs out every browser
    assert [m["subject"] for m in accounts.outbox()][0].startswith("Reset your")

    olga = browser(base)
    olga.post("/login", data={"email": "olga@example.com", "password": "the third password!"})
    link = olga.post("/api/workspace/invitations", json={"email": "eve@example.com", "role": "editor"}).json()["link"]
    eve = sign_up(base, "eve@example.com", invitation=link.rsplit("/", 1)[1])
    ws = accounts.get_workspace("acme")
    path = accounts.data_dir / ws["store_ref"]
    assert olga.post("/api/projects", json={"name": "P"}).status_code == 201 and path.exists()
    # The only owner of a shared workspace must hand it over or delete it first.
    blocked = olga.request("DELETE", "/api/me", json={"password": "the third password!"})
    assert blocked.status_code == 400 and "only owner" in blocked.json()["error"]
    assert olga.request("DELETE", "/api/workspace", json={"confirm": "wrong"}).status_code == 400
    assert olga.request("DELETE", "/api/workspace", json={"confirm": "Acme"}).status_code == 200
    assert not path.exists()  # the memory file is gone with the workspace
    assert eve.get("/api/me").json()["workspace"]["name"] != "Acme"
    assert olga.request("DELETE", "/api/me", json={"password": "nope"}).status_code == 400
    assert olga.request("DELETE", "/api/me", json={"password": "the third password!"}).status_code == 200
    assert accounts.find_user("olga@example.com") is None and olga.get("/api/me").status_code == 401
    for b in (olga, other, eve):
        b.close()


def test_export_contains_the_workspace_and_nothing_secret(team):
    base, accounts, p = team
    p["olga"].post("/api/workspace/demo", json={})
    p["olga"].post("/api/workspace/providers", json={"provider": "openai", "secret": "sk-live-0123456789"})
    out = p["olga"].get("/api/workspace/export")
    assert "attachment" in out.headers["content-disposition"]
    data = out.json()
    assert data["workspace"]["name"] == "Acme" and len(data["members"]) == 4
    assert len(data["projects"][0]["records"]) == 18 and data["projects"][0]["history"]
    assert "sk-live" not in out.text and "password" not in out.text


def test_closed_signup_allows_only_invitations(tmp_path):
    base, accounts, stop = start_nexus(tmp_path, signup="closed")
    try:
        with browser(base) as anon:
            assert "by invitation" in anon.get("/signup").text
            assert anon.post("/signup", data={"name": "X", "email": "x@example.com", "password": PASSWORD}).status_code == 400
        owner = accounts.create_user("owner@example.com", PASSWORD)
        ws = accounts.create_workspace("Closed", owner["id"])
        _, token = accounts.create_invitation(ws["id"], "in@example.com", "viewer", owner["id"])
        joined = sign_up(base, "in@example.com", invitation=token)
        assert joined.get("/api/me").json()["workspace"]["name"] == "Closed"
        joined.close()
    finally:
        stop()


def test_role_rules_at_the_store(tmp_path):
    acc = AccountStore(tmp_path)
    a, b = acc.create_user("a@example.com", PASSWORD), acc.create_user("b@example.com", PASSWORD)
    ws = acc.create_workspace("Two owners", a["id"])
    _, token = acc.create_invitation(ws["id"], "b@example.com", "admin", a["id"])
    acc.accept_invitation(token, b)
    with pytest.raises(PermissionDenied):
        acc.set_role(ws["id"], b["id"], "owner", by_role="admin")
    acc.set_role(ws["id"], b["id"], "owner", by_role="owner")
    acc.set_role(ws["id"], a["id"], "viewer", by_role="owner")  # fine now: another owner exists
    with pytest.raises(ValidationError, match="at least one owner"):
        acc.remove_member(ws["id"], b["id"], by_role="owner")
    assert acc.get_workspace("two-owners")["id"] == ws["id"] and acc.create_workspace("Two owners", a["id"])["slug"] == "two-owners-2"
    assert acc.migrations_applied == ["0001_accounts", "0002_workspaces", "0003_keys", "0004_metering"]
    acc.close()
    assert AccountStore(tmp_path).migrations_applied == []  # reopening applies nothing twice
