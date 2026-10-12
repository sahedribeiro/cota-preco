import argparse
from getpass import getpass
from sqlalchemy import select,delete
from app.core.database import SessionLocal
from app.core.auth import hash_password
from app.models import AppUser,LoginSession


def main() -> None:
    parser=argparse.ArgumentParser(description='Criar utilizador ou substituir a palavra-passe localmente.')
    parser.add_argument('username');args=parser.parse_args()
    username=args.username.strip().casefold()
    if not 1<=len(username)<=100: raise SystemExit('Nome inválido.')
    password=getpass('Palavra-passe (mínimo 12 caracteres): ')
    if password!=getpass('Repetir palavra-passe: '): raise SystemExit('As palavras-passe não coincidem.')
    hashed=hash_password(password)
    with SessionLocal() as session:
        user=session.scalar(select(AppUser).where(AppUser.username==username))
        if not user: user=AppUser(username=username,password_hash=hashed);session.add(user)
        else:
            user.password_hash=hashed;user.active=True;user.failed_attempts=0;user.locked_until=None
            session.execute(delete(LoginSession).where(LoginSession.user_id==user.id))
        session.commit()
    print('Utilizador guardado. A palavra-passe não foi registada em texto simples.')

if __name__=='__main__': main()
