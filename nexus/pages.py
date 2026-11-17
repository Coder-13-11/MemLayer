"""Server-rendered pages: sign in, create account, password reset, invitations and app consent, plus the
public site's templates. Plain HTML forms that work without JavaScript; every value is escaped.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .accounts import MIN_PASSWORD
from .brand import BRAND

STATIC_DIR = Path(__file__).resolve().parent / "static"
e = html.escape

# The mark: two strokes meeting at a point that a third line passes through. Drawn inline so pages need no image.
MARK = ('<svg class="mark-glyph" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">'
        '<path d="M4 19V5l16 14V5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="miter"/>'
        '</svg>')


def shell(title: str, body: str, *, wide: bool = False) -> str:
    name = e(BRAND["name"])
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{e(title)} · {name}</title><meta name='robots' content='noindex'>"
        "<link rel='icon' href='/static/site/favicon.svg' type='image/svg+xml'>"
        "<link rel='stylesheet' href='/static/site/site.css'></head><body class='auth'>"
        f"<header class='auth-top'><a class='mark' href='/'>{MARK}<span>{name}</span></a></header>"
        f"<main class='auth-card{' wide' if wide else ''}'>{body}</main></body></html>")


def _error(message: str | None) -> str:
    return f"<p class='notice error' role='alert'>{e(message)}</p>" if message else ""


def _password_field(new: bool, label: str = "Password", name: str = "password") -> str:
    hint = f" <span class='hint'>At least {MIN_PASSWORD} characters</span>" if new else ""
    return (f"<label for='{name}'>{label}{hint}</label>"
            f"<input id='{name}' name='{name}' type='password' required minlength='{MIN_PASSWORD if new else 1}' "
            f"autocomplete='{'new-password' if new else 'current-password'}'>")


def login_page(*, error: str | None = None, email: str = "", next_: str = "/app", signup_open: bool = True,
               notice: str | None = None) -> str:
    q = f"?next={quote(next_, safe='')}" if next_ != "/app" else ""
    body = f"""<h1>Sign in</h1>
<p class='lede'>Open your team's project record.</p>
{f"<p class='notice ok' role='status'>{e(notice)}</p>" if notice else ""}{_error(error)}
<form class='form' method='post' action='/login'>
<input type='hidden' name='next' value='{e(next_)}'>
<label for='email'>Email</label>
<input id='email' name='email' type='email' required autocomplete='email' value='{e(email)}' autofocus>
{_password_field(False)}
<div class='actions'><button class='btn primary' type='submit'>Sign in</button></div>
</form>
<p class='alt'><a href='/forgot'>Forgot your password?</a>{f" · New here? <a href='/signup{q}'>Create an account</a>" if signup_open else ""}</p>"""
    return shell("Sign in", body)


def signup_page(*, error: str | None = None, email: str = "", name: str = "", workspace: str = "",
                next_: str = "/app", needs_code: bool = False, invitation: dict[str, Any] | None = None,
                invitation_token: str = "", closed: bool = False) -> str:
    if closed and not invitation:
        return shell("Create an account", """<h1>Sign-up is by invitation</h1>
<p class='lede'>New accounts on this server are created from a workspace invitation. Ask a teammate who already
uses it to invite you, then follow the link they send.</p>
<p class='alt'><a href='/login'>Sign in</a></p>""")
    q = f"?next={quote(next_, safe='')}" if next_ != "/app" else ""
    if invitation:
        lede = (f"You were invited to <strong>{e(invitation['workspace'])}</strong> as {e(invitation['role'])}. "
                "Create your account to join.")
        email_field = (f"<label for='email'>Email</label><input id='email' name='email' type='email' readonly "
                       f"value='{e(invitation['email'])}'>")
        workspace_field = ""
    else:
        lede = "One shared record of what your project has decided, for every AI tool your team uses."
        email_field = (f"<label for='email'>Work email</label><input id='email' name='email' type='email' required "
                       f"autocomplete='email' value='{e(email)}'>")
        workspace_field = ("<label for='workspace'>Workspace name <span class='hint'>Usually your team or company</span>"
                           f"</label><input id='workspace' name='workspace' maxlength='80' value='{e(workspace)}' "
                           "placeholder='Acme Platform Team'>")
    code = ("<label for='code'>Invite code</label><input id='code' name='code' required autocomplete='off'>"
            if needs_code and not invitation else "")
    body = f"""<h1>Create your account</h1>
