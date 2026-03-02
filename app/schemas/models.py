from pydantic import BaseModel
from typing import Optional, List

class LoginModel(BaseModel):
    username: str
    password: str

class SettingsModel(BaseModel):
    emby_host: str
    emby_api_key: str
    tmdb_api_key: Optional[str] = ""
    proxy_url: Optional[str] = ""
    webhook_token: Optional[str] = "embypulse"
    hidden_users: List[str] = []
    emby_public_url: Optional[str] = ""  
    welcome_message: Optional[str] = ""  
    client_download_url: Optional[str] = ""
    # 🔥 新增：MoviePilot 和面板地址设置项
    moviepilot_url: Optional[str] = ""
    moviepilot_token: Optional[str] = ""
    pulse_url: Optional[str] = ""

class BotSettingsModel(BaseModel):
    tg_bot_token: str
    tg_chat_id: str
    enable_bot: bool
    enable_notify: bool
    enable_library_notify: Optional[bool] = False
    
    wecom_corpid: Optional[str] = ""
    wecom_corpsecret: Optional[str] = ""
    wecom_agentid: Optional[str] = ""
    wecom_touser: Optional[str] = "@all"
    wecom_proxy_url: Optional[str] = "https://qyapi.weixin.qq.com"
    wecom_token: Optional[str] = ""
    wecom_aeskey: Optional[str] = ""

class PushRequestModel(BaseModel):
    user_id: str
    period: str
    theme: str

class ScheduleRequestModel(BaseModel):
    user_id: str
    period: str
    theme: str

class UserUpdateModel(BaseModel):
    user_id: str
    password: Optional[str] = None
    is_disabled: Optional[bool] = None
    expire_date: Optional[str] = None 
    enable_all_folders: Optional[bool] = None
    enabled_folders: Optional[List[str]] = None
    excluded_sub_folders: Optional[List[str]] = None

class NewUserModel(BaseModel):
    name: str
    password: Optional[str] = None 
    expire_date: Optional[str] = None
    template_user_id: Optional[str] = None 

class InviteGenModel(BaseModel):
    days: int 
    template_user_id: Optional[str] = None 
    count: Optional[int] = 1

class UserRegisterModel(BaseModel):
    code: str
    username: str
    password: str

# 🔥 新增：批量操作模型
class BatchActionModel(BaseModel):
    user_ids: List[str]
    action: str  # 可选: 'enable', 'disable', 'delete', 'renew'
    value: Optional[str] = None  # 用于 renew 时传递 '+30' 或 '2025-10-01'

# ================= 资源求片系统 Models (V2.0 增强版) =================

class MediaRequestSubmitModel(BaseModel):
    tmdb_id: int
    media_type: str  # 'movie' or 'tv'
    title: str
    year: str = ""
    poster_path: str = ""
    overview: str = "" # 🔥 新增：用于发送带简介的丰富通知

class MediaRequestStatusUpdateModel(BaseModel):
    tmdb_id: int
    status: int  

class MediaRequestActionModel(BaseModel):
    action: str  
    tmdb_id: int