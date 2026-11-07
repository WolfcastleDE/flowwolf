"""User management and roles end to end: invitations, accepting them, roles and what each role may
do, password reset (link and token), changing the own password, two-factor authentication with
recovery codes, deleting users with and without a data transfer, API keys of deleted users.

Usage: python tests/e2e_users.py [base_url]   (needs a fresh instance)
"""

import base64
import hashlib
import hmac
import http.cookiejar
import json
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
passed = 0


def check(label, cond, detail=""):
    global passed
    assert cond, f"FAILED: {label} {detail}"
    passed += 1
    print(f"  ok  {label}")


class Client:
    """One browser: its own cookie jar (session)."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json", **(headers or {})})
        try:
            with self.op.open(req, timeout=30) as r:
                t = r.read().decode()
                return r.status, (json.loads(t) if t else None)
        except urllib.error.HTTPError as e:
            t = e.read().decode()
            try:
                return e.code, json.loads(t)
            except ValueError:
                return e.code, t

    def rest(self, method, path, body=None):
        return self.call(method, "/rest" + path, body)

    def data(self, method, path, body=None):
        st, j = self.rest(method, path, body)
        assert st < 300, f"{method} {path} -> {st}: {j}"
        return j["data"]


def totp(secret, at=None):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int((at or time.time()) // 30))
    mac = hmac.new(key, counter, hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def token_of(url):
    """The token of an app link (/app/#/signup?token=…)."""
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).fragment.split("?", 1)[1])["token"][0]


OWNER = {"email": "owner@example.com", "password": "correct horse 42", "firstName": "Olga", "lastName": "Owner"}
owner = Client()
check("fresh instance: owner setup", owner.rest("POST", "/owner/setup", OWNER)[0] == 200)

# ---------------------------------------------------------------- invitations
inv = owner.data("POST", "/invitations", [{"email": "Mia@Example.com", "role": "global:member"}, {"email": "ada@example.com", "role": "global:admin"},
                                          {"email": "no-at-sign", "role": "global:member"}])
check("invitations return accept links; mails are not sent without SMTP", len(inv) == 3 and inv[0]["user"]["email"] == "mia@example.com"
      and "/app/#/signup?token=" in inv[0]["user"]["inviteAcceptUrl"] and inv[0]["user"]["emailSent"] is False, str(inv))
check("an invalid address is reported per entry", "error" in inv[2] and "Invalid email" in inv[2]["error"], str(inv[2]))
dup = owner.data("POST", "/invitations", [{"email": "mia@example.com"}])
check("inviting an existing address is refused", "already exists" in dup[0].get("error", ""), str(dup))
st, j = owner.rest("POST", "/invitations", [{"email": "x@example.com", "role": "global:owner"}])
check("the owner role cannot be given", st == 400, str(j))
users = {u["email"]: u for u in owner.data("GET", "/users")}
mia_id, ada_id = users["mia@example.com"]["id"], users["ada@example.com"]["id"]
check("invited users are listed as pending with their role", users["mia@example.com"]["isPending"] is True and users["ada@example.com"]["role"] == "admin"
      and users["owner@example.com"]["isPending"] is False)
st, _ = Client().rest("POST", "/login", {"email": "mia@example.com", "password": ""})
check("a pending user cannot log in", st == 401)
mia_token = token_of(inv[0]["user"]["inviteAcceptUrl"])
info = Client().data("GET", f"/resolve-signup-token?token={mia_token}")
check("the signup page learns who invited whom", info["email"] == "mia@example.com" and info["inviter"]["firstName"] == "Olga", str(info))
st, _ = Client().rest("GET", "/resolve-signup-token?token=wrong")
check("an unknown invitation token is refused", st == 400)
mia = Client()
st, j = mia.rest("POST", "/invitations/accept", {"token": mia_token, "firstName": "Mia", "lastName": "Member", "password": "short"})
check("accepting needs a strong password", st == 400 and "8 characters" in j["message"], str(j))
st, j = mia.rest("POST", f"/invitations/{mia_id}/accept", {"token": mia_token, "firstName": "Mia", "lastName": "Member", "password": "mia password 1"})
check("accepting the invitation signs the user in", st == 200 and mia.data("GET", "/login")["email"] == "mia@example.com", str(j))
st, _ = Client().rest("POST", "/invitations/accept", {"token": mia_token, "password": "another pass 1"})
check("an invitation link works only once", st == 400)
st, _ = owner.rest("POST", f"/users/{mia_id}/reinvite")
check("accepted users cannot be re-invited", st == 400)
re = owner.data("POST", f"/users/{ada_id}/reinvite")
ada = Client()
st, _ = ada.rest("POST", "/invitations/accept", {"token": token_of(inv[1]["user"]["inviteAcceptUrl"]), "password": "ada password 1"})
check("re-inviting replaces the old link", st == 400)
st, _ = ada.rest("POST", "/invitations/accept", {"token": token_of(re["inviteAcceptUrl"]), "firstName": "Ada", "password": "ada password 1"})
check("the new link works", st == 200 and ada.data("GET", "/login")["role"] == "admin")

