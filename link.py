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

# 🔄 动态初始化配置：有链接才启用，没有则禁用
q_img_url = get_secret("quark", "img_url")
b_img_url = get_secret("baidu", "img_url")

FIXED_IMAGE_CONFIG = {
    "quark": {
        "url": q_img_url,
        "enabled": bool(q_img_url and q_img_url.strip()) # 只有链接不为空才启用
    },
    "baidu": {
        "url": b_img_url,
        "pwd": get_secret("baidu", "img_pwd"),
        "name": get_secret("baidu", "img_name", "公众号关注.jpg"),
        "enabled": bool(b_img_url and b_img_url.strip()) # 只有链接不为空才启用
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
        """🧹 自动清理超过 24 小时的旧任务"""
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
            timestamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")
            self.jobs[job_id]["logs"].append({"time": timestamp, "msg": message, "type": type})

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
# 2. 页面配置与样式
# ==========================================
st.set_page_config(
    page_title="网盘转存助手",
    page_icon="📂",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ⚓️ 顶部锚点
st.markdown('<div id="top-anchor" style="position:absolute; top:-50px; visibility:hidden;"></div>', unsafe_allow_html=True)

st.markdown("""
    <style>
    .block-container { padding-top: 32px !important; padding-bottom: 3rem; }
    .stTextArea textarea { font-family: 'Source Code Pro', monospace; font-size: 14px; }
    .success-text { color: #09ab3b; font-weight: bold; }
    .stStatusWidget { border: 1px solid #e0e0e0; border-radius: 8px; }
    
    /* 日志样式美化 */
    .log-container {
        font-family: 'Menlo', 'Consolas', monospace;
        font-size: 13px;
        line-height: 1.6;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }
    .log-item {
        display: flex;
        align-items: flex-start;
        padding: 4px 8px;
        border-radius: 4px;
        background: #f8f9fa;
        border-left: 3px solid #dee2e6;
    }
    .log-time {
        color: #999;
        margin-right: 10px;
        min-width: 65px;
        font-weight: bold;
    }
    .log-msg {
        color: #333;
        word-break: break-all;
    }
    
    /* 不同类型的日志配色 */
    .log-type-success { border-left-color: #09ab3b; background: #f0fdf4; }
    .log-type-error { border-left-color: #ff4b4b; background: #fff1f0; }
    .log-type-quark { border-left-color: #0088ff; background: #e6f7ff; }
    .log-type-baidu { border-left-color: #ff4d4f; background: #fff1f0; }
    .log-type-info { border-left-color: #888; background: #f8f9fa; }
    
    .result-box { border: 2px solid #e6f4ea; padding: 15px; border-radius: 10px; background-color: #f9fdfa; margin-top: 20px; }
    .running-badge { color: #0088ff; font-weight: bold; animation: pulse 1.5s infinite; }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
    </style>
""", unsafe_allow_html=True)

INVALID_CHARS_REGEX = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9_\-\s]')

def get_beijing_time_str():
    utc_now = datetime.now(timezone.utc)
    beijing_now = utc_now + timedelta(hours=8)
    return beijing_now.strftime("%H:%M:%S")

def get_time_diff(start_time):
    diff = time.time() - start_time
    return f"{diff:.2f}s"

def create_copy_button_html(text_to_copy: str):
    safe_text = json.dumps(text_to_copy)[1:-1]
    return f"""
    <div style="margin-top: 10px;">
        <button id="copyBtn" style="width:100%;padding:12px;cursor:pointer;background:#ffffff;border:1px solid #d6d6d6;border-radius:8px;font-weight:600;color:#31333F;transition:all 0.2s;" 
        onclick="navigator.clipboard.writeText('{safe_text}').then(()=>{{let b=document.getElementById('copyBtn');b.innerText='✅ 已复制全部结果';b.style.color='#09ab3b';b.style.borderColor='#09ab3b';setTimeout(()=>{{b.innerText='📋 一键复制结果';b.style.color='#31333F';b.style.borderColor='#d6d6d6'}}, 2000)}})">
        📋 一键复制结果
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

# 推送函数
def send_notification(bark_key, pushdeer_key, title, body):
    if bark_key:
        encoded_title = quote(title)
        encoded_body = quote(body)
        url = f"https://api.day.app/{bark_key}/{encoded_title}/{encoded_body}?icon=https://cdn-icons-png.flaticon.com/512/2991/2991110.png"
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
                if errno == -33: err_msg = "文件数超出限制(非会员500)"
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
# 5. 核心：后台线程 Worker (执行实际转存)
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
                    job_manager.add_log(job_id, "❌ 夸克：未配置Cookie，跳过", "error")
                else:
                    job_manager.add_log(job_id, "--- ☁️ **开始处理夸克链接** ---", "quark")
                    t0 = time.time()
                    user = await q_engine.check_login()
                    if not user: 
                        job_manager.add_log(job_id, f"❌ 登录失败 ({get_time_diff(t0)})", "error")
                    else:
                        job_manager.add_log(job_id, f"✅ 登录成功: {user} ({get_time_diff(t0)})", "success")
                        t0 = time.time()
                        root_fid = await q_engine.get_folder_id(QUARK_SAVE_PATH)
                        if not root_fid: 
                            job_manager.add_log(job_id, f"❌ 目录不存在 ({get_time_diff(t0)})", "error")
                        else:
                            for match in q_matches:
                                current_idx += 1
                                raw_url = match.group(1)
                                job_manager.add_log(job_id, f"🔄 [{current_idx}/{total_tasks}] 处理: `{raw_url}`", "quark")
                                job_manager.update_progress(job_id, current_idx, total_tasks)
                                
                                t_task = time.time()
                                new_url, msg, new_fid = await q_engine.process_url(raw_url, root_fid)
                                t_task_end = get_time_diff(t_task)
                                
                                if new_url:
                                    log_msg = f"✅ 成功 ({t_task_end})"
                                    if FIXED_IMAGE_CONFIG['quark']['enabled'] and new_fid:
                                        t_img = time.time()
                                        res_url, res_msg, _ = await q_engine.process_url(FIXED_IMAGE_CONFIG['quark']['url'], new_fid, is_inject=True)
                                        if res_url == "INJECT_OK": log_msg += f" + 图片 ({get_time_diff(t_img)})"
                                        else: log_msg += f" (图片失败: {res_msg})"
                                    
                                    job_manager.add_log(job_id, f"{log_msg}", "success")
                                    final_text = final_text.replace(raw_url, new_url)
                                    success_count += 1
                                else:
                                    job_manager.add_log(job_id, f"{msg} ({t_task_end})", "error")

                                await asyncio.sleep(random.uniform(2, 4))

            # --- 百度 ---
            if b_matches:
                if not baidu_cookie: 
                    job_manager.add_log(job_id, "❌ 百度：未配置Cookie，跳过", "error")
                else:
                    job_manager.add_log(job_id, "--- 🐻 **开始处理百度链接** ---", "baidu")
                    t0 = time.time()
                    if not b_engine.init_token(): 
                        job_manager.add_log(job_id, f"❌ 登录失败 ({get_time_diff(t0)})", "error")
                    else:
                        job_manager.add_log(job_id, f"✅ 登录成功 ({get_time_diff(t0)})", "success")
                        if not b_engine.check_dir_exists(BAIDU_SAVE_PATH): b_engine.create_dir(BAIDU_SAVE_PATH)
                        
                        for match in b_matches:
                            current_idx += 1
                            raw_url = match.group(1)
                            pwd_match = re.search(r'(?:\?pwd=|&pwd=|\s+|提取码[:：]?\s*)([a-zA-Z0-9]{4})', match.group(0))
                            pwd = pwd_match.group(1) if pwd_match else ""
                            name = extract_smart_folder_name(input_text, match.start())
                            
                            job_manager.add_log(job_id, f"🔄 [{current_idx}/{total_tasks}] 处理: `{name}`", "baidu")
                            job_manager.update_progress(job_id, current_idx, total_tasks)
                            
                            t_task = time.time()
                            new_url, msg, new_dir_path = b_engine.process_url({'url': raw_url, 'pwd': pwd, 'name': name}, BAIDU_SAVE_PATH)
                            t_task_end = get_time_diff(t_task)
                            
                            if new_url:
                                log_msg = f"✅ 成功 ({t_task_end})"
                                if FIXED_IMAGE_CONFIG['baidu']['enabled'] and new_dir_path:
                                    t_img = time.time()
                                    img_res_url, img_msg, _ = b_engine.process_url({'url': FIXED_IMAGE_CONFIG['baidu']['url'], 'pwd': FIXED_IMAGE_CONFIG['baidu']['pwd']}, new_dir_path, is_inject=True)
                                    if img_res_url == "INJECT_OK": log_msg += f" + 图片 ({get_time_diff(t_img)})"
                                    else: log_msg += f" (图片失败: {img_msg})"

                                job_manager.add_log(job_id, f"{log_msg}", "success")
                                final_text = final_text.replace(raw_url, new_url)
                                success_count += 1
                            else:
                                job_manager.add_log(job_id, f"{msg} ({t_task_end})", "error")

                            time.sleep(random.uniform(2, 4))

        finally:
            if q_engine: await q_engine.close()
            duration_obj = datetime.now() - start_time
            duration_str = str(duration_obj)[:-4] if len(str(duration_obj)) > 4 else str(duration_obj)
            summary = {"success": success_count, "total": total_tasks, "duration": str(duration_obj)}
            job_manager.complete_job(job_id, final_text, summary)
            
            # 🆕 任务结束，发送推送
            if bark_key or pushdeer_key:
                body_msg = f"成功: {success_count}/{total_tasks} | 耗时: {duration_str}"
                title_msg = "✅ 转存完成" if success_count > 0 else "❌ 转存结束(无成功)"
                send_notification(bark_key, pushdeer_key, title_msg, body_msg)

    asyncio.run(async_worker())

# ==========================================
# 6. 主逻辑 (前端 UI)
# ==========================================
def check_password():
    """🔒 密码校验逻辑"""
    if "general" not in st.secrets or "app_password" not in st.secrets["general"]:
        return True # 如果没配置密码，直接放行

    TARGET_PWD = st.secrets["general"]["app_password"]
    
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
    # 🔒 安全检查
    if not check_password():
        return

    st.title("网盘转存助手")
    
    # 🆕 从 Secrets 读取推送 Key
    bark_key = get_secret("general", "bark_key")
    pushdeer_key = get_secret("general", "pushdeer_key")
    
    # 🆕 从 Secrets 读取 Cookie
    q_c = get_secret("quark", "cookie")
    b_c = get_secret("baidu", "cookie")

    with st.sidebar:
        st.header("⚙️ 账号状态")
        if q_c: st.success(f"☁️ 夸克已配置")
        else: st.error("☁️ 夸克未配置")
        
        # 🆕 图片植入状态
        if FIXED_IMAGE_CONFIG['quark']['enabled']:
            st.success("🖼️ 夸克图片植入已启用")
        else:
            st.caption("⚪ 夸克图片植入未配置")
        
        if b_c: st.success(f"🐻 百度已配置")
        else: st.error("🐻 百度未配置")
        
        # 🆕 图片植入状态
        if FIXED_IMAGE_CONFIG['baidu']['enabled']:
            st.success("🖼️ 百度图片植入已启用")
        else:
            st.caption("⚪ 百度图片植入未配置")
        
        st.divider()
        if bark_key or pushdeer_key:
            st.info(f"📢 推送已启用")
        else:
            st.caption("🔕 推送未配置")

    # 1️⃣ 检查是否有正在运行的任务
    query_params = st.query_params
    current_job_id = query_params.get("job_id", None)

    if not current_job_id:
        st.info("💡 提示：点击开始后，**你可以关闭网页或切到后台**。重新打开此页面即可查看进度。")
        input_text = st.text_area("📝 请在此处粘贴链接文本...", height=200, key="link_input")
        
        if st.button("🚀 开始后台转存", type="primary", use_container_width=True):
            if not input_text.strip():
                st.toast("请输入内容", icon="⚠️"); return
            
            new_job_id = job_manager.create_job()
            
            # 🆕 传入配置好的 Keys
            t = threading.Thread(target=worker_thread, args=(new_job_id, input_text, q_c, b_c, bark_key, pushdeer_key))
            t.start()
            
            st.query_params["job_id"] = new_job_id
            st.rerun()

    # 2️⃣ 监控面板
    else:
        job_data = job_manager.get_job(current_job_id)
        
        if not job_data:
            st.error("❌ 任务不存在或已过期 (服务器重启会导致任务丢失)")
            if st.button("🔙 返回首页"):
                st.query_params.clear()
                st.rerun()
        else:
            status = job_data['status']
            
            if status == "running":
                st.markdown(f"### 🔄 正在后台运行中... <span class='running-badge'>RUNNING</span>", unsafe_allow_html=True)
                st.caption(f"任务ID: `{current_job_id}` (你可以随时关闭此页面，回来查看结果)")
            else:
                st.markdown("### ✅ 任务已完成")

            prog = job_data['progress']
            if prog['total'] > 0:
                st.progress(prog['current'] / prog['total'], text=f"进度: {prog['current']} / {prog['total']}")

            with st.expander("📜 实时日志 (自动刷新)", expanded=True):
                # 🆕 使用美化后的日志容器
                st.markdown('<div class="log-container">', unsafe_allow_html=True)
                # 直接遍历，不反转，实现从上往下打印 (先出来的在上面)
                for log in job_data['logs']:
                    log_cls = f"log-type-{log['type']}"
                    st.markdown(f"""
                    <div class="log-item {log_cls}">
                        <div class="log-time">{log['time']}</div>
                        <div class="log-msg">{log['msg']}</div>
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
                    <h3>✨ 处理统计</h3>
                    <p>成功: <b>{summary.get('success', 0)}</b> / {summary.get('total', 0)} 
                    &nbsp;|&nbsp; 耗时: {safe_duration}</p>
                </div>
                """, unsafe_allow_html=True)
                
                st.text_area("⬇️ 最终结果", value=res_text, height=250)
                components.html(create_copy_button_html(res_text), height=80)
                
                if st.button("🗑️ 开始新任务", use_container_width=True):
                    st.query_params.clear()
                    st.rerun()
            else:
                time.sleep(2) 
                st.rerun()

# 悬浮球
st.markdown("""
    <style>
    .back-to-top {
        position: fixed;
        bottom: 120px;
        right: 20px;
        width: 50px;
        height: 50px;
        background-color: #FF4B4B;
        border-radius: 50%;
        box-shadow: 2px 2px 10px rgba(0,0,0,0.3);
        z-index: 999999;
        text-decoration: none;
        transition: all 0.3s ease;
        opacity: 0.85;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .back-to-top:hover {
        background-color: #D33030;
        opacity: 1;
        transform: scale(1.1);
    }
    .back-to-top svg { width: 28px; height: 28px; stroke: white; }
    </style>
    <a href="#top-anchor" class="back-to-top" title="回到顶部">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 10.5 12 3m0 0 7.5 7.5M12 3v18" />
        </svg>
    </a>
""", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
