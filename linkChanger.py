import streamlit as st
import streamlit.components.v1 as components
import requests
from retrying import retry
import time
import re
import random
import string
import json
from typing import Union, List, Any

# ==========================================
# 第一部分：Streamlit 页面配置
# ==========================================

st.set_page_config(
    page_title="转存助手 Pro",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# 第二部分：配置与常量
# ==========================================

BASE_URL = 'https://pan.baidu.com'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Referer': 'https://pan.baidu.com',
}

# 默认保存路径
FIXED_SAVE_PATH = "我的资源/LinkChanger"

INVALID_CHARS_REGEX = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9_\-\s]')

# ==========================================
# 第三部分：核心工具函数
# ==========================================

def sanitize_filename(name: str) -> str:
    if not name: return ""
    name = re.sub(r'[【】\[\]()]', ' ', name)
    clean_name = INVALID_CHARS_REGEX.sub('', name)
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
    return clean_name

def extract_folder_name(full_text: str, match_start: int) -> str:
    lookback_limit = max(0, match_start - 200)
    pre_text = full_text[lookback_limit:match_start]
    lines = pre_text.splitlines()

    candidate_name = ""
    for line in reversed(lines):
        clean_line = line.strip()
        if not clean_line: continue
        if re.match(r'^(百度|链接|提取码|:|：|https?|夸克)*$', clean_line, re.IGNORECASE):
            continue
        clean_line = re.sub(r'(百度|链接|提取码|:|：|pwd|夸克).*$', '', clean_line, flags=re.IGNORECASE).strip()

        if clean_line:
            candidate_name = clean_line
            break

    final_name = sanitize_filename(candidate_name)
    if not final_name or len(final_name) < 2:
        return None
    return final_name[:50]

def clean_quark_links(text: str) -> str:
    return re.sub(r'^.*pan\.quark\.cn.*$[\r\n]*', '', text, flags=re.MULTILINE)

def update_cookie(bdclnd: str, cookie: str) -> str:
    cookies_dict = dict(map(lambda item: item.split('=', 1), filter(None, cookie.split(';'))))
    cookies_dict['BDCLND'] = bdclnd
    return ';'.join([f'{key}={value}' for key, value in cookies_dict.items()])

def generate_code() -> str:
    return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(4))

def parse_response(content: str) -> Union[List[Any], int]:
    try:
        content_str = content.decode("utf-8")
    except:
        content_str = str(content)

    shareid = re.search(r'"shareid":(\d+?),', content_str)
    uk = re.search(r'"share_uk":"(\d+?)",', content_str)
    fs_id = re.findall(r'"fs_id":(\d+?),', content_str)

    if shareid and uk and fs_id:
        return [shareid.group(1), uk.group(1), fs_id, [], []]
    return -1

def create_copy_button_html(text_to_copy: str):
    safe_text = json.dumps(text_to_copy)[1:-1]
    html = f"""
    <style>
    .copy-btn {{
        background-color: #ffffff;
        color: #31333F;
        padding: 0.5rem 1rem;
        border-radius: 0.5rem;
        border: 1px solid #d6d6d6;
        font-family: sans-serif;
        font-size: 1rem;
        cursor: pointer;
        width: 100%;
        margin-top: 10px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        transition: all 0.2s;
    }}
    .copy-btn:hover {{
        border-color: #ff4b4b;
        color: #ff4b4b;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }}
    .copy-btn:active {{
        background-color: #ff4b4b;
        color: white;
    }}
    </style>
    <script>
    async function copyToClipboard() {{
        const text = "{safe_text}";
        try {{
            await navigator.clipboard.writeText(text);
            const btn = document.getElementById("copyBtn");
            const originalText = btn.innerText;
            btn.innerText = "✅ 已复制成功";
            btn.style.borderColor = "#09ab3b";
            btn.style.color = "#09ab3b";
            setTimeout(() => {{
                btn.innerText = originalText;
                btn.style.borderColor = "#d6d6d6";
                btn.style.color = "#31333F";
            }}, 2000);
        }} catch (err) {{
            const textArea = document.createElement("textarea");
            textArea.value = text;
            document.body.appendChild(textArea);
            textArea.select();
            document.execCommand("copy");
            document.body.removeChild(textArea);
            alert("已复制！");
        }}
    }}
    </script>
    <button id="copyBtn" class="copy-btn" onclick="copyToClipboard()">📋 一键复制全部结果</button>
    """
    return html

