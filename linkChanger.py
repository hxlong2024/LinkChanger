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
from datetime import datetime
from typing import Union, List, Any
from retrying import retry

# ==========================================
# 第一部分：页面配置与全局样式
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
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 第二部分：通用常量与工具函数
# ==========================================

QUARK_SAVE_PATH = "来自：分享/LinkChanger"
BAIDU_SAVE_PATH = "/我的资源/LinkChanger"

INVALID_CHARS_REGEX = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9_\-\s]')

def get_timestamp_str():
    return datetime.now().strftime("%H:%M:%S")

def sanitize_filename(name: str) -> str:
    if not name: return ""
    name = re.sub(r'[【】\[\]()]', ' ', name)
    clean_name = INVALID_CHARS_REGEX.sub('', name)
    return re.sub(r'\s+', ' ', clean_name).strip()

def extract_smart_folder_name(full_text: str, match_start: int) -> str:
    """智能提取资源名称 (通用版)"""
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

# ==========================================
# 第三部分：夸克引擎 (Async)
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

    async def process_url(self, url: str, target_fid: str):
        try:
            if '/s/' not in url: return None, "格式错误"
            pwd_id = url.split('/s/')[-1].split('?')[0].split('#')[0]
            match = re.search(r'[?&]pwd=([a-zA-Z0-9]+)', url)
            passcode = match.group(1) if match else ""
        except: return None, "解析异常"

        try:
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token", 
                                     json={"pwd_id": pwd_id, "passcode": passcode}, params=self._params())
            stoken = r.json().get('data', {}).get('stoken')
            if not stoken: return None, "提取码错误或失效"
        except: return None, "Token请求失败"

        params = self._params()
        params.update({"pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "_page": 1, "_size": 50})
        try:
            r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail", params=params)
            items = r.json().get('data', {}).get('list', [])
            if not items: return None, "空分享"
            source_fids = [i['fid'] for i in items]
            source_tokens = [i['share_fid_token'] for i in items]
            first_name = items[0]['file_name']
        except: return None, "获取详情失败"

        save_data = {"fid_list": source_fids, "fid_token_list": source_tokens, "to_pdir_fid": target_fid, 
                     "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "scene": "link"}
        try:
            r = await self.client.post("https://drive.quark.cn/1/clouddrive/share/sharepage/save", json=save_data, params=self._params())
            if r.json().get('code') not in [0, 'OK']: return None, f"转存失败: {r.json().get('message')}"
            task_id = r.json().get('data', {}).get('task_id')
        except: return None, "转存请求失败"

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
        
        if not new_fid: return None, "未找到转存文件"

        share_data = {"fid_list": [new_fid], "title": first_name, "url_type": 1, "expired_type": 1}
        try:
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share", json=share_data, params=self._params())
            share_task_id = r.json().get('data', {}).get('task_id')
            
            await asyncio.sleep(0.5)
            params = self._params()
            params.update({'task_id': share_task_id, 'retry_index': 0})
            r = await self.client.get("https://drive-pc.quark.cn/1/clouddrive/task", params=params)
            share_id = r.json().get('data', {}).get('share_id')
            
            r = await self.client.post("https://drive-pc.quark.cn/1/clouddrive/share/password", json={"share_id": share_id}, params=self._params())
            return r.json()['data']['share_url'], "成功"
        except: return None, "分享创建失败"

# ==========================================
# 第四部分：百度引擎 (Sync - Requests)
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
        r = self.s.get('https://pan.baidu.com/api/list', params={'dir': path, 'bdstoken': self.bdstoken, 'start': 0, 'limit': 1}, headers=self.headers, verify=False)
        return r.json().get('errno') == 0

    def create_dir(self, path):
        if not path.startswith("/"): path = "/" + path
        self.s.post('https://pan.baidu.com/api/create', params={'a': 'commit', 'bdstoken': self.bdstoken}, 
                    data={'path': path, 'isdir': 1, 'block_list': '[]'}, headers=self.headers, verify=False)

    def process_url(self, url_info: dict, root_path: str):
        url = url_info['url']
        pwd = url_info['pwd']
        clean_url = url.split('?')[0]
        folder_name = url_info['name']

        if pwd:
            surl = re.search(r'(?:surl=|/s/1|/s/)([\w\-]+)', clean_url)
            if not surl: return None, "URL格式错误"
            r = self.s.post('https://pan.baidu.com/share/verify', 
                            params={'surl': surl.group(1), 't': int(time.time()*1000), 'bdstoken': self.bdstoken, 'channel': 'chunlei', 'web': 1, 'clienttype': 0},
                            data={'pwd': pwd, 'vcode': '', 'vcode_str': ''}, headers=self.headers, verify=False)
            if r.json()['errno'] == 0:
                self.update_cookie_bdclnd(r.json()['randsk'])
            else:
                return None, "提取码错误"

        content = self.s.get(clean_url, headers=self.headers, verify=False).text
        try:
            shareid = re.search(r'"shareid":(\d+?),', content).group(1)
            uk = re.search(r'"share_uk":"(\d+?)",', content).group(1)
            fs_id_list = re.findall(r'"fs_id":(\d+?),', content)
            if not fs_id_list: return None, "无文件"
        except: return None, "页面解析失败"

        safe_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
        final_folder = f"{folder_name}_{safe_suffix}"
        save_path = f"{root_path}/{final_folder}"
        
        self.create_dir(save_path) 

        r = self.s.post('https://pan.baidu.com/share/transfer', 
                        params={'shareid': shareid, 'from': uk, 'bdstoken': self.bdstoken},
                        data={'fsidlist': f"[{','.join(fs_id_list)}]", 'path': save_path}, headers=self.headers, verify=False)
        if r.json()['errno'] != 0: return None, f"转存失败({r.json()['errno']})"

        r = self.s.get('https://pan.baidu.com/api/list', params={'dir': root_path, 'bdstoken': self.bdstoken}, headers=self.headers, verify=False)
        target_fsid = None
        for item in r.json().get('list', []):
            if item['server_filename'] == final_folder:
                target_fsid = item['fs_id']; break
        
        if not target_fsid: return None, "获取新目录失败"

        new_pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
        r = self.s.post('https://pan.baidu.com/share/set', 
                        params={'bdstoken': self.bdstoken, 'channel': 'chunlei', 'clienttype': 0, 'web': 1},
                        data={'period': 0, 'pwd': new_pwd, 'fid_list': f'[{target_fsid}]', 'schannel': 4}, headers=self.headers, verify=False)
        
        if r.json()['errno'] == 0:
            return f"{r.json()['link']}?pwd={new_pwd}", "成功"
        return None, "分享创建失败"

# ==========================================
# 第五部分：主逻辑与界面
# ==========================================

def get_secret(key):
    try:
        if "quark" in st.secrets and key == "quark_cookie": return st.secrets["quark"]["cookie"]
        if "baidu" in st.secrets and key == "baidu_cookie": return st.secrets["baidu"]["cookie"]
        return st.secrets.get(key.upper(), "")
    except: return ""

def clear_text():
    st.session_state["link_input"] = ""

def main():
    st.title("网盘转存助手")
    
    with st.sidebar:
        st.header("⚙️ 账号配置")
        
        tab_q, tab_b = st.tabs(["☁️ 夸克设置", "🐻 百度设置"])
        
        with tab_q:
            q_cookie_default = get_secret("quark_cookie")
            quark_cookie = st.text_area("夸克 Cookie", value=q_cookie_default, height=100, key="q_c", placeholder="b-user-id=...")
            st.caption(f"📂 存至: `{QUARK_SAVE_PATH}`")
            
        with tab_b:
            b_cookie_default = get_secret("baidu_cookie")
            baidu_cookie = st.text_area("百度 Cookie", value=b_cookie_default, height=100, key="b_c", placeholder="BDUSS=...")
            st.caption(f"📂 存至: `{BAIDU_SAVE_PATH}`")

    st.info("💡 提示：支持混合输入夸克和百度链接，程序会自动识别并分类处理。")
    
    # 绑定 key 以便清空
    input_text = st.text_area("📝 请在此处粘贴链接文本...", height=200, key="link_input")

    col1, col2 = st.columns([1, 4])
    
    if col1.button("🚀 开始转存", type="primary", use_container_width=True):
        if not input_text.strip():
            st.toast("请输入内容", icon="⚠️"); return

        quark_regex = re.compile(r'(https://pan\.quark\.cn/s/[a-zA-Z0-9]+(?:\?pwd=[a-zA-Z0-9]+)?)')
        baidu_regex = re.compile(r'(https?://pan\.baidu\.com/s/[a-zA-Z0-9_\-]+(?:\?pwd=[a-zA-Z0-9]+)?)')
        
        q_matches = list(quark_regex.finditer(input_text))
        b_matches = list(baidu_regex.finditer(input_text))
        
        total_tasks = len(q_matches) + len(b_matches)
        if total_tasks == 0:
            st.warning("❌ 未识别到有效链接"); return

        q_engine = QuarkEngine(quark_cookie) if q_matches else None
        b_engine = BaiduEngine(baidu_cookie) if b_matches else None

        async def run_process():
            final_text = input_text
            success_count = 0
            
            with st.status(f"正在处理 {total_tasks} 个任务...", expanded=True) as status:
                
                if q_matches:
                    if not quark_cookie:
                        st.error("检测到夸克链接但未配置 Cookie，跳过。")
                    else:
                        st.write("--- ☁️ **开始处理夸克链接** ---")
                        user = await q_engine.check_login()
                        if not user:
                            st.error("夸克登录失败：Cookie 无效或 IP 限制")
                        else:
                            st.write(f"✅ 夸克登录成功: {user}")
                            target_fid = await q_engine.get_folder_id(QUARK_SAVE_PATH)
                            if not target_fid:
                                st.error(f"❌ 夸克目录不存在: {QUARK_SAVE_PATH}")
                            else:
                                for match in q_matches:
                                    raw_url = match.group(1)
                                    st.write(f"🔄 处理: {raw_url}")
                                    new_url, msg = await q_engine.process_url(raw_url, target_fid)
                                    
                                    if new_url:
                                        final_text = final_text.replace(raw_url, new_url) 
                                        st.markdown(f"<span class='quark-tag'>夸克</span> ✅ 成功", unsafe_allow_html=True)
                                        success_count += 1
                                    else:
                                        st.markdown(f"<span class='quark-tag'>夸克</span> ❌ 失败: {msg}", unsafe_allow_html=True)

                if b_matches:
                    if not baidu_cookie:
                        st.error("检测到百度链接但未配置 Cookie，跳过。")
                    else:
                        st.write("--- 🐻 **开始处理百度链接** ---")
                        if not b_engine.init_token():
                            st.error("百度登录失败：Cookie 无效")
                        else:
                            st.write("✅ 百度 Token 获取成功")
                            if not b_engine.check_dir_exists(BAIDU_SAVE_PATH):
                                st.write(f"📁 创建百度目录: {BAIDU_SAVE_PATH}")
                                b_engine.create_dir(BAIDU_SAVE_PATH)
                            
                            for match in b_matches:
                                raw_url = match.group(1)
                                full_match_str = match.group(0)
                                
                                pwd_match = re.search(r'(?:\?pwd=|&pwd=|\s+|提取码[:：]?\s*)([a-zA-Z0-9]{4})', full_match_str)
                                pwd = pwd_match.group(1) if pwd_match else ""
                                name = extract_smart_folder_name(input_text, match.start())
                                
                                st.write(f"🔄 处理: {name} | {raw_url}")
                                
                                new_url, msg = b_engine.process_url({'url': raw_url, 'pwd': pwd, 'name': name}, BAIDU_SAVE_PATH)
                                
                                if new_url:
                                    final_text = final_text.replace(raw_url, new_url)
                                    st.markdown(f"<span class='baidu-tag'>百度</span> ✅ 成功", unsafe_allow_html=True)
                                    success_count += 1
                                else:
                                    st.markdown(f"<span class='baidu-tag'>百度</span> ❌ 失败: {msg}", unsafe_allow_html=True)

                if q_engine: await q_engine.close()
                status.update(label="处理完成", state="complete", expanded=False)
            
            if success_count > 0:
                st.balloons()
                st.success(f"✨ 成功转存 {success_count}/{total_tasks} 个链接")
                st.text_area("⬇️ 最终结果", value=final_text, height=250)
                components.html(create_copy_button_html(final_text), height=80)
            else:
                st.warning("没有链接被成功处理。")

        asyncio.run(run_process())

    # 清空按钮绑定回调
    if col2.button("🗑️ 清空内容", use_container_width=True, on_click=clear_text):
        pass

if __name__ == "__main__":
    main()
