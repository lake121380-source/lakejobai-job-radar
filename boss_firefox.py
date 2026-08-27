#!/usr/bin/env python3
"""
BOSS直聘 AI Agent 岗位采集工具

流程:
  1. 搜索列表页 → 提取基本信息（标题、薪资、公司、城市、经验、学历、链接）
  2. 逐个访问详情页 → 提取"岗位技能"原文输出

用法:
  python3 boss_firefox.py                     # 采集+分析
  python3 boss_firefox.py --login             # 首次扫码登录
  python3 boss_firefox.py --headless          # 无头模式
"""

import argparse
import csv
import io
import json
import os
import random
import re
import sys
import time
import logging
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import quote_plus, urlencode

from playwright.sync_api import sync_playwright

# Windows 编码修复
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── 配置 ──
TODAY = date.today().isoformat()
DATE_STR = date.today().strftime("%Y-%m-%d")

KEYWORDS = [
    "AI Agent",
    "AI产品经理",
    "电商",
    "机械",
    "化工",
    "外贸",
]

# BOSS直聘城市代码
CITIES = {
    # 山东省
    "济南": "101120100",
    "青岛": "101120200",
    "淄博": "101120300",
    "德州": "101120400",
    "烟台": "101120500",
    "潍坊": "101120600",
    "济宁": "101120700",
    "泰安": "101120800",
    "临沂": "101120900",
    "菏泽": "101121000",
    "滨州": "101121100",
    "东营": "101121200",
    "威海": "101121300",
    "枣庄": "101121400",
    "日照": "101121500",
    "聊城": "101121700",
    # 一线城市
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    # 新一线城市
    "成都": "101270100",
    "杭州": "101210100",
    "武汉": "101200100",
    "南京": "101190100",
    "重庆": "101040100",
    "西安": "101110100",
    "长沙": "101250100",
    "天津": "101030100",
    "苏州": "101190400",
    "郑州": "101180100",
    "东莞": "101281600",
    "沈阳": "101070100",
    "宁波": "101210400",
    "昆明": "101290100",
    # 其他省会城市
    "合肥": "101220100",
    "福州": "101230100",
    "厦门": "101230200",
    "南昌": "101240100",
    "贵阳": "101260100",
    "南宁": "101300100",
    "太原": "101100100",
    "石家庄": "101090100",
    "哈尔滨": "101050100",
    "长春": "101060100",
    "兰州": "101160100",
    "乌鲁木齐": "101130100",
    "呼和浩特": "101080100",
    "拉萨": "101140100",
    "西宁": "101150100",
    "银川": "101170100",
    "海口": "101310100",
    "三亚": "101310200",
    "全国": "100010000",
}

OUTPUT_DIR = Path.home() / "AI" / "岗位日报"
STATE_FILE = Path(__file__).parent / ".boss_profile" / "firefox_state.json"
PROFILE_DIR = Path(__file__).parent / ".boss_profile" / "firefox_user_data"

ANTI_DETECT = """
// ── 核心：隐藏 webdriver 标记 ──
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
try { delete Object.getPrototypeOf(navigator).webdriver; } catch(e) {}

// ── 语言 ──
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});

// ── 硬件（桌面端典型值）──
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});

// ── screen 与 viewport 保持一致 ──
const fixScreen = () => {
    const w = window.innerWidth || 1280;
    const h = window.innerHeight || 800;
    Object.defineProperty(screen, 'width',  {get: () => w});
    Object.defineProperty(screen, 'height', {get: () => h});
    Object.defineProperty(screen, 'availWidth',  {get: () => w});
    Object.defineProperty(screen, 'availHeight', {get: () => h});
    Object.defineProperty(screen, 'colorDepth', {get: () => 24});
    Object.defineProperty(screen, 'pixelDepth', {get: () => 24});
};
fixScreen();
window.addEventListener('resize', fixScreen);

// ── 时区 ──
if (Intl && Intl.DateTimeFormat) {
    const origResolved = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function() {
        const r = origResolved.call(this);
        r.timeZone = 'Asia/Shanghai';
        return r;
    };
}

// ── canvas 指纹干扰：轻微噪声扰动 ──
try {
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function() {
        const ctx = this.getContext('2d');
        if (ctx && this.width > 10 && this.height > 10) {
            const imgData = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < imgData.data.length; i += 4) {
                imgData.data[i] ^= 1;  // R channel ±1 bit
            }
            ctx.putImageData(imgData, 0, 0);
        }
        return origToDataURL.apply(this, arguments);
    };
} catch(e) {}

// ── WebGL 指纹一致性 ──
try {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        // UNMASKED_VENDOR / UNMASKED_RENDERER
        if (p === 37445) return 'Google Inc. (Intel)';
        if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)';
        return getParam.call(this, p);
    };
} catch(e) {}

// ── 权限通知 ──
if (window.Permissions) {
    const orig = window.Permissions.prototype.query;
    window.Permissions.prototype.query = function(d) {
        if (d.name === 'notifications') return Promise.resolve({state: 'prompt'});
        return orig.call(this, d);
    };
}

// ── navigator.connection ──
try {
    Object.defineProperty(navigator, 'connection', {
        get: () => ({effectiveType: '4g', rtt: 50, downlink: 10, saveData: false})
    });
} catch(e) {}
"""

