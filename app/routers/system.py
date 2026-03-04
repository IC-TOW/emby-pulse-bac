from fastapi import APIRouter, Request
from app.schemas.models import SettingsModel
from app.core.config import cfg, save_config
import requests

router = APIRouter()

@router.get("/api/settings")
def api_get_settings(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    return {
        "status": "success",
        "data": {
            "emby_host": cfg.get("emby_host"),
            "emby_api_key": cfg.get("emby_api_key"),
            "tmdb_api_key": cfg.get("tmdb_api_key"),
            "proxy_url": cfg.get("proxy_url"),
            "webhook_token": cfg.get("webhook_token", "embypulse"),
            "hidden_users": cfg.get("hidden_users") or [],
            "emby_public_url": cfg.get("emby_public_url", ""),
            "welcome_message": cfg.get("welcome_message", ""),
            "client_download_url": cfg.get("client_download_url", ""),
            # 🔥 新增：把 MP 和面板地址吐给前端显示
            "moviepilot_url": cfg.get("moviepilot_url", ""),
            "moviepilot_token": cfg.get("moviepilot_token", ""),
            "pulse_url": cfg.get("pulse_url", "")
        }
    }

@router.post("/api/settings")
def api_update_settings(data: SettingsModel, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    
    # 验证 Emby 连接
    try:
        res = requests.get(f"{data.emby_host}/emby/System/Info?api_key={data.emby_api_key}", timeout=5)
        if res.status_code != 200:
            return {"status": "error", "message": "无法连接 Emby，请检查地址或 API Key"}
    except:
        return {"status": "error", "message": "Emby 地址无法访问"}

    # 更新配置
    cfg["emby_host"] = data.emby_host
    cfg["emby_api_key"] = data.emby_api_key
    cfg["tmdb_api_key"] = data.tmdb_api_key
    cfg["proxy_url"] = data.proxy_url
    cfg["webhook_token"] = data.webhook_token
    cfg["hidden_users"] = data.hidden_users
    cfg["emby_public_url"] = data.emby_public_url
    cfg["welcome_message"] = data.welcome_message
    cfg["client_download_url"] = data.client_download_url
    
    # 🔥 新增：把接收到的新字段手动存入 cfg
    cfg["moviepilot_url"] = data.moviepilot_url
    cfg["moviepilot_token"] = data.moviepilot_token
    cfg["pulse_url"] = data.pulse_url
    
    save_config()
    
    return {"status": "success", "message": "配置已保存"}

@router.post("/api/settings/test_tmdb")
def api_test_tmdb(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    # 简化逻辑，直接测试 cfg 里的
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url")
    
    if not tmdb_key: return {"status": "error", "message": "未配置 TMDB API Key"}
    
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        url = f"https://api.themoviedb.org/3/authentication/token/new?api_key={tmdb_key}"
        res = requests.get(url, proxies=proxies, timeout=10)
        if res.status_code == 200:
            return {"status": "success", "message": "TMDB 连接成功"}
        return {"status": "error", "message": f"连接失败: {res.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/api/settings/test_mp")
async def test_moviepilot(request: Request):
    if not request.session.get("user"): 
        return {"status": "error", "message": "权限不足"}
        
    data = await request.json()
    mp_url = data.get("mp_url", "").strip().rstrip('/')
    mp_token = data.get("mp_token", "").strip().strip("'\"")

    if not mp_url or not mp_token:
        return {"status": "error", "message": "请先填写 MoviePilot 地址和 Token"}

    try:
        # 请求 MoviePilot 的基础接口来验证 Token 有效性
        res = requests.get(
            f"{mp_url}/api/v1/site/", 
            headers={"X-API-KEY": mp_token}, 
            timeout=5
        )
        
        if res.status_code == 200:
            return {"status": "success", "message": "🎉 MoviePilot 连通测试成功！"}
        elif res.status_code in [401, 403]:
            return {"status": "error", "message": "❌ Token 认证失败，请检查 API Key 是否正确"}
        else:
            return {"status": "success", "message": f"⚠️ 服务器已连通 (状态码: {res.status_code})，但建议检查地址是否指向 API 根路径"}
            
    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": f"❌ 无法连接到 MoviePilot，请检查地址或服务器网络。"}