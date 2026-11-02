"""APIM専用の内部HTTP入口。購読IDは共有鍵の検証後に利用する。"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .cm import CmClient
from .identity import OidcClient
from .service import BrokerError, BrokerService, COOKIE_NAME, browser_cookie
from .settings import Settings


def create_app(service=None):
    @asynccontextmanager
    async def lifespan(app):
        try:
            if service is None:
                from .store import SqlStore
                settings = Settings.from_environment()
                store = SqlStore.from_environment()
                store.ensure_ready()
                app.state.broker = BrokerService(settings, store, OidcClient(settings.public_origin), CmClient(settings))
            else:
                service.store.ensure_ready()
                app.state.broker = service
        except Exception:
            # 起動失敗時も、SQL接続文字列や認証処理の詳細をログへ出しません。
            raise RuntimeError("Credential broker initialization failed") from None
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # HTTPライブラリの詳細ログへOAuth queryや内部資格情報を出しません。
    for name in ("httpx", "httpcore", "msal", "azure"):
        logging.getLogger(name).setLevel(logging.WARNING)

    @app.middleware("http")
    async def boundary(request, call_next):
        request.state.request_id = str(uuid4())
        try:
            if request.url.path != "/health":
                values = [value.decode("latin-1") for key, value in request.scope["headers"] if key.lower() == b"x-core-broker-key"]
                if len(values) != 1:
                    raise BrokerError("BrokerAuthenticationRequired", 401)
                app.state.broker.authenticate(values[0])
            response = await call_next(request)
        except BrokerError as error:
            response = JSONResponse({"error": {"code": error.code}, "requestId": request.state.request_id}, status_code=error.status)
        except Exception:
            response = JSONResponse({"error": {"code": "ConnectionUnavailable"}, "requestId": request.state.request_id}, status_code=503)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if response.status_code == 429:
            response.headers["Retry-After"] = "60"
        return response

    @app.exception_handler(BrokerError)
    async def expected_error(request, error):
        return JSONResponse({"error": {"code": error.code}, "requestId": request.state.request_id}, status_code=error.status)

    def one_header(request, name, *, required=True):
        values = [value.decode("latin-1") for key, value in request.scope["headers"] if key.lower() == name.lower().encode()]
        if len(values) != 1 or (required and not values[0]):
            raise BrokerError("ConnectionNotAuthorized", 403)
        return values[0]

    def query_values(request):
        keys = list(request.query_params.keys())
        if any(len(request.query_params.getlist(key)) != 1 for key in keys):
            raise BrokerError("ConnectionNotAuthorized", 403)
        return dict(request.query_params)

    async def body(request, required):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 32768:
                raise BrokerError("InvalidRequest", 400)
        try:
            import json
            def unique_pairs(pairs):
                result = {}
                for key, item in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = item
                return result
            value = json.loads(raw, object_pairs_hook=unique_pairs)
        except (ValueError, UnicodeError):
            raise BrokerError("InvalidRequest", 400) from None
        if not isinstance(value, dict) or set(value) != set(required):
            raise BrokerError("InvalidRequest", 400)
        return value

    @app.get("/health")
    def health():
        if not app.state.broker.store.health():
            raise BrokerError()
        return {"status": "ok"}

    @app.post("/v1/start")
    async def start(request: Request):
        value = await body(request, ("providerName",))
        return await run_in_threadpool(app.state.broker.start, value["providerName"], one_header(request, "X-Core-Subscription-Id"))

    @app.post("/v1/challenge")
    async def challenge(request: Request):
        value = await body(request, ("providerName", "connectionSession", "reason"))
        return await run_in_threadpool(app.state.broker.challenge, value["providerName"],
            one_header(request, "X-Core-Subscription-Id"), value["connectionSession"], value["reason"])

    @app.post("/v1/launch")
    async def launch(request: Request):
        value = await body(request, ("transactionId", "launch"))
        result, cookie = await run_in_threadpool(app.state.broker.launch, value["transactionId"], value["launch"], one_header(request, "Origin"))
        response = JSONResponse(result)
        response.set_cookie(COOKIE_NAME, cookie, max_age=app.state.broker.settings.transaction_ttl,
            httponly=True, secure=True, samesite="lax", path="/")
        return response

    @app.get("/v1/identity-callback")
    def identity_callback(request: Request):
        query = query_values(request)
        cookie = browser_cookie(one_header(request, "Cookie"))
        location = app.state.broker.identity_callback(query, cookie)
        return RedirectResponse(location, status_code=303)

    @app.get("/v1/status")
    def status(request: Request):
        query = query_values(request)
        if set(query) != {"transactionId"}:
            raise BrokerError("InvalidRequest", 400)
        return app.state.broker.status(query["transactionId"], one_header(request, "X-Core-Connection-Poll"),
            one_header(request, "X-Core-Subscription-Id"))

    @app.post("/v1/logout")
    async def logout(request: Request):
        value, sub = await session_body(request)
        return await run_in_threadpool(app.state.broker.logout, value["connectionSession"], sub)

    async def session_body(request, extra=()):
        value = await body(request, ("connectionSession", "subscriptionId", *extra))
        sub = one_header(request, "X-Core-Subscription-Id")
        if value["subscriptionId"] != sub:
            raise BrokerError("ConnectionNotAuthorized", 403)
        return value, sub

    @app.post("/v2/session/authorize")
    async def authorize(request: Request):
        value = await body(request, ("connectionSession", "subscriptionId", "profileName"))
        sub = one_header(request, "X-Core-Subscription-Id")
        if value["subscriptionId"] != sub:
            raise BrokerError("ConnectionNotAuthorized", 403)
        return await run_in_threadpool(app.state.broker.authorize, value["connectionSession"], sub, value["profileName"])

    @app.post("/v2/session/use")
    async def use(request: Request):
        value, sub = await session_body(request, ("profileName", "verifiedOwnerId", "apiId", "operationId"))
        return await run_in_threadpool(app.state.broker.use, value["connectionSession"], sub,
            value["profileName"], value["verifiedOwnerId"], value["apiId"], value["operationId"])

    @app.post("/v2/session/commit")
    async def commit_rotation(request: Request):
        value, sub = await session_body(request, ("rotationId", "candidateSession", "fromGeneration"))
        return await run_in_threadpool(app.state.broker.commit_rotation, value["connectionSession"], sub,
            value["rotationId"], value["candidateSession"], value["fromGeneration"])

    @app.post("/v2/session/logout-all")
    async def logout_all(request: Request):
        value, sub = await session_body(request)
        return await run_in_threadpool(app.state.broker.logout_all, value["connectionSession"], sub)

    @app.post("/v2/session/disconnect")
    async def disconnect(request: Request):
        value, sub = await session_body(request)
        result = await run_in_threadpool(app.state.broker.disconnect, value["connectionSession"], sub)
        return JSONResponse(result, status_code=202 if result["status"] == "disconnect_pending" else 200)

    @app.post("/v2/connection/status")
    async def connection_status(request: Request):
        value, sub = await session_body(request, ("profileName",))
        return await run_in_threadpool(app.state.broker.connection_status, value["connectionSession"], sub, value["profileName"])

    @app.post("/v1/callback-context")
    async def callback_context(request: Request):
        value = await body(request, ("transactionId", "state", "cookie"))
        return await run_in_threadpool(app.state.broker.callback_context, value["transactionId"], value["state"], value["cookie"])

    @app.post("/v1/complete")
    async def complete(request: Request):
        value = await body(request, ("transactionId", "callbackTicket", "verifiedOwnerId"))
        return await run_in_threadpool(app.state.broker.complete, value["transactionId"], value["callbackTicket"], value["verifiedOwnerId"])

    return app


app = create_app()
