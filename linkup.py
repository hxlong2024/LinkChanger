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
# 0. 全局配置与工具
# ==========================================
st.set_page_config(page_title="网盘转存助手", page_icon="📂", layout="wide")

st.markdown("""
    <style>
    .log-container {
        font-family: 'Menlo', monospace; font-size: 12px;
        border: 1px solid #e0e0e0; border-radius: 8px;
        padding: 5px; background: #fafafa;
        max-height: 300px; overflow-y: auto;
    }
    .log-item { display: flex; align-items: flex-start; padding: 4px 0; border-bottom: 1px solid #f0f0f0; }
    .log-time { color: #999; font-size: 11px; margin-right: 8px; min-width: 55px; }
    .log-msg { color: #333; word-break: break-all; }
    .smart-link { background: #e6f7ff; color: #1890ff; padding: 0 4px; border-radius: 3px; font-size: 11px; }
    .status-dot-green { color:#52c41a; margin-right:4px; }
    .status-dot-gray { color:#d9d9d9; margin-right:4px; }
    </style>
""", unsafe_allow_html=True)

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
            "status": "running", "logs": [], "result_text": "",
            "progress": {"current": 0, "total": 0},
            "created_at": datetime.now(), "summary": {}
        }
        return job_id

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_log(self, job_id, message, type="info"):
        if job_id in self.jobs:
            timestamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")
            self.jobs[job_id]["logs"].append({"time": timestamp, "msg": html.escape(message), "type": type})

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
# 2. 引擎类 (夸克 & 百度)
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
        self.inject_cache = None # 广告缓存

    async def close(self): await self.client.aclose()
    def _params(self): return {'pr': 'ucpro', 'fr': 'pc', '__dt': random.randint(100, 9999), '__t': int(time.time() * 1000)}

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
                        curr_id = item['fid']; found = True; break
            except: pass
            if not found: return None 
        return curr_id

    async def process_url(self, url: str, target_fid: str, is_inject: bool = False):
        # 🚀 优化：有缓存则直接读取
        if is_inject and self.inject_cache:
            source_fids = self.inject_cache['fids']
            source_tokens = self.inject_cache['tokens']
            pwd_id = self.inject_cache['pwd_id']
            stoken = self.inject_cache['stoken']
        else:
            try:
                if '/s/' not in url: return None, "格式错误", None
                pwd_id = url.split('/s/')[-1].split('?')[0].split('#')[0]
                match = re.search(r'[?&]pwd=([a-zA-Z0-9]+)', url)
                passcode = match.group(1) if match else ""
                
                r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token", 
                                         json={"pwd_id": pwd_id, "passcode": passcode}, params=self._params())
                stoken = r.json().get('data', {}).get('stoken')
                if not stoken: return None, "提取码失效", None
                
                params = self._params()
                params.update({"pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "_page": 1, "_size": 50})
                r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail", params=params)
                items = r.json().get('data', {}).get('list', [])
                if not items: return None, "空分享", None
                source_fids = [i['fid'] for i in items]
                source_tokens = [i['share_fid_token'] for i in items]
                first_name = items[0]['file_name']

                # 🚀 写入缓存
                if is_inject:
                    self.inject_cache = {'fids': source_fids, 'tokens': source_tokens, 'pwd_id': pwd_id, 'stoken': stoken}
            except: return None, "解析异常", None

        try:
            save_data = {"fid_list": source_fids, "fid_token_list": source_tokens, "to_pdir_fid": target_fid, 
                         "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "scene": "link"}
            r = await self.client.post("https://drive.quark.cn/1/clouddrive/share/sharepage/save", json=save_data, params=self._params())
            if r.json().get('code') not in [0, 'OK']: return None, f"转存失败: {r.json().get('message')}", None
            task_id = r.json().get('data', {}).get('task_id')
        except: return None, "转存请求失败", None

        if is_inject: return "INJECT_OK", "植入成功", None

        for _ in range(8):
            await asyncio.sleep(1)
            try:
                params = self._params(); params['task_id'] = task_id
                r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/task", params=params)
                if r.json().get('data', {}).get('status') == 2: break
            except: pass

        await asyncio.sleep(1.5)
        new_fid = None
        params = self._params(); params.update({'pdir_fid': target_fid, '_page': 1, '_size': 20, '_sort': 'updated_at:desc'})
        try:
            r = await self.client.get('https://drive-pc.quark.cn/1/clouddrive/file/sort', params=params)
            for item in r.json().get('data', {}).get('list', []):
                if item['file_name'] == first_name: new_fid = item['fid']; break
            if not new_fid and r.json().get('data', {}).get('list'): new_fid = r.json()['data']['list'][0]['fid']
        except: pass
        
        if not new_fid: return None, "已存入但无法分享", None
        share_data = {"fid_list": [new_fid], "title": first_name, "url_type": 1, "expired_type": 1}
        try:
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share", json=share_data, params=self._params())
            share_task_id = r.json().get('data', {}).get('task_id')
            await asyncio.sleep(0.5)
            params = self._params(); params.update({'task_id': share_task_id, 'retry_index': 0})
            r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/task", params=params)
            share_id = r.json().get('data', {}).get('share_id')
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share/password", json={"share_id": share_id}, params=self._params())
            return r.json()['data']['share_url'], "成功", new_fid
        except: return None, "✅ 已存入网盘 (但分享创建异常)", None

class BaiduEngine:
    def __init__(self, cookies: str):
        self.s = requests.Session()
        self.headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://pan.baidu.com', 'Cookie': "".join(cookies.split())}
        self.bdstoken = ''
        self.inject_cache = None # 广告缓存
        requests.packages.urllib3.disable_warnings()

    def update_cookie(self, bdclnd):
        self.headers['Cookie'] += f'; BDCLND={bdclnd}'

    @retry(stop_max_attempt_number=2)
    def init_token(self):
        try:
            r = self.s.get('https://pan.baidu.com/api/gettemplatevariable', params={'fields': '["bdstoken"]'}, headers=self.headers, verify=False)
            if r.json().get('errno') == 0: self.bdstoken = r.json()['result']['bdstoken']; return True
        except: pass
        return False

    def check_dir_exists(self, path):
        try:
            r = self.s.get('https://pan.baidu.com/api/list', params={'dir': path, 'bdstoken': self.bdstoken, 'start': 0, 'limit': 1}, headers=self.headers, verify=False)
            return r.json().get('errno') == 0
        except: return False

    def create_dir(self, path):
        try:
            self.s.post('https://pan.baidu.com/api/create', params={'a': 'commit', 'bdstoken': self.bdstoken}, 
                        data={'path': path, 'isdir': 1, 'block_list': '[]'}, headers=self.headers, verify=False)
        except: pass

    def process_url(self, url_info: dict, root_path: str, is_inject: bool = False):
        # 🚀 优化：有缓存直接读取
        if is_inject and self.inject_cache:
            shareid = self.inject_cache['shareid']
            uk = self.inject_cache['uk']
            fs_id_list_str = self.inject_cache['fsidlist']
        else:
            try:
                url, pwd = url_info['url'], url_info['pwd']
                clean_url = url.split('?')[0]
                if pwd:
                    surl = re.search(r'(?:surl=|/s/1|/s/)([\w\-]+)', clean_url)
                    if not surl: return None, "URL格式错误", None
                    r = self.s.post('https://pan.baidu.com/share/verify', params={'surl': surl.group(1), 't': int(time.time()*1000), 'bdstoken': self.bdstoken, 'channel': 'chunlei', 'web': 1, 'clienttype': 0}, data={'pwd': pwd, 'vcode': '', 'vcode_str': ''}, headers=self.headers, verify=False)
                    if r.json()['errno'] == 0: self.update_cookie(r.json()['randsk'])
                    else: return None, "提取码错误", None

                content = self.s.get(clean_url, headers=self.headers, verify=False).text
                shareid = re.search(r'"shareid":(\d+?),', content).group(1)
                uk = re.search(r'"share_uk":"(\d+?)",', content).group(1)
                fs_id_list = re.findall(r'"fs_id":(\d+?),', content)
                if not fs_id_list: return None, "无文件", None
                fs_id_list_str = f"[{','.join(fs_id_list)}]"
                
                if is_inject:
                    self.inject_cache = {'shareid': shareid, 'uk': uk, 'fsidlist': fs_id_list_str}
            except Exception as e: return None, f"异常: {str(e)[:20]}", None

        try:
            if is_inject: save_path = root_path
            else:
                folder_name = url_info.get('name', 'Res')
                safe_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
                final_folder = f"{folder_name}_{safe_suffix}"
                save_path = f"{root_path}/{final_folder}"
                self.create_dir(save_path) 

            r = self.s.post('https://pan.baidu.com/share/transfer', params={'shareid': shareid, 'from': uk, 'bdstoken': self.bdstoken}, 
                            data={'fsidlist': fs_id_list_str, 'path': save_path}, headers=self.headers, verify=False, timeout=20)
            
            if r.json().get('errno') == 12 and is_inject: return "INJECT_OK", "已存在", save_path
            if r.json().get('errno') != 0: return None, f"转存失败({r.json().get('errno')})", None
            if is_inject: return "INJECT_OK", "成功", save_path

            # 分享
            r = self.s.get('https://pan.baidu.com/api/list', params={'dir': root_path, 'bdstoken': self.bdstoken}, headers=self.headers, verify=False)
            target_fsid = None
            for item in r.json().get('list', []):
                if item['server_filename'] == final_folder: target_fsid = item['fs_id']; break
            if not target_fsid: return None, "获取文件失败", None
            new_pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
            r = self.s.post('https://pan.baidu.com/share/set', params={'bdstoken': self.bdstoken, 'channel': 'chunlei', 'clienttype': 0, 'web': 1}, data={'period': 0, 'pwd': new_pwd, 'fid_list': f'[{target_fsid}]', 'schannel': 4}, headers=self.headers, verify=False)
            if r.json()['errno'] == 0: return f"{r.json()['link']}?pwd={new_pwd}", "成功", save_path 
            return None, "分享失败", None
        except Exception as e: return None, f"异常: {str(e)[:20]}", None

# ==========================================
# 3. Worker 线程 (已更新：接收用户动态配置)
# ==========================================
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

def smart_shorten_url(text):
    return re.sub(r'(https?://[^\s]+)', r'<span class="smart-link">LINK</span>', text)

def create_copy_button_html(text):
    safe_text = json.dumps(text)[1:-1]
    return f"""<button onclick="navigator.clipboard.writeText('{safe_text}')" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;background:#fff;cursor:pointer;">📋 点击复制结果</button>"""

def sanitize_filename(name):
    return re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9_\-\s]', '', name).strip()

def extract_smart_folder_name(full_text, match_start):
    # 简单的名字提取逻辑
    return f"Res_{int(time.time())}"

def worker_thread(job_id, input_text, quark_cookie, baidu_cookie, bark_key, pushdeer_key, inject_config):
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
                if not quark_cookie: job_manager.add_log(job_id, "夸克：未配置Cookie，跳过", "error")
                else:
                    job_manager.add_log(job_id, "开始处理夸克...", "quark")
                    user = await q_engine.check_login()
                    if not user: job_manager.add_log(job_id, "夸克登录失败", "error")
                    else:
                        job_manager.add_log(job_id, f"登录成功: {user}", "success")
                        root_fid = await q_engine.get_folder_id(QUARK_SAVE_PATH)
                        if not root_fid: job_manager.add_log(job_id, "目录不存在", "error")
                        else:
                            for match in q_matches:
                                current_idx += 1; step_prefix = f"[{current_idx}/{total_tasks}]"
                                job_manager.update_progress(job_id, current_idx, total_tasks)
                                
                                t_task = time.time()
                                raw_url = match.group(1)
                                new_url, msg, new_fid = await q_engine.process_url(raw_url, root_fid)
                                
                                if new_url:
                                    log_msg = f"{step_prefix} 转存成功: {new_url} (耗时: {get_time_diff(t_task)})"
                                    # 🚀 动态植入配置
                                    if inject_config['quark']['enabled'] and new_fid:
                                        t_img = time.time()
                                        res_url, _, _ = await q_engine.process_url(inject_config['quark']['url'], new_fid, is_inject=True)
                                        if res_url == "INJECT_OK": log_msg += f" + 植入(耗时:{get_time_diff(t_img)})"
                                    
                                    job_manager.add_log(job_id, log_msg, "success")
                                    final_text = final_text.replace(raw_url, new_url)
                                    success_count += 1
                                else:
                                    job_manager.add_log(job_id, f"{step_prefix} {msg}", "error")
                                await asyncio.sleep(random.uniform(2, 4))

            # --- 百度 ---
            if b_matches:
                if not baidu_cookie: job_manager.add_log(job_id, "百度：未配置Cookie，跳过", "error")
                else:
                    job_manager.add_log(job_id, "开始处理百度...", "baidu")
                    if not b_engine.init_token(): job_manager.add_log(job_id, "百度登录失败", "error")
                    else:
                        job_manager.add_log(job_id, "登录成功", "success")
                        if not b_engine.check_dir_exists(BAIDU_SAVE_PATH): b_engine.create_dir(BAIDU_SAVE_PATH)
                        
                        for match in b_matches:
                            current_idx += 1; step_prefix = f"[{current_idx}/{total_tasks}]"
                            job_manager.update_progress(job_id, current_idx, total_tasks)
                            
                            t_task = time.time()
                            raw_url = match.group(1)
                            pwd = re.search(r'(?:\?pwd=|&pwd=|\s+|提取码[:：]?\s*)([a-zA-Z0-9]{4})', match.group(0))
                            pwd = pwd.group(1) if pwd else ""
                            name = extract_smart_folder_name(input_text, match.start())
                            
                            new_url, msg, new_dir_path = b_engine.process_url({'url': raw_url, 'pwd': pwd, 'name': name}, BAIDU_SAVE_PATH)
                            
                            if new_url:
                                log_msg = f"{step_prefix} 转存成功: {new_url} (耗时: {get_time_diff(t_task)})"
                                # 🚀 动态植入配置
                                if inject_config['baidu']['enabled'] and new_dir_path:
                                    t_img = time.time()
                                    img_res_url, _, _ = b_engine.process_url({'url': inject_config['baidu']['url'], 'pwd': inject_config['baidu']['pwd']}, new_dir_path, is_inject=True)
                                    if img_res_url == "INJECT_OK": log_msg += f" + 植入(耗时:{get_time_diff(t_img)})"

                                job_manager.add_log(job_id, log_msg, "success")
                                final_text = final_text.replace(raw_url, new_url)
                                success_count += 1
                            else:
                                job_manager.add_log(job_id, f"{step_prefix} {msg}", "error")
                            time.sleep(random.uniform(2, 4))

        finally:
            if q_engine: await q_engine.close()
            summary = {"success": success_count, "total": total_tasks, "duration": str(datetime.now() - start_time)[:-4]}
            job_manager.complete_job(job_id, final_text, summary)
            if bark_key or pushdeer_key:
                send_notification(bark_key, pushdeer_key, f"转存完成 ({success_count}/{total_tasks})", "处理结束")

    asyncio.run(async_worker())

def get_time_diff(t): return f"{time.time() - t:.2f}s"

# ==========================================
# 4. 主逻辑 (Secrets + 多用户)
# ==========================================
def get_user_from_secrets(uid):
    """从 Secrets 的 [users] 节点读取用户配置"""
    try:
        if "users" in st.secrets:
            return st.secrets["users"].get(uid, None)
    except: pass
    return None

def main():
    query_params = st.query_params
    uid = query_params.get("uid", None)
    job_id = query_params.get("job_id", None)
    
    # 状态 A: 游客模式 (拒绝访问)
    if not uid:
        st.title("☁️ 网盘转存助手")
        st.info("👋 欢迎！此为内部工具。")
        st.warning("⚠️ 请使用管理员下发的 **专属链接** (例如 `?uid=xxx`) 访问。")
        st.stop()

    # 状态 B: 身份验证
    user_data = get_user_from_secrets(uid)
    if not user_data:
        st.error(f"⛔ **访问拒绝**：无效的用户 ID `{uid}`")
        st.stop()

    # ✨ 核心：读取用户级配置 (如果没填则为空字符串)
    current_name = user_data.get('name', 'User')
    q_cookie = user_data.get('q', '')
    b_cookie = user_data.get('b', '')
    user_bark = user_data.get('bark', '')         
    user_pushdeer = user_data.get('pushdeer', '') 
    
    # ✨ 核心：构建用户级植入配置
    user_q_img = user_data.get('q_img', '')
    user_b_img = user_data.get('b_img', '')
    user_b_pwd = user_data.get('b_pwd', '')
    
    # 组装植入配置，传给 worker_thread
    inject_config = {
        "quark": {"url": user_q_img, "enabled": bool(user_q_img)},
        "baidu": {"url": user_b_img, "pwd": user_b_pwd, "enabled": bool(user_b_img)}
    }

    # 状态 C: 正常功能
    st.title(f"网盘转存 - {current_name}")
    
    with st.expander("🔌 账号与配置状态", expanded=False):
        c1, c2 = st.columns(2)
        c1.markdown(f"**夸克**: {'<span class=status-dot-green>●</span>已连接' if q_cookie else '<span class=status-dot-gray>●</span>未配置'}", unsafe_allow_html=True)
        c2.markdown(f"**百度**: {'<span class=status-dot-green>●</span>已连接' if b_cookie else '<span class=status-dot-gray>●</span>未配置'}", unsafe_allow_html=True)
        
        c3, c4 = st.columns(2)
        has_push = bool(user_bark or user_pushdeer)
        c3.markdown(f"**消息推送**: {'<span class=status-dot-green>●</span>开启' if has_push else '<span class=status-dot-gray>●</span>关闭'}", unsafe_allow_html=True)
        has_inject = bool(user_q_img or user_b_img)
        c4.markdown(f"**广告植入**: {'<span class=status-dot-green>●</span>开启' if has_inject else '<span class=status-dot-gray>●</span>关闭'}", unsafe_allow_html=True)

    if not job_id:
        if not q_cookie and not b_cookie:
            st.error("您尚未配置任何网盘 Cookie，请联系管理员。")
        else:
            input_text = st.text_area("📝 粘贴链接...", height=150, placeholder="支持夸克、百度网盘链接混合粘贴")
            if st.button("🚀 开始转存", type="primary", use_container_width=True):
                if not input_text.strip(): st.toast("请输入内容"); return
                
                new_job_id = job_manager.create_job()
                # 🚀 启动线程：传入用户专属配置
                t = threading.Thread(target=worker_thread, args=(
                    new_job_id, input_text, q_cookie, b_cookie, 
                    user_bark, user_pushdeer, inject_config
                ))
                t.start()
                
                st.query_params["job_id"] = new_job_id
                st.query_params["uid"] = uid # 保持 UID
                st.rerun()

    else:
        # 任务查看区 (逻辑基本不变，只需确保返回按钮带上 UID)
        job = job_manager.get_job(job_id)
        if not job:
            st.warning("任务已过期或不存在")
            if st.button("🔙 返回"):
                st.query_params["uid"] = uid; st.rerun()
        else:
            status = job['status']
            if status == "running":
                st.info("🔄 正在处理中...")
                prog = job['progress']
                if prog['total'] > 0: st.progress(prog['current']/prog['total'])
            else:
                st.success("✅ 处理完成！")

            with st.container():
                st.markdown('<div class="log-container">', unsafe_allow_html=True)
                for log in job['logs']:
                    msg = smart_shorten_url(log['msg'])
                    st.markdown(f'<div class="log-item"><div class="log-time">{log["time"]}</div><div class="log-msg">{msg}</div></div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
            
            if status == "done":
                res = job['result_text']
                st.text_area("结果", value=res, height=150)
                components.html(create_copy_button_html(res), height=60)
                if st.button("🗑️ 开始新任务", use_container_width=True):
                    st.query_params.clear(); st.query_params["uid"] = uid; st.rerun()
            else:
                time.sleep(2); st.rerun()

st.markdown("""
    <style>
    .back-to-top {
        position: fixed; bottom: 80px; right: 20px; width: 40px; height: 40px;
        background-color: #333; border-radius: 50%; opacity: 0.6;
        display: flex; align-items: center; justify-content: center; z-index: 999;
    }
    .back-to-top svg { width: 20px; height: 20px; stroke: white; }
    </style>
    <a href="#top-anchor" class="back-to-top"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.5"><path d="M4.5 10.5 12 3m0 0 7.5 7.5M12 3v18" stroke-linecap="round" stroke-linejoin="round"/></svg></a>
""", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
