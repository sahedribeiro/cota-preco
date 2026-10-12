# Cota.Preço.AI

Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, SQLite, Jinja2, HTMX e Tailwind. **EASY WAY**.

## Entrega actual

Incrementos 0 e 1: regras actualizadas e catálogo persistente em `/catalog`. Login simples acrescentado imediatamente depois. Os fluxos anteriores continuam disponíveis; o novo MVP completo ainda está em construção.

- Product, SupplierProduct e PriceRecord: associação canónica, preços por fornecedor e histórico preservado. O preço elegível mais recente aparece no catálogo; expirados e rejeitados não concorrem. Datas desconhecidas são identificadas; preços antigos continuam visíveis. `CATALOG_STALE_DAYS=60`.
- Ofertas antigas copiadas para PriceRecord pela migração. Proformas antigas disponibilizadas nas importações pendentes para confirmar fornecedor e importar linhas restantes. Não apaga dados, ficheiros ou requisições existentes.
- Excel/CSV: mapeamento reutilizável por fornecedor, separador decimal configurável, amostra antes de gravar; sem chamadas IA. Todas as linhas são conservadas; valores inválidos ficam para revisão. Fórmulas devem ser exportadas como valores; folhas com cabeçalhos diferentes devem ser importadas separadamente.
- PDF/texto: extracção por blocos, sem limite global de 8.000 caracteres/20 linhas no catálogo. PDFs digitalizados e JPG/PNG usam visão OpenAI com PyMuPDF. Blocos concluídos são guardados e uma falha pode ser retomada. Respostas incompletas são recusadas, sem corte silencioso. Upload limitado a 10 MB; XLSX descomprimido até 30 MB.
- Fornecedor identificado pode ser confirmado/criado num único envio. Preços válidos com confiança ≥85 são aceites; dúvidas vão para o histórico com estado **Rever**. A confiança na extracção não confirma equivalência entre produtos.
- Leitores web existentes também gravam no catálogo e mantêm cache de 24 horas. Respeitam robots.txt, redes privadas e bloqueios. Websites são complementares; não se tenta contornar lojas bloqueadas.
- Login por utilizador/senha, hash PBKDF2, sessão de oito horas, logout, bloqueio temporário após cinco falhas e CSRF. Sem contas ou palavras-passe predefinidas. Dados são partilhados pelos utilizadores autorizados; não há multitenancy.

## Instalar e executar (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
# Só se ainda não existir .env:
Copy-Item .env.example .env
python -m alembic upgrade head
python -m app.manage_users administrador
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

O comando de utilizador pede e confirma a palavra-passe sem a mostrar. Executá-lo novamente altera a palavra-passe e invalida as sessões desse utilizador. Não sobrescrever um `.env` existente.

