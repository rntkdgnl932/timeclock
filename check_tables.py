# check_tables.py
import sqlite3
from timeclock.settings import DB_PATH

def main():
    print("DB_PATH =", DB_PATH)

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    tables = [r[0] for r in rows]
    print("TABLES =", tables)
    conn.close()

if __name__ == "__main__":
    main()
