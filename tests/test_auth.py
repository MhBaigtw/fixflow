import pytest
from django.conf import settings

from apps.accounts.models import User

from .conftest import assert_error
from .factories import PASSWORD, ContractorFactory, ManagerFactory, TenantFactory

pytestmark = pytest.mark.django_db


def test_token_obtain_and_refresh(anon):
    user = TenantFactory()
    resp = anon.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    assert resp.status_code == 200
    tokens = resp.json()
    assert {"access", "refresh"} <= set(tokens)

    resp = anon.post("/api/auth/token/refresh/", {"refresh": tokens["refresh"]})
    assert resp.status_code == 200
    assert "access" in resp.json()

    anon.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert anon.get("/api/me/").json()["email"] == user.email


def test_bad_credentials_use_error_shape(anon):
    user = TenantFactory()
    resp = anon.post("/api/auth/token/", {"email": user.email, "password": "wrong"})
    assert_error(resp, 401, "no_active_account")


def test_invalid_refresh_token_uses_error_shape(anon):
    resp = anon.post("/api/auth/token/refresh/", {"refresh": "not-a-token"})
    assert_error(resp, 401, "token_not_valid")


def test_invalid_access_token_uses_error_shape(anon):
    anon.credentials(HTTP_AUTHORIZATION="Bearer garbage")
    assert_error(anon.get("/api/me/"), 401, "token_not_valid")


def test_jwt_lifetimes_default_to_spec():
    assert settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds() == 15 * 60
    assert settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].days == 1


@pytest.mark.parametrize(
    "factory,role",
    [(TenantFactory, "tenant"), (ManagerFactory, "manager"), (ContractorFactory, "contractor")],
)
def test_me_returns_role(client_for, factory, role):
    user = factory()
    resp = client_for(user).get("/api/me/")
    assert resp.status_code == 200
    assert resp.json() == {
        "id": user.id,
        "email": user.email,
        "first_name": "",
        "last_name": "",
        "role": role,
    }


def test_me_requires_auth(anon):
    assert_error(anon.get("/api/me/"), 401, "not_authenticated")


def test_no_public_signup(anon):
    resp = anon.post("/api/users/", {"email": "x@example.com", "password": "pw"})
    assert resp.status_code == 404


def test_user_manager_normalizes_and_requires_fields():
    user = User.objects.create_user("Mixed@Example.COM", "pw", role=User.Role.TENANT)
    assert user.email == "mixed@example.com"
    assert user.check_password("pw")
    assert not user.is_staff
    with pytest.raises(ValueError):
        User.objects.create_user("", "pw", role=User.Role.TENANT)
    with pytest.raises(ValueError):
        User.objects.create_user("norole@example.com", "pw")


def test_create_superuser_defaults():
    admin = User.objects.create_superuser("admin@example.com", "pw")
    assert admin.is_staff and admin.is_superuser
    assert admin.role == User.Role.MANAGER
    assert str(admin) == "admin@example.com"
