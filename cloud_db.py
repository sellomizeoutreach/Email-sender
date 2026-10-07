"""
cloud_db.py - Cloud PostgreSQL persistence and compatibility layer for Sellomize Reach.
Enables seamless connectivity to Cloud PostgreSQL providers (Supabase, Neon, Railway, AWS RDS)
while providing 100% transparent SQLite dialect compatibility for all CRM, template, outbox,
and settings queries.
"""

import os
import sys
import re
import json
import time
import random
import threading
import logging
from typing import Dict, List, Optional, Any, Union, Tuple

logger = logging.getLogger("cloud_db")

try:
    import psycopg2
    from psycopg2 import sql as pg_sql
    from psycopg2.extensions import ISOLATION_LEVEL_READ_COMMITTED
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    pg_sql = None
    PSYCOPG2_AVAILABLE = False

_thread_local = threading.local()

TABLES_WITH_ID = {
    'contacts',
    'emails',
    'templates',
    'smtp_accounts',
    'notifications',
    'sequence_rules',
    'campaign_campaigns',
    'campaign_steps',
    'campaign_contacts',
    'campaign_events',
    'campaign_images',
    'images',
    'send_jobs',
}

class RowProxy(dict):
    """
    Lightweight, high-performance dict subclass that also supports integer indexing
    and .keys() identically to sqlite3.Row.
    """
    __slots__ = ('_values', '_cols')

    def __init__(self, cols: List[str], values: Union[Tuple, List]):
        super().__init__(zip(cols, values))
        self._values = list(values)
        self._cols = list(cols)

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, (int, slice)):
            return self._values[item]
        return super().__getitem__(item)

    def keys(self) -> List[str]:
        return self._cols

    def __repr__(self) -> str:
        return f"<RowProxy {super().__repr__()}>"


