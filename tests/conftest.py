"""Stub the GitHub binary so plan() tests do not download the release."""

from __future__ import annotations

import hashlib

import pytest

from bridge import binary

STUB = b"#!/bin/sh\nexit 0\n"
STUB_SHA = hashlib.sha256(STUB).hexdigest()


@pytest.fixture(autouse=True)
def stub_release_binary(monkeypatch, request):
    if request.node.get_closest_marker("no_binary_stub"):
        return
    table = {name: STUB_SHA for name in binary.BINARY_SHA256}
    monkeypatch.setattr(binary, "BINARY_SHA256", table)
    monkeypatch.setattr(binary, "fetch_release_bytes", lambda url: STUB)
