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

# Coupon parsing helpers
_NAME_PATTERNS = [
    re.compile(r'(?:\u4f18\u60e0\u5238\u6807\u9898|\u4f18\u60e0\u5238\u540d\u79f0|\u4f18\u60e0\u540d\u79f0|\u6807\u9898|\u540d\u79f0|\u5238\u540d|\u5546\u54c1\u540d\u79f0|title|name)\s*[:\uff1a]\s*(.+)', re.I),
]
_DETAIL_PATTERN = re.compile(r'(?:\u5185\u5bb9|\u63cf\u8ff0|\u9002\u7528|\u5546\u54c1|\u5957\u9910|\u8bf4\u660e)\s*[:\uff1a]\s*(.+)')
_META_LABELS = [
    "\u6709\u6548\u671f", "\u72b6\u6001", "\u5238\u7801", "\u5238\u53f7", "\u4f7f\u7528\u89c4\u5219",
    "\u56fe\u7247", "\u94fe\u63a5", "\u4ef7\u683c", "\u7528\u5238\u4ef7\u683c", "coupon", "code"
]
_GENERIC_NAMES = {
    "\u4f18\u60e0", "\u4f18\u60e0\u5238", "\u4f18\u60e0\u5238\u6807\u9898", "\u4f18\u60e0\u5238\u540d\u79f0", "\u5238"
}
_PRICE_ONLY_RE = re.compile(r'^(?:\u4f18\u60e0|\u7279\u60e0)?\s*¥\s*\d+(?:\.\d+)?$', re.I)


