import streamlit as st
import streamlit.components.v1 as components
import httpx
import requests
import asyncio
import re
import time
import random
import string
import json
import threading
import uuid
import html
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from typing import Union, List, Any
from retrying import retry

# ==========================================
# 0. 全局配置与 Secrets 读取
# ==========================================
def get_secret(section, key, default=""):
    try:
        if section in st.secrets:
            return st.secrets[section].get(key, default)
        flat_key = f"{section}_{key}".upper()
        if flat_key in st.secrets:
            return st.secrets[flat_key]
    except: pass
    return default

# 🔄 动态初始化配置
q_img_url = get_secret("quark", "img_url")
b_img_url = get_secret("baidu", "img_url")

FIXED_IMAGE_CONFIG = {
    "quark": {
        "url": q_img_url,
        "enabled": bool(q_img_url and q_img_url.strip())
    },
    "baidu": {
        "url": b_img_url,
        "pwd": get_secret("baidu", "img_pwd"),
        "name": get_secret("baidu", "img_name", "公众号关注.jpg"),
        "enabled": bool(b_img_url and b_img_url.strip())
    }
}

QUARK_SAVE_PATH = "来自：分享/LinkChanger"
BAIDU_SAVE_PATH = "/我的资源/LinkChanger"

# ==========================================
# 1. 核心：后台任务管理器
# ==========================================
@st.cache_resource
class JobManager:
    def __init__(self):
        self.jobs = {} 

    def _cleanup_old_jobs(self):
        now = datetime.now()
        expired_ids = [jid for jid, job in self.jobs.items() 
                       if (now - job['created_at']).total_seconds() > 86400]
        for jid in expired_ids:
            del self.jobs[jid]

    def create_job(self):
        self._cleanup_old_jobs()
        job_id = str(uuid.uuid4())[:8]
        self.jobs[job_id] = {
            "status": "running",
            "logs": [],
            "result_text": "",
            "progress": {"current": 0, "total": 0},
            "created_at": datetime.now(),
            "summary": {}
        }
        return job_id

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_log(self, job_id, message, type="info"):
        """type: info, success, error, quark, baidu"""
        if job_id in self.jobs:
            # 获取东八区时间
            timestamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")
            safe_message = html.escape(message)
            self.jobs[job_id]["logs"].append({"time": timestamp, "msg": safe_message, "type": type})

    def update_progress(self, job_id, current, total):
        if job_id in self.jobs:
            self.jobs[job_id]["progress"] = {"current": current, "total": total}

    def complete_job(self, job_id, final_text, summary):
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = "done"
            self.jobs[job_id]["result_text"] = final_text
            self.jobs[job_id]["summary"] = summary

job_manager = JobManager()

