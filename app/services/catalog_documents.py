import csv
import io
from pathlib import Path
from zipfile import ZipFile
from openpyxl import load_workbook
import pymupdf
from app.services.file_storage_service import DocumentError


def text_blocks(text: str, size: int=4000):
    block=[]; length=0
    for line in text.splitlines():
        # Oversized lines are still fully processed, in explicitly labelled fragments.
        pieces=[line[i:i+size] for i in range(0,len(line),size)] or ['']
        for piece in pieces:
            if block and length+len(piece)+1>size:
                yield '\n'.join(block);block=[];length=0
            block.append(piece);length+=len(piece)+1
    if block: yield '\n'.join(block)


def table_rows(path: Path) -> tuple[list[str],list[list[str]]]:
    if path.suffix=='.csv':
        raw=path.read_bytes()
        try: text=raw.decode('utf-8-sig')
        except UnicodeDecodeError: text=raw.decode('cp1252')
        try: dialect=csv.Sniffer().sniff(text[:4096],delimiters=',;\t|')
        except csv.Error: dialect=csv.excel
        rows=list(csv.reader(io.StringIO(text),dialect))
    else:
        with ZipFile(path) as archive:
            if sum(e.file_size for e in archive.infolist())>30*1024*1024: raise DocumentError('XLSX demasiado grande depois de descomprimido.')
        book=load_workbook(path,read_only=True,data_only=False,keep_links=False)
        rows=[];header=None
        try:
            for sheet in book:
                sheet_rows=[]
                for row in sheet:
                    if any(c.data_type=='f' for c in row): raise DocumentError('Exporta as fórmulas como valores antes de importar.')
                    values=[str(c.value) if c.value is not None else '' for c in row]
                    if any(values): sheet_rows.append(values)
                if not sheet_rows: continue
                if header is None: header=sheet_rows[0];rows.append(header)
                elif header!=sheet_rows[0]: raise DocumentError('As folhas têm cabeçalhos diferentes. Importa cada estrutura num ficheiro separado.')
                rows.extend(sheet_rows[1:])
        finally: book.close()
    rows=[r for r in rows if any(str(v).strip() for v in r)]
    if len(rows)<2: raise DocumentError('Falta o cabeçalho ou as linhas de produtos.')
    return rows[0],rows[1:]


def document_blocks(path: Path):
    if path.suffix=='.txt':
        raw=path.read_bytes()
        try: text=raw.decode('utf-8-sig')
        except UnicodeDecodeError: text=raw.decode('cp1252')
        for block in text_blocks(text): yield block,None
        return
    with pymupdf.open(path) as doc:
        if doc.needs_pass: raise DocumentError('Documento protegido por palavra-passe.')
        for index,page in enumerate(doc):
            text=page.get_text() if path.suffix=='.pdf' else ''
            if text.strip():
                for block in text_blocks(text): yield f'Página {index+1}\n{block}',None
            else:
                scale=min(2,2000/max(page.rect.width,page.rect.height))
                pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False)
                yield f'Página {index+1}: extrai as linhas visíveis e identifica o vendedor se visível.',pix.tobytes('png')
