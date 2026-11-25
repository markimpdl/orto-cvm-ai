"""Channel numbers from YouTube Analytics (Phase 8) and the weekly title-pattern approval.

GET  /channels/{id}/stats → {connected, views_7d, views_28d, watch_hours_365d, subscribers,
     top_views: {title,url,views}|null, top_retention: {title,url,avg_view_pct}|null,
     monetization: {hours_target, subs_target, deadline, hours, subs}, suggestions: [{title, why}],
     title_pattern_suggestion: {pattern, status}|null, updated_at, error?}
     (the week's goal vs done is GET /channels/{id}/week, cvm/schedule.py)
     (connected=false and nulls without the Google sign-in; ?refresh=1 pulls now)
POST /channels/{id}/stats/connect → {auth_url}: open it, pick the channel's Google account/brand channel
GET  /oauth/google/callback (Google redirects here) → stores the refresh token in the Keychain/keyring
POST /channels/{id}/stats/disconnect
POST /channels/{id}/title-patterns/{approve|reject}
"""

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from cvm.config import settings
from cvm.db import get_session
from cvm.models import Channel
from cvm.publish import analytics, weekly

router = APIRouter(tags=["stats"])


def _channel(session: Session, channel_id: int) -> Channel:
    c = session.get(Channel, channel_id)
    if not c:
        raise HTTPException(404)
    return c


@router.get("/channels/{channel_id}/stats")
def channel_stats(channel_id: int, refresh: bool = False, session: Session = Depends(get_session)):
    return analytics.stats(session, _channel(session, channel_id), force=refresh)


@router.post("/channels/{channel_id}/stats/connect")
def connect(channel_id: int, request: Request, session: Session = Depends(get_session)):
    c = _channel(session, channel_id)
    # Desktop OAuth clients accept any loopback port; Google wants the IP form rather than "localhost"
    redirect = f"http://127.0.0.1:{request.url.port or settings.api_port}{analytics.CALLBACK_PATH}"
    try:
        return {"auth_url": analytics.auth_url(session, c, redirect)}
    except analytics.NotConnected as e:
        raise HTTPException(409, str(e)) from e


@router.post("/channels/{channel_id}/stats/disconnect")
def disconnect(channel_id: int, session: Session = Depends(get_session)):
    c = _channel(session, channel_id)
    analytics.delete_token(analytics.account_ref(c))
    return {"connected": False}


def _page(title: str, text: str, ok: bool) -> HTMLResponse:
    color = "#1a7f37" if ok else "#b42318"
    body = (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<body style='font-family:system-ui;max-width:560px;margin:15vh auto;padding:0 16px'>"
            f"<h2 style='color:{color}'>{html.escape(title)}</h2><p>{html.escape(text)}</p></body>")
    return HTMLResponse(body, status_code=200 if ok else 400)


@router.get(analytics.CALLBACK_PATH)
def oauth_callback(state: str = "", code: str = "", error: str = "", session: Session = Depends(get_session)):
    if error or not code:
        return _page("Conexão cancelada", f"O Google não autorizou ({error or 'sem código'}). Tente de novo pelo painel do canal.", False)
    try:
        c = analytics.finish(session, state, code)
    except analytics.NotConnected as e:
        return _page("Não conectado", str(e), False)
    try:
        analytics.refresh(session, c)
    except Exception:  # noqa: BLE001 — connected anyway; the panel retries
        pass
    return _page("Canal conectado", f"{c.name} está ligado ao YouTube Analytics. Pode fechar esta aba e voltar ao CVM.", True)


@router.post("/channels/{channel_id}/title-patterns/{decision}")
def decide_title_pattern(channel_id: int, decision: str, session: Session = Depends(get_session)):
    if decision not in ("approve", "reject"):
        raise HTTPException(422, "decision: approve | reject")
    try:
        return weekly.decide_pattern(session, _channel(session, channel_id), decision)
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
