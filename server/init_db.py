import os
from pathlib import Path
import psycopg

if __name__ == '__main__':
    with psycopg.connect(os.environ['CAPTURE_DATABASE_URL'], autocommit=True) as db:
        db.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    print('Database schema v1 ready')
