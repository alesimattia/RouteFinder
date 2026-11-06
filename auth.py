"""
Autenticazione a token condiviso. Il firmware manda l'header; il browser, che
non può aggiungere header a una navigazione, incolla il token una volta nella
pagina di accesso e da lì viaggia in un cookie.
"""

from __future__ import annotations

import hmac
import html
import os
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Request


TOKEN_ENV = "ROUTEFINDER_TOKEN"
HEADER_NAME = "X-Routefinder-Token"
COOKIE_NAME = "routefinder_token"
QUERY_NAME = "token"
COOKIE_MAX_AGE_S = 365 * 24 * 3600
DEFAULT_NEXT = "/route"


def expected_token() -> Optional[str]:
    """Token configurato sul servizio; None se manca, e allora nessuno passa."""
    value = os.environ.get(TOKEN_ENV, "")
    return value or None


def token_matches(candidate: Optional[str]) -> bool:
    """Confronto a tempo costante col token configurato; falso se uno dei due manca."""
    expected = expected_token()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def request_token(request: Request) -> Optional[str]:
    """Token della richiesta, nell'ordine header, cookie, querystring."""
    return (request.headers.get(HEADER_NAME)
            or request.cookies.get(COOKIE_NAME)
            or request.query_params.get(QUERY_NAME))


def is_authorized(request: Request) -> bool:
    return token_matches(request_token(request))


def wants_html(request: Request) -> bool:
    """Vero per una navigazione da browser, che riceve la pagina di accesso
    invece del 401 secco destinato a curl e al firmware."""
    return "text/html" in request.headers.get("accept", "")


def safe_next(target: Optional[str]) -> str:
    """Destinazione del rimando dopo l'accesso, solo un percorso di questo
    servizio: un URL assoluto o "//host" sarebbe un open redirect."""
    if not target:
        return DEFAULT_NEXT
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return DEFAULT_NEXT
    return target


_LOGIN_PAGE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Routefinder - accesso</title>
<style>
  body {{ font-family: Roboto, "Segoe UI", system-ui, sans-serif; background: #f3f3f3;
         margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
         box-sizing: border-box; color: #1f1f1f; }}
  form {{ background: #fff; border-radius: 16px; padding: 24px; width: 100%; max-width: 380px;
         box-shadow: 0 1px 3px rgba(0,0,0,.2); box-sizing: border-box; }}
  h1 {{ font-size: 22px; font-weight: 500; margin: 0 0 8px; }}
  p {{ margin: 0 0 20px; color: #444; line-height: 1.4; }}
  label {{ display: block; font-size: 13px; color: #444; margin-bottom: 6px; }}
  input {{ width: 100%; box-sizing: border-box; padding: 12px; font-size: 15px;
          border: 1px solid #8a8a8a; border-radius: 8px; }}
  button {{ margin-top: 20px; width: 100%; padding: 12px; font-size: 15px; font-weight: 500;
           border: 0; border-radius: 20px; background: #b3261e; color: #fff; cursor: pointer; }}
  .error {{ color: #b3261e; margin: 12px 0 0; }}
</style></head><body>
<form method="post" action="/auth">
  <h1>Token di accesso</h1>
  <p>Questo servizio calcola percorsi con le API Google a pagamento: incolla il token
  configurato su render.com per continuare.</p>
  <label for="token">Token</label>
  <input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
  <input type="hidden" name="next" value="{next}">
  <button type="submit">Accedi</button>
  {error}
</form>
</body></html>"""


def login_page(next_path: str, error: Optional[str] = None) -> str:
    """Pagina in cui incollare il token; `next_path` è già passato da safe_next()."""
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return _LOGIN_PAGE.format(next=html.escape(next_path, quote=True), error=error_html)
