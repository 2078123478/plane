# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectMember, User, WorkspaceMember


def create_project_member(
    workspace,
    project,
    *,
    email: str,
    username: str,
    workspace_role: int,
    project_role: int,
):
    user = User.objects.create_user(email=email, username=username)
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=workspace_role, is_active=True)
    project_member = ProjectMember.objects.create(project=project, member=user, role=project_role, is_active=True)
    return user, project_member


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Member API Project",
        identifier="MAP",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return project


@pytest.mark.contract
class TestProjectMemberDetailAPIEndpoint:
    def get_project_member_url(self, workspace_slug, project_id, project_member_id):
        return f"/api/workspaces/{workspace_slug}/projects/{project_id}/members/{project_member_id}/"

    @pytest.mark.django_db
    def test_project_admin_can_update_other_admin_role(self, session_client, workspace, project):
        project_admin, _ = create_project_member(
            workspace,
            project,
            email="project-admin@example.com",
            username="projectadmin",
            workspace_role=15,
            project_role=20,
        )
        _, target_membership = create_project_member(
            workspace,
            project,
            email="target-admin@example.com",
            username="targetadmin",
            workspace_role=20,
            project_role=20,
        )

        session_client.force_authenticate(user=project_admin)
        url = self.get_project_member_url(workspace.slug, project.id, target_membership.id)
        response = session_client.patch(url, {"role": 15}, format="json")

        assert response.status_code == status.HTTP_200_OK

        target_membership.refresh_from_db()
        assert target_membership.role == 15

    @pytest.mark.django_db
    def test_workspace_admin_can_update_member_role_without_project_admin_role(self, session_client, workspace, project):
        workspace_admin, _ = create_project_member(
            workspace,
            project,
            email="workspace-admin@example.com",
            username="workspaceadmin",
            workspace_role=20,
            project_role=15,
        )
        _, target_membership = create_project_member(
            workspace,
            project,
            email="target-member@example.com",
            username="targetmember",
            workspace_role=15,
            project_role=15,
        )

        session_client.force_authenticate(user=workspace_admin)
        url = self.get_project_member_url(workspace.slug, project.id, target_membership.id)
        response = session_client.patch(url, {"role": 5}, format="json")

        assert response.status_code == status.HTTP_200_OK

        target_membership.refresh_from_db()
        assert target_membership.role == 5

    @pytest.mark.django_db
    def test_project_member_cannot_update_another_member_role(self, session_client, workspace, project):
        member_user, _ = create_project_member(
            workspace,
            project,
            email="member-user@example.com",
            username="memberuser",
            workspace_role=15,
            project_role=15,
        )
        _, target_membership = create_project_member(
            workspace,
            project,
            email="guest-user@example.com",
            username="guestuser",
            workspace_role=5,
            project_role=5,
        )

        session_client.force_authenticate(user=member_user)
        url = self.get_project_member_url(workspace.slug, project.id, target_membership.id)
        response = session_client.patch(url, {"role": 15}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

        target_membership.refresh_from_db()
        assert target_membership.role == 5
