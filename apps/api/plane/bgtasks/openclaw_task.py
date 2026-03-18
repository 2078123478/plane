# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import hashlib
import logging
import uuid
from typing import Iterable

import requests
from celery import shared_task
from django.conf import settings

from plane.db.models import Notification, User
from plane.settings.redis import redis_instance
from plane.utils.exception_logger import log_exception


logger = logging.getLogger("plane.worker")
OPENCLAW_INBOX_CHECK_EVENT = "plane_inbox_check"
OPENCLAW_SESSIONS_SEND_TOOL = "sessions_send"
OPENCLAW_DELIVERY_MODE = "private"
OPENCLAW_SUMMARY_PREVIEW_LIMIT = 3
OPENCLAW_MESSAGE_TEXT_LIMIT = 80
UNKNOWN_MEMBER_LABEL = "某位成员"
UNKNOWN_ISSUE_LABEL = "任务"
UNKNOWN_PROJECT_LABEL = "未归属项目"
UNKNOWN_WORKSPACE_LABEL = "未知"

ACTIVITY_LABEL_BY_FIELD = {
    "state": "更新了状态",
    "comment": "新增了评论",
    "assignee": "更新了负责人",
    "priority": "更新了优先级",
    "label": "更新了标签",
}

ACTIVITY_LABEL_BY_VERB = {
    "created": "新增了任务动态",
    "deleted": "删除了任务动态",
}


def openclaw_notifications_enabled() -> bool:
    return bool(getattr(settings, "OPENCLAW_GATEWAY_URL", ""))


def get_openclaw_cooldown_key(workspace_slug: str, receiver_id: str) -> str:
    return f"openclaw:inbox-check:{workspace_slug}:{receiver_id}"


def get_openclaw_signature_key(receiver_id: str) -> str:
    return f"openclaw:inbox-check:signature:{receiver_id}"


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


def should_send_openclaw_message(receiver_id: str, message_signature: str) -> bool:
    dedupe_seconds = getattr(settings, "OPENCLAW_MESSAGE_DEDUPE_SECONDS", 30)
    if dedupe_seconds <= 0:
        return True

    try:
        redis_client = redis_instance()
        signature_key = get_openclaw_signature_key(receiver_id=receiver_id)
        previous_signature = redis_client.get(signature_key)
        if previous_signature:
            normalized_signature = (
                previous_signature.decode("utf-8")
                if isinstance(previous_signature, (bytes, bytearray))
                else str(previous_signature)
            )
            if normalized_signature == message_signature:
                return False

        redis_client.set(signature_key, message_signature, ex=dedupe_seconds)
        return True
    except Exception as exc:
        log_exception(exc, warning=True)
        logger.warning("Failed to apply OpenClaw message dedupe")
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


def _truncate_text(value: str | None, limit: int = OPENCLAW_MESSAGE_TEXT_LIMIT) -> str:
    normalized_value = (value or "").strip()
    if not normalized_value:
        return ""
    if len(normalized_value) <= limit:
        return normalized_value
    return f"{normalized_value[: limit - 1]}..."


def _build_actor_label(notification: Notification) -> str:
    actor = notification.triggered_by
    if not actor:
        return UNKNOWN_MEMBER_LABEL
    return actor.display_name or actor.first_name or UNKNOWN_MEMBER_LABEL


def _build_issue_label(notification: Notification) -> str:
    issue_data = (notification.data or {}).get("issue") or {}
    issue_identifier = issue_data.get("identifier")
    issue_sequence_id = issue_data.get("sequence_id")
    issue_name = issue_data.get("name")

    issue_code = (
        f"{issue_identifier}-{issue_sequence_id}"
        if issue_identifier is not None and issue_sequence_id is not None
        else issue_identifier
    )
    if issue_code and issue_name:
        return _truncate_text(f"{issue_code} {issue_name}")
    if issue_code:
        return _truncate_text(str(issue_code))
    if issue_name:
        return _truncate_text(str(issue_name))
    return UNKNOWN_ISSUE_LABEL