# ==========================================
# 第四部分：网络请求类
# ==========================================

class Network:
    def __init__(self, cookie_str):
        self.s = requests.Session()
        self.s.trust_env = False
        self.headers = HEADERS.copy()
        self.headers['Cookie'] = "".join(cookie_str.split())
        self.bdstoken = ''
        requests.packages.urllib3.disable_warnings()

    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def get_bdstoken(self) -> Union[str, int]:
        url = f'{BASE_URL}/api/gettemplatevariable'
        params = {'fields': '["bdstoken","token","uk","isdocuser"]'}
        try:
            r = self.s.get(url, params=params, headers=self.headers, verify=False)
            if 'errno' in r.json() and r.json()['errno'] != 0:
                return r.json()['errno']
            return r.json()['result']['bdstoken']
        except:
            return -1

    @retry(stop_max_attempt_number=3)
    def verify_pass_code(self, link: str, code: str) -> Union[str, int]:
        url = f'{BASE_URL}/share/verify'
        surl = re.search(r'(?:surl=|/s/1|/s/)([\w\-]+)', link)
        if not surl: return -9

        params = {
            'surl': surl.group(1),
            't': str(int(time.time() * 1000)),
            'bdstoken': self.bdstoken,
            'channel': 'chunlei', 'web': '1', 'clienttype': '0'
        }
        data = {'pwd': code, 'vcode': '', 'vcode_str': ''}
        r = self.s.post(url, params=params, data=data, headers=self.headers, verify=False)
        if r.json()['errno'] == 0:
            return r.json()['randsk']
        return r.json()['errno']

    def get_transfer_params(self, url: str) -> bytes:
        return self.s.get(url, headers=self.headers, verify=False).content

    @retry(stop_max_attempt_number=3)
    def create_dir(self, path: str) -> int:
        url = f'{BASE_URL}/api/create'
        if not path.startswith("/"): path = "/" + path
        data = {'path': path, 'isdir': '1', 'block_list': '[]'}
        params = {'a': 'commit', 'bdstoken': self.bdstoken}
        r = self.s.post(url, params=params, data=data, headers=self.headers, verify=False)
        return r.json()['errno']

    @retry(stop_max_attempt_number=5)
    def transfer_file(self, params_list: list, path: str) -> int:
        url = f'{BASE_URL}/share/transfer'
        if not path.startswith("/"): path = "/" + path
        data = {'fsidlist': f"[{','.join(params_list[2])}]", 'path': path}
        params = {'shareid': params_list[0], 'from': params_list[1], 'bdstoken': self.bdstoken}
        r = self.s.post(url, params=params, data=data, headers=self.headers, verify=False)
        return r.json()['errno']

    @retry(stop_max_attempt_number=3)
    def delete_file(self, path: str) -> int:
        url = f'{BASE_URL}/api/filemanager'
        if not path.startswith("/"): path = "/" + path
        data = {'filelist': f'["{path}"]'}
        params = {'oper': 'delete', 'bdstoken': self.bdstoken}
        try:
            r = self.s.post(url, params=params, data=data, headers=self.headers, verify=False)
            if 'errno' in r.json():
                return r.json()['errno']
            return -1
        except:
            return -1

    @retry(stop_max_attempt_number=3)
    def create_share(self, fs_id: str, pwd: str) -> Union[str, int]:
        url = f'{BASE_URL}/share/set'
        data = {'period': '0', 'pwd': pwd, 'fid_list': f'[{fs_id}]', 'schannel': '4'}
        params = {'bdstoken': self.bdstoken, 'channel': 'chunlei', 'clienttype': '0', 'web': '1'}
        r = self.s.post(url, params=params, data=data, headers=self.headers, verify=False)
        if r.json()['errno'] == 0:
            return r.json()['link']
        return r.json()['errno']

    def get_dir_fsid(self, parent_path: str, target_name: str) -> str:
        url = f'{BASE_URL}/api/list'
        if not parent_path.startswith("/"): parent_path = "/" + parent_path
        params = {'dir': parent_path, 'bdstoken': self.bdstoken, 'order': 'time', 'desc': '1'}
        r = self.s.get(url, params=params, headers=self.headers, verify=False)
        if r.json()['errno'] == 0:
            for item in r.json()['list']:
                if item['server_filename'] == target_name:
                    return item['fs_id']
        return None

