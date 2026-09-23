"""
The opt-in update check (roadmap 2.6).

Nothing here touches the network: `update_check._fetch` is the only door out
and every test replaces it, so the suite stays offline and fast. RELEASES_JSON
below is shaped like a real response from
https://api.github.com/repos/<owner>/<repo>/releases, down to the draft and
the pre-release that must both be ignored.
"""
import json
import socket
from urllib.error import URLError

import pytest

from core import update_check
from core.update_check import check_for_update, is_newer, latest_release


RELEASES_JSON = json.dumps([
    {
        "url": "https://api.github.com/repos/capsuleutkarsh-design/automated-tracker/releases/9",
        "html_url": "https://github.com/capsuleutkarsh-design/automated-tracker/releases/tag/v1.3.0",
        "id": 9,
        "tag_name": "v1.3.0",
        "target_commitish": "main",
        "name": "1.3.0 - scene setup",
        "draft": True,
        "prerelease": False,
        "created_at": "2025-09-01T09:00:00Z",
        "published_at": None,
        "body": "Not published yet.",
    },
    {
        "html_url": "https://github.com/capsuleutkarsh-design/automated-tracker/releases/tag/v1.2.0-rc1",
        "id": 8,
        "tag_name": "v1.2.0-rc1",
        "name": "1.2.0 release candidate",
        "draft": False,
        "prerelease": True,
        "created_at": "2025-08-20T11:30:00Z",
        "published_at": "2025-08-20T11:45:00Z",
        "body": "Testing only.",
    },
    {
        "html_url": "https://github.com/capsuleutkarsh-design/automated-tracker/releases/tag/v1.1.2",
        "id": 7,
        "tag_name": "v1.1.2",
        "name": "1.1.2 - lens distortion",
        "draft": False,
        "prerelease": False,
        "created_at": "2025-08-02T08:15:00Z",
        "published_at": "2025-08-02T08:20:00Z",
        "body": "STMaps and undistorted plates.",
        "assets": [{"name": "Automated_Tracker_Setup_1.1.2.exe", "size": 1234567}],
    },
    {
        "html_url": "https://github.com/capsuleutkarsh-design/automated-tracker/releases/tag/v1.1.1",
        "id": 6,
        "tag_name": "v1.1.1",
        "name": "",
        "draft": False,
        "prerelease": False,
        "created_at": "2025-07-11T10:00:00Z",
        "published_at": "2025-07-11T10:05:00Z",
        "body": "Wide-baseline retry.",
    },
])


@pytest.fixture
def fake_fetch(monkeypatch):
    """Replaces the one function that would otherwise open a socket."""
    calls = []

    def install(result):
        def _fetch(url, timeout):
            calls.append((url, timeout))
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(update_check, "_fetch", _fetch)
        return calls

    return install


# -- version comparison -------------------------------------------------------

@pytest.mark.parametrize("candidate,current,expected", [
    ("1.1.1", "1.1.0", True),
    ("1.1.0", "1.1.1", False),
    ("v1.1.2", "1.1.1", True),
    ("1.1.1", "v1.1.1", False),          # a leading v is not a version bump
    ("v1.2", "1.10", False),             # 10 beats 2, dot count is irrelevant
    ("1.10", "v1.2", True),
    ("1.2", "1.2.0", False),             # missing components are zero
    ("1.2.0", "1.2", False),
    ("1.2.1", "1.2", True),
    ("2.0.0", "1.99.99", True),
    ("1.1.1", "1.1.1", False),           # equal is not newer
    ("1.2.0-rc1", "1.2.0", False),       # a word component loses to a number
    ("1.2.0", "1.2.0-rc1", True),
    ("", "1.1.1", False),
    ("1.1.1", "", True),
    ("", "", False),
    ("banana", "1.1.1", False),
    ("latest", "", False),
    (None, "1.1.1", False),
])
def test_is_newer(candidate, current, expected):
    assert is_newer(candidate, current) is expected


def test_is_newer_matches_the_shipping_version():
    """The value the window will pass in really is a plain dotted string."""
    from core.version import APP_VERSION
    assert is_newer(APP_VERSION, APP_VERSION) is False
    assert is_newer("99.0.0", APP_VERSION) is True
    assert is_newer("0.0.1", APP_VERSION) is False


# -- parsing a captured payload -----------------------------------------------

def test_latest_release_picks_the_newest_published_release(fake_fetch):
    calls = fake_fetch(RELEASES_JSON)
    info = latest_release(repo="owner/repo", timeout=1.5)
    assert info == {
        "tag": "v1.1.2",
        "name": "1.1.2 - lens distortion",
        "url": "https://github.com/capsuleutkarsh-design/automated-tracker/releases/tag/v1.1.2",
        "published": "2025-08-02T08:20:00Z",
    }
    # The draft (1.3.0) and the pre-release (1.2.0-rc1) are both higher tags,
    # so picking 1.1.2 is proof that neither of them was considered.
    url, timeout = calls[0]
    assert url == "https://api.github.com/repos/owner/repo/releases"
    assert timeout == 1.5


def test_untitled_release_falls_back_to_its_tag(fake_fetch):
    payload = [e for e in json.loads(RELEASES_JSON) if e["tag_name"] == "v1.1.1"]
    fake_fetch(json.dumps(payload))
    assert latest_release()["name"] == "v1.1.1"


def test_empty_and_unusable_payloads_return_none(fake_fetch):
    for body in ("[]", '{"message": "Not Found"}', '[{"draft": true}]',
                 '[{"name": "no tag here"}]', "null"):
        fake_fetch(body)
        assert latest_release() is None, body


def test_request_carries_a_user_agent():
    """GitHub answers 403 without one, so the header is part of the contract."""
    assert update_check._user_agent().strip()


# -- failures all come back as None -------------------------------------------

def test_network_failure_returns_none(fake_fetch):
    fake_fetch(URLError("getaddrinfo failed"))
    assert latest_release() is None
    assert check_for_update("1.0.0") is None


def test_timeout_returns_none(fake_fetch):
    fake_fetch(socket.timeout("timed out"))
    assert latest_release() is None
    assert check_for_update("1.0.0") is None


def test_non_json_body_returns_none(fake_fetch):
    fake_fetch("<html><body>Captive portal sign-in</body></html>")
    assert latest_release() is None
    assert check_for_update("1.0.0") is None


# -- the call the window makes ------------------------------------------------

def test_check_for_update_reports_a_newer_release(fake_fetch):
    fake_fetch(RELEASES_JSON)
    info = check_for_update("1.1.1", repo="owner/repo")
    assert info["tag"] == "v1.1.2"
    assert info["url"].endswith("/v1.1.2")


def test_check_for_update_is_quiet_when_current_or_ahead(fake_fetch):
    fake_fetch(RELEASES_JSON)
    assert check_for_update("1.1.2") is None
    assert check_for_update("v1.1.2") is None
    assert check_for_update("1.4.0") is None


def test_check_for_update_defaults_to_the_shipping_version(fake_fetch):
    from core.version import APP_VERSION
    fake_fetch(RELEASES_JSON)
    assert check_for_update() == check_for_update(APP_VERSION)
