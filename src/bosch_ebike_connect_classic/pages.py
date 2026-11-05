"""HTML pages (sign-in, success, message, landing). English only. No JavaScript: the CSP forbids it, everything works with plain HTML."""

from __future__ import annotations

from html import escape

LANGS = ("en",)

_STYLE = """
:root{color-scheme:light dark;--bg:#eef1f4;--fg:#14181d;--card:#fff;--mut:#5b6572;--acc:#0a7a55;--acc2:#0d9468;
--accfg:#fff;--err:#b3261e;--errbg:#fdeceb;--okbg:#e7f6ef;--bd:#d9dee4;--chip:#eef1f4}
@media(prefers-color-scheme:dark){:root{--bg:#0d1013;--fg:#e8eaed;--card:#171b20;--mut:#9aa4b0;--acc:#3fb88c;--acc2:#58cfa3;
--accfg:#07261b;--err:#f2867f;--errbg:#33201f;--okbg:#14291f;--bd:#2a3037;--chip:#222830}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%;background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
min-height:100vh;padding:20px;display:-webkit-box;display:-ms-flexbox;display:flex;
-webkit-box-align:center;-ms-flex-align:center;align-items:center;-webkit-box-pack:center;-ms-flex-pack:center;justify-content:center}
main{display:block;background:var(--card);overflow-wrap:break-word;word-wrap:break-word;border:1px solid var(--bd);border-radius:18px;padding:32px 28px;max-width:440px;width:100%;
box-shadow:0 10px 30px rgba(0,0,0,.07)}
.logo{width:56px;height:56px;border-radius:16px;background:var(--acc);background:linear-gradient(135deg,var(--acc),var(--acc2));
display:-webkit-box;display:-ms-flexbox;display:flex;-webkit-box-align:center;-ms-flex-align:center;align-items:center;-webkit-box-pack:center;-ms-flex-pack:center;justify-content:center;margin:0 auto 16px}
.logo svg{width:32px;height:32px;stroke:var(--accfg);fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.logo.ok{width:64px;height:64px;border-radius:50%}
h1{font-size:1.3rem;line-height:1.3;margin:0 0 6px;text-align:center}
h2{font-size:.95rem;margin:1.4rem 0 .5rem}
p{color:var(--mut);margin:.4rem 0 1rem;font-size:.93rem}
.center{text-align:center}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:2px 12px;font-weight:600;
color:var(--fg);font-size:.9rem;max-width:100%;overflow:hidden;text-overflow:ellipsis;vertical-align:bottom}
ul.can{list-style:none;padding:0;margin:.8rem 0 1.2rem}
ul.can li{padding:3px 0 3px 26px;position:relative;font-size:.9rem;color:var(--mut)}
ul.can li:before{content:"";position:absolute;left:4px;top:.55em;width:11px;height:6px;
border-left:2px solid var(--acc);border-bottom:2px solid var(--acc);transform:rotate(-45deg)}
label{display:block;font-size:.85rem;font-weight:600;margin:1rem 0 .3rem}
input{width:100%;padding:12px;border:1px solid var(--bd);border-radius:10px;background:transparent;color:inherit;font:inherit}
input:focus{outline:2px solid var(--acc);outline-offset:1px;border-color:transparent}
.btn{display:block;margin-top:1.4rem;width:100%;padding:13px;border:0;border-radius:10px;background:var(--acc);
color:var(--accfg);font:inherit;font-weight:700;cursor:pointer;text-align:center;text-decoration:none}
.btn:hover{background:var(--acc2)}
.err{background:var(--errbg);color:var(--err);border-radius:10px;padding:10px 12px;font-size:.9rem;margin:1rem 0 0}
.note{background:var(--okbg);border-radius:12px;padding:12px 14px;font-size:.9rem;color:var(--fg);margin:1rem 0}
.box{border:1px solid var(--bd);border-radius:12px;padding:4px 16px 12px;margin-top:1.4rem}
.box ol{margin:.4rem 0 0;padding-left:1.2rem}
.box li{font-size:.88rem;color:var(--mut);margin:.45rem 0}
.box b,.box code{color:var(--fg)}
code{background:var(--chip);border-radius:6px;padding:1px 6px;font-size:.85em;word-break:break-all}
small{display:block;color:var(--mut);margin-top:1.3rem;font-size:.77rem;line-height:1.45}
.how{margin:0 0 .2rem;padding:0}
.how dt{font-weight:600;font-size:.9rem;margin:.9rem 0 .15rem}
.how dd{margin:0;font-size:.88rem;color:var(--mut)}
pre{margin:.4rem 0 0;padding:10px 12px;background:var(--chip);border-radius:8px;font-size:.8rem;
white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word;color:var(--fg);
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
"""