# ==========================================
# 2. 页面配置与样式 (重点优化部分)
# ==========================================
st.set_page_config(
    page_title="网盘转存助手",
    page_icon="📂",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown('<div id="top-anchor" style="position:absolute; top:-50px; visibility:hidden;"></div>', unsafe_allow_html=True)

st.markdown("""
    <style>
    /* 基础容器微调 */
    .block-container { padding-top: 32px !important; padding-bottom: 3rem; }
    .stTextArea textarea { font-family: 'Source Code Pro', monospace; font-size: 14px; border-radius: 8px; }
    
    /* 日志容器优化 */
    .log-container {
        font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
        font-size: 12px;
        display: flex;
        flex-direction: column;
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        padding: 0;
        background: #fafafa;
        overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    
    /* 单条日志 */
    .log-item {
        display: flex;
        align-items: flex-start; /* 顶部对齐 */
        padding: 8px 12px;
        border-bottom: 1px solid #f0f0f0;
        line-height: 1.6;
        transition: background 0.2s;
    }
    .log-item:hover { background: #f0f7ff; }
    .log-item:last-child { border-bottom: none; }
    
    /* 时间列 */
    .log-time {
        color: #999;
        font-size: 11px;
        margin-right: 12px;
        min-width: 58px;
        text-align: right;
        flex-shrink: 0;
        padding-top: 1px;
    }
    
    /* 消息主体 */
    .log-msg {
        color: #333;
        flex-grow: 1;
        word-wrap: break-word; /* 允许换行 */
        min-width: 0; /* 防止flex子元素溢出 */
    }
    
    /* 智能链接缺省样式 */
    .smart-link {
        display: inline-block;
        background: #e6f7ff;
        color: #1890ff;
        padding: 0 4px;
        border-radius: 3px;
        font-family: monospace;
        border: 1px solid #bae7ff;
        max-width: 180px; /* 移动端最大宽度 */
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis; /* 超出显示省略号 */
        vertical-align: bottom;
        font-size: 11px;
        cursor: text;
    }
    
    /* 进度标记 */
    .step-badge {
        display: inline-block;
        background: #f0f0f0;
        color: #666;
        padding: 0 4px;
        border-radius: 3px;
        margin-right: 5px;
        font-size: 10px;
        font-weight: bold;
    }
    
    /* 耗时标记 */
    .time-badge {
        color: #8c8c8c;
        font-size: 10px;
        margin-left: 5px;
    }

    /* 图标颜色 */
    .icon-success { color: #52c41a; font-weight:bold; margin-right: 4px; }
    .icon-error { color: #ff4d4f; font-weight:bold; margin-right: 4px; }
    .icon-quark { color: #1677ff; font-weight:bold; margin-right: 4px; }
    .icon-baidu { color: #ff4d4f; font-weight:bold; margin-right: 4px; }
    .icon-info { color: #8c8c8c; font-weight:bold; margin-right: 4px; }

    /* 结果区域 */
    .result-box { 
        background: #fff; 
        border: 1px solid #b7eb8f; 
        padding: 15px; 
        border-radius: 8px; 
        margin-top: 20px; 
        margin-bottom: 25px; 
        background-color: #f6ffed;
    }
    
    .running-badge { color: #0088ff; font-weight: bold; animation: pulse 1.5s infinite; }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }
    .status-dot-green { display:inline-block; width:8px; height:8px; background:#52c41a; border-radius:50%; margin-right:6px; }
    .status-dot-red { display:inline-block; width:8px; height:8px; background:#ff4d4f; border-radius:50%; margin-right:6px; }
    .status-dot-gray { display:inline-block; width:8px; height:8px; background:#d9d9d9; border-radius:50%; margin-right:6px; }
    </style>
""", unsafe_allow_html=True)

INVALID_CHARS_REGEX = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9_\-\s]')

def get_time_diff(start_time):
    diff = time.time() - start_time
    return f"{diff:.2f}s"

# 智能缩短链接用于展示
def smart_shorten_url(text):
    # 正则查找URL
    url_pattern = re.compile(r'(https?://[^\s]+)')
    
    def replace_func(match):
        url = match.group(1)
        # 提取域名和末尾关键字符
        try:
            domain = url.split('/')[2]
            if "quark" in domain: domain = "夸克"
            elif "baidu" in domain: domain = "百度"
            
            # 保留链接的最后8位用于识别
            suffix = url[-8:] if len(url) > 20 else url[-5:]
            short_text = f"{domain}...{suffix}"
            return f'<span class="smart-link" title="{url}">{short_text}</span>'
        except:
            return f'<span class="smart-link" title="{url}">链接...</span>'

    return url_pattern.sub(replace_func, text)

def create_copy_button_html(text_to_copy: str):
    safe_text = json.dumps(text_to_copy)[1:-1]
    return f"""
    <div style="margin-top: 10px;">
        <button id="copyBtn" style="width:100%;padding:10px;cursor:pointer;background:#fff;border:1px solid #e0e0e0;border-radius:6px;font-weight:500;color:#333;transition:all 0.2s;" 
        onclick="navigator.clipboard.writeText('{safe_text}').then(()=>{{let b=document.getElementById('copyBtn');b.innerText='✅ 已复制';b.style.color='#52c41a';setTimeout(()=>{{b.innerText='📋 复制结果';b.style.color='#333'}}, 2000)}})">
        📋 复制结果
        </button>
    </div>
    """

def sanitize_filename(name: str) -> str:
    if not name: return ""
    name = re.sub(r'[【】\[\]()]', ' ', name)
    clean_name = INVALID_CHARS_REGEX.sub('', name)
    return re.sub(r'\s+', ' ', clean_name).strip()

