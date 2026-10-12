import pytest
from app.services import website_service as service


def test_private_address_blocked(monkeypatch):
    monkeypatch.setattr(service.socket, 'getaddrinfo', lambda *args, **kwargs: [(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(service.WebsiteError, match='privados'):
        service.PublicHTTPSConnection('example.com').connect()


def test_robots_denied(monkeypatch):
    monkeypatch.setattr(service, 'read_public_page', lambda url: (200,'text/plain',b'User-agent: *\nDisallow: /',url))
    with pytest.raises(service.WebsiteError, match='robots'):
        service.extract_website('https://example.com/')


def test_html_extract_and_budget(monkeypatch):
    def fetch(url):
        if url.endswith('robots.txt'): return 404, 'text/plain', b'', url
        return 200,'text/html',b'<script>secret</script><h1>Empresa</h1><p>Contacto</p>' + b'<p>' + b'a'*9000 + b'</p>',url
    monkeypatch.setattr(service,'read_public_page',fetch)
    text, truncated = service.extract_website('https://example.com/')
    assert 'secret' not in text and 'Empresa' in text
    assert len(text) == 8000 and truncated


def test_automatic_access_block_explained(monkeypatch):
    def fetch(url):
        if url.endswith('robots.txt'):
            return 404, 'text/plain', b'', url
        return 403, 'text/html', b'<title>Just a moment...</title>', url
    monkeypatch.setattr(service, 'read_public_page', fetch)
    with pytest.raises(service.WebsiteError, match='HTTP 403'):
        service.read_allowed_html('https://example.com/')
