import time
import requests
import os
import logging
from fastapi import APIRouter, Request
from app.core.config import cfg
from app.core.database import query_db

router = APIRouter(prefix="/api/system", tags=["System Tools"])

def ping_url(url, proxies=None):
    start = time.time()
    try:
        # 使用配置中的代理发起请求
        res = requests.get(url, proxies=proxies, timeout=5)
        latency = int((time.time() - start) * 1000)
        # 只要能连通，哪怕是 401/403 也算网络通畅
        return True, latency
    except Exception:
        return False, 0

@router.get("/network_check")
async def network_check():
    proxy_url = cfg.get("proxy_url")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    
    # 1. 测速 TG API
    tg_ok, tg_ping = ping_url("https://api.telegram.org", proxies)
    
    # 2. 测速 TMDB API
    tmdb_key = cfg.get("tmdb_api_key", "")
    tmdb_url = f"https://api.themoviedb.org/3/configuration?api_key={tmdb_key}" if tmdb_key else "https://api.themoviedb.org/3/"
    tmdb_ok, tmdb_ping = ping_url(tmdb_url, proxies)
    
    # 3. 检测 Webhook 心跳 (查询最近一条入库或播放记录的时间)
    last_webhook = "暂无记录"
    try:
        rows = query_db("SELECT DateCreated FROM PlaybackActivity ORDER BY DateCreated DESC LIMIT 1")
        if rows and rows[0]['DateCreated']:
            last_webhook = rows[0]['DateCreated']
            if 'T' in last_webhook:
                last_webhook = last_webhook.replace('T', ' ')[:19]
    except Exception:
        pass

    return {
        "success": True,
        "data": {
            "tg": {"ok": tg_ok, "ping": tg_ping},
            "tmdb": {"ok": tmdb_ok, "ping": tmdb_ping},
            "webhook": {"last_active": last_webhook}
        }
    }

@router.get("/logs")
async def get_logs(lines: int = 150):
    """实时读取最后 N 行运行日志"""
    log_file = "pulse.log" # 假设你的日志文件名为 pulse.log，如果不一样请自行修改
    if not os.path.exists(log_file):
        return {
            "success": True, 
            "data": "[SYSTEM] 未在根目录找到日志文件 pulse.log。\n\n💡 提示：如果想在这里看到日志，请在启动时将控制台输出重定向到文件。\n例如使用命令: nohup python main.py > pulse.log 2>&1 &\n"
        }
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:]
            return {"success": True, "data": "".join(last_lines)}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@router.post("/debug")
async def toggle_debug(req: Request):
    """动态热切换全局日志等级"""
    data = await req.json()
    enable = data.get("enable", False)
    
    # 获取 Uvicorn 和全局 Logger
    uvicorn_logger = logging.getLogger("uvicorn")
    app_logger = logging.getLogger()
    
    level = logging.DEBUG if enable else logging.INFO
    uvicorn_logger.setLevel(level)
    app_logger.setLevel(level)
    
    if enable:
        app_logger.debug("======== DEBUG MODE ENABLED BY CONTROL CENTER ========")
        
    return {"success": True, "msg": f"Debug 模式已{'开启' if enable else '关闭'}"}