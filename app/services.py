from __future__ import annotations

import csv
import io
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.config import (
    BATTERY_CSV_PATH,
    FLOSHIP_BASE_URL,
    FLOSHIP_TOKEN,
    HUBSPOT_BASE_URL,
    HUBSPOT_TOKEN,
    OTP_CODE,
    SHOPIFY_SHOP,
    SHOPIFY_TOKEN,
)

log = logging.getLogger(__name__)

HEALTHY_THRESHOLD_HOURS = 24.0
OTP_MAX_ATTEMPTS = 3
OTP_MAX_CODES_PER_HOUR = 3

MONTHS: list[tuple[int, str, str]] = [
    (10, "October", "avg_oct"),
    (11, "November", "avg_nov"),
    (12, "December", "avg_dec"),
    (1, "January", "avg_jan"),
    (2, "February", "avg_feb"),
    (3, "March", "avg_mar"),
    (4, "April", "avg_apr"),
    (5, "May", "avg_may"),
    (6, "June", "avg_jun"),
    (7, "July", "avg_jul"),
    (8, "August", "avg_aug"),
    (9, "September", "avg_sep"),
]

ORDER_NUMBER_PROPERTIES = [
    "order_number",
    "order_number__2_",
    "order_number_3",
    "order_number_4",
    "order_number_5",
    "order_number_6",
    "ordernumber_7",
    "order_number_8",
]
APP_USER_PROPERTIES = ["app_user_id", "appuserid", "velia_app_user_id"]
SHIPPED_STATUSES = {
    "shipped",
    "in_transit",
    "intransit",
    "delivered",
    "fulfilled",
    "partially_fulfilled",
    "out_for_delivery",
    "outfordelivery",
}


def normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def mask_email(email: str) -> str:
    cleaned = normalize_email(email)
    if "@" not in cleaned:
        return cleaned
    local, domain = cleaned.split("@", 1)
    if not local or not domain:
        return cleaned
    visible = local[: min(2, len(local))]
    hidden = max(1, len(local) - len(visible))
    return f"{visible}{'*' * hidden}@{domain}"


def ticket_subject(subject: str) -> str:
    """Every chatbot ticket title starts with 'Velio - '."""
    text = " ".join((subject or "").split())
    lowered = text.lower()
    if lowered.startswith("velio - "):
        rest = text[8:].strip()
    elif lowered.startswith("velio "):
        rest = text[6:].strip()
    elif lowered == "velio":
        rest = ""
    else:
        rest = text
    return f"Velio - {rest}" if rest else "Velio - support request"


def _email_ok(email: str) -> bool:
    cleaned = normalize_email(email)
    if cleaned.count("@") != 1:
        return False
    local, domain = cleaned.split("@", 1)
    return bool(local and domain and "." in domain and " " not in cleaned)


