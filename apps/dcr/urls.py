from django.urls import path

from .views import (
    CertificationSchemeListView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetView,
    RegisterView,
    ValidateEmailView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("validate-email/", ValidateEmailView.as_view(), name="auth-validate-email"),
    path("password-reset/", PasswordResetView.as_view(), name="auth-password-reset"),
    path("login/", LoginView.as_view(), name="auth-login"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", MeView.as_view(), name="auth-me"),
    path(
        "dcr/certification-schemes/",
        CertificationSchemeListView.as_view(),
        name="dcr-certification-schemes",
    ),
]
