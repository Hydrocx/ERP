from django.contrib.auth import get_user_model
from wagtail.permission_policies import ModelPermissionPolicy


class ReadOnlyPermissionPolicy(ModelPermissionPolicy):
    """Allow viewing only; records are created by services, never by hand."""

    blocked_actions = {"add", "change", "delete"}

    def user_has_permission(self, user, action):
        if action in self.blocked_actions:
            return False
        return super().user_has_permission(user, action)

    def users_with_any_permission(self, actions):
        actions = [a for a in actions if a not in self.blocked_actions]
        if not actions:
            return get_user_model().objects.none()
        return super().users_with_any_permission(actions)
