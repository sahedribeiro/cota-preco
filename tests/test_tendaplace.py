from app.services.product_parser import parse_products


def test_catalogue_product_links_without_actions_or_search():
    url = 'https://tendaplace.ao/consumiveis/316-kit-hp-9500-c8562a-drum-kit-amarelo.html'
    html = f'<a href="{url}">Produto</a><a href="/pesquisa?s=HP">Pesquisar</a><a href="{url}?add=1">Carrinho</a><a href="https://other.example/consumiveis/316-kit.html">Outro</a>'
    assert parse_products(html, 'https://tendaplace.ao/')[1] == [url]