class HubSpot:
    def __init__(self) -> None:
        self.token = HUBSPOT_TOKEN
        self.base = HUBSPOT_BASE_URL

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        response = requests.request(
            method,
            f"{self.base}{path}",
            headers=self._headers(),
            timeout=30,
            **kwargs,
        )
        return response

    def find_contact(self, email: str) -> dict[str, Any] | None:
        body = {
            "properties": ["email", "firstname", "lastname"],
            "limit": 1,
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": normalize_email(email),
                        }
                    ]
                }
            ],
        }
        response = self._request("POST", "/crm/v3/objects/contacts/search", json=body)
        if not response.ok:
            log.warning("HubSpot contact search failed: %s", response.text[:300])
            return None
        results = (response.json() or {}).get("results") or []
        return results[0] if results else None

    def ensure_contact(self, email: str, first_name: str = "") -> dict[str, Any] | None:
        existing = self.find_contact(email)
        if existing:
            return existing
        properties: dict[str, str] = {"email": normalize_email(email)}
        if first_name.strip():
            properties["firstname"] = first_name.strip()
        response = self._request(
            "POST",
            "/crm/v3/objects/contacts",
            json={"properties": properties},
        )
        if not response.ok:
            log.warning("HubSpot contact create failed: %s", response.text[:300])
            return None
        return response.json()

    def contact_details(self, contact: dict[str, Any]) -> dict[str, str]:
        props = contact.get("properties") or {}
        contact_id = str(contact.get("id") or "")
        app_user_id = ""
        for name in APP_USER_PROPERTIES:
            app_user_id = str(props.get(name) or "").strip()
            if app_user_id:
                break
        if contact_id and not app_user_id:
            response = self._request(
                "GET",
                f"/crm/v3/objects/contacts/{contact_id}",
                params={"properties": ",".join(APP_USER_PROPERTIES + ORDER_NUMBER_PROPERTIES)},
            )
            if response.ok:
                props = (response.json() or {}).get("properties") or props
                for name in APP_USER_PROPERTIES:
                    app_user_id = str(props.get(name) or "").strip()
                    if app_user_id:
                        break
        references: list[str] = []
        for name in ORDER_NUMBER_PROPERTIES:
            value = str(props.get(name) or "").strip()
            if value and value not in references:
                references.append(value)
        return {
            "id": contact_id,
            "email": normalize_email(str(props.get("email") or "")),
            "firstname": str(props.get("firstname") or "").strip(),
            "app_user_id": app_user_id,
            "order_references": references,
        }

    def category_value(self, label: str) -> str:
        target = str(label or "").strip()
        if not target:
            return ""
        response = self._request("GET", "/crm/v3/properties/tickets/hs_ticket_category")
        if not response.ok:
            return target
        options = (response.json() or {}).get("options") or []
        for option in options:
            option_label = str(option.get("label") or "").strip()
            option_value = str(option.get("value") or "").strip()
            if target.lower() in {option_label.lower(), option_value.lower()}:
                return option_value or option_label
        return target

    def set_ticket_category(self, ticket_id: str, label: str) -> bool:
        value = self.category_value(label)
        if not ticket_id or not value:
            return False
        response = self._request(
            "PATCH",
            f"/crm/v3/objects/tickets/{ticket_id}",
            json={"properties": {"hs_ticket_category": value}},
        )
        if not response.ok:
            log.warning("HubSpot category update failed: %s", response.text[:300])
            return False
        return True

    def update_ticket_content(self, ticket_id: str, content: str) -> bool:
        text = str(content or "").strip()
        if not ticket_id or not text:
            return False
        response = self._request(
            "PATCH",
            f"/crm/v3/objects/tickets/{ticket_id}",
            json={"properties": {"content": text}},
        )
        if not response.ok:
            log.warning("HubSpot ticket description update failed: %s", response.text[:300])
            return False
        return True

    def ticket_contact_id(self, ticket_id: str) -> str:
        response = self._request("GET", f"/crm/v4/objects/tickets/{ticket_id}/associations/contacts")
        if not response.ok:
            return ""
        results = (response.json() or {}).get("results") or []
        if not results:
            return ""
        return str(results[0].get("toObjectId") or "")

    def attach_note(self, ticket_id: str, note_id: str, contact_id: str = "") -> None:
        """Link the note to the contact and pin it so it shows on the ticket."""
        if contact_id:
            linked = self._request(
                "PUT",
                f"/crm/v4/objects/notes/{note_id}/associations/contacts/{contact_id}",
                json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
            )
            if not linked.ok:
                log.warning("HubSpot note contact link failed: %s", linked.text[:300])
        pinned = self._request(
            "PATCH",
            f"/crm/v3/objects/tickets/{ticket_id}",
            json={"properties": {"hs_pinned_engagement_id": str(note_id)}},
        )
        if not pinned.ok:
            log.warning("HubSpot note pin failed: %s", pinned.text[:300])

    def add_ticket_note(self, ticket_id: str, note_body: str, contact_id: str = "") -> str | None:
        body = str(note_body or "").strip()
        if not ticket_id or not body:
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        associations = [
            {
                "to": {"id": str(ticket_id)},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 228,
                    }
                ],
            }
        ]
        if contact_id:
            associations.append(
                {
                    "to": {"id": str(contact_id)},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 202,
                        }
                    ],
                }
            )
        response = self._request(
            "POST",
            "/crm/v3/objects/notes",
            json={
                "properties": {
                    "hs_timestamp": timestamp,
                    "hs_note_body": body,
                },
                "associations": associations,
            },
        )
        if response.ok:
            return str((response.json() or {}).get("id") or "") or None
        log.warning("HubSpot note create failed: %s", response.text[:300])
        legacy_associations: dict[str, Any] = {
            "ticketIds": [int(ticket_id)] if str(ticket_id).isdigit() else [ticket_id],
        }
        if contact_id and str(contact_id).isdigit():
            legacy_associations["contactIds"] = [int(contact_id)]
        legacy = self._request(
            "POST",
            "/engagements/v1/engagements",
            json={
                "engagement": {"active": True, "type": "NOTE"},
                "associations": legacy_associations,
                "metadata": {"body": body},
            },
        )
        if not legacy.ok:
            log.warning("HubSpot note fallback failed: %s", legacy.text[:300])
            return None
        engagement = (legacy.json() or {}).get("engagement") or {}
        return str(engagement.get("id") or "") or None

    def update_ticket_note(self, note_id: str, note_body: str) -> bool:
        body = str(note_body or "").strip()
        if not note_id or not body:
            return False
        response = self._request(
            "PATCH",
            f"/crm/v3/objects/notes/{note_id}",
            json={"properties": {"hs_note_body": body}},
        )
        if response.ok:
            return True
        legacy = self._request(
            "PATCH",
            f"/engagements/v1/engagements/{note_id}",
            json={"metadata": {"body": body}},
        )
        if legacy.ok:
            return True
        log.warning("HubSpot note update failed: %s", response.text[:300])
        return False

    def create_ticket(
        self,
        *,
        contact_id: str,
        subject: str,
        content: str,
        priority: str,
        category: str = "",
    ) -> str | None:
        mapped = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "urgent": "HIGH"}.get(
            priority.lower(), "MEDIUM"
        )
        properties = {
            "subject": ticket_subject(subject),
            "content": content.strip(),
            "hs_pipeline": "0",
            "hs_pipeline_stage": "1",
            "hs_ticket_priority": mapped,
        }
        if category:
            properties["hs_ticket_category"] = self.category_value(category)
        body: dict[str, Any] = {"properties": properties}
        if contact_id:
            body["associations"] = [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 16,
                        }
                    ],
                }
            ]
        response = self._request("POST", "/crm/v3/objects/tickets", json=body)
        if not response.ok and properties.get("hs_ticket_category"):
            properties.pop("hs_ticket_category", None)
            body["properties"] = properties
            response = self._request("POST", "/crm/v3/objects/tickets", json=body)
        if not response.ok and contact_id:
            response = self._request(
                "POST",
                "/crm/v3/objects/tickets",
                json={"properties": properties},
            )
            if response.ok:
                ticket_id = str((response.json() or {}).get("id") or "")
                if ticket_id:
                    self._request(
                        "PUT",
                        f"/crm/v4/objects/tickets/{ticket_id}/associations/contacts/{contact_id}",
                        json=[
                            {
                                "associationCategory": "HUBSPOT_DEFINED",
                                "associationTypeId": 16,
                            }
                        ],
                    )
                return ticket_id or None
        if not response.ok:
            log.warning("HubSpot ticket create failed: %s", response.text[:400])
            return None
        return str((response.json() or {}).get("id") or "") or None