Abrir [login](http://127.0.0.1:8000/login) e depois [catálogo](http://127.0.0.1:8000/catalog). Sem conta, todas as páginas de negócio e downloads exigem login. `/health` e `/api/health` são públicos e confirmam actividade do processo. O arranque não altera o esquema.

Para servir outras máquinas, configurar HTTPS no proxy e `AUTH_COOKIE_SECURE=true`. Autenticação vem ligada por defeito; `AUTH_ENABLED=false` é apenas para testes/desenvolvimento isolado. Usar um worker: a pesquisa em segundo plano mantém o progresso em memória. Um reinício interrompe tarefas, mas conserva resultados já gravados.

## Importar preços

1. Catálogo → Importar; seleccionar fornecedor se conhecido e carregar o ficheiro.
2. Excel/CSV: mapear colunas e guardar. PDF/imagem/TXT: iniciar extracção, confirmar o vendedor e guardar.
3. Abrir um produto para ver todas as fontes, datas, preços e excepções. Correcções de preços acrescentam registos; os valores originais permanecem no histórico.
4. Associar produtos de fornecedores ao mesmo produto canónico para os visualizar lado a lado.

IA e OCR passam exclusivamente pelo AIService, com `OPENAI_API_KEY` e `OPENAI_MODEL` no `.env`. O modelo configurado tem de suportar imagens para OCR. Cada bloco consome tokens; não há retries automáticos. Documentação: [entrada de imagens na OpenAI](https://developers.openai.com/api/docs/guides/images-vision). Os testes não usam a chave real.

## Dados e manutenção

SQLite: `data/cota-preco-ai.db`; ficheiros: `data/uploads`. Fazer backup de ambos antes de mover a aplicação. Dinheiro usa Decimal/Numeric. IVA, moeda, data e stock ausentes não são inventados. Sem compras, pagamentos, contactos ou envio automático de cotações.

```powershell
python -m alembic upgrade head
python -m alembic check
python -m pytest -q --basetemp=work/pytest-check
```

Migrações novas: `0007` conserva a base de rascunhos comerciais iniciada anteriormente; `aab15f012df1` cria o catálogo e preserva os dados antigos; `7db06ee9cb27` cria utilizadores e sessões. Não executar downgrade numa base com dados a preservar.

Assets locais incluídos. `npm ci` e `npm run build` apenas para regenerar CSS. `docker compose up --build app` arranca a aplicação com SQLite; criar o utilizador dentro do contentor. PostgreSQL opcional: `pip install -e ".[postgres]"` e configurar `DATABASE_URL` antes das migrações.

## Pendentes

- Empresas emissoras/clientes reutilizáveis; actualmente os campos são editáveis por cotação.
- Numeração, emissão e versões imutáveis de cotações; câmbio manual datado.
- Validar outros documentos reais com descontos, IVA misto e layouts diferentes; obter o Excel de referência Girassol.
- Conectores API e melhorias de pesquisa online, depois do fluxo documental.

## Incremento correctivo: solicitação → matriz → cotação

A página inicial separa a solicitação do cliente (texto ou um ficheiro) de até cinco proformas. O cliente nunca vira fornecedor. Proformas e catálogo estão activos por defeito; lojas online só por pedido explícito. A identificação após o upload reutiliza a extracção em cache. Layouts locais reconhecidos não usam OpenAI; os restantes usam blocos e resultados estruturados via AIService, com checkpoints para não repetir blocos pagos.

A matriz preserva números e quantidades da solicitação, apresenta preços, totais e extremos, fontes em tooltip, catálogo datado, divergências de totais e padrões proporcionais. AOA assumida fica identificada (documento angolano ou moeda predefinida explicitamente no formulário). IVA desconhecido permite comparação nominal; IVA incluído/excluído sem taxa bloqueia a normalização. Valores em moedas diferentes não são somados. O catálogo recebe todas as linhas, conserva histórico e não duplica fornecedor + referência de proforma + linha. Equivalências duvidosas podem ser confirmadas na matriz e reutilizadas.

A cotação usa **markup sobre custo**, arredondamento unitário ROUND_HALF_UP e IVA sobre o subtotal. É diferente de margem sobre venda. Oferece menor, maior, média, fornecedor e escolha manual; recálculo sem IA. Empresa, cliente, condições e logótipo são editáveis por cotação. Excel: Matriz, Cotação, Margem (interno), Fontes. PDF interno da matriz e PDF do cliente separado, sem fornecedores/custos/margem. Não há envio automático nem certificação fiscal.

Aceitação real: `tests/fixtures/girassol/` contém a OC e três PDFs recuperados dos uploads do utilizador. `pytest tests/test_matrix_flow.py` verifica 37 × 3, totais, cotação 132.978.930,49 Kz, alertas, importação idempotente, catálogo sem proformas e exportações. IA simulada nos testes; os PDFs e XLSX são lidos localmente sem simulação. O Excel de referência mencionado ainda não foi disponibilizado, pelo que a conformidade visual com esse modelo está pendente. A linha 13 da NEOMED tem divergência real de 2 Kz (400 × 9.725,63 vs 3.890.250,00) e é correctamente assinalada.

OCR de páginas/imagens usa um limite de saída até pelo menos 8.000 tokens por página para comportar listas extensas; texto é dividido em blocos pequenos. Nenhuma chamada real OpenAI foi necessária para o fixture Girassol. As comparações recentes podem ser reabertas em `/matrices`.

## Importações partilhadas

Catálogo, proformas de requisições e comparação usam a mesma extracção por blocos e o mesmo registo de preços. O endpoint antigo de comparação continua compatível; novos pedidos devem começar pela solicitação na página inicial. CSV/XLSX do catálogo conservam o mapeamento manual de colunas. Documentos já revistos e relatórios anteriores permanecem disponíveis.

A reutilização do mesmo documento evita novas chamadas para blocos concluídos e preços duplicados. AOA é a predefinição do catálogo, identificada quando assumida. Mesma referência com dados de linha alterados conserva os preços anteriores e coloca a nova versão em revisão. Cabeçalhos contraditórios também exigem revisão. PDFs com páginas de texto e digitalizadas processam ambos os tipos; as imagens só usam IA quando necessário.

`tests/test_shared_imports.py` cobre a proforma real NEOMED importada por duas entradas sem duplicar os 37 preços, CSV sintético com 250 artigos, PDF misto sintético com IA simulada, cache e versões com preços alterados. Os testes não fazem chamadas pagas. A validação de OCR com novos documentos reais e de descontos comerciais permanece pendente.

## GitHub e dados locais

O repositório contém apenas código e recursos da aplicação. `.env`, base de dados, uploads e documentos comerciais em `tests/fixtures/girassol/` ficam fora do Git. Os oito testes que dependem desses documentos são ignorados quando os ficheiros não estão disponíveis; os restantes testes continuam executáveis num clone novo. Nunca adicionar chaves ou documentos de clientes ao Git.