<p class='lede'>{lede}</p>{_error(error)}
<form class='form' method='post' action='/signup'>
<input type='hidden' name='next' value='{e(next_)}'><input type='hidden' name='invitation' value='{e(invitation_token)}'>
<label for='name'>Your name</label>
<input id='name' name='name' required maxlength='80' autocomplete='name' value='{e(name)}' autofocus>
{email_field}
{_password_field(True)}
{workspace_field}{code}
<div class='actions'><button class='btn primary' type='submit'>{'Create account and join' if invitation else 'Create account'}</button></div>
</form>
<p class='alt'>Already have an account? <a href='/login{q}'>Sign in</a></p>"""
    return shell("Create an account", body)


def forgot_page(*, sent: bool = False, link: str | None = None, mail: bool = False, error: str | None = None) -> str:
    if sent:
        if link:  # a local server with no mail: the operator is the user
            extra = (f"<p class='notice ok'>This server has no outgoing mail configured, and it is running locally, "
                     f"so here is the link:<br><a href='{e(link)}'>{e(link)}</a></p>")
        elif mail:
            extra = "<p class='lede'>The link works once and expires in an hour.</p>"
        else:
            extra = ("<p class='notice'>This server has no outgoing mail configured. The link was recorded for the "
                     "server's operator, who can send it to you.</p>")
        return shell("Reset your password", "<h1>Check your email</h1><p class='lede'>If that address has an account, "
                     f"a link to choose a new password is on its way.</p>{extra}<p class='alt'><a href='/login'>Back to sign in</a></p>")
    body = f"""<h1>Reset your password</h1>
<p class='lede'>Enter your email and we will send a link to choose a new one.</p>{_error(error)}
<form class='form' method='post' action='/forgot'>
<label for='email'>Email</label><input id='email' name='email' type='email' required autocomplete='email' autofocus>
<div class='actions'><button class='btn primary' type='submit'>Send the link</button></div>
</form><p class='alt'><a href='/login'>Back to sign in</a></p>"""
    return shell("Reset your password", body)


def reset_page(token: str, *, error: str | None = None) -> str:
    body = f"""<h1>Choose a new password</h1>
<p class='lede'>Changing it signs you out of other browsers and disconnects your AI apps, which will ask you to
sign in again.</p>{_error(error)}
<form class='form' method='post' action='/reset/{e(token)}'>
{_password_field(True, "New password")}
<div class='actions'><button class='btn primary' type='submit'>Set password</button></div>
</form>"""
    return shell("Choose a new password", body)


def message_page(title: str, heading: str, text: str, link: tuple[str, str] | None = None) -> str:
    tail = f"<p class='alt'><a href='{e(link[0])}'>{e(link[1])}</a></p>" if link else ""
    return shell(title, f"<h1>{e(heading)}</h1><p class='lede'>{e(text)}</p>{tail}")


def invite_page(token: str, inv: dict[str, Any], user: dict[str, Any] | None, *, error: str | None = None) -> str:
    head = (f"<h1>Join {e(inv['workspace'])}</h1><p class='lede'>{e(inv.get('invited_by') or 'A teammate')} invited "
            f"<strong>{e(inv['email'])}</strong> to this workspace as {e(inv['role'])}.</p>{_error(error)}")
    here = quote(f"/invite/{token}", safe="")
    if user and user["email"].lower() == inv["email"]:
        body = (f"{head}<form class='form' method='post' action='/invite/{e(token)}'><div class='actions'>"
                f"<button class='btn primary' type='submit'>Join as {e(user['email'])}</button></div></form>")
    elif user:
        body = (f"{head}<p class='notice'>You are signed in as {e(user['email'])}, but this invitation is for "
                f"{e(inv['email'])}. Sign out, then open the link again and sign in with that address.</p>"
                "<form class='form' method='post' action='/logout'><div class='actions'>"
                "<button class='btn' type='submit'>Sign out</button></div></form>")
    else:
        body = (f"{head}<div class='actions stack'><a class='btn primary' href='/signup?invitation={e(token)}'>"
                f"Create an account</a><a class='btn' href='/login?next={here}'>I already have an account</a></div>")
    return shell("Invitation", body)


def consent_page(req: dict[str, Any], client: dict[str, Any] | None, *, user: dict[str, Any] | None,
                 workspaces: list[dict[str, Any]], mode: str = "signin", needs_code: bool = False,
                 signup_open: bool = True, error: str | None = None, email: str = "") -> str:
    """Approve an AI app. A signed-in person picks the workspace and how much the app may do; otherwise they
    sign in (or create an account) on the same page."""
    client = client or {}
    # Any app can register under any name, so the page also shows where the person will be sent back to.
    app_name = e(client.get("client_name") or "An app")
    # hostname, not netloc: "https://claude.ai@evil.example/" must read as evil.example
    target = urlparse(req["params"]["redirect_uri"])
    host = (target.hostname or "") + (f":{target.port}" if target.hostname and target.port else "")
    back_to = e(host or f"the {target.scheme} app on this device")
    rid = e(req["id"])
    signup = mode == "signup" and signup_open
    if user:
        options = "".join(
            f"<option value='{e(w['id'])}'>{e(w['name'])} ({e(w['role'])})</option>" for w in workspaces)
        who = (f"<p class='signed'>Signed in as <strong>{e(user['email'])}</strong>.</p>"
               f"<label for='workspace'>Workspace</label><select id='workspace' name='workspace'>{options}</select>"
               "<fieldset class='choice'><legend>What may it do?</legend>"
               "<label><input type='radio' name='access' value='write' checked> Read and add to the record"
               "<span class='hint'>It can save and correct records. It can never delete or confirm them.</span></label>"
               "<label><input type='radio' name='access' value='read'> Read only"
               "<span class='hint'>It can read and check plans, and change nothing.</span></label></fieldset>")
        allow = "Allow access"
        other = ""
    else:
        code = ("<label for='code'>Invite code</label><input id='code' name='code' required autocomplete='off'>"
                if signup and needs_code else "")
        name = ("<label for='name'>Your name</label><input id='name' name='name' required maxlength='80' "
                "autocomplete='name'>" if signup else "")
        who = (f"<input type='hidden' name='mode' value='{'signup' if signup else 'signin'}'>{name}"
               f"<label for='email'>Email</label><input id='email' name='email' type='email' required "
               f"autocomplete='email' value='{e(email)}'>{_password_field(signup)}{code}")
        allow = "Create account and allow" if signup else "Sign in and allow"
        other = (f"<p class='alt'>Already have an account? <a href='/consent?request={rid}'>Sign in</a></p>" if signup
                 else f"<p class='alt'>New here? <a href='/consent?request={rid}&amp;mode=signup'>Create an account</a></p>"
                 if signup_open else "")
    body = f"""<h1>Connect {app_name}</h1>
