"""Tests for the /healthz/ endpoint used by the compose healthcheck."""

import pytest
from django.db import connection
from django.urls import reverse


@pytest.mark.django_db
def test_healthz_reports_ok(client):
    response = client.get(reverse("healthz"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["vendor"] == connection.vendor


@pytest.mark.django_db
def test_healthz_rejects_post(client):
    assert client.post(reverse("healthz")).status_code == 405