# ── 技能词库（仅分析用）──
SKILL_MAP = {
    "编程语言": {
        "Python",
        "Java",
        "Go",
        "Golang",
        "Rust",
        "C++",
        "C#",
        "C",
        "PHP",
        "Ruby",
        "Swift",
        "Kotlin",
        "Scala",
        "TypeScript",
        "JavaScript",
        "Node.js",
    },
    "前端": {"React", "Vue", "Angular", "Next.js", "HTML", "CSS", "Tailwind"},
    "AI/ML框架": {
        "PyTorch",
        "TensorFlow",
        "Transformers",
        "vLLM",
        "ONNX",
        "HuggingFace",
        "GGUF",
        "Stable Diffusion",
        "Diffusion",
        "Vision",
        "Multimodal",
    },
    "AI框架/工具": {
        "LangChain",
        "LangGraph",
        "LlamaIndex",
        "AutoGen",
        "CrewAI",
        "Dify",
        "Coze",
        "MCP",
    },
    "大模型技术": {
        "RAG",
        "Fine-tuning",
        "Finetune",
        "微调",
        "SFT",
        "RLHF",
        "LoRA",
        "QLoRA",
        "Prompt",
        "Function Calling",
        "Tool Calling",
        "Agent",
        "Multi-Agent",
        "Embedding",
        "LLM",
        "AI Agent",
        "AIGC",
    },
    "数据库/中间件": {
        "MySQL",
        "PostgreSQL",
        "Redis",
        "MongoDB",
        "Elasticsearch",
        "Milvus",
        "FAISS",
        "Chroma",
        "Qdrant",
        "Pinecone",
        "Weaviate",
        "Kafka",
        "RabbitMQ",
    },
    "部署/架构": {
        "Docker",
        "Kubernetes",
        "K8s",
        "FastAPI",
        "Flask",
        "Django",
        "Spring",
        "Nginx",
        "gRPC",
        "GraphQL",
        "WebSocket",
        "REST",
        "RESTful",
        "CI/CD",
        "GitHub Actions",
        "Linux",
        "GPU",
        "CUDA",
    },
    "云平台": {"AWS", "GCP", "Azure", "阿里云", "腾讯云"},
    "其他": {
        "数据结构",
        "算法",
        "系统设计",
        "架构",
        "微服务",
        "高并发",
        "分布式",
        "设计模式",
        "OOP",
        "TDD",
        "单元测试",
        "测试",
    },
}
ALL_SKILLS = {s for v in SKILL_MAP.values() for s in v}
MY_SKILLS = {
    s.lower()
    for v in {
        "编程语言": {"Python", "TypeScript", "JavaScript"},
        "AI框架/工具": {"LangChain", "LangGraph", "AutoGen", "CrewAI", "Dify", "Coze"},
        "大模型技术": {
            "LLM",
            "AI Agent",
            "RAG",
            "微调",
            "MCP",
            "Prompt Engineering",
            "Function Calling",
            "Tool Calling",
            "Embedding",
        },
        "数据库/向量库": {"MySQL", "Milvus", "FAISS", "Chroma", "Qdrant"},
        "部署/运维": {"Docker", "FastAPI", "Kubernetes"},
        "AI平台/模型": {"Claude", "OpenAI", "GPT"},
    }.values()
    for s in v
}


def decode_salary(text):
    return "".join(str(ord(c) - 0xE030) if 0xE030 <= ord(c) <= 0xE039 else c for c in text)


def salary_ok(text):
    if not text:
        return False
    nums = re.findall(
        r"(\d+)",
        re.sub(r"[^\d-]", "", text.replace("~", "-").replace("K", "").replace("k", "")),
    )
    if len(nums) < 2:
        return False
    l, h = int(nums[0]), int(nums[1])
    if l < 5 and h < 20:
        l *= 10
        h *= 10
    return 15 <= l and h <= 35


def pause(a=1.0, b=3.0):
    time.sleep(random.uniform(a, b))


def parse_skills(text):
    tl = text.lower()
    r = defaultdict(list)
    for cat, skills in SKILL_MAP.items():
        for s in skills:
            if s.lower() in tl:
                r[cat].append(s)
    return dict(r)


# ══════════════════════════════════════
#  HR 活跃度解析 (CHANGES §4)
# ══════════════════════════════════════

