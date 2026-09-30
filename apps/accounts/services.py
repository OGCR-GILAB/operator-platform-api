from django.utils import timezone

from .models import User


def sync_user_from_dcr(profile: dict) -> User:
    """
    Create or refresh the local user from a DCR /users/current payload.

    Expected keys (OBP format): user_id, email, provider, provider_id, username.
    """
    dcr_user_id = profile.get("user_id")
    if not dcr_user_id:
        raise ValueError("DCR profile has no user_id")

    preferred_username = profile.get("username") or profile.get("provider_id") or dcr_user_id

    user = User.objects.filter(dcr_user_id=dcr_user_id).first()
    if user is None:
        username = _unique_username(preferred_username, dcr_user_id)
        user = User(dcr_user_id=dcr_user_id, username=username)
        user.set_unusable_password()

    user.email = profile.get("email") or ""
    user.dcr_provider = profile.get("provider") or ""
    user.dcr_synced_at = timezone.now()
    user.dcr_profile = {k: v for k, v in profile.items() if k != "views"}
    user.save()
    return user


def _unique_username(preferred: str, dcr_user_id: str) -> str:
    preferred = preferred[:140]
    if not User.objects.filter(username=preferred).exists():
        return preferred
    return f"{preferred}_{dcr_user_id[:8]}"
