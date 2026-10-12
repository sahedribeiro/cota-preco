import io
import pytest
from fastapi import UploadFile
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from app.core.config import settings
from app.services.document_service import DocumentService, VisualProcessingRequired
from app.services.file_storage_service import FileStorageService, DocumentError


@pytest.mark.parametrize('extension, content, expected', [('.txt','2 computadores','2 computadores'),('.csv','Produto;Qtd\nComputador;2','Computador | 2')])
def test_text_formats(tmp_path, extension, content, expected):
    path = tmp_path / ('pedido' + extension)
    path.write_text(content, encoding='utf-8-sig')
    assert expected in DocumentService().extract(path)


def test_xlsx_and_empty_formula(tmp_path):
    path = tmp_path / 'pedido.xlsx'
    book = Workbook(); sheet = book.active
    book.save(path)
    with pytest.raises(DocumentError, match='Não encontrei'): DocumentService().extract(path)
    sheet.append(['Produto','Quantidade']); sheet.append(['PC', 2]); book.save(path)
    assert 'PC | 2' in DocumentService().extract(path)
    sheet['C2'] = '=1+1'; book.save(path)
    with pytest.raises(DocumentError, match='fórmulas'): DocumentService().extract(path)
    book.close()


def test_pdf_with_text(tmp_path):
    path = tmp_path / 'pedido.pdf'
    writer = PdfWriter(); page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 30 200 Td (2 computadores i7) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as output: writer.write(output)
    assert '2 computadores i7' in DocumentService().extract(path)


def test_limits_invalid_and_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'document_max_chars', 100)
    path = tmp_path / 'pedido.txt'; path.write_text('a' * 101)
    with pytest.raises(DocumentError, match='ultrapassa'): DocumentService().extract(path)
    path.write_text('   ')
    with pytest.raises(DocumentError, match='Não encontrei'): DocumentService().extract(path)
    path = tmp_path / 'broken.pdf'; path.write_bytes(b'bad pdf')
    with pytest.raises(DocumentError, match='Não foi possível'): DocumentService().extract(path)


def test_storage_limits_and_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'upload_path', tmp_path)
    monkeypatch.setattr(settings, 'upload_max_size_mb', 1)
    for filename, data in [('bad.exe',b'x'),('empty.txt',b''),('big.txt',b'a' * (1024*1024+1))]:
        with pytest.raises(DocumentError):
            FileStorageService().save(UploadFile(filename=filename, file=io.BytesIO(data)))
    assert list(tmp_path.iterdir()) == []
