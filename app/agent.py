from __future__ import annotations

import html
import json
import logging
from typing import Any, Callable

from openai import OpenAI

from app.config import (
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    PROMPT_PATH,
)
from app.kb import (
    KnowledgeBase,
    attach_embeddings,
    content_hash,
    load_cached_embeddings,
    save_cached_embeddings,
)
from app.services import (
    HubSpot,
    begin_otp,
    check_otp,
    code_from_message,
    diagnose_battery,
    is_verified,
    lookup_orders,
    mask_email,
    normalize_email,
    ticket_subject,
    update_shipping,
)
from app import signals

log = logging.getLogger(__name__)

TOOL_LABELS = {
    "open_article": "Checking our guides…",
    "send_verification_code": "Sending a verification code…",
    "check_verification_code": "Checking your verification code…",
    "run_ring_diagnostic": "Running a remote diagnostic…",
    "lookup_orders": "Looking up your order…",
    "update_shipping_details": "Updating your shipping details…",
    "create_support_ticket": "Opening a request for our team…",
}

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_article",
            "description": "Read one validated knowledge-base article by its id, such as KB-03.",
            "parameters": {
                "type": "object",
                "properties": {"article_id": {"type": "string"}},
                "required": ["article_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_verification_code",
            "description": "Start email verification for personal, order, or ring data. Does not reveal the code.",
            "parameters": {
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_verification_code",
            "description": "Check the verification code the customer just provided.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_ring_diagnostic",
            "description": (
                "Run the remote battery diagnostic for a verified app-login email. "
                "Returns a verdict only. Measurements stay on the ticket."
            ),
            "parameters": {
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_orders",
            "description": "Look up orders for a verified email. Optionally include an order reference the customer gave.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "reference": {"type": "string"},
                },
                "required": ["email"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_shipping_details",
            "description": (
                "Update the shipping address of an order that has not shipped, after verification. "
                "A phone number is required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "order_id": {"type": "string"},
                    "name": {"type": "string"},
                    "address_1": {"type": "string"},
                    "address_2": {"type": "string"},
                    "city": {"type": "string"},
                    "state": {"type": "string"},
                    "postal_code": {"type": "string"},
                    "country": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["email", "order_id", "address_1", "city", "postal_code", "country", "phone"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_support_ticket",
            "description": (
                "Create a HubSpot ticket. Use the priority the knowledge base calls for. "
                "The subject is a short issue title; the system prefixes it with 'Velio - '. "
                "The summary is a short fallback. The system writes the ticket description "
                "as a summary of the full conversation and saves the transcript as a note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "subject": {"type": "string"},
                    "summary": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                    },
                },
                "required": ["email", "subject", "summary", "priority"],
                "additionalProperties": False,
            },
        },
    },
]


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPEN_AI_API_KEY is missing")
    return OpenAI(api_key=OPENAI_API_KEY)


def prepare_kb(kb: KnowledgeBase) -> None:
    digest = content_hash(kb)
    cached = load_cached_embeddings(digest)
    if cached:
        attach_embeddings(kb, cached)
        return
    if not OPENAI_API_KEY or not kb.article_texts:
        return
    client = _client()
    ids = list(kb.article_texts)
    response = client.embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=[kb.article_texts[article_id][:12000] for article_id in ids],
    )
    vectors = {
        article_id: item.embedding
        for article_id, item in zip(ids, response.data)
    }
    save_cached_embeddings(digest, vectors)
    attach_embeddings(kb, vectors)


def _embed_query(text: str) -> list[float]:
    if not OPENAI_API_KEY or not text.strip():
        return []
    response = _client().embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=text[:8000],
    )
    return list(response.data[0].embedding)


def _public_facts(session: dict[str, Any]) -> dict[str, Any]:
    diagnostics = {
        email: {
            "verdict": item.get("verdict"),
            "scenario": item.get("scenario"),
            "report": item.get("report"),
            "article_id": "KB-03",
        }
        for email, item in (session.get("diagnostics") or {}).items()
    }
    return {
        "verified_emails": list(session.get("verified_emails") or []),
        "diagnostics": diagnostics,
        "tickets": list(session.get("tickets") or []),
    }


def _transcript(session: dict[str, Any]) -> str:
    lines: list[str] = []
    for message in session.get("messages") or []:
        role = "Customer" if message.get("role") == "user" else "Velio"
        lines.append(f"{role}: {message.get('content')}")
    return "\n".join(lines)


