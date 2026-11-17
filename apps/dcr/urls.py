from django.urls import path

from .views import (
    CertificationSchemeListView,
    DCRPermissionsView,
    LoginView,
    LogoutView,
    MeView,
    MobilePhoneView,
    PasswordPolicyView,
    PasswordResetView,
    RegisterView,
    ResendValidationEmailView,
    ValidateEmailView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("validate-email/", ValidateEmailView.as_view(), name="auth-validate-email"),
    path(
        "resend-validation-email/",
        ResendValidationEmailView.as_view(),
        name="auth-resend-validation-email",
    ),
    path("password-policy/", PasswordPolicyView.as_view(), name="auth-password-policy"),
    path("password-reset/", PasswordResetView.as_view(), name="auth-password-reset"),
    path("login/", LoginView.as_view(), name="auth-login"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", MeView.as_view(), name="auth-me"),
    path("me/mobile-phone/", MobilePhoneView.as_view(), name="auth-mobile-phone"),
    path("dcr/permissions/", DCRPermissionsView.as_view(), name="dcr-permissions"),
    path(
        "dcr/certification-schemes/",
        CertificationSchemeListView.as_view(),
        name="dcr-certification-schemes",
    ),
]