# ---------------------------------------------------------------- what each role may do
st_list, _ = mia.rest("GET", "/users")
forbidden = [mia.rest("POST", "/invitations", [{"email": "z@example.com"}])[0], mia.rest("PATCH", f"/users/{ada_id}/role", {"newRoleName": "global:member"})[0],
             mia.rest("DELETE", f"/users/{ada_id}")[0], mia.rest("GET", f"/users/{ada_id}/password-reset-link")[0],
             mia.rest("POST", "/users", {"email": "z@example.com", "password": "zzzzzzzz1"})[0], mia.rest("POST", f"/users/{ada_id}/reinvite")[0]]
check("members can list users (for sharing) but manage none", st_list == 200 and forbidden == [403] * 6, str(forbidden))
owner_id = users["owner@example.com"]["id"]
check("admins cannot change the owner's role", ada.rest("PATCH", f"/users/{owner_id}/role", {"newRoleName": "global:member"})[0] == 403)
check("nobody changes their own role", ada.rest("PATCH", f"/users/{ada_id}/role", {"newRoleName": "global:member"})[0] == 400)
check("admins cannot delete the owner", ada.rest("DELETE", f"/users/{owner_id}")[0] == 403)
check("nobody deletes themselves", owner.rest("DELETE", f"/users/{owner_id}")[0] == 400)
ada.data("PATCH", f"/users/{mia_id}/role", {"newRoleName": "global:admin"})
check("an admin can promote a member; it takes effect at once", mia.data("GET", "/login")["role"] == "admin" and mia.rest("GET", f"/users/{ada_id}/password-reset-link")[0] == 200)
owner.data("PATCH", f"/users/{mia_id}/role", {"newRoleName": "member"})
check("and demote again", mia.data("GET", "/login")["role"] == "member" and mia.rest("POST", "/invitations", [{"email": "q@example.com"}])[0] == 403)

# ---------------------------------------------------------------- password reset
st, j = Client().rest("POST", "/forgot-password", {"email": "nobody@example.com"})
st2, j2 = Client().rest("POST", "/forgot-password", {"email": "mia@example.com"})
check("forgot-password answers the same for known and unknown addresses", st == st2 == 200 and j == j2)
check("admins cannot reset the owner's password", ada.rest("GET", f"/users/{owner_id}/password-reset-link")[0] == 403)
link = ada.data("GET", f"/users/{mia_id}/password-reset-link")["link"]
rt = token_of(link)
check("reset links point to the change-password page", "/app/#/change-password?token=" in link and Client().data("GET", f"/resolve-password-token?token={rt}") == {"mfaEnabled": False})
check("weak new passwords are refused", Client().rest("POST", "/change-password", {"token": rt, "password": "123"})[0] == 400)
check("changing the password works", Client().rest("POST", "/change-password", {"token": rt, "password": "mia new pass 2"})[0] == 200)
check("it signs the user out everywhere", mia.rest("GET", "/login")[0] == 401)
check("the reset link works only once", Client().rest("POST", "/change-password", {"token": rt, "password": "mia new pass 3"})[0] == 400)
check("the old password no longer works", Client().rest("POST", "/login", {"email": "mia@example.com", "password": "mia password 1"})[0] == 401)
mia = Client()
check("the new password does", mia.rest("POST", "/login", {"email": "mia@example.com", "password": "mia new pass 2"})[0] == 200)
mia2 = Client()
mia2.rest("POST", "/login", {"email": "mia@example.com", "password": "mia new pass 2"})
check("changing the own password needs the current one", mia.rest("PATCH", "/me", {"currentPassword": "wrong", "newPassword": "mia new pass 3"})[0] == 400)
mia.data("PATCH", "/me", {"currentPassword": "mia new pass 2", "newPassword": "mia new pass 3", "firstName": "Mia"})
check("changing the own password keeps this session and ends the others", mia.rest("GET", "/login")[0] == 200 and mia2.rest("GET", "/login")[0] == 401)

# ---------------------------------------------------------------- two-factor authentication
qr = mia.data("GET", "/mfa/qr")
check("2FA setup gives a secret, an otpauth link, a QR code and 10 recovery codes", qr["qrCode"].startswith("otpauth://totp/Flowwolf:mia%40example.com?secret=")
      and "<svg" in qr["qrSvg"] and len(qr["recoveryCodes"]) == 10, str({k: v for k, v in qr.items() if k != "qrSvg"}))