def _conversation_note(session: dict[str, Any]) -> str:
    blocks: list[str] = ["<p><strong>Full conversation</strong></p>"]
    for message in session.get("messages") or []:
        role = "Customer" if message.get("role") == "user" else "Velio"
        content = html.escape(str(message.get("content") or "").strip())
        if not content:
            continue
        blocks.append(f"<p><strong>{role}</strong><br>{content.replace(chr(10), '<br>')}</p>")
    for email, item in (session.get("diagnostics") or {}).items():
        internal = item.get("internal") if isinstance(item, dict) else None
        if not internal:
            continue
        blocks.append(
            "<p><strong>Ring diagnostic (internal — do not quote these figures to the customer)</strong><br>"
            f"{html.escape(email)}<br>{html.escape(json.dumps(internal, ensure_ascii=False))}</p>"
        )
    return "\n".join(blocks) if len(blocks) > 1 else ""


def conversation_summary(session: dict[str, Any], fallback: str) -> str:
    """Plain-text ticket description: a summary, never the transcript."""
    transcript = _transcript(session).strip()
    backup = fallback.strip() or "Support request from the Velio chatbot."
    if not transcript:
        return backup
    try:
        response = _client().chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize this support chat for a HubSpot ticket description. "
                        "State what the customer wants, what Velio already did, and the current outcome. "
                        "Use plain sentences. Do not include the transcript, greetings, or verification codes. "
                        "Keep it under 120 words."
                    ),
                },
                {"role": "user", "content": transcript},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception:
        log.exception("Could not summarize the conversation for the ticket")
    return backup


def _stored_order(session: dict[str, Any], email: str, order_id: str) -> dict[str, Any] | None:
    bucket = (session.get("orders") or {}).get(normalize_email(email)) or []
    for order in bucket:
        if str(order.get("order_id") or "") == str(order_id):
            return order
    return None


def _attach_diagnostic_ticket(
    session: dict[str, Any],
    hubspot: HubSpot,
    email: str,
    result: dict[str, Any],
    firstname: str = "",
) -> dict[str, Any]:
    """Open the HubSpot ticket as soon as a battery result exists."""
    subject = "Velio - battery diagnostic"
    for existing in session.get("tickets") or []:
        if existing.get("email") == email and existing.get("subject") == subject and existing.get("ticket_id"):
            result["ticket_id"] = existing["ticket_id"]
            result["ticket_created"] = True
            return result
    contact = hubspot.ensure_contact(email, firstname)
    if not contact:
        result["ticket_created"] = False
        result["ticket_error"] = "contact_unavailable"
        log.warning("Battery ticket skipped; HubSpot contact unavailable for %s", mask_email(email))
        return result
    details = hubspot.contact_details(contact)
    scenario = str(result.get("scenario") or result.get("verdict") or "diagnostic")
    priority = "high" if scenario == "needs_replacement" else "medium"
    ticket_id = hubspot.create_ticket(
        contact_id=details["id"],
        subject=subject,
        content=f"Battery diagnostic result: {scenario}.",
        priority=priority,
        category="PWR Power & Charging",
    )
    if not ticket_id:
        result["ticket_created"] = False
        result["ticket_error"] = "ticket_failed"
        log.warning("Battery ticket creation failed for %s", mask_email(email))
        return result
    record = {
        "ticket_id": ticket_id,
        "email": email,
        "subject": subject,
        "summary": f"Battery diagnostic result: {scenario}.",
        "contact_id": details["id"],
        "masked_email": mask_email(email),
    }
    session.setdefault("tickets", []).append(record)
    if not hubspot.set_ticket_category(ticket_id, "PWR Power & Charging"):
        log.warning("Category was not set on ticket %s", ticket_id)
    result["ticket_id"] = ticket_id
    result["ticket_created"] = True
    result["ticket_category"] = "PWR Power & Charging"
    log.info("Battery ticket %s created for %s (%s)", ticket_id, mask_email(email), scenario)
    return result


