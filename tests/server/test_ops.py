# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The op catalog endpoints."""

from __future__ import annotations


def test_catalog(client):
    response = client.get("/api/ops")
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["counts"]["ops"] > 0
    assert catalog["categories"]


def test_one_op(client):
    response = client.get("/api/ops/compute_sv")
    assert response.status_code == 200
    assert response.json()["op"] == "compute_sv"
    assert "params" in response.json()


def test_unknown_op_is_404(client):
    response = client.get("/api/ops/no_such_op")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
