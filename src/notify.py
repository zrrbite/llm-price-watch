"""Notification payloads: issue body, and email.

The GitHub issue is created by the workflow with `gh` — this module only
builds the text, which keeps it testable without a token. Email is sent from
here because smtplib lives in Python anyway.

Email is deliberately inert until secrets exist. A repo with no SMTP
configuration logs a line and carries on green; the code path stays live and
tested so that adding the secrets is the only step needed to switch it on.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

import diffing

CLASS_HEADINGS = {
    diffing.PRICE_CHANGED: "Price changes",
    diffing.MODEL_ADDED: "Models added",
    diffing.MODEL_REMOVED: "Models removed",
    diffing.DETAIL_CHANGED: "Detail changes",
    diffing.ADVISORY_ADDED: "New notes",
    diffing.ADVISORY_CHANGED: "Changed notes",
    diffing.ADVISORY_REMOVED: "Withdrawn notes",
}

SITE_URL = "https://zrrbite.github.io/llm-price-watch/"


def issue_title(entries: list[dict], problems: list[dict], date: str) -> str:
    if problems:
        names = ", ".join(sorted({p.get("source", "?") for p in problems}))
        return f"Parser broken: {names} ({date})"
    count = len(entries)
    noun = "change" if count == 1 else "changes"
    vendors = sorted({_vendor(e) for e in entries})
    return f"{count} pricing {noun} — {', '.join(vendors)} ({date})"


def _vendor(entry: dict) -> str:
    return "Anthropic" if entry.get("source") == "anthropic" else "Copilot"


def _line(entry: dict) -> str:
    if entry.get("class") in {
        diffing.ADVISORY_ADDED,
        diffing.ADVISORY_CHANGED,
        diffing.ADVISORY_REMOVED,
    }:
        return f"- **{_vendor(entry)}** — {entry.get('text') or entry.get('summary', '')}"

    fields = entry.get("fields") or []
    if not fields:
        return f"- **{_vendor(entry)}** — {entry.get('summary', '')}"
    parts = [
        f"{f['field']} `{diffing.fmt_value(f.get('old'), f['field'])}` → "
        f"`{diffing.fmt_value(f.get('new'), f['field'])}`"
        for f in fields
    ]
    return f"- **{_vendor(entry)}** {entry.get('key', '')} — " + ", ".join(parts)


def issue_body(entries: list[dict], problems: list[dict], date: str) -> str:
    out: list[str] = []

    if problems:
        out.append("A source could not be parsed, so **nothing was committed for it**.")
        out.append("")
        out.append(
            "This is the guard against reporting a vendor page redesign as though "
            "every model had been deleted. The previous snapshot is untouched and "
            "still correct; the parser needs a look."
        )
        out.append("")
        for problem in problems:
            out.append(f"- **{problem.get('source', '?')}** — {problem.get('message', '')}")
        out.append("")

    if entries:
        out.append(f"Detected on {date}.")
        out.append("")
        grouped: dict[str, list[dict]] = {}
        for entry in entries:
            grouped.setdefault(entry.get("class", "?"), []).append(entry)
        for cls in diffing.CLASS_ORDER:
            if cls not in grouped:
                continue
            out.append(f"### {CLASS_HEADINGS.get(cls, cls)}")
            out.append("")
            out.extend(_line(e) for e in grouped[cls])
            out.append("")

    out.append(f"[Full changelog]({SITE_URL})")
    return "\n".join(out)


# --------------------------------------------------------------------------
# email


def email_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_TO"))


def send_email(subject: str, body: str) -> bool:
    """Send *body* as plain text. Returns False when SMTP is not configured."""
    if not email_configured():
        print("email: SMTP_HOST/SMTP_TO not set, skipping (this is not an error)")
        return False

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM") or user or f"llm-price-watch@{host}"
    recipients = [r.strip() for r in os.environ["SMTP_TO"].split(",") if r.strip()]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            if user and password:
                server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls(context=context)
            if user and password:
                server.login(user, password)
            server.send_message(message)

    print(f"email: sent to {len(recipients)} recipient(s)")
    return True