def extract_smart_folder_name(full_text: str, match_start: int) -> str:
    lookback_limit = max(0, match_start - 200)
    pre_text = full_text[lookback_limit:match_start]
    lines = pre_text.splitlines()
    candidate_name = ""
    for line in reversed(lines):
        clean_line = line.strip()
        if not clean_line: continue
        if re.match(r'^(百度|链接|提取码|:|：|https?|夸克|pwd|code)*$', clean_line, re.IGNORECASE):
            continue
        clean_line = re.sub(r'(百度|链接|提取码|:|：|pwd|夸克).*$', '', clean_line, flags=re.IGNORECASE).strip()
        if clean_line:
            candidate_name = clean_line
            break
    final_name = sanitize_filename(candidate_name)
    if not final_name or len(final_name) < 2:
        return f"Res_{int(time.time())}" 
    return final_name[:50]

def send_notification(bark_key, pushdeer_key, title, body):
    if bark_key:
        url = f"https://api.day.app/{bark_key}/{quote(title)}/{quote(body)}?icon=https://cdn-icons-png.flaticon.com/512/2991/2991110.png"
        try: requests.get(url, timeout=5)
        except: pass
    if pushdeer_key:
        url = "https://api2.pushdeer.com/message/push"
        params = {"pushkey": pushdeer_key, "text": title, "desp": body, "type": "markdown"}
        try: requests.get(url, params=params, timeout=5)
        except: pass