def _build_project_label(notification: Notification) -> str:
    project = notification.project
    if project and project.name:
        return _truncate_text(project.name, limit=40)
    return UNKNOWN_PROJECT_LABEL


def _build_activity_label(notification: Notification) -> str:
    sender = notification.sender or ""
    issue_activity = (notification.data or {}).get("issue_activity") or {}
    field = (issue_activity.get("field") or "").strip().lower()
    verb = (issue_activity.get("verb") or "").strip().lower()

    if "mentioned" in sender or field == "mention":
        return "提及了你"

    field_activity_label = ACTIVITY_LABEL_BY_FIELD.get(field)
    if field_activity_label:
        return field_activity_label

    verb_activity_label = ACTIVITY_LABEL_BY_VERB.get(verb)
    if verb_activity_label:
        return verb_activity_label

    return "更新了任务信息"


def _get_recent_unread_notifications(
    receiver_id: str, preview_limit: int = OPENCLAW_SUMMARY_PREVIEW_LIMIT
) -> tuple[int, list[Notification]]:
    unread_notifications = (
        Notification.objects.filter(
            receiver_id=receiver_id,
            read_at__isnull=True,
            archived_at__isnull=True,
            snoozed_till__isnull=True,
        )
        .select_related("workspace", "project", "triggered_by")
        .order_by("-created_at")
    )
    total_unread_count = unread_notifications.count()
    preview_notifications = list(unread_notifications[:preview_limit])
    return total_unread_count, preview_notifications


def _build_workspace_summary(workspace_slug: str, notifications: list[Notification]) -> str:
    workspace_slugs = {
        notification.workspace.slug
        for notification in notifications
        if notification.workspace and notification.workspace.slug
    }
    if workspace_slug:
        workspace_slugs.add(workspace_slug)

    if not workspace_slugs:
        return UNKNOWN_WORKSPACE_LABEL
    return "、".join(sorted(workspace_slugs))


def build_openclaw_message(
    workspace_slug: str,
    unread_count: int | None = None,
    notifications: list[Notification] | None = None,
) -> str:
    notifications = notifications or []
    unread_count = unread_count if unread_count is not None else max(1, len(notifications))
    workspace_summary = _build_workspace_summary(workspace_slug=workspace_slug, notifications=notifications)
    lines = [
        "【Plane 系统通知】",
        "来源：Plane 系统通知",
        f"你有 {unread_count} 条未读 Inbox 更新。",
        f"工作区：{workspace_summary}",
    ]

    if notifications:
        lines.append("最近变动：")
        for index, notification in enumerate(notifications, start=1):
            lines.append(
                f"{index}. {_build_actor_label(notification)} · {_build_project_label(notification)} · "
                f"{_build_issue_label(notification)} · {_build_activity_label(notification)}"
            )

    lines.append("请在 Plane Inbox 中查看详情。")
    return "\n".join(lines)


def build_openclaw_message_signature(
    receiver_id: str,
    unread_count: int,
    notifications: list[Notification],
) -> str:
    signature_input = (
        f"{receiver_id}|{unread_count}|"
        + "|".join(str(notification.id) for notification in notifications)
    )
    return hashlib.sha1(signature_input.encode("utf-8")).hexdigest()


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

    unread_count, preview_notifications = _get_recent_unread_notifications(receiver_id=receiver_id)
    if unread_count > 0:
        message = build_openclaw_message(
            workspace_slug=workspace_slug,
            unread_count=unread_count,
            notifications=preview_notifications,
        )
    else:
        message = build_openclaw_message(workspace_slug=workspace_slug)

    message_signature = build_openclaw_message_signature(
        receiver_id=receiver_id,
        unread_count=max(unread_count, 1),
        notifications=preview_notifications,
    )
    if not should_send_openclaw_message(receiver_id=receiver_id, message_signature=message_signature):
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
            "message": message,
            "timeoutSeconds": settings.OPENCLAW_DELIVERY_TIMEOUT_SECONDS,
            "deliveryMode": OPENCLAW_DELIVERY_MODE,
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
