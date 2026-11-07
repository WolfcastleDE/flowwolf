"""Triggers that register their webhook at the service: create on activation, signed events start the
workflow (unsigned or forged ones are rejected), ping/handshake/HEAD checks, re-registration on change,
delete on deactivation, failed registration keeps the workflow inactive, and a create/delete contract
check for every one of the 30 webhook triggers.

The services are Flowwolf webhook workflows; fixed hosts are redirected with FLOWWOLF_API_BASE_OVERRIDES:
  docker run -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_hooktriggers.py --overrides)" ...
Usage: python tests/e2e_hooktriggers.py [base_url]
"""

import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

M = "http://127.0.0.1:5678/webhook/svc"
OVERRIDES = {
    "https://api.stripe.com/v1": "stripe", "https://api.calendly.com": "cal", "https://app.asana.com/api/1.0": "asana", "https://api.trello.com/1": "trello",
    "https://acme.myshopify.com/admin/api/2024-10": "shop", "https://api.typeform.com": "tf2", "https://api.clickup.com/api/v2": "cu", "https://api.linear.app": "lin",
    "https://us1.api.mailchimp.com/3.0": "mc2", "https://api.pipedrive.com/v1": "pd2", "https://api.webflow.com/v2": "wfl", "https://api.bitbucket.org/2.0": "bb2",
    "https://api.netlify.com/api/v1": "nl", "https://api.box.com/2.0": "box", "https://www.formstack.com/api/v2": "fst", "https://api.surveymonkey.com/v3": "smk",
    "https://api.postmarkapp.com": "pm", "https://connect.mailerlite.com/api": "ml", "https://api.getresponse.com/v3": "gr2", "https://api.lemlist.com/api": "lm2",
    "https://api.gumroad.com/v2": "gum", "https://api.figma.com/v2": "fig",
    "https://webexapis.com/v1": "wx2", "https://acme.zendesk.com/api/v2": "zd2", "https://api.copper.com/developer_api/v1": "cp2", "https://api.affinity.co": "af2",
    "https://www.eventbriteapi.com/v3": "eb", "https://acme.workable.com/spi/v3": "wk", "https://acuityscheduling.com/api/v1": "acu", "https://api.brevo.com/v3": "brv",
    "https://api.mailjet.com/v3/REST": "mjt", "https://api.helpscout.net/v2": "hsc", "https://api.infusionsoft.com/crm/rest/v1": "kp2", "https://onfleet.com/api/v2": "onf",
    "https://paypal.mock": "pp3",
}
if "--overrides" in sys.argv:
    print(",".join(f"{a}={M}/{b}" for a, b in OVERRIDES.items()))
    sys.exit()

import e2e_lib as L  # noqa: E402

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
rest, node, chain, out, check = L.rest, L.node, L.chain, L.out, L.check
L.login_or_setup()


services = {}


