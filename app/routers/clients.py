import sqlite3
import requests
import datetime
from fastapi import APIRouter, Request
from pydantic import BaseModel
from app.core.config import cfg
from app.core.database import DB_PATH, query_db

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

# 🔥 修复：UTC 时间转东八区本地时间
def parse_emby_utc(date_str):
    if not date_str: return ""
    try:
        # 截断毫秒和Z (如: 2024-03-04T05:00:00.0000000Z -> 2024-03-04T05:00:00)
        clean_str = date_str.split('.')[0].replace('Z', '')
        dt = datetime.datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S")
        # 自动+8小时转换为北京/台北时间
        local_dt = dt + datetime.timedelta(hours=8)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        # 异常备用方案
        return date_str.replace("T", " ").split(".")[0]

@router.get("/api/clients/data")
async def get_clients_data(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "鉴权失败"}
    
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    if not host or not key:
        return {"status": "error", "message": "Emby 配置未完成，请检查 config.yaml"}

    try:
        # 获取所有设备
        res = requests.get(f"{host}/emby/Devices?api_key={key}", timeout=5)
        devices = res.json().get("Items", [])
        
        # 获取活跃会话，提取当前播放的设备指纹
        sess_res = requests.get(f"{host}/emby/Sessions?api_key={key}", timeout=5)
        sessions = sess_res.json()
        active_sigs = [{
            "device_id": s.get("DeviceId", ""), 
            "client": s.get("Client", ""), 
            "user_name": s.get("UserName", "")
        } for s in sessions if s.get("NowPlayingItem")]
    except Exception as e:
        return {"status": "error", "message": f"连接 Emby 失败: {str(e)}"}

    app_counts = {}
    top_devices = {}
    
    # 获取图表数据（已更新为 TOP 10）
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
    for d in devices:
        app_name = d.get("AppName") or "未知客户端"
        is_blocked = app_name.lower() in blacklist
        date_str = d.get("DateLastActivity", "")
        last_active = parse_emby_utc(date_str) if date_str else "从未连接"
        last_user = d.get("LastUserName") or "未知用户"
        
        # 🔥 修复：在线状态判定（支持模糊匹配跨越设备 ID 漂移）
        d_id = d.get("Id", "")
        is_active = False
        for sig in active_sigs:
            # 1. 强匹配: DeviceId 直接匹配
            if d_id and sig["device_id"] and d_id == sig["device_id"]:
                is_active = True
                break
            # 2. 弱匹配: 专治 Infuse 等第三方工具 ID 漂移 (客户端名称 + 用户名 完全一致则认为在线)
            if app_name and sig["client"] and last_user and sig["user_name"]:
                if app_name.lower() == sig["client"].lower() and last_user.lower() == sig["user_name"].lower():
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

    # 按时间倒序
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
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    
    blacklist_rows = query_db("SELECT app_name FROM client_blacklist")
    if not blacklist_rows: 
        return {"status": "success", "message": "当前黑名单为空，无设备被阻断"}
    blacklist = [r['app_name'].lower() for r in blacklist_rows]
    
    blocked_count = 0
    try:
        res = requests.get(f"{host}/emby/Devices?api_key={key}", timeout=5)
        devices = res.json().get("Items", [])
        
        for d in devices:
            app_name = (d.get("AppName") or "").lower()
            if app_name in blacklist:
                requests.delete(f"{host}/emby/Devices?Id={d['Id']}&api_key={key}", timeout=2)
                blocked_count += 1
                
        return {"status": "success", "message": f"扫描完成！成功强制注销了 {blocked_count} 个违规设备。"}
    except Exception as e:
        return {"status": "error", "message": f"执行阻断失败: {str(e)}"}