def begin_otp(session: dict[str, Any], email: str) -> dict[str, Any]:
    target = normalize_email(email)
    if not _email_ok(target):
        return {"ok": False, "reason": "invalid_email"}
    verified = {normalize_email(item) for item in session.get("verified_emails") or []}
    if target in verified:
        return {"ok": True, "already_verified": True, "email": target, "masked_email": mask_email(target)}

    now = time.time()
    pending = dict(session.get("otp") or {})
    sent_at = [float(item) for item in pending.get("sent_at") or [] if now - float(item) < 3600]
    if target == pending.get("email") and len(sent_at) >= OTP_MAX_CODES_PER_HOUR:
        return {
            "ok": False,
            "reason": "too_many_codes",
            "policy": "POL-ID-EDGE-CASES",
            "masked_email": mask_email(target),
        }
    sent_at.append(now)
    session["otp"] = {
        "email": target,
        "code": OTP_CODE,
        "attempts": 0,
        "sent_at": sent_at,
    }
    return {
        "ok": True,
        "already_verified": False,
        "email": target,
        "masked_email": mask_email(target),
        "policy": "POL-ID-OTP",
    }


def normalize_code(code: str) -> str:
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not digits:
        return ""
    if len(digits) < 6:
        digits = digits.zfill(6)
    return digits


def code_from_message(message: str) -> str:
    text = str(message or "").strip()
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    import re

    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    return match.group(1) if match else ""


