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
# 第一部分：配置与常量
# ==========================================

BASE_URL = 'https://pan.baidu.com'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Referer': 'https://pan.baidu.com',
}

# 用户指定的固定配置
FIXED_SAVE_PATH = "linkchanger/link"
FIXED_COOKIE = r"XFI=5610b6a6-9c5b-5af5-2920-01c6f26cd68e; XFCS=F867D20ADD986D508B4FE3FC9808AF594712E01CED1ECEA8A4509FE3681EF65A; XFT=+aWVjJd3bSgnCMTSdWoHwdzzwpN3sEvD6qltd+NJ16U=; PANWEB=1; BAIDU_WISE_UID=wapp_1757493034845_354; scholar_new_version=1; __bid_n=199562b36651328548f06c; scholar_new_detail=1; BIDUPSID=1D0E90A4825BC0724DDDE7091DA86F18; PSTM=1758790941; BAIDUID=1D0E90A4825BC0724DDDE7091DA86F18:SL=0:NR=10:FG=1; BAIDUID_BFESS=1D0E90A4825BC0724DDDE7091DA86F18:SL=0:NR=10:FG=1; MAWEBCUID=web_beWNQkUiLcQQKTWugVChMJZhRTUPPaCiFaATwGLlhjwmIkROOx; ZFY=Ox2DfbvW6ZTnC:ALtyhO:B87488WU3duP6wlSdAlihrp0:C; Hm_lvt_fa0277816200010a74ab7d2895df481b=1762328389; newlogin=1; ploganondeg=1; H_PS_PSSID=60275_63147_65361_65894_65986_66101_66122_66218_66203_66169_66359_66287_66261_66393_66394_66443_66511_66516_66529_66558_66584_66591_66599_66604_66615; H_WISE_SIDS=60275_63147_65361_65894_65986_66101_66122_66218_66203_66169_66359_66287_66261_66393_66394_66443_66511_66516_66529_66558_66584_66591_66599_66604_66615; BDUSS=NXdVgxSXBOUmtzR0NzUk80U1dJQ2tDb1p4ZVo1Rm9sWmVKc0NVRmMxUEQxVmxwSVFBQUFBJCQAAAAAAAAAAAEAAAB1B9yX0KGxprXEufvBo7PIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAMNIMmnDSDJpTH; BDUSS_BFESS=NXdVgxSXBOUmtzR0NzUk80U1dJQ2tDb1p4ZVo1Rm9sWmVKc0NVRmMxUEQxVmxwSVFBQUFBJCQAAAAAAAAAAAEAAAB1B9yX0KGxprXEufvBo7PIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAMNIMmnDSDJpTH; STOKEN=6b758669a4bcfae2afe57badfc0d5b73ac4f9adf9f70d10dedafcd910b50ec61; Hm_lvt_7a3960b6f067eb0085b7f96ff5e660b0=1764034348,1764155073,1764551731,1765087281; BDCLND=EphFZs3F45F%2Bem1Ozl0fXIAgegDn0BKAaY5F4JRQPQA%3D; ZD_ENTRY=bing; PANPSC=3843437961712308433%3Au9Rut0jYI4qfFLw%2F5TJWE1cS2d9ns3O5C61tf8CKQkhoIDnjYJB5kw3MPJHnDhkCz81ttRoL0tAiVxZWCjKhbOJEKVZg82vZj7FJ7ADqJPsWXujC1eV6KOKEQjOY60ydECuWaePJJP%2B4A0ipQ2gQX0SbgxEKExKM0oUakcVUn8vvFIVZmIcELSHq5mg%2FcPBD1h8mCCD3Fkn75SjD4q9rtpR00d0Z6OohxASwYanDF8KxzJ2BeBROmwWMR6ewJUxvytJJL%2BMQEINTBmV4fV02TuU0aYK2SJHYLx2iyOOtLODyPJDZ5fFjQ7Xf7ylHQwl61C1ubP4y%2FN8Mc%2FxAohkhNA%3D%3D; csrfToken=6GJipGLUWpJ88u6IiL03XfYH; Hm_lvt_182d6d59474cf78db37e0b2248640ea5=1765087298,1765977114; HMACCOUNT=729A66B9AF8EBD50; ndut_fmt=FE31FDC675D66019B8D6FF97322125AD358CB961CE4545AF6F65A199A29DB000; ab_sr=1.0.1_NzIzZWM0YmNjNzEwZDFkMDEyYTgzYjdmYmVjYjU5MjcwMDhiZGI4YTAwNTYwODMxNzg4MTA5MDliZWI0ZjA0ZTJlODJlMDcwMTA1ZDBiMWI3NDM0ZDJkMWY1YmVhM2MwZjY3Y2E2ZDI1OTYyZTM1Nzk1NWZiZmQ2YTk2YTA3Y2NkNjc2N2Q3MDgzNTI2ZTdjNTEyY2VmYzQ4Yzc3NWU3Njc0ODM1MThmZTE1NzRmNmVmZmVhZDRmMWJjMjhjMGMx; Hm_lpvt_182d6d59474cf78db37e0b2248640ea5=1765977167"

