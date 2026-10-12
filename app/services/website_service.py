import http.client
import ipaddress
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from app.schemas.supplier import website_url


class WebsiteError(Exception):
    pass


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or any((not ipaddress.ip_address(row[4][0]).is_global or ipaddress.ip_address(row[4][0]).is_multicast) for row in addresses):
            raise WebsiteError("Só são permitidos websites públicos; endereços locais ou privados são bloqueados.")
        self.sock = socket.create_connection((addresses[0][4][0], 443), self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def read_public_page(url: str) -> tuple[int, str, bytes, str]:
    for _ in range(4):
        try:
            normalized = website_url(url)
            parts = urlsplit(normalized or "")
            if parts.scheme != "https" or parts.port not in {None, 443}:
                raise WebsiteError("A leitura automática requer HTTPS na porta padrão.")
            connection = PublicHTTPSConnection(parts.hostname, timeout=10)
            try:
                path = parts.path + ("?" + parts.query if parts.query else "")
                connection.request("GET", path, headers={"User-Agent": "CotaPrecoBot/0.1", "Accept-Encoding": "identity"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    raise WebsiteError("O site redirecciona. Usa o endereço final exacto para uma nova leitura.")
                data = response.read(1_000_001)
                if len(data) > 1_000_000:
                    raise WebsiteError("Página demasiado grande para uma leitura simples.")
                return response.status, response.getheader("Content-Type", ""), data, url
            finally:
                connection.close()
        except (OSError, ValueError, http.client.HTTPException):
            raise WebsiteError("Não foi possível ler o site por HTTPS. Podes preencher o cadastro manualmente.") from None
    raise WebsiteError("Demasiados redireccionamentos.")


def read_allowed_html(url: str) -> str:
    parts = urlsplit(website_url(url) or "")
    robots_url = f"https://{parts.netloc}/robots.txt"
    status, _, body, _ = read_public_page(robots_url)
    if status == 200:
        policy = RobotFileParser()
        policy.parse(body.decode("utf-8", errors="replace").splitlines())
        if not policy.can_fetch("CotaPrecoBot", url):
            raise WebsiteError("Este website não permite esta leitura no robots.txt.")
    elif status not in {404, 410}:
        raise WebsiteError("Não foi possível confirmar as regras de leitura do website.")
    status, content_type, body, final_url = read_public_page(url)
    if status in {403, 429}:
        raise WebsiteError(f'O site bloqueou a leitura automática (HTTP {status}). Pode abrir normalmente no teu navegador e exigir verificação humana. Consulta a fonte manualmente; não foi recolhido um preço.')
    if status != 200 or "text/html" not in content_type:
        raise WebsiteError(f'A página não disponibilizou HTML público (HTTP {status}). Sem login, CAPTCHA ou execução de JavaScript.')
    return body.decode("utf-8", errors="replace")


def extract_website(url: str) -> tuple[str, bool]:
    parser = PageText()
    parser.feed(read_allowed_html(url))
    text = "\n".join(parser.parts)
    if not text.strip():
        raise WebsiteError("A página não tem texto legível. Podes preencher os dados manualmente.")
    return text[:8000], len(text) > 8000
