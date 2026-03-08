import re
import os
from comment.ip_db.xdbSearcher import XdbSearcher

def searchProvince(ip):
    """
    查询IP地址归属地（省份或国家）
    
    Args:
        ip: IPv4 地址字符串
    
    Returns:
        str: 归属地名称，格式为省份名（国内）或国家名（国外），失败返回"未知"
    """
    # 简单校验 IP 格式
    if not ip or not isinstance(ip, str):
        return "未知"
    
    # 去除首尾空格
    ip = ip.strip()
    
    # 校验 IPv4 格式
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
        return "未知"
    
    # 校验 IP 地址范围
    parts = ip.split('.')
    if any(int(part) > 255 for part in parts):
        return "未知"
    
    searcher = None
    try:
        # 加载 IP 数据库
        dbPath = os.path.join(os.path.dirname(__file__), "ip2region_v4.xdb")
        
        # 检查数据库文件是否存在
        if not os.path.exists(dbPath):
            return "未知"
        
        # 加载数据库内容
        cb = XdbSearcher.loadContentFromFile(dbfile=dbPath)
        
        # 创建新的 searcher 实例（避免共享状态）
        searcher = XdbSearcher(contentBuff=cb)
        
        # 查询IP归属地
        region_str = searcher.search(ip)
        
        if not region_str:  # None 或 空字符串
            return "未知"
        
        # 解析结果：格式为 国家|区域|省份|城市|运营商
        region_parts = region_str.split("|")
        
        if len(region_parts) < 2:
            return "未知"
        
        country = region_parts[0] if region_parts[0] != '0' else ""
        province = region_parts[1] if len(region_parts) > 1 and region_parts[1] != '0' else ""
        
        # 国内IP返回省份，国外IP返回国家
        if country == "中国":
            if province:
                # 去掉"省"字后缀
                if province.endswith("省"):
                    province = province[:-1]
                return province
            else:
                return "中国"
        elif country:
            return country
        else:
            return "未知"
    
    except Exception as e:
        # 记录错误但不抛出异常
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"IP归属地查询失败 (IP: {ip}): {e}")
        return "未知"
    
    finally:
        # 确保关闭 searcher，释放资源
        if searcher:
            try:
                searcher.close()
            except Exception:
                pass

