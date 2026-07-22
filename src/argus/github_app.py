"""GitHub App authentication.

Exchanges the Argus GitHub App's private key for a short-lived installation
access token, so Argus can post reviews under its own bot identity instead
of a personal access token — the token owner otherwise shows up as whichever
human's PAT was used, which is both confusing and breaks self-review (GitHub
won't let a PR's own author submit a REQUEST_CHANGES-style review on it).

PyGithub's GithubIntegration already implements the JWT + installation-token
exchange (RFC 7519 signing, installation lookup, token minting), so this is
a thin wrapper rather than a re-implementation.
"""

from __future__ import annotations

from github import GithubIntegration


def get_installation_token(app_id: str, private_key: str, repo: str) -> str:
    """Returns a ~1-hour installation access token scoped to `repo`'s
    installation of the Argus GitHub App. `repo` is "owner/name"; `private_key`
    is the App's PEM-encoded private key content (not a file path)."""
    owner, name = repo.split("/", 1)
    integration = GithubIntegration(app_id, private_key)
    installation = integration.get_repo_installation(owner, name)
    return integration.get_access_token(installation.id).token
