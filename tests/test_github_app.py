from unittest.mock import MagicMock

import pytest

from argus.github_app import get_installation_token


def test_get_installation_token_splits_repo_and_returns_token(monkeypatch):
    installation = MagicMock(id=42)
    auth = MagicMock(token="ghs_installation-token")

    integration = MagicMock()
    integration.get_repo_installation.return_value = installation
    integration.get_access_token.return_value = auth

    captured = {}

    def fake_integration(app_id, private_key):
        captured["app_id"] = app_id
        captured["private_key"] = private_key
        return integration

    monkeypatch.setattr("argus.github_app.GithubIntegration", fake_integration)

    token = get_installation_token("123", "-----BEGIN RSA PRIVATE KEY-----\n...", "owner/repo")

    assert token == "ghs_installation-token"
    assert captured["app_id"] == "123"
    integration.get_repo_installation.assert_called_once_with("owner", "repo")
    integration.get_access_token.assert_called_once_with(42)


def test_get_installation_token_rejects_malformed_repo(monkeypatch):
    monkeypatch.setattr("argus.github_app.GithubIntegration", MagicMock())
    with pytest.raises(ValueError, match="owner/name"):
        get_installation_token("123", "key", "not-a-valid-repo-format")


def test_get_installation_token_rejects_non_numeric_app_id(monkeypatch):
    monkeypatch.setattr("argus.github_app.GithubIntegration", MagicMock())
    with pytest.raises(ValueError, match="GITHUB_APP_ID must be numeric"):
        get_installation_token("not-a-number", "key", "owner/repo")