INVALID_CHARS_REGEX = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9_\-\s]')


# ==========================================
# 第二部分：核心工具函数
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
    """生成一个可以复制指定文本的HTML按钮"""
    safe_text = json.dumps(text_to_copy)[1:-1]
    
    html = f"""
    <style>
    .copy-btn {{
        background-color: #f0f2f6;
        color: #31333F;
        padding: 0.5rem 1rem;
        border-radius: 0.5rem;
        border: 1px solid rgba(49, 51, 63, 0.2);
        font-family: "Source Sans Pro", sans-serif;
        font-size: 1rem;
        cursor: pointer;
        width: 100%;
        margin-top: 10px;
        transition: all 0.2s;
    }}
    .copy-btn:hover {{
        border-color: #ff4b4b;
        color: #ff4b4b;
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
            btn.innerText = "✅ 已复制到剪贴板！";
            btn.style.borderColor = "#09ab3b";
            btn.style.color = "#09ab3b";
            setTimeout(() => {{
                btn.innerText = originalText;
                btn.style.borderColor = "rgba(49, 51, 63, 0.2)";
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
    <button id="copyBtn" class="copy-btn" onclick="copyToClipboard()">📋 一键复制结果</button>
    """
    return html


# ==========================================
# 第三部分：网络请求类
# ==========================================

class Network:
    def __init__(self):
        self.s = requests.Session()
        self.s.trust_env = False
        self.headers = HEADERS.copy()
        self.headers['Cookie'] = "".join(FIXED_COOKIE.split())
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

    # --- 新增：删除文件方法 ---
    @retry(stop_max_attempt_number=3)
    def delete_file(self, path: str) -> int:
        """删除指定文件或文件夹"""
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
# 第四部分：Streamlit 业务流程
# ==========================================

