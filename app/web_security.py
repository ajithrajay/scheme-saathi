"""Owner-only sessions, CSRF, bounded uploads and a separate public health check."""
import hashlib
import hmac
import secrets
import time
from collections import deque
from pathlib import Path
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse

class BodyLimit:
    def __init__(self, app, limit=12 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] in {'GET','HEAD','OPTIONS'}:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > self.limit:
                return await JSONResponse({'detail':'Request exceeds 12 MB'},status_code=413)(scope,receive,send)
            chunks.append(message.get('body', b''))
            if not message.get('more_body'):
                break
        body, delivered = b''.join(chunks), False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':body,'more_body':False}
            return await receive()
        return await self.app(scope, replay, send)

def install_security(app, config):
    cloud = config.mode == 'cloud'
    sessions = {}
    attempts = deque()
    assets = Path(__file__).parent / 'web'
    app.add_middleware(BodyLimit)

    def authenticated(request):
        digest = hashlib.sha256(request.cookies.get('saathi_session','').encode()).hexdigest()
        return sessions.get(digest, 0) > time.monotonic()

    @app.middleware('http')
    async def security(request, call_next):
        path = request.url.path
        if path.startswith('/api/'):
            if path != '/api/bootstrap' and not authenticated(request):
                return JSONResponse({'detail':'Sign in with your owner key.'}, status_code=401, headers={'Cache-Control':'no-store'})
            if request.method not in {'GET','HEAD'} and (path != '/api/bootstrap' or cloud):
                origin = request.headers.get('origin')
                expected = config.public_url if cloud else str(request.base_url).rstrip('/')
                if request.headers.get('x-saathi-request') != '1' or (origin and origin != expected):
                    return JSONResponse({'detail':'Cross-origin write rejected'},status_code=403)
        try:
            response = await call_next(request)
        except Exception as exc:
            # Do not print request bodies, SQL parameters, uploaded data or portal URLs.
            print('Request failed:', type(exc).__name__)
            response = JSONResponse({'detail':'Server operation failed. Retry after checking service health.'},status_code=500)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), geolocation=(), microphone=(self)'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if path.startswith('/demo/'):
            response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
        if cloud:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        if path.startswith('/api/') or path in {'/login','/browser','/'}:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/api/bootstrap')
    async def login(request: Request):
        now = time.monotonic()
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429,'Too many sign-in attempts. Wait one minute.',headers={'Retry-After':'60'})
        attempts.append(now)
        body = await request.json()
        if not isinstance(body,dict) or not hmac.compare_digest(str(body.get('token','')).encode(), config.token.encode()):
            raise HTTPException(401,'Invalid owner key.')
        for key, expiry in list(sessions.items()):
            if expiry <= now:
                sessions.pop(key,None)
        if len(sessions) >= 50:
            sessions.pop(next(iter(sessions)))
        token = secrets.token_urlsafe(32)
        sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 12*3600
        response = JSONResponse({'ok':True})
        response.set_cookie('saathi_session',token,httponly=True,secure=cloud,samesite='strict',max_age=12*3600,path='/')
        return response

    @app.post('/api/logout')
    async def logout(request: Request):
        sessions.pop(hashlib.sha256(request.cookies.get('saathi_session','').encode()).hexdigest(),None)
        response = JSONResponse({'ok':True})
        response.delete_cookie('saathi_session',path='/',secure=cloud,samesite='strict')
        return response

    @app.get('/healthz')
    async def healthz():
        try:
            app.state.store.get('monitor','health')
        except Exception:
            return JSONResponse({'status':'unavailable'},status_code=503)
        return {'status':'ok'}

    @app.get('/login')
    async def login_page():
        return FileResponse(assets/'login.html')

    @app.get('/browser')
    async def browser_page(request: Request):
        if not authenticated(request):
            from fastapi.responses import RedirectResponse
            return RedirectResponse('/login')
        return FileResponse(assets/'browser.html')

    from fastapi.staticfiles import StaticFiles
    app.mount('/web',StaticFiles(directory=assets),name='web')
