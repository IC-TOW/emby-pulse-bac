from fastapi import APIRouter, Depends, HTTPException
import requests
from datetime import datetime
from app.core.config import cfg
from app.core.database import query_db
from pydantic import BaseModel
import re

router = APIRouter(prefix="/api/gaps", tags=["gaps"])

# 辅助函数：获取管理员用户ID，以便拥有最高权限扫库
def get_admin_user_id():
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    if not host or not key: return None
    try:
        res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5)
        if res.status_code == 200:
            users = res.json()
            for u in users:
                if u.get("Policy", {}).get("IsAdministrator"):
                    return u['Id']
            if users: return users[0]['Id']
    except: pass
    return None

@router.get("/scan")
def scan_library_gaps():
    """
    【深空雷达引擎】
    扫描 Emby 媒体库中的剧集，对比 TMDB 获取缺集情况
    - 免疫 Season 0 (花絮)
    - 免疫 未来未开播的集数 (air_date 对比)
    - 免疫 已标记为“处理中/已屏蔽”的集数
    - 精准识别 多集合一 (IndexNumberEnd)
    """
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    tmdb_key = cfg.get("tmdb_api_key") # 确保用户在 config.yaml 填了 TMDB_API_KEY
    
    if not host or not key or not tmdb_key:
        return {"status": "error", "message": "系统未配置 Emby 或 TMDB API_KEY"}
        
    admin_id = get_admin_user_id()
    if not admin_id:
        return {"status": "error", "message": "无法获取 Emby 管理员身份"}

    # 获取所有数据库里的状态锁 (忽略/处理中)
    records = query_db("SELECT series_id, season_number, episode_number, status FROM gap_records")
    lock_map = {}
    if records:
        for r in records:
            lock_map[f"{r['series_id']}_{r['season_number']}_{r['episode_number']}"] = r['status']
            
    # 1. 抓取本地所有剧集 (Series)
    series_url = f"{host}/emby/Users/{admin_id}/Items?IncludeItemTypes=Series&Recursive=true&Fields=ProviderIds&api_key={key}"
    try:
        series_res = requests.get(series_url, timeout=15).json()
        series_list = series_res.get("Items", [])
    except Exception as e:
        return {"status": "error", "message": f"请求 Emby 剧集失败: {str(e)}"}

    gap_results = []
    today = datetime.now().strftime("%Y-%m-%d")

    for series in series_list:
        series_id = series.get("Id")
        series_name = series.get("Name")
        tmdb_id = series.get("ProviderIds", {}).get("Tmdb")
        
        if not tmdb_id: continue # 没有 TMDB ID 无法测算，跳过
        
        # 2. 拉取本地该剧集的所有实际存在的单集 (Episodes)
        episodes_url = f"{host}/emby/Users/{admin_id}/Items?ParentId={series_id}&IncludeItemTypes=Episode&Recursive=true&Fields=IndexNumberEnd&api_key={key}"
        try:
            local_eps_data = requests.get(episodes_url, timeout=10).json().get("Items", [])
        except: continue

        # 建立本地已拥有的季集映射表
        local_inventory = {} # 格式: { season_num: set([ep1, ep2, ep3]) }
        for ep in local_eps_data:
            s_num = ep.get("ParentIndexNumber") # 季号
            e_num = ep.get("IndexNumber")       # 起始集号
            e_end = ep.get("IndexNumberEnd")    # 结束集号 (处理多集合一，如E01-E02)
            
            if s_num is None or e_num is None: continue
            if s_num not in local_inventory: local_inventory[s_num] = set()
            
            # 🔥 破解“多集合一”盲区：把 E01-E02 拆解并全部视为“已拥有”
            end_idx = e_end if e_end else e_num
            for i in range(e_num, end_idx + 1):
                local_inventory[s_num].add(i)

        # 3. 穿透查询 TMDB 真实数据
        try:
            tmdb_series_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=zh-CN&api_key={tmdb_key}"
            tmdb_series_data = requests.get(tmdb_series_url, timeout=10).json()
            tmdb_seasons = tmdb_series_data.get("seasons", [])
        except: continue

        series_gaps = []
        
        for season in tmdb_seasons:
            s_num = season.get("season_number")
            # 🔥 免疫拦截 1：直接跳过第 0 季 (花絮特别篇)
            if s_num == 0 or s_num is None: continue
            
            # 如果本地连这一季的文件夹都没有，或者这一季TMDB显示尚未播出，继续深挖
            tmdb_ep_count = season.get("episode_count", 0)
            if tmdb_ep_count == 0: continue
            
            # 如果本地全有，不用再查 TMDB 单集详情了
            local_season_inventory = local_inventory.get(s_num, set())
            if len(local_season_inventory) >= tmdb_ep_count: continue
            
            # 存在缺口，向 TMDB 请求该季所有单集的详细播出日期
            try:
                tmdb_season_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{s_num}?language=zh-CN&api_key={tmdb_key}"
                tmdb_season_data = requests.get(tmdb_season_url, timeout=5).json()
                tmdb_episodes = tmdb_season_data.get("episodes", [])
            except: continue
            
            for tmdb_ep in tmdb_episodes:
                e_num = tmdb_ep.get("episode_number")
                air_date = tmdb_ep.get("air_date")
                
                # 🔥 免疫拦截 2：未来尚未播出的集数，绝不报警
                if not air_date or air_date > today: continue
                
                # 开始比对！如果本地没有，且已播出
                if e_num not in local_season_inventory:
                    # 检查是否被“屏蔽”或者“处理中”
                    lock_key = f"{series_id}_{s_num}_{e_num}"
                    status = lock_map.get(lock_key, 0)
                    
                    if status == 1:
                        # 已永久屏蔽，跳过
                        continue
                        
                    series_gaps.append({
                        "season": s_num,
                        "episode": e_num,
                        "title": tmdb_ep.get("name", f"第 {e_num} 集"),
                        "status": status # 0: 缺集(红框), 2: 处理中(蓝框)
                    })
        
        # 如果这部剧有缺口，打包放进结果雷达
        if series_gaps:
            gap_results.append({
                "series_id": series_id,
                "series_name": series_name,
                "tmdb_id": tmdb_id,
                "poster": f"{host}/emby/Items/{series_id}/Images/Primary?maxHeight=400&maxWidth=300&api_key={key}",
                "gaps": series_gaps
            })

    return {"status": "success", "data": gap_results}

