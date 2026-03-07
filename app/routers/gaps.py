from fastapi import APIRouter, BackgroundTasks
import requests
import threading
import concurrent.futures
from datetime import datetime
from pydantic import BaseModel
import time

from app.core.config import cfg
from app.core.database import query_db
# 🔥 导入 Emby 路由生成器
from app.routers.search import get_emby_sys_info, is_new_emby_router

router = APIRouter(prefix="/api/gaps", tags=["gaps"])

# ----------------- 异步状态机 (全局变量) -----------------
scan_state = {
    "is_scanning": False,
    "progress": 0,
    "total": 0,
    "current_item": "系统准备中...",
    "results": [],
    "error": None
}
state_lock = threading.Lock()

def update_progress(item_name=None):
    with state_lock:
        scan_state["progress"] += 1
        if item_name:
            display_name = item_name[:20] + "..." if len(item_name) > 20 else item_name
            scan_state["current_item"] = f"比对基因: {display_name}"

def _get_proxies():
    proxy = cfg.get("proxy_url")
    return {"http": proxy, "https": proxy} if proxy else None

def get_admin_user_id():
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    if not host or not key: return None
    try:
        res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5)
        if res.status_code == 200:
            users = res.json()
            for u in users:
                if u.get("Policy", {}).get("IsAdministrator"): return u['Id']
            if users: return users[0]['Id']
    except: pass
    return None

# ----------------- 线程池打工人任务 -----------------
def process_single_series(series, lock_map, host, key, tmdb_key, proxies, today, global_inventory):
    series_id = series.get("Id")
    series_name = series.get("Name", "未知剧集")
    tmdb_id = series.get("ProviderIds", {}).get("Tmdb")

    if not tmdb_id:
        update_progress(series_name)
        return None

    # 🔥 检查是否被“全剧忽略”
    if lock_map.get(f"{series_id}_-1_-1", 0) == 1:
        update_progress(series_name)
        return None

    local_inventory = global_inventory.get(series_id, {})

    try:
        tmdb_series_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=zh-CN&api_key={tmdb_key}"
        tmdb_series_data = requests.get(tmdb_series_url, proxies=proxies, timeout=10).json()
        tmdb_seasons = tmdb_series_data.get("seasons", [])
        tmdb_status = tmdb_series_data.get("status", "") 
    except: 
        update_progress(series_name)
        return None

    series_gaps = []
    
    for season in tmdb_seasons:
        s_num = season.get("season_number")
        if s_num == 0 or s_num is None: continue
        
        tmdb_ep_count = season.get("episode_count", 0)
        if tmdb_ep_count == 0: continue
        
        local_season_inventory = local_inventory.get(s_num, set())
        if len(local_season_inventory) >= tmdb_ep_count: continue
        
        try:
            tmdb_season_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{s_num}?language=zh-CN&api_key={tmdb_key}"
            tmdb_season_data = requests.get(tmdb_season_url, proxies=proxies, timeout=10).json()
            tmdb_episodes = tmdb_season_data.get("episodes", [])
        except: continue
        
        for tmdb_ep in tmdb_episodes:
            e_num = tmdb_ep.get("episode_number")
            air_date = tmdb_ep.get("air_date")
            if not air_date or air_date > today: continue
            
            if e_num not in local_season_inventory:
                lock_key = f"{series_id}_{s_num}_{e_num}"
                status = lock_map.get(lock_key, 0)
                if status == 1: continue 
                    
                series_gaps.append({
                    "season": s_num,
                    "episode": e_num,
                    "title": tmdb_ep.get("name", f"第 {e_num} 集"),
                    "status": status 
                })
    
    update_progress(series_name) 
    
    if series_gaps:
        # 🔥 生成 Emby 跳转链接和代理海报
        sys_info = get_emby_sys_info(host, key)
        use_new_route = is_new_emby_router(sys_info)
        public_host = cfg.get("emby_public_url") or cfg.get("emby_external_url") or cfg.get("emby_public_host") or host
        public_host = public_host.rstrip('/')

        emby_url = f"{public_host}/web/index.html#!/item?id={series_id}" if use_new_route else f"{public_host}/web/index.html#!/item/details.html?id={series_id}"
        poster_url = f"/api/library/image/{series_id}?type=Primary&width=400"

        return {
            "series_id": series_id,
            "series_name": series_name,
            "tmdb_id": tmdb_id,
            "poster": poster_url,
            "emby_url": emby_url,
            "gaps": series_gaps
        }
    else:
        if tmdb_status in ["Ended", "Canceled"]:
            try:
                query_db("INSERT OR IGNORE INTO gap_perfect_series (series_id, tmdb_id, series_name) VALUES (?, ?, ?)", (series_id, tmdb_id, series_name))
            except: pass
        return None

