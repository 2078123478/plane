import pytest
from django.test import RequestFactory, override_settings

from plane.utils.host import base_host
from plane.utils.path_validator import get_safe_redirect_url


@pytest.mark.unit
class TestBaseHost:
    def test_base_host_prefers_request_host_when_enabled(self):
        request = RequestFactory().get("/", HTTP_HOST="plane.company.internal:8081")

        with override_settings(
            USE_REQUEST_HOST_FOR_APP_URLS=True,
            WEB_URL="http://10.0.10.163:8081",
            APP_BASE_URL=None,
        ):
            assert base_host(request=request, is_app=True) == "http://plane.company.internal:8081"

    def test_base_host_uses_forwarded_https_scheme(self):
        request = RequestFactory().get(
            "/",
            HTTP_HOST="plane.company.internal",
            HTTP_X_FORWARDED_PROTO="https",
        )

        with override_settings(
            USE_REQUEST_HOST_FOR_APP_URLS=True,
            WEB_URL="http://10.0.10.163:8081",
            APP_BASE_URL=None,
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ):
            assert base_host(request=request, is_app=True) == "https://plane.company.internal"

    def test_base_host_falls_back_to_config_when_disabled(self):
        request = RequestFactory().get("/", HTTP_HOST="plane.company.internal:8081")

        with override_settings(
            USE_REQUEST_HOST_FOR_APP_URLS=False,
            WEB_URL="http://10.0.10.163:8081",
            APP_BASE_URL=None,
        ):
            assert base_host(request=request, is_app=True) == "http://10.0.10.163:8081"


@pytest.mark.unit
class TestSafeRedirectURL:
    def test_safe_redirect_url_accepts_dynamic_request_host(self):
        with override_settings(
            WEB_URL="http://10.0.10.163:8081",
            APP_BASE_URL=None,
            ADMIN_BASE_URL=None,
            SPACE_BASE_URL=None,
        ):
            url = get_safe_redirect_url(
                base_url="http://plane.company.internal:8081",
                next_path="/projects",
            )

        assert url == "http://plane.company.internal:8081/?next_path=/projects"