_BIKE = ('<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5.5" cy="16.5" r="3.5"/><circle cx="18.5" cy="16.5" r="3.5"/>'
         '<path d="M5.5 16.5 9 8h5l4.5 8.5M9 8 7.5 6H5M14 8l-3.2 8.5H5.5"/></svg>')
_CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'

T = {
    "en": {
        "signin_title": "Sign in to eBike Connect Classic",
        "h1": "Connect your eBike Connect account",
        "intro": "{who} asks for access to your Bosch eBike Connect <b>Classic</b> account (ebike-connect.com).",
        "app_default": "An application",
        "can1": "Read your rides, trips and statistics",
        "can2": "Read your bikes and components",
        "can3": "Read-only: nothing is changed or deleted on your account",
        "can4": "You can disconnect at any time",
        "email": "eBike Connect e-mail",
        "password": "Password",
        "submit": "Sign in and connect",
        "privacy": "Your password goes to ebike-connect.com once to sign in and is <b>not stored</b>. "
                   "Only the resulting session is kept, encrypted. Unofficial service, not affiliated with Bosch. "
                   "For the Classic system only (not eBike Flow / Smart System).",
        "e_fields": "Please enter your e-mail and password.",
        "e_bad": "E-mail or password not accepted by eBike Connect.",
        "e_many": "Too many attempts. Please wait a few minutes.",
        "e_down": "eBike Connect did not answer. Please try again in a moment.",
        "ok_title": "Connected",
        "ok_h1": "You are connected",
        "ok_sub": "{who} can now read your eBike Connect data.",
        "ok_acct": "Account: <b>{email}</b>",
        "ok_go": "Continue to {who}",
        "ok_wait": "You are sent back automatically in a few seconds. If nothing happens, use the button.",
        "out_h": "How to disconnect again",
        "out1": "<b>Ask your assistant:</b> “Disconnect my eBike account.” It runs the tool "
                "<code>disconnect_account</code> (with <code>confirm=true</code>), which deletes the session and "
                "tokens stored on this server.",
        "out2": "<b>Remove the connector</b> in your app (Claude: Settings → Connectors; Claude Code: "
                "<code>/mcp</code> → clear authentication).",
        "out3": "<b>Fully end the session at Bosch:</b> the portal does not revoke the old session on logout. "
                "Change your password on ebike-connect.com to invalidate it.",
        "msg_invalid": "Invalid request",
        "msg_expired": "Session expired",
        "msg_expired_t": "Please go back and try again.",
        "land_h1": "Bosch eBike Connect Classic MCP",
        "land_p": "Remote MCP server for the Bosch eBike Connect <b>Classic</b> portal (ebike-connect.com). "
                  "It works with any MCP client that supports remote servers (Streamable HTTP) and OAuth.",
        "land_url": "Server URL",
        "land_how": "Connect a client",
        "land_claude": "Settings → Connectors → Add custom connector → paste the URL",
        "land_cc": "run in a terminal:",
        "land_other": "Other clients (ChatGPT, Cursor, VS Code, MCP Inspector …)",
        "land_other_t": "Add a remote MCP server of type “Streamable HTTP” with the URL above. "
                        "The client discovers sign-in by itself (OAuth 2.1 + PKCE, dynamic client registration).",
        "land_note": "Read-only, unofficial, not affiliated with Bosch. Not for the Smart System / eBike Flow app. "
                     "To disconnect later, ask your assistant to run <code>disconnect_account</code>.",
    },
}


def pick_lang(accept_language: str = "") -> str:
    """UI is English only; kept so a language can be added later."""
    return "en"


