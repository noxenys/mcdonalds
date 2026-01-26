"""
优惠券提醒和精选推送模块
提供优惠券有效期检测、每日精选分析等功能
"""
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

def get_cst_now():
    """获取当前北京时间"""
    utc_now = datetime.now(timezone.utc)
    return utc_now + timedelta(hours=8)

def clean_markdown_text(text: str) -> str:
    """清理 Markdown 格式文本"""
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    # Remove Markdown bold/italic/code markers and backslashes
    return text.replace("**", "").replace("__", "").replace("*", "").replace("`", "").replace("\\", "").strip()

def parse_expiry_date(text: str) -> Optional[datetime]:
    """
    从优惠券文本中提取有效期
    支持格式：2026-01-25、2026/01/25、01月25日等
    """
    # 尝试匹配 YYYY-MM-DD 或 YYYY/MM/DD
    match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            pass
    
    # 尝试匹配 MM月DD日
    match = re.search(r'(\d{1,2})月(\d{1,2})日', text)
    if match:
        month, day = match.groups()
        try:
            now = get_cst_now()
            year = now.year
            date = datetime(year, int(month), int(day))
            # 如果日期已过，可能是明年的
            if date < now:
                date = datetime(year + 1, int(month), int(day))
            return date
        except ValueError:
            pass
    
    # 尝试匹配 "有效期至..."
    match = re.search(r'有效期[：:至到]*\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            pass
    
    return None

def check_expiring_soon(coupons_text: str, days_threshold: int = 3) -> List[Dict]:
    """
    检查即将过期的优惠券
    
    Args:
        coupons_text: 优惠券文本
        days_threshold: 天数阈值（默认3天内）
    
    Returns:
        即将过期的优惠券列表
    """
    expiring_coupons = []
    now = get_cst_now()
    # Ensure now is naive for comparison if parsed dates are naive, or handle tz
    # datetime(...) creates naive objects by default. 
    # But get_cst_now returns tz-aware. 
    # To compare with naive dates from parse_expiry_date (which uses datetime(y,m,d)), 
    # we should make now naive or make parsed dates aware.
    # Simpler to make now naive (stripping tzinfo) since we manually adjusted to CST.
    now = now.replace(tzinfo=None)
    
    threshold_date = now + timedelta(days=days_threshold)
    
    # 解析优惠券文本，按行或按段落分割
    lines = coupons_text.splitlines()
    current_coupon = {}
    
    for line in lines:
        line = line.strip()
        if not line:
            if current_coupon:
                expiring_coupons.append(current_coupon)
                current_coupon = {}
            continue
        
        title_match = re.search(r'(优惠券标题|标题|名称)[：:]\s*(.+)', line)
        if title_match:
            title = title_match.group(2).strip()
            if current_coupon:
                expiring_coupons.append(current_coupon)
            current_coupon = {'name': title, 'raw_text': line}
        else:
            is_metadata = any(keyword in line for keyword in ['有效期', '状态', 'coupon', '图片', 'http', '券码', '使用规则'])
            if (line.startswith('-') or line.startswith('•') or line.startswith('##')) and not is_metadata:
                if current_coupon:
                    expiring_coupons.append(current_coupon)
                
                title = re.sub(r'^[-•#\s]+', '', line).strip()
                
                if title.startswith('优惠券标题：'):
                    title = title.replace('优惠券标题：', '').strip()
                elif title.startswith('标题：'):
                    title = title.replace('标题：', '').strip()
                    
                current_coupon = {'name': title, 'raw_text': line}
        
        # 检测有效期
        expiry = parse_expiry_date(line)
        if expiry:
            if current_coupon:
                current_coupon['expiry_date'] = expiry
                current_coupon['days_left'] = (expiry - now).days
    
    # 添加最后一个
    if current_coupon:
        expiring_coupons.append(current_coupon)
    
    # 过滤：只返回即将过期的
    result = [
        c for c in expiring_coupons 
        if 'expiry_date' in c and 0 <= c['days_left'] <= days_threshold
    ]
    
    return result

def analyze_coupon_value(coupon_text: str) -> int:
    """
    分析优惠券价值，返回评分（0-100）
    评分标准：免费>买一送一>大额折扣>小额折扣
    """
    score = 50  # 基础分
    text = coupon_text.lower()
    
    # 免费类
    if '免费' in text or '0元' in text:
        score += 50
    
    # 买一送一
    if '买一送一' in text or '1+1' in text or '买1送1' in text:
        score += 40
    
    # 半价
    if '半价' in text or '5折' in text:
        score += 35
    
    # 大额优惠
    if any(word in text for word in ['19.9', '29.9', '39.9']):
        score += 25
    
    # 小额优惠
    if any(word in text for word in ['9.9', '6.9', '4.9']):
        score += 15
    
    # 热门商品
    if any(word in text for word in ['巨无霸', '麦辣鸡腿堡', '薯条', '汉堡']):
        score += 10
    
    # 限时
    if '限时' in text or '今日' in text:
        score += 5
    
    return min(score, 100)

def get_daily_highlights(available_coupons_text: str, top_n: int = 5) -> List[Dict]:
    """
    从可领优惠券中筛选出每日精选
    
    Args:
        available_coupons_text: 可领优惠券文本
        top_n: 返回前N个
    
    Returns:
        精选优惠券列表，按价值排序
    """
    coupons = []
    lines = available_coupons_text.splitlines()
    
    current_coupon = {}
    for line in lines:
        line = line.strip()
        if not line:
            if current_coupon:
                coupons.append(current_coupon)
                current_coupon = {}
            continue
        
        # 提取优惠券名称
        if re.match(r'^\d+\.', line) or line.startswith('-') or line.startswith('##'):
            if current_coupon:
                coupons.append(current_coupon)
            
            title = re.sub(r'^[\d\-•#.\s]+', '', line).strip()
            current_coupon = {
                'name': title,
                'raw_text': line
            }
    
    if current_coupon:
        coupons.append(current_coupon)
    
    # 计算每个券的价值分数
    for coupon in coupons:
        coupon['score'] = analyze_coupon_value(coupon['name'])
    
    # 按分数排序，返回top N
    sorted_coupons = sorted(coupons, key=lambda x: x['score'], reverse=True)
    return sorted_coupons[:top_n]

def format_expiry_reminder(expiring_coupons: List[Dict]) -> str:
    """格式化过期提醒消息"""
    if not expiring_coupons:
        return ""
    
    SEPARATOR = "━━━━━━━━━━━━━━━━━━━"
    
    msg_parts = [
        "⏰ 优惠券过期提醒",
        SEPARATOR,
        "",
        f"你有 {len(expiring_coupons)} 张优惠券即将过期：",
        ""
    ]
    
    for coupon in expiring_coupons:
        days_left = coupon.get('days_left', 0)
        if days_left == 0:
            urgency = "🔴 今天过期！"
        elif days_left == 1:
            urgency = "🟠 明天过期"
        else:
            urgency = f"🟡 {days_left}天后过期"
        
        name = coupon.get('name') or "未识别券名"
        expiry_dt = coupon.get('expiry_date')
        if expiry_dt:
            expiry_str = expiry_dt.strftime('%Y-%m-%d')
            msg_parts.append(f"{urgency} {name}（有效期至 {expiry_str}）")
        else:
            msg_parts.append(f"{urgency} {name}")
    
    msg_parts.extend(["", "💡 记得及时使用，不要浪费哦~"])
    
    return "\n".join(msg_parts)

def format_daily_highlights(highlights: List[Dict]) -> str:
    """格式化每日精选消息"""
    if not highlights:
        return ""
    
    SEPARATOR = "━━━━━━━━━━━━━━━━━━━"
    # Use CST for hour check
    now = get_cst_now()
    current_hour = now.hour
    
    # 根据时段调整问候语
    if 5 <= current_hour < 12:
        greeting = "🌅 早安！今日精选优惠新鲜出炉"
    elif 12 <= current_hour < 18:
        greeting = "☀️ 午间优惠精选"
    else:
        greeting = "🌙 晚间优惠精选"
    
    msg_parts = [
        greeting,
        SEPARATOR,
        "",
        f"根据优惠力度，今天最值得领的 {len(highlights)} 张券：",
        ""
    ]
    
    medals = ["🥇", "🥈", "🥉", "🏅", "⭐"]
    for i, coupon in enumerate(highlights):
        medal = medals[i] if i < len(medals) else "📌"
        msg_parts.append(f"{medal} {coupon['name']}")
    
    msg_parts.extend([
        "",
        "💰 先到先得，记得及时领取！",
        "",
        "发送 /claim 立即领券"
    ])
    
    return "\n".join(msg_parts)
