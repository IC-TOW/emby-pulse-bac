from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
import requests
import sqlite3
from app.core.config import cfg
from app.core.database import DB_PATH
from app.schemas.models import MediaRequestSubmitModel
from app.services.bot_service import bot

router = APIRouter()

def execute_sql(query, params=()):
    """用来执行插入/更新的本地数据库安全方法"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(query, params)
        conn.commit()
        return True, ""
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

class RequestLoginModel(BaseModel):
    username: str
    password: str

# ================= 1. Emby 专属登录鉴权 =================
@router.post("/api/requests/auth")
def request_system_login(data: RequestLoginModel, request: Request):
    host = cfg.get("emby_host")
    if not host:
        return {"status": "error", "message": "系统未配置 Emby 地址"}
        
    # 构造 Emby 要求的标准鉴权请求头
    headers = {
        "X-Emby-Authorization": 'MediaBrowser Client="EmbyPulse", Device="Web", DeviceId="PulseReqSys", Version="1.0"'
    }
    try:
        res = requests.post(
            f"{host}/emby/Users/AuthenticateByName",
            json={"Username": data.username, "Pw": data.password},
            headers=headers,
            timeout=8
        )
        if res.status_code == 200:
            user_info = res.json().get("User", {})
            # 🔥 验证成功！给前端发一个专属的 session 票据
            request.session["req_user"] = {"Id": user_info.get("Id"), "Name": user_info.get("Name")}
            return {"status": "success", "message": "登录成功"}
        else:
            return {"status": "error", "message": "账号或密码错误"}
    except Exception as e:
        return {"status": "error", "message": f"连接 Emby 失败: {str(e)}"}

# ================= 2. TMDB 视觉搜索接口 =================
@router.get("/api/requests/search")
def search_tmdb(query: str, request: Request):
    # 拦截未登录的白嫖怪
    if not request.session.get("req_user"):
        return {"status": "error", "message": "未登录，请先验证 Emby 账号"}
        
    tmdb_key = cfg.get("tmdb_api_key") # 稍后我们需要在系统设置里加这个配置
    if not tmdb_key:
        return {"status": "error", "message": "服主暂未配置 TMDB API Key"}

    proxy = cfg.get("proxy_url")
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={tmdb_key}&language=zh-CN&query={query}&page=1"
        res = requests.get(url, proxies=proxies, timeout=10)
        if res.status_code == 200:
            data = res.json()
            results = []
            for item in data.get("results", []):
                # 我们只要电影和剧集，不要演员等其他乱七八糟的结果
                if item.get("media_type") not in ["movie", "tv"]:
                    continue
                title = item.get("title") or item.get("name")
                year_str = item.get("release_date") or item.get("first_air_date") or ""
                year = year_str[:4] if year_str else "未知"
                poster = f"https://image.tmdb.org/t/p/w500{item.get('poster_path')}" if item.get("poster_path") else ""
                
                results.append({
                    "tmdb_id": item.get("id"),
                    "media_type": item.get("media_type"),
                    "title": title,
                    "year": year,
                    "poster_path": poster,
                    "overview": item.get("overview", "")
                })
            return {"status": "success", "data": results}
        return {"status": "error", "message": "TMDB API 响应异常"}
    except Exception as e:
        return {"status": "error", "message": f"网络代理或请求错误: {str(e)}"}

# ================= 3. 提交求片与防撞车机制 =================
@router.post("/api/requests/submit")
def submit_media_request(data: MediaRequestSubmitModel, request: Request):
    user = request.session.get("req_user")
    if not user:
        return {"status": "error", "message": "登录已过期，请刷新页面重新登录"}

    user_id = user.get("Id")
    username = user.get("Name")

    # 1. 查询系统里是不是已经有人求过这部片子了
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT status FROM media_requests WHERE tmdb_id = ?", (data.tmdb_id,))
    existing = c.fetchone()
    
    if not existing:
        # 如果是全站第一次求这部片，插入主表
        execute_sql(
            "INSERT INTO media_requests (tmdb_id, media_type, title, year, poster_path, status) VALUES (?, ?, ?, ?, ?, 0)",
            (data.tmdb_id, data.media_type, data.title, data.year, data.poster_path)
        )
    else:
        # 如果已经有了，判断一下状态
        if existing[0] == 2:
            conn.close()
            return {"status": "error", "message": "这部片子已经入库啦！快去 Emby 里找找看吧！"}

    # 2. 绑定这个用户和这部片子 (+1 机制)
    success, err_msg = execute_sql(
        "INSERT INTO request_users (tmdb_id, user_id, username) VALUES (?, ?, ?)",
        (data.tmdb_id, user_id, username)
    )
    conn.close()

    if not success:
        if "UNIQUE constraint failed" in err_msg:
            return {"status": "error", "message": "你已经提交过这部片子了，不用重复点 +1 啦，耐心等待服主处理吧！"}
        return {"status": "error", "message": f"数据库写入失败: {err_msg}"}

    # 3. 唤醒你的双端机器人，给服主（你）发送审批通知！
    type_cn = "🎬 电影" if data.media_type == "movie" else "📺 剧集"
    bot_msg = (
        f"🔔 <b>新求片订单提醒</b>\n\n"
        f"👤 <b>求片人</b>：{username}\n"
        f"📌 <b>片名</b>：{data.title} ({data.year})\n"
        f"🏷️ <b>类型</b>：{type_cn}\n\n"
        f"👉 请前往 EmbyPulse 后台【🍿 资源求片】进行审批和下载。"
    )
    # 调用 bot_service 里的发图文消息
    bot.send_photo("sys_notify", data.poster_path if data.poster_path else REPORT_COVER_URL, bot_msg, platform="all")

    return {"status": "success", "message": "心愿提交成功！已通知服主处理。"}

# ================= 4. 获取用户自己的求片列表 =================
@router.get("/api/requests/my")
def get_my_requests(request: Request):
    user = request.session.get("req_user")
    if not user:
        return {"status": "error", "message": "未登录"}
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 连表查询：查出该用户求过的片子以及当前的最新状态
    query = """
        SELECT m.tmdb_id, m.title, m.year, m.poster_path, m.status, r.requested_at
        FROM request_users r
        JOIN media_requests m ON r.tmdb_id = m.tmdb_id
        WHERE r.user_id = ?
        ORDER BY r.requested_at DESC
    """
    c.execute(query, (user.get("Id"),))
    rows = c.fetchall()
    conn.close()
    
    result = []
    for r in rows:
        result.append({
            "tmdb_id": r[0], "title": r[1], "year": r[2], 
            "poster_path": r[3], "status": r[4], "requested_at": r[5]
        })
    return {"status": "success", "data": result}

from app.schemas.models import MediaRequestActionModel

# ================= 5. [管理端] 获取所有求片列表 (带人数聚合) =================
@router.get("/api/manage/requests")
def get_all_requests(request: Request):
    if not request.session.get("user"): 
        return {"status": "error", "message": "未登录管理后台"}
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 核心 SQL：聚合查出每部片子有几个人求，以及具体是谁求的
    query = """
        SELECT m.tmdb_id, m.media_type, m.title, m.year, m.poster_path, m.status, m.created_at,
               COUNT(r.user_id) as request_count,
               GROUP_CONCAT(r.username, ', ') as requested_by
        FROM media_requests m
        LEFT JOIN request_users r ON m.tmdb_id = r.tmdb_id
        GROUP BY m.tmdb_id
        ORDER BY m.status ASC, request_count DESC, m.created_at DESC
    """
    c.execute(query)
    rows = c.fetchall()
    conn.close()
    
    result = []
    for r in rows:
        result.append({
            "tmdb_id": r[0], "media_type": r[1], "title": r[2], "year": r[3],
            "poster_path": r[4], "status": r[5], "created_at": r[6],
            "request_count": r[7], "requested_by": r[8] or "未知"
        })
    return {"status": "success", "data": result}

# ================= 6. [管理端] 审批操作 =================
@router.post("/api/manage/requests/action")
def manage_request_action(data: MediaRequestActionModel, request: Request):
    if not request.session.get("user"): 
        return {"status": "error", "message": "权限不足"}

    new_status = 0
    if data.action == "approve":
        new_status = 1  # 状态 1: 下载中
        # ⚠️ 这里预留：未来在此处调用 MoviePilot 的 API 触发自动下载
    elif data.action == "reject":
        new_status = 3  # 状态 3: 已拒绝
    elif data.action == "finish":
        new_status = 2  # 状态 2: 已入库 (可手动标记)
    elif data.action == "delete":
        # 如果是彻底删除，需要把主表和用户关联表都删掉
        execute_sql("DELETE FROM media_requests WHERE tmdb_id = ?", (data.tmdb_id,))
        execute_sql("DELETE FROM request_users WHERE tmdb_id = ?", (data.tmdb_id,))
        return {"status": "success", "message": "记录已彻底删除"}
    else:
        return {"status": "error", "message": "未知操作"}

    success, err_msg = execute_sql("UPDATE media_requests SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE tmdb_id = ?", (new_status, data.tmdb_id))
    
    if success:
        action_name = {"approve": "批准并标记为下载中", "reject": "已残忍拒绝", "finish": "已标记入库"}.get(data.action, "操作成功")
        return {"status": "success", "message": action_name}
    return {"status": "error", "message": err_msg}