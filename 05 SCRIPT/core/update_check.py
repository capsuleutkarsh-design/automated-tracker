"""
Tells the application when a newer release has been published.

Installs here are manual - somebody downloads the installer from the GitHub
releases page and runs it - so nothing on the machine ever knows it has fallen
behind, and a workstation can sit two or three versions back for months with
no sign of it. This module is the whole of the update story: it reads the
GitHub releases API, compares the newest published tag with the running
`core.version.APP_VERSION`, and says whether there is something newer. It
never downloads a file and never writes one; acting on the answer is the
artist's business.

Two rules for whoever calls it.

It is opt-in. A studio machine may be behind a proxy, off the internet, or on
a network where an outbound call to github.com is simply not welcome, so the
check only runs when the setting has been turned on deliberately.

It must run off the GUI thread. urllib blocks, and a firewall that drops the
packets rather than refusing them turns `timeout` into that many seconds of
frozen window. Run it in a QThread (or a plain thread) and carry the result
back to the window through a signal.

Nothing in here raises. Every failure - no route to the host, a proxy asking
for credentials, a 404 from a renamed repository, the HTML login page a
captive portal serves instead of JSON, GitHub's rate limit - comes back as
None, because "could not tell" and "nothing new" mean the same thing to the
caller: say nothing and leave the artist alone.
"""

import json
from urllib.request import Request, urlopen

from core.version import APP_NAME, APP_VERSION

DEFAULT_REPO = "capsuleutkarsh-design/automated-tracker"
DEFAULT_TIMEOUT = 3.0

# The releases list, not /releases/latest: the latest endpoint hides drafts and
# pre-releases but also hides its reasoning, and we want to do that filtering
# ourselves so a mistakenly published pre-release can never reach an artist.
API_URL = "https://api.github.com/repos/%s/releases"

# A wrong URL can hand back a stream of any size; nothing legitimate here is
# more than a few tens of kilobytes, so stop reading well before memory hurts.
MAX_BYTES = 512 * 1024


def _user_agent():
    """GitHub answers 403 to any request without a User-Agent, so send one."""
    return "%s/%s (+https://github.com/%s)" % (
        APP_NAME.replace(" ", ""), APP_VERSION, DEFAULT_REPO)


def _fetch(url, timeout):
    """
    Returns the body of `url` as text.

    Separated from the parsing above it so the tests can replace this one
    function and never touch the network.
    """
    req = Request(url, headers={
        "User-Agent": _user_agent(),
        "Accept": "application/vnd.github+json",
    })
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES)
    if isinstance(raw, bytes):
        # Whatever encoding claim came back, the API is UTF-8; a byte that is
        # not is a corrupt body, not a reason to blow up.
        return raw.decode("utf-8", "replace")
    return str(raw)


def _version_key(text):
    """
    Sort key for one dotted version string.

    Each component becomes a tuple so that a non-numeric component always sorts
    below a numeric one: (0, 0, text) for words, (1, number, "") for digits and
    for anything missing, which counts as zero. That is
    what makes '1.2.0-rc1' older than '1.2.0' - the component '0-rc1' is not a
    number - and it keeps a garbage tag from ever looking newer than a real
    version.
    """
    parts = []
    for chunk in str(text or "").strip().lstrip("vV").split("."):
        chunk = chunk.strip()
        if chunk.isdigit() or not chunk:
            # An empty component - '1..2', or the empty string itself - is a
            # missing component, and a missing component is zero.
            chunk = chunk or "0"
            parts.append((1, int(chunk), ""))
        else:
            parts.append((0, 0, chunk.lower()))
    return parts


def is_newer(candidate, current):
    """
    True when the version string `candidate` is later than `current`.

    Tolerant on purpose, because a release tag is typed by a human: a leading
    'v' is ignored, components missing from either side count as zero (so
    '1.2' and '1.2.0' are the same version), a non-numeric component sorts
    before any number, and two equal versions are not newer than each other.
    Empty or nonsense input is compared, not rejected - it simply loses.
    """
    a, b = _version_key(candidate), _version_key(current)
    # Pad the short one with zeros rather than comparing ragged lists, so
    # 'v1.2' against '1.10' is decided by the second component and not by
    # which string had more dots in it.
    while len(a) < len(b):
        a.append((1, 0, ""))
    while len(b) < len(a):
        b.append((1, 0, ""))
    return a > b


def _release_info(entry):
    """One entry of the API response reduced to the four fields we show."""
    tag = str(entry.get("tag_name") or "").strip()
    if not tag:
        return None
    return {
        "tag": tag,
        # An untitled release shows its tag instead of an empty banner.
        "name": str(entry.get("name") or "").strip() or tag,
        "url": str(entry.get("html_url") or "").strip(),
        "published": str(entry.get("published_at") or "").strip(),
    }


def _pick_release(payload):
    """
    The newest published release in a decoded API response, or None.

    Drafts are not public and pre-releases are not meant for artists, so both
    are dropped. The rest are compared by version rather than trusted in the
    order GitHub returned them, because a patch backported to an older branch
    is published after a newer release and would otherwise come out on top.
    """
    if not isinstance(payload, list):
        return None
    best = None
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("draft") or entry.get("prerelease"):
            continue
        info = _release_info(entry)
        if info is None:
            continue
        if best is None or is_newer(info["tag"], best["tag"]):
            best = info
    return best


def latest_release(repo=DEFAULT_REPO, timeout=DEFAULT_TIMEOUT):
    """
    The newest published release of `repo`, or None if it cannot be read.

    Returns a dict with 'tag', 'name', 'url' and 'published'. Blocks for up to
    `timeout` seconds, so call it off the GUI thread.
    """
    try:
        body = _fetch(API_URL % repo, float(timeout))
        return _pick_release(json.loads(body))
    except Exception:
        # Deliberately everything: a failed update check is not worth a dialog,
        # a log line the artist has to read, or a traceback in a worker thread.
        return None


def check_for_update(current_version=APP_VERSION, repo=DEFAULT_REPO,
                     timeout=DEFAULT_TIMEOUT):
    """
    The newest release when it is later than `current_version`, else None.

    The one call the window needs: None means say nothing - either because the
    build is current or because the check could not be made - and a dict means
    there is a newer version, ready to be named in a banner with its link.
    Off the GUI thread, and only when the artist has opted in.
    """
    try:
        info = latest_release(repo=repo, timeout=timeout)
        if info and is_newer(info["tag"], current_version):
            return info
        return None
    except Exception:
        return None