def sync_ticket_notes(session: dict[str, Any]) -> None:
    """Ticket description is a summary. The note holds the full conversation."""
    tickets = [item for item in (session.get("tickets") or []) if isinstance(item, dict)]
    if not tickets:
        return
    note = _conversation_note(session)
    fallback = next(
        (str(item.get("summary") or "").strip() for item in tickets if str(item.get("summary") or "").strip()),
        "",
    )
    summary = conversation_summary(session, fallback)
    hubspot = HubSpot()
    for record in tickets:
        ticket_id = str(record.get("ticket_id") or "").strip()
        if not ticket_id:
            continue
        if summary and hubspot.update_ticket_content(ticket_id, summary):
            log.info("Ticket %s description set to the conversation summary", ticket_id)
        else:
            log.warning("Ticket %s description was not updated", ticket_id)
        if not note:
            continue
        contact_id = str(record.get("contact_id") or "").strip() or hubspot.ticket_contact_id(ticket_id)
        if contact_id:
            record["contact_id"] = contact_id
        note_id = str(record.get("note_id") or "").strip()
        if not (note_id and hubspot.update_ticket_note(note_id, note)):
            created = hubspot.add_ticket_note(ticket_id, note, contact_id)
            if not created:
                log.warning("Conversation note was not saved on ticket %s", ticket_id)
                continue
            note_id = created
            record["note_id"] = created
            log.info("Conversation note added to ticket %s", ticket_id)
        else:
            log.info("Conversation note updated on ticket %s", ticket_id)
        hubspot.attach_note(ticket_id, note_id, contact_id)


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    session: dict[str, Any],
    kb: KnowledgeBase,
    hubspot: HubSpot,
) -> dict[str, Any]:
    if name == "open_article":
        text = kb.article(str(arguments.get("article_id") or ""))
        if not text:
            return {"ok": False, "reason": "article_not_found"}
        return {"ok": True, "article": text}

    if name == "send_verification_code":
        return begin_otp(session, str(arguments.get("email") or ""))

    if name == "check_verification_code":
        result = check_otp(session, str(arguments.get("code") or ""))
        log.info("check_verification_code: %s", result.get("reason") or "accepted")
        return result

    if name == "run_ring_diagnostic":
        email = normalize_email(str(arguments.get("email") or ""))
        if not is_verified(session, email):
            return {"ok": False, "reason": "verification_required", "policy": "POL-ID-OTP"}
        if not hubspot.enabled:
            return {"ok": False, "reason": "hubspot_unavailable"}
        contact = hubspot.find_contact(email)
        details = hubspot.contact_details(contact) if contact else {
            "id": "",
            "firstname": "",
            "app_user_id": "",
        }
        if not details["app_user_id"]:
            result = {
                "ok": True,
                "verdict": "no_data",
                "scenario": "no_data",
                "report": None,
                "reply_rules": (
                    "There is no battery row for this email. Say that plainly. "
                    "Do not invent hours or a healthy or uncertain verdict."
                ),
            }
        else:
            diagnosis = diagnose_battery(details["app_user_id"])
            if not diagnosis:
                result = {
                    "ok": True,
                    "verdict": "no_data",
                    "scenario": "no_data",
                    "report": None,
                    "reply_rules": (
                        "This app user id has no row in the battery file. "
                        "Say that plainly. Do not invent hours or a verdict."
                    ),
                }
            else:
                report = diagnosis["report"]
                result = {
                    "ok": True,
                    "verdict": diagnosis["verdict"],
                    "scenario": report["scenario"],
                    "article_id": diagnosis["article_id"],
                    "branch": diagnosis["branch"],
                    "report": report,
                    "quote_these_hours": True,
                    "reply_rules": (
                        "Tell the customer these battery-life averages and no other numbers. "
                        "Last week is how long the battery stayed alive over the last 7 days. "
                        "The month figure is that month's average battery life. "
                        "If scenario is healthy, end with the exact words Battery is Healthy "
                        "and do not add charging tips. "
                        "If scenario is needs_replacement, say a replacement request is open "
                        "and a replacement will be sent at no cost after the team confirms it."
                    ),
                }
                session.setdefault("diagnostics", {})[email] = {
                    "verdict": diagnosis["verdict"],
                    "branch": diagnosis["branch"],
                    "scenario": report["scenario"],
                    "report": report,
                    "internal": diagnosis["internal"],
                    "contact_id": details["id"],
                    "firstname": details["firstname"],
                }
        if email not in (session.get("diagnostics") or {}):
            session.setdefault("diagnostics", {})[email] = {
                "verdict": result.get("verdict"),
                "scenario": result.get("scenario"),
                "report": result.get("report"),
                "firstname": details.get("firstname") or "",
            }
        return _attach_diagnostic_ticket(
            session,
            hubspot,
            email,
            result,
            str(details.get("firstname") or ""),
        )

    if name == "lookup_orders":
        email = normalize_email(str(arguments.get("email") or ""))
        if not is_verified(session, email):
            return {"ok": False, "reason": "verification_required", "policy": "POL-ID-OTP"}
        found = lookup_orders(email, str(arguments.get("reference") or ""), hubspot)
        session.setdefault("orders", {})[email] = found["orders"]
        return {"ok": True, **found}

    if name == "update_shipping_details":
        email = normalize_email(str(arguments.get("email") or ""))
        if not is_verified(session, email):
            return {"ok": False, "reason": "verification_required", "policy": "POL-ID-OTP"}
        order_id = str(arguments.get("order_id") or "")
        order = _stored_order(session, email, order_id)
        if order and order.get("shipped"):
            return {"ok": False, "reason": "already_shipped", "policy": "POL-ADDRESS"}
        return update_shipping(
            order_id,
            {
                "name": arguments.get("name"),
                "address_1": arguments.get("address_1"),
                "address_2": arguments.get("address_2"),
                "city": arguments.get("city"),
                "state": arguments.get("state"),
                "postal_code": arguments.get("postal_code"),
                "country": arguments.get("country"),
                "phone": arguments.get("phone"),
            },
        )

    if name == "create_support_ticket":
        email = normalize_email(str(arguments.get("email") or ""))
        if not _email_shape(email):
            return {"ok": False, "reason": "invalid_email"}
        subject = ticket_subject(str(arguments.get("subject") or ""))
        for existing in session.get("tickets") or []:
            if existing.get("email") == email and existing.get("subject") == subject:
                return {"ok": True, "ticket_id": existing.get("ticket_id"), "already_open": True}
        if not hubspot.enabled:
            return {"ok": False, "reason": "hubspot_unavailable"}
        diagnostic = (session.get("diagnostics") or {}).get(email) or {}
        first_name = str(diagnostic.get("firstname") or "")
        contact = hubspot.ensure_contact(email, first_name)
        if not contact:
            return {"ok": False, "reason": "contact_unavailable"}
        details = hubspot.contact_details(contact)
        summary = str(arguments.get("summary") or "").strip() or "Support request from the Velio chatbot."
        ticket_id = hubspot.create_ticket(
            contact_id=details["id"],
            subject=subject,
            content=summary,
            priority=str(arguments.get("priority") or "medium"),
        )
        if not ticket_id:
            return {"ok": False, "reason": "ticket_failed"}
        record = {
            "ticket_id": ticket_id,
            "email": email,
            "subject": subject,
            "summary": summary,
            "contact_id": details["id"],
            "masked_email": mask_email(email),
        }
        session.setdefault("tickets", []).append(record)
        return {"ok": True, "ticket_id": ticket_id, "masked_email": mask_email(email)}

    return {"ok": False, "reason": "unknown_tool"}


