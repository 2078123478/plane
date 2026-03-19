# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from plane.bgtasks.openclaw_task import (
    OPENCLAW_INBOX_CHECK_EVENT,
    OPENCLAW_SESSIONS_SEND_TOOL,
    build_openclaw_message,
    enqueue_openclaw_inbox_checks,
    notify_openclaw_inbox_check,
    resolve_openclaw_targets,
    should_enqueue_openclaw_inbox_check,
)
from plane.bgtasks.notification_task import notifications
from plane.db.models import Issue, IssueSubscriber, Notification, Profile, Project, ProjectMember, State, User


@pytest.mark.unit
class TestOpenClawTask:
    @pytest.mark.django_db
    @override_settings(OPENCLAW_GATEWAY_URL="")
    @patch("plane.bgtasks.openclaw_task.notify_openclaw_inbox_check.delay")
    def test_enqueue_openclaw_inbox_checks_skips_when_disabled(self, mock_delay, workspace, create_user):
        enqueue_openclaw_inbox_checks(
            workspace_slug=workspace.slug,
            receiver_ids=[create_user.id],
        )

        mock_delay.assert_not_called()

    @pytest.mark.django_db
    def test_resolve_openclaw_targets_filters_disabled_and_invalid_profiles(self, create_user):
        active_user = create_user
        Profile.objects.create(
            user=active_user,
            openclaw_notify_enabled=True,
            openclaw_agent_name="agent_1",
        )

        invalid_agent_user = User.objects.create(
            email="invalid-agent-user@plane.so",
            first_name="Invalid",
            last_name="Agent",
        )
        Profile.objects.create(
            user=invalid_agent_user,
            openclaw_notify_enabled=True,
            openclaw_agent_name="bad:name",
        )

        disabled_user = User.objects.create(
            email="disabled-user@plane.so",
            first_name="Disabled",
            last_name="User",
        )
        Profile.objects.create(
            user=disabled_user,
            openclaw_notify_enabled=False,
            openclaw_agent_name="agent_disabled",
        )

        targets = resolve_openclaw_targets(
            [active_user.id, invalid_agent_user.id, disabled_user.id, active_user.id]
        )

        assert targets == [{"receiver_id": str(active_user.id), "session_key": "agent:agent_1:main"}]

    @pytest.mark.django_db
    @override_settings(OPENCLAW_GATEWAY_URL="https://openclaw.example/tools/invoke")
    @patch("plane.bgtasks.openclaw_task.should_enqueue_openclaw_inbox_check", return_value=True)
    @patch("plane.bgtasks.openclaw_task.notify_openclaw_inbox_check.delay")
    def test_enqueue_openclaw_inbox_checks_deduplicates_receivers(
        self,
        mock_delay,
        mock_should_enqueue,
        workspace,
        create_user,
    ):
        Profile.objects.create(
            user=create_user,
            openclaw_notify_enabled=True,
            openclaw_agent_name="ludehua",
        )

        enqueue_openclaw_inbox_checks(
            workspace_slug=workspace.slug,
            receiver_ids=[create_user.id, create_user.id],
        )

        assert mock_should_enqueue.call_count == 1
        assert mock_delay.call_count == 1
        mock_delay.assert_called_once_with(
            workspace_slug=workspace.slug,
            receiver_id=str(create_user.id),
            session_key="agent:ludehua:main",
        )

    @override_settings(OPENCLAW_NOTIFY_COOLDOWN_SECONDS=5)
    @patch("plane.bgtasks.openclaw_task.redis_instance")
    def test_should_enqueue_openclaw_inbox_check_respects_cooldown(self, mock_redis_instance):
        mock_redis_instance.return_value.set.return_value = False

        should_enqueue = should_enqueue_openclaw_inbox_check(
            workspace_slug="test-workspace",
            receiver_id="00000000-0000-0000-0000-000000000001",
        )

        assert should_enqueue is False
        mock_redis_instance.return_value.set.assert_called_once_with(
            "openclaw:inbox-check:test-workspace:00000000-0000-0000-0000-000000000001",
            "1",
            ex=5,
            nx=True,
        )

    @override_settings(
        OPENCLAW_GATEWAY_URL="https://openclaw.example/tools/invoke",
        OPENCLAW_GATEWAY_TOKEN="secret-token",
        OPENCLAW_GATEWAY_TIMEOUT=9,
        OPENCLAW_DELIVERY_TIMEOUT_SECONDS=30,
    )
    @patch("plane.bgtasks.openclaw_task.requests.post")
    def test_notify_openclaw_inbox_check_posts_payload(self, mock_post):
        mock_response = MagicMock()
        mock_post.return_value = mock_response

        notify_openclaw_inbox_check.run(
            workspace_slug="test-workspace",
            receiver_id="00000000-0000-0000-0000-000000000001",
            session_key="agent:ludehua:main",
        )

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://openclaw.example/tools/invoke"
        assert kwargs["timeout"] == 9
        assert kwargs["json"]["tool"] == OPENCLAW_SESSIONS_SEND_TOOL
        assert kwargs["json"]["args"]["sessionKey"] == "agent:ludehua:main"
        assert kwargs["json"]["args"]["message"] == build_openclaw_message("test-workspace")
        assert kwargs["json"]["args"]["timeoutSeconds"] == 30
        assert kwargs["json"]["args"]["deliveryMode"] == "private"
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
        assert kwargs["headers"]["X-Plane-Event"] == OPENCLAW_INBOX_CHECK_EVENT
        mock_response.raise_for_status.assert_called_once()

    @override_settings(
        OPENCLAW_GATEWAY_URL="https://openclaw.example/api/openclaw/plane-notify",
        OPENCLAW_GATEWAY_TOKEN="secret-token",
        OPENCLAW_GATEWAY_TIMEOUT=9,
        OPENCLAW_DELIVERY_TIMEOUT_SECONDS=30,
    )
    @patch("plane.bgtasks.openclaw_task.requests.post")
    def test_notify_openclaw_inbox_check_posts_relay_payload(self, mock_post):
        mock_response = MagicMock()
        mock_post.return_value = mock_response

        notify_openclaw_inbox_check.run(
            workspace_slug="test-workspace",
            receiver_id="00000000-0000-0000-0000-000000000001",
            session_key="agent:ludehua:main",
        )

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://openclaw.example/api/openclaw/plane-notify"
        assert kwargs["timeout"] == 9
        assert kwargs["json"]["sessionKey"] == "agent:ludehua:main"
        assert kwargs["json"]["message"] == build_openclaw_message("test-workspace")
        assert kwargs["json"]["timeoutSeconds"] == 30
        assert "tool" not in kwargs["json"]
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
        assert kwargs["headers"]["X-Plane-Event"] == OPENCLAW_INBOX_CHECK_EVENT
        mock_response.raise_for_status.assert_called_once()

    @pytest.mark.django_db
    def test_build_openclaw_message_includes_notification_content(self, workspace, create_user):
        project = Project.objects.create(
            name="Test Project",
            identifier="TP",
            workspace=workspace,
            created_by=create_user,
        )
        notification = Notification(
            workspace=workspace,
            project=project,
            sender="in_app:issue_activities:subscribed",
            triggered_by=create_user,
            receiver=create_user,
            entity_identifier=uuid.uuid4(),
            entity_name="issue",
            title="Commented",
            data={
                "issue": {
                    "id": str(uuid.uuid4()),
                    "name": "Test notification flow",
                    "identifier": "TP",
                    "sequence_id": 123,
                    "state_name": "Todo",
                    "state_group": "backlog",
                },
                "issue_activity": {
                    "id": str(uuid.uuid4()),
                    "verb": "created",
                    "field": "comment",
                    "actor": str(create_user.id),
                    "new_value": "<p>测试内容，测试通知链路走通。</p>",
                    "old_value": "",
                    "issue_comment": "测试内容，测试通知链路走通。",
                },
            },
        )

        message = build_openclaw_message(
            workspace_slug=workspace.slug,
            unread_count=1,
            notifications=[notification],
        )

        actor_label = create_user.display_name or create_user.first_name
        assert actor_label in message
        assert "评论内容：测试内容，测试通知链路走通。" in message
        assert "工作项：TP-123 Test notification flow" in message
        assert "项目：Test Project" in message
        assert "如有必要，请主动查看相关工作项变动并提醒主人。" in message
        assert "Inbox" not in message

    @pytest.mark.django_db
    @patch("plane.bgtasks.notification_task.enqueue_openclaw_inbox_checks")
    def test_notifications_enqueues_openclaw_after_creating_notifications(
        self,
        mock_enqueue,
        workspace,
        create_user,
    ):
        subscriber = User.objects.create(
            email="subscriber@plane.so",
            first_name="Subscriber",
            last_name="User",
        )
        project = Project.objects.create(
            name="Test Project",
            identifier="TP",
            workspace=workspace,
            created_by=create_user,
        )
        ProjectMember.objects.create(project=project, member=create_user, role=20)
        ProjectMember.objects.create(project=project, member=subscriber, role=15)
        state = State.objects.create(
            name="Todo",
            project=project,
            group="backlog",
            default=True,
        )
        issue = Issue.objects.create(
            name="Test Issue",
            workspace=workspace,
            project=project,
            state=state,
            created_by=create_user,
        )
        IssueSubscriber.objects.create(project=project, issue=issue, subscriber=subscriber)

        notifications(
            type="issue.activity.updated",
            issue_id=str(issue.id),
            project_id=str(project.id),
            actor_id=str(create_user.id),
            subscriber=False,
            issue_activities_created=json.dumps(
                [
                    {
                        "id": str(uuid.uuid4()),
                        "verb": "updated",
                        "field": "priority",
                        "actor_id": str(create_user.id),
                        "new_value": "high",
                        "old_value": "low",
                        "comment": "Priority updated",
                        "issue_comment": None,
                        "old_identifier": None,
                        "new_identifier": None,
                        "created_at": "2026-03-17T00:00:00Z",
                        "issue_detail": {"id": str(issue.id)},
                    }
                ]
            ),
            requested_data=json.dumps({"description_html": "<p></p>"}),
            current_instance=json.dumps({"description_html": "<p></p>"}),
        )

        assert Notification.objects.filter(receiver=subscriber, project=project).count() == 1
        mock_enqueue.assert_called_once_with(
            workspace_slug=workspace.slug,
            receiver_ids=[subscriber.id],
        )