# BOSS 直聘的活跃度文案:  刚刚活跃 / 今日活跃 / 3日内活跃 / 本周活跃 / 30日内活跃 / 半年内活跃
_HR_ACTIVE_RE = [
    (re.compile(r"(刚刚|刚才|刚刚活跃|今日活跃|今天活跃)"), 0),
    (re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*日(?:内)?\s*活跃"), "day"),
    (re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*天\s*(?:内)?\s*活跃"), "day"),
    (re.compile(r"本周活跃|这周活跃|周内活跃|一周(?:内)?活跃"), 7),
    (re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*周(?:内)?\s*活跃"), "week"),
    (re.compile(r"30\s*日\s*内\s*活跃|月内活跃"), 30),
    (re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*个?月\s*内?\s*活跃"), "month"),
    (re.compile(r"半(?:年|个?年)内活跃|6\s*个月\s*内"), 180),
    (re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*年\s*活跃"), "year"),
    (re.compile(r"很久未活跃|不活跃|未知"), -1),
]

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_cn_int(s: str) -> Optional[int]:
    """'三' -> 3, '十二' -> 12, '3' -> 3. 失败返 None."""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    if len(s) == 2 and s[0] == "十":
        return 10 + _CN_NUM.get(s[1], 0)
    if len(s) == 2 and s[1] == "十":
        return _CN_NUM.get(s[0], 0) * 10
    if len(s) == 3 and s[1] == "十":
        return _CN_NUM.get(s[0], 0) * 10 + _CN_NUM.get(s[2], 0)
    return None


def parse_hr_active(text: str) -> dict:
    """解析 BOSS 的 HR 活跃度文案, 返回 {days, label, raw}.

    Examples:
        "刚刚活跃"           -> {days: 0, label: "刚刚活跃", raw: ...}
        "今日活跃"           -> {days: 0, ...}
        "3日内活跃"          -> {days: 3, ...}
        "本周活跃"           -> {days: 7, ...}
        "30日内活跃"         -> {days: 30, ...}
        "半年内活跃"         -> {days: 180, ...}
        "三个月活跃"         -> {days: 90, ...}
        "一年活跃"           -> {days: 365, ...}
        "" (没匹配到)         -> {days: -1, label: "", raw: ""}
    """
    if not text:
        return {"days": -1, "label": "", "raw": ""}
    raw = text.strip()
    for pat, mode in _HR_ACTIVE_RE:
        m = pat.search(raw)
        if not m:
            continue
        if isinstance(mode, int):
            return {"days": mode, "label": raw, "raw": raw}
        # mode = 'day' | 'week' | 'month' | 'year'  → 提取数字 × 倍数
        num_str = next((g for g in m.groups() if g), None)
        n = _parse_cn_int(num_str) if num_str else None
        if n is None:
            return {"days": -1, "label": raw, "raw": raw}
        if mode == "day":
            return {"days": n, "label": raw, "raw": raw}
        if mode == "week":
            return {"days": n * 7, "label": raw, "raw": raw}
        if mode == "month":
            return {"days": n * 30, "label": raw, "raw": raw}
        if mode == "year":
            return {"days": n * 365, "label": raw, "raw": raw}
    return {"days": -1, "label": raw, "raw": raw}


# ══════════════════════════════════════
#  浏览器
# ══════════════════════════════════════


log = logging.getLogger(__name__)


class BossScraper:
    def __init__(self, headless=False):
        self.headless = headless
        self._pw = self._br = self._ctx = None
        self.page = None

    def start(self):
        self._pw = sync_playwright().start()
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        kw = {
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 800},
            "locale": "zh-CN",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        }
        self._ctx = self._pw.firefox.launch_persistent_context(str(PROFILE_DIR), **kw)
        self._br = None

        # 持久化 profile 自动管理 cookies，不额外 add_cookies 避免冲突
        if STATE_FILE.exists():
            try:
                state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                cookies = state.get("cookies") or []
                if cookies:
                    for c in cookies:
                        try:
                            self._ctx.add_cookies([c])
                        except Exception:
                            pass
            except Exception:
                pass

        self._ctx.add_init_script(ANTI_DETECT)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.set_default_timeout(30000)

    def close(self):
        if self._ctx:
            try:
                self._ctx.close()
            except Exception:
                pass
        elif self._br:
            self._br.close()
        if self._pw:
            self._pw.stop()

    def _body_text(self, limit=1500):
        try:
            return self.page.inner_text("body")[:limit]
        except Exception:
            return ""

    def _login_prompt_visible(self):
        """判断当前页面是否真的落在登录/扫码态，避免误判普通详情页。"""
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""

        explicit_login_paths = (
            "/web/user/",
            "/login/",
            "ka=header-login",
            "login?redirect=",
        )
        if any(path in url for path in explicit_login_paths):
            return True

        body = self._body_text(4000)

        # 详情页/聊天页的已登录特征，优先级高于任意“登录”字样。
        authenticated_indicators = (
            "职位描述",
            "岗位职责",
            "任职要求",
            "公司介绍",
            "竞争力分析",
            "立即沟通",
            "立即聊",
            "已沟通",
            "继续沟通",
            "聊天",
            "消息",
            "沟通中",
            "发简历",
        )
        if any(text in body for text in authenticated_indicators):
            return False

        strong_prompts = (
            "请登录",
            "扫码登录",
            "密码登录",
            "验证码登录",
            "微信扫码",
            "登录BOSS直聘",
        )
        if not any(text in body for text in strong_prompts):
            return False

        try:
            return self.page.evaluate("""() => {
                const visible = el => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const ariaHidden = el.getAttribute('aria-hidden');
                    return style
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0'
                        && ariaHidden !== 'true'
                        && rect.width > 0
                        && rect.height > 0;
                };

                const selectors = [
                    'input[placeholder*="手机号"]',
                    'input[placeholder*="验证码"]',
                    'input[type="password"]',
                    '.qrcode-img',
                    'img[class*="qrcode"]',
                    '[class*="login-panel"]',
                    '[class*="login-modal"]',
                    '[class*="sign-form"]',
                    '[class*="user-sign"]',
                ];

                return selectors.some(sel =>
                    Array.from(document.querySelectorAll(sel)).some(visible)
                );
            }""")
        except Exception:
            # JS 检测失败时, body 又已出现强登录提示,
            # 宁可保守视为"仍在登录页"返回 True, 避免拿未登录态去操作业务接口.
            return True

    def is_logged_in_page(self):
        """当前页面是否能作为已登录态使用；about:blank 属于未知，不当作过期。"""
        try:
            url = self.page.url
        except Exception:
            return False
        if url == "about:blank":
            return True
        return not self._login_prompt_visible()

    def login(self):
        self.page.goto("https://www.zhipin.com/web/user/?ka=header-login")
        pause(2, 4)
        self.page.bring_to_front()
        print("\n🔓 浏览器已打开，请扫码登录")
        last = self.page.url
        logged_in = False
        for i in range(600):
            time.sleep(1)
            try:
                url = self.page.evaluate("window.location.href")
            except:
                continue
            if (
                any(p in url for p in ["/web/geek", "/web/geek/chat", "/job_detail"])
                and not self._login_prompt_visible()
            ):
                print("✅ 登录成功")
                logged_in = True
                break
            last = url
            if i > 0 and i % 30 == 0:
                print("  ⏳ %ds" % i)
        if not logged_in:
            raise TimeoutError("扫码登录超时或未确认进入已登录页面")
        state = self._ctx.storage_state()
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        print("✅ 登录状态已保存")

        # 预热：导航到聊天页验证 session 稳定性，确保 token 生效
        try:
            self.page.goto("https://www.zhipin.com/web/geek/chat", wait_until="load", timeout=30000)
            pause(3, 5)
            if not self._login_prompt_visible():
                print("✅ 会话预热成功")
            else:
                print("⚠️ 预热时仍检测到登录提示，可能需要手动刷新页面")
        except Exception as e:
            print(f"⚠️ 会话预热失败: {e}")

    # ── 搜索列表页 ──

    def _wait_for_jobs_loaded(self, min_count: int = 10, max_wait_s: int = 15) -> int:
        """等待 BOSS 岗位卡片 DOM 加载完成。

        替代固定的 pause(3,5)，通过轮询卡片计数判断是否加载完。
        返回当前卡片数（0 表示超时未加载）。
        """
        start = time.time()
        last_count = -1
        stable = 0
        count = 0
        while time.time() - start < max_wait_s:
            try:
                count = self.page.locator('a[href*="/job_detail/"]').count()
            except Exception:
                count = 0
            if count >= min_count and count == last_count:
                stable += 1
                if stable >= 3:
                    return count
            else:
                stable = 0
                last_count = count
            time.sleep(0.5)
        return count

    def search(
        self,
        keyword,
        city_code="100010000",
        job_type: str = "",       # "1"=全职, "2"=兼职, 或 "1901"/"1903" 直传; ''=不限
        salary: str = "",         # BOSS 薪资 code 直传, 如 "405"=10-20K
        salary_min: Optional[int] = None,  # K 单位 (旧接口兼容)
        salary_max: Optional[int] = None,
        experience: Optional[int] = None,  # 经验 code
        edu: Optional[int] = None,         # 学历 code (内部参数名, URL 映射为 degree)
        scale: Optional[int] = None,      # 公司规模 code
        stage: Optional[int] = None,      # 融资阶段 code
        area_business: Optional[str] = None,  # 区县筛选: 逗号分隔的区名或 6 位区 code
    ):
        """搜索关键词，返回岗位列表。

        BOSS 直聘搜索 URL 参数 (经多个 GitHub 爬虫项目验证):
        - query=     搜索关键词
        - city=      城市 code
        - jobType=   岗位类型 (1901=全职, 1903=兼职, 注意: 是4位code, 不是1/2/3)
        - salary=    薪资 (BOSS code: 402=3K以下, 403=3-5K, 404=5-10K, 405=10-20K, 406=20-50K, 407=50K以上)
        - degree=    学历 (202=大专, 203=本科, 204=硕士, 205=博士, 206=高中, 208=中专, 209=初中及以下)
        - experience= 经验 (102=应届, 103=1年内, 104=1-3年, 105=3-5年, 106=5-10年, 107=10年以上, 108=在校)
        - scale=     公司规模 (301=0-20, 302=20-99, 303=100-499, 304=500-999, 305=1000-9999, 306=10000以上)
        - stage=     融资阶段 (801=未融资, 802=天使轮, 803=A轮, 804=B轮, 805=C轮, 806=D轮及以上, 807=已上市, 808=不需要融资)
        """
        t0 = time.time()

        # 薪资参数: 优先用前端直传的 salary code, 其次用 salary_min/max 转换
        if not salary and salary_min is not None and salary_max is not None:
            salary_code_map = {3: "403", 5: "404", 10: "405", 20: "406", 50: "407"}
            for k, code in sorted(salary_code_map.items(), reverse=True):
                if salary_min <= k:
                    salary = code
                    break
            else:
                salary = "403"

        params = {
            "query": keyword,
            "city": city_code,
        }
        # jobType: BOSS URL 用 4 位 code (1901=全职, 1902=实习, 1903=兼职)
        # 前端语义 "1"/"2"/"3" → 1901/1902/1903; 也支持 4 位 code 直传
        # 证据: CareerAI/Job-Hunting-Agent/OpenLOA 等多个 GitHub 项目 + 实测(jobType=1902 返回 ~75% 实习岗)
        if job_type:
            _type_code_map = {"full": "1901", "part": "1903", "practice": "1902",
                              "1": "1901", "2": "1903", "3": "1902",
                              "1901": "1901", "1902": "1902", "1903": "1903"}
            params["jobType"] = _type_code_map.get(job_type, job_type)
        if salary:
            params["salary"] = salary
        if experience is not None:
            params["experience"] = str(experience)
        # BOSS URL 参数名是 degree, 不是 edu (多个 GitHub 爬虫项目验证)
        if edu is not None:
            params["degree"] = str(edu)
        if scale is not None:
            params["scale"] = str(scale)
        if stage is not None:
            params["stage"] = str(stage)

        # 区县筛选: 前端传逗号分隔的区名或 6 位区 code, 解析后拼 multiBusinessDistrict 重复参数
        district_codes: list[str] = []
        if area_business:
            for item in (a.strip() for a in str(area_business).split(",") if a.strip()):
                if item.isdigit() and len(item) >= 6:
                    code = item
                else:
                    try:
                        from boss_geo import resolve_district_code
                    except Exception:
                        resolve_district_code = None
                    code = (resolve_district_code(city_code, item) or "") if resolve_district_code else ""
                if code and code not in district_codes:
                    district_codes.append(code)

        qparams = list(params.items())
        for dc in district_codes:
            qparams.append(("multiBusinessDistrict", dc))
        url = "https://www.zhipin.com/web/geek/job?" + urlencode(qparams)
        log.info(f"搜索URL: {url}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        loaded_count = self._wait_for_jobs_loaded(min_count=10, max_wait_s=15)
        t_wait = time.time() - t0
        self._scroll_all()
        t_scroll = time.time() - t0

        dom_jobs = self._extract_job_cards()
        if dom_jobs:
            return dom_jobs

        lines = [l.strip() for l in self.page.inner_text("body").split("\n") if l.strip()]

        # 薪资行定位
        sal_idx = [i for i, l in enumerate(lines) if re.search(r"\d+[-~]\d+K", decode_salary(l), re.I)]

        jobs = []
        for n, si in enumerate(sal_idx):
            if n > 0 and si - sal_idx[n - 1] < 3:
                continue
            if si == 0:
                continue
            title = lines[si - 1]
            if not (2 < len(title) < 60):
                continue

            salary = decode_salary(lines[si])
            company = exp = edu = city = ""
            end = sal_idx[n + 1] if n + 1 < len(sal_idx) else min(si + 10, len(lines))
            for j in range(si + 1, min(end, len(lines))):
                ln = lines[j]
                if "经验" in ln or "应届" in ln:
                    exp = ln
                elif re.search(r"本科|硕士|博士|大专|学历不限", ln):
                    edu = ln
                elif "·" in ln and len(ln) < 30:
                    city = ln
                elif (
                    not company
                    and len(ln) > 2
                    and len(ln) < 40
                    and not re.search(r"年|学历|大专|本科|硕士|博士|不限|应届|·", ln)
                ):
                    company = ln

            jobs.append(
                {
                    "title": title,
                    "salary": salary,
                    "company": company,
                    "experience": exp,
                    "education": edu,
                    "city": city,
                    "url": "",
                    "description": "",
                    "hr_name": "",
                    "hr_title": "",
                }
            )

        # 合并链接
        links = self._extract_links()
        if links:
            lm = {l["title"][:12]: l["href"] for l in links if l["title"][:12]}
            for j in jobs:
                if not j["url"] and j["title"][:12] in lm:
                    j["url"] = lm[j["title"][:12]]
        elapsed = time.time() - t0
        print(f"[search] keyword={keyword} city={city_code} "
              f"loaded={loaded_count} wait={t_wait:.1f}s "
              f"scroll={t_scroll - t_wait:.1f}s total={elapsed:.1f}s "
              f"found={len(jobs)} via={'dom' if dom_jobs else 'text'}")
        if elapsed < 1.5:
            print(f"[search] WARN: search finished in {elapsed:.1f}s, may have missed jobs")
        return jobs

    def _filter_by_welfare(self, jobs, welfare_keywords):
        """福利筛选：AND逻辑，所有关键词都必须匹配。"""
        if not welfare_keywords:
            return jobs
        filtered = []
        for j in jobs:
            tags = " ".join(j.get("welfareList", []) or [])
            if not tags:
                tags = j.get("description", "") or ""
            if all(kw in tags for kw in welfare_keywords):
                filtered.append(j)
        return filtered

    def _extract_job_cards(self):
        """优先从岗位卡片 DOM 提取，避免正文行号变化导致链接和岗位错配。"""
        try:
            rows = self.page.evaluate("""() => {
                const pickText = (root, selectors) => {
                    for (const sel of selectors) {
                        const el = root.querySelector(sel);
                        const text = (el && el.innerText || '').trim();
                        if (text) return text;
                    }
                    return '';
                };
                const linesOf = (root) => (root.innerText || '')
                    .split('\\n')
                    .map(s => s.trim())
                    .filter(Boolean);
                const cards = [];
                const seen = new Set();
                document.querySelectorAll('a[href*="/job_detail/"]').forEach(a => {
                    const href = a.href || a.getAttribute('href') || '';
                    if (!href || seen.has(href)) return;
                    const card = a.closest('.job-card-wrapper, .job-card-body, .job-primary, li, .job-list-box, .search-job-result') || a;
                    const lines = linesOf(card);
                    let title = pickText(card, [
                        '.job-name', '.job-title', '.job-card-left .job-name',
                        '[class*="job-name"]', '[class*="job-title"]'
                    ]) || (a.innerText || '').trim().split('\\n')[0] || lines[0] || '';
                    let salary = pickText(card, ['.salary', '.red', '[class*="salary"]'])
                        || lines.find(x => /\\d+[-~]\\d+K/i.test(x)) || '';
                    let company = pickText(card, [
                        '.company-name', '.brand-name', '.company-text',
                        '[class*="company-name"]', '[class*="brand-name"]'
                    ]);
                    let city = pickText(card, ['.job-area', '[class*="job-area"]'])
                        || lines.find(x => x.includes('·') && x.length < 40) || '';
                    let experience = lines.find(x => /经验|应届|在校|不限/.test(x) && x.length < 30) || '';
                    let education = lines.find(x => /本科|硕士|博士|大专|学历不限|中专|高中/.test(x) && x.length < 30) || '';
                    // CHANGES §3 §4: 抓 companyId / brandName / hrActive
                    let companyId = '';
                    let brandName = '';
                    let hrActive = '';
                    // 1) 公司块 href 一般是 /gongsi/<id>.html
                    const companyLink = card.querySelector('a[href*="/gongsi/"]');
                    if (companyLink) {
                        const chref = companyLink.getAttribute('href') || '';
                        const cm = chref.match(/\\/gongsi\\/([a-zA-Z0-9_\\-]+)\\.html/);
                        if (cm) companyId = cm[1];
                        // brandName 优先用 brand-name 元素文本, 否则 companyLink 文本
                        brandName = pickText(companyLink, ['.brand-name', '.company-name'])
                                  || (companyLink.innerText || '').trim().split('\\n')[0] || '';
                    }
                    // 2) HR 活跃度: 在卡片里找含"活跃"二字的元素
                    const activeEl = card.querySelector('[class*="active"], [class*="active-time"], [class*="online"]')
                                  || Array.from(card.querySelectorAll('span, em, i')).find(
                                      e => /活跃/.test((e.innerText || '').trim()) && (e.innerText || '').trim().length <= 12
                                  );
                    if (activeEl) {
                        hrActive = (activeEl.innerText || '').trim();
                    }
                    if (!company) {
                        company = brandName || lines.find(x =>
                            x !== title && x !== salary && x !== city &&
                            !/经验|应届|在校|不限|本科|硕士|博士|大专|学历|·|\\d+[-~]\\d+K/i.test(x) &&
                            x.length > 1 && x.length < 40
                        ) || '';
                    }
                    title = title.replace(/\\s+/g, ' ').trim();
                    if (title && salary) {
                        seen.add(href);
                        cards.push({
                            title, salary, company, companyId, brandName, hrActive,
                            city, experience, education, url: href
                        });
                    }
                });
                return cards;
            }""")
        except Exception:
            return []

        jobs = []
        seen = set()
        for row in rows or []:
            url = (row.get("url") or "").strip()
            title = decode_salary((row.get("title") or "").strip())
            if not url or not title or url in seen:
                continue
            seen.add(url)
            jobs.append(
                {
                    "title": title,
                    "salary": decode_salary((row.get("salary") or "").strip()),
                    "company": (row.get("company") or "").strip(),
                    "company_id": (row.get("companyId") or "").strip(),
                    "brand_name": (row.get("brandName") or "").strip(),
                    "hr_active_label": (row.get("hrActive") or "").strip(),
                    "experience": (row.get("experience") or "").strip(),
                    "education": (row.get("education") or "").strip(),
                    "city": (row.get("city") or "").strip(),
                    "url": url,
                    "description": "",
                    "hr_name": "",
                    "hr_title": "",
                }
            )
        # 二次解析: 文本 -> 天数
        for j in jobs:
            if j.get("hr_active_label"):
                parsed = parse_hr_active(j["hr_active_label"])
                j["hr_active_days"] = parsed["days"]
            else:
                j["hr_active_days"] = -1
        return jobs

    def _scroll_all(self, max_scrolls: int = 80, stable_rounds: int = 4):
        """持续滚动直到没有新岗位卡片加载，或达到最大滚动次数。

        改用卡片计数代替 scrollHeight 判断：
        BOSS 是 virtual scroll + lazy load，scrollHeight 在加载完成前可能不变，
        导致提前退出只拿到部分结果。
        """
        try:
            last_count = 0
            stable_count = 0
            for _ in range(max_scrolls):
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(0.8, 1.5))
                cur_count = self.page.locator('a[href*="/job_detail/"]').count()
                if cur_count == last_count and cur_count > 0:
                    stable_count += 1
                    if stable_count >= stable_rounds:
                        break
                else:
                    stable_count = 0
                    last_count = cur_count
            # 滚回顶部，确保 DOM 完整渲染
            self.page.evaluate("window.scrollTo(0,0)")
            time.sleep(random.uniform(0.3, 0.5))
        except:
            pass

    def _extract_links(self):
        try:
            return self.page.evaluate("""()=>{
                const r=[];const s=new Set();
                document.querySelectorAll('a[href*="/job_detail/"]').forEach(a=>{
                    const h=a.href,t=(a.innerText||'').trim();
                    if(h&&t&&!s.has(h)&&t.length<60){s.add(h);r.push({href:h,title:t.substring(0,60)});}
                });return r;
            }""")
        except:
            return []

    # ── 详情页 ──

    def fetch_detail(self, url):
        """访问详情页，提取岗位描述 + HR/招聘者信息 + 活跃度"""
        result = {"description": "", "hr_name": "", "hr_title": "", "hr_active_label": "", "hr_active_days": -1}
        try:
            self.page.goto(url, wait_until="load", timeout=45000)
            pause(2, 4)

            # ── 提取招聘者信息 ──
            try:
                hr_info = self.page.evaluate("""() => {
                    const body = document.body.innerText || '';
                    const lines = body.split('\\n').map(l => l.trim()).filter(Boolean);
                    let hrName = '', hrTitle = '';
                    for (let i = 0; i < lines.length; i++) {
                        const l = lines[i];
                        // BOSS直聘招聘者区域: 通常 "HR" "招聘者" "经理" 等标识
                        if (l.includes('HR') || l.includes('招聘者') || l.includes('招聘经理') ||
                            l.includes('人事') || l.includes('HRBP') || l.includes('猎头')) {
                            // 上一行或当前行可能是名字
                            if (i > 0 && lines[i-1].length <= 6 && !/\\d|省|市|区|路|号|招聘|公司|BOSS/.test(lines[i-1])) {
                                hrName = lines[i-1];
                            }
                            hrTitle = l;
                            break;
                        }
                    }
                    // 也尝试用选择器找招聘者信息区域
                    const bossSelectors = [
                        '.boss-info-attr', '.boss-info', '.recruiter-info',
                        '.boss-name', '.recruiter-name', '[class*="boss"]',
                    ];
                    for (const sel of bossSelectors) {
                        const el = document.querySelector(sel);
                        if (el && el.innerText.trim()) {
                            const t = el.innerText.trim();
                            if (t.length <= 15) {
                                if (!hrName) hrName = t;
                                break;
                            }
                        }
                    }
                    // CHANGES §4: 详情页抓 HR 活跃度
                    let hrActive = '';
                    const activeSel = document.querySelector('[class*="active-time"], [class*="boss-active"]');
                    if (activeSel) {
                        hrActive = (activeSel.innerText || '').trim();
                    } else {
                        for (const el of document.querySelectorAll('span, em, i')) {
                            const t = (el.innerText || '').trim();
                            if (/活跃/.test(t) && t.length <= 12) { hrActive = t; break; }
                        }
                    }
                    return {hrName, hrTitle, hrActive};
                }""")
                result["hr_name"] = (hr_info.get("hrName") or "").strip()
                result["hr_title"] = (hr_info.get("hrTitle") or "").strip()
                result["hr_active_label"] = (hr_info.get("hrActive") or "").strip()
            except:
                pass

            # 二次解析: 文本 -> 天数
            if result.get("hr_active_label"):
                parsed = parse_hr_active(result["hr_active_label"])
                result["hr_active_days"] = parsed["days"]
            else:
                # 从 body 文本兜底找一遍
                for l in lines:
                    if "活跃" in l and len(l) <= 12:
                        result["hr_active_label"] = l
                        parsed = parse_hr_active(l)
                        result["hr_active_days"] = parsed["days"]
                        break

            # ── 提取岗位描述 ──
            body = self.page.inner_text("body")
            lines = [l.strip() for l in body.split("\n") if l.strip()]

            skill_lines = []
            capture = False
            for l in lines:
                if "职位描述" in l or "岗位职责" in l:
                    capture = True
                    continue
                if capture:
                    if any(
                        stop in l
                        for stop in [
                            "公司介绍",
                            "工商信息",
                            "BOSS 安全提示",
                            "竞争力分析",
                        ]
                    ):
                        break
                    skill_lines.append(l)
            result["description"] = "\n".join(skill_lines) if skill_lines else ""

            # 如果 JS 没抓到招聘者信息，从文本中尝试解析
            if not result["hr_name"]:
                for i, l in enumerate(lines):
                    if l in ("HR", "招聘者", "招聘经理", "HRBP", "人事", "猎头"):
                        if i > 0 and len(lines[i - 1]) <= 6:
                            result["hr_name"] = lines[i - 1]
                            result["hr_title"] = l
                            break

        except Exception:
            pass
        return result


# ══════════════════════════════════════
#  分析
# ══════════════════════════════════════


def skill_gap(jobs):
    c = Counter()
    for j in jobs:
        text = (j.get("description") or "") + " " + (j.get("title") or "")
        seen = set()
        for cat, skills in parse_skills(text).items():
            for s in skills:
                if s.lower() not in seen:
                    seen.add(s.lower())
                    c[s] += 1
    have, miss = [], []
    for s, n in c.most_common():
        (have if s.lower() in MY_SKILLS else miss).append({"skill": s, "count": n})
    return {"have": have, "missing": miss, "total": len(jobs)}


# ══════════════════════════════════════
#  输出
# ══════════════════════════════════════


def output_report(jobs):
    lines = ["# 招聘日报 · %s\n" % DATE_STR]
    lines.append("> 来源：**BOSS直聘** · 无薪资限制 · 共 %d 条\n---\n" % len(jobs))

    for i, j in enumerate(jobs, 1):
        lines.append("### %d. %s %s" % (i, j["title"], j["salary"]))
        lines.append("- 公司: %s" % (j.get("company") or "未显示"))
        if j.get("city"):
            lines.append("- 城市: %s" % j["city"])
        if j.get("experience"):
            lines.append("- 经验: %s" % j["experience"])
        if j.get("education"):
            lines.append("- 学历: %s" % j["education"])
        if j.get("hr_name"):
            lines.append("- 👤 招聘者: %s (%s)" % (j["hr_name"], j.get("hr_title") or ""))
        if j.get("url"):
            lines.append("- 链接: %s" % j["url"])
        desc = j.get("description", "")
        if desc:
            lines.append("- 岗位技能：%s" % desc[:600])
        lines.append("---\n")
    lines.append("\n*数据采集于 %s，BOSS直聘*\n" % DATE_STR)
    return "\n".join(lines)


def skill_report(gap):
    lines = ["# AI Agent 技能差距分析报告 · %s\n" % DATE_STR]
    lines.append("> 基于 BOSS 直聘 %d 个岗位\n---\n" % gap["total"])
    lines.append("## 一、✅ 你已拥有的技能\n")
    for item in gap["have"]:
        lines.append("- **%s**: %d个岗位" % (item["skill"], item["count"]))
    lines.append("\n## 二、🔍 需要查漏补缺\n")
    for item in gap["missing"][:30]:
        p = "🔴" if item["count"] >= 10 else "🟡" if item["count"] >= 5 else "🟢"
        lines.append("- %s **%s**: %d个岗位" % (p, item["skill"], item["count"]))
    return "\n".join(lines)


# ══════════════════════════════════════
#  主流程
# ══════════════════════════════════════


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--headless", action="store_true", default=False)
    ap.add_argument("--keywords")
    ap.add_argument("--output", default=str(OUTPUT_DIR))
    ap.add_argument("--max-jobs", type=int, default=64)
    args = ap.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else KEYWORDS

    if not STATE_FILE.exists() and not args.login:
        print("⚠️ 请先运行: python3 boss_firefox.py --login")
        sys.exit(1)

    sc = BossScraper(headless=args.headless)
    sc.start()
    try:
        if args.login:
            sc.login()
            return

        # Phase 1: 搜索列表（关键词 × 城市）
        all_jobs = []
        seen = set()
        for city_name, city_code in CITIES.items():
            for kw in keywords:
                if len(all_jobs) >= args.max_jobs:
                    break
                print("\n📌 搜索: 「%s」@ %s" % (kw, city_name))
                try:
                    jobs = sc.search(kw, city_code)
                except Exception as e:
                    print("  ⚠️ 失败: %s" % e)
                    continue
                ok = []
                for j in jobs:
                    key = j["title"] + j["salary"] + j.get("company", "")
                    if key not in seen:
                        seen.add(key)
                        j["city"] = city_name  # 标记城市
                        ok.append(j)
                print("  %d条, 去重后%d条(累计%d)" % (len(jobs), len(ok), len(all_jobs)))
                all_jobs.extend(ok)
                if len(all_jobs) >= args.max_jobs:
                    print("  📊 已达上限%d条" % args.max_jobs)
                    break
                pause(2, 4)
            if len(all_jobs) >= args.max_jobs:
                break

        print("\n📊 共%d条" % len(all_jobs))
        if not all_jobs:
            return

        # Phase 2: 逐个访问详情页，提取岗位技能 + 招聘者信息
        print("\n🔍 开始采集岗位详情（共%d条）..." % len(all_jobs))
        success = 0
        for i, j in enumerate(all_jobs):
            if not j.get("url"):
                continue
            print(
                "  [%d/%d] %s" % (i + 1, len(all_jobs), j["title"][:25]),
                end=" ",
                flush=True,
            )
            detail = sc.fetch_detail(j["url"])
            if detail["description"]:
                j["description"] = detail["description"]
                success += 1
            j["hr_name"] = detail.get("hr_name", "")
            j["hr_title"] = detail.get("hr_title", "")
            if detail["description"]:
                print("✅ %d字 | HR: %s" % (len(detail["description"]), j["hr_name"] or "未识别"))
            else:
                print("⚠️ 无描述 | HR: %s" % (j["hr_name"] or "未识别"))
            time.sleep(random.uniform(1.5, 3.0))

        print("📊 详情采集: %d/%d条成功" % (success, len(all_jobs)))

        # 分析输出到终端即可
        gap = skill_gap(all_jobs)
        print("\n" + "=" * 60)
        print("📊 技能差距分析")
        print("=" * 60)
        for item in gap["have"][:10]:
            print("  ✅ %s: %d个岗位" % (item["skill"], item["count"]))
        for item in gap["missing"][:15]:
            p = "🔴" if item["count"] >= 10 else "🟡" if item["count"] >= 5 else "🟢"
            print("  %s %s: %d个岗位" % (p, item["skill"], item["count"]))

        # 输出——招聘日报 + CSV 数据文件
        with open(out_dir / ("招聘日报_%s.md" % DATE_STR), "w", encoding="utf-8") as f:
            f.write(output_report(all_jobs))
        print("📄 日报: %s/招聘日报_%s.md" % (out_dir, DATE_STR))

        # CSV 格式，方便 Excel 打开
        csv_path = out_dir / ("招聘数据_%s.csv" % DATE_STR)
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "title",
                    "company",
                    "salary",
                    "city",
                    "experience",
                    "education",
                    "hr_name",
                    "hr_title",
                    "url",
                    "description",
                ],
            )
            writer.writeheader()
            for j in all_jobs:
                writer.writerow({k: j.get(k, "") for k in writer.fieldnames})
        print("📊 数据: %s/招聘数据_%s.csv" % (out_dir, DATE_STR))
        print("\n✅ 完成！")

    finally:
        sc.close()


if __name__ == "__main__":
    main()