check("a wrong code does not enable 2FA", mia.rest("POST", "/mfa/enable", {"mfaCode": "000000" if totp(qr["secret"]) != "000000" else "111111"})[0] == 400)
mia.data("POST", "/mfa/enable", {"mfaCode": totp(qr["secret"])})
check("users list shows 2FA", {u["email"]: u for u in owner.data("GET", "/users")}["mia@example.com"]["mfaEnabled"] is True)
login = {"email": "mia@example.com", "password": "mia new pass 3"}
st, j = Client().rest("POST", "/login", login)
check("with 2FA the password alone is not enough (n8n's MFA error)", st == 401 and j.get("code") == 998, str(j))
check("a wrong code is refused", Client().rest("POST", "/login", {**login, "mfaCode": "123456" if totp(qr["secret"]) != "123456" else "654321"})[0] == 401)
check("the right code signs in", Client().rest("POST", "/login", {**login, "mfaCode": totp(qr["secret"])})[0] == 200)
rc = qr["recoveryCodes"][0]
check("a recovery code signs in", Client().rest("POST", "/login", {**login, "mfaRecoveryCode": rc.lower()})[0] == 200)
check("… but only once", Client().rest("POST", "/login", {**login, "mfaRecoveryCode": rc})[0] == 401)
check("a wrong password stays wrong even with the right code", Client().rest("POST", "/login", {**login, "password": "nope nope", "mfaCode": totp(qr["secret"])})[0] == 401)
rt = token_of(owner.data("GET", f"/users/{mia_id}/password-reset-link")["link"])
check("password reset asks for the 2FA code", Client().data("GET", f"/resolve-password-token?token={rt}") == {"mfaEnabled": True}
      and Client().rest("POST", "/change-password", {"token": rt, "password": "mia new pass 4"})[0] == 400)
check("… and works with it", Client().rest("POST", "/change-password", {"token": rt, "password": "mia new pass 4", "mfaCode": totp(qr["secret"])})[0] == 200)
mia = Client()
mia.rest("POST", "/login", {"email": "mia@example.com", "password": "mia new pass 4", "mfaCode": totp(qr["secret"])})
check("turning 2FA off needs a code", mia.rest("POST", "/mfa/disable", {})[0] == 400)
mia.data("POST", "/mfa/disable", {"mfaRecoveryCode": qr["recoveryCodes"][1]})
check("after turning it off the password is enough again", Client().rest("POST", "/login", {"email": "mia@example.com", "password": "mia new pass 4"})[0] == 200)

# ---------------------------------------------------------------- deleting users
WF = {"name": "Mia's flow", "nodes": [{"name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}}], "connections": {}}
mia_wf = mia.data("POST", "/workflows", WF)["id"]
mia_cred = mia.data("POST", "/credentials", {"name": "Mia's token", "type": "httpHeaderAuth", "data": {"name": "x", "value": "secret"}})["id"]
key = mia.data("POST", "/api-keys", {"label": "ci"})["apiKey"]
st, _ = mia.call("GET", "/api/v1/workflows", headers={"X-N8N-API-KEY": key})
check("the member's API key works before the deletion", st == 200)
check("data can only go to an active user", owner.rest("DELETE", f"/users/{mia_id}?transferId={mia_id}")[0] == 400)
owner.data("DELETE", f"/users/{mia_id}?transferId={ada_id}")
check("deleting with a transfer hands workflows and credentials over", ada.data("GET", f"/workflows/{mia_wf}/share")["ownerId"] == ada_id
      and ada.data("GET", f"/credentials/{mia_cred}/share")["ownerId"] == ada_id)
check("the deleted user's session and API key stop working", mia.rest("GET", "/login")[0] == 401
      and mia.call("GET", "/api/v1/workflows", headers={"X-N8N-API-KEY": key})[0] == 401)
check("the deleted user is gone from the list", "mia@example.com" not in [u["email"] for u in owner.data("GET", "/users")])
tom_inv = owner.data("POST", "/invitations", [{"email": "tom@example.com"}])[0]["user"]
tom = Client()
tom.rest("POST", "/invitations/accept", {"token": token_of(tom_inv["inviteAcceptUrl"]), "password": "tom password 1"})
tom_wf = tom.data("POST", "/workflows", WF)["id"]
tom_cred = tom.data("POST", "/credentials", {"name": "Tom's token", "type": "httpHeaderAuth", "data": {"name": "x", "value": "y"}})["id"]
owner.data("DELETE", f"/users/{tom_inv['id']}")
check("deleting without a transfer removes the user's workflows and credentials", owner.rest("GET", f"/workflows/{tom_wf}")[0] == 404
      and owner.rest("GET", f"/credentials/{tom_cred}")[0] == 404)
pend = owner.data("POST", "/invitations", [{"email": "pending@example.com"}])[0]["user"]
check("pending invitations can be withdrawn by deleting the user", owner.rest("DELETE", f"/users/{pend['id']}")[0] == 200
      and Client().rest("GET", f"/resolve-signup-token?token={token_of(pend['inviteAcceptUrl'])}")[0] == 400)

print(f"\nall {passed} user management checks passed")