# ----------------- 后台主控引擎 -----------------
def run_scan_task():
    try:
        host = cfg.get("emby_host")
        key = cfg.get("emby_api_key")
        tmdb_key = cfg.get("tmdb_api_key")
        admin_id = get_admin_user_id()
        today = datetime.now().strftime("%Y-%m-%d")
        proxies = _get_proxies()

        query_db('''
            CREATE TABLE IF NOT EXISTS gap_perfect_series (
                series_id TEXT PRIMARY KEY, tmdb_id TEXT, series_name TEXT, marked_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        records = query_db("SELECT series_id, season_number, episode_number, status FROM gap_records")
        lock_map = {f"{r['series_id']}_{r['season_number']}_{r['episode_number']}": r['status'] for r in records} if records else {}
                
        perfect_records = query_db("SELECT series_id FROM gap_perfect_series")
        perfect_set = set([r['series_id'] for r in perfect_records]) if perfect_records else set()

        series_url = f"{host}/emby/Users/{admin_id}/Items?IncludeItemTypes=Series&Recursive=true&Fields=ProviderIds&api_key={key}"
        all_series = requests.get(series_url, timeout=15).json().get("Items", [])
        pending_series = [s for s in all_series if s.get("Id") not in perfect_set]

        with state_lock:
            scan_state["total"] = len(pending_series)
            scan_state["current_item"] = "正在汇聚全库单集到内存..."

        if len(pending_series) == 0:
            with state_lock: scan_state["results"] = []
            return

        all_eps_url = f"{host}/emby/Users/{admin_id}/Items?IncludeItemTypes=Episode&Recursive=true&Fields=IndexNumberEnd&api_key={key}"
        all_eps_data = requests.get(all_eps_url, timeout=45).json().get("Items", [])
        
        global_inventory = {}
        for ep in all_eps_data:
            ser_id = ep.get("SeriesId")
            s_num = ep.get("ParentIndexNumber")
            e_num = ep.get("IndexNumber")
            e_end = ep.get("IndexNumberEnd")
            
            if not ser_id or s_num is None or e_num is None: continue
            if ser_id not in global_inventory: global_inventory[ser_id] = {}
            if s_num not in global_inventory[ser_id]: global_inventory[ser_id][s_num] = set()
            
            end_idx = e_end if e_end else e_num
            for i in range(e_num, end_idx + 1): global_inventory[ser_id][s_num].add(i)

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for series in pending_series:
                futures.append(executor.submit(process_single_series, series, lock_map, host, key, tmdb_key, proxies, today, global_inventory))
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res: results.append(res)

        with state_lock: scan_state["results"] = results
    except Exception as e:
        with state_lock: scan_state["error"] = str(e)
    finally:
        with state_lock:
            scan_state["is_scanning"] = False
            scan_state["current_item"] = "扫描完成！"

# ----------------- 自动巡检守护线程 -----------------
def daily_scan_scheduler():
    while True:
        try:
            now = datetime.now()
            if now.hour == 3 and now.minute == 0:
                res = query_db("SELECT status FROM gap_records WHERE series_id='SYSTEM' AND season_number=-99")
                if res and res[0]['status'] == 1:
                    if not scan_state["is_scanning"]:
                        run_scan_task()
            time.sleep(60)
        except:
            time.sleep(60)

threading.Thread(target=daily_scan_scheduler, daemon=True).start()

# ----------------- API 接口 -----------------
@router.post("/scan/start")
def start_scan(bg_tasks: BackgroundTasks):
    with state_lock:
        if scan_state["is_scanning"]: return {"status": "error", "message": "雷达已在扫描中"}
        scan_state["is_scanning"] = True; scan_state["progress"] = 0; scan_state["total"] = 0
        scan_state["current_item"] = "正在校准雷达阵列..."; scan_state["results"] = []; scan_state["error"] = None
    bg_tasks.add_task(run_scan_task)
    return {"status": "success"}

@router.get("/scan/progress")
def get_progress():
    with state_lock: return {"status": "success", "data": scan_state}

@router.post("/scan/auto_toggle")
def toggle_auto_scan(payload: dict):
    enabled = 1 if payload.get("enabled") else 0
    query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES ('SYSTEM', 'AUTO_SCAN', -99, -99, ?) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = ?", (enabled, enabled))
    return {"status": "success"}

@router.get("/scan/auto_status")
def get_auto_status():
    try:
        res = query_db("SELECT status FROM gap_records WHERE series_id='SYSTEM' AND season_number=-99")
        return {"status": "success", "enabled": bool(res[0]['status']) if res else False}
    except: return {"status": "success", "enabled": False}

@router.post("/ignore")
def ignore_gap(payload: dict):
    try:
        query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, ?, ?, 1) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 1", (payload.get("series_id"), payload.get("series_name", ""), int(payload.get("season_number", 0)), int(payload.get("episode_number", 0))))
        return {"status": "success"}
    except Exception as e: return {"status": "error"}

@router.post("/ignore/series")
def ignore_entire_series(payload: dict):
    try:
        query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, -1, -1, 1) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 1", (payload.get("series_id"), payload.get("series_name", "")))
        return {"status": "success"}
    except Exception as e: return {"status": "error"}

class GapSearchReq(BaseModel): series_id: str; series_name: str; season: int; episode: int
class GapDownloadReq(BaseModel): series_id: str; series_name: str; season: int; episode: int; torrent_info: dict

@router.post("/search_mp")
def search_mp_for_gap(req: GapSearchReq):
    host = cfg.get("emby_host"); key = cfg.get("emby_api_key"); mp_url = cfg.get("moviepilot_url"); mp_token = cfg.get("moviepilot_token")
    if not mp_url or not mp_token: return {"status": "error", "message": "未配置 MP"}
    admin_id = get_admin_user_id(); genes = []
    if admin_id:
        try:
            items = requests.get(f"{host}/emby/Users/{admin_id}/Items?ParentId={req.series_id}&IncludeItemTypes=Episode&Recursive=true&Limit=1&Fields=MediaSources&api_key={key}", timeout=5).json().get("Items", [])
            if items and items[0].get("MediaSources"):
                video = next((s for s in items[0]["MediaSources"][0].get("MediaStreams", []) if s.get("Type") == "Video"), None)
                if video:
                    w = video.get("Width", 0)
                    if w >= 3800: genes.append("4K")
                    elif w >= 1900: genes.append("1080P")
                    d_title = video.get("DisplayTitle", "").upper()
                    if "HDR" in video.get("VideoRange", "") or "HDR" in d_title: genes.append("HDR")
                    if "DOVI" in d_title or "DOLBY VISION" in d_title: genes.append("DoVi")
        except: pass
    if not genes: genes = ["无特殊基因"]
    
    keyword = f"{req.series_name} S{str(req.season).zfill(2)}E{str(req.episode).zfill(2)}"
    clean_token = mp_token.strip().strip("'\"")
    headers = {"X-API-KEY": clean_token, "Authorization": f"Bearer {clean_token}"}
    
    try:
        # 🔥 修复 403 报错：使用 params 传递 keyword 确保 URL 空格被正确转义
        mp_search_url = f"{mp_url.rstrip('/')}/api/v1/search/title"
        mp_res = requests.get(mp_search_url, params={"keyword": keyword}, headers=headers, timeout=20)
        
        # 兼容老版本 MP API
        if mp_res.status_code == 404:
            mp_search_url = f"{mp_url.rstrip('/')}/api/v1/search"
            mp_res = requests.get(mp_search_url, params={"keyword": keyword}, headers=headers, timeout=20)
            
        if mp_res.status_code != 200:
            return {"status": "error", "message": f"MP搜索失败 (HTTP {mp_res.status_code})"}
            
        results = mp_res.json()
        for r in results:
            score = 0; combined_text = r.get("title", "").upper() + " " + r.get("description", "").upper()
            if "4K" in genes: score += 50 if ("2160P" in combined_text or "4K" in combined_text) else -20
            if "1080P" in genes and "1080P" in combined_text: score += 50
            if "DoVi" in genes and ("DOVI" in combined_text or "VISION" in combined_text): score += 30
            if "HDR" in genes and "HDR" in combined_text: score += 20
            if "WEB" in combined_text: score += 10
            r["match_score"] = score
            tags = []
            if "2160P" in combined_text or "4K" in combined_text: tags.append("4K")
            elif "1080P" in combined_text: tags.append("1080P")
            if "DOVI" in combined_text or "VISION" in combined_text: tags.append("DoVi")
            elif "HDR" in combined_text: tags.append("HDR")
            if "WEB" in combined_text: tags.append("WEB-DL")
            r["extracted_tags"] = tags
        results.sort(key=lambda x: x["match_score"], reverse=True)
        return {"status": "success", "data": {"genes": genes, "results": results[:10]}}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/download")
def download_gap_item(req: GapDownloadReq):
    mp_url = cfg.get("moviepilot_url"); mp_token = cfg.get("moviepilot_token")
    clean_token = mp_token.strip().strip("'\""); headers = {"X-API-KEY": clean_token, "Authorization": f"Bearer {clean_token}"}
    try:
        res = requests.post(f"{mp_url.rstrip('/')}/api/v1/download/", headers=headers, json=req.torrent_info, timeout=10)
        if res.status_code == 200:
            query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, ?, ?, 2) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 2", (req.series_id, req.series_name, req.season, req.episode))
            return {"status": "success", "message": "已派单给 MP"}
        return {"status": "error", "message": "下发失败"}
    except Exception as e: return {"status": "error", "message": str(e)}