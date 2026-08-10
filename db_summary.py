from database import get_session, init_db
from sqlalchemy import text
init_db()
with get_session() as s:
    co = s.execute(text("SELECT COUNT(*) FROM companies")).scalar()
    sig = s.execute(text("SELECT COUNT(*) FROM signals")).scalar()
    rows = s.execute(text("SELECT status, COUNT(*) as cnt FROM companies GROUP BY status ORDER BY cnt DESC")).fetchall()
    sources = s.execute(text("SELECT source, COUNT(*) as cnt FROM signals GROUP BY source ORDER BY cnt DESC")).fetchall()
    types = s.execute(text("SELECT signal_type, COUNT(*) as cnt FROM signals GROUP BY signal_type ORDER BY cnt DESC")).fetchall()
    print(f"Total Companies: {co}")
    print(f"Total Signals:   {sig}")
    print("\nCompany statuses:")
    for r in rows:
        print(f"  {r[0]:20} {r[1]}")
    print("\nSignal sources:")
    for r in sources:
        print(f"  {str(r[0]):15} {r[1]}")
    print("\nSignal types:")
    for r in types:
        print(f"  {r[0]:20} {r[1]}")