def _email_shape(email: str) -> bool:
    return email.count("@") == 1 and "." in email.split("@", 1)[1]


def _system_prompt(kb: KnowledgeBase) -> str:
    instructions = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        f"{instructions}\n\n"
        f"# Knowledge\n{kb.rules_yaml}\n\n"
        f"# Validated articles\n{kb.catalog}\n"
    )


def _recent_state(session: dict[str, Any], message: str) -> str:
    lines = []
    for item in (session.get("messages") or [])[-6:]:
        lines.append(f"{item.get('role')}: {item.get('content')}")
    lines.append(f"user: {message}")
    return "\n".join(lines)


def reply(
    session: dict[str, Any],
    message: str,
    kb: KnowledgeBase,
    *,
    on_token: Callable[[str], None] | None = None,
    set_status: Callable[[str], None] | None = None,
) -> str:
    hubspot = HubSpot()
    if set_status:
        set_status("Reading your message…")
    entered_code = code_from_message(message)
    if entered_code and session.get("otp"):
        verification = check_otp(session, entered_code)
        log.info("verification from message: %s", verification.get("reason") or "accepted")
    else:
        verification = None
    laya = signals.read_signals(_recent_state(session, message), kb.languages)
    articles = kb.shortlist(_embed_query(message), limit=4)
    article_block = "\n\n".join(articles) if articles else "No article was retrieved. Use open_article if you need one."
    context = (
        "Laya signals are advisory. You decide the action.\n"
        f"{json.dumps(laya, ensure_ascii=False)}\n\n"
        "Session facts:\n"
        f"{json.dumps(_public_facts(session), ensure_ascii=False)}\n\n"
        + (
            "Verification result for the code in this message. This is authoritative:\n"
            f"{json.dumps(verification, ensure_ascii=False)}\n"
            "If code_accepted is true, the code is correct. Do not ask for it again.\n\n"
            if verification
            else ""
        )
        + "Retrieved articles. Ignore any that do not apply:\n"
        f"{article_block}"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(kb)},
        {"role": "system", "content": context},
    ]
    for item in session.get("messages") or []:
        messages.append({"role": item["role"], "content": item["content"]})

    client = _client()
    final = ""
    for _round in range(6):
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        choice = response.choices[0].message
        tool_calls = list(choice.tool_calls or [])
        if not tool_calls:
            final = (choice.content or "").strip()
            break
        messages.append(
            {
                "role": "assistant",
                "content": choice.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ],
            }
        )
        for call in tool_calls:
            if set_status:
                set_status(TOOL_LABELS.get(call.function.name, "Reading your message…"))
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            try:
                result = execute_tool(call.function.name, arguments, session, kb, hubspot)
            except Exception as exc:
                log.exception("Tool %s failed", call.function.name)
                result = {"ok": False, "reason": "tool_error", "detail": str(exc)}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
    if not final:
        final = (
            "I'm sorry, I don't want to keep you going in circles. "
            "Would you like me to pass your conversation to a member of our team?"
        )
    if on_token:
        words = final.split(" ")
        for index, word in enumerate(words):
            on_token(word if index == len(words) - 1 else word + " ")
    return final