def service(method, path, response):
    """A fake service endpoint: answers with `response` and records every call as an execution.
    A later fake on the same endpoint replaces the earlier one."""
    if (method, path) in services:
        rest("DELETE", f"/workflows/{services[(method, path)]}")
    wf = rest("POST", "/workflows", {"name": f"svc {method} {path}", "nodes": [
        node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": method, "path": f"svc/{path}", "responseMode": "responseNode"}, 2),
        node("Reply", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": json.dumps(response)}, 1.1)], "connections": chain("Hook", "Reply")})
    rest("POST", f"/workflows/{wf['id']}/activate")
    services[(method, path)] = wf["id"]
    return wf["id"]


def calls(wf_id, n=1):
    for _ in range(40):
        ex = rest("GET", f"/executions?workflowId={wf_id}")
        if len(ex) >= n:
            return [out(L.wait_exec(e["id"]), "Hook")[0] for e in reversed(ex)]
        time.sleep(0.25)
    return [out(L.wait_exec(e["id"]), "Hook")[0] for e in reversed(rest("GET", f"/executions?workflowId={wf_id}"))]


def cred(kind, data):
    return {kind: {"id": rest("POST", "/credentials", {"name": kind, "type": kind, "data": data})["id"], "name": kind}}


def trigger_wf(name, kind, params, creds):
    wf = rest("POST", "/workflows", {"name": name, "nodes": [node("T", kind, 0, params, credentials=creds), node("Seen", "n8n-nodes-base.noOp", 200)],
                                     "connections": chain("T", "Seen")})
    hook = [n for n in rest("GET", f"/workflows/{wf['id']}")["nodes"] if n["name"] == "T"][0]["webhookId"]
    return wf["id"], hook


def secret_of(wid):
    return (rest("GET", f"/workflows/{wid}")["staticData"] or {}).get("node:T", {}).get("secret")


def deliver(hook, raw=None, headers=None, method="POST"):
    req = urllib.request.Request(f"{L.BASE}/webhook/{hook}", data=raw, method=method, headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


def runs(wf_id, n, timeout=10):
    for _ in range(timeout * 4):
        ex = rest("GET", f"/executions?workflowId={wf_id}")
        if len(ex) >= n:
            return [L.wait_exec(e["id"]) for e in ex]
        time.sleep(0.25)
    return [L.wait_exec(e["id"]) for e in rest("GET", f"/executions?workflowId={wf_id}")]


def hmac_hex(secret, raw):
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- GitHub: full life cycle with signatures
gh_create = service("POST", "gh/repos/acme/app/hooks", {"id": 77})
gh_delete = service("DELETE", "gh/repos/acme/app/hooks/77", {})
gh_create2 = service("POST", "gh/repos/acme/app2/hooks", {"id": 78})
gh = cred("githubApi", {"server": f"{M}/gh", "accessToken": "ght"})
wid, hook = trigger_wf("gh", "n8n-nodes-base.githubTrigger", {"owner": "acme", "repository": "app", "events": ["push", "pull_request"]}, gh)
rest("POST", f"/workflows/{wid}/activate")
c = calls(gh_create)
secret = secret_of(wid)
check("activation registers the webhook at GitHub with a fresh secret", c and secret and len(secret) >= 32 and c[0]["body"] == {
    "name": "web", "active": True, "events": ["push", "pull_request"],
    "config": {"url": f"http://localhost:5678/webhook/{hook}", "content_type": "json", "insecure_ssl": "0", "secret": secret}}
      and c[0]["headers"]["authorization"] == "Bearer ght", f"{c} {secret}")
check("the returned hook id is kept in the node's static data", rest("GET", f"/workflows/{wid}")["staticData"]["node:T"]["webhookId"] == "77")
deliver(hook, b'{"zen": "hi"}', {"x-github-event": "ping"})
raw = json.dumps({"ref": "refs/heads/main", "commits": [{"id": "abc"}]}).encode()
st_unsigned, _ = deliver(hook, raw, {"x-github-event": "push"})
st_forged, _ = deliver(hook, raw, {"x-github-event": "push", "x-hub-signature-256": "sha256=" + hmac_hex("wrong", raw)})
time.sleep(1)
check("ping, unsigned and forged events start nothing (401 for the last two)", st_unsigned == 401 and st_forged == 401 and rest("GET", f"/executions?workflowId={wid}") == [])
st, _ = deliver(hook, raw, {"x-github-event": "push", "x-hub-signature-256": "sha256=" + hmac_hex(secret, raw)})
got = runs(wid, 1)
check("a correctly signed event starts the workflow with the event body", st == 200 and got and out(got[0], "T")[0] == {"ref": "refs/heads/main", "commits": [{"id": "abc"}]}, f"{st}")
w = rest("GET", f"/workflows/{wid}")
w["nodes"][0]["parameters"]["repository"] = "app2"
rest("PATCH", f"/workflows/{wid}", {"nodes": w["nodes"]})
check("changing the repository re-registers (old hook deleted, new one created)", len(calls(gh_delete)) == 1 and len(calls(gh_create2)) == 1
      and rest("GET", f"/workflows/{wid}")["staticData"]["node:T"]["webhookId"] == "78")
gh_delete2 = service("DELETE", "gh/repos/acme/app2/hooks/78", {})
rest("POST", f"/workflows/{wid}/deactivate")
check("deactivation deletes the webhook at GitHub", len(calls(gh_delete2)) == 1 and "node:T" not in (rest("GET", f"/workflows/{wid}")["staticData"] or {}))

# ---------------------------------------------------------------- Stripe: secret from the response, timestamped signature
st_create = service("POST", "stripe/webhook_endpoints", {"id": "we_1", "secret": "whsec_test"})
wid, hook = trigger_wf("st", "n8n-nodes-base.stripeTrigger", {"events": ["invoice.paid", "charge.failed"]}, cred("stripeApi", {"secretKey": "sk_test_1"}))
rest("POST", f"/workflows/{wid}/activate")
c = calls(st_create)
check("Stripe endpoint is created with form fields", c and c[0]["body"] == {"url": f"http://localhost:5678/webhook/{hook}", "enabled_events[0]": "invoice.paid",
                                                                          "enabled_events[1]": "charge.failed", "description": "Flowwolf"}, f"{c}")
raw = b'{"type": "invoice.paid", "data": {"object": {"id": "in_1"}}}'
t = int(time.time())
old = t - 3600
st_old, _ = deliver(hook, raw, {"stripe-signature": f"t={old},v1={hmac_hex('whsec_test', f'{old}.'.encode() + raw)}"})
st_ok, _ = deliver(hook, raw, {"stripe-signature": f"t={t},v1={hmac_hex('whsec_test', f'{t}.'.encode() + raw)}"})
check("Stripe: signature over 't.body' with the returned secret; replayed old events are refused", st_old == 401 and st_ok == 200 and len(runs(wid, 1)) == 1, f"{st_old} {st_ok}")

# ---------------------------------------------------------------- Calendly: the id is an absolute resource URL
cal_create = service("POST", "cal/webhook_subscriptions", {"resource": {"uri": f"{M}/cal/webhook_subscriptions/s1"}})
cal_delete = service("DELETE", "cal/webhook_subscriptions/s1", {})
wid, hook = trigger_wf("cal", "n8n-nodes-base.calendlyTrigger", {"organization": "https://api.calendly.com/organizations/O1", "events": ["invitee.created"]},
                       cred("calendlyApi", {"accessToken": "ct"}))
rest("POST", f"/workflows/{wid}/activate")
c = calls(cal_create)
check("Calendly subscription carries scope and signing key", c and c[0]["body"]["scope"] == "organization" and c[0]["body"]["signing_key"] == secret_of(wid), f"{c}")
rest("POST", f"/workflows/{wid}/deactivate")
check("Calendly subscription is deleted via its resource URL", len(calls(cal_delete)) == 1)

# ---------------------------------------------------------------- Asana handshake secret, Telegram token header, Trello HEAD check
service("POST", "asana/webhooks", {"data": {"gid": "9"}})
wid, hook = trigger_wf("asana", "n8n-nodes-base.asanaTrigger", {"resource": "123"}, cred("asanaApi", {"accessToken": "at"}))
rest("POST", f"/workflows/{wid}/activate")
st, hs = deliver(hook, None, {"x-hook-secret": "s3cret"})
raw = b'{"events": [{"action": "changed"}]}'
st_bad, _ = deliver(hook, raw, {"x-hook-signature": hmac_hex("other", raw)})
st_ok, _ = deliver(hook, raw, {"x-hook-signature": hmac_hex("s3cret", raw)})
check("Asana: handshake echoes X-Hook-Secret and later events are verified with it", st == 200 and hs.get("x-hook-secret") == "s3cret" and st_bad == 401 and st_ok == 200
      and len(runs(wid, 1)) == 1, f"{hs} {st_bad} {st_ok}")
service("POST", "tg/bot123abc/setWebhook", {"ok": True})
wid, hook = trigger_wf("tg", "n8n-nodes-base.telegramTrigger", {"updates": ["message"]}, cred("telegramApi", {"accessToken": "123abc", "baseUrl": f"{M}/tg"}))
rest("POST", f"/workflows/{wid}/activate")
raw = b'{"message": {"text": "hi"}}'
st_bad, _ = deliver(hook, raw, {"x-telegram-bot-api-secret-token": "nope"})
st_ok, _ = deliver(hook, raw, {"x-telegram-bot-api-secret-token": secret_of(wid)})
check("Telegram: the secret_token header must match", st_bad == 401 and st_ok == 200, f"{st_bad} {st_ok}")
service("POST", "trello/webhooks", {"id": "tw1"})
wid, hook = trigger_wf("trello", "n8n-nodes-base.trelloTrigger", {"modelId": "board1"}, cred("trelloApi", {"apiKey": "k", "apiToken": "t"}))
rest("POST", f"/workflows/{wid}/activate")
st, _ = deliver(hook, method="HEAD")
check("Trello's HEAD check on the callback URL answers 200", st == 200)

# ---------------------------------------------------------------- failing registration
wid, hook = trigger_wf("gl", "n8n-nodes-base.gitlabTrigger", {"project": "42"}, cred("gitlabApi", {"server": f"{M}/gitlab-missing", "accessToken": "x"}))
st, body = L.call("POST", f"/rest/workflows/{wid}/activate")
check("a failed registration keeps the workflow inactive with the service's error", st == 400 and "Could not create the webhook (404)" in json.dumps(body)
      and rest("GET", f"/workflows/{wid}")["active"] is False, f"{st} {body}")

# ---------------------------------------------------------------- create/delete contract for every webhook trigger
service("POST", "tok", {"access_token": "tok-1", "expires_in": 600})
OA = {"grantType": "clientCredentials", "accessTokenUrl": f"{M}/tok", "clientId": "id", "clientSecret": "sec"}
U = lambda hook: f"http://localhost:5678/webhook/{hook}"  # noqa: E731
# (kind, credential, data, params, (create method, path(hook), response), (delete method, path), check(request, hook))
TABLE = [
    ("githubTrigger", "githubApi", {"server": f"{M}/gh3", "accessToken": "a"}, {"owner": "o", "repository": "r", "events": ["push"]},
     ("POST", "gh3/repos/o/r/hooks", {"id": 1}), ("DELETE", "gh3/repos/o/r/hooks/1"), lambda r, h: r["body"]["config"]["url"] == U(h)),
    ("gitlabTrigger", "gitlabApi", {"server": f"{M}/gl", "accessToken": "a"}, {"project": "42", "pushEvents": True},
     ("POST", "gl/api/v4/projects/42/hooks", {"id": 5}), ("DELETE", "gl/api/v4/projects/42/hooks/5"),
     lambda r, h: r["body"]["push_events"] is True and r["body"]["url"] == U(h) and r["headers"]["private-token"] == "a" and len(r["body"]["token"]) >= 32),
    ("stripeTrigger", "stripeApi", {"secretKey": "sk"}, {"events": ["charge.failed"]},
     ("POST", "stripe/webhook_endpoints", {"id": "we_9", "secret": "whsec_9"}), ("DELETE", "stripe/webhook_endpoints/we_9"), lambda r, h: r["body"]["enabled_events[0]"] == "charge.failed"),
    ("shopifyTrigger", "shopifyAccessTokenApi", {"shopSubdomain": "acme", "accessToken": "shp"}, {"topic": "orders/paid"},
     ("POST", "shop/webhooks.json", {"webhook": {"id": 901}}), ("DELETE", "shop/webhooks/901.json"),
     lambda r, h: r["body"] == {"webhook": {"topic": "orders/paid", "address": U(h), "format": "json"}} and r["headers"]["x-shopify-access-token"] == "shp"),
    ("typeformTrigger", "typeformApi", {"accessToken": "tft"}, {"formId": "F1"},
     ("PUT", lambda h: f"tf2/forms/F1/webhooks/{h}", {}), ("DELETE", lambda h: f"tf2/forms/F1/webhooks/{h}"), lambda r, h: r["body"]["url"] == U(h) and r["body"]["enabled"] is True),
    ("calendlyTrigger", "calendlyApi", {"accessToken": "c"}, {"organization": "https://api.calendly.com/organizations/O2", "events": ["invitee.canceled"]},
     ("POST", "cal/webhook_subscriptions", {"resource": {"uri": f"{M}/cal/webhook_subscriptions/s2"}}), ("DELETE", "cal/webhook_subscriptions/s2"),
     lambda r, h: r["body"]["events"] == ["invitee.canceled"]),
    ("telegramTrigger", "telegramApi", {"accessToken": "999xyz", "baseUrl": f"{M}/tg"}, {"updates": ["message", "callback_query"]},
     ("POST", "tg/bot999xyz/setWebhook", {"ok": True}), ("POST", "tg/bot999xyz/deleteWebhook"), lambda r, h: r["body"]["allowed_updates"] == ["message", "callback_query"]),
    ("trelloTrigger", "trelloApi", {"apiKey": "k", "apiToken": "t"}, {"modelId": "b9"},
     ("POST", "trello/webhooks", {"id": "tw9"}), ("DELETE", "trello/webhooks/tw9"), lambda r, h: r["body"]["callbackURL"] == U(h) and r["query"] == {"key": "k", "token": "t"}),
    ("jiraTrigger", "jiraSoftwareCloudApi", {"domain": f"{M}/jira", "email": "e@x.io", "apiToken": "jt"}, {"events": ["jira:issue_created"], "jql": "project = OPS"},
     ("POST", "jira/rest/webhooks/1.0/webhook", {"self": f"{M}/jira/rest/webhooks/1.0/webhook/12"}), ("DELETE", "jira/rest/webhooks/1.0/webhook/12"),
     lambda r, h: r["body"]["filters"] == {"issue-related-events-section": "project = OPS"}),
    ("asanaTrigger", "asanaApi", {"accessToken": "a"}, {"resource": "p1"},
     ("POST", "asana/webhooks", {"data": {"gid": "9"}}), ("DELETE", "asana/webhooks/9"), lambda r, h: r["body"] == {"data": {"resource": "p1", "target": U(h)}}),
    ("clickUpTrigger", "clickUpApi", {"accessToken": "pk_1"}, {"team": "T1", "events": ["taskCreated"]},
     ("POST", "cu/team/T1/webhook", {"id": "w1", "webhook": {"secret": "cs"}}), ("DELETE", "cu/webhook/w1"), lambda r, h: r["body"] == {"endpoint": U(h), "events": ["taskCreated"]}),
    ("linearTrigger", "linearApi", {"apiKey": "lin_1"}, {"teamId": "TM", "resources": ["Issue"]},
     ("POST", "lin/graphql", {"data": {"webhookCreate": {"webhook": {"id": "L1"}}}}), ("POST", "lin/graphql"),
     lambda r, h: r["body"]["variables"]["input"]["url"] == U(h) and r["body"]["query"].startswith("mutation")),
    ("mailchimpTrigger", "mailchimpApi", {"server": "us1", "apiKey": "mk"}, {"list": "L9"},
     ("POST", "mc2/lists/L9/webhooks", {"id": "mw"}), ("DELETE", "mc2/lists/L9/webhooks/mw"), lambda r, h: r["body"]["events"]["subscribe"] is True and r["body"]["url"] == U(h)),
    ("pipedriveTrigger", "pipedriveApi", {"apiToken": "pt"}, {"action": "added", "object": "deal"},
     ("POST", "pd2/webhooks", {"data": {"id": 31}}), ("DELETE", "pd2/webhooks/31"), lambda r, h: r["body"] == {"subscription_url": U(h), "event_action": "added", "event_object": "deal"}),
    ("webflowTrigger", "webflowApi", {"accessToken": "w"}, {"siteId": "S1", "triggerType": "form_submission"},
     ("POST", "wfl/sites/S1/webhooks", {"id": "wh1"}), ("DELETE", "wfl/webhooks/wh1"), lambda r, h: r["body"] == {"triggerType": "form_submission", "url": U(h)}),
    ("wooCommerceTrigger", "wooCommerceApi", {"url": f"{M}/woo", "consumerKey": "ck", "consumerSecret": "cs"}, {"event": "order.created"},
     ("POST", "woo/wp-json/wc/v3/webhooks", {"id": 44}), ("DELETE", "woo/wp-json/wc/v3/webhooks/44"), lambda r, h: r["body"]["topic"] == "order.created" and len(r["body"]["secret"]) >= 32),
    ("bitbucketTrigger", "bitbucketApi", {"username": "u", "appPassword": "p"}, {"workspace": "ws", "repository": "repo", "events": ["repo:push"]},
     ("POST", "bb2/repositories/ws/repo/hooks", {"uuid": "abc-1"}), ("DELETE", "bb2/repositories/ws/repo/hooks/abc-1"), lambda r, h: r["body"]["events"] == ["repo:push"]),
    ("netlifyTrigger", "netlifyApi", {"accessToken": "n"}, {"siteId": "S9", "event": "deploy_failed"},
     ("POST", "nl/hooks", {"id": "n1"}), ("DELETE", "nl/hooks/n1"), lambda r, h: r["query"] == {"site_id": "S9"} and r["body"]["data"] == {"url": U(h)}),
    ("boxTrigger", "boxOAuth2Api", OA, {"targetId": "0", "targetType": "folder", "events": ["FILE.UPLOADED"]},
     ("POST", "box/webhooks", {"id": "b1"}), ("DELETE", "box/webhooks/b1"), lambda r, h: r["headers"]["authorization"] == "Bearer tok-1" and r["body"]["triggers"] == ["FILE.UPLOADED"]),
    ("formstackTrigger", "formstackApi", {"accessToken": "f"}, {"formId": "F2"},
     ("POST", "fst/form/F2/webhook.json", {"id": "f1"}), ("DELETE", "fst/webhook/f1.json"), lambda r, h: r["body"] == {"url": U(h), "content_type": "json"}),
    ("surveyMonkeyTrigger", "surveyMonkeyApi", {"accessToken": "s"}, {"surveyId": "SV1"},
     ("POST", "smk/webhooks", {"id": "s1"}), ("DELETE", "smk/webhooks/s1"), lambda r, h: r["body"]["object_ids"] == ["SV1"] and r["body"]["subscription_url"] == U(h)),
    ("postmarkTrigger", "postmarkApi", {"serverToken": "pmt"}, {},
     ("POST", "pm/webhooks", {"ID": 7}), ("DELETE", "pm/webhooks/7"), lambda r, h: r["body"]["Triggers"]["Open"] == {"Enabled": True} and r["body"]["MessageStream"] == "outbound"),
    ("mailerLiteTrigger", "mailerLiteApi", {"apiKey": "m"}, {"events": ["subscriber.created"]},
     ("POST", "ml/webhooks", {"data": {"id": "m1", "secret": "mls"}}), ("DELETE", "ml/webhooks/m1"), lambda r, h: r["body"]["url"] == U(h)),
    ("activeCampaignTrigger", "activeCampaignApi", {"apiUrl": f"{M}/ac", "apiKey": "ak"}, {"events": ["subscribe"]},
     ("POST", "ac/api/3/webhooks", {"webhook": {"id": "3"}}), ("DELETE", "ac/api/3/webhooks/3"), lambda r, h: r["body"]["webhook"]["sources"] == ["public", "admin", "api", "system"]),
    ("getResponseTrigger", "getResponseApi", {"apiKey": "g"}, {},
     ("POST", "gr2/accounts/callbacks", {}), ("DELETE", "gr2/accounts/callbacks"), lambda r, h: r["body"]["actions"]["subscribe"] is True),
    ("lemlistTrigger", "lemlistApi", {"apiKey": "l"}, {"event": "emailsReplied"},
     ("POST", "lm2/hooks", {"_id": "h1"}), ("DELETE", "lm2/hooks/h1"), lambda r, h: r["body"] == {"targetUrl": U(h), "type": "emailsReplied"}),
    ("mauticTrigger", "mauticApi", {"url": f"{M}/mau", "username": "u", "password": "p"}, {"events": ["mautic.form_on_submit"]},
     ("POST", "mau/api/hooks/new", {"hook": {"id": 5}}), ("DELETE", "mau/api/hooks/5/delete"), lambda r, h: r["body"]["triggers"] == ["mautic.form_on_submit"]),
    ("calTrigger", "calApi", {"host": f"{M}/calc", "apiKey": "cal_1"}, {"events": ["BOOKING_CREATED"]},
     ("POST", "calc/v1/hooks", {"webhook": {"id": "c1"}}), ("DELETE", "calc/v1/hooks/c1"), lambda r, h: r["query"] == {"apiKey": "cal_1"} and r["body"]["subscriberUrl"] == U(h)),
    ("gumroadTrigger", "gumroadApi", {"accessToken": "gt"}, {"resource": "sale"},
     ("PUT", "gum/resource_subscriptions", {"resource_subscription": {"id": "g1"}}), ("DELETE", "gum/resource_subscriptions/g1"),
     lambda r, h: r["body"] == {"access_token": "gt", "resource_name": "sale", "post_url": U(h)}),
    ("figmaTrigger", "figmaApi", {"accessToken": "fgt"}, {"teamId": "TM1", "event": "FILE_UPDATE"},
     ("POST", "fig/webhooks", {"id": "fg1"}), ("DELETE", "fig/webhooks/fg1"), lambda r, h: r["body"]["passcode"] == h and r["headers"]["x-figma-token"] == "fgt"),
]
TABLE += [
    ("keapTrigger", "keapOAuth2Api", OA, {"eventKey": "order.add"},
     ("POST", "kp2/hooks", {"key": "k77"}), ("DELETE", "kp2/hooks/k77"), lambda r, h: r["body"] == {"eventKey": "order.add", "hookUrl": U(h)} and r["headers"]["authorization"] == "Bearer tok-1"),
    ("koBoToolboxTrigger", "koBoToolboxApi", {"URL": f"{M}/kobo", "token": "kt"}, {"formId": "aF1"},
     ("POST", "kobo/api/v2/assets/aF1/hooks", {"uid": "hk1"}), ("DELETE", "kobo/api/v2/assets/aF1/hooks/hk1"),
     lambda r, h: r["headers"]["authorization"] == "Token kt" and r["body"]["endpoint"] == U(h) and r["body"]["export_type"] == "json"),
    ("onfleetTrigger", "onfleetApi", {"apiKey": "of"}, {"trigger": 6},
     ("POST", "onf/webhooks", {"id": "w9"}), ("DELETE", "onf/webhooks/w9"), lambda r, h: r["body"] == {"url": U(h), "trigger": 6, "name": "Flowwolf"}),
    ("payPalTrigger", "payPalApi", dict(OA, apiHost="paypal.mock"), {"event": "CHECKOUT.ORDER.APPROVED"},
     ("POST", "pp3/v1/notifications/webhooks", {"id": "WH-1"}), ("DELETE", "pp3/v1/notifications/webhooks/WH-1"),
     lambda r, h: r["body"] == {"url": U(h), "event_types": [{"name": "CHECKOUT.ORDER.APPROVED"}]}),
    ("ciscoWebexTrigger", "ciscoWebexApi", {"accessToken": "wx"}, {"resource": "messages", "event": "created"},
     ("POST", "wx2/webhooks", {"id": "Y2"}), ("DELETE", "wx2/webhooks/Y2"), lambda r, h: r["body"]["targetUrl"] == U(h) and r["body"]["resource"] == "messages"),
    ("zendeskTrigger", "zendeskApi", {"subdomain": "acme", "email": "a@b.c", "apiToken": "zt"}, {},
     ("POST", "zd2/webhooks", {"webhook": {"id": "01H"}}), ("DELETE", "zd2/webhooks/01H"),
     lambda r, h: r["body"]["webhook"]["subscriptions"] == ["zen:event-type:user.created"] and r["body"]["webhook"]["endpoint"] == U(h)),
    ("copperTrigger", "copperApi", {"email": "me@x.io", "apiKey": "ck"}, {"resource": "lead", "event": "update"},
     ("POST", "cp2/webhooks", {"id": 55}), ("DELETE", "cp2/webhooks/55"), lambda r, h: r["body"] == {"target": U(h), "type": "lead", "event": "update"}),
    ("affinityTrigger", "affinityApi", {"apiKey": "ak"}, {"events": ["person.created", "opportunity.created"]},
     ("POST", "af2/webhook/subscribe", {"id": 4}), ("DELETE", "af2/webhook/4"), lambda r, h: r["body"]["subscriptions"] == ["person.created", "opportunity.created"]),
    ("eventbriteTrigger", "eventbriteApi", {"apiKey": "eb"}, {"organization": "O9"},
     ("POST", "eb/organizations/O9/webhooks", {"id": "e1"}), ("DELETE", "eb/webhooks/e1"), lambda r, h: r["body"] == {"endpoint_url": U(h), "actions": "order.placed,attendee.updated"}),
    ("workableTrigger", "workableApi", {"subdomain": "acme", "accessToken": "wt"}, {"event": "candidate_moved"},
     ("POST", "wk/subscriptions", {"id": 12}), ("DELETE", "wk/subscriptions/12"), lambda r, h: r["body"] == {"target": U(h), "event": "candidate_moved"}),
    ("acuitySchedulingTrigger", "acuitySchedulingApi", {"userId": "u1", "apiKey": "k1"}, {},
     ("POST", "acu/webhooks", {"id": 8}), ("DELETE", "acu/webhooks/8"), lambda r, h: r["body"] == {"target": U(h), "event": "appointment.scheduled"}),
    ("sendInBlueTrigger", "sendInBlueApi", {"apiKey": "bk"}, {"events": ["opened", "click"]},
     ("POST", "brv/webhooks", {"id": 21}), ("DELETE", "brv/webhooks/21"),
     lambda r, h: r["body"] == {"url": U(h), "events": ["opened", "click"], "type": "transactional", "description": "Flowwolf"} and r["headers"]["api-key"] == "bk"),
    ("mailjetTrigger", "mailjetEmailApi", {"apiKey": "k", "secretKey": "s"}, {"event": "bounce"},
     ("POST", "mjt/eventcallbackurl", {"Data": [{"ID": 3141}]}), ("DELETE", "mjt/eventcallbackurl/3141"), lambda r, h: r["body"] == {"EventType": "bounce", "Url": U(h), "Version": 2}),
    ("helpScoutTrigger", "helpScoutOAuth2Api", OA, {"events": ["convo.created"]},
     ("POST", "hsc/webhooks", {"id": 61}), ("DELETE", "hsc/webhooks/61"), lambda r, h: r["body"]["events"] == ["convo.created"] and len(r["body"]["secret"]) >= 32),
]
for kind, cred_kind, data, params, (cm, cpath, resp), (dm, dpath), ok in TABLE:
    wid, hook = trigger_wf(kind, f"n8n-nodes-base.{kind}", params, cred(cred_kind, data))
    cp = cpath(hook) if callable(cpath) else cpath
    dp = dpath(hook) if callable(dpath) else dpath
    create = service(cm, cp, resp)
    same_endpoint = (cm, cp) == (dm, dp)
    delete = create if same_endpoint else service(dm, dp, {})
    st, body = L.call("POST", f"/rest/workflows/{wid}/activate")
    req = calls(create)
    good = False
    try:
        good = st == 200 and req and ok(req[0], hook)
    except (KeyError, TypeError, IndexError):
        pass
    rest("POST", f"/workflows/{wid}/deactivate")
    deleted = len(calls(delete, 2 if same_endpoint else 1)) == (2 if same_endpoint else 1)
    check(f"{kind}: registers and deletes its webhook", good and deleted, f"{st} {body} {req} deleted={deleted}")

# ---------------------------------------------------------------- Onfleet URL check, SHA-1 signatures (Help Scout)
service("POST", "onf/webhooks", {"id": "w10"})
wid, hook = trigger_wf("onfleet-check", "n8n-nodes-base.onfleetTrigger", {"trigger": 3}, cred("onfleetApi", {"apiKey": "of2"}))
rest("POST", f"/workflows/{wid}/activate")
with urllib.request.urlopen(f"{L.BASE}/webhook/{hook}?check=a1b2c3", timeout=30) as r:
    body = r.read().decode()
check("Onfleet: the URL check gets its value back as plain text", body == "a1b2c3", body)
service("POST", "hsc/webhooks", {"id": 62})
wid, hook = trigger_wf("hs-sig", "n8n-nodes-base.helpScoutTrigger", {"events": ["convo.created"]}, cred("helpScoutOAuth2Api", OA))
rest("POST", f"/workflows/{wid}/activate")
secret = secret_of(wid)
raw = b'{"id": 1, "subject": "Help"}'
good = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha1).digest()).decode()
bad = base64.b64encode(hmac.new(b"x", raw, hashlib.sha1).digest()).decode()
st_bad, _ = deliver(hook, raw, {"x-helpscout-signature": bad})
st_ok, _ = deliver(hook, raw, {"x-helpscout-signature": good})
check("Help Scout: HMAC-SHA1 signature with the registered secret", st_bad == 401 and st_ok == 200 and len(runs(wid, 1)) == 1, f"{st_bad} {st_ok}")

print(f"\nall {L.passed} webhook-trigger checks passed")
