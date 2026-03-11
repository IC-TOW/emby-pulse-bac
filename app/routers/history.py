from fastapi import APIRouter
from typing import Optional
from app.core.database import query_db
from app.core.config import cfg
# 🔥 引入核心适配器
from app.core.media_adapter import media_api
import math

router = APIRouter()

# --- 内部工具：获取用户映射 ---
def get_user_map_local():
    user_map = {}
    try:
        # 🚀 替换为 media_api
        res = media_api.get("/Users", timeout=2)
        if res.status_code == 200:
            for u in res.json(): 
                user_map[u['Id']] = u['Name']
    except: 
        pass
    return user_map

@router.get("/api/history/list")
def api_get_history(
    page: int = 1, 
    limit: int = 20, 
    user_id: Optional[str] = None, 
    keyword: Optional[str] = None
):
    try:
        where_clauses = []
        params = []
        
        hidden_users = cfg.get("hidden_users") or []
        if hidden_users:
            placeholders = ','.join(['?'] * len(hidden_users))
            where_clauses.append(f"UserId NOT IN ({placeholders})")
            params.extend(hidden_users)

        if user_id and user_id != 'all':
            where_clauses.append("UserId = ?")
            params.append(user_id)
            
        if keyword:
            where_clauses.append("ItemName LIKE ?")
            params.append(f"%{keyword}%")

        where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        count_sql = f"SELECT COUNT(*) as c FROM PlaybackActivity{where_sql}"
        count_res = query_db(count_sql, params)
        total = count_res[0]['c'] if count_res else 0
        total_pages = math.ceil(total / limit)

        offset = (page - 1) * limit
        
        data_sql = f"""
            SELECT DateCreated, UserId, ItemId, ItemName, ItemType, PlayDuration, DeviceName, ClientName
            FROM PlaybackActivity
            {where_sql}
            ORDER BY DateCreated DESC 
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = query_db(data_sql, params)

        user_map = get_user_map_local()
        result = []
        for row in rows:
            item = dict(row)
            item['UserName'] = user_map.get(item['UserId'], "未知用户")
            
            seconds = item.get('PlayDuration') or 0
            if seconds < 60:
                item['DurationStr'] = f"{seconds}秒"
            elif seconds < 3600:
                item['DurationStr'] = f"{round(seconds/60)}分钟"
            else:
                item['DurationStr'] = f"{round(seconds/3600, 1)}小时"
            
            try:
                item['DateStr'] = item['DateCreated'].replace('T', ' ')[:16]
            except:
                item['DateStr'] = item['DateCreated']
                
            result.append(item)

        return {
            "status": "success", 
            "data": result, 
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": total_pages
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "data": []}