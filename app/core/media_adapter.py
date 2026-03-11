import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from app.core.config import cfg
import logging

logger = logging.getLogger("uvicorn")

class MediaServerAdapter:
    def __init__(self):
        # 🔥 工业级抗压处理：使用全局 Session 复用连接，并设置指数退避重试
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
        self.session.mount('http://', HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100))
        self.session.mount('https://', HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100))

    @property
    def host(self):
        return cfg.get("emby_host", "").rstrip('/')

    @property
    def api_key(self):
        return cfg.get("emby_api_key", "")

    @property
    def server_type(self):
        # 获取类型，转为小写，默认为 emby
        return cfg.get("server_type", "emby").lower()

    def _build_url(self, path: str) -> str:
        """智能路由转换器：解决 Jellyfin 和 Emby 路径差异"""
        if not path.startswith('/'):
            path = '/' + path
        
        if self.server_type == "jellyfin":
            # Jellyfin 的 API 抛弃了 /emby 前缀
            if path.startswith('/emby/'):
                path = path.replace('/emby/', '/', 1)
        else:
            # Emby 保留 /emby 前缀
            if not path.startswith('/emby/'):
                path = '/emby' + path

        return f"{self.host}{path}"

    def _get_headers(self, custom_headers=None) -> dict:
        """智能鉴权转换器：解决鉴权方式差异"""
        headers = {}
        if self.server_type == "jellyfin":
            headers["Authorization"] = f'MediaBrowser Token="{self.api_key}"'
        else:
            headers["X-Emby-Token"] = self.api_key
        
        if custom_headers:
            headers.update(custom_headers)
        return headers

    def request(self, method: str, path: str, **kwargs):
        """统一请求拦截入口"""
        if not self.host or not self.api_key:
            raise ValueError("Media Server 尚未配置完整 (Host 或 API Key 缺失)")

        url = self._build_url(path)
        kwargs['headers'] = self._get_headers(kwargs.get('headers'))
        
        # 因为已经统一使用了 Header 鉴权，所以清理掉可能存在的 URL Params 里的 api_key (更安全优雅)
        if 'params' in kwargs and kwargs['params'] and 'api_key' in kwargs['params']:
            del kwargs['params']['api_key']

        return self.session.request(method, url, **kwargs)

    # 便捷方法包装
    def get(self, path: str, **kwargs): return self.request('GET', path, **kwargs)
    def post(self, path: str, **kwargs): return self.request('POST', path, **kwargs)
    def delete(self, path: str, **kwargs): return self.request('DELETE', path, **kwargs)

# 实例化单例，全局复用
media_api = MediaServerAdapter()