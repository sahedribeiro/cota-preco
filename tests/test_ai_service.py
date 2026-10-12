import json
from types import SimpleNamespace
import httpx
import pytest
from openai import OpenAI
from pydantic import SecretStr
from app.core.config import settings
from app.schemas.chat import ContextMessage
from app.services.ai_service import AIService, AIServiceError
import app.services.ai_service as module


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, 'openai_api_key', SecretStr('test-key-not-real'))
    monkeypatch.setattr(settings, 'openai_model', 'gpt-4.1-mini')


def connect_mock(monkeypatch, handler):
    client = OpenAI(api_key='test-key-not-real', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(module, 'OpenAI', lambda **kwargs: client)


def test_structured_output_over_sdk(configured, monkeypatch):
    payload = {'reply':'Ya chefe, confirma o rascunho.', 'purchase_request': {
        'title':'Computadores', 'conditions':['Considerar IVA'], 'items':[{
            'description':'10 PCs i7', 'normalized_description':'Computador Intel Core i7',
            'brand':None, 'model':None, 'reference':None, 'quantity':10, 'unit':'unidade',
            'specifications':[{'name':'RAM','value':'16 GB'}]}]}}
    def handler(request):
        data = json.loads(request.content)
        assert data['store'] is False
        assert data['max_output_tokens'] == 2500
        assert data['text']['format']['type'] == 'json_schema'
        assert data['input'][0]['content'] == 'Preciso de PCs'
        assert 'tools' not in data
        return httpx.Response(200, json={
            'id':'resp_test', 'object':'response', 'created_at':0, 'status':'completed',
            'model':'gpt-4.1-mini', 'output':[{'id':'msg_test','type':'message','role':'assistant','status':'completed',
                'content':[{'type':'output_text','text':json.dumps(payload),'annotations':[]}]}],
            'usage':{'input_tokens':100,'output_tokens':90,'total_tokens':190}})
    connect_mock(monkeypatch, handler)
    result = AIService().respond('10 i7 com 16 GB', [ContextMessage(role='user', content='Preciso de PCs')])
    assert result.response.purchase_request.items[0].quantity == 10
    assert result.response.purchase_request.items[0].brand is None
    assert result.input_tokens == 100


@pytest.mark.parametrize('status, expected', [(401,'chave'),(429,'limite'),(500,'resposta válida')])
def test_errors_do_not_expose_provider_details(configured, monkeypatch, status, expected):
    connect_mock(monkeypatch, lambda request: httpx.Response(status,json={'error':{'message':'SECRET provider details','type':'error'}}))
    with pytest.raises(AIServiceError) as error:
        AIService().respond('Olá', [])
    assert expected in str(error.value)
    assert 'SECRET' not in str(error.value)


def test_timeout(configured, monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout('SECRET', request=request)
    connect_mock(monkeypatch, handler)
    with pytest.raises(AIServiceError, match='demorou'):
        AIService().respond('Olá', [])


@pytest.mark.parametrize('status', ['incomplete', 'completed'])
def test_refusal_or_incomplete(configured, monkeypatch, status):
    class FakeClient:
        responses = SimpleNamespace(parse=lambda **kwargs: SimpleNamespace(status=status, output_parsed=None))
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(module, 'OpenAI', lambda **kwargs: FakeClient())
    with pytest.raises(AIServiceError, match='concluir'):
        AIService().respond('Olá', [])


def test_missing_key(monkeypatch):
    monkeypatch.setattr(settings, 'openai_api_key', SecretStr(''))
    with pytest.raises(AIServiceError, match='configurar'):
        AIService().respond('Olá', [])


def test_proforma_structured_output(configured, monkeypatch):
    payload = dict(supplier_name=None, document_reference='PF1', document_date=None, valid_until=None,
                   warnings='Preço unitário ausente.', lines=[dict(description='PC', reference=None,
                   quantity='2', unit=None, unit_price=None, currency=None, vat_included=None, evidence='PC 2 Total 3000')])
    def handler(request):
        data = json.loads(request.content)
        assert data['store'] is False and data['max_output_tokens'] == 2500
        assert data['text']['format']['name'] == 'ProformaDraft'
        assert 'tools' not in data
        return httpx.Response(200, json={
            'id':'resp_pf', 'object':'response', 'created_at':0, 'status':'completed', 'model':'gpt-4.1-mini',
            'output':[{'id':'msg_pf', 'type':'message', 'role':'assistant', 'status':'completed',
                       'content':[{'type':'output_text', 'text':json.dumps(payload), 'annotations':[]}]}],
            'usage':{'input_tokens':10, 'output_tokens':20, 'total_tokens':30}})
    connect_mock(monkeypatch, handler)
    result = AIService().extract_proforma('PC 2 Total 3000')
    assert result.response.lines[0].unit_price is None
