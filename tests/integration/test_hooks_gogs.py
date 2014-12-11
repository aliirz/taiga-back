import pytest
import json

from unittest import mock

from django.core.urlresolvers import reverse
from django.core import mail

from taiga.hooks.gogs import event_hooks
from taiga.hooks.gogs.api import GogsViewSet
from taiga.hooks.exceptions import ActionSyntaxException
from taiga.projects.issues.models import Issue
from taiga.projects.tasks.models import Task
from taiga.projects.userstories.models import UserStory
from taiga.projects import services
from .. import factories as f

pytestmark = pytest.mark.django_db


def test_bad_signature(client):
    project=f.ProjectFactory()
    url = reverse("gogs-hook-list")
    url = "{}?project={}".format(url, project.id)
    data = {"secret": "bad"}
    response = client.post(url, json.dumps(data), content_type="application/json")
    response_content = json.loads(response.content.decode("utf-8"))
    assert response.status_code == 400
    assert "Bad signature" in response_content["_error_message"]


def test_ok_signature(client):
    project=f.ProjectFactory()
    f.ProjectModulesConfigFactory(project=project, config={
        "gogs": {
            "secret": "tpnIwJDz4e"
        }
    })

    url = reverse("gogs-hook-list")
    url = "{}?project={}".format(url, project.id)
    data = {"test:": "data", "secret": "tpnIwJDz4e"}
    response = client.post(url, json.dumps(data), content_type="application/json")

    assert response.status_code == 200


def test_push_event_detected(client):
    project=f.ProjectFactory()
    url = reverse("gogs-hook-list")
    url = "%s?project=%s"%(url, project.id)
    data = {"commits": [
      {"message": "test message"},
    ]}

    GogsViewSet._validate_signature = mock.Mock(return_value=True)

    with mock.patch.object(event_hooks.PushEventHook, "process_event") as process_event_mock:
        response = client.post(url, json.dumps(data), content_type="application/json")

        assert process_event_mock.call_count == 1

    assert response.status_code == 200


def test_push_event_issue_processing(client):
    creation_status = f.IssueStatusFactory()
    role = f.RoleFactory(project=creation_status.project, permissions=["view_issues"])
    membership = f.MembershipFactory(project=creation_status.project, role=role, user=creation_status.project.owner)
    new_status = f.IssueStatusFactory(project=creation_status.project)
    issue = f.IssueFactory.create(status=creation_status, project=creation_status.project, owner=creation_status.project.owner)
    payload = {"commits": [
        {"message": """test message
            test   TG-%s    #%s   ok
            bye!
        """%(issue.ref, new_status.slug)},
    ]}
    mail.outbox = []
    ev_hook = event_hooks.PushEventHook(issue.project, payload)
    ev_hook.process_event()
    issue = Issue.objects.get(id=issue.id)
    assert issue.status.id == new_status.id
    assert len(mail.outbox) == 1


def test_push_event_task_processing(client):
    creation_status = f.TaskStatusFactory()
    role = f.RoleFactory(project=creation_status.project, permissions=["view_tasks"])
    membership = f.MembershipFactory(project=creation_status.project, role=role, user=creation_status.project.owner)
    new_status = f.TaskStatusFactory(project=creation_status.project)
    task = f.TaskFactory.create(status=creation_status, project=creation_status.project, owner=creation_status.project.owner)
    payload = {"commits": [
        {"message": """test message
            test   TG-%s    #%s   ok
            bye!
        """%(task.ref, new_status.slug)},
    ]}
    mail.outbox = []
    ev_hook = event_hooks.PushEventHook(task.project, payload)
    ev_hook.process_event()
    task = Task.objects.get(id=task.id)
    assert task.status.id == new_status.id
    assert len(mail.outbox) == 1