def translate_sqlite_to_pg(sql: str) -> Tuple[str, bool]:
    """
    Translates an SQLite query to PostgreSQL dialect:
    - Replaces parameter placeholders '?' outside quotes with '%s'
    - INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL PRIMARY KEY
    - CREATE VIEW IF NOT EXISTS -> CREATE OR REPLACE VIEW
    - ALTER TABLE <table> ADD COLUMN <col> <def> -> ALTER TABLE <table> ADD COLUMN IF NOT EXISTS <col> <def>
    - INSERT OR IGNORE INTO <table> ... -> INSERT INTO <table> ... ON CONFLICT DO NOTHING
    - If INSERT INTO a table with an 'id' column and no RETURNING, appends RETURNING id
    Returns (translated_sql, expects_returning_id)
    """
    res = []
    in_single = False
    in_double = False
    escaped = False
    for ch in sql:
        if ch == '\\' and not escaped:
            escaped = True
            res.append(ch)
            continue
        if ch == "'" and not in_double and not escaped:
            in_single = not in_single
        elif ch == '"' and not in_single and not escaped:
            in_double = not in_double
        elif ch == '?' and not in_single and not in_double:
            res.append('%s')
            escaped = False
            continue
        res.append(ch)
        escaped = False
    translated = "".join(res)

    # 1. DDL auto-increment translation
    translated = re.sub(
        r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
        'SERIAL PRIMARY KEY',
        translated,
        flags=re.IGNORECASE
    )

    # 2. View creation translation
    translated = re.sub(
        r'\bCREATE\s+VIEW\s+IF\s+NOT\s+EXISTS\b',
        'CREATE OR REPLACE VIEW',
        translated,
        flags=re.IGNORECASE
    )

    # 3. Safe column migration: ADD COLUMN IF NOT EXISTS
    translated = re.sub(
        r'\bALTER\s+TABLE\s+([a-zA-Z0-9_]+)\s+ADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS\b)',
        r'ALTER TABLE \1 ADD COLUMN IF NOT EXISTS ',
        translated,
        flags=re.IGNORECASE
    )

    # 4. INSERT OR IGNORE translation
    if re.search(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b', translated, re.IGNORECASE):
        translated = re.sub(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b', 'INSERT INTO', translated, flags=re.IGNORECASE)
        if not re.search(r'\bON\s+CONFLICT\b', translated, re.IGNORECASE):
            translated = translated.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'

    # 5. Check if we should append RETURNING id for lastrowid tracking
    expects_returning_id = False
    m_ins = re.match(r'^\s*INSERT\s+INTO\s+([a-zA-Z0-9_]+)', translated, re.IGNORECASE)
    if m_ins:
        tbl_name = m_ins.group(1).lower()
        if tbl_name in TABLES_WITH_ID and 'RETURNING' not in translated.upper():
            translated = translated.rstrip().rstrip(';') + ' RETURNING id'
            expects_returning_id = True

    return translated, expects_returning_id


class PostgresCursorWrapper:
    """
    Wraps a psycopg2 cursor to mirror the sqlite3.Cursor interface,
    converting queries on the fly and formatting results as RowProxy objects.
    """
    def __init__(self, raw_cursor, raw_conn):
        self._cursor = raw_cursor
        self._conn = raw_conn
        self._lastrowid = None
        self._expects_returning = False

    def execute(self, sql: str, params: Optional[Union[Tuple, List, Dict]] = None):
        translated_sql, expects_returning = translate_sqlite_to_pg(sql)
        self._expects_returning = expects_returning
        self._lastrowid = None

        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                if params is not None:
                    self._cursor.execute(translated_sql, params)
                else:
                    self._cursor.execute(translated_sql)

                if self._expects_returning:
                    try:
                        res = self._cursor.fetchone()
                        if res and len(res) > 0:
                            self._lastrowid = res[0]
                    except Exception:
                        self._lastrowid = None
                return self
            except Exception as e:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                err_str = str(e).lower()
                if ("deadlock" in err_str or "40p01" in err_str or "lock wait timeout" in err_str) and attempt < max_attempts - 1:
                    sleep_s = 0.15 * (attempt + 1) + random.uniform(0.05, 0.25)
                    time.sleep(sleep_s)
                    continue
                raise e

    def executemany(self, sql: str, seq_of_params):
        translated_sql, _ = translate_sqlite_to_pg(sql)
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                self._cursor.executemany(translated_sql, seq_of_params)
                return self
            except Exception as e:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                err_str = str(e).lower()
                if ("deadlock" in err_str or "40p01" in err_str or "lock wait timeout" in err_str) and attempt < max_attempts - 1:
                    sleep_s = 0.15 * (attempt + 1) + random.uniform(0.05, 0.25)
                    time.sleep(sleep_s)
                    continue
                raise e

    def fetchone(self) -> Optional[RowProxy]:
        try:
            row = self._cursor.fetchone()
            if row is None:
                return None
            if self._cursor.description:
                cols = [desc[0] for desc in self._cursor.description]
                return RowProxy(cols, row)
            return row
        except Exception:
            return None

    def fetchall(self) -> List[RowProxy]:
        try:
            rows = self._cursor.fetchall()
            if not rows:
                return []
            if self._cursor.description:
                cols = [desc[0] for desc in self._cursor.description]
                return [RowProxy(cols, r) for r in rows]
            return rows
        except Exception:
            return []

    def fetchmany(self, size: Optional[int] = None) -> List[RowProxy]:
        try:
            rows = self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()
            if not rows:
                return []
            if self._cursor.description:
                cols = [desc[0] for desc in self._cursor.description]
                return [RowProxy(cols, r) for r in rows]
            return rows
        except Exception:
            return []

    def __iter__(self):
        if self._cursor.description:
            cols = [desc[0] for desc in self._cursor.description]
            for r in self._cursor:
                yield RowProxy(cols, r)
        else:
            for r in self._cursor:
                yield r

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> Optional[int]:
        return self._lastrowid

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass


class PostgresConnectionWrapper:
    """
    Wraps a psycopg2 connection to mirror sqlite3.Connection semantics,
    providing transaction management and thread-pooled reuse.
    """
    def __init__(self, raw_conn, url: str):
        self._conn = raw_conn
        self._url = url
        self.row_factory = None

    def cursor(self) -> PostgresCursorWrapper:
        return PostgresCursorWrapper(self._conn.cursor(), self._conn)

    def commit(self):
        try:
            self._conn.commit()
        except Exception as e:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise e

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        """
        In connection-pooled environment, commit pending work and keep the
        socket open in the thread cache for maximum query performance.
        """
        try:
            self._conn.commit()
        except Exception:
            pass

    def real_close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()


def get_db_config_path() -> str:
    """Determine JSON file path for persistent cloud DB config."""
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        config_dir = os.path.join(appdata, "SellomizeReach")
    else:
        config_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "sellomize_db_config.json")


def normalize_database_url(url: str) -> str:
    """
    Sanitizes and normalizes user-provided PostgreSQL connection URLs:
    - Strips surrounding brackets from passwords: [Sellomize###0300] -> Sellomize###0300
    - URL-encodes special characters in passwords (e.g. # -> %23)
    - If direct Supabase IPv6 host (db.[ref].supabase.co) fails IPv4 DNS, auto-routes via IPv4 pooler
    """
    if not url:
        return ""
    clean = url.strip()
    if clean.startswith("postgres://"):
        clean = "postgresql://" + clean[11:]

    # Match: postgresql://username:password@host:port/dbname
    m = re.match(r'^(postgresql://)([^:]+):(.*)@([^:/]+)(?::(\d+))?(/.*)$', clean)
    if m:
        prefix, user, raw_pw, host, port, db_path = m.groups()
        port = port or "5432"

        if raw_pw.startswith('[') and raw_pw.endswith(']'):
            raw_pw = raw_pw[1:-1]

        import urllib.parse
        unquoted = urllib.parse.unquote(raw_pw)
        quoted_pw = urllib.parse.quote(unquoted, safe='')

        m_sb = re.match(r'^db\.([a-z0-9]+)\.supabase\.co$', host, re.IGNORECASE)
        if m_sb:
            proj_ref = m_sb.group(1)
            import socket
            can_resolve_ipv4 = False
            try:
                socket.gethostbyname(host)
                can_resolve_ipv4 = True
            except Exception:
                can_resolve_ipv4 = False

            if not can_resolve_ipv4:
                if '.' not in user:
                    user = f"{user}.{proj_ref}"
                pooler_region = "ap-northeast-1" if proj_ref == "ledgdhmagbpmjylywmnm" else "us-east-1"
                host = f"aws-0-{pooler_region}.pooler.supabase.com"
                port = "5432"

        return f"{prefix}{user}:{quoted_pw}@{host}:{port}{db_path}"

    return clean


DEFAULT_CLOUD_DB_URL = "postgresql://postgres.ledgdhmagbpmjylywmnm:Sellomize%23%23%230300@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"


def get_database_url() -> str:
    """
    Retrieve configured Cloud PostgreSQL connection URL.
    Checks:
    1. SELLOMIZE_FORCE_SQLITE override
    2. Environment variables: DATABASE_URL, POSTGRES_URL, SUPABASE_DB_URL
    3. Streamlit secrets: st.secrets['DATABASE_URL'] or st.secrets['postgres']['url']
    4. Persistent config file: sellomize_db_config.json
    5. Fallback to production Cloud PostgreSQL if none of the above are set
    """
    if os.environ.get("SELLOMIZE_FORCE_SQLITE", "").lower() in ("1", "true", "yes"):
        return ""

    raw_url = ""
    for env_key in ("DATABASE_URL", "POSTGRES_URL", "SUPABASE_DB_URL", "SELLOMIZE_POSTGRES_URL"):
        val = os.environ.get(env_key)
        if val and val.strip():
            raw_url = val.strip()
            break

    if not raw_url:
        try:
            import streamlit as st
            if hasattr(st, "secrets"):
                if "DATABASE_URL" in st.secrets and str(st.secrets["DATABASE_URL"]).strip():
                    raw_url = str(st.secrets["DATABASE_URL"]).strip()
                elif "postgres" in st.secrets and isinstance(st.secrets["postgres"], dict) and st.secrets["postgres"].get("url"):
                    raw_url = str(st.secrets["postgres"]["url"]).strip()
        except Exception:
            pass

    if not raw_url:
        cfg_path = get_db_config_path()
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    url = data.get("database_url", "")
                    if url and url.strip():
                        raw_url = url.strip()
            except Exception:
                pass

    if not raw_url:
        raw_url = DEFAULT_CLOUD_DB_URL

    return normalize_database_url(raw_url) if raw_url else ""



def set_database_url(url: str) -> None:
    """Save or clear PostgreSQL connection URL."""
    cfg_path = get_db_config_path()
    clean_url = (url or "").strip()
    data = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data["database_url"] = clean_url
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write DB config: {e}")

    if clean_url:
        os.environ["DATABASE_URL"] = clean_url
    else:
        os.environ.pop("DATABASE_URL", None)

    close_all_pg_connections()


def is_postgres_active() -> bool:
    """Return True if Cloud PostgreSQL is configured and psycopg2 is available."""
    if not PSYCOPG2_AVAILABLE:
        return False
    url = get_database_url()
    return bool(url and (url.startswith("postgresql://") or url.startswith("postgres://")))


def get_postgres_connection(url: Optional[str] = None) -> PostgresConnectionWrapper:
    """
    Get or create a thread-local cached PostgreSQL connection.
    Pings the connection before returning to automatically recover from idle drops.
    """
    if not PSYCOPG2_AVAILABLE:
        raise RuntimeError("psycopg2 is not installed. Please run: pip install psycopg2-binary")

    pg_url = normalize_database_url(url or get_database_url())
    if not pg_url:
        raise ValueError("No PostgreSQL URL configured.")

    # Check cached thread-local connection
    cached: Optional[PostgresConnectionWrapper] = getattr(_thread_local, "pg_conn", None)
    if cached is not None and cached._url == pg_url and not cached._conn.closed:
        try:
            with cached._conn.cursor() as cur:
                cur.execute("SELECT 1;")
            return cached
        except Exception:
            try:
                cached.real_close()
            except Exception:
                pass
            _thread_local.pg_conn = None

    # Connect to PostgreSQL with SSL support for cloud providers
    connect_kwargs: Dict[str, Any] = {"connect_timeout": 10}
    if "sslmode" not in pg_url and any(x in pg_url for x in ("supabase.co", "neon.tech", "aws", "railway", "render")):
        connect_kwargs["sslmode"] = "require"

    raw_conn = psycopg2.connect(pg_url, **connect_kwargs)
    raw_conn.autocommit = False
    wrapped = PostgresConnectionWrapper(raw_conn, pg_url)
    _thread_local.pg_conn = wrapped
    return wrapped


def close_all_pg_connections():
    """Close and discard thread-local cached connection."""
    cached: Optional[PostgresConnectionWrapper] = getattr(_thread_local, "pg_conn", None)
    if cached is not None:
        try:
            cached.real_close()
        except Exception:
            pass
        _thread_local.pg_conn = None


def test_pg_connection(url: Optional[str] = None) -> Tuple[bool, str, float]:
    """
    Test connection to PostgreSQL database.
    Returns: (is_success, status_message, latency_ms)
    """
    if not PSYCOPG2_AVAILABLE:
        return False, "Driver missing: psycopg2-binary is not installed.", 0.0

    target_url = normalize_database_url(url or get_database_url())
    if not target_url:
        return False, "No connection URL provided.", 0.0

    t0 = time.time()
    try:
        connect_kwargs = {"connect_timeout": 10}
        if "sslmode" not in target_url and any(x in target_url for x in ("supabase.co", "neon.tech", "aws", "railway")):
            connect_kwargs["sslmode"] = "require"

        conn = psycopg2.connect(target_url, **connect_kwargs)
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            ver = cur.fetchone()[0]
        conn.close()
        elapsed_ms = round((time.time() - t0) * 1000, 1)

        # Extract provider host for friendly summary
        host_match = re.search(r'@([^:/]+)', target_url)
        host_str = host_match.group(1) if host_match else "PostgreSQL"
        return True, f"Connected to {host_str} in {elapsed_ms}ms ({ver.split()[0]} {ver.split()[1]})", elapsed_ms
    except Exception as e:
        elapsed_ms = round((time.time() - t0) * 1000, 1)
        return False, f"Connection failed ({elapsed_ms}ms): {e}", elapsed_ms


def migrate_sqlite_to_postgres(sqlite_path: str, pg_url: Optional[str] = None) -> Tuple[bool, str]:
    """
    Migrate all data from local SQLite database to Cloud PostgreSQL:
    - system_config
    - contacts
    - templates
    - smtp_accounts
    - emails
    - notifications
    - sequence_rules
    - processed_inbox_messages
    """
    import sqlite3
    if not os.path.exists(sqlite_path):
        return False, f"Source SQLite database not found at {sqlite_path}"

    target_url = pg_url or get_database_url()
    if not target_url:
        return False, "No Cloud PostgreSQL URL provided."

    sq_conn = sqlite3.connect(sqlite_path)
    sq_conn.row_factory = sqlite3.Row

    try:
        # Import init_db from database module to ensure all tables exist in Postgres
        from database import init_db
        pg_conn = get_postgres_connection(target_url)
        
        # Initialize target schema on PostgreSQL using active pg_conn
        init_db(conn=pg_conn)

        cur_sq = sq_conn.cursor()
        cur_pg = pg_conn.cursor()

        def table_exists_sq(tbl: str) -> bool:
            cur_sq.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
            return cur_sq.fetchone() is not None

        def safe_reset_seq(tbl: str, col: str = 'id'):
            try:
                cur_pg.execute(f"""
                    DO $$
                    DECLARE
                        seq_name text;
                        max_id bigint;
                    BEGIN
                        seq_name := pg_get_serial_sequence('{tbl}', '{col}');
                        IF seq_name IS NOT NULL THEN
                            EXECUTE format('SELECT COALESCE(MAX(%I), 0) FROM %I', '{col}', '{tbl}') INTO max_id;
                            IF max_id > 0 THEN
                                EXECUTE format('SELECT setval(%L, %s, true)', seq_name, max_id);
                            ELSE
                                EXECUTE format('SELECT setval(%L, 1, false)', seq_name);
                            END IF;
                        END IF;
                    END $$;
                """)
            except Exception as ex_seq:
                logger.warning(f"Sequence reset warning for {tbl}.{col}: {ex_seq}")

        stats = {
            "configs": 0, "contacts": 0, "templates": 0, "smtp_accounts": 0,
            "emails": 0, "notifications": 0, "sequence_rules": 0, "inbox_messages": 0,
            "campaigns": 0, "campaign_steps": 0, "campaign_contacts": 0
        }

        # 1. system_config
        if table_exists_sq("system_config"):
            cur_sq.execute("SELECT key, value FROM system_config")
            cfg_rows = cur_sq.fetchall()
            for r in cfg_rows:
                cur_pg.execute("""
                    INSERT INTO system_config (key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (r["key"], r["value"]))
            stats["configs"] = len(cfg_rows)

        # 2. contacts
        if table_exists_sq("contacts"):
            cur_sq.execute("SELECT * FROM contacts")
            c_rows = cur_sq.fetchall()
            for r in c_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO contacts ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["contacts"] = len(c_rows)
            safe_reset_seq("contacts")

        # 3. templates
        if table_exists_sq("templates"):
            cur_sq.execute("SELECT * FROM templates")
            t_rows = cur_sq.fetchall()
            for r in t_rows:
                r_dict = dict(r)
                raw_name = (r_dict.get("name") or r_dict.get("template_name") or "").strip()
                raw_cat = (r_dict.get("template_category") or "").strip()
                cur_pg.execute("""
                    SELECT id FROM templates
                    WHERE LOWER(TRIM(template_name)) = %s 
                       OR LOWER(TRIM(name)) = %s
                       OR LOWER(TRIM(template_category)) = %s
                    ORDER BY id ASC LIMIT 1
                """, (raw_name.lower(), raw_name.lower(), raw_cat.lower()))
                exist_t = cur_pg.fetchone()
                if exist_t:
                    upd_cols = [k for k in r_dict.keys() if k != "id"]
                    set_clause = ", ".join(f"{k} = %s" for k in upd_cols)
                    cur_pg.execute(f"UPDATE templates SET {set_clause} WHERE id = %s", tuple(r_dict[k] for k in upd_cols) + (exist_t[0],))
                else:
                    cols = list(r_dict.keys())
                    placeholders = ", ".join(["%s"] * len(cols))
                    col_str = ", ".join(cols)
                    cur_pg.execute(f"""
                        INSERT INTO templates ({col_str})
                        VALUES ({placeholders})
                        ON CONFLICT (id) DO NOTHING
                    """, tuple(r_dict[k] for k in cols))
            stats["templates"] = len(t_rows)
            safe_reset_seq("templates")

        # 4. smtp_accounts
        if table_exists_sq("smtp_accounts"):
            cur_sq.execute("SELECT * FROM smtp_accounts")
            s_rows = cur_sq.fetchall()
            for r in s_rows:
                acc_email = (r["email"] or "").strip()
                cur_pg.execute("SELECT id FROM smtp_accounts WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))", (acc_email,))
                exist_acc = cur_pg.fetchone()
                if exist_acc:
                    update_cols = [k for k in r.keys() if k != "id"]
                    set_clause = ", ".join(f"{k} = %s" for k in update_cols)
                    cur_pg.execute(f"UPDATE smtp_accounts SET {set_clause} WHERE id = %s", tuple(r[k] for k in update_cols) + (exist_acc[0],))
                else:
                    cols = list(r.keys())
                    placeholders = ", ".join(["%s"] * len(cols))
                    col_str = ", ".join(cols)
                    cur_pg.execute(f"""
                        INSERT INTO smtp_accounts ({col_str})
                        VALUES ({placeholders})
                        ON CONFLICT (id) DO NOTHING
                    """, tuple(r[k] for k in cols))
            stats["smtp_accounts"] = len(s_rows)
            safe_reset_seq("smtp_accounts")

        # 5. emails
        if table_exists_sq("emails"):
            cur_sq.execute("SELECT * FROM emails")
            e_rows = cur_sq.fetchall()
            for r in e_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO emails ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["emails"] = len(e_rows)
            safe_reset_seq("emails")

        # 6. notifications
        if table_exists_sq("notifications"):
            cur_sq.execute("SELECT * FROM notifications")
            n_rows = cur_sq.fetchall()
            for r in n_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO notifications ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["notifications"] = len(n_rows)
            safe_reset_seq("notifications")

        # 7. sequence_rules
        if table_exists_sq("sequence_rules"):
            cur_sq.execute("SELECT * FROM sequence_rules")
            sr_rows = cur_sq.fetchall()
            for r in sr_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO sequence_rules ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["sequence_rules"] = len(sr_rows)
            safe_reset_seq("sequence_rules")

        # 8. processed_inbox_messages
        if table_exists_sq("processed_inbox_messages"):
            cur_sq.execute("SELECT * FROM processed_inbox_messages")
            pm_rows = cur_sq.fetchall()
            for r in pm_rows:
                cur_pg.execute("""
                    INSERT INTO processed_inbox_messages (message_id, sender_email, subject, mailbox, processed_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO NOTHING
                """, (r["message_id"], r["sender_email"], r["subject"], r["mailbox"], r["processed_at"]))
            stats["inbox_messages"] = len(pm_rows)

        # 9. campaign_campaigns
        if table_exists_sq("campaign_campaigns"):
            cur_sq.execute("SELECT * FROM campaign_campaigns")
            camp_rows = cur_sq.fetchall()
            for r in camp_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO campaign_campaigns ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["campaigns"] = len(camp_rows)
            safe_reset_seq("campaign_campaigns")

        # 10. campaign_steps
        if table_exists_sq("campaign_steps"):
            cur_sq.execute("SELECT * FROM campaign_steps")
            cs_rows = cur_sq.fetchall()
            for r in cs_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO campaign_steps ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["campaign_steps"] = len(cs_rows)
            safe_reset_seq("campaign_steps")

        # 11. campaign_contacts
        if table_exists_sq("campaign_contacts"):
            cur_sq.execute("SELECT * FROM campaign_contacts")
            cc_rows = cur_sq.fetchall()
            for r in cc_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO campaign_contacts ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["campaign_contacts"] = len(cc_rows)
            safe_reset_seq("campaign_contacts")

        # 12. campaign_events
        if table_exists_sq("campaign_events"):
            cur_sq.execute("SELECT * FROM campaign_events")
            ce_rows = cur_sq.fetchall()
            for r in ce_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO campaign_events ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            safe_reset_seq("campaign_events")

        # 13. campaign_images
        if table_exists_sq("campaign_images"):
            cur_sq.execute("SELECT * FROM campaign_images")
            ci_rows = cur_sq.fetchall()
            for r in ci_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO campaign_images ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            safe_reset_seq("campaign_images")

        # 14. images (Lead image library)
        if table_exists_sq("images"):
            cur_sq.execute("SELECT * FROM images")
            img_rows = cur_sq.fetchall()
            for r in img_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO images ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["images"] = len(img_rows)

        # 15. send_jobs (DB-backed delivery queue)
        if table_exists_sq("send_jobs"):
            cur_sq.execute("SELECT * FROM send_jobs")
            job_rows = cur_sq.fetchall()
            for r in job_rows:
                cols = list(r.keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_str = ", ".join(cols)
                cur_pg.execute(f"""
                    INSERT INTO send_jobs ({col_str})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO NOTHING
                """, tuple(r[k] for k in cols))
            stats["send_jobs"] = len(job_rows)

        # Deduplicate templates if any redundant records exist
        try:
            from database import deduplicate_templates
            deduplicate_templates(conn=pg_conn)
        except Exception as dedup_ex:
            logger.warning(f"Template deduplication note during migration: {dedup_ex}")

        pg_conn.commit()
        return True, (
            f"Successfully migrated to Cloud PostgreSQL! "
            f"Transferred {stats['contacts']} contacts, {stats['templates']} templates, "
            f"{stats['smtp_accounts']} mailboxes, {stats['emails']} emails, and {stats['campaigns']} campaigns."
        )
    except Exception as e:
        logger.error(f"Migration error: {e}", exc_info=True)
        return False, f"Migration failed: {e}"
    finally:
        sq_conn.close()
