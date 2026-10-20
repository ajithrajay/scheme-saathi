"""One-time encrypted import into an EMPTY database, before the first cloud start.

Run while the local app is stopped. DATABASE_URL and SCHEMESAATHI_ENCRYPTION_KEY
are read from .env. Pass a backup copy of the old runtime directory as argument.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from app.store import Store
from app.postgres_store import PostgresStore
load_dotenv(Path(__file__).resolve().parents[1]/'.env',encoding='utf-8')

def main():
    if len(sys.argv)!=2:
        raise SystemExit('Usage: python scripts/migrate_local.py PATH_TO_BACKUP_RUNTIME')
    source=Path(sys.argv[1]).resolve()
    if not (source/'app.sqlite3').is_file() or not (source/'encryption.key').is_file():
        raise SystemExit('The backup must contain app.sqlite3 and encryption.key.')
    url=os.environ.get('DATABASE_URL','');key=os.environ.get('SCHEMESAATHI_ENCRYPTION_KEY','')
    if not url or not key:
        raise SystemExit('Set DATABASE_URL and SCHEMESAATHI_ENCRYPTION_KEY first.')
    old=Store(source)
    from tempfile import TemporaryDirectory
    try:
        with TemporaryDirectory() as directory:
            new=PostgresStore(Path(directory),url,key)
            with new.connection() as db:
                db.execute('LOCK TABLE records,events,private_blobs IN ACCESS EXCLUSIVE MODE')
                if any(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('records','events','private_blobs')):
                    raise SystemExit('Destination is not empty. Nothing was imported.')
                for kind,id,body,updated in old.db.execute('SELECT kind,id,body,updated FROM records'):
                    if kind=='screenshot':
                        continue
                    db.execute('INSERT INTO records VALUES (%s,%s,%s,%s)',(kind,id,new.encode(old.decode(body)),updated))
                for document in old.all('document'):
                    blob=new.cipher.encrypt(old.cipher.decrypt(old.get_blob(document['blob'])))
                    db.execute('INSERT INTO private_blobs VALUES (%s,%s)',(document['blob'],blob))
                for job,body in old.db.execute('SELECT job_id,body FROM events ORDER BY seq'):
                    db.execute('INSERT INTO events(job_id,body) VALUES (%s,%s)',(job,new.encode(old.decode(body))))
            print('Import complete. Browser cookies were not copied. Keep your backup and encryption key.')
    finally:
        old.close()

if __name__=='__main__':
    main()
