from rest_framework.authentication import SessionAuthentication


class CsrfProtectedSessionAuthentication(SessionAuthentication):
    """Require a CSRF cookie/header pair even for anonymous mutation requests."""

    def authenticate(self, request):
        self.enforce_csrf(request)
        user = getattr(request._request, "user", None)
        if not user or not user.is_active:
            return None
        return user, None
