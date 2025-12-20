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
from datetime import datetime, timedelta, timezone # 引入时区处理
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

FIXED_IMAGE_CONFIG = {
    "quark": {
        "url": get_secret("quark", "img_url"),
        "enabled": False 
    },
    "baidu": {
        "url": get_secret("baidu", "img_url"),
        "pwd": get_secret("baidu", "img_pwd"),
        "name": get_secret("baidu", "img_name", "公众号关注.jpg"),
        "enabled": False
    }
}

QUARK_SAVE_PATH = "来自：分享/LinkChanger"
BAIDU_SAVE_PATH = "/我的资源/LinkChanger"

# ==========================================
# 1. 页面配置与样式
# ==========================================
st.set_page_config(
    page_title="网盘转存助手",
    page_icon="📂",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .stTextArea textarea { font-family: 'Source Code Pro', monospace; font-size: 14px; }
    .success-text { color: #09ab3b; font-weight: bold; }
    .stStatusWidget { border: 1px solid #e0e0e0; border-radius: 8px; }
    .quark-tag { background-color: #0088ff; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; margin-right: 5px; }
    .baidu-tag { background-color: #ff4d4f; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; margin-right: 5px; }
    .inject-tag { background-color: #ff9900; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; margin-right: 5px; }
    .time-tag { color: #888; font-size: 0.85em; margin-left: 8px; font-family: monospace; }
    .result-box { border: 2px solid #e6f4ea; padding: 15px; border-radius: 10px; background-color: #f9fdfa; margin-top: 20px; }
    </style>
""", unsafe_allow_html=True)

# 初始化状态
if 'process_logs' not in st.session_state:
    st.session_state.process_logs = []
if 'final_result_cache' not in st.session_state:
    st.session_state.final_result_cache = ""
if 'process_status' not in st.session_state:
    st.session_state.process_status = None
if 'task_summary' not in st.session_state:
    st.session_state.task_summary = {}

INVALID_CHARS_REGEX = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9_\-\s]')

# 获取北京时间字符串
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

# ==========================================
# 2. 夸克引擎 (Async)
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

        # 1. Token
        try:
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token", 
                                     json={"pwd_id": pwd_id, "passcode": passcode}, params=self._params())
            stoken = r.json().get('data', {}).get('stoken')
            if not stoken: return None, "提取码失效", None
        except: return None, "Token请求失败", None

        # 2. Detail
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

        # 3. Transfer
        save_data = {"fid_list": source_fids, "fid_token_list": source_tokens, "to_pdir_fid": target_fid, 
                     "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "scene": "link"}
        try:
            r = await self.client.post("https://drive.quark.cn/1/clouddrive/share/sharepage/save", json=save_data, params=self._params())
            if r.json().get('code') not in [0, 'OK']: return None, f"转存失败: {r.json().get('message')}", None
            task_id = r.json().get('data', {}).get('task_id')
        except: return None, "转存请求失败", None

        if is_inject: return "INJECT_OK", "植入成功", None

        # 4. Wait
        for _ in range(8):
            await asyncio.sleep(1)
            try:
                params = self._params()
                params['task_id'] = task_id
                r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/task", params=params)
                if r.json().get('data', {}).get('status') == 2: break
            except: pass

        # 5. Find New
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

        # 6. Share
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

# ==========================================
# 3. 百度引擎 (Sync - 增强稳定性版)
# ==========================================
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
# 4. 主逻辑 (北京时间+无图标版)
# ==========================================
def clear_state():
    st.session_state.link_input = ""
    st.session_state.process_logs = []
    st.session_state.final_result_cache = ""
    st.session_state.process_status = None
    st.session_state.task_summary = {}

def add_log(message: str, is_error=False):
    timestamp = get_beijing_time_str() # 使用北京时间
    log_entry = f"`{timestamp}` {message}"
    st.session_state.process_logs.append(log_entry)

def main():
    st.title("网盘转存助手")
    
    with st.sidebar:
        st.header("⚙️ 账号配置")
        tab_q, tab_b = st.tabs(["☁️ 夸克设置", "🐻 百度设置"])
        
        with tab_q:
            q_cookie_default = get_secret("quark", "cookie")
            quark_cookie = st.text_area("夸克 Cookie", value=q_cookie_default, height=100, key="q_c", placeholder="b-user-id=...")
            st.divider()
            st.markdown("🖼️ **图片植入**")
            q_img_url = st.text_input("图片分享链接", value=FIXED_IMAGE_CONFIG['quark']['url'], key="q_img")
            if q_img_url: FIXED_IMAGE_CONFIG['quark']['url'] = q_img_url; FIXED_IMAGE_CONFIG['quark']['enabled'] = True
            
        with tab_b:
            b_cookie_default = get_secret("baidu", "cookie")
            baidu_cookie = st.text_area("百度 Cookie", value=b_cookie_default, height=100, key="b_c", placeholder="BDUSS=...")
            st.divider()
            st.markdown("🖼️ **图片植入**")
            b_img_url = st.text_input("图片分享链接", value=FIXED_IMAGE_CONFIG['baidu']['url'], key="b_img")
            b_img_pwd = st.text_input("提取码", value=FIXED_IMAGE_CONFIG['baidu']['pwd'], key="b_img_pwd")
            if b_img_url: FIXED_IMAGE_CONFIG['baidu']['url'] = b_img_url; FIXED_IMAGE_CONFIG['baidu']['pwd'] = b_img_pwd; FIXED_IMAGE_CONFIG['baidu']['enabled'] = True

    st.info("💡 提示：支持混合输入夸克和百度链接，程序会自动识别并分类处理。")
    input_text = st.text_area("📝 请在此处粘贴链接文本...", height=200, key="link_input")

    col1, col2 = st.columns([1, 4])
    
    if col1.button("🚀 开始转存", type="primary", use_container_width=True):
        if not input_text.strip():
            st.toast("请输入内容", icon="⚠️"); return

        st.session_state.process_logs = []
        st.session_state.final_result_cache = ""
        st.session_state.process_status = "running"
        
        quark_regex = re.compile(r'(https://pan\.quark\.cn/s/[a-zA-Z0-9]+(?:\?pwd=[a-zA-Z0-9]+)?)')
        baidu_regex = re.compile(r'(https?://pan\.baidu\.com/s/[a-zA-Z0-9_\-]+(?:\?pwd=[a-zA-Z0-9]+)?)')
        q_matches = list(quark_regex.finditer(input_text))
        b_matches = list(baidu_regex.finditer(input_text))
        total_tasks = len(q_matches) + len(b_matches)

        if total_tasks == 0:
            st.warning("❌ 未识别到有效链接"); st.stop()

        q_engine = QuarkEngine(quark_cookie) if q_matches else None
        b_engine = BaiduEngine(baidu_cookie) if b_matches else None

        async def run_process():
            start_time = datetime.now()
            final_text = input_text
            success_count = 0
            current_idx = 0
            
            status_container = st.status(f"正在处理 {total_tasks} 个任务...", expanded=True)
            log_placeholder = status_container.empty()

            try:
                # --- 夸克 ---
                if q_matches:
                    if not quark_cookie: add_log("❌ 夸克：未配置Cookie，跳过", True)
                    else:
                        add_log("--- ☁️ **开始处理夸克链接** ---")
                        t0 = time.time()
                        user = await q_engine.check_login()
                        if not user: add_log(f"❌ 登录失败 ({get_time_diff(t0)})", True)
                        else:
                            add_log(f"✅ 登录成功: {user} ({get_time_diff(t0)})")
                            t0 = time.time()
                            root_fid = await q_engine.get_folder_id(QUARK_SAVE_PATH)
                            if not root_fid: add_log(f"❌ 目录不存在 ({get_time_diff(t0)})", True)
                            else:
                                for match in q_matches:
                                    current_idx += 1
                                    raw_url = match.group(1)
                                    add_log(f"🔄 [{current_idx}/{total_tasks}] 处理: `{raw_url}`")
                                    log_placeholder.markdown("\n\n".join(st.session_state.process_logs))
                                    
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
                                        
                                        add_log(f"  ↳ {log_msg}")
                                        final_text = final_text.replace(raw_url, new_url)
                                        success_count += 1
                                    else:
                                        is_err = "✅" not in msg
                                        add_log(f"  ↳ {msg} ({t_task_end})", is_err)

                                    if current_idx < total_tasks: await asyncio.sleep(random.uniform(2, 4))

                # --- 百度 ---
                if b_matches:
                    if not baidu_cookie: add_log("❌ 百度：未配置Cookie，跳过", True)
                    else:
                        add_log("--- 🐻 **开始处理百度链接** ---")
                        t0 = time.time()
                        if not b_engine.init_token(): add_log(f"❌ 登录失败 ({get_time_diff(t0)})", True)
                        else:
                            add_log(f"✅ 登录成功 ({get_time_diff(t0)})")
                            if not b_engine.check_dir_exists(BAIDU_SAVE_PATH): b_engine.create_dir(BAIDU_SAVE_PATH)
                            
                            for match in b_matches:
                                current_idx += 1
                                raw_url = match.group(1)
                                pwd_match = re.search(r'(?:\?pwd=|&pwd=|\s+|提取码[:：]?\s*)([a-zA-Z0-9]{4})', match.group(0))
                                pwd = pwd_match.group(1) if pwd_match else ""
                                name = extract_smart_folder_name(input_text, match.start())
                                
                                add_log(f"🔄 [{current_idx}/{total_tasks}] 处理: `{name}`")
                                log_placeholder.markdown("\n\n".join(st.session_state.process_logs))
                                
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

                                    add_log(f"  ↳ {log_msg}")
                                    final_text = final_text.replace(raw_url, new_url)
                                    success_count += 1
                                else:
                                    is_err = "✅" not in msg
                                    add_log(f"  ↳ {msg} ({t_task_end})", is_err)

                                if current_idx < total_tasks: time.sleep(random.uniform(2, 4))

            finally:
                if q_engine: await q_engine.close()
                status_container.update(label="处理完成", state="complete", expanded=False)
                
                st.session_state.final_result_cache = final_text
                st.session_state.process_status = "done"
                st.session_state.task_summary = {
                    "success": success_count,
                    "total": total_tasks,
                    "duration": str(datetime.now() - start_time)
                }
                st.rerun()

        asyncio.run(run_process())

    if col2.button("🗑️ 清空内容", use_container_width=True, on_click=clear_state):
        pass

    # ==========================================
    # 5. 持久化结果展示区
    # ==========================================
    if st.session_state.process_logs:
        with st.expander("📜 处理日志历史 (点击展开)", expanded=(st.session_state.process_status != 'done')):
            for log in st.session_state.process_logs:
                st.markdown(log)

    if st.session_state.final_result_cache:
        # 安全获取耗时
        duration_str = st.session_state.task_summary.get('duration', '0s')
        if isinstance(duration_str, str) and len(duration_str) > 4:
            safe_duration = duration_str[:-4]
        else:
            safe_duration = duration_str

        st.markdown(f"""
        <div class="result-box">
            <h3>✨ 处理完成</h3>
            <p>成功: <b>{st.session_state.task_summary.get('success', 0)}</b> / {st.session_state.task_summary.get('total', 0)} 
            &nbsp;|&nbsp; 耗时: {safe_duration}</p>
        </div>
        """, unsafe_allow_html=True)
        st.balloons()
        st.text_area("⬇️ 最终结果 (已保存)", value=st.session_state.final_result_cache, height=250)
        components.html(create_copy_button_html(st.session_state.final_result_cache), height=80)

if __name__ == "__main__":
    main()
