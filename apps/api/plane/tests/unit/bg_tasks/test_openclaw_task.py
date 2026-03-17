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
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
        assert kwargs["headers"]["X-Plane-Event"] == OPENCLAW_INBOX_CHECK_EVENT
        mock_response.raise_for_status.assert_called_once()

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