# ==========================================
# 3. 引擎类 (夸克 & 百度)
# ==========================================
class QuarkEngine:
    def __init__(self, cookies: str):
        self.headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'cookie': cookies,
            'origin': 'https://pan.quark.cn',
            'referer': 'https://pan.quark.cn/',
        }
        self.client = httpx.AsyncClient(timeout=45.0, headers=self.headers, follow_redirects=True)

    async def close(self):
        await self.client.aclose()

    def _params(self):
        return {'pr': 'ucpro', 'fr': 'pc', '__dt': random.randint(100, 9999), '__t': int(time.time() * 1000)}

    async def check_login(self):
        try:
            r = await self.client.get('https://pan.quark.cn/account/info', params=self._params())
            data = r.json()
            if (data.get('code') == 0 or data.get('code') == 'OK') and data.get('data'):
                return data['data'].get('nickname', '用户')
        except: pass
        return None

    async def get_folder_id(self, path: str):
        parts = path.split('/')
        curr_id = '0'
        for part in parts:
            if not part: continue
            found = False
            params = self._params()
            params.update({'pdir_fid': curr_id, '_page': 1, '_size': 50, '_fetch_total': 'false', '_sort': 'file_type:asc,updated_at:desc'})
            try:
                r = await self.client.get('https://drive-pc.quark.cn/1/clouddrive/file/sort', params=params)
                for item in r.json().get('data', {}).get('list', []):
                    if item['file_name'] == part and item['dir']:
                        curr_id = item['fid']
                        found = True
                        break
            except: pass
            if not found: return None 
        return curr_id

    async def process_url(self, url: str, target_fid: str, is_inject: bool = False):
        try:
            if '/s/' not in url: return None, "格式错误", None
            pwd_id = url.split('/s/')[-1].split('?')[0].split('#')[0]
            match = re.search(r'[?&]pwd=([a-zA-Z0-9]+)', url)
            passcode = match.group(1) if match else ""
        except: return None, "解析异常", None

        try:
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token", 
                                     json={"pwd_id": pwd_id, "passcode": passcode}, params=self._params())
            stoken = r.json().get('data', {}).get('stoken')
            if not stoken: return None, "提取码失效", None
        except: return None, "Token请求失败", None

        params = self._params()
        params.update({"pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "_page": 1, "_size": 50})
        try:
            r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail", params=params)
            items = r.json().get('data', {}).get('list', [])
            if not items: return None, "空分享", None
            source_fids = [i['fid'] for i in items]
            source_tokens = [i['share_fid_token'] for i in items]
            first_name = items[0]['file_name']
        except: return None, "获取详情失败", None

        save_data = {"fid_list": source_fids, "fid_token_list": source_tokens, "to_pdir_fid": target_fid, 
                     "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "scene": "link"}
        try:
            r = await self.client.post("https://drive.quark.cn/1/clouddrive/share/sharepage/save", json=save_data, params=self._params())
            if r.json().get('code') not in [0, 'OK']: return None, f"转存失败: {r.json().get('message')}", None
            task_id = r.json().get('data', {}).get('task_id')
        except: return None, "转存请求失败", None

        if is_inject: return "INJECT_OK", "植入成功", None

        for _ in range(8):
            await asyncio.sleep(1)
            try:
                params = self._params()
                params['task_id'] = task_id
                r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/task", params=params)
                if r.json().get('data', {}).get('status') == 2: break
            except: pass

        await asyncio.sleep(1.5)
        new_fid = None
        params = self._params()
        params.update({'pdir_fid': target_fid, '_page': 1, '_size': 20, '_sort': 'updated_at:desc'})
        try:
            r = await self.client.get('https://drive-pc.quark.cn/1/clouddrive/file/sort', params=params)
            for item in r.json().get('data', {}).get('list', []):
                if item['file_name'] == first_name: 
                    new_fid = item['fid']; break
            if not new_fid and r.json().get('data', {}).get('list'):
                new_fid = r.json()['data']['list'][0]['fid']
        except: pass
        
        if not new_fid: return None, "✅ 已存入网盘 (但无法获取文件ID，未分享)", None

        share_data = {"fid_list": [new_fid], "title": first_name, "url_type": 1, "expired_type": 1}
        try:
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share", json=share_data, params=self._params())
            res = r.json()
            if res.get('code') != 0 and res.get('code') != 'OK':
                return None, f"✅ 已存入网盘 (但分享被拦截: {res.get('message')})", None
                
            share_task_id = res.get('data', {}).get('task_id')
            await asyncio.sleep(0.5)
            params = self._params()
            params.update({'task_id': share_task_id, 'retry_index': 0})
            r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/task", params=params)
            share_id = r.json().get('data', {}).get('share_id')
            
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share/password", json={"share_id": share_id}, params=self._params())
            return r.json()['data']['share_url'], "成功", new_fid
        except: return None, "✅ 已存入网盘 (但分享创建异常)", None

class BaiduEngine:
    def __init__(self, cookies: str):
        self.s = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
            'Referer': 'https://pan.baidu.com',
            'Cookie': "".join(cookies.split())
        }
        self.bdstoken = ''
        requests.packages.urllib3.disable_warnings()

    def update_cookie_bdclnd(self, bdclnd):
        current = dict(i.split('=', 1) for i in self.headers['Cookie'].split(';') if '=' in i)
        current['BDCLND'] = bdclnd
        self.headers['Cookie'] = ';'.join([f'{k}={v}' for k,v in current.items()])

    @retry(stop_max_attempt_number=2)
    def init_token(self):
        url = 'https://pan.baidu.com/api/gettemplatevariable'
        r = self.s.get(url, params={'fields': '["bdstoken","token","uk","isdocuser"]'}, headers=self.headers, verify=False)
        if r.json().get('errno') == 0:
            self.bdstoken = r.json()['result']['bdstoken']
            return True
        return False

    def check_dir_exists(self, path):
        if not path.startswith("/"): path = "/" + path
        try:
            r = self.s.get('https://pan.baidu.com/api/list', params={'dir': path, 'bdstoken': self.bdstoken, 'start': 0, 'limit': 1}, headers=self.headers, verify=False)
            return r.json().get('errno') == 0
        except: return False

    def create_dir(self, path):
        if not path.startswith("/"): path = "/" + path
        try:
            self.s.post('https://pan.baidu.com/api/create', params={'a': 'commit', 'bdstoken': self.bdstoken}, 
                        data={'path': path, 'isdir': 1, 'block_list': '[]'}, headers=self.headers, verify=False)
        except: pass

    def process_url(self, url_info: dict, root_path: str, is_inject: bool = False):
        url = url_info['url']
        pwd = url_info['pwd']
        clean_url = url.split('?')[0]
        folder_name = url_info.get('name', 'Temp')

        try:
            # 1. Verify
            if pwd:
                surl = re.search(r'(?:surl=|/s/1|/s/)([\w\-]+)', clean_url)
                if not surl: return None, "URL格式错误", None
                r = self.s.post('https://pan.baidu.com/share/verify', 
                                params={'surl': surl.group(1), 't': int(time.time()*1000), 'bdstoken': self.bdstoken, 'channel': 'chunlei', 'web': 1, 'clienttype': 0},
                                data={'pwd': pwd, 'vcode': '', 'vcode_str': ''}, headers=self.headers, verify=False)
                if r.json()['errno'] == 0:
                    self.update_cookie_bdclnd(r.json()['randsk'])
                else:
                    return None, "提取码错误", None

            # 2. Get FSID
            content = self.s.get(clean_url, headers=self.headers, verify=False).text
            try:
                shareid = re.search(r'"shareid":(\d+?),', content).group(1)
                uk = re.search(r'"share_uk":"(\d+?)",', content).group(1)
                fs_id_list = re.findall(r'"fs_id":(\d+?),', content)
                if not fs_id_list: return None, "无文件", None
            except: return None, "页面解析失败", None

            # 3. Path
            if is_inject:
                save_path = root_path
            else:
                safe_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
                final_folder = f"{folder_name}_{safe_suffix}"
                save_path = f"{root_path}/{final_folder}"
                self.create_dir(save_path) 

            # 4. Transfer
            try:
                r = self.s.post('https://pan.baidu.com/share/transfer', 
                                params={'shareid': shareid, 'from': uk, 'bdstoken': self.bdstoken},
                                data={'fsidlist': f"[{','.join(fs_id_list)}]", 'path': save_path}, 
                                headers=self.headers, verify=False, timeout=20)
                res = r.json()
            except requests.exceptions.RequestException:
                return None, "转存请求超时(文件可能过大)", None

            if res.get('errno') == 12: 
                 if is_inject: return "INJECT_OK", "文件已存在", save_path
                 return None, "转存失败(文件已存在)", None
            
            if res.get('errno') != 0: 
                errno = res.get('errno')
                err_msg = f"转存失败({errno})"
                if errno == -10: err_msg = "容量不足或文件数超限"
                elif errno == -33: err_msg = "文件数超出限制(非会员500)"
                elif errno == 4: err_msg = "文件路径无效或包含违规内容(errno:4)"
                return None, err_msg, None

            if is_inject: return "INJECT_OK", "成功", save_path

            # 5. Share
            r = self.s.get('https://pan.baidu.com/api/list', params={'dir': root_path, 'bdstoken': self.bdstoken}, headers=self.headers, verify=False)
            target_fsid = None
            for item in r.json().get('list', []):
                if item['server_filename'] == final_folder:
                    target_fsid = item['fs_id']; break
            
            if not target_fsid: return None, "✅ 已存入网盘 (获取目录失败)", None

            new_pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
            r = self.s.post('https://pan.baidu.com/share/set', 
                            params={'bdstoken': self.bdstoken, 'channel': 'chunlei', 'clienttype': 0, 'web': 1},
                            data={'period': 0, 'pwd': new_pwd, 'fid_list': f'[{target_fsid}]', 'schannel': 4}, headers=self.headers, verify=False)
            
            if r.json()['errno'] == 0:
                return f"{r.json()['link']}?pwd={new_pwd}", "成功", save_path 
            return None, "✅ 已存入网盘 (分享失败)", None

        except Exception as e:
            return None, f"发生异常: {str(e)[:20]}...", None

# ==========================================
# 5. 核心：后台线程 Worker
# ==========================================
def worker_thread(job_id, input_text, quark_cookie, baidu_cookie, bark_key, pushdeer_key):
    
    async def async_worker():
        start_time = datetime.now()
        final_text = input_text
        success_count = 0
        current_idx = 0
        
        quark_regex = re.compile(r'(https://pan\.quark\.cn/s/[a-zA-Z0-9]+(?:\?pwd=[a-zA-Z0-9]+)?)')
        baidu_regex = re.compile(r'(https?://pan\.baidu\.com/s/[a-zA-Z0-9_\-]+(?:\?pwd=[a-zA-Z0-9]+)?)')
        q_matches = list(quark_regex.finditer(input_text))
        b_matches = list(baidu_regex.finditer(input_text))
        total_tasks = len(q_matches) + len(b_matches)
        
        job_manager.update_progress(job_id, 0, total_tasks)
        
        q_engine = QuarkEngine(quark_cookie) if q_matches else None
        b_engine = BaiduEngine(baidu_cookie) if b_matches else None

        try:
            # --- 夸克 ---
            if q_matches:
                if not quark_cookie: 
                    job_manager.add_log(job_id, "夸克：未配置Cookie，跳过", "error")
                else:
                    job_manager.add_log(job_id, "开始处理夸克链接...", "quark")
                    t0 = time.time()
                    user = await q_engine.check_login()
                    if not user: 
                        job_manager.add_log(job_id, f"登录失败 (耗时: {get_time_diff(t0)})", "error")
                    else:
                        job_manager.add_log(job_id, f"登录成功: {user} (耗时: {get_time_diff(t0)})", "success")
                        t_root = time.time()
                        root_fid = await q_engine.get_folder_id(QUARK_SAVE_PATH)
                        if not root_fid: 
                            job_manager.add_log(job_id, f"目录不存在，尝试创建 (耗时: {get_time_diff(t_root)})", "error")
                        else:
                            for match in q_matches:
                                current_idx += 1
                                raw_url = match.group(1)
                                step_prefix = f"[{current_idx}/{total_tasks}]"
                                
                                job_manager.add_log(job_id, f"{step_prefix} 处理中: {raw_url}", "quark")
                                job_manager.update_progress(job_id, current_idx, total_tasks)
                                
                                t_task = time.time()
                                new_url, msg, new_fid = await q_engine.process_url(raw_url, root_fid)
                                t_task_end = get_time_diff(t_task)
                                
                                if new_url:
                                    log_msg = f"{step_prefix} 转存成功: {new_url} (耗时: {t_task_end})"
                                    if FIXED_IMAGE_CONFIG['quark']['enabled'] and new_fid:
                                        t_img = time.time()
                                        res_url, res_msg, _ = await q_engine.process_url(FIXED_IMAGE_CONFIG['quark']['url'], new_fid, is_inject=True)
                                        if res_url == "INJECT_OK": log_msg += f" + 植入(耗时:{get_time_diff(t_img)})"
                                    
                                    job_manager.add_log(job_id, log_msg, "success")
                                    final_text = final_text.replace(raw_url, new_url)
                                    success_count += 1
                                else:
                                    job_manager.add_log(job_id, f"{step_prefix} {msg} (耗时: {t_task_end})", "error")

                                await asyncio.sleep(random.uniform(2, 4))

            # --- 百度 ---
            if b_matches:
                if not baidu_cookie: 
                    job_manager.add_log(job_id, "百度：未配置Cookie，跳过", "error")
                else:
                    job_manager.add_log(job_id, "开始处理百度链接...", "baidu")
                    t0 = time.time()
                    if not b_engine.init_token(): 
                        job_manager.add_log(job_id, f"登录失败 (耗时: {get_time_diff(t0)})", "error")
                    else:
                        job_manager.add_log(job_id, f"登录成功 (耗时: {get_time_diff(t0)})", "success")
                        if not b_engine.check_dir_exists(BAIDU_SAVE_PATH): b_engine.create_dir(BAIDU_SAVE_PATH)
                        
                        for match in b_matches:
                            current_idx += 1
                            raw_url = match.group(1)
                            pwd_match = re.search(r'(?:\?pwd=|&pwd=|\s+|提取码[:：]?\s*)([a-zA-Z0-9]{4})', match.group(0))
                            pwd = pwd_match.group(1) if pwd_match else ""
                            step_prefix = f"[{current_idx}/{total_tasks}]"
                            
                            job_manager.add_log(job_id, f"{step_prefix} 处理中: {raw_url}", "baidu")
                            job_manager.update_progress(job_id, current_idx, total_tasks)
                            
                            t_task = time.time()
                            name = extract_smart_folder_name(input_text, match.start())
                            new_url, msg, new_dir_path = b_engine.process_url({'url': raw_url, 'pwd': pwd, 'name': name}, BAIDU_SAVE_PATH)
                            t_task_end = get_time_diff(t_task)
                            
                            if new_url:
                                log_msg = f"{step_prefix} 转存成功: {new_url} (耗时: {t_task_end})"
                                if FIXED_IMAGE_CONFIG['baidu']['enabled'] and new_dir_path:
                                    t_img = time.time()
                                    img_res_url, img_msg, _ = b_engine.process_url({'url': FIXED_IMAGE_CONFIG['baidu']['url'], 'pwd': FIXED_IMAGE_CONFIG['baidu']['pwd']}, new_dir_path, is_inject=True)
                                    if img_res_url == "INJECT_OK": log_msg += f" + 植入(耗时:{get_time_diff(t_img)})"

                                job_manager.add_log(job_id, log_msg, "success")
                                final_text = final_text.replace(raw_url, new_url)
                                success_count += 1
                            else:
                                job_manager.add_log(job_id, f"{step_prefix} {msg} (耗时: {t_task_end})", "error")

                            time.sleep(random.uniform(2, 4))

        finally:
            if q_engine: await q_engine.close()
            duration_obj = datetime.now() - start_time
            duration_str = str(duration_obj)[:-4] if len(str(duration_obj)) > 4 else str(duration_obj)
            summary = {"success": success_count, "total": total_tasks, "duration": str(duration_obj)}
            job_manager.complete_job(job_id, final_text, summary)
            
            if bark_key or pushdeer_key:
                body_msg = f"成功: {success_count}/{total_tasks} | 耗时: {duration_str}"
                title_msg = "✅ 转存完成" if success_count > 0 else "❌ 转存结束(无成功)"
                send_notification(bark_key, pushdeer_key, title_msg, body_msg)

    asyncio.run(async_worker())

# ==========================================
# 6. 主逻辑 (前端 UI)
# ==========================================
@st.cache_data(ttl=300) 
def check_cookies_validity(q_c, b_c):
    status = {"quark": False, "baidu": False}
    
    # 夸克检测 (使用 requests 同步检测)
    if q_c:
        try:
            headers = {
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'cookie': q_c,
                'referer': 'https://pan.quark.cn/'
            }
            params = {'pr': 'ucpro', 'fr': 'pc', '__dt': random.randint(100, 9999)}
            r = requests.get('https://pan.quark.cn/account/info', headers=headers, params=params, timeout=5)
            data = r.json()
            if (data.get('code') == 0 or data.get('code') == 'OK') and data.get('data'):
                status["quark"] = True
        except: pass
        
    # 百度检测
    if b_c:
        try:
            b_eng = BaiduEngine(b_c)
            if b_eng.init_token(): status["baidu"] = True
        except: pass
        
    return status

def check_password():
    """🔒 密码校验逻辑 (支持为空免密)"""
    TARGET_PWD = get_secret("general", "app_password", "")

    if not TARGET_PWD or not TARGET_PWD.strip():
        return True

    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if not st.session_state.password_correct:
        st.title("🔒 访问受限")
        pwd = st.text_input("请输入访问密码", type="password")
        if st.button("解锁"):
            if pwd == TARGET_PWD:
                st.session_state.password_correct = True
                st.rerun()
            else:
                st.error("密码错误")
        return False
    return True

def main():
    if not check_password():
        return

    st.title("网盘转存助手")
    
    bark_key = get_secret("general", "bark_key")
    pushdeer_key = get_secret("general", "pushdeer_key")
    q_c = get_secret("quark", "cookie")
    b_c = get_secret("baidu", "cookie")

    # 🟡 自动检测 Cookie 有效性
    cookie_status = check_cookies_validity(q_c, b_c)

    with st.sidebar:
        st.header("⚙️ 状态监控")
        
        if not q_c:
            st.markdown('<span class="status-dot-gray"></span> 夸克: 未配置', unsafe_allow_html=True)
        elif cookie_status["quark"]:
            st.markdown('<span class="status-dot-green"></span> 夸克: <span style="color:#52c41a">有效</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="status-dot-red"></span> 夸克: <span style="color:#ff4d4f">已失效</span>', unsafe_allow_html=True)
            
        if not b_c:
            st.markdown('<span class="status-dot-gray"></span> 百度: 未配置', unsafe_allow_html=True)
        elif cookie_status["baidu"]:
            st.markdown('<span class="status-dot-green"></span> 百度: <span style="color:#52c41a">有效</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="status-dot-red"></span> 百度: <span style="color:#ff4d4f">已失效</span>', unsafe_allow_html=True)

        st.divider()
        
        if FIXED_IMAGE_CONFIG['quark']['enabled']:
            st.success("🖼️ 夸克植入: 开启")
        else:
            st.caption("⚪ 夸克植入: 关闭")
        
        if FIXED_IMAGE_CONFIG['baidu']['enabled']:
            st.success("🖼️ 百度植入: 开启")
        else:
            st.caption("⚪ 百度植入: 关闭")
        
        if bark_key or pushdeer_key:
            st.info("📢 消息推送: 开启")

    query_params = st.query_params
    current_job_id = query_params.get("job_id", None)

    if not current_job_id:
        st.info("💡 提示：后台自动运行，任务开始后可关闭网页。")
        input_text = st.text_area("📝 粘贴链接...", height=150, key="link_input")
        
        if st.button("🚀 开始转存", type="primary", use_container_width=True):
            if not input_text.strip():
                st.toast("请输入内容", icon="⚠️"); return
            
            if not cookie_status["quark"] and not cookie_status["baidu"]:
                 st.error("❌ 所有账号 Cookie 均已失效，请更新 Secrets 后重试。")
                 return

            new_job_id = job_manager.create_job()
            
            t = threading.Thread(target=worker_thread, args=(new_job_id, input_text, q_c, b_c, bark_key, pushdeer_key))
            t.start()
            
            st.query_params["job_id"] = new_job_id
            st.rerun()

    else:
        job_data = job_manager.get_job(current_job_id)
        
        if not job_data:
            st.error("❌ 任务不存在或已过期")
            if st.button("🔙 返回"):
                st.query_params.clear()
                st.rerun()
        else:
            status = job_data['status']
            
            if status == "running":
                st.markdown(f"### 🔄 运行中... <span class='running-badge'>RUNNING</span>", unsafe_allow_html=True)
                st.caption(f"ID: `{current_job_id}`")
            else:
                st.markdown("### ✅ 已完成")

            prog = job_data['progress']
            if prog['total'] > 0:
                st.progress(prog['current'] / prog['total'], text=f"进度: {prog['current']} / {prog['total']}")

            with st.expander("📜 执行日志", expanded=True):
                st.markdown('<div class="log-container">', unsafe_allow_html=True)
                for log in job_data['logs']:
                    # 图标逻辑
                    icon = "🔹"
                    if log['type'] == 'success': icon = '<span class="icon-success">✔</span>'
                    elif log['type'] == 'error': icon = '<span class="icon-error">✖</span>'
                    elif log['type'] == 'quark': icon = '<span class="icon-quark">☁</span>'
                    elif log['type'] == 'baidu': icon = '<span class="icon-baidu">🐻</span>'
                    
                    # 消息格式化：高亮进度与时间
                    msg_display = log['msg']
                    
                    # 替换进度 [1/10] 为徽章样式
                    msg_display = re.sub(r'(\[\d+/\d+\])', r'<span class="step-badge">\1</span>', msg_display)
                    # 替换耗时 (耗时: x.xxs) 为灰色小字
                    msg_display = re.sub(r'(\(耗时:.*?\))', r'<span class="time-badge">\1</span>', msg_display)
                    
                    # 智能缩短链接（防止手机端换行）
                    msg_display = smart_shorten_url(msg_display)

                    st.markdown(f"""
                    <div class="log-item">
                        <div class="log-time">{log['time']}</div>
                        <div class="log-msg">{icon} {msg_display}</div>
                    </div>
                    """, unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

            if status == "done":
                res_text = job_data['result_text']
                summary = job_data['summary']
                
                duration_str = str(summary.get('duration', '0s'))
                safe_duration = duration_str[:-4] if len(duration_str) > 4 else duration_str

                st.markdown(f"""
                <div class="result-box">
                    <p style="margin:0;color:#389e0d;font-weight:bold;font-size:16px;">
                        🎉 处理完成
                    </p>
                    <p style="margin-top:8px;color:#666;font-size:14px;">
                        成功: <b style="color:#52c41a">{summary.get('success', 0)}</b> / {summary.get('total', 0)} 
                        &nbsp;|&nbsp; ⏱ 总耗时: {safe_duration}
                    </p>
                </div>
                """, unsafe_allow_html=True)
                
                st.text_area("⬇️ 最终结果 (可直接复制)", value=res_text, height=200)
                components.html(create_copy_button_html(res_text), height=80)
                
                if st.button("🗑️ 开始新任务", use_container_width=True):
                    st.query_params.clear()
                    st.rerun()
            else:
                time.sleep(2) 
                st.rerun()

st.markdown("""
    <style>
    .back-to-top {
        position: fixed;
        bottom: 80px;
        right: 20px;
        width: 40px;
        height: 40px;
        background-color: #333;
        border-radius: 50%;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        z-index: 999999;
        text-decoration: none;
        display: flex;
        align-items: center;
        justify-content: center;
        opacity: 0.6;
        transition: opacity 0.3s;
    }
    .back-to-top:hover { opacity: 1; }
    .back-to-top svg { width: 20px; height: 20px; stroke: white; }
    </style>
    <a href="#top-anchor" class="back-to-top" title="Top">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 10.5 12 3m0 0 7.5 7.5M12 3v18" />
        </svg>
    </a>
""", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
