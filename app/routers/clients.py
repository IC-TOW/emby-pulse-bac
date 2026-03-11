import sqlite3
import requests
import datetime
from fastapi import APIRouter, Request
from pydantic import BaseModel
from app.core.config import cfg
from app.core.database import DB_PATH, query_db
# 🔥 引入核心适配器
from app.core.media_adapter import media_api

router = APIRouter()

def ensure_blacklist_schema():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS client_blacklist (
                        app_name TEXT PRIMARY KEY,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )''')
        conn.commit()
        conn.close()
    except Exception as e:
        pass

ensure_blacklist_schema()

class BlacklistModel(BaseModel):
    app_name: str

@router.get("/api/clients/blacklist")
async def get_blacklist():
    rows = query_db("SELECT * FROM client_blacklist ORDER BY created_at DESC")
    return {"status": "success", "data": [dict(r) for r in rows] if rows else []}

@router.post("/api/clients/blacklist")
async def add_blacklist(data: BlacklistModel):
    app_name = data.app_name.strip()
    if not app_name: 
        return {"status": "error", "message": "软件名不能为空"}
    try:
        query_db("INSERT INTO client_blacklist (app_name) VALUES (?)", (app_name,))
        return {"status": "success"}
    except:
        return {"status": "error", "message": f"[{app_name}] 已存在于黑名单中"}

@router.delete("/api/clients/blacklist/{app_name}")
async def delete_blacklist(app_name: str):
    query_db("DELETE FROM client_blacklist WHERE app_name = ?", (app_name,))
    return {"status": "success"}

def parse_emby_utc(date_str):
    if not date_str: return ""
    try:
        clean_str = date_str.split('.')[0].replace('Z', '')
        dt = datetime.datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S")
        local_dt = dt + datetime.timedelta(hours=8)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return date_str.replace("T", " ").split(".")[0]

@router.get("/api/clients/data")
async def get_clients_data(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "鉴权失败"}

    try:
        # 🚀 替换为 media_api
        res = media_api.get("/Devices", timeout=5)
        devices = res.json().get("Items", [])
        
        sess_res = media_api.get("/Sessions", timeout=5)
        sessions = sess_res.json()
        active_sigs = [{
            "device_id": s.get("DeviceId", ""), 
            "client": s.get("Client", ""), 
            "user_name": s.get("UserName", "")
        } for s in sessions if s.get("NowPlayingItem")]
    except Exception as e:
        return {"status": "error", "message": f"连接媒体服务器失败: {str(e)}"}

    app_counts = {}
    top_devices = {}
    
    try:
        pie_rows = query_db("SELECT COALESCE(ClientName, Client, '未知客户端') as c_name, COUNT(*) as cnt FROM PlaybackActivity WHERE c_name IS NOT NULL AND c_name != '' GROUP BY c_name")
        if pie_rows:
            app_counts = {r['c_name']: r['cnt'] for r in pie_rows}
            
        bar_rows = query_db("SELECT DeviceName, COUNT(*) as cnt FROM PlaybackActivity WHERE DeviceName IS NOT NULL AND DeviceName != '' GROUP BY DeviceName ORDER BY cnt DESC LIMIT 10")
        if bar_rows:
            top_devices = {r['DeviceName']: r['cnt'] for r in bar_rows}
    except: pass

    if not app_counts:
        for d in devices:
            an = d.get("AppName") or "未知客户端"
            app_counts[an] = app_counts.get(an, 0) + 1
            
    if not top_devices:
        sorted_devs = sorted(devices, key=lambda x: x.get("DateLastActivity", ""), reverse=True)[:10]
        top_devices = { (d.get("Name") or "未知设备"): 1 for d in sorted_devs}

    blacklist_rows = query_db("SELECT app_name FROM client_blacklist")
    blacklist = [r['app_name'].lower() for r in blacklist_rows] if blacklist_rows else []

    table_data = []
    now_utc = datetime.datetime.utcnow()

    for d in devices:
        app_name = d.get("AppName") or "未知客户端"
        is_blocked = app_name.lower() in blacklist
        date_str = d.get("DateLastActivity", "")
        last_active = parse_emby_utc(date_str) if date_str else "从未连接"
        last_user = d.get("LastUserName") or "未知用户"
        
        time_diff_sec = 9999999
        if date_str:
            try:
                clean_str = date_str.split('.')[0].replace('Z', '')
                dt = datetime.datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S")
                time_diff_sec = abs((now_utc - dt).total_seconds())
            except Exception:
                pass

        d_id = d.get("Id", "")
        is_active = False
        
        for sig in active_sigs:
            if d_id and sig["device_id"] and d_id == sig["device_id"]:
                is_active = True
                break
            if app_name and sig["client"] and last_user and sig["user_name"]:
                if app_name.lower() == sig["client"].lower() and last_user.lower() == sig["user_name"].lower():
                    if time_diff_sec <= 900:
                        is_active = True
                        break
        
        table_data.append({
            "id": d_id,
            "name": d.get("Name") or "未知设备",
            "app_name": app_name,
            "last_active": last_active,
            "last_user": last_user,
            "is_active": is_active,
            "is_blocked": is_blocked
        })

    table_data.sort(key=lambda x: x["last_active"], reverse=True)

    return {
        "status": "success",
        "charts": {
            "pie": {"labels": list(app_counts.keys()), "data": list(app_counts.values())},
            "bar": {"labels": list(top_devices.keys()), "data": list(top_devices.values())}
        },
        "devices": table_data
    }

@router.post("/api/clients/execute_block")
async def execute_block():
    blacklist_rows = query_db("SELECT app_name FROM client_blacklist")
    if not blacklist_rows: 
        return {"status": "success", "message": "当前黑名单为空，无设备被阻断"}
    blacklist = [r['app_name'].lower() for r in blacklist_rows]
    
    blocked_count = 0
    try:
        # 🚀 替换为 media_api
        res = media_api.get("/Devices", timeout=5)
        devices = res.json().get("Items", [])
        
        for d in devices:
            app_name = (d.get("AppName") or "").lower()
            if app_name in blacklist:
                media_api.delete("/Devices", params={"Id": d['Id']}, timeout=2)
                blocked_count += 1
                
        return {"status": "success", "message": f"扫描完成！成功强制注销了 {blocked_count} 个违规设备。"}
    except Exception as e:
        return {"status": "error", "message": f"执行阻断失败: {str(e)}"}