def check_otp(session: dict[str, Any], code: str) -> dict[str, Any]:
    pending = dict(session.get("otp") or {})
    email = normalize_email(str(pending.get("email") or ""))
    if not email:
        last = session.get("last_verification") or {}
        if last.get("ok"):
            return {**last, "already_verified": True}
        return {"ok": False, "reason": "no_pending_code"}
    attempts = int(pending.get("attempts") or 0)
    if attempts >= OTP_MAX_ATTEMPTS:
        return {
            "ok": False,
            "reason": "code_exhausted",
            "policy": "POL-ID-EDGE-CASES",
            "masked_email": mask_email(email),
        }
    supplied = normalize_code(code)
    expected = normalize_code(str(pending.get("code") or ""))
    if supplied != expected:
        attempts += 1
        pending["attempts"] = attempts
        session["otp"] = pending
        if attempts >= OTP_MAX_ATTEMPTS:
            return {
                "ok": False,
                "reason": "code_exhausted",
                "policy": "POL-ID-EDGE-CASES",
                "masked_email": mask_email(email),
            }
        return {
            "ok": False,
            "reason": "wrong_code",
            "attempts_remaining": OTP_MAX_ATTEMPTS - attempts,
            "masked_email": mask_email(email),
        }
    verified = [normalize_email(item) for item in session.get("verified_emails") or []]
    if email not in verified:
        verified.append(email)
    session["verified_emails"] = verified
    session.pop("otp", None)
    result = {"ok": True, "email": email, "masked_email": mask_email(email), "code_accepted": True}
    session["last_verification"] = result
    return result


def is_verified(session: dict[str, Any], email: str) -> bool:
    target = normalize_email(email)
    return target in {normalize_email(item) for item in session.get("verified_emails") or []}


def _parse_hours(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text.upper() == "NO DATA":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_battery_rows() -> list[dict[str, str]]:
    if not BATTERY_CSV_PATH.is_file():
        return []
    lines = BATTERY_CSV_PATH.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        return []
    start = 0
    if lines[0].strip().lower().startswith("sep="):
        delimiter = lines[0].split("=", 1)[1].strip().strip('"') or ","
        start = 1
    else:
        delimiter = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter=delimiter)
    rows: list[dict[str, str]] = []
    for row in reader:
        normalized = {
            str(key or "").strip().strip('"').lower(): value for key, value in row.items()
        }
        user_id = str(normalized.get("user_id") or "").strip().lower()
        if user_id:
            rows.append(normalized)
    return rows


_battery_cache: dict[str, Any] = {"mtime": None, "rows": None}


def battery_rows() -> list[dict[str, str]]:
    try:
        mtime = BATTERY_CSV_PATH.stat().st_mtime
    except OSError:
        return []
    if _battery_cache["rows"] is not None and _battery_cache["mtime"] == mtime:
        return _battery_cache["rows"]
    rows = _load_battery_rows()
    _battery_cache["mtime"] = mtime
    _battery_cache["rows"] = rows
    return rows


def _column_for_prefix(row: dict[str, str], prefix: str) -> str | None:
    if prefix in row:
        return prefix
    matches = [key for key in row if key.startswith(prefix + "_")]
    return sorted(matches)[-1] if matches else None


def _year_from_column(column: str | None) -> int | None:
    if not column:
        return None
    tail = column.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() and len(tail) == 4 else None


def _analyze_row(row: dict[str, str]) -> dict[str, Any]:
    month_hours: dict[str, float] = {}
    readings: list[dict[str, Any]] = []
    below = 0
    for _month, label, prefix in MONTHS:
        column = _column_for_prefix(row, prefix)
        hours = _parse_hours(row.get(column)) if column else None
        if hours is None:
            continue
        year = _year_from_column(column)
        month_hours[label] = hours
        readings.append({"label": label, "year": year, "hours": hours})
        if hours < HEALTHY_THRESHOLD_HOURS:
            below += 1
    last_7 = _parse_hours(row.get("avg_last_7_days"))
    if last_7 is not None and last_7 < HEALTHY_THRESHOLD_HOURS:
        below += 1
    latest = readings[-1] if readings else None
    latest_hours = latest["hours"] if latest else None
    latest_label = None
    if latest:
        latest_label = f"{latest['label']} {latest['year']}" if latest.get("year") else latest["label"]
    healthy = (
        last_7 is not None
        and last_7 > HEALTHY_THRESHOLD_HOURS
        and latest_hours is not None
        and latest_hours > HEALTHY_THRESHOLD_HOURS
    )
    needs_replacement = (
        not healthy
        and last_7 is not None
        and last_7 < HEALTHY_THRESHOLD_HOURS
        and latest_hours is not None
        and latest_hours < HEALTHY_THRESHOLD_HOURS
        and below >= 2
    )
    if healthy:
        scenario = "healthy"
    elif needs_replacement:
        scenario = "needs_replacement"
    elif last_7 is None and latest_hours is not None:
        scenario = "missing_last_week"
    elif last_7 is None and latest_hours is None:
        scenario = "no_data"
    else:
        scenario = "inconclusive"
    return {
        "ring_size": str(row.get("ring_size") or "").strip(),
        "last_7_hours": last_7,
        "last_month_label": latest_label,
        "last_month_hours": latest_hours,
        "month_hours": month_hours,
        "below_threshold_count": below,
        "healthy": healthy,
        "needs_replacement": needs_replacement,
        "scenario": scenario,
    }


