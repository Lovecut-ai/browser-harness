"""BU_BROWSER_CONTEXT_ID keeps a daemon and its helpers inside one CDP browser context."""
import asyncio
from unittest.mock import patch

from browser_harness import daemon, helpers


class _ContextCDP:
    def __init__(self, targets):
        self.targets = targets
        self.calls = []

    async def send_raw(self, method, params=None, session_id=None):
        self.calls.append((method, params, session_id))
        if method == "Target.getTargets":
            return {"targetInfos": self.targets}
        if method == "Target.createTarget":
            tid = f"created-{len(self.targets)}"
            self.targets.append({"targetId": tid, "url": "about:blank", "type": "page",
                                 "browserContextId": params.get("browserContextId", "default")})
            return {"targetId": tid}
        if method == "Target.attachToTarget":
            return {"sessionId": f"session-for-{params['targetId']}"}
        return {}


def _creates(d):
    return [p for (m, p, _s) in d.cdp.calls if m == "Target.createTarget"]


def _daemon(monkeypatch, name, targets):
    monkeypatch.setattr(daemon, "NAME", name)
    monkeypatch.setattr(daemon, "REMOTE_ID", None)
    monkeypatch.setattr(daemon, "BROWSER_KIND", "cdp")
    monkeypatch.setattr(daemon, "CONTEXT_ID", "ctx-a")
    d = daemon.Daemon()
    d.cdp = _ContextCDP(targets)
    return d


def test_named_daemon_creates_its_dedicated_tab_in_the_context(monkeypatch):
    d = _daemon(monkeypatch, "worker-a", [])
    page = asyncio.run(d.attach_first_page())
    assert _creates(d) == [{"url": "about:blank", "background": True, "browserContextId": "ctx-a"}]
    assert d.target_id == page["targetId"]


def test_named_daemon_replacement_tab_stays_in_the_context(monkeypatch):
    d = _daemon(monkeypatch, "worker-a", [])
    asyncio.run(d.attach_first_page())
    d.cdp.targets.clear()   # the dedicated tab was closed
    asyncio.run(d.attach_first_page())
    assert [p.get("browserContextId") for p in _creates(d)] == ["ctx-a", "ctx-a"]


def test_default_daemon_never_attaches_a_page_of_another_context(monkeypatch):
    other = {"targetId": "other", "url": "https://example.com/", "type": "page", "browserContextId": "ctx-b"}
    d = _daemon(monkeypatch, "default", [other])
    page = asyncio.run(d.attach_first_page())
    assert page["targetId"] != "other"
    assert _creates(d) == [{"url": "about:blank", "background": True, "browserContextId": "ctx-a"}]


def test_default_daemon_attaches_a_page_of_its_own_context(monkeypatch):
    own = {"targetId": "own", "url": "https://example.com/", "type": "page", "browserContextId": "ctx-a"}
    d = _daemon(monkeypatch, "default", [own])
    assert asyncio.run(d.attach_first_page())["targetId"] == "own"
    assert _creates(d) == []


def test_daemon_without_a_context_keeps_upstream_behaviour(monkeypatch):
    d = _daemon(monkeypatch, "worker-a", [])
    monkeypatch.setattr(daemon, "CONTEXT_ID", None)
    asyncio.run(d.attach_first_page())
    assert _creates(d) == [{"url": "about:blank", "background": True}]


TARGETS = [
    {"targetId": "a1", "type": "page", "url": "https://a.test/", "title": "A", "browserContextId": "ctx-a"},
    {"targetId": "b1", "type": "page", "url": "https://b.test/", "title": "B", "browserContextId": "ctx-b"},
    {"targetId": "af", "type": "iframe", "url": "https://frame.test/a", "browserContextId": "ctx-a"},
    {"targetId": "bf", "type": "iframe", "url": "https://frame.test/b", "browserContextId": "ctx-b"},
]


def _fake_cdp(calls):
    def fake(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.getTargets":
            return {"targetInfos": TARGETS}
        if method == "Target.createTarget":
            return {"targetId": "new"}
        return {}
    return fake


def test_list_tabs_shows_only_the_context(monkeypatch):
    monkeypatch.setenv("BU_BROWSER_CONTEXT_ID", "ctx-a")
    with patch("browser_harness.helpers.cdp", side_effect=_fake_cdp([])):
        assert [t["targetId"] for t in helpers.list_tabs()] == ["a1"]


def test_list_tabs_without_a_context_shows_every_tab(monkeypatch):
    monkeypatch.delenv("BU_BROWSER_CONTEXT_ID", raising=False)
    with patch("browser_harness.helpers.cdp", side_effect=_fake_cdp([])):
        assert [t["targetId"] for t in helpers.list_tabs()] == ["a1", "b1"]


def test_iframe_target_stays_in_the_context(monkeypatch):
    monkeypatch.setenv("BU_BROWSER_CONTEXT_ID", "ctx-b")
    with patch("browser_harness.helpers.cdp", side_effect=_fake_cdp([])):
        assert helpers.iframe_target("frame.test") == "bf"


def test_new_tab_opens_in_the_context(monkeypatch):
    monkeypatch.setenv("BU_BROWSER_CONTEXT_ID", "ctx-a")
    calls = []
    with patch("browser_harness.helpers.cdp", side_effect=_fake_cdp(calls)), \
         patch("browser_harness.helpers.switch_tab"):
        assert helpers.new_tab() == "new"
    assert ("Target.createTarget", {"url": "about:blank", "background": True, "browserContextId": "ctx-a"}) in calls
