# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import logging
import uuid
from typing import Iterable

import requests
from celery import shared_task
from django.conf import settings

from plane.settings.redis import redis_instance
from plane.utils.exception_logger import log_exception


logger = logging.getLogger("plane.worker")
OPENCLAW_INBOX_CHECK_EVENT = "plane_inbox_check"


def openclaw_notifications_enabled() -> bool:
    return bool(getattr(settings, "OPENCLAW_NOTIFY_URL", ""))


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


def enqueue_openclaw_inbox_checks(workspace_slug: str, receiver_ids: Iterable[str]) -> None:
    if not openclaw_notifications_enabled() or not workspace_slug:
        return

    unique_receiver_ids = sorted({str(receiver_id) for receiver_id in receiver_ids if receiver_id})

    for receiver_id in unique_receiver_ids:
        if should_enqueue_openclaw_inbox_check(workspace_slug=workspace_slug, receiver_id=receiver_id):
            notify_openclaw_inbox_check.delay(workspace_slug=workspace_slug, receiver_id=receiver_id)


@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_backoff=5,
    max_retries=3,
    retry_jitter=True,
)
def notify_openclaw_inbox_check(self, workspace_slug: str, receiver_id: str) -> None:
    if not openclaw_notifications_enabled() or not workspace_slug or not receiver_id:
        return

    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Plane/OpenClaw",
        "X-Plane-Delivery": delivery_id,
        "X-Plane-Event": OPENCLAW_INBOX_CHECK_EVENT,
    }

    if settings.OPENCLAW_NOTIFY_TOKEN:
        headers["Authorization"] = f"Bearer {settings.OPENCLAW_NOTIFY_TOKEN}"

    payload = {
        "type": OPENCLAW_INBOX_CHECK_EVENT,
        "workspace_slug": workspace_slug,
        "receiver_id": receiver_id,
        "delivery_id": delivery_id,
    }

    try:
        response = requests.post(
            settings.OPENCLAW_NOTIFY_URL,
            headers=headers,
            json=payload,
            timeout=settings.OPENCLAW_NOTIFY_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException:
        raise
    except Exception as exc:
        log_exception(exc)
