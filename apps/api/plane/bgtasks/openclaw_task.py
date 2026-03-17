# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import logging
import uuid
from typing import Iterable

import requests
from celery import shared_task
from django.conf import settings

from plane.db.models import User
from plane.settings.redis import redis_instance
from plane.utils.exception_logger import log_exception


logger = logging.getLogger("plane.worker")
OPENCLAW_INBOX_CHECK_EVENT = "plane_inbox_check"
OPENCLAW_SESSIONS_SEND_TOOL = "sessions_send"


def openclaw_notifications_enabled() -> bool:
    return bool(getattr(settings, "OPENCLAW_GATEWAY_URL", ""))


def get_openclaw_cooldown_key(workspace_slug: str, receiver_id: str) -> str:
    return f"openclaw:inbox-check:{workspace_slug}:{receiver_id}"


def should_enqueue_openclaw_inbox_check(workspace_slug: str, receiver_id: str) -> bool:
    cooldown_seconds = getattr(settings, "OPENCLAW_NOTIFY_COOLDOWN_SECONDS", 5)
    if cooldown_seconds <= 0:
        return True

    try:
        redis_client = redis_instance()
        return bool(
            redis_client.set(
                get_openclaw_cooldown_key(workspace_slug=workspace_slug, receiver_id=receiver_id),
                "1",
                ex=cooldown_seconds,
                nx=True,
            )
        )
    except Exception as exc:
        log_exception(exc, warning=True)
        logger.warning("Failed to apply OpenClaw inbox check cooldown")
        return True


def is_valid_openclaw_agent_name(agent_name: str) -> bool:
    if not agent_name:
        return False

    # Keep agent names simple so we can safely derive session keys.
    return all(char.isalnum() or char in {"_", "-"} for char in agent_name)


def normalize_openclaw_agent_name(agent_name: str | None) -> str | None:
    normalized_agent_name = (agent_name or "").strip()
    if not normalized_agent_name or not is_valid_openclaw_agent_name(normalized_agent_name):
        return None
    return normalized_agent_name


def build_openclaw_session_key(agent_name: str) -> str:
    return f"agent:{agent_name}:main"


def build_openclaw_message(workspace_slug: str) -> str:
    return (
        "Plane notification: you have new inbox updates. "
        f"Please check your inbox. workspace={workspace_slug}"
    )


def resolve_openclaw_targets(receiver_ids: Iterable[str]) -> list[dict[str, str]]:
    unique_receiver_ids = sorted({str(receiver_id) for receiver_id in receiver_ids if receiver_id})
    if not unique_receiver_ids:
        return []

    targets = []
    receiver_profiles = User.objects.filter(id__in=unique_receiver_ids).values(
        "id",
        "profile__openclaw_notify_enabled",
        "profile__openclaw_agent_name",
    )

    for receiver_profile in receiver_profiles:
        if not receiver_profile["profile__openclaw_notify_enabled"]:
            continue

        normalized_agent_name = normalize_openclaw_agent_name(receiver_profile["profile__openclaw_agent_name"])
        if not normalized_agent_name:
            continue

        targets.append(
            {
                "receiver_id": str(receiver_profile["id"]),
                "session_key": build_openclaw_session_key(normalized_agent_name),
            }
        )

    return targets


def enqueue_openclaw_inbox_checks(workspace_slug: str, receiver_ids: Iterable[str]) -> None:
    if not openclaw_notifications_enabled() or not workspace_slug:
        return

    for target in resolve_openclaw_targets(receiver_ids):
        receiver_id = target["receiver_id"]
        if should_enqueue_openclaw_inbox_check(workspace_slug=workspace_slug, receiver_id=receiver_id):
            notify_openclaw_inbox_check.delay(
                workspace_slug=workspace_slug,
                receiver_id=receiver_id,
                session_key=target["session_key"],
            )


@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_backoff=5,
    max_retries=3,
    retry_jitter=True,
)
def notify_openclaw_inbox_check(self, workspace_slug: str, receiver_id: str, session_key: str) -> None:
    if not openclaw_notifications_enabled() or not workspace_slug or not receiver_id or not session_key:
        return

    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Plane/OpenClaw",
        "X-Plane-Delivery": delivery_id,
        "X-Plane-Event": OPENCLAW_INBOX_CHECK_EVENT,
    }

    if settings.OPENCLAW_GATEWAY_TOKEN:
        headers["Authorization"] = f"Bearer {settings.OPENCLAW_GATEWAY_TOKEN}"

    payload = {
        "tool": OPENCLAW_SESSIONS_SEND_TOOL,
        "args": {
            "sessionKey": session_key,
            "message": build_openclaw_message(workspace_slug=workspace_slug),
        },
    }

    try:
        response = requests.post(
            settings.OPENCLAW_GATEWAY_URL,
            headers=headers,
            json=payload,
            timeout=settings.OPENCLAW_GATEWAY_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException:
        raise
    except Exception as exc:
        log_exception(exc)