@router.post("/ignore")
def ignore_gap(payload: dict):
    """
    【隐形收容所】永久屏蔽某个缺集
    接收参数: series_id, series_name, season_number, episode_number
    """
    series_id = payload.get("series_id")
    series_name = payload.get("series_name", "未知剧集")
    season = int(payload.get("season_number", 0))
    episode = int(payload.get("episode_number", 0))
    
    if not series_id:
        return {"status": "error", "message": "参数缺失"}
        
    try:
        # status = 1 代表永久忽略
        query_db("""
            INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) 
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 1
        """, (series_id, series_name, season, episode))
        return {"status": "success", "message": "✅ 已加入免检白名单，强迫症治愈！"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ----------------- 第二阶段：联动枢纽 (追加代码) -----------------

class GapSearchReq(BaseModel):
    series_id: str
    series_name: str
    season: int
    episode: int

class GapDownloadReq(BaseModel):
    series_id: str
    series_name: str
    season: int
    episode: int
    torrent_info: dict  # MP 搜索返回的种子信息对象

@router.post("/search_mp")
def search_mp_for_gap(req: GapSearchReq):
    """
    【智能配型引擎】
    1. 从本地兄弟集提取“洗版基因” (4K, 1080P, HDR, DoVi 等)
    2. 调用 MP 搜索该单集
    3. 根据基因重合度对 MP 结果进行打分排序
    """
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    mp_url = cfg.get("moviepilot_url")
    mp_token = cfg.get("moviepilot_token")
    
    if not mp_url or not mp_token:
        return {"status": "error", "message": "系统未配置 MoviePilot 连接信息"}

    admin_id = get_admin_user_id()
    
    # 1. 提取家族基因 (抽样本地1集)
    genes = []
    if admin_id:
        try:
            sample_url = f"{host}/emby/Users/{admin_id}/Items?ParentId={req.series_id}&IncludeItemTypes=Episode&Recursive=true&Limit=1&Fields=MediaSources&api_key={key}"
            sample_res = requests.get(sample_url, timeout=5).json()
            items = sample_res.get("Items", [])
            if items:
                sources = items[0].get("MediaSources", [])
                if sources:
                    video = next((s for s in sources[0].get("MediaStreams", []) if s.get("Type") == "Video"), None)
                    if video:
                        w = video.get("Width", 0)
                        if w >= 3800: genes.append("4K")
                        elif w >= 1900: genes.append("1080P")
                        
                        v_range = video.get("VideoRange", "")
                        d_title = video.get("DisplayTitle", "").upper()
                        if "HDR" in v_range or "HDR" in d_title: genes.append("HDR")
                        if "DOVI" in d_title or "DOLBY VISION" in d_title: genes.append("DoVi")
        except: pass
    
    if not genes: genes = ["未提取到特殊基因(默认)"]

    # 2. 联动 MP 搜索
    # 构建搜索词，如 "权力的游戏 S01E05"
    keyword = f"{req.series_name} S{str(req.season).zfill(2)}E{str(req.episode).zfill(2)}"
    
    headers = {
        "X-API-KEY": mp_token.strip().strip("'\""),
        "Authorization": f"Bearer {mp_token.strip().strip('\"')}"
    }
    
    try:
        # 调用 MP 的标题搜索 API
        mp_search_url = f"{mp_url.rstrip('/')}/api/v1/search/title?keyword={keyword}"
        mp_res = requests.get(mp_search_url, headers=headers, timeout=20)
        if mp_res.status_code != 200:
            return {"status": "error", "message": f"MP搜索失败 (HTTP {mp_res.status_code})"}
            
        results = mp_res.json()
        if not results:
            return {"status": "success", "data": {"genes": genes, "results": []}, "message": "MP 未搜索到该单集"}
            
        # 3. 基因配型打分
        for r in results:
            score = 0
            title = r.get("title", "").upper()
            desc = r.get("description", "").upper()
            combined_text = title + " " + desc
            
            # 分辨率匹配 (权重最高)
            if "4K" in genes:
                if "2160P" in combined_text or "4K" in combined_text: score += 50
                else: score -= 20 # 降级扣分
            if "1080P" in genes:
                if "1080P" in combined_text: score += 50
            
            # 动态范围匹配
            if "DoVi" in genes and ("DOVI" in combined_text or "VISION" in combined_text): score += 30
            if "HDR" in genes and "HDR" in combined_text: score += 20
            
            # WEB-DL 加分 (通常剧集 WEB-DL 兼容性最好)
            if "WEB" in combined_text: score += 10
            
            # 把分数写进结果里
            r["match_score"] = score
            
            # 生成高亮标签
            tags = []
            if "2160P" in combined_text or "4K" in combined_text: tags.append("4K")
            elif "1080P" in combined_text: tags.append("1080P")
            if "DOVI" in combined_text or "VISION" in combined_text: tags.append("DoVi")
            elif "HDR" in combined_text: tags.append("HDR")
            if "WEB" in combined_text: tags.append("WEB-DL")
            r["extracted_tags"] = tags
            
        # 按分数从高到低排序，过滤掉负分太离谱的，取前 10 个
        results.sort(key=lambda x: x["match_score"], reverse=True)
        top_results = results[:10]
        
        return {
            "status": "success", 
            "data": {
                "genes": genes,
                "results": top_results
            }
        }
    except Exception as e:
        return {"status": "error", "message": f"MP搜索异常: {str(e)}"}

@router.post("/download")
def download_gap_item(req: GapDownloadReq):
    """
    【一键派单并锁定】
    1. 将选中的种子推给 MP 下载
    2. 将数据库中该集状态设为 2 (蓝灯处理中)
    """
    mp_url = cfg.get("moviepilot_url")
    mp_token = cfg.get("moviepilot_token")
    if not mp_url or not mp_token:
        return {"status": "error", "message": "系统未配置 MoviePilot 连接信息"}

    headers = {
        "X-API-KEY": mp_token.strip().strip("'\""),
        "Authorization": f"Bearer {mp_token.strip().strip('\"')}"
    }

    try:
        # 直接把 MP 自己吐出来的 torrent_info 完整塞回去给下载接口
        # 兼容 MoviePilot 的下载 API 规范
        mp_dl_url = f"{mp_url.rstrip('/')}/api/v1/download/"
        res = requests.post(mp_dl_url, headers=headers, json=req.torrent_info, timeout=10)
        
        if res.status_code == 200:
            # 下载指令发送成功，锁定本地状态为 2 (处理中)
            query_db("""
                INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) 
                VALUES (?, ?, ?, ?, 2)
                ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 2
            """, (req.series_id, req.series_name, req.season, req.episode))
            
            return {"status": "success", "message": "🚀 已成功下发至 MoviePilot，状态已锁定为处理中。"}
        else:
            return {"status": "error", "message": f"下发下载失败 (HTTP {res.status_code})"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

