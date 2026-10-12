import re
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.main import app
from app.core.config import settings
from app.core.auth import hash_password,verify_password
import app.core.auth as auth
from app.models import AppUser,LoginSession,utc_now
from tests.test_database import migrated_db


def test_login_access_csrf_logout_and_expiry(migrated_db,monkeypatch):
    engine,_=migrated_db
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(auth,'SessionLocal',factory)
    import app.routes.requests as request_routes
    monkeypatch.setattr(request_routes,'SessionLocal',factory)
    monkeypatch.setattr(settings,'auth_enabled',True)
    with factory() as session:
        session.add(AppUser(username='ana',password_hash=hash_password('a-secure-test-password')));session.commit()
    with TestClient(app) as client:
        for path in ('/','/catalog','/requests','/comparisons/00000000000000000000000000000000/export.xlsx','/docs'):
            assert client.get(path,follow_redirects=False).headers['location']=='/login'
        assert client.get('/health').status_code==200
        page=client.get('/login')
        csrf=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
        # A second tab must not invalidate the form already open in the first.
        second_page = client.get('/login')
        assert csrf in second_page.text
        assert client.cookies['login_csrf'] == csrf
        assert client.post('/login',data={'username':'ana','password':'bad','_csrf':csrf}).status_code==401
        result=client.post('/login',data={'username':'ana','password':'a-secure-test-password','_csrf':csrf},follow_redirects=False)
        assert result.status_code==303 and 'HttpOnly' in result.headers['set-cookie']
        # Use a protected read independent of production route DBs.
        assert client.get('/openapi.json').status_code==200
        token=client.cookies['kota_csrf']
        import json
        draft=json.dumps({'title':'CSRF form test','conditions':[],'items':[]})
        assert client.post('/requests',data={'draft_json':draft,'_csrf':token},follow_redirects=False).status_code==303
        assert client.post('/logout').status_code==403
        assert client.post('/logout',headers={'X-CSRF-Token':token,'Origin':'https://attacker.example'}).status_code==403
        assert client.post('/logout',data={'_csrf':token},follow_redirects=False).status_code==303
        assert client.get('/openapi.json',follow_redirects=False).status_code==303
        client.get('/login');csrf=client.cookies['login_csrf']
        client.post('/login',data={'username':'ana','password':'a-secure-test-password','_csrf':csrf},follow_redirects=False)
        with factory() as session:
            login=session.scalar(select(LoginSession));login.expires_at=utc_now()-timedelta(seconds=1);session.commit()
        assert client.get('/openapi.json',follow_redirects=False).status_code==303


def test_password_hash_is_salted_and_verified():
    first=hash_password('a-secure-test-password');second=hash_password('a-secure-test-password')
    assert first!=second and 'a-secure-test-password' not in first
    assert verify_password('a-secure-test-password',first)
    assert not verify_password('wrong',first)


def test_expired_login_form_can_be_retried(monkeypatch):
    monkeypatch.setattr(settings, 'auth_enabled', True)
    with TestClient(app) as client:
        response = client.post('/login', data={'_csrf': 'expired', 'password': 'never-echo-this'})
        assert response.status_code == 403
        assert '<form' in response.text
        assert 'never-echo-this' not in response.text
        csrf = client.cookies['login_csrf']
        assert csrf in response.text
        assert 'no-store' in response.headers['cache-control']
        response = client.post('/login', data={'_csrf': 'invalid'})
        assert response.status_code == 403
        assert csrf in response.text
        assert client.get('/openapi.json', follow_redirects=False).status_code == 303
