import sqlite3
import os
from app.core.config import cfg, DB_PATH

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # [原有代码] 播放记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS PlaybackActivity (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            UserId TEXT,
            UserName TEXT,
            ItemId TEXT,
            ItemName TEXT,
            PlayDuration INTEGER,
            DateCreated DATETIME DEFAULT CURRENT_TIMESTAMP,
            Client TEXT,
            DeviceName TEXT
        )
    ''')
    
    # [原有代码] 用户扩展信息表 (到期时间等)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users_meta (
            user_id TEXT PRIMARY KEY,
            expire_date TEXT
        )
    ''')
    
    # [原有代码] 邀请码表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invitations (
            code TEXT PRIMARY KEY,
            days INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_at DATETIME,
            used_by TEXT,
            status INTEGER DEFAULT 0 
        )
    ''')

    # 🔥 [新增代码] 求片资源主表
    # status说明: 0=待审核, 1=正在寻找/下载中, 2=已入库, 3=已拒绝
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS media_requests (
            tmdb_id INTEGER PRIMARY KEY,
            media_type TEXT,
            title TEXT,
            year TEXT,
            poster_path TEXT,
            status INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 🔥 [新增代码] 求片用户关联表 (+1 机制)
    # 联合唯一索引保证一个用户对同一部片子只能求一次
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS request_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER,
            user_id TEXT,
            username TEXT,
            requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tmdb_id, user_id)
        )
    ''')

    conn.commit()
    conn.close()
        print("✅ Database initialized (Plugin Read-Only Mode).")
    except Exception as e: 
        print(f"❌ DB Init Error: {e}")

def query_db(query, args=(), one=False):
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, args)
        if query.strip().upper().startswith("SELECT"):
            rv = cur.fetchall()
            conn.close()
            return (rv[0] if rv else None) if one else rv
        else:
            conn.commit()
            conn.close()
            return True
    except Exception as e: 
        print(f"SQL Error: {e}")
        return None

def get_base_filter(user_id_filter):
    where = "WHERE 1=1"
    params = []
    
    # 注意：插件数据库列名通常是 UserId (PascalCase)
    # 如果您的插件版本不同，可能需要改为 user_id，但标准版是 UserId
    if user_id_filter and user_id_filter != 'all':
        where += " AND UserId = ?"
        params.append(user_id_filter)
    
    # 隐藏用户过滤
    hidden = cfg.get("hidden_users")
    if (not user_id_filter or user_id_filter == 'all') and hidden and len(hidden) > 0:
        placeholders = ','.join(['?'] * len(hidden))
        where += f" AND UserId NOT IN ({placeholders})"
        params.extend(hidden)
        
    return where, params