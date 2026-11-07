"""Contract checks for declarative integrations: every request goes to a Flowwolf echo mock and the
method, path, query, auth header and body are compared with what the real API expects.

Fixed API hosts are redirected with FLOWWOLF_API_BASE_OVERRIDES (see OVERRIDES below), so start the
instance with:  docker run -e FLOWWOLF_API_BASE_OVERRIDES="$(python tests/e2e_contracts.py --overrides)" ...
Usage: python tests/e2e_contracts.py [base_url]
"""

import base64
import json
import sys

M = "http://127.0.0.1:5678/webhook/c"
OVERRIDES = {
    "https://acme.service-now.com/api/now": f"{M}/snow",
    "https://acme.zendesk.com/api/v2": f"{M}/zd",
    "https://api.mailjet.com/v3.1": f"{M}/mj",
    "https://api2.amplitude.com": f"{M}/amp",
    "https://circleci.com/api/v2": f"{M}/ci",
    "https://sync.api.bannerbear.com/v2": f"{M}/bb",
    "https://api.paddle.com": f"{M}/pd",
    "https://api.mollie.com/v2": f"{M}/mo",
    "https://jot.mock": f"{M}/jf",
    "https://www.zohoapis.eu/crm/v6": f"{M}/zo",
    "https://api.xero.com/api.xro/2.0": f"{M}/xe",
    "https://qb.mock/v3/company": f"{M}/qb",
    "https://api.hunter.io/v2": f"{M}/hu",
    "https://api.brandfetch.io/v2": f"{M}/bf",
    "https://api.lemlist.com/api": f"{M}/lm",
    "https://track.customer.io/api/v1": f"{M}/cio",
    "https://api.segment.io/v1": f"{M}/sg",
    "https://api.iterable.com/api": f"{M}/it",
    "https://api.getresponse.com/v3": f"{M}/gr",
    "https://api.pushbullet.com/v2": f"{M}/pb",
    "https://api.pushcut.io/v1": f"{M}/pc",
    "https://connect.signl4.com/webhook": f"{M}/s4",
    "https://mapi.storyblok.com/v1": f"{M}/sb",
    "https://api.travis-ci.com": f"{M}/tr",
    "https://api.gong.io/v2": f"{M}/gg",
    "https://services.leadconnectorhq.com": f"{M}/hl",
    "https://api.medium.com/v1": f"{M}/md",
    "https://api.transferwise.com": f"{M}/ws",
    "https://onfleet.com/api/v2": f"{M}/of",
    "https://api.peekalink.io": f"{M}/pk",
    "https://api.marketstack.com/v1": f"{M}/ms",
    "https://www.openthesaurus.de": f"{M}/ot",
    "https://api.ouraring.com/v2/usercollection": f"{M}/ou",
    **{f"https://{s}.eu-central-1.amazonaws.com": f"{M}/aws-{s}" for s in ("lambda", "sns", "sqs", "email", "dynamodb", "comprehend", "rekognition", "textract", "transcribe")},
    "https://acme.freshservice.com/api/v2": f"{M}/fs",
    "https://api.copper.com/developer_api/v1": f"{M}/cp",
    "https://acme.agilecrm.com/dev/api": f"{M}/ag",
    "https://www.formstack.com/api/v2": f"{M}/fk",
    "https://acme.wufoo.com/api/v3": f"{M}/wf",
    "https://api.surveymonkey.com/v3": f"{M}/sm",
    "https://api.tapfiliate.com/1.6": f"{M}/tf",
    "https://api.dropcontact.io": f"{M}/dc",
    "https://rest.messagebird.com": f"{M}/mb",
    "https://api.plivo.com/v1/Account": f"{M}/pv",
    "https://webexapis.com/v1": f"{M}/wx",
    "https://myapp.bubbleapps.io/api/1.1/obj": f"{M}/bu",
    "https://api.quickbase.com/v1": f"{M}/qbs",
    "https://api.phantombuster.com/api/v2": f"{M}/ph2",
    "https://rest.moceanapi.com/rest/2": f"{M}/mc",
    "https://commentanalyzer.googleapis.com/v1alpha1": f"{M}/pp",
    "https://disqus.com/api/3.0": f"{M}/dq",
    "https://api.securityscorecard.io": f"{M}/ssc",
    "https://urlscan.io/api/v1": f"{M}/us",
    "https://www.strava.com/api/v3": f"{M}/st",
    "https://people.googleapis.com/v1": f"{M}/gp",
    "https://slides.googleapis.com/v1": f"{M}/gsl",
    "https://chat.googleapis.com/v1": f"{M}/gch",
    "https://storage.googleapis.com/storage/v1": f"{M}/gcs",
    "https://analyticsdata.googleapis.com/v1beta": f"{M}/ga",
    "https://www.googleapis.com/books/v1": f"{M}/gbk",
    "https://language.googleapis.com/v1": f"{M}/gnl",
    "https://firestore.googleapis.com/v1": f"{M}/gfs",
    "https://admin.googleapis.com/admin/directory/v1": f"{M}/gad",
    "https://graph.microsoft.com/v1.0": f"{M}/graph",
    "https://cognito-idp.eu-central-1.amazonaws.com": f"{M}/aws-cognito",
    "https://acm.eu-central-1.amazonaws.com": f"{M}/aws-acm",
    "https://api.affinity.co": f"{M}/af",
    "https://rest.apitemplate.io/v2": f"{M}/at",
    "https://www.beeminder.com/api/v1": f"{M}/bm",
    "https://api.bamboohr.com/api/gateway.php": f"{M}/bh",
    "https://api.bitwarden.com/public": f"{M}/bw",
    "https://acme.chargebee.com/api/v2": f"{M}/cb",
    "https://my.demio.com/api/v1": f"{M}/dm",
    "https://api-eu.dhl.com": f"{M}/dhl",
    "https://driftapi.com": f"{M}/dr",
    "https://acme.myfreshworks.com/crm/sales/api": f"{M}/fw",
    "https://api.getgo.com/G2W/rest/v2/organizers": f"{M}/gtw",
    "https://api.helpscout.net/v2": f"{M}/hs",
    "https://api.infusionsoft.com/crm/rest/v1": f"{M}/kp",
    "https://api.mailcheck.co/v1": f"{M}/mck",
    "https://mandrillapp.com/api/1.0": f"{M}/mdr",
    "https://api.nasa.gov": f"{M}/nasa",
    "https://paypal.mock": f"{M}/pp2",
    "https://www.toptal.com/developers/postbin/api": f"{M}/pbin",
    "https://acme.salesmate.io/apis": f"{M}/smt",
    "https://stackby.com/api/betav1": f"{M}/sby",
    "https://acme.syncromsp.com/api/v1": f"{M}/syn",
    "https://api.twist.com/api/v3": f"{M}/tw",
    "https://api.uplead.com/v2": f"{M}/ul",
    "https://api.uptimerobot.com/v2": f"{M}/ur",
    "https://api.getvero.com/api/v2": f"{M}/vr",
    "https://actionnetwork.org/api/v2": f"{M}/an", "https://api.adalo.com/v0/apps": f"{M}/ad", "https://company.clearbit.com/v2": f"{M}/cbt",
    "https://api.egoiapp.com": f"{M}/eg", "https://r.jina.ai": f"{M}/jr", "https://api-b2b.lingvanex.com/v1": f"{M}/lx", "https://control.msg91.com/api/v5": f"{M}/m91",
    "https://gmail.googleapis.com/gmail/v1/users/me": f"{M}/gm", "https://www.googleapis.com/drive/v3": f"{M}/gd",
    "https://www.googleapis.com/upload/drive/v3/files": f"{M}/gdu",
    "https://slack.com/api": f"{M}/sl", "https://api.telegram.org": f"{M}/tg", "https://discord.com/api/v10": f"{M}/dsc",
    "https://onesimpleapi.com/api": f"{M}/osa", "https://api.uproc.io/api/v2": f"{M}/up", "https://api.venafi.cloud": f"{M}/vf",
}
if "--overrides" in sys.argv:
    print(",".join(f"{a}={b}" for a, b in OVERRIDES.items()))
    sys.exit()

import e2e_lib as L  # noqa: E402

L.BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5679"
rest, node, chain, run_manual, out, check = L.rest, L.node, L.chain, L.run_manual, L.out, L.check
L.login_or_setup()

# The echo answers under every root key the integrations use, so any "root" setting finds the echo.
ECHO = ("={{ (() => { const e = {params: $json.params, query: $json.query, body: $json.body, auth: $json.headers.authorization || '', "
        "key: $json.headers['circle-token'] || $json.headers['apikey'] || $json.headers['api-key'] || '', h: $json.headers}; "
        "const r = Object.assign({}, e); "
        "for (const k of ['ticket', 'result', 'data', 'story', 'contact', 'call', 'user', 'link', 'Event', 'QueryResponse', 'Customer', 'Item', 'TranscriptionJob', 'Certificate', 'User', 'Data', 'customer', 'subscription', 'channel', 'file', 'profile', 'usergroup', 'message']) r[k] = e; "
        "for (const k of ['Messages', 'content', 'results', 'items', 'tickets', 'users', 'records', 'Invoices', 'Contacts', 'pushes', 'stories', "
        "'builds', 'entry', 'calls', 'synsets', 'Functions', 'Items', 'Languages', 'Entities', 'Labels', 'TextDetections', 'ExpenseDocuments', "
        "'agents', 'Forms', 'Entries', 'forms', 'submissions', 'connections', 'entries', 'spaces', 'documents', 'groups', 'value', 'CertificateSummaryList', 'Users', 'organizations', 'employees', 'list', 'shipments', 'objects', 'monitors', 'rows', 'customers', 'cases', 'certificates', 'channels', 'members', 'files', 'usergroups', 'scheduled_messages', 'workflows']) r[k] = [e]; r._embedded = {payments: [e], webinars: [e], conversations: [e], 'osdi:people': [e]}; r.ocs = {data: Object.assign({}, e, {users: [e]})}; "
        "r.response = Object.assign({}, e, {Attribute: [e], results: [e]}); "
        "return JSON.stringify(r); })() }}")
mocks = set()