def test_push_event_user_story_processing(client):
    creation_status = f.UserStoryStatusFactory()
    role = f.RoleFactory(project=creation_status.project, permissions=["view_us"])
    membership = f.MembershipFactory(project=creation_status.project, role=role, user=creation_status.project.owner)
    new_status = f.UserStoryStatusFactory(project=creation_status.project)
    user_story = f.UserStoryFactory.create(status=creation_status, project=creation_status.project, owner=creation_status.project.owner)
    payload = {"commits": [
        {"message": """test message
            test   TG-%s    #%s   ok
            bye!
        """%(user_story.ref, new_status.slug)},
    ]}

    mail.outbox = []
    ev_hook = event_hooks.PushEventHook(user_story.project, payload)
    ev_hook.process_event()
    user_story = UserStory.objects.get(id=user_story.id)
    assert user_story.status.id == new_status.id
    assert len(mail.outbox) == 1


def test_push_event_processing_case_insensitive(client):
    creation_status = f.TaskStatusFactory()
    role = f.RoleFactory(project=creation_status.project, permissions=["view_tasks"])
    membership = f.MembershipFactory(project=creation_status.project, role=role, user=creation_status.project.owner)
    new_status = f.TaskStatusFactory(project=creation_status.project)
    task = f.TaskFactory.create(status=creation_status, project=creation_status.project, owner=creation_status.project.owner)
    payload = {"commits": [
        {"message": """test message
            test   tg-%s    #%s   ok
            bye!
        """%(task.ref, new_status.slug.upper())},
    ]}
    mail.outbox = []
    ev_hook = event_hooks.PushEventHook(task.project, payload)
    ev_hook.process_event()
    task = Task.objects.get(id=task.id)
    assert task.status.id == new_status.id
    assert len(mail.outbox) == 1


def test_push_event_task_bad_processing_non_existing_ref(client):
    issue_status = f.IssueStatusFactory()
    payload = {"commits": [
        {"message": """test message
            test   TG-6666666    #%s   ok
            bye!
        """%(issue_status.slug)},
    ]}
    mail.outbox = []

    ev_hook = event_hooks.PushEventHook(issue_status.project, payload)
    with pytest.raises(ActionSyntaxException) as excinfo:
        ev_hook.process_event()

    assert str(excinfo.value) == "The referenced element doesn't exist"
    assert len(mail.outbox) == 0


def test_push_event_us_bad_processing_non_existing_status(client):
    user_story = f.UserStoryFactory.create()
    payload = {"commits": [
        {"message": """test message
            test   TG-%s    #non-existing-slug   ok
            bye!
        """%(user_story.ref)},
    ]}

    mail.outbox = []

    ev_hook = event_hooks.PushEventHook(user_story.project, payload)
    with pytest.raises(ActionSyntaxException) as excinfo:
        ev_hook.process_event()

    assert str(excinfo.value) == "The status doesn't exist"
    assert len(mail.outbox) == 0


def test_push_event_bad_processing_non_existing_status(client):
    issue = f.IssueFactory.create()
    payload = {"commits": [
        {"message": """test message
            test   TG-%s    #non-existing-slug   ok
            bye!
        """%(issue.ref)},
    ]}

    mail.outbox = []

    ev_hook = event_hooks.PushEventHook(issue.project, payload)
    with pytest.raises(ActionSyntaxException) as excinfo:
        ev_hook.process_event()

    assert str(excinfo.value) == "The status doesn't exist"
    assert len(mail.outbox) == 0


def test_api_get_project_modules(client):
    project = f.create_project()
    membership = f.MembershipFactory(project=project, user=project.owner, is_owner=True)

    url = reverse("projects-modules", args=(project.id,))

    client.login(project.owner)
    response = client.get(url)
    assert response.status_code == 200
    content = json.loads(response.content.decode("utf-8"))
    assert "gogs" in content
    assert content["gogs"]["secret"] != ""
    assert content["gogs"]["webhooks_url"] != ""


def test_api_patch_project_modules(client):
    project = f.create_project()
    membership = f.MembershipFactory(project=project, user=project.owner, is_owner=True)

    url = reverse("projects-modules", args=(project.id,))

    client.login(project.owner)
    data = {
        "gogs": {
            "secret": "test_secret",
            "url": "test_url",
        }
    }
    response = client.patch(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 204

    config = services.get_modules_config(project).config
    assert "gogs" in config
    assert config["gogs"]["secret"] == "test_secret"
    assert config["gogs"]["webhooks_url"] != "test_url"