def diagnose_battery(app_user_id: str) -> dict[str, Any] | None:
    needle = str(app_user_id or "").strip().lower()
    if not needle:
        return None
    rows = [row for row in battery_rows() if str(row.get("user_id") or "").strip().lower() == needle]
    if not rows:
        return None
    analyses = [_analyze_row(row) for row in rows]
    primary = max(
        analyses,
        key=lambda item: (
            item["last_7_hours"] is not None,
            item["last_7_hours"] or 0,
            len(item["month_hours"]),
        ),
    )
    scenario = primary["scenario"]
    if scenario == "healthy":
        verdict = "ok"
        branch = "battery_ok"
    elif scenario == "needs_replacement":
        verdict = "not_ok"
        branch = "battery_not_ok"
    else:
        verdict = scenario
        branch = "main"
    report = {
        "scenario": scenario,
        "last_week_average_battery_life_hours": primary["last_7_hours"],
        "month_label": primary["last_month_label"],
        "month_average_battery_life_hours": primary["last_month_hours"],
    }
    return {
        "verdict": verdict,
        "branch": branch,
        "article_id": "KB-03",
        "report": report,
        "internal": {
            "app_user_id": needle,
            "ring_size": primary["ring_size"],
            "last_7_hours": primary["last_7_hours"],
            "last_month_label": primary["last_month_label"],
            "last_month_hours": primary["last_month_hours"],
            "below_threshold_count": primary["below_threshold_count"],
            "month_hours": primary["month_hours"],
        },
    }


def _floship_headers() -> dict[str, str]:
    return {
        "Authorization": f"Token {FLOSHIP_TOKEN}",
        "Accept": "application/json",
    }


def _floship_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
    if not FLOSHIP_TOKEN:
        return None
    response = requests.get(
        f"{FLOSHIP_BASE_URL}{path}",
        headers=_floship_headers(),
        params=params,
        timeout=30,
    )
    if not response.ok:
        log.warning("Floship request failed %s: %s", path, response.status_code)
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def _public_order(order: dict[str, Any], source: str) -> dict[str, Any]:
    address = order.get("shipping_address") or {}
    if not isinstance(address, dict):
        address = {}
    lines = []
    for line in order.get("order_lines") or order.get("line_items") or []:
        if not isinstance(line, dict):
            continue
        lines.append(
            {
                "description": line.get("description") or line.get("title") or line.get("name"),
                "sku": line.get("sku") or line.get("item_sku"),
                "quantity": line.get("quantity"),
            }
        )
    tracking = str(order.get("tracking_number") or "").strip()
    tracking_url = str(order.get("tracking_url") or "").strip()
    if not tracking_url and tracking:
        tracking_url = f"https://track.floship.com/?trackingId={tracking}"
    status = str(order.get("status") or order.get("fulfillment_status") or "").strip()
    tracking_status = str(order.get("tracking_status") or "").strip()
    shipped = bool(tracking) or status.lower() in SHIPPED_STATUSES or tracking_status.lower() in SHIPPED_STATUSES
    return {
        "source": source,
        "order_id": str(order.get("order_id") or order.get("id") or ""),
        "reference": str(order.get("customer_reference") or order.get("reference") or order.get("name") or ""),
        "status": status,
        "tracking_status": tracking_status,
        "tracking_number": tracking or None,
        "tracking_link": tracking_url or None,
        "shipped": shipped,
        "created_at": order.get("create_date") or order.get("created_at"),
        "items": lines[:8],
        "shipping_address": {
            "name": address.get("addressee") or address.get("name"),
            "address_1": address.get("address_1") or address.get("address1"),
            "address_2": address.get("address_2") or address.get("address2"),
            "city": address.get("city"),
            "state": address.get("state") or address.get("province"),
            "postal_code": address.get("postal_code") or address.get("zip"),
            "country": address.get("country") or address.get("country_code"),
            "phone": address.get("phone"),
        },
    }


