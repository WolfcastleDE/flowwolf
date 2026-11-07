"""Generates src/nodes/integrations.json: declarative REST integrations.

Each operation describes the HTTP call; the Rust executor (src/nodes/declarative.rs) runs it.
Field spec: F(name, display, where, api=None, kind="string", ...)
  where: "path" (used as {name} in the path), "query", "body", or "none" (only used by templates)
  kind:  string | number | boolean | json | list (comma separated -> array) | options
Run: python tools/gen_integrations.py
"""

import json
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "src", "nodes", "integrations.json")


def F(name, display, where="body", api=None, kind="string", required=False, default=None, placeholder="", description="", options=None, rows=None, map=None):
    return dict(name=name, display=display, where=where, api=api or name, kind=kind, required=required, default=default,
                placeholder=placeholder, description=description, options=options, rows=rows, map=map)


def OP(resource, op, label, method, path, fields=(), extra=(), root=None, limit=None, static=None, static_query=None,
       body_from=None, transform=None, extra_target=None, description="", template=None, merge_json=None, headers=None, extra_name=None, fetch_sha=None):
    """template: JSON body with "{field}" / "{cred.x}" placeholders (blank values are dropped)."""
    return dict(resource=resource, op=op, label=label, method=method, path=path, fields=list(fields), extra=list(extra),
                root=root, limit=limit, static=static, static_query=static_query, body_from=body_from, transform=transform,
                extra_target=extra_target, description=description, template=template, merge_json=merge_json, headers=headers,
                extra_name=extra_name, fetch_sha=fetch_sha)


def prop_for(f, show):
    kind = f["kind"]
    p = {"displayName": f["display"], "name": f["name"], "type": {"list": "string", "csv": "string", "base64": "string", "options": "options"}.get(kind, kind),
         "default": f["default"] if f["default"] is not None else {"number": 0, "boolean": False, "json": "{}"}.get(kind, "")}
    if f["required"]:
        p["required"] = True
    if f["placeholder"]:
        p["placeholder"] = f["placeholder"]
    desc = f["description"] or ("Comma-separated list" if kind == "list" else "")
    if desc:
        p["description"] = desc
    if f["options"]:
        p["options"] = [{"name": o, "value": o} if isinstance(o, str) else {"name": o[0], "value": o[1]} for o in f["options"]]
        if p["default"] == "":
            p["default"] = p["options"][0]["value"]
    if f["rows"]:
        p["typeOptions"] = {"rows": f["rows"]}
    if show is not None:
        p["displayOptions"] = {"show": show}
    return p


def spec_of(f):
    return {"name": f["name"], "api": f["api"], "where": f["where"], "kind": f["kind"], **({"map": f["map"]} if f["map"] else {})}


def node(type_name, display, cred, base, auth, ops, color, icon, category, description, headers=None, body_type="json", version=1, extra_creds=(), error_flag=None, top=()):
    resources = []
    for o in ops:
        if o["resource"] not in resources:
            resources.append(o["resource"])
    title = lambda s: "".join(" " + c if c.isupper() else c for c in s).strip().title()
    props = list(top) + [{"displayName": "Resource", "name": "resource", "type": "options", "noDataExpression": True, "default": resources[0],
                          "options": [{"name": title(r), "value": r} for r in resources]}]
    for r in resources:
        rops = [o for o in ops if o["resource"] == r]
        props.append({"displayName": "Operation", "name": "operation", "type": "options", "noDataExpression": True, "default": rops[0]["op"],
                      "displayOptions": {"show": {"resource": [r]}},
                      "options": [{"name": o["label"], "value": o["op"], "description": o["description"] or o["label"]} for o in rops]})
    # One property per (field name, set of operations) keeps the form compact.
    seen = {}
    for o in ops:
        show_key = (o["resource"], o["op"])
        for f in o["fields"]:
            seen.setdefault((f["name"], json.dumps(prop_for(f, None), sort_keys=True)), (f, []))[1].append(show_key)
        if o["limit"]:
            for f in (F("returnAll", "Return All", "none", kind="boolean"), F("limit", "Limit", "none", kind="number", default=50)):
                seen.setdefault((f["name"], json.dumps(prop_for(f, None), sort_keys=True)), (f, []))[1].append(show_key)
    for (_, _), (f, keys) in seen.items():
        res = sorted({k[0] for k in keys})
        opsn = sorted({k[1] for k in keys})
        show = {"resource": res, "operation": opsn}
        if f["name"] == "limit":
            show["returnAll"] = [False]
        props.append(prop_for(f, show))
    for o in ops:
        if o["extra"]:
            name = o["extra_name"] or "additionalFields"
            props.append({"displayName": title(name) if name != "additionalFields" else "Additional Fields", "name": name, "type": "collection", "default": {},
                          "placeholder": "Add Field",
                          "displayOptions": {"show": {"resource": [o["resource"]], "operation": [o["op"]]}},
                          "options": [prop_for(f, None) for f in o["extra"]]})
    decl = {"credential": cred or "", "baseUrl": base, "auth": auth, "headers": headers or {}, "bodyType": body_type, "operations": {}, "service": display}
    if error_flag:
        decl["errorFlag"] = error_flag
    for o in ops:
        decl["operations"][f'{o["resource"]}:{o["op"]}'] = {k: v for k, v in {
            "method": o["method"], "path": o["path"], "fields": [spec_of(f) for f in o["fields"]],
            "extra": [spec_of(f) for f in o["extra"]], "extraTarget": o["extra_target"], "root": o["root"], "limit": o["limit"],
            "static": o["static"], "staticQuery": o["static_query"], "bodyFrom": o["body_from"], "transform": o["transform"],
            "template": o["template"], "mergeJson": o["merge_json"], "headers": o["headers"], "extraName": o["extra_name"],
            "fetchSha": o["fetch_sha"]}.items() if v not in (None, [], {})}
    creds = ([{"name": cred, "required": True}] if cred else []) + [{"name": c} for c in extra_creds]
    return {"name": type_name, "displayName": display, "group": ["output"], "category": category, "icon": icon, "color": color,
            "version": version, "defaults": {"name": display}, "description": description, "inputs": 1, "outputs": [""],
            "credentials": creds, "properties": props, "declarative": decl}


OWNER = F("owner", "Repository Owner", "path", required=True, placeholder="octocat")
REPO = F("repository", "Repository Name", "path", required=True, placeholder="hello-world")
ISSUE = F("issueNumber", "Issue Number", "path", kind="number", required=True)
PR = F("pullRequestNumber", "Pull Request Number", "path", kind="number", required=True)
RELEASE = F("release_id", "Release ID", "path", required=True)
REVIEW = F("reviewId", "Review ID", "path", required=True)
WORKFLOW = F("workflowId", "Workflow", "path", required=True, placeholder="ci.yml or 123456")
FILE_PATH = F("filePath", "File Path", "path", required=True, placeholder="docs/README.md")


SL_CH = F("channelId", "Channel", api="channel", required=True, placeholder="C0123456")
SL_CHQ = F("channelId", "Channel", "query", api="channel", required=True, placeholder="C0123456")
SL_TS = F("timestamp", "Message Timestamp", api="timestamp", kind="string", required=True, placeholder="1663233118.856619")
SL_USERQ = F("user", "User", "query", required=True, placeholder="U0123456")
SL_GROUP = F("userGroupId", "User Group ID", api="usergroup", required=True)
SL_SELECT = F("select", "Send Message To", "none", kind="options", options=[("Channel", "channel"), ("User", "user")])
SL_USER = F("user", "User", "none", placeholder="U0123456")
SL_MSGTYPE = F("messageType", "Message Type", "none", kind="options", options=[("Simple Text Message", "text"), ("Blocks", "block"), ("Attachments", "attachment")])
SL_BLOCKS = F("blocksUi", "Blocks (JSON)", "none", kind="json", default="", description='e.g. {"blocks": [...]} from the Slack Block Kit Builder')
SL_TEXT = F("text", "Message Text", "none", rows=3)
SL_OTHER = [F("thread_ts", "Reply to Thread (ts)"), F("reply_broadcast", "Also Send to Channel", kind="boolean"), F("unfurl_links", "Unfurl Links", kind="boolean"),
            F("unfurl_media", "Unfurl Media", kind="boolean"), F("mrkdwn", "Use Markdown", kind="boolean"), F("link_names", "Link User and Channel Names", kind="boolean"),
            F("username", "Bot Name"), F("icon_emoji", "Icon Emoji"), F("icon_url", "Icon URL")]
SLACK_OPS = [
    # message: post, schedule and delete pick channel or user and run in Rust (integrations.rs)
    OP("message", "post", "Send", "POST", "/chat.postMessage", [SL_SELECT, F("channelId", "Channel", "none", placeholder="#general or C0123456"), SL_USER, SL_MSGTYPE, SL_TEXT, SL_BLOCKS,
       F("attachments", "Attachments (JSON)", "none", kind="json", default="")], extra=SL_OTHER, extra_name="otherOptions"),
    OP("message", "schedule", "Schedule", "POST", "/chat.scheduleMessage", [SL_SELECT, F("channelId", "Channel", "none", placeholder="#general or C0123456"), SL_USER, SL_MSGTYPE, SL_TEXT,
       SL_BLOCKS, F("postAt", "Post At", "none", kind="dateTime", required=True)], extra=SL_OTHER, extra_name="otherOptions"),
    OP("message", "delete", "Delete", "POST", "/chat.delete", [SL_SELECT, F("channelId", "Channel", "none", placeholder="C0123456"), SL_USER,
       F("timestamp", "Message Timestamp", "none", required=True)]),
    OP("message", "postEphemeral", "Send Ephemeral", "POST", "/chat.postEphemeral", [SL_CH, F("user", "User", required=True, placeholder="U0123456"),
       F("text", "Message Text", rows=3), SL_BLOCKS], merge_json="blocksUi", extra=SL_OTHER, extra_name="otherOptions"),
    OP("message", "update", "Update", "POST", "/chat.update", [SL_CH, F("ts", "Message Timestamp", required=True), F("text", "Message Text", rows=3), SL_BLOCKS],
       merge_json="blocksUi", extra=[F("link_names", "Link User and Channel Names", kind="boolean"), F("parse", "Parse", kind="options", options=["client", "full", "none"])],
       extra_name="updateFields"),
    OP("message", "getPermalink", "Get Permalink", "GET", "/chat.getPermalink", [SL_CHQ, F("timestamp", "Message Timestamp", "query", api="message_ts", required=True)]),
    OP("message", "search", "Search", "GET", "/search.messages", [F("query", "Search Query", "query", required=True),
       F("sort", "Sort By", "query", api="sort_dir", kind="options", options=[("Newest", "desc"), ("Oldest", "asc")])], static_query={"sort": "timestamp"},
       root="messages.matches", limit="count"),
    OP("message", "getManyScheduled", "Get Many Scheduled", "GET", "/chat.scheduledMessages.list", [], root="scheduled_messages", limit="limit", extra_name="filters",
       extra=[F("channel", "Channel"), F("latest", "Latest (Unix time)"), F("oldest", "Oldest (Unix time)")]),
    OP("message", "deleteScheduled", "Delete Scheduled", "POST", "/chat.deleteScheduledMessage", [SL_CH, F("scheduledMessageId", "Scheduled Message ID", api="scheduled_message_id", required=True)]),
    OP("channel", "archive", "Archive", "POST", "/conversations.archive", [SL_CH]),
    OP("channel", "close", "Close", "POST", "/conversations.close", [SL_CH]),
    OP("channel", "create", "Create", "POST", "/conversations.create", [F("channelId", "Channel Name", api="name", required=True),
       F("channelVisibility", "Channel Visibility", api="is_private", kind="options", options=[("Public Channel", "public"), ("Private Channel", "private")], map={"public": False, "private": True})]),
    OP("channel", "get", "Get", "GET", "/conversations.info", [SL_CHQ], root="channel", extra_name="options",
       extra=[F("includeNumMembers", "Include Num of Members", api="include_num_members", kind="boolean")]),
    OP("channel", "getAll", "Get Many", "GET", "/conversations.list", [], root="channels", limit="limit", extra_name="filters",
       extra=[F("types", "Types", default="public_channel", description="public_channel, private_channel, mpim, im"),
              F("excludeArchived", "Exclude Archived", api="exclude_archived", kind="boolean")]),
    OP("channel", "history", "History", "GET", "/conversations.history", [SL_CHQ], root="messages", limit="limit", extra_name="filters",
       extra=[F("latest", "Latest (ts)"), F("oldest", "Oldest (ts)"), F("inclusive", "Inclusive", kind="boolean")]),
    OP("channel", "invite", "Invite", "POST", "/conversations.invite", [SL_CH, F("userIds", "User IDs", api="users", kind="csv", required=True)], root="channel"),
    OP("channel", "join", "Join", "POST", "/conversations.join", [SL_CH], root="channel"),
    OP("channel", "kick", "Kick", "POST", "/conversations.kick", [SL_CH, F("userId", "User ID", api="user", required=True)]),
    OP("channel", "leave", "Leave", "POST", "/conversations.leave", [SL_CH]),
    OP("channel", "member", "Member", "GET", "/conversations.members", [SL_CHQ], root="members", limit="limit"),
    OP("channel", "open", "Open", "POST", "/conversations.open", [], root="channel", extra_name="options",
       extra=[F("channelId", "Resume Conversation (Channel)", api="channel"), F("users", "Users", kind="csv"), F("returnIm", "Return IM", api="return_im", kind="boolean")]),
    OP("channel", "rename", "Rename", "POST", "/conversations.rename", [SL_CH, F("name", "Name", required=True)], root="channel"),
    OP("channel", "replies", "Replies", "GET", "/conversations.replies", [SL_CHQ, F("ts", "Message Timestamp", "query", required=True)], root="messages", limit="limit",
       extra_name="filters", extra=[F("latest", "Latest (ts)"), F("oldest", "Oldest (ts)"), F("inclusive", "Inclusive", kind="boolean")]),
    OP("channel", "setPurpose", "Set Purpose", "POST", "/conversations.setPurpose", [SL_CH, F("purpose", "Purpose", required=True)]),
    OP("channel", "setTopic", "Set Topic", "POST", "/conversations.setTopic", [SL_CH, F("topic", "Topic", required=True)]),
    OP("channel", "unarchive", "Unarchive", "POST", "/conversations.unarchive", [SL_CH]),
    OP("file", "get", "Get", "GET", "/files.info", [F("fileId", "File ID", "query", api="file", required=True)], root="file"),
    OP("file", "getAll", "Get Many", "GET", "/files.list", [], root="files", limit="count", extra_name="filters",
       extra=[F("channelId", "Channel", api="channel"), F("userId", "User", api="user"), F("types", "Types"), F("tsFrom", "From (Unix time)", api="ts_from"),
              F("tsTo", "To (Unix time)", api="ts_to"), F("showFilesHidden", "Show Hidden Files", api="show_files_hidden_by_limit", kind="boolean")]),
    # file:upload runs in Rust (three step external upload)
    OP("file", "upload", "Upload", "POST", "/files.completeUploadExternal", [F("binaryData", "Binary File", "none", kind="boolean", default=True),
       F("fileContent", "File Content", "none", rows=3), F("binaryPropertyName", "Input Binary Field", "none", default="data")], extra_name="options",
       extra=[F("channelIds", "Share in Channels", kind="csv"), F("fileName", "File Name"), F("title", "Title"), F("initialComment", "Initial Comment"),
              F("threadTs", "Thread Timestamp")]),
    OP("reaction", "add", "Add", "POST", "/reactions.add", [SL_CH, SL_TS, F("name", "Emoji Code", required=True, placeholder="robot_face")]),
    OP("reaction", "get", "Get", "GET", "/reactions.get", [SL_CHQ, F("timestamp", "Message Timestamp", "query", required=True)], root="message"),
    OP("reaction", "remove", "Remove", "POST", "/reactions.remove", [SL_CH, SL_TS, F("name", "Emoji Code", required=True, placeholder="robot_face")]),
    OP("star", "add", "Add", "POST", "/stars.add", [F("target", "Target", "none", kind="options", options=[("Message", "message"), ("File", "file")]),
       F("channelId", "Channel", api="channel"), F("timestamp", "Message Timestamp"), F("fileId", "File ID", api="file")]),
    OP("star", "delete", "Delete", "POST", "/stars.remove", [], extra_name="options",
       extra=[F("channelId", "Channel", api="channel"), F("timestamp", "Message Timestamp"), F("fileId", "File ID", api="file")]),
    OP("star", "getAll", "Get Many", "GET", "/stars.list", [], root="items", limit="count"),
    OP("user", "info", "Get", "GET", "/users.info", [SL_USERQ], root="user"),
    OP("user", "getAll", "Get Many", "GET", "/users.list", [], root="members", limit="limit"),
    OP("user", "getPresence", "Get Presence", "GET", "/users.getPresence", [SL_USERQ]),
    OP("user", "getProfile", "Get Profile", "GET", "/users.profile.get", [F("user", "User", "query", placeholder="U0123456")], root="profile"),
    OP("user", "lookupByEmail", "Get by Email", "GET", "/users.lookupByEmail", [F("email", "Email", "query", required=True)], root="user"),
    OP("user", "updateProfile", "Update Profile", "POST", "/users.profile.set", [], root="profile", extra_name="options", extra_target="profile",
       extra=[F("first_name", "First Name"), F("last_name", "Last Name"), F("email", "Email"), F("status_text", "Status Text"), F("status_emoji", "Status Emoji"),
              F("status_expiration", "Status Expiration (Unix time)", kind="number")]),
    OP("userGroup", "create", "Create", "POST", "/usergroups.create", [F("name", "Name", required=True)], root="usergroup", extra_name="Options",
       extra=[F("channels", "Default Channels", kind="csv"), F("description", "Description"), F("handle", "Handle"), F("include_count", "Include Count", kind="boolean")]),
    OP("userGroup", "disable", "Disable", "POST", "/usergroups.disable", [SL_GROUP], root="usergroup", extra_name="options",
       extra=[F("include_count", "Include Count", kind="boolean")]),
    OP("userGroup", "enable", "Enable", "POST", "/usergroups.enable", [SL_GROUP], root="usergroup", extra_name="option",
       extra=[F("include_count", "Include Count", kind="boolean")]),
    OP("userGroup", "getAll", "Get Many", "GET", "/usergroups.list", [], root="usergroups", extra_name="options",
       extra=[F("include_count", "Include Count", kind="boolean"), F("include_disabled", "Include Disabled", kind="boolean"), F("include_users", "Include Users", kind="boolean")]),
    OP("userGroup", "getUsers", "Get Users", "GET", "/usergroups.users.list", [F("userGroupId", "User Group ID", "query", api="usergroup", required=True)], root="users",
       extra_name="options", extra=[F("include_disabled", "Include Disabled", kind="boolean")]),
    OP("userGroup", "update", "Update", "POST", "/usergroups.update", [SL_GROUP], root="usergroup", extra_name="updateFields",
       extra=[F("name", "Name"), F("description", "Description"), F("handle", "Handle"), F("channels", "Default Channels", kind="csv")]),
    OP("userGroup", "updateUsers", "Update Users", "POST", "/usergroups.users.update", [SL_GROUP, F("users", "Users", kind="csv", required=True)], root="usergroup"),
]

TG_CHAT = F("chatId", "Chat ID", api="chat_id", required=True)
TG_MSG = F("messageId", "Message ID", api="message_id", required=True)
TG_SEND_EXTRA = [F("caption", "Caption"), F("parse_mode", "Parse Mode", kind="options", options=["HTML", "Markdown", "MarkdownV2"]),
                 F("disable_notification", "Disable Notification", kind="boolean"), F("disable_web_page_preview", "Disable WebPage Preview", kind="boolean"),
                 F("reply_to_message_id", "Reply To Message ID", kind="number"), F("message_thread_id", "Message Thread ID", kind="number"),
                 F("protect_content", "Protect Content", kind="boolean"), F("reply_markup", "Reply Markup (JSON)", kind="json")]


def tg_file(op, label, field):
    # binaryData=true uploads the binary property as multipart (handled in integrations.rs)
    return OP("message", op, label, "POST", "/" + op, [TG_CHAT, F("binaryData", "Binary File", "none", kind="boolean"),
              F("file", label.replace("Send ", ""), api=field, description="File ID or URL"), F("binaryPropertyName", "Input Binary Field", "none", default="data")],
              extra=TG_SEND_EXTRA)


TELEGRAM_OPS = [
    OP("message", "sendMessage", "Send Message", "POST", "/sendMessage", [TG_CHAT, F("text", "Text", rows=3, required=True)], extra=TG_SEND_EXTRA),
    OP("message", "editMessageText", "Edit Message Text", "POST", "/editMessageText", [
       F("messageType", "Message Type", "none", kind="options", options=[("Message", "message"), ("Inline Message", "inlineMessage")]),
       F("chatId", "Chat ID", api="chat_id"), F("messageId", "Message ID", api="message_id"), F("inlineMessageId", "Inline Message ID", api="inline_message_id"),
       F("text", "Text", rows=3, required=True)], extra=TG_SEND_EXTRA),
    OP("message", "deleteMessage", "Delete Chat Message", "POST", "/deleteMessage", [TG_CHAT, TG_MSG]),
    OP("message", "pinChatMessage", "Pin Chat Message", "POST", "/pinChatMessage", [TG_CHAT, TG_MSG], extra=[F("disable_notification", "Disable Notification", kind="boolean")]),
    OP("message", "unpinChatMessage", "Unpin Chat Message", "POST", "/unpinChatMessage", [TG_CHAT, TG_MSG]),
    OP("message", "sendChatAction", "Send Chat Action", "POST", "/sendChatAction", [TG_CHAT, F("action", "Action", kind="options", options=[
       ("Typing", "typing"), ("Upload Photo", "upload_photo"), ("Record Video", "record_video"), ("Upload Video", "upload_video"), ("Record Audio", "record_voice"),
       ("Upload Audio", "upload_voice"), ("Upload Document", "upload_document"), ("Find Location", "find_location"), ("Record Video Note", "record_video_note"),
       ("Upload Video Note", "upload_video_note")])]),
    OP("message", "sendLocation", "Send Location", "POST", "/sendLocation", [TG_CHAT, F("latitude", "Latitude", kind="number", required=True),
       F("longitude", "Longitude", kind="number", required=True)], extra=TG_SEND_EXTRA),
    OP("message", "sendMediaGroup", "Send Media Group", "POST", "/sendMediaGroup", [TG_CHAT, F("media", "Media (JSON)", kind="json", rows=4, required=True,
       description='[{"type": "photo", "media": "https://..."}]')], extra=[F("disable_notification", "Disable Notification", kind="boolean")]),
    tg_file("sendAnimation", "Send Animation", "animation"),
    tg_file("sendAudio", "Send Audio", "audio"),
    tg_file("sendDocument", "Send Document", "document"),
    tg_file("sendPhoto", "Send Photo", "photo"),
    tg_file("sendSticker", "Send Sticker", "sticker"),
    tg_file("sendVideo", "Send Video", "video"),
    OP("chat", "get", "Get", "POST", "/getChat", [TG_CHAT], root="result"),
    OP("chat", "administrators", "Get Administrators", "POST", "/getChatAdministrators", [TG_CHAT], root="result"),
    OP("chat", "member", "Get Member", "POST", "/getChatMember", [TG_CHAT, F("userId", "User ID", api="user_id", required=True)], root="result"),
    OP("chat", "leave", "Leave", "POST", "/leaveChat", [TG_CHAT]),
    OP("chat", "setDescription", "Set Description", "POST", "/setChatDescription", [TG_CHAT, F("description", "Description", required=True)]),
    OP("chat", "setTitle", "Set Title", "POST", "/setChatTitle", [TG_CHAT, F("title", "Title", required=True)]),
    OP("callback", "answerQuery", "Answer Query", "POST", "/answerCallbackQuery", [F("queryId", "Query ID", api="callback_query_id", required=True)],
       extra=[F("text", "Text"), F("show_alert", "Show Alert", kind="boolean"), F("url", "URL"), F("cache_time", "Cache Time", kind="number")]),
    OP("callback", "answerInlineQuery", "Answer Inline Query", "POST", "/answerInlineQuery", [F("queryId", "Query ID", api="inline_query_id", required=True),
       F("results", "Results (JSON)", kind="json", rows=4, required=True)], extra=[F("cache_time", "Cache Time", kind="number"), F("is_personal", "Is Personal", kind="boolean")]),
    # file:get runs in Rust (getFile + optional download)
    OP("file", "get", "Get", "POST", "/getFile", [F("fileId", "File ID", api="file_id", required=True), F("download", "Download", "none", kind="boolean", default=True)], root="result"),
]

DC_GUILD = F("guildId", "Server", "path", required=True)
DC_CH = F("channelId", "Channel", "path", required=True)
DC_MSG = F("messageId", "Message ID", "path", required=True)
DC_USER = F("userId", "User", "path", required=True)
DC_CH_OPTS = [F("topic", "Topic"), F("nsfw", "Age-Restricted (NSFW)", kind="boolean"), F("position", "Position", kind="number"),
              F("rate_limit_per_user", "Slowmode (seconds)", kind="number"), F("bitrate", "Bitrate", kind="number"), F("user_limit", "User Limit", kind="number"),
              F("categoryId", "Parent Category", api="parent_id")]
DISCORD_OPS = [
    OP("message", "send", "Send a Message", "POST", "/channels/{channelId}/messages", [F("guildId", "Server", "none"), DC_CH, F("content", "Message", rows=3),
       F("embeds", "Embeds (JSON)", kind="json", default="")], extra_name="options",
       extra=[F("tts", "TTS", kind="boolean"), F("flags", "Flags", kind="number"), F("message_reference", "Reply To (JSON)", kind="json")]),
    OP("message", "get", "Get", "GET", "/channels/{channelId}/messages/{messageId}", [F("guildId", "Server", "none"), DC_CH, DC_MSG]),
    OP("message", "getAll", "Get Many", "GET", "/channels/{channelId}/messages", [F("guildId", "Server", "none"), DC_CH], limit="limit"),
    OP("message", "deleteMessage", "Delete", "DELETE", "/channels/{channelId}/messages/{messageId}", [F("guildId", "Server", "none"), DC_CH, DC_MSG]),
    OP("message", "react", "React with Emoji", "PUT", "/channels/{channelId}/messages/{messageId}/reactions/{emoji}/@me",
       [F("guildId", "Server", "none"), DC_CH, DC_MSG, F("emoji", "Emoji", "path", required=True, placeholder="👍")]),
    OP("channel", "create", "Create", "POST", "/guilds/{guildId}/channels", [DC_GUILD, F("name", "Name", required=True),
       F("type", "Type", kind="options", default="0", options=[("Guild Text", "0"), ("Guild Voice", "2"), ("Guild Category", "4")], map={"0": 0, "2": 2, "4": 4})],
       extra=DC_CH_OPTS, extra_name="options"),
    OP("channel", "get", "Get", "GET", "/channels/{channelId}", [F("guildId", "Server", "none"), DC_CH]),
    OP("channel", "getAll", "Get Many", "GET", "/guilds/{guildId}/channels", [DC_GUILD]),
    OP("channel", "update", "Update", "PATCH", "/channels/{channelId}", [F("guildId", "Server", "none"), DC_CH, F("name", "Name")], extra=DC_CH_OPTS, extra_name="options"),
    OP("channel", "deleteChannel", "Delete", "DELETE", "/channels/{channelId}", [F("guildId", "Server", "none"), DC_CH]),
    OP("member", "getAll", "Get Many", "GET", "/guilds/{guildId}/members", [DC_GUILD], limit="limit", extra_name="options", extra=[F("after", "After User ID")]),
    OP("member", "roleAdd", "Role Add", "PUT", "/guilds/{guildId}/members/{userId}/roles/{role}", [DC_GUILD, DC_USER, F("role", "Role ID", "path", required=True)]),
    OP("member", "roleRemove", "Role Remove", "DELETE", "/guilds/{guildId}/members/{userId}/roles/{role}", [DC_GUILD, DC_USER, F("role", "Role ID", "path", required=True)]),
    OP("member", "kick", "Kick", "DELETE", "/guilds/{guildId}/members/{userId}", [DC_GUILD, DC_USER]),
    OP("member", "ban", "Ban", "PUT", "/guilds/{guildId}/bans/{userId}", [DC_GUILD, DC_USER], extra_name="options",
       extra=[F("delete_message_seconds", "Delete Messages of the Last (seconds)", kind="number")]),
    OP("member", "unban", "Unban", "DELETE", "/guilds/{guildId}/bans/{userId}", [DC_GUILD, DC_USER]),
    OP("member", "timeout", "Timeout", "PATCH", "/guilds/{guildId}/members/{userId}", [DC_GUILD, DC_USER,
       F("timeoutUntil", "Timeout Until", api="communication_disabled_until", kind="dateTime", required=True)]),
    # webhook:sendLegacy runs in Rust with the webhook credential
    OP("webhook", "sendLegacy", "Send a Message", "POST", "/webhooks", [F("content", "Message", "none", rows=3)], extra_name="options",
       extra=[F("username", "Username"), F("avatar_url", "Avatar URL"), F("tts", "TTS", kind="boolean")]),
]
DISCORD_TOP = [{"displayName": "Connection Type", "name": "authentication", "type": "options", "default": "botToken",
                "options": [{"name": "Bot Token", "value": "botToken"}, {"name": "Webhook", "value": "webhook"}]}]


# Gmail and Google Drive: operations with own logic (MIME mails, threads, uploads, exports, trash)
# run in src/nodes/google.rs; the rest is declarative.
GM_MSG = F("messageId", "Message ID", "path", required=True)
GM_THREAD = F("threadId", "Thread ID", "path", required=True)
GM_TYPE = F("emailType", "Email Type", "none", kind="options", default="html", options=[("HTML", "html"), ("Text", "text")])
GM_BODY = F("message", "Message", "none", rows=5, required=True)
GM_ATT = F("attachmentsUi", "Attachments (binary fields)", description="Comma-separated binary property names, e.g. data")
GM_SEND = [F("ccList", "CC"), F("bccList", "BCC"), F("senderName", "Sender Name"), F("replyTo", "Reply To"), GM_ATT]
GM_READ = [F("downloadAttachments", "Download Attachments", kind="boolean"), F("dataPropertyAttachmentsPrefixName", "Attachment Prefix", default="attachment_")]
GM_FILTERS = [F("q", "Search"), F("labelIds", "Label IDs", kind="list"), F("readStatus", "Read Status", kind="options", options=[("Unread and read", "both"), ("Unread", "unread"), ("Read", "read")]),
              F("sender", "Sender"), F("receivedAfter", "Received After", kind="dateTime"), F("receivedBefore", "Received Before", kind="dateTime"),
              F("includeSpamTrash", "Include Spam and Trash", kind="boolean")]
GM_SIMPLE = F("simple", "Simplify", "none", kind="boolean", default=True)
GMAIL_OPS = [
    OP("message", "send", "Send", "POST", "/messages/send", [F("sendTo", "To", "none", required=True, placeholder="info@example.com"), F("subject", "Subject", "none", required=True),
       GM_TYPE, GM_BODY], extra=GM_SEND, extra_name="options"),
    OP("message", "reply", "Reply", "POST", "/messages/send", [F("messageId", "Message ID", "none", required=True), GM_TYPE, GM_BODY],
       extra=GM_SEND + [F("replyToSenderOnly", "Reply to Sender Only", kind="boolean")], extra_name="options"),
    OP("message", "get", "Get", "GET", "/messages/{messageId}", [GM_MSG, GM_SIMPLE], extra=GM_READ, extra_name="options"),
    OP("message", "getAll", "Get Many", "GET", "/messages", [GM_SIMPLE], limit="maxResults", extra=GM_FILTERS, extra_name="filters"),
    OP("message", "delete", "Delete", "DELETE", "/messages/{messageId}", [GM_MSG]),
    OP("message", "addLabels", "Add Label", "POST", "/messages/{messageId}/modify", [GM_MSG, F("labelIds", "Label IDs", api="addLabelIds", kind="list", required=True)]),
    OP("message", "removeLabels", "Remove Label", "POST", "/messages/{messageId}/modify", [GM_MSG, F("labelIds", "Label IDs", api="removeLabelIds", kind="list", required=True)]),
    OP("message", "markAsRead", "Mark as Read", "POST", "/messages/{messageId}/modify", [GM_MSG], static={"removeLabelIds": ["UNREAD"]}),
    OP("message", "markAsUnread", "Mark as Unread", "POST", "/messages/{messageId}/modify", [GM_MSG], static={"addLabelIds": ["UNREAD"]}),
    OP("label", "create", "Create", "POST", "/labels", [F("name", "Name", required=True)], extra_name="options",
       extra=[F("labelListVisibility", "Label List Visibility", kind="options", options=[("Show", "labelShow"), ("Show If Unread", "labelShowIfUnread"), ("Hide", "labelHide")]),
              F("messageListVisibility", "Message List Visibility", kind="options", options=[("Show", "show"), ("Hide", "hide")])]),
    OP("label", "delete", "Delete", "DELETE", "/labels/{labelId}", [F("labelId", "Label ID", "path", required=True)]),
    OP("label", "get", "Get", "GET", "/labels/{labelId}", [F("labelId", "Label ID", "path", required=True)]),
    OP("label", "getAll", "Get Many", "GET", "/labels", [], root="labels"),
    OP("draft", "create", "Create", "POST", "/drafts", [F("subject", "Subject", "none", required=True), GM_TYPE, GM_BODY], extra_name="options",
       extra=[F("sendTo", "To"), F("threadId", "Thread ID"), F("fromAlias", "From Alias")] + GM_SEND),
    OP("draft", "delete", "Delete", "DELETE", "/drafts/{messageId}", [F("messageId", "Draft ID", "path", required=True)]),
    OP("draft", "get", "Get", "GET", "/drafts/{messageId}", [F("messageId", "Draft ID", "path", required=True)], extra=GM_READ, extra_name="options"),
    OP("draft", "getAll", "Get Many", "GET", "/drafts", [], limit="maxResults", extra=GM_READ + [F("includeSpamTrash", "Include Spam and Trash", kind="boolean")],
       extra_name="options"),
    OP("thread", "get", "Get", "GET", "/threads/{threadId}", [GM_THREAD, GM_SIMPLE], extra_name="options",
       extra=[F("returnOnlyMessages", "Return Only Messages", kind="boolean", default=True)]),
    OP("thread", "getAll", "Get Many", "GET", "/threads", [], limit="maxResults", extra=GM_FILTERS, extra_name="filters"),
    OP("thread", "reply", "Reply", "POST", "/messages/send", [F("threadId", "Thread ID", "none", required=True), GM_TYPE, GM_BODY],
       extra=GM_SEND + [F("messageId", "Reply to Message ID"), F("replyToSenderOnly", "Reply to Sender Only", kind="boolean")], extra_name="options"),
    OP("thread", "addLabels", "Add Label", "POST", "/threads/{threadId}/modify", [GM_THREAD, F("labelIds", "Label IDs", api="addLabelIds", kind="list", required=True)]),
    OP("thread", "removeLabels", "Remove Label", "POST", "/threads/{threadId}/modify", [GM_THREAD, F("labelIds", "Label IDs", api="removeLabelIds", kind="list", required=True)]),
    OP("thread", "delete", "Delete", "DELETE", "/threads/{threadId}", [GM_THREAD]),
    OP("thread", "trash", "Trash", "POST", "/threads/{threadId}/trash", [GM_THREAD]),
    OP("thread", "untrash", "Untrash", "POST", "/threads/{threadId}/untrash", [GM_THREAD]),
]

DR_FILE = F("fileId", "File", "none", required=True, placeholder="File ID or URL")
DR_DRIVE = F("driveId", "Drive", "none", default="My Drive", placeholder="My Drive or shared drive ID")
DR_FOLDER = F("folderId", "Folder", "none", default="root", placeholder="root, folder ID or URL")
DR_PERM = [F("role", "Role", "none", kind="options", options=[("Reader", "reader"), ("Commenter", "commenter"), ("Writer", "writer"), ("File Organizer", "fileOrganizer"),
                                                              ("Organizer", "organizer"), ("Owner", "owner")]),
           F("type", "Type", "none", kind="options", options=[("User", "user"), ("Group", "group"), ("Domain", "domain"), ("Anyone", "anyone")]),
           F("emailAddress", "Email Address", "none"), F("domain", "Domain", "none"), F("allowFileDiscovery", "Allow File Discovery", "none", kind="boolean")]
DR_SHARE_OPTS = [F("emailMessage", "Email Message"), F("sendNotificationEmail", "Send Notification Email", kind="boolean"), F("transferOwnership", "Transfer Ownership", kind="boolean"),
                 F("moveToNewOwnersRoot", "Move to New Owner's Root", kind="boolean"), F("useDomainAdminAccess", "Use Domain Admin Access", kind="boolean")]
DR_UPLOAD_OPTS = [F("keepRevisionForever", "Keep Revision Forever", kind="boolean"), F("ocrLanguage", "OCR Language"),
                  F("useContentAsIndexableText", "Use Content as Indexable Text", kind="boolean"), F("simplifyOutput", "Simplify Output", kind="boolean", default=True)]
DR_DELETE = [F("deletePermanently", "Delete Permanently", kind="boolean")]
DRIVE_OPS = [
    OP("file", "upload", "Upload", "POST", "/files", [F("name", "File Name", "none"), DR_DRIVE, DR_FOLDER, F("inputDataFieldName", "Input Data Field Name", "none", default="data")],
       extra=DR_UPLOAD_OPTS, extra_name="options"),
    OP("file", "download", "Download", "GET", "/files/{fileId}", [DR_FILE], extra_name="options",
       extra=[F("binaryPropertyName", "Put Output File in Field", default="data"), F("fileName", "File Name"),
              F("docsToFormat", "Google Docs As", kind="options", default="text/html", options=[("HTML", "text/html"), ("MS Word", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                                                                                              ("PDF", "application/pdf"), ("Plain Text", "text/plain"), ("Open Office", "application/vnd.oasis.opendocument.text")]),
              F("sheetsToFormat", "Google Sheets As", kind="options", default="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                options=[("MS Excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), ("CSV", "text/csv"), ("PDF", "application/pdf"),
                         ("Open Office", "application/vnd.oasis.opendocument.spreadsheet")]),
              F("slidesToFormat", "Google Slides As", kind="options", default="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                options=[("MS PowerPoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation"), ("PDF", "application/pdf"), ("Plain Text", "text/plain")]),
              F("drawingsToFormat", "Google Drawings As", kind="options", default="image/jpeg", options=[("JPEG", "image/jpeg"), ("PNG", "image/png"), ("SVG", "image/svg+xml"),
                                                                                                        ("PDF", "application/pdf")])]),
    OP("file", "copy", "Copy", "POST", "/files/{fileId}/copy", [DR_FILE, F("name", "File Name", "none"), F("sameFolder", "Copy In The Same Folder", "none", kind="boolean", default=True),
       DR_DRIVE, DR_FOLDER], extra=[F("copyRequiresWriterPermission", "Copy Requires Writer Permission", kind="boolean"), F("description", "Description")], extra_name="options"),
    OP("file", "createFromText", "Create From Text", "POST", "/files", [F("content", "File Content", "none", rows=5), F("name", "File Name", "none", required=True), DR_DRIVE, DR_FOLDER],
       extra=[F("convertToGoogleDocument", "Convert to Google Document", kind="boolean")] + DR_UPLOAD_OPTS, extra_name="options"),
    OP("file", "deleteFile", "Delete", "DELETE", "/files/{fileId}", [DR_FILE], extra=DR_DELETE, extra_name="options"),
    OP("file", "move", "Move", "PATCH", "/files/{fileId}", [DR_FILE, DR_DRIVE, DR_FOLDER]),
    OP("file", "share", "Share", "POST", "/files/{fileId}/permissions", [DR_FILE] + DR_PERM, extra=DR_SHARE_OPTS, extra_name="options"),
    OP("file", "update", "Update", "PATCH", "/files/{fileId}", [F("fileToUpdate", "File to Update", "none", required=True), F("changeFileContent", "Change File Content", "none", kind="boolean"),
       F("inputDataFieldName", "Input Data Field Name", "none", default="data"), F("newUpdatedFileName", "New Updated File Name", "none")], extra_name="options",
       extra=[F("trashed", "Move to Trash", kind="boolean"), F("starred", "Starred", kind="boolean"), F("description", "Description")] + DR_UPLOAD_OPTS[:3]),
    OP("fileFolder", "search", "Search", "GET", "/files", [F("searchMethod", "Search Method", "none", kind="options", options=[("Search File/Folder Name", "name"), ("Advanced Search", "query")]),
       F("queryString", "Search Query", "none")], limit="pageSize", extra_name="filter",
       extra=[F("driveId", "Drive"), F("folderId", "Folder"), F("whatToSearch", "What to Search", kind="options", options=[("Files and Folders", "all"), ("Files", "files"), ("Folders", "folders")]),
              F("fileTypes", "File Types", kind="list", description="MIME types, e.g. application/vnd.google-apps.spreadsheet"), F("includeTrashed", "Include Trashed Items", kind="boolean")]),
    OP("folder", "create", "Create", "POST", "/files", [F("name", "Folder Name", "none"), DR_DRIVE, DR_FOLDER], extra_name="options",
       extra=[F("folderColorRgb", "Folder Color"), F("simplifyOutput", "Simplify Output", kind="boolean", default=True)]),
    OP("folder", "deleteFolder", "Delete", "DELETE", "/files/{folderNoRootId}", [F("folderNoRootId", "Folder", "none", required=True)], extra=DR_DELETE, extra_name="options"),
    OP("folder", "share", "Share", "POST", "/files/{folderNoRootId}/permissions", [F("folderNoRootId", "Folder", "none", required=True)] + DR_PERM, extra=DR_SHARE_OPTS,
       extra_name="options"),
    OP("drive", "create", "Create", "POST", "/drives", [F("name", "Name", "none", required=True)], extra_name="options",
       extra=[F("colorRgb", "Color RGB"), F("hidden", "Hidden", kind="boolean")]),
    OP("drive", "deleteDrive", "Delete", "DELETE", "/drives/{driveId}", [F("driveId", "Shared Drive", "path", required=True)]),
    OP("drive", "get", "Get", "GET", "/drives/{driveId}", [F("driveId", "Shared Drive", "path", required=True)], extra_name="options",
       extra=[F("useDomainAdminAccess", "Use Domain Admin Access", kind="boolean")]),
    OP("drive", "list", "Get Many", "GET", "/drives", [], root="drives", limit="pageSize", extra_name="options",
       extra=[F("q", "Query"), F("useDomainAdminAccess", "Use Domain Admin Access", kind="boolean")]),
    OP("drive", "update", "Update", "PATCH", "/drives/{driveId}", [F("driveId", "Shared Drive", "path", required=True)], extra_name="options",
       extra=[F("name", "Name"), F("colorRgb", "Color RGB"), F("restrictions", "Restrictions (JSON)", kind="json")]),
]


# AWS S3: requests are built and signed in src/nodes/s3.rs; the operations here describe the form.
S3_BUCKET = F("bucketName", "Bucket Name", "none", required=True)
S3_ACL = F("acl", "ACL", kind="options", options=[("Authenticated Read", "authenticatedRead"), ("AWS Exec Read", "awsExecRead"), ("Bucket Owner Full Control", "bucketOwnerFullControl"),
                                                ("Bucket Owner Read", "bucketOwnerRead"), ("Private", "private"), ("Public Read", "publicRead"), ("Public Read Write", "publicReadWrite")])
S3_GRANTS = [F("grantFullControl", "Grant Full Control", kind="boolean"), F("grantRead", "Grant Read", kind="boolean"), F("grantReadAcp", "Grant Read ACP", kind="boolean"),
             F("grantWriteAcp", "Grant Write ACP", kind="boolean")]
S3_STORAGE = F("storageClass", "Storage Class", kind="options", options=[("Standard", "standard"), ("Glacier", "glacier"), ("Deep Archive", "deepArchive"),
                                                                       ("Intelligent Tiering", "intelligentTiering"), ("One Zone IA", "onezoneIA"), ("Reduced Redundancy", "RecudedRedundancy"),
                                                                       ("Standard IA", "standardIA")])
S3_OBJECT = [S3_ACL, S3_STORAGE, F("serverSideEncryption", "Server Side Encryption", kind="options", options=[("AES256", "AES256"), ("AWS:KMS", "aws:kms")]),
             F("encryptionAwsKmsKeyId", "Encryption AWS KMS Key ID"), F("serverSideEncryptionContext", "Server Side Encryption Context"),
             F("serversideEncryptionCustomerAlgorithm", "Server Side Encryption Customer Algorithm"), F("serversideEncryptionCustomerKey", "Server Side Encryption Customer Key"),
             F("lockLegalHold", "Lock Legal Hold", kind="boolean"), F("lockMode", "Lock Mode", kind="options", options=[("Governance", "governance"), ("Compliance", "compliance")]),
             F("lockRetainUntilDate", "Lock Retain Until Date", kind="dateTime"), F("requesterPays", "Requester Pays", kind="boolean")] + S3_GRANTS
S3_OPS = [
    OP("file", "download", "Download", "GET", "/", [S3_BUCKET, F("fileKey", "File Key", "none", required=True), F("binaryPropertyName", "Put Output File in Field", "none", default="data")]),
    OP("file", "upload", "Upload", "PUT", "/", [S3_BUCKET, F("fileName", "File Name", "none", required=True), F("binaryData", "Binary File", "none", kind="boolean", default=True),
       F("binaryPropertyName", "Input Binary Field", "none", default="data"), F("fileContent", "File Content", "none", rows=4),
       F("tagsUi", "Tags (JSON)", "none", kind="json", default="", description='{"tagsValues": [{"key": "team", "value": "ops"}]}')],
       extra=[F("parentFolderKey", "Parent Folder Key")] + S3_OBJECT),
    OP("file", "copy", "Copy", "PUT", "/", [F("sourcePath", "Source Path", "none", required=True, placeholder="/bucket/folder/file.txt"),
       F("destinationPath", "Destination Path", "none", required=True, placeholder="/bucket/folder/copy.txt")],
       extra=S3_OBJECT + [F("metadataDirective", "Metadata Directive", kind="options", options=[("Copy", "copy"), ("Replace", "replace")]),
                          F("taggingDirective", "Tagging Directive", kind="options", options=[("Copy", "copy"), ("Replace", "replace")])]),
    OP("file", "delete", "Delete", "DELETE", "/", [S3_BUCKET, F("fileKey", "File Key", "none", required=True)], extra=[F("versionId", "Version ID")], extra_name="options"),
    OP("file", "getAll", "Get Many", "GET", "/", [S3_BUCKET], limit="max-keys", extra=[F("folderKey", "Folder Key"), F("fetchOwner", "Fetch Owner", kind="boolean")], extra_name="options"),
    OP("folder", "create", "Create", "PUT", "/", [S3_BUCKET, F("folderName", "Folder Name", "none", required=True)],
       extra=[F("parentFolderKey", "Parent Folder Key"), F("requesterPays", "Requester Pays", kind="boolean"), S3_STORAGE]),
    OP("folder", "delete", "Delete", "DELETE", "/", [S3_BUCKET, F("folderKey", "Folder Key", "none", required=True)]),
    OP("folder", "getAll", "Get Many", "GET", "/", [S3_BUCKET], limit="max-keys", extra=[F("folderKey", "Folder Key"), F("fetchOwner", "Fetch Owner", kind="boolean")], extra_name="options"),
    OP("bucket", "create", "Create", "PUT", "/", [F("name", "Name", "none", required=True)],
       extra=[S3_ACL, F("bucketObjectLockEnabled", "Bucket Object Lock Enabled", kind="boolean"), F("region", "Region")] + S3_GRANTS + [F("grantWrite", "Grant Write", kind="boolean")]),
    OP("bucket", "delete", "Delete", "DELETE", "/", [F("name", "Name", "none", required=True)]),
    OP("bucket", "getAll", "Get Many", "GET", "/", [], limit="max-keys"),
    OP("bucket", "search", "Search", "GET", "/", [S3_BUCKET], limit="max-keys",
       extra=[F("delimiter", "Delimiter"), F("encodingType", "Encoding Type", kind="options", options=[("URL", "url")]), F("fetchOwner", "Fetch Owner", kind="boolean"),
              F("prefix", "Prefix"), F("requesterPays", "Requester Pays", kind="boolean"), F("startAfter", "Start After")]),
]


# MongoDB runs in src/nodes/mongo.rs (official driver); the operations here describe the form.
MG_COLL = F("collection", "Collection", "none", required=True)
MG_DOC_OPTS = [F("dateFields", "Date Fields", description="Comma-separated fields to store as dates"), F("useDotNotation", "Use Dot Notation", kind="boolean")]
MG_UPDATE = [MG_COLL, F("updateKey", "Update Key", "none", default="id", required=True), F("fields", "Fields", "none", description="Comma-separated; empty = all"),
             F("upsert", "Upsert", "none", kind="boolean")]
MG_INDEX = F("indexNameRequired", "Index Name", "none", required=True)
MG_DEF = F("indexDefinition", "Index Definition (JSON)", "none", kind="json", rows=5, required=True)
MONGO_OPS = [
    OP("document", "find", "Find", "GET", "/", [MG_COLL, F("query", "Query (JSON)", "none", kind="json", rows=4, default="{}")], extra_name="options",
       extra=[F("limit", "Limit", kind="number"), F("skip", "Skip", kind="number"), F("sort", "Sort (JSON)", kind="json"), F("projection", "Projection (JSON)", kind="json")]),
    OP("document", "aggregate", "Aggregate", "GET", "/", [MG_COLL, F("query", "Pipeline (JSON)", "none", kind="json", rows=6, default='[{"$match": {}}]')]),
    OP("document", "insert", "Insert", "POST", "/", [MG_COLL, F("fields", "Fields", "none", description="Comma-separated; empty = all")], extra=MG_DOC_OPTS, extra_name="options"),
    OP("document", "update", "Update", "PUT", "/", MG_UPDATE, extra=MG_DOC_OPTS, extra_name="options"),
    OP("document", "findOneAndUpdate", "Find And Update", "PUT", "/", MG_UPDATE, extra=MG_DOC_OPTS, extra_name="options"),
    OP("document", "findOneAndReplace", "Find And Replace", "PUT", "/", MG_UPDATE, extra=MG_DOC_OPTS, extra_name="options"),
    OP("document", "delete", "Delete", "DELETE", "/", [MG_COLL, F("query", "Delete Query (JSON)", "none", kind="json", rows=4, default="{}")]),
    OP("searchIndexes", "createSearchIndex", "Create Search Index", "POST", "/", [MG_COLL, MG_INDEX, MG_DEF,
       F("indexType", "Index Type", "none", kind="options", options=[("Vector Search", "vectorSearch"), ("Search", "search")], default="vectorSearch")]),
    OP("searchIndexes", "dropSearchIndex", "Drop Search Index", "DELETE", "/", [MG_COLL, MG_INDEX]),
    OP("searchIndexes", "listSearchIndexes", "List Search Indexes", "GET", "/", [MG_COLL, F("indexName", "Index Name", "none")]),
    OP("searchIndexes", "updateSearchIndex", "Update Search Index", "PUT", "/", [MG_COLL, MG_INDEX, MG_DEF]),
]


# FTP / SFTP run in src/nodes/ftp.rs and src/nodes/sftp.rs; the operations here describe the form.
FTP_PATH = F("path", "Path", "none", required=True, placeholder="/public/documents/file.txt")
FTP_OPS = [
    OP("file", "download", "Download", "GET", "/", [FTP_PATH, F("binaryPropertyName", "Put Output File in Field", "none", default="data")]),
    OP("file", "upload", "Upload", "PUT", "/", [FTP_PATH, F("binaryData", "Binary File", "none", kind="boolean", default=True),
       F("binaryPropertyName", "Input Binary Field", "none", default="data"), F("fileContent", "File Content", "none", rows=4)]),
    OP("file", "list", "List", "GET", "/", [F("path", "Path", "none", default="/"), F("recursive", "Recursive", "none", kind="boolean")]),
    OP("file", "rename", "Rename", "POST", "/", [F("oldPath", "Old Path", "none", required=True), F("newPath", "New Path", "none", required=True)],
       extra=[F("createDirectories", "Create Directories", kind="boolean")], extra_name="options"),
    OP("file", "delete", "Delete", "DELETE", "/", [FTP_PATH], extra=[F("folder", "Folder", kind="boolean"), F("recursive", "Recursive", kind="boolean")], extra_name="options"),
]
FTP_TOP = [{"displayName": "Protocol", "name": "protocol", "type": "options", "default": "ftp",
            "options": [{"name": "FTP", "value": "ftp"}, {"name": "SFTP", "value": "sftp"}]}]

NODES = [
    node("n8n-nodes-base.github", "GitHub", "githubApi", "{cred.server}", ["bearer:accessToken"], [
        OP("issue", "create", "Create", "POST", "/repos/{owner}/{repository}/issues",
           [OWNER, REPO, F("title", "Title", required=True), F("body", "Body", rows=4), F("labels", "Labels", kind="list"), F("assignees", "Assignees", kind="list")]),
        OP("issue", "createComment", "Create Comment", "POST", "/repos/{owner}/{repository}/issues/{issueNumber}/comments",
           [OWNER, REPO, ISSUE, F("body", "Body", rows=4)]),
        OP("issue", "edit", "Edit", "PATCH", "/repos/{owner}/{repository}/issues/{issueNumber}", [OWNER, REPO, ISSUE],
           extra=[F("title", "Title"), F("body", "Body", rows=4), F("state", "State", kind="options", options=["open", "closed"]), F("labels", "Labels", kind="list")]),
        OP("issue", "get", "Get", "GET", "/repos/{owner}/{repository}/issues/{issueNumber}", [OWNER, REPO, ISSUE]),
        OP("issue", "getAll", "Get Many", "GET", "/repos/{owner}/{repository}/issues",
           [OWNER, REPO, F("state", "State", "query", kind="options", options=["open", "closed", "all"])], limit="per_page"),
        OP("file", "get", "Get", "GET", "/repos/{owner}/{repository}/contents/{+filePath}", [OWNER, REPO, F("filePath", "File Path", "path", required=True, placeholder="docs/README.md")]),
        OP("release", "create", "Create", "POST", "/repos/{owner}/{repository}/releases",
           [OWNER, REPO, F("releaseTag", "Tag", api="tag_name", required=True), F("releaseName", "Name", api="name"), F("releaseBody", "Body", api="body", rows=4)],
           extra=[F("draft", "Draft", kind="boolean"), F("prerelease", "Prerelease", kind="boolean")]),
        OP("release", "getAll", "Get Many", "GET", "/repos/{owner}/{repository}/releases", [OWNER, REPO], limit="per_page"),
        OP("repository", "get", "Get", "GET", "/repos/{owner}/{repository}", [OWNER, REPO]),
        OP("user", "getRepositories", "Get Repositories", "GET", "/users/{owner}/repos", [F("owner", "User", "path", required=True)], limit="per_page"),
        OP("user", "getUserIssues", "Get Issues", "GET", "/issues", [], limit="per_page", extra_name="getUserIssuesFilters",
           extra=[F("filter", "Filter", kind="options", options=["assigned", "created", "mentioned", "subscribed", "all"]), F("state", "State", kind="options", options=["open", "closed", "all"]),
                  F("labels", "Labels"), F("sort", "Sort", kind="options", options=["created", "updated", "comments"]), F("direction", "Direction", kind="options", options=["asc", "desc"]),
                  F("since", "Updated Since", kind="dateTime")]),
        OP("user", "invite", "Invite", "POST", "/orgs/{organization}/invitations", [F("organization", "Organization", "path", required=True), F("email", "Email", required=True)]),
        OP("issue", "lock", "Lock", "PUT", "/repos/{owner}/{repository}/issues/{issueNumber}/lock", [OWNER, REPO, ISSUE,
           F("lockReason", "Lock Reason", api="lock_reason", kind="options", options=["off-topic", "too heated", "resolved", "spam"])]),
        OP("file", "create", "Create", "PUT", "/repos/{owner}/{repository}/contents/{+filePath}", [OWNER, REPO, FILE_PATH,
           F("fileContent", "File Content", api="content", kind="base64", rows=4), F("commitMessage", "Commit Message", api="message", required=True)],
           extra=[F("branch", "Branch"), F("author", "Author (JSON)", kind="json"), F("committer", "Committer (JSON)", kind="json")]),
        OP("file", "edit", "Edit", "PUT", "/repos/{owner}/{repository}/contents/{+filePath}", [OWNER, REPO, FILE_PATH,
           F("fileContent", "File Content", api="content", kind="base64", rows=4), F("commitMessage", "Commit Message", api="message", required=True)],
           extra=[F("branch", "Branch"), F("author", "Author (JSON)", kind="json"), F("committer", "Committer (JSON)", kind="json")], fetch_sha=True),
        OP("file", "delete", "Delete", "DELETE", "/repos/{owner}/{repository}/contents/{+filePath}", [OWNER, REPO, FILE_PATH,
           F("commitMessage", "Commit Message", api="message", required=True)], extra=[F("branch", "Branch")], fetch_sha=True),
        OP("file", "list", "List", "GET", "/repos/{owner}/{repository}/contents/{+filePath}", [OWNER, REPO, F("filePath", "Path", "path", placeholder="docs")]),
        OP("organization", "getRepositories", "Get Repositories", "GET", "/orgs/{owner}/repos", [F("owner", "Organization", "path", required=True)], limit="per_page"),
        OP("organization", "getMembers", "Get Members", "GET", "/orgs/{owner}/members", [F("owner", "Organization", "path", required=True)], limit="per_page"),
        OP("pullRequest", "create", "Create", "POST", "/repos/{owner}/{repository}/pulls", [OWNER, REPO, F("title", "Title", required=True),
           F("head", "Head Branch", required=True), F("base", "Base Branch", required=True), F("body", "Body", rows=4)],
           extra=[F("draft", "Draft", kind="boolean"), F("maintainer_can_modify", "Maintainers Can Modify", kind="boolean")]),
        OP("pullRequest", "get", "Get", "GET", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}", [OWNER, REPO, PR]),
        OP("pullRequest", "update", "Update", "PATCH", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}", [OWNER, REPO, PR],
           extra=[F("title", "Title"), F("body", "Body", rows=4), F("state", "State", kind="options", options=["open", "closed"]), F("base", "Base Branch"),
                  F("maintainer_can_modify", "Maintainers Can Modify", kind="boolean")], extra_name="updateFields"),
        OP("pullRequest", "close", "Close", "PATCH", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}", [OWNER, REPO, PR], static={"state": "closed"}),
        OP("pullRequest", "reopen", "Reopen", "PATCH", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}", [OWNER, REPO, PR], static={"state": "open"}),
        OP("pullRequest", "merge", "Merge", "PUT", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}/merge", [OWNER, REPO, PR],
           extra=[F("commit_title", "Commit Title"), F("commit_message", "Commit Message"), F("merge_method", "Merge Method", kind="options", options=["merge", "squash", "rebase"]),
                  F("sha", "Head SHA")]),
        OP("pullRequest", "createComment", "Create Comment", "POST", "/repos/{owner}/{repository}/issues/{pullRequestNumber}/comments", [OWNER, REPO, PR, F("body", "Body", rows=4, required=True)]),
        OP("pullRequest", "editComment", "Edit Comment", "PATCH", "/repos/{owner}/{repository}/issues/comments/{commentId}", [OWNER, REPO,
           F("commentId", "Comment ID", "path", required=True), F("body", "Body", rows=4, required=True)]),
        OP("pullRequest", "getDiff", "Get Diff", "GET", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}", [OWNER, REPO, PR], headers={"Accept": "application/vnd.github.diff"}),
        OP("pullRequest", "getPatch", "Get Patch", "GET", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}", [OWNER, REPO, PR], headers={"Accept": "application/vnd.github.patch"}),
        OP("release", "get", "Get", "GET", "/repos/{owner}/{repository}/releases/{release_id}", [OWNER, REPO, RELEASE]),
        OP("release", "update", "Update", "PATCH", "/repos/{owner}/{repository}/releases/{release_id}", [OWNER, REPO, RELEASE],
           extra=[F("tag_name", "Tag"), F("name", "Name"), F("body", "Body", rows=4), F("draft", "Draft", kind="boolean"), F("prerelease", "Prerelease", kind="boolean"),
                  F("target_commitish", "Target")]),
        OP("release", "delete", "Delete", "DELETE", "/repos/{owner}/{repository}/releases/{release_id}", [OWNER, REPO, RELEASE]),
        OP("repository", "getIssues", "Get Issues", "GET", "/repos/{owner}/{repository}/issues", [OWNER, REPO], limit="per_page", extra_name="getRepositoryIssuesFilters",
           extra=[F("state", "State", kind="options", options=["open", "closed", "all"]), F("labels", "Labels"), F("assignee", "Assignee"), F("creator", "Creator"),
                  F("mentioned", "Mentioned"), F("sort", "Sort", kind="options", options=["created", "updated", "comments"]), F("direction", "Direction", kind="options", options=["asc", "desc"]),
                  F("since", "Updated Since", kind="dateTime")]),
        OP("repository", "getPullRequests", "Get Pull Requests", "GET", "/repos/{owner}/{repository}/pulls", [OWNER, REPO], limit="per_page", extra_name="getRepositoryPullRequestsFilters",
           extra=[F("state", "State", kind="options", options=["open", "closed", "all"]), F("head", "Head"), F("base", "Base"),
                  F("sort", "Sort", kind="options", options=["created", "updated", "popularity", "long-running"]), F("direction", "Direction", kind="options", options=["asc", "desc"])]),
        OP("repository", "getLicense", "Get License", "GET", "/repos/{owner}/{repository}/license", [OWNER, REPO]),
        OP("repository", "getProfile", "Get Profile", "GET", "/repos/{owner}/{repository}/community/profile", [OWNER, REPO]),
        OP("repository", "listPopularPaths", "List Popular Paths", "GET", "/repos/{owner}/{repository}/traffic/popular/paths", [OWNER, REPO]),
        OP("repository", "listReferrers", "List Referrers", "GET", "/repos/{owner}/{repository}/traffic/popular/referrers", [OWNER, REPO]),
        OP("review", "create", "Create", "POST", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}/reviews", [OWNER, REPO, PR,
           F("event", "Event", kind="options", options=[("Approve", "APPROVE"), ("Request Change", "REQUEST_CHANGES"), ("Comment", "COMMENT"), ("Pending", "PENDING")]),
           F("body", "Body", rows=4)], extra=[F("commit_id", "Commit ID")]),
        OP("review", "get", "Get", "GET", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}/reviews/{reviewId}", [OWNER, REPO, PR, REVIEW]),
        OP("review", "getAll", "Get Many", "GET", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}/reviews", [OWNER, REPO, PR], limit="per_page"),
        OP("review", "update", "Update", "PUT", "/repos/{owner}/{repository}/pulls/{pullRequestNumber}/reviews/{reviewId}", [OWNER, REPO, PR, REVIEW, F("body", "Body", rows=4, required=True)]),
        OP("workflow", "list", "List", "GET", "/repos/{owner}/{repository}/actions/workflows", [OWNER, REPO], root="workflows"),
        OP("workflow", "get", "Get", "GET", "/repos/{owner}/{repository}/actions/workflows/{workflowId}", [OWNER, REPO, WORKFLOW]),
        OP("workflow", "getUsage", "Get Usage", "GET", "/repos/{owner}/{repository}/actions/workflows/{workflowId}/timing", [OWNER, REPO, WORKFLOW]),
        OP("workflow", "enable", "Enable", "PUT", "/repos/{owner}/{repository}/actions/workflows/{workflowId}/enable", [OWNER, REPO, WORKFLOW]),
        OP("workflow", "disable", "Disable", "PUT", "/repos/{owner}/{repository}/actions/workflows/{workflowId}/disable", [OWNER, REPO, WORKFLOW]),
        OP("workflow", "dispatch", "Dispatch", "POST", "/repos/{owner}/{repository}/actions/workflows/{workflowId}/dispatches", [OWNER, REPO, WORKFLOW,
           F("ref", "Branch or Tag", required=True, default="main"), F("inputs", "Inputs (JSON)", kind="json", default="")]),
    ], "#8b949e", "github", "Developer", "Manage issues, releases and repositories on GitHub",
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}),

    node("n8n-nodes-base.awsS3", "AWS S3", "aws", "", [], S3_OPS, "#e05243", "cloud", "Data & Storage",
         "Upload, download, copy and list files, folders and buckets in AWS S3 or any S3-compatible storage", version=2),
    node("n8n-nodes-base.mongoDb", "MongoDB", "mongoDb", "", [], MONGO_OPS, "#13aa52", "table", "Data & Storage",
         "Find, aggregate, insert, update and delete MongoDB documents and manage Atlas search indexes", version=1.2),
    node("n8n-nodes-base.ftp", "FTP", "ftp", "", [], FTP_OPS, "#2f5bea", "file", "Data & Storage",
         "List, download, upload, rename and delete files over FTP or SFTP", extra_creds=("sftp",), top=FTP_TOP),
    node("n8n-nodes-base.slack", "Slack", "slackApi", "https://slack.com/api", ["bearer:accessToken"], SLACK_OPS, "#4a154b", "slack", "Communication",
         "Send and manage Slack messages, channels, files, reactions, users and user groups", version=2.3, error_flag="ok"),
    node("n8n-nodes-base.telegram", "Telegram", "telegramApi", "{cred.baseUrl|https://api.telegram.org}/bot{cred.accessToken}", [], TELEGRAM_OPS, "#229ed9", "telegram",
         "Communication", "Send messages and files with a Telegram bot, manage chats and answer callbacks", version=1.2, error_flag="ok"),
    node("n8n-nodes-base.discord", "Discord", "discordBotApi", "https://discord.com/api/v10", ["header:Authorization:Bot {botToken}"], DISCORD_OPS, "#5865f2", "discord",
         "Communication", "Send and manage Discord messages, channels and members (bot) or post through a webhook", version=2, extra_creds=("discordWebhookApi",), top=DISCORD_TOP),
    node("n8n-nodes-base.notion", "Notion", "notionApi", "https://api.notion.com/v1", ["bearer:apiKey"], [
        OP("databasePage", "getAll", "Get Many", "POST", "/databases/{databaseId}/query",
           [F("databaseId", "Database ID", "path", required=True), F("filter", "Filter (JSON)", kind="json", default=""), F("sorts", "Sorts (JSON)", kind="json", default="")],
           root="results", limit="page_size"),
        OP("databasePage", "create", "Create", "POST", "/pages",
           [F("databaseId", "Database ID", api="parent.database_id", required=True), F("properties", "Properties (JSON)", kind="json", rows=6,
             description='Notion property values, e.g. {"Name": {"title": [{"text": {"content": "Hello"}}]}}'), F("children", "Content Blocks (JSON)", kind="json", default="")]),
        OP("databasePage", "update", "Update", "PATCH", "/pages/{pageId}", [F("pageId", "Page ID", "path", required=True), F("properties", "Properties (JSON)", kind="json", rows=6)]),
        OP("page", "get", "Get", "GET", "/pages/{pageId}", [F("pageId", "Page ID", "path", required=True)]),
        OP("page", "archive", "Archive", "PATCH", "/pages/{pageId}", [F("pageId", "Page ID", "path", required=True)], static={"archived": True}),
        OP("block", "append", "Append After", "PATCH", "/blocks/{blockId}/children", [F("blockId", "Block or Page ID", "path", required=True), F("children", "Blocks (JSON)", kind="json", rows=6)]),
        OP("block", "getAll", "Get Many Children", "GET", "/blocks/{blockId}/children", [F("blockId", "Block or Page ID", "path", required=True)], root="results", limit="page_size"),
        OP("database", "search", "Search", "POST", "/search", [F("query", "Search Text")], static={"filter": {"property": "object", "value": "database"}}, root="results"),
    ], "#8a8a86", "notion", "Productivity", "Read and write Notion databases, pages and blocks", headers={"Notion-Version": "2022-06-28"}),

    node("n8n-nodes-base.airtable", "Airtable", "airtableTokenApi", "https://api.airtable.com/v0", ["bearer:accessToken"], [
        OP("record", "search", "Search", "GET", "/{base}/{table}",
           [F("base", "Base ID", "path", required=True, placeholder="appXXXXXXXX"), F("table", "Table", "path", required=True),
            F("filterByFormula", "Filter By Formula", "query", placeholder="NOT({Name} = '')"), F("view", "View", "query")], root="records", limit="maxRecords"),
        OP("record", "get", "Get", "GET", "/{base}/{table}/{id}", [F("base", "Base ID", "path", required=True), F("table", "Table", "path", required=True), F("id", "Record ID", "path", required=True)]),
        OP("record", "create", "Create", "POST", "/{base}/{table}",
           [F("base", "Base ID", "path", required=True), F("table", "Table", "path", required=True), F("fields", "Fields (JSON)", kind="json", rows=6, description='e.g. {"Name": "Ada"}')],
           extra=[F("typecast", "Typecast", kind="boolean")]),
        OP("record", "update", "Update", "PATCH", "/{base}/{table}/{id}",
           [F("base", "Base ID", "path", required=True), F("table", "Table", "path", required=True), F("id", "Record ID", "path", required=True), F("fields", "Fields (JSON)", kind="json", rows=6)]),
        OP("record", "deleteRecord", "Delete", "DELETE", "/{base}/{table}/{id}", [F("base", "Base ID", "path", required=True), F("table", "Table", "path", required=True), F("id", "Record ID", "path", required=True)]),
    ], "#18bfff", "table", "Data & Storage", "Read, create and update Airtable records"),

    node("n8n-nodes-base.stripe", "Stripe", "stripeApi", "https://api.stripe.com/v1", ["bearer:secretKey"], [
        OP("customer", "create", "Create", "POST", "/customers", [F("name", "Name"), F("email", "Email")],
           extra=[F("description", "Description"), F("phone", "Phone"), F("metadata", "Metadata (JSON)", kind="json")]),
        OP("customer", "get", "Get", "GET", "/customers/{customerId}", [F("customerId", "Customer ID", "path", required=True)]),
        OP("customer", "getAll", "Get Many", "GET", "/customers", [F("email", "Email", "query")], root="data", limit="limit"),
        OP("customer", "delete", "Delete", "DELETE", "/customers/{customerId}", [F("customerId", "Customer ID", "path", required=True)]),
        OP("charge", "get", "Get", "GET", "/charges/{chargeId}", [F("chargeId", "Charge ID", "path", required=True)]),
        OP("charge", "getAll", "Get Many", "GET", "/charges", [], root="data", limit="limit"),
        OP("paymentIntent", "getAll", "Get Many", "GET", "/payment_intents", [], root="data", limit="limit"),
        OP("balance", "get", "Get", "GET", "/balance", []),
    ], "#635bff", "card", "Sales", "Customers, charges and balance in Stripe", body_type="form"),

    node("n8n-nodes-base.todoist", "Todoist", "todoistApi", "https://api.todoist.com/api/v1", ["bearer:apiKey"], [
        OP("task", "create", "Create", "POST", "/tasks", [F("content", "Content", required=True)],
           extra=[F("description", "Description"), F("dueString", "Due (natural language)", api="due_string", placeholder="tomorrow 9am"),
                  F("projectId", "Project ID", api="project_id"), F("priority", "Priority (1-4)", kind="number", default=1), F("labels", "Labels", kind="list")]),
        OP("task", "get", "Get", "GET", "/tasks/{taskId}", [F("taskId", "Task ID", "path", required=True)]),
        OP("task", "getAll", "Get Many", "GET", "/tasks", [F("projectId", "Project ID", "query", api="project_id")], root="results", limit="limit"),
        OP("task", "close", "Close", "POST", "/tasks/{taskId}/close", [F("taskId", "Task ID", "path", required=True)]),
        OP("task", "delete", "Delete", "DELETE", "/tasks/{taskId}", [F("taskId", "Task ID", "path", required=True)]),
    ], "#e44332", "check", "Productivity", "Create and manage Todoist tasks"),

    node("n8n-nodes-base.googleSheets", "Google Sheets", "googleSheetsOAuth2Api", "https://sheets.googleapis.com/v4", ["oauth2"], [
        OP("sheet", "read", "Get Row(s)", "GET", "/spreadsheets/{documentId}/values/{range}",
           [F("documentId", "Document ID", "path", required=True, description="The long ID in the sheet URL"), F("range", "Range", "path", default="Sheet1!A:Z", required=True)],
           transform="sheetRows", description="First row is used as column names"),
        OP("sheet", "append", "Append Row", "POST", "/spreadsheets/{documentId}/values/{range}:append",
           [F("documentId", "Document ID", "path", required=True), F("range", "Range", "path", default="Sheet1!A:Z", required=True),
            F("columns", "Columns", "none", kind="list", description="Field names to write, in column order. Empty = all fields of the item")],
           static_query={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}, body_from="sheetRow"),
        OP("sheet", "clear", "Clear", "POST", "/spreadsheets/{documentId}/values/{range}:clear",
           [F("documentId", "Document ID", "path", required=True), F("range", "Range", "path", default="Sheet1!A:Z", required=True)]),
        OP("spreadsheet", "create", "Create", "POST", "/spreadsheets", [F("title", "Title", api="properties.title", required=True)]),
    ], "#0f9d58", "table", "Data & Storage", "Read, append and clear rows in Google Sheets"),

    node("n8n-nodes-base.gmail", "Gmail", "gmailOAuth2", "https://gmail.googleapis.com/gmail/v1/users/me", ["oauth2"], GMAIL_OPS, "#ea4335", "mail", "Communication",
         "Send, reply to, read and label Gmail messages, threads, drafts and labels", version=2.1),

    node("n8n-nodes-base.googleCalendar", "Google Calendar", "googleCalendarOAuth2Api", "https://www.googleapis.com/calendar/v3", ["oauth2"], [
        OP("event", "create", "Create", "POST", "/calendars/{calendar}/events",
           [F("calendar", "Calendar ID", "path", default="primary", required=True), F("start", "Start", api="start.dateTime", required=True, placeholder="2026-12-24T18:00:00+01:00"),
            F("end", "End", api="end.dateTime", required=True), F("summary", "Title")],
           extra=[F("description", "Description"), F("location", "Location")]),
        OP("event", "getAll", "Get Many", "GET", "/calendars/{calendar}/events",
           [F("calendar", "Calendar ID", "path", default="primary", required=True), F("timeMin", "After", "query"), F("timeMax", "Before", "query")],
           static_query={"singleEvents": "true", "orderBy": "startTime"}, root="items", limit="maxResults"),
        OP("event", "delete", "Delete", "DELETE", "/calendars/{calendar}/events/{eventId}",
           [F("calendar", "Calendar ID", "path", default="primary", required=True), F("eventId", "Event ID", "path", required=True)]),
    ], "#4285f4", "calendar", "Productivity", "Create and list Google Calendar events"),

    node("n8n-nodes-base.jira", "Jira Software", "jiraSoftwareCloudApi", "{cred.domain}/rest/api", ["basic:email:apiToken"], [
        OP("issue", "create", "Create", "POST", "/2/issue",
           [F("project", "Project Key", api="fields.project.key", required=True), F("issueType", "Issue Type", api="fields.issuetype.name", default="Task"),
            F("summary", "Summary", api="fields.summary", required=True)],
           extra=[F("description", "Description", api="description", rows=4), F("labels", "Labels", api="labels", kind="list")], extra_target="fields"),
        OP("issue", "get", "Get", "GET", "/2/issue/{issueKey}", [F("issueKey", "Issue Key", "path", required=True)]),
        OP("issue", "getAll", "Get Many (JQL)", "GET", "/3/search/jql", [F("jql", "JQL", "query", default="order by created DESC"), F("fields", "Fields", "query", default="*navigable")],
           root="issues", limit="maxResults"),
        OP("issue", "transition", "Transition", "POST", "/2/issue/{issueKey}/transitions",
           [F("issueKey", "Issue Key", "path", required=True), F("transitionId", "Transition ID", api="transition.id", required=True)]),
        OP("issueComment", "add", "Add", "POST", "/2/issue/{issueKey}/comment", [F("issueKey", "Issue Key", "path", required=True), F("comment", "Comment", api="body", rows=4)]),
    ], "#2684ff", "check", "Developer", "Create and update Jira issues"),

    node("n8n-nodes-base.hubspot", "HubSpot", "hubspotAppToken", "https://api.hubapi.com", ["bearer:appToken"], [
        OP("contact", "upsert", "Create", "POST", "/crm/v3/objects/contacts", [F("email", "Email", api="properties.email", required=True)],
           extra=[F("firstname", "First Name"), F("lastname", "Last Name"), F("phone", "Phone"), F("company", "Company")], extra_target="properties"),
        OP("contact", "get", "Get", "GET", "/crm/v3/objects/contacts/{contactId}", [F("contactId", "Contact ID", "path", required=True)]),
        OP("contact", "getAll", "Get Many", "GET", "/crm/v3/objects/contacts", [], root="results", limit="limit"),
        OP("contact", "delete", "Delete", "DELETE", "/crm/v3/objects/contacts/{contactId}", [F("contactId", "Contact ID", "path", required=True)]),
        OP("deal", "create", "Create", "POST", "/crm/v3/objects/deals", [F("dealName", "Deal Name", api="properties.dealname", required=True), F("stage", "Deal Stage", api="properties.dealstage", default="appointmentscheduled")],
           extra=[F("amount", "Amount"), F("pipeline", "Pipeline"), F("closedate", "Close Date")], extra_target="properties"),
        OP("deal", "getAll", "Get Many", "GET", "/crm/v3/objects/deals", [], root="results", limit="limit"),
    ], "#ff7a59", "users", "Sales", "Contacts and deals in HubSpot CRM"),

    node("n8n-nodes-base.twilio", "Twilio", "twilioApi", "https://api.twilio.com/2010-04-01/Accounts/{cred.accountSid}", ["basic:accountSid:authToken"], [
        OP("sms", "send", "Send", "POST", "/Messages.json",
           [F("from", "From", api="From", required=True, placeholder="+14155238886"), F("to", "To", api="To", required=True), F("message", "Message", api="Body", rows=3)]),
        OP("call", "make", "Make", "POST", "/Calls.json",
           [F("from", "From", api="From", required=True), F("to", "To", api="To", required=True), F("twiml", "TwiML", api="Twiml", rows=3, placeholder="<Response><Say>Hello</Say></Response>")]),
    ], "#f22f46", "phone", "Communication", "Send SMS and place calls with Twilio", body_type="form"),

    node("n8n-nodes-base.trello", "Trello", "trelloApi", "https://api.trello.com/1", ["query:key:apiKey", "query:token:apiToken"], [
        OP("card", "create", "Create", "POST", "/cards",
           [F("listId", "List ID", "query", api="idList", required=True), F("name", "Name", "query", required=True), F("description", "Description", "query", api="desc")],
           extra=[F("due", "Due Date"), F("idLabels", "Label IDs")]),
        OP("card", "get", "Get", "GET", "/cards/{id}", [F("id", "Card ID", "path", required=True)]),
        OP("card", "update", "Update", "PUT", "/cards/{id}", [F("id", "Card ID", "path", required=True)],
           extra=[F("name", "Name"), F("desc", "Description"), F("idList", "Move to List ID"), F("closed", "Archived", kind="boolean")]),
        OP("card", "delete", "Delete", "DELETE", "/cards/{id}", [F("id", "Card ID", "path", required=True)]),
        OP("list", "getCards", "Get Cards", "GET", "/lists/{id}/cards", [F("id", "List ID", "path", required=True)]),
    ], "#0079bf", "layers", "Productivity", "Create and update Trello cards"),

    node("n8n-nodes-base.supabase", "Supabase", "supabaseApi", "{cred.host}/rest/v1", ["header:apikey:serviceRole", "bearer:serviceRole"], [
        OP("row", "getAll", "Get Many", "GET", "/{tableId}", [F("tableId", "Table", "path", required=True), F("filter", "Filter", "none", description="PostgREST filter, e.g. status=eq.active")],
           static_query={"select": "*"}, limit="limit", transform="postgrestFilter"),
        OP("row", "create", "Create", "POST", "/{tableId}", [F("tableId", "Table", "path", required=True), F("data", "Row (JSON)", "none", kind="json", rows=6)], body_from="jsonParam"),
        OP("row", "update", "Update", "PATCH", "/{tableId}", [F("tableId", "Table", "path", required=True), F("filter", "Filter", "none", required=True, placeholder="id=eq.1"),
                                                            F("data", "Fields (JSON)", "none", kind="json", rows=6)], body_from="jsonParam", transform="postgrestFilter"),
        OP("row", "delete", "Delete", "DELETE", "/{tableId}", [F("tableId", "Table", "path", required=True), F("filter", "Filter", "none", required=True, placeholder="id=eq.1")],
           transform="postgrestFilter"),
    ], "#3ecf8e", "database", "Data & Storage", "Query and change rows in Supabase", headers={"Prefer": "return=representation"}),

    node("n8n-nodes-base.openWeatherMap", "OpenWeatherMap", "openWeatherMapApi", "https://api.openweathermap.org/data/2.5", ["query:appid:accessToken"], [
        OP("weather", "currentWeather", "Current Weather", "GET", "/weather",
           [F("cityName", "City", "query", api="q", placeholder="berlin,de"), F("format", "Units", "query", api="units", kind="options", options=[("Metric", "metric"), ("Imperial", "imperial"), ("Kelvin", "standard")])]),
        OP("weather", "5DayForecast", "5 Day Forecast", "GET", "/forecast",
           [F("cityName", "City", "query", api="q"), F("format", "Units", "query", api="units", kind="options", options=[("Metric", "metric"), ("Imperial", "imperial"), ("Kelvin", "standard")])], root="list"),
    ], "#eb6e4b", "sun", "Utility", "Current weather and forecasts"),

    node("n8n-nodes-base.mattermost", "Mattermost", "mattermostApi", "{cred.baseUrl}/api/v4", ["bearer:accessToken"], [
        OP("message", "post", "Post", "POST", "/posts", [F("channelId", "Channel ID", api="channel_id", required=True), F("message", "Message", rows=3)]),
    ], "#3d6bb3", "chat", "Communication", "Post messages to Mattermost"),
]

N = lambda name, display, **kw: F(name, display, "none", **kw)  # value used only inside a template

LINEAR_CREATE = "mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { id identifier title url } } }"
LINEAR_GET = "query($id: String!) { issue(id: $id) { id identifier title description url state { name } assignee { name } } }"
MONDAY_CREATE = "mutation ($board: ID!, $name: String!, $values: JSON) { create_item (board_id: $board, item_name: $name, column_values: $values) { id name } }"

NODES += [
    node("n8n-nodes-base.pipedrive", "Pipedrive", "pipedriveApi", "https://api.pipedrive.com/v1", ["query:api_token:apiToken"], [
        OP("person", "create", "Create", "POST", "/persons", [F("name", "Name", required=True)], extra=[F("email", "Email", kind="list"), F("phone", "Phone", kind="list"), F("org_id", "Organization ID", kind="number")], root="data"),
        OP("person", "getAll", "Get Many", "GET", "/persons", [], root="data", limit="limit"),
        OP("deal", "create", "Create", "POST", "/deals", [F("title", "Title", required=True)], extra=[F("value", "Value", kind="number"), F("currency", "Currency"), F("person_id", "Person ID", kind="number")], root="data"),
        OP("deal", "get", "Get", "GET", "/deals/{dealId}", [F("dealId", "Deal ID", "path", required=True)], root="data"),
        OP("deal", "getAll", "Get Many", "GET", "/deals", [], root="data", limit="limit"),
        OP("activity", "create", "Create", "POST", "/activities", [F("subject", "Subject", required=True), F("type", "Type", default="call")], extra=[F("due_date", "Due Date"), F("deal_id", "Deal ID", kind="number")], root="data"),
    ], "#3ea86b", "users", "Sales", "Persons, deals and activities in Pipedrive"),

    node("n8n-nodes-base.sendGrid", "SendGrid", "sendGridApi", "https://api.sendgrid.com/v3", ["bearer:apiKey"], [
        OP("mail", "send", "Send", "POST", "/mail/send",
           [N("fromEmail", "Sender Email", required=True), N("toEmail", "Recipient Email", required=True), N("subject", "Subject"),
            N("contentType", "Content Type", kind="options", options=[("Text", "text/plain"), ("HTML", "text/html")]), N("contentValue", "Message", rows=5)],
           template={"personalizations": [{"to": [{"email": "{toEmail}"}]}], "from": {"email": "{fromEmail}"}, "subject": "{subject}", "content": [{"type": "{contentType}", "value": "{contentValue}"}]}),
        OP("contact", "upsert", "Create or Update", "PUT", "/marketing/contacts", [N("email", "Email", required=True), N("firstName", "First Name"), N("lastName", "Last Name")],
           template={"contacts": [{"email": "{email}", "first_name": "{firstName}", "last_name": "{lastName}"}]}),
    ], "#1a82e2", "mail", "Communication", "Send email and manage contacts with SendGrid"),

    node("n8n-nodes-base.sendInBlue", "Brevo", "sendInBlueApi", "https://api.brevo.com/v3", ["header:api-key:apiKey"], [
        OP("email", "send", "Send", "POST", "/smtp/email",
           [N("senderEmail", "Sender Email", required=True), N("receipientEmail", "Recipient Email", required=True), N("subject", "Subject"), N("htmlContent", "HTML Content", rows=5)],
           template={"sender": {"email": "{senderEmail}"}, "to": [{"email": "{receipientEmail}"}], "subject": "{subject}", "htmlContent": "{htmlContent}"}),
        OP("contact", "create", "Create", "POST", "/contacts", [F("email", "Email", required=True), F("attributes", "Attributes (JSON)", kind="json", default="")], extra=[F("listIds", "List IDs (JSON array)", kind="json")]),
    ], "#0b996e", "mail", "Communication", "Transactional email and contacts with Brevo"),

    node("n8n-nodes-base.mailgun", "Mailgun", "mailgunApi", "{cred.apiDomain}/v3", ["basic:=api:apiKey"], [
        OP("message", "send", "Send", "POST", "/{cred.emailDomain}/messages",
           [F("fromEmail", "From", api="from", required=True), F("toEmail", "To", api="to", required=True), F("subject", "Subject"), F("text", "Text", rows=5)], extra=[F("html", "HTML", rows=5), F("cc", "CC"), F("bcc", "BCC")]),
    ], "#c02126", "mail", "Communication", "Send email through Mailgun", body_type="form"),

    node("n8n-nodes-base.clickUp", "ClickUp", "clickUpApi", "https://api.clickup.com/api/v2", ["header:Authorization:accessToken"], [
        OP("task", "create", "Create", "POST", "/list/{list}/task", [F("list", "List ID", "path", required=True), F("name", "Name", required=True)],
           extra=[F("description", "Description"), F("status", "Status"), F("priority", "Priority (1-4)", kind="number"), F("due_date", "Due Date (ms)", kind="number")]),
        OP("task", "get", "Get", "GET", "/task/{id}", [F("id", "Task ID", "path", required=True)]),
        OP("task", "getAll", "Get Many", "GET", "/list/{list}/task", [F("list", "List ID", "path", required=True)], root="tasks"),
        OP("task", "update", "Update", "PUT", "/task/{id}", [F("id", "Task ID", "path", required=True)], extra=[F("name", "Name"), F("description", "Description"), F("status", "Status")]),
        OP("task", "delete", "Delete", "DELETE", "/task/{id}", [F("id", "Task ID", "path", required=True)]),
    ], "#7b68ee", "check", "Productivity", "Tasks in ClickUp"),

    node("n8n-nodes-base.asana", "Asana", "asanaApi", "https://app.asana.com/api/1.0", ["bearer:accessToken"], [
        OP("task", "create", "Create", "POST", "/tasks", [N("workspace", "Workspace ID", required=True), N("name", "Name", required=True), N("projectId", "Project ID"), N("notes", "Notes", rows=3)],
           template={"data": {"workspace": "{workspace}", "name": "{name}", "projects": ["{projectId}"], "notes": "{notes}"}}, root="data"),
        OP("task", "get", "Get", "GET", "/tasks/{id}", [F("id", "Task ID", "path", required=True)], root="data"),
        OP("task", "getAll", "Get Many", "GET", "/tasks", [F("projectId", "Project ID", "query", api="project", required=True)], root="data", limit="limit"),
        OP("task", "update", "Update", "PUT", "/tasks/{id}", [F("id", "Task ID", "path", required=True), N("name", "Name"), N("completed", "Completed", kind="boolean")],
           template={"data": {"name": "{name}", "completed": "{completed}"}}, root="data"),
    ], "#f06a6a", "check", "Productivity", "Tasks in Asana"),

    node("n8n-nodes-base.linear", "Linear", "linearApi", "https://api.linear.app", ["header:Authorization:apiKey"], [
        OP("issue", "create", "Create", "POST", "/graphql", [N("teamId", "Team ID", required=True), N("title", "Title", required=True), N("description", "Description", rows=4)],
           template={"query": LINEAR_CREATE, "variables": {"input": {"teamId": "{teamId}", "title": "{title}", "description": "{description}"}}}, root="data.issueCreate.issue"),
        OP("issue", "get", "Get", "POST", "/graphql", [N("issueId", "Issue ID", required=True)], template={"query": LINEAR_GET, "variables": {"id": "{issueId}"}}, root="data.issue"),
    ], "#5e6ad2", "check", "Developer", "Create and read Linear issues"),

    node("n8n-nodes-base.mondayCom", "Monday.com", "mondayComApi", "https://api.monday.com", ["header:Authorization:apiToken"], [
        OP("boardItem", "create", "Create", "POST", "/v2", [N("boardId", "Board ID", required=True), N("name", "Name", required=True), N("columnValues", "Column Values (JSON string)", default="")],
           template={"query": MONDAY_CREATE, "variables": {"board": "{boardId}", "name": "{name}", "values": "{columnValues}"}}, root="data.create_item"),
    ], "#ff3d57", "layers", "Productivity", "Create items on monday.com boards"),

    node("n8n-nodes-base.gitlab", "GitLab", "gitlabApi", "{cred.server}/api/v4", ["header:PRIVATE-TOKEN:accessToken"], [
        OP("issue", "create", "Create", "POST", "/projects/{projectId}/issues", [F("projectId", "Project ID or Path", "path", required=True, placeholder="group/project"), F("title", "Title", required=True), F("description", "Description", rows=4)],
           extra=[F("labels", "Labels"), F("assignee_ids", "Assignee IDs", kind="list")]),
        OP("issue", "getAll", "Get Many", "GET", "/projects/{projectId}/issues", [F("projectId", "Project ID or Path", "path", required=True), F("state", "State", "query", kind="options", options=["opened", "closed", "all"])], limit="per_page"),
        OP("issue", "createComment", "Create Comment", "POST", "/projects/{projectId}/issues/{issueNumber}/notes", [F("projectId", "Project ID or Path", "path", required=True), F("issueNumber", "Issue IID", "path", required=True), F("body", "Body", rows=4)]),
        OP("repository", "get", "Get", "GET", "/projects/{projectId}", [F("projectId", "Project ID or Path", "path", required=True)]),
    ], "#fc6d26", "github", "Developer", "Issues and projects on GitLab"),

    node("n8n-nodes-base.shopify", "Shopify", "shopifyAccessTokenApi", "https://{cred.shopSubdomain}.myshopify.com/admin/api/2024-10", ["header:X-Shopify-Access-Token:accessToken"], [
        OP("order", "getAll", "Get Many", "GET", "/orders.json", [F("status", "Status", "query", kind="options", options=["any", "open", "closed", "cancelled"])], root="orders", limit="limit"),
        OP("order", "get", "Get", "GET", "/orders/{orderId}.json", [F("orderId", "Order ID", "path", required=True)], root="order"),
        OP("product", "getAll", "Get Many", "GET", "/products.json", [], root="products", limit="limit"),
        OP("product", "create", "Create", "POST", "/products.json", [N("title", "Title", required=True), N("bodyHtml", "Description (HTML)", rows=4), N("vendor", "Vendor"), N("productType", "Product Type")],
           template={"product": {"title": "{title}", "body_html": "{bodyHtml}", "vendor": "{vendor}", "product_type": "{productType}"}}, root="product"),
    ], "#95bf47", "card", "Sales", "Orders and products in Shopify"),

    node("n8n-nodes-base.wooCommerce", "WooCommerce", "wooCommerceApi", "{cred.url}/wp-json/wc/v3", ["basic:consumerKey:consumerSecret"], [
        OP("order", "getAll", "Get Many", "GET", "/orders", [F("status", "Status", "query", default="any")], limit="per_page"),
        OP("order", "get", "Get", "GET", "/orders/{orderId}", [F("orderId", "Order ID", "path", required=True)]),
        OP("order", "update", "Update", "PUT", "/orders/{orderId}", [F("orderId", "Order ID", "path", required=True)], extra=[F("status", "Status"), F("customer_note", "Customer Note")]),
        OP("product", "getAll", "Get Many", "GET", "/products", [], limit="per_page"),
        OP("product", "create", "Create", "POST", "/products", [F("name", "Name", required=True), F("regularPrice", "Regular Price", api="regular_price")], extra=[F("description", "Description"), F("sku", "SKU"), F("status", "Status")]),
    ], "#96588a", "card", "Sales", "Orders and products in WooCommerce"),

    node("n8n-nodes-base.freshdesk", "Freshdesk", "freshdeskApi", "https://{cred.domain}.freshdesk.com/api/v2", ["basic:apiKey:=X"], [
        OP("ticket", "create", "Create", "POST", "/tickets", [F("email", "Requester Email", required=True), F("subject", "Subject", required=True), F("description", "Description", rows=4),
                                                         F("priority", "Priority (1-4)", kind="number", default=1), F("status", "Status (2=open)", kind="number", default=2)]),
        OP("ticket", "get", "Get", "GET", "/tickets/{ticketId}", [F("ticketId", "Ticket ID", "path", required=True)]),
        OP("ticket", "getAll", "Get Many", "GET", "/tickets", [], limit="per_page"),
        OP("ticket", "update", "Update", "PUT", "/tickets/{ticketId}", [F("ticketId", "Ticket ID", "path", required=True)], extra=[F("status", "Status", kind="number"), F("priority", "Priority", kind="number")]),
    ], "#25c16f", "chat", "Support", "Tickets in Freshdesk"),

    node("n8n-nodes-base.pushover", "Pushover", "pushoverApi", "https://api.pushover.net/1", [], [
        OP("message", "push", "Push", "POST", "/messages.json", [N("userKey", "User Key", required=True), N("message", "Message", required=True), N("title", "Title"), N("priority", "Priority (-2..2)", kind="number", default=0)],
           template={"token": "{cred.apiKey}", "user": "{userKey}", "message": "{message}", "title": "{title}", "priority": "{priority}"}),
    ], "#249df1", "phone", "Communication", "Push notifications with Pushover", body_type="form"),

    node("n8n-nodes-base.ntfy", "ntfy", "ntfyApi", "{cred.url}", ["bearer:token"], [
        OP("message", "publish", "Publish", "POST", "/", [N("topic", "Topic", required=True), N("message", "Message", required=True), N("title", "Title"), N("priority", "Priority (1-5)", kind="number", default=3), N("tags", "Tags", kind="list")],
           template={"topic": "{topic}", "message": "{message}", "title": "{title}", "priority": "{priority}", "tags": "{tags}"}),
    ], "#317f6f", "phone", "Communication", "Push notifications with ntfy"),

    node("n8n-nodes-base.baserow", "Baserow", "baserowApi", "{cred.host}/api", [], [
        OP("row", "getAll", "Get Many", "GET", "/database/rows/table/{tableId}/", [F("tableId", "Table ID", "path", required=True), F("search", "Search", "query")],
           static_query={"user_field_names": "true"}, root="results", limit="size"),
        OP("row", "get", "Get", "GET", "/database/rows/table/{tableId}/{rowId}/", [F("tableId", "Table ID", "path", required=True), F("rowId", "Row ID", "path", required=True)], static_query={"user_field_names": "true"}),
        OP("row", "create", "Create", "POST", "/database/rows/table/{tableId}/", [F("tableId", "Table ID", "path", required=True), F("data", "Row (JSON)", "none", kind="json", rows=6)],
           static_query={"user_field_names": "true"}, body_from="jsonParam"),
        OP("row", "update", "Update", "PATCH", "/database/rows/table/{tableId}/{rowId}/", [F("tableId", "Table ID", "path", required=True), F("rowId", "Row ID", "path", required=True), F("data", "Fields (JSON)", "none", kind="json", rows=6)],
           static_query={"user_field_names": "true"}, body_from="jsonParam"),
        OP("row", "delete", "Delete", "DELETE", "/database/rows/table/{tableId}/{rowId}/", [F("tableId", "Table ID", "path", required=True), F("rowId", "Row ID", "path", required=True)]),
    ], "#4f8ff7", "table", "Data & Storage", "Rows in Baserow", headers={"Authorization": "Token {cred.apiToken}"}),

    node("n8n-nodes-base.microsoftOutlook", "Microsoft Outlook", "microsoftOutlookOAuth2Api", "https://graph.microsoft.com/v1.0/me", ["oauth2"], [
        OP("message", "send", "Send", "POST", "/sendMail",
           [N("toRecipients", "To", required=True), N("subject", "Subject"), N("bodyContentType", "Body Type", kind="options", options=[("Text", "Text"), ("HTML", "HTML")]), N("bodyContent", "Body", rows=5)],
           template={"message": {"subject": "{subject}", "body": {"contentType": "{bodyContentType}", "content": "{bodyContent}"}, "toRecipients": [{"emailAddress": {"address": "{toRecipients}"}}]}}),
        OP("message", "getAll", "Get Many", "GET", "/messages", [F("filter", "Filter", "query", api="$filter")], root="value", limit="$top"),
        OP("event", "create", "Create Event", "POST", "/events", [N("subject", "Subject", required=True), N("start", "Start (ISO)", required=True), N("end", "End (ISO)", required=True), N("timeZone", "Time Zone", default="UTC")],
           template={"subject": "{subject}", "start": {"dateTime": "{start}", "timeZone": "{timeZone}"}, "end": {"dateTime": "{end}", "timeZone": "{timeZone}"}}),
    ], "#0078d4", "mail", "Communication", "Send mail and create events in Outlook"),

    node("n8n-nodes-base.microsoftTeams", "Microsoft Teams", "microsoftTeamsOAuth2Api", "https://graph.microsoft.com/v1.0", ["oauth2"], [
        OP("channelMessage", "create", "Create", "POST", "/teams/{teamId}/channels/{channelId}/messages",
           [F("teamId", "Team ID", "path", required=True), F("channelId", "Channel ID", "path", required=True), N("message", "Message", rows=3),
            N("contentType", "Content Type", kind="options", options=[("Text", "text"), ("HTML", "html")])],
           template={"body": {"contentType": "{contentType}", "content": "{message}"}}),
    ], "#6264a7", "chat", "Communication", "Post messages to Teams channels"),

    node("n8n-nodes-base.whatsApp", "WhatsApp Business Cloud", "whatsAppApi", "https://graph.facebook.com/v21.0", ["bearer:accessToken"], [
        OP("message", "send", "Send", "POST", "/{phoneNumberId}/messages",
           [F("phoneNumberId", "Sender Phone Number ID", "path", required=True), N("recipientPhoneNumber", "Recipient Phone Number", required=True), N("textBody", "Text", rows=3)],
           template={"messaging_product": "whatsapp", "to": "{recipientPhoneNumber}", "type": "text", "text": {"body": "{textBody}"}}),
    ], "#25d366", "chat", "Communication", "Send WhatsApp messages via the Cloud API"),

    node("n8n-nodes-base.mailchimp", "Mailchimp", "mailchimpApi", "https://{cred.server}.api.mailchimp.com/3.0", ["basic:=anystring:apiKey"], [
        OP("member", "create", "Create", "POST", "/lists/{list}/members",
           [F("list", "Audience ID", "path", required=True), N("email", "Email", required=True), N("status", "Status", kind="options", options=["subscribed", "pending", "unsubscribed"]),
            N("mergeFields", "Merge Fields (JSON)", kind="json", default="")],
           template={"email_address": "{email}", "status": "{status}", "merge_fields": "{mergeFields}"}),
        OP("member", "getAll", "Get Many", "GET", "/lists/{list}/members", [F("list", "Audience ID", "path", required=True)], root="members", limit="count"),
    ], "#ffe01b", "mail", "Marketing", "Audience members in Mailchimp"),

    node("n8n-nodes-base.webflow", "Webflow", "webflowApi", "https://api.webflow.com/v2", ["bearer:accessToken"], [
        OP("item", "getAll", "Get Many", "GET", "/collections/{collectionId}/items", [F("collectionId", "Collection ID", "path", required=True)], root="items", limit="limit"),
        OP("item", "create", "Create", "POST", "/collections/{collectionId}/items", [F("collectionId", "Collection ID", "path", required=True), N("fieldData", "Field Data (JSON)", kind="json", rows=6),
                                                                                N("isDraft", "Draft", kind="boolean")],
           template={"fieldData": "{fieldData}", "isDraft": "{isDraft}"}),
    ], "#4353ff", "layers", "Marketing", "CMS items in Webflow"),

    node("n8n-nodes-base.typeform", "Typeform", "typeformApi", "https://api.typeform.com", ["bearer:accessToken"], [
        OP("response", "getAll", "Get Responses", "GET", "/forms/{formId}/responses", [F("formId", "Form ID", "path", required=True), F("since", "Since", "query")], root="items", limit="page_size"),
        OP("form", "getAll", "Get Forms", "GET", "/forms", [], root="items", limit="page_size"),
    ], "#8b8b8b", "form", "Marketing", "Forms and responses in Typeform"),

    node("n8n-nodes-base.googleDrive", "Google Drive", "googleDriveOAuth2Api", "https://www.googleapis.com/drive/v3", ["oauth2"], DRIVE_OPS, "#1fa463", "file",
         "Data & Storage", "Upload, download, search, share and organise files, folders and shared drives in Google Drive", version=3),
]

CREDS = [
    {"name": "githubApi", "displayName": "GitHub API", "properties": [
        {"displayName": "Access Token", "name": "accessToken", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "GitHub Server", "name": "server", "type": "string", "default": "https://api.github.com"}]},
    {"name": "todoistApi", "displayName": "Todoist API", "properties": [{"displayName": "API Key", "name": "apiKey", "type": "string", "typeOptions": {"password": True}, "default": ""}]},
    {"name": "jiraSoftwareCloudApi", "displayName": "Jira SW Cloud API", "properties": [
        {"displayName": "Email", "name": "email", "type": "string", "default": ""},
        {"displayName": "API Token", "name": "apiToken", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Domain", "name": "domain", "type": "string", "default": "", "placeholder": "https://example.atlassian.net"}]},
    {"name": "hubspotAppToken", "displayName": "HubSpot App Token", "properties": [{"displayName": "App Token", "name": "appToken", "type": "string", "typeOptions": {"password": True}, "default": ""}]},
    {"name": "twilioApi", "displayName": "Twilio API", "properties": [
        {"displayName": "Account SID", "name": "accountSid", "type": "string", "default": ""},
        {"displayName": "Auth Token", "name": "authToken", "type": "string", "typeOptions": {"password": True}, "default": ""}]},
    {"name": "trelloApi", "displayName": "Trello API", "properties": [
        {"displayName": "API Key", "name": "apiKey", "type": "string", "default": ""},
        {"displayName": "API Token", "name": "apiToken", "type": "string", "typeOptions": {"password": True}, "default": ""}]},
    {"name": "supabaseApi", "displayName": "Supabase API", "properties": [
        {"displayName": "Host", "name": "host", "type": "string", "default": "", "placeholder": "https://xyz.supabase.co"},
        {"displayName": "Service Role Secret", "name": "serviceRole", "type": "string", "typeOptions": {"password": True}, "default": ""}]},
    {"name": "openWeatherMapApi", "displayName": "OpenWeatherMap API", "properties": [{"displayName": "Access Token", "name": "accessToken", "type": "string", "typeOptions": {"password": True}, "default": ""}]},
    {"name": "mattermostApi", "displayName": "Mattermost API", "properties": [
        {"displayName": "Access Token", "name": "accessToken", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Base URL", "name": "baseUrl", "type": "string", "default": "", "placeholder": "https://chat.example.com"}]},
]


def microsoft(name, display, scope):
    return {"name": name, "displayName": display, "properties": [
        {"displayName": "Client ID", "name": "clientId", "type": "string", "default": ""},
        {"displayName": "Client Secret", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Tenant", "name": "tenant", "type": "hidden", "default": "common"},
        {"displayName": "Authorization URL", "name": "authUrl", "type": "hidden", "default": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "hidden", "default": "https://login.microsoftonline.com/common/oauth2/v2.0/token"},
        {"displayName": "Scope", "name": "scope", "type": "hidden", "default": scope},
        {"displayName": "Authentication", "name": "authentication", "type": "hidden", "default": "body"}]}


def simple(name, display, *fields):
    """fields: (name, display, secret, default, placeholder)"""
    return {"name": name, "displayName": display, "properties": [
        {"displayName": d, "name": n, "type": "string", "default": dflt, **({"typeOptions": {"password": True}} if secret else {}), **({"placeholder": ph} if ph else {})}
        for n, d, secret, dflt, ph in fields]}


def google(name, display, scope):
    return {"name": name, "displayName": display, "properties": [
        {"displayName": "Client ID", "name": "clientId", "type": "string", "default": ""},
        {"displayName": "Client Secret", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Authorization URL", "name": "authUrl", "type": "hidden", "default": "https://accounts.google.com/o/oauth2/v2/auth"},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "hidden", "default": "https://oauth2.googleapis.com/token"},
        {"displayName": "Scope", "name": "scope", "type": "hidden", "default": scope},
        {"displayName": "Auth URI Query Parameters", "name": "authQueryParameters", "type": "hidden", "default": "access_type=offline&prompt=consent"},
        {"displayName": "Authentication", "name": "authentication", "type": "hidden", "default": "body"}]}


CREDS += [
    {"name": "oAuth2Api", "displayName": "OAuth2 API", "properties": [
        {"displayName": "Grant Type", "name": "grantType", "type": "options", "default": "authorizationCode",
         "options": [{"name": "Authorization Code", "value": "authorizationCode"}, {"name": "PKCE", "value": "pkce"}, {"name": "Client Credentials", "value": "clientCredentials"}]},
        {"displayName": "Authorization URL", "name": "authUrl", "type": "string", "default": "", "displayOptions": {"show": {"grantType": ["authorizationCode", "pkce"]}}},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "string", "default": ""},
        {"displayName": "Client ID", "name": "clientId", "type": "string", "default": ""},
        {"displayName": "Client Secret", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Scope", "name": "scope", "type": "string", "default": ""},
        {"displayName": "Auth URI Query Parameters", "name": "authQueryParameters", "type": "string", "default": "", "displayOptions": {"show": {"grantType": ["authorizationCode", "pkce"]}}},
        {"displayName": "Authentication", "name": "authentication", "type": "options", "default": "header",
         "options": [{"name": "Send Auth Header", "value": "header"}, {"name": "Send Credentials in Body", "value": "body"}]}]},
    google("googleSheetsOAuth2Api", "Google Sheets OAuth2 API", "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file"),
    google("gmailOAuth2", "Gmail OAuth2 API", "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.modify"),
    google("googleCalendarOAuth2Api", "Google Calendar OAuth2 API", "https://www.googleapis.com/auth/calendar"),
    google("googleOAuth2Api", "Google OAuth2 API (generic)", ""),
    google("googleDriveOAuth2Api", "Google Drive OAuth2 API", "https://www.googleapis.com/auth/drive"),
    microsoft("microsoftOutlookOAuth2Api", "Microsoft Outlook OAuth2 API", "openid offline_access Mail.ReadWrite Mail.Send Calendars.ReadWrite"),
    microsoft("microsoftTeamsOAuth2Api", "Microsoft Teams OAuth2 API", "openid offline_access ChannelMessage.Send Team.ReadBasic.All"),
    {"name": "microsoftOAuth2Api", "displayName": "Microsoft OAuth2 API", "properties": [
        {"displayName": "Client ID", "name": "clientId", "type": "string", "default": ""},
        {"displayName": "Client Secret", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Scope", "name": "scope", "type": "string", "default": "openid offline_access User.Read Mail.Send"},
        {"displayName": "Authorization URL", "name": "authUrl", "type": "hidden", "default": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "hidden", "default": "https://login.microsoftonline.com/common/oauth2/v2.0/token"},
        {"displayName": "Authentication", "name": "authentication", "type": "hidden", "default": "body"}]},
]

CREDS += [
    simple("pipedriveApi", "Pipedrive API", ("apiToken", "API Token", True, "", "")),
    simple("sendGridApi", "SendGrid API", ("apiKey", "API Key", True, "", "")),
    simple("sendInBlueApi", "Brevo API", ("apiKey", "API Key", True, "", "")),
    simple("mailgunApi", "Mailgun API", ("apiDomain", "API Domain", False, "https://api.eu.mailgun.net", "https://api.mailgun.net"),
           ("emailDomain", "Email Domain", False, "", "mg.example.com"), ("apiKey", "API Key", True, "", "")),
    simple("clickUpApi", "ClickUp API", ("accessToken", "Personal Token", True, "", "pk_...")),
    simple("asanaApi", "Asana API", ("accessToken", "Personal Access Token", True, "", "")),
    simple("linearApi", "Linear API", ("apiKey", "API Key", True, "", "lin_api_...")),
    simple("mondayComApi", "Monday.com API", ("apiToken", "API Token", True, "", "")),
    simple("gitlabApi", "GitLab API", ("server", "GitLab Server", False, "https://gitlab.com", ""), ("accessToken", "Access Token", True, "", "")),
    simple("shopifyAccessTokenApi", "Shopify Access Token API", ("shopSubdomain", "Shop Subdomain", False, "", "my-shop"), ("accessToken", "Access Token", True, "", "shpat_...")),
    simple("wooCommerceApi", "WooCommerce API", ("url", "WooCommerce URL", False, "", "https://shop.example.com"), ("consumerKey", "Consumer Key", False, "", ""),
           ("consumerSecret", "Consumer Secret", True, "", "")),
    simple("freshdeskApi", "Freshdesk API", ("domain", "Domain", False, "", "company"), ("apiKey", "API Key", True, "", "")),
    simple("pushoverApi", "Pushover API", ("apiKey", "Application Token", True, "", "")),
    simple("ntfyApi", "ntfy", ("url", "Server URL", False, "https://ntfy.sh", ""), ("token", "Access Token (optional)", True, "", "")),
    simple("baserowApi", "Baserow API", ("host", "Host", False, "https://api.baserow.io", ""), ("apiToken", "Database Token", True, "", "")),
    simple("whatsAppApi", "WhatsApp API", ("accessToken", "Access Token", True, "", "")),
    simple("mailchimpApi", "Mailchimp API", ("server", "Data Center", False, "", "us21"), ("apiKey", "API Key", True, "", "")),
    simple("webflowApi", "Webflow API", ("accessToken", "Access Token", True, "", "")),
    simple("typeformApi", "Typeform API", ("accessToken", "Personal Access Token", True, "", "")),
]

# ---------------------------------------------------------------------------
# Batch 3: communication, marketing, dev-ops, AI and public APIs
# ---------------------------------------------------------------------------
T = lambda *a, **k: OP(*a, **k)

NODES += [
    node("n8n-nodes-base.postmark", "Postmark", "postmarkApi", "https://api.postmarkapp.com", ["header:X-Postmark-Server-Token:serverToken"], [
        T("email", "send", "Send", "POST", "/email", [F("fromEmail", "From", api="From", required=True), F("toEmail", "To", api="To", required=True), F("subject", "Subject", api="Subject"),
                                                      F("textBody", "Text", api="TextBody", rows=4)], extra=[F("htmlBody", "HTML", api="HtmlBody", rows=4), F("tag", "Tag", api="Tag")]),
    ], "#ffde00", "mail", "Communication", "Transactional email with Postmark"),
    node("n8n-nodes-base.resend", "Resend", "resendApi", "https://api.resend.com", ["bearer:apiKey"], [
        T("email", "send", "Send", "POST", "/emails", [F("from", "From", required=True), F("to", "To", kind="list", required=True), F("subject", "Subject", required=True), F("html", "HTML", rows=4)],
          extra=[F("text", "Text", rows=4), F("cc", "CC", kind="list"), F("bcc", "BCC", kind="list"), F("reply_to", "Reply To")]),
    ], "#8b8b8b", "mail", "Communication", "Send email with Resend"),
    node("n8n-nodes-base.vonage", "Vonage", "vonageApi", "https://rest.nexmo.com", [], [
        T("sms", "send", "Send", "POST", "/sms/json", [N("from", "From", required=True), N("to", "To", required=True), N("message", "Message", rows=3)],
          template={"api_key": "{cred.apiKey}", "api_secret": "{cred.apiSecret}", "from": "{from}", "to": "{to}", "text": "{message}"}),
    ], "#871fff", "phone", "Communication", "Send SMS with Vonage", body_type="form"),
    node("n8n-nodes-base.rocketchat", "Rocketchat", "rocketchatApi", "{cred.domain}/api/v1", ["header:X-Auth-Token:authKey", "header:X-User-Id:userId"], [
        T("chat", "postMessage", "Post Message", "POST", "/chat.postMessage", [F("channel", "Channel", required=True, placeholder="#general"), F("text", "Text", rows=3)], extra=[F("alias", "Alias"), F("emoji", "Emoji")]),
    ], "#f5455c", "chat", "Communication", "Post messages to Rocket.Chat"),
    node("n8n-nodes-base.matrix", "Matrix", "matrixApi", "{cred.homeserverUrl}/_matrix/client/v3", ["bearer:accessToken"], [
        T("message", "create", "Send Message", "PUT", "/rooms/{roomId}/send/m.room.message/{txnId}",
          [F("roomId", "Room ID", "path", required=True, placeholder="!abc:matrix.org"), F("txnId", "Transaction ID", "path", default="={{ $execution.id + '-' + $now.toMillis() + '-' + $itemIndex }}"), N("text", "Text", rows=3)],
          template={"msgtype": "m.text", "body": "{text}"}),
    ], "#0dbd8b", "chat", "Communication", "Send messages to Matrix rooms"),
    node("n8n-nodes-base.zulip", "Zulip", "zulipApi", "{cred.url}/api/v1", ["basic:email:apiKey"], [
        T("message", "sendStream", "Send to Stream", "POST", "/messages", [F("stream", "Stream", api="to", required=True), F("topic", "Topic", required=True), F("content", "Content", rows=3)], static={"type": "stream"}),
        T("message", "sendPrivate", "Send Private", "POST", "/messages", [F("to", "To (emails)", required=True), F("content", "Content", rows=3)], static={"type": "private"}),
    ], "#6492fe", "chat", "Communication", "Send Zulip messages", body_type="form"),
    node("n8n-nodes-base.gotify", "Gotify", "gotifyApi", "{cred.url}", ["query:token:appApiToken"], [
        T("message", "create", "Create", "POST", "/message", [F("message", "Message", required=True), F("title", "Title")], extra=[F("priority", "Priority", kind="number")]),
    ], "#71c3e5", "phone", "Communication", "Push notifications to Gotify"),
    node("n8n-nodes-base.mautic", "Mautic", "mauticApi", "{cred.url}/api", ["basic:username:password"], [
        T("contact", "create", "Create", "POST", "/contacts/new", [F("email", "Email", required=True)], extra=[F("firstname", "First Name"), F("lastname", "Last Name"), F("company", "Company")], root="contact"),
        T("contact", "get", "Get", "GET", "/contacts/{contactId}", [F("contactId", "Contact ID", "path", required=True)], root="contact"),
    ], "#4e5e9e", "users", "Marketing", "Contacts in Mautic"),
    node("n8n-nodes-base.activeCampaign", "ActiveCampaign", "activeCampaignApi", "{cred.apiUrl}/api/3", ["header:Api-Token:apiKey"], [
        T("contact", "create", "Create", "POST", "/contacts", [N("email", "Email", required=True), N("firstName", "First Name"), N("lastName", "Last Name"), N("phone", "Phone")],
          template={"contact": {"email": "{email}", "firstName": "{firstName}", "lastName": "{lastName}", "phone": "{phone}"}}, root="contact"),
        T("contact", "getAll", "Get Many", "GET", "/contacts", [F("email", "Email", "query")], root="contacts", limit="limit"),
    ], "#356ae6", "users", "Marketing", "Contacts in ActiveCampaign"),
    node("n8n-nodes-base.convertKit", "Kit (ConvertKit)", "convertKitApi", "https://api.convertkit.com/v3", [], [
        T("form", "addSubscriber", "Add Subscriber", "POST", "/forms/{formId}/subscribe", [F("formId", "Form ID", "path", required=True), N("email", "Email", required=True), N("firstName", "First Name")],
          template={"api_key": "{cred.apiKey}", "email": "{email}", "first_name": "{firstName}"}, root="subscription"),
    ], "#fb6970", "mail", "Marketing", "Add subscribers in Kit"),
    node("n8n-nodes-base.intercom", "Intercom", "intercomApi", "https://api.intercom.io", ["bearer:apiKey"], [
        T("contact", "create", "Create", "POST", "/contacts", [N("role", "Role", kind="options", options=["user", "lead"]), N("email", "Email", required=True), N("name", "Name")],
          template={"role": "{role}", "email": "{email}", "name": "{name}"}),
        T("contact", "get", "Get", "GET", "/contacts/{contactId}", [F("contactId", "Contact ID", "path", required=True)]),
    ], "#1f8ded", "chat", "Support", "Contacts in Intercom", headers={"Intercom-Version": "2.11"}),
    node("n8n-nodes-base.calendly", "Calendly", "calendlyApi", "https://api.calendly.com", ["bearer:accessToken"], [
        T("event", "getAll", "Get Scheduled Events", "GET", "/scheduled_events", [F("user", "User URI", "query", required=True), F("status", "Status", "query", kind="options", options=["active", "canceled"])], root="collection", limit="count"),
        T("user", "me", "Get Current User", "GET", "/users/me", [], root="resource"),
    ], "#006bff", "calendar", "Productivity", "Scheduled events in Calendly"),
    node("n8n-nodes-base.zoom", "Zoom", "zoomOAuth2Api", "https://api.zoom.us/v2", ["oauth2"], [
        T("meeting", "create", "Create", "POST", "/users/me/meetings", [N("topic", "Topic", required=True), N("startTime", "Start Time (ISO)"), N("duration", "Duration (min)", kind="number", default=30)],
          template={"topic": "{topic}", "type": 2, "start_time": "{startTime}", "duration": "{duration}"}),
        T("meeting", "getAll", "Get Many", "GET", "/users/me/meetings", [], root="meetings", limit="page_size"),
        T("meeting", "delete", "Delete", "DELETE", "/meetings/{meetingId}", [F("meetingId", "Meeting ID", "path", required=True)]),
    ], "#2d8cff", "calendar", "Communication", "Meetings in Zoom"),
    node("n8n-nodes-base.dropbox", "Dropbox", "dropboxOAuth2Api", "https://api.dropboxapi.com/2", ["oauth2"], [
        T("folder", "list", "List", "POST", "/files/list_folder", [N("path", "Folder Path", default="")], template={"path": "{path}"}, root="entries"),
        T("folder", "create", "Create", "POST", "/files/create_folder_v2", [N("path", "Folder Path", required=True)], template={"path": "{path}"}, root="metadata"),
        T("file", "delete", "Delete", "POST", "/files/delete_v2", [N("path", "Path", required=True)], template={"path": "{path}"}, root="metadata"),
    ], "#0061ff", "file", "Data & Storage", "Folders and files in Dropbox"),
    node("n8n-nodes-base.strapi", "Strapi", "strapiTokenApi", "{cred.url}/api", ["bearer:apiToken"], [
        T("entry", "getAll", "Get Many", "GET", "/{contentType}", [F("contentType", "Content Type (plural)", "path", required=True, placeholder="articles")], root="data"),
        T("entry", "get", "Get", "GET", "/{contentType}/{entryId}", [F("contentType", "Content Type (plural)", "path", required=True), F("entryId", "Entry ID", "path", required=True)], root="data"),
        T("entry", "create", "Create", "POST", "/{contentType}", [F("contentType", "Content Type (plural)", "path", required=True), N("data", "Fields (JSON)", kind="json", rows=6)], template={"data": "{data}"}, root="data"),
        T("entry", "delete", "Delete", "DELETE", "/{contentType}/{entryId}", [F("contentType", "Content Type (plural)", "path", required=True), F("entryId", "Entry ID", "path", required=True)]),
    ], "#4945ff", "layers", "Marketing", "Entries in Strapi"),
    node("n8n-nodes-base.wordpress", "WordPress", "wordpressApi", "{cred.url}/wp-json/wp/v2", ["basic:username:password"], [
        T("post", "create", "Create", "POST", "/posts", [F("title", "Title", required=True), F("content", "Content", rows=6)], extra=[F("status", "Status", kind="options", options=["draft", "publish", "private"]), F("excerpt", "Excerpt"), F("slug", "Slug")]),
        T("post", "get", "Get", "GET", "/posts/{postId}", [F("postId", "Post ID", "path", required=True)]),
        T("post", "getAll", "Get Many", "GET", "/posts", [F("search", "Search", "query")], limit="per_page"),
        T("post", "update", "Update", "POST", "/posts/{postId}", [F("postId", "Post ID", "path", required=True)], extra=[F("title", "Title"), F("content", "Content", rows=6), F("status", "Status")]),
        T("user", "getAll", "Get Many", "GET", "/users", [], limit="per_page"),
    ], "#21759b", "layers", "Marketing", "Posts and users in WordPress"),
    node("n8n-nodes-base.ghost", "Ghost", "ghostContentApi", "{cred.url}/ghost/api/content", ["query:key:apiKey"], [
        T("post", "getAll", "Get Many", "GET", "/posts/", [F("filter", "Filter", "query")], root="posts", limit="limit"),
        T("post", "get", "Get", "GET", "/posts/slug/{slug}/", [F("slug", "Slug", "path", required=True)], root="posts"),
    ], "#8b8b8b", "layers", "Marketing", "Read posts from Ghost (Content API)"),
    node("n8n-nodes-base.contentful", "Contentful", "contentfulApi", "https://cdn.contentful.com/spaces/{cred.spaceId}/environments/master", ["bearer:contentDeliveryAccessToken"], [
        T("entry", "getAll", "Get Many", "GET", "/entries", [F("content_type", "Content Type", "query")], root="items", limit="limit"),
        T("entry", "get", "Get", "GET", "/entries/{entryId}", [F("entryId", "Entry ID", "path", required=True)]),
    ], "#2478cc", "layers", "Marketing", "Entries from Contentful"),
    node("n8n-nodes-base.sentryIo", "Sentry.io", "sentryIoApi", "{cred.url}/api/0", ["bearer:token"], [
        T("issue", "getAll", "Get Many", "GET", "/projects/{organizationSlug}/{projectSlug}/issues/", [F("organizationSlug", "Organization", "path", required=True), F("projectSlug", "Project", "path", required=True), F("query", "Query", "query", default="is:unresolved")]),
        T("issue", "update", "Update", "PUT", "/issues/{issueId}/", [F("issueId", "Issue ID", "path", required=True)], extra=[F("status", "Status", kind="options", options=["resolved", "unresolved", "ignored"])]),
    ], "#a960ee", "alert", "Developer", "Issues in Sentry"),
    node("n8n-nodes-base.pagerDuty", "PagerDuty", None, "https://events.pagerduty.com/v2", [], [
        T("event", "trigger", "Trigger Incident", "POST", "/enqueue", [N("routingKey", "Integration Key", required=True), N("summary", "Summary", required=True), N("source", "Source", default="flowwolf"),
                                                                      N("severity", "Severity", kind="options", options=["critical", "error", "warning", "info"]), N("dedupKey", "Dedup Key")],
          template={"routing_key": "{routingKey}", "event_action": "trigger", "dedup_key": "{dedupKey}", "payload": {"summary": "{summary}", "source": "{source}", "severity": "{severity}"}}),
        T("event", "resolve", "Resolve Incident", "POST", "/enqueue", [N("routingKey", "Integration Key", required=True), N("dedupKey", "Dedup Key", required=True)],
          template={"routing_key": "{routingKey}", "event_action": "resolve", "dedup_key": "{dedupKey}"}),
    ], "#06ac38", "alert", "Developer", "Trigger and resolve PagerDuty incidents"),
    node("n8n-nodes-base.datadog", "Datadog", "datadogApi", "{cred.url}/api/v1", ["header:DD-API-KEY:apiKey"], [
        T("event", "create", "Create Event", "POST", "/events", [F("title", "Title", required=True), F("text", "Text", rows=3)], extra=[F("alert_type", "Alert Type", kind="options", options=["info", "warning", "error", "success"]), F("tags", "Tags", kind="list")]),
    ], "#632ca6", "alert", "Developer", "Post events to Datadog"),
    node("n8n-nodes-base.cloudflare", "Cloudflare", "cloudflareApi", "https://api.cloudflare.com/client/v4", ["bearer:apiToken"], [
        T("dnsRecord", "getAll", "Get Many", "GET", "/zones/{zoneId}/dns_records", [F("zoneId", "Zone ID", "path", required=True), F("name", "Name", "query")], root="result", limit="per_page"),
        T("dnsRecord", "create", "Create", "POST", "/zones/{zoneId}/dns_records", [F("zoneId", "Zone ID", "path", required=True), F("type", "Type", kind="options", options=["A", "AAAA", "CNAME", "TXT", "MX"]),
                                                                                 F("name", "Name", required=True), F("content", "Content", required=True), F("ttl", "TTL", kind="number", default=1)], extra=[F("proxied", "Proxied", kind="boolean")], root="result"),
        T("dnsRecord", "delete", "Delete", "DELETE", "/zones/{zoneId}/dns_records/{recordId}", [F("zoneId", "Zone ID", "path", required=True), F("recordId", "Record ID", "path", required=True)], root="result"),
        T("cache", "purge", "Purge Everything", "POST", "/zones/{zoneId}/purge_cache", [F("zoneId", "Zone ID", "path", required=True)], static={"purge_everything": True}),
    ], "#f38020", "globe", "Developer", "DNS records and cache on Cloudflare"),
    node("n8n-nodes-base.hetzner", "Hetzner Cloud", "hetznerCloudApi", "https://api.hetzner.cloud/v1", ["bearer:apiToken"], [
        T("server", "getAll", "Get Many", "GET", "/servers", [], root="servers"),
        T("server", "get", "Get", "GET", "/servers/{serverId}", [F("serverId", "Server ID", "path", required=True)], root="server"),
        T("server", "powerOn", "Power On", "POST", "/servers/{serverId}/actions/poweron", [F("serverId", "Server ID", "path", required=True)], root="action"),
        T("server", "reboot", "Reboot", "POST", "/servers/{serverId}/actions/reboot", [F("serverId", "Server ID", "path", required=True)], root="action"),
        T("server", "shutdown", "Shutdown", "POST", "/servers/{serverId}/actions/shutdown", [F("serverId", "Server ID", "path", required=True)], root="action"),
    ], "#d50c2d", "database", "Developer", "Servers in Hetzner Cloud"),
    node("n8n-nodes-base.digitalOcean", "DigitalOcean", "digitalOceanApi", "https://api.digitalocean.com/v2", ["bearer:accessToken"], [
        T("droplet", "getAll", "Get Many", "GET", "/droplets", [], root="droplets", limit="per_page"),
        T("droplet", "reboot", "Reboot", "POST", "/droplets/{dropletId}/actions", [F("dropletId", "Droplet ID", "path", required=True)], static={"type": "reboot"}, root="action"),
    ], "#0080ff", "database", "Developer", "Droplets in DigitalOcean"),
    node("n8n-nodes-base.netlify", "Netlify", "netlifyApi", "https://api.netlify.com/api/v1", ["bearer:accessToken"], [
        T("site", "getAll", "Get Many", "GET", "/sites", []),
        T("deploy", "create", "Trigger Build", "POST", "/sites/{siteId}/builds", [F("siteId", "Site ID", "path", required=True)]),
        T("deploy", "getAll", "Get Many", "GET", "/sites/{siteId}/deploys", [F("siteId", "Site ID", "path", required=True)], limit="per_page"),
    ], "#00ad9f", "globe", "Developer", "Sites and deploys on Netlify"),
    node("n8n-nodes-base.vercel", "Vercel", "vercelApi", "https://api.vercel.com", ["bearer:accessToken"], [
        T("deployment", "getAll", "Get Many", "GET", "/v6/deployments", [F("projectId", "Project ID", "query")], root="deployments", limit="limit"),
        T("project", "getAll", "Get Many", "GET", "/v9/projects", [], root="projects", limit="limit"),
    ], "#8b8b8b", "globe", "Developer", "Deployments and projects on Vercel"),
    node("n8n-nodes-base.jenkins", "Jenkins", "jenkinsApi", "{cred.baseUrl}", ["basic:username:apiKey"], [
        T("job", "trigger", "Trigger", "POST", "/job/{job}/build", [F("job", "Job Name", "path", required=True)]),
        T("job", "triggerParams", "Trigger with Parameters", "POST", "/job/{job}/buildWithParameters", [F("job", "Job Name", "path", required=True)], extra=[F("token", "Build Token")]),
        T("build", "getAll", "Get Last Build", "GET", "/job/{job}/lastBuild/api/json", [F("job", "Job Name", "path", required=True)]),
    ], "#d24939", "terminal", "Developer", "Trigger Jenkins jobs", body_type="form"),
    node("n8n-nodes-base.homeAssistant", "Home Assistant", "homeAssistantApi", "{cred.url}/api", ["bearer:accessToken"], [
        T("state", "get", "Get State", "GET", "/states/{entityId}", [F("entityId", "Entity ID", "path", required=True, placeholder="light.kitchen")]),
        T("state", "getAll", "Get All States", "GET", "/states", []),
        T("service", "call", "Call Service", "POST", "/services/{domain}/{service}", [F("domain", "Domain", "path", required=True, placeholder="light"), F("service", "Service", "path", required=True, placeholder="turn_on"),
                                                                                   N("entityId", "Entity ID"), N("serviceAttributes", "Service Data (JSON)", kind="json", default="")],
          template={"entity_id": "{entityId}"}, merge_json="serviceAttributes"),
    ], "#18bcf2", "sun", "Utility", "States and services in Home Assistant"),
    node("n8n-nodes-base.googleGemini", "Google Gemini", "googlePalmApi", "{cred.host}/v1beta", ["header:x-goog-api-key:apiKey"], [
        T("text", "message", "Message a Model", "POST", "/models/{model}:generateContent", [F("model", "Model", "path", default="gemini-2.5-flash", required=True), N("prompt", "Prompt", rows=4, required=True),
                                                                                         N("system", "System Instruction", rows=2)],
          template={"contents": [{"role": "user", "parts": [{"text": "{prompt}"}]}], "systemInstruction": {"parts": [{"text": "{system}"}]}}),
    ], "#4285f4", "sparkle", "AI", "Message Google Gemini models"),
    node("n8n-nodes-base.deepL", "DeepL", "deepLApi", "{cred.url}/v2", [], [
        T("language", "translate", "Translate", "POST", "/translate", [N("text", "Text", rows=3, required=True), N("translateTo", "Target Language", default="DE"), N("sourceLang", "Source Language")],
          template={"text": ["{text}"], "target_lang": "{translateTo}", "source_lang": "{sourceLang}"}, root="translations"),
    ], "#0f6fb5", "globe", "AI", "Translate text with DeepL", headers={"Authorization": "DeepL-Auth-Key {cred.apiKey}"}),
    node("n8n-nodes-base.huggingFace", "Hugging Face", "huggingFaceApi", "https://router.huggingface.co/hf-inference/models", ["bearer:apiKey"], [
        T("model", "inference", "Run Inference", "POST", "/{+model}", [F("model", "Model", "path", required=True, placeholder="facebook/bart-large-cnn"), N("inputs", "Inputs", rows=3, required=True)],
          template={"inputs": "{inputs}"}),
    ], "#ff9d00", "sparkle", "AI", "Run Hugging Face inference models"),
    node("n8n-nodes-base.bitly", "Bitly", "bitlyApi", "https://api-ssl.bitly.com/v4", ["bearer:accessToken"], [
        T("link", "create", "Create", "POST", "/shorten", [F("longUrl", "Long URL", api="long_url", required=True)], extra=[F("domain", "Domain"), F("group_guid", "Group")]),
        T("link", "get", "Get", "GET", "/bitlinks/{id}", [F("id", "Bitlink", "path", required=True, placeholder="bit.ly/abc")]),
    ], "#ee6123", "globe", "Marketing", "Shorten links with Bitly"),
    node("n8n-nodes-base.coinGecko", "CoinGecko", None, "https://api.coingecko.com/api/v3", [], [
        T("coin", "price", "Price", "GET", "/simple/price", [F("ids", "Coin IDs", "query", required=True, placeholder="bitcoin,ethereum"), F("vs_currencies", "Currencies", "query", default="eur,usd")]),
        T("coin", "market", "Markets", "GET", "/coins/markets", [F("vs_currency", "Currency", "query", default="eur")], limit="per_page"),
    ], "#8dc63f", "card", "Utility", "Crypto prices from CoinGecko"),
    node("n8n-nodes-base.hackerNews", "Hacker News", None, "https://hacker-news.firebaseio.com/v0", [], [
        T("article", "get", "Get", "GET", "/item/{articleId}.json", [F("articleId", "Item ID", "path", required=True)]),
        T("all", "getAll", "Top Stories (IDs)", "GET", "/topstories.json", []),
        T("user", "get", "Get User", "GET", "/user/{username}.json", [F("username", "Username", "path", required=True)]),
    ], "#ff6600", "layers", "Utility", "Stories and users from Hacker News"),
]

CREDS += [
    simple("postmarkApi", "Postmark API", ("serverToken", "Server API Token", True, "", "")),
    simple("resendApi", "Resend API", ("apiKey", "API Key", True, "", "re_...")),
    simple("vonageApi", "Vonage API", ("apiKey", "API Key", False, "", ""), ("apiSecret", "API Secret", True, "", "")),
    simple("rocketchatApi", "Rocket.Chat API", ("domain", "Domain", False, "", "https://chat.example.com"), ("userId", "User ID", False, "", ""), ("authKey", "Auth Key", True, "", "")),
    simple("matrixApi", "Matrix API", ("homeserverUrl", "Homeserver URL", False, "https://matrix-client.matrix.org", ""), ("accessToken", "Access Token", True, "", "")),
    simple("zulipApi", "Zulip API", ("url", "URL", False, "", "https://org.zulipchat.com"), ("email", "Bot Email", False, "", ""), ("apiKey", "API Key", True, "", "")),
    simple("gotifyApi", "Gotify API", ("url", "URL", False, "", "https://gotify.example.com"), ("appApiToken", "App Token", True, "", "")),
    simple("mauticApi", "Mautic API", ("url", "URL", False, "", "https://mautic.example.com"), ("username", "Username", False, "", ""), ("password", "Password", True, "", "")),
    simple("activeCampaignApi", "ActiveCampaign API", ("apiUrl", "API URL", False, "", "https://account.api-us1.com"), ("apiKey", "API Key", True, "", "")),
    simple("convertKitApi", "Kit API", ("apiKey", "API Key", True, "", "")),
    simple("intercomApi", "Intercom API", ("apiKey", "Access Token", True, "", "")),
    simple("calendlyApi", "Calendly API", ("accessToken", "Personal Access Token", True, "", "")),
    simple("strapiTokenApi", "Strapi API Token", ("url", "URL", False, "", "https://cms.example.com"), ("apiToken", "API Token", True, "", "")),
    simple("wordpressApi", "WordPress API", ("url", "WordPress URL", False, "", "https://blog.example.com"), ("username", "Username", False, "", ""),
           ("password", "Application Password", True, "", "")),
    simple("ghostContentApi", "Ghost Content API", ("url", "URL", False, "", "https://blog.example.com"), ("apiKey", "Content API Key", True, "", "")),
    simple("contentfulApi", "Contentful API", ("spaceId", "Space ID", False, "", ""), ("contentDeliveryAccessToken", "Content Delivery Token", True, "", "")),
    simple("sentryIoApi", "Sentry API", ("url", "URL", False, "https://sentry.io", ""), ("token", "Auth Token", True, "", "")),
    simple("datadogApi", "Datadog API", ("url", "API URL", False, "https://api.datadoghq.eu", ""), ("apiKey", "API Key", True, "", "")),
    simple("cloudflareApi", "Cloudflare API", ("apiToken", "API Token", True, "", "")),
    simple("hetznerCloudApi", "Hetzner Cloud API", ("apiToken", "API Token", True, "", "")),
    simple("digitalOceanApi", "DigitalOcean API", ("accessToken", "Access Token", True, "", "")),
    simple("netlifyApi", "Netlify API", ("accessToken", "Access Token", True, "", "")),
    simple("vercelApi", "Vercel API", ("accessToken", "Access Token", True, "", "")),
    simple("jenkinsApi", "Jenkins API", ("baseUrl", "Jenkins URL", False, "", "https://ci.example.com"), ("username", "Username", False, "", ""), ("apiKey", "API Token", True, "", "")),
    simple("homeAssistantApi", "Home Assistant API", ("url", "URL", False, "", "http://homeassistant.local:8123"), ("accessToken", "Long-Lived Access Token", True, "", "")),
    simple("googlePalmApi", "Google Gemini API", ("host", "Host", False, "https://generativelanguage.googleapis.com", ""), ("apiKey", "API Key", True, "", "")),
    simple("deepLApi", "DeepL API", ("url", "API URL", False, "https://api-free.deepl.com", "https://api.deepl.com for Pro"), ("apiKey", "Authentication Key", True, "", "")),
    simple("huggingFaceApi", "Hugging Face API", ("apiKey", "Access Token", True, "", "hf_...")),
    simple("bitlyApi", "Bitly API", ("accessToken", "Access Token", True, "", "")),
    {"name": "zoomOAuth2Api", "displayName": "Zoom OAuth2 API", "properties": [
        {"displayName": "Client ID", "name": "clientId", "type": "string", "default": ""},
        {"displayName": "Client Secret", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Authorization URL", "name": "authUrl", "type": "hidden", "default": "https://zoom.us/oauth/authorize"},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "hidden", "default": "https://zoom.us/oauth/token"},
        {"displayName": "Authentication", "name": "authentication", "type": "hidden", "default": "header"}]},
    {"name": "dropboxOAuth2Api", "displayName": "Dropbox OAuth2 API", "properties": [
        {"displayName": "Client ID", "name": "clientId", "type": "string", "default": ""},
        {"displayName": "Client Secret", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Authorization URL", "name": "authUrl", "type": "hidden", "default": "https://www.dropbox.com/oauth2/authorize"},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "hidden", "default": "https://api.dropboxapi.com/oauth2/token"},
        {"displayName": "Auth URI Query Parameters", "name": "authQueryParameters", "type": "hidden", "default": "token_access_type=offline"},
        {"displayName": "Authentication", "name": "authentication", "type": "hidden", "default": "header"}]},
]

# ---------------------------------------------------------------------------
# Batch 4: accounting (DE), AI providers, Google/Microsoft, time tracking, search, social
# ---------------------------------------------------------------------------
def chat_ai(type_name, display, cred, base, color, desc, model, extra_auth=None):
    """OpenAI-style chat completion nodes (Mistral, Groq, Perplexity)."""
    return node(type_name, display, cred, base, extra_auth or ["bearer:apiKey"], [
        T("chat", "complete", "Message a Model", "POST", "/chat/completions",
          [N("model", "Model", default=model, required=True), N("system", "System Message", rows=2), N("prompt", "Prompt", rows=4, required=True),
           N("temperature", "Temperature", kind="number", default=0.7)],
          template={"model": "{model}", "temperature": "{temperature}", "messages": [{"role": "system", "content": "{system}"}, {"role": "user", "content": "{prompt}"}]},
          root="choices"),
    ], color, "sparkle", "AI", desc)


NODES += [
    node("n8n-nodes-base.sevDesk", "sevDesk", "sevDeskApi", "https://my.sevdesk.de/api/v1", ["header:Authorization:apiToken"], [
        T("contact", "getAll", "Get Many", "GET", "/Contact", [F("name", "Name", "query"), F("depth", "Include Persons", "query", kind="options", options=[("Organisations only", "0"), ("All", "1")])], root="objects", limit="limit"),
        T("contact", "get", "Get", "GET", "/Contact/{contactId}", [F("contactId", "Contact ID", "path", required=True)], root="objects"),
        T("invoice", "getAll", "Get Many", "GET", "/Invoice", [F("status", "Status", "query", kind="options", options=[("Any", ""), ("Draft", "100"), ("Open", "200"), ("Paid", "1000")])], root="objects", limit="limit"),
        T("invoice", "get", "Get", "GET", "/Invoice/{invoiceId}", [F("invoiceId", "Invoice ID", "path", required=True)], root="objects"),
        T("voucher", "getAll", "Get Many", "GET", "/Voucher", [], root="objects", limit="limit"),
    ], "#e11d48", "card", "Finance", "Contacts, invoices and vouchers in sevDesk"),
    node("n8n-nodes-base.lexoffice", "lexoffice", "lexofficeApi", "https://api.lexoffice.io/v1", ["bearer:apiKey"], [
        T("contact", "getAll", "Get Many", "GET", "/contacts", [F("name", "Name", "query"), F("email", "Email", "query")], root="content", limit="size"),
        T("contact", "create", "Create Person", "POST", "/contacts", [N("firstName", "First Name"), N("lastName", "Last Name", required=True), N("email", "Email")],
          template={"version": 0, "roles": {"customer": {}}, "person": {"firstName": "{firstName}", "lastName": "{lastName}"}, "emailAddresses": {"business": ["{email}"]}}),
        T("invoice", "get", "Get", "GET", "/invoices/{invoiceId}", [F("invoiceId", "Invoice ID", "path", required=True)]),
        T("voucherlist", "getAll", "Get Voucher List", "GET", "/voucherlist", [F("voucherType", "Type", "query", default="invoice"), F("voucherStatus", "Status", "query", default="open,paid")], root="content", limit="size"),
    ], "#00a3e0", "card", "Finance", "Contacts and vouchers in lexoffice"),
    node("n8n-nodes-base.mailerLite", "MailerLite", "mailerLiteApi", "https://connect.mailerlite.com/api", ["bearer:apiKey"], [
        T("subscriber", "create", "Create or Update", "POST", "/subscribers", [F("email", "Email", required=True)], extra=[F("fields", "Fields (JSON)", kind="json"), F("groups", "Group IDs", kind="list")], root="data"),
        T("subscriber", "get", "Get", "GET", "/subscribers/{subscriberId}", [F("subscriberId", "Subscriber ID or Email", "path", required=True)], root="data"),
        T("subscriber", "getAll", "Get Many", "GET", "/subscribers", [], root="data", limit="limit"),
    ], "#09c269", "mail", "Marketing", "Subscribers in MailerLite"),
    chat_ai("@flowwolf/nodes.mistralAi", "Mistral AI", "mistralCloudApi", "https://api.mistral.ai/v1", "#fa520f", "Chat with Mistral models", "mistral-small-latest"),
    chat_ai("@flowwolf/nodes.groq", "Groq", "groqApi", "https://api.groq.com/openai/v1", "#f55036", "Very fast inference with Groq", "llama-3.3-70b-versatile"),
    chat_ai("n8n-nodes-base.perplexity", "Perplexity", "perplexityApi", "https://api.perplexity.ai", "#20808d", "Answers with web search from Perplexity", "sonar"),
    node("@flowwolf/nodes.ollama", "Ollama", "ollamaApi", "{cred.baseUrl}/api", [], [
        T("chat", "complete", "Message a Model", "POST", "/chat", [N("model", "Model", default="llama3.2", required=True), N("system", "System Message", rows=2), N("prompt", "Prompt", rows=4, required=True)],
          template={"model": "{model}", "stream": False, "messages": [{"role": "system", "content": "{system}"}, {"role": "user", "content": "{prompt}"}]}),
        T("model", "list", "List Models", "GET", "/tags", [], root="models"),
    ], "#8b8b8b", "sparkle", "AI", "Run local models with Ollama"),
    node("n8n-nodes-base.googleTranslate", "Google Translate", "googleTranslateApi", "https://translation.googleapis.com/language/translate/v2", ["query:key:apiKey"], [
        T("language", "translate", "Translate", "POST", "", [N("text", "Text", rows=3, required=True), N("translateTo", "Translate To", default="de", required=True)],
          template={"q": "{text}", "target": "{translateTo}", "format": "text"}, root="data.translations"),
    ], "#4285f4", "globe", "AI", "Translate text with Google Translate"),
    node("n8n-nodes-base.googleTasks", "Google Tasks", "googleTasksOAuth2Api", "https://tasks.googleapis.com/tasks/v1", ["oauth2"], [
        T("task", "create", "Create", "POST", "/lists/{task}/tasks", [F("task", "Task List ID", "path", default="@default", required=True), N("title", "Title", required=True), N("notes", "Notes"), N("due", "Due (RFC 3339)")],
          template={"title": "{title}", "notes": "{notes}", "due": "{due}"}),
        T("task", "getAll", "Get Many", "GET", "/lists/{task}/tasks", [F("task", "Task List ID", "path", default="@default", required=True), F("showCompleted", "Show Completed", "query", kind="options", options=["true", "false"])], root="items", limit="maxResults"),
        T("task", "delete", "Delete", "DELETE", "/lists/{task}/tasks/{taskId}", [F("task", "Task List ID", "path", default="@default", required=True), F("taskId", "Task ID", "path", required=True)]),
    ], "#4285f4", "check", "Productivity", "Tasks in Google Tasks"),
    node("n8n-nodes-base.googleDocs", "Google Docs", "googleDocsOAuth2Api", "https://docs.googleapis.com/v1", ["oauth2"], [
        T("document", "create", "Create", "POST", "/documents", [N("title", "Title", required=True)], template={"title": "{title}"}),
        T("document", "get", "Get", "GET", "/documents/{documentId}", [F("documentId", "Document ID", "path", required=True)]),
        T("document", "update", "Append Text", "POST", "/documents/{documentId}:batchUpdate", [F("documentId", "Document ID", "path", required=True), N("text", "Text", rows=4, required=True)],
          template={"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": "{text}"}}]}),
    ], "#4285f4", "file", "Productivity", "Create and edit Google Docs"),
    node("n8n-nodes-base.googleBigQuery", "Google BigQuery", "googleBigQueryOAuth2Api", "https://bigquery.googleapis.com/bigquery/v2", ["oauth2"], [
        T("database", "executeQuery", "Execute Query", "POST", "/projects/{projectId}/queries", [F("projectId", "Project ID", "path", required=True), N("sqlQuery", "SQL", rows=5, required=True)],
          template={"query": "{sqlQuery}", "useLegacySql": False}, transform="bigqueryRows"),
    ], "#4285f4", "database", "Data & Storage", "Run SQL in BigQuery"),
    node("n8n-nodes-base.youTube", "YouTube", "youTubeApiKey", "https://www.googleapis.com/youtube/v3", ["query:key:apiKey"], [
        T("video", "search", "Search", "GET", "/search", [F("q", "Query", "query", required=True), F("type", "Type", "query", default="video")], static_query={"part": "snippet"}, root="items", limit="maxResults"),
        T("channel", "get", "Get Channel", "GET", "/channels", [F("id", "Channel ID", "query", required=True)], static_query={"part": "snippet,statistics"}, root="items"),
    ], "#ff0000", "play", "Marketing", "Search videos and read channels on YouTube"),
    node("n8n-nodes-base.microsoftToDo", "Microsoft To Do", "microsoftToDoOAuth2Api", "https://graph.microsoft.com/v1.0/me/todo", ["oauth2"], [
        T("list", "getAll", "Get Lists", "GET", "/lists", [], root="value"),
        T("task", "create", "Create", "POST", "/lists/{taskListId}/tasks", [F("taskListId", "List ID", "path", required=True), N("title", "Title", required=True), N("importance", "Importance", kind="options", options=["normal", "low", "high"])],
          template={"title": "{title}", "importance": "{importance}"}),
        T("task", "getAll", "Get Many", "GET", "/lists/{taskListId}/tasks", [F("taskListId", "List ID", "path", required=True)], root="value", limit="$top"),
    ], "#2564cf", "check", "Productivity", "Tasks in Microsoft To Do"),
    node("n8n-nodes-base.microsoftOneDrive", "Microsoft OneDrive", "microsoftOneDriveOAuth2Api", "https://graph.microsoft.com/v1.0/me/drive", ["oauth2"], [
        T("folder", "getChildren", "Get Items in a Folder", "GET", "/root:/{+folderPath}:/children", [F("folderPath", "Folder Path", "path", default="Documents", required=True)], root="value"),
        T("file", "search", "Search", "GET", "/root/search(q='{query}')", [F("query", "Query", "path", required=True)], root="value"),
        T("folder", "create", "Create Folder", "POST", "/root/children", [N("name", "Name", required=True)], template={"name": "{name}", "folder": {}, "@microsoft.graph.conflictBehavior": "rename"}),
    ], "#0078d4", "file", "Data & Storage", "Files and folders in OneDrive"),
    node("n8n-nodes-base.clockify", "Clockify", "clockifyApi", "https://api.clockify.me/api/v1", ["header:X-Api-Key:apiKey"], [
        T("timeEntry", "create", "Create", "POST", "/workspaces/{workspaceId}/time-entries", [F("workspaceId", "Workspace ID", "path", required=True), F("start", "Start (ISO)", required=True), F("end", "End (ISO)")],
          extra=[F("description", "Description"), F("projectId", "Project ID"), F("billable", "Billable", kind="boolean")]),
        T("project", "getAll", "Get Many", "GET", "/workspaces/{workspaceId}/projects", [F("workspaceId", "Workspace ID", "path", required=True)], limit="page-size"),
        T("workspace", "getAll", "Get Many", "GET", "/workspaces", []),
    ], "#03a9f4", "clock", "Productivity", "Time entries in Clockify"),
    node("n8n-nodes-base.togglTrack", "Toggl Track", "togglApi", "https://api.track.toggl.com/api/v9", ["basic:apiToken:=api_token"], [
        T("timeEntry", "getAll", "Get Many", "GET", "/me/time_entries", [F("start_date", "Start Date", "query"), F("end_date", "End Date", "query")]),
        T("timeEntry", "create", "Create", "POST", "/workspaces/{workspaceId}/time_entries", [F("workspaceId", "Workspace ID", "path", required=True), N("description", "Description"), N("start", "Start (ISO)", required=True),
                                                                                         N("duration", "Duration (s)", kind="number", default=3600)],
          template={"description": "{description}", "start": "{start}", "duration": "{duration}", "created_with": "flowwolf", "workspace_id": "{workspaceId}"}),
    ], "#e57cd8", "clock", "Productivity", "Time entries in Toggl Track"),
    node("n8n-nodes-base.harvest", "Harvest", "harvestApi", "https://api.harvestapp.com/v2", ["bearer:accessToken", "header:Harvest-Account-Id:accountId"], [
        T("timeEntry", "getAll", "Get Many", "GET", "/time_entries", [F("from", "From", "query"), F("to", "To", "query")], root="time_entries", limit="per_page"),
        T("timeEntry", "create", "Create", "POST", "/time_entries", [F("project_id", "Project ID", kind="number", required=True), F("task_id", "Task ID", kind="number", required=True),
                                                                   F("spent_date", "Date", required=True), F("hours", "Hours", kind="number")], extra=[F("notes", "Notes")]),
        T("project", "getAll", "Get Many", "GET", "/projects", [], root="projects", limit="per_page"),
    ], "#fa5d00", "clock", "Productivity", "Time tracking in Harvest"),
    node("n8n-nodes-base.elasticsearch", "Elasticsearch", "elasticsearchApi", "{cred.baseUrl}", ["basic:username:password"], [
        T("document", "index", "Create", "POST", "/{indexId}/_doc", [F("indexId", "Index", "path", required=True), F("data", "Document (JSON)", "none", kind="json", rows=6)], body_from="jsonParam"),
        T("document", "get", "Get", "GET", "/{indexId}/_doc/{documentId}", [F("indexId", "Index", "path", required=True), F("documentId", "Document ID", "path", required=True)]),
        T("document", "search", "Search", "POST", "/{indexId}/_search", [F("indexId", "Index", "path", required=True), F("data", "Query (JSON)", "none", kind="json", rows=6, default='{"query": {"match_all": {}}}')],
          body_from="jsonParam", root="hits.hits"),
        T("document", "delete", "Delete", "DELETE", "/{indexId}/_doc/{documentId}", [F("indexId", "Index", "path", required=True), F("documentId", "Document ID", "path", required=True)]),
    ], "#00bfb3", "search", "Data & Storage", "Index and search documents in Elasticsearch"),
    node("n8n-nodes-base.googleFirebaseRealtimeDatabase", "Firebase Realtime Database", "firebaseDatabaseApi", "{cred.databaseUrl}", ["query:auth:secret"], [
        T("data", "get", "Get", "GET", "/{+path}.json", [F("path", "Path", "path", required=True, placeholder="users/42")]),
        T("data", "update", "Update", "PATCH", "/{+path}.json", [F("path", "Path", "path", required=True), F("data", "Data (JSON)", "none", kind="json", rows=5)], body_from="jsonParam"),
        T("data", "push", "Push", "POST", "/{+path}.json", [F("path", "Path", "path", required=True), F("data", "Data (JSON)", "none", kind="json", rows=5)], body_from="jsonParam"),
        T("data", "delete", "Delete", "DELETE", "/{+path}.json", [F("path", "Path", "path", required=True)]),
    ], "#ffca28", "database", "Data & Storage", "Read and write Firebase Realtime Database"),
    node("n8n-nodes-base.mastodon", "Mastodon", "mastodonApi", "{cred.url}/api/v1", ["bearer:accessToken"], [
        T("status", "create", "Post", "POST", "/statuses", [F("status", "Text", rows=4, required=True)], extra=[F("visibility", "Visibility", kind="options", options=["public", "unlisted", "private", "direct"]), F("spoiler_text", "Content Warning")]),
        T("status", "getAll", "Home Timeline", "GET", "/timelines/home", [], limit="limit"),
    ], "#6364ff", "chat", "Marketing", "Post to Mastodon"),
    node("n8n-nodes-base.discourse", "Discourse", "discourseApi", "{cred.url}", ["header:Api-Key:apiKey", "header:Api-Username:username"], [
        T("post", "create", "Create Topic", "POST", "/posts.json", [F("title", "Title", required=True), F("content", "Content", api="raw", rows=5, required=True)], extra=[F("category", "Category ID", kind="number")]),
        T("post", "getAll", "Latest Topics", "GET", "/latest.json", [], root="topic_list.topics"),
    ], "#2c9eda", "chat", "Communication", "Topics in Discourse"),
    node("n8n-nodes-base.bitbucket", "Bitbucket", "bitbucketApi", "https://api.bitbucket.org/2.0", ["basic:username:appPassword"], [
        T("repository", "getAll", "Get Many", "GET", "/repositories/{workspace}", [F("workspace", "Workspace", "path", required=True)], root="values", limit="pagelen"),
        T("pullRequest", "getAll", "Get Pull Requests", "GET", "/repositories/{workspace}/{repository}/pullrequests", [F("workspace", "Workspace", "path", required=True), F("repository", "Repository", "path", required=True)], root="values"),
        T("issue", "create", "Create Issue", "POST", "/repositories/{workspace}/{repository}/issues", [F("workspace", "Workspace", "path", required=True), F("repository", "Repository", "path", required=True), N("title", "Title", required=True), N("content", "Content", rows=4)],
          template={"title": "{title}", "content": {"raw": "{content}"}}),
    ], "#2684ff", "github", "Developer", "Repositories and pull requests on Bitbucket"),
    node("n8n-nodes-base.zammad", "Zammad", "zammadTokenAuthApi", "{cred.baseUrl}/api/v1", ["header:Authorization:authHeader"], [
        T("ticket", "create", "Create", "POST", "/tickets", [N("title", "Title", required=True), N("group", "Group", default="Users", required=True), N("customer", "Customer Email", required=True), N("body", "Body", rows=4)],
          template={"title": "{title}", "group": "{group}", "customer": "{customer}", "article": {"subject": "{title}", "body": "{body}", "type": "note", "internal": False}}),
        T("ticket", "get", "Get", "GET", "/tickets/{id}", [F("id", "Ticket ID", "path", required=True)]),
        T("ticket", "getAll", "Get Many", "GET", "/tickets", [], limit="per_page"),
    ], "#ffd500", "chat", "Support", "Tickets in Zammad"),
    node("n8n-nodes-base.raindrop", "Raindrop", "raindropOAuth2Api", "https://api.raindrop.io/rest/v1", ["bearer:accessToken"], [
        T("bookmark", "create", "Create", "POST", "/raindrop", [F("link", "Link", required=True)], extra=[F("title", "Title"), F("tags", "Tags", kind="list")], root="item"),
        T("bookmark", "getAll", "Get Many", "GET", "/raindrops/{collectionId}", [F("collectionId", "Collection ID", "path", default="0", required=True)], root="items", limit="perpage"),
    ], "#2f80ed", "tag", "Productivity", "Bookmarks in Raindrop.io"),
    node("n8n-nodes-base.plausible", "Plausible", "plausibleApi", "{cred.url}/api/v1", ["bearer:apiKey"], [
        T("stats", "aggregate", "Aggregate", "GET", "/stats/aggregate", [F("site_id", "Site", "query", required=True), F("period", "Period", "query", default="30d"), F("metrics", "Metrics", "query", default="visitors,pageviews,bounce_rate")], root="results"),
        T("stats", "breakdown", "Breakdown", "GET", "/stats/breakdown", [F("site_id", "Site", "query", required=True), F("property", "Property", "query", default="event:page"), F("period", "Period", "query", default="30d")], root="results", limit="limit"),
    ], "#5850ec", "sigma", "Marketing", "Website statistics from Plausible"),
    node("n8n-nodes-base.grafana", "Grafana", "grafanaApi", "{cred.baseUrl}/api", ["bearer:apiKey"], [
        T("annotation", "create", "Create Annotation", "POST", "/annotations", [F("text", "Text", required=True)], extra=[F("tags", "Tags", kind="list"), F("dashboardUID", "Dashboard UID")]),
        T("dashboard", "search", "Search Dashboards", "GET", "/search", [F("query", "Query", "query")]),
    ], "#f46800", "sigma", "Developer", "Annotations and dashboards in Grafana"),
]

CREDS += [
    simple("sevDeskApi", "sevDesk API", ("apiToken", "API Token", True, "", "")),
    simple("lexofficeApi", "lexoffice API", ("apiKey", "API Key", True, "", "")),
    simple("mailerLiteApi", "MailerLite API", ("apiKey", "API Key", True, "", "")),
    simple("mistralCloudApi", "Mistral Cloud API", ("apiKey", "API Key", True, "", "")),
    simple("groqApi", "Groq API", ("apiKey", "API Key", True, "", "")),
    simple("perplexityApi", "Perplexity API", ("apiKey", "API Key", True, "", "")),
    simple("ollamaApi", "Ollama", ("baseUrl", "Base URL", False, "http://host.docker.internal:11434", "")),
    simple("googleTranslateApi", "Google Translate API Key", ("apiKey", "API Key", True, "", "")),
    simple("youTubeApiKey", "YouTube API Key", ("apiKey", "API Key", True, "", "")),
    google("googleTasksOAuth2Api", "Google Tasks OAuth2 API", "https://www.googleapis.com/auth/tasks"),
    google("googleDocsOAuth2Api", "Google Docs OAuth2 API", "https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/drive.file"),
    google("googleBigQueryOAuth2Api", "Google BigQuery OAuth2 API", "https://www.googleapis.com/auth/bigquery"),
    microsoft("microsoftToDoOAuth2Api", "Microsoft To Do OAuth2 API", "openid offline_access Tasks.ReadWrite"),
    microsoft("microsoftOneDriveOAuth2Api", "Microsoft OneDrive OAuth2 API", "openid offline_access Files.ReadWrite.All"),
    simple("clockifyApi", "Clockify API", ("apiKey", "API Key", True, "", "")),
    simple("togglApi", "Toggl API", ("apiToken", "API Token", True, "", "")),
    simple("harvestApi", "Harvest API", ("accountId", "Account ID", False, "", ""), ("accessToken", "Personal Access Token", True, "", "")),
    simple("elasticsearchApi", "Elasticsearch API", ("baseUrl", "Base URL", False, "", "https://es.example.com:9200"), ("username", "Username", False, "elastic", ""), ("password", "Password", True, "", "")),
    simple("firebaseDatabaseApi", "Firebase Realtime Database", ("databaseUrl", "Database URL", False, "", "https://project-default-rtdb.firebaseio.com"), ("secret", "Database Secret / ID Token", True, "", "")),
    simple("mastodonApi", "Mastodon API", ("url", "Instance URL", False, "https://mastodon.social", ""), ("accessToken", "Access Token", True, "", "")),
    simple("discourseApi", "Discourse API", ("url", "URL", False, "", "https://forum.example.com"), ("username", "Username", False, "system", ""), ("apiKey", "API Key", True, "", "")),
    simple("bitbucketApi", "Bitbucket API", ("username", "Username", False, "", ""), ("appPassword", "App Password", True, "", "")),
    simple("zammadTokenAuthApi", "Zammad Token Auth", ("baseUrl", "Base URL", False, "", "https://support.example.com"), ("authHeader", "Authorization Header", True, "", "Token token=...")),
    simple("raindropOAuth2Api", "Raindrop API", ("accessToken", "Test Token", True, "", "")),
    simple("plausibleApi", "Plausible API", ("url", "URL", False, "https://plausible.io", ""), ("apiKey", "API Key", True, "", "")),
    simple("grafanaApi", "Grafana API", ("baseUrl", "Base URL", False, "", "https://grafana.example.com"), ("apiKey", "Service Account Token", True, "", "")),
]

# ---------------------------------------------------------------------------
# Batch 5: social networks (OAuth2/PKCE), media, spreadsheets-as-databases
# ---------------------------------------------------------------------------
def oauth_preset(name, display, auth_url, token_url, scope, authentication="header", extra=None, pkce=False):
    props = [
        {"displayName": "Client ID", "name": "clientId", "type": "string", "default": ""},
        {"displayName": "Client Secret", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Authorization URL", "name": "authUrl", "type": "hidden", "default": auth_url},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "hidden", "default": token_url},
        {"displayName": "Scope", "name": "scope", "type": "hidden", "default": scope},
        {"displayName": "Authentication", "name": "authentication", "type": "hidden", "default": authentication},
    ]
    if extra:
        props.append({"displayName": "Auth URI Query Parameters", "name": "authQueryParameters", "type": "hidden", "default": extra})
    if pkce:
        props.append({"displayName": "PKCE", "name": "pkce", "type": "hidden", "default": True})
    return {"name": name, "displayName": display, "properties": props}


NODES += [
    node("n8n-nodes-base.twitter", "X (Formerly Twitter)", "twitterOAuth2Api", "https://api.x.com/2", ["oauth2"], [
        T("tweet", "create", "Create", "POST", "/tweets", [N("text", "Text", rows=3, required=True), N("replyTo", "Reply to Tweet ID")],
          template={"text": "{text}", "reply": {"in_reply_to_tweet_id": "{replyTo}"}}, root="data"),
        T("tweet", "delete", "Delete", "DELETE", "/tweets/{tweetId}", [F("tweetId", "Tweet ID", "path", required=True)], root="data"),
        T("user", "me", "Get Me", "GET", "/users/me", [], root="data"),
    ], "#8b8b8b", "chat", "Marketing", "Post to X (Twitter)"),
    node("n8n-nodes-base.linkedIn", "LinkedIn", "linkedInOAuth2Api", "https://api.linkedin.com/v2", ["oauth2"], [
        T("post", "create", "Create", "POST", "/ugcPosts", [N("person", "Person URN ID", required=True, description="The id part of urn:li:person:<id> (from /v2/userinfo 'sub')"), N("text", "Text", rows=4, required=True),
                                                      N("visibility", "Visibility", kind="options", options=[("Public", "PUBLIC"), ("Connections", "CONNECTIONS")])],
          template={"author": "urn:li:person:{person}", "lifecycleState": "PUBLISHED",
                    "specificContent": {"com.linkedin.ugc.ShareContent": {"shareCommentary": {"text": "{text}"}, "shareMediaCategory": "NONE"}},
                    "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "{visibility}"}}),
        T("profile", "me", "Get Me", "GET", "/userinfo", []),
    ], "#0a66c2", "users", "Marketing", "Share posts on LinkedIn", headers={"X-Restli-Protocol-Version": "2.0.0"}),
    node("n8n-nodes-base.reddit", "Reddit", "redditOAuth2Api", "https://oauth.reddit.com", ["oauth2"], [
        T("post", "create", "Create", "POST", "/api/submit", [F("subreddit", "Subreddit", api="sr", required=True), F("title", "Title", required=True), F("text", "Text", rows=4)], static={"kind": "self", "api_type": "json"}),
        T("post", "getAll", "Get Many", "GET", "/r/{subreddit}/{category}.json", [F("subreddit", "Subreddit", "path", required=True), F("category", "Category", "path", kind="options", options=["hot", "new", "top", "rising"])],
          root="data.children", limit="limit"),
    ], "#ff4500", "chat", "Marketing", "Submit and read Reddit posts", body_type="form"),
    node("n8n-nodes-base.spotify", "Spotify", "spotifyOAuth2Api", "https://api.spotify.com/v1", ["oauth2"], [
        T("track", "search", "Search", "GET", "/search", [F("q", "Query", "query", required=True)], static_query={"type": "track"}, root="tracks.items", limit="limit"),
        T("player", "currentlyPlaying", "Currently Playing", "GET", "/me/player/currently-playing", []),
        T("player", "pause", "Pause", "PUT", "/me/player/pause", []),
        T("player", "resume", "Resume", "PUT", "/me/player/play", []),
        T("playlist", "add", "Add Track to Playlist", "POST", "/playlists/{playlistId}/tracks", [F("playlistId", "Playlist ID", "path", required=True), N("trackUri", "Track URI", required=True, placeholder="spotify:track:...")],
          template={"uris": ["{trackUri}"]}),
    ], "#1db954", "play", "Utility", "Search music and control playback on Spotify"),
    node("n8n-nodes-base.box", "Box", "boxOAuth2Api", "https://api.box.com/2.0", ["oauth2"], [
        T("folder", "getItems", "Get Items", "GET", "/folders/{folderId}/items", [F("folderId", "Folder ID", "path", default="0", required=True)], root="entries", limit="limit"),
        T("folder", "create", "Create", "POST", "/folders", [N("name", "Name", required=True), N("parentId", "Parent Folder ID", default="0")], template={"name": "{name}", "parent": {"id": "{parentId}"}}),
        T("file", "search", "Search", "GET", "/search", [F("query", "Query", "query", required=True)], root="entries", limit="limit"),
    ], "#0061d5", "file", "Data & Storage", "Files and folders in Box"),
    node("n8n-nodes-base.facebookGraphApi", "Facebook Graph API", "facebookGraphApi", "https://graph.facebook.com/v21.0", ["query:access_token:accessToken"], [
        T("page", "post", "Post to Page", "POST", "/{pageId}/feed", [F("pageId", "Page ID", "path", required=True), F("message", "Message", rows=3, required=True)], extra=[F("link", "Link")]),
        T("node", "get", "Get Node", "GET", "/{+nodeId}", [F("nodeId", "Node / Edge", "path", required=True, placeholder="me/accounts"), F("fields", "Fields", "query")]),
    ], "#1877f2", "users", "Marketing", "Call the Facebook Graph API"),
    node("n8n-nodes-base.coda", "Coda", "codaApi", "https://coda.io/apis/v1", ["bearer:accessToken"], [
        T("row", "getAll", "Get Many", "GET", "/docs/{docId}/tables/{tableId}/rows", [F("docId", "Doc ID", "path", required=True), F("tableId", "Table ID or Name", "path", required=True)],
          static_query={"useColumnNames": "true", "valueFormat": "simple"}, root="items", limit="limit"),
        T("row", "create", "Insert", "POST", "/docs/{docId}/tables/{tableId}/rows", [F("docId", "Doc ID", "path", required=True), F("tableId", "Table ID or Name", "path", required=True),
                                                                                      N("cells", "Cells (JSON array)", kind="json", rows=4, default='[{"column": "Name", "value": "Ada"}]')],
          template={"rows": [{"cells": "{cells}"}]}),
    ], "#f46a54", "table", "Data & Storage", "Rows in Coda tables"),
    node("n8n-nodes-base.nocoDb", "NocoDB", "nocoDbApiToken", "{cred.host}/api/v2", ["header:xc-token:apiToken"], [
        T("row", "getAll", "Get Many", "GET", "/tables/{tableId}/records", [F("tableId", "Table ID", "path", required=True), F("where", "Where", "query", placeholder="(Status,eq,Open)")], root="list", limit="limit"),
        T("row", "get", "Get", "GET", "/tables/{tableId}/records/{rowId}", [F("tableId", "Table ID", "path", required=True), F("rowId", "Row ID", "path", required=True)]),
        T("row", "create", "Create", "POST", "/tables/{tableId}/records", [F("tableId", "Table ID", "path", required=True), F("data", "Row (JSON)", "none", kind="json", rows=6)], body_from="jsonParam"),
        T("row", "update", "Update", "PATCH", "/tables/{tableId}/records", [F("tableId", "Table ID", "path", required=True), F("data", "Row incl. Id (JSON)", "none", kind="json", rows=6)], body_from="jsonParam"),
    ], "#3366ff", "table", "Data & Storage", "Records in NocoDB"),
    node("n8n-nodes-base.grist", "Grist", "gristApi", "{cred.domain}/api", ["bearer:apiKey"], [
        T("row", "getAll", "Get Many", "GET", "/docs/{docId}/tables/{tableId}/records", [F("docId", "Document ID", "path", required=True), F("tableId", "Table ID", "path", required=True)], root="records", limit="limit"),
        T("row", "create", "Create", "POST", "/docs/{docId}/tables/{tableId}/records", [F("docId", "Document ID", "path", required=True), F("tableId", "Table ID", "path", required=True), N("fields", "Fields (JSON)", kind="json", rows=5)],
          template={"records": [{"fields": "{fields}"}]}, root="records"),
    ], "#16b378", "table", "Data & Storage", "Records in Grist"),
]

CREDS += [
    oauth_preset("twitterOAuth2Api", "X OAuth2 API", "https://x.com/i/oauth2/authorize", "https://api.x.com/2/oauth2/token", "tweet.read tweet.write users.read offline.access", pkce=True),
    oauth_preset("linkedInOAuth2Api", "LinkedIn OAuth2 API", "https://www.linkedin.com/oauth/v2/authorization", "https://www.linkedin.com/oauth/v2/accessToken", "openid profile w_member_social", authentication="body"),
    oauth_preset("redditOAuth2Api", "Reddit OAuth2 API", "https://www.reddit.com/api/v1/authorize", "https://www.reddit.com/api/v1/access_token", "identity submit read", extra="duration=permanent"),
    oauth_preset("spotifyOAuth2Api", "Spotify OAuth2 API", "https://accounts.spotify.com/authorize", "https://accounts.spotify.com/api/token",
                 "user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-modify-public playlist-modify-private"),
    oauth_preset("boxOAuth2Api", "Box OAuth2 API", "https://account.box.com/api/oauth2/authorize", "https://api.box.com/oauth2/token", "", authentication="body"),
    simple("facebookGraphApi", "Facebook Graph API", ("accessToken", "Access Token", True, "", "")),
    simple("codaApi", "Coda API", ("accessToken", "API Token", True, "", "")),
    simple("nocoDbApiToken", "NocoDB API Token", ("host", "Host", False, "https://app.nocodb.com", ""), ("apiToken", "API Token", True, "", "")),
    simple("gristApi", "Grist API", ("domain", "Server URL", False, "https://docs.getgrist.com", ""), ("apiKey", "API Key", True, "", "")),
]

NODES += [
    node("n8n-nodes-base.n8n", "Flowwolf / n8n API", "n8nApi", "{cred.baseUrl}/api/v1", ["header:X-N8N-API-KEY:apiKey"], [
        T("workflow", "getAll", "Get Many", "GET", "/workflows", [F("active", "Active Only", "query", kind="options", options=[("All", ""), ("Active", "true"), ("Inactive", "false")])], root="data"),
        T("workflow", "get", "Get", "GET", "/workflows/{workflowId}", [F("workflowId", "Workflow ID", "path", required=True)]),
        T("workflow", "activate", "Activate", "POST", "/workflows/{workflowId}/activate", [F("workflowId", "Workflow ID", "path", required=True)]),
        T("workflow", "deactivate", "Deactivate", "POST", "/workflows/{workflowId}/deactivate", [F("workflowId", "Workflow ID", "path", required=True)]),
        T("execution", "getAll", "Get Many", "GET", "/executions", [F("workflowId", "Workflow ID", "query"), F("status", "Status", "query", kind="options", options=[("Any", ""), ("Success", "success"), ("Error", "error")])], root="data", limit="limit"),
        T("execution", "get", "Get", "GET", "/executions/{executionId}", [F("executionId", "Execution ID", "path", required=True)]),
        T("credential", "create", "Create", "POST", "/credentials", [F("name", "Name", required=True), F("credentialTypeName", "Type", api="type", required=True), F("data", "Data (JSON)", kind="json")]),
    ], "#ff6d5a", "workflow", "Core", "Manage workflows and executions through the public API"),
]
CREDS += [simple("n8nApi", "Flowwolf / n8n API", ("apiKey", "API Key", True, "", ""), ("baseUrl", "Base URL", False, "http://127.0.0.1:5678", ""))]

# ---------------------------------------------------------------------------
# Batch 6: shop systems, support desks, analytics, CI, payments
# ---------------------------------------------------------------------------
NODES += [
    node("n8n-nodes-base.shopware", "Shopware 6", "shopware6Api", "{cred.url}/api", ["oauth2"], [
        T("product", "search", "Search", "POST", "/search/product", [N("term", "Term"), N("filter", "Filter (JSON array)", kind="json", default="[]")],
          template={"term": "{term}", "filter": "{filter}"}, root="data", limit="limit"),
        T("product", "get", "Get", "GET", "/product/{productId}", [F("productId", "Product ID", "path", required=True)], root="data"),
        T("product", "update", "Update", "PATCH", "/product/{productId}", [F("productId", "Product ID", "path", required=True), F("data", "Fields (JSON)", "none", kind="json", rows=5, default='{"stock": 10}')],
          body_from="jsonParam"),
        T("order", "search", "Search", "POST", "/search/order", [N("filter", "Filter (JSON array)", kind="json", default="[]")],
          template={"filter": "{filter}", "sort": [{"field": "orderDateTime", "order": "DESC"}]}, root="data", limit="limit"),
        T("order", "get", "Get", "GET", "/order/{orderId}", [F("orderId", "Order ID", "path", required=True)], root="data"),
        T("order", "transition", "Change State", "POST", "/_action/order/{orderId}/state/{transition}",
          [F("orderId", "Order ID", "path", required=True), F("transition", "Transition", "path", kind="options", options=["process", "complete", "cancel", "reopen"])]),
        T("customer", "search", "Search", "POST", "/search/customer", [N("term", "Term")], template={"term": "{term}"}, root="data", limit="limit"),
    ], "#189eff", "cart", "Sales", "Products, orders and customers in Shopware 6 (Admin API)", headers={"Accept": "application/json"}),
    node("n8n-nodes-base.confluence", "Confluence", "confluenceApi", "{cred.domain}/wiki/api/v2", ["basic:email:apiToken"], [
        T("page", "get", "Get", "GET", "/pages/{pageId}", [F("pageId", "Page ID", "path", required=True)], static_query={"body-format": "storage"}),
        T("page", "getAll", "Get Many", "GET", "/spaces/{spaceId}/pages", [F("spaceId", "Space ID", "path", required=True)], root="results", limit="limit"),
        T("page", "create", "Create", "POST", "/pages", [N("spaceId", "Space ID", required=True), N("title", "Title", required=True), N("body", "Body (storage format)", rows=5), N("parentId", "Parent Page ID")],
          template={"spaceId": "{spaceId}", "status": "current", "title": "{title}", "parentId": "{parentId}", "body": {"representation": "storage", "value": "{body}"}}),
        T("space", "getAll", "Get Many", "GET", "/spaces", [], root="results", limit="limit"),
    ], "#1868db", "file", "Productivity", "Pages and spaces in Confluence Cloud"),
    node("n8n-nodes-base.serviceNow", "ServiceNow", "serviceNowBasicApi", "https://{cred.subdomain}.service-now.com/api/now", ["basic:user:password"], [
        T("incident", "create", "Create", "POST", "/table/incident", [F("short_description", "Short Description", required=True)],
          extra=[F("description", "Description", rows=3), F("urgency", "Urgency", kind="options", options=[("High", "1"), ("Medium", "2"), ("Low", "3")]), F("assignment_group", "Assignment Group")], root="result"),
        T("incident", "get", "Get", "GET", "/table/incident/{sysId}", [F("sysId", "Sys ID", "path", required=True)], root="result"),
        T("incident", "getAll", "Get Many", "GET", "/table/incident", [F("sysparm_query", "Query", "query", placeholder="active=true^priority=1")], root="result", limit="sysparm_limit"),
        T("incident", "update", "Update", "PATCH", "/table/incident/{sysId}", [F("sysId", "Sys ID", "path", required=True)],
          extra=[F("state", "State"), F("work_notes", "Work Notes", rows=3), F("close_notes", "Close Notes")], root="result"),
        T("tableRecord", "getAll", "Get Many", "GET", "/table/{tableName}", [F("tableName", "Table", "path", required=True), F("sysparm_query", "Query", "query")], root="result", limit="sysparm_limit"),
    ], "#62d84e", "ticket", "Support", "Incidents and table records in ServiceNow"),
    node("n8n-nodes-base.zendesk", "Zendesk", "zendeskApi", "https://{cred.subdomain}.zendesk.com/api/v2", ["basic:{email}/token:apiToken"], [
        T("ticket", "create", "Create", "POST", "/tickets", [N("subject", "Subject", required=True), N("description", "Description", rows=4, required=True), N("priority", "Priority", kind="options", options=["normal", "low", "high", "urgent"])],
          template={"ticket": {"subject": "{subject}", "comment": {"body": "{description}"}, "priority": "{priority}"}}, root="ticket"),
        T("ticket", "get", "Get", "GET", "/tickets/{ticketId}", [F("ticketId", "Ticket ID", "path", required=True)], root="ticket"),
        T("ticket", "getAll", "Get Many", "GET", "/tickets", [], root="tickets", limit="per_page"),
        T("ticket", "update", "Add Comment", "PUT", "/tickets/{ticketId}", [F("ticketId", "Ticket ID", "path", required=True), N("comment", "Comment", rows=3, required=True),
                                                                         N("public", "Public", kind="boolean", default=True), N("status", "Status", kind="options", options=[("Keep", ""), "open", "pending", "solved"])],
          template={"ticket": {"comment": {"body": "{comment}", "public": "{public}"}, "status": "{status}"}}, root="ticket"),
        T("user", "search", "Search", "GET", "/users/search", [F("query", "Query", "query", required=True)], root="users"),
    ], "#03363d", "ticket", "Support", "Tickets and users in Zendesk"),
    node("n8n-nodes-base.mailjet", "Mailjet", "mailjetEmailApi", "https://api.mailjet.com/v3.1", ["basic:apiKey:secretKey"], [
        T("email", "send", "Send", "POST", "/send", [N("from", "From Email", required=True), N("to", "To Email", required=True), N("subject", "Subject", required=True), N("text", "Text", rows=4), N("html", "HTML", rows=4)],
          template={"Messages": [{"From": {"Email": "{from}"}, "To": [{"Email": "{to}"}], "Subject": "{subject}", "TextPart": "{text}", "HTMLPart": "{html}"}]}, root="Messages"),
    ], "#9585f4", "mail", "Communication", "Send emails with Mailjet"),
    node("n8n-nodes-base.postHog", "PostHog", "postHogApi", "{cred.url}", [], [
        T("event", "create", "Capture", "POST", "/capture/", [N("event", "Event", required=True), N("distinctId", "Distinct ID", required=True), N("properties", "Properties (JSON)", kind="json", default="{}")],
          template={"api_key": "{cred.apiKey}", "event": "{event}", "distinct_id": "{distinctId}", "properties": "{properties}"}),
        T("identity", "create", "Identify", "POST", "/capture/", [N("distinctId", "Distinct ID", required=True), N("properties", "Person Properties (JSON)", kind="json", default="{}")],
          template={"api_key": "{cred.apiKey}", "event": "$identify", "distinct_id": "{distinctId}", "properties": {"$set": "{properties}"}}),
    ], "#f54e00", "chart", "Analytics", "Capture events in PostHog"),
    node("n8n-nodes-base.amplitude", "Amplitude", "amplitudeApi", "https://api2.amplitude.com", [], [
        T("event", "track", "Track", "POST", "/2/httpapi", [N("eventType", "Event Type", required=True), N("userId", "User ID", required=True), N("eventProperties", "Event Properties (JSON)", kind="json", default="{}")],
          template={"api_key": "{cred.apiKey}", "events": [{"event_type": "{eventType}", "user_id": "{userId}", "event_properties": "{eventProperties}"}]}),
    ], "#1e61f0", "chart", "Analytics", "Send events to Amplitude"),
    node("n8n-nodes-base.circleCi", "CircleCI", "circleCiApi", "https://circleci.com/api/v2", ["header:Circle-Token:apiKey"], [
        T("pipeline", "trigger", "Trigger", "POST", "/project/{+projectSlug}/pipeline", [F("projectSlug", "Project Slug", "path", required=True, placeholder="gh/org/repo"), F("branch", "Branch")]),
        T("pipeline", "get", "Get", "GET", "/pipeline/{pipelineId}", [F("pipelineId", "Pipeline ID", "path", required=True)]),
        T("pipeline", "getAll", "Get Many", "GET", "/project/{+projectSlug}/pipeline", [F("projectSlug", "Project Slug", "path", required=True), F("branch", "Branch", "query")], root="items"),
    ], "#343434", "workflow", "Developer", "Trigger and inspect CircleCI pipelines"),
    node("n8n-nodes-base.jotForm", "Jotform", "jotFormApi", "https://{cred.apiDomain}", ["header:APIKEY:apiKey"], [
        T("form", "getAll", "Get Many", "GET", "/user/forms", [], root="content", limit="limit"),
        T("submission", "getAll", "Get Many", "GET", "/form/{formId}/submissions", [F("formId", "Form ID", "path", required=True)], root="content", limit="limit"),
    ], "#ff6100", "form", "Utility", "Forms and submissions in Jotform"),
    node("n8n-nodes-base.bannerbear", "Bannerbear", "bannerbearApi", "https://sync.api.bannerbear.com/v2", ["bearer:apiKey"], [
        T("image", "create", "Create", "POST", "/images", [N("template", "Template ID", required=True), N("modifications", "Modifications (JSON array)", kind="json", default='[{"name": "title", "text": "Hello"}]')],
          template={"template": "{template}", "modifications": "{modifications}"}),
    ], "#ffcc00", "image", "Marketing", "Render images from Bannerbear templates"),
    node("n8n-nodes-base.paddle", "Paddle", "paddleApi", "https://api.paddle.com", ["bearer:apiKey"], [
        T("customer", "getAll", "Get Many", "GET", "/customers", [F("email", "Email", "query")], root="data", limit="per_page"),
        T("transaction", "getAll", "Get Many", "GET", "/transactions", [F("status", "Status", "query")], root="data", limit="per_page"),
        T("subscription", "get", "Get", "GET", "/subscriptions/{subscriptionId}", [F("subscriptionId", "Subscription ID", "path", required=True)], root="data"),
    ], "#ffd200", "cart", "Sales", "Customers, transactions and subscriptions in Paddle Billing"),
    node("n8n-nodes-base.mollie", "Mollie", "mollieApi", "https://api.mollie.com/v2", ["bearer:apiKey"], [
        T("payment", "create", "Create", "POST", "/payments", [N("amount", "Amount", required=True, placeholder="10.00"), N("currency", "Currency", default="EUR"), N("description", "Description", required=True), N("redirectUrl", "Redirect URL", required=True)],
          template={"amount": {"currency": "{currency}", "value": "{amount}"}, "description": "{description}", "redirectUrl": "{redirectUrl}"}),
        T("payment", "get", "Get", "GET", "/payments/{paymentId}", [F("paymentId", "Payment ID", "path", required=True)]),
        T("payment", "getAll", "Get Many", "GET", "/payments", [], root="_embedded.payments", limit="limit"),
    ], "#000000", "cart", "Sales", "Payments with Mollie"),
]

CREDS += [
    {"name": "shopware6Api", "displayName": "Shopware 6 Admin API", "properties": [
        {"displayName": "Shop URL", "name": "url", "type": "string", "default": "", "placeholder": "https://shop.example.com"},
        {"displayName": "Access Key ID", "name": "clientId", "type": "string", "default": "", "description": "Settings > System > Integrations"},
        {"displayName": "Secret Access Key", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Grant Type", "name": "grantType", "type": "hidden", "default": "clientCredentials"},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "hidden", "default": "{url}/api/oauth/token"},
        {"displayName": "Authentication", "name": "authentication", "type": "hidden", "default": "body"}]},
    simple("confluenceApi", "Confluence Cloud API", ("domain", "Domain", False, "", "https://example.atlassian.net"), ("email", "Email", False, "", ""), ("apiToken", "API Token", True, "", "")),
    simple("serviceNowBasicApi", "ServiceNow Basic Auth", ("subdomain", "Subdomain", False, "", "dev12345"), ("user", "User", False, "", ""), ("password", "Password", True, "", "")),
    simple("zendeskApi", "Zendesk API", ("subdomain", "Subdomain", False, "", "acme"), ("email", "Email", False, "", ""), ("apiToken", "API Token", True, "", "")),
    simple("mailjetEmailApi", "Mailjet Email API", ("apiKey", "API Key", False, "", ""), ("secretKey", "Secret Key", True, "", "")),
    simple("postHogApi", "PostHog API", ("url", "URL", False, "https://eu.i.posthog.com", ""), ("apiKey", "Project API Key", True, "", "")),
    simple("amplitudeApi", "Amplitude API", ("apiKey", "API Key", True, "", "")),
    simple("circleCiApi", "CircleCI API", ("apiKey", "Personal API Token", True, "", "")),
    simple("jotFormApi", "Jotform API", ("apiKey", "API Key", True, "", ""), ("apiDomain", "API Domain", False, "eu-api.jotform.com", "api.jotform.com")),
    simple("bannerbearApi", "Bannerbear API", ("apiKey", "API Key", True, "", "")),
    simple("paddleApi", "Paddle API", ("apiKey", "API Key", True, "", "")),
    simple("mollieApi", "Mollie API", ("apiKey", "API Key", True, "", "")),
]

# ---------------------------------------------------------------------------
# Batch 7: CRM/ERP via OAuth2, sales intelligence, marketing automation, security, ops
# ---------------------------------------------------------------------------
JSONBODY = F("data", "Fields (JSON)", "none", kind="json", rows=6, default="{}")


def with_fields(cred, *fields):
    """Adds plain string fields (instance URL, region, ...) to a credential definition."""
    cred["properties"] = [{"displayName": d, "name": n, "type": "string", "default": dflt, **({"placeholder": ph} if ph else {})} for n, d, dflt, ph in fields] + cred["properties"]
    return cred


NODES += [
    node("n8n-nodes-base.salesforce", "Salesforce", "salesforceOAuth2Api", "{cred.instanceUrl}/services/data/v60.0", ["oauth2"], [
        T("record", "query", "SOQL Query", "GET", "/query", [F("q", "SOQL", "query", required=True, placeholder="SELECT Id, Name FROM Account LIMIT 10")], root="records"),
        T("record", "create", "Create", "POST", "/sobjects/{sobject}", [F("sobject", "Object", "path", required=True, placeholder="Lead"), JSONBODY], body_from="jsonParam"),
        T("record", "get", "Get", "GET", "/sobjects/{sobject}/{recordId}", [F("sobject", "Object", "path", required=True), F("recordId", "Record ID", "path", required=True)]),
        T("record", "update", "Update", "PATCH", "/sobjects/{sobject}/{recordId}", [F("sobject", "Object", "path", required=True), F("recordId", "Record ID", "path", required=True), JSONBODY], body_from="jsonParam"),
    ], "#00a1e0", "cloud", "Sales", "Query and change Salesforce records"),
    node("n8n-nodes-base.zohoCrm", "Zoho CRM", "zohoOAuth2Api", "https://www.zohoapis.{cred.dataCenter}/crm/v6", ["oauth2"], [
        T("record", "getAll", "Get Many", "GET", "/{module}", [F("module", "Module", "path", required=True, default="Leads"), F("fields", "Fields", "query", default="Last_Name,Email")], root="data", limit="per_page"),
        T("record", "create", "Create", "POST", "/{module}", [F("module", "Module", "path", required=True, default="Leads"), N("record", "Record (JSON)", kind="json", rows=5, default='{"Last_Name": "Lovelace"}')],
          template={"data": ["{record}"]}, root="data"),
    ], "#e42527", "users", "Sales", "Records in Zoho CRM"),
    node("n8n-nodes-base.xero", "Xero", "xeroOAuth2Api", "https://api.xero.com/api.xro/2.0", ["oauth2"], [
        T("invoice", "getAll", "Get Many", "GET", "/Invoices", [F("where", "Where", "query", placeholder='Status=="AUTHORISED"')], root="Invoices"),
        T("contact", "getAll", "Get Many", "GET", "/Contacts", [], root="Contacts"),
        T("contact", "create", "Create", "POST", "/Contacts", [N("name", "Name", required=True), N("email", "Email")], template={"Contacts": [{"Name": "{name}", "EmailAddress": "{email}"}]}, root="Contacts"),
    ], "#13b5ea", "cart", "Finance", "Invoices and contacts in Xero", headers={"Xero-tenant-id": "{cred.tenantId}", "Accept": "application/json"}),
    node("n8n-nodes-base.quickbooks", "QuickBooks Online", "quickBooksOAuth2Api", "https://{cred.apiHost}/v3/company/{cred.realmId}", ["oauth2"], [
        T("record", "query", "Query", "GET", "/query", [F("query", "Query", "query", required=True, default="SELECT * FROM Customer MAXRESULTS 10")], root="QueryResponse"),
        T("customer", "create", "Create Customer", "POST", "/customer", [JSONBODY], body_from="jsonParam", root="Customer"),
    ], "#2ca01c", "cart", "Finance", "Customers and queries in QuickBooks Online", headers={"Accept": "application/json"}),
    node("n8n-nodes-base.hunter", "Hunter", "hunterApi", "https://api.hunter.io/v2", ["query:api_key:apiKey"], [
        T("email", "domainSearch", "Domain Search", "GET", "/domain-search", [F("domain", "Domain", "query", required=True)], root="data"),
        T("email", "find", "Email Finder", "GET", "/email-finder", [F("domain", "Domain", "query", required=True), F("first_name", "First Name", "query"), F("last_name", "Last Name", "query")], root="data"),
        T("email", "verify", "Email Verifier", "GET", "/email-verifier", [F("email", "Email", "query", required=True)], root="data"),
    ], "#fa5320", "search", "Sales", "Find and verify email addresses with Hunter"),
    node("n8n-nodes-base.brandfetch", "Brandfetch", "brandfetchApi", "https://api.brandfetch.io/v2", ["bearer:apiKey"], [
        T("brand", "get", "Get", "GET", "/brands/{domain}", [F("domain", "Domain", "path", required=True, placeholder="n8n.io")]),
    ], "#000000", "image", "Marketing", "Logos, colors and fonts of a brand"),
    node("n8n-nodes-base.lemlist", "Lemlist", "lemlistApi", "https://api.lemlist.com/api", ["basic:=:apiKey"], [
        T("campaign", "getAll", "Get Many", "GET", "/campaigns", []),
        T("lead", "create", "Create", "POST", "/campaigns/{campaignId}/leads/{email}", [F("campaignId", "Campaign ID", "path", required=True), F("email", "Email", "path", required=True)],
          extra=[F("firstName", "First Name"), F("lastName", "Last Name"), F("companyName", "Company")]),
        T("lead", "get", "Get", "GET", "/leads/{email}", [F("email", "Email", "path", required=True)]),
        T("lead", "delete", "Delete", "DELETE", "/campaigns/{campaignId}/leads/{email}", [F("campaignId", "Campaign ID", "path", required=True), F("email", "Email", "path", required=True)]),
    ], "#316bff", "mail", "Marketing", "Campaigns and leads in Lemlist"),
    node("n8n-nodes-base.customerIo", "Customer.io", "customerIoApi", "https://track.customer.io/api/v1", ["basic:trackingSiteId:trackingApiKey"], [
        T("customer", "upsert", "Create or Update", "PUT", "/customers/{customerId}", [F("customerId", "Customer ID", "path", required=True), JSONBODY], body_from="jsonParam"),
        T("event", "track", "Track", "POST", "/customers/{customerId}/events", [F("customerId", "Customer ID", "path", required=True), N("name", "Event Name", required=True), N("eventData", "Data (JSON)", kind="json", default="{}")],
          template={"name": "{name}", "data": "{eventData}"}),
        T("customer", "delete", "Delete", "DELETE", "/customers/{customerId}", [F("customerId", "Customer ID", "path", required=True)]),
    ], "#7131ff", "mail", "Marketing", "Customers and events in Customer.io"),
    node("n8n-nodes-base.segment", "Segment", "segmentApi", "https://api.segment.io/v1", ["basic:writekey:="], [
        T("track", "event", "Track Event", "POST", "/track", [N("userId", "User ID", required=True), N("event", "Event", required=True), N("properties", "Properties (JSON)", kind="json", default="{}")],
          template={"userId": "{userId}", "event": "{event}", "properties": "{properties}"}),
        T("identify", "create", "Identify", "POST", "/identify", [N("userId", "User ID", required=True), N("traits", "Traits (JSON)", kind="json", default="{}")], template={"userId": "{userId}", "traits": "{traits}"}),
    ], "#52bd94", "chart", "Analytics", "Send events and traits to Segment"),
    node("n8n-nodes-base.iterable", "Iterable", "iterableApi", "https://api.iterable.com/api", ["header:Api-Key:apiKey"], [
        T("event", "track", "Track", "POST", "/events/track", [N("email", "Email", required=True), N("eventName", "Event Name", required=True), N("dataFields", "Data Fields (JSON)", kind="json", default="{}")],
          template={"email": "{email}", "eventName": "{eventName}", "dataFields": "{dataFields}"}),
        T("user", "upsert", "Create or Update", "POST", "/users/update", [N("email", "Email", required=True), N("dataFields", "Data Fields (JSON)", kind="json", default="{}")], template={"email": "{email}", "dataFields": "{dataFields}"}),
        T("user", "get", "Get", "GET", "/users/{email}", [F("email", "Email", "path", required=True)], root="user"),
    ], "#36c3f2", "mail", "Marketing", "Users and events in Iterable"),
    node("n8n-nodes-base.getResponse", "GetResponse", "getResponseApi", "https://api.getresponse.com/v3", ["header:X-Auth-Token:api-key {apiKey}"], [
        T("contact", "create", "Create", "POST", "/contacts", [N("email", "Email", required=True), N("name", "Name"), N("campaignId", "List (Campaign) ID", required=True)],
          template={"email": "{email}", "name": "{name}", "campaign": {"campaignId": "{campaignId}"}}),
        T("contact", "getAll", "Get Many", "GET", "/contacts", [F("query[email]", "Email Filter", "query")], limit="perPage"),
    ], "#00baff", "mail", "Marketing", "Contacts in GetResponse"),
    node("n8n-nodes-base.pushbullet", "Pushbullet", "pushbulletOAuth2Api", "https://api.pushbullet.com/v2", ["header:Access-Token:accessToken"], [
        T("push", "create", "Create", "POST", "/pushes", [N("title", "Title"), N("body", "Body", rows=3, required=True)], template={"type": "note", "title": "{title}", "body": "{body}"}),
        T("push", "getAll", "Get Many", "GET", "/pushes", [], root="pushes", limit="limit"),
    ], "#4ab367", "bell", "Communication", "Send and list Pushbullet pushes"),
    node("n8n-nodes-base.pushcut", "Pushcut", "pushcutApi", "https://api.pushcut.io/v1", ["header:API-Key:apiKey"], [
        T("notification", "send", "Send", "POST", "/notifications/{notificationName}", [F("notificationName", "Notification Name", "path", required=True), F("title", "Title"), F("text", "Text")]),
    ], "#1f2937", "bell", "Communication", "Trigger Pushcut notifications on iOS"),
    node("n8n-nodes-base.signl4", "SIGNL4", "signl4Api", "https://connect.signl4.com/webhook/{cred.teamSecret}", [], [
        T("alert", "send", "Send", "POST", "", [N("title", "Title", required=True), N("message", "Message", rows=3), N("externalId", "External ID")],
          template={"Title": "{title}", "Message": "{message}", "X-S4-ExternalID": "{externalId}", "X-S4-Status": "new"}),
        T("alert", "resolve", "Resolve", "POST", "", [N("externalId", "External ID", required=True)], template={"X-S4-ExternalID": "{externalId}", "X-S4-Status": "resolved"}),
    ], "#4ca1f0", "bell", "Communication", "Raise and resolve SIGNL4 alerts"),
    node("n8n-nodes-base.yourls", "Yourls", "yourlsApi", "{cred.url}/yourls-api.php", ["query:signature:signature"], [
        T("url", "shorten", "Shorten", "GET", "", [F("url", "URL", "query", required=True), F("keyword", "Keyword", "query")], static_query={"action": "shorturl", "format": "json"}),
        T("url", "expand", "Expand", "GET", "", [F("shorturl", "Short URL", "query", required=True)], static_query={"action": "expand", "format": "json"}),
        T("url", "stats", "Stats", "GET", "", [F("shorturl", "Short URL", "query", required=True)], static_query={"action": "url-stats", "format": "json"}, root="link"),
    ], "#1e1e1e", "link", "Utility", "Shorten and expand links with your YOURLS instance"),
    node("n8n-nodes-base.storyblok", "Storyblok", "storyblokManagementApi", "https://mapi.storyblok.com/v1", ["header:Authorization:accessToken"], [
        T("story", "getAll", "Get Many", "GET", "/spaces/{space}/stories", [F("space", "Space ID", "path", required=True), F("starts_with", "Starts With", "query")], root="stories", limit="per_page"),
        T("story", "get", "Get", "GET", "/spaces/{space}/stories/{storyId}", [F("space", "Space ID", "path", required=True), F("storyId", "Story ID", "path", required=True)], root="story"),
        T("story", "publish", "Publish", "GET", "/spaces/{space}/stories/{storyId}/publish", [F("space", "Space ID", "path", required=True), F("storyId", "Story ID", "path", required=True)]),
    ], "#09b3af", "file", "Marketing", "Stories in Storyblok (Management API)"),
    node("n8n-nodes-base.travisCi", "Travis CI", "travisCiApi", "https://api.travis-ci.com", ["header:Authorization:token {apiToken}"], [
        T("build", "getAll", "Get Many", "GET", "/builds", [], root="builds", limit="limit"),
        T("build", "trigger", "Trigger", "POST", "/repo/{slug}/requests", [F("slug", "Repository", "path", required=True, placeholder="owner/repo"), N("branch", "Branch", default="main"), N("message", "Message")],
          template={"request": {"branch": "{branch}", "message": "{message}"}}),
    ], "#3eaaaf", "workflow", "Developer", "Builds on Travis CI", headers={"Travis-API-Version": "3"}),
    node("n8n-nodes-base.rundeck", "Rundeck", "rundeckApi", "{cred.url}/api/41", ["header:X-Rundeck-Auth-Token:token"], [
        T("job", "execute", "Execute", "POST", "/job/{jobId}/run", [F("jobId", "Job ID", "path", required=True), F("argString", "Arguments", placeholder="-name value")]),
        T("job", "getMetadata", "Get Metadata", "GET", "/job/{jobId}/info", [F("jobId", "Job ID", "path", required=True)]),
    ], "#e5282e", "terminal", "Developer", "Run Rundeck jobs", headers={"Accept": "application/json"}),
    node("n8n-nodes-base.splunk", "Splunk", "splunkApi", "{cred.baseUrl}/services", ["bearer:authToken"], [
        T("search", "create", "Create Search Job", "POST", "/search/jobs", [F("search", "Search", required=True, placeholder="search index=main | head 10")], static={"output_mode": "json"}),
        T("search", "get", "Get Search Job", "GET", "/search/jobs/{sid}", [F("sid", "Search ID", "path", required=True)], static_query={"output_mode": "json"}, root="entry"),
        T("search", "getResults", "Get Results", "GET", "/search/jobs/{sid}/results", [F("sid", "Search ID", "path", required=True)], static_query={"output_mode": "json"}, root="results"),
    ], "#65a637", "search", "Developer", "Searches in Splunk", body_type="form"),
    node("n8n-nodes-base.theHiveProject", "TheHive 5", "theHiveProjectApi", "{cred.url}/api/v1", ["bearer:apiKey"], [
        T("alert", "create", "Create", "POST", "/alert", [F("type", "Type", required=True, default="external"), F("source", "Source", required=True, default="flowwolf"),
                                                    F("sourceRef", "Source Reference", required=True), F("title", "Title", required=True), F("description", "Description", rows=3, required=True)],
          extra=[F("severity", "Severity", kind="options", options=[("Low", 1), ("Medium", 2), ("High", 3), ("Critical", 4)])]),
        T("case", "create", "Create", "POST", "/case", [F("title", "Title", required=True), F("description", "Description", rows=3, required=True)]),
        T("case", "get", "Get", "GET", "/case/{caseId}", [F("caseId", "Case ID", "path", required=True)]),
    ], "#f2c200", "ticket", "Security", "Alerts and cases in TheHive 5"),
    node("n8n-nodes-base.misp", "MISP", "mispApi", "{cred.baseUrl}", ["header:Authorization:apiKey"], [
        T("event", "create", "Create", "POST", "/events/add", [N("info", "Info", required=True), N("distribution", "Distribution", kind="options", options=[("Your organisation", "0"), ("Community", "1"), ("Connected", "2"), ("All", "3")]),
                                                         N("threatLevel", "Threat Level", kind="options", options=[("High", "1"), ("Medium", "2"), ("Low", "3"), ("Undefined", "4")])],
          template={"Event": {"info": "{info}", "distribution": "{distribution}", "threat_level_id": "{threatLevel}", "analysis": "0"}}, root="Event"),
        T("event", "get", "Get", "GET", "/events/view/{eventId}", [F("eventId", "Event ID", "path", required=True)], root="Event"),
        T("attribute", "search", "Search", "POST", "/attributes/restSearch", [F("value", "Value", required=True)], root="response.Attribute"),
    ], "#2c3e50", "shield", "Security", "Events and attributes in MISP", headers={"Accept": "application/json"}),
    node("n8n-nodes-base.gong", "Gong", "gongApi", "https://api.gong.io/v2", ["basic:accessKey:accessKeySecret"], [
        T("call", "getAll", "Get Many", "GET", "/calls", [F("fromDateTime", "From", "query", placeholder="2026-01-01T00:00:00Z"), F("toDateTime", "To", "query")], root="calls"),
        T("call", "get", "Get", "GET", "/calls/{callId}", [F("callId", "Call ID", "path", required=True)], root="call"),
        T("user", "getAll", "Get Many", "GET", "/users", [], root="users"),
    ], "#8039df", "users", "Sales", "Calls and users in Gong"),
    node("n8n-nodes-base.highLevel", "HighLevel", "highLevelApi", "https://services.leadconnectorhq.com", ["bearer:apiKey"], [
        T("contact", "create", "Create", "POST", "/contacts/", [F("locationId", "Location ID", required=True), F("email", "Email"), F("firstName", "First Name"), F("lastName", "Last Name"), F("phone", "Phone")], root="contact"),
        T("contact", "get", "Get", "GET", "/contacts/{contactId}", [F("contactId", "Contact ID", "path", required=True)], root="contact"),
    ], "#188bf6", "users", "Sales", "Contacts in HighLevel", headers={"Version": "2021-07-28"}),
    node("n8n-nodes-base.medium", "Medium", "mediumApi", "https://api.medium.com/v1", ["bearer:accessToken"], [
        T("post", "create", "Create", "POST", "/users/{authorId}/posts", [F("authorId", "Author ID", "path", required=True), F("title", "Title", required=True),
                                                                       F("contentFormat", "Format", kind="options", options=["markdown", "html"]), F("content", "Content", rows=6, required=True),
                                                                       F("publishStatus", "Status", kind="options", options=["draft", "public", "unlisted"])], root="data"),
        T("user", "me", "Get Me", "GET", "/me", [], root="data"),
    ], "#000000", "file", "Marketing", "Publish posts on Medium"),
    node("n8n-nodes-base.wise", "Wise", "wiseApi", "https://api.transferwise.com", ["bearer:apiToken"], [
        T("profile", "getAll", "Get Many", "GET", "/v2/profiles", []),
        T("exchangeRate", "get", "Get", "GET", "/v1/rates", [F("source", "Source Currency", "query", required=True, default="EUR"), F("target", "Target Currency", "query", required=True, default="USD")]),
    ], "#9fe870", "chart", "Finance", "Profiles and exchange rates from Wise (read-only)"),
    node("n8n-nodes-base.onfleet", "Onfleet", "onfleetApi", "https://onfleet.com/api/v2", ["basic:apiKey:="], [
        T("task", "create", "Create", "POST", "/tasks", [N("address", "Address", required=True), N("name", "Recipient Name"), N("phone", "Recipient Phone"), N("notes", "Notes")],
          template={"destination": {"address": {"unparsed": "{address}"}}, "recipients": [{"name": "{name}", "phone": "{phone}"}], "notes": "{notes}"}),
        T("task", "get", "Get", "GET", "/tasks/{taskId}", [F("taskId", "Task ID", "path", required=True)]),
        T("worker", "getAll", "Get Many", "GET", "/workers", []),
    ], "#e43a3a", "map", "Utility", "Deliveries and drivers in Onfleet"),
    node("n8n-nodes-base.peekalink", "Peekalink", "peekalinkApi", "https://api.peekalink.io", ["header:X-API-Key:apiKey"], [
        T("link", "preview", "Preview", "POST", "/", [F("link", "URL", required=True)]),
    ], "#7c3aed", "link", "Utility", "Link previews with Peekalink"),
    node("n8n-nodes-base.marketstack", "Marketstack", "marketstackApi", "https://api.marketstack.com/v1", ["query:access_key:apiKey"], [
        T("endOfDayData", "getAll", "Get Many", "GET", "/eod", [F("symbols", "Symbols", "query", required=True, placeholder="AAPL,MSFT"), F("date_from", "From", "query"), F("date_to", "To", "query")], root="data", limit="limit"),
        T("ticker", "get", "Get", "GET", "/tickers/{symbol}", [F("symbol", "Symbol", "path", required=True)]),
    ], "#0b6efd", "chart", "Finance", "Stock prices from Marketstack"),
    node("n8n-nodes-base.openThesaurus", "OpenThesaurus", "", "https://www.openthesaurus.de", [], [
        T("synonym", "get", "Get Synonyms", "GET", "/synonyme/search", [F("q", "Word", "query", required=True)], static_query={"format": "application/json"}, root="synsets"),
    ], "#4d8a3f", "book", "Utility", "German synonyms from OpenThesaurus"),
    node("n8n-nodes-base.oura", "Oura", "ouraApi", "https://api.ouraring.com/v2/usercollection", ["bearer:accessToken"], [
        T("profile", "get", "Get", "GET", "/personal_info", []),
        T("summary", "sleep", "Daily Sleep", "GET", "/daily_sleep", [F("start_date", "Start", "query"), F("end_date", "End", "query")], root="data"),
        T("summary", "activity", "Daily Activity", "GET", "/daily_activity", [F("start_date", "Start", "query"), F("end_date", "End", "query")], root="data"),
    ], "#111827", "chart", "Utility", "Sleep and activity data from Oura"),
]

CREDS += [
    with_fields(oauth_preset("salesforceOAuth2Api", "Salesforce OAuth2 API", "https://login.salesforce.com/services/oauth2/authorize", "https://login.salesforce.com/services/oauth2/token",
                             "api refresh_token", authentication="body", pkce=True),
                ("instanceUrl", "Instance URL", "", "https://yourcompany.my.salesforce.com")),
    with_fields(oauth_preset("zohoOAuth2Api", "Zoho OAuth2 API", "https://accounts.zoho.{dataCenter}/oauth/v2/auth", "https://accounts.zoho.{dataCenter}/oauth/v2/token",
                             "ZohoCRM.modules.ALL", authentication="body", extra="access_type=offline&prompt=consent"),
                ("dataCenter", "Data Center (domain)", "eu", "eu, com, in, com.au, jp")),
    with_fields(oauth_preset("xeroOAuth2Api", "Xero OAuth2 API", "https://login.xero.com/identity/connect/authorize", "https://identity.xero.com/connect/token",
                             "offline_access accounting.transactions accounting.contacts"),
                ("tenantId", "Tenant ID", "", "from https://api.xero.com/connections")),
    with_fields(oauth_preset("quickBooksOAuth2Api", "QuickBooks OAuth2 API", "https://appcenter.intuit.com/connect/oauth2", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
                             "com.intuit.quickbooks.accounting"),
                ("realmId", "Company ID (Realm ID)", "", ""), ("apiHost", "API Host", "quickbooks.api.intuit.com", "sandbox-quickbooks.api.intuit.com for sandbox companies")),
    simple("hunterApi", "Hunter API", ("apiKey", "API Key", True, "", "")),
    simple("brandfetchApi", "Brandfetch API", ("apiKey", "API Key", True, "", "")),
    simple("lemlistApi", "Lemlist API", ("apiKey", "API Key", True, "", "")),
    simple("customerIoApi", "Customer.io API", ("trackingSiteId", "Tracking Site ID", False, "", ""), ("trackingApiKey", "Tracking API Key", True, "", "")),
    simple("segmentApi", "Segment API", ("writekey", "Write Key", True, "", "")),
    simple("iterableApi", "Iterable API", ("apiKey", "API Key", True, "", "")),
    simple("getResponseApi", "GetResponse API", ("apiKey", "API Key", True, "", "")),
    simple("pushbulletOAuth2Api", "Pushbullet API", ("accessToken", "Access Token", True, "", "")),
    simple("pushcutApi", "Pushcut API", ("apiKey", "API Key", True, "", "")),
    simple("signl4Api", "SIGNL4 Webhook", ("teamSecret", "Team Secret", True, "", "")),
    simple("yourlsApi", "Yourls API", ("url", "URL", False, "", "https://sho.rt"), ("signature", "Signature Token", True, "", "")),
    simple("storyblokManagementApi", "Storyblok Management API", ("accessToken", "Personal Access Token", True, "", "")),
    simple("travisCiApi", "Travis CI API", ("apiToken", "API Token", True, "", "")),
    simple("rundeckApi", "Rundeck API", ("url", "URL", False, "", "https://rundeck.example.com"), ("token", "Token", True, "", "")),
    simple("splunkApi", "Splunk API", ("baseUrl", "Base URL", False, "", "https://splunk.example.com:8089"), ("authToken", "Auth Token", True, "", "")),
    simple("theHiveProjectApi", "TheHive 5 API", ("url", "URL", False, "", "https://thehive.example.com"), ("apiKey", "API Key", True, "", "")),
    simple("mispApi", "MISP API", ("baseUrl", "Base URL", False, "", "https://misp.example.com"), ("apiKey", "API Key", True, "", "")),
    simple("gongApi", "Gong API", ("accessKey", "Access Key", False, "", ""), ("accessKeySecret", "Access Key Secret", True, "", "")),
    simple("highLevelApi", "HighLevel API", ("apiKey", "Private Integration Token", True, "", "")),
    simple("mediumApi", "Medium API", ("accessToken", "Integration Token", True, "", "")),
    simple("wiseApi", "Wise API", ("apiToken", "API Token", True, "", "")),
    simple("onfleetApi", "Onfleet API", ("apiKey", "API Key", True, "", "")),
    simple("peekalinkApi", "Peekalink API", ("apiKey", "API Key", True, "", "")),
    simple("marketstackApi", "Marketstack API", ("apiKey", "Access Key", True, "", "")),
    simple("ouraApi", "Oura API", ("accessToken", "Personal Access Token", True, "", "")),
]

# ---------------------------------------------------------------------------
# Batch 8: AWS (Signature V4), support desks, forms, messaging, commerce
# ---------------------------------------------------------------------------
def aws(service_host, target_prefix=None, json_version="1.1"):
    """Base URL and headers for an AWS API; JSON-protocol APIs pick the action via X-Amz-Target."""
    return f"https://{service_host}.{{cred.region}}.amazonaws.com"


def amz(action, version="1.1"):
    return {"X-Amz-Target": action, "Content-Type": f"application/x-amz-json-{version}"}


S3OBJ = (N("bucket", "S3 Bucket", required=True), N("key", "S3 Object Key", required=True))

NODES += [
    node("n8n-nodes-base.awsLambda", "AWS Lambda", "aws", aws("lambda"), ["aws:lambda"], [
        T("function", "invoke", "Invoke", "POST", "/2015-03-31/functions/{function}/invocations", [F("function", "Function Name or ARN", "path", required=True), F("data", "Payload (JSON)", "none", kind="json", rows=5, default="{}")],
          body_from="jsonParam"),
        T("function", "getAll", "Get Many", "GET", "/2015-03-31/functions/", [], root="Functions"),
    ], "#ff9900", "zap", "Developer", "Invoke AWS Lambda functions"),
    node("n8n-nodes-base.awsSns", "AWS SNS", "aws", aws("sns"), ["aws:sns"], [
        T("topic", "publish", "Publish", "POST", "/", [F("topicArn", "Topic ARN", api="TopicArn", required=True), F("subject", "Subject", api="Subject"), F("message", "Message", api="Message", rows=3, required=True)],
          static={"Action": "Publish", "Version": "2010-03-31"}),
    ], "#ff4f8b", "bell", "Communication", "Publish messages to AWS SNS topics", body_type="form", headers={"Accept": "application/json"}),
    node("n8n-nodes-base.awsSqs", "AWS SQS", "aws", aws("sqs"), ["aws:sqs"], [
        T("queue", "sendMessage", "Send Message", "POST", "/", [F("queueUrl", "Queue URL", api="QueueUrl", required=True), F("messageBody", "Message", api="MessageBody", rows=3, required=True)],
          extra=[F("delaySeconds", "Delay (seconds)", api="DelaySeconds", kind="number"), F("messageGroupId", "Message Group ID (FIFO)", api="MessageGroupId")], headers=amz("AmazonSQS.SendMessage", "1.0")),
        T("queue", "receiveMessages", "Receive Messages", "POST", "/", [F("queueUrl", "Queue URL", api="QueueUrl", required=True), F("max", "Max Messages", api="MaxNumberOfMessages", kind="number", default=10)],
          headers=amz("AmazonSQS.ReceiveMessage", "1.0"), root="Messages"),
    ], "#ff4f8b", "list", "Developer", "Send and receive AWS SQS messages"),
    node("n8n-nodes-base.awsSes", "AWS SES", "aws", aws("email"), ["aws:ses"], [
        T("email", "send", "Send", "POST", "/v2/email/outbound-emails", [N("from", "From", required=True), N("to", "To", required=True), N("subject", "Subject", required=True), N("text", "Text", rows=4), N("html", "HTML", rows=4)],
          template={"FromEmailAddress": "{from}", "Destination": {"ToAddresses": ["{to}"]},
                    "Content": {"Simple": {"Subject": {"Data": "{subject}"}, "Body": {"Text": {"Data": "{text}"}, "Html": {"Data": "{html}"}}}}}),
    ], "#dd344c", "mail", "Communication", "Send email with AWS SES (v2 API)"),
    node("n8n-nodes-base.awsDynamoDb", "AWS DynamoDB", "aws", aws("dynamodb"), ["aws:dynamodb"], [
        T("item", "upsert", "Create or Update", "POST", "/", [N("table", "Table", required=True), N("item", "Item (DynamoDB JSON)", kind="json", rows=5, default='{"pk": {"S": "1"}}')],
          template={"TableName": "{table}", "Item": "{item}"}, headers=amz("DynamoDB_20120810.PutItem", "1.0")),
        T("item", "get", "Get", "POST", "/", [N("table", "Table", required=True), N("key", "Key (DynamoDB JSON)", kind="json", default='{"pk": {"S": "1"}}')],
          template={"TableName": "{table}", "Key": "{key}"}, headers=amz("DynamoDB_20120810.GetItem", "1.0"), root="Item"),
        T("item", "getAll", "Scan", "POST", "/", [N("table", "Table", required=True)], template={"TableName": "{table}"}, headers=amz("DynamoDB_20120810.Scan", "1.0"), root="Items"),
    ], "#4053d6", "table", "Data & Storage", "Items in AWS DynamoDB"),
    node("n8n-nodes-base.awsComprehend", "AWS Comprehend", "aws", aws("comprehend"), ["aws:comprehend"], [
        T("text", "detectSentiment", "Detect Sentiment", "POST", "/", [F("text", "Text", api="Text", rows=3, required=True), F("languageCode", "Language", api="LanguageCode", default="en")],
          headers=amz("Comprehend_20171127.DetectSentiment")),
        T("text", "detectDominantLanguage", "Detect Language", "POST", "/", [F("text", "Text", api="Text", rows=3, required=True)], headers=amz("Comprehend_20171127.DetectDominantLanguage"), root="Languages"),
        T("text", "detectEntities", "Detect Entities", "POST", "/", [F("text", "Text", api="Text", rows=3, required=True), F("languageCode", "Language", api="LanguageCode", default="en")],
          headers=amz("Comprehend_20171127.DetectEntities"), root="Entities"),
    ], "#01a88d", "sparkles", "AI", "Sentiment, language and entities with AWS Comprehend"),
    node("n8n-nodes-base.awsRekognition", "AWS Rekognition", "aws", aws("rekognition"), ["aws:rekognition"], [
        T("image", "detectLabels", "Detect Labels", "POST", "/", [*S3OBJ, N("maxLabels", "Max Labels", kind="number", default=10)],
          template={"Image": {"S3Object": {"Bucket": "{bucket}", "Name": "{key}"}}, "MaxLabels": "{maxLabels}"}, headers=amz("RekognitionService.DetectLabels"), root="Labels"),
        T("image", "detectText", "Detect Text", "POST", "/", list(S3OBJ), template={"Image": {"S3Object": {"Bucket": "{bucket}", "Name": "{key}"}}},
          headers=amz("RekognitionService.DetectText"), root="TextDetections"),
    ], "#01a88d", "image", "AI", "Labels and text in images with AWS Rekognition"),
    node("n8n-nodes-base.awsTextract", "AWS Textract", "aws", aws("textract"), ["aws:textract"], [
        T("document", "analyzeExpense", "Analyze Receipt or Invoice", "POST", "/", list(S3OBJ), template={"Document": {"S3Object": {"Bucket": "{bucket}", "Name": "{key}"}}},
          headers=amz("Textract.AnalyzeExpense"), root="ExpenseDocuments"),
    ], "#01a88d", "file", "AI", "Extract data from receipts and invoices with AWS Textract"),
    node("n8n-nodes-base.awsTranscribe", "AWS Transcribe", "aws", aws("transcribe"), ["aws:transcribe"], [
        T("transcriptionJob", "create", "Create", "POST", "/", [N("name", "Job Name", required=True), N("mediaUri", "Media S3 URI", required=True, placeholder="s3://bucket/audio.mp3"), N("language", "Language", default="en-US")],
          template={"TranscriptionJobName": "{name}", "LanguageCode": "{language}", "Media": {"MediaFileUri": "{mediaUri}"}}, headers=amz("Transcribe.StartTranscriptionJob"), root="TranscriptionJob"),
        T("transcriptionJob", "get", "Get", "POST", "/", [N("name", "Job Name", required=True)], template={"TranscriptionJobName": "{name}"},
          headers=amz("Transcribe.GetTranscriptionJob"), root="TranscriptionJob"),
    ], "#01a88d", "mic", "AI", "Speech to text jobs with AWS Transcribe"),

    node("n8n-nodes-base.freshservice", "Freshservice", "freshserviceApi", "https://{cred.domain}.freshservice.com/api/v2", ["basic:apiKey:=X"], [
        T("ticket", "create", "Create", "POST", "/tickets", [F("subject", "Subject", required=True), F("description", "Description", rows=3, required=True), F("email", "Requester Email", required=True),
                                                       F("priority", "Priority", kind="options", options=[("Low", 1), ("Medium", 2), ("High", 3), ("Urgent", 4)]),
                                                       F("status", "Status", kind="options", options=[("Open", 2), ("Pending", 3), ("Resolved", 4), ("Closed", 5)])], root="ticket"),
        T("ticket", "get", "Get", "GET", "/tickets/{ticketId}", [F("ticketId", "Ticket ID", "path", required=True)], root="ticket"),
        T("agent", "getAll", "Get Many", "GET", "/agents", [], root="agents", limit="per_page"),
    ], "#2b77e8", "ticket", "Support", "Tickets and agents in Freshservice"),
    node("n8n-nodes-base.copper", "Copper", "copperApi", "https://api.copper.com/developer_api/v1", ["header:X-PW-AccessToken:apiKey", "header:X-PW-UserEmail:email"], [
        T("person", "create", "Create", "POST", "/people", [N("name", "Name", required=True), N("email", "Email")], template={"name": "{name}", "emails": [{"email": "{email}", "category": "work"}]}),
        T("company", "search", "Search", "POST", "/companies/search", [F("name", "Name")]),
    ], "#f26722", "users", "Sales", "People and companies in Copper CRM", headers={"X-PW-Application": "developer_api"}),
    node("n8n-nodes-base.agileCrm", "Agile CRM", "agileCrmApi", "https://{cred.subdomain}.agilecrm.com/dev/api", ["basic:email:apiKey"], [
        T("contact", "create", "Create", "POST", "/contacts", [N("email", "Email", required=True), N("firstName", "First Name"), N("lastName", "Last Name")],
          template={"properties": [{"type": "SYSTEM", "name": "email", "value": "{email}"}, {"type": "SYSTEM", "name": "first_name", "value": "{firstName}"},
                                   {"type": "SYSTEM", "name": "last_name", "value": "{lastName}"}]}),
        T("contact", "get", "Get", "GET", "/contacts/{contactId}", [F("contactId", "Contact ID", "path", required=True)]),
    ], "#ff6b00", "users", "Sales", "Contacts in Agile CRM", headers={"Accept": "application/json"}),
    node("n8n-nodes-base.monicaCrm", "Monica CRM", "monicaCrmApi", "{cred.domain}/api", ["bearer:apiToken"], [
        T("contact", "getAll", "Get Many", "GET", "/contacts", [F("query", "Search", "query")], root="data", limit="limit"),
        T("contact", "create", "Create", "POST", "/contacts", [N("firstName", "First Name", required=True), N("lastName", "Last Name"), N("genderId", "Gender ID", kind="number", default=3)],
          template={"first_name": "{firstName}", "last_name": "{lastName}", "gender_id": "{genderId}", "is_birthdate_known": False, "is_deceased": False, "is_deceased_date_known": False}, root="data"),
    ], "#325776", "users", "Productivity", "Contacts in Monica, the personal CRM"),
    node("n8n-nodes-base.koBoToolbox", "KoBoToolbox", "koBoToolboxApi", "{cred.URL}/api/v2", ["header:Authorization:Token {token}"], [
        T("form", "getAll", "Get Many", "GET", "/assets/", [], static_query={"q": "asset_type:survey", "format": "json"}, root="results"),
        T("submission", "getAll", "Get Many", "GET", "/assets/{formId}/data/", [F("formId", "Form ID", "path", required=True)], static_query={"format": "json"}, root="results", limit="limit"),
    ], "#2094f3", "form", "Utility", "Forms and submissions in KoBoToolbox"),
    node("n8n-nodes-base.formstack", "Formstack", "formstackApi", "https://www.formstack.com/api/v2", ["bearer:accessToken"], [
        T("form", "getAll", "Get Many", "GET", "/form.json", [], root="forms"),
        T("submission", "getAll", "Get Many", "GET", "/form/{formId}/submission.json", [F("formId", "Form ID", "path", required=True)], static_query={"data": "true"}, root="submissions", limit="per_page"),
    ], "#21b573", "form", "Utility", "Forms and submissions in Formstack"),
    node("n8n-nodes-base.wufoo", "Wufoo", "wufooApi", "https://{cred.subdomain}.wufoo.com/api/v3", ["basic:apiKey:=footastic"], [
        T("form", "getAll", "Get Many", "GET", "/forms.json", [], root="Forms"),
        T("entry", "getAll", "Get Many", "GET", "/forms/{formHash}/entries.json", [F("formHash", "Form Hash", "path", required=True)], root="Entries", limit="pageSize"),
    ], "#e15f25", "form", "Utility", "Forms and entries in Wufoo"),
    node("n8n-nodes-base.surveyMonkey", "SurveyMonkey", "surveyMonkeyApi", "https://api.surveymonkey.com/v3", ["bearer:accessToken"], [
        T("survey", "getAll", "Get Many", "GET", "/surveys", [], root="data", limit="per_page"),
        T("response", "getAll", "Get Many", "GET", "/surveys/{surveyId}/responses/bulk", [F("surveyId", "Survey ID", "path", required=True)], root="data", limit="per_page"),
    ], "#00bf6f", "form", "Utility", "Surveys and responses in SurveyMonkey"),
    node("n8n-nodes-base.tapfiliate", "Tapfiliate", "tapfiliateApi", "https://api.tapfiliate.com/1.6", ["header:X-Api-Key:apiKey"], [
        T("affiliate", "create", "Create", "POST", "/affiliates/", [F("firstname", "First Name", required=True), F("lastname", "Last Name", required=True), F("email", "Email", required=True)]),
        T("conversion", "create", "Create", "POST", "/conversions/", [F("external_id", "External ID", required=True), F("amount", "Amount", kind="number"), F("click_id", "Click ID")]),
        T("affiliate", "getAll", "Get Many", "GET", "/affiliates/", []),
    ], "#2196f3", "users", "Marketing", "Affiliates and conversions in Tapfiliate"),
    node("n8n-nodes-base.dropcontact", "Dropcontact", "dropcontactApi", "https://api.dropcontact.io", ["header:X-Access-Token:apiKey"], [
        T("contact", "enrich", "Enrich", "POST", "/batch", [N("email", "Email"), N("firstName", "First Name"), N("lastName", "Last Name"), N("company", "Company")],
          template={"data": [{"email": "{email}", "first_name": "{firstName}", "last_name": "{lastName}", "company": "{company}"}], "siren": False, "language": "en"}),
        T("contact", "fetchRequest", "Fetch Request", "GET", "/batch/{requestId}", [F("requestId", "Request ID", "path", required=True)]),
    ], "#0abf53", "search", "Sales", "Enrich contacts with Dropcontact"),
    node("n8n-nodes-base.messageBird", "MessageBird", "messageBirdApi", "https://rest.messagebird.com", ["header:Authorization:AccessKey {accessKey}"], [
        T("sms", "send", "Send", "POST", "/messages", [F("originator", "From", required=True), F("recipients", "To", kind="list", required=True), F("body", "Message", rows=3, required=True)]),
        T("balance", "get", "Get", "GET", "/balance", []),
    ], "#2481d7", "chat", "Communication", "Send SMS with MessageBird"),
    node("n8n-nodes-base.plivo", "Plivo", "plivoApi", "https://api.plivo.com/v1/Account/{cred.authId}", ["basic:authId:authToken"], [
        T("sms", "send", "Send", "POST", "/Message/", [F("src", "From", required=True), F("dst", "To", required=True), F("text", "Message", rows=3, required=True)]),
        T("call", "make", "Make Call", "POST", "/Call/", [F("from", "From", required=True), F("to", "To", required=True), F("answer_url", "Answer URL", required=True)]),
    ], "#43b02a", "phone", "Communication", "SMS and calls with Plivo"),
    node("n8n-nodes-base.ciscoWebex", "Webex by Cisco", "ciscoWebexApi", "https://webexapis.com/v1", ["bearer:accessToken"], [
        T("message", "create", "Create", "POST", "/messages", [F("roomId", "Space (Room) ID", required=True), F("markdown", "Message (Markdown)", rows=3, required=True)]),
        T("room", "getAll", "Get Many", "GET", "/rooms", [], root="items", limit="max"),
    ], "#00bceb", "chat", "Communication", "Messages and spaces in Webex"),
    node("n8n-nodes-base.erpNext", "ERPNext", "erpNextApi", "{cred.domain}/api", [], [
        T("document", "getAll", "Get Many", "GET", "/resource/{docType}", [F("docType", "DocType", "path", required=True, placeholder="Customer"), F("fields", "Fields (JSON array)", "query", default='["name"]')],
          root="data", limit="limit_page_length"),
        T("document", "get", "Get", "GET", "/resource/{docType}/{documentName}", [F("docType", "DocType", "path", required=True), F("documentName", "Name", "path", required=True)], root="data"),
        T("document", "create", "Create", "POST", "/resource/{docType}", [F("docType", "DocType", "path", required=True), F("data", "Fields (JSON)", "none", kind="json", rows=5)], body_from="jsonParam", root="data"),
    ], "#0089ff", "table", "Finance", "Documents in ERPNext / Frappe", headers={"Authorization": "token {cred.apiKey}:{cred.apiSecret}"}),
    node("n8n-nodes-base.magento2", "Magento 2", "magento2Api", "{cred.host}/rest/default/V1", ["bearer:accessToken"], [
        T("order", "getAll", "Get Many", "GET", "/orders", [F("searchCriteria[pageSize]", "Page Size", "query", default="20")], root="items"),
        T("product", "get", "Get", "GET", "/products/{sku}", [F("sku", "SKU", "path", required=True)]),
        T("customer", "getAll", "Get Many", "GET", "/customers/search", [F("searchCriteria[pageSize]", "Page Size", "query", default="20")], root="items"),
    ], "#ee672f", "cart", "Sales", "Orders, products and customers in Magento 2 / Adobe Commerce"),
    node("n8n-nodes-base.bubble", "Bubble", "bubbleApi", "https://{cred.appName}.bubbleapps.io/api/1.1/obj", ["bearer:apiToken"], [
        T("object", "get", "Get", "GET", "/{typeName}/{objectId}", [F("typeName", "Type", "path", required=True), F("objectId", "Object ID", "path", required=True)], root="response"),
        T("object", "getAll", "Get Many", "GET", "/{typeName}", [F("typeName", "Type", "path", required=True)], root="response.results", limit="limit"),
        T("object", "create", "Create", "POST", "/{typeName}", [F("typeName", "Type", "path", required=True), F("data", "Fields (JSON)", "none", kind="json", rows=5)], body_from="jsonParam"),
    ], "#0d0d0d", "table", "Data & Storage", "Things in a Bubble app (Data API)"),
    node("n8n-nodes-base.quickbase", "Quick Base", "quickbaseApi", "https://api.quickbase.com/v1", [], [
        T("record", "query", "Query", "POST", "/records/query", [F("tableId", "Table ID", api="from", required=True), F("where", "Where", placeholder="{6.EX.'open'}")], root="data"),
        T("record", "upsert", "Create or Update", "POST", "/records", [N("tableId", "Table ID", required=True), N("records", "Records (JSON array)", kind="json", rows=5, default='[{"6": {"value": "open"}}]')],
          template={"to": "{tableId}", "data": "{records}"}),
    ], "#74489d", "table", "Data & Storage", "Records in Quickbase", headers={"QB-Realm-Hostname": "{cred.hostname}", "Authorization": "QB-USER-TOKEN {cred.userToken}"}),
    node("n8n-nodes-base.phantombuster", "Phantombuster", "phantombusterApi", "https://api.phantombuster.com/api/v2", ["header:X-Phantombuster-Key-1:apiKey"], [
        T("agent", "launch", "Launch", "POST", "/agents/launch", [N("agentId", "Agent ID", required=True), N("argument", "Argument (JSON)", kind="json", default="{}")], template={"id": "{agentId}", "argument": "{argument}"}),
        T("agent", "getAll", "Get Many", "GET", "/agents/fetch-all", []),
        T("agent", "getOutput", "Get Output", "GET", "/agents/fetch-output", [F("id", "Agent ID", "query", required=True)]),
    ], "#1a1a1a", "zap", "Sales", "Launch Phantombuster agents"),
    node("n8n-nodes-base.sendy", "Sendy", "sendyApi", "{cred.url}", [], [
        T("subscriber", "add", "Add", "POST", "/subscribe", [N("list", "List ID", required=True), N("email", "Email", required=True), N("name", "Name")],
          template={"api_key": "{cred.apiKey}", "list": "{list}", "email": "{email}", "name": "{name}", "boolean": "true"}),
        T("subscriber", "delete", "Unsubscribe", "POST", "/unsubscribe", [N("list", "List ID", required=True), N("email", "Email", required=True)],
          template={"api_key": "{cred.apiKey}", "list": "{list}", "email": "{email}", "boolean": "true"}),
    ], "#2ab27b", "mail", "Marketing", "Subscribers in a self-hosted Sendy", body_type="form"),
    node("n8n-nodes-base.mocean", "Mocean", "moceanApi", "https://rest.moceanapi.com/rest/2", ["query:mocean-api-key:apiKey", "query:mocean-api-secret:apiSecret"], [
        T("sms", "send", "Send", "POST", "/sms", [F("from", "From", api="mocean-from", required=True), F("to", "To", api="mocean-to", required=True), F("text", "Message", api="mocean-text", rows=3, required=True)],
          static={"mocean-resp-format": "json"}),
    ], "#2f74c0", "chat", "Communication", "Send SMS with Mocean", body_type="form"),
    node("n8n-nodes-base.googlePerspective", "Google Perspective", "googlePerspectiveApi", "https://commentanalyzer.googleapis.com/v1alpha1", ["query:key:apiKey"], [
        T("text", "analyze", "Analyze Comment", "POST", "/comments:analyze", [N("text", "Text", rows=3, required=True), N("attribute", "Attribute", kind="options", options=["TOXICITY", "SEVERE_TOXICITY", "INSULT", "PROFANITY", "THREAT"]), N("language", "Language", default="en")],
          template={"comment": {"text": "{text}"}, "requestedAttributes": {"{attribute}": {}}, "languages": ["{language}"]}),
    ], "#4285f4", "shield", "AI", "Toxicity scores with Google Perspective"),
    node("n8n-nodes-base.disqus", "Disqus", "disqusApi", "https://disqus.com/api/3.0", ["query:api_key:accessToken"], [
        T("forum", "get", "Get", "GET", "/forums/details.json", [F("forum", "Forum", "query", required=True)], root="response"),
        T("forum", "getPosts", "Get Posts", "GET", "/forums/listPosts.json", [F("forum", "Forum", "query", required=True)], root="response", limit="limit"),
    ], "#2e9fff", "chat", "Marketing", "Forums and posts on Disqus"),
    node("n8n-nodes-base.securityScorecard", "SecurityScorecard", "securityScorecardApi", "https://api.securityscorecard.io", ["header:Authorization:Token {apiKey}"], [
        T("company", "getScorecard", "Get Scorecard", "GET", "/companies/{domain}", [F("domain", "Domain", "path", required=True)]),
        T("company", "getFactor", "Get Factors", "GET", "/companies/{domain}/factors", [F("domain", "Domain", "path", required=True)], root="entries"),
    ], "#00b0ff", "shield", "Security", "Security ratings from SecurityScorecard"),
    node("n8n-nodes-base.urlScanIo", "urlscan.io", "urlScanIoApi", "https://urlscan.io/api/v1", ["header:API-Key:apiKey"], [
        T("scan", "perform", "Perform", "POST", "/scan/", [F("url", "URL", required=True), F("visibility", "Visibility", kind="options", options=["public", "unlisted", "private"])]),
        T("scan", "get", "Get", "GET", "/result/{scanId}/", [F("scanId", "Scan ID", "path", required=True)]),
    ], "#f8862d", "search", "Security", "Scan URLs with urlscan.io"),
    node("n8n-nodes-base.strava", "Strava", "stravaOAuth2Api", "https://www.strava.com/api/v3", ["oauth2"], [
        T("activity", "getAll", "Get Many", "GET", "/athlete/activities", [], limit="per_page"),
        T("activity", "get", "Get", "GET", "/activities/{activityId}", [F("activityId", "Activity ID", "path", required=True)]),
        T("activity", "create", "Create", "POST", "/activities", [F("name", "Name", required=True), F("sport_type", "Sport", default="Run"), F("start_date_local", "Start (ISO)", required=True),
                                                            F("elapsed_time", "Duration (seconds)", kind="number", required=True)]),
    ], "#fc4c02", "map", "Utility", "Activities on Strava"),
    node("n8n-nodes-base.googleContacts", "Google Contacts", "googleContactsOAuth2Api", "https://people.googleapis.com/v1", ["oauth2"], [
        T("contact", "getAll", "Get Many", "GET", "/people/me/connections", [], static_query={"personFields": "names,emailAddresses,phoneNumbers"}, root="connections", limit="pageSize"),
        T("contact", "create", "Create", "POST", "/people:createContact", [N("givenName", "First Name", required=True), N("familyName", "Last Name"), N("email", "Email")],
          template={"names": [{"givenName": "{givenName}", "familyName": "{familyName}"}], "emailAddresses": [{"value": "{email}"}]}),
    ], "#1a73e8", "users", "Productivity", "Contacts in Google Contacts"),
]

CREDS += [
    {"name": "aws", "displayName": "AWS", "properties": [
        {"displayName": "Region", "name": "region", "type": "string", "default": "eu-central-1"},
        {"displayName": "Access Key ID", "name": "accessKeyId", "type": "string", "default": ""},
        {"displayName": "Secret Access Key", "name": "secretAccessKey", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Session Token (temporary credentials)", "name": "sessionToken", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Custom Endpoints", "name": "customEndpoints", "type": "boolean", "default": False},
        {"displayName": "S3 Endpoint", "name": "s3Endpoint", "type": "string", "default": "", "placeholder": "https://minio.example.com",
         "description": "S3-compatible storage (MinIO, R2, Wasabi); uses path-style URLs", "displayOptions": {"show": {"customEndpoints": [True]}}}]},
    simple("freshserviceApi", "Freshservice API", ("domain", "Domain", False, "", "acme (from acme.freshservice.com)"), ("apiKey", "API Key", True, "", "")),
    simple("copperApi", "Copper API", ("email", "Email", False, "", ""), ("apiKey", "API Key", True, "", "")),
    simple("agileCrmApi", "Agile CRM API", ("subdomain", "Subdomain", False, "", ""), ("email", "Email", False, "", ""), ("apiKey", "API Key", True, "", "")),
    simple("monicaCrmApi", "Monica CRM API", ("domain", "URL", False, "https://app.monicahq.com", ""), ("apiToken", "API Token", True, "", "")),
    simple("koBoToolboxApi", "KoBoToolbox API", ("URL", "URL", False, "https://kf.kobotoolbox.org", ""), ("token", "API Token", True, "", "")),
    simple("formstackApi", "Formstack API", ("accessToken", "Access Token", True, "", "")),
    simple("wufooApi", "Wufoo API", ("subdomain", "Subdomain", False, "", ""), ("apiKey", "API Key", True, "", "")),
    simple("surveyMonkeyApi", "SurveyMonkey API", ("accessToken", "Access Token", True, "", "")),
    simple("tapfiliateApi", "Tapfiliate API", ("apiKey", "API Key", True, "", "")),
    simple("dropcontactApi", "Dropcontact API", ("apiKey", "API Key", True, "", "")),
    simple("messageBirdApi", "MessageBird API", ("accessKey", "Access Key", True, "", "")),
    simple("plivoApi", "Plivo API", ("authId", "Auth ID", False, "", ""), ("authToken", "Auth Token", True, "", "")),
    simple("ciscoWebexApi", "Webex API", ("accessToken", "Access Token", True, "", "")),
    simple("erpNextApi", "ERPNext API", ("domain", "URL", False, "", "https://erp.example.com"), ("apiKey", "API Key", False, "", ""), ("apiSecret", "API Secret", True, "", "")),
    simple("magento2Api", "Magento 2 API", ("host", "Host", False, "", "https://shop.example.com"), ("accessToken", "Access Token", True, "", "")),
    simple("bubbleApi", "Bubble API", ("appName", "App Name", False, "", ""), ("apiToken", "API Token", True, "", "")),
    simple("quickbaseApi", "Quick Base API", ("hostname", "Realm Hostname", False, "", "acme.quickbase.com"), ("userToken", "User Token", True, "", "")),
    simple("phantombusterApi", "Phantombuster API", ("apiKey", "API Key", True, "", "")),
    simple("sendyApi", "Sendy API", ("url", "URL", False, "", "https://sendy.example.com"), ("apiKey", "API Key", True, "", "")),
    simple("moceanApi", "Mocean API", ("apiKey", "API Key", False, "", ""), ("apiSecret", "API Secret", True, "", "")),
    simple("googlePerspectiveApi", "Google Perspective API", ("apiKey", "API Key", True, "", "")),
    simple("disqusApi", "Disqus API", ("accessToken", "API Key", True, "", "")),
    simple("securityScorecardApi", "SecurityScorecard API", ("apiKey", "API Key", True, "", "")),
    simple("urlScanIoApi", "urlscan.io API", ("apiKey", "API Key", True, "", "")),
    oauth_preset("stravaOAuth2Api", "Strava OAuth2 API", "https://www.strava.com/oauth/authorize", "https://www.strava.com/oauth/token", "read,activity:read_all,activity:write", authentication="body"),
    google("googleContactsOAuth2Api", "Google Contacts OAuth2 API", "https://www.googleapis.com/auth/contacts"),
]

# ---------------------------------------------------------------------------
# Batch 9: Google Workspace & Cloud, Microsoft Graph, HR, billing, support, dev tools
# ---------------------------------------------------------------------------
GRAPH = "https://graph.microsoft.com/v1.0"


def client_credentials(name, display, token_url, scope="", extra_fields=()):
    """OAuth2 client-credentials preset (no browser consent, token fetched on demand)."""
    props = [
        {"displayName": "Client ID", "name": "clientId", "type": "string", "default": ""},
        {"displayName": "Client Secret", "name": "clientSecret", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Grant Type", "name": "grantType", "type": "hidden", "default": "clientCredentials"},
        {"displayName": "Access Token URL", "name": "accessTokenUrl", "type": "hidden", "default": token_url},
        {"displayName": "Scope", "name": "scope", "type": "hidden", "default": scope},
        {"displayName": "Authentication", "name": "authentication", "type": "hidden", "default": "body"},
    ]
    return {"name": name, "displayName": display, "properties": [{"displayName": d, "name": n, "type": "string", "default": dflt} for n, d, dflt in extra_fields] + props}


NODES += [
    node("n8n-nodes-base.googleSlides", "Google Slides", "googleSlidesOAuth2Api", "https://slides.googleapis.com/v1", ["oauth2"], [
        T("presentation", "create", "Create", "POST", "/presentations", [F("title", "Title", required=True)]),
        T("presentation", "get", "Get", "GET", "/presentations/{presentationId}", [F("presentationId", "Presentation ID", "path", required=True)]),
        T("page", "getThumbnail", "Get Thumbnail", "GET", "/presentations/{presentationId}/pages/{pageObjectId}/thumbnail",
          [F("presentationId", "Presentation ID", "path", required=True), F("pageObjectId", "Page ID", "path", required=True)]),
    ], "#f4b400", "file", "Productivity", "Presentations in Google Slides"),
    node("n8n-nodes-base.googleChat", "Google Chat", "googleChatOAuth2Api", "https://chat.googleapis.com/v1", ["oauth2"], [
        T("message", "create", "Create", "POST", "/{+spaceName}/messages", [F("spaceName", "Space", "path", required=True, placeholder="spaces/AAAA"), F("text", "Text", rows=3, required=True)]),
        T("space", "getAll", "Get Many", "GET", "/spaces", [], root="spaces", limit="pageSize"),
    ], "#00ac47", "chat", "Communication", "Messages and spaces in Google Chat"),
    node("n8n-nodes-base.googleCloudStorage", "Google Cloud Storage", "googleCloudStorageOAuth2Api", "https://storage.googleapis.com/storage/v1", ["oauth2"], [
        T("bucket", "getAll", "Get Many", "GET", "/b", [F("project", "Project ID", "query", required=True)], root="items", limit="maxResults"),
        T("object", "getAll", "Get Many", "GET", "/b/{bucket}/o", [F("bucket", "Bucket", "path", required=True), F("prefix", "Prefix", "query")], root="items", limit="maxResults"),
        T("object", "get", "Get Metadata", "GET", "/b/{bucket}/o/{object}", [F("bucket", "Bucket", "path", required=True), F("object", "Object Name", "path", required=True)]),
    ], "#4285f4", "file", "Data & Storage", "Buckets and objects in Google Cloud Storage"),
    node("n8n-nodes-base.googleAnalytics", "Google Analytics", "googleAnalyticsOAuth2", "https://analyticsdata.googleapis.com/v1beta", ["oauth2"], [
        T("report", "get", "Run Report (GA4)", "POST", "/properties/{propertyId}:runReport",
          [F("propertyId", "Property ID", "path", required=True), N("startDate", "Start", default="7daysAgo"), N("endDate", "End", default="today"),
           N("dimension", "Dimension", default="date"), N("metric", "Metric", default="activeUsers")],
          template={"dateRanges": [{"startDate": "{startDate}", "endDate": "{endDate}"}], "dimensions": [{"name": "{dimension}"}], "metrics": [{"name": "{metric}"}]}, root="rows"),
    ], "#e37400", "chart", "Analytics", "Reports from Google Analytics 4"),
    node("n8n-nodes-base.googleBooks", "Google Books", "googleBooksOAuth2Api", "https://www.googleapis.com/books/v1", ["oauth2"], [
        T("volume", "getAll", "Search", "GET", "/volumes", [F("q", "Query", "query", required=True)], root="items", limit="maxResults"),
        T("volume", "get", "Get", "GET", "/volumes/{volumeId}", [F("volumeId", "Volume ID", "path", required=True)]),
    ], "#4285f4", "book", "Utility", "Search Google Books"),
    node("n8n-nodes-base.googleCloudNaturalLanguage", "Google Cloud Natural Language", "googleCloudNaturalLanguageOAuth2Api", "https://language.googleapis.com/v1", ["oauth2"], [
        T("document", "analyzeSentiment", "Analyze Sentiment", "POST", "/documents:analyzeSentiment", [N("content", "Text", rows=3, required=True), N("language", "Language")],
          template={"document": {"type": "PLAIN_TEXT", "content": "{content}", "language": "{language}"}, "encodingType": "UTF8"}),
    ], "#4285f4", "sparkles", "AI", "Sentiment analysis with Google Cloud Natural Language"),
    node("n8n-nodes-base.googleFirebaseCloudFirestore", "Google Cloud Firestore", "googleFirebaseCloudFirestoreOAuth2Api", "https://firestore.googleapis.com/v1/projects/{cred.projectId}/databases/(default)/documents", ["oauth2"], [
        T("document", "get", "Get", "GET", "/{collection}/{documentId}", [F("collection", "Collection", "path", required=True), F("documentId", "Document ID", "path", required=True)]),
        T("document", "create", "Create", "POST", "/{collection}", [F("collection", "Collection", "path", required=True), F("data", "Document (Firestore JSON)", "none", kind="json", rows=5,
                                                                                                                           default='{"fields": {"name": {"stringValue": "Ada"}}}')], body_from="jsonParam"),
        T("document", "getAll", "Get Many", "GET", "/{collection}", [F("collection", "Collection", "path", required=True)], root="documents", limit="pageSize"),
    ], "#ffca28", "table", "Data & Storage", "Documents in Cloud Firestore"),
    node("n8n-nodes-base.gSuiteAdmin", "Google Workspace Admin", "gSuiteAdminOAuth2Api", "https://admin.googleapis.com/admin/directory/v1", ["oauth2"], [
        T("user", "getAll", "Get Many", "GET", "/users", [F("query", "Query", "query")], static_query={"customer": "my_customer"}, root="users", limit="maxResults"),
        T("user", "create", "Create", "POST", "/users", [N("email", "Primary Email", required=True), N("firstName", "First Name", required=True), N("lastName", "Last Name", required=True), N("password", "Password", required=True)],
          template={"primaryEmail": "{email}", "name": {"givenName": "{firstName}", "familyName": "{lastName}"}, "password": "{password}", "changePasswordAtNextLogin": True}),
        T("group", "getAll", "Get Many", "GET", "/groups", [], static_query={"customer": "my_customer"}, root="groups", limit="maxResults"),
    ], "#4285f4", "users", "Productivity", "Users and groups in Google Workspace"),
    node("n8n-nodes-base.microsoftExcel", "Microsoft Excel 365", "microsoftExcelOAuth2Api", GRAPH, ["oauth2"], [
        T("worksheet", "getAll", "Get Many", "GET", "/me/drive/items/{workbookId}/workbook/worksheets", [F("workbookId", "Workbook (Drive Item) ID", "path", required=True)], root="value"),
        T("table", "addRow", "Append Rows", "POST", "/me/drive/items/{workbookId}/workbook/tables/{table}/rows",
          [F("workbookId", "Workbook (Drive Item) ID", "path", required=True), F("table", "Table Name or ID", "path", required=True), N("values", "Rows (JSON array of arrays)", kind="json", default='[["Ada", 36]]')],
          template={"values": "{values}"}),
        T("table", "getRows", "Get Rows", "GET", "/me/drive/items/{workbookId}/workbook/tables/{table}/rows", [F("workbookId", "Workbook (Drive Item) ID", "path", required=True), F("table", "Table", "path", required=True)], root="value"),
    ], "#217346", "table", "Data & Storage", "Worksheets and tables in Excel on OneDrive / SharePoint"),
    node("n8n-nodes-base.microsoftEntra", "Microsoft Entra ID", "microsoftEntraOAuth2Api", GRAPH, ["oauth2"], [
        T("user", "getAll", "Get Many", "GET", "/users", [F("$filter", "Filter", "query", placeholder="startswith(displayName,'A')")], root="value", limit="$top"),
        T("user", "create", "Create", "POST", "/users", [N("displayName", "Display Name", required=True), N("mailNickname", "Mail Nickname", required=True), N("upn", "User Principal Name", required=True), N("password", "Password", required=True)],
          template={"accountEnabled": True, "displayName": "{displayName}", "mailNickname": "{mailNickname}", "userPrincipalName": "{upn}",
                    "passwordProfile": {"password": "{password}", "forceChangePasswordNextSignIn": True}}),
        T("group", "getAll", "Get Many", "GET", "/groups", [], root="value", limit="$top"),
    ], "#0078d4", "users", "Security", "Users and groups in Microsoft Entra ID"),
    node("n8n-nodes-base.microsoftSharePoint", "Microsoft SharePoint", "microsoftSharePointOAuth2Api", GRAPH, ["oauth2"], [
        T("item", "getAll", "Get Many", "GET", "/sites/{siteId}/lists/{listId}/items", [F("siteId", "Site ID", "path", required=True), F("listId", "List ID", "path", required=True)],
          static_query={"expand": "fields"}, root="value", limit="$top"),
        T("item", "create", "Create", "POST", "/sites/{siteId}/lists/{listId}/items", [F("siteId", "Site ID", "path", required=True), F("listId", "List ID", "path", required=True),
                                                                                      N("fields", "Fields (JSON)", kind="json", default='{"Title": "New"}')], template={"fields": "{fields}"}),
    ], "#038387", "table", "Productivity", "List items in SharePoint"),
    node("n8n-nodes-base.awsCognito", "AWS Cognito", "aws", "https://cognito-idp.{cred.region}.amazonaws.com", ["aws:cognito-idp"], [
        T("user", "getAll", "Get Many", "POST", "/", [F("userPoolId", "User Pool ID", api="UserPoolId", required=True), F("filter", "Filter", api="Filter", placeholder='email = "a@b.c"')],
          headers=amz("AWSCognitoIdentityProviderService.ListUsers"), root="Users"),
        T("user", "create", "Create", "POST", "/", [N("userPoolId", "User Pool ID", required=True), N("username", "Username", required=True), N("email", "Email")],
          template={"UserPoolId": "{userPoolId}", "Username": "{username}", "UserAttributes": [{"Name": "email", "Value": "{email}"}]},
          headers=amz("AWSCognitoIdentityProviderService.AdminCreateUser"), root="User"),
    ], "#dd344c", "users", "Security", "Users in AWS Cognito user pools"),
    node("n8n-nodes-base.awsCertificateManager", "AWS Certificate Manager", "aws", "https://acm.{cred.region}.amazonaws.com", ["aws:acm"], [
        T("certificate", "getAll", "Get Many", "POST", "/", [F("statuses", "Statuses", api="CertificateStatuses", kind="list", placeholder="ISSUED,EXPIRED")],
          headers=amz("CertificateManager.ListCertificates"), root="CertificateSummaryList"),
        T("certificate", "get", "Get", "POST", "/", [F("arn", "Certificate ARN", api="CertificateArn", required=True)], headers=amz("CertificateManager.DescribeCertificate"), root="Certificate"),
    ], "#dd344c", "shield", "Security", "TLS certificates in AWS Certificate Manager"),

    node("n8n-nodes-base.affinity", "Affinity", "affinityApi", "https://api.affinity.co", ["basic:=:apiKey"], [
        T("person", "create", "Create", "POST", "/persons", [F("first_name", "First Name", required=True), F("last_name", "Last Name", required=True), F("emails", "Emails", kind="list")]),
        T("organization", "getAll", "Get Many", "GET", "/organizations", [F("term", "Search", "query")], root="organizations", limit="page_size"),
    ], "#2a59ff", "users", "Sales", "People and organizations in Affinity"),
    node("n8n-nodes-base.apiTemplateIo", "APITemplate.io", "apiTemplateIoApi", "https://rest.apitemplate.io/v2", ["header:X-API-KEY:apiKey"], [
        T("image", "create", "Create", "POST", "/create-image", [F("template_id", "Template ID", "query", required=True), F("data", "Overrides (JSON)", "none", kind="json", rows=5, default='{"overrides": []}')],
          body_from="jsonParam"),
        T("pdf", "create", "Create", "POST", "/create-pdf", [F("template_id", "Template ID", "query", required=True), F("data", "Data (JSON)", "none", kind="json", rows=5)], body_from="jsonParam"),
        T("account", "get", "Get", "GET", "/account-information", []),
    ], "#4c6ef5", "image", "Marketing", "Generate images and PDFs with APITemplate.io"),
    node("n8n-nodes-base.beeminder", "Beeminder", "beeminderApi", "https://www.beeminder.com/api/v1", ["query:auth_token:authToken"], [
        T("datapoint", "create", "Create", "POST", "/users/me/goals/{goalName}/datapoints.json", [F("goalName", "Goal", "path", required=True), F("value", "Value", kind="number", required=True), F("comment", "Comment")]),
        T("datapoint", "getAll", "Get Many", "GET", "/users/me/goals/{goalName}/datapoints.json", [F("goalName", "Goal", "path", required=True)], limit="count"),
    ], "#ffc800", "chart", "Productivity", "Datapoints for Beeminder goals"),
    node("n8n-nodes-base.bambooHr", "BambooHR", "bambooHrApi", "https://api.bamboohr.com/api/gateway.php/{cred.subdomain}/v1", ["basic:apiKey:=x"], [
        T("employee", "getAll", "Get Many", "GET", "/employees/directory", [], root="employees"),
        T("employee", "get", "Get", "GET", "/employees/{employeeId}", [F("employeeId", "Employee ID", "path", required=True), F("fields", "Fields", "query", default="firstName,lastName,workEmail,jobTitle")]),
        T("timeOff", "getAll", "Who's Out", "GET", "/time_off/whos_out", [F("start", "Start", "query"), F("end", "End", "query")]),
    ], "#73c41d", "users", "Productivity", "Employees and time off in BambooHR", headers={"Accept": "application/json"}),
    node("n8n-nodes-base.bitwarden", "Bitwarden", "bitwardenApi", "https://api.bitwarden.com/public", ["oauth2"], [
        T("member", "getAll", "Get Many", "GET", "/members", [], root="data"),
        T("collection", "getAll", "Get Many", "GET", "/collections", [], root="data"),
        T("group", "getAll", "Get Many", "GET", "/groups", [], root="data"),
    ], "#175ddc", "shield", "Security", "Members, groups and collections of a Bitwarden organization"),
    node("n8n-nodes-base.chargebee", "Chargebee", "chargebeeApi", "https://{cred.accountName}.chargebee.com/api/v2", ["basic:apiKey:="], [
        T("customer", "create", "Create", "POST", "/customers", [F("first_name", "First Name"), F("last_name", "Last Name"), F("email", "Email", required=True)], root="customer"),
        T("invoice", "getAll", "Get Many", "GET", "/invoices", [], root="list", limit="limit"),
        T("subscription", "cancel", "Cancel", "POST", "/subscriptions/{subscriptionId}/cancel_for_items", [F("subscriptionId", "Subscription ID", "path", required=True),
                                                                                                         F("end_of_term", "At End of Term", kind="boolean")], root="subscription"),
    ], "#ff7846", "cart", "Finance", "Customers, invoices and subscriptions in Chargebee", body_type="form"),
    node("n8n-nodes-base.cockpit", "Cockpit", "cockpitApi", "{cred.url}/api", ["query:token:accessToken"], [
        T("collection", "getEntries", "Get Entries", "POST", "/collections/get/{collection}", [F("collection", "Collection", "path", required=True), N("filter", "Filter (JSON)", kind="json", default="{}")],
          template={"filter": "{filter}"}, root="entries"),
        T("collection", "save", "Save Entry", "POST", "/collections/save/{collection}", [F("collection", "Collection", "path", required=True), N("entry", "Entry (JSON)", kind="json", default="{}")], template={"data": "{entry}"}),
    ], "#3c56f3", "file", "Marketing", "Collections in Cockpit CMS"),
    node("n8n-nodes-base.demio", "Demio", "demioApi", "https://my.demio.com/api/v1", ["header:Api-Key:apiKey", "header:Api-Secret:apiSecret"], [
        T("event", "getAll", "Get Many", "GET", "/events", [F("type", "Type", "query", kind="options", options=["upcoming", "past", "automated"])]),
        T("event", "register", "Register", "PUT", "/event/register", [F("id", "Event ID", required=True), F("name", "Name", required=True), F("email", "Email", required=True)]),
    ], "#2bb673", "play", "Marketing", "Webinars and registrations in Demio"),
    node("n8n-nodes-base.dhl", "DHL", "dhlApi", "https://api-eu.dhl.com", ["header:DHL-API-Key:apiKey"], [
        T("shipment", "get", "Track", "GET", "/track/shipments", [F("trackingNumber", "Tracking Number", "query", required=True), F("language", "Language", "query", default="de")], root="shipments"),
    ], "#ffcc00", "map", "Utility", "Track DHL shipments"),
    node("n8n-nodes-base.drift", "Drift", "driftApi", "https://driftapi.com", ["bearer:accessToken"], [
        T("contact", "create", "Create", "POST", "/contacts", [N("email", "Email", required=True), N("name", "Name")], template={"attributes": {"email": "{email}", "name": "{name}"}}, root="data"),
        T("contact", "get", "Get", "GET", "/contacts/{contactId}", [F("contactId", "Contact ID", "path", required=True)], root="data"),
    ], "#2f80ed", "chat", "Sales", "Contacts in Drift"),
    node("n8n-nodes-base.freshworksCrm", "Freshworks CRM", "freshworksCrmApi", "https://{cred.domain}.myfreshworks.com/crm/sales/api", ["header:Authorization:Token token={apiKey}"], [
        T("contact", "create", "Create", "POST", "/contacts", [N("firstName", "First Name"), N("lastName", "Last Name", required=True), N("email", "Email")],
          template={"contact": {"first_name": "{firstName}", "last_name": "{lastName}", "emails": "{email}"}}, root="contact"),
        T("contact", "get", "Get", "GET", "/contacts/{contactId}", [F("contactId", "Contact ID", "path", required=True)], root="contact"),
    ], "#ff5f3a", "users", "Sales", "Contacts in Freshworks CRM (Freshsales)"),
    node("n8n-nodes-base.goToWebinar", "GoTo Webinar", "goToWebinarOAuth2Api", "https://api.getgo.com/G2W/rest/v2/organizers/{cred.organizerKey}", ["oauth2"], [
        T("webinar", "getAll", "Get Many", "GET", "/webinars", [F("fromTime", "From (ISO)", "query", required=True), F("toTime", "To (ISO)", "query", required=True)], root="_embedded.webinars"),
        T("registrant", "create", "Create", "POST", "/webinars/{webinarKey}/registrants", [F("webinarKey", "Webinar Key", "path", required=True), F("firstName", "First Name", required=True),
                                                                                            F("lastName", "Last Name", required=True), F("email", "Email", required=True)]),
    ], "#1b5be6", "play", "Marketing", "Webinars and registrants in GoTo Webinar"),
    node("n8n-nodes-base.helpScout", "Help Scout", "helpScoutOAuth2Api", "https://api.helpscout.net/v2", ["oauth2"], [
        T("conversation", "getAll", "Get Many", "GET", "/conversations", [F("status", "Status", "query", kind="options", options=["active", "pending", "closed", "all"])], root="_embedded.conversations"),
        T("customer", "create", "Create", "POST", "/customers", [N("firstName", "First Name", required=True), N("lastName", "Last Name"), N("email", "Email")],
          template={"firstName": "{firstName}", "lastName": "{lastName}", "emails": [{"type": "work", "value": "{email}"}]}),
    ], "#1292ee", "ticket", "Support", "Conversations and customers in Help Scout"),
    node("n8n-nodes-base.invoiceNinja", "Invoice Ninja", "invoiceNinjaApi", "{cred.url}/api/v1", ["header:X-API-TOKEN:apiToken"], [
        T("client", "create", "Create", "POST", "/clients", [N("name", "Name", required=True), N("email", "Contact Email")], template={"name": "{name}", "contacts": [{"email": "{email}"}]}, root="data"),
        T("invoice", "getAll", "Get Many", "GET", "/invoices", [F("status", "Status", "query")], root="data", limit="per_page"),
    ], "#000000", "cart", "Finance", "Clients and invoices in Invoice Ninja (v5)", headers={"X-Requested-With": "XMLHttpRequest"}),
    node("n8n-nodes-base.keap", "Keap", "keapOAuth2Api", "https://api.infusionsoft.com/crm/rest/v1", ["oauth2"], [
        T("contact", "create", "Create", "POST", "/contacts", [N("givenName", "First Name"), N("familyName", "Last Name"), N("email", "Email", required=True)],
          template={"given_name": "{givenName}", "family_name": "{familyName}", "email_addresses": [{"email": "{email}", "field": "EMAIL1"}]}),
        T("contact", "getAll", "Get Many", "GET", "/contacts", [F("email", "Email", "query")], root="contacts", limit="limit"),
    ], "#36a635", "users", "Sales", "Contacts in Keap (Infusionsoft)"),
    node("n8n-nodes-base.mailcheck", "Mailcheck", "mailcheckApi", "https://api.mailcheck.co/v1", ["bearer:apiKey"], [
        T("email", "check", "Check", "POST", "/singleEmail:check", [F("email", "Email", required=True)]),
    ], "#00c48c", "mail", "Utility", "Validate email addresses with Mailcheck"),
    node("n8n-nodes-base.mandrill", "Mandrill", "mandrillApi", "https://mandrillapp.com/api/1.0", [], [
        T("message", "send", "Send", "POST", "/messages/send", [N("fromEmail", "From", required=True), N("toEmail", "To", required=True), N("subject", "Subject", required=True), N("html", "HTML", rows=4), N("text", "Text", rows=4)],
          template={"key": "{cred.apiKey}", "message": {"from_email": "{fromEmail}", "to": [{"email": "{toEmail}"}], "subject": "{subject}", "html": "{html}", "text": "{text}"}}),
        T("message", "sendTemplate", "Send Template", "POST", "/messages/send-template", [N("template", "Template", required=True), N("toEmail", "To", required=True)],
          template={"key": "{cred.apiKey}", "template_name": "{template}", "template_content": [], "message": {"to": [{"email": "{toEmail}"}]}}),
    ], "#c02539", "mail", "Communication", "Transactional email with Mandrill"),
    node("n8n-nodes-base.nasa", "NASA", "nasaApi", "https://api.nasa.gov", ["query:api_key:apiKey"], [
        T("astronomyPictureOfTheDay", "get", "Get", "GET", "/planetary/apod", [F("date", "Date", "query", placeholder="2026-11-07")]),
        T("asteroidNeoFeed", "get", "Get", "GET", "/neo/rest/v1/feed", [F("start_date", "Start", "query"), F("end_date", "End", "query")]),
    ], "#0b3d91", "sparkles", "Utility", "Data from NASA APIs"),
    node("n8n-nodes-base.npm", "npm", "npmApi", "{cred.registryUrl}", ["bearer:accessToken"], [
        T("package", "getMetadata", "Get Metadata", "GET", "/{packageName}", [F("packageName", "Package", "path", required=True)]),
        T("package", "search", "Search", "GET", "/-/v1/search", [F("text", "Query", "query", required=True)], root="objects", limit="size"),
    ], "#cb3837", "code", "Developer", "Packages in the npm registry"),
    node("n8n-nodes-base.okta", "Okta", "oktaApi", "{cred.url}/api/v1", ["header:Authorization:SSWS {apiToken}"], [
        T("user", "getAll", "Get Many", "GET", "/users", [F("q", "Search", "query")], limit="limit"),
        T("user", "create", "Create", "POST", "/users", [N("firstName", "First Name", required=True), N("lastName", "Last Name", required=True), N("email", "Email", required=True)],
          template={"profile": {"firstName": "{firstName}", "lastName": "{lastName}", "email": "{email}", "login": "{email}"}}),
        T("user", "deactivate", "Deactivate", "POST", "/users/{userId}/lifecycle/deactivate", [F("userId", "User ID", "path", required=True)]),
    ], "#007dc1", "users", "Security", "Users in Okta"),
    node("n8n-nodes-base.payPal", "PayPal", "payPalApi", "https://{cred.apiHost}", ["oauth2"], [
        T("payout", "create", "Create", "POST", "/v1/payments/payouts", [N("batchId", "Sender Batch ID", required=True), N("receiver", "Receiver Email", required=True), N("amount", "Amount", required=True),
                                                                         N("currency", "Currency", default="EUR"), N("note", "Note")],
          template={"sender_batch_header": {"sender_batch_id": "{batchId}"},
                    "items": [{"recipient_type": "EMAIL", "receiver": "{receiver}", "amount": {"value": "{amount}", "currency": "{currency}"}, "note": "{note}"}]}),
        T("payout", "get", "Get", "GET", "/v1/payments/payouts/{payoutBatchId}", [F("payoutBatchId", "Payout Batch ID", "path", required=True)]),
    ], "#003087", "cart", "Finance", "Payouts with PayPal"),
    node("n8n-nodes-base.postBin", "PostBin", "", "https://www.toptal.com/developers/postbin/api", [], [
        T("bin", "create", "Create", "POST", "/bin", []),
        T("bin", "get", "Get", "GET", "/bin/{binId}", [F("binId", "Bin ID", "path", required=True)]),
        T("request", "shift", "Remove First", "GET", "/bin/{binId}/req/shift", [F("binId", "Bin ID", "path", required=True)]),
    ], "#204ecf", "inbox", "Developer", "Temporary request bins on Toptal PostBin"),
    node("n8n-nodes-base.salesmate", "Salesmate", "salesmateApi", "https://{cred.url}/apis", [], [
        T("contact", "create", "Create", "POST", "/contact/v4", [F("firstName", "First Name"), F("lastName", "Last Name", required=True), F("email", "Email")], root="Data"),
        T("contact", "get", "Get", "GET", "/contact/v4/{contactId}", [F("contactId", "Contact ID", "path", required=True)], root="Data"),
    ], "#ff6a36", "users", "Sales", "Contacts in Salesmate", headers={"sessionToken": "{cred.sessionToken}", "x-linkname": "{cred.url}"}),
    node("n8n-nodes-base.stackby", "Stackby", "stackbyApi", "https://stackby.com/api/betav1", ["header:api-key:apiKey"], [
        T("row", "getAll", "Get Many", "GET", "/rowlist/{stackId}/{table}", [F("stackId", "Stack ID", "path", required=True), F("table", "Table", "path", required=True)]),
        T("row", "create", "Create", "POST", "/rowcreate/{stackId}/{table}", [F("stackId", "Stack ID", "path", required=True), F("table", "Table", "path", required=True), N("fields", "Fields (JSON)", kind="json", default="{}")],
          template={"records": [{"field": "{fields}"}]}),
    ], "#2d46c8", "table", "Data & Storage", "Rows in Stackby"),
    node("n8n-nodes-base.syncroMsp", "SyncroMSP", "syncroMspApi", "https://{cred.subdomain}.syncromsp.com/api/v1", ["bearer:apiKey"], [
        T("ticket", "create", "Create", "POST", "/tickets", [F("customer_id", "Customer ID", kind="number", required=True), F("subject", "Subject", required=True), F("problem_type", "Issue Type", default="Other")], root="ticket"),
        T("customer", "getAll", "Get Many", "GET", "/customers", [F("query", "Search", "query")], root="customers"),
    ], "#1e88e5", "ticket", "Support", "Tickets and customers in SyncroMSP"),
    node("n8n-nodes-base.twist", "Twist", "twistOAuth2Api", "https://api.twist.com/api/v3", ["oauth2"], [
        T("channel", "getAll", "Get Many", "GET", "/channels/get", [F("workspace_id", "Workspace ID", "query", required=True)]),
        T("comment", "create", "Create", "POST", "/comments/add", [F("thread_id", "Thread ID", kind="number", required=True), F("content", "Content", rows=3, required=True)]),
    ], "#2ba3d7", "chat", "Communication", "Channels and comments in Twist"),
    node("n8n-nodes-base.uplead", "Uplead", "upleadApi", "https://api.uplead.com/v2", ["header:Authorization:apiKey"], [
        T("person", "enrich", "Enrich", "GET", "/person-search", [F("email", "Email", "query", required=True)], root="data"),
        T("company", "enrich", "Enrich", "GET", "/company-search", [F("domain", "Domain", "query", required=True)], root="data"),
    ], "#2e7cf6", "search", "Sales", "Person and company data from Uplead"),
    node("n8n-nodes-base.uptimeRobot", "UptimeRobot", "uptimeRobotApi", "https://api.uptimerobot.com/v2", [], [
        T("monitor", "getAll", "Get Many", "POST", "/getMonitors", [], template={"api_key": "{cred.apiKey}", "format": "json"}, root="monitors"),
        T("monitor", "create", "Create", "POST", "/newMonitor", [N("friendlyName", "Name", required=True), N("url", "URL", required=True)],
          template={"api_key": "{cred.apiKey}", "format": "json", "type": "1", "friendly_name": "{friendlyName}", "url": "{url}"}),
    ], "#3bd671", "chart", "Developer", "Uptime monitors in UptimeRobot", body_type="form"),
    node("n8n-nodes-base.vero", "Vero", "veroApi", "https://api.getvero.com/api/v2", [], [
        T("user", "create", "Identify", "POST", "/users/track", [N("id", "User ID", required=True), N("email", "Email")], template={"auth_token": "{cred.authToken}", "id": "{id}", "email": "{email}"}),
        T("event", "track", "Track", "POST", "/events/track", [N("id", "User ID", required=True), N("eventName", "Event", required=True), N("data", "Data (JSON)", kind="json", default="{}")],
          template={"auth_token": "{cred.authToken}", "identity": {"id": "{id}"}, "event_name": "{eventName}", "data": "{data}"}),
    ], "#00b2ec", "mail", "Marketing", "Users and events in Vero"),
]

CREDS += [
    google("googleSlidesOAuth2Api", "Google Slides OAuth2 API", "https://www.googleapis.com/auth/presentations"),
    google("googleChatOAuth2Api", "Google Chat OAuth2 API", "https://www.googleapis.com/auth/chat.messages https://www.googleapis.com/auth/chat.spaces.readonly"),
    google("googleCloudStorageOAuth2Api", "Google Cloud Storage OAuth2 API", "https://www.googleapis.com/auth/devstorage.read_write"),
    google("googleAnalyticsOAuth2", "Google Analytics OAuth2 API", "https://www.googleapis.com/auth/analytics.readonly"),
    google("googleBooksOAuth2Api", "Google Books OAuth2 API", "https://www.googleapis.com/auth/books"),
    google("googleCloudNaturalLanguageOAuth2Api", "Google Cloud Natural Language OAuth2 API", "https://www.googleapis.com/auth/cloud-language"),
    with_fields(google("googleFirebaseCloudFirestoreOAuth2Api", "Google Firestore OAuth2 API", "https://www.googleapis.com/auth/datastore"), ("projectId", "Project ID", "", "")),
    google("gSuiteAdminOAuth2Api", "Google Workspace Admin OAuth2 API", "https://www.googleapis.com/auth/admin.directory.user https://www.googleapis.com/auth/admin.directory.group.readonly"),
    microsoft("microsoftExcelOAuth2Api", "Microsoft Excel OAuth2 API", "openid offline_access Files.ReadWrite"),
    microsoft("microsoftEntraOAuth2Api", "Microsoft Entra ID OAuth2 API", "openid offline_access User.ReadWrite.All Group.Read.All"),
    microsoft("microsoftSharePointOAuth2Api", "Microsoft SharePoint OAuth2 API", "openid offline_access Sites.ReadWrite.All"),
    simple("affinityApi", "Affinity API", ("apiKey", "API Key", True, "", "")),
    simple("apiTemplateIoApi", "APITemplate.io API", ("apiKey", "API Key", True, "", "")),
    simple("beeminderApi", "Beeminder API", ("authToken", "Auth Token", True, "", "")),
    simple("bambooHrApi", "BambooHR API", ("subdomain", "Subdomain", False, "", ""), ("apiKey", "API Key", True, "", "")),
    client_credentials("bitwardenApi", "Bitwarden API", "https://identity.bitwarden.com/connect/token", "api.organization"),
    simple("chargebeeApi", "Chargebee API", ("accountName", "Account Name", False, "", ""), ("apiKey", "API Key", True, "", "")),
    simple("cockpitApi", "Cockpit API", ("url", "URL", False, "", "https://cms.example.com"), ("accessToken", "Access Token", True, "", "")),
    simple("demioApi", "Demio API", ("apiKey", "API Key", False, "", ""), ("apiSecret", "API Secret", True, "", "")),
    simple("dhlApi", "DHL API", ("apiKey", "API Key", True, "", "")),
    simple("driftApi", "Drift API", ("accessToken", "Access Token", True, "", "")),
    simple("freshworksCrmApi", "Freshworks CRM API", ("domain", "Domain", False, "", "acme (from acme.myfreshworks.com)"), ("apiKey", "API Key", True, "", "")),
    with_fields(oauth_preset("goToWebinarOAuth2Api", "GoTo Webinar OAuth2 API", "https://authentication.logmeininc.com/oauth/authorize", "https://authentication.logmeininc.com/oauth/token", ""),
                ("organizerKey", "Organizer Key", "", "")),
    client_credentials("helpScoutOAuth2Api", "Help Scout OAuth2 API", "https://api.helpscout.net/v2/oauth2/token"),
    simple("invoiceNinjaApi", "Invoice Ninja API", ("url", "URL", False, "https://invoicing.co", ""), ("apiToken", "API Token", True, "", "")),
    oauth_preset("keapOAuth2Api", "Keap OAuth2 API", "https://accounts.infusionsoft.com/app/oauth/authorize", "https://api.infusionsoft.com/token", "full", authentication="body"),
    simple("mailcheckApi", "Mailcheck API", ("apiKey", "API Key", True, "", "")),
    simple("mandrillApi", "Mandrill API", ("apiKey", "API Key", True, "", "")),
    simple("nasaApi", "NASA API", ("apiKey", "API Key", True, "DEMO_KEY", "")),
    simple("npmApi", "npm API", ("registryUrl", "Registry URL", False, "https://registry.npmjs.org", ""), ("accessToken", "Access Token (optional)", True, "", "")),
    simple("oktaApi", "Okta API", ("url", "URL", False, "", "https://acme.okta.com"), ("apiToken", "API Token", True, "", "")),
    client_credentials("payPalApi", "PayPal API", "https://{apiHost}/v1/oauth2/token", extra_fields=(("apiHost", "API Host (api-m.sandbox.paypal.com for sandbox)", "api-m.paypal.com"),)),
    simple("salesmateApi", "Salesmate API", ("url", "Domain", False, "", "acme.salesmate.io"), ("sessionToken", "Session Token", True, "", "")),
    simple("stackbyApi", "Stackby API", ("apiKey", "API Key", True, "", "")),
    simple("syncroMspApi", "SyncroMSP API", ("subdomain", "Subdomain", False, "", ""), ("apiKey", "API Key", True, "", "")),
    oauth_preset("twistOAuth2Api", "Twist OAuth2 API", "https://twist.com/oauth/authorize", "https://twist.com/oauth/access_token",
                 "user:read,workspaces:read,channels:read,threads:read,comments:write", authentication="body"),
    simple("upleadApi", "Uplead API", ("apiKey", "API Key", True, "", "")),
    simple("uptimeRobotApi", "UptimeRobot API", ("apiKey", "API Key", True, "", "")),
    simple("veroApi", "Vero API", ("authToken", "Auth Token", True, "", "")),
]

# ---------------------------------------------------------------------------
# Batch 10: triggers that register their webhook at the service on activation
# (src/hooktriggers.rs runs `webhookTrigger`; ids live in the node's static data)
# ---------------------------------------------------------------------------
def P(name, display, kind="string", default="", options=None, required=False, placeholder="", description=""):
    p = {"displayName": display, "name": name, "type": kind, "default": default}
    if options:
        p["options"] = [{"name": o, "value": o} if isinstance(o, str) else {"name": o[0], "value": o[1]} for o in options]
    if required:
        p["required"] = True
    if placeholder:
        p["placeholder"] = placeholder
    if description:
        p["description"] = description
    return p


def EVENTS(options, default):
    return P("events", "Events", "multiOptions", default, options, required=True)


def trigger(type_name, display, cred, base, auth, props, create, delete, id_path, color, icon, desc, headers=None, body_type="json", output="body", ignore=None,
            signature=None, secret_path=None):
    """create/delete: (method, path, template or None)."""
    spec = {"credential": cred or "", "baseUrl": base, "auth": auth, "headers": headers or {}, "bodyType": body_type, "output": output,
            "create": {"method": create[0], "path": create[1], "template": create[2]},
            "delete": {"method": delete[0], "path": delete[1], "template": delete[2]}}
    if id_path:
        spec["idPath"] = id_path
    if ignore:
        spec["ignore"] = ignore
    if signature:
        spec["signature"] = signature
    if secret_path:
        spec["secretPath"] = secret_path
    return {"name": type_name, "displayName": display, "group": ["trigger"], "category": "Triggers", "icon": icon, "color": color, "version": 1,
            "defaults": {"name": display}, "description": desc, "inputs": 0, "outputs": [""],
            "credentials": [{"name": cred, "required": True}] if cred else [], "properties": props, "webhookTrigger": spec}


URL = "{webhookUrl}"
GH_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

NODES += [
    trigger("n8n-nodes-base.githubTrigger", "GitHub Trigger", "githubApi", "{cred.server}", ["bearer:accessToken"],
            [P("owner", "Repository Owner", required=True), P("repository", "Repository Name", required=True),
             EVENTS(["push", "pull_request", "issues", "issue_comment", "release", "star", "fork", "workflow_run", "create", "delete", "*"], ["push"])],
            ("POST", "/repos/{owner}/{repository}/hooks", {"name": "web", "active": True, "events": "{events}", "config": {"url": URL, "content_type": "json", "insecure_ssl": "0", "secret": "{secret}"}}),
            ("DELETE", "/repos/{owner}/{repository}/hooks/{webhookId}", None), "id", "#8b949e", "github", "Starts the workflow on GitHub repository events",
            headers=GH_HEADERS, ignore={"header": "x-github-event", "values": ["ping"]}, signature={"header": "x-hub-signature-256", "prefix": "sha256=", "encoding": "hex"}),
    trigger("n8n-nodes-base.gitlabTrigger", "GitLab Trigger", "gitlabApi", "{cred.server}/api/v4", ["header:PRIVATE-TOKEN:accessToken"],
            [P("project", "Project ID or Path", required=True, placeholder="group/repo"), P("pushEvents", "Push", "boolean", True), P("mergeRequestsEvents", "Merge Requests", "boolean", False),
             P("issuesEvents", "Issues", "boolean", False), P("tagPushEvents", "Tags", "boolean", False), P("pipelineEvents", "Pipelines", "boolean", False)],
            ("POST", "/projects/{project}/hooks", {"url": URL, "push_events": "{pushEvents}", "merge_requests_events": "{mergeRequestsEvents}", "issues_events": "{issuesEvents}",
                                                   "tag_push_events": "{tagPushEvents}", "pipeline_events": "{pipelineEvents}", "enable_ssl_verification": True, "token": "{secret}"}),
            ("DELETE", "/projects/{project}/hooks/{webhookId}", None), "id", "#fc6d26", "code", "Starts the workflow on GitLab project events",
            signature={"header": "x-gitlab-token", "mode": "equals"}),
    trigger("n8n-nodes-base.stripeTrigger", "Stripe Trigger", "stripeApi", "https://api.stripe.com/v1", ["bearer:secretKey"],
            [EVENTS(["charge.succeeded", "charge.failed", "checkout.session.completed", "customer.created", "customer.subscription.created", "customer.subscription.deleted",
                     "invoice.paid", "invoice.payment_failed", "payment_intent.succeeded", "*"], ["checkout.session.completed"])],
            ("POST", "/webhook_endpoints", {"url": URL, "enabled_events": "{events}", "description": "Flowwolf"}),
            ("DELETE", "/webhook_endpoints/{webhookId}", None), "id", "#635bff", "cart", "Starts the workflow on Stripe events", body_type="form",
            signature={"header": "stripe-signature", "mode": "timestamped"}, secret_path="secret"),
    trigger("n8n-nodes-base.shopifyTrigger", "Shopify Trigger", "shopifyAccessTokenApi", "https://{cred.shopSubdomain}.myshopify.com/admin/api/2024-10",
            ["header:X-Shopify-Access-Token:accessToken"],
            [P("topic", "Topic", "options", "orders/create", ["orders/create", "orders/paid", "orders/updated", "orders/cancelled", "products/create", "products/update",
                                                               "customers/create", "customers/update", "app/uninstalled"])],
            ("POST", "/webhooks.json", {"webhook": {"topic": "{topic}", "address": URL, "format": "json"}}),
            ("DELETE", "/webhooks/{webhookId}.json", None), "webhook.id", "#95bf47", "cart", "Starts the workflow on Shopify store events"),
    trigger("n8n-nodes-base.typeformTrigger", "Typeform Trigger", "typeformApi", "https://api.typeform.com", ["bearer:accessToken"],
            [P("formId", "Form ID", required=True)],
            ("PUT", "/forms/{formId}/webhooks/{nodeWebhookId}", {"url": URL, "enabled": True, "verify_ssl": True, "secret": "{secret}"}),
            ("DELETE", "/forms/{formId}/webhooks/{nodeWebhookId}", None), None, "#262627", "form", "Starts the workflow when a Typeform is submitted",
            signature={"header": "typeform-signature", "prefix": "sha256=", "encoding": "base64"}),
    trigger("n8n-nodes-base.calendlyTrigger", "Calendly Trigger", "calendlyApi", "https://api.calendly.com", ["bearer:accessToken"],
            [P("organization", "Organization URI", required=True, placeholder="https://api.calendly.com/organizations/XXXX"),
             EVENTS([("Invitee Created", "invitee.created"), ("Invitee Canceled", "invitee.canceled")], ["invitee.created"])],
            ("POST", "/webhook_subscriptions", {"url": URL, "events": "{events}", "organization": "{organization}", "scope": "organization", "signing_key": "{secret}"}),
            ("DELETE", "{webhookId}", None), "resource.uri", "#006bff", "calendar", "Starts the workflow on Calendly bookings",
            signature={"header": "calendly-webhook-signature", "mode": "timestamped"}),
    trigger("n8n-nodes-base.telegramTrigger", "Telegram Trigger", "telegramApi", "{cred.baseUrl}/bot{cred.accessToken}", [],
            [P("updates", "Updates", "multiOptions", ["message"], ["message", "edited_message", "channel_post", "callback_query", "inline_query", "*"])],
            ("POST", "/setWebhook", {"url": URL, "allowed_updates": "{updates}", "secret_token": "{secret}"}),
            ("POST", "/deleteWebhook", None), None, "#2aabee", "chat", "Starts the workflow on Telegram bot updates",
            signature={"header": "x-telegram-bot-api-secret-token", "mode": "equals"}),
    trigger("n8n-nodes-base.trelloTrigger", "Trello Trigger", "trelloApi", "https://api.trello.com/1", ["query:key:apiKey", "query:token:apiToken"],
            [P("modelId", "Model ID (board, list or card)", required=True)],
            ("POST", "/webhooks", {"callbackURL": URL, "idModel": "{modelId}", "description": "Flowwolf"}),
            ("DELETE", "/webhooks/{webhookId}", None), "id", "#0079bf", "board", "Starts the workflow on Trello board changes"),
    trigger("n8n-nodes-base.jiraTrigger", "Jira Trigger", "jiraSoftwareCloudApi", "{cred.domain}/rest/webhooks/1.0", ["basic:email:apiToken"],
            [EVENTS(["jira:issue_created", "jira:issue_updated", "jira:issue_deleted", "comment_created", "comment_updated", "*"], ["jira:issue_created"]),
             P("jql", "Filter (JQL)", placeholder="project = OPS")],
            ("POST", "/webhook", {"name": "Flowwolf", "url": URL, "events": "{events}", "filters": {"issue-related-events-section": "{jql}"}, "excludeBody": False}),
            ("DELETE", "{webhookId}", None), "self", "#0052cc", "ticket", "Starts the workflow on Jira issue events"),
    trigger("n8n-nodes-base.asanaTrigger", "Asana Trigger", "asanaApi", "https://app.asana.com/api/1.0", ["bearer:accessToken"],
            [P("resource", "Resource GID (project or task)", required=True)],
            ("POST", "/webhooks", {"data": {"resource": "{resource}", "target": URL}}),
            ("DELETE", "/webhooks/{webhookId}", None), "data.gid", "#f06a6a", "check", "Starts the workflow on Asana changes",
            signature={"header": "x-hook-signature", "encoding": "hex", "secretFrom": "handshake"}),
    trigger("n8n-nodes-base.clickUpTrigger", "ClickUp Trigger", "clickUpApi", "https://api.clickup.com/api/v2", ["header:Authorization:accessToken"],
            [P("team", "Workspace (Team) ID", required=True),
             EVENTS(["taskCreated", "taskUpdated", "taskDeleted", "taskStatusUpdated", "taskAssigneeUpdated", "taskCommentPosted", "*"], ["taskCreated"])],
            ("POST", "/team/{team}/webhook", {"endpoint": URL, "events": "{events}"}),
            ("DELETE", "/webhook/{webhookId}", None), "id", "#7b68ee", "check", "Starts the workflow on ClickUp task events",
            signature={"header": "x-signature", "encoding": "hex"}, secret_path="webhook.secret"),
    trigger("n8n-nodes-base.linearTrigger", "Linear Trigger", "linearApi", "https://api.linear.app", ["header:Authorization:apiKey"],
            [P("teamId", "Team ID", required=True), P("resources", "Resources", "multiOptions", ["Issue"], ["Issue", "Comment", "Project", "Cycle", "IssueLabel", "Reaction"])],
            ("POST", "/graphql", {"query": "mutation($input: WebhookCreateInput!) { webhookCreate(input: $input) { success webhook { id } } }",
                                  "variables": {"input": {"url": URL, "teamId": "{teamId}", "resourceTypes": "{resources}", "label": "Flowwolf", "secret": "{secret}"}}}),
            ("POST", "/graphql", {"query": "mutation($id: String!) { webhookDelete(id: $id) { success } }", "variables": {"id": "{webhookId}"}}),
            "data.webhookCreate.webhook.id", "#5e6ad2", "check", "Starts the workflow on Linear changes", signature={"header": "linear-signature", "encoding": "hex"}),
    trigger("n8n-nodes-base.mailchimpTrigger", "Mailchimp Trigger", "mailchimpApi", "https://{cred.server}.api.mailchimp.com/3.0", ["basic:=anystring:apiKey"],
            [P("list", "Audience (List) ID", required=True), P("subscribe", "Subscribe", "boolean", True), P("unsubscribe", "Unsubscribe", "boolean", True),
             P("profile", "Profile Updates", "boolean", False), P("cleaned", "Cleaned", "boolean", False), P("upemail", "Email Changed", "boolean", False), P("campaign", "Campaign Sent", "boolean", False)],
            ("POST", "/lists/{list}/webhooks", {"url": URL, "events": {"subscribe": "{subscribe}", "unsubscribe": "{unsubscribe}", "profile": "{profile}", "cleaned": "{cleaned}",
                                                                        "upemail": "{upemail}", "campaign": "{campaign}"}, "sources": {"user": True, "admin": True, "api": True}}),
            ("DELETE", "/lists/{list}/webhooks/{webhookId}", None), "id", "#ffe01b", "mail", "Starts the workflow on Mailchimp audience events"),
    trigger("n8n-nodes-base.pipedriveTrigger", "Pipedrive Trigger", "pipedriveApi", "https://api.pipedrive.com/v1", ["query:api_token:apiToken"],
            [P("action", "Action", "options", "*", ["*", "added", "updated", "deleted", "merged"]),
             P("object", "Object", "options", "*", ["*", "deal", "person", "organization", "activity", "note", "product"])],
            ("POST", "/webhooks", {"subscription_url": URL, "event_action": "{action}", "event_object": "{object}"}),
            ("DELETE", "/webhooks/{webhookId}", None), "data.id", "#1a1a1a", "users", "Starts the workflow on Pipedrive changes"),
    trigger("n8n-nodes-base.webflowTrigger", "Webflow Trigger", "webflowApi", "https://api.webflow.com/v2", ["bearer:accessToken"],
            [P("siteId", "Site ID", required=True),
             P("triggerType", "Event", "options", "form_submission", ["form_submission", "site_publish", "page_created", "ecomm_new_order", "collection_item_created", "collection_item_changed"])],
            ("POST", "/sites/{siteId}/webhooks", {"triggerType": "{triggerType}", "url": URL}),
            ("DELETE", "/webhooks/{webhookId}", None), "id", "#4353ff", "globe", "Starts the workflow on Webflow site events"),
    trigger("n8n-nodes-base.wooCommerceTrigger", "WooCommerce Trigger", "wooCommerceApi", "{cred.url}/wp-json/wc/v3", ["basic:consumerKey:consumerSecret"],
            [P("event", "Event", "options", "order.created", ["order.created", "order.updated", "order.deleted", "product.created", "product.updated", "customer.created", "coupon.created"])],
            ("POST", "/webhooks", {"name": "Flowwolf", "topic": "{event}", "delivery_url": URL, "status": "active", "secret": "{secret}"}),
            ("DELETE", "/webhooks/{webhookId}?force=true", None), "id", "#96588a", "cart", "Starts the workflow on WooCommerce store events", signature={"header": "x-wc-webhook-signature", "encoding": "base64"}),
    trigger("n8n-nodes-base.bitbucketTrigger", "Bitbucket Trigger", "bitbucketApi", "https://api.bitbucket.org/2.0", ["basic:username:appPassword"],
            [P("workspace", "Workspace", required=True), P("repository", "Repository", required=True),
             EVENTS(["repo:push", "pullrequest:created", "pullrequest:updated", "pullrequest:fulfilled", "issue:created"], ["repo:push"])],
            ("POST", "/repositories/{workspace}/{repository}/hooks", {"description": "Flowwolf", "url": URL, "active": True, "events": "{events}", "secret": "{secret}"}),
            ("DELETE", "/repositories/{workspace}/{repository}/hooks/{webhookId}", None), "uuid", "#2684ff", "code", "Starts the workflow on Bitbucket repository events", signature={"header": "x-hub-signature", "prefix": "sha256=", "encoding": "hex"}),
    trigger("n8n-nodes-base.netlifyTrigger", "Netlify Trigger", "netlifyApi", "https://api.netlify.com/api/v1", ["bearer:accessToken"],
            [P("siteId", "Site ID", required=True), P("event", "Event", "options", "deploy_created", ["deploy_building", "deploy_created", "deploy_failed", "submission_created"])],
            ("POST", "/hooks?site_id={siteId}", {"type": "url", "event": "{event}", "data": {"url": URL}}),
            ("DELETE", "/hooks/{webhookId}", None), "id", "#00ad9f", "globe", "Starts the workflow on Netlify deploys and form submissions"),
    trigger("n8n-nodes-base.boxTrigger", "Box Trigger", "boxOAuth2Api", "https://api.box.com/2.0", ["oauth2"],
            [P("targetId", "Target ID", required=True), P("targetType", "Target Type", "options", "folder", ["folder", "file"]),
             EVENTS(["FILE.UPLOADED", "FILE.DELETED", "FILE.MOVED", "FOLDER.CREATED", "COMMENT.CREATED"], ["FILE.UPLOADED"])],
            ("POST", "/webhooks", {"target": {"id": "{targetId}", "type": "{targetType}"}, "address": URL, "triggers": "{events}"}),
            ("DELETE", "/webhooks/{webhookId}", None), "id", "#0061d5", "file", "Starts the workflow on Box file events"),
    trigger("n8n-nodes-base.formstackTrigger", "Formstack Trigger", "formstackApi", "https://www.formstack.com/api/v2", ["bearer:accessToken"],
            [P("formId", "Form ID", required=True)],
            ("POST", "/form/{formId}/webhook.json", {"url": URL, "content_type": "json"}),
            ("DELETE", "/webhook/{webhookId}.json", None), "id", "#21b573", "form", "Starts the workflow on Formstack submissions"),
    trigger("n8n-nodes-base.surveyMonkeyTrigger", "SurveyMonkey Trigger", "surveyMonkeyApi", "https://api.surveymonkey.com/v3", ["bearer:accessToken"],
            [P("surveyId", "Survey ID", required=True), P("event", "Event", "options", "response_completed", ["response_completed", "response_created", "response_updated", "response_disqualified"])],
            ("POST", "/webhooks", {"name": "Flowwolf", "event_type": "{event}", "object_type": "survey", "object_ids": ["{surveyId}"], "subscription_url": URL}),
            ("DELETE", "/webhooks/{webhookId}", None), "id", "#00bf6f", "form", "Starts the workflow on SurveyMonkey responses"),
    trigger("n8n-nodes-base.postmarkTrigger", "Postmark Trigger", "postmarkApi", "https://api.postmarkapp.com", ["header:X-Postmark-Server-Token:serverToken"],
            [P("stream", "Message Stream", default="outbound"), P("open", "Open", "boolean", True), P("click", "Click", "boolean", False), P("delivery", "Delivery", "boolean", True),
             P("bounce", "Bounce", "boolean", True), P("spam", "Spam Complaint", "boolean", False)],
            ("POST", "/webhooks", {"Url": URL, "MessageStream": "{stream}", "Triggers": {"Open": {"Enabled": "{open}"}, "Click": {"Enabled": "{click}"}, "Delivery": {"Enabled": "{delivery}"},
                                                                                          "Bounce": {"Enabled": "{bounce}"}, "SpamComplaint": {"Enabled": "{spam}"}}}),
            ("DELETE", "/webhooks/{webhookId}", None), "ID", "#ffde00", "mail", "Starts the workflow on Postmark email events"),
    trigger("n8n-nodes-base.mailerLiteTrigger", "MailerLite Trigger", "mailerLiteApi", "https://connect.mailerlite.com/api", ["bearer:apiKey"],
            [EVENTS(["subscriber.created", "subscriber.updated", "subscriber.unsubscribed", "subscriber.bounced", "campaign.sent"], ["subscriber.created"])],
            ("POST", "/webhooks", {"name": "Flowwolf", "events": "{events}", "url": URL}),
            ("DELETE", "/webhooks/{webhookId}", None), "data.id", "#09c269", "mail", "Starts the workflow on MailerLite events", signature={"header": "signature", "encoding": "hex"}, secret_path="data.secret"),
    trigger("n8n-nodes-base.activeCampaignTrigger", "ActiveCampaign Trigger", "activeCampaignApi", "{cred.apiUrl}/api/3", ["header:Api-Token:apiKey"],
            [EVENTS(["subscribe", "unsubscribe", "update", "sent", "open", "click", "deal_add", "deal_update"], ["subscribe"]),
             P("sources", "Sources", "multiOptions", ["public", "admin", "api", "system"], ["public", "admin", "api", "system"])],
            ("POST", "/webhooks", {"webhook": {"name": "Flowwolf", "url": URL, "events": "{events}", "sources": "{sources}"}}),
            ("DELETE", "/webhooks/{webhookId}", None), "webhook.id", "#356ae6", "mail", "Starts the workflow on ActiveCampaign events"),
    trigger("n8n-nodes-base.getResponseTrigger", "GetResponse Trigger", "getResponseApi", "https://api.getresponse.com/v3", ["header:X-Auth-Token:api-key {apiKey}"],
            [P("subscribe", "Subscribe", "boolean", True), P("unsubscribe", "Unsubscribe", "boolean", True), P("open", "Open", "boolean", False), P("click", "Click", "boolean", False)],
            ("POST", "/accounts/callbacks", {"url": URL, "actions": {"subscribe": "{subscribe}", "unsubscribe": "{unsubscribe}", "open": "{open}", "click": "{click}"}}),
            ("DELETE", "/accounts/callbacks", None), None, "#00baff", "mail", "Starts the workflow on GetResponse events"),
    trigger("n8n-nodes-base.lemlistTrigger", "Lemlist Trigger", "lemlistApi", "https://api.lemlist.com/api", ["basic:=:apiKey"],
            [P("event", "Event", "options", "emailsReplied", ["emailsSent", "emailsOpened", "emailsClicked", "emailsReplied", "emailsBounced", "emailsUnsubscribed"])],
            ("POST", "/hooks", {"targetUrl": URL, "type": "{event}"}),
            ("DELETE", "/hooks/{webhookId}", None), "_id", "#316bff", "mail", "Starts the workflow on Lemlist campaign events"),
    trigger("n8n-nodes-base.mauticTrigger", "Mautic Trigger", "mauticApi", "{cred.url}/api", ["basic:username:password"],
            [EVENTS(["mautic.lead_post_save_new", "mautic.lead_post_save_update", "mautic.lead_post_delete", "mautic.form_on_submit", "mautic.email_on_open"], ["mautic.lead_post_save_new"])],
            ("POST", "/hooks/new", {"name": "Flowwolf", "webhookUrl": URL, "triggers": "{events}"}),
            ("DELETE", "/hooks/{webhookId}/delete", None), "hook.id", "#4e5e9e", "mail", "Starts the workflow on Mautic events"),
    trigger("n8n-nodes-base.calTrigger", "Cal.com Trigger", "calApi", "{cred.host}/v1", ["query:apiKey:apiKey"],
            [EVENTS(["BOOKING_CREATED", "BOOKING_CANCELLED", "BOOKING_RESCHEDULED", "MEETING_ENDED"], ["BOOKING_CREATED"])],
            ("POST", "/hooks", {"subscriberUrl": URL, "eventTriggers": "{events}", "active": True, "secret": "{secret}"}),
            ("DELETE", "/hooks/{webhookId}", None), "webhook.id", "#111827", "calendar", "Starts the workflow on Cal.com bookings", signature={"header": "x-cal-signature-256", "encoding": "hex"}),
    trigger("n8n-nodes-base.gumroadTrigger", "Gumroad Trigger", "gumroadApi", "https://api.gumroad.com/v2", [],
            [P("resource", "Resource", "options", "sale", ["sale", "refund", "dispute", "dispute_won", "cancellation", "subscription_updated", "subscription_ended"])],
            ("PUT", "/resource_subscriptions", {"access_token": "{cred.accessToken}", "resource_name": "{resource}", "post_url": URL}),
            ("DELETE", "/resource_subscriptions/{webhookId}?access_token={cred.accessToken}", None), "resource_subscription.id", "#ff90e8", "cart",
            "Starts the workflow on Gumroad sales", body_type="form"),
    trigger("n8n-nodes-base.figmaTrigger", "Figma Trigger", "figmaApi", "https://api.figma.com/v2", ["header:X-Figma-Token:accessToken"],
            [P("teamId", "Team ID", required=True), P("event", "Event", "options", "FILE_UPDATE", ["FILE_UPDATE", "FILE_VERSION_UPDATE", "FILE_COMMENT", "FILE_DELETE", "LIBRARY_PUBLISH"])],
            ("POST", "/webhooks", {"event_type": "{event}", "team_id": "{teamId}", "endpoint": URL, "passcode": "{nodeWebhookId}"}),
            ("DELETE", "/webhooks/{webhookId}", None), "id", "#a259ff", "image", "Starts the workflow on Figma file events"),
]

CREDS += [
    simple("calApi", "Cal.com API", ("host", "Host", False, "https://api.cal.com", ""), ("apiKey", "API Key", True, "", "")),
    simple("gumroadApi", "Gumroad API", ("accessToken", "Access Token", True, "", "")),
    simple("figmaApi", "Figma API", ("accessToken", "Personal Access Token", True, "", "")),
]

# ---------------------------------------------------------------------------
# Batch 11: polling triggers (src/pollers.rs), more webhook triggers, more integrations
# ---------------------------------------------------------------------------
POLL_TIMES = {"displayName": "Poll Times", "name": "pollTimes", "type": "fixedCollection", "typeOptions": {"multipleValues": True},
              "default": {"item": [{"mode": "everyMinute"}]},
              "options": [{"name": "item", "displayName": "Item", "values": [
                  {"displayName": "Mode", "name": "mode", "type": "options", "default": "everyMinute",
                   "options": [{"name": "Every Minute", "value": "everyMinute"}, {"name": "Every Hour", "value": "everyHour"}, {"name": "Every Day", "value": "everyDay"},
                               {"name": "Every X", "value": "everyX"}]},
                  {"displayName": "Value", "name": "value", "type": "number", "default": 5, "displayOptions": {"show": {"mode": ["everyX"]}}},
                  {"displayName": "Unit", "name": "unit", "type": "options", "default": "minutes", "displayOptions": {"show": {"mode": ["everyX"]}},
                   "options": [{"name": "Minutes", "value": "minutes"}, {"name": "Hours", "value": "hours"}]}]}]}


def poll_trigger(type_name, display, cred, base, auth, props, request, color, icon, desc, root=None, id_tpl="{id}", since=False, transform=None, expand=None, headers=None):
    """request/expand: (method, path, query dict or None, template or None)."""
    def req(r):
        return {"method": r[0], "path": r[1], "query": r[2], "template": r[3]}
    spec = {"credential": cred or "", "baseUrl": base, "auth": auth, "headers": headers or {}, "request": req(request), "id": id_tpl, "since": since}
    if root:
        spec["root"] = root
    if transform:
        spec["transform"] = transform
    if expand:
        spec["expand"] = req(expand)
    return {"name": type_name, "displayName": display, "group": ["trigger"], "category": "Triggers", "icon": icon, "color": color, "version": 1,
            "defaults": {"name": display}, "description": desc, "inputs": 0, "outputs": [""],
            "credentials": [{"name": cred, "required": True}] if cred else [], "properties": [POLL_TIMES] + props, "pollTrigger": spec}


NODES += [
    poll_trigger("n8n-nodes-base.gmailTrigger", "Gmail Trigger", "gmailOAuth2", "https://gmail.googleapis.com/gmail/v1/users/me", ["oauth2"],
                 [P("q", "Search", default="in:inbox", description="Gmail search, e.g. from:billing@acme.com has:attachment")],
                 ("GET", "/messages", {"q": "{q} after:{sinceUnix}", "maxResults": "50"}, None), "#ea4335", "mail", "Starts the workflow for new Gmail messages",
                 root="messages", since=True, expand=("GET", "/messages/{item.id}", {"format": "full"}, None)),
    poll_trigger("n8n-nodes-base.googleSheetsTrigger", "Google Sheets Trigger", "googleSheetsOAuth2Api", "https://sheets.googleapis.com/v4", ["oauth2"],
                 [P("documentId", "Spreadsheet ID", required=True), P("range", "Sheet or Range", default="Sheet1")],
                 ("GET", "/spreadsheets/{documentId}/values/{range}", None, None), "#0f9d58", "table", "Starts the workflow when rows are added to a sheet",
                 id_tpl="{row_number}", transform="sheetRows"),
    poll_trigger("n8n-nodes-base.googleDriveTrigger", "Google Drive Trigger", "googleDriveOAuth2Api", "https://www.googleapis.com/drive/v3", ["oauth2"],
                 [P("folderId", "Folder ID", required=True)],
                 ("GET", "/files", {"q": "'{folderId}' in parents and modifiedTime > '{since}' and trashed = false",
                                    "fields": "files(id,name,mimeType,modifiedTime,createdTime,webViewLink)", "orderBy": "modifiedTime"}, None),
                 "#1fa463", "file", "Starts the workflow when files in a Drive folder are created or changed", root="files", id_tpl="{id}:{modifiedTime}", since=True),
    poll_trigger("n8n-nodes-base.googleCalendarTrigger", "Google Calendar Trigger", "googleCalendarOAuth2Api", "https://www.googleapis.com/calendar/v3", ["oauth2"],
                 [P("calendarId", "Calendar ID", default="primary")],
                 ("GET", "/calendars/{calendarId}/events", {"updatedMin": "{since}", "singleEvents": "true", "showDeleted": "false", "maxResults": "250"}, None),
                 "#4285f4", "calendar", "Starts the workflow when events are created or updated", root="items", id_tpl="{id}:{updated}", since=True),
    poll_trigger("n8n-nodes-base.airtableTrigger", "Airtable Trigger", "airtableTokenApi", "https://api.airtable.com/v0", ["bearer:accessToken"],
                 [P("baseId", "Base ID", required=True), P("table", "Table", required=True)],
                 ("GET", "/{baseId}/{table}", {"filterByFormula": "IS_AFTER(LAST_MODIFIED_TIME(), '{since}')", "pageSize": "100"}, None),
                 "#18bfff", "table", "Starts the workflow when records are created or changed", root="records", id_tpl="{id}:{createdTime}", since=True),
    poll_trigger("n8n-nodes-base.notionTrigger", "Notion Trigger", "notionApi", "https://api.notion.com/v1", ["bearer:apiKey"],
                 [P("databaseId", "Database ID", required=True), P("event", "Event", "options", "pageAddedToDatabase",
                                                                    [("Page added", "pageAddedToDatabase"), ("Page updated", "pagedUpdatedInDatabase")])],
                 ("POST", "/databases/{databaseId}/query", None, {"filter": {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": "{since}"}},
                                                                   "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]}),
                 "#8a8a86", "file", "Starts the workflow when pages are added to or updated in a database", root="results", id_tpl="{id}:{last_edited_time}", since=True,
                 headers={"Notion-Version": "2022-06-28"}),
    poll_trigger("n8n-nodes-base.microsoftOutlookTrigger", "Microsoft Outlook Trigger", "microsoftOutlookOAuth2Api", "https://graph.microsoft.com/v1.0/me", ["oauth2"],
                 [P("folder", "Folder", default="inbox")],
                 ("GET", "/mailFolders/{folder}/messages", {"$filter": "receivedDateTime ge {since}", "$orderby": "receivedDateTime desc", "$top": "50"}, None),
                 "#0078d4", "mail", "Starts the workflow for new Outlook mail", root="value", since=True),
    poll_trigger("n8n-nodes-base.clockifyTrigger", "Clockify Trigger", "clockifyApi", "https://api.clockify.me/api/v1", ["header:X-Api-Key:apiKey"],
                 [P("workspaceId", "Workspace ID", required=True), P("userId", "User ID", required=True)],
                 ("GET", "/workspaces/{workspaceId}/user/{userId}/time-entries", {"start": "{since}"}, None),
                 "#03a9f4", "clock", "Starts the workflow for new Clockify time entries", since=True),
    poll_trigger("n8n-nodes-base.togglTrigger", "Toggl Trigger", "togglApi", "https://api.track.toggl.com/api/v9", ["basic:apiToken:=api_token"],
                 [], ("GET", "/me/time_entries", {"since": "{sinceUnix}"}, None),
                 "#e57cd8", "clock", "Starts the workflow for new or changed Toggl time entries", id_tpl="{id}:{at}", since=True),

    # webhook-registering triggers
    trigger("n8n-nodes-base.keapTrigger", "Keap Trigger", "keapOAuth2Api", "https://api.infusionsoft.com/crm/rest/v1", ["oauth2"],
            [P("eventKey", "Event", "options", "contact.add", ["contact.add", "contact.edit", "contact.delete", "order.add", "invoice.payment.add", "opportunity.add"])],
            ("POST", "/hooks", {"eventKey": "{eventKey}", "hookUrl": URL}), ("DELETE", "/hooks/{webhookId}", None), "key", "#36a635", "users", "Starts the workflow on Keap events"),
    trigger("n8n-nodes-base.koBoToolboxTrigger", "KoBoToolbox Trigger", "koBoToolboxApi", "{cred.URL}/api/v2", ["header:Authorization:Token {token}"],
            [P("formId", "Form ID", required=True)],
            ("POST", "/assets/{formId}/hooks/", {"name": "Flowwolf", "endpoint": URL, "active": True, "export_type": "json"}),
            ("DELETE", "/assets/{formId}/hooks/{webhookId}/", None), "uid", "#2094f3", "form", "Starts the workflow on KoBoToolbox submissions"),
    trigger("n8n-nodes-base.onfleetTrigger", "Onfleet Trigger", "onfleetApi", "https://onfleet.com/api/v2", ["basic:apiKey:="],
            [P("trigger", "Event", "options", 3, [("Task started", 0), ("Task ETA", 1), ("Task arrival", 2), ("Task completed", 3), ("Task failed", 4), ("Task created", 6), ("Task deleted", 8)])],
            ("POST", "/webhooks", {"url": URL, "trigger": "{trigger}", "name": "Flowwolf"}), ("DELETE", "/webhooks/{webhookId}", None), "id", "#e43a3a", "map",
            "Starts the workflow on Onfleet task events"),
    trigger("n8n-nodes-base.payPalTrigger", "PayPal Trigger", "payPalApi", "https://{cred.apiHost}", ["oauth2"],
            [P("event", "Event", "options", "PAYMENT.CAPTURE.COMPLETED", ["PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.REFUNDED", "CHECKOUT.ORDER.APPROVED",
                                                                       "BILLING.SUBSCRIPTION.ACTIVATED", "BILLING.SUBSCRIPTION.CANCELLED", "PAYMENT.PAYOUTSBATCH.SUCCESS", "*"])],
            ("POST", "/v1/notifications/webhooks", {"url": URL, "event_types": [{"name": "{event}"}]}), ("DELETE", "/v1/notifications/webhooks/{webhookId}", None),
            "id", "#003087", "cart", "Starts the workflow on PayPal events"),
    trigger("n8n-nodes-base.ciscoWebexTrigger", "Webex by Cisco Trigger", "ciscoWebexApi", "https://webexapis.com/v1", ["bearer:accessToken"],
            [P("resource", "Resource", "options", "messages", ["messages", "memberships", "rooms", "meetings", "attachmentActions"]),
             P("event", "Event", "options", "created", ["created", "updated", "deleted", "started", "ended", "all"])],
            ("POST", "/webhooks", {"name": "Flowwolf", "targetUrl": URL, "resource": "{resource}", "event": "{event}", "secret": "{secret}"}),
            ("DELETE", "/webhooks/{webhookId}", None), "id", "#00bceb", "chat", "Starts the workflow on Webex events",
            signature={"header": "x-spark-signature", "encoding": "hex", "algorithm": "sha1"}),
    trigger("n8n-nodes-base.zendeskTrigger", "Zendesk Trigger", "zendeskApi", "https://{cred.subdomain}.zendesk.com/api/v2", ["basic:{email}/token:apiToken"],
            [P("event", "Event", "options", "zen:event-type:user.created", ["zen:event-type:user.created", "zen:event-type:user.deleted", "zen:event-type:organization.created",
                                                                          "zen:event-type:article.published"])],
            ("POST", "/webhooks", {"webhook": {"name": "Flowwolf", "endpoint": URL, "http_method": "POST", "request_format": "json", "status": "active",
                                               "subscriptions": ["{event}"]}}),
            ("DELETE", "/webhooks/{webhookId}", None), "webhook.id", "#03363d", "ticket", "Starts the workflow on Zendesk events"),
    trigger("n8n-nodes-base.copperTrigger", "Copper Trigger", "copperApi", "https://api.copper.com/developer_api/v1", ["header:X-PW-AccessToken:apiKey", "header:X-PW-UserEmail:email"],
            [P("resource", "Resource", "options", "person", ["person", "company", "lead", "opportunity", "project", "task"]),
             P("event", "Event", "options", "new", ["new", "update", "delete"])],
            ("POST", "/webhooks", {"target": URL, "type": "{resource}", "event": "{event}"}), ("DELETE", "/webhooks/{webhookId}", None), "id", "#f26722", "users",
            "Starts the workflow on Copper CRM events", headers={"X-PW-Application": "developer_api"}),
    trigger("n8n-nodes-base.affinityTrigger", "Affinity Trigger", "affinityApi", "https://api.affinity.co", ["basic:=:apiKey"],
            [EVENTS(["person.created", "person.updated", "organization.created", "opportunity.created", "list_entry.created", "field_value.updated"], ["person.created"])],
            ("POST", "/webhook/subscribe", {"webhook_url": URL, "subscriptions": "{events}"}), ("DELETE", "/webhook/{webhookId}", None), "id", "#2a59ff", "users",
            "Starts the workflow on Affinity events"),
    trigger("n8n-nodes-base.eventbriteTrigger", "Eventbrite Trigger", "eventbriteApi", "https://www.eventbriteapi.com/v3", ["bearer:apiKey"],
            [P("organization", "Organization ID", required=True),
             P("actions", "Actions", default="order.placed,attendee.updated", description="Comma-separated: order.placed, order.refunded, attendee.updated, event.published, ...")],
            ("POST", "/organizations/{organization}/webhooks/", {"endpoint_url": URL, "actions": "{actions}"}), ("DELETE", "/webhooks/{webhookId}/", None),
            "id", "#f05537", "calendar", "Starts the workflow on Eventbrite orders and attendees"),
    trigger("n8n-nodes-base.workableTrigger", "Workable Trigger", "workableApi", "https://{cred.subdomain}.workable.com/spi/v3", ["bearer:accessToken"],
            [P("event", "Event", "options", "candidate_created", ["candidate_created", "candidate_moved"])],
            ("POST", "/subscriptions", {"target": URL, "event": "{event}"}), ("DELETE", "/subscriptions/{webhookId}", None), "id", "#1d9bf0", "users",
            "Starts the workflow on Workable candidate events"),
    trigger("n8n-nodes-base.acuitySchedulingTrigger", "Acuity Scheduling Trigger", "acuitySchedulingApi", "https://acuityscheduling.com/api/v1", ["basic:userId:apiKey"],
            [P("event", "Event", "options", "appointment.scheduled", ["appointment.scheduled", "appointment.rescheduled", "appointment.canceled", "appointment.changed", "order.completed"])],
            ("POST", "/webhooks", {"target": URL, "event": "{event}"}), ("DELETE", "/webhooks/{webhookId}", None), "id", "#315b7d", "calendar",
            "Starts the workflow on Acuity Scheduling appointments"),
    trigger("n8n-nodes-base.sendInBlueTrigger", "Brevo Trigger", "sendInBlueApi", "https://api.brevo.com/v3", ["header:api-key:apiKey"],
            [P("type", "Type", "options", "transactional", ["transactional", "marketing", "inbound"]),
             EVENTS(["sent", "delivered", "opened", "click", "hardBounce", "softBounce", "unsubscribed", "spam"], ["delivered"])],
            ("POST", "/webhooks", {"url": URL, "events": "{events}", "type": "{type}", "description": "Flowwolf"}), ("DELETE", "/webhooks/{webhookId}", None),
            "id", "#0b996e", "mail", "Starts the workflow on Brevo email events"),
    trigger("n8n-nodes-base.mailjetTrigger", "Mailjet Trigger", "mailjetEmailApi", "https://api.mailjet.com/v3/REST", ["basic:apiKey:secretKey"],
            [P("event", "Event", "options", "open", ["sent", "open", "click", "bounce", "blocked", "spam", "unsub"])],
            ("POST", "/eventcallbackurl", {"EventType": "{event}", "Url": URL, "Version": 2}), ("DELETE", "/eventcallbackurl/{webhookId}", None),
            "Data.0.ID", "#9585f4", "mail", "Starts the workflow on Mailjet email events"),
    trigger("n8n-nodes-base.helpScoutTrigger", "Help Scout Trigger", "helpScoutOAuth2Api", "https://api.helpscout.net/v2", ["oauth2"],
            [EVENTS(["convo.created", "convo.assigned", "convo.status", "convo.customer.reply.created", "customer.created", "satisfaction.ratings"], ["convo.created"])],
            ("POST", "/webhooks", {"url": URL, "events": "{events}", "secret": "{secret}", "payloadVersion": "V2"}), ("DELETE", "/webhooks/{webhookId}", None),
            "id", "#1292ee", "ticket", "Starts the workflow on Help Scout events",
            signature={"header": "x-helpscout-signature", "encoding": "base64", "algorithm": "sha1"}),

    # integrations
    node("n8n-nodes-base.actionNetwork", "Action Network", "actionNetworkApi", "https://actionnetwork.org/api/v2", ["header:OSDI-API-Token:apiKey"], [
        T("person", "create", "Create", "POST", "/people", [N("email", "Email", required=True), N("givenName", "First Name"), N("familyName", "Last Name")],
          template={"person": {"email_addresses": [{"address": "{email}"}], "given_name": "{givenName}", "family_name": "{familyName}"}}),
        T("person", "getAll", "Get Many", "GET", "/people", [], root="_embedded.osdi:people"),
        T("event", "getAll", "Get Many", "GET", "/events", [], root="_embedded.osdi:events"),
    ], "#9c1d2b", "users", "Marketing", "People and events in Action Network"),
    node("n8n-nodes-base.adalo", "Adalo", "adaloApi", "https://api.adalo.com/v0/apps/{cred.appId}/collections", ["bearer:apiKey"], [
        T("collection", "getAll", "Get Many", "GET", "/{collectionId}", [F("collectionId", "Collection ID", "path", required=True)], root="records", limit="limit"),
        T("collection", "get", "Get", "GET", "/{collectionId}/{rowId}", [F("collectionId", "Collection ID", "path", required=True), F("rowId", "Row ID", "path", required=True)]),
        T("collection", "create", "Create", "POST", "/{collectionId}", [F("collectionId", "Collection ID", "path", required=True), F("data", "Fields (JSON)", "none", kind="json", rows=5)],
          body_from="jsonParam"),
    ], "#4a3ab9", "table", "Data & Storage", "Records in Adalo collections"),
    node("n8n-nodes-base.clearbit", "Clearbit", "clearbitApi", "https://company.clearbit.com/v2", ["bearer:apiKey"], [
        T("company", "enrich", "Enrich", "GET", "/companies/find", [F("domain", "Domain", "query", required=True)]),
    ], "#3fa0f0", "search", "Sales", "Company data from Clearbit"),
    node("n8n-nodes-base.cortex", "Cortex", "cortexApi", "{cred.host}/api", ["bearer:apiKey"], [
        T("analyzer", "execute", "Run", "POST", "/analyzer/{analyzerId}/run", [F("analyzerId", "Analyzer ID", "path", required=True), F("data", "Observable", api="data", required=True),
                                                                             F("dataType", "Data Type", default="ip"), F("tlp", "TLP", kind="number", default=2)]),
        T("job", "get", "Get Job", "GET", "/job/{jobId}", [F("jobId", "Job ID", "path", required=True)]),
        T("job", "report", "Get Report", "GET", "/job/{jobId}/report", [F("jobId", "Job ID", "path", required=True)]),
    ], "#13a1c2", "shield", "Security", "Run Cortex analyzers on observables"),
    node("n8n-nodes-base.egoi", "E-goi", "egoiApi", "https://api.egoiapp.com", ["header:Apikey:apiKey"], [
        T("contact", "create", "Create", "POST", "/lists/{listId}/contacts", [F("listId", "List ID", "path", required=True), N("email", "Email", required=True), N("firstName", "First Name")],
          template={"base": {"email": "{email}", "first_name": "{firstName}"}}),
        T("contact", "getAll", "Get Many", "GET", "/lists/{listId}/contacts", [F("listId", "List ID", "path", required=True)], root="items", limit="limit"),
    ], "#00aeef", "mail", "Marketing", "Contacts in E-goi"),
    node("n8n-nodes-base.elasticSecurity", "Elastic Security", "elasticSecurityApi", "{cred.baseUrl}/api", ["basic:username:password"], [
        T("case", "create", "Create", "POST", "/cases", [N("title", "Title", required=True), N("description", "Description", rows=3, required=True)],
          template={"title": "{title}", "description": "{description}", "tags": [], "owner": "securitySolution", "settings": {"syncAlerts": True},
                    "connector": {"id": "none", "name": "none", "type": ".none", "fields": None}}),
        T("case", "get", "Get", "GET", "/cases/{caseId}", [F("caseId", "Case ID", "path", required=True)]),
        T("case", "getAll", "Get Many", "GET", "/cases/_find", [F("status", "Status", "query", kind="options", options=[("Any", ""), "open", "in-progress", "closed"])], root="cases", limit="perPage"),
    ], "#f04e98", "shield", "Security", "Cases in Elastic Security", headers={"kbn-xsrf": "true"}),
    node("n8n-nodes-base.jinaAi", "Jina AI", "jinaAiApi", "https://r.jina.ai", ["bearer:apiKey"], [
        T("reader", "read", "Read URL", "GET", "/{+url}", [F("url", "URL", "path", required=True, placeholder="https://example.com/article")]),
    ], "#009191", "sparkles", "AI", "Turn web pages into clean text for LLMs with Jina AI Reader", headers={"Accept": "application/json"}),
    node("n8n-nodes-base.lingvaNex", "LingvaNex", "lingvaNexApi", "https://api-b2b.lingvanex.com/v1", ["bearer:apiKey"], [
        T("translation", "translate", "Translate", "POST", "/translate", [F("data", "Text", rows=3, required=True), F("to", "Target Language", required=True, default="de_DE"),
                                                                         F("from", "Source Language")], static={"platform": "api"}),
    ], "#3e5df7", "chat", "Utility", "Translate text with LingvaNex"),
    node("n8n-nodes-base.msg91", "MSG91", "msg91Api", "https://control.msg91.com/api/v5", ["header:authkey:authkey"], [
        T("sms", "send", "Send (Flow)", "POST", "/flow/", [N("templateId", "Template ID", required=True), N("mobiles", "Mobile Number", required=True)],
          template={"template_id": "{templateId}", "recipients": [{"mobiles": "{mobiles}"}]}),
    ], "#2e7ce4", "chat", "Communication", "Send SMS with MSG91"),
    node("n8n-nodes-base.nextCloud", "Nextcloud", "nextCloudApi", "{cred.url}/ocs/v1.php/cloud", ["basic:user:password"], [
        T("user", "create", "Create", "POST", "/users", [F("userid", "Username", required=True), F("email", "Email"), F("password", "Password")], root="ocs.data"),
        T("user", "get", "Get", "GET", "/users/{userId}", [F("userId", "Username", "path", required=True)], root="ocs.data"),
        T("user", "getAll", "Get Many", "GET", "/users", [F("search", "Search", "query")], root="ocs.data.users"),
    ], "#0082c9", "users", "Productivity", "Users in Nextcloud", headers={"OCS-APIRequest": "true", "Accept": "application/json"}, body_type="form"),
    node("n8n-nodes-base.oneSimpleApi", "One Simple API", "oneSimpleApi", "https://onesimpleapi.com/api", ["query:token:apiToken"], [
        T("website", "screenshot", "Screenshot", "GET", "/screenshot", [F("url", "URL", "query", required=True)], static_query={"output": "json"}),
        T("website", "seo", "SEO Check", "GET", "/page_info", [F("url", "URL", "query", required=True)], static_query={"output": "json"}),
        T("utility", "exchangeRate", "Exchange Rate", "GET", "/exchange_rate", [F("from_currency", "From", "query", default="EUR"), F("to_currency", "To", "query", default="USD")]),
    ], "#1e1e1e", "globe", "Utility", "Screenshots, page info and exchange rates from One Simple API"),
    node("n8n-nodes-base.uproc", "uProc", "uprocApi", "https://api.uproc.io/api/v2", ["basic:email:apiKey"], [
        T("tool", "process", "Run Tool", "POST", "/process", [N("processor", "Tool", required=True, placeholder="check-email-exists"), N("params", "Parameters (JSON)", kind="json", default='{"email": ""}')],
          template={"processor": "{processor}", "params": "{params}"}),
    ], "#ff6633", "zap", "Utility", "Data quality tools from uProc"),
    node("n8n-nodes-base.microsoftGraphSecurity", "Microsoft Graph Security", "microsoftGraphSecurityOAuth2Api", GRAPH, ["oauth2"], [
        T("alert", "getAll", "Get Many", "GET", "/security/alerts_v2", [F("$filter", "Filter", "query", placeholder="severity eq 'high'")], root="value", limit="$top"),
        T("alert", "update", "Update", "PATCH", "/security/alerts_v2/{alertId}", [F("alertId", "Alert ID", "path", required=True),
                                                                                  F("status", "Status", kind="options", options=["new", "inProgress", "resolved"])]),
        T("incident", "getAll", "Get Many", "GET", "/security/incidents", [], root="value", limit="$top"),
    ], "#0078d4", "shield", "Security", "Alerts and incidents in Microsoft Graph Security"),
    node("n8n-nodes-base.venafiTlsProtectCloud", "Venafi TLS Protect Cloud", "venafiTlsProtectCloudApi", "https://api.venafi.cloud", ["header:tppl-api-key:apiKey"], [
        T("certificate", "getAll", "Get Many", "GET", "/outagedetection/v1/certificates", [], root="certificates"),
        T("certificate", "get", "Get", "GET", "/outagedetection/v1/certificates/{certificateId}", [F("certificateId", "Certificate ID", "path", required=True)]),
    ], "#ff8200", "shield", "Security", "Certificates in Venafi TLS Protect Cloud"),
]

CREDS += [
    simple("eventbriteApi", "Eventbrite API", ("apiKey", "Private Token", True, "", "")),
    simple("workableApi", "Workable API", ("subdomain", "Subdomain", False, "", ""), ("accessToken", "Access Token", True, "", "")),
    simple("acuitySchedulingApi", "Acuity Scheduling API", ("userId", "User ID", False, "", ""), ("apiKey", "API Key", True, "", "")),
    simple("actionNetworkApi", "Action Network API", ("apiKey", "API Key", True, "", "")),
    simple("adaloApi", "Adalo API", ("appId", "App ID", False, "", ""), ("apiKey", "API Key", True, "", "")),
    simple("clearbitApi", "Clearbit API", ("apiKey", "API Key", True, "", "")),
    simple("cortexApi", "Cortex API", ("host", "URL", False, "", "https://cortex.example.com"), ("apiKey", "API Key", True, "", "")),
    simple("egoiApi", "E-goi API", ("apiKey", "API Key", True, "", "")),
    simple("elasticSecurityApi", "Elastic Security API", ("baseUrl", "Kibana URL", False, "", "https://kibana.example.com"), ("username", "Username", False, "", ""), ("password", "Password", True, "", "")),
    simple("jinaAiApi", "Jina AI API", ("apiKey", "API Key", True, "", "")),
    simple("lingvaNexApi", "LingvaNex API", ("apiKey", "API Key", True, "", "")),
    simple("msg91Api", "MSG91 API", ("authkey", "Auth Key", True, "", "")),
    simple("nextCloudApi", "Nextcloud API", ("url", "URL", False, "", "https://cloud.example.com"), ("user", "User", False, "", ""), ("password", "App Password", True, "", "")),
    simple("oneSimpleApi", "One Simple API", ("apiToken", "API Token", True, "", "")),
    simple("uprocApi", "uProc API", ("email", "Email", False, "", ""), ("apiKey", "API Key", True, "", "")),
    microsoft("microsoftGraphSecurityOAuth2Api", "Microsoft Graph Security OAuth2 API", "openid offline_access SecurityAlert.ReadWrite.All SecurityIncident.Read.All"),
    simple("venafiTlsProtectCloudApi", "Venafi TLS Protect Cloud API", ("apiKey", "API Key", True, "", "")),
]

# ---------------------------------------------------------------------------
# Batch 12: AI cluster nodes (src/nodes/cluster.rs). Sub-nodes connect to their root through
# ai_* connections and never run on their own; roots list the sub-node types they accept.
# ---------------------------------------------------------------------------
LCN = "@n8n/n8n-nodes-langchain."


def sub(kind, display, sub_type, props, desc, creds=(), color="#6d5acf", icon="sparkles", ai_inputs=()):
    d = {"name": LCN + kind, "displayName": display, "group": ["transform"], "category": "AI", "icon": icon, "color": color, "version": 1,
         "defaults": {"name": display}, "description": desc, "inputs": 0, "outputs": [], "subNodeType": sub_type,
         "credentials": [{"name": c, "required": True} for c in creds], "properties": props}
    if ai_inputs:
        d["aiInputs"] = list(ai_inputs)
    return d


def root(kind, display, props, desc, ai_inputs, outputs=None, color="#6d5acf", icon="sparkles", creds=()):
    return {"name": LCN + kind, "displayName": display, "group": ["transform"], "category": "AI", "icon": icon, "color": color, "version": 1,
            "defaults": {"name": display}, "description": desc, "inputs": 1, "outputs": outputs or [""], "aiInputs": ai_inputs,
            "credentials": [{"name": c, "required": True} for c in creds], "properties": props}


def MODEL(default, name="model", display="Model"):
    return P(name, display, default=default)


LLM_OPTS = {"displayName": "Options", "name": "options", "type": "collection", "default": {}, "placeholder": "Add Option", "options": [
    P("temperature", "Sampling Temperature", "number", 0.7), P("maxTokens", "Maximum Number of Tokens", "number", -1)]}
SESSION = [P("sessionIdType", "Session ID", "options", "fromInput", [("Connected Chat Trigger Node", "fromInput"), ("Define below", "customKey")]),
           dict(P("sessionKey", "Key", default="={{ $json.sessionId }}"), displayOptions={"show": {"sessionIdType": ["customKey"]}}),
           P("contextWindowLength", "Context Window Length", "number", 5)]
PROMPT = [P("promptType", "Source for Prompt", "options", "auto", [("Connected Chat Trigger Node", "auto"), ("Define below", "define")]),
          dict(P("text", "Prompt", default="", description="The question or instruction for the model"), displayOptions={"show": {"promptType": ["define"]}}, typeOptions={"rows": 3})]
VS_PROPS = [P("mode", "Operation Mode", "options", "retrieve", [("Get Many", "load"), ("Insert Documents", "insert"), ("Retrieve Documents (as vector store for chain/tool)", "retrieve"),
                                                                 ("Retrieve Documents (as tool for AI Agent)", "retrieve-as-tool")]),
            dict(P("prompt", "Prompt", default=""), displayOptions={"show": {"mode": ["load"]}}),
            dict(P("topK", "Limit", "number", 4), displayOptions={"show": {"mode": ["load", "retrieve-as-tool"]}}),
            dict(P("toolName", "Name", default=""), displayOptions={"show": {"mode": ["retrieve-as-tool"]}}),
            dict(P("toolDescription", "Description", default="Retrieves documents from the knowledge base"), displayOptions={"show": {"mode": ["retrieve-as-tool"]}}),
            dict(P("clearStore", "Clear Store", "boolean", False), displayOptions={"show": {"mode": ["insert"]}})]

NODES += [
    # chat models
    sub("lmChatOpenAi", "OpenAI Chat Model", "ai_languageModel", [MODEL("gpt-4o-mini"), LLM_OPTS], "Chat model of OpenAI or any OpenAI-compatible server", ["openAiApi"], "#10a37f"),
    sub("lmChatAnthropic", "Anthropic Chat Model", "ai_languageModel", [MODEL("claude-sonnet-5-5"), LLM_OPTS], "Claude models by Anthropic", ["anthropicApi"], "#d97757"),
    sub("lmChatOllama", "Ollama Chat Model", "ai_languageModel", [MODEL("llama3.2"), LLM_OPTS], "Local models served by Ollama", ["ollamaApi"], "#1e1e1e"),
    sub("lmChatMistralCloud", "Mistral Cloud Chat Model", "ai_languageModel", [MODEL("mistral-small-latest"), LLM_OPTS], "Models by Mistral AI", ["mistralCloudApi"], "#fa520f"),
    sub("lmChatGroq", "Groq Chat Model", "ai_languageModel", [MODEL("llama-3.3-70b-versatile"), LLM_OPTS], "Fast inference on Groq", ["groqApi"], "#f55036"),
    sub("lmChatGoogleGemini", "Google Gemini Chat Model", "ai_languageModel", [MODEL("models/gemini-2.0-flash", "modelName"), LLM_OPTS], "Gemini models by Google", ["googlePalmApi"], "#4285f4"),
    sub("lmChatDeepSeek", "DeepSeek Chat Model", "ai_languageModel", [MODEL("deepseek-chat"), LLM_OPTS], "Models by DeepSeek", ["deepSeekApi"], "#4d6bfe"),
    sub("lmChatOpenRouter", "OpenRouter Chat Model", "ai_languageModel", [MODEL("openai/gpt-4o-mini"), LLM_OPTS], "Hundreds of models through OpenRouter", ["openRouterApi"], "#6467f2"),
    sub("lmChatXAiGrok", "xAI Grok Chat Model", "ai_languageModel", [MODEL("grok-3-mini"), LLM_OPTS], "Grok models by xAI", ["xAiApi"], "#000000"),
    sub("lmChatCohere", "Cohere Chat Model", "ai_languageModel", [MODEL("command-a-03-2025"), LLM_OPTS], "Command models by Cohere", ["cohereApi"], "#39594d"),
    sub("lmChatAzureOpenAi", "Azure OpenAI Chat Model", "ai_languageModel", [MODEL("", "model", "Deployment Name"), LLM_OPTS], "OpenAI models deployed on Azure", ["azureOpenAiApi"], "#0078d4"),
    sub("lmChatAwsBedrock", "AWS Bedrock Chat Model", "ai_languageModel", [MODEL("anthropic.claude-3-5-haiku-20241022-v1:0"), LLM_OPTS], "Models on AWS Bedrock (Converse API)", ["aws"], "#ff9900"),
    # memory
    sub("memoryBufferWindow", "Simple Memory", "ai_memory", SESSION, "Keeps the last messages of a conversation in Flowwolf", (), "#b2775e", "history"),
    sub("memoryPostgresChat", "Postgres Chat Memory", "ai_memory", SESSION + [P("tableName", "Table Name", default="n8n_chat_histories")], "Chat history in PostgreSQL", ["postgres"], "#336791", "history"),
    sub("memoryRedisChat", "Redis Chat Memory", "ai_memory", SESSION + [P("sessionTTL", "Session Time To Live (seconds)", "number", 0)], "Chat history in Redis", ["redis"], "#d82c20", "history"),
    # tools
    sub("toolCalculator", "Calculator", "ai_tool", [], "Lets the agent compute math expressions", (), "#ff9922", "code"),
    sub("toolThink", "Think Tool", "ai_tool", [P("description", "Description", default="Use the tool to think about something.")], "Gives the agent room to reason step by step", (), "#8a8a86", "sparkles"),
    sub("toolWikipedia", "Wikipedia", "ai_tool", [], "Looks up Wikipedia articles", (), "#333333", "book"),
    sub("toolSerpApi", "SerpApi (Google Search)", "ai_tool", [], "Web search through SerpApi", ["serpApi"], "#2a6af5", "search"),
    sub("toolWolframAlpha", "Wolfram|Alpha", "ai_tool", [], "Math, science and unit answers", ["wolframAlphaApi"], "#dd1100", "chart"),
    sub("toolSearXng", "SearXNG", "ai_tool", [], "Web search through your SearXNG instance", ["searXngApi"], "#3050ff", "search"),
    sub("toolCode", "Code Tool", "ai_tool", [P("name", "Name", default="my_tool"), P("description", "Description", default=""),
                                             dict(P("jsCode", "JavaScript", default="// The input is available as `query`\nreturn query;"), typeOptions={"rows": 8, "editor": "code"})],
        "A tool written in JavaScript", (), "#ff9922", "code"),
    sub("toolHttpRequest", "HTTP Request Tool", "ai_tool", [P("toolDescription", "Description", default="Calls an API"), P("method", "Method", "options", "GET", ["GET", "POST", "PUT", "PATCH", "DELETE"]),
                                                             P("url", "URL", default="", placeholder="https://api.example.com/search?q={input}")],
        "Lets the agent call an HTTP endpoint", (), "#0ea5e9", "globe"),
    sub("toolWorkflow", "Call n8n Workflow Tool", "ai_tool", [P("name", "Name", default="call_workflow"), P("description", "Description", default=""), P("workflowId", "Workflow", default="")],
        "Lets the agent run another workflow", (), "#ff6d5a", "workflow"),
    sub("toolVectorStore", "Vector Store Question Answer Tool", "ai_tool", [P("name", "Name", default="knowledge_base"), P("description", "Description", default=""), P("topK", "Limit", "number", 4)],
        "Answers questions from a connected vector store", (), "#7c5cff", "table", ai_inputs=("ai_vectorStore",)),
    sub("mcpClientTool", "MCP Client Tool", "ai_tool", [P("endpointUrl", "Endpoint", default="", placeholder="https://mcp.example.com/mcp"),
                                                         P("serverTransport", "Server Transport", "options", "httpStreamable",
                                                           [("HTTP Streamable", "httpStreamable"), ("Server Sent Events (Deprecated)", "sse")]),
                                                         P("authentication", "Authentication", "options", "none", [("None", "none"), ("Bearer", "bearerAuth"), ("Header", "headerAuth")]),
                                                         P("include", "Tools to Include", "options", "all", [("All", "all"), ("Selected", "selected"), ("All Except", "except")]),
                                                         dict(P("includeTools", "Tools to Include", "multiOptions", []), displayOptions={"show": {"include": ["selected"]}}),
                                                         dict(P("excludeTools", "Tools to Exclude", "multiOptions", []), displayOptions={"show": {"include": ["except"]}})],
        "Gives the agent the tools of an MCP server (streamable HTTP or SSE)", (), "#1e1e1e", "zap"),
    # embeddings
    sub("embeddingsOpenAi", "Embeddings OpenAI", "ai_embedding", [MODEL("text-embedding-3-small")], "Embeddings from OpenAI or a compatible server", ["openAiApi"], "#10a37f", "table"),
    sub("embeddingsOllama", "Embeddings Ollama", "ai_embedding", [MODEL("nomic-embed-text")], "Local embeddings with Ollama", ["ollamaApi"], "#1e1e1e", "table"),
    sub("embeddingsMistralCloud", "Embeddings Mistral Cloud", "ai_embedding", [MODEL("mistral-embed")], "Embeddings by Mistral AI", ["mistralCloudApi"], "#fa520f", "table"),
    sub("embeddingsGoogleGemini", "Embeddings Google Gemini", "ai_embedding", [MODEL("models/text-embedding-004", "modelName")], "Embeddings by Google", ["googlePalmApi"], "#4285f4", "table"),
    # documents
    sub("documentDefaultDataLoader", "Default Data Loader", "ai_document", [
        P("dataType", "Type of Data", "options", "json", [("JSON", "json"), ("Binary (file)", "binary")]),
        dict(P("jsonMode", "Mode", "options", "allInputData", [("Load All Input Data", "allInputData"), ("Load Specific Data", "expressionData")]),
             displayOptions={"show": {"dataType": ["json"]}}),
        dict(P("jsonData", "Data", default=""), displayOptions={"show": {"jsonMode": ["expressionData"], "dataType": ["json"]}}),
        dict(P("loader", "Data Format", "options", "auto", [("Automatically by file type", "auto"), ("Text", "textLoader"), ("CSV", "csvLoader"), ("JSON", "jsonLoader"),
                                                             ("PDF", "pdfLoader")]), displayOptions={"show": {"dataType": ["binary"]}}),
        dict(P("binaryDataKey", "Input Data Field Name", default="data"), displayOptions={"show": {"dataType": ["binary"]}}),
        P("textSplittingMode", "Text Splitting", "options", "simple", [("Simple (1000 characters, 200 overlap)", "simple"), ("Custom (connect a text splitter)", "custom")]),
        {"displayName": "Options", "name": "options", "type": "collection", "default": {}, "placeholder": "Add Option", "options": [
            {"displayName": "Metadata", "name": "metadata", "type": "fixedCollection", "typeOptions": {"multipleValues": True}, "default": {},
             "options": [{"name": "metadataValues", "displayName": "Fields to Add", "values": [P("name", "Name"), P("value", "Value")]}]}]}],
        "Turns input items or files (text, CSV, JSON, PDF) into documents for a vector store", (), "#7c5cff", "file", ai_inputs=("ai_textSplitter",)),
    sub("textSplitterRecursiveCharacterTextSplitter", "Recursive Character Text Splitter", "ai_textSplitter", [P("chunkSize", "Chunk Size", "number", 1000), P("chunkOverlap", "Chunk Overlap", "number", 0)],
        "Splits text at paragraphs, lines and words", (), "#7c5cff", "file"),
    sub("textSplitterCharacterTextSplitter", "Character Text Splitter", "ai_textSplitter", [P("separator", "Separator", default="\\n\\n"), P("chunkSize", "Chunk Size", "number", 1000),
                                                                                            P("chunkOverlap", "Chunk Overlap", "number", 0)],
        "Splits text at a separator", (), "#7c5cff", "file"),
    sub("textSplitterTokenSplitter", "Token Splitter", "ai_textSplitter", [P("chunkSize", "Chunk Size (tokens)", "number", 1000), P("chunkOverlap", "Chunk Overlap", "number", 0)],
        "Splits text into chunks of about N tokens", (), "#7c5cff", "file"),
    sub("retrieverVectorStore", "Vector Store Retriever", "ai_retriever", [P("topK", "Limit", "number", 4)], "Retrieves documents from a connected vector store", (), "#7c5cff", "search",
        ai_inputs=("ai_vectorStore",)),
    # output parsers
    sub("outputParserStructured", "Structured Output Parser", "ai_outputParser", [
        P("schemaType", "Schema Type", "options", "fromJson", [("Generate From JSON Example", "fromJson"), ("Define using JSON Schema", "manual")]),
        dict(P("jsonSchemaExample", "JSON Example", default='{\n  "state": "California",\n  "cities": ["Los Angeles", "San Francisco"]\n}'), displayOptions={"show": {"schemaType": ["fromJson"]}}, typeOptions={"rows": 6}),
        dict(P("inputSchema", "Input Schema", default='{\n  "type": "object",\n  "properties": {}\n}'), displayOptions={"show": {"schemaType": ["manual"]}}, typeOptions={"rows": 6})],
        "Makes the model answer with JSON of a given shape", (), "#5d6f84", "code"),
    sub("outputParserItemList", "Item List Output Parser", "ai_outputParser", [{"displayName": "Options", "name": "options", "type": "collection", "default": {}, "placeholder": "Add Option",
                                                                                "options": [P("numberOfItems", "Number Of Items", "number", -1), P("separator", "Separator", default="\\n")]}],
        "Makes the model answer with a list", (), "#5d6f84", "list"),
    sub("outputParserAutofixing", "Auto-fixing Output Parser", "ai_outputParser", [], "Lets a model repair answers another parser rejects", (), "#5d6f84", "code",
        ai_inputs=("ai_languageModel", "ai_outputParser")),
    # vector stores
    root("vectorStoreInMemory", "Simple Vector Store", VS_PROPS + [P("memoryKey", "Memory Key", default="vector_store_key")], "Vector store kept by Flowwolf (no extra service)",
         ["ai_embedding", "ai_document"], color="#7c5cff", icon="table"),
    root("vectorStoreQdrant", "Qdrant Vector Store", VS_PROPS + [P("qdrantCollection", "Collection", default="")], "Vector store in Qdrant",
         ["ai_embedding", "ai_document"], color="#dc244c", icon="table", creds=("qdrantApi",)),
    root("vectorStorePinecone", "Pinecone Vector Store", VS_PROPS + [P("pineconeIndex", "Pinecone Index", default=""),
                                                                     {"displayName": "Options", "name": "options", "type": "collection", "default": {}, "placeholder": "Add Option",
                                                                      "options": [P("pineconeNamespace", "Namespace", default=""), P("clearNamespace", "Clear Namespace", "boolean", False)]}],
         "Vector store in Pinecone", ["ai_embedding", "ai_document"], color="#1c17ff", icon="table", creds=("pineconeApi",)),
    root("vectorStoreSupabase", "Supabase Vector Store", VS_PROPS + [P("tableName", "Table Name", default="documents"),
                                                                     {"displayName": "Options", "name": "options", "type": "collection", "default": {}, "placeholder": "Add Option",
                                                                      "options": [P("queryName", "Query Name", default="match_documents")]}],
         "Vector store in Supabase (pgvector with a match_documents function)", ["ai_embedding", "ai_document"], color="#3ecf8e", icon="table", creds=("supabaseApi",)),
    root("vectorStorePGVector", "Postgres PGVector Store", VS_PROPS + [P("tableName", "Table Name", default="n8n_vectors")],
         "Vector store in PostgreSQL with the pgvector extension", ["ai_embedding", "ai_document"], color="#336791", icon="table", creds=("postgres",)),
    {"name": LCN + "mcpTrigger", "displayName": "MCP Server Trigger", "group": ["trigger"], "category": "Triggers", "icon": "zap", "color": "#1e1e1e", "version": 1,
     "defaults": {"name": "MCP Server Trigger"}, "description": "Offers the connected tools to MCP clients (streamable HTTP at /mcp/<path>, SSE at /mcp/<path>/sse)",
     "inputs": 0, "outputs": [], "aiInputs": ["ai_tool"], "credentials": [{"name": "httpBearerAuth"}, {"name": "httpHeaderAuth"}],
     "properties": [P("path", "Path", default="", description="Served at /mcp/<path>; empty uses the node's webhook id"),
                    P("authentication", "Authentication", "options", "none", [("None", "none"), ("Bearer Auth", "bearerAuth"), ("Header Auth", "headerAuth")])]},
    # chains
    root("chainLlm", "Basic LLM Chain", PROMPT + [P("hasOutputParser", "Require Specific Output Format", "boolean", False),
                                                  {"displayName": "Chat Messages", "name": "messages", "type": "fixedCollection", "typeOptions": {"multipleValues": True}, "default": {},
                                                   "options": [{"name": "messageValues", "displayName": "Prompt", "values": [
                                                       P("type", "Type", "options", "SystemMessagePromptTemplate", [("System", "SystemMessagePromptTemplate")]), P("message", "Message")]}]}],
         "Sends a prompt to a chat model", ["ai_languageModel", "ai_outputParser"]),
    root("sentimentAnalysis", "Sentiment Analysis", [P("inputText", "Text to Analyze", default="={{ $json.text }}"),
                                                     {"displayName": "Options", "name": "options", "type": "collection", "default": {}, "placeholder": "Add Option", "options": [
                                                         P("categories", "Sentiment Categories", default="Positive, Neutral, Negative"),
                                                         P("includeDetailedResults", "Include Detailed Results", "boolean", False),
                                                         P("systemPromptTemplate", "System Prompt Template", default="")]}],
         "Routes items by the sentiment of a text (one output per category)", ["ai_languageModel"], outputs="dynamic:categories"),
    root("textClassifier", "Text Classifier", [P("inputText", "Text to Classify", default="={{ $json.text }}"),
                                               {"displayName": "Categories", "name": "categories", "type": "fixedCollection", "typeOptions": {"multipleValues": True}, "default": {},
                                                "options": [{"name": "categories", "displayName": "Category", "values": [P("category", "Category"), P("description", "Description")]}]},
                                               {"displayName": "Options", "name": "options", "type": "collection", "default": {}, "placeholder": "Add Option", "options": [
                                                   P("multiClass", "Allow Multiple Classes To Be True", "boolean", False),
                                                   P("fallback", "When No Clear Match", "options", "discard", [("Discard Item", "discard"), ("Output on Extra 'Other' Branch", "other")]),
                                                   P("systemPromptTemplate", "System Prompt Template", default="")]}],
         "Routes items to the category a model picks (one output per category)", ["ai_languageModel"], outputs="dynamic:categories"),
    root("informationExtractor", "Information Extractor", [P("text", "Text", default="={{ $json.text }}"),
                                                           P("schemaType", "Schema Type", "options", "fromAttributes", [("From Attribute Descriptions", "fromAttributes"),
                                                                                                                          ("Generate From JSON Example", "fromJson"), ("Define using JSON Schema", "manual")]),
                                                           dict({"displayName": "Attributes", "name": "attributes", "type": "fixedCollection", "typeOptions": {"multipleValues": True}, "default": {},
                                                                 "options": [{"name": "attributes", "displayName": "Attribute", "values": [
                                                                     P("name", "Name"), P("type", "Type", "options", "string", ["string", "number", "boolean", "date"]), P("description", "Description"),
                                                                     P("required", "Required", "boolean", False)]}]}, displayOptions={"show": {"schemaType": ["fromAttributes"]}}),
                                                           dict(P("jsonSchemaExample", "JSON Example", default="{}"), displayOptions={"show": {"schemaType": ["fromJson"]}}),
                                                           dict(P("inputSchema", "Input Schema", default="{}"), displayOptions={"show": {"schemaType": ["manual"]}})],
         "Pulls structured fields out of text", ["ai_languageModel"]),
    root("chainSummarization", "Summarization Chain", [P("operationMode", "Data to Summarize", "options", "nodeInputJson", [("Use Node Input (JSON)", "nodeInputJson"),
                                                                                                                          ("Use Document Loader", "documentLoader")]),
                                                       P("chunkSize", "Chunk Size", "number", 6000)],
         "Summarizes the input (map-reduce for long texts)", ["ai_languageModel", "ai_document"]),
    root("chainRetrievalQa", "Question and Answer Chain", PROMPT, "Answers questions from documents of a retriever", ["ai_languageModel", "ai_retriever"]),
]

CREDS += [
    simple("deepSeekApi", "DeepSeek API", ("apiKey", "API Key", True, "", ""), ("url", "Base URL", False, "https://api.deepseek.com", "")),
    simple("openRouterApi", "OpenRouter API", ("apiKey", "API Key", True, "", ""), ("url", "Base URL", False, "https://openrouter.ai/api/v1", "")),
    simple("xAiApi", "xAI API", ("apiKey", "API Key", True, "", ""), ("url", "Base URL", False, "https://api.x.ai/v1", "")),
    simple("cohereApi", "Cohere API", ("apiKey", "API Key", True, "", ""), ("url", "Base URL", False, "https://api.cohere.ai/compatibility/v1", "")),
    simple("azureOpenAiApi", "Azure OpenAI API", ("resourceName", "Resource Name", False, "", ""), ("apiKey", "API Key", True, "", ""),
           ("apiVersion", "API Version", False, "2024-10-21", ""), ("endpoint", "Endpoint (optional)", False, "", "https://my-resource.openai.azure.com")),
    {"name": "mongoDb", "displayName": "MongoDB", "properties": [
        {"displayName": "Configuration Type", "name": "configurationType", "type": "options", "default": "connectionString",
         "options": [{"name": "Connection String", "value": "connectionString"}, {"name": "Values", "value": "values"}]},
        {"displayName": "Connection String", "name": "connectionString", "type": "string", "typeOptions": {"password": True}, "default": "",
         "placeholder": "mongodb://user:pass@localhost:27017", "displayOptions": {"show": {"configurationType": ["connectionString"]}}},
        {"displayName": "Host", "name": "host", "type": "string", "default": "", "displayOptions": {"show": {"configurationType": ["values"]}}},
        {"displayName": "Port", "name": "port", "type": "number", "default": 27017, "displayOptions": {"show": {"configurationType": ["values"]}}},
        {"displayName": "User", "name": "user", "type": "string", "default": "", "displayOptions": {"show": {"configurationType": ["values"]}}},
        {"displayName": "Password", "name": "password", "type": "string", "typeOptions": {"password": True}, "default": "", "displayOptions": {"show": {"configurationType": ["values"]}}},
        {"displayName": "TLS", "name": "tls", "type": "boolean", "default": False, "displayOptions": {"show": {"configurationType": ["values"]}}},
        {"displayName": "Database", "name": "database", "type": "string", "default": ""}]},
    simple("ftp", "FTP", ("host", "Host", False, "", "localhost"), ("port", "Port", False, "21", ""), ("username", "Username", False, "", ""), ("password", "Password", True, "", "")),
    {"name": "sftp", "displayName": "SFTP", "properties": [
        {"displayName": "Host", "name": "host", "type": "string", "default": "", "placeholder": "localhost"},
        {"displayName": "Port", "name": "port", "type": "number", "default": 22},
        {"displayName": "Username", "name": "username", "type": "string", "default": ""},
        {"displayName": "Password", "name": "password", "type": "string", "typeOptions": {"password": True}, "default": ""},
        {"displayName": "Private Key", "name": "privateKey", "type": "string", "typeOptions": {"password": True, "rows": 4}, "default": "",
         "description": "OpenSSH or PEM private key; used instead of the password when set"},
        {"displayName": "Passphrase", "name": "passphrase", "type": "string", "typeOptions": {"password": True}, "default": ""}]},
    simple("discordBotApi", "Discord Bot API", ("botToken", "Bot Token", True, "", "")),
    simple("pineconeApi", "Pinecone API", ("apiKey", "API Key", True, "", "")),
    simple("qdrantApi", "Qdrant API", ("qdrantUrl", "Qdrant URL", False, "http://localhost:6333", ""), ("apiKey", "API Key", True, "", "")),
    simple("serpApi", "SerpApi", ("apiKey", "API Key", True, "", "")),
    simple("wolframAlphaApi", "Wolfram|Alpha API", ("appId", "App ID", True, "", "")),
    simple("searXngApi", "SearXNG API", ("apiUrl", "API URL", False, "", "https://searx.example.com")),
]

if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"nodes": NODES, "credentials": CREDS}, f, indent=1)
    print(f"wrote {len(NODES)} nodes, {len(CREDS)} credential types -> {os.path.normpath(OUT)}")
