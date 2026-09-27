from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tidal2ytm import ytm_client as ytm_client_mod


def _stub_yt() -> Any:
    return SimpleNamespace(
        context={
            "context": {"client": {"clientName": "TVHTML5", "clientVersion": "7.20230924.01.00"}}
        }
    )


def test_browse_uses_web_remix_without_auth_headers() -> None:
    """Regression: get_album hits /browse?, which YouTube rejects under TVHTML5."""
    yt = _stub_yt()
    seen: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> str:
        seen["headers"] = kwargs.get("headers")
        seen["client"] = kwargs["json"]["context"]["client"]["clientName"]
        return "ok"

    patched = ytm_client_mod._patched_post(yt, fake_post)  # pyright: ignore[reportPrivateUsage]
    out = patched(
        "https://music.youtube.com/youtubei/v1/browse?",
        headers={"authorization": "Bearer x", "X-Goog-Request-Time": "1", "other": "kept"},
        json={"context": {"client": {"clientName": "TVHTML5"}}, "browseId": "MPRE_A"},
    )
    assert out == "ok"
    assert seen["headers"] == {"other": "kept"}
    assert seen["client"] == "WEB_REMIX"
    assert yt.context["context"]["client"]["clientName"] == "TVHTML5"


def test_non_read_endpoint_untouched() -> None:
    yt = _stub_yt()
    seen: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> str:
        seen["headers"] = kwargs.get("headers")
        return "ok"

    patched = ytm_client_mod._patched_post(yt, fake_post)  # pyright: ignore[reportPrivateUsage]
    patched(
        "https://music.youtube.com/youtubei/v1/like?",
        headers={"authorization": "Bearer x"},
    )
    assert seen["headers"] == {"authorization": "Bearer x"}
