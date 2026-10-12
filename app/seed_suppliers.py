"""Cadastro inicial solicitado pelo utilizador; não certifica os fornecedores."""
from sqlalchemy import func, select
from app.core.database import SessionLocal
from app.models import Supplier

TECH = 'Tecnologia e informática'
HEALTH = 'Saúde e equipamentos médicos'
SUPPLIERS = [
    ('Stylus Angola', TECH, 'https://www.stylus.co.ao/', 'Hardware, servidores, redes e licenciamento; perfil B2B.'),
    ('ITGest Angola', TECH, 'https://www.itgest.ao/itgest/', 'Consultoria de sistemas e infraestrutura tecnológica.'),
    ('TIS Angola', TECH, 'https://tis.ao/', 'Consultoria, cibersegurança, redes e software.'),
    ('IT One', TECH, None, 'Soluções e serviços de TI, consultoria de infraestruturas.'),
    ('Consult.IT', TECH, None, 'TI, telecomunicações, segurança electrónica e licenciamento.'),
    ('ITEC Angola', TECH, 'https://itec.ao/', 'Computadores, monitores e electrónica; catálogo a confirmar.'),
    ('MultiTek', TECH, 'https://www.multitek.ao/?post_type=product', 'Informática, consumíveis, escritório e segurança.'),
    ('Jupiter Systems & Solutions', TECH, None, 'Software de gestão, consultoria e integração de sistemas.'),
    ('Grupo Medtech', HEALTH, None, 'Engenharia hospitalar, equipamentos e consumíveis.'),
    ('Saúde Pró', HEALTH, None, 'Equipamentos hospitalares, diagnóstico e consumíveis.'),
    ('Appy Saúde', HEALTH, None, 'Plataforma de produtos farmacêuticos e médicos.'),
    ('GLOBALMED', HEALTH, 'https://www.globalmed.ao/', 'Medicamentos, consumíveis e apoio à gestão hospitalar.'),
    ('Saúde Prime', HEALTH, 'https://saude-prime.com/', 'Mobiliário hospitalar, consumíveis e diagnóstico.'),
    ('Teramed', HEALTH, 'https://teramed.ao/', 'Tecnologias e sistemas integrados de saúde.'),
    ('MEDENG', HEALTH, None, 'Engenharia biomédica, equipamentos e assistência técnica.'),
    ('SUMS, Lda', HEALTH, None, 'Consumíveis, produtos farmacêuticos e laboratório.'),
    ('Unimed Angola', HEALTH, None, 'Projectos hospitalares e manutenção de electromedicina.'),
    ('Neomedic Angola', HEALTH, None, 'Produtos farmacêuticos, dispositivos e diagnóstico clínico.'),
]


def seed_suppliers() -> int:
    count = 0
    with SessionLocal() as session:
        for name, category, website, description in SUPPLIERS:
            if session.scalar(select(Supplier.id).where(func.lower(Supplier.name) == name.lower())):
                continue
            session.add(Supplier(name=name, category=category, website=website, country='Angola',
                notes='Indicado pelo utilizador; actividade por verificar. ' + description + (' Website sugerido por pesquisa pública; confirmar antes de activar leitura.' if website else ' Website ainda por identificar.'),
                source_kind='user_list', source_reference=website, reviewed=False, active=True, scraping_enabled=False))
            count += 1
        session.commit()
    return count


if __name__ == '__main__':
    print(f'{seed_suppliers()} fornecedores adicionados; registos existentes preservados.')
