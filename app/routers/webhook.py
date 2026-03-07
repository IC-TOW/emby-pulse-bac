from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from app.services.bot_service import bot
from app.core.config import cfg
from app.core.database import query_db
import requests
import json
import logging

logger = logging.getLogger("uvicorn")
router = APIRouter()

def intercept_illegal_client(data: dict):
    """
    🔥 城门级主动防御：毫秒级拦截并秒踢黑名单客户端
    """
    session = data.get("Session", {})
    device_id = session.get("DeviceId") or data.get("DeviceId")
    client = session.get("Client") or data.get("Client") or data.get("AppName")
    session_id = session.get("Id")
    
    # 只有存在设备指纹的“客户端请求”才进行拦截匹配
    if not client or not device_id:
        return False
        
    client_lower = client.lower()
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    
    try:
        blacklist_rows = query_db("SELECT app_name FROM client_blacklist")
        if not blacklist_rows: return False
            
        blacklist = [r['app_name'].lower() for r in blacklist_rows]
        if client_lower in blacklist:
            if session_id:
                msg_cmd = {
                    "Name": "DisplayMessage",
                    "Arguments": {
                        "Header": "🚫 违规客户端拦截",
                        "Text": f"检测到违规客户端 ({client})，该设备已被踢出！",
                        "TimeoutMs": "10000"
                    }
                }
                try: requests.post(f"{host}/emby/Sessions/{session_id}/Command?api_key={key}", json=msg_cmd, timeout=2)
                except: pass
                try: requests.post(f"{host}/emby/Sessions/{session_id}/Playing/Stop?api_key={key}", timeout=2)
                except: pass
            
            try: requests.delete(f"{host}/emby/Devices?Id={device_id}&api_key={key}", timeout=3)
            except: pass
            
            logger.warning(f"💥 [主动防御] 已秒踢违规客户端: {client}")
            return True
    except: pass
    return False

def clear_gap_record_async(item: dict):
    """
    🧹 缺集补全“清道夫”任务：自动抹除已入库的集数
    """
    try:
        if item.get("Type") != "Episode": return
        
        series_id = str(item.get("SeriesId"))
        season = int(item.get("ParentIndexNumber", -1))
        episode = int(item.get("IndexNumber", -1))
        
        if season == -1 or episode == -1: return

        # 1. 物理删除数据库记录
        query_db("DELETE FROM gap_records WHERE series_id=? AND season_number=? AND episode_number=?", (series_id, season, episode))
        
        # 2. 实时刷新内存快照 (跨模块导入)
        try:
            from app.routers.gaps import state_lock, scan_state
            with state_lock:
                if scan_state.get("results"):
                    for s in scan_state["results"]:
                        if str(s.get("series_id")) == series_id:
                            # 剔除内存里的这一集
                            s["gaps"] = [ep for ep in s.get("gaps", []) if not (int(ep.get("season")) == season and int(ep.get("episode")) == episode)]
                    
                    # 过滤掉已经没缺集的剧集外壳
                    scan_state["results"] = [s for s in scan_state["results"] if len(s.get("gaps", [])) > 0]
                    
                    # 3. 同步持久化快照，防止重启复活
                    query_db("INSERT OR REPLACE INTO gap_scan_cache (id, result_json, updated_at) VALUES (1, ?, datetime('now', 'localtime'))", (json.dumps(scan_state["results"]),))
            
            logger.info(f"🎉 [缺集联动] 检测到 S{season}E{episode} 入库，已自动完成抹除。")
        except: pass
    except Exception as e:
        logger.error(f"清道夫任务执行失败: {e}")

@router.post("/api/v1/webhook")
async def emby_webhook(request: Request, background_tasks: BackgroundTasks):
    query_token = request.query_params.get("token")
    if query_token != cfg.get("webhook_token"):
        raise HTTPException(status_code=403, detail="Invalid Token")

    try:
        data = None
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            data = await request.json()
        elif "form" in content_type:
            form = await request.form()
            raw_data = form.get("data")
            if raw_data: data = json.loads(raw_data)

        if not data: return {"status": "error", "message": "Empty"}

        # 🔥 防御引擎 (仅针对播放，不影响系统入库通知)
        if intercept_illegal_client(data):
            return {"status": "success", "message": "Blocked"}

        # 🔥 强化事件识别：改用模糊包含匹配，防止不同版本 Emby 命名差异
        event = data.get("Event", "").lower().strip()
        if event: logger.info(f"🔔 Webhook Event: {event}")

        # 1. 媒体入库逻辑
        if "item.added" in event or "library.new" in event:
            item = data.get("Item", {})
            if item.get("Id"):
                # 下发推送任务
                bot.add_library_task(item)
                
                # 集数联动：日历标记 + 缺集抹除
                if item.get("Type") == "Episode":
                    from app.services.calendar_service import calendar_service
                    calendar_service.mark_episode_ready(item.get("SeriesId"), item.get("ParentIndexNumber"), item.get("IndexNumber"))
                    # 🔥 发动清道夫后台任务
                    background_tasks.add_task(clear_gap_record_async, item)

        # 2. 播放状态逻辑
        elif "playback.start" in event:
            background_tasks.add_task(bot.push_playback_event, data, "start")
        elif "playback.stop" in event:
            background_tasks.add_task(bot.push_playback_event, data, "stop")

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Webhook 通道故障: {e}")
        return {"status": "error", "message": str(e)}