def _page(title: str, body: str, lang: str = "en", head: str = "") -> str:
    return (f'<!DOCTYPE html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
            f'<meta http-equiv="X-UA-Compatible" content="IE=edge">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<meta name="color-scheme" content="light dark">\n<meta name="robots" content="noindex">\n{head}'
            f'<title>{escape(title)}</title>\n<style>{_STYLE}</style>\n</head>\n'
            f'<body>\n<main>{body}</main>\n</body>\n</html>\n')


def authorize_page(*, client_name: str, params: dict[str, str], csrf: str,
                   error: str = "", email: str = "", lang: str = "en", redirect_to: str = "") -> str:
    t = T[lang]
    hidden = "".join(f"<input type=hidden name='{escape(k)}' value='{escape(v)}'>"
                     for k, v in params.items())
    err = f'<div class="err" role="alert">{escape(error)}</div>' if error else ""
    who = f'<span class="chip">{escape(client_name or t["app_default"])}</span>'
    redirect_note = (f'<div class="note">After you sign in, your browser is sent to <b>{escape(redirect_to)}</b>. '
                     'The application name above is chosen by the application itself and is not verified. '
                     'Continue only if you started this connection yourself.</div>') if redirect_to else ""
    return _page(t["signin_title"], f"""
<div class="logo">{_BIKE}</div>
<h1>{t['h1']}</h1>
<p class="center">{t['intro'].format(who=who)}</p>
{redirect_note}<ul class="can"><li>{t['can1']}</li><li>{t['can2']}</li><li>{t['can3']}</li><li>{t['can4']}</li></ul>
<form method="post" action="">
{hidden}<input type=hidden name=csrf value='{escape(csrf)}'>
<label for=e>{t['email']}</label>
<input id=e name=email type=email autocomplete=username required value='{escape(email)}'>
<label for=p>{t['password']}</label>
<input id=p name=password type=password autocomplete=current-password required>
{err}
<button class="btn" type="submit">{t['submit']}</button>
</form>
<small>{t['privacy']}</small>""", lang)


def success_page(*, client_name: str, email: str, target: str, lang: str = "en", delay: int = 4) -> str:
    t = T[lang]
    who = f'<span class="chip">{escape(client_name or t["app_default"])}</span>'
    plain = escape(client_name or t["app_default"])
    refresh = f'<meta http-equiv="refresh" content="{delay};url={escape(target)}">\n'
    return _page(t["ok_title"], f"""
<div class="logo ok">{_CHECK}</div>
<h1>{t['ok_h1']}</h1>
<p class="center">{t['ok_sub'].format(who=who)}</p>
<div class="note">{t['ok_acct'].format(email=escape(email))}</div>
<a class="btn" href="{escape(target)}">{t['ok_go'].format(who=plain)}</a>
<p class="center" style="margin-top:.8rem;font-size:.8rem">{t['ok_wait']}</p>
<div class="box"><h2>{t['out_h']}</h2><ol><li>{t['out1']}</li><li>{t['out2']}</li><li>{t['out3']}</li></ol></div>""",
                 lang, refresh)


def done_page(lang: str = "en") -> str:
    """Shown by local helper/callback listeners after the client received the sign-in code."""
    return _page("Done", f'<div class="logo ok">{_CHECK}</div><h1>All done</h1>'
                 '<p class="center">Sign-in finished and the connection is active. You can close this tab '
                 'and return to your assistant.</p>', lang)


def message_page(title: str, text: str, lang: str = "en") -> str:
    return _page(title, f'<div class="logo">{_BIKE}</div><h1>{escape(title)}</h1><p class="center">{escape(text)}</p>', lang)


def landing_page(public_url: str, lang: str = "en") -> str:
    t = T[lang]
    url = escape(public_url) + "/mcp"
    return _page("Bosch eBike Connect Classic MCP", f"""
<div class="logo">{_BIKE}</div>
<h1>{t['land_h1']}</h1>
<p class="center">{t['land_p']}</p>
<h2>{t['land_url']}</h2>
<p><code>{url}</code></p>
<h2>{t['land_how']}</h2>
<dl class="how">
<dt>Claude</dt><dd>{t['land_claude']}</dd>
<dt>Claude Code</dt><dd>{t['land_cc']}<pre>claude mcp add --transport http bosch-ebike-connect-classic {url}</pre></dd>
<dt>{t['land_other']}</dt><dd>{t['land_other_t']}</dd>
</dl>
<small>{t['land_note']}</small>""", lang)