# ==========================================
# 第五部分：Streamlit 业务流程
# ==========================================

def process_single_link(network, match, full_text, root_path, log_container):
    url = match.group(1)
    pwd_match = re.search(r'(?:\?pwd=|&pwd=|\s+|提取码[:：]?\s*)([a-zA-Z0-9]{4})', match.group(0))
    pwd = pwd_match.group(1) if pwd_match else ""
    clean_url = url.split('?')[0]

    folder_name = extract_folder_name(full_text, match.start())
    if not folder_name:
        folder_name = f"Resource_{int(time.time())}"
        log_container.warning(f"⚠️ 使用默认名: **{folder_name}**")
    else:
        log_container.info(f"📂 识别: **{folder_name}**")

    if pwd:
        res = network.verify_pass_code(clean_url, pwd)
        if isinstance(res, int):
            log_container.error(f"❌ 密码验证失败 ({clean_url})")
            return None
        network.headers['Cookie'] = update_cookie(res, network.headers['Cookie'])

    content = network.get_transfer_params(clean_url)
    params = parse_response(content)
    if params == -1:
        log_container.error(f"❌ 链接解析失败")
        return None

    safe_suffix = generate_code()
    final_folder_name = f"{folder_name}_{safe_suffix}"
    full_save_path = f"{root_path}/{final_folder_name}"

    # 1. 创建子目录
    create_res = network.create_dir(full_save_path)
    if create_res != 0 and create_res != -8:
        # 重试策略
        final_folder_name = f"Transfer_{int(time.time())}_{safe_suffix}"
        full_save_path = f"{root_path}/{final_folder_name}"
        if network.create_dir(full_save_path) != 0:
            log_container.error("❌ 目录创建失败")
            return None

    # 2. 转存
    transfer_res = network.transfer_file(params, full_save_path)
    if transfer_res != 0:
        log_container.error(f"❌ 转存失败 (Code: {transfer_res})，清理空目录...")
        # 失败时立即删除空文件夹
        network.delete_file(full_save_path)
        return None

    # 3. 获取ID并分享
    fs_id = network.get_dir_fsid(root_path, final_folder_name)
    if not fs_id:
        log_container.error("❌ 获取文件ID失败")
        return None

    new_pwd = generate_code()
    share_link = network.create_share(fs_id, new_pwd)

    if isinstance(share_link, int):
        log_container.error(f"❌ 创建分享失败")
        return None

    log_container.success(f"✅ 成功")
    return f"{share_link}?pwd={new_pwd}"

def clear_text():
    st.session_state["user_input"] = ""