<p class='lede'><strong>{app_name}</strong> is asking to use a {e(BRAND['name'])} workspace.</p>
<dl class='grant'>
<div><dt>It can</dt><dd>Read the workspace's projects and, if you allow it, add and correct records</dd></div>
<div><dt>It cannot</dt><dd>Delete anything, confirm records as checked by a person, or see other workspaces</dd></div>
<div><dt>Then</dt><dd>You go back to <strong>{back_to}</strong></dd></div>
</dl>{_error(error)}
<form class='form' method='post' action='/consent'>
<input type='hidden' name='request' value='{rid}'>
{who}
<div class='actions split'><button class='btn' type='submit' name='action' value='deny' formnovalidate>Cancel</button>
<button class='btn primary' type='submit' name='action' value='allow'>{allow}</button></div>
</form>{other}"""
    return shell("Connect an app", body)


_PAGE_NAME = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)?$")


def render_site(name: str, values: dict[str, str]) -> str | None:
    """A public page from ``static/site/<name>.html`` inside the shared layout, with ``{{TOKENS}}`` filled in.

    Pages under ``docs/`` also get the documentation's side navigation; other inner pages get a single reading
    column. Files whose names start with an underscore are parts, not pages, and are never served on their own.
    """
    site = STATIC_DIR / "site"
    if not _PAGE_NAME.match(name):
        return None
    page = site / f"{name}.html"
    if not page.is_file():
        return None
    content = page.read_text(encoding="utf-8")
    title = BRAND["name"]
    if content.startswith("<!--title:"):
        first, _, content = content.partition("\n")
        title = first[len("<!--title:"):-3].strip()
    section = name.split("/")[0]
    if section == "docs":
        nav = (site / "docs" / "_nav.html").read_text(encoding="utf-8")
        here = "/docs" if name == "docs/index" else f"/{name}"
        nav = nav.replace(f'href="{here}"', f'href="{here}" aria-current="page"')
        content = (f'<div class="doc"><nav class="doc-nav" aria-label="Documentation">{nav}</nav>'
                   f'<article class="prose">{content}</article></div>')
    elif name != "index":
        content = f'<div class="doc single"><article class="prose">{content}</article></div>'
    out = (site / "_layout.html").read_text(encoding="utf-8").replace("{{CONTENT}}", content)
    for key, value in {**values, "TITLE": e(title), "PAGE": section}.items():
        out = out.replace("{{" + key + "}}", value)
    return out
