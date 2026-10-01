from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_router = None
_lock = threading.Lock()
_unavailable_reason = ""


def _questions(languages: list[dict[str, str]]) -> dict[str, Any]:
    criteria = {
        item["code"]: f"the customer is writing in {item['name']}"
        for item in languages
        if item.get("code") and item.get("name")
    }
    criteria["other"] = "the customer is writing in a language that is not allowed"
    return {
        "reply_language": {
            "type": "choice",
            "instructions": "Which language is the customer writing in?",
            "criteria": criteria,
        },
        "needs_personal_data": {
            "type": "noul",
            "instructions": (
                "Does a correct answer need this customer's own order, address, "
                "account, or ring diagnostic data?"
            ),
        },
        "safety_issue": {
            "type": "noul",
            "instructions": (
                "Is the customer reporting that the ring burned, hurt, or irritated their skin, "
                "or became painfully hot while worn?"
            ),
        },
        "explicit_human": {
            "type": "noul",
            "instructions": "Is the customer explicitly asking to talk to a person on the team?",
        },
        "strong_frustration": {
            "type": "noul",
            "instructions": (
                "Is the customer strongly frustrated, insulting, threatening a chargeback, "
                "lawyer, or public review, or saying they have already asked several times?"
            ),
        },
        "in_scope": {
            "type": "noul",
            "instructions": (
                "Is this about the Velia ring, the Velia app, an order, shipping, "
                "returns, or Velia support?"
            ),
        },
    }


def warmup(languages: list[dict[str, str]]) -> None:
    read_signals("Hello", languages)


def _ensure_router(languages: list[dict[str, str]]) -> Any:
    """Load Laya once. A chat turn never waits on the download."""
    global _router, _unavailable_reason
    if _router is not None:
        return _router
    if _unavailable_reason:
        return None
    if not _lock.acquire(blocking=False):
        return None
    try:
        if _router is None:
            from laya import Router

            router = Router()
            router.predict("Hello", _questions(languages))
            _router = router
    except Exception as exc:
        _unavailable_reason = str(exc)
        log.warning("Laya could not be loaded: %s", exc)
        return None
    finally:
        _lock.release()
    return _router


def read_signals(state: str, languages: list[dict[str, str]]) -> dict[str, Any]:
    """Calibrated Laya decisions. Callers must not branch the conversation on them."""
    text = (state or "").strip()
    if not text:
        return {"available": False, "reason": "empty"}
    router = _ensure_router(languages)
    if router is None:
        return {"available": False, "reason": _unavailable_reason or "loading"}
    try:
        result = router.predict(text, _questions(languages))
    except Exception as exc:
        log.warning("Laya signal read failed: %s", exc)
        return {"available": False, "reason": str(exc)}

    answers = result.get("answers") or {}
    routing = result.get("routing") or {}

    def choice(name: str) -> dict[str, Any]:
        payload = answers.get(name) or {}
        return {
            "choice": payload.get("choice"),
            "confidence": payload.get("confidence") or payload.get("answer_confidence"),
        }

    def noul(name: str) -> float | None:
        payload = answers.get(name) or {}
        value = payload.get("noul")
        if value is None:
            return None
        try:
            return round(float(value), 3)
        except (TypeError, ValueError):
            return None

    return {
        "available": True,
        "model": routing.get("model"),
        "reply_language": choice("reply_language"),
        "needs_personal_data": noul("needs_personal_data"),
        "safety_issue": noul("safety_issue"),
        "explicit_human": noul("explicit_human"),
        "strong_frustration": noul("strong_frustration"),
        "in_scope": noul("in_scope"),
    }