def main():
        # --- 侧边栏配置区 ---
    with st.sidebar:
        st.header("⚙️ 配置面板")
        
        # 1. 智能读取 Secrets (支持多种格式)
        default_cookie = ""
        
        # 尝试方式 A: [baidu] cookie = "..."
        if "baidu" in st.secrets and "cookie" in st.secrets["baidu"]:
            default_cookie = st.secrets["baidu"]["cookie"]
        
        # 尝试方式 B: BD_COOKIE = "..." (你可能用的这种)
        elif "BD_COOKIE" in st.secrets:
            default_cookie = st.secrets["BD_COOKIE"]
            
        # 尝试方式 C: cookie = "..." (直接放在根目录)
        elif "cookie" in st.secrets:
            default_cookie = st.secrets["cookie"]
            
        # 调试信息 (如果还是空的，会在侧边栏显示当前读取到了什么Key，方便排查)
        # if not default_cookie:
        #     st.caption(f"调试: 检测到的Secrets Keys: {list(st.secrets.keys())}")
        
        # 2. 显示输入框
        user_cookie = st.text_input(
            "百度 Cookie (BDUSS等)",
            value=default_cookie,
            type="password", 
            help="优先读取 Secrets 配置，也可在此处临时修改。"
        )

    # --- 主界面 ---
    st.title("🔗 LinkChanger Pro")
    
    input_text = st.text_area(
        "📝 输入资源文本",
        height=180,
        placeholder="在此粘贴包含链接的文本（支持混合文本，自动提取链接和提取码）...",
        key="user_input"
    )

    col_act1, col_act2 = st.columns([1, 5])
    with col_act1:
        start_process = st.button("🚀 开始处理", type="primary", use_container_width=True)
    with col_act2:
        st.button("🗑️ 清空", on_click=clear_text)

    if start_process:
        if not input_text:
            st.warning("请先输入文本")
            st.stop()
            
        if not user_cookie:
            st.error("❌ 缺少 Cookie，无法进行操作。")
            st.stop()

        processed_text = clean_quark_links(input_text)
        
        # 初始化网络类，使用侧边栏（或Secrets）里的 Cookie
        network = Network(user_cookie)

        # 验证 Token
        token = network.get_bdstoken()
        if isinstance(token, int):
            st.error(f"❌ Cookie 无效或已过期 (Error: {token})")
            st.sidebar.error("Cookie 失效，请更新")
            st.stop()
        network.bdstoken = token

        # 查找链接
        link_regex = re.compile(r'(https?://pan\.baidu\.com/s/[a-zA-Z0-9_\-]+(?:\?pwd=[a-zA-Z0-9]+)?)')
        matches = list(link_regex.finditer(processed_text))

        if not matches:
            st.info("⚠️ 文本中未找到百度网盘链接")
            st.stop()

        # 准备进度显示
        progress_bar = st.progress(0)
        status_text = st.empty()
        final_text = processed_text
        success_count = 0
        total_links = len(matches)

        # === 关键修正：在循环前只创建一次根目录 ===
        network.create_dir(FIXED_SAVE_PATH)

        # 使用折叠框显示详细日志
        with st.expander("📜 处理日志详情 (点击展开)", expanded=True):
            for i, match in enumerate(reversed(matches)):
                status_text.text(f"正在处理链接 {i+1}/{total_links}...")
                progress_bar.progress((i + 1) / total_links)
                
                # 为每个链接创建一个小的容器显示状态
                log_col1, log_col2 = st.columns([3, 1])
                with log_col1:
                    new_link = process_single_link(network, match, processed_text, FIXED_SAVE_PATH, st)
                
                if new_link:
                    start, end = match.span()
                    final_text = final_text[:start] + new_link + final_text[end:]
                    success_count += 1

        progress_bar.empty()
        status_text.empty()

        # --- 结果展示区 ---
        st.divider()
        
        # 显示统计指标
        m1, m2, m3 = st.columns(3)
        m1.metric("总链接数", total_links)
        m2.metric("成功转存", success_count, delta_color="normal")
        m3.metric("失败/跳过", total_links - success_count, delta_color="inverse")

        if success_count > 0:
            st.success("🎉 处理完成！")
            st.text_area("✨ 最终结果 (可直接编辑)", value=final_text, height=250)
            components.html(create_copy_button_html(final_text), height=60)
        else:
            st.error("⚠️ 处理完成，但没有生成新的链接。")

if __name__ == '__main__':
    main()