def process_single_link(network, match, full_text, root_path):
    url = match.group(1)
    pwd_match = re.search(r'(?:\?pwd=|&pwd=|\s+|提取码[:：]?\s*)([a-zA-Z0-9]{4})', match.group(0))
    pwd = pwd_match.group(1) if pwd_match else ""
    clean_url = url.split('?')[0]

    folder_name = extract_folder_name(full_text, match.start())
    if not folder_name:
        folder_name = f"Resource_{int(time.time())}"
        st.write(f"⚠️ 无法提取有效名称，使用默认名: **{folder_name}**")
    else:
        st.write(f"📂 识别资源名: **{folder_name}**")

    if pwd:
        res = network.verify_pass_code(clean_url, pwd)
        if isinstance(res, int):
            st.error(f"❌ 链接验证失败 ({clean_url}) 错误代码: {res}")
            return None
        network.headers['Cookie'] = update_cookie(res, network.headers['Cookie'])

    content = network.get_transfer_params(clean_url)
    params = parse_response(content)
    if params == -1:
        st.error(f"❌ 链接解析失败 ({clean_url})")
        return None

    safe_suffix = generate_code()
    final_folder_name = f"{folder_name}_{safe_suffix}"
    full_save_path = f"{root_path}/{final_folder_name}"

    network.create_dir(root_path)
    create_res = network.create_dir(full_save_path)

    if create_res != 0 and create_res != -8:
        st.warning(f"⚠️ 目录创建失败 (代码: {create_res})，尝试安全名...")
        final_folder_name = f"Transfer_{int(time.time())}_{safe_suffix}"
        full_save_path = f"{root_path}/{final_folder_name}"
        create_res_retry = network.create_dir(full_save_path)
        if create_res_retry != 0 and create_res_retry != -8:
            st.error(f"❌ 目录创建失败，跳过。")
            return None

    # --- 转存并处理失败情况 ---
    transfer_res = network.transfer_file(params, full_save_path)
    if transfer_res != 0:
        st.error(f"❌ 转存文件失败 (代码: {transfer_res})，正在清理空文件夹...")
        # 失败时立即删除空文件夹
        del_res = network.delete_file(full_save_path)
        if del_res == 0:
            st.info(f"🧹 已自动删除无效目录: {final_folder_name}")
        else:
            st.warning(f"⚠️ 自动清理失败，请手动删除: {final_folder_name}")
        return None
    # -----------------------

    fs_id = network.get_dir_fsid(root_path, final_folder_name)
    if not fs_id:
        st.error("❌ 无法获取文件夹ID")
        return None

    new_pwd = generate_code()
    share_link = network.create_share(fs_id, new_pwd)

    if isinstance(share_link, int):
        st.error(f"❌ 分享失败 (代码: {share_link})")
        return None

    st.success(f"✅ 处理成功！")
    return f"{share_link}?pwd={new_pwd}"


def clear_text():
    st.session_state["user_input"] = ""


def main():
    st.set_page_config(page_title="转存助手", layout="wide")

    input_text = st.text_area(
        "📝 待处理文本",
        height=200,
        placeholder="在此粘贴包含链接的文本...",
        key="user_input"
    )

    col1, col2 = st.columns([1, 6])
    
    with col1:
        start_process = st.button("🚀 开始处理", type="primary", use_container_width=True)
    
    with col2:
        st.button("🗑️ 一键清除", on_click=clear_text)

    if start_process:
        if not input_text:
            st.warning("请先输入文本")
            st.stop()

        processed_text = clean_quark_links(input_text)
        network = Network()

        with st.status("正在自动化处理...", expanded=False) as status:
            token = network.get_bdstoken()
            if isinstance(token, int):
                status.update(label=f"❌ Cookie 无效 (代码: {token})", state="error")
                st.stop()
            network.bdstoken = token

            link_regex = re.compile(r'(https?://pan\.baidu\.com/s/[a-zA-Z0-9_\-]+(?:\?pwd=[a-zA-Z0-9]+)?)')
            matches = list(link_regex.finditer(processed_text))

            if not matches:
                status.update(label="⚠️ 未找到百度网盘链接", state="complete")
                st.stop()

            final_text = processed_text
            success_count = 0

            for match in reversed(matches):
                st.divider()
                new_link = process_single_link(network, match, processed_text, FIXED_SAVE_PATH)
                if new_link:
                    start, end = match.span()
                    final_text = final_text[:start] + new_link + final_text[end:]
                    success_count += 1

            if success_count > 0:
                status.update(label=f"✅ 完成！处理了 {success_count} 个链接", state="complete")
            else:
                status.update(label="⚠️ 完成，但无成功链接", state="error")

        if success_count > 0:
            st.subheader("🎉 处理结果")
            
            # 1. 使用文本框展示结果
            st.text_area("结果内容", value=final_text, height=300, label_visibility="collapsed")
            
            # 2. 嵌入JS脚本的HTML按钮
            components.html(create_copy_button_html(final_text), height=60)


if __name__ == '__main__':
    main()