def _normalize_coupon_name(name: str) -> str:
    name = clean_markdown_text(name)
    # Remove trailing expiry info inside parentheses
    name = re.sub(r'[\(（].*?有效.*?[\)）]', '', name).strip()
    if "有效期" in name:
        name = re.split(r'有效期.*', name)[0].strip()
    name = re.sub(r'^[\s\-\u2022\*]+', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _is_metadata_label(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    for kw in _META_LABELS:
        if kw.lower() in lowered:
            return True
    return False


def _is_generic_coupon_name(name: str) -> bool:
    if not name:
        return True
    if name in _GENERIC_NAMES:
        return True
    lowered = name.lower()
    compact = re.sub(r'\s+', '', name)
    if _PRICE_ONLY_RE.match(compact):
        return True
    return lowered in {"coupon", "coupons"}


def _extract_coupon_name_from_line(line: str) -> str:
    if not line:
        return ""
    clean_line = clean_markdown_text(line)
    price_like = re.search(r'¥\s*\d+(\.\d+)?', clean_line)
    for pat in _NAME_PATTERNS:
        match = pat.search(clean_line)
        if match:
            name = _normalize_coupon_name(match.group(1))
            if name and not _is_metadata_label(name):
                return name
    stripped = re.sub(r'^[\s\-\u2022\*]+', '', clean_line)
    if price_like and len(stripped) <= 80 and not _is_metadata_label(stripped):
        stripped = re.sub(r'[\(（].*?有效.*?[\)）]', '', stripped).strip()
        if stripped:
            return _normalize_coupon_name(stripped)
    if stripped.startswith("##"):
        name = _normalize_coupon_name(stripped.lstrip("#").strip())
        if name and not _is_metadata_label(name):
            return name
    match = re.match(r'^([^:\uff1a]{2,80})\s*[:\uff1a]', stripped)
    if match:
        name = _normalize_coupon_name(match.group(1))
        if name and not _is_metadata_label(name):
            return name
    # Fallback: plain bullet line without colon
    if line.lstrip().startswith(("-", "*", "\u2022")):
        candidate = stripped
        if candidate and len(candidate) <= 80 and not _is_metadata_label(candidate):
            if not re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', candidate):
                return candidate
    return ""

def _extract_detail_from_line(line: str) -> str:
    if not line:
        return ""
    clean_line = clean_markdown_text(line)
    if ":" in clean_line:
        _, detail = clean_line.split(":", 1)
    elif "\uff1a" in clean_line:
        _, detail = clean_line.split("\uff1a", 1)
    else:
        return ""
    detail = detail.strip()
    detail = re.split(r'(?:\u6709\u6548\u671f|\u6709\u6548\u81f3|\(|\uff08)', detail)[0].strip()
    if len(detail) < 2:
        return ""
    return detail

def _extract_descriptive_detail(line: str) -> str:
    if not line:
        return ""
    clean_line = clean_markdown_text(line)
    match = _DETAIL_PATTERN.search(clean_line)
    if not match:
        return ""
    detail = match.group(1).strip()
    detail = re.split(r'(?:\u6709\u6548\u671f|\u6709\u6548\u81f3|\(|\uff08)', detail)[0].strip()
    if len(detail) < 2:
        return ""
    return detail


def _extract_coupon_code(line: str) -> str:
    if not line:
        return ""
    match = re.search(r'(?:couponCode|couponId|\u5238\u7801|\u5238\u53f7|\u5151\u6362\u7801)\s*[:\uff1a]\s*([A-Za-z0-9\-]{3,})', line, re.I)
    return match.group(1) if match else ""

def parse_expiry_date(text: str) -> Optional[datetime]:
    """
    从优惠券文本中提取有效期
    支持格式：2026-01-25、2026/01/25、01月25日等
    """
    now = get_cst_now().replace(tzinfo=None)
    # 尝试匹配 YYYY-MM-DD 或 YYYY/MM/DD
    match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            pass
    
    # 尝试匹配 MM月DD日
    match = re.search(r'(\d{1,2})月\s*(\d{1,2})日', text)
    if match:
        month, day = match.groups()
        try:
            year = now.year
            date = datetime(year, int(month), int(day))
            # 如果日期已过，可能是明年的
            if date < now:
                date = datetime(year + 1, int(month), int(day))
            return date
        except ValueError:
            pass
    
    # 尝试匹配 "有效期至..."
    match = re.search(r'(?:有效期|有效期至|有效期到|有效期为|有效期截止)[^\d]*(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            pass

    # 有效期但无年份：有效期至 01-31
    match = re.search(r'(?:有效期|有效期至|有效期到|有效期为|有效期截止)[^\d]*(\d{1,2})[-/](\d{1,2})', text)
    if match:
        month, day = match.groups()
        try:
            year = now.year
            date = datetime(year, int(month), int(day))
            if date < now:
                date = datetime(year + 1, int(month), int(day))
            return date
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
    
    
    # 解析优惠券文本，按行或按段落分割
    lines = coupons_text.splitlines()
    current_coupon = {}
    
    for idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            if current_coupon and current_coupon.get('expiry_date'):
                expiring_coupons.append(current_coupon)
                current_coupon = {}
            continue
        
        name_candidate = _extract_coupon_name_from_line(line)
        if name_candidate:
            if current_coupon:
                if current_coupon.get('expiry_date') and _is_generic_coupon_name(current_coupon.get('name', '')) and not _is_generic_coupon_name(name_candidate):
                    current_coupon['name'] = name_candidate
                    current_coupon['raw_text'] = line
                else:
                    expiring_coupons.append(current_coupon)
                    current_coupon = {}
            if not current_coupon:
                current_coupon = {'name': name_candidate, 'raw_text': line}

        code = _extract_coupon_code(line)
        if code and current_coupon:
            current_coupon['code'] = code

        if current_coupon and _is_generic_coupon_name(current_coupon.get('name', '')):
            detail = _extract_descriptive_detail(line) or _extract_detail_from_line(line)
            if detail and detail not in current_coupon['name']:
                current_coupon['name'] = f"{current_coupon['name']} {detail}".strip()

        expiry = parse_expiry_date(line)
        if expiry:
            if not current_coupon:
                current_coupon = {'name': name_candidate or "", 'raw_text': line}
            current_coupon['expiry_date'] = expiry
            current_coupon['days_left'] = (expiry - now).days
            current_coupon['expiry_line_idx'] = idx
    
    # 添加最后一个
    if current_coupon and current_coupon.get('expiry_date'):
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
        
        name = coupon.get('name') or "\u672a\u8bc6\u522b\u5238\u540d"
        expiry_dt = coupon.get('expiry_date')
        if expiry_dt:
            expiry_str = expiry_dt.strftime('%Y-%m-%d')
            msg_parts.append(f"{urgency} {name}(\u6709\u6548\u671f\u81f3 {expiry_str})")
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