def _lookup_floship_reference(reference: str) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=540)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = _floship_get(
        "/orders",
        {"customer_reference": reference, "update_date_after": cutoff, "page_size": "10"},
    )
    results = (payload or {}).get("results") or []
    return [item for item in results if isinstance(item, dict)]


def _lookup_floship_email(email: str) -> list[dict[str, Any]]:
    target = normalize_email(email)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=540)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{FLOSHIP_BASE_URL}/orders"
    params: dict[str, str] | None = {"update_date_after": cutoff, "page_size": "100"}
    matches: list[dict[str, Any]] = []
    for _page in range(4):
        response = requests.get(url, headers=_floship_headers(), params=params, timeout=30)
        if not response.ok:
            break
        payload = response.json()
        for order in payload.get("results") or []:
            if not isinstance(order, dict):
                continue
            address = order.get("shipping_address") or {}
            if normalize_email(str(address.get("email") or "")) == target:
                matches.append(order)
        url = str(payload.get("next") or "")
        params = None
        if matches or not url:
            break
    return matches[:3]


def _lookup_shopify_email(email: str) -> list[dict[str, Any]]:
    if not SHOPIFY_TOKEN or not SHOPIFY_SHOP:
        return []
    shop = SHOPIFY_SHOP if "." in SHOPIFY_SHOP else f"{SHOPIFY_SHOP}.myshopify.com"
    response = requests.get(
        f"https://{shop}/admin/api/2024-10/orders.json",
        headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN},
        params={"status": "any", "email": normalize_email(email), "limit": 5},
        timeout=30,
    )
    if not response.ok:
        log.warning("Shopify order lookup failed: %s", response.status_code)
        return []
    orders = (response.json() or {}).get("orders") or []
    public: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        fulfillment = (order.get("fulfillments") or [{}])[0] if order.get("fulfillments") else {}
        shaped = {
            "id": order.get("id"),
            "name": order.get("name"),
            "fulfillment_status": order.get("fulfillment_status") or "unfulfilled",
            "created_at": order.get("created_at"),
            "line_items": order.get("line_items") or [],
            "shipping_address": order.get("shipping_address") or {},
            "tracking_number": (fulfillment or {}).get("tracking_number"),
            "tracking_url": (fulfillment or {}).get("tracking_url"),
        }
        public.append(_public_order(shaped, "shopify"))
    return public


def lookup_orders(email: str, reference: str, hubspot: HubSpot) -> dict[str, Any]:
    target = normalize_email(email)
    orders: list[dict[str, Any]] = []
    seen: set[str] = set()
    references: list[str] = []
    if reference.strip():
        references.append(reference.strip())
    if hubspot.enabled:
        contact = hubspot.find_contact(target)
        if contact:
            references.extend(hubspot.contact_details(contact)["order_references"])
    if FLOSHIP_TOKEN:
        for ref in references:
            for order in _lookup_floship_reference(ref):
                key = str(order.get("order_id") or order.get("customer_reference") or "")
                if key and key in seen:
                    continue
                seen.add(key)
                orders.append(_public_order(order, "floship"))
        if not orders:
            for order in _lookup_floship_email(target):
                orders.append(_public_order(order, "floship"))
    if not orders:
        orders.extend(_lookup_shopify_email(target))
    return {"email": target, "orders": orders[:3], "found": bool(orders)}


def update_shipping(order_id: str, address: dict[str, Any]) -> dict[str, Any]:
    if not FLOSHIP_TOKEN or not str(order_id).strip():
        return {"ok": False, "reason": "order_update_unavailable"}
    phone = str(address.get("phone") or "").strip()
    if not phone:
        return {"ok": False, "reason": "phone_required", "policy": "POL-ADDRESS"}
    payload = {
        "shipping_address": {
            "address_1": address.get("address_1"),
            "address_2": address.get("address_2") or "",
            "city": address.get("city"),
            "state": address.get("state") or "",
            "postal_code": address.get("postal_code"),
            "country": address.get("country"),
            "phone": phone,
            "addressee": address.get("name") or "",
        }
    }
    response = requests.patch(
        f"{FLOSHIP_BASE_URL}/orders/{order_id}/",
        headers={**_floship_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if not response.ok:
        return {"ok": False, "reason": "update_failed", "status": response.status_code}
    return {"ok": True, "order_id": str(order_id)}
