import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
import re
from fastapi import APIRouter,Request,Form
from fastapi.responses import HTMLResponse,RedirectResponse,Response
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select,delete
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AppUser,LoginSession,utc_now
from app.routes.web import templates

router=APIRouter()


def hash_password(password:str) -> str:
    if not 12<=len(password)<=1024: raise ValueError('A palavra-passe deve ter entre 12 e 1024 caracteres.')
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def verify_password(password:str,stored:str) -> bool:
    try:
        _,iterations,salt,digest=stored.split('$')
        calculated=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),int(iterations)).hex()
        return hmac.compare_digest(calculated,digest)
    except (ValueError,TypeError): return False


def utc(value): return value.replace(tzinfo=utc_now().tzinfo) if value.tzinfo is None else value

def csrf_value(token:str) -> str: return hashlib.sha256((token+'|csrf').encode()).hexdigest()


class LoginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        if not settings.auth_enabled or request.url.path in {'/health','/api/health','/login'} or request.url.path.startswith('/static/'):
            return await call_next(request)
        token=request.cookies.get('kota_session','')
        valid=False
        with SessionLocal() as session:
            record=session.get(LoginSession,hashlib.sha256(token.encode()).hexdigest()) if token else None
            user=session.get(AppUser,record.user_id) if record else None
            valid=bool(record and user and user.active and utc(record.expires_at)>utc_now())
        if not valid:
            if request.headers.get('HX-Request')=='true': return Response(status_code=401,headers={'HX-Redirect':'/login'})
            return RedirectResponse('/login',303)
        csrf=csrf_value(token)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'): return Response('Origem inválida.',403)
            supplied=request.headers.get('X-CSRF-Token','')
            if not supplied and request.headers.get('content-type','').startswith(('application/x-www-form-urlencoded','multipart/form-data')):
                await request.body()
                form=await request.form();supplied=str(form.get('_csrf',''))
            if not hmac.compare_digest(supplied,csrf): return Response('Sessão do formulário inválida. Recarrega a página.',403)
        response=await call_next(request)
        if 'text/html' in response.headers.get('content-type',''):
            body=b''.join([chunk async for chunk in response.body_iterator])
            hidden=f'<input type="hidden" name="_csrf" value="{csrf}">'.encode()
            body=re.sub(rb'(<form\b[^>]*>)',lambda m:m[1]+hidden if re.search(rb'method=[\"\']post[\"\']',m[1],re.I) else m[1],body,flags=re.I)
            headers=dict(response.headers);headers.pop('content-length',None)
            response=Response(body,status_code=response.status_code,headers=headers,background=response.background)
        response.set_cookie('kota_csrf',csrf,httponly=False,samesite='strict',secure=settings.auth_cookie_secure,max_age=28800)
        response.headers['Cache-Control']='no-store'
        return response


def render_login(request: Request, *, error: str | None = None, status_code: int = 200) -> Response:
    token = request.cookies.get('login_csrf', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request, name='login.html', status_code=status_code,
        context={'csrf': token, 'error': error},
    )
    response.set_cookie('login_csrf', token, httponly=True, samesite='lax',
                        secure=settings.auth_cookie_secure, max_age=28800)
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/login')
def login_page(request: Request) -> Response:
    return render_login(request)


@router.post('/login')
async def login(request:Request):
    form=await request.form()
    supplied = str(form.get('_csrf', '')).encode()
    expected = request.cookies.get('login_csrf', '').encode()
    if not expected or not hmac.compare_digest(supplied, expected):
        return render_login(request, error='O formulário foi renovado. Introduz os teus dados e tenta novamente. Se o erro continuar, permite os cookies deste site.', status_code=403)
    username=str(form.get('username','')).strip().casefold()[:100]
    password=str(form.get('password',''))
    if len(password)>1024: return Response('Credenciais inválidas.',401)
    with SessionLocal() as session:
        user=session.scalar(select(AppUser).where(AppUser.username==username))
        if user is None:
            hashlib.pbkdf2_hmac('sha256',password.encode(),b'dummy-timing-salt',600000)
        valid=user and user.active and not (user.locked_until and utc(user.locked_until)>utc_now()) and verify_password(password,user.password_hash)
        if not valid:
            if user:
                user.failed_attempts+=1
                if user.failed_attempts>=5: user.locked_until=utc_now()+timedelta(minutes=15)
                session.commit()
            return render_login(request, status_code=401, error='Credenciais inválidas ou acesso temporariamente bloqueado.')
        user.failed_attempts=0;user.locked_until=None
        session.execute(delete(LoginSession).where(LoginSession.expires_at<utc_now()))
        token=secrets.token_urlsafe(32)
        session.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,expires_at=utc_now()+timedelta(hours=8)))
        session.commit()
    response=RedirectResponse('/',303)
    response.set_cookie('kota_session',token,httponly=True,samesite='strict',secure=settings.auth_cookie_secure,max_age=28800)
    response.delete_cookie('login_csrf')
    return response


@router.post('/logout')
def logout(request:Request):
    token=request.cookies.get('kota_session','')
    with SessionLocal() as session:
        session.execute(delete(LoginSession).where(LoginSession.token_hash==hashlib.sha256(token.encode()).hexdigest()));session.commit()
    response=RedirectResponse('/login',303)
    response.delete_cookie('kota_session');response.delete_cookie('kota_csrf')
    return response