def mock(method, path):
    if (method, path) in mocks:
        return
    mocks.add((method, path))
    # A ':' makes a webhook path dynamic (prefixed with the webhook id, as in n8n), so such
    # segments ("comments:analyze") become a parameter under the webhook id "c".
    if ":" in path:
        hook = {"path": "/".join(":p" if ":" in s else s for s in path.split("/"))}
        extra = {"webhookId": "c"}
    else:
        hook, extra = {"path": f"c/{path}"}, {}
    wf = rest("POST", "/workflows", {"name": f"c {path}", "nodes": [
        node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": method, **hook, "responseMode": "responseNode"}, 2, **extra),
        node("Reply", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": ECHO}, 1.1)], "connections": chain("Hook", "Reply")})
    rest("POST", f"/workflows/{wf['id']}/activate")


creds = {}


def cred(kind, data):
    if kind not in creds:
        creds[kind] = rest("POST", "/credentials", {"name": kind, "type": kind, "data": data})["id"]
    return {kind: {"id": creds[kind], "name": kind}}


basic = lambda u, p: "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()  # noqa: E731


def case(label, kind, cred_kind, cred_data, params, method, mock_path, expect):
    mock(method, mock_path)
    ex = run_manual({"name": label, "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0),
                                             node("N", kind, 200, params, credentials=cred(cred_kind, cred_data))], "connections": chain("Start", "N")})
    e = out(ex, "N")[0] if ex["status"] == "success" else None
    ok = False
    try:
        ok = e is not None and expect(e)
    except (KeyError, TypeError, IndexError):
        pass
    check(label, ok, f"{ex['data']['resultData'].get('error')} {e}")


SN = {"subdomain": "acme", "user": "admin", "password": "pw"}
case("ServiceNow: create incident (basic auth, extra fields)", "n8n-nodes-base.serviceNow", "serviceNowBasicApi", SN,
     {"resource": "incident", "operation": "create", "short_description": "Printer down", "additionalFields": {"urgency": "1"}}, "POST", "snow/table/incident",
     lambda e: e["auth"] == basic("admin", "pw") and e["body"] == {"short_description": "Printer down", "urgency": "1"})
case("ServiceNow: list incidents with encoded query", "n8n-nodes-base.serviceNow", "serviceNowBasicApi", SN,
     {"resource": "incident", "operation": "getAll", "sysparm_query": "active=true^priority=1", "limit": 5}, "GET", "snow/table/incident",
     lambda e: e["query"]["sysparm_query"] == "active=true^priority=1" and e["query"]["sysparm_limit"] == "5")

ZD = {"subdomain": "acme", "email": "ops@acme.io", "apiToken": "zt"}
case("Zendesk: create ticket (email/token basic auth, nested template)", "n8n-nodes-base.zendesk", "zendeskApi", ZD,
     {"resource": "ticket", "operation": "create", "subject": "Hi", "description": "Broken", "priority": "high"}, "POST", "zd/tickets",
     lambda e: e["auth"] == basic("ops@acme.io/token", "zt") and e["body"] == {"ticket": {"subject": "Hi", "comment": {"body": "Broken"}, "priority": "high"}})
case("Zendesk: comment keeps status out when blank, public stays boolean", "n8n-nodes-base.zendesk", "zendeskApi", ZD,
     {"resource": "ticket", "operation": "update", "ticketId": "42", "comment": "Done", "public": False, "status": ""}, "PUT", "zd/tickets/42",
     lambda e: e["body"] == {"ticket": {"comment": {"body": "Done", "public": False}}})

case("Mailjet: send (v3.1 Messages array)", "n8n-nodes-base.mailjet", "mailjetEmailApi", {"apiKey": "k", "secretKey": "s"},
     {"resource": "email", "operation": "send", "from": "a@x.io", "to": "b@y.io", "subject": "S", "text": "T"}, "POST", "mj/send",
     lambda e: e["auth"] == basic("k", "s") and e["body"] == {"Messages": [{"From": {"Email": "a@x.io"}, "To": [{"Email": "b@y.io"}], "Subject": "S", "TextPart": "T"}]})

case("PostHog: capture with api_key in body and parsed properties", "n8n-nodes-base.postHog", "postHogApi", {"url": f"{M}/ph", "apiKey": "phc_1"},
     {"resource": "event", "operation": "create", "event": "signup", "distinctId": "u1", "properties": '{"plan": "pro"}'}, "POST", "ph/capture",
     lambda e: e["body"] == {"api_key": "phc_1", "event": "signup", "distinct_id": "u1", "properties": {"plan": "pro"}})
case("Amplitude: track event", "n8n-nodes-base.amplitude", "amplitudeApi", {"apiKey": "ak"},
     {"resource": "event", "operation": "track", "eventType": "buy", "userId": "u2", "eventProperties": '{"sum": 3}'}, "POST", "amp/2/httpapi",
     lambda e: e["body"] == {"api_key": "ak", "events": [{"event_type": "buy", "user_id": "u2", "event_properties": {"sum": 3}}]})

case("CircleCI: trigger pipeline (token header, slug path)", "n8n-nodes-base.circleCi", "circleCiApi", {"apiKey": "cc"},
     {"resource": "pipeline", "operation": "trigger", "projectSlug": "gh-org-repo", "branch": "main"}, "POST", "ci/project/gh-org-repo/pipeline",
     lambda e: e["key"] == "cc" and e["body"] == {"branch": "main"})

case("Jotform: submissions (APIKEY header, templated host)", "n8n-nodes-base.jotForm", "jotFormApi", {"apiKey": "jk", "apiDomain": "jot.mock"},
     {"resource": "submission", "operation": "getAll", "formId": "991", "limit": 3}, "GET", "jf/form/991/submissions",
     lambda e: e["key"] == "jk" and e["query"]["limit"] == "3")

case("Bannerbear: image with modifications array", "n8n-nodes-base.bannerbear", "bannerbearApi", {"apiKey": "bk"},
     {"resource": "image", "operation": "create", "template": "T1", "modifications": '[{"name": "title", "text": "Hey"}]'}, "POST", "bb/images",
     lambda e: e["auth"] == "Bearer bk" and e["body"] == {"template": "T1", "modifications": [{"name": "title", "text": "Hey"}]})

case("Paddle: customers by email", "n8n-nodes-base.paddle", "paddleApi", {"apiKey": "pk"},
     {"resource": "customer", "operation": "getAll", "email": "a@b.c", "limit": 2}, "GET", "pd/customers",
     lambda e: e["auth"] == "Bearer pk" and e["query"]["email"] == "a@b.c" and e["query"]["per_page"] == "2")

case("Mollie: create payment (amount object)", "n8n-nodes-base.mollie", "mollieApi", {"apiKey": "test_x"},
     {"resource": "payment", "operation": "create", "amount": "10.00", "currency": "EUR", "description": "Order 7", "redirectUrl": "https://shop/x"}, "POST", "mo/payments",
     lambda e: e["auth"] == "Bearer test_x" and e["body"] == {"amount": {"currency": "EUR", "value": "10.00"}, "description": "Order 7", "redirectUrl": "https://shop/x"})
case("Mollie: list payments from _embedded", "n8n-nodes-base.mollie", "mollieApi", {"apiKey": "test_x"},
     {"resource": "payment", "operation": "getAll", "limit": 4}, "GET", "mo/payments", lambda e: e["query"]["limit"] == "4")

case("Confluence: create page (storage body, v2 API)", "n8n-nodes-base.confluence", "confluenceApi", {"domain": f"{M}/cf", "email": "me@x.io", "apiToken": "ct"},
     {"resource": "page", "operation": "create", "spaceId": "65", "title": "Runbook", "body": "<p>Hi</p>"}, "POST", "cf/wiki/api/v2/pages",
     lambda e: e["auth"] == basic("me@x.io", "ct") and e["body"] == {"spaceId": "65", "status": "current", "title": "Runbook", "body": {"representation": "storage", "value": "<p>Hi</p>"}})
# ---------------------------------------------------------------- batch 7
tok = rest("POST", "/workflows", {"name": "c token", "nodes": [
    node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": "POST", "path": "c/tok", "responseMode": "responseNode"}, 2),
    node("Reply", "n8n-nodes-base.respondToWebhook", 200, {"respondWith": "json", "responseBody": '{"access_token": "tok-1", "expires_in": 600}'}, 1.1)], "connections": chain("Hook", "Reply")})
rest("POST", f"/workflows/{tok['id']}/activate")
# OAuth2 credentials are connected through client credentials against the mock token endpoint.
OA = {"grantType": "clientCredentials", "accessTokenUrl": f"{M}/tok", "clientId": "id", "clientSecret": "sec"}
BEARER = "Bearer tok-1"

case("Salesforce: SOQL query with OAuth2 and the instance URL", "n8n-nodes-base.salesforce", "salesforceOAuth2Api", dict(OA, instanceUrl=f"{M}/sf"),
     {"resource": "record", "operation": "query", "q": "SELECT Id FROM Lead"}, "GET", "sf/services/data/v60.0/query",
     lambda e: e["auth"] == BEARER and e["query"]["q"] == "SELECT Id FROM Lead")
case("Salesforce: update record (PATCH, JSON body)", "n8n-nodes-base.salesforce", "salesforceOAuth2Api", dict(OA, instanceUrl=f"{M}/sf"),
     {"resource": "record", "operation": "update", "sobject": "Lead", "recordId": "00Q1", "data": '{"Status": "Working"}'}, "PATCH", "sf/services/data/v60.0/sobjects/Lead/00Q1",
     lambda e: e["body"] == {"Status": "Working"})
case("Zoho CRM: create record in the region's API", "n8n-nodes-base.zohoCrm", "zohoOAuth2Api", dict(OA, dataCenter="eu"),
     {"resource": "record", "operation": "create", "module": "Leads", "record": '{"Last_Name": "Lovelace"}'}, "POST", "zo/Leads",
     lambda e: e["auth"] == BEARER and e["body"] == {"data": [{"Last_Name": "Lovelace"}]})
case("Xero: tenant header from the credential", "n8n-nodes-base.xero", "xeroOAuth2Api", dict(OA, tenantId="T-42"),
     {"resource": "contact", "operation": "create", "name": "Acme", "email": "a@acme.io"}, "POST", "xe/Contacts",
     lambda e: e["h"]["xero-tenant-id"] == "T-42" and e["body"] == {"Contacts": [{"Name": "Acme", "EmailAddress": "a@acme.io"}]})
case("QuickBooks: query against the company (realm) path", "n8n-nodes-base.quickbooks", "quickBooksOAuth2Api", dict(OA, realmId="9130", apiHost="qb.mock"),
     {"resource": "record", "operation": "query", "query": "SELECT * FROM Customer"}, "GET", "qb/9130/query",
     lambda e: e["auth"] == BEARER and e["query"]["query"] == "SELECT * FROM Customer")
case("Hunter: email finder (api_key in query)", "n8n-nodes-base.hunter", "hunterApi", {"apiKey": "hk"},
     {"resource": "email", "operation": "find", "domain": "n8n.io", "first_name": "Ada", "last_name": "L"}, "GET", "hu/email-finder",
     lambda e: e["query"] == {"api_key": "hk", "domain": "n8n.io", "first_name": "Ada", "last_name": "L"})
case("Brandfetch: brand by domain", "n8n-nodes-base.brandfetch", "brandfetchApi", {"apiKey": "bk"},
     {"resource": "brand", "operation": "get", "domain": "n8n.io"}, "GET", "bf/brands/n8n.io", lambda e: e["auth"] == "Bearer bk")
case("Lemlist: add lead (basic auth with empty user, extra fields)", "n8n-nodes-base.lemlist", "lemlistApi", {"apiKey": "lk"},
     {"resource": "lead", "operation": "create", "campaignId": "cam1", "email": "ada", "additionalFields": {"firstName": "Ada"}}, "POST", "lm/campaigns/cam1/leads/ada",
     lambda e: e["auth"] == basic("", "lk") and e["body"] == {"firstName": "Ada"})
case("Customer.io: track event", "n8n-nodes-base.customerIo", "customerIoApi", {"trackingSiteId": "site", "trackingApiKey": "key"},
     {"resource": "event", "operation": "track", "customerId": "u7", "name": "purchased", "eventData": '{"total": 9}'}, "POST", "cio/customers/u7/events",
     lambda e: e["auth"] == basic("site", "key") and e["body"] == {"name": "purchased", "data": {"total": 9}})
case("Segment: track (write key as basic user)", "n8n-nodes-base.segment", "segmentApi", {"writekey": "wk"},
     {"resource": "track", "operation": "event", "userId": "u1", "event": "Signed Up", "properties": '{"plan": "pro"}'}, "POST", "sg/track",
     lambda e: e["auth"] == basic("wk", "") and e["body"] == {"userId": "u1", "event": "Signed Up", "properties": {"plan": "pro"}})
case("Iterable: upsert user (Api-Key header)", "n8n-nodes-base.iterable", "iterableApi", {"apiKey": "ik"},
     {"resource": "user", "operation": "upsert", "email": "a@b.c", "dataFields": '{"tier": 2}'}, "POST", "it/users/update",
     lambda e: e["key"] == "ik" and e["body"] == {"email": "a@b.c", "dataFields": {"tier": 2}})
case("GetResponse: contact (templated X-Auth-Token)", "n8n-nodes-base.getResponse", "getResponseApi", {"apiKey": "gk"},
     {"resource": "contact", "operation": "create", "email": "a@b.c", "campaignId": "L1"}, "POST", "gr/contacts",
     lambda e: e["h"]["x-auth-token"] == "api-key gk" and e["body"] == {"email": "a@b.c", "campaign": {"campaignId": "L1"}})
case("Pushbullet: note push", "n8n-nodes-base.pushbullet", "pushbulletOAuth2Api", {"accessToken": "pt"},
     {"resource": "push", "operation": "create", "title": "Hi", "body": "Done"}, "POST", "pb/pushes",
     lambda e: e["h"]["access-token"] == "pt" and e["body"] == {"type": "note", "title": "Hi", "body": "Done"})
case("Pushcut: notification by name", "n8n-nodes-base.pushcut", "pushcutApi", {"apiKey": "pck"},
     {"resource": "notification", "operation": "send", "notificationName": "Deploy", "text": "ok"}, "POST", "pc/notifications/Deploy",
     lambda e: e["h"]["api-key"] == "pck" and e["body"] == {"text": "ok"})
case("SIGNL4: alert to the team webhook", "n8n-nodes-base.signl4", "signl4Api", {"teamSecret": "team9"},
     {"resource": "alert", "operation": "send", "title": "Disk full", "message": "92%", "externalId": "d1"}, "POST", "s4/team9",
     lambda e: e["body"] == {"Title": "Disk full", "Message": "92%", "X-S4-ExternalID": "d1", "X-S4-Status": "new"})
case("Yourls: shorten (signature + action in query)", "n8n-nodes-base.yourls", "yourlsApi", {"url": f"{M}/yr", "signature": "sig"},
     {"resource": "url", "operation": "shorten", "url": "https://example.com/x"}, "GET", "yr/yourls-api.php",
     lambda e: e["query"] == {"signature": "sig", "action": "shorturl", "format": "json", "url": "https://example.com/x"})
case("Storyblok: publish story", "n8n-nodes-base.storyblok", "storyblokManagementApi", {"accessToken": "sbt"},
     {"resource": "story", "operation": "publish", "space": "123", "storyId": "456"}, "GET", "sb/spaces/123/stories/456/publish", lambda e: e["auth"] == "sbt")
case("Travis CI: trigger (slug encoded, API version header)", "n8n-nodes-base.travisCi", "travisCiApi", {"apiToken": "tt"},
     {"resource": "build", "operation": "trigger", "slug": "acme-app", "branch": "main"}, "POST", "tr/repo/acme-app/requests",
     lambda e: e["auth"] == "token tt" and e["h"]["travis-api-version"] == "3" and e["body"] == {"request": {"branch": "main"}})
case("Rundeck: run job with arguments", "n8n-nodes-base.rundeck", "rundeckApi", {"url": f"{M}/rd", "token": "rt"},
     {"resource": "job", "operation": "execute", "jobId": "j1", "argString": "-env prod"}, "POST", "rd/api/41/job/j1/run",
     lambda e: e["h"]["x-rundeck-auth-token"] == "rt" and e["body"] == {"argString": "-env prod"})
case("Splunk: search job as form body", "n8n-nodes-base.splunk", "splunkApi", {"baseUrl": f"{M}/sp", "authToken": "st"},
     {"resource": "search", "operation": "create", "search": "search index=main"}, "POST", "sp/services/search/jobs",
     lambda e: e["auth"] == "Bearer st" and e["body"]["search"] == "search index=main" and e["body"]["output_mode"] == "json")
case("TheHive 5: alert with severity", "n8n-nodes-base.theHiveProject", "theHiveProjectApi", {"url": f"{M}/th", "apiKey": "hk"},
     {"resource": "alert", "operation": "create", "type": "external", "source": "fw", "sourceRef": "r1", "title": "T", "description": "D", "additionalFields": {"severity": 3}}, "POST", "th/api/v1/alert",
     lambda e: e["auth"] == "Bearer hk" and e["body"] == {"type": "external", "source": "fw", "sourceRef": "r1", "title": "T", "description": "D", "severity": 3})
case("MISP: create event (nested Event object)", "n8n-nodes-base.misp", "mispApi", {"baseUrl": f"{M}/mi", "apiKey": "mk"},
     {"resource": "event", "operation": "create", "info": "Phishing wave", "distribution": "0", "threatLevel": "2"}, "POST", "mi/events/add",
     lambda e: e["auth"] == "mk" and e["body"] == {"Event": {"info": "Phishing wave", "distribution": "0", "threat_level_id": "2", "analysis": "0"}})
case("Gong: calls in a time range", "n8n-nodes-base.gong", "gongApi", {"accessKey": "ak", "accessKeySecret": "as"},
     {"resource": "call", "operation": "getAll", "fromDateTime": "2026-01-01T00:00:00Z"}, "GET", "gg/calls",
     lambda e: e["auth"] == basic("ak", "as") and e["query"]["fromDateTime"] == "2026-01-01T00:00:00Z")
case("HighLevel: contact (Version header)", "n8n-nodes-base.highLevel", "highLevelApi", {"apiKey": "hlk"},
     {"resource": "contact", "operation": "create", "locationId": "loc1", "email": "a@b.c"}, "POST", "hl/contacts",
     lambda e: e["h"]["version"] == "2021-07-28" and e["body"] == {"locationId": "loc1", "email": "a@b.c"})
case("Medium: create post", "n8n-nodes-base.medium", "mediumApi", {"accessToken": "mt"},
     {"resource": "post", "operation": "create", "authorId": "me1", "title": "Hello", "contentFormat": "markdown", "content": "# Hi", "publishStatus": "draft"}, "POST", "md/users/me1/posts",
     lambda e: e["body"] == {"title": "Hello", "contentFormat": "markdown", "content": "# Hi", "publishStatus": "draft"})
case("Wise: exchange rate", "n8n-nodes-base.wise", "wiseApi", {"apiToken": "wt"},
     {"resource": "exchangeRate", "operation": "get", "source": "EUR", "target": "GBP"}, "GET", "ws/v1/rates",
     lambda e: e["auth"] == "Bearer wt" and e["query"] == {"source": "EUR", "target": "GBP"})
case("Onfleet: task with unparsed address", "n8n-nodes-base.onfleet", "onfleetApi", {"apiKey": "ok"},
     {"resource": "task", "operation": "create", "address": "1 Main St", "name": "Ada", "phone": "+4917"}, "POST", "of/tasks",
     lambda e: e["auth"] == basic("ok", "") and e["body"] == {"destination": {"address": {"unparsed": "1 Main St"}}, "recipients": [{"name": "Ada", "phone": "+4917"}]})
case("Peekalink: link preview", "n8n-nodes-base.peekalink", "peekalinkApi", {"apiKey": "pk1"},
     {"resource": "link", "operation": "preview", "link": "https://n8n.io"}, "POST", "pk", lambda e: e["h"]["x-api-key"] == "pk1" and e["body"] == {"link": "https://n8n.io"})
case("Marketstack: end-of-day prices", "n8n-nodes-base.marketstack", "marketstackApi", {"apiKey": "msk"},
     {"resource": "endOfDayData", "operation": "getAll", "symbols": "AAPL", "limit": 5}, "GET", "ms/eod",
     lambda e: e["query"] == {"access_key": "msk", "symbols": "AAPL", "limit": "5"})
mock("GET", "ot/synonyme/search")
ex = run_manual({"name": "ot", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), node("N", "n8n-nodes-base.openThesaurus", 200, {"resource": "synonym", "operation": "get", "q": "schnell"})],
                 "connections": chain("Start", "N")})
check("OpenThesaurus: no credential needed", ex["status"] == "success" and out(ex, "N")[0]["query"] == {"q": "schnell", "format": "application/json"}, f"{ex['data']['resultData'].get('error')}")
case("Oura: daily sleep", "n8n-nodes-base.oura", "ouraApi", {"accessToken": "oat"},
     {"resource": "summary", "operation": "sleep", "start_date": "2026-11-01"}, "GET", "ou/daily_sleep",
     lambda e: e["auth"] == "Bearer oat" and e["query"]["start_date"] == "2026-11-01")

# ---------------------------------------------------------------- batch 8: AWS with an independent Signature V4 check
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import urllib.parse  # noqa: E402

AWS = {"region": "eu-central-1", "accessKeyId": "AKIDEXAMPLE", "secretAccessKey": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"}


def sigv4_ok(e, method, path, service, json_body=True):
    """Recomputes the signature from what the mock received and compares it with the Authorization header."""
    h = e["h"]
    amz, payload = h["x-amz-date"], h["x-amz-content-sha256"]
    if json_body and payload != hashlib.sha256(json.dumps(e["body"], separators=(",", ":"), ensure_ascii=False).encode()).hexdigest():
        return False
    enc = lambda s: urllib.parse.quote(s, safe="-_.~")  # noqa: E731
    query = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(e["query"].items()))
    uri = "/".join(enc(seg) for seg in ("/webhook/c/" + path).split("/"))
    # Every header named in SignedHeaders takes part, with the value the mock received; every
    # x-amz-* header that was sent must be among them.
    names = e["auth"].split("SignedHeaders=")[1].split(",")[0].split(";")
    if not {"host", "x-amz-content-sha256", "x-amz-date"} <= set(names) or any(k.startswith("x-amz-") and k not in names for k in h):
        return False
    headers = "".join(f"{n}:{'127.0.0.1:5678' if n == 'host' else h[n].strip()}\n" for n in names)
    canonical = f"{method}\n{uri}\n{query}\n{headers}\n{';'.join(names)}\n{payload}"
    scope = f"{amz[:8]}/{AWS['region']}/{service}/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = ("AWS4" + AWS["secretAccessKey"]).encode()
    for part in (amz[:8], AWS["region"], service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return e["auth"] == f"AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"


case("AWS Lambda: invoke with a valid Signature V4", "n8n-nodes-base.awsLambda", "aws", AWS,
     {"resource": "function", "operation": "invoke", "function": "resize", "data": '{"w": 200}'}, "POST", "aws-lambda/2015-03-31/functions/resize/invocations",
     lambda e: e["body"] == {"w": 200} and sigv4_ok(e, "POST", "aws-lambda/2015-03-31/functions/resize/invocations", "lambda"))
case("AWS SNS: publish (query protocol as form, signed)", "n8n-nodes-base.awsSns", "aws", AWS,
     {"resource": "topic", "operation": "publish", "topicArn": "arn:aws:sns:eu-central-1:1:alerts", "message": "Disk full"}, "POST", "aws-sns",
     lambda e: e["body"] == {"Action": "Publish", "Version": "2010-03-31", "TopicArn": "arn:aws:sns:eu-central-1:1:alerts", "Message": "Disk full"}
     and sigv4_ok(e, "POST", "aws-sns/", "sns", json_body=False))
case("AWS SQS: send message (JSON protocol target header)", "n8n-nodes-base.awsSqs", "aws", AWS,
     {"resource": "queue", "operation": "sendMessage", "queueUrl": "https://sqs/1/q", "messageBody": "hi", "additionalFields": {"delaySeconds": 5}}, "POST", "aws-sqs",
     lambda e: e["h"]["x-amz-target"] == "AmazonSQS.SendMessage" and e["h"]["content-type"] == "application/x-amz-json-1.0"
     and e["body"] == {"QueueUrl": "https://sqs/1/q", "MessageBody": "hi", "DelaySeconds": 5} and sigv4_ok(e, "POST", "aws-sqs/", "sqs"))
case("AWS SES: v2 email (service name 'ses' in the scope)", "n8n-nodes-base.awsSes", "aws", AWS,
     {"resource": "email", "operation": "send", "from": "a@x.io", "to": "b@y.io", "subject": "S", "text": "T"}, "POST", "aws-email/v2/email/outbound-emails",
     lambda e: e["body"] == {"FromEmailAddress": "a@x.io", "Destination": {"ToAddresses": ["b@y.io"]}, "Content": {"Simple": {"Subject": {"Data": "S"}, "Body": {"Text": {"Data": "T"}}}}}
     and sigv4_ok(e, "POST", "aws-email/v2/email/outbound-emails", "ses"))
case("AWS DynamoDB: put item", "n8n-nodes-base.awsDynamoDb", "aws", dict(AWS, sessionToken=""),
     {"resource": "item", "operation": "upsert", "table": "orders", "item": '{"pk": {"S": "o1"}}'}, "POST", "aws-dynamodb",
     lambda e: e["h"]["x-amz-target"] == "DynamoDB_20120810.PutItem" and e["body"] == {"TableName": "orders", "Item": {"pk": {"S": "o1"}}} and sigv4_ok(e, "POST", "aws-dynamodb/", "dynamodb"))
case("AWS Comprehend: sentiment", "n8n-nodes-base.awsComprehend", "aws", AWS,
     {"resource": "text", "operation": "detectSentiment", "text": "Great tool", "languageCode": "en"}, "POST", "aws-comprehend",
     lambda e: e["h"]["x-amz-target"] == "Comprehend_20171127.DetectSentiment" and e["body"] == {"Text": "Great tool", "LanguageCode": "en"} and sigv4_ok(e, "POST", "aws-comprehend/", "comprehend"))
case("AWS Rekognition: labels of an S3 image", "n8n-nodes-base.awsRekognition", "aws", AWS,
     {"resource": "image", "operation": "detectLabels", "bucket": "imgs", "key": "cat.jpg", "maxLabels": 5}, "POST", "aws-rekognition",
     lambda e: e["body"] == {"Image": {"S3Object": {"Bucket": "imgs", "Name": "cat.jpg"}}, "MaxLabels": 5} and sigv4_ok(e, "POST", "aws-rekognition/", "rekognition"))
case("AWS Textract: analyze expense", "n8n-nodes-base.awsTextract", "aws", AWS,
     {"resource": "document", "operation": "analyzeExpense", "bucket": "docs", "key": "r.png"}, "POST", "aws-textract",
     lambda e: e["h"]["x-amz-target"] == "Textract.AnalyzeExpense" and sigv4_ok(e, "POST", "aws-textract/", "textract"))
case("AWS Transcribe: start job", "n8n-nodes-base.awsTranscribe", "aws", AWS,
     {"resource": "transcriptionJob", "operation": "create", "name": "j1", "mediaUri": "s3://b/a.mp3", "language": "de-DE"}, "POST", "aws-transcribe",
     lambda e: e["body"] == {"TranscriptionJobName": "j1", "LanguageCode": "de-DE", "Media": {"MediaFileUri": "s3://b/a.mp3"}} and sigv4_ok(e, "POST", "aws-transcribe/", "transcribe"))

# ---------------------------------------------------------------- batch 8: other services
case("Freshservice: ticket with numeric priority", "n8n-nodes-base.freshservice", "freshserviceApi", {"domain": "acme", "apiKey": "fk"},
     {"resource": "ticket", "operation": "create", "subject": "VPN", "description": "down", "email": "a@b.c", "priority": 3, "status": 2}, "POST", "fs/tickets",
     lambda e: e["auth"] == basic("fk", "X") and e["body"] == {"subject": "VPN", "description": "down", "email": "a@b.c", "priority": 3, "status": 2})
case("Copper: person (three X-PW headers)", "n8n-nodes-base.copper", "copperApi", {"email": "me@x.io", "apiKey": "ck"},
     {"resource": "person", "operation": "create", "name": "Ada", "email": "ada@x.io"}, "POST", "cp/people",
     lambda e: e["h"]["x-pw-accesstoken"] == "ck" and e["h"]["x-pw-useremail"] == "me@x.io" and e["h"]["x-pw-application"] == "developer_api"
     and e["body"] == {"name": "Ada", "emails": [{"email": "ada@x.io", "category": "work"}]})
case("Agile CRM: contact properties", "n8n-nodes-base.agileCrm", "agileCrmApi", {"subdomain": "acme", "email": "me@x.io", "apiKey": "ak"},
     {"resource": "contact", "operation": "create", "email": "ada@x.io", "firstName": "Ada"}, "POST", "ag/contacts",
     lambda e: e["auth"] == basic("me@x.io", "ak") and e["body"] == {"properties": [{"type": "SYSTEM", "name": "email", "value": "ada@x.io"}, {"type": "SYSTEM", "name": "first_name", "value": "Ada"}, {"type": "SYSTEM", "name": "last_name"}]})
case("Monica CRM: contact with fixed flags", "n8n-nodes-base.monicaCrm", "monicaCrmApi", {"domain": f"{M}/mo2", "apiToken": "mt"},
     {"resource": "contact", "operation": "create", "firstName": "Ada", "genderId": 3}, "POST", "mo2/api/contacts",
     lambda e: e["body"] == {"first_name": "Ada", "gender_id": 3, "is_birthdate_known": False, "is_deceased": False, "is_deceased_date_known": False})
case("KoBoToolbox: submissions (Token header)", "n8n-nodes-base.koBoToolbox", "koBoToolboxApi", {"URL": f"{M}/kb", "token": "kt"},
     {"resource": "submission", "operation": "getAll", "formId": "aX1", "limit": 3}, "GET", "kb/api/v2/assets/aX1/data",
     lambda e: e["auth"] == "Token kt" and e["query"] == {"format": "json", "limit": "3"})
case("Formstack: submissions with data", "n8n-nodes-base.formstack", "formstackApi", {"accessToken": "ft"},
     {"resource": "submission", "operation": "getAll", "formId": "77", "limit": 2}, "GET", "fk/form/77/submission.json", lambda e: e["query"] == {"data": "true", "per_page": "2"})
case("Wufoo: entries (API key as basic user)", "n8n-nodes-base.wufoo", "wufooApi", {"subdomain": "acme", "apiKey": "wk"},
     {"resource": "entry", "operation": "getAll", "formHash": "z1", "limit": 4}, "GET", "wf/forms/z1/entries.json", lambda e: e["auth"] == basic("wk", "footastic"))
case("SurveyMonkey: bulk responses", "n8n-nodes-base.surveyMonkey", "surveyMonkeyApi", {"accessToken": "smt"},
     {"resource": "response", "operation": "getAll", "surveyId": "s9", "limit": 5}, "GET", "sm/surveys/s9/responses/bulk", lambda e: e["auth"] == "Bearer smt")
case("Tapfiliate: conversion amount is numeric", "n8n-nodes-base.tapfiliate", "tapfiliateApi", {"apiKey": "tk"},
     {"resource": "conversion", "operation": "create", "external_id": "o1", "amount": 49.5}, "POST", "tf/conversions",
     lambda e: e["h"]["x-api-key"] == "tk" and e["body"] == {"external_id": "o1", "amount": 49.5})
case("Dropcontact: batch enrichment", "n8n-nodes-base.dropcontact", "dropcontactApi", {"apiKey": "dk"},
     {"resource": "contact", "operation": "enrich", "email": "ada@x.io"}, "POST", "dc/batch",
     lambda e: e["h"]["x-access-token"] == "dk" and e["body"] == {"data": [{"email": "ada@x.io"}], "siren": False, "language": "en"})
case("MessageBird: SMS to a recipient list", "n8n-nodes-base.messageBird", "messageBirdApi", {"accessKey": "mk"},
     {"resource": "sms", "operation": "send", "originator": "Flow", "recipients": "4917, 4918", "body": "Hi"}, "POST", "mb/messages",
     lambda e: e["auth"] == "AccessKey mk" and e["body"] == {"originator": "Flow", "recipients": ["4917", "4918"], "body": "Hi"})
case("Plivo: SMS below the account path", "n8n-nodes-base.plivo", "plivoApi", {"authId": "MA1", "authToken": "pt"},
     {"resource": "sms", "operation": "send", "src": "+491", "dst": "+492", "text": "Hi"}, "POST", "pv/MA1/Message",
     lambda e: e["auth"] == basic("MA1", "pt") and e["body"] == {"src": "+491", "dst": "+492", "text": "Hi"})
case("Webex: message in a space", "n8n-nodes-base.ciscoWebex", "ciscoWebexApi", {"accessToken": "wxt"},
     {"resource": "message", "operation": "create", "roomId": "r1", "markdown": "**Hi**"}, "POST", "wx/messages", lambda e: e["body"] == {"roomId": "r1", "markdown": "**Hi**"})
case("ERPNext: create document (token key:secret header)", "n8n-nodes-base.erpNext", "erpNextApi", {"domain": f"{M}/erp", "apiKey": "k1", "apiSecret": "s1"},
     {"resource": "document", "operation": "create", "docType": "Customer", "data": '{"customer_name": "Acme"}'}, "POST", "erp/api/resource/Customer",
     lambda e: e["auth"] == "token k1:s1" and e["body"] == {"customer_name": "Acme"})
case("Magento 2: orders with searchCriteria", "n8n-nodes-base.magento2", "magento2Api", {"host": f"{M}/mg", "accessToken": "mgt"},
     {"resource": "order", "operation": "getAll"}, "GET", "mg/rest/default/V1/orders", lambda e: e["auth"] == "Bearer mgt" and e["query"] == {"searchCriteria[pageSize]": "20"})
case("Bubble: thing by id", "n8n-nodes-base.bubble", "bubbleApi", {"appName": "myapp", "apiToken": "bt"},
     {"resource": "object", "operation": "get", "typeName": "order", "objectId": "x9"}, "GET", "bu/order/x9", lambda e: e["auth"] == "Bearer bt")
case("Quick Base: query (realm + user token headers)", "n8n-nodes-base.quickbase", "quickbaseApi", {"hostname": "acme.quickbase.com", "userToken": "qt"},
     {"resource": "record", "operation": "query", "tableId": "bq1", "where": "{6.EX.'open'}"}, "POST", "qbs/records/query",
     lambda e: e["h"]["qb-realm-hostname"] == "acme.quickbase.com" and e["auth"] == "QB-USER-TOKEN qt" and e["body"] == {"from": "bq1", "where": "{6.EX.'open'}"})
case("Phantombuster: launch agent", "n8n-nodes-base.phantombuster", "phantombusterApi", {"apiKey": "pbk"},
     {"resource": "agent", "operation": "launch", "agentId": "42", "argument": '{"query": "rust"}'}, "POST", "ph2/agents/launch",
     lambda e: e["h"]["x-phantombuster-key-1"] == "pbk" and e["body"] == {"id": "42", "argument": {"query": "rust"}})
case("Sendy: subscribe (api key in the form body)", "n8n-nodes-base.sendy", "sendyApi", {"url": f"{M}/sy", "apiKey": "syk"},
     {"resource": "subscriber", "operation": "add", "list": "L1", "email": "a@b.c"}, "POST", "sy/subscribe",
     lambda e: e["body"] == {"api_key": "syk", "list": "L1", "email": "a@b.c", "boolean": "true"})
case("Mocean: SMS (keys in query, form body)", "n8n-nodes-base.mocean", "moceanApi", {"apiKey": "k", "apiSecret": "s"},
     {"resource": "sms", "operation": "send", "from": "Flow", "to": "4917", "text": "Hi"}, "POST", "mc/sms",
     lambda e: e["query"] == {"mocean-api-key": "k", "mocean-api-secret": "s"} and e["body"] == {"mocean-from": "Flow", "mocean-to": "4917", "mocean-text": "Hi", "mocean-resp-format": "json"})
case("Google Perspective: attribute as dynamic key", "n8n-nodes-base.googlePerspective", "googlePerspectiveApi", {"apiKey": "gk"},
     {"resource": "text", "operation": "analyze", "text": "you are great", "attribute": "INSULT", "language": "en"}, "POST", "pp/comments:analyze",
     lambda e: e["query"] == {"key": "gk"} and e["body"] == {"comment": {"text": "you are great"}, "requestedAttributes": {"INSULT": {}}, "languages": ["en"]})
case("Disqus: forum details", "n8n-nodes-base.disqus", "disqusApi", {"accessToken": "dk"},
     {"resource": "forum", "operation": "get", "forum": "flowwolf"}, "GET", "dq/forums/details.json", lambda e: e["query"] == {"api_key": "dk", "forum": "flowwolf"})
case("SecurityScorecard: scorecard", "n8n-nodes-base.securityScorecard", "securityScorecardApi", {"apiKey": "sk"},
     {"resource": "company", "operation": "getScorecard", "domain": "n8n.io"}, "GET", "ssc/companies/n8n.io", lambda e: e["auth"] == "Token sk")
case("urlscan.io: scan", "n8n-nodes-base.urlScanIo", "urlScanIoApi", {"apiKey": "uk"},
     {"resource": "scan", "operation": "perform", "url": "https://n8n.io", "visibility": "unlisted"}, "POST", "us/scan",
     lambda e: e["h"]["api-key"] == "uk" and e["body"] == {"url": "https://n8n.io", "visibility": "unlisted"})
case("Strava: create activity (OAuth2)", "n8n-nodes-base.strava", "stravaOAuth2Api", OA,
     {"resource": "activity", "operation": "create", "name": "Morning Run", "sport_type": "Run", "start_date_local": "2026-11-07T07:00:00Z", "elapsed_time": 1800}, "POST", "st/activities",
     lambda e: e["auth"] == BEARER and e["body"] == {"name": "Morning Run", "sport_type": "Run", "start_date_local": "2026-11-07T07:00:00Z", "elapsed_time": 1800})
case("Google Contacts: create contact", "n8n-nodes-base.googleContacts", "googleContactsOAuth2Api", OA,
     {"resource": "contact", "operation": "create", "givenName": "Ada", "email": "ada@x.io"}, "POST", "gp/people:createContact",
     lambda e: e["auth"] == BEARER and e["body"] == {"names": [{"givenName": "Ada"}], "emailAddresses": [{"value": "ada@x.io"}]})

# ---------------------------------------------------------------- batch 9
case("Google Slides: create presentation", "n8n-nodes-base.googleSlides", "googleSlidesOAuth2Api", OA,
     {"resource": "presentation", "operation": "create", "title": "Q4"}, "POST", "gsl/presentations", lambda e: e["auth"] == BEARER and e["body"] == {"title": "Q4"})
case("Google Chat: message to a space path ({+} keeps the slash)", "n8n-nodes-base.googleChat", "googleChatOAuth2Api", OA,
     {"resource": "message", "operation": "create", "spaceName": "spaces/AAA", "text": "Deployed"}, "POST", "gch/spaces/AAA/messages", lambda e: e["body"] == {"text": "Deployed"})
case("Google Cloud Storage: objects with prefix", "n8n-nodes-base.googleCloudStorage", "googleCloudStorageOAuth2Api", OA,
     {"resource": "object", "operation": "getAll", "bucket": "logs", "prefix": "2026/", "limit": 10}, "GET", "gcs/b/logs/o", lambda e: e["query"] == {"prefix": "2026/", "maxResults": "10"})
case("Google Analytics: GA4 runReport (colon path)", "n8n-nodes-base.googleAnalytics", "googleAnalyticsOAuth2", OA,
     {"resource": "report", "operation": "get", "propertyId": "123", "metric": "sessions"}, "POST", "ga/properties/123:runReport",
     lambda e: e["body"] == {"dateRanges": [{"startDate": "7daysAgo", "endDate": "today"}], "dimensions": [{"name": "date"}], "metrics": [{"name": "sessions"}]})
case("Google Books: search", "n8n-nodes-base.googleBooks", "googleBooksOAuth2Api", OA,
     {"resource": "volume", "operation": "getAll", "q": "rust", "limit": 5}, "GET", "gbk/volumes", lambda e: e["query"] == {"q": "rust", "maxResults": "5"})
case("Google Natural Language: sentiment", "n8n-nodes-base.googleCloudNaturalLanguage", "googleCloudNaturalLanguageOAuth2Api", OA,
     {"resource": "document", "operation": "analyzeSentiment", "content": "Great"}, "POST", "gnl/documents:analyzeSentiment",
     lambda e: e["body"] == {"document": {"type": "PLAIN_TEXT", "content": "Great"}, "encodingType": "UTF8"})
case("Firestore: create document under the project", "n8n-nodes-base.googleFirebaseCloudFirestore", "googleFirebaseCloudFirestoreOAuth2Api", dict(OA, projectId="proj1"),
     {"resource": "document", "operation": "create", "collection": "users", "data": '{"fields": {"name": {"stringValue": "Ada"}}}'}, "POST", "gfs/projects/proj1/databases/(default)/documents/users",
     lambda e: e["body"] == {"fields": {"name": {"stringValue": "Ada"}}})
case("Google Workspace Admin: users of my_customer", "n8n-nodes-base.gSuiteAdmin", "gSuiteAdminOAuth2Api", OA,
     {"resource": "user", "operation": "getAll", "limit": 3}, "GET", "gad/users", lambda e: e["query"] == {"customer": "my_customer", "maxResults": "3"})
case("Excel 365: append table rows", "n8n-nodes-base.microsoftExcel", "microsoftExcelOAuth2Api", OA,
     {"resource": "table", "operation": "addRow", "workbookId": "W1", "table": "Sales", "values": '[["Ada", 36]]'}, "POST", "graph/me/drive/items/W1/workbook/tables/Sales/rows",
     lambda e: e["auth"] == BEARER and e["body"] == {"values": [["Ada", 36]]})
case("Entra ID: create user with password profile", "n8n-nodes-base.microsoftEntra", "microsoftEntraOAuth2Api", OA,
     {"resource": "user", "operation": "create", "displayName": "Ada", "mailNickname": "ada", "upn": "ada@acme.io", "password": "Temp-1234!"}, "POST", "graph/users",
     lambda e: e["body"] == {"accountEnabled": True, "displayName": "Ada", "mailNickname": "ada", "userPrincipalName": "ada@acme.io",
                             "passwordProfile": {"password": "Temp-1234!", "forceChangePasswordNextSignIn": True}})
case("SharePoint: list items with expanded fields", "n8n-nodes-base.microsoftSharePoint", "microsoftSharePointOAuth2Api", OA,
     {"resource": "item", "operation": "getAll", "siteId": "S1", "listId": "L1", "limit": 7}, "GET", "graph/sites/S1/lists/L1/items", lambda e: e["query"] == {"expand": "fields", "$top": "7"})
case("AWS Cognito: list users (service cognito-idp)", "n8n-nodes-base.awsCognito", "aws", AWS,
     {"resource": "user", "operation": "getAll", "userPoolId": "eu-central-1_X"}, "POST", "aws-cognito",
     lambda e: e["h"]["x-amz-target"] == "AWSCognitoIdentityProviderService.ListUsers" and sigv4_ok(e, "POST", "aws-cognito/", "cognito-idp"))
case("AWS Certificate Manager: list with status filter", "n8n-nodes-base.awsCertificateManager", "aws", AWS,
     {"resource": "certificate", "operation": "getAll", "statuses": "ISSUED, EXPIRED"}, "POST", "aws-acm",
     lambda e: e["body"] == {"CertificateStatuses": ["ISSUED", "EXPIRED"]} and sigv4_ok(e, "POST", "aws-acm/", "acm"))
case("Affinity: person with email list", "n8n-nodes-base.affinity", "affinityApi", {"apiKey": "afk"},
     {"resource": "person", "operation": "create", "first_name": "Ada", "last_name": "L", "emails": "a@x.io, b@x.io"}, "POST", "af/persons",
     lambda e: e["auth"] == basic("", "afk") and e["body"] == {"first_name": "Ada", "last_name": "L", "emails": ["a@x.io", "b@x.io"]})
case("APITemplate.io: image (template in query, JSON body)", "n8n-nodes-base.apiTemplateIo", "apiTemplateIoApi", {"apiKey": "atk"},
     {"resource": "image", "operation": "create", "template_id": "t1", "data": '{"overrides": [{"name": "text_1", "text": "Hi"}]}'}, "POST", "at/create-image",
     lambda e: e["h"]["x-api-key"] == "atk" and e["query"] == {"template_id": "t1"} and e["body"] == {"overrides": [{"name": "text_1", "text": "Hi"}]})
case("Beeminder: datapoint", "n8n-nodes-base.beeminder", "beeminderApi", {"authToken": "bt"},
     {"resource": "datapoint", "operation": "create", "goalName": "pushups", "value": 25, "comment": "morning"}, "POST", "bm/users/me/goals/pushups/datapoints.json",
     lambda e: e["query"] == {"auth_token": "bt"} and e["body"] == {"value": 25, "comment": "morning"})
case("BambooHR: employee with fields", "n8n-nodes-base.bambooHr", "bambooHrApi", {"subdomain": "acme", "apiKey": "bk"},
     {"resource": "employee", "operation": "get", "employeeId": "7"}, "GET", "bh/acme/v1/employees/7",
     lambda e: e["auth"] == basic("bk", "x") and e["query"] == {"fields": "firstName,lastName,workEmail,jobTitle"})
case("Bitwarden: members via client credentials", "n8n-nodes-base.bitwarden", "bitwardenApi", OA,
     {"resource": "member", "operation": "getAll"}, "GET", "bw/members", lambda e: e["auth"] == BEARER)
case("Chargebee: customer as form body", "n8n-nodes-base.chargebee", "chargebeeApi", {"accountName": "acme", "apiKey": "cbk"},
     {"resource": "customer", "operation": "create", "first_name": "Ada", "email": "a@x.io"}, "POST", "cb/customers",
     lambda e: e["auth"] == basic("cbk", "") and e["body"] == {"first_name": "Ada", "email": "a@x.io"})
case("Cockpit: entries with filter", "n8n-nodes-base.cockpit", "cockpitApi", {"url": f"{M}/ck", "accessToken": "ckt"},
     {"resource": "collection", "operation": "getEntries", "collection": "posts", "filter": '{"published": true}'}, "POST", "ck/api/collections/get/posts",
     lambda e: e["query"] == {"token": "ckt"} and e["body"] == {"filter": {"published": True}})
case("Demio: register (key and secret headers)", "n8n-nodes-base.demio", "demioApi", {"apiKey": "dk", "apiSecret": "ds"},
     {"resource": "event", "operation": "register", "id": "e1", "name": "Ada", "email": "a@x.io"}, "PUT", "dm/event/register",
     lambda e: e["h"]["api-key"] == "dk" and e["h"]["api-secret"] == "ds" and e["body"] == {"id": "e1", "name": "Ada", "email": "a@x.io"})
case("DHL: tracking", "n8n-nodes-base.dhl", "dhlApi", {"apiKey": "dhk"},
     {"resource": "shipment", "operation": "get", "trackingNumber": "00340434"}, "GET", "dhl/track/shipments",
     lambda e: e["h"]["dhl-api-key"] == "dhk" and e["query"] == {"trackingNumber": "00340434", "language": "de"})
case("Drift: contact attributes", "n8n-nodes-base.drift", "driftApi", {"accessToken": "drt"},
     {"resource": "contact", "operation": "create", "email": "a@x.io"}, "POST", "dr/contacts", lambda e: e["body"] == {"attributes": {"email": "a@x.io"}})
case("Freshworks CRM: contact (Token token= header)", "n8n-nodes-base.freshworksCrm", "freshworksCrmApi", {"domain": "acme", "apiKey": "fwk"},
     {"resource": "contact", "operation": "create", "lastName": "Lovelace", "email": "a@x.io"}, "POST", "fw/contacts",
     lambda e: e["auth"] == "Token token=fwk" and e["body"] == {"contact": {"last_name": "Lovelace", "emails": "a@x.io"}})
case("GoTo Webinar: webinars of the organizer", "n8n-nodes-base.goToWebinar", "goToWebinarOAuth2Api", dict(OA, organizerKey="ORG1"),
     {"resource": "webinar", "operation": "getAll", "fromTime": "2026-11-01T00:00:00Z", "toTime": "2026-12-01T00:00:00Z"}, "GET", "gtw/ORG1/webinars",
     lambda e: e["auth"] == BEARER and e["query"]["fromTime"] == "2026-11-01T00:00:00Z")
case("Help Scout: customer (client credentials)", "n8n-nodes-base.helpScout", "helpScoutOAuth2Api", OA,
     {"resource": "customer", "operation": "create", "firstName": "Ada", "email": "a@x.io"}, "POST", "hs/customers",
     lambda e: e["auth"] == BEARER and e["body"] == {"firstName": "Ada", "emails": [{"type": "work", "value": "a@x.io"}]})
case("Invoice Ninja: client (token + XHR header)", "n8n-nodes-base.invoiceNinja", "invoiceNinjaApi", {"url": f"{M}/inj", "apiToken": "int"},
     {"resource": "client", "operation": "create", "name": "Acme", "email": "a@acme.io"}, "POST", "inj/api/v1/clients",
     lambda e: e["h"]["x-api-token"] == "int" and e["h"]["x-requested-with"] == "XMLHttpRequest" and e["body"] == {"name": "Acme", "contacts": [{"email": "a@acme.io"}]})
case("Keap: contact with EMAIL1 field", "n8n-nodes-base.keap", "keapOAuth2Api", OA,
     {"resource": "contact", "operation": "create", "givenName": "Ada", "email": "a@x.io"}, "POST", "kp/contacts",
     lambda e: e["body"] == {"given_name": "Ada", "email_addresses": [{"email": "a@x.io", "field": "EMAIL1"}]})
case("Mailcheck: single email (colon path)", "n8n-nodes-base.mailcheck", "mailcheckApi", {"apiKey": "mck"},
     {"resource": "email", "operation": "check", "email": "a@x.io"}, "POST", "mck/singleEmail:check", lambda e: e["auth"] == "Bearer mck" and e["body"] == {"email": "a@x.io"})
case("Mandrill: send (key inside the body)", "n8n-nodes-base.mandrill", "mandrillApi", {"apiKey": "mdk"},
     {"resource": "message", "operation": "send", "fromEmail": "a@x.io", "toEmail": "b@y.io", "subject": "S", "text": "T"}, "POST", "mdr/messages/send",
     lambda e: e["body"] == {"key": "mdk", "message": {"from_email": "a@x.io", "to": [{"email": "b@y.io"}], "subject": "S", "text": "T"}})
case("NASA: picture of the day", "n8n-nodes-base.nasa", "nasaApi", {"apiKey": "nk"},
     {"resource": "astronomyPictureOfTheDay", "operation": "get", "date": "2026-11-07"}, "GET", "nasa/planetary/apod", lambda e: e["query"] == {"api_key": "nk", "date": "2026-11-07"})
case("npm: package metadata without a token", "n8n-nodes-base.npm", "npmApi", {"registryUrl": f"{M}/npm"},
     {"resource": "package", "operation": "getMetadata", "packageName": "flowwolf"}, "GET", "npm/flowwolf", lambda e: e["auth"] == "")
case("Okta: create user (SSWS header)", "n8n-nodes-base.okta", "oktaApi", {"url": f"{M}/okta", "apiToken": "ot"},
     {"resource": "user", "operation": "create", "firstName": "Ada", "lastName": "L", "email": "a@x.io"}, "POST", "okta/api/v1/users",
     lambda e: e["auth"] == "SSWS ot" and e["body"] == {"profile": {"firstName": "Ada", "lastName": "L", "email": "a@x.io", "login": "a@x.io"}})
case("PayPal: payout (host from the credential)", "n8n-nodes-base.payPal", "payPalApi", dict(OA, apiHost="paypal.mock"),
     {"resource": "payout", "operation": "create", "batchId": "b1", "receiver": "r@x.io", "amount": "5.00", "currency": "EUR"}, "POST", "pp2/v1/payments/payouts",
     lambda e: e["auth"] == BEARER and e["body"] == {"sender_batch_header": {"sender_batch_id": "b1"},
                                                    "items": [{"recipient_type": "EMAIL", "receiver": "r@x.io", "amount": {"value": "5.00", "currency": "EUR"}}]})
mock("POST", "pbin/bin")
ex = run_manual({"name": "pbin", "nodes": [node("Start", "n8n-nodes-base.manualTrigger", 0), node("N", "n8n-nodes-base.postBin", 200, {"resource": "bin", "operation": "create"})],
                 "connections": chain("Start", "N")})
check("PostBin: create bin without credentials", ex["status"] == "success" and out(ex, "N")[0]["auth"] == "", f"{ex['data']['resultData'].get('error')}")
case("Salesmate: contact (session + link name headers)", "n8n-nodes-base.salesmate", "salesmateApi", {"url": "acme.salesmate.io", "sessionToken": "smk"},
     {"resource": "contact", "operation": "create", "lastName": "L", "email": "a@x.io"}, "POST", "smt/contact/v4",
     lambda e: e["h"]["sessiontoken"] == "smk" and e["h"]["x-linkname"] == "acme.salesmate.io" and e["body"] == {"lastName": "L", "email": "a@x.io"})
case("Stackby: create row", "n8n-nodes-base.stackby", "stackbyApi", {"apiKey": "sbk"},
     {"resource": "row", "operation": "create", "stackId": "st1", "table": "Leads", "fields": '{"Name": "Ada"}'}, "POST", "sby/rowcreate/st1/Leads",
     lambda e: e["h"]["api-key"] == "sbk" and e["body"] == {"records": [{"field": {"Name": "Ada"}}]})
case("SyncroMSP: ticket with numeric customer", "n8n-nodes-base.syncroMsp", "syncroMspApi", {"subdomain": "acme", "apiKey": "syk"},
     {"resource": "ticket", "operation": "create", "customer_id": 12, "subject": "Printer"}, "POST", "syn/tickets",
     lambda e: e["body"] == {"customer_id": 12, "subject": "Printer", "problem_type": "Other"})
case("Twist: comment", "n8n-nodes-base.twist", "twistOAuth2Api", OA,
     {"resource": "comment", "operation": "create", "thread_id": 99, "content": "Done"}, "POST", "tw/comments/add", lambda e: e["body"] == {"thread_id": 99, "content": "Done"})
case("Uplead: person by email", "n8n-nodes-base.uplead", "upleadApi", {"apiKey": "ulk"},
     {"resource": "person", "operation": "enrich", "email": "a@x.io"}, "GET", "ul/person-search", lambda e: e["auth"] == "ulk" and e["query"] == {"email": "a@x.io"})
case("UptimeRobot: new monitor as form with api_key", "n8n-nodes-base.uptimeRobot", "uptimeRobotApi", {"apiKey": "urk"},
     {"resource": "monitor", "operation": "create", "friendlyName": "Site", "url": "https://x.io"}, "POST", "ur/newMonitor",
     lambda e: e["body"] == {"api_key": "urk", "format": "json", "type": "1", "friendly_name": "Site", "url": "https://x.io"})
case("Vero: track event", "n8n-nodes-base.vero", "veroApi", {"authToken": "vt"},
     {"resource": "event", "operation": "track", "id": "u1", "eventName": "signup", "data": '{"plan": "pro"}'}, "POST", "vr/events/track",
     lambda e: e["body"] == {"auth_token": "vt", "identity": {"id": "u1"}, "event_name": "signup", "data": {"plan": "pro"}})

# ---------------------------------------------------------------- batch 11 integrations
case("Action Network: person (OSDI token header)", "n8n-nodes-base.actionNetwork", "actionNetworkApi", {"apiKey": "ank"},
     {"resource": "person", "operation": "create", "email": "a@x.io", "givenName": "Ada"}, "POST", "an/people",
     lambda e: e["h"]["osdi-api-token"] == "ank" and e["body"] == {"person": {"email_addresses": [{"address": "a@x.io"}], "given_name": "Ada"}})
case("Adalo: records of a collection in the app", "n8n-nodes-base.adalo", "adaloApi", {"appId": "APP1", "apiKey": "adk"},
     {"resource": "collection", "operation": "getAll", "collectionId": "C1", "limit": 5}, "GET", "ad/APP1/collections/C1", lambda e: e["auth"] == "Bearer adk" and e["query"] == {"limit": "5"})
case("Clearbit: company by domain", "n8n-nodes-base.clearbit", "clearbitApi", {"apiKey": "sk_c"},
     {"resource": "company", "operation": "enrich", "domain": "n8n.io"}, "GET", "cbt/companies/find", lambda e: e["auth"] == "Bearer sk_c" and e["query"] == {"domain": "n8n.io"})
case("Cortex: run analyzer with TLP", "n8n-nodes-base.cortex", "cortexApi", {"host": f"{M}/cx", "apiKey": "cxk"},
     {"resource": "analyzer", "operation": "execute", "analyzerId": "Abuse_1", "data": "1.2.3.4", "dataType": "ip", "tlp": 2}, "POST", "cx/api/analyzer/Abuse_1/run",
     lambda e: e["body"] == {"data": "1.2.3.4", "dataType": "ip", "tlp": 2})
case("E-goi: contact base fields", "n8n-nodes-base.egoi", "egoiApi", {"apiKey": "egk"},
     {"resource": "contact", "operation": "create", "listId": "7", "email": "a@x.io", "firstName": "Ada"}, "POST", "eg/lists/7/contacts",
     lambda e: e["h"]["apikey"] == "egk" and e["body"] == {"base": {"email": "a@x.io", "first_name": "Ada"}})
case("Elastic Security: case (kbn-xsrf header, fixed connector)", "n8n-nodes-base.elasticSecurity", "elasticSecurityApi", {"baseUrl": f"{M}/es", "username": "elastic", "password": "pw"},
     {"resource": "case", "operation": "create", "title": "Phishing", "description": "Reported mail"}, "POST", "es/api/cases",
     lambda e: e["h"]["kbn-xsrf"] == "true" and e["body"]["connector"]["type"] == ".none" and e["body"]["owner"] == "securitySolution")
case("Jina AI: reader keeps the target as path", "n8n-nodes-base.jinaAi", "jinaAiApi", {"apiKey": "jk"},
     {"resource": "reader", "operation": "read", "url": "example.com/article"}, "GET", "jr/example.com/article", lambda e: e["auth"] == "Bearer jk")
case("LingvaNex: translate", "n8n-nodes-base.lingvaNex", "lingvaNexApi", {"apiKey": "lk"},
     {"resource": "translation", "operation": "translate", "data": "Hallo", "to": "en_GB"}, "POST", "lx/translate",
     lambda e: e["body"] == {"data": "Hallo", "to": "en_GB", "platform": "api"})
case("MSG91: flow SMS", "n8n-nodes-base.msg91", "msg91Api", {"authkey": "m9"},
     {"resource": "sms", "operation": "send", "templateId": "T1", "mobiles": "4917"}, "POST", "m91/flow",
     lambda e: e["h"]["authkey"] == "m9" and e["body"] == {"template_id": "T1", "recipients": [{"mobiles": "4917"}]})
case("Nextcloud: create user (OCS header, form body)", "n8n-nodes-base.nextCloud", "nextCloudApi", {"url": f"{M}/nc", "user": "admin", "password": "pw"},
     {"resource": "user", "operation": "create", "userid": "ada", "email": "a@x.io"}, "POST", "nc/ocs/v1.php/cloud/users",
     lambda e: e["h"]["ocs-apirequest"] == "true" and e["body"] == {"userid": "ada", "email": "a@x.io"})
case("One Simple API: screenshot as JSON", "n8n-nodes-base.oneSimpleApi", "oneSimpleApi", {"apiToken": "ot"},
     {"resource": "website", "operation": "screenshot", "url": "https://n8n.io"}, "GET", "osa/screenshot", lambda e: e["query"] == {"token": "ot", "url": "https://n8n.io", "output": "json"})
case("uProc: run a tool", "n8n-nodes-base.uproc", "uprocApi", {"email": "a@x.io", "apiKey": "uk"},
     {"resource": "tool", "operation": "process", "processor": "check-email-exists", "params": '{"email": "b@y.io"}'}, "POST", "up/process",
     lambda e: e["auth"] == basic("a@x.io", "uk") and e["body"] == {"processor": "check-email-exists", "params": {"email": "b@y.io"}})
case("Graph Security: resolve alert", "n8n-nodes-base.microsoftGraphSecurity", "microsoftGraphSecurityOAuth2Api", OA,
     {"resource": "alert", "operation": "update", "alertId": "da1", "status": "resolved"}, "PATCH", "graph/security/alerts_v2/da1",
     lambda e: e["auth"] == BEARER and e["body"] == {"status": "resolved"})
case("Venafi: certificates", "n8n-nodes-base.venafiTlsProtectCloud", "venafiTlsProtectCloudApi", {"apiKey": "vk"},
     {"resource": "certificate", "operation": "getAll"}, "GET", "vf/outagedetection/v1/certificates", lambda e: e["h"]["tppl-api-key"] == "vk")

# ---------------------------------------------------------------- Slack, Telegram, Discord, GitHub (full n8n operation sets)
def fixed(method, path, body, kind="json"):
    """A mock with a fixed answer (JSON text or plain text)."""
    if (method, path) in mocks:
        return
    mocks.add((method, path))
    params = {"respondWith": "json", "responseBody": body} if kind == "json" else {"respondWith": "text", "responseBody": body}
    wf = rest("POST", "/workflows", {"name": f"c {path}", "nodes": [
        node("Hook", "n8n-nodes-base.webhook", 0, {"httpMethod": method, "path": f"c/{path}", "responseMode": "responseNode"}, 2),
        node("Reply", "n8n-nodes-base.respondToWebhook", 200, params, 1.1)], "connections": chain("Hook", "Reply")})
    rest("POST", f"/workflows/{wf['id']}/activate")


def run_with(label, kind, cred_kind, cred_data, params, items=None):
    """Runs one node (optionally after a Code node that emits `items`) and returns the execution."""
    nodes = [node("Start", "n8n-nodes-base.manualTrigger", 0)]
    names = ["Start"]
    if items is not None:
        nodes.append(node("Items", "n8n-nodes-base.code", 100, {"jsCode": f"return {json.dumps(items)};"}, 2))
        names.append("Items")
    nodes.append(node("N", kind, 200, params, credentials=cred(cred_kind, cred_data)))
    return run_manual({"name": label, "nodes": nodes, "connections": chain(*names, "N")})


SL = ("n8n-nodes-base.slack", "slackApi", {"accessToken": "xoxb-1"})
RL = lambda v: {"__rl": True, "mode": "id", "value": v}  # noqa: E731
case("Slack: create private channel (visibility maps to is_private)", *SL, {"resource": "channel", "operation": "create", "channelId": "ops", "channelVisibility": "private"},
     "POST", "sl/conversations.create", lambda e: e["auth"] == "Bearer xoxb-1" and e["body"] == {"name": "ops", "is_private": True})
case("Slack: list channels with filters as query", *SL, {"resource": "channel", "operation": "getAll", "limit": 5, "filters": {"types": "private_channel", "excludeArchived": True}},
     "GET", "sl/conversations.list", lambda e: e["query"] == {"limit": "5", "types": "private_channel", "exclude_archived": "true"})
case("Slack: invite users (resource locator, user list joined)", *SL, {"resource": "channel", "operation": "invite", "channelId": RL("C1"), "userIds": ["U1", "U2"]},
     "POST", "sl/conversations.invite", lambda e: e["body"] == {"channel": "C1", "users": "U1,U2"})
case("Slack: add reaction", *SL, {"resource": "reaction", "operation": "add", "channelId": RL("C1"), "timestamp": "1663233118.856619", "name": "robot_face"},
     "POST", "sl/reactions.add", lambda e: e["body"] == {"channel": "C1", "timestamp": "1663233118.856619", "name": "robot_face"})
case("Slack: look up user by email", *SL, {"resource": "user", "operation": "lookupByEmail", "email": "ada@x.io"},
     "GET", "sl/users.lookupByEmail", lambda e: e["query"] == {"email": "ada@x.io"})
case("Slack: update user group members", *SL, {"resource": "userGroup", "operation": "updateUsers", "userGroupId": "S1", "users": ["U1", "U3"]},
     "POST", "sl/usergroups.users.update", lambda e: e["body"] == {"usergroup": "S1", "users": "U1,U3"})
case("Slack: send blocks to a user with a thread reply", *SL, {"resource": "message", "operation": "post", "select": "user", "user": RL("U9"), "messageType": "block",
     "blocksUi": '{"blocks": [{"type": "divider"}]}', "text": "fallback", "otherOptions": {"thread_ts": {"replyValues": {"thread_ts": "1.2"}}, "includeLinkToWorkflow": False}},
     "POST", "sl/chat.postMessage", lambda e: e["body"] == {"channel": "U9", "text": "fallback", "blocks": [{"type": "divider"}], "thread_ts": "1.2"})
case("Slack: schedule a message (post_at in Unix time)", *SL, {"resource": "message", "operation": "schedule", "select": "channel", "channelId": RL("C1"),
     "text": "later", "postAt": "2030-01-01T00:00:00Z"}, "POST", "sl/chat.scheduleMessage", lambda e: e["body"] == {"channel": "C1", "text": "later", "post_at": 1893456000})
fixed("POST", "sl/conversations.archive", '{"ok": false, "error": "channel_not_found"}')
ex = run_with("Slack error", *SL, {"resource": "channel", "operation": "archive", "channelId": RL("C404")})
check("Slack: {ok: false} answers fail the node with Slack's error", ex["status"] == "error" and "channel_not_found" in str(ex["data"]["resultData"].get("error")), str(ex["data"]["resultData"].get("error")))
fixed("GET", "sl/files.getUploadURLExternal", f'{{"ok": true, "upload_url": "{M}/slup", "file_id": "F1"}}')
mock("POST", "slup")
mock("POST", "sl/files.completeUploadExternal")
ex = run_with("Slack upload", *SL, {"resource": "file", "operation": "upload", "binaryData": False, "fileContent": "hello",
                                    "options": {"fileName": "a.txt", "channelIds": ["C1"], "initialComment": "here"}})
up = out(ex, "N")[0] if ex["status"] == "success" else {}
check("Slack: file upload uses the external upload flow and shares the file", ex["status"] == "success" and up.get("body") == {
    "files": [{"id": "F1", "title": "a.txt"}], "channels": "C1", "initial_comment": "here"}, f"{ex['data']['resultData'].get('error')} {up}")

TG = ("n8n-nodes-base.telegram", "telegramApi", {"accessToken": "T1"})
case("Telegram: send location", *TG, {"resource": "message", "operation": "sendLocation", "chatId": "42", "latitude": 52.5, "longitude": 13.4},
     "POST", "tg/botT1/sendLocation", lambda e: e["body"] == {"chat_id": "42", "latitude": 52.5, "longitude": 13.4})
case("Telegram: get chat member", *TG, {"resource": "chat", "operation": "member", "chatId": "42", "userId": "7"},
     "POST", "tg/botT1/getChatMember", lambda e: e["body"] == {"chat_id": "42", "user_id": "7"})
case("Telegram: answer callback query", *TG, {"resource": "callback", "operation": "answerQuery", "queryId": "q1", "additionalFields": {"text": "Done", "show_alert": True}},
     "POST", "tg/botT1/answerCallbackQuery", lambda e: e["body"] == {"callback_query_id": "q1", "text": "Done", "show_alert": True})
case("Telegram: send photo by URL", *TG, {"resource": "message", "operation": "sendPhoto", "chatId": "42", "file": "https://x.io/a.png", "additionalFields": {"caption": "Hi"}},
     "POST", "tg/botT1/sendPhoto", lambda e: e["body"] == {"chat_id": "42", "photo": "https://x.io/a.png", "caption": "Hi"})
mock("POST", "tg/botT1/sendDocument")
pic = [{"json": {}, "binary": {"data": {"data": base64.b64encode(b"%PDF-1").decode(), "mimeType": "application/pdf", "fileName": "r.pdf"}}}]
ex = run_with("Telegram upload", *TG, {"resource": "message", "operation": "sendDocument", "chatId": "42", "binaryData": True, "binaryPropertyName": "data",
                                       "additionalFields": {"caption": "Report"}}, items=pic)
e = out(ex, "N")[0] if ex["status"] == "success" else {}
check("Telegram: binary file goes up as multipart with the other fields", ex["status"] == "success" and e.get("body", {}).get("chat_id") == "42"
      and e["body"].get("caption") == "Report" and "multipart/form-data" in e["h"].get("content-type", ""), f"{ex['data']['resultData'].get('error')} {e}")
fixed("POST", "tg/botT1/getFile", '{"ok": true, "result": {"file_id": "f1", "file_path": "documents/a.txt"}}')
fixed("GET", "tg/file/botT1/documents/a.txt", "hello file", kind="text")
ex = run_with("Telegram download", *TG, {"resource": "file", "operation": "get", "fileId": "f1"})
item = ex["data"]["resultData"]["runData"]["N"][-1]["data"]["main"][0][0] if ex["status"] == "success" else {}
check("Telegram: get file downloads it as binary", item.get("json", {}).get("file_path") == "documents/a.txt"
      and base64.b64decode(item.get("binary", {}).get("data", {}).get("data", "")) == b"hello file", f"{ex['data']['resultData'].get('error')} {item}")

DC = ("n8n-nodes-base.discord", "discordBotApi", {"botToken": "dt"})
case("Discord: send a message as bot (embeds parsed)", *DC, {"resource": "message", "operation": "send", "guildId": RL("g1"), "channelId": RL("123"), "content": "Hi",
     "embeds": '[{"title": "T"}]', "options": {"tts": True}}, "POST", "dsc/channels/123/messages",
     lambda e: e["auth"] == "Bot dt" and e["body"] == {"content": "Hi", "embeds": [{"title": "T"}], "tts": True})
case("Discord: add a role to a member", *DC, {"resource": "member", "operation": "roleAdd", "guildId": RL("g1"), "userId": RL("u1"), "role": "r1"},
     "PUT", "dsc/guilds/g1/members/u1/roles/r1", lambda e: e["auth"] == "Bot dt")
case("Discord: create a voice channel (type as number)", *DC, {"resource": "channel", "operation": "create", "guildId": RL("g1"), "name": "Talk", "type": "2"},
     "POST", "dsc/guilds/g1/channels", lambda e: e["body"] == {"name": "Talk", "type": 2})
case("Discord: workflows without resource keep posting through the webhook", "n8n-nodes-base.discord", "discordWebhookApi", {"webhookUri": f"{M}/dwh"},
     {"content": "Legacy"}, "POST", "dwh", lambda e: e["body"] == {"content": "Legacy"} and e["query"] == {"wait": "true"})

GH = ("n8n-nodes-base.github", "githubApi", {"accessToken": "ghp", "server": f"{M}/gh"})
fixed("GET", "gh/repos/o/r/contents/docs/a.md", '{"sha": "abc"}')
case("GitHub: edit a file (current sha fetched, content base64)", *GH, {"resource": "file", "operation": "edit", "owner": "o", "repository": "r", "filePath": "docs/a.md",
     "fileContent": "hi", "commitMessage": "m"}, "PUT", "gh/repos/o/r/contents/docs/a.md",
     lambda e: e["auth"] == "Bearer ghp" and e["body"] == {"content": base64.b64encode(b"hi").decode(), "message": "m", "sha": "abc"})
case("GitHub: squash-merge a pull request", *GH, {"resource": "pullRequest", "operation": "merge", "owner": "o", "repository": "r", "pullRequestNumber": 7,
     "additionalFields": {"merge_method": "squash"}}, "PUT", "gh/repos/o/r/pulls/7/merge", lambda e: e["body"] == {"merge_method": "squash"})
case("GitHub: dispatch a workflow with inputs", *GH, {"resource": "workflow", "operation": "dispatch", "owner": "o", "repository": "r", "workflowId": RL("ci.yml"),
     "ref": "main", "inputs": '{"env": "prod"}'}, "POST", "gh/repos/o/r/actions/workflows/ci.yml/dispatches", lambda e: e["body"] == {"ref": "main", "inputs": {"env": "prod"}})
case("GitHub: request changes in a review", *GH, {"resource": "review", "operation": "create", "owner": "o", "repository": "r", "pullRequestNumber": 7,
     "event": "REQUEST_CHANGES", "body": "Please fix"}, "POST", "gh/repos/o/r/pulls/7/reviews", lambda e: e["body"] == {"event": "REQUEST_CHANGES", "body": "Please fix"})

# ---------------------------------------------------------------- Gmail and Google Drive
def raw_of(e):
    return base64.urlsafe_b64decode(e["body"]["raw"] + "=" * (-len(e["body"]["raw"]) % 4)).decode()


def first_item(ex):
    return ex["data"]["resultData"]["runData"]["N"][-1]["data"]["main"][0][0] if ex["status"] == "success" else {}


def b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


GM = ("n8n-nodes-base.gmail", "gmailOAuth2", OA)
att = [{"json": {}, "binary": {"data": {"data": base64.b64encode(b"report").decode(), "mimeType": "text/plain", "fileName": "r.txt"}}}]
mock("POST", "gm/messages/send")
ex = run_with("Gmail send", *GM, {"resource": "message", "operation": "send", "sendTo": "a@x.io", "subject": "Bericht", "emailType": "text", "message": "Anbei",
                                  "options": {"ccList": "c@x.io", "attachmentsUi": {"attachmentsBinary": [{"property": "data"}]}}}, items=att)
e = out(ex, "N")[0] if ex["status"] == "success" else {}
raw = raw_of(e) if e else ""
check("Gmail: send builds a MIME mail with CC and a binary attachment", e.get("auth") == BEARER and "To: a@x.io\r\n" in raw and "Cc: c@x.io\r\n" in raw
      and "multipart/mixed" in raw and 'filename="r.txt"' in raw and base64.b64encode(b"report").decode() in raw, f"{ex['data']['resultData'].get('error')} {raw[:300]}")
fixed("GET", "gm/messages/m1", json.dumps({"id": "m1", "threadId": "t1", "payload": {"headers": [
    {"name": "Subject", "value": "Hello"}, {"name": "From", "value": "Bob <b@x.io>"}, {"name": "To", "value": "me@x.io"}, {"name": "Message-ID", "value": "<x1@mail>"}]}}))
ex = run_with("Gmail reply", *GM, {"resource": "message", "operation": "reply", "messageId": "m1", "emailType": "text", "message": "Thanks", "options": {"replyToSenderOnly": True}})
e = out(ex, "N")[0] if ex["status"] == "success" else {}
raw = raw_of(e) if e else ""
check("Gmail: reply stays in the thread with Re:, In-Reply-To and References", e.get("body", {}).get("threadId") == "t1" and "To: Bob <b@x.io>\r\n" in raw
      and "Subject: Re: Hello\r\n" in raw and "In-Reply-To: <x1@mail>\r\n" in raw and "References: <x1@mail>\r\n" in raw, f"{ex['data']['resultData'].get('error')} {raw[:300]}")
fixed("GET", "gm/messages", '{"messages": [{"id": "m2"}]}')
fixed("GET", "gm/messages/m2", '{"id": "m2", "threadId": "t2", "snippet": "Hi", "labelIds": ["INBOX"], "payload": {"headers": [{"name": "Subject", "value": "S2"}]}}')
ex = run_with("Gmail list", *GM, {"resource": "message", "operation": "getAll", "limit": 10, "filters": {"readStatus": "unread"}})
o = out(ex, "N") if ex["status"] == "success" else []
check("Gmail: get many fetches each message and simplifies it", len(o) == 1 and o[0]["Subject"] == "S2" and o[0]["snippet"] == "Hi", f"{ex['data']['resultData'].get('error')} {o}")
fixed("GET", "gm/messages/m4", json.dumps({"id": "m4", "threadId": "t4", "payload": {"mimeType": "multipart/mixed", "headers": [{"name": "Subject", "value": "Rechnung"}], "parts": [
    {"mimeType": "text/plain", "filename": "", "body": {"data": b64u(b"Hallo")}}, {"mimeType": "application/pdf", "filename": "re.pdf", "body": {"data": b64u(b"%PDF")}}]}}))
ex = run_with("Gmail get full", *GM, {"resource": "message", "operation": "get", "messageId": "m4", "simple": False, "options": {"downloadAttachments": True}})
it = first_item(ex)
check("Gmail: full message gives text, subject and attachments as binary", it.get("json", {}).get("text") == "Hallo" and it["json"]["subject"] == "Rechnung"
      and base64.b64decode(it.get("binary", {}).get("attachment_0", {}).get("data", "")) == b"%PDF", f"{ex['data']['resultData'].get('error')} {it}")
case("Gmail: mark as read removes the UNREAD label", *GM, {"resource": "message", "operation": "markAsRead", "messageId": "m3"}, "POST", "gm/messages/m3/modify",
     lambda e: e["body"] == {"removeLabelIds": ["UNREAD"]})
case("Gmail: create label with visibility", *GM, {"resource": "label", "operation": "create", "name": "VIP", "options": {"labelListVisibility": "labelShow"}}, "POST", "gm/labels",
     lambda e: e["body"] == {"name": "VIP", "labelListVisibility": "labelShow"})

GD = ("n8n-nodes-base.googleDrive", "googleDriveOAuth2Api", OA)
mock("POST", "gdu")
ex = run_with("Drive upload", *GD, {"resource": "file", "operation": "upload", "name": "r.txt", "folderId": {"__rl": True, "mode": "id", "value": "fold1"}}, items=att)
e = out(ex, "N")[0] if ex["status"] == "success" else {}
check("Google Drive: upload sends metadata and content as multipart/related", e.get("query", {}).get("uploadType") == "multipart"
      and "multipart/related" in e.get("h", {}).get("content-type", ""), f"{ex['data']['resultData'].get('error')} {e.get('query')}")
fixed("GET", "gd/files/f1", '{"id": "f1", "name": "Plan", "mimeType": "application/vnd.google-apps.document"}')
fixed("GET", "gd/files/f1/export", "<p>Plan</p>", kind="text")
ex = run_with("Drive export", *GD, {"resource": "file", "operation": "download", "fileId": "https://docs.google.com/document/d/f1/edit"})
it = first_item(ex)
b = it.get("binary", {}).get("data", {})
check("Google Drive: Google Docs are exported (HTML by default) and named", b.get("fileName") == "Plan.html" and base64.b64decode(b.get("data", "")) == b"<p>Plan</p>",
      f"{ex['data']['resultData'].get('error')} {b}")
mock("PATCH", "gd/files/f2")
ex = run_with("Drive delete", *GD, {"resource": "file", "operation": "deleteFile", "fileId": "f2"})
check("Google Drive: delete moves to the trash unless permanent", ex["status"] == "success" and out(ex, "N")[0] == {"fileId": "f2", "success": True},
      str(ex["data"]["resultData"].get("error")))
fixed("GET", "gd/files/f3", '{"parents": ["old1"]}')
case("Google Drive: move swaps the parents", *GD, {"resource": "file", "operation": "move", "fileId": "f3", "folderId": "fold9"}, "PATCH", "gd/files/f3",
     lambda e: e["query"]["addParents"] == "fold9" and e["query"]["removeParents"] == "old1")
case("Google Drive: share with a user (n8n permission collection)", *GD, {"resource": "file", "operation": "share", "fileId": "https://drive.google.com/file/d/f4/view",
     "permissionsUi": {"permissionsValues": {"role": "writer", "type": "user", "emailAddress": "a@x.io"}}, "options": {"sendNotificationEmail": False}},
     "POST", "gd/files/f4/permissions", lambda e: e["body"] == {"role": "writer", "type": "user", "emailAddress": "a@x.io"} and e["query"]["sendNotificationEmail"] == "false")
case("Google Drive: search builds the query from name and filters", *GD, {"resource": "fileFolder", "operation": "search", "queryString": "rep", "limit": 5,
     "filter": {"folderId": "fold1", "whatToSearch": "files"}}, "GET", "gd/files",
     lambda e: e["query"]["q"] == "name contains 'rep' and 'fold1' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed = false" and e["query"]["pageSize"] == "5")
case("Google Drive: create folder in a folder", *GD, {"resource": "folder", "operation": "create", "name": "Reports", "folderId": "fold1"}, "POST", "gd/files",
     lambda e: e["body"] == {"name": "Reports", "mimeType": "application/vnd.google-apps.folder", "parents": ["fold1"]})
case("Google Drive: copy into another folder", *GD, {"resource": "file", "operation": "copy", "fileId": "f5", "name": "Copy", "sameFolder": False, "folderId": "fold2"},
     "POST", "gd/files/f5/copy", lambda e: e["body"] == {"name": "Copy", "parents": ["fold2"]})
case("Google Drive: create a shared drive with a request id", *GD, {"resource": "drive", "operation": "create", "name": "Team"}, "POST", "gd/drives",
     lambda e: e["body"] == {"name": "Team"} and len(e["query"]["requestId"]) == 36)

print(f"\nall {L.passed} contract checks passed")
