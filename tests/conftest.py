import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture
def client_for():
    """Return an APIClient authenticated with a real JWT access token for `user`."""

    def make(user):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    return make


def assert_error(response, status_code, code=None):
    """Assert the standard error shape and return the error object."""
    assert response.status_code == status_code, response.content
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == {"code", "message", "details"}, error
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)
    if code is not None:
        assert error["code"] == code, error
